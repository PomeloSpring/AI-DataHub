"""数据大屏工具（直建通道）+ 字模库的护城河/权限离线单测(不触真库、不连数据源)。

2026-11-17 拍板的口径（本文件即它的回归网）：
  - 会话内不再走设计面板：`create_data_screen` / `update_data_screen_chart` 不再重定向到
    `request_dashboard_design`，`design` 工具组整组退役（连 `dashboard_design_tools` 都不存在）。
  - 每图先落**语义型数据集**（adh_datasets: object_key + datasource_id + chart_preset.intent）
    再挂成 `source_type='dataset'` 的图；图表行不存 SQL/意图（`sql_query`/`semantic_query` 置空，
    否则非空 semantic_query 会被判成“服务端生成通道”而绕过数据集授权闸门）。
  - 直建不支持看板交互筛选参数（传 `filters` 直接拒，给外出路）；数据集取数只走治理口。
"""

import asyncio
import importlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

screen_tools = importlib.import_module("backend.modules.mind.execution.sdk_tools.screen_tools")
sdk = importlib.import_module("backend.modules.mind.execution.sdk_tools")

from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools import ExecutionContextVar

DS_IDS = [101, 102]
NEW_DS = 202                    # 新建数据集的 id


def _is_err(result: dict) -> bool:
    return bool(result.get("isError"))


def _text_of(result: dict) -> str:
    return result["content"][0]["text"] if result.get("content") else ""


def _ctx(datasource_id=DS_IDS[0]):
    return ExecutionContext(
        user_id=7, username="tester", user_role="analyst", workspace_id=1,
        datasource_id=datasource_id,
        extra={"available_datasource_ids": list(DS_IDS)})


@pytest.fixture
def box(monkeypatch):
    """把建屏/建数据集/取数/图表服务全部桩掉，只验工具层口径。

    用 import_module 取模块: `services/__init__` 里重导出了同名单例
    `dashboard_service`, `import a.b.c as m` 会 getattr 到实例而不是模块。
    """
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")
    ds_mod = importlib.import_module("backend.modules.viz.services.dataset_service")
    db_mod = importlib.import_module("backend.common.db")
    auth_mod = importlib.import_module("backend.common.auth")
    perm_mod = importlib.import_module("backend.core.perm_link")
    rg_mod = importlib.import_module("backend.modules.mind.execution.resource_guard")
    br_mod = importlib.import_module("backend.semantics.binding_resolver")
    qt_mod = importlib.import_module("backend.modules.mind.execution.sdk_tools.query_tools")

    box = {"charts": [], "datasets": [], "writes": [], "perm_calls": [],
           "dataset_queries": [], "validated_sources": [], "site_map": {}, "roles": (1,),
           "empty_titles": ("空图",), "reuse": None, "deleted": []}
    names: dict = {}

    monkeypatch.setattr(auth_mod, "get_user_by_id",
                        lambda uid: {"user_id": uid, "username": "tester", "user_role": "analyst"})
    monkeypatch.setattr(perm_mod, "require_write_perm",
                        lambda uid, wid, code, label="": box["perm_calls"].append(code))
    monkeypatch.setattr(br_mod, "resolve_binding",
                        lambda obj, datasource_id=0, **k: (SimpleNamespace(object_key=obj), []))
    monkeypatch.setattr(rg_mod, "validate_binding",
                        lambda ctx, binding, effective=0: box["validated_sources"].append(effective))
    # 站点/源业务名 → 源 id（业务名解析的替身）；未传则回落到会话生效源（同真实实现）。
    # 工具不得消费任何数值 id：这条替身不读 args 里的 datasource_id。
    monkeypatch.setattr(qt_mod, "_resolve_datasource_arg",
                        lambda args, ctx, table_hint="": box["site_map"].get(
                            str(args.get("site") or args.get("datasource") or ""),
                            ctx.datasource_id if ctx else None))

    def fake_query(sql, params=None, fetchone=False):
        if "FROM adh_user_roles" in sql:
            return [{"role_id": r} for r in box["roles"]]
        if "FROM adh_datasets WHERE name" in sql:
            return None
        if "FROM adh_datasets" in sql:
            return []                    # 复用探测：默认无既有数据集
        return None

    monkeypatch.setattr(db_mod, "execute_query", fake_query)
    monkeypatch.setattr(db_mod, "execute_write",
                        lambda sql, params=None: box["writes"].append((sql, params)) or 1)

    def create_dataset(payload, identity, origin="manual"):
        did = NEW_DS + len(names)
        names[did] = payload["name"]
        box["datasets"].append({"id": did, "origin": origin, **payload})
        return {"id": did, "name": payload["name"]}

    def query_dataset(ds_id, params, identity):
        box["dataset_queries"].append((int(ds_id), dict(params)))
        empty = any(t in (names.get(int(ds_id)) or "") for t in box["empty_titles"])
        rows = [] if empty else [{"c": 1}, {"c": 2}]
        return {"columns": ["c"], "rows": rows, "row_count": len(rows), "unpaged": True}

    monkeypatch.setattr(ds_mod, "create_dataset", create_dataset)
    monkeypatch.setattr(ds_mod, "query_dataset", query_dataset)

    class _DS:
        def create_dashboard(self, payload, user_id):
            box["dashboard"] = dict(payload)
            return 9001

        def delete_dashboard(self, dashboard_id, user_id):
            box["deleted"].append(int(dashboard_id))
            return True

        def get_dashboard(self, dashboard_id, user_id, enforce_visibility=False):
            return {"id": dashboard_id, "name": "S", "charts": [
                {"id": 5, "name": "旧图", "chart_type": "bar", "config": {"legend": True}}]}

    class _CS:
        def create_chart(self, dashboard_id, payload, identity=None):
            box["chart_identity"] = identity
            box["charts"].append(dict(payload))
            return 700 + len(box["charts"])

        def update_chart(self, dashboard_id, chart_id, data, identity=None):
            box["update"] = dict(data)
            box["update_identity"] = identity
            return True

    monkeypatch.setattr(dash_mod, "DashboardService", _DS)
    monkeypatch.setattr(dash_mod, "ChartService", _CS)
    monkeypatch.setattr(dash_mod, "_invalidate_dashboard_payload", lambda *a, **k: None)
    monkeypatch.setattr(dash_mod, "_invalidate_dashboard_cache", lambda *a, **k: None)

    token = ExecutionContextVar.set(_ctx())
    try:
        yield box
    finally:
        ExecutionContextVar.reset(token)


