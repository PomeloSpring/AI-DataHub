"""Convert JSON ontology models to RDF/Turtle for Oxigraph.

The ontology model is stored in `adh_ontology_models.json_content` as:
{
    "name": "Logistics Ontology",
    "version": "1.0",
    "objects": [
        {
            "id": "obj_1",
            "class": "Shipment",
            "label": "货运单",
            "description": "...",
            "properties": [
                {"name": "weight", "type": "float", "label": "重量"},
                {"name": "origin", "type": "Port", "label": "起运港"}
            ],
            "relations": [
                {"type": "ships_via", "target": "Port", "label": "途经"}
            ],
            "constraints": [
                {"type": "required", "property": "weight"},
                {"type": "min_value", "property": "weight", "value": 0}
            ]
        }
    ],
    "enums": [...],
    "mappings": [...]
}

This module converts that structure into OWL/RDFS RDF triples.
"""

import json
import logging
from typing import Any

from backend.common.rdf.namespaces import ADH_NS, TURTLE_PREFIXES

logger = logging.getLogger(__name__)


def ontology_json_to_turtle(json_content: str | dict, datasource_id: int = 0) -> str:
    """Convert an ontology JSON model to Turtle serialization.

    Args:
        json_content: The ontology model JSON (string or parsed dict).
        datasource_id: Datasource ID for scoping the named graph.

    Returns:
        Turtle-formatted RDF string ready for Oxigraph loading.
    """
    if isinstance(json_content, str):
        model = json.loads(json_content)
    else:
        model = json_content

    lines = [TURTLE_PREFIXES, ""]
    model_name = _safe_uri(model.get("name", "ontology"))
    model_iri = f"adh:{model_name}"

    # Ontology declaration
    lines.append(f"{model_iri} a owl:Ontology ;")
    lines.append(f'    rdfs:label "{model.get("name", "Ontology")}" ;')
    if model.get("description"):
        lines.append(f'    rdfs:comment "{_escape(model["description"])}" .')
    else:
        lines[-1] = lines[-1].rstrip(" ;") + " ."
    lines.append("")

    # Process each object → owl:Class
    for obj in model.get("objects", []):
        _emit_class(lines, obj, model_name)

    # ── M3 规则 / M4 场景节点（图谱知识图谱化：规则作用域与场景编排可视觉追踪）──
    for rule in (model.get("rules") or []):
        rkey = str(rule.get("key") or "").strip()
        if not rkey:
            continue
        r_iri = f"adh:rule:{_safe_uri(rkey)}"
        lines.append(f"{r_iri} a adh:Rule ;")
        stmt = _escape(str(rule.get("statement") or rkey))[:120]
        lines.append(f'    rdfs:label "{stmt}"@zh ;')
        rtype = _escape(str(rule.get("type") or ""))
        lines.append(f'    adh:ruleType "{rtype}"^^xsd:string .')
        enf = rule.get("enforcement") or {}
        for tgt in (enf.get("applies_to") or []):
            tgt_iri = _class_iri(str(tgt))
            lines.append(f"{r_iri} adh:governs {tgt_iri} .")
        act_ref = str(enf.get("action") or "").strip()
        if act_ref and act_ref != "*":
            lines.append(f"{r_iri} adh:controls adh:act:{_safe_uri(act_ref)} .")
    if model.get("rules"):
        lines.append("")
    for scn in (model.get("scenarios") or []):
        skey = str(scn.get("key") or "").strip()
        if not skey:
            continue
        s_iri = f"adh:scenario:{_safe_uri(skey)}"
        lines.append(f"{s_iri} a adh:Scenario ;")
        stitle = _escape(str(scn.get("title") or skey))
        lines.append(f'    rdfs:label "{stitle}"@zh ;')
        if scn.get("goal"):
            sgoal = _escape(str(scn["goal"]))
            lines.append(f'    adh:effect "{sgoal}"^^xsd:string ;')
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        for tgt in (scn.get("uses_objects") or []):
            lines.append(f"{s_iri} adh:usesObject {_class_iri(str(tgt))} .")
        for act in (scn.get("uses_actions") or []):
            lines.append(f"{s_iri} adh:usesAction adh:act:{_safe_uri(str(act))} .")
        for rk in (scn.get("uses_rules") or []):
            lines.append(f"{s_iri} adh:usesRule adh:rule:{_safe_uri(str(rk))} .")
    if model.get("scenarios"):
        lines.append("")

    # Process enums → owl:Class with owl:oneOf
    for enum in model.get("enums", []):
        _emit_enum(lines, enum, model_name)

    # Process mappings → owl:ObjectProperty
    for mapping in model.get("mappings", []):
        _emit_mapping(lines, mapping, model_name)

    return "\n".join(lines)


