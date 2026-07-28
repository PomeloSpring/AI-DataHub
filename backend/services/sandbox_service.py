"""Sandbox Service — CRUD for sandbox environments.

Manages sandbox backends (local/ssh/fc) for isolated code execution.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id():
    return int(_time.time() * 1000000)


# ── Sandbox Type Definitions ──────────────────────────────────────

SANDBOX_TYPES = {
    "local": {
        "label": "本地沙箱",
        "description": "本机 Docker 容器隔离执行",
        "config_schema": {
            "docker_socket": {"type": "string", "label": "Docker Socket", "default": "unix:///var/run/docker.sock", "required": True},
            "network": {"type": "string", "label": "Docker 网络", "default": "sandbox-net"},
            "memory_limit": {"type": "string", "label": "内存限制", "default": "2g", "placeholder": "如 2g, 512m"},
            "cpu_limit": {"type": "string", "label": "CPU 限制", "default": "2.0", "placeholder": "如 2.0"},
            "timeout": {"type": "number", "label": "超时时间(秒)", "default": 300},
            "auto_remove": {"type": "boolean", "label": "执行完自动删除容器", "default": True},
            "temp_dir": {"type": "string", "label": "临时工作目录", "default": "/tmp/sandbox-work"},
        },
    },
    "ssh": {
        "label": "SSH 远程沙箱",
        "description": "SSH 连接远程服务器，Docker 容器隔离执行",
        "config_schema": {
            "host": {"type": "string", "label": "主机地址", "required": True, "placeholder": "192.168.1.100"},
            "port": {"type": "number", "label": "SSH 端口", "default": 22},
            "user": {"type": "string", "label": "用户名", "required": True, "placeholder": "sandbox"},
            "auth_type": {"type": "select", "label": "认证方式", "options": ["key", "password"], "default": "key"},
            "key_file": {"type": "string", "label": "密钥文件路径", "default": "~/.ssh/id_rsa", "placeholder": "~/.ssh/sandbox_key", "show_if": {"auth_type": "key"}},
            "password": {"type": "password", "label": "密码", "show_if": {"auth_type": "password"}},
            "docker_socket": {"type": "string", "label": "远程 Docker Socket", "default": "/var/run/docker.sock"},
            "network": {"type": "string", "label": "Docker 网络", "default": "sandbox-net"},
            "memory_limit": {"type": "string", "label": "内存限制", "default": "8g", "placeholder": "如 8g"},
            "cpu_limit": {"type": "string", "label": "CPU 限制", "default": "4.0", "placeholder": "如 4.0"},
            "gpus": {"type": "number", "label": "GPU 数量", "default": 0, "placeholder": "0 表示无 GPU"},
            "timeout": {"type": "number", "label": "超时时间(秒)", "default": 600},
            "auto_remove": {"type": "boolean", "label": "执行完自动删除容器", "default": True},
            "temp_dir": {"type": "string", "label": "远程临时目录", "default": "/tmp/sandbox-work"},
        },
    },
    "fc": {
        "label": "阿里云函数计算",
        "description": "Aliyun FC Serverless 执行",
        "config_schema": {
            "region": {"type": "string", "label": "地域", "required": True, "default": "cn-hangzhou", "placeholder": "cn-hangzhou"},
            "access_key_id": {"type": "string", "label": "AccessKey ID", "required": True},
            "access_key_secret": {"type": "password", "label": "AccessKey Secret", "required": True},
            "service": {"type": "string", "label": "FC 服务名", "default": "sandbox-hub"},
        },
    },
}


class SandboxService:
    """Service for managing sandbox environments."""

    # ── List / Get ────────────────────────────────────────────────

    def list_sandboxes(self, page: int = 1, size: int = 50,
                       sandbox_type: str = "", search: str = "") -> dict:
        """List sandbox environments with pagination."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                conditions = []
                params = []
                if sandbox_type:
                    conditions.append("sandbox_type = %s")
                    params.append(sandbox_type)
                if search:
                    conditions.append("(name LIKE %s OR display_name LIKE %s)")
                    params.extend([f"%{search}%", f"%{search}%"])
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                cur.execute(f"SELECT COUNT(*) AS total FROM adh_sandbox_environments {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_sandbox_environments {where} "
                    f"ORDER BY is_default DESC, created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_row(r)
                return {"items": rows, "total": total}
        finally:
            conn.close()

    def get_sandbox(self, sandbox_id: int) -> Optional[dict]:
        """Get a single sandbox by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_sandbox_environments WHERE id = %s", (sandbox_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_row(row)
                return row
        finally:
            conn.close()

    def get_default_sandbox(self) -> Optional[dict]:
        """Get the default sandbox environment."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_sandbox_environments WHERE is_default = 1 AND is_active = 1 LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    self._normalize_row(row)
                return row
        finally:
            conn.close()

    # ── Create / Update / Delete ──────────────────────────────────

    def create_sandbox(self, data: dict) -> int:
        """Create a new sandbox environment. Returns the new ID."""
        sandbox_id = _generate_id()
        now = _now()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # If setting as default, unset other defaults
                if data.get("is_default"):
                    cur.execute("UPDATE adh_sandbox_environments SET is_default = 0")

                cur.execute(
                    "INSERT INTO adh_sandbox_environments "
                    "(id, name, sandbox_type, display_name, description, config, "
                    "status, is_default, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        sandbox_id,
                        data["name"],
                        data["sandbox_type"],
                        data.get("display_name", ""),
                        data.get("description", ""),
                        json.dumps(data.get("config", {}), ensure_ascii=False),
                        "unknown",
                        1 if data.get("is_default") else 0,
                        1 if data.get("is_active", True) else 0,
                        now, now,
                    ),
                )
                conn.commit()
                return sandbox_id
        finally:
            conn.close()

    def update_sandbox(self, sandbox_id: int, data: dict) -> bool:
        """Update an existing sandbox environment."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Check exists
                cur.execute("SELECT id FROM adh_sandbox_environments WHERE id = %s", (sandbox_id,))
                if not cur.fetchone():
                    return False

                # If setting as default, unset other defaults
                if data.get("is_default"):
                    cur.execute("UPDATE adh_sandbox_environments SET is_default = 0")

                sets = ["updated_at = %s"]
                params = [_now()]
                for field in ["display_name", "description", "is_active", "is_default"]:
                    if field in data:
                        sets.append(f"{field} = %s")
                        params.append(data[field])
                if "config" in data:
                    sets.append("config = %s")
                    params.append(json.dumps(data["config"], ensure_ascii=False))

                params.append(sandbox_id)
                cur.execute(
                    f"UPDATE adh_sandbox_environments SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return True
        finally:
            conn.close()

    def delete_sandbox(self, sandbox_id: int) -> bool:
        """Delete a sandbox environment."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_sandbox_environments WHERE id = %s", (sandbox_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def set_default(self, sandbox_id: int) -> bool:
        """Set a sandbox as the default."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_sandbox_environments WHERE id = %s", (sandbox_id,))
                if not cur.fetchone():
                    return False
                cur.execute("UPDATE adh_sandbox_environments SET is_default = 0")
                cur.execute(
                    "UPDATE adh_sandbox_environments SET is_default = 1, updated_at = %s WHERE id = %s",
                    (_now(), sandbox_id),
                )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Connection Test ───────────────────────────────────────────

    def test_connection(self, sandbox_id: int) -> dict:
        """Test sandbox connectivity. Returns {success, message, resource_info}."""
        sandbox = self.get_sandbox(sandbox_id)
        if not sandbox:
            return {"success": False, "message": "沙箱不存在"}

        sandbox_type = sandbox["sandbox_type"]
        config = sandbox.get("config", {})

        try:
            if sandbox_type == "local":
                return self._test_local(config)
            elif sandbox_type == "ssh":
                return self._test_ssh(config)
            elif sandbox_type == "fc":
                return self._test_fc(config)
            else:
                return {"success": False, "message": f"不支持的沙箱类型: {sandbox_type}"}
        except Exception as e:
            logger.error(f"Sandbox test failed for {sandbox['name']}: {e}")
            return {"success": False, "message": str(e)}
        finally:
            # Update last_heartbeat
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE adh_sandbox_environments SET last_heartbeat = %s WHERE id = %s",
                        (_now(), sandbox_id),
                    )
                    conn.commit()
            finally:
                conn.close()

    def _test_local(self, config: dict) -> dict:
        """Test local Docker connectivity."""
        import subprocess
        socket = config.get("docker_socket", "unix:///var/run/docker.sock")
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                # Get resource info
                mem_result = subprocess.run(
                    ["docker", "info", "--format", "{{.MemTotal}}"],
                    capture_output=True, text=True, timeout=10,
                )
                cpu_result = subprocess.run(
                    ["docker", "info", "--format", "{{.NCPU}}"],
                    capture_output=True, text=True, timeout=10,
                )
                resource_info = {}
                if mem_result.returncode == 0:
                    try:
                        mem_bytes = int(mem_result.stdout.strip())
                        resource_info["memory_gb"] = round(mem_bytes / (1024**3), 1)
                    except ValueError:
                        pass
                if cpu_result.returncode == 0:
                    try:
                        resource_info["cpu"] = int(cpu_result.stdout.strip())
                    except ValueError:
                        pass
                return {
                    "success": True,
                    "message": f"Docker 连接成功 (v{version})",
                    "resource_info": resource_info,
                }
            else:
                return {"success": False, "message": f"Docker 连接失败: {result.stderr.strip()}"}
        except FileNotFoundError:
            return {"success": False, "message": "未找到 docker 命令，请确认 Docker 已安装"}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Docker 连接超时"}

    def _test_ssh(self, config: dict) -> dict:
        """Test SSH + remote Docker connectivity."""
        import subprocess
        host = config.get("host", "")
        port = config.get("port", 22)
        user = config.get("user", "root")
        auth_type = config.get("auth_type", "key")
        key_file = config.get("key_file", "")

        if not host:
            return {"success": False, "message": "未配置主机地址"}

        # Build SSH command
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-p", str(port)]
        if auth_type == "key" and key_file:
            ssh_cmd.extend(["-i", key_file])
        ssh_cmd.append(f"{user}@{host}")

        try:
            # Test SSH connection
            test_cmd = ssh_cmd + ["echo", "ok"]
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return {"success": False, "message": f"SSH 连接失败: {result.stderr.strip()}"}

            # Test remote Docker
            docker_cmd = ssh_cmd + ["docker", "info", "--format", "{{.ServerVersion}}"]
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                version = result.stdout.strip()
                # Get remote resources
                mem_cmd = ssh_cmd + ["docker", "info", "--format", "{{.MemTotal}}"]
                cpu_cmd = ssh_cmd + ["docker", "info", "--format", "{{.NCPU}}"]
                mem_result = subprocess.run(mem_cmd, capture_output=True, text=True, timeout=10)
                cpu_result = subprocess.run(cpu_cmd, capture_output=True, text=True, timeout=10)

                resource_info = {"host": host}
                if mem_result.returncode == 0:
                    try:
                        mem_bytes = int(mem_result.stdout.strip())
                        resource_info["memory_gb"] = round(mem_bytes / (1024**3), 1)
                    except ValueError:
                        pass
                if cpu_result.returncode == 0:
                    try:
                        resource_info["cpu"] = int(cpu_result.stdout.strip())
                    except ValueError:
                        pass

                # Check GPU
                gpu_cmd = ssh_cmd + ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
                gpu_result = subprocess.run(gpu_cmd, capture_output=True, text=True, timeout=10)
                if gpu_result.returncode == 0 and gpu_result.stdout.strip():
                    gpus = [line.strip() for line in gpu_result.stdout.strip().split("\n") if line.strip()]
                    resource_info["gpu"] = gpus[0] if gpus else "available"
                    resource_info["gpu_count"] = len(gpus)

                return {
                    "success": True,
                    "message": f"SSH + Docker 连接成功 ({host}, Docker v{version})",
                    "resource_info": resource_info,
                }
            else:
                return {"success": False, "message": f"远程 Docker 不可用: {result.stderr.strip()}"}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "SSH 连接超时"}
        except Exception as e:
            return {"success": False, "message": f"SSH 测试失败: {e}"}

    def _test_fc(self, config: dict) -> dict:
        """Test Aliyun FC connectivity."""
        region = config.get("region", "")
        ak = config.get("access_key_id", "")
        if not region or not ak:
            return {"success": False, "message": "未配置地域或 AccessKey"}
        # Basic validation — actual FC SDK test would require aliyun-fc2 SDK
        return {"success": True, "message": f"FC 配置验证通过 ({region})，详细测试需部署后验证"}

    # ── Helpers ───────────────────────────────────────────────────

    def _normalize_row(self, row: dict):
        """Normalize a database row for API response."""
        # Parse JSON fields
        for field in ["config", "resource_info"]:
            val = row.get(field)
            if isinstance(val, str):
                try:
                    row[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    row[field] = {}
            elif val is None:
                row[field] = {}

        # Convert datetime to string
        for field in ["created_at", "updated_at", "last_heartbeat"]:
            val = row.get(field)
            if val and hasattr(val, "strftime"):
                row[field] = val.strftime("%Y-%m-%d %H:%M:%S")

        # Ensure boolean-like fields
        row["is_default"] = bool(row.get("is_default", 0))
        row["is_active"] = bool(row.get("is_active", 1))

    def _normalize_log(self, row: dict):
        """Normalize a log row for API response."""
        for field in ["requirements"]:
            val = row.get(field)
            if isinstance(val, str):
                try:
                    row[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    row[field] = []
            elif val is None:
                row[field] = []
        for field in ["created_at"]:
            val = row.get(field)
            if val and hasattr(val, "strftime"):
                row[field] = val.strftime("%Y-%m-%d %H:%M:%S")
        row["success"] = bool(row.get("success", 0))

    # ── Execution Logs ────────────────────────────────────────────

    def log_execution(self, sandbox_id: int, sandbox_name: str, sandbox_type: str,
                      code: str, requirements: list, result: dict,
                      conversation_id: int = 0, user_id: int = 0):
        """Record a sandbox execution log entry."""
        log_id = _generate_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_sandbox_logs "
                    "(id, sandbox_id, sandbox_name, sandbox_type, code, requirements, "
                    "success, stdout, stderr, result, error, elapsed_ms, "
                    "conversation_id, user_id, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        log_id, sandbox_id, sandbox_name, sandbox_type,
                        code, json.dumps(requirements or [], ensure_ascii=False),
                        1 if result.get("success") else 0,
                        result.get("stdout", ""),
                        result.get("stderr", ""),
                        result.get("result", ""),
                        result.get("error", ""),
                        result.get("elapsed_ms", 0),
                        conversation_id, user_id, _now(),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log sandbox execution: {e}")
        finally:
            conn.close()

    def list_logs(self, sandbox_id: int = 0, user_id: int = 0,
                  page: int = 1, size: int = 50) -> dict:
        """List sandbox execution logs with pagination."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                conditions = []
                params = []
                if sandbox_id:
                    conditions.append("sandbox_id = %s")
                    params.append(sandbox_id)
                if user_id:
                    conditions.append("user_id = %s")
                    params.append(user_id)
                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                cur.execute(f"SELECT COUNT(*) AS total FROM adh_sandbox_logs {where}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_sandbox_logs {where} "
                    f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                rows = cur.fetchall()
                for r in rows:
                    self._normalize_log(r)
                return {"items": rows, "total": total}
        finally:
            conn.close()

    def get_log(self, log_id: int) -> Optional[dict]:
        """Get a single execution log by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_sandbox_logs WHERE id = %s", (log_id,))
                row = cur.fetchone()
                if row:
                    self._normalize_log(row)
                return row
        finally:
            conn.close()


# Singleton instance
sandbox_service = SandboxService()
