"""多源知识检索（QMind 多库 + 百炼外部只读语料）的离线回归：不连库、不连 CLI、不出网。

这批的出错代价是「答案来源被绑定顺序决定」和「把没问到说成库里没有」，所以断言集中在：

- **first-hit-wins 已死**：两个库都有命中时必须都进结果；绑定顺序置换不得改变结果；
- **多库一次联合召回**：qmind CLI 的 `--nb a,b` 是云端 rerank，>5 库按 5 个一批拆分；
- **出处标题不再恒为空**（真缺陷：旧代码取不存在的 `title`/`source` 键）；`citations` 被消费；
- **黑盒**：`originUrl`/`sourceUri`/`notebook_id`/`index_id`/凭据 一律不进返回体；
- **跨源只吃名次**（RRF k=60）+ tie-break 三条 + 正文指纹去重并标注 `also_in`；
- **逐源状态可见**：ok/empty/failed/circuit_open/not_supported 五分，部分失败仍出结果但
  置 degraded；凭据缺失是 failed 而不是空列表；
- **绑定 fail-loud**：未接入类型（local/vector_db）绑定即拒并说清原因；
- **凭据纪律**：`source_config` 写入即拒、读取即掩、更新保留库内旧值；
- **熔断分键**：百炼抖动不得熔掉 qmind。
"""
import json

import pytest

from backend.modules.mind.rag import knowledge_retriever as kr
from backend.modules.mind.rag import qmind_retriever as qr
from backend.modules.mind.rag.providers import PROVIDERS, provider_for, supported_kb_types
from backend.modules.mind.rag.providers.bailian import BailianProvider, _norm_node
from backend.modules.mind.rag.providers.base import (CORPUS_ORIGIN_EXTERNAL,
                                                    CORPUS_ORIGIN_PLATFORM_SYNCED, RRF_K,
                                                    Capabilities, Chunk, ProviderResult,
                                                    SourceStatus, merge_by_rank)
from backend.modules.mind.rag.providers.qmind import QMindProvider


# ── 替身 ─────────────────────────────────────────────────────────────────────

def nb_chunk(nb, title, text, score, **over):
    """qmind CLI 的 chunk 形状（实测字段名：sourceTitle/notebookId/originUrl…）。

    故意带上**不得外发**的字段：用例若漏了黑盒断言，这里的值会直接泄漏出去。
    """
    row = {"content": text, "sourceTitle": title, "notebookTitle": f"库{nb}",
           "notebookId": nb, "sourceId": f"src-{nb}", "chunkId": f"c-{nb}",
           "tokenCount": 120, "score": score,
           "originUrl": "https://oss.example.com/presigned?sig=SECRET",
           "sourceUri": "notebook/sources/internal/path.md", "metadata": {"k": "v"}}
    row.update(over)
    return row


def cli_payload(chunks, citations=None):
    return {"chunks": chunks, "citations": citations or [], "reranked": True,
            "total": len(chunks)}


class FakeResponse:
    """requests 响应替身（百炼走 HTTPS，不出网就靠它）。"""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture
def no_breaker(monkeypatch):
    """熔断放行 + 记账空转（Redis 不在离线用例射程内）。"""
    monkeypatch.setattr(qr, "_breaker_allowed", lambda now=None: True)
    monkeypatch.setattr(qr, "_breaker_record", lambda success, now=None: None)
    monkeypatch.setattr(BailianProvider, "_breaker_allowed", lambda self: True)
    monkeypatch.setattr(BailianProvider, "_breaker_record", lambda self, success: None)


def use_cli(monkeypatch, responder):
    """替换 qmind CLI 子进程入口，记录 argv。responder(args) -> (data, err)。"""
    calls = []

    def fake(args):
        calls.append(list(args))
        return responder(args)

    monkeypatch.setattr(qr, "_run_cli_ex", fake)
    return calls


def fake_graphrag(monkeypatch):
    """本地元数据回退替身（回退行为本身不在本批范围，只需要它可被识别）。"""
    seen = {}

    def _fb(question, datasource_id, tagged):
        seen["tagged"] = tagged
        return {"chunks": [{"content": "本地元数据"}], "count": 1,
                "rag_source": "graphrag" + ("|qmind_fallback" if tagged else "")}

    monkeypatch.setattr(qr, "_fallback_graphrag", _fb)
    return seen


