"""构建「平台请求日志」业务本体（中国站 + 国际站两模型）与指标/维度字典（幂等，默认 dry-run）。

为什么需要本脚本（会话 2026-11-25 现场）：
SLS 源里的 `logstash*` 是**平台自身的请求日志**（网关/前端上报：后端接口调用 + 网页端页面访问），
体量中国站约 170 万条/天、国际四站合计约 90 万条/天，但**零本体、零字典、零技能**——
ChatBI 拿到「接口成功率/慢接口/访问量」这类问题只能裸 SQL 猜列名（`businessType` 被当成业务类型、
`uid`（每行唯一的请求标识）被当成用户），同题不同答复。

实测事实（2026-11-25，源 prd-sls-cn / prd-sls-en / jp / uk / eu，24h 窗口，脚本同批只读探针）：
- **表名不同构**：中国站是 `logstash_cn`（40 列），国际四站是 `logstash`（46~52 列）；
  中国站**不存在** `logstash`（SLS: logstore logstash does not exist），国际站也不存在 `logstash_cn`。
  `adh_column_metadata` 里中国站的 `logstash` 行是滞留行（表已被删/改名）。
  → 按本体建模规范 §1「某站 schema 真不同 → 为该站单独配一个本体」，落到**两个模型**；
     国际四站之间同构（同一张 `logstash`），走 deploy_sites 一份契约 × 4 站路由（与联耀业务本体同法）。
- **点名业务口径**（用户 2026-11-25）：「用别名来指代方便使用」——两模型的业务名互为别名
  （国际站字典行 `aliases` 包含中国站业务名），问法在两站一致；指标/维度 `name` 因字典全局唯一
  才带「·国际站」后缀，物理表各自绑定（planner 按 `binding.physical_table` 加载本站那套字典）。
- `businessType` 混合两类语义：`SERVER`=后端接口调用（有 method/costTime/responseParam.code），
  `CLIENT`=网页端页面访问（三者全空，requestUrl 是整页 URL）→ 成功率/耗时类口径只对 SERVER 成立。
- `uid` 每行唯一（1,718,927 distinct = 行数）→ 是请求标识不是用户；用户是 `userCode`。
- `headerMap` 含 accessToken 明文 → 不建模，另登记敏感列（block）。
- `ip` 是**客户端 IP**（业务数据，不是基础设施 IP）→ 只做去重计数（`独立IP数`），
  明细按数据权限脱敏；不登记成可分组维度（避免诱导按 IP 列明细）。
- 无分析价值/基础设施字段不建模：`tId`（常量）、`busTB`/`tag`（常量 accesslog/business）、
  `geoip.*`（全 null）、`responseParam.msg`（全 null）、`pageNumber`/`pageSize`（固定 1/0）、
  `host`/`host.name`/`@timestamp`/`timestamp`（重复或基础设施）。
- 可用函数实测：`approx_percentile(costTime,0.95)`（P95）、`SUM(CASE...)/NULLIF(...)`（成功率）、
  `COUNT(DISTINCT userCode/pcId/ip)`。

写入路径（本体建模规范 §1，绝不绕过级联直改派生表）：
    canonical doc → save_generated_draft/save_draft → claim 数据产品 → activate 级联
    → ontology_yaml_import._persist_bindings（部署绑定层）→ metrics_service 字典
    → deploy_contract_bindings（各站数据产品行）→ 图谱重建 → 知识库同步

幂等边界：模型按 (name, datasource_id) 复用；字典行只 update **归属本域对象**的同名行
（`_existing_owned_row`，绝不覆盖别的域口径）；部署绑定每次按 doc 重建契约行；
二次执行报 0 新增。结构性/配置面无 SQL 改动（敏感列另见 `docker/mysql/` 迁移）。

用法（repo 根目录）：
    venv/bin/python -m backend.scripts.build_platform_request_log_ontology            # dry-run
    venv/bin/python -m backend.scripts.build_platform_request_log_ontology --apply    # 落库并发布
    venv/bin/python -m backend.scripts.build_platform_request_log_ontology --only cn  # 只跑中国站
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
from backend.core import site_service  # noqa: E402
from backend.modules.catalog.services import metrics_service, ontology_service  # noqa: E402
from backend.modules.catalog.services import ontology_kb_sync, ontology_yaml_import  # noqa: E402

CREATED_BY = "script:build_platform_request_log_ontology"

DOMAIN = "平台请求日志域（接口调用 · 页面访问）"

# 技能（唯一事实源 = config/skills/{name}/SKILL.md）：只绑能跑语义层取数的角色。
# `requires_any: [run_semantic_query]` 的能力门控由会话工具清单把关；viewer 未授权 semantic 组，绑了也展不开。
SKILL_NAME = "platform-request-log"
SKILL_BOTS = ("admin", "analyst", "Operations Manager")

# 两个部署域：物理表不同构（中国站 logstash_cn / 国际站 logstash），各自一个模型。
PROFILES: list[dict] = [
    {
        "key": "cn",
        "model_name": "平台请求日志本体（中国站）",
        "object_key": "platform_request_log",
        "object_display": "平台请求日志（中国站）",
        "ds_name": "prd-sls-cn",
        "table": "logstash_cn",
        "site_ds": [("cn", "prd-sls-cn")],
        "dict_name_suffix": "",
        "dict_en_suffix": "",
        "alias_lead": "",
        "note": ("中国站（prd-sls-cn.logstash_cn，约 170 万条/天）。平台自身的请求日志："
                 "后端接口调用（SERVER）与网页端页面访问（CLIENT）"),
    },
    {
        "key": "intl",
        "model_name": "平台请求日志本体（国际站）",
        "object_key": "platform_request_log_intl",
        "object_display": "平台请求日志（国际站）",
        "ds_name": "prd-sls-en",
        "table": "logstash",
        "site_ds": [("int", "prd-sls-en"), ("jp", "prd-sls-jp"),
                    ("uk", "prd-sls-uk"), ("eu", "prd-sls-eu")],
        "dict_name_suffix": "·国际站",
        "dict_en_suffix": "_intl",
        "alias_lead": "国际站",
        "note": ("国际站（prd-sls-en/jp/uk/eu.logstash，四站同构，合计约 90 万条/天）。"
                 "平台自身的请求日志：后端接口调用（SERVER）与网页端页面访问（CLIENT）"),
    },
]

# 对象别名：两模型共用同一组业务词（用户口径「用别名来指代方便使用」）。
# 不把物理表名写进别名：上云文档会原样渲染别名清单，表名属物理细节（护栏 §7）。
OBJECT_ALIASES = [
    "平台请求日志", "平台访问日志", "请求日志", "访问日志", "接口日志", "接口调用日志",
    "服务端日志", "网关日志", "网页访问日志", "页面访问日志", "前端访问日志",
    "接口调用", "接口请求", "请求记录", "访问记录", "平台日志",
]

# ── 枚举（键即库里码值，实采确认；空串键表示未标注）─────────────────────────────
BUSINESS_TYPE_LABELS = {
    "SERVER": "后端接口调用",
    "CLIENT": "页面访问",
}
LOG_TYPE_LABELS = {
    "INFO": "信息",
    "ERROR": "错误",
}
METHOD_LABELS = {
    "POST": "POST 提交",
    "GET": "GET 查询",
}
APPLICATION_LABELS = {
    "cloud-platform-v2": "云平台主服务",
    "cloud-platform-admin": "运营管理后台服务",
    "cloud-platform-help-center": "帮助中心服务",
}
APP_CODE_LABELS = {
    "ASWEB": "平台网页端（AS Web）",
    "CONNECT": "AS Connect 桌面客户端",
    "SCANPRO": "ScanPro 客户端",
    "SCANPROSA": "ScanPro 服务端",
    "AS_WEB_MOBILE": "移动端网页",
    "ALLIYUN-FC": "阿里云函数（Serverless）",
    "ASADMIN": "运营管理后台",
    "WEBSITE": "官网",
    "support": "帮助中心",
    "patient": "患者端",
    "FACTORY": "工厂端",
    "CLOUD_DISK_ADMIN": "云盘管理后台",
    "ASCONNECT_IOS": "AS Connect iOS 客户端",
    "ASCONNECT_ANDROID": "AS Connect Android 客户端",
    "": "未标注（多为开放接口）",
}

# 跨域分界（三处互相点名，防止「日志/RUM/埋点」问法串域；文案只出现业务名与对象 key）
DOMAIN_EDGE = (
    "本域只覆盖**平台服务端请求日志**（后端接口调用 + 网页端页面访问上报）。"
    "Web 端前端体验/性能/页面内点击属「官网可观测本体」（对象 rum_event）；"
    "桌面客户端 app 使用遥测属「应用使用事件本体」（对象 app_usage_event）；"
    "设备侧/软件/第三方接口运行遥测属「云平台运行日志本体」（ES，对象 cloud_es_*）。"
    "四域互不相同，不得混用或相加。"
)

SITE_SCOPE_NOTE = (
    "站点口径：默认分站呈现不合计（跨站合计需显式指定「全球」），故不按 region 维度过滤。"
    "中国站与国际站是两个本体（物理表不同构），口径与问法一致但不得相加。"
)

# ── 对象属性（两模型同一套业务列；只取实存的共有主干列，不含 region/geoip/host 等）─────
PROPERTIES: list[dict] = [
    {"column": "businessType", "name": "请求类型", "type": "string", "is_key": False,
     "description": "SERVER=后端接口调用（有请求方法/耗时/响应码）；CLIENT=网页端页面访问",
     "enum": BUSINESS_TYPE_LABELS},
    {"column": "appCode", "name": "来源应用通道", "type": "string", "is_key": False,
     "description": "上报方的产品/通道（平台网页端、桌面客户端、移动端、官网、运营后台等）",
     "enum": APP_CODE_LABELS},
    {"column": "application", "name": "后端服务", "type": "string", "is_key": False,
     "description": "处理该请求的后端服务名", "enum": APPLICATION_LABELS},
    {"column": "logType", "name": "日志级别", "type": "string", "is_key": False,
     "description": "上报级别（INFO 为主，ERROR 极少）", "enum": LOG_TYPE_LABELS},
    {"column": "method", "name": "请求方法", "type": "string", "is_key": False,
     "description": "HTTP 方法（仅后端接口调用有值；页面访问为空）", "enum": METHOD_LABELS},
    {"column": "requestUrl", "name": "请求URL", "type": "string", "is_key": False,
     "description": "请求地址：接口路径（后端调用）或整页 URL（页面访问）", "enum": []},
    {"column": "responseParam.code", "name": "响应码", "type": "int", "is_key": False,
     "description": "业务/HTTP 响应码（200 为成功；3036/3060/2001/402/403/501 等为各类失败）",
     "enum": []},
    {"column": "costTime", "name": "耗时毫秒", "type": "int", "is_key": False,
     "description": "后端接口耗时（毫秒，仅请求类型=后端接口调用有值）", "enum": []},
    {"column": "userCode", "name": "用户账号", "type": "string", "is_key": True,
     "description": "登录用户账号码（用户数口径的唯一去重键）", "enum": []},
    {"column": "pcId", "name": "设备标识", "type": "string", "is_key": False,
     "description": "客户端机器标识（设备数口径的去重键）", "enum": []},
    {"column": "ip", "name": "客户端IP", "type": "string", "is_key": False,
     "description": "请求方客户端 IP（业务数据；只做去重计数，明细按数据权限脱敏下发）",
     "enum": []},
    {"column": "cVersion", "name": "客户端版本", "type": "string", "is_key": False,
     "description": "上报客户端版本号（形如 1.0.18.1）", "enum": []},
    {"column": "pVersion", "name": "平台版本", "type": "string", "is_key": False,
     "description": "平台版本号（形如 2.0.0）", "enum": []},
    {"column": "__time__", "name": "请求日期", "type": "date", "is_key": False,
     "description": "请求日期（引擎内置事件时间，天粒度）", "enum": []},
    {"column": "__time__", "name": "请求小时", "type": "datetime", "is_key": False,
     "description": "请求小时（引擎内置事件时间，小时粒度）", "enum": []},
]

# ── 指标字典：formula 必须可编译（SLS/Presto 子集），带点列一律写已加双引号形态 ─────
# （`_fix_formula` 幂等保留引号；`_require_formula_matches_object` 的列名抽取会跳过引号片段）
METRIC_SPECS: list[dict] = [
    {"name": "平台请求总数", "name_en": "platform_request_count", "agg_type": "COUNT",
     "formula": 'COUNT(*)', "unit": "条",
     # 别名只登记**未被其它域占用**的口语词（字典别名全局参与解析，重名即串味）。
     # 「独立IP数/接口调用次数/日志条数/页面访问量/设备数/用户数/业务类型/请求地址/设备标识/
     # 客户端版本…」已被 RUM(rum_event)、ES 云平台运行日志、应用使用事件(app_usage_event)
     # 等域占作名字或别名 —— 一律让出，改用带「平台」限定的说法（本域自证口径见 SKILL 正文）。
     "aliases": ["请求总数", "请求量", "请求条数", "日志总条数"],
     "description": "平台请求日志条数（含后端接口调用与页面访问两类；按「请求类型」区分口径）"},
    {"name": "平台接口调用次数", "name_en": "platform_api_call_count", "agg_type": "SUM",
     "formula": "SUM(CASE WHEN businessType = 'SERVER' THEN 1 ELSE 0 END)", "unit": "次",
     "aliases": ["接口次数", "接口量", "后端调用次数", "服务端请求数", "后端接口调用次数"],
     "description": "后端接口被调用的次数（请求类型=后端接口调用）；不含网页端页面访问"},
    {"name": "平台页面访问次数", "name_en": "platform_page_view_count", "agg_type": "SUM",
     "formula": "SUM(CASE WHEN businessType = 'CLIENT' THEN 1 ELSE 0 END)", "unit": "次",
     "aliases": ["页面访问次数", "网页访问次数", "前端访问次数", "平台页面访问量"],
     "description": "网页端页面上报的访问次数（请求类型=页面访问；无请求方法/耗时/响应码）"},
    {"name": "平台接口失败次数", "name_en": "platform_api_fail_count", "agg_type": "SUM",
     "formula": ("SUM(CASE WHEN businessType = 'SERVER' AND \"responseParam.code\" IS NOT NULL "
                 "AND \"responseParam.code\" <> 200 THEN 1 ELSE 0 END)"),
     "unit": "次",
     "aliases": ["接口报错次数", "失败请求数", "平台接口报错次数"],
     "description": "后端接口响应码非 200 且非空（如 3036/3060/2001/402/403/501）的次数"},
    {"name": "平台接口成功率", "name_en": "platform_api_success_rate", "agg_type": "SUM",
     "formula": ("ROUND(SUM(CASE WHEN businessType = 'SERVER' AND \"responseParam.code\" = 200 "
                 "THEN 1 ELSE 0 END) * 100.0 / NULLIF(SUM(CASE WHEN businessType = 'SERVER' AND "
                 "\"responseParam.code\" IS NOT NULL THEN 1 ELSE 0 END), 0), 2)"),
     "unit": "%",
     "aliases": ["调用成功率", "接口成功率(%)", "后端接口成功率"],
     "description": ("成功率 = 响应码 200 的接口调用数 ÷ 有响应码的接口调用数 × 100（0~100 百分数）；"
                     "页面访问行不参与分母")},
    {"name": "平台接口平均耗时", "name_en": "platform_api_avg_cost_ms", "agg_type": "AVG",
     "formula": "AVG(CASE WHEN businessType = 'SERVER' THEN costTime END)", "unit": "毫秒",
     "aliases": ["平均耗时", "接口平均响应时间", "后端接口平均耗时"],
     "description": "后端接口平均耗时（毫秒，仅请求类型=后端接口调用有值）"},
    {"name": "平台接口P95耗时", "name_en": "platform_api_p95_cost_ms", "agg_type": "NONE",
     "formula": "approx_percentile(CASE WHEN businessType = 'SERVER' THEN costTime END, 0.95)",
     "unit": "毫秒",
     "aliases": ["P95耗时", "接口P95响应时间", "慢接口阈值", "后端接口P95"],
     "description": "后端接口耗时的 P95 分位（毫秒），用于定位慢接口；仅后端接口调用有值"},
    {"name": "平台登录用户数", "name_en": "platform_login_user_count", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT userCode)", "unit": "人",
     "aliases": ["登录用户数", "活跃用户数", "去重用户数", "平台登录用户"],
     "description": "按用户账号去重的登录用户数（页面访问行多数无账号，仅统计有账号的请求）"},
    {"name": "平台访问设备数", "name_en": "platform_device_count", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT pcId)", "unit": "台",
     "aliases": ["访问设备数", "去重设备数", "客户端数", "平台访问设备"],
     "description": "按设备标识去重的访问设备数"},
    {"name": "平台独立IP数", "name_en": "platform_unique_ip_count", "agg_type": "COUNT_DISTINCT",
     "formula": "COUNT(DISTINCT ip)", "unit": "个",
     "aliases": ["去重IP数", "独立IP", "平台独立IP"],
     "description": "按客户端 IP 去重的访问来源数（只回计数；IP 明细按数据权限脱敏下发）"},
    # ── 故障/性能治理面（3 个）──────────────────────────────────────────────────
    # 只有「次数/均值/P95」时，「有多少请求慢」「多少个接口在报错」「影响多少用户」这三问答不了：
    # P95 是分位不能反推慢请求条数，失败次数是次数不是波及面。三者是独立口径，不可互推。
    {"name": "平台慢请求数", "name_en": "platform_slow_request_count", "agg_type": "SUM",
     "formula": (
         "SUM(CASE WHEN businessType = 'SERVER' AND costTime >= 3000 THEN 1 ELSE 0 END)"),
     "unit": "次",
     "aliases": ["慢请求数", "慢请求条数", "慢调用数", "慢接口请求数"],
     "description": ("后端接口调用中耗时 ≥3000 毫秒的请求条数（慢请求阈值固定为本域 3 秒口径）；"
                     "页面访问行无耗时，不计入；慢≠失败，与「平台接口失败次数」分开报")},
    {"name": "平台失败接口数", "name_en": "platform_failed_api_count", "agg_type": "COUNT_DISTINCT",
     "formula": ("COUNT(DISTINCT CASE WHEN businessType = 'SERVER' AND \"responseParam.code\" "
                 "IS NOT NULL AND \"responseParam.code\" <> 200 THEN requestUrl END)"),
     "unit": "个",
     "aliases": ["失败接口数", "报错接口数", "失败接口个数"],
     "description": ("有失败（响应码非 200）的不同接口数（按请求 URL 去重）：回答「多少个接口在报错」，"
                     "不是失败次数（同一个接口失败 10 万次仍只算 1 个）")},
    {"name": "平台失败影响用户数", "name_en": "platform_failed_user_count",
     "agg_type": "COUNT_DISTINCT",
     "formula": ("COUNT(DISTINCT CASE WHEN businessType = 'SERVER' AND \"responseParam.code\" "
                 "IS NOT NULL AND \"responseParam.code\" <> 200 THEN userCode END)"),
     "unit": "人",
     "aliases": ["失败影响用户数", "报错影响用户数", "失败涉及用户数"],
     "description": ("遇到过失败请求的去重登录用户数（故障影响面）；页面访问行多数无账号，"
                     "该值天然偏小，报数时需说明统计口径")},
]

# ── 维度字典（业务权威名 → 物理列）─────────────────────────────────────────────
DIMENSION_SPECS: list[dict] = [
    {"name": "请求类型", "name_en": "request_business_type", "target_column": "businessType",
     "category": "分类", "aliases": ["请求类别", "请求业务类型", "日志分类"],
     "value_labels": BUSINESS_TYPE_LABELS,
     "description": ("后端接口调用（SERVER，有方法/耗时/响应码）与页面访问（CLIENT，仅页面 URL）；"
                     "两类指标口径不同，不得把页面访问并入成功率分母")},
    {"name": "来源应用通道", "name_en": "source_app_channel", "target_column": "appCode",
     "category": "分类", "aliases": ["应用通道", "来源应用", "客户端类型"],
     "value_labels": APP_CODE_LABELS,
     "description": "上报方的产品/通道（平台网页端、桌面客户端、移动端、官网、运营后台等）"},
    {"name": "后端服务", "name_en": "backend_service", "target_column": "application",
     "category": "分类", "aliases": ["服务名", "平台服务"], "value_labels": APPLICATION_LABELS,
     "description": "处理该请求的后端服务名"},
    {"name": "日志级别", "name_en": "request_log_level", "target_column": "logType",
     "category": "分类", "aliases": ["日志等级", "级别"], "value_labels": LOG_TYPE_LABELS,
     "description": "上报级别（INFO 为主，ERROR 极少）"},
    {"name": "请求方法", "name_en": "request_http_method", "target_column": "method",
     "category": "分类", "aliases": ["HTTP方法", "请求方式"], "value_labels": METHOD_LABELS,
     "description": "HTTP 方法（仅后端接口调用有值；页面访问为空）"},
    {"name": "请求URL", "name_en": "request_path_url", "target_column": "requestUrl",
     "category": "属性", "aliases": ["页面地址", "请求路径", "接口路径"], "value_labels": None,
     "description": ("请求地址：接口路径（后端调用）或整页 URL（页面访问）；"
                     "高基数，适合过滤与 TopN，不宜无过滤全量分组")},
    {"name": "响应码", "name_en": "request_response_code", "target_column": "responseParam.code",
     "category": "分类", "aliases": ["HTTP状态码", "状态码", "返回码", "错误码"],
     "value_labels": None,
     "description": "业务/HTTP 响应码（200 成功；3036/3060/2001/402/403/501 等为各类失败）"},
    {"name": "用户账号", "name_en": "request_user_account", "target_column": "userCode",
     "category": "属性", "aliases": ["账号", "登录账号", "请求账号"], "value_labels": None,
     "description": "登录用户账号码（用户数口径的去重键）"},
    {"name": "请求设备标识", "name_en": "request_device_id", "target_column": "pcId",
     "category": "属性", "aliases": ["客户端设备标识", "终端标识", "请求设备"], "value_labels": None,
     "description": "客户端机器标识（设备数口径的去重键）"},
    {"name": "请求客户端版本", "name_en": "request_client_version", "target_column": "cVersion",
     "category": "属性", "aliases": ["上报客户端版本", "终端版本", "请求版本"], "value_labels": None,
     "description": "上报客户端版本号"},
    {"name": "平台版本", "name_en": "request_platform_version", "target_column": "pVersion",
     "category": "属性", "aliases": ["版本号", "平台版本号"], "value_labels": None,
     "description": "平台版本号"},
    {"name": "请求日期", "name_en": "request_date", "target_column": "__time__",
     "category": "时间", "aliases": [], "value_labels": None,
     "description": "请求日期（天粒度，引擎内置事件时间）；SLS 默认只扫最近约 15 分钟，"
                    "必须显式给时间窗口"},
    {"name": "请求小时", "name_en": "request_hour", "target_column": "__time__",
     "category": "时间", "aliases": [], "value_labels": None,
     "description": "请求小时（小时粒度，引擎内置事件时间）"},
]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ds_name(en: str, suffix: str) -> str:
    return f"{en}{suffix}" if suffix else en


def dict_metric_spec(profile: dict, spec: dict) -> dict:
    """把指标定义按部署域展开成字典行（国际站 name/name_en 加站点后缀，aliases 含中国站全名）。"""
    suffix, en_suffix = profile["dict_name_suffix"], profile["dict_en_suffix"]
    aliases = list(spec["aliases"])
    if suffix:
        top = f"{spec['name']}{suffix}"
        # 问法一致：国际站行的别名里带上中国站业务名（本站按物理表只加载本站行，不会串味）
        aliases = [spec["name"]] + aliases
    return {
        "name": f"{spec['name']}{suffix}",
        "name_en": _ds_name(spec["name_en"], en_suffix),
        "agg_type": spec["agg_type"],
        "formula": spec["formula"],
        "unit": spec["unit"],
        "aliases": aliases,
        "description": spec["description"],
        "top": f"{spec['name']}{suffix}" if suffix else spec["name"],
    }


def dict_dimension_spec(profile: dict, spec: dict) -> dict:
    suffix, en_suffix = profile["dict_name_suffix"], profile["dict_en_suffix"]
    aliases = list(spec["aliases"])
    if suffix:
        aliases = [spec["name"]] + aliases
    return {
        "name": f"{spec['name']}{suffix}",
        "name_en": _ds_name(spec["name_en"], en_suffix),
        "target_column": spec["target_column"],
        "category": spec["category"],
        "aliases": aliases,
        "value_labels": spec["value_labels"],
        "description": spec["description"],
        "top": f"{spec['name']}{suffix}" if suffix else spec["name"],
    }


# ── 落库流程（同 build_app_usage_ontology 的合规顺序：草案→认领→激活→绑定→字典→站点→图/KB→核验）──
def _ids_by_name(names: list[str]) -> dict[str, int]:
    ph = ",".join(["%s"] * len(names))
    rows = execute_query(
        f"SELECT id, name FROM adh_datasources WHERE name IN ({ph})", tuple(names))
    return {str(r["name"]): int(r["id"]) for r in rows or []}


def _find_model(name: str, datasource_id: int):
    return execute_query(
        "SELECT id, name, status, kind FROM adh_ontology_models "
        "WHERE name = %s AND datasource_id = %s AND status IN ('draft','active') "
        "ORDER BY updated_at DESC LIMIT 1", (name, datasource_id), fetchone=True)


def _model_id(profile: dict, ds_id: int) -> int:
    row = _find_model(profile["model_name"], ds_id)
    if not row:
        raise RuntimeError(f"模型「{profile['model_name']}」不存在（前序步骤未落库）")
    return int(row["id"])


def _alias_list(raw) -> list[str]:
    if isinstance(raw, (str, bytes)) and raw:
        try:
            got = json.loads(raw)
        except ValueError:
            return []
        return [str(x).strip() for x in got] if isinstance(got, list) else []
    return [str(x).strip() for x in raw] if isinstance(raw, list) else []


def build_doc(profile: dict) -> dict:
    """canonical doc（唯一可编辑事实源；执行绑定走部署绑定层，不写进 doc）。"""
    return {
        "datasource_name": profile["ds_name"],
        "domain": DOMAIN,
        "description": (
            f"{profile['note']}。以「{profile['object_display']}」为事实对象，覆盖后端接口调用（SERVER）"
            f"与网页端页面访问（CLIENT）两类请求的调用量、成功率、耗时（均值/P95）、登录用户数、"
            f"访问设备数、独立IP数。{DOMAIN_EDGE}{SITE_SCOPE_NOTE}"),
        "kind": "business",
        "objects": [{
            "key": profile["object_key"],
            "display_name": profile["object_display"],
            "aliases": list(OBJECT_ALIASES),
            "description": (
                f"平台自身产生的一条请求日志（网关/前端上报）。{DOMAIN_EDGE}"
                "「请求类型」区分后端接口调用与页面访问：成功率与耗时类口径只对后端接口调用成立，"
                "页面访问行没有请求方法、耗时与响应码。中国站与国际站是两个本体（物理表不同构），"
                "业务名与别名一致、问法相同，但两站数据不得相加。"),
            "primary_table": profile["table"],
            "properties": [dict(p) for p in PROPERTIES],
            "metrics": [dict_metric_spec(profile, m)["name"] for m in METRIC_SPECS],
        }],
        "sources": [],
    }


def preflight(ds_ids: dict[str, int], profile: dict) -> list[str]:
    """写入前的可诊断检查：源/站点/元数据列/名字冲突，任一不过即整体不写（不半截落库）。"""
    from backend.modules.catalog.services.ontology_service import object_identity_key
    from backend.modules.catalog.services.physical_ref import ENGINE_BUILTIN_COLUMNS

    tag = f"[{profile['key']}]"
    problems: list[str] = []
    for _code, ds in profile["site_ds"]:
        if ds not in ds_ids:
            problems.append(f"{tag} 数据源未注册: {ds}")
    sites = {str(r.get("site_code")) for r in
             execute_query("SELECT site_code FROM adh_sites WHERE is_active = 1") or []}
    for code, _ds in profile["site_ds"]:
        if code not in sites:
            problems.append(f"{tag} 站点未启用或缺失: {code}")

    # 物理表与属性列实存（表名/列名写错 → 字典写进去也取不到数，宁缺勿错）
    ds_id = int(ds_ids.get(profile["ds_name"]) or 0)
    known: set[str] = set()
    if ds_id:
        rows = execute_query(
            "SELECT column_name AS c FROM adh_column_metadata "
            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
            (ds_id, profile["table"])) or []
        known = {str(r["c"]).strip().lower() for r in rows}
    if not known:
        problems.append(f"{tag} 表元数据零列（表名错或元数据未同步）: "
                        f"{profile['ds_name']}.{profile['table']}")
    builtin = {c.lower() for c in ENGINE_BUILTIN_COLUMNS}
    for p in PROPERTIES:
        col = str(p["column"]).strip().lower()
        if col not in builtin and col not in known:
            problems.append(f"{tag} 属性列不在 {profile['table']} 元数据: {p['column']}（{p['name']}）")

    # 名字/别名冲突（字典 name/name_en 全局唯一、别名参与解析；重名即串味）。
    # 本域两模型的行不算冲突：国际站行的 aliases 刻意包含中国站业务名（用户口径「用别名指代」）。
    mine = {p["object_key"] for p in PROFILES}

    def _occupied(table: str) -> tuple[set, dict]:
        names, alias_owner = set(), {}
        rows = execute_query(
            f"SELECT name, name_en, aliases, COALESCE(bound_object_key,'') AS bok "
            f"FROM {table} WHERE is_active = 1") or []
        for r in rows:
            if str(r.get("bok") or "") in mine:
                continue
            names.add(str(r["name"]).strip())
            if r.get("name_en"):
                names.add(str(r["name_en"]).strip())
            for a in _alias_list(r.get("aliases")):
                alias_owner.setdefault(a, str(r["name"]))
        return names, alias_owner

    for table, specs, kind, expand in (
            ("adh_metrics", METRIC_SPECS, "指标", dict_metric_spec),
            ("adh_dimensions", DIMENSION_SPECS, "维度", dict_dimension_spec)):
        names, alias_owner = _occupied(table)
        for spec in specs:
            d = expand(profile, spec)
            if d["name"] in names:
                problems.append(f"{tag} {kind}名已存在: {d['name']}")
            if d["name_en"] in names:
                problems.append(f"{tag} {kind}英文句柄已被占用: {d['name_en']}（{d['name']}）")
            for a in d["aliases"]:
                if a in names:
                    problems.append(f"{tag} {kind}别名与既有业务名冲突: {a}（{d['name']}）")
                elif a in alias_owner:
                    problems.append(f"{tag} {kind}别名已被「{alias_owner[a]}」占用: {a}（{d['name']}）")

    # 对象 key 身份撞车（与 activate 同口径：撞了检索歧义、激活硬阻断）
    ik = object_identity_key(profile["object_key"])
    for r in execute_query("SELECT DISTINCT object_key FROM adh_ontology_objects WHERE is_active = 1") or []:
        key = str(r["object_key"])
        if key not in mine and object_identity_key(key) == ik:
            problems.append(f"{tag} 对象 key 身份与既有对象冲突: {key}")

    # 同名模型不得挂在别的数据源（否则界面与知识库出现两个同名本体）
    for r in execute_query(
            "SELECT datasource_id FROM adh_ontology_models WHERE name = %s "
            "AND status IN ('draft','active')", (profile["model_name"],)) or []:
        if int(r["datasource_id"] or 0) != ds_id:
            problems.append(f"{tag} 同名模型已挂在数据源 id={r['datasource_id']}，与计划主源不一致")
    return problems


def ensure_datasource_access(ds_ids: dict[str, int], apply: bool) -> None:
    """角色数据源授权（可查性唯一裁决点）：缺授权时新域会 fail-closed 拒查。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_roles WHERE is_active = 1 ORDER BY id")
            roles = cur.fetchall() or []
            role_id = int(roles[0]["id"]) if roles else 0
            if not role_id:
                _log("[access][warn] 未找到可用角色，跳过授权检查（新域可能因无授权不可查）")
                return
            missing = []
            for ds, ds_id in ds_ids.items():
                cur.execute("SELECT 1 FROM adh_role_datasource_access "
                            "WHERE role_id = %s AND datasource_id = %s", (role_id, ds_id))
                if not cur.fetchone():
                    missing.append(ds)
            if not missing:
                _log("[access] 管理员角色对全部 SLS 源已授权")
                return
            _log(f"[access] {'待补授权' if not apply else '补授权'}: {missing} → role {role_id}")
            if apply:
                for ds in missing:
                    cur.execute("INSERT IGNORE INTO adh_role_datasource_access "
                                "(role_id, datasource_id) VALUES (%s, %s)", (role_id, ds_ids[ds]))
                conn.commit()


