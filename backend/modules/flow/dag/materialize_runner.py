"""物化节点执行器 — 数据集 → 已登记数据源表（定时物化的唯一执行核心）。

定位（`data-sync-engineering.md`）：
- 本通道属**数据工程**：读授权数据 → 计算 → 写授权表，不向人或 LLM 返回数据行；
- 因此不套交互式取数的 200/2000 行默认上限，改用**显式预算**（超预算即拒），
  绝不静默截断（截断后报成功 = 错数）；
- 取数仍走统一治理口 `governed_execute`（敏感屏蔽/RLS/RBAC/审计），写入走
  `target_writer.write_dataframe_atomic`（staging + 原子切换 + 目标侧对账）。

任务配置（DAG 节点 config；单任务物化 = 只含一个该节点的 DAG）::

    {
      "dataset": "数据集名",                 # 选择框（业务名，服务端解析 id）
      "target_datasource": "目标数据源名",   # 选择框（adh_datasources.name）
      "target_table": "目标表名",            # 必须已存在（不隐式建表）
      "write_mode": "overwrite",             # overwrite | append | partition_overwrite
      "partition": {                         # 仅 partition_overwrite 必填
        "column": "dt",                      # 目标/口径日期列（用于分区对账）
        "date_arg": "dt",                    # 数据集参数名（按日期渲染，必须已声明）
        "target_partition": "p{yyyyMMdd}",   # Doris 分区名模板（Doris 必填）
        "lookback_days": 1                   # 含运行日在内的回看天数（迟到数据重跑）
      },
      "args": {"地区": "全部"},               # 固定参数（必须与数据集声明一致）
      "site": "cn",                          # 可选：站点业务名（多站路由）
      "batch_size": 1000,
      "budget": {"max_rows": 200000, "max_seconds": 1800}
    }

失败一律抛 `NodeExecutionError`（带 error_code），由 DAG 执行器记 failed：
不写半态（staging 未切换即视为未生效）、不拿写入回报当成功依据。

多分区回看（lookback_days>1）的提交边界：**每个分区各自原子提交**，中途失败
不会留下半个分区；已提交的日期不会回滚（按业务日期幂等，重跑同名分区覆盖）。
失败信息会带上已完成的日期列表，避免“整份回滚”的误解。

敏感字段边界（诚实记账）：读侧沿用统一治理口（`block` 剔除、`mask` 脱敏），
因此 mask 列落库为脱敏后的值；若要"数仓存原值"必须另立特权读取通道，
本模块不做静默绕过（不得以物化为名绕过敏感基线）。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 200_000
DEFAULT_MAX_SECONDS = 1800
DEFAULT_LOOKBACK_DAYS = 1
MAX_BATCH_SIZE = 20_000
RUNTIME_TZ = timezone(timedelta(hours=8))  # 与调度默认时区 Asia/Shanghai 同口径

WRITE_MODES = ("overwrite", "append", "partition_overwrite")


class MaterializeError(RuntimeError):
    """物化节点失败（error_code 可诊断；原始细节仅进服务端日志）。"""

    def __init__(self, message: str, error_code: str = "MATERIALIZE_FAILED"):
        self.error_code = error_code
        super().__init__(message)


def _today() -> date:
    return datetime.now(RUNTIME_TZ).date()


def int_or_default(value, default: int) -> int:
    """只把"未配置"（None/空串）当作默认值；显式 0/负数必须由调用方回报。

    早期写 `int(x or default)` 会把配成 0 的预算/批量静默吃成默认值，
    管理员以为任务被收紧，实际跑的是默认口径（no-silent-degradation §1）。
    校验器（dag_validator）与执行期共用本函数，避免两套容错口径漂移。
    """
    if value is None or value == "":
        return default
    return int(value)


def _run_dates(lookback_days: int) -> list[date]:
    """回看窗口的日期序列（含运行日，升序）。

    越界不静默夹取：范围由调用方先校验并给可读错误（口径见 dag_validator）。
    """
    days = int(lookback_days)
    if not 1 <= days <= 366:
        raise ValueError(f"回看天数须在 1~366 之间（收到 {days}）")
    end = _today()
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _partition_name(template: str, value: date) -> str:
    """分区名模板渲染（{yyyyMMdd} / {yyyy-MM-dd} / {date}）。"""
    return (str(template or "")
            .replace("{yyyyMMdd}", value.strftime("%Y%m%d"))
            .replace("{yyyy-MM-dd}", value.strftime("%Y-%m-%d"))
            .replace("{date}", value.strftime("%Y-%m-%d")))


def _resolve_dataset(name: str, identity: dict) -> dict:
    from backend.modules.viz.services.dataset_service import (
        _assert_dataset_visible, get_dataset_by_name,
    )
    ds = get_dataset_by_name(str(name or "").strip())
    if not ds:
        raise MaterializeError(f"数据集 '{name}' 不存在或已停用", "DATASET_MISSING")
    if str(ds.get("status") or "") != "active":
        raise MaterializeError(f"数据集 '{name}' 已停用，不能物化", "DATASET_INACTIVE")
    # 归属/可见性：属主、admin 或按角色共享给该用户，否则拒绝（fail-closed）
    _assert_dataset_visible(ds, identity, action="物化")
    if not str(ds.get("sql_query") or "").strip():
        raise MaterializeError(
            f"数据集 '{name}' 未配置执行 SQL，无法物化；"
            "语义型数据集的行数上限由语义层编译决定，请改用 SQL 型数据集做物化",
            "DATASET_NOT_SQL")
    return ds


def _assert_target_authorized(datasource_name: str, user_id: int, workspace_id: int) -> dict:
    """目标数据源必须已登记，且任务属主的角色对该源有授权（fail-closed）。"""
    from backend.common.db import get_datasource_by_name
    from backend.core.role_service import role_service

    source = get_datasource_by_name(str(datasource_name or ""))
    if not source:
        raise MaterializeError(
            f"目标数据源 '{datasource_name}' 未登记，请先在数据源管理中登记",
            "TARGET_DATASOURCE_MISSING")
    allowed = role_service.get_user_allowed_datasources(int(user_id or 0), int(workspace_id or 0))
    if int(source["id"]) not in {int(x) for x in allowed or []}:
        raise MaterializeError(
            f"任务属主的角色未被授权写入目标数据源 '{datasource_name}'，物化已拒绝",
            "TARGET_UNAUTHORIZED")
    return source


def _declared_params(ds: dict) -> set[str]:
    """数据集声明的可传参数集（逻辑参数 key ∪ 模板变量名）。

    与前端选择项、编译期 `declared_names` 同源（`param_summary`）：只用模板变量名
    会误拒“多变量聚合为一个逻辑参数”的合法 key。
    """
    from backend.semantics.param_plan import param_summary

    names: set[str] = set()
    try:
        for group in param_summary(ds) or []:
            if group.get("key"):
                names.add(str(group["key"]))
            for target in group.get("vars") or []:
                if target.get("var"):
                    names.add(str(target["var"]))
    except Exception as exc:  # noqa: BLE001 摘要算不出来不应阻断：交由编译期报错
        logger.warning("[Materialize] 数据集参数摘要解析失败: %s", exc)
    return names


def _compile_sql(ds: dict, args: dict, max_rows: int) -> tuple[str, list[str], int]:
    """按预算编译数据集正文（预算外多取 1 行用于识别"超预算"）。"""
    from backend.semantics.dataset_compiler import (
        DatasetCompileError, MATERIALIZE_MAX_LIMIT, compile_dataset,
    )

    budget_rows = max(1, min(int(max_rows), MATERIALIZE_MAX_LIMIT))
    try:
        compiled = compile_dataset(
            ds,
            args=args,
            limit=budget_rows + 1,
            inner_limit=0,          # 不补 5000 保底：由外层预算 LIMIT 兜住
            max_limit=budget_rows + 1,
        )
    except DatasetCompileError as exc:
        raise MaterializeError(str(exc), "DATASET_COMPILE_FAILED") from exc
    return compiled.sql, list(compiled.resolved_tables or []), budget_rows


def _fetch(sql: str, datasource_id: int, identity: dict) -> tuple[list[str], list[dict]]:
    """治理取数（敏感/RLS/RBAC/审计同分析面，一次取回；行数由外层 LIMIT 兜）。"""
    from backend.core.governed_query import governed_execute

    result = governed_execute(
        sql, int(datasource_id or 0) or None,
        int(identity.get("user_id") or 0),
        int(identity.get("workspace_id") or 0),
        identity.get("username") or "")
    return list(result.get("columns") or []), list(result.get("rows") or [])


def _to_dataframe(columns: list[str], rows: list[dict]):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _write(df, target_ds: str, target_table: str, write_mode: str, run_date,
           partition: dict, target_partition: str, batch_size: int, context: dict,
           writer) -> dict:
    """调原子写入并把引擎错误归到可诊断错误码（不把失败降级成"看起来成功"）。"""
    partition_args = None
    if run_date is not None:
        partition_args = {"column": partition.get("column"),
                          "value": run_date.strftime("%Y-%m-%d"),
                          "target_partition": target_partition}
    try:
        return writer(
            df, target_ds, target_table,
            write_mode=write_mode,
            partition=partition_args,
            batch_size=batch_size,
            context=f"materialize run={context.get('run_id')} node={context.get('node_key')}"
                    + (f" partition={run_date.isoformat()}" if run_date else ""))
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 统一转可诊断错误码，原始细节进日志
        logger.exception("[Materialize] 写入失败 target=%s.%s mode=%s",
                         target_ds, target_table, write_mode)
        code = "RECONCILE_FAILED" if "对账" in str(exc) else "TARGET_WRITE_FAILED"
        raise MaterializeError(
            f"写入目标表 {target_ds}.{target_table} 失败：{exc}", code) from exc


def run_materialize_node(config: dict, context: dict) -> dict:
    """执行物化节点（单/多日期分区），返回行数、分区与对账结果。"""
    from backend.modules.flow.dag.target_writer import write_dataframe_atomic

    dataset_name = str(config.get("dataset") or "").strip()
    target_ds = str(config.get("target_datasource") or "").strip()
    target_table = str(config.get("target_table") or "").strip()
    write_mode = str(config.get("write_mode") or "overwrite")
    if not dataset_name or not target_ds or not target_table:
        raise MaterializeError("物化节点缺少数据集/目标数据源/目标表", "CONFIG_INCOMPLETE")
    if write_mode not in WRITE_MODES:
        raise MaterializeError(f"物化写入模式不合法: {write_mode}", "CONFIG_INVALID")

    identity = dict(context.get("owner_identity") or {})
    identity["workspace_id"] = int(context.get("workspace_id") or 0)
    if not int(identity.get("user_id") or 0):
        raise MaterializeError("物化任务未认领创建者，无法执行（OWNER_REQUIRED）",
                               "OWNER_REQUIRED")

    budget = config.get("budget") or {}
    max_rows = int_or_default(budget.get("max_rows"), DEFAULT_MAX_ROWS)
    max_seconds = int_or_default(budget.get("max_seconds"), DEFAULT_MAX_SECONDS)
    batch_size = int_or_default(config.get("batch_size"), 1000)
    if max_rows < 1 or max_seconds < 1:
        raise MaterializeError("物化预算必须为正数（行数/秒）", "BUDGET_INVALID")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise MaterializeError(
            f"批量写入行数须在 1~{MAX_BATCH_SIZE} 之间", "BUDGET_INVALID")

    ds = _resolve_dataset(dataset_name, identity)
    _assert_target_authorized(target_ds, identity["user_id"], identity["workspace_id"])

    from backend.modules.viz.services.dataset_service import resolve_effective_source

    effective_source = resolve_effective_source(
        ds, identity, site=str(config.get("site") or ""))
    ds = {**ds, "datasource_id": effective_source}

    fixed_args = config.get("args") if isinstance(config.get("args"), dict) else {}
    partition = config.get("partition") or {}
    dates: list[date] = []
    date_arg = ""
    if write_mode == "partition_overwrite":
        date_arg = str(partition.get("date_arg") or "").strip()
        part_col = str(partition.get("column") or "").strip()
        if not date_arg or not part_col:
            raise MaterializeError(
                "分区覆盖必须指定「日期参数名」与「分区/日期列」", "CONFIG_INCOMPLETE")
        declared = _declared_params(ds)
        if date_arg not in declared:
            raise MaterializeError(
                f"数据集 '{dataset_name}' 未声明参数 '{date_arg}'，无法按日期增量物化"
                f"（已声明: {sorted(declared) or '无'}）", "DATASET_PARAM_MISSING")
        try:
            dates = _run_dates(int_or_default(partition.get("lookback_days"),
                                              DEFAULT_LOOKBACK_DAYS))
        except (TypeError, ValueError) as exc:
            raise MaterializeError(str(exc), "CONFIG_INVALID") from exc

    started = time.monotonic()
    partitions: list[dict] = []
    total_read = 0
    total_written = 0
    strategies: set[str] = set()
    datasets_tables: set[str] = set()
    last_columns: list[str] = []
    last_df = None

    runs = dates or [None]
    for run_date in runs:
        context["stop_check"]()
        if time.monotonic() - started > max_seconds:
            raise MaterializeError(
                f"物化超预算：已耗时超过 {max_seconds} 秒（预算即拒，不写半态）；"
                f"已提交分区: {[p['date'] for p in partitions] or '无'}",
                "BUDGET_EXCEEDED")

        args = dict(fixed_args)
        if run_date is not None:
            args[date_arg] = run_date.strftime("%Y-%m-%d")
        sql, resolved_tables, budget_rows = _compile_sql(ds, args, max_rows)
        datasets_tables.update(resolved_tables)

        columns, rows = _fetch(sql, int(effective_source or 0), identity)
        last_columns = columns
        # 预算即拒：撞到 max_rows+1 就是"还有更多"，绝不可截断入库
        if len(rows) > budget_rows:
            raise MaterializeError(
                f"物化结果超过行数预算 {budget_rows} 行（读取到 {len(rows)} 行）。"
                "请缩小口径、加日期分区或提高预算后重跑；平台不会静默截断。"
                f"已提交分区: {[p['date'] for p in partitions] or '无'}",
                "BUDGET_EXCEEDED")

        df = _to_dataframe(columns, rows)
        last_df = df
        rows_read = len(df)
        target_partition = ""
        if run_date is not None:
            target_partition = _partition_name(
                str(partition.get("target_partition") or ""), run_date)
        write_result = _write(df, target_ds, target_table, write_mode, run_date,
                             partition, target_partition, batch_size, context,
                             write_dataframe_atomic)
        total_read += rows_read
        total_written += int(write_result.get("rows_written") or 0)
        strategies.add(str(write_result.get("strategy") or ""))
        partitions.append({
            "date": run_date.isoformat() if run_date else "",
            "rows_read": rows_read,
            "rows_written": int(write_result.get("rows_written") or 0),
            "target_partition": target_partition,
            "target_count": write_result.get("target_count"),
            "reconciled": bool(write_result.get("reconciled")),
        })

    context["stop_check"]()

    # 血缘（旁路）：数据集触及表 → 目标表
    try:
        from backend.modules.flow.dag.node_runners import _record_lineage
        from backend.modules.viz.services.dataset_service import effective_source_name
        _record_lineage(
            f"INSERT INTO `{target_table}` SELECT * FROM ({' UNION '.join(sorted(datasets_tables)) or 'dataset'})",
            effective_source_name(effective_source), context,
            target_datasource_name=target_ds)
    except Exception as exc:  # noqa: BLE001 旁路
        logger.warning("[Materialize] 血缘采集失败（不影响物化结果）: %s", exc)

    # 质量检查与数据产品登记（沿用同步节点口径）
    quality = None
    product = {"registered": False}
    try:
        from backend.modules.flow.dag.node_runners import (
            _register_product_after_write, _run_quality_checks,
        )
        quality = _run_quality_checks(config, context, target_ds, target_table, "materialize")
        from backend.modules.viz.services.dataset_service import effective_source_name
        product = _register_product_after_write(
            last_df, target_ds, target_table, "materialize",
            f"materialize:{context.get('node_key') or ''}",
            [effective_source_name(effective_source)], context)
    except Exception as exc:  # noqa: BLE001 旁路（质量/产品登记失败不谎报主链路失败）
        logger.warning("[Materialize] 质量/产品登记失败（不影响物化结果）: %s", exc)

    return {
        "rows_read": total_read,
        "rows_written": total_written,
        "dataset": ds.get("name"),
        "target": f"{target_ds}.{target_table}",
        "write_mode": write_mode,
        "partitions": partitions,
        "reconciled": all(item.get("reconciled") for item in partitions),
        "strategy": ",".join(sorted(s for s in strategies if s)),
        "budget": {"max_rows": max_rows, "max_seconds": max_seconds},
        "columns": last_columns,
        "quality": quality,
        "data_product": product,
    }