class FakeProvider:
    """按 kb_type 分派的 provider 替身：只负责给出 chunk 与逐源状态。"""

    def __init__(self, kb_type, corpus_origin, by_kb, fail_kb=()):
        self.kb_type = kb_type
        self.label = kb_type
        self.corpus_origin = corpus_origin
        self.capabilities = Capabilities(read=True,
                                         write=corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED)
        self.by_kb = by_kb                      # {库名: [正文...]}
        self.fail_kb = set(fail_kb)
        self.ordered = []                       # 实际被问到的库顺序

    def search(self, kbs, question, top_k, datasource_id=0):
        res = ProviderResult()
        for kb in kbs:
            name = str(kb.get("name") or "")
            self.ordered.append(name)
            if name in self.fail_kb:
                res.add_source(name, SourceStatus.FAILED, "模拟通道 500")
                continue
            texts = self.by_kb.get(name) or []
            for i, t in enumerate(texts):
                res.chunks.append(Chunk(
                    text=t, title=t, kb_name=name, provider=self.kb_type, rank=i,
                    score=1.0 / (i + 1),
                    doc_stale=False if self.corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED else None,
                    corpus_origin=self.corpus_origin,
                    ref={"notebook_id": f"nb-{name}", "index_id": f"ix-{name}"}))
            res.add_source(name, SourceStatus.OK if texts else SourceStatus.EMPTY)
        return res


def bind(monkeypatch, kbs):
    """绑定库清单替身（跳过 config_cache 与回源 SQL）。"""
    monkeypatch.setattr(kr, "_bound_kbs", lambda kb_ids: list(kbs))


def dispatch(monkeypatch, providers_by_type):
    monkeypatch.setattr(kr, "provider_for",
                        lambda kb: providers_by_type.get(str(kb.get("kb_type") or "")))


# ── A. qmind CLI 收敛 ───────────────────────────────────────────────────────

def test_retrieve_notebooks_batches_five_per_call(monkeypatch):
    """CLI 单次最多 5 个 notebook（`retrieve --help` 原文 maximum 5）：6 库必须拆 2 批。"""
    seen = []
    use_cli(monkeypatch, lambda args: (seen.append(list(args)) or (cli_payload([]), "")))
    out = qr.retrieve_notebooks([f"nb{i}" for i in range(6)], "官网域名", 5)

    assert len(seen) == 2, "多库被拆成逐库单问 = 每库多付一次 node 子进程启动"
    assert [a[2] for a in seen] == ["nb0,nb1,nb2,nb3,nb4", "nb5"], "批次切分不是 5 个一批"
    for a in seen:
        assert a[:2] == ["retrieve", "--nb"] and a[3:5] == ["-q", "官网域名"]
        assert "--max-results" in a and "--format" in a
    assert out["errors"] == []


def test_chunk_title_comes_from_source_title_and_urls_are_dropped(monkeypatch):
    """真缺陷回归：旧代码取 `title`/`source` 两个不存在的键 → 出处标题恒为空。

    同时黑盒断言：签名 URL 与对象存储路径不得进任何返回体（护栏 §7）。
    """
    use_cli(monkeypatch, lambda args: (cli_payload(
        [nb_chunk("nb-a", "站点全景.md", "默认分站呈现", 0.91)],
        [{"sourceTitle": "站点全景.md", "notebookTitle": "运营知识库",
          "originUrl": "https://oss/presigned?sig=SECRET"}]), ""))
    got = qr.retrieve_notebooks(["nb-a"], "官网域名", 5)

    assert got["chunks"][0]["title"] == "站点全景.md", "出处标题仍取空 → 剧本要求的标来源拿不到依据"
    assert got["chunks"][0]["_notebook_id"] == "nb-a", "服务端内部归属丢失（逐源状态与去重要吃它）"
    assert got["citations"] == [{"title": "站点全景.md", "knowledge_base": "运营知识库"}]

    blob = json.dumps([qr._drop_internal(c) for c in got["chunks"]] + got["citations"],
                      ensure_ascii=False)
    for leak in ("originUrl", "sourceUri", "presigned", "notebookId", "oss.example.com"):
        assert leak not in blob, f"内部标识/签名 URL 泄漏：{leak}"


