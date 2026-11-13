"""ES 供数层闸门 + 内置 MCP 机制的回归门禁。

【口径已换（2026-11-13 阶段三）】内置 Elasticsearch MCP 服务（`adh_mcp_servers` 旧 id=9001 与
`list_es_indexes`/`describe_es_index`/`query_es_sql` 三工具）**已退役**：ES 现在是**正式数据源**
（`adh_datasources.db_type='es'`），取数一律过治理执行口（角色源授权 + RLS/列级 + 审计，护栏 §1）。
那三个工具直接调 `es_client`，既不查角色数据源授权也不套列级/行级策略，属护栏 §1 的旁路，故整体下线。

本文件锁三层不变量：
1. **ES 供数层闸门**（第 1-4 节）：开放范围 fail-closed（**空白名单 = 不开放任何索引**，
   不得解释为"没配就不限制"）、只读口径与护栏 §5 一致（仅单条 SELECT、禁 DDL/DML/多语句、
   必须 LIMIT、LIMIT 按上限收紧）、任何对外文案不含地址/端口/账号/凭据/ES 原始响应（护栏 §7）；
2. **退役可证伪**（第 5 节）：注册表查不到 elasticsearch、`es_tools` 模块与种子脚本已删除、
   任何工具组不广告裸 ES SQL 入口、库里残留的内置行被显式拒（不是静默无工具）；
3. **内置 MCP 机制**（第 6-7 节）：与外部服务同一授权入口、未登记实现/未就绪均显式拒、
   响应投影剥光配置字段、内置行不可被页面改删 —— 将来登记新内置服务时按同一口径把关。

（阶段三第 2 步已摘掉 `config.ES_*` 与 `es_client.legacy_ctx()`：第 1-4 节的夹具因此改为
按「一条 ES 型数据源」构造 `EsCtx`，闸门断言逐字不变。）
"""
import functools
import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.common import config
from backend.core import es_client
from backend.modules.mind.execution import builtin_mcp


# ── 夹具：ES 目标（一条 ES 型数据源） ────────────────────────────────────────────────

ES_SRC_ID = 1794535612029          # 相当于一条已登记的 `db_type='es'` 数据源行
_ES_ENTRY_POINTS = ("index_allowed", "require_index", "parse_select", "run_sql",
                    "translate_select", "list_patterns", "describe_index_fields", "health")


class EsTarget:
    """用例里可改单项的 ES 目标（每次调用现组 EsCtx，改了立即生效）。"""

    def __init__(self, **over):
        self.fields = dict(
            datasource_id=ES_SRC_ID, label="cloud-platform-es",
            base_url="http://es-inner:9200", username="svc-es-readonly",
            password="s3cr3t-pass", api_key="", verify_certs=False,
            timeout_sec=5, max_rows=1000, allowed=("orders-*", "cases"),
            pool_size=es_client.DEFAULT_POOL_SIZE)
        self.fields.update(over)

    def patch(self, **over):
        self.fields.update(over)

    def ctx(self):
        return es_client.EsCtx(**self.fields)


@pytest.fixture
def es_config(monkeypatch):
    """把 ES 入口接上一条已登记的 ES 数据源（地址 + 开放范围齐备），返回可继续覆盖的目标。

    旧夹具往 `config.ES_*` 上打补丁（内置通道）。阶段三第 2 步把那组全局配置与
    `es_client.legacy_ctx()` 一并退役，所以这里直接构造一份 `EsCtx`（等同一条
    `db_type='es'` 的数据源行），并把 ES 入口统一注入它：用例不必逐个传 ctx，而闸门
    断言（开放范围/只读/LIMIT/不泄露物理信息）与迁移前逐字相同。
    """
    target = EsTarget()

    def make(fn, tgt=target):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def call(*args, **kwargs):
            # 只在调用方**没给** ctx 时注入：ES 入口之间会互相带 ctx 递归调用
            # （如 `run_sql` 把 scope 位置参递给 `parse_select`），无条件补 kwarg
            # 会报“多次传 ctx”，把闸门用例错当成功能失败。
            if "ctx" in sig.parameters and "ctx" not in sig.bind_partial(*args, **kwargs).arguments:
                kwargs["ctx"] = tgt.ctx()
            return fn(*args, **kwargs)
        return call

    for name in _ES_ENTRY_POINTS:
        monkeypatch.setattr(es_client, name, make(getattr(es_client, name)))
    return target


