"""回填历史回合的模型通道（adh_llm_traces.channel），dry-run 默认、判不出的不猜。

背景:
  `channel` 列 2026-11-05 上线，之前的回合该列为 NULL，概览的「模型通道」表因此把
  一整段历史显示成"未记录"。本脚本按**可核验的判据**回填，并把来源标成 `backfilled`
  （运行时实记的行是 `runtime`），让"一手事实"和"事后推断"在页面上分得开。

判据（按可靠性排序，只处理 channel IS NULL 的行）:
  R1 `INSTR(model_ref,'/')>0`      → anthropic-direct
     模型中心的引用形态是 `{provider}/{model_name}`，Qoder 网关的模型名里没有一个带斜杠。
     （用 INSTR 而不是 LIKE '%/%'：pymysql 会把裸 % 当占位符，带 params 的语句会直接报错。）
  R2 `cost_currency = 'USD'`       → anthropic-direct
     Qoder 通道的成本是按资源包单价折算的 CNY（由 OBSERVABILITY_CREDIT_COST_CURRENCY 决定），
     USD 只可能来自供应商直连的 CLI 上报。**跑前先校验该配置确实是 CNY**，否则这条判据不成立。
  R3 `credits IS NOT NULL`         → qoder-gateway
     只有 Qoder 网关会产生 credit；直连通道根本没有这个量。
  R4 `BINARY model_ref IN (清单)`  → qoder-gateway
     清单来自 `qodercli --list-models` + 历史用过的分级名（Auto/Efficient）。
     **必须用 BINARY 比较**：本表 collation 是 utf8mb4_0900_ai_ci（大小写不敏感），
     否则百炼的 `qwen3.7-plus` 会被误判成 Qoder 的 `Qwen3.7-Plus`。

不回填的情况（留在 NULL，页面显示"未记录"）:
  - `handled_by='intent_gate'`：前置意图层直答**根本没进执行层**，给它任何通道都是编数据。
    这类行由查询侧单独归入"未走执行层"桶展示，不占 channel 值域。
  - 模型名为空且无 credit/币种线索的失败或早期回合。

用法（repo 根目录）:
    venv/bin/python -m backend.scripts.backfill_trace_channel            # 只看计划与冲突
    venv/bin/python -m backend.scripts.backfill_trace_channel --apply    # 执行回填
"""

import argparse
import sys

from backend.common.db import execute_query, execute_write

CHANNEL_DIRECT = "anthropic-direct"
CHANNEL_QODER = "qoder-gateway"

# Qoder 网关在线模型名（`qodercli --list-models` 的当前清单 + 历史用过的分级名）。
# 大小写敏感匹配，改动时请同步 `qodercli --list-models` 的实际输出。
QODER_MODEL_NAMES = (
    "Auto", "Efficient", "Ultimate", "Performance",
    "Qwen3.8-Max", "Qwen3.8-Flash", "Qwen3.7-Max", "Qwen3.7-Plus", "Qwen3.7-Flash",
    "DeepSeek-V4-Pro", "DeepSeek-Flash", "DeepSeek-V4-Flash",
    "GLM-5.3", "GLM-5.3-Flash", "GLM-5.2", "Kimi-K3.1-Pro", "Kimi-K2.8-Preview",
    "Kimi-K2.7-Code", "Step-5-Preview", "MiniMax-M3.1-Flash", "MiniMax-M2.7",
)


def _count(sql, params=()):
    row = execute_query(sql, params, fetchone=True) or {}
    return int(row.get("c") or 0)


def precheck() -> list[str]:
    """判据成立的前提；不满足就返回原因列表（脚本拒绝写库）。"""
    from backend.common.config import OBSERVABILITY_CREDIT_COST_CURRENCY

    problems = []
    if (OBSERVABILITY_CREDIT_COST_CURRENCY or "CNY").upper() != "CNY":
        problems.append(
            f"R2 判据失效：OBSERVABILITY_CREDIT_COST_CURRENCY={OBSERVABILITY_CREDIT_COST_CURRENCY}，"
            "Qoder 通道的成本币种不再是 CNY，无法用 cost_currency='USD' 区分直连。"
            "请只依赖 R1/R3/R4（去掉 R2 的 SQL 条件）后重跑。")
    try:
        coll = (execute_query(
            "SELECT TABLE_COLLATION c FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_llm_traces'", fetchone=True) or {}).get("c", "")
        if coll and "_ci" in coll:
            print(f"  i18n 提醒: 表 collation={coll} 大小写不敏感，R4 已用 BINARY 比较规避")
    except Exception as e:  # noqa: BLE001 — 拿不到 collation 不阻断，BINARY 本身就是防御
        print(f"  无法读取表 collation（不影响 BINARY 判据）: {e}")
    return problems