def ensure_model(profile: dict, doc: dict, ds_id: int, apply: bool) -> int:
    """草案落库（save_generated_draft 建新 / save_draft 就地改）+ 模型级站点声明。"""
    tag = f"[{profile['key']}]"
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    row = _find_model(profile["model_name"], ds_id)
    if not row:
        if not apply:
            _log(f"{tag}[model] dry-run：将新建业务本体草案「{profile['model_name']}」"
                 f"（主源 {profile['ds_name']}.{profile['table']}）")
            return 0
        res = ontology_service.save_generated_draft(
            ds_id, [dict(o) for o in doc["objects"]], domain=doc["domain"],
            description=doc["description"], append=False, created_by=CREATED_BY)
        model_id = int(res["id"])
        _log(f"{tag}[model] 草案已建 (id={model_id}, name={res.get('name')})")
    else:
        model_id = int(row["id"])
        _log(f"{tag}[model] 已存在「{profile['model_name']}」(id={model_id}, status={row['status']})，按 doc 覆盖")
    if not apply:
        return model_id
    saved = ontology_service.save_draft(model_id, payload, name=profile["model_name"])
    for w in saved.get("validation_warnings") or []:
        _log(f"{tag}[model][warn] {w}")
    site_service.set_model_deploy_sites(
        model_id, [{"site_code": c, "datasource": d} for c, d in profile["site_ds"]])
    _log(f"{tag}[model] doc 已保存 + deploy_sites {len(profile['site_ds'])} 站: "
         + ", ".join(f"{c}→{d}" for c, d in profile["site_ds"]))
    return model_id


