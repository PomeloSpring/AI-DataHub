"""构建「云平台运行日志（ES）」独立业务本体：4 对象 + 指标/维度字典（幂等，dry-run 默认）。

背景：数据源 `cloud-platform-es`（db_type=es）下有四张运行日志索引——
设备操作、设备告警、软件操作、第三方接口调用，共约 3 亿条文档，但**零本体、零字典**。
结果 ChatBI 拿不到这域语义，只能拿业务词去猜列名；而 ES 没有列注释可依赖，猜必错。

用户口径（2026-11-18 拍板）：
- **独立新模型**（不并入 SLS「应用使用事件本体」/「数据仓库模型」等既有模型）；
- 设备操作日志**要单独建对象**；
- **尽量多抽取可分析的语义资产**（可分组/可过滤的列尽量登记）；
- 枚举**只登记码值**（`value_labels` 一律不填，码值清单写进 description，不编中文标签）；
  **唯一例外**：「应用分组」是**分桶**维度（多码 → 同一业务标签），必须填 `value_labels` 才能
  表达分组与按标签过滤（不填则分组退化回 25 个裸码、且「三方集成App」这类问法无处落地）；
  分桶标签取看板 ds#36/56 既有业务词汇，不是给单码硬编中文名。
- ES 侧**无敏感列**（不新增 adh_sensitive_fields；既有 requestBody/responseHeader/responseBody
  三条 block 属系统治理基线，本脚本不建模这三列，保持不动）。

实机取证（2026-11-18，脚本 `logs/probe_es_*` 同批，模型事实一律以实采为准）：
- 行数：设备操作 281,683,306 / 软件操作 13,876,592 / 设备告警 4,501,028 / 三方接口 2,632,148；
- **业务时间列是 text/keyword**（`operationTime.keyword` 形如 '2026-11-18 14:12:30'），
  可直接字符串比较与切片；`@timestamp` 在本源**不可用**——设备告警索引跨滚动索引把
  `@timestamp` 映射成 date 与 keyword 两种类型，查询直接 400（ambiguation），故四个对象
  一律以业务时间列作时间维度，不登记 `@timestamp`；
- ES 8 Calcite 子集：无 JOIN/窗口/派生表/OFFSET；`SUBSTRING` 三参 1 起；时间桶走定长切片；
  季度粒度编译期拒绝；必带 LIMIT（planner 已按默认 1000 补齐）；
- 高基数列（serialNumber/userCode/companyCode/businessCode）**不能分组**（实测 3 天窗口
  GROUP BY 即 30s 超时），但**可以** `= / LIKE` 过滤与 `COUNT(DISTINCT)` 去重——字典里登记
  并在 description 明确"仅用于过滤，不要按它分组"，把能力边界交给 LLM，而不是让它试错。

写入路径（本体建模规范 §1，绝不绕过级联直改派生表）：
    canonical doc → save_generated_draft/save_draft → claim 数据产品 → activate 级联
    → ontology_yaml_import._persist_bindings（部署绑定层）→ metrics_service 字典
    → 图谱重建 → 知识库同步

用法（repo 根目录）：
    venv/bin/python -m backend.scripts.build_es_platform_logs_ontology            # dry-run
    venv/bin/python -m backend.scripts.build_es_platform_logs_ontology --apply    # 落库并发布
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

CREATED_BY = "script:build_es_platform_logs_ontology"

DS_NAME = "cloud-platform-es"
MODEL_NAME = "云平台运行日志本体（设备·软件·接口遥测）"
DOMAIN = "云平台运行日志域（设备/软件/第三方接口遥测）"

# 与 SLS「应用使用事件本体」的分界：两个域都叫"日志/遥测"，不点名必串。
DOMAIN_EDGE = (
    "本域只覆盖云平台 Elasticsearch 侧的四类运行日志：设备操作、设备告警、软件操作、"
    "第三方接口调用。桌面 app 的埋点使用事件在另一模型「应用使用事件本体（桌面 app 遥测）」"
    "（SLS 通道，对象 app_usage_event），两者的条数口径不同，不得相加或互相替代。"
)

ENGINE_NOTE = (
    "检索层能力边界（Elasticsearch）：只支持单索引聚合，不支持 JOIN / 窗口函数 / 派生表 / "
    "季度粒度；按时间看趋势**必须**指定时间粒度（日/周/月），且默认只取前 1000 行、"
    "单次查询有 30 秒预算——时间长窗口请配时间粒度或收窄范围。"
)

SITE_NOTE = (
    "站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；"
    "取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。"
)

ENUM_NOTE = "枚举只登记实采码值，不编中文标签（码值本身的业务含义请以产品定义为准确认）。"

# 数据质量事实（实测，不修数据只公开口径）：滚动窗口只有下界（与其它引擎的
# `>= DATE_SUB(NOW(),...)` 同口径），源内偶有远期脏时间——设备操作日志实测 3 条落在
# 2036/2063/2112 年，按天趋势会多出异常远期桶。这是源数据问题，不得当成模型缺陷、
# 也不得擅自加“上限过滤”把设备时钟偏移的正当事件一起丢掉。
DATA_QUALITY_NOTE = (
    "数据质量提醒：源内偶有远期脏时间（实测设备操作日志有 3 条落在 2036/2063/2112 年），"
    "按时间粒度看趋势时可能出现异常远期桶，属源端脏数据；汇报趋势时应以正常日期区间为准。"
)

# 与「数据仓库模型」的分界（用户拍板：本域独立成模型，但必须点名同业务事实的物化形态，
# 否则同一句「设备操作日志」会检索串味：一个绑 Doris 物化宽表、一个绑 ES 原始日志）。
DW_EDGE = (
    "与「数据仓库模型」的分界：该模型里的「设备操作日志」对象绑 Doris 数仓的"
    "清洗后物化宽表（dwd 层），本对象是**未清洗的原始运行日志**——粒度更细、"
    "带明细文本与固件版本，但不做补全与去重。两者条数口径不同，不得相加、"
    "也不得互相替代：问原始流水/明细/固件相关用本对象；问已汇总或已清洗口径"
    "用「数据仓库模型」的对应对象。"
)

# ── 各对象实采码值（2026-11-15~18 窗口，3 天样本；只记录码值，不编标签）─────────
ACTION_CODES = ("tiptype, scan, battery, refine, transaction, sysinfo, cut, bitepressure, "
                "toothmark, lock, undercut, matching, impressionregion")
OP_APP_CODES = "CONNECT, SCANPRO"
WARN_LEVEL_CODES = "warning, error, crash, critical, （空串）"
WARN_HARDWARE_CODES = "config, msp430, fpga, sensor, i2c, dlpc, imu, spi, （空串）"
WARN_APP_CODES = "ASSCANPRO, SCANPRO, CONNECT"
SW_APP_CODES = ("ASSCANPRO, ASWEB, CONNECT, ModelBuilder, OralHealthReport, NHDataHubIntegration, "
                "MeshCompare, NewOrthoSim, SmileDesign, SprintRayIntegration, PressureImpression, "
                "coDiagnostiXIntegration（共 25 个取值，按量降序）")
SW_BUSINESS_CODES = "openApp, orderExport, caseExport"
# 17 个第三方集成应用码（与看板 ds#36/56 分桶清单同源：改一处必须同步另一处）。
# 显式枚举而非 LIKE '%Integration%'：新增集成码不得被**静默**算作三方（多算/少算都是错数）。
SW_TP_APP_LIST = (
    "NHDataHubIntegration", "SprintRayIntegration", "FormlabsIntegration",
    "coDiagnostiXIntegration", "ClinuxCADIntegration", "AsigaComposerIntegration",
    "UnizDental", "DentreadIntegration", "ShapeWareIntegration",
    "CameoPrinterIntegration", "3DXWORKSIntegration", "LuxDesignIntegration",
    "NHKeaIntegration", "CircleOneIntegration", "RapidShapeIntegration",
    "HeyGearsIntegration", "DentbirdIntegration",
)
SW_TP_APP_CODES = ", ".join(SW_TP_APP_LIST) + "（共 17 个第三方集成应用码）"
# 分桶标签：17 个集成码 → 「第三方集成App」；NewOrthoSim/OrthoSim → 「Ortho Simulation」。
# 未登记的码值由 planner 的 CASE ELSE 原样输出（不静默归入看板的「其他未归类App」桶）。
SW_APP_GROUP_LABELS = {c: "第三方集成App" for c in SW_TP_APP_LIST}
SW_APP_GROUP_LABELS.update({"NewOrthoSim": "Ortho Simulation", "OrthoSim": "Ortho Simulation"})
SW_SOURCE_CODES = "ASCONNECT, ASWEB"
SW_APPMANAGER_CODES = ("ALLIEDSTAR, CEFLAMYSCAN, NEURALHIVE, OWANDYIOS, NEOSS, DYNAMIC, "
                       "CEFLAVISIOSCAN, AIDITE3D, CEFLAALPHASCAN, CEFLAOPTISCAN")
TP_BUSINESS_MODEL_CODES = ("send_order_callback, upload_convert_completed_callback, "
                           "authorization_completed_callback, upload_zip_completed_callback, "
                           "upload_attachments_completed_callback, scan_completed_callback")
TP_STATUS_CODES = "FAIL, SUCCESS, ERROR"


def _in_list(codes) -> str:
    """码值清单 → SQL `IN` 列表（不含括号）；formula 内嵌时与 SW_TP_APP_LIST 同源，不另抄一份。"""
    return ", ".join(f"'{c}'" for c in codes)


# ── 对象定义 ──────────────────────────────────────────────────────────────
TABLE_OP = "t_equipment_operation_log"
TABLE_WARN = "t_equipment_warning_log"
TABLE_SW = "t_software_app_operation_log"
TABLE_TP = "sys_thrid_request_log"

# 对象 key 一律带源前缀（`cloud_es_`）：本体对象 key 参与检索归一与部署绑定解析，
# 裸业务 key（如 `equipment_operation_log`）与「数据仓库模型」的同名对象撞车——
# 同一句问法会命中两个对象（一个绑 Doris 物化表、一个绑 ES 原始索引），属定位漂移。
OBJ_OP = "cloud_es_equipment_operation_log"
OBJ_WARN = "cloud_es_equipment_warning_log"
OBJ_SW = "cloud_es_software_app_operation_log"
OBJ_TP = "cloud_es_third_party_request_log"

# 每个对象：properties（本体属性，尽量全）/ dimensions（字典维度）/ metrics（指标 name 引用）
OBJECTS: list[dict] = [
    {
        "key": OBJ_OP,
        "display_name": "云平台设备操作日志",
        "table": TABLE_OP,
        # 别名一律带来源限定：裸词「设备操作日志」是「数据仓库模型」里 Doris 物化对象的
        # 显示名，两边共用会让同一句问法命中两个对象（定位漂移）。
        "aliases": ["云平台设备操作日志", "ES设备操作日志", "设备操作原始日志",
                    "设备操作原始流水", "云平台设备操作事件", OBJ_OP],
        "description": (
            f"云平台记录的**单次设备操作事件**（一次扫描/取模/电量读取/事务/系统信息查询等），"
            f"当前约 2.82 亿条，是设备侧使用强度的主事实表。"
            f"「操作类型」是本对象最核心的分析维度；「设备序列号」「用户账号」用于过滤单台设备/"
            f"单个用户或去重计数（按它们分组会超时）。"
            + DW_EDGE + DATA_QUALITY_NOTE + ENGINE_NOTE + SITE_NOTE + ENUM_NOTE),
        "properties": [
            {"column": "operationTime.keyword", "name": "操作时间", "type": "datetime",
             "description": "设备侧事件时间（本地时间，形如 2026-11-18 14:12:30）"},
            {"column": "action.keyword", "name": "操作类型", "type": "string",
             "description": f"操作行为码值：{ACTION_CODES}"},
            {"column": "appCode.keyword", "name": "操作应用", "type": "string",
             "description": f"上报操作的客户端产品码值：{OP_APP_CODES}"},
            {"column": "application.keyword", "name": "应用名称", "type": "string",
             "description": "应用名称字段（当前样本 99.99% 为空，仅少量带 cloud-platform-v2）"},
            {"column": "region.keyword", "name": "站点区域", "type": "string",
             "description": "站点区域码值：cn / global / eu / jp / uk（与 site 列同值）"},
            {"column": "site.keyword", "name": "站点", "type": "string",
             "description": "站点码值，与 region 列取值一致（分析时用「站点区域」一个维度即可）"},
            {"column": "cVersion.keyword", "name": "操作客户端版本", "type": "string",
             "description": "客户端版本号（形如 1.0.44.0）"},
            {"column": "fVersion.keyword", "name": "操作固件版本", "type": "string",
             "description": "设备固件版本号（形如 1.0.3.3）"},
            {"column": "serialNumber.keyword", "name": "操作设备序列号", "type": "string",
             "description": "设备序列号（去重设备数口径的唯一键；仅用于过滤/去重，不要分组）"},
            {"column": "userCode.keyword", "name": "操作用户账号", "type": "string",
             "description": "操作人账号码（去重用户数口径的唯一键；仅用于过滤/去重，不要分组）"},
            {"column": "detail.keyword", "name": "操作明细", "type": "string",
             "description": "操作明细文本（自由文本，适合 LIKE 关键词过滤，不适合分组）"},
            {"column": "pcId.keyword", "name": "操作电脑标识", "type": "string",
             "description": "客户端机器标识（仅用于过滤/去重）"},
            {"column": "tid.keyword", "name": "事务标识", "type": "string",
             "description": "日志侧事务标识（高基数，仅用于定位单条链路）"},
            {"column": "createTime.keyword", "name": "记录写入时间", "type": "string",
             "description": "日志写入时间（与操作时间不同，前者是落库时刻）"},
        ],
        "dimensions": [
            {"name": "设备操作时间", "name_en": "es_op_time", "target_column": "operationTime.keyword",
             "category": "时间", "aliases": ["设备操作时间", "云平台操作时间"],
             "description": "设备操作的事件时间"},
            {"name": "操作类型", "name_en": "es_op_action", "target_column": "action.keyword",
             "category": "分类", "aliases": ["操作行为", "设备操作类型"],
             "description": f"设备操作行为码值：{ACTION_CODES}"},
            {"name": "操作应用", "name_en": "es_op_app", "target_column": "appCode.keyword",
             "category": "属性", "aliases": ["设备操作应用"],
             "description": f"上报操作的客户端产品码值：{OP_APP_CODES}"},
            {"name": "操作站点区域", "name_en": "es_op_region", "target_column": "region.keyword",
             "category": "地理", "aliases": ["设备操作区域"],
             "description": "站点区域码值 cn / global / eu / jp / uk（与 site 列同值，合并为一个维度）"},
            {"name": "操作客户端版本", "name_en": "es_op_cversion", "target_column": "cVersion.keyword",
             "category": "版本", "aliases": ["设备操作客户端版本"], "description": "客户端版本号"},
            {"name": "操作固件版本", "name_en": "es_op_fversion", "target_column": "fVersion.keyword",
             "category": "版本", "aliases": ["设备操作固件版本"], "description": "设备固件版本号"},
            {"name": "操作设备序列号", "name_en": "es_op_device", "target_column": "serialNumber.keyword",
             "category": "设备", "aliases": ["操作设备编码"],
             "description": "设备序列号。仅用于过滤指定设备或去重设备数，不要按它分组（高基数会超时）"},
            {"name": "操作用户账号", "name_en": "es_op_user", "target_column": "userCode.keyword",
             "category": "用户", "aliases": ["操作用户编码"],
             "description": "操作人账号码。仅用于过滤指定用户或去重用户数，不要按它分组（高基数会超时）"},
            {"name": "操作明细", "name_en": "es_op_detail", "target_column": "detail.keyword",
             "category": "属性", "aliases": ["操作内容"],
             "description": "操作明细自由文本。适合 LIKE 关键词过滤，不适合分组"},
            {"name": "操作应用名称", "name_en": "es_op_appname", "target_column": "application.keyword",
             "category": "属性", "aliases": ["设备操作应用名称"],
             "description": "应用名称字段（当前样本几乎全空，登记以备数据回填后可用）"},
        ],
        "metrics": [
            {"name": "设备操作次数", "name_en": "es_equip_operation_count", "agg_type": "COUNT",
             "formula": "COUNT(*)", "unit": "次",
             "aliases": ["设备操作次数", "云平台设备操作次数"],
             "description": "设备操作日志条数（每次操作一条）"},
            {"name": "设备操作设备数", "name_en": "es_equip_operation_devices",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "serialNumber.keyword")',
             "unit": "台", "aliases": ["操作设备数"],
             "description": "有操作记录的去重设备数（按「操作设备序列号」去重）"},
            {"name": "设备操作用户数", "name_en": "es_equip_operation_users",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "userCode.keyword")',
             "unit": "人", "aliases": ["操作用户数"],
             "description": "有操作记录的去重用户数（按「操作用户账号」去重）"},
            {"name": "扫描操作次数", "name_en": "es_equip_scan_count", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "action.keyword" = \'scan\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["设备扫描次数"],
             "description": "操作类型码值 = 'scan' 的操作次数"},
            {"name": "电量操作次数", "name_en": "es_equip_battery_count", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "action.keyword" = \'battery\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["电量读取次数"],
             "description": "操作类型码值 = 'battery' 的操作次数"},
        ],
    },
    {
        "key": OBJ_WARN,
        "display_name": "云平台设备告警日志",
        "table": TABLE_WARN,
        "aliases": ["云平台设备告警日志", "ES设备告警日志", "设备告警原始日志",
                    "设备故障原始日志", "云平台设备报警日志", OBJ_WARN],
        "description": (
            "云平台记录的**单条设备告警事件**（硬件/固件/系统上报的 warning/error/crash 等），"
            "当前约 450 万条。核心维度是「告警级别」「告警硬件模块」「告警码」。"
            "「告警码」是高基数码值（TOP 分析可用，不做全量枚举）。" + ENGINE_NOTE + SITE_NOTE + ENUM_NOTE),
        "properties": [
            {"column": "warningTime.keyword", "name": "告警时间", "type": "datetime",
             "description": "设备告警的发生时间（本地时间）"},
            {"column": "level.keyword", "name": "告警级别", "type": "string",
             "description": f"告警级别码值：{WARN_LEVEL_CODES}"},
            {"column": "hardware.keyword", "name": "告警硬件模块", "type": "string",
             "description": f"上报告警的硬件模块码值：{WARN_HARDWARE_CODES}"},
            {"column": "code.keyword", "name": "告警码", "type": "string",
             "description": "告警码（数值字符串，高基数；适合 TOP N 分布，不做全量枚举）"},
            {"column": "appCode.keyword", "name": "告警应用", "type": "string",
             "description": f"告警所属客户端产品码值：{WARN_APP_CODES}"},
            {"column": "isRelease", "name": "是否正式版", "type": "number",
             "description": "1=正式版，0=非正式版（样本内全部为 1）"},
            {"column": "region.keyword", "name": "站点区域", "type": "string",
             "description": "站点区域码值：cn / global / eu / jp / uk（与 site 列同值）"},
            {"column": "site.keyword", "name": "站点", "type": "string",
             "description": "站点码值，与 region 列取值一致"},
            {"column": "cVersion.keyword", "name": "告警客户端版本", "type": "string",
             "description": "客户端版本号"},
            {"column": "fVersion.keyword", "name": "告警固件版本", "type": "string",
             "description": "设备固件版本号"},
            {"column": "serialNumber.keyword", "name": "告警设备序列号", "type": "string",
             "description": "设备序列号（去重设备数口径；仅用于过滤/去重，不要分组）"},
            {"column": "userCode.keyword", "name": "告警用户账号", "type": "string",
             "description": "关联用户账号码（仅用于过滤/去重，不要分组）"},
            {"column": "body.keyword", "name": "告警内容", "type": "string",
             "description": "告警正文（自由文本，适合 LIKE 关键词过滤）"},
            {"column": "uid.keyword", "name": "告警记录标识", "type": "string",
             "description": "告警记录唯一标识（仅用于定位单条告警）"},
            {"column": "createTime.keyword", "name": "记录写入时间", "type": "string",
             "description": "日志写入时间"},
        ],
        "dimensions": [
            {"name": "告警时间", "name_en": "es_warn_time", "target_column": "warningTime.keyword",
             "category": "时间", "aliases": ["设备告警时间"], "description": "设备告警的发生时间"},
            {"name": "告警级别", "name_en": "es_warn_level", "target_column": "level.keyword",
             "category": "状态", "aliases": ["设备告警级别", "告警等级"],
             "description": f"告警级别码值：{WARN_LEVEL_CODES}"},
            {"name": "告警硬件模块", "name_en": "es_warn_hardware", "target_column": "hardware.keyword",
             "category": "设备", "aliases": ["告警模块", "硬件模块"],
             "description": f"上报告警的硬件模块码值：{WARN_HARDWARE_CODES}"},
            {"name": "告警码", "name_en": "es_warn_code", "target_column": "code.keyword",
             "category": "分类", "aliases": ["告警编码", "故障码"],
             "description": "告警码（数值字符串，高基数）：适合 TOP N 分布分析"},
            {"name": "告警应用", "name_en": "es_warn_app", "target_column": "appCode.keyword",
             "category": "属性", "aliases": ["设备告警应用"],
             "description": f"告警所属客户端产品码值：{WARN_APP_CODES}"},
            {"name": "告警站点区域", "name_en": "es_warn_region", "target_column": "region.keyword",
             "category": "地理", "aliases": ["设备告警区域"],
             "description": "站点区域码值 cn / global / eu / jp / uk"},
            {"name": "告警客户端版本", "name_en": "es_warn_cversion", "target_column": "cVersion.keyword",
             "category": "版本", "aliases": ["设备告警客户端版本"], "description": "客户端版本号"},
            {"name": "告警固件版本", "name_en": "es_warn_fversion", "target_column": "fVersion.keyword",
             "category": "版本", "aliases": ["设备告警固件版本"], "description": "设备固件版本号"},
            {"name": "告警设备序列号", "name_en": "es_warn_device", "target_column": "serialNumber.keyword",
             "category": "设备", "aliases": ["告警设备编码"],
             "description": "设备序列号。仅用于过滤指定设备或去重设备数，不要按它分组（高基数会超时）"},
            {"name": "告警用户账号", "name_en": "es_warn_user", "target_column": "userCode.keyword",
             "category": "用户", "aliases": ["告警用户编码"],
             "description": "关联用户账号码。仅用于过滤/去重，不要按它分组（高基数会超时）"},
            {"name": "告警内容", "name_en": "es_warn_body", "target_column": "body.keyword",
             "category": "属性", "aliases": ["告警正文", "告警描述"],
             "description": "告警正文自由文本。适合 LIKE 关键词过滤，不适合分组"},
            {"name": "是否正式版", "name_en": "es_warn_is_release", "target_column": "isRelease",
             "category": "状态", "aliases": ["正式版标记"],
             "description": "1=正式版，0=非正式版（日志码值）"},
        ],
        "metrics": [
            {"name": "设备告警次数", "name_en": "es_equip_warning_count", "agg_type": "COUNT",
             "formula": "COUNT(*)", "unit": "次", "aliases": ["告警次数", "设备告警量"],
             "description": "设备告警日志条数（每条告警一条）"},
            {"name": "告警设备数", "name_en": "es_equip_warning_devices",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "serialNumber.keyword")',
             "unit": "台", "aliases": ["告警设备台数"],
             "description": "产生过告警的去重设备数"},
            {"name": "告警用户数", "name_en": "es_equip_warning_users",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "userCode.keyword")',
             "unit": "人", "aliases": ["告警涉及用户数"],
             "description": "告警关联的去重用户数"},
            {"name": "严重告警次数", "name_en": "es_equip_warning_severe", "agg_type": "SUM",
             "formula": ('SUM(CASE WHEN "level.keyword" IN (\'error\', \'crash\', \'critical\') '
                         'THEN 1 ELSE 0 END)'),
             "unit": "次", "aliases": ["严重告警数"],
             "description": "告警级别码值属于 error / crash / critical 的告警次数"},
            {"name": "崩溃告警次数", "name_en": "es_equip_warning_crash", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "level.keyword" = \'crash\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["崩溃次数"],
             "description": "告警级别码值 = 'crash' 的告警次数"},
            {"name": "告警码种类数", "name_en": "es_equip_warning_codes",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "code.keyword")',
             "unit": "种", "aliases": ["告警码数量"],
             "description": "出现过的去重告警码个数（衡量故障种类广度）"},
        ],
    },
    {
        "key": OBJ_SW,
        "display_name": "云平台软件操作日志",
        "table": TABLE_SW,
        "aliases": ["云平台软件操作日志", "ES软件操作日志", "软件操作原始日志",
                    "软件使用原始日志", "云平台客户端软件日志",
                    "三方集成App访问日志", "第三方集成App使用日志", "App使用日志", OBJ_SW],
        "description": (
            "云平台记录的**单条软件操作事件**（打开应用/订单导出/病例导出等），当前约 1388 万条。"
            "核心维度是「软件应用」「应用分组」「业务类型」「事件来源」「应用管理器」。"
            "**问「三方集成 App / 第三方集成 App 的打开量、访问量、使用量、使用用户数」就是本对象**"
            "（17 个第三方集成应用码归为「应用分组 = 第三方集成App」，或直接用同名指标）；"
            "那不是「第三方接口调用日志」（那是回调调用次数）、也不是联耀「集成记录」（那是对接台账，"
            "记录数不是 App 使用量）。"
            "注意：本对象是**软件侧行为**，设备操作与设备告警在另外两个对象。" + ENGINE_NOTE + SITE_NOTE + ENUM_NOTE),
        "properties": [
            {"column": "evenTime.keyword", "name": "软件事件时间", "type": "datetime",
             "description": "软件操作事件时间（本地时间；列名拼写为 evenTime）"},
            {"column": "appCode.keyword", "name": "软件应用", "type": "string",
             "description": f"产生事件的软件应用码值：{SW_APP_CODES}；其中第三方集成应用码：{SW_TP_APP_CODES}"},
            {"column": "eventSource.keyword", "name": "事件来源", "type": "string",
             "description": f"事件上报来源码值：{SW_SOURCE_CODES}"},
            {"column": "businessType.keyword", "name": "业务类型", "type": "string",
             "description": f"软件业务类型码值：{SW_BUSINESS_CODES}"},
            {"column": "eventDescription.keyword", "name": "事件描述", "type": "string",
             "description": "事件描述文本（样本内多为空，少量如 OPEN ASCONNECT）"},
            {"column": "appVersion.keyword", "name": "应用版本", "type": "string",
             "description": "被操作软件的版本号（样本含空串/空值，统计版本分布时需排除）"},
            {"column": "appmanager.keyword", "name": "应用管理器", "type": "string",
             "description": f"软件归属的应用管理器/品牌码值：{SW_APPMANAGER_CODES}"},
            {"column": "region.keyword", "name": "站点区域", "type": "string",
             "description": "站点区域码值：cn / global / eu / jp / uk（与 site 列同值）"},
            {"column": "site.keyword", "name": "站点", "type": "string",
             "description": "站点码值，与 region 列取值一致"},
            {"column": "serialNumber.keyword", "name": "软件事件设备序列号", "type": "string",
             "description": "设备序列号（去重设备数口径；仅用于过滤/去重，不要分组）"},
            {"column": "userCode.keyword", "name": "软件事件用户账号", "type": "string",
             "description": "用户账号码（去重用户数口径；仅用于过滤/去重，不要分组）"},
            {"column": "cVersion.keyword", "name": "软件客户端版本", "type": "string",
             "description": "客户端版本号"},
            {"column": "sVersion.keyword", "name": "软件版本", "type": "string",
             "description": "软件版本号"},
            {"column": "fVersion.keyword", "name": "软件固件版本", "type": "string",
             "description": "设备固件版本号"},
            {"column": "companyCode.keyword", "name": "公司编码", "type": "string",
             "description": "公司/租户编码（高基数，仅用于过滤，不要分组）"},
            {"column": "orderCode.keyword", "name": "订单号", "type": "string",
             "description": "关联订单号（仅用于定位单笔订单）"},
            {"column": "caseUid.keyword", "name": "病例编号", "type": "string",
             "description": "关联病例编号（仅用于定位单个病例）"},
            {"column": "operationParam.keyword", "name": "操作参数", "type": "string",
             "description": "操作附带参数文本"},
            {"column": "extra.keyword", "name": "扩展信息", "type": "string",
             "description": "扩展字段文本"},
            {"column": "createTime.keyword", "name": "记录写入时间", "type": "string",
             "description": "日志写入时间"},
        ],
        "dimensions": [
            {"name": "软件事件时间", "name_en": "es_sw_event_time", "target_column": "evenTime.keyword",
             "category": "时间", "aliases": ["软件操作时间"], "description": "软件操作事件时间"},
            {"name": "应用分组", "name_en": "es_sw_app_group", "target_column": "appCode.keyword",
             "category": "分类", "aliases": ["软件应用分组", "应用分桶", "App使用分组"],
             "value_labels": SW_APP_GROUP_LABELS,
             "description": (
                 "软件应用按业务口径分桶：17 个第三方集成应用码合并为「第三方集成App」、"
                 "NewOrthoSim 与 OrthoSim 合并为「Ortho Simulation」、其余应用按码值原样呈现。"
                 "**本维度是分组维度**：可直接 GROUP BY，也可按业务标签过滤"
                 "（如 应用分组 = 第三方集成App，语义层会展开成 17 个码值而非只取一个）；"
                 "未登记的新应用码会原样出现（看板 ds#36/56 把它们归入「其他未归类App」，"
                 "本维度不静默归并，差异在此）"
                 f"。分桶清单与看板同源：{SW_TP_APP_CODES}")},
            {"name": "软件应用", "name_en": "es_sw_app", "target_column": "appCode.keyword",
             "category": "分类", "aliases": ["软件名称", "软件产品"],
             "description": f"软件应用码值（按量降序）：{SW_APP_CODES}"},
            {"name": "事件来源", "name_en": "es_sw_event_source", "target_column": "eventSource.keyword",
             "category": "渠道", "aliases": ["软件事件来源"],
             "description": f"事件上报来源码值：{SW_SOURCE_CODES}"},
            {"name": "业务类型", "name_en": "es_sw_business_type", "target_column": "businessType.keyword",
             "category": "分类", "aliases": ["软件业务类型"],
             "description": f"软件业务类型码值：{SW_BUSINESS_CODES}"},
            {"name": "事件描述", "name_en": "es_sw_event_desc", "target_column": "eventDescription.keyword",
             "category": "属性", "aliases": ["软件事件描述"],
             "description": "事件描述文本（样本内多为空）"},
            {"name": "软件应用版本", "name_en": "es_sw_app_version", "target_column": "appVersion.keyword",
             "category": "版本", "aliases": ["应用版本号"],
             "description": "被操作软件版本号（含空值/空串，做版本分布时需排除）"},
            {"name": "应用管理器", "name_en": "es_sw_appmanager", "target_column": "appmanager.keyword",
             "category": "组织", "aliases": ["软件品牌", "应用品牌"],
             "description": f"软件归属的应用管理器/品牌码值：{SW_APPMANAGER_CODES}"},
            {"name": "软件站点区域", "name_en": "es_sw_region", "target_column": "region.keyword",
             "category": "地理", "aliases": ["软件事件区域"],
             "description": "站点区域码值 cn / global / eu / jp / uk"},
            {"name": "软件事件设备序列号", "name_en": "es_sw_device",
             "target_column": "serialNumber.keyword", "category": "设备",
             "aliases": ["软件设备编码"],
             "description": "设备序列号。仅用于过滤指定设备或去重设备数，不要按它分组（高基数会超时）"},
            {"name": "软件事件用户账号", "name_en": "es_sw_user", "target_column": "userCode.keyword",
             "category": "用户", "aliases": ["软件用户编码"],
             "description": "用户账号码。仅用于过滤指定用户或去重用户数，不要按它分组（高基数会超时）"},
            {"name": "软件客户端版本", "name_en": "es_sw_cversion", "target_column": "cVersion.keyword",
             "category": "版本", "aliases": ["软件客户端版本名"], "description": "客户端版本号"},
            {"name": "软件版本", "name_en": "es_sw_sversion", "target_column": "sVersion.keyword",
             "category": "版本", "aliases": ["软件版本号"], "description": "软件版本号"},
            {"name": "公司编码", "name_en": "es_sw_company", "target_column": "companyCode.keyword",
             "category": "组织", "aliases": ["租户编码"],
             "description": "公司/租户编码（高基数）：仅用于过滤某个公司，不要按它分组（会超时）"},
            {"name": "订单号", "name_en": "es_sw_order", "target_column": "orderCode.keyword",
             "category": "属性", "aliases": ["软件订单号"],
             "description": "关联订单号（高基数，仅用于定位单笔订单）"},
            {"name": "病例编号", "name_en": "es_sw_case", "target_column": "caseUid.keyword",
             "category": "属性", "aliases": ["软件病例编号"],
             "description": "关联病例编号（高基数，仅用于定位单个病例）"},
        ],
        "metrics": [
            {"name": "软件操作事件数", "name_en": "es_sw_event_count", "agg_type": "COUNT",
             "formula": "COUNT(*)", "unit": "次", "aliases": ["软件事件数", "软件操作次数"],
             "description": "软件操作日志条数"},
            {"name": "软件使用设备数", "name_en": "es_sw_devices", "agg_type": "COUNT_DISTINCT",
             "formula": 'COUNT(DISTINCT "serialNumber.keyword")', "unit": "台",
             "aliases": ["软件活跃设备数"], "description": "产生软件操作事件的去重设备数"},
            {"name": "软件使用用户数", "name_en": "es_sw_users", "agg_type": "COUNT_DISTINCT",
             "formula": 'COUNT(DISTINCT "userCode.keyword")', "unit": "人",
             "aliases": ["软件活跃用户数"], "description": "产生软件操作事件的去重用户数"},
            {"name": "打开应用次数", "name_en": "es_sw_open_app", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "businessType.keyword" = \'openApp\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["应用打开次数", "应用访问量", "App访问量", "应用打开量"],
             "description": "业务类型码值 = 'openApp' 的事件数（按「软件应用」或「应用分组」过滤即该 App/该组的使用量）"},
            {"name": "三方集成App访问量", "name_en": "es_sw_tp_app_open", "agg_type": "SUM",
             "formula": ("SUM(CASE WHEN \"businessType.keyword\" = 'openApp' AND "
                         '"appCode.keyword" IN (' + _in_list(SW_TP_APP_LIST) + ') THEN 1 ELSE 0 END)'),
             "unit": "次", "aliases": ["第三方集成App访问量", "三方集成App打开次数",
                                        "第三方集成App打开次数", "集成App访问量", "三方App访问量"],
             "description": (
                 "使用/打开**第三方集成 App** 的次数：业务类型 = 'openApp' 且软件应用属于 17 个"
                 "第三方集成应用码的事件数。清单与看板 ds#36/56 同源，新增集成 App 码不会自动计入"
                 f"（需同批更新）：{SW_TP_APP_CODES}。"
                 "注意：不要与「云平台第三方接口调用日志」的接口调用次数混算（那是回调调用量，"
                 "不是 App 打开量）")},
            {"name": "三方集成App使用用户数", "name_en": "es_sw_tp_app_users",
             "agg_type": "COUNT_DISTINCT",
             "formula": ("COUNT(DISTINCT CASE WHEN \"businessType.keyword\" = 'openApp' AND "
                         '"appCode.keyword" IN (' + _in_list(SW_TP_APP_LIST) + ') THEN "userCode.keyword" '
                         "ELSE NULL END)"),
             "unit": "人", "aliases": ["第三方集成App使用用户数", "集成App用户数", "三方App用户数"],
             "description": "打开过 17 个第三方集成应用之一的去重用户数（ES cardinality 近似值，误差约 ±0.5%~2%）"},
            {"name": "订单导出次数", "name_en": "es_sw_order_export", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "businessType.keyword" = \'orderExport\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["订单导出量"],
             "description": "业务类型码值 = 'orderExport' 的事件数"},
            {"name": "病例导出次数", "name_en": "es_sw_case_export", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "businessType.keyword" = \'caseExport\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["病例导出量"],
             "description": "业务类型码值 = 'caseExport' 的事件数"},
            {"name": "使用应用种类数", "name_en": "es_sw_app_kinds", "agg_type": "COUNT_DISTINCT",
             "formula": 'COUNT(DISTINCT "appCode.keyword")', "unit": "个",
             "aliases": ["软件种类数"], "description": "出现过的去重软件应用个数"},
        ],
    },
    {
        "key": OBJ_TP,
        "display_name": "云平台第三方接口调用日志",
        "table": TABLE_TP,
        "aliases": ["云平台第三方接口调用日志", "ES接口调用日志", "三方接口原始日志",
                    "云平台回调日志", "接口调用原始记录", OBJ_TP],
        "description": (
            "云平台与第三方系统之间的**单次接口调用记录**（订单/病例回调推送等），当前约 263 万条。"
            "核心维度是「业务模型」「响应状态」「请求地址」「调用方应用标识」。"
            "失败率口径：按「响应状态」码值 FAIL 计数除以总调用次数（可分组到业务模型/接口地址）。"
            "含请求/响应正文的列属系统敏感治理基线范围，本对象未建模。" + ENGINE_NOTE + SITE_NOTE + ENUM_NOTE),
        "properties": [
            {"column": "requestTime.keyword", "name": "接口请求时间", "type": "datetime",
             "description": "接口请求时间（本地时间；源内约 9% 记录该列为空，不计入时间趋势）"},
            {"column": "businessModel.keyword", "name": "业务模型", "type": "string",
             "description": f"调用对应的业务模型码值：{TP_BUSINESS_MODEL_CODES}"},
            {"column": "responseStatus.keyword", "name": "响应状态", "type": "string",
             "description": f"调用结果码值：{TP_STATUS_CODES}（FAIL 占多数）"},
            {"column": "requestUrl.keyword", "name": "请求地址", "type": "string",
             "description": "被调用的第三方接口地址（含主机与路径，适合 TOP N 分布）"},
            {"column": "appid.keyword", "name": "调用方应用标识", "type": "string",
             "description": "调用方系统的应用标识（第三方分配的凭据标识，雪花 id 形态；仅用于过滤/TOP）"},
            {"column": "businessCode.keyword", "name": "业务编码", "type": "string",
             "description": "业务编码（高基数：适合 TOP N，不适合全量枚举）"},
            {"column": "otherBusinessCode.keyword", "name": "其他业务编码", "type": "string",
             "description": "备用业务编码（样本内全空）"},
            {"column": "businessType", "name": "接口类型码", "type": "number",
             "description": "接口类型数值码（样本内恒为 1）"},
            {"column": "region.keyword", "name": "站点区域", "type": "string",
             "description": "站点区域码值：cn / global / eu / uk（与 site 列同值）"},
            {"column": "site.keyword", "name": "站点", "type": "string",
             "description": "站点码值，与 region 列取值一致"},
            {"column": "responseTime.keyword", "name": "响应时间", "type": "string",
             "description": "响应时间字段（样本内与请求时间同值，不能用于计算耗时，故不建耗时指标）"},
            {"column": "createTime.keyword", "name": "记录写入时间", "type": "string",
             "description": "日志写入时间"},
        ],
        "dimensions": [
            {"name": "接口请求时间", "name_en": "es_tp_request_time", "target_column": "requestTime.keyword",
             "category": "时间", "aliases": ["第三方接口请求时间"], "description": "接口请求时间"},
            {"name": "业务模型", "name_en": "es_tp_business_model", "target_column": "businessModel.keyword",
             "category": "分类", "aliases": ["三方业务模型"],
             "description": f"调用对应的业务模型码值：{TP_BUSINESS_MODEL_CODES}"},
            {"name": "响应状态", "name_en": "es_tp_response_status",
             "target_column": "responseStatus.keyword", "category": "状态",
             "aliases": ["接口响应状态", "调用结果"],
             "description": f"调用结果码值：{TP_STATUS_CODES}（FAIL 占多数）"},
            {"name": "请求地址", "name_en": "es_tp_request_url", "target_column": "requestUrl.keyword",
             "category": "属性", "aliases": ["接口地址", "三方接口地址"],
             "description": "被调用的第三方接口地址（含主机与路径，适合 TOP N 分布）"},
            {"name": "调用方应用标识", "name_en": "es_tp_appid", "target_column": "appid.keyword",
             "category": "属性", "aliases": ["调用方标识", "三方应用标识"],
             "description": "调用方系统的应用标识（第三方分配的凭据标识，非本平台资源 id；仅用于过滤/TOP）"},
            {"name": "业务编码", "name_en": "es_tp_business_code", "target_column": "businessCode.keyword",
             "category": "分类", "aliases": ["三方业务编码"],
             "description": "业务编码（高基数）：适合 TOP N 分布，不适合全量枚举"},
            {"name": "接口站点区域", "name_en": "es_tp_region", "target_column": "region.keyword",
             "category": "地理", "aliases": ["三方接口区域"],
             "description": "站点区域码值 cn / global / eu / uk"},
            {"name": "其他业务编码", "name_en": "es_tp_other_business_code",
             "target_column": "otherBusinessCode.keyword", "category": "属性",
             "aliases": ["备用业务编码"], "description": "备用业务编码（样本内全空）"},
            {"name": "接口类型码", "name_en": "es_tp_business_type", "target_column": "businessType",
             "category": "属性", "aliases": ["接口类型"],
             "description": "接口类型数值码（样本内恒为 1，当前无区分度，登记以备后续区分）"},
        ],
        "metrics": [
            {"name": "接口调用次数", "name_en": "es_tp_request_count", "agg_type": "COUNT",
             "formula": "COUNT(*)", "unit": "次", "aliases": ["三方调用次数", "接口调用量"],
             "description": "第三方接口调用记录条数"},
            {"name": "接口调用失败次数", "name_en": "es_tp_fail_count", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "responseStatus.keyword" = \'FAIL\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["接口失败次数", "调用失败次数"],
             "description": "响应状态码值 = 'FAIL' 的调用次数（失败率 = 本指标 / 接口调用次数）"},
            {"name": "接口调用成功次数", "name_en": "es_tp_success_count", "agg_type": "SUM",
             "formula": 'SUM(CASE WHEN "responseStatus.keyword" = \'SUCCESS\' THEN 1 ELSE 0 END)',
             "unit": "次", "aliases": ["接口成功次数", "调用成功次数"],
             "description": "响应状态码值 = 'SUCCESS' 的调用次数"},
            {"name": "接口调用方数", "name_en": "es_tp_caller_count", "agg_type": "COUNT_DISTINCT",
             "formula": 'COUNT(DISTINCT "appid.keyword")', "unit": "个",
             "aliases": ["调用方数量", "三方调用方数"],
             "description": "去重调用方应用标识个数"},
            {"name": "接口地址数", "name_en": "es_tp_url_count", "agg_type": "COUNT_DISTINCT",
             "formula": 'COUNT(DISTINCT "requestUrl.keyword")', "unit": "个",
             "aliases": ["接口数量", "三方接口数"],
             "description": "去重接口地址个数"},
            {"name": "业务模型数", "name_en": "es_tp_business_model_count",
             "agg_type": "COUNT_DISTINCT", "formula": 'COUNT(DISTINCT "businessModel.keyword")',
             "unit": "个", "aliases": ["三方业务模型数"],
             "description": "去重业务模型个数"},
        ],
    },
]

# 数据产品认领与绑定用的执行形状（ES 单索引直查：raw_source → 强制 LIMIT）
QUERY_MODE = "raw_source"


def _log(msg: str) -> None:
    print(msg, flush=True)


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


def build_doc(ds_id: int) -> dict:
    """canonical doc（唯一可编辑事实源）。

    每个对象带 `primary_table`：业务本体的来源表是激活门禁（find_unregistered_tables）与
    字典 target_table 派生的依据；物理列绑定走部署绑定层（properties[].column 供 column_map
    与列元数据实存裁决使用）。
    """
    objects = []
    for spec in OBJECTS:
        objects.append({
            "key": spec["key"],
            "display_name": spec["display_name"],
            "aliases": list(spec["aliases"]),
            "description": spec["description"],
            "primary_table": spec["table"],
            "properties": [dict(p) for p in spec["properties"]],
            "metrics": [m["name"] for m in spec["metrics"]],
        })
    return {
        "datasource_name": DS_NAME,
        "domain": DOMAIN,
        "description": (
            f"云平台 Elasticsearch 侧运行日志业务本体（主源 {DS_NAME}）：以「云平台设备操作日志」"
            f"「云平台设备告警日志」「云平台软件操作日志」「云平台第三方接口调用日志」四个事实对象，"
            f"覆盖设备使用强度、设备故障、软件功能使用（含三方集成 App 使用量）、三方接口调用成功率四类分析。"
            + DW_EDGE + DATA_QUALITY_NOTE + DOMAIN_EDGE + SITE_NOTE),
        "kind": "business",
        "objects": objects,
        "sources": [],
    }


def preflight(ds_ids: dict[str, int]) -> list[str]:
    """写入前的可诊断检查：数据源/站点/名字冲突/对象 key 冲突，任一不过即拒绝落库。"""
    problems: list[str] = []
    if DS_NAME not in ds_ids:
        problems.append(f"数据源未注册: {DS_NAME}")
    row = execute_query("SELECT name, db_type, site_code FROM adh_datasources WHERE name = %s",
                        (DS_NAME,), fetchone=True)
    if row and str(row.get("db_type") or "").lower() not in ("es", "elasticsearch"):
        problems.append(f"数据源 {DS_NAME} 的 db_type={row.get('db_type')!r}，不是 ES")

    # 列元数据必须已同步（否则字典写入会按 unverified 放行错列，取数时才暴露）
    for spec in OBJECTS:
        got = execute_query(
            "SELECT COUNT(*) AS n FROM adh_column_metadata "
            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
            (ds_ids.get(DS_NAME, 0), spec["table"]), fetchone=True)
        if not got or not int(got["n"] or 0):
            problems.append(f"列元数据缺失: {spec['table']}（先同步该数据源元数据）")
            continue
        cols = {str(r["column_name"]) for r in execute_query(
            "SELECT column_name FROM adh_column_metadata "
            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
            (ds_ids.get(DS_NAME, 0), spec["table"])) or []}
        for p in spec["properties"]:
            if p["column"] not in cols:
                problems.append(f"{spec['table']} 属性列不存在: {p['column']}（{p['name']}）")
        for d in spec["dimensions"]:
            if d["target_column"] not in cols:
                problems.append(f"{spec['table']} 维度列不存在: {d['target_column']}（{d['name']}）")

    # 名字/别名冲突（字典全局唯一口径：解析按 name/name_en/aliases 命中，重名即串味）。
    # 本域已登记的行不算冲突（重跑时它们正是本脚本要收敛的产物）。
    mine_keys = {s["key"] for s in OBJECTS}

    def _occupied(table: str) -> tuple[set, dict]:
        names, alias_owner = set(), {}
        rows = execute_query(
            f"SELECT name, name_en, aliases, COALESCE(bound_object_key,'') AS bok "
            f"FROM {table}") or []
        for r in rows:
            if str(r.get("bok") or "") in mine_keys:
                continue
            names.add(str(r["name"]).strip())
            if r.get("name_en"):
                names.add(str(r["name_en"]).strip())
            raw = r["aliases"]
            aliases = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else (raw or [])
            for a in aliases if isinstance(aliases, list) else []:
                alias_owner.setdefault(str(a).strip(), r["name"])
        return names, alias_owner

    for spec in OBJECTS:
        for table, key, kind in (("adh_metrics", "metrics", "指标"),
                                 ("adh_dimensions", "dimensions", "维度")):
            names, alias_owner = _occupied(table)
            for s in spec[key]:
                if s["name"] in names:
                    problems.append(f"{kind}名已存在: {s['name']}")
                if s.get("name_en") and s["name_en"] in names:
                    problems.append(f"{kind}英文句柄已被占用: {s['name_en']}（{s['name']}）")
                for a in (s.get("aliases") or []):
                    if a in names:
                        problems.append(f"{kind}别名与既有名冲突: {a}（{s['name']}）")
                    elif a in alias_owner:
                        problems.append(f"{kind}别名已被「{alias_owner[a]}」占用: {a}（{s['name']}）")

    # 对象 key 撞车（identity 判定与激活同口径：撞了会让检索歧义、激活硬阻断）
    from backend.modules.catalog.services.ontology_service import object_identity_key
    mine_ids = {object_identity_key(k) for k in mine_keys}
    for r in execute_query("SELECT DISTINCT object_key FROM adh_ontology_objects WHERE is_active = 1") or []:
        if object_identity_key(str(r["object_key"])) in mine_ids and str(r["object_key"]) not in mine_keys:
            problems.append(f"对象 key 身份与既有对象冲突: {r['object_key']}")
    return problems


def ensure_model(doc: dict, ds_id: int, apply: bool) -> int:
    """草案落库（save_generated_draft 建新 / save_draft 就地改）。"""
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    row = _find_model(MODEL_NAME, ds_id)
    if not row:
        if not apply:
            _log(f"[model] dry-run：将新建业务本体草案「{MODEL_NAME}」（主源 {DS_NAME}，"
                 f"{len(doc['objects'])} 个对象）")
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
    # 单源单站：ES 源 site_code 为空 → 不声明 deploy_sites（保持"未配置"口径，
    # 取数不经过站点路由，行为零变化；find_deploy_site_issues 对空声明返回空）
    ds_row = execute_query("SELECT site_code FROM adh_datasources WHERE id = %s",
                           (ds_id,), fetchone=True) or {}
    site = str(ds_row.get("site_code") or "")
    if site:
        site_service.set_model_deploy_sites(model_id, [{"site_code": site, "datasource": DS_NAME}])
        _log(f"[model] deploy_sites 已声明 {site}→{DS_NAME}")
    else:
        _log("[model] 数据源未归属站点（site_code 为空）：不声明 deploy_sites，取数按绑定行直连")
    _log(f"[model] doc 已保存（{len(doc['objects'])} 对象 / "
         f"{sum(len(o['properties']) for o in doc['objects'])} 属性）")
    return model_id


def claim_products(model_id: int, apply: bool) -> None:
    """激活门禁 P2.3 的前置：把 4 个对象的来源表认领为数据产品（幂等）。"""
    from backend.scripts import claim_ontology_tables_as_products as claim

    if not model_id:
        _log("[product] dry-run：草案尚未建模型，激活前需认领 "
             + "、".join(f"{DS_NAME}.{s['table']}" for s in OBJECTS))
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
        _log("[binding] dry-run：将为 4 个对象各写一条 primary 契约行 "
             f"（{DS_NAME}.<各索引>，query_mode={QUERY_MODE}）")
        return
    bdoc = dict(doc)
    bdoc["datasource_id"] = ds_id
    objs = []
    for spec in OBJECTS:
        obj = dict(next(o for o in doc["objects"] if o["key"] == spec["key"]))
        obj["execution_binding"] = {
            "bind_kind": "primary",
            "physical_table": spec["table"],
            "datasource_name": DS_NAME,
            "query_mode": QUERY_MODE,
            "allow_full_scan": 1,
            "sync_state": "bound",
        }
        objs.append(obj)
    bdoc["objects"] = objs
    stats = ontology_yaml_import._persist_bindings(bdoc, _model_id(ds_id))
    _log(f"[binding] 契约行写入: written={stats.get('written')} drifted={stats.get('drifted')} "
         f"unbound={stats.get('unbound')}")


def _model_id(ds_id: int) -> int:
    row = _find_model(MODEL_NAME, ds_id)
    if not row:
        raise RuntimeError("模型不存在（前序步骤未落库）")
    return int(row["id"])


def _existing_owned_row(table: str, name: str, object_key: str) -> int:
    """同名行必须属于本域对象才允许 update，否则显式报错（防跨域误覆盖）。

    历史隐患：字典按 name 全局唯一，但表里可能有**停用**的同名行（preflight 已把它当占用）；
    若只按 name 命中就 update，会把别的模型的口径行原地改写成本域的表/列，
    对方取数静默错乱（比建档失败危害大得多，属静默降级）。
    """
    row = execute_query(
        f"SELECT id, COALESCE(bound_object_key,'') AS bok FROM {table} WHERE name = %s",
        (name,), fetchone=True)
    if not row:
        return 0
    if str(row.get("bok") or "") != object_key:
        raise RuntimeError(
            f"{table} 已有同名行「{name}」归属对象 {row.get('bok')!r}（非本域 {object_key}），"
            "拒绝覆盖：请先人工确认口径归属或改名，不得把别的口径行改写成本域映射")
    return int(row["id"])


def ensure_dicts(apply: bool) -> None:
    """指标/维度字典（幂等**收敛**：已存在则对齐到脚本定义，不跳过）。

    存在即跳过会把上一轮的错映射留在库里并报"已存在"；列名实存裁决一律交 metrics_service。
    `certified` 一律 0：脚本拿不出"谁对口径负责"，不自封认证。
    """
    created, updated = [], []
    for spec in OBJECTS:
        for m in spec["metrics"]:
            # formula 里的带点列写成已加双引号形态：`_fix_formula` 幂等保留，
            # 而 `_require_formula_matches_object` 的列名抽取会跳过引号片段
            # （裸写 serialNumber.keyword 会被它按「表.列」拆成 serialNumber 而误判为跨表）
            payload = {"display_name": m["name"], "name": m["name_en"],
                       "calculation_type": m["agg_type"], "expression": m["formula"],
                       "unit": m["unit"], "description": m["description"],
                       "aliases": m["aliases"], "category": "云平台运行日志",
                       "bound_object_key": spec["key"], "target_table": spec["table"]}
            row_id = _existing_owned_row("adh_metrics", m["name"], spec["key"])
            if not apply:
                (updated if row_id else created).append(f"metric:{m['name']}")
                continue
            if row_id:
                metrics_service.update_metric(row_id, payload)
                updated.append(f"metric:{m['name']}")
            else:
                res = metrics_service.create_metric(payload)
                created.append(f"metric:{m['name']}(id={res['id']})")
        for d in spec["dimensions"]:
            payload = {"display_name": d["name"], "name_en": d["name_en"],
                       "target_table": spec["table"], "target_column": d["target_column"],
                       "category": d["category"], "description": d["description"],
                       "bound_object_key": spec["key"], "aliases": d["aliases"],
                       # 例外：分桶维度（应用分组）必须带 value_labels，其余维度为 None
                       "value_labels": d.get("value_labels"), "certified": 0}
            row_id = _existing_owned_row("adh_dimensions", d["name"], spec["key"])
            if not apply:
                (updated if row_id else created).append(f"dimension:{d['name']}")
                continue
            if row_id:
                metrics_service.update_dimension(row_id, payload)
                updated.append(f"dimension:{d['name']}")
            else:
                res = metrics_service.create_dimension(payload)
                created.append(f"dimension:{d['name']}(id={res['id']})")
    _log(f"[dict] {'dry-run 待写' if not apply else '已建'}: {created or '（无新建）'}")
    if updated:
        _log(f"[dict] 已对齐现有行: {updated}")


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
        _log(f"[kb] 上云文档已同步 synced={res.get('synced')} targets={res.get('targets')}")
    else:
        _log(f"[kb][warn] 未同步（原因 {res.get('skipped')}）：可在本体页手动重试，不得当成已发布")


def ensure_datasource_access(ds_id: int, apply: bool) -> None:
    """角色数据源授权（可查性唯一裁决点）：缺授权时新域会 fail-closed 拒查。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_roles WHERE is_active = 1 ORDER BY id")
            roles = cur.fetchall() or []
            role_id = int(roles[0]["id"]) if roles else 0
            if not role_id:
                _log("[access][warn] 未找到可用角色，跳过授权检查")
                return
            cur.execute("SELECT 1 FROM adh_role_datasource_access WHERE role_id = %s AND datasource_id = %s",
                        (role_id, ds_id))
            if cur.fetchone():
                _log(f"[access] 角色 {role_id} 已授权 {DS_NAME}")
                return
            _log(f"[access] {'待补授权' if not apply else '补授权'}: {DS_NAME} → role {role_id}")
            if apply:
                cur.execute(
                    "INSERT IGNORE INTO adh_role_datasource_access (role_id, datasource_id) VALUES (%s, %s)",
                    (role_id, ds_id))
                conn.commit()


