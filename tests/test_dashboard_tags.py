"""看板标签(tags)持久化契约的离线回归(不触真库)。

锁定：
  - create_dashboard 的 INSERT 必带 `tags` 列，值为**归一后的 JSON 数组**(去空/去重/strip)；
  - update_dashboard 传 tags → 只 patch `tags` 且值归一；传 [] → 显式清空(JSON 列存 [])，
    不能被 exclude_none 之类当成"没传"而跳过；不传 tags → 绝不写入该列(局部 patch 语义)；
  - _clean_tags 归一：接受 JSON 字符串/列表/None/嵌套脏值 → 干净字符串数组；
  - update_dashboard 成功后必须失效该看板详情载荷(DETAIL_KEY)，否则 get_dashboard 最长一个 TTL 读到旧 tags。
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")


class FakeCursor:
    def __init__(self, log, rowcount=1):
        self._log = log
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def close(self):
        pass


def _install(monkeypatch, log, rowcount=1):
    cur = FakeCursor(log, rowcount=rowcount)
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: FakeConn(cur))
    # 缓存失效是旁路，测试里只记录不触真缓存
    monkeypatch.setattr(dash, "_invalidate_dashboard_cache", lambda *_: None)
    payload_calls = []
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload",
                        lambda *a, **k: payload_calls.append(a))
    return payload_calls


def test_create_dashboard_inserts_normalized_tags(monkeypatch):
    log = []
    _install(monkeypatch, log)
    svc = dash.DashboardService()
    svc.create_dashboard({"name": "x", "tags": ["经营", " 日度 ", "经营", "", None]}, 1)
    sql, params = log[0]
    assert "`tags`" in sql
    # 归一：去空、去重、strip → ["经营", "日度"]，按 JSON 串落库
    assert json.dumps(["经营", "日度"]) in list(params)


def test_update_dashboard_patches_only_tags_when_given(monkeypatch):
    log = []
    payload_calls = _install(monkeypatch, log, rowcount=1)
    svc = dash.DashboardService()
    assert svc.update_dashboard(101, {"tags": ["周报", "周报 "]}, 1) is True
    sql, params = log[0]
    assert "`tags` = %s" in sql
    # 只写 tags + updated_at，不得顺带把 name/description/status 写空
    assert "name = %s" not in sql and "`status` = %s" not in sql
    assert json.dumps(["周报"]) in list(params)
    # 详情载荷必须按 dashboard_id 失效（否则 get_dashboard 读到旧 tags）
    assert payload_calls and payload_calls[0][0] == 101


def test_update_dashboard_empty_tags_clears_not_skips(monkeypatch):
    log = []
    _install(monkeypatch, log, rowcount=1)
    dash.DashboardService().update_dashboard(101, {"tags": []}, 1)
    sql, params = log[0]
    assert "`tags` = %s" in sql
    assert json.dumps([]) in list(params)   # 显式清空, 存 "[]"


def test_update_dashboard_without_tags_does_not_touch_column(monkeypatch):
    log = []
    _install(monkeypatch, log, rowcount=1)
    dash.DashboardService().update_dashboard(101, {"name": "改名"}, 1)
    sql, _ = log[0]
    assert "`tags`" not in sql


def test_clean_tags_normalizes_various_shapes():
    assert dash._clean_tags('["a", " a ", "", "b"]') == ["a", "b"]
    assert dash._clean_tags(["x", 1, "  "]) == ["x", "1"]
    assert dash._clean_tags(None) == []
    assert dash._clean_tags("") == []
    assert dash._clean_tags("裸串") == ["裸串"]
