"""User Assets API — 用户资产清单(OSS 托管, 资产跟随用户跨工作空间)。

资产归属是**用户**而非工作空间: 会话产物经"归档收藏"进入个人资产清单, 一旦归档
跟着用户走, 不受工作空间限制:
  * 资产本体托管对象存储(OSS/MinIO, 未配置回退本地), 对象 key 为真值
    ({prefix}/{user_id}/assets/{asset_id}_{filename}, 见 object_storage.build_asset_key);
  * origin_workspace_id/source_conversation_id 仅作溯源展示与本地产物清理定位;
  * 归档后会话本地产物可基于磁盘配额清理(资产不受影响);
  * LLM 经 assets 工具组感知清单并与用户确认归档/清理。

路由分两域:
  * /api/assets          — 用户资产域(跨工作空间), 属主校验 = user_id 过滤(fail-closed);
  * /api/workspace-assets — 工作空间本地产物域(会话文件/磁盘配额/清理), 仍按工作空间治理。
"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from backend.common.auth import get_current_user, authorize_workspace
from backend.common.db.metadata_db import get_metadata_conn
from backend.common.object_storage import build_asset_key, get_object_storage

logger = logging.getLogger(__name__)

# 用户资产域: 资产跟随用户, 跨工作空间
router = APIRouter()
# 工作空间本地产物域: 会话文件/磁盘用量/清理仍按工作空间治理
workspace_router = APIRouter()


class ArchiveRequest(BaseModel):
    name: str
    conversation_id: int
    path: str
    delete_source: bool = False


class CleanupRequest(BaseModel):
    confirm: bool = False


def _locate_session_file(user_id: int, conversation_id: int, rel_path: str,
                         workspace_id: Optional[int] = None):
    """定位会话产物文件(仅属主, 防目录穿越/符号链接逃逸)。

    workspace_id 给定时叠加工作空间过滤(本地产物域端点用); 用户资产域按
    conversation_id + user_id 定位(资产跟人走, 不限定工作空间)。
    返回 (target, workspace_id, 规范化相对路径)。
    """
    from backend.modules.mind.execution import session_workspace as sw
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id is None:
                cur.execute(
                    "SELECT session_key, workspace_id FROM adh_agent_sessions "
                    "WHERE conversation_id=%s AND user_id=%s",
                    (conversation_id, user_id))
            else:
                cur.execute(
                    "SELECT session_key, workspace_id FROM adh_agent_sessions "
                    "WHERE conversation_id=%s AND user_id=%s AND workspace_id=%s",
                    (conversation_id, user_id, workspace_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row.get("session_key"):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    base = sw.workspace_base(create=False)
    root = sw.session_paths(base, row["session_key"], row["workspace_id"], create=False)
    ws_dir = (root / "workspace").resolve()
    rel = (rel_path or "").strip()
    for prefix in ("/workspace/", "/workspace", "workspace/", "./", "/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    rel = rel.lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    target = (ws_dir / rel).resolve()
    if target != ws_dir and ws_dir not in target.parents:
        raise HTTPException(status_code=403, detail="非法文件路径")
    for p in [target, *target.parents]:
        if p == ws_dir:
            break
        if p.is_symlink():
            raise HTTPException(status_code=403, detail="非法文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target, row["workspace_id"], rel


def archive_session_file(user_id: int, conversation_id: int, rel_path: str,
                         name: str, delete_source: bool = False) -> dict:
    """归档会话产物到**用户**资产清单(供 LLM 工具与 API 共用)。

    资产归属 user_id(跨工作空间随用户走); 来源工作空间/会话仅作溯源。
    返回体不含 object_key(内部存储标识, 不外露)。
    """
    target, origin_ws, rel = _locate_session_file(user_id, conversation_id, rel_path)
    data = target.read_bytes()
    filename = target.name
    asset_id = uuid.uuid4().hex
    object_key = build_asset_key(user_id, asset_id, filename)
    storage = get_object_storage()
    storage.upload_bytes(object_key, data, content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_user_assets "
                "(id, user_id, origin_workspace_id, name, filename, category, object_key, storage_type, size, "
                " source_conversation_id, source_path, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (asset_id, user_id, origin_ws, name or filename, filename, _category(filename),
                 object_key, "object" if storage.is_object_storage else "local", len(data),
                 conversation_id, rel, user_id))
            conn.commit()
    finally:
        conn.close()
    if delete_source:
        try:
            target.unlink()
        except OSError:
            logger.warning("归档成功但删除本地产物失败: %s", target)
    return {"id": asset_id, "name": name or filename, "filename": filename, "size": len(data),
            "source_conversation_id": conversation_id, "origin_workspace_id": origin_ws}


def _category(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        return "image"
    if ext in ("sql",):
        return "sql"
    if ext in ("html", "pdf", "docx", "xlsx", "pptx"):
        return "report"
    return "file"


def list_user_assets(user_id: int) -> list:
    """用户资产清单(跨工作空间); origin_workspace_name 供前端溯源展示。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.name, a.filename, a.category, a.size, a.source_conversation_id, "
                "a.origin_workspace_id, w.name AS origin_workspace_name, a.created_at "
                "FROM adh_user_assets a LEFT JOIN adh_workspaces w ON w.id = a.origin_workspace_id "
                "WHERE a.user_id=%s ORDER BY a.created_at DESC",
                (user_id,))
            return cur.fetchall()
    finally:
        conn.close()


