"""L1 编译层 ES（cloud-platform-es）用例播种（幂等, 默认 dry-run, --apply 落库）。

为什么需要这一组：`planner`/`dialect` 新增了 ES 方言族（对象编译族，双引号标识符 +
定长切片时间桶 + 强制 LIMIT + 不可表达形态拒绝）。建模规范要求「新增解析/编译行为
分支必须同步新增 eval 用例」，否则这条能力只有单测覆盖、进不了发布门禁。

数据来自**实机取证**（2026-11-18 对 cloud-platform-es 四张运行日志索引的探针）：

* 业务时间/分类列都是 `xxx.keyword` 文本列 —— 列引用必须整体加双引号，
  否则 mysql 生成器会把 `"a.keyword"` 渲染成字符串字面量（语法能过、结果全错）；
* 引擎没有 `NOW()`/`INTERVAL`/`DATE_FORMAT` —— 时间窗边界在服务端算好下推，
  时间桶只能是 `SUBSTRING(CAST(col AS VARCHAR), 1, n)`；
* 必须显式 `LIMIT`（force_limit 关闭也要补，并留痕），季度粒度不可表达、必须拒绝而不是
  退化成日桶或原列（宁缺勿错）。

用例跑在离线 fixture（`backend/eval/fixtures/demo_semantics.py` 的 ES 源打桩）上：
不进真实库、不下发数据源，只锁编译形态。真机取数链路另由
`logs/probe_es_semantic_e2e.py` 与建模脚本 verify 覆盖。

用法（repo 根目录）:
  venv/bin/python backend/scripts/seed_eval_es_compile_suite.py           # dry-run
  venv/bin/python backend/scripts/seed_eval_es_compile_suite.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

SUITE = "compile"
DS_ID = 6                      # cloud-platform-es（demo_semantics.DATASOURCES）
OBJ = "cloud_es_equipment_operation_log"


def _c(key, question, capability, intent, expected, note, *, tags="", sort=0):
    return {"case_key": key, "suite": SUITE, "question": question,
            "capability": capability, "case_status": "active", "status_note": "",
            "tags": tags, "sort": sort, "note": note,
            "payload": {"intent": intent, "datasource_id": DS_ID},
            "expected": expected}


CASES: list[dict] = [
    _c("es_time_grain_day_slice",
       "云平台设备操作日志按天看操作次数",
       "time",
       {"object": OBJ, "metrics": ["设备操作次数"],
        "dimensions": ["设备操作时间"], "time_grain": "day", "limit": 500},
       {"ok": True,
        "sql_contains": ['SUBSTRING(CAST("operationTime.keyword" AS VARCHAR), 1, 10)',
                         "GROUP BY"]},
       "ES 无 DATE_FORMAT/to_char：天桶只能是定长字符串切片；退回 mysql 写法会直接 400。"
       "同时锁住时间维度必须同时出现在 SELECT 与 GROUP BY（只投影不分组会串桶）",
       tags="time_grain,es", sort=10),

    _c("es_mandatory_limit_with_trace",
       "云平台设备操作总次数",
       "calc",
       {"object": OBJ, "metrics": ["设备操作次数"]},
       {"ok": True, "error_contains": "必须带 LIMIT",
        "sql_contains": ["LIMIT 1000"]},
       "binding 的 force_limit 是关闭的、intent 也没给 limit，但 ES 引擎硬要求 LIMIT："
       "planner 必须自行补齐并在 warnings 留痕。补不上/不补都是把全表扫描留给引擎超时",
       tags="calc,limit,es", sort=11),

    _c("es_keyword_dotted_column_quoted",
       "云平台按操作类型看涉及设备数",
       "calc",
       {"object": OBJ, "metrics": ["设备操作设备数"], "dimensions": ["操作类型"],
        "limit": 100},
       {"ok": True,
        "sql_contains": ['FROM "t_equipment_operation_log"',
                         'COUNT(DISTINCT "serialNumber.keyword")',
                         '"action.keyword"']},
       "带点 keyword 列必须整体加双引号（含表名）：mysql 生成器会把双引号内容当字面量，"
       "实测这类错编译不报错、返回 0 行",
       tags="calc,identifier,es", sort=12),

    _c("es_conditional_code_literal_kept",
       "云平台扫码操作次数",
       "calc",
       {"object": OBJ, "metrics": ["扫描操作次数"], "limit": 100},
       {"ok": True,
        "sql_contains": ["SUM(CASE WHEN \"action.keyword\" = 'scan' THEN 1 ELSE 0 END)"]},
       "公式里的 'scan' 是码值字面量：给它加双引号会让条件恒假（静默返回 0），"
       "这类错数比报错危险，锁成门禁",
       tags="calc,formula,es", sort=13),

    _c("es_quarter_grain_rejected",
       "云平台设备操作日志按季度看操作次数",
       "time",
       {"object": OBJ, "metrics": ["设备操作次数"],
        "dimensions": ["设备操作时间"], "time_grain": "quarter", "limit": 100},
       {"error_contains": "季度"},
       "季度粒度在 ES 不可表达：必须编译期拒绝并说明原因，不得退化成日桶或原列充数"
       "（no-silent-degradation：凑合出来的结果是错数）",
       tags="time_grain,reject,es", sort=14),

    _c("es_metric_and_dim_dict_exact",
       "云平台接口按告警级别看操作次数",
       "resolution",
       {"object": OBJ, "metrics": ["设备操作次数"], "dimensions": ["告警级别"],
        "limit": 100},
       {"ok": True,
        "resolution_source": {"设备操作次数": "dict_exact", "告警级别": "dict_exact"},
        "sql_contains": ["GROUP BY"]},
       "ES 源与其它族走同一套字典解析档位：对象换了方言，解析优先级不得漂移"
       "（否则同一问题在 ES 上会落到别名/物理列兜底，口径悄悄变）",
       tags="resolution,es", sort=15),
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

    # 先做契约校验：不合法的用例（未知期望键 / 缺 capability）在写库前暴露，
    # 否则落库后每次运行都产一堆 invalid_cases
    for c in CASES:
        try:
            normalize_case({**c, "id": 0, "is_active": 1})
        except CaseContractError as exc:
            print(f"✗ 用例 {c['case_key']} 不合法: {exc}")
            return 1

    # 用离线适配器先跑一遍：播种前就证明这些用例当前能过，
    # 免得把"我以为是绿的"写进分母（新增用例失败会立刻污染门禁）
    from backend.eval.contract import Case
    from backend.eval.adapters import compile as compile_adapter
    from backend.eval.contract import check
    bad = 0
    for c in CASES:
        case = Case(case_key=c["case_key"], suite=SUITE, question=c["question"],
                    payload=c["payload"], expected=c["expected"],
                    capability=c["capability"], case_status=c["case_status"],
                    tags=[t for t in (c["tags"] or "").split(",") if t])
        observed = compile_adapter.run_case(case)
        ok, reason = check(SUITE, observed, case.expected)
        print(("  ✓ " if ok else "  ✗ ") + c["case_key"] + ("" if ok else f" -> {reason}"))
        if not ok:
            bad += 1
            print("    SQL:", (observed.get("sql") or "")[:400])
            print("    warnings:", observed.get("warnings"))
    if bad:
        print(f"\n✗ {bad} 条用例当前无法通过，拒绝落库（先修好再播种）")
        return 1

    if not args.apply:
        print(f"\n(dry-run，未落库；{len(CASES)} 条全部通过离线编译，"
              f"确认无误后加 --apply)")
        return 0

    created, skipped = 0, 0
    for c in CASES:
        if store.get_case_by_key(SUITE, c["case_key"]):
            skipped += 1
            continue
        store.create_case(c, created_by="seed_eval_es_compile_suite")
        created += 1
    print(f"已新建 {created} 条，已存在跳过 {skipped} 条（幂等：不覆盖人工改过的用例）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
