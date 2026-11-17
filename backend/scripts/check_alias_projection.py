"""单向投影巡检：对象 aliases（源）→ 图谱 skos:altLabel（投影）一致性。

替代已退役的 scripts/check_alias_drift.py（三处横比）。别名单源后只查
"源 → 投影" 单向：源里有、图里无 = 投影滞后；图里有、源里无 = 悬空投影。
三个历史误报教训必须守住（原脚本连错三次的根因）：
  ① 三元组在命名图 ds:<id> 里，必须 `GRAPH ?g` 全命名图扫描，查默认图必误报"图谱滞后"；
  ② IRI 必须剥命名空间/前缀后再比对（`obj:case_file` 与 `case_file` 是同一 owner）；
  ③ 指标/维度在图里用 rdfs:label/nameCn 而非 altLabel，**不参与** altLabel 比对；
     术语-字典重叠交给 backend/scripts/absorb_business_terms.py（收编即退役）。

只报告不自动改。用法（repo 根目录）:
  venv/bin/python backend/scripts/check_alias_projection.py
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.dirname(__file__))

_SKOS_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"


def _iri_owner(iri) -> str:
    """剥 IRI 命名空间与类型前缀，取 owner 键（教训②）。"""
    s = str(iri or "").strip()
    if not s:
        return ""
    s = s.rstrip(">") if s.startswith("<") else s
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.strip()


def _fetch_altlabels(client) -> dict[str, set[str]]:
    """全命名图扫描 skos:altLabel（教训①：不得查默认图/单个空图）。"""
    rows = client.query(
        f"SELECT ?g ?s ?o WHERE {{ GRAPH ?g {{ ?s <{_SKOS_ALT}> ?o }} }}")
    out: dict[str, set[str]] = {}
    for r in rows or []:
        owner = _iri_owner(r.get("s"))
        if owner:
            out.setdefault(owner, set()).add(str(r.get("o") or ""))
    return out


def _source_object_aliases() -> dict[str, set[str]]:
    """源侧：active 模型 canonical 对象的 aliases（唯一可编辑登记）。"""
    from backend.common.db.metadata_db import get_metadata_conn

    src: dict[str, set[str]] = {}
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT json_content FROM adh_ontology_models "
                "WHERE status = 'active' AND json_content IS NOT NULL")
            for row in cur.fetchall() or []:
                try:
                    doc = json.loads(row.get("json_content") or "{}")
                except json.JSONDecodeError:
                    continue
                for o in doc.get("objects") or []:
                    key = str(o.get("key") or "").strip()
                    if not key:
                        continue
                    bucket = src.setdefault(key, set())
                    for a in o.get("aliases") or []:
                        bucket.add(str(a))
    return src


def check(client=None) -> dict:
    """返回 {missing_in_graph, stale_in_graph, graph_error}（graph_error 非空=巡检不完整）。"""
    if client is None:
        from backend.common.rdf.sparql_client import get_sparql_client
        client = get_sparql_client()
    try:
        graph = _fetch_altlabels(client)   # 教训：图查询失败必须报告，不得吞掉后当"0 条"
    except Exception as e:  # noqa: BLE001 — 巡检只报告
        return {"missing_in_graph": {}, "stale_in_graph": {},
                "graph_error": f"图谱查询失败（巡检不完整，勿据此判定滞后）: {e}"}

    src = _source_object_aliases()
    missing: dict[str, list[str]] = {}
    for owner, aliases in src.items():
        have = {a.lower() for a in graph.get(owner, set())}
        gap = sorted(a for a in aliases if a.lower() not in have)
        if gap:
            missing[owner] = gap

    stale: dict[str, list[str]] = {}
    for owner, labels in graph.items():
        known = {a.lower() for a in src.get(owner, set())} | {owner.lower()}
        ghost = sorted(a for a in labels if a.lower() not in known)
        if ghost:
            stale[owner] = ghost

    return {"missing_in_graph": missing, "stale_in_graph": stale, "graph_error": ""}


def main() -> int:
    report = check()
    if report["graph_error"]:
        print(f"[不完整] {report['graph_error']}")
    miss, stale = report["missing_in_graph"], report["stale_in_graph"]
    if not miss and not stale and not report["graph_error"]:
        print("巡检通过：对象 aliases 与图谱 altLabel 投影一致")
    for owner, gap in sorted(miss.items()):
        print(f"[投影滞后] {owner}: 源有图无 → {'/'.join(gap)}")
    for owner, ghost in sorted(stale.items()):
        print(f"[悬空投影] {owner}: 图有源无 → {'/'.join(ghost)}")

    from absorb_business_terms import _load_targets, _resolve, _tokens
    from backend.common.db.metadata_db import get_metadata_conn
    dicts, objs = _load_targets()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_business_terms WHERE is_active = 1")
            terms = cur.fetchall() or []
    overlap = 0
    for term in terms:
        toks = _tokens(term.get("term_cn"), term.get("term_en"), term.get("term_aliases"))
        target, amb = _resolve(toks, dicts + objs)
        if target or amb:
            overlap += 1
    print(f"[术语-字典重叠] 待收编 {overlap} 条（明细跑 absorb_business_terms.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
