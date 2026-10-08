"""AS-BOT 解析服务单元测试 — 对应计划 test_as_bot_resolution.

覆盖 backend/modules/mind/execution/as_bots.py:
- resolve_as_bots: 角色-AS-BOT 一对一绑定(按 role_id 直查 adh_as_bots)
- _normalize: DB JSON 字段(persona/tools/mcp_server_ids/skills)解析
- default_as_bot: is_default 优先,否则首个
- collect_mcp_server_ids / collect_tool_groups / collect_standard_tools 合并去重

DB 通过 mock backend.common.db.execute_query 隔离。
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.mind.execution import as_bots as w


# ── 测试辅助 ──────────────────────────────────────────────────────

def _row(id_, name, **overrides):
    """构造一条 adh_as_bots(+is_default)DB 行(JSON 列以字符串存储)."""
    row = {
        "id": id_,
        "as_bot_key": name,
        "name": name,
        "display_name": overrides.pop("display_name", name),
        "description": overrides.pop("description", ""),
        "category": "custom",
        "system_prompt": "",
        "persona": json.dumps(overrides.pop("persona", {}), ensure_ascii=False),
        "tools": json.dumps(overrides.pop("tools", {}), ensure_ascii=False),
        "mcp_server_ids": json.dumps(overrides.pop("mcp_server_ids", [])),
        "datasource_ids": json.dumps(overrides.pop("datasource_ids", [])),
        "skills": json.dumps(overrides.pop("skills", []), ensure_ascii=False),
        "chart_enabled": overrides.pop("chart_enabled", 1),
        "permission_mode": "inherit",
        "workspace_id": overrides.pop("workspace_id", 5),
        "is_default": overrides.pop("is_default", 0),
    }
    row.update(overrides)
    return row


def _fake_query(role_rows=None, as_bot_rows=None):
    """按 SQL 关键字路由的 execute_query 替身(角色-AS-BOT 一对一)."""
    def _q(sql, params=None, fetchone=False):
        s = " ".join(sql.split())
        if "FROM adh_roles WHERE name" in s:
            return list(role_rows or [])
        if "FROM adh_as_bots WHERE role_id IN" in s:
            return list(as_bot_rows or [])
        return []
    return _q


# ── resolve_as_bots:角色-AS-BOT 一对一绑定 ──────────────────────────

class TestResolveOneToOne:
    def test_role_with_as_bot_returns_it(self):
        """角色有对应 AS-BOT 时直接返回."""
        rows = [_row(1, "analyst")]
        fq = _fake_query(role_rows=[{"id": 2}], as_bot_rows=rows)
        with patch("backend.common.db.execute_query", fq):
            result = w.resolve_as_bots(5, "analyst")
        assert [x["name"] for x in result] == ["analyst"]

    def test_role_without_as_bot_returns_empty(self):
        """角色存在但无 AS-BOT 时返回空列表."""
        fq = _fake_query(role_rows=[{"id": 10}], as_bot_rows=[])
        with patch("backend.common.db.execute_query", fq):
            result = w.resolve_as_bots(5, "viewer")
        assert result == []

    def test_unknown_role_returns_empty(self):
        """角色不存在时返回空列表."""
        fq = _fake_query(role_rows=[], as_bot_rows=[])
        with patch("backend.common.db.execute_query", fq):
            result = w.resolve_as_bots(5, "ghost")
        assert result == []

    def test_no_role_specified_returns_empty(self):
        """未指定角色时返回空列表."""
        fq = _fake_query(role_rows=[], as_bot_rows=[])
        with patch("backend.common.db.execute_query", fq):
            result = w.resolve_as_bots(5, "")
        assert result == []

    def test_as_bot_key_mismatch_raises(self):
        """指定 as_bot_key 但与角色绑定的不匹配时抛 PermissionError."""
        rows = [_row(1, "analyst")]
        fq = _fake_query(role_rows=[{"id": 2}], as_bot_rows=rows)
        with patch("backend.common.db.execute_query", fq):
            with pytest.raises(PermissionError, match="未授权"):
                w.resolve_as_bots(5, "analyst", as_bot_key="wrong_key")


# ── _normalize: JSON 列解析 ──────────────────────────────────────

class TestNormalize:
    def test_json_fields_parsed(self):
        row = _row(
            1, "analyst",
            persona={"responsibility": "查询数据", "style": "简洁"},
            tools={"groups": ["catalog", "semantic"], "standard": ["read", "grep"]},
            mcp_server_ids=[101, 102],
            skills=[{"name": "月度报告", "instruction": "按月汇总"}],
        )
        n = w._normalize(row)
        assert n["persona"]["responsibility"] == "查询数据"
        assert n["tools"]["groups"] == ["catalog", "semantic"]
        assert n["tools"]["standard"] == ["read", "grep"]
        assert n["mcp_server_ids"] == [101, 102]
        assert n["skills"][0]["name"] == "月度报告"
        assert n["chart_enabled"] is True

    def test_legacy_tools_as_list(self):
        # 兼容早期: tools 直接是组名列表
        row = _row(1, "a", tools=["catalog", "semantic"])
        row["tools"] = json.dumps(["catalog", "semantic"])
        n = w._normalize(row)
        assert n["tools"]["groups"] == ["catalog", "semantic"]
        assert n["tools"]["standard"] == []

    def test_bad_resource_json_is_rejected(self):
        row = _row(1, "a")
        row["mcp_server_ids"] = "oops"
        with pytest.raises(ValueError, match="资源绑定"):
            w._normalize(row)


# ── default_as_bot ────────────────────────────────────────────────

class TestDefaultAsBot:
    def test_is_default_preferred(self):
        rows = [_row(1, "a"), _row(2, "b", is_default=1)]
        ws = [w._normalize(r) for r in rows]
        assert w.default_as_bot(ws)["name"] == "b"

    def test_first_when_no_default(self):
        ws = [w._normalize(_row(1, "a")), w._normalize(_row(2, "b"))]
        assert w.default_as_bot(ws)["name"] == "a"

    def test_none_when_empty(self):
        assert w.default_as_bot([]) is None


# ── collect_* 合并去重 ───────────────────────────────────────────

class TestCollectors:
    def _sample_as_bots(self):
        return [
            w._normalize(_row(1, "a", mcp_server_ids=[101, 102], tools={"groups": ["catalog"], "standard": ["read"]})),
            w._normalize(_row(2, "b", mcp_server_ids=[102, 103], tools={"groups": ["semantic", "catalog"], "standard": ["grep", "read"]})),
        ]

    def test_collect_mcp_ids_dedup(self):
        assert w.collect_mcp_server_ids(self._sample_as_bots()) == [101, 102, 103]

    def test_collect_mcp_ids_ignores_non_int(self):
        ws = [{"mcp_server_ids": [5, "x", None, "7"]}]
        assert w.collect_mcp_server_ids(ws) == [5, 7]

    def test_collect_tool_groups_dedup(self):
        assert w.collect_tool_groups(self._sample_as_bots()) == ["catalog", "semantic"]

    def test_collect_standard_tools_dedup(self):
        assert w.collect_standard_tools(self._sample_as_bots()) == ["read", "grep"]

    def test_collectors_empty_input(self):
        assert w.collect_mcp_server_ids([]) == []
        assert w.collect_tool_groups([]) == []
        assert w.collect_standard_tools([]) == []
