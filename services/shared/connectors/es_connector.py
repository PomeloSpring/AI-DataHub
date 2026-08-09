"""Elasticsearch Connector — ES-specific query logic for agents.

Provides:
  - es_mapping: Get index field mapping (allowed in agent mode for dynamic indices)
  - es_query: Build and execute ES queries via LLM reasoning

This module is called by ConfigurableAgent._query_datasource, not directly by agents.
The LLM decides what to query based on mapping information.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def es_mapping(index: str, datasource_id: int) -> dict:
    """Get ES index mapping. ES-specific tool, allowed in agent mode.

    Returns:
        {"success": true, "fields": {"fieldName": "type", ...}} or {"success": false, "error": "..."}
    """
    from services.shared.connectors.query_executor import _get_ds_conn_params, _build_es_client

    params = _get_ds_conn_params(datasource_id)
    es = _build_es_client(params)
    try:
        mapping = es.indices.get_mapping(index=index)
        fields = {}
        for _idx_name, idx_mapping in mapping.items():
            props = idx_mapping.get("mappings", {}).get("properties", {})
            _extract_fields(props, "", fields)
        return {"success": True, "fields": fields}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        es.close()


def es_query(question: str, index_name: str, datasource_id: int) -> tuple[str, list[dict], str]:
    """Query ES using LLM to build the query based on mapping.

    Flow:
    1. Get index mapping
    2. LLM builds query based on mapping + question
    3. Execute via execute_query

    Returns:
        (result_text, tool_calls, error_message)
    """
    from services.shared.connectors.query_executor import execute_query
    from services.shared.common.llm.llm_client import _get_model_config, _get_client_for_model

    # Step 1: Get mapping
    mapping_result = es_mapping(index_name, datasource_id)
    mapping_info = ""
    if mapping_result.get("success"):
        fields = mapping_result.get("fields", {})
        field_list = [f"  {k}: {v}" for k, v in sorted(fields.items())]
        mapping_info = f"索引 {index_name} 的字段:\n" + "\n".join(field_list)
    else:
        logger.warning("Failed to get mapping for %s: %s", index_name, mapping_result.get("error"))

    # Step 2: LLM builds the query
    query_prompt = f"""你是 ES 查询构建器。根据用户问题和索引 mapping，生成查询。

{mapping_info}

用户问题: {question}

根据 mapping 中的字段类型选择查询方式：
- keyword 字段 → term 精确匹配
- text 字段 → match_phrase 短语匹配
- 如果有 traceId 等 UUID 字段，优先用 match_phrase
- 如果有 _id 查询需求，使用 REST 模式: GET /索引名/_doc/_id值

返回 JSON: {{"query_type": "rest" 或 "dsl", "sql": "查询语句"}}
只返回 JSON，不要其他文字。"""

    try:
        config = _get_model_config()
        client = _get_client_for_model(config)
        response = client.messages.create(
            model=config.get("model_name", ""),
            max_tokens=500,
            messages=[{"role": "user", "content": query_prompt}],
        )
        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw = block.text.strip()
                break

        # Parse LLM response
        cleaned = raw.strip().strip("`").replace("json\n", "").replace("json", "")
        query_spec = json.loads(cleaned)
        query_type = query_spec.get("query_type", "dsl")
        query_sql = query_spec.get("sql", "")

        if not query_sql:
            return "", [], "LLM 未能生成查询语句"

    except Exception as e:
        logger.warning("LLM query generation failed: %s", e)
        return "", [], f"查询生成失败: {str(e)}"

    # Step 3: Execute the query
    try:
        df, elapsed_ms, row_count = execute_query(query_sql, datasource_id, query_type=query_type)

        if row_count == 0:
            return "", [], f"索引 {index_name} 中未找到匹配的数据"

        # Format results
        lines = [f"在索引 {index_name} 中找到 {row_count} 条数据 (耗时 {elapsed_ms}ms):\n"]
        for i, row in df.head(5).iterrows():
            lines.append(f"--- 记录 {i+1} ---")
            for col in df.columns:
                val = row[col]
                if val and str(val) != "nan":
                    lines.append(f"  {col}: {str(val)[:300]}")
            lines.append("")

        result_text = "\n".join(lines)
        tool_calls = [{
            "tool": f"execute_sql({query_type})",
            "arguments": {"sql": query_sql, "query_type": query_type},
            "result_preview": result_text[:200],
        }]
        return result_text, tool_calls, ""

    except Exception as e:
        return "", [], f"查询执行失败: {str(e)}"


def _extract_fields(props: dict, prefix: str, result: dict):
    """Recursively extract field names and types from ES mapping."""
    for field_name, field_info in props.items():
        full_name = f"{prefix}.{field_name}" if prefix else field_name
        es_type = field_info.get("type", "object")
        if es_type != "object":
            result[full_name] = es_type
        if "properties" in field_info:
            _extract_fields(field_info["properties"], full_name, result)
