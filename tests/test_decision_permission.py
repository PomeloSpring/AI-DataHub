"""决策建议的门控回归：默认全关、三前缀互不嵌套、菜单反查与授权同步。

这一组用例锁的是"这个功能对谁可见"的唯一口径（menu-permission-sync §3：
菜单显隐真值源 = adh_menu_registry.perm_code ⋈ adh_role_perms）。历史缺陷类型：

* 页面加了入口但菜单没登记 → 角色页配不到（空承诺）；
* 菜单登记了但没挂权限点 → 勾了也白勾的死开关；
* api_pattern 用一个总前缀 → fnmatch 的 `*` 连 `/` 一起吃，读码顺带放行发布；
* 迁移顺手给存量角色 INSERT IGNORE → 新能力上线即所有人可用（扩权）。

DB 不可用时相关用例 skip（与 tests/test_function_ai_access.py 同口径），
纯判定部分（前缀互斥、级别裁决）始终执行。
"""
import os
import sys
from fnmatch import fnmatch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.common import api_permission as ap
from backend.core import perm_link
from backend.modules.decision import engine, service
from backend.modules.decision.api import decisions as decisions_api

_MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "docker", "mysql", "decision_tree_migration.sql")


def _db_conn():
    try:
        import pymysql
        env = {}
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "backend", ".env")
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        return pymysql.connect(
            host=env["METADATA_DB_HOST"], port=int(env["METADATA_DB_PORT"]),
            user=env["METADATA_DB_USER"], password=env["METADATA_DB_PASSWORD"],
            database=env["METADATA_DB_DATABASE"], charset="utf8mb4", autocommit=True)
    except Exception:  # noqa: BLE001
        return None


def _rows(sql):
    conn = _db_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


# ── 1. 三前缀互不嵌套（中间件做不了减法，只能靠路径分段）────────────

class TestPatternDisjoint:
    """decision:read 的 GET/POST 绝不能顺带放行发布或配置写。"""

    READ = "/api/decisions/query*"
    MANAGE = "/api/decisions/config*"
    PUBLISH = "/api/decisions/publish*"

    @pytest.mark.parametrize("path", [
        "/api/decisions/publish/activation-risk",
        "/api/decisions/publish/activation-risk/retire",
        "/api/decisions/config/trees",
        "/api/decisions/config/advice/adv_x",
    ])
    def test_read_pattern_does_not_reach_write_paths(self, path):
        assert not fnmatch(path, self.READ)

    @pytest.mark.parametrize("path", [
        "/api/decisions/query/trees", "/api/decisions/query/evaluate",
        "/api/decisions/query/runs/12",
    ])
    def test_query_paths_are_not_matched_by_write_patterns(self, path):
        assert not fnmatch(path, self.MANAGE)
        assert not fnmatch(path, self.PUBLISH)

    def test_manage_does_not_reach_publish(self):
        """发布是独立权限码（两段式第二段），配置权不得顺带把人发布出去。"""
        assert not fnmatch("/api/decisions/publish/risk", self.MANAGE)

    def test_every_route_is_under_exactly_one_declared_prefix(self):
        """路由表必须整棵落进三个前缀之一，漏一个前缀 = 那个端点无人裁决。"""
        prefixes = (self.READ, self.MANAGE, self.PUBLISH)
        mapped = {
            "GET": ap._pattern_to_regex if False else None,   # 只作说明，不引内部实现
        }
        del mapped
        for r in decisions_api.router.routes:
            path = f"/api/decisions{r.path}"
            hits = [p for p in prefixes if fnmatch(path, p)]
            assert len(hits) == 1, f"{sorted(r.methods)} {path} 命中 {hits} 个前缀（应恰好 1 个）"
            # 方法也要对得上：GET 路由不得落在只声明 POST 的前缀上
            pattern_method = {"query": "GET,POST", "config": "POST,PUT,DELETE",
                              "publish": "POST"}[path.split("/")[3]]
            assert set(r.methods) <= set(pattern_method.split(",")), (path, r.methods)


# ── 2. 级别与动作裁决（纯函数，不依赖 DB）──────────────────────────

SPECS = {
    "decision.list": {"perm_code": "decision:read", "read_only": True, "label": "查看决策清单"},
    "decision.evaluate": {"perm_code": "decision:read", "read_only": True, "label": "跑一次评估"},
}


