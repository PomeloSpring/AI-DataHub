"""图重建的写入批量与总时长预算（离线，无 DB / 无 Oxigraph）。

要钉住的两个真实事故:
  1. ``create_column_node`` 每列发一次 SPARQL UPDATE。全目录重建
     （``_ds_scope(0)`` = 「聚合全部数据源」，不过滤）要写 1.5 万+ 列 →
     1.5 万次串行 HTTP 往返，表现为 ``ontology_service.save_draft`` 卡死
     数百秒不返回（实测 ``tests/test_binding_layer_separation.py`` 300s 超时；
     修完批量后同一文件 45s 跑完）。
  2. 没有总时长上界时，任何一次慢重建都只能靠"它总会结束吧"祈祷。

批量必须**等价**而不是更快：本文件同时断言三元组内容逐条还在（列节点 +
``hasColumn`` 边都在同一批里），因为丢数据比慢更糟。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.semantics.graph_rag.graph_builder import (
    GraphBuildAborted, GraphBuilder,
)
from backend.semantics.graph_rag.oxigraph_store import OxigraphStore  # noqa: E402


class _RecordingClient:
    """记录每次 SPARQL UPDATE 的请求，用于数往返次数与看内容。"""

    def __init__(self):
        self.updates: list[str] = []

    def update(self, sparql: str, prefix: bool = True, timeout=None) -> None:
        self.updates.append(sparql)

    # 批量路径不读这些，留空以防被触到时报清晰错误
    def __getattr__(self, name):
        raise AssertionError(f"不该被调用: {name}")


def _store() -> tuple[OxigraphStore, _RecordingClient]:
    cli = _RecordingClient()
    return OxigraphStore(client=cli), cli


class TestBatchedWrites:
    def test_many_columns_cost_one_round_trip(self):
        store, cli = _store()
        n = store.create_column_nodes(
            [{"table": "t_case_records", "column": f"c{i}"} for i in range(300)],
            datasource_id=1780478236183)
        assert n == 300
        assert len(cli.updates) == 1, "300 列必须一次往返（旧实现是 300 次）"

    def test_batch_content_keeps_node_and_hascolumn_triple(self):
        """批量不能只写节点不写边 —— 每个列都要带自己的 hasColumn。"""
        store, cli = _store()
        store.create_column_nodes(
            [{"table": "t_a", "column": "x"}, {"table": "t_a", "column": "y"},
             {"table": "t_b", "column": "z"}], datasource_id=0)
        body = cli.updates[0]
        # ADH_NS 在拼接时已展开成完整 IRI（不是字面量前缀 adh:）
        base = "http://ai-datahub.org/ontology/"
        for iri in ("col:t_a.x", "col:t_a.y", "col:t_b.z"):
            assert f"<{base}{iri}>" in body
        assert body.count("hasColumn") == 3
        assert body.count(f"a <{base}Column>") == 3

    def test_blank_rows_are_skipped_not_sent_as_garbage(self):
        """空白 table/column 必须被剔掉：写进图就是个 label 为空的垃圾节点。"""
        store, cli = _store()
        assert store.create_column_nodes(
            [{"table": "", "column": "x"}, {"table": "t", "column": "  "},
             {"table": "  ", "column": "y"}, {"table": "t", "column": "ok"}],
            datasource_id=0) == 1
        assert "col:t.ok" in cli.updates[0]
        assert "col:t.  " not in cli.updates[0]

    def test_empty_batch_sends_nothing(self):
        """空批发一个空 INSERT 会被引擎拒（且制造噪声往返）。"""
        store, cli = _store()
        assert store.create_column_nodes([], datasource_id=0) == 0
        assert store.create_table_nodes([], datasource_id=0) == 0
        assert cli.updates == []

    def test_single_node_helpers_delegate_to_batch(self):
        """create_table_node/create_column_node 保留原签名，避免调用方分叉。"""
        store, cli = _store()
        store.create_column_node(table="t_a", column="x", data_type="int",
                                 comment="备注", datasource_id=7)
        store.create_table_node(name="t_a", comment="c", business_desc="bd",
                                datasource_id=7)
        assert len(cli.updates) == 2
        assert "col:t_a.x" in cli.updates[0]
        assert "businessDesc" in cli.updates[1]
        # 命名图按 datasource 隔离，不能都落到 ds:0
        assert "ds:7" in cli.updates[0] and "ds:7" in cli.updates[1]

    def test_quotes_in_metadata_cannot_break_the_statement(self):
        """列注释里带引号/反斜杠是常态；批量拼接必须走同一个转义口径。"""
        store, cli = _store()
        store.create_column_nodes(
            [{"table": "t", "column": "c", "comment": '他说"含引号"\n换行'}],
            datasource_id=0)
        body = cli.updates[0]
        assert '\\"' in body and "\\n" in body
        assert body.count('"""') == 0


