"""Graph Builder — build knowledge graph from metadata into Oxigraph.

Reads metadata from MySQL tables (adh_table_info, adh_column_metadata, etc.)
and constructs RDF triples in Oxigraph, organized by named graph per datasource.
Also merges active ontology models from adh_ontology_models.
"""

import logging
from typing import Any

from backend.modules.mind.rag.graph_rag.oxigraph_store import (
    OxigraphStore,
    SYSTEM_DATASOURCE_ID,
    is_system_scope,
)

logger = logging.getLogger(__name__)


def _ds_scope(datasource_id: int, col: str = "datasource_id") -> tuple[str, list]:
    """返回 (SQL 过滤子句, params), 按三种域区分:

    - 系统域 (datasource_id == SYSTEM_DATASOURCE_ID/-1): 仅 `col = 0 OR col IS NULL`（系统元数据）;
    - 业务域 (datasource_id > 0): `col = %s OR col = 0`（本域 + 全局共享 ds=0 行）;
    - 聚合 (datasource_id == 0): 不过滤（全部数据源）—— ChatBI 全局图语义保持不变。
    """
    if is_system_scope(datasource_id):
        return f"AND ({col} = 0 OR {col} IS NULL)", []
    if datasource_id:
        return f"AND ({col} = %s OR {col} = 0)", [datasource_id]
    return "", []


def _system_object_keys() -> list[str]:
    """系统域 active 模型(datasource_id 空/0)的对象 key 清单。

    字典表(adh_metrics 等)的 datasource_id 恒为 0(全局行), 其业务/系统归属
    只能由 bound_object_key 所在模型判定 —— 按 datasource_id 筛字典等于没筛
    (GMV/订单数串进系统图的根因)。
    """
    import json
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    keys: set[str] = set()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT JSON_EXTRACT(json_content, '$.objects[*].key') AS objs "
                "FROM adh_ontology_models "
                "WHERE status = 'active' AND (datasource_id IS NULL OR datasource_id = 0) "
                "AND json_content IS NOT NULL"
            )
            for row in cur.fetchall():
                raw = row.get("objs") if isinstance(row, dict) else row[0]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    if isinstance(parsed, list):
                        keys.update(str(k) for k in parsed if k)
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        logger.warning("Failed to load system object keys: %s", e)
    finally:
        conn.close()
    return sorted(keys)