class TestAiLevelGate:
    def test_none_level_hides_both_actions(self):
        allowed, denied = perm_link.resolve_function_actions(
            SPECS, {"decision:read": {"ai_access": "none", "label": "查看决策建议"}})
        assert allowed == {}
        assert set(denied) == {"decision.list", "decision.evaluate"}
        assert "AI" in denied["decision.list"] or "级别" in denied["decision.list"]

    def test_read_level_enables_readonly_actions(self):
        allowed, _ = perm_link.resolve_function_actions(
            SPECS, {"decision:read": {"ai_access": "read", "label": "查看决策建议"}})
        assert set(allowed) == {"decision.list", "decision.evaluate"}

    def test_missing_perm_code_is_fail_closed(self):
        """角色没这个码 → 两个动作都不可用，且给出可解释原因（不是空清单）。"""
        allowed, denied = perm_link.resolve_function_actions(SPECS, {"chat:use": {"ai_access": "read"}})
        assert allowed == {}
        assert "decision:read" in denied["decision.list"]

    def test_manage_perms_are_not_ai_channel(self):
        """写口径（建树/发布）刻意 ai_access=none：AI 只能查建议，不能改判断标准。"""
        for code in ("decision:manage", "decision:publish"):
            assert perm_link.ai_channel(code, "") == perm_link.AI_CHANNEL_NONE


# ── 3. 迁移登记与默认全关（需要 DB）────────────────────────────────

DB = pytest.mark.skipif(_db_conn() is None, reason="元数据库不可用")


@DB
class TestRegistryRows:
    PERM_EXPECT = {
        "decision:read": ("workspace:chat", "/api/decisions/query*", "GET,POST", "read"),
        "decision:manage": ("data:decisions", "/api/decisions/config*", "POST,PUT,DELETE", "none"),
        "decision:publish": ("data:decisions", "/api/decisions/publish*", "POST", "none"),
    }

    def test_perm_registry_rows_match_plan(self):
        rows = {r["perm_code"]: r for r in (_rows(
            "SELECT perm_code, menu_key, api_pattern, api_method, ai_access, ai_action_key, "
            "is_active FROM adh_perm_registry WHERE perm_code LIKE 'decision:%'") or [])}
        assert set(rows) == set(self.PERM_EXPECT), "三个决策权限点必须齐（缺一个就是死开关）"
        for code, (menu, pattern, method, level) in self.PERM_EXPECT.items():
            r = rows[code]
            assert r["is_active"] == 1, code
            assert r["menu_key"] == menu, code
            assert r["api_pattern"] == pattern, code
            assert r["api_method"] == method, code
            assert r["ai_access"] == level, code

    def test_read_perm_declares_ai_action_keys(self):
        """ai_access=read 必须有消费方，否则角色页是个"选了不生效"的开关（空承诺）。"""
        rows = _rows("SELECT ai_action_key FROM adh_perm_registry WHERE perm_code='decision:read'")
        keys = {k.strip() for k in (rows[0]["ai_action_key"] or "").split(",") if k.strip()}
        assert keys == {"decision.list", "decision.evaluate"}
        from backend.modules.mind.execution import function_tools
        for action in keys:
            assert action in function_tools.FUNCTION_ACTION_SPECS, f"{action} 未登记，DB 侧是空头承诺"

    def test_write_perms_have_no_ai_channel_claimed(self):
        rows = {r["perm_code"]: r for r in (_rows(
            "SELECT perm_code, ai_access, ai_action_key FROM adh_perm_registry "
            "WHERE perm_code IN ('decision:manage','decision:publish')") or [])}
        for code, r in rows.items():
            assert (r["ai_action_key"] or "") == "", f"{code} 填了 ai_action_key 就是在广告不存在的写工具"
            assert r["ai_access"] == "none", f"{code} 开放 AI 写 = 让模型改业务判断口径"

    def test_menu_registry_rows(self):
        """菜单只登记配置面：工作空间侧栏不占一项（2026-11-12 改拍）。

        workspace:decisions 必须停在 is_active=0（而非被删）：角色页三处读取都带
        `WHERE is_active=1`，下架后“角色页菜单”与“实际导航”才逐项一致；
        而 decision:read 已改挂 workspace:chat，不能变成一个挂在不可见菜单下的孤儿权限点。
        """
        rows = {r["menu_key"]: r for r in (_rows(
            "SELECT menu_key, label, section, module, sort, perm_code, is_active "
            "FROM adh_menu_registry WHERE menu_key IN ('workspace:decisions','data:decisions','workspace:chat')") or [])}
        assert set(rows) == {"workspace:decisions", "data:decisions", "workspace:chat"}
        retired = rows["workspace:decisions"]
        assert retired["is_active"] == 0, "工作空间的决策菜单必须已下架（侧栏没入口却登记可见 = 双口径）"
        assert rows["workspace:chat"]["is_active"] == 1, "decision:read 挂的菜单必须可见，否则角色页里它无处可勾"
        d = rows["data:decisions"]
        assert (d["label"], d["section"], d["module"], d["perm_code"], d["is_active"]) == \
            ("决策建议", "数据目录", "data", "decision:manage", 1)

    def test_no_role_is_granted_by_default(self):
        """用户拍板"默认全关，管理员逐个开通"——迁移写一行 INSERT IGNORE 都算违反。"""
        rows = _rows("SELECT r.name, rp.perm_code FROM adh_roles r "
                     "JOIN adh_role_perms rp ON rp.role_id = r.id "
                     "WHERE rp.perm_code LIKE 'decision:%'")
        assert rows == [], f"决策建议被默认授权给了 {rows}（应全关，由管理员在角色页开通）"

    def test_non_admin_cannot_reach_any_decision_endpoint(self):
        """默认全关的实际效果：analyst/viewer 连读口都是 403。"""
        for role in ("analyst", "viewer", "Operations Manager"):
            assert not ap.check_api_permission(role, "GET", "/api/decisions/query/trees"), role
            assert not ap.check_api_permission(role, "POST", "/api/decisions/publish/x"), role
        # admin 走 unrestricted 旁路（不靠授权行）
        assert ap.check_api_permission("admin", "GET", "/api/decisions/query/trees")

    def test_migration_does_not_touch_is_active_of_others(self):
        """本批只登记自己的两行菜单；不得顺手把已退役的 data:metrics 等改回可见（那是扩权）。"""
        body = open(_MIGRATION, encoding="utf-8").read()
        statements = [s.strip() for s in body.split(";") if s.strip()]
        for sql in statements:
            if "is_active" in sql and "adh_menu_registry" in sql.split("is_active")[0][-40:]:
                target = sql[sql.lower().find("adh_menu_registry"):]
                assert "data:metrics" not in target and "data:knowledge-graph" not in target, sql[:160]
        assert "INSERT IGNORE INTO adh_role_perms" not in body

    def test_workspace_menu_sort_matches_sidebar_order(self):
        """角色页排序按 (module, sort)，与侧栏顺序不一致就是 §2.1 的漂移缺陷。"""
        rows = _rows("SELECT menu_key, section, sort FROM adh_menu_registry "
                     "WHERE module='workspace' AND is_active=1 ORDER BY sort")
        keys = [r["menu_key"] for r in rows]
        # 决策建议不在工作空间菜单里（侧栏没这一项，登记面也不能有）
        assert "workspace:decisions" not in keys, keys
        assert keys[:3] == ["workspace:dashboards", "workspace:chat", "workspace:reports"], keys
        assert keys[-2:] == ["workspace:assets", "workspace:scheduled"], keys


