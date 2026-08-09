"""数据质量规则执行引擎 — 连接目标数据源执行 SQL 检查并记录结果.

支持的规则类型（与前端 RULE_TYPE_OPTIONS 对齐）：
- not_null      非空检查     config: {"include_empty": true}（可选，把空串也视为违规）
- unique        唯一性检查   config: {}
- range         范围检查     config: {"min_value": 0, "max_value": 1000000}
- format        格式检查     config: {"regex": "^1[0-9]{10}$"}
- referential   引用完整性   config: {"ref_table": "users", "ref_column": "id"}
- custom_sql    自定义 SQL   config: {"sql": "SELECT ... 返回违规行"}
- freshness     数据新鲜度   config: {"max_age_hours": 24}（时间列取 target_column）
- row_count     行数检查     config: {"min_rows": 1, "max_rows": 1000000}
- distribution  分布检查     config: {"max_top_ratio": 0.8}（最大取值占比上限）

表级检查（freshness/row_count/distribution）以 total_rows=1、failed_rows=0|1 记语义。

Usage:
    from services.datagov.services.quality_engine import execute_single_rule
    result = execute_single_rule(rule_row)  # rule_row 来自 SELECT * FROM adh_quality_rules
"""

import json
import logging
import re
import time
from datetime import datetime

from services.shared.common.db import DBConnection
from services.shared.common.db.datasource_db import get_datasource_by_id, get_datasource_conn

logger = logging.getLogger(__name__)

# 合法标识符：字母/数字/下划线/中文，可带 db.table 前缀
_IDENT_RE = re.compile(r"^[\w\u4e00-\u9fa5]+(\.[\w\u4e00-\u9fa5]+)*$", re.UNICODE)
SAMPLE_LIMIT = 5


def _quote(name: str, label: str = "标识符") -> str:
    """校验并反引号包裹标识符（支持 db.table 分段加引号），防止 SQL 注入."""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"非法{label}: {name!r}")
    return ".".join(f"`{part}`" for part in name.split("."))


