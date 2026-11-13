"""同参数查询去重（`backend/common/query_dedup` + ES 数据集外壳）的回归。

起因：`App·ScanPro 口扫软件` 的 4 张 KPI 卡打的是**同一条正文 + 同一组参数**，
单条实测 13s、ES 读预算 20s —— 连发 4 遍就把集群按在预算线上摩擦，间歇越界成「刷新失败」。
去重只解决重复，不改口径，所以本文件锁的是"重复被合并"与"该分开的绝不共用"两件事：

- 同 key 串行第二次走 memo、并发同 key 只真跑一次（其余 shared）；
- **套在现网真路径上**：`_query_es_on_source`（App 使用类数据集存的是 source_type=sql +
  ES 数据源）；内置通道 `_query_es` 与它的用例已随阶段三第 2 步退役，外壳本身不迁就两条路；
- **失败绝不记忆**（一次抖动不能被复用十几秒），错误原样回抛；
- 返回的是拷贝：调用方往结果里加字段（field_formats / query_dedup）不得污染备忘录；
- 键覆盖数据集版本、身份与 params 全量：不同用户、不同参数、不同钉定源、改过正文都必须回源；
- 翻页（search_after 游标）不参与去重；
- 命中不发 ES 请求，但**审计照旧落一行且写明 dedup**（护栏 §9，去重不许被藏起来）；
- TTL=0 是显式关闭开关（权限变更要即时生效时的口径）。
"""
import threading
import time

import pytest

from backend.common import config, query_dedup
from backend.modules.viz.services import dataset_service as svc


@pytest.fixture
def dedup_on(monkeypatch):
    """本文件要验的就是去重，所以显式把窗口打开（conftest 默认关掉）。"""
    monkeypatch.setattr(config, "ES_QUERY_DEDUP_TTL_SEC", 30, raising=False)
    query_dedup.clear()
    yield
    query_dedup.clear()


def _counter():
    calls = []

    def fn():
        calls.append(1)
        return {"columns": [{"name": "c"}], "rows": [[1]]}
    return calls, fn


class TestRunDedup:
    def test_serial_same_key_runs_once_and_returns_a_copy(self):
        calls, fn = _counter()
        first, src1 = query_dedup.run(("k",), fn, ttl=30)
        second, src2 = query_dedup.run(("k",), fn, ttl=30)
        assert (src1, src2) == ("ran", "memo")
        assert len(calls) == 1
        # 命中给的是拷贝：调用方加字段不得写进备忘录，也不得让后来者拿到"别人改过的"结果
        second["rows"].append([9])
        second["field_formats"] = {"c": "date"}
        third, _ = query_dedup.run(("k",), fn, ttl=30)
        assert third["rows"] == [[1]] and "field_formats" not in third

    def test_concurrent_same_key_executes_once(self):
        started = threading.Barrier(4)
        calls, lock = [], threading.Lock()
        done = []

        def fn():
            with lock:
                calls.append(1)
            time.sleep(0.05)              # 拉长在途窗口，让另外 3 个必然撞上
            return {"rows": [[1]]}

        def worker():
            started.wait()
            _, source = query_dedup.run(("k",), fn, ttl=30)
            done.append(source)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1
        assert done.count("ran") == 1 and done.count("shared") == 3

    def test_different_keys_never_share(self):
        calls, fn = _counter()
        query_dedup.run(("k", 1), fn, ttl=30)
        query_dedup.run(("k", 2), fn, ttl=30)          # 参数不同
        assert len(calls) == 2

    def test_failure_is_not_memoized_and_reraises(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] == 1:
                raise ValueError("ES 超时")
            return {"rows": [[2]]}

        with pytest.raises(ValueError):
            query_dedup.run(("k",), flaky, ttl=30)
        value, source = query_dedup.run(("k",), flaky, ttl=30)
        assert source == "ran" and state["n"] == 2 and value["rows"] == [[2]]

    def test_ttl_expiry_goes_back_to_source(self):
        calls, fn = _counter()
        query_dedup.run(("k",), fn, ttl=0.05)
        time.sleep(0.08)
        _, source = query_dedup.run(("k",), fn, ttl=30)
        assert source == "ran" and len(calls) == 2

    def test_ttl_zero_is_an_explicit_off_switch(self):
        calls, fn = _counter()
        query_dedup.run(("k",), fn, ttl=0)
        _, source = query_dedup.run(("k",), fn, ttl=0)
        assert source == "ran" and len(calls) == 2


IDENTITY = {"user_id": 1791022918037, "username": "analyst-01",
            "role": "analyst", "workspace_id": 1}


def _sql_es_ds(**over) -> dict:
    """挂在 ES 数据源上的 SQL 型数据集（现网 App 使用类就长这样，走 `_query_es_on_source`）。"""
    row = {"id": 901, "name": "App 使用·单 App 规模汇总", "status": "active",
           "source_type": "sql", "es_pattern": "", "datasource_id": 1794535612029,
           "sql_query": 'SELECT 1 FROM "t-t_software_app_operation_log*" LIMIT 10',
           "sql_bodies": "{}", "variables": "{}", "field_config": "[]",
           "updated_at": "2026-11-13 10:00:00", "available_datasources": "[]", "owner_id": 1}
    row.update(over)
    return row


class TestEsSourceBranchDedup:
    """钉住真路径：现网 ES 数据集走 `_query_es_on_source`，去重必须套在那一支上。

    历史缺陷：只包了内置通道 `_query_es`，导致 App 看板的 4 张 KPI 卡仍然并发全超时。
    """

    def _patch(self, monkeypatch, ds):
        hits, audits = [], []
        monkeypatch.setattr(svc, "get_dataset", lambda _id: ds)
        monkeypatch.setattr(svc, "_scope_filters", lambda *_a, **_kw: [])
        monkeypatch.setattr(svc, "resolve_effective_source", lambda *_a, **_kw: 1794535612029)
        monkeypatch.setattr(svc, "effective_source_name", lambda *_a, **_kw: "ES-生产")
        monkeypatch.setattr(svc, "_es_source_dataset", lambda _ds: True)
        monkeypatch.setattr(svc, "field_formats_for", lambda *_a, **_kw: {})
        monkeypatch.setattr(svc, "_es_audit", lambda *a, **kw: audits.append(kw))

        def fake_on_source(_ds, params, _scopes, _identity):
            hits.append(params)
            return {"columns": ["opens"], "rows": [[1]], "row_count": 1}
        monkeypatch.setattr(svc, "_query_es_on_source", fake_on_source)
        return hits, audits

    def test_four_cards_on_es_source_dataset_hit_once(self, monkeypatch, dedup_on):
        hits, audits = self._patch(monkeypatch, _sql_es_ds())
        shape = {"args": {"app_code": "ASSCANPRO"}, "limit": 10, "order": [], "filters": []}
        for _ in range(4):
            out = svc.query_dataset(901, dict(shape), IDENTITY)
        assert len(hits) == 1, "ES 数据源分支没被去重（真路径仍然 4 连发）"
        assert out.get("query_dedup") == "memo"
        assert audits and all("dedup" in str(a.get("reason")) for a in audits)

    def test_es_source_results_are_not_shared_across_users(self, monkeypatch, dedup_on):
        hits, _ = self._patch(monkeypatch, _sql_es_ds())
        shape = {"args": {"app_code": "CONNECT"}, "limit": 10}
        svc.query_dataset(901, dict(shape), IDENTITY)
        svc.query_dataset(901, dict(shape), dict(IDENTITY, user_id=1791022918038))
        assert len(hits) == 2
