"""语义层语料的**类型级归属**离线用例（不连库）。

锁的是这批最容易静默回退的三件事：

1. **类型分码 + 已下线类型**：`/api/admin/knowledge*` 一条路径服务多种 knowledge_type，
   中间件按 api_pattern × api_method 分不出类型，路由层必须再校一遍，否则只勾了一类
   语料的角色就能把另一类（含注入 AI 提示词的正文）整份拉走；而「SQL 示例对」已与
   数据集重合下线，带该 type 过来要回 410 说清去向，不能当成"未知类型"糊弄。
2. **消费面白名单只放一条精确路由**：`/api/knowledge/scenarios` 可公开读，
   但它旁边的 `/api/admin/knowledge*` 必须仍按码 —— 放宽成整个 knowledge 前缀就等于
   把语义层配置面（含物理表列名的语料）下发给所有登录用户。
3. **admin 恒放行**：补类型校验时漏掉这一条，会把超级用户自己挡在门外。

role_service 用 monkeypatch 注入，不碰真库。
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from backend.common.api_permission import _OPEN_READ_ROUTES, _SELF_ROUTE_PATTERNS
from backend.modules.auth.api import knowledge as kb

QUICK = "quick_path"
SQLP = "sql_pair"   # 已下线类型（与数据集重合），保留常量以锁退役行为


def hold(*codes):
    """把"用户持有这些权限码"接进 _held_codes（真值源仍是 role_service，这里只换数据）。

    _held_codes 是函数内 import，所以换 sys.modules 里的模块对象就能生效，
    不需要碰真库里的 adh_role_perms。
    """
    held = sorted(set(codes))
    monkey = pytest.MonkeyPatch()
    mod = types.ModuleType("backend.core.role_service")

    class _RS:
        @staticmethod
        def get_user_role_perm_codes(user_id, workspace_id=0):
            return list(held)

    mod.role_service = _RS()
    monkey.setitem(sys.modules, "backend.core.role_service", mod)
    return monkey


ADMIN = {"user_id": 1, "role": "admin", "username": "root"}
ANALYST = {"user_id": 9, "role": "analyst", "username": "bob"}
EDITOR = {"user_id": 10, "role": "editor", "username": "amy"}
VIEWER = {"user_id": 11, "role": "viewer", "username": "cid"}


def test_admin_is_never_locked_out_by_type_check():
    """admin 由中间件恒放行，路由层必须同口径，否则超级用户反而打不开自己的配置页。"""
    kb._assert_type_perm(ADMIN, QUICK, write=True)
    assert set(kb._readable_types(ADMIN)) == set(kb.TYPE_PERMS)
    # 已下线类型不是权限问题，admin 也一样拒 —— 不给"超级用户还能用死功能"的口子
    with pytest.raises(HTTPException) as exc:
        kb._assert_type_perm(ADMIN, SQLP, write=True)
    assert exc.value.status_code == 410


def test_type_without_code_is_not_readable():
    """只勾了 SQL 示例对那类旧码的角色，现在一类语料都读不到：码随功能一起退役。"""
    m = hold("sql-pairs:read", "sql-pairs:manage")
    try:
        with pytest.raises(HTTPException) as exc:
            kb._assert_type_perm(ANALYST, QUICK)
        assert exc.value.status_code == 403
        assert "分析场景" in exc.value.detail and "knowledge:read" in exc.value.detail
        assert kb._readable_types(ANALYST) == []
    finally:
        m.undo()


def test_retired_sql_pair_is_rejected_with_actionable_reason():
    """SQL 示例对已下线（与数据集重合）：带该 type 过来回 410 并指路，不当"未知类型"糊弄。"""
    m = hold("knowledge:read", "knowledge:manage")
    try:
        kb._assert_type_perm(EDITOR, QUICK, write=True)            # 现役类型照常可用
        for write in (False, True):
            with pytest.raises(HTTPException) as exc:
                kb._assert_type_perm(EDITOR, SQLP, write=write)
            assert exc.value.status_code == 410
            assert "已下线" in exc.value.detail and "数据集" in exc.value.detail
        assert SQLP not in kb.TYPE_PERMS and SQLP not in kb._readable_types(EDITOR)
    finally:
        m.undo()


def test_sql_pairs_perm_codes_retired_from_backend_source():
    """漂移守卫：功能下线后代码里不得再引用 sql-pairs:* 权限码
    （菜单行与权限点由 docker/mysql/sql_pairs_retire_migration.sql 从库里删除）。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("backend/modules/auth/api/knowledge.py", "backend/core/perm_link.py"):
        assert "sql-pairs" not in (root / rel).read_text(encoding="utf-8"), rel


def test_no_codes_means_no_type_readable():
    m = hold()   # 角色一个码都没勾
    try:
        with pytest.raises(HTTPException):
            kb._assert_type_perm(VIEWER, QUICK)
        assert kb._readable_types(VIEWER) == []
    finally:
        m.undo()


def test_unknown_type_is_rejected_not_defaulted():
    """未知 knowledge_type 报错，不静默按"最宽松的那类"处理。"""
    m = hold("knowledge:read")
    try:
        with pytest.raises(HTTPException) as exc:
            kb._assert_type_perm(EDITOR, "some_new_type")
        assert exc.value.status_code == 400
    finally:
        m.undo()


# ── 消费面白名单的边界（放宽过头就是泄露）──────────────────────────────────

def test_consumer_route_open_read_but_admin_prefix_still_gated():
    assert _OPEN_READ_ROUTES.match("/api/knowledge/scenarios")
    # 旁边任何一条都不许被顺手放宽（只放这一条精确路由）
    for path in ("/api/knowledge", "/api/knowledge/documents", "/api/admin/knowledge",
                 "/api/admin/knowledge/random", "/api/admin/knowledge/stats"):
        assert not _OPEN_READ_ROUTES.match(path), path
        assert not _SELF_ROUTE_PATTERNS.match(path), path


def test_consumer_response_has_no_body_or_physical_columns():
    """消费面只回标题+适用场景: 正文里含对象/指标编排细节与可能的物理名，不下发。"""
    import inspect
    src = inspect.getsource(kb.list_scenarios_for_chat)
    assert "SELECT id, title, metadata" in src          # 没有 content / related_tables
    assert "content" not in src.split("items = []")[1]  # 组装段里不出现正文字段
