"""数据源级高级配置（超时 / 最大行数 / 连接池）必须**被真实消费**的回归.

为什么单独一套用例：用户口径拍板「超时配置应该在数据源配置里配，不同源可以不一样，
还有连接池数量等高级配置」——这类"界面/表结构有了旋钮"的改动，真正的风险不是存不下，
而是**存了没人读**（配了没生效）与**多处各读一套**（同一源在不同路径预算不同）。
本文件把四件事钉住：

1. 唯一优先级：调用侧显式预算 > 数据源高级配置 > 代码缺省（`resolve_timeouts` 一处实现）；
2. ES 连接上下文（超时/最大行数/连接池/证书校验）只认数据源行，**不回读全局 .env**；
3. 连接池真实存在且被复用：`_request` 走 Session（不再每次新建连接），池大小进 adapter，
   数据源保存后 Session 被丢弃重建；
4. 校验 fail-loud：越界/非整数/未知键/给 SQL 源塞 ES 键一律拒（不静默丢弃也不"先存着"），
   脏 JSON 报错而不是按默认预算继续跑。

不连真实集群、不连真实元数据库：全部 monkeypatch。
"""
import json
import sys
import types

import pytest

from backend.common.db import datasource_db
from backend.core import es_client
from backend.modules.catalog.services import datasource_service as dsvc


# ── 1. 唯一优先级（L0 解析器）────────────────────────────────────────────
class TestResolveTimeouts:
    def test_未配置等于历史写死值(self):
        # 行为零变化是硬要求：没配高级配置的源必须与改前逐字一致
        assert datasource_db.resolve_timeouts({"host": "h"}) == (10, 30)

    def test_数据源配置被消费_JSON_字符串形态(self):
        row = {"extra_config": '{"connect_timeout_sec": 5, "read_timeout_sec": 90}'}
        assert datasource_db.resolve_timeouts(row) == (5, 90)

    def test_bytes_形态也能解析(self):
        row = {"extra_config": json.dumps({"read_timeout_sec": 45}).encode()}
        assert datasource_db.resolve_timeouts(row) == (10, 45)

    def test_调用侧显式预算优先于数据源配置(self):
        row = {"extra_config": {"read_timeout_sec": 90}}
        assert datasource_db.resolve_timeouts(row, timeout_sec=15) == (10, 15)

    def test_脏_JSON_报错不静默走默认(self):
        # 静默按默认预算跑 = "配了没生效"，正是本功能要消灭的形态
        with pytest.raises(ValueError, match="合法 JSON"):
            datasource_db.resolve_timeouts({"extra_config": "{坏"})


# ── 2. 校验层：只认有消费方的键，非法值拒 ────────────────────────────────
class TestExtraValidation:
    ES_KEEP = {"allowed_aliases": ["t_software_app_operation_log"], "allowed_index_patterns": []}

    def test_ES_连接池合法归一(self):
        out = dsvc._validated_es_extra("es", {**self.ES_KEEP, "pool_size": "16"})
        assert out["pool_size"] == 16

    @pytest.mark.parametrize("value", [0, -1, 999])
    def test_ES_连接池越界拒(self, value):
        with pytest.raises(ValueError, match="连接池数量"):
            dsvc._validated_es_extra("es", {**self.ES_KEEP, "pool_size": value})

    def test_ES_超时越界与文案带区间(self):
        with pytest.raises(ValueError, match="1~300"):
            dsvc._validated_es_extra("es", {**self.ES_KEEP, "timeout_sec": 301})

    def test_ES_非整数拒而不是静默取整失败(self):
        with pytest.raises(ValueError, match="整数"):
            dsvc._validated_es_extra("es", {**self.ES_KEEP, "pool_size": "十"})

    def test_未知键拒_防止写了不生效(self):
        # pool_sie 这种拼错如果静默入库，管理员会以为已经配了池大小
        with pytest.raises(ValueError, match="不认识这些配置项"):
            dsvc._validated_es_extra("es", {**self.ES_KEEP, "pool_sie": 8})

    def test_SQL_型只认两个超时(self):
        out = dsvc._validated_sql_extra("mysql", {"connect_timeout_sec": 5, "read_timeout_sec": 60})
        assert out == {"connect_timeout_sec": 5, "read_timeout_sec": 60}
        with pytest.raises(ValueError, match="不认识这些配置项"):
            dsvc._validated_sql_extra("mysql", {"timeout_sec": 30})   # ES 的键不得塞进 SQL 源

    def test_SQL_型查询超时上限比_ES_宽(self):
        # Doris/SLS 的大表扫描本就比交互式 ES 聚合慢，天花板不同是有意区分
        assert dsvc._validated_sql_extra("sls", {"read_timeout_sec": 600})["read_timeout_sec"] == 600
        with pytest.raises(ValueError, match="1~600"):
            dsvc._validated_sql_extra("sls", {"read_timeout_sec": 601})

    def test_没配任何东西时不落空配置行(self):
        assert dsvc._validated_sql_extra("mysql", {}) is None
        assert dsvc._validated_sql_extra("mysql", None) is None
        assert dsvc._validated_es_extra("mysql", {"timeout_sec": 3}) is None   # 非 ES 不代写 ES 字段

    def test_分派器与两侧同口径(self):
        assert dsvc._validated_extra("elasticsearch", dict(self.ES_KEEP))["allowed_aliases"] == \
            ["t_software_app_operation_log"]
        assert dsvc._validated_extra("doris", {"read_timeout_sec": 120}) == {"read_timeout_sec": 120}