def test_qmind_retrieve_no_longer_first_hit_wins(monkeypatch, no_breaker):
    """两个库都有命中时必须都进结果（旧实现问到一个就 return，后面的库永远不被问）。"""
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [
        {"id": 1, "name": "运营知识库", "notebook_id": "nb-a", "cfg": {}},
        {"id": 2, "name": "产品手册", "notebook_id": "nb-b", "cfg": {}}])
    calls = use_cli(monkeypatch, lambda args: (cli_payload([
        nb_chunk("nb-a", "站点全景.md", "官网是 example.com", 0.9),
        nb_chunk("nb-b", "部署手册.md", "部署在华东2", 0.8)]), ""))

    out = qr.qmind_retrieve("官网域名", 5, [1, 2])

    assert len(calls) == 1, "绑定多库又退回逐库单问"
    assert out["count"] == 2 and "部署在华东2" in json.dumps(out["chunks"], ensure_ascii=False)
    assert out["rag_source"] == "qmind"
    assert sorted(out["knowledge_bases"]) == ["产品手册", "运营知识库"]
    assert all(c["title"] for c in out["chunks"]), "命中项缺出处标题"
    blob = json.dumps(out, ensure_ascii=False)
    for leak in ("notebook_id", "nb-a", "nb-b", "originUrl", "sourceUri", "presigned"):
        assert leak not in blob, f"qmind_retrieve 返回体泄漏内部标识：{leak}"


def test_bound_kb_without_channel_falls_back_but_says_so(monkeypatch, no_breaker):
    """no-silent-degradation：绑了库但没有可问通道时，回退必须带 degraded + 原因 + 可分桶标签。

    旧实现这里 kbs 为空 → tagged=False，用户看到的是「正常知识答案」，实际是元数据投影。
    """
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [])
    seen = fake_graphrag(monkeypatch)

    out = qr.qmind_retrieve("销售额", 5, [9])

    assert out.get("degraded") is True and "qmind 通道" in out["degraded_reason"]
    assert seen["tagged"] is True, "回退未打 qmind_fallback 标签 → 评测分桶会把降级当成正常命中"
    assert "qmind_fallback" in out["rag_source"]


# ── C. 跨源名次归并 ─────────────────────────────────────────────────────────

def chunk(text, kb, provider, rank, corpus_origin=CORPUS_ORIGIN_PLATFORM_SYNCED, stale=False):
    return Chunk(text=text, title=text, kb_name=kb, provider=provider, rank=rank,
                 doc_stale=stale if corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED else None,
                 corpus_origin=corpus_origin)


def test_merge_uses_ranks_not_scores_and_fuses_multi_hit():
    """各家分数量级不可比：外部语料的 0.91 不得压过平台同步语料的 0.02。"""
    a = [chunk("平台同步命中", "运营库", "qmind", 0)]
    b = [chunk("外部语料命中", "产品手册", "bailian", 0, corpus_origin=CORPUS_ORIGIN_EXTERNAL)]
    merged = merge_by_rank([a, b], top_k=5)

    assert merged[0].text == "平台同步命中", "跨源排序被原始分带跑（应只看名次 + 归属 tie-break）"
    assert merged[1].text == "外部语料命中"

    # 同一正文被两个源命中 → RRF 分数相加，必须排到单源命中之前（去重后只剩一条）
    out = merge_by_rank([[chunk("平台同步命中", "运营库", "qmind", 0),
                          chunk("重复段落", "运营库", "qmind", 1)],
                         [chunk("重复段落", "产品手册", "bailian", 0)]], top_k=5)
    assert out[0].text == "重复段落" and len(out) == 2


def test_merge_tie_break_prefers_platform_synced_then_fresh():
    """同分时的裁决顺序：① 平台同步 > 外部；② 未过期 > 过期。"""
    same_rank = [[chunk("外部那份", "产品手册", "bailian", 0, corpus_origin=CORPUS_ORIGIN_EXTERNAL)],
                 [chunk("平台那份", "运营库", "qmind", 0)]]
    assert merge_by_rank(same_rank, 3)[0].kb_name == "运营库"

    fresh = chunk("还新鲜", "运营库", "qmind", 0, stale=False)
    stale = chunk("已过期", "另一库", "qmind", 0, stale=True)
    out = merge_by_rank([[fresh], [stale]], 3)      # 同 rank ⇒ 同 RRF 分
    assert out[0].text == "还新鲜", "同分未把过期那份降级（doc_stale 是 tie-break 第二档）"