# ── 4. 未发布树不被消费（DB-free，服务层用假数据）──────────────────

class TestDraftNotConsumed:
    def test_active_trees_only_selects_active(self, monkeypatch):
        seen = {}

        def fake_query(sql, params=None, fetchone=False):
            seen["sql"] = sql
            return []
        monkeypatch.setattr(service, "execute_query", fake_query)
        service.active_trees(0)
        assert "status = 'active'" in seen["sql"] and "is_active = 1" in seen["sql"]

    def test_draft_tree_evaluation_is_skipped_with_reason(self, monkeypatch):
        monkeypatch.setattr("backend.semantics.execute.execute",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("草案不该取数")))
        row = {"id": 1, "tree_key": "t", "name": "T", "status": "draft", "version": 1,
               "is_active": 1, "tree_doc": {"root": {"type": "branch", "label": "r", "op": "all",
                                                     "children": []}}, "apply_sites": []}
        ctx = engine.EvalContext(mode="full", dictionary={"metric": [], "dimension": []})
        outcome = engine.evaluate_tree(row, ctx)
        assert outcome.verdict == "skipped" and "已发布" in outcome.reason


# ── 5. 实例可见性：按当时发起人的授权留痕 ──────────────────────────

class TestRunOwnership:
    def test_foreign_run_is_rejected_for_non_admin(self, monkeypatch):
        """实例里带实测值：别人按自己的授权取到的数，不能凭 run_id 就读到。"""
        monkeypatch.setattr(service, "get_run", lambda rid: {
            "id": rid, "user_id": 4242, "workspace_id": 301, "tree_key": "t"})
        monkeypatch.setattr(decisions_api, "authorize_resource_scope", lambda user, ws: ws)

        with pytest.raises(decisions_api.HTTPException) as exc:
            decisions_api.query_run(99, {"user_id": 7, "role": "analyst", "username": "bob"})
        assert exc.value.status_code == 403

    def test_admin_can_read_others_run(self, monkeypatch):
        monkeypatch.setattr(service, "get_run", lambda rid: {
            "id": rid, "user_id": 4242, "workspace_id": 301, "tree_key": "t",
            "verdict": "hit", "verdict_reason": "", "evidence": [], "hit_path": [],
            "advice_snapshot": []})
        monkeypatch.setattr(decisions_api, "authorize_resource_scope", lambda user, ws: ws)
        out = decisions_api.query_run(99, {"user_id": 1, "role": "admin", "username": "root"})
        assert out["id"] == 99 and out["verdict"] == "hit"

    def test_missing_run_is_404_not_empty(self, monkeypatch):
        monkeypatch.setattr(service, "get_run", lambda rid: None)
        with pytest.raises(decisions_api.HTTPException) as exc:
            decisions_api.query_run(1234, {"user_id": 7, "role": "analyst", "workspace_id": 1})
        assert exc.value.status_code == 404

    def test_run_projection_names_tree_and_flags_deletion(self, monkeypatch):
        """实例对外形状：树名反查得到；树被删过也不能显示成空白，要如实标注。"""
        monkeypatch.setattr(service, "get_tree", lambda key: {"name": "精度风险判定"})
        out = service._project_row_shape({"tree_key": "accuracy_risk"})
        assert out["tree_name"] == "精度风险判定" and out["tree_deleted"] is False

        monkeypatch.setattr(service, "get_tree", lambda key: None)
        gone = service._project_row_shape({"tree_key": "accuracy_risk"})
        assert gone["tree_deleted"] is True and "accuracy_risk" in gone["tree_name"]


