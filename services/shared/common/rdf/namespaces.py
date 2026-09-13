"""Standard RDF namespace definitions for AI-DataHub ontology.

All SPARQL queries and RDF serializations should use these prefixes
to ensure consistent IRI resolution across the platform.
"""

# Namespace IRIs
ADH_NS = "http://ai-datahub.org/ontology/"
SCHEMA_NS = "https://schema.org/"
OWL_NS = "http://www.w3.org/2002/07/owl#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
XSD_NS = "http://www.w3.org/2001/XMLSchema#"

# SPARQL PREFIX block — inject at the top of every query
SPARQL_PREFIXES = f"""
PREFIX adh: <{ADH_NS}>
PREFIX schema: <{SCHEMA_NS}>
PREFIX owl: <{OWL_NS}>
PREFIX rdfs: <{RDFS_NS}>
PREFIX rdf: <{RDF_NS}>
PREFIX skos: <{SKOS_NS}>
PREFIX xsd: <{XSD_NS}>
""".strip()

# Turtle @prefix block — used when serializing RDF data
TURTLE_PREFIXES = f"""
@prefix adh: <{ADH_NS}> .
@prefix schema: <{SCHEMA_NS}> .
@prefix owl: <{OWL_NS}> .
@prefix rdfs: <{RDFS_NS}> .
@prefix rdf: <{RDF_NS}> .
@prefix skos: <{SKOS_NS}> .
@prefix xsd: <{XSD_NS}> .
""".strip()
