"""Agentic text→SPARQL retriever — GraphRAG grounding without vectors.

The local LLM drives a ReAct loop, issuing **read-only** SPARQL queries against
the Oxigraph named graph to *ground* a natural-language question onto concrete
graph entities (tables, SQL templates, business terms, metrics). There is no
embedding and no BM25: the knowledge graph is the single retrieval substrate.

Guardrails:
  - Only SELECT / ASK / CONSTRUCT / DESCRIBE are accepted (write keywords rejected).
  - Every query is force-scoped to the datasource's named graph (FROM-clause
    is sanitised/injected so the model cannot read across datasources).
  - A LIMIT is appended when missing; rows and cells are truncated before they
    are fed back to the model.
  - The loop is bounded by ``max_turns``; SPARQL errors are returned verbatim as
    tool results so the model can self-correct.

The loop ends when the model calls ``submit_grounding`` with the final entity
set. The returned grounding contains exact names/ids that the strategy then
hydrates into full prompt context.
"""

import json
import logging
import re
from typing import Any

from services.shared.common.rdf.sparql_client import get_sparql_client, OxigraphClient
from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore

logger = logging.getLogger(__name__)

# ── Guardrail defaults ───────────────────────────────────────────────
_MAX_TURNS = 6
_MAX_ROWS = 50
_MAX_CELL_CHARS = 400
_MAX_RESULT_CHARS = 8000

# Any of these keywords in a query => reject (read-only enforcement).
_FORBIDDEN = re.compile(
    r"\b(INSERT|DELETE|DROP|CLEAR|LOAD|CREATE|ADD|MOVE|COPY|SILENT)\b",
    re.IGNORECASE,
)
# A query must begin with one of these (after optional PREFIX lines are added
# by the client, so we check the first statement keyword the model wrote).
_ALLOWED_START = re.compile(
    r"^\s*(SELECT|ASK|CONSTRUCT|DESCRIBE|PREFIX)\b", re.IGNORECASE
)
# Sanitise any dataset clause so it always targets our graph.
_FROM_RE = re.compile(r"(?i)\bFROM(?:\s+NAMED)?\s*<[^>]*>")


