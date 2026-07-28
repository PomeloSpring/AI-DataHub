"""DAG Engine — DAG-based workflow execution engine.

This module implements a DAG (Directed Acyclic Graph) execution engine
for the ChatBI workflow system. It supports:
- Topological sorting for execution order
- Parallel execution of independent nodes
- Conditional branching
- Node status tracking
- Progress callbacks
"""

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    """Represents a node in the DAG."""
    id: str
    type: str
    label: str
    dependencies: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class DAGEdge:
    """Represents an edge in the DAG."""
    source: str
    target: str
    edge_type: str = "normal"
    condition_expr: Optional[str] = None
    label: Optional[str] = None


class DAGExecutor:
    """DAG workflow executor.

    Executes a workflow defined as a Directed Acyclic Graph,
    supporting parallel execution of independent nodes.

    Usage:
        executor = DAGExecutor(workflow_id=123, context={"question": "...", "datasource_id": 1})
        result = await executor.execute()
    """

    def __init__(
        self,
        workflow_id: int,
        context: Dict[str, Any],
        progress_callback: Optional[Callable] = None,
        stream_callback: Optional[Callable] = None,
    ):
        self.workflow_id = workflow_id
        self.context = context
        self.progress_callback = progress_callback
        self.stream_callback = stream_callback
        self.nodes: Dict[str, DAGNode] = {}
        self.edges: List[DAGEdge] = []
        self.execution_log: List[Dict[str, Any]] = []

    async def execute(self) -> Dict[str, Any]:
        """Execute the entire DAG workflow.

        Returns:
            Dict with execution results including:
            - success: bool
            - node_results: dict mapping node_id to result
            - execution_order: list of execution layers
            - total_elapsed_ms: int
        """
        start_time = time.time()

        try:
            # 1. Load workflow config from database
            await self._load_workflow_config()

            # 2. Validate DAG (no cycles, no orphans)
            errors = self.validate_dag()
            if errors:
                return {"success": False, "errors": errors}

            # 3. Topological sort to get execution layers
            layers = self._topological_sort()
            logger.info("DAG execution plan: %d layers, %d nodes", len(layers), len(self.nodes))

            # 4. Execute layers
            for layer_idx, layer in enumerate(layers):
                self._emit_progress(f"Executing layer {layer_idx + 1}/{len(layers)}", layer_idx / len(layers))

                # Execute all nodes in this layer concurrently
                tasks = [self._execute_node(node_id) for node_id in layer]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check for errors
                for node_id, result in zip(layer, results):
                    if isinstance(result, Exception):
                        self.nodes[node_id].status = NodeStatus.ERROR
                        self.nodes[node_id].error = str(result)
                        logger.error("Node %s failed: %s", node_id, result)

                # Check if we should continue (stop on critical errors)
                if any(isinstance(r, Exception) for r in results):
                    failed_nodes = [nid for nid, r in zip(layer, results) if isinstance(r, Exception)]
                    # Check if downstream nodes should be skipped
                    self._skip_downstream_nodes(failed_nodes)

            # 5. Collect results
            total_elapsed_ms = int((time.time() - start_time) * 1000)
            node_results = {
                nid: {"status": n.status.value, "result": n.result, "error": n.error}
                for nid, n in self.nodes.items()
            }

            return {
                "success": all(n.status == NodeStatus.SUCCESS or n.status == NodeStatus.SKIPPED for n in self.nodes.values()),
                "node_results": node_results,
                "execution_order": [layer for layer in layers],
                "total_elapsed_ms": total_elapsed_ms,
                "execution_log": self.execution_log,
            }

        except Exception as e:
            logger.error("DAG execution failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def _load_workflow_config(self):
        """Load workflow configuration from database."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Load workflow metadata
                cur.execute(
                    "SELECT id, name, description FROM adh_workflow_configs WHERE id = %s AND is_active = 1",
                    (self.workflow_id,),
                )
                workflow = cur.fetchone()
                if not workflow:
                    raise ValueError(f"Workflow {self.workflow_id} not found")

                # Load nodes (steps)
                cur.execute(
                    "SELECT id, step_type, step_name, dependencies, node_type, config, "
                    "position_x, position_y, max_rounds, is_enabled, prompt_key "
                    "FROM adh_workflow_steps WHERE workflow_id = %s",
                    (self.workflow_id,),
                )
                steps = cur.fetchall()
                for step in steps:
                    dependencies = []
                    if step.get("dependencies"):
                        try:
                            dependencies = json.loads(step["dependencies"])
                        except (json.JSONDecodeError, TypeError):
                            pass

                    config = {}
                    if step.get("config"):
                        try:
                            config = json.loads(step["config"]) if isinstance(step["config"], str) else step["config"]
                        except (json.JSONDecodeError, TypeError):
                            pass

                    node = DAGNode(
                        id=str(step["id"]),
                        type=step.get("node_type") or step["step_type"],
                        label=step["step_name"],
                        dependencies=[str(d) for d in dependencies],
                        config={
                            "step_type": step["step_type"],
                            "step_name": step["step_name"],
                            "max_rounds": step.get("max_rounds", 1),
                            "is_enabled": bool(step.get("is_enabled", True)),
                            "prompt_key": step.get("prompt_key"),
                            **config,
                        },
                    )
                    self.nodes[node.id] = node

                # Load edges
                cur.execute(
                    "SELECT source_step_id, target_step_id, edge_type, condition_expr, label "
                    "FROM adh_workflow_edges WHERE workflow_id = %s",
                    (self.workflow_id,),
                )
                edges = cur.fetchall()
                for edge in edges:
                    self.edges.append(DAGEdge(
                        source=str(edge["source_step_id"]),
                        target=str(edge["target_step_id"]),
                        edge_type=edge.get("edge_type", "normal"),
                        condition_expr=edge.get("condition_expr"),
                        label=edge.get("label"),
                    ))

                # If no edges loaded, build from dependencies
                if not self.edges:
                    for node in self.nodes.values():
                        for dep_id in node.dependencies:
                            self.edges.append(DAGEdge(source=dep_id, target=node.id))

        finally:
            conn.close()

    def validate_dag(self) -> List[str]:
        """Validate the DAG structure.

        Returns:
            List of error messages, empty if valid.
        """
        errors = []

        # Check for cycles using DFS
        if self._has_cycle():
            errors.append("DAG contains a cycle")

        # Check for start and end nodes
        has_start = any(n.type == "start" for n in self.nodes.values())
        has_end = any(n.type == "end" for n in self.nodes.values())
        if not has_start:
            errors.append("DAG has no start node")
        if not has_end:
            errors.append("DAG has no end node")

        # Check for orphan nodes (no incoming or outgoing edges)
        connected_nodes = set()
        for edge in self.edges:
            connected_nodes.add(edge.source)
            connected_nodes.add(edge.target)
        orphans = set(self.nodes.keys()) - connected_nodes
        # Allow start/end nodes to be "orphans" if they're properly typed
        for orphan in orphans:
            if self.nodes[orphan].type not in ("start", "end"):
                errors.append(f"Node '{orphan}' is not connected to any other node")

        return errors

    def _has_cycle(self) -> bool:
        """Check if the DAG has a cycle using Kahn's algorithm."""
        in_degree = defaultdict(int)
        adjacency = defaultdict(list)

        for node_id in self.nodes:
            in_degree[node_id] = 0

        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        visited = 0

        while queue:
            node_id = queue.popleft()
            visited += 1
            for neighbor in adjacency[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return visited != len(self.nodes)

    def _topological_sort(self) -> List[List[str]]:
        """Topological sort returning execution layers.

        Nodes in the same layer have no dependencies between them
        and can be executed in parallel.

        Returns:
            List of layers, each layer is a list of node IDs.
        """
        in_degree = defaultdict(int)
        adjacency = defaultdict(list)

        for node_id in self.nodes:
            in_degree[node_id] = 0

        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        # Filter out disabled nodes
        disabled_nodes = {nid for nid, n in self.nodes.items() if not n.config.get("is_enabled", True)}

        layers = []
        remaining = set(self.nodes.keys()) - disabled_nodes

        while remaining:
            # Find all nodes with in-degree 0 among remaining
            current_layer = [
                nid for nid in remaining
                if in_degree[nid] == 0 and nid in remaining
            ]

            if not current_layer:
                # This shouldn't happen if DAG is valid, but handle gracefully
                logger.warning("No nodes with in-degree 0 found, remaining: %s", remaining)
                break

            layers.append(current_layer)

            # Remove current layer and update in-degrees
            for nid in current_layer:
                remaining.discard(nid)
                for neighbor in adjacency[nid]:
                    in_degree[neighbor] -= 1

        # Mark disabled nodes as skipped
        for nid in disabled_nodes:
            self.nodes[nid].status = NodeStatus.SKIPPED

        return layers

    async def _execute_node(self, node_id: str):
        """Execute a single node in the DAG."""
        node = self.nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        self._emit_progress(f"Executing: {node.label}", node_id=node_id)
        self._log_execution(node_id, "started")

        try:
            # Collect inputs from dependency nodes
            input_data = self._collect_inputs(node_id)

            # Execute based on node type
            step_type = node.config.get("step_type", node.type)

            if step_type == "metadata_retrieval":
                result = await self._execute_metadata_retrieval(node, input_data)
            elif step_type == "llm_analysis":
                result = await self._execute_llm_analysis(node, input_data)
            elif step_type == "metadata_supplement":
                result = await self._execute_metadata_supplement(node, input_data)
            elif step_type == "sql_generation":
                result = await self._execute_sql_generation(node, input_data)
            elif step_type == "sql_execution":
                result = await self._execute_sql_execution(node, input_data)
            elif step_type == "result_analysis":
                result = await self._execute_result_analysis(node, input_data)
            elif step_type == "condition":
                result = await self._execute_condition(node, input_data)
            elif step_type == "llm_call":
                result = await self._execute_llm_call(node, input_data)
            elif step_type == "transform":
                result = await self._execute_transform(node, input_data)
            elif step_type in ("start", "end"):
                result = {"success": True, "data": input_data}
            else:
                result = {"success": True, "data": input_data, "note": f"Passthrough for {step_type}"}

            node.result = result
            node.status = NodeStatus.SUCCESS
            node.completed_at = time.time()
            self._log_execution(node_id, "completed", result)

        except Exception as e:
            node.status = NodeStatus.ERROR
            node.error = str(e)
            node.completed_at = time.time()
            self._log_execution(node_id, "error", {"error": str(e)})
            logger.error("Node %s (%s) failed: %s", node_id, node.type, e)
            raise

    def _collect_inputs(self, node_id: str) -> Dict[str, Any]:
        """Collect inputs from dependency nodes."""
        input_data = {"question": self.context.get("question"), "datasource_id": self.context.get("datasource_id")}

        for edge in self.edges:
            if edge.target == node_id and edge.source in self.nodes:
                source_node = self.nodes[edge.source]
                if source_node.result:
                    input_data.update(source_node.result)

        return input_data

    def _skip_downstream_nodes(self, failed_node_ids: List[str]):
        """Skip downstream nodes when a dependency fails."""
        adjacency = defaultdict(list)
        for edge in self.edges:
            adjacency[edge.source].append(edge.target)

        to_skip = set()
        queue = deque(failed_node_ids)
        while queue:
            nid = queue.popleft()
            for neighbor in adjacency[nid]:
                if self.nodes[neighbor].status == NodeStatus.PENDING:
                    to_skip.add(neighbor)
                    queue.append(neighbor)

        for nid in to_skip:
            self.nodes[nid].status = NodeStatus.SKIPPED
            self.nodes[nid].error = "Skipped due to upstream failure"

    async def _execute_metadata_retrieval(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute metadata retrieval step."""
        from backend.rag.rag_retriever import retrieve_all
        from backend.rag.table_selector import select_tables

        question = input_data.get("question", self.context.get("question", ""))
        datasource_id = input_data.get("datasource_id", self.context.get("datasource_id", 0))

        selected_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
        rag_context = retrieve_all(question=question, selected_tables=selected_tables, datasource_id=datasource_id)

        return {
            "table_info": rag_context.get("table_info", []),
            "column_metadata": rag_context.get("column_metadata", []),
            "business_terms": rag_context.get("business_terms", []),
            "table_relations": rag_context.get("table_relations", []),
            "sql_templates": rag_context.get("sql_templates", []),
            "selected_tables": selected_tables,
        }

    async def _execute_llm_analysis(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute LLM analysis step."""
        from backend.nl2sql.orchestrator.workflow.loop_engine import analyze_metadata_need

        question = input_data.get("question", self.context.get("question", ""))
        current_metadata = {
            "table_info": input_data.get("table_info", []),
            "column_metadata": input_data.get("column_metadata", []),
            "business_terms": input_data.get("business_terms", []),
            "table_relations": input_data.get("table_relations", []),
        }

        result = analyze_metadata_need(question, current_metadata, node.config.get("prompt_key"))
        return result

    async def _execute_metadata_supplement(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute metadata supplement step."""
        from backend.rag.rag_retriever import retrieve_all

        question = input_data.get("question", self.context.get("question", ""))
        required_tables = input_data.get("required_tables", [])
        datasource_id = input_data.get("datasource_id", self.context.get("datasource_id", 0))

        if required_tables:
            supplement = retrieve_all(question=question, selected_tables=required_tables, datasource_id=datasource_id)
            return {
                "supplemented_table_info": supplement.get("table_info", []),
                "supplemented_column_metadata": supplement.get("column_metadata", []),
            }
        return {"supplemented_table_info": [], "supplemented_column_metadata": []}

    async def _execute_sql_generation(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute SQL generation step."""
        from backend.nl2sql.prompt.prompt_builder import build_nl2sql_prompt
        from backend.common.llm.llm_client import generate_sql

        question = input_data.get("question", self.context.get("question", ""))
        messages = build_nl2sql_prompt(
            question=question,
            table_info=input_data.get("table_info", []),
            column_metadata=input_data.get("column_metadata", []),
            sql_templates=input_data.get("sql_templates", []),
            business_terms=input_data.get("business_terms", []),
            table_relations=input_data.get("table_relations", []),
            engine="Doris",
        )

        result = generate_sql(messages=messages, max_tokens=2000)
        sql = result.get("sql", "")

        # Parse JSON response
        content = sql.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            parsed = json.loads(content)
            return {"generated_sql": parsed.get("sql", ""), "sql_response": parsed}
        except json.JSONDecodeError:
            return {"generated_sql": content, "sql_response": content}

    async def _execute_sql_execution(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute SQL execution step."""
        from backend.nl2sql.sql.sql_validator import validate_and_fix
        from backend.nl2sql.sql.query_executor import execute_query

        sql = input_data.get("generated_sql", "")
        datasource_id = input_data.get("datasource_id", self.context.get("datasource_id", 0))

        if not sql:
            return {"success": False, "error": "No SQL to execute"}

        sql, warnings = validate_and_fix(sql)
        df, elapsed_ms, row_count = execute_query(sql, datasource_id=datasource_id)

        columns = list(df.columns) if not df.empty else []
        rows = df.to_dict(orient="records") if not df.empty else []

        # Sanitize rows
        from decimal import Decimal
        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
                elif isinstance(v, Decimal):
                    row[k] = float(v)
                elif isinstance(v, bytes):
                    row[k] = v.decode('utf-8', errors='replace')

        return {
            "success": True,
            "columns": columns,
            "row_count": row_count,
            "rows": rows,
            "elapsed_ms": elapsed_ms,
        }

    async def _execute_result_analysis(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute result analysis step."""
        from backend.nl2sql.orchestrator.workflow.loop_engine import analyze_result

        question = input_data.get("question", self.context.get("question", ""))
        query_result = {
            "columns": input_data.get("columns", []),
            "rows": input_data.get("rows", []),
            "row_count": input_data.get("row_count", 0),
        }
        column_metadata = input_data.get("column_metadata", [])

        result = analyze_result(question, query_result, column_metadata, node.config.get("prompt_key"))
        return result

    async def _execute_condition(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute condition node - evaluate expression and set branch."""
        condition_expr = node.config.get("condition_expr", "")

        # Simple expression evaluation
        # In production, use a safer evaluation method
        try:
            # Create a safe evaluation context
            eval_context = {**input_data, "len": len, "int": int, "str": str, "float": float}
            result = eval(condition_expr, {"__builtins__": {}}, eval_context)
            return {"condition_result": bool(result), "branch": "true" if result else "false"}
        except Exception as e:
            logger.warning("Condition evaluation failed: %s", e)
            return {"condition_result": False, "branch": "false", "error": str(e)}

    async def _execute_llm_call(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute generic LLM call."""
        from backend.common.llm.llm_client import generate_sql

        prompt = node.config.get("prompt", "")
        if not prompt:
            prompt = f"Analyze: {json.dumps(input_data, ensure_ascii=False)[:500]}"

        result = generate_sql(messages=[{"role": "user", "content": prompt}], max_tokens=1000)
        return {"llm_response": result.get("sql", ""), "tokens": result.get("tokens", {})}

    async def _execute_transform(self, node: DAGNode, input_data: Dict) -> Dict:
        """Execute data transformation."""
        transform_type = node.config.get("transform_type", "passthrough")

        if transform_type == "filter":
            # Filter rows based on condition
            rows = input_data.get("rows", [])
            condition = node.config.get("filter_condition", "")
            if condition:
                filtered = [r for r in rows if eval(condition, {"__builtins__": {}}, r)]
                return {"rows": filtered, "row_count": len(filtered)}
            return input_data

        elif transform_type == "merge":
            # Merge multiple inputs
            merged = {}
            for k, v in input_data.items():
                if isinstance(v, list):
                    merged[k] = v
                else:
                    merged[k] = v
            return merged

        # Passthrough
        return input_data

    def _emit_progress(self, message: str, progress: float = 0, node_id: str = None):
        """Emit progress event."""
        if self.progress_callback:
            self.progress_callback({
                "type": "progress",
                "message": message,
                "progress": progress,
                "node_id": node_id,
                "timestamp": datetime.now().isoformat(),
            })

    def _log_execution(self, node_id: str, status: str, data: Any = None):
        """Log execution event."""
        self.execution_log.append({
            "node_id": node_id,
            "node_type": self.nodes[node_id].type if node_id in self.nodes else "unknown",
            "status": status,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })


def load_dag_config(workflow_id: int) -> Dict[str, Any]:
    """Load DAG configuration for a workflow.

    Returns:
        Dict with nodes and edges in a format suitable for frontend rendering.
    """
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, step_type, step_name, dependencies, node_type, config, "
                "position_x, position_y, max_rounds, is_enabled, prompt_key "
                "FROM adh_workflow_steps WHERE workflow_id = %s",
                (workflow_id,),
            )
            steps = cur.fetchall()

            cur.execute(
                "SELECT source_step_id, target_step_id, edge_type, condition_expr, label "
                "FROM adh_workflow_edges WHERE workflow_id = %s",
                (workflow_id,),
            )
            edges = cur.fetchall()

            return {
                "nodes": [
                    {
                        "id": str(s["id"]),
                        "type": s.get("node_type") or s["step_type"],
                        "label": s["step_name"],
                        "position": {"x": s.get("position_x", 0), "y": s.get("position_y", 0)},
                        "config": json.loads(s["config"]) if s.get("config") and isinstance(s["config"], str) else s.get("config", {}),
                        "dependencies": json.loads(s["dependencies"]) if s.get("dependencies") and isinstance(s["dependencies"], str) else s.get("dependencies", []),
                        "is_enabled": bool(s.get("is_enabled", True)),
                    }
                    for s in steps
                ],
                "edges": [
                    {
                        "source": str(e["source_step_id"]),
                        "target": str(e["target_step_id"]),
                        "type": e.get("edge_type", "normal"),
                        "condition_expr": e.get("condition_expr"),
                        "label": e.get("label"),
                    }
                    for e in edges
                ],
            }
    finally:
        conn.close()
