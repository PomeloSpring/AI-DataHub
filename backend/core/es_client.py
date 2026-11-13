"""Elasticsearch 取数的唯一 REST 口 —— `/_sql` + `/_sql/translate` + 索引开放范围硬门。

定位（2026-11-13 阶段二/三定稿）：ES 是**正式数据源**（`adh_datasources.db_type='es'`），
返回数据行的取数一律经治理执行口 `core.query_executor._execute_via_es_client`（列级
屏蔽/脱敏与行级策略在上游 `secure_read_sql` 就已编译进正文）。本模块只负责它下面那一层：
REST 调用、方言硬门（只读/单表/必带 LIMIT）、开放范围裁决与错误文案脱敏。

不复用 `semantics.execution` 的连接器体系：那套的前提是 SQL 可被 sqlglot 按目标方言
解析回写、RLS 可包裹子查询注入；ES SQL 两者都不成立（无 JOIN、子查询仅支持可扁平化
的形状、sqlglot 无 elasticsearch 方言）。行级策略因此改走「AND 进顶层 WHERE」（
`semantics.sql_guard._inject_filters_es`），护栏 §1 约束的是“必须过治理执行口”，
不是“必须用同一个连接器”。

安全约定（护栏 §5 / §7）：
- 只读：仅允许单条 SELECT，拒 DDL/DML/多语句；缺 LIMIT 直接拒绝（不静默补，
  因为补在谁身上决定扫描规模，让调用方显式写）。
- 开放范围：数据源登记的索引别名/模式之外一律拒，空白名单 = 不开放（fail-closed）。
- 错误文案**不含** host / 端口 / 账号 / 凭据 / 原始响应体（EsError 只回业务原因）。
- 凭据来自数据源行（服务端解密），只活在进程内的 EsCtx 与连接池里，不出入返回值与日志。
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

_SQL_PATH = "/_sql"
_SQL_TRANSLATE_PATH = "/_sql/translate"
_FIELD_CAPS_PATH = "/_field_caps"
# ES `index.max_result_window` 默认值：from/size 只能翻到第 1 万行，超出后 ES 要么报错
# 要么（多索引模式下）返 200 + 空 hits + 分片失败。深分页一律走 search_after，不用 from。
_MAX_RESULT_WINDOW = 10000
# 连接池默认并发连接数（数据源可用 `extra_config.pool_size` 覆盖）。
# 过去每次 `requests.request` 新建连接：一块看板 30 张图 = 30 次 TLS 握手，
# 而 ES SQL 本身只跑几秒 —— 握手与 TCP 慢启动在图表扇出时是真实成本。
DEFAULT_POOL_SIZE = 10
# 数据源没配预算时的代码缺省。**不**拿 `config.ES_*`（全局 .env）当数据源级兜底：
# ES 已是正式数据源，预算只有一个可配层（数据源高级配置）；再留一条全局通道
# 就会变成“界面改了没反应、变量在 .env 里”的双口径（内置通道退役后该彻底断开）。
DEFAULT_TIMEOUT_SEC = 20
DEFAULT_MAX_ROWS = 1000

# ES 错误类型 → 业务可操作指引（只讲本通道能做什么/不能做什么，不转述响应体自由文本）。
# 错因分类靠 ES 自己的 `error.type`，比拿关键字猜可靠；拿不到类型时回退到旧文案。
_ES_TYPE_HINTS: dict[str, str] = {
    "parsing_exception": (
        "语法不被 Elasticsearch SQL 支持：本通道无派生表/窗口函数/OFFSET，"
        "且标识符要按 ES SQL 写法引号包裹；请把聚合直接写进正文。"),
    "sql_parse_exception": (
        "SQL 解析失败：本通道无派生表/窗口函数/OFFSET，函数写法以 ES SQL 为准"
        "（如取尾用 RIGHT、月度桶用 SUBSTRING）。"),
    "verification_exception": (
        "字段或索引在目标里不存在/类型不匹配：请核对字段名（文本字段分组应用 .keyword）"
        "与索引是否在开放范围内。"),
    "index_not_found_exception": (
        "索引/别名不存在或已改名：请在「数据源」页核对开放范围（可查索引别名）。"),
    "illegal_argument_exception": (
        "查询参数非法：常见于 FROM 目标与开放范围不匹配、或对不支持的形状做了分页。"),
}


class EsError(ValueError):
    """对外可展示的 ES 错误：文案只允许含索引名、字段名与业务原因。

    严禁携带连接地址、账号、凭据或 ES 原始响应（调用方直接把它回给 LLM/前端）。

    `safe_business_message` 是给治理口看的标记（`semantics.execute.execute_sql`）：
    本类的文案按上面的约定**已经脱敏且面向人**（开放范围清单、超时预算建议、别名失效），
    把它归类成通用 `EXEC_FAIL_HINT` 会把可操作提示降级成“系统侧问题，稍后重试”。
    """

    safe_business_message = True


# ── 连接配置上下文：ES 目标只有「数据源行」一个来源 ──────────────────────
#
# 内置通道（`.env` 托管、`datasource_id=0`）已退役：ES 是正式数据源，连接地址、开放
# 范围与预算都只在「数据源」页那一条行上。也不保留“缺 ctx 就回退全局配置”的双来源
# 窗口 —— 那会把“忘了绑数据源”的缺陷变成一次打到另一个集群的查询（no-silent-degradation）。


@dataclass(frozen=True)
class EsCtx:
    """一个 Elasticsearch 目标的连接与开放范围（凭据只在服务端，不出入返回值与日志）。"""

    datasource_id: int          # 恒 > 0：ES 型数据源行 id（内置通道已退役，不再有 .env 托管目标）
    label: str                  # 业务可读名（错误文案用，不含地址/账号）
    base_url: str
    username: str
    password: str
    api_key: str
    verify_certs: bool
    timeout_sec: int
    max_rows: int
    allowed: tuple[str, ...]    # 开放范围：索引别名与 `t-别名*` 模式的并集
    # 连接池大小（同一目标可复用的 HTTP 连接数）：按数据源可配，不同源的并发预算不同
    pool_size: int = DEFAULT_POOL_SIZE


def _require_ctx(ctx: Optional["EsCtx"]) -> "EsCtx":
    """ES 目标上下文必须显式给定：内置通道（`.env` 托管、`datasource_id=0`）已退役。

    不做“缺 ctx 就拿全局配置兜底”：那等于把“调用方忘了带数据源”的缺陷
    变成一次打到另一个集群/另一份开放范围的查询，结果看着像正常的数，审计里却归不到源。
    """
    if ctx is None:
        raise EsError(
            "Elasticsearch 取数必须绑定 ES 型数据源（内置通道已退役）："
            "请在「数据源」页登记 ES 源，并在数据集上选择它")
    return ctx


def es_alias_of(target: Any) -> str:
    """索引目标 → 逻辑表名（索引别名）。

    实机校准（2026-11-13）：集群里每个双写月度族都配了同名别名
    （`t_software_app_operation_log` ↔ 索引 `t-t_software_app_operation_log-...-YYYY.MM`），
    因此同一份数据有两种写法：`FROM "别名"` 与 `FROM "t-别名*"`。而策略/敏感登记按裸表名，
    所以两种写法必须能归一到同一个键 —— 否则就会“看起来配了策略，其实永远命不中”。
    """
    text = _norm_index(target)
    if text.startswith("t-") and text.endswith("*"):
        return text[2:-1]
    return text


def es_field_of(column: Any) -> str:
    """ES 字段写法 → 逻辑字段名（列级策略匹配用，与 `es_alias_of` 同性质）。

    ES 的 text 字段带一个 `keyword` 子字段（不分词、可精确比较/聚合），所以正文里同一个逻辑
    列有两种写法：`"appCode"` 与 `"appCode.keyword"` —— 它们是**同一份数据**（实机取值一致，
    只是分析器不同）。而 sqlglot 把双引号标识符解成单个名字（点号不当表限定符），于是
    `"appCode.keyword"` 的列名就是 `appCode.keyword`，与按 `appCode` 登记的策略对不上。
    治理侧必须归一到逻辑字段名，否则“配了策略但不生效”（no-silent-degradation）。
    仅剥尾部 `.keyword`（大小写不敏感），不动其他带点的名字。
    """
    text = str(column or "").strip().lower()
    return text[: -len(".keyword")] if text.endswith(".keyword") else text


def es_field_variants(column: Any) -> list:
    """逻辑字段名 ↔ ES 正文写法清单（供按字面名比对的改写层展开策略键）。

    `apply_column_restriction` 是按 `exp.Column.name` 字面比对的，不接收归一函数；
    所以 ES 分支把一条策略同时展开成 `appCode` 与 `appCode.keyword` 两种写法，
    两条路径（显式投影 / `*` 展开后的列清单）都能命中。
    """
    base = es_field_of(column)
    if not base:
        return []
    return [base, f"{base}.keyword"]


def ctx_for(datasource_id: Any) -> EsCtx:
    """按数据源 id 取 ES 连接与开放范围；不是 ES 源/缺行一律报错（fail-loud）。"""
    try:
        ds_id = int(datasource_id or 0)
    except (TypeError, ValueError):
        ds_id = 0
    if ds_id <= 0:
        # 不退回部署级配置：内置通道已退役，“没给源”就是调用缺陷，说清比猜一个集群好
        raise EsError("Elasticsearch 取数必须指明 ES 型数据源（内置通道已退役）："
                      "请在「数据源」页登记 ES 源，并在数据集上选择它")

    from backend.common.crypto import decrypt_password, is_encrypted
    from backend.common.db import get_datasource_by_id

    row = get_datasource_by_id(ds_id) or {}
    if not row:
        raise EsError(f"数据源 #{ds_id} 不存在，无法执行 Elasticsearch 查询（请先在「数据源」页登记该 ES 源）")
    db_type = str(row.get("db_type") or "").strip().lower()
    from backend.semantics.dialect import is_es_db_type

    if not is_es_db_type(db_type):
        raise EsError(f"数据源「{row.get('name') or ds_id}」类型为 {db_type or '未配置'}，不是 Elasticsearch；请用 ES 型数据源执行该查询")

    extra = row.get("extra_config")
    if isinstance(extra, (bytes, bytearray)):
        extra = extra.decode("utf-8", errors="replace")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except ValueError as exc:
            # 脏 JSON 是配置缺陷，不能当“没配”静默走默认预算（调了没反应最难查）
            raise EsError("ES 数据源的高级配置不是合法 JSON 对象，请在「数据源」页重新保存") from exc
    extra = extra if isinstance(extra, dict) else {}

    password = row.get("password") or ""
    if password and is_encrypted(password):
        try:
            password = decrypt_password(password)
        except ValueError as exc:
            raise EsError("Elasticsearch 数据源凭据无法解密，请在数据源页重新保存口令") from exc

    host = str(row.get("host") or "").strip()
    if not host:
        raise EsError("Elasticsearch 数据源缺少连接地址，请在数据源页补全后重试")
    if host.startswith(("http://", "https://")):
        base = host.rstrip("/")
    else:
        port = int(row.get("port") or 9200)
        scheme = "https" if (str(row.get("ssl_mode") or "").lower() != "disabled" or bool(row.get("ssl"))) else "http"
        base = f"{scheme}://{host}:{port}"

    aliases = tuple(str(x).strip() for x in (extra.get("allowed_aliases") or ()) if str(x).strip())
    patterns = tuple(str(x).strip() for x in (extra.get("allowed_index_patterns") or ()) if str(x).strip())
    return EsCtx(
        datasource_id=ds_id, label=str(row.get("name") or f"ES 数据源 {ds_id}"),
        base_url=base.rstrip("/"), username=str(row.get("username") or ""),
        password=password, api_key=str(extra.get("api_key") or ""),
        verify_certs=bool(extra.get("verify_certs", True)),
        timeout_sec=int(extra.get("timeout_sec") or DEFAULT_TIMEOUT_SEC),
        max_rows=int(extra.get("max_rows") or DEFAULT_MAX_ROWS),
        allowed=aliases + patterns,
        # 连接池只认数据源自己的配置（不拿全局 env 当第二口径）：不同源并发预算不同
        pool_size=int(extra.get("pool_size") or DEFAULT_POOL_SIZE),
    )


def resolve_ctx(datasource_id: Any = None) -> EsCtx:
    return ctx_for(datasource_id)


def _base_url(ctx: Optional[EsCtx] = None) -> str:
    ctx = _require_ctx(ctx)
    if not ctx.base_url:
        raise EsError("Elasticsearch 数据源缺少连接地址，请在「数据源」页补全后重试")
    return ctx.base_url


def _headers(ctx: Optional[EsCtx] = None) -> dict[str, str]:
    return {"Content-Type": "application/json", "Accept": "application/json"}


def _auth(ctx: Optional[EsCtx] = None) -> Optional[Any]:
    """API Key 优先于用户名口令；两者都没配则匿名（部分集群走网关鉴权）。"""
    ctx = _require_ctx(ctx)
    if ctx.api_key:
        return ("apikey", ctx.api_key)
    if ctx.username:
        return ("basic", (ctx.username, ctx.password))
    return None


# 按目标复用的 HTTP 会话（连接池）。键 = 地址 + 证书校验 + 池大小：
# 凭据**不进会话也不进键**（每个请求自带 auth/headers），否则改完口令会因复用旧会话而继续拿旧凭据跑。
_sessions: dict[tuple, requests.Session] = {}
_sessions_lock = threading.Lock()


def _session_for(ctx: EsCtx) -> requests.Session:
    pool = max(1, int(ctx.pool_size or DEFAULT_POOL_SIZE))
    key = (ctx.base_url, bool(ctx.verify_certs), pool)
    with _sessions_lock:
        session = _sessions.get(key)
        if session is None:
            session = requests.Session()
            # max_retries=0：不静默重试。超时就是超预算（必须让人看到），
            # 自动重发会把“查不动”伪装成“慢但能出数”，还会抬高集群负载。
            adapter = HTTPAdapter(
                pool_connections=pool, pool_maxsize=pool, max_retries=0)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.verify = key[1]
            _sessions[key] = session
        return session


def reset_sessions() -> None:
    """丢弃全部连接池（数据源配置页保存后调用；测试隔离也用它）。

    旧 Session 直接交给 GC 回收（requests 不强制 close）；重建后下一个请求就按新地址/池大小握手。
    """
    with _sessions_lock:
        _sessions.clear()


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None, ctx: Optional[EsCtx] = None) -> Any:
    """发起 ES REST 请求；任何失败都转成不含物理信息的 EsError。"""
    ctx = _require_ctx(ctx)
    auth = _auth(ctx)
    kwargs: dict[str, Any] = {
        "method": method,
        "url": f"{_base_url(ctx)}{path}",
        "headers": _headers(ctx),
        "params": params or {},
        "json": json_body,
        "timeout": ctx.timeout_sec,
        "verify": ctx.verify_certs,
    }
    if auth and auth[0] == "apikey":
        kwargs["headers"]["Authorization"] = f"ApiKey {auth[1]}"
    elif auth:
        kwargs["auth"] = auth[1]

    try:
        # 走按目标复用的 Session（连接池）：同一数据源的图表扇出不再每次重新 TLS 握手
        resp = _session_for(ctx).request(**kwargs)
    except requests.exceptions.SSLError as exc:
        # 不悄悄关闭证书校验重试（那等于把 TLS 降级藏进兜底路径），只给可操作原因。
        logger.warning("[es] TLS 校验失败: %s", exc)
        raise EsError("Elasticsearch 证书校验失败：请核对集群证书，或在配置中显式声明自签证书") from exc
    except requests.exceptions.Timeout as exc:
        # 超时必须与“连不上”分开报：前者是**这条查询太贵**（收窄时间窗/降低分组基数/提高预算），
        # 后者是**服务或凭据有问题**。把超时说成不可达是误导向的错因（no-silent-degradation）。
        # 不降级重试、不截断结果：超预算就是没算出来，不能拿部分结果当答案。
        logger.warning("[es] 查询超时（>%ss）: %s", ctx.timeout_sec, exc)
        raise EsError(
            f"Elasticsearch 查询超时（超过 {ctx.timeout_sec}s）：该查询扫描量/分组基数过大。"
            "请收窄时间窗与过滤条件、降低分组基数，或调高该数据源的 timeout_sec") from exc
    except requests.exceptions.RequestException as exc:
        logger.warning("[es] 请求异常: %s", exc)
        raise EsError("Elasticsearch 服务不可达，请检查服务配置或稍后重试") from exc

    if resp.status_code == 401:
        raise EsError("Elasticsearch 认证失败，请检查服务凭据配置")
    if resp.status_code == 403:
        raise EsError("Elasticsearch 拒绝访问：当前凭据无权读取所请求的索引")
    if resp.status_code >= 400:
        # ES 的 4xx 正文常含索引/表达式细节，原始体只进服务端日志（护栏 §7）。
        logger.warning("[es] HTTP %s 响应: %s", resp.status_code, resp.text[:2000])
        raise _http_error(resp)
    try:
        return resp.json()
    except ValueError as exc:
        logger.warning("[es] 响应不是合法 JSON: %s", exc)
        raise EsError("Elasticsearch 响应格式异常，请联系管理员查看服务端日志") from exc


def _es_error_type(resp: Any) -> str:
    """从 ES 4xx 响应里取机器可读的错误类型（如 index_not_found_exception）。

    只取 `type`，不取 reason/index 明细：它们不进对外文案，只挂到异常属性上供调用方
    区分“别名被改名/删除”与“集群连不上”（把配置缺口报成网络故障是经典误诊断）。
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — 响应体不是 JSON 就当无类型信息
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return ""
    causes = error.get("root_cause") or []
    if causes and isinstance(causes[0], dict):
        return str(causes[0].get("type") or error.get("type") or "")
    return str(error.get("type") or "")


