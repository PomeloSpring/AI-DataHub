"""数据源连接的查询上界（跨境 SLS「看板查询超时」根因回归）。

锁定的契约：
  - 走 PG 协议(含 SLS)的连接此前只设 connect_timeout，握手后查询阶段**无任何上界**：
    跨境源(新加坡 arms-prd-en)抖动时调用无限挂在读上，前端 abort 也不释放连接 → 越重试越堆。
    现在必须用 libpq `options=-c statement_timeout=…` 下推服务端上界（实机探测 SLS 端点接受），
    并对 read_timeout 负责（query_executor.get_connection 传 60 → statement_timeout=60000ms）。
  - keepalives 一并打开，用于探测被链路(NAT/防火墙)静默断开的半死 socket。
  - MySQL 分支保持原样：用 socket read_timeout，不得被塞进 PG 的 options。

全程离线：只 monkeypatch 驱动 connect 捕获 kwargs，不触真实数据源。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.common.db import datasource_db as dd  # noqa: E402


@pytest.fixture
def cap_pg(monkeypatch):
    captured = {}

    class _FakeConn:
        def close(self):
            pass

    def fake_connect(**kw):
        captured.update(kw)
        return _FakeConn()

    monkeypatch.setattr(dd.psycopg2, "connect", fake_connect)
    return captured


@pytest.mark.skipif(not dd.HAS_PSYCOPG2, reason="psycopg2 not installed")
def test_sls_conn_bounds_query_via_statement_timeout(cap_pg):
    dd.get_datasource_conn("sls", "ap-southeast-1.log.aliyuncs.com", 5432,
                           "u", "p", "proj-xtrace-en", read_timeout=60)
    assert cap_pg["connect_timeout"] == 10
    # read_timeout 被真正下推成服务端上界（此前对 PG/SLS 完全无效）
    assert "statement_timeout=60000" in cap_pg["options"]
    # 探测半死 socket：keepalives 必须打开
    assert cap_pg.get("keepalives") == 1
    assert cap_pg.get("keepalives_idle", 0) >= 5


@pytest.mark.skipif(not dd.HAS_PSYCOPG2, reason="psycopg2 not installed")
def test_pg_conn_default_read_timeout_still_bounds(cap_pg):
    # 未显式传 read_timeout 时也按默认 30s 兜底，杜绝"无限挂"
    dd.get_datasource_conn("postgres", "h", 5432, "u", "p", "db")
    assert "statement_timeout=30000" in cap_pg["options"]


def test_mysql_conn_uses_socket_read_timeout_not_statement_timeout(monkeypatch):
    captured = {}

    class _FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(dd.pymysql, "connect",
                        lambda **kw: (captured.update(kw), _FakeConn())[1])
    dd.get_datasource_conn("mysql", "h", 3306, "u", "p", "db", read_timeout=60)
    # MySQL 走 socket read_timeout，不得被误塞 PG 的 options/statement_timeout
    assert captured["read_timeout"] == 60
    assert "options" not in captured
