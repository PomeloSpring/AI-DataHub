"""Graph Builder — build knowledge graph from metadata into Oxigraph.

Reads metadata from MySQL tables (adh_table_info, adh_column_metadata, etc.)
and constructs RDF triples in Oxigraph, organized by named graph per datasource.
Also merges active ontology models from adh_ontology_models.
"""

import logging
import os
import time
from typing import Any

from backend.common.rdf.namespaces import TURTLE_PREFIXES
from backend.common.rdf.sparql_client import bulk_timeout
from backend.semantics.graph_rag.oxigraph_store import (
    OxigraphStore,
    SYSTEM_DATASOURCE_ID,
    graph_target_for_model,
    is_system_scope,
)

logger = logging.getLogger(__name__)


def _strip_prefix_block(turtle: str) -> str:
    """剥掉 ontology_json_to_turtle 输出开头的 @prefix 头（合并多模型时只保留一份）。

    各模型 Turtle 都以同一 ``TURTLE_PREFIXES`` 起头；拼成一份整体加载时，重复 @prefix
    虽合法但冗余，这里去掉后续模型的头部只留正文，最后统一加一次前缀。
    """
    if turtle.startswith(TURTLE_PREFIXES):
        return turtle[len(TURTLE_PREFIXES):].lstrip()
    return turtle


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


class GraphBuildAborted(RuntimeError):
    """图重建超出总时长预算。

    为什么必须有这个上界：`datasource_id=0` 是「聚合全部数据源」语义，而
    `kind='business'` 的本体保存正好走这一路径 —— 一次保存就会触发全目录重建。
    没预算时它要么跑数十分钟，要么在客户端默认超时上逐次报错误导人。
    超出就抛错带进度，绝不返回部分完成的 success。
    """

    def __init__(self, message: str, *, stats: dict, elapsed_s: float, budget_s: float):
        super().__init__(message)
        self.stats = stats
        self.elapsed_s = elapsed_s
        self.budget_s = budget_s


