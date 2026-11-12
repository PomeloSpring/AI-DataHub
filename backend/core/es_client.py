"""Elasticsearch 内置能力的唯一取数口 —— REST `/_sql` + 索引白名单硬门。

定位（与 backend/common/config.py 的 ES 段同源决策，2026-11-04）：
ES **不是** adh_datasources 数据源，不进列级/行级治理域，因此本模块不复用
`semantics.execution` 的连接器体系（那套的前提是 SQL 可被 sqlglot 按目标方言
解析回写、RLS 可包裹子查询注入；ES SQL 两者都不成立：无 JOIN、子查询仅支持
可扁平化的形状、sqlglot 无 elasticsearch 方言）。数据面的唯一闸门是白名单：
**未登记的索引一律拒绝**，空白名单 = 不开放任何索引（fail-closed，与"空角色
授权不等于全量"同口径）。

安全约定（护栏 §5 / §7）：
- 只读：仅允许单条 SELECT，拒 DDL/DML/多语句；缺 LIMIT 直接拒绝（不静默补，
  因为补在谁身上决定扫描规模，让调用方显式写）。
- 错误文案**不含** host / 端口 / 账号 / 凭据 / 原始响应体（EsError 只回业务原因）。
- 凭据只在 `common.config` 从 .env 读取，本模块不入库、不回显。
"""

from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any, Optional

import requests

from backend.common import config

logger = logging.getLogger(__name__)

_SQL_PATH = "/_sql"
_SQL_TRANSLATE_PATH = "/_sql/translate"
_FIELD_CAPS_PATH = "/_field_caps"
# ES `index.max_result_window` 默认值：from/size 只能翻到第 1 万行，超出后 ES 要么报错
# 要么（多索引模式下）返 200 + 空 hits + 分片失败。深分页一律走 search_after，不用 from。
_MAX_RESULT_WINDOW = 10000


class EsError(ValueError):
    """对外可展示的 ES 错误：文案只允许含索引名、字段名与业务原因。

    严禁携带连接地址、账号、凭据或 ES 原始响应（调用方直接把它回给 LLM/前端）。
    """


def _base_url() -> str:
    url = (config.ES_URL or "").strip().rstrip("/")
    if not url:
        raise EsError("Elasticsearch 服务未配置（缺少连接地址），请联系管理员在环境变量中配置")
    return url


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json", "Accept": "application/json"}


def _auth() -> Optional[Any]:
    """API Key 优先于用户名口令；两者都没配则匿名（部分集群走网关鉴权）。"""
    if config.ES_API_KEY:
        return ("apikey", config.ES_API_KEY)
    if config.ES_USERNAME:
        return ("basic", (config.ES_USERNAME, config.ES_PASSWORD))
    return None


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None) -> Any:
    """发起 ES REST 请求；任何失败都转成不含物理信息的 EsError。"""
    auth = _auth()
    kwargs: dict[str, Any] = {
        "method": method,
        "url": f"{_base_url()}{path}",
        "headers": _headers(),
        "params": params or {},
        "json": json_body,
        "timeout": config.ES_TIMEOUT_SEC,
        "verify": config.ES_VERIFY_CERTS,
    }
    if auth and auth[0] == "apikey":
        kwargs["headers"]["Authorization"] = f"ApiKey {auth[1]}"
    elif auth:
        kwargs["auth"] = auth[1]

    try:
        resp = requests.request(**kwargs)
    except requests.exceptions.SSLError as exc:
        # 不悄悄关闭证书校验重试（那等于把 TLS 降级藏进兜底路径），只给可操作原因。
        logger.warning("[es] TLS 校验失败: %s", exc)
        raise EsError("Elasticsearch 证书校验失败：请核对集群证书，或在配置中显式声明自签证书") from exc
    except requests.exceptions.RequestException as exc:
        logger.warning("[es] 请求异常: %s", exc)
        raise EsError("Elasticsearch 服务不可达，请稍后重试或联系管理员检查服务状态") from exc

    if resp.status_code == 401:
        raise EsError("Elasticsearch 认证失败，请检查服务凭据配置")
    if resp.status_code == 403:
        raise EsError("Elasticsearch 拒绝访问：当前凭据无权读取所请求的索引")
    if resp.status_code >= 400:
        # ES 的 4xx 正文常含索引/表达式细节，原始体只进服务端日志（护栏 §7）。
        logger.warning("[es] HTTP %s 响应: %s", resp.status_code, resp.text[:2000])
        raise EsError(f"Elasticsearch 拒绝了该请求（HTTP {resp.status_code}），请检查查询是否受支持")
    try:
        return resp.json()
    except ValueError as exc:
        logger.warning("[es] 响应不是合法 JSON: %s", exc)
        raise EsError("Elasticsearch 响应格式异常，请联系管理员查看服务端日志") from exc


# ── 索引范围（data view）：数据面唯一闸门 ───────────────────────────────

def allowed_patterns() -> tuple[str, ...]:
    return tuple(config.ES_ALLOWED_INDEXES or ())