def claim_products(profile: dict, model_id: int, apply: bool) -> None:
    """激活门禁（find_unregistered_tables）的前置：把对象来源表认领为数据产品（幂等）。"""
    from backend.scripts import claim_ontology_tables_as_products as claim

    tag = f"[{profile['key']}]"
    if not model_id:
        _log(f"{tag}[product] dry-run：草案尚未建模型，激活前需认领 "
             f"{profile['ds_name']}.{profile['table']}")
        return
    res = claim.claim_model(model_id, dry_run=not apply, owner=CREATED_BY)
    _log(f"{tag}[product] {'dry-run 待认领' if not apply else '已认领'}: "
         f"{res.get('claimed') or '（无缺项）'} | 门禁剩余: {res.get('gate_missing') or '（空=通过）'}")
    for w in res.get("warnings") or []:
        _log(f"{tag}[product][warn] {w}")


def activate_model(profile: dict, model_id: int, apply: bool) -> None:
    tag = f"[{profile['key']}]"
    if not model_id:
        return
    if not apply:
        _log(f"{tag}[activate] dry-run：跳过激活（正式执行时走级联：展开对象→枚举→图谱→知识库）")
        return
    model = ontology_service.activate(model_id)
    _log(f"{tag}[activate] 已激活 (status={model['status']}, objects={model.get('object_count')})")