@router.get("")
def list_assets_endpoint(user: dict = Depends(get_current_user)):
    """我的资产清单(跟随用户, 跨工作空间)。"""
    return list_user_assets(user["user_id"])


@router.post("/archive")
def archive_asset_endpoint(req: ArchiveRequest, user: dict = Depends(get_current_user)):
    """归档会话产物到我的资产清单(OSS 托管); delete_source=true 归档后清理本地产物。"""
    return archive_session_file(user["user_id"], req.conversation_id, req.path, req.name,
                                delete_source=req.delete_source)


@router.get("/{asset_id}/download-url")
def asset_download_url(asset_id: str, user: dict = Depends(get_current_user)):
    """OSS 直链下载: 返回 presigned URL, 浏览器直连对象存储下载(不经服务端回源)。

    归档入对象存储的资产, 下载与归档同源(都在 OSS); 本地回退模式无直链,
    显式报错引导用 /download 代理下载, 不静默降级。
    """
    row = _get_asset(asset_id, user["user_id"])
    storage = get_object_storage()
    if not storage.is_object_storage:
        raise HTTPException(status_code=400, detail="当前资产存储为本地模式，无 OSS 直链；请用 /download 代理下载")
    url = storage.get_presigned_url(row["object_key"], expires=1800,
                                    download_filename=row["filename"])
    if not url:
        raise HTTPException(status_code=502, detail="对象存储直链生成失败，请稍后重试")
    return {"url": url, "filename": row["filename"], "expires_in": 1800}


@router.get("/{asset_id}/download")
def download_asset_endpoint(asset_id: str, user: dict = Depends(get_current_user)):
    """服务端代理下载(本地存储模式/兼容入口); OSS 直链见 /download-url。"""
    row = _get_asset(asset_id, user["user_id"])
    data = get_object_storage().download_bytes(row["object_key"])
    if data is None:
        raise HTTPException(status_code=404, detail="资产内容不可用")
    media_type = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
    # HTTP 头仅 latin-1 可编码: 文件名统一百分号编码, 并以 RFC 5987 filename* 声明 UTF-8 原名
    quoted = quote(row["filename"])
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}'})


@router.delete("/{asset_id}")
def delete_asset_endpoint(asset_id: str, user: dict = Depends(get_current_user)):
    """删除资产(仅属主)。"""
    row = _get_asset(asset_id, user["user_id"])
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_user_assets WHERE id=%s AND user_id=%s",
                        (asset_id, user["user_id"]))
            conn.commit()
    finally:
        conn.close()
    get_object_storage().delete(row["object_key"])
    return {"success": True}