def plan() -> dict:
    """各判据的命中数与冲突样本（dry-run 与 --apply 都先跑这个）。"""
    nullish = "channel IS NULL"
    direct_name = "INSTR(model_ref, '/') > 0"      # 带 provider 前缀 = 模型中心引用
    not_direct = "INSTR(model_ref, '/') = 0"
    usd = "(cost_currency IS NULL OR cost_currency <> 'USD')"
    buckets = {
        "R0 channel 已实记但缺来源标记 → runtime":
            _count("SELECT COUNT(*) c FROM adh_llm_traces WHERE channel IS NOT NULL AND channel_source IS NULL"),
        "R1 模型名带 provider 前缀 → direct":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate' "
                   f"AND {direct_name}"),
        "R2 成本币种 USD → direct":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate' "
                   f"AND {not_direct} AND cost_currency='USD'"),
        "R3 有 credit → gateway":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate' "
                   f"AND {not_direct} AND {usd} AND credits IS NOT NULL"),
        "R4 模型名命中 Qoder 清单 → gateway":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate' "
                   f"AND {not_direct} AND {usd} AND credits IS NULL AND BINARY model_ref IN (" +
                   ",".join(["%s"] * len(QODER_MODEL_NAMES)) + ")", QODER_MODEL_NAMES),
        "跳过：前置拦截（未走执行层）":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by='intent_gate'"),
        "留在 NULL：判据不足（不猜）":
            _count(f"SELECT COUNT(*) c FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate' "
                   f"AND {not_direct} AND {usd} AND credits IS NULL "
                   f"AND (model_ref='' OR BINARY model_ref NOT IN (" +
                   ",".join(["%s"] * len(QODER_MODEL_NAMES)) + "))", QODER_MODEL_NAMES),
    }
    # 冲突检查：同一行既像直连又像网关 → 不写，报告出来人工看
    conflicts = execute_query(
        f"""SELECT trace_id, started_at, model_ref, credits, cost_currency
            FROM adh_llm_traces WHERE {nullish} AND handled_by<>'intent_gate'
              AND {direct_name} AND credits IS NOT NULL LIMIT 10""") or []
    return {"buckets": buckets, "conflicts": conflicts}


def apply() -> list[str]:
    """按判据分批回填；每批一条 UPDATE，命中条件互斥（不重复覆盖）。"""
    actions = []
    base = "channel IS NULL AND handled_by<>'intent_gate'"
    direct_name = "INSTR(model_ref, '/') > 0"
    not_direct = "INSTR(model_ref, '/') = 0"
    usd = "(cost_currency IS NULL OR cost_currency <> 'USD')"
    ph = ",".join(["%s"] * len(QODER_MODEL_NAMES))

    # R0 来源标记补全：channel 的值只可能来自 recorder（运行时）或本脚本（会同时写 backfilled），
    # 所以“有 channel 但无来源”必然是 store 接线前的运行时行，标 runtime 是可靠推断不是猜。
    n = execute_write(
        "UPDATE adh_llm_traces SET channel_source='runtime' "
        "WHERE channel IS NOT NULL AND channel_source IS NULL")
    actions.append(f"R0 → channel_source=runtime: {n} 行")

    # R1 + R2 → anthropic-direct
    n = execute_write(
        f"""UPDATE adh_llm_traces SET channel=%s, channel_source='backfilled'
            WHERE {base} AND ({direct_name} OR cost_currency='USD')""",
        (CHANNEL_DIRECT,))
    actions.append(f"R1/R2 → {CHANNEL_DIRECT}: {n} 行")

    # R3 → qoder-gateway（有 credit 就一定是网关）
    n = execute_write(
        f"""UPDATE adh_llm_traces SET channel=%s, channel_source='backfilled'
            WHERE {base} AND channel IS NULL AND {not_direct}
              AND {usd} AND credits IS NOT NULL""",
        (CHANNEL_QODER,))
    actions.append(f"R3 → {CHANNEL_QODER}: {n} 行")

    # R4 → qoder-gateway（BINARY 精确匹配模型清单）
    n = execute_write(
        f"""UPDATE adh_llm_traces SET channel=%s, channel_source='backfilled'
            WHERE {base} AND channel IS NULL AND {not_direct}
              AND {usd} AND credits IS NULL
              AND BINARY model_ref IN ({ph})""",
        tuple([CHANNEL_QODER] + list(QODER_MODEL_NAMES)))
    actions.append(f"R4 → {CHANNEL_QODER}: {n} 行")
    return actions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回填历史回合的模型通道（dry-run 默认）")
    ap.add_argument("--apply", action="store_true", help="真正写库")
    args = ap.parse_args(argv)

    problems = precheck()
    if problems:
        for p in problems:
            print("前提不满足:", p)
        if args.apply:
            return 2
    p = plan()
    print("\n判据命中统计:")
    for k, v in p["buckets"].items():
        print(f"  {k:34s} {v:5d} 行")
    if p["conflicts"]:
        print("\n⚠ 判据冲突（既带 provider 前缀又有 credit）——这些行不会被回填，请人工看:")
        for c in p["conflicts"]:
            print("  ", {k: str(v)[:19] for k, v in c.items()})
    else:
        print("\n判据冲突: 无")
    if not args.apply:
        print("\n[dry-run] 未写库。确认后加 --apply。")
        return 0
    print()
    for line in apply():
        print("✓", line)
    left = _count("SELECT COUNT(*) c FROM adh_llm_traces WHERE channel IS NULL")
    print(f"\n仍为 NULL（未走执行层 + 判据不足）: {left} 行 —— 页面会分别显示"
          "「未走执行层」与「未记录」，不会混成一桶")
    return 0


if __name__ == "__main__":
    sys.exit(main())
