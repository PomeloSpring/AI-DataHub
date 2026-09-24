"""别名回流建议队列 — 系统自进化闭环的第一条实回路。

语义层编译(plan)产出的 unresolved_terms(被拒近似词 fuzzy_rejected / 未解析词 unresolved)
自动落入 adh_alias_suggestions; 人工审核后通过既有 AS-BOT `alias.approve` 动作写回
对应字典(指标/维度)或本体对象的 aliases, 并触发图谱重建 + 知识库同步(复用 save_draft 联动)。

设计约束:
- 落入/审核全链路 best-effort: 任何失败只记日志, 绝不影响取数主链路。
- 只存业务词与候选(来自 planner 的结构化输出), 不存物理列/SQL。
- 变更执行不另造通道: 审核动作走 AS-BOT 审批(adh_as_bot_role_actions fail-closed 控权)。
"""
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TARGET_TYPES = ("object", "metric", "dimension")


def record_unresolved_terms(datasource_id: int, terms: list[dict]) -> int:
    """把 plan provenance 的 unresolved_terms 落入队列(去重累加 hit_count)。返回落入条数。"""
    if not terms:
        return 0
    from services.shared.common.db import execute_query

    recorded = 0
    for t in terms:
        term = str(t.get("term") or "").strip()[:128]
        if not term:
            continue
        ttype = t.get("type") if t.get("type") in _TARGET_TYPES else "dimension"
        cands = [str(c)[:128] for c in (t.get("candidates") or [])][:5]
        # 候选第一个作为建议挂载目标(可空, 审核时可改)
        target_ref = cands[0] if cands else ""
        reason = t.get("reason") or "unresolved"
        source = "fuzzy_rejected" if reason == "fuzzy_rejected" else "unresolved"
        try:
            execute_query(
                "INSERT INTO adh_alias_suggestions "
                "(term, target_type, target_ref, datasource_id, source, candidates, hit_count) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1) "
                "ON DUPLICATE KEY UPDATE hit_count = hit_count + 1, updated_at = NOW()",
                (term, ttype, target_ref, int(datasource_id or 0), source,
                 json.dumps(cands, ensure_ascii=False)),
            )
            recorded += 1
        except Exception as e:  # noqa: BLE001 — 队列表缺失(未迁移)等不影响主链路
            logger.debug("[AliasSuggest] record %r failed: %s", term, e)
    if recorded:
        logger.info("[AliasSuggest] recorded %d unresolved term suggestion(s) ds=%s",
                    recorded, datasource_id)
    return recorded


def list_pending(limit: int = 50) -> list[dict]:
    from services.shared.common.db import execute_query

    try:
        rows = execute_query(
            "SELECT * FROM adh_alias_suggestions WHERE status = 'pending' "
            "ORDER BY hit_count DESC, updated_at DESC LIMIT %s", (min(limit, 200),))
        for r in rows or []:
            r["candidates"] = _parse_json(r.get("candidates"), [])
        return rows or []
    except Exception as e:  # noqa: BLE001
        logger.warning("[AliasSuggest] list_pending failed: %s", e)
        return []


def get_suggestion(suggestion_id: int) -> Optional[dict]:
    from services.shared.common.db import execute_query

    rows = execute_query("SELECT * FROM adh_alias_suggestions WHERE id = %s",
                         (suggestion_id,))
    if not rows:
        return None
    r = rows[0]
    r["candidates"] = _parse_json(r.get("candidates"), [])
    return r


