"""Knowledge Base Management API — Support multiple knowledge bases with various source types.

Supports:
- QMind (Qoder 云端知识库/笔记本):列表与检索均来自 qmind CLI,
  通过 `GET /knowledge-bases/qmind/notebooks` 实时检索、`POST /knowledge-bases/qmind/import`
  将选定的 notebook 落地为可被 AS-BOT 绑定的知识库行(kb_type='qmind')。
- Local data directory
- Vector databases (Doris, Milvus, Pinecone, etc.)
- Cloud RAG services (Aliyun, Tencent, Baidu, etc.)
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from backend.common.auth import get_current_user, require_admin

from backend.common.db import get_metadata_conn

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _invalidate_kb_config_cache(reason: str) -> None:
    """知识库写入口同批失效轮次起点的配置缓存。

    缓存的只是 AS-BOT 提示词里的知识库清单（id/name/kb_type，见
    `as_bots.load_knowledge_bases`）；检索命中的文档内容永远实时从源读取。知识库
    改名/停用/删除后若不清理，用户会在提示词里看到旧名称（下一轮自然回源）。
    """
    from backend.common import as_bot_config_cache
    as_bot_config_cache.invalidate(reason)


# 阻塞型 CLI/DB 调用统一放到线程池,避免阻塞事件循环
_to_thread = asyncio.to_thread

# ── 接入配置的凭据纪律（护栏 §7）─────────────────────────────
#: `source_config` 里的凭据类键：一律**不得入库、不得回显**。
#: 历史缺陷：百炼类配置把 `api_key` 明文写进 JSON 列，而 list/get 又把它**原样回显给
#: admin 前端**（连非 admin 走工作空间可见集时也带着）。本批起：写入即拒、读取即掩。
#: 凭据真值只从环境变量读（`backend/.env` 的 DASHSCOPE_API_KEY，与 QMIND_TOKEN 同纪律）。
SECRET_CONFIG_KEYS = ("api_key", "apikey", "secret", "token", "password", "passwd",
                      "access_key", "accesskey", "credential_value")
#: 回显给前端的掩码标记（非空即表示库里已配过凭据；存量旧行迁移期仍可能含真值，所以读取必须过掩码）
SECRET_CONFIGURED_MARK = "_secrets_configured"


def _is_secret_key(key) -> bool:
    k = str(key or "").strip().lower()
    return any(s in k for s in SECRET_CONFIG_KEYS)


def _reject_incoming_secrets(cfg: dict) -> dict:
    """写入前拒掉凭据键：让用户知道密钥该去哪儿，而不是静默丢弃他刚填的值。"""
    bad = sorted({str(k) for k in (cfg or {}) if _is_secret_key(k) and str(cfg[k]).strip()})
    if bad:
        raise HTTPException(status_code=400, detail=(
            f"知识库接入配置不允许保存密钥字段（{'、'.join(bad)}）。"
            "凭据请写入 backend/.env（百炼用 DASHSCOPE_API_KEY），配置里只填知识库 ID / "
            "业务空间 ID，或用 credential_ref 指向环境变量名。"
        ))
    return dict(cfg or {})


def _mask_config(raw) -> dict:
    """出参形状：非密项原样，凭据键只说“已配置”（值不回显，含 admin）。"""
    cfg = raw if isinstance(raw, dict) else _load_cfg(raw)
    out = {k: v for k, v in (cfg or {}).items() if not _is_secret_key(k)}
    held = sorted({str(k) for k in (cfg or {}) if _is_secret_key(k) and cfg.get(k)})
    if held:
        out[SECRET_CONFIGURED_MARK] = held
    return out


def _load_cfg(value) -> dict:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        got = json.loads(value)
        return got if isinstance(got, dict) else {}
    except (TypeError, ValueError):
        return {}


def _merge_config_keeping_secrets(existing: dict, incoming: dict) -> dict:
    """更新时保留库内已有凭据键（入参不接受凭据，所以旧值不得因为“没回传”而被静默抹掉）。

    存量旧行可能仍含明文 api_key：编辑其他字段不应把它弄丢（弄丢就是一个能用的配置
    突然不可用）；清理它由单独的凭据迁移动作做，不在这里顺手删。
    """
    merged = dict(existing or {})
    for key, val in (incoming or {}).items():
        if _is_secret_key(key):
            continue      # 入参本已在 _reject_incoming_secrets 拒掉，这里再兜一层
        merged[key] = val
    return merged


def _retrieval_view(row: dict) -> dict:
    """该库**能不能被检索到**与原因（AS-BOT 绑定页据此灰显）。

    清单必须与检索分派、绑定校验同源（`rag.providers`）：三处各写一套 kb_type 白名单
    就是"配置页能选、检索时静默过滤"的成因。原因原文来自服务端，前端不自己编措辞。
    """
    from backend.modules.mind.rag.providers import describe

    info = describe({"name": row.get("name") or "", "kb_type": row.get("kb_type") or "",
                     "cfg": _load_cfg(row.get("source_config"))})
    return {"readable": bool(info.get("readable")),
            "provider": str(info.get("provider") or ""),
            "corpus_origin": str(info.get("corpus_origin") or ""),
            "reason": str(info.get("reason") or "")}


# ── Pydantic Models ────────────────────────────────────────────────

class KnowledgeBasePayload(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_workspace_binding(cls, data):
        if isinstance(data, dict) and {"workspace_id", "workspace_ids"}.intersection(data):
            raise ValueError("知识库使用范围请在 AS-BOT 中绑定，不再支持工作空间绑定")
        return data


class KnowledgeBaseCreate(KnowledgeBasePayload):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    kb_type: str = Field(..., pattern="^(local|vector_db|cloud_rag|qmind)$")
    source_config: dict = Field(default_factory=dict)


class KnowledgeBaseUpdate(KnowledgeBasePayload):
    name: Optional[str] = None
    description: Optional[str] = None
    source_config: Optional[dict] = None
    status: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    source_config: Optional[dict] = None
    status: str
    document_count: int
    chunk_count: int
    last_sync_at: Optional[str]
    created_at: str
    updated_at: str
    #: 检索通道可用性（与检索分派/绑定校验同源，前端不得再写一份 kb_type 白名单）
    retrieval: dict = Field(default_factory=dict)


# ── Table Initialization ───────────────────────────────────────────

def _ensure_table():
    """Create knowledge_bases table if it doesn't exist."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS adh_knowledge_bases (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    kb_type VARCHAR(20) NOT NULL DEFAULT 'local',
                    source_config JSON,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    document_count INT NOT NULL DEFAULT 0,
                    chunk_count INT NOT NULL DEFAULT 0,
                    last_sync_at DATETIME,
                    workspace_ids JSON,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_kb_type (kb_type),
                    INDEX idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            conn.commit()
    finally:
        conn.close()


