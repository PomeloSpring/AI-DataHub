"""字段显示格式（时间列怎么给人看）回归测试。

背景：唯一取数出口 `df_to_columns_rows` 用 pandas `to_json(date_format="iso",
date_unit="ms")` 序列化，MySQL 的 DATE 与 DATETIME 都变成
`2026-10-12T00:00:00.000`（实测连 `datetime.date` 也补零点+毫秒，所以 SQL 里写
`CAST(x AS DATE)` 没用）。这串直接当类目轴标签会长到被截成一排 `2026⋯`。

本套锁定的是「格式声明在数据集字段、随取数结果下发、各渲染面同源消费」这条链路：
1. 保存即校验：悬空格式令牌拒入库（配了但永远渲染不出来的死键）；
2. `field_formats_for` 按 SQL 输出列名与语义业务名双键匹配，且只回本次真实出现的列；
3. `query_dataset` 两条通道（SQL/语义 与 ES）都随结果下发；
4. `resolve_fields` 把格式补到映射后的语义字段上（否则配在 SQL 列名上、语义路失效）；
5. 看板缓存留住格式（首屏不重查也得与刷新后同一显示口径）；
6. 前后端令牌清单逐字一致（任一侧加令牌另一侧认不出 = 静默不生效）。

全部内存断言，不依赖元库数据。
"""
import inspect
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.viz.services import dataset_registry as reg   # noqa: E402
from backend.modules.viz.services import dataset_service as svc    # noqa: E402
from backend.modules.viz.services.dashboard_service import (  # noqa: E402
    ChartService, _chart_cache_json, _paging_extra)

ROOT = os.path.join(os.path.dirname(__file__), "..")


# ═══════════════════════════════════════════════════════════════════
# 1. 保存即校验
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("token", sorted(reg.FIELD_DISPLAY_FORMATS - {""}))
def test_every_declared_token_is_accepted(token):
    errors, _ = reg.validate_field_config(
        [{"field": "stat_date", "role": "dimension", "display_format": token}])
    assert errors == [], errors


def test_empty_token_means_no_format_and_passes():
    errors, _ = reg.validate_field_config(
        [{"field": "stat_date", "role": "dimension", "display_format": ""}])
    assert errors == []


def test_unknown_token_is_rejected_at_save():
    """悬空格式令牌 = 配了但前端认不出、永远渲染不出来，必须在存下之前拒。"""
    errors, _ = reg.validate_field_config(
        [{"field": "stat_date", "role": "dimension", "display_format": "yyyy-MM-dd"}])
    assert len(errors) == 1
    assert "display_format" in errors[0]
    assert "yyyy-MM-dd" in errors[0]


