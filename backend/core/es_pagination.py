"""ES 型数据集的服务端分页（`/_sql/translate` + `search_after` 游标）。

为什么不是「SQL 里拼 LIMIT/OFFSET」—— 三条都是实机校准（ES 8.9.1 + 本集群网关）：
1. ES SQL 语法里**没有 OFFSET**：`LIMIT 10, 5` 与 `LIMIT 5 OFFSET 10` 均回 HTTP 400；
2. PIT 接口（`/_search/point_in_time`、`/_pit`）在本集群是 `no handler found`，
   因此官方推荐的 `_shard_doc` tiebreaker 用不了（ES 明确拒绝
   "cannot be used without [point in time]"），tiebreaker 只能用 `_doc`；
3. `from/size` 受 `index.max_result_window`（默认 1 万行）限制，且按模式检索跨数百
   分片时超窗会回 **200 + 空 hits + 数百个分片失败** —— 空表会被读成"这页没数据"。

所以走 `search_after`：深度无上限、与 `from/size` 同序（已逐值对拍），代价是
**只能从上一页游标往后走，随机跳页不可得**。因此本模块只接受两类请求：第 1 页，
以及游标已在缓存里的页（已访问过的页 / 紧邻的下一页）。其余显式拒绝并给可行做法，
不做"从头逐跳快进"的假跳页 —— 那会把一次点击变成几十次全索引检索。

游标缓存放服务端而不是前端：游标是 ES 内部 sort 值，让它进 LLM/前端视野既无意义
也等于把取数形状外泄；且同一图表被多个渲染面（浏览页/大屏/弹窗页）共用。

翻页上下文（首页确立的检索口径）也必须放服务端：正文里的时间窗在调用方未传参时
由服务端按当前时刻补（`_es_default_window`），逐页重新渲染会让每页查的不是同一个集合
—— 那是漏行/重行（错数），比报错难查得多。详见 `_page_context`。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from backend.core import es_client

logger = logging.getLogger(__name__)

# 表值图每页行数：档位由前端只给 20/50/100，服务端仍按硬上限收口（不信客户端自觉）
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100
# 翻页上下文缓存：签名 → {"sql": 首页正文, "cursors": {页号: 游标}, "ts": monotonic}
# cursors[N] 的含义：取第 N 页要带的 search_after；第 1 页不需要游标，故不登记。
_CURSOR_TTL_SEC = 900.0
_CURSOR_SIGNATURES = 200

_lock = threading.Lock()
_contexts: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class EsPagingUnsupported(ValueError):
    """该 ES 正文形态不能按页取数（聚合/表达式/列映射不可证）。

    文案面向人（数据集页/看板都能看到），只含业务原因与可行做法，不含集群地址/凭据；
    `safe_business_message` 让治理口原样外抛这句（而不是降级成通用“执行失败，稍后重试”，
    与 `es_client.EsError` 同一约定）。
    """

    safe_business_message = True


def resolve_page(params: dict) -> Optional[tuple[int, int]]:
    """从取数参数解析 (page_size, page_num)；未要求分页返回 None。

    只认 `page_size`/`page_num` 两个业务参数（页号从 1 起）。偏移量由服务端算，
    不接受调用方直接递交 offset —— 那等于让前端决定扫到第几行。
    没给 = 不分页；给了但非法 = 报错（不静默改成 1 页/首页，那会把前端的 bug 吃成错页）。
    """
    raw_size = params.get("page_size")
    if raw_size in (None, ""):
        return None
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        raise ValueError(f"page_size 必须是正整数，收到 {raw_size!r}")
    if size < 1:
        raise ValueError(f"page_size 必须是正整数，收到 {raw_size!r}")
    raw_num = params.get("page_num")
    if raw_num in (None, ""):
        num = 1
    else:
        try:
            num = int(raw_num)
        except (TypeError, ValueError):
            raise ValueError(f"page_num 必须是从 1 起的正整数，收到 {raw_num!r}")
        if num < 1:
            raise ValueError(f"page_num 必须是从 1 起的正整数，收到 {raw_num!r}")
    # 硬上限：档位之外（手改请求体/旧配置残留）一律收到 100，不放开扫行规模
    return min(size, PAGE_SIZE_MAX), num


def _signature(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def paging_key(pattern: str, body: str, args: dict, page_size: int) -> str:
    """翻页上下文签名：只含**不随时刻变化**的口径（索引范围 + 正文模板 + 调用方给的参 + 页大小）。

    拿渲染后的正文算就会漂：未传时间参数时服务端按 `datetime.now()` 补滚动窗
    （见 `dataset_service._es_default_window`），两次请求隔几秒正文就不一样，
    第 2 页永远找不到第 1 页留下的游标 —— 现网实测只能停在首页。
    正文被改写（模板变了）则签名自然变化 → 旧上下文作废，不会拿旧口径继续翻。
    """
    try:
        rendered_args = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered_args = repr(sorted((args or {}).keys()))
    return _signature(f"{pattern}\n{body}\n{rendered_args}\n{page_size}")


def _take_entry(key: str) -> dict[str, Any]:
    """取（或建）该签名的翻页上下文，顺带按 TTL 与容量清理（调用方持锁）。"""
    now = time.monotonic()
    while _contexts and now - next(iter(_contexts.values()))["ts"] > _CURSOR_TTL_SEC:
        _contexts.popitem(last=False)
    while len(_contexts) >= _CURSOR_SIGNATURES:
        _contexts.popitem(last=False)
    entry = _contexts.get(key)
    if entry is None:
        entry = {"sql": "", "cursors": {}, "ts": now}
        _contexts[key] = entry
    _contexts.move_to_end(key)
    entry["ts"] = now
    return entry


def reset_cursors() -> None:
    """清空翻页上下文（测试与配置变更后的显式重置入口；不参与取数口径判定）。"""
    with _lock:
        _contexts.clear()


def _page_context(key: str, sql: str, page_num: int) -> str:
    """定下本次检索用的正文：首页重新确立口径，后续页必须复用首页那一份。

    为什么不能逐页各自渲染：滚动窗随时刻右移，新数据会插进排序序列的头部，
    拿上一页留下的游标去取“下一页”就会漏行/重行。这里宁可用略旧但口径一致的
    窗口把这一页取对，也不用新窗口取出一张错位的表（错数比报错危害大）。
    """
    with _lock:
        entry = _take_entry(key)
        if page_num == 1:
            entry["sql"] = sql
            entry["cursors"].clear()   # 重新起翻：更后面的陈旧游标一律作废
            return sql
        bound = entry["sql"]
    if not bound:
        raise EsPagingUnsupported(
            "该查询的翻页上下文已失效（服务重启、超时未翻、或筛选条件已变化）："
            "请先回到第 1 页重新查询，再逐页往后翻")
    return bound


def _resolve_cursor(key: str, page_num: int) -> Optional[list]:
    """定该页的 search_after 游标；取不到即拒（不"从头快进"冒充跳页）。"""
    if page_num == 1:
        return None
    with _lock:
        cursors = _take_entry(key)["cursors"]
        cursor = cursors.get(page_num)
        if cursor is not None:
            return list(cursor)
        deepest = max(cursors) if cursors else 1
    raise EsPagingUnsupported(
        f"ES 明细数据集按游标顺序翻页：当前最多可翻到第 {deepest} 页，"
        f"无法直接跳到第 {page_num} 页。请逐页向后翻，或缩小筛选范围减少总行数")


def _remember_cursor(key: str, page_num: int, res: dict[str, Any]) -> None:
    """记下"下一页的入口游标"；本页没取满说明已到末尾，顺手清掉更后面的陈旧游标。"""
    with _lock:
        cursors = _take_entry(key)["cursors"]
        if res.get("last_sort") and res.get("has_more"):
            cursors[page_num + 1] = res["last_sort"]
        else:
            cursors.pop(page_num + 1, None)


def _validate_shape(dsl: dict, items: list[tuple[str, str]]) -> None:
    """DSL 形状门：只有"平铺明细检索"能按页取，其余显式拒并说明怎么做才对。"""
    if dsl.get("aggregations"):
        raise EsPagingUnsupported(
            "该 ES 正文是聚合统计结果（GROUP BY/聚合函数），本身就是完整的一页，"
            "不存在按页取数；请在数据集关闭「支持分页」，图表按行数上限展示")
    fields = dsl.get("fields") or []
    if not fields:
        raise EsPagingUnsupported(
            "该 ES 正文没有可映射的字段投影（可能是常量查询），无法按页取数")
    if any("script" in (f or {}) for f in fields):
        raise EsPagingUnsupported(
            "该 ES 正文的投影含表达式/函数，检索结果无法与输出列一一对应；"
            "请把表达式做成数据集的独立列，或关闭「支持分页」")
    paths = [f.get("field") for f in fields]
    parsed = [p for p, _alias in items]
    if paths != parsed:
        # 列名与值是按位置配对的：对不上就是错行（错数比无答案危害大，宁拒不错）
        detail = "; ".join(f"{a}→{b}" for a, b in zip(parsed, paths)) or "字段数不一致"
        raise EsPagingUnsupported(
            f"投影字段与检索字段对不上（{detail}），列名与值可能错行，已拒绝按页取数")


def query_page(pattern: str, sql: str, *, page_size: int, page_num: int,
               context_key: str, ctx: Optional["es_client.EsCtx"] = None) -> dict[str, Any]:
    """按页取 ES 明细：返回 columns/rows/row_count/total/page_size/page_num/paging_mode/sql。

    `columns` 用 SQL 投影的业务别名（不是 ES 字段路径），与 `run_sql` 的输出列名同口径；
    `rows` 是**按列序的值数组**（与 `run_sql` 同形），重名列由调用方的
    `df_serialize._unique_columns` 统一消歧，不在这里拿 dict 存（同名列会静默合并）。
    `context_key` 是翻页上下文签名（由 `paging_key` 算，不含随时刻漂移的渲染结果）；
    回显的 `sql` 是本页**实际检索用的正文**，调用方的审计/es_indexes 要按它算，
    不得拿自己重新渲染的那一份（后续页用的是首页确立的口径）。
    `ctx` 缺省 = 内置 ES 通道（阶段二迁完前的存量行为零变化）；挂 ES 数据源时由
    调用方传 `es_client.ctx_for(datasource_id)`，开放范围与预算一律按该源判。
    """
    sql = _page_context(context_key, sql, page_num)
    items = es_client.select_items(sql)
    dsl = es_client.translate_select(sql, ctx=ctx)
    _validate_shape(dsl, items)

    # `_doc` 作唯一性 tiebreaker：主排序键（如秒级时间戳）必然存在并列，
    # search_after 要求全序，缺它同一行可能跨页重复或整行被跳过。
    paged_dsl = {**dsl, "sort": list(dsl.get("sort") or []) + [{"_doc": "asc"}]}
    cursor = _resolve_cursor(context_key, page_num)

    res = es_client.search_page(pattern, paged_dsl, size=page_size, search_after=cursor, ctx=ctx)
    rows = [[r.get(path) for path, _alias in items] for r in res["rows"]]
    _remember_cursor(context_key, page_num, res)

    return {
        "columns": [alias for _path, alias in items],
        "rows": rows,
        "row_count": len(rows),
        "total": res["total"],
        "page_size": page_size,
        "page_num": page_num,
        "paging_mode": "cursor",
        "sql": sql,
    }
