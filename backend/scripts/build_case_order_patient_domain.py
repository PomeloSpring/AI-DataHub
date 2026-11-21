"""构建「案例·订单·患者」业务域：联耀本体三对象就地更新 + 字典收敛 + 数据集 + 看板。

口径来源：已审阅设计稿《本体指标维度设计-案例订单患者域.xlsx》（Sheet① 对象总览 /
Sheet② 指标 73 行 / Sheet③ 维度 79 行 / Sheet④ 打回修正 / Sheet⑤ 说明与回写指引）。
设计数据全部内嵌为本文件常量（逐行转录自该稿），不依赖 xlsx 文件。

范围（对象总览拍板）：
- case(t_case_records) / order(t_order_records) / patient(t_user_hospital_patient) 三对象；
- CaseFile(16 分表)/CaseTreatment/CaseRecycleBin、订单金额族（真实源 t_order_bill）、
  product_codes 多值列、PII 列全部不建模不登记；
- 不新建本体模型：就地更新「联耀业务本体」(active, 18 对象, deploy_sites=5 站) 的
  case/order/patient 三对象 doc → save_draft → activate（唯一合规路径，级联展开）。

写入口径（宁拒勿错）：
- 指标/维度字典按 name 命中即 update 对齐（幂等收敛，不存在则 create）；
- 语义错位/被替代的现有行显式停用（is_active=0 + description 追加替代说明）；
- CUSTOM 巡检指标（跨院重复档案/档案案例数不一致）不参与语义层编译，取数走专用数据集；
- 停用行的 name/name_en 占位不释放（create_metric/create_dimension 查重含 inactive 行），
  新行命名已避开全表占用（2026 实测：adh_metrics 243 行 / adh_dimensions 288 行逐一核对；
  其中指标 id≥194、维度 id≥238 为本脚本一次未留日志的部分落库产物，幂等重跑按 name 收敛）；
- 维度别名与既有 name/name_en/别名冲突的词不登记（如「渠道」被 rum 域占、「客户端版本」
  被设备操作日志占），Excel 别名纪律：撞名不登记，靠 name/name_en 检索。

用法（repo 根目录）：
    venv\\Scripts\\python.exe -m backend.scripts.build_case_order_patient_domain           # dry-run
    venv\\Scripts\\python.exe -m backend.scripts.build_case_order_patient_domain --apply   # 落库

幂等：可重复执行。doc 就地修（属性增删/改名/描述重写后 save_draft+activate）、
字典按 name upsert、数据集按 name update、看板与图表按 name 命中即跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.common.db import execute_query  # noqa: E402
from backend.common.db.metadata_db import get_metadata_conn  # noqa: E402
from backend.modules.catalog.services import metrics_service, ontology_service  # noqa: E402
from backend.modules.viz.services import dataset_service  # noqa: E402
from backend.modules.viz.services.dashboard_service import ChartService, DashboardService  # noqa: E402

CREATED_BY = "script:build_case_order_patient_domain"

MODEL_NAME = "联耀业务本体"
SITE_DS = [("cn", "prd-cn"), ("int", "prd-en"), ("jp", "prd-jp"),
           ("uk", "prd-uk"), ("eu", "prd-eu")]          # 联耀本体 deploy_sites 现状（5 站）
AVAILABLE_DS = [d for _c, d in SITE_DS]                 # 数据集可用范围（看板可切站点）

TABLES = {"case": "t_case_records", "order": "t_order_records", "patient": "t_user_hospital_patient"}

# 每对象口径（Excel Sheet①/⑤：首选业务时间轴 / 记录时间轴 / 有效口径 / 排除项），写进对象描述。
OBJECT_NOTES = {
    "case": (
        "首选业务时间轴=扫描时间(scan_time)，记录时间轴=创建时间(create_time)；"
        "有效口径 del_flag=0（回收站 del_flag=1，无值 2）；"
        "排除项：CaseFile(16 分表)/CaseTreatment/CaseRecycleBin 本期不建模、案例步骤(非物理列)、"
        "精确完成时长(update_time 会被文件清理刷新，仅近似周期)。"
        "PII 列(patient_phone/patient_user_name)不建模，仅允许缺失类聚合。"
    ),
    "order": (
        "首选业务时间轴=下单时间(order_time)，记录时间轴=创建时间(create_time)；"
        "有效口径双角色侧：诊所侧 clinic_del_flag=0、技工所侧 lab_del_flag=0(实体 OrderRecords:301/307)；"
        "排除项：全部金额指标(真实源 t_order_bill/_detail，本期不做)、product_codes 多值列、"
        "接单时长(无 accept_time 列)。"
    ),
    "patient": (
        "首选业务时间轴=登记日期(registration_date)，记录时间轴=创建时间(create_time)；"
        "本表无删除标记列，不设有效口径过滤；"
        "排除项：逐次就诊明细(本表仅有最近就诊日期 recent_visit_date)。"
        "PII 列(real_name/id_no/phone/email/address_detail/avatar/username/nick_name/"
        "longitude/latitude)不建模不输出，仅允许非空率/缺失数类聚合。"
    ),
}

# ── 指标字典（Sheet② 逐行转录）────────────────────────────────────────────
# spec: name=业务权威名, en=英文句柄(create 新行留空避开停用行占位), agg=聚合, formula=口径,
#       unit, aliases, cat=类别, bok=归属对象, col=时间敏感列, desc=描述/口径
METRIC_UPDATES: list[dict] = [
    {"name": "案例数量", "en": "case_count", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["case数", "total_cases", "口扫案例数", "病例数", "病例总量", "检查数", "检查次数"],
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN id ELSE NULL END)",
     "desc": "有效案例(病例)记录数，排除已删除(del_flag=0)"},
    {"name": "已完成案例数", "en": "completed_cases_wiki", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["completed_cases", "已结案数", "结案数"],
     "formula": "COUNT(CASE WHEN case_status = 5 AND del_flag = 0 THEN 1 END)",
     "desc": "状态为已完成(5)且未删除的案例数；英文句柄 completed_cases 降级为别名"},
    {"name": "已上传案例数", "en": "uploaded_cases", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["云端案例数"],
     "formula": "COUNT(CASE WHEN case_status = 3 AND del_flag = 0 THEN 1 END)",
     "desc": "已上传到云端(3)的有效案例数"},
    {"name": "已下单案例数", "en": "ordered_cases", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["转订单案例数", "已发起订单案例数"],
     "formula": "COUNT(CASE WHEN has_order = 1 AND del_flag = 0 THEN 1 END)",
     "desc": "has_order=1 的有效案例数；下单标记由订单创建回写（原口径 case_status=4 有误）"},
    {"name": "上锁案例数", "en": "locked_cases", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["锁定案例数", "锁定的病例数"],
     "formula": "COUNT(CASE WHEN locked = 1 AND del_flag = 0 THEN 1 END)",
     "desc": "处于锁定状态的有效案例数"},
    {"name": "种植修复案例数", "en": "implant_cases", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["种植案例", "种植体病例数"],
     "formula": "COUNT(CASE WHEN is_plant = 1 AND del_flag = 0 THEN 1 END)",
     "desc": "种植修复案例数。权威口径是 is_plant=1；不可用 case_type=101（永不落库，"
             "CaseServiceImpl:3389-3400 中 type=1 时置 is_plant=1 但 case_type 写成 REPAIR(100)）"},
    {"name": "订单数", "en": "order_count", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["order_count", "下单量", "全部订单数", "订单量"],
     "formula": "COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END)",
     "desc": "诊所侧有效订单数。本表没有单一 del_flag：诊所侧看 clinic_del_flag、技工所侧看 "
             "lab_del_flag(实体 OrderRecords:301/307，查询实现 OrderRecordsServiceImpl:107/115/116)"},
    {"name": "已取消订单数", "en": "cancelled_orders", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["取消订单", "cancel订单数"],
     "formula": "COUNT(CASE WHEN order_status = 7 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已取消(7=取消)的订单数。原稿写 status=9 口径错：9 是处理中(READY)，取消是 7"
             "(OrderStatusEnum:CANCEL(7)/READY(9))"},
    {"name": "患者档案数", "en": "patient_count", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["total_patients", "病人档案数", "建档数"],
     "formula": "COUNT(*)",
     "desc": "患者档案记录条数（同一人在 N 家医院有 N 条档案）；自然人数按 file_code 去重，"
             "见「患者人数」"},
    {"name": "患者平均年龄", "en": "avg_patient_age", "agg": "AVG", "unit": "岁", "cat": "客户", "bok": "patient",
     "col": "", "aliases": ["平均年龄"],
     "formula": "AVG(TIMESTAMPDIFF(YEAR, birthday, CURDATE()))",
     "desc": "以出生日现算的平均年龄。原稿 AVG(age)：age 是建档时写入的快照列，不随时间刷新，"
             "跨年统计会系统性偏小；已改为由 birthday 现算"},
]

# 语义错位/被替代的现有行 → 显式停用（is_active=0，description 追加替代说明）。
# total_cases(bok=company 却公式引用 Case.id)、total_orders/completed_orders 等英文行按
# 打回说明「业务权威名改中文，英文标识降级为别名」处置。
METRIC_RETIRES: list[dict] = [
    {"name": "订单量", "alt": "订单数", "reason": "与「订单数」同口径两个名字，收敛到业务权威名"},
    {"name": "total_cases", "alt": "案例数量", "reason": "bok=company 却公式引用 Case.id，伪指标"},
    {"name": "completed_cases", "alt": "已完成案例数", "reason": "英文行降级为别名（业务权威名改中文）"},
    {"name": "pending_cases", "alt": "待处理案例数", "reason": "英文行降级为别名；0/1/2 混装掩盖卡点，另拆未扫描/扫描未优化"},
    {"name": "total_case_storage_gb", "alt": "案例总存储GB", "reason": "公式未排除已删除案例，会把待清理数据算进成本"},
    {"name": "avg_case_cycle_hours", "alt": "平均案例处理时长小时", "reason": "英文行降级为别名，公式补 del_flag 过滤"},
    {"name": "cases_with_orders_ratio", "alt": "有订单案例占比", "reason": "公式未排除已删除案例且分母未防零除"},
    {"name": "is_frequent_visitor", "alt": "常访患者数", "reason": "布尔派生规则不是度量，转为计数指标"},
    {"name": "total_patients", "alt": "患者档案数/患者人数", "reason": "档案条数与去重人数混用，拆为两指标"},
    {"name": "is_pending", "alt": "未发送订单数/处理中订单数", "reason": "status IN (1,9) 合成「待处理」无业务意义，两端状态已拆开"},
    {"name": "total_orders", "alt": "订单数", "reason": "英文行且无删除过滤"},
    {"name": "completed_orders", "alt": "已完成订单数", "reason": "英文行且无 clinic_del_flag 过滤"},
    {"name": "拆牙请求案例数", "alt": "排牙请求案例数", "reason": "公式只含状态 1 缺 2，且措辞以 SplitTeethStatusEnum「排牙」为权威"},
    {"name": "待确认订单数", "alt": "已发送待接单订单数", "reason": "订单表无 del_flag 列（公式引用错列），且枚举标签来自通用电商模板"},
    {"name": "已收货订单数", "alt": "已完成订单数", "reason": "OrderStatusEnum 无「已收货」状态（5=已发货）"},
]

METRIC_CREATES: list[dict] = [
    # ── case（Sheet② case 域新增/重建行）──
    {"name": "待处理案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["pending_cases", "未结案案例数"],
     "formula": "COUNT(CASE WHEN case_status IN (0, 1, 2) AND del_flag = 0 THEN 1 END)",
     "desc": "尚未上传(0/1/2)的有效案例数；0=仅表单未扫描，1/2 已拆分为独立指标"},
    {"name": "未扫描案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["空案例数", "仅表单案例数"],
     "formula": "COUNT(CASE WHEN case_status = 0 AND del_flag = 0 THEN 1 END)",
     "desc": "建档但未做口扫的案例数(仅表单)"},
    {"name": "扫描未优化案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["待优化案例数"],
     "formula": "COUNT(CASE WHEN case_status = 1 AND del_flag = 0 THEN 1 END)",
     "desc": "扫描完但未完成 ScanPro 优化的案例数"},
    {"name": "有订单案例占比", "en": "", "agg": "SUM", "unit": "", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["cases_with_orders_ratio", "案例下单转化率", "转订单率", "成单率"],
     "formula": "SUM(has_order = 1 AND del_flag = 0) / NULLIF(COUNT(*), 0)",
     "desc": "已发起过订单的案例占全部案例的比例（原稿公式未排除已删除案例，分母补 NULLIF 防零除）"},
    {"name": "正畸案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["正畸病例数", "牙齿矫正案例数"],
     "formula": "COUNT(CASE WHEN case_type = 200 AND del_flag = 0 THEN 1 END)",
     "desc": "案例类型为正畸(200)的有效案例数"},
    {"name": "修复案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["修复病例数"],
     "formula": "COUNT(CASE WHEN case_type = 100 AND del_flag = 0 THEN 1 END)",
     "desc": "案例类型为修复(100)的有效案例数；注意其中含种植(is_plant=1)"},
    {"name": "案例类型未设置数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["无类型案例数"],
     "formula": "COUNT(CASE WHEN case_type IS NULL AND del_flag = 0 THEN 1 END)",
     "desc": "未传治疗方案时 case_type 保持 NULL(CaseServiceImpl:3373-3375 直接 return)；"
             "该桶占比高说明按类型分组的指标不可信"},
    {"name": "排牙请求案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["拆牙请求数", "需要排牙案例数"],
     "formula": "COUNT(CASE WHEN split_teeth_status IN (1, 2) AND del_flag = 0 THEN 1 END)",
     "desc": "已发起排牙/三方排牙请求的案例数"},
    {"name": "排牙成功案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["拆牙成功数"],
     "formula": "COUNT(CASE WHEN split_teeth_status = 3 AND del_flag = 0 THEN 1 END)",
     "desc": "三方拆牙返回成功的案例数"},
    {"name": "排牙失败案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["拆牙失败数"],
     "formula": "COUNT(CASE WHEN split_teeth_status = 4 AND del_flag = 0 THEN 1 END)",
     "desc": "三方拆牙返回失败的案例数"},
    {"name": "排牙成功率", "en": "", "agg": "SUM", "unit": "", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["拆牙成功率"],
     "formula": "SUM(split_teeth_status = 3) / NULLIF(SUM(split_teeth_status IN (1, 2, 3, 4)), 0)",
     "desc": "分母只取已发起过排牙请求(1/2/3/4)的案例，不含未排牙(0)"},
    {"name": "数据收集完成案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["已收集案例数"],
     "formula": "COUNT(CASE WHEN collection_status = 2 AND del_flag = 0 THEN 1 END)",
     "desc": "研发数据收集状态为已上传(2)的案例数"},
    {"name": "数据收集忽略案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["忽略收集数"],
     "formula": "COUNT(CASE WHEN collection_status = 4 AND del_flag = 0 THEN 1 END)",
     "desc": "被标记忽略收集(4)的案例数"},
    {"name": "双颌案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["上下颌都有案例数"],
     "formula": "COUNT(CASE WHEN is_upper = 1 AND is_lower = 1 AND del_flag = 0 THEN 1 END)",
     "desc": "同时包含上颌与下颌扫描的案例数"},
    {"name": "单颌案例数", "en": "", "agg": "SUM", "unit": "例", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["仅上颌或仅下颌数"],
     "formula": "SUM(CASE WHEN del_flag = 0 AND ((is_upper = 1 AND is_lower = 0) OR (is_upper = 0 AND is_lower = 1)) THEN 1 ELSE 0 END)",
     "desc": "只有上颌或只有下颌的案例数"},
    {"name": "案例总存储GB", "en": "", "agg": "SUM", "unit": "GB", "cat": "成本", "bok": "case",
     "col": "create_time", "aliases": ["total_case_storage_gb", "案例存储空间"],
     "formula": "SUM(CASE WHEN del_flag = 0 THEN occupied_space_size ELSE NULL END) / 1073741824.0",
     "desc": "有效案例占用云端存储(GB)，occupied_space_size 单位为字节；"
             "原稿未排除已删除案例，会把待清理数据算进成本"},
    {"name": "单案例平均存储MB", "en": "", "agg": "AVG", "unit": "MB", "cat": "成本", "bok": "case",
     "col": "create_time", "aliases": ["平均案例大小"],
     "formula": "AVG(CASE WHEN del_flag = 0 THEN occupied_space_size END) / 1048576.0",
     "desc": "单个有效案例的平均占用(MB)"},
    {"name": "待OSS清理案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "运维", "bok": "case",
     "col": "file_clean_time", "aliases": ["到期清理案例数", "文件回收案例数"],
     "formula": "COUNT(CASE WHEN file_clean_time IS NOT NULL AND file_clean_time <= NOW() AND del_flag <> 0 THEN 1 END)",
     "desc": "已到清理时间点、文件仍待回收的案例数；file_clean_time 是清理任务写入的目标时间"},
    {"name": "活跃口扫设备数", "en": "", "agg": "COUNT", "unit": "台", "cat": "设备", "bok": "case",
     "col": "create_time", "aliases": ["有案例的设备数"],
     "formula": "COUNT(DISTINCT CASE WHEN del_flag = 0 THEN serial_number END)",
     "desc": "统计周期内产生过案例的设备序列号去重数"},
    {"name": "活跃诊断医生数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "case",
     "col": "create_time", "aliases": ["有案例医生数", "开单医生数"],
     "formula": "COUNT(DISTINCT CASE WHEN del_flag = 0 THEN doctor_user_code END)",
     "desc": "去重诊断医生账号数；键用 doctor_user_code，姓名(可改)仅作展示"},
    {"name": "产生案例的诊所数", "en": "", "agg": "COUNT", "unit": "家", "cat": "客户", "bok": "case",
     "col": "create_time", "aliases": ["活跃医院数"],
     "formula": "COUNT(DISTINCT CASE WHEN del_flag = 0 THEN company_code END)",
     "desc": "去重机构编码数；键用 company_code 而非 company_name"},
    {"name": "第三方接入案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "开放平台", "bok": "case",
     "col": "create_time", "aliases": ["ISV案例数", "开放平台案例数"],
     "formula": "COUNT(CASE WHEN appid IS NOT NULL AND appid <> '' AND del_flag = 0 THEN 1 END)",
     "desc": "由 ISV/第三方 app 写入的案例数(appid 非空)"},
    {"name": "案例关联患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "case",
     "col": "create_time", "aliases": ["涉及病人数"],
     "formula": "COUNT(DISTINCT CASE WHEN del_flag = 0 THEN patient_file_code END)",
     "desc": "案例侧去重患者档案号数；与患者域「患者人数」交叉校验口径差异"},
    {"name": "平均案例处理时长小时", "en": "", "agg": "AVG", "unit": "小时", "cat": "业务", "bok": "case",
     "col": "create_time", "aliases": ["avg_case_cycle_hours", "cycle_time_hours", "案例周期"],
     "formula": "AVG(CASE WHEN case_status = 5 AND del_flag = 0 THEN TIMESTAMPDIFF(HOUR, create_time, update_time) END)",
     "desc": "近似口径：t_case_records 无完成时间列，update_time 会被后续任意更新(含文件清理)刷新；"
             "精确完成时长需状态流转日志，本期不纳入"},
    # ── order ──
    {"name": "技工所侧有效订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["lab侧订单数", "加工厂可见订单数"],
     "formula": "COUNT(CASE WHEN lab_del_flag = 0 THEN 1 END)",
     "desc": "技工所侧未删除的订单数，与诊所侧口径分开，不能混用"},
    {"name": "新增订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "create_time", "aliases": ["新订单数", "下单数"],
     "formula": "COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END)",
     "desc": "统计周期内创建的订单数；记录时间轴用 create_time，业务时间轴用 order_time"},
    {"name": "已完成订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["completed_orders", "结案订单数"],
     "formula": "COUNT(CASE WHEN order_status = 6 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "状态为已完成(6)的订单数。原稿写 status=6：列名错(本表是 order_status)，值 6 恰好正确"},
    {"name": "未发送订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["is_pending", "待发送订单数", "草稿订单数"],
     "formula": "COUNT(CASE WHEN order_status = 1 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已创建但未发送给技工所的订单数(1=未发送)。原稿 is_pending 定义为 status IN (1,9) 且无描述，"
             "1=未发送与 9=处理中是两端状态，已拆开"},
    {"name": "处理中订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["加工中订单数", "READY订单数"],
     "formula": "COUNT(CASE WHEN order_status = 9 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "技工所已开始加工(9=处理中)的订单数"},
    {"name": "已发送待接单订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["待接单订单数"],
     "formula": "COUNT(CASE WHEN order_status = 2 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已发送但技工所尚未处置(2=已发送)的订单数"},
    {"name": "已接单订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["接受订单数"],
     "formula": "COUNT(CASE WHEN order_status IN (3, 9, 5, 6, 8) AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已接受及其后续状态(3/9/5/6/8)的订单数"},
    {"name": "已拒绝订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["拒单数", "reject订单数"],
     "formula": "COUNT(CASE WHEN order_status = 4 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "技工所拒单(4=已拒绝)的订单数。原稿「订单状态」维度把 4 标为「已发货」，与 OrderStatusEnum 不符"},
    {"name": "已发货订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["shipped订单数", "出库订单数"],
     "formula": "COUNT(CASE WHEN order_status = 5 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已发货(5=已发货)的订单数"},
    {"name": "已归档订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["归档订单数"],
     "formula": "COUNT(CASE WHEN order_status = 8 AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已归档(8)的订单数"},
    {"name": "接单率", "en": "", "agg": "SUM", "unit": "", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["接受率", "技工所接单率"],
     "formula": "SUM(order_status IN (3, 9, 5, 6, 8)) / NULLIF(SUM(order_status IN (2, 3, 4, 9, 5, 6, 8)), 0)",
     "desc": "分母为已进入技工所处置环节(已发送及之后，不含诊所侧取消 7)的订单"},
    {"name": "拒单率", "en": "", "agg": "SUM", "unit": "", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["拒绝率"],
     "formula": "SUM(order_status = 4) / NULLIF(SUM(order_status IN (2, 3, 4, 9, 5, 6, 8)), 0)",
     "desc": "与接单率同分母，便于对比"},
    {"name": "订单完成率", "en": "", "agg": "SUM", "unit": "", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["完成率"],
     "formula": "SUM(order_status = 6) / NULLIF(COUNT(*), 0)",
     "desc": "已完成订单占全部有效订单比例"},
    {"name": "订单取消率", "en": "", "agg": "SUM", "unit": "", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["取消率"],
     "formula": "SUM(order_status = 7) / NULLIF(COUNT(*), 0)",
     "desc": "取消订单占全部有效订单比例"},
    {"name": "待发货积压订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["未发货订单数", "积压订单数"],
     "formula": "COUNT(CASE WHEN order_status IN (2, 3, 9) AND ship_date IS NULL AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "已接单但尚未登记发货的订单数，供应链卡点指标"},
    {"name": "平均发货时效小时", "en": "", "agg": "AVG", "unit": "小时", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["平均出货时长", "发货周期"],
     "formula": "AVG(CASE WHEN clinic_del_flag = 0 AND ship_date IS NOT NULL AND order_time IS NOT NULL "
               "THEN TIMESTAMPDIFF(HOUR, order_time, ship_date) END)",
     "desc": "从下单(order_time)到发货(ship_date)的平均小时数；两列均在本表，口径闭合"},
    {"name": "按期发货率", "en": "", "agg": "SUM", "unit": "", "cat": "订单", "bok": "order",
     "col": "ship_date", "aliases": ["承诺达成率", "准时发货率"],
     "formula": "SUM(CASE WHEN ship_date <= expected_arrival_date THEN 1 ELSE 0 END) / "
                "NULLIF(SUM(CASE WHEN ship_date IS NOT NULL AND expected_arrival_date IS NOT NULL THEN 1 ELSE 0 END), 0)",
     "desc": "以本表 expected_arrival_date(约定到达日)为基准的达成率；本表无实际签收时间列，"
             "不能算真实送达时效"},
    {"name": "逾期发货订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "订单", "bok": "order",
     "col": "ship_date", "aliases": ["超期发货数"],
     "formula": "COUNT(CASE WHEN ship_date > expected_arrival_date AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "发货日期晚于约定到达日期的订单数"},
    {"name": "承诺周期天数均值", "en": "", "agg": "AVG", "unit": "天", "cat": "订单", "bok": "order",
     "col": "order_time", "aliases": ["要求交付周期"],
     "formula": "AVG(CASE WHEN clinic_del_flag = 0 AND req_delivery_date IS NOT NULL AND order_time IS NOT NULL "
               "THEN TIMESTAMPDIFF(DAY, order_time, req_delivery_date) END)",
     "desc": "下单到客户要求交付日的天数，反映交付压力分布"},
    {"name": "已发货未登记物流单号订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "运维", "bok": "order",
     "col": "order_time", "aliases": ["物流单号缺失数"],
     "formula": "COUNT(CASE WHEN order_status IN (5, 6, 8) AND (track_number IS NULL OR track_number = '') "
               "AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "数据质量巡检：状态已发货但 track_number 为空的订单"},
    {"name": "活跃承接技工所数", "en": "", "agg": "COUNT", "unit": "家", "cat": "客户", "bok": "order",
     "col": "order_time", "aliases": ["接单技工所数", "活跃加工厂数"],
     "formula": "COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN laboratory_company_code END)",
     "desc": "去重承接技工所编码数"},
    {"name": "活跃下单诊所数", "en": "", "agg": "COUNT", "unit": "家", "cat": "客户", "bok": "order",
     "col": "order_time", "aliases": ["下单医院数"],
     "formula": "COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN company_code END)",
     "desc": "去重下单机构编码数"},
    {"name": "诊所-技工所活跃配对数", "en": "", "agg": "COUNT", "unit": "对", "cat": "业务", "bok": "order",
     "col": "order_time", "aliases": ["合作下单对数"],
     "formula": "COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 AND laboratory_company_code IS NOT NULL "
               "AND laboratory_company_code <> '' THEN CONCAT(company_code, '#', laboratory_company_code) END)",
     "desc": "实际发生下单的配对数，可与 partnership 域的登记合作总数对比算激活率"},
    {"name": "订单关联案例数", "en": "", "agg": "COUNT", "unit": "例", "cat": "业务", "bok": "order",
     "col": "order_time", "aliases": ["涉及病例数"],
     "formula": "COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN case_code END)",
     "desc": "订单侧去重案例编码，跨域引用案例域主键，不重复建模 Case 对象"},
    {"name": "第三方来源订单数", "en": "", "agg": "COUNT", "unit": "单", "cat": "开放平台", "bok": "order",
     "col": "order_time", "aliases": ["ISV订单数"],
     "formula": "COUNT(CASE WHEN appid IS NOT NULL AND appid <> '' AND clinic_del_flag = 0 THEN 1 END)",
     "desc": "由第三方 app 创建的订单数"},
    # ── patient ──
    {"name": "患者人数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["去重患者数", "病人数", "自然人患者数"],
     "formula": "COUNT(DISTINCT file_code)",
     "desc": "按患者档案编号去重的自然人数；与档案数之差即跨院重复程度"},
    {"name": "新增患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["新登记患者", "新建档数"],
     "formula": "COUNT(*)",
     "desc": "统计周期内新登记的档案数；时间轴固定用 registration_date(登记日)，不用 create_time(入库日)"},
    {"name": "有就诊记录患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "recent_visit_date", "aliases": ["就诊患者数", "已随访患者数"],
     "formula": "COUNT(CASE WHEN recent_visit_date IS NOT NULL THEN 1 END)",
     "desc": "有过最近就诊日期的患者数"},
    {"name": "沉默患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "recent_visit_date", "aliases": ["流失患者数", "超期未就诊患者数"],
     "formula": "COUNT(CASE WHEN recent_visit_date IS NULL OR "
               "recent_visit_date < DATE_SUB(NOW(), INTERVAL 180 DAY) THEN 1 END)",
     "desc": "180 天(阈值默认写死于口径)未就诊或从未就诊的患者数"},
    {"name": "常访患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["is_frequent_visitor", "高频患者", "复诊患者数"],
     "formula": "COUNT(CASE WHEN case_count >= 3 THEN 1 END)",
     "desc": "关联案例数≥3 的患者数；case_count 是档案表上的冗余计数列。"
             "原稿把 case_count>=3 做成布尔指标 is_frequent_visitor，属派生规则而非度量，已转为计数指标"},
    {"name": "患者平均案例数", "en": "", "agg": "AVG", "unit": "例", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["人均案例数", "人均就诊次数"],
     "formula": "AVG(case_count)",
     "desc": "每位患者的平均关联案例数(基于冗余列 case_count)"},
    {"name": "未成年患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "", "aliases": ["儿童患者数", "18岁以下患者数"],
     "formula": "COUNT(CASE WHEN birthday IS NOT NULL AND TIMESTAMPDIFF(YEAR, birthday, CURDATE()) < 18 THEN 1 END)",
     "desc": "现龄小于 18 的患者数"},
    {"name": "男性患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["男患者数"],
     "formula": "COUNT(CASE WHEN sex = 1 THEN 1 END)",
     "desc": "性别为男(1)的患者数；取值权威源 UserSexEnEnum"},
    {"name": "女性患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["女患者数"],
     "formula": "COUNT(CASE WHEN sex = 2 THEN 1 END)",
     "desc": "性别为女(2)的患者数"},
    {"name": "建档医院数", "en": "", "agg": "COUNT", "unit": "家", "cat": "客户", "bok": "patient",
     "col": "registration_date", "aliases": ["有患者的医院数"],
     "formula": "COUNT(DISTINCT company_code)",
     "desc": "有患者档案的机构编码去重数"},
    {"name": "手机号缺失患者数", "en": "", "agg": "COUNT", "unit": "人", "cat": "运维", "bok": "",
     "col": "", "aliases": ["无联系方式患者数"],
     "formula": "COUNT(CASE WHEN phone IS NULL OR phone = '' THEN 1 END)",
     "desc": "触达能力巡检：只统计缺失数量，禁止输出 phone 明文(PII)。"
             "phone 属 PII 列不在 patient 对象建模，故不绑对象(bok 空)，语义层不编译"},
    {"name": "跨院重复档案患者数", "en": "", "agg": "CUSTOM", "unit": "人", "cat": "运维", "bok": "",
     "col": "", "aliases": ["重复建档数"],
     "formula": "GROUP BY file_code HAVING COUNT(DISTINCT company_code) > 1",
     "desc": "巡检指标（语义层不编译，取数走专用数据集「COP·跨院重复档案患者明细」）："
             "同一 file_code 在多家机构存在档案的患者数，用于治理，不用于业务报表"},
    {"name": "档案案例数不一致患者数", "en": "", "agg": "CUSTOM", "unit": "人", "cat": "运维", "bok": "",
     "col": "", "aliases": ["case_count脏数据患者数"],
     "formula": "IFNULL(p.case_count, 0) <> IFNULL(x.c, 0)（p=患者档案 LEFT JOIN 案例表按 patient_file_code 实算）",
     "desc": "巡检指标（语义层不编译，取数走专用数据集「COP·档案案例数不一致患者明细」）："
             "冗余列 case_count 与案例表实算不一致的患者数，为 case_count 类指标提供可信度度量"},
]

# ── 维度字典（Sheet③ 逐行转录）────────────────────────────────────────────
# name 全局唯一：对象域同名维度按先占原则消歧落库（Excel 名进别名或停用重建）。
# 与既有 name/name_en/别名冲突的检索词不登记（撞名不登记，靠 name/name_en 检索）。
# spec: name, en, col=物理列, cat=类别, labels=枚举值→标签(以 platform-dto 枚举类为权威),
#       aliases, bok, desc
DIMENSION_UPDATES: list[dict] = [
    {"name": "创建日期", "en": "create_date", "col": "create_time", "cat": "时间", "labels": None, "bok": "case",
     "aliases": [],
     "desc": "记录创建时间(平台侧入库时间)；语义查询按 time_grain(day/week/month) 分桶。"
             "本表有 create_time/scan_time/update_time/local_modify_date 多个时间列，时间轴必须显式声明"},
    {"name": "案例状态", "en": "case_status", "col": "case_status", "cat": "状态", "bok": "case",
     "labels": {"0": "仅表单(创建未扫描)", "1": "需处理(扫描完未优化)", "2": "扫描完成(ScanPro优化并Finish)",
                "3": "已上传", "4": "已下单", "5": "已完成"},
     "aliases": ["病例状态", "案例进展"],
     "desc": "案例生命周期状态(CaseStatusEnum 0-5，以枚举类为权威源重写映射)"},
    {"name": "案例类型", "en": "case_type", "col": "case_type", "cat": "属性", "bok": "case",
     "labels": {"0": "其他", "100": "修复", "101": "种植(代码永不产生,勿用)", "200": "正畸"},
     "aliases": ["case类型", "修复/种植/正畸"],
     "desc": "案例业务类型(0/100/101/200，OrderTypeEnum)；101 永不落库，分桶须与 is_plant 一起看；"
             "NULL 表示未传治疗方案"},
    {"name": "就诊医院", "en": "hospital_name", "col": "company_name", "cat": "组织", "labels": None, "bok": "case",
     "aliases": ["医院/诊所名称", "医院名称", "机构"],
     "desc": "案例所属机构名称(展示用)；分组分析优先用 company_code(见「就诊医院编码」)"},
    {"name": "诊断医生", "en": "doctor_name", "col": "doctor_user_name", "cat": "组织", "labels": None, "bok": "case",
     "aliases": ["医师", "医生", "诊断医生姓名", "doctor"],
     "desc": "开单诊断医生账号名(展示用)；稳定键见「诊断医生编码」，与报告牙医不是同一角色"},
    {"name": "拆牙状态", "en": "split_teeth_status", "col": "split_teeth_status", "cat": "状态", "bok": "case",
     "labels": {"0": "不排牙", "1": "请求排牙", "2": "已发起三方请求", "3": "排牙成功", "4": "排牙失败"},
     "aliases": ["排牙状态", "三方拆牙"],
     "desc": "三方拆牙流程状态(SplitTeethStatusEnum 0-4，以代码枚举为权威)"},
    {"name": "订单状态", "en": "order_status", "col": "order_status", "cat": "状态", "bok": "order",
     "labels": {"0": "初始化", "1": "未发送", "2": "已发送", "3": "已接受", "4": "已拒绝", "5": "已发货",
                "6": "已完成", "7": "已取消", "8": "已归档", "9": "处理中"},
     "aliases": ["order_status", "订单进展", "加工状态"],
     "desc": "订单流转状态(OrderStatusEnum 0-9)。原稿标签为通用电商值(待确认/已确认/加工中/已发货)，"
             "与本项目枚举完全不符，且物理列写 status 实际是 order_status"},
    {"name": "应用域标识", "en": "", "col": "appmanager", "cat": "属性", "labels": None, "bok": "case",
     "aliases": ["应用域", "域", "appmanager", "所属站点"],
     "desc": "案例所属应用域/站点标识。'新加坡'既可作为国家值也可作为站点值，命中歧义时由 planner 回抛候选；"
             "「站点」一词被数据源维度占，不作为别名"},
    {"name": "是否种植修复", "en": "is_plant", "col": "is_plant", "cat": "属性", "bok": "case",
     "labels": {"0": "否", "1": "是"}, "aliases": ["是否种植", "种植", "is_plant"],
     "desc": "种植修复标记(权威口径，优于 case_type=101)"},
    {"name": "案例下单渠道", "en": "case_app_code", "col": "app_code", "cat": "属性", "labels": None, "bok": "case",
     "aliases": [],
     "desc": "案例来源渠道编码(app_code)，如 CONNECT/第三方。Excel 别名「渠道」被 RUM 应用渠道占、"
             "「app_code」归订单域、「来源渠道」归患者域，按先占原则不再登记"},
    {"name": "是否锁定", "en": "case_locked", "col": "locked", "cat": "属性", "bok": "case",
     "labels": {"0": "否", "1": "是"}, "aliases": ["locked", "锁定"],
     "desc": "案例是否处于锁定状态"},
    {"name": "患者来源渠道", "en": "patient_channel", "col": "channel", "cat": "属性", "labels": None, "bok": "patient",
     "aliases": ["来源渠道", "channel", "获客渠道"],
     "desc": "患者来源渠道(channel，取值需按数据分布补枚举)；Excel 业务权威名「来源渠道」降级为别名"},
    {"name": "下单渠道", "en": "order_app_code", "col": "app_code", "cat": "属性", "labels": None, "bok": "order",
     "aliases": ["app_code", "下单渠道编码"],
     "desc": "订单来源渠道编码(app_code)"},
    {"name": "是否有上颌", "en": "order_is_upper", "col": "is_upper", "cat": "属性", "bok": "order",
     "labels": {"0": "否", "1": "是"}, "aliases": [],
     "desc": "订单是否含上颌。别名「上颌/is_upper」让给案例域新行(案例域是主问法域)，靠 name 检索"},
    {"name": "是否有下颌", "en": "order_is_lower", "col": "is_lower", "cat": "属性", "bok": "order",
     "labels": {"0": "否", "1": "是"}, "aliases": [],
     "desc": "订单是否含下颌。别名「下颌/is_lower」让给案例域新行"},
    {"name": "要求送达时间段", "en": "req_delivery_time_period", "col": "req_delivery_time_period_code", "cat": "属性",
     "labels": None, "bok": "order", "aliases": ["要求交付时段", "时段"],
     "desc": "客户要求的交付时段编码(取值需按数据分布补全枚举，勿凭空命名)；"
             "原绑定列 req_delivery_time_period 为文本列，修正绑定到 _code 编码列"},
    {"name": "接单技工所", "en": "laboratory_company_name", "col": "laboratory_company_name", "cat": "组织",
     "labels": None, "bok": "order",
     "aliases": ["技工所", "加工厂", "实验室", "laboratory", "承接技工所"],
     "desc": "承接加工任务的技工所名称(展示)；Excel 业务权威名「承接技工所」降级为别名"},
    {"name": "是否展示牙医签名", "en": "order_show_dentist_signature", "col": "show_dentist_signature", "cat": "属性",
     "bok": "order", "labels": {"0": "否", "1": "是"}, "aliases": ["是否显示牙医签名", "签名"],
     "desc": "订单是否展示牙医签名"},
    {"name": "是否显示患者姓名", "en": "order_is_show_patient_name", "col": "is_show_patient_name", "cat": "属性",
     "bok": "order", "labels": {"0": "否", "1": "是"}, "aliases": ["匿名下单"],
     "desc": "合规维度：控制订单在技工所侧是否暴露患者姓名；该列决定脱敏口径，必须登记"},
    {"name": "诊所删除标识", "en": "clinic_del_flag", "col": "clinic_del_flag", "cat": "状态", "bok": "order",
     "labels": {"0": "未删除(有效)", "1": "已删除(回收站)"}, "aliases": ["诊所删除"],
     "desc": "诊所(下单方)侧删除标记。订单域是双角色表，必须区分两侧删除口径"},
    {"name": "技工所删除标识", "en": "lab_del_flag", "col": "lab_del_flag", "cat": "状态", "bok": "order",
     "labels": {"0": "未删除(有效)", "1": "已删除(回收站)"}, "aliases": ["加工厂删除"],
     "desc": "技工所(承接方)侧删除标记"},
    {"name": "Order创建日期", "en": "order_create_date", "col": "create_time", "cat": "时间", "labels": None,
     "bok": "order", "aliases": ["记录创建"],
     "desc": "平台入库时间；仅用于记录增长类统计，不用于业务时效。"
             "别名「创建时间」让给案例域「创建日期」(先占)，业务时效问法用「下单时间」"},
]

DIMENSION_RETIRES: list[dict] = [
    {"name": "sex", "alt": "患者性别", "reason": "业务权威名缺失(直接用物理列名)，且枚举缺 3=intersex"},
    {"name": "案例步骤", "alt": "案例状态", "reason": "t_case_records 不存在 case_step 列；步骤语义已含在案例状态枚举标签中"},
    {"name": "patient_sex", "alt": "病人性别", "reason": "英文行降级为别名(业务权威名改中文)，枚举补 3=intersex"},
    {"name": "产品改善计划状态", "alt": "数据收集状态", "reason": "语义错位：真实列 collection_status 是研发数据收集状态"
                                                  "(CollectionStatusEnum 0-4)，与产品改善计划无关"},
    {"name": "患者删除标记", "alt": "患者档案数(有效口径见指标描述)", "reason": "Excel patient 域未纳入此维度(有效口径以"
                                              "指标 description 为准)，且其别名 已删除 与案例域「删除标记」冲突"},
]

# 域外行的脏别名清理：这两行属于其他业务域(case_stat 日粒度快照 / isv_integration 集成台账)，
# 不改其口径，只回收被本域权威名占用的别名，避免 planner 检索歧义。
EXTRA_ALIAS_FIXES: list[dict] = [
    {"table": "adh_metrics", "name": "日案例数量", "aliases": [],
     "reason": "原别名 案例数/案例总数/案例数量 与案例域权威指标检索词冲突"},
    {"table": "adh_dimensions", "name": "集成来源应用", "aliases": ["集成来源应用码"],
     "reason": "原别名 应用域标识 与案例域「应用域标识」维度冲突"},
]

DIMENSION_CREATES: list[dict] = [
    # ── case（Sheet③ case 域新增行；「应用域」并入现有「应用域标识」别名，不新建）──
    {"name": "就诊医院编码", "en": "company_code", "col": "company_code", "cat": "组织", "labels": None,
     "bok": "case", "aliases": ["机构编码", "医院编码"],
     "desc": "机构稳定编码，与「就诊医院」(名称)同维度两种物理实现"},
    {"name": "诊断医生编码", "en": "doctor_user_code", "col": "doctor_user_code", "cat": "组织", "labels": None,
     "bok": "case", "aliases": ["医生账号", "doctor_code"],
     "desc": "诊断医生账号编码(稳定键)；与报告牙医不是同一角色，勿混用"},
    {"name": "病人性别", "en": "case_patient_sex", "col": "patient_sex", "cat": "属性", "bok": "case",
     "labels": {"0": "other 未知", "1": "male 男", "2": "female 女", "3": "intersex 双性"},
     "aliases": ["patient_sex"],
     "desc": "案例上的病人性别(UserSexEnEnum 0-3)。原稿只列 0/1/2 缺 3=intersex，已补；"
             "别名「患者性别」归患者域新行，不在此登记"},
    {"name": "病人年龄", "en": "patient_age", "col": "patient_age", "cat": "属性", "labels": None, "bok": "case",
     "aliases": [],
     "desc": "案例上登记的病人年龄(数值，可分桶)。别名「患者年龄/age」归患者域新行"},
    {"name": "数据收集状态", "en": "case_collection_status", "col": "collection_status", "cat": "状态", "bok": "case",
     "labels": {"0": "未标记(未收集)", "1": "已标记(收集中)", "2": "已上传(已收集)", "3": "已下载", "4": "忽略收集"},
     "aliases": ["collection_status", "收集状态", "数据回收"],
     "desc": "研发数据收集状态(CollectionStatusEnum 0-4)。真实列存在，原稿完全未登记该维度；"
             "原「产品改善计划状态」属语义错位已停用"},
    {"name": "扫描时间", "en": "scan_time", "col": "scan_time", "cat": "时间", "labels": None, "bok": "case",
     "aliases": ["扫描日期", "口扫时间"],
     "desc": "本地端口扫发生时间(业务时间轴)。本表多个 datetime 列必须显式声明时间轴，不能让兜底猜"},
    {"name": "本地修改时间", "en": "case_local_modify_date", "col": "local_modify_date", "cat": "时间",
     "labels": None, "bok": "case", "aliases": ["端上修改时间"],
     "desc": "本地客户端侧的最后修改时间(设备所在时区的业务时间)，跨时区口径纠偏用；"
             "患者域同列行是「患者本地修改时间」"},
    {"name": "用户时区", "en": "case_up_time_zone", "col": "up_time_zone", "cat": "属性", "labels": None,
     "bok": "case", "aliases": ["up_time_zone", "上传用户所在时区", "时区"],
     "desc": "案例创建方所在时区；跨时区按天分桶必须先声明此维度或转 UTC"},
    {"name": "更新时间", "en": "update_time", "col": "update_time", "cat": "时间", "labels": None, "bok": "case",
     "aliases": ["最后更新时间"],
     "desc": "记录最后更新时间；会被文件清理任务刷新，不能当「完成时间」"},
    {"name": "上级应用域", "en": "parent_appmanager", "col": "parent_appmanager", "cat": "属性", "labels": None,
     "bok": "case", "aliases": ["父域"],
     "desc": "上级/父应用域，用于跨站汇总口径"},
    {"name": "第三方应用", "en": "appid", "col": "appid", "cat": "属性", "labels": None, "bok": "case",
     "aliases": ["ISV应用", "接入应用"],
     "desc": "写入该案例的第三方 app 标识；空值表示非开放平台来源"},
    {"name": "Connect版本", "en": "c_version", "col": "c_version", "cat": "属性", "labels": None, "bok": "case",
     "aliases": ["connect版本"],
     "desc": "Connect 客户端版本号(字符串，勿用大小比较做版本筛选)。"
             "别名「客户端版本」被设备操作日志维度占，不登记"},
    {"name": "ScanPro版本", "en": "s_version", "col": "s_version", "cat": "属性", "labels": None, "bok": "case",
     "aliases": ["scanpro版本"],
     "desc": "ScanPro 版本号"},
    {"name": "案例固件版本", "en": "f_version", "col": "f_version", "cat": "属性", "labels": None, "bok": "case",
     "aliases": ["设备固件"],
     "desc": "扫描设备固件版本号。「固件版本」被设备操作日志维度占，故落库名加对象域前缀"},
    {"name": "扫描设备", "en": "", "col": "serial_number", "cat": "设备", "labels": None, "bok": "case",
     "aliases": ["口扫仪"],
     "desc": "产生案例的设备序列号；跨域引用设备域对象，不在此重复建模 Equipment。"
             "别名「设备序列号/serial_number」被设备操作日志维度占，不登记"},
    {"name": "患者档案号", "en": "case_patient_file_code", "col": "patient_file_code", "cat": "客户", "labels": None,
     "bok": "case", "aliases": ["病人档案号", "档案编码"],
     "desc": "案例关联的患者档案编号，与患者域 file_code 对应；患者域权威行是「患者档案编号」"},
    {"name": "外部患者号", "en": "case_out_patient_id", "col": "out_patient_id", "cat": "客户", "labels": None,
     "bok": "case", "aliases": ["HIS患者号"],
     "desc": "外部系统(HIS)患者标识，用于跨系统对账。patient 域同列行是「患者外部患者号」"},
    {"name": "案例是否有上颌", "en": "case_is_upper", "col": "is_upper", "cat": "属性", "bok": "case",
     "labels": {"0": "否", "1": "是"}, "aliases": ["上颌", "is_upper"],
     "desc": "是否含上颌扫描。订单域同名行保留「是否有上颌」，别名让给本行"},
    {"name": "案例是否有下颌", "en": "case_is_lower", "col": "is_lower", "cat": "属性", "bok": "case",
     "labels": {"0": "否", "1": "是"}, "aliases": ["下颌", "is_lower"],
     "desc": "是否含下颌扫描。订单域同名行保留「是否有下颌」"},
    {"name": "是否已下单", "en": "has_order", "col": "has_order", "cat": "属性", "bok": "case",
     "labels": {"0": "否", "1": "是"}, "aliases": ["有订单", "is_ordered"],
     "desc": "案例是否已发起过订单"},
    {"name": "删除标记", "en": "del_flag", "col": "del_flag", "cat": "状态", "bok": "case",
     "labels": {"0": "未删除(有效)", "1": "已删除(回收站)"}, "aliases": ["已删除", "回收"],
     "desc": "逻辑删除标记(CaseRecordMapper.xml:35/45 有 del_flag=0，:73/87/307/315 回收站用 del_flag=1，"
             "代码中无值 2)；登记为可筛维度，防止「有效/全部」口径歧义"},
    # ── order ──
    {"name": "下单诊所", "en": "order_company_name", "col": "company_name", "cat": "组织", "labels": None,
     "bok": "order", "aliases": ["诊所", "医院", "客户机构"],
     "desc": "下单机构名称(展示)；分组优先用 company_code(见「下单诊所编码」)"},
    {"name": "下单诊所编码", "en": "order_company_code", "col": "company_code", "cat": "组织", "labels": None,
     "bok": "order", "aliases": [],
     "desc": "下单机构稳定编码。Excel 别名 company_code/机构编码 被案例域「就诊医院编码」占，不登记"},
    {"name": "承接技工所编码", "en": "laboratory_company_code", "col": "laboratory_company_code", "cat": "组织",
     "labels": None, "bok": "order", "aliases": ["加工厂编码"],
     "desc": "承接技工所稳定编码，跨域引用 company 域"},
    {"name": "技工所接单人", "en": "laboratory_user_name", "col": "laboratory_user_name", "cat": "组织",
     "labels": None, "bok": "order", "aliases": ["加工师"],
     "desc": "技工所侧处理人姓名"},
    {"name": "订单诊断医生", "en": "order_doctor_user_name", "col": "doctor_user_name", "cat": "组织",
     "labels": None, "bok": "order", "aliases": ["doctor_user_name"],
     "desc": "订单上的诊断医生账号名。别名「医生」被案例域「诊断医生」占，不登记"},
    {"name": "报告牙医", "en": "order_dentist_name", "col": "dentist_name", "cat": "组织", "labels": None,
     "bok": "order", "aliases": ["牙医"],
     "desc": "订单上的牙医姓名(与操作账号 doctor_user_name 分列，来源不同)"},
    {"name": "订单设备序列号", "en": "", "col": "serial_number", "cat": "设备", "labels": None, "bok": "order",
     "aliases": ["扫描仪"],
     "desc": "下单来源设备序列号。「设备序列号/serial_number」被设备操作日志维度占，不登记"},
    {"name": "关联案例编码", "en": "case_code", "col": "case_code", "cat": "业务", "labels": None, "bok": "order",
     "aliases": ["病例号"],
     "desc": "订单对应的案例编码(跨域逻辑外键，不重复建模 Case)"},
    {"name": "订单案例类型", "en": "order_case_type", "col": "case_type", "cat": "属性", "bok": "order",
     "labels": {"0": "其他", "100": "修复", "101": "种植(代码永不产生,勿用)", "200": "正畸"},
     "aliases": [],
     "desc": "订单冗余存储的案例类型，与案例域同枚举。"
             "别名 case_type/业务类型 被案例域「案例类型」与三方日志维度占，不登记"},
    {"name": "订单应用域", "en": "order_appmanager", "col": "appmanager", "cat": "属性", "labels": None,
     "bok": "order", "aliases": [],
     "desc": "订单所属应用域/站点。别名 appmanager/站点/域 被案例域「应用域标识」占，不登记"},
    {"name": "订单第三方应用", "en": "order_appid", "col": "appid", "cat": "属性", "labels": None,
     "bok": "order", "aliases": [],
     "desc": "创建订单的第三方 app 标识。别名 appid 被案例域「第三方应用」占，不登记"},
    {"name": "下单时间", "en": "order_time", "col": "order_time", "cat": "时间", "labels": None, "bok": "order",
     "aliases": ["订单时间", "业务下单时间"],
     "desc": "业务口径的下单时间(订单域首选时间轴)。本表同时有 create_time/order_time/ship_date/"
             "expected_arrival_date/req_delivery_date/local_modify_date，时间轴必须显式指定"},
    {"name": "发货日期", "en": "ship_date", "col": "ship_date", "cat": "时间", "labels": None, "bok": "order",
     "aliases": ["出货日期"], "desc": "实际发货日期"},
    {"name": "约定到达日期", "en": "expected_arrival_date", "col": "expected_arrival_date", "cat": "时间",
     "labels": None, "bok": "order", "aliases": ["预计到达"],
     "desc": "与客户约定的到达日期，是按期发货率的基准"},
    {"name": "要求交付日期", "en": "req_delivery_date", "col": "req_delivery_date", "cat": "时间", "labels": None,
     "bok": "order", "aliases": ["客户期望交付日"],
     "desc": "客户提出的要求交付日期"},
    {"name": "订单用户时区", "en": "order_up_time_zone", "col": "up_time_zone", "cat": "属性", "labels": None,
     "bok": "order", "aliases": [],
     "desc": "下单方时区，跨时区按天分桶用。别名 时区/up_time_zone 被案例域「用户时区」占，不登记"},
    # ── patient ──
    {"name": "患者性别", "en": "patient_gender", "col": "sex", "cat": "属性", "bok": "patient",
     "labels": {"0": "other 未知", "1": "male 男", "2": "female 女", "3": "intersex 双性"},
     "aliases": ["sex"],
     "desc": "患者性别(UserSexEnEnum 0=other/1=male/2=female/3=intersex)。原稿写 {0:未知,1:男,2:女} 缺 intersex；"
             "「性别」一词被用户域维度占、患者性别不登记「性别」别名；案例域同名行是「病人性别」"},
    {"name": "登记日期", "en": "registration_date", "col": "registration_date", "cat": "时间", "labels": None,
     "bok": "patient", "aliases": ["建档日期", "入档时间"],
     "desc": "患者档案登记日期(患者域首选时间轴)。患者表同时有 create_time/update_time/"
             "registration_date/recent_visit_date/local_modify_date，必须显式声明"},
    {"name": "最近就诊日期", "en": "recent_visit_date", "col": "recent_visit_date", "cat": "时间", "labels": None,
     "bok": "patient", "aliases": ["最近就诊", "复诊日期"],
     "desc": "最近一次就诊日期；沉默/流失分析的唯一可用时间轴(本表无逐次就诊明细)"},
    {"name": "出生日期", "en": "birthday", "col": "birthday", "cat": "时间", "labels": None, "bok": "patient",
     "aliases": ["生日"], "desc": "出生日(年龄的权威源，按月/年可分桶)"},
    {"name": "患者年龄", "en": "age", "col": "age", "cat": "属性", "labels": None, "bok": "patient",
     "aliases": ["登记年龄"],
     "desc": "建档时写入的年龄快照，仅用于按年龄段分组，不用于现龄统计(现龄由 birthday 现算)"},
    {"name": "患者省份", "en": "patient_province", "col": "province", "cat": "地理", "labels": None, "bok": "patient",
     "aliases": ["province"],
     "desc": "患者所在省份。别名「省份」被用户/企业/RUM 域占，不登记"},
    {"name": "患者城市", "en": "patient_city", "col": "city", "cat": "地理", "labels": None, "bok": "patient",
     "aliases": ["city"],
     "desc": "患者所在城市。别名「城市」被用户/企业/RUM 域占，不登记"},
    {"name": "患者区县", "en": "district", "col": "district", "cat": "地理", "labels": None, "bok": "patient",
     "aliases": ["区县"], "desc": "患者所在区县。真实列存在，原稿未登记"},
    {"name": "患者乡镇", "en": "town", "col": "town", "cat": "地理", "labels": None, "bok": "patient",
     "aliases": ["乡镇", "街道"], "desc": "患者所在乡镇/街道，最细地理粒度"},
    {"name": "转介码", "en": "referral_code", "col": "referral_code", "cat": "属性", "labels": None, "bok": "patient",
     "aliases": ["推荐码"], "desc": "转介绍编码，用于口碑传播分析"},
    {"name": "建档机构", "en": "patient_company_code", "col": "company_code", "cat": "组织", "labels": None,
     "bok": "patient", "aliases": ["所属医院"],
     "desc": "档案归属机构编码(跨域引用 company 域)；本表无机构名称列，展示需 JOIN。"
             "Excel 别名 company_code 被案例域占、诊所/医院归订单域，不登记"},
    {"name": "患者档案编号", "en": "patient_file_code", "col": "file_code", "cat": "客户", "labels": None,
     "bok": "patient", "aliases": ["档案编号"],
     "desc": "患者档案业务编号，人数去重键。案例域同列行是「患者档案号」(案例域先占原名)"},
    {"name": "患者唯一标识", "en": "patient_uid", "col": "patient_uid", "cat": "客户", "labels": None, "bok": "patient",
     "aliases": ["uid"],
     "desc": "患者主键 UID(技术键，报表优先用 file_code)"},
    {"name": "患者外部患者号", "en": "patient_out_patient_id", "col": "out_patient_id", "cat": "客户",
     "labels": None, "bok": "patient", "aliases": ["HIS号"],
     "desc": "外部 HIS 系统患者号。Excel 别名 out_patient_id 被案例域行占，不登记"},
    {"name": "建档人", "en": "patient_user_code", "col": "user_code", "cat": "组织", "labels": None, "bok": "patient",
     "aliases": ["录入人"],
     "desc": "创建该档案的用户账号编码。别名 user_code 被设备操作日志维度占，不登记"},
    {"name": "患者本地修改时间", "en": "patient_local_modify_date", "col": "local_modify_date", "cat": "时间",
     "labels": None, "bok": "patient", "aliases": [],
     "desc": "本地端修改时间，跨时区口径纠偏用；案例域同列行是「本地修改时间」"},
]

# ── 数据集（13 个 SQL 数据集；口径=指标/维度字典；PII 红线：不含任何患者敏感列）──────
def _dim(field: str, label: str) -> dict:
    return {"role": "dimension", "field": field, "label": label}


def _msr(field: str, label: str, unit: str) -> dict:
    return {"role": "measure", "field": field, "label": label, "agg": "NONE", "unit": unit}


DATASETS: list[dict] = [
    {"name": "COP·案例运营KPI", "obj": "case", "paginable": False,
     "desc": "案例域总览KPI（全量聚合，口径=指标字典同名指标）：有效案例/已完成/待处理/云端存储",
     "sql": ("SELECT COUNT(CASE WHEN del_flag = 0 THEN id END) AS case_count, "
             "COUNT(CASE WHEN case_status = 5 AND del_flag = 0 THEN 1 END) AS completed_cases, "
             "COUNT(CASE WHEN case_status IN (0, 1, 2) AND del_flag = 0 THEN 1 END) AS pending_cases, "
             "SUM(CASE WHEN del_flag = 0 THEN occupied_space_size ELSE NULL END) / 1073741824.0 AS storage_gb "
             "FROM t_case_records LIMIT 10"),
     "fields": [_msr("case_count", "案例数量", "例"), _msr("completed_cases", "已完成案例数", "例"),
                _msr("pending_cases", "待处理案例数", "例"), _msr("storage_gb", "案例总存储GB", "GB")]},
    {"name": "COP·订单运营KPI", "obj": "order", "paginable": False,
     "desc": "订单域总览KPI（诊所侧口径 clinic_del_flag=0）：订单数/已完成/平均发货时效/物流单号缺失",
     "sql": ("SELECT COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END) AS order_count, "
             "COUNT(CASE WHEN order_status = 6 AND clinic_del_flag = 0 THEN 1 END) AS completed_orders, "
             "AVG(CASE WHEN clinic_del_flag = 0 AND ship_date IS NOT NULL AND order_time IS NOT NULL "
             "THEN TIMESTAMPDIFF(HOUR, order_time, ship_date) END) AS avg_ship_hours, "
             "COUNT(CASE WHEN order_status IN (5, 6, 8) AND (track_number IS NULL OR track_number = '') "
             "AND clinic_del_flag = 0 THEN 1 END) AS missing_track_orders "
             "FROM t_order_records LIMIT 10"),
     "fields": [_msr("order_count", "订单数", "单"), _msr("completed_orders", "已完成订单数", "单"),
                _msr("avg_ship_hours", "平均发货时效小时", "小时"),
                _msr("missing_track_orders", "已发货未登记物流单号订单数", "单")]},
    {"name": "COP·患者运营KPI", "obj": "patient", "paginable": False,
     "desc": "患者域总览KPI：去重患者人数/档案条数（不含任何 PII 列）",
     "sql": ("SELECT COUNT(DISTINCT file_code) AS patient_count, COUNT(*) AS patient_records "
             "FROM t_user_hospital_patient LIMIT 10"),
     "fields": [_msr("patient_count", "患者人数", "人"), _msr("patient_records", "患者档案数", "人")]},
    {"name": "COP·案例状态分布", "obj": "case", "paginable": False,
     "desc": "案例状态分布（CaseStatusEnum 0-5 标签，del_flag=0）",
     "sql": ("SELECT CASE case_status WHEN 0 THEN '仅表单(创建未扫描)' WHEN 1 THEN '需处理(扫描完未优化)' "
             "WHEN 2 THEN '扫描完成(ScanPro优化并Finish)' WHEN 3 THEN '已上传' WHEN 4 THEN '已下单' "
             "WHEN 5 THEN '已完成' ELSE '其他' END AS status_label, COUNT(*) AS cnt "
             "FROM t_case_records WHERE del_flag = 0 GROUP BY case_status ORDER BY cnt DESC LIMIT 100"),
     "fields": [_dim("status_label", "案例状态"), _msr("cnt", "案例数", "例")]},
    {"name": "COP·案例类型分布", "obj": "case", "paginable": False,
     "desc": "案例类型分布（0/100/101/200；101 种植永不落库，种植看 is_plant 口径）",
     "sql": ("SELECT CASE case_type WHEN 0 THEN '其他' WHEN 100 THEN '修复' WHEN 101 THEN '种植(勿用)' "
             "WHEN 200 THEN '正畸' ELSE '未设置' END AS type_label, COUNT(*) AS cnt "
             "FROM t_case_records WHERE del_flag = 0 GROUP BY case_type ORDER BY cnt DESC LIMIT 100"),
     "fields": [_dim("type_label", "案例类型"), _msr("cnt", "案例数", "例")]},
    {"name": "COP·订单状态分布", "obj": "order", "paginable": False,
     "desc": "订单状态分布（OrderStatusEnum 0-9 全枚举，诊所侧有效订单）",
     "sql": ("SELECT CASE order_status WHEN 0 THEN '初始化' WHEN 1 THEN '未发送' WHEN 2 THEN '已发送' "
             "WHEN 3 THEN '已接受' WHEN 4 THEN '已拒绝' WHEN 5 THEN '已发货' WHEN 6 THEN '已完成' "
             "WHEN 7 THEN '已取消' WHEN 8 THEN '已归档' WHEN 9 THEN '处理中' ELSE '其他' END AS status_label, "
             "COUNT(*) AS cnt "
             "FROM t_order_records WHERE clinic_del_flag = 0 GROUP BY order_status ORDER BY cnt DESC LIMIT 100"),
     "fields": [_dim("status_label", "订单状态"), _msr("cnt", "订单数", "单")]},
    {"name": "COP·患者性别分布", "obj": "patient", "paginable": False,
     "desc": "患者性别分布（UserSexEnEnum 0-3，含 3=intersex）",
     "sql": ("SELECT CASE sex WHEN 0 THEN '未知' WHEN 1 THEN '男' WHEN 2 THEN '女' WHEN 3 THEN '双性' "
             "ELSE '未填写' END AS sex_label, COUNT(*) AS cnt "
             "FROM t_user_hospital_patient GROUP BY sex ORDER BY cnt DESC LIMIT 100"),
     "fields": [_dim("sex_label", "患者性别"), _msr("cnt", "患者数", "人")]},
    {"name": "COP·患者省份TOP10", "obj": "patient", "paginable": False,
     "desc": "患者省份分布 TOP10（province 空串/NULL 归入「未知」）",
     "sql": ("SELECT COALESCE(NULLIF(province, ''), '未知') AS province_label, COUNT(*) AS cnt "
             "FROM t_user_hospital_patient GROUP BY province_label ORDER BY cnt DESC LIMIT 10"),
     "fields": [_dim("province_label", "省份"), _msr("cnt", "患者数", "人")]},
    {"name": "COP·案例新增趋势", "obj": "case", "paginable": False,
     "desc": "近 30 天按天案例新增（时间轴=create_time 记录时间轴，del_flag=0）",
     "sql": ("SELECT DATE(create_time) AS stat_date, COUNT(CASE WHEN del_flag = 0 THEN 1 END) AS cnt "
             "FROM t_case_records WHERE create_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) "
             "GROUP BY DATE(create_time) ORDER BY stat_date LIMIT 100"),
     "fields": [_dim("stat_date", "日期"), _msr("cnt", "新增案例数", "例")]},
    {"name": "COP·订单新增趋势", "obj": "order", "paginable": False,
     "desc": "近 30 天按天订单新增（时间轴=order_time 业务时间轴，诊所侧有效）",
     "sql": ("SELECT DATE(order_time) AS stat_date, COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END) AS cnt "
             "FROM t_order_records WHERE order_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) "
             "GROUP BY DATE(order_time) ORDER BY stat_date LIMIT 100"),
     "fields": [_dim("stat_date", "日期"), _msr("cnt", "新增订单数", "单")]},
    {"name": "COP·患者登记趋势", "obj": "patient", "paginable": False,
     "desc": "近 30 天按天患者登记（时间轴=registration_date 患者域首选时间轴）",
     "sql": ("SELECT DATE(registration_date) AS stat_date, COUNT(*) AS cnt "
             "FROM t_user_hospital_patient WHERE registration_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) "
             "GROUP BY DATE(registration_date) ORDER BY stat_date LIMIT 100"),
     "fields": [_dim("stat_date", "日期"), _msr("cnt", "新增患者", "人")]},
    {"name": "COP·跨院重复档案患者明细", "obj": "patient", "paginable": True,
     "desc": "巡检明细：同一 file_code 在多家机构存在档案（「跨院重复档案患者数」的取数数据集）",
     "sql": ("SELECT file_code, COUNT(DISTINCT company_code) AS hospital_cnt, COUNT(*) AS record_cnt, "
             "MAX(registration_date) AS latest_registration "
             "FROM t_user_hospital_patient GROUP BY file_code HAVING COUNT(DISTINCT company_code) > 1 "
             "ORDER BY record_cnt DESC LIMIT 500"),
     "fields": [_dim("file_code", "患者档案号"), _dim("hospital_cnt", "建档机构数"),
                _dim("record_cnt", "档案条数"), _dim("latest_registration", "最近登记日期")]},
    {"name": "COP·档案案例数不一致患者明细", "obj": "patient", "paginable": True,
     "desc": "巡检明细：档案表冗余列 case_count 与案例表按 patient_file_code 实算不一致的患者",
     "sql": ("SELECT p.id AS patient_id, p.file_code AS file_code, p.company_code AS company_code, "
             "p.case_count AS stored_case_count, COALESCE(x.c, 0) AS actual_case_count "
             "FROM t_user_hospital_patient p "
             "LEFT JOIN (SELECT patient_file_code AS pfc, COUNT(*) AS c FROM t_case_records "
             "WHERE del_flag = 0 GROUP BY patient_file_code) x ON x.pfc = p.file_code "
             "WHERE IFNULL(p.case_count, 0) <> COALESCE(x.c, 0) ORDER BY p.id LIMIT 500"),
     "fields": [_dim("patient_id", "档案ID"), _dim("file_code", "患者档案号"),
                _dim("company_code", "建档机构"), _dim("stored_case_count", "档案冗余案例数"),
                _dim("actual_case_count", "案例表实算数")]},
]

# 巡检 CUSTOM 指标（bok=''，语义层不编译）formula 引用的列，preflight 用它做实存核对。
CUSTOM_METRIC_COLUMNS: dict[str, set[str]] = {
    "t_user_hospital_patient": {"file_code", "company_code", "case_count"},
    "t_case_records": {"patient_file_code", "del_flag"},
}

# ── 看板（17 图：9 KPI 卡 + 5 分布 + 3 趋势；config/position 形态=前端 DashboardChart 实测）
DASHBOARD_NAME = "案例·订单·患者 运营总览"
DASHBOARD_CHARTS: list[dict] = [
    {"name": "KPI·案例数量", "type": "text_display", "ds": "COP·案例运营KPI",
     "config": {"yCol": "case_count"}, "pos": (0, 0, 384, 168)},
    {"name": "KPI·已完成案例数", "type": "text_display", "ds": "COP·案例运营KPI",
     "config": {"yCol": "completed_cases"}, "pos": (384, 0, 384, 168)},
    {"name": "KPI·待处理案例数", "type": "text_display", "ds": "COP·案例运营KPI",
     "config": {"yCol": "pending_cases"}, "pos": (768, 0, 384, 168)},
    {"name": "KPI·订单数", "type": "text_display", "ds": "COP·订单运营KPI",
     "config": {"yCol": "order_count"}, "pos": (0, 184, 384, 168)},
    {"name": "KPI·已完成订单数", "type": "text_display", "ds": "COP·订单运营KPI",
     "config": {"yCol": "completed_orders"}, "pos": (384, 184, 384, 168)},
    {"name": "KPI·平均发货时效小时", "type": "text_display", "ds": "COP·订单运营KPI",
     "config": {"yCol": "avg_ship_hours"}, "pos": (768, 184, 384, 168)},
    {"name": "KPI·患者人数", "type": "text_display", "ds": "COP·患者运营KPI",
     "config": {"yCol": "patient_count"}, "pos": (0, 368, 384, 168)},
    {"name": "KPI·案例总存储GB", "type": "text_display", "ds": "COP·案例运营KPI",
     "config": {"yCol": "storage_gb"}, "pos": (384, 368, 384, 168)},
    {"name": "KPI·已发货未登记物流单号订单数", "type": "text_display", "ds": "COP·订单运营KPI",
     "config": {"yCol": "missing_track_orders"}, "pos": (768, 368, 384, 168)},
    {"name": "案例状态分布", "type": "pie", "ds": "COP·案例状态分布",
     "config": {"xCol": "status_label", "yCol": "cnt"}, "pos": (0, 552, 768, 384)},
    {"name": "案例类型分布", "type": "bar", "ds": "COP·案例类型分布",
     "config": {"xCol": "type_label", "yCol": "cnt"}, "pos": (784, 552, 768, 384)},
    {"name": "订单状态分布", "type": "bar", "ds": "COP·订单状态分布",
     "config": {"xCol": "status_label", "yCol": "cnt"}, "pos": (0, 952, 768, 384)},
    {"name": "患者性别分布", "type": "pie", "ds": "COP·患者性别分布",
     "config": {"xCol": "sex_label", "yCol": "cnt"}, "pos": (784, 952, 768, 384)},
    {"name": "患者省份TOP10", "type": "bar", "ds": "COP·患者省份TOP10",
     "config": {"xCol": "province_label", "yCol": "cnt"}, "pos": (0, 1352, 768, 384)},
    {"name": "近30天案例新增趋势", "type": "line", "ds": "COP·案例新增趋势",
     "config": {"xCol": "stat_date", "yCol": "cnt"}, "pos": (784, 1352, 768, 384)},
    {"name": "近30天订单新增趋势", "type": "line", "ds": "COP·订单新增趋势",
     "config": {"xCol": "stat_date", "yCol": "cnt"}, "pos": (0, 1752, 768, 384)},
    {"name": "患者登记趋势", "type": "line", "ds": "COP·患者登记趋势",
     "config": {"xCol": "stat_date", "yCol": "cnt"}, "pos": (784, 1752, 768, 384)},
]


# ── 执行函数 ────────────────────────────────────────────────────────────────────
def _log(msg: str) -> None:
    print(msg, flush=True)


def _parse_aliases(raw) -> list[str]:
    try:
        al = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else (raw or [])
    except Exception:
        al = []
    if isinstance(al, str):
        al = [al]
    return [str(a).strip() for a in al if str(a).strip()]


def _ids_by_name(names: list[str]) -> dict[str, int]:
    marks = ",".join(["%s"] * len(names))
    rows = execute_query(f"SELECT id, name FROM adh_datasources WHERE name IN ({marks})",
                         tuple(names)) or []
    return {str(r["name"]): int(r["id"]) for r in rows}


def _find_model(name: str, ds_id: int):
    return execute_query(
        "SELECT id, status, json_content, deploy_sites FROM adh_ontology_models "
        "WHERE name = %s AND datasource_id = %s AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
        (name, ds_id), fetchone=True)


def _admin_id() -> int:
    row = execute_query("SELECT id FROM adh_users WHERE username = 'admin' ORDER BY id LIMIT 1",
                        fetchone=True)
    if not row:
        raise RuntimeError("未找到 admin 用户（数据集/看板需要创建身份）")
    return int(row["id"])


# 公式列名提取：SQL 关键字/函数名排除表（覆盖本脚本全部公式与数据集 SQL 用到的词）。
_SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "as", "case", "when", "then",
    "else", "end", "count", "sum", "avg", "min", "max", "distinct", "group", "by", "having", "order",
    "desc", "asc", "limit", "join", "left", "right", "inner", "outer", "on", "timestampdiff", "year",
    "month", "day", "hour", "minute", "curdate", "now", "date_sub", "date_add", "interval", "nullif",
    "concat", "ifnull", "coalesce", "date", "if", "over", "partition",
}


def _sql_columns(expr: str) -> set[str]:
    return {m.lower() for m in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", expr or "")
            if m.lower() not in _SQL_KEYWORDS}


def _required_columns() -> dict[str, set[str]]:
    req: dict[str, set[str]] = {t: set() for t in TABLES.values()}
    for spec in METRIC_UPDATES + METRIC_CREATES:
        if spec["bok"]:
            req[TABLES[spec["bok"]]] |= _sql_columns(spec["formula"])
        if spec.get("col"):
            req[TABLES[spec["bok"]]].add(spec["col"])
    for table, cols in CUSTOM_METRIC_COLUMNS.items():
        req[table] |= cols
    for spec in DIMENSION_UPDATES + DIMENSION_CREATES:
        req[TABLES[spec["bok"]]].add(spec["col"])
    return req


def _registered_columns(ds_ids: dict[str, int]) -> set[tuple[str, str, str]]:
    ids, tables = list(ds_ids.values()), list(TABLES.values())
    marks, tmarks = ",".join(["%s"] * len(ids)), ",".join(["%s"] * len(tables))
    rows = execute_query(
        f"SELECT datasource_id, table_name, column_name FROM adh_column_metadata "
        f"WHERE is_active = 1 AND datasource_id IN ({marks}) AND table_name IN ({tmarks})",
        tuple(ids + tables)) or []
    name_by_id = {v: k for k, v in ds_ids.items()}
    return {(name_by_id[int(r["datasource_id"])], str(r["table_name"]), str(r["column_name"]))
            for r in rows if int(r["datasource_id"]) in name_by_id}


def check_column_gaps(ds_ids: dict[str, int]) -> tuple[list[str], list[str]]:
    """列实存核对。返回 (阻断问题, 可补登缺失=file_clean_time)。"""
    have = _registered_columns(ds_ids)
    problems, fixable = [], []
    for table, cols in sorted(_required_columns().items()):
        for col in sorted(cols):
            miss = [d for d in AVAILABLE_DS if (d, table, col) not in have]
            if not miss:
                continue
            if table == TABLES["case"] and col == "file_clean_time":
                fixable.append(f"{table}.{col} 缺站 {miss}")
            else:
                problems.append(f"物理列未登记: {table}.{col} 缺站 {miss}")
    return problems, fixable


def _occupancy(table: str, update_names: set[str], retire_names: set[str],
               create_names: set[str] = frozenset(), update_ens: set[str] = frozenset(),
               alias_fix_names: set[str] = frozenset()) -> dict[str, int]:
    """现有字典行的检索词占用表。豁免：停用让位行、本脚本将 update 的行（name 或 name_en 命中）、
    create 幂等命中行（name 已收敛到自己）；停用行(is_active=0)与将被 EXTRA_ALIAS_FIXES
    清理别名的域外行，其别名不占检索词。"""
    rows = execute_query(f"SELECT id, name, name_en, aliases, is_active FROM {table}") or []
    occ: dict[str, int] = {}
    for r in rows:
        nm, en = str(r["name"] or ""), str(r["name_en"] or "")
        if nm in retire_names or nm in update_names or nm in create_names or en in update_ens:
            continue
        words = {nm, en}
        if int(r["is_active"] or 0) and nm not in alias_fix_names:
            words |= set(_parse_aliases(r["aliases"]))
        for w in words - {""}:
            occ.setdefault(w, int(r["id"]))
    return occ


def check_dict_conflicts() -> list[str]:
    """指标/维度新行与对齐行的全部检索词（name/name_en/别名）：跨行重复或撞现有占用 → 拒。"""
    problems: list[str] = []
    for table, updates, creates, retires in (
            ("adh_metrics", METRIC_UPDATES, METRIC_CREATES, METRIC_RETIRES),
            ("adh_dimensions", DIMENSION_UPDATES, DIMENSION_CREATES, DIMENSION_RETIRES)):
        occ = _occupancy(table, {s["name"] for s in updates}, {s["name"] for s in retires},
                         {s["name"] for s in creates},
                         {s.get("en") for s in updates if s.get("en")},
                         {s["name"] for s in EXTRA_ALIAS_FIXES if s["table"] == table})
        owners: dict[str, list[str]] = {}
        for spec in creates + updates:
            words = ({spec["name"], spec.get("en") or ""} | set(spec.get("aliases") or [])) - {""}
            for w in words:
                owners.setdefault(w, []).append(spec["name"])
        for w, rows_ in sorted(owners.items()):
            if len(set(rows_)) > 1:
                problems.append(f"{table} 词 {w!r} 跨行重复: {rows_}")
            if w in occ:
                problems.append(f"{table} 行「{rows_[0]}」的词 {w!r} 与现有 id={occ[w]} 冲突")
    return problems


def preflight(ds_ids: dict[str, int]) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    missing_ds = [n for n in AVAILABLE_DS if n not in ds_ids]
    if missing_ds:
        problems.append(f"数据源未注册: {missing_ds}")
    sites = {str(r["site_code"]) for r in
             execute_query("SELECT site_code FROM adh_sites WHERE is_active = 1") or []}
    lack = [c for c, _d in SITE_DS if c not in sites]
    if lack:
        problems.append(f"站点清单缺失(adh_sites): {lack}")
    problems += check_dict_conflicts()
    col_problems, fixable = check_column_gaps(ds_ids)
    problems += col_problems
    return problems, fixable


def ensure_column_metadata(apply: bool) -> list[str]:
    """幂等补登 t_case_records.file_clean_time 列元数据（5 站逐一核对）。返回剩余缺失站。"""
    ds_ids = _ids_by_name(AVAILABLE_DS)
    missing = []
    for ds_name in AVAILABLE_DS:
        row = execute_query(
            "SELECT id FROM adh_column_metadata WHERE datasource_id = %s AND table_name = %s "
            "AND column_name = 'file_clean_time' AND is_active = 1",
            (ds_ids[ds_name], TABLES["case"]), fetchone=True)
        if not row:
            missing.append(ds_name)
    if not missing:
        _log("[colmeta] file_clean_time 五站均已登记")
        return []
    _log(f"[colmeta] {'补登' if apply else 'dry-run 待补登'} file_clean_time: {missing}")
    if not apply:
        return missing
    base = int(time.time() * 1000000)
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for i, ds_name in enumerate(missing):
                cur.execute(
                    "INSERT INTO adh_column_metadata (id, datasource_id, table_name, column_name, "
                    "data_type, column_comment, business_desc, keywords, is_key, is_nullable, "
                    "is_active, sync_time) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW())",
                    (base + i + 500000, ds_ids[ds_name], TABLES["case"], "file_clean_time",
                     "datetime", "文件清理目标时间(清理任务写入)", "到期清理;文件回收", "file_clean_time",
                     "false", "true"))
        conn.commit()
    _log(f"[colmeta] 已补登 {len(missing)} 站")
    return []


# doc 就地修改口径（Excel Sheet①/④）：PII 属性移除、悬空属性移除、属性名对齐。
_PII_COLUMNS = {
    "patient": {"real_name", "id_no", "phone", "email", "address_detail", "avatar",
                "username", "nick_name", "longitude", "latitude"},
    "case": {"patient_phone", "patient_user_name"},
}
_DROPPED = {"case": {"case_step"}}        # 「案例步骤」列不存在（Excel ⛔不纳入）
_RENAMED = {
    "case": {"collection_status": "数据收集状态"},
    "order": {"ship_date": "发货日期", "expected_arrival_date": "约定到达日期",
              "req_delivery_date": "要求交付日期"},
    "patient": {},
}
_NOTE_MARK = "【域口径】"


def _bare(col) -> str:
    """doc 属性 column 可能带表前缀(如 t_user_hospital_patient.phone)，取裸列名做匹配。"""
    return str(col or "").split(".")[-1].strip()


def ensure_doc(model_row: dict, apply: bool) -> None:
    """联耀本体三对象就地修（幂等达标即跳过）→ save_draft → activate（唯一合规路径）。"""
    model_id = int(model_row["id"])
    doc = json.loads(model_row["json_content"] or "{}")
    changes: list[str] = []
    # save_draft 破坏性覆盖护栏按属性名判定丢失：本脚本点名移除/改名的旧属性名需显式声明
    declared_removals: dict[str, list[str]] = {}
    dentist_ok = bool(execute_query(
        "SELECT id FROM adh_column_metadata WHERE table_name = %s AND column_name = 'dentist_name' "
        "AND is_active = 1 LIMIT 1", (TABLES["case"],), fetchone=True))
    for key in ("case", "order", "patient"):
        obj = next((o for o in doc.get("objects") or [] if o.get("key") == key), None)
        if obj is None:
            _log(f"[doc][FAIL] 模型缺对象 {key}（联耀本体应含 case/order/patient）")
            changes.append(f"missing:{key}")
            continue
        props = obj.get("properties") or []
        drop = _PII_COLUMNS.get(key, set()) | _DROPPED.get(key, set())
        dropped = [str(p.get("column")) for p in props if _bare(p.get("column")) in drop]
        if dropped:
            for p in props:
                nm = str(p.get("name") or "").strip()
                if _bare(p.get("column")) in drop and nm:
                    declared_removals.setdefault(key, []).append(nm)
            props = [p for p in props if _bare(p.get("column")) not in drop]
            changes.append(f"{key}: 移除属性 {dropped}")
        for p in props:
            col = _bare(p.get("column"))
            want = _RENAMED.get(key, {}).get(col)
            if want and p.get("name") != want:
                old_nm = str(p.get("name") or "").strip()
                changes.append(f"{key}: 属性 {col} 名称 {p.get('name')!r}→「{want}」")
                p["name"] = want
                if old_nm:
                    declared_removals.setdefault(key, []).append(old_nm)
        if key == "case" and dentist_ok \
                and not any(_bare(p.get("column")) == "dentist_name" for p in props):
            props.append({"column": "t_case_records.dentist_name", "name": "报告牙医", "type": "string",
                          "is_key": False, "enum": [],
                          "description": "案例报告牙医姓名（与操作账号 doctor_user_name 分列，来源不同）"})
            changes.append("case: 补属性 dentist_name「报告牙医」")
        if key == "case" and not any(_bare(p.get("column")) == "file_clean_time" for p in props):
            props.append({"column": "t_case_records.file_clean_time", "name": "文件清理时间",
                          "type": "datetime", "is_key": False, "enum": [],
                          "description": "文件清理目标时间(清理任务写入)；存储成本治理与待OSS清理口径用"})
            changes.append("case: 补属性 file_clean_time「文件清理时间」")
        obj["properties"] = props
        want_desc = _NOTE_MARK + OBJECT_NOTES[key]
        desc_now = str(obj.get("description") or "")
        if want_desc not in desc_now:
            base = desc_now.split(_NOTE_MARK)[0].rstrip()
            obj["description"] = (base + "。" if base else "") + want_desc
            changes.append(f"{key}: 对象描述补口径")
    if "missing:" in "".join(changes):
        _log("[doc][FATAL] 对象缺失，终止（不写 doc）")
        raise RuntimeError("联耀本体缺 case/order/patient 对象")
    if not changes:
        _log("[doc] 三对象 doc 已达标（PII 已移除/名称对齐/口径就位），跳过")
        return
    _log(f"[doc] {'待修改' if not apply else '已修改'}: {changes}")
    if not apply:
        return
    if declared_removals:
        # 顶层声明键，save_draft 写入前剥离，不进 canonical 正文
        doc["_declared_removals"] = declared_removals
    saved = ontology_service.save_draft(model_id, json.dumps(doc, ensure_ascii=False))
    for w in saved.get("validation_warnings") or []:
        _log(f"[doc][warn] {w}")
    ontology_service.activate(model_id)
    _log(f"[doc] save_draft+activate 完成（模型 {model_id}，级联展开/字典联动/图谱）")


def _metric_retire(apply: bool, out: list[str]) -> None:
    for spec in METRIC_RETIRES:
        row = execute_query("SELECT id, description FROM adh_metrics WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            out.append(f"metric:{spec['name']}(未命中,跳过)")
            continue
        desc = str(row["description"] or "")
        note = f"[已停用] 被「{spec['alt']}」替代：{spec['reason']}"
        payload: dict = {"is_active": 0}
        if "[已停用]" not in desc:
            payload["description"] = f"{note}｜原描述：{desc}" if desc else note
        if apply:
            metrics_service.update_metric(int(row["id"]), payload)
        out.append(f"metric:{spec['name']}(id={row['id']})")


def _dimension_retire(apply: bool, out: list[str]) -> None:
    for spec in DIMENSION_RETIRES:
        row = execute_query("SELECT id, description FROM adh_dimensions WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            out.append(f"dimension:{spec['name']}(未命中,跳过)")
            continue
        desc = str(row["description"] or "")
        note = f"[已停用] 被「{spec['alt']}」替代：{spec['reason']}"
        payload: dict = {"is_active": 0}
        if "[已停用]" not in desc:
            payload["description"] = f"{note}｜原描述：{desc}" if desc else note
        if apply:
            metrics_service.update_dimension(int(row["id"]), payload)
        out.append(f"dimension:{spec['name']}(id={row['id']})")


def ensure_dicts(apply: bool) -> None:
    """字典收敛：先停用（释放口径叙述），再对齐现有行，再建新行；全部幂等 upsert。"""
    retired: list[str] = []
    _metric_retire(apply, retired)
    _dimension_retire(apply, retired)
    _log(f"[dict] {'dry-run 待停用' if not apply else '已停用'}: {retired}")

    fixed: list[str] = []
    for spec in EXTRA_ALIAS_FIXES:
        svc = (metrics_service.update_metric if spec["table"] == "adh_metrics"
               else metrics_service.update_dimension)
        row = execute_query(f"SELECT id, aliases FROM {spec['table']} WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            fixed.append(f"{spec['name']}(未命中,跳过)")
            continue
        if set(_parse_aliases(row["aliases"])) == set(spec["aliases"]):
            fixed.append(f"{spec['name']}(已达标)")
            continue
        if apply:
            svc(int(row["id"]), {"aliases": spec["aliases"]})
        fixed.append(f"{spec['name']}(清别名)")
    _log(f"[dict] {'dry-run 待清' if not apply else '已清'} 域外脏别名: {fixed}")

    created, updated = [], []
    for spec in METRIC_UPDATES:
        payload = {"display_name": spec["name"], "calculation_type": spec["agg"],
                   "expression": spec["formula"], "unit": spec["unit"], "description": spec["desc"],
                   "aliases": spec["aliases"], "category": spec["cat"],
                   "bound_object_key": spec["bok"], "target_table": TABLES[spec["bok"]]}
        row = execute_query("SELECT id FROM adh_metrics WHERE name = %s", (spec["name"],), fetchone=True)
        if not row and spec.get("en"):
            # en 兜底：现有行可能仍是英文句柄名(如「锁定案例数」en=locked_cases)，update 会改名对齐
            row = execute_query("SELECT id FROM adh_metrics WHERE name_en = %s",
                                (spec["en"],), fetchone=True)
        if row:
            if apply:
                metrics_service.update_metric(int(row["id"]), payload)
            updated.append(f"metric:{spec['name']}")
        else:  # 现状实测在库；若缺失则补建（en 非空可安全 create）
            payload["name"] = spec["en"]
            if apply:
                metrics_service.create_metric(payload)
            created.append(f"metric:{spec['name']}(缺失,转create)")
    for spec in METRIC_CREATES:
        payload = {"display_name": spec["name"], "name": spec.get("en") or "",
                   "calculation_type": spec["agg"], "expression": spec["formula"],
                   "unit": spec["unit"], "description": spec["desc"], "aliases": spec["aliases"],
                   "category": spec["cat"], "bound_object_key": spec["bok"],
                   "target_table": TABLES.get(spec["bok"]) or "t_user_hospital_patient"}
        row = execute_query("SELECT id FROM adh_metrics WHERE name = %s", (spec["name"],), fetchone=True)
        if row:
            if apply:
                metrics_service.update_metric(int(row["id"]),
                                              {k: v for k, v in payload.items() if k != "name"})
            updated.append(f"metric:{spec['name']}")
            continue
        if not apply:
            created.append(f"metric:{spec['name']}")
            continue
        try:
            res = metrics_service.create_metric(payload)
            created.append(f"metric:{spec['name']}(id={res['id']})")
        except Exception as exc:  # noqa: BLE001 — 校验拒绝也显式报告（verify 会报缺项）
            _log(f"[dict][warn] 指标 create 拒绝「{spec['name']}」: {type(exc).__name__}: {str(exc)[:180]}")
    for spec in DIMENSION_UPDATES:
        payload = {"name_en": spec.get("en") or None, "category": spec["cat"],
                   "target_table": TABLES[spec["bok"]], "target_column": spec["col"],
                   "description": spec["desc"], "bound_object_key": spec["bok"],
                   "aliases": spec["aliases"],
                   "value_labels": json.dumps(spec["labels"], ensure_ascii=False)
                   if spec.get("labels") else None, "certified": 0}
        row = execute_query("SELECT id FROM adh_dimensions WHERE name = %s", (spec["name"],), fetchone=True)
        if row:
            if apply:
                metrics_service.update_dimension(int(row["id"]), payload)
            updated.append(f"dimension:{spec['name']}")
        else:
            created.append(f"dimension:{spec['name']}(缺失,转create)")
            if apply:
                metrics_service.create_dimension(
                    {"display_name": spec["name"], **payload, "target_table": TABLES[spec["bok"]]})
    for spec in DIMENSION_CREATES:
        payload = {"display_name": spec["name"], "name_en": spec.get("en") or "",
                   "target_table": TABLES[spec["bok"]], "target_column": spec["col"],
                   "category": spec["cat"], "description": spec["desc"],
                   "bound_object_key": spec["bok"], "aliases": spec["aliases"],
                   "value_labels": json.dumps(spec["labels"], ensure_ascii=False)
                   if spec.get("labels") else None, "certified": 0}
        row = execute_query("SELECT id FROM adh_dimensions WHERE name = %s", (spec["name"],), fetchone=True)
        if row:
            if apply:
                metrics_service.update_dimension(int(row["id"]), payload)
            updated.append(f"dimension:{spec['name']}")
            continue
        if not apply:
            created.append(f"dimension:{spec['name']}")
            continue
        try:
            res = metrics_service.create_dimension(payload)
            created.append(f"dimension:{spec['name']}(id={res['id']})")
        except Exception as exc:  # noqa: BLE001
            _log(f"[dict][warn] 维度 create 拒绝「{spec['name']}」: {type(exc).__name__}: {str(exc)[:180]}")
    _log(f"[dict] {'dry-run 待建' if not apply else '已建'}: {created or '（无新建）'}")
    if updated:
        _log(f"[dict] 已对齐现有行: {updated}")


def _admin_identity(admin: int) -> dict:
    """服务层 identity 形状：dataset_service/chart_service 要求 dict（role 判定 + user_id 写 owner）。"""
    return {"user_id": int(admin), "role": "admin", "username": "admin"}


def ensure_datasets(apply: bool, ds_ids: dict[str, int], admin: int) -> None:
    ident = _admin_identity(admin)
    created, updated = [], []
    for spec in DATASETS:
        req = {"name": spec["name"], "description": spec["desc"], "object_key": spec["obj"],
               "sql_query": spec["sql"], "datasource_id": ds_ids["prd-cn"],
               "available_datasources": list(AVAILABLE_DS), "field_config": spec["fields"],
               "paginable": bool(spec.get("paginable"))}
        row = execute_query("SELECT id FROM adh_datasets WHERE name = %s", (spec["name"],), fetchone=True)
        if row:
            if apply:
                dataset_service.update_dataset(int(row["id"]), req, ident)
            updated.append(f"dataset:{spec['name']}")
        else:
            if apply:
                res = dataset_service.create_dataset(req, ident)
                new_id = res.get("id") if isinstance(res, dict) else None
                created.append(f"dataset:{spec['name']}(id={new_id})")
            else:
                created.append(f"dataset:{spec['name']}")
    _log(f"[datasets] {'dry-run 待建' if not apply else '已建'}: {created or '（无新建）'}")
    if updated:
        _log(f"[datasets] 已对齐现有行: {updated}")


def ensure_dashboard(apply: bool, admin: int) -> None:
    dash = execute_query("SELECT id FROM adh_dashboards WHERE name = %s", (DASHBOARD_NAME,), fetchone=True)
    if not dash and not apply:
        _log(f"[dash] dry-run：将建看板「{DASHBOARD_NAME}」(status=enabled) + {len(DASHBOARD_CHARTS)} 图")
        return
    if dash:
        dash_id = int(dash["id"])
        _log(f"[dash] 看板已存在(id={dash_id})，跳过建板")
    else:
        DashboardService().create_dashboard(
            {"name": DASHBOARD_NAME,
             "description": "案例·订单·患者三域运营总览（口径=指标字典；数据集五站可用，看板可切站点）",
             "layout": None, "filters": [], "params": {},
             "tags": ["案例", "订单", "患者", "运营", CREATED_BY], "status": "enabled"}, admin)
        dash = execute_query("SELECT id FROM adh_dashboards WHERE name = %s", (DASHBOARD_NAME,), fetchone=True)
        dash_id = int(dash["id"])
        _log(f"[dash] 看板已建(id={dash_id})")
    dataset_ids = {str(r["name"]): int(r["id"]) for r in
                   execute_query("SELECT id, name FROM adh_datasets WHERE name LIKE 'COP·%'") or []}
    existing = {str(r["name"]) for r in
                execute_query("SELECT name FROM adh_charts WHERE dashboard_id = %s", (dash_id,)) or []}
    done, skipped = [], []
    for spec in DASHBOARD_CHARTS:
        if spec["name"] in existing:
            skipped.append(spec["name"])
            continue
        x, y, w, h = spec["pos"]
        if apply:
            ChartService().create_chart(dash_id, {
                "name": spec["name"], "chart_type": spec["type"],
                "config": json.dumps(spec["config"], ensure_ascii=False),
                "position": json.dumps({"x": x, "y": y, "w": w, "h": h}),
                "source_type": "dataset", "source_id": dataset_ids[spec["ds"]],
                "query_source": "dataset"}, _admin_identity(admin))
        done.append(spec["name"])
    _log(f"[dash] 图表 {'dry-run 待建' if not apply else '已建'} {len(done)} 个: {done}")
    if skipped:
        _log(f"[dash] 图表已存在跳过 {len(skipped)} 个: {skipped}")


def verify(ds_ids: dict[str, int]) -> int:
    """落库核验：模型/门禁/字典/数据集/看板逐项核对，缺项显式报出。"""
    problems: list[str] = []
    model = _find_model(MODEL_NAME, ds_ids["prd-cn"])
    if not model:
        problems.append("模型缺失: 联耀业务本体")
    else:
        if model.get("status") != "active":
            problems.append(f"模型状态非 active: {model.get('status')}")
        try:
            doc = json.loads(model["json_content"] or "{}")
            orphan = ontology_service.find_orphan_dict_bindings(doc)
            if orphan:
                problems.append(f"字典孤儿引用: {orphan}")
        except Exception as exc:  # noqa: BLE001 — 核验失败也要显式报
            problems.append(f"门禁检查异常: {type(exc).__name__}: {exc}")
        issues = ontology_service.find_deploy_site_issues(int(model["id"]))
        if issues:
            problems.append(f"适用站点: {issues}")
        doc = json.loads(model["json_content"] or "{}")
        for o in doc.get("objects") or []:
            drop = _PII_COLUMNS.get(str(o.get("key")), set())
            hit = sorted({_bare(p.get("column")) for p in o.get("properties") or []} & drop)
            if hit:
                problems.append(f"对象 {o.get('key')} 仍含 PII 属性: {hit}")
    for spec in METRIC_UPDATES + METRIC_CREATES:
        row = execute_query(
            "SELECT is_active, target_table FROM adh_metrics WHERE name = %s",
            (spec["name"],), fetchone=True)
        if not row:
            problems.append(f"指标缺失: {spec['name']}")
        elif not int(row["is_active"] or 0):
            problems.append(f"指标未激活: {spec['name']}")
        elif spec["bok"] and str(row["target_table"] or "") != TABLES[spec["bok"]]:
            problems.append(f"指标 {spec['name']} target_table={row['target_table']!r} ≠ {TABLES[spec['bok']]}")
    for spec in METRIC_RETIRES:
        row = execute_query("SELECT is_active FROM adh_metrics WHERE name = %s",
                            (spec["name"],), fetchone=True)
        # 行不存在 = 已被 create 复用改名收敛(如 pending_cases→待处理案例数)，视为达标
        if row and int(row["is_active"] or 0):
            problems.append(f"退役指标未停用: {spec['name']}")
    for spec in DIMENSION_UPDATES + DIMENSION_CREATES:
        row = execute_query(
            "SELECT is_active, target_table FROM adh_dimensions WHERE name = %s",
            (spec["name"],), fetchone=True)
        if not row:
            problems.append(f"维度缺失: {spec['name']}")
        elif not int(row["is_active"] or 0):
            problems.append(f"维度未激活: {spec['name']}")
        elif str(row["target_table"] or "") != TABLES[spec["bok"]]:
            problems.append(f"维度 {spec['name']} target_table={row['target_table']!r}")
    for spec in DIMENSION_RETIRES:
        row = execute_query("SELECT is_active FROM adh_dimensions WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if row and int(row["is_active"] or 0):
            problems.append(f"退役维度未停用: {spec['name']}")
    for spec in EXTRA_ALIAS_FIXES:
        row = execute_query(f"SELECT aliases FROM {spec['table']} WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if row and set(_parse_aliases(row["aliases"])) != set(spec["aliases"]):
            problems.append(f"域外脏别名未清: {spec['name']}({spec['table']})")
    for spec in DATASETS:
        row = execute_query("SELECT status, available_datasources FROM adh_datasets WHERE name = %s",
                            (spec["name"],), fetchone=True)
        if not row:
            problems.append(f"数据集缺失: {spec['name']}")
            continue
        if str(row["status"] or "") != "active":
            problems.append(f"数据集非 active: {spec['name']}({row['status']})")
        try:
            avail = json.loads(row["available_datasources"] or "[]")
        except Exception:
            avail = []
        if sorted(str(a) for a in avail) != sorted(AVAILABLE_DS):
            problems.append(f"数据集 {spec['name']} available_datasources={avail} ≠ 5 站")
    dash = execute_query("SELECT id FROM adh_dashboards WHERE name = %s", (DASHBOARD_NAME,), fetchone=True)
    if not dash:
        problems.append(f"看板缺失: {DASHBOARD_NAME}")
    else:
        dataset_ids = {str(r["name"]): int(r["id"]) for r in
                       execute_query("SELECT id, name FROM adh_datasets WHERE name LIKE 'COP·%'") or []}
        charts = execute_query("SELECT name, source_type, source_id FROM adh_charts WHERE dashboard_id = %s",
                               (int(dash["id"]),)) or []
        by_name = {str(c["name"]): c for c in charts}
        for spec in DASHBOARD_CHARTS:
            c = by_name.get(spec["name"])
            if not c:
                problems.append(f"看板图缺失: {spec['name']}")
            elif str(c["source_type"]) != "dataset":
                problems.append(f"看板图 {spec['name']} source_type={c['source_type']!r} ≠ dataset")
            elif dataset_ids.get(spec["ds"]) != (int(c["source_id"]) if c["source_id"] is not None else None):
                problems.append(f"看板图 {spec['name']} source_id 未命中数据集「{spec['ds']}」")
    for p in problems:
        _log(f"[verify][FAIL] {p}")
    if not problems:
        _log(f"[verify] 全绿: 模型 active/门禁空/PII 已移除；字典对齐 {len(METRIC_UPDATES)}+{len(DIMENSION_UPDATES)} 行、"
             f"新建 {len(METRIC_CREATES)}+{len(DIMENSION_CREATES)} 行、停用 {len(METRIC_RETIRES)}+{len(DIMENSION_RETIRES)} 行；"
             f"数据集 {len(DATASETS)} 个 5 站；看板 {len(DASHBOARD_CHARTS)} 图全部命中数据集")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建「案例·订单·患者」域：本体三对象/字典收敛/数据集/看板")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    args = ap.parse_args()
    apply = bool(args.apply)
    _log(f"== {'执行' if apply else 'dry-run（只报计划，不写库）'} ==")
    ds_ids = _ids_by_name(AVAILABLE_DS)
    problems, fixable = preflight(ds_ids)
    for p in problems:
        _log(f"[preflight][FAIL] {p}")
    if problems:
        _log("[FATAL] 前置检查未过，未写入任何内容")
        return 2
    if fixable:
        _log(f"[preflight] file_clean_time 缺登记（{'将补登' if apply else 'dry-run 待补登'}）: {fixable}")
    remain = ensure_column_metadata(apply)
    if remain and apply:  # dry-run 时 remain 只是待补登清单，不阻断计划展示
        _log(f"[FATAL] 列元数据补登后仍缺: {remain}")
        return 2
    model_row = _find_model(MODEL_NAME, ds_ids["prd-cn"])
    if not model_row:
        _log(f"[FATAL] active 模型「{MODEL_NAME}」不存在（主源 prd-cn）")
        return 2
    ensure_doc(model_row, apply)
    ensure_dicts(apply)
    admin = _admin_id()
    ensure_datasets(apply, ds_ids, admin)
    ensure_dashboard(apply, admin)
    if apply:
        return verify(ds_ids)
    _log("== dry-run 结束：以上为落地计划，--apply 落库 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

