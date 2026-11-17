"""L3 真实检索层用例播种（幂等，默认 dry-run）。

期望值来源（不是照抄当前行为，也不是凭猜）
------------------------------------------
本表所有期望值来自 outage 前在真实链路上的**实测探针**：

    retrieve_with_strategy(target_tables=['t_case_records'], ds=1780478236183)
      → table_info=1, column_metadata=49, business_terms=18, sql_templates=1,
        rag_source='graphrag'
      （注：`sql_templates` 桶已随预制 SQL 收敛到数据集而退役，同一位置现在叫
        `saved_datasets`；上述条目数是收敛前的实测记录，新条目数需重跑探针核实）
    retrieve_with_strategy(keywords=[...], 不给候选表)
      → 六张清单全空, rag_source='graphrag'
    strategy='ontology_traversal' + target_tables
      → rag_source='ontology_traversal:no_seed'（关键词未被抽成图种子）

据此得到三条结论，本播种表就是按它们写的：

1. ``GraphRagStrategy`` 的 agentic grounding 已退役，现在只做**已选候选表**的
   by-name 元数据回填，`keywords` 只是候选内部的排序线索。所以"给 keywords 就
   该有命中"这类断言**结构上不可能成立**，旧用例把它当期望是错的。
2. `max_results` / `hit:false` 这类断言在空结果上恒真，不能单独构成一条用例的
   期望 —— 新用例一律带 `min_results` 或 `hit:true` 这类可证伪下界。
3. 元数据检索对可写 SQL 的消费方**就该**返回真实表列（waker-datasource-domain §3），
   所以这里用 `no_credential_leak`（只卡凭据/地址/报错栈），不用 `no_physical`。
4. 路由索引层在 v0.5 已退役（编写不再是契约），所以本表不断言 route_resolved；
   真正要顶到门禁里的是“旧消费方还在调一个恒返回 [] 的层”，见该用例 status_note。
   旧 4 条 `route_hit_*` 针对的是已废弃的「数据资产路由图」形态，已在 P0 标为 retired。

用法（repo 根目录）:
  venv/bin/python backend/scripts/seed_eval_retrieval_suite.py           # dry-run
  venv/bin/python backend/scripts/seed_eval_retrieval_suite.py --apply

注意：--apply 需要元库可达。执行前请先跑一次
``venv/bin/python -m backend.eval.runner --suite retrieval --no-persist``
复核实测条目数是否仍是上面这组（表列数会随同步变化，`min_results` 取下界而非精确值）。
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

SUITE = "retrieval"
DS = "prd-cn"          # 真实业务源；用 name 不用 id，避免源删重建后静默腐坏


def _c(key, question, capability, payload, expected, note, *,
       status="active", status_note="", tags="", sort=0):
    return {"case_key": key, "suite": SUITE, "question": question,
            "capability": capability, "case_status": status, "status_note": status_note,
            "tags": tags, "sort": sort, "note": note,
            "payload": payload, "expected": expected}


CASES: list[dict] = []

# ── recall：候选表 → 元数据回填（这是本层真实可验证的契约）──────
CASES += [
    _c("ret_candidates_hydrate_table_and_columns", "案例表的列结构是什么", "recall",
       {"target_tables": ["t_case_records"], "datasource_name": DS},
       {"hit": True, "min_results": 10, "retrieval_source": "graphrag",
        "no_credential_leak": True},
       "给定候选表时必须回填出表与列；min_results 让「链路空转」无法蒙混过关",
       tags="recall,hydrate", sort=10),

    _c("ret_business_terms_hydrated_for_candidate", "案例表关联了哪些业务术语", "recall",
       {"target_tables": ["t_case_records"], "datasource_name": DS},
       {"hit": True, "min_results": 5, "retrieval_source": "graphrag"},
       "business_terms 回填是提示词组装的原料；实测 18 条，下界取 5 抗同步波动",
       tags="recall,terms", sort=11),

    _c("ret_multiple_candidates_within_prompt_budget", "多张候选表的元数据一次性回填", "recall",
       {"target_tables": ["t_case_records", "t_order_records"], "datasource_name": DS},
       {"hit": True, "min_results": 2, "max_results": 200},
       "多候选表要同时进结果；max_results 守住上下文预算不被一次塞爆",
       tags="recall,limit,budget", sort=12),

    _c("ret_no_candidates_returns_empty_by_design", "只给关键词不给候选表", "recall",
       {"keywords": ["案例", "医院"], "datasource_name": DS},
       {"hit": False},
       "锁住已退役的设计事实：grounding 不再由关键词产生命中。"
       "将来若恢复关键词召回，本条必须改并同步改写上面三条的依据",
       tags="recall,contract", sort=13),
]

# ── leak：元数据通道只卡凭据，不卡表列 ────────────────────────
CASES += [
    _c("ret_metadata_no_credential_leak", "案例表结构（含敏感列）", "leak",
       {"target_tables": ["t_case_records"], "datasource_name": DS},
       {"hit": True, "no_credential_leak": True},
       "护栏 §7 禁的是连接信息/凭据/报错栈外泄与 datasource_id 外泄，"
       "不是禁止向可写 SQL 的消费方回真实表列；这里不用 no_physical",
       tags="leak,metadata", sort=20),

    _c("ret_stripped_of_datasource_internal_ids", "检索结果不得携带内部标识", "leak",
       {"target_tables": ["t_case_records"], "datasource_name": DS},
       {"hit": True, "forbidden": ["datasource_id", "catalog_ref", "jdbc:", "physical_table"]},
       "内部标识与连接串一律不得出现在检索产出里（waker-datasource-domain §2 反模式清单）",
       tags="leak,no_leak", sort=21),
]

# ── routing：按**当前** canonical 形态重写（旧 4 条针对已废弃的路由图）──
# "未命中不猜路由" 已由存量用例 route_no_hit_no_guess 覆盖，不在此重复建行。
# 实测 resolve_business_routes：对 active 业务本体的**任何**对象都返回 []。
CASES += [
    _c("ret_route_layer_retired_returns_none", "业务对象的路由索引层在 v0.5 已退役", "routing",
       {"hit_object_keys": ["case"]},
       {"routes": [], "route_bucket": "route_none"},
       "v0.5 退役了路由编写（find_business_route_issues 不再要求 objects[].route，"
       "route: None 合法），所以现业主本体无路由数据是**合理现状**，不是资产缺失。"
       "本条锁的是契约：不得有人又把 route_resolved 当期望写回来。",
       tags="routing,retired_layer,contract", sort=31),
]

# ── honesty：空输入与异常不得被当成正常结果 ────────────────────
CASES += [
    _c("ret_empty_keywords_does_not_error", "（无关键词的空检索请求）", "honesty",
       {"keywords": [], "datasource_name": DS},
       {"hit": False, "no_credential_leak": True},
       "空输入不报错、也不得靠回吐物理细节充数",
       tags="honesty,reject", sort=40),
]

# ── 已知缺陷：图遍历种子（继续跑、继续拖累下限，逼着修）────────
CASES += [
    _c("ret_ontology_traversal_needs_seed", "案例相关的对象与关系", "recall",
       {"strategy": "ontology_traversal", "target_tables": ["t_case_records"],
        "datasource_name": DS},
       {"hit": True, "retrieval_source": "ontology_traversal"},
       "图遍历策略应能以候选表为种子遍历本体图并返回关系",
       status="known_defect",
       status_note=("实测 rag_source=ontology_traversal:no_seed —— 传了 target_tables 仍被判为无种子，"
                    "说明该策略的种子来源不是候选表而是别的东西（关键词/对象 key），"
                    "种子抽取契约与调用方口径不一致。缺口在种子抽取侧，不是图里没有数据。"),
       tags="recall,graph,seed_gap", sort=50),
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

    defects = [c["case_key"] for c in CASES if c["case_status"] == "known_defect"]
    print(f"L3 检索层用例 {len(CASES)} 条；known_defect {len(defects)} 条: {defects}")
    by_cap: dict[str, int] = {}
    for c in CASES:
        by_cap[c["capability"]] = by_cap.get(c["capability"], 0) + 1
    print("按能力契约：", by_cap)

    if not args.apply:
        print("\n(dry-run，未落库。元库恢复可达后先复核实测条目数，再 --apply)")
        return 0

    created = skipped = 0
    for c in CASES:
        if store.get_case_by_key(SUITE, c["case_key"]):
            skipped += 1
            continue
        store.create_case(c, created_by="seed_eval_retrieval_suite")
        created += 1
    print(f"已新建 {created} 条，已存在跳过 {skipped} 条（不覆盖人工改过的用例）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