class GraphBuilder:
    """Builds the RDF knowledge graph from database metadata."""

    def __init__(self, store: OxigraphStore = None):
        self._store = store or OxigraphStore()

    def build_from_metadata(self, datasource_id: int = 0, kind: str = "") -> dict[str, Any]:
        """Build the knowledge graph from MySQL metadata tables.

        Args:
            datasource_id: Scope to a specific datasource (0 = all).
            kind: 本体类型 source/business/system —— 决定本体对象图的归属与目标 graph
                （业务本体跨源 ds_id=0，按 datasource_id 判会错拉系统图）。

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
            ontology_count = self._merge_ontology_models(datasource_id, kind)
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

            # Build datasource nodes —— 系统域不枚举业务数据源(test-alb 等名称不得进系统图)
            datasources = [] if is_system_scope(datasource_id) else self._load_datasources()
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
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
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
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
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
        # 业务术语是"尚未绑定对象/字典的业务黑话"缓冲区(建模规范§5), 属行业词汇而非
        # 平台运营知识 —— 系统域图不收录(宁缺勿错)。
        if is_system_scope(datasource_id):
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
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
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if is_system_scope(datasource_id):
                    # 字典行 datasource_id 恒为 0, 只能按绑定对象所在模型判定归属:
                    # 仅收录绑到系统域模型对象的指标; 未绑定(bound_object_key 空)一律排除。
                    keys = _system_object_keys()
                    if not keys:
                        return []
                    ph = ",".join(["%s"] * len(keys))
                    cur.execute(f"""
                        SELECT name, description,
                               target_table AS table_name,
                               target_column AS column_name, datasource_id
                        FROM adh_metrics
                        WHERE is_active = 1 AND bound_object_key IN ({ph})
                    """, keys)
                    return cur.fetchall()
                ds_filter, params = _ds_scope(datasource_id)
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
        from backend.common.db.metadata_db import get_metadata_conn
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
        """Load JOIN relations from table_relations, falling back to data_lineage.

        adh_data_lineage 的列是 source_name/target_name(source_type/target_type 区分
        实体种类), **没有** source_table/target_table, 也没有任何 datasource 列。
        旧写法按旧 schema 取列, 一执行就 MySQL 1054, 被下面的 except 吞成 warning
        后静默返回空 —— 图谱缺边且无人察觉。这里对齐真实 schema, 并显式表达域边界。
        """
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
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

                if is_system_scope(datasource_id):
                    # adh_data_lineage 无 datasource 列, 无法按域过滤; 其内容是业务表
                    # 血缘, 吸入系统图会造成业务元数据串域(as-bot-system-waker §1)。
                    # 这是有意的域边界, 不是失败, 故记 debug 而非 warning。
                    logger.debug("[graph] 系统域图不吸收业务血缘关系(域边界)")
                    return []

                # Fallback: 业务/聚合域取表->表血缘(排除 datasource->* 等非表实体)
                cur.execute("""
                    SELECT source_name AS table1, target_name AS table2,
                           'lineage' AS join_type, 0 AS datasource_id
                    FROM adh_data_lineage
                    WHERE is_active = 1
                      AND source_type = 'table' AND target_type = 'table'
                      AND source_name != target_name
                    GROUP BY source_name, target_name
                """)
                return cur.fetchall()
        except Exception as e:
            # 真正的意外异常才走到这里(列名/表名错配已在上面对齐 schema)。
            # 旁路降级: 图谱关系边缺失不影响取数主链路, 但必须留 warning 可诊断。
            logger.warning("Failed to load join relations: %s", e)
            return []
        finally:
            conn.close()

    def _load_sql_templates(self, datasource_id: int) -> list[dict]:
        """Load active SQL templates to materialize as graph nodes.

        Best-effort: if the table is absent, return [] so graph build continues.
        """
        # 模板是面向业务取数的查询资产(含业务表 SQL 文本), 系统域图不收录。
        if is_system_scope(datasource_id):
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
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

    def build_system_graph(self) -> dict[str, Any]:
        """构建/重建 AS-BOT 专用的**系统域**图 (ds:-1)。

        收录: 系统本体模型(datasource_id 空/0) + 系统元数据表(datasource_id 空/0)
        + 绑定到系统模型对象的字典指标; 排除业务字典/术语/SQL模板/数据源节点。
        """
        return self.build_from_metadata(SYSTEM_DATASOURCE_ID)

    def _merge_ontology_models(self, datasource_id: int, kind: str = "") -> int:
        """Merge active ontology models into the graph as RDF.

        按 **kind** 归属，不用 `datasource_id=0` 判系统域（业务本体跨源 ds_id 也是 0，
        旧口径会把业务本体拉进系统图 ds:-1、污染 AS-BOT 图谱）。
        目标图：business→ds:0(业务图) / system→ds:-1(系统图) / source→ds:N(源图)。
        """
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle

        # kind 为空时按 datasource_id 推断（兼容旧调用）：-1=系统域，>0=源，0=聚合
        if not kind:
            kind = "system" if is_system_scope(datasource_id) else ("source" if datasource_id else "")

        # 目标图按 kind（不按 datasource_id）
        if kind == "system":
            target_ds = SYSTEM_DATASOURCE_ID
        elif kind == "business":
            target_ds = 0          # 业务本体独立图（跨源连通图）
        elif kind == "source":
            target_ds = datasource_id
        else:
            target_ds = SYSTEM_DATASOURCE_ID if is_system_scope(datasource_id) else datasource_id

        conn = get_metadata_conn()
        count = 0
        try:
            with conn.cursor() as cur:
                if kind == "system":
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND kind = 'system'"
                    )
                elif kind == "business":
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND kind = 'business'"
                    )
                elif kind == "source" or datasource_id:
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND kind = 'source' AND datasource_id = %s",
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
                        datasource_id=target_ds,
                    )
                    self._store.load_turtle(turtle, target_ds)
                    count += 1
                except Exception as e:
                    logger.warning("Failed to merge ontology model %s: %s",
                                   model.get("id"), e)
        except Exception as e:
            logger.warning("Failed to load ontology models: %s", e)
        finally:
            conn.close()

        return count
