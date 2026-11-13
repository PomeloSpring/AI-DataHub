"""测试期公共夹具：把"同参数查询去重"默认关掉。

为什么必须默认关：`backend/common/query_dedup` 是进程内备忘录，pytest 整个会话共用一个进程。
不去掉的话，上一个用例 monkeypatch 出来的 `es_client.run_sql` 结果会被下一个用例当成
"本次查到的数"复用 —— 测试会绿得毫无意义（而真实故障恰好藏在"以为跑了"里）。

需要验证去重本身的用例，在自己文件里显式把 TTL 打开（见 tests/test_es_query_dedup.py）。
"""
import pytest

from backend.common import config, query_dedup


@pytest.fixture(autouse=True)
def _no_query_dedup_between_tests(monkeypatch):
    """每个用例都从空备忘录开始，并且默认不去重。"""
    query_dedup.clear()
    monkeypatch.setattr(config, "ES_QUERY_DEDUP_TTL_SEC", 0, raising=False)
    yield
    query_dedup.clear()
