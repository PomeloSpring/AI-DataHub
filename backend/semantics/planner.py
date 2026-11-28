"""Planner — 把 SemanticQuery + ResolvedBinding 编译成 PlannedExecution。

职责(确定性、无 LLM):
  ① 选路:按 binding.guardrail.query_mode -> materialized / federated / raw_source
  ② 编译基线 SQL:SELECT cols/measures FROM table WHERE ... GROUP BY ... ORDER ... LIMIT
  ③ 护栏:huge 且 allow_full_scan=False 时, 无过滤/无 LIMIT -> 直接拒绝;
          force_limit 生效时, 若未指定 LIMIT -> 补默认值
  ④ provenance / warnings 输出, 供 Playground 与审计面板展示

跨对象与窗口(Phase 2「Link 参与编译」):
  - 限定业务名(对象名.业务名)引用跨对象字段, join 路径从 canonical links[]确定性
    解出(≤3 跳, 无路径回抛候选不猜 join), 产出 LEFT JOIN 链;
  - 结构化窗口模式(window 字段)编译为"内层聚合 + 外层窗口"两层结构;
    窗口表达式仅进 SELECT 投影层; 自由形状 SQL 不在本层(走模板对象/nl2sql)。

方言两级口径见 `backend/semantics/dialect.py`: 语法骨架按**方言族**(mysql/postgres/es)编译,
函数实现按**引擎**(mysql/doris/postgres/sls/es)取变体 —— 指标 `formula_dialects` 与模板
`sql_bodies` 共用同一套 engine → family → 通用回落规则。

ES(Elasticsearch SQL = Calcite 子集)是**骨架级**差异, 不是同族换函数(实机取证见
`semantics/es_dialect.py` 与本文件 `_ES_*` 常量)：双引号标识符、下标从 1 起的 3 参
`SUBSTRING`、无 `DATE_FORMAT/to_char/DATE_SUB/INTERVAL`、无 JOIN/窗口/派生表/OFFSET。
因此 ES 的时间桶走「定长字符串切片」、时间边界由**服务端算好后下推**(不依赖引擎时钟),
不可表达的形态(window/JOIN/季度粒度/裸 datetime 比较)一律编译期 fail-loud, 产物再经
`es_rejection` 复核 —— 不用"换成别的形状凑合"掩盖(no-silent-degradation)。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

from backend.common.db.metadata_db import get_metadata_conn
from backend.semantics import dialect as sem_dialect
from backend.semantics.models import (
    PlannedExecution, ResolvedBinding, SemanticFilter, SemanticQuery, SemanticWindow,
)
from backend.semantics.sql_params import (
    declared_names, literal as _lit, parse_json_obj, parse_variables, placeholder_names,
)

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000
_HUGE_BLOCK_MESSAGE = (
    "huge table without filter/limit is blocked by guardrail "
    "(set allow_full_scan=1 or add filters or specify LIMIT)"
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGG_FNS = {"SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"}
_TIME_GRAIN_FMT_MYSQL = {
    "second": "%Y-%m-%d %H:%i:%s",
    "minute": "%Y-%m-%d %H:%i",
    "hour": "%Y-%m-%d %H",
    "day": "%Y-%m-%d",
    "week": "%x-%v",
    "month": "%Y-%m",
    "quarter": "%Y-Q%q",
    "year": "%Y",
}
# PG to_char 模板(week 用 ISO 年-周, quarter 把 Q 当字面量引号输出)
_TIME_GRAIN_FMT_PG = {
    "second": "YYYY-MM-DD HH24:MI:SS",
    "minute": "YYYY-MM-DD HH24:MI",
    "hour": "YYYY-MM-DD HH24",
    "day": "YYYY-MM-DD",
    "week": "IYYY-IW",
    "month": "YYYY-MM",
    "quarter": 'YYYY-"Q"Q',
    "year": "YYYY",
}
# db_type -> 编译方言族: mysql/doris 同一族(MySQL 协议/语法), postgres 家族含 sls(PG 协议)
# 单一事实源在 semantics/dialect.py(引擎级变体键同源), 此处仅保留历史导入口径。
_DIALECT_OF_DBTYPE = sem_dialect.FAMILY_OF_DBTYPE
# time_window 单位 -> (MySQL INTERVAL 单位, PG INTERVAL 单位)
_WINDOW_UNIT = {
    "s": ("SECOND", "seconds"),
    "m": ("MINUTE", "minutes"),
    "h": ("HOUR", "hours"),
    "d": ("DAY", "days"),
    "w": ("WEEK", "weeks"),
    "M": ("MONTH", "months"),
    "y": ("YEAR", "years"),
}
# 日历时间区间 -> (起, 止) 左闭右开谓词表达式; MySQL/Doris 族(服务端 CURDATE 单一时钟)
_MY_RANGE = {
    "today": ("CURDATE()", "CURDATE() + INTERVAL 1 DAY"),
    "yesterday": ("CURDATE() - INTERVAL 1 DAY", "CURDATE()"),
    "this_week": (
        "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)",
        "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) + INTERVAL 7 DAY"),
    "last_week": (
        "DATE_SUB(CURDATE(), INTERVAL (WEEKDAY(CURDATE()) + 7) DAY)",
        "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)"),
    "this_month": ("DATE_FORMAT(CURDATE(), '%Y-%m-01')",
                   "DATE_FORMAT(CURDATE(), '%Y-%m-01') + INTERVAL 1 MONTH"),
    "last_month": (
        "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH)",
        "DATE_FORMAT(CURDATE(), '%Y-%m-01')"),
    "this_quarter": (
        "MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 1) * 3 MONTH",
        "MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL QUARTER(CURDATE()) * 3 MONTH"),
    "last_quarter": (
        "MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 2) * 3 MONTH",
        "MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 1) * 3 MONTH"),
    "this_year": ("MAKEDATE(YEAR(CURDATE()), 1)", "MAKEDATE(YEAR(CURDATE()) + 1, 1)"),
    "last_year": ("MAKEDATE(YEAR(CURDATE()) - 1, 1)", "MAKEDATE(YEAR(CURDATE()), 1)"),
}
# PostgreSQL 族(含 sls): date_trunc ISO 周边界
_PG_RANGE = {
    "today": ("CURRENT_DATE", "CURRENT_DATE + 1"),
    "yesterday": ("CURRENT_DATE - 1", "CURRENT_DATE"),
    "this_week": ("DATE_TRUNC('week', CURRENT_DATE)",
                  "DATE_TRUNC('week', CURRENT_DATE) + INTERVAL '7 days'"),
    "last_week": ("DATE_TRUNC('week', CURRENT_DATE) - INTERVAL '7 days'",
                  "DATE_TRUNC('week', CURRENT_DATE)"),
    "this_month": ("DATE_TRUNC('month', CURRENT_DATE)",
                   "DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month'"),
    "last_month": ("DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'",
                   "DATE_TRUNC('month', CURRENT_DATE)"),
    "this_quarter": ("DATE_TRUNC('quarter', CURRENT_DATE)",
                     "DATE_TRUNC('quarter', CURRENT_DATE) + INTERVAL '3 months'"),
    "last_quarter": ("DATE_TRUNC('quarter', CURRENT_DATE) - INTERVAL '3 months'",
                     "DATE_TRUNC('quarter', CURRENT_DATE)"),
    "this_year": ("DATE_TRUNC('year', CURRENT_DATE)",
                  "DATE_TRUNC('year', CURRENT_DATE) + INTERVAL '1 year'"),
    "last_year": ("DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 year'",
                  "DATE_TRUNC('year', CURRENT_DATE)"),
}
# 绝对区间兜底: 仅接受 ISO 日期/日期时间, 防注入
_ABS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
_WINDOW_RE = re.compile(r"^(\d{1,4})([smhdwMy])$")

# ── SLS(Presto) 引擎级时间变体 ─────────────────────────────────
# 探针实证(logs/probe_sls_core*.py, 全部 LIMIT 0/纯聚合)，PG 族通用写法在 SLS 上不是报错就是错数：
#   ① `to_char` 未注册(O ERR)            -> 时间桶用 date_format(from_unixtime(<epoch 秒列>), fmt)(N/S4/S5/S6/U2/U3 OK)
#   ② `INTERVAL '30' DAY` Unknown type(G ERR) -> 单位必须写进字符串 `INTERVAL '30 days'`(H/S1/S2/S3 OK)
#   ③ bigint 列与 timestamp 边界混型(Q1/T7 ERR) -> 边界包 to_unixtime(...), 与 epoch 秒同型比较(S1/Q4 OK)
#   ④ `CURRENT_DATE - 1` 不适用(T3 ERR)   -> date_add('day', -1, CURRENT_DATE)(U4/U5/U6/U7 OK)
#   ⑤ `%q`(季度) 无定义(T9 输出字面量 "2026-Qq"), `||` 解析不通(U1 ERR) -> CONCAT + quarter()(V1)
_SLS_GRAIN_FMT = {
    "second": "%Y-%m-%d %H:%i:%s",
    "minute": "%Y-%m-%d %H:%i",
    "hour": "%Y-%m-%d %H",
    "day": "%Y-%m-%d",
    "week": "%x-%v",     # ISO 年-周(实测 T8 -> 2026-44, 与 PG IYYY-IW 同口径)
    "month": "%Y-%m",
    "year": "%Y",
}
_SLS_GRAIN_QUARTER = "CONCAT(date_format({t}, '%Y'), '-Q', CAST(quarter({t}) AS VARCHAR))"
# 日历区间边界(日期表达式); epoch 秒时间列渲染时再包 to_unixtime()
_SLS_RANGE = {
    "today": ("CURRENT_DATE", "date_add('day', 1, CURRENT_DATE)"),
    "yesterday": ("date_add('day', -1, CURRENT_DATE)", "CURRENT_DATE"),
    "this_week": ("date_trunc('week', CURRENT_DATE)",
                  "date_add('day', 7, date_trunc('week', CURRENT_DATE))"),
    "last_week": ("date_add('day', -7, date_trunc('week', CURRENT_DATE))",
                  "date_trunc('week', CURRENT_DATE)"),
    "this_month": ("date_trunc('month', CURRENT_DATE)",
                   "date_add('month', 1, date_trunc('month', CURRENT_DATE))"),
    "last_month": ("date_add('month', -1, date_trunc('month', CURRENT_DATE))",
                   "date_trunc('month', CURRENT_DATE)"),
    "this_quarter": ("date_trunc('quarter', CURRENT_DATE)",
                     "date_add('month', 3, date_trunc('quarter', CURRENT_DATE))"),
    "last_quarter": ("date_add('month', -3, date_trunc('quarter', CURRENT_DATE))",
                     "date_trunc('quarter', CURRENT_DATE)"),
    "this_year": ("date_trunc('year', CURRENT_DATE)",
                  "date_add('year', 1, date_trunc('year', CURRENT_DATE))"),
    "last_year": ("date_add('year', -1, date_trunc('year', CURRENT_DATE))",
                  "date_trunc('year', CURRENT_DATE)"),
}
# SLS 内置事件时间列: epoch 秒(实测 max=1793245034); 业务同名 timestamp 列是 epoch 毫秒
# (max=1793245028088) —— 二者与 to_unixtime() 秒边界只有前者同型可比, 后者单位不在
# 元数据里声明, 静默按秒比较会把窗口放大到全表(错数比无答案更坏), 故一律拒。
_SLS_EPOCH_SEC_COLUMN = "__time__"
_INTEGER_TYPES = {"bigint", "int", "integer", "smallint", "tinyint"}

# SLS epoch 秒时间维未显式请求粒度时的默认粒度：按已登记的业务名判（「统计小时」→hour、
# 「统计日期」→day）。裸 epoch 既不可读，按它 GROUP BY 等于把「时间」当整数维度(逐秒出行)；
# 业务名里的粒度词是建模时对用户的承诺，以它为准不是猜单位。名字里没有粒度词时
# 兜到秒级完整时间串（等价于其余引擎「不限粒度就输出时间点本身」）。
_GRAIN_BY_NAME: tuple[tuple[str, str], ...] = (
    ("小时", "hour"), ("整点", "hour"), ("分钟", "minute"), ("秒", "second"),
    ("日期", "day"), ("天", "day"), ("周", "week"), ("月", "month"),
    ("季", "quarter"), ("年", "year"),
)


def _grain_from_dim_name(name: str) -> str:
    s = str(name or "")
    for kw, grain in _GRAIN_BY_NAME:
        if kw in s:
            return grain
    return ""


# ── ES(Elasticsearch SQL) 编译常量 ─────────────────────────────
# 引擎无 DATE_FORMAT/to_char, 时间桶一律用「定长字符串切片」(实机 2026-11-18)：
#   "operationTime.keyword" = '2026-11-18 14:12:30'
#   SUBSTRING(CAST(<col> AS VARCHAR), 1, 7) -> '2026-11' (月) / 10=日 / 13=时 / 4=年
# datetime 型列(@timestamp)先 CAST AS VARCHAR 再切片(裸 datetime 进 SUBSTRING 直接 400)。
_ES_SUBSTR_GRAIN_LEN = {
    "second": 19, "minute": 16, "hour": 13, "day": 10, "month": 7, "year": 4,
}
# ES 元数据里 datetime 型列的 data_type 取值(其余 keyword/text/long 等按字符串/数值处理)
_ES_DATE_TYPES = {"date", "datetime", "timestamp"}
# time_window 单位 -> timedelta 关键字(月/年另走日历加减)
_ES_WINDOW_DELTA = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _add_months(dt, k: int):
    """按日历加月(date/datetime 通用, 月末自动收敛到当月最后一天)。"""
    total = dt.year * 12 + (dt.month - 1) + int(k)
    year, month0 = divmod(total, 12)
    month = month0 + 1
    # 当月天数: 下月 1 号 - 1 天
    nxt = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    last_day = (nxt - timedelta(days=1)).day
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def _es_window_start(n: int, unit_key: str, now: Optional[datetime] = None) -> str:
    """相对窗口起点 -> 'YYYY-MM-DD HH:MM:SS'(服务端单一时钟)。

    ES SQL 没有 `DATE_SUB(NOW(), INTERVAL ...)` 之类写法, 边界必须算好再下推; 用服务端
    时钟与其余引擎的 `NOW()/CURRENT_DATE` 同源, 不引入第二种"现在"。
    """
    t = (now or datetime.now()).replace(microsecond=0)
    if unit_key == "M":
        t = _add_months(t, -n)
    elif unit_key == "y":
        t = _add_months(t, -12 * n)
    else:
        t = t - timedelta(**{_ES_WINDOW_DELTA[unit_key]: n})
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _es_calendar_range(key: str, now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    """日历区间 -> (起, 止) 的 'YYYY-MM-DD'(止为右开, ISO 周一起算)。

    与 `_MY_RANGE/_PG_RANGE/_SLS_RANGE` 同口径(左闭右开), 只是把边界算在服务端 ——
    ES 既没有 `CURDATE()` 系函数也没有 INTERVAL 语法, 让引擎算等于各站各表各时区。
    """
    d = (now or datetime.now()).date()
    if key == "today":
        s, e = d, d + timedelta(days=1)
    elif key == "yesterday":
        s, e = d - timedelta(days=1), d
    elif key in ("this_week", "last_week"):
        start = d - timedelta(days=d.weekday())
        s = start if key == "this_week" else start - timedelta(days=7)
        e = s + timedelta(days=7)
    elif key in ("this_month", "last_month"):
        first = d.replace(day=1)
        s = first if key == "this_month" else _add_months(first, -1)
        e = _add_months(s, 1)
    elif key in ("this_quarter", "last_quarter"):
        qstart = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
        s = qstart if key == "this_quarter" else _add_months(qstart, -3)
        e = _add_months(s, 3)
    elif key in ("this_year", "last_year"):
        s = date(d.year, 1, 1) if key == "this_year" else date(d.year - 1, 1, 1)
        e = date(s.year + 1, 1, 1)
    else:
        return None
    return s.isoformat(), e.isoformat()


def _dialect_of(db_type: str) -> str:
    """数据源 db_type -> **对象编译**方言族(ES 单列一族)。

    不用 `family_of`: 那是**数据集正文**的方言族(es 不登在那里, 正文走透传)。单表对象的
    编译骨架需要 ES 自己的写法, 故走 `compile_family_of`。未知类型保守回落 mysql(与旧行为
    一致)并出 warning。
    """
    return sem_dialect.compile_family_of(db_type)


# ── 跨对象 JOIN（Phase 2「Link 参与编译」）────────────────────────
# LLM 只用限定业务名(对象名.业务名)引用跨对象字段; join 路径由语义层从
# canonical links[] 确定性解出(≤3 跳), 无路径回抛候选, 不猜 join(宁缺勿错)。

_JOIN_MAX_HOPS = 3
_links_cache: dict[int, tuple[float, dict]] = {}
_LINKS_TTL_SEC = 60.0
# 「主对象上解析不到 → 关联对象上同名维度/指标」的推荐写法缓存
# （仅失败路径的提示，60s TTL；元数据库是远程的，重跑同一无解名称不必重查）
_XOBJ_HINT_CACHE: dict[tuple, tuple[float, list[str]]] = {}
_XOBJ_HINT_TTL_SEC = 60.0


def _identity(s: str) -> str:
    """对象身份键: 忽略大小写与分隔符(与 object_identity_key 同口径)。"""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _ref_key(s: str) -> str:
    """限定名对象引用归一: 去空白/分隔符但保留中文(_identity 会把中文剥空)。"""
    return re.sub(r"[\s_\-]+", "", str(s or "").strip().lower())


def _split_qualified(name) -> tuple[str, str]:
    """限定业务名拆分: "客户.行业" -> ("客户", "行业"); 无限定 -> ("", name)。"""
    s = str(name or "").strip()
    if "." in s:
        obj, _, rest = s.partition(".")
        return obj.strip(), rest.strip()
    return "", s


def _alias_of(name: str) -> str:
    """SELECT AS 用别名: 限定名的 '.' 换 '_'(点号会破坏标识符/ORDER BY)。"""
    return str(name or "").strip().replace(".", "_")


# 别名引用：AS/ORDER BY 右侧是**名字不是表达式**，无论含什么字符都必须包引号——
# 否则「层间转化率(%)」这类业务名会被当表达式透传，生成非法 SQL。
def _quote_alias(name: str, family: str) -> str:
    return sem_dialect.quote_alias(name, family)


def _qualify_bare(expr: str, alias: str, cols: set[str]) -> str:
    """给公式文本中未限定的裸列名加表别名(已限定/引号内不碰)。"""
    out = str(expr or "")
    for c in sorted({x for x in cols if x}, key=len, reverse=True):
        out = re.sub(rf"(?<![\w.`\"])({re.escape(c)})(?![\w`\"])", f"{alias}.\\1", out)
    return out


# 物理列名本身含点的引擎：SLS/ARMS LogStore 的字段名就是 `view.url`、`session.id`
# 这种带点形式(实测 logstore-rum 202 列里绝大多数带点)。
# 探针实证(logs/probe_sls_core.py A/B、probe_sls_core2.py Q8/Q9/Q10)：
#   SELECT view.url     -> InternalError_: Column 'view.url' cannot be resolved
#   SELECT "view.url"   -> OK
#   GROUP BY view.url   -> 同上报错；GROUP BY "view.url" -> OK
# 裸写会被 Presto 解析成「行变量.字段」访问，因此这类列必须整体加引号。
# ES 同理(探针 2026-11-18)：`appCode.keyword` 裸写被当 `appCode`.`keyword` 解析；
# 整体双引号 `"appCode.keyword"` 是 ES SQL 引用多字段子字段的唯一正确写法。
# 其余引擎不列入：列名不含点，改写路径行为完全不变(不制造第二条口径)。
_DOTTED_COLUMN_ENGINES = frozenset({"sls", "es"})

# 字符串字面量 / 已加引号片段：公式规范化时必须跳过(否则会改坏 'view' 这类码值)
_QUOTED_SEG_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


# 字面量/已引号片段之外的带点标识符链(贪婪匹配 = 长名优先, 不会只抢到 `view`)
_DOTTED_TOKEN_RE = re.compile(r"(?<![\w.\"`])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?![\w\"`])")


def _quote_dotted_columns(expr: str, family: str) -> str:
    """把表达式文本里裸写的「带点物理列」整体加引号(仅带点列引擎调用)。

    改写文本里所有带点标识符链, 不按列元数据清单过滤。两个原因：
    - 这类引擎的单表公式里带点 token 只可能是列名 —— 真正的跳表限定已在字典写入
      时被 `metric_formula.is_cross_table` 拒掉, 不会活到编译期；
    - 跳站部署时目标源可能尚未同步列元数据(实测 arms-prd-en 零登记行)，
      若因为清单为空就不加引号, `COUNT(DISTINCT session.id)` 会直接报 Column cannot
      be resolved —— 引号化不能依赖一个可能缺失的元数据开关。
    字面量与已加引号片段一律跳过(否则会改坏 `'view'` 这类码值)。
    """
    if not expr:
        return expr
    out: list[str] = []
    pos = 0
    for seg in _QUOTED_SEG_RE.finditer(expr):
        out.append(_DOTTED_TOKEN_RE.sub(
            lambda m: sem_dialect.quote_alias(m.group(1), family), expr[pos:seg.start()]))
        out.append(seg.group(0))
        pos = seg.end()
    out.append(_DOTTED_TOKEN_RE.sub(
        lambda m: sem_dialect.quote_alias(m.group(1), family), expr[pos:]))
    return "".join(out)


def _load_object_links(datasource_id: int = 0) -> dict:
    """对象关系邻接（C1：Oxigraph/SPARQL）。

    来源 = 知识图谱 reified adh:Link（canonical links[] 的图投影）+ label/altLabel
    实体对齐（客户→customer）；图不可用抛 RuntimeError（fail-loud，不回退其他
    关系源）。60s TTL 缓存，评测/单测可打桩本函数。
    """
    now = time.time()
    hit = _links_cache.get(datasource_id)
    if hit and now - hit[0] < _LINKS_TTL_SEC:
        logger.debug("[planner] 关系图缓存命中(ds=%s, 剩余 %ds)",
                     datasource_id, int(_LINKS_TTL_SEC - (now - hit[0])))
        return hit[1]
    from backend.semantics.graph_rag.link_graph import load_link_graph
    data = load_link_graph()
    _links_cache[datasource_id] = (now, data)
    logger.info("[planner] 跨对象关系图已加载(Oxigraph/SPARQL, ds=%s): 名 %d 个/边 %d 条",
                datasource_id, len(data.get("names") or {}),
                sum(len(v) for v in (data.get("edges") or {}).values()))
    return data


def _join_is_usable(join) -> bool:
    """该关系的 join 能不能编进 SQL：空 join（纯语义关系）与跨跳占位符（含 `→`/`—`）都不能。

    跨跳占位符是建模时写的“两段式说明”（`A.x = Mid.x → Mid.y = C.y`），中间表/对象未建模，
    无法确定性改写。必须在这里就当作**不可遍历边**跳过：否则 BFS 把它当最短路径选出来，
    真正可用的多跳路径（A→B→C 各自单跳 join）永远轮不到（历史缺陷 2026-11-26：
    用户→企业 被 `→` 直连边占据，2 跳的正规路径被挡，跨对象维度一律被丢）。
    """
    j = str(join or "")
    return bool(j.strip()) and "→" not in j and "—" not in j


def _link_chains(edges: dict[str, list[tuple[str, str]]], src: str, dst: str,
                 max_hops: int = _JOIN_MAX_HOPS) -> list[list[tuple[str, str]]]:
    """BFS 求出**最短**层级的全部可编译关系链（按节点序列去重）。

    只走**可编译**的边（非空 join 且无跨跳占位符）。返回多条 = 图上有多个同长度口径
    （如 用户→企业 可经“用户企业关系”也可经“案例”），选哪条会改变数据含义。
    """
    if src == dst:
        return [[]]
    frontier: list[tuple[str, list, set]] = [(src, [], {src})]
    for _ in range(max_hops):
        nxt: list[tuple[str, list, set]] = []
        found: list[list[tuple[str, str]]] = []
        for node, path, seen in frontier:
            for tgt, join in edges.get(node, []):
                if not _join_is_usable(join):
                    continue        # 语义关系/跨跳说明不构成 SQL 链(宁缺勿错)
                if tgt == dst:
                    found.append(path + [(tgt, join)])
                    continue
                if tgt not in seen:
                    nxt.append((tgt, path + [(tgt, join)], seen | {tgt}))
        if found:
            uniq: dict[tuple, list] = {}
            for p in found:
                uniq.setdefault(tuple(t for t, _ in p), p)
            return list(uniq.values())
        frontier = nxt
    return []


def _link_chain(edges: dict[str, list[tuple[str, str]]], src: str, dst: str,
                max_hops: int = _JOIN_MAX_HOPS) -> Optional[list[tuple[str, str]]]:
    """**唯一**可编译关系链；无路径或“多条同长度路径（口径歧义）”一律 None（宁缺勿错）。"""
    chains = _link_chains(edges, src, dst, max_hops)
    return chains[0] if len(chains) == 1 else None


def _reachable_objects(edges: dict[str, list[tuple[str, str]]], src: str,
                       max_hops: int = _JOIN_MAX_HOPS) -> list[str]:
    """src 出发 ≤max_hops 的**可编译**关联对象（不含自身，BFS 层序稳定）。"""
    out: list[str] = []
    seen = {src}
    frontier = [src]
    for _ in range(max_hops):
        nxt: list[str] = []
        for node in frontier:
            for tgt, join in (edges.get(node) or []):
                if tgt in seen or not _join_is_usable(join):
                    continue
                seen.add(tgt)
                out.append(tgt)
                nxt.append(tgt)
        frontier = nxt
    return out


def _cross_object_suggestions(binding, name: str, kind: str) -> list[str]:
    """主对象解析不到该名称时，列出**关联对象**上的同名维度/指标（`对象key.名称`）。

    为什么必须在失败路径给可执行写法：跨对象维度只能写成「对象key.名称」，而 LLM 拿到
    的候选清单只包含主对象的维度（如 isv 上看不到「用户类型」）——只回“无法解析”会把模型
    逼向裸 SQL 或直接说“查不了”（2026-11-26 联耀实测）。这里只**推荐写法**，
    不替调用方自动跨对象绑定（口径仍由用户确认，宁缺勿错）。

    只在失败路径调用；60s TTL 缓存同一 (源, 主对象, 名称) 的结论——模型按题目重试同一无解
    名称很常见，而元数据库是远程的（实测单次 ~300ms/往返）。
    """
    ds = int(getattr(binding, "datasource_id", 0) or 0)
    src = str(getattr(binding, "object_key", "") or "").strip()
    ck = (ds, src, str(name), kind)
    now = time.time()
    hit = _XOBJ_HINT_CACHE.get(ck)
    if hit and now - hit[0] < _XOBJ_HINT_TTL_SEC:
        return hit[1]
    try:
        links = _load_object_links(ds)
    except Exception as e:  # noqa: BLE001 — 提示是旁路，图不可用不影响编译主链路
        logger.warning("[planner] 跨对象候选查询失败(仅提示): %s", e)
        return []
    hits: list[str] = []
    for obj_key in _reachable_objects((links or {}).get("edges") or {}, src):
        fb = _resolve_foreign_binding(obj_key, ds)
        if fb is None or not fb.physical_table:
            continue
        r = _ColumnResolver(fb)
        found = (r.lookup_dimension(name) is not None if kind == "dimension"
                 else r._lookup_metric(name) is not None)
        if found:
            hits.append(f"{obj_key}.{name}")
    if len(_XOBJ_HINT_CACHE) > 512:      # 防无界增长：仅提示缓存，清了不丢正确性
        _XOBJ_HINT_CACHE.clear()
    _XOBJ_HINT_CACHE[ck] = (now, hits)
    return hits


def _resolve_foreign_binding(obj_key: str, datasource_id: int):
    """跨对象 binding 解析(与主对象同一裁决源 binding_resolver)。"""
    from backend.semantics.binding_resolver import resolve_binding
    binding, _warns = resolve_binding(obj_key, datasource_id=datasource_id)
    return binding


# ── public API ───────────────────────────────────────────────────

def _apply_column_policy(expr: str, policies: dict[str, str], *, dialect: str,
                         engine: str = "") -> str:
    """把列级脱敏策略编译进**单个投影表达式**（护栏 §7：能不能下发由数据权限裁决）。

    SELECT 与 GROUP BY 共用同一份脱敏后表达式，天然做到“按脱敏值分组”——而不是
    “按原值分组后只把展示值打星”（后者会把 distinct 个数泄露出去，且分组数会爆）。
    只做列名词边界匹配（与 `gates._blocked_column_guard` 的 SQL 文本兜底同口径）：
    命中即整表达式包一层 mask，多列命中按列名排序逐层包（确定性）。
    聚合指标不走这条路（`resolve_measure` 分支）：`COUNT(DISTINCT ip)` 输出的是计数
    不是原值，包 mask 会静默改数；真不能查的列由 block 闸门硬拒。
    同名列跨表宁多脱敏不漏脱敏。无策略时原样返回（不引入任何开销）。
    """
    if not expr or not policies:
        return expr
    from backend.semantics.security.masking import mask_expr

    out = str(expr)
    for col in sorted(policies):
        if not col:
            continue
        if not re.search(rf"(?<!\w){re.escape(col)}(?!\w)", out, re.IGNORECASE):
            continue
        out = mask_expr(out, policies[col], dialect, engine)
    return out


def plan(query: SemanticQuery, binding: ResolvedBinding,
         column_policies: Optional[dict[str, str]] = None) -> PlannedExecution:
    """编译 SemanticQuery -> PlannedExecution。

    失败(违反护栏)时, warnings 非空且 sql 为空;调用方据此拒绝执行。

    `column_policies`: {物理列名(小写): mask 类型} —— 列级安全策略
    （`adh_sensitive_fields` 敏感基线 ∪ RLS 列策略）。**缺省(None)时按会话身份自行解析**，
    使所有 plan() 入口（Chat/看板/报表/大屏/Playground）生成的 SQL 都带上策略；
    显式传 `{}` = 确认无策略（离线编译/用例）。策略加载失败按护栏 fail-loud：
    返回空计划 + 原因，绝不默默编译未脱敏 SQL。
    """
    warnings: list[str] = []
    g = binding.guardrail
    dialect = _dialect_of(binding.db_type)
    engine = sem_dialect.engine_of(binding.db_type)
    is_es = engine == "es"
    if not sem_dialect.is_known_db_type(binding.db_type) and not is_es:
        warnings.append(
            f"unknown db_type '{binding.db_type}', compiled with mysql dialect fallback")

    if query.nest:
        return _plan_nested_query(query, binding, dialect, column_policies)

    # 结构化窗口与数据集对象互斥(fail-loud, 不静默忽略窗口诉求)
    if query.window and binding.bind_kind == "dataset":
        warnings.append("window 不支持数据集对象; 请去掉 window 或改用普通对象")
        return PlannedExecution(
            sql="", secured_sql="", route=g.query_mode, dialect=dialect,
            datasource_id=binding.datasource_id,
            resolved_tables=[binding.physical_table],
            guardrail=g, access=binding.access, warnings=warnings)

    # 数据集对象: 绑定的是参数化预制 SQL 而非单表聚合, 走独立编译分支
    if binding.bind_kind == "dataset":
        return _plan_dataset(query, binding, dialect, warnings)

    # ES(Elasticsearch SQL) 不可表达形态: 编译期 fail-loud(不降级/不拿别的形状凑合)
    if is_es:
        if query.window:
            warnings.append(
                "Elasticsearch 不支持窗口函数(结构化 window)：已拒绝编译；"
                "请改用时间粒度分组 + 多轮取数（环比/排名在平台侧计算）")
            return _empty_plan(binding, query, dialect, warnings)
        if str(query.time_grain or "").strip().lower() == "quarter":
            warnings.append(
                "Elasticsearch 不支持季度粒度的时间桶（未取证的安全写法，不静默凑合）："
                "已拒绝编译；请改用 月/年 粒度，或用 time_range(如 this_quarter/last_quarter) 过滤")
            return _empty_plan(binding, query, dialect, warnings)

    # ① 护栏预检: huge + raw_source 且未开全表扫 -> 必须有 filters 或 LIMIT
    if g.size_class == "huge" and not g.allow_full_scan:
        if not query.filters and not query.limit and not query.time_window:
            return PlannedExecution(
                sql="", secured_sql="", route=g.query_mode, dialect=dialect,
                datasource_id=binding.datasource_id,
                resolved_tables=[binding.physical_table],
                guardrail=g, access=binding.access,
                warnings=[_HUGE_BLOCK_MESSAGE],
                provenance=_provenance(binding, query),
            )

    # ② 解析: measure/dim 名 -> 物理列/表达式
    resolver = _ColumnResolver(binding, dialect=dialect)

    # 投影别名槽位：SLS/Presto 拒绝任何非 ASCII 标识符（连双引号包裹的中文别名也
    # 直接语法错，探针 M/S15 ERR），而投影别名由请求的业务名决定，建模侧无法规避。
    # 与数据集编译分支（dataset_compiler）同源：本引擎下走稳定 ASCII 槽位(o0/o1..)，
    # 业务名记进 output_header_map 由执行层 `_to_result` 映回；其他方言行为完全不变
    # （no-silent-degradation：只为确实不兼容的引擎加一条显式映射，不改能跑通的通道）。
    ascii_out = resolver.engine == "sls"
    slot_of: dict[str, str] = {}
    header_map: dict[str, str] = {}

    def _proj_alias(name: str) -> str:
        disp = _alias_of(name)
        if not ascii_out:
            return disp
        slot = slot_of.get(disp)
        if slot is None:
            slot = f"o{len(slot_of)}"
            slot_of[disp] = slot
            header_map[slot] = disp
        return slot

    # ①.5 跨对象引用: 限定业务名(对象名.业务名) -> links 解链 JOIN(≤3 跳)
    join_ctx: dict[str, tuple[_ColumnResolver, str]] = {}   # obj_ref -> (resolver, alias)
    join_parts: list[str] = []
    alias_by_obj: dict[str, str] = {}          # obj_key -> tN 别名(确定性首现序)
    fb_by_obj: dict[str, ResolvedBinding] = {}
    resolve_failed: set[str] = set()           # 无路径/无绑定的 obj_ref(名字直接跳过)
    needed: list[str] = []
    for name in [*query.dimensions, *query.metrics,
                 *[f.dim for f in query.filters],
                 *[f.dim for f in query.having],
                 *[o.by for o in query.order],
                 *[operand for cm in query.computed_metrics for operand in cm.operands],
                 *([query.window.metric] if query.window and query.window.metric else [])]:
        obj_ref, _ = _split_qualified(name)
        if obj_ref and obj_ref not in needed:
            needed.append(obj_ref)
    if needed:
        links_data = _load_object_links(binding.datasource_id)
        src_key = str(binding.object_key or query.object or "").strip()
        edges_seq: list[tuple[str, str, str]] = []   # (prev_key, tgt_key, join)
        chain_ok: set[str] = set()
        for obj_ref in sorted(needed):
            tgt_key = links_data["names"].get(_ref_key(obj_ref)) or _ref_key(obj_ref)
            if tgt_key == _identity(src_key) or tgt_key == src_key:
                chain_ok.add(obj_ref)   # 限定到主对象自身: 退化主表查询
                continue
            chain = _link_chain(links_data["edges"], src_key, tgt_key)
            if chain is None:
                # 区分“真的无路径”与“多条同长度路径（口径不唯一）”：后者不能任选一条，
                # 否则同一问题会因图边顺序不同而得到不同口径的数据（宁缺勿错）。
                paths = _link_chains(links_data["edges"], src_key, tgt_key)
                if len(paths) > 1:
                    cand = " / ".join(
                        "→".join([src_key] + [t for t, _ in c]) for c in paths[:3])
                    warnings.append(
                        f"跨对象引用「{obj_ref}.…」存在多条同长度关联路径({cand})，口径不唯一，"
                        "已跳过；请直接以关系对象为主对象提问，或在本体中约定唯一关联口径")
                else:
                    warnings.append(
                        f"跨对象引用「{obj_ref}.…」无关联路径(≤{_JOIN_MAX_HOPS}跳), 已跳过; "
                        "请确认对象关系(links)已建模或改用主对象字段")
                resolve_failed.add(obj_ref)
                continue
            prev, ok = src_key, True
            for tgt, join_expr in chain:
                if tgt not in fb_by_obj:
                    fb = _resolve_foreign_binding(tgt, binding.datasource_id)
                    if fb is None or not fb.physical_table:
                        ok = False
                        break
                    fb_by_obj[tgt] = fb
                edge = (prev, tgt, join_expr)
                if edge not in edges_seq:
                    edges_seq.append(edge)
                prev = tgt
            if not ok:
                warnings.append(f"跨对象引用「{obj_ref}.…」的目标对象未绑定物理表, 已跳过")
                resolve_failed.add(obj_ref)
                continue
            chain_ok.add(obj_ref)
        for _prev, tgt, _j in edges_seq:
            if tgt not in alias_by_obj:
                alias_by_obj[tgt] = f"t{len(alias_by_obj) + 1}"
        # 表限定名 → 查询内引用: 物理表名 与 对象 key 都按 identity 归一登记。
        # 建模常以对象显示名(如 Equipment)书写 links[].join, 而物理表却叫 t_product_equipment;
        # 只按物理表名改写会漏掉显示名限定, 生成 `ON Equipment.company_code` 被数据源拒
        # (Unknown column) 并被误报成"数据源不可用"(掩盖改写缺口, 违反 no-silent-degradation)。
        # 故物理表名/对象 key 一并按大小写·分隔符无关的 identity 映射; 主表在 FROM 不限定,
        # 其限定名改写回物理裸名(与 FROM 中的表名一致)。
        main_table = str(binding.physical_table or "").strip()
        ref_alias: dict[str, str] = {}

        def _reg_ref(fb: ResolvedBinding, as_ref: str) -> None:
            for token in (fb.physical_table, fb.object_key):
                idk = _identity(token)
                if idk and as_ref:
                    ref_alias.setdefault(idk, as_ref)

        _reg_ref(binding, main_table)
        for obj_k, al in alias_by_obj.items():
            _reg_ref(fb_by_obj[obj_k], al)

        def _rewrite_join(expr: str) -> tuple[str, list[str]]:
            """把 join 条件里的表限定名改写为查询内引用; 返回(改写结果, 无法解析的限定名)。"""
            unknown: list[str] = []

            def _repl(m):
                tok = m.group(1)
                ref = ref_alias.get(_identity(tok))
                if ref is None:
                    if tok not in unknown:
                        unknown.append(tok)
                    return m.group(0)
                return f"{ref}{m.group(0)[len(tok):]}"

            out = re.sub(r"(?<![\w.`])([A-Za-z_]\w*)\.(?=\w)", _repl, str(expr or ""))
            return out, unknown

        # 逐边改写; 含无法解析限定名 / 跨跳占位箭头(→) 的关联视为不可用(不硬造)。
        rewritten: list[tuple[str, str, str]] = []   # (prev, tgt, join_sql)
        for _prev, tgt, join_expr in edges_seq:
            join_sql, unknown = _rewrite_join(join_expr)
            if unknown or "→" in join_sql or "—" in join_sql:
                warnings.append(
                    f"跨对象关联「{_prev}→{_identity(tgt)}」的 join 条件引用了未建模/无法解析"
                    "的对象或含跨跳占位符, 已跳过该关联; 请在本体中补全对应对象或修正 links 关系")
                continue
            rewritten.append((_prev, tgt, join_sql))
        # 反复剔除引用了"被丢弃关联别名"(该别名对应表不在 FROM)的下游边, 直到收敛,
        # 防止只跳过中间边却留下引用其别名的边再次生成非法 SQL。
        while True:
            kept_refs = {alias_by_obj[t] for (_p, t, _j) in rewritten}
            dropped = [it for it in rewritten
                       if any(m.group(1) not in kept_refs
                              for m in re.finditer(r"(?<![\w.`])(t\d+)\.(?=\w)", it[2]))]
            if not dropped:
                break
            for (_p, _t, _j) in dropped:
                warnings.append(f"跨对象关联「{_p}→{_t}」依赖的关联已跳过, 一并跳过")
            rewritten = [it for it in rewritten if it not in dropped]
        bad_edge_tgts = ({t for (_p, t, _j) in edges_seq}
                         - {t for (_p, t, _j) in rewritten})
        for _prev, tgt, join_sql in rewritten:
            join_parts.append(
                f"LEFT JOIN {fb_by_obj[tgt].physical_table} AS {alias_by_obj[tgt]} ON {join_sql}")
        for obj_ref in sorted(needed):
            if obj_ref in resolve_failed or obj_ref not in chain_ok:
                continue
            tgt_key = links_data["names"].get(_ref_key(obj_ref)) or _ref_key(obj_ref)
            if tgt_key == src_key or tgt_key == _identity(src_key):
                join_ctx[obj_ref] = (resolver, "")   # 主对象别名: 不限定
                continue
            chain = _link_chain(links_data["edges"], src_key, tgt_key) or []
            if any(t in bad_edge_tgts for (t, _j) in chain):
                resolve_failed.add(obj_ref)   # 关联被跳过: 回抛候选, 不留悬空别名
                continue
            join_ctx[obj_ref] = (
                _ColumnResolver(fb_by_obj[tgt_key], dialect=dialect,
                                column_prefix=alias_by_obj[tgt_key]),
                alias_by_obj[tgt_key],
            )

    def _pick(name: str) -> tuple[Optional[_ColumnResolver], str, bool]:
        """无限定 -> 主 resolver; 限定 -> 链上对象 resolver(列已带表别名)。"""
        obj_ref, rest = _split_qualified(name)
        if not obj_ref:
            return resolver, name, True
        if obj_ref in resolve_failed:
            return None, rest, False
        ctx = join_ctx.get(obj_ref)
        if ctx is None:
            return None, rest, False
        return ctx[0], rest, True

    # ES 无 JOIN(实机 400)：跨对象字段在本引擎根本编译不出来，先拒并说清原因，
    # 不静默丢掉跨对象部分只算本表(那会把“某客户在某设备的操作数”算成全量)。
    if is_es and join_parts:
        warnings.append(
            "Elasticsearch 不支持 JOIN：本次请求引用了其他对象的字段（"
            + "、".join(sorted(alias_by_obj)) + "），无法在本源上联合取数；"
            "已拒绝编译，请只问本对象内的维度/指标，或改用数据集通道")
        return _empty_plan(binding, query, dialect, warnings)

    select_items: list[tuple[str, str]] = []   # (expr, alias)
    group_by: list[str] = []
    col_roles: dict[str, str] = {}
    metric_expressions: dict[str, str] = {}
    computed_sources: set[str] = set()
    unresolved: list[dict] = []   # 结构化未解析项(供别名回流队列, 只含业务词)

    # 列级安全策略：编译期写进投影（执行后按列名脱敏在业务别名/SLS 槽位链路上对不上名）
    if column_policies is None:
        from backend.semantics import rls as _rls
        try:
            column_policies = _rls.column_policies(
                user_id=int(query.user_id or 0), workspace_id=int(query.workspace_id or 0),
                datasource_id=int(binding.datasource_id or 0),
                table_names=[binding.physical_table,
                             *[fb.physical_table for fb in fb_by_obj.values()]],
            )
        except PermissionError as e:
            # 敏感基线读不到 = 不知道该怎么脱敏，宁不执行也不下发未脱敏明细
            warnings.append(f"列级安全策略不可用，已拒绝编译: {e}")
            return _empty_plan(binding, query, dialect, warnings)

    for d in query.dimensions:
        r, d_rest, ok = _pick(d)
        if not ok or r is None:
            warnings.append(f"无法解析维度「{d}」(跨对象引用未关联/无路径), 已跳过")
            unresolved.append({"term": d, "type": "dimension",
                               "reason": "no_join_path", "candidates": []})
            continue
        expr, role = r.resolve_dimension(d_rest, time_grain=query.time_grain)
        if expr is None:
            hint = r.fuzzy_hint(d_rest)
            # 本对象没有 → 去关联对象找同名维度，回可直接使用的「对象key.名称」写法
            xhits = ([] if _split_qualified(d)[0]
                     else _cross_object_suggestions(binding, d_rest, "dimension"))
            if xhits:
                warnings.append(
                    f"无法解析维度「{d}」, 已跳过。该名称在关联对象上有同名维度: "
                    f"{' / '.join(xhits[:5])}（跨对象引用写法 `对象key.名称`，语义层自动关联 JOIN）")
            else:
                warnings.append(
                    f"无法解析维度「{d}」, 已跳过。{hint or f'本对象可用维度: {r.suggest_dimensions()}; 请用字典名或其别名重试'}")
            unresolved.append({
                "term": d, "type": "dimension",
                "reason": r.resolution_sources().get(d_rest) or "unresolved",
                "candidates": r.fuzzy_candidates(d_rest) or xhits,
            })
            continue
        alias = _proj_alias(d)
        # 脱敏后的表达式同时用于 SELECT 与 GROUP BY（两者必须同文，否则分组口径错位）
        expr = _apply_column_policy(expr, column_policies,
                                    dialect=dialect, engine=resolver.engine)
        select_items.append((expr, alias))
        col_roles[alias] = role
        group_by.append(expr)

    for m in query.metrics:
        r, m_rest, ok = _pick(m)
        if not ok or r is None:
            warnings.append(f"无法解析指标「{m}」(跨对象引用未关联/无路径), 已跳过")
            unresolved.append({"term": m, "type": "metric",
                               "reason": "no_join_path", "candidates": []})
            continue
        expr, agg_type, unit, unsupported = r.resolve_measure(m_rest)
        if unsupported:
            _eng = (f"/{r.engine}" if r.engine and r.engine != dialect else "")
            warnings.append(
                f"指标「{m}」未配置 {dialect}{_eng} 方言表达式(formula_dialects), 已跳过")
        if expr is None:
            xm = ([] if _split_qualified(m)[0]
                  else _cross_object_suggestions(binding, m_rest, "metric"))
            if xm:
                warnings.append(
                    f"无法解析指标「{m}」, 已跳过。该名称在关联对象上有同名指标: "
                    f"{' / '.join(xm[:5])}（跨对象引用写法 `对象key.名称`，语义层自动关联 JOIN）")
            else:
                warnings.append(
                    f"无法解析指标「{m}」, 已跳过。本对象可用指标: "
                    f"{r.suggest_metrics()}; 请用字典名或其别名重试")
            unresolved.append({
                "term": m, "type": "metric", "reason": "unresolved", "candidates": xm,
            })
            continue
        metric_expressions[_norm_name(m)] = expr
        metric_expressions[_norm_name(_alias_of(m))] = expr
        alias = _proj_alias(m)
        select_items.append((expr, alias))
        col_roles[alias] = "measure"
        # 附带单位与聚合信息供溯源

    for cm in query.computed_metrics:
        expr = _compile_computed_expression(
            cm.expr, cm.operands, metric_expressions)
        if expr is None:
            warnings.append(
                f"派生指标「{cm.name}」引用了未解析指标或包含不受控语法，已被拒绝")
            continue
        alias = _proj_alias(cm.name)
        select_items.append((expr, alias))
        col_roles[alias] = "measure"
        metric_expressions[_norm_name(cm.name)] = expr
        metric_expressions[_norm_name(_alias_of(cm.name))] = expr
        computed_sources.add(cm.name)
    if not select_items:
        # 至少给一个 "*" 的兜底, 但更常见是调用方忘了 metrics/dimensions
        warnings.append("no valid measure/dimension selected; returning row preview only")
        select_items = [("*", "_preview")]

    # ③ WHERE
    where_parts: list[str] = []
    for f in query.filters:
        r, f_rest, ok = _pick(f.dim)
        if not ok or r is None:
            warnings.append(f"无法解析过滤维度「{f.dim}」(跨对象引用未关联/无路径)")
            if not any(u["term"] == f.dim and u["type"] == "dimension" for u in unresolved):
                unresolved.append({"term": f.dim, "type": "dimension",
                                   "reason": "no_join_path", "candidates": []})
            continue
        f_arg = f.model_copy(update={"dim": f_rest}) if f_rest != f.dim else f
        cond = r.render_filter(f_arg)
        if cond:
            where_parts.append(cond)
        else:
            hint = r.fuzzy_hint(f_rest)
            warnings.append(
                f"无法解析过滤维度「{f.dim}」。{hint or f'本对象可用维度: {r.suggest_dimensions()}'}")
            if not any(u["term"] == f.dim and u["type"] == "dimension" for u in unresolved):
                unresolved.append({
                    "term": f.dim, "type": "dimension",
                    "reason": r.resolution_sources().get(f_rest) or "unresolved",
                    "candidates": r.fuzzy_candidates(f_rest),
                })

    # ③.5 时间条件(优先级): time_range(日历) > time_start/time_end(绝对) > time_window(相对滚动)
    if query.time_range:
        _tc = resolver.render_time_range(
            query.time_range, query.time_column, warnings)
        if _tc:
            where_parts.append(_tc)
    elif query.time_start or query.time_end:
        _tc = resolver.render_time_bounds(
            query.time_start, query.time_end, query.time_column, warnings)
        if _tc:
            where_parts.append(_tc)
    elif query.time_window:
        win_cond = resolver.render_time_window(
            query.time_window, query.time_column, warnings)
        if win_cond:
            where_parts.append(win_cond)

    # 聚合后过滤必须引用已解析指标表达式；不把 HAVING 当作普通列过滤，
    # 也不允许把用户给的原始字段/SQL片段拼进语句。
    having_parts: list[str] = []
    for f in query.having:
        expr = metric_expressions.get(_norm_name(f.dim))
        if not expr:
            warnings.append(f"无法解析聚合过滤指标「{f.dim}」，HAVING 条件已跳过")
            continue
        cond = _render_condition(expr, f.op, f.value, f.values)
        if cond:
            having_parts.append(cond)
        else:
            warnings.append(f"聚合过滤指标「{f.dim}」的条件无效，HAVING 条件已跳过")

    # ④ ORDER BY
    order_parts: list[str] = []
    for o in query.order:
        cands = [o.by, _alias_of(o.by)]
        if ascii_out:
            # 业务名在 SQL 里只以投影槽位存在, 先映到槽位, 原名作兼容位
            cands = [slot_of.get(c, c) for c in cands] + cands
        key = next((c for c in cands if c in col_roles), None)
        if key is not None:
            order_parts.append(f"{_quote_alias(key, dialect)} {'DESC' if o.desc else 'ASC'}")
        else:
            warnings.append(f"order-by references unknown field: {o.by}")

    # ⑤ LIMIT (护栏兜底)
    limit_val = query.limit
    if limit_val is None:
        if g.force_limit:
            limit_val = g.max_rows or _DEFAULT_LIMIT
            warnings.append(f"guardrail: force_limit applied ({limit_val})")
        elif g.max_rows:
            limit_val = g.max_rows
    elif g.max_rows and limit_val > g.max_rows:
        limit_val = g.max_rows
        warnings.append(f"guardrail: limit clamped to max_rows ({limit_val})")
    if is_es and limit_val is None:
        # ES SQL 不带 LIMIT 一律被引擎拒(硬要求)；这里显式补齐并留痕，不靠执行层兜
        limit_val = _DEFAULT_LIMIT
        warnings.append(f"Elasticsearch 必须带 LIMIT: 已按默认 {limit_val} 行补齐")

    table_ref = _exec_table_ref(binding, dialect)
    if join_parts:
        table_ref = " ".join([table_ref, *join_parts])

    if query.window:
        win_sql = _render_window_sql(
            query.window, table_ref, select_items, where_parts, group_by,
            order_parts, limit_val, dialect, col_roles, warnings, slot_of,
            having_parts=having_parts)
        if not win_sql:
            return PlannedExecution(
                sql="", secured_sql="", route=g.query_mode, dialect=dialect,
                datasource_id=binding.datasource_id,
                resolved_tables=[binding.physical_table],
                guardrail=g, access=binding.access,
                provenance=_provenance(binding, query), warnings=warnings)
        sql = win_sql
    else:
        sql = _render_sql(
            table_ref=table_ref,
            select_items=select_items,
            where_parts=where_parts,
            group_by=group_by,
            having_parts=having_parts,
            order_parts=order_parts,
            limit=limit_val,
            dialect=dialect,
        )

    # ES 产物守门(纵深)：上面各分支已避开 ES 不认的形态，这里再按产物白名单复核一道，
    # 防后续新增图型/函数在没人注意时把 SUBSTR/反引号/派生表写进去。
    if is_es:
        from backend.semantics.es_dialect import es_rejection

        _reject = es_rejection(sql)
        if _reject:
            warnings.append(f"ES 编译产物形态不被引擎接受，已拒绝执行：{_reject}")
            return _empty_plan(binding, query, dialect, warnings)

    prov = _provenance(binding, query)
    # 服务端诊断用(工具侧 provenance 已剥离): 供 gates/Playground 在解析失败时给出候选
    prov["available_dimensions"] = resolver.available_dimension_names()
    # 归因: 每个请求名字的解析来源(dict_exact/dict_alias/column_map/physical_desc/phys_col/fuzzy_rejected)
    combined_sources = dict(resolver.resolution_sources())
    for name in computed_sources:
        combined_sources[name] = "computed"
    for _n in [*query.dimensions, *query.metrics, *[f.dim for f in query.filters]]:
        _o, _rest = _split_qualified(_n)
        if _o and _o in join_ctx:
            got = join_ctx[_o][0]._res_trace.get(_rest)
            if got:
                combined_sources[_n] = got
    prov["resolution_sources"] = combined_sources
    if column_policies:
        # 诊断用：本次编译写进 SQL 的列级脱敏策略（provenance 不下发 LLM/前端，审计可查）
        prov["column_policies_applied"] = dict(sorted(column_policies.items()))
    # 结构化未解析项(业务词+候选), 供别名回流队列自动落入(不含物理细节)
    prov["unresolved_terms"] = unresolved

    return PlannedExecution(
        sql=sql,
        secured_sql=sql,   # Phase 4 RLS 改写会覆盖 secured_sql
        dialect=dialect,
        route=g.query_mode,
        datasource_id=binding.datasource_id,
        resolved_tables=[binding.physical_table]
        + [fb.physical_table for fb in fb_by_obj.values()],
        guardrail=g,
        access=binding.access,
        provenance=prov,
        warnings=warnings,
        output_header_map=header_map,
    )


# ── 受控计算表达式与嵌套聚合 ─────────────────────────────────────

_COMPUTED_EXPR_TOKEN_RE = re.compile(
    r"\s*(\{[^{}]+\}|(?:\d+(?:\.\d*)?|\.\d+)|[()+\-*/])")


def _compile_computed_expression(expr: str, operands: list[str],
                                 metric_expressions: dict[str, str]) -> Optional[str]:
    """把 {指标} 四则表达式编译为 SQL；不允许标识符、函数名或原始 SQL。"""
    text = str(expr or "").strip()
    if not text or len(text) > 256:
        return None
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        match = _COMPUTED_EXPR_TOKEN_RE.match(text, pos)
        if not match:
            return None
        tokens.append(match.group(1))
        pos = match.end()

    declared = {_norm_name(name) for name in (operands or []) if str(name or "").strip()}
    index = 0

    def factor():
        nonlocal index
        if index >= len(tokens):
            raise ValueError("missing operand")
        token = tokens[index]
        if token in ("+", "-"):
            index += 1
            return ("unary", token, factor())
        if token == "(":
            index += 1
            node = expression()
            if index >= len(tokens) or tokens[index] != ")":
                raise ValueError("unclosed parenthesis")
            index += 1
            return node
        if token.startswith("{") and token.endswith("}"):
            name = token[1:-1].strip()
            key = _norm_name(name)
            if not key or key not in declared or key not in metric_expressions:
                raise ValueError("undeclared metric")
            index += 1
            return ("value", metric_expressions[key])
        if re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", token):
            index += 1
            return ("value", token)
        raise ValueError("invalid token")

    def term():
        nonlocal index
        node = factor()
        while index < len(tokens) and tokens[index] in ("*", "/"):
            op = tokens[index]
            index += 1
            node = ("binary", op, node, factor())
        return node

    def expression():
        nonlocal index
        node = term()
        while index < len(tokens) and tokens[index] in ("+", "-"):
            op = tokens[index]
            index += 1
            node = ("binary", op, node, term())
        return node

    def render(node) -> str:
        if node[0] == "value":
            return f"({node[1]})" if any(c in node[1] for c in "() ") else node[1]
        if node[0] == "unary":
            return f"({node[1]}{render(node[2])})"
        _, op, left, right = node
        lhs, rhs = render(left), render(right)
        if op == "/":
            return f"({lhs} / NULLIF({rhs}, 0))"
        return f"({lhs} {op} {rhs})"

    try:
        tree = expression()
        if index != len(tokens):
            return None
        return render(tree)
    except (ValueError, IndexError):
        return None


def _plan_nested_query(query: SemanticQuery, binding: ResolvedBinding, dialect: str,
                       column_policies: Optional[dict[str, str]]) -> PlannedExecution:
    """编译受控的两层聚合：inner 语义查询作为派生表，outer 只引用其输出别名。"""
    warnings: list[str] = []
    nest = query.nest
    if binding.bind_kind == "dataset":
        return _empty_plan(binding, query, dialect,
                           ["数据集对象不支持嵌套聚合；请使用已治理的数据集查询"])
    if sem_dialect.engine_of(binding.db_type) == "es":
        return _empty_plan(binding, query, dialect,
                           ["Elasticsearch SQL 不支持派生表，已拒绝嵌套聚合"])
    if (query.metrics or query.dimensions or query.filters or query.having
            or query.computed_metrics or query.window):
        return _empty_plan(binding, query, dialect,
                           ["nest 不能与顶层 metrics/dimensions/filters/having/window 混用"])
    if not nest.inner.metrics and not nest.inner.dimensions:
        return _empty_plan(binding, query, dialect,
                           ["nest.inner 至少需要一个已声明指标或维度"])
    if not nest.outer.metrics:
        return _empty_plan(binding, query, dialect,
                           ["nest.outer.metrics 不能为空"])

    # 子查询负责聚合完整的 inner 结果；响应行数限制只放在外层，避免先截断再聚合。
    # huge 表仍由普通 plan 的护栏检查，未授权全扫且没有过滤条件时会拒绝。
    inner_guardrail = binding.guardrail.model_copy(
        update={"force_limit": False, "max_rows": None})
    inner_binding = binding.model_copy(update={"guardrail": inner_guardrail})
    inner_query = query.model_copy(update={
        "metrics": list(nest.inner.metrics),
        "dimensions": list(nest.inner.dimensions),
        "filters": list(nest.inner.filters),
        "having": [], "computed_metrics": [], "nest": None,
        "order": [], "limit": None, "window": None,
    })
    inner_plan = plan(inner_query, inner_binding, column_policies=column_policies)
    warnings.extend(inner_plan.warnings or [])
    if not inner_plan.sql or any(
            "无法解析" in w or "未配置" in w or "no valid" in w for w in warnings):
        if not warnings:
            warnings.append("nest.inner 未能生成有效聚合 SQL")
        return _empty_plan(binding, query, dialect, warnings)

    # inner SELECT alias → 在子查询中可安全引用的列名（SLS 会使用 ASCII 槽位）。
    header_inverse = {
        _norm_name(original): slot
        for slot, original in (inner_plan.output_header_map or {}).items()
    }
    inner_names = [*nest.inner.dimensions, *nest.inner.metrics]
    inner_columns = {
        _norm_name(name): header_inverse.get(
            _norm_name(_alias_of(name)), _alias_of(name))
        for name in inner_names
    }

    select_items: list[tuple[str, str]] = []
    outer_metric_exprs: dict[str, str] = {}
    output_header_map: dict[str, str] = {}
    ascii_out = sem_dialect.engine_of(binding.db_type) == "sls"
    output_slots: dict[str, str] = {}

    def outer_alias(name: str) -> str:
        display = _alias_of(name)
        if not ascii_out:
            return display
        slot = output_slots.get(display)
        if slot is None:
            slot = f"o{len(output_slots)}"
            output_slots[display] = slot
            output_header_map[slot] = display
        return slot

    for metric in nest.outer.metrics:
        agg = metric.agg.upper()
        source = metric.of.strip()
        if source == "*":
            if agg != "COUNT":
                warnings.append("nest.outer 只有 COUNT 可以聚合 *")
                return _empty_plan(binding, query, dialect, warnings)
            expr = "COUNT(*)"
        else:
            inner_alias = inner_columns.get(_norm_name(source))
            if not inner_alias:
                warnings.append(f"nest.outer 指标「{metric.name}」引用了未投影的 inner 字段")
                return _empty_plan(binding, query, dialect, warnings)
            ref = _quote_alias(inner_alias, dialect)
            expr = f"COUNT(DISTINCT {ref})" if agg == "COUNT_DISTINCT" else f"{agg}({ref})"
        alias = outer_alias(metric.name)
        select_items.append((expr, alias))
        outer_metric_exprs[_norm_name(metric.name)] = expr
        outer_metric_exprs[_norm_name(_alias_of(metric.name))] = expr

    where_parts: list[str] = []
    for f in nest.outer.filters:
        inner_alias = inner_columns.get(_norm_name(f.dim))
        if not inner_alias:
            warnings.append(f"nest.outer filter 引用了未投影的 inner 字段「{f.dim}」")
            return _empty_plan(binding, query, dialect, warnings)
        cond = _render_condition(_quote_alias(inner_alias, dialect), f.op, f.value, f.values)
        if not cond:
            warnings.append(f"nest.outer filter「{f.dim}」条件无效")
            return _empty_plan(binding, query, dialect, warnings)
        where_parts.append(cond)

    having_parts: list[str] = []
    for f in nest.outer.having:
        expr = outer_metric_exprs.get(_norm_name(f.dim))
        if not expr:
            warnings.append(f"nest.outer having 引用了未声明的外层指标「{f.dim}」")
            return _empty_plan(binding, query, dialect, warnings)
        cond = _render_condition(expr, f.op, f.value, f.values)
        if not cond:
            warnings.append(f"nest.outer having「{f.dim}」条件无效")
            return _empty_plan(binding, query, dialect, warnings)
        having_parts.append(cond)

    order_parts: list[str] = []
    output_aliases = {
        _norm_name(m.name): outer_alias(m.name) for m in nest.outer.metrics
    }
    for order in query.order:
        alias = output_aliases.get(_norm_name(order.by))
        if not alias:
            warnings.append(f"nest.order 引用了未投影的外层指标「{order.by}」")
            return _empty_plan(binding, query, dialect, warnings)
        order_parts.append(f"{_quote_alias(alias, dialect)} {'DESC' if order.desc else 'ASC'}")

    g = binding.guardrail
    limit = query.limit
    if limit is None:
        limit = g.max_rows or (_DEFAULT_LIMIT if g.force_limit else None)
    sql = _render_sql(
        table_ref=f"({inner_plan.sql}) AS _nest",
        select_items=select_items,
        where_parts=where_parts,
        group_by=[], order_parts=order_parts, limit=limit, dialect=dialect,
        having_parts=having_parts,
    )
    provenance = _provenance(binding, query)
    provenance["nested"] = True
    return PlannedExecution(
        sql=sql, secured_sql=sql, dialect=dialect, route=g.query_mode,
        datasource_id=binding.datasource_id,
        resolved_tables=inner_plan.resolved_tables,
        guardrail=g, access=binding.access, provenance=provenance,
        warnings=warnings, output_header_map=output_header_map,
    )


# ── SQL renderer (简单确定性, 不依赖 sqlglot) ─────────────────

def _render_sql(
    table_ref: str,
    select_items: list[tuple[str, str]],
    where_parts: list[str],
    group_by: list[str],
    order_parts: list[str],
    limit: Optional[int],
    dialect: str = "mysql",
    having_parts: Optional[list[str]] = None,
) -> str:
    # 别名引用符随方言: MySQL 反引号 / PG 双引号(否则 PG 下 `AS \`x\`` 非法)
    proj = ", ".join(
        "*" if expr == "*" else f"{expr} AS {_quote_alias(alias, dialect)}"
        for expr, alias in select_items
    )
    parts = [f"SELECT {proj}", f"FROM {table_ref}"]
    if where_parts:
        parts.append("WHERE " + " AND ".join(where_parts))
    if group_by:
        parts.append("GROUP BY " + ", ".join(group_by))
    if having_parts:
        parts.append("HAVING " + " AND ".join(having_parts))
    if order_parts:
        parts.append("ORDER BY " + ", ".join(order_parts))
    if limit is not None:
        parts.append(f"LIMIT {int(limit)}")
    return "\n".join(parts)


def _render_window_sql(
    w: SemanticWindow,
    table_ref: str,
    select_items: list[tuple[str, str]],
    where_parts: list[str],
    group_by: list[str],
    order_parts: list[str],
    limit: Optional[int],
    dialect: str,
    col_roles: dict[str, str],
    warnings: list[str],
    slot_of: Optional[dict[str, str]] = None,
    having_parts: Optional[list[str]] = None,
) -> Optional[str]:
    """结构化窗口 -> "内层聚合 + 外层窗口" 两层结构(窗口与 GROUP BY 不同层)。

    partition←dimensions(rolling/period_compare 剔除时间维)、order←order[];
    窗口表达式仅进 SELECT 投影层。配置非法返回 None 并记 warnings(宁缺勿错)。
    slot_of: SLS 类引擎的业务名 → 投影槽位(别名不在 SQL 里, 必须映后才能引用)。
    """
    slot_of = slot_of or {}
    metric_aliases = [a for a, r in col_roles.items() if r == "measure"]
    dim_aliases = [a for a, r in col_roles.items() if r != "measure"]
    time_aliases = [a for a, r in col_roles.items() if r == "time"]

    if w.metric:
        target = _alias_of(w.metric)
        target = slot_of.get(target, target)
    else:
        target = metric_aliases[0] if metric_aliases else ""
    if w.type != "rank" and target not in metric_aliases:
        warnings.append(
            f"window.{w.type} 需要已解析的目标指标, 「{w.metric or ''}」未命中; "
            f"可用指标: {metric_aliases}")
        return None
    if not select_items:
        warnings.append("window 需要至少一个已解析的指标/维度")
        return None

    inner = _render_sql(
        table_ref, select_items, where_parts, group_by, [], None, dialect,
        having_parts=having_parts)
    m_q = _quote_alias(target, dialect) if target else ""
    part_all = [_quote_alias(a, dialect) for a in dim_aliases]
    part_no_time = [_quote_alias(a, dialect) for a in dim_aliases if a not in time_aliases]
    t_q = _quote_alias(time_aliases[0], dialect) if time_aliases else ""

    def _over(part: list[str], tail: str = "") -> str:
        seg = []
        if part:
            seg.append("PARTITION BY " + ", ".join(part))
        if tail:
            seg.append(tail)
        return "OVER (" + " ".join(seg) + ")"

    win_cols: list[str] = []
    ord_sql = ", ".join(order_parts)

    if w.type == "rank":
        ord_sql = ord_sql or (f"{m_q} DESC" if m_q else "")
        if not ord_sql:
            warnings.append("window.rank 需要 order[] 或至少一个指标用于排序")
            return None
        win_cols.append(
            f"RANK() {_over(part_all, 'ORDER BY ' + ord_sql)} AS {_quote_alias('rank', dialect)}")
    elif w.type == "rolling":
        n = int((w.params or {}).get("preceding", 2))
        if n < 0:
            warnings.append("window.rolling.params.preceding 必须为非负整数")
            return None
        if not t_q:
            warnings.append("window.rolling 需要时间维度参与分组(先给时间维度+time_grain)")
            return None
        win_cols.append(
            f"SUM({m_q}) {_over(part_no_time, f'ORDER BY {t_q} ROWS BETWEEN {n} PRECEDING AND CURRENT ROW')} "
            f"AS {_quote_alias(target + '_rolling', dialect)}")
    elif w.type == "period_compare":
        lag = int((w.params or {}).get("lag", 1))
        if lag < 1:
            warnings.append("window.period_compare.params.lag 必须为正整数")
            return None
        if not t_q:
            warnings.append("window.period_compare 需要时间维度参与分组(先给时间维度+time_grain)")
            return None
        over = _over(part_no_time, f"ORDER BY {t_q}")
        win_cols.append(
            f"LAG({m_q}, {lag}) {over} AS {_quote_alias(target + '_prev', dialect)}")
        win_cols.append(
            f"({m_q} / NULLIF(LAG({m_q}, {lag}) {over}, 0) - 1) "
            f"AS {_quote_alias(target + '_pct', dialect)}")
    elif w.type == "share":
        # share 语义: 各分组占总量比例; partition=时间维(同周期内比), 无时间维=全局总量
        part = [_quote_alias(a, dialect) for a in time_aliases[:1]]
        win_cols.append(
            f"{m_q} / NULLIF(SUM({m_q}) {_over(part)}, 0) "
            f"AS {_quote_alias(target + '_share', dialect)}")
    else:
        warnings.append(f"未知 window.type: {w.type}")
        return None

    parts = [f"SELECT *, {', '.join(win_cols)}", f"FROM ({inner}) AS _agg"]
    if ord_sql:
        parts.append("ORDER BY " + ord_sql)
    if limit is not None:
        parts.append(f"LIMIT {int(limit)}")
    return "\n".join(parts)


def _is_agg(expr: str) -> bool:
    return any(re.match(rf"\b{fn}\s*\(", expr.upper()) for fn in _AGG_FNS)


def _quote_ident(name: str, dialect: str = "mysql") -> str:
    """标识符引用(单一口径在 semantics/dialect.py, 此处仅保留历史导入口径)。"""
    return sem_dialect.quote_ident(name, dialect)


_EXPR_CHAR_RE = sem_dialect._EXPR_CHAR_RE


def _norm_name(s: Any) -> str:
    """名称归一化: 全角→半角、去所有空白、casefold。

    让"创建时间/创立 时间/CREATE_TIME/create_time"这类写法归一到同一键。
    """
    t = str(s or "")
    # 全角 ASCII(FF01-FF5E) -> 半角
    t = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in t)
    t = t.replace("\u3000", "")
    return re.sub(r"\s+", "", t).casefold()


def _exec_table_ref(b: ResolvedBinding, dialect: str = "mysql") -> str:
    """执行侧表引用。

    MySQL/Doris 路: 只用 `db.table` 二段式。DataFusion 把数据源注册到默认
    catalog(`datafusion`), MySQL 直连则连到数据源自身 database;两条路径都不认
    元数据层 catalog 名(如 "adh"), 三段式会 `catalog not found`/`Unknown database`。

    PG 路: 不拼 `database.table` —— psycopg 连接已定 dbname, 二段式会被当成
    `schema.table` 报 schema 不存在;DataFusion 侧同样按注册的默认 schema 解析,
    故用裸表名(由 search_path/默认 schema 解析)。

    ES 路: 裸索引名 + **双引号**(ES SQL 用 `FROM "index"`; 反引号直接 400), 索引本身
    就是全域，拼 `database.table` 反而不存在。
    """
    if dialect in ("postgres", "es"):
        return _quote_ident(b.physical_table, dialect)
    parts = [p for p in (b.db_name, b.physical_table) if p]
    return ".".join(parts) or b.physical_table


# ── 列/指标/维度解析 ────────────────────────────────────────────

class _ColumnResolver:
    """把 SemanticQuery 的名字解析为物理列/表达式。

    解析优先级: 维度/指标字典精确名 → 字典别名(aliases/name_en/属性中文名) →
    binding.column_map → 物理列(含列业务名 business_desc)。包含式模糊不再静默绑定,
    只回抛候选(fuzzy_rejected)由调用方确认后重试(宁缺勿错)。
    字典行携带 value_labels(枚举码→业务标签)时, SELECT/GROUP BY 用同一 CASE 表达式,
    WHERE 则支持 label→code 反查(过滤可传业务名也可传码值)。
    dialect 决定标识符引用与时间函数(MySQL 系 DATE_FORMAT/反引号 vs PG to_char/双引号);
    engine 决定函数变体取用(见 semantics/dialect.py: 引擎键优先于族键)。
    """

    _TIME_TYPES = {"date", "datetime", "timestamp", "time"}

    def __init__(self, binding: ResolvedBinding, dialect: str = "mysql",
                 column_prefix: str = "", engine: str = ""):
        self.binding = binding
        self.dialect = dialect
        # 引擎级变体键(Doris/SLS 与同族通用写法的函数差异); 缺省按 binding.db_type 推导
        self.engine = engine or sem_dialect.engine_of(binding.db_type)
        self._prefix = column_prefix   # JOIN 侧表别名: 列引用统一限定; 空=主表不限定
        self._phys: dict[str, dict[str, Any]] | None = None
        self._metrics: dict[str, dict[str, Any]] | None = None
        self._dims: dict[str, dict[str, Any]] | None = None
        self._index: dict[str, dict[str, Any]] | None = None
        # 归因: normkey -> 来源类别; requested name -> 解析来源(供 plan provenance)
        self._dim_origin: dict[str, str] = {}
        self._res_trace: dict[str, str] = {}
        # T5: 包含式模糊命中不再静默绑定, 只回抛候选(宁缺勿错)
        self._fuzzy_candidates: dict[str, list[str]] = {}

    def _col(self, col: str) -> str:
        """列引用: 主表原样; JOIN 侧统一带表别名前缀(源头限定, 不做事后正则)。

        带点物理列(SLS LogStore 嵌套字段)必须整体加引号: 裸写 `view.url` 被引擎
        当「行变量.字段」访问, 直接 Column cannot be resolved(探针 A/B、Q8/Q9)。
        已带引号的登记值原样透传, 不二次转义。
        """
        s = str(col or "").strip()
        if ("." in s and self.engine in _DOTTED_COLUMN_ENGINES
                and not (s.startswith('"') and s.endswith('"'))):
            q = sem_dialect.quote_alias(s, self.dialect)
        else:
            q = _quote_ident(s, self.dialect)
        return f"{self._prefix}.{q}" if self._prefix else q

    def _fix_formula(self, expr: Optional[str]) -> Optional[str]:
        """公式文本规范化: 带点物理列整体加引号(引擎级) -> JOIN 侧裸列补表别名。

        指标公式不经 `_col` 路径(它是文本), 故带点列必须在此显式引号化,
        否则 `COUNT(DISTINCT session.id)` 这类合法建模写法在 SLS 上直接报错(探针 K)。
        顺序不能换: 先引号化, `_qualify_bare` 的前后瞻才不会把已引号片段再补一次
        前缀(生成 `t1.session.id` 这种非法引用)。
        """
        if not expr:
            return expr
        out = str(expr)
        if self.engine in _DOTTED_COLUMN_ENGINES:
            out = _quote_dotted_columns(out, self.dialect)
        if self._prefix:
            cols = (set(self._load_phys().keys())
                    | {str(v) for v in (self.binding.column_map or {}).values()})
            out = _qualify_bare(out, self._prefix, cols)
        return out

    def fuzzy_hint(self, name: str) -> str:
        """若该名称是被拒的模糊命中, 给出候选提示文案(仅业务名, 不暴露物理列)。"""
        cands = self._fuzzy_candidates.get(name)
        if not cands:
            return ""
        return (f"近似候选: {cands}; 未确认前不会自动绑定, "
                f"请确认语义后从候选中选准确名称重试。")

    def fuzzy_candidates(self, name: str) -> list[str]:
        """被拒模糊命中的业务名候选(无则空列表)。"""
        return list(self._fuzzy_candidates.get(name) or [])

    def resolution_sources(self) -> dict[str, str]:
        """返回本次编译中每个被请求名字的解析来源。

        取值: dict_exact / dict_alias / column_map / physical_desc / phys_col / fuzzy。
        供 eval 按来源分桶统计"种子解析质量"(不对外暴露物理细节, 仅服务端诊断)。
        """
        return dict(self._res_trace)

    def physical_columns(self) -> set[str]:
        return set(self._load_phys())

    def _load_phys(self) -> dict[str, dict[str, Any]]:
        """物理列名 -> {data_type, business_desc}(供类型判定与中文业务名索引)。"""
        if self._phys is None:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT column_name, data_type, "
                            "COALESCE(NULLIF(business_desc, ''), column_comment) AS business_desc "
                            "FROM adh_column_metadata "
                            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
                            (self.binding.datasource_id, self.binding.physical_table),
                        )
                    except Exception:
                        # 旧库无 business_desc/column_comment 列时回落
                        cur.execute(
                            "SELECT column_name, data_type, '' AS business_desc "
                            "FROM adh_column_metadata "
                            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
                            (self.binding.datasource_id, self.binding.physical_table),
                        )
                    self._phys = {r["column_name"]: r for r in cur.fetchall()}
            finally:
                conn.close()
        return self._phys

    @staticmethod
    def _parse_json(v: Any, fallback):
        if isinstance(v, (str, bytes)):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return fallback
        return v if v is not None else fallback

    def _load_metrics(self) -> dict[str, dict[str, Any]]:
        if self._metrics is None:
            self._metrics = {}
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    extra = self._extra_cols(cur, 'adh_metrics',
                                             ('formula_dialects', 'aliases'))
                    cur.execute(
                        "SELECT name, name_en, formula, formula_dsl, agg_type, default_agg, unit, "
                        "       description"
                        + (", formula_dialects" if 'formula_dialects' in extra else '')
                        + (", aliases" if 'aliases' in extra else '')
                        + " FROM adh_metrics WHERE is_active = 1 AND target_table = %s",
                        (self.binding.physical_table,),
                    )
                    rows = cur.fetchall()
                for r in rows:
                    r["formula_dialects"] = self._parse_json(r.get("formula_dialects"), None)
                    r["aliases"] = self._parse_json(r.get("aliases"), [])
                self._metrics = {r["name"]: r for r in rows}
                # name_en 也接受
                for r in list(self._metrics.values()):
                    if r.get("name_en"):
                        self._metrics.setdefault(r["name_en"], r)
            finally:
                conn.close()
        return self._metrics

    @staticmethod
    def _extra_cols(cur, table: str, candidates: tuple) -> set[str]:
        """探测可选扩展列是否存在(旧库未迁移时自动降级)。"""
        try:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                f"AND COLUMN_NAME IN ({', '.join(['%s'] * len(candidates))})",
                (table, *candidates),
            )
            return {r["COLUMN_NAME"] for r in cur.fetchall()}
        except Exception:  # noqa: BLE001 — 探测失败视为无扩展列
            return set()

    def _load_dims(self) -> dict[str, dict[str, Any]]:
        if self._dims is None:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT name, name_en, target_column, category, "
                            "       aliases, value_labels FROM adh_dimensions "
                            "WHERE is_active = 1 AND target_table = %s ORDER BY id ASC",
                            (self.binding.physical_table,),
                        )
                    except Exception:
                        # aliases/value_labels 为字典增强列; 迁移未执行时回落
                        cur.execute(
                            "SELECT name, name_en, target_column, category, "
                            "       NULL AS aliases, NULL AS value_labels FROM adh_dimensions "
                            "WHERE is_active = 1 AND target_table = %s ORDER BY id ASC",
                            (self.binding.physical_table,),
                        )
                    rows = cur.fetchall()
                for r in rows:
                    r["aliases"] = self._parse_json(r.get("aliases"), [])
                    r["value_labels"] = {str(k): str(v) for k, v in
                                         (self._parse_json(r.get("value_labels"), {}) or {}).items()}
                self._dims = {r["name"]: r for r in rows}
                for r in list(self._dims.values()):
                    if r.get("name_en"):
                        self._dims.setdefault(r["name_en"], r)
            finally:
                conn.close()
        return self._dims

    # ── 统一别名索引 ──────────────────────────────────────

    def _dim_index(self) -> dict[str, dict[str, Any]]:
        """norm(任意写法) -> {col, time, labels, name, kind}。

        kind: 'dim'=字典行(可展示给用户) / 'phys'=物理列(严禁出现在提示文案)。
        """
        if self._index is None:
            idx: dict[str, dict[str, Any]] = {}
            phys = self._load_phys()

            def add(key: str, entry: dict, origin: str, prefer: bool = False):
                k = _norm_name(key)
                if not k:
                    return
                if prefer or k not in idx:
                    idx[k] = entry
                    self._dim_origin[k] = origin

            # 1) 物理列本体名(最低优先, 可被字典覆盖)
            for col, meta in phys.items():
                dt = str(meta.get("data_type") or "").split("(")[0].lower()
                add(col, {"col": col, "time": dt in self._TIME_TYPES,
                          "labels": {}, "name": col, "kind": "phys"}, "phys_col")
            # 2) 字典行: name/name_en/aliases 全部入索引; 字典优先于物理同名列
            for _k, d in self._load_dims().items():
                col = d.get("target_column") or ""
                if not col:
                    continue
                pm = phys.get(col) or {}
                dt = str(pm.get("data_type") or "").split("(")[0].lower()
                entry = {
                    "col": col,
                    "time": (d.get("category") or "") == "时间" or dt in self._TIME_TYPES,
                    "labels": d.get("value_labels") or {},
                    "name": d["name"], "kind": "dim",
                }
                add(d["name"], entry, "dict_exact", prefer=True)
                if d.get("name_en"):
                    add(d["name_en"], entry, "dict_exact", prefer=True)
                for a in (d.get("aliases") or []):
                    add(a, entry, "dict_alias", prefer=True)
            # 3) 物理列中文业务名(business_desc) → 指回物理列(不对外展示)
            for col, meta in phys.items():
                bd = str(meta.get("business_desc") or "").strip()
                if bd:
                    e = idx.get(_norm_name(col))
                    if e and e["kind"] == "dim":
                        continue  # 该列已登记为字典维度, 中文业务名指向字典条目
                    add(bd, {"col": col, "time": str(meta.get("data_type") or "")
                             .split("(")[0].lower() in self._TIME_TYPES,
                             "labels": {}, "name": col, "kind": "phys"}, "physical_desc")
            # 4) binding.column_map(本体属性名 → 物理列)
            for alias, col in (self.binding.column_map or {}).items():
                if col:
                    e = idx.get(_norm_name(col)) or {}
                    add(alias, {"col": col, "time": bool(e.get("time")),
                                "labels": e.get("labels") or {},
                                "name": e.get("name") if e.get("kind") == "dim" else col,
                                "kind": e.get("kind") or "phys"}, "column_map")
            self._index = idx
        return self._index

    def lookup_dimension(self, name: str) -> Optional[dict[str, Any]]:
        """任意写法 → 维度条目; 仅精确/别名/列映射/业务名命中才绑定。

        包含式模糊命中不再静默绑定(T5): 只记入 _fuzzy_candidates 供 plan() 回抛候选,
        错绑产生的静默错数比"回抛后多一轮重试"危害大得多(宁缺勿错)。
        """
        idx = self._dim_index()
        n = _norm_name(name)
        if not n:
            return None
        hit = idx.get(n)
        if hit:
            self._res_trace[name] = self._dim_origin.get(
                n, "dict_exact" if hit.get("kind") == "dim" else "phys_col")
            return hit
        # 包含式模糊("X包含Y"或"Y包含X"): 不绑定, 回抛业务名候选
        cand_keys = [k for k in idx if (n in k or k in n)]
        if cand_keys:
            self._res_trace[name] = "fuzzy_rejected"
            labels = sorted({
                idx[k]["name"] if idx[k]["kind"] == "dim" else k
                for k in cand_keys
                if self._dim_origin.get(k) in ("dict_exact", "dict_alias",
                                               "physical_desc", "column_map")
            })
            if labels:
                self._fuzzy_candidates[name] = labels
        return None

    def suggest_dimensions(self, limit: int = 10) -> list[str]:
        """对象可用维度提示(只列字典名+主要别名, 不暴露物理列)。"""
        out: list[str] = []
        seen: set[str] = set()
        for _k, d in self._load_dims().items():
            if d["name"] in seen or not d.get("target_column"):
                continue
            seen.add(d["name"])
            al = [str(a) for a in (d.get("aliases") or []) if a][:3]
            item = d["name"] + (f"(别名: {'/'.join(al)})" if al else "")
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def available_dimension_names(self) -> list[str]:
        return sorted({d["name"] for d in self._load_dims().values()
                       if d.get("target_column")})

    # ── dimension ─────────────────────────────────────────────
    
    def resolve_dimension(
        self, name: str, time_grain: Optional[str] = None,
    ) -> tuple[Optional[str], str]:
        """返回 SELECT/GROUP BY 用的表达式(枚举维度翻成业务标签 CASE)。"""
        entry = self.lookup_dimension(name)
        if not entry:
            return None, ""
        if entry.get("labels"):
            return self._label_case_expr(entry["col"], entry["labels"]), "dimension"
        if entry.get("time"):
            # time_grain 只对时间类维度生效(枚举/类别维度不受全局粒度影响)
            wrapped = self._maybe_wrap_time(
                entry["col"], time_grain, str(entry.get("name") or name))
            if not wrapped:
                return None, ""
            return wrapped, "time"
        return self._col(entry["col"]), "dimension"
    
    def _label_case_expr(self, col: str, labels: dict[str, str]) -> str:
        """枚举码→业务标签的 CASE 表达式; 未命中码值原样输出(转文本避免混型)。"""
        q = self._col(col)
        whens = " ".join(
            f"WHEN {_lit(str(code))} THEN {_lit(label)}"
            for code, label in sorted(labels.items()))
        if self.dialect == "es":
            # ES 无 `CAST(x AS CHAR)`；keyword 本身已是字符串，原样作 ELSE（实测同一
            # CASE 同时出现在 SELECT 与 GROUP BY 时不混型）
            else_ = q
        elif self.dialect == "postgres":
            else_ = f"{q}::text"
        else:
            else_ = f"CAST({q} AS CHAR)"
        return f"CASE {q} {whens} ELSE {else_} END"
    
    @staticmethod
    def _label_to_code(entry: dict[str, Any], value: Any) -> Any:
        """WHERE 值反查: 业务标签 → 码值; 非标签(含已是码值)原样保留。

        一个业务标签可能对应**多个码值**（如维度「应用分组」把 17 个三方集成码归为
        「三方集成App」这一个桶）：此时必须返回全部码值，由 `_render_condition` 渲染成
        `IN (...)`。只取其中一个码值 = 静默少算（错数比回绝危害大）。
        """
        labels = entry.get("labels") or {}
        if not labels or isinstance(value, dict):
            return value
        rev: dict[str, list[str]] = {}
        for code, label in labels.items():
            rev.setdefault(_norm_name(label), []).append(str(code))
        if isinstance(value, (list, tuple)):
            out: list[Any] = []
            for v in value:
                out.extend(rev.get(_norm_name(v), [v]))
            return out
        codes = rev.get(_norm_name(value))
        if not codes:
            return value
        return codes[0] if len(codes) == 1 else codes
    
    def _maybe_wrap_time(self, col: str, grain: Optional[str],
                         dim_name: str = "") -> str:
        q = self._col(col)
        if self.engine == "es":
            # ES 无 DATE_FORMAT/to_char：时间桶 = 定长字符串切片(探针 2026-11-18：
            # SUBSTRING(CAST("operationTime.keyword" AS VARCHAR),1,7) -> '2026-11')
            if not grain:
                return q
            n = _ES_SUBSTR_GRAIN_LEN.get(grain)
            if n:
                return f"SUBSTRING(CAST({q} AS VARCHAR), 1, {n})"
            if grain == "week":
                # ES 无 ISO 周年-周格式串：取周起始日(周一对齐, 实测 2026-11-16)
                return (f"SUBSTRING(CAST(DATE_TRUNC('WEEK', CAST({q} AS TIMESTAMP)) AS VARCHAR),"
                        " 1, 10)")
            # quarter 等未取证粒度：返回空串让调用方显式回绝(不静默出错误时间轴)
            return ""
        if self.engine == "sls":
            kind = self._sls_time_kind(col)
            if not kind:
                # 时间单位无法安全判定 -> 返回空表达式, 调用方按"未解析"显式回绝
                # (不静默当秒/当原生时间处理, 那会给出错的时间轴)
                return ""
            if kind == "epoch_sec":
                # 未指定粒度时也不能返回裸 epoch 数字(展示成 1793245034 这种值对用户
                # 无意义, 且按它 GROUP BY 等于把「时间」当成一个整数维度)：
                # 先看业务名自带的粒度承诺, 否则取秒级完整时间串。
                grain = grain or _grain_from_dim_name(dim_name)
                t = f"from_unixtime({q})"
                if grain == "quarter":
                    return _SLS_GRAIN_QUARTER.format(t=t)
                fmt = _SLS_GRAIN_FMT.get(grain or "second")
                return f"date_format({t}, '{fmt}')" if fmt else q
            fmt = _SLS_GRAIN_FMT.get(grain)
            return f"date_format({q}, '{fmt}')" if fmt else q
        if not grain:
            return q
        if self.dialect == "postgres":
            fmt = _TIME_GRAIN_FMT_PG.get(grain)
            if not fmt:
                return q
            return f"to_char({q}, '{fmt}')"
        fmt = _TIME_GRAIN_FMT_MYSQL.get(grain)
        if not fmt:
            return q
        return f"DATE_FORMAT({q}, '{fmt}')"

    def _sls_epoch_filter_expr(self, col: str, f: SemanticFilter, raw: str) -> str:
        """SLS epoch 秒时间列上「未指定粒度」的时间过滤：按过滤值的形状决定比较粒度。

        `"__time__" >= '2026-10-01'` 在引擎侧是 bigint vs varchar 类型错误，而把日期
        换算成 epoch 数字又要猜单位(秒/毫秒) —— 改为把列按值粒度分桶后作字符串比较，
        与展示口径同源、不涉及单位猜测。值不全是 ISO 日期/日期时间时原样比较
        (按登记口径当 epoch 秒数值)。其余引擎不受影响。
        """
        if self.engine != "sls" or self._sls_time_kind(col) != "epoch_sec":
            return raw
        samples = [str(v).strip() for v in
                   ([f.value] if f.value is not None else []) + list(f.values or [])
                   if isinstance(v, str)]
        if not samples:
            return raw
        iso = [_ABS_DATE_RE.match(s) for s in samples]
        if not all(iso):
            return raw
        grain = "second" if all(m.group(1) for m in iso) else "day"
        return f"date_format(from_unixtime({raw}), '{_SLS_GRAIN_FMT[grain]}')"
    
    # ── measure ───────────────────────────────────────────────
    
    def resolve_measure(
        self, name: str,
    ) -> tuple[Optional[str], Optional[str], str, bool]:
        """返回 (表达式, agg, 单位, 方言不支持)。支持别名/归一化匹配。"""
        m = self._lookup_metric(name)
        if m:
            expr, unsupported = self._pick_dialect_expr(m)
            if unsupported:
                return None, None, m.get("unit") or "", True
            expr = self._fix_formula(expr)
            agg = (m.get("default_agg") or m.get("agg_type") or "").upper() or None
            return expr or None, agg, m.get("unit") or "", False
        # 兑底: 直接把 name 当物理列 SUM(归一化比较)
        phys = {_norm_name(c): c for c in self._load_phys()}
        col = phys.get(_norm_name(name))
        if col:
            self._res_trace[name] = "phys_col"
            return f"SUM({self._col(col)})", "SUM", "", False
        return None, None, "", False
    
    def _lookup_metric(self, name: str) -> Optional[dict[str, Any]]:
        ms = self._load_metrics()
        m = ms.get(name)
        if m:
            self._res_trace[name] = "dict_exact"
            return m
        n = _norm_name(name)
        for _k, row in ms.items():
            cands = {_norm_name(row["name"]), _norm_name(row.get("name_en") or "")}
            if n in cands:
                self._res_trace[name] = "dict_exact"
                return row
            aliases = {_norm_name(a) for a in (row.get("aliases") or []) if a}
            if n in aliases:
                self._res_trace[name] = "dict_alias"
                return row
        return None
    
    def suggest_metrics(self, limit: int = 10) -> list[str]:
        seen: list[str] = []
        for _k, row in self._load_metrics().items():
            if row["name"] not in seen:
                seen.append(row["name"])
            if len(seen) >= limit:
                break
        return seen
    
    def _pick_dialect_expr(self, m: dict[str, Any]) -> tuple[str, bool]:
        """取式优先级: formula_dialects[引擎] -> [方言族] -> formula_dsl/formula。

        formula_dialects 命中引擎键或族键时: 值为空/null 表示"显式声明不支持本引擎"
        (unsupported=True); 两键都不存在则继续回落通用 formula。
        引擎键优先, 使 {"mysql": null, "doris": "window_funnel(...)"} 只在 Doris 上生效
        —— 历史缺陷: 早期只认族键, 按引擎注册的 Doris 函数成了永不命中的死键。
        """
        expr, unsupported = sem_dialect.pick_variant(
            m.get("formula_dialects"), self.engine, self.dialect)
        if unsupported:
            return "", True
        if expr:
            return expr, False
        return (m.get("formula_dsl") or m.get("formula") or "").strip(), False
    
    # ── filter ──────────────────────────────────────────────
    
    def render_filter(self, f: SemanticFilter) -> Optional[str]:
        entry = self.lookup_dimension(f.dim)
        if not entry:
            return None
        # WHERE 永远打在原始列上(标签只在结果集展示), 值支持业务名→码值反查
        expr = self._col(entry["col"])
        if entry.get("time"):
            if f.grain:
                expr = self._maybe_wrap_time(entry["col"], f.grain)
                if not expr:
                    return None   # 时间粒度无法安全生成 -> 回绝(由调用方出"未解析"告警)
            else:
                expr = self._sls_epoch_filter_expr(entry["col"], f, expr)
        if entry.get("labels"):
            value = self._label_to_code(entry, f.value)
            values = ([self._label_to_code(entry, v) for v in f.values]
                      if f.values is not None else None)
            if values is not None:
                # 单个值反查可能出多码(见 _label_to_code) → 拍平成一维码值列表
                values = [c for v in values
                          for c in (v if isinstance(v, (list, tuple)) else [v])]
        else:
            value, values = f.value, f.values
        return _render_condition(expr, f.op, value, values)
    
    # ── 时间列定位 + 时间谓词(相对窗口 / 日历区间 / 绝对区间) ──────

    def _sls_time_kind(self, col: str) -> str:
        """SLS 时间列取值形态: 'epoch_sec' / 'native' / ''(判定不了 → 不猜)。"""
        bare = str(col or "").strip().strip('"').strip("`")
        if bare == _SLS_EPOCH_SEC_COLUMN:
            return "epoch_sec"
        meta = self._load_phys().get(bare) or {}
        dt = str(meta.get("data_type") or "").split("(")[0].lower()
        if dt in self._TIME_TYPES:
            return "native"
        # bigint/int 类(如业务侧毫秒时间戳) 或元数据未登记: 单位未知, 不猜
        return ""

    def _es_time_kind(self, col: str) -> str:
        """ES 时间列取值形态: 'date'(datetime 型) / 'text'(可直接字符串比较)。

        实机（2026-11-18）：datetime 型列与裸字符串字面量比较直接 400
        （`"@timestamp" >= '2026-11-18 00:00:00'` -> parse_exception），而 keyword/text
        列（`operationTime.keyword` 存 'YYYY-MM-DD HH:MM:SS'）可字符串比较与切片。
        边界渲染按本形态分流：date 先 CAST 成 VARCHAR 再与 ISO-T 串比较。
        """
        bare = str(col or "").strip().strip('"').strip("`")
        meta = self._load_phys().get(bare) or {}
        dt = str(meta.get("data_type") or "").split("(")[0].lower()
        return "date" if dt in _ES_DATE_TYPES else "text"

    @staticmethod
    def _es_cmp_expr(col_expr: str, kind: str) -> str:
        """ES 时间比较左端。

        两种列型都不包转换，直接在列上比较（包 `CAST(列 AS VARCHAR)` 会让索引条件下沉
        失效，2.8 亿文档的索引会直接超时）：
        - text 列（'YYYY-MM-DD HH:MM:SS' 字符串）与同形字符串字面量比较；
        - date 列与 `CAST('...' AS TIMESTAMP)` 比较（裸字符串字面量在 ES 上 400）。
        """
        return col_expr

    @staticmethod
    def _es_bound(kind: str, value: str) -> str:
        """ES 时间边界字面量：date 列必须 CAST 成 TIMESTAMP（裸字符串比较实机 400）。"""
        v = str(value)
        if kind == "date":
            return f"CAST({_lit(v)} AS TIMESTAMP)"
        return _lit(v)

    def _resolve_time_column_name(
        self, time_column: Optional[str], warnings: list[str],
    ) -> str:
        """定位事件时间列名: 显式 time_column(走别名解析) -> 字典时间维度 -> 唯一时间类物理列。"""
        if time_column:
            entry = self.lookup_dimension(time_column)
            if entry is None:
                warnings.append(
                    f"时间维度「{time_column}」无法解析, 可用时间维度: "
                    f"{self.suggest_time_dimensions() or '无'}")
                return ""
            return str(entry["col"] or "")
        for d in self._load_dims().values():
            if (d.get("category") or "") == "时间" and d.get("target_column"):
                return str(d["target_column"])
        tcols = [c for c, meta in self._load_phys().items()
                 if str(meta.get("data_type") or "").split("(")[0].lower()
                 in self._TIME_TYPES]
        if len(tcols) == 1:
            return str(tcols[0])
        warnings.append(
            "时间条件需要显式 time_column: 本对象未登记时间类维度"
            f"(可用维度: {self.suggest_dimensions()})")
        return ""

    def _resolve_time_col_expr(
        self, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """(兼容入口)时间列引用表达式; 只需列名的调用方请用 `_resolve_time_column_name`。"""
        col = self._resolve_time_column_name(time_column, warnings)
        return self._col(col) if col else None

    def _time_target(
        self, time_column: Optional[str], warnings: list[str],
    ) -> tuple[Optional[str], str]:
        """时间谓词的左端 -> (引用表达式, 取值形态); 不能安全定域时 (None, '')。

        仅 SLS 需要第 2 项：LogStore 内置事件时间列是 epoch 秒(可与 to_unixtime 同型
        比较), 而业务侧同名时间字段实测存的是 epoch 毫秒且元数据不声明单位 —— 拿它
        当秒比较等于把时间窗静默放大到全表(错数), 因此宁可在编译期显式回绝。
        文案只说业务名/登记口径, 不输物理列名(护栏 §7)。
        """
        col = self._resolve_time_column_name(time_column, warnings)
        if not col:
            return None, ""
        q = self._col(col)
        if self.engine == "es":
            # datetime 型列与裸字符串比较在 ES 上直接 400(实机)，故按列型带回取值形态：
            # 'date' 的边界渲染/值比较要先 CAST 成 VARCHAR 再比。
            return q, self._es_time_kind(col)
        if self.engine != "sls":
            return q, ""
        kind = self._sls_time_kind(col)
        if not kind:
            warnings.append(
                f"时间维度「{time_column or self._time_dim_label(col)}」的时间单位无法确定, "
                "已拒绝生成时间条件(不静默猜单位): 日志服务类数据源请将时间维登记到"
                "内置事件时间列(按秒存), 业务自定义的毫秒时间戳列需先在字典里换为可读粒度")
            return None, ""
        return q, kind

    def _time_dim_label(self, col: str) -> str:
        """时间列对应的业务名(提示文案用, 不输出物理列名)。"""
        for _k, d in self._load_dims().items():
            if str(d.get("target_column") or "").strip() == str(col or "").strip():
                return str(d.get("name") or col)
        return str(col or "")

    def render_time_window(
        self, window: str, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """把 time_window('7d' 等) 编译为滚动区间谓词; 无法定位时间列时告警并跳过。"""
        mm = _WINDOW_RE.match(str(window or "").strip())
        if not mm:
            warnings.append(f"invalid time_window: {window!r} (expect e.g. '7d'/'24h'/'2w'/'1M')")
            return None
        n, unit_key = int(mm.group(1)), mm.group(2)
        my_unit, pg_unit = _WINDOW_UNIT[unit_key]
        col_expr, kind = self._time_target(time_column, warnings)
        if col_expr is None:
            return None
        if self.engine == "es":
            # 边界在服务端算好再下推（ES 无 DATE_SUB/INTERVAL，也不拿引擎时钟当时区基准）
            lhs = self._es_cmp_expr(col_expr, kind)
            return f"{lhs} >= {self._es_bound(kind, _es_window_start(n, unit_key))}"
        if self.engine == "sls" and kind == "epoch_sec":
            # 边界包 to_unixtime 与 epoch 秒同型(探针 Q1 ERR/S1 OK), INTERVAL 单位写在字符串里(G ERR/H OK)
            return f"{col_expr} >= to_unixtime(now() - INTERVAL '{n} {pg_unit}')"
        if self.dialect == "postgres":
            return f"{col_expr} >= now() - INTERVAL '{n} {pg_unit}'"
        return f"{col_expr} >= DATE_SUB(NOW(), INTERVAL {n} {my_unit})"

    def render_time_range(
        self, rng: str, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """把日历 time_range('last_week' 等) 编译为左闭右开区间谓词(col >= 起 AND col < 止)。"""
        key = str(rng or "").strip()
        if self.engine == "es":
            bounds = _es_calendar_range(key)
        elif self.engine == "sls":
            bounds = _SLS_RANGE.get(key)
        else:
            bounds = (_PG_RANGE if self.dialect == "postgres" else _MY_RANGE).get(key)
        if not bounds:
            warnings.append(f"unknown time_range: {rng!r} (expect 'last_week'/'this_month'/...)")
            return None
        col_expr, kind = self._time_target(time_column, warnings)
        if col_expr is None:
            return None
        start, end = bounds
        if self.engine == "es":
            lhs = self._es_cmp_expr(col_expr, kind)
            return (f"{lhs} >= {self._es_bound(kind, start + ' 00:00:00')} AND "
                    f"{lhs} < {self._es_bound(kind, end + ' 00:00:00')}")
        if self.engine == "sls" and kind == "epoch_sec":
            # epoch 秒列与日期表达式不同型, 边界取 unix 秒(T1/U4/U6/U7 均为 to_unixtime(...) OK)
            start, end = f"to_unixtime({start})", f"to_unixtime({end})"
        return f"{col_expr} >= {start} AND {col_expr} < {end}"

    def render_time_bounds(
        self, start: Optional[str], end: Optional[str],
        time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """绝对区间兜底: time_start/time_end(严格 ISO, 服务端单一时钟) 编译为闭区间谓词。"""
        s, e = str(start or "").strip(), str(end or "").strip()
        for v in (s, e):
            if v and not _ABS_DATE_RE.match(v):
                warnings.append(f"invalid time bound: {v!r} (expect ISO date/datetime)")
                return None
        if not s and not e:
            return None
        col_expr, kind = self._time_target(time_column, warnings)
        if col_expr is None:
            return None

        if self.engine == "es":
            # ES：纯日期右端按左闭右开取次日零点（字符串形态下 `<= 当日` 会把当天
            # 00:00 之后的记录全漏掉；`CONCAT(end,'~')` 那种花招不可维护，不用）
            lhs = self._es_cmp_expr(col_expr, kind)
            parts: list[str] = []
            if s:
                parts.append(f"{lhs} >= {self._es_bound(kind, s if len(s) > 10 else s + ' 00:00:00')}")
            if e:
                if len(e) == 10:
                    nxt = (date.fromisoformat(e) + timedelta(days=1)).isoformat()
                    parts.append(f"{lhs} < {self._es_bound(kind, nxt + ' 00:00:00')}")
                else:
                    parts.append(f"{lhs} <= {self._es_bound(kind, e)}")
            return " AND ".join(parts) if parts else None

        def _bound(v: str) -> str:
            if self.engine == "sls":
                # 裸字符串与 bigint 列比较直接报错(T7 ERR); epoch 秒列还要过 to_unixtime(T6 OK)
                ts = f"timestamp '{v}'"
                return f"to_unixtime({ts})" if kind == "epoch_sec" else ts
            return _lit(v)

        parts: list[str] = []
        if s:
            parts.append(f"{col_expr} >= {_bound(s)}")
        if e:
            parts.append(f"{col_expr} <= {_bound(e)}")
        return " AND ".join(parts)
    
    def suggest_time_dimensions(self) -> list[str]:
        out = []
        for _k, d in self._load_dims().items():
            if (d.get("category") or "") == "时间" and d.get("target_column"):
                out.append(d["name"])
        return sorted(set(out))


# ── 数据集对象编译 (bind_kind='dataset') ──────────────────────────────

def _load_dataset_row(ref: str) -> Optional[dict[str, Any]]:
    """按名/按 id 载数据集行（SQL 形态的"可信任预制查询"）。

    数据集是平台内唯一的预制 SQL 载体：人写 SQL 形状（两级聚合/窗口/自关联），
    参数与输出列由声明约束，编译规则见 `semantics/dataset_compiler.py`。
    """
    key = str(ref or "").strip()
    if not key:
        return None
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if key.isdigit():
                cur.execute("SELECT * FROM adh_datasets WHERE id = %s AND status='active'",
                            (int(key),))
            else:
                cur.execute("SELECT * FROM adh_datasets WHERE name = %s AND status='active'",
                            (key,))
            return cur.fetchone()
    finally:
        conn.close()


def _empty_plan(
    binding: ResolvedBinding, query: SemanticQuery,
    dialect: str, warnings: list[str],
) -> PlannedExecution:
    g = binding.guardrail
    return PlannedExecution(
        sql="", secured_sql="", route=g.query_mode, dialect=dialect,
        datasource_id=binding.datasource_id,
        resolved_tables=[p for p in [binding.physical_table] if p],
        guardrail=g, access=binding.access,
        warnings=warnings, provenance=_provenance(binding, query),
    )


def _dataset_has_scopes(dataset_id: int) -> bool:
    """该数据集是否配了行级范围（直读元库，不在 L1 反向依赖 modules/viz）。"""
    if not dataset_id:
        return False
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM adh_dataset_scopes WHERE dataset_id = %s",
                        (dataset_id,))
            row = cur.fetchone() or {}
        return int(row.get("n") or 0) > 0
    except Exception:  # noqa: BLE001 — 旧库无该表 = 无 scope 可用，不影响主链路
        return False
    finally:
        conn.close()


def _load_datasource_rows(names: Optional[list[str]] = None,
                          exec_id: int = 0) -> list[dict]:
    """数据源清单行 [{id, name}]（按业务名/id 查重载，供可用范围解析）。

    单独成一个函数是为了离线可打桩（评测侧 demo 用内存清单，不碰元库）。
    """
    want = [str(x or "").strip() for x in (names or []) if str(x or "").strip()]
    rows: list[dict] = []
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if want:
                cur.execute(
                    f"SELECT id, name FROM adh_datasources WHERE name IN ({','.join(['%s'] * len(want))})",
                    tuple(want))
                rows.extend(cur.fetchall() or [])
            if int(exec_id or 0):
                cur.execute("SELECT id, name FROM adh_datasources WHERE id = %s", (exec_id,))
                rows.extend([cur.fetchone()] if cur.fetchone() else [])
    finally:
        conn.close()
    return [{"id": int(r["id"]), "name": str(r.get("name") or "")} for r in rows if r]


def _dataset_source_scope(ds: dict) -> tuple[set[int], list[str]]:
    """数据集可用数据源：(id 集, 业务名清单) = 执行源 ∪ available_datasources(按名解析)。

    直读元库（不在 L1 反向依赖 modules/viz），与 `dataset_service.usable_sources` 同口径：
    解析不到 id 的源名（已删/改名）不计入 id 集，fail-closed（宁可拒绘计划，不可静默换源）；
    但必须出现在业务名清单里并标注失效（no-silent-degradation，否则永远看不出为何被拒）。
    默认引擎(0) 计入（不参与源级 RBAC）；未配范围 = 仅执行源（存量行为零变化）。
    文案只给业务名，绝不外抛内部 id（护栏 §7）。
    """
    raw = parse_json_obj(ds.get("available_datasources"), []) or []
    names = list(dict.fromkeys(str(x or "").strip() for x in raw if str(x or "").strip()))
    exec_id = int(ds.get("datasource_id") or 0)
    rows = _load_datasource_rows(names, exec_id=exec_id)
    by_name = {r["name"]: r["id"] for r in rows if r["name"]}
    exec_row = next((r for r in rows if r["id"] == exec_id), {}) if exec_id else {}

    ids: set[int] = set()
    labels: list[str] = []
    if not exec_id:
        ids.add(0)
        labels.append("默认引擎")
    else:
        ids.add(exec_id)
        labels.append(str(exec_row.get("name") or "执行数据源(已不存在)"))
    for nm in names:
        sid = by_name.get(nm)
        if sid is None:
            labels.append(f"{nm}(已失效)")
            continue
        ids.add(int(sid))
        if nm not in labels:
            labels.append(nm)
    return ids, labels


def _plan_dataset(
    query: SemanticQuery, binding: ResolvedBinding, dialect: str, warnings: list[str],
) -> PlannedExecution:
    """数据集对象编译: 载数据集 -> 委托 dataset_compiler 编译(参数/方言正文/输出列聚合)。

    数据集是平台内唯一的"可信任预制 SQL"载体: 语义层编译不出的两级聚合/窗口/自关联
    由数据集正文承担, 参数与输出列由声明约束(见 semantics/dataset_compiler.py)。
    编译被拒(未声明参数/无本方言正文)一律 fail-loud 返回空计划 + 原因, 不静默换通道。
    """
    from backend.semantics.dataset_compiler import DatasetCompileError, compile_dataset

    g = binding.guardrail
    ref = str(binding.dataset_ref or "").strip()
    if not ref:
        warnings.append("dataset binding missing dataset_ref")
        return _empty_plan(binding, query, dialect, warnings)
    ds = _load_dataset_row(ref)
    if not ds:
        warnings.append(f"数据集 '{ref}' 不存在或已停用")
        return _empty_plan(binding, query, dialect, warnings)

    # ES 执行通道的数据集不能当语义层对象绑定用：正文是 ES SQL（无派生表、sqlglot 无 ES 方言），
    # 拿它按目标源方言二次编译会报错或错数（宁阻断勿错数），取数只走 query_dataset。
    # 判据只用执行源 db_type（L0 `datasource_db.is_es_datasource`，与取数分派同一口径）：
    # 旧写法看 `source_type='es'/es_pattern`（内置通道行形状），那种行已退役 → 这道门会静默失效。
    # 注：ES 数据集本身**在**治理域内（列级/行级编译进 SQL），不在治理域的是“把它归到
    # 目标关系源上二次编译”这件事，文案不得再讲旧那套“通道型不在数据权限治理域”的口径。
    from backend.common.db.datasource_db import is_es_datasource

    if is_es_datasource(int(ds.get("datasource_id") or 0)):
        warnings.append(
            f"数据集 '{ds.get('name') or ref}' 的执行源是 Elasticsearch 数据源，"
            "不能作为语义层对象绑定使用（ES SQL 无派生表，不能按目标源方言二次编译）；"
            "请经 query_dataset 取该数据集，或为同一业务口径另建一个挂关系型数据源的数据集")
        return _empty_plan(binding, query, dialect, warnings)

    # 数据集行级范围(scope)只在 query_dataset 通道施加（它拿得到身份）；
    # 语义层绑定不施加 → 必须拒绝而不是静默少一层过滤（护栏 §2 fail-closed）
    if _dataset_has_scopes(int(ds.get("id") or 0)):
        warnings.append(
            f"数据集 '{ds.get('name') or ref}' 配了行级范围(scope)，不能作为语义层对象绑定使用；"
            "请改用 query_dataset 取数（会按身份叠加 scope），或先移除该数据集的 scope")
        return _empty_plan(binding, query, dialect, warnings)

    # 站点路由后的目标源必须落在数据集可用范围内（与本体 deploy_sites 同构约束）：
    # 一份预制 SQL 只在它声明过的源上可信，静默拿另一个源跑 = 口径漂移（表不存在也可能是
    # 错数），一律 fail-loud 空计划，不得自行换源或回退到执行源。
    target_ds = int(binding.datasource_id or 0)
    scope_ids, scope_labels = _dataset_source_scope(ds)
    if target_ds not in scope_ids:
        warnings.append(
            f"数据集 '{ds.get('name') or ref}' 不适用当前目标数据源；已适用："
            + "、".join(scope_labels or ["无"])
            + "（请在数据集页把该源登记进「可用数据源范围」，或改绑适用的数据集）")
        return _empty_plan(binding, query, dialect, warnings)

    declared = parse_variables(ds.get("variables"))
    known = declared_names(str(ds.get("sql_query") or ""), declared) | \
        {n for b in (parse_json_obj(ds.get("sql_bodies"), {}) or {}).values()
           for n in placeholder_names(str(b or ""))}
    args: dict[str, Any] = dict(query.params or {})
    # filters 语法糖: 与参数同名的 filter 折算为参数值(正文里没这个列, 不能当谓词下推)
    for f in query.filters:
        if f.dim in known and f.dim not in args:
            args[f.dim] = (f.values
                           if f.op in ("in", "nin", "between") and f.values
                           else f.value)
    if query.time_window or query.time_range or query.time_start or query.time_end:
        warnings.append(
            "数据集对象忽略 time_window/time_range/time_start/time_end: "
            "时间窗请用 params 传数据集声明的时间参数(否则口径与正文里写死的窗口不一致)")

    try:
        compiled = compile_dataset(
            ds,
            args=args,
            dimensions=query.dimensions,
            measures=query.metrics,
            filters=[{"field": f.dim, "op": f.op, "value": f.value, "values": f.values}
                     for f in query.filters],
            order=[{"by": o.by, "desc": o.desc} for o in query.order],
            limit=query.limit if query.limit is not None else (g.max_rows or None),
            time_grain=query.time_grain,
            db_type=binding.db_type,
        )
    except DatasetCompileError as e:
        warnings.append(str(e))
        return _empty_plan(binding, query, dialect, warnings)

    warnings.extend(compiled.warnings)
    if g.force_limit and query.limit is None:
        warnings.append(f"guardrail: force_limit applied ({g.max_rows or _DEFAULT_LIMIT})")

    provenance = _provenance(binding, query)
    provenance["dataset_ref"] = str(ds.get("name") or ref)
    provenance["dataset_id"] = int(ds.get("id") or 0)
    provenance["dataset_certified"] = bool(ds.get("certified") or 0)
    if compiled.resolution_sources:
        provenance["resolution_sources"] = compiled.resolution_sources
    if compiled.unresolved:
        provenance["unresolved_terms"] = compiled.unresolved
    return PlannedExecution(
        sql=compiled.sql,
        secured_sql=compiled.sql,
        dialect=dialect,
        route=g.query_mode,
        datasource_id=binding.datasource_id,
        resolved_tables=list(compiled.resolved_tables),
        guardrail=g, access=binding.access,
        provenance=provenance,
        warnings=warnings,
        # SLS/Presto 类引擎下 SQL 别名为 ASCII 槽位，需据此映回业务列名（其他引擎为空）。
        output_header_map=dict(compiled.header_map),
    )


def _render_condition(lhs: str, op: str, value: Any, values: Optional[list[Any]]) -> Optional[str]:
    lit = _lit

    if op == "eq":
        # 业务标签反查出多个码值时，等值语义等价于 IN（不得只取首个码值）
        if isinstance(value, (list, tuple)):
            return (f"{lhs} IN (" + ", ".join(lit(v) for v in value) + ")") if value else None
        return f"{lhs} = {lit(value)}"
    if op == "ne":
        if isinstance(value, (list, tuple)):
            return (f"{lhs} NOT IN (" + ", ".join(lit(v) for v in value) + ")") if value else None
        return f"{lhs} <> {lit(value)}"
    if op == "gt":       return f"{lhs} > {lit(value)}"
    if op == "gte":      return f"{lhs} >= {lit(value)}"
    if op == "lt":       return f"{lhs} < {lit(value)}"
    if op == "lte":      return f"{lhs} <= {lit(value)}"
    if op == "like":     return f"{lhs} LIKE {lit(value)}"
    if op == "is_null":  return f"{lhs} IS NULL"
    if op == "is_not_null": return f"{lhs} IS NOT NULL"
    if op == "in":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr: return None
        return f"{lhs} IN (" + ", ".join(lit(v) for v in arr) + ")"
    if op == "nin":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr: return None
        return f"{lhs} NOT IN (" + ", ".join(lit(v) for v in arr) + ")"
    if op == "between":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr or len(arr) < 2: return None
        return f"{lhs} BETWEEN {lit(arr[0])} AND {lit(arr[1])}"
    return None


# ── provenance ──────────────────────────────────────────────────

def _provenance(b: ResolvedBinding, q: SemanticQuery) -> dict[str, Any]:
    return {
        "object": b.object_key,
        "model_id": b.model_id,
        "datasource_id": b.datasource_id,
        "binding_source": b.source,
        "sync_state": b.sync_state,
        "catalog_ref": b.catalog_ref,
        "route": b.guardrail.query_mode,
        "size_class": b.guardrail.size_class,
        "allow_full_scan": b.guardrail.allow_full_scan,
        "requested_metrics": q.metrics,
        "requested_dimensions": q.dimensions,
    }