class GraphBuilder:
    """Builds the RDF knowledge graph from database metadata."""

    def __init__(self, store: OxigraphStore = None):
        self._store = store or OxigraphStore()

    @staticmethod
    def _write_chunk() -> int:
        """单次 INSERT DATA 塑多少节点（往返次数的除数）。"""
        try:
            n = int(os.getenv("GRAPH_WRITE_CHUNK", "500"))
        except (TypeError, ValueError):
            n = 500
        return max(n, 1)

    @staticmethod
    def _build_budget_s() -> float:
        """整场重建的墙钟预算（秒）。<=0 视为不限，仅供显式关闭。"""
        try:
            return float(os.getenv("GRAPH_BUILD_DEADLINE_S", "600"))
        except (TypeError, ValueError):
            return 600.0

    @staticmethod
    def _check_deadline(started: float, stats: dict, stage: str, done: int, total: int) -> None:
        budget = GraphBuilder._build_budget_s()
        if budget <= 0:
            return
        elapsed = time.monotonic() - started
        if elapsed > budget:
            raise GraphBuildAborted(
                f"图重建超出预算 {budget:.0f}s（已进入 {stage} 阶段，已写 {done}/{total}）；"
                f"可调 GRAPH_BUILD_DEADLINE_S 或缩小重建范围（datasource_id=0 是全目录重建）",
                stats=dict(stats), elapsed_s=elapsed, budget_s=budget)

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
        started = time.monotonic()

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
            "datasets": 0,
        }

        try:
            # Clear existing graph for this datasource
            self._store.clear_graph(datasource_id)

            # Merge ontology FIRST: the RDF bulk load (PUT /store) replaces the
            # named graph, so it must run before the physical nodes are appended
            # via INSERT DATA — otherwise it would wipe them.
            ontology_count, ontology_failures = self._merge_ontology_models(datasource_id, kind)
            logger.info("Merged %d ontology model(s)", ontology_count)
            if ontology_failures:
                logger.error("%d ontology model(s) failed to merge into graph: %s",
                             len(ontology_failures), ontology_failures)

            # Build table nodes —— 分块批量写，不再一表一次 HTTP 往返
            tables = self._load_tables(datasource_id)
            chunk = self._write_chunk()
            for i in range(0, len(tables), chunk):
                piece = tables[i:i + chunk]
                stats["tables"] += self._store.create_table_nodes(
                    [{"name": t["table_name"],
                      "comment": t.get("table_comment", ""),
                      "business_desc": t.get("table_business_desc", "")}
                     for t in piece],
                    datasource_id=datasource_id,
                    timeout=bulk_timeout())
                self._check_deadline(started, stats, "tables",
                                     stats["tables"], len(tables))
            logger.info("Created %d table nodes", stats["tables"])

            # Build column nodes —— 同上；这一步的量级最大（全目录万级列）
            columns = self._load_columns(datasource_id)
            for i in range(0, len(columns), chunk):
                piece = columns[i:i + chunk]
                stats["columns"] += self._store.create_column_nodes(
                    [{"table": c["table_name"],
                      "column": c["column_name"],
                      "data_type": c.get("data_type", ""),
                      "comment": c.get("column_comment", "")}
                     for c in piece],
                    datasource_id=datasource_id,
                    timeout=bulk_timeout())
                self._check_deadline(started, stats, "columns",
                                     stats["columns"], len(columns))
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
                # 对象→指标(归属): 源于字典 bound_object_key, 使指标像属性一样挂在对象上
                if m.get("bound_object_key"):
                    self._store.create_object_metric_relation(
                        object_key=m["bound_object_key"],
                        metric_name=m.get("name", ""),
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

            # Build dimension nodes (与指标对称: 字典权威行, 供图谱呈现分析维度)
            dimensions = self._load_dimensions(datasource_id)
            for d in dimensions:
                self._store.create_dimension_node(
                    name=d.get("name", ""),
                    description=d.get("description", ""),
                    category=d.get("category", ""),
                    datasource_id=datasource_id,
                )
                # 对象→维度(归属): 与对象→指标对称, 维度跟着它 bound_object_key 指向的对象
                if d.get("bound_object_key"):
                    self._store.create_object_dimension_relation(
                        object_key=d["bound_object_key"],
                        dimension_name=d.get("name", ""),
                        datasource_id=datasource_id,
                    )
                if d.get("table_name") and d.get("column_name"):
                    self._store.create_dimension_column_relation(
                        dimension_name=d["name"],
                        table=d["table_name"],
                        column=d["column_name"],
                        datasource_id=datasource_id,
                    )
                stats["dimensions"] += 1
            logger.info("Created %d dimension nodes", stats["dimensions"])

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

            # Build dataset nodes (governed query assets; retriever grounds questions
            # against them via SPARQL). SQL 正文留在服务端，不入图。
            table_names = [t["table_name"] for t in tables]
            for ds in self._load_datasets(datasource_id):
                body = "\n".join([str(ds.get("sql_query") or "")] + [
                    str(v or "") for v in (ds.get("_bodies") or {}).values()])
                lowered = body.lower()
                touched = [tn for tn in table_names
                           if tn and tn.lower() in lowered]
                self._store.create_dataset_node(
                    name=ds.get("name", ""),
                    description=ds.get("description", "") or "",
                    source_type=ds.get("source_type", "") or "",
                    object_key=ds.get("object_key", "") or "",
                    variables=ds.get("variables", "") or "",
                    tables=touched,
                    datasource_id=datasource_id,
                )
                stats["datasets"] += 1
            logger.info("Created %d dataset nodes", stats["datasets"])

            # Ontology models were merged at the start (see above).

            total_triples = self._store.count_triples(datasource_id)
            logger.info("Graph build complete. Total triples: %d", total_triples)

            result = {
                # 有本体模型没入图 = 图不完整，不得谎报成功（no-silent-degradation）。
                "success": not ontology_failures,
                **stats,
                "ontology_models_merged": ontology_count,
                "ontology_merge_failures": ontology_failures,
                "total_triples": total_triples,
                "datasource_id": datasource_id,
            }
            if ontology_failures:
                result["error"] = (
                    f"{len(ontology_failures)} 个本体模型未能入图（图不完整，请查服务端日志）: "
                    + "; ".join(f"model={f.get('model_id')} err={f.get('error')}"
                                for f in ontology_failures)
                )
            return result

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
                        SELECT name, description, bound_object_key,
                               target_table AS table_name,
                               target_column AS column_name, datasource_id
                        FROM adh_metrics
                        WHERE is_active = 1 AND bound_object_key IN ({ph})
                    """, keys)
                    return cur.fetchall()
                ds_filter, params = _ds_scope(datasource_id)
                cur.execute(f"""
                    SELECT name, description, bound_object_key,
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

    def _load_dimensions(self, datasource_id: int) -> list[dict]:
        """加载分析维度字典行(与指标对称): 系统域仅收录绑到系统模型对象的维度。"""
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if is_system_scope(datasource_id):
                    keys = _system_object_keys()
                    if not keys:
                        return []
                    ph = ",".join(["%s"] * len(keys))
                    cur.execute(f"""
                        SELECT name, description, category, bound_object_key,
                               target_table AS table_name,
                               target_column AS column_name, datasource_id
                        FROM adh_dimensions
                        WHERE is_active = 1 AND bound_object_key IN ({ph})
                    """, keys)
                    return cur.fetchall()
                ds_filter, params = _ds_scope(datasource_id)
                cur.execute(f"""
                    SELECT name, description, category, bound_object_key,
                           target_table AS table_name,
                           target_column AS column_name, datasource_id
                    FROM adh_dimensions
                    WHERE is_active = 1 {ds_filter}
                """, params)
                return cur.fetchall()
        except Exception as e:
            logger.warning("Failed to load dimensions: %s", e)
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

    def _load_datasets(self, datasource_id: int) -> list[dict]:
        """Load active datasets to materialize as graph nodes.

        Best-effort: if the table/columns are absent, return [] so graph build continues.
        数据集是面向业务取数的查询资产，系统域图不收录。
        """
        if is_system_scope(datasource_id):
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.semantics.sql_params import parse_json_obj

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ds_filter, params = _ds_scope(datasource_id)
                cur.execute(f"""
                    SELECT name, description, source_type, object_key, sql_query,
                           sql_bodies, variables
                    FROM adh_datasets
                    WHERE status = 'active' {ds_filter}
                    ORDER BY name
                """, params)
                rows = cur.fetchall() or []
            for r in rows:
                r["_bodies"] = parse_json_obj(r.get("sql_bodies"), {}) or {}
            return rows
        except Exception as e:
            logger.warning("Failed to load datasets for graph: %s", e)
            return []
        finally:
            conn.close()

    def build_system_graph(self) -> dict[str, Any]:
        """构建/重建 AS-BOT 专用的**系统域**图 (ds:-1)。

        收录: 系统本体模型(datasource_id 空/0) + 系统元数据表(datasource_id 空/0)
        + 绑定到系统模型对象的字典指标; 排除业务字典/术语/SQL模板/数据源节点。
        """
        return self.build_from_metadata(SYSTEM_DATASOURCE_ID)

    def _merge_ontology_models(self, datasource_id: int, kind: str = "") -> tuple[int, list[dict]]:
        """Merge active ontology models into the graph as RDF.

        按 **kind** 归属，不用 `datasource_id=0` 判系统域（业务本体在多站点下 ds_id 也是 0，
        旧口径会把业务本体拉进系统图 ds:-1、污染 AS-BOT 图谱）。
        目标图：business→ds:0(跨源业务聚合图，多本体共存) / system→ds:-1(系统图)；
        具体数据源命名图（ds:N）只保留物理血缘，不再归并本体（源本体已退役）。
        选图口径收敛到 `graph_target_for_model`，与状态对账
        （ontology_kb_sync.model_sync_status）共用一份，不再两处各写一套。

        返回 ``(成功入图的模型数, 失败清单)``。失败清单非空时调用方须判失败（不再静默）。
        """
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.common.rdf.ontology_to_rdf import ontology_json_to_turtle

        # kind 为空时按 datasource_id 推断（兼容旧调用）：-1=系统域，0=跨源业务聚合（ds:0），>0=具体源（只建物理血缘）。
        # 源本体已退役：不再把 datasource_id>0 推为 "source"（那会去 merge 已不存在的 kind='source' 本体）。
        if not kind:
            kind = "system" if is_system_scope(datasource_id) else ("" if datasource_id else "business")

        # 目标图按 kind（不按 datasource_id）
        target_ds = graph_target_for_model(kind, datasource_id)

        conn = get_metadata_conn()
        failures: list[dict] = []
        models: list[dict] = []
        try:
            with conn.cursor() as cur:
                if kind == "system":
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND kind = 'system'"
                    )
                elif kind == "business":
                    # 多业务本体并存（WS0 解除 kind 级单例）：全部 active business 合并进 ds:0 跨源聚合图。
                    cur.execute(
                        "SELECT id, json_content FROM adh_ontology_models "
                        "WHERE status = 'active' AND kind = 'business'"
                    )
                else:
                    # 源本体已退役（WS2 合并为 business）：具体数据源的命名图只保留物理血缘层
                    # （表/列/血缘由 build_from_metadata 另行写入），不再 merge 本体对象。
                    return 0, []
                models = cur.fetchall()

            # 关键：Oxigraph `PUT /store?graph=<uri>` 对指定命名图是「替换」而非追加
            # （noreplace=true 只防清空整个 store，不防该命名图被覆盖）。多业务本体若逐个
            # load_turtle 到同一 ds:0，后一个会盖掉前一个 → 只剩最后加载的模型（历史缺陷：
            # 联耀/ARMS 被数据仓库模型覆盖，ds:0 只剩 4 个类）。因此把所有模型的 Turtle 去重
            # 前缀后拼成一份、单次 load：一份 PUT 即包含全部模型对象，彼此共存。
            bodies: list[str] = []
            for model in models:
                try:
                    turtle = ontology_json_to_turtle(
                        model["json_content"],
                        datasource_id=target_ds,
                    )
                    bodies.append(_strip_prefix_block(turtle))
                except Exception as e:
                    logger.error("Failed to convert ontology model %s to Turtle: %s",
                                 model.get("id"), e, exc_info=True)
                    failures.append({"model_id": model.get("id"), "error": str(e)})

            count = 0
            if bodies:
                try:
                    self._store.load_turtle(
                        TURTLE_PREFIXES + "\n\n" + "\n\n".join(bodies), target_ds)
                    count = len(bodies)
                except Exception as e:
                    # 单次整体加载失败 = 本轮所有模型都没入图，如实上报（不谎报成功）。
                    logger.error("Failed to bulk-load merged ontology turtle into ds:%s: %s",
                                 target_ds, e, exc_info=True)
                    failures.append({"model_id": None, "error": f"bulk load failed: {e}"})
        except Exception as e:
            logger.error("Failed to load ontology models: %s", e, exc_info=True)
            failures.append({"model_id": None, "error": str(e)})
            count = 0
        finally:
            conn.close()

        return count, failures