class _FakeResp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _install_transport(monkeypatch, responses):
    """替换 ES 的 HTTP 传输层，记录每次调用参数供断言。

    2026-11-13 起 `es_client` 走按 ctx 缓存的 `requests.Session` 连接池（`_session_for`），
    打 `requests.request` 已拦不住（会变成真去解析 es-inner 主机名，测试靠 DNS 失败“巧合通过”），
    所以必须打 session 工厂：`responses` 可为响应对象或按序号生成（也可抛异常模拟传输失败）。
    """
    calls = []

    class _FakeSession:
        def request(self, **kwargs):
            calls.append(kwargs)
            return responses(len(calls) - 1) if callable(responses) else responses

    monkeypatch.setattr(es_client, "_session_for", lambda ctx: _FakeSession())
    return calls


# ── 1. 白名单：数据面唯一闸门 ───────────────────────────────────────────

def test_empty_allowlist_opens_nothing(es_config):
    """空白名单必须拒绝一切 —— 不得当成"不限制"（与空角色授权不同口径）。"""
    es_config.patch(allowed=())
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


def test_limit_tightened_to_config_cap(es_config):
    es_config.patch(max_rows=50, allowed=("cases",))
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


def test_http_error_names_the_concrete_cause_by_type(es_config, monkeypatch):
    """ES 已把错因分类（error.type）：文案必须给方向，不能一律“请检查是否受支持”。

    现网反馈：Playground/数据集报 HTTP 400 时只看到“请检查查询是否受支持”，
    而 ES 其实回了 parsing_exception（语法不支持）或 verification_exception（列不存在）——
    两件事的修法完全不同，拿掉分类就是把排查方向抹平（护栏 §7 只禁止转述自由文本）。
    """
    _install_transport(monkeypatch, _FakeResp(
        {"error": {"type": "parsing_exception", "reason": "mismatched input '$' at [3:7]"}},
        status=400))
    with pytest.raises(es_client.EsError) as exc:
        es_client.run_sql("SELECT * FROM cases LIMIT 10")
    message = str(exc.value)
    assert "无派生表" in message or "不支持" in message
    assert "mismatched input" not in message          # reason 依旧不外泄
    assert exc.value.error_type == "parsing_exception"  # type: ignore[attr-defined]

    _install_transport(monkeypatch, _FakeResp(
        {"error": {"root_cause": [{"type": "verification_exception",
                                   "reason": "Unknown column [caseUidTypo]"}]}},
        status=400))
    with pytest.raises(es_client.EsError) as exc2:
        es_client.run_sql("SELECT caseUidTypo FROM cases LIMIT 10")
    assert "字段或索引" in str(exc2.value)
    assert "Unknown column" not in str(exc2.value)


def test_connection_error_hides_host(es_config, monkeypatch):
    def _boom(_idx):
        raise requests.exceptions.ConnectionError(
            "Failed to establish a new connection: es-inner:9200")

    _install_transport(monkeypatch, _boom)
    with pytest.raises(es_client.EsError) as exc:
        es_client.run_sql("SELECT * FROM cases LIMIT 10")
    assert "es-inner" not in str(exc.value) and "9200" not in str(exc.value)


def test_missing_configuration_fails_loud(es_config):
    """数据源缺地址就拒，不猜集群也不打空 URL。"""
    es_config.patch(base_url="")
    with pytest.raises(es_client.EsError):
        es_client.run_sql("SELECT * FROM cases LIMIT 10")


def test_api_key_sent_as_header_not_url(es_config, monkeypatch):
    es_config.patch(api_key="AK-XYZ", username="")
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
    def _boom(_idx):
        raise AssertionError("列开放范围不该发请求（展开是集群侧在查询时做的事）")

    _install_transport(monkeypatch, _boom)
    assert es_client.list_patterns() == ["orders-*", "cases"]


# ── 5. 内置 ES MCP 通道已退役（防“把裸路捡回来”的可证伪锁）────────────────

