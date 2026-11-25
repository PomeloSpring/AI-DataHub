"""DAG 校验 — 保存/发布/运行前的结构与配置校验。

校验项（任一不过即显式报错并定位到节点，fail-loud）：
- 结构：节点 key 唯一、边引用存在、无环、无悬空边；
- materialize 节点：数据集/目标数据源/目标表齐全，写入模式合法；分区覆盖必带
  日期参数与日期列，且数据集确实是 SQL 型并声明了该参数（否则运行期必报）；
- sync 节点：源/目标数据源 name 可解析、表名非空、增量模式必带增量列；
- sql_task 节点：SQL 只读单条（sql_guard.parse_query）、UDF 引用已注册且启用、
  目标写入配置完整；
- control 节点：动作合法。

数据源一律按 name 解析（waker-datasource-domain §2），解析失败给出可操作错误。
"""

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

NODE_TYPES = ("materialize", "sync", "sql_task", "control")
WRITE_MODES = ("append", "overwrite")
MATERIALIZE_WRITE_MODES = ("append", "overwrite", "partition_overwrite")
SYNC_MODES = ("full", "incremental")
CONTROL_ACTIONS = ("pass", "fail")
# 运行参数（按次运行，不写进 graph_json）：物化时间窗口 + 本次参与的粒度。
RUN_PARAM_KEYS = ("start_date", "end_date", "grains")
RUN_GRAINS = ("day", "month", "year")

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


class DagValidationError(ValueError):
    """DAG 不合法；message 面向用户可展示（不含凭据/内部 id）。"""

    def __init__(self, message: str, node_key: str = ""):
        self.node_key = node_key
        super().__init__(f"[{node_key}] {message}" if node_key else message)


def _resolve_datasource(name: str) -> dict:
    from backend.common.db import get_datasource_by_name
    source = get_datasource_by_name(name or "")
    if not source:
        raise DagValidationError(f"数据源 '{name}' 不存在，请从数据源清单中选择")
    return source


def _validate_table_name(name: str, field: str, node_key: str) -> None:
    if not name or not IDENT_RE.match(name):
        raise DagValidationError(f"{field} 不合法: {name}", node_key)


def validate_graph(graph: dict, check_datasources: bool = True,
                   check_udfs: bool = True) -> dict:
    """校验 graph_json = {nodes: [...], edges: [{from, to}]}，返回规范化图。"""
    if not isinstance(graph, dict):
        raise DagValidationError("DAG 定义必须是对象 {nodes, edges}")
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise DagValidationError("DAG 至少需要一个节点")

    seen_keys = set()
    node_map = {}
    for node in nodes:
        key = str(node.get("key") or "").strip()
        if not key:
            raise DagValidationError("节点缺少 key")
        if key in seen_keys:
            raise DagValidationError("节点 key 重复", key)
        seen_keys.add(key)
        node_map[key] = node

    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src not in node_map:
            raise DagValidationError(f"边引用了不存在的节点 '{src}'")
        if dst not in node_map:
            raise DagValidationError(f"边引用了不存在的节点 '{dst}'")
        if src == dst:
            raise DagValidationError("节点不能依赖自身", src)

    # 无环校验（Kahn 拓扑）
    order = topological_order(node_map, edges)
    if len(order) != len(node_map):
        cyclic = sorted(set(node_map) - set(order))
        raise DagValidationError(f"存在循环依赖: {', '.join(cyclic)}")

    for key, node in node_map.items():
        ntype = node.get("type")
        if ntype not in NODE_TYPES:
            raise DagValidationError(f"未知节点类型: {ntype}", key)
        config = node.get("config") or {}
        if ntype == "materialize":
            _validate_materialize_config(config, key, check_datasources)
        elif ntype == "sync":
            _validate_sync_config(config, key, check_datasources, check_udfs)
        elif ntype == "sql_task":
            _validate_sql_config(config, key, check_datasources, check_udfs)
        elif ntype == "control":
            if config.get("action", "pass") not in CONTROL_ACTIONS:
                raise DagValidationError(f"control 动作不合法: {config.get('action')}", key)

    return {"nodes": nodes, "edges": edges}


def _int_or_default(value, default: int) -> int:
    """只把"未配置"（None/空串）当作默认值；0 与负数一律报错。

    早期写 `int(x or default)` 会把显式配成 0 的预算/批量/回看天数静默吃成默认值，
    管理员以为任务被收紧，实际跑的是默认口径（no-silent-degradation §1）。
    口径与执行期同源：直接复用 `materialize_runner.int_or_default`，不另写一份。
    """
    from backend.modules.flow.dag.materialize_runner import int_or_default

    return int_or_default(value, default)


