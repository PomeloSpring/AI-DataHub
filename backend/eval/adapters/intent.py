"""L0 前置分流适配器：route_intent 的确定性判定评测。

**覆盖范围（引用结论前必读）**
本适配器只调 `backend/modules/mind/execution/intent_gate.route_intent`，词典由
`fixtures/demo_semantics` 的字典名/别名/列业务名**在内存里打桩**：

* 不连元库、不起 SDK 会话、不返回任何数据行 —— 因此它**不反映模型真实的意图理解
  能力**，也不给取数正确率背书；
* 它回答的是另一个问题：**分流判定的对错**。这一层代价不对称 ——
  该放行的被拦 = 用户拿不到答案；该拦的没拦 = 白付一整个 agent 回合。
  用例必须把期望去向写清楚（``decision``/``handled_by``），门禁下限 100%。

`sources` 分桶是 **判定层级与模式**（layer/mode），不是解析档位也不是检索来源。
"""

from __future__ import annotations

from backend.eval.contract import Case
from backend.eval.fixtures import demo_semantics as demo


def lexicon_terms() -> list[str]:
    """内存词典（与 intent_gate._build_lexicon 同形状：业务名 + 别名 + 列业务名）。"""
    terms: set[str] = set()
    for table_cols in demo.PHYS.values():
        for meta in table_cols.values():
            desc = str(meta.get("business_desc") or "").strip()
            if len(desc) >= 2:
                terms.add(desc)
    for group in (demo.DIMS, demo.METRICS):
        for rows in group.values():
            for row in rows.values():
                for key in ("name", "name_en"):
                    val = str(row.get(key) or "").strip()
                    if len(val) >= 2:
                        terms.add(val)
                for a in (row.get("aliases") or []):
                    val = str(a or "").strip()
                    if len(val) >= 2:
                        terms.add(val)
    # 平台内真实业务对象名（评测里当作"已建模实体"的补充信号）
    terms.update({"案例", "工单", "客户", "区域"})
    return sorted(terms)


class _StubPolicy:
    """最小策略替身：本地文案只用它取 AS-BOT 名与已授权能力组，不做任何权限裁决。"""

    def __init__(self, as_bot: dict, selection: dict):
        self.as_bot = as_bot
        self.selection = selection
        self.external = {}
        self.function_names = ()


def run_case(case: Case) -> dict:
    """跑一条分流用例，返回 ``observed`` 供 contract.check_intent 比对。"""
    from backend.modules.mind.execution import intent_gate

    payload = case.payload or {}
    terms = payload.get("lexicon")
    if terms is None:
        terms = lexicon_terms()

    policy = None
    if payload.get("as_bot") or payload.get("groups"):
        policy = _StubPolicy(dict(payload.get("as_bot") or {}), dict(payload.get("groups") or {}))

    decision = intent_gate.route_intent(
        case.question,
        history=payload.get("history") or [],
        attachments=payload.get("attachments") or [],
        task_binding=payload.get("task_binding"),
        force_agent=bool(payload.get("force_agent")),
        # 评测默认按 enforce 判"该不该拦"，行为档位另有用例显式指定
        mode=str(payload.get("mode") or "enforce"),
        policy=policy,
        lexicon_terms=list(terms),
    )

    # 本地文案只在真要拦截时才生成；评测里无论哪个模式都把它构造出来，
    # 这样 reply_contains / no_leak 的断言在 shadow 模式下同样可证伪。
    reply = decision.get("reply") or ""
    if decision.get("intercept") and not reply:
        reply = intent_gate.build_local_reply(decision, policy=policy, ctx=None)

    signals = list(decision.get("signals") or [])
    if decision.get("gate_error"):
        signals = signals + [f"gate_error:{decision['gate_error']}"]
    return {
        "decision": intent_gate._outcome_of(decision),
        "label": decision.get("label"),
        "handled_by": decision.get("handled_by"),
        "intercept": bool(decision.get("intercept")),
        "reason": decision.get("reason"),
        "signals": signals,
        "reply": reply,
        "sources": {"layer": decision.get("layer") or "",
                    "mode": decision.get("mode") or ""},
        "item_count": len(signals),
    }
