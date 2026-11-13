"""交付形态渲染机（render 工具组）回归：模型自选通道 + 服务端能力硬门 + 选道记账。

锁定口径（方案 §2/§4/§5）：
- **裁决权在模型**：服务端不判"该不该用 HTML"，只拒"HTML 做不到 / 会泄露"；
- **体检不过 = 可重试错误**（原因 + 出路回给模型），不是静默清洗、也不是静默降级；
- **G1 单轨**：一轮一个主交付物；但**校验失败不占用**本轮（模型仍可改选另一形态）；
- **G4 黑盒**：回执不含属主 id / 数据源标识 / 正文回显；
- **G5 记账**：每次选道（含失败）写 delivery_channel span。

离线运行：文档表读写与观测均用假实现接管，不连库、不发 span。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools import render_tools as rt
from backend.modules.mind.execution.sdk_tools.context import (
    ExecutionContextVar, set_execution_context)
from backend.modules.viz.services import report_document_service as docs


GOOD_HTML = ("<html><head><style>:root{--primary:#4f46e5;--bg:#ffffff;--card:#fff}</style></head>"
             "<body><h1>订单分析报告</h1><table><tr><td>1</td></tr></table></body></html>")


def _payload(result):
    return json.loads(result["content"][0]["text"])


class FakeStore:
    """内存文档表：只实现本用例集用到的四种语句（读写按子句路由）。"""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.writes: list[str] = []

    def execute_write(self, sql, params=()):
        self.writes.append(" ".join(sql.split()))
        if sql.strip().upper().startswith("UPDATE"):
            row = self.rows[str(params[-2])]
            row.update({"title": params[0], "theme": params[1], "template_key": params[2],
                        "body": params[3], "digest": params[4], "size_chars": params[5],
                        "warnings": params[6], "row_counts": params[7]})
            return 1
        row = {
            "id": params[0], "conversation_id": params[1], "message_uuid": params[2],
            "user_id": params[3], "workspace_id": params[4], "kind": params[5],
            "body_kind": params[6], "title": params[7], "theme": params[8],
            "template_key": params[9], "body": params[10], "digest": params[11],
            "size_chars": params[12], "warnings": params[13], "row_counts": params[14],
        }
        self.rows.setdefault(str(params[0]), row)
        return 1

    def execute_query(self, sql, params=(), fetchone=False):
        text = " ".join(sql.split())
        if text.startswith("SELECT id FROM adh_render_documents"):
            row = self.rows.get(str(params[0]))
            if fetchone:
                return {"id": row["id"]} if row else None
            return [{"id": row["id"]}] if row else []
        if "FROM adh_render_documents WHERE id=" in text:
            row = self.rows.get(str(params[0]))
            if not row:
                return None if fetchone else []
            out = {k: v for k, v in row.items() if k != "body" or "body" in text}
            return out if fetchone else [out]
        if "FROM adh_render_documents" in text:
            rows = [dict(r) for r in self.rows.values()
                    if str(r.get("conversation_id")) == str(params[0])
                    and str(r.get("user_id")) == str(params[1])]
            return rows
        raise AssertionError(f"假实现未覆盖的语句: {text}")


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(docs, "execute_query", fake.execute_query)
    monkeypatch.setattr(docs, "execute_write", fake.execute_write)
    return fake


@pytest.fixture
def spans(monkeypatch):
    """G5 记账捕获（观测层替身）：断言选道事实可统计，而不是只写在注释里。"""
    from backend import observability
    captured = []
    monkeypatch.setattr(observability, "record_span",
                        lambda **kw: captured.append(kw), raising=False)
    monkeypatch.setattr(observability, "message_uuid", lambda: "msg-uuid-1", raising=False)
    return captured


@pytest.fixture
def ctx():
    token = set_execution_context(ExecutionContext(
        user_id=7, workspace_id=3, user_role="analyst",
        extra={"conversation_id": 42, "report_theme": "datafoundry"}))
    yield ExecutionContextVar.get()
    ExecutionContextVar.reset(token)


def _render_html(args):
    return asyncio.run(rt.render_html(args))


def _render_chart(args):
    return asyncio.run(rt.render_chart(args))


_SEM_ROWS = [{"月份": "2026-01", "订单量": 12}, {"月份": "2026-02", "订单量": 18}]


@pytest.fixture
def semantic(monkeypatch):
    """取数口替身：记录调用 + 可控载荷（不连库、不跑语义层）。

    桩的是 `_execute_single`（run_semantic_query 单发/批量共用的那条执行函数）：
    渲染机拿类型保真的数据，不能拿 `_text` 信封后的 JSON（DECIMAL 会被写成字符串）。
    同时桩掉 `check_tool`：本用例集的 ExecutionContext 没有 secure_runtime，
    而“没授权取数工具就不能拿渲染机当代取数入口”另有专项用例锁。
    """
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.modules.mind.execution import tool_policy

    calls = []
    state = {"payload": {"columns": ["月份", "订单量"], "rows": list(_SEM_ROWS),
                         "row_count": 2},
             "is_error": False}

    async def fake_single(intent, ctx):
        calls.append({"intent_json": intent})
        return state["payload"], state["is_error"]

    monkeypatch.setattr(sq, "_execute_single", fake_single)
    monkeypatch.setattr(tool_policy, "check_tool", lambda ctx, name: None)
    return calls, state


# ── 正常交付：落对象 + 只回引用（不回正文）────────────────────────────

def test_render_html_stores_document_and_returns_ref(ctx, store, spans):
    out = _payload(_render_html({"title": "订单分析报告", "html": GOOD_HTML}))
    assert out["doc_id"]
    assert out["body_kind"] == "html"
    assert "body" not in out and GOOD_HTML not in json.dumps(out)  # 正文不回显（省 token + 防重复粘贴）
    row = store.rows[out["doc_id"]]
    assert row["body"] == GOOD_HTML and row["user_id"] == 7 and row["conversation_id"] == 42
    assert row["body_kind"] == "html" and row["kind"] == "doc"
    # 未点名主题时继承会话生效主题（不是"没主题"，也不是模型自造）
    assert row["theme"] == "datafoundry"
    assert out["digest"] == row["digest"]
    assert "```report" in out["next"] and out["doc_id"] in out["next"]


def test_render_html_ack_carries_no_internal_identifiers(ctx, store, spans):
    out = _render_html({"title": "T", "html": GOOD_HTML})
    text = json.dumps(out, ensure_ascii=False)
    # G4：user_id/datasource_id/正文都不进回执（护栏 §7）
    for banned in ("user_id", "datasource_id", "workspace_id", GOOD_HTML[:40]):
        assert banned not in text


# ── 体检五项：命中即拒 + 给可操作出路（不静默清洗）────────────────────

@pytest.mark.parametrize("html, expect_keyword", [
    ('<html><script src="https://cdn.jsdelivr.net/npm/echarts.min.js"></script>'
     ':root{--primary:#1}', "外部资源"),
    ('<html><svg>' + '<circle cx="1" cy="1" r="2"/>' * 8 + '</svg>:root{--primary:#1}', "伪图"),
    ("<html><b>没有令牌块</b></html>", "主题令牌"),
    ("<html>:root{--primary:#1}" + ("#abcdef " * 200) + "</html>", "硬编码"),
    ("<html>:root{--primary:#1} datasource_id: 5</html>", "系统信息"),
])
def test_validation_blockers_are_reported_not_silently_fixed(ctx, store, spans, html, expect_keyword):
    out = _render_html({"title": "T", "html": html})
    assert out.get("isError"), f"{expect_keyword} 应被拒，不能静默放行"
    payload = _payload(out)
    assert expect_keyword in payload["error"]
    assert payload["blockers"], "阻断项必须逐条列出（可诊断），不能只给一句"
    assert payload.get("how_to_fix") or payload.get("hint")
    assert store.rows == {}, "体检不过不得落库（半份/坏文档不能混进交付清单）"


def test_svg_namespace_is_not_treated_as_remote_resource(ctx, store, spans):
    """内联 SVG 的 xmlns 是规范要求的固定 URI，不是外链 —— 误杀会让所有带图的报告都被拒。"""
    html = GOOD_HTML.replace("<table>", '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0"/></svg><table>')
    out = _payload(_render_html({"title": "T", "html": html}))
    assert out.get("doc_id"), out


def test_oversize_document_rejected_with_threshold_in_message(ctx, store, spans, monkeypatch):
    monkeypatch.setattr(docs, "RENDER_DOC_MAX_CHARS", 200)
    out = _payload(_render_html({"title": "T", "html": GOOD_HTML * 30}))
    assert "超过文档上限" in out["error"] and "200" in out["error"]
    assert store.rows == {}


def test_rich_chart_facts_produce_note_not_blocker(ctx, store, spans):
    """G2 推荐档：本轮取到过地图数据但正文没伪图 = 警告放行并标注去向，不一刀切拒收。"""
    rt.record_rich_chart_facts(ctx, ["china_map", "bar"])
    out = _payload(_render_html({"title": "T", "html": GOOD_HTML}))
    assert out.get("doc_id"), "次要维度级不应阻断"
    assert any("地图" in n or "图型" in n for n in out["notes"]), out["notes"]
    assert store.rows[out["doc_id"]]["warnings"], "警告要随文档落库，前端才看得到徽标"


# ── G1 单轨 ────────────────────────────────────────────────────────────

def test_second_primary_channel_is_rejected_in_same_turn(ctx, store, spans):
    first = _payload(_render_html({"title": "A", "html": GOOD_HTML}))
    out = _payload(_render_html({"title": "B", "html": GOOD_HTML, "doc_id": first["doc_id"]}))
    assert out.get("doc_id") == first["doc_id"], "带 doc_id 的修订要复用同一份，不产生第二份正文"
    assert len(store.rows) == 1


def test_body_validation_failure_does_not_claim_primary_channel(ctx, store, spans):
    """体检不过不占用主交付物：模型改选另一形态的权利必须保留（否则一次写坏就锁死本轮）。"""
    bad = _render_html({"title": "A", "html": "<html><b>无令牌</b></html>"})
    assert bad.get("isError")
    assert rt._channel_state(ctx).get("primary") == ""


def test_unsaved_primary_channel_switch_is_blocked(ctx, store, spans, monkeypatch):
    """已落地的 html 之后想改 spec：本轮拒并存，提示修改已有文档。"""
    monkeypatch.setattr(rt, "PRIMARY_CHANNELS", ("html", "spec"))
    _render_html({"title": "A", "html": GOOD_HTML})
    blocked = rt._claim_primary(ctx, "spec")
    assert blocked and "主交付物" in blocked


# ── 身份与失败可见性 ────────────────────────────────────────────────────

def test_missing_identity_is_rejected(store, spans):
    token = ExecutionContextVar.set(ExecutionContext(user_id=0, extra={"conversation_id": 42}))
    try:
        out = _render_html({"title": "T", "html": GOOD_HTML})
        assert out.get("isError") and "身份" in _payload(out)["error"]
    finally:
        ExecutionContextVar.reset(token)
    assert store.rows == {}


def test_missing_title_or_body_is_rejected(ctx, store, spans):
    assert _render_html({"html": GOOD_HTML}).get("isError")
    assert _render_html({"title": "T", "html": "   "}).get("isError")
    assert store.rows == {}


# ── G5 记账 ─────────────────────────────────────────────────────────────

def test_channel_choice_span_recorded_for_success_and_failure(ctx, store, spans):
    _render_html({"title": "A", "html": GOOD_HTML})
    _render_html({"title": "B", "html": "<html>无令牌</html>"})
    choices = [s for s in spans if s.get("kind") == "delivery_channel"]
    assert len(choices) == 2
    ok, fail = choices[0], choices[1]
    assert ok["status"] == "success" and json.loads(ok["output_text"])["doc_id"]
    assert fail["status"] == "error" and json.loads(fail["output_text"])["blockers"]
    assert json.loads(fail["output_text"])["attempt"] == 2, "重试次数要记账（滥用与反复失败都靠它看）"
    for s in choices:
        assert GOOD_HTML not in s.get("output_text", ""), "span 里不回显正文"


def test_attempt_counter_is_turn_scoped(ctx, store, spans):
    """ExecutionContext 每回合新建 = 选道状态天然回合级，不依赖观测的 message_uuid 当身份。"""
    _render_html({"title": "A", "html": GOOD_HTML})
    token2 = ExecutionContextVar.set(ExecutionContext(
        user_id=7, extra={"conversation_id": 42}))
    try:
        assert rt._channel_state(ExecutionContextVar.get()).get("primary") == ""
        out = _payload(_render_html({"title": "B", "html": GOOD_HTML}))
        assert out.get("doc_id"), "新一轮要能正常出主交付物"
    finally:
        ExecutionContextVar.reset(token2)


# ── render_chart：取数同源 + 图型/形态回弹（不再静默退柱状图）──────────────

def test_render_chart_fetches_by_intent_and_stores_data_object(ctx, store, spans, semantic):
    calls, _ = semantic
    out = _payload(_render_chart({"title": "月度订单量", "chart_type": "line",
                                "intent": {"object": "订单", "metrics": ["订单量"],
                                            "dimensions": ["月份"]}}))
    assert out["chart_id"]
    assert calls and calls[0]["intent_json"]["object"] == "订单", "intent 要原样交给语义层执行口"
    row = store.rows[out["chart_id"]]
    assert row["kind"] == "chart" and row["body_kind"] == "chart-data"
    body = json.loads(row["body"])
    assert body["chart_type"] == "line" and body["columns"] == ["月份", "订单量"]
    assert body["rows"] == _SEM_ROWS and body["row_count"] == 2
    assert row["row_counts"] and json.loads(row["row_counts"])["row_count"] == 2
    # 数据不回回执（引用形态的意义）：只回列名与行数，不回 rows
    assert "rows" not in out and "2026-01" not in json.dumps(out, ensure_ascii=False)
    assert out["columns"] == ["月份", "订单量"]
    assert "```chart" in out["next"] and "chart_ref" in out["next"]


def test_render_chart_row_count_matches_fetch_even_when_truncated(ctx, store, spans, semantic):
    """方案验收项：row_count 与取数对账相等（不拿 len(rows) 反推）。"""
    _, state = semantic
    state["payload"] = {"columns": ["月份", "订单量"], "rows": list(_SEM_ROWS),
                        "row_count": 137, "truncated": True}
    out = _payload(_render_chart({"title": "T", "chart_type": "line", "intent": {"object": "订单"}}))
    assert out["row_count"] == 137 and out["returned_rows"] == 2
    assert any("137" in n and "全量" in n for n in out["notes"]), out["notes"]


def test_render_chart_normalizes_decimal_before_shape_check(ctx, store, spans, semantic):
    """Decimal 归一为 float 后才能被前端认成数值列（拿归一前形状校验会漏到渲染侧）。"""
    import decimal
    _, state = semantic
    state["payload"] = {"columns": ["月份", "金额"],
                        "rows": [{"月份": "2026-01", "金额": decimal.Decimal("12.50")}],
                        "row_count": 1}
    out = _payload(_render_chart({"title": "T", "chart_type": "bar", "intent": {"object": "订单"}}))
    body = json.loads(store.rows[out["chart_id"]]["body"])
    assert body["rows"][0]["金额"] == 12.5 and isinstance(body["rows"][0]["金额"], float)


def test_render_chart_intent_failure_does_not_store(ctx, store, spans, semantic):
    _, state = semantic
    state["payload"] = {"error": "当前会话未确定数据源", "retry_with": "datasource"}
    state["is_error"] = True
    out = _render_chart({"title": "T", "intent": {"object": "订单"}})
    assert out.get("isError")
    assert "未确定数据源" in _payload(out)["error"]
    assert store.rows == {}, "取数失败不得落一份空图对象（那会把失败伪装成交付）"


def test_render_chart_without_fetch_authorization_is_refused(ctx, store, spans, semantic, monkeypatch):
    """只勾 render 没勾取数时，不能拿渲染机当代取数入口（同一条授权口径）。"""
    from backend.modules.mind.execution import tool_policy
    calls, _ = semantic

    def deny(_ctx, name):
        raise PermissionError("当前 AS-BOT 未授权该工具")

    monkeypatch.setattr(tool_policy, "check_tool", deny)
    out = _payload(_render_chart({"title": "T", "intent": {"object": "订单"}}))
    assert "未授权取数工具" in out["error"]
    assert not calls and store.rows == {}


def test_render_chart_columns_rows_fallback_is_labelled_unreconciled(ctx, store, spans, semantic):
    out = _payload(_render_chart({"title": "T", "chart_type": "bar",
                                 "columns": ["省份", "订单量"],
                                 "rows": [{"省份": "江苏省", "订单量": 251}]}))
    assert out["chart_id"]
    assert any("未经服务端取数对账" in n for n in out["notes"]), out["notes"]


@pytest.mark.parametrize("chart_type, columns, rows, keyword", [
    ("radarr", ["月", "额"], _SEM_ROWS, "未知图型"),
    ("chord", ["月", "额"], _SEM_ROWS, "未知图型"),
    ("table", ["月", "额"], _SEM_ROWS, "不是图型"),
    ("bar", ["月"], [{"月": "2026-01"}], "两列"),
    ("bar", ["省份", "产品"], [{"省份": "江苏", "产品": "A"}], "需要数值列"),
    ("bar", ["省份", "订单量"], [{"省份": "江苏", "订单量": "120"}], "字符串"),
    ("radar", ["产品", "a", "b"], [{"产品": "A", "a": 1, "b": 2}], "多指标"),
    ("china_map", ["产品", "销量"], [{"产品": "A", "销量": 3}], "地理列"),
    ("map_heat", ["城市", "销量"], [{"城市": "北京", "销量": 3}], "经度列"),
    ("timeseries_line", ["产品", "销量"], [{"产品": "A", "销量": 3}], "时间列"),
    ("scatter", ["产品", "销量"], [{"产品": "A", "销量": 3}], "两个数值列"),
    ("sankey", ["来源", "销量"], [{"来源": "a", "销量": 3}], "两个维度列"),
])
def test_chart_shape_problems_are_rejected_not_silently_dropped(
        ctx, store, spans, semantic, chart_type, columns, rows, keyword):
    """方案验收项：未知图型/缺数值列等一律回可重试错误，不退错图也不落库。"""
    out = _render_chart({"title": "T", "chart_type": chart_type,
                         "columns": columns, "rows": rows})
    assert out.get("isError"), chart_type
    payload = _payload(out)
    assert keyword in payload["error"], (chart_type, payload["error"])
    assert payload.get("how_to_fix")
    assert store.rows == {}, "不合格图形式不得当成交付对象落库"


def test_geo_columns_with_real_region_values_pass(ctx, store, spans, semantic):
    """地区列靠取值后缀也能认（列名没写“省份”时不能误拒，模型改不了列名）。"""
    out = _payload(_render_chart({"title": "T", "chart_type": "china_map",
                                  "columns": ["归属地", "浏览量"],
                                  "rows": [{"归属地": "江苏省", "浏览量": 251},
                                            {"归属地": "广东省", "浏览量": 190}]}))
    assert out["chart_id"], out


def test_chart_type_inference_is_visible_in_ack(ctx, store, spans, semantic):
    out = _payload(_render_chart({"title": "T", "intent": {"object": "订单"}}))
    assert out["chart_type_applied"] == "line", "有时间列→时序形线，不是静默交柱状"
    assert any("未指定 chart_type" in n for n in out["notes"]), out["notes"]


def test_legacy_chart_type_alias_resolved_with_note(ctx, store, spans, semantic):
    out = _payload(_render_chart({"title": "T", "chart_type": "map",
                                  "columns": ["经度", "纬度", "件数"],
                                  "rows": [{"经度": 116.4, "纬度": 39.9, "件数": 3}]}))
    assert out["chart_type_applied"] == "map_heat"
    assert any("不是平台类型名" in n for n in out["notes"]), out["notes"]


def test_render_chart_bare_sql_without_data_is_rejected(ctx, store, spans, semantic):
    out = _render_chart({"title": "T", "sql": "SELECT 1"})
    assert out.get("isError")
    assert "不接受裸 SQL" in _payload(out)["error"]
    assert store.rows == {}


def test_render_chart_fallback_sql_with_internal_identifiers_is_rejected(ctx, store, spans, semantic):
    out = _render_chart({"title": "T", "chart_type": "bar", "sql": "datasource_id: 5",
                         "columns": ["省份", "订单量"], "rows": [{"省份": "江苏", "订单量": 1}]})
    assert out.get("isError")
    assert "系统信息" in _payload(out)["error"]
    assert store.rows == {}


def test_render_chart_row_cap_blocks_before_storage(ctx, store, spans, semantic, monkeypatch):
    monkeypatch.setattr(rt, "_MAX_CHART_ROWS", 3)
    out = _render_chart({"title": "T", "chart_type": "bar",
                         "columns": ["省份", "订单量"],
                         "rows": [{"省份": f"省{i}", "订单量": i} for i in range(5)]})
    assert out.get("isError") and "超过上限" in _payload(out)["error"]
    assert store.rows == {}


def test_render_chart_revises_same_chart_id(ctx, store, spans, semantic):
    first = _payload(_render_chart({"title": "A", "chart_type": "bar",
                                    "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 1}]}))
    second = _payload(_render_chart({"title": "A", "chart_type": "bar", "chart_id": first["chart_id"],
                                     "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 2}]}))
    assert second["chart_id"] == first["chart_id"]
    assert len(store.rows) == 1, "修订同一张图不能产生第二份数据对象"


def test_chart_after_primary_document_is_blocked(ctx, store, spans, semantic):
    """G1 单轨的反向：已交整份文档就不再散铺图表片段（同一结论两份形态会互相对不上数）。"""
    _render_html({"title": "A", "html": GOOD_HTML})
    out = _render_chart({"title": "T", "chart_type": "bar",
                         "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 1}]})
    assert out.get("isError")
    assert "主交付物" in _payload(out)["error"]


def test_render_chart_records_rich_facts_consumed_by_html_channel(ctx, store, spans, semantic):
    """M2 事实登记：G2 的“本轮取到过地图数据”警告要吃真事实，而不是靠猜。"""
    out = _payload(_render_chart({"title": "T", "chart_type": "china_map",
                                  "columns": ["省份", "浏览量"],
                                  "rows": [{"省份": "江苏省", "浏览量": 251},
                                            {"省份": "广东省", "浏览量": 190}]}))
    assert out["chart_id"]
    assert "china_map" in (rt._channel_state(ctx).get("chart_types") or [])
    html = _payload(_render_html({"title": "R", "html": GOOD_HTML}))
    assert html.get("doc_id"), "富图型只是次要维度时应警告放行（方案 §10 待确认项 1 的推荐档）"
    assert any("china_map" in n for n in html["notes"]), html["notes"]
    assert store.rows[html["doc_id"]]["warnings"], "警告要随文档落库，前端才看得到徽标"


def test_render_chart_receipt_has_no_internal_identifiers(ctx, store, spans, semantic):
    out = _render_chart({"title": "T", "chart_type": "bar",
                         "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 1}]})
    text = json.dumps(out, ensure_ascii=False)
    for banned in ("user_id", "datasource_id", "workspace_id", GOOD_HTML[:20]):
        assert banned not in text


def test_render_chart_channel_choice_span_recorded(ctx, store, spans, semantic):
    _render_chart({"title": "A", "chart_type": "bar",
                   "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 1}]})
    _render_chart({"title": "B", "chart_type": "bogus",
                   "columns": ["省份", "量"], "rows": [{"省份": "江苏", "量": 1}]})
    choices = [s for s in spans if s.get("kind") == "delivery_channel"]
    assert len(choices) == 2
    ok, fail = choices
    assert json.loads(ok["output_text"])["channel"] == "chart" and ok["status"] == "success"
    assert fail["status"] == "error" and json.loads(fail["output_text"])["blockers"]


# ── render_report（spec）：逐章取数 + 结构硬门 + 失败章节显式化 ────────────

def _render_report(args):
    return asyncio.run(rt.render_report(args))


_SECTIONS_OK = [
    {"title": "核心指标", "kind": "kpi", "intent": {"object": "订单", "metrics": ["订单量"]}},
    {"title": "月度趋势", "kind": "chart", "chart_type": "line",
     "intent": {"object": "订单", "metrics": ["订单量"], "dimensions": ["月份"]},
     "narrative": "2 月环比上升 50%。"},
    {"title": "口径说明", "kind": "text", "narrative": "订单量按下单时间归属月份。"},
]


def test_render_report_fetches_each_section_and_stores_spec(ctx, store, spans, semantic):
    calls, _ = semantic
    out = _payload(_render_report({"title": "订单分析报告", "sections": _SECTIONS_OK}))
    assert out["doc_id"] and out["body_kind"] == "spec"
    assert out["sections_count"] == 3 and len(calls) == 2, "text 章节不取数"
    row = store.rows[out["doc_id"]]
    assert row["kind"] == "doc" and row["body_kind"] == "spec"
    doc = json.loads(row["body"])
    assert doc["sections"][1]["data"]["columns"] == ["月份", "订单量"]
    assert doc["sections"][2]["kind"] == "text" and "data" not in doc["sections"][2]
    assert [r["row_count"] for r in out["row_counts"]] == [2, 2, 0]
    assert [r["title"] for r in out["row_counts"]] == [s["title"] for s in _SECTIONS_OK]
    assert "body" not in out and "```report" in out["next"] and '"body_kind": "spec"' in out["next"]


def test_render_report_structure_errors_are_listed_at_once(ctx, store, spans, semantic):
    bad = [{"kind": "kpi"},
           {"title": "明细", "kind": "chart", "intent": {"object": "订单", "rows": [[1]]}},
           {"title": "流程", "kind": "mermaid"}]
    out = _render_report({"title": "T", "sections": bad})
    assert out.get("isError")
    payload = _payload(out)
    assert len(payload["blockers"]) >= 3, "逐项列全，不修一个耗一轮"
    assert any("缺 intent" in b for b in payload["blockers"])
    assert any("rows" in b for b in payload["blockers"]), "章节 intent 不接受 rows/sql"
    assert any("code" in b for b in payload["blockers"])
    assert store.rows == {}


def test_render_report_chart_shape_mismatch_rejects_whole_call(ctx, store, spans, semantic):
    _, state = semantic
    state["payload"] = {"columns": ["产品", "销量"], "rows": [{"产品": "A", "销量": 3}], "row_count": 1}
    out = _render_report({"title": "T", "sections": [
        {"title": "地域分布", "kind": "chart", "chart_type": "china_map",
         "intent": {"object": "订单"}}]})
    assert out.get("isError")
    assert "地理列" in _payload(out)["error"]
    assert store.rows == {}, "不拿半份/错图报告入库"


def test_render_report_section_fetch_failure_keeps_section_visible(ctx, store, spans, semantic,
                                                                   monkeypatch):
    """方案 §3 分支 F：取数失败的章节写“暂无数据 + 原因”，不得沿用上一版数字。"""
    seen = {"n": 0}

    async def flaky(intent, ctx_):
        seen["n"] += 1
        if seen["n"] == 1:
            return {"error": "会话未确定数据源"}, True
        return {"columns": ["月份", "订单量"], "rows": list(_SEM_ROWS), "row_count": 2}, False

    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    monkeypatch.setattr(sq, "_execute_single", flaky)
    out = _payload(_render_report({"title": "T", "sections": [
        {"title": "概览", "kind": "kpi", "intent": {"object": "订单"}},
        {"title": "趋势", "kind": "chart", "chart_type": "line", "intent": {"object": "订单"}}]}))
    assert out["doc_id"], "一章失败不能吞掉整份报告"
    assert out["failed_sections"] == ["1. 概览"], out["failed_sections"]
    doc = json.loads(store.rows[out["doc_id"]]["body"])
    assert "未确定数据源" in doc["sections"][0]["error"]
    assert doc["sections"][0].get("data") is None, "失败章节不得带上一版数字"
    assert doc["sections"][1]["data"]["rows"]


def test_render_report_and_html_are_mutually_exclusive(ctx, store, spans, semantic):
    first = _payload(_render_report({"title": "A", "sections": _SECTIONS_OK}))
    assert first["doc_id"]
    second = _render_html({"title": "B", "html": GOOD_HTML})
    assert second.get("isError")
    assert "主交付物" in _payload(second)["error"]
    # 带原 doc_id 的修订仍走同一份（不产第二份正文）
    revise = _payload(_render_report({"title": "A", "sections": _SECTIONS_OK,
                                     "doc_id": first["doc_id"]}))
    assert revise["doc_id"] == first["doc_id"] and len(store.rows) == 1


def test_render_report_unavailable_template_lists_candidates(ctx, store, spans, semantic, monkeypatch):
    from backend.modules.viz.services import report_service as reports
    monkeypatch.setattr(reports, "resolve_report_template_by_name",
                        lambda name: {"found": False, "reason": f"报告模板「{name}」不存在或已下架",
                                      "available": ["周报模板", "运营月报"]}, raising=True)
    out = _render_report({"title": "T", "template": "不存在的模板", "sections": _SECTIONS_OK})
    payload = _payload(out)
    assert "周报模板" in payload["error"] and "不存在的模板" in payload["error"]
    assert payload["blockers"] == ["template_unavailable"]
    assert store.rows == {}, "模板读不到不能静默退成通用排版（§1）"


def test_render_report_template_theme_and_key_are_recorded(ctx, store, spans, semantic, monkeypatch):
    from backend.modules.viz.services import report_service as reports
    monkeypatch.setattr(reports, "resolve_report_template_by_name",
                        lambda name: {"found": True, "template_key": "77", "name": name,
                                      "theme": "finance", "layout": {"headings": ["核心指标"]}},
                        raising=True)
    out = _payload(_render_report({"title": "T", "template": "周报模板", "sections": _SECTIONS_OK}))
    row = store.rows[out["doc_id"]]
    assert row["template_key"] == "77" and row["theme"] == "finance", "模板锁的主题优先于会话默认"
    assert out["template"] == "周报模板"
    assert json.loads(row["body"])["template_name"] == "周报模板"


def test_render_report_records_rich_chart_facts(ctx, store, spans, semantic):
    _, state = semantic
    state["payload"] = {"columns": ["省份", "浏览量"],
                        "rows": [{"省份": "江苏省", "浏览量": 251}, {"省份": "广东省", "浏览量": 12}],
                        "row_count": 2}
    _render_report({"title": "T", "sections": [
        {"title": "地域", "kind": "chart", "chart_type": "china_map", "intent": {"object": "访问"}}]})
    assert "china_map" in (rt._channel_state(ctx).get("chart_types") or [])


def test_render_report_mermaid_and_section_count_gates(ctx, store, spans, semantic):
    out = _payload(_render_report({"title": "T", "sections": [
        {"title": "流程", "kind": "mermaid", "code": "graph TD;A-->B;"}]}))
    assert out["doc_id"]
    assert json.loads(store.rows[out["doc_id"]]["body"])["sections"][0]["code"].startswith("graph TD")
    many = [{"title": f"第{i}章", "kind": "text", "narrative": "x"} for i in range(30)]
    blocked = _render_report({"title": "T", "sections": many})
    assert blocked.get("isError") and "超过上限" in _payload(blocked)["error"]


def test_render_report_channel_span_recorded(ctx, store, spans, semantic):
    _payload(_render_report({"title": "A", "sections": _SECTIONS_OK}))
    _render_report({"title": "A", "sections": [{"kind": "text"}]})
    choices = [s for s in spans if s.get("kind") == "delivery_channel"]
    assert len(choices) == 2
    assert json.loads(choices[0]["output_text"])["channel"] == "spec"
    assert json.loads(choices[1]["output_text"])["attempt"] == 2


# ── render 三形态的共同验收 ──────────────────────────────────────

def test_all_three_channels_are_registered_and_gated_by_authorization():
    """三个渲染机全部接线；契约只说已接的部分，没授权的工具不注入对应段。"""
    from backend.modules.mind.execution.chart_contract import render_contract_text
    from backend.modules.mind.execution.sdk_tools import build_tool_servers
    out = build_tool_servers("qoder", None,
                             selection={"render": ["render_chart", "render_html", "render_report"]})
    assert len(out["allowed_tools"]) == 3
    full = render_contract_text(["render_chart", "render_html", "render_report"])
    for marker in ("chart_ref", "```report", "render_report", "body_kind"):
        assert marker in full, marker
    assert "尚未开放" not in full, "接线完成后还留着“尚未开放”就是陈旧描述"
    # 只授权其中一条时，契约不得提名另一条（那会把没授权的工具说成可用）
    only_spec = render_contract_text(["render_report"])
    assert "render_html" not in only_spec and "render_chart" not in only_spec
    only_chart = render_contract_text(["render_chart"])
    assert "render_report" not in only_chart and "render_html" not in only_chart


# ── 文档读取：三态可区分（前端文案要靠它）────────────────────────────

def test_load_document_distinguishes_missing_and_forbidden(ctx, store, spans):
    doc_id = _payload(_render_html({"title": "T", "html": GOOD_HTML}))["doc_id"]
    loaded = docs.load_document(doc_id, user_id=7)
    assert loaded["body"] == GOOD_HTML
    assert "user_id" not in loaded, "属主 id 是实现细节，不外泄给调用方"

    with pytest.raises(docs.RenderDocumentError) as exc_missing:
        docs.load_document("deadbeef", user_id=7)
    assert exc_missing.value.code == "not_found"

    with pytest.raises(docs.RenderDocumentError) as exc_owner:
        docs.load_document(doc_id, user_id=8)
    assert exc_owner.value.code == "forbidden"


# ── 工具注册与阶段门（未接线的形态不得下发给模型）────────────────────

def test_render_group_registers_only_wired_tools():
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS, build_tool_servers
    assert TOOL_SERVER_TOOLS["render"][0] == "datahub_render"
    assert TOOL_SERVER_TOOLS["render"][1] == ["render_chart", "render_html", "render_report"]
    out = build_tool_servers("qoder", None, selection={"render": ["render_html"]})
    assert out["allowed_tools"] == ["mcp__datahub_render__render_html"]
    both = build_tool_servers("qoder", None, selection={"render": ["render_chart", "render_html"]})
    assert both["allowed_tools"] == ["mcp__datahub_render__render_chart",
                                     "mcp__datahub_render__render_html"]
    # 清单里没登记的工具名必须显式报错，不能静默注册成“看得见调不动”的空工具
    with pytest.raises(ValueError):
        build_tool_servers("qoder", None, selection={"render": ["render_dashboard"]})
    # 显式空选择不得回退整组注册
    assert build_tool_servers("qoder", None, selection={"render": []})["allowed_tools"] == []


def test_as_bot_tool_catalog_lists_render_group():
    """AS-BOT 编辑器拿不到这个组 = 配不了，属"功能有、配置无"。"""
    from backend.modules.platform.api.as_bots import _TOOL_GROUP_META, _TOOL_LABELS
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    assert "render" in _TOOL_GROUP_META
    for _, (_server, tool_names) in TOOL_SERVER_TOOLS.items():
        for name in tool_names:
            assert name in _TOOL_LABELS, f"工具 {name} 未登记中文标签（编辑器里显示成裸英文像不可选项）"


def test_migration_scripts_stay_in_sync_with_registered_tools():
    """授权脚本与权威清单必须同源：漏更新会让新工具静默不下发（历史同类坑）。"""
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS
    from backend.scripts import migrate_render_tool_group as mig
    assert mig.RENDER_TOOLS == TOOL_SERVER_TOOLS["render"][1]


def test_migration_merge_does_not_mutate_its_input():
    """幂等判定必须在副本上做：原地改入参会让“待改 vs 原值”永远相等 → 新工具静默不下发。

    这个坑在本批接入 render_chart 时真撞过一次（dry-run 报“无需变更”，而库里只有
    render_html）：只数 affected rows 根发现不了。
    """
    from backend.scripts import migrate_render_tool_group as mig
    tools = {"mcp": {"render": ["render_html"], "semantic": ["get_metrics"]}, "groups": ["render"]}
    before = json.dumps(tools, sort_keys=True)
    merged = mig._merge(tools)
    assert json.dumps(tools, sort_keys=True) == before, "入参不得被改动"
    assert "render_chart" in merged["mcp"]["render"]
    assert merged["mcp"]["semantic"] == ["get_metrics"], "只动本批拥有的组，其他组原样保留"
    # 已是最新则原样返回（二次重跑不产生 UPDATE）
    assert mig._merge(merged) == merged


# ── 交付契约注入门控（没授权就不能向模型承诺这条通道存在）──────────────────

def _compose(cap_names):
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    caps = {"tools": [{"name": n, "description": ""} for n in cap_names], "unavailable_tools": []}
    return compose_system_prompt(None, [], username="u", user_role="analyst", capabilities=caps)


def test_render_contract_injected_only_when_channel_granted():
    granted = _compose(["mcp__datahub_render__render_html", "mcp__datahub_semantic__run_semantic_query"])
    not_granted = _compose(["mcp__datahub_semantic__run_semantic_query"])
    assert "交付形态渲染机" in granted
    assert "交付形态渲染机" not in not_granted, "未授权也注入契约 = 向模型广告调不到的入口"


def test_render_contract_sections_follow_granted_tools():
    """契约按工具粒度注入：只勾图表机就不能承诺整页文档（同阶段门纪律，只是粒度更细）。"""
    from backend.modules.mind.execution.chart_contract import render_contract_text
    only_chart = render_contract_text(["render_chart"])
    assert "chart_ref" in only_chart
    assert "```report" not in only_chart and "render_html" not in only_chart, only_chart[:200]
    only_html = render_contract_text(["render_html"])
    assert "```report" in only_html and "chart_ref" not in only_html
    assert render_contract_text([]) == "", "一个都没授权时不得注入空段落占预算"


def test_render_contract_covers_all_wired_channels():
    """三个形态都已接线：契约要齐（引用块形状写死），且不再留着“尚未开放”的陈旧描述。"""
    from backend.modules.mind.execution.chart_contract import render_contract_text
    full = render_contract_text(["render_chart", "render_html", "render_report"])
    assert "尚未开放" not in full
    assert "```report" in full, "引用块形状要写明（否则前端拿不到块，用户只看到文字描述）"
    assert "chart_ref" in full and "body_kind\": \"spec" in full
    # 单轨与代价都要写进契约，不能只靠 SKILL 长文
    for must in ("只一个主交付物", "主题快照", "不可下钻", "暂无数据"):
        assert must in full, must


def test_render_contract_size_is_bounded():
    """契约常驻每轮重发，尺寸要有数（预算护栏只能由人调，不得静默抬阈值）。

    三个渲染机都接线后全文实测 2225 字符；M5 补上“报告主体不要再同时交一个 artifact .html”
    那段边界后现为 2381（上限 3000 = 约 1.26 倍余量）。再多一段契约就要拍板而不是改数字。
    """
    from backend.modules.mind.execution.chart_contract import render_contract_text
    full = render_contract_text(["render_chart", "render_html", "render_report"])
    assert 1_500 <= len(full) < 3_000, len(full)
    # artifact 降格为下载件载体（fence 通道保留兼容，但报告主体不再是“去文件清单里找的文件”）
    assert "只留给" in full and "```artifact" in full


def test_tool_descriptions_carry_the_three_hard_guidances():
    """§3 的三条硬引导必须进 tool description：只写 SKILL 长文，没命中技能的回合就看不见。"""
    specs = {s["name"]: s["description"] for s in rt.TOOL_SPECS}
    html = specs["render_html"]
    # ② 选 html 前的富图型自检（LLM 天然爱写长文档，这句是对“系统性偏 html”的唯一运行时对策）
    assert "先自问" in html and "核心结论" in html and "render_report" in html
    # ③ 四项代价写进元信息（退让不能伪装成等价）
    assert "四项代价" in html and "元信息" in html
    # ① 一轮只一个主交付物：两条文档通道各自说一遍（模型只看到一个工具时也能收到）
    assert "只允许一个主交付物" in html and "一轮只一个主交付物" in specs["render_report"]
    assert "render_html" in specs["render_report"], "互斥要指名对方，不然仍是两套并存"
