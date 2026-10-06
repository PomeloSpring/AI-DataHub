"""UDF 注册中心离线回归：表达式校验、宏展开、版本引用（不连数据库）。

覆盖：纯表达式约束（拒子查询/表引用/非参数列/未注册函数）、
调用展开（实参代入、UDF 调 UDF 递归、参数个数校验）、
版本锁定与停用拒绝（fail-loud，不静默跳过）。
"""

import pytest

from services.dataflow.dag.udf_registry import (
    validate_expression, expand_udfs, UdfValidationError, _load_definitions,
)


# ── 表达式校验 ────────────────────────────────────────────────────


def test_validate_accepts_pure_expression():
    validate_expression("CASE WHEN x > 0 THEN 1 ELSE 0 END", [{"name": "x", "type": "int"}])
    validate_expression("CONCAT(LEFT(p, 3), '****')", [{"name": "p"}])
    validate_expression("1 + 2", [])


def test_validate_rejects_subquery():
    with pytest.raises(UdfValidationError):
        validate_expression("(SELECT max(a) FROM t)", [{"name": "x"}])
    with pytest.raises(UdfValidationError):
        validate_expression("x IN (SELECT 1)", [{"name": "x"}])


def test_validate_rejects_table_reference():
    with pytest.raises(UdfValidationError):
        validate_expression("t.col + x", [{"name": "x"}])


def test_validate_rejects_non_param_column():
    """列引用必须 ⊆ 参数名（纯函数不得捕获表列）。"""
    with pytest.raises(UdfValidationError, match="非参数列"):
        validate_expression("other_col + x", [{"name": "x"}])


def test_validate_rejects_unregistered_function():
    with pytest.raises(UdfValidationError, match="未注册函数"):
        validate_expression("mystery(x)", [{"name": "x"}])
    # 允许引用已注册 UDF
    validate_expression("mystery(x)", [{"name": "x"}], known_udfs={"mystery"})


def test_validate_rejects_multi_statement():
    with pytest.raises(UdfValidationError):
        validate_expression("SELECT 1; DROP TABLE t", [{"name": "x"}])


def test_validate_rejects_write_ops():
    with pytest.raises(UdfValidationError):
        validate_expression("1; UPDATE t SET x = 1", [{"name": "x"}])
    with pytest.raises(UdfValidationError):
        validate_expression("DELETE FROM t", [{"name": "x"}])


# ── 宏展开（打桩定义加载） ────────────────────────────────────────


def _defs():
    return {
        "phone_mask": {
            "id": 11, "name": "phone_mask", "version": 2,
            "expression": "CASE WHEN LENGTH(x) = 11 THEN CONCAT(LEFT(x, 3), '****', RIGHT(x, 4)) ELSE x END",
            "params": [{"name": "x", "type": "string"}], "is_active": 1,
        },
        "double_it": {
            "id": 12, "name": "double_it", "version": 1,
            "expression": "x * 2",
            "params": [{"name": "x", "type": "int"}], "is_active": 1,
        },
        "wrap_it": {
            "id": 13, "name": "wrap_it", "version": 1,
            "expression": "double_it(x) + 1",
            "params": [{"name": "x", "type": "int"}], "is_active": 1,
        },
    }


def test_expand_inlines_expression_with_args(monkeypatch):
    monkeypatch.setattr("services.dataflow.dag.udf_registry._load_definitions",
                        lambda refs: {k: v for k, v in _defs().items() if k in ["phone_mask"]})
    monkeypatch.setattr("services.dataflow.dag.udf_registry.udf_registry.known_udf_names",
                        lambda: {"phone_mask"})
    sql, used = expand_udfs("SELECT phone_mask(phone) AS p FROM users LIMIT 10", ["phone_mask:2"])
    assert "CASE WHEN LENGTH(phone) = 11" in sql
    assert "phone_mask(" not in sql
    assert used == [{"name": "phone_mask", "version": 2, "id": 11}]


def test_expand_nested_udf_calls(monkeypatch):
    monkeypatch.setattr("services.dataflow.dag.udf_registry._load_definitions",
                        lambda refs: _defs())
    monkeypatch.setattr("services.dataflow.dag.udf_registry.udf_registry.known_udf_names",
                        lambda: {"phone_mask", "double_it", "wrap_it"})
    sql, used = expand_udfs("SELECT wrap_it(a) AS v FROM t LIMIT 5", ["wrap_it", "double_it"])
    # 内层 double_it 也展开：x*2 的 x 代入 a
    assert "a * 2" in sql.replace("(", " ").replace(")", " ") or "a * 2" in sql
    assert "wrap_it(" not in sql and "double_it(" not in sql
    names = {item["name"] for item in used}
    assert names == {"wrap_it", "double_it"}


def test_expand_rejects_wrong_arity(monkeypatch):
    monkeypatch.setattr("services.dataflow.dag.udf_registry._load_definitions",
                        lambda refs: _defs())
    monkeypatch.setattr("services.dataflow.dag.udf_registry.udf_registry.known_udf_names",
                        lambda: {"phone_mask", "double_it", "wrap_it"})
    with pytest.raises(UdfValidationError, match="需要 1 个参数"):
        expand_udfs("SELECT phone_mask(a, b) FROM t LIMIT 1", ["phone_mask"])


def test_expand_rejects_undeclared_udf_reference(monkeypatch):
    """SQL 引用了未在 udf_refs 声明的 UDF → 显式报错（声明式引用，血缘可追溯）。"""
    monkeypatch.setattr("services.dataflow.dag.udf_registry._load_definitions",
                        lambda refs: {})
    monkeypatch.setattr("services.dataflow.dag.udf_registry.udf_registry.known_udf_names",
                        lambda: {"phone_mask"})
    with pytest.raises(UdfValidationError, match="未声明"):
        expand_udfs("SELECT phone_mask(a) FROM t LIMIT 1", [])


# ── 版本与生命周期（打桩 DB 访问） ────────────────────────────────


def test_load_definitions_resolves_version_and_rejects_missing(monkeypatch):
    from services.dataflow.dag.udf_registry import udf_registry
    monkeypatch.setattr(udf_registry, "get_version", lambda name, ver: _defs().get(name) if ver == 2 else None)
    monkeypatch.setattr(udf_registry, "get_active", lambda name: _defs().get(name))

    defs = _load_definitions(["phone_mask:2"])
    assert defs["phone_mask"]["version"] == 2

    with pytest.raises(UdfValidationError, match="不存在"):
        _load_definitions(["phone_mask:99"])

    with pytest.raises(UdfValidationError, match="不存在或未启用"):
        _load_definitions(["ghost_udf"])


def test_load_definitions_rejects_disabled_udf(monkeypatch):
    from services.dataflow.dag.udf_registry import udf_registry
    disabled = {**_defs()["double_it"], "is_active": 0}
    monkeypatch.setattr(udf_registry, "get_active", lambda name: disabled if name == "double_it" else None)
    with pytest.raises(UdfValidationError, match="已停用"):
        _load_definitions(["double_it"])
