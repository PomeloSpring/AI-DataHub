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

# 执行失败的**分类口径**（类别进日志与结果标注，对外只给类别 + 可操作下一步）。
# 为什么不能一律回 EXEC_FAIL_HINT：它把每个执行失败都说成“连接/凭据暂不可用，建议
# 稍后重试”，而 1064/1054/1146 这类是**确定性缺陷**（方言不符、表/列不存在、选错源），
# 重试永远复现。历史缺陷（会话 1783069459277）：SLS/Presto 方言的 SQL 被选到了本站的
# MySQL 业务库上，服务端报 1064（能报 1064 就说明连接是通的），而模型拿到“连接失败”文案后
# 向用户报“数据源在执行阶段连接失败（系统侧问题）+建议稍后重试”——真因被掩成抖动。
_SQL_SHAPE_MARKERS = ("1064", "syntax error", "parse error", "mismatched input",
                      "unexpected token", "not a valid sql")
_MISSING_OBJECT_MARKERS = ("1146", "1054", "unknown table", "unknown column",
                           "no such table", "no such column", "table not found",
                           "column not found", "does not exist", "doesn't exist",
                           "cannot resolve", "logstore does not exist")
_CONNECTIVITY_MARKERS = ("2002", "2003", "1045", "access denied", "authentication",
                         "connection", "connect ", "timed out", "timeout",
                         "unreachable", "reset by peer", "broken pipe",
                         "unknownhost", "no route to host", "name or service not known")


def classify_exec_failure(reason: str, db_type: str = "") -> tuple[str, str]:
    """按原始异常文本归类执行失败 → (类别, 对外文案)。

    对外文案只说类别与下一步，不含 SQL、表名、主机、账号（护栏 §7）；
    认不出来时回 `EXEC_FAIL_HINT`（归类失败不得编造原因）。
    优先级：连接/凭据类 → 表/列不存在类 → 方言/语法类（后两者文本常共现，
    先判“不存在”能避免把缺表归成语法错）。
    """
    text = str(reason or "").lower()
    src = f"（目标源类型：{db_type}）" if db_type else ""
    if any(m in text for m in _CONNECTIVITY_MARKERS):
        return "connectivity", EXEC_FAIL_HINT
    if any(m in text for m in _MISSING_OBJECT_MARKERS):
        return "missing_object", (
            f"目标数据源里没有这条 SQL 引用的表或列{src}。这是确定性失败，重试不会好："
            "表名/列名请先用 get_table_schema 或 search_metadata 确认（不要把语义对象名当表名用），"
            "并核对本次选定的数据源/站点是否就是数据所在的那个源。"
            "不得向用户展示物理表名/列名，也不得据此说“该站点无数据”。")
    if any(m in text for m in _SQL_SHAPE_MARKERS):
        return "sql_dialect", (
            f"SQL 与目标数据源的方言不匹配{src}，被服务端拒绝。这是确定性失败，重试不会好："
            "请按目标源方言重写标识符引号、时间列与函数（不同方言系对双引号标识符、"
            "UNIX 时间函数、内置时间列的写法不同）；若目标源选错了，改用 datasource（业务名）"
            "显式指向数据真正所在的那个源。不得向用户回显生成的 SQL。")
    return "unclassified", EXEC_FAIL_HINT


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


def sanitize_execute_reason(reason: str, blocked_at: str = "", db_type: str = "") -> str:
    """执行阶段原始报错（可含 MySQL Access denied/IP）仅进服务端日志，对外回分类后的可操作文案。

    非执行阶段的拒绝原因（闸门声明式文案）原样返回。
    口径与 `classify_exec_failure` 同源：不得把确定性的方言/缺表失败说成“稍后重试”（历史缺陷：
    会话 1783069459277 的 1064 被掩成“数据源连接失败（系统侧问题）”）。
    """
    reason = str(reason or "")
    if blocked_at == "execute" or reason.startswith("执行失败"):
        category, hint = classify_exec_failure(reason, db_type)
        logger.error("[semantics] execute-stage failed (%s, detail server-side only): %s",
                     category, reason)
        return hint
    return reason