def test_builtin_registry_has_no_elasticsearch():
    """ES 已数据源化：内置注册表里不得再有 elasticsearch。

    留着代码条目而库里无实现行（或反之）就是“页面勾了、执行说没有”的空承诺；
    注册表为空 = 当前没有内置服务，机制保留。`catalog()` 是展示侧单一事实源，必须同步为空。
    """
    assert "elasticsearch" not in builtin_mcp.BUILTIN_MCP_SERVICES
    assert builtin_mcp.resolve("elasticsearch") is None
    assert builtin_mcp.catalog() == []


def test_es_tools_module_is_gone():
    """三个裸 SQL/元数据工具的模块必须物理删除：留着文件就留着“不经治理口直调 es_client”的旁路。"""
    assert importlib.util.find_spec(
        "backend.modules.mind.execution.sdk_tools.es_tools") is None


def test_builtin_es_row_seeder_script_is_gone():
    """往 `adh_mcp_servers` 种 9001 行的脚本同步删除：留着就能把死行重新塞回任何新环境。"""
    backend_root = Path(es_client.__file__).parents[1]   # .../backend
    repo_root = backend_root.parent
    assert not (backend_root / "scripts" / "migrate_es_builtin_mcp.py").exists()
    # 历史迁移不再插行（只保留表结构变更），否则新装库会出现“有服务行、无代码实现”
    seeder_sql = (repo_root / "docker" / "mysql" / "es_builtin_mcp_migration.sql").read_text()
    assert "INSERT INTO adh_mcp_servers" not in seeder_sql


def test_no_agent_tool_advertises_raw_es_sql():
    """工具目录口径：任何工具组都不得再广告 `query_es_sql` 这类裸 ES 入口。

    替代通路必须存在（ES SQL 走治理口，按 `db_type='es'` 分派，见 test_dataset_es_source /
    test_es_rls_injection），否则就是“拆了旧的没给新的”。
    """
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS

    names = {n for _server, ns in TOOL_SERVER_TOOLS.values() for n in ns}
    for retired in ("list_es_indexes", "describe_es_index", "query_es_sql"):
        assert retired not in names
    assert {"check_sql", "execute_sql"} <= names


def test_es_mcp_is_not_installable_from_market():
    """MCP 市场清单不得再提供可安装的 Elasticsearch 服务（旁路不能拆一边又开一边）。

    官方 `docker.elastic.co/mcp/elasticsearch` 安装后是一行 stdio 外部服务：AS-BOT 绑上就能
    拿 ES_URL/ES_API_KEY 直连集群，不查角色源授权、不套行/列策略、不落取数审计（护栏 §1）。
    市场页只接了浏览/详情/安装三个端点（create/update/delete_registry 无消费方），
    所以下架只能落迁移 `mcp_registry_es_retire_migration.sql`。同时不得有已安装的 ES 服务行。
    """
    from backend.common.db import execute_query

    assert execute_query(
        "SELECT COUNT(*) n FROM adh_mcp_registry "
        "WHERE LOWER(COALESCE(package_name,'')) LIKE '%elasticsearch%' "
        "OR name = 'Elasticsearch'", fetchone=True)["n"] == 0, \
        "迁移 mcp_registry_es_retire_migration.sql 未应用到当前库"
    assert not execute_query(
        "SELECT id, name FROM adh_mcp_servers "
        "WHERE LOWER(name) LIKE '%elastic%' OR LOWER(COALESCE(args,'')) LIKE '%elasticsearch%' "
        "OR LOWER(COALESCE(command,'')) LIKE '%elastic%'")


# ── 6. 内置 MCP 机制接线（注册表当前为空，用中性样例锁机制）──────────────

DEMO_TOOL = {"name": "demo_tool", "description": "机制样例工具",
             "schema": {"type": "object", "properties": {}},
             "handler": lambda args: {"content": []}}


def _demo_spec(ready: bool = True) -> dict:
    """内置服务契约的最小实现（label/description/tool_specs/state/probe 齐备）。

    真实内置服务已退役（注册表为空），但机制不得因此失去回归：将来登记新服务时，
    下面的用例就是它必须满足的口径。样例不接任何外部系统，`state` 只给结论与原因。
    """
    return {
        "label": "演示内置服务",
        "description": "只用于锁机制",
        "tool_specs": lambda: [DEMO_TOOL],
        "state": lambda cfg: {
            "ready": ready,
            "state": "ready" if ready else "disabled",
            "status_message": "" if ready else "未启用（部署侧开关关闭）",
            "open_view_count": 3 if ready else 0,
        },
        "probe": lambda: {"success": ready,
                          "message": "连接正常" if ready else "未启用"},
    }