def verify(ds_id: int) -> int:
    """发布后核验：门禁全空 + 对象/绑定/字典齐备，任何缺项都显式报出。"""
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

    for spec in OBJECTS:
        obj_rows = execute_query(
            "SELECT id FROM adh_ontology_objects WHERE model_id = %s AND object_key = %s AND is_active = 1",
            (int(model["id"]), spec["key"])) or []
        if len(obj_rows) != 1:
            problems.append(f"对象展开行异常: {spec['key']} → {len(obj_rows)} 行")
        binds = execute_query(
            "SELECT physical_table, status FROM adh_ontology_bindings "
            "WHERE model_id = %s AND object_key = %s AND status = 'active'",
            (int(model["id"]), spec["key"])) or []
        if not binds:
            problems.append(f"部署绑定层无生效行: {spec['key']}（planner 拿不到物理表，取数必挂）")
        elif str(binds[0]["physical_table"]) != spec["table"]:
            problems.append(f"绑定物理表不一致: {spec['key']} → {binds[0]['physical_table']}")
        for m in spec["metrics"]:
            row = execute_query("SELECT target_table, bound_object_key FROM adh_metrics WHERE name = %s",
                                (m["name"],), fetchone=True)
            if not row:
                problems.append(f"指标缺失: {m['name']}")
            elif str(row["target_table"] or "") != spec["table"]:
                problems.append(f"指标 {m['name']} target_table={row['target_table']!r} ≠ {spec['table']}")
            elif str(row["bound_object_key"] or "") != spec["key"]:
                problems.append(f"指标 {m['name']} bound_object_key={row['bound_object_key']!r} ≠ {spec['key']}")
        for d in spec["dimensions"]:
            row = execute_query("SELECT target_table, target_column FROM adh_dimensions WHERE name = %s",
                                (d["name"],), fetchone=True)
            if not row:
                problems.append(f"维度缺失: {d['name']}")
            elif str(row["target_table"] or "") != spec["table"]:
                problems.append(f"维度 {d['name']} target_table={row['target_table']!r} ≠ {spec['table']}")
            elif str(row["target_column"] or "") != d["target_column"]:
                problems.append(f"维度 {d['name']} target_column={row['target_column']!r} "
                                f"≠ {d['target_column']}")
        # 时间维度唯一性：多行 category='时间' 时 planner 只取第一行，会挑错时间列
        times = execute_query(
            "SELECT name FROM adh_dimensions WHERE target_table = %s AND is_active = 1 AND category = '时间'",
            (spec["table"],)) or []
        if len(times) != 1:
            problems.append(f"{spec['table']} 时间维度行数 {len(times)}（必须恰好 1 个，否则时间谓词挑错列）")

    for p in problems:
        _log(f"[verify][FAIL] {p}")
    if not problems:
        _log("[verify] 建模齐备：模型 active / 门禁空 / 4 对象已展开 / 绑定生效 / "
             f"字典 {sum(len(s['metrics']) for s in OBJECTS)} 指标 + "
             f"{sum(len(s['dimensions']) for s in OBJECTS)} 维度均落各自物理表 / 时间维度唯一")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建「云平台运行日志」独立业务本体（ES 源）")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    args = ap.parse_args()
    apply = bool(args.apply)
    _log(f"== {'执行' if apply else 'dry-run（只报计划，不写库）'} ==")

    ds_ids = _ids_by_name([DS_NAME])
    if DS_NAME not in ds_ids:
        _log(f"[FATAL] 数据源未注册: {DS_NAME}")
        return 2
    ds_id = ds_ids[DS_NAME]

    problems = preflight(ds_ids)
    for p in problems:
        _log(f"[preflight][FAIL] {p}")
    if problems:
        _log("[FATAL] 前置检查未过，未写入任何内容")
        return 2

    doc = build_doc(ds_id)
    _log(f"[doc] {len(doc['objects'])} 对象 / "
         f"{sum(len(o['properties']) for o in doc['objects'])} 属性 / "
         f"{sum(len(s['metrics']) for s in OBJECTS)} 指标 / "
         f"{sum(len(s['dimensions']) for s in OBJECTS)} 维度")
    ensure_datasource_access(ds_id, apply)
    model_id = ensure_model(doc, ds_id, apply)
    claim_products(model_id, apply)
    if not apply:
        _log("== 后续步骤（激活/绑定/字典/图谱/知识库）需 --apply 才执行 ==")
        ensure_dicts(apply)
        return 0
    activate_model(model_id, apply)
    ensure_binding(doc, ds_id, apply)
    ensure_dicts(apply)
    rebuild_graph_and_kb(apply)
    return verify(ds_id)


if __name__ == "__main__":
    raise SystemExit(main())
