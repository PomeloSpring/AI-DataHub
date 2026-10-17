"""用户记忆回归：单一事实源校验 + 固定槽位覆盖 + 浮动条目多行/限量 + 工具组注册 + prompt 注入。

覆盖的历史缺陷：
1. memory 工具组从未登记进 TOOL_SERVER_TOOLS → 工具不注册、LLM 无从写记忆
   （"AI 自动学习保存"是空承诺，adh_user_memory 长期 0 行）；
2. 唯一键 uk_user_slot(user_id, slot_key) 挡住浮动条目第二行 → 存第二条 correction 撞 1062；
3. save_memory 读 args["id"] 但 schema 未声明 id → 浮动条目更新分支不可达；
4. 会话上下文("正在做 X"/"本次结果")一旦入记忆会跨会话串味 → 服务端必须硬拒。
"""
import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest
from pymysql.err import IntegrityError

from backend.core import memory_service
from backend.core.memory_service import (
    FIXED_SLOTS, FLOATING_CATEGORIES, FLOATING_LIMIT,
    validate_content, save_entry, update_entry, delete_entry, list_entries,
)
from backend.modules.mind.execution.sdk_tools.memory_tools import (
    TOOL_SPECS, save_memory, list_memory, delete_memory,
)
from backend.modules.mind.execution.prompt_composer import MEMORY_RULES, _user_memory_lines

REPO = Path(__file__).resolve().parent.parent
UID = 7


# ── 模型定义 ────────────────────────────────────────────────────

def test_fixed_slots_count():
    assert len(FIXED_SLOTS) == 6


def test_tool_layer_reuses_service_constants():
    """工具层只是适配层，常量必须是服务层同一对象（防两侧漂移）。"""
    from backend.modules.mind.execution.sdk_tools import memory_tools
    assert memory_tools.FIXED_SLOTS is memory_service.FIXED_SLOTS
    assert memory_tools.FLOATING_LIMIT is memory_service.FLOATING_LIMIT


def test_floating_categories():
    assert set(FLOATING_CATEGORIES.keys()) == {"correction", "workflow"}
    assert FLOATING_LIMIT == 5


# ── 内容校验：只准跨会话稳定事实 ─────────────────────────────────

def test_validate_content_rejects_empty_and_long():
    assert validate_content("") is not None
    assert validate_content("   ") is not None
    assert validate_content("x" * 301) is not None
    assert validate_content("x" * 300) is None


@pytest.mark.parametrize("text", [
    "正在做华东区报表",
    "上次问了 GMV 问题",
    "刚才查了销售数据",
    "上一轮的分析结果",
    "当前会话绑定了数据源",
    "本次要记住我喜欢柱状图",        # 会话内措辞不得沉淀
    "这轮的结论是华东区更好",
    "接下来会先看周报",
])
def test_validate_content_rejects_session_context(text):
    """会话状态/临时上下文一律拒绝 —— 记忆会注入后续所有会话的提示词。"""
    assert validate_content(text) is not None


@pytest.mark.parametrize("text", [
    "查询结果是 1234 笔",
    "共 87 条案例",
    "GMV 指已支付未退款金额",        # 口径文字（无数值）合法
    "图表默认用柱状图",
    "我主要负责华东区销售分析",
    "每周一查华东区周报",
])
def test_validate_content_result_leak_boundary(text):
    is_leak = bool(re.search(r"(查询结果|共\s*\d+\s*(行|条|个|笔)|结果是\s*\d)", text))
    assert (validate_content(text) is not None) is is_leak


# ── 服务层：固定槽位覆盖 / 浮动条目多行 / 限量 ────────────────────