def test_merge_dedupes_and_marks_also_in():
    """同文重复只保留权威那份，其余库名进 also_in（不静默丢弃），且不含自身。"""
    out = merge_by_rank([[chunk("同一段口径说明", "运营库", "qmind", 0)],
                         [chunk("同一段口径说明", "产品手册", "bailian", 1)]], 3)

    assert len(out) == 1 and out[0].kb_name == "运营库"
    assert out[0].also_in == ["产品手册"], "去重不得让另一份无声消失（用户要能知道两处都登记了）"
    assert RRF_K == 60


def test_merge_rewrites_merged_rank():
    """归并后的 rank 是全局名次（对外只作展示与审计），且按 top_k 截断。"""
    out = merge_by_rank([[chunk("甲", "A库", "qmind", 0), chunk("乙", "A库", "qmind", 1),
                         chunk("丙", "A库", "qmind", 2)]], 2)
    assert [c.text for c in out] == ["甲", "乙"] and [c.rank for c in out] == [0, 1]


# ── B. 统一入口：并行扇出 + 逐源状态 ────────────────────────────────────────

def test_knowledge_retrieve_hits_both_providers_and_exposes_sources(monkeypatch):
    qm = FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED, {"运营库": ["官网 example.com"]})
    bl = FakeProvider("cloud_rag", CORPUS_ORIGIN_EXTERNAL, {"手册库": ["部署在华东2"]})
    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}},
                       {"id": 2, "name": "手册库", "kb_type": "cloud_rag", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": qm, "cloud_rag": bl})

    out = kr.knowledge_retrieve("官网域名", 5, [1, 2], top_k=5)

    assert out["count"] == 2
    assert out["rag_source"] == "kb_multi", "多通道同时命中的归因必须可分桶"
    assert {s["kb"]: s["status"] for s in out["sources"]} == {
        "运营库": SourceStatus.OK, "手册库": SourceStatus.OK}
    assert {c["provider"] for c in out["chunks"]} == {"qmind", "cloud_rag"}
    assert out["citations"] and all(c["title"] for c in out["citations"])
    assert "degraded" not in out


def test_binding_order_permutation_does_not_change_results(monkeypatch):
    """锁死 first-hit-wins 的回潮：绑定顺序只是书写顺序，不是答案来源。"""
    rows = {"q1": {"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}},
            "q2": {"id": 2, "name": "运营库2", "kb_type": "qmind", "cfg": {}},
            "b1": {"id": 3, "name": "手册库", "kb_type": "cloud_rag", "cfg": {}}}

    def run(order):
        qm = FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED,
                          {"运营库": ["甲"], "运营库2": ["乙"]})
        bl = FakeProvider("cloud_rag", CORPUS_ORIGIN_EXTERNAL, {"手册库": ["丙"]})
        kbs = [rows[k] for k in order]
        bind(monkeypatch, kbs)
        dispatch(monkeypatch, {"qmind": qm, "cloud_rag": bl})
        out = kr.knowledge_retrieve("问题", 0, [r["id"] for r in kbs], top_k=5)
        return [c["content"] for c in out["chunks"]], sorted(s["kb"] for s in out["sources"])

    forward, backward = run(["q1", "q2", "b1"]), run(["b1", "q2", "q1"])
    assert forward[0] == backward[0], "结果次序被绑定顺序决定（用户看不到也调不了的那个顺序）"
    assert forward[1] == backward[1]


def test_partial_provider_failure_is_visible_not_silenced(monkeypatch):
    """百炼抖动但 qmind 有命中：照常出结果，同时 degraded + 该源原因（不得演成「库里没有」）。"""
    qm = FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED, {"运营库": ["官网 example.com"]})
    bl = FakeProvider("cloud_rag", CORPUS_ORIGIN_EXTERNAL, {"手册库": []}, fail_kb={"手册库"})
    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}},
                       {"id": 2, "name": "手册库", "kb_type": "cloud_rag", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": qm, "cloud_rag": bl})

    out = kr.knowledge_retrieve("官网域名", 5, [1, 2])

    assert out["count"] == 1 and out.get("degraded") is True
    assert "手册库" in out["degraded_reason"]
    failed = [s for s in out["sources"] if s["kb"] == "手册库"][0]
    assert failed["status"] == SourceStatus.FAILED and failed["error"]


def test_all_sources_empty_falls_back_tagged_and_lists_sources(monkeypatch):
    """云端都问过且确实没内容：回退本地元数据，rag_source 可区分、逐源状态是 empty。"""
    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED,
                                                 {"运营库": []})})
    seen = fake_graphrag(monkeypatch)

    out = kr.knowledge_retrieve("没登记的事", 5, [1])

    assert seen["tagged"] is True and "qmind_fallback" in out["rag_source"]
    assert [s["status"] for s in out["sources"]] == [SourceStatus.EMPTY]
    assert "degraded" not in out, "问过而库里没有 ≠ 降级，两者混淆会掩盖真故障"