# ── 6. 标识列宽度：全库 id 口径是 BIGINT ──────────────────────────
# 实测缺陷（2026-11-12 端到端核验）：三张表先前把 created_by / user_id /
# workspace_id 写成 INT，而本库的用户 id 是时间戳形态的 13 位数字（超 INT 上限），
# 存草案与文案直接 500：Out of range value for column 'created_by'。
# 单测里用的是小整数 id，照不出这个溢出 —— 只有真库 + 真用户会炸，所以这里
# 同时钉住“迁移声明”与“库内实况”（迁移改了不重新应用也算红）。
_ID_COLUMNS = {
    "adh_decision_trees": ["id", "workspace_id", "notify_channel_id",
                           "created_by", "updated_by", "published_by"],
    "adh_decision_advice": ["id", "workspace_id", "created_by", "updated_by"],
    "adh_decision_runs": ["tree_id", "user_id", "workspace_id", "conversation_id"],
}


class TestIdColumnWidth:
    @pytest.mark.parametrize("table,columns", list(_ID_COLUMNS.items()))
    def test_migration_declares_bigint(self, table, columns):
        sql = open(_MIGRATION, encoding="utf-8").read()
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split(") ENGINE=", 1)[0]
        for column in columns:
            line = next((l for l in body.splitlines() if l.strip().startswith(f"{column} ")), "")
            assert "BIGINT" in line.upper(), f"{table}.{column} 没声明成 BIGINT：{line.strip()!r}"
        # CREATE TABLE IF NOT EXISTS 不会改已在库的列，所以存量库要靠一段幂等 MODIFY
        assert f"ALTER TABLE {table} MODIFY COLUMN" in sql, f"{table} 缺幂等 MODIFY 修正段"

    @pytest.mark.parametrize("table,columns", list(_ID_COLUMNS.items()))
    def test_live_schema_is_bigint(self, table, columns):
        rows = _rows("SELECT COLUMN_NAME c, DATA_TYPE d FROM information_schema.COLUMNS "
                     f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'")
        if rows is None:
            pytest.skip("元数据库不可用")
        got = {r["c"]: r["d"] for r in rows}
        for column in columns:
            assert got.get(column) == "bigint", (
                f"库里 {table}.{column} 是 {got.get(column)}（应为 bigint）："
                f"decision_tree_migration.sql 没重新应用到当前库")
