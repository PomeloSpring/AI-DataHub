"""LLM 出站结果的敏感信息守卫（工具结果流向 LLM 的唯一收口）。

为什么需要它
------------
security-guardrails §7 要求：取数结果严禁向 LLM / 普通客户端泄露生成的 SQL、
``datasource_id``、``catalog_ref``、``physical_table``、provenance、账号/主机地址
（基础设施 IP）、原始报错栈。

此前这些脱敏散落在各 handler（``catalog_tools._strip_physical_ids``、
``semantic_query._safe_warnings``），每个工具各管一段，新增工具很容易漏掉。
本模块把口径收敛成**一份常量**，并在 ``compat.make_tool.guarded`` 的收口处强制执行
—— 任何工具的结果都要过这里，漏写脱敏会在出口被拦下。

与敏感字段基线的分工（不要混）
------------------------------
* **本模块**：只管**基础设施 / 凭据 / 内部标识**泄露（连接串、密码、Token、主机
  地址、物理表列名、报错栈）。这些是"配置面"的泄漏，任何工具都不该输出。
* **业务数据内容**（IP / 手机号 / 邮箱 / 身份证等，含业务实体的客户端 IP）：
  唯一裁决点是**数据权限** —— `adh_sensitive_fields`（全局 datasource_id=0/表名空
  ∪ 指定数据源/表，按物理列名）：`block` → `gates._blocked_column_guard` 执行前硬拒
  + 结果剔除；`mask`(full/partial/hash) → `query_executor` 结果侧 `apply_post_processing`
  按列名施加（对所有人生效含 admin）。生效点与已知缺口（SLS ASCII 槽位列名对不上
  → mask 不落地）见护栏 §7。

**禁止在本模块 hardcode 业务数据的内容形态规则**（2026-10-30 决策）。
历史缺陷：值形态正则 `ipv4_literal`（裸 IPv4 匹配）把已治理的业务「客户端IP」维度
**整体拒发** —— 管理员在「敏感数据」页查不到任何配置依据（它是代码常量，不是规则），
改 mask 也救不回来；同时中文维度名「客户端IP」被裸 `ip` 键名规则误判为内部键，
两条叠加 = 该维度无论怎么配权限都出不来。裁决权在数据权限，出站层不得越权替它判。

已知边界
--------
若业务表真有名为 ``token`` / ``password`` / ``host`` 的列，其数据行也会命中本守卫
并被拒绝下发。这是**有意的**（fail-loud）：这类列名本身就是泄露信号，正确做法是
把该列登记进 ``adh_sensitive_fields`` 走列级脱敏，而不是让守卫放行。
键名判定按**完整键名**取边界（``\w`` 为 Unicode 口径，中文算词字符）：
``"客户端IP":`` 是业务标签不是内部键 ``ip``，不得命中（见 ``_KEY_RE`` 注释）。

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
# 判定是"**完整**键名出现即命中"，不看值 —— 值为空也不该暴露这个字段的存在与结构。
# 本清单只放基础设施/凭据/内部标识键，不得拿它当业务敏感列的裁决手段（见上面分工）。
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
# 前导边界用 Unicode 的 ``\w``（含中文等有字词形），不能只排 ASCII：历史缺陷是
# ``(?<![A-Za-z0-9_])`` 把中文当边界，于是业务维度标签 ``"客户端IP":`` 被误判成
# 内部键 ``ip``（ASCII 前缀的 ``client_ip`` 反倒不命中）—— 业务列名一律被拒发。
_KEY_RE = re.compile(
    r"(?i)(?<!\w)(?:"
    + "|".join(re.escape(k) for k in SENSITIVE_OUTPUT_KEYS)
    + r")\s*[\"']?\s*[:=]"
)

# ── 禁止外泄的值形态（与键名无关，凭形态即可判定）──────────────────────────────
# 值形态规则**只收基础设施/凭据/SQL/报错这类与业务列无关的泄露形态**。
# 不得加 IP 字面量、手机号、邮箱之类的"业务数据内容"正则：那些由
# ``adh_sensitive_fields`` 在执行前注入 SQL 裁决（见模块 docstring 的分工）。
# 不要在值形态里加「主机名:端口」裸匹配：`14:30`、ISO 时间戳 `2026-10-03T14:30:00`
# 都会命中，正常数据行会被误杀。裸 host/port 已由上面的键名检测覆盖。
_VALUE_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("connection_string", re.compile(
        r"(?i)\b(?:jdbc|mysql|postgres|postgresql|redis|mongodb|clickhouse|doris|"
        r"oracle|sqlserver|mssql|ftp|sftp|smtp)://[^\s\"']+")),
    ("credential_in_url", re.compile(r"(?i)://[^\s\"'/]+:[^\s\"'/]+@")),
    # 生成 SQL 回显（守 §7：base_sql/secured_sql 不得下发）。
    # 要求表标识含 `_` 或 `.`（adh_reports / db.table），
    # 否则英文散文 "select X from the list" 会被误杀。
    # 前导边界用“非标识符字符”而不是 ASCII 空白/标点：历史缺陷是字典口径里的 SQL
    # 接在中文标点后面（“口径：select count(1) from db.tbl”）绕过检测下发给了 LLM。
    ("generated_sql", re.compile(
        r"(?is)(?<![0-9A-Za-z_])(?:select|with)\b[\s\S]{0,400}?\bfrom\s+[`\"']?"
        r"\w+[_.]\w+[`\"']?")),
    ("error_stack", re.compile(
        r"Traceback \(most recent call last\)|\bFile \"[^\"]+\.py\", line \d+")),
    # 数据库/驱动原始报错：天然携带物理表名/列名/SQL 片段（历史缺陷：catalog 旧列名
    # 报错 `(1054, "Unknown column 'metric_name' in 'field list'")` 被直接下发给 LLM）。
    # 只匹配驱动错误的固定签名，不匹配裸表名，避免误杀“请先对表 X 执行元数据同步”这类可操作提示。
    ("raw_db_error", re.compile(
        r"(?i)Unknown column\b|Unknown table\b|\bin 'field list'\b|"
        r"\b(?:table|column|database|view) [^\n]{0,60}?doesn't exist|"
        r"You have an error in your SQL syntax|ERROR \d{3,5} \[|\bORA-\d{4,5}\b|"
        r"\b(?:pymysql|MySQLdb|psycopg2|sqlite3)\.err|SQLException|SQL compilation error")
    ),
)

# 与 semantics.contract.LEAK_KEYWORDS 同源的历史口径，保留兼容（旧告警文本里出现即算命中）。
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
