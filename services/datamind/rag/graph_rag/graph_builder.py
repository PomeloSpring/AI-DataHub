"""Graph Builder — build knowledge graph from metadata into Oxigraph.

Reads metadata from MySQL tables (adh_table_info, adh_column_metadata, etc.)
and constructs RDF triples in Oxigraph, organized by named graph per datasource.
Also merges active ontology models from adh_ontology_models.
"""

import logging
from typing import Any

from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore

logger = logging.getLogger(__name__)


class GraphBuilder:
    """Builds the RDF knowledge graph from database metadata."""

    def __init__(self, store: OxigraphStore = None):
        self._store = store or OxigraphStore()

    def build_from_metadata(self, datasource_id: int = 0) -> dict[str, Any]:
        """Build the knowledge graph from MySQL metadata tables.

        Args:
            datasource_id: Scope to a specific datasource (0 = all).

        Returns:
            Build statistics.
        """
        logger.info("Building knowledge graph for datasource: %d", datasource_id)

        stats = {
            "tables": 0,
            "columns": 0,
            "terms": 0,
            "metrics": 0,
            "dimensions": 0,
            "datasources": 0,
            "joins": 0,
            "term_mappings": 0,
            "metric_relations": 0,
            "sql_templates": 0,
        }

        try:
            # Clear existing graph for this datasource
            self._store.clear_graph(datasource_id)

            # Merge ontology FIRST: the RDF bulk load (PUT /store) replaces the
            # named graph, so it must run before the physical nodes are appended
            # via INSERT DATA — otherwise it would wipe them.
            ontology_count = self._merge_ontology_models(datasource_id)
            logger.info("Merged %d ontology model(s)", ontology_count)

            # Build table nodes
            tables = self._load_tables(datasource_id)
            for t in tables:
                self._store.create_table_node(
                    name=t["table_name"],
                    comment=t.get("table_comment", ""),
                    business_desc=t.get("table_business_desc", ""),
                    datasource_id=datasource_id,
                )
                stats["tables"] += 1
            logger.info("Created %d table nodes", stats["tables"])

            # Build column nodes
            columns = self._load_columns(datasource_id)
            for c in columns:
                self._store.create_column_node(
                    table=c["table_name"],
                    column=c["column_name"],
                    data_type=c.get("data_type", ""),
                    comment=c.get("column_comment", ""),
                    datasource_id=datasource_id,
                )
                stats["columns"] += 1
            logger.info("Created %d column nodes", stats["columns"])

            # Build business term nodes
            terms = self._load_terms(datasource_id)
            for t in terms:
                self._store.create_term_node(
                    name_cn=t.get("name_cn", ""),
                    name_en=t.get("name_en", ""),
                    description=t.get("description", ""),
                    calculation=t.get("calculation", ""),
                    datasource_id=datasource_id,
                )
                # Create term → column mappings
                if t.get("mapped_table") and t.get("mapped_column"):
                    self._store.create_term_mapping(
                        term_name=t["name_cn"],
                        table=t["mapped_table"],
                        column=t["mapped_column"],
                        datasource_id=datasource_id,
                    )
                    stats["term_mappings"] += 1
                stats["terms"] += 1
            logger.info("Created %d term nodes, %d mappings", stats["terms"], stats["term_mappings"])

            # Build metric nodes
            metrics = self._load_metrics(datasource_id)
            for m in metrics:
                self._store.create_metric_node(
                    name=m.get("name", ""),
                    description=m.get("description", ""),
                    datasource_id=datasource_id,
                )
                # Create metric → column relations
                if m.get("table_name") and m.get("column_name"):
                    self._store.create_metric_column_relation(
                        metric_name=m["name"],
                        table=m["table_name"],
                        column=m["column_name"],
                        datasource_id=datasource_id,
                    )
                    stats["metric_relations"] += 1
                stats["metrics"] += 1
            logger.info("Created %d metric nodes", stats["metrics"])

            # Build datasource nodes
            datasources = self._load_datasources()
            for ds in datasources:
                self._store.create_datasource_node(
                    ds_id=ds["id"],
                    name=ds.get("name", ""),
                    db_type=ds.get("db_type", ""),
                )
                stats["datasources"] += 1
            logger.info("Created %d datasource nodes", stats["datasources"])

            # Build JOIN relations from data_lineage or table_relations
            joins = self._load_join_relations(datasource_id)
            for j in joins:
                self._store.create_join_relation(
                    table1=j["table1"],
                    table2=j["table2"],
                    join_type=j.get("join_type", ""),
                    datasource_id=datasource_id,
                )
                stats["joins"] += 1
            logger.info("Created %d join relations", stats["joins"])

            # Build SQL template nodes (materialize templates into the graph so
            # the retriever can ground questions against them via SPARQL).
            table_names = [t["table_name"] for t in tables]
            templates = self._load_sql_templates(datasource_id)
            for tpl in templates:
                sql_text = tpl.get("sql_template", "") or ""
                lowered = sql_text.lower()
                touched = [tn for tn in table_names
                           if tn and tn.lower() in lowered]
                self._store.create_sql_template_node(
                    template_id=tpl.get("template_id", ""),
                    name=tpl.get("template_name", ""),
                    sql=sql_text,
                    intent_keywords=tpl.get("intent_keywords", ""),
                    category=tpl.get("category", ""),
                    description=tpl.get("description", ""),
                    variables=tpl.get("variables", "") or "",
                    rules=tpl.get("rules", "") or "",
                    dialect=tpl.get("dialect", "") or "",
                    tables=touched,
                    datasource_id=datasource_id,
                )
                stats["sql_templates"] += 1
            logger.info("Created %d SQL template nodes", stats["sql_templates"])

            # Ontology models were merged at the start (see above).

            total_triples = self._store.count_triples(datasource_id)
            logger.info("Graph build complete. Total triples: %d", total_triples)

            return {
                "success": True,
                **stats,
                "ontology_models_merged": ontology_count,
                "total_triples": total_triples,
                "datasource_id": datasource_id,
            }

        except Exception as e:
            logger.error("Graph build failed: %s", e, exc_info=True)
            return {"success": False, **stats, "error": str(e), "datasource_id": datasource_id}

    # ── Data loaders (MySQL) ─────────────────────────────────────────

    def _load_tables(self, datasource_id: int) -> list[dict]:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT table_name, table_comment, table_business_desc, datasource_id
                    FROM adh_table_info
                    WHERE is_active = 1 {ds_filter}
                    ORDER BY table_name
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load tables: %s", e)
            return []
        finally:
            conn.close()

    def _load_columns(self, datasource_id: int) -> list[dict]:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT table_name, column_name, data_type, column_comment, datasource_id
                    FROM adh_column_metadata
                    WHERE is_active = 1 {ds_filter}
                    ORDER BY table_name, id
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load columns: %s", e)
            return []
        finally:
            conn.close()

    def _load_terms(self, datasource_id: int) -> list[dict]:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT term_cn AS name_cn, term_en AS name_en, description,
                           calculation, target_table AS mapped_table,
                           target_column AS mapped_column, datasource_id
                    FROM adh_business_terms
                    WHERE is_active = 1 {ds_filter}
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load business terms: %s", e)
            return []
        finally:
            conn.close()

    def _load_metrics(self, datasource_id: int) -> list[dict]:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT name, description,
                           target_table AS table_name,
                           target_column AS column_name, datasource_id
                    FROM adh_metrics
                    WHERE is_active = 1 {ds_filter}
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load metrics: %s", e)
            return []
        finally:
            conn.close()

    def _load_datasources(self) -> list[dict]:
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # adh_datasources 无 is_active/status 列(init.sql schema), 全量列出即可
                cur.execute("SELECT id, name, db_type FROM adh_datasources")
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load datasources: %s", e)
            return []
        finally:
            conn.close()

    def _load_join_relations(self, datasource_id: int) -> list[dict]:
        """Load JOIN relations from table_relations or data_lineage."""
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                # Try adh_table_relations first
                cur.execute(f"""
                    SELECT source_table AS table1, target_table AS table2,
                           relation_type AS join_type, datasource_id
                    FROM adh_table_relations
                    WHERE is_active = 1 {ds_filter}
                """, params)
                rows = cur.fetchall()
                if rows:
                    return rows

                # Fallback: extract from data_lineage
                cur.execute(f"""
                    SELECT source_table AS table1, target_table AS table2,
                           'lineage' AS join_type, 0 AS datasource_id
                    FROM adh_data_lineage
                    WHERE is_active = 1 AND source_table != target_table
                    {ds_filter.replace('datasource_id', 'source_datasource_id') if datasource_id else ''}
                    GROUP BY source_table, target_table
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load join relations: %s", e)
            return []
        finally:
            conn.close()

    def _load_sql_templates(self, datasource_id: int) -> list[dict]:
        """Load active SQL templates to materialize as graph nodes.

        Best-effort: if the table is absent, return [] so graph build continues.
        """
        from services.shared.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT template_id, template_name, category, intent_keywords,
                           sql_template, variables, rules, description, dialect
                    FROM adh_sql_templates
                    WHERE is_active = 1 {ds_filter}
                    ORDER BY template_id
                """, params)
                return cur.fetchall()
        except Exception as e:
            try:
                # dialect 列为迁移新增: 旧库上回落无 dialect 查询, 图谱装载不中断
                cur.execute(f"""
                    SELECT template_id, template_name, category, intent_keywords,
                           sql_template, variables, rules, description
                    FROM adh_sql_templates
                    WHERE is_active = 1 {ds_filter}
                    ORDER BY template_id
                """, params)
                return cur.fetchall()
            except Exception as e2:
                logger.warning("Failed to load SQL templates: %s", e2)
                return []
        finally:
            conn.close()

    def _merge_ontology_models(self, datasource_id: int) -> int:
        """Merge active ontology models into the graph as RDF."""
        from services.shared.common.db.metadata_db import get_metadata_conn
        from services.shared.common.rdf.ontology_to_rdf import ontology_json_to_turtle

        conn = get_metadata_conn()
        count = 0
        try:
            with conn.cursor() as cur:
                if datasource_id:
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND datasource_id = %s",
                        [datasource_id],
                    )
                else:
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models WHERE status = 'active'"
                    )
                models = cur.fetchall()

            for model in models:
                try:
                    turtle = ontology_json_to_turtle(
                        model["json_content"],
                        datasource_id=datasource_id,
                    )
                    self._store.load_turtle(turtle, datasource_id)
                    count += 1
                except Exception as e:
                    logger.warning("Failed to merge ontology model %s: %s",
                                   model.get("id"), e)
        except Exception as e:
            logger.warning("Failed to load ontology models: %s", e)
        finally:
            conn.close()

        return count
