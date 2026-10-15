"""自进化闭环服务：信号 → 定时归纳 → 候选 → 人工审核四出口 → 回归验证。

要补的断链（本模块存在的理由）
------------------------------
1. **采集近乎为零**：`adh_message_feedback` 全库 1 条；前端点踩后让用户手输
   物理表名（违反 ui-resource-display §2 与护栏 §7）。
2. **归纳不存在**：反馈表与别名建议表零关联，踩了不产生任何候选；
   `absorb_business_terms.py` / `check_alias_projection.py` 是手动 dry-run 脚本。
3. **闭环在关键场景走不通**：9 条 pending 建议里 8 条 `target_ref` 为空
   （`DAU`/`activation_rate`/`站点`/`企业省份` —— 正是"指标还没建"的真实缺口），
   而旧的 `approve_suggestion` 只能把词追加到**已存在**的字典行，对不存在的行
   直接 raise。越需要归纳的词，审核越点不通。

三条不可妥协的纪律
------------------
* **分类必须可解释**：每条候选写 `classify_reason`（planner 结构化 type /
  字典命中结果 / 用户所选原因）。拿黑盒分桶糊弄分类，人只能凭直觉审核。
* **歧义不自动合并**：一个词命中多个归属方时只落 pending 清单等人工确认，
  沿用 `absorb_business_terms.py` 既有纪律（自动合并会污染别名）。
* **验证可证伪**：候选转成评测用例后，只有该用例在后续运行中 pass 才置
  `verified`；仍 fail 置 `stale` 并在页面可见。"转出去了"不等于"修好了"。

写出口一律复用既有 service 与权限码，不另造旁路；评测出口复用
`backend.eval.store.create_case`，且评测权限码 `ai_access='none'` 保证
"评价 AI 的标尺"不由 AI 自己写。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger("catalog.evolution")

KINDS = ("new_metric", "new_dimension", "alias", "binding_gap", "term")
SOURCES = ("parse_fail", "feedback", "script", "eval_failure")
#: expired 与 rejected 的差别不是轻重，是**谁做的判断**：
#: rejected = 人看过认为不该收；expired = 词已不再出现/已自行修好，自动关单。
#: 混用一个状态会让"这批候选被否了"与"这批候选过期了"无法区分。
STATUSES = ("pending", "converted", "rejected", "stale", "verified", "expired")

#: 命中权威名却仍解析不通 = 资产已存在但**对本对象不可达**。
#: 实测依据：旧建议队列 9 条里 7 条（activated_devices / online_devices /
#: activation_rate / DAU / 站点 / 企业省份 / case_count）在字典里 is_active=1，
#: 却被 planner 报 unresolved —— planner 按**物理表**加载字典，字典行的
#: target_table/bound_object_key 与对象主表不一致就看不见。这时候补别名是
#: 误诊（别名治不了不可达），建重复字典行更糟（同一概念两个口径）。
#: 所以单独成类，指向"核绑定"而不是"建资产"。
BINDING_GAP_REASON = ("字典行已以该权威名登记但 planner 仍报未解析："
                      "差异在绑定可达性（target_table/bound_object_key 与对象主表不一致），"
                      "不在词典缺失；请核对绑定后重试，不要另建新行")

#: 用户点踩时的原因分派 → 候选 kind。前端选择框与本表必须一致：
#: 口径写两处迟早飘，所以这里就是唯一映射表。
FEEDBACK_REASON_KIND: dict[str, str] = {
    "missing_metric": "new_metric",
    "missing_dimension": "new_dimension",
    "wrong_answer": "term",
    "not_understood": "term",
    "data_incomplete": "term",
}
FEEDBACK_REASONS: tuple[str, ...] = (
    "wrong_answer", "not_understood", "missing_metric",
    "missing_dimension", "data_incomplete",
)

#: 形态线索：只在**没有结构化信号**时才用，且必须把用到的线索写进 classify_reason。
_METRIC_SHAPE = re.compile(
    r"(数|总量|总额|金额|率|占比|均价|均值|单价|时长|环比|同比|gmv|dau|mau|uv|pv|"
    r"count|amount|rate|ratio|avg|total|sum|revenue)", re.I)
#: 分组/维度词尾线索（"企业省份""按站点""渠道"……）。
_DIMENSION_SHAPE = re.compile(
    r"(省|市|区|域|站点|渠道|类型|状态|阶段|等级|来源|类别|分类)", re.I)

# ── 小工具 ────────────────────────────────────────────────────

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


def _loads(val, default):
    if isinstance(val, (list, dict)):
        return val
    if val in (None, ""):
        return default
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return default


def _clean(term, limit: int = 128) -> str:
    return str(term or "").strip()[:limit]


def datasource_name_by_id(datasource_id: int) -> str:
    """id → 业务名。数据源可用性/展示一律以 name 为准（name 全局唯一）。"""
    ds_id = int(datasource_id or 0)
    if not ds_id:
        return ""
    rows = _q("SELECT name FROM adh_datasources WHERE id = %s", (ds_id,))
    return _clean((rows[0].get("name") if rows else "") or "", 128)


# ── 权威命中判定（决定 alias 还是新建）──────────────────────────

def datasource_id_by_name(datasource_name: str) -> int:
    """业务名 → id（name 全局唯一 uk_datasource_name，删重建也不影响匹配）。"""
    nm = _clean(datasource_name, 128)
    if not nm:
        return 0
    rows = _q("SELECT id FROM adh_datasources WHERE name = %s", (nm,))
    return int(rows[0]["id"]) if rows else 0


def find_authoritative(term: str, datasource_name: str = "") -> list[dict]:
    """业务词是否已有权威归属：字典名/英文名/别名、本体对象 key/显示名/别名。

    返回命中清单（可能多条=歧义）。命中即应走 `alias` 出口（补登记），
    未命中才走 `new_metric`/`new_dimension`/`term`。
    """
    t = _clean(term)
    if not t:
        return []
    hits: list[dict] = []

    for table, kind in (("adh_metrics", "metric"), ("adh_dimensions", "dimension")):
        rows = _q(
            f"SELECT id, name, name_en, aliases, bound_object_key, target_table FROM {table} "
            "WHERE is_active = 1 AND (name = %s OR name_en = %s)", (t, t))
        for r in rows:
            hits.append({"kind": kind, "ref": _clean(r.get("name")),
                         "object_key": _clean(r.get("bound_object_key")),
                         "target_table": _clean(r.get("target_table")),
                         "via": "name"})
        if not rows:
            # 别名登记在 JSON 数组里，用 LIKE 预筛再精确比对（避免把子串当命中）
            cand = _q(
                f"SELECT id, name, aliases, bound_object_key, target_table FROM {table} "
                "WHERE is_active = 1 AND aliases LIKE %s", (f"%{t}%",))
            for r in cand:
                if t in [str(x) for x in (_loads(r.get("aliases"), []) or [])]:
                    hits.append({"kind": kind, "ref": _clean(r.get("name")),
                                 "object_key": _clean(r.get("bound_object_key")),
                                 "target_table": _clean(r.get("target_table")),
                                 "via": "aliases"})

    # 对象层：adh_ontology_models 没有 datasource_name 列，只有 datasource_id；
    # 源名先转 id 再筛，不拿名字去碰不存在的列。
    sql = ("SELECT o.object_key, o.display_name, o.aliases "
           "FROM adh_ontology_objects o "
           "JOIN adh_ontology_models m ON m.id = o.model_id "
           "WHERE m.status = 'active' AND o.is_active = 1 AND (o.object_key = %s OR o.display_name = %s)")
    params: list = [t, t]
    ds_id = datasource_id_by_name(datasource_name)
    if datasource_name:
        if not ds_id:
            return hits          # 指定了源名但源不存在：不猜，仅保留字典层命中
        sql += " AND o.datasource_id = %s"
        params.append(ds_id)
    for r in _q(sql, tuple(params)):
        hits.append({"kind": "object", "ref": _clean(r.get("object_key")),
                     "object_key": _clean(r.get("object_key")), "via": "object_key"})
        if t in [str(x) for x in (_loads(r.get("aliases"), []) or [])]:
            hits.append({"kind": "object", "ref": _clean(r.get("object_key")),
                         "object_key": _clean(r.get("object_key")), "via": "object_aliases"})
    return hits


def classify_term(term: str, *, hint_type: str = "", candidates: list | None = None,
                  origin: str = "parse_fail", feedback_reason: str = "",
                  datasource_name: str = "") -> dict:
    """决定一个业务词的处置方向，并给出**可审计的分类依据**。

    Args:
        hint_type: planner 的结构化类型（metric|dimension|object），优先于任何猜测
        candidates: planner 回抛的近似候选（fuzzy_rejected 场景）
        origin: parse_fail | feedback | script
        feedback_reason: 用户点踩时选的原因（FEEDBACK_REASON_KIND）

    优先级：权威命中 → 结构化 hint_type → 用户声明原因 → 形态线索 → term 缓冲区。
    只有前者才构成 `alias`；后三类都是"新建"方向，因为本词典确实没有这个概念。
    """
    t = _clean(term)
    cands = [str(c) for c in (candidates or [])][:5]
    hits = find_authoritative(t, datasource_name)

    if hits:
        uniq = sorted({(h["kind"], h["ref"]) for h in hits if h["ref"]})
        # 歧义优先于一切细分判定：多个归属方时必须由人选，不能先扣上
        # "绑定不可达"的结论——那会让人对着两个候选中的一个盲改绑定。
        if len(uniq) > 1:
            return {"kind": "alias", "target_ref": "", "target_type": "",
                    "ambiguous": True,
                    "suggested_refs": [r for _k, r in uniq[:6]] + cands,
                    "classify_reason": ("命中多个归属方，按 absorb_business_terms 纪律"
                                        f"不自动合并，等人工确认：{uniq[:6]}")}
        # 命中的就是权威名（name/name_en）且只有一个 → 词已建档，
        # 不可达属绑定问题，不是缺别名
        by_name = [h for h in hits if h["via"] == "name"]
        if by_name and not any(h["via"] in ("aliases", "object_aliases") for h in hits):
            h0 = by_name[0]
            return {"kind": "binding_gap", "target_ref": h0["ref"],
                    "target_type": h0["kind"], "ambiguous": False,
                    "suggested_refs": cands, "hit_detail": by_name,
                    "classify_reason": BINDING_GAP_REASON}
        if len(uniq) == 1:
            kind, ref = uniq[0]
            return {"kind": "alias", "target_ref": ref, "target_type": kind,
                    "ambiguous": False, "suggested_refs": cands,
                    "classify_reason": (f"已命中权威归属（{kind}:{ref}，经 "
                                        f"{hits[0]['via']}），差异属别名缺登记，"
                                        f"走收编出口而非新建")}

    # 有近似候选（planner 的 fuzzy_rejected）= 概念很可能已存在，只是叫法不同。
    # 这时候建**新**字典行会造出两个近义指标（口径打架比缺指标更难发现），
    # 所以走 alias 出口由人指定目标；目标确不存在时再用"建草稿"出口。
    if cands:
        return {"kind": "alias", "target_ref": cands[0], "target_type": hint_type or "",
                "ambiguous": len(cands) > 1, "suggested_refs": cands,
                "classify_reason": (f"本词典无精确命中但 planner 回抛了近似候选 "
                                    f"{cands}；先走收编并默认指向首位，多个候选需人工选定，"
                                    f"避免同一概念重复建行")}

    if hint_type in ("metric", "dimension", "object"):
        if hint_type == "metric":
            return {"kind": "new_metric", "target_ref": "", "target_type": "metric",
                    "ambiguous": False, "suggested_refs": [],
                    "classify_reason": "planner 结构化 type=metric，且无权威命中也无近似候选"}
        if hint_type == "dimension":
            return {"kind": "new_dimension", "target_ref": "", "target_type": "dimension",
                    "ambiguous": False, "suggested_refs": [],
                    "classify_reason": "planner 结构化 type=dimension，且无权威命中也无近似候选"}
        return {"kind": "term", "target_ref": "", "target_type": "object",
                "ambiguous": False, "suggested_refs": [],
                "classify_reason": "planner 判为对象级缺口，本体尚无该对象，先进术语缓冲区待建模"}

    if origin == "feedback" and feedback_reason in FEEDBACK_REASON_KIND:
        kind = FEEDBACK_REASON_KIND[feedback_reason]
        return {"kind": kind, "target_ref": "", "target_type": "", "ambiguous": False,
                "suggested_refs": [],
                "classify_reason": f"用户点踩时声明原因 {feedback_reason}，本词典无权威命中"}

    if _METRIC_SHAPE.search(t):
        return {"kind": "new_metric", "target_ref": "", "target_type": "metric",
                "ambiguous": False, "suggested_refs": [],
                "classify_reason": "无权威命中与结构化类型，词形含度量线索"
                                   "（线索兜底分类，依这里声明的依据人工复核）"}
    if _DIMENSION_SHAPE.search(t):
        return {"kind": "new_dimension", "target_ref": "", "target_type": "dimension",
                "ambiguous": False, "suggested_refs": [],
                "classify_reason": "无权威命中与结构化类型，词形含分组维度线索"
                                   "（线索兜底分类，供人工复核）"}

    return {"kind": "term", "target_ref": "", "target_type": "", "ambiguous": False,
            "suggested_refs": [],
            "classify_reason": "无权威命中、无结构化类型、词形无可区分线索："
                               "按 ontology-modeling §5 落 business_terms 缓冲区，不猜建什么"}


# ── 候选落库（幂等 upsert，累加 hit_count）──────────────────────

def upsert_candidate(*, term: str, kind: str, source: str, datasource_name: str = "",
                     object_hint: str = "", target_ref: str = "", target_type: str = "",
                     classify_reason: str = "", evidence: dict | None = None,
                     delta_hits: int = 1) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind 不合法（未知候选类型 {kind!r}），应为 {list(KINDS)}")
    if source not in SOURCES:
        raise ValueError(f"source 不合法（未知信号来源 {source!r}），应为 {list(SOURCES)}")
    t = _clean(term)
    if not t:
        raise ValueError("候选必须有待收编的业务词")
    if not classify_reason:
        # 分类依据缺失 = 不可审计的黑盒分类，宁可报错也不入库
        raise ValueError(f"候选 {t!r} 缺少 classify_reason")

    rows = _q("SELECT id, status, hit_count FROM adh_evolution_candidates "
              "WHERE term=%s AND datasource_name=%s AND kind=%s",
              (t, _clean(datasource_name, 128), kind))
    payload = json.dumps(evidence or {}, ensure_ascii=False, default=str)
    if rows:
        cur = rows[0]
        # 已人工处置过的候选不再被作业改写状态，只累加证据与命中数
        _w("UPDATE adh_evolution_candidates SET hit_count = hit_count + %s, "
           "evidence = %s, target_ref = IF(target_ref='' OR %s='', %s, target_ref), "
           "target_type = IF(target_type='' OR %s='', %s, target_type), "
           "object_hint = IF(object_hint='' OR %s='', %s, object_hint), "
           "classify_reason = %s WHERE id = %s",
           (max(int(delta_hits or 1), 1), payload,
            target_ref, target_ref, payload,
            target_type, target_type, payload,
            object_hint, object_hint, payload,
            classify_reason, int(cur["id"])))
        return int(cur["id"])
    _w("INSERT INTO adh_evolution_candidates (term, kind, source, datasource_name, "
       "object_hint, target_ref, target_type, classify_reason, evidence, hit_count, status) "
       "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
       (t, kind, source, _clean(datasource_name, 128), _clean(object_hint, 128),
        _clean(target_ref, 128), _clean(target_type, 32), classify_reason[:512],
        payload, max(int(delta_hits or 1), 1)))
    new = _q("SELECT id FROM adh_evolution_candidates WHERE term=%s "
             "AND datasource_name=%s AND kind=%s", (t, _clean(datasource_name, 128), kind))
    return int(new[0]["id"]) if new else 0


def record_parse_fail_terms(datasource_id: int, terms: list[dict],
                            object_ref: str = "") -> int:
    """语义解析失败词落入候选队列（原 alias_suggestion.record_unresolved_terms 的继任）。

    只存业务词与候选，不存物理列/SQL。失败必须可见：这里不吞异常，
    调用方（取数主链路）自行决定旁路容错边界。
    """
    recorded = 0
    ds_name = datasource_name_by_id(datasource_id)
    for item in terms or []:
        term = _clean(item.get("term") if isinstance(item, dict) else item)
        if not term:
            continue
        hint = str(item.get("type") or "") if isinstance(item, dict) else ""
        cands = list(item.get("candidates") or []) if isinstance(item, dict) else []
        reason_src = str(item.get("reason") or "unresolved") if isinstance(item, dict) else "unresolved"
        cls = classify_term(term, hint_type=hint, candidates=cands, origin="parse_fail",
                            datasource_name=ds_name)
        upsert_candidate(
            term=term, kind=cls["kind"], source="parse_fail", datasource_name=ds_name,
            object_hint=_clean(object_ref, 128), target_ref=cls["target_ref"],
            target_type=cls["target_type"],
            classify_reason=f"{cls['classify_reason']}；原始信号 reason={reason_src}",
            evidence={"candidates": cands, "planner_type": hint, "reason": reason_src,
                      "object_ref": _clean(object_ref, 128),
                      # binding_gap 要能修，就得知道字典行现在绑在哪张表、哪个对象；
                      # 该字段仅 admin 可见（evolution:read, ai_access='none'）
                      "authoritative_hits": cls.get("hit_detail") or []},
            delta_hits=1)
        recorded += 1
    if recorded:
        logger.info("[evolution] 解析失败词落入候选 %d 条 ds=%s", recorded, ds_name or "-")
    return recorded


# ── 定时归纳作业 ─────────────────────────────────────────────

KIND_LABELS = {
    "new_metric": "待开发指标", "new_dimension": "待开发维度",
    "alias": "待收编别名", "binding_gap": "待核绑定", "term": "术语候选",
}


def record_eval_failure_candidate(*, suite: str, case_key: str, question: str,
                                  capability: str = "", datasource_name: str = "",
                                  term: str = "", reason: str = "",
                                  decided_by: str = "") -> dict:
    """评测失败就地转进化候选（人在评测页点击，不由作业自动扫）。

    为什么不让作业自动扫失败：自动判读得把逐用例 ``observed`` 存下来，而拿
    失败文案里的自由文本去猜"缺哪个业务词"只会造伪候选。这里只收人明确指定的
    ``term``（缺省用用例问句），并把"来自哪条用例的哪次失败"写进 classify_reason。
    """
    from backend.eval.contract import SUITES

    if suite not in SUITES:
        raise ValueError(f"未知评测层 {suite!r}，应为 {list(SUITES)}")
    t = _clean(term or question, 128)
    if not t:
        raise ValueError("转候选需要业务词或问句，空信号不入库")
    cls = classify_term(t, origin="parse_fail", datasource_name=datasource_name)
    cid = upsert_candidate(
        term=t, kind=cls["kind"], source="eval_failure", datasource_name=datasource_name,
        target_ref=cls["target_ref"], target_type=cls["target_type"],
        classify_reason=(f"由评测失败转入（{suite}/{case_key}）；{cls['classify_reason']}；"
                         f"失败原因：{_clean(reason, 200) or '未给出'}"),
        evidence={"suite": suite, "case_key": case_key, "capability": capability,
                  "question": _clean(question, 300), "failure_reason": _clean(reason, 300)},
        delta_hits=1)
    # 审核人也落库：这条候选是人工从评测失败里拉出来的，不是作业猜出来的
    _w("UPDATE adh_evolution_candidates SET decided_by=%s "
       "WHERE id=%s AND status='pending'", (_clean(decided_by, 64), cid))
    cand = get_candidate(cid) or {}
    kind = cand.get("kind")
    return {"success": True, "candidate_id": cid, "kind": kind, "term": t,
            "classify_reason": cand.get("classify_reason"),
            "message": f"已转入进化中心（{KIND_LABELS.get(kind, kind)}）"}


def recheck_candidates(*, object_probe_limit: int = 60, expire_days: int = 45) -> dict:
    """队列自我清算：复核已修好的词、关掉长期不再出现的词。

    为什么必须有这一步：候选表只有进没有出时，进化中心会被过期待办淹没，
    最后就没人看——一个没清算机制的 backlog 不是一个待办清单，是噪声发生器。

    三条口径：
    * 只复核**带对象上下文**的条目（能确定往哪个对象试）；剩下的计数报出来，
      不假装它们也过了；
    * 词已能解析→ verified（带依据）；长期不再命中→ expired（区别于人工 rejected）；
    * 不删行：留痕才能事后看出"这类词反复出现又反复被关掉"。
    """
    rows = _q(
        "SELECT id, term, kind, datasource_name, object_hint, evidence, hit_count, updated_at "
        "FROM adh_evolution_candidates WHERE status = 'pending' "
        "ORDER BY hit_count DESC, updated_at DESC LIMIT %s", (int(object_probe_limit),))
    resolved, still_open, unverifiable, expired = 0, 0, 0, 0
    for r in rows:
        ev = _loads(r.get("evidence"), {})
        obj = _clean(r.get("object_hint") or ev.get("object_ref"), 128)
        if not obj:
            unverifiable += 1
            continue
        kind = r.get("kind")
        # 只给字典层探针：planner 按字典 name/别名解析，能解就代表本词已可达
        field = "metrics" if kind in ("new_metric", "alias") else "dimensions"
        if kind == "term":
            unverifiable += 1
            continue
        try:
            still_fails = _term_still_unresolved(obj, field, r["term"],
                                                 r.get("datasource_name") or "")
        except Exception as e:  # noqa: BLE001 — 探针失败不得误判为"已修好"
            logger.warning("[evolution] 候选复核失败 candidate=%s: %s", r["id"], e)
            unverifiable += 1
            continue
        if not still_fails:
            _w("UPDATE adh_evolution_candidates SET status='verified', last_checked_at=NOW() "
               "WHERE id=%s", (int(r["id"]),))
            resolved += 1
        else:
            _w("UPDATE adh_evolution_candidates SET last_checked_at=NOW() WHERE id=%s",
               (int(r["id"]),))
            still_open += 1

    # 长期不再命中且无人处理的 → 过期（不是删，也不是当已修好）
    exp_rows = _q(
        "SELECT id FROM adh_evolution_candidates WHERE status='pending' "
        "AND hit_count = 1 AND updated_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (int(expire_days),))
    for r in exp_rows:
        _w("UPDATE adh_evolution_candidates SET status='expired', last_checked_at=NOW() "
           "WHERE id=%s", (int(r["id"]),))
        expired += 1

    return {"rechecked": len(rows), "resolved": resolved, "still_open": still_open,
            "unverifiable": unverifiable, "expired": expired}


def _term_still_unresolved(object_key: str, field: str, term: str,
                           datasource_name: str) -> bool:
    """用 L2 适配器跑一次真实解析，判断该词是否仍解析不通。

    故意复用 ``backend.eval.adapters.semantic`` 而不是自己再写一遍 resolve+plan：
    两处解析口径迟早飘，而飘了就会把"其实还没修好"误判成"已修好"自动关单。
    """
    from backend.eval.adapters.semantic import run_case as semantic_run_case
    from backend.eval.contract import normalize_case

    case = normalize_case({
        "case_key": "__evolution_recheck__", "suite": "semantic", "question": term,
        "capability": "resolution", "case_status": "active", "status_note": "",
        "tags": "", "is_active": 1, "id": 0,
        "payload": {"intent": {"object": object_key, field: [term], "limit": 1},
                    "datasource_name": datasource_name,
                    "identity": {"username": "admin"}},
        "expected": {},
    })
    observed = semantic_run_case(case)
    if observed.get("intent_rejected"):
        return True          # 问法本身被拒，不能当作"已修好"
    terms = {str(t.get("term") if isinstance(t, dict) else t)
             for t in (observed.get("unresolved_terms") or [])}
    return term in terms or bool(observed.get("unresolved_terms"))


def consume_feedback(*, limit: int = 300) -> dict:
    """把点踩反馈归类成候选。取不到的信号一律显式计数，不静默丢弃。

    问句与数据源从 ``adh_llm_traces`` 按 ``message_uuid`` 关联取：
    ``adh_message_feedback`` 本身**没存 question**（而 chat 入口收了 ``question``
    字段却从未落库），而 ``tags.datasource_id`` 是客户端上报的，不可信也不得当权威。
    不在这里存问句副本 —— 回合的事实源只有一个。
    """
    rows = _q(
        """SELECT f.id, f.expected_ref, f.dissatisfaction_reason, f.reason,
                  f.trace_id, f.message_uuid, t.question,
                  t.datasource_id AS trace_datasource_id
           FROM adh_message_feedback f
           LEFT JOIN adh_llm_traces t ON t.message_uuid = f.message_uuid
           WHERE f.satisfied = 0 AND f.digest_status = 'new'
           ORDER BY f.id DESC LIMIT %s""", (int(limit),))
    created, skipped_no_signal, consumed_ids = 0, 0, []
    for r in rows:
        # 数据源：用服务端记录的 trace，回退到客户端上报的 tags（旧行可能两者皆空）
        ds_id = int(r.get("trace_datasource_id") or 0)
        if not ds_id:
            ds_id = int((_loads(r.get("tags"), {}) or {}).get("datasource_id") or 0)
        ds_name = datasource_name_by_id(ds_id)
        reason = _clean(r.get("dissatisfaction_reason"), 32)
        ref = _clean(r.get("expected_ref"), 128)
        # 术语只取用户声明的期望业务名：拿自由文本问句分词去猜"他缺什么"
        # 会产出一堆伪候选，把审核成本转嫁给人（regex 猜词不是归纳）
        term = ref
        if not term:
            skipped_no_signal += 1
            _w("UPDATE adh_message_feedback SET digest_status='ignored' WHERE id=%s",
               (int(r["id"]),))
            continue
        cls = classify_term(term, origin="feedback", feedback_reason=reason,
                            datasource_name=ds_name)
        upsert_candidate(
            term=term, kind=cls["kind"], source="feedback", datasource_name=ds_name,
            object_hint=cls["target_ref"] if cls["kind"] == "alias" else "",
            target_ref=cls["target_ref"], target_type=cls["target_type"],
            classify_reason=cls["classify_reason"],
            evidence={"question": _clean(r.get("question"), 300),
                      "trace_id": _clean(r.get("trace_id"), 64),
                      "message_uuid": _clean(r.get("message_uuid"), 64),
                      "feedback_reason": reason, "expected_ref": ref,
                      "user_note": _clean(r.get("reason"), 300)},
            delta_hits=1)
        created += 1
        consumed_ids.append(int(r["id"]))
    if consumed_ids:
        _w(f"UPDATE adh_message_feedback SET digest_status='consumed' "
           f"WHERE id IN ({','.join(map(str, consumed_ids))})")
    return {"feedback_rows": len(rows), "candidates_touched": created,
            "skipped_no_signal": skipped_no_signal}


def recheck_verification() -> dict:
    """回填验证闭环：converted_ref 指向评测用例的候选，按最近一次运行结果定 verified/stale。"""
    rows = _q("SELECT id, converted_ref FROM adh_evolution_candidates "
              "WHERE converted_kind = 'eval_case' AND converted_ref <> '' "
              "AND status IN ('converted','verified','stale')")
    from backend.eval import store

    # 用例表只取一次（候选不多，但逐条查会把 N×全表扫描累在作业里）
    cases_by_key = {c.get("case_key"): c.get("suite")
                    for c in (store.list_cases(tags="", active_only=False) or [])}
    latest_run = {}
    verified, stale = 0, 0
    for r in rows:
        case_key = _clean(r.get("converted_ref"), 128)
        suite = cases_by_key.get(case_key) or ""
        if not suite:
            # 转出的用例已不在用例表：不能当已验证，也不能静默跳过
            _w("UPDATE adh_evolution_candidates SET status='stale', last_checked_at=NOW() "
               "WHERE id=%s", (int(r["id"]),))
            stale += 1
            continue
        if suite not in latest_run:
            latest_run[suite] = store.latest_done_run(suite)
        run = latest_run[suite]
        if not run:
            continue
        hit = [x for x in store.get_results(int(run["id"]))
               if x.get("case_key") == case_key]
        if not hit:
            continue
        passed = bool(hit[0].get("passed"))
        _w("UPDATE adh_evolution_candidates SET status=%s, last_checked_at=NOW() WHERE id=%s",
           ("verified" if passed else "stale", int(r["id"])))
        verified += 1 if passed else 0
        stale += 0 if passed else 1
    return {"verified": verified, "stale": stale, "checked": len(rows)}


def run_digest(*, limit: int = 300) -> dict:
    """归纳作业主入口（由内置任务 evolution_digest 周期调用）。

    汇总本轮事实进返回值供 record_run 落摘要；任何一步失败都直接抛，
    由调用方落 status='failed' 让页面可见（no-silent-degradation §1）。
    """
    fb = consume_feedback(limit=limit)
    rc = recheck_candidates()
    vc = recheck_verification()
    pending = _q("SELECT kind, COUNT(*) n FROM adh_evolution_candidates "
                 "WHERE status='pending' GROUP BY kind")
    by_kind = {r["kind"]: int(r["n"]) for r in pending}
    summary = (f"反馈行 {fb['feedback_rows']}→候选 {fb['candidates_touched']}"
               f"（无可归纳术语 {fb['skipped_no_signal']}）; "
               f"复核 {rc['rechecked']}：已修好 {rc['resolved']}/仍开放 {rc['still_open']}"
               f"/无法自动复核 {rc['unverifiable']}/过期 {rc['expired']}; "
               f"闭环复查 {vc['checked']} 条：已验证 {vc['verified']}/未生效 {vc['stale']}; "
               f"待办 {by_kind or '{}'}")
    return {"feedback": fb, "recheck": rc, "verification": vc,
            "pending_by_kind": by_kind, "summary": summary[:500]}


def backfill_alias_suggestions() -> dict:
    """把旧 adh_alias_suggestions 的历史行收编进候选表（幂等，单一事实源收口）。"""
    rows = _q("SELECT id, term, target_type, target_ref, datasource_id, source, "
              "candidates, hit_count, status, created_at FROM adh_alias_suggestions "
              "ORDER BY id")
    touched = 0
    for r in rows:
        term = _clean(r.get("term"))
        if not term:
            continue
        ds_name = datasource_name_by_id(int(r.get("datasource_id") or 0))
        cls = classify_term(term, hint_type=str(r.get("target_type") or ""),
                            candidates=_loads(r.get("candidates"), []),
                            origin="parse_fail", datasource_name=ds_name)
        cid = upsert_candidate(
            term=term, kind=cls["kind"], source="parse_fail", datasource_name=ds_name,
            target_ref=cls["target_ref"] or _clean(r.get("target_ref")),
            target_type=cls["target_type"] or _clean(r.get("target_type"), 32),
            classify_reason=f"{cls['classify_reason']}；搬迁自 "
                            f"adh_alias_suggestions#{r['id']}（source={r.get('source')}）",
            evidence={"candidates": _loads(r.get("candidates"), []),
                      "migrated_from": int(r["id"]), "original_status": r.get("status"),
                      "authoritative_hits": cls.get("hit_detail") or []},
            delta_hits=int(r.get("hit_count") or 1))
        if cid and str(r.get("status") or "pending") != "pending":
            _w("UPDATE adh_evolution_candidates SET status=%s, decided_at=NOW() WHERE id=%s",
               ("converted" if r.get("status") == "approved" else "rejected", cid))
        touched += 1
    return {"scanned": len(rows), "touched": touched}


# ── 别名回写（复用既有联动，不另造通道）────────────────────────

def adopt_alias(*, term: str, target_type: str, target_ref: str, datasource_id: int = 0,
                decided_by: str = "", candidate_id: int = 0) -> dict:
    """把词收编为已存在字典行/本体对象的别名。

    目标不存在时**不报错也不静默**，而是返回可操作的下一步指引（转建指标/维度草稿），
    这正是旧 approve_suggestion 的死路：8/9 条 pending 的 target_ref 为空，
    越需要归纳的词越点不通。
    """
    term = _clean(term)
    target_type = _clean(target_type, 32)
    target_ref = _clean(target_ref, 128)
    if not term or target_type not in ("metric", "dimension", "object"):
        raise ValueError("别名收编需要 term 与合法 target_type(metric|dimension|object)")

    if target_type == "object":
        model = _q("SELECT id FROM adh_ontology_models WHERE datasource_id = %s "
                   "AND status='active' AND kind='business' "
                   "ORDER BY updated_at DESC LIMIT 1", (int(datasource_id or 0),))
        if not model:
            raise ValueError("该数据源没有 active 业务本体模型，无法回写对象别名")
        from backend.modules.catalog.services import ontology_service
        doc = _loads(_q("SELECT json_content FROM adh_ontology_models WHERE id=%s",
                        (int(model[0]["id"]),))[0].get("json_content"), {})
        hit = False
        for obj in doc.get("objects") or []:
            if str(obj.get("key") or "").strip().lower() == target_ref.lower():
                aliases = [str(a) for a in (obj.get("aliases") or [])]
                if term not in aliases:
                    aliases.append(term)
                obj["aliases"] = aliases
                hit = True
                break
        if not hit:
            raise ValueError(f"active 业务本体中不存在对象 {target_ref!r}")
        # save_draft 自带级联：重算绑定/重展开对象/重建图谱/触发知识库同步
        ontology_service.save_draft(int(model[0]["id"]), json.dumps(doc, ensure_ascii=False))
    else:
        table = "adh_metrics" if target_type == "metric" else "adh_dimensions"
        rows = _q(f"SELECT id, aliases FROM {table} WHERE is_active = 1 AND name = %s LIMIT 1",
                  (target_ref,))
        if not rows:
            nxt = "new_metric" if target_type == "metric" else "new_dimension"
            result = {"success": False, "reason": "target_missing",
                      "next_action": nxt,
                      "message": (f"目标字典行 {target_ref!r} 不存在，无法只加别名；"
                                  f"请改用「建{nxt}草稿」出口")}
            return result
        aliases = [str(a) for a in (_loads(rows[0].get("aliases"), []) or [])]
        if term not in aliases:
            aliases.append(term)
            _w(f"UPDATE {table} SET aliases = %s WHERE id = %s",
               (json.dumps(aliases, ensure_ascii=False), int(rows[0]["id"])))

    if candidate_id:
        _w("UPDATE adh_evolution_candidates SET status='converted', converted_kind='alias', "
           "converted_ref=%s, decided_by=%s, decided_at=NOW() WHERE id=%s",
           (target_ref, _clean(decided_by, 64), int(candidate_id)))
    return {"success": True, "term": term, "target_type": target_type,
            "target_ref": target_ref}


# ── 字典草稿出口（修 target_ref 为空时的断点）──────────────────

def resolve_property_column(*, bound_object_key: str, datasource_name: str,
                            property_name: str) -> dict:
    """业务属性名 → 物理列（服务端映射，人不接触物理列名，也不让人手输）。

    口径与 planner 的 physical_desc 档一致：按本对象主表的列元数据反查。
    查不到就报错并回候选业务属性清单，**不得**回退成"直接用输入当列名"。
    """
    from backend.modules.catalog.services.metrics_service import _object_primary_table

    ds_rows = (_q("SELECT id FROM adh_datasources WHERE name=%s", (datasource_name,))
               if datasource_name else [])
    ds_id = int(ds_rows[0]["id"]) if ds_rows else 0
    conn = _conn()
    try:
        with conn.cursor() as cur:
            table = _object_primary_table(cur, bound_object_key, ds_id)
            if not table:
                raise ValueError(f"对象 {bound_object_key!r} 没有主表绑定，无法为其建维度")
            cur.execute("SELECT column_name, business_desc FROM adh_column_metadata "
                        "WHERE table_name = %s ORDER BY column_name", (table,))
            rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()
    want = _clean(property_name)
    by_biz = {str(r.get("business_desc") or "").strip(): str(r.get("column_name") or "")
              for r in rows}
    by_col = {str(r.get("column_name") or ""): str(r.get("column_name") or "") for r in rows}
    column = by_biz.get(want) or by_col.get(want)
    if not column:
        options = sorted({k for k in by_biz if k})[:20]
        raise ValueError(
            f"业务属性 {want!r} 在对象 {bound_object_key!r} 主表上找不到对应列；"
            f"请从可用业务属性中选择（示例: {options}），不得手填物理列名")
    return {"target_table": table, "target_column": column, "datasource_id": ds_id}


def create_metric_draft(*, term: str, bound_object_key: str, datasource_name: str,
                        display_name: str = "", agg_type: str = "COUNT",
                        formula: str = "", unit: str = "", description: str = "",
                        decided_by: str = "", candidate_id: int = 0) -> dict:
    """转指标草稿。落 is_active=0：口径未补全前不进语义层广告，避免半成品污染取数。"""
    from backend.modules.catalog.services.metrics_service import create_metric

    biz = _clean(display_name or term, 128)
    ds_rows = (_q("SELECT id FROM adh_datasources WHERE name=%s", (datasource_name,))
               if datasource_name else [])
    data = {"name": _clean(re.sub(r"[^0-9A-Za-z_]", "_", biz), 64) or biz,
            "display_name": biz, "bound_object_key": _clean(bound_object_key, 128),
            "datasource_id": int(ds_rows[0]["id"]) if ds_rows else 0,
            "calculation_type": _clean(agg_type, 32) or "COUNT",
            "formula": formula or "", "unit": unit or "",
            "description": description or "自进化候选归纳生成的指标草稿（待补口径后启用）",
            "aliases": [term] if _clean(term) != biz else [],
            "is_active": 0, "workspace_id": 0}
    res = create_metric(data)
    if candidate_id:
        _w("UPDATE adh_evolution_candidates SET status='converted', "
           "converted_kind='metric', converted_ref=%s, decided_by=%s, decided_at=NOW() "
           "WHERE id=%s", (biz, _clean(decided_by, 64), int(candidate_id)))
    # 草稿必须说清还缺什么：建完就报"成功"而没人补口径，等于把缺口挪了个地方
    return {"success": True, "id": res.get("id"), "name": biz, "is_active": 0,
            "needs_completion": ([] if data["formula"] else ["formula"]),
            "message": ("指标草稿已创建（未启用）。补齐口径后在指标中心启用，"
                        "否则语义层不会广告该指标。")
                    if data["formula"] else
                    ("指标草稿已创建（未启用）但**缺计算公式**，planner 无法编译它；"
                     "请在指标中心补 formula 后再启用。")}


def create_dimension_draft(*, term: str, bound_object_key: str, datasource_name: str,
                           property_name: str, display_name: str = "",
                           category: str = "属性", description: str = "",
                           decided_by: str = "", candidate_id: int = 0) -> dict:
    """转维度草稿。target_column 由**业务属性名**经服务端映射，人不输物理列。"""
    from backend.modules.catalog.services.metrics_service import create_dimension

    biz = _clean(display_name or term, 128)
    col = resolve_property_column(bound_object_key=_clean(bound_object_key, 128),
                                 datasource_name=datasource_name,
                                 property_name=property_name)
    data = {"name": biz, "display_name": biz,
            "name_en": _clean(re.sub(r"[^0-9A-Za-z_]", "_", str(term)), 64),
            "bound_object_key": _clean(bound_object_key, 128),
            "target_table": col["target_table"], "target_column": col["target_column"],
            "datasource_id": col["datasource_id"], "category": _clean(category, 32) or "属性",
            "description": description or "自进化候选归纳生成的维度草稿",
            "aliases": [term] if _clean(term) != biz else [],
            "workspace_id": 0}
    res = create_dimension(data)
    if candidate_id:
        _w("UPDATE adh_evolution_candidates SET status='converted', "
           "converted_kind='dimension', converted_ref=%s, decided_by=%s, decided_at=NOW() "
           "WHERE id=%s", (biz, _clean(decided_by, 64), int(candidate_id)))
    return {"success": True, "id": res.get("id"), "name": biz,
            "target_column": col["target_column"],
            "message": "维度草稿已创建并已绑定对象属性列（字典行即刻生效，"
                       "如口径不符请在维度中心修正或退役）"}


def to_business_term(*, term: str, datasource_name: str, decided_by: str = "",
                     candidate_id: int = 0, description: str = "") -> dict:
    """转入业务术语缓冲区（尚未绑定任何对象/字典的纯黑话，§5 合法落点）。

    术语**按 datasource 分桶**（§5）：必须带 datasource_id，不得落成全局行。
    无名可转（指定的源名查不到）时直接报错，不默默落到 ds=0 把所有源都污染。
    """
    ds_id = datasource_id_by_name(datasource_name)
    if datasource_name and not ds_id:
        raise ValueError(f"数据源 {datasource_name!r} 不存在，无法按源分桶落术语")
    biz = _clean(term, 128)
    existing = _q("SELECT id FROM adh_business_terms WHERE term_cn = %s "
                  "AND datasource_id = %s LIMIT 1", (biz, ds_id))
    if not existing:
        _w("INSERT INTO adh_business_terms (datasource_id, term_cn, description, is_active, "
           "created_at, updated_at) VALUES (%s, %s, %s, 1, NOW(), NOW())",
           (ds_id, biz, _clean(description or "自进化候选归纳转入的术语缓冲区条目", 512)))
    if candidate_id:
        _w("UPDATE adh_evolution_candidates SET status='converted', converted_kind='term', "
           "converted_ref=%s, decided_by=%s, decided_at=NOW() WHERE id=%s",
           (biz, _clean(decided_by, 64), int(candidate_id)))
    return {"success": True, "term": biz, "datasource_id": ds_id,
            "created": not existing,
            "message": "已按数据源分桶转入业务术语缓冲区；映射到对象/字典后请在本页标 verified"}


def to_eval_case(*, candidate_id: int, suite: str, capability: str, question: str,
                 payload: dict | None, expected: dict | None, note: str = "",
                 decided_by: str = "") -> dict:
    """候选转评测用例（把一次缺口固化成回归标尺）。

    复用 eval/store.create_case：期望键合法性、case_key 唯一性都由契约层硬校验，
    这里不放宽。评测权限码 ai_access='none' 保证该动作只能由人在页面上做。
    """
    from backend.eval import store
    from backend.eval.contract import SUITES
    from backend.eval.gate import CAPABILITIES

    if suite not in SUITES:
        raise ValueError(f"未知评测层 {suite!r}，应为 {list(SUITES)}")
    if capability not in CAPABILITIES:
        raise ValueError(f"未知能力契约组 {capability!r}，应为 {list(CAPABILITIES)}")
    q = _clean(question, 512)
    if not q:
        raise ValueError("评测用例必须有问句")
    case_key = _clean(f"evo_{int(candidate_id)}_{datetime.now():%Y%m%d%H%M%S}", 128)
    cid = store.create_case({"case_key": case_key, "suite": suite, "question": q,
                             "capability": capability, "case_status": "active",
                             "status_note": "", "tags": ["from_evolution"],
                             "payload": payload or {}, "expected": expected or {},
                             "note": _clean(note or "由进化候选归纳生成", 512),
                             "is_active": 1, "sort": 900},
                            created_by=_clean(decided_by, 64))
    cand = get_candidate(candidate_id) or {}
    _w("UPDATE adh_evolution_candidates SET status='converted', converted_kind='eval_case', "
       "converted_ref=%s, decided_by=%s, decided_at=NOW() WHERE id=%s",
       (case_key, _clean(decided_by, 64), int(candidate_id)))
    return {"success": True, "case_id": cid, "case_key": case_key,
            "candidate": cand.get("term"),
            "message": "已登记为评测用例；归纳作业会在该用例下次运行后回填 verified/stale"}


def reject_candidate(*, candidate_id: int, decided_by: str = "") -> dict:
    _w("UPDATE adh_evolution_candidates SET status='rejected', decided_by=%s, "
       "decided_at=NOW() WHERE id=%s", (_clean(decided_by, 64), int(candidate_id)))
    return {"success": True, "id": int(candidate_id)}


# ── 读面（页面消费）───────────────────────────────────────────

def get_candidate(candidate_id: int) -> dict | None:
    rows = _q("SELECT * FROM adh_evolution_candidates WHERE id=%s", (int(candidate_id),))
    if not rows:
        return None
    r = rows[0]
    r["evidence"] = _loads(r.get("evidence"), {})
    return r


def list_candidates(*, status: str = "pending", kind: str = "", source: str = "",
                    datasource_name: str = "", limit: int = 200) -> list[dict]:
    cond, params = [], []
    if status:
        cond.append("status = %s")
        params.append(status)
    if kind:
        cond.append("kind = %s")
        params.append(kind)
    if source:
        cond.append("source = %s")
        params.append(source)
    if datasource_name:
        cond.append("datasource_name = %s")
        params.append(datasource_name)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    params.append(int(limit))
    rows = _q(f"SELECT * FROM adh_evolution_candidates {where} "
              f"ORDER BY hit_count DESC, updated_at DESC LIMIT %s", tuple(params))
    for r in rows:
        r["evidence"] = _loads(r.get("evidence"), {})
    return rows


def object_options(datasource_name: str) -> list[dict]:
    """可归属对象清单（显示业务名、value 用 key；候选出口的选择框数据源）。"""
    ds_id = datasource_id_by_name(datasource_name)
    if not ds_id:
        return []
    rows = _q("SELECT DISTINCT o.object_key, o.display_name "
              "FROM adh_ontology_objects o JOIN adh_ontology_models m ON m.id = o.model_id "
              "WHERE m.status='active' AND o.is_active = 1 AND o.datasource_id = %s "
              "ORDER BY COALESCE(o.display_name, o.object_key)", (ds_id,))
    return [{"key": r["object_key"],
             "display_name": _clean(r.get("display_name") or r.get("object_key"))}
            for r in rows]


def property_options(*, bound_object_key: str, datasource_name: str) -> list[dict]:
    """对象主表上的**业务属性名**清单（维度草稿据此选列，人不碰物理名）。"""
    from backend.modules.catalog.services.metrics_service import _object_primary_table

    ds_rows = (_q("SELECT id FROM adh_datasources WHERE name=%s", (datasource_name,))
               if datasource_name else [])
    ds_id = int(ds_rows[0]["id"]) if ds_rows else 0
    conn = _conn()
    try:
        with conn.cursor() as cur:
            table = _object_primary_table(cur, _clean(bound_object_key, 128), ds_id)
            cur.execute("SELECT column_name, business_desc FROM adh_column_metadata "
                        "WHERE table_name = %s ORDER BY column_name", (table,))
            rows = [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()
    out = []
    for r in rows:
        label = _clean(r.get("business_desc") or r.get("column_name"))
        if label:
            out.append({"property": label, "column_name": r.get("column_name")})
    return out


def overview() -> dict:
    """进化中心首屏：候选分布 + 反馈大盘 + 闭环成效（不含这些就看不到缺口）。"""
    by_kind = {r["kind"]: int(r["n"]) for r in _q(
        "SELECT kind, COUNT(*) n FROM adh_evolution_candidates WHERE status='pending' "
        "GROUP BY kind")}
    by_source = {r["source"]: int(r["n"]) for r in _q(
        "SELECT source, COUNT(*) n FROM adh_evolution_candidates WHERE status='pending' "
        "GROUP BY source")}
    outcome = {r["status"]: int(r["n"]) for r in _q(
        "SELECT status, COUNT(*) n FROM adh_evolution_candidates "
        "WHERE status IN ('converted','verified','stale','rejected') GROUP BY status")}
    fb = _q("SELECT SUM(CASE WHEN satisfied=1 THEN 1 ELSE 0 END) up, "
            "SUM(CASE WHEN satisfied=0 THEN 1 ELSE 0 END) down, "
            "SUM(CASE WHEN satisfied=0 AND digest_status='new' THEN 1 ELSE 0 END) "
            " AS pending_negative FROM adh_message_feedback")[0]
    return {"pending_by_kind": by_kind, "pending_by_source": by_source,
            "outcome": outcome,
            "feedback": {"up": int(fb.get("up") or 0), "down": int(fb.get("down") or 0),
                         "pending_negative": int(fb.get("pending_negative") or 0)},
            "reasons": FEEDBACK_REASONS, "kinds": KINDS, "sources": SOURCES}