def approve_suggestion(payload: dict) -> dict:
    """审核通过: 把 term 写回目标(metric/dimension 字典或本体对象)的别名。

    payload: {suggestion_id, term?, target_type, target_ref}
    - metric/dimension: 追加到对应字典行 aliases(JSON)。
    - object: 追加到当前 active 本体模型该对象的 aliases, 经 save_draft 联动重建图谱+同步知识库。
    成功后 suggestion 置 approved; 失败抛出(由 AS-BOT 记 failed 审批结果)。
    """
    sid = int(payload.get("suggestion_id") or 0)
    sug = get_suggestion(sid) if sid else None
    target_type = str(payload.get("target_type") or (sug or {}).get("target_type") or "")
    target_ref = str(payload.get("target_ref") or (sug or {}).get("target_ref") or "").strip()
    term = str(payload.get("term") or (sug or {}).get("term") or "").strip()
    if not term or target_type not in _TARGET_TYPES:
        raise ValueError("alias.approve 参数不完整: 需要 term 与合法 target_type")
    if target_type == "object":
        _approve_object_alias(term, target_ref, (sug or {}).get("datasource_id") or 0)
    else:
        if not target_ref:
            raise ValueError("字典别名回写需要 target_ref(目标指标/维度名)")
        _approve_dict_alias(target_type, term, target_ref)
    if sid:
        _set_status(sid, "approved", payload.get("decided_by"))
    return {"success": True, "term": term, "target_type": target_type,
            "target_ref": target_ref or term}


def reject_suggestion(payload: dict) -> dict:
    sid = int(payload.get("suggestion_id") or 0)
    if not sid:
        raise ValueError("缺少 suggestion_id")
    _set_status(sid, "rejected", payload.get("decided_by"))
    return {"success": True}


# ── 内部写回 ─────────────────────────────────────────────

def _approve_dict_alias(target_type: str, term: str, ref: str) -> None:
    """把 term 追加为指标/维度字典行的别名(幂等, JSON 列整体重写)。"""
    from services.shared.common.db import execute_query

    table = "adh_metrics" if target_type == "metric" else "adh_dimensions"
    rows = execute_query(
        f"SELECT id, aliases FROM {table} WHERE is_active = 1 AND name = %s "
        "LIMIT 1", (ref,))
    if not rows:
        raise ValueError(f"目标字典行不存在: {table}:{ref}")
    aliases = _parse_json(rows[0].get("aliases"), []) or []
    if term not in aliases:
        aliases.append(term)
        execute_query(f"UPDATE {table} SET aliases = %s WHERE id = %s",
                      (json.dumps(aliases, ensure_ascii=False), rows[0]["id"]))


def _approve_object_alias(term: str, obj_ref: str, datasource_id: int) -> None:
    """把 term 追加为 active 本体模型的对象别名, 经 save_draft 联动(图谱+知识库)。"""
    from services.datacatalog.services import ontology_service
    from services.shared.common.db import execute_query

    rows = execute_query(
        "SELECT id FROM adh_ontology_models WHERE datasource_id = %s "
        "AND status = 'active' ORDER BY updated_at DESC LIMIT 1", (datasource_id,))
    if not rows:
        raise ValueError(f"数据源 {datasource_id} 无 active 本体模型, 无法回写对象别名")
    model = ontology_service.get_model(rows[0]["id"])
    doc = json.loads(model.get("json_content") or "{}")
    hit = False
    for o in doc.get("objects") or []:
        if str(o.get("key") or "").strip().lower() == obj_ref.lower():
            aliases = o.get("aliases") or []
            if term not in aliases:
                aliases.append(term)
            o["aliases"] = aliases
            hit = True
            break
    if not hit:
        raise ValueError(f"active 模型中不存在对象: {obj_ref}")
    # save_draft(active 模型) 自带联动: 重算绑定/重建图谱/触发知识库同步
    ontology_service.save_draft(rows[0]["id"], json.dumps(doc, ensure_ascii=False))


def _set_status(sid: int, status: str, decided_by: Any) -> None:
    from services.shared.common.db import execute_query

    try:
        execute_query("UPDATE adh_alias_suggestions SET status = %s, decided_by = %s "
                      "WHERE id = %s", (status, decided_by, sid))
    except Exception as e:  # noqa: BLE001
        logger.warning("[AliasSuggest] set status %s for %s failed: %s", status, sid, e)


def _parse_json(value, fallback):
    if isinstance(value, (list, dict)):
        return value
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback
