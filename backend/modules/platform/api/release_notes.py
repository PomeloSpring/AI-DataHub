"""版本更新公告 + 新手引导状态 API.

Tables:
  - ``adh_release_notes``      公告内容(管理员在「系统配置 → 版本公告」维护)
  - ``adh_user_guide_state``   按用户的引导完成/跳过与公告已读状态

口径:
  * 公告**读取**是登录用户的个人自助能力(与 /api/memory 同口径, 见
    ``backend/common/api_permission.py`` 的 _SELF_ROUTE_PATTERNS), 不按权限码拦;
    只回 ``is_published=1`` 的行。管理域 /api/admin/release-notes* 才require_admin。
  * 引导状态 ``user_id`` **只取服务端身份**, 不信任请求体(护栏 §2 身份 fail-closed)。
  * 状态 key 白名单: 只允许 ``guide:<guideId>`` / ``note:<version>``, 其余一律拒,
    避免脏 key 把状态表写成垃圾场(脏 key 会让自动弹出判定永远命中"已处理")。
  * 引导步骤定义不在库里(事实源是前端 ``lib/onboarding/guides.ts``, 与 DOM 的
    ``data-tour`` 锚点同批演进), 库只存内容文案与用户状态。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.common.auth import get_current_user, require_admin
from backend.common.db import execute_insert, execute_query, execute_write

logger = logging.getLogger(__name__)

router = APIRouter()          # /api/release-notes          登录用户个人域
admin_router = APIRouter()    # /api/admin/release-notes     管理员内容发布域

# ── 常量与校验(与 docker/mysql/release_notes_migration.sql 注释同口径) ────────
STATUSES = ("completed", "skipped", "read")
SECTION_KINDS = ("feature", "improvement", "fix")
_STATE_KEY_RE = re.compile(r"^(?:guide:[a-z][a-z0-9_-]{0,63}|note:[0-9][0-9A-Za-z.\-]{0,31})$")
_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.]+)?$")
_GUIDE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LIST_LIMIT = 50


class GuideStateUpdate(BaseModel):
    key: Optional[str] = None
    status: str = "completed"
    # 批量场景(如"全部标记已读"): 一次落多条, 避免 N 次请求
    keys: Optional[list[str]] = None


class ReleaseNoteUpsert(BaseModel):
    version: str
    title: str
    summary: Optional[str] = ""
    sections: Optional[list[dict]] = None
    guide_key: Optional[str] = ""
    is_major: Optional[int] = 0
    is_published: Optional[int] = 1


# ── 内部工具 ────────────────────────────────────────────────────────────────

def _normalize_sections(raw) -> list[dict]:
    """sections 归一为 ``[{kind, items:[str]}]``。

    DB 里 JSON 列可能回字符串(pymysql 依版本而异), 必须先 loads 再判形状,
    否则前端 ``Object.entries``/``map`` 会把字符串按字符展开。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("[release-notes] sections JSON 解析失败, 按空分组处理: %r", raw[:120])
            return []
    out: list[dict] = []
    for grp in (raw or []):
        if not isinstance(grp, dict):
            continue
        kind = str(grp.get("kind") or "")
        items = [str(x) for x in (grp.get("items") or []) if str(x or "").strip()]
        if not items:
            continue
        # 未知 kind 不静默丢弃: 原样回给前端, 由前端归到"其他"分组展示
        out.append({"kind": kind if kind in SECTION_KINDS else "other", "raw_kind": kind, "items": items})
    return out


def _validate_sections(sections: list[dict] | None) -> list[dict]:
    """写入口校验: 分组 kind 必须是指定三类, 每组至少一条非空文字。"""
    cleaned: list[dict] = []
    for idx, grp in enumerate(sections or []):
        if not isinstance(grp, dict):
            raise HTTPException(400, f"第 {idx + 1} 组更新项格式不正确")
        kind = str(grp.get("kind") or "").strip()
        if kind not in SECTION_KINDS:
            raise HTTPException(400, f"第 {idx + 1} 组的类型不合法（只支持 {'/'.join(SECTION_KINDS)}）")
        items = [str(x).strip() for x in (grp.get("items") or []) if str(x or "").strip()]
        if not items:
            raise HTTPException(400, f"第 {idx + 1} 组（{kind}）还没有任何更新项")
        cleaned.append({"kind": kind, "items": items})
    return cleaned