# ── 3. ES 上下文只认数据源行 ────────────────────────────────────────────
def _es_row(extra):
    return {"id": 900, "name": "cloud-platform-es", "db_type": "es",
            "host": "es.invalid", "port": 9200, "ssl_mode": "required",
            "username": "elastic", "password": "pw", "extra_config": extra}


class TestEsCtxFromDatasource:
    @pytest.fixture(autouse=True)
    def _no_global_env_channel(self, monkeypatch):
        # 数据源级预算只能来自数据源行：旧内置通道那组 `config.ES_*` 已随阶段三第 2 步删除，
        # `es_client` 不再 import 全局配置（拿属性存在当断言：多一个入口就会变成“界面改了
        # 没反应、变量在 .env 里”的双口径）。
        assert not hasattr(es_client, "config"), "es_client 不得再读全局配置（内置通道已退役）"
        from backend.common import db
        monkeypatch.setattr(db, "get_datasource_by_id", lambda ds_id: _es_row(self.extra))

    extra = {"allowed_aliases": ["a_log"], "timeout_sec": 30, "max_rows": 2000,
             "pool_size": 24, "verify_certs": False}

    def test_三项预算都来自数据源行(self):
        ctx = es_client.ctx_for(900)
        assert (ctx.timeout_sec, ctx.max_rows, ctx.pool_size, ctx.verify_certs) == (30, 2000, 24, False)

    def test_未配的项走代码缺省而不是_env(self):
        self.extra = {"allowed_aliases": ["a_log"]}
        ctx = es_client.ctx_for(900)
        assert ctx.timeout_sec == es_client.DEFAULT_TIMEOUT_SEC
        assert ctx.max_rows == es_client.DEFAULT_MAX_ROWS
        assert ctx.pool_size == es_client.DEFAULT_POOL_SIZE

    def test_脏_extra_config_报错(self):
        self.extra = "{坏 JSON"
        with pytest.raises(es_client.EsError, match="合法 JSON"):
            es_client.ctx_for(900)


# ── 4. 连接池真实存在并被复用 ───────────────────────────────────────────
def _ctx(**over) -> es_client.EsCtx:
    base = dict(datasource_id=900, label="es", base_url="https://es.invalid:9200",
                username="u", password="p", api_key="", verify_certs=True,
                timeout_sec=30, max_rows=1000, allowed=("a_log",))
    base.update(over)
    return es_client.EsCtx(**base)


class TestHttpSessionPool:
    @pytest.fixture(autouse=True)
    def _clean_sessions(self):
        es_client.reset_sessions()
        yield
        es_client.reset_sessions()

    def test_同一目标复用同一会话(self):
        assert es_client._session_for(_ctx()) is es_client._session_for(_ctx())

    def test_池大小不同不共用会话(self):
        # 改了池大小必须重建，否则"配了没生效"
        a = es_client._session_for(_ctx(pool_size=4))
        b = es_client._session_for(_ctx(pool_size=32))
        assert a is not b
        assert a.adapters["https://"]._pool_maxsize == 4
        assert b.adapters["https://"]._pool_maxsize == 32

    def test_证书校验不同不共用会话(self):
        assert es_client._session_for(_ctx()) is not es_client._session_for(_ctx(verify_certs=False))

    def test_不静默重试(self):
        # 超时必须显式暴露（no-silent-degradation）：自动重发会把"查不动"伪装成"慢但出数"
        assert es_client._session_for(_ctx()).adapters["https://"].max_retries.total == 0

    def test_reset_后重建(self):
        first = es_client._session_for(_ctx())
        es_client.reset_sessions()
        assert es_client._session_for(_ctx()) is not first

    def test_取数走会话而不是裸_requests(self, monkeypatch):
        calls = {}

        class FakeSession:
            def request(self, **kw):
                calls.update(kw)
                raise es_client.EsError("停在这里就够了")

            def close(self):
                pass

        monkeypatch.setattr(es_client, "_session_for", lambda ctx: FakeSession())
        with pytest.raises(es_client.EsError):
            es_client._request("POST", "/_sql", json_body={"query": "SELECT 1 LIMIT 1"}, ctx=_ctx())
        assert calls["timeout"] == 30                      # 超时确实来自 ctx
        assert calls["url"].endswith("/_sql")

    def test_裸_requests_不得重新成为出口(self):
        """源码级护栏：`requests.request(` 一旦回到 `_request` 就是拾回“每请求新建连接”。

        行为用例只能证明“现在走了 Session”，证明不了“以后没人改回去”；
        同一思路见 `test_execute_sql_governance.py` 的裸连接旁路禁止。
        """
        import inspect

        src = inspect.getsource(es_client._request)
        assert "requests.request(" not in src, "ES 取数必须走连接池 Session"
        assert "_session_for(ctx).request(" in src


