"""DataFusion 方言桥离线单测 — 锁定 MySQL→DataFusion 转译映射(不触碰引擎)。

真值依据 2026-09 对运行中 datafusion-gateway 的实测(见 datafusion_dialect 模块文档):
- date_sub/date_add → interval 算术;IFNULL→COALESCE;CURDATE→CURRENT_DATE
- DATE_FORMAT 严禁转 TO_CHAR(引擎对 timestamp 原样返回格式串=静默错值)
- DATEDIFF/TIMESTAMPDIFF → epoch 差(date-date 在引擎返回 Duration,不可用)
- 未覆盖语法必须抛错,由 engine_client 保留原 SQL(引擎报错→直连回退),绝不静默出错值
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.shared.semantics.datafusion_dialect import to_datafusion


def test_date_sub_add_to_interval():
    assert to_datafusion(
        "SELECT DATE_SUB(NOW(), INTERVAL 7 DAY) AS d FROM t"
    ) == """SELECT NOW() - INTERVAL '7 DAY' AS d FROM t"""
    assert to_datafusion(
        "SELECT dt + 1 FROM t WHERE dt >= DATE_ADD(CURDATE(), INTERVAL 3 DAY)"
    ) == """SELECT dt + 1 FROM t WHERE dt >= CURRENT_DATE + INTERVAL '3 DAY'"""


def test_ifnull_and_year_extract():
    out = to_datafusion("SELECT IFNULL(a, 0), YEAR(dt) FROM t LIMIT 10")
    assert "COALESCE(a, 0)" in out
    # EXTRACT 整型化(MySQL 语义),避免引擎浮点 2026.0
    assert "CAST(EXTRACT(YEAR FROM CAST(dt AS DATE)) AS BIGINT)" in out
    assert "LIMIT 10" in out


def test_date_format_prefix_patterns():
    out = to_datafusion("SELECT DATE_FORMAT(created_at, '%Y-%m') AS m FROM t GROUP BY m")
    assert "AS VARCHAR), 1, 7)" in out and "SUBSTR(" in out
    # 绝不允许落到 TO_CHAR(引擎静默错值)
    assert "TO_CHAR" not in out


def test_unsupported_patterns_raise_not_silent():
    # 未覆盖的 DATE_FORMAT / TIMESTAMPDIFF 单位必须抛错(上层保留原 SQL),不得静默产出错语义
    with pytest.raises(Exception):
        to_datafusion("SELECT DATE_FORMAT(dt, '%x%v') FROM t")
    with pytest.raises(Exception):
        to_datafusion("SELECT TIMESTAMPDIFF(MONTH, a, b) FROM t")


def test_datediff_uses_epoch_form():
    out = to_datafusion("SELECT DATEDIFF(NOW(), dt) AS dd FROM t")
    assert "DATE_PART('epoch'" in out and "/ 86400" in out
    # 禁止裸 date-date(引擎返回 Duration 秒,非天数)
    assert ") - CAST(dt AS DATE))" not in out


def test_group_concat_and_plain_passthrough():
    assert "STRING_AGG(name, ',')" in to_datafusion("SELECT GROUP_CONCAT(name) FROM t")
    plain = "SELECT a, b FROM t WHERE c = 1"
    assert to_datafusion(plain).startswith("SELECT a, b FROM t")


def test_engine_client_fallback_keeps_original_sql():
    """转译失败时 _transpile_for_engine 必须原样返回,不吞查询。"""
    from services.shared.common.engine_client import _transpile_for_engine
    weird = "SELECT DATE_FORMAT(dt, '%x%v') FROM t"
    assert _transpile_for_engine(weird) == weird
    ok = "SELECT DATE_SUB(NOW(), INTERVAL 7 DAY) FROM t"
    assert "INTERVAL" in _transpile_for_engine(ok)