def _item(row: dict, *, include_meta: bool = False) -> dict:
    """响应组装: sections 一定是数组(字符串先 loads), 前端可直接 map。"""
    data = {
        "id": row["id"],
        "version": row["version"],
        "title": row["title"],
        "summary": row.get("summary") or "",
        "sections": _normalize_sections(row.get("sections")),
        "guide_key": row.get("guide_key") or "",
        "is_major": int(row.get("is_major") or 0),
        "published_at": _iso(row.get("published_at")),
    }
    if include_meta:
        data["is_published"] = int(row.get("is_published") or 0)
        data["updated_by"] = row.get("updated_by") or ""
        data["updated_at"] = _iso(row.get("updated_at"))
    return data


def _iso(val) -> Optional[str]:
    return val.isoformat() if val is not None else None


_SELECT_COLS = ("id, version, title, summary, sections, guide_key, is_major, "
                "is_published, published_at, updated_by, updated_at")


# ── 登录用户个人域 ────────────────────────────────────────────────────────────

@router.get("/")
@router.get("")
def list_release_notes(user: dict = Depends(get_current_user)):
    """已发布公告清单(新→旧)。未发布的草稿只对管理员可见, 不出现在此接口。"""
    rows = execute_query(
        f"SELECT {_SELECT_COLS} FROM adh_release_notes WHERE is_published = 1 "
        f"ORDER BY published_at DESC, id DESC LIMIT {_LIST_LIMIT}"
    ) or []
    items = [_item(r) for r in rows]
    return {"items": items, "latest_version": items[0]["version"] if items else ""}


@router.get("/state")
def get_guide_state(user: dict = Depends(get_current_user)):
    """当前用户的引导/已读状态。身份由服务端解析, 无身份 → 401。"""
    uid = _require_uid(user)
    rows = execute_query(
        "SELECT state_key, status, updated_at FROM adh_user_guide_state WHERE user_id = %s",
        (uid,),
    ) or []
    return {
        "states": {
            r["state_key"]: {"status": r["status"], "updated_at": _iso(r.get("updated_at"))}
            for r in rows
        }
    }


@router.put("/state")
def put_guide_state(req: GuideStateUpdate, user: dict = Depends(get_current_user)):
    """写入/覆盖引导状态(upsert)。只允许合法 key 形态与指定状态值。"""
    uid = _require_uid(user)
    keys = [k for k in ([req.key] if req.key else []) + list(req.keys or []) if k]
    if not keys:
        raise HTTPException(400, "缺少要保存的状态标识")
    if req.status not in STATUSES:
        raise HTTPException(400, f"状态值不合法（只支持 {'/'.join(STATUSES)}）")
    for key in keys:
        if not _STATE_KEY_RE.match(key):
            raise HTTPException(400, "状态标识不合法（只支持 guide:<引导> 或 note:<版本>）")
    sql = (
        "INSERT INTO adh_user_guide_state (user_id, state_key, status) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE status = VALUES(status), updated_at = CURRENT_TIMESTAMP"
    )
    for key in keys:
        execute_write(sql, (uid, key, req.status))
    return {"success": True, "saved": len(keys)}


