"""Agent Pipeline Tools — tool execution, hooks, and result truncation.

Contains:
- _execute_system_tool: execute a system tool by name
- _pre_tool_use_hook / _post_tool_use_hook: lifecycle hooks
- _truncate_tool_result: per-tool result size management
"""

import json
import logging
import math
from decimal import Decimal

from services.datamind.nl2sql.orchestrator.agent_constants import TOOL_RESULT_MAX_CHARS, DEFAULT_TOOL_RESULT_MAX

logger = logging.getLogger(__name__)


# ── Utilities ────────────────────────────────────────────────────────

def _sanitize_row(row: dict) -> dict:
    """Sanitize a single row for JSON serialization."""
    for k, v in row.items():
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            row[k] = None
        elif isinstance(v, Decimal):
            row[k] = float(v)
        elif hasattr(v, "isoformat"):
            row[k] = v.isoformat()
        elif isinstance(v, bytes):
            row[k] = v.decode("utf-8", errors="replace")
        elif isinstance(v, (set, frozenset)):
            row[k] = list(v)
    return row


# ── Tool Result Truncation ──────────────────────────────────────────

def _truncate_tool_result(tool_name: str, result: str) -> str:
    """Truncate tool result based on per-tool budget. Returns original if within budget."""
    max_chars = TOOL_RESULT_MAX_CHARS.get(tool_name, DEFAULT_TOOL_RESULT_MAX)
    if max_chars is None or len(result) <= max_chars:
        return result

    truncated = result[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.7:
        truncated = truncated[:last_newline]
    omitted = result.count("\n") - truncated.count("\n")
    return f"{truncated}\n… {omitted} additional line(s) omitted."


# ── Hooks ────────────────────────────────────────────────────────────

async def pre_tool_use_hook(
    tool_name: str,
    tool_input: dict,
    agent_context: dict,
) -> dict:
    """PreToolUse hook — auto-inject parameters, log, warn.

    Returns possibly modified tool_input.
    """
    if tool_name == "retrieve_metadata" and not tool_input.get("question"):
        tool_input["question"] = agent_context.get("question", "")

    if tool_name == "execute_sql":
        validated = agent_context.get("validated_sqls", set())
        sql = tool_input.get("sql", "")
        if sql and sql not in validated:
            logger.info("[Hook] execute_sql called without prior validate_sql")

    logger.info("[Hook] PreToolUse: %s(%s)", tool_name,
                json.dumps(tool_input, ensure_ascii=False)[:200])

    return tool_input


async def post_tool_use_hook(
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    agent_context: dict,
) -> str:
    """PostToolUse hook — truncate results, detect anomalies.

    Returns possibly modified tool_output.
    """
    if tool_name == "validate_sql":
        try:
            parsed = json.loads(tool_output)
            if parsed.get("sql"):
                agent_context.setdefault("validated_sqls", set()).add(parsed["sql"])
        except (json.JSONDecodeError, TypeError):
            pass

    truncated = _truncate_tool_result(tool_name, tool_output)

    # Check if this tool result is very large and might need immediate compaction
    if len(tool_output) > 10000:  # > 10KB
        logger.warning(
            "[Hook] PostToolUse: %s result is very large (%d chars), may trigger compaction",
            tool_name, len(tool_output),
        )
        # Signal that compaction might be needed
        agent_context.setdefault("_large_results", []).append({
            "tool": tool_name,
            "size": len(tool_output),
        })

    if len(truncated) < len(tool_output):
        logger.info("[Hook] PostToolUse: %s truncated %d → %d chars",
                     tool_name, len(tool_output), len(truncated))

    return truncated


# ── System Tool Execution ────────────────────────────────────────────

async def execute_system_tool(
    tool_name: str,
    tool_input: dict,
    datasource_id: int,
    model_id: int,
    user_id: int,
    username: str,
    question: str = "",
) -> str:
    """Execute a system tool and return the result as a string."""
    try:
        # ── Context Gathering ──────────────────────────────────────

        if tool_name == "list_tables":
            from services.datamind.rag.table_selector import _get_all_tables
            keywords = tool_input.get("keywords", [])
            if not keywords:
                return json.dumps({
                    "error": "必须提供 keywords 列表。如果无法确定要搜索什么，请调用 ask_user 向用户询问。",
                }, ensure_ascii=False)
            all_tables = _get_all_tables(datasource_id)
            result = {}
            for kw in keywords:
                kw_lower = kw.lower().strip()
                if not kw_lower:
                    continue
                matched = [
                    {"table_name": t["table_name"], "comment": t.get("table_comment", "")}
                    for t in all_tables
                    if kw_lower in t.get("table_name", "").lower()
                    or kw_lower in t.get("table_comment", "").lower()
                ][:10]
                result[kw] = matched
            return json.dumps({"results": result}, ensure_ascii=False)

        elif tool_name == "select_tables":
            from services.datamind.rag.table_selector import select_tables
            tables = select_tables(
                tool_input["question"], top_k=15, datasource_id=datasource_id,
            )
            return json.dumps({"tables": tables}, ensure_ascii=False)

        elif tool_name == "search_columns":
            from services.shared.common.db.metadata_db import get_metadata_conn


            keyword = tool_input["keyword"]
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    cur.execute(
                        f"SELECT table_name, column_name, data_type, column_comment "
                        f"FROM adh_column_metadata "
                        f"WHERE is_active = 1 {ds_filter} "
                        f"AND (column_name LIKE %s OR column_comment LIKE %s) "
                        f"LIMIT 30",
                        (f"%{keyword}%", f"%{keyword}%"),
                    )
                    columns = cur.fetchall()
            finally:
                conn.close()

            return json.dumps({
                "keyword": keyword,
                "total": len(columns),
                "columns": columns,
            }, ensure_ascii=False, default=str)

        elif tool_name == "retrieve_metadata":
            from services.datamind.rag.rag_retriever import (
                _get_table_info_for_names, _get_columns_for_tables,
                retrieve_sql_templates, retrieve_business_terms, retrieve_table_relations,
            )
            from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            table_names = tool_input["table_names"]
            search_question = tool_input.get("question", "") or question

            table_info = _get_table_info_for_names(table_names, datasource_id)
            columns = _get_columns_for_tables(table_names, datasource_id)

            from services.datamind.nl2sql.prompt.prompt_builder import _to_m_schema, _to_er_diagram, _to_terminologies, _to_sql_examples
            schema_text = _to_m_schema(table_info, columns)

            vec_literal = ""
            if search_question:
                try:
                    vec_literal = embedding_to_sql_literal(generate_embedding(search_question))
                except Exception:
                    pass

            templates = retrieve_sql_templates(search_question, 5, vec_literal, datasource_id)
            terms = retrieve_business_terms(search_question, 20, vec_literal=vec_literal, datasource_id=datasource_id)
            relations = retrieve_table_relations(search_question, 20, table_names, vec_literal, datasource_id)

            er_text = _to_er_diagram(relations)
            terms_text = _to_terminologies(terms)
            examples_text = _to_sql_examples(templates)

            parts = [f"## 表结构\n{schema_text}"]
            if er_text:
                parts.append(f"\n{er_text}")
            if terms_text:
                parts.append(f"\n{terms_text}")
            if examples_text:
                parts.append(f"\n{examples_text}")

            logger.info("[Agent] retrieve_metadata: tables=%d, cols=%d, templates=%d, terms=%d, relations=%d",
                        len(table_info), len(columns), len(templates), len(terms), len(relations))

            return "\n".join(parts)

        elif tool_name == "get_sample_data":
            from services.datamind.nl2sql.sql.query_executor import execute_query

            table_name = tool_input["table_name"]
            columns = tool_input.get("columns", [])
            col_str = ", ".join(f"`{c}`" for c in columns) if columns else "*"
            sql = f"SELECT {col_str} FROM `{table_name}` LIMIT 5"

            df, elapsed_ms, row_count = execute_query(sql, datasource_id)
            cols = list(df.columns) if not df.empty else []
            rows = [_sanitize_row(row) for row in df.to_dict(orient="records")] if not df.empty else []

            return json.dumps({
                "table": table_name,
                "columns": cols,
                "rows": rows,
                "row_count": row_count,
            }, ensure_ascii=False, default=str)

        elif tool_name == "search_business_terms":
            from services.datamind.rag.rag_retriever import retrieve_business_terms
            from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            keywords = tool_input["keywords"]
            q = " ".join(keywords)
            try:
                vec_literal = embedding_to_sql_literal(generate_embedding(q))
            except Exception:
                vec_literal = None

            terms = retrieve_business_terms(
                q, 20, keywords=keywords,
                vec_literal=vec_literal, datasource_id=datasource_id,
            )
            return json.dumps({"terms": terms[:10]}, ensure_ascii=False, default=str)

        elif tool_name == "search_relations":
            import pymysql as _pymysql
            from services.shared.common.config import (
                DORIS_HOST as _H, DORIS_PORT as _P, DORIS_USER as _U,
                DORIS_PASSWORD as _PW, METADATA_DB_DATABASE as _DB,
            )

            table_names = tool_input["table_names"]
            if not table_names:
                return json.dumps({"error": "必须提供 table_names"}, ensure_ascii=False)

            conn = _pymysql.connect(
                host=_H, port=_P, user=_U, password=_PW,
                database=_DB, charset="utf8mb4",
            )
            try:
                with conn.cursor() as cur:
                    ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                    placeholders = ",".join(["%s"] * len(table_names))
                    cur.execute(
                        f"SELECT source_table, source_column, target_table, target_column, "
                        f"relation_type, description "
                        f"FROM adh_table_relations "
                        f"WHERE is_active = 1 {ds_filter} "
                        f"AND (source_table IN ({placeholders}) OR target_table IN ({placeholders}))",
                        table_names + table_names,
                    )
                    relations = cur.fetchall()
            finally:
                conn.close()

            return json.dumps({
                "table_names": table_names,
                "total": len(relations),
                "relations": relations,
            }, ensure_ascii=False, default=str)

        # ── SQL Generation & Execution ─────────────────────────────

        elif tool_name == "generate_sql":
            from services.datamind.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
            from services.shared.common.llm.llm_client import generate_sql as llm_generate_sql
            from services.datamind.rag.rag_retriever import (
                retrieve_all, _get_table_info_for_names, _get_columns_for_tables,
                retrieve_sql_templates, retrieve_business_terms, retrieve_table_relations,
            )
            from services.datamind.nl2sql.sql.query_executor import _get_ds_conn_params
            from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

            gen_question = tool_input["question"]
            agent_context_str = tool_input.get("context", "")

            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())

            if agent_context_str:
                import re as _re_tables
                recovered_tables = list(dict.fromkeys(
                    m.group(1) for m in _re_tables.finditer(
                        r'#\s*Table:\s*(\w+)', agent_context_str
                    )
                ))

                if recovered_tables:
                    table_info = _get_table_info_for_names(recovered_tables, datasource_id)
                    columns = _get_columns_for_tables(recovered_tables, datasource_id)
                    try:
                        vec = embedding_to_sql_literal(generate_embedding(gen_question))
                    except Exception:
                        vec = ""
                    templates = retrieve_sql_templates(gen_question, 5, vec, datasource_id)
                    terms = retrieve_business_terms(gen_question, 20, vec_literal=vec, datasource_id=datasource_id)
                    relations = retrieve_table_relations(gen_question, 20, recovered_tables, vec, datasource_id)
                else:
                    rag = retrieve_all(gen_question, datasource_id=datasource_id)
                    table_info = rag["table_info"]
                    columns = rag["column_metadata"]
                    templates = rag["sql_templates"]
                    terms = rag["business_terms"]
                    relations = rag.get("table_relations", [])
            else:
                rag = retrieve_all(gen_question, datasource_id=datasource_id)
                table_info = rag["table_info"]
                columns = rag["column_metadata"]
                templates = rag["sql_templates"]
                terms = rag["business_terms"]
                relations = rag.get("table_relations", [])

            messages = build_nl2sql_prompt(
                question=gen_question,
                table_info=table_info,
                column_metadata=columns,
                sql_templates=templates,
                business_terms=terms,
                table_relations=relations,
                engine=engine,
            )

            result = llm_generate_sql(messages, model_id=model_id)
            raw = result.get("sql", "")

            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            try:
                parsed = json.loads(text)
                return json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                match = _re.search(r'\{.*\}', text, _re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                        return json.dumps(parsed, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass
                return json.dumps({
                    "success": True, "sql": text,
                    "tables": [], "chart-type": "table",
                }, ensure_ascii=False)

        elif tool_name == "get_sql_rules":
            from services.datamind.nl2sql.sql.template_loader import get_sql_prompt
            from services.datamind.nl2sql.sql.query_executor import _get_ds_conn_params

            ds_params = _get_ds_conn_params(datasource_id)
            db_type = ds_params.get("db_type", "doris")
            engine_map = {"doris": "Doris", "mysql": "MySQL", "elasticsearch": "Elasticsearch"}
            engine = engine_map.get(db_type, db_type.capitalize())

            tpl = get_sql_prompt(engine, query_limit=True)
            return tpl.get("system", "")

        elif tool_name == "validate_sql":
            from services.datamind.nl2sql.sql.sql_validator import validate_and_fix
            sql, warnings = validate_and_fix(tool_input["sql"])
            return json.dumps({"sql": sql, "warnings": warnings}, ensure_ascii=False)

        elif tool_name == "execute_sql":
            from services.datamind.nl2sql.sql.query_executor import execute_query
            from services.datamind.nl2sql.sql.sql_validator import validate_and_fix

            sql = tool_input["sql"]
            query_type = tool_input.get("query_type", "sql")

            # Pre-execution verification
            if query_type == "sql":
                import re as _re
                table_pattern = _re.compile(r'(?:FROM|JOIN)\s+`?(\w+)`?', _re.IGNORECASE)
                tables_in_sql = list(set(m.group(1).lower() for m in table_pattern.finditer(sql)))

                if tables_in_sql:
                    import pymysql as _pymysql
                    from services.shared.common.config import (
                        DORIS_HOST as _H, DORIS_PORT as _P, DORIS_USER as _U,
                        DORIS_PASSWORD as _PW, METADATA_DB_DATABASE as _DB,
                    )
                    try:
                        _conn = _pymysql.connect(
                            host=_H, port=_P, user=_U, password=_PW,
                            database=_DB, charset="utf8mb4",
                        )
                        try:
                            with _conn.cursor() as _cur:
                                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                                placeholders = ",".join(["%s"] * len(tables_in_sql))
                                _cur.execute(
                                    f"SELECT table_name FROM adh_table_info "
                                    f"WHERE is_active = 1 {ds_filter} AND LOWER(table_name) IN ({placeholders})",
                                    tables_in_sql,
                                )
                                verified = {r["table_name"].lower() for r in _cur.fetchall()}
                                missing = [t for t in tables_in_sql if t not in verified]
                        finally:
                            _conn.close()

                        if missing:
                            return json.dumps({
                                "error": f"SQL 中的以下表在元数据中不存在: {', '.join(missing)}",
                                "missing_tables": missing,
                                "hint": "请调用 select_tables 或 retrieve_metadata 确认正确的表名。",
                            }, ensure_ascii=False)

                        # Verify columns
                        col_pattern = _re.compile(r'`(\w+)`\.`(\w+)`', _re.IGNORECASE)
                        table_col_pairs = list(set(
                            (m.group(1).lower(), m.group(2).lower())
                            for m in col_pattern.finditer(sql)
                        ))
                        sql_keywords = {"select", "from", "where", "and", "or", "on", "in", "as",
                                        "group", "by", "order", "having", "limit", "join", "inner",
                                        "left", "right", "outer", "union", "insert", "update", "delete",
                                        "create", "drop", "alter", "index", "null", "not", "is", "like",
                                        "between", "exists", "case", "when", "then", "else", "end",
                                        "distinct", "count", "sum", "avg", "min", "max", "desc", "asc"}
                        table_col_pairs = [
                            (t, c) for t, c in table_col_pairs
                            if c not in sql_keywords and t not in sql_keywords
                        ]

                        if table_col_pairs:
                            try:
                                _conn2 = _pymysql.connect(
                                    host=_H, port=_P, user=_U, password=_PW,
                                    database=_DB, charset="utf8mb4",
                                )
                                try:
                                    with _conn2.cursor() as _cur2:
                                        missing_cols = []
                                        for tbl, col in table_col_pairs:
                                            ds_filter2 = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                                            _cur2.execute(
                                                f"SELECT COUNT(*) as cnt FROM adh_column_metadata "
                                                f"WHERE is_active = 1 {ds_filter2} "
                                                f"AND LOWER(table_name) = %s AND LOWER(column_name) = %s",
                                                (tbl, col),
                                            )
                                            if _cur2.fetchone()["cnt"] == 0:
                                                missing_cols.append(f"{tbl}.{col}")

                                        if missing_cols:
                                            return json.dumps({
                                                "error": f"SQL 中的以下字段在元数据中不存在: {', '.join(missing_cols)}",
                                                "hint": "请调用 retrieve_metadata 或 search_columns 确认正确的字段名。",
                                            }, ensure_ascii=False)
                                finally:
                                    _conn2.close()
                            except Exception:
                                pass
                    except Exception as _verify_err:
                        logger.warning("[Agent] Pre-execution verification failed: %s", _verify_err)

            sql, val_warnings = validate_and_fix(sql)
            if not sql:
                return json.dumps({
                    "error": "未提取到有效的 SQL 语句。请确保返回可执行的 SELECT 查询。",
                }, ensure_ascii=False)
            df, elapsed_ms, row_count = execute_query(sql, datasource_id, query_type=query_type)

            columns = list(df.columns) if not df.empty else []
            rows = [_sanitize_row(row) for row in df.head(100).to_dict(orient="records")] if not df.empty else []

            # Return summary to LLM (avoid sending full data to context)
            # Full data is sent via SSE to frontend separately
            result = {
                "columns": columns,
                "row_count": row_count,
                "elapsed_ms": elapsed_ms,
                "fixed_sql": sql if val_warnings else None,
                "warnings": val_warnings,
            }

            # Include sample rows (first 5) for LLM context
            if rows:
                result["sample_rows"] = rows[:5]
                result["has_more"] = row_count > 5

            return json.dumps(result, ensure_ascii=False, default=str)

        # ── Self-Correction ────────────────────────────────────────

        elif tool_name == "explain_error":
            error_msg = tool_input["error_message"]
            failed_sql = tool_input["failed_sql"]
            table_names = tool_input.get("table_names", [])

            analysis_parts = [f"## 错误信息\n{error_msg}", f"## 失败的 SQL\n```sql\n{failed_sql}\n```"]

            if table_names:
                try:
                    from services.datamind.rag.rag_retriever import _get_table_info_for_names, _get_columns_for_tables
                    table_info = _get_table_info_for_names(table_names, datasource_id)
                    columns = _get_columns_for_tables(table_names, datasource_id)
                    if columns:
                        col_text = "\n".join(
                            f"- `{c['table_name']}`.`{c['column_name']}` ({c.get('data_type', '')}) — {c.get('column_comment', '')}"
                            for c in columns[:50]
                        )
                        analysis_parts.append(f"## 相关表字段\n{col_text}")
                except Exception:
                    pass

            fix_suggestions = []
            if "LIMIT" in error_msg or "limit" in error_msg:
                fix_suggestions.append("在 SQL 末尾添加 `LIMIT 100`")
            if "Unknown column" in error_msg or "unknown column" in error_msg.lower():
                fix_suggestions.append("字段名可能有误，请用 search_columns 工具确认正确的字段名")
            if "Table" in error_msg and "doesn't exist" in error_msg:
                fix_suggestions.append("表名可能有误，请用 select_tables 或 list_tables 工具确认正确的表名")
            if "Syntax error" in error_msg or "syntax" in error_msg.lower():
                fix_suggestions.append("SQL 语法错误，请调用 get_sql_rules 获取当前引擎的语法规则")
            if "GROUP BY" in error_msg or "group by" in error_msg.lower():
                fix_suggestions.append("GROUP BY 子句问题：SELECT 中的非聚合字段必须出现在 GROUP BY 中")

            if fix_suggestions:
                analysis_parts.append("## 修复建议\n" + "\n".join(f"- {s}" for s in fix_suggestions))

            return "\n\n".join(analysis_parts)

        # ── Reasoning ──────────────────────────────────────────────

        elif tool_name == "think":
            return json.dumps({"status": "ok", "message": "已记录思考内容。请继续执行下一步。"}, ensure_ascii=False)

        # ── User Interaction ───────────────────────────────────────

        elif tool_name == "ask_user":
            return json.dumps({
                "__ask_user__": True,
                "question": tool_input["question"],
                "options": tool_input.get("options", []),
            }, ensure_ascii=False)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        logger.error("System tool %s failed: %s", tool_name, e, exc_info=True)
        error_msg = str(e)
        if tool_name == "execute_sql":
            failed_sql = tool_input.get("sql", "")
            return json.dumps({
                "error": error_msg,
                "failed_sql": failed_sql,
                "hint": "请调用 explain_error 工具分析错误原因，修正 SQL 后重新执行。",
            }, ensure_ascii=False)
        return json.dumps({"error": error_msg})
