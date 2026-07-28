"""Docker Executor — 本地或 SSH 远程执行 Docker 命令。

复用沙箱 SSH 配置，在宿主机或远程机器上构建/运行 MCP 服务 Docker 镜像。
后端容器内无法直接访问 Docker daemon，通过 SSH 隧道解决。
"""

import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 用于流式输出的回调类型
LogCallback = Callable[[str], None]


def _noop_callback(line: str):
    pass


class DockerExecutor:
    """通过本地或 SSH 远程执行 Docker 命令。"""

    def __init__(self, ssh_config: dict = None):
        """
        Args:
            ssh_config: SSH 配置 dict，包含 host/port/user/auth_type/key_file。
                        为 None 时使用本地 Docker。
        """
        self.ssh_config = ssh_config or {}
        self.is_remote = bool(self.ssh_config.get("host"))

    def _build_ssh_cmd(self) -> list[str]:
        """构建 SSH 基础命令。"""
        cfg = self.ssh_config
        host = cfg.get("host", "")
        port = cfg.get("port", 22)
        user = cfg.get("user", "root")
        auth_type = cfg.get("auth_type", "key")
        key_file = cfg.get("key_file", "")

        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30",
            "-p", str(port),
        ]
        if auth_type == "key" and key_file:
            cmd.extend(["-i", os.path.expanduser(key_file)])
        cmd.append(f"{user}@{host}")
        return cmd

    def _run_local(self, cmd: list[str], callback: LogCallback = _noop_callback,
                   timeout: int = 600) -> tuple[bool, str]:
        """本地执行命令，流式输出。"""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            last_line = ""
            for line in proc.stdout:
                line = line.rstrip("\n")
                last_line = line
                callback(line)
            proc.wait(timeout=timeout)
            ok = proc.returncode == 0
            if not ok and last_line:
                logger.error("[DockerExecutor] Local cmd failed: %s", last_line)
            return ok, last_line
        except subprocess.TimeoutExpired:
            proc.kill()
            callback("ERROR: 命令超时")
            return False, "命令超时"
        except Exception as e:
            callback(f"ERROR: {e}")
            return False, str(e)

    def _run_ssh(self, remote_cmd: str, callback: LogCallback = _noop_callback,
                 timeout: int = 600) -> tuple[bool, str]:
        """通过 SSH 执行远程命令，流式输出。"""
        ssh_cmd = self._build_ssh_cmd()
        ssh_cmd.append(remote_cmd)

        try:
            proc = subprocess.Popen(
                ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            last_line = ""
            for line in proc.stdout:
                line = line.rstrip("\n")
                last_line = line
                callback(line)
            proc.wait(timeout=timeout)
            ok = proc.returncode == 0
            if not ok and last_line:
                logger.error("[DockerExecutor] SSH cmd failed: %s", last_line)
            return ok, last_line
        except subprocess.TimeoutExpired:
            proc.kill()
            callback("ERROR: SSH 命令超时")
            return False, "SSH 命令超时"
        except Exception as e:
            callback(f"ERROR: SSH {e}")
            return False, str(e)

    def _run(self, cmd_or_remote: str, callback: LogCallback = _noop_callback,
             timeout: int = 600, is_local_cmd: bool = False) -> tuple[bool, str]:
        """统一执行入口。"""
        if self.is_remote:
            return self._run_ssh(cmd_or_remote, callback, timeout)
        else:
            if is_local_cmd:
                # cmd_or_remote 是本地命令列表
                return self._run_local(cmd_or_remote, callback, timeout)
            else:
                # cmd_or_remote 是 shell 字符串
                return self._run_local(["bash", "-c", cmd_or_remote], callback, timeout)

    # ── 公开接口 ─────────────────────────────────────────────────

    def detect_mode(self) -> dict:
        """检测当前 Docker 执行环境。"""
        if self.is_remote:
            # 测试 SSH + 远程 Docker
            ok, out = self._run("docker info --format '{{.ServerVersion}}'", timeout=15)
            if ok:
                return {"mode": "ssh", "host": self.ssh_config["host"],
                        "docker_version": out.strip(), "available": True}
            return {"mode": "ssh", "host": self.ssh_config["host"],
                    "available": False, "error": out}
        else:
            # 测试本地 Docker
            try:
                r = subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    return {"mode": "local", "docker_version": r.stdout.strip(),
                            "available": True}
            except Exception:
                pass
            # 检查是否在容器中
            in_container = os.path.exists("/.dockerenv")
            return {"mode": "local", "available": False,
                    "in_container": in_container,
                    "error": "Docker 不可用" + ("（在容器中运行）" if in_container else "")}

    def image_exists(self, image_name: str) -> bool:
        """检查镜像是否存在。"""
        cmd = f"docker image inspect {image_name} > /dev/null 2>&1 && echo yes || echo no"
        ok, out = self._run(cmd, timeout=15)
        return ok and "yes" in out

    def build_image(self, image_name: str, dockerfile_content: str,
                    callback: LogCallback = _noop_callback,
                    timeout: int = 600) -> tuple[bool, str]:
        """构建 Docker 镜像。

        Args:
            image_name: 镜像名 (如 adh-mcp/xxx:latest)
            dockerfile_content: Dockerfile 内容
            callback: 每行输出的回调
            timeout: 超时秒数

        Returns:
            (success, message)
        """
        # 转义 Dockerfile 内容用于 shell heredoc
        escaped_df = dockerfile_content.replace("'", "'\\''")

        if self.is_remote:
            host = self.ssh_config["host"]
            build_dir = f"/tmp/adh-mcp-build-{int(time.time())}"
            cmd = (
                f"mkdir -p {build_dir} && "
                f"cat > {build_dir}/Dockerfile <<'___DOCKERFILE_EOF___\n"
                f"{dockerfile_content}\n"
                f"___DOCKERFILE_EOF___\n"
                f"docker build -t {image_name} -f {build_dir}/Dockerfile {build_dir} && "
                f"rm -rf {build_dir}"
            )
            callback(f"[SSH → {host}] 开始构建镜像 {image_name} ...")
            ok, out = self._run(cmd, callback, timeout)
        else:
            # 本地构建
            with tempfile.TemporaryDirectory() as tmpdir:
                df_path = os.path.join(tmpdir, "Dockerfile")
                with open(df_path, "w") as f:
                    f.write(dockerfile_content)

                callback(f"[本地] 开始构建镜像 {image_name} ...")
                cmd = ["docker", "build", "-t", image_name, "-f", df_path, tmpdir]
                ok, out = self._run(cmd, callback, timeout, is_local_cmd=True)

        if ok:
            callback(f"✅ 镜像 {image_name} 构建成功")
        else:
            callback(f"❌ 镜像构建失败: {out}")

        return ok, out

    def run_container_sync(self, image_name: str, env_vars: dict = None,
                           command: str = None, timeout: int = 30) -> tuple[bool, str]:
        """同步运行容器（用于测试连接），捕获输出。"""
        docker_args = ["docker", "run", "--rm"]
        if env_vars:
            for k, v in env_vars.items():
                docker_args.extend(["-e", f"{k}={v}"])
        docker_args.append(image_name)
        if command:
            docker_args.extend(command.split())

        cmd_str = " ".join(f"'{a}'" if " " in a else a for a in docker_args)

        if self.is_remote:
            return self._run(cmd_str, timeout=timeout)
        else:
            return self._run_local(docker_args, timeout=timeout)


# ── 单例管理 ─────────────────────────────────────────────────

_executor: Optional[DockerExecutor] = None


def get_docker_executor(ssh_config: dict = None) -> DockerExecutor:
    """获取 Docker 执行器（可传入 SSH 配置覆盖默认）。"""
    global _executor
    if ssh_config is not None:
        return DockerExecutor(ssh_config)
    if _executor is None:
        _executor = DockerExecutor(_load_default_ssh_config())
    return _executor


def _load_default_ssh_config() -> dict:
    """从默认沙箱配置加载 SSH 设置。"""
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config FROM adh_sandbox_environments "
                    "WHERE sandbox_type = 'ssh' AND is_default = 1 AND is_active = 1 "
                    "LIMIT 1"
                )
                row = cur.fetchone()
                if row and row.get("config"):
                    import json
                    cfg = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
                    logger.info("[DockerExecutor] Loaded SSH config from default sandbox: %s@%s",
                                cfg.get("user"), cfg.get("host"))
                    return cfg
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[DockerExecutor] No default sandbox SSH config: %s", e)
    return {}


def reset_executor():
    """重置执行器单例（用于测试或配置变更后）。"""
    global _executor
    _executor = None
