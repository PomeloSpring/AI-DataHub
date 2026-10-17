"""Planner — 把 SemanticQuery + ResolvedBinding 编译成 PlannedExecution。

职责(确定性、无 LLM):
  ① 选路:按 binding.guardrail.query_mode -> materialized / federated / raw_source
  ② 编译基线 SQL:SELECT cols/measures FROM table WHERE ... GROUP BY ... ORDER ... LIMIT
  ③ 护栏:huge 且 allow_full_scan=False 时, 无过滤/无 LIMIT -> 直接拒绝;
          force_limit 生效时, 若未指定 LIMIT -> 补默认值
  ④ provenance / warnings 输出, 供 Playground 与审计面板展示

跨对象与窗口(Phase 2「Link 参与编译」):
  - 限定业务名(对象名.业务名)引用跨对象字段, join 路径从 canonical links[]确定性
    解出(≤2 跳, 无路径回抛候选不猜 join), 产出 LEFT JOIN 链;
  - 结构化窗口模式(window 字段)编译为"内层聚合 + 外层窗口"两层结构;
    窗口表达式仅进 SELECT 投影层; 自由形状 SQL 不在本层(走模板对象/nl2sql)。

方言两级口径见 `backend/semantics/dialect.py`: 语法骨架按**方言族**(mysql/postgres)编译,
函数实现按**引擎**(mysql/doris/postgres/sls)取变体 —— 指标 `formula_dialects` 与模板
`sql_bodies` 共用同一套 engine → family → 通用回落规则。
"""

from __future__ import annotations

import json
import logging
import re
import time
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


def _dialect_of(db_type: str) -> str:
    """数据源 db_type -> SQL 编译方言族。未知类型保守回落 mysql(与旧行为一致)并出 warning。"""
    return sem_dialect.family_of(db_type)


# ── 跨对象 JOIN（Phase 2「Link 参与编译」）────────────────────────
# LLM 只用限定业务名(对象名.业务名)引用跨对象字段; join 路径由语义层从
# canonical links[] 确定性解出(≤2 跳), 无路径回抛候选, 不猜 join(宁缺勿错)。

_JOIN_MAX_HOPS = 2
_links_cache: dict[int, tuple[float, dict]] = {}
_LINKS_TTL_SEC = 60.0


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


def _link_chain(edges: dict[str, list[tuple[str, str]]], src: str, dst: str,
                max_hops: int = _JOIN_MAX_HOPS) -> Optional[list[tuple[str, str]]]:
    """BFS 求 src→dst 关系链(≤max_hops 跳); 无路径 None(回抛, 不猜 join)。"""
    if src == dst:
        return []
    frontier = [(src, [])]
    seen = {src}
    for _ in range(max_hops):
        nxt: list[tuple[str, list]] = []
        for node, path in frontier:
            for tgt, join in edges.get(node, []):
                if not join:
                    continue        # 语义关系(无物理 join)不构成 SQL 链(宁缺勿错)
                if tgt == dst:
                    return path + [(tgt, join)]
                if tgt not in seen:
                    seen.add(tgt)
                    nxt.append((tgt, path + [(tgt, join)]))
        frontier = nxt
    return None


def _resolve_foreign_binding(obj_key: str, datasource_id: int):
    """跨对象 binding 解析(与主对象同一裁决源 binding_resolver)。"""
    from backend.semantics.binding_resolver import resolve_binding
    binding, _warns = resolve_binding(obj_key, datasource_id=datasource_id)
    return binding


# ── public API ───────────────────────────────────────────────────

