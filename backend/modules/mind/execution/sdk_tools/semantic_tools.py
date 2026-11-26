"""semantic 工具组 — 业务语义增强(get_metrics / get_glossary / query_by_tags / knowledge_search).

handler 为 SDK 无关的纯函数,由 build_semantic_server(backend) 包装。
"""

import asyncio
import json
import logging
import re
import time
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)


# ── 字典口径 description 脱敏（护栏 §7：物理字段名不得暴露给 LLM/前端）────────
# 历史缺陷：adh_metrics/adh_dimensions 的 description 列含物理列引用
# （如 "排除已删除记录(del_flag=0)"、"物理列为 create_time"、"业务别名: total_cases"）
# 直接下发 → 会话侧栏「命中的字典口径」卡片渲染出物理列名。
# 正确口径：description 是「文字性口径说明」，物理列引用应剥离；
# 别名已由结构化 aliases 字段承载，description 里重复列出不需要。
_PHYSICAL_REF_PATTERNS: tuple[re.Pattern, ...] = (
    # "物理列为 xxx" / "物理列: xxx" / "对应物理列 xxx" — 整句删除（含前导逗号/句号）
    re.compile(r"[,，.。]?\s*(?:对应)?物理列[名]?(?:为|是|:|：)\s*\w+"),
    # 括号内含 SQL 赋值/比较的 snake_case 标识符: (del_flag=0), (is_deleted != 1)
    re.compile(r"[,，]?\s*\([a-z_]\w*\s*(?:[=<>!]+|IN|LIKE)\s*[^)]+\)", re.IGNORECASE),
    # "业务别名: xxx / yyy" — 已由 aliases 字段承载，删除该子句（含前导逗号/句号）
    re.compile(r"[,，.。]?\s*业务别名[:：]?\s*[^。，,]+"),
    # "snake_case 口径：..." — 物理列枚举解释（如 "source_type 口径：0=案例、1=订单"）
    re.compile(r"[,，.。;；]?\s*[a-z][a-z_]{2,}\s+口径[:：][^。；]+"),
    # "口径来源：..." — 技术溯源注释（如 "口径来源：集成-记录表 app_code 与…同义"）
    re.compile(r"[,，.。;；]?\s*口径来源[:：][^。]+"),
)


def _sanitize_dict_desc(text: str) -> str:
    """剥离字典 description 中的物理列引用（护栏 §7 + ontology-modeling §3）。"""
    if not text:
        return ""
    out = text
    for pat in _PHYSICAL_REF_PATTERNS:
        out = pat.sub("", out)
    # 清理残留的多余标点和空格（保留句末句号，只剥因删除产生的悬挂标点）
    out = re.sub(r"[,，]{2,}", "，", out)
    out = re.sub(r"[,，;；]\s*$", "", out)
    out = re.sub(r"^[,，.。;；\s]+", "", out)
    return out

#: 多问法并发上限：每路检索会 spawn 一个 qmind CLI(node) 子进程，不封顶会在
#: 共享宿主上把内存打满；4 路已足以把“N 问串行”压成“~N/4 轮”。
_MAX_CONCURRENT_RETRIEVE = 4


#: snake_case 纯小写标识符 = 物理表/列名（如 t_order_records、t_user_customer）
_PHYSICAL_ALIAS_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _public_aliases(aliases, physical: str = "") -> list[str]:
    """对象别名的对 LLM 投影：剥除物理表/列名（护栏 §7）。

    建模时常把 primary_table 写进 aliases（如 t_order_records），它本身就是
    physical_table，不得随对象摘要下发给 LLM/前端。
    """
    out: list[str] = []
    items = aliases
    if isinstance(items, str):
        # 表里 aliases 可能是 JSON 字符串（"[\"a\",\"b\"]"）或逗号串；
        # 直接迭代字符串会逐字符拆成单个字母，必须归一为列表。
        raw = items.strip()
        try:
            loaded = json.loads(raw)
            items = loaded if isinstance(loaded, list) else [raw]
        except (ValueError, TypeError):
            items = [p for p in re.split(r"[,，;；]", raw) if p.strip()]
    elif items is None:
        items = []
    phys = str(physical or "").strip().lower()
    for a in items:
        s = str(a or "").strip()
        if not s:
            continue
        if phys and s.lower() == phys:
            continue
        if _PHYSICAL_ALIAS_RE.match(s):
            continue
        out.append(s)
    return out


def _prop_view(prop) -> dict | None:
    """本体对象属性的**业务投影**（护栏 §7：物理列名一律剥离）。

    canonical doc 的属性业务名键是 `name`（历史/导入数据可能用 `display_name`，
    两者都要认）。历史缺陷（会话 1783069459337）：投影只认 `display_name`，
    而实际 100% 是 `name` → 每个对象的 properties 都被过滤成空数组，
    模型据此反复断言“Order 属性登记为空、订单没有接单方字段”，
    本体建得再全也永远查不出来。属性缺失必须显式可见，不能伪装成“没有属性”。
    """
    if not isinstance(prop, dict):
        return None
    label = str(prop.get("name") or prop.get("display_name") or prop.get("business_desc") or "").strip()
    if not label:
        return None
    out: dict = {"name": label}
    if prop.get("type"):
        out["type"] = str(prop["type"])
    if prop.get("is_key"):
        out["is_key"] = True
    enum = prop.get("enum")
    if enum:
        out["enum"] = enum
    desc = _sanitize_dict_desc(str(prop.get("description") or ""))
    if desc:
        out["description"] = desc
    return out


# ── 工具 handler(SDK 无关) ─────────────────────────────────────

