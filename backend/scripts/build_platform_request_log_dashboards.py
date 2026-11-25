#!/usr/bin/env python3
"""平台请求日志：语义数据集 + 五站看板 + 多站组合。

## 由来

口径（13 指标 / 13 维度 / 敏感基线）由 `build_platform_request_log_ontology.py`
落进本体字典；本脚本只做「装配」：把字典里的口径装成数据集，再把数据集装成看板与组合。
中国站与国际站是两个本体（物理表不同构：logstash_cn / logstash），故 20 个意图各建两套
数据集（cn=40 个），五张站板共用各自域的数据集、图表 `config.datasource` 逐站钉执行源。

## 为什么每板 20 张，不是 8 张

只放「调用量 / 成功率 / 耗时」三张卡时，运维会立刻追问「哪个接口在报错、影响多少用户、
哪些地址最热、哪个版本占比高」，而这些问题分别对应不同维度切片，8 张卡回答不了。
本板按 SKILL 剧本 A–G 铺 20 张：6 KPI（调用量/成功率/失败次数/失败接口数/失败影响用户数/
慢请求数）+ 4 分布（响应码/来源通道/请求方法/请求类型）+ 2 趋势（接口/页面，近 7 天按天）
+ 8 排行（慢接口 P95、失败接口、用户失败次数、后端服务、小时分布、客户端版本、
平台版本、地址热榜）。

两个口径要点：① 「失败影响用户数」是全局去重值，**不可**按用户账号分组（那样每行恒为 1），
所以「用户失败次数 Top10」按失败次数排、不按该指标排；② 响应码 90%+ 是 200，全量条形图
会被一根巨柱压成贴地一条线（无法读），故只留**非 200**（`op=ne`）再 Top12；③ KPI 里的
「成功率 / 失败接口数 / 影响用户数」是单值，用 `text_display`（`big_number` 不是真实图型，
悬空类型会落到柱状兜底甚至空白）。

## 布局：显式 pageLayout v3，不靠 position 自动换算

看板查看页的矩形真值源是每张图的 `config.pageLayout`（v3：列数 大屏 64 / 桌面 48 /
平板 24 / 手机 1，行单位恒 4px，间距只作用于列间与避让留白）。**没有 pageLayout 的图会被
当成 `automatic`**：先按 `position` 近似换算，再跑避让下推——同一排里高度不同的卡互相
挤到下一行，看上去就是一条斜阶梯 + 右侧大片空白（2026-11-25 用户反馈“这太丑了”的根因）。
所以本脚本四个断点都写显式 rect，`position` 只当旧画布/导出的近似兼容值：

- 桌面 48 列：KPI 6×8、分布 4×12、趋势与排行 2×24（两侧对齐，不留斜缝）；
- 大屏 64 列：同构放大 1.25 倍（10/15/30 列），左右各留 2 列白边居中；
- 平板 24 列：KPI 3×8（2 行）、分布 2×12（2 行）、趋势与排行单列 24；
- 手机 1 列：纵向堆叠，KPI 紧凑高、图卡统一高。

## 时间窗（不要改成 this_year）

日志是流水表：中国站约 170 万条/天，`time_range=this_year` 会让 SLS 扫描整年分区并撞
`query_gate` 的并发/超时预算。口径固定为：KPI/分布/排行近 24 小时，趋势与慢接口近 7 天按天。
20 张图 = 进板 20 次查询/次刷新（前端 `chartQueryPool` 并发 4，首屏先渲染 `data_cache`，
后台补齐），这是本板刻意选择的密度，不是遗漏。

## 幂等

- 数据集按 `name` 判存在：已存在则按需 `update_dataset`（口径/范围/预设变了才写）；
- 看板按 `name` 判存在：已存在则 `update_dashboard` 对齐状态与说明；
- 图表按 `dashboard_id + name` 判存在：已存在则 `update_chart`（配置/位置/页面布局/数据集），
  不重复插行；改名后的旧图与旧数据集按**白名单收敛**清理（先删图再删集，仍被引用即报错）。
- 组合按 `name` 判存在：已存在则 `set_group_items` / `set_group_roles` 全量覆盖；
- 数据集共享与看板可见角色每次全量重写（`set_dataset_sharing` / `set_dashboard_roles`）。

## 用法

    venv/bin/python -m backend.scripts.build_platform_request_log_dashboards                  # dry-run（含版面自检）
    venv/bin/python -m backend.scripts.build_platform_request_log_dashboards --apply          # 真写
    venv/bin/python -m backend.scripts.build_platform_request_log_dashboards --apply --warm   # 真写并预热首页 data_cache

## 明确不做

- 不改 `adh_menu_registry`：本次不新增页面/入口，组合走既有看板页；
- 只授 admin 与 Operations Manager：analyst 当前未获任何 prd-sls-* 数据源授权，
  授了看板也只会在取数时报「源不在授权范围」——要开给 analyst 须先由管理员补数据源授权；
- 默认**不写** `data_cache`：查看页 GET 只回缓存，不预热时首屏会显示「暂无数据」，
  需人手点“刷新数据”；要一次性取满就加 `--warm`（走与刷新按钮同一条治理链，
  单图失败如实上报并非 0 退出，不静默当已交付）。
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OWNER_ID = 1778154925
IDENTITY = {"user_id": OWNER_ID, "username": "admin", "role": "admin", "workspace_id": 0}

# 可见角色：admin(1) + Operations Manager。analyst(2) 未授 SLS 源，故不授（见 docstring）。
ROLE_IDS = [1, 1791524185193618]

# 两个部署域（与本体一致）：中国站单站；国际站四站同构共用一套数据集。
PROFILES = {
    "cn": dict(
        key="cn", object_key="platform_request_log", suffix="",
        ds_name="prd-sls-cn", sources=["prd-sls-cn"],
        board_label="中国站",
    ),
    "intl": dict(
        key="intl", object_key="platform_request_log_intl", suffix="·国际站",
        ds_name="prd-sls-en",
        sources=["prd-sls-en", "prd-sls-jp", "prd-sls-uk", "prd-sls-eu"],
        board_label="国际站",
    ),
}

# 五张站板：板名后缀 / 执行源业务名 / 使用哪个域的数据集
BOARDS = [
    ("中国站", "prd-sls-cn", "cn"),
    ("国际站", "prd-sls-en", "intl"),
    ("欧洲站", "prd-sls-eu", "intl"),
    ("英国站", "prd-sls-uk", "intl"),
    ("日本站", "prd-sls-jp", "intl"),
]

# 卡片分类（顺序 = CHARTS 顺序）：决定各断点下的宽度/高度与成列规则
KINDS = ["kpi"] * 6 + ["dist"] * 4 + ["trend"] * 2 + ["rank"] * 8

# 每断点的块尺寸 (kind, 每行几张, 列宽, 行高)；列数见 pageLayout v3 约定。
# 块间/块内行距固定 4 行 = 16px，与看板默认间距 `DEFAULT_PAGE_GAP`（screen/desktop/tablet 均 16）
# 同口径：间距一半由 CSS `columnGap` 给（横向），一半由这里的行距给（纵向，rowGap 恒 0）。
GRID = {
    # 桌面 48 列：KPI 6×8=48、分布 4×12=48、趋势/排行 2×24=48 —— 整排对齐，不留斜缝
    "desktop": dict(columns=48, gap=4, blocks=[("kpi", 6, 8, 44), ("dist", 4, 12, 72),
                                                ("trend", 2, 24, 80), ("rank", 2, 24, 90)]),
    # 大屏 64 列：同构放大，左右各留 2 列白边（6×10=60 / 4×15=60 / 2×30=60）
    "screen": dict(columns=64, gap=4, blocks=[("kpi", 6, 10, 56), ("dist", 4, 15, 90),
                                               ("trend", 2, 30, 100), ("rank", 2, 30, 112)]),
    # 平板 24 列：KPI 3×8 两行、分布 2×12 两行、趋势单列 24、排行 2×12（单列 8 张会把板拉得过长）
    "tablet": dict(columns=24, gap=4, blocks=[("kpi", 3, 8, 44), ("dist", 2, 12, 72),
                                               ("trend", 1, 24, 80), ("rank", 2, 12, 90)]),
}
# 手机单列：只有高度有意义（列恒为 1）；行距 3 行 = 12px（手机默认间距）
MOBILE_HEIGHT = {"kpi": 40, "dist": 80, "trend": 80, "rank": 80}
MOBILE_GAP = 3
# 旧画布 position 的近似换算（桌面列宽 30px、行 4px）—— 仅兼容旧画布与导出
DESKTOP_COLUMN_PX = 30
PAGE_ROW_PX = 4


def _grid_rects(kind_list: list[str], bp: str) -> list[dict]:
    """按块尺寸铺格；块内按 per_row 换行，行距 = 行高 + gap（与避让留白同口径）。"""
    spec = GRID[bp]
    out: list[dict] = [dict() for _ in kind_list]
    y = 0
    for kind, per_row, w, h in spec["blocks"]:
        idxs = [i for i, k in enumerate(kind_list) if k == kind]
        if not idxs:
            continue
        row_count = (len(idxs) + per_row - 1) // per_row
        used_w = min(per_row, len(idxs)) * w
        left = max(0, (spec["columns"] - used_w) // 2)   # 未占满时居中留白（大屏各 2 列）
        for j, i in enumerate(idxs):
            out[i] = {"x": left + (j % per_row) * w,
                      "y": y + (j // per_row) * (h + spec["gap"]), "w": w, "h": h}
        y += row_count * h + (row_count - 1) * spec["gap"] + spec["gap"]
    return out


def _page_layout(idx: int) -> dict:
    """四断点显式矩形（v3）。缺任一断点就会回退自动换算 —— 那正是斜阶梯的来源。"""
    layout: dict = {"version": 3}
    for bp in ("screen", "desktop", "tablet"):
        layout[bp] = _grid_rects(KINDS, bp)[idx]
    layout["mobile"] = {
        "x": 0, "y": sum(MOBILE_HEIGHT[KINDS[j]] + MOBILE_GAP for j in range(idx)),
        "w": 1, "h": MOBILE_HEIGHT[KINDS[idx]],
    }
    return layout


def _position(idx: int) -> dict:
    """旧画布坐标：按桌面矩形近似换算（列宽 30px、行 4px）。"""
    rect = _grid_rects(KINDS, "desktop")[idx]
    return {"x": rect["x"] * DESKTOP_COLUMN_PX, "y": rect["y"] * PAGE_ROW_PX,
            "w": rect["w"] * DESKTOP_COLUMN_PX - 16, "h": rect["h"] * PAGE_ROW_PX}


def _assert_layout_sane() -> None:
    """版面自检：四个断点的矩形必须合法且两两不重叠。

    重叠在查看页上就是“卡片压在一起 / 文字被盖住”（CSS grid 会直接把两块叠到同一格），
    提交前算一遍比上线后看截图便宜——dry-run 也跑它。
    """
    for bp in ("screen", "desktop", "tablet", "mobile"):
        cols = GRID[bp]["columns"] if bp != "mobile" else 1
        rects = [_page_layout(i)[bp] for i in range(len(KINDS))]
        for i, a in enumerate(rects):
            if a["x"] < 0 or a["y"] < 0 or a["w"] < 1 or a["h"] < 1 or a["x"] + a["w"] > cols:
                raise SystemExit(f"[fail] {bp} 第 {i} 张卡矩形越界（列数 {cols}）: {a}")
            for j, b in enumerate(rects[:i]):
                if (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"]
                        and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]):
                    raise SystemExit(f"[fail] {bp} 第 {j}/{i} 张卡重叠: {b} vs {a}")

# 20 个意图（名字用中国站字典口径，国际站运行时加「·国际站」后缀，精确命中规范名）。
# window/grain 见 docstring：流水表不用日历年区间。标题统一「指标 · 时间窗」（卡片只有 8 列
# ≈227px 宽，带括号的长标题会被截成「慢请求数（近24小时，≥3秒…」），口径细节写进数据集描述。
CHARTS = [
    # ── 6 KPI（近 24 小时）────────────────────────────────────────────────
    dict(title="接口调用 · 24h", chart_type="big_number_trend",
         measures=["平台接口调用次数"], dimensions=["请求小时"],
         time_column="请求小时", time_grain="hour", time_window="24h"),
    # 成功率是 0~100 的百分数：text_display + valueFormat=percent 才显示成 99.9%；
    # 旧 gauge 的 thresholds 要求 0~1（写 [95,99] 永远不亮），窄卡里也读不出数
    dict(title="成功率 · 24h", chart_type="text_display",
         measures=["平台接口成功率"], time_window="24h",
         extra={"valueFormat": "percent", "showComparison": False, "valueFontSize": 36}),
    dict(title="失败请求 · 24h", chart_type="big_number_trend",
         measures=["平台接口失败次数"], dimensions=["请求小时"],
         time_column="请求小时", time_grain="hour", time_window="24h"),
    # 两个波及面口径用单值卡：按小时分解后相加不等于全天去重值，不挂趋势避免误相加
    dict(title="失败接口 · 24h", chart_type="text_display",
         measures=["平台失败接口数"], time_window="24h",
         extra={"showComparison": False, "valueFontSize": 36}),
    dict(title="影响用户 · 24h", chart_type="text_display",
         measures=["平台失败影响用户数"], time_window="24h",
         extra={"showComparison": False, "valueFontSize": 36}),
    dict(title="慢请求 · 24h", chart_type="big_number_trend",
         measures=["平台慢请求数"], dimensions=["请求小时"],
         time_column="请求小时", time_grain="hour", time_window="24h"),
    # ── 4 分布（近 24 小时）──────────────────────────────────────────────
    # 响应码 90%+ 是 200：全量条形图会被一根巨柱压成贴地线（用户实测“读不出”），
    # 所以只留**非 200**（op=ne）再 Top12 —— 这张卡回答“哪些失败码在发生”。
    dict(title="非200响应码 · 24h", chart_type="bar",
         measures=["平台接口调用次数"], dimensions=["响应码"], time_window="24h",
         filters=[{"dim": "响应码", "op": "ne", "value": 200}],
         limit=12, order_by=[{"by": "平台接口调用次数", "desc": True}]),
    dict(title="来源通道 · 24h", chart_type="pie",
         measures=["平台请求总数"], dimensions=["来源应用通道"], time_window="24h",
         limit=20, order_by=None),
    dict(title="请求方法 · 24h", chart_type="bar",
         measures=["平台接口调用次数"], dimensions=["请求方法"], time_window="24h",
         limit=10, order_by=[{"by": "平台接口调用次数", "desc": True}]),
    dict(title="请求类型 · 24h", chart_type="pie",
         measures=["平台请求总数"], dimensions=["请求类型"], time_window="24h",
         limit=10, order_by=None),
    # ── 2 趋势（近 7 天按天）──────────────────────────────────────────────
    dict(title="接口调用趋势 · 7d", chart_type="timeseries_line",
         measures=["平台接口调用次数"], dimensions=["请求日期"],
         time_column="请求日期", time_grain="day", time_window="7d"),
    dict(title="页面访问趋势 · 7d", chart_type="timeseries_line",
         measures=["平台页面访问次数"], dimensions=["请求日期"],
         time_column="请求日期", time_grain="day", time_window="7d"),
    # ── 8 排行 / 明细 ────────────────────────────────────────────────────
    dict(title="慢接口 P95 · 7d", chart_type="bar",
         measures=["平台接口P95耗时"], dimensions=["请求URL"], time_window="7d",
         limit=10, order_by=[{"by": "平台接口P95耗时", "desc": True}]),
    dict(title="失败接口 Top10 · 24h", chart_type="bar",
         measures=["平台接口失败次数"], dimensions=["请求URL"], time_window="24h",
         limit=10, order_by=[{"by": "平台接口失败次数", "desc": True}]),
    # 注意：这里按「失败次数」排，不按「失败影响用户数」——后者按用户分组恒为 1；
    # 且必须排掉空账号：未登录/开放接口的 userCode 为空，会聚成一组把真实用户挤下去
    dict(title="用户失败次数 Top10 · 24h", chart_type="bar",
         measures=["平台接口失败次数"], dimensions=["用户账号"], time_window="24h",
         filters=[{"dim": "用户账号", "op": "is_not_null"}],
         limit=10, order_by=[{"by": "平台接口失败次数", "desc": True}]),
    # 小时轴按时间升序（否则条形顺序由 planner 返回顺序决定，相邻两天能排反）
    dict(title="请求小时分布 · 24h", chart_type="bar",
         measures=["平台请求总数"], dimensions=["请求小时"],
         time_column="请求小时", time_grain="hour", time_window="24h",
         limit=30, order_by=[{"by": "请求小时", "desc": False}]),
    dict(title="后端服务 · 24h", chart_type="bar",
         measures=["平台接口调用次数"], dimensions=["后端服务"], time_window="24h",
         limit=10, order_by=[{"by": "平台接口调用次数", "desc": True}]),
    dict(title="客户端版本 · 7d", chart_type="bar",
         measures=["平台请求总数"], dimensions=["请求客户端版本"], time_window="7d",
         limit=10, order_by=[{"by": "平台请求总数", "desc": True}]),
    dict(title="平台版本 · 7d", chart_type="bar",
         measures=["平台请求总数"], dimensions=["平台版本"], time_window="7d",
         limit=10, order_by=[{"by": "平台请求总数", "desc": True}]),
    dict(title="请求地址热榜 · 24h", chart_type="bar",
         measures=["平台请求总数"], dimensions=["请求URL"], time_window="24h",
         limit=10, order_by=[{"by": "平台请求总数", "desc": True}]),
]

# 本脚本命名的数据集前缀：白名单收敛只碰自己造的，别人的同名开头数据不误删
DATASET_PREFIX = "平台请求日志 · "

# 卡片分类必须与 CHARTS 一一对应（顺序 = 页面顺序），错位会让布局落到别的卡上
assert len(KINDS) == len(CHARTS), f"KINDS({len(KINDS)}) 与 CHARTS({len(CHARTS)}) 数量必须一致"

GROUP_NAME = "平台请求日志分析（多站）"


def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dict_name(profile: dict, cn_name: str) -> str:
    """中国站口径名 → 该部署域的字典规范名（国际站加「·国际站」）。"""
    return f"{cn_name}{profile['suffix']}"


def _dashboard_name(site_label: str) -> str:
    return f"平台请求日志分析 · {site_label}"


def _dataset_name(profile: dict, chart: dict) -> str:
    suffix = "（国际站）" if profile["key"] == "intl" else ""
    return f"{DATASET_PREFIX}{chart['title']}{suffix}"


def _expected_dataset_names() -> set[str]:
    """本轮要保留的数据集全集（白名单收敛用）：不在集合里的旧集扫尾删掉。"""
    return {_dataset_name(p, c) for p in PROFILES.values() for c in CHARTS}


def _intent(profile: dict, chart: dict) -> dict:
    """语义意图（与 `dataset_service._query_semantic` 认的字段同名）。"""
    intent = {
        "object": profile["object_key"],
        "metrics": [_dict_name(profile, m) for m in chart["measures"]],
    }
    if chart.get("dimensions"):
        intent["dimensions"] = [_dict_name(profile, d) for d in chart["dimensions"]]
    for key in ("time_column", "time_grain", "time_window"):
        if chart.get(key):
            value = chart[key]
            if key == "time_column":
                value = _dict_name(profile, value)
            intent[key] = value
    if chart.get("filters"):
        intent["filters"] = [
            {"dim": _dict_name(profile, f["dim"]), "op": f["op"],
             **(({"value": f["value"]}) if "value" in f else {})}
            for f in chart["filters"]
        ]
    return intent


def _xy(profile: dict, chart: dict) -> tuple[str, str]:
    """图表 X/Y 列：X 取首个分组维度（无维度时退化成指标本身），Y 恒为首个指标。"""
    dims = chart.get("dimensions") or []
    x = _dict_name(profile, dims[0]) if dims else _dict_name(profile, chart["measures"][0])
    y = _dict_name(profile, chart["measures"][0])
    return x, y


def _dataset_payload(profile: dict, chart: dict) -> dict:
    x, y = _xy(profile, chart)
    dims = [_dict_name(profile, d) for d in chart.get("dimensions") or []]
    return {
        "name": _dataset_name(profile, chart),
        "description": (
            f"「{_dashboard_name(profile['board_label'])}」{chart['title']} 的口径集："
            f"指标 {'、'.join(_dict_name(profile, m) for m in chart['measures'])}"
            + (f"；维度 {'、'.join(dims)}" if dims else "")
            + f"；时间窗 {chart.get('time_window') or '默认'}。"
            "五站看板共用本数据集，执行源由各图 config.datasource 逐站钉。"),
        "object_key": profile["object_key"],
        "available_datasources": list(profile["sources"]),
        "chart_type": chart["chart_type"],
        "chart_preset": {"xCol": x, "yCol": y, "intent": _intent(profile, chart)},
        "paginable": 0,
        "status": "active",
    }


def _chart_config(profile: dict, chart: dict, dataset_name: str, site_source: str,
                  idx: int) -> dict:
    x, y = _xy(profile, chart)
    cfg = {
        "measures": [_dict_name(profile, m) for m in chart["measures"]],
        "dimensions": [_dict_name(profile, d) for d in chart.get("dimensions") or []],
        "xCol": x,
        "yCol": y,
        "dataset_name": dataset_name,
        # 钉执行源（业务名）：数据集可用范围 ∩ 角色授权源之内，服务端不猜源
        "datasource": site_source,
        "limit": int(chart.get("limit") or 500),
    }
    for key in ("time_window", "time_grain"):
        if chart.get(key):
            cfg[key] = chart[key]
    if chart.get("time_column"):
        cfg["time_column"] = _dict_name(profile, chart["time_column"])
    if chart.get("order_by"):
        cfg["order"] = [
            {"by": _dict_name(profile, o["by"]), "desc": bool(o.get("desc"))}
            for o in chart["order_by"]
        ]
    if chart.get("filters"):
        cfg["filters"] = [
            {"dim": _dict_name(profile, f["dim"]), "op": f["op"],
             **(({"value": f["value"]}) if "value" in f else {})}
            for f in chart["filters"]
        ]
    cfg.update(chart.get("extra") or {})
    # 页面布局写进图表 config（v3）：这是查看页/缩略图/编辑器的**矩形真值源**。
    # 缺了它就会被当成 automatic —— 按 position 近似换算再跑避让下推，
    # 同排高度不同的卡互相挤成斜阶梯（用户反馈“这太丑了”的根因）。
    # 放在 extra 之后写：布局由脚本统一给定，不允许被 extra 覆盖。
    cfg["pageLayout"] = _page_layout(idx)
    return cfg


def _chart_payload(profile: dict, chart: dict, dataset_id: int, dataset_name: str,
                   site_source: str, idx: int) -> dict:
    return {
        "name": chart["title"],
        "chart_type": chart["chart_type"],
        "source_type": "dataset",
        "source_id": dataset_id,
        "query_source": "dataset",
        "config": _chart_config(profile, chart, dataset_name, site_source, idx),
        # position 只是旧画布/导出的兼容近似值（真正的观感看 config.pageLayout）
        "position": _position(idx),
    }


# ── 落地（幂等） ──────────────────────────────────────────────────────────


def _existing_datasets() -> dict:
    from backend.common.db import execute_query
    rows = execute_query("SELECT id, name FROM adh_datasets")
    return {str(r["name"]): int(r["id"]) for r in rows}


def _existing_dashboards() -> dict:
    from backend.common.db import execute_query
    rows = execute_query("SELECT id, name FROM adh_dashboards")
    return {str(r["name"]): int(r["id"]) for r in rows}


def _existing_charts(dashboard_id: int) -> dict:
    from backend.common.db import execute_query
    rows = execute_query("SELECT id, name FROM adh_charts WHERE dashboard_id = %s",
                         (dashboard_id,))
    return {str(r["name"]): int(r["id"]) for r in rows}


def _existing_groups() -> dict:
    from backend.common.db import execute_query
    rows = execute_query("SELECT id, name FROM adh_dashboard_groups")
    return {str(r["name"]): int(r["id"]) for r in rows}


def _json_of(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dataset_stale(current: dict, wanted: dict) -> dict:
    """只回需要改的字段：避免每次 apply 都写一遍（并掩盖真正变化）。"""
    patch: dict = {}
    if str(current.get("object_key") or "") != wanted["object_key"]:
        patch["object_key"] = wanted["object_key"]
    cur_sources = sorted(_json_of(current.get("available_datasources"), []) or [])
    if cur_sources != sorted(wanted["available_datasources"]):
        patch["available_datasources"] = wanted["available_datasources"]
    cur_preset = _json_of(current.get("chart_preset"), {}) or {}
    if cur_preset != wanted["chart_preset"]:
        patch["chart_preset"] = wanted["chart_preset"]
    if str(current.get("chart_type") or "") != wanted["chart_type"]:
        patch["chart_type"] = wanted["chart_type"]
    if str(current.get("status") or "") != "active":
        patch["status"] = "active"
    return patch


def main() -> None:
    apply = "--apply" in sys.argv
    _log(f"=== 平台请求日志 数据集+五站看板+组合（{'APPLY' if apply else 'DRY-RUN'}）{_now()}")

    from backend.core.role_service import role_service
    from backend.modules.viz.services import dataset_service
    from backend.modules.viz.services.dashboard_service import (
        chart_service, create_group, dashboard_service, set_group_items, set_group_roles)

    # 数据源 id（编译基准源 = 各域主站源）
    from backend.common.db import execute_query
    rows = execute_query(
        "SELECT id, name FROM adh_datasources WHERE name IN "
        "('prd-sls-cn','prd-sls-en','prd-sls-jp','prd-sls-uk','prd-sls-eu')")
    ds_ids_by_name = {str(r["name"]): int(r["id"]) for r in rows}
    missing = [n for _, n, _ in BOARDS if n not in ds_ids_by_name]
    if missing:
        raise SystemExit(f"[fail] 数据源缺失: {missing}")

    ds_ids = _existing_datasets()
    dash_ids = _existing_dashboards()
    group_ids = _existing_groups()

    # ── 计划 ──
    plan_datasets = []   # (profile, chart, wanted, existing_id)
    for pkey in ("cn", "intl"):
        profile = PROFILES[pkey]
        for chart in CHARTS:
            wanted = _dataset_payload(profile, chart)
            plan_datasets.append((profile, chart, wanted, ds_ids.get(wanted["name"])))

    _log(f"[1] 数据集：{len(CHARTS)} 意图 × 2 部署域 = {len(plan_datasets)} 个"
         f"（已有 {sum(1 for x in plan_datasets if x[3])}）")

    plan_dashboards = []
    for site_label, site_source, pkey in BOARDS:
        name = _dashboard_name(site_label)
        plan_dashboards.append((site_label, site_source, pkey, name, dash_ids.get(name)))
    _log(f"[2] 看板：{len(plan_dashboards)} 张（每板 {len(CHARTS)} 图）")

    total_charts = 0
    keep_titles_for_log = {c["title"] for c in CHARTS}
    for site_label, _src, _pkey, name, did in plan_dashboards:
        have = _existing_charts(did) if did else {}
        total_charts += len(CHARTS)
        matched = len(set(have) & keep_titles_for_log)
        stale = len(have) - matched
        _log(f"    {'已存在 #' + str(did) if did else '+'} {name}: 同名卡 {matched}/{len(CHARTS)}"
             + (f"，待收敛旧卡 {stale} 张" if stale else ""))
    _log(f"[3] 图表合计：{total_charts} 张")
    _assert_layout_sane()
    _log("    版面自检：四断点矩形合法、无重叠")

    gid = group_ids.get(GROUP_NAME)
    _log(f"[4] 组合：{'已存在 #' + str(gid) if gid else '+'} {GROUP_NAME}"
         f"（{len(BOARDS)} 张板，角色 {ROLE_IDS}）")

    if not apply:
        _log("\n=== DRY-RUN 结束（未写任何一行）。加 --apply 落库。")
        return

    # ── 0. 白名单收敛（改名/改口径后不留孤儿）──
    # 不手写退役名单：凡本板上不在当前 CHARTS 的图都删（名字就是对齐键）；
    # 再删不在当前期望集合的自建数据集——仍被引用会抛错（说明图没对齐，先修图再删，
    # 不得静默跳过，否则旧图会挂在已删集上变成永久空白卡）。
    _log("\n=== APPLY：白名单收敛")
    keep_titles = {c["title"] for c in CHARTS}
    removed_charts = 0
    for _site, _src, _pkey, name, did in plan_dashboards:
        if not did:
            continue
        for old, cid in _existing_charts(did).items():
            if old in keep_titles:
                continue
            chart_service.delete_chart(did, cid)
            removed_charts += 1
            _log(f"    - 删除旧图 {name} / {old}")
    removed_sets = 0
    keep_sets = _expected_dataset_names()
    for old_ds, oid in ds_ids.items():
        if not old_ds.startswith(DATASET_PREFIX) or old_ds in keep_sets:
            continue
        dataset_service.delete_dataset(oid, IDENTITY)
        removed_sets += 1
        _log(f"    - 删除旧数据集 #{oid} {old_ds}")
    _log(f"    收敛：删图 {removed_charts} 张，删集 {removed_sets} 个")

    # ── 1. 数据集 ──
    _log("\n=== APPLY：数据集")
    dataset_ids: dict[str, int] = {}
    for profile, chart, wanted, eid in plan_datasets:
        name = wanted["name"]
        base = ds_ids_by_name[profile["ds_name"]]
        payload = dict(wanted, datasource_id=base)
        if eid:
            current = dataset_service.get_dataset(eid) or {}
            patch = _dataset_stale(current, payload)
            if patch:
                dataset_service.update_dataset(eid, patch, IDENTITY)
                _log(f"    ~ 更新 #{eid} {name}: {sorted(patch)}")
            else:
                _log(f"    = 无变化 #{eid} {name}")
            dataset_ids[name] = eid
        else:
            saved = dataset_service.create_dataset(payload, IDENTITY, origin="manual")
            dataset_ids[name] = int(saved["id"])
            _log(f"    + 新建 #{saved['id']} {name}")

    # 数据集按角色共享（admin 建 = workspace，显式补 role_ids 供运营查看）
    for name, ds_id in dataset_ids.items():
        dataset_service.set_dataset_sharing(
            ds_id, {"visibility": "workspace", "role_ids": ROLE_IDS}, IDENTITY)

    # ── 2. 看板 ──
    _log("\n=== APPLY：看板")
    dashboard_ids: dict[str, int] = {}
    for site_label, site_source, pkey, name, did in plan_dashboards:
        profile = PROFILES[pkey]
        desc = (f"平台自身的请求日志（后端接口调用 + 网页端页面访问）：调用量、成功率、失败面、"
                f"耗时与慢请求、用户/设备规模，以及按响应码/接口/服务/版本/小时的分布。"
                f"站点：{site_label}（执行源 {site_source}）。口径全部来自「数据集」页，本板不写 SQL；"
                f"中国站与国际站是两个本体，口径一致但不得相加；同类看板见「{GROUP_NAME}」")
        if did:
            dashboard_service.update_dashboard(
                did, {"description": desc, "status": "enabled"}, OWNER_ID)
            dashboard_ids[name] = did
            _log(f"    = 已存在 #{did} {name}")
        else:
            new_id = dashboard_service.create_dashboard({
                "name": name,
                "description": desc,
                "layout": [],
                "workspace_id": 0,
                "status": "enabled",
            }, OWNER_ID)
            dashboard_ids[name] = new_id
            _log(f"    + 新建 #{new_id} {name}")

    # ── 3. 图表 ──
    _log("\n=== APPLY：图表")
    created = updated = 0
    for site_label, site_source, pkey, name, _did in plan_dashboards:
        profile = PROFILES[pkey]
        did = dashboard_ids[name]
        have = _existing_charts(did)
        for idx, chart in enumerate(CHARTS):
            ds_name = _dataset_name(profile, chart)
            ds_id = dataset_ids[ds_name]
            payload = _chart_payload(profile, chart, ds_id, ds_name, site_source, idx)
            cid = have.get(chart["title"])
            if cid:
                chart_service.update_chart(did, cid, payload, IDENTITY)
                updated += 1
            else:
                chart_service.create_chart(did, payload, IDENTITY)
                created += 1
        _log(f"    {name}: 共 {len(CHARTS)} 张（新增/更新按名字对齐）")
    _log(f"    合计：新建 {created} 张，对齐更新 {updated} 张")

    # ── 4. 组合 + 可见角色 ──
    _log("\n=== APPLY：组合与可见角色")
    if not gid:
        gid = create_group(GROUP_NAME, "平台请求日志的多站看板（中国站 / 国际站 / 欧洲站 / 英国站 / 日本站）")
        _log(f"    + 新建组合 #{gid} {GROUP_NAME}")
    else:
        _log(f"    = 已存在组合 #{gid} {GROUP_NAME}")
    members = [dashboard_ids[_dashboard_name(s)] for s, _src, _pkey in BOARDS]
    result = set_group_items(gid, members)
    if result["dropped"]:
        raise SystemExit(f"[fail] 组合成员登记被丢弃: {result['dropped']}")
    set_group_roles(gid, ROLE_IDS)
    for name, did in dashboard_ids.items():
        role_service.set_dashboard_roles(did, ROLE_IDS)
    _log(f"    成员 {result['count']}/{len(BOARDS)}，看板可见角色与组合角色 = {ROLE_IDS}")

    # ── 收尾核对 ──
    _log("\n=== 落库后核对")
    n_ds_expect = len(CHARTS) * 2
    got_ds = execute_query(
        "SELECT COUNT(*) AS n FROM adh_datasets WHERE name LIKE '平台请求日志 · %' AND status='active'",
        fetchone=True)["n"]
    per_board = []
    for site_label, _src, _pkey, name, _did in plan_dashboards:
        did = dashboard_ids[name]
        cnt = int(execute_query("SELECT COUNT(*) AS n FROM adh_charts WHERE dashboard_id=%s",
                                (did,), fetchone=True)["n"])
        per_board.append(cnt)
    members_cnt = int(execute_query(
        "SELECT COUNT(*) AS n FROM adh_dashboard_group_items WHERE group_id=%s",
        (gid,), fetchone=True)["n"])
    _log(f"    数据集 {got_ds}/{n_ds_expect}，每板图数 {per_board}，组合成员 {members_cnt}/{len(BOARDS)}")
    if int(got_ds) != n_ds_expect:
        raise SystemExit("[fail] 数据集数量不符")
    if any(c != len(CHARTS) for c in per_board):
        raise SystemExit("[fail] 存在图数不足的看板")
    if members_cnt != len(BOARDS):
        raise SystemExit("[fail] 组合成员数不符")

    # ── 5. 预热缓存（可选：--warm）──
    # 查看页 GET 只回 data_cache，不会自己重查：不预热时首屏大片「暂无数据」，
    # 要人手点“刷新数据”才出数——那就是用户截图里的“丑”。预热走的是与刷新按钮
    # 同一条治理链路（refresh_all_charts → query_dataset），不过任何旁路；
    # 单板/单图失败一律如实上报并以非 0 退出，不得静默当成“已交付”。
    if "--warm" in sys.argv:
        _log("\n=== WARM：整板重查，把首屏要用的 data_cache 一次取满")
        failed = 0
        for site_label, _src, _pkey, name, _did in plan_dashboards:
            did = dashboard_ids[name]
            try:
                res = chart_service.refresh_all_charts(
                    did, user_id=OWNER_ID, workspace_id=0, username="admin")
            except Exception as exc:  # noqa: BLE001  逐板如实上报（不吞错）
                _log(f"    ! {name} 整板重查失败：{exc}")
                failed += 1
                continue
            charts = res.get("charts") or {}
            errors = {cid: r.get("error") for cid, r in charts.items()
                      if isinstance(r, dict) and r.get("error")}
            empty = [cid for cid, r in charts.items()
                     if isinstance(r, dict) and not r.get("error") and not r.get("rows")]
            _log(f"    {site_label}：{len(charts)} 张，失败 {len(errors)}，空结果 {len(empty)}")
            for cid, err in errors.items():
                _log(f"      ! chart#{cid} {err}")
            for cid in empty:
                _log(f"      ? chart#{cid} 查询成功但 0 行（该卡会显示“暂无数据”）")
            failed += len(errors)
        if failed:
            raise SystemExit(f"[fail] 预热有 {failed} 处失败（见上）；首屏会缺数，先修再交付")


if __name__ == "__main__":
    main()