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
    """会话工具专用沙箱；不会接受模型提供的镜像、挂载、环境或 Docker 参数。"""

    @staticmethod
    def ensure_available():
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
        writable = name in ("write", "edit") or (name == "bash" and bool(set(runtime.policy.standard) & {"write", "edit"}))
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
        import asyncio
        runtime.verify()
        container, command = SessionToolSandbox.command(runtime, name)
        payload = json.dumps({"name": name, "args": args}, ensure_ascii=False).encode()
        if len(payload) > 500_000:
            raise ValueError("工具参数超过大小限制")
        import anyio
        runtime.sandbox_used = True
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