def _http_error(resp: Any) -> EsError:
    """统一构造 4xx 异常：对外文案不含地址/凭据/原始体，但携带机器可读错误类型。

    文案按 ES 的错误类型给**具体方向**（类型是机器可读标签，不是响应体里的自由文本）：
    旧写法不论语法错、列不存在还是索引问题，都只说“请检查查询是否受支持”——
    这句话对排查没有方向价值，用户与模型只能猜（而 ES 实际已把错因分好了类）。
    只取 `type` 不取 `reason`：reason 是自由文本，可能含集群内部信息（护栏 §7）。
    """
    etype = _es_error_type(resp)
    hint = _ES_TYPE_HINTS.get(etype, "")
    text = (f"Elasticsearch 拒绝了该请求（HTTP {resp.status_code}）"
            + (f"：{hint}" if hint else "，请检查查询是否受支持"))
    exc = EsError(text)
    exc.error_type = etype  # type: ignore[attr-defined]
    return exc


# ── 索引范围（data view）：数据面唯一闸门 ───────────────────────────────

def allowed_patterns(ctx: Optional[EsCtx] = None) -> tuple[str, ...]:
    return tuple(_require_ctx(ctx).allowed)


def _norm_index(name: Any) -> str:
    """去 ES SQL 的标识符引号与空白，得到可比对的索引/模式名。"""
    text = str(name or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def index_allowed(name: Any, ctx: Optional[EsCtx] = None) -> bool:
    """目标是否落在开放范围内（**别名与模式取并集口径**，2026-11-13 拍板）。

    空白名单恒为 False —— 这不是"没配就不限制"的宽松回落，是 fail-closed。
    并集口径的含义：同一份数据有两种写法（`FROM "t_software_app_operation_log"` 与
    `FROM "t-t_software_app_operation_log*"`，实机验证两者覆盖的索引集合双向差集为 0、
    计数相等），因此登记哪一种、正文写哪一种，四种组合都必须能过门 ——
    阶段二逐个改写正文期间，不能因为"清单收了别名而正文还是模式"就把能查的数据误拒。
    比对先按原形 fnmatch（保留“丢掉模式里的字面字符算扩权”的旧护栏），
    再按归一别名比一次（别名 ↔ `t-别名*` 互认）。
    """
    target = _norm_index(name)
    if not target:
        return False
    patterns = allowed_patterns(ctx)
    if any(fnmatch.fnmatchcase(target, pattern) for pattern in patterns):
        return True
    alias = es_alias_of(target)
    return any(alias == es_alias_of(pattern) for pattern in patterns)


def require_index(name: Any, ctx: Optional[EsCtx] = None) -> str:
    """不在开放范围即抛错，错误文案给出**可查清单**引导改写法（不猜、不放行）。"""
    target = _norm_index(name)
    if not target:
        raise EsError("缺少索引名")
    if not index_allowed(target, ctx):
        scope = _require_ctx(ctx)
        raise EsError(
            f"索引「{target}」未在数据源「{scope.label}」的开放范围内。当前可查的索引别名/模式: "
            f"{', '.join(scope.allowed) or '（无）'}")
    return target


# ── SQL 只读校验与 FROM 引用提取 ────────────────────────────────────────
# 只读校验/引用提取用的词法判定（与护栏 §5 同口径，但按 ES 实际语法形状做）。
# 不用 sqlglot：它没有 elasticsearch 方言，ES 的 `[a.b]` 字段路径与受限语法会被
# 误判成"结构不受支持"，把合法查询拒在门外（本仓已有方言误判的前车之鉴）。
#
# 实机校准（ES 8.9.1）两点：
# 1) **单引号**才是字符串字面量；**双引号是标识符引用**（索引名常含连字符与点，
#    不包双引号会被 ES 当减法），因此不能把 `"idx-name"` 当字面量剥掉——剥掉后
#    FROM 目标变成 `''`，会让正确写法被白名单误拒。
# 2) 写动作关键字只在**语句起始词 + 多语句分隔**两处拦；全文搜 REPLACE/SET 会误伤
#    合法函数（ES SQL 有 REPLACE()），而起始词必须是 SELECT 已拦住语句级写操作。
_FORBIDDEN_STATEMENT_HEADS = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|REPLACE|UPSERT|SET|MERGE)\b",
    re.IGNORECASE)
