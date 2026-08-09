"""Ontology-First Strategy — 基于业务本体对象的检索。

Pipeline:
  Step 1: 问题向量召回激活的本体对象（adh_ontology_objects）
  Step 2: 从对象 JSON 展开物理元数据（主表/属性列/链接表）
  Step 3: 并行辅助检索（术语/模板/关系/数据集）
  Fallback: 无激活模型或召回为空 → hybrid 策略

输出与 base.RetrievalStrategy 一致的标准 dict，下游 prompt_builder 零改动。
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from services.datamind.rag.strategies.base import RetrievalStrategy

logger = logging.getLogger(__name__)


class OntologyFirstStrategy(RetrievalStrategy):
    """本体对象优先检索：对象召回 → 展开物理上下文。"""

    name = "ontology_first"

    def __init__(self, top_objects: int = 5):
        self.top_objects = top_objects

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # ── Step 1: 本体对象召回 ──
        objects = self._recall_objects(question, datasource_id)
        if not objects:
            logger.info("[ontology_first] no active objects, falling back to hybrid")
            return self._fallback_hybrid(question, selected_tables, target_tables, keywords, datasource_id)

        # ── Step 2: 展开物理元数据 ──
        table_names = self._expand_tables(objects, selected_tables, target_tables)
        if not table_names:
            logger.warning("[ontology_first] objects matched no physical tables, fallback")
            return self._fallback_hybrid(question, selected_tables, target_tables, keywords, datasource_id)

        from services.datamind.rag.rag_retriever import (
            _get_table_info_for_names,
            _get_columns_for_tables,
        )

        table_info = _get_table_info_for_names(table_names, datasource_id)
        column_metadata = _get_columns_for_tables(table_names, datasource_id)

        # ── Step 3: 并行辅助检索 ──
        sql_templates, business_terms, table_relations, saved_datasets = (
            self._retrieve_auxiliary(question, keywords, table_names, vec_literal, datasource_id)
        )

        logger.info(
            "[ontology_first] objects=%s → tables=%s, columns=%d",
            [o.get("object_key") for o in objects], table_names, len(column_metadata),
        )

        return {
            "table_info": table_info,
            "column_metadata": column_metadata,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": "ontology_first",
        }

    # ── 对象召回 ────────────────────────────────────────────────────

    def _recall_objects(self, question: str, datasource_id: int) -> list[dict]:
        """向量召回激活的本体对象，返回解析后的对象 JSON 列表。"""
        from services.shared.common.llm.embedding import generate_embedding
        from services.shared.common.vector import get_vector_store
        from services.shared.common.db.metadata_db import get_metadata_conn

        filters = {"is_active": 1}
        if datasource_id:
            filters["datasource_id"] = datasource_id

        store = get_vector_store()
        hits = store.search(
            "adh_ontology_objects",
            generate_embedding(question),
            limit=self.top_objects,
            filters=filters,
            output_columns=["model_id", "object_key"],
        )
        if not hits:
            return []

        # 取所属模型 JSON，按 object_key 展开
        model_ids = list({h["model_id"] for h in hits})
        placeholders = ", ".join(["%s"] * len(model_ids))
        try:
            with get_metadata_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, json_content FROM adh_ontology_models WHERE id IN ({placeholders})",
                        model_ids,
                    )
                    model_jsons = {r["id"]: r["json_content"] for r in cur.fetchall()}
        except Exception as e:
            logger.warning("[ontology_first] load model json failed: %s", e)
            return []

        objects = []
        seen = set()
        for h in hits:
            try:
                doc = json.loads(model_jsons.get(h["model_id"], "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            for obj in doc.get("objects", []):
                if obj.get("key") == h["object_key"] and obj["key"] not in seen:
                    seen.add(obj["key"])
                    objects.append(obj)
        return objects

    # ── 物理表展开 ──────────────────────────────────────────────────

    def _expand_tables(
        self,
        objects: list[dict],
        selected_tables: list[str],
        target_tables: list[str],
    ) -> list[str]:
        """从对象定义提取物理表名：主表 + 属性列前缀 + 链接 join 涉及的表。"""
        tables: list[str] = []

        def _add(name: str):
            if name and name not in tables:
                tables.append(name)

        for obj in objects:
            _add(obj.get("primary_table", ""))
            for p in obj.get("properties", []):
                col = p.get("column", "")
                if "." in col:
                    _add(col.split(".", 1)[0])
            for lk in obj.get("links", []):
                # 从 join 表达式 "a.col = b.col" 提取表名
                for m in re.findall(r"([a-zA-Z_][\w]*)\s*\.\s*\w+", lk.get("join", "")):
                    _add(m)

        # 意图识别/预选表并入，保证不丢用户上下文
        for t in (selected_tables or []) + (target_tables or []):
            _add(t)

        return tables[:15]

    # ── 辅助检索 ────────────────────────────────────────────────────

    def _retrieve_auxiliary(
        self,
        question: str,
        keywords: list[str],
        table_names: list[str],
        vec_literal: str,
        datasource_id: int,
    ) -> tuple:
        """并行检索模板/术语/关系/数据集（与 hybrid 一致）。"""
        from services.datamind.rag.rag_retriever import (
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_table_relations,
            retrieve_saved_datasets,
        )

        sql_templates, business_terms, table_relations, saved_datasets = [], [], [], []

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_sql = pool.submit(retrieve_sql_templates, question, 5, vec_literal, datasource_id)
            f_terms = pool.submit(retrieve_business_terms, question, 20, keywords, vec_literal, datasource_id)
            f_rels = pool.submit(retrieve_table_relations, question, 20, table_names, vec_literal, datasource_id)
            f_ds = pool.submit(retrieve_saved_datasets, question)

            for name, future in [
                ("sql_templates", f_sql),
                ("business_terms", f_terms),
                ("table_relations", f_rels),
                ("saved_datasets", f_ds),
            ]:
                try:
                    result = future.result()
                    if name == "sql_templates":
                        sql_templates = result
                    elif name == "business_terms":
                        business_terms = result
                    elif name == "table_relations":
                        table_relations = result
                    else:
                        saved_datasets = result
                except Exception as e:
                    logger.warning("%s failed: %s", name, e)

        return sql_templates, business_terms, table_relations, saved_datasets

    # ── Fallback ────────────────────────────────────────────────────

    def _fallback_hybrid(
        self,
        question: str,
        selected_tables: list[str],
        target_tables: list[str],
        keywords: list[str],
        datasource_id: int,
    ) -> dict:
        from services.datamind.rag.strategies.hybrid import HybridStrategy

        result = HybridStrategy().retrieve(
            question,
            selected_tables=selected_tables,
            target_tables=target_tables,
            keywords=keywords,
            datasource_id=datasource_id,
        )
        result["rag_source"] = f"ontology_first:fallback:{result.get('rag_source', 'hybrid')}"
        return result
