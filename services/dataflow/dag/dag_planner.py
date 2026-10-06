"""DAG 执行规划 — 就绪节点计算与失败传播。

执行语义：
- 依赖全为 success/skipped 才就绪；任一依赖 failed/timeout/cancelled → 本节点 skipped；
- 同层就绪节点可并行（执行器按串行/并行策略调用）；
- 上游 skipped 的下游也 skipped（跳过原因记录 UPSTREAM_FAILED）。
"""

from typing import Optional

FINISHED_OK = ("success", "skipped")
FINISHED_BAD = ("failed", "timeout", "cancelled")


def build_dependency_map(graph: dict) -> dict:
    """node_key -> [上游 node_key]"""
    deps = {str(n.get("key")): [] for n in graph.get("nodes") or []}
    for edge in graph.get("edges") or []:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if dst in deps and src in deps:
            deps[dst].append(src)
    return deps


def ready_nodes(graph: dict, status_map: dict) -> list:
    """返回当前就绪（可执行）的节点 key 列表。"""
    deps = build_dependency_map(graph)
    ready = []
    for key, upstream in deps.items():
        if status_map.get(key) is not None and status_map.get(key) not in ("queued",):
            continue  # 已有状态（含 running/终态）不再调度
        states = [status_map.get(dep) for dep in upstream]
        if any(state in FINISHED_BAD for state in states):
            continue  # 依赖失败 → 由 propagate_skips 处理为 skipped
        if all(state in FINISHED_OK for state in states):
            ready.append(key)
    return ready


def propagate_skips(graph: dict, status_map: dict, dag_service=None, run_id: int = 0) -> list:
    """把依赖失败/被跳过的节点标为 skipped，返回新跳过的节点 key。"""
    deps = build_dependency_map(graph)
    node_map = {str(n.get("key")): n for n in graph.get("nodes") or []}
    newly_skipped = []
    changed = True
    while changed:
        changed = False
        for key, upstream in deps.items():
            if status_map.get(key) not in (None, "queued"):
                continue
            bad = [dep for dep in upstream if status_map.get(dep) in FINISHED_BAD]
            skipped = [dep for dep in upstream if status_map.get(dep) == "skipped"]
            if bad or skipped:
                status_map[key] = "skipped"
                newly_skipped.append(key)
                changed = True
                if dag_service is not None and run_id:
                    reason = "上游节点失败: " + (", ".join(bad or skipped))
                    dag_service.skip_node_run(
                        run_id, key, (node_map.get(key) or {}).get("type", "unknown"), reason)
    return newly_skipped


def run_status_aggregate(status_map: dict) -> str:
    """节点状态聚合出 run 状态。"""
    states = list(status_map.values())
    if not states:
        return "success"
    if any(state in ("failed", "timeout", "cancelled") for state in states):
        if all(state in ("success", "skipped", "failed", "timeout", "cancelled") for state in states):
            # 有失败但仍有成功节点 = partial；全失败 = failed
            return "partial" if any(state == "success" for state in states) else "failed"
        return "running"
    if all(state in FINISHED_OK for state in states):
        return "success"
    return "running"


def remaining_nodes(graph: dict, status_map: dict) -> list:
    """未进入终态的节点（含未创建实例的）。"""
    return [str(n.get("key")) for n in graph.get("nodes") or []
            if status_map.get(str(n.get("key"))) in (None, "queued", "running")]
