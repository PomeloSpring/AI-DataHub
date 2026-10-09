"""Phase 6.3 治理强制层 —— RLS/mask 视图化强制行为锁定。

- mask 表达式按方言生成（null/partial/hash × mysql/pg），未知类型 fail-loud；
- 过滤视图体：hidden 剔除、masked 换表达式、row_filter 进 WHERE；列限制缺列清单拒绝生成；
- 表引用视图化改写：别名保留、无非法双重别名、JOIN 两侧天然覆盖（护栏 §4 行为迁至本层）；
- 视图按会话指纹注册，强制面在会话内真实生效（查询即过滤/脱敏）。
"""

import pyarrow as pa
import pytest

from backend.semantics.execution.session import SessionPool, policy_fingerprint
from backend.semantics.security import (
    PolicyViewManager,
    filtered_view_sql,
    mask_expr,
    rewrite_to_views,
    view_name,
)

# ── mask 表达式按方言生成 ────────────────────────────────────────


def test_mask_expr_by_dialect():
    assert mask_expr("phone", "null", "mysql") == "NULL"
    assert mask_expr("phone", "full", "mysql") == "NULL"          # 敏感基线 full → 置空
    assert mask_expr("phone", "hash", "mysql") == "MD5(phone)"
    assert mask_expr("phone", "hash", "postgres") == "MD5(CAST(phone AS TEXT))"
    assert mask_expr("phone", "partial", "mysql") == "CONCAT(LEFT(phone, 2), '***')"
    assert mask_expr("phone", "partial", "pg") == "CONCAT(LEFT(CAST(phone AS TEXT), 2), '***')"


def test_mask_expr_unknown_type_fails_loud():
    with pytest.raises(ValueError):
        mask_expr("phone", "plaintext", "mysql")  # 未知类型绝不放行原列


# ── 过滤视图体生成 ──────────────────────────────────────────────


def test_filtered_view_sql_no_restriction_returns_none():
    assert filtered_view_sql("t_user") is None
    assert filtered_view_sql("t_user", column_restriction={"hidden": [], "masked": {}}) is None


def test_filtered_view_sql_row_filter_only():
    sql = filtered_view_sql("t_user", row_filter="dept = 'sales'")
    assert sql == "SELECT * FROM t_user WHERE dept = 'sales'"


def test_filtered_view_sql_hidden_dropped_masked_rewritten():
    sql = filtered_view_sql(
        "t_user",
        row_filter="dept = 'sales'",
        column_restriction={"hidden": ["secret"], "masked": {"phone": "partial"}},
        columns=["id", "name", "phone", "secret"],
    )
    assert sql == (
        "SELECT id, name, CONCAT(LEFT(phone, 2), '***') AS phone FROM t_user WHERE dept = 'sales'"
    )
    assert "secret" not in sql.split(" FROM ")[0]  # hidden 列不得出现在投影


def test_filtered_view_sql_column_restriction_requires_columns():
    with pytest.raises(ValueError):
        filtered_view_sql(
            "t_user", column_restriction={"hidden": ["secret"]}, columns=None,
        )


# ── 表引用视图化改写（护栏 §4 行为迁至本层） ────────────────────


def test_rewrite_preserves_aliases_no_double_alias():
    from backend.semantics.sql_guard import parse_query

    sql = "SELECT t1.id FROM t_user t1 JOIN t_user t2 ON t1.id = t2.id"
    out, applied = rewrite_to_views(sql, ["t_user"])
    assert applied == ["t_user"]
    assert "sec_t_user" in out and "AS t1" in out and "AS t2" in out   # 别名保留
    assert "AS t t1" not in out and "AS sec_t_user t1" not in out      # 无非法双重别名
    parse_query(out, "mysql")  # 可再解析 = 语法合法（非法双重别名必致解析失败）


def test_rewrite_covers_both_join_sides_and_subqueries():
    sql = (
        "SELECT * FROM t_user JOIN (SELECT id FROM t_user WHERE x=1) s ON t_user.id = s.id"
    )
    out, applied = rewrite_to_views(sql, ["t_user"])
    assert out.count("sec_t_user") == 2  # FROM 与子查询两侧都覆盖
    assert applied == ["t_user"]


def test_rewrite_no_targets_passthrough():
    sql = "SELECT 1 FROM t_other"
    assert rewrite_to_views(sql, ["t_user"]) == (sql, [])


def test_view_name_disambiguates_from_physical():
    assert view_name("t_user") == "sec_t_user"  # 同名遮蔽会递归，必须区别于物理表名


# ── 会话内强制生效（视图化集成） ────────────────────────────────


def test_policy_views_registered_and_enforced_in_session():
    pool = SessionPool(max_buckets=4)
    mgr = PolicyViewManager(pool=pool)
    policy = {"user_id": 42, "rls": {"t_user": "dept = 'sales'"}}
    fingerprint = policy_fingerprint(policy)

    # 急切解析：先注册底层表，再注册过滤视图
    ctx = pool.get(fingerprint)
    ctx.register_record_batches("t_user", [pa.table({
        "id": [1, 2], "name": ["a", "b"],
        "phone": ["13800000000", "13900000000"],
        "secret": ["s1", "s2"], "dept": ["sales", "hr"],
    }).to_batches()])

    registered = mgr.apply(
        policy,
        {"t_user": {
            "row_filter": "dept = 'sales'",
            "column_restriction": {"hidden": ["secret"], "masked": {"phone": "null"}},
        }},
        columns={"t_user": ["id", "name", "phone", "secret", "dept"]},
    )
    assert registered == ["sec_t_user"]
    assert mgr.view_sql_of(policy, "t_user").startswith("SELECT id, name, NULL AS phone")

    # 会话内查询强制面：行过滤 + hidden 剔除 + mask 生效
    result = ctx.sql("SELECT * FROM sec_t_user").to_pydict()
    assert result["id"] == [1]                       # 行过滤：只剩 sales
    assert "secret" not in result                     # hidden 剔除
    assert result["phone"] == [None]                  # mask(null) 生效

    # 改写后的业务查询引用视图即受强制
    rewritten, _ = rewrite_to_views(
        "SELECT id, name FROM t_user", ["t_user"],
    )
    assert ctx.sql(rewritten).to_pydict() == {"id": [1], "name": ["a"]}
