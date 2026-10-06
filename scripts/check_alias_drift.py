"""别名/枚举一致性巡检: 分层比对四处来源, 输出报告(只报告, 不自动改)。

四处来源(应当同源派生, 独立编辑必然漂移):
  A. 对象层 aliases   —— canonical objects[].key/aliases (建模事实源)
  B. 字典 aliases      —— adh_metrics/adh_dimensions.aliases (可编辑事实源)
  C. RDF 图 altLabel   —— Oxigraph named graph `ds:<id>` 中对象的 skos:altLabel (只读投影)
  D. 业务术语表        —— adh_business_terms (只留未绑定黑话)

口径(历史上连错三次的收敛, 由 tests/test_alias_drift_check.py 锁定):
  - 图谱查询必须按 named graph 遍历(`GRAPH ?g` / `GRAPH <ds:ID>`), 不查默认单图;
  - IRI 一律剥命名空间与 `obj:` 前缀后比 owner, 同 alias 同 owner 不是漂移;
  - 仅字典有的 alias 是正常的(指标/维度自带名字), **不是**漂移;
  - 对象有图无 = 图谱滞后(missing_in_graph, 重建图谱可收敛), 与真漂移分开报;
  - 术语未收编 = 待办进展(terms_not_adopted), 不是漂移;
  - 图谱不可用必须显式报错, 不得静默当"无漂移"。

**不得 import `services.datamind.rag.graph_rag`**(其 __init__ 会缓存 OxigraphStore
单例, 测试注入的假 client 会被焊进共享实例, 污染 eval 检索链路)。

用法: venv/bin/python scripts/check_alias_drift.py [--datasource-id N]
退出码恒为 0(巡检不阻断); 有漂移时打印清单。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.shared.common.db.metadata_db import get_metadata_conn  # noqa: E402


# ── 口径: IRI 归一与图命名 ──────────────────────────────────────

def _iri_owner(iri) -> str:
    """剥 URL 命名空间/`obj:` 前缀, 取对象 key 作 owner。空值安全。"""
    s = str(iri or "").strip()
    if not s:
        return ""
    return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _graph_uri(datasource_id) -> str:
    """与 OxigraphStore.graph_uri 同格式(named graph = ds:<datasource_id>)。"""
    return f"http://ai-datahub.org/ontology/ds:{int(datasource_id or 0)}"


# ── 采集: 四层来源统一为 {alias: {alias, owners:set, graphs:set}} ──

def _entry(alias, owner="", graph=""):
    e = {"alias": alias, "owners": set(), "graphs": set()}
    if owner:
        e["owners"].add(owner)
    if graph:
        e["graphs"].add(graph)
    return e


def collect_graph_altlabels(datasource_id, client=None):
    """C: 图谱层 alias → owners。返回 (out, err)；图谱失败显式返回 err，不当"无漂移"。"""
    if client is None:
        from services.shared.common.rdf.sparql_client import get_sparql_client
        client = get_sparql_client()
    graph_clause = f"GRAPH <{_graph_uri(datasource_id or 0)}>" if datasource_id else "GRAPH ?g"
    sparql = f"""
        PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
        SELECT ?g ?owner ?alias WHERE {{
            {graph_clause} {{ ?owner skos:altLabel ?alias . }}
        }} LIMIT 5000
    """
    out = {}
    try:
        rows = client.query(sparql)
    except Exception as e:  # noqa: BLE001 — 巡检可诊断优先于吞错
        return {}, f"{type(e).__name__}: {e}"
    for r in rows or []:
        alias = str(r.get("alias", "") or "").strip()
        if not alias:
            continue
        e = out.setdefault(alias, _entry(alias))
        e["owners"].add(_iri_owner(r.get("owner")))
        if r.get("g") is not None:
            e["graphs"].add(str(r.get("g")))
    return out, None


def _object_aliases(datasource_id):
    """A: 对象层 key/aliases → owner=对象 key（canonical 是唯一可编辑事实源）。"""
    out = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT json_content FROM adh_ontology_models WHERE status='active'"
                + (" AND datasource_id IN (%s, 0)" if datasource_id else ""),
                ((datasource_id,) if datasource_id else ()))
            for r in cur.fetchall():
                raw = r.get("json_content")
                doc = json.loads(raw) if isinstance(raw, str) else (raw or {})
                for o in (doc or {}).get("objects", []):
                    key = str(o.get("key") or "").strip()
                    if not key:
                        continue
                    out.setdefault(key, _entry(key, key))
                    for a in (o.get("aliases") or []):
                        a = str(a).strip()
                        if a:
                            e = out.setdefault(a, _entry(a))
                            e["owners"].add(key)
    finally:
        conn.close()
    return out


def _dict_aliases(datasource_id):
    """B: 字典层 alias → owner=指标/维度 name。"""
    out = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for table in ("adh_metrics", "adh_dimensions"):
                try:
                    cur.execute(
                        f"SELECT name, aliases FROM {table} WHERE is_active = 1 "
                        + ("AND datasource_id IN (%s, 0)" if datasource_id else ""),
                        ((datasource_id,) if datasource_id else ()))
                    for r in cur.fetchall():
                        name = str(r.get("name") or "").strip()
                        if not name:
                            continue
                        out.setdefault(name, _entry(name, name))
                        aliases = r.get("aliases")
                        if isinstance(aliases, str):
                            try:
                                aliases = json.loads(aliases)
                            except Exception:
                                aliases = []
                        for a in (aliases or []):
                            a = str(a).strip()
                            if a:
                                e = out.setdefault(a, _entry(a))
                                e["owners"].add(name)
                except Exception:
                    conn.rollback()
    finally:
        conn.close()
    return out


def _business_terms(datasource_id):
    """D: 未收编业务黑话 → owner=术语自身。"""
    out = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT term_cn, term_aliases FROM adh_business_terms WHERE is_active = 1 "
                + ("AND datasource_id IN (%s, 0)" if datasource_id else ""),
                ((datasource_id,) if datasource_id else ()))
            for r in cur.fetchall():
                term = str(r.get("term_cn") or "").strip()
                if term:
                    out.setdefault(term, _entry(term, term))
                for x in str(r.get("term_aliases") or "").split(","):
                    x = x.strip()
                    if x:
                        e = out.setdefault(x, _entry(x))
                        e["owners"].add(term or x)
    finally:
        conn.close()
    return out


# ── 口径: 分层比对(测试锁定) ────────────────────────────────────

def diff(obj, graph, dic, terms):
    """分层比对口径:
    - 同 alias 同 owner 一致; 同 alias 不同 owner → obj_vs_graph_conflict / dict_vs_obj_conflict;
    - 仅字典有的 alias 正常(指标/维度自带名字), 不是漂移;
    - 对象有图无 = missing_in_graph(图谱滞后), 与真漂移分开报;
    - 术语未收编 = terms_not_adopted(进展待办), 不是漂移。
    """
    def owners(entry):
        return set((entry or {}).get("owners") or ())

    def fmt(alias, entry):
        return f"'{alias}' → {sorted(owners(entry))}"

    obj_vs_graph_conflict = []
    missing_in_graph = []
    graph_only = []
    for alias, oe in obj.items():
        ge = graph.get(alias)
        if ge is None:
            missing_in_graph.append(fmt(alias, oe))
        elif owners(ge) != owners(oe):
            obj_vs_graph_conflict.append(f"{fmt(alias, oe)} vs 图谱 {sorted(owners(ge))}")
    for alias, ge in graph.items():
        if alias not in obj:
            graph_only.append(fmt(alias, ge))
    dict_vs_obj_conflict = []
    for alias, de in dic.items():
        oe = obj.get(alias)
        if oe is not None and owners(de) != owners(oe):
            dict_vs_obj_conflict.append(f"{fmt(alias, de)} vs 对象层 {sorted(owners(oe))}")
    terms_not_adopted = sorted(t for t in terms if t not in obj)
    return {
        "obj_vs_graph_conflict": obj_vs_graph_conflict,
        "missing_in_graph": sorted(missing_in_graph),
        "graph_only": sorted(graph_only),
        "dict_vs_obj_conflict": dict_vs_obj_conflict,
        "dict_alias_count": len(dic),
        "terms_not_adopted": terms_not_adopted,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasource-id", type=int, default=0)
    args = ap.parse_args()

    obj = _object_aliases(args.datasource_id)
    dic = _dict_aliases(args.datasource_id)
    graph, err = collect_graph_altlabels(args.datasource_id)
    terms = _business_terms(args.datasource_id)

    print(f"别名一致性分层巡检 (datasource={args.datasource_id or 'ALL'})")
    print(f"  A 对象层条目  : {len(obj)}")
    print(f"  B 字典别名条目: {dic and sum(1 for _ in dic)}")
    if err:
        print(f"  C 图 altLabel : 读取失败 — {err}（不得当作无漂移）")
    else:
        print(f"  C 图 altLabel : {len(graph)}")
    print(f"  D 术语表条目  : {len(terms)}")

    r = diff(obj, graph if not err else {}, dic, terms)
    if err:
        r["graph_error"] = err

    def show(title, items, limit=30):
        if items:
            print(f"\n[漂移] {title}:")
            for x in items[:limit]:
                print(f"  - {x}")

    show("同名别名指向不同对象(对象层 vs 图谱, 需重建图谱或修 IRI)", r["obj_vs_graph_conflict"])
    show("同名别名指向不同对象(字典 vs 对象层, 需对齐事实源)", r["dict_vs_obj_conflict"])
    show("字典/对象别名未投影到图谱(图谱滞后, 重建图谱可收敛)", r["missing_in_graph"])
    if r["graph_only"]:
        print(f"\n[提示] 图谱独有别名 {len(r['graph_only'])} 条(可能来自已删对象, 重建图谱可收敛)")
    if r["terms_not_adopted"]:
        print(f"\n[提示] 术语表尚未收编的黑话 {len(r['terms_not_adopted'])} 条(待办进展, 可进别名回流队列评估):")
        for x in r["terms_not_adopted"][:30]:
            print(f"  - {x}")

    total = len(r["obj_vs_graph_conflict"]) + len(r["dict_vs_obj_conflict"])
    print("\n巡检完成:", "无真漂移" if total == 0 and not err else f"{total} 项真漂移" + (f"，图谱读取失败: {err}" if err else "") + "（仅报告, 不自动改）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