# ── 直建通道：图表/数据集口径 ─────────────────────────────────────────

def _one_widget(**kw):
    w = {"title": "营收趋势", "chart_type": "bar", "query": {"object": "订单", "metrics": ["营收"]}}
    w.update(kw)
    return {"name": "经营大屏", "widgets": [w]}


def test_create_screen_mounts_dataset_chart_not_sql(box):
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert not _is_err(res), _text_of(res)

    payload = box["charts"][0]
    assert payload["source_type"] == "dataset"
    assert payload["source_id"] == NEW_DS          # 挂的是数据集 id
    assert payload["query_source"] == "dataset"
    assert payload["semantic_query"] is None       # 非空会被判“服务端生成通道”→绕过数据集闸门
    assert payload.get("sql_query") is None
    assert "datasource_id" not in payload["config"]
    assert payload["config"]["dataset_name"]


def test_create_screen_materializes_semantic_dataset(box):
    asyncio.run(screen_tools.create_data_screen(_one_widget()))
    ds = box["datasets"][0]
    assert ds["origin"] == "ai_session"
    assert ds["object_key"] == "订单"
    assert ds["datasource_id"] == DS_IDS[0]
    assert ds["chart_preset"]["intent"] == {"object": "订单", "metrics": ["营收"]}
    # 建屏要 dashboard:manage + dataset:manage 双权限（fail-closed 的 perm_link 裁决）
    assert box["perm_calls"] == ["dashboard:manage", "dataset:manage"]
    assert box["chart_identity"]["user_id"] == 7   # 数据集闸门拿得到 uid


def test_create_screen_reuses_equivalent_dataset(monkeypatch, box):
    monkeypatch.setattr(screen_tools, "_find_reusable_dataset",
                        lambda *a, **k: {"id": 88, "name": "既有数据集合", "reused": True})
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert not _is_err(res), _text_of(res)
    assert box["datasets"] == []                   # 同意图不重复造资产
    assert box["charts"][0]["source_id"] == 88


def test_create_screen_chart_persist_error_is_classified_and_rolls_back(monkeypatch, box):
    """建图落库失败：回到因分类文案（不得兜成「权限/资源」），并回收半成品看板。

    历史缺陷（2026-11-17）：`adh_charts.query_source` 枚举缺 `dataset`，MySQL 报 1265，
    异常被工具壳兜成「权限、资源或执行环境不满足要求」→ 用户以为“chat 建大屏被权限卡住”。
    """
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")

    class _BoomChart:
        def create_chart(self, dashboard_id, payload, identity=None):
            raise ValueError('(1265, "Data truncated for column \'query_source\' at row 1")')

    monkeypatch.setattr(dash_mod, "ChartService", _BoomChart)
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert _is_err(res)
    text = _text_of(res)
    assert text and "权限" not in text           # 不得把 schema/DB 失败归因成权限
    assert box["deleted"] == [9001]               # 0 图空屏不得残留


def test_create_screen_dashboard_persist_error_is_classified(monkeypatch, box):
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")

    class _BoomDash:
        def create_dashboard(self, payload, user_id):
            raise RuntimeError("insert into adh_dashboards failed")

    monkeypatch.setattr(dash_mod, "DashboardService", _BoomDash)
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert _is_err(res)
    assert "权限" not in _text_of(res)


def test_update_screen_chart_persist_error_is_classified(monkeypatch, box):
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")

    class _BoomChart:
        def update_chart(self, dashboard_id, chart_id, data, identity=None):
            raise RuntimeError("update adh_charts failed")

    monkeypatch.setattr(dash_mod, "ChartService", _BoomChart)
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "title": "新标题"}))
    assert _is_err(res)
    assert "权限" not in _text_of(res)


