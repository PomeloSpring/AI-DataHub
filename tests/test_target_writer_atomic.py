"""Doris 分区覆盖前的按需建分区（历史回填必需，data-sync-engineering §4）。

锁定三件事：
1. 必须走 `ADD PARTITION IF NOT EXISTS`（幂等可重跑，不被动态分区自动淘汰）；
2. 区间与分区名同源（分区名 pYYYYMMDD ↔ 日期区间 [d, d+1)），否则 OVERWRITE 装错分区；
3. 分区名/日期做严格校验，不接受拼接片段（防注入）。
"""
import pytest

from backend.modules.flow.dag.target_writer import (
    _count_rows,
    _doris_shadow_ddl,
    _ensure_doris_partition,
    _partition_where,
)


class _Cur:
    def __init__(self):
        self.sqls: list[str] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)


def test_add_partition_is_idempotent_and_day_bounded():
    cur = _Cur()
    _ensure_doris_partition(cur, "stardb.dws_case_company_rank", "p20261101", "2026-11-01")
    sql = cur.sqls[0]
    assert "ADD PARTITION IF NOT EXISTS p20261101" in sql
    assert "VALUES [('2026-11-01'),('2026-11-02'))" in sql


def test_year_partition_uses_same_convention():
    cur = _Cur()
    _ensure_doris_partition(cur, "tbl", "p20260101", "2026-01-01")
    assert "VALUES [('2026-01-01'),('2026-01-02'))" in cur.sqls[0]


@pytest.mark.parametrize("name", ["p 1", "p1;DROP TABLE x", "p-1", "", "p1)"])
def test_reject_illegal_partition_name(name):
    with pytest.raises(ValueError):
        _ensure_doris_partition(_Cur(), "tbl", name, "2026-01-01")


@pytest.mark.parametrize("value", ["2026/01/01", "", "not-a-date"])
def test_reject_illegal_partition_date(value):
    with pytest.raises(ValueError):
        _ensure_doris_partition(_Cur(), "tbl", "p20260101", value)


# ── 对账取数必须兼容 DictCursor（目标数据源统一用 DictCursor）─────────────
class _DictCur:
    """模拟 pymysql DictCursor：fetchone() 返回 dict 而不是 tuple。"""

    def __init__(self, row):
        self.row = row
        self.sqls: list[str] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchone(self):
        return self.row


def test_count_rows_supports_dict_cursor():
    cur = _DictCur({"c": 42})
    assert _count_rows(cur, "dws_case_metric", "doris") == 42


def test_count_rows_supports_tuple_cursor():
    class _TupleCur(_DictCur):
        def fetchone(self):
            return (7,)

    assert _count_rows(_TupleCur(None), "t", "mysql") == 7


def test_count_rows_empty_result_is_zero():
    assert _count_rows(_DictCur(None), "t", "mysql") == 0


# ── 分区对账谓词按引擎方言（Doris 无 MySQL 的 DATE()）───────────────────
def test_partition_where_doris_uses_half_open_range():
    where, params = _partition_where("stat_date", "2026-11-01", "doris")
    assert where == "`stat_date` >= %s AND `stat_date` < %s"
    assert params == ("2026-11-01", "2026-11-02")
    assert "CAST" not in where and "DATE(" not in where


def test_partition_where_mysql_uses_half_open_range():
    where, params = _partition_where("stat_date", "2026-11-01", "mysql")
    assert where == "`stat_date` >= %s AND `stat_date` < %s"
    assert params == ("2026-11-01", "2026-11-02")
    assert "DATE(" not in where


def test_partition_where_postgres_casts_params_only():
    where, params = _partition_where("stat_date", "2026-11-01", "postgres")
    assert where == '"stat_date" >= %s::date AND "stat_date" < %s::date'
    assert params == ("2026-11-01", "2026-11-02")


def test_partition_where_keeps_fixed_partition_columns():
    """多站同表：固定分区列必须一并入谓词（否则对账把别站行算进来）。"""
    where, params = _partition_where("stat_date", "2026-11-01", "doris",
                                     {"tenant_id": 1})
    assert where == "`tenant_id` = %s AND `stat_date` >= %s AND `stat_date` < %s"
    assert params == (1, "2026-11-01", "2026-11-02")


def test_partition_where_rejects_illegal_date():
    with pytest.raises(ValueError):
        _partition_where("stat_date", "2026/11/01", "doris")


# ── Doris staging 影子表：不复制目标表分区（打挂 Doris 的根因）────────────
class _DescCur:
    """DESC 返回列定义（DictCursor 形态，与目标数据源一致）。"""

    def __init__(self, rows):
        self.rows = rows
        self.sqls: list[str] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchall(self):
        return self.rows


_DESC_ROWS = [
    {"Field": "tenant_id", "Type": "INT", "Null": "No"},
    {"Field": "stat_date", "Type": "DATE", "Null": "No"},
    {"Field": "metric_value", "Type": "DECIMAL(18,2)", "Null": "Yes"},
]


def test_doris_shadow_ddl_does_not_copy_partitions():
    cur = _DescCur(_DESC_ROWS)
    ddl = _doris_shadow_ddl(cur, "dws_x__adh_stage", "dws_x")
    assert "PARTITION BY" not in ddl.upper()
    assert " LIKE " not in ddl.upper()
    assert "DUPLICATE KEY(`tenant_id`)" in ddl
    assert "DISTRIBUTED BY RANDOM BUCKETS 1" in ddl
    assert '"replication_num" = "1"' in ddl
    assert "`tenant_id` INT NOT NULL" in ddl
    assert "`metric_value` DECIMAL(18,2) NULL" in ddl


def test_doris_shadow_ddl_rejects_aggregate_column_types():
    rows = [{"Field": "k", "Type": "INT", "Null": "No"},
            {"Field": "uv", "Type": "BITMAP", "Null": "Yes"}]
    with pytest.raises(ValueError, match="聚合/嵌套类型"):
        _doris_shadow_ddl(_DescCur(rows), "st", "t")


def test_doris_shadow_ddl_rejects_nullable_or_non_scalar_head():
    nullable_head = [{"Field": "k", "Type": "INT", "Null": "Yes"},
                     {"Field": "v", "Type": "INT", "Null": "Yes"}]
    with pytest.raises(ValueError, match="排序键"):
        _doris_shadow_ddl(_DescCur(nullable_head), "st", "t")

    nested_head = [{"Field": "k", "Type": "BITMAP", "Null": "No"}]
    with pytest.raises(ValueError, match="聚合/嵌套类型"):
        _doris_shadow_ddl(_DescCur(nested_head), "st", "t")


def test_doris_shadow_ddl_rejects_empty_desc():
    with pytest.raises(ValueError, match="DESC 为空"):
        _doris_shadow_ddl(_DescCur([]), "st", "t")