# 出现在 SELECT 正文中间也只可能是写操作的高危词（不包含可当函数名的 REPLACE/SET）
_FORBIDDEN_INLINE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE)\b", re.IGNORECASE)
# 单引号字符串字面量。**不**包含 `''` 转义分支：那个分支会让贪婪匹配跨过闭合引号，
# 把 `'a;b' LIMIT 10` 整段吞成 `''`，导致 LIMIT 检测误判为“缺 LIMIT”（已踩）。
# 剥得不完美只会留下更多文本 → 分号/关键字判定更严，不会造成误放行。
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'")
_FROM_REF = re.compile(r"\bFROM\s+([^\s,;()]+)", re.IGNORECASE)
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
# FROM 目标后紧跟逗号 = 多表（隐式交叉连接）。实机校准 ES 会拒
# （"Queries with multiple indices are not supported"），但 `_FROM_REF` 只取逗号前那段，
# 后半截不会被白名单检查到 —— 与其让 ES 回一句模型看不懂的 400，不如在这里
# 明确拒并说明原因（白名单判定不能依赖下游报错兜底）。
# 必须紧锚在目标后面：写成 `[^,;()]+,` 会把 `FROM t GROUP BY a, b` 误判成多表。
_MULTI_INDEX_FROM = re.compile(r"\bFROM\s+(\"[^\"]*\"|[^\s,;()\"]+)\s*,", re.IGNORECASE)
_JOIN_REF = re.compile(r"\bJOIN\b", re.IGNORECASE)