def block_code(blocked_at: str) -> ErrorCode:
    """闸门拦截环节 -> 稳定错误码。未知环节按内部错误兜底（fail-loud 不吞）。"""
    return BLOCK_CODE_MAP.get(blocked_at or "", ErrorCode.INTERNAL)


# 治理串里“冒号后是逗分隔的物理列清单”的那一类前缀（enforcer.policies_applied 口径）
_GOVERNANCE_COL_PREFIXES = ("sensitive_block", "sensitive_fields", "hidden_columns", "column_policy")


def safe_governance_notes(
    applied_rls: list[str] | None,
    masked_columns: list[str] | None,
    label_map: dict[str, str] | None = None,
    *,
    redact: list[str] | None = None,
) -> dict[str, list[str]]:
    """把治理生效信息里的**物理列名**换成业务名（护栏 §7：数据源对 LLM 是黑盒）。

    入参形如 `"sensitive_block:activator_ip,mobile"`（全局敏感基线按列名登记，跨源
    同名即在册，**对所有数据源生效是正确设计**）。直接下发会出两类事：
      1) 物理列名进 LLM 视野后被转述给用户（历史缺陷：回答里出现 `activator_ip`）；
      2) 模型拿“某个字段的治理策略”去推断**另一个字段**被屏蔽/不存在
         （历史缺陷：基线里的 `activator_ip` 被推断成“RUM 的 IP 字段被合规屏蔽”，
         而 RUM 的 IP 是 `net.ip`，是另一个字段）。

    口径：
      - 能映到本对象列（label_map: 物理列 -> 业务名）→ 只报业务名；
      - 映不到 → **不列名**，只报数量并明确“只约束它自己那几列，不得跨字段推断”；
        被 block 的列定义上不会返回，该陈述可验证，不是降级掩盖；
      - 非列清单类条目过 `LEAK_KEYWORDS` 与 `redact`（物理表名）后才保留；
      - 原始策略串仍完整留在服务端 provenance/审计与评测契约里，本函数只影响对 LLM 下发。
    """
    lm: dict[str, str] = {}
    for col, name in (label_map or {}).items():
        for key in (str(col), str(col).rsplit(".", 1)[-1]):
            if key:
                lm.setdefault(key.lower(), str(name))
    redacts = [str(r) for r in (redact or []) if r]

    def _label_of(token: str) -> str:
        t = token.strip().lower()
        return lm.get(t) or lm.get(t.rsplit(".", 1)[-1]) or ""

    def _redactable(text: str) -> bool:
        low = text.lower()
        return any(k in low for k in LEAK_KEYWORDS) or any(r.lower() in low for r in redacts)

    notes: list[str] = []
    for raw in list(applied_rls or []):
        s = str(raw or "").strip()
        if not s:
            continue
        kind, _, payload = s.partition(":")
        tokens = [t.strip() for t in payload.split(",") if t.strip()] if payload else []
        if kind in _GOVERNANCE_COL_PREFIXES and tokens:
            named = [lbl for lbl in (_label_of(t) for t in tokens) if lbl]
            if named:
                uniq = sorted(set(named))
                extra = len(tokens) - len(named)
                text = f"{kind}: 已对 {len(uniq)} 列生效（{'、'.join(uniq)}）"
                if extra > 0:
                    text += f"；另有 {extra} 个受管控列是其他字段（不同列名），未作用于本次结果"
                notes.append(text)
            else:
                notes.append(
                    f"{kind}: 全局敏感字段基线在册 {len(tokens)} 列（按列名匹配、对所有数据源生效），"
                    "本次查询的字段与它们是不同列名：基线只约束它自己那几列，"
                    "不得据此推断别的字段被屏蔽/不存在；某字段是否可用只看 get_metrics 目录与实际取数"
                )
            continue
        if _redactable(s):
            continue
        if s not in notes:
            notes.append(s)

    masked_out: list[str] = []
    unmapped = 0
    for col in list(masked_columns or []):
        lbl = _label_of(str(col))
        if lbl:
            if lbl not in masked_out:
                masked_out.append(lbl)
        else:
            unmapped += 1
    if unmapped:
        masked_out.append(f"另有 {unmapped} 个脱敏列是其他字段（不同列名），未作用于本次结果")
    return {"applied_rls": notes, "masked_columns": masked_out}


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
