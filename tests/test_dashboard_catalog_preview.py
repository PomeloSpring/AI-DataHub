"""看板目录预览载荷契约：只回「每图几何 + 网格布局」摘要，不再由后端算区块几何。

背景（同一数据两套几何的缺陷）：系统配置「看板管理」列表用前端唯一渲染器
DashboardThumbnail（page 模式走 resolvePageLayout(config.pageLayout)），而工作空间
「数据看板」列表消费后端 card_catalog_fields 现算的 preview.blocks（只看 position 像素）。
page 布局的看板因此在浏览侧退化成大屏像素图（内容只占顶部一小块），两个列表对不上。
"""
import importlib

import backend.common.db as common_db

dash_svc = importlib.import_module("backend.modules.viz.services.dashboard_service")


def _row(did, filters="{}"):
    return {"id": did, "name": f"看板 {did}", "status": "enabled", "is_default": 0,
            "sort_order": 0, "owner_id": 1, "description": "", "updated_at": None, "filters": filters}


_CHARTS = [
    {"id": 1, "dashboard_id": 101, "chart_type": "bar",
     "position": '{"x":0,"y":0,"w":600,"h":300}',
     # config 里混进口径字段，预览必须只取 pageLayout/hidden
     "config": '{"pageLayout":{"version":3,"desktop":{"x":0,"y":0,"w":24,"h":70}},'
               '"sql_template":"SELECT secret FROM t","xCol":"d"}'},
    {"id": 2, "dashboard_id": 101, "chart_type": "widget_search", "position": None,
     "config": '{"hidden":true,"label":"查询"}'},
]


def _run(monkeypatch, rows, charts=_CHARTS):
    seen = {}

    def fake_exec(sql, params=None, fetchone=False):
        assert "FROM adh_charts" in sql
        seen["sql"] = sql
        return charts

    monkeypatch.setattr(common_db, "execute_query", fake_exec)
    return dash_svc.card_catalog_fields([dict(r) for r in rows]), seen


def test_preview_carries_layout_summary_and_no_backend_blocks(monkeypatch):
    rows = [_row(101, '{"design":{"page":{"version":3,"gap":{"desktop":8}},"canvas":{"width":1440,"height":900}}}')]
    (out, _) = _run(monkeypatch, rows)
    preview = out[0]["preview"]
    # 旧口径已废除：后端不再产出区块矩形，几何由前端唯一布局源换算
    assert "blocks" not in preview
    assert preview["page"] == {"version": 3, "gap": {"desktop": 8}}
    assert (preview["width"], preview["height"]) == (1440.0, 900.0)
    charts = preview["charts"]
    assert [c["chart_type"] for c in charts] == ["bar", "widget_search"]
    assert charts[0]["pageLayout"]["desktop"] == {"x": 0, "y": 0, "w": 24, "h": 70}
    assert charts[0]["position"] == {"x": 0.0, "y": 0.0, "w": 600.0, "h": 300.0}
    # 脏 position（NULL）回落最小合法矩形，不产生 0 尺寸
    assert charts[1]["position"]["w"] > 0 and charts[1]["hidden"] is True
    # chart_count 只数数据图表，控件不计
    assert out[0]["chart_count"] == 1


def test_preview_does_not_leak_query_config(monkeypatch):
    rows = [_row(101)]
    (out, _) = _run(monkeypatch, rows)
    blob = str(out[0]["preview"])
    for leaked in ("sql_template", "SELECT secret", "xCol", '"label"'):
        assert leaked not in blob, f"预览载荷泄露口径字段: {leaked}"
    # 原始 filters 用完即剔除，不随目录下发
    assert "filters" not in out[0]


def test_screen_mode_dashboard_still_expands_canvas_by_positions(monkeypatch):
    """无 pageLayout 的大屏看板：画布按 position 外扩，保证预览不被裁掉。"""
    charts = [{"id": 3, "dashboard_id": 102, "chart_type": "bar",
               "position": '{"x":1500,"y":1200,"w":800,"h":400}', "config": "{}"}]
    rows = [_row(102)]
    (out, _) = _run(monkeypatch, rows, charts)
    preview = out[0]["preview"]
    assert preview["width"] == 2300.0 and preview["height"] == 1600.0
    assert preview["charts"][0]["pageLayout"] is None


def test_page_layout_rows_do_not_inflate_screen_canvas(monkeypatch):
    """带 pageLayout 的图表不参与像素外扩：尺寸由前端按网格算，避免两套口径。"""
    rows = [_row(101)]
    (out, _) = _run(monkeypatch, rows)
    assert out[0]["preview"]["width"] == 1920.0
    assert out[0]["preview"]["height"] == 1080.0