def _validate_materialize_config(config: dict, key: str, check_datasources: bool) -> None:
    """物化节点配置校验（保存/运行前同一口径，fail-loud 定位到节点）。"""
    from backend.semantics.dataset_compiler import MATERIALIZE_MAX_LIMIT

    dataset = str(config.get("dataset") or "").strip()
    if not dataset:
        raise DagValidationError("缺少数据集（须从数据集清单选择）", key)
    target_ds = str(config.get("target_datasource") or "").strip()
    if not target_ds:
        raise DagValidationError("缺少目标数据源（须从数据源清单选择）", key)
    if check_datasources:
        _resolve_datasource(target_ds)
    _validate_table_name(config.get("target_table", ""), "目标表名", key)

    write_mode = str(config.get("write_mode") or "overwrite")
    if write_mode not in MATERIALIZE_WRITE_MODES:
        raise DagValidationError(f"物化写入模式不合法: {write_mode}", key)

    try:
        batch_size = _int_or_default(config.get("batch_size"), 1000)
    except (TypeError, ValueError) as exc:
        raise DagValidationError("批量写入行数必须为正整数", key) from exc
    if not 1 <= batch_size <= 20000:
        raise DagValidationError("批量写入行数须在 1~20000 之间", key)

    budget = config.get("budget") or {}
    try:
        max_rows = _int_or_default(budget.get("max_rows"), 200000)
        max_seconds = _int_or_default(budget.get("max_seconds"), 1800)
    except (TypeError, ValueError) as exc:
        raise DagValidationError("物化预算（行数/秒）必须为正整数", key) from exc
    if not 1 <= max_rows <= MATERIALIZE_MAX_LIMIT:
        raise DagValidationError(
            f"行数预算须在 1~{MATERIALIZE_MAX_LIMIT} 之间", key)
    if not 1 <= max_seconds <= 86400:
        raise DagValidationError("超时预算须在 1~86400 秒之间", key)

    ds = None
    if check_datasources and dataset:
        from backend.modules.viz.services.dataset_service import get_dataset_by_name
        ds = get_dataset_by_name(dataset)
        if not ds:
            raise DagValidationError(f"数据集 '{dataset}' 不存在或已停用", key)
        if not str(ds.get("sql_query") or "").strip():
            raise DagValidationError(
                f"数据集 '{dataset}' 是语义型（无执行 SQL），不支持物化；请改用 SQL 型数据集", key)

    if write_mode == "partition_overwrite":
        # 回看单位随粒度变化（天/月/年），常量与执行器同源，避免两套夹取口径漂移
        from backend.modules.flow.dag.materialize_runner import (
            GRAIN_LOOKBACK_LABEL, GRAIN_LOOKBACK_LIMIT, GRAINS,
        )
        partition = config.get("partition") or {}
        date_arg = str(partition.get("date_arg") or "").strip()
        column = str(partition.get("column") or "").strip()
        if not date_arg:
            raise DagValidationError("分区覆盖必须指定日期参数名（数据集参数）", key)
        _validate_table_name(column, "分区日期列名", key)
        grain = str(partition.get("grain") or "day").strip().lower()
        if grain not in GRAINS:
            raise DagValidationError(
                f"物化粒度须为 {'/'.join(GRAINS)} 之一（收到 {grain}）", key)
        try:
            lookback = _int_or_default(partition.get("lookback_days"), 1)
        except (TypeError, ValueError) as exc:
            raise DagValidationError("回看格数必须为正整数", key) from exc
        limit = GRAIN_LOOKBACK_LIMIT[grain]
        if not 1 <= lookback <= limit:
            raise DagValidationError(
                f"回看{GRAIN_LOOKBACK_LABEL[grain]}须在 1~{limit} 之间", key)
        if ds is not None:
            declared = _declared_dataset_params(ds)
            if date_arg not in declared:
                raise DagValidationError(
                    f"数据集 '{dataset}' 未声明参数 '{date_arg}'（已声明: "
                    f"{sorted(declared) or '无'}），无法按日期增量物化", key)
        # 多站同表：分区键的固定列（如 region='cn'）+ 分区名模板占位。
        # 站点不进分区键时，同一天两站写同名分区，后跑的 OVERWRITE 会替掉前一站（数据静默消失）。
        fixed = partition.get("fixed")
        key_columns = partition.get("key_columns") or []
        if fixed is not None:
            if not isinstance(fixed, dict) or not fixed:
                raise DagValidationError("分区固定列须为非空 {列名: 值} 映射", key)
            for fixed_name, fixed_value in fixed.items():
                if not IDENT_RE.match(str(fixed_name or "")):
                    raise DagValidationError(
                        f"分区固定列名不合法: {fixed_name!r}", key)
                if isinstance(fixed_value, bool) or not isinstance(fixed_value, (str, int)):
                    raise DagValidationError(
                        f"分区固定列 '{fixed_name}' 的值须为字符串或整数", key)
        if key_columns:
            if not isinstance(key_columns, list):
                raise DagValidationError("分区键列 key_columns 须为数组", key)
            for col in key_columns:
                if not IDENT_RE.match(str(col or "")):
                    raise DagValidationError(f"分区键列名不合法: {col!r}", key)
                if not fixed or str(col) not in fixed:
                    raise DagValidationError(
                        f"分区键列 '{col}' 缺少固定值（partition.fixed 须给出）", key)
        if partition.get("target_partition"):
            template = str(partition.get("target_partition"))
            if "{yyyyMMdd}" not in template and "{yyyy-MM-dd}" not in template \
                    and "{date}" not in template:
                raise DagValidationError(
                    "Doris 分区名模板须含日期占位（{yyyyMMdd} 或 {yyyy-MM-dd}）", key)
            allowed = {"yyyyMMdd", "yyyy-MM-dd", "date"} | set(fixed or {})
            unknown = [ph for ph in re.findall(r"\{([^{}]+)\}", template)
                       if ph not in allowed]
            if unknown:
                raise DagValidationError(
                    f"分区名模板含未知占位 {unknown}（可用：日期占位 + 固定分区列名）", key)