def test_unbound_type_is_reported_not_dropped(monkeypatch):
    """绑了 local 库（本批无通道）：结果里必须照列 not_supported + degraded 原因。"""
    bind(monkeypatch, [{"id": 7, "name": "本地库", "kb_type": "local", "cfg": {}}])
    dispatch(monkeypatch, {})                    # 没有 provider 能接 local
    seen = fake_graphrag(monkeypatch)

    out = kr.knowledge_retrieve("销售额", 5, [7])

    assert seen["tagged"] is True and out.get("degraded") is True
    assert "没有已接入的检索通道" in out["degraded_reason"] and "本地库" in out["degraded_reason"]
    assert out["sources"][0]["status"] == SourceStatus.NOT_SUPPORTED


def test_provider_exception_does_not_kill_the_turn(monkeypatch):
    """provider 抛异常收口成「该源失败」（另一源仍可出结果）。"""
    class Boom(FakeProvider):
        def search(self, kbs, question, top_k, datasource_id=0):
            raise RuntimeError("node 子进程炸了")

    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}},
                       {"id": 2, "name": "手册库", "kb_type": "cloud_rag", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": Boom("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED, {}),
                           "cloud_rag": FakeProvider("cloud_rag", CORPUS_ORIGIN_EXTERNAL,
                                                     {"手册库": ["部署在华东2"]})})

    out = kr.knowledge_retrieve("部署在哪", 5, [1, 2])

    assert out["count"] == 1 and out.get("degraded") is True
    assert "node 子进程炸了" in out["degraded_reason"]


def test_system_scope_is_marked_on_every_branch(monkeypatch):
    """能力叠加：system_scope 只作标注供分桶归因，不改变检索与回退行为。"""
    bind(monkeypatch, [{"id": 1, "name": "系统库", "kb_type": "qmind", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED,
                                                 {"系统库": ["用量口径"]})})
    fake_graphrag(monkeypatch)

    assert kr.knowledge_retrieve("用量", 0, [1], system_scope=True)["system_scope"] is True
    assert "system_scope" not in kr.knowledge_retrieve("用量", 0, [1])


def test_result_carries_no_internal_identifiers(monkeypatch):
    """no_leak：对外形状只给业务可读出处；ref（notebook/index id）留在服务端。"""
    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}}])
    dispatch(monkeypatch, {"qmind": FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED,
                                                 {"运营库": ["官网 example.com"]})})
    fake_graphrag(monkeypatch)

    out = kr.knowledge_retrieve("官网", 5, [1])
    blob = json.dumps(out, ensure_ascii=False)

    for leak in ("nb-运营库", "ix-", "index_id", "notebook_id", "\"ref\""):
        assert leak not in blob, f"内部标识进了对外返回体：{leak}"
    assert out["chunks"][0]["knowledge_base"] == "运营库"


def test_outward_result_passes_outbound_guard(monkeypatch):
    """契约：检索结果必须原样过出站守卫（守卫与 RAG 投影不得各说各话）。

    2026-11-11 事故复盘：chunk 投影里的语料归属键当时叫 `provenance`，与语义层取数
    溯源同名，命中守卫的键名黑名单。守卫拒的是**整条工具结果**，于是"知识库只要有
    命中就一律答不出"，用户侧只剩一句道歉。上面的 no_leak 用例只查了几个字面量
    （index_id/notebook_id），查不出键名撞车，缺陷因此一路带到线上。这里改成直接调
    守卫本身：以后再加字段，撞名或带出凭据/SQL 形态会当场红，而不是等用户发现。
    """
    from backend.modules.mind.execution.outbound_guard import (assert_no_sensitive,
                                                              find_sensitive_hits)

    bind(monkeypatch, [{"id": 1, "name": "运营库", "kb_type": "qmind", "cfg": {}},
                       {"id": 2, "name": "手册库", "kb_type": "cloud_rag", "cfg": {}}])
    dispatch(monkeypatch, {
        "qmind": FakeProvider("qmind", CORPUS_ORIGIN_PLATFORM_SYNCED,
                              {"运营库": ["联耀医疗公司简介"]}),
        "cloud_rag": FakeProvider("cloud_rag", CORPUS_ORIGIN_EXTERNAL,
                                  {"手册库": ["部署流程"]}),
    })

    out = kr.knowledge_retrieve("公司简介", 5, [1, 2])
    blob = json.dumps(out, ensure_ascii=False)

    hits = find_sensitive_hits(blob)
    assert hits == [], f"对外结果命中出站守卫黑名单（整条检索会被拒发）：{hits}"
    wrapped = {"content": [{"type": "text", "text": blob}]}
    assert assert_no_sensitive(wrapped, "knowledge_search") is wrapped

    # 归属标签是模型判断口径权威的依据，改名不能把它弄丢（两处对外形状都要有）
    assert {c["corpus_origin"] for c in out["chunks"]} == {"platform_synced", "external"}
    assert out["citations"][0]["corpus_origin"] in {"platform_synced", "external"}


