"""字模库管理页服务端**真分页**回归（禁止"拉全量再前端切片"的假分页）。

口径（.qoder/rules/list-pagination-server-side.md）：
* 传了 page_size 才走分页并返回 {items,total,page,page_size}；不传仍回全量数组（选用面需要全量在手）；
* 行总数由服务端 COUNT 得到（不是当前页长度），筛选/搜索/下架禁用两档全部下推 SQL；
* 偏移量由页号算出（调用方不递 offset），每页行数服务端夹到上限，排序含唯一 id 兜底（不重行/漏行）。
"""
import importlib

from backend.modules.viz.services.vis_library_service import vis_library_service


def _row(**over):
    row = {"id": 1, "code": "c1", "name": "字模", "category": "chart_style",
           "style_config": "{}", "is_active": 1, "is_enabled": 1, "is_builtin": 0,
           "sort_order": 0, "description": ""}
    row.update(over)
    return row


def test_list_components_page_pushes_filters_and_uses_limit_offset(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    calls = []

    def fake_execute_query(sql, params=None, fetchone=False, **k):
        calls.append((sql, list(params or []), fetchone))
        if "COUNT(*)" in sql:
            return {"cnt": 3}
        return [_row(id=i) for i in (1, 2)]

    monkeypatch.setattr(vls, "execute_query", fake_execute_query)

    out = vls.vis_library_service.list_components_page(
        category="theme_pack", include_inactive=True, include_disabled=True,
        q="霓虹", page=2, page_size=2,
    )
    # 契约：{items,total,page,page_size}；total 来自 COUNT，不是当前页长度
    assert set(out) == {"items", "total", "page", "page_size"}
    assert out["total"] == 3 and out["page"] == 2 and out["page_size"] == 2
    assert len(out["items"]) == 2
    # style_config JSON 文本列已归一为对象（前端直接消费）
    assert isinstance(out["items"][0]["style_config"], dict)

    count_sql, count_params, _ = calls[0]
    # 筛选/搜索全部下推：category 精确；include_inactive+include_disabled=True → 不写 is_active/is_enabled 过滤
    assert "category = %s" in count_sql
    assert "is_enabled = 1" not in count_sql and "is_active = 1" not in count_sql
    assert "name LIKE %s" in count_sql and "code LIKE %s" in count_sql and "description LIKE %s" in count_sql
    assert count_params == ["theme_pack", "%霓虹%", "%霓虹%", "%霓虹%"]

    page_sql, page_params, _ = calls[1]
    # 真分页：LIMIT/OFFSET 由服务端按页号算，偏移 = (page-1)*size
    assert "LIMIT %s OFFSET %s" in page_sql
    assert page_params[-2:] == [2, 2]
    # 排序确定性（含唯一 id 兜底）→ 相邻页不会重行/漏行
    assert "ORDER BY sort_order ASC, is_builtin DESC, id ASC" in page_sql


def test_list_components_page_clamps_size_and_defaults_first_page(monkeypatch):
    vls = importlib.import_module("backend.modules.viz.services.vis_library_service")
    seen = []

    def fake(sql, params=None, fetchone=False, **k):
        seen.append((sql, list(params or [])))
        return {"cnt": 0} if "COUNT(*)" in sql else []

    monkeypatch.setattr(vls, "execute_query", fake)
    # 每页行数服务端夹到上限（越界不照单全收），正常值原样保留
    assert vls.vis_library_service.list_components_page(page_size=5000)["page_size"] == 200
    assert vls.vis_library_service.list_components_page(page_size=30)["page_size"] == 30
    # 页号越界（≤0）收敛到第 1 页 → offset 0；默认不外发下架+禁用（fail-closed）
    out = vls.vis_library_service.list_components_page(page=0, page_size=20)
    assert out["page"] == 1
    assert seen[-1][1][-1] == 0
    assert "is_active = 1" in seen[0][0] and "is_enabled = 1" in seen[0][0]


def test_endpoint_branches_on_page_size(monkeypatch):
    """不传 page_size 回全量数组；传 page_size 回分页信封（两档显式，非静默）。"""
    from backend.modules.viz.api import vis_library as api
    monkeypatch.setattr(api._service, "list_components", lambda *a, **k: [_row()])
    monkeypatch.setattr(api._service, "list_components_page",
                        lambda *a, **k: {"items": [_row()], "total": 1, "page": 1, "page_size": 20})
    full = api.list_components(category="", include_inactive=True, include_disabled=True,
                               q="", page=1, page_size=None, user={"user_id": 1})
    assert isinstance(full, list)
    page = api.list_components(category="", include_inactive=True, include_disabled=True,
                               q="", page=1, page_size=20, user={"user_id": 1})
    assert isinstance(page, dict) and set(page) == {"items", "total", "page", "page_size"}