async def get_metrics(args):
    """对象级语义目录：L1 按 keyword 做条目级检索（topK + 每对象计数），L2 按 object 下钻取全量。

    只暴露字典层信息(name/aliases/unit/枚举标签), 不回显 target_table 等物理细节。
    `keyword` **必填**（空关键词=整层字典会被运行时截断，见 KEYWORD_REQUIRED_HINT）。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.semantics.mdl_compiler import (
            _get_dimensions, _get_metrics, active_binding_tables, binding_scope_for, dict_unavailable_reason,
        )

        ds_id = (ctx.datasource_id if ctx else 0) or 0
        # fail-loud: 业务会话未确定数据源时不得回空目录假成功(系统域 ds=0 是合法系统域, 不拦)。
        # 但聚合态（全球）会话 ds=0 是合法口径：指标/维度字典全局共享（adh_metrics/dimensions
        # 均 datasource_id=0），按 ds=0 加载正是全球语义层的正确目录，不得回"请先选会话源"
        # 死胡同（历史缺陷：聚合态站点下连合法指标名都查不出，逐站编排无从开始）。
        from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
        if not ds_id and ctx is not None and not system_scope(ctx):
            from backend.core.site_service import is_aggregate_site
            if not is_aggregate_site((getattr(ctx, "extra", None) or {}).get("site_code") or ""):
                return _text({"error": "当前会话未确定数据源，无法加载语义目录；请先在会话中选择一个已授权数据源后重试。"},
                             is_error=True)
        keywords = metric_keywords(args)
        object_arg = str(args.get("object") or args.get("object_key") or "").strip()
        if not keywords and not object_arg:
            # 空关键词=整层字典（见 KEYWORD_REQUIRED_HINT 的历史缺陷说明）：显式拒，不静默回全量
            return _text({"error": KEYWORD_REQUIRED_HINT}, is_error=True)
        metrics = await asyncio.to_thread(_get_metrics, ds_id)
        dims = await asyncio.to_thread(_get_dimensions, ds_id)
        # 目录层（bound_object_key）与编译层（target_table=物理表）必须同一口径：
        # 拿不到绑定表集合时只做空值裁决，不拿“查不到”当“不可用”
        try:
            all_tables, tables_by_object = await asyncio.to_thread(active_binding_tables)
        except Exception as bind_err:  # noqa: BLE001
            logger.warning("[get_metrics] 部署绑定表集合加载失败（仅做空值裁决）: %s", bind_err)
            all_tables, tables_by_object = set(), {}
        blocked: list[dict] = []

        # 两档语义（与生产路径 scoped_metadata.get_metrics 同口径）：
        #   L1 keyword → 条目级 BM25/子串排序取 topK，只回命中条目 + 每对象全量计数；
        #   L2 object  → 该对象全量（显式下钻）。
        # 两个历史缺陷：子集冒充全量（1783069459337）与泛词整对象补全被截断（1783069459352）。
        rows_all: list[dict] = []
        for m in metrics:
            rows_all.append({**m, "kind": "metric", "object": m.get("bound_object_key") or ""})
        for d in dims:
            rows_all.append({**d, "kind": "dimension", "object": d.get("bound_object_key") or ""})
        totals: dict[str, dict] = {}
        for r in rows_all:
            b = totals.setdefault(r["object"], {"metric_total": 0, "dimension_total": 0})
            b["metric_total" if r["kind"] == "metric" else "dimension_total"] += 1

        object_want = object_arg
        try:
            top_k = max(5, min(30, int(args.get("top_k") or 12)))
        except (TypeError, ValueError):
            top_k = 12
        if object_want:
            picked_rows = [r for r in rows_all if r["object"] == object_want]
            if not picked_rows:
                return _text({"error": f"对象 {object_want} 下没有已登记的指标/维度；"
                                       "先用 keyword 检索确认对象名。"}, is_error=True)
            search_meta = {"engine": "object", "top_k": top_k,
                           "matched_total": len(picked_rows), "truncated": False}
            mode = "object_full"
        else:
            from backend.modules.mind.rag import dict_index
            ranked = dict_index.rank(rows_all, keywords, top_k=top_k)
            picked_rows = [rows_all[h["idx"]] for h in ranked["hits"]]
            search_meta = {"engine": ranked["engine"], "top_k": top_k,
                           "matched_total": ranked["matched_total"],
                           "truncated": ranked["truncated"]}
            mode = "keyword_search"

        objects: dict[str, dict] = {}
        for r in picked_rows:
            reason = dict_unavailable_reason(
                r, binding_scope_for(r, all_tables, tables_by_object), kind=r["kind"])
            if reason:
                blocked.append({"name": r["name"], "kind": r["kind"], "reason": reason})
                continue
            key = r["object"]
            bucket_row = objects.setdefault(key or "(未绑定对象)", {
                "object": key or "", "metrics": [], "dimensions": [],
                **totals.get(key, {})})
            if r["kind"] == "metric":
                bucket_row["metrics"].append({
                    "name": r["name"], "aliases": r.get("aliases") or [],
                    "unit": r.get("unit") or "",
                    "description": _sanitize_dict_desc(r.get("description") or "")})
            else:
                bucket_row["dimensions"].append({
                    "name": r["name"], "aliases": r.get("aliases") or [],
                    "is_time": (r.get("category") or "") == "时间",
                    "enum": r.get("value_labels") or {},
                    "description": _sanitize_dict_desc(r.get("description") or "")})
        items = list(objects.values())
        # C2: 对象关系上下文（Oxigraph/SPARQL）——让 LLM 看到对象间真实关系；
        # 物理 join 表达式不外泄（护栏 §7）；图不可用显式降级标注，不静默。
        relations: list[dict] = []
        relations_note = ""
        obj_keys = [it["object"] for it in items if it.get("object")]
        if obj_keys:
            try:
                from backend.semantics.graph_rag.link_graph import object_links
                relations = [
                    {k: v for k, v in r.items() if k != "join"}
                    for r in await asyncio.to_thread(object_links, obj_keys)
                ]
            except Exception as rel_err:  # noqa: BLE001
                relations_note = f"关系上下文不可用(知识图谱): {rel_err}"
        # 部署清单(v0.6): 对象 × 站点部署事实(事实源=模型级 deploy_sites)——LLM 逐站编排知识;
        # 非独立调用(get_metrics 顺风车字段, 职责三分见 GLOBAL_SEMANTIC_LAYER_DESIGN.md §9)。
        deployments: list[dict] = []
        try:
            from backend.semantics.cross_site import list_deployments
            deployments = await asyncio.to_thread(list_deployments)
        except Exception as dep_err:  # noqa: BLE001
            relations_note = relations_note or f"部署清单不可用: {dep_err}"
        # 取数路径（carrier）已下线：同一概念的跨源取数统一由 LLM 按站点逐站编排
        # （见下方 deployments + usage）；跨源去重合计口径不在目录层提供，需由知识库文档定义。
        out = {"objects": items, "total": len(items), "relations": relations,
               "deployments": deployments, "mode": mode, "search": search_meta,
               "keyword_note": (
                   "只回命中条目（BM25+子串排序取前 {n} 条），不是对象全量；每对象带 "
                   "metric_total/dimension_total，truncated=true 表示还有命中未下发。"
                   "看某对象全量：get_metrics(object=\"<object>\")。"
                   "未出现的对象=名称/英文名/别名/枚举业务名不含这些词，不代表平台没有。").format(n=top_k),
               "usage": "dimensions/metrics 直接复制此处 name 或其 aliases; 按天/月趋势选 is_time=true 的维度并配 time_grain; "
                        "enum 字段列出码值→业务名, filter 可传业务名也可传码值, 结果自动显示业务名; "
                        "问题里给出具体机构/企业名（某技工所、某医院、某客户）时，用同名或近似名的**名称类维度**"
                        "（如「接单技工所」「医院名称」）做 like 过滤（value 写 %名称%），不要因为名称不在维度名里就断言“没有该维度/无法查询”; "
                        "relations 列出对象间关联(跨对象字段用 '对象名.业务名' 限定引用); "
                        "**全部站点/多站点问题：按 deployments 对每个可查站点分别调用 run_semantic_query（site 参数）逐站查询，"
                        "站点不是维度；会话默认站点与可查站点清单见上下文「数据源拓扑」段**; "
                        "跨站去重合计口径不在取数目录提供：默认分站呈现、不得把各站数字相加当合计值；"
                        "如需一个合计数字，其口径需由知识库文档定义后向用户说明；"
                        "若报错'无法解析', 从返回的可用清单改名重试。"}
        if relations_note:
            out["relations_degraded"] = relations_note
        if blocked:
            # 显式告知：这些口径已登记但今天编不出数，不得向用户承诺可查（也不得转成
            # “换个指标试试”的瞎猜）；需要时告知用户该口径待建模修复
            out["unavailable"] = blocked
            out["unavailable_note"] = (
                "unavailable 内的指标/维度在字典中已登记但当前无法取数（原因见 reason）："
                "不得当作可查口径向用户承诺，也不得自行替换成别的指标作答；"
                "用户问到时应说明该口径尚未完成物理绑定/待修复。")
        # 单次下发预算：泛词命中十几个对象时显式降级（见 fit_metrics_payload）
        return _text(fit_metrics_payload(out))
    except Exception as e:
        logger.error("get_metrics error: %s", e)
        return _text({"error": str(e)}, is_error=True)


def _split_multi(value) -> list[str]:
    """把入参归一为多个关键词/问题: 支持 JSON 数组、逗号/换行分隔字符串。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(v).strip() for v in arr if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in s.replace("\n", ",").split(",") if p.strip()]


