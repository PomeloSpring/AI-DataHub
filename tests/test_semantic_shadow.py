"""Phase 7.1 shadow 双跑对拍 —— 新引擎通道对拍行为锁定。

- 对拍维度：列名序列 / 行数（min(len, 5000) 截断口径）/ 行值（前 100 行，str 归一
  容忍两通道类型保真差异）；
- fail-safe：shadow 侧任何异常不影响主链路（返回 None，主结果照常）；
- 开关 `SEMANTIC_ENGINE_SHADOW` 不开则零开销（新引擎不执行）。
"""

import pyarrow as pa
import pytest

from backend.semantics.execution import shadow


def test_compare_results_matched():
    cols, rows = ["a", "b"], [{"a": 1, "b": "x"}, {"a": None, "b": "y"}]
    assert shadow.compare_results(cols, rows, cols, list(rows)) == []


def test_compare_results_tolerates_type_repr():
    """str 归一：两通道类型保真不同（int vs str、None）不误报。"""
    main = [{"a": 1, "b": None}]
    shad = [{"a": "1", "b": None}]
    assert shadow.compare_results(["a", "b"], main, ["a", "b"], shad) == []


def test_compare_results_diffs():
    d = shadow.compare_results(["a"], [{"a": 1}], ["a", "x"], [{"a": 2}])
    assert any(x.startswith("columns:") for x in d)
    assert any(x.startswith("row[0].a:") for x in d)

    d = shadow.compare_results(["a"], [{"a": 1}], ["a"], [{"a": 1}, {"a": 2}])
    assert any(x.startswith("row_count:") for x in d)


def test_compare_results_row_cap_aligned_with_gates_truncation():
    """行数口径 min(len, 5000)：两侧都超界视为一致（与 gates head(5000) 对齐）。"""
    main = [{"a": i} for i in range(5100)]
    shad = [{"a": i} for i in range(5000)]
    assert shadow.compare_results(["a"], main, ["a"], shad) == []


def test_shadow_check_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(shadow, "SHADOW_ENABLED", False)

    def _boom(*a, **kw):
        raise AssertionError("disabled 时不得执行新引擎")

    monkeypatch.setattr("backend.semantics.execution.engine.SemanticEngine.execute_pushdown", _boom)
    assert shadow.shadow_check("SELECT 1", main_columns=["a"], main_rows=[{"a": 1}]) is None


def test_shadow_check_reports_diffs(monkeypatch):
    monkeypatch.setattr(shadow, "SHADOW_ENABLED", True)
    monkeypatch.setattr(
        "backend.semantics.execution.engine.SemanticEngine.execute_pushdown",
        lambda self, sql, **kw: pa.table({"a": [1, 2]}),
    )
    diffs = shadow.shadow_check(
        "SELECT a FROM t", main_columns=["a"], main_rows=[{"a": 1}],
        datasource_id=7, db_type="mysql", tag="t",
    )
    assert diffs and any(x.startswith("row_count:") for x in diffs)

    monkeypatch.setattr(
        "backend.semantics.execution.engine.SemanticEngine.execute_pushdown",
        lambda self, sql, **kw: pa.table({"a": [1]}),
    )
    assert shadow.shadow_check(
        "SELECT a FROM t", main_columns=["a"], main_rows=[{"a": 1}],
        datasource_id=7, db_type="mysql",
    ) == []


def test_shadow_check_fail_safe_on_engine_error(monkeypatch):
    monkeypatch.setattr(shadow, "SHADOW_ENABLED", True)

    def _boom(self, sql, **kw):
        raise RuntimeError("连接器炸了：Access denied for user 'root'@'10.0.0.9'")

    monkeypatch.setattr("backend.semantics.execution.engine.SemanticEngine.execute_pushdown", _boom)
    # shadow 侧异常吞掉返回 None，主链路不受影响
    assert shadow.shadow_check(
        "SELECT 1", main_columns=["a"], main_rows=[{"a": 1}], db_type="mysql",
    ) is None


def test_execute_hook_fail_safe_on_records_fn_error(monkeypatch):
    """execute 口的对拍钩子：records 构造异常也不影响主链路。"""
    monkeypatch.setattr(shadow, "SHADOW_ENABLED", True)
    from backend.semantics.execute import _shadow_check

    def _bad_records():
        raise RuntimeError("构造失败")

    _shadow_check("SELECT 1", tag="x", columns=["a"], records_fn=_bad_records)  # 不抛即通过