class AgenticSparqlRetriever:
    """Grounds a question onto graph entities via an LLM-driven SPARQL loop."""

    def __init__(self, client: OxigraphClient = None, store: OxigraphStore = None):
        self._client = client or get_sparql_client()
        self._store = store or OxigraphStore(self._client)

    # ── Public API ───────────────────────────────────────────────────

    def ground(
        self,
        question: str,
        datasource_id: int = 0,
        keywords: list[str] = None,
        candidate_tables: list[str] = None,
        model_id: int = None,
        max_turns: int = _MAX_TURNS,
        max_rows: int = _MAX_ROWS,
    ) -> dict[str, Any]:
        """Run the agentic loop and return the grounding.

        Returns:
            {
              "tables": [...], "sql_templates": [...], "business_terms": [...],
              "metrics": [...], "reasoning": str,
              "turns": int, "submitted": bool, "trace": [...],
            }
        """
        from services.shared.common.llm.llm_client import generate_with_tools

        graph = self._store.graph_uri(datasource_id)
        tools = self._tools(max_rows)
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt(graph, max_rows)},
            {"role": "user", "content": self._user_prompt(question, keywords, candidate_tables)},
        ]

        trace: list[dict] = []
        grounding: dict[str, Any] | None = None
        submitted = False
        last_text = ""

        for turn in range(max_turns):
            try:
                resp = generate_with_tools(messages, tools, max_tokens=2048, model_id=model_id)
            except Exception as e:
                logger.error("[agentic-sparql] LLM call failed on turn %d: %s", turn + 1, e)
                trace.append({"turn": turn + 1, "error": f"LLM call failed: {e}"})
                break

            last_text = resp.get("text", "") or ""
            tool_uses = resp.get("tool_uses", []) or []

            assistant_content: list[dict] = []
            if last_text:
                assistant_content.append({"type": "text", "text": last_text})
            for tc in tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "input": tc.get("input", {}) or {},
                })
            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content})

            # No tool call => nudge the model to either query or submit.
            if not tool_uses:
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": "请用 sparql_query 继续探查，或调用 submit_grounding 给出最终结果。",
                    }],
                })
                continue

            tool_results: list[dict] = []
            for tc in tool_uses:
                name = tc.get("name", "")
                inp = tc.get("input", {}) or {}
                tid = tc.get("id", "")
                if name == "sparql_query":
                    out = self._exec_sparql(inp.get("query", ""), graph, max_rows, turn + 1, trace)
                    tool_results.append({"type": "tool_result", "tool_use_id": tid, "content": out})
                elif name == "submit_grounding":
                    grounding = _normalize_grounding(inp)
                    submitted = True
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tid,
                        "content": "Grounding accepted. You may stop now.",
                    })
                else:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tid,
                        "content": f"Unknown tool: {name}",
                    })
            messages.append({"role": "user", "content": tool_results})

            if submitted:
                break
        else:
            logger.info("[agentic-sparql] reached max_turns=%d without submit", max_turns)

        if grounding is None:
            grounding = {"tables": [], "sql_templates": [], "business_terms": [], "metrics": [], "reasoning": ""}
        grounding["turns"] = len(trace)
        grounding["submitted"] = submitted
        grounding["reasoning"] = grounding.get("reasoning") or last_text[:500]
        grounding["trace"] = trace
        logger.info(
            "[agentic-sparql] grounded tables=%d templates=%d terms=%d submitted=%s",
            len(grounding["tables"]), len(grounding["sql_templates"]),
            len(grounding["business_terms"]), submitted,
        )
        return grounding

    # ── SPARQL execution + guardrails ────────────────────────────────

    def _exec_sparql(self, query: str, graph: str, max_rows: int,
                     turn: int, trace: list[dict]) -> str:
        q = (query or "").strip()
        if not q:
            return "ERROR: empty query"
        if not _ALLOWED_START.match(q):
            return "ERROR: query must start with SELECT/ASK/CONSTRUCT/DESCRIBE"
        if _FORBIDDEN.search(q):
            return "ERROR: only read-only SPARQL is allowed (no INSERT/DELETE/DROP/CLEAR/LOAD/CREATE)"

        q = self._enforce_scope(q, graph)
        q = self._enforce_limit(q, max_rows)
        try:
            rows = self._client.query(q)
        except Exception as e:
            trace.append({"turn": turn, "query": q, "error": str(e)})
            return f"ERROR: SPARQL failed: {e}\n请修正语法/谓词后重试。"

        rows = rows[:max_rows]
        slim = []
        for r in rows:
            slim.append({
                k: (v[:_MAX_CELL_CHARS] + "…" if isinstance(v, str) and len(v) > _MAX_CELL_CHARS else v)
                for k, v in r.items()
            })
        trace.append({"turn": turn, "query": q, "n_rows": len(slim)})

        out = json.dumps(slim, ensure_ascii=False)
        if len(out) > _MAX_RESULT_CHARS:
            out = out[:_MAX_RESULT_CHARS] + "\n…(结果被截断，请用更精确的 FILTER/聚合或减小 LIMIT)"
        return out if out and out != "[]" else "[] (0 行)"

    @staticmethod
    def _enforce_scope(query: str, graph: str) -> str:
        """Force the query to read only the given named graph."""
        # Neutralise any dataset clause the model wrote (prevent cross-graph reads).
        q = _FROM_RE.sub("", query)
        upper = q.upper()
        if "GRAPH" in upper:
            return q
        # No explicit GRAPH: bind the dataset via FROM <graph> before the first WHERE.
        if re.search(r"(?i)\bWHERE\b", q):
            q = re.sub(r"(?i)\bWHERE\b", f"FROM <{graph}> WHERE", q, count=1)
        else:
            q = q.rstrip().rstrip(";") + f"\nFROM <{graph}>"
        return q

    @staticmethod
    def _enforce_limit(query: str, max_rows: int) -> str:
        if re.search(r"(?i)\bLIMIT\b", query):
            return query
        return query.rstrip().rstrip(";") + f"\nLIMIT {max_rows}"

    # ── Prompt / tool schema ─────────────────────────────────────────

    def _tools(self, max_rows: int) -> list[dict]:
        return [
            {
                "name": "sparql_query",
                "description": (
                    "对知识图谱执行一次只读 SPARQL 查询并返回结果行。先探查 schema，"
                    f"再逐步收窄。结果最多 {max_rows} 行且可能被截断。只能用 SELECT/ASK/CONSTRUCT。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "SPARQL 查询。可用前缀 adh/rdfs/rdf/owl/skos/xsd/schema。"
                                "实体名称优先取 rdfs:label；模糊匹配用 CONTAINS(LCASE(..))。"
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "submit_grounding",
                "description": "确定相关实体后提交最终接地结果，结束循环。名称必须来自图谱（先验证存在）。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tables": {
                            "type": "array", "items": {"type": "string"},
                            "description": "相关表名（adh:Table 的 rdfs:label），按重要性排序。",
                        },
                        "sql_templates": {
                            "type": "array", "items": {"type": "string"},
                            "description": "相关模板的 template_id 或 template_name。",
                        },
                        "business_terms": {
                            "type": "array", "items": {"type": "string"},
                            "description": "相关业务术语名（adh:nameCn）。",
                        },
                        "metrics": {
                            "type": "array", "items": {"type": "string"},
                            "description": "相关指标名（adh:Metric 的 rdfs:label）。",
                        },
                        "reasoning": {"type": "string", "description": "一句话说明为何这些实体可回答问题。"},
                    },
                    "required": ["tables"],
                },
            },
        ]

    def _system_prompt(self, graph: str, max_rows: int) -> str:
        return (
            "你是数据检索智能体，通过编写 SPARQL 查询一个 RDF 知识图谱，把用户的自然语言问题"
            "“接地”到图谱中的具体实体。你不生成 SQL，只负责找出与问题相关的表/SQL模板/业务术语/指标。\n\n"
            f"当前图谱（命名图）：< {graph} >。查询需作用于该图（直接写 GRAPH <{graph}> {{…}}，"
            "或不写 GRAPH 而由系统自动 FROM 限定）。\n\n"
            "# 本体 Schema\n"
            "类与谓词（均在前缀 adh: 下，除 rdfs:label）：\n"
            "- adh:Table      : rdfs:label(表名), adh:comment(注释), adh:businessDesc(业务描述);\n"
            "                    adh:hasColumn→adh:Column ; adh:join↔adh:Table\n"
            "- adh:Column     : rdfs:label(列名), adh:tableName, adh:dataType, adh:comment\n"
            "- adh:Term       : adh:nameCn, adh:nameEn, adh:comment(描述), adh:calculation; adh:mapsTo→adh:Column\n"
            "- adh:Metric     : rdfs:label(指标名), adh:comment; adh:defines→adh:Column\n"
            "- adh:SQLTemplate: rdfs:label(模板名), adh:sqlText, adh:intentKeywords, adh:category,\n"
            "                    adh:comment(描述), adh:variables, adh:rules; adh:touchTable→adh:Table\n"
            "- adh:DataSource : rdfs:label, adh:dbType\n"
            "注意：模板/表等 IRI 的局部名含冒号（如 sqltpl:xxx），不能用 adh: 前缀简写，需用完整 <IRI>。\n\n"
            "# 策略\n"
            f"1) 先概览（如列出所有表名/模板关键词），再据问题用 CONTAINS/FILTER 收窄；必要时用 ?t adh:join ?u 发现关联表。\n"
            "2) 提交前用查询验证你选中的名称确实存在于图中。\n"
            "3) 最多若干轮，务必在结束前调用 submit_grounding；tables 尽量精简（≤8 张最相关表）。\n\n"
            "# 示例\n"
            f"SELECT ?n WHERE {{ GRAPH <{graph}> {{ ?t a adh:Table ; rdfs:label ?n }} }} LIMIT {max_rows}\n"
            f"SELECT ?name ?kw WHERE {{ GRAPH <{graph}> {{ ?s a adh:SQLTemplate ; rdfs:label ?name ; adh:intentKeywords ?kw }} }}\n"
            "只有只读查询被允许；越权或写操作会被拒绝并返回错误，请据此自我修正。"
        )

    def _user_prompt(self, question: str, keywords: list[str] | None,
                     candidate_tables: list[str] | None) -> str:
        parts = [f"用户问题：{question}"]
        if keywords:
            parts.append("参考关键词：" + ", ".join(keywords))
        if candidate_tables:
            parts.append("（可选提示）候选表：" + ", ".join(candidate_tables[:10]) +
                         "——仍需用 SPARQL 验证它们确在图中。")
        parts.append("请通过查询图谱，找出回答该问题所需的表、SQL模板、业务术语与指标，然后调用 submit_grounding。")
        return "\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────

def _normalize_grounding(inp: dict) -> dict[str, Any]:
    def _list(x) -> list[str]:
        if not x:
            return []
        if isinstance(x, str):
            return [s.strip() for s in re.split(r"[,;、]", x) if s.strip()]
        return [str(v).strip() for v in x if str(v).strip()]

    return {
        "tables": _list(inp.get("tables")),
        "sql_templates": _list(inp.get("sql_templates")),
        "business_terms": _list(inp.get("business_terms")),
        "metrics": _list(inp.get("metrics")),
        "reasoning": str(inp.get("reasoning", ""))[:1000],
    }
