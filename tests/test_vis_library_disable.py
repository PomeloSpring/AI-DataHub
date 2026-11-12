"""字模库「禁用」状态回归（停用但保留 ≠ 下架）。

口径（.qoder/rules/ui-vis-library-style.md §1/§3 + no-silent-degradation）：
* is_active=0 是下架（软删除），is_enabled=0 是禁用（条目仍在库里可查可再启用）；
* 禁用后一律**不可被选用**：清单默认不外发（fail-closed），主题包套不上、
  报表主题下拉与 AS-BOT 清单都不给，报告令牌按"未生效"显式标注原因；
* 内置字模的启用/禁用与编辑/下架同口径（admin-only）。
"""
import asyncio
import importlib
import json

import pytest

from backend.modules.mind.execution import report_themes as rt
from backend.modules.viz.services import report_service as reports
from backend.modules.viz.services.vis_library_service import vis_library_service

_PACK_SC = {"mode": "light", "palette": ["#0037CB"], "background": {"backgroundColor": "#F7F8FA"},
            "card": {"cardBg": "#ffffff"}, "charts": {"default": {"gradient": False}}}


def _row(**over):
    row = {"id": 7, "code": "tp_tech", "name": "科技霓虹", "category": "theme_pack",
           "style_config": dict(_PACK_SC), "is_active": 1, "is_enabled": 1, "is_builtin": 1}
    row.update(over)
    return row


# ── 服务层：状态切换与清单口径 ────────────────────────────────────────────