def ensure_binding(profile: dict, doc: dict, ds_id: int, apply: bool) -> None:
    """部署绑定层写入（唯一事实源；走 `_persist_bindings`，不自造 SQL）。"""
    tag = f"[{profile['key']}]"
    if not apply:
        _log(f"{tag}[binding] dry-run：将写主源契约行 bind_kind=primary / "
             f"{profile['ds_name']}.{profile['table']}（列映射由 doc properties 实存裁决生成）")
        return
    bdoc = dict(doc)
    bdoc["datasource_id"] = ds_id
    obj = dict(bdoc["objects"][0])
    obj["execution_binding"] = {
        "bind_kind": "primary",
        "physical_table": profile["table"],
        "datasource_name": profile["ds_name"],
        "query_mode": "raw_source",
        "allow_full_scan": 1,
        "sync_state": "bound",
    }
    bdoc["objects"] = [obj]
    stats = ontology_yaml_import._persist_bindings(bdoc, _model_id(profile, ds_id))
    _log(f"{tag}[binding] 契约行写入: written={stats.get('written')} drifted={stats.get('drifted')} "
         f"unbound={stats.get('unbound')}")


def _owned_row(table: str, biz_name: str, object_key: str):
    """同名行只能更新**归属本域对象**的那一行；跨域同名一律阻断（绝不覆盖别域口径）。"""
    row = execute_query(
        f"SELECT id, COALESCE(bound_object_key,'') AS bok FROM {table} WHERE name = %s",
        (biz_name,), fetchone=True)
    if not row:
        return None
    if str(row["bok"] or "") != object_key:
        raise RuntimeError(
            f"{table} 已存在同名行「{biz_name}」但归属对象 {row['bok']!r} ≠ {object_key}"
            "（跨域同名，拒绝覆盖；请改名或人工裁决）")
    return row


