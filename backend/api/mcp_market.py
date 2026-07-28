"""MCP Market API — MCP 服务市场（注册表 CRUD + 一键安装 + npm 搜索）。

提供预置 MCP 服务目录浏览、分类筛选、一键安装到 adh_mcp_servers，
以及实时搜索 npm registry 上的 MCP 包。
"""

import json
import os
import time as _time
import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.auth import require_admin
from backend.common.auth import log_audit
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

def _find_command(name: str, fallback_paths: list = None) -> str:
    """查找命令的绝对路径，优先使用 shutil.which，其次尝试 fallback_paths。"""
    import shutil
    found = shutil.which(name)
    if found:
        return found
    for path in (fallback_paths or []):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # 最后回退到原始名称（让 subprocess 报错更清晰）
    return name


def _sanitize_image_name(name: str) -> str:
    """将 MCP 服务名转为合法的 Docker 镜像名。"""
    import re
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9._-]', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return f"adh-mcp/{s or 'unnamed'}:latest"


def _build_docker_image(image_name: str, build_info: dict) -> tuple[bool, str]:
    """构建 Docker 镜像。返回 (success, message)。"""
    import subprocess
    import tempfile

    base_image = build_info.get("base_image", "node:18-slim")
    install_cmd = build_info.get("install_cmd", "")
    entrypoint = build_info.get("entrypoint", "")
    add_main = build_info.get("add_main", False)

    if not install_cmd:
        return False, "缺少 install_cmd"

    # 生成 Dockerfile
    dockerfile_lines = [f"FROM {base_image}"]
    dockerfile_lines.append(f"RUN {install_cmd}")

    # 对于没有 __main__.py 的 pip 包，自动添加一个 wrapper
    if add_main:
        # 从 entrypoint 中提取模块名 (e.g., "python -m foo_bar" -> "foo_bar")
        ep_parts = entrypoint.split()
        module_name = ""
        if len(ep_parts) >= 3 and ep_parts[0] == "python" and ep_parts[1] == "-m":
            module_name = ep_parts[2]
        elif "pip install" in install_cmd:
            module_name = install_cmd.split()[-1].replace("-", "_")

        if module_name:
            # 创建一个 wrapper script，自动发现并运行 server.py
            wrapper = (
                f"RUN python -c \"{module_name}\" 2>/dev/null || true && "
                f"MODULE_DIR=$(python -c \"import {module_name} as m; print(m.__path__[0])\" 2>/dev/null) && "
                f"if [ ! -f \"$MODULE_DIR/__main__.py\" ] && [ -f \"$MODULE_DIR/server.py\" ]; then "
                f"echo 'from .server import main; main()' > \"$MODULE_DIR/__main__.py\"; fi || true"
            )
            dockerfile_lines.append(wrapper)

    if entrypoint:
        # 解析 entrypoint 为 JSON 数组格式
        parts = entrypoint.split()
        dockerfile_lines.append(f'ENTRYPOINT {json.dumps(parts)}')

    dockerfile_content = "\n".join(dockerfile_lines) + "\n"

    # 写入临时目录并构建
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = os.path.join(tmpdir, "Dockerfile")
            with open(dockerfile_path, "w") as f:
                f.write(dockerfile_content)

            logger.info("[MCP Install] Building image %s:\n%s", image_name, dockerfile_content)

            result = subprocess.run(
                ["docker", "build", "-t", image_name, "-f", dockerfile_path, tmpdir],
                capture_output=True, text=True, timeout=300,
            )

            if result.returncode != 0:
                error_msg = result.stderr[-500:] if result.stderr else "unknown error"
                logger.error("[MCP Install] Docker build failed: %s", error_msg)
                return False, f"Docker 构建失败: {error_msg}"

            logger.info("[MCP Install] Image %s built successfully", image_name)
            return True, f"镜像 {image_name} 构建成功"
    except subprocess.TimeoutExpired:
        return False, "Docker 构建超时（5分钟）"
    except Exception as e:
        return False, f"Docker 构建异常: {e}"


