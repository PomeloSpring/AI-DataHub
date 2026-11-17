"""一次性回灌：adh_llm_traces 的 model_ref / credits 缺失值（从已落库的 span 与累计值推导）。

背景（两处历史缺陷）：
- trace.model_ref 只取请求入参，未指定模型（走默认/Auto）时整列为空 → 「模型用量」显示 —；
  真实生效模型名其实已在 `adh_llm_spans`(kind=llm_call).model_ref 里，可回填。
- 新版 Qoder CLI 不再给请求级 credits，只在 ResultMessage 给**会话累计** total_credits
  （已存 session_credits 列）→ trace.credits 恒为 NULL，模型/回合维度 credit 全为 0；
  本脚本按"同会话累计值之差"补算回合 credit（累计回落视为会话重置，按全额计；
  补算结果为 0 的历史回合保持 NULL —— 旧 CLI 不报 credit，写 0 是假数据）。

成本按 OBSERVABILITY_CREDIT_COST_RATE 单价与 OBSERVABILITY_CREDIT_COST_CURRENCY 币种折算
（单价填 0 = 不折算，成本列保持 NULL，不臆造价格）。token 无法回灌（历史 result span 未落 usage）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/backfill_llm_trace_usage.py          # dry-run 报告
  venv/bin/python backend/scripts/backfill_llm_trace_usage.py --apply  # 落库

幂等：第二次执行应报 0 处变更。
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)


def _plan_model_ref(cur) -> list[tuple[str, str]]:
    """空 model_ref 的 trace → 该 trace 首个（按时间）有模型名的 llm_call span。"""
    cur.execute(
        """SELECT s.trace_id, s.model_ref
             FROM adh_llm_spans s
             JOIN adh_llm_traces t ON t.trace_id = s.trace_id
             WHERE t.model_ref = '' AND s.kind = 'llm_call' AND s.model_ref <> ''
             ORDER BY s.trace_id, s.started_at""")
    first: dict[str, str] = {}
    for r in cur.fetchall() or []:
        first.setdefault(r["trace_id"], r["model_ref"])
    return list(first.items())


def _plan_credits(cur) -> list[tuple[str, float]]:
    """credits 为 NULL 且有会话累计的 trace → 按会话内累计增量补算。

    基线必须走**全部**有累计值的回合（含已有请求级 credit 的），否则跳过一轮会重复计数。
    """
    cur.execute(
        """SELECT trace_id, conversation_id, session_credits, credits
             FROM adh_llm_traces
             WHERE session_credits IS NOT NULL AND conversation_id > 0
             ORDER BY conversation_id, started_at, created_at""")
    rows = cur.fetchall() or []
    plans: list[tuple[str, float]] = []
    skipped_zero = 0
    baseline: dict[int, float] = {}
    for r in rows:
        conv, cum = int(r["conversation_id"]), float(r["session_credits"])
        prev = baseline.get(conv)
        delta = cum if prev is None or cum < prev else cum - prev
        baseline[conv] = cum
        if r["credits"] is not None:
            continue
        # 旧版 CLI 根本不回报会话累计（恒为 0），无法与“真的未计费”区分：
        # 宁可留 NULL（前端显示 —）也不写假 0。新链路（实时补算）不受此限。
        if delta <= 0:
            skipped_zero += 1
            continue
        plans.append((r["trace_id"], round(delta, 6)))
    if skipped_zero:
        print(f"  （跳过 {skipped_zero} 条累计为 0 的历史回合：旧 CLI 未上报 credit，保持 NULL）")
    return plans


def _plan_costs(cur, rate: float, currency: str) -> list[tuple[str, float, str]]:
    """已有 credit 但无成本的 trace → 按配置单价折算（币种显式写入）。"""
    cur.execute(
        """SELECT trace_id, credits FROM adh_llm_traces
           WHERE credits IS NOT NULL AND cost_amount IS NULL""")
    rows = cur.fetchall() or []
    if rate <= 0:
        return []
    return [(r["trace_id"], round(float(r["credits"]) * rate, 6), currency) for r in rows]


def run(apply: bool) -> int:
    from backend.common.db.metadata_db import get_metadata_conn
    from backend.common.config import OBSERVABILITY_CREDIT_COST_CURRENCY, OBSERVABILITY_CREDIT_COST_RATE

    rate = max(0.0, float(OBSERVABILITY_CREDIT_COST_RATE or 0))
    currency = (OBSERVABILITY_CREDIT_COST_CURRENCY or "CNY").strip().upper()
    with get_metadata_conn() as conn:
        cur = conn.cursor()
        model_plans = _plan_model_ref(cur)
        credit_plans = _plan_credits(cur)
        cost_plans = _plan_costs(cur, rate, currency)
        print(f"待回填 model_ref: {len(model_plans)} 条；待补算 credits: {len(credit_plans)} 条；"
              f"待折算成本: {len(cost_plans)} 条（单价 {rate or '未配置'} {currency}）")
        for tid, m in model_plans[:10]:
            print(f"  model_ref  {tid[:12]}… → {m}")
        for tid, c in credit_plans[:10]:
            print(f"  credits    {tid[:12]}… → {c}")
        if len(model_plans) > 10 or len(credit_plans) > 10:
            print("  …（其余省略）")
        if not apply:
            print("dry-run：未写库。加 --apply 执行。")
            return 0

        n_model = n_credit = n_cost = 0
        for tid, m in model_plans:
            cur.execute("UPDATE adh_llm_traces SET model_ref=%s "
                        "WHERE trace_id=%s AND model_ref=''", (m, tid))
            n_model += cur.rowcount
        for tid, c in credit_plans:
            cur.execute("UPDATE adh_llm_traces SET credits=%s "
                        "WHERE trace_id=%s AND credits IS NULL", (c, tid))
            n_credit += cur.rowcount
        for tid, amount, cur_ccy in cost_plans:
            cur.execute("UPDATE adh_llm_traces SET cost_amount=%s, cost_currency=%s "
                        "WHERE trace_id=%s AND cost_amount IS NULL", (amount, cur_ccy, tid))
            n_cost += cur.rowcount
        conn.commit()
        print(f"已回填 model_ref {n_model} 条、credits {n_credit} 条、cost_amount {n_cost} 条。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args()
    return run(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
