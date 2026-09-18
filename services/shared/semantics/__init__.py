"""Semantic layer shared package (Phase 1).

BI/ChatBI -> 语义层 -> 执行层 三层架构中间层的进程内共享模块。
Phase 1: 只做 pydantic 契约 + binding 解析 + MDL 编译 + 选路 planner,
Phase 3/4 抽为独立 `services/semanticservice`(:8012)HTTP 服务时,
本包保留为服务实现依赖 + 现有 datamind/datacatalog 的过渡接口。
"""

from services.shared.semantics.models import (
    AccessControl,
    BindKind,
    FilterOp,
    Guardrail,
    PlannedExecution,
    QueryMode,
    ResolvedBinding,
    ResultColumn,
    SemanticFilter,
    SemanticOrder,
    SemanticQuery,
    SemanticResult,
    SizeClass,
    TimeGrain,
    normalize_object_ref,
)

__all__ = [
    "AccessControl", "BindKind", "FilterOp", "Guardrail",
    "PlannedExecution", "QueryMode", "ResolvedBinding", "ResultColumn",
    "SemanticFilter", "SemanticOrder", "SemanticQuery", "SemanticResult",
    "SizeClass", "TimeGrain", "normalize_object_ref",
]