def test_create_screen_without_role_fails_loud(box):
    box["roles"] = ()
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert _is_err(res)
    assert "没有任何角色" in _text_of(res)


def test_create_screen_rejects_interactive_filters(box):
    args = _one_widget()
    args["filters"] = [{"name": "range", "type": "date_range"}]
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    assert "参数" in _text_of(res) and "数据集" in _text_of(res)
    assert box["datasets"] == [] and box["charts"] == []


def test_create_screen_rejects_widget_control_with_param_guidance(box):
    """参数控件不得被当成"一种图表类型"含糊拒掉：必须说清控件的消费链路去哪配。

    历史反馈（2026-11-18）：模型以为直建能造筛选器，被"不支持的图表类型"打发后
    换别的图型重试，用户拿到的板还是不能筛；skill 侧同样缺少控件指导。
    """
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "widgets": [{"title": "日期", "chart_type": "widget_daterange",
                                    "query": {"object": "x"}}]}))
    assert _is_err(res)
    text = _text_of(res)
    assert "参数控件" in text and "widget_daterange" in text
    assert "B2.5" in text          # 指向技能里真正会下发的口径（references 不随正文下发）
    assert box["datasets"] == [] and box["charts"] == []


def test_update_screen_chart_rejects_widget_type(monkeypatch, box):
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "chart_type": "widget_select"}))
    assert _is_err(res)
    assert "参数控件" in _text_of(res)


def test_create_screen_resolves_site_business_name(box):
    box["site_map"] = {"中国站": 102}
    args = _one_widget(site="中国站")
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)
    assert box["datasets"][0]["datasource_id"] == 102
    assert box["validated_sources"] == [102]       # 本次生效源一起传，跨站不被误拒


def test_create_screen_ignores_numeric_datasource_id(box):
    args = _one_widget(datasource_id=999)
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)
    assert box["datasets"][0]["datasource_id"] == DS_IDS[0]   # 数值 id 不被消费


def test_create_screen_without_source_asks_for_site(box):
    ExecutionContextVar.set(_ctx(datasource_id=0))
    res = asyncio.run(screen_tools.create_data_screen(_one_widget()))
    assert _is_err(res)
    assert "site" in _text_of(res) or "datasource" in _text_of(res)
    assert "未绑定" not in _text_of(res)


# ── create_data_screen: §6 拒收裸 SQL / 缺意图 / 非法类型 ──────────────

def test_create_screen_rejects_raw_sql_field(box):
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "widgets": [{"title": "t", "chart_type": "bar", "sql": "SELECT 1"}]}))
    assert _is_err(res)
    assert "SQL" in _text_of(res) or "sql" in _text_of(res)


def test_create_screen_requires_query_intent(box):
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "widgets": [{"title": "t", "chart_type": "bar"}]}))
    assert _is_err(res)
    assert "query" in _text_of(res) or "意图" in _text_of(res)


def test_create_screen_rejects_unknown_chart_type(box):
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "widgets": [{"title": "t", "chart_type": "bogut", "query": {"object": "x"}}]}))
    assert _is_err(res)


def test_create_screen_empty_widgets(box):
    assert _is_err(asyncio.run(screen_tools.create_data_screen({"name": "S", "widgets": []})))
    assert _is_err(asyncio.run(screen_tools.create_data_screen({"widgets": [{"chart_type": "bar"}]})))


def test_create_screen_unbound_object_fails_closed(monkeypatch, box):
    br_mod = importlib.import_module("backend.semantics.binding_resolver")
    monkeypatch.setattr(br_mod, "resolve_binding", lambda *a, **k: (None, ["warn"]))
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "widgets": [{"title": "t", "chart_type": "bar", "query": {"object": "NoSuchObj"}}]}))
    assert _is_err(res)
    assert "未绑定" in _text_of(res) or "bound" in _text_of(res)
    assert box["datasets"] == [] and box["charts"] == []    # 落库前整单拒绝


# ── 行数/空图必须回报（零行不得被当成“创建成功”）────────────────────

def test_create_screen_reports_row_counts_and_empty_charts(box):
    args = {"name": "S", "widgets": [
        {"title": "有数图", "chart_type": "bar", "query": {"object": "订单"}},
        {"title": "空图", "chart_type": "pie", "query": {"object": "订单"}},
    ]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)
    payload = json.loads(_text_of(res))
    assert [a["row_count"] for a in payload["data_audit"]] == [2, 0]
    assert payload["empty_charts"] == ["空图"]
    assert "空图不是成功" in payload["message"]
    assert payload["url"] == "/screen/9001"
    assert len(payload["datasets"]) == 2
    assert len(box["charts"]) == 2
    # 预填缓存走创建后 UPDATE（create_chart 不接受非 demoData 的 data_cache）
    assert any(sql.startswith("UPDATE adh_charts SET data_cache") for sql, _ in box["writes"])


def test_create_screen_iframe_has_no_row_count(box):
    args = {"name": "S", "widgets": [{"title": "外链", "chart_type": "iframe",
                                      "config": {"url": "https://example.com"}}]}
    payload = json.loads(_text_of(asyncio.run(screen_tools.create_data_screen(args))))
    assert payload["data_audit"][0]["row_count"] is None
    assert payload["empty_charts"] == []      # iframe 不取数, 不得被当成空图误报
    assert box["datasets"] == []              # iframe 不落数据集