def test_save_fixed_slot_upsert(monkeypatch):
    """同 slot_key 重复 save 只保留最新值（DB 唯一键语义）。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "business_domain", "华东区销售")
    save_entry(UID, "business_domain", "华南区运营")
    rows = [r for r in table.rows if r["slot_key"] == "business_domain"]
    assert len(rows) == 1 and rows[0]["content"] == "华南区运营"
    assert rows[0]["entry_no"] == 1


def test_floating_entries_allow_multiple_rows(monkeypatch):
    """缺陷 2 回归：同类浮动条目第二条必须存得下（entry_no 递增，不撞唯一键）。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "correction", "GMV 指已支付未退款金额")
    save_entry(UID, "correction", "客单价按已支付订单计算")
    rows = [r for r in table.rows if r["slot_key"] == "correction"]
    assert [r["entry_no"] for r in rows] == [1, 2]
    assert [r["content"] for r in rows][0].startswith("GMV")


def test_floating_limit_exceeded_fail_loud(monkeypatch):
    """超过共享上限 5 条 → 拒绝并说明原因（不静默淘汰旧条目）。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    for i in range(FLOATING_LIMIT):
        save_entry(UID, "correction" if i % 2 == 0 else "workflow", f"稳定事实{i}")
    with pytest.raises(ValueError) as exc:
        save_entry(UID, "correction", "第 6 条")
    assert "上限" in str(exc.value)
    assert len(table.rows) == FLOATING_LIMIT


def test_save_rejects_unknown_slot_key(monkeypatch):
    _patch_conn(monkeypatch, _FakeTable())
    with pytest.raises(ValueError):
        save_entry(UID, "invalid_key", "test")


def test_save_requires_user_id():
    with pytest.raises(ValueError):
        save_entry(0, "business_domain", "华东区销售")


def test_update_entry_own_only(monkeypatch):
    """只能改自己的行；他人 id 视为不存在（fail-closed）。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "correction", "GMV 指已支付未退款金额")
    target = table.rows[0]["id"]
    update_entry(UID, target, "GMV 不含退款")
    assert table.rows[0]["content"] == "GMV 不含退款"
    with pytest.raises(ValueError):
        update_entry(999, target, "别人的记忆")


def test_update_entry_idempotent_when_content_unchanged(monkeypatch):
    """内容未变时重复保存不得被误报成"记忆不存在"。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "correction", "GMV 指已支付未退款金额")
    target = table.rows[0]["id"]
    update_entry(UID, target, "GMV 指已支付未退款金额")


def test_delete_entry_own_only(monkeypatch):
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "workflow", "每周一查华东区周报")
    assert delete_entry(999, table.rows[0]["id"]) == 0
    assert delete_entry(UID, table.rows[0]["id"]) == 1
    assert table.rows == []


def test_list_entries_scoped_by_user(monkeypatch):
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "business_domain", "华东区销售")
    save_entry(1234, "business_domain", "华南区运营")
    rows = list_entries(UID)
    assert len(rows) == 1 and rows[0]["content"] == "华东区销售"


# ── 工具层：错误可诊断 + schema 完整 ─────────────────────────────

def test_tool_save_returns_reason_on_reject(monkeypatch):
    """被拒时把原因回给 LLM（不静默丢弃、不假装成功）。"""
    _patch_conn(monkeypatch, _FakeTable())
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: UID)
    result = asyncio.run(save_memory({"slot_key": "correction", "content": "正在做的报表"}))
    assert result.get("isError")
    assert "会话" in str(result["content"][0]["text"])


def test_tool_save_requires_user_context(monkeypatch):
    _patch_conn(monkeypatch, _FakeTable())
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 0)
    result = asyncio.run(save_memory({"slot_key": "business_domain", "content": "华东区销售"}))
    assert result.get("isError")


def test_tool_save_floating_updates_by_id(monkeypatch):
    """缺陷 3 回归：save_memory 支持 id 更新已有浮动条目。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: UID)
    asyncio.run(save_memory({"slot_key": "correction", "content": "GMV 指已支付未退款金额"}))
    entry_id = table.rows[0]["id"]
    result = asyncio.run(save_memory({"slot_key": "correction", "content": "GMV 不含退款", "id": entry_id}))
    assert not result.get("isError")
    assert len(table.rows) == 1 and table.rows[0]["content"] == "GMV 不含退款"


def test_save_memory_schema_declares_all_handler_args():
    """schema 缺参数 = 该分支对 LLM 不可达（本仓库踩过的坑）。"""
    spec = next(s for s in TOOL_SPECS if s["name"] == "save_memory")
    assert set(spec["schema"]) == {"slot_key", "content", "source", "id"}