def _parse_config(rule: dict) -> dict:
    cfg = rule.get("rule_config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    return cfg


def _open_target_conn(rule: dict):
    """按规则的 target_datasource_id 建立目标库连接."""
    ds_id = rule.get("target_datasource_id")
    if not ds_id:
        raise ValueError("规则未配置目标数据源 (target_datasource_id)")
    ds = get_datasource_by_id(ds_id)
    if not ds:
        raise ValueError(f"数据源不存在: {ds_id}")
    return get_datasource_conn(
        ds["db_type"], ds["host"], ds["port"],
        ds["username"], ds["password"], ds.get("database_name"),
    )


def _fetch_one(conn, sql: str, params=None) -> dict:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() or {}


def _fetch_all(conn, sql: str, params=None) -> list:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _sanitize_samples(rows: list) -> list:
    """采样行转 JSON 安全格式."""
    out = []
    for row in rows[:SAMPLE_LIMIT]:
        out.append({k: (str(v) if isinstance(v, (datetime, bytes)) else v) for k, v in row.items()})
    return out


# ── 各规则类型的检查实现 ─────────────────────────────────────────────
# 每个检查函数返回 (total_rows, failed_rows, samples)

def _check_not_null(conn, table: str, column: str, cfg: dict):
    where = f"{column} IS NULL"
    if cfg.get("include_empty"):
        where = f"({column} IS NULL OR CAST({column} AS CHAR) = '')"
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    failed = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {where}")["c"]
    samples = _fetch_all(conn, f"SELECT * FROM {table} WHERE {where} LIMIT {SAMPLE_LIMIT}")
    return total, failed, samples


def _check_unique(conn, table: str, column: str, cfg: dict):
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    distinct = _fetch_one(conn, f"SELECT COUNT(DISTINCT {column}) AS c FROM {table} WHERE {column} IS NOT NULL")["c"]
    null_cnt = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {column} IS NULL")["c"]
    failed = total - distinct - null_cnt
    samples = _fetch_all(
        conn,
        f"SELECT {column}, COUNT(*) AS dup_count FROM {table} "
        f"WHERE {column} IS NOT NULL GROUP BY {column} HAVING COUNT(*) > 1 LIMIT {SAMPLE_LIMIT}",
    )
    return total, failed, samples


def _check_range(conn, table: str, column: str, cfg: dict):
    min_v, max_v = cfg.get("min_value"), cfg.get("max_value")
    if min_v is None and max_v is None:
        raise ValueError("range 规则需要 min_value 或 max_value")
    conds = []
    if min_v is not None:
        conds.append(f"{column} < {float(min_v)}")
    if max_v is not None:
        conds.append(f"{column} > {float(max_v)}")
    where = " OR ".join(conds)
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    failed = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {where}")["c"]
    samples = _fetch_all(conn, f"SELECT * FROM {table} WHERE {where} LIMIT {SAMPLE_LIMIT}")
    return total, failed, samples


def _check_format(conn, table: str, column: str, cfg: dict):
    regex = cfg.get("regex")
    if not regex:
        raise ValueError("format 规则需要 regex 配置")
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    where = f"{column} IS NOT NULL AND CAST({column} AS CHAR) NOT REGEXP %s"
    failed = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", (regex,))["c"]
    samples = _fetch_all(conn, f"SELECT * FROM {table} WHERE {where} LIMIT {SAMPLE_LIMIT}", (regex,))
    return total, failed, samples


def _check_referential(conn, table: str, column: str, cfg: dict):
    ref_table = _quote(cfg.get("ref_table", ""), "引用表")
    ref_column = _quote(cfg.get("ref_column", ""), "引用列")
    if not cfg.get("ref_table") or not cfg.get("ref_column"):
        raise ValueError("referential 规则需要 ref_table 和 ref_column")
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {column} IS NOT NULL")["c"]
    failed = _fetch_one(
        conn,
        f"SELECT COUNT(*) AS c FROM {table} t LEFT JOIN {ref_table} r ON t.{column} = r.{ref_column} "
        f"WHERE t.{column} IS NOT NULL AND r.{ref_column} IS NULL",
    )["c"]
    samples = _fetch_all(
        conn,
        f"SELECT t.* FROM {table} t LEFT JOIN {ref_table} r ON t.{column} = r.{ref_column} "
        f"WHERE t.{column} IS NOT NULL AND r.{ref_column} IS NULL LIMIT {SAMPLE_LIMIT}",
    )
    return total, failed, samples


def _check_custom_sql(conn, table: str, column: str, cfg: dict):
    sql = (cfg.get("sql") or "").strip().rstrip(";")
    if not sql:
        raise ValueError("custom_sql 规则需要 sql 配置（SELECT 语句，返回违规行）")
    if not re.match(r"^\s*select\b", sql, re.IGNORECASE):
        raise ValueError("custom_sql 仅允许 SELECT 语句")
    failed = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM ({sql}) _violations")["c"]
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    samples = _fetch_all(conn, f"SELECT * FROM ({sql}) _violations LIMIT {SAMPLE_LIMIT}")
    return total, failed, samples


def _check_freshness(conn, table: str, column: str, cfg: dict):
    """表级检查：最新数据时间距今不得超过 max_age_hours."""
    max_age = float(cfg.get("max_age_hours", 24))
    row = _fetch_one(conn, f"SELECT MAX({column}) AS latest FROM {table}")
    latest = row.get("latest")
    if not latest:
        return 1, 1, [{"error": f"{column} 无有效时间数据"}]
    age_hours = (datetime.now() - latest).total_seconds() / 3600
    failed = 1 if age_hours > max_age else 0
    return 1, failed, [{"latest": str(latest), "age_hours": round(age_hours, 1), "max_age_hours": max_age}]


def _check_row_count(conn, table: str, column: str, cfg: dict):
    """表级检查：行数在 [min_rows, max_rows] 区间内."""
    min_rows, max_rows = cfg.get("min_rows"), cfg.get("max_rows")
    if min_rows is None and max_rows is None:
        raise ValueError("row_count 规则需要 min_rows 或 max_rows")
    count = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table}")["c"]
    failed = 0
    if min_rows is not None and count < int(min_rows):
        failed = 1
    if max_rows is not None and count > int(max_rows):
        failed = 1
    return 1, failed, [{"row_count": count, "min_rows": min_rows, "max_rows": max_rows}]


