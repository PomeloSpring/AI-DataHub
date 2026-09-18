"""Waker 解析服务单元测试 — 对应计划 test_waker_resolution.

覆盖 services/datamind/execution/wakers.py:
- resolve_wakers: 工作空间绑定 / 全局回退 / 角色白名单交集 / 无重叠回退候选
- _normalize: DB JSON 字段(persona/tools/mcp_server_ids/skills)解析
- default_waker: is_default 优先,否则首个
- collect_mcp_server_ids / collect_tool_groups / collect_standard_tools 合并去重

DB 通过 mock services.shared.common.db.execute_query 隔离。
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.execution import wakers as w


# ── 测试辅助 ──────────────────────────────────────────────────────

def _row(id_, name, **overrides):
    """构造一条 adh_wakers(+is_default)DB 行(JSON 列以字符串存储)."""
    row = {
        "id": id_,
        "waker_key": name,
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


def _fake_query(workspace_rows=None, global_rows=None, role_rows=None, role_waker_rows=None):
    """按 SQL 关键字路由的 execute_query 替身."""
    def _q(sql, params=None, fetchone=False):
        s = " ".join(sql.split())
        if "adh_workspace_wakers" in s:
            return list(workspace_rows or [])
        if "FROM adh_wakers WHERE is_active = 1 AND workspace_id = 0" in s:
            return list(global_rows or [])
        if "FROM adh_roles WHERE name" in s:
            return list(role_rows or [])
        if "adh_role_wakers" in s:
            return list(role_waker_rows or [])
        return []
    return _q


# ── resolve_wakers:候选与回退 ────────────────────────────────────

class TestResolveCandidates:
    def test_workspace_binding_returns_candidates(self):
        rows = [_row(1, "analyst", is_default=1), _row(2, "reporter")]
        with patch("services.shared.common.db.execute_query", _fake_query(workspace_rows=rows)):
            result = w.resolve_wakers(5, "")
        assert [x["name"] for x in result] == ["analyst", "reporter"]
        assert result[0]["is_default"] is True

    def test_fallback_to_global_when_no_workspace_binding(self):
        g = [_row(9, "global_default", workspace_id=0)]
        with patch(
            "services.shared.common.db.execute_query",
            _fake_query(workspace_rows=[], global_rows=g),
        ):
            result = w.resolve_wakers(5, "")
        assert [x["name"] for x in result] == ["global_default"]

    def test_no_wakers_at_all_returns_empty(self):
        with patch(
            "services.shared.common.db.execute_query",
            _fake_query(workspace_rows=[], global_rows=[]),
        ):
            assert w.resolve_wakers(5, "") == []


# ── resolve_wakers:角色白名单 ────────────────────────────────────

class TestRoleWhitelist:
    def test_role_binding_filters_candidates(self):
        rows = [_row(1, "analyst"), _row(2, "reporter")]
        fq = _fake_query(
            workspace_rows=rows,
            role_rows=[{"id": 10}],
            role_waker_rows=[{"waker_id": 2}],
        )
        with patch("services.shared.common.db.execute_query", fq):
            result = w.resolve_wakers(5, "analyst")
        assert [x["name"] for x in result] == ["reporter"]

    def test_role_without_binding_keeps_all_candidates(self):
        rows = [_row(1, "analyst"), _row(2, "reporter")]
        fq = _fake_query(workspace_rows=rows, role_rows=[{"id": 10}], role_waker_rows=[])
        with patch("services.shared.common.db.execute_query", fq):
            result = w.resolve_wakers(5, "viewer")
        assert len(result) == 2

    def test_unknown_role_keeps_all_candidates(self):
        rows = [_row(1, "analyst")]
        fq = _fake_query(workspace_rows=rows, role_rows=[])  # 角色不存在
        with patch("services.shared.common.db.execute_query", fq):
            result = w.resolve_wakers(5, "ghost")
        assert len(result) == 1

    def test_role_binding_no_overlap_falls_back_to_candidates(self):
        # 角色白名单与工作空间候选无交集 → 保留全部候选(不塌缩为空)
        rows = [_row(1, "analyst"), _row(2, "reporter")]
        fq = _fake_query(
            workspace_rows=rows,
            role_rows=[{"id": 10}],
            role_waker_rows=[{"waker_id": 999}],
        )
        with patch("services.shared.common.db.execute_query", fq):
            result = w.resolve_wakers(5, "analyst")
        assert len(result) == 2


# ── _normalize: JSON 列解析 ──────────────────────────────────────

class TestNormalize:
    def test_json_fields_parsed(self):
        row = _row(
            1, "analyst",
            persona={"responsibility": "查询数据", "style": "简洁"},
            tools={"groups": ["catalog", "query"], "standard": ["read", "grep"]},
            mcp_server_ids=[101, 102],
            skills=[{"name": "月度报告", "instruction": "按月汇总"}],
        )
        n = w._normalize(row)
        assert n["persona"]["responsibility"] == "查询数据"
        assert n["tools"]["groups"] == ["catalog", "query"]
        assert n["tools"]["standard"] == ["read", "grep"]
        assert n["mcp_server_ids"] == [101, 102]
        assert n["skills"][0]["name"] == "月度报告"
        assert n["chart_enabled"] is True

    def test_legacy_tools_as_list(self):
        # 兼容早期: tools 直接是组名列表
        row = _row(1, "a", tools=["catalog", "query"])
        row["tools"] = json.dumps(["catalog", "query"])
        n = w._normalize(row)
        assert n["tools"]["groups"] == ["catalog", "query"]
        assert n["tools"]["standard"] == []

    def test_bad_json_falls_back_to_default(self):
        row = _row(1, "a")
        row["persona"] = "{not valid json"
        row["mcp_server_ids"] = "oops"
        n = w._normalize(row)
        assert n["persona"] == {}
        assert n["mcp_server_ids"] == []


# ── default_waker ────────────────────────────────────────────────

class TestDefaultWaker:
    def test_is_default_preferred(self):
        rows = [_row(1, "a"), _row(2, "b", is_default=1)]
        ws = [w._normalize(r) for r in rows]
        assert w.default_waker(ws)["name"] == "b"

    def test_first_when_no_default(self):
        ws = [w._normalize(_row(1, "a")), w._normalize(_row(2, "b"))]
        assert w.default_waker(ws)["name"] == "a"

    def test_none_when_empty(self):
        assert w.default_waker([]) is None


# ── collect_* 合并去重 ───────────────────────────────────────────

class TestCollectors:
    def _sample_wakers(self):
        return [
            w._normalize(_row(1, "a", mcp_server_ids=[101, 102], tools={"groups": ["catalog"], "standard": ["read"]})),
            w._normalize(_row(2, "b", mcp_server_ids=[102, 103], tools={"groups": ["query", "catalog"], "standard": ["grep", "read"]})),
        ]

    def test_collect_mcp_ids_dedup(self):
        assert w.collect_mcp_server_ids(self._sample_wakers()) == [101, 102, 103]

    def test_collect_mcp_ids_ignores_non_int(self):
        ws = [{"mcp_server_ids": [5, "x", None, "7"]}]
        assert w.collect_mcp_server_ids(ws) == [5, 7]

    def test_collect_tool_groups_dedup(self):
        assert w.collect_tool_groups(self._sample_wakers()) == ["catalog", "query"]

    def test_collect_standard_tools_dedup(self):
        assert w.collect_standard_tools(self._sample_wakers()) == ["read", "grep"]

    def test_collectors_empty_input(self):
        assert w.collect_mcp_server_ids([]) == []
        assert w.collect_tool_groups([]) == []
        assert w.collect_standard_tools([]) == []
