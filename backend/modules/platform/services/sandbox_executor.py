"""Sandbox Executor — Execute Python code in isolated Docker containers.

Provides secure code execution with:
- Container-level isolation (memory, CPU, network, filesystem)
- Code-level safety checks (AST analysis)
- Dynamic dependency installation
- Output capture (stdout, stderr, return value)
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from backend.modules.platform.services.code_validator import code_validator

logger = logging.getLogger(__name__)

# ── Default Configuration ──────────────────────────────────────────

DEFAULT_IMAGE = "sandbox-python:3.10"
DEFAULT_TIMEOUT = 60
DEFAULT_MEMORY = "512m"
DEFAULT_CPU = "1.0"
DEFAULT_WORK_DIR = "/tmp/sandbox-work"

# python_runtime：本地会话沙箱（SessionToolSandbox）唯一使用的运行时镜像。
# 运行期容器 --network=none / --read-only，无法 pip 安装，基础包在镜像构建期
# 固化（见 docker/agent-sandbox/requirements.txt 与 docker/agent-sandbox/build.sh）。
# 可用 ADH_AGENT_SANDBOX_IMAGE 覆盖，但默认必须指向预装基础包的 python_runtime。
PYTHON_RUNTIME_IMAGE = "adh-python-runtime:1"


def _python_runtime_image() -> str:
    return os.environ.get("ADH_AGENT_SANDBOX_IMAGE", PYTHON_RUNTIME_IMAGE)


# ── 会话工具沙箱后端（docker 默认/生产；bwrap 供本地开发）─────────────
# 后端是"部署级"选择；租户隔离（只挂本会话目录、断网、非 root、可核验停止）
# 由 command 构造保证，切换后端不改变多租户权限边界。
_DEFAULT_SANDBOX_CACHE = {"row": None, "expires": 0.0}


def _default_sandbox_row():
    """读取"系统管理-沙箱管理"里设为默认的沙箱行（30s TTL）；读取失败按无默认处理并记日志
    （docker 是无显式配置时的安全默认后端，非把 bwrap 失败掩成 docker）。"""
    now = time.time()
    if _DEFAULT_SANDBOX_CACHE["expires"] > now:
        return _DEFAULT_SANDBOX_CACHE["row"]
    row = None
    try:
        from backend.modules.platform.services.sandbox_service import sandbox_service
        row = sandbox_service.get_default_sandbox()
    except Exception:
        logger.warning("读取默认沙箱配置失败，按 Docker 会话沙箱处理", exc_info=True)
    _DEFAULT_SANDBOX_CACHE["row"] = row
    _DEFAULT_SANDBOX_CACHE["expires"] = now + 30.0
    return row


def resolve_backend() -> str:
    """会话工具沙箱后端：ADH_SANDBOX_BACKEND 显式覆盖 → 默认沙箱类型 → docker。"""
    override = os.environ.get("ADH_SANDBOX_BACKEND", "").strip().lower()
    if override in ("docker", "bwrap"):
        return override
    row = _default_sandbox_row()
    if row and row.get("sandbox_type") == "bwrap" and row.get("is_active", True):
        return "bwrap"
    return "docker"


def _bwrap_config() -> dict:
    """bwrap 后端参数：优先取默认 bwrap 沙箱 config，缺失回落 env 默认（无 rootfs 即报错）。"""
    row = _default_sandbox_row()
    cfg = dict(row.get("config") or {}) if (row or {}).get("sandbox_type") == "bwrap" else {}
    cfg.setdefault("mode", os.environ.get("ADH_SANDBOX_BWRAP_MODE", "rootfs").strip().lower())
    cfg.setdefault("rootfs_dir", os.environ.get("ADH_SANDBOX_BWRAP_ROOTFS", ""))
    cfg.setdefault("memory_limit", os.environ.get("ADH_SANDBOX_BWRAP_MEMORY", ""))
    cfg.setdefault("cpu_limit", os.environ.get("ADH_SANDBOX_BWRAP_CPU", "1.0"))
    cfg.setdefault("timeout", int(os.environ.get("ADH_SANDBOX_BWRAP_TIMEOUT", "120")))
    return cfg


def _to_mb(value, default_mb: int = 1024) -> int:
    """把 '512m'/'1g'/'2048' 解析为 MB；无法解析用 default_mb（不做"解析失败=不限制"的静默）。"""
    s = str(value or "").strip().lower()
    if not s:
        return default_mb
    mult = {"k": 1 / 1024, "m": 1, "g": 1024}.get(s[-1])
    try:
        if mult is not None:
            return max(1, int(float(s[:-1]) * mult))
        return max(1, int(float(s)))
    except (TypeError, ValueError):
        return default_mb


def _is_writable_tool(runtime, name: str) -> bool:
    return name in ("write", "edit") or (
        name == "bash" and bool(set(runtime.policy.standard) & {"write", "edit"}))


def bwrap_host_python_root_args() -> list:
    """host-python 模式的根绑定（空根 + 白名单）；探针与真实命令共用，避免两者漂移。"""
    return ["--tmpfs", "/",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"]


def bwrap_probe_argv(cfg: dict) -> list:
    """就绪/连接测试探针：与 bwrap_command 用同一套 root 绑定（含 /bin 等符号链接），
    否则 host-python 下 /bin/true 不存在会被误报为“无法建立隔离沙箱”。"""
    mode = (cfg.get("mode") or "rootfs").strip().lower()
    root = bwrap_host_python_root_args() if mode == "host-python" \
        else ["--ro-bind", (cfg.get("rootfs_dir") or "").strip(), "/"]
    return ["bwrap",
            "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--die-with-parent", "--new-session",
            *root, "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "/bin/true"]


# Wrapper script that captures stdout/stderr and return value
EXECUTION_WRAPPER = '''\
import sys
import io
import json
import traceback

# Redirect stdout/stderr
_stdout = io.StringIO()
_stderr = io.StringIO()
sys.stdout = _stdout
sys.stderr = _stderr

_result = None
_error = None

try:
    # Execute user code
{indented_code}

    # Try to get the last expression as result
    _result = None
except Exception as e:
    _error = traceback.format_exc()

# Restore stdout/stderr
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

# Output result as JSON
output = {{
    "stdout": _stdout.getvalue(),
    "stderr": _stderr.getvalue(),
    "result": repr(_result) if _result is not None else None,
    "error": _error,
}}
print("___SANDBOX_RESULT___")
print(json.dumps(output, ensure_ascii=False))
'''


class SessionToolSandbox:
    """会话工具专用沙箱；后端可插拔（docker 默认 / bwrap 本地开发）。

    模型侧不接受任何镜像、挂载、环境或 Docker/bwrap 参数——命令一律由服务端按会话
    构造；租户隔离（只挂本会话目录、断网、非 root、可核验停止）与后端无关。
    """

    @staticmethod
    def ensure_available():
        if resolve_backend() == "bwrap":
            SessionToolSandbox._ensure_bwrap()
            return
        image = _python_runtime_image()
        try:
            subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                           capture_output=True, timeout=15, check=True)
        except Exception as exc:
            raise ValueError(
                f"会话工具沙箱未就绪，请执行 docker/agent-sandbox/build.sh 构建 python_runtime 镜像（{image}）"
            ) from exc

    @staticmethod
    def command(runtime, name):
        import uuid
        image = _python_runtime_image()
        container = f"adh-agent-{runtime.key}-{uuid.uuid4().hex}"
        writable = _is_writable_tool(runtime, name)
        mount = f"type=bind,src={runtime.workspace},dst=/workspace" + ("" if writable else ",readonly")
        uid = os.getuid() if os.getuid() else 65534
        gid = os.getgid() if os.getuid() else 65534
        return container, [
            "docker", "run", "--name", container, "--pull=never", "--rm", "-i",
            "--label", f"adh.agent.session={runtime.key}",
            "--label", f"adh.agent.execution={runtime.token}",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--memory-swap=256m", "--cpus=1",
            "--user", f"{uid}:{gid}", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
            "--mount", mount, "--workdir", "/workspace", image,
        ]

    @staticmethod
    async def run(runtime, name, args):
        # 后端可插拔：docker（默认/生产）或 bwrap（本地开发）；两者均 fail-closed，
        # 后端不可用即显式报错，绝不回退到宿主裸跑（沙箱安全隔离硬性规范）。
        if resolve_backend() == "bwrap":
            return await SessionToolSandbox._run_bwrap(runtime, name, args)
        return await SessionToolSandbox._run_docker(runtime, name, args)

    @staticmethod
    async def _run_docker(runtime, name, args):
        import asyncio
        runtime.verify()
        container, command = SessionToolSandbox.command(runtime, name)
        payload = json.dumps({"name": name, "args": args}, ensure_ascii=False).encode()
        if len(payload) > 500_000:
            raise ValueError("工具参数超过大小限制")
        import anyio
        runtime.sandbox_used = True
        runtime.sandbox_backend = "docker"
        async def launch():
            from functools import partial
            from backend.modules.mind.execution.session_workspace import run_owned_sync
            create_command = list(command)
            create_command[1] = "create"
            created = await run_owned_sync(partial(subprocess.run, create_command,
                capture_output=True, text=True, timeout=30))
            if created.returncode:
                logger.error("会话沙箱创建失败: %s", created.stderr[:2000])
                raise RuntimeError("会话沙箱不可用，请检查 Docker 和会话持久目录")
            return await asyncio.create_subprocess_exec(
                "docker", "start", "-ai", container, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        creation = asyncio.create_task(launch())
        process = None
        try:
            process = await asyncio.shield(creation)
            stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=130)
            if len(stdout) > 300_000:
                raise RuntimeError("沙箱输出超过限制")
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeDecodeError) as exc:
                logger.error("会话沙箱启动/协议失败: %s", stderr.decode(errors="replace")[:2000])
                raise RuntimeError("会话沙箱不可用，请检查 Docker 和工具镜像") from exc
            if result.get("error"):
                raise ValueError(result["error"])
            if process.returncode:
                raise RuntimeError("会话沙箱执行失败")
            return result["result"]
        finally:
            # 取消 Docker 客户端不会自动终止容器，必须确认容器清理再返回。
            with anyio.CancelScope(shield=True):
                try:
                    try:
                        process = process or await asyncio.shield(creation)
                    finally:
                        await SessionToolSandbox.stop(container)
                except BaseException:
                    runtime.unsafe = True
                    raise
                finally:
                    if process is not None and process.returncode is None:
                        process.kill()
                        await process.wait()

    @staticmethod
    async def stop(container):
        import asyncio
        process = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode and b"No such container" not in stderr:
            logger.error("无法确认沙箱已停止: %s", stderr.decode(errors="replace")[:1000])
            raise RuntimeError("无法确认沙箱停止，会话必须保持阻断")

    # ── bwrap 后端（bubblewrap 用户命名空间；本地开发等价隔离）──────────
    @staticmethod
    def bwrap_command(runtime, name, cfg=None):
        """构造 bwrap argv：只挂本会话目录（非写只读）、断网、非 root、无宿主泄漏。

    两种镜像来源（mode）：
      - rootfs（默认）：--ro-bind {rootfs} /，rootfs 由 export-rootfs.sh 从 python_runtime 镜像导出，
        与生产逐字节一致（可复现环境），worker 入口内置于 rootfs。
      - host-python（本地开发免 docker）：--tmpfs / 先置空根，再只绑白名单 /usr /etc（+重建 /lib /bin 符号链接），
        绝不 --ro-bind / /——/home（含 backend/.env）、/root、/var 均不可达；worker 直接绑仓库当前文件。

    两模式共用：隔离 net/pid/ipc/uts/cgroup（断网 + pid 隔离）、workspace 仅写权限时 --bind；
    不启用 --unshare-user（root 应用下会话目录 chown 到 65534，userns 会把 root 映射为内层 uid0 而访问不到），
    改由 bwrap 以 root 挂载/chdir，worker 启动后降权到 65534（对齐 docker --user 65534）。内存/CPU 帽由 worker 施加 rlimit。
    """
        cfg = cfg or _bwrap_config()
        mode = (cfg.get("mode") or "rootfs").strip().lower()
        timeout = int(cfg.get("timeout", 120))
        bind = "--bind" if _is_writable_tool(runtime, name) else "--ro-bind"
        envs = ["--setenv", "HOME", "/tmp",
                "--setenv", "MPLCONFIGDIR", "/tmp/matplotlib",
                "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                "--setenv", "ADH_RLIMIT_CPU_S", str(timeout + 15)]
        mem_raw = str(cfg.get("memory_limit") or "").strip()
        if mem_raw:
            envs += ["--setenv", "ADH_RLIMIT_AS_MB", str(_to_mb(mem_raw))]
        if os.geteuid() == 0:
            # root 应用：worker 降权到 65534（对齐 session_paths 的 chown 与 docker --user 65534）。
            envs += ["--setenv", "ADH_DROP_UID", "65534", "--setenv", "ADH_DROP_GID", "65534"]
        if mode == "host-python":
            from backend.modules.mind.execution import sandbox_worker
            worker = str(Path(sandbox_worker.__file__).resolve())
            # 探针不绑 worker（只跑 /bin/true）；真实命令额外绑当前仓库 worker。
            root_args = bwrap_host_python_root_args() + [
                "--dir", "/opt", "--dir", "/opt/agent",
                "--ro-bind", worker, "/opt/agent/sandbox_worker.py"]
            python = "/usr/bin/python3"
        else:
            rootfs = (cfg.get("rootfs_dir") or "").strip()
            if not rootfs:
                raise ValueError("bwrap rootfs 模式缺少 rootfs_dir：请配置或改用 mode=host-python")
            root_args = ["--ro-bind", rootfs, "/"]
            python = "python"
        return [
            "bwrap",
            "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--die-with-parent", "--new-session",
            *root_args,
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            bind, str(runtime.workspace), "/workspace",
            *envs,
            "--chdir", "/workspace",
            python, "-I", "/opt/agent/sandbox_worker.py",
        ]

    @staticmethod
    def _ensure_bwrap():
        try:
            subprocess.run(["bwrap", "--version"], capture_output=True, timeout=10, check=True)
        except Exception as exc:
            raise ValueError("会话工具沙箱(bwrap)未就绪：请安装 bubblewrap（apt install bubblewrap）") from exc
        cfg = _bwrap_config()
        mode = (cfg.get("mode") or "rootfs").strip().lower()
        if mode == "host-python":
            if not Path("/usr/bin/python3").exists():
                raise ValueError("bwrap host-python 模式需要 /usr/bin/python3（未找到）；请装 python3 或改用 rootfs/Docker 后端")
        elif mode == "rootfs":
            rootfs = (cfg.get("rootfs_dir") or "").strip()
            if not rootfs or not Path(rootfs).is_dir():
                raise ValueError(
                    f"bwrap 会话沙箱 rootfs 缺失({rootfs or '未配置'})：请执行 docker/agent-sandbox/export-rootfs.sh "
                    "或在系统管理-沙箱管理配置 rootfs_dir（或改用 mode=host-python）")
            if not Path(rootfs, "opt/agent/sandbox_worker.py").is_file():
                raise ValueError(
                    f"bwrap rootfs 缺 worker 入口({rootfs}/opt/agent/sandbox_worker.py)：请重跑 export-rootfs.sh（它会把当前 sandbox_worker.py 烘入）")
        else:
            raise ValueError(f"未知 bwrap mode: {mode}（应为 rootfs 或 host-python）")
        # 探针与真实 bwrap_command 用同一套 root 绑定（含 /bin 符号链接），携带 stderr 以便诊断。
        proc = subprocess.run(bwrap_probe_argv(cfg), capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            raise ValueError(
                "bwrap 无法建立隔离沙箱：" + (proc.stderr.strip()[:300] or "未知错误") +
                "（检查 bubblewrap/挂载权限；非 root 部署可能需 user namespace 或改用 Docker 后端）")

    @staticmethod
    async def _run_bwrap(runtime, name, args):
        import asyncio
        runtime.verify()
        cfg = _bwrap_config()
        command = SessionToolSandbox.bwrap_command(runtime, name, cfg)
        payload = json.dumps({"name": name, "args": args}, ensure_ascii=False).encode()
        if len(payload) > 500_000:
            raise ValueError("工具参数超过大小限制")
        import anyio
        runtime.sandbox_used = True
        runtime.sandbox_backend = "bwrap"
        timeout = int(cfg.get("timeout", 120))
        process = None
        try:
            # start_new_session=True → bwrap 为本进程直接会话首领，killpg 可确认整树退出
            # （--unshare-pid + --die-with-parent 防孤儿）；后端不可用/协议错均显式抛出。
            process = await asyncio.create_subprocess_exec(
                *command, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            runtime.sandbox_pids.add(process.pid)
            stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=timeout + 10)
            if len(stdout) > 300_000:
                raise RuntimeError("沙箱输出超过限制")
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeDecodeError) as exc:
                logger.error("bwrap 启动/协议失败: %s", stderr.decode(errors="replace")[:2000])
                raise RuntimeError("会话沙箱(bwrap)不可用，请检查 rootfs 镜像与 python 入口") from exc
            if result.get("error"):
                raise ValueError(result["error"])
            if process.returncode:
                raise RuntimeError("会话沙箱执行失败")
            return result["result"]
        finally:
            # 未确认停止前不返回；存活 pid 留给 confirm_stopped 阻断执行权释放（对齐 docker stop 语义）。
            with anyio.CancelScope(shield=True):
                if process is not None and process.pid is not None:
                    if process.returncode is None:
                        try:
                            import signal
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=15)
                    except BaseException:
                        if process.returncode is None:
                            runtime.unsafe = True
                            raise RuntimeError("无法确认沙箱(bwrap)停止，会话必须保持阻断")
                    runtime.sandbox_pids.discard(process.pid)


class SandboxExecutor:
    """Execute Python code in isolated Docker containers."""

    def __init__(self, sandbox_config: dict):
        """Initialize with sandbox configuration.

        Args:
            sandbox_config: Sandbox environment config dict from DB.
        """
        self.config = sandbox_config
        self.sandbox_type = sandbox_config.get("sandbox_type", "local")
        self.docker_config = sandbox_config.get("config", {})

    def execute(
        self,
        code: str,
        requirements: list = None,
        timeout: int = None,
        image: str = None,
    ) -> dict:
        """Execute Python code in the sandbox.

        Args:
            code: Python source code to execute.
            requirements: List of pip packages to install before execution.
            timeout: Execution timeout in seconds (overrides sandbox config).
            image: Docker image to use (overrides default).

        Returns:
            {
                "success": bool,
                "stdout": str,
                "stderr": str,
                "result": str | None,
                "error": str | None,
                "elapsed_ms": int,
                "timeout": bool,
            }
        """
        start_time = time.time()

        # 1. Validate code safety
        is_safe, reason = code_validator.validate(code)
        if not is_safe:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "result": None,
                "error": f"代码安全检查未通过: {reason}",
                "elapsed_ms": int((time.time() - start_time) * 1000),
                "timeout": False,
            }

        # 2. Resolve execution parameters
        timeout = timeout or self.docker_config.get("timeout", DEFAULT_TIMEOUT)
        memory = self.docker_config.get("memory_limit", DEFAULT_MEMORY)
        cpu = self.docker_config.get("cpu_limit", DEFAULT_CPU)
        image = image or DEFAULT_IMAGE

        # 3. Build execution script
        exec_script = self._build_execution_script(code, requirements)

        # 4. Execute based on sandbox type
        if self.sandbox_type == "local":
            result = self._execute_local(exec_script, image, memory, cpu, timeout)
        elif self.sandbox_type == "ssh":
            result = self._execute_ssh(exec_script, image, memory, cpu, timeout)
        else:
            result = {
                "success": False,
                "stdout": "",
                "stderr": "",
                "result": None,
                "error": f"不支持的沙箱类型: {self.sandbox_type}",
                "timeout": False,
            }

        result["elapsed_ms"] = int((time.time() - start_time) * 1000)

        # 5. Log execution (async, don't block)
        try:
            from backend.modules.platform.services.sandbox_service import sandbox_service
            sandbox_service.log_execution(
                sandbox_id=self.config.get("id", 0),
                sandbox_name=self.config.get("name", "unknown"),
                sandbox_type=self.sandbox_type,
                code=code,
                requirements=requirements,
                result=result,
            )
        except Exception as e:
            logger.warning(f"Failed to log sandbox execution: {e}")

        return result

    def _build_execution_script(self, code: str, requirements: list = None) -> str:
        """Build the full execution script with dependency install and wrapper."""
        lines = []

        # Install dependencies if specified
        if requirements:
            lines.append("import subprocess, sys")
            req_str = " ".join(requirements)
            lines.append(f"subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', '{req_str}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")

        # Indent user code for the try block
        indented = "\n".join("    " + line for line in code.split("\n"))
        wrapper = EXECUTION_WRAPPER.format(indented_code=indented)
        lines.append(wrapper)

        return "\n".join(lines)

    def _execute_local(self, script: str, image: str, memory: str, cpu: str, timeout: int) -> dict:
        """Execute code in a local Docker container via stdin."""
        try:
            # Build docker run command — use stdin to pass script
            cmd = [
                "docker", "run", "--rm", "-i",
                f"--memory={memory}",
                f"--cpus={cpu}",
                "--network=sandbox-net",
                "--read-only",
                "--tmpfs", "/tmp:size=50m",
                "--pids-limit=100",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-w", "/tmp",
                image,
                "python", "-",
            ]

            # Execute with timeout — pass script via stdin
            result = subprocess.run(
                cmd,
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout + 5,  # Add buffer for container startup
            )

            return self._parse_output(result.stdout, result.stderr, result.returncode, timeout)

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "result": None,
                "error": f"执行超时 ({timeout}秒)",
                "timeout": True,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "result": None,
                "error": "未找到 docker 命令，请确认 Docker 已安装",
                "timeout": False,
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "result": None,
                "error": f"执行失败: {str(e)}",
                "timeout": False,
            }

    def _execute_ssh(self, script: str, image: str, memory: str, cpu: str, timeout: int) -> dict:
        """Execute code in a Docker container on a remote SSH server via stdin."""
        host = self.docker_config.get("host", "")
        port = self.docker_config.get("port", 22)
        user = self.docker_config.get("user", "root")
        auth_type = self.docker_config.get("auth_type", "key")
        key_file = self.docker_config.get("key_file", "")

        if not host:
            return {"success": False, "stdout": "", "stderr": "", "result": None, "error": "未配置主机地址", "timeout": False}

        # Build SSH command base
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-p", str(port)]
        if auth_type == "key" and key_file:
            ssh_cmd.extend(["-i", key_file])
        ssh_cmd.append(f"{user}@{host}")

        try:
            # Execute in Docker container via stdin — no file upload needed
            docker_cmd = ssh_cmd + [
                "docker", "run", "--rm", "-i",
                f"--memory={memory}",
                f"--cpus={cpu}",
                "--network=sandbox-net",
                "--read-only",
                "--tmpfs", "/tmp:size=50m",
                "--pids-limit=100",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-w", "/tmp",
                image,
                "python", "-",
            ]

            result = subprocess.run(
                docker_cmd,
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )

            return self._parse_output(result.stdout, result.stderr, result.returncode, timeout)

        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "", "result": None,
                    "error": f"执行超时 ({timeout}秒)", "timeout": True}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": "", "result": None,
                    "error": f"SSH 执行失败: {str(e)}", "timeout": False}

    def _parse_output(self, stdout: str, stderr: str, returncode: int, timeout: int) -> dict:
        """Parse container output and extract structured result."""
        # Look for the result marker
        marker = "___SANDBOX_RESULT___"
        if marker in stdout:
            parts = stdout.split(marker, 1)
            pre_output = parts[0].strip()
            try:
                result_json = json.loads(parts[1].strip())
                return {
                    "success": returncode == 0 and result_json.get("error") is None,
                    "stdout": result_json.get("stdout", pre_output),
                    "stderr": result_json.get("stderr", stderr),
                    "result": result_json.get("result"),
                    "error": result_json.get("error"),
                    "timeout": False,
                }
            except (json.JSONDecodeError, IndexError):
                pass

        # Fallback: raw output
        if returncode == -9 or "Killed" in stderr:
            return {
                "success": False,
                "stdout": stdout,
                "stderr": stderr,
                "result": None,
                "error": f"进程被杀死（可能内存超限: {self.docker_config.get('memory_limit', DEFAULT_MEMORY)}）",
                "timeout": False,
            }

        return {
            "success": returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "result": None,
            "error": stderr if returncode != 0 else None,
            "timeout": False,
        }