# ── 版面自检：坐标可核验，不靠“看着排好了” ───────────────────

def test_layout_audit_flags_overlap_and_out_of_canvas():
    audit = screen_tools._layout_audit([
        {"title": "A", "x": 0, "y": 0, "w": 384, "h": 168},
        {"title": "B", "x": 300, "y": 0, "w": 384, "h": 168},      # 与 A 横叠 84px
        {"title": "C", "x": 1800, "y": 200, "w": 384, "h": 300},    # 越出 1920 画布
    ])
    assert audit["ok"] is False
    assert ["A", "B"] in audit["overlaps"]
    assert audit["out_of_canvas"] == ["C"]
    assert audit["hint"]


def test_layout_audit_passes_tiling_row_and_reports_idle_px():
    kpis = [{"title": f"K{i}", "x": i * 384, "y": 0, "w": 384, "h": 168} for i in range(5)]
    audit = screen_tools._layout_audit(kpis)
    assert audit["ok"] is True and not audit["overlaps"]
    assert audit["idle_rows"] == []            # 5×384 正好铺满 1920
    half = screen_tools._layout_audit([{"title": "A", "x": 0, "y": 0, "w": 960, "h": 300}])
    assert half["ok"] is True
    assert half["idle_rows"] == [{"y": 0, "unused_px": 960}]   # 半屏卡旁边留白如实报


def test_create_screen_returns_layout_audit(box):
    payload = json.loads(_text_of(asyncio.run(screen_tools.create_data_screen(_one_widget()))))
    assert payload["layout_audit"]["ok"] is True
    assert payload["layout_audit"]["overlaps"] == []


def test_update_screen_chart_audits_whole_board(monkeypatch, box):
    """只挪一张图也可能压住邻居：自检必须算整板，并在 message 里显式暴露。"""
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")

    class _DS:
        def get_dashboard(self, dashboard_id, user_id, enforce_visibility=False):
            return {"id": dashboard_id, "name": "S", "charts": [
                {"id": 5, "name": "A", "chart_type": "bar", "config": {},
                 "position": {"x": 0, "y": 0, "w": 960, "h": 400}},
                {"id": 6, "name": "B", "chart_type": "bar", "config": {},
                 "position": {"x": 480, "y": 0, "w": 960, "h": 400}},
            ]}

        def update_chart(self, *a, **k):
            return True

    monkeypatch.setattr(dash_mod, "DashboardService", _DS)
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "title": "A2"}))
    assert not _is_err(res), _text_of(res)
    payload = json.loads(_text_of(res))
    assert payload["layout_audit"]["ok"] is False
    assert ["A2", "B"] in payload["layout_audit"]["overlaps"]
    assert "版面自检未通过" in payload["message"]


# ── update_data_screen_chart: 换意图 → 重挂数据集并清空旧 SQL ─────────

def test_update_screen_chart_remounts_dataset_and_clears_sql(box):
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "query": {"object": "订单", "metrics": ["营收"]}}))
    assert not _is_err(res), _text_of(res)
    upd = box["update"]
    assert upd["source_type"] == "dataset" and upd["source_id"] == NEW_DS
    assert upd["query_source"] == "dataset"
    assert upd["semantic_query"] is None and upd["sql_query"] is None
    assert box["update_identity"]["user_id"] == 7
    payload = json.loads(_text_of(res))
    assert payload["dataset_name"] and payload["row_count"] == 2


def test_update_screen_chart_reports_empty_result(box):
    box["empty_titles"] = ("旧图",)
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "query": {"object": "订单"}}))
    payload = json.loads(_text_of(res))
    assert payload["row_count"] == 0 and "取数为空" in payload["message"]


def test_update_screen_chart_visual_only_keeps_channel(box):
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "title": "新标题"}))
    assert not _is_err(res), _text_of(res)
    assert box["update"]["name"] == "新标题"
    assert "source_type" not in box["update"]        # 只改标题不动取数通道
    assert box["perm_calls"] == ["dashboard:manage"]  # 不因为改标题多要 dataset:manage
    assert box["datasets"] == []


def test_update_screen_chart_no_longer_redirects_to_design(monkeypatch, box):
    """系统域 ctx 也不得再转设计面板（那正是“对话里打开设计面板”的根因）。"""
    sm = importlib.import_module("backend.modules.mind.execution.sdk_tools.scoped_metadata")
    monkeypatch.setattr(sm, "system_scope", lambda ctx: True)
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "query": {"object": "订单"}}))
    assert not _is_err(res), _text_of(res)
    assert box["update"]["source_type"] == "dataset"


# ── design 工具组整组退役 ─────────────────────────────────────────────

def test_design_group_retired_from_registry():
    assert "design" not in sdk.TOOL_SERVER_BUILDERS
    assert "design" not in sdk.TOOL_SERVER_TOOLS
    with pytest.raises(ImportError):
        importlib.import_module(
            "backend.modules.mind.execution.sdk_tools.dashboard_design_tools")


