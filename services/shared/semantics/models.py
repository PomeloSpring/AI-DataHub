"""Semantic Layer shared models (Phase 1).

BI/ChatBI 与 dataviz 大屏共用的**声明式**契约 (JSON-over-REST DSL),
不上 GraphQL;字段设计为 GraphQL-subset 兼容(将来对外可门面化)。

- SemanticQuery: 用户/LLM/大屏 -> 语义层 的"意图"
- SemanticResult: 语义层 -> 调用方 的"结果 + 溯源 + 已生效 RLS/护栏"
- ResolvedBinding / Guardrail / AccessControl: 语义层内部解析产物, 与
  `adh_ontology_bindings` / `adh_table_info` 扩列一一对齐。
- PlannedExecution: planner 输出的执行方案 (选路 + sidecar)。

约定:所有模型 `extra="forbid"` 保证契约稳定, 上游字段漂移时显式失败。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

QueryMode = Literal["materialized", "federated", "raw_source"]
SizeClass = Literal["small", "large", "huge"]
BindKind = Literal["primary", "lateral", "bridge", "metric_source", "sql_template"]
FilterOp = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte",
    "in", "nin", "like", "between", "is_null", "is_not_null",
]
TimeGrain = Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]


# ── 意图 (LLM 与大屏共用) ─────────────────────────────────────────

class SemanticFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dim: str = Field(..., description="维度名: 对象属性 / dimension.name / 物理列名")
    op: FilterOp = "eq"
    value: Any = None
    values: Optional[list[Any]] = None        # in / nin / between 用
    grain: Optional[TimeGrain] = None        # 时间维度可选粒度


class SemanticOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: str
    desc: bool = False


class SemanticQuery(BaseModel):
    """LLM/大屏对语义层的唯一入口契约。

    注意:**不含 SQL**,只声明"要什么"。SQL 由语义层编译。
    """
    model_config = ConfigDict(extra="forbid")

    object: str = Field(..., description="本体对象 IRI 或 object_key, e.g. 'Patient' / 'obj:Patient'")
    metrics: list[str] = Field(default_factory=list, description="指标名列表(adh_metrics.name)")
    dimensions: list[str] = Field(default_factory=list, description="分组维度(adh_dimensions.name 或对象属性)")
    filters: list[SemanticFilter] = Field(default_factory=list)
    order: list[SemanticOrder] = Field(default_factory=list)
    limit: Optional[int] = Field(None, ge=1, le=10000)
    time_grain: Optional[TimeGrain] = None
    time_window: Optional[str] = Field(
        None, pattern=r"^\d{1,4}[smhdwMy]$",
        description="相对时间窗(滚动窗口), 形如 '7d'/'24h'/'30m'/'2w'/'1M'; "
                    "编译为 源库方言 的 now()-interval 谓词, 与 filters AND 叠加",
    )
    time_column: Optional[str] = Field(
        None, description="time_window 作用的事件时间列(维度名/物理列); 缺省回落 category='时间' 的首个维度",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="SQL 模板对象的具名参数(仅 bind_kind='sql_template' 接受); "
                    "键必须在模板 variables 声明内, 值按声明类型校验后渲染",
    )
    datasource_id: int = 0
    workspace_id: int = 0
    user_id: int = 0
    dry_run: bool = Field(False, description="只编译不执行, 供 Playground/审计预演")


# ── 解析产物 ────────────────────────────────────────────────────

class Guardrail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_mode: QueryMode = "materialized"
    size_class: Optional[SizeClass] = None
    allow_full_scan: bool = True
    max_rows: Optional[int] = None
    timeout_sec: Optional[int] = None
    force_limit: bool = Field(False, description="raw_source 或 huge 时强制 LIMIT")


class AccessControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_tokens: list[str] = Field(default_factory=list)
    rls_policy_refs: list[str] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)


class ResolvedBinding(BaseModel):
    """一个对象 -> 物理表 (catalog.db.table) 的绑定视图, 由 binding_resolver 产出。"""
    model_config = ConfigDict(extra="forbid")

    object_key: str
    model_id: Optional[int] = None
    datasource_id: int
    datasource_name: str = Field(
        "", description="数据源名(全局唯一标识); 解析时用它映射到当前有效 datasource_id, 删除重建后仍可关联",
    )
    catalog_name: str = "adh"
    db_name: str = ""
    physical_table: str
    catalog_ref: str = Field("", description="catalog.db.table 三段式")
    bind_kind: BindKind = "primary"
    template_ref: str = Field("", description="bind_kind='sql_template' 时指向 adh_sql_templates.template_id")
    db_type: str = Field("mysql", description="数据源协议/方言族: mysql|doris -> mysql 方言; postgres|postgresql|pg|sls -> PG 方言")
    join_expr: str = ""
    column_map: dict[str, str] = Field(default_factory=dict)
    guardrail: Guardrail = Field(default_factory=Guardrail)
    access: AccessControl = Field(default_factory=AccessControl)
    sync_state: Literal["bound", "drifted", "orphaned", "unbound"] = "bound"
    source: Literal["adh_ontology_bindings", "adh_table_info", "adh_ontology_objects.execution_binding"] = \
        "adh_ontology_bindings"


# ── 计划 ────────────────────────────────────────────────────────

class PlannedExecution(BaseModel):
    """planner 输出:选路 + 基线 SQL + 元数据, 交给执行层。"""
    model_config = ConfigDict(extra="forbid")

    sql: str = Field("", description="基线 SQL (未经 RLS 改写)")
    secured_sql: str = Field("", description="经语义层 RLS sqlglot 改写后的最终 SQL")
    dialect: str = "mysql"
    route: QueryMode = "materialized"
    datasource_id: int = 0
    resolved_tables: list[str] = Field(default_factory=list)
    guardrail: Guardrail = Field(default_factory=Guardrail)
    access: AccessControl = Field(default_factory=AccessControl)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# ── 结果 ────────────────────────────────────────────────────────

class ResultColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: Literal["measure", "dimension", "time", "key", "fk", "plain"] = "plain"
    data_type: str = ""
    unit: str = ""


class SemanticResult(BaseModel):
    """语义层对外的统一响应。ChatBI 与 dataviz 共用, 支持同源一致性。"""
    model_config = ConfigDict(extra="forbid")

    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    applied_rls: list[str] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)
    resolved_tables: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    # dry_run / Playground 专用: 展示语义层编译管线的每一环
    debug: Optional[dict[str, Any]] = None


# ── 便捷 ────────────────────────────────────────────────────────

def normalize_object_ref(obj: str) -> str:
    """把 'obj:Patient' / 'http://.../obj:Patient' / 'Patient' 归一为 object_key。"""
    if not obj:
        return ""
    s = obj.strip()
    if "/obj:" in s:
        return s.split("/obj:", 1)[1]
    if s.startswith("obj:"):
        return s[4:]
    if ":" in s and not s.startswith("http"):
        # 'ns:Key' 型 prefix, 只取 Key
        return s.split(":", 1)[1]
    return s