@pytest.fixture
def demo_builtin(monkeypatch):
    monkeypatch.setattr(builtin_mcp, "BUILTIN_MCP_SERVICES", {"demo-builtin": _demo_spec()})
    return "demo-builtin"


def test_es_is_not_a_data_tool_group():
    """ES 不得同时存在于数据工具组（双口径）：它现在是数据源，不是工具组、也不是内置服务。"""
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS, TOOL_SERVER_BUILDERS

    assert "elasticsearch" not in TOOL_SERVER_TOOLS
    assert "elasticsearch" not in TOOL_SERVER_BUILDERS


def test_builtin_row_detection():
    assert builtin_mcp.is_builtin_row({"transport": "builtin"}) is True
    assert builtin_mcp.is_builtin_row({"is_builtin": 1}) is True
    assert builtin_mcp.is_builtin_row({"transport": "stdio"}) is False
    assert builtin_mcp.is_builtin_row({}) is False


def test_builtin_server_registers_under_authorized_prefix(demo_builtin):
    """注册前缀必须与 tool_policy 生成的 `mcp__external_{sid}__{tool}` 同源，否则勾了调不到。"""
    cfg = builtin_mcp.build_builtin_server(
        "qoder", {"name": demo_builtin}, "external_9101",
        "mcp__external_9101", ["demo_tool"])
    assert cfg["name"] == "external_9101"


def test_unregistered_builtin_row_rejected():
    """库里有内置行但代码没登记实现：显式拒，不能当成“没这个服务”静默跳过。"""
    with pytest.raises(PermissionError):
        builtin_mcp.build_builtin_server("qoder", {"name": "ghost"},
                                        "external_9", "mcp__external_9", ["x"])


def test_retired_builtin_row_is_rejected_by_name():
    """已退役的 elasticsearch 内置行走同一条 fail-loud 分支（不是“返空工具”）。

    退役迁移只软删服务行；若某环境漏跑迁移、或有人手建一行同名 builtin，绑上去必须报错，
    而不是“能绑上但模型永远拿不到工具”。
    """
    with pytest.raises(PermissionError) as exc:
        builtin_mcp.build_builtin_server("qoder", {"name": "elasticsearch"},
                                        "external_9001", "mcp__external_9001", ["query_es_sql"])
    assert "未登记实现" in str(exc.value)


def test_selected_builtin_tool_must_exist(demo_builtin):
    with pytest.raises(ValueError):
        builtin_mcp.build_builtin_server("qoder", {"name": demo_builtin},
                                        "external_9101", "mcp__external_9101", ["not_a_tool"])


def test_binding_not_ready_builtin_service_is_rejected(monkeypatch):
    """未就绪的内置服务不给注册：否则模型拿到工具只会反复报错（不假承诺）。"""
    from backend.modules.mind.execution.sdk_tools import external_tools

    row = {"id": 9101, "name": "demo-builtin", "transport": "builtin", "is_builtin": 1}
    policy = SimpleNamespace(as_bot={"mcp_server_ids": [9101]}, external={"9101": ["demo_tool"]})
    monkeypatch.setattr(external_tools, "execute_query", lambda *a, **k: dict(row))

    monkeypatch.setattr(builtin_mcp, "BUILTIN_MCP_SERVICES", {"demo-builtin": _demo_spec(ready=False)})
    with pytest.raises(PermissionError):
        external_tools.load_server(9101, policy)

    monkeypatch.setattr(builtin_mcp, "BUILTIN_MCP_SERVICES", {"demo-builtin": _demo_spec()})
    assert external_tools.load_server(9101, policy)["name"] == "demo-builtin"


def test_binding_unknown_builtin_impl_is_rejected(es_config, monkeypatch):
    from backend.modules.mind.execution.sdk_tools import external_tools

    row = {"id": 9002, "name": "ghost", "transport": "builtin", "is_builtin": 1}
    monkeypatch.setattr(external_tools, "execute_query", lambda *a, **k: dict(row))
    policy = SimpleNamespace(as_bot={"mcp_server_ids": [9002]}, external={"9002": ["x"]})
    with pytest.raises(PermissionError):
        external_tools.load_server(9002, policy)


