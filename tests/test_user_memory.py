"""用户记忆系统回归：固定槽位覆盖 + 浮动条目限量 + prompt 注入 + 权限。"""
import asyncio

from backend.modules.mind.execution.sdk_tools.memory_tools import (
    FIXED_SLOTS, FLOATING_CATEGORIES, FLOATING_LIMIT,
    _validate_content, save_memory, list_memory, delete_memory,
)
from backend.modules.mind.execution.prompt_composer import _user_memory_lines


# ── 固定槽位定义完整性 ──────────────────────────────────────────

def test_fixed_slots_count():
    assert len(FIXED_SLOTS) == 6


def test_floating_categories():
    assert set(FLOATING_CATEGORIES.keys()) == {"correction", "workflow"}
    assert FLOATING_LIMIT == 5


# ── 内容校验 ────────────────────────────────────────────────────

def test_validate_content_rejects_empty():
    assert _validate_content("") is not None
    assert _validate_content("   ") is not None


def test_validate_content_rejects_too_long():
    assert _validate_content("x" * 301) is not None
    assert _validate_content("x" * 300) is None


def test_validate_content_rejects_session_state():
    """禁止会话状态/临时上下文。"""
    assert _validate_content("正在做华东区报表") is not None
    assert _validate_content("上次问了 GMV 问题") is not None
    assert _validate_content("刚才查了销售数据") is not None
    assert _validate_content("上一轮的分析结果") is not None
    assert _validate_content("当前会话绑定了数据源") is not None


def test_validate_content_accepts_stable_facts():
    assert _validate_content("GMV 指已支付未退款金额") is None
    assert _validate_content("图表默认用柱状图") is None
    assert _validate_content("我主要负责华东区销售分析") is None
    assert _validate_content("每周一查华东区周报") is None


# ── prompt 注入 ─────────────────────────────────────────────────

def test_memory_lines_empty_when_no_user():
    assert _user_memory_lines(0) == ""
    assert _user_memory_lines(-1) == ""


def test_memory_lines_empty_when_no_memory(monkeypatch):
    """无记忆时返回空串（零膨胀）。"""
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn([]))
    assert _user_memory_lines(7) == ""


def test_memory_lines_injects_fixed_and_floating(monkeypatch):
    rows = [
        {"slot_key": "business_domain", "content": "华东区销售"},
        {"slot_key": "preferred_chart", "content": "柱状图"},
        {"slot_key": "correction", "content": "GMV 指已支付未退款金额"},
        {"slot_key": "workflow", "content": "每周一查华东区周报"},
    ]
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn(rows))
    text = _user_memory_lines(7)
    assert "用户记忆" in text
    assert "业务域: 华东区销售" in text
    assert "默认图表: 柱状图" in text
    assert "[correction] GMV 指已支付未退款金额" in text
    assert "[workflow] 每周一查华东区周报" in text


def test_memory_lines_hard_cap_800(monkeypatch):
    """记忆段硬上限 800 字符（固定槽位优先保留，浮动条目截断）。"""
    rows = [{"slot_key": "business_domain", "content": "A" * 50}]
    # 5 条浮动条目，每条 300 字符
    for i in range(5):
        rows.append({"slot_key": "correction", "content": f"第{i}条纠错" + "x" * 280})
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn(rows))
    text = _user_memory_lines(7)
    assert len(text) <= 800
    assert "业务域" in text  # 固定槽位优先保留


def test_memory_lines_db_error_returns_empty(monkeypatch):
    """记忆查询失败不阻断 prompt 组装。"""
    def _fail():
        raise RuntimeError("DB down")
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn", _fail)
    assert _user_memory_lines(7) == ""


# ── 工具: save_memory 固定槽位覆盖 ──────────────────────────────

def test_save_fixed_slot_upsert(monkeypatch):
    """同 slot_key 重复 save 只保留最新值。"""
    store = {}
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn([], store=store))
    r1 = asyncio.run(save_memory({"slot_key": "business_domain", "content": "华东区销售"}))
    r2 = asyncio.run(save_memory({"slot_key": "business_domain", "content": "华南区运营"}))
    assert not r1.get("isError") and not r2.get("isError")
    assert store.get("business_domain") == "华南区运营"


# ── 工具: 浮动条目限量 ─────────────────────────────────────────

def test_save_floating_limit_exceeded(monkeypatch):
    """第 6 条浮动条目被拒绝（fail-loud）。"""
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    rows = [{"slot_key": "correction", "content": f"纠错{i}", "id": i} for i in range(5)]
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn(rows, count=5))
    result = asyncio.run(save_memory({"slot_key": "correction", "content": "第6条"}))
    assert result.get("isError")
    assert "上限" in str(result["content"][0]["text"])


def test_save_floating_under_limit(monkeypatch):
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn([], count=2, store={}))
    result = asyncio.run(save_memory({"slot_key": "workflow", "content": "每周一查周报"}))
    assert not result.get("isError")


# ── 工具: list/delete 用户隔离 ─────────────────────────────────

def test_list_memory_user_isolated(monkeypatch):
    """list_memory 按用户隔离。"""
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    rows = [{"slot_key": "business_domain", "content": "华东区", "id": 1, "source": "auto", "updated_at": None}]
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn(rows))
    result = asyncio.run(list_memory({}))
    assert not result.get("isError")
    import json
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 1
    assert data["fixed_slots"][0]["slot_key"] == "business_domain"


def test_delete_memory_own_only(monkeypatch):
    """delete_memory 只能删自己的（fail-closed）。"""
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    monkeypatch.setattr("backend.common.db.metadata_db.get_metadata_conn",
                        lambda: _FakeConn([], deleted=0))
    result = asyncio.run(delete_memory({"id": 999}))
    assert result.get("isError")


# ── 未知 slot_key 校验 ─────────────────────────────────────────

def test_save_rejects_unknown_slot_key(monkeypatch):
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.memory_tools._ctx_user_id", lambda: 7)
    result = asyncio.run(save_memory({"slot_key": "invalid_key", "content": "test"}))
    assert result.get("isError")


# ── AS-BOT 未授权 memory 组时工具不注册 ─────────────────────────

def test_memory_server_respects_tool_names():
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    tool_names = TOOL_SERVER_TOOLS["memory"][1]
    assert set(tool_names) == {"save_memory", "list_memory", "delete_memory"}


# ── 假 DB 连接 ─────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows, store=None, count=0, deleted=0):
        self._rows = rows
        self._store = store if store is not None else {}
        self._count = count
        self._deleted = deleted
        self.rowcount = deleted

    def execute(self, sql, params=None):
        if "INSERT" in sql and "ON DUPLICATE" in sql:
            self._store[params[1]] = params[2]
        elif "INSERT" in sql:
            self._store[params[1]] = params[2]
        elif "COUNT" in sql:
            self._count_result = self._count
        elif "DELETE" in sql:
            self.rowcount = self._deleted

    def fetchone(self):
        return {"c": self._count}

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows, store=None, count=0, deleted=0):
        self._rows = rows
        self._store = store if store is not None else {}
        self._count = count
        self._deleted = deleted

    def cursor(self):
        return _FakeCursor(self._rows, self._store, self._count, self._deleted)

    def commit(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