# ── D. 百炼只读外部语料 ─────────────────────────────────────────────────────

def test_bailian_capabilities_are_external_readonly_and_write_side_is_qmind_only():
    p = provider_for({"kb_type": "cloud_rag"})
    assert isinstance(p, BailianProvider)
    assert p.capabilities == Capabilities(read=True, write=False, doc_tree=False,
                                          freshness_stamp=False)
    assert p.corpus_origin == CORPUS_ORIGIN_EXTERNAL
    # 写侧目标类型只能由 capabilities 派生，不得各处手抄
    assert [x.kb_type for x in PROVIDERS if x.capabilities.write] == ["qmind"]


def test_bailian_missing_credential_reports_failed_not_empty(monkeypatch):
    """缺凭据是配置问题，不是「库里没有」：整通道如实 failed，不得回空列表冒充无结果。"""
    from backend.common import config as app_config

    monkeypatch.setattr(app_config, "DASHSCOPE_API_KEY", "", raising=False)
    called = []
    monkeypatch.setattr(BailianProvider, "_retrieve",
                        lambda self, *a, **k: called.append(1) or ([], ""))

    res = BailianProvider().search(
        [{"name": "手册库", "kb_type": "cloud_rag",
          "cfg": {"provider": "aliyun", "index_id": "ix-1"}}], "部署在哪", 5)

    assert res.chunks == [] and called == [], "没有凭据还去发起调用"
    assert res.sources[0]["status"] == SourceStatus.FAILED
    assert "DASHSCOPE_API_KEY" in res.sources[0]["error"]


def test_bailian_node_parsing_strips_signed_urls():
    """百炼 metadata 里 file_path/image_url 等是带过期时间的签名 URL（官方文档亦说无意义）。"""
    node = {"Text": "部署手册正文", "Score": 0.77,
            "Metadata": json.dumps({"title": "部署手册", "doc_name": "deploy.md",
                                    "file_path": "https://oss/presigned?sig=SECRET",
                                    "image_url": "https://oss/img?sig=SECRET",
                                    "audio_url": "https://oss/a?sig=SECRET"})}
    got = _norm_node(node)

    assert got["text"] == "部署手册正文" and got["title"] == "部署手册"
    blob = json.dumps(got, ensure_ascii=False)
    for leak in ("presigned", "file_path", "image_url", "audio_url", "SECRET"):
        assert leak not in blob


