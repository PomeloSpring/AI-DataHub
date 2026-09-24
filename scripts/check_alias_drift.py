"""别名/枚举一致性巡检: 比对三处漂移, 输出报告(只报告, 不自动改)。

三处来源(应当同源派生, 独立编辑必然漂移):
  A. 字典 aliases      —— adh_metrics/adh_dimensions.aliases (可编辑事实源)
  B. RDF 图 altLabel   —— Oxigraph 中对象的 skos:altLabel (应为 A/本体 的只读投影)
  C. 业务术语表        —— adh_business_terms (应只留未绑定黑话, 已绑定的应与 A/B 一致)

用法: venv/bin/python scripts/check_alias_drift.py [--datasource-id N]
退出码恒为 0(巡检不阻断); 有漂移时打印清单。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.shared.common.db.metadata_db import get_metadata_conn  # noqa: E402


def _parse_json(v, fallback):
    if isinstance(v, (list, dict)):
        return v
    if v in (None, ""):
        return fallback
    try:
        return json.loads(v)
    except Exception:
        return fallback


def _dict_aliases(datasource_id):
    """A: 字典层 name -> set(aliases)。"""
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
                        out.setdefault(r["name"], set()).update(
                            str(a) for a in (_parse_json(r.get("aliases"), []) or []))
                except Exception:
                    conn.rollback()
    finally:
        conn.close()
    return out


def _rdf_alt_labels(datasource_id):
    """B: 图侧 skos:altLabel -> label 集(取不到 Oxigraph 时返回 None 表示跳过)。"""
    try:
        from services.shared.common.rdf.sparql_client import get_sparql_client
        from services.shared.common.rdf.namespaces import ADH_NS

        graph = f"{ADH_NS}ds:{datasource_id or 0}"
        sparql = f"""
            SELECT ?label ?alt WHERE {{
                GRAPH <{graph}> {{
                    ?s <http://www.w3.org/2000/01/rdf-schema#label> ?label ;
                       <http://www.w3.org/2004/02/skos/core#altLabel> ?alt .
                }}
            }} LIMIT 2000
        """
        rows = get_sparql_client().query(sparql)
        out = {}
        for r in rows:
            out.setdefault(str(r.get("label", "")).strip(), set()).add(str(r.get("alt", "")).strip())
        return out
    except Exception as e:
        print(f"  [skip] RDF altLabel 读取失败(Oxigraph 不可用?): {e}")
        return None


def _business_terms(datasource_id):
    """C: 已绑定到表的业务术语 term -> 目标线索。"""
    out = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT term_cn, term_aliases, target_table, target_column "
                "FROM adh_business_terms WHERE is_active = 1 "
                + ("AND datasource_id IN (%s, 0)" if datasource_id else ""),
                ((datasource_id,) if datasource_id else ()))
            for r in cur.fetchall():
                al = {x.strip() for x in str(r.get("term_aliases") or "").split(",") if x.strip()}
                out.setdefault(r["term_cn"], set()).update(al)
    finally:
        conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasource-id", type=int, default=0)
    args = ap.parse_args()

    dict_a = _dict_aliases(args.datasource_id)
    rdf_b = _rdf_alt_labels(args.datasource_id)
    terms_c = _business_terms(args.datasource_id)

    print(f"别名/枚举一致性巡检 (datasource={args.datasource_id or 'ALL'})")
    print(f"  A 字典别名条目: {sum(len(v) for v in dict_a.values())} (词头 {len(dict_a)})")
    print(f"  B 图 altLabel : {'(跳过)' if rdf_b is None else sum(len(v) for v in rdf_b.values())}")
    print(f"  C 术语表条目  : {sum(len(v) for v in terms_c.values())} (词头 {len(terms_c)})")

    drift = 0
    # A -> B: 字典别名应已投影到图上(图滞后 = 本体改后未重建)
    if rdf_b is not None:
        all_rdf_alt = {a for v in rdf_b.values() for a in v}
        missing_in_graph = sorted({a for v in dict_a.values() for a in v} - all_rdf_alt)
        if missing_in_graph:
            drift += len(missing_in_graph)
            print("\n[漂移] 字典别名未投影到图谱(需重建图谱):")
            for x in missing_in_graph[:30]:
                print(f"  - {x}")

    # C -> A: 业务术语的词若既不在字典词头也不在字典别名, 属"未收编黑话"(设计允许, 仅提示)
    known = set(dict_a) | {a for v in dict_a.values() for a in v}
    unbound = sorted(t for t in terms_c if t not in known)
    if unbound:
        print("\n[提示] 术语表中尚未收编到字典/本体的黑话(可进别名回流队列评估):")
        for x in unbound[:30]:
            print(f"  - {x}")

    print("\n巡检完成:", "无图谱投影漂移" if drift == 0 else f"{drift} 项漂移(仅报告, 不自动改)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
