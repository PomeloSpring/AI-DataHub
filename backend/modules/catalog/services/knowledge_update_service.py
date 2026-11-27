"""知识更新提议服务：Chat / 人工 → 进化中心待审 → 人用**目标域权限**落库。

为什么需要它（缺口，不是重复建设）
--------------------------------
进化中心此前只能处置「语义缺口」（缺别名/指标/维度/绑定），而业务的真实纠正往往
是"这句话说错了/这个文档要补一段/这类分析该怎么做"——它们落点是**本体对象说明**、
**知识库文档正文**、**分析场景**，用缺口四出口一个都接不住。本模块把这类输入统一
收敛成"提议"（`adh_knowledge_update_proposals`），由人在进化中心逐条确认。

三条不可妥协的纪律
------------------
* **提议不直接改资产**：本模块只写提议表。真正落库由界面调用各域既有通道
  （知识库 `/api/content/knowledge-bases*` 的 `kbdoc:write`、场景 `/api/admin/knowledge*`
  的 `knowledge:manage`、本体 `ontology:save` + 独立 `ontology:publish`）——
  AI 不直写知识库（`kbdoc:write` 的 `ai_access=none`），也不越权发布本体。
* **目标只认业务名**：`target_hint` 存知识库名/文档标题/场景标题/对象业务名，
  **不存任何内部 id**（LLM 拿不到 id 只能猜，security-guardrails §7）；
  具体落到哪一行由人在界面用选择框确认（ui-resource-display §2）。
* **状态机只前进一步**：`pending → applied|rejected|superseded` 用条件 UPDATE 仲裁，
  重复点击不会重复落库，也不会把已处置的提议改回去（宁阻断勿错写）。

本体对象目录只列**业务本体**：系统能力本体由 `system_ontology_catalog.py` +
`refresh_system_ontology.py` 声明式维护，界面改完会被下次刷新覆盖，因此不给入口。
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("catalog.knowledge_update")

TARGET_TYPES = ("kb_doc", "scenario", "ontology_object")
SOURCES = ("chat", "manual", "evolution")
STATUSES = ("pending", "applied", "rejected", "superseded")

MAX_TITLE = 191
MAX_HINT = 191
MAX_RATIONALE = 1000
MAX_NOTE = 500
#: 单条提议正文上限（与知识库单篇 200KB 上限同量级；MEDIUMTEXT 能存，但没人会审 1MB 提议）
MAX_CONTENT_BYTES = 200_000


# ── 小工具（与 evolution_service 同口径，避免两套连接写法）──────────────────

def _conn():
    from backend.common.db.metadata_db import get_metadata_conn
    return get_metadata_conn()


def _q(sql: str, params: tuple = ()) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _w(sql: str, params: tuple = ()) -> int:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = int(cur.rowcount or 0)
        conn.commit()
        return affected
    finally:
        conn.close()


def _insert(sql: str, params: tuple = ()) -> int:
    """INSERT 并返回自增主键。

    不能用 `_w` 的 rowcount 当新行 id：INSERT 的 rowcount 恒为 1，调用方拿着它去
    「标记已处理」会打到 id=1 那条别人的提议上（真踩过：POST /proposals 永远回 id=1）。
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = int(cur.lastrowid or 0)
        conn.commit()
        return new_id
    finally:
        conn.close()


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _assert_choice(value: str, allowed: tuple, field: str) -> str:
    v = str(value or "").strip()
    if v not in allowed:
        raise ValueError(f"{field} 不合法（{value!r}），应为 {list(allowed)}")
    return v


def _row_out(row: dict) -> dict:
    """对外形状（时间转字符串，JSON 列保持对象；不含任何物理/内部标识）。"""
    out = dict(row)
    for key in ("created_at", "updated_at", "decided_at"):
        val = out.get(key)
        out[key] = val.strftime("%Y-%m-%d %H:%M:%S") if hasattr(val, "strftime") else (
            str(val) if val else "")
    out["id"] = int(out.get("id") or 0)
    return out


# ── 提议 CRUD ────────────────────────────────────────────────────────────────

