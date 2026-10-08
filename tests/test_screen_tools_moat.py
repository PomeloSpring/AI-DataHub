"""数据大屏工具 + 字模库的护城河/权限离线单测(不触真库、不连数据源)。

覆盖计划第 7 节要点:
  - §6: create_data_screen 拒收 widget 内裸 SQL 字段;缺 query 意图报错;非法图表类型报错。
  - 无身份(uid=0)经 governed_execute fail-closed(在意图解析路径抛错, 不回退裸执行)。
  - vis_library 内置字模 admin-only 保护(非 admin 改/下架被拒)。
  - datahub_screen 工具组已注册。
"""

import asyncio
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

screen_tools = importlib.import_module("backend.modules.mind.execution.sdk_tools.screen_tools")


def _is_err(result: dict) -> bool:
    return bool(result.get("isError"))


def _text_of(result: dict) -> str:
    return result["content"][0]["text"] if result.get("content") else ""


# ── create_data_screen: §6 拒收裸 SQL / 缺意图 / 非法类型 ──────────────

def test_create_screen_rejects_raw_sql_field():
    args = {"name": "S", "widgets": [{"title": "t", "chart_type": "bar", "sql": "SELECT 1"}]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    assert "SQL" in _text_of(res) or "sql" in _text_of(res)


def test_create_screen_requires_query_intent():
    args = {"name": "S", "widgets": [{"title": "t", "chart_type": "bar"}]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    assert "query" in _text_of(res) or "意图" in _text_of(res)


def test_create_screen_rejects_unknown_chart_type():
    args = {"name": "S", "widgets": [{"title": "t", "chart_type": "bogut", "query": {"object": "x"}}]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)


def test_create_screen_empty_widgets():
    assert _is_err(asyncio.run(screen_tools.create_data_screen({"name": "S", "widgets": []})))
    assert _is_err(asyncio.run(screen_tools.create_data_screen({"widgets": [{"chart_type": "bar"}]})))


# ── create_data_screen: 意图解析失败(未绑定) 走声明式错误, 不回退裸执行 ──

def test_create_screen_unbound_object_fails_closed(monkeypatch):
    # 让 resolve_binding 返回 None(未绑定), 应在建大屏/建图前就返回声明式错误
    import backend.semantics.intent as intent_mod
    import backend.semantics.binding_resolver as br_mod

    class _Q:
        object = "NoSuchObj"
        datasource_id = 0

    monkeypatch.setattr(intent_mod, "parse_intent",
                        lambda payload: (_Q(), None, []))
    monkeypatch.setattr(br_mod, "resolve_binding",
                        lambda *a, **k: (None, ["warn"]), raising=True)

    args = {"name": "S", "widgets": [{"title": "t", "chart_type": "bar", "query": {"object": "NoSuchObj"}}]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    assert "未绑定" in _text_of(res) or "bound" in _text_of(res)


# ── vis_library: 内置字模 admin-only 保护 ─────────────────────────────

def test_vis_library_builtin_admin_only(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    builtin_row = {"id": 7, "code": "cs_x", "name": "内置", "category": "chart_style",
                   "chart_type": "bar", "style_config": {}, "query_template": None,
                   "is_builtin": 1, "is_active": 1, "source": "system"}
    monkeypatch.setattr(vls, "execute_query", lambda *a, **k: dict(builtin_row))

    svc = vls.VisLibraryService()
    with pytest.raises(vls.BuiltinProtectedError):
        svc.update_component(7, {"name": "x"}, user_id=2, is_admin=False)
    with pytest.raises(vls.BuiltinProtectedError):
        svc.delete_component(7, user_id=2, is_admin=False)

    # admin 可改: execute_write 命中返回受影响行数
    called = {}
    def _fake_write(sql, params=None):
        called["sql"] = sql
        return 1
    monkeypatch.setattr(vls, "execute_write", _fake_write)
    assert svc.update_component(7, {"name": "ok"}, user_id=1, is_admin=True) is True


def test_vis_library_rejects_unknown_category():
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    svc = vls.VisLibraryService()
    with pytest.raises(ValueError):
        svc.create_component({"name": "x", "category": "not_a_category"}, user_id=1, is_admin=False)


# ── datahub_screen 工具组注册 ──────────────────────────────────────────

def test_screen_group_registered():
    sdk = importlib.import_module("backend.modules.mind.execution.sdk_tools")
    assert "screen" in sdk.TOOL_SERVER_BUILDERS
    srv_name, tools = sdk.TOOL_SERVER_TOOLS["screen"]
    assert srv_name == "datahub_screen"
    assert {"create_data_screen", "get_data_screen", "update_data_screen_chart",
            "list_vis_components", "get_vis_component", "save_vis_component"} <= set(tools)


# ── AS-BOT 工具白名单: 放开 screen 但禁 query（逐工具授权裁决，无强制组） ──

def test_asbot_tool_selection_includes_screen_excludes_query():
    """工具去留完全由 as_bot.tools.mcp 逐工具勾选裁决：
    勾了 screen/semantic 即注册，未勾 query 即不注册（LLM 无从调用）。"""
    tool_policy = importlib.import_module("backend.modules.mind.execution.tool_policy")
    policy = tool_policy.compile_policy({
        "as_bot_key": "as_bot", "mcp_server_ids": [],
        "tools": {"mcp": {"screen": ["create_data_screen", "get_data_screen"],
                          "semantic": ["get_metrics", "run_semantic_query"]}},
    })
    names = {n.rsplit("__", 1)[-1] for n in policy.allowed}
    assert "create_data_screen" in names and "get_metrics" in names
    assert "execute_sql" not in names and "check_sql" not in names