def _strip_literals(sql: str) -> str:
    """剥单引号字符串字面量（双引号标识符保留，供 FROM 引用提取）。"""
    return _STRING_LITERAL.sub("''", sql)


def from_indexes(sql: str) -> list[str]:
    """提取 SQL 引用的索引名（**不做校验**，供审计与错误上下文使用）。

    解析不出时返回空列表 —— 这不是放行路径（执行前 `parse_select` 仍会硬拦），
    只影响审计条目能否带上索引名。
    """
    body = _strip_literals(re.sub(r"--[^\n]*", "", str(sql or "")))
    return [_norm_index(r) for r in _FROM_REF.findall(body) if r]


def parse_select(sql: str, ctx: Optional[EsCtx] = None) -> tuple[str, Optional[int]]:
    """校验并返回 (规范化 SQL, LIMIT 值)；不合规则抛 EsError。

    判定失败一律拒绝执行，不做"解不动就当合法"的宽松回落。
    """
    text = re.sub(r"--[^\n]*", "", str(sql or "")).strip()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).strip()
    if not text:
        raise EsError("查询不能为空")

    body = _strip_literals(text)
    if ";" in body.rstrip("; \t\r\n"):
        raise EsError("仅允许单条查询语句")
    if _MULTI_INDEX_FROM.search(body):
        raise EsError("Elasticsearch SQL 不支持多表查询（FROM 只能有一个索引模式）")
    if _JOIN_REF.search(body):
        raise EsError("Elasticsearch SQL 不支持 JOIN：请只对单个索引模式查询，或改走平台数据源 SQL 通道")
    text = text.rstrip(";").strip()

    if not re.match(r"^SELECT\b", text, re.IGNORECASE):
        raise EsError("仅允许 SELECT 查询")
    if _FORBIDDEN_STATEMENT_HEADS.match(text):
        raise EsError("仅允许只读查询，检测到写操作语句")
    hit = _FORBIDDEN_INLINE_KEYWORDS.search(body)
    if hit:
        raise EsError(f"仅允许只读查询，检测到不允许的关键字 {hit.group(1).upper()}")

    limit_match = _LIMIT_CLAUSE.search(body)
    if limit_match is None:
        # 缺 LIMIT 在 ES 上意味着按集群 max_size 取数，扫描规模由不得调用方含糊。
        raise EsError("查询必须包含 LIMIT 子句（请显式指定返回行数上限）")
    limit_value = int(limit_match.group(1))
    if limit_value <= 0:
        raise EsError("LIMIT 必须是正整数")

    # FROM 引用从**原文**提取（body 已把单引号字面量换成 ''，但双引号标识符保留）：
    # 这样 `FROM "idx-name"` 能拿到真实索引名去过白名单，而不是拿到带引号的串。
    refs = _FROM_REF.findall(_strip_literals(text))
    if not refs:
        raise EsError("查询必须 FROM 一个已开放的索引模式（不接受无表的常量查询）")
    for ref in refs:
        target = require_index(ref, ctx)
        _assert_quotable_identifier(ref, target)

    cap = min(limit_value, int(_require_ctx(ctx).max_rows or limit_value))
    if cap != limit_value:
        # 收紧到配置上限：重写 LIMIT 而不是照发原值（ES 侧无治理层兜底）。
        text = _LIMIT_CLAUSE.sub(f"LIMIT {cap}", text, count=1)
    return text, cap