def _declared_dataset_params(ds: dict) -> set[str]:
    """数据集可传参数集（逻辑参数 key ∪ 模板变量名，与执行期同源）。

    用 `param_summary` 而不是裸 `parse_variables`：后者只有模板变量名，
    会把“多变量聚合为一个逻辑参数”的合法 key 误判为未声明。
    """
    from backend.semantics.param_plan import param_summary

    names: set[str] = set()
    for group in param_summary(ds) or []:
        if group.get("key"):
            names.add(str(group["key"]))
        for target in group.get("vars") or []:
            if target.get("var"):
                names.add(str(target["var"]))
    return names


def _validate_sync_config(config: dict, key: str, check_datasources: bool,
                          check_udfs: bool = True) -> None:
    for field, label in (("source_datasource", "源数据源"), ("target_datasource", "目标数据源")):
        if not config.get(field):
            raise DagValidationError(f"缺少{label}（须从数据源清单选择）", key)
        if check_datasources:
            _resolve_datasource(config[field])
    _validate_table_name(config.get("source_table", ""), "源表名", key)
    _validate_table_name(config.get("target_table", ""), "目标表名", key)
    sync_mode = config.get("sync_mode", "full")
    if sync_mode not in SYNC_MODES:
        raise DagValidationError(f"同步模式不合法: {sync_mode}", key)
    if sync_mode == "incremental" and not config.get("incremental_column"):
        raise DagValidationError("增量同步必须指定增量列", key)
    if config.get("incremental_column"):
        _validate_table_name(config["incremental_column"], "增量列名", key)
    if config.get("write_mode", "append") not in WRITE_MODES:
        raise DagValidationError(f"写入模式不合法: {config.get('write_mode')}", key)

    # 可选转换 SQL（可引用 UDF）：先代入 {{source}} 占位，再 UDF 展开后做只读结构校验
    transform_sql = (config.get("transform_sql") or "").strip()
    if transform_sql:
        from backend.modules.flow.dag.udf_registry import expand_udfs, UdfValidationError
        sql = transform_sql.replace("{{source}}", f"`{config.get('source_table', '')}`")
        if check_udfs:
            try:
                expanded, _used = expand_udfs(sql, config.get("udf_refs") or [])
            except UdfValidationError as exc:
                raise DagValidationError(str(exc), key) from exc
        else:
            expanded = sql
        if config.get("source_table", "").lower() not in expanded.lower():
            raise DagValidationError(
                "转换 SQL 未引用源表（用 {{source}} 占位或 FROM 源表）", key)
        from backend.semantics.sql_guard import parse_query
        try:
            parse_query(expanded)
        except PermissionError as exc:
            raise DagValidationError(f"转换 SQL 不受支持: {exc}", key) from exc