class QMindImportRequest(BaseModel):
    notebook_ids: Optional[List[str]] = None  # 为空表示导入全部检索到的 notebook


# Initialize table on module load
_ensure_table()


# ── API Endpoints ──────────────────────────────────────────────────

@router.get("/knowledge-bases", response_model=List[KnowledgeBaseResponse], response_model_exclude_none=True)
async def list_knowledge_bases(
    kb_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    workspace_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user),
):
    """系统管理目录或从当前身份可用 AS-BOT 派生的只读资源目录。"""
    allowed_ids = None
    if workspace_id is not None:
        from backend.modules.mind.execution.as_bots import visible_resource_ids
        allowed_ids = set(visible_resource_ids(user, workspace_id, "knowledge_base_ids"))
        if not allowed_ids:
            return []
    elif user.get("role") != "admin":
        raise HTTPException(403, "系统知识库目录仅管理员可访问，请选择已授权工作空间")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT * FROM adh_knowledge_bases WHERE 1=1"
            params = []

            if kb_type:
                sql += " AND kb_type = %s"
                params.append(kb_type)

            if status:
                sql += " AND status = %s"
                params.append(status)

            if allowed_ids is not None:
                sql += " AND status='active' AND id IN (" + ','.join(['%s'] * len(allowed_ids)) + ")"
                params.extend(sorted(allowed_ids))
            sql += " ORDER BY created_at DESC"
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                if allowed_ids is not None and (row["id"] not in allowed_ids or row["status"] != "active"):
                    continue

                results.append(KnowledgeBaseResponse(
                    id=row["id"],
                    name=row["name"],
                    description=row.get("description") or "",
                    kb_type=row["kb_type"],
                    retrieval=_retrieval_view(row),
                    source_config=_mask_config(row.get("source_config")) if user.get("role") == "admin" else None,
                    status=row["status"],
                    document_count=row.get("document_count") or 0,
                    chunk_count=row.get("chunk_count") or 0,
                    last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                    created_at=row["created_at"].isoformat(),
                    updated_at=row["updated_at"].isoformat(),
                ))

            return results
    finally:
        conn.close()


