"""MCP Market Service — CRUD operations for adh_mcp_registry + install.

Migrated from backend/api/mcp_market.py.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)

CATEGORIES = [
    {"key": "database", "label": "数据库", "icon": "Database"},
    {"key": "filesystem", "label": "文件系统", "icon": "FolderOpen"},
    {"key": "devtools", "label": "开发工具", "icon": "Wrench"},
    {"key": "search", "label": "搜索引擎", "icon": "Search"},
    {"key": "cloud", "label": "云服务", "icon": "Cloud"},
    {"key": "communication", "label": "通讯协作", "icon": "MessageSquare"},
    {"key": "media", "label": "媒体处理", "icon": "Image"},
    {"key": "ai", "label": "AI / ML", "icon": "Brain"},
    {"key": "bigdata", "label": "大数据", "icon": "Database"},
    {"key": "other", "label": "其他", "icon": "Package"},
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


def list_categories() -> list:
    """Get MCP service categories with counts."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT category, COUNT(*) AS cnt FROM adh_mcp_registry GROUP BY category"
            )
            counts = {r["category"]: r["cnt"] for r in cur.fetchall()}

    result = []
    for cat in CATEGORIES:
        result.append({**cat, "count": counts.get(cat["key"], 0)})
    return result


def list_registry(
    category: str = "", keyword: str = "", install_type: str = "",
    is_verified: Optional[int] = None, is_popular: Optional[int] = None,
    page: int = 1, size: int = 50,
) -> dict:
    """List MCP registry entries with filtering and pagination."""
    conditions = []
    params = []
    if category:
        conditions.append("category = %s")
        params.append(category)
    if install_type:
        conditions.append("install_type = %s")
        params.append(install_type)
    if is_verified is not None:
        conditions.append("is_verified = %s")
        params.append(is_verified)
    if is_popular is not None:
        conditions.append("is_popular = %s")
        params.append(is_popular)
    if keyword:
        conditions.append(
            "(name LIKE %s OR description LIKE %s OR tags LIKE %s OR package_name LIKE %s)"
        )
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_mcp_registry {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, name, package_name, description, author, homepage, "
                f"install_type, default_args, required_env, category, tags, "
                f"logo_url, stars, downloads, is_verified, is_popular, sort_order, "
                f"created_at, updated_at "
                f"FROM adh_mcp_registry {where} "
                f"ORDER BY is_popular DESC, sort_order ASC, id DESC "
                f"LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()

            # Check which are already installed
            if rows:
                cur.execute("SELECT name FROM adh_mcp_servers")
                installed_names = {r["name"] for r in cur.fetchall()}
                for row in rows:
                    row["is_installed"] = row["name"] in installed_names

    return {"total": total, "items": rows}


def get_registry(row_id: int) -> Optional[dict]:
    """Get a single MCP registry entry."""
    return execute_query(
        "SELECT * FROM adh_mcp_registry WHERE id = %s", (row_id,), fetchone=True
    )


def create_registry(data: dict) -> int:
    """Create a new MCP registry entry."""
    if not data.get("name") or not data.get("package_name"):
        raise ValueError("name and package_name are required")

    row_id = _generate_id()
    now = _now()
    execute_insert(
        "INSERT INTO adh_mcp_registry "
        "(id, name, package_name, description, author, homepage, "
        "install_type, install_cmd, default_args, required_env, "
        "category, tags, logo_url, stars, downloads, "
        "is_verified, is_popular, sort_order, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row_id, data["name"], data["package_name"],
         data.get("description", ""), data.get("author", ""),
         data.get("homepage", ""), data.get("install_type", "npm"),
         data.get("install_cmd", ""),
         json.dumps(data.get("default_args", []), ensure_ascii=False) if isinstance(data.get("default_args"), list) else data.get("default_args", ""),
         json.dumps(data.get("required_env", {}), ensure_ascii=False) if isinstance(data.get("required_env"), dict) else data.get("required_env", ""),
         data.get("category", "other"),
         json.dumps(data.get("tags", []), ensure_ascii=False) if isinstance(data.get("tags"), list) else data.get("tags", ""),
         data.get("logo_url", ""),
         data.get("stars", 0), data.get("downloads", 0),
         data.get("is_verified", 0), data.get("is_popular", 0),
         data.get("sort_order", 0), now, now),
    )
    return row_id


