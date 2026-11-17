"""评测用例分层 v2 回填：逐条登记 capability 与 case_status（幂等, 默认 dry-run）。

为什么不用 tag 启发式推断
------------------------
capability 是"这条用例主要守住哪项用户可感知能力"的唯一裁决口径，覆盖矩阵据此
反推缺口。按 tag 猜会把"tags 里同时有 time_window 和 alias"这类多信号用例塞错组，
错的组会让人**误判哪块能力没被覆盖**——比空着更坏（空着至少诚实）。
所以下面按 case_key 逐条显式指定，脚本只认这张表：

* DB 里有表里没有的用例 → 报错（不默认成任何组）；
* 表里有 DB 里没有的键 → 报错（防止改名后静默漏登记）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/backfill_eval_capability.py           # dry-run 全表
  venv/bin/python backend/scripts/backfill_eval_capability.py --apply   # 落库

case_status 同步登记（见下方 RETIRED / KNOWN_DEFECT）：
  retired      —— 用例针对的形态在本平台已不存在，不跑不入分母，但原因必须可见；
  known_defect —— 被测链路真有缺陷，继续跑、继续拖累门禁下限，逼着修（不许藏）。
  二者均必须带 status_note：没有原因的退役等于把失败扫到地毯下。
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

# ── capability 逐条登记（suite -> {case_key: capability}）────────────────────
CAPABILITY_BY_KEY: dict[str, dict[str, str]] = {
    "compile": {
        # 仪表盘设计链路
        "dashboard_case_7d_confirmed": "time",
        "dashboard_system_scope_cannot_resolve_business": "honesty",
        "dashboard_other_domain_same_name": "routing",
        "dashboard_agent_raw_sql_rejected": "honesty",
        # 定时任务链路
        "scheduled_waker_bound_business_intent": "resolution",
        "scheduled_mcp_raw_sql_rejected": "honesty",
        "scheduled_waker_system_cannot_bind_business": "honesty",
        # 名字/别名/枚举/物理业务名 的解析档位
        "m_exact_casecount": "resolution",
        "d_exact_hospital": "resolution",
        "m_exact_en": "resolution",
        "d_exact_en": "resolution",
        "m_alias_bingli": "resolution",
        "m_alias_totalfee": "resolution",
        "d_alias_hospital": "resolution",
        "d_alias_createdtime": "resolution",
        "d_alias_en": "resolution",
        "m_alias_en_total": "resolution",
        "d_physdesc_amount": "resolution",
        "d_physcol_rawname": "resolution",
        "m_physcol_sumfallback": "resolution",
        "enum_filter_label": "resolution",
        "enum_filter_code": "resolution",
        "enum_dim_display": "resolution",
        "enum_filter_in_labels": "resolution",
        # 相对时间与粒度
        "time_range_last_week": "time",
        "time_range_this_month": "time",
        "time_range_precedence_over_window": "time",
        "time_range_invalid_token_rejected": "honesty",
        "tw_7d": "time",
        "tw_24h_col": "time",
        "tw_2w": "time",
        "tw_pg_dialect": "time",
        "grain_month": "time",
        "grain_day": "time",
        "ds2_time_window": "time",
        "compile_ana_case_trend": "time",
        # 跨源
        "xsrc_ds1": "routing",
        "xsrc_ds2": "routing",
        # 计算形状：组合/JOIN/窗口/派生/近似/百分位/排序分页过滤/模板
        "tpl_ok": "calc",
        "order_desc": "calc",
        "limit_val": "calc",
        "combo_metric_dim": "calc",
        "ds2_all": "calc",
        "multi_metric": "calc",
        "order_by_dim_asc": "calc",
        "filter_like": "calc",
        "join_three_hop_chain": "calc",
        "join_cross_object_dim": "calc",
        "join_two_hops": "calc",
        "having_gt_threshold": "calc",
        "approx_distinct_count_distinct": "calc",
        "percentile_pg_renders": "calc",
        "derived_metric_ratio": "calc",
        "nested_agg_count_above_threshold": "calc",
        "nested_outer_reaggregate_having": "calc",
        "compile_ana_hospital_comparison": "calc",
        "window_rank": "calc",
        "window_rolling": "calc",
        "window_period_compare": "calc",
        "window_share": "calc",
        # 诚实性：该拒就拒、无路径不猜、方言不支持时 fail-loud 不降级
        "tpl_missing_param": "honesty",
        "tpl_unknown_param": "honesty",
        "unbound_device": "honesty",
        "reject_raw_sql": "honesty",
        "fuzzy_status": "honesty",
        "percentile_mysql_fails_loud": "honesty",
        "derived_metric_bare_col_rejected": "honesty",
        "join_no_path_rejected": "honesty",
        "window_missing_time_rejected": "honesty",
        # 回抛候选文案不得含物理细节
        "unknown_metric": "leak",
        "unknown_dim": "leak",
        "unknown_filter": "leak",
    },
    "retrieval": {
        "retrieval_no_physical_leak": "leak",
        "retrieval_result_bounded": "recall",
        "retrieval_empty_keywords_returns_safely": "honesty",
        "route_hit_ai_conversation": "routing",
        "route_hit_multi_objects_one_source": "routing",
        "route_no_hit_no_guess": "routing",
        "route_hit_ds_node": "routing",
        "route_hit_multi_nodes_one_source": "routing",
        "retrieval_sys_ops_modeling_progress": "honesty",
        "retrieval_sys_ops_approval": "honesty",
        "retrieval_sys_ops_contract_change": "honesty",
        "retrieval_sys_ops_dashboard_publish": "honesty",
        "graph_traversal_hit": "recall",
        "graph_traversal_no_seed": "recall",
    },
    "llm": {
        "llm_datasource_list_safe_no_credentials": "leak",
        "llm_scheduled_task_list_no_webhook_secret": "leak",
        "llm_report_list_no_share_token": "leak",
        "llm_quality_rule_list_no_physical_table": "leak",
        "llm_dataset_list_no_sql_text": "leak",
        "llm_secret_tool_never_called": "honesty",
        "llm_write_requires_user_confirm": "clarify",
        "llm_write_executes_after_confirm": "clarify",
        "llm_blocked_reason_reported_not_silent": "honesty",
    },
}

# ─ 退役（用例针对的形态在本平台已不存在；不跑、不入分母、原因可见）──────────
RETIRED: dict[str, str] = {
    "route_hit_ai_conversation": (
        "断言的「业务本体=数据资产路由图」形态已废弃：active 业务本体对象为 "
        "user/company/case/…/ai_conversation，无 ds_*/dataset_* 节点且无任何 route；"
        "resolve_business_routes 实测返回 []。routing 契约按当前 canonical 重写。"),
    "route_hit_ds_node": (
        "同上：ds_test_alb/test-alb/联耀业务本体 的路由断言针对已废弃形态。"),
    "route_hit_multi_nodes_one_source": (
        "同上：dataset_1 等数据资产节点已不在业务本体 canonical 中。"),
    "route_hit_multi_objects_one_source": (
        "同上：多节点同源断言依赖已废弃的数据资产路由图。"),
    "retrieval_sys_ops_modeling_progress": (
        "与 as-bot-system-waker §1 冲突：系统运营问题必须由 system 工具组回答，"
        "不得以元数据检索命中为验收。改判 L4（断言走 system 工具轨迹）。"),
    "retrieval_sys_ops_approval": (
        "同上：待审批动作属系统运营问题，应由 system 工具组而非 RAG 检索命中。"),
    "retrieval_sys_ops_contract_change": (
        "同上：契约变更影响面属系统运营问题，应由 system 工具组回答。"),
    "retrieval_sys_ops_dashboard_publish": (
        "同上：发布条件属系统运营问题，应由 system 工具组回答。"),
}

# ─ 已知缺陷（被测链路真有洞：继续跑、继续拖累下限，逼着修）─────────────────
KNOWN_DEFECT: dict[str, str] = {
    "graph_traversal_hit": (
        "实测 rag_source=ontology_traversal:no_seed：关键词「案例」未被抽成图遍历种子。"
        "缺口在 关键词→图种子 抽取契约，不是图里没数据。"),
}


def _load_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, suite, case_key, capability, case_status, status_note "
                    "FROM adh_eval_cases ORDER BY suite, sort, id")
        return list(cur.fetchall())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只报告）")
    args = ap.parse_args()

    from backend.common.db.metadata_db import get_metadata_conn
    from backend.eval.gate import CAPABILITIES

    expected = {
        (suite, key): cap
        for suite, m in CAPABILITY_BY_KEY.items() for key, cap in m.items()
    }
    for cap in expected.values():
        if cap not in CAPABILITIES:
            print(f"✗ 登记表里有非法契约组 {cap!r}，应为 {list(CAPABILITIES)}")
            return 1

    # 状态表只写了 case_key，所属层从契约登记表反推；跨层重名直接报错而不是猜。
    suite_of = {k: s for s, m in CAPABILITY_BY_KEY.items() for k in m}

    def _to_pairs(src: dict[str, str], label: str) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for k, v in src.items():
            hits = {s for s, m in CAPABILITY_BY_KEY.items() if k in m}
            if not hits:
                print(f"✗ {label} 里的 {k!r} 不在任何层的契约登记表里（先登记再标状态）")
                raise SystemExit(1)
            if len(hits) > 1:
                # 同一 case_key 出现在多层：光靠 case_key 无法定层，必须改成显式 (suite, key)
                print(f"✗ {label} 里的 {k!r} 跨层重名 {sorted(hits)}，"
                      f"请改为 (suite, case_key) 键后再执行")
                raise SystemExit(1)
            out[(suite_of[k], k)] = v
        return out

    retired_pairs = _to_pairs(RETIRED, "RETIRED")
    defect_pairs = _to_pairs(KNOWN_DEFECT, "KNOWN_DEFECT")

    conn = get_metadata_conn()
    try:
        rows = _load_rows(conn)
    finally:
        conn.close()

    actual = {(r["suite"], r["case_key"]) for r in rows}
    missing = sorted(actual - set(expected))
    extra = sorted(set(expected) - actual)
    if missing:
        print("✗ DB 里有用例未在 CAPABILITY_BY_KEY 登记（不猜，请人工指定）：")
        for s, k in missing:
            print(f"    {s}/{k}")
        return 1
    if extra:
        print("✗ 登记表里有 DB 中不存在的用例键（改名/删除后需同步本表）：")
        for s, k in extra:
            print(f"    {s}/{k}")
        return 1

    changes: list[tuple[int, str, str, str, str]] = []
    for r in rows:
        suite, key = r["suite"], r["case_key"]
        cap = expected[(suite, key)]
        # 状态表按 (suite, case_key) 寻址：不同层可能重名，不允许靠 case_key 全局推定。
        pair = (suite, key)
        if pair in retired_pairs:
            status, note = "retired", retired_pairs[pair]
        elif pair in defect_pairs:
            status, note = "known_defect", defect_pairs[pair]
        else:
            # 未显式登记状态的用例一律回到 active 并清空原因，保证幂等（重跑结果一致）
            status, note = "active", ""
        if r["capability"] != cap or r["case_status"] != status \
                or (r["status_note"] or "") != note:
            changes.append((int(r["id"]), cap, status, note, f"{suite}/{key}"))

    print(f"共 {len(rows)} 条用例，需写入 {len(changes)} 条"
          f"（retired {len(retired_pairs)} / known_defect {len(defect_pairs)}）")
    for _id, cap, status, note, label in changes:
        print(f"  {label:<48} -> {cap:<11} {status:<13}"
              f"{' | ' + note[:44] + '…' if note else ''}")

    if not args.apply:
        print("\n(dry-run，未落库；确认无误后加 --apply)")
        return 0
    if not changes:
        print("已是目标状态，无需写入。")
        return 0

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for _id, cap, status, note, _label in changes:
                cur.execute(
                    "UPDATE adh_eval_cases SET capability=%s, case_status=%s, status_note=%s "
                    "WHERE id=%s", (cap, status, note, _id))
        conn.commit()
    finally:
        conn.close()
    print(f"已写入 {len(changes)} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