def test_design_tools_absent_from_asbot_editor_catalog():
    as_bots = importlib.import_module("backend.modules.platform.api.as_bots")
    assert "design" not in as_bots._TOOL_GROUP_META
    for name in ("request_dashboard_design", "get_dashboard_design", "prepare_dashboard_design",
                 "get_business_semantics", "search_business_knowledge"):
        assert name not in as_bots._TOOL_LABELS


# ── 迁移脚本：幂等摘除 design 组 ──────────────────────────────────────

class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("UPDATE") and params:
            tools_json, key = params
            for row in self.conn.rows:
                if row["as_bot_key"] == key:
                    row["tools"] = tools_json

    def fetchall(self):
        return [dict(r) for r in self.conn.rows]

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_retire_design_tool_group_is_idempotent(monkeypatch, capsys):
    mig = importlib.import_module("backend.scripts.retire_design_tool_group")
    rows = [{"as_bot_key": "analyst",
             "is_active": 1,
             "tools": json.dumps({"groups": ["screen", "design"],
                                  "mcp": {"design": ["request_dashboard_design"],
                                          "screen": ["create_data_screen"]}},
                                 ensure_ascii=False)},
            {"as_bot_key": "viewer", "is_active": 1,
             "tools": json.dumps({"groups": ["semantic"], "mcp": {"semantic": ["get_metrics"]}})}]
    conn = _FakeConn(rows)
    monkeypatch.setattr(mig, "get_metadata_conn", lambda: conn)

    assert mig.migrate(apply=True) == 1
    tools = json.loads(rows[0]["tools"])
    assert "design" not in tools["mcp"] and "design" not in tools["groups"]
    assert tools["mcp"]["screen"] == ["create_data_screen"]     # 其他组原样保留
    assert json.loads(rows[1]["tools"])["mcp"] == {"semantic": ["get_metrics"]}
    assert conn.commits == 1
    capsys.readouterr()

    # 重跑：零变更（幂等），且核验残留为 0
    assert mig.migrate(apply=True) == 0
    assert mig.verify() == 0


# ── 提示词/渲染契约：必须出图 ─────────────────────────────────────────

def test_render_chart_section_forces_chart_over_bare_table():
    cc = importlib.import_module("backend.modules.mind.execution.chart_contract")
    section = cc.RENDER_CHART_SECTION
    assert "必须" in section and "render_chart" in section
    assert "≥2 个类别一律出图" in section
    assert "不得" in section and "只贴" in section
    assert "不豁免图表" in section


def test_orchestration_rules_do_not_excuse_numeric_questions_from_charts():
    pc = importlib.import_module("backend.modules.mind.execution.prompt_composer")
    rules = pc.TOOL_ORCHESTRATION_RULES
    assert "数值结果照" in rules                 # 事实型只收叙述长度，数值必须出图
    assert "不列表格（数值结果照" in rules        # 旧措辞“不列表格、”不再裸露


def test_screen_direct_rules_replace_design_rules():
    pc = importlib.import_module("backend.modules.mind.execution.prompt_composer")
    assert not hasattr(pc, "AS_BOT_DESIGN_RULES")
    rules = pc.SCREEN_DIRECT_RULES
    assert "create_data_screen" in rules
    assert "不得" in rules and "设计面板" in rules
    assert "site" in rules


# ── 数据集刷新链路：相对时间窗按当前时刻重算（不静默丢口径）──────────

def test_refresh_dataset_chart_passes_intent_window_from_config(monkeypatch):
    dash_mod = importlib.import_module("backend.modules.viz.services.dashboard_service")
    ds_mod = importlib.import_module("backend.modules.viz.services.dataset_service")
    auth_mod = importlib.import_module("backend.common.auth")

    captured: dict = {}
    monkeypatch.setattr(ds_mod, "query_dataset",
                        lambda ds_id, params, identity: captured.update(params) or
                        {"columns": ["c"], "rows": [], "row_count": 0})
    monkeypatch.setattr(dash_mod, "_dataset_bound_args", lambda ds_id, params: ({}, {}))
    monkeypatch.setattr(auth_mod, "get_user_by_id", lambda uid: {"user_role": "analyst"})

    class _Cur:
        def execute(self, *a, **k):
            pass

    chart = {"id": 1, "source_id": 66, "chart_type": "bar", "config": json.dumps({
        "limit": 10, "time_window": "7d", "time_column": "下单日期", "time_grain": "day",
        "filters": [{"dim": "状态", "op": "eq", "value": "已结案"}], "measures": ["营收"]})}
    svc = dash_mod.ChartService.__new__(dash_mod.ChartService)
    svc._refresh_dataset_chart(chart, {}, 7, 1, "tester", _Cur())

    assert captured["time_window"] == "7d"
    assert captured["time_column"] == "下单日期"
    assert captured["time_grain"] == "day"
    assert captured["filters"] == [{"dim": "状态", "op": "eq", "value": "已结案"}]
    assert captured["limit"] == 10