def update_registry(row_id: int, data: dict) -> bool:
    """Update MCP registry entry."""
    allowed = (
        "name", "package_name", "description", "author", "homepage",
        "install_type", "install_cmd", "category",
        "logo_url", "stars", "downloads",
        "is_verified", "is_popular", "sort_order",
    )
    fields, params = [], []
    for key in allowed:
        if key in data:
            fields.append(f"{key} = %s")
            params.append(data[key])
    # JSON fields
    for key in ("default_args", "required_env", "tags"):
        if key in data:
            fields.append(f"{key} = %s")
            val = data[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            params.append(val)

    if not fields:
        return False

    fields.append("updated_at = %s")
    params.append(_now())
    params.append(row_id)
    execute_write(f"UPDATE adh_mcp_registry SET {', '.join(fields)} WHERE id = %s", params)
    return True


def delete_registry(row_id: int) -> bool:
    """Delete MCP registry entry."""
    execute_write("DELETE FROM adh_mcp_registry WHERE id = %s", (row_id,))
    return True


def install_from_registry(
    row_id: int, name: str = "", env_vars: dict = None,
    extra_args: list = None, description: str = "", workspace_id: int = 0,
) -> dict:
    """Install MCP server from registry entry."""
    if env_vars is None:
        env_vars = {}
    if extra_args is None:
        extra_args = []

    entry = execute_query(
        "SELECT * FROM adh_mcp_registry WHERE id = %s", (row_id,), fetchone=True
    )
    if not entry:
        raise ValueError("Registry entry not found")

    custom_name = name.strip() or entry["name"]

    # Check if already installed
    existing = execute_query(
        "SELECT id FROM adh_mcp_servers WHERE name = %s", (custom_name,), fetchone=True
    )
    if existing:
        raise ValueError(f"Service '{custom_name}' already exists")

    # Build command + args
    install_type = entry["install_type"]
    package_name = entry["package_name"]

    if install_type == "npm":
        command = "npx"
        args_list = ["-y", package_name]
    elif install_type == "pip":
        command = "uvx"
        args_list = [package_name]
    elif install_type == "docker":
        command = "docker"
        args_list = ["run", "-i", "--rm", package_name]
    else:
        command = entry.get("install_cmd", package_name)
        args_list = []

    # Append default args
    default_args = entry.get("default_args", "")
    if default_args:
        try:
            parsed = json.loads(default_args) if isinstance(default_args, str) else default_args
            if isinstance(parsed, list):
                args_list.extend(str(a) for a in parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    # Append user extra args
    if extra_args:
        args_list.extend(str(a) for a in extra_args)

    args_str = ",".join(args_list)

    # Environment variables
    tools_config = ""
    if env_vars:
        tools_config = json.dumps(env_vars, ensure_ascii=False)

    # Insert into adh_mcp_servers
    server_id = _generate_id()
    now = _now()
    desc = description.strip() or entry.get("description", "")

    execute_insert(
        "INSERT INTO adh_mcp_servers "
        "(id, name, description, transport, url, command, args, "
        "tools_config, is_active, datasource_id, workspace_id, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (server_id, custom_name, desc,
         "stdio", "", command, args_str,
         tools_config, 1, 0, workspace_id, now, now),
    )

    return {
        "success": True,
        "server_id": server_id,
        "message": f"Installed {custom_name} ({install_type})",
    }


def batch_install(ids: list, env_vars: dict = None, workspace_id: int = 0) -> list:
    """Batch install multiple MCP services from registry."""
    results = []
    for row_id in ids:
        try:
            result = install_from_registry(
                row_id=row_id, env_vars=env_vars or {}, workspace_id=workspace_id
            )
            results.append({"id": row_id, **result})
        except ValueError as e:
            results.append({"id": row_id, "success": False, "message": str(e)})
        except Exception as e:
            results.append({"id": row_id, "success": False, "message": str(e)})
    return results


def _guess_category(keywords: list, name: str, desc: str) -> str:
    """Guess MCP service category from keywords."""
    text = " ".join(keywords + [name, desc]).lower()
    if any(k in text for k in ["flink", "spark", "hadoop", "hive", "kafka", "bigdata"]):
        return "bigdata"
    if any(k in text for k in ["database", "sql", "postgres", "mysql", "sqlite", "mongo", "redis"]):
        return "database"
    if any(k in text for k in ["file", "filesystem", "folder", "disk"]):
        return "filesystem"
    if any(k in text for k in ["github", "git", "docker", "kubernetes", "ci", "dev"]):
        return "devtools"
    if any(k in text for k in ["search", "web", "browser", "scrape"]):
        return "search"
    if any(k in text for k in ["aws", "azure", "gcp", "cloud", "s3", "bucket"]):
        return "cloud"
    if any(k in text for k in ["slack", "email", "discord", "chat", "message"]):
        return "communication"
    if any(k in text for k in ["image", "video", "audio", "media", "pdf"]):
        return "media"
    if any(k in text for k in ["ai", "ml", "model", "llm", "embedding"]):
        return "ai"
    return "other"