def ensure_dicts(profile: dict, apply: bool) -> None:
    """指标/维度字典（幂等**收敛**：已存在则对齐到脚本定义，不跳过）。"""
    tag = f"[{profile['key']}]"
    created, updated = [], []
    for spec in METRIC_SPECS:
        d = dict_metric_spec(profile, spec)
        payload = {"display_name": d["name"], "name": d["name_en"],
                   "calculation_type": d["agg_type"], "expression": d["formula"],
                   "unit": d["unit"], "description": d["description"],
                   "aliases": d["aliases"], "category": "可观测",
                   "bound_object_key": profile["object_key"], "target_table": profile["table"]}
        row = _owned_row("adh_metrics", d["name"], profile["object_key"])
        if not apply:
            (updated if row else created).append(f"metric:{d['name']}")
            continue
        if row:
            metrics_service.update_metric(int(row["id"]), payload)
            updated.append(f"metric:{d['name']}")
        else:
            res = metrics_service.create_metric(payload)
            created.append(f"metric:{d['name']}(id={res['id']})")
    for spec in DIMENSION_SPECS:
        d = dict_dimension_spec(profile, spec)
        # 维度入参键位与指标不同：英文句柄走 name_en（create_metric 走 name），写错会落成中文句柄
        payload = {"display_name": d["name"], "name_en": d["name_en"],
                   "target_table": profile["table"], "target_column": d["target_column"],
                   "category": d["category"], "description": d["description"],
                   "bound_object_key": profile["object_key"], "aliases": d["aliases"],
                   "value_labels": d["value_labels"], "certified": 0}
        row = _owned_row("adh_dimensions", d["name"], profile["object_key"])
        if not apply:
            (updated if row else created).append(f"dimension:{d['name']}")
            continue
        if row:
            metrics_service.update_dimension(int(row["id"]), payload)
            updated.append(f"dimension:{d['name']}")
        else:
            res = metrics_service.create_dimension(payload)
            created.append(f"dimension:{d['name']}(id={res['id']})")
    _log(f"{tag}[dict] {'dry-run 待写' if not apply else '已建'}: {created or '（无新建）'}")
    if updated:
        _log(f"{tag}[dict] 已对齐现有行: {updated}")