def build_sparql_insert_from_turtle(turtle_str: str, graph_uri: str) -> str:
    """Wrap Turtle data into a SPARQL INSERT DATA statement for a named graph.

    Note: For large datasets, prefer using OxigraphClient.load_rdf() which
    uses the PUT /store endpoint directly. This method is for small incremental
    updates via SPARQL UPDATE.
    """
    # SPARQL INSERT DATA doesn't support prefixes in all implementations,
    # so we use the PUT /store endpoint for bulk loads instead.
    # This is a convenience wrapper for small updates.
    return f"# Use OxigraphClient.load_rdf() for bulk loading.\n# Graph: {graph_uri}\n{turtle_str}"


# ── Internal emitters ────────────────────────────────────────────────

def _class_iri(key: str) -> str:
    """IRI for an ontology object class (namespaced to avoid colliding with
    the physical adh:table: / adh:col: nodes built by the graph store)."""
    return f"adh:obj:{_safe_uri(key)}"


def _emit_class(lines: list[str], obj: dict, model_name: str):
    """Emit an ontology object as an owl:Class plus its traversable links.

    Accepts the canonical ontology_service schema
    (key / display_name / aliases / primary_table / properties[].column / links[])
    and stays backward-compatible with the legacy class / relations schema.

    The important addition over the old behaviour: each link is emitted as an
    *instance-level* triple (``adh:linkedTo``) so a single SPARQL property path
    can walk the object graph, plus a reified ``adh:Link`` node carrying the
    physical JOIN expression so it survives one-shot traversal.
    """
    key = obj.get("key") or obj.get("class") or obj.get("id") or "Unknown"
    class_iri = _class_iri(key)
    label = obj.get("display_name") or obj.get("label") or key

    # Dual-typed: owl:Class (consumed by the ontology_traversal SPARQL) plus
    # adh:Term so the existing "业务知识图" visualization renders it without a
    # frontend change (that view queries adh:Term/Metric/Dimension nodes).
    lines.append(f"{class_iri} a owl:Class , adh:Term ;")
    lines.append(f'    rdfs:label "{_escape(str(label))}"@zh ;')
    if obj.get("description"):
        desc = _escape(str(obj["description"]))
        lines.append(f'    rdfs:comment "{desc}"@zh ;')
        lines.append(f'    adh:comment "{desc}"^^xsd:string ;')
    if obj.get("primary_table"):
        lines.append(f'    adh:primaryTable "{_escape(str(obj["primary_table"]))}"^^xsd:string ;')
    # 业务本体路由指针（路由索引层）：目标源本体/数据源/对象 + 源内过滤提示。
    # 只投影 name 级标识（与护栏 §7 一致），供业务图 ds:0 的路由消费与图谱展示。
    route = obj.get("route")
    if isinstance(route, dict) and route:
        if route.get("source_ontology"):
            lines.append(f'    adh:routeSource "{_escape(str(route["source_ontology"]))}"^^xsd:string ;')
        if route.get("datasource_name"):
            lines.append(f'    adh:routeDatasource "{_escape(str(route["datasource_name"]))}"^^xsd:string ;')
        if route.get("object_key"):
            lines.append(f'    adh:routeObject "{_escape(str(route["object_key"]))}"^^xsd:string ;')
        if route.get("mode"):
            lines.append(f'    adh:routeMode "{_escape(str(route["mode"]))}"^^xsd:string ;')
        for hint in (route.get("filter_hints") or []):
            dim = str((hint or {}).get("dimension") or "")
            if dim:
                lines.append(f'    adh:routeFilterHint "{_escape(dim)}"^^xsd:string ;')
    for alias in (obj.get("aliases") or []):
        # 英文别名以 @en 标签，便于 SPARQL 根据语言过滤；中文默认 @zh。
        lang = "en" if _is_ascii(str(alias)) else "zh"
        lines.append(f'    skos:altLabel "{_escape(str(alias))}"@{lang} ;')
    # 执行绑定徒章（Phase 2）：将语义层已解析好的 query_mode/size_class/
    # catalog_ref/permission_tokens 直接写图，供图谱徒章与 traversal 回传。
    binding = obj.get("execution_binding") or {}
    emitted_binding = False
    if isinstance(binding, dict) and binding:
        qm = binding.get("query_mode")
        sc = binding.get("size_class")
        cr = binding.get("catalog_ref")
        afs = binding.get("allow_full_scan")
        st = binding.get("sync_state")
        if qm:
            lines.append(f'    adh:queryMode "{_escape(str(qm))}"^^xsd:string ;')
            emitted_binding = True
        if sc:
            lines.append(f'    adh:sizeClass "{_escape(str(sc))}"^^xsd:string ;')
            emitted_binding = True
        if cr:
            lines.append(f'    adh:catalogRef "{_escape(str(cr))}"^^xsd:string ;')
            emitted_binding = True
        if afs is not None:
            lines.append(f'    adh:allowFullScan "{1 if int(afs or 0) else 0}"^^xsd:boolean ;')
            emitted_binding = True
        if st:
            lines.append(f'    adh:syncState "{_escape(str(st))}"^^xsd:string ;')
            emitted_binding = True
    lines[-1] = lines[-1].rstrip(" ;") + " ."
    lines.append("")

    # Object → Table 的 boundTo 边（数据血缘链：Object-boundTo-Table-Column-DataSource）
    phys = obj.get("primary_table") or ""
    if emitted_binding and phys:
        table_iri = f"adh:table:{_safe_uri(str(phys))}"
        lines.append(f"{class_iri} adh:boundTo {table_iri} .")
        for tok in (binding.get("permission_tokens") or []):
            lines.append(f'{class_iri} adh:permToken "{_escape(str(tok))}"^^xsd:string .')
        for mc in (binding.get("masked_columns") or []):
            lines.append(f'{class_iri} adh:maskedColumn "{_escape(str(mc))}"^^xsd:string .')
        lines.append("")

    # Physical column mapping (dedup) — used to expand objects back to tables.
    seen_cols: set[str] = set()
    for prop in obj.get("properties", []):
        column = prop.get("column")
        if column and str(column) not in seen_cols:
            seen_cols.add(str(column))
            lines.append(f'{class_iri} adh:mapsColumn "{_escape(str(column))}"^^xsd:string .')
    if seen_cols:
        lines.append("")

    # ── 语义展开节点（M2 行为 / 属性维度 / 派生指标）──
    # 图谱知识图谱化：对象周围以连线呈现可分析的语义单元，而非只有对象间 link。
    for act in (obj.get("actions") or []):
        akey = str(act.get("key") or "").strip()
        if not akey:
            continue
        act_iri = f"adh:act:{_safe_uri(akey)}"
        lines.append(f"{act_iri} a adh:Action ;")
        lines.append(f'    rdfs:label "{_escape(str(act.get("label") or akey))}"@zh ;')
        lines.append(f'    adh:actionKey "{_escape(akey)}"^^xsd:string ;')
        lines.append(f'    adh:readOnly "{1 if act.get("read_only") else 0}"^^xsd:boolean ;')
        if act.get("risk"):
            risk_val = _escape(str(act["risk"]))
            lines.append(f'    adh:risk "{risk_val}"^^xsd:string ;')
        if act.get("effect"):
            effect_val = _escape(str(act["effect"]))
            lines.append(f'    adh:effect "{effect_val}"^^xsd:string ;')
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append(f"{class_iri} adh:hasAction {act_iri} .")
    for prop in (obj.get("properties") or []):
        pname = str(prop.get("name") or "").strip()
        pcol = str(prop.get("column") or "").strip()
        if not pname or not pcol:
            continue
        prop_iri = f"adh:prop:{_safe_uri(str(key) + '.' + pcol.rsplit('.', 1)[-1])}"
        lines.append(f"{prop_iri} a adh:Property ;")
        lines.append(f'    rdfs:label "{_escape(pname)}"@zh ;')
        if prop.get("type"):
            ptype = _escape(str(prop["type"]))
            lines.append(f'    adh:dataType "{ptype}"^^xsd:string ;')
        if prop.get("description"):
            pdesc = _escape(str(prop["description"]))
            lines.append(f'    adh:effect "{pdesc}"^^xsd:string ;')
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append(f"{class_iri} adh:hasProperty {prop_iri} .")
    for m in (obj.get("metrics") or []):
        mname = str(m.get("name") or "").strip()
        if not mname:
            continue
        m_iri = f"adh:objmetric:{_safe_uri(str(key) + '.' + mname)}"
        lines.append(f"{m_iri} a adh:Metric ;")
        lines.append(f'    rdfs:label "{_escape(mname)}"@zh ;')
        if m.get("description"):
            mdesc = _escape(str(m["description"]))
            lines.append(f'    adh:effect "{mdesc}"^^xsd:string ;')
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append(f"{class_iri} adh:hasMetric {m_iri} .")
    if (obj.get("actions") or obj.get("properties") or obj.get("metrics")):
        lines.append("")

    # Links → traversable instance edges + reified join expressions.
    for rel in (obj.get("links") or obj.get("relations") or []):
        _emit_link_instance(lines, class_iri, key, rel)


