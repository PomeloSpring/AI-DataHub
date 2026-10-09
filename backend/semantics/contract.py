"""语义层跨语言唯一契约 —— JSON Schema 导出 + 错误口径（Phase 6 契约先行）。

`SemanticQuery` / `SemanticResult`（models.py，全部 `extra="forbid"`）是跨语言唯一契约：
本模块导出其 JSON Schema（前端/外部客户端/CI 据此生成类型与校验），并登记
**错误码与对外失败文案的唯一口径**（护栏 §7）：

- 引擎原始错误仅进服务端日志，对外一律通用文案（`EXEC_FAIL_HINT`）；
- 对外下发文本绝不回显 SQL/数据源/主机/账号/IP/物理表（`LEAK_KEYWORDS` 过滤）；
- 场景特化文案（如数据集换源提示、DAG 节点提示）须自行保证不含敏感细节，
  执行阶段失败的通用口径以本模块为唯一真源。
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from backend.semantics.models import (
    SemanticFilter,
    SemanticOrder,
    SemanticQuery,
    SemanticResult,
)

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "1.0"


class ErrorCode(str, enum.Enum):
    """对外稳定错误码（SemanticError.code）。只增不改，跨版本兼容。"""

    INVALID_INTENT = "INVALID_INTENT"         # intent 非法/含 SQL 字段被拒
    UNBOUND = "UNBOUND"                       # 对象未绑定到任何物理表
    NO_IDENTITY = "NO_IDENTITY"               # 无可信身份（fail-closed）
    FORBIDDEN = "FORBIDDEN"                   # 权限拒绝（数据源/表/列 RBAC）
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"   # 护栏拒绝（full_scan/size/LIMIT 等）
    RLS_BLOCKED = "RLS_BLOCKED"               # RLS 改写失败/行策略拒绝
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"   # 高风险需提案审批
    EXEC_FAILED = "EXEC_FAILED"               # 执行阶段失败（对外通用文案）
    INTERNAL = "INTERNAL"                     # 语义层内部错误（对外通用文案）


# 执行阶段失败对外统一文案（原始报错仅进服务端日志，护栏 §7）。
EXEC_FAIL_HINT = (
    "取数在执行阶段失败（通常是数据源连接/凭据或物理表暂不可用）。这是系统侧问题，"
    "与你的查询意图无关。请勿猜测或向用户展示数据源主机、账号、IP、生成的 SQL 等细节，"
    "建议稍后重试或联系管理员核实数据源可用性。"
)

# 可能泄露物理表/数据源/catalog 的告警关键字，不随结果对外下发。
LEAK_KEYWORDS = ("physical_table", "catalog", "datasource", "catalog_ref", "not in adh_table_info")

# 闸门拦截环节 -> 对外错误码（gates.blocked_at 口径）
BLOCK_CODE_MAP: dict[str, ErrorCode] = {
    "intent": ErrorCode.INVALID_INTENT,
    "binding": ErrorCode.UNBOUND,
    "identity": ErrorCode.NO_IDENTITY,
    "permission": ErrorCode.FORBIDDEN,
    "preflight": ErrorCode.GUARDRAIL_BLOCKED,
    "proposal": ErrorCode.GUARDRAIL_BLOCKED,
    "rls": ErrorCode.RLS_BLOCKED,
    "approval": ErrorCode.APPROVAL_REQUIRED,
    "execute": ErrorCode.EXEC_FAILED,
}


class SemanticError(Exception):
    """语义层对外错误。message 一律为可安全下发的通用/声明式文案。

    原始引擎错误不得进 message（仅 logger），detail 仅在服务内消费、不得整包下发。
    """

    def __init__(self, code: ErrorCode, message: str, *,
                 blocked_at: str = "", detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.blocked_at = blocked_at
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        """对外安全投影（LLM/前端只拿这个，不拿 detail）。"""
        return {"error": self.message, "code": self.code.value, "blocked_at": self.blocked_at}


def safe_warnings(*groups: list[str] | None) -> list[str]:
    """过滤掉会泄露物理表/数据源/catalog 细节的告警，仅保留可下发文本。"""
    out: list[str] = []
    for ws in groups:
        for w in (ws or []):
            lw = str(w).lower()
            if any(k in lw for k in LEAK_KEYWORDS):
                continue
            if w and w not in out:
                out.append(w)
    return out


def sanitize_execute_reason(reason: str, blocked_at: str = "") -> str:
    """执行阶段原始报错（可含 MySQL Access denied/IP）仅进服务端日志，对外回通用文案。

    非执行阶段的拒绝原因（闸门声明式文案）原样返回。
    """
    reason = str(reason or "")
    if blocked_at == "execute" or reason.startswith("执行失败"):
        logger.error("[semantics] execute-stage failed (detail server-side only): %s", reason)
        return EXEC_FAIL_HINT
    return reason


def block_code(blocked_at: str) -> ErrorCode:
    """闸门拦截环节 -> 稳定错误码。未知环节按内部错误兜底（fail-loud 不吞）。"""
    return BLOCK_CODE_MAP.get(blocked_at or "", ErrorCode.INTERNAL)


def export_schemas() -> dict[str, Any]:
    """导出跨语言唯一契约的 JSON Schema（OpenAPI 可直接 $ref）。

    SemanticQuery/SemanticResult 各自携带 $defs（SemanticFilter/SemanticOrder/
    ResultColumn 等嵌套模型），additionalProperties=false 由 extra="forbid" 保证。
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "SemanticQuery": SemanticQuery.model_json_schema(),
        "SemanticResult": SemanticResult.model_json_schema(),
        "SemanticFilter": SemanticFilter.model_json_schema(),
        "SemanticOrder": SemanticOrder.model_json_schema(),
    }
