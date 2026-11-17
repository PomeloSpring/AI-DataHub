"""退役被误分类为指标的"行级派生视图"（幂等，dry-run 默认，--apply 才写库）。

这些行本质是对象底列(如 status / total_amount / activation_time / version_code)的
行级派生表达式(布尔标记 或 标量换算)，不是聚合指标；其底列已作为对象属性/维度存在，
故从指标字典退役(is_active=0)，停止被 get_metrics 广告、避免"目录挂着、编译用不了"。

退役后 object.metrics[] 里残留的名字是无害悬空引用(字典行已 inactive，get_metrics
按 is_active=1 过滤、planner 也加载不到)；彻底清理 doc 引用需走 save_draft/activate
级联，属更高爆炸半径操作，单独评估。

只处理精确匹配的 (name, bound_object_key) 且 target_table 为空的 active 行，可逆。

用法：
    PYTHONPATH=. venv/bin/python -m backend.scripts.retire_derived_pseudo_metrics          # dry-run
    PYTHONPATH=. venv/bin/python -m backend.scripts.retire_derived_pseudo_metrics --apply  # 执行
"""
from __future__ import annotations

import sys

from backend.common.db.metadata_db import get_metadata_conn

# (name, bound_object_key) —— 15 条行级派生视图（布尔标记 / 标量换算 / is_latest）
RETIRE: list[tuple[str, str]] = [
    ("is_active", "user"),
    ("days_since_registration", "user"),
    ("is_certified", "company"),
    ("is_frequent_visitor", "patient"),
    ("storage_mb", "case"),
    ("is_completed", "case"),
    ("cycle_time_hours", "case"),
    ("amount_yuan", "order"),
    ("is_pending", "order"),
    ("is_activated", "equipment"),
    ("is_online", "equipment"),
    ("is_warranty_expired", "equipment"),
    ("days_since_activation", "equipment"),
    ("resolution_hours", "work_order"),
    ("is_latest", "software_version"),
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
                rows = cur.fetchall()
                if not rows:
                    print(f"  [miss] {name!r} obj={obj} 未找到 active 行(可能已退役)")
                    continue
                for r in rows:
                    if (r.get("target_table") or "").strip():
                        print(f"  [keep] {name!r} 已有 target_table={r['target_table']}，跳过")
                        continue
                    print(f"  [{'RETIRE' if apply else 'dry'}] id={r['id']} {name!r} obj={obj} "
                          f"formula={str(r.get('formula') or '')[:50]!r}")
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
