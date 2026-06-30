"""MCP Market API — MCP 服务市场（注册表 CRUD + 一键安装 + npm 搜索）。

提供预置 MCP 服务目录浏览、分类筛选、一键安装到 adh_mcp_servers，
以及实时搜索 npm registry 上的 MCP 包。
"""

import json
import time as _time
import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.auth import require_admin
from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

router = APIRouter()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Categories ──────────────────────────────────────────────────────

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


@router.get("/categories")
def list_categories():
    """获取 MCP 服务分类列表（含每个分类的数量）。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT category, COUNT(*) AS cnt FROM adh_mcp_registry GROUP BY category"
            )
            counts = {r["category"]: r["cnt"] for r in cur.fetchall()}
    finally:
        conn.close()

    result = []
    for cat in CATEGORIES:
        result.append({**cat, "count": counts.get(cat["key"], 0)})
    return result


# ── List / Search ──────────────────────────────────────────────────

@router.get("/")
def list_registry(
    category: str = Query("", description="按分类筛选"),
    keyword: str = Query("", description="搜索关键词（名称/描述/标签）"),
    install_type: str = Query("", description="安装类型: npm/pip/docker/binary"),
    is_verified: Optional[int] = Query(None, description="只看官方/审核"),
    is_popular: Optional[int] = Query(None, description="只看推荐"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    """获取 MCP 服务注册表列表。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
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

            # 检查哪些已安装（关联 adh_mcp_servers）
            if rows:
                installed_names = set()
                cur.execute("SELECT name FROM adh_mcp_servers")
                for r in cur.fetchall():
                    installed_names.add(r["name"])

                for row in rows:
                    row["is_installed"] = row["name"] in installed_names

            return {"total": total, "items": rows}
    finally:
        conn.close()


# ── Detail ─────────────────────────────────────────────────────────