def _norm_index(name: Any) -> str:
    """去 ES SQL 的标识符引号与空白，得到可比对的索引/模式名。"""
    text = str(name or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def index_allowed(name: Any) -> bool:
    """目标是否落在白名单内（模式按 fnmatch 通配匹配）。

    空白名单恒为 False —— 这不是"没配就不限制"的宽松回落，是 fail-closed。
    目标本身带 `*`（如 `logs-*`）同样能匹配：实机校准 ES SQL 支持直接拿索引模式作
    FROM 目标（同 Kibana data view，ES 自己跨时间分片合并），所以不需要也不应该先展开
    成具体索引再逐个查。收窄到某个时间段是在模式后接时间片段（`logs-*2026.11`，模式原样
    保留）；丢掉模式里的连字符（`logs*2026.11`）会被当成扩权拒掉 —— fnmatch 把目标当字串
    比对，正好拦住通配展开后的越界。
    """
    target = _norm_index(name)
    if not target:
        return False
    return any(fnmatch.fnmatchcase(target, pattern) for pattern in allowed_patterns())


def require_index(name: Any) -> str:
    """不在白名单即抛错，错误文案给出**可查模式清单**引导改写法（不猜、不放行）。"""
    target = _norm_index(name)
    if not target:
        raise EsError("缺少索引名")
    if not index_allowed(target):
        raise EsError(
            f"索引「{target}」未开放查询。当前可查的索引模式: "
            f"{', '.join(allowed_patterns()) or '（无）'}")
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


def parse_select(sql: str) -> tuple[str, Optional[int]]:
    """校验并返回 (规范化 SQL, LIMIT 值)；不合规则抛 EsError。

    判定失败一律拒绝，不做"解不动就当合法"的宽松回落。
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
        target = require_index(ref)
        _assert_quotable_identifier(ref, target)

    cap = min(limit_value, int(config.ES_MAX_ROWS or limit_value))
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

def run_sql(sql: str, *, fetch_size: int = 200) -> dict[str, Any]:
    """执行只读 SQL，返回 {columns, rows, row_count, elapsed_hint}。

    多值字段按 lenient 取（ES 默认对数组字段抛错，会让"查一条就 500"成为常态）；
    行数按配置上限截断兜底（LIMIT 已在 parse_select 收紧）。
    """
    query, limit = parse_select(sql)
    payload = {
        "query": query,
        "fetch_size": max(1, min(int(fetch_size or 200), limit or 200)),
        "field_multi_value_leniency": True,
    }
    data = _request("POST", _SQL_PATH, json_body=payload)
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


def translate_select(sql: str) -> dict:
    """SQL → 检索 DSL（`/_sql/translate`）。

    下发前仍过 `parse_select`：白名单/只读/必须 LIMIT 三道硬门不因分页而旁路。
    """
    query, _limit = parse_select(sql)
    return _request("POST", _SQL_TRANSLATE_PATH, json_body={"query": query})


def _first_value(value: Any) -> Any:
    """ES `fields` 对多值字段回数组；与 `run_sql` 的 leniency 同口径取首值。"""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def search_page(pattern: str, dsl: dict, *, size: int,
                search_after: Optional[list] = None) -> dict[str, Any]:
    """按 DSL 取一页行 + 精确总数（search_after 深分页，不受 1 万行窗口限制）。

    三处护栏是实机校准出来的，不是保守：
    - `allow_partial_search_results=false` 只能放 **URI 参数**（放 body 里 ES 报
      Unknown key）。模式检索跳数百分片，超窗时默认返 200 + 空 hits + 388 个分片
      失败，空表会伪装成“这页没数据”——必须让它显式失败。
    - 分片失败再判一次并给可读原因（ES 原始体只进服务端日志，护栏 §7）。
    - 总数按 `track_total_hits=true` 取精确值（不取则默认只算到 1 万，
      会把“共 446 万行”报成“共 1 万行”）。
    """
    target = require_index(pattern)
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
                        json_body=body)
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


def list_patterns() -> list[str]:
    """可查索引模式清单（即白名单本身，Kibana data view 同构）。

    不向集群展开：实机校准一个模式对应 331~403 个月分片（名称以时间为后缀），全部展开
    会慢到不可用并把上千个名字塞进模型上下文。查询直接拿模式写
    `FROM "t-t_equipment_warning_log*"`，实测跨 331 分片 COUNT 直接得到 2.7 亿行。
    本函数不发任何请求：展开是集群侧的事，这里不预取清单。
    """
    return list(allowed_patterns())


def describe_index_fields(pattern: Any) -> dict[str, Any]:
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
    target = require_index(pattern)
    data = _request("GET", f"/{target}{_FIELD_CAPS_PATH}", params={"fields": "*", "ignore_unavailable": "true"})
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
            f'或写法与已开放模式不一致（请原样复制 list_es_indexes 返回的模式，时间片段只接在末尾）')

    exact_of = {s["field"][: -len(".keyword")]: s["field"]
                for s in subs if s["field"].endswith(".keyword")}
    for item in top:
        if item["field"] in exact_of:
            item["exact_match_field"] = exact_of[item["field"]]
    return {"pattern": target, "fields": top, "sub_fields": subs}


def health() -> dict[str, Any]:
    """连通性探测（供内置服务“测试连接”用）：只回业务可读状态，不回集群标识。

    **不**在这里展开索引清单：按本模块的 data view 口径，清单就是已登记的模式本身，
    展开需要逐模式发请求（N × 超时）会把“测试连接”拖成分钟级；本函数只报模式数量。
    """
    data = _request("GET", "")
    return {
        "reachable": True,
        "version": str((data.get("version") or {}).get("number") or ""),
        "registered_scopes": len(allowed_patterns()),
    }
