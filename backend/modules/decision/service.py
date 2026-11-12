"""决策建议服务层：资产 CRUD、发布门禁、实例读写与对外投影。

分层与 gov/viz 同构（api → service → common.db），无 ORM。三条硬纪律：

1. **草案/发布两段式**：`save_draft` 只落 draft 并返回 `validation_warnings`
   （不阻断，让配置的人能先存半成品）；`publish` 才硬阻断 —— 口径核对不上、
   建议文案缺失、站点越界都拒绝发布（宁阻断勿让半截口径上线）。
2. **派生列只由发布重算**：`required_metrics` / `bound_object_keys` 由 `tree_doc`
   算出，任何入口都不接受外部写入（事实源与派生视图不并列可编辑）。
3. **对外投影只回业务名**：树详情的 `tree_doc` 本身在契约层就拒物理名
   （`models._reject_physical_ref`），实例投影再过一次出站收口
   （`outbound_guard.find_sensitive_hits`），命中即抛，不静默清洗。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from backend.common.db import execute_query, execute_write
from backend.modules.decision import dictionary as dic
from backend.modules.decision import engine
from backend.modules.decision.models import (
    SEVERITIES, DecisionLeaf, DecisionTreeDoc,
)

logger = logging.getLogger(__name__)

TREE_COLUMNS = (
    "id, tree_key, name, description, workspace_id, status, tree_doc, required_metrics, "
    "bound_object_keys, apply_sites, schedule_cron, timezone, notify_channel_id, version, "
    "created_by, updated_by, published_by, published_at, is_active, created_at, updated_at"
)

_JSON_COLS = ("tree_doc", "required_metrics", "bound_object_keys", "apply_sites")

# tree_key / advice_key 形状：只允许标识符字符（它们会出现在 URL 与引用里，
# 不接受中文/空格/路径片段，避免与路由和 fnmatch 权限模式撞车）
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class DecisionError(ValueError):
    """业务校验失败（路由层转 400/409）。message 必须能解释给用户。"""

    def __init__(self, message: str, code: str = "DECISION_INVALID", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_obj(val: Any, default: Any = None) -> Any:
    if val is None or isinstance(val, (dict, list)):
        return val if val is not None else default
    if isinstance(val, str):
        if not val.strip():
            return default
        try:
            return json.loads(val)
        except ValueError:
            # 存坏 JSON 不能当"没有配置"——那会让半条口径悄悄上线（§1 不静默）
            logger.error("[decision] JSON 列解析失败: %s", val[:120])
            raise DecisionError("库里有一条决策配置的 JSON 已损坏，请先修复该树配置后重试",
                                "DECISION_CORRUPT_DOC", 409)
    return default


def _iso_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k in ("created_at", "updated_at", "published_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return rows


# ══════════════════════════════════════════════════════════════════
# 决策树：读
# ══════════════════════════════════════════════════════════════════

def _decode_json_cols(row: dict) -> dict:
    """一行树的 JSON 列归一（库里存字符串，前端拿到必须是对象/数组）。"""
    for col in _JSON_COLS:
        if col in row:
            row[col] = _json_obj(row.get(col), [] if col != "tree_doc" else {})
    return row


def list_trees(workspace_id: int = 0, status: str = "", include_retired: bool = False) -> dict:
    """清单：显示用 name，不显裸 id（ui-resource-display §1）；id 只进 title 供排查。"""
    where, params = ["1=1"], []
    if int(workspace_id or 0) > 0:
        where.append("(workspace_id = %s OR workspace_id = 0)")
        params.append(int(workspace_id))
    if status:
        where.append("status = %s")
        params.append(status)
    elif not include_retired:
        where.append("status <> 'retired'")

    rows = execute_query(
        f"SELECT {TREE_COLUMNS} FROM adh_decision_trees "
        f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC", params) or []
    return {"items": _iso_rows([_decode_json_cols(r) for r in rows]), "total": len(rows)}


def get_tree(tree_key: str) -> Optional[dict]:
    row = execute_query(
        f"SELECT {TREE_COLUMNS} FROM adh_decision_trees WHERE tree_key = %s",
        (tree_key,), fetchone=True)
    if not row:
        return None
    return _iso_rows([_decode_json_cols(row)])[0]


def get_tree_by_id(tree_id: int) -> Optional[dict]:
    """按自增 id 取树（定时出口拿的是 Beat 传的 args，不是 tree_key）。"""
    if int(tree_id or 0) <= 0:
        return None
    row = execute_query(
        f"SELECT {TREE_COLUMNS} FROM adh_decision_trees WHERE id = %s",
        (int(tree_id),), fetchone=True)
    return _iso_rows([_decode_json_cols(row)])[0] if row else None


def scheduled_tree_rows() -> list[dict]:
    """配了 Cron 且已发布启用的树（Beat 第五个调度源的唯一读取口径）。

    只读调度三字段 + 身份列：定时预警只跑 active 的树，草案永不被调度消费
    （与评估入口同一口径，不在这里开第二个口子）。
    """
    return _iso_rows(execute_query(
        "SELECT id, tree_key, name, workspace_id, schedule_cron, timezone, notify_channel_id "
        "FROM adh_decision_trees WHERE status = 'active' AND is_active = 1 "
        "AND schedule_cron IS NOT NULL AND schedule_cron <> '' ORDER BY id") or [])


def require_tree(tree_key: str) -> dict:
    row = get_tree(tree_key)
    if not row:
        raise DecisionError(f"决策树「{tree_key}」不存在", "TREE_NOT_FOUND", 404)
    return row


# ══════════════════════════════════════════════════════════════════
# 校验（保存=告警，发布=硬阻断）
# ══════════════════════════════════════════════════════════════════

def validate_doc(doc: DecisionTreeDoc, *, datasource_id: int = 0,
                 advice_index: dict | None = None) -> tuple[list[dict], list[dict]]:
    """返回 ``(errors, warnings)``。

    errors：结构/引用**必然**导致求值不可信的问题（建议文案不存在、字典名不存在）；
    warnings：可以带着继续编辑的问题（字典行缺归属、时间窗未给）。

    字典解析用 `datasource_id`=0 的全量视图：决策树是**口径资产**，跨站共享，
    不能因为编辑器恰好连在某个源上就把别的站的口径判成不存在。
    """
    index = dic.load_dictionary(0)
    advice_index = advice_index if advice_index is not None else advice_keys_index()
    errors: list[dict] = []
    warnings: list[dict] = []

    def check(term: str, kind: str, where: str) -> None:
        term = str(term or "").strip()
        if not term:
            return
        hit, miss = dic.resolve(term, kind, index)
        if miss:
            errors.append({"where": where, "term": term, "type": kind, "reason": miss.reason,
                           "candidates": miss.candidates,
                           "message": (f"「{term}」无法在{'指标' if kind == 'metric' else '维度'}字典中确认"
                                       f"（{miss.reason}）"
                                       + (f"；近似候选: {miss.candidates}" if miss.candidates else ""))})
            return
        if kind == "metric" and not hit.object_key:
            warnings.append({"where": where, "term": term, "type": kind,
                             "message": f"指标「{hit.name}」的字典行还没有归属对象，发布前请补口径"})

    def walk(node, path: str) -> None:
        here = f"{path}/{node.label}" if node.label else path
        if isinstance(node, DecisionLeaf):
            check(node.when.metric, "metric", here)
            if node.when.threshold_ref:
                check(node.when.threshold_ref, "metric", here)
            if node.when.dimension:
                check(node.when.dimension, "dimension", here)
            if node.when.time_column:
                check(node.when.time_column, "dimension", here)
            if not node.when.time_window and not node.when.time_column:
                warnings.append({"where": here, "term": node.when.metric, "type": "window",
                                 "message": "条件没有相对时间窗，判定将使用数据源的默认口径（结果不可复现）"})
            if node.advice_key not in advice_index:
                errors.append({"where": here, "term": node.advice_key, "type": "advice",
                               "candidates": [],
                               "message": f"建议文案「{node.advice_key}」不存在，请先在文案库登记"})
            if node.severity and node.severity not in SEVERITIES:
                errors.append({"where": here, "term": node.severity, "type": "severity",
                               "candidates": list(SEVERITIES),
                               "message": f"严重级「{node.severity}」无效"})
            return
        if not node.children:
            errors.append({"where": here, "term": node.label, "type": "branch",
                           "candidates": [], "message": "分支下没有任何子节点"})
        for child in node.children:
            walk(child, here)

    walk(doc.root, "root")
    return errors, warnings


def advice_keys_index() -> dict[str, dict]:
    rows = execute_query(
        "SELECT advice_key, action, rationale, severity, kb_refs, is_active "
        "FROM adh_decision_advice") or []
    out = {}
    for r in rows:
        r["kb_refs"] = _json_obj(r.get("kb_refs"), [])
        out[str(r["advice_key"])] = r
    return out


# ══════════════════════════════════════════════════════════════════
# 决策树：写（草案 / 发布 / 下架 / 删除）
# ══════════════════════════════════════════════════════════════════

def save_draft(data: dict, user: dict) -> dict:
    """新建或更新草案。不阻断（半成品可存），但返回 validation_errors/warnings。"""
    tree_key = str(data.get("tree_key") or "").strip()
    name = str(data.get("name") or "").strip()
    if not tree_key:
        raise DecisionError("tree_key 不能为空（它是决策树的稳定标识）")
    if not name:
        raise DecisionError("决策树名称不能为空")
    if not _KEY_RE.match(tree_key):
        raise DecisionError("tree_key 只能由字母/数字/短横线/下划线组成")

    doc_raw = data.get("tree_doc")
    doc, err = engine.load_doc(doc_raw if not isinstance(doc_raw, str) else doc_raw)
    if err:
        raise DecisionError(err, "DECISION_DOC_INVALID")

    errors, warnings = validate_doc(doc)
    metrics, dims = engine.tree_required_names({"tree_doc": doc})
    object_keys = _object_keys_for(metrics + dims)
    workspace_id = int(data.get("workspace_id") or 0)
    uid = int((user or {}).get("user_id") or 0)
    apply_sites = _normalize_sites(data.get("apply_sites"))

    existing = execute_query("SELECT id, status, version FROM adh_decision_trees WHERE tree_key = %s",
                             (tree_key,), fetchone=True)
    doc_json = json.dumps(doc.model_dump(mode="json"), ensure_ascii=False)
    if existing:
        execute_write(
            "UPDATE adh_decision_trees SET name=%s, description=%s, workspace_id=%s, tree_doc=%s, "
            "apply_sites=%s, schedule_cron=%s, timezone=%s, notify_channel_id=%s, "
            "required_metrics=%s, bound_object_keys=%s, updated_by=%s "
            "WHERE tree_key=%s",
            (name, str(data.get("description") or ""), workspace_id, doc_json,
             json.dumps(apply_sites, ensure_ascii=False),
             str(data.get("schedule_cron") or "").strip(),
             str(data.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
             int(data.get("notify_channel_id") or 0),
             json.dumps(metrics, ensure_ascii=False),
             json.dumps(object_keys, ensure_ascii=False), uid, tree_key))
    else:
        execute_write(
            "INSERT INTO adh_decision_trees (tree_key, name, description, workspace_id, status, "
            "tree_doc, apply_sites, schedule_cron, timezone, notify_channel_id, required_metrics, "
            "bound_object_keys, version, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
            (tree_key, name, str(data.get("description") or ""), workspace_id, doc_json,
             json.dumps(apply_sites, ensure_ascii=False),
             str(data.get("schedule_cron") or "").strip(),
             str(data.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
             int(data.get("notify_channel_id") or 0),
             json.dumps(metrics, ensure_ascii=False),
             json.dumps(object_keys, ensure_ascii=False), uid, uid))

    return {"tree_key": tree_key, "status": (existing or {}).get("status") or "draft",
            "saved": True, "validation_errors": errors, "validation_warnings": warnings}


def publish(tree_key: str, user: dict) -> dict:
    """发布：硬门禁（口径核对不上就不给上线），发布后 version+1 且 status=active。"""
    row = require_tree(tree_key)
    doc, err = engine.load_doc(row["tree_doc"])
    if err:
        raise DecisionError(err, "DECISION_DOC_INVALID")
    errors, warnings = validate_doc(doc)
    if errors:
        raise DecisionError(
            "决策树不能发布：" + "；".join(e["message"] for e in errors[:5]),
            "DECISION_PUBLISH_BLOCKED", 409)
    if not int(row.get("is_active") or 1):
        raise DecisionError("决策树已停用，请先恢复启用再发布", "TREE_INACTIVE", 409)

    metrics, dims = engine.tree_required_names({"tree_doc": doc})
    object_keys = _object_keys_for(metrics + dims)
    # 悬空对象引用硬阻断（与本体 activate 同口径，ontology-modeling §4）：
    # 条件里的指标挂在一个已不存在的对象上 = 这棵树注定取不到数，
    # 让它上线只会批量产出 partial/skipped 的假建议。
    dangling = [k for k in object_keys if k not in _existing_object_keys(object_keys)]
    if dangling:
        raise DecisionError(
            "决策树不能发布：下列本体对象已不存在，条件口径悬空（请在本体建模恢复或改绑）："
            + "、".join(dangling[:8]) + (" 等" if len(dangling) > 8 else ""),
            "DECISION_DANGLING_OBJECT", 409)
    uid = int((user or {}).get("user_id") or 0)
    new_version = int(row.get("version") or 0) + 1
    execute_write(
        "UPDATE adh_decision_trees SET status='active', version=%s, required_metrics=%s, "
        "bound_object_keys=%s, published_by=%s, published_at=%s, updated_by=%s WHERE tree_key=%s",
        (new_version, json.dumps(metrics, ensure_ascii=False),
         json.dumps(object_keys, ensure_ascii=False), uid, _now(), uid, tree_key))
    _invalidate_cache(tree_key)
    return {"tree_key": tree_key, "status": "active", "version": new_version,
            "validation_warnings": warnings}


def set_status(tree_key: str, status: str, user: dict) -> dict:
    if status not in ("retired", "active"):
        raise DecisionError("只能下架（retired）或恢复（active）")
    row = require_tree(tree_key)
    if status == "active" and row["status"] == "draft":
        raise DecisionError("草案要走发布流程才会生效，不能直接置为 active", "DRAFT_NOT_PUBLISHED", 409)
    execute_write("UPDATE adh_decision_trees SET status=%s, updated_by=%s WHERE tree_key=%s",
                  (status, int((user or {}).get("user_id") or 0), tree_key))
    _invalidate_cache(tree_key)
    return {"tree_key": tree_key, "status": status}


def delete_tree(tree_key: str, user: dict) -> dict:
    require_tree(tree_key)
    # 实例不级联删：历史建议要能说清"当时按哪棵树判的"（tree_key/version 已冗余在行上）
    execute_write("DELETE FROM adh_decision_trees WHERE tree_key=%s", (tree_key,))
    _invalidate_cache(tree_key)
    return {"tree_key": tree_key, "deleted": True}


def _object_keys_for(names: list[str]) -> list[str]:
    """这些业务名挂在哪些本体对象上（发布门禁查悬空的依据）。"""
    index = dic.load_dictionary(0)
    out: list[str] = []
    for term in names:
        for kind in ("metric", "dimension"):
            hit, _ = dic.resolve(term, kind, index)
            if hit and hit.object_key and hit.object_key not in out:
                out.append(hit.object_key)
                break
    return out


def _existing_object_keys(keys: list[str]) -> set[str]:
    """本体里真实存在（启用中）的对象 key —— 判定口径与 metrics_service 一致。"""
    if not keys:
        return set()
    ph = ",".join(["%s"] * len(keys))
    rows = execute_query(
        f"SELECT DISTINCT object_key FROM adh_ontology_objects "
        f"WHERE is_active = 1 AND object_key IN ({ph})", tuple(keys)) or []
    return {str(r.get("object_key") or "").strip() for r in rows if r.get("object_key")}


def _normalize_sites(raw: Any) -> list[dict]:
    items = raw if isinstance(raw, list) else (_json_obj(raw, []) or [])
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"site_code": it.strip(), "datasource": ""})
            continue
        code = str((it or {}).get("site_code") or "").strip()
        if code:
            out.append({"site_code": code, "datasource": str((it or {}).get("datasource") or "").strip()})
    return out


# ══════════════════════════════════════════════════════════════════
# 建议文案库
# ══════════════════════════════════════════════════════════════════

def list_advice(workspace_id: int = 0, include_inactive: bool = False) -> dict:
    where, params = ["1=1"], []
    if int(workspace_id or 0) > 0:
        where.append("(workspace_id = %s OR workspace_id = 0)")
        params.append(int(workspace_id))
    if not include_inactive:
        where.append("is_active = 1")
    rows = execute_query(
        "SELECT id, advice_key, action, rationale, severity, kb_refs, workspace_id, is_active, "
        "created_at, updated_at FROM adh_decision_advice "
        f"WHERE {' AND '.join(where)} ORDER BY action", params) or []
    for r in rows:
        r["kb_refs"] = _json_obj(r.get("kb_refs"), [])
    return {"items": _iso_rows(rows), "total": len(rows)}


def save_advice(data: dict, user: dict) -> dict:
    key = str(data.get("advice_key") or "").strip()
    action = str(data.get("action") or "").strip()
    if not key or not _KEY_RE.match(key):
        raise DecisionError("advice_key 不能为空，且只能由字母/数字/短横线/下划线组成")
    if not action:
        raise DecisionError("建议动作正文不能为空")
    severity = str(data.get("severity") or "medium").strip()
    if severity not in SEVERITIES:
        raise DecisionError(f"严重级「{severity}」无效（仅 {'|'.join(SEVERITIES)}）")
    kb_refs = data.get("kb_refs") if isinstance(data.get("kb_refs"), list) else _json_obj(data.get("kb_refs"), [])
    uid = int((user or {}).get("user_id") or 0)
    workspace_id = int(data.get("workspace_id") or 0)
    existing = execute_query("SELECT id FROM adh_decision_advice WHERE advice_key=%s", (key,), fetchone=True)
    if existing:
        execute_write(
            "UPDATE adh_decision_advice SET action=%s, rationale=%s, severity=%s, kb_refs=%s, "
            "workspace_id=%s, is_active=%s, updated_by=%s WHERE advice_key=%s",
            (action, str(data.get("rationale") or ""), severity,
             json.dumps(kb_refs or [], ensure_ascii=False), workspace_id,
             1 if data.get("is_active", True) else 0, uid, key))
    else:
        execute_write(
            "INSERT INTO adh_decision_advice (advice_key, action, rationale, severity, kb_refs, "
            "workspace_id, is_active, created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (key, action, str(data.get("rationale") or ""), severity,
             json.dumps(kb_refs or [], ensure_ascii=False), workspace_id,
             1 if data.get("is_active", True) else 0, uid, uid))
    return {"advice_key": key, "saved": True}


def delete_advice(advice_key: str) -> dict:
    used = execute_query(
        "SELECT tree_key FROM adh_decision_trees WHERE JSON_SEARCH("
        "JSON_EXTRACT(tree_doc, '$.root'), 'one', %s, NULL, "
        "'$**.advice_key', '$*.children.**.advice_key') IS NOT NULL",
        (advice_key,)) or []
    if used:
        raise DecisionError(
            f"建议文案「{advice_key}」仍被这些决策树引用，不能删："
            f"{[u['tree_key'] for u in used[:5]]}；请先改树或下架该树",
            "ADVICE_IN_USE", 409)
    execute_write("DELETE FROM adh_decision_advice WHERE advice_key=%s", (advice_key,))
    return {"advice_key": advice_key, "deleted": True}


# ══════════════════════════════════════════════════════════════════
# 评估与实例
# ══════════════════════════════════════════════════════════════════

def active_trees(workspace_id: int = 0) -> list[dict]:
    """参与评估的树：只有 active + is_active=1 出现（草案永不被消费）。"""
    where, params = ["status = 'active'", "is_active = 1"], []
    if int(workspace_id or 0) > 0:
        where.append("(workspace_id = %s OR workspace_id = 0)")
        params.append(int(workspace_id))
    rows = execute_query(
        f"SELECT {TREE_COLUMNS} FROM adh_decision_trees WHERE {' AND '.join(where)} "
        "ORDER BY name", params) or []
    return [_decode_json_cols(r) for r in rows]


def evaluate(tree_key: str, ctx: engine.EvalContext, *, persist: bool = True) -> dict:
    """求值一棵树并落实例（full 模式同一时间桶内走缓存，不反复打库）。"""
    row = require_tree(tree_key)
    outcome = _evaluate_cached(row, ctx)
    run = record_run(row, outcome, ctx, persist=persist)
    return project_run(run, outcome)


def evaluate_all(workspace_id: int, ctx: engine.EvalContext, *, persist: bool = True) -> dict:
    """按工作空间批量求值（决策中心「全部重算」/ 定时预警 / Chat 回合出口共用）。

    返回里 `skipped` 与 `errors` 都保留原因，不折叠成"没有建议"（§1）。
    """
    items, errored = [], []
    for row in active_trees(workspace_id):
        try:
            outcome = _evaluate_cached(row, ctx)
        except Exception as exc:  # noqa: BLE001 — 一棵树出错不能带走整批，但原因必须回传
            logger.exception("[decision] 求值异常 tree=%s", row.get("tree_key"))
            errored.append({"tree_key": row.get("tree_key"), "reason": f"求值异常（{type(exc).__name__}）"})
            continue
        run = record_run(row, outcome, ctx, persist=persist)
        items.append(project_run(run, outcome))
    hits = [i for i in items if i.get("verdict") == "hit"]
    return {"items": items, "hit_count": len(hits), "evaluated": len(items), "errors": errored}


# full 模式的结果缓存粒度：(tree_key, version, site, 时间桶)。
# 为什么要缓存：一轮会话里同一棵树可能被多个出口（看板控件 + Chat）重复求值，
# 而每次求值都是一次语义层取数（回合耗时对本平台很敏感）。15 分钟粒度的代价是
# “新落的数据最多 15 分钟后才反映到建议”，这比每轮重取数便宜且可解释。
# turn 模式**不缓存**：它的输入是本轮自己的结果，缓存等于把上一轮的数据当本轮用。
_EVAL_TTL_SECONDS = 900


def _evaluate_cached(tree_row: dict, ctx: engine.EvalContext):
    if ctx.mode != "turn" and ctx.conversation_id == 0:
        cache = _eval_cache()
        key = _eval_cache_key(tree_row, ctx)
        if cache is not None and key:
            hit = cache.get(key)
            if isinstance(hit, dict) and hit.get("verdict"):
                return engine.DecisionOutcome.model_validate(hit)
        outcome = engine.evaluate_tree(tree_row, ctx)
        # skipped 不缓存：下次可能就能评了（授权/字典修好了），缓存会把临时态当结论
        if cache is not None and key and outcome.verdict != "skipped":
            try:
                cache.set(key, outcome.model_dump(mode="json"), ttl=_EVAL_TTL_SECONDS)
            except Exception as exc:  # noqa: BLE001 — 缓存写失败不影响已算出的结论
                logger.warning("[decision] 评估结果写入缓存失败: %s", exc)
        return outcome
    return engine.evaluate_tree(tree_row, ctx)


def _eval_cache():
    try:
        from backend.common.cache import get_cache
        return get_cache("decision")
    except Exception as exc:  # noqa: BLE001 — Redis 不可用时候选降级为无缓存，不是无建议
        logger.warning("[decision] 缓存层不可用（本次不缓存）: %s", exc)
        return None


def _eval_cache_key(tree_row: dict, ctx: engine.EvalContext) -> str:
    tree_key = str(tree_row.get("tree_key") or "")
    if not tree_key:
        return ""
    bucket = int(datetime.now().timestamp() // _EVAL_TTL_SECONDS)
    names = sorted(_json_obj(tree_row.get("required_metrics"), []) or [])
    digest = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:12]
    # 看板附加条件也进键：否则「带筛选」与「不带筛选」共用一条缓存条目，
    # 就是把全量数字当成筛选后的结论（静默出错数）
    fs = hashlib.sha1(ctx.filter_signature().encode("utf-8")).hexdigest()[:8]
    return f"eval:{tree_key}:{int(tree_row.get('version') or 0)}:{ctx.site_code or '-'}:{digest}:{fs}:{bucket}"


def record_run(tree_row: dict, outcome, ctx: engine.EvalContext, *, persist: bool = True) -> dict:
    advice_rows = _advice_for(outcome.advice_keys)
    snapshot = advice_rows
    run = {
        "tree_id": int(tree_row.get("id") or 0),
        "tree_key": str(tree_row.get("tree_key") or ""),
        "tree_version": int(tree_row.get("version") or 0),
        "trigger_type": ctx.resolved_trigger(),
        "user_id": int(ctx.user_id or 0),
        "workspace_id": int(ctx.workspace_id or 0),
        "site_code": ctx.site_code or "",
        "conversation_id": int(getattr(ctx, "conversation_id", 0) or 0),
        "message_uuid": str(getattr(ctx, "message_uuid", "") or ""),
        "message_ordinal": int(getattr(ctx, "message_ordinal", -1) or -1),
        "run_key": str(getattr(ctx, "run_key", "") or ""),
        "verdict": outcome.verdict,
        "verdict_reason": outcome.reason or "",
        "evidence": outcome.evidence,
        "hit_path": outcome.hit_path,
        "advice_snapshot": snapshot,
        "elapsed_ms": int(outcome.elapsed_ms or 0),
        "created_at": _now(),
    }
    if not persist:
        return run
    execute_write(
        "INSERT INTO adh_decision_runs (tree_id, tree_key, tree_version, trigger_type, user_id, "
        "workspace_id, site_code, conversation_id, message_uuid, message_ordinal, run_key, "
        "evidence, hit_path, advice_snapshot, verdict, verdict_reason, elapsed_ms, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (run["tree_id"], run["tree_key"], run["tree_version"], run["trigger_type"],
         run["user_id"], run["workspace_id"], run["site_code"],
         run["conversation_id"], run["message_uuid"], run["message_ordinal"], run["run_key"],
         json.dumps(run["evidence"], ensure_ascii=False, default=str),
         json.dumps(run["hit_path"], ensure_ascii=False),
         json.dumps(run["advice_snapshot"], ensure_ascii=False, default=str),
         run["verdict"], run["verdict_reason"], run["elapsed_ms"], run["created_at"]))
    return run


def _advice_for(keys: list[str]) -> list[dict]:
    if not keys:
        return []
    ph = ",".join(["%s"] * len(keys))
    rows = execute_query(
        f"SELECT advice_key, action, rationale, severity, kb_refs FROM adh_decision_advice "
        f"WHERE advice_key IN ({ph})", keys) or []
    for r in rows:
        r["kb_refs"] = _json_obj(r.get("kb_refs"), [])
    # 保持叶子出现顺序（谁先命中谁在前），并如实标出库里已不存在的引用
    by_key = {str(r["advice_key"]): r for r in rows}
    out = []
    for k in keys:
        hit = by_key.get(k)
        if hit:
            out.append(hit)
        else:
            # 文案被删了不能整个建议消失：回一条显式说明（快照只兜底，缺口要可见）
            out.append({"advice_key": k, "action": "", "severity": "medium",
                        "note": f"建议文案「{k}」已从文案库删除，请联系治理负责人补登记"})
    return out


_RUN_COLUMNS = (
    "id, tree_id, tree_key, tree_version, trigger_type, user_id, workspace_id, "
    "site_code, conversation_id, message_uuid, message_ordinal, run_key, evidence, hit_path, "
    "advice_snapshot, verdict, verdict_reason, elapsed_ms, created_at"
)


def dispatched_by_run_key(run_key: str) -> bool:
    """定时出口幂等：同一墙钟分钟已落过实例就不再重投（与 sync_task 同构）。

    为什么拿实例表当幂等位而不是拿一个 Redis 键：预警要发的就是"这一分钟的
    命中条数"，实例行就是那份事实本身；缓存抹了最多重发一次，而建一张
    只记 run_key 的新表就是第二个事实源。
    """
    if not run_key:
        return False
    row = execute_query(
        "SELECT 1 AS ok FROM adh_decision_runs WHERE run_key = %s LIMIT 1",
        (run_key,), fetchone=True)
    return bool(row)


def list_runs(workspace_id: int = 0, tree_key: str = "", verdict: str = "",
              page: int = 1, size: int = 20) -> dict:
    where, params = ["1=1"], []
    if int(workspace_id or 0) > 0:
        where.append("workspace_id = %s")
        params.append(int(workspace_id))
    if tree_key:
        where.append("tree_key = %s")
        params.append(tree_key)
    if verdict:
        where.append("verdict = %s")
        params.append(verdict)
    total = (execute_query(
        f"SELECT COUNT(*) AS c FROM adh_decision_runs WHERE {' AND '.join(where)}",
        params, fetchone=True) or {}).get("c", 0)
    offset = max(0, (int(page or 1) - 1) * int(size or 20))
    rows = execute_query(
        f"SELECT {_RUN_COLUMNS} FROM adh_decision_runs "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC LIMIT {int(size or 20)} OFFSET {offset}", params) or []
    for r in rows:
        for col in ("evidence", "hit_path", "advice_snapshot"):
            r[col] = _json_obj(r.get(col), [])
    return {"items": [_project_row_shape(r) for r in _iso_rows(rows)], "total": int(total),
            "page": int(page or 1), "size": int(size or 20)}


def get_run(run_id: int) -> Optional[dict]:
    row = execute_query(
        f"SELECT {_RUN_COLUMNS} FROM adh_decision_runs WHERE id = %s",
        (int(run_id),), fetchone=True)
    if not row:
        return None
    for col in ("evidence", "hit_path", "advice_snapshot"):
        row[col] = _json_obj(row.get(col), [])
    return _project_row_shape(_iso_rows([row])[0])


def find_turn_runs(conversation_id: int, message_uuid: str) -> list[dict]:
    """Chat 回放取实例，不重算：重算要重新取数会拖慢回放，且口径可能已变。

    这是全模块唯一一处"读实例而不是重算"的例外，原因写在计划 §出口 1 与
    `outlet.advice_for_messages`；定位键用 message_uuid 而不是消息序号：消息
    由前端持久化（PUT /chat/conversations），服务端在 done 时拿不到 seq。
    done 事件与本路径共用同一份实例来源（record_run 落的行）。
    """
    uuid = str(message_uuid or "").strip()
    if not uuid:
        return []
    rows = execute_query(
        f"SELECT {_RUN_COLUMNS} FROM adh_decision_runs "
        "WHERE conversation_id = %s AND message_uuid = %s ORDER BY id",
        (int(conversation_id or 0), uuid)) or []
    for r in rows:
        for col in ("evidence", "hit_path", "advice_snapshot"):
            r[col] = _json_obj(r.get(col), [])
    return [_project_row_shape(r) for r in _iso_rows(rows)]


def _project_row_shape(row: dict) -> dict:
    """实例的对外形状：树名随 tree_key 反查（显示用 name，不显裸 id）。"""
    tree = get_tree(str(row.get("tree_key") or ""))
    row["tree_name"] = (tree or {}).get("name") or (
        f"决策树已删除（{row.get('tree_key')}）" if row.get("tree_key") else "未知决策树")
    row["tree_deleted"] = not bool(tree)
    return row


def project_run(run: dict, outcome) -> dict:
    """把 (实例, 引擎产物) 合成一个前端可直接渲染的载荷。"""
    payload = {
        "tree_key": run.get("tree_key"),
        "tree_name": run.get("tree_name") or outcome.tree_name,
        "tree_version": run.get("tree_version"),
        "verdict": run.get("verdict"),
        "reason": run.get("verdict_reason") or outcome.reason,
        "hit_path": run.get("hit_path") or [],
        "advice": run.get("advice_snapshot") or [],
        "evidence": run.get("evidence") or [],
        "site_code": run.get("site_code") or "",
        "mode": outcome.mode,
        "missing_metrics": outcome.missing_metrics,
        "scope_notes": outcome.scope_notes,
        "unresolved": outcome.unresolved,
        "run_id": run.get("id"),
        "created_at": run.get("created_at"),
    }
    _assert_clean(payload)
    return payload


def _assert_clean(payload: Any) -> None:
    """出站收口（纵深防御）：投影里出现内部标识就是缺陷，当场抛而不是清洗后蒙混。

    判定复用 outbound_guard 的既有清单，不在本模块另写一套关键词表（护栏 §7）。
    """
    from backend.modules.mind.execution.outbound_guard import find_sensitive_hits

    text = json.dumps(payload, ensure_ascii=False, default=str)
    hits = find_sensitive_hits(text)
    if hits:
        logger.error("[decision] 对外投影命中敏感类别: %s", hits)
        raise DecisionError("决策建议内容里出现了不该下发的系统信息，已阻断本次返回",
                            "DECISION_OUTBOUND_BLOCKED", 500)


def _invalidate_cache(tree_key: str) -> None:
    """改树/发布/下架后失效该树的评估缓存（否则改完口径旧建议还会发 15 分钟）。"""
    cache = _eval_cache()
    if cache is None:
        return
    try:
        cache.clear_pattern(f"eval:{tree_key}:*")
    except Exception as exc:  # noqa: BLE001 — 缓存失效失败不能改判已落库的发布结果
        logger.warning("[decision] 缓存失效失败 tree=%s: %s", tree_key, exc)