def _assert_quotable_identifier(raw_ref: str, target: str) -> None:
    """含连字符/点的索引名必须包双引号，否则 ES 把它当减法表达式（实机 400）。

    与其把 ES 那句模型看不懂的语法错原样抛回，不如在这里给出**可操作的改写**：
    报错本身就是下一步该怎么写（不靠模型猜 ES 方言）。
    """
    quoted = str(raw_ref or "").startswith('"')
    if not quoted and ("-" in target or "." in target):
        raise EsError(
            f'索引名「{target}」含连字符或点，ES SQL 会把它当减法/限定表达式；'
            f'请写成 FROM "{target}"')


# ── 对外能力 ─────────────────────────────────────────────────────────────

def run_sql(sql: str, *, fetch_size: int = 0, ctx: Optional[EsCtx] = None) -> dict[str, Any]:
    """执行只读 SQL，返回 {columns, rows, row_count, elapsed_hint}。

    多值字段按 lenient 取（ES 默认对数组字段抛错，会让"查一条就 500"成为常态）；
    行数按配置上限截断兜底（LIMIT 已在 parse_select 收紧）。

    `fetch_size` 缺省（0）= 按正文自己声明的 LIMIT 取满一屏：ES SQL 一次只回
    `fetch_size` 行、剩下的要拿响应里的 cursor 续取，而本函数不续 cursor ——
    拿固定小批次去取一条 `LIMIT 1000` 的正文会**静默只回前 N 行**（实机实测
    governed 口 200/1000），这是少给行数的错数，不是“够用”。调用方显式给值时
    仍按调用方要的行数收。
    """
    scope = _require_ctx(ctx)
    query, limit = parse_select(sql, scope)
    payload = {
        "query": query,
        "fetch_size": max(1, min(int(fetch_size or 0) or int(limit or 200), limit or 200)),
        "field_multi_value_leniency": True,
    }
    data = _request("POST", _SQL_PATH, json_body=payload, ctx=scope)
    columns = [{"name": c.get("name"), "type": c.get("type")}
               for c in (data.get("columns") or [])]
    rows = [list(r) for r in (data.get("rows") or [])]
    if limit is not None:
        rows = rows[:limit]
    return {"columns": columns, "rows": rows, "row_count": len(rows),
            "indexes": from_indexes(query)}