# ── 5. 三个连接器都消费数据源超时 ───────────────────────────────────────
class TestConnectorsConsumeTimeouts:
    def test_mysql_把两项超时传给驱动(self, monkeypatch):
        from backend.semantics.execution.connectors.mysql import MySQLConnector

        seen = {}

        def fake_conn(db_type, host, port, user, password, database, **kw):
            seen.update(kw)
            raise RuntimeError("到此为止")

        monkeypatch.setattr(datasource_db, "get_datasource_conn", fake_conn)
        monkeypatch.setattr(datasource_db, "get_datasource_by_id",
                            lambda ds_id: {"host": "h", "extra_config": {"read_timeout_sec": 88,
                                                                         "connect_timeout_sec": 6}})
        with pytest.raises(RuntimeError):
            MySQLConnector(datasource_id=1)._connect(None)
        assert (seen["read_timeout"], seen["connect_timeout"]) == (88, 6)

    def test_mysql_调用侧预算仍优先(self, monkeypatch):
        from backend.semantics.execution.connectors.mysql import MySQLConnector

        seen = {}
        monkeypatch.setattr(datasource_db, "get_datasource_conn",
                            lambda *a, **kw: seen.update(kw) or (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(datasource_db, "get_datasource_by_id",
                            lambda ds_id: {"host": "h", "extra_config": {"read_timeout_sec": 88}})
        with pytest.raises(RuntimeError):
            MySQLConnector(datasource_id=1)._connect(20)
        assert seen["read_timeout"] == 20

    def test_sls_不再忽略预算(self, monkeypatch):
        """历史缺陷：SLS 连接器根本不往下看 timeout_sec，只吃工厂默认 30s。"""
        from backend.semantics.execution.connectors.sls import SlsConnector

        seen = {}
        monkeypatch.setattr(datasource_db, "get_datasource_conn",
                            lambda *a, **kw: seen.update(kw) or (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(datasource_db, "get_datasource_by_id",
                            lambda ds_id: {"host": "h", "extra_config": {"read_timeout_sec": 120}})
        with pytest.raises(RuntimeError):
            SlsConnector(datasource_id=1)._connect(None)
        assert seen["read_timeout"] == 120
        assert seen["connect_timeout"] == datasource_db.DEFAULT_CONNECT_TIMEOUT_SEC

    def test_postgres_超时进_uri_而不是只进_kwargs(self, monkeypatch):
        """ADBC 只从 URI 解析参数（传 kwargs 会被驱动拒），所以两项都必须出现在 URI 里。"""
        from backend.semantics.execution.connectors.postgres import PostgresConnector

        uris = []
        module = types.ModuleType("adbc_driver_postgresql")
        dbapi = types.ModuleType("adbc_driver_postgresql.dbapi")
        dbapi.connect = lambda uri, **kw: uris.append(uri) or object()
        module.dbapi = dbapi
        monkeypatch.setitem(sys.modules, "adbc_driver_postgresql", module)
        monkeypatch.setitem(sys.modules, "adbc_driver_postgresql.dbapi", dbapi)
        PostgresConnector(config={"host": "h", "port": 5432, "user": "u", "password": "p",
                                  "database": "d",
                                  "extra_config": {"read_timeout_sec": 90, "connect_timeout_sec": 7}}
                          )._connect(None)
        assert "connect_timeout=7" in uris[0]
        assert "statement_timeout%3D90000" in uris[0]        # 90s 以毫秒下发（URL 编码后的 = ）
