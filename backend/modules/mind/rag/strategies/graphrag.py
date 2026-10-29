"""GraphRagStrategy — deterministic by-name metadata hydration (no LLM).

Agentic text→SPARQL grounding（LLM 单发生成 SPARQL）已退役；本策略仅对既有
候选表（selected_tables/target_tables）做确定性 by-name 元数据回填
（exact-name/IN 查询 — no embeddings, no BM25, no LLM）。
keywords/知识库对象 key 仍参与候选挑选（命中线索者优先），不经 LLM 解释。

Returns the uniform strategy dict consumed by downstream prompt assembly:
    table_info, column_metadata, business_terms, table_relations,
    saved_datasets, rag_source.

`saved_datasets` 是**已治理的查询资产目录**（`adh_datasets`），只回名称/描述/字段语义，
**不回 SQL 正文与数据源 id**（护栏 §7）：预制 SQL 由服务端按名执行，LLM 不需要也不应该看到它。
"""

import logging

from backend.modules.mind.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)

# Cap hydrated tables so the prompt stays focused.
_MAX_TABLES = 8


class GraphRagStrategy(RetrievalStrategy):
    """By-name hydration over pre-selected candidate tables (no LLM grounding)."""

    name = "graphrag"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
        extra_object_keys: list[str] = None,
    ) -> dict:
        candidate = list(selected_tables or target_tables or [])
        # T7: 知识库命中的对象 key 与词法 keywords 一并作为候选挑选线索
        clues = [str(k).lower() for k in (
            list(keywords or []) + [str(k) for k in (extra_object_keys or []) if k]
        ) if str(k)]
        tables = self._select_candidates(candidate, clues)

        result = empty_result("graphrag")
        result["table_info"] = self._hydrate_tables(tables, datasource_id)
        result["column_metadata"] = self._hydrate_columns(tables, datasource_id)
        result["saved_datasets"] = self._hydrate_datasets([], tables, datasource_id)
        result["business_terms"] = self._hydrate_terms([], tables, datasource_id)
        result["table_relations"] = self._hydrate_relations(tables, datasource_id)
        # agentic grounding 已退役：调试键保留但置空（下游 .get("grounding") 兼容）
        result["ontology_context"] = {"grounding": {}}
        logger.info(
            "[graphrag] hydrated tables=%d cols=%d datasets=%d terms=%d relations=%d",
            len(result["table_info"]), len(result["column_metadata"]),
            len(result["saved_datasets"]), len(result["business_terms"]),
            len(result["table_relations"]),
        )
        return result

    def _select_candidates(self, candidate: list[str], clues: list[str]) -> list[str]:
        """候选挑选：命中线索者优先（确定性排序，无 LLM），截断到 _MAX_TABLES。"""
        if not candidate:
            return []
        if not clues:
            return candidate[:_MAX_TABLES]
        hit = [t for t in candidate if any(c in str(t).lower() for c in clues)]
        rest = [t for t in candidate if t not in hit]
        return (hit + rest)[:_MAX_TABLES]

    # ── Hydration (deterministic metadata lookups, no embedding/BM25) ──

    def _hydrate_tables(self, tables: list[str], datasource_id: int) -> list[dict]:
        if not tables:
            return []
        from backend.modules.mind.rag.rag_retriever import _get_table_info_for_names
        return _get_table_info_for_names(tables, datasource_id)

    def _hydrate_columns(self, tables: list[str], datasource_id: int) -> list[dict]:
        if not tables:
            return []
        from backend.modules.mind.rag.rag_retriever import _get_columns_for_tables
        return _get_columns_for_tables(tables, datasource_id)

    def _hydrate_datasets(self, grounded: list[str], tables: list[str],
                          datasource_id: int) -> list[dict]:
        """Match governed datasets by name/object_key, or by touching a candidate table.

        匹配时可以读正文（判断碰了哪些表），但**返回给下游的只有声明层信息**：
        名称/描述/绑定对象/字段语义 —— SQL 正文与数据源 id 不外泄（护栏 §7）。
        """
        if not grounded and not tables:
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.semantics.sql_params import parse_json_obj

        gset = {str(x).strip().casefold() for x in grounded}
        tset = {str(x).strip().lower() for x in tables if x}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                # 会话源命中 = 执行源本身，**或** 该源在数据集的可用数据源范围内
                # （available_datasources 存业务名）——只比 datasource_id 会让跳源数据集
                # 在 Chat RAG 里看不见（建模者配了范围但 AI 永远提不到）。
                ds_name = ""
                if datasource_id:
                    cur.execute("SELECT name FROM adh_datasources WHERE id = %s", (int(datasource_id),))
                    _row = cur.fetchone() or {}
                    ds_name = str(_row.get("name") or "")
                if datasource_id:
                    ds_filter = ("AND (d.datasource_id = %s OR d.datasource_id = 0"
                                 " OR JSON_CONTAINS(COALESCE(d.available_datasources, '[]'), JSON_QUOTE(%s)))")
                    params = [int(datasource_id), ds_name]
                else:
                    ds_filter = ""
                    params = []
                cur.execute(f"""
                    SELECT d.name, d.description, d.source_type, d.object_key,
                           d.sql_query, d.sql_bodies, d.field_config, d.certified,
                           d.available_datasources, ds.name AS datasource_name
                    FROM adh_datasets d
                    LEFT JOIN adh_datasources ds ON ds.id = d.datasource_id
                    WHERE d.status = 'active' {ds_filter}
                """, params)
                rows = cur.fetchall()
            for r in rows:
                name = str(r.get("name") or "")
                body = "\n".join([
                    str(r.get("sql_query") or ""),
                    "\n".join(str(v or "") for v in
                              (parse_json_obj(r.get("sql_bodies"), {}) or {}).values()),
                ]).lower()
                hit = (name.casefold() in gset
                       or str(r.get("object_key") or "").casefold() in gset)
                if not hit and tset:
                    hit = any(tn in body for tn in tset)
                if not hit:
                    continue
                fields = [
                    {"field": str(c.get("field") or ""),
                     "label": str(c.get("label") or c.get("field") or ""),
                     "role": str(c.get("role") or "dimension")}
                    for c in (parse_json_obj(r.get("field_config"), []) or [])
                    if isinstance(c, dict)
                ][:30]
                entry = {"name": name,
                         "description": str(r.get("description") or ""),
                         "source_type": str(r.get("source_type") or ""),
                         "certified": bool(r.get("certified") or 0)}
                # 可用数据源只回业务名（护栏 §7）：多源数据集告知 LLM 可用 site/datasource 逐查询定源
                src_names = ([str(r.get("datasource_name") or "")]
                             + [str(x) for x in (parse_json_obj(r.get("available_datasources"), []) or [])
                                if str(x or "").strip()])
                src_names = list(dict.fromkeys(n for n in src_names if n))
                if len(src_names) > 1:
                    entry["available_datasources"] = src_names
                if fields:
                    entry["fields"] = fields
                out.append(entry)
        except Exception as e:
            logger.warning("[graphrag] dataset hydration failed: %s", e)
            return []
        finally:
            conn.close()
        # Grounded matches first (preserve model's ordering intent), then table-derived.
        def _rank(d):
            key = str(d.get("name", "")).casefold()
            order = [str(g).casefold() for g in grounded]
            for i, g in enumerate(order):
                if g == key:
                    return (0, i)
            return (1, 0)
        out.sort(key=_rank)
        return out[:8]

    def _hydrate_terms(self, grounded: list[str], tables: list[str],
                       datasource_id: int) -> list[dict]:
        """Match business terms by grounded name, or mapped to a grounded table."""
        if not grounded and not tables:
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        gset = {str(x).strip() for x in grounded}
        tset = {str(x).strip().lower() for x in tables if x}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT term_cn, term_en, description, calculation,
                           target_table, target_column
                    FROM adh_business_terms WHERE is_active = 1 {ds_filter}
                """, params)
                for r in cur.fetchall():
                    hit = str(r.get("term_cn", "")) in gset or str(r.get("term_en", "")) in gset
                    if not hit and tset and (r.get("target_table") or "").lower() in tset:
                        hit = True
                    if hit:
                        out.append(r)
        except Exception as e:
            logger.warning("[graphrag] term hydration failed: %s", e)
            return []
        finally:
            conn.close()
        return out[:20]

    def _hydrate_relations(self, tables: list[str], datasource_id: int) -> list[dict]:
        """Table relations (with join columns) involving any grounded table."""
        if not tables:
            return []
        from backend.common.db.metadata_db import get_metadata_conn
        tset = {str(x).strip() for x in tables}
        conn = get_metadata_conn()
        out: list[dict] = []
        try:
            with conn.cursor() as cur:
                ds_filter = "AND (datasource_id = %s OR datasource_id = 0)" if datasource_id else ""
                params = [datasource_id] if datasource_id else []
                cur.execute(f"""
                    SELECT source_table, source_column, target_table, target_column,
                           relation_type, join_type, description
                    FROM adh_table_relations WHERE is_active = 1 {ds_filter}
                """, params)
                for r in cur.fetchall():
                    if str(r.get("source_column", "")).strip() and str(r.get("target_column", "")).strip() \
                            and (r.get("source_table") in tset or r.get("target_table") in tset):
                        out.append(r)
        except Exception as e:
            logger.warning("[graphrag] relation hydration failed: %s", e)
            return []
        finally:
            conn.close()
        return out[:30]