# ── 分页：SQL → 检索 DSL → search_after 取一页 ────────────────────────────
# 为什么不能拿 ES SQL 分页（实机校准，ES 8.9.1）：`LIMIT 10, 5` 与
# `LIMIT 5 OFFSET 10` 均回 HTTP 400（ES SQL 语法里没有 OFFSET）；`/_sql/async`
# 与 PIT（`/_search/point_in_time`）在本集群网关上 `no handler found`，因此
# `_shard_doc` 这个官方 tiebreaker 也用不了。剩下的可用形状只有一条：
# `/_sql/translate` 拿 DSL → `/_search` + `search_after`（tiebreaker 用 `_doc`）。
# search_after 只能从上一页游标向后走，所以随机跳页不可得，调用方需持有游标。

_SELECT_LIST_RE = re.compile(r"^\s*SELECT\s+(?P<items>.*?)\s+FROM\s+",
                            re.IGNORECASE | re.DOTALL)
# 纯字段引用：`"a.b"` / `[a.b]` / 裸名（含点与下划线）。带括号/运算符/星号的一律不当纯字段。
_PLAIN_FIELD_RE = re.compile(r'^(?:"([^"]+)"|\[([^\]]+)\]|([A-Za-z_][\w.]*))$')


def _unwrap_token(token: str) -> str:
    """去标识符包裹（双引号/方括号）与空白，得到可比的字段路径。"""
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return text