def test_list_memory_tool_shape(monkeypatch):
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: UID)
    asyncio.run(save_memory({"slot_key": "business_domain", "content": "华东区销售"}))
    asyncio.run(save_memory({"slot_key": "correction", "content": "GMV 指已支付未退款金额"}))
    data = json.loads(asyncio.run(list_memory({}))["content"][0]["text"])
    assert data["count"] == 2
    assert data["fixed_slots"][0]["label"] == "业务域"
    assert data["floating_entries"][0]["label"] == "口径纠错"
    assert data["floating_limit"] == FLOATING_LIMIT


def test_delete_memory_tool_own_only(monkeypatch):
    _patch_conn(monkeypatch, _FakeTable())
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: UID)
    result = asyncio.run(delete_memory({"id": 999}))
    assert result.get("isError")


# ── 工具组注册（缺陷 1：未登记 → 永不注册，LLM 无从调用）──────────

def test_memory_group_registered_in_sdk_tools():
    from backend.modules.mind.execution.sdk_tools import (
        TOOL_SERVER_BUILDERS, TOOL_SERVER_TOOLS, build_memory_server,
    )
    assert TOOL_SERVER_BUILDERS["memory"] is build_memory_server
    srv_name, tool_names = TOOL_SERVER_TOOLS["memory"]
    assert srv_name == "datahub_memory"
    assert set(tool_names) == {"save_memory", "list_memory", "delete_memory"}


def test_build_tool_servers_registers_memory_selection():
    """逐工具授权后必须真的产出 server + 精确 allowed_tools 名。"""
    from backend.modules.mind.execution.sdk_tools import build_tool_servers
    out = build_tool_servers("qoder", None, selection={"memory": ["list_memory"]})
    assert "datahub_memory" in out["servers"]
    assert out["allowed_tools"] == ["mcp__datahub_memory__list_memory"]


def test_tool_policy_accepts_memory_group():
    from backend.modules.mind.execution.tool_policy import normalize_tools
    tools = normalize_tools({"mcp": {"memory": ["save_memory", "list_memory", "delete_memory"]}})
    assert tools["mcp"]["memory"] == ["save_memory", "list_memory", "delete_memory"]


def test_as_bot_editor_catalog_labels_cover_memory_tools():
    """AS-BOT 编辑器以 TOOL_SERVER_TOOLS 为权威清单，缺标签会显示裸英文名。"""
    from backend.modules.platform.api.as_bots import _TOOL_GROUP_META, _TOOL_LABELS
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    assert "memory" in _TOOL_GROUP_META
    for name in TOOL_SERVER_TOOLS["memory"][1]:
        assert _TOOL_LABELS.get(name), f"工具 {name} 未登记中文标签"


# ── 单一事实源（防两侧规则漂移）──────────────────────────────────

def test_rest_api_reuses_memory_service():
    """REST 不得再自带一份槽位常量/校验/SQL —— 曾与工具侧漂移出不同上限行为。"""
    from backend.modules.auth.api import memory as memory_api
    src = inspect.getsource(memory_api)
    assert "business_domain" not in src, "REST 侧又硬编码了一份槽位清单"
    assert "FROM adh_user_memory" not in src, "REST 侧绕开服务层自己写 SQL"
    assert "memory_service" in src


