"""知识库文档 API — 内容中心里的「知识库目录树查看/编辑」。

挂载: ``/api/content/knowledge-bases``（backend/app/main.py，platform 组，
与内容中心同前缀但独立 router 文件：文档树属知识库域，不掺进 articles 的编排语义）

职责边界（用户 2026-11-04 拍板）：
- **知识库管理仍在系统配置**（``/api/knowledge-bases*`` + ``kb:read``/``kb:manage``，
  建库/绑 AS-BOT/配凭据），本文件一行都不碰；
- 这里只做**库内部**的文档：目录树查看 + 单篇编辑，入口是内容中心的独立菜单
  ``content:kb-docs``。

权限门控（唯一裁决点仍是 API 权限中间件）：

=================  ==========================================  =========================
权限码              api_pattern / method                        端点
=================  ==========================================  =========================
``kbdoc:read``     GET    /api/content/knowledge-bases*        库清单 / 目录树 / 读正文
``kbdoc:write``    POST,PUT /api/content/knowledge-bases*      保存 / 新建文档 / 新建目录
=================  ==========================================  =========================

为什么不复用 ``kb:read``/``kb:manage``：那两个的 pattern 是 ``/api/knowledge-bases*``，
挂在上面等于让"能看知识库配置（含类型与连接参数）"的人自动能改云端语料，反之为了
给文档编辑权还得连带勾上库管理权 —— 两件事必须能分开勾。也不复用 ``article:manage``：
门户内容的审核发布链条与语料维护是两套人（编辑/审核人 vs 数据/知识负责人）。

**读接口不放进 ``_OPEN_READ_ROUTES``**：与内容中心同口径，"能看"也要走权限码，
否则管理员收不回入口。

写入的并发与副作用（都在服务层，这里只负责把它们如实回给前端）：
- 保存一篇 = 上传新件 + 删旧件（云端无原地更新接口），**只提交改动的那一篇**；
- 带 ``expected_sha256`` 的乐观并发：别人先改过就 409；
- 本体上云的派生文档（带 model-version-stamp）一律 403 拒改，指回本体建模。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.common.auth import get_current_user
from backend.modules.platform.services import kb_docs_service as svc
from backend.modules.platform.services import content_kb_sync_service as sync_svc

logger = logging.getLogger(__name__)

router = APIRouter()


class DocSavePayload(BaseModel):
    content: str = ""
    expected_sha256: Optional[str] = None


class DocCreatePayload(BaseModel):
    title: str = ""
    content: str = ""
    parent_id: Optional[str] = None


class FolderCreatePayload(BaseModel):
    title: str = ""
    parent_id: Optional[str] = None


class SyncFromArticlePayload(BaseModel):
    """同步请求体：只收内容的**业务标识**（slug 或 id），不收任何身份字段。"""
    article: str = ""
    parent_id: Optional[str] = None


def _actor(user: dict) -> str:
    return str(user.get("username") or user.get("user_id") or "")


def _fail(exc: svc.KbDocError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _audit(user: dict, request: Request, action: str, kb_name: str, detail: str) -> None:
    """云端语料的每一次写入都留痕（best-effort，不影响主链路）。"""
    try:
        from backend.common.auth import log_audit
        log_audit(int(user.get("user_id") or 0), _actor(user), action,
                  target_type="knowledge_base_doc", target_id=0,
                  detail=f"{kb_name}: {detail}",
                  ip_address=(request.client.host if request.client else ""),
                  module="content")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kb-docs] 审计写入失败（不影响主链路）: %s", exc)


# ── 读 ────────────────────────────────────────────────────────────────────────

@router.get("/knowledge-bases")
def read_bases(user: dict = Depends(get_current_user)):
    """可浏览的知识库清单（含不支持文档树的类型，并写明为什么不支持）。"""
    try:
        return {"items": svc.list_bases()}
    except svc.KbDocError as e:
        raise _fail(e) from None


@router.get("/knowledge-bases/{kb}/tree")
def read_tree(kb: str):
    """目录树。云端失败原样 502，不回空树把"查不到"说成"库里没文档"。"""
    try:
        return svc.tree(kb)
    except svc.KbDocError as e:
        raise _fail(e) from None


@router.get("/knowledge-bases/{kb}/documents/{source_id}")
def read_document(kb: str, source_id: str):
    """单篇正文 + 基线哈希（保存时带回来做乐观并发）+ 只读原因。"""
    try:
        return svc.read_doc(kb, source_id)
    except svc.KbDocError as e:
        raise _fail(e) from None


# ── 写（kbdoc:write）──────────────────────────────────────────────────────────

@router.put("/knowledge-bases/{kb}/documents/{source_id}")
def save_document(kb: str, source_id: str, payload: DocSavePayload, request: Request,
                  user: dict = Depends(get_current_user)):
    """保存**这一篇**（其余文档不动）。空正文/超体积/含凭据均拒；派生文档 403。"""
    try:
        out = svc.save_doc(kb, source_id, payload.content,
                           str(payload.expected_sha256 or ""), _actor(user))
        _audit(user, request, "content.kbdoc.save", kb,
               f"保存 {out.get('title')}（{source_id} → {out.get('source_id')}）")
        return out
    except svc.KbDocError as e:
        raise _fail(e) from None


@router.post("/knowledge-bases/{kb}/documents")
def create_document(kb: str, payload: DocCreatePayload, request: Request,
                    user: dict = Depends(get_current_user)):
    try:
        out = svc.create_doc(kb, payload.title, payload.content,
                             str(payload.parent_id or ""), _actor(user))
        _audit(user, request, "content.kbdoc.create", kb, f"新建文档 {out.get('title')}")
        return out
    except svc.KbDocError as e:
        raise _fail(e) from None


@router.post("/knowledge-bases/{kb}/folders")
def create_folder(kb: str, payload: FolderCreatePayload, request: Request,
                  user: dict = Depends(get_current_user)):
    """新建目录。移动/改名/删除本批未开放（破坏性且云端无回收站），页面上显式说明。"""
    try:
        out = svc.create_folder(kb, payload.title, str(payload.parent_id or ""), _actor(user))
        _audit(user, request, "content.kbdoc.mkdir", kb, f"新建目录 {out.get('title')}")
        return out
    except svc.KbDocError as e:
        raise _fail(e) from None


@router.post("/knowledge-bases/{kb}/sync")
def sync_from_article(kb: str, payload: SyncFromArticlePayload, request: Request,
                      user: dict = Depends(get_current_user)):
    """把一篇**已发布**内容同步进本知识库（更新同一篇，不新建副本）。

    为什么挂在知识库前缀下而不是内容前缀：这一步写的是**语料**，裁决码必须是
    ``kbdoc:write``；放进 ``/api/content/articles*`` 会让“能编辑内容”的人自动拿到
    写云端语料的权限（另造第二套裁决，违反权限裁决点唯一）。

    发布（``article:publish``）与同步（``kbdoc:write``）是两个动作：前端在发布成功后
    另发本请求，失败各自显式提示（“已发布，但语料同步失败…”），不合并成一个
    “看着一步到位”的端点。
    """
    try:
        out = sync_svc.sync_to_kb(payload.article, kb,
                                  parent_id=str(payload.parent_id or ""), actor=_actor(user))
        _audit(user, request, "content.kbdoc.sync", kb,
               f"同步 {out.get('doc_title')} ← 内容 {payload.article}"
               + (f"；提醒：{'；'.join(out['warnings'])}" if out.get("warnings") else ""))
        return out
    except sync_svc.SyncError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None
    except svc.KbDocError as e:
        raise _fail(e) from None