def deploy_sites_products(profile: dict, ds_ids: dict[str, int], apply: bool) -> None:
    """各站数据产品行（同构路由的登记依据；不同构行留例外清单，不静默删差异）。"""
    from backend.scripts import deploy_contract_bindings as dcb

    tag = f"[{profile['key']}]"
    if not apply:
        _log(f"{tag}[sites] dry-run：将跑 deploy_contract_bindings --model（补各站产品行）")
        return
    code = dcb.run(apply=True, only_model=_model_id(profile, ds_ids[profile["ds_name"]]))
    _log(f"{tag}[sites] deploy_contract_bindings 退出码={code}")


def rebuild_graph_and_kb(profile: dict, ds_ids: dict[str, int], apply: bool) -> None:
    """重建业务图（按**每个**部署站源各一次）+ 推送上云文档。"""
    tag = f"[{profile['key']}]"
    if not apply:
        _log(f"{tag}[graph] dry-run：将重建业务图并推送上云文档")
        return
    for ds in sorted({d for _c, d in profile["site_ds"]}):
        g = ontology_yaml_import._rebuild_graph(ds_ids[ds], "business")
        if g.get("success"):
            _log(f"{tag}[graph] {ds} 业务图重建完成 tables={g.get('tables')} relations={g.get('relations')}")
        else:
            _log(f"{tag}[graph][warn] {ds} 图重建未完成（doc 已落库，图是滞后状态）: "
                 f"{str(g.get('error') or g.get('message') or '未给出原因')[:200]}")
    res = ontology_kb_sync.sync_model_to_qmind(_model_id(profile, ds_ids[profile["ds_name"]]))
    if res.get("synced"):
        _log(f"{tag}[kb] 上云文档已同步 synced={res.get('synced')} targets={res.get('targets')}")
    else:
        _log(f"{tag}[kb][warn] 未同步（原因 {res.get('skipped')}）：可在本体页手动重试，不得当成已发布")