def _emit_link_instance(lines: list[str], src_iri: str, src_key: str, rel: dict):
    """Emit a traversable edge plus a reified adh:Link carrying the JOIN."""
    target = rel.get("target") or ""
    if not target:
        return
    tgt_iri = _class_iri(target)
    rel_name = _safe_uri(str(rel.get("type") or rel.get("name") or "linked"))
    join_expr = str(rel.get("join") or "")
    cardinality = str(rel.get("cardinality") or "")
    link_label = rel.get("description") or rel.get("label") or rel_name

    # Direct edge for SPARQL property-path traversal (adh:linkedTo*).
    lines.append(f"{src_iri} adh:linkedTo {tgt_iri} .")
    # Mirror as adh:mapsTo (Term→Term) so the object↔object link is drawn by the
    # existing "业务知识图" view, whose relation filter selects mapsTo/defines/belongsTo.
    lines.append(f"{src_iri} adh:mapsTo {tgt_iri} .")
    # Reified link so the JOIN / cardinality survive a single closure query.
    link_iri = f"adh:link_{_safe_uri(src_key)}_{rel_name}_{_safe_uri(str(target))}"
    lines.append(f"{link_iri} a adh:Link ;")
    lines.append(f"    adh:linkFrom {src_iri} ;")
    lines.append(f"    adh:linkTo {tgt_iri} ;")
    lines.append(f'    adh:linkType "{_escape(rel_name)}"^^xsd:string ;')
    if cardinality:
        lines.append(f'    adh:cardinality "{_escape(cardinality)}"^^xsd:string ;')
    lines.append(f'    adh:joinExpr "{_escape(join_expr)}"^^xsd:string ;')
    lines.append(f'    rdfs:label "{_escape(str(link_label))}"@zh .')
    lines.append("")