def _validate_sql_config(config: dict, key: str, check_datasources: bool, check_udfs: bool) -> None:
    sql = (config.get("sql") or "").strip()
    if not sql:
        raise DagValidationError("缺少 SQL", key)
    if not config.get("source_datasource"):
        raise DagValidationError("缺少主数据源（须从数据源清单选择）", key)
    if check_datasources:
        _resolve_datasource(config["source_datasource"])

    # UDF 展开后再做只读结构校验（展开后只剩内置函数）
    from backend.modules.flow.dag.udf_registry import expand_udfs, UdfValidationError
    if check_udfs:
        try:
            expanded, _used = expand_udfs(sql, config.get("udf_refs") or [])
        except UdfValidationError as exc:
            raise DagValidationError(str(exc), key) from exc
    else:
        expanded = sql

    from backend.semantics.sql_guard import parse_query, extract_tables
    try:
        parse_query(expanded)
        referenced = extract_tables(expanded)
    except PermissionError as exc:
        raise DagValidationError(f"SQL 不受支持: {exc}", key) from exc

    # 跨源限定名的数据源存在性（三段式首段）；解析失败即拒绝
    if check_datasources:
        for table in referenced:
            parts = table.split(".")
            if len(parts) == 3:
                _resolve_datasource(parts[0])
            elif len(parts) not in (1, 2):
                raise DagValidationError(f"表引用格式不受支持: {table}", key)

    target = config.get("target") or {}
    if target:
        if not target.get("datasource") or not target.get("table"):
            raise DagValidationError("目标写入配置不完整（需目标数据源与目标表）", key)
        if check_datasources:
            _resolve_datasource(target["datasource"])
        _validate_table_name(target["table"], "目标表名", key)
        if target.get("write_mode", "append") not in WRITE_MODES:
            raise DagValidationError(f"写入模式不合法: {target.get('write_mode')}", key)


def validate_run_params(workflow: dict, params: dict | None) -> dict:
    """校验一次运行的参数（物化时间窗口），返回规范化后的参数字典。

    为什么不能原样往上传：
    - 未识别的键会被执行期静默忽略 —— 用户以为“指定了起始日期”，实际按回看
      窗口跑了（假初始化，跑完还报成功，`no-silent-degradation`）；
    - 日期格式写错（`2024-13-01` / `20241301`）靠下游宽松解析会把区间算歪；
    - 工作流没有日期分区物化节点时，“起始参数”无处生效，必须当场拒
      （不然参数存在但零作用，是最难查的一类“配置了没反应”）。

    `grains` 是“本次运行只跑哪些粒度”：历史初始化时月/年档要回塑多年，
    而日档只服务近月视图 —— 不区分粒度就会把日档也从 2021 年逐天扫一遍
    （几千格、必超预算）。它只做“本次跳过”，不改任何节点的配置。
    """
    if not params:
        return {}
    if not isinstance(params, dict):
        raise DagValidationError("运行参数必须是对象 {start_date?, end_date?, grains?}")
    unknown = sorted(set(params) - set(RUN_PARAM_KEYS))
    if unknown:
        raise DagValidationError(
            f"不支持的运行参数 {unknown}（可用：{list(RUN_PARAM_KEYS)}）")

    out: dict = {}
    for key in RUN_PARAM_KEYS:
        raw = params.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        if key == "grains":
            if not isinstance(raw, (list, tuple)) or not raw:
                raise DagValidationError("grains 须为非空列表（day/month/year 的子集）")
            picked: list[str] = []
            for item in raw:
                text = str(item).strip().lower()
                if text not in RUN_GRAINS:
                    raise DagValidationError(
                        f"未知物化粒度 {item!r}（可用：{list(RUN_GRAINS)}）")
                if text not in picked:
                    picked.append(text)
            out[key] = picked
            continue
        text = str(raw).strip()
        try:
            out[key] = date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise DagValidationError(
                f"{key} 须为 YYYY-MM-DD 日期（收到 {text!r}）") from exc
    if "start_date" in out and "end_date" in out and out["start_date"] > out["end_date"]:
        raise DagValidationError(
            f"起始日期 {out['start_date']} 晚于结束日期 {out['end_date']}")

    nodes = (workflow.get("graph_json") or {}).get("nodes") or []
    windowed = [
        n for n in nodes
        if n.get("type") == "materialize"
        and str((n.get("config") or {}).get("write_mode") or "") == "partition_overwrite"
    ]
    if not windowed:
        raise DagValidationError(
            "该工作流没有按日期分区的物化节点，运行参数（时间窗口）无处生效；"
            "请用节点配置里的回看格数（partition.lookback_days）控制窗口")
    return out


def topological_order(node_map: dict, edges: list) -> list:
    """Kahn 拓扑排序；有环时返回已排序部分（调用方据长度判环）。"""
    indegree = {key: 0 for key in node_map}
    outgoing = {key: [] for key in node_map}
    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src in node_map and dst in node_map:
            indegree[dst] += 1
            outgoing[src].append(dst)
    queue = sorted(key for key, deg in indegree.items() if deg == 0)
    order = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for nxt in outgoing[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order