def _split_top_level(text: str) -> list[str]:
    """按顶层逗号切分（引号/方括号/圆括号内的逗号不切）。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in str(text or ""):
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth <= 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def select_items(sql: str) -> list[tuple[str, str]]:
    """解析 ES SQL 投影清单 → [(字段路径, 输出列名)]。

    只认「纯字段引用 [AS 别名]」这一种形状，其余（函数/表达式/`SELECT *`/DISTINCT）
    全部抛 EsError。这不是保守：分页要拿 `/_search` 的 `fields` 回值，而 DSL 里只有
    ES 字段路径、没有 SQL 别名；只有投影是纯字段时，才能把字段路径与输出列名
    一一对上。拿位置盲猜 zipper 会出“列名与值错行”的错数，宁拒不错。
    """
    text = re.sub(r"--[^\n]*", "", str(sql or ""))
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    m = _SELECT_LIST_RE.match(text.strip())
    if not m:
        raise EsError("无法解析查询投影清单，不能按列映射分页结果")
    items = _split_top_level(m.group("items"))
    if not items:
        raise EsError("查询投影为空，不能分页")
    out: list[tuple[str, str]] = []
    for item in items:
        if re.match(r"^DISTINCT\b", item, re.IGNORECASE):
            raise EsError("DISTINCT 查询不能按页取数（检索命中不去重，翻页会多数）")
        tokens = _split_tokens(item)
        if len(tokens) == 3 and tokens[1].upper() == "AS":
            field_token, alias = tokens[0], tokens[2]
        elif len(tokens) == 2:
            field_token, alias = tokens[0], tokens[1]
        elif len(tokens) == 1:
            field_token, alias = tokens[0], ""
        else:
            raise EsError(f"投影项「{item[:60]}」不是纯字段引用，无法映射分页列")
        fm = _PLAIN_FIELD_RE.match(_unwrap_token(field_token) if field_token.startswith('"')
                                   or field_token.startswith("[") else field_token.strip())
        if not fm:
            raise EsError(f"投影项「{item[:60]}」不是纯字段引用，无法映射分页列")
        path = next(g for g in fm.groups() if g)
        out.append((path, _unwrap_token(alias) if alias else path))
    return out


def _split_tokens(item: str) -> list[str]:
    """投影项按空白切 token，但双引号/方括号内的空白不切（`"a b"` 是一个标识符）。"""
    tokens: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in str(item or ""):
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in '"[':
            quote = {"'": "'", '"': '"', "[": "]"}[ch]
            buf.append(ch)
            continue
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def translate_select(sql: str, ctx: Optional[EsCtx] = None) -> dict:
    """SQL → 检索 DSL（`/_sql/translate`）。

    下发前仍过 `parse_select`：开放范围/只读/必须 LIMIT 三道硬门不因分页而旁路。
    """
    scope = _require_ctx(ctx)
    query, _limit = parse_select(sql, scope)
    return _request("POST", _SQL_TRANSLATE_PATH, json_body={"query": query}, ctx=scope)


def _first_value(value: Any) -> Any:
    """ES `fields` 对多值字段回数组；与 `run_sql` 的 leniency 同口径取首值。"""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def search_page(pattern: str, dsl: dict, *, size: int,
                search_after: Optional[list] = None,
                ctx: Optional[EsCtx] = None) -> dict[str, Any]:
    """按 DSL 取一页行 + 精确总数（search_after 深分页，不受 1 万行窗口限制）。

    三处护栏是实机校准出来的，不是保守：
    - `allow_partial_search_results=false` 只能放 **URI 参数**（放 body 里 ES 报
      Unknown key）。模式检索跳数百分片，超窗时默认返 200 + 空 hits + 388 个分片
      失败，空表会伪装成“这页没数据”——必须让它显式失败。
    - 分片失败再判一次并给可读原因（ES 原始体只进服务端日志，护栏 §7）。
    - 总数按 `track_total_hits=true` 取精确值（不取则默认只算到 1 万，
      会把“共 446 万行”报成“共 1 万行”）。
    """
    target = require_index(pattern, ctx)
    body: dict[str, Any] = {
        "query": dsl.get("query"),
        "fields": dsl.get("fields"),
        "sort": dsl.get("sort"),
        "_source": dsl.get("_source"),
        "size": max(1, int(size or 1)),
        "track_total_hits": True,
    }
    if search_after:
        body["search_after"] = list(search_after)
    body = {k: v for k, v in body.items() if v is not None}

    try:
        data = _request("POST", f"/{target}/_search",
                        params={"allow_partial_search_results": "false"},
                        json_body=body, ctx=ctx)
    except EsError as exc:
        msg = str(exc)
        if "Result window is too large" in msg or "max_result_window" in msg:
            raise EsError(
                f"翻页超出索引结果窗口（单索引上限 {_MAX_RESULT_WINDOW} 行）；"
                f"请缩小筛选范围或提高每页行数以减少页数") from exc
        raise

    shards = data.get("_shards") or {}
    failures = shards.get("failures") or []
    if failures:
        # 200 + 部分分片失败 = 行数与总数都不可信，不能当“没数据”回给前端
        logger.warning("[es] 分页检索存在分片失败: %s", str(failures)[:1500])
        raise EsError(
            f"Elasticsearch 未能完整执行该页检索（{shards.get('failed')}/{shards.get('total')} "
            f"个分片失败），结果不可用，请重试或缩小查询范围")

    hits = ((data.get("hits") or {}).get("hits")) or []
    paths = [f.get("field") for f in (dsl.get("fields") or []) if f.get("field")]
    rows: list[dict[str, Any]] = []
    for h in hits:
        fld = h.get("fields") or {}
        rows.append({p: _first_value(fld.get(p)) for p in paths})
    total_raw = (data.get("hits") or {}).get("total")
    total = None
    if isinstance(total_raw, dict):
        try:
            total = int(total_raw.get("value"))
        except (TypeError, ValueError):
            total = None
    elif total_raw is not None:
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            total = None
    return {
        "rows": rows,
        "total": total,
        "row_count": len(rows),
        "last_sort": (hits[-1].get("sort") if hits else None),
        "has_more": bool(hits) and len(hits) >= body["size"],
    }


def list_patterns(ctx: Optional[EsCtx] = None) -> list[str]:
    """可查索引模式清单（即白名单本身，Kibana data view 同构）。

    不向集群展开：实机校准一个模式对应 331~403 个月分片（名称以时间为后缀），全部展开
    会慢到不可用并把上千个名字塞进模型上下文。查询直接拿模式写
    `FROM "t-t_equipment_warning_log*"`，实测跨 331 分片 COUNT 直接得到 2.7 亿行。
    本函数不发任何请求：展开是集群侧的事，这里不预取清单。
    """
    return list(allowed_patterns(ctx))


def describe_index_fields(pattern: Any, ctx: Optional[EsCtx] = None) -> dict[str, Any]:
    """索引模式（data view）的字段清单：对 pattern 直接做 `_field_caps`，不展开成具体索引。

    实机校准：对模式请求返回该模式下所有分片的**字段并集**（样本 30 个），一次请求
    就够，不需要逐月索引探查；另一个关键行为是 `_field_caps` 对**匹配不到任何索引的
    通配模式返回 HTTP 200 + 空 fields**（不报 404）—— 所以"没匹配到"必须在本层显式报错，
    否则模型会当成"这个范围没有字段"而开始猜字段名（no-silent-degradation）。

    输出收敛两件事（不收敛会让模型写错 SQL）：
    - 点号路径（nested/object 子字段）单独列在 `sub_fields`，ES SQL 只能引用子字段本身；
    - text 字段标出 `exact_match_field`（其 `.keyword` 子字段）：ES 上 text 不能精确
      比较/聚合，分组与等值过滤必须走 `.keyword`，不提示就会"语法对但查不出数"。
    """
    target = require_index(pattern, ctx)
    data = _request("GET", f"/{target}{_FIELD_CAPS_PATH}", params={"fields": "*", "ignore_unavailable": "true"}, ctx=ctx)
    raw = data.get("fields") or {}
    top: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []
    for name in sorted(raw):
        if name.startswith("_"):
            continue  # 元字段（_index/_seq_no 等）不作为业务字段罗列
        caps = raw[name] or {}
        es_type = next(iter(caps), "unknown")
        meta = caps.get(es_type) or {}
        item = {"field": name, "type": es_type,
                "aggregatable": bool(meta.get("aggregatable")),
                "searchable": bool(meta.get("searchable"))}
        (subs if "." in name else top).append(item)

    if not top and not subs:
        raise EsError(
            f'索引模式「{target}」当前未匹配到任何字段：可能是该时间范围内还没有索引，'
            f'或写法与已开放模式不一致（请原样复制该 ES 数据源「开放范围」里登记的别名/模式，时间片段只接在末尾）')

    exact_of = {s["field"][: -len(".keyword")]: s["field"]
                for s in subs if s["field"].endswith(".keyword")}
    for item in top:
        if item["field"] in exact_of:
            item["exact_match_field"] = exact_of[item["field"]]
    return {"pattern": target, "fields": top, "sub_fields": subs}


def health(ctx: Optional[EsCtx] = None) -> dict[str, Any]:
    """连通性探测（供数据源页“测试连接”用）：只回业务可读状态，不回集群标识。

    **不**在这里展开索引清单：按本模块的 data view 口径，清单就是已登记的模式本身，
    展开需要逐模式发请求（N × 超时）会把“测试连接”拖成分钟级；本函数只报模式数量。
    """
    scope = _require_ctx(ctx)
    data = _request("GET", "", ctx=scope)
    return {
        "reachable": True,
        "version": str((data.get("version") or {}).get("number") or ""),
        "registered_scopes": len(allowed_patterns(scope)),
    }


# “集群里没这个目标”的错误类型（ES 对不存在的索引/别名统一报 index_not_found_exception，
# 带 ignore_unavailable 时可能返 resource_not_found）——只按类型判，不拿文案猜。
_MISSING_TARGET_TYPES = ("index_not_found_exception", "resource_not_found_exception",
                         "index_missing_exception", "no such index")


def probe_target(target: Any, ctx: Optional[EsCtx] = None) -> dict[str, Any]:
    """单个索引别名/模式的可用性探测（数据源“测试连接”用）。

    口径（2026-11-13 拍板）：**能查到数据就行** —— 不统计背了多少索引、
    不做“最新月是否已挂上别名”的覆盖巡检（那需要展开分片清单，与 data view 口径相左）。

    返回 {target, status: ok|empty|missing|rejected, reason}；连接不通/超时不在这里
    消化（直接上抛 EsError）：“配错了”与“连不上”必须分开报，否则管理员看到的是
    “服务不可达”而真相是别名被改了名。
    """
    name = require_index(target, ctx)
    try:
        rows = run_sql(f'SELECT 1 AS _probe FROM "{name}" LIMIT 1', ctx=ctx).get("rows") or []
    except EsError as exc:
        etype = str(getattr(exc, "error_type", "") or "")
        if etype in _MISSING_TARGET_TYPES:
            return {"target": name, "status": "missing",
                    "reason": "集群侧不存在该索引别名（可能被改名或删除）"}
        return {"target": name, "status": "rejected", "reason": str(exc)}
    return {"target": name, "status": "ok" if rows else "empty", "reason": ""}