def test_dataset_semantic_query_passes_time_window(monkeypatch):
    ds_mod = importlib.import_module("backend.modules.viz.services.dataset_service")
    intent_mod = importlib.import_module("backend.semantics.intent")
    br_mod = importlib.import_module("backend.semantics.binding_resolver")
    gates = importlib.import_module("backend.semantics.gates")
    planner = importlib.import_module("backend.semantics.planner")

    seen: dict = {}

    def _parse(payload):
        seen.update(payload)
        return SimpleNamespace(object="订单", datasource_id=0), None, []

    monkeypatch.setattr(intent_mod, "parse_intent", _parse)
    monkeypatch.setattr(br_mod, "resolve_binding", lambda *a, **k: (SimpleNamespace(), []))
    monkeypatch.setattr(planner, "plan", lambda q, b: SimpleNamespace(sql="SELECT 1",
                                                                     output_header_map={}))
    monkeypatch.setattr(gates, "execute_semantic",
                        lambda *a, **k: SimpleNamespace(allowed=False, reason="policy stop",
                                                        blocked_at="policy", result={}))

    ds = {"id": 1, "name": "DS", "object_key": "订单", "datasource_id": 1}
    with pytest.raises(PermissionError):
        ds_mod._query_semantic(ds, {"time_window": "7d", "time_column": "下单日期"},
                              [], {"user_id": 7, "username": "tester", "workspace_id": 1})
    assert seen["time_window"] == "7d"
    assert seen["time_column"] == "下单日期"


# ── 字模库：内置字模 admin-only 保护 ─────────────────────────────────

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


# ── datahub_screen 工具组注册 / AS-BOT 逐工具授权 ────────────────────

def test_screen_group_registered():
    assert "screen" in sdk.TOOL_SERVER_BUILDERS
    srv_name, tools = sdk.TOOL_SERVER_TOOLS["screen"]
    assert srv_name == "datahub_screen"
    assert {"create_data_screen", "get_data_screen", "update_data_screen_chart",
            "list_vis_components", "get_vis_component", "save_vis_component"} <= set(tools)


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


# ── 工具入参契约（schema 类型 + 容错归一） ──────────────────────────────
# 历史缺陷（2026-11-17）：TOOL_SPECS 只写参数描述、没写类型，`to_json_schema`
# 把 widgets/query/theme/style 全声明成 string ⇒ 模型只能塞 JSON 字符串，
# `len(widgets)` 数的是字符数，1 张图也报「组件数量不能超过 12」；
# 传 "[]" 又能过长度校验、在 enumerate 字符串时炸成「widgets[0] 必须是对象」。

def test_create_screen_schema_declares_structured_params():
    from backend.modules.mind.execution.sdk_tools.compat import to_json_schema

    spec = next(s for s in screen_tools.TOOL_SPECS if s["name"] == "create_data_screen")
    schema = to_json_schema(spec["schema"])
    props = schema["properties"]
    assert props["widgets"]["type"] == "array"
    assert props["widgets"]["items"]["type"] == "object"
    assert props["widgets"]["maxItems"] == screen_tools._MAX_WIDGETS
    assert props["widgets"]["items"]["properties"]["query"]["type"] == "object"
    # theme 既收对象也收 'light'/'dark' 简写（字符串不能只写在描述里，须在 schema 放行）
    assert props["theme"]["type"] == ["object", "string"]
    assert props["style"]["type"] == "object"
    # 只有真正必填项进 required（旧写法把每个参数都当必填）
    assert schema["required"] == ["name", "widgets"]


def test_update_chart_schema_keeps_query_and_position_as_objects():
    from backend.modules.mind.execution.sdk_tools.compat import to_json_schema

    spec = next(s for s in screen_tools.TOOL_SPECS if s["name"] == "update_data_screen_chart")
    props = to_json_schema(spec["schema"])["properties"]
    assert props["query"]["type"] == "object"
    assert props["config"]["type"] == "object"
    assert props["position"]["type"] == "object"
    assert props["dashboard_id"]["type"] == "integer"
    assert props["chart_id"]["type"] == "integer"


def test_create_screen_accepts_widgets_as_json_string(box):
    """模型把 widgets 塞成 JSON 字符串时按解析处理：1 张图必须能建（旧缺陷按字符数判超限）。"""
    payload = _one_widget()
    args = {"name": payload["name"], "widgets": json.dumps(payload["widgets"], ensure_ascii=False)}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)
    assert len(box["charts"]) == 1


def test_create_screen_widget_count_uses_real_items(box):
    """超限报的是真实条数，不是字符串长度。"""
    payload = _one_widget()
    many = payload["widgets"] * (screen_tools._MAX_WIDGETS + 1)
    args = {"name": payload["name"], "widgets": json.dumps(many, ensure_ascii=False)}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    text = _text_of(res)
    assert f"组件数量 {screen_tools._MAX_WIDGETS + 1} 超过上限" in text
    assert box["charts"] == []


def test_create_screen_single_widget_object_is_wrapped(box):
    payload = _one_widget()
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": payload["name"], "widgets": payload["widgets"][0]}))
    assert not _is_err(res), _text_of(res)
    assert len(box["charts"]) == 1