def _do_install(row_id: int, name: str = "", env_vars: dict = None,
                extra_args: list = None, description: str = "", admin=None,
                workspace_id: int = 0):
    """内部安装逻辑：构建 Docker 镜像并注册到 adh_mcp_servers。"""
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

        install_type = entry["install_type"]
        package_name = entry["package_name"]

        # ── Docker 模式（npm/pip 类型全部走 Docker） ──
        docker_image = ""
        command = ""
        args_str = ""

        if install_type in ("npm", "pip"):
            # 解析 Docker 构建信息
            build_info_raw = entry.get("install_cmd", "")
            if not build_info_raw:
                raise HTTPException(status_code=400,
                    detail=f"缺少 Docker 构建信息 (install_cmd)，无法安装 {custom_name}")

            try:
                build_info = json.loads(build_info_raw) if isinstance(build_info_raw, str) else build_info_raw
            except (json.JSONDecodeError, TypeError):
                raise HTTPException(status_code=400,
                    detail=f"install_cmd 格式错误，无法解析 Docker 构建信息")

            # 生成镜像名并构建
            docker_image = _sanitize_image_name(custom_name)
            ok, msg = _build_docker_image(docker_image, build_info)
            if not ok:
                raise HTTPException(status_code=500, detail=msg)

            # command 存储 entrypoint（可覆盖镜像默认 CMD）
            command = build_info.get("entrypoint", "")

        elif install_type == "docker":
            # 直接使用指定的 Docker 镜像
            docker_image = package_name
            command = ""

        else:
            raise HTTPException(status_code=400, detail=f"不支持的安装类型: {install_type}")

        # 环境变量写入 env 列
        env_json = ""
        if env_vars:
            env_json = json.dumps(env_vars, ensure_ascii=False)

        # 写入 adh_mcp_servers
        server_id = int(_time.time() * 1000000)
        now = _now()
        desc = description.strip() or entry.get("description", "")

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_mcp_servers "
                "(id, name, description, transport, url, command, docker_image, args, "
                "`env`, tools_config, is_active, datasource_id, workspace_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    server_id, custom_name, desc,
                    "stdio", "", command, docker_image, args_str,
                    env_json, "", 1, 0, workspace_id, now, now,
                ),
            )
        conn.commit()

        if admin:
            log_audit(admin.id, admin.username, "install_mcp",
                      target_type="mcp_server", target_id=server_id,
                      detail=f"安装MCP服务 {custom_name}（{install_type}）", module="system")

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
        workspace_id=req.get("workspace_id", 0),
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
                workspace_id=req.get("workspace_id", 0),
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


# ── Docker Config (复用沙箱 SSH 配置) ──────────────────────────────

@router.get("/docker-config")
def get_docker_config(admin=Depends(require_admin)):
    """获取 MCP Docker 构建的执行配置（从默认沙箱读取 SSH 配置）。"""
    from backend.services.docker_executor import get_docker_executor, reset_executor
    executor = get_docker_executor()
    mode_info = executor.detect_mode()
    return {
        "ssh_config": executor.ssh_config if executor.is_remote else {},
        "execution_mode": mode_info,
    }


@router.put("/docker-config")
def update_docker_config(req: dict, admin=Depends(require_admin)):
    """更新 MCP Docker 构建的 SSH 配置（存入默认沙箱配置）。
    Body: {host, port, user, auth_type, key_file}
    """
    from backend.services.docker_executor import reset_executor
    conn = get_metadata_conn()
    try:
        ssh_config = {
            "host": req.get("host", ""),
            "port": req.get("port", 22),
            "user": req.get("user", "root"),
            "auth_type": req.get("auth_type", "key"),
            "key_file": req.get("key_file", "~/.ssh/id_rsa"),
        }
        if not ssh_config["host"]:
            raise HTTPException(status_code=400, detail="host 不能为空")

        with conn.cursor() as cur:
            # 查找或创建 MCP 专用 SSH 沙箱
            cur.execute(
                "SELECT id FROM adh_sandbox_environments WHERE name = 'mcp-docker-ssh' LIMIT 1"
            )
            row = cur.fetchone()
            config_json = json.dumps(ssh_config, ensure_ascii=False)

            if row:
                cur.execute(
                    "UPDATE adh_sandbox_environments SET config=%s, is_default=1, updated_at=%s WHERE id=%s",
                    (config_json, _now(), row["id"]),
                )
            else:
                sid = int(_time.time() * 1000000)
                cur.execute(
                    "INSERT INTO adh_sandbox_environments "
                    "(id, name, sandbox_type, display_name, description, config, is_default, is_active, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid, "mcp-docker-ssh", "ssh", "MCP Docker SSH",
                     "MCP 服务 Docker 构建远程执行环境", config_json, 1, 1, _now(), _now()),
                )
        conn.commit()
        reset_executor()
        return {"success": True, "ssh_config": ssh_config}
    finally:
        conn.close()


