"""回填/修复字典行 target_table（幂等迁移脚本，dry-run 默认，--apply 才写库）。

背景：本体生成器 `_sync_metric_specs` 把指标 INSERT 时 target_table 写成空串，
导致 planner（按 `target_table = 物理表` 加载指标/维度）编译期取不到这些指标 →
语义问数"无法解析"。本脚本按 bound_object_key → adh_ontology_bindings 的
权威物理表回填 target_table，让合法指标可编译。

安全边界（宁缺勿错，不做有害激活）：
- 只处理 is_active=1、bound_object_key 非空、target_table 为空的行；
- 对象须能唯一映射到一张物理表（多表歧义/无主表 → 跳过并报告）；
- 指标公式仅两类落地：
  ① 合法单表聚合（COUNT/SUM/AVG/MIN/MAX(...)，无内联 WHERE/子查询/跨表限定）→ 只回填 target_table；
  ② `AGG(...) WHERE <cond>` 内联 WHERE → 等价改写为 `AGG(CASE WHEN <cond> THEN <arg> END)`
     （SUM 用 ELSE 0）后回填；改写要求能无歧义解析出聚合函数与内层参数，否则跳过；
- 布尔派生字段（如 `status = 1`）、无聚合标量（如 `total_amount / 100.0`）、跨表引用、
  子查询等一律**跳过**（属建模回炉项，激活会生成非法/错误 SQL），仅在报告中列出。

用法：
    PYTHONPATH=. venv/bin/python -m backend.scripts.backfill_dict_target_table            # dry-run
    PYTHONPATH=. venv/bin/python -m backend.scripts.backfill_dict_target_table --apply    # 执行
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from backend.common.db.metadata_db import get_metadata_conn
from backend.modules.catalog.services.metric_formula import AGG_HEAD, columns_of_table, is_cross_table

_AGG_FUNCS = ("COUNT", "SUM", "AVG", "MIN", "MAX")
_RE_WHERE = re.compile(r"\bWHERE\b", re.I)
_RE_SELECT = re.compile(r"\bSELECT\b", re.I)
_RE_AGG_HEAD = AGG_HEAD


def _split_top_where(expr: str) -> tuple[str, str] | None:
    """把 `AGG(...) WHERE cond` 在**顶层**第一个 WHERE 处切开（忽略括号内的 WHERE）。

    返回 (agg_part, cond) 或 None（无顶层 WHERE）。
    """
    depth = 0
    up = expr.upper()
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and up.startswith("WHERE", i) and \
                (i == 0 or not expr[i - 1].isalnum()) and \
                (i + 5 >= len(expr) or not expr[i + 5].isalnum()):
            return expr[:i].strip(), expr[i + 5:].strip()
        i += 1
    return None


def _split_agg_arg(agg_part: str) -> tuple[str, str] | None:
    """`FUNC(arg)` → (FUNC, arg)，要求 FUNC 后的左括号与串尾右括号配对（内层无游离）。"""
    m = _RE_AGG_HEAD.match(agg_part)
    if not m or not agg_part.endswith(")"):
        return None
    func = m.group(1).upper()
    open_idx = agg_part.index("(", m.start())
    depth = 0
    for i in range(open_idx, len(agg_part)):
        if agg_part[i] == "(":
            depth += 1
        elif agg_part[i] == ")":
            depth -= 1
            if depth == 0:
                # 顶层闭合必须正好在串尾，否则 FUNC(...) 后还有内容 → 不处理
                if i != len(agg_part) - 1:
                    return None
                return func, agg_part[open_idx + 1:i].strip()
    return None


def _rewrite_inline_where(expr: str) -> str | None:
    """`AGG(...) WHERE cond` → `AGG(CASE WHEN cond THEN arg [ELSE 0] END)`；无法解析返回 None。"""
    split = _split_top_where(expr)
    if not split:
        return None
    agg_part, cond = split
    parsed = _split_agg_arg(agg_part)
    if not parsed or not cond:
        return None
    func, arg = parsed
    if arg == "*":
        inner = "1"
    else:
        inner = arg
    if func == "SUM":
        return f"SUM(CASE WHEN {cond} THEN {inner} ELSE 0 END)"
    return f"{func}(CASE WHEN {cond} THEN {inner} END)"


def _classify(formula: str, table: str | None,
              known_columns: set | None = None) -> tuple[str, str]:
    """返回 (action, new_formula)。action ∈ backfill|rewrite|skip。

    带点 token 以列元数据实存为裁决（SLS/ARMS 列名本体含点，如 `session.id`、
    `resource.duration`，旧口径把这类合法单表聚合全部误判跳）。
    """
    f = (formula or "").strip()
    if not f or not table:
        return "skip", f
    if _RE_SELECT.search(f):
        return "skip", f
    # 真正的跨表限定（前缀不是本表名、也不是实存的带点列名）→ 跳过（需 JOIN 建模）
    if is_cross_table(f, table, known_columns):
        return "skip", f
    split = _split_top_where(f)
    if split:
        new_f = _rewrite_inline_where(f)
        return ("rewrite", new_f) if new_f else ("skip", f)
    # 无内联 WHERE：须是合法聚合表达式（以聚合函数开头）才回填
    if _RE_AGG_HEAD.match(f):
        return "backfill", f
    return "skip", f  # 布尔派生字段/无聚合标量 → 建模回炉


def main(apply: bool) -> int:
    conn = get_metadata_conn()
    stats = defaultdict(int)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT object_key, physical_table, datasource_id FROM adh_ontology_bindings "
                "WHERE status='active' AND physical_table<>''")
            obj_tables: dict[str, set] = defaultdict(set)
            table_ds: dict[str, int] = {}
            for r in cur.fetchall():
                tab = str(r["physical_table"]).strip()
                obj_tables[str(r["object_key"]).strip().lower()].add(tab)
                # 列元数据按源+全局查；多源同表名时任取一源作为优先线索（主路径同名不同源罕见）
                table_ds.setdefault(tab, int(r.get("datasource_id") or 0))
            col_cache: dict = {}

            cur.execute(
                "SELECT id, name, bound_object_key, formula, formula_dsl FROM adh_metrics "
                "WHERE is_active=1 AND bound_object_key IS NOT NULL AND bound_object_key<>'' "
                "AND (target_table IS NULL OR target_table='')")
            rows = cur.fetchall()
            print(f"待处理指标行={len(rows)}  模式={'APPLY' if apply else 'DRY-RUN'}\n")

            for r in rows:
                ok = str(r["bound_object_key"]).strip().lower()
                tabs = obj_tables.get(ok, set())
                table = next(iter(tabs)) if len(tabs) == 1 else None
                cur_expr = str(r.get("formula_dsl") or r.get("formula") or "").strip()
                known = columns_of_table(table_ds.get(table or "", 0), table or "", col_cache)
                action, new_expr = _classify(cur_expr, table, known)
                stats[action] += 1
                if action == "skip":
                    print(f"  [skip]    {r['name']!r} obj={ok} table={table} formula={cur_expr[:60]!r}")
                    continue
                if action == "rewrite":
                    print(f"  [rewrite] {r['name']!r} → table={table}\n"
                          f"            {cur_expr!r}\n         → {new_expr!r}")
                else:
                    print(f"  [backfill]{r['name']!r} → table={table}")
                if apply:
                    if action == "rewrite":
                        cur.execute(
                            "UPDATE adh_metrics SET target_table=%s, formula=%s, "
                            "formula_dsl=NULL, updated_at=NOW() WHERE id=%s",
                            (table, new_expr, r["id"]))
                    else:
                        cur.execute(
                            "UPDATE adh_metrics SET target_table=%s, updated_at=NOW() WHERE id=%s",
                            (table, r["id"]))
            if apply:
                conn.commit()
                print(f"\n已提交。backfill={stats['backfill']} rewrite={stats['rewrite']} "
                      f"skip={stats['skip']}")
            else:
                print(f"\n[dry-run] 未写库。backfill={stats['backfill']} "
                      f"rewrite={stats['rewrite']} skip={stats['skip']}（skip 为建模回炉项）")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