def plan(query: SemanticQuery, binding: ResolvedBinding) -> PlannedExecution:
    """编译 SemanticQuery -> PlannedExecution。

    失败(违反护栏)时, warnings 非空且 sql 为空;调用方据此拒绝执行。
    """
    warnings: list[str] = []
    g = binding.guardrail
    dialect = _dialect_of(binding.db_type)
    if not sem_dialect.is_known_db_type(binding.db_type):
        warnings.append(
            f"unknown db_type '{binding.db_type}', compiled with mysql dialect fallback")

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

    # ①.5 跨对象引用: 限定业务名(对象名.业务名) -> links 解链 JOIN(≤2 跳)
    join_ctx: dict[str, tuple[_ColumnResolver, str]] = {}   # obj_ref -> (resolver, alias)
    join_parts: list[str] = []
    alias_by_obj: dict[str, str] = {}          # obj_key -> tN 别名(确定性首现序)
    fb_by_obj: dict[str, ResolvedBinding] = {}
    resolve_failed: set[str] = set()           # 无路径/无绑定的 obj_ref(名字直接跳过)
    needed: list[str] = []
    for name in [*query.dimensions, *query.metrics,
                 *[f.dim for f in query.filters],
                 *[o.by for o in query.order],
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

    select_items: list[tuple[str, str]] = []   # (expr, alias)
    group_by: list[str] = []
    col_roles: dict[str, str] = {}
    unresolved: list[dict] = []   # 结构化未解析项(供别名回流队列, 只含业务词)

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
            warnings.append(
                f"无法解析维度「{d}」, 已跳过。{hint or f'本对象可用维度: {r.suggest_dimensions()}; 请用字典名或其别名重试'}")
            unresolved.append({
                "term": d, "type": "dimension",
                "reason": r.resolution_sources().get(d_rest) or "unresolved",
                "candidates": r.fuzzy_candidates(d_rest),
            })
            continue
        alias = _alias_of(d)
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
            warnings.append(
                f"无法解析指标「{m}」, 已跳过。本对象可用指标: "
                f"{r.suggest_metrics()}; 请用字典名或其别名重试")
            unresolved.append({
                "term": m, "type": "metric", "reason": "unresolved", "candidates": [],
            })
            continue
        alias = _alias_of(m)
        select_items.append((expr, alias))
        col_roles[alias] = "measure"
        # 附带单位与聚合信息供溯源
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

    # ④ ORDER BY
    order_parts: list[str] = []
    for o in query.order:
        key = o.by if o.by in col_roles else _alias_of(o.by)
        if key in col_roles:
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

    table_ref = _exec_table_ref(binding, dialect)
    if join_parts:
        table_ref = " ".join([table_ref, *join_parts])

    if query.window:
        win_sql = _render_window_sql(
            query.window, table_ref, select_items, where_parts, group_by,
            order_parts, limit_val, dialect, col_roles, warnings)
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
            order_parts=order_parts,
            limit=limit_val,
            dialect=dialect,
        )

    prov = _provenance(binding, query)
    # 服务端诊断用(工具侧 provenance 已剥离): 供 gates/Playground 在解析失败时给出候选
    prov["available_dimensions"] = resolver.available_dimension_names()
    # 归因: 每个请求名字的解析来源(dict_exact/dict_alias/column_map/physical_desc/phys_col/fuzzy_rejected)
    combined_sources = dict(resolver.resolution_sources())
    for _n in [*query.dimensions, *query.metrics, *[f.dim for f in query.filters]]:
        _o, _rest = _split_qualified(_n)
        if _o and _o in join_ctx:
            got = join_ctx[_o][0]._res_trace.get(_rest)
            if got:
                combined_sources[_n] = got
    prov["resolution_sources"] = combined_sources
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
) -> Optional[str]:
    """结构化窗口 -> "内层聚合 + 外层窗口" 两层结构(窗口与 GROUP BY 不同层)。

    partition←dimensions(rolling/period_compare 剔除时间维)、order←order[];
    窗口表达式仅进 SELECT 投影层。配置非法返回 None 并记 warnings(宁缺勿错)。
    """
    metric_aliases = [a for a, r in col_roles.items() if r == "measure"]
    dim_aliases = [a for a, r in col_roles.items() if r != "measure"]
    time_aliases = [a for a, r in col_roles.items() if r == "time"]

    if w.metric:
        target = _alias_of(w.metric)
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

    inner = _render_sql(table_ref, select_items, where_parts, group_by, [], None, dialect)
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
    s = str(name or "").strip()
    if not s:
        return s
    # 含空白/括号/引号/逗号等运算结构的表达式原样透传; 其余(含中文词)一律视为标识符加引号,
    # 否则中文维度别名在 ORDER BY/AS 处裸奔(PG 直接语法错)。
    if not _IDENT_RE.match(s) and _EXPR_CHAR_RE.search(s):
        return s
    if dialect == "postgres":
        return '"' + s.replace('"', '""') + '"'
    return f"`{s}`"


