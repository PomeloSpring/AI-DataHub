"""构建「应用使用事件（桌面 app 遥测）」业务本体 + ARMS 侧命名分域（幂等，dry-run 默认）。

背景（会话 1783069459151 的 P0 根因）：平台上叫 "RUM" 的采集域有两个，但只有一个有语义层资产——

    arms-prd-cn.logstore-rum        Web 端（官网/H5 与平台网页）前端体验/点击（RUM）  → 已有本体「RUM观测事件」+ 19 指标 28 维度
    prd-sls-{cn,en,jp,uk,eu}.observability_rum  桌面 app 埋点      → 零本体、零字典、零数据产品

结果：`get_metrics`/语义层拿不到 app 遥测，AI 只能退到裸 SQL 猜列名（appCode 被当成"应用"，
而 app 名其实在 target 里），同一问题在不同回合给出不同答案。

口径（用户拍板 2026-11-04）：
- 渗透率分母 = **应用中心活跃用户**（`target='apps'` 去重 userCode，即"打开过应用中心的人"）；
- **新建独立 SLS 业务本体**（不把 app 遥测塞进 ARMS 模型），ARMS 侧只做业务名收窄；
- 本批就写入生产本体并 activate 发布。

实测事实（90 天窗口，源 prd-sls-cn；复采脚本 `logs/probe_rum_shape.py`、
`logs/probe_rum_app_targets.py`，建模的业务名与枚举一律以实采为准，不照搬列名、不编造语义）：
- `appCode` 只有 8 个取值且都是宿主产品（ScanPro 5,769,094 / AS Connect 5,516,879 / 其余 <60），
  `appCode = target` 恒不成立（0 行）→ 宿主产品不是"应用"；
- `target` 混合语义：既有 UI 元素（colorButton / cases / scan / newCase…），也有 app 名
  （MeshCompare 4,216 事件·1,498 人、OralHealthReport 4,104·859、ModelBuilder 3,002·1,341、
  NewOrthoSim 1,104·413、SmileDesign 973·512、PressureImpression 297·109、OrthoSim 5·3），
  以及应用中心入口 `target='apps'`（19,510 事件·4,135 人，page='/sideBar'、action='check'）；
  app 名事件一律 `action='click'`（在病例/订单详情页点击打开对应 app）；
- 全站去重人数 5,992（同期应用中心 4,135 → 整体触达约 69%）；
- `osInfo` 是 JSON 串，且 prd-sls-en 额外把 osInfo.cpu/gpu/os/ram/diskInfo 平铺成独立列
  （站点不同构）→ 按本体建模规范 §2「同构假设才进 deploy_sites」，osInfo 不入契约；
- `region` 与本站 deploy_site 同值、`ip`/`timestamp`/`row_index`/`oss_object_key` 无分析价值
  或属基础设施字段 → 同样不登记（登记了反而诱导 AI 拿 region 过滤跨站数据）。

写入路径（唯一合规路径，绝不绕过级联直改派生表——本体建模规范 §1）：
    canonical doc → save_generated_draft/save_draft → set_model_deploy_sites → activate
    → ontology_yaml_import._persist_bindings（部署绑定层）→ metrics_service 字典
    → deploy_contract_bindings（各站数据产品行）→ 图谱重建 → 知识库同步

用法（repo 根目录）：
    venv/bin/python -m backend.scripts.build_app_usage_ontology            # dry-run 看计划
    venv/bin/python -m backend.scripts.build_app_usage_ontology --apply    # 落库并发布
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.common.db import execute_query  # noqa: E402
from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.modules.catalog.services import metrics_service, ontology_service  # noqa: E402
from backend.modules.catalog.services import ontology_kb_sync, ontology_yaml_import  # noqa: E402
from backend.core import site_service  # noqa: E402

CREATED_BY = "script:build_app_usage_ontology"

DS_NAME = "prd-sls-cn"                     # 主源（其余站点由 deploy_sites 路由）
TABLE = "observability_rum"
DOMAIN = "应用使用域（桌面 app 遥测）"
MODEL_NAME = "应用使用事件本体（桌面 app 遥测）"
OBJECT_KEY = "app_usage_event"
OBJECT_DISPLAY = "应用使用事件"
OBJECT_ALIASES = ["应用使用事件", "app 使用事件", "桌面应用使用事件", "应用埋点事件",
                  "客户端遥测事件", "应用使用埋点", OBJECT_KEY]
# 不把物理表名写进别名：上云文档会原样渲染别名清单，表名属于物理细节（护栏 §7）。
# 同构多站（实测五站列形状一致，prd-sls-en 仅多出不入契约的 osInfo.* 平铺列）
SITE_DS = [("cn", "prd-sls-cn"), ("int", "prd-sls-en"), ("jp", "prd-sls-jp"),
           ("uk", "prd-sls-uk"), ("eu", "prd-sls-eu")]

# 事件形态（action 实采 8 值；中文标签是行为描述，不改 code 本义）
ACTION_LABELS = {
    "click": "点击",
    "check": "选中/开启",
    "uncheck": "取消选中",
    "select": "选择",
    "input": "输入",
    "resize": "界面尺寸变化",
    "scanBegin": "扫描开始",
    "scanEnd": "扫描结束",
}
# 操作目标里的 app 码（其余取值是 UI 元素标识，不逐一枚举）；
# OrthoSim 是 NewOrthoSim 的旧码，标签里写明合并口径（计划要求：合并规则落 value_labels，不留口头约定）
TARGET_LABELS = {
    "apps": "应用中心",
    "MeshCompare": "网格对比（MeshCompare）",
    "ModelBuilder": "模型构建（ModelBuilder）",
    "OralHealthReport": "口腔健康报告（OralHealthReport）",
    "SmileDesign": "微笑设计（SmileDesign）",
    "PressureImpression": "压力印模（PressureImpression）",
    "NewOrthoSim": "正畸模拟（NewOrthoSim）",
    "OrthoSim": "正畸模拟（旧码 OrthoSim，统计时与 NewOrthoSim 合并）",
}

# 跨域分界说明：两条域互相点名，是本次争议（RUM 到底指哪个域）的正解 - 只改名字不改分界说明，
# 下一个问法仍会串。文案只出现业务名与对象 key，不写物理表名/列名（描述会随上云文档下发，护栏 §7）。
DOMAIN_EDGE = (
    "本域只覆盖桌面客户端遥测（宿主产品 ScanPro / AS Connect 客户端）；"
    "Web 端（官网/H5 与 AS Connect 平台网页）的前端体验与页面内点击在另一模型「ARMS RUM 观测本体」"
    "（对象 rum_event），两者不得混用——同一个 App 名在桌面客户端与平台网页里各记一条，不得相加。"
)

PENETRATION_NOTE = (
    "渗透率口径（拍板）：分母取「应用中心活跃用户数」（打开过应用中心的去重人数），"
    "分子取该 app 的「应用活跃用户数」（维度「操作目标」= 该 app 码）。"
    "分子分母来自不同过滤集，无法编译成单表聚合，故不登记比率指标：需分两次取数后相除。"
)

PROPERTIES: list[dict] = [
    {"column": "action", "name": "事件形态", "type": "string", "is_key": False,
     "description": "埋点行为形态（取值清单见枚举）", "enum": ACTION_LABELS},
    {"column": "appCode", "name": "宿主应用", "type": "string", "is_key": False,
     "description": "上报所在的宿主客户端产品（ScanPro / AS Connect 等），不是被使用的 app",
     "enum": []},
    {"column": "appVersion", "name": "宿主应用版本", "type": "string", "is_key": False,
     "description": "宿主客户端版本号（形如 1.0.42.5）", "enum": []},
    {"column": "target", "name": "操作目标", "type": "string", "is_key": False,
     "description": ("被操作的目标标识：多数是界面元素码，点击打开 app 时是 app 码，"
                     "打开应用中心时是应用中心入口码。统计某个 app 的使用量=按本维度过滤该 app 码"),
     "enum": TARGET_LABELS},
    {"column": "page", "name": "界面路径", "type": "string", "is_key": False,
     "description": "事件发生时的界面路由（形如 /cases/caseDetail、/sideBar）", "enum": []},
    {"column": "userCode", "name": "应用用户账号", "type": "string", "is_key": True,
     "description": "登录用户账号码（人数口径的唯一去重键）", "enum": []},
    {"column": "pcId", "name": "设备标识", "type": "string", "is_key": False,
     "description": "客户端机器标识（设备数口径的去重键）", "enum": []},
    {"column": "__time__", "name": "使用日期", "type": "date", "is_key": False,
     "description": "事件日期（引擎内置事件时间，天粒度）", "enum": []},
    {"column": "__time__", "name": "使用小时", "type": "datetime", "is_key": False,
     "description": "事件小时（引擎内置事件时间，小时粒度）", "enum": []},
]

# 维度字典（业务权威名 → 物理列）：列名全部对着 adh_column_metadata 实存，
# 且刻意避开 ARMS 已占用的名字/别名（重名会让 get_metrics/解析串味）。
# name_en 是英文句柄（字典按 name/name_en/aliases 解析），两个时间维度共用物理列
# __time__ 故不能用列名当 name_en（唯一性口径会撞）。
DIMENSIONS: list[dict] = [
    {"name": "事件形态", "name_en": "action", "target_column": "action", "category": "分类",
     "aliases": ["操作类型", "行为类型"], "value_labels": ACTION_LABELS,
     "description": "app 埋点行为形态（桌面客户端遥测，与 Web 端 RUM 的事件类型是两套口径）"},
    {"name": "宿主应用", "name_en": "appCode", "target_column": "appCode", "category": "属性",
     "aliases": ["客户端产品", "宿主产品"], "value_labels": None,
     "description": "上报所在的宿主客户端产品（ScanPro / AS Connect 等），不是被使用的 app"},
    {"name": "宿主应用版本", "name_en": "appVersion", "target_column": "appVersion", "category": "属性",
     "aliases": ["客户端版本"], "value_labels": None, "description": "宿主客户端版本号"},
    {"name": "操作目标", "name_en": "target", "target_column": "target", "category": "分类",
     "aliases": ["目标元素", "被操作对象", "应用入口"], "value_labels": TARGET_LABELS,
     "description": ("被操作目标：界面元素码或 app 码；'apps'=应用中心入口。"
                     "「应用」的统计口径就是本维度取 app 码，不用宿主应用"),
    },
    {"name": "界面路径", "name_en": "page", "target_column": "page", "category": "属性",
     "aliases": ["页面路径"], "value_labels": None, "description": "事件发生时的界面路由"},
    {"name": "应用用户账号", "name_en": "userCode", "target_column": "userCode", "category": "属性",
     "aliases": ["用户编码", "使用者账号"], "value_labels": None,
     "description": "登录用户账号码（人数去重键）"},
    {"name": "设备标识", "name_en": "pcId", "target_column": "pcId", "category": "属性",
     "aliases": ["电脑标识", "机器标识"], "value_labels": None,
     "description": "客户端机器标识（设备数去重键）"},
    {"name": "使用日期", "name_en": "app_use_date", "target_column": "__time__", "category": "时间",
     "aliases": [], "value_labels": None, "description": "事件日期（天粒度，引擎内置事件时间）"},
    {"name": "使用小时", "name_en": "app_use_hour", "target_column": "__time__", "category": "时间",
     "aliases": [], "value_labels": None, "description": "事件小时（小时粒度，引擎内置事件时间）"},
]

# 指标字典：formula 必须是可编译的单表聚合（retire_derived_pseudo_metrics 的教训——
# 伪公式会被原样拼进 SELECT，"目录挂着、编译用不了"）。比率类只有同过滤集才登记。
METRICS: list[dict] = [
    {"name": "应用使用事件数", "name_en": "app_usage_events", "agg_type": "COUNT",
     "formula": "COUNT(*)", "unit": "条", "aliases": ["埋点事件数", "app事件数", "使用事件数"],
     "description": "桌面客户端埋点事件条数（不含 Web 端 RUM：官网/H5 与 AS Connect 平台网页的页面/点击事件）"},
    {"name": "应用活跃用户数", "name_en": "app_active_users", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT userCode)", "unit": "人", "aliases": ["使用人数", "app活跃用户数", "app用户数"],
     "description": "去重登录用户数；按「操作目标」过滤即该 app 的使用人数。" + PENETRATION_NOTE},
    {"name": "应用活跃设备数", "name_en": "app_active_pcs", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT pcId)", "unit": "台", "aliases": ["活跃设备数", "电脑数", "使用设备数"],
     "description": "去重客户端机器数（按设备口径统计装机/在用）"},
    {"name": "应用中心活跃用户数", "name_en": "app_center_active_users", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT CASE WHEN target = 'apps' THEN userCode END)", "unit": "人",
     "aliases": ["应用中心人数", "打开过应用中心的人数"],
     "description": "打开过应用中心的去重用户数（渗透率分母，拍板口径）。" + PENETRATION_NOTE},
    {"name": "人均使用事件数", "name_en": "app_events_per_user", "agg_type": "NONE",
     "formula": "COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT userCode), 0)", "unit": "条",
     "aliases": ["人均事件数"],
     "description": "事件数 / 去重用户数（同一过滤集内的强度指标，可按任意维度切片）"},
]

# ── G1：ARMS 侧命名收窄（只改业务名与分界说明，不动指标名——指标名被看板/技能/云端文档引用）──
ARMS_MODEL_NAME = "ARMS RUM 观测本体"
ARMS_OBJECT_KEY = "rum_event"
# 2026-11-17 修正：RUM 通道并不只承载官网/H5——AS Connect 等平台网页页面的访问与「点开三方 App」
# 也记在同一条 RUM 通道上（内容中心《2026年度联耀三方集成 App 网页端入口点击分析》的数据就来自这里）。
# 原名「（官网/H5）」会让模型把「网页端」判成官网专属、把桌面遥测 app_usage_event 并成同层候选。
ARMS_DISPLAY = "Web 端 RUM 观测事件（官网/H5 · 平台网页）"
# 保留裸 "RUM" 别名：历史上所有"RUM 问法"都指向这个域，摘掉会让存量问法入口凭空消失
ARMS_ALIASES = ["RUM", "Web端RUM", "网页RUM", "网页端RUM", "平台网页RUM", "H5 RUM", "前端体验事件",
                "用户体验事件", "观测事件", ARMS_OBJECT_KEY, "logstore-rum"]
ARMS_EDGE = (
    "本域覆盖 Web 端（官网/H5 与 AS Connect 平台网页）的前端体验与页面内点击"
    "（页面浏览/资源加载/会话/异常/长任务/页面内点击）；"
    "桌面客户端（ScanPro / AS Connect 客户端）里 app 的使用遥测在另一模型「应用使用事件本体（桌面 app 遥测）」"
    "（对象 app_usage_event），两者不得混用——同一 App 名在两侧各记一条，不得相加。"
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ids_by_name(names: list[str]) -> dict[str, int]:
    ph = ",".join(["%s"] * len(names))
    rows = execute_query(
        f"SELECT id, name, site_code FROM adh_datasources WHERE name IN ({ph})", tuple(names))
    return {str(r["name"]): int(r["id"]) for r in rows or []}


def _find_model(name: str, datasource_id: int):
    return execute_query(
        "SELECT id, name, status, kind FROM adh_ontology_models "
        "WHERE name = %s AND datasource_id = %s AND status IN ('draft','active') "
        "ORDER BY updated_at DESC LIMIT 1", (name, datasource_id), fetchone=True)


def build_doc(ds_id: int) -> dict:
    """canonical doc（唯一可编辑事实源）。

    对象带 `primary_table`：业务本体的来源表是激活门禁（find_unregistered_tables）与
    字典 target_table 派生的依据；物理列绑定则走部署绑定层（doc 里的 properties[].column
    是给 column_map/字典实存裁决用的业务名→列映射）。
    """
    return {
        "datasource_name": DS_NAME,
        "domain": DOMAIN,
        "description": (
            f"桌面 app 使用遥测业务本体（主源 {DS_NAME}，五站同构）：以「应用使用事件」为事实对象，"
            f"覆盖 app 打开/交互/扫描等行为的事件数、使用人数、设备数与应用中心触达。{DOMAIN_EDGE}"
            "站点口径：默认分站呈现不合计（跨站合计需显式指定「全球」），故不按站点维度过滤。"
        ),
        "kind": "business",
        "objects": [{
            "key": OBJECT_KEY,
            "display_name": OBJECT_DISPLAY,
            "aliases": list(OBJECT_ALIASES),
            "description": (
                f"桌面客户端上报的单条使用事件（一次点击/选中/扫描等行为）。{DOMAIN_EDGE} "
                "「应用」取维度「操作目标」里的 app 码（「宿主应用」维度只有 ScanPro/AS Connect 等宿主产品）。"
                "渗透率见「应用中心活跃用户数」口径说明。"
            ),
            "primary_table": TABLE,
            "properties": [dict(p) for p in PROPERTIES],
            "metrics": [m["name"] for m in METRICS],
        }],
        "sources": [],
    }


def preflight(ds_ids: dict[str, int]) -> list[str]:
    """写入前的可诊断检查：数据源/站点/名字冲突，任一不过即拒绝落库（不半截写入）。"""
    problems: list[str] = []
    for _, ds in SITE_DS:
        if ds not in ds_ids:
            problems.append(f"数据源未注册: {ds}")
    sites = {str(r.get("site_code")) for r in
             execute_query("SELECT site_code FROM adh_sites WHERE is_active = 1") or []}
    for code, _ds in SITE_DS:
        if code not in sites:
            problems.append(f"站点未启用或缺失: {code}")

    # 名字/别名冲突（字典全局唯一口径：解析按 name/name_en/aliases 命中，重名即串味）。
    # 本对象已登记的行不算冲突（它们正是本脚本要收敛的对象行，否则重跑会被自己的产物拦下）。
    def _occupied(table: str) -> tuple[set, dict]:
        names, alias_owner = set(), {}
        rows = execute_query(
            f"SELECT name, name_en, aliases, COALESCE(bound_object_key,'') AS bok "
            f"FROM {table} WHERE is_active = 1") or []
        for r in rows:
            if str(r.get("bok") or "") == OBJECT_KEY:
                continue
            names.add(str(r["name"]).strip())
            if r.get("name_en"):
                names.add(str(r["name_en"]).strip())
            raw = r["aliases"]
            aliases = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else (raw or [])
            for a in aliases if isinstance(aliases, list) else []:
                alias_owner.setdefault(str(a).strip(), r["name"])
        return names, alias_owner

    for table, specs, kind in (("adh_metrics", METRICS, "指标"), ("adh_dimensions", DIMENSIONS, "维度")):
        names, alias_owner = _occupied(table)
        for s in specs:
            if s["name"] in names:
                problems.append(f"{kind}名已存在: {s['name']}")
            if s.get("name_en") and s["name_en"] in names:
                # create_* 查重按 (name OR name_en) 全局判，英文句柄与别处的业务名/句柄撞了同样落不进去
                problems.append(f"{kind}英文句柄已被占用: {s['name_en']}（{s['name']}）")
            for a in (s.get("aliases") or []):
                if a in names:
                    problems.append(f"{kind}别名与既有指标/维度名冲突: {a}（{s['name']}）")
                elif a in alias_owner:
                    problems.append(f"{kind}别名已被「{alias_owner[a]}」占用: {a}（{s['name']}）")

    # 对象 key 撞车（identity 判定与激活同口径：撞了会让检索命中歧义、激活硬阻断）
    from backend.modules.catalog.services.ontology_service import object_identity_key
    mine = object_identity_key(OBJECT_KEY)
    for r in execute_query("SELECT DISTINCT object_key FROM adh_ontology_objects WHERE is_active = 1") or []:
        if object_identity_key(str(r["object_key"])) == mine and str(r["object_key"]) != OBJECT_KEY:
            problems.append(f"对象 key 身份与既有对象冲突: {r['object_key']}")
    return problems


def ensure_model(doc: dict, ds_id: int, apply: bool) -> int:
    """草案落库（save_generated_draft 建新 / save_draft 就地改）+ 模型级站点声明。"""
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    row = _find_model(MODEL_NAME, ds_id)
    if not row:
        if not apply:
            _log("[model] dry-run：将新建业务本体草案「%s」（主源 %s）" % (MODEL_NAME, DS_NAME))
            return 0
        res = ontology_service.save_generated_draft(
            ds_id, [dict(o) for o in doc["objects"]], domain=doc["domain"],
            description=doc["description"], append=False, created_by=CREATED_BY)
        model_id = int(res["id"])
        _log(f"[model] 草案已建 (id={model_id}, name={res.get('name')})")
    else:
        model_id = int(row["id"])
        _log(f"[model] 已存在「{MODEL_NAME}」(id={model_id}, status={row['status']})，按 doc 覆盖")
    if not apply:
        return model_id
    saved = ontology_service.save_draft(model_id, payload, name=MODEL_NAME)
    for w in saved.get("validation_warnings") or []:
        _log(f"[model][warn] {w}")
    site_service.set_model_deploy_sites(model_id, [{"site_code": c, "datasource": d} for c, d in SITE_DS])
    _log(f"[model] doc 已保存 + deploy_sites 已声明 {len(SITE_DS)} 站: "
         + ", ".join(f"{c}→{d}" for c, d in SITE_DS))
    return model_id


def claim_products(model_id: int, apply: bool) -> None:
    """激活门禁 P2.3 的前置：把对象来源表认领为数据产品（幂等）。"""
    from backend.scripts import claim_ontology_tables_as_products as claim

    if not model_id:
        _log("[product] dry-run：草案尚未建模型，激活前需认领 "
             f"{DS_NAME}.{TABLE}（其余站点由 deploy_contract_bindings 补按站产品行）")
        return
    res = claim.claim_model(model_id, dry_run=not apply, owner=CREATED_BY)
    _log(f"[product] {'dry-run 待认领' if not apply else '已认领'}: {res.get('claimed') or '（无缺项）'}"
         f" | 门禁剩余: {res.get('gate_missing') or '（空=通过）'}")
    for w in res.get("warnings") or []:
        _log(f"[product][warn] {w}")


def activate_model(model_id: int, apply: bool) -> None:
    if not model_id:
        return
    if not apply:
        _log("[activate] dry-run：跳过激活（正式执行时走级联：展开对象→枚举联动→图谱→知识库）")
        return
    model = ontology_service.activate(model_id)
    _log(f"[activate] 已激活 (status={model['status']}, objects={model.get('object_count')})")


def ensure_binding(doc: dict, ds_id: int, apply: bool) -> None:
    """部署绑定层写入（唯一事实源，doc 不带执行绑定）：走 `_persist_bindings` 而非自造 SQL。"""
    if not apply:
        _log(f"[binding] dry-run：将写主源契约行 bind_kind=primary / {DS_NAME}.{TABLE}"
             "（列映射由 doc properties 实存裁决生成）")
        return
    bdoc = dict(doc)
    bdoc["datasource_id"] = ds_id
    obj = dict(bdoc["objects"][0])
    obj["execution_binding"] = {
        "bind_kind": "primary",
        "physical_table": TABLE,
        "datasource_name": DS_NAME,
        "query_mode": "raw_source",
        "allow_full_scan": 1,
        "sync_state": "bound",
    }
    bdoc["objects"] = [obj]
    stats = ontology_yaml_import._persist_bindings(bdoc, _model_id(ds_id))
    _log(f"[binding] 契约行写入: written={stats.get('written')} drifted={stats.get('drifted')} "
         f"unbound={stats.get('unbound')}")


def _model_id(ds_id: int) -> int:
    row = _find_model(MODEL_NAME, ds_id)
    if not row:
        raise RuntimeError("模型不存在（前序步骤未落库）")
    return int(row["id"])


def ensure_dicts(apply: bool) -> None:
    """指标/维度字典（幂等**收敛**：已存在则对齐到脚本定义，不跳过）。

    为何不能“存在即跳过”：上一轮执行在 create_dimension 里落成过 target_table 为空的
    维度行（planner 按物理表加载字典 → 命中 0 行，列名对了却取不到数）；跳过会把
    这个缺陷留在库里并报“已存在”。列名/表的实存裁决一律交 metrics_service（宁拒勿错写）。
    """
    created, updated = [], []
    # 不自封认证：`certified=1` 必须配口径责任人（_certify 的硬规则），脚本拿不出“谁对口径负责”。
    # 认证覆盖率低是**待办清单**不是缺陷，但拿 0 改成 1 当认证会让治理页看不见真缺口（P4）。
    # 维度显式传 certified=0：既防新行假认证，也把早先误写的假认证行收敛回草稿。
    for spec in METRICS:
        payload = {"display_name": spec["name"], "name": spec["name_en"],
                   "calculation_type": spec["agg_type"], "expression": spec["formula"],
                   "unit": spec["unit"], "description": spec["description"],
                   "aliases": spec["aliases"], "category": "可观测",
                   "bound_object_key": OBJECT_KEY, "target_table": TABLE}
        row = execute_query("SELECT id FROM adh_metrics WHERE name = %s", (spec["name"],), fetchone=True)
        if not apply:
            (updated if row else created).append(f"metric:{spec['name']}")
            continue
        if row:
            metrics_service.update_metric(int(row["id"]), payload)
            updated.append(f"metric:{spec['name']}")
        else:
            res = metrics_service.create_metric(payload)
            created.append(f"metric:{spec['name']}(id={res['id']})")
    for spec in DIMENSIONS:
        payload = {"display_name": spec["name"], "name_en": spec["name_en"],
                   "target_table": TABLE, "target_column": spec["target_column"],
                   "category": spec["category"], "description": spec["description"],
                   "bound_object_key": OBJECT_KEY, "aliases": spec["aliases"],
                   "value_labels": spec["value_labels"], "certified": 0}
        row = execute_query("SELECT id FROM adh_dimensions WHERE name = %s", (spec["name"],), fetchone=True)
        if not apply:
            (updated if row else created).append(f"dimension:{spec['name']}")
            continue
        if row:
            metrics_service.update_dimension(int(row["id"]), payload)
            updated.append(f"dimension:{spec['name']}")
        else:
            res = metrics_service.create_dimension(payload)
            created.append(f"dimension:{spec['name']}(id={res['id']})")
    _log(f"[dict] {'dry-run 待写' if not apply else '已建'}: {created or '（无新建）'}")
    if updated:
        _log(f"[dict] 已对齐现有行: {updated}")


def deploy_sites_products(apply: bool) -> None:
    """各站数据产品行 + 站点收敛（同构行置 deprecated，不同构留例外清单，不静默删差异）。"""
    from backend.scripts import deploy_contract_bindings as dcb

    if not apply:
        _log("[sites] dry-run：将跑 deploy_contract_bindings --model（补各站产品行）")
        return
    model_id = _model_id(_ids_by_name([DS_NAME])[DS_NAME])
    code = dcb.run(apply=True, only_model=model_id)
    _log(f"[sites] deploy_contract_bindings 退出码={code}")


def rebuild_graph_and_kb(apply: bool) -> None:
    if not apply:
        _log("[graph] dry-run：将重建业务图并推送上云文档")
        return
    ds_id = _ids_by_name([DS_NAME])[DS_NAME]
    g = ontology_yaml_import._rebuild_graph(ds_id, "business")
    if g.get("success"):
        _log(f"[graph] 业务图重建完成 tables={g.get('tables')} relations={g.get('relations')}")
    else:
        _log(f"[graph][warn] 图重建未完成（doc 已落库，图是滞后状态）: "
             f"{str(g.get('error') or g.get('message') or '未给出原因')[:200]}")
    res = ontology_kb_sync.sync_model_to_qmind(_model_id(ds_id))
    if res.get("synced"):
        # targets 元素形态由同步层决定（可为库名或带 name 的 dict），只取可打印部分，不在脚本里猜结构
        _log(f"[kb] 上云文档已同步 synced={res.get('synced')} targets={res.get('targets')}")
    else:
        _log(f"[kb][warn] 未同步（原因 {res.get('skipped')}）：可在本体页手动重试，"
             "不得当成已发布")


def sync_kb_if_stale(model_id: int) -> None:
    """上云文档落后于 doc 时重推（命名分域改了 ARMS 对象名，KB 不推就是“库里旧名”）。

    口径：拿 `model_sync_status` 的 `kb.synced_at` 与模型 `updated_at` 比；推不动只出声
    （不拿“未同步”当成已发布，也不让旁路失败阻断主流程）。
    """
    st = ontology_kb_sync.model_sync_status(int(model_id)) or {}
    kb = st.get("kb") or {}
    model = execute_query("SELECT updated_at FROM adh_ontology_models WHERE id = %s",
                          (int(model_id),), fetchone=True)
    synced_at, updated_at = str(kb.get("synced_at") or ""), str((model or {}).get("updated_at") or "")
    if kb.get("state") == "success" and synced_at and synced_at >= updated_at:
        _log(f"[kb] 模型 {model_id} 上云文档已是最新（{synced_at} ≥ doc {updated_at}）")
        return
    res = ontology_kb_sync.sync_model_to_qmind(int(model_id))
    if res.get("synced"):
        _log(f"[kb] 模型 {model_id} 上云文档已重推 synced={res.get('synced')}")
    else:
        _log(f"[kb][warn] 模型 {model_id} 未同步（原因 {res.get('skipped')}）："
             "名称已在库中生效但知识库仍旧名，需在本体页重试")


def narrow_arms_naming(apply: bool) -> None:
    """G1：ARMS 对象业务名收窄为「Web 端 RUM（官网/H5 · 平台网页）」，并写进分界说明。

    指标名不动（被看板/技能/云端文档引用，改名爆炸半径大于收益）；分域由
    对象业务名 + 两侧描述互指完成，新域的业务名里不出现 "RUM"。
    """
    row = execute_query("SELECT id, status, json_content FROM adh_ontology_models "
                        "WHERE name = %s AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                        (ARMS_MODEL_NAME,), fetchone=True)
    if not row:
        _log(f"[arms][warn] 未找到 active 模型「{ARMS_MODEL_NAME}」，跳过命名分域")
        return
    doc = json.loads(row["json_content"] or "{}")
    obj = next((o for o in doc.get("objects") or [] if o.get("key") == ARMS_OBJECT_KEY), None)
    if not obj:
        _log(f"[arms][warn] 模型 {row['id']} 里没有对象 {ARMS_OBJECT_KEY}，跳过")
        return
    if obj.get("display_name") == ARMS_DISPLAY and ARMS_EDGE in (doc.get("description") or ""):
        _log("[arms] 命名已收窄且分界说明已就位，跳过")
        sync_kb_if_stale(int(row["id"]))   # 名字已对但上云文档可能还停在旧名，照旧自愈
        return
    _log(f"[arms] 「{obj.get('display_name')}」→「{ARMS_DISPLAY}」（保留裸 RUM 别名，历史问法入口不消失）")
    if not apply:
        return
    obj["display_name"] = ARMS_DISPLAY
    obj["aliases"] = list(ARMS_ALIASES)
    obj["description"] = (
        "官网/H5 与 AS Connect 平台网页页面与资源侧上报的单个前端观测事件（页面/资源/会话/交互/异常/长任务）。"
        + ARMS_EDGE + " 默认仅统计生产环境（应用环境=生产）。")
    # 幂等：新旧两版分界说明的开头都切掉（旧版「本域只覆盖」/新版「本域覆盖」），避免重跑叠加
    _base = str(doc.get("description") or "")
    for _marker in ("本域覆盖", "本域只覆盖"):
        _base = _base.split(_marker)[0]
    doc["description"] = _base.rstrip() + " " + ARMS_EDGE
    saved = ontology_service.save_draft(int(row["id"]), json.dumps(doc, ensure_ascii=False, indent=2))
    for w in saved.get("validation_warnings") or []:
        _log(f"[arms][warn] {w}")
    _log(f"[arms] 已保存并级联（模型 {row['id']}）")
    sync_kb_if_stale(int(row["id"]))


def retire_stale_bindings(apply: bool) -> None:
    """G3：清理已退役对象的 deprecated 绑定行（先确认无对象行/无字典绑定，避免悬空）。"""
    rows = execute_query(
        "SELECT id, model_id, object_key, datasource_name, physical_table FROM adh_ontology_bindings "
        "WHERE status = 'deprecated' AND physical_table = 'dwd_observability_rum'") or []
    if not rows:
        _log("[retire] 无 dwd_observability_rum 的 deprecated 绑定行，跳过")
        return
    for r in rows:
        key = str(r["object_key"])
        live_obj = execute_query(
            "SELECT id FROM adh_ontology_objects WHERE object_key = %s LIMIT 1", (key,), fetchone=True)
        live_dict = execute_query(
            "SELECT id FROM adh_metrics WHERE bound_object_key = %s AND is_active = 1 LIMIT 1",
            (key,), fetchone=True) or execute_query(
            "SELECT id FROM adh_dimensions WHERE bound_object_key = %s AND is_active = 1 LIMIT 1",
            (key,), fetchone=True)
        if live_obj or live_dict:
            _log(f"[retire] 跳过 {key}(id={r['id']})：仍有对象行或字典行引用（不制造悬空）")
            continue
        _log(f"[retire] {'待清理' if not apply else '已清理'} deprecated 绑定行 "
             f"{key} → {r['datasource_name']}.{r['physical_table']} (id={r['id']})")
        if apply:
            with get_metadata_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM adh_ontology_bindings WHERE id = %s AND status = 'deprecated'",
                                (int(r["id"]),))
                conn.commit()


def ensure_datasource_access(ds_ids: dict[str, int], apply: bool) -> None:
    """角色数据源授权（可查性唯一裁决点）：缺授权时新域会 fail-closed 拒查。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_roles WHERE is_active = 1 ORDER BY id")
            roles = cur.fetchall() or []
            admin_id = int(roles[0]["id"]) if roles else 0
            if not admin_id:
                _log("[access][warn] 未找到可用角色，跳过授权检查")
                return
            missing = []
            for ds, ds_id in ds_ids.items():
                cur.execute("SELECT 1 FROM adh_role_datasource_access WHERE role_id = %s AND datasource_id = %s",
                            (admin_id, ds_id))
                if not cur.fetchone():
                    missing.append((ds, ds_id))
            if not missing:
                _log("[access] 管理员角色对全部 SLS 源已授权")
                return
            _log(f"[access] {'待补授权' if not apply else '补授权'}: "
                 f"{[m[0] for m in missing]} → role {admin_id}")
            if apply:
                for _ds, ds_id in missing:
                    cur.execute(
                        "INSERT IGNORE INTO adh_role_datasource_access (role_id, datasource_id) VALUES (%s, %s)",
                        (admin_id, ds_id))
                conn.commit()