# ── 7. 展示与写保护：内置行不回配置字段、不可改删 ────────────────────

BUILTIN_ROW = {"id": 9101, "name": "demo-builtin", "transport": "builtin", "is_builtin": 1,
               "description": "平台内置能力（配置由部署侧托管）", "is_active": 1,
               # 故意放入脏数据：历史误填或手工改库也不能把凭据带进页面
               "url": "http://stale-host:9200", "command": "npx internal-runner",
               "args": "--nodeps", "env": '{"PASSWORD": "s3cr3t-pass"}',
               # 旧清单：内置行的工具清单必须回源代码，不得拿这一列广告调不到的入口
               "tools_config": "{}",
               "discovered_tools": json.dumps(
                   [{"name": "ghost_tool", "description": "种子留下的旧清单"}])}

# 已退役服务的残留行（代码无实现）：投影必须是“不可用 + 可读原因”，旧工具清单要被洗掉
RETIRED_ROW = {"id": 9001, "name": "elasticsearch", "transport": "builtin", "is_builtin": 1,
               "description": "平台内置 Elasticsearch 只读查询能力", "is_active": 0,
               "url": "http://stale-es:9200", "command": "", "args": "",
               "env": '{"ES_PASSWORD": "s3cr3t-pass"}',
               "tools_config": "{}",
               "discovered_tools": json.dumps([{"name": n, "description": n} for n in (
                   "list_es_indexes", "describe_es_index", "query_es_sql")])}


def test_builtin_row_projection_strips_configuration(demo_builtin):
    from backend.modules.platform.api import mcp_servers as api

    out = api._project_builtin(dict(BUILTIN_ROW))
    for key in ("url", "command", "args", "env", "tools_config"):
        assert key not in out
    text = json.dumps(out, ensure_ascii=False)
    for leak in ("stale-host", "9200", "s3cr3t-pass", "npx"):
        assert leak not in text
    assert out["builtin"] is True and out["state"] == "ready" and out["is_active"] == 1
    assert out["label"] == "演示内置服务"
    # 工具清单回源代码：DB 列里的旧清单（ghost_tool）不得原样外渲
    assert [t["name"] for t in json.loads(out["discovered_tools"])] == ["demo_tool"]
    assert "ghost_tool" not in text


def test_retired_builtin_row_projects_as_unregistered():
    """库里残留的已退役内置行：不得投影成“就绪”，脏配置字段同样剥光（护栏 §7）。

    这是“页面看得见但永远用不了”的典型形态，状态与原因必须可读（不是 unknown 裸值）。
    """
    from backend.modules.platform.api import mcp_servers as api

    out = api._project_builtin(dict(RETIRED_ROW))
    assert out["state"] == "unknown" and out["is_active"] == 0
    assert "未登记实现" in out["status_message"]
    assert out["open_view_count"] == 0
    # 服务已退役 → 旧工具清单必须被洗成空：页面不能“提供工具 (3)”但三个都调不到
    assert json.loads(out["discovered_tools"]) == []
    text = json.dumps(out, ensure_ascii=False)
    for name in ("list_es_indexes", "describe_es_index", "query_es_sql"):
        assert name not in text
    for key in ("url", "command", "args", "env", "tools_config"):
        assert key not in out
    for leak in ("stale-es", "9200", "s3cr3t-pass"):
        assert leak not in text


def test_projection_marks_not_ready_service_inactive(monkeypatch):
    """未就绪的内置行对外 is_active=0：AS-BOT 候选按 is_active 过滤，绑不上也不会“绑了必错”。"""
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(builtin_mcp, "BUILTIN_MCP_SERVICES", {"demo-builtin": _demo_spec(ready=False)})
    out = api._project_builtin(dict(BUILTIN_ROW))
    assert out["is_active"] == 0 and out["state"] == "disabled"
    assert "未启用" in out["status_message"]