def _check_distribution(conn, table: str, column: str, cfg: dict):
    """表级检查：最高频取值占比不得超过 max_top_ratio（检测数据倾斜/枚举失控）."""
    max_ratio = float(cfg.get("max_top_ratio", 0.8))
    total = _fetch_one(conn, f"SELECT COUNT(*) AS c FROM {table} WHERE {column} IS NOT NULL")["c"]
    if total == 0:
        return 1, 1, [{"error": f"{column} 无有效数据"}]
    top = _fetch_one(
        conn,
        f"SELECT {column} AS top_value, COUNT(*) AS cnt FROM {table} "
        f"WHERE {column} IS NOT NULL GROUP BY {column} ORDER BY cnt DESC LIMIT 1",
    )
    ratio = (top.get("cnt") or 0) / total
    failed = 1 if ratio > max_ratio else 0
    return 1, failed, [{"top_value": str(top.get("top_value")), "top_ratio": round(ratio, 4), "max_top_ratio": max_ratio}]


_CHECKERS = {
    "not_null": _check_not_null,
    "unique": _check_unique,
    "range": _check_range,
    "format": _check_format,
    "referential": _check_referential,
    "custom_sql": _check_custom_sql,
    "freshness": _check_freshness,
    "row_count": _check_row_count,
    "distribution": _check_distribution,
}


# ── 入口 ─────────────────────────────────────────────────────────────

def execute_single_rule(rule: dict) -> dict:
    """执行单条质量规则，写入 adh_quality_results 并返回结果."""
    rule_id = rule.get("id")
    rule_type = rule.get("rule_type", "")
    cfg = _parse_config(rule)
    start = time.time()

    base = {
        "rule_id": rule_id,
        "rule_name": rule.get("rule_name") or rule.get("name"),
        "rule_type": rule_type,
        "target_table": rule.get("target_table"),
    }

    checker = _CHECKERS.get(rule_type)
    if not checker:
        return {**base, "passed": False, "error": f"不支持的规则类型: {rule_type}"}

    conn = None
    try:
        table = _quote(rule.get("target_table", ""), "目标表")
        column = _quote(rule.get("target_column", ""), "目标列") if rule.get("target_column") else None
        # 列级规则必须有目标列
        if rule_type not in ("row_count",) and not column:
            raise ValueError(f"{rule_type} 规则需要 target_column")

        conn = _open_target_conn(rule)
        total_rows, failed_rows, samples = checker(conn, table, column, cfg)

        passed = failed_rows <= int(cfg.get("max_failed_rows", 0))
        pass_rate = round((total_rows - failed_rows) / total_rows * 100, 2) if total_rows > 0 else 0.0
        elapsed_ms = int((time.time() - start) * 1000)
        check_time = datetime.now()

        detail = {"samples": _sanitize_samples(samples), "config": cfg}
        with DBConnection() as db:
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_quality_results
                       (rule_id, workspace_id, check_time, passed, total_rows, failed_rows,
                        pass_rate, detail, elapsed_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (rule_id, rule.get("workspace_id") or 0, check_time, int(passed),
                     total_rows, failed_rows, pass_rate,
                     json.dumps(detail, ensure_ascii=False, default=str), elapsed_ms),
                )

        return {
            **base, "passed": passed, "total_rows": total_rows, "failed_rows": failed_rows,
            "pass_rate": pass_rate, "detail_samples": _sanitize_samples(samples),
            "elapsed_ms": elapsed_ms, "check_time": str(check_time),
        }
    except Exception as e:
        logger.error("Quality rule %s execution failed: %s", rule_id, e)
        # 执行异常也记录一条 error 结果，便于追踪
        try:
            with DBConnection() as db:
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO adh_quality_results
                           (rule_id, workspace_id, check_time, passed, total_rows, failed_rows,
                            pass_rate, detail, elapsed_ms)
                           VALUES (%s, %s, %s, 0, 0, 0, 0.00, %s, %s)""",
                        (rule_id, rule.get("workspace_id") or 0, datetime.now(),
                         json.dumps({"error": str(e)[:500]}, ensure_ascii=False),
                         int((time.time() - start) * 1000)),
                    )
        except Exception:
            logger.warning("Failed to record error result for rule %s", rule_id)
        return {**base, "passed": False, "error": str(e)[:500]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