def _require_uid(user: dict) -> int:
    """身份 fail-closed: 无可信 user_id 一律拒, 不回退成"按所有人/按 0 号用户"处理。"""
    try:
        uid = int((user or {}).get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        raise HTTPException(401, "缺少可信身份，未保存引导状态")
    return uid


# ── 管理员内容发布域 ──────────────────────────────────────────────────────────

@admin_router.get("/")
@admin_router.get("")
def admin_list_notes(admin: dict = Depends(require_admin)):
    """全量公告(含未发布草稿), 供版本公告管理页列表。"""
    rows = execute_query(
        f"SELECT {_SELECT_COLS} FROM adh_release_notes "
        f"ORDER BY published_at DESC, id DESC LIMIT {_LIST_LIMIT}"
    ) or []
    return {"items": [_item(r, include_meta=True) for r in rows]}


@admin_router.post("")   # 前端调的是不带尾斜杠的集合路径(与 brand.py 同口径双注册)
@admin_router.post("/")
def admin_create_note(req: ReleaseNoteUpsert, admin: dict = Depends(require_admin)):
    """新增公告版本(version 唯一)。"""
    version, title = _validate_head(req)
    payload = _validate_body(req)
    dup = execute_query("SELECT id FROM adh_release_notes WHERE version = %s", (version,), fetchone=True)
    if dup:
        raise HTTPException(409, f"版本 {version} 已存在，请直接编辑该公告")
    note_id = execute_insert(
        "INSERT INTO adh_release_notes (version, title, summary, sections, guide_key, is_major, "
        "is_published, published_at, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 1 THEN UTC_TIMESTAMP(6) ELSE NULL END, %s)",
        (version, title, payload["summary"], json.dumps(payload["sections"], ensure_ascii=False),
         payload["guide_key"], payload["is_major"], payload["is_published"], payload["is_published"],
         str(admin.get("username") or "")),
    )
    logger.info("[release-notes] created v%s by admin user_id=%s", version, admin.get("user_id"))
    return {"success": True, "id": note_id}


@admin_router.put("/{note_id}")
def admin_update_note(note_id: int, req: ReleaseNoteUpsert, admin: dict = Depends(require_admin)):
    """编辑公告(整体覆盖内容; version 变更时仍保持唯一)。"""
    version, title = _validate_head(req)
    payload = _validate_body(req)
    if not execute_query("SELECT id FROM adh_release_notes WHERE id = %s", (note_id,), fetchone=True):
        raise HTTPException(404, "公告不存在")
    dup = execute_query("SELECT id FROM adh_release_notes WHERE version = %s AND id <> %s",
                        (version, note_id), fetchone=True)
    if dup:
        raise HTTPException(409, f"版本 {version} 已被另一条公告占用")
    execute_write(
        "UPDATE adh_release_notes SET version=%s, title=%s, summary=%s, sections=%s, guide_key=%s, "
        "is_major=%s, is_published=%s, "
        "published_at=CASE WHEN %s = 1 THEN COALESCE(published_at, UTC_TIMESTAMP(6)) ELSE published_at END, "
        "updated_by=%s WHERE id=%s",
        (version, title, payload["summary"], json.dumps(payload["sections"], ensure_ascii=False),
         payload["guide_key"], payload["is_major"], payload["is_published"], payload["is_published"],
         str(admin.get("username") or ""), note_id),
    )
    return {"success": True, "id": note_id}


@admin_router.delete("/{note_id}")
def admin_delete_note(note_id: int, admin: dict = Depends(require_admin)):
    """删除公告。已读状态是 (user_id, key) 行, 删内容不会误伤其它版本的状态。"""
    if not execute_query("SELECT id FROM adh_release_notes WHERE id = %s", (note_id,), fetchone=True):
        raise HTTPException(404, "公告不存在")
    execute_write("DELETE FROM adh_release_notes WHERE id = %s", (note_id,))
    logger.info("[release-notes] deleted id=%s by admin user_id=%s", note_id, admin.get("user_id"))
    return {"success": True, "id": note_id}


# 状态白名单/正则等校验口径集中在上方常量区, 此处不再散落


def _validate_head(req: ReleaseNoteUpsert) -> tuple[str, str]:
    version = (req.version or "").strip()
    title = (req.title or "").strip()
    if not _VERSION_RE.match(version):
        raise HTTPException(400, "版本号格式不正确（示例：1.2.0）")
    if not title:
        raise HTTPException(400, "请填写版本标题")
    if len(title) > 200:
        raise HTTPException(400, "版本标题过长（上限 200 字）")
    return version, title


def _validate_body(req: ReleaseNoteUpsert) -> dict:
    summary = (req.summary or "").strip()[:500]
    guide_key = (req.guide_key or "").strip()
    if guide_key and not _GUIDE_KEY_RE.match(guide_key):
        raise HTTPException(400, "关联引导标识不合法（请从清单中选择）")
    return {
        "summary": summary,
        "sections": _validate_sections(req.sections),
        "guide_key": guide_key,
        "is_major": 1 if int(req.is_major or 0) else 0,
        "is_published": 1 if int(req.is_published if req.is_published is not None else 1) else 0,
    }