def _get_asset(asset_id: str, user_id: int) -> dict:
    """按 (asset_id, user_id) 取资产(属主 fail-closed: 他人的资产一律 404)。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, object_key FROM adh_user_assets WHERE id=%s AND user_id=%s",
                (asset_id, user_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    return row


# ═══════════════════════════════════════════════════════════════════
# 工作空间本地产物域(会话文件/磁盘配额/清理) — 仍按工作空间治理
# ═══════════════════════════════════════════════════════════════════


@workspace_router.get("/{workspace_id}/conversations/{conversation_id}/files")
def list_session_files_endpoint(workspace_id: int, conversation_id: int,
                                user: dict = Depends(get_current_user)):
    """列出会话工作区产物文件(供归档选择, 非手填路径)。"""
    authorize_workspace(user, workspace_id)
    return list_session_files(workspace_id, conversation_id, user["user_id"])


@workspace_router.delete("/{workspace_id}/conversations/{conversation_id}/files")
def delete_session_file_endpoint(workspace_id: int, conversation_id: int,
                                 path: str = Query(..., description="工作区相对路径"),
                                 user: dict = Depends(get_current_user)):
    """手动删除会话工作区产物文件(仅属主, 防目录穿越/符号链接逃逸)。

    删除物理文件, 不可恢复; 归档过的资产不受影响(资产是独立副本)。"""
    authorize_workspace(user, workspace_id)
    target, _, _ = _locate_session_file(user["user_id"], conversation_id, path, workspace_id)
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    return {"ok": True, "deleted": target.name}


def list_session_files(workspace_id: int, conversation_id: int, user_id: int) -> list:
    target_root, _, _ = _locate_session_file_root(workspace_id, conversation_id, user_id)
    ws_dir = (target_root / "workspace").resolve()
    if not ws_dir.is_dir():
        return []
    files = []
    for p in sorted(ws_dir.rglob("*")):
        try:
            if p.is_file() and not p.is_symlink():
                files.append({"path": str(p.relative_to(ws_dir)), "size": p.stat().st_size})
        except OSError:
            continue
    return files


def _locate_session_file_root(workspace_id: int, conversation_id: int, user_id: int):
    """定位会话目录根(仅属主); 返回 (session_root, workspace_id, "")。"""
    from backend.modules.mind.execution import session_workspace as sw
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_key, workspace_id FROM adh_agent_sessions "
                "WHERE conversation_id=%s AND user_id=%s AND workspace_id=%s",
                (conversation_id, user_id, workspace_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row.get("session_key"):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    base = sw.workspace_base(create=False)
    return sw.session_paths(base, row["session_key"], row["workspace_id"], create=False), row["workspace_id"], ""


@workspace_router.get("/{workspace_id}/disk-status")
def disk_status_endpoint(workspace_id: int, user: dict = Depends(get_current_user)):
    """工作空间磁盘用量/配额/会话产物清单(LLM disk_status 工具同源)。"""
    authorize_workspace(user, workspace_id)
    return disk_status(workspace_id)


def disk_status(workspace_id: int) -> dict:
    from backend.modules.mind.execution.session_workspace import workspace_disk_usage
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT q.disk_quota_bytes FROM adh_user_workspace_quota q "
                "JOIN adh_workspaces w ON w.owner_id = q.user_id WHERE w.id = %s",
                (workspace_id,))
            quota_row = cur.fetchone()
            cur.execute(
                "SELECT session_key, conversation_id, status, created_at FROM adh_agent_sessions "
                "WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 50", (workspace_id,))
            sessions = cur.fetchall()
    finally:
        conn.close()
    quota = int((quota_row or {}).get("disk_quota_bytes") or 0)
    usage = workspace_disk_usage(workspace_id)
    for s in sessions:
        s["bytes"] = _session_dir_bytes(workspace_id, s.get("session_key") or "")
    return {"workspace_id": workspace_id, "usage_bytes": usage, "quota_bytes": quota,
            "sessions": sessions}


def _session_dir_bytes(workspace_id: int, session_key: str) -> int:
    from backend.modules.mind.execution.session_workspace import session_paths, workspace_base
    import re
    if not re.fullmatch(r"[a-f0-9]{32}", session_key or ""):
        return 0
    try:
        root = session_paths(workspace_base(create=False), session_key, workspace_id, create=False)
    except Exception:  # noqa: BLE001 — 目录缺失/布局异常按 0 计
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


@workspace_router.post("/{workspace_id}/cleanup")
def cleanup_endpoint(workspace_id: int, req: CleanupRequest, user: dict = Depends(get_current_user)):
    """清理本地产物: 候选=已归档来源文件 + 已关闭会话目录(资产在 OSS 不受影响)。

    confirm=false 仅列候选(dry-run); confirm=true 执行删除。
    """
    authorize_workspace(user, workspace_id)
    candidates = _cleanup_candidates(workspace_id)
    if not req.confirm:
        return {"dry_run": True, "candidates": candidates}
    freed = 0
    for c in candidates:
        try:
            if c["kind"] == "file":
                from pathlib import Path
                p = Path(c["path"])
                if p.is_file() and not p.is_symlink():
                    freed += p.stat().st_size
                    p.unlink()
            elif c["kind"] == "session":
                from backend.modules.mind.execution.session_workspace import (
                    session_paths, workspace_base, _remove_session_directory)
                root = session_paths(workspace_base(create=False), c["session_key"], workspace_id, create=False)
                freed += c["bytes"]
                _remove_session_directory(root)
        except Exception as e:  # noqa: BLE001 — 单项失败不阻断整体清理
            logger.warning("cleanup candidate failed: %s (%s)", c, e)
    return {"dry_run": False, "freed_bytes": freed, "cleaned": len(candidates)}


def _cleanup_candidates(workspace_id: int) -> list:
    """已归档来源文件(按资产 source_path 定位) + 已关闭会话目录。"""
    import re
    from pathlib import Path
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, filename, source_path, source_conversation_id FROM adh_user_assets "
                "WHERE origin_workspace_id=%s AND source_conversation_id > 0", (workspace_id,))
            assets = cur.fetchall()
            cur.execute(
                "SELECT session_key, conversation_id, status FROM adh_agent_sessions "
                "WHERE workspace_id=%s", (workspace_id,))
            sessions = cur.fetchall()
    finally:
        conn.close()
    candidates = []
    for a in assets:
        # 归档来源文件(会话 workspace 内)仍存在则列为候选
        try:
            target, _, _ = _locate_session_file(
                int(a["user_id"]), int(a["source_conversation_id"]),
                a.get("source_path") or a.get("filename") or "", workspace_id)
        except (HTTPException, KeyError, TypeError, ValueError):
            continue
        candidates.append({"kind": "file", "path": str(target), "bytes": target.stat().st_size,
                           "conversation_id": a["source_conversation_id"],
                           "filename": a.get("filename") or target.name})
    for s in sessions:
        key = s.get("session_key") or ""
        if s.get("status") != "closed" or not re.fullmatch(r"[a-f0-9]{32}", key):
            continue
        candidates.append({"kind": "session", "session_key": key,
                           "conversation_id": s.get("conversation_id"), "bytes": _session_dir_bytes(workspace_id, key)})
    return candidates
