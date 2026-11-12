"""决策建议的 Chat 出口：随本轮分析结果出建议（服务端判定，前端只消费）。

接线点只有一个：`chat_service._sse_event` 的 ``done`` 分支，与「存为报表」的
``with_reportability`` 同批同范式 —— 判断留在服务端，绝不在前端再写一套"要不要
显示建议"的逻辑（那必然漂出第二个口径）。

三条设计约束（都是被护栏逼出来的，不是偏好）：

1. **turn 模式不补取数**：只用本轮已经拿到的结果求值。本轮没取到的指标 → 
   ``verdict='partial'`` 并点名缺哪些指标，卡片给「完整评估」按钮由人决定要不要
   再打一次库。悄悄补取数会把首答拖慢 3~10s 而用户完全不知道发生了什么。
2. **实例是唯一的回放来源**：done 事件与历史回放读的是同一批 ``adh_decision_runs``
   行。回放**不重算** —— 重算要重新取数（拖慢打开会话），而且口径可能已被人改过，
   重算出来的"当时建议"根本不是当时那条（这是本模块唯一的"读实例而非重算"例外）。
3. **无权限与闲聊轮都不出卡**，但原因必须落在 payload 里；有候选树却判不了时必须
   出 ``partial/skipped`` 卡（"没建议"与"没能给建议"是两件事，§1 不静默）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.modules.decision import dictionary as dic
from backend.modules.decision import engine, models, service

logger = logging.getLogger(__name__)

READ_PERM = "decision:read"
#: 与 report_service.DATA_BEARING_TOOLS 同源：只有真取过数的回合才值得评估。
#: 不另列一份清单，否则"可存为报表"与"可出建议"两套口径迟早飘开。
_DATA_TOOLS = ("execute_sql", "query_execute", "run_semantic_query",
               "query_dataset", "run_quality_check", "generate_report")


def _turn_results(payload: dict) -> tuple[list[dict], str]:
    """从 done 载荷里收集本轮的取数结果，回 ``([{columns, rows}, ...], 无法使用的说明)``。

    两个来源：① 载荷自带的 ``result``（非 agent 链路与旧口径）；② ``tool_calls`` 里
    取数类工具的结果文本（agent 链路的结果只在这里）。工具结果是为展示而截断过的
    文本，解析不出来就归到"说明"里如实告知，不拿半截数据当依据。
    """
    out: list[dict[str, Any]] = []
    truncated = 0

    res = payload.get("result")
    if isinstance(res, dict) and res.get("columns") and res.get("rows"):
        out.append({"columns": [str(c) for c in res["columns"]], "rows": res.get("rows") or []})

    for call in payload.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool") or "").rsplit("__", 1)[-1]
        if name not in _DATA_TOOLS or call.get("error"):
            continue
        raw = call.get("result")
        if isinstance(raw, (dict, list)):
            parsed = raw
        else:
            text = str(raw or "").strip()
            if text.endswith("…"):        # 展示截断标记：这份结果不能拿来判定
                truncated += 1
                continue
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                truncated += 1
                continue
        cols = parsed.get("columns") if isinstance(parsed, dict) else None
        rows = parsed.get("rows") if isinstance(parsed, dict) else None
        if not cols or not rows:
            continue
        columns = [str(c.get("name", "") if isinstance(c, dict) else c) for c in cols]
        out.append({"columns": columns, "rows": rows})

    if truncated:
        note = (f"本轮有 {truncated} 次取数结果为展示而截断，无法完整还原，"
                "这部分未做判定（如需完整结论请用决策中心的重新评估）")
        return out, note
    return out, ""


def _column_names(results: list[dict]) -> set[str]:
    names: set[str] = set()
    for r in results:
        for c in r.get("columns") or []:
            n = dic.norm(c)
            if n:
                names.add(n)
    return names


def _candidate_trees(trees: list[dict], columns: set[str]) -> list[dict]:
    """候选树 = required_metrics 与本轮列名有交集的树（按业务名归一后比对）。

    交集为空就不评估：拿一棵本轮完全没碰到的树去判，得到的一定是 partial，
    每轮都弹一张"没法判断"的卡片只是噪声。
    """
    out = []
    for row in trees:
        names = row.get("required_metrics") or []
        if isinstance(names, str):
            try:
                names = json.loads(names)
            except (TypeError, ValueError):
                names = []
        hit = [n for n in (names or []) if dic.norm(n) in columns]
        if hit:
            out.append(row)
    return out


def _pick_result(results: list[dict], metrics: list[str]) -> dict:
    """给这棵树挑一份最能覆盖它的本轮结果（多工具回合里各结果列不同）。"""
    want = {dic.norm(m) for m in metrics if dic.norm(m)}
    best, best_score = {}, 0
    for r in results:
        score = len(want & {dic.norm(c) for c in r.get("columns") or []})
        if score > best_score:
            best, best_score = r, score
    return best or (results[0] if results else {})


def can_read(identity: dict) -> bool:
    """决策建议可见性：admin 走与 API 中间件同样的 unrestricted 旁路。

    判定只读 ``role_service``（角色权限码唯一真值源），本模块不新建第二套判断。
    """
    if str(identity.get("role") or "") == "admin":
        return True
    from backend.core.role_service import role_service
    try:
        codes = role_service.get_user_role_perm_codes(
            int(identity.get("user_id") or 0), int(identity.get("workspace_id") or 0)) or []
    except Exception as exc:  # noqa: BLE001 — 读不到授权就当没有授权（fail-closed）
        logger.warning("[decision] 角色权限码读取失败，本轮不评估: %s", exc)
        return False
    return READ_PERM in codes


def evaluate_turn(payload: dict, identity: dict) -> dict:
    """本轮求值并落实例，回 ``{"items": [...], "skipped_reason": str}``。"""
    if payload.get("error"):
        return {"items": [], "skipped_reason": "本轮执行未完成，未做决策判定"}

    results, unusable = _turn_results(payload)
    if not results:
        # 问答/操作说明类回合根本不带取数结果：这类回合与决策无关，**不下发任何文案**
        # （前端 items 与 reason 都空就不渲染）。给一句“本轮不做判定”看似诚实，
        # 实际是把“没这个必要”说成“功能没跑”，还会反过来教模型每轮都提决策。
        # 只有“本该有结果但被截断/不可用”才要说清（那是真没评成，no-silent-degradation §1）。
        return {"items": [], "skipped_reason": unusable}

    if not can_read(identity):
        return {"items": [], "skipped_reason":
                f"当前角色未授予「{READ_PERM}」，决策建议不可见；请联系管理员在角色权限页开通"}

    ws = int(identity.get("workspace_id") or 0)
    columns = _column_names(results)
    try:
        trees = _candidate_trees(service.active_trees(ws), columns)
    except Exception as exc:  # noqa: BLE001 — 字典/资产读失败必须显式，不演成"没有建议"
        logger.exception("[decision] Chat 回合取候选树失败")
        return {"items": [], "skipped_reason":
                f"决策树清单读取失败（{type(exc).__name__}），本轮未评估"}
    if not trees:
        return {"items": [], "skipped_reason": ""}

    items = []
    for row in trees:
        metrics = row.get("required_metrics") or []
        chosen = _pick_result(results, metrics)
        ctx = engine.EvalContext(
            mode="turn",
            user_id=int(identity.get("user_id") or 0),
            username=str(identity.get("username") or ""),
            role=str(identity.get("role") or ""),
            workspace_id=ws,
            datasource_id=int(identity.get("datasource_id") or 0),
            site_code=str(identity.get("site_code") or ""),
            trigger_type="chat_turn",
            conversation_id=int(identity.get("conversation_id") or 0),
            message_uuid=str(identity.get("message_uuid") or ""),
            turn_columns=chosen.get("columns") or [],
            turn_rows=chosen.get("rows") or [],
        )
        try:
            outcome = engine.evaluate_tree(row, ctx)
        except Exception as exc:  # noqa: BLE001 — 一棵树异常不能带走整轮回答
            logger.exception("[decision] Chat 回合求值异常 tree=%s", row.get("tree_key"))
            items.append({"tree_key": row.get("tree_key"), "tree_name": row.get("name"),
                          "verdict": "skipped", "reason": f"求值异常（{type(exc).__name__}），未评估",
                          "hit_path": [], "advice": [], "evidence": []})
            continue
        run = service.record_run(row, outcome, ctx)
        items.append(service.project_run(run, outcome))
    return {"items": items, "skipped_reason": unusable}


def with_decision_advice(payload: dict, identity: dict | None) -> dict:
    """给 done 事件补 ``decision_advice``（返回副本）。

    出口只此一处；身份与站点都由调用方（chat_service）从服务端上下文给出，
    绝不从请求体取（护栏 §2）。``identity is None`` = 这个出口是错误/中止回合，
    根本不补这个键（前端无键就不渲染，比回一句空原因更诚实）。
    """
    out = dict(payload)
    if identity is None:
        return out
    try:
        out["decision_advice"] = evaluate_turn(payload, identity)
    except service.DecisionError as exc:
        # 出站收口命中（内容里有内部标识）：整块建议不下发，但原因要说清
        logger.error("[decision] Chat 建议被出站收口拦下: %s", exc)
        out["decision_advice"] = {"items": [], "skipped_reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 建议出口故障不能拖垮已完成的回答
        logger.exception("[decision] Chat 建议出口异常")
        out["decision_advice"] = {"items": [], "skipped_reason":
                                 f"决策建议出口异常（{type(exc).__name__}），本轮未评估"}
    return out


def advice_for_messages(conversation_id: int, message_uuids: list[str]) -> dict[str, list]:
    """历史回放：按 message_uuid 批量取实例，**不重算**。

    为什么例外于"结论都实时算"：重算需要重新取数，打开一个 50 轮的老会话就会
    变成 50 轮打库；而且口径可能已被人改过，重算出来的不是"当时那条建议"。
    done 事件与本路径读的是同一批 ``adh_decision_runs`` 行（唯一实例来源）。
    """
    out: dict[str, list] = {}
    for uuid in [u for u in (message_uuids or []) if u]:
        rows = service.find_turn_runs(conversation_id, uuid)
        if rows:
            out[uuid] = rows
    return out


def report_block_markdown(conversation_id: int, message_uuid: str) -> str:
    """报表正文里的「决策建议」区块（计划 §四出口 4）。

    只读那一轮已落的实例，**不重算**（与历史回放同一条例外与同一份来源）：报表
    归档可能在几天后，重算出来的不是当时那条建议。

    内容只用业务名/阈值/实测值/建议正文（`record_run` 落库前已经过
    `service._assert_clean`，报表侧还会再过一道 `outbound_guard`），不拼任何物理标识。
    没实例时回空串（该轮确实没建议）；读失败时回一句显式说明，
    不能把“没读到”好发成“没有建议”（no-silent-degradation §1）。
    """
    try:
        runs = service.find_turn_runs(conversation_id, message_uuid)
    except Exception as exc:  # noqa: BLE001 — 归档不该因此失败，但要留痕迹
        logger.exception("[decision] 报表读取回合建议失败 conv=%s", conversation_id)
        return f"> 本轮决策建议未能随正文归档（读取失败：{type(exc).__name__}）。"
    if not runs:
        return ""

    lines = ["## 决策建议", ""]
    for run in runs:
        verdict = str(run.get("verdict") or "")
        label = models.VERDICT_LABELS.get(verdict, verdict or "未记录")
        head = f"- **{run.get('tree_name') or run.get('tree_key') or '未知决策树'}**"
        version = run.get("tree_version")
        if version:
            head += f"（v{version}）"
        lines.append(f"{head}：{label}")
        path = [str(p) for p in (run.get("hit_path") or []) if str(p)]
        if path:
            lines.append(f"  - 判定路径：{' → '.join(path)}")
        for a in (run.get("advice_snapshot") or []):
            if not isinstance(a, dict):
                continue
            action = str(a.get("action") or "（建议文案缺失）")
            severity = models.SEVERITY_LABELS.get(str(a.get("severity") or ""), "")
            lines.append(f"  - 建议：{action}" + (f"（严重级：{severity}）" if severity else ""))
            if a.get("rationale"):
                lines.append(f"    - 理由：{a['rationale']}")
            for ref in (a.get("kb_refs") or [])[:5]:
                slug = ref if isinstance(ref, str) else str((ref or {}).get("slug") or "")
                if slug:
                    lines.append(f"    - 知识出处：{slug}")
        for e in (run.get("evidence") or []):
            if not isinstance(e, dict):
                continue
            if e.get("judged") is False:
                lines.append(f"  - 依据：{e.get('metric') or '未具名指标'} 未能判定"
                             f"（{e.get('note') or '取数不可用'}）")
                continue
            unit = f" {e.get('unit')}" if e.get("unit") else ""
            lines.append(f"  - 依据：{e.get('metric') or '未具名指标'} "
                         f"{e.get('comparator') or ''} {_cell(e.get('threshold'))}{unit}，"
                         f"实测 {_cell(e.get('measured'))}{unit}")
        if run.get("verdict_reason") and verdict != "hit":
            lines.append(f"  - 说明：{run['verdict_reason']}")
    lines += ["", f"> 本节为当时那一轮的判定结果快照（决策树口径后续可能被修改）。 "
               f"完整清单见「决策建议」页。"]
    return "\n".join(lines).strip()


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return " ~ ".join(str(v) for v in value)
    return str(value)
