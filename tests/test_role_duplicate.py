"""角色一键复制（含 AS-BOT 配置，不含绑定用户）—— 离线单测（不触碰真实数据库）。

覆盖:
  D1  duplicate_role 整份复制角色级授权（权限码/数据源/数据集/看板/RLS 绑定/数据范围/
      表级/列级），新角色 is_system=0，逐表复制计数随返回值回报;
  D2  **不复制绑定用户**（adh_user_roles / adh_users 零写入）——复制是"快速造角色"，
      人员归属由管理员另行指派;
  D3  AS-BOT 一对一复制：as_bot_key/name/display_name 随新角色名，工具/知识库/模型等
      纯配置原样搬；提示词/简介/persona 里提及原角色名处替换为新名并**显式回报替换次数**
      （prompt_renamed），不静默改写;
  D4  重名 / 来源不存在 fail-loud（ValueError）且整体回滚，不留半截角色；
      **中途失败**（如某张授权表写入报错）同样整体 ROLLBACK、不 COMMIT；
  D5  来源角色没有 AS-BOT 时 as_bot_copied=False，不静默补默认 bot（复制≠新建）;
  D6  API 层：成功回显 copied/prompt_renamed；ValueError → 400 带可读原因；
  D7  显式事务（START TRANSACTION/COMMIT/ROLLBACK）：池连接 autocommit=True，
      不开显式事务则失败后早前语句已各自提交（历史缺陷“报错但数据存下来了”）；
  D8  delete_role 把全部授权表 + AS-BOT + 用户绑定清干净（不留孤儿权限行）。

全部用假 get_metadata_conn 注入，不落真库。
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

role_service_module = importlib.import_module("backend.core.role_service")
roles_api = importlib.import_module("backend.modules.auth.api.roles")

ADMIN = {"role": "admin", "user_id": 1}

# 与 role_service._ROLE_COPY_TABLES 同一份口径（用例锁“八张授权表一张不漏”）
GRANT_COLS = {
    "adh_role_perms": ("perm_code",),
    "adh_role_datasource_access": ("datasource_id",),
    "adh_role_dataset_access": ("dataset_id",),
    "adh_role_dashboard_access": ("dashboard_id",),
    "adh_role_rls_policies": ("policy_id",),
    "adh_role_attributes": ("workspace_id", "attr_key", "attr_value"),
    "adh_role_table_access": ("datasource_id", "table_name", "access_type"),
    "adh_role_column_access": ("datasource_id", "table_name", "column_name", "access_type", "mask_pattern"),
}

# 与活库真实列型一致（2026-11 实测 SHOW COLUMNS）：这四张表 id 是自增列，
# INSERT 不得显式传 id（adh_role_dataset_access.id 还是 INT，塞 _gen_id() 雪花值
# 必报 1264 Out of range）；其余表 id 非自增，必须显式生成。
AUTO_ID_TABLES = {"adh_role_perms", "adh_role_dataset_access", "adh_role_dashboard_access", "adh_role_rls_policies"}


class FakeDB:
    """最小假库：读走内存状态，写只记流水（复制语义靠流水断言）。"""

    def __init__(self):
        self.roles = {
            100: {"id": 100, "name": "analyst", "display_name": "数据分析师",
                  "description": "来源角色描述", "is_system": 0, "is_active": 1},
        }
        self.grants = {t: {100: [dict(zip(cols, vals))]}
                       for t, cols, vals in (
                           ("adh_role_perms", ("perm_code",), ("report:read",)),
                           ("adh_role_datasource_access", ("datasource_id",), (5,)),
                           ("adh_role_dataset_access", ("dataset_id",), (31,)),
                           ("adh_role_dashboard_access", ("dashboard_id",), (7,)),
                           ("adh_role_rls_policies", ("policy_id",), (10,)),
                           ("adh_role_attributes", ("workspace_id", "attr_key", "attr_value"), (0, "region", "华东")),
                           ("adh_role_table_access", ("datasource_id", "table_name", "access_type"), (5, "orders", "read")),
                           ("adh_role_column_access", ("datasource_id", "table_name", "column_name", "access_type", "mask_pattern"), (5, "users", "phone", "masked", "partial")),
                       )}
        self.as_bots = {
            100: {
                "id": 555, "as_bot_key": "analyst", "name": "analyst",
                "display_name": "数据分析师", "description": "数据分析师角色的AI助手",
                "category": "custom",
                "system_prompt": "你是 数据分析师 的AI助手，负责协助完成数据分析与相关任务。",
                # persona 用 JSON 字符串（覆盖驱动回 str 的分支）；tools 用已解码对象（覆盖另一分支）
                "persona": json.dumps({"responsibility": "协助 数据分析师 角色完成数据分析",
                                       "style": "专业、简洁"}, ensure_ascii=False),
                "tools": {"mcp": {"semantic": ["run_semantic_query"]}, "groups": ["semantic"]},
                "mcp_server_ids": [], "knowledge_base_ids": [1, 2], "skills": ["s1"], "models": ["m1"],
                "execution_layer_id": 3, "chart_enabled": 1, "is_active": 1, "is_builtin": 0,
                "role_id": 100, "workspace_id": 0, "created_by": "admin", "i18n": {},
            },
        }
        self.writes = []       # [(归一化 sql, params)]
        self.txn = []          # 显式事务语句流水：START TRANSACTION / COMMIT / ROLLBACK
        self.fail_on = ""      # 非空 = 该前缀的 INSERT 抛错（模拟中途失败）
        self.commits = 0
        self.rollbacks = 0

    def conn_factory(self):
        return FakeConn(self)


class FakeCursor:
    def __init__(self, db: FakeDB):
        self.db = db
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = list(params or [])
        if s in ("START TRANSACTION", "COMMIT", "ROLLBACK"):
            self.db.txn.append(s)
            self._rows = []
            return
        if s.startswith("SHOW COLUMNS FROM "):
            table = s.split("FROM", 1)[1].split("WHERE")[0].strip()
            self._rows = [{"Extra": "auto_increment" if table in AUTO_ID_TABLES else ""}]
            return
        if s.startswith("INSERT INTO") or s.startswith("DELETE FROM"):
            if self.db.fail_on and s.startswith(self.db.fail_on):
                raise RuntimeError(f"模拟写入失败: {self.db.fail_on}")
            self.db.writes.append((s, p))
            self._rows = []
            return
        if "FROM adh_roles WHERE id =" in s:
            row = self.db.roles.get(p[0])
            self._rows = [row] if row else []
        elif "FROM adh_roles WHERE name =" in s:
            self._rows = [{"id": r["id"]} for r in self.db.roles.values() if r["name"] == p[0]]
        elif "FROM adh_as_bots WHERE role_id =" in s:
            row = self.db.as_bots.get(p[0])
            self._rows = [row] if row else []
        elif "FROM adh_role_" in s and "WHERE role_id =" in s:
            table = s.split("FROM", 1)[1].split("WHERE")[0].strip()
            self._rows = [dict(r) for r in self.db.grants.get(table, {}).get(p[0], [])]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    st = FakeDB()
    monkeypatch.setattr(role_service_module, "get_metadata_conn", st.conn_factory)
    return st


def _inserts(db, table):
    return [(s, p) for s, p in db.writes if s.startswith(f"INSERT INTO {table} ")]


# ── D1 整份复制角色级授权 ─────────────────────────────────────────────


def test_duplicate_copies_all_grant_tables(db):
    result = role_service_module.role_service.duplicate_role(
        100, "analyst_east", "华东分析师", "复制出来的描述")
    new_id = result["id"]

    role_rows = _inserts(db, "adh_roles")
    assert len(role_rows) == 1
    assert role_rows[0][1] == [new_id, "analyst_east", "华东分析师", "复制出来的描述"]
    # 新角色恒 is_system=0 / is_active=1（SQL 字面量）
    assert "is_system, is_active" in role_rows[0][0] and "0, 1" in role_rows[0][0]

    for table, cols in GRANT_COLS.items():
        rows = _inserts(db, table)
        assert len(rows) == 1, f"{table} 应恰好复制一行"
        sql, params = rows[0]
        src = db.grants[table][100][0]
        if table in AUTO_ID_TABLES:
            # 自增 id 列不得显式传 id（INT 列塞雪花值必 1264 Out of range）
            assert "(id, role_id" not in sql, f"{table} id 自增，不得显式传 id"
            assert params[0] == new_id and params[1:] == [src[c] for c in cols]
        else:
            assert "(id, role_id" in sql and params[0] and params[1] == new_id
            assert params[2:] == [src[c] for c in cols], f"{table} 业务列应与来源一致"

    assert result["copied"] == {"permissions": 1, "datasources": 1, "datasets": 1, "dashboards": 1,
                                "rls_policies": 1, "attributes": 1, "tables": 1, "columns": 1}
    # 显式事务：成功只 COMMIT 一次，中途不得提前提交
    assert db.txn == ["START TRANSACTION", "COMMIT"]


# ── D2 绑定用户不复制 ─────────────────────────────────────────────────


def test_duplicate_never_copies_user_bindings(db):
    role_service_module.role_service.duplicate_role(100, "analyst_east", "华东分析师", "")
    touched = [s for s, _ in db.writes if "adh_user_roles" in s or "adh_users" in s]
    assert touched == [], "复制不得触碰用户绑定（adh_user_roles）或用户表"
    # 来源角色的 AS-BOT/授权一行不动（只写新角色的行）
    for s, p in db.writes:
        if s.startswith("INSERT INTO adh_as_bots"):
            assert p[16] != 100  # role_id 列不得落回来源角色


# ── D3 AS-BOT 随新角色名 + 提示词替换并回报 ───────────────────────────


def test_duplicate_as_bot_follows_new_name_and_renames_prompt(db):
    result = role_service_module.role_service.duplicate_role(
        100, "analyst_east", "华东分析师", "")
    assert result["as_bot_copied"] is True

    bot_rows = _inserts(db, "adh_as_bots")
    assert len(bot_rows) == 1
    params = bot_rows[0][1]
    # (as_bot_key, name, display_name, description, category, system_prompt, persona, tools, ...)
    assert params[0] == "analyst_east" and params[1] == "analyst_east" and params[2] == "华东分析师"
    # 提示词/简介里的原角色名已换成新名
    assert "华东分析师" in params[5] and "数据分析师" not in params[5]
    assert "华东分析师角色的AI助手" == params[3]
    persona = json.loads(params[6])
    assert persona["responsibility"] == "协助 华东分析师 角色完成数据分析"
    assert persona["style"] == "专业、简洁"  # 与角色名无关的配置不动
    # 纯配置原样搬（工具/知识库/skills/模型/执行层/图表开关）
    assert json.loads(params[7]) == {"mcp": {"semantic": ["run_semantic_query"]}, "groups": ["semantic"]}
    assert json.loads(params[9]) == [1, 2] and json.loads(params[10]) == ["s1"] and json.loads(params[11]) == ["m1"]
    assert params[12] == 3 and params[13] == 1
    # 新 AS-BOT 恒 is_builtin=0 且绑定新角色
    assert params[15] == 0 and params[16] == result["id"]
    # 替换次数显式回报（提示词 1 处 + 简介 1 处 + persona 1 处）
    assert result["prompt_renamed"] == 3


def test_duplicate_as_bot_rename_single_pass_no_re_replace(db):
    #新名常包含旧名（如 *_copy/副本）：替换必须单趟完成，长 token 先匹配。
    # 逐趟 str.replace 会把上一趟的新文本再命中一次（sales→sales2 后，
    # sales_cn→sales_cn2 里的 "sales" 被二次命中成 sales2_cn2）。
    db.roles[100] = {"id": 100, "name": "sales", "display_name": "sales_cn",
                     "description": "", "is_system": 0, "is_active": 1}
    db.as_bots[100] = {"id": 555, "as_bot_key": "sales", "name": "sales",
                       "display_name": "sales_cn", "description": "", "category": "custom",
                       "system_prompt": "you serve sales_cn and sales", "persona": {},
                       "tools": {}, "mcp_server_ids": [], "knowledge_base_ids": [],
                       "skills": [], "models": [], "execution_layer_id": 0,
                       "chart_enabled": 1, "is_active": 1, "is_builtin": 0,
                       "role_id": 100, "workspace_id": 0, "created_by": "admin", "i18n": {}}
    role_service_module.role_service.duplicate_role(100, "sales2", "sales_cn2", "")
    params = _inserts(db, "adh_as_bots")[0][1]
    assert params[5] == "you serve sales_cn2 and sales2"
    assert "sales2_cn2" not in params[5]


# ── D4 fail-loud + 整体回滚 ───────────────────────────────────────────


def test_duplicate_rejects_existing_name_and_rolls_back(db):
    db.roles[200] = {"id": 200, "name": "analyst_east", "display_name": "已存在",
                     "description": "", "is_system": 0, "is_active": 1}
    with pytest.raises(ValueError, match="已存在"):
        role_service_module.role_service.duplicate_role(100, "analyst_east", "x", "")
    assert "ROLLBACK" in db.txn and "COMMIT" not in db.txn
    assert _inserts(db, "adh_roles") == [], "重名必须整体回滚，不留半截角色"


def test_duplicate_rejects_missing_source(db):
    with pytest.raises(ValueError, match="来源角色不存在"):
        role_service_module.role_service.duplicate_role(999, "new_role", "新角色", "")
    assert "ROLLBACK" in db.txn and "COMMIT" not in db.txn


def test_duplicate_mid_copy_failure_rolls_back_whole_txn(db):
    """中途写失败（如授权表报错）必须整体 ROLLBACK、绝不 COMMIT。

    回归锁的就是“报错但数据存下来了”事故：池连接 autocommit=True，
    不开显式事务则失败前的语句已各自提交，半截角色直接留在库里。
    """
    db.fail_on = "INSERT INTO adh_role_dataset_access"
    with pytest.raises(RuntimeError, match="模拟写入失败"):
        role_service_module.role_service.duplicate_role(100, "analyst_east", "华东分析师", "")
    assert db.txn == ["START TRANSACTION", "ROLLBACK"], "中途失败只 ROLLBACK，不得 COMMIT"


def test_duplicate_rejects_blank_or_overlong_name(db):
    with pytest.raises(ValueError, match="不能为空"):
        role_service_module.role_service.duplicate_role(100, "  ", "", "")
    with pytest.raises(ValueError, match="过长"):
        role_service_module.role_service.duplicate_role(100, "x" * 65, "", "")


# ── D5 无 AS-BOT 不静默补默认 ─────────────────────────────────────────


def test_duplicate_without_as_bot_reports_not_copied(db):
    del db.as_bots[100]
    result = role_service_module.role_service.duplicate_role(100, "analyst_east", "华东分析师", "")
    assert result["as_bot_copied"] is False and result["prompt_renamed"] == 0
    assert _inserts(db, "adh_as_bots") == [], "来源没有 AS-BOT 时不得静默补一个默认 bot"


# ── D7/D8 事务口径与删除清理 ───────────────────────────────────────────


def test_create_role_writes_role_and_as_bot_in_one_txn(db):
    role_service_module.role_service.create_role("newbie", "新角色", "描述")
    assert _inserts(db, "adh_roles") and _inserts(db, "adh_as_bots")
    assert db.txn == ["START TRANSACTION", "COMMIT"]


def test_delete_role_cleans_all_grant_tables_and_as_bot(db):
    """删角色必须清干净全部授权表（历史缺陷：漏 4 张表留孤儿权限行）。"""
    assert role_service_module.role_service.delete_role(100) is True
    deleted = [s for s, _ in db.writes if s.startswith("DELETE FROM")]
    for table in GRANT_COLS:
        assert any(f"DELETE FROM {table} WHERE role_id" in s for s in deleted), f"{table} 漏清会留孤儿行"
    assert any("DELETE FROM adh_as_bots" in s for s in deleted)
    assert any("DELETE FROM adh_user_roles" in s for s in deleted)
    assert db.txn == ["START TRANSACTION", "COMMIT"]


# ── D6 API 层 ─────────────────────────────────────────────────────────


def test_api_duplicate_role_returns_counts(monkeypatch):
    canned = {"id": 2, "name": "x", "display_name": "X",
              "copied": {"permissions": 3}, "as_bot_copied": True, "prompt_renamed": 2}
    monkeypatch.setattr(roles_api.role_service, "duplicate_role", lambda *a, **k: canned)
    res = roles_api.duplicate_role(1, roles_api.DuplicateRoleRequest(name="x"), admin=ADMIN)
    assert res["success"] is True
    assert res["copied"]["permissions"] == 3 and res["prompt_renamed"] == 2


def test_api_duplicate_role_maps_value_error_to_400(monkeypatch):
    def boom(*a, **k):
        raise ValueError("角色标识「x」已存在，请换一个")

    monkeypatch.setattr(roles_api.role_service, "duplicate_role", boom)
    with pytest.raises(HTTPException) as exc:
        roles_api.duplicate_role(1, roles_api.DuplicateRoleRequest(name="x"), admin=ADMIN)
    assert exc.value.status_code == 400 and "已存在" in exc.value.detail