# ── SSE 流式安装 ──────────────────────────────────────────────────

def _load_sandbox_ssh_config(sandbox_id: int) -> dict:
    """从沙箱环境加载 SSH 配置。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sandbox_type, config FROM adh_sandbox_environments WHERE id = %s AND is_active = 1",
                (sandbox_id,)
            )
            row = cur.fetchone()
            if not row:
                return {}
            if row["sandbox_type"] != "ssh":
                return {}
            cfg = row.get("config", "")
            if isinstance(cfg, str):
                cfg = json.loads(cfg) if cfg else {}
            return cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        logger.warning("Failed to load sandbox SSH config: %s", e)
        return {}
    finally:
        conn.close()


@router.get("/sandboxes")
def list_sandboxes_for_mcp(admin=Depends(require_admin)):
    """列出可用于 MCP Docker 构建的沙箱环境。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, sandbox_type, display_name, description, is_default, is_active "
                "FROM adh_sandbox_environments WHERE is_active = 1 ORDER BY is_default DESC, id ASC"
            )
            rows = cur.fetchall()
            # 标记哪些支持 Docker
            result = []
            for r in rows:
                r["supports_docker"] = r["sandbox_type"] in ("ssh", "local")
                result.append(r)
            return result
    finally:
        conn.close()

@router.post("/{row_id}/install-stream")
async def install_mcp_stream(row_id: int, req: dict = None, admin=Depends(require_admin)):
    """SSE 流式安装 MCP 服务：构建 Docker 镜像并注册。

    Body:
        name: 自定义名称（可选）
        env_vars: 环境变量 dict（可选）
        description: 自定义描述（可选）
        sandbox_id: 沙箱环境 ID（可选，指定 Docker 构建目标环境）
        ssh_config: SSH 配置（可选，直接指定，优先级高于 sandbox_id）
    """
    from fastapi.responses import StreamingResponse
    from backend.services.docker_executor import get_docker_executor

    if req is None:
        req = {}

    # 解析 SSH 配置：优先用直接传入的 ssh_config，其次从 sandbox_id 查找
    ssh_config = req.get("ssh_config")
    if not ssh_config and req.get("sandbox_id"):
        ssh_config = _load_sandbox_ssh_config(req["sandbox_id"])
    if not ssh_config:
        ssh_config = None  # 让 executor 使用默认沙箱

    async def event_generator():
        """SSE 事件生成器。"""
        import asyncio
        import queue
        import threading

        log_queue: queue.Queue = queue.Queue()

        def log_callback(line: str):
            log_queue.put(line)

        def do_install_sync():
            """在后台线程中执行安装。"""
            try:
                _do_install_with_stream(
                    row_id=row_id,
                    name=req.get("name", ""),
                    env_vars=req.get("env_vars", {}),
                    description=req.get("description", ""),
                    admin=admin,
                    workspace_id=req.get("workspace_id", 0),
                    ssh_config=ssh_config,
                    log_callback=log_callback,
                )
                log_queue.put(None)  # 结束信号
            except Exception as e:
                log_queue.put(f"__ERROR__:{e}")
                log_queue.put(None)

        # 在后台线程执行安装
        thread = threading.Thread(target=do_install_sync, daemon=True)
        thread.start()

        # 持续从队列读取日志并 yield SSE 事件
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: log_queue.get(timeout=300)
                )
                if line is None:
                    break
                if line.startswith("__ERROR__"):
                    error_msg = line[len("__ERROR__:"):]
                    yield f"event: error\ndata: {json.dumps({'success': False, 'message': error_msg}, ensure_ascii=False)}\n\n"
                else:
                    yield f"event: log\ndata: {json.dumps({'message': line}, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"event: error\ndata: {json.dumps({'success': False, 'message': '安装超时'}, ensure_ascii=False)}\n\n"
                break

        thread.join(timeout=5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _do_install_with_stream(row_id: int, name: str = "", env_vars: dict = None,
                            description: str = "", admin=None, workspace_id: int = 0,
                            ssh_config: dict = None, log_callback=None):
    """带流式日志输出的安装逻辑。"""
    if env_vars is None:
        env_vars = {}
    if log_callback is None:
        log_callback = lambda line: None

    from backend.services.docker_executor import get_docker_executor

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_mcp_registry WHERE id = %s", (row_id,))
            entry = cur.fetchone()
        if not entry:
            raise Exception("注册表条目不存在")

        custom_name = name.strip() or entry["name"]
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_mcp_servers WHERE name = %s", (custom_name,))
            if cur.fetchone():
                raise Exception(f"服务 '{custom_name}' 已存在")

        install_type = entry["install_type"]
        package_name = entry["package_name"]

        log_callback(f"📦 准备安装 {custom_name} ({install_type}: {package_name})")

        # ── Docker 模式 ──
        docker_image = ""
        command = ""

        if install_type in ("npm", "pip"):
            build_info_raw = entry.get("install_cmd", "")
            if not build_info_raw:
                raise Exception("缺少 Docker 构建信息")

            build_info = json.loads(build_info_raw) if isinstance(build_info_raw, str) else build_info_raw
            docker_image = _sanitize_image_name(custom_name)

            # 生成 Dockerfile
            dockerfile = _generate_dockerfile(build_info)
            log_callback(f"📝 Dockerfile:\n{dockerfile}")

            # 获取执行器（SSH 或本地）
            executor = get_docker_executor(ssh_config)
            mode = executor.detect_mode()
            log_callback(f"🔧 执行模式: {mode}")

            if not mode.get("available"):
                raise Exception(f"Docker 不可用: {mode.get('error', '未知')}")

            # 构建镜像
            log_callback(f"🔨 开始构建镜像 {docker_image} ...")
            ok, msg = executor.build_image(docker_image, dockerfile, log_callback, timeout=600)
            if not ok:
                raise Exception(f"镜像构建失败: {msg}")

            command = build_info.get("entrypoint", "")

        elif install_type == "docker":
            docker_image = package_name
            log_callback(f"🐳 使用预构建镜像: {docker_image}")

        else:
            raise Exception(f"不支持的安装类型: {install_type}")

        # 写入数据库
        env_json = json.dumps(env_vars, ensure_ascii=False) if env_vars else ""
        server_id = int(_time.time() * 1000000)
        now = _now()
        desc = description.strip() or entry.get("description", "")

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_mcp_servers "
                "(id, name, description, transport, url, command, docker_image, args, "
                "`env`, tools_config, is_active, datasource_id, workspace_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (server_id, custom_name, desc, "stdio", "", command, docker_image, "",
                 env_json, "", 1, 0, workspace_id, now, now),
            )
        conn.commit()

        if admin:
            log_audit(admin.id, admin.username, "install_mcp",
                      target_type="mcp_server", target_id=server_id,
                      detail=f"安装MCP服务 {custom_name}（Docker）", module="system")

        log_callback(f"✅ 安装成功！server_id={server_id}")

        # 发送完成事件（通过特殊标记）
        log_callback(f"__DONE__:{json.dumps({'success': True, 'server_id': server_id, 'message': f'已安装 {custom_name}'})}")

    except Exception as e:
        log_callback(f"❌ {e}")
        raise
    finally:
        conn.close()