def _emit_property(lines: list[str], prop: dict, class_iri: str, class_name: str):
    """Emit an owl:DatatypeProperty declaration."""
    prop_name = _safe_uri(prop.get("name", "unknown"))
    prop_iri = f"adh:{prop_name}"
    prop_type = prop.get("type", "string")
    prop_label = prop.get("label", prop_name)
    xsd_type = _python_to_xsd(prop_type)

    lines.append(f"{prop_iri} a owl:DatatypeProperty ;")
    lines.append(f'    rdfs:label "{_escape(prop_label)}"@zh ;')
    lines.append(f"    rdfs:domain {class_iri} ;")
    lines.append(f"    rdfs:range {xsd_type} .")
    lines.append("")


def _emit_relation(lines: list[str], rel: dict, class_iri: str):
    """Emit an owl:ObjectProperty declaration."""
    rel_name = _safe_uri(rel.get("type", rel.get("name", "related")))
    rel_iri = f"adh:{rel_name}"
    target_class = _safe_uri(rel.get("target", "Thing"))
    target_iri = f"adh:{target_class}"
    label = rel.get("label", rel_name)

    lines.append(f"{rel_iri} a owl:ObjectProperty ;")
    lines.append(f'    rdfs:label "{_escape(label)}"@zh ;')
    lines.append(f"    rdfs:domain {class_iri} ;")
    lines.append(f"    rdfs:range {target_iri} .")
    lines.append("")


