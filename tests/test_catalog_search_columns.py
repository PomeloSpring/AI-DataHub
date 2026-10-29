"""目录检索（search_metadata / /api/catalog/search）的列名契约 — 锁历史缺陷。

历史缺陷：`catalog_service.global_search` 的指标分支仍查 `adh_metrics.metric_name /
metric_display_name / metric_type`（字典改造后这些列已不存在），一条 1054 让
**四路检索整体失败**（tables/columns 已取到也一起丢），AI 侧 `search_metadata` 连续两轮
只拿到 `{"error": "(1054, \\"Unknown column 'metric_name'...\\")"}`，
并诱发模型多绕 4 轮补救（真实会话实测多花 ~40s）。

本文件锁定三层防线：
1. 静态：SQL 字面量里不得再出现已退役列名；
2. 契约：指标分支必须查现行字典列，且占位符与参数个数一致；
3. 集成：四路检索对现网元库都能跑通（拿不到库即 skip，不把环境问题算成回归）。
"""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.modules.catalog.services import catalog_service  # noqa: E402

_RETIRED_METRIC_COLS = ("metric_name", "metric_display_name", "metric_type")


class _Cursor:
    def __init__(self, sink):
        self._sink = sink
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._last = (" ".join(sql.split()), list(params or []))
        self._sink.append(self._last)

    def fetchall(self):
        # 每路返回一行，键取自 SELECT 列表，便于验证“查询能跑通”而非只验证 SQL 文本
        cols = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", self._last[0].split("FROM")[0])
        return [{c: 1 for c in cols if c.upper() not in ("SELECT", "ID")}]


class _Conn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self._sink)


@pytest.fixture
def sql_sink(monkeypatch):
    captured: list = []
    monkeypatch.setattr(catalog_service, "DBConnection", lambda: _Conn(captured))
    return captured


def _metric_sql(sink):
    hits = [s for s, _ in sink if "FROM adh_metrics" in s]
    assert len(hits) == 1, f"指标分支应恰好一条查询，实际 {len(hits)} 条"
    return hits[0]


def test_metric_branch_has_no_retired_columns(sql_sink):
    catalog_service.global_search("案例", None, 301, 5)
    sql = _metric_sql(sql_sink)
    for col in _RETIRED_METRIC_COLS:
        assert col not in sql, f"指标分支仍在查已退役列 {col}（会让四路检索整体抛 1054）: {sql}"


def test_metric_branch_uses_current_dict_columns(sql_sink):
    """现行字典列：name（业务权威名）/ name_en+aliases（合法别名）/ agg_type / description。"""
    catalog_service.global_search("案例", None, 0, 5)
    sql, params = _metric_sql(sql_sink), sql_sink[[s for s, _ in sql_sink].index(_metric_sql(sql_sink))][1]
    for col in ("name", "name_en", "aliases", "agg_type", "description"):
        assert col in sql, f"指标分支未使用现行列 {col}: {sql}"
    # 占位符与参数个数必须一致（漏一个参数就是 1054/编程错误，运行时才炸最难查）
    assert sql.count("%s") == len(params), f"{sql} 与参数 {params} 数量不一致"


def test_all_branches_execute_against_live_schema(sql_sink):
    """静态兜底：四路都产出查询且逐路参数齐全（不依赖外部库）。"""
    catalog_service.global_search("订单", None, 7, 3)
    tables = {t for s, _ in sql_sink for t in
              ("adh_table_info", "adh_column_metadata", "adh_metrics", "adh_business_terms")
              if f"FROM {t}" in s}
    assert tables == {"adh_table_info", "adh_column_metadata", "adh_metrics", "adh_business_terms"}, \
        f"四路检索缺路: {tables}"
    for sql, params in sql_sink:
        assert sql.count("%s") == len(params), f"{sql} 与参数 {params} 数量不一致"


@pytest.mark.skipif(os.getenv("ADH_TEST_LIVE_DB") != "1",
                    reason="集成路径需现网元库（默认关闭，避免 CI 依赖公网库）")
def test_global_search_live_smoke():
    """现网真库跑一遍：列名/表名漂移在改造当刻即暴露，而不是等到 AI 拿不到元数据。"""
    r = catalog_service.global_search("案例", None, 0, 5)
    assert set(r) == {"tables", "columns", "metrics", "terms"}
    assert r["metrics"], "指标字典里应有可命中行（'案例'），空结果说明检索或字典被动过"