def test_bailian_search_calls_one_index_per_kb_and_marks_corpus_origin(monkeypatch):
    """单 index 一次调用（与 qmind 的多 nb 联合召回不同），跨库名次交上层 RRF。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        calls.append({"url": url, "body": json, "headers": headers})
        idx = json["index_id"]
        return FakeResponse({"nodes": [{"Text": f"第 {idx[-1]} 章 部署流程", "Score": 0.5,
                                        "Metadata": {"title": f"部署手册-{idx[-1]}"}}]})

    from backend.common import config as app_config

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(app_config, "DASHSCOPE_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(BailianProvider, "_breaker_allowed", lambda self: True)
    monkeypatch.setattr(BailianProvider, "_breaker_record", lambda self, success: None)

    res = BailianProvider().search(
        [{"name": "手册库", "kb_type": "cloud_rag", "cfg": {"provider": "aliyun", "index_id": "ix-1"}},
         {"name": "法规库", "kb_type": "cloud_rag", "cfg": {"provider": "aliyun", "index_id": "ix-2"}}],
        "怎么部署", 5)

    assert [c["body"]["index_id"] for c in calls] == ["ix-1", "ix-2"]
    assert all(c["headers"]["Authorization"] == "Bearer sk-test" for c in calls)
    assert all(c["url"].startswith("https://") for c in calls), "凭据走 HTTP 明文即泄漏"
    assert len(res.chunks) == 2
    assert [c.corpus_origin for c in res.chunks] == [CORPUS_ORIGIN_EXTERNAL] * 2
    assert all(c.doc_stale is None for c in res.chunks), "外部语料无版本戳，不得下新鲜度结论"
    for c in res.chunks:
        public = json.dumps(c.to_public(), ensure_ascii=False)
        assert "index_id" not in public and "ix-" not in public


def test_bailian_rejects_unsupported_vendor_with_reason():
    """cloud_rag 下的其他厂商（混元/千帆）配置可以存在，但必须显式回报未接入。"""
    res = BailianProvider().search(
        [{"name": "混元库", "kb_type": "cloud_rag",
          "cfg": {"provider": "tencent", "index_id": "ix-9"}}], "问题", 5)

    assert res.chunks == []
    assert res.sources[0]["status"] == SourceStatus.NOT_SUPPORTED
    assert "tencent" in res.sources[0]["error"]


def test_breaker_keys_are_namespaced_per_provider():
    """分键纪律：百炼抖动不得熔掉 qmind（全局单键正是这批改掉的缺陷）。"""
    assert qr._BREAKER_PREFIX == "chatbi:kb:qmind:breaker"
    assert BailianProvider._BREAKER_PREFIX == "chatbi:kb:bailian:breaker"


# ── E. 绑定校验与写侧边界 ───────────────────────────────────────────────────

def test_validate_knowledge_binding_rejects_unsupported_types(monkeypatch):
    from backend.modules.mind.execution import resource_guard as rg

    rows = [{"id": 1, "name": "运营库", "kb_type": "qmind"},
            {"id": 2, "name": "手册库", "kb_type": "cloud_rag"},
            {"id": 7, "name": "本地库", "kb_type": "local"},
            {"id": 8, "name": "向量库", "kb_type": "vector_db"}]
    monkeypatch.setattr(rg, "execute_query", lambda sql, params=None: [
        r for r in rows if r["id"] in set(params or ())])

    with pytest.raises(rg.ResourceScopeError) as err:
        rg.validate_knowledge_binding([1, 2, 7, 8])
    msg = str(err.value)
    assert "本地库" in msg and "向量库" in msg and "运营库" not in msg and "手册库" not in msg
    assert "绑了也不会被检索" in msg, "原因必须写清（fail-loud 杜绝「配了没用」的空承诺）"

    rg.validate_knowledge_binding([1, 2])          # 已接入类型不得被拒


def test_write_side_targets_only_platform_synced():
    """写侧（本体上云/内容中心同步）只走 qmind：外部只读语料不能被「顺手同步」。"""
    from backend.modules.platform.services import kb_docs_service as kb

    assert "qmind" in kb.EDITABLE_KB_TYPES and "cloud_rag" not in kb.EDITABLE_KB_TYPES
    readonly = kb._unsupported_reason("cloud_rag")
    unimplemented = kb._unsupported_reason("local")
    assert "外部只读语料" in readonly and "本批未接入" not in readonly, \
        "把有意只读说成未实现，会误导管理员去等一条不会来的通道"
    assert "本批未接入" in unimplemented and "外部只读" not in unimplemented


def test_source_config_secrets_rejected_on_write_and_masked_on_read():
    """存量安全债：`source_config` 的明文 api_key 曾原样回显给 admin 前端。"""
    from fastapi import HTTPException

    from backend.modules.mind.api import knowledge_bases as api

    with pytest.raises(HTTPException) as err:
        api._reject_incoming_secrets({"provider": "aliyun", "index_id": "ix",
                                      "api_key": "sk-should-not-be-saved"})
    assert err.value.status_code == 400 and "DASHSCOPE_API_KEY" in err.value.detail

    masked = api._mask_config({"provider": "aliyun", "index_id": "ix", "api_key": "sk-old"})
    assert masked["index_id"] == "ix" and "sk-old" not in json.dumps(masked)
    assert masked[api.SECRET_CONFIGURED_MARK] == ["api_key"]

    merged = api._merge_config_keeping_secrets(
        {"provider": "aliyun", "index_id": "ix", "api_key": "sk-old"},
        {"index_id": "ix-new", "api_key": "sk-injected"})
    assert merged["index_id"] == "ix-new" and merged["api_key"] == "sk-old", \
        "更新其他字段时既不得抹掉库内旧凭据，也不得接受入参注入"


def test_supported_kb_types_is_the_single_source_of_truth():
    """绑定校验、检索分派、前端过滤必须读同一份清单（否则「配置页能选、检索问不到」）。"""
    assert set(supported_kb_types()) == {p.kb_type for p in PROVIDERS}
    assert set(supported_kb_types()) == {"qmind", "cloud_rag"}


def test_prompt_knowledge_base_labels_come_from_registry():
    """提示词的归属标注必须与 provider 同源（手抄清单会与通道能力漂移）。"""
    from backend.modules.mind.execution.prompt_composer import _knowledge_base_lines

    lines = _knowledge_base_lines([{"name": "运营知识库", "kb_type": "qmind"},
                                   {"name": "产品手册", "kb_type": "cloud_rag"},
                                   {"name": "本地库", "kb_type": "local"}])

    assert "（平台同步语料）" in lines[0]
    assert "（外部只读语料）" in lines[1]
    assert "未接入" in lines[2] and "不得向用户承诺能查到内容" in lines[2], \
        "未接入的库被藏起来会被读成「库没了」，管理员以为不用管"


# ── provider 层逐源状态 ─────────────────────────────────────────────────────

def test_qmind_provider_restores_per_source_status(monkeypatch, no_breaker):
    """一次 `--nb a,b` 联合召回仍能按库归属回报（notebookId 留在服务端用于归属）。"""
    use_cli(monkeypatch, lambda args: (cli_payload([nb_chunk("nb-a", "站点全景.md", "甲", 0.9)]), ""))
    monkeypatch.setattr(qr, "_evaluate_doc_freshness",
                        lambda ds, chunks: {"doc_stale": False, "doc_version": "v1"})

    res = QMindProvider().search(
        [{"name": "运营库", "kb_type": "qmind", "notebook_id": "nb-a", "cfg": {}},
         {"name": "空库", "kb_type": "qmind", "notebook_id": "nb-b", "cfg": {}},
         {"name": "没绑库", "kb_type": "qmind", "notebook_id": "", "cfg": {}}], "官网", 5)

    statuses = {s["kb"]: s for s in res.sources}
    assert statuses["运营库"]["status"] == SourceStatus.OK
    assert statuses["空库"]["status"] == SourceStatus.EMPTY
    # 没绑 notebook 的库在**收集可问清单时**就被挡下（逐源状态不得依赖它进入云端调用）
    assert statuses["没绑库"]["status"] == SourceStatus.NOT_SUPPORTED
    assert "notebook" in statuses["没绑库"]["error"]
    assert res.chunks[0].corpus_origin == CORPUS_ORIGIN_PLATFORM_SYNCED
    assert res.chunks[0].ref["notebook_id"] == "nb-a", "服务端内部归属丢失"
    assert "notebook_id" not in json.dumps(res.chunks[0].to_public(), ensure_ascii=False)


def test_qmind_provider_reports_failure_instead_of_empty(monkeypatch, no_breaker):
    """CLI 失败必须进 sources=failed（旧实现把失败与「库里没有」合并成空结果再静默回退）。"""
    use_cli(monkeypatch, lambda args: (None, "AUTH_REQUIRED: token 无效"))

    res = QMindProvider().search(
        [{"name": "运营库", "kb_type": "qmind", "notebook_id": "nb-a", "cfg": {}}], "官网", 5)

    assert res.chunks == [] and res.sources[0]["status"] == SourceStatus.FAILED
    assert "AUTH_REQUIRED" in res.sources[0]["error"]


def test_retrieval_source_vocabulary_is_bucketable():
    """归因词汇与 `_rag_source_of` 同源（eval 按这些标签分桶，改一侧必须同步另一侧）。"""
    from backend.modules.mind.execution.sdk_tools.semantic_tools import _classify_retrieval_source

    assert _classify_retrieval_source("qmind", 3) == "qmind_hit"          # 旧基线值不重命名
    assert _classify_retrieval_source("cloud_rag", 3) == "bailian_hit"
    assert _classify_retrieval_source("kb_multi", 3) == "multi_source_hit"
    assert _classify_retrieval_source("graphrag|qmind_fallback", 3) == "local_hybrid_fallback"
    assert _classify_retrieval_source("none", 0) == "none"