def verify(ds_id: int) -> int:
    """发布后核验：门禁全空 + 对象/绑定/字典/站点齐备，任何缺项都显式报出。"""
    problems: list[str] = []
    model = ontology_service.get_model(_model_id(ds_id))
    doc = json.loads(model.get("json_content") or "{}")
    if model.get("status") != "active":
        problems.append(f"模型状态非 active: {model.get('status')}")
    for fn, label in ((lambda: ontology_service.find_unregistered_tables(doc), "未认领来源表"),
                      (lambda: ontology_service.find_orphan_dict_bindings(doc), "字典孤儿引用"),
                      (lambda: [i for i in ontology_service.find_m234_issues(doc)
                                if i["severity"] == "error"], "M2/M3/M4 引用")):
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 — 核验本身失败也要显式报
            got = [f"检查异常 {type(exc).__name__}: {exc}"]
        if got:
            problems.append(f"{label}: {got}")
    issues = ontology_service.find_deploy_site_issues(int(model["id"]))
    if issues:
        problems.append(f"适用站点: {issues}")

    obj_rows = execute_query(
        "SELECT id FROM adh_ontology_objects WHERE model_id = %s AND object_key = %s AND is_active = 1",
        (int(model["id"]), OBJECT_KEY)) or []
    if len(obj_rows) != 1:
        problems.append(f"对象展开行异常: {len(obj_rows)} 行")
    binds = execute_query(
        "SELECT datasource_name, physical_table, status FROM adh_ontology_bindings "
        "WHERE object_key = %s AND status = 'active'", (OBJECT_KEY,)) or []
    if not binds:
        problems.append("部署绑定层无生效行（planner 拿不到物理表，取数必挂）")
    for spec in METRICS:
        row = execute_query("SELECT target_table, bound_object_key, formula FROM adh_metrics WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            problems.append(f"指标缺失: {spec['name']}")
        elif str(row["target_table"] or "") != TABLE:
            problems.append(f"指标 {spec['name']} target_table={row['target_table']!r} ≠ {TABLE}"
                            "（planner 按物理表加载字典，空/错值等于不可编译）")
    for spec in DIMENSIONS:
        row = execute_query("SELECT target_table FROM adh_dimensions WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            problems.append(f"维度缺失: {spec['name']}")
        elif str(row["target_table"] or "") != TABLE:
            problems.append(f"维度 {spec['name']} target_table={row['target_table']!r} ≠ {TABLE}")

    ds = execute_query("SELECT deploy_sites FROM adh_ontology_models WHERE id = %s",
                       (int(model["id"]),), fetchone=True)
    sites = json.loads(ds["deploy_sites"] or "[]") if ds else []
    if {str(s.get("site_code")) for s in sites} != {c for c, _ in SITE_DS}:
        problems.append(f"deploy_sites 与计划不一致: {sites}")

    for p in problems:
        _log(f"[verify][FAIL] {p}")
    if not problems:
        _log("[verify] 建模齐备：模型 active / 门禁空 / 对象已展开 / 绑定生效 / "
             f"字典 {len(METRICS)} 指标 + {len(DIMENSIONS)} 维度均落 {TABLE} / 站点 {len(sites)} 站")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建「应用使用事件」业务本体 + ARMS 命名分域")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    args = ap.parse_args()
    apply = bool(args.apply)
    _log(f"== {'执行' if apply else 'dry-run（只报计划，不写库）'} ==")

    ds_ids = _ids_by_name([d for _c, d in SITE_DS])
    if DS_NAME not in ds_ids:
        _log(f"[FATAL] 主源未注册: {DS_NAME}")
        return 2
    ds_id = ds_ids[DS_NAME]

    problems = preflight(ds_ids)
    for p in problems:
        _log(f"[preflight][FAIL] {p}")
    if problems:
        _log("[FATAL] 前置检查未过，未写入任何内容")
        return 2

    doc = build_doc(ds_id)
    _log(f"[doc] 对象 {OBJECT_KEY}「{OBJECT_DISPLAY}」 属性 {len(doc['objects'][0]['properties'])} 个 / "
         f"指标引用 {len(doc['objects'][0]['metrics'])} 个")
    ensure_datasource_access(ds_ids, apply)
    model_id = ensure_model(doc, ds_id, apply)
    claim_products(model_id, apply)
    if not apply:
        _log("== 后续步骤（激活/绑定/字典/站点/图谱/知识库）需 --apply 才执行 ==")
        narrow_arms_naming(apply)
        retire_stale_bindings(apply)
        return 0
    activate_model(model_id, apply)
    ensure_binding(doc, ds_id, apply)
    ensure_dicts(apply)
    deploy_sites_products(apply)
    narrow_arms_naming(apply)
    retire_stale_bindings(apply)
    rebuild_graph_and_kb(apply)
    return verify(ds_id)


if __name__ == "__main__":
    raise SystemExit(main())