@router.get("/knowledge-bases/qmind/notebooks", dependencies=[Depends(require_admin)])
async def list_qmind_notebooks():
    """从 Qoder 实时检索 QMind 知识库(笔记本)列表.

    返回每个 notebook 及其是否已导入为本地知识库(imported / kb_id),
    供后台展示“可绑定的 Qoder 知识库”。qmind CLI 不可用时返回空列表。
    """
    from backend.modules.mind.rag.qmind_retriever import list_notebooks

    notebooks = await _to_thread(list_notebooks)
    # 已导入的 notebook_id → 本地 kb 行
    imported: dict[str, int] = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, source_config FROM adh_knowledge_bases WHERE kb_type = 'qmind'"
            )
            for row in cursor.fetchall():
                try:
                    cfg = json.loads(row.get("source_config") or "{}")
                except (json.JSONDecodeError, TypeError):
                    cfg = {}
                nb = cfg.get("notebook_id")
                if nb:
                    imported[nb] = row["id"]
    finally:
        conn.close()

    return {
        "available": bool(notebooks),
        "notebooks": [
            {**nb, "imported": nb["notebook_id"] in imported, "kb_id": imported.get(nb["notebook_id"])}
            for nb in notebooks
        ],
    }


@router.post("/knowledge-bases/qmind/import", dependencies=[Depends(require_admin)])
async def import_qmind_notebooks(request: QMindImportRequest):
    """将选定的 Qoder QMind notebook 导入(落地)为可绑定的知识库(kb_type='qmind').

    以 notebook_id 为幂等键:已存在则刷新标题/描述,不存在则新建。未指定
    notebook_ids 时导入检索到的全部 notebook。
    """
    from backend.modules.mind.rag.qmind_retriever import list_notebooks

    notebooks = await _to_thread(list_notebooks)
    if not notebooks:
        raise HTTPException(status_code=502, detail="未能从 Qoder 检索到知识库(qmind CLI 不可用或未登录)")
    wanted = set(request.notebook_ids or [])
    selected = [n for n in notebooks if not wanted or n["notebook_id"] in wanted]
    if not selected:
        raise HTTPException(status_code=400, detail="选定的 notebook 不在检索结果中")

    created, updated = 0, 0
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            for nb in selected:
                cursor.execute(
                    "SELECT id FROM adh_knowledge_bases WHERE kb_type='qmind' AND "
                    "JSON_UNQUOTE(JSON_EXTRACT(source_config, '$.notebook_id')) = %s",
                    (nb["notebook_id"],),
                )
                row = cursor.fetchone()
                cfg = json.dumps(
                    {"notebook_id": nb["notebook_id"], "org_id": nb["org_id"],
                     "title": nb["title"], "top_k": 5},
                    ensure_ascii=False,
                )
                if row:
                    cursor.execute(
                        "UPDATE adh_knowledge_bases SET name=%s, description=%s, source_config=%s "
                        "WHERE id=%s",
                        (nb["title"], nb["description"], cfg, row["id"]),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """INSERT INTO adh_knowledge_bases
                           (name, description, kb_type, source_config, status, workspace_ids)
                           VALUES (%s, %s, 'qmind', %s, 'active', '[]')""",
                        (nb["title"], nb["description"], cfg),
                    )
                    created += 1
            conn.commit()
    finally:
        conn.close()

    if created or updated:
        _invalidate_kb_config_cache(f"qmind_import created={created} updated={updated}")

    return {
        "message": f"已导入 {created} 个、刷新 {updated} 个 Qoder 知识库",
        "created": created,
        "updated": updated,
    }


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
async def get_knowledge_base(kb_id: int):
    """Get a specific knowledge base by ID."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                retrieval=_retrieval_view(row),
                source_config=_mask_config(row.get("source_config")),
                status=row["status"],
                document_count=row.get("document_count") or 0,
                chunk_count=row.get("chunk_count") or 0,
                last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.post("/knowledge-bases/{kb_id}/probe", dependencies=[Depends(require_admin)])
async def probe_knowledge_base(kb_id: int):
    """「测试连接」：按 provider 实调一次连通性自检，成功/失败原样回报。

    返回体只含可读结论与错因，**不回显接入配置与凭据**（护栏 §7）；未接入检索
    通道的类型明确说“本批未接入”，不返回一个看起来能用的 ok。
    """
    from backend.modules.mind.rag.providers import provider_for

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="知识库不存在")

    kb = {"id": row["id"], "name": row.get("name") or "", "kb_type": row.get("kb_type") or "",
          "cfg": _load_cfg(row.get("source_config"))}
    provider = provider_for(kb)
    if provider is None:
        return {"ok": False, "knowledge_base": kb["name"],
                "reason": f"「{kb['kb_type']}」类型的检索通道本批未接入，无法测试"}
    if not provider.supports(kb):
        return {"ok": False, "knowledge_base": kb["name"],
                "reason": "该库的厂商/配置尚未接入检索通道，请核对云厂商与知识库 ID"}
    try:
        ok, err = await _to_thread(provider.probe, kb)
    except Exception as exc:  # noqa: BLE001 — 自检异常必须原样告知，不得假装连通
        logger.warning("[kb] probe failed name=%s: %s", kb["name"], exc)
        return {"ok": False, "knowledge_base": kb["name"],
                "reason": f"自检异常: {exc}"[:300]}
    return {"ok": bool(ok), "knowledge_base": kb["name"], **({"reason": err} if err else {})}


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
async def create_knowledge_base(request: KnowledgeBaseCreate):
    """Create a new knowledge base."""
    cfg = _reject_incoming_secrets(request.source_config)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO adh_knowledge_bases (name, description, kb_type, source_config, status, workspace_ids)
                   VALUES (%s, %s, %s, %s, 'active', '[]')""",
                (
                    request.name,
                    request.description,
                    request.kb_type,
                    json.dumps(cfg),
                )
            )
            conn.commit()
            kb_id = cursor.lastrowid
            _invalidate_kb_config_cache(f"create_knowledge_base id={kb_id}")

            # Fetch the created record
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                retrieval=_retrieval_view(row),
                source_config=_mask_config(row.get("source_config")),
                status=row["status"],
                document_count=0,
                chunk_count=0,
                last_sync_at=None,
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.put("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
async def update_knowledge_base(kb_id: int, request: KnowledgeBaseUpdate):
    """Update a knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            # 存在性检查（顺带取回现有配置：入参不接受凭据，更新时必须保留库内已有凭据键，
            # 不能因为前端拿到的是掩码形状、回传时没带该字段就把一个能用的配置抹掉）
            cursor.execute("SELECT id, source_config FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            existing = cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="知识库不存在")
            incoming_cfg = (_reject_incoming_secrets(request.source_config)
                            if request.source_config is not None else None)

            # Build update SQL dynamically
            updates = []
            params = []

            if request.name is not None:
                updates.append("name = %s")
                params.append(request.name)

            if request.description is not None:
                updates.append("description = %s")
                params.append(request.description)

            if incoming_cfg is not None:
                updates.append("source_config = %s")
                params.append(json.dumps(_merge_config_keeping_secrets(
                    _load_cfg(existing.get("source_config")), incoming_cfg)))

            if request.status is not None:
                updates.append("status = %s")
                params.append(request.status)

            if updates:
                sql = f"UPDATE adh_knowledge_bases SET {', '.join(updates)} WHERE id = %s"
                params.append(kb_id)
                cursor.execute(sql, params)
                conn.commit()
                # name/status 均在缓存的清单字段里（停用行会从不命中集剔除）
                _invalidate_kb_config_cache(f"update_knowledge_base id={kb_id}")

            # Return updated record
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                retrieval=_retrieval_view(row),
                source_config=_mask_config(row.get("source_config")),
                status=row["status"],
                document_count=row.get("document_count") or 0,
                chunk_count=row.get("chunk_count") or 0,
                last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.delete("/knowledge-bases/{kb_id}", dependencies=[Depends(require_admin)])
async def delete_knowledge_base(kb_id: int):
    """Delete a knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            cursor.execute("DELETE FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            conn.commit()
            _invalidate_kb_config_cache(f"delete_knowledge_base id={kb_id}")

            return {"message": f'已删除知识库 "{row["name"]}"'}
    finally:
        conn.close()


@router.post("/knowledge-bases/{kb_id}/sync", dependencies=[Depends(require_admin)])
async def sync_knowledge_base(kb_id: int):
    """Sync a knowledge base (triggers background sync process)."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            kb_type = row["kb_type"]
            source_config = json.loads(row.get("source_config") or "{}")

            # TODO: Implement actual sync logic based on kb_type
            # For now, just update the last_sync_at timestamp
            cursor.execute(
                "UPDATE adh_knowledge_bases SET last_sync_at = NOW() WHERE id = %s",
                (kb_id,)
            )
            conn.commit()

            return {
                "message": f'正在同步知识库 "{row["name"]}"',
                "kb_type": kb_type,
                "status": "syncing",
            }
    finally:
        conn.close()


@router.get("/knowledge-bases/{kb_id}/documents", dependencies=[Depends(require_admin)])
async def list_knowledge_base_documents(
    kb_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """知识库的文档清单（云端 qmind 库直接回 notebook 里的 source 列表）。

    历史缺陷(2026-11-04 修): 这里曾 SELECT `adh_knowledge_chunks` 的
    `knowledge_base_id / title / doc_type / source` —— **那些列根本不存在**(该表只有
    document_id/chunk_index/content/...), 报错被 `except Exception` 吞成
    `documents=[], total=0`。于是"库里有没有文档"永远回答"没有", 页面看着正常而
    数据是假的(no-silent-degradation §1)。另外 `adh_knowledge_documents` 是张**没有
    知识库关联列**的孤儿表(全库零行、无任何写入方), 不能拿来充当"某库的文档"。

    现在的口径:
      * `qmind` → 走云端 `source list`(唯一真值源), 失败原样报错, 不回空清单;
      * 其他类型(local/vector_db/cloud_rag) → 显式 409 说明"本端点给不出该类型的
        文档清单", 而不是回一份恒空的列表。
    """
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT name, kb_type, status, source_config, qmind_notebook_id "
                "FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            kb = cursor.fetchone()
    finally:
        conn.close()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    kb_type = str(kb.get("kb_type") or "")
    if kb_type != "qmind":
        raise HTTPException(
            status_code=409,
            detail=(f"「{kb.get('name')}」的类型是 {kb_type}，平台元数据里没有它的文档清单"
                    "（adh_knowledge_documents 无知识库关联列且无写入方）。"
                    "这里不做『看起来能用、其实永远为空』的列表 —— 请在源系统查看该库的文件，"
                    "或改用云端知识库（qmind）"))

    from backend.modules.mind.rag.qmind_retriever import (
        QMindCliError, build_source_tree, list_sources,
    )
    cfg = {}
    try:
        cfg = json.loads(kb.get("source_config") or "{}")
    except (TypeError, ValueError):
        cfg = {}
    nb = str(kb.get("qmind_notebook_id") or "").strip() or str(cfg.get("notebook_id") or "").strip()
    if not nb:
        raise HTTPException(status_code=409,
                            detail=f"知识库「{kb.get('name')}」还没有绑定云端 notebook，"
                                   "请先在系统配置「知识库」里从 Qoder 检索导入")
    try:
        items = await _to_thread(list_sources, nb)
    except QMindCliError as e:
        # 失败就说失败: 空清单会被读成"这个库没有文档"
        raise HTTPException(status_code=502, detail=f"读取云端文档清单失败：{e}") from None

    roots, docs_total, _folders, _flat = build_source_tree(items)
    flat_nodes = _flatten_nodes(roots)
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    start = (page - 1) * page_size
    return {
        "knowledge_base": kb.get("name") or "",
        "documents": flat_nodes[start:start + page_size],
        "total": docs_total,
        "page": page,
        "page_size": page_size,
    }


def _flatten_nodes(nodes: list[dict]) -> list[dict]:
    """树 → 按层级顺序的扁平清单（分页要在扁平序列上做, 否则一页里可能一个文档都没有）。"""
    out: list[dict] = []
    for n in nodes:
        if not n.get("is_dir"):
            out.append({k: v for k, v in n.items() if k != "children"})
        kids = n.get("children") or []
        if kids:
            out.extend(_flatten_nodes(kids))
    return out