def test_catalog_projection_carries_no_configuration(demo_builtin):
    """展示侧单一事实源：catalog() 的结构里根本没有配置字段可泄。"""
    text = json.dumps(builtin_mcp.catalog(), ensure_ascii=False)
    for leak in ("stale-host", "9200", "s3cr3t-pass", "http://", "orders-*"):
        assert leak not in text
    item = builtin_mcp.catalog()[0]
    assert {t["name"] for t in item["tools"]} == {"demo_tool"}
    assert item["open_view_count"] == 3  # 只给数量，不给范围清单（属部署配置）


def test_empty_registry_catalog_is_empty_and_fails_loud_per_row():
    """注册表为空：展示侧给空清单（前端渲空态），但已存在的内置行仍按“未登记实现”拒。"""
    assert builtin_mcp.catalog() == []
    assert builtin_mcp.state_of("anything")["ready"] is False
    assert "未登记实现" in builtin_mcp.state_of("anything")["status_message"]


def test_builtin_test_endpoint_uses_code_probe_not_remote_connection(demo_builtin, monkeypatch):
    """内置行点「测试连接」只调代码登记的 probe，绝不建远程 MCP 连接（不读库里的 url/command）。

    probe 自己不得在“未就绪”时去碰外部系统：这是内置服务与部署配置之间的唯一硬约束。
    """
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query", lambda *a, **k: dict(BUILTIN_ROW))
    seen = {}
    monkeypatch.setattr(api, "update_builtin_test_result",
                        lambda sid, probe: seen.update(sid=sid, probe=probe))

    def _boom(*a, **kw):
        raise AssertionError("内置服务不得走远程连接发现流程")

    monkeypatch.setattr("backend.mcp_client.client.MCPClient", _boom)
    out = api.test_mcp_server(9101)
    assert seen["sid"] == 9101 and out == seen["probe"]
    assert out["success"] is True and "连接正常" in out["message"]


def test_builtin_test_endpoint_rejects_row_without_impl(monkeypatch):
    """无实现的内置行点测试：显式失败并给出原因，不假成功（no-silent-degradation）。"""
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query", lambda *a, **k: dict(RETIRED_ROW))
    seen = {}
    monkeypatch.setattr(api, "update_builtin_test_result",
                        lambda sid, probe: seen.update(sid=sid, probe=probe))

    def _boom(*a, **kw):
        raise AssertionError("已退役服务行不得去连集群")

    monkeypatch.setattr("backend.mcp_client.client.MCPClient", _boom)
    out = api.test_mcp_server(9001)
    assert out["success"] is False and "未登记实现" in out["message"]
    assert seen["probe"]["success"] is False


def test_builtin_test_writeback_keeps_tool_catalog(monkeypatch):
    """内置行的工具清单来自代码登记：测试失败也不得清空它，否则逐工具授权渲不出。"""
    from backend.modules.platform.api import mcp_servers as api

    captured = {}
    monkeypatch.setattr(api, "execute_write",
                        lambda sql, params=None: captured.update(sql=sql, params=params))
    api.update_builtin_test_result(9101, {"success": False, "message": "x" * 900})
    assert "discovered_tools" not in captured["sql"]
    assert "is_builtin=1" in captured["sql"]
    assert len(captured["params"][2]) <= 500  # 对齐列宽，不靠数据库报错发现


def test_builtin_row_cannot_be_edited_or_deleted(monkeypatch):
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query", lambda *a, **k: dict(BUILTIN_ROW))
    for verb in ("修改", "删除"):
        with pytest.raises(HTTPException) as exc:
            api._reject_builtin_mutation(9101, verb)
        assert exc.value.status_code == 409
        assert "系统维护" in exc.value.detail and "部署侧托管" in exc.value.detail


def test_builtin_row_mutation_allows_external_rows(monkeypatch):
    from backend.modules.platform.api import mcp_servers as api

    monkeypatch.setattr(api, "execute_query",
                        lambda *a, **k: {"id": 7, "name": "log-warehouse", "transport": "sse"})
    api._reject_builtin_mutation(7, "修改")  # 外部服务不受限


def test_page_cannot_create_fake_builtin_service():
    """内置服务只能由代码登记 + 迁移建行：允许页面新建 builtin = 允许伪造无实现入口。"""
    from backend.modules.platform.api import mcp_servers as api

    with pytest.raises(HTTPException) as exc:
        api.create_mcp_server(api.MCPServerCreate(name="fake-builtin", transport="builtin"))
    assert exc.value.status_code == 409