def _dedupe(items: list[str]) -> list[str]:
    """保留先后次序的去重（LLM 常把同一个词/问法写两遍，不拦就是白烧一次远程查询）。"""
    seen: set = set()
    out: list[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


# ── get_metrics 关键词必填（2026-11-19 拍板）────────────────────────
# 空关键词=把整层字典一次下发：会话 1783069459335 实测 29 对象 / 180 指标 / 230 维度
# = 63,226 字符（落盘 72,588 字节），被 agent 运行时判「输出过大」截断成文件指针 ——
# 模型拿到的是**半本目录**，于是又出现"目录挂着、模型说没这个口径"（no-silent-degradation
# 的反面：不是静默空结果，是静默残缺结果）。发现层因此改为必须先由知识检索 / 技能细则
# 定出业务词，再带词检索；拒绝文案是唯一口径，两条实现（字典层 / 业务投影）共用。
KEYWORD_REQUIRED_HINT = (
    "get_metrics 必须带 keyword，不提供空关键词全量列举（整层字典会被运行时判「输出过大」截断，"
    "目录反而看不全）。关键词直接取自问题里的业务对象/指标/维度词——问题说清楚了就带上直接查，不必先绕知识库；"
    "只有不知道该用什么词时，才先用 `knowledge_search`（业务知识库）或技能细则定词。"
    "多个概念一次带齐（逗号分隔或 JSON 数组，如 \"页面浏览量,渠道\"），不要拆成多轮。"
    "命中对象会回该对象的**全量**口径；结果里没有某对象=它的名称/别名/枚举业务名不含这些词，"
    "**不代表平台没有该口径**，可换更贴近业务的词再查一次，或 ask_user 让用户点名要看什么。")


# ── get_metrics 输出预算（2026-11-25 拍板）────────────────────────────
# 会话 1783069459352 实测：keyword="leads,lead,request demo,表单提交,官网,渠道,广告,案例"
# 命中 15 个对象，`命中即整对象补全` 后一次下发 70,598 字符 → agent 运行时判「输出过大」
# 整份落盘换成文件指针（该文件在会话沙箱外、读不回来），模型手里等于**零目录**，
# 随后把「没查到」说成「平台没有线索对象」——静默残缺结果又一次变成了错结论。
# keyword 必填只挡住了「空词=整层字典」，挡不住「一堆泛词=十几个对象的全量口径」。
# 预算按**下发口径**（紧凑 JSON）度量；超预算逐级降级并显式声明丢了什么、下一步怎么拿。
_MAX_METRICS_PAYLOAD_CHARS = 20000
# 降级档里每个对象最多带多少个名称（逐档收紧，直到收进预算）
_METRICS_INDEX_NAME_STEPS = (8, 4, 2, 0)


def _compact_chars(payload) -> int:
    """按下发口径（紧凑 JSON，见 compat.make_tool 的 json.dumps）度量体积。

    indent 后的字符数不代表真实下发量：同一份目录 indent=2 时 115,896 字符，
    紧凑下发是 70,598 —— 按 indent 定预算等于没设防（同 run_semantic_query 的口径）。
    """
    return len(json.dumps(payload, ensure_ascii=False, default=str))


def _entry_names(rows: list) -> list:
    """只留名称（+ 直命中标记）：剥 aliases/description/enum/unit 这些「解释性字段」。"""
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        item = {"name": r.get("name")}
        if "matched_by_keyword" in r:
            item["matched_by_keyword"] = bool(r.get("matched_by_keyword"))
        out.append(item)
    return out


def _budget_view(out: dict, objects: list, level: str) -> dict:
    """构造降级视图：**只动 objects 明细，其余字段（keywords/note/unavailable…）原样保留**。"""
    view = dict(out)
    view["objects"] = objects
    view["budget_level"] = level
    view["_budget_note"] = (
        "目录超出单次返回预算（紧凑 JSON 上限 {budget} 字符），已按 level={level} 降级下发："
        "**命中的对象清单是全的，缺的只是明细**——某对象没有明细不代表它没有 口径。"
        "要看完整口径：get_metrics(object=\"<object>\") 取该对象全量（不必再猜关键词）；"
        "要换角度重排：调整 keyword 或 top_k。").format(
            budget=_MAX_METRICS_PAYLOAD_CHARS, level=level)
    return view


def fit_metrics_payload(out: dict, budget: int = _MAX_METRICS_PAYLOAD_CHARS) -> dict:
    """把 get_metrics 目录收进单次下发预算，降级必须显式（no-silent-degradation §1）。

    为什么要有预算：`命中即整对象补全` 的语义是对的（子集不得冒充全量，见上面
    keyword_note），但泛词一次传 8 个就会命中十几个对象、每个对象回全量口径。
    运行时截断对模型是**静默**的（只剩一个读不回来的文件指针），所以必须在工具侧兜住。

    三档降级（每档都带 budget_level + _budget_note，说明丢了什么、怎么拿回来）：
    - full  ：预算内，原样（缺省路径，口径零变化）
    - names ：只留 name（+matched_by_keyword），剥 aliases/description/enum/unit
    - index ：只留对象与口径数量 + 每对象前 N 个名称（N 逐档收紧到 0）
    任何一档都**不得把对象从清单里删掉**：删对象 = 半本目录，模型会据此断言「平台没有」。
    """
    if not isinstance(out, dict):
        return out
    if _compact_chars(out) <= budget:
        return out

    objects = out.get("objects") or []
    names = _budget_view(out, [
        {"object": o.get("object"),
         "metrics": _entry_names(o.get("metrics")),
         "dimensions": _entry_names(o.get("dimensions"))}
        for o in objects], "names")
    if _compact_chars(names) <= budget:
        return names

    for step in _METRICS_INDEX_NAME_STEPS:
        rows: list = []
        for o in objects:
            ms = _entry_names(o.get("metrics"))
            ds = _entry_names(o.get("dimensions"))
            row = {
                "object": o.get("object"),
                "metrics": len(ms), "dimensions": len(ds),
                "metric_names": [m.get("name") for m in ms[:step]],
                "dimension_names": [d.get("name") for d in ds[:step]],
            }
            if len(ms) > step:
                row["metric_names_truncated"] = f"该对象共 {len(ms)} 个指标，此处只列前 {step} 个"
            if len(ds) > step:
                row["dimension_names_truncated"] = f"该对象共 {len(ds)} 个维度，此处只列前 {step} 个"
            rows.append(row)
        index = _budget_view(out, rows, "index")
        if len(rows) == 0 or _compact_chars(index) <= budget:
            return index
    return index


def metric_keywords(args: dict) -> list[str]:
    """get_metrics 关键词归一（唯一取法）：多词一次传齐（逗号/换行/JSON 数组），小写供匹配。"""
    return [w.lower() for w in _dedupe(_split_multi(args.get("keyword")))][:10]


# ── 对象级命中 + 跨对象可执行写法（一次给足，省下钻轮次）──────────────────
# 为什么需要它（2026-11-26 联耀实测）：get_metrics 的关键词只检索**字典行**（指标/维度名与
# 别名），本体对象自身的业务名/别名/描述不参与。用户问「第三方集成的用户类型」时，
# `isv`（open_isv_info）根本不出现在候选里，模型只看到 user / user_company_membership
# 两套「用户类型」就反问口径；即使看到 isv，也不知道它经「商户绑定用户」2 跳可达
# 「用户企业关系」。这里把两件事一次给足：对象命中 + 该对象的跨对象可执行写法。
# 只**推荐写法**，不替调用方自动跨对象绑定（口径仍由用户/模型确认，宁缺勿错）。
_CROSS_MAX_HOPS = 2          # 与 planner._JOIN_MAX_HOPS 同口径
_CROSS_HINT_LIMIT = 8
# 对象描述下发：摘要截断 + 「【」口径段**整段保留**。
# 为什么不能硬截一个固定值（2026-11-26 实测）：口径/跨对象写法写在描述末尾，
# 硬截 160 后模型只看到半句「本对象经「商户绑定用户」(isv_」，随后判定
# “描述被截断，需要知识库查完整描述”白烧一轮检索，跨对象写法直接丢掉。
_DESC_HEAD_CHARS = 240
_DESC_NOTE_MAX = 800


def _object_desc(desc, head_chars: int = _DESC_HEAD_CHARS) -> str:
    """对象描述下发：业务摘要（可截断，必须标注）+ 「【…】」口径段整段保留。

    口径段从【开始切分，而不是先截 head 再补全段：两者重叠会把同一口径写两遍，
    既浪费预算又让模型误以为看到两套口径。
    """
    s = str(desc or "").strip()
    if len(s) <= head_chars:
        return s
    cut = s.find("\n【")
    if cut < 0 and s.startswith("【"):
        cut = 0
    body, notes = (s[:cut], s[cut:]) if cut >= 0 else (s, "")
    body = body.strip()
    if len(body) > head_chars:
        body = body[:head_chars].rstrip() + "…（摘要已截断）"
    notes = notes.strip()
    if len(notes) > _DESC_NOTE_MAX:
        notes = (notes[:_DESC_NOTE_MAX]
                 + "…（口径说明已截断，完整见 get_ontology_model(object=…)）")
    return "\n".join(p for p in (body, notes) if p)


def _usable_link_join(join) -> bool:
    """与 planner._join_is_usable 同口径：空 join 与跨跳占位符（含 →/—）都不可编译。"""
    j = str(join or "")
    return bool(j.strip()) and "→" not in j and "—" not in j


def _reachable_objects(edges: dict[str, list[str]], src: str,
                       hops: int = _CROSS_MAX_HOPS) -> list[str]:
    """src 出发 ≤hops 跳的可编译关联对象（不含自身，BFS 层序稳定）。"""
    out: list[str] = []
    seen = {src}
    frontier = [src]
    for _ in range(hops):
        nxt: list[str] = []
        for node in frontier:
            for tgt in edges.get(node) or []:
                if tgt in seen:
                    continue
                seen.add(tgt)
                out.append(tgt)
                nxt.append(tgt)
        frontier = nxt
    return out


def build_object_hits(keywords: list[str], objects: list[dict], dict_rows: list[dict],
                      *, limit: int = 5, max_cross: int = _CROSS_HINT_LIMIT) -> list[dict]:
    """关键词命中的本体对象 + 其 ≤2 跳关联对象上可跨对象引用的维度/指标。

    objects: [{key, display_name, aliases, description, links:[{target, join}]}]
    dict_rows: [{object, kind, name, aliases}]（已在调用方做过作用域过滤）

    命中分档：名称/别名完全相同 > 名称/别名包含 > 描述包含（档内按 key 定序，确定性）。
    cross_object 先给「名称/别名含关键词」的行，剩余名额补该关联对象已登记的指标——
    因为问「按 X 分类有多少」时，分类字段与计数指标往往不同名。
    """
    toks = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    if not toks:
        return []
    scored: list[tuple] = []
    for obj in objects or []:
        key = str(obj.get("key") or "").strip()
        if not key:
            continue
        names = [key, str(obj.get("display_name") or "")]
        names += [str(a) for a in (obj.get("aliases") or [])]
        names_l = [n.lower() for n in names if n]
        desc_l = str(obj.get("description") or "").lower()
        tier = None
        for t in toks:
            if any(n == t for n in names_l):
                tier = 0
                break
            if any(t in n for n in names_l):
                tier = 1
                break
        if tier is None and any(t in desc_l for t in toks):
            tier = 2
        if tier is not None:
            scored.append((tier, key, obj))
    scored.sort(key=lambda x: (x[0], x[1]))

    edges: dict[str, list[str]] = {}
    for obj in objects or []:
        src = str(obj.get("key") or "")
        if src:
            edges[src] = [str(l.get("target") or "") for l in (obj.get("links") or [])
                          if l.get("target") and _usable_link_join(l.get("join"))]
    rows_by_obj: dict[str, list] = {}
    for r in dict_rows or []:
        rows_by_obj.setdefault(str(r.get("object") or ""), []).append(r)

    out: list[dict] = []
    for _tier, key, obj in scored[:limit]:
        # 主对象自己已有同名字段时不再推荐别处的同名（否则「用户类型」会指向 user 主档的
        # 简化分类，而不是 user_company_membership 的权威 0–11 编码）。
        # 身份名取 name/name_en/aliases：同义（如「用户类型」是企业用户类型的别名）也算自有。
        own: set[str] = set()
        for r in rows_by_obj.get(key, []):
            for n in [r.get("name"), r.get("name_en")] + list(r.get("aliases") or []):
                if str(n or "").strip():
                    own.add(str(n).strip().lower())
        matched: list[str] = []
        backup_by_obj: dict[str, list[str]] = {}
        for tgt in _reachable_objects(edges, key):
            for r in rows_by_obj.get(tgt, []):
                name = str(r.get("name") or "")
                if not name or name.lower() in own:
                    continue
                ref = f"{tgt}.{name}"
                hay = " ".join([name, str(r.get("name_en") or "")]
                               + [str(a) for a in (r.get("aliases") or [])]).lower()
                if any(t in hay for t in toks):
                    if ref not in matched:
                        matched.append(ref)
                elif r.get("kind") in ("metric", "dimension"):
                    lst = backup_by_obj.setdefault(tgt, [])
                    if ref not in lst:
                        lst.append(ref)
        # 名额分配：关键词命中优先；余量按目标对象**轮转**各补 2 条，避免单个对象
        # （如 1 跳的 user）把 8 个名额全占满，导致真正需要的 2 跳对象不给提示。
        cross = matched[:max_cross]
        seen = set(cross)
        queues = {tgt: [r for r in refs if r not in seen][:2]
                  for tgt, refs in backup_by_obj.items()}
        progressed = True
        while len(cross) < max_cross and progressed:
            progressed = False
            for tgt in list(queues):
                if len(cross) >= max_cross:
                    break
                if queues[tgt]:
                    ref = queues[tgt].pop(0)
                    if ref in seen:
                        continue
                    seen.add(ref)
                    cross.append(ref)
                    progressed = True
        item = {"object": key,
                "display_name": obj.get("display_name") or "",
                "description": _object_desc(obj.get("description"))}
        if cross:
            item["cross_object"] = cross
        out.append(item)
    return out


async def get_glossary(args):
    """业务术语查询; 支持 keywords 一次传多个(逗号分隔/JSON数组), 合并去重返回。

    多关键词走 **单条合并查询**（TermService.search_terms：一次建连 + 一条 SQL，
    词间 OR）。之前的逐词 list_terms 已实测不占优（N 词 = N 次建连 + 2N 条 SQL，
    并发只把等待换成线程/连接开销），而单次往返直接把这部分从耗时里拿掉。
    未命中词显式列在 unmatched_keywords，不让模型拿“空结果”当“再试一次”的理由。
    """
    try:
        from backend.modules.catalog.services.term_service import TermService

        words = _dedupe(_split_multi(args.get("keywords")) or _split_multi(args.get("keyword")))
        if not words:
            result = await asyncio.to_thread(TermService.list_terms, 1, 50, "")
            return _text(result)
        words = words[:10]
        result = await asyncio.to_thread(TermService.search_terms, words)
        items = result.get("items") or []
        seen: set = set()
        merged: list = []
        for item in items:
            key = item.get("id") or item.get("term_cn")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        matched = sorted({w for it in merged for w in (it.get("matched_keywords") or [])})
        return _text({"items": merged, "total": len(merged),
                      "queried_keywords": words,
                      "matched_keywords": matched,
                      "unmatched_keywords": [w for w in words if w not in matched],
                      "usage": "多术语已在一次调用内全部查完, 无需逐个关键词重复调用；"
                               "unmatched_keywords 里的词术语表确实没登记, 换 knowledge_search 或直接告知用户。"})
    except Exception as e:
        logger.error("get_glossary error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def query_by_tags(args):
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.catalog.services import tags_service
        params = json.loads(args.get("tag_conditions") or "{}")
        conditions = params.get("conditions", [])
        if not conditions:
            return _text({"error": "At least one tag condition required"}, is_error=True)
        # 本体资源域隔离: 授权数据源集由服务端守卫注入 ctx.extra, 不进 LLM 视野、不可篡改。
        # None(未过守卫/程序化直调) → 不裸查全库; 空集 → service 层 fail-closed 不返回。
        ds_ids = (getattr(ctx, "extra", None) or {}).get("tag_authorized_datasource_ids")
        if ds_ids is None:
            return _text({"error": "标签查询需在已授权数据源范围内执行；当前会话未确定可用数据源。"}, is_error=True)
        result = await asyncio.to_thread(
            tags_service.query_entities_by_tags,
            conditions,
            params.get("operator", "AND"),
            ctx.workspace_id,
            list(ds_ids),
        )
        return _text({"items": result, "total": len(result)})
    except json.JSONDecodeError:
        return _text({"error": "Invalid JSON in tag_conditions"}, is_error=True)
    except Exception as e:
        logger.error("query_by_tags error: %s", e)
        return _text({"error": str(e)}, is_error=True)


def _classify_retrieval_source(rag_source: str, count: int) -> str:
    """把检索结果的 rag_source 归一为 eval 可分桶的来源标签。

    词汇（与 `knowledge_retriever._rag_source_of` 同源，新增通道时两侧同改）：
      qmind_hit=仅云端 qmind 命中 / bailian_hit=仅百炼外部语料命中 /
      multi_source_hit=多通道同时命中（跨源名次归并）/
      local_hybrid_fallback=回退本地 graphrag+BM25 / none=无命中。

    `qmind_hit` 这个旧词汇保留：它是历史评测基线与用例的断言值，多源接入后仍准确
    描述“只有 qmind 命中”这一档；新通道另加标签而不是重命名旧标签（否则基线伪回退）。
    """
    rs = (rag_source or "").lower()
    if not count:
        return "none"
    if "fallback" in rs or "graphrag" in rs or "bm25" in rs:
        return "local_hybrid_fallback"
    if rs == "kb_multi":
        return "multi_source_hit"
    if rs == "qmind":
        return "qmind_hit"
    if rs in ("cloud_rag", "bailian"):
        return "bailian_hit"
    return f"{rs}_hit" if rs else "none"


def _attach_routes(result: dict) -> dict:
    """把命中对象 key 解析为业务本体路由并附到结果（canonical 确定性读取）。

    路由解析失败时 routes 置空并显式标注 route_resolution=failed ——
    routes=[]（真无路由）与 failed（解析降级）必须可区分（no-silent-degradation）。
    """
    try:
        from backend.modules.mind.rag.business_route import resolve_business_routes
        result["routes"] = resolve_business_routes(result.get("hit_object_keys") or [])
        result["route_resolution"] = "ok"
    except Exception as e:  # noqa: BLE001 — 路由是增强信息，不毁掉检索主结果
        logger.warning("[knowledge_search] 业务本体路由解析失败: %s", e)
        result["routes"] = []
        result["route_resolution"] = f"failed: {e}"
    return result


def _attach_paths(result: dict, keywords: list | None = None) -> dict:
    """把命中结果解析为行为路径口径（关键路径/漏斗/用户指定路径）并附到结果。

    路径不是 SQL 维度而是"有序步骤口径"：它告诉 LLM 该查哪个对象、能传哪些参数，
    避免把漏斗拆成 N 个独立步骤指标（无顺序/无窗口⇒转化率系统性偏高）。
    解析失败时 paths 置空并显式标注 path_resolution=failed（与 routes 同口径）。
    """
    try:
        from backend.modules.mind.rag.behavior_path import resolve_behavior_paths
        paths = resolve_behavior_paths(
            object_keys=result.get("hit_object_keys") or [], keywords=keywords or [])
        result["paths"] = paths
        result["path_resolution"] = "ok"
    except Exception as e:  # noqa: BLE001 — 路径是增强信息，不毁掉检索主结果，但必须可诊断
        logger.warning("[knowledge_search] 行为路径解析失败: %s", e)
        result["paths"] = []
        result["path_resolution"] = f"failed: {e}"
    return result


def _attach_graph_relations(result: dict) -> dict:
    """C3: 检索命中对象的 Oxigraph 关系展开（1 跳邻接）；图不可用显式降级标注。"""
    keys = result.get("hit_object_keys") or []
    if not keys:
        return result
    try:
        from backend.semantics.graph_rag.link_graph import object_links
        rels = [{k: v for k, v in r.items() if k != "join"} for r in object_links(keys)]
        return {**result, "graph_relations": rels, "graph_source": "oxigraph"}
    except Exception as e:  # noqa: BLE001 — 不静默：降级必须可诊断
        return {**result, "graph_relations": [],
                "graph_degraded": f"关系展开不可用(知识图谱): {e}"}


def _attach_dict_candidates(result: dict, question: str, ds_id: int) -> dict:
    """把知识库命中的对象拿到字典里做**名称校验**（候选而非裁决）。

    为什么要这一步：知识库适合判断“这问题属于哪个域/对象”，但它本身不是名称权威
    （文档可能 doc_stale，且只能拿到 chunk 片段）。让模型凭 chunk 猜指标名，
    planner 解析失败后会退到 execute_sql 或直接断言“平台没有”。这里用问题词在
    **KB 命中对象内**做一次精确字面匹配，把可直接用的口径名列出来；命中不了就
    明确引导去 get_metrics，而不是让模型自己编。
    """
    keys = result.get("hit_object_keys") or []
    if not keys:
        return result
    try:
        from backend.modules.mind.rag import dict_index
        terms = dict_index.extract_terms(question)
        if not terms:
            return {**result, "dict_candidates": [], "dict_verify": "no_terms"}
        scoped = dict_index.search(terms, datasource_id=ds_id,
                                   allowed_keys=set(keys), top_k=8)
        cands = [{k: h.get(k) for k in
                  ("object", "kind", "name", "name_en", "aliases", "enum_labels", "matched_by")}
                 for h in scoped.get("hits") or []]
        out = {**result, "dict_candidates": cands,
               "dict_verify": "ok" if cands else "empty"}
        if not cands:
            out["dict_note"] = (
                "知识库命中的对象里，问题字面没有直接匹配到已登记口径名。"
                "可换业务同义词再检索一次，或先用 get_metrics(keyword=...) 确认名称；不要凭文档片段自造。")
        if result.get("doc_stale") is True:
            out["dict_warning"] = (
                "知识库文档落后于当前本体（doc_stale=true）：上面 dict_candidates 已与字典核对，"
                "可用；但文档里的口径解释可能过期，以 get_metrics 与字典为准。")
        return out
    except Exception as e:  # noqa: BLE001 — 校验是增强，失败必须可见（不得静默当“已验证”）
        logger.warning("[knowledge_search] 字典候选校验失败: %s", e)
        return {**result, "dict_candidates": [], "dict_verify": f"failed: {e}"}


def _retrieve_and_enrich(question: str, ds_id: int, bound_kb_ids, system_scope_flag: bool) -> dict:
    """单问题：多源知识检索 + 增强链（业务本体路由 / 图关系 / 行为路径）整体**同步**执行。

    必须是同步函数并由调用方丢进线程：增强链里有 DB/图查询，留在事件循环上会
    ① 把并发检索退化成串行（attach 期间占住循环），② 阻塞流式消息解析。

    `retrieval_ms` 只计检索段（不含 attach / CLI→MCP 分派 / 工具治理开销），
    使 trace 能区分“知识库真慢”与“工具包装层慢”。

    入口从 `qmind_retrieve` 改为 `knowledge_retrieve`：绑定多个知识库时由服务端
    并行扇出到各 provider 并按名次归并，**不得**回到“逐库单问 + first-hit-wins”——
    那会让绑定顺序决定答案来源，而那个顺序用户既看不到也调不了。
    """
    from backend.modules.mind.rag.knowledge_retriever import knowledge_retrieve

    t0 = time.perf_counter()
    result = knowledge_retrieve(question, ds_id, bound_kb_ids, system_scope=system_scope_flag)
    retrieval_ms = int((time.perf_counter() - t0) * 1000)
    source = _classify_retrieval_source(result.get("rag_source"), result.get("count") or 0)
    return _attach_paths(
        _attach_graph_relations(_attach_dict_candidates(_attach_routes(
            {**result, "retrieval_source": source, "retrieval_ms": retrieval_ms}), question, ds_id)),
        keywords=_split_multi(question))


async def knowledge_search(args):
    """知识库检索; 支持 questions 一次传多个问题, 批量**并发**检索减少调用轮次。

    附带检索来源归因(记录到 observability, 未开启即 no-op), 供 eval 按来源分桶统计。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.observability import record_span

    ctx = get_execution_context()
    try:
        # 仅消费服务端 AS-BOT 显式绑定；缺少上下文不能查询全库。
        bound_kb_ids = (ctx.extra or {}).get("bound_knowledge_base_ids")
        # 系统能力形态（能力叠加）：系统 KB 照常检索，无命中同样回退业务元数据并标注来源；
        # “系统运营问题不拿业务知识充数”由 prompt 引导 + 来源分桶承担，不再检索硬限定。
        from backend.modules.mind.execution.sdk_tools.scoped_metadata import system_scope
        system_scope_flag = system_scope(ctx)
        if not bound_kb_ids:
            return _text({"error": "当前 AS-BOT 未绑定知识库"}, is_error=True)
        from backend.modules.mind.execution.resource_guard import validate_knowledge_binding
        await asyncio.to_thread(validate_knowledge_binding, bound_kb_ids)
        # 会话已选源由服务端上下文提供；不把 datasource_id 暴露给 LLM——
        # 护栏 _check_source 本就拒绝非会话源，暴露可选参数只会诱导模型猜 id 而被反复拒。
        ds_id = (ctx.datasource_id if ctx else 0) or 0
        questions = _split_multi(args.get("questions")) or _split_multi(args.get("question"))
        questions = _dedupe(questions)[:8]
        if not questions:
            return _text({"error": "请传 question 或 questions(多个用逗号/JSON数组)"}, is_error=True)
        if len(questions) == 1:
            result = await asyncio.to_thread(
                _retrieve_and_enrich, questions[0], ds_id, bound_kb_ids, system_scope_flag)
            # 归因不写敏感 SQL/PII, 仅来源标签 + 耗时(护栏 §9)
            record_span(kind="retrieval", name="knowledge_search", status="success",
                        duration_ms=int(result.get("retrieval_ms") or 0),
                        input_text=questions[0][:200],
                        output_text=f"source={result.get('retrieval_source')} "
                                    f"count={result.get('count') or 0}")
            return _text(result)
        # 多问法**并发**检索（历史缺陷：for 循环串行，2 问=2 段检索时间直接叠加）。
        t0 = time.perf_counter()
        sem = asyncio.Semaphore(_MAX_CONCURRENT_RETRIEVE)

        async def _one(qtext: str) -> dict:
            async with sem:
                return await asyncio.to_thread(
                    _retrieve_and_enrich, qtext, ds_id, bound_kb_ids, system_scope_flag)

        fetched = await asyncio.gather(*(_one(q) for q in questions))
        wall_ms = int((time.perf_counter() - t0) * 1000)
        results = [{"question": qtext, **r} for qtext, r in zip(questions, fetched)]
        # 归因按标签动态计数（多源接入后不再只有 qmind_hit），但保留旧键恒存在：
        # 历史用例与管理页读 `attribution["qmind_hit"]`，缺键会被当成统计异常。
        attribution: dict[str, int] = {}
        for x in results:
            label = str(x.get("retrieval_source") or "none")
            attribution[label] = attribution.get(label, 0) + 1
        attribution.setdefault("qmind_hit", 0)
        attribution.setdefault("local_hybrid_fallback", 0)
        record_span(kind="retrieval", name="knowledge_search", status="success",
                    duration_ms=wall_ms, input_text=f"{len(questions)} questions",
                    output_text=" ".join(f"{k}={v}" for k, v in sorted(attribution.items())))
        return _text({"results": results, "total": len(results),
                      "attribution": attribution,
                      "usage": "多问题已在一次调用内并发检索, 无需逐个重复调用。"})
    except Exception:
        logger.exception("知识检索失败")
        return _text({"error": "知识检索未完成，请检查 AS-BOT 知识库绑定或联系管理员"}, is_error=True)


async def list_datasets(args):
    """列出当前用户可见的 BI 数据集(治理建模层)."""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.viz.services import dataset_service
        identity = {
            "user_id": (ctx.user_id if ctx else 0) or 0,
            "username": (ctx.username if ctx else "") or "",
            "role": (ctx.user_role if ctx else "") or "",
            "workspace_id": (ctx.workspace_id if ctx else 0) or 0,
        }
        items = await asyncio.to_thread(
            dataset_service.list_datasets, identity, args.get("keyword") or "")
        return _text({
            "datasets": [
                {"id": d["id"], "name": d["name"], "source_type": d["source_type"],
                 "object_key": d.get("object_key") or "", "description": d.get("description") or "",
                 "field_count": d.get("field_count"),
                 # 可用数据源范围只回业务名（护栏 §7）：多源数据集靠 site/datasource 逐查询定源
                 "datasource": d.get("datasource_name") or "",
                 "available_datasources": d.get("available_source_names") or [],
                 "usable_datasources": d.get("usable_now") or []}
                for d in items
            ],
            "total": len(items),
            "usage": ("用 query_dataset(name=...) 取数; 已内置行级范围/RLS/敏感脱敏, 与看板同口径。"
                      "available_datasources 多于 1 个源的数据集可跨源执行：用 site（站点业务名）或 "
                      "datasource（数据源业务名）指定本站；不传则由服务端按会话源/执行源定源。"),
        })
    except Exception as e:
        logger.error("list_datasets error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def query_dataset(args):
    """按数据集取数(治理建模层): 权限/RLS/scopes/审计服务端施加."""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.viz.services import dataset_service
        identity = {
            "user_id": (ctx.user_id if ctx else 0) or 0,
            "username": (ctx.username if ctx else "") or "",
            "role": (ctx.user_role if ctx else "") or "",
            "workspace_id": (ctx.workspace_id if ctx else 0) or 0,
        }
        ds = None
        ds_id = int(args.get("dataset_id") or 0)
        if ds_id:
            ds = await asyncio.to_thread(dataset_service.get_dataset, ds_id)
        elif args.get("name"):
            ds = await asyncio.to_thread(
                dataset_service.get_dataset_by_name, str(args["name"]).strip())
        if not ds:
            return _text({"error": "数据集不存在, 先用 list_datasets 查看可用数据集"},
                         is_error=True)
        params = {
            "dimensions": args.get("dimensions") or [],
            "measures": args.get("measures") or [],
            "filters": args.get("filters") or [],
            "order": args.get("order") or [],
            "limit": args.get("limit") or 200,
            "args": args.get("args") or args.get("variables") or {},
            # 选源只接业务名（站点名/源名），服务端在“可用范围 ∩ 角色授权集”内定源
            "site": args.get("site") or "",
            "datasource": args.get("datasource") or "",
            "preferred_datasource_id": int((ctx.datasource_id if ctx else 0) or 0),
        }
        result = await asyncio.to_thread(
            dataset_service.query_dataset, int(ds["id"]), params, identity)
        # 数据源黑盒: 剥离可能出现的 SQL/物理细节
        for k in ("sql", "base_sql", "secured_sql", "datasource_id", "catalog_ref"):
            result.pop(k, None)
        return _text(result)
    except Exception as e:
        logger.error("query_dataset error: %s", e)
        return _text({"error": str(e)}, is_error=True)


# 工具元信息(name / description / schema / annotations)
TOOL_SPECS = [
    {
        "name": "get_metrics",
        "description": (
            "指标定义检索：对象级语义目录（字典层）已登记的可用名称，是 object/metric/dimension 命名的**最终裁决者**（不止于工具）。"
            "但**它不是每次取数的必经步骤**：知识库的平台语义层文档（knowledge_search 返回的 dict_candidates）"
            "同样含对象/指标/维度/别名/枚举，那里已给出可用名称且未标 doc_stale 时可直接取数，不必再调本工具。"
            "需要它兜底的情形：检索无命中/dict_verify=empty、doc_stale=true（文档落后于本体）、或要某对象的完整口径清单。"
            "指标/维度名只能从本目录或平台语义层文档原文复制（name 或任一 alias 都合法，系统自动解析别名），"
            "不要凭术语表、SQL 结果或常识推断、改写或另造；目录与平台语义层文档都没有的名称一律不得使用。"
            "趋势用 is_time=true 的维度并配 time_grain；enum 给出枚举码值→业务名，filter 两种写法都可；"
            "relations 列出对象间关联；dimensions/metrics 的 name/aliases/unit/口径以本目录返回为准。"
            "**keyword 必填**：空关键词的全量列举已取消（整层字典 60KB+ 会被运行时判「输出过大」截断）。"
            "多个概念用逗号分隔或 JSON 数组一次带齐。**只回命中条目**（BM25+子串排序取 topK），"
            "每个对象附 metric_total/dimension_total（该对象一共多少条）；要取某对象全量口径用 object=\"<object>\"。"
        ),
        "schema": {"keyword": Annotated[Optional[str],
                     "指标/维度/对象的业务关键词（与 object 二选一）。多个概念一次带齐："
                     "逗号分隔或 JSON 数组，如「订单,成交额」；两者都空会被拒，不做全量列举"],
                   "object": Annotated[Optional[str],
                     "对象 key（下钻）：取该对象**全量**指标/维度，替代过去靠命中隐式获得的整对象补全"],
                   "top_k": Annotated[Optional[int],
                     "命中条目上限（5~30，默认 12）；泛词命中太多时上调，要精确时下调"]},
        "handler": get_metrics,
    },
    {
        "name": "get_glossary",
        "description": (
            "Search the business glossary for term definitions and field mappings "
            "(e.g. what '华东' or '活跃用户' means in this business). "
            "Pass ALL terms you need in ONE call via `keywords` (comma-separated or JSON array) "
            "instead of calling repeatedly — 多关键词已在服务端合成一次查询。 "
            "Use this to translate business terms before writing SQL filters. "
            "仅限业务黑话/枚举口径的翻译；入口地址、联系方式、组织分工这类事实问题不在术语表里，"
            "不要用本工具去补 knowledge_search 的未命中。"
        ),
        "schema": {
            "keyword": Annotated[Optional[str], "Single term keyword in Chinese or English; empty means list all"],
            "keywords": Annotated[Optional[str], "Multiple terms in ONE call, comma-separated or JSON array, e.g. '华东,活跃用户' — merged & deduped"],
        },
        "handler": get_glossary,
    },
    {
        "name": "query_by_tags",
        "description": (
            "Find data entities (tables etc.) by tag conditions, e.g. entities tagged '财务' AND '核心'. "
            "tag_conditions is a JSON string: {\"conditions\": [{\"tag_id\": 1}], \"operator\": \"AND\"}."
        ),
        "schema": {"tag_conditions": Annotated[str, 'JSON string, e.g. {"conditions": [{"tag_id": 1}], "operator": "AND"}']},
        "handler": query_by_tags,
    },
    {
        "name": "knowledge_search",
        "description": (
            "Retrieve authoritative business context for natural-language questions. "
            "服务端会并行检索**全部已绑定的知识库**（平台同步的 QMind 本体文档、以及企业维护的"
            "外部语料）并按名次归并，你不需要也不应该选通道。平台同步库里含**最新**本体模型文档"
            "(objects/metrics/dimensions/SQL-template variables) — 先查一次再作答。 "
            "Pass ALL questions in ONE call via `questions` (comma-separated or JSON array); "
            "多个问法在一次调用内**并发**检索，因此不要把候选问法拆成多次调用逐个试探——"
            "一次就把所有同义措辞（如“官网域名/中国站网址/访问入口”）全传进去。 "
            "每条结果带 `knowledge_base`/`corpus_origin`：`platform_synced` 可作口径解释与建模上下文，"
            "`external` 只作背景与资料事实，**不得**拿它当指标/维度名称权威（名称以平台语义层文档与 get_metrics 为准）；"
            "引用结论时照 `citations` 里的标题说出处。 "
            "When hit_object_keys is returned, prefer copying those keys verbatim as the "
            "`object` of run_semantic_query (they are the KB-recognized ontology objects; verify "
            "names still exist in get_metrics if doc_stale=true). "
            "`dict_candidates` 是 KB 命中对象下**已与字典核对**的指标/维度名（含 kind/别名/枚举业务名），"
            "可直接用作 run_semantic_query 的 object/metrics/dimensions；`dict_verify=empty` 表示问题"
            "字面没接上已登记口径，发查询前先用 get_metrics(keyword=...) 确认名称，不得自造；"
            "`dict_warning` 出现时表示文档落后于本体，名称以 dict_candidates/get_metrics 为准。 "
            "无命中时回退本地元数据检索（BM25 + graph），并在 `sources`/`degraded` 里如实标注；"
            "部分库未能检索时会带原因返回，不得把“没问到”当成“库里没有”。 "
            "仅用于业务本体/口径建模上下文；系统运营/用量/活跃类问题（如“近N天有多少人用 chat”）"
            "请用 system_usage / system_overview，不要用本工具。"
        ),
        "schema": {
            "question": Annotated[Optional[str], "Single natural-language question, e.g. '上个月的销售额'"],
            "questions": Annotated[Optional[str], "Multiple questions in ONE call, comma-separated or JSON array (max 8) — results aligned by order"],
        },
        "handler": knowledge_search,
    },
    {
        "name": "list_datasets",
        "description": (
            "List published BI datasets visible to the current user (governed modeling layer on "
            "top of the ontology). Each dataset has a stable name, fields (dimensions/measures) "
            "and row-scope policy. Prefer querying a published dataset over ad-hoc "
            "run_semantic_query when one matches the user's question — same numbers as dashboards."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Optional name/description keyword"]},
        "handler": list_datasets,
    },
    {
        "name": "query_dataset",
        "description": (
            "Fetch rows from a published BI dataset by name (or dataset_id). Server applies "
            "permissions/RLS/sensitive masking/row-scope/audit — identical governance as "
            "dashboards. dimensions/measures must be copied from the dataset field list "
            "(see list_datasets or dataset detail). filters: [{field, op(eq|ne|gt|gte|lt|lte|in|like), value}]. "
            "A dataset may declare multiple usable datasources (available_datasources): pick one per "
            "query with site (site business name) or datasource (datasource business name) — never an id; "
            "if it stays ambiguous the server returns the candidate business names instead of guessing."
        ),
        "schema": {
            "name": Annotated[Optional[str], "Exact dataset name from list_datasets"],
            "dataset_id": Annotated[Optional[int], "Alternative: numeric dataset id"],
            "site": Annotated[Optional[str], "Business site name to run against, e.g. '中国' / '国际' (multi-site datasets)"],
            "datasource": Annotated[Optional[str], "Business datasource name to run against, e.g. 'prd-cn' (takes precedence over site when both)"],
            "dimensions": Annotated[Optional[list], "Dimension field names to group by"],
            "measures": Annotated[Optional[list], "Measure field names to aggregate"],
            "filters": Annotated[Optional[list], "[{field, op, value}] conditions"],
            "order": Annotated[Optional[list], "[{by, desc}] ordering"],
            "limit": Annotated[Optional[int], "Max rows (default 200)"],
            "args": Annotated[Optional[dict], "Declared dataset variables {name: value} (from dataset variables)"],
        },
        "handler": query_dataset,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_semantic_server(backend: str = "qoder", tool_names=None):
    """构建 semantic 进程内 MCP server(qoder / claude).

    Phase 3 将 run_semantic_query 归入本组（与 retrieval 类工具同层），
    保证 agent 只需开启 “semantic” 即可拿到完整“发现对象 + 下意”工具集。
    tool_names 给定时只注册被选中的工具(AS-BOT 粒度逐个控制)。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        TOOL_SPECS as SEMANTIC_QUERY_SPECS,
    )

    specs = list(TOOL_SPECS) + list(SEMANTIC_QUERY_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_semantic", tools)
