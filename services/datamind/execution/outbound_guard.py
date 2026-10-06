"""LLM 出站结果的敏感信息守卫（工具结果流向 LLM 的唯一收口）。

为什么需要它
------------
security-guardrails §7 要求：取数结果严禁向 LLM / 普通客户端泄露生成的 SQL、
``datasource_id``、``catalog_ref``、``physical_table``、provenance、账号/IP/主机、
原始报错栈。

此前这些脱敏散落在各 handler（``catalog_tools._strip_physical_ids``、
``semantic_query._safe_warnings``），每个工具各管一段，新增工具很容易漏掉。
本模块把口径收敛成**一份常量**，并在 ``compat.make_tool.guarded`` 的收口处强制执行
—— 任何工具的结果都要过这里，漏写脱敏会在出口被拦下。

与敏感字段基线的分工（不要混）
------------------------------
* **本模块**：管**基础设施 / 凭据 / 内部标识**泄露（连接串、密码、Token、主机 IP、
  物理表列名、报错栈）。这些是"配置面"的泄漏，任何工具都不该输出。
* **敏感字段基线**（``permission_enforcer`` + ``adh_sensitive_fields``）：管**业务数据
  内容**（手机号、身份证、邮箱等），按列做 block/mask，对所有人生效含 admin。

业务数据行里出现手机号是正常的（已由基线脱敏），不该被本模块判失败；反过来，
配置里出现密码也不该指望列级基线拦住。所以本模块**不匹配** PII 正则。

已知边界
--------
若业务表真有名为 ``token`` / ``password`` / ``host`` 的列，其数据行也会命中本守卫
并被拒绝下发。这是**有意的**（fail-loud）：这类列名本身就是泄露信号，正确做法是
把该列登记进 ``adh_sensitive_fields`` 走列级脱敏，而不是让守卫放行。

处置策略：fail-loud，不静默清空
------------------------------
命中即拒绝下发：返回 ``isError`` + 命中类别，服务端日志留全量原文供排查。
**不**把命中的字段悄悄删掉再返回部分结果 —— 静默清空会让缺陷逃过所有人眼睛，
直到给出错答案才暴露（no-silent-degradation §1）。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── 禁止外泄的字段名（JSON 键 / 赋值左值 / 形参名）──────────────────────────────
# 判定是"键名出现即命中"，不看值 —— 值为空也不该暴露这个字段的存在与结构。
SENSITIVE_OUTPUT_KEYS = (
    # 连接凭据 / 密钥 / 令牌
    "password", "passwd", "pwd", "passphrase",
    "secret", "client_secret", "webhook_secret", "app_secret",
    "token", "access_token", "refresh_token", "share_token_hash",
    "webhook_token", "api_key", "apikey", "access_key", "secret_key",
    "private_key", "credential", "credentials", "authorization", "auth_token",
    # 基础设施位置
    "host", "hostname", "ip", "ip_address", "port", "endpoint", "jdbc_url",
    "connection_string", "conn_str", "dsn",
    # 内部标识 / 生成 SQL / 溯源（守 §7 黑盒）
    "datasource_id", "catalog_ref", "physical_table", "base_sql", "secured_sql",
    "provenance", "security_context",
)

# 键名后跟 : 或 = ，容忍引号与空白（匹配 "password": "x" / password=x / 'password' => 'x'）
_KEY_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:"
    + "|".join(re.escape(k) for k in SENSITIVE_OUTPUT_KEYS)
    + r")\s*[\"']?\s*[:=]"
)

# ── 禁止外泄的值形态（与键名无关，凭形态即可判定）──────────────────────────────
# 不要在值形态里加「主机名:端口」裸匹配：`14:30`、ISO 时间戳 `2026-10-03T14:30:00`
# 都会命中，正常数据行会被误杀。裸 host/port 已由上面的键名检测覆盖。
_VALUE_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("connection_string", re.compile(
        r"(?i)\b(?:jdbc|mysql|postgres|postgresql|redis|mongodb|clickhouse|doris|"
        r"oracle|sqlserver|mssql|ftp|sftp|smtp)://[^\s\"']+")),
    ("credential_in_url", re.compile(r"(?i)://[^\s\"'/]+:[^\s\"'/]+@")),
    ("ipv4_literal", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b")),
    # 生成 SQL 回显（守 §7：base_sql/secured_sql 不得下发）。
    # 要求表标识含 `_` 或 `.`（adh_reports / db.table），
    # 否则英文散文 "select X from the list" 会被误杀。
    ("generated_sql", re.compile(
        r"(?is)(?:^|[\s(,;])(?:select|with)\b[\s\S]{0,400}?\bfrom\s+[`\"']?"
        r"\w+[_.]\w+[`\"']?")),
    ("error_stack", re.compile(
        r"Traceback \(most recent call last\)|\bFile \"[^\"]+\.py\", line \d+")),
)

# 与 semantic_query._LEAK_KEYWORDS 同源的历史口径，保留兼容（旧告警文本里出现即算命中）。
_LEGACY_LEAK_KEYWORDS = ("physical_table", "catalog_ref", "not in adh_table_info")


def find_sensitive_hits(text: str) -> list[str]:
    """返回命中的敏感类别（去重、稳定顺序）。空列表 = 干净。"""
    if not text:
        return []
    hits: list[str] = []
    if _KEY_RE.search(text):
        hits.append("sensitive_field")
    for label, pattern in _VALUE_RES:
        if pattern.search(text):
            hits.append(label)
    for kw in _LEGACY_LEAK_KEYWORDS:
        if kw in text and "legacy_leak" not in hits:
            hits.append("legacy_leak")
    # 稳定顺序：按声明顺序
    order = ["sensitive_field"] + [label for label, _ in _VALUE_RES] + ["legacy_leak"]
    return [h for h in order if h in hits]


def _result_texts(result) -> list[str]:
    """从 CallToolResult 里取出全部待下发文本。"""
    if result is None:
        return []
    if isinstance(result, str):
        return [result]
    if isinstance(result, dict):
        out = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text") or ""))
        return out
    return []


def assert_no_sensitive(result, tool_name: str = ""):
    """出站收口：命中敏感信息就拒绝下发（fail-loud），否则原样返回。

    Returns:
        原 result（干净时），或 ``isError`` 结果（命中时，含命中类别）。
        服务端日志保留原始文本以便排查 —— 对外不回显原文。
    """
    texts = _result_texts(result)
    hits: list[str] = []
    for t in texts:
        for h in find_sensitive_hits(t):
            if h not in hits:
                hits.append(h)
    if not hits:
        return result

    logger.error("[outbound_guard] 工具 %s 的结果含敏感信息，已拒绝下发(命中=%s)。原文前 2000 字: %s",
                 tool_name or "<unknown>", hits, " || ".join(texts)[:2000])
    return {
        "content": [{
            "type": "text",
            "text": (f"工具结果包含不应下发给 AI 的敏感信息，已拒绝返回。"
                     f"命中类别: {', '.join(hits)}。"
                     f"请通知管理员检查该功能的输出脱敏；服务端日志已留原始内容。"),
        }],
        "isError": True,
    }