def verify(profile: dict, ds_id: int) -> list[str]:
    """发布后核验：门禁全空 + 对象/绑定/字典/站点齐备，任何缺项显式报出。"""
    tag = f"[{profile['key']}]"
    problems: list[str] = []
    model = ontology_service.get_model(_model_id(profile, ds_id))
    doc = json.loads(model.get("json_content") or "{}")
    if model.get("status") != "active":
        problems.append(f"{tag} 模型状态非 active: {model.get('status')}")
    for fn, label in ((lambda: ontology_service.find_unregistered_tables(doc), "未认领来源表"),
                      (lambda: ontology_service.find_orphan_dict_bindings(doc), "字典孤儿引用"),
                      (lambda: [i for i in ontology_service.find_m234_issues(doc)
                                if i["severity"] == "error"], "M2/M3/M4 引用")):
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 — 核验本身失败也要显式报
            got = [f"检查异常 {type(exc).__name__}: {exc}"]
        if got:
            problems.append(f"{tag} {label}: {got}")
    issues = ontology_service.find_deploy_site_issues(int(model["id"]))
    if issues:
        problems.append(f"{tag} 适用站点: {issues}")

    obj_rows = execute_query(
        "SELECT id FROM adh_ontology_objects WHERE model_id = %s AND object_key = %s AND is_active = 1",
        (int(model["id"]), profile["object_key"])) or []
    if len(obj_rows) != 1:
        problems.append(f"{tag} 对象展开行异常: {len(obj_rows)} 行")

    binds = execute_query(
        "SELECT datasource_name, physical_table, status FROM adh_ontology_bindings "
        "WHERE object_key = %s AND status = 'active'", (profile["object_key"],)) or []
    if len(binds) != 1:
        problems.append(f"{tag} 部署绑定层有效行 {len(binds)} 行（应为 1：主源契约行，各站按 deploy_sites 路由）")
    elif str(binds[0]["physical_table"] or "") != profile["table"]:
        problems.append(f"{tag} 绑定物理表={binds[0]['physical_table']!r} ≠ {profile['table']}")

    for table, specs, kind, expand in (
            ("adh_metrics", METRIC_SPECS, "指标", dict_metric_spec),
            ("adh_dimensions", DIMENSION_SPECS, "维度", dict_dimension_spec)):
        for spec in specs:
            d = expand(profile, spec)
            row = execute_query(
                f"SELECT target_table, COALESCE(bound_object_key,'') AS bok FROM {table} WHERE name = %s",
                (d["name"],), fetchone=True)
            if not row:
                problems.append(f"{tag} {kind}缺失: {d['name']}")
                continue
            if str(row["target_table"] or "") != profile["table"]:
                problems.append(f"{tag} {kind}「{d['name']}」target_table={row['target_table']!r} "
                                f"≠ {profile['table']}（planner 按物理表加载字典，错值等于不可编译）")
            if str(row["bok"] or "") != profile["object_key"]:
                problems.append(f"{tag} {kind}「{d['name']}」归属对象={row['bok']!r} "
                                f"≠ {profile['object_key']}")

    row = execute_query("SELECT deploy_sites FROM adh_ontology_models WHERE id = %s",
                        (int(model["id"]),), fetchone=True)
    sites = json.loads(row["deploy_sites"] or "[]") if row else []
    if {str(s.get("site_code")) for s in sites} != {c for c, _ in profile["site_ds"]}:
        problems.append(f"{tag} deploy_sites 与计划不一致: {sites}")
    return problems


