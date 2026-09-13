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

from services.shared.common.rdf.namespaces import ADH_NS, TURTLE_PREFIXES

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

def _emit_class(lines: list[str], obj: dict, model_name: str):
    """Emit an owl:Class declaration with properties and restrictions."""
    class_name = _safe_uri(obj.get("class", obj.get("id", "Unknown")))
    class_iri = f"adh:{class_name}"
    label = obj.get("label", class_name)

    lines.append(f"{class_iri} a owl:Class ;")
    lines.append(f'    rdfs:label "{_escape(label)}"@zh ;')

    if obj.get("description"):
        lines.append(f'    rdfs:comment "{_escape(obj["description"])}"@zh ;')

    # Data properties (primitive types)
    for prop in obj.get("properties", []):
        prop_name = prop.get("name", "")
        prop_type = prop.get("type", "string")
        prop_label = prop.get("label", prop_name)

        if _is_primitive_type(prop_type):
            # owl:DatatypeProperty
            prop_iri = f"adh:{_safe_uri(prop_name)}"
            lines.append(f"    # property: {prop_name} ({prop_type})")
        else:
            # owl:ObjectProperty → range is another class
            target_iri = f"adh:{_safe_uri(prop_type)}"
            lines.append(f"    # relation: {prop_name} -> {prop_type}")

    lines[-1] = lines[-1].rstrip(" ;") + " ."
    lines.append("")

    # Emit individual properties as owl:DatatypeProperty / owl:ObjectProperty
    for prop in obj.get("properties", []):
        _emit_property(lines, prop, class_iri, obj.get("class", "Unknown"))

    # Emit relations as owl:ObjectProperty
    for rel in obj.get("relations", []):
        _emit_relation(lines, rel, class_iri)

    # Emit constraints as owl:Restriction
    for constraint in obj.get("constraints", []):
        _emit_constraint(lines, constraint, class_iri)


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
