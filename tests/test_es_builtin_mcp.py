"""平台内置 Elasticsearch 能力的回归门禁。

锁定的不变量（2026-11-04 决策：ES 作为内置能力而非数据源，不进列级/行级治理域）：
1. 索引白名单是数据面唯一闸门，且 **空白名单 = 不开放任何索引**（fail-closed，
   不得解释为"没配就不限制"）；
2. 只读口径与护栏 §5 一致（仅单条 SELECT、禁 DDL/DML/多语句、必须 LIMIT、
   LIMIT 按配置上限收紧）；
3. 任何对外文案**不含**连接地址 / 端口 / 账号 / 凭据 / ES 原始响应（护栏 §7）；
4. 内置服务在「MCP 服务」页只读投影，响应体里根本没有配置字段可泄；
5. 未开启的能力不列入 AS-BOT 工具目录（不做"配了没用"的死开关）；
6. ES 通道的结果必须带 governance 标注（与治理通道可区分，no-silent-degradation）。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.common import config
from backend.core import es_client
from backend.modules.mind.execution import builtin_mcp
from backend.modules.mind.execution.sdk_tools import es_tools


# ── 夹具：可控的 ES 配置 ────────────────────────────────────────────────

@pytest.fixture
def es_config(monkeypatch):
    """把 ES 配置置为"已开启 + 有地址 + 有白名单"，返回可继续覆盖的 SimpleNamespace。"""
    state = SimpleNamespace(
        ES_MCP_ENABLED=True,
        ES_URL="http://es-inner:9200",
        ES_USERNAME="svc-es-readonly",
        ES_PASSWORD="s3cr3t-pass",
        ES_API_KEY="",
        ES_VERIFY_CERTS=False,
        ES_TIMEOUT_SEC=5,
        ES_MAX_ROWS=1000,
        ES_ALLOWED_INDEXES=("orders-*", "cases"),
    )
    for key, value in vars(state).items():
        monkeypatch.setattr(config, key, value)
    return state


class _FakeResp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _install_transport(monkeypatch, responses):
    """替换 requests.request，记录每次调用参数供断言。"""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return responses(len(calls) - 1) if callable(responses) else responses

    monkeypatch.setattr(es_client.requests, "request", _fake)
    return calls


# ── 1. 白名单：数据面唯一闸门 ───────────────────────────────────────────

def test_empty_allowlist_opens_nothing(monkeypatch):
    """空白名单必须拒绝一切 —— 不得当成"不限制"（与空角色授权不同口径）。"""
    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ())
    assert es_client.index_allowed("orders-2026") is False
    assert es_client.index_allowed("anything") is False
    with pytest.raises(es_client.EsError):
        es_client.require_index("anything")


@pytest.mark.parametrize("index", ["orders-2026.11", "orders-", "cases",
                                   "orders-*",         # 模式本身即查询单位（Kibana data view 同构）
                                   "orders-*2026.11"])  # 模式后接时间片段收窄（模式原样保留才成立）
def test_allowlist_pattern_match(es_config, index):
    assert es_client.index_allowed(index) is True


@pytest.mark.parametrize("index", ["*", "cases*", "orders_backup*", "*2026.11",
                                   "orders*2026.11"])  # 丢掉模式里的 - 就不是“模式内收窄”，而是扩权
def test_target_wildcard_cannot_widen_beyond_allowlist(es_config, index):
    """目标带 `*` 也必须整体落在某个已开放模式内 —— 否则通配会变成扩权通道。

    `orders*2026.11` 被拒是故意的：ES 会把它展开成 `ordersbackup-2026.11` 这类
    不属于 `orders-*` 的索引，而 fnmatch 把目标当字串比对正好括住这个口。
    """
    assert es_client.index_allowed(index) is False


@pytest.mark.parametrize("index", ["users", "orders_backup", "cases_hidden", "log-2026"])
def test_index_outside_allowlist_rejected(es_config, index):
    assert es_client.index_allowed(index) is False


def test_rejection_message_lists_available_without_leaking(es_config):
    """拒绝文案给出可查清单引导改写，但不得出现地址/账号/口令。"""
    with pytest.raises(es_client.EsError) as exc:
        es_client.require_index("users")
    message = str(exc.value)
    assert "cases" in message and "orders-*" in message
    for leak in ("es-inner", "9200", "elastic", "s3cr3t-pass", "http://"):
        assert leak not in message


def test_quoted_index_name_is_normalized(es_config):
    """ES SQL 的 `"orders-2026"` 带引号写法要能正确过闸（引号不是白名单的一部分）。"""
    assert es_client.index_allowed('"orders-2026"') is True
    assert es_client.require_index('"cases"') == "cases"


# ── 2. 只读口径（护栏 §5）──────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "DELETE FROM cases LIMIT 10",
    "UPDATE cases SET a=1 LIMIT 10",
    "INSERT INTO cases VALUES (1) LIMIT 10",
    "DROP TABLE cases",
    "CREATE INDEX x ON cases (a)",
    "WITH x AS (SELECT 1) SELECT * FROM x LIMIT 10",   # ES SQL 不吃 CTE，且本通道只放 SELECT
    "SELECT * FROM cases",                              # 缺 LIMIT
    "SELECT * FROM cases LIMIT -1",
    "SELECT * FROM cases LIMIT 0",
    "SHOW FUNCTIONS",
])
def test_non_readonly_or_unbounded_rejected(es_config, sql):
    with pytest.raises(es_client.EsError):
        es_client.parse_select(sql)


def test_multi_statement_rejected_but_semicolon_in_literal_ok(es_config):
    """字符串字面量里的分号不算语句分隔；真分隔必须拒。"""
    ok, _ = es_client.parse_select("SELECT * FROM cases WHERE note = 'a;b' LIMIT 10")
    assert "a;b" in ok
    with pytest.raises(es_client.EsError):
        es_client.parse_select("SELECT 1 FROM cases LIMIT 1; SELECT 2 FROM cases LIMIT 1")


def test_unlisted_index_in_sql_rejected(es_config):
    with pytest.raises(es_client.EsError):
        es_client.parse_select("SELECT * FROM users LIMIT 10")


def test_comment_content_is_not_mistaken_for_code(es_config):
    """注释里的关键字不当代码误拒；注释也不能用来隔开多条语句。"""
    query, _ = es_client.parse_select("SELECT a FROM cases /* DROP TABLE x */ LIMIT 10")
    assert "LIMIT 10" in query
    with pytest.raises(es_client.EsError):
        es_client.parse_select("SELECT a FROM cases LIMIT 10 -- c\n; DELETE FROM cases LIMIT 1")


def test_limit_tightened_to_config_cap(monkeypatch):
    monkeypatch.setattr(config, "ES_MCP_ENABLED", True)
    monkeypatch.setattr(config, "ES_URL", "http://es-inner:9200")
    monkeypatch.setattr(config, "ES_MAX_ROWS", 50)
    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ("cases",))
    query, limit = es_client.parse_select("SELECT * FROM cases LIMIT 99999")
    assert limit == 50 and "LIMIT 50" in query


def test_line_comment_is_stripped_before_head_check(es_config):
    query, _ = es_client.parse_select("-- 取最新数据\nSELECT * FROM cases LIMIT 10")
    assert query.upper().startswith("SELECT")


def test_multi_index_from_rejected(es_config):
    """实机校准 ES 会拒多表，但 `_FROM_REF` 只取逗号前一段：白名单不能靠下游报错兜底。"""
    with pytest.raises(es_client.EsError) as exc:
        es_client.parse_select('SELECT * FROM "orders-2026", users LIMIT 5')
    assert "多表" in str(exc.value)


def test_group_by_comma_is_not_mistaken_for_multi_index(es_config):
    """`FROM t GROUP BY a, b` 是单表：多表判定必须锚在 FROM 目标后面（不然常见查询误拒）。"""
    query, _ = es_client.parse_select("SELECT a, b FROM cases GROUP BY a, b LIMIT 10")
    assert "GROUP BY a, b" in query


def test_join_rejected(es_config):
    with pytest.raises(es_client.EsError) as exc:
        es_client.parse_select('SELECT * FROM "orders-2026" a INNER JOIN users b ON a.id = b.id LIMIT 5')
    assert "JOIN" in str(exc.value)


def test_constant_query_without_from_rejected(es_config):
    """ES 能跑 `SELECT 1`（实测），但本通道不做无表常量查询：fail-closed。"""
    with pytest.raises(es_client.EsError):
        es_client.parse_select("SELECT 1 AS x LIMIT 1")


# 以下三条锁定实机校准（ES 8.9.1）发现的三个真实行为。

def test_quoted_hyphen_index_is_not_treated_as_literal(es_config):
    """双引号是**标识符引用**：当字面量剥掉会让正确写法被白名单误拒（实机踩过）。"""
    query, _ = es_client.parse_select('SELECT * FROM "orders-2026.11" LIMIT 5')
    assert '"orders-2026.11"' in query


def test_unquoted_hyphen_index_rejected_with_rewrite_hint(es_config):
    """未包引号的连字符索引名 ES 会当减法：报错要直接给出可照抄的改写。"""
    with pytest.raises(es_client.EsError) as exc:
        es_client.parse_select("SELECT * FROM orders-2026.11 LIMIT 5")
    assert 'FROM "orders-2026.11"' in str(exc.value)


def test_replace_function_is_not_mistaken_for_write(es_config):
    """ES SQL 有 REPLACE() 字符串函数：全文关键字黑名单会误拒合法只读查询。"""
    query, _ = es_client.parse_select("SELECT REPLACE(name, 'a', 'b') AS n FROM cases LIMIT 5")
    assert "REPLACE" in query


def test_plain_index_needs_no_quotes(es_config):
    query, _ = es_client.parse_select("SELECT a FROM cases LIMIT 5")
    assert "FROM cases" in query


# ── 3. 传输层错误不得泄露物理信息（护栏 §7）────────────────────────────

def test_http_error_returns_generic_message(es_config, monkeypatch):
    """ES 4xx 正文常含索引表达式/集群信息：原始体只进日志，对外给通用文案。"""
    _install_transport(monkeypatch, _FakeResp(None, status=400,
                                              text='{"error":{"reason":"parse failure at [1:24]"}}'))
    with pytest.raises(es_client.EsError) as exc:
        es_client.run_sql("SELECT * FROM cases LIMIT 10")
    message = str(exc.value)
    assert "parse failure" not in message and "es-inner" not in message
    assert "Elasticsearch" in message


def test_connection_error_hides_host(es_config, monkeypatch):
    def _boom(**kwargs):
        raise requests.exceptions.ConnectionError("Failed to establish a new connection: es-inner:9200")

    monkeypatch.setattr(es_client.requests, "request", _boom)
    with pytest.raises(es_client.EsError) as exc:
        es_client.run_sql("SELECT * FROM cases LIMIT 10")
    assert "es-inner" not in str(exc.value) and "9200" not in str(exc.value)


def test_missing_configuration_fails_loud(monkeypatch):
    monkeypatch.setattr(config, "ES_URL", "")
    with pytest.raises(es_client.EsError):
        es_client.run_sql("SELECT * FROM cases LIMIT 10")


def test_api_key_sent_as_header_not_url(es_config, monkeypatch):
    monkeypatch.setattr(config, "ES_API_KEY", "AK-XYZ")
    monkeypatch.setattr(config, "ES_USERNAME", "")
    calls = _install_transport(monkeypatch, _FakeResp({"columns": [], "rows": []}))
    es_client.run_sql("SELECT * FROM cases LIMIT 10")
    sent = calls[0]
    assert sent["headers"]["Authorization"] == "ApiKey AK-XYZ"
    assert "AK-XYZ" not in sent["url"]


# ── 4. 执行与探查的正常路径 ─────────────────────────────────────────────

def test_run_sql_returns_columns_rows_and_indexes(es_config, monkeypatch):
    payload = {"columns": [{"name": "order_id", "type": "LONG"},
                           {"name": "amount", "type": "DOUBLE"}],
               "rows": [[1, 9.9], [2, 10.0]]}
    calls = _install_transport(monkeypatch, _FakeResp(payload))
    result = es_client.run_sql('SELECT order_id, amount FROM "orders-2026" LIMIT 10')
    assert result["row_count"] == 2 and result["indexes"] == ["orders-2026"]
    sent = calls[0]
    assert sent["url"].endswith("/_sql")
    # 多值字段按 lenient 取：否则"查一条就报错"会成为常态（ES 默认对数组字段抛异常）
    assert sent["json"]["field_multi_value_leniency"] is True


def test_describe_pattern_splits_subfields_and_keyword(es_config, monkeypatch):
    """字段探查对**模式**一次请求就够（实机：模式 = 所有时间分片的字段并集）。"""
    payload = {"fields": {"_index": {"keyword": {}},
                          "name": {"text": {"aggregatable": False, "searchable": True}},
                          "name.keyword": {"keyword": {"aggregatable": True, "searchable": True}},
                          "dep.dep_name": {"keyword": {}}}}
    calls = _install_transport(monkeypatch, _FakeResp(payload))
    info = es_client.describe_index_fields("orders-*")
    assert [f["field"] for f in info["fields"]] == ["name"]
    assert [f["field"] for f in info["sub_fields"]] == ["dep.dep_name", "name.keyword"]
    # text 字段不提示 .keyword 就会“语法对但查不出数”（分组/等值过滤必须走 keyword）
    assert info["fields"][0]["exact_match_field"] == "name.keyword"
    assert calls[0]["url"].endswith("/orders-*/_field_caps")
    assert calls[0]["params"]["ignore_unavailable"] == "true"


def test_describe_index_rejects_unlisted(es_config):
    with pytest.raises(es_client.EsError):
        es_client.describe_index_fields("users")


def test_describe_unmatched_pattern_fails_loud(es_config, monkeypatch):
    """实机校准：`_field_caps` 对匹配不到的通配模式返 HTTP 200 + 空 fields（不报 404）。

    静默回空清单会被模型当成"这个范围没字段"而去猜字段名 —— 必须显式报错。
    """
    _install_transport(monkeypatch, _FakeResp({"fields": {}}))
    with pytest.raises(es_client.EsError) as exc:
        es_client.describe_index_fields("orders-*")
    assert "未匹配到任何字段" in str(exc.value)


def test_list_patterns_does_not_expand_or_call_cluster(es_config, monkeypatch):
    """data view 口径：可查清单就是已开放模式本身，不发请求也不展开成月索引。

    实机校准一个模式背 331~403 个月分片，展开会把上千个名字塞进模型上下文。
    """
    def _boom(**kwargs):
        raise AssertionError("列开放范围不该发请求（展开是集群侧在查询时做的事）")

    monkeypatch.setattr(es_client.requests, "request", _boom)
    assert es_client.list_patterns() == ["orders-*", "cases"]


# ── 5. 工具层：未开启要显式提示，结果要标治理口径 ──────────────────────

def test_tool_reports_unconfigured_loudly(monkeypatch):
    """未开启时不得返回空清单 —— 空结果会被模型当成"索引里没数据"从而答错。"""
    monkeypatch.setattr(config, "ES_MCP_ENABLED", False)
    result = asyncio.run(es_tools.list_es_indexes({}))
    assert result.get("isError") is True
    payload = json.loads(result["content"][0]["text"])
    assert "配置" in payload["error"]


def test_list_tool_returns_patterns_not_months(es_config, monkeypatch):
    """工具层同口径：返回开放模式，不展开成月索引（展开会把上下文塞满）。"""
    def _boom(**kwargs):
        raise AssertionError("列开放范围不该访问集群")

    monkeypatch.setattr(es_client.requests, "request", _boom)
    payload = json.loads(asyncio.run(es_tools.list_es_indexes({}))["content"][0]["text"])
    assert payload["data_views"] == ["orders-*", "cases"]
    assert 'FROM "<pattern>"' in payload["how_to_query"]
    assert payload["governance"]["gate"] == "index_allowlist"


def test_list_tool_empty_allowlist_is_error_not_empty_list(es_config, monkeypatch):
    """空白名单要报错而不是空清单：空清单会被模型答成"这个范围没有数据"。"""
    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ())
    result = asyncio.run(es_tools.list_es_indexes({}))
    assert result.get("isError") is True
    assert "未开放" in json.loads(result["content"][0]["text"])["error"]


def test_describe_tool_returns_view_and_field_split(es_config, monkeypatch):
    _install_transport(monkeypatch, _FakeResp({"fields": {"a": {"keyword": {}}}}))
    monkeypatch.setattr(es_tools, "_audit", lambda *a, **k: None)
    payload = json.loads(
        asyncio.run(es_tools.describe_es_index({"index": "orders-*"}))["content"][0]["text"])
    assert payload["view"] == "orders-*" and payload["total"] == 1
    assert ".keyword" in payload["hint"]  # text 不能精确分组：不提示就会查不出数


def test_query_result_carries_governance_note(es_config, monkeypatch):
    """ES 通道结果必须可被审计/评测分辨：显式声明未套列级/行级策略。"""
    _install_transport(monkeypatch, _FakeResp(
        {"columns": [{"name": "a", "type": "LONG"}], "rows": [[1]]}))
    monkeypatch.setattr(es_tools, "_audit", lambda *a, **k: None)
    result = asyncio.run(es_tools.query_es_sql({"sql": "SELECT a FROM cases LIMIT 10"}))
    payload = json.loads(result["content"][0]["text"])
    assert payload["governance"]["channel"] == "builtin_elasticsearch"
    assert payload["governance"]["column_policy"] == "not_applied"
    assert payload["rows"] == [{"a": 1}]


def test_query_error_does_not_leak_transport_detail(es_config, monkeypatch):
    def _boom(**kwargs):
        raise requests.exceptions.ConnectionError("read timeout to es-inner:9200 as svc-es-readonly")

    monkeypatch.setattr(es_client.requests, "request", _boom)
    monkeypatch.setattr(es_tools, "_audit", lambda *a, **k: None)
    result = asyncio.run(es_tools.query_es_sql({"sql": "SELECT a FROM cases LIMIT 10"}))
    text = result["content"][0]["text"]
    assert result.get("isError") is True
    for leak in ("es-inner", "9200", "svc-es-readonly", "timeout"):
        assert leak not in text


# ── 6. 内置 MCP 服务的注册与授权接线 ──────────────────────────────────

def test_es_is_not_a_data_tool_group():
    """ES 只从「引用 MCP 服务」入口配置：不得同时存在于数据工具组（双口径）。"""
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS, TOOL_SERVER_BUILDERS

    assert "elasticsearch" not in TOOL_SERVER_TOOLS
    assert "elasticsearch" not in TOOL_SERVER_BUILDERS


def test_builtin_row_detection():
    assert builtin_mcp.is_builtin_row({"transport": "builtin"}) is True
    assert builtin_mcp.is_builtin_row({"is_builtin": 1}) is True
    assert builtin_mcp.is_builtin_row({"transport": "stdio"}) is False
    assert builtin_mcp.is_builtin_row({}) is False


def test_builtin_server_registers_under_authorized_prefix():
    """注册前缀必须与 tool_policy 生成的 `mcp__external_{sid}__{tool}` 同源，否则勾了调不到。"""
    cfg = builtin_mcp.build_builtin_server(
        "qoder", {"name": "elasticsearch"}, "external_9001",
        "mcp__external_9001", ["list_es_indexes", "query_es_sql"])
    assert cfg["name"] == "external_9001"


def test_unregistered_builtin_row_rejected():
    """库里有内置行但代码没登记实现：显式拒，不能当成“没这个服务”静默跳过。"""
    with pytest.raises(PermissionError):
        builtin_mcp.build_builtin_server("qoder", {"name": "ghost"},
                                        "external_9", "mcp__external_9", ["x"])


def test_selected_builtin_tool_must_exist():
    with pytest.raises(ValueError):
        builtin_mcp.build_builtin_server("qoder", {"name": "elasticsearch"},
                                        "external_9001", "mcp__external_9001", ["not_a_tool"])


def test_binding_not_ready_builtin_service_is_rejected(es_config, monkeypatch):
    """未就绪的内置服务不给注册：否则模型拿到工具只会反复报错（不假承诺）。"""
    from backend.modules.mind.execution.sdk_tools import external_tools

    row = {"id": 9001, "name": "elasticsearch", "transport": "builtin", "is_builtin": 1}
    monkeypatch.setattr(external_tools, "execute_query", lambda *a, **k: dict(row))
    policy = SimpleNamespace(as_bot={"mcp_server_ids": [9001]},
                             external={"9001": ["list_es_indexes"]})

    monkeypatch.setattr(config, "ES_MCP_ENABLED", False)
    with pytest.raises(PermissionError):
        external_tools.load_server(9001, policy)

    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ())
    monkeypatch.setattr(config, "ES_MCP_ENABLED", True)
    with pytest.raises(PermissionError):
        external_tools.load_server(9001, policy)

    monkeypatch.setattr(config, "ES_ALLOWED_INDEXES", ("cases",))
    assert external_tools.load_server(9001, policy)["name"] == "elasticsearch"


def test_binding_unknown_builtin_impl_is_rejected(es_config, monkeypatch):
    from backend.modules.mind.execution.sdk_tools import external_tools

    row = {"id": 9002, "name": "ghost", "transport": "builtin", "is_builtin": 1}
    monkeypatch.setattr(external_tools, "execute_query", lambda *a, **k: dict(row))
    policy = SimpleNamespace(as_bot={"mcp_server_ids": [9002]}, external={"9002": ["x"]})
    with pytest.raises(PermissionError):
        external_tools.load_server(9002, policy)


# ── 7. 展示与写保护：内置行不回配置字段、不可改删 ────────────────────

BUILTIN_ROW = {"id": 9001, "name": "elasticsearch", "transport": "builtin", "is_builtin": 1,
               "description": "平台内置 Elasticsearch 只读查询能力", "is_active": 1,
               # 故意放入脏数据：历史误填或手工改库也不能把凭据带进页面
               "url": "http://stale-es:9200", "command": "npx es-server",
               "args": "--nodeps", "env": '{"ES_PASSWORD": "s3cr3t-pass"}',
               "tools_config": "{}", "discovered_tools": "[]"}


def test_builtin_row_projection_strips_configuration(es_config):
    from backend.modules.platform.api import mcp_servers as api

    out = api._project_builtin(dict(BUILTIN_ROW))
    for key in ("url", "command", "args", "env", "tools_config"):
        assert key not in out
    text = json.dumps(out, ensure_ascii=False)
    for leak in ("stale-es", "9200", "s3cr3t-pass", "npx", "svc-es-readonly"):
        assert leak not in text
    assert out["builtin"] is True and out["state"] == "ready" and out["is_active"] == 1
    assert out["label"] == "Elasticsearch 检索服务"


def test_projection_marks_not_ready_service_inactive(monkeypatch):
    """未就绪的内置行对外 is_active=0：AS-BOT 候选按 is_active 过滤，绑不上也不会“绑了必错”。"""
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(config, "ES_MCP_ENABLED", False)
    out = api._project_builtin(dict(BUILTIN_ROW))
    assert out["is_active"] == 0 and out["state"] == "disabled"
    assert "未启用" in out["status_message"]


def test_catalog_projection_carries_no_configuration(es_config):
    """展示侧单一事实源：catalog() 的结构里根本没有配置字段可泄。"""
    text = json.dumps(builtin_mcp.catalog(), ensure_ascii=False)
    for leak in ("stale-es", "9200", "s3cr3t-pass", "svc-es-readonly", "http://", "orders-*"):
        assert leak not in text
    item = builtin_mcp.catalog()[0]
    assert {t["name"] for t in item["tools"]} == {
        "list_es_indexes", "describe_es_index", "query_es_sql"}
    assert item["open_view_count"] == 2  # 只给数量，不给模式内容（模式名属部署配置）


def test_probe_message_hides_cluster_identity(es_config, monkeypatch):
    _install_transport(monkeypatch, _FakeResp(
        {"version": {"number": "8.13.0"}, "cluster_name": "prod-secret-cluster",
         "name": "node-7.internal"}))
    result = builtin_mcp._es_probe()
    text = json.dumps(result, ensure_ascii=False)
    assert result["success"] is True
    for leak in ("prod-secret-cluster", "node-7.internal", "es-inner", "9200", "s3cr3t-pass"):
        assert leak not in text
    assert "已开放 2 个索引范围" in result["message"]


def test_probe_reports_disabled_without_connecting(es_config, monkeypatch):
    called = []
    monkeypatch.setattr(es_client.requests, "request",
                        lambda **kw: called.append(kw) or _FakeResp({}))
    monkeypatch.setattr(config, "ES_MCP_ENABLED", False)
    result = builtin_mcp._es_probe()
    assert result["success"] is False and "未启用" in result["message"]
    assert called == []  # 未就绪时不应去碰集群


def test_builtin_test_writeback_keeps_tool_catalog(es_config, monkeypatch):
    """内置行的工具清单来自代码登记：测试失败也不得清空它，否则逐工具授权渲不出。"""
    from backend.modules.platform.api import mcp_servers as api

    captured = {}
    monkeypatch.setattr(api, "execute_write",
                        lambda sql, params=None: captured.update(sql=sql, params=params))
    api.update_builtin_test_result(9001, {"success": False, "message": "x" * 900})
    assert "discovered_tools" not in captured["sql"]
    assert "is_builtin=1" in captured["sql"]
    assert len(captured["params"][2]) <= 500  # 对齐列宽，不靠数据库报错发现


def test_builtin_row_cannot_be_edited_or_deleted(es_config, monkeypatch):
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query", lambda *a, **k: dict(BUILTIN_ROW))
    for verb in ("修改", "删除"):
        with pytest.raises(HTTPException) as exc:
            api._reject_builtin_mutation(9001, verb)
        assert exc.value.status_code == 409
        assert "系统维护" in exc.value.detail and "部署侧托管" in exc.value.detail


def test_builtin_row_mutation_allows_external_rows(es_config, monkeypatch):
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query",
                        lambda *a, **k: {"id": 7, "name": "log-warehouse", "transport": "sse"})
    api._reject_builtin_mutation(7, "修改")  # 外部服务不受限


def test_page_cannot_create_fake_builtin_service(es_config):
    """内置服务只能由迁移登记：允许页面新建 builtin = 允许伪造无实现入口。"""
    from backend.modules.platform.api import mcp_servers as api

    with pytest.raises(HTTPException) as exc:
        api.create_mcp_server(api.MCPServerCreate(name="fake-builtin", transport="builtin"))
    assert exc.value.status_code == 409
