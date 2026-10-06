"""Workspace Assets API — 工作空间资产清单(OSS 托管, 跨会话共享项目)。

工作空间是用户个人工作站。会话产物经"归档收藏"进入资产清单:
  * 资产本体托管对象存储(OSS/MinIO, 未配置回退本地), 对象 key 为真值;
  * 归档后会话本地产物可基于磁盘配额清理(资产不受影响);
  * LLM 经 assets 工具组感知清单并与用户确认归档/清理。
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

from services.shared.common.auth import get_current_user, authorize_workspace
from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.object_storage import get_object_storage

logger = logging.getLogger(__name__)
router = APIRouter()


class ArchiveRequest(BaseModel):
    name: str
    conversation_id: int
    path: str
    delete_source: bool = False


class CleanupRequest(BaseModel):
    confirm: bool = False


def _locate_session_file(workspace_id: int, conversation_id: int, rel_path: str, user_id: int):
    """定位会话产物文件(仅属主, 防目录穿越/符号链接逃逸)。"""
    from services.datamind.execution import session_workspace as sw
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
    root = sw.session_paths(base, row["session_key"], workspace_id, create=False)
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
    return target


def archive_session_file(workspace_id: int, conversation_id: int, rel_path: str,
                         name: str, user_id: int, delete_source: bool = False) -> dict:
    """归档会话产物到资产清单(供 LLM 工具与 API 共用)。"""
    target = _locate_session_file(workspace_id, conversation_id, rel_path, user_id)
    data = target.read_bytes()
    filename = target.name
    asset_id = uuid.uuid4().hex
    object_key = f"workspaces/{workspace_id}/assets/{asset_id}_{filename}"
    storage = get_object_storage()
    storage.upload_bytes(object_key, data, content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_workspace_assets "
                "(id, workspace_id, name, filename, category, object_key, storage_type, size, "
                " source_conversation_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (asset_id, workspace_id, name or filename, filename, _category(filename),
                 object_key, "object" if storage.is_object_storage else "local", len(data),
                 conversation_id, user_id))
            conn.commit()
    finally:
        conn.close()
    if delete_source:
        try:
            target.unlink()
        except OSError:
            logger.warning("归档成功但删除本地产物失败: %s", target)
    return {"id": asset_id, "name": name or filename, "filename": filename, "size": len(data),
            "object_key": object_key, "source_conversation_id": conversation_id}


def _category(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        return "image"
    if ext in ("sql",):
        return "sql"
    if ext in ("html", "pdf", "docx", "xlsx", "pptx"):
        return "report"
    return "file"


def list_workspace_assets(workspace_id: int) -> list:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, filename, category, size, source_conversation_id, created_by, created_at "
                "FROM adh_workspace_assets WHERE workspace_id=%s ORDER BY created_at DESC",
                (workspace_id,))
            return cur.fetchall()
    finally:
        conn.close()


@router.get("/{workspace_id}/assets")
def list_assets_endpoint(workspace_id: int, user: dict = Depends(get_current_user)):
    """工作空间资产清单(该空间所有会话共享)。"""
    authorize_workspace(user, workspace_id)
    return list_workspace_assets(workspace_id)


@router.post("/{workspace_id}/assets/archive")
def archive_asset_endpoint(workspace_id: int, req: ArchiveRequest, user: dict = Depends(get_current_user)):
    """归档会话产物到资产清单(OSS 托管); delete_source=true 归档后清理本地产物。"""
    authorize_workspace(user, workspace_id)
    return archive_session_file(workspace_id, req.conversation_id, req.path, req.name,
                                user["user_id"], delete_source=req.delete_source)


@router.get("/{workspace_id}/assets/{asset_id}/download-url")
def asset_download_url(workspace_id: int, asset_id: str, user: dict = Depends(get_current_user)):
    """OSS 直链下载: 返回 presigned URL, 浏览器直连对象存储下载(不经服务端回源)。

    归档入对象存储的资产, 下载与归档同源(都在 OSS); 本地回退模式无直链,
    显式报错引导用 /download 代理下载, 不静默降级。
    """
    authorize_workspace(user, workspace_id)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, object_key FROM adh_workspace_assets WHERE id=%s AND workspace_id=%s",
                (asset_id, workspace_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    storage = get_object_storage()
    if not storage.is_object_storage:
        raise HTTPException(status_code=400, detail="当前资产存储为本地模式，无 OSS 直链；请用 /download 代理下载")
    url = storage.get_presigned_url(row["object_key"], expires=1800,
                                    download_filename=row["filename"])
    if not url:
        raise HTTPException(status_code=502, detail="对象存储直链生成失败，请稍后重试")
    return {"url": url, "filename": row["filename"], "expires_in": 1800}


@router.get("/{workspace_id}/assets/{asset_id}/download")
def download_asset_endpoint(workspace_id: int, asset_id: str, user: dict = Depends(get_current_user)):
    """服务端代理下载(本地存储模式/兼容入口); OSS 直链见 /download-url。"""
    authorize_workspace(user, workspace_id)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, object_key FROM adh_workspace_assets WHERE id=%s AND workspace_id=%s",
                (asset_id, workspace_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    data = get_object_storage().download_bytes(row["object_key"])
    if data is None:
        raise HTTPException(status_code=404, detail="资产内容不可用")
    media_type = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
    # HTTP 头仅 latin-1 可编码: 文件名统一百分号编码, 并以 RFC 5987 filename* 声明 UTF-8 原名
    quoted = quote(row["filename"])
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}'})


@router.delete("/{workspace_id}/assets/{asset_id}")
def delete_asset_endpoint(workspace_id: int, asset_id: str, user: dict = Depends(get_current_user)):
    """删除资产(仅属主)。"""
    authorize_workspace(user, workspace_id)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key FROM adh_workspace_assets WHERE id=%s AND workspace_id=%s",
                (asset_id, workspace_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="资产不存在")
            cur.execute("DELETE FROM adh_workspace_assets WHERE id=%s", (asset_id,))
            conn.commit()
    finally:
        conn.close()
    get_object_storage().delete(row["object_key"])
    return {"success": True}


@router.get("/{workspace_id}/conversations/{conversation_id}/files")
def list_session_files_endpoint(workspace_id: int, conversation_id: int,
                               user: dict = Depends(get_current_user)):
    """列出会话工作区产物文件(供归档选择, 非手填路径)。"""
    authorize_workspace(user, workspace_id)
    return list_session_files(workspace_id, conversation_id, user["user_id"])


@router.delete("/{workspace_id}/conversations/{conversation_id}/files")
def delete_session_file_endpoint(workspace_id: int, conversation_id: int,
                                path: str = Query(..., description="工作区相对路径"),
                                user: dict = Depends(get_current_user)):
    """手动删除会话工作区产物文件(仅属主, 防目录穿越/符号链接逃逸)。

    删除物理文件, 不可恢复; 归档过的资产不受影响(资产是独立副本)。"""
    authorize_workspace(user, workspace_id)
    target = _locate_session_file(workspace_id, conversation_id, path, user["user_id"])
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    return {"ok": True, "deleted": target.name}


def list_session_files(workspace_id: int, conversation_id: int, user_id: int) -> list:
    from services.datamind.execution import session_workspace as sw
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_key FROM adh_agent_sessions "
                "WHERE conversation_id=%s AND user_id=%s AND workspace_id=%s",
                (conversation_id, user_id, workspace_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row.get("session_key"):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    base = sw.workspace_base(create=False)
    ws_dir = (sw.session_paths(base, row["session_key"], workspace_id, create=False) / "workspace").resolve()
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


@router.get("/{workspace_id}/disk-status")
def disk_status_endpoint(workspace_id: int, user: dict = Depends(get_current_user)):
    """工作空间磁盘用量/配额/会话产物清单(LLM disk_status 工具同源)。"""
    authorize_workspace(user, workspace_id)
    return disk_status(workspace_id)


def disk_status(workspace_id: int) -> dict:
    from services.datamind.execution.session_workspace import workspace_disk_usage
    from services.authservice.services.role_service import role_service
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
    from services.datamind.execution.session_workspace import session_paths, workspace_base
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


@router.post("/{workspace_id}/cleanup")
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
                from services.datamind.execution.session_workspace import (
                    session_paths, workspace_base, _remove_session_directory)
                root = session_paths(workspace_base(create=False), c["session_key"], workspace_id, create=False)
                freed += c["bytes"]
                _remove_session_directory(root)
        except Exception as e:  # noqa: BLE001 — 单项失败不阻断整体清理
            logger.warning("cleanup candidate failed: %s (%s)", c, e)
    return {"dry_run": False, "freed_bytes": freed, "cleaned": len(candidates)}


def _cleanup_candidates(workspace_id: int) -> list:
    """已归档来源文件 + 已关闭会话目录。"""
    import re
    from pathlib import Path
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, filename, source_conversation_id FROM adh_workspace_assets "
                "WHERE workspace_id=%s AND source_conversation_id > 0", (workspace_id,))
            assets = cur.fetchall()
            cur.execute(
                "SELECT session_key, conversation_id, status FROM adh_agent_sessions "
                "WHERE workspace_id=%s", (workspace_id,))
            sessions = cur.fetchall()
    finally:
        conn.close()
    candidates = []
    for a in assets:
        m = re.match(r"^workspaces/\d+/assets/[0-9a-f]{32}_(.+)$", a.get("object_key") or "")
        if not m:
            continue
        # 归档来源文件(会话 workspace 内)仍存在则列为候选
        try:
            target = _locate_session_file(workspace_id, int(a["source_conversation_id"]), m.group(1), _asset_owner(a))
        except HTTPException:
            continue
        candidates.append({"kind": "file", "path": str(target), "bytes": target.stat().st_size,
                           "conversation_id": a["source_conversation_id"], "filename": m.group(1)})
    for s in sessions:
        key = s.get("session_key") or ""
        if s.get("status") != "closed" or not re.fullmatch(r"[a-f0-9]{32}", key):
            continue
        candidates.append({"kind": "session", "session_key": key,
                           "conversation_id": s.get("conversation_id"), "bytes": _session_dir_bytes(workspace_id, key)})
    return candidates


def _asset_owner(asset: dict) -> int:
    """归档来源文件归属校验用: 取资产归档人。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT created_by FROM adh_workspace_assets WHERE object_key=%s",
                        (asset.get("object_key"),))
            row = cur.fetchone()
            return int((row or {}).get("created_by") or 0)
    finally:
        conn.close()