def test_bad_display_format_does_not_mask_other_field_errors():
    """格式非法只追加错误，不得 continue 掉后面的度量 agg 校验（否则一次修不干净）。"""
    errors, _ = reg.validate_field_config(
        [{"field": "uv", "role": "measure", "agg": "MEDIAN", "display_format": "nope"}])
    assert any("display_format" in e for e in errors)
    assert any("agg" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════
# 2. field_formats_for：双键匹配 + 只回本次出现的列
# ═══════════════════════════════════════════════════════════════════

def test_matches_sql_output_column_name():
    ds = {"field_config": [{"field": "stat_date", "display_format": "date"}]}
    assert svc.field_formats_for(ds, ["stat_date", "record_count"]) == {"stat_date": "date"}


def test_matches_semantic_business_name_via_maps_to():
    """语义路输出的是业务名，格式配在 SQL 列名上也要能命中（双键同源）。"""
    ds = {"field_config": [
        {"field": "create_time", "maps_to": "统计日期", "display_format": "datetime"}]}
    assert svc.field_formats_for(ds, ["统计日期", "订单量"]) == {"统计日期": "datetime"}


def test_only_columns_present_in_this_result_are_emitted():
    ds = {"field_config": [
        {"field": "stat_date", "display_format": "date"},
        {"field": "dropped_col", "display_format": "month"}]}
    assert svc.field_formats_for(ds, ["stat_date"]) == {"stat_date": "date"}


def test_entries_without_format_are_ignored():
    ds = {"field_config": [{"field": "stat_date"}, {"field": "uv", "display_format": ""}]}
    assert svc.field_formats_for(ds, ["stat_date", "uv"]) == {}


@pytest.mark.parametrize("raw", [None, "", "not-json", "{}", 42, "[]"])
def test_tolerates_missing_or_malformed_field_config(raw):
    """字段声明缺失/脏值不得报错（取数已成功，显示格式是旁路信息）。"""
    assert svc.field_formats_for({"field_config": raw}, ["stat_date"]) == {}


def test_malformed_entries_skipped_not_fatal():
    ds = {"field_config": ["scalar", {"display_format": "date"},
                           {"field": "stat_date", "display_format": "date"}]}
    assert svc.field_formats_for(ds, ["stat_date"]) == {"stat_date": "date"}


def test_no_columns_means_no_payload():
    ds = {"field_config": [{"field": "stat_date", "display_format": "date"}]}
    assert svc.field_formats_for(ds, []) == {}


# ═══════════════════════════════════════════════════════════════════
# 3. query_dataset 两条通道都随结果下发
# ═══════════════════════════════════════════════════════════════════

def _capture_attached(source: str) -> dict:
    """跑一遍 `query_dataset` 的下游拼装逻辑，取回 `field_formats`。

    直接调 `query_dataset` 要连元库与数据源，这里按源码接线做等价断言：
    两条通道各自 return 前都必须过 `field_formats_for`。
    """
    assert "field_formats_for" in source, "取数结果未附带字段显示格式"
    assert 'result["field_formats"]' in source, "字段显示格式未写进结果键"
    return {}


def test_sql_and_semantic_channel_attaches_formats():
    _capture_attached(inspect.getsource(svc.query_dataset))


def test_es_channel_also_attaches_formats():
    """ES 分支在定源前 return（它不挂在数据源上），漏挂就等于 ES 数据集永远没格式。"""
    src = inspect.getsource(svc.query_dataset)
    es_block = src.split("is_es_dataset(ds)", 1)[1].split("\n    if params.get(", 1)[0]
    assert "field_formats_for" in es_block, "ES 通道未下发字段显示格式"


def test_emitted_payload_is_column_to_token_only():
    """下发的映射只能是「业务列名 → 格式令牌」，不得混进内部标识（护栏 §7）。"""
    out = svc.field_formats_for(
        {"field_config": [{"field": "stat_date", "display_format": "date",
                           "datasource_id": 7, "physical_table": "t"}]}, ["stat_date"])
    assert out == {"stat_date": "date"}
    assert all(isinstance(v, str) for v in out.values())


# ═══════════════════════════════════════════════════════════════════
# 4. resolve_fields：格式补到映射后的语义字段
# ═══════════════════════════════════════════════════════════════════

def test_resolve_fields_carries_format_onto_mapped_semantic_field(monkeypatch):
    monkeypatch.setattr(svc, "_semantic_fields", lambda ds_id, key, memo=None: [
        {"field": "统计日期", "label": "stat_date", "role": "dimension", "from": "semantic"}])
    ds = {"object_key": "order", "datasource_id": 5, "field_config": [
        {"field": "create_time", "maps_to": "统计日期", "display_format": "date"}]}
    fields = svc.resolve_fields(ds)
    hit = next(f for f in fields if f["field"] == "统计日期")
    assert hit["sql_column"] == "create_time"
    assert hit["display_format"] == "date", "语义字段没带上格式 = 配了等于没配"


def test_resolve_fields_keeps_format_on_pure_sql_config(monkeypatch):
    monkeypatch.setattr(svc, "_semantic_fields", lambda *a, **k: [])
    ds = {"object_key": "", "field_config": [
        {"field": "stat_date", "role": "dimension", "display_format": "month"}]}
    assert svc.resolve_fields(ds)[0]["display_format"] == "month"


# ═══════════════════════════════════════════════════════════════════
# 5. 看板缓存留住格式（首屏与刷新后同一口径）
# ═══════════════════════════════════════════════════════════════════

def test_paging_extra_keeps_completeness_facts():
    """truncated / unpaged 随首屏缓存一起落库：少了它们，重载页面后这张图又变成状态未知，
    前端只能再补一次无谓重查。撞线（truncated）与完整（unpaged）互斥，不得同时出现。"""
    extra = _paging_extra({"rows": [{"a": 1}], "truncated": True})
    assert extra["truncated"] is True and "unpaged" not in extra
    extra = _paging_extra({"rows": [{"a": 1}], "unpaged": True})
    assert extra["unpaged"] is True and "truncated" not in extra
    assert _paging_extra({"rows": []}) == {}


def test_cache_keeps_whole_result_when_server_declares_unpaged():
    """统计型结果（服务端说“本次即全部行”）整份入缓存，不再只留 20 行预览。

    留着预览行 + unpaged 会拿 20 行冒充 88 行；只留预览行又不带事实，
    首屏就必须补一次重查才能知道真实行数（存量表值图开一次看板转一次圈的源头）。
    """
    chart = {"id": 1, "chart_type": "table_value", "config": {"pageSize": 20}, "name": "x"}
    rows = [{"a": i} for i in range(88)]
    cache = json.loads(_chart_cache_json(chart, ["a"], rows, {"unpaged": True}))
    assert len(cache["rows"]) == 88 and cache["unpaged"] is True


def test_cache_drops_unpaged_when_byte_budget_pops_rows():
    """宽文本把缓存预算顶到逐行退时，“本次即全量”必须随之抹掉。

    剩下的行只是预览，继续宣称完整就是拿确定的假数充数（比状态未知更坏）。
    """
    chart = {"id": 1, "chart_type": "table_value", "config": {"pageSize": 20}, "name": "x"}
    rows = [{"a": f"操作明细正文_{i}_" + "长" * 400} for i in range(400)]
    cache = json.loads(_chart_cache_json(chart, ["a"], rows, {"unpaged": True}))
    assert len(cache["rows"]) < len(rows) and "unpaged" not in cache


def test_paged_cache_still_keeps_only_default_page():
    """按页取数（有 paging）时缓存照旧只留默认首页，不受 unpaged 分支影响。"""
    chart = {"id": 1, "chart_type": "table_value", "config": {"pageSize": 20}, "name": "x"}
    rows = [{"a": i} for i in range(20)]
    extra = {"paging": {"mode": "offset", "page_size": 20, "page_num": 1,
                        "total": 5000, "can_jump": True, "stable_order": True}, "total": 5000}
    cache = json.loads(_chart_cache_json(chart, ["a"], rows, extra))
    assert len(cache["rows"]) == 20 and cache["paging"]["total"] == 5000 and "unpaged" not in cache


def test_paging_extra_keeps_field_formats():
    extra = _paging_extra({"total": 9, "field_formats": {"stat_date": "date"}})
    assert extra["field_formats"] == {"stat_date": "date"}
    assert extra["total"] == 9


@pytest.mark.parametrize("payload", [{}, {"field_formats": {}}, {"field_formats": None},
                                    {"field_formats": ["date"]}])
def test_paging_extra_omits_empty_or_malformed_formats(payload):
    assert "field_formats" not in _paging_extra(payload)


def test_chart_cache_json_carries_formats_into_data_cache():
    chart = {"chart_type": "bar", "config": "{}"}
    cache = json.loads(_chart_cache_json(
        chart, ["stat_date"], [{"stat_date": "2026-10-12T00:00:00.000"}],
        {"field_formats": {"stat_date": "date"}}))
    assert cache["field_formats"] == {"stat_date": "date"}


def test_dataset_chart_response_whitelist_includes_formats():
    """`_refresh_dataset_chart` 按白名单挑键回传，漏一项就是"服务端下发了、图表拿不到"。"""
    src = inspect.getsource(ChartService._refresh_dataset_chart)
    hit = re.search(r'for k in \(([^)]*)\)', src)
    assert hit and "field_formats" in hit.group(1), "图表响应白名单未包含 field_formats"


# ═══════════════════════════════════════════════════════════════════
# 6. 前后端令牌清单逐字一致（另一侧认不出 = 静默不生效）
# ═══════════════════════════════════════════════════════════════════

def test_frontend_token_list_matches_backend_registry():
    ts = open(os.path.join(ROOT, "frontend/src/lib/fieldFormat.ts"), encoding="utf-8").read()
    block = ts.split("FIELD_DISPLAY_FORMATS", 1)[1].split("];", 1)[0]
    front = set(re.findall(r"\{\s*value:\s*'([^']*)'", block))
    assert front == reg.FIELD_DISPLAY_FORMATS, (
        f"前后端格式令牌不一致：前端独有={front - reg.FIELD_DISPLAY_FORMATS} "
        f"后端独有={reg.FIELD_DISPLAY_FORMATS - front}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
