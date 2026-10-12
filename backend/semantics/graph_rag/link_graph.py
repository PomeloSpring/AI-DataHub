"""Oxigraph 关系图读取（工作流 C 共用）：reified adh:Link 关系邻接 + label/altLabel 实体对齐。

图谱投影契约（backend/common/rdf/ontology_to_rdf._emit_link_instance）：
  adh:obj:{key}  adh:linkedTo  adh:obj:{key2}          ← 可遍历边（属性路径用）
  adh:link_{src}_{type}_{tgt}  a adh:Link ;
      adh:linkFrom / adh:linkTo / adh:joinExpr / adh:cardinality / adh:linkType
  类节点带 rdfs:label / skos:altLabel，供"客户 -> customer"实体对齐。

消费方：
  - planner._load_object_links（C1）：run_semantic_query 跨对象 join 链发现；
  - semantic_tools.get_metrics / knowledge_search（C2/C3）：关系上下文展开。

fail-loud：图不可达/查询失败一律抛 RuntimeError（或返回显式降级标注），
不静默回退其他关系源（no-silent-degradation）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
_SKOS_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"


def _iri_key(iri) -> str:
    """类 IRI → 对象 key（剥命名空间与 obj: 前缀）；非 obj: IRI 返回空。"""
    s = str(iri or "").strip()
    if not s:
        return ""
    s = s.rstrip(">") if s.startswith("<") else s
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    if ":" not in s:
        return ""          # 无前缀段（如 reified link_ 节点）不作为对象端点
    prefix, _, rest = s.partition(":")
    if prefix != "obj":
        return ""        # 只认对象类节点（table:/col: 等不参与对齐/邻接）
    return rest.strip()


def _ref_key(s: str) -> str:
    """限定名对象引用归一：去空白/分隔符，保留中文（与 planner._ref_key 同口径）。"""
    return re.sub(r"[\s_\-]+", "", str(s or "").strip().lower())


def _client():
    from backend.common.rdf.sparql_client import get_sparql_client
    client = get_sparql_client()
    if not client.health():
        raise RuntimeError("知识图谱(Oxigraph)不可达，关系无法解析（fail-loud，不回退其他关系源）")
    return client


def load_link_graph() -> dict:
    """全量关系图快照：{"edges": {obj_key: [(obj_key, joinExpr)]}, "names": {ref_key: obj_key}}。

    names 覆盖 key/display_name/aliases（label + altLabel 投影），供限定名实体对齐。
    """
    client = _client()
    try:
        # 教训（同 check_alias_drift 历史误报）：三元组在命名图 ds:<id> 里，
        # 必须 GRAPH ?g 全命名图扫描，查默认图必得 0 条。
        labels = client.query(
            f"SELECT ?s ?o WHERE {{ GRAPH ?g {{ ?s <{_RDFS_LABEL}> ?o }} }}") or []
        alts = client.query(
            f"SELECT ?s ?o WHERE {{ GRAPH ?g {{ ?s <{_SKOS_ALT}> ?o }} }}") or []
        # client 默认注入 SPARQL_PREFIXES(adh: <http://ai-datahub.org/ontology/>)
        links = client.query(
            "SELECT ?f ?t ?j WHERE { GRAPH ?g { "
            "?l a adh:Link ; adh:linkFrom ?f ; adh:linkTo ?t ; adh:joinExpr ?j } }") or []
    except Exception as e:  # noqa: BLE001 — 图查询失败必须显式暴露
        logger.error("[link_graph] load_link_graph 失败（fail-loud）: %s", e)
        raise RuntimeError(f"知识图谱查询失败，关系无法解析（fail-loud）: {e}") from e

    names: dict[str, str] = {}
    for row in labels + alts:
        key = _iri_key(row.get("s"))
        if not key:
            continue
        for ref in (key, row.get("o")):
            rk = _ref_key(ref)
            if rk:
                names.setdefault(rk, key)

    edges: dict[str, list[tuple[str, str]]] = {}
    for row in links:
        f_key, t_key = _iri_key(row.get("f")), _iri_key(row.get("t"))
        join = str(row.get("j") or "").strip()
        if f_key and t_key:
            # join 可空：语义关系（站点/产品类等路由索引层）无物理 join 表达式，
            # 仍属图关系（检索/关系上下文用）；planner 只把非空 join 当 SQL 链。
            edges.setdefault(f_key, []).append((t_key, join))
    logger.info("[link_graph] load_link_graph: 对齐名 %d 个, 关系边 %d 条(labels=%d alts=%d links=%d)",
                len(names), sum(len(v) for v in edges.values()),
                len(labels), len(alts), len(links))
    return {"edges": edges, "names": names}


def object_links(keys: list[str] | None = None) -> list[dict]:
    """对象关系上下文（C2/C3）：[{source, target, type, cardinality, join}]（业务名可读）。

    keys 为空 = 全部关系；失败抛 RuntimeError（调用方若容忍降级必须显式标注）。
    """
    client = _client()
    try:
        rows = client.query(
            "SELECT ?f ?t ?ty ?c ?j WHERE { GRAPH ?g { "
            "?l a adh:Link ; adh:linkFrom ?f ; adh:linkTo ?t ; adh:joinExpr ?j . "
            "OPTIONAL { ?l adh:linkType ?ty } "
            "OPTIONAL { ?l adh:cardinality ?c } } }") or []
    except Exception as e:  # noqa: BLE001
        logger.error("[link_graph] object_links 失败（fail-loud）: %s", e)
        raise RuntimeError(f"知识图谱查询失败，关系上下文不可用（fail-loud）: {e}") from e

    wanted = {str(k or "").strip() for k in (keys or []) if str(k or "").strip()}
    out: list[dict] = []
    for row in rows:
        f_key, t_key = _iri_key(row.get("f")), _iri_key(row.get("t"))
        if not f_key or not t_key:
            continue
        if wanted and f_key not in wanted and t_key not in wanted:
            continue
        out.append({
            "source": f_key, "target": t_key,
            "type": str(row.get("ty") or "linked").strip(),
            "cardinality": str(row.get("c") or "").strip(),
            "join": str(row.get("j") or "").strip(),   # 物理 join 表达式仅服务端内部使用
        })
    logger.info("[link_graph] object_links: keys=%s -> %d 条关系", sorted(wanted) or "*", len(out))
    return out