def test_rest_rejection_detail_is_plain_string(monkeypatch):
    """拒写时的 detail 必须是可读字符串 —— 前端 toast 直接消费它，
    pydantic validator 报出的 422 结构化数组会渲染成 [object]。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.common.auth import get_current_user
    from backend.modules.auth.api import memory as memory_api

    _patch_conn(monkeypatch, _FakeTable())
    app = FastAPI()
    app.include_router(memory_api.router, prefix="/api/memory")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": UID}
    client = TestClient(app)

    resp = client.post("/api/memory/", json={"slot_key": "workflow", "content": "正在做的这份报表先记一下"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "会话" in detail
    # 固定槽位 upsert + 浮动条目多行走同一路径
    assert client.post("/api/memory/", json={"slot_key": "correction", "content": "GMV 不含退款"}).status_code == 200
    assert client.post("/api/memory/", json={"slot_key": "correction", "content": "客单价按已支付订单"}).status_code == 200


# ── prompt 注入 ─────────────────────────────────────────────────

def test_memory_lines_empty_when_no_user():
    assert _user_memory_lines(0) == ""
    assert _user_memory_lines(-1) == ""


def test_memory_lines_empty_when_no_memory(monkeypatch):
    _patch_conn(monkeypatch, _FakeTable())
    assert _user_memory_lines(UID) == ""


def test_memory_lines_injects_fixed_and_floating(monkeypatch):
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "business_domain", "华东区销售")
    save_entry(UID, "preferred_chart", "柱状图")
    save_entry(UID, "correction", "GMV 指已支付未退款金额")
    save_entry(UID, "workflow", "每周一查华东区周报")
    text = _user_memory_lines(UID)
    assert "用户记忆" in text and "非会话上下文" in text
    assert "业务域: 华东区销售" in text
    assert "默认图表: 柱状图" in text
    assert "[correction] GMV 指已支付未退款金额" in text
    assert "[workflow] 每周一查华东区周报" in text


def test_memory_lines_hard_cap_800(monkeypatch):
    """记忆段硬上限 800 字符（固定槽位优先保留，浮动条目截断）。"""
    table = _FakeTable()
    _patch_conn(monkeypatch, table)
    save_entry(UID, "business_domain", "A" * 50)
    for i in range(3):
        save_entry(UID, "correction", f"第{i}条纠错" + "x" * 280)
    for i in range(2):
        save_entry(UID, "workflow", f"习惯{i}" + "y" * 280)
    text = _user_memory_lines(UID)
    assert len(text) <= 800
    assert "业务域" in text


def test_memory_lines_db_error_does_not_break_prompt(monkeypatch):
    """记忆查询失败不阻断 prompt 组装（旁路降级，服务端留 warning）。"""
    def _fail():
        raise RuntimeError("DB down")
    monkeypatch.setattr("backend.core.memory_service.get_metadata_conn", _fail)
    assert _user_memory_lines(UID) == ""


def test_memory_rules_forbid_session_context():
    """提示词纪律必须与 validate_content 同向：只记稳定事实、严禁会话上下文。"""
    assert "严禁记录" in MEMORY_RULES
    assert "上次问了" in MEMORY_RULES
    assert "已记住" in MEMORY_RULES  # 写入对用户可见，不做静默学习


def test_memory_rules_injected_only_when_tool_available(monkeypatch):
    """未注册 save_memory 时不得宣称能写记忆（提示词与工具清单不能矛盾）。"""
    from backend.modules.mind.execution import prompt_composer as pc

    def _compose(capabilities):
        monkeypatch.setattr(pc, "_permission_summary", lambda *a, **k: "")
        monkeypatch.setattr(pc, "chart_contract_text", lambda: "")
        return pc.compose_system_prompt(None, [], capabilities=capabilities)

    with_tool = _compose({"tools": [{"name": "mcp__datahub_memory__save_memory", "description": ""}]})
    without_tool = _compose({"tools": [{"name": "mcp__datahub_semantic__get_metrics", "description": ""}]})
    assert "用户记忆读写规范" in with_tool
    assert "用户记忆读写规范" not in without_tool


# ── DDL 契约（迁移文件必须与代码列/键一致）───────────────────────

def test_migration_ddl_declares_entry_no_and_new_unique_key():
    ddl = (REPO / "docker/mysql/user_memory_migration.sql").read_text(encoding="utf-8")
    assert ddl.strip(), "迁移文件为空 → 新环境不会建表"
    assert "entry_no" in ddl
    assert "uk_user_slot_entry (user_id, slot_key, entry_no)" in ddl
    assert "DROP INDEX uk_user_slot" in ddl, "旧唯一键未撤掉，浮动条目第二行仍会撞 1062"


def test_service_sql_columns_exist_in_ddl():
    """服务层 SELECT 引用的列必须在 DDL 里存在（防 1054）。"""
    ddl = (REPO / "docker/mysql/user_memory_migration.sql").read_text(encoding="utf-8")
    create = re.search(r"CREATE TABLE IF NOT EXISTS adh_user_memory\s*\((.*?)\)\s*ENGINE",
                       ddl, re.S).group(1)
    cols = set()
    for line in create.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY", "UNIQUE", "KEY", "INDEX", "--")):
            continue
        m = re.match(r"^`?([a-z_][a-z0-9_]*)`?\s+\w+", line, re.I)
        if m:
            cols.add(m.group(1))
    for col in ("id", "user_id", "slot_key", "entry_no", "content", "source", "created_at", "updated_at"):
        assert col in cols, f"DDL 缺列 {col}"


# ── 假 DB（内存表，复刻 uk_user_slot_entry 唯一约束）──────────────

class _FakeTable:
    def __init__(self):
        self.rows: list = []
        self._next_id = 1

    def insert(self, user_id, slot_key, entry_no, content, source):
        if any(r["user_id"] == user_id and r["slot_key"] == slot_key and r["entry_no"] == entry_no
               for r in self.rows):
            raise IntegrityError(1062, "Duplicate entry for key 'adh_user_memory.uk_user_slot_entry'")
        self.rows.append({"id": self._next_id, "user_id": user_id, "slot_key": slot_key,
                          "entry_no": entry_no, "content": content, "source": source,
                          "created_at": None, "updated_at": None})
        self._next_id += 1

    def upsert(self, user_id, slot_key, entry_no, content, source):
        for r in self.rows:
            if (r["user_id"], r["slot_key"], r["entry_no"]) == (user_id, slot_key, entry_no):
                r["content"], r["source"] = content, source
                return
        self.insert(user_id, slot_key, entry_no, content, source)


class _FakeCursor:
    def __init__(self, table):
        self._table = table
        self._rows: list = []
        self._one = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        p = list(params or [])
        compact = " ".join(sql.split())
        if "ON DUPLICATE KEY" in compact:
            self._table.upsert(p[0], p[1], p[2], p[3], p[4])
        elif compact.startswith("INSERT INTO adh_user_memory"):
            self._table.insert(p[0], p[1], p[2], p[3], p[4])
        elif "COUNT(*) AS c" in compact:
            self._one = {"c": sum(1 for r in self._table.rows if r["slot_key"] in FLOATING_CATEGORIES
                                  and r["user_id"] == p[0])}
        elif "MAX(entry_no)" in compact:
            nos = [r["entry_no"] for r in self._table.rows
                   if (r["user_id"], r["slot_key"]) == (p[0], p[1])]
            self._one = {"n": (max(nos) + 1) if nos else 1}
        elif "SELECT id FROM adh_user_memory" in compact:
            self._rows = [r for r in self._table.rows
                          if (r["id"], r["user_id"]) == (p[0], p[1])]
        elif "UPDATE adh_user_memory SET content" in compact:
            hit = [r for r in self._table.rows if (r["id"], r["user_id"]) == (p[2], p[3])]
            for r in hit:
                r["content"], r["source"] = p[0], p[1]
            self.rowcount = len(hit)
        elif compact.startswith("DELETE FROM adh_user_memory"):
            before = len(self._table.rows)
            self._table.rows = [r for r in self._table.rows
                                if (r["id"], r["user_id"]) != (p[0], p[1])]
            self.rowcount = before - len(self._table.rows)
        elif compact.startswith("SELECT"):
            self._rows = [dict(r) for r in self._table.rows if r["user_id"] == p[0]]
        else:
            raise AssertionError(f"未预期 SQL: {compact}")

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, table):
        self._table = table

    def cursor(self):
        return _FakeCursor(self._table)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_conn(monkeypatch, table):
    """memory_service 是模块级 from-import，必须 patch 它自己命名里的引用。"""
    monkeypatch.setattr("backend.core.memory_service.get_metadata_conn", lambda: _FakeConn(table))
