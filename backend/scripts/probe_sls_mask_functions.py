"""SLS(PG 协议/Presto) 端点的**脱敏函数可用性探针** —— 为 gates 注入列级安全策略取证。

背景：语义主路的列级 mask 过去靠执行后按 DataFrame 列名匹配
（`query_executor` -> `enforcer.apply_post_processing`），而 planner 把投影别名换成
业务名/SLS ASCII 槽位（`o0/o1…`），列名对不上 → mask 静默不生效。修复方向是把策略
**编译进下推 SQL**（`rls.apply_column_restriction`），因此必须先确认 SLS 端点认哪些
函数 —— 本仓纪律是「探针实证」（见 planner.py 的 SLS 时间变体注释），不猜方言函数。

安全性：只跑常量表达式（无 FROM 业务表），不取任何业务明细行。

用法：
    venv/bin/python -m backend.scripts.probe_sls_mask_functions [datasource_id]

第一轮实测结论（ds=1793178243857 arms-prd-en，2026-10-30）：
  OK   CONCAT / CAST(x AS VARCHAR) / LENGTH / CHAR_LENGTH / LEFT / RIGHT /
       SUBSTR(x,1,n) / REPEAT / GREATEST / CASE WHEN / 常量 SELECT（无需 FROM）
       MD5(varchar) -> 32 位十六进制字符串（可直接 SUBSTR 截 16 位当 hash）
  ERR  to_utf8 / VARBINARY / SHA2(x,256) / TO_HEX / HEX / SHA1 / SHA256(varchar)
       -> 原生 Presto 哈希写法在本端点不存在，hash 只能用 MD5
  陷阱 SUBSTR(x, -2) **不报错但返回整串** -> 负数下标不支持，
       拿它做"尾保留"partial mask 等于不脱敏（实测返回 20**********203.160.101.44）

落地的三条 SLS 表达式（均已实机 OK，与 security/masking.py 的 _SLS 分支同源）：
  partial -> 20**********44        hash -> a94443fc4bc1f456        null -> NULL
"""
from __future__ import annotations

import sys

# (探针名, 常量表达式)
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
    ("char_length", "CHAR_LENGTH('abcdef')"),
    # hash 候选：SLS PG 端点函数系与原生 Presto 有差异，逐个取证
    ("md5", "MD5('abc')"),
    ("md5_cast", "MD5(CAST('abc' AS VARCHAR))"),
    ("to_hex_md5", "TO_HEX(MD5('abc'))"),
    ("hex_md5", "HEX(MD5('abc'))"),
    ("sha256", "SHA256('abc')"),
    ("substr_sha256", "SUBSTR(SHA256('abc'), 1, 16)"),
    ("sha1", "SHA1('abc')"),
]

# 最终要落地的表达式（与 security/masking.py 的 SLS 分支保持一致）
_IP = "CAST('203.160.101.44' AS VARCHAR)"
TARGETS: list[tuple[str, str]] = [
    ("sls_partial",
     f"CASE WHEN LENGTH({_IP}) <= 4 THEN REPEAT('*', LENGTH({_IP})) "
     f"ELSE CONCAT(LEFT({_IP}, 2), REPEAT('*', GREATEST(LENGTH({_IP}) - 4, 0)), "
     f"RIGHT({_IP}, 2)) END"),
    ("sls_short", "CASE WHEN LENGTH('ab') <= 4 THEN REPEAT('*', LENGTH('ab')) ELSE 'x' END"),
    ("sls_hash", f"SUBSTR(MD5({_IP}), 1, 16)"),
    ("sls_null", "CAST(NULL AS VARCHAR)"),
    # 列改写走 sqlglot AST 重渲染，会把手写文法改成同义形式（now() -> CURRENT_TIMESTAMP、
    # 函数名大写、INTERVAL 单位大写）—— 这些重写后形状必须在 SLS 上取证，否则是拿主链路赌解析器。
    ("rt_to_unixtime_current", "TO_UNIXTIME(CURRENT_TIMESTAMP - INTERVAL '7 DAYS')"),
    ("rt_date_format_upper", "DATE_FORMAT(FROM_UNIXTIME(1730000000), '%Y-%m-%d')"),
]


def main() -> int:
    ds_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    from backend.common.db import execute_query, get_datasource_by_id
    from backend.semantics.execution.connectors.sls import SlsConnector

    row = get_datasource_by_id(ds_id) if ds_id else None
    if not row:
        hit = execute_query("SELECT id FROM adh_datasources WHERE db_type='sls' ORDER BY id LIMIT 1")
        ds_id = int((hit or [{}])[0].get("id") or 0)
        row = get_datasource_by_id(ds_id)
    print(f"[probe] SLS datasource_id={ds_id} name={row.get('name') if row else '?'}")

    connector = SlsConnector(ds_id)
    target_names = {name for name, _ in TARGETS}

    def run(expr: str) -> tuple[bool, str]:
        try:
            table = connector.execute_pushdown(f"SELECT {expr} AS probe_value", max_rows=1)
            vals = (table.to_pydict().get("probe_value") or [])
            return True, str(vals[0] if vals else "")
        except Exception as exc:  # noqa: BLE001 — 探针就是要收集失败原因
            return False, f"{type(exc).__name__}: {str(exc)[:120]}"

    failed = []
    for name, expr in PROBES + TARGETS:
        ok, detail = run(expr)
        print(f"  {'OK ' if ok else 'ERR'} {name:17s} {expr[:56]:56s} -> {detail[:70]}")
        if not ok and name in target_names:
            failed.append(name)
    print(f"[probe] 目标表达式失败: {failed or '无（SLS 可用）'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