@router.get("/{row_id}")
def get_registry(row_id: int):
    """获取单个 MCP 服务详情。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adh_mcp_registry WHERE id = %s", (row_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="记录不存在")
            return row
    finally:
        conn.close()


# ── CRUD (Admin) ───────────────────────────────────────────────────

@router.post("/")
def create_registry(req: dict, admin=Depends(require_admin)):
    """新增 MCP 服务注册表条目。"""
    if not req.get("name") or not req.get("package_name"):
        raise HTTPException(status_code=400, detail="name 和 package_name 必填")

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            row_id = int(_time.time() * 1000000)
            now = _now()
            cur.execute(
                "INSERT INTO adh_mcp_registry "
                "(id, name, package_name, description, author, homepage, "
                "install_type, install_cmd, default_args, required_env, "
                "category, tags, logo_url, stars, downloads, "
                "is_verified, is_popular, sort_order, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    row_id, req["name"], req["package_name"],
                    req.get("description", ""), req.get("author", ""),
                    req.get("homepage", ""), req.get("install_type", "npm"),
                    req.get("install_cmd", ""), req.get("default_args", ""),
                    req.get("required_env", ""), req.get("category", "other"),
                    req.get("tags", ""), req.get("logo_url", ""),
                    req.get("stars", 0), req.get("downloads", 0),
                    req.get("is_verified", 0), req.get("is_popular", 0),
                    req.get("sort_order", 0), now, now,
                ),
            )
        conn.commit()
        return {"id": row_id, "success": True}
    finally:
        conn.close()


@router.put("/{row_id}")
def update_registry(row_id: int, req: dict, admin=Depends(require_admin)):
    """更新 MCP 服务注册表条目。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            allowed = (
                "name", "package_name", "description", "author", "homepage",
                "install_type", "install_cmd", "default_args", "required_env",
                "category", "tags", "logo_url", "stars", "downloads",
                "is_verified", "is_popular", "sort_order",
            )
            fields, params = [], []
            for key in allowed:
                if key in req:
                    fields.append(f"{key} = %s")
                    params.append(req[key])
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            fields.append("updated_at = %s")
            params.append(_now())
            params.append(row_id)
            cur.execute(
                f"UPDATE adh_mcp_registry SET {', '.join(fields)} WHERE id = %s",
                params,
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/{row_id}")
def delete_registry(row_id: int, admin=Depends(require_admin)):
    """删除 MCP 服务注册表条目。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_mcp_registry WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── Install ────────────────────────────────────────────────────────

def _do_install(row_id: int, name: str = "", env_vars: dict = None,
                extra_args: list = None, description: str = "", admin=None):
    """内部安装逻辑，供 install 和 batch_install 共用。"""
    if env_vars is None:
        env_vars = {}
    if extra_args is None:
        extra_args = []

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_mcp_registry WHERE id = %s", (row_id,))
            entry = cur.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="注册表条目不存在")

        # 检查是否已安装（按名称去重）
        custom_name = name.strip() or entry["name"]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM adh_mcp_servers WHERE name = %s", (custom_name,)
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail=f"服务 '{custom_name}' 已存在")

        # 构建 MCP 服务配置
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

        # 追加默认参数
        default_args = entry.get("default_args", "")
        if default_args:
            try:
                parsed = json.loads(default_args) if isinstance(default_args, str) else default_args
                if isinstance(parsed, list):
                    args_list.extend(str(a) for a in parsed)
            except (json.JSONDecodeError, TypeError):
                pass

        # 追加用户额外参数
        if extra_args:
            args_list.extend(str(a) for a in extra_args)

        args_str = ",".join(args_list)

        # 环境变量写入 tools_config
        tools_config = ""
        if env_vars:
            tools_config = json.dumps(env_vars, ensure_ascii=False)

        # 写入 adh_mcp_servers
        server_id = int(_time.time() * 1000000)
        now = _now()
        desc = description.strip() or entry.get("description", "")

        # Get workspace_id from request if provided
        workspace_id = req.get("workspace_id", 0) if req else 0

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_mcp_servers "
                "(id, name, description, transport, url, command, args, "
                "tools_config, is_active, datasource_id, workspace_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    server_id, custom_name, desc,
                    "stdio", "", command, args_str,
                    tools_config, 1, 0, workspace_id, now, now,
                ),
            )
        conn.commit()

        return {
            "success": True,
            "server_id": server_id,
            "message": f"已安装 {custom_name}（{install_type}）",
        }
    finally:
        conn.close()


@router.post("/{row_id}/install")
def install_mcp(row_id: int, req: dict = None, admin=Depends(require_admin)):
    """一键安装 MCP 服务：从注册表读取配置，写入 adh_mcp_servers。

    Body:
        name: 自定义名称（可选，默认用注册表 name）
        env_vars: 环境变量 dict（可选，对应 required_env）
        extra_args: 额外参数 list（可选）
        description: 自定义描述（可选）
    """
    if req is None:
        req = {}

    return _do_install(
        row_id=row_id,
        name=req.get("name", ""),
        env_vars=req.get("env_vars", {}),
        extra_args=req.get("extra_args", []),
        description=req.get("description", ""),
        admin=admin,
    )


# ── Batch Install ──────────────────────────────────────────────────

@router.post("/install/batch")
def batch_install(req: dict, admin=Depends(require_admin)):
    """批量安装多个 MCP 服务。

    Body:
        ids: list[int] — 注册表 ID 列表
        env_vars: dict — 全局环境变量（可选，对所有服务生效）
    """
    ids = req.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    results = []
    for row_id in ids:
        try:
            result = _do_install(
                row_id=row_id,
                env_vars=req.get("env_vars", {}),
                admin=admin,
            )
            results.append({"id": row_id, **result})
        except HTTPException as e:
            results.append({"id": row_id, "success": False, "message": e.detail})
        except Exception as e:
            results.append({"id": row_id, "success": False, "message": str(e)})

    return {"results": results}


# ── npm Search ─────────────────────────────────────────────────────

_NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
_MCP_KEYWORDS = ["mcp", "model-context-protocol", "modelcontextprotocol"]


@router.get("/npm/search")
async def search_npm(
    keyword: str = Query("", description="搜索关键词"),
    size: int = Query(20, ge=1, le=50),
):
    """搜索 npm registry 上的 MCP 相关包。

    自动添加 mcp 相关关键词以提高命中率。
    """
    query = keyword.strip()
    if not query:
        query = "mcp server"
    elif not any(kw in query.lower() for kw in _MCP_KEYWORDS):
        query = f"{query} mcp"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _NPM_SEARCH_URL,
                params={"text": query, "size": size},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            results.append({
                "name": pkg.get("name", ""),
                "version": pkg.get("version", ""),
                "description": pkg.get("description", ""),
                "author": (pkg.get("author") or {}).get("name", ""),
                "homepage": (pkg.get("links") or {}).get("homepage", ""),
                "repository": (pkg.get("links") or {}).get("repository", ""),
                "npm_url": (pkg.get("links") or {}).get("npm", ""),
                "keywords": pkg.get("keywords", []),
                "stars": 0,
                "downloads": 0,
            })

        return {"total": data.get("total", 0), "items": results}
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="npm 搜索超时，请稍后重试")
    except Exception as e:
        logger.error("npm search failed: %s", e)
        raise HTTPException(status_code=502, detail=f"npm 搜索失败: {e}")


# ── npm Import ─────────────────────────────────────────────────────

@router.post("/npm/import")
async def import_from_npm(req: dict, admin=Depends(require_admin)):
    """从 npm 导入一个 MCP 包到注册表。

    Body:
        name: npm 包名
        category: 分类（可选，默认 other）
    """
    package_name = req.get("name", "").strip()
    if not package_name:
        raise HTTPException(status_code=400, detail="包名不能为空")

    # 从 npm 获取包信息
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://registry.npmjs.org/{package_name}")
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"npm 包 '{package_name}' 不存在")
            resp.raise_for_status()
            pkg = resp.json()

        latest_version = pkg.get("dist-tags", {}).get("latest", "")
        latest_info = pkg.get("versions", {}).get(latest_version, {})

        name = latest_info.get("name", package_name).split("/")[-1]
        description = latest_info.get("description", "") or pkg.get("description", "")
        author_raw = latest_info.get("author", "")
        if isinstance(author_raw, dict):
            author = author_raw.get("name", "")
        else:
            author = str(author_raw)
        homepage = latest_info.get("homepage", "") or (latest_info.get("repository") or {}).get("url", "")
        keywords = latest_info.get("keywords", []) or pkg.get("keywords", [])

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="npm 请求超时")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("npm fetch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"获取 npm 包信息失败: {e}")

    # 检查是否已存在
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM adh_mcp_registry WHERE package_name = %s",
                (package_name,),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail=f"包 '{package_name}' 已在注册表中")

            category = req.get("category", "").strip() or _guess_category(keywords, name, description)

            row_id = int(_time.time() * 1000000)
            now = _now()
            cur.execute(
                "INSERT INTO adh_mcp_registry "
                "(id, name, package_name, description, author, homepage, "
                "install_type, category, tags, is_verified, is_popular, "
                "sort_order, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    row_id, name, package_name, description, author, homepage,
                    "npm", category, ",".join(keywords[:10]),
                    0, 0, 0, now, now,
                ),
            )
        conn.commit()
        return {"id": row_id, "success": True, "message": f"已导入 {package_name}"}
    finally:
        conn.close()


def _guess_category(keywords: list, name: str, desc: str) -> str:
    """根据关键词猜测 MCP 服务分类。"""
    text = " ".join(keywords + [name, desc]).lower()

    if any(k in text for k in ["flink", "spark", "hadoop", "hive", "kafka", "bigdata", "大数据"]):
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