_EXPR_CHAR_RE = re.compile(r"[\s(),;'\".\[}=+*/-]")


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
    """
    if dialect == "postgres":
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
        """列引用: 主表原样; JOIN 侧统一带表别名前缀(源头限定, 不做事后正则)。"""
        q = _quote_ident(col, self.dialect)
        return f"{self._prefix}.{q}" if self._prefix else q

    def _fix_formula(self, expr: Optional[str]) -> Optional[str]:
        """JOIN 侧指标公式文本中的裸列名补表别名(公式不经 _col 路径)。"""
        if not self._prefix or not expr:
            return expr
        cols = (set(self._load_phys().keys())
                | {str(v) for v in (self.binding.column_map or {}).values()})
        return _qualify_bare(expr, self._prefix, cols)

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
            return self._maybe_wrap_time(entry["col"], time_grain), "time"
        return self._col(entry["col"]), "dimension"
    
    def _label_case_expr(self, col: str, labels: dict[str, str]) -> str:
        """枚举码→业务标签的 CASE 表达式; 未命中码值原样输出(转文本避免混型)。"""
        q = self._col(col)
        whens = " ".join(
            f"WHEN {_lit(str(code))} THEN {_lit(label)}"
            for code, label in sorted(labels.items()))
        else_ = f"{q}::text" if self.dialect == "postgres" else f"CAST({q} AS CHAR)"
        return f"CASE {q} {whens} ELSE {else_} END"
    
    @staticmethod
    def _label_to_code(entry: dict[str, Any], value: Any) -> Any:
        """WHERE 值反查: 业务标签 → 码值; 非标签(含已是码值)原样保留。"""
        labels = entry.get("labels") or {}
        if not labels or isinstance(value, (list, dict)):
            return value
        rev = {_norm_name(v): str(k) for k, v in labels.items()}
        return rev.get(_norm_name(value), value)
    
    def _maybe_wrap_time(self, col: str, grain: Optional[str]) -> str:
        q = self._col(col)
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
        if entry.get("time") and f.grain:
            expr = self._maybe_wrap_time(entry["col"], f.grain)
        if entry.get("labels"):
            value = self._label_to_code(entry, f.value)
            values = ([self._label_to_code(entry, v) for v in f.values]
                      if f.values is not None else None)
        else:
            value, values = f.value, f.values
        return _render_condition(expr, f.op, value, values)
    
    # time window (相对滚动窗口)
    
    def _resolve_time_col_expr(
        self, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """定位事件时间列: 显式 time_column(走别名解析) -> 字典时间维度 -> 唯一 datetime 物理列。"""
        if time_column:
            entry = self.lookup_dimension(time_column)
            if entry is None:
                warnings.append(
                    f"时间维度「{time_column}」无法解析, 可用时间维度: "
                    f"{self.suggest_time_dimensions() or '无'}")
                return None
            return self._col(entry["col"])
        for d in self._load_dims().values():
            if (d.get("category") or "") == "时间" and d.get("target_column"):
                return self._col(d["target_column"])
        tcols = [c for c, meta in self._load_phys().items()
                 if str(meta.get("data_type") or "").split("(")[0].lower()
                 in self._TIME_TYPES]
        if len(tcols) == 1:
            return self._col(tcols[0])
        warnings.append(
            "时间条件需要显式 time_column: 本对象未登记时间类维度"
            f"(可用维度: {self.suggest_dimensions()})")
        return None

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
        col_expr = self._resolve_time_col_expr(time_column, warnings)
        if col_expr is None:
            return None
        if self.dialect == "postgres":
            return f"{col_expr} >= now() - INTERVAL '{n} {pg_unit}'"
        return f"{col_expr} >= DATE_SUB(NOW(), INTERVAL {n} {my_unit})"

    def render_time_range(
        self, rng: str, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """把日历 time_range('last_week' 等) 编译为左闭右开区间谓词(col >= 起 AND col < 止)。"""
        table = _PG_RANGE if self.dialect == "postgres" else _MY_RANGE
        bounds = table.get(str(rng or "").strip())
        if not bounds:
            warnings.append(f"unknown time_range: {rng!r} (expect 'last_week'/'this_month'/...)")
            return None
        col_expr = self._resolve_time_col_expr(time_column, warnings)
        if col_expr is None:
            return None
        start, end = bounds
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
        col_expr = self._resolve_time_col_expr(time_column, warnings)
        if col_expr is None:
            return None
        parts: list[str] = []
        if s:
            parts.append(f"{col_expr} >= {_lit(s)}")
        if e:
            parts.append(f"{col_expr} <= {_lit(e)}")
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

    # 数据集行级范围(scope)只在 query_dataset 通道施加（它拿得到身份）；
    # 语义层绑定不施加 → 必须拒绝而不是静默少一层过滤（护栏 §2 fail-closed）
    if _dataset_has_scopes(int(ds.get("id") or 0)):
        warnings.append(
            f"数据集 '{ds.get('name') or ref}' 配了行级范围(scope)，不能作为语义层对象绑定使用；"
            "请改用 query_dataset 取数（会按身份叠加 scope），或先移除该数据集的 scope")
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
    )


def _render_condition(lhs: str, op: str, value: Any, values: Optional[list[Any]]) -> Optional[str]:
    lit = _lit

    if op == "eq":       return f"{lhs} = {lit(value)}"
    if op == "ne":       return f"{lhs} <> {lit(value)}"
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