def test_create_screen_empty_array_fails_loud(box):
    res = asyncio.run(screen_tools.create_data_screen({"name": "空屏", "widgets": "[]"}))
    assert _is_err(res)
    assert "widgets is required" in _text_of(res)


def test_create_screen_malformed_json_fails_loud(box):
    """坏 JSON 必须显式报错，不得静默当空值（no-silent-degradation §1）。"""
    res = asyncio.run(screen_tools.create_data_screen({"name": "坏屏", "widgets": "{oops"}))
    assert _is_err(res)
    text = _text_of(res)
    assert "widgets" in text and "JSON" in text
    assert box["charts"] == []


def test_create_screen_non_object_widget_points_at_index(box):
    payload = _one_widget()
    args = {"name": payload["name"], "widgets": payload["widgets"] + ["oops"]}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert _is_err(res)
    assert "widgets[1] 必须是对象" in _text_of(res)


def test_coerce_struct_rejects_wrong_type():
    with pytest.raises(screen_tools.ScreenIntentError):
        screen_tools._coerce_struct(123, "theme", dict)
    assert screen_tools._coerce_struct('{"mode": "dark"}', "theme", dict) == {"mode": "dark"}
    assert screen_tools._coerce_struct("", "theme", dict) is None
    assert screen_tools._coerce_struct({"a": 1}, "widgets", (list, dict)) == {"a": 1}


# ── theme 简写 / 图表类型别名（反馈 2026-11-17） ───────────────────────────
# 现象：模型传 `theme: "light"` 被 SDK 按对象类型拒（Input validation error:
# 'light' is not of type 'object'）；传 `chart_type: "big_number"` 又被当成未知图型。
# 两者都是“模型话术 vs 工具契约”的差异，不是数据/权限问题。

def test_create_screen_accepts_theme_string_shorthand(box):
    args = {**_one_widget(), "theme": "dark"}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)
    assert box["dashboard"]["filters"]["theme"] == {"mode": "dark"}
    assert box["charts"][0]["config"]["theme"] == "dark"


def test_create_screen_theme_object_still_supported(box):
    args = {**_one_widget(), "theme": {"mode": "light", "background": "#fff"}}
    assert not _is_err(asyncio.run(screen_tools.create_data_screen(args)))
    assert box["dashboard"]["filters"]["theme"]["background"] == "#fff"


def test_create_screen_bad_theme_string_fails_loud(box):
    """未知主题字符串不得静默丢弃，要告诉模型只能 'light'/'dark' 或传对象。"""
    res = asyncio.run(screen_tools.create_data_screen({**_one_widget(), "theme": "neon"}))
    assert _is_err(res)
    text = _text_of(res)
    assert "light" in text and "dark" in text
    assert box["charts"] == []


@pytest.mark.parametrize("raw,expected", [
    ("big_number", "big_number_trend"),
    ("KPI", "big_number_trend"),
    ("big number card", "big_number_trend"),
    ("Bar", "bar"),
    ("column_chart", "bar"),
    ("time-series-line", "timeseries_line"),
    ("bar", "bar"),          # 真实类型原样通过
])
def test_chart_type_alias_normalized(box, raw, expected):
    widget = {"title": "图", "chart_type": raw,
              "query": {"object": "订单", "metrics": ["营收"]}}
    res = asyncio.run(screen_tools.create_data_screen({"name": "S", "widgets": [widget]}))
    assert not _is_err(res), _text_of(res)
    assert box["charts"][0]["chart_type"] == expected


def test_unknown_chart_type_still_rejected_with_options(box):
    """别名归一不得变成“什么都收”：未知值仍拒，并把可选类型告诉模型。"""
    widget = {"title": "图", "chart_type": "bogut",
              "query": {"object": "订单", "metrics": ["营收"]}}
    res = asyncio.run(screen_tools.create_data_screen({"name": "S", "widgets": [widget]}))
    assert _is_err(res)
    text = _text_of(res)
    assert "bogut" in text and "big_number_trend" in text   # 错因 + 可选清单
    assert box["charts"] == []


def test_update_chart_type_alias_normalized(box):
    res = asyncio.run(screen_tools.update_data_screen_chart(
        {"dashboard_id": 9001, "chart_id": 5, "chart_type": "big_number"}))
    assert not _is_err(res), _text_of(res)
    assert box["update"]["chart_type"] == "big_number_trend"


# ── 一屏多站 + 版面 + 预览入口（反馈 2026-11-17） ───────────────────────
# 现象：用户要「一屏看 5 个站」，却拿到 5 块独立看板，且自动布局把 KPI 拉满整屏、
# 卡片大小不一；会话里也没有任何可点的预览入口（只把 /screen/<id> 当纯文本念）。

SITES = ("中国站点", "新加坡站点")


def _multi_site_widgets():
    return [
        {"title": "中国·KPI", "chart_type": "big_number",
         "query": {"object": "订单", "metrics": ["营收"]}, "site": "中国站点"},
        {"title": "中国·趋势", "chart_type": "timeseries_line",
         "query": {"object": "订单", "metrics": ["营收"]}, "site": "中国站点"},
        {"title": "新加坡·KPI", "chart_type": "big_number",
         "query": {"object": "订单", "metrics": ["营收"]}, "site": "新加坡站点"},
        {"title": "新加坡·趋势", "chart_type": "timeseries_line",
         "query": {"object": "订单", "metrics": ["营收"]}, "site": "新加坡站点"},
    ]