def _generate_dockerfile(build_info: dict) -> str:
    """从构建信息生成 Dockerfile 内容。"""
    base_image = build_info.get("base_image", "node:18-slim")
    install_cmd = build_info.get("install_cmd", "")
    entrypoint = build_info.get("entrypoint", "")
    add_main = build_info.get("add_main", False)

    lines = [f"FROM {base_image}"]
    lines.append(f"RUN {install_cmd}")

    if add_main:
        ep_parts = entrypoint.split()
        module_name = ""
        if len(ep_parts) >= 3 and ep_parts[0] == "python" and ep_parts[1] == "-m":
            module_name = ep_parts[2]

        if module_name:
            lines.append(
                f'RUN python -c "import {module_name}" 2>/dev/null || true && '
                f'MODULE_DIR=$(python -c "import {module_name} as m; print(m.__path__[0])" 2>/dev/null) && '
                f'if [ ! -f "$MODULE_DIR/__main__.py" ] && [ -f "$MODULE_DIR/server.py" ]; then '
                f'echo \'from .server import main; main()\' > "$MODULE_DIR/__main__.py"; fi || true'
            )

    if entrypoint:
        parts = entrypoint.split()
        lines.append(f'ENTRYPOINT {json.dumps(parts)}')

    return "\n".join(lines) + "\n"
