"""L1 编译层「行为路径 / 漏斗（查询数据集）」用例播种（幂等，默认 dry-run）。

期望值来源（不是照抄当前行为）
------------------------------
本表断言的是**设计契约**，每条都对应一条明确的规则：

1. 语义层 intent→plan 只会编译单层聚合，漏斗/路径的"按实体判序 → 再去重计数"
   由**查询数据集**（`bind_kind='dataset'`，正文是已治理的预制 SQL）承载；正文按
   **引擎**取变体：engine → family → generic，**缺实现即拒**，绝不把某引擎专有
   写法下发给别的引擎；
2. 数据集声明 `field_config`（维度/度量）后，其结果是一个可组合的 mini-MDL：
   `metrics`/`dimensions`/`filters`/`time_grain` 在声明字段上再编译一层聚合 ——
   这是"漏斗按渠道/日期/步骤下钻"的落地形状；
3. 未声明的名字一律回抛候选且提示只含业务名（宁缺勿错，护栏 §7 不泄露物理列）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/seed_eval_path_funnel_suite.py           # dry-run
  venv/bin/python backend/scripts/seed_eval_path_funnel_suite.py --apply

注意：用例跑在 compile 适配器上（语义目录被 `fixtures/demo_semantics` 打桩），
数据源约定 1=mysql / 4=doris / 5=sls 的 funnel 对象（均绑定数据集「转化漏斗」）；
**不反映真实本体与治理** —— 数据集触及物理表是否真被 RLS 覆盖由 L2/护城河用例负责。
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

SUITE = "compile"
_PARAMS_OK = {"start_date": "2024-01-01", "scene": "口扫"}


def _c(key, question, capability, intent, expected, note, *,
       status="active", status_note="", tags="", sort=0, ds=1):
    return {"case_key": key, "suite": SUITE, "question": question,
            "capability": capability, "case_status": status, "status_note": status_note,
            "tags": tags, "sort": sort, "note": note,
            "payload": {"datasource_id": ds, "intent": intent}, "expected": expected}


CASES: list[dict] = []

# ── 方言变体选择：engine → family → generic，缺实现即拒 ──────────
CASES += [
    _c("funnel_mysql_engine_compiles_dataset",
       "近30天商品到支付漏斗（MySQL 源）", "calc",
       {"object": "funnel", "params": _PARAMS_OK},
       {"ok": True, "sql_contains": ["t_case_records", "_ds_base"]},
       "数据集正文走 generic 实现；结果被包成派生表供外层治理/限流",
       tags="dataset,dialect", sort=10),
    _c("funnel_doris_engine_falls_to_generic_body",
       "近30天商品到支付漏斗（Doris 源，未单独写 Doris 实现）", "calc",
       {"object": "funnel", "params": _PARAMS_OK},
       {"ok": True, "sql_contains": ["t_case_records"]},
       "sql_bodies 无 doris 键时走 generic 正文（可移植写法），不是拒绝也不是猜别的方言",
       tags="dataset,dialect", sort=11, ds=4),
    _c("funnel_sls_engine_uses_its_own_body",
       "近30天商品到支付漏斗（SLS 源，PG 协议 Presto 风味）", "calc",
       {"object": "funnel", "params": _PARAMS_OK},
       {"ok": True, "sql_contains": ["observability_rum"]},
       "SLS 与 PG 同方言族但函数不同：必须取 sls 自己的正文，不得拿通用正文糊弄",
       tags="dataset,dialect,sls", sort=12, ds=5),
]

# ── 数据集输出可组合：按维度下钻（漏斗/路径分析的主用法）──────────
CASES += [
    _c("funnel_drilldown_by_step_composes_aggregate",
       "漏斗各步骤到达客户数按场景看", "calc",
       {"object": "funnel", "params": _PARAMS_OK,
        "metrics": ["到达实体数"], "dimensions": ["场景"]},
       {"ok": True,
        "sql_contains": ["COUNT(DISTINCT company_name)", "GROUP BY", "`场景`"],
        "resolution_source": {"到达实体数": "dataset_field", "场景": "dataset_field"}},
       "声明 field_config 后数据集结果可再聚合：一条数据集服务所有步骤/渠道/日期切片",
       tags="dataset,drilldown,combo", sort=20),
    _c("funnel_time_grain_over_dataset_output",
       "漏斗按月的到达客户数", "time",
       {"object": "funnel", "params": _PARAMS_OK,
        "metrics": ["到达实体数"], "dimensions": ["统计日期"], "time_grain": "month"},
       {"ok": True, "sql_contains": ["DATE_FORMAT"]},
       "时间粒度在数据集输出列上编译（与主路同一粒度模板表），不要求正文自己按月聚合",
       tags="dataset,time_grain", sort=21),
    _c("funnel_filter_on_declared_field",
       "只看漏斗第 2 步的到达客户数", "calc",
       {"object": "funnel", "params": _PARAMS_OK,
        "metrics": ["到达实体数"], "dimensions": ["场景"],
        "filters": [{"dim": "步骤序号", "op": "eq", "value": 2}]},
       {"ok": True, "sql_contains": ["`step_order` = 2", "GROUP BY"]},
       "非参数名的 filter 下推到外层 WHERE（谓词打在声明的输出列上，业务名只用于展示）",
       tags="dataset,filter", sort=22),
]

# ── 诚实性与脱敏（预制 SQL 通道的红线）───────────────────────────
CASES += [
    _c("funnel_missing_required_param_is_refused",
       "漏斗不传场景参数", "honesty",
       {"object": "funnel", "params": {"start_date": "2024-01-01"}},
       {"error_contains": "缺少参数"},
       "缺 required 参数必须拒执行，不得用默认值或空串凑出一条能跑的 SQL",
       tags="dataset,reject", sort=30),
    _c("funnel_undeclared_param_is_refused",
       "漏斗传一个数据集没声明的参数", "honesty",
       {"object": "funnel", "params": {**_PARAMS_OK, "uid": "abc"}},
       {"error_contains": "未在数据集声明"},
       "未声明键直接拒（参数面就是注入面，不接受「顺手多给一个」）",
       tags="dataset,reject", sort=31),
    _c("funnel_unresolved_metric_returns_business_names_only",
       "漏斗查一个不存在的指标", "leak",
       {"object": "funnel", "params": _PARAMS_OK,
        "metrics": ["转化率提升幅度"], "dimensions": ["场景"]},
       {"error_contains": "无法解析指标", "hint_no_physical": True},
       "数据集未声明的名字不得被猜成物理列；提示只出业务名清单",
       tags="dataset,unresolved,no_leak", sort=32),
    _c("funnel_intent_rejects_raw_sql",
       "把裸 SQL 塞进漏斗意图", "leak",
       {"object": "funnel", "sql": "SELECT window_funnel(1800,'strict')(ts) FROM t",
        "params": _PARAMS_OK},
       {"intent_rejected": True},
       "数据集通道同样不吃裸 SQL：SQL 只能来自已注册的数据集正文",
       tags="dataset,reject", sort=33),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="落库（默认只报告）")
    args = ap.parse_args()

    from backend.eval import store
    from backend.eval.contract import CaseContractError, normalize_case

    keys = [c["case_key"] for c in CASES]
    if len(set(keys)) != len(keys):
        print("✗ 播种表里有重复 case_key")
        return 1

    for c in CASES:
        try:
            normalize_case({**c, "id": 0, "is_active": 1})
        except CaseContractError as exc:
            print(f"✗ 用例 {c['case_key']} 不合法: {exc}")
            return 1

    by_cap: dict[str, int] = {}
    for c in CASES:
        by_cap[c["capability"]] = by_cap.get(c["capability"], 0) + 1
    print(f"行为路径/漏斗编译用例 {len(CASES)} 条；按能力契约：{by_cap}")

    if not args.apply:
        print("\n(dry-run，未落库。确认 demo 夹具与期望一致后再 --apply)")
        return 0

    created = skipped = 0
    for c in CASES:
        if store.get_case_by_key(SUITE, c["case_key"]):
            skipped += 1
            continue
        store.create_case(c, created_by="seed_eval_path_funnel_suite")
        created += 1
    print(f"已新建 {created} 条，已存在跳过 {skipped} 条（不覆盖人工改过的用例）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