def verify_alias_bridge() -> list[str]:
    """核验用户口径「用别名指代」：国际站每一行都必须把**中国站业务名**登记为可用名。

    字典全局唯一才让国际站 name 带后缀；若国际站行没把中国站业务名放进 name/aliases，
    用户在**国际站**问「平台接口成功率」就命中不到本域（只能模糊回抛），口径就白定了。
    """
    problems: list[str] = []
    cn, intl = PROFILES[0], PROFILES[1]
    for table, specs, kind, expand in (
            ("adh_metrics", METRIC_SPECS, "指标", dict_metric_spec),
            ("adh_dimensions", DIMENSION_SPECS, "维度", dict_dimension_spec)):
        for spec in specs:
            want, got = expand(cn, spec)["name"], expand(intl, spec)["name"]
            row = execute_query(f"SELECT name, name_en, aliases FROM {table} WHERE name = %s",
                                (got,), fetchone=True)
            if not row:
                problems.append(f"[alias] {kind}「{got}」未落库（国际站问法无法指代）")
                continue
            terms = {str(row["name"]).strip(), str(row["name_en"] or "").strip()}
            terms |= set(_alias_list(row["aliases"]))
            if want not in terms:
                problems.append(f"[alias] {kind}「{got}」未把中国站业务名「{want}」登记为别名")
    return problems


def bind_skill(apply: bool) -> list[str]:
    """把技能登记到 AS-BOT（幂等追加，返回问题清单）。

    技能正文的唯一事实源是 `config/skills/{name}/SKILL.md`；这里只登记「谁带它」。
    写入口走平台 API 层的 `update_as_bot()`（JSON 列序列化 + 配置缓存失效同源），不自造 UPDATE，
    也不去碰 tools（工具授权是另一件事，混在一起会把角色能力改坏）。
    """
    from backend.modules.mind.config.skill_loader import load_skill

    if not load_skill(SKILL_NAME):
        return [f"技能文件缺失: config/skills/{SKILL_NAME}/SKILL.md（绑定不会生效）"]
    from backend.modules.platform.api.as_bots import AsBotUpdate, update_as_bot

    problems: list[str] = []
    for key in SKILL_BOTS:
        row = execute_query("SELECT id, skills FROM adh_as_bots WHERE as_bot_key = %s",
                            (key,), fetchone=True)
        if not row:
            problems.append(f"AS-BOT 不存在: {key}")
            continue
        names = _alias_list(row["skills"])
        if SKILL_NAME in names:
            _log(f"[skill] {key} 已绑定 {SKILL_NAME}（跳过）")
            continue
        _log(f"[skill] {'待绑定' if not apply else '绑定'} {key}: +{SKILL_NAME}")
        if apply:
            update_as_bot(int(row["id"]), AsBotUpdate(skills=names + [SKILL_NAME]),
                          user={"role": "admin"})
    return problems


def run_profile(profile: dict, ds_ids: dict[str, int], apply: bool) -> list[str]:
    """单部署域全流程；返回核验问题（空=通过）。"""
    tag = f"[{profile['key']}]"
    doc = build_doc(profile)
    _log(f"{tag}[doc] 对象 {profile['object_key']}「{profile['object_display']}」 "
         f"属性 {len(PROPERTIES)} 个 / 指标 {len(METRIC_SPECS)} 个 / 维度 {len(DIMENSION_SPECS)} 个")
    ds_id = ds_ids[profile["ds_name"]]
    model_id = ensure_model(profile, doc, ds_id, apply)
    claim_products(profile, model_id, apply)
    if not apply:
        return []
    activate_model(profile, model_id, apply)
    ensure_binding(profile, doc, ds_id, apply)
    ensure_dicts(profile, apply)
    deploy_sites_products(profile, ds_ids, apply)
    rebuild_graph_and_kb(profile, ds_ids, apply)
    return verify(profile, ds_id)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="构建「平台请求日志」业务本体（中国站 + 国际站两模型，别名互为指代）")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    ap.add_argument("--only", choices=[p["key"] for p in PROFILES],
                    help="只跑某个部署域（默认两个都跑）")
    args = ap.parse_args()
    apply = bool(args.apply)
    profiles = [p for p in PROFILES if not args.only or p["key"] == args.only]
    _log(f"== {'执行' if apply else 'dry-run（只报计划，不写库）'} | 部署域: "
         + ", ".join(p["key"] for p in profiles) + " ==")

    ds_ids = _ids_by_name(sorted({d for p in PROFILES for _c, d in p["site_ds"]}))
    missing = [p["ds_name"] for p in profiles if p["ds_name"] not in ds_ids]
    if missing:
        _log(f"[FATAL] 主源未注册: {missing}")
        return 2

    problems: list[str] = []
    for p in profiles:
        problems += preflight(ds_ids, p)
    for p in problems:
        _log(f"[preflight][FAIL] {p}")
    if problems:
        _log("[FATAL] 前置检查未过，未写入任何内容")
        return 2

    ensure_datasource_access(ds_ids, apply)
    for p in profiles:
        problems += run_profile(p, ds_ids, apply)
    problems += bind_skill(apply)
    if not apply:
        _log("== 后续步骤（激活/绑定/字典/站点/图谱/知识库）需 --apply 才执行 ==")
        return 0
    if len(profiles) == len(PROFILES):
        problems += verify_alias_bridge()
    for p in problems:
        _log(f"[verify][FAIL] {p}")
    if problems:
        _log("[FATAL] 落库后核验未过（模型已落库，按上面缺项逐条修，不要当成已交付）")
        return 1
    _log("[verify] 建模齐备：两模型 active / 门禁空 / 绑定生效 / 字典落各自物理表 / "
         "站点齐备 / 国际站别名已指代中国站业务名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())