#!/usr/bin/env python3
"""联耀：案例 / 订单 / 患者域 —— 语义数据集 + 五站看板 + 域组合。

## 由来

口径（指标 / 维度 / 枚举标签 / 删除过滤 / 敏感基线）由 `build_lianyao_case_order_patient.py`
落进本体字典；本脚本只做「装配」：把字典里的口径装成数据集，再把数据集装成看板与组合。
Excel 原型「④ 看板」页给的是**五站各一套**，按拍板口径落成 15 张板 + 3 个组合
（同一域的 5 站板同属一个组合，顺序 cn→int→eu→uk→jp）。

## 为什么是 24 个数据集，不是 120 个

图表 `query_source='dataset'` 时可以在数据集「可用数据源范围 ∩ 角色授权源」内**钉**
一个执行源（`config.datasource` 存业务名，与本体 deploy_sites 同口径）。所以
「指标 × 维度 × 时间窗」只需要 1 个语义数据集，5 个站点各 1 张图复用同一数据集、
只在图上钉各自的站 —— 数据集数 = 域(3) × 意图(8) = 24，而不是 24×5。

## 幂等

- 数据集按 `name` 判存在：已存在则按需 `update_dataset`（口径/范围/预设变了才写）；
- 看板按 `name` 判存在：已存在则 `update_dashboard` 对齐状态与说明；
- 图表按 `dashboard_id + name` 判存在：已存在则 `update_chart`（配置/位置/数据集），
  不重复插行，避免同一张板刷出两套同名卡；
- 组合按 `name` 判存在：已存在则 `set_group_items` / `set_group_roles` 全量覆盖。

## 用法

    venv/bin/python -m backend.scripts.build_lianyao_case_order_patient_dashboards            # dry-run
    venv/bin/python -m backend.scripts.build_lianyao_case_order_patient_dashboards --apply    # 真写

## 明确不做

- 不改 `adh_menu_registry`：本次不新增页面/入口，组合走既有看板页；
- 只授管理员角色（role_id=1，与存量联耀组合一致）；要开放给运营/销售需另补
  `adh_role_dashboard_access` + `adh_role_dataset_access` + 组合角色；
- 不写图表的 `data_cache`：首屏由服务端按查看者身份现取（治理链一步不少）。
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

# 数据集编译/执行基准源（cn）；真正的执行源由图表 config.datasource 逐站钉
BASE_DATASOURCE_ID = 1780478236183

# 站点顺序 = 组合内看板顺序 = 拍板的 cn→int→eu→uk→jp
SITES = [
    ("中国站", "prd-cn"),
    ("国际站", "prd-en"),
    ("欧洲站", "prd-eu"),
    ("英国站", "prd-uk"),
    ("日本站", "prd-jp"),
]
SITE_DATASOURCES = [name for _, name in SITES]

# 每张板的 8 个卡片槽位（1920 宽栅格）：上排 4 个 KPI/仪表，下三排两两并排
SLOTS = [
    (0, 0, 480, 190), (480, 0, 480, 190), (960, 0, 480, 190), (1440, 0, 480, 190),
    (0, 200, 960, 430), (960, 200, 960, 430),
    (0, 640, 960, 430), (960, 640, 960, 430),
]

# 域定义：每域 8 个意图，顺序即看板卡片顺序（槽位 0-3 = KPI/仪表，4 = 时序，5 = 饼，6-7 = 柱）
DOMAINS = [
    dict(
        key="case", label="案例域", object_key="case",
        desc="口扫案例的生命周期与结构：规模、完成、转订单、存储、状态/类型/医院分布",
        charts=[
            dict(title="案例数量（本年累计）", chart_type="big_number_trend",
                 measures=["案例数量"], dimensions=["创建日期"],
                 time_column="创建日期", time_grain="month", time_range="this_year"),
            dict(title="已完成案例数（本年累计）", chart_type="big_number_trend",
                 measures=["已完成案例数"], dimensions=["创建日期"],
                 time_column="创建日期", time_grain="month", time_range="this_year"),
            dict(title="有订单案例占比", chart_type="gauge",
                 measures=["有订单案例占比"], extra={"maxValue": 1, "thresholds": [0.5, 0.8]}),
            dict(title="案例总存储GB", chart_type="big_number",
                 measures=["案例总存储GB"]),
            dict(title="月度案例数量", chart_type="timeseries_line",
                 measures=["案例数量"], dimensions=["创建日期"],
                 time_column="创建日期", time_grain="month", time_range="this_year"),
            dict(title="案例状态构成", chart_type="pie",
                 measures=["案例数量"], dimensions=["案例状态"], order_by=None),
            dict(title="案例类型分布", chart_type="bar",
                 measures=["案例数量"], dimensions=["案例类型"], order_by=None),
            dict(title="就诊医院Top10", chart_type="bar",
                 measures=["案例数量"], dimensions=["就诊医院"], limit=10,
                 order_by=[{"by": "案例数量", "desc": True}]),
        ]),
    dict(
        key="order", label="订单域", object_key="order",
        desc="诊所→技工所订单的流转与产能：规模、完成、接/拒单、发货积压、状态/技工所分布",
        charts=[
            dict(title="订单数（本年累计）", chart_type="big_number_trend",
                 measures=["订单数"], dimensions=["下单时间"],
                 time_column="下单时间", time_grain="month", time_range="this_year"),
            dict(title="已完成订单数（本年累计）", chart_type="big_number_trend",
                 measures=["已完成订单数"], dimensions=["下单时间"],
                 time_column="下单时间", time_grain="month", time_range="this_year"),
            dict(title="接单率", chart_type="gauge",
                 measures=["接单率"], extra={"maxValue": 1, "thresholds": [0.5, 0.8]}),
            dict(title="拒单率", chart_type="gauge",
                 measures=["拒单率"], extra={"maxValue": 1, "thresholds": [0.1, 0.3]}),
            dict(title="待发货积压订单数", chart_type="big_number",
                 measures=["待发货积压订单数"]),
            dict(title="月度订单数", chart_type="timeseries_line",
                 measures=["订单数"], dimensions=["下单时间"],
                 time_column="下单时间", time_grain="month", time_range="this_year"),
            dict(title="订单状态构成", chart_type="pie",
                 measures=["订单数"], dimensions=["订单状态"], order_by=None),
            dict(title="承接技工所Top10", chart_type="bar",
                 measures=["订单数"], dimensions=["接单技工所"], limit=10,
                 order_by=[{"by": "订单数", "desc": True}]),
        ]),
    dict(
        key="patient", label="患者域", object_key="patient",
        desc="患者档案的规模与结构：档案/人数、新增、沉默、性别/省份/年龄分布",
        charts=[
            dict(title="患者档案数（本年累计）", chart_type="big_number_trend",
                 measures=["患者档案数"], dimensions=["登记日期"],
                 time_column="登记日期", time_grain="month", time_range="this_year"),
            dict(title="患者人数（全量去重）", chart_type="big_number",
                 measures=["患者人数"]),
            dict(title="新增患者数（本年）", chart_type="big_number",
                 measures=["新增患者数"], time_range="this_year"),
            dict(title="沉默患者数", chart_type="big_number",
                 measures=["沉默患者数"]),
            dict(title="月度新增患者数", chart_type="timeseries_line",
                 measures=["新增患者数"], dimensions=["登记日期"],
                 time_column="登记日期", time_grain="month", time_range="this_year"),
            dict(title="病人性别构成", chart_type="pie",
                 measures=["患者档案数"], dimensions=["患者性别"], order_by=None),
            dict(title="患者省份Top10", chart_type="bar",
                 measures=["患者档案数"], dimensions=["患者省份"], limit=10,
                 order_by=[{"by": "患者档案数", "desc": True}]),
            dict(title="年龄分布", chart_type="bar",
                 measures=["患者档案数"], dimensions=["患者年龄"], limit=100),
        ]),
]

# 组合：一个域一个组合，内含该域 5 站看板
GROUP_ROLE_IDS = [1]


def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dashboard_name(domain: dict, site_label: str) -> str:
    return f"{domain['label']}分析 · {site_label}"


def _group_name(domain: dict) -> str:
    return f"{domain['label']}分析（联耀五站）"


def _dataset_name(domain: dict, chart: dict) -> str:
    return f"联耀{domain['label']} · {chart['title']}"


def _intent(domain: dict, chart: dict) -> dict:
    """语义意图（与 `dataset_service._query_semantic` 认的字段同名）。"""
    intent = {"object": domain["object_key"], "metrics": list(chart["measures"])}
    if chart.get("dimensions"):
        intent["dimensions"] = list(chart["dimensions"])
    for key in ("time_column", "time_grain", "time_range"):
        if chart.get(key):
            intent[key] = chart[key]
    return intent


def _xy(domain: dict, chart: dict) -> tuple[str, str]:
    """图表 X/Y 列：X 取首个分组维度（无维度时退化成指标本身），Y 恒为首个指标。"""
    dims = chart.get("dimensions") or []
    return (dims[0] if dims else chart["measures"][0]), chart["measures"][0]


def _dataset_payload(domain: dict, chart: dict) -> dict:
    x, y = _xy(domain, chart)
    return {
        "name": _dataset_name(domain, chart),
        "description": (f"「{_group_name(domain)}」{chart['title']} 的口径集："
                        f"指标 {'、'.join(chart['measures'])}"
                        + (f"；维度 {'、'.join(chart['dimensions'])}" if chart.get("dimensions") else "")
                        + "。五站看板共用本数据集，执行源由各图 config.datasource 逐站钉。"),
        "object_key": domain["object_key"],
        "datasource_id": BASE_DATASOURCE_ID,
        "available_datasources": SITE_DATASOURCES,
        "chart_type": chart["chart_type"],
        "chart_preset": {
            "xCol": x,
            "yCol": y,
            "intent": _intent(domain, chart),
        },
        "paginable": 0,
        "status": "active",
    }


def _chart_config(domain: dict, chart: dict, dataset_name: str, site_source: str) -> dict:
    x, y = _xy(domain, chart)
    cfg = {
        "measures": list(chart["measures"]),
        "dimensions": list(chart.get("dimensions") or []),
        "xCol": x,
        "yCol": y,
        "dataset_name": dataset_name,
        # 钉执行源（业务名）：数据集可用范围 ∩ 角色授权源之内，服务端不猜源
        "datasource": site_source,
        "limit": int(chart.get("limit") or 500),
    }
    for key in ("time_column", "time_grain", "time_range"):
        if chart.get(key):
            cfg[key] = chart[key]
    if chart.get("order_by"):
        cfg["order"] = chart["order_by"]
    cfg.update(chart.get("extra") or {})
    return cfg


def _chart_payload(domain: dict, chart: dict, dataset_id: int, dataset_name: str,
                   site_source: str, idx: int) -> dict:
    x, y, w, h = SLOTS[idx]
    return {
        "name": chart["title"],
        "chart_type": chart["chart_type"],
        "source_type": "dataset",
        "source_id": dataset_id,
        "query_source": "dataset",
        "config": _chart_config(domain, chart, dataset_name, site_source),
        "position": {"x": x, "y": y, "w": w, "h": h},
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
    """行字段可能是已解析对象也可能是 JSON 文本（get_dataset 直出库行）。"""
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dataset_stale(current: dict, wanted: dict) -> dict:
    """已存在数据集与期望口径的差异（只比会改口径的字段）。"""
    patch: dict = {}
    if str(current.get("object_key") or "") != wanted["object_key"]:
        patch["object_key"] = wanted["object_key"]
    if int(current.get("datasource_id") or 0) != int(wanted["datasource_id"]):
        patch["datasource_id"] = wanted["datasource_id"]
    have_avail = [str(x) for x in _json_of(current.get("available_datasources"), [])]
    if sorted(have_avail) != sorted(wanted["available_datasources"]):
        patch["available_datasources"] = wanted["available_datasources"]
    if str(current.get("chart_type") or "") != wanted["chart_type"]:
        patch["chart_type"] = wanted["chart_type"]
    have_preset = _json_of(current.get("chart_preset"), {})
    if json.dumps(have_preset, sort_keys=True, ensure_ascii=False) != \
            json.dumps(wanted["chart_preset"], sort_keys=True, ensure_ascii=False):
        patch["chart_preset"] = wanted["chart_preset"]
    if str(current.get("status") or "") != "active":
        patch["status"] = "active"
    return patch


def main() -> None:
    apply = "--apply" in sys.argv
    _log(f"=== 联耀案例/订单/患者域 数据集+看板+组合（{'APPLY' if apply else 'DRY-RUN'}）{_now()}")
    _log(f"数据源基准 prd-cn id={BASE_DATASOURCE_ID}；可用范围 {SITE_DATASOURCES}")

    from backend.modules.viz.services import dataset_service
    from backend.modules.viz.services.dashboard_service import (
        chart_service, create_group, dashboard_service, set_group_items, set_group_roles)

    ds_ids = _existing_datasets()
    dash_ids = _existing_dashboards()
    group_ids = _existing_groups()

    _log(f"\n[1] 数据集：{sum(len(d['charts']) for d in DOMAINS)} 个语义集（{len(DOMAINS)} 域 × 8 意图）")
    plan_datasets = []      # (domain, chart, wanted, existing_id)
    for domain in DOMAINS:
        for chart in domain["charts"]:
            wanted = _dataset_payload(domain, chart)
            eid = ds_ids.get(wanted["name"])
            plan_datasets.append((domain, chart, wanted, eid))
            if eid:
                _log(f"    已存在 #{eid} {wanted['name']}")
            else:
                _log(f"    + 新建 {wanted['name']}（{wanted['object_key']} / "
                     f"{'/'.join(chart['measures'])}"
                     + (f" × {'/'.join(chart['dimensions'])}" if chart.get("dimensions") else "")
                     + "）")

    _log(f"\n[2] 看板：{len(DOMAINS)} 域 × {len(SITES)} 站 = {len(DOMAINS) * len(SITES)} 张")
    plan_dashboards = []
    for domain in DOMAINS:
        for site_label, site_source in SITES:
            name = _dashboard_name(domain, site_label)
            did = dash_ids.get(name)
            plan_dashboards.append((domain, site_label, site_source, name, did))
            _log(f"    {'已存在 #' + str(did) if did else '+'} {name}"
                 f"（图表钉源 {site_source}）")

    _log(f"\n[3] 图表：每板 {len(SLOTS)} 张 = "
         f"{len(DOMAINS) * len(SITES) * len(SLOTS)} 张")
    for domain in DOMAINS:
        for site_label, site_source in SITES:
            name = _dashboard_name(domain, site_label)
            did = dash_ids.get(name)
            have = _existing_charts(did) if did else {}
            hits = [c["title"] for c in domain["charts"] if c["title"] in have]
            _log(f"    {name}: 已有 {len(hits)}/{len(SLOTS)} 张同名卡")

    _log(f"\n[4] 组合：{len(DOMAINS)} 个（每域 5 站板，顺序 "
         f"{'→'.join(s for s, _ in SITES)}），角色 {GROUP_ROLE_IDS}")
    for domain in DOMAINS:
        gname = _group_name(domain)
        gid = group_ids.get(gname)
        _log(f"    {'已存在 #' + str(gid) if gid else '+'} {gname}")

    if not apply:
        _log("\n=== DRY-RUN 结束（未写任何一行）。加 --apply 落库。")
        return

    # ── 1. 数据集 ──
    _log("\n=== APPLY：数据集")
    dataset_ids: dict[str, int] = {}
    for domain, chart, wanted, eid in plan_datasets:
        name = wanted["name"]
        if eid:
            current = dataset_service.get_dataset(eid) or {}
            patch = _dataset_stale(current, wanted)
            if patch:
                dataset_service.update_dataset(eid, patch, IDENTITY)
                _log(f"    ~ 更新 #{eid} {name}: {sorted(patch)}")
            else:
                _log(f"    = 无变化 #{eid} {name}")
            dataset_ids[name] = eid
        else:
            saved = dataset_service.create_dataset(dict(wanted), IDENTITY, origin="manual")
            dataset_ids[name] = int(saved["id"])
            _log(f"    + 新建 #{saved['id']} {name}")

    # ── 2. 看板 ──
    _log("\n=== APPLY：看板")
    dashboard_ids: dict[str, int] = {}
    for domain, site_label, site_source, name, did in plan_dashboards:
        desc = (f"{domain['desc']}。站点：{site_label}（执行源 {site_source}）。"
                f"口径全部来自「数据集」页，本板不写 SQL；五站同类看板见「{_group_name(domain)}」")
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
            dash_ids[name] = new_id
            dashboard_ids[name] = new_id
            _log(f"    + 新建 #{new_id} {name}")

    # ── 3. 图表 ──
    _log("\n=== APPLY：图表")
    created_charts = updated_charts = 0
    for domain in DOMAINS:
        for site_label, site_source in SITES:
            dash_name = _dashboard_name(domain, site_label)
            did = dashboard_ids[dash_name]
            have = _existing_charts(did)
            for idx, chart in enumerate(domain["charts"]):
                ds_name = _dataset_name(domain, chart)
                ds_id = dataset_ids[ds_name]
                payload = _chart_payload(domain, chart, ds_id, ds_name, site_source, idx)
                cid = have.get(chart["title"])
                if cid:
                    chart_service.update_chart(did, cid, payload, IDENTITY)
                    updated_charts += 1
                else:
                    chart_service.create_chart(did, payload, IDENTITY)
                    created_charts += 1
            _log(f"    {dash_name}: 共 {len(domain['charts'])} 张（新增/更新已按名字对齐）")
    _log(f"    合计：新建 {created_charts} 张，对齐更新 {updated_charts} 张")

    # ── 4. 组合 ──
    _log("\n=== APPLY：组合")
    for domain in DOMAINS:
        gname = _group_name(domain)
        gid = group_ids.get(gname)
        if not gid:
            gid = create_group(gname, domain["desc"])
            _log(f"    + 新建组合 #{gid} {gname}")
        else:
            _log(f"    = 已存在组合 #{gid} {gname}")
        members = [dashboard_ids[_dashboard_name(domain, s)] for s, _ in SITES]
        result = set_group_items(gid, members)
        roles = set_group_roles(gid, GROUP_ROLE_IDS)
        _log(f"    成员 {result['count']}/5（{SITES[0][0]}→{SITES[-1][0]}），角色 {roles} 个")
        if result["dropped"]:
            raise SystemExit(f"[fail] 组合成员登记被丢弃（看板不存在）: {result['dropped']}")

    # ── 收尾核对 ──
    _log("\n=== 落库后核对")
    from backend.common.db import execute_query
    for domain in DOMAINS:
        n_ds = len(domain["charts"])
        got_ds = execute_query(
            "SELECT COUNT(*) AS n FROM adh_datasets WHERE name LIKE %s AND status='active'",
            (f"联耀{domain['label']} · %",), fetchone=True)["n"]
        n_dash = len(SITES)
        got_dash = 0
        per_board = []
        for site_label, _src in SITES:
            did = dashboard_ids[_dashboard_name(domain, site_label)]
            cnt = execute_query("SELECT COUNT(*) AS n FROM adh_charts WHERE dashboard_id=%s",
                                (did,), fetchone=True)["n"]
            per_board.append(int(cnt))
            got_dash += 1 if int(cnt) == len(SLOTS) else 0
        gid = group_ids.get(_group_name(domain)) or execute_query(
            "SELECT id FROM adh_dashboard_groups WHERE name=%s",
            (_group_name(domain),), fetchone=True)["id"]
        members = execute_query(
            "SELECT COUNT(*) AS n FROM adh_dashboard_group_items WHERE group_id=%s",
            (gid,), fetchone=True)["n"]
        _log(f"    {domain['label']}: 数据集 {got_ds}/{n_ds}，看板 {got_dash}/{n_dash} 张图表数={len(SLOTS)}，"
             f"每板图数 {per_board}，组合成员 {members}/5")
        if got_ds != n_ds or got_dash != n_dash or int(members) != len(SITES):
            raise SystemExit("[fail] 收尾核对不通过，见上")


if __name__ == "__main__":
    main()