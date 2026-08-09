---
kind: external_dependency
name: Neo4j — Graph Database for Knowledge Graph
slug: neo4j
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

Neo4j is an optional graph database used by the knowledge graph / graphservice layer. It is configured via `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` environment variables and can be started via `docker/neo4j/docker-compose.yml`. It is not required for core functionality but powers entity relationship visualization and graph-based features.