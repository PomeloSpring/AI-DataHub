"""看板遗留「查询参数」去重迁移的回归（2026-11-10 收口）。

锁三件事：
1. **SQL 写法契约**：MySQL 8 的 `JSON_TABLE()` 不接受表达式（CASE/CAST 包一层就报
   1210），所以必须走"递归 CTE 按下标展开"；MySQL 没有 `JSON_EQUALS()`，"内容未变不写"
   只能用双向 `JSON_CONTAINS`；`JSON_ARRAYAGG` 会把 NULL 聚成 JSON null，剔除必须用
   WHERE 过滤 + LEFT JOIN 回补空数组（否则"全被剔除"的看板根本不会出现在聚合里，
   迁移会一条都不改）。
2. **只删重复，不动口径**：SET 目标只能是 `params`，`filters`（含 pageParams）不得被写。
3. **库里现状**：迁移已应用且幂等 —— 现网不得再有与 `filters.pageParams` 重名的 params
   项（有的话就是"同一参数两处登记、顶部又长出死输入口"的缺陷复发）。

第 3 项按 `test_eval_baseline.py` 的口径：DB 不可用时**显式失败**，不静默跳过。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MIGRATION = os.path.join(os.path.dirname(__file__), "..",
                         "docker", "mysql", "dashboard_legacy_params_dedup_migration.sql")

# 与迁移文件同一份"待剔除"判定（只读，不写库）
DEDUP_SELECT = """
WITH RECURSIVE idx AS (SELECT 0 AS i UNION ALL SELECT i + 1 FROM idx WHERE i < 63),
base AS (
  SELECT id, CAST(params AS JSON) AS params_doc, JSON_LENGTH(CAST(params AS JSON)) AS n,
         COALESCE(JSON_EXTRACT(filters, '$.pageParams'), JSON_ARRAY()) AS page
  FROM adh_dashboards
  WHERE params IS NOT NULL AND JSON_VALID(params)
    AND JSON_TYPE(CAST(params AS JSON)) = 'ARRAY'
    AND JSON_LENGTH(CAST(params AS JSON)) > 0
),
picked AS (
  SELECT b.id AS id, JSON_EXTRACT(b.params_doc, CONCAT('$[', i.i, ']')) AS item,
         JSON_UNQUOTE(JSON_EXTRACT(b.params_doc, CONCAT('$[', i.i, '].name'))) AS nm,
         JSON_CONTAINS(b.page, JSON_OBJECT('name',
           JSON_UNQUOTE(JSON_EXTRACT(b.params_doc, CONCAT('$[', i.i, '].name'))))) AS dup
  FROM base b JOIN idx i ON i.i < b.n
)
SELECT id, nm FROM picked WHERE dup = 1
"""


def _statement():
    with open(MIGRATION, encoding="utf-8") as fh:
        text = fh.read()
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))


class TestMigrationSqlContract(unittest.TestCase):
    def test_file_exists_and_uses_supported_json_form(self):
        self.assertTrue(os.path.exists(MIGRATION), "迁移文件必须随改动一起提交")
        stmt = _statement()
        # MySQL 8 的坑：JSON_TABLE 的第一参数只能是基础表列
        self.assertNotIn("LEFT JOIN JSON_TABLE", stmt,
                         "JSON_TABLE 不接受表达式入参（CASE/CAST 包一层报 1210），改用递归 CTE 按下标展开")
        self.assertIn("WITH RECURSIVE idx", stmt)
        # MySQL 没有 JSON_EQUALS()
        self.assertNotIn("JSON_EQUALS", stmt, "MySQL 无此函数，等价判定用双向 JSON_CONTAINS")
        self.assertEqual(stmt.count("JSON_CONTAINS"), 3,
                         "1 处判重名 + 2 处双向等价守卫（WHERE 少一边就会重复写库、抖 updated_at）")

    def test_only_params_is_written(self):
        stmt = _statement()
        self.assertIn("SET d.params", stmt)
        self.assertNotIn("SET d.filters", stmt, "pageParams 是控件绑定的真值来源，迁移不得触碰")
        self.assertNotIn("DELETE", stmt.upper().replace("--", ""), "只重写数组内容，不删看板行")

    def test_empty_after_dedup_is_not_null(self):
        """全被剔除时必须落 []：JSON_ARRAYAGG 不吃 NULL 过滤，靠 LEFT JOIN 回补。"""
        stmt = _statement()
        self.assertIn("kept_agg", stmt)
        self.assertIn("LEFT JOIN kept_agg", stmt)
        self.assertIn("COALESCE(k.kept, JSON_ARRAY())", stmt)


class TestLiveDbState(unittest.TestCase):
    """迁移要"应用到当前库"：这里核验现状，而不是只提交一个文件。"""

    def test_no_legacy_param_duplicates_left(self):
        try:
            from backend.common.db import execute_query
            rows = execute_query(DEDUP_SELECT)
        except Exception as exc:                                    # noqa: BLE001
            self.fail(f"元数据库不可用，门禁无法执行（不做静默跳过）: {type(exc).__name__}: {exc}")
        self.assertEqual([], [(r["id"], r["nm"]) for r in rows],
                         "params 与 filters.pageParams 重名 = 同一参数两处登记，"
                         "顶部会长出被 pageParamValues 覆盖的死输入口（先跑迁移再改代码）")

    def test_dashboard_payload_still_carries_page_params(self):
        """剔除 params 后，控件 bind 的变量名仍要在 pageParams 里（否则筛选彻底失效）。"""
        try:
            from backend.common.db import execute_query
            rows = execute_query("SELECT id, filters FROM adh_dashboards "
                                 "WHERE filters IS NOT NULL AND JSON_VALID(filters)")
        except Exception as exc:                                    # noqa: BLE001
            self.fail(f"元数据库不可用: {type(exc).__name__}: {exc}")
        checked = 0
        for row in rows:
            page = (json.loads(row["filters"] or "{}") or {}).get("pageParams") or []
            if not page:
                continue
            checked += 1
            self.assertTrue(all(p.get("name") for p in page), f"看板 {row['id']} 的 pageParams 有空参数名")
        self.assertGreater(checked, 0, "现网应当仍有登记了页面参数的看板（清单为空说明查询写错了）")


if __name__ == "__main__":
    unittest.main()
