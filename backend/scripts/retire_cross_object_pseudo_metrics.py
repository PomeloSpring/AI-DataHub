"""退役无法作为"单对象存储指标"编译的跨表/子查询伪指标（幂等，dry-run 默认）。

原因：planner 的跨对象聚合只由**查询级限定名**（`目标对象.指标`，经 links[] 解链）触发，
不会解析指标公式内部的 `Case.id` 之类跨表引用。故这些行一旦回填 target_table，会生成
`SELECT COUNT(Case.id) FROM t_product_equipment`（无 JOIN）→ 非法 SQL。它们不是可编译的
存储指标，且 get_metrics 按 bound_object_key 广告它们 → "目录挂着、一问就无法解析"。

其中 6 条的能力由**受支持的限定引用机制**覆盖（相关 links 已存在）：
  case_count / avg_cases_per_device → `case.total_cases` 按 equipment 分组（equipment→case link 存在）
  total_cases(company)             → `case.total_cases` 按 company 分组（company→case link 存在）
  file_count                       → `case_file.*` 按 case 分组（case→case_file link 存在）
  member_count / company_count     → `user.total_users` / `company.total_companies` 反向分组（link 存在）
另 3 条无等价限定路径，属建模待办（见退役后报告）：
  total_ai_messages（缺 ai_conversation→message link）
  latest_software_versions（多列 SELECT，本质是视图不是聚合指标）
  avg_cases_per_device（跨对象比值，需 JOIN 后二次计算）

只处理精确匹配 (name, bound_object_key) 且 target_table 为空的 active 行，可逆。

用法：
    PYTHONPATH=. venv/bin/python -m backend.scripts.retire_cross_object_pseudo_metrics          # dry-run
    PYTHONPATH=. venv/bin/python -m backend.scripts.retire_cross_object_pseudo_metrics --apply  # 执行
"""
from __future__ import annotations

import sys

from backend.common.db.metadata_db import get_metadata_conn

RETIRE: list[tuple[str, str]] = [
    ("case_count", "equipment"),
    ("avg_cases_per_device", "equipment"),
    ("total_cases", "company"),
    ("member_count", "company"),
    ("company_count", "user"),
    ("file_count", "case"),
    ("total_ai_messages", "ai_conversation"),
    ("latest_software_versions", "software_version"),
]


def main(apply: bool) -> int:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            hit = 0
            for name, obj in RETIRE:
                cur.execute(
                    "SELECT id, name, bound_object_key, target_table, formula "
                    "FROM adh_metrics WHERE name=%s AND bound_object_key=%s AND is_active=1",
                    (name, obj))
                for r in cur.fetchall():
                    if (r.get("target_table") or "").strip():
                        print(f"  [keep] {name!r} 已有 target_table，跳过")
                        continue
                    print(f"  [{'RETIRE' if apply else 'dry'}] id={r['id']} {name!r} obj={obj} "
                          f"formula={str(r.get('formula') or '')[:56]!r}")
                    hit += 1
                    if apply:
                        cur.execute(
                            "UPDATE adh_metrics SET is_active=0, updated_at=NOW() WHERE id=%s",
                            (r["id"],))
            if apply:
                conn.commit()
                print(f"\n已退役 {hit} 条（is_active=0，可逆）。")
            else:
                print(f"\n[dry-run] 将退役 {hit} 条，未写库。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