def _emit_constraint(lines: list[str], constraint: dict, class_iri: str):
    """Emit an owl:Restriction as rdfs:subClassOf axiom."""
    ctype = constraint.get("type", "")
    prop_name = constraint.get("property", "")

    if not prop_name:
        return

    prop_iri = f"adh:{_safe_uri(prop_name)}"

    if ctype == "required":
        lines.append(f"{class_iri} rdfs:subClassOf [")
        lines.append(f"    a owl:Restriction ;")
        lines.append(f"    owl:onProperty {prop_iri} ;")
        lines.append(f"    owl:minCardinality 1")
        lines.append(f"] .")
    elif ctype == "min_value":
        val = constraint.get("value", 0)
        lines.append(f"{class_iri} rdfs:subClassOf [")
        lines.append(f"    a owl:Restriction ;")
        lines.append(f"    owl:onProperty {prop_iri} ;")
        lines.append(f'    owl:allValuesFrom [ a rdfs:Datatype ; owl:minInclusive "{val}"^^xsd:decimal ]')
        lines.append(f"] .")
    elif ctype == "max_value":
        val = constraint.get("value", 0)
        lines.append(f"{class_iri} rdfs:subClassOf [")
        lines.append(f"    a owl:Restriction ;")
        lines.append(f"    owl:onProperty {prop_iri} ;")
        lines.append(f'    owl:allValuesFrom [ a rdfs:Datatype ; owl:maxInclusive "{val}"^^xsd:decimal ]')
        lines.append(f"] .")
    lines.append("")


def _emit_enum(lines: list[str], enum: dict, model_name: str):
    """Emit an owl:Class with owl:oneOf for enumerated values."""
    enum_name = _safe_uri(enum.get("name", enum.get("id", "Enum")))
    enum_iri = f"adh:{enum_name}"
    label = enum.get("label", enum_name)
    values = enum.get("values", [])

    lines.append(f"{enum_iri} a owl:Class ;")
    lines.append(f'    rdfs:label "{_escape(label)}"@zh ;')

    if values:
        enum_list = " ".join(f'"{_escape(str(v))}"' for v in values)
        lines.append(f"    owl:oneOf ( {enum_list} ) .")
    else:
        lines[-1] = lines[-1].rstrip(" ;") + " ."
    lines.append("")


def _emit_mapping(lines: list[str], mapping: dict, model_name: str):
    """Emit a mapping relationship as owl:ObjectProperty."""
    source = _safe_uri(mapping.get("source", ""))
    target = _safe_uri(mapping.get("target", ""))
    rel_type = _safe_uri(mapping.get("type", "maps_to"))

    if not source or not target:
        return

    prop_iri = f"adh:{rel_type}_{source}_{target}"
    lines.append(f"{prop_iri} a owl:ObjectProperty ;")
    lines.append(f"    rdfs:domain adh:{source} ;")
    lines.append(f"    rdfs:range adh:{target} .")
    lines.append("")


# ── Utility functions ────────────────────────────────────────────────

_PRIMITIVE_TYPES = {"string", "int", "integer", "float", "double", "boolean",
                    "date", "datetime", "text", "decimal", "long", "number"}


def _is_primitive_type(type_name: str) -> bool:
    return type_name.lower() in _PRIMITIVE_TYPES


def _python_to_xsd(type_name: str) -> str:
    """Map Python/JSON type names to XSD datatypes."""
    mapping = {
        "string": "xsd:string",
        "text": "xsd:string",
        "int": "xsd:integer",
        "integer": "xsd:integer",
        "long": "xsd:long",
        "float": "xsd:float",
        "double": "xsd:double",
        "decimal": "xsd:decimal",
        "number": "xsd:decimal",
        "boolean": "xsd:boolean",
        "date": "xsd:date",
        "datetime": "xsd:dateTime",
    }
    return mapping.get(type_name.lower(), "xsd:string")


def _safe_uri(name: str) -> str:
    """Sanitize a name for use as an RDF local name (NCName)."""
    if not name:
        return "unnamed"
    # Replace spaces and special chars with underscores
    result = ""
    for ch in name:
        if ch.isalnum() or ch == "_":
            result += ch
        else:
            result += "_"
    # Ensure it starts with a letter or underscore
    if result and result[0].isdigit():
        result = "_" + result
    return result or "unnamed"


def _escape(text: str) -> str:
    """Escape special characters for RDF string literals."""
    return (text
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))


def _is_ascii(text: str) -> bool:
    """判断字符串是否全 ASCII，用于给 skos:altLabel 打 @en/@zh 语言标签。"""
    try:
        return all(ord(ch) < 128 for ch in text)
    except Exception:
        return False