def test_disable_builtin_requires_admin(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    monkeypatch.setattr(vls, "execute_query", lambda *a, **k: dict(_row()))
    with pytest.raises(vls.BuiltinProtectedError):
        vls.vis_library_service.set_enabled(7, False, user_id=2, is_admin=False)


def test_admin_disable_writes_is_enabled(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    monkeypatch.setattr(vls, "execute_query", lambda *a, **k: dict(_row()))
    calls = []
    monkeypatch.setattr(vls, "execute_write", lambda sql, params=None: (calls.append((sql, params)), 1)[1])
    result = vls.vis_library_service.set_enabled(7, False, user_id=1, is_admin=True)
    assert "is_enabled = %s" in calls[0][0]
    assert calls[0][1] == (0, 7)
    # 返回摘要给前端提示影响面（禁用主题包会连带让系统主题不可选）
    assert result == {"id": 7, "code": "tp_tech", "name": "科技霓虹",
                      "category": "theme_pack", "is_enabled": 0}


def test_list_components_excludes_disabled_by_default(monkeypatch):
    """默认不外发禁用行（fail-closed）：新消费方漏传参数只会少给，不会把停用的字模当在用。"""
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    seen = []
    monkeypatch.setattr(vls, "execute_query",
                        lambda sql, params=None, **k: (seen.append(sql), [])[1])
    vls.vis_library_service.list_components("theme_pack")
    assert "is_enabled = 1" in seen[0] and "is_active = 1" in seen[0]
    vls.vis_library_service.list_components("theme_pack", include_disabled=True)
    assert "is_enabled = 1" not in seen[1]


def test_update_component_accepts_is_enabled(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    monkeypatch.setattr(vls, "execute_query", lambda *a, **k: dict(_row(is_builtin=0)))
    calls = []
    monkeypatch.setattr(vls, "execute_write", lambda sql, params=None: (calls.append((sql, params)), 1)[1])
    assert vls.vis_library_service.update_component(7, {"is_enabled": 0}, user_id=2, is_admin=False) is True
    assert "is_enabled" in calls[0][0] and 0 in calls[0][1]


# ── 消费口：报告令牌 / 主题清单 / 报表主题下拉 ────────────────────────────

@pytest.fixture
def packs(monkeypatch):
    """字模库桩：tp_tech 在用、tp_light 可被置为禁用；list_components 同口径过滤禁用行。"""
    state = {"rows": {"tp_tech": _row(), "tp_light": _row(id=8, code="tp_light", name="中性浅色")}}

    def get_component_by_code(code):
        row = state["rows"].get(code)
        return dict(row) if row else None

    def list_components(category="", include_inactive=False, include_disabled=False):
        return [dict(r) for r in state["rows"].values()
                if (include_disabled or r.get("is_enabled", 1) != 0)
                and (include_inactive or r.get("is_active", 1) != 0)]

    monkeypatch.setattr(vis_library_service, "get_component_by_code", get_component_by_code)
    monkeypatch.setattr(vis_library_service, "list_components", list_components)
    return state


def test_disabled_pack_falls_back_loudly(packs):
    """禁用主题包后报告不再用它，且降级原因要写明"已禁用"（不是含糊的"读不到"）。"""
    t = rt.resolve_theme("tech")
    assert t["source"] == "vis_library"
    packs["rows"]["tp_tech"]["is_enabled"] = 0
    t2 = rt.resolve_theme("tech")
    assert t2["source"] == "builtin_fallback"
    assert "禁用" in t2["reason"] and "科技霓虹" in t2["reason"] and "不一致" in t2["reason"]


def test_disabled_pack_absent_from_theme_lists(packs):
    """模型/报表主题下拉都不能把已停用的主题当选项推出去（否则从另一条通道绕过禁用）。"""
    packs["rows"]["tp_light"]["is_enabled"] = 0
    assert "light" not in rt.available_themes()
    assert "tech" in rt.available_themes()
    assert [o["theme"] for o in reports.report_theme_options()] == ["tech"]


def test_missing_is_enabled_column_keeps_packs_usable(packs):
    """未跑迁移的存量库（没有 is_enabled 键）不得被误判成禁用。"""
    packs["rows"]["tp_tech"].pop("is_enabled")
    assert rt.resolve_theme("tech")["source"] == "vis_library"


# ── AS-BOT 取用口：禁用字模不得从服务端通道绕过 ───────────────────────────

def test_screen_style_refs_skip_disabled(monkeypatch):
    st = importlib.import_module("backend.modules.mind.execution.sdk_tools.screen_tools")
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    chart = {"code": "cs_bar_neon", "name": "霓虹渐变柱", "category": "chart_style",
             "style_config": {"config": {"gradient": True, "borderRadius": 6}},
             "is_active": 1, "is_enabled": 1}
    monkeypatch.setattr(vls.vis_library_service, "get_component_by_code", lambda code: dict(chart))

    merged = {}
    st._resolve_style_refs(["cs_bar_neon"], merged)
    assert merged == {"gradient": True, "borderRadius": 6}      # 在用字模照常生效

    monkeypatch.setattr(vls.vis_library_service, "get_component_by_code",
                        lambda code: dict(chart, is_enabled=0))
    merged = {}
    st._resolve_style_refs(["cs_bar_neon"], merged)
    assert merged == {}, "已禁用的字模仍被合并进大屏配置（服务端绕过了禁用）"


def test_get_vis_component_rejects_disabled(monkeypatch):
    st = importlib.import_module("backend.modules.mind.execution.sdk_tools.screen_tools")
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    row = dict(_row())
    monkeypatch.setattr(vls.vis_library_service, "get_component_by_code", lambda code: dict(row))
    res = asyncio.run(st.get_vis_component({"code": "tp_tech"}))
    assert not res.get("isError")

    row["is_enabled"] = 0
    res2 = asyncio.run(st.get_vis_component({"code": "tp_tech"}))
    assert res2.get("isError") is True
    text = json.dumps(res2, ensure_ascii=False)
    assert "不可使用" in text and "禁用" in text


def test_disabled_report_template_is_not_usable(monkeypatch):
    """禁用报告模板后定时任务不能照用那份版式（禁用=不可使用，对所有分类一致）。"""
    row = {"id": 9, "code": "rt_monthly", "name": "月度经营模板", "category": "report_template",
           "style_config": {"format": "markdown", "content": "# 章节"},
           "is_active": 1, "is_enabled": 0}
    monkeypatch.setattr(vis_library_service, "get_component", lambda cid: dict(row))
    res = reports.resolve_report_template("9")
    assert res["found"] is False
    assert "禁用" in res["reason"] and "月度经营模板" in res["reason"]

    row["is_enabled"] = 1
    assert reports.resolve_report_template("9")["found"] is True