def create_proposal(*, target_type: str, title: str, content: str = "", rationale: str = "",
                    target_hint: str = "", datasource_name: str = "",
                    source: str = "manual", origin_conversation_id: int = 0,
                    origin_message_uuid: str = "") -> dict:
    """写入一条待审提议（只写本表，不碰任何资产）。"""
    ttype = _assert_choice(target_type, TARGET_TYPES, "target_type")
    src = _assert_choice(source, SOURCES, "source")
    title = _clean(title, MAX_TITLE)
    if not title:
        raise ValueError("标题必填（知识库文档标题 / 场景标题 / 对象业务名）")
    text = str(content or "").strip()
    if not text:
        raise ValueError("提议正文必填：请写清建议修改成的业务内容")
    if len(text.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(f"提议正文不能超过 {MAX_CONTENT_BYTES // 1024}KB，请拆条提交")
    new_id = _insert(
        "INSERT INTO adh_knowledge_update_proposals "
        "(source, target_type, title, content, rationale, target_hint, datasource_name, "
        " status, origin_conversation_id, origin_message_uuid) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)",
        (src, ttype, title, text, _clean(rationale, MAX_RATIONALE),
         _clean(target_hint, MAX_HINT), _clean(datasource_name, 128),
         int(origin_conversation_id or 0), _clean(origin_message_uuid, 64)))
    logger.info("[knowledge-update] proposal created id=%s type=%s source=%s",
                new_id, ttype, src)
    return {"id": new_id, "status": "pending"}


def list_proposals(status: str = "", target_type: str = "", limit: int = 100,
                   source: str = "") -> dict:
    """待审/已处置清单（稳定序：created_at DESC, id DESC），附各状态计数。"""
    where, params = [], []
    if status:
        where.append("status=%s")
        params.append(_assert_choice(status, STATUSES, "status"))
    if target_type:
        where.append("target_type=%s")
        params.append(_assert_choice(target_type, TARGET_TYPES, "target_type"))
    if source:
        where.append("source=%s")
        params.append(_assert_choice(source, SOURCES, "source"))
    sql = ("SELECT id, source, target_type, title, content, rationale, target_hint, "
           "datasource_name, status, applied_ref, decided_by, decided_at, decided_note, "
           "origin_conversation_id, origin_message_uuid, created_at, updated_at "
           "FROM adh_knowledge_update_proposals")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    rows = _q(sql, tuple(params) + (max(1, min(int(limit or 100), 500)),))
    counts = {s: 0 for s in STATUSES}
    for r in _q("SELECT status, COUNT(*) AS n FROM adh_knowledge_update_proposals "
                "GROUP BY status"):
        if r.get("status") in counts:
            counts[r["status"]] = int(r.get("n") or 0)
    return {"items": [_row_out(r) for r in rows], "counts": counts}


def get_proposal(proposal_id: int) -> dict | None:
    rows = _q("SELECT * FROM adh_knowledge_update_proposals WHERE id=%s", (int(proposal_id),))
    return _row_out(rows[0]) if rows else None


def mark_applied(proposal_id: int, applied_ref: str, actor: str) -> dict:
    """人已在目标域落库成功 → 标记 applied。

    条件 UPDATE 只放行 `pending`：重复提交（已 applied/rejected）不覆盖既有结论，
    回 `changed=False` 让前端如实提示"这条已经不是待处理状态"。
    ``applied_ref`` 服务层再校验一次（与决策服务同口径）：空落点就是假闭环，
    历史空落点记录不伪造补齐。
    """
    if not _clean(applied_ref, MAX_HINT):
        raise ValueError("applied_ref 必填：须先在目标域（知识库文档/分析场景/本体对象）"
                         "真正落库，再回写落点业务名；没有落库不得标记已处理")
    affected = _w(
        "UPDATE adh_knowledge_update_proposals "
        "SET status='applied', applied_ref=%s, decided_by=%s, decided_at=NOW() "
        "WHERE id=%s AND status='pending'",
        (_clean(applied_ref, MAX_HINT), _clean(actor, 64), int(proposal_id)))
    if affected:
        return {"changed": True, "status": "applied"}
    row = get_proposal(proposal_id)
    if not row:
        raise ValueError("提议不存在")
    return {"changed": False, "status": row.get("status") or "",
            "message": f"该提议当前状态是 {row.get('status')!r}，未重复标记；"
                       "如需重开请另建一条提供新证据的提议"}


def reject_proposal(proposal_id: int, actor: str, note: str = "") -> dict:
    """人判定不收：留痕（谁、为什么），不删行 —— 反复被驳回的信号本身是数据。

    驳回必须说明理由（服务层再校验，与决策服务同口径）：无理由驳回无法审计。"""
    if not _clean(note, MAX_NOTE):
        raise ValueError("驳回必须说明理由（note）：被驳的信号本身是数据，无理由驳回无法审计")
    affected = _w(
        "UPDATE adh_knowledge_update_proposals "
        "SET status='rejected', decided_by=%s, decided_at=NOW(), decided_note=%s "
        "WHERE id=%s AND status='pending'",
        (_clean(actor, 64), _clean(note, MAX_NOTE), int(proposal_id)))
    if affected:
        return {"changed": True, "status": "rejected"}
    row = get_proposal(proposal_id)
    if not row:
        raise ValueError("提议不存在")
    return {"changed": False, "status": row.get("status") or "",
            "message": f"该提议当前状态是 {row.get('status')!r}，只有待处理状态可以驳回"}


# ── 本体对象目录（标量更新的选择框 + 当前值）────────────────────────────────

def ontology_object_catalog(datasource_name: str = "") -> list[dict]:
    """业务本体对象 + 当前标量值：``[{key, display_name, description, aliases,
    model_id, model_name, datasource_names}]``。

    * 只列 `kind='business'`：系统能力本体由声明目录维护（界面改会被刷新覆盖）；
    * 当前值取「草案优先、生效兜底」（与本体建模页同一基线），避免拿对象投影表的旧值；
    * `datasource_name` 传了就按模型落站过滤（业务本体是跨源的，按 deploy_sites 判）。
    """
    from backend.core.scenario_access import subject_catalog
    from backend.modules.catalog.services import ontology_service

    ds_filter = _clean(datasource_name, 128)
    models: dict[int, dict] = {}
    docs: dict[int, list] = {}

    def _model_meta(model_id: int) -> dict:
        if model_id in models:
            return models[model_id]
        rows = _q("SELECT id, name, kind, datasource_id, deploy_sites FROM adh_ontology_models "
                  "WHERE id=%s", (model_id,))
        row = rows[0] if rows else {}
        raw = row.get("deploy_sites")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "[]")
            except (TypeError, ValueError):
                raw = []
        names = {str(d.get("datasource") or "").strip() for d in (raw or [])
                 if isinstance(d, dict) and d.get("datasource")}
        ds_id = int(row.get("datasource_id") or 0)
        if ds_id:
            ds_rows = _q("SELECT name FROM adh_datasources WHERE id=%s", (ds_id,))
            if ds_rows:
                names.add(str(ds_rows[0].get("name") or "").strip())
        models[model_id] = {"name": str(row.get("name") or ""),
                            "kind": str(row.get("kind") or ""),
                            "datasource_names": sorted(n for n in names if n)}
        return models[model_id]

    def _objects(model_id: int) -> list:
        if model_id in docs:
            return docs[model_id]
        try:
            model = ontology_service.get_model(model_id) or {}
            base = (ontology_service.get_draft(model_id).get("draft")
                    or model.get("json_content") or "{}")
            doc = json.loads(base)
            objs = doc.get("objects") if isinstance(doc, dict) else []
            docs[model_id] = [o for o in (objs or []) if isinstance(o, dict)]
        except Exception as e:  # noqa: BLE001 —— 目录读不到要可诊断，不静默空清单
            logger.warning("[knowledge-update] 读模型 %s 正文失败: %s", model_id, e)
            docs[model_id] = []
        return docs[model_id]

    out: list[dict] = []
    for c in subject_catalog():
        if (c.get("kind") or "").lower() != "business":
            continue
        model_id = int(c.get("model_id") or 0)
        if not model_id:
            continue
        meta = _model_meta(model_id)
        if ds_filter and ds_filter not in meta["datasource_names"]:
            continue
        key = str(c.get("key") or "")
        obj = next((o for o in _objects(model_id)
                    if str(o.get("key") or "").strip().lower() == key.lower()), None)
        if obj is None:
            continue
        aliases = obj.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a for a in aliases.replace("，", ",").split(",") if a.strip()]
        out.append({
            "key": key,
            "display_name": str(obj.get("display_name") or c.get("display_name") or key),
            "description": str(obj.get("description") or ""),
            "aliases": [str(a).strip() for a in aliases if str(a).strip()],
            "model_id": model_id,
            "model_name": meta["name"] or str(c.get("model_name") or ""),
            "datasource_names": meta["datasource_names"],
        })
    out.sort(key=lambda x: (x["model_name"], x["display_name"]))
    return out