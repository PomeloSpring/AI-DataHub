"""L2 真实语义层评测集播种（幂等, 默认 dry-run, --apply 落库）。

这些用例的期望值全部来自**在真实本体/字典/治理配置上实测的产物**，不是照抄当前行为：

* 已通过的行为 → 期望写"应有的结果"，跑通即锁住不回退；
* 已确认的缺陷 → 期望写**规则要求的结果**（而非今天实际的错误结果），并登记
  ``case_status='known_defect'`` + 原因。known_defect 计入门禁分母、继续拖累下限，
  这样缺陷不会因"用例改成迁就现状"而被洗白（no-silent-degradation §1）。

用法（repo 根目录）:
  venv/bin/python backend/scripts/seed_eval_semantic_suite.py           # dry-run
  venv/bin/python backend/scripts/seed_eval_semantic_suite.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

SUITE = "semantic"
DS = "prd-cn"          # 真实业务源（mysql）；跨源用例另用 prd-jp / Doris
DS_JP = "prd-jp"
DS_DORIS = "Doris"
ADMIN = {"username": "admin"}          # 服务端按用户名解析 id，用例不写内部 id
ANALYST = {"username": "zhangsan"}     # 非 admin，用于证明敏感基线不豁免


def _c(key, question, capability, intent, expected, note, *, ds=DS,
       identity=ADMIN, status="active", status_note="", tags="", sort=0):
    return {"case_key": key, "suite": SUITE, "question": question,
            "capability": capability, "case_status": status, "status_note": status_note,
            "tags": tags, "sort": sort, "note": note,
            "payload": {"intent": intent, "datasource_name": ds, "identity": identity},
            "expected": expected}


CASES: list[dict] = []

# ── resolution：名字解析档位（本层的核心可观测面）────────────────────
CASES += [
    _c("sem_res_metric_dict_exact", "案例数量是多少", "resolution",
       {"object": "case", "metrics": ["案例数量"], "limit": 10},
       {"allowed": True, "bound_object": "case", "binding_source": "bindings",
        "resolution_source": {"案例数量": "dict_exact"}},
       "真实字典精确名必须命中 dict_exact 档，且经真实 binding 落到本体对象 case",
       tags="resolution,exact", sort=10),

    _c("sem_res_metric_name_en", "case_count 是多少", "resolution",
       {"object": "case", "metrics": ["case_count"], "limit": 10},
       {"allowed": True, "resolution_source": {"case_count": "dict_exact"}},
       "字典 name_en 是合法权威名，解析档位应与中文名同级（不因英文写法降级）",
       tags="resolution,english", sort=11),

    _c("sem_res_dimension_dict_alias", "按医院名称看案例数量", "resolution",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["医院名称"], "limit": 10},
       {"allowed": True, "resolution_source": {"医院名称": "dict_alias"},
        "sql_contains": ["company_name"]},
       "别名必须落 dict_alias 档并解析到维度 target_column；provenance 要记档位（护栏 §7 可追溯）",
       tags="resolution,alias", sort=12),

    _c("sem_res_dimension_phys_col", "按 create_time 看案例数量", "resolution",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["create_time"], "limit": 10},
       {"allowed": True, "resolution_source": {"create_time": "phys_col"}},
       "物理列名兜底档必须被标注为 phys_col：它是最低优先档，档位不标注就无法审计口径漂移",
       tags="resolution,phys_col", sort=13),

    _c("sem_res_registered_alias_must_resolve", "病例数是多少", "resolution",
       {"object": "case", "metrics": ["病例数"], "limit": 10},
       {"allowed": True, "resolution_source": {"病例数": "dict_alias"}},
       "「病例数」已登记为「案例数量」的别名，按 ontology-modeling §7 必须解析命中",
       status="known_defect",
       status_note=("adh_metrics.aliases 里「案例数量」的 JSON 被双重编码（元素本身又是字符串化的"
                    "数组，如 '[\"case数\", ...]'），别名解析读不到「病例数」，实测落 "
                    "unresolved + preflight 拒收。属字典数据缺陷，非解析层设计问题。"),
       tags="resolution,alias,data_defect", sort=14),
]

# ── time：相对时间与粒度 ─────────────────────────────────────
CASES += [
    _c("sem_time_window_7d_rolls_to_date_sub", "最近7天案例数量", "time",
       {"object": "case", "metrics": ["案例数量"], "time_window": "7d", "limit": 10},
       {"allowed": True, "sql_contains": ["INTERVAL 7 DAY"]},
       "相对时间必须由 time_window 编译出滚动窗口；不允许 LLM 手算绝对日期（护栏 §7）",
       tags="time,time_window", sort=20),

    _c("sem_time_calendar_last_week_not_rolling", "上周案例数量", "time",
       {"object": "case", "metrics": ["案例数量"], "time_range": "last_week",
        "time_column": "创建日期", "limit": 10},
       {"allowed": True, "sql_contains": ["WEEKDAY(CURDATE())"]},
       "日历区间（上周）优先于滚动窗口，不得把 last_week 悄悄降级成 7d",
       tags="time,calendar", sort=21),

    _c("sem_time_grain_month_groups_by_grain", "按月看案例数量", "time",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["创建日期"],
        "time_grain": "month", "limit": 10},
       {"allowed": True, "sql_contains": ["DATE_FORMAT", "GROUP BY"]},
       "粒度必须同时进 SELECT 与 GROUP BY；只格式化不分组会把月度趋势算成一个总数",
       tags="time,time_grain", sort=22),
]

# ── routing：跨源与未命中不猜 ─────────────────────────────────
CASES += [
    _c("sem_route_cross_site_same_object_binds_locally", "日本站点案例数量", "routing",
       {"object": "case", "metrics": ["案例数量"], "limit": 10},
       {"allowed": True, "bound_object": "case",
        "resolution_source": {"案例数量": "dict_exact"}},
       note="同名对象在不同数据源各自绑定本源字典；换源不得回退到别的源的对象（跨源时串味）",
       ds=DS_JP, tags="routing,cross_source", sort=30),

    _c("sem_route_datasource_without_binding_stays_unbound", "Doris 上的案例数量", "routing",
       {"object": "case", "metrics": ["案例数量"], "limit": 10},
       {"unbound": True},
       note="该源没有本体绑定行时必须显式 unbound，不得借用其它源的绑定结果充数（宁缺勿错）",
       ds=DS_DORIS, identity={}, tags="routing,unbound", sort=31),
]

# ── honesty：该拒就拒、丢字段不能静默 ───────────────────────────
CASES += [
    _c("sem_hon_intent_carrying_sql_is_rejected", "把这段 SQL 当语义查询跑", "honesty",
       {"object": "case", "sql": "SELECT 1"},
       {"intent_rejected": True, "reject_reason_contains": "不得携带 SQL"},
       note="语义 intent 不接受裸 SQL（护栏 §6：两条路径不得互串），拒收必须发生在解析层",
       identity={}, tags="honesty,guardrail,reject", sort=40),

    _c("sem_hon_site_dimension_rejected_not_dropped", "按站点统计案例数量", "honesty",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["站点"], "limit": 10},
       {"intent_rejected": True, "reject_reason_contains": "站点不是 SQL 维度"},
       note="站点不是 SQL 维度：必须显式拒收并指引按源编排，而不是丢掉该维度后给出去重口径的总数",
       identity={}, tags="honesty,reject,routing", sort=41),

    _c("sem_hon_unknown_object_no_guess", "查询一个未建模的概念", "honesty",
       {"object": "不存在对象xyz", "metrics": ["案例数量"], "limit": 10},
       {"unbound": True},
       note="未命中对象不得猜绑定；route_none 与 failed 必须可区分（本层用 unbound 表达）",
       identity={}, tags="honesty,unbound", sort=42),

    _c("sem_hon_metric_from_other_object_rejected", "设备对象上用案例指标", "honesty",
       {"object": "equipment", "metrics": ["案例数量"], "limit": 10},
       {"allowed": False, "blocked_at": "preflight",
        "unresolved_terms_contains": ["案例数量"]},
       "指标属于别的对象时不得跨对象借用编译，须显式失败并回抛未解析项",
       tags="honesty,scope", sort=43),

    _c("sem_hon_dropped_dimension_must_not_answer", "按状态分布看案例数量", "honesty",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["状态分布"], "limit": 10},
       {"allowed": False},
       note="请求的维度解析失败时必须显式失败或回抛候选，不能丢维度后给总量",
       status="known_defect",
       status_note=("实测缺陷：维度「状态分布」解析失败后被静默丢弃，闸门仍放行并产出一个"
                    "不带任何分组的总数（SQL 里既无该维度也无 GROUP BY）。用户问的是分布，"
                    "拿总量作答属口径事故，违反 ontology-modeling §7「模糊只回抛候选，"
                    "绝不静默绑定」与 no-silent-degradation。正解：请求的维度被丢弃时，"
                    "闸门应拒绝或回抛候选，而不是给一个看起来成功的答案。"),
       tags="honesty,fuzzy,silent_drop", sort=44),
]

# ── governance：敏感基线与行级过滤的实际施加 ──────────────────────
CASES += [
    _c("sem_gov_sensitive_block_rejected_for_analyst", "按 user_name 分组看案例数量", "governance",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["user_name"], "limit": 10},
       identity=ANALYST,
       expected={"allowed": False, "blocked_at": "permission",
                 "blocked_columns_rejected": True,
                 "block_reason_contains": "敏感合规屏蔽列",
                 "governance_verified": True},
       note="block 列被显式请求即拒（护栏 §3 第 1 层敏感基线），且必须给出可诊断拒绝原因",
       tags="governance,sensitive_block", sort=50),

    _c("sem_gov_sensitive_block_not_exempt_for_admin", "admin 按 user_name 分组", "governance",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["user_name"], "limit": 10},
       expected={"allowed": False, "blocked_at": "permission",
                 "blocked_columns_rejected": True,
                 "governance_verified": True},
       note="admin 旁路**不含**敏感脱敏基线（护栏 §3.3）：这条用例专门锁住"
            "「误把 admin 当豁免」的回归",
       tags="governance,sensitive_block,admin", sort=51),

    _c("sem_gov_sensitive_baseline_loaded_for_normal_query", "按病人性别看案例数量", "governance",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["patient_sex"], "limit": 10},
       identity=ANALYST,
       expected={"allowed": True, "governance_verified": True,
                 "governance_bucket": "applied",
                 "rls_policies_contains": ["sensitive_fields:", "sensitive_block:"]},
       note="普通取数也要真实挂上敏感基线（策略名以 sensitive_fields:/sensitive_block: 前缀呈现，"
            "用例只断言前缀，不抄列名）",
       tags="governance,mask", sort=52),

    _c("sem_gov_no_identity_must_not_claim_rls_verified", "无身份时不得自称验证过行级权限", "governance",
       {"object": "case", "metrics": ["案例数量"], "limit": 10},
       identity={},
       expected={"allowed": False, "blocked_at": "identity"},
       note="缺身份 → 闸门在 identity 段拒绝（护栏 §2 fail-closed）。本条同时保护 L2 自身的"
            "诚实性：无身份时治理段是 unverified，任何 rls_applied 断言都不得判为通过",
       tags="governance,identity,fail_closed", sort=53),

    _c("sem_gov_row_filter_applies_on_bound_table", "带行级过滤的中文区案例数量", "governance",
       {"object": "case", "metrics": ["案例数量"], "limit": 10},
       expected={"rls_applied": True, "governance_verified": True,
                 "secured_differs_from_base": True},
       note="行级过滤必须能在真实本体配置上实跑验证（护栏 §4 要求保留别名并覆盖 FROM/JOIN 两侧）",
       status="known_defect",
       status_note=("行级权限目前**无法在语义层回归验证**：adh_rls_policies 里生效的两条策略"
                    "分别指向 dwd_t_case_records_all 与 t_equipment_operation_shadow，而 "
                    "adh_ontology_bindings 中没有任何对象绑定到这两张表，闸门因此整段跳过 "
                    "row_filter 注入。RLS 注入正确性（护栏 §4：保留别名、覆盖 FROM/JOIN 两侧）"
                    "只有 tests/ 单测覆盖，真实本体配置上没有端到端证据。"
                    "修法：给某个对象绑定到带 RLS 策略的表，再让本条转为 active。"),
       tags="governance,rls,coverage_gap", sort=54),
]

# ── leak：回抛文案不得含物理细节 ──────────────────────────────
CASES += [
    _c("sem_leak_unresolved_hint_has_no_physical_detail", "利润率是多少", "leak",
       {"object": "case", "metrics": ["利润率"], "limit": 10},
       {"allowed": False, "unresolved_terms_contains": ["利润率"],
        "hint_no_physical": True},
       note="未解析提示只能出现业务名候选与字典清单，不得含物理表/列名（护栏 §7）",
       tags="leak,no_leak,unresolved", sort=60),

    _c("sem_leak_sources_bucket_has_no_physical_token", "闸门分桶只暴露业务可读档位", "leak",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["医院名称"], "limit": 10},
       {"allowed": True, "gate_bucket": "gate_allowed", "hint_no_physical": True},
       note="sources 分桶是会被页面与报告直接渲染的字段，只能放档位/闸门名，"
            "不得放表名/列名/连接信息（本层适配器已按此约束实现，本条锁住不回退）",
       tags="leak,sources", sort=61),
]

# ── calc：真实字典下的计算形状 ────────────────────────────────
CASES += [
    _c("sem_calc_multi_metric_one_dimension", "各医院案例数与案例存储量", "calc",
       {"object": "case", "metrics": ["案例数量", "total_case_storage_gb"],
        "dimensions": ["医院名称"], "limit": 10},
       {"allowed": True, "sql_contains": ["COUNT(", "SUM(occupied_space_size)", "GROUP BY"]},
       note="多指标 + 维度在真实字典下要同时编译进 SELECT 与 GROUP BY；"
            "派生/存储量类指标最容易被静默跳过，本条锁住不丢",
       tags="calc,combo", sort=70),

    _c("sem_calc_order_desc_limit_compiled", "案例数量最多的前10家医院", "calc",
       {"object": "case", "metrics": ["案例数量"], "dimensions": ["医院名称"],
        "order": [{"by": "案例数量", "desc": True}], "limit": 10},
       {"allowed": True, "sql_contains": ["ORDER BY", "DESC", "LIMIT 10"]},
       note="排序方向与分页必须由 intent 编译出来；缺 ORDER BY 会把「排行」答成任意顺序",
       tags="calc,order,limit", sort=71),
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

    # 先做契约校验：不合法的用例（未知期望键 / 缺 capability / 非 active 无原因）
    # 在写库之前就要暴露，否则落库后每次运行都产一堆 invalid_cases
    for c in CASES:
        try:
            normalize_case({**c, "id": 0, "is_active": 1})
        except CaseContractError as exc:
            print(f"✗ 用例 {c['case_key']} 不合法: {exc}")
            return 1

    defects = [c["case_key"] for c in CASES if c["case_status"] == "known_defect"]
    print(f"L2 真实语义层用例 {len(CASES)} 条；其中 known_defect {len(defects)} 条:")
    for k in defects:
        print("    !", k)
    print("\n按能力契约分布：")
    by_cap: dict[str, list[str]] = {}
    for c in CASES:
        by_cap.setdefault(c["capability"], []).append(c["case_key"])
    for cap in sorted(by_cap):
        print(f"  {cap:<12} {len(by_cap[cap]):>2} 条")

    if not args.apply:
        print("\n(dry-run，未落库；确认无误后加 --apply)")
        return 0

    created, skipped = 0, 0
    for c in CASES:
        if store.get_case_by_key(SUITE, c["case_key"]):
            skipped += 1
            continue
        store.create_case(c, created_by="seed_eval_semantic_suite")
        created += 1
    print(f"已新建 {created} 条，已存在跳过 {skipped} 条（幂等：不覆盖人工改过的用例）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