def test_auto_layout_keeps_one_site_per_column():
    """多站一屏：一列一个站，同站同 x / 同宽，站点顺序 = 输入顺序。"""
    widgets = _multi_site_widgets()
    pos = {w["title"]: p for w, p in screen_tools._auto_layout(widgets)}
    assert pos["中国·KPI"]["x"] == 0 and pos["中国·趋势"]["x"] == 0
    assert pos["新加坡·KPI"]["x"] == pos["新加坡·趋势"]["x"] == screen_tools._CANVAS_W // 2
    # 列宽一致、列内 KPI 在上趋势在下、不重叠
    assert pos["中国·KPI"]["w"] == pos["新加坡·趋势"]["w"] == screen_tools._CANVAS_W // 2
    assert pos["中国·KPI"]["y"] < pos["中国·趋势"]["y"]
    assert pos["中国·KPI"]["y"] + pos["中国·KPI"]["h"] <= pos["中国·趋势"]["y"]


def test_auto_layout_caps_single_kpi_width():
    """单站单 KPI 不得拉满整屏（迷你趋势被拉平、数字悬空）。"""
    widgets = [
        {"title": "KPI", "chart_type": "big_number_trend"},
        {"title": "趋势", "chart_type": "timeseries_line"},
        {"title": "占比", "chart_type": "pie"},
    ]
    pos = {w["title"]: p for w, p in screen_tools._auto_layout(widgets)}
    assert pos["KPI"]["w"] <= screen_tools._KPI_MAX_W
    # 内容区主左辅右：趋势比占比宽
    assert pos["趋势"]["w"] > pos["占比"]["w"]
    assert pos["趋势"]["x"] + pos["趋势"]["w"] <= pos["占比"]["x"]


def test_auto_layout_centers_isolated_other_widget():
    """异构卡（状态构成/表格）只在部分站点有时不按列留空：居中且宽度受限。

    反馈现象：五站合一的底行只有中国有「状态构成」，旧矩阵把其余 4 列留空 → 右侧一大片错位空白。
    """
    widgets = [
        {"title": "中国·KPI", "chart_type": "big_number", "site": "中国站点"},
        {"title": "新加坡·KPI", "chart_type": "big_number", "site": "新加坡站点"},
        {"title": "中国·构成", "chart_type": "pie", "site": "中国站点"},
    ]
    pos = {w["title"]: p for w, p in screen_tools._auto_layout(widgets)}
    pie = pos["中国·构成"]
    assert pie["x"] + pie["w"] + pie["x"] == screen_tools._CANVAS_W   # 左右留白对称
    assert pie["w"] <= screen_tools._CANVAS_W * 2 // 3                # 不拉满整屏
    assert pie["y"] > pos["中国·KPI"]["y"]                            # 仍在 KPI 带之下


def test_create_screen_multi_site_stays_on_one_dashboard(box):
    """多站默认**同一块板**（不是每站一块），且返回会话内预览入口。"""
    box["site_map"] = {SITES[0]: DS_IDS[0], SITES[1]: DS_IDS[1]}
    args = {"name": "今年案例数量总览 · 2 站", "widgets": _multi_site_widgets()}
    res = asyncio.run(screen_tools.create_data_screen(args))
    assert not _is_err(res), _text_of(res)

    # 4 张图都在同一块板上
    assert len(box["charts"]) == 4
    payload = json.loads(_text_of(res))
    assert payload["charts"] == 4 and payload["dashboard_id"] == 9001

    # 站点列：中国 4 图 x=0，新加坡图 x=960
    pos = {c["name"]: c["position"] for c in box["charts"]}
    assert pos["中国·KPI"]["x"] == 0 and pos["中国·趋势"]["x"] == 0
    assert pos["新加坡·KPI"]["x"] == screen_tools._CANVAS_W // 2

    # 会话内可点入口：不再只给一段纯文本路径
    act = payload["actions"][0]
    assert act["type"] == "open_screen"
    assert act["dashboard_id"] == 9001 and act["url"] == "/screen/9001"
    assert "预览大屏" in act["label"]


def test_create_screen_top_level_site_fills_widget_gap(box):
    """没在 widget 写 site 时用顶层 site 参与布局分组（否则跨站屏会被当单站排成一块）。"""
    box["site_map"] = {SITES[0]: DS_IDS[0]}
    widgets = [{"title": "趋势", "chart_type": "timeseries_line",
                "query": {"object": "订单", "metrics": ["营收"]}}]
    res = asyncio.run(screen_tools.create_data_screen(
        {"name": "S", "site": "中国站点", "widgets": widgets}))
    assert not _is_err(res), _text_of(res)
    assert screen_tools._auto_layout([
        {**widgets[0], "site": "中国站点"}])[0][1]["w"] == screen_tools._CANVAS_W
