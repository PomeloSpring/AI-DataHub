"""SLS(PG 协议/Presto) 端点的**脱敏函数可用性探针** —— 为 gates 注入列级安全策略取证。

## 为什么需要它

列级 mask 要编译进下推 SQL（`rls.apply_column_restriction`），必须先确认 SLS 端点认哪些函数。
本仓纪律是「探针实证」，不猜方言函数。

## 必须带真实 FROM（2026-11-25 缺陷根因）

早期版本只跑 `SELECT REPEAT('*', 3)` 这类**无 FROM 的常量 SELECT**，走的是常量折叠路径：
`LEFT` / `RIGHT` / `REPEAT` / `MD5(varchar)` 全部返回 OK。但真实查询里它们分别报
`Function left not registered` / `Expected: repeat(double, integer)` / `Expected: md5(varbinary)`，
于是任何投影到 partial 列的查询整条失败（用户侧显示「取数未完成」）。
所以本探针从 catalog 取一张真实表 + 一列，所有表达式都套 `FROM <表> LIMIT 1` 执行；
SELECT 只取派生值、不选原始列，不落业务明细。

## 用法

    venv/bin/python -m backend.scripts.probe_sls_mask_functions [datasource_id] [table] [column]

缺省自动挑该源 catalog 里第一张表的第一列；挑不到直接报错（不退回常量探针，
那正是本脚本要防的假阳性）。

## 边界

- 只读：`LIMIT 1` 的派生投影，不写库、不下发原始列值；
- 目标表达式**从 `masking.mask_expr` 同源取**，实现改了探针自动跟着验，不存在两处漂移；
- 退出码非 0 = 有目标表达式在真实查询里失败，必须先修 `masking` 再上线。
"""
from __future__ import annotations

import sys

# (探针名, 表达式) —— 全部在真实表上下文执行；这里只说明“函数是否存在/参数类型是否合”
PROBES: list[tuple[str, str]] = [
    ("select_const", "1"),
    ("concat", "CONCAT('ab', 'c')"),
    ("cast_varchar", "CAST(123 AS VARCHAR)"),
    ("length", "LENGTH('abcdef')"),
    ("substr_pos", "SUBSTR('abcdef', 1, 2)"),
    ("substr_neg", "SUBSTR('abcdef', -2)"),
    ("repeat", "REPEAT('*', 3)"),
    ("greatest", "GREATEST(1, 2)"),
    ("left_fn", "LEFT('abcdef', 2)"),
    ("right_fn", "RIGHT('abcdef', 2)"),
    ("lpad_empty", "LPAD('', 5, '*')"),
    ("to_utf8_md5", "SUBSTR(MD5(TO_UTF8('abc')), 1, 16)"),
    ("md5_varchar", "MD5(CAST('abc' AS VARCHAR))"),
]


def _targets(column: str) -> list[tuple[str, str]]:
    """最终落地的表达式：直接取 `masking.mask_expr` 的产物（同源，不手抄）。"""
    from backend.semantics.security.masking import mask_expr

    col = f'CAST("{column}" AS VARCHAR)'
    short = f'SUBSTR({col}, 1, 2)'   # 走 partial 的“长度<=4 全打星”分支
    return [
        ("sls_partial", mask_expr(col, "partial", "postgres", "sls")),
        ("sls_partial_short", mask_expr(short, "partial", "postgres", "sls")),
        ("sls_hash", mask_expr(col, "hash", "postgres", "sls")),
        ("sls_null", mask_expr(col, "null", "postgres", "sls")),
    ]


def _resolve_table(ds_id: int, table: str, column: str) -> tuple[str, str]:
    """表/列缺省值从 catalog 取；取不到就报错（不允许无 FROM 的常量探针）。"""
    from backend.common.db import execute_query

    table = table or str((execute_query(
        "SELECT table_name FROM adh_table_info WHERE datasource_id=%s AND is_active=1 "
        "ORDER BY table_name LIMIT 1", (ds_id,)) or [{}])[0].get("table_name") or "")
    if not table:
        raise SystemExit(f"[probe] 数据源 {ds_id} 在 catalog 里没有可用的表：请显式传 table 参数"
                         "（无 FROM 的常量探针会假阳性，不作为取证依据）")
    column = column or str((execute_query(
        "SELECT column_name FROM adh_column_metadata WHERE datasource_id=%s AND table_name=%s "
        "AND is_active=1 ORDER BY id LIMIT 1", (ds_id, table)) or [{}])[0].get("column_name") or "")
    if not column:
        raise SystemExit(f"[probe] 表 {table} 在 catalog 里没有可用列：请显式传 column 参数")
    return table, column


def main() -> int:
    ds_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    table = sys.argv[2] if len(sys.argv) > 2 else ""
    column = sys.argv[3] if len(sys.argv) > 3 else ""

    from backend.common.db import execute_query, get_datasource_by_id
    from backend.semantics.execution.connectors.sls import SlsConnector

    row = get_datasource_by_id(ds_id) if ds_id else None
    if not row:
        hit = execute_query("SELECT id FROM adh_datasources WHERE db_type='sls' ORDER BY id LIMIT 1")
        ds_id = int((hit or [{}])[0].get("id") or 0)
        row = get_datasource_by_id(ds_id)
    table, column = _resolve_table(ds_id, table, column)
    print(f"[probe] SLS datasource_id={ds_id} name={(row or {}).get('name')} "
          f"table={table} column={column}")

    connector = SlsConnector(ds_id)
    targets = _targets(column)
    target_names = {name for name, _ in targets}

    def run(expr: str) -> tuple[bool, str]:
        # 关键：带 FROM —— 函数注册与参数类型检查只在真实查询计划里暴露
        sql = f'SELECT {expr} AS probe_value FROM "{table}" LIMIT 1'
        try:
            result = connector.execute_pushdown(sql, max_rows=1)
            vals = (result.to_pydict().get("probe_value") or [])
            return True, str(vals[0] if vals else "")
        except Exception as exc:  # noqa: BLE001 — 探针就是要收集失败原因
            return False, f"{type(exc).__name__}: {str(exc)[:140]}"

    failed = []
    for name, expr in PROBES + targets:
        ok, detail = run(expr)
        print(f"  {'OK ' if ok else 'ERR'} {name:17s} {expr[:58]:58s} -> {detail[:80]}")
        if not ok and name in target_names:
            failed.append(name)
    print(f"[probe] 目标表达式失败: {failed or '无（SLS 可用）'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())