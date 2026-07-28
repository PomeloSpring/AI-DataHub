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

from backend.services.code_validator import code_validator

logger = logging.getLogger(__name__)

# ── Default Configuration ──────────────────────────────────────────

DEFAULT_IMAGE = "sandbox-python:3.10"
DEFAULT_TIMEOUT = 60
DEFAULT_MEMORY = "512m"
DEFAULT_CPU = "1.0"
DEFAULT_WORK_DIR = "/tmp/sandbox-work"

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
            from backend.services.sandbox_service import sandbox_service
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
