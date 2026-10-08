"""DAG 节点执行器 — sync / sql_task / control 三类节点的执行核心。

共同契约：
- run_node(node, context) -> {"rows_read": int, "rows_written": int}
- 失败一律抛异常（由 dag_executor 统一记 error_code/脱敏错误），
  不得返回假成功（no-silent-degradation）；
- 取数一律走 governed_federated_read（统一治理入口，护栏 §1）；
- 写目标走 target_writer（数据工程写入通道，不返回数据行、不经 LLM）。

context（由 dag_executor 注入）：
{
  "run_id": int, "workflow_id": int, "node_key": str,
  "owner_identity": {"user_id": int, "username": str},   # 服务端身份（任务 owner）
  "workspace_id": int,
  "watermark_key": str,        # 水位线维度键
  "stop_check": callable,      # 停止/超时检查，抛异常即中止
}
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class NodeExecutionError(RuntimeError):
    """节点执行失败（error_code 可诊断，原始细节仅进服务端日志）。"""

    def __init__(self, message: str, error_code: str = "NODE_FAILED"):
        self.error_code = error_code
        super().__init__(message)


def run_node(node: dict, context: dict) -> dict:
    ntype = node.get("type")
    config = node.get("config") or {}
    if ntype == "sync":
        return run_sync_node(config, context)
    if ntype == "sql_task":
        return run_sql_task_node(config, context)
    if ntype == "control":
        return run_control_node(config, context)
    raise NodeExecutionError(f"未知节点类型: {ntype}", "UNKNOWN_NODE_TYPE")


# ── sync：表级搬运（全量 / 增量水位线） ─────────────────────────────


def _record_lineage(sql_for_lineage: str, datasource_name: str, context: dict,
                    target_datasource_name: str = "") -> None:
    """执行成功后采集表/字段级血缘（旁路 best-effort，失败不阻断同步主链路）。

    节点 ID 带数据源维度（`数据源名.表` / `数据源名.表.列`）：同名表在不同
    数据源是不同实体，血缘必须能区分；采集前把 SQL 的表引用限定到对应源。
    血缘属派生元数据（FDE §4 旁路）：采集失败仅记 warning，可事后补录。
    """
    try:
        from backend.common.db import get_datasource_by_name
        from backend.core.task_runtime import call
        source = get_datasource_by_name(datasource_name)
        if not source:
            logger.warning("[Lineage] 数据源 %s 不存在，跳过血缘采集", datasource_name)
            return
        qualified = _qualify_lineage_sql(
            sql_for_lineage, datasource_name, target_datasource_name)
        result = call("gov.persist_sql_lineage", 
            qualified, int(source["id"]), context.get("workspace_id") or 0)
        logger.info("[Lineage] run=%s node=%s 血缘已记录 edges=%d column_edges=%d",
                    context.get("run_id"), context.get("node_key"),
                    len(result.get("edges") or []), len(result.get("column_edges") or []))
    except Exception as exc:
        logger.warning("[Lineage] 血缘采集失败（不影响同步结果，可事后补录）: %s", exc)


def _qualify_lineage_sql(sql: str, source_ds: str, target_ds: str) -> str:
    """把血缘 SQL 的表引用限定到数据源：INSERT 目标 → target_ds，其余表 → source_ds。

    血缘节点 ID 由此获得数据源维度（sql_lineage 输出 db.table 两段名）。
    """
    import sqlglot
    from sqlglot import exp
    tree = sqlglot.parse_one(sql, read="mysql")
    inserts = [n for n in tree.find_all(exp.Table)]
    insert_target = tree.this if isinstance(tree, exp.Insert) else None
    for table in inserts:
        if table.db:
            continue
        ds = target_ds if (insert_target is not None and table is insert_target) else source_ds
        if ds:
            table.set("db", exp.to_identifier(ds))
    return tree.sql(dialect="mysql")


def _run_quality_checks(config: dict, context: dict, target_ds_name: str,
                        target_table: str, kind: str) -> dict:
    """数据落地后自动执行目标表质量检查（衍生检查，失败不回滚已成功的搬运）。

    - 执行目标表上所有启用的质量规则（含全局 workspace 0），结果标注 source；
    - 目标表无规则时自动创建「数据存在性」基础规则（按名称幂等），
      保证有数据落地就有质量检查；
    - 检查异常记录在返回值（quality.error）与日志，不静默吞掉。
    """
    summary = {"checks": 0, "passed": 0, "failed": 0}
    try:
        from backend.common.db import get_datasource_by_name, execute_query, execute_write
        from backend.core.task_runtime import call
        source = get_datasource_by_name(target_ds_name)
        if not source:
            summary["error"] = f"目标数据源 '{target_ds_name}' 不存在"
            return summary
        ds_id = int(source["id"])
        ws = int(context.get("workspace_id") or 0)
        rule_name = f"{target_table} 数据存在性（同步自动创建）"
        rules = execute_query(
            "SELECT * FROM adh_quality_rules "
            "WHERE target_datasource_id=%s AND target_table=%s AND is_active=1 "
            " AND workspace_id IN (%s, 0)",
            (ds_id, target_table, ws)) or []
        if not rules:
            exists = execute_query(
                "SELECT id FROM adh_quality_rules WHERE rule_name=%s AND target_table=%s",
                (rule_name, target_table), fetchone=True)
            if not exists:
                import time as _t
                from datetime import datetime as _dt
                execute_write(
                    "INSERT INTO adh_quality_rules "
                    "(id, workspace_id, rule_name, description, rule_type, target_datasource_id, "
                    " target_table, target_column, rule_config, severity, is_active, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,'row_count',%s,%s,NULL,%s,'medium',1,%s,%s)",
                    (int(_t.time() * 1000000), ws, rule_name,
                     f"数据同步落地自动生成：{target_table} 至少存在 1 行",
                     ds_id, target_table,
                     '{"min_rows": 1, "max_failed_rows": 0}', _dt.now(), _dt.now()))
            rules = execute_query(
                "SELECT * FROM adh_quality_rules WHERE rule_name=%s AND target_table=%s",
                (rule_name, target_table)) or []

        for rule in rules:
            try:
                result = call("gov.execute_quality_rule", 
                    rule, context["owner_identity"],
                    source={"kind": f"{kind}_auto", "run_key": str(context.get("run_id")),
                            "node": context.get("node_key")})
                summary["checks"] += 1
                summary["passed" if result.get("passed") else "failed"] += 1
            except Exception as exc:
                summary["checks"] += 1
                summary["failed"] += 1
                logger.warning("[Quality] 规则 %s 自动检查异常: %s", rule.get("rule_name"), exc)
    except Exception as exc:
        summary["error"] = f"质量检查未完成: {type(exc).__name__}"
        logger.warning("[Quality] 自动质量检查失败（不影响同步结果）: %s", exc)
    return summary


# ── 数据产品登记（加工层产出 → 治理身份）──────────────────────


def _register_product_after_write(df, target_ds: str, target_table: str,
                                  producer_kind: str, producer_ref: str,
                                  upstream_refs: list, context: dict) -> dict:
    """写入成功后把目标表登记为数据产品（幂等 upsert）。

    加工层产出的表只有登记后才能被本体引用——这是“本体只绑数据产品”
    的前提。schema 变更会产生新版本；破坏性变更标 is_breaking 并显式带回。

    纪律：登记失败**不阻断**同步主链路（同步是主功能、登记是治理旁路），
    但必须把失败与告警**显式带回返回体**，不得静默吞掉（no-silent-degradation）。
    """
    out = {"registered": False, "product_name": "", "schema_changed": False,
           "is_breaking": False, "warnings": [], "error": ""}
    try:
        from backend.core.task_runtime import get_callback
        dps = get_callback("catalog.data_product_service")

        columns = []
        if df is not None and hasattr(df, "columns"):
            dtypes = getattr(df, "dtypes", None)
            for i, col in enumerate(list(df.columns)):
                typ = ""
                try:
                    typ = str(dtypes.iloc[i]) if dtypes is not None else ""
                except Exception:  # noqa: BLE001 — 类型推断失败不阻断登记
                    typ = ""
                columns.append({"name": str(col), "type": typ, "comment": ""})

        # 产品名用“数据源.表”——全局唯一、不含内部 id，数据源重建后仍可重关联
        product_name = f"{target_ds}.{target_table}"
        res = dps.register_product({
            "product_name": product_name,
            "display_name": f"{target_ds}.{target_table}",
            "datasource_name": target_ds,
            "physical_table": target_table,
            "producer_kind": producer_kind,
            "producer_ref": producer_ref,
            "upstream_refs": upstream_refs or [],
            "data_version": str(context.get("run_id") or ""),
            "status": "draft",
        }, columns=columns, created_by=f"{producer_kind}:{context.get('run_id')}")
        out["registered"] = True
        out["product_name"] = product_name
        out["schema_changed"] = bool(res.get("schema_changed"))
        vr = res.get("version_record") or {}
        out["is_breaking"] = bool(vr.get("is_breaking"))
        out["warnings"] = list(res.get("warnings") or [])
    except Exception as e:  # noqa: BLE001 — 见 docstring：不阻断，但显式带回
        out["error"] = f"{type(e).__name__}: {e}"
        out["warnings"].append(f"数据产品登记失败(目标表 {target_ds}.{target_table}): {e}")
    return out


def run_sync_node(config: dict, context: dict) -> dict:
    from backend.modules.flow.dag.federated_reader import governed_read_dataframe
    from backend.modules.flow.dag.target_writer import write_dataframe
    from backend.modules.flow.dag.dag_service import dag_service

    source_ds = config["source_datasource"]
    source_table = config["source_table"]
    target_ds = config["target_datasource"]
    target_table = config["target_table"]
    sync_mode = config.get("sync_mode", "full")
    write_mode = config.get("write_mode", "append")
    batch_size = int(config.get("batch_size") or 1000)
    transform_sql = (config.get("transform_sql") or "").strip()
    udf_refs = config.get("udf_refs") or []

    used_versions = []
    if transform_sql:
        # 转换 SQL：以源表为数据源的 SELECT（可引用 UDF），支持 {{source}}
        # 占位符显式指定源表；UDF 展开后只剩内置函数
        from backend.modules.flow.dag.udf_registry import expand_udfs, UdfValidationError
        sql = transform_sql.replace("{{source}}", f"`{source_table}`")
        if source_table.lower() not in sql.lower():
            raise NodeExecutionError(
                "转换 SQL 未引用源表（用 {{source}} 占位或 FROM 源表）", "TRANSFORM_INVALID")
        try:
            sql, used_versions = expand_udfs(sql, udf_refs)
        except UdfValidationError as exc:
            raise NodeExecutionError(f"UDF 展开失败: {exc}", "UDF_EXPAND_FAILED") from exc
    else:
        sql = f"SELECT * FROM `{source_table}`"

    if sync_mode == "incremental":
        column = config["incremental_column"]
        watermark = dag_service.get_watermark(context["watermark_key"])
        if not transform_sql:
            if watermark is not None:
                # 水位值来自服务端记录（上次同步的最大增量列值），非用户输入
                sql += f" WHERE `{column}` > '{str(watermark).replace(chr(39), '')}'"
        sql += f" ORDER BY `{column}`"

    context["stop_check"]()
    df = governed_read_dataframe(sql, source_ds, context["owner_identity"],
                                 context["workspace_id"])
    rows_read = len(df)

    context["stop_check"]()
    # 增量追加与全量 overwrite 语义一致；增量 + overwrite 无意义，强制 append
    effective_mode = "append" if sync_mode == "incremental" else write_mode
    rows_written = write_dataframe(
        df, target_ds, target_table, effective_mode, batch_size,
        context=f"run={context['run_id']} node={context['node_key']}")

    if sync_mode == "incremental" and rows_read and not transform_sql:
        column = config["incremental_column"]
        if column in df.columns and df[column].notna().any():
            new_watermark = str(df[column].dropna().max())
            dag_service.advance_watermark(context["watermark_key"], new_watermark, rows_read)

    # 血缘采集（旁路）：构造 INSERT INTO ... SELECT 形态供血缘解析归因；
    # 表引用限定到数据源（节点 ID 带源维度）
    _record_lineage(f"INSERT INTO `{target_table}` {sql}", source_ds, context,
                    target_datasource_name=target_ds)
    quality = _run_quality_checks(config, context, target_ds, target_table, "sync")
    product = _register_product_after_write(
        df, target_ds, target_table, "sync_task",
        f"sync_task:{context.get('node_key') or ''}", [source_ds], context)
    if used_versions:
        from backend.modules.flow.dag.udf_registry import udf_registry
        udf_registry.bump_usage([item["name"] for item in used_versions])
    return {"rows_read": rows_read, "rows_written": rows_written,
            "udf_versions": used_versions, "quality": quality, "data_product": product}


# ── sql_task：SQL 同步/转换（支持跨源联邦 + UDF） ────────────────────


def run_sql_task_node(config: dict, context: dict) -> dict:
    from backend.modules.flow.dag.federated_reader import governed_federated_read
    from backend.modules.flow.dag.target_writer import write_dataframe
    from backend.modules.flow.dag.udf_registry import expand_udfs, udf_registry, UdfValidationError

    sql = (config.get("sql") or "").strip()
    udf_refs = config.get("udf_refs") or []
    try:
        expanded_sql, used_versions = expand_udfs(sql, udf_refs)
    except UdfValidationError as exc:
        raise NodeExecutionError(f"UDF 展开失败: {exc}", "UDF_EXPAND_FAILED") from exc

    context["stop_check"]()
    df, rows_read = governed_federated_read(
        expanded_sql, config["source_datasource"],
        context["owner_identity"], context["workspace_id"])

    rows_written = 0
    target = config.get("target") or {}
    if target:
        context["stop_check"]()
        rows_written = write_dataframe(
            df, target["datasource"], target["table"],
            target.get("write_mode", "append"),
            int(config.get("batch_size") or 1000),
            context=f"run={context['run_id']} node={context['node_key']}")

    if used_versions:
        udf_registry.bump_usage([item["name"] for item in used_versions])
    # 血缘采集（旁路）：有写目标时记录 源表→目标表 血缘
    quality = None
    product = {"registered": False}
    if target:
        _record_lineage(
            f"INSERT INTO `{target['table']}` {expanded_sql}",
            config["source_datasource"], context,
            target_datasource_name=target.get("datasource") or "")
        quality = _run_quality_checks(config, context, target["datasource"],
                                      target["table"], "sql_task")
        product = _register_product_after_write(
            df, target.get("datasource") or "", target["table"], "dag_workflow",
            f"dag_workflow:{context.get('workflow_id') or 0}:{context.get('node_key') or ''}",
            [config.get("source_datasource") or ""], context)
    return {"rows_read": rows_read, "rows_written": rows_written,
            "udf_versions": used_versions, "quality": quality, "data_product": product}


# ── control：控制节点（一期 pass / fail） ────────────────────────────


def run_control_node(config: dict, context: dict) -> dict:
    action = config.get("action", "pass")
    if action == "fail":
        raise NodeExecutionError(config.get("message") or "控制节点显式失败", "CONTROL_FAIL")
    return {"rows_read": 0, "rows_written": 0}