class TestBuildDeadline:
    def test_exceeding_budget_raises_with_progress(self, monkeypatch):
        monkeypatch.setenv("GRAPH_BUILD_DEADLINE_S", "1")
        # 预算 1s，而"已经过了 9999s" —— 用时间桩确定性触发，不等真实等待
        monkeypatch.setattr("backend.semantics.graph_rag.graph_builder.time.monotonic",
                            lambda: 9999.0)
        stats = {"tables": 12, "columns": 3400}
        with pytest.raises(GraphBuildAborted) as ei:
            GraphBuilder._check_deadline(0.0, stats, "columns", 3400, 15581)
        msg = str(ei.value)
        assert "columns" in msg and "3400/15581" in msg
        # 进度与预算必须带在异常对象上，供上层落可诊断结果而不是只留一句话
        assert ei.value.stats["columns"] == 3400
        assert ei.value.budget_s == 1.0

    def test_within_budget_is_silent(self, monkeypatch):
        monkeypatch.setenv("GRAPH_BUILD_DEADLINE_S", "600")
        monkeypatch.setattr("backend.semantics.graph_rag.graph_builder.time.monotonic",
                            lambda: 5.0)
        GraphBuilder._check_deadline(0.0, {}, "tables", 1, 1)

    def test_deadline_can_be_disabled_explicitly_only(self, monkeypatch):
        """<=0 是"我明确要关掉"，不是"配置读失败就当作关掉"。"""
        monkeypatch.setenv("GRAPH_BUILD_DEADLINE_S", "0")
        monkeypatch.setattr("backend.semantics.graph_rag.graph_builder.time.monotonic",
                            lambda: 10 ** 9)
        GraphBuilder._check_deadline(0.0, {}, "columns", 1, 1)

    def test_unparsable_budget_falls_back_to_default_not_off(self, monkeypatch):
        """环境变量写错时不能变成"没有上界" —— 那正好把防护关掉。"""
        monkeypatch.setenv("GRAPH_BUILD_DEADLINE_S", "abc")
        assert GraphBuilder._build_budget_s() == 600.0

    def test_chunk_size_guardrails(self, monkeypatch):
        monkeypatch.setenv("GRAPH_WRITE_CHUNK", "0")
        assert GraphBuilder._write_chunk() == 1, "0 会让 range step 报错，必须夹到 >=1"
        monkeypatch.setenv("GRAPH_WRITE_CHUNK", "junk")
        assert GraphBuilder._write_chunk() == 500


class TestFailureIsNotSilent:
    def test_abort_surfaces_as_unsuccessful_build_result(self, monkeypatch):
        """``build_from_metadata`` 把异常转成 success:False —— 这条路径必须仍然成立，
        且必须带上已写入的进度，否则"部分完成的图"看起来像成功。"""
        from backend.semantics.graph_rag import graph_builder as gb

        calls = {"n": 0}

        class _Boom:
            def clear_graph(self, datasource_id=0):
                return None

            def create_table_nodes(self, items, datasource_id=0, timeout=None):
                calls["n"] += 1
                raise GraphBuildAborted("图重建超出预算 1s（已进入 tables 阶段，已写 1/2）",
                                        stats={"tables": 1}, elapsed_s=9.0, budget_s=1.0)

        b = gb.GraphBuilder(store=_Boom())
        monkeypatch.setattr(b, "_merge_ontology_models", lambda ds, kind: 0)
        monkeypatch.setattr(b, "_load_tables", lambda ds: [{"table_name": "t"}])
        monkeypatch.setattr(b, "_load_columns", lambda ds: [])
        monkeypatch.setenv("GRAPH_BUILD_DEADLINE_S", "1")

        out = b.build_from_metadata(0, "business")
        assert out["success"] is False, out
        assert "超出预算" in out["error"]
        assert calls["n"] == 1
