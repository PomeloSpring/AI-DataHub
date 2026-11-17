#!/usr/bin/env python
"""补齐「联耀业务本体」：对象属性（物理列）、维度、指标。

背景（本脚本存在的原因）
------------------------
联耀业务本体的 18 个对象普遍只登记了少量属性（order 9/56、equipment 16/72、
isv_integration 0 个可用物理列），指标与维度字典也缺一大片。建模页此前既不能新增
属性也不能删除，只能改 JSON 正文 —— 于是"缺列"长期挂着没人补。

本脚本按**物理列元数据**（`adh_column_metadata` 的列名/类型/注释）+ 业务口径
（cloud-platform-data-wiki：枚举、指标公式、维度语义）把缺口补齐，全部走
**canonical doc 唯一写入口** `ontology_service.save_draft`：

1. 属性：为每个对象的 primary_table 补齐未登记的实存列（业务名取自列注释，
   技术列 create_by/update_by/经纬度/测试标记等不登记），枚举从列注释解析；
   存量 `{key, display_name, data_type}` 形状的属性归一成 `{column, name, type}`；
2. 维度：doc 顶层 `dimensions[]`，由 `absorb_doc_semantic_assets` 收编进
   `adh_dimensions`（target_table 由部署绑定层派生、列按实存裁决）；
3. 指标：doc 顶层 `metrics[]`，同上收编进 `adh_metrics`（公式列必须落在用归属对象
   已登记的物理列里，错列会被字典写入口拒绝并回报）。

口径纪律
--------
- 幂等：已登记的属性/维度/指标一律跳过，重复执行不产生重复行；
- 不静默：被跳过的列（没注释）、被拒的条目（字典冲突/列不存在）全部打印出来；
- 不绕过级联：写的是 doc，展开对象/部署绑定/图谱由 save_draft 同批完成；
- 枚举以物理列注释 + wiki 为准；两者冲突时按 wiki（现网口径）并在报告里点名。

用法
----
    venv/bin/python backend/scripts/enrich_lianyao_ontology.py            # 只打印计划（dry-run）
    venv/bin/python backend/scripts/enrich_lianyao_ontology.py --apply    # 真正写库（触发级联）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

MODEL_NAME = "联耀业务本体"

# 对象的中文业务名（doc 的 display_name 是英文类名，维度/指标文案不能直接用）。
# 取自各对象 description 与已有字典行，不新造叫法。
OBJECT_LABELS = {
    "user": "用户", "company": "企业", "role": "角色", "department": "部门",
    "patient": "患者档案", "partnership": "合作关系", "dentist": "牙医档案",
    "case": "案例", "case_file": "案例文件", "case_treatment": "治疗方案",
    "case_recycle_bin": "回收站案例", "order": "订单", "equipment": "设备",
    "work_order": "工单", "isv": "服务商", "software_version": "软件版本",
    "ai_conversation": "AI会话", "isv_integration": "集成记录",
}

# 不登记为属性的列。分四类，每类都有明确理由（不是"懒得写"）：
# ① 无列注释：命名只能靠猜（test_flag 在 user/company/case/order 四表注释为空）；
# ② 技术/集成/配置列：进语义层只会被 LLM 误当分析口径（打包任务 id、同步版本号、扩 展 JSON 等）；
# ③ 涉密列：权限快照、租户黑白名单、MD5 签名等，不进 LLM 视野（security-guardrails §7）；
# ④ 语义重复列：can_eidt_username 与 can_edit_username 是同一业务含义的两个列（前者列名拼错、
#    注释同为"是否能借编辑用户名"），登记两条会让 LLM 在同义属性间摇摆，保留拼写正确的那列。
SKIP_COLUMNS = {
    # ① 无注释
    "test_flag",
    # ② 技术/集成/配置
    "request_id", "source_files", "sync_version", "apps_visible", "extension_config",
    "extension_info", "file_sign_info", "notify_url_hook", "ignored_version", "md5",
    # ③ 涉密
    "permission_snapshot", "tenants_black_list", "tenants_white_list",
    # ④ 语义重复 / 注释不足以命名
    "can_eidt_username",   # 与 can_edit_username 同义
    "appmanager",          # 注释只有"域"，且同表 app_code 注释已是"应用域标识"，不硬凑名
}

# 列注释不好用（空/易误解）时的人工业务名（(表, 列) → 业务名）
NAME_OVERRIDES: dict[tuple[str, str], str] = {
    ("t_user_customer", "company_code"): "所属企业编码",
    ("t_user_customer", "user_code"): "用户编码",
    ("t_user_customer", "id_no"): "身份证号",
    ("t_user_customer", "nick_name"): "昵称",
    ("t_user_customer", "wx_name"): "微信昵称",
    ("t_user_company", "dealer_company_code"): "经销商企业编码",
    ("t_user_company", "address_county"): "公司地址（县）",
    ("t_user_company", "address_town"): "公司地址（街道乡镇）",
    ("t_user_company", "memo"): "补充备注",
    ("t_user_hospital_patient", "email"): "邮箱",
    ("t_user_hospital_patient", "out_patient_id"): "外部病人ID",
    ("t_case_records", "ortho_sim_status"): "正畸模拟状态",
    ("t_case_records", "c_version"): "Connect 客户端版本",
    ("t_case_records", "s_version"): "ScanPro 软件版本",
    ("t_case_records", "locked"): "是否锁定",
    ("t_product_equipment", "ccode"): "区域码",
    ("t_product_equipment", "version"): "设备版本号",
    ("t_product_equipment", "version_code"): "发行区域版本",
    ("t_product_equipment", "version_name"): "营销版本名称",
    ("t_product_equipment", "months_after_shipment"): "发货后保修月数",
    ("t_product_equipment", "months_after_activation"): "激活后保修月数",
    # 注释单字（"镇"）或错字（纬度的"维度"）时给可读业务名；其余列直接用列注释
    ("t_user_customer", "town"): "所属乡镇",
    ("t_user_hospital_patient", "town"): "所属乡镇",
    ("t_user_customer", "latitude"): "地址纬度",
    ("t_user_company", "latitude"): "公司地址纬度",
    ("t_user_hospital_patient", "latitude"): "地址纬度",
    ("t_case_files", "latitude"): "纬度",
    ("t_user_company", "logo_url"): "Logo 地址",
    ("t_integration_record", "integration_status"): "集成状态",
    ("t_integration_record", "integration_record_code"): "集成记录编码",
    ("t_product_work_order", "received_by"): "接单工程师",
    ("t_product_work_order", "issue_type_title"): "问题分类名称",
    ("t_product_equipment", "receiver_country"): "收货国家",
    ("t_product_equipment", "compatible_with_alliedstar"): "能适配联耀 ScanPro",
    ("t_ai_conversation", "del_flag"): "是否删除",
    ("open_isv_info", "user_id"): "用户编码",
    ("open_isv_info", "oss_bucket_name"): "OSS Bucket 名称",
    ("open_isv_info", "appmanager"): "应用订制标识",
    ("t_integration_record", "id"): "集成记录主键",
    ("t_integration_record", "external_code"): "第三方编码",
    ("t_integration_record", "external_name"): "第三方名称",
    ("t_integration_record", "external_info"): "第三方信息",
    ("t_integration_record", "integration_user_code"): "集成用户编码",
    ("t_integration_record", "out_user_code"): "第三方用户编码",
    ("t_product_work_order", "product_type"): "商品类型",
    ("t_order_records", "product_codes"): "产品编码清单",
    ("t_order_records", "comment"): "ScanPro备注",
    ("t_order_records", "f_version"): "固件版本号",
    ("t_order_records", "case_name"): "案例名称",
    ("t_order_records", "case_uid"): "案例客户端唯一ID",
    ("t_order_records", "case_type"): "案例类型",
    ("t_order_records", "scan_time"): "案例扫描时间",
    ("t_order_records", "appid"): "商户AppID",
    ("t_order_records", "appmanager"): "应用域标识",
    ("t_order_records", "parent_appmanager"): "父级域",
    ("t_order_records", "out_patient_id"): "外部病人ID",
    ("t_order_records", "req_delivery_time_period_code"): "要求送达时间段编码",
    ("t_order_records", "clinic_del_flag"): "诊所删除标识",
    ("t_order_records", "lab_del_flag"): "技工所删除标识",
    ("t_user_customer", "can_edit_username"): "是否可编辑用户名",
    ("t_user_company", "isv_appid"): "合作商户AppID",
    ("t_product_equipment", "appid"): "合作商户AppID",
    ("t_case_treatments", "lower_jaw_work_type"): "下颚工作类型",
    ("t_case_treatments", "upper_jaw_work_type"): "上颚工作类型",
    ("t_case_files", "software_app_code"): "软件App编码",
    ("t_order_records", "del_flag"): "是否删除",
    ("t_case_records", "comment"): "ScanPro备注",
    ("t_case_records", "f_version"): "固件版本号",
    ("t_case_records", "appid"): "商户AppID",
    ("t_order_records", "req_delivery_time_period"): "要求送达时间段",
    ("t_software_upgrade_version", "software_type"): "应用类型",
    ("t_software_upgrade_version", "is_force"): "是否强制更新",
    ("t_software_upgrade_version", "region_range"): "适用站点范围",
    ("t_software_upgrade_version", "is_gray"): "是否灰度升级",
    ("t_software_upgrade_version", "is_flaw"): "是否有缺陷版本",
    ("t_software_upgrade_version", "is_publish"): "是否已发布",
    ("t_software_upgrade_version", "is_factory_default"): "是否出厂默认版本",
    ("t_user_role", "data_type"): "数据权限类型",
    ("t_user_role", "system_id"): "所属系统",
    ("t_user_role", "is_default"): "是否默认角色",
    ("t_user_role", "show_sort"): "排序序号",
    ("t_user_company_department", "show_sort"): "排序序号",
    ("t_user_company_department", "is_parent"): "是否父节点部门",
    ("t_user_partner", "out_clinic_code"): "第三方诊所编码",
    ("t_user_partner", "request_company_type"): "发起方类型",
    ("t_user_hospital_patient", "user_code"): "关联患者账号编码",
    ("t_user_hospital_patient", "patient_uid"): "客户端患者唯一ID",
    ("t_ai_conversation", "user_id"): "用户工号",
    ("open_isv_info", "vendor_no"): "厂商编码",
    ("open_isv_info", "sign_type"): "签名类型",
    ("open_isv_info", "endpoint"): "回调域名",
    ("open_isv_info", "endpoint_v2"): "回调域名V2",
    ("open_isv_info", "activate_notify_url"): "设备激活通知地址",
    ("open_isv_info", "callback_trigger_type"): "回调触发类型",
}

# 枚举：以物理列注释 + cloud-platform-data-wiki 为准（与既有维度行保持同一套码值）
ENUMS: dict[tuple[str, str], dict[str, str]] = {
    # 逻辑删除统一口径：0=未删除、1=已删除（与既有指标公式 del_flag = 0 同源）
    ("t_user_company", "is_show"): {"0": "不展示", "1": "展示"},
    ("t_user_company", "is_parent"): {"0": "非父节点", "1": "父节点"},
    ("t_user_company", "has_product"): {"0": "无产品", "1": "有产品"},
    ("t_user_company", "lab_certified_type"): {"0": "未认证", "1": "AlliedStar 认证技工所"},
    ("t_user_company", "auto_accept_partnership"): {"0": "不自动接受", "1": "自动接受"},
    ("t_user_customer", "is_primary_account"): {"0": "否", "1": "是"},
    ("t_user_customer", "is_team_leader"): {"0": "否", "1": "是"},
    ("t_user_customer", "is_confirm_email"): {"0": "未确认", "1": "已确认"},
    ("t_user_customer", "can_edit_username"): {"0": "否", "1": "是"},
    ("t_user_customer", "force_change_password"): {"0": "否", "1": "是"},
    ("t_user_hospital_patient", "del_flag"): {"0": "未删除", "1": "已删除"},
    ("t_case_records", "is_notification"): {"0": "否", "1": "是"},
    ("t_case_records", "collection_status"): {
        "0": "未标识", "1": "已标识", "2": "已上传", "3": "已下载"},
    ("t_case_records", "new_ortho_sim_status"): {"0": "没有", "1": "已上传"},
    ("t_case_files", "is_success"): {"0": "否", "1": "是"},
    ("t_case_files", "is_selected"): {"0": "否", "1": "是"},
    ("t_case_treatments", "inlay_core"): {"0": "否", "1": "是"},
    ("t_case_treatments", "is_all_on_x"): {"0": "否", "1": "是"},
    ("t_case_treatments", "scan_pre_op"): {"0": "否", "1": "是"},
    ("t_case_treatments", "mesial_connected"): {"0": "否", "1": "是"},
    ("t_case_treatments", "scan_provisional"): {"0": "否", "1": "是"},
    ("t_case_recycle_bin", "del_flag"): {"0": "未删除", "1": "已删除"},
    ("t_order_records", "is_lower"): {"0": "否", "1": "是"},
    ("t_order_records", "is_upper"): {"0": "否", "1": "是"},
    ("t_order_records", "lab_del_flag"): {"0": "未删除", "1": "已删除"},
    ("t_order_records", "clinic_del_flag"): {"0": "未删除", "1": "已删除"},
    ("t_order_records", "is_notification"): {"0": "否", "1": "是"},
    ("t_order_records", "is_show_patient_name"): {"0": "否", "1": "是"},
    ("t_order_records", "show_dentist_signature"): {"0": "否", "1": "是"},
    ("t_order_records", "new_ortho_sim_status"): {"0": "没有", "1": "已上传"},
    ("t_product_equipment", "disabled"): {"0": "未禁用", "1": "已禁用"},
    ("t_product_equipment", "is_export"): {"0": "否", "1": "是"},
    ("t_product_equipment", "is_repair"): {"0": "否", "1": "是"},
    ("t_product_equipment", "is_upload"): {"0": "否", "1": "是"},
    ("t_product_equipment", "is_release"): {"0": "否", "1": "是"},
    ("t_product_equipment", "equipment_type"): {"1": "扫描仪"},
    ("t_product_equipment", "is_show_data_flow"): {"0": "否", "1": "是"},
    ("t_product_equipment", "is_premium_function"): {"0": "否", "1": "是"},
    ("t_product_equipment", "is_premium_extension"): {"0": "否", "1": "是"},
    ("t_product_equipment", "compatible_with_alliedstar"): {"0": "不支持", "1": "支持"},
    ("t_product_work_order", "product_type"): {"1": "扫描仪"},
    ("t_product_work_order", "del_flag"): {"0": "未删除", "1": "已删除"},
    ("t_software_upgrade_version", "status"): {"0": "禁用", "1": "启用"},
    ("t_software_upgrade_version", "is_flaw"): {"0": "否", "1": "是"},
    ("t_software_upgrade_version", "is_gray"): {"0": "否", "1": "是"},
    ("t_software_upgrade_version", "is_publish"): {"0": "否", "1": "是"},
    ("t_software_upgrade_version", "is_factory_default"): {"0": "否", "1": "是"},
    ("t_software_upgrade_version", "official_website_scope"): {
        "0": "应用全部官网", "1": "仅国内官网", "2": "仅海外官网", "3": "不应用官网"},
    ("open_isv_info", "sign_type"): {"1": "RSA2", "2": "MD5"},
    ("open_isv_info", "is_select_show"): {"0": "否", "1": "是"},
    ("t_integration_record", "is_downloaded"): {"0": "否", "1": "是"},
    ("t_user_partner", "request_company_type"): {"2": "诊所发起", "3": "技工所发起"},
    ("t_user_role", "is_default"): {"0": "否", "1": "是"},
    ("t_user_company_department", "is_parent"): {"0": "非父节点", "1": "父节点"},
    ("t_user_company_department", "status"): {"0": "停用", "1": "启用"},
}

# 维度（doc 顶层 dimensions[] → adh_dimensions）：分类列 + 枚举标签
# 已存在同名维度会被 absorb 报 conflict（不覆盖），因此只登记缺的
DIMENSIONS: list[dict[str, Any]] = [
    # 用户
    {"bound_object_key": "user", "name": "用户来源渠道", "name_en": "user_appmanager",
     "target_column": "appmanager", "category": "属性", "description": "用户注册/使用所属 App 渠道标识"},
    {"bound_object_key": "user", "name": "注册终端类型", "name_en": "terminal_type",
     "target_column": "terminal_code", "category": "属性", "description": "注册时使用的终端类型"},
    {"bound_object_key": "user", "name": "用户国家", "name_en": "user_country",
     "target_column": "country", "category": "地理", "description": "用户所在国家"},
    {"bound_object_key": "user", "name": "是否部门领导", "name_en": "is_team_leader",
     "target_column": "is_team_leader", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    # 企业
    {"bound_object_key": "company", "name": "技工所认证类型", "name_en": "lab_certified_type",
     "target_column": "lab_certified_type", "category": "属性",
     "value_labels": {"0": "未认证", "1": "AlliedStar 认证技工所"}},
    {"bound_object_key": "company", "name": "是否拥有产品", "name_en": "has_product",
     "target_column": "has_product", "category": "属性",
     "value_labels": {"0": "无产品", "1": "有产品"}},
    {"bound_object_key": "company", "name": "是否父节点企业", "name_en": "is_parent_company",
     "target_column": "is_parent", "category": "属性",
     "value_labels": {"0": "非父节点", "1": "父节点"}},
    # 部门 / 角色
    {"bound_object_key": "department", "name": "部门状态", "name_en": "department_status",
     "target_column": "status", "category": "状态",
     "value_labels": {"0": "停用", "1": "启用"}},
    {"bound_object_key": "role", "name": "数据权限类型", "name_en": "role_data_type",
     "target_column": "data_type", "category": "属性", "description": "角色的数据范围类型"},
    {"bound_object_key": "role", "name": "是否默认角色", "name_en": "role_is_default",
     "target_column": "is_default", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    # 患者
    {"bound_object_key": "patient", "name": "患者来源渠道", "name_en": "patient_channel",
     "target_column": "channel", "category": "属性", "description": "患者档案的来源渠道"},
    # 案例
    {"bound_object_key": "case", "name": "是否种植修复", "name_en": "is_plant",
     "target_column": "is_plant", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "case", "name": "案例下单渠道", "name_en": "case_app_code",
     "target_column": "app_code", "category": "属性", "description": "案例下单渠道编码"},
    {"bound_object_key": "case", "name": "是否锁定", "name_en": "case_locked",
     "target_column": "locked", "category": "属性",
     "value_labels": {"0": "未锁定", "1": "已锁定"}},
    {"bound_object_key": "case", "name": "产品改善计划状态", "name_en": "collection_status",
     "target_column": "collection_status", "category": "状态",
     "value_labels": {"0": "未标识", "1": "已标识", "2": "已上传", "3": "已下载"}},
    # 案例文件
    {"bound_object_key": "case_file", "name": "文件分类", "name_en": "file_category",
     "target_column": "category_name", "category": "属性", "description": "文件业务分类名称"},
    {"bound_object_key": "case_file", "name": "文件格式", "name_en": "file_format",
     "target_column": "file_format", "category": "属性", "description": "文件扩展格式"},
    {"bound_object_key": "case_file", "name": "是否打包成功", "name_en": "file_is_success",
     "target_column": "is_success", "category": "状态",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "case_file", "name": "文件所属软件", "name_en": "software_app_code",
     "target_column": "software_app_code", "category": "属性", "description": "产出该文件的软件编码"},
    # 治疗
    {"bound_object_key": "case_treatment", "name": "工作类型", "name_en": "work_type",
     "target_column": "work_type", "category": "属性", "description": "治疗方案的工作类型"},
    {"bound_object_key": "case_treatment", "name": "是否全口种植", "name_en": "is_all_on_x",
     "target_column": "is_all_on_x", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "case_treatment", "name": "是否内嵌体", "name_en": "inlay_core",
     "target_column": "inlay_core", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    # 订单
    {"bound_object_key": "order", "name": "下单渠道", "name_en": "order_app_code",
     "target_column": "app_code", "category": "属性", "description": "订单下单渠道编码"},
    {"bound_object_key": "order", "name": "是否有上颌", "name_en": "order_is_upper",
     "target_column": "is_upper", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "order", "name": "是否有下颌", "name_en": "order_is_lower",
     "target_column": "is_lower", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "order", "name": "要求送达时间段", "name_en": "req_delivery_time_period",
     "target_column": "req_delivery_time_period", "category": "属性",
     "description": "客户要求送达的时间段"},
    {"bound_object_key": "order", "name": "接单技工所", "name_en": "laboratory_company_name",
     "target_column": "laboratory_company_name", "category": "组织"},
    # 设备
    {"bound_object_key": "equipment", "name": "设备类型", "name_en": "equipment_type",
     "target_column": "equipment_type", "category": "属性",
     "value_labels": {"1": "扫描仪"}},
    {"bound_object_key": "equipment", "name": "是否返修机", "name_en": "is_repair",
     "target_column": "is_repair", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "是否禁用", "name_en": "equipment_disabled",
     "target_column": "disabled", "category": "属性",
     "value_labels": {"0": "未禁用", "1": "已禁用"}},
    {"bound_object_key": "equipment", "name": "出库类型", "name_en": "outbound_type",
     "target_column": "outbound_type", "category": "属性", "description": "设备出库类型"},
    {"bound_object_key": "equipment", "name": "发行区域版本", "name_en": "equipment_version_code",
     "target_column": "version_code", "category": "属性",
     "description": "发行版本编码（CHINA-中国版，INTERNATIONAL-国际版）"},
    {"bound_object_key": "equipment", "name": "出售区域", "name_en": "receiver_country",
     "target_column": "receiver_country", "category": "地理"},
    {"bound_object_key": "equipment", "name": "是否高级功能", "name_en": "is_premium_function",
     "target_column": "is_premium_function", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "经销商", "name_en": "dealer_name",
     "target_column": "dealer_name", "category": "组织"},
    {"bound_object_key": "equipment", "name": "物料名称", "name_en": "catalog_name",
     "target_column": "catalog_name", "category": "属性"},
    # 工单
    {"bound_object_key": "work_order", "name": "商品类型", "name_en": "work_order_product_type",
     "target_column": "product_type", "category": "属性",
     "value_labels": {"1": "扫描仪"}},
    {"bound_object_key": "work_order", "name": "故障分类", "name_en": "issue_type_title",
     "target_column": "issue_type_title", "category": "属性", "description": "工单问题分类标题"},
    # 商户
    {"bound_object_key": "isv", "name": "签名类型", "name_en": "isv_sign_type",
     "target_column": "sign_type", "category": "属性",
     "value_labels": {"1": "RSA2", "2": "MD5"}},
    # 软件版本
    {"bound_object_key": "software_version", "name": "应用类型", "name_en": "software_type",
     "target_column": "software_type", "category": "属性", "description": "升级包所属应用"},
    {"bound_object_key": "software_version", "name": "是否强制更新", "name_en": "is_force",
     "target_column": "is_force", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "software_version", "name": "是否已发布", "name_en": "version_is_publish",
     "target_column": "is_publish", "category": "状态",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "software_version", "name": "是否灰度升级", "name_en": "version_is_gray",
     "target_column": "is_gray", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "software_version", "name": "适用站点范围", "name_en": "region_range",
     "target_column": "region_range", "category": "属性",
     "description": "适用站点（cn/en/eu/uk/us/jp），空为全量"},
    # 集成记录
    {"bound_object_key": "isv_integration", "name": "是否已下载", "name_en": "integration_is_downloaded",
     "target_column": "is_downloaded", "category": "状态",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "isv_integration", "name": "集成状态", "name_en": "integration_status",
     "target_column": "integration_status", "category": "状态"},
    {"bound_object_key": "isv_integration", "name": "三方名称", "name_en": "external_name",
     "target_column": "external_name", "category": "组织"},
    {"bound_object_key": "isv_integration", "name": "集成来源应用", "name_en": "integration_app_code",
     "target_column": "app_code", "category": "属性"},
    # ---- 第二轮：补齐"有列注释但还没有维度"的枚举/开关列 ----
    # value_labels 一律抄列注释给的原码值；注释未给码值的（如 is_notification）不写标签，
    # 由 precheck 从同名属性的 enum 继承——不靠臆测码值。
    {"bound_object_key": "user", "name": "是否强制修改密码", "name_en": "force_change_password",
     "target_column": "force_change_password", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "user", "name": "是否可编辑用户名", "name_en": "can_edit_username",
     "target_column": "can_edit_username", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "user", "name": "是否确认电子邮箱", "name_en": "is_confirm_email",
     "target_column": "is_confirm_email", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "company", "name": "是否自动接受合作申请", "name_en": "auto_accept_partnership",
     "target_column": "auto_accept_partnership", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "company", "name": "企业是否展示", "name_en": "company_is_show",
     "target_column": "is_show", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "partnership", "name": "发起方类型", "name_en": "request_company_type",
     "target_column": "request_company_type", "category": "属性",
     "value_labels": {"2": "诊所", "3": "技工所"}},
    {"bound_object_key": "case", "name": "是否通知企业", "name_en": "case_is_notification",
     "target_column": "is_notification", "category": "属性"},
    {"bound_object_key": "case", "name": "联耀正畸模拟状态", "name_en": "case_new_ortho_sim_status",
     "target_column": "new_ortho_sim_status", "category": "状态",
     "value_labels": {"0": "没有", "1": "已上传"}},
    {"bound_object_key": "case_file", "name": "是否选中", "name_en": "file_is_selected",
     "target_column": "is_selected", "category": "属性"},
    {"bound_object_key": "case_treatment", "name": "扫描预操作", "name_en": "scan_pre_op",
     "target_column": "scan_pre_op", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "case_treatment", "name": "内侧连接", "name_en": "mesial_connected",
     "target_column": "mesial_connected", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "case_treatment", "name": "扫描临时修复体", "name_en": "scan_provisional",
     "target_column": "scan_provisional", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "order", "name": "是否展示牙医签名", "name_en": "order_show_dentist_signature",
     "target_column": "show_dentist_signature", "category": "属性"},
    {"bound_object_key": "order", "name": "是否显示患者姓名", "name_en": "order_is_show_patient_name",
     "target_column": "is_show_patient_name", "category": "属性"},
    {"bound_object_key": "order", "name": "订单是否通知企业", "name_en": "order_is_notification",
     "target_column": "is_notification", "category": "属性"},
    {"bound_object_key": "order", "name": "订单联耀正畸模拟状态", "name_en": "order_new_ortho_sim_status",
     "target_column": "new_ortho_sim_status", "category": "状态",
     "value_labels": {"0": "没有", "1": "已上传"}},
    {"bound_object_key": "order", "name": "诊所删除标识", "name_en": "clinic_del_flag",
     "target_column": "clinic_del_flag", "category": "状态",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "order", "name": "技工所删除标识", "name_en": "lab_del_flag",
     "target_column": "lab_del_flag", "category": "状态",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "能适配联耀 ScanPro", "name_en": "compatible_with_alliedstar",
     "target_column": "compatible_with_alliedstar", "category": "属性",
     "value_labels": {"0": "不可以", "1": "可以"}},
    {"bound_object_key": "equipment", "name": "是否允许上传云平台", "name_en": "is_upload",
     "target_column": "is_upload", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "是否可以导出本地", "name_en": "is_export",
     "target_column": "is_export", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "高级扩展", "name_en": "is_premium_extension",
     "target_column": "is_premium_extension", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "是否发行版本", "name_en": "is_release",
     "target_column": "is_release", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "equipment", "name": "是否可以查看数据流", "name_en": "is_show_data_flow",
     "target_column": "is_show_data_flow", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "isv", "name": "是否选择控件展示项", "name_en": "is_select_show",
     "target_column": "is_select_show", "category": "属性"},
    {"bound_object_key": "software_version", "name": "是否有缺陷版本", "name_en": "is_flaw",
     "target_column": "is_flaw", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "software_version", "name": "是否出厂默认版本", "name_en": "is_factory_default",
     "target_column": "is_factory_default", "category": "属性",
     "value_labels": {"0": "否", "1": "是"}},
    {"bound_object_key": "software_version", "name": "版本状态", "name_en": "version_status",
     "target_column": "status", "category": "状态",
     "value_labels": {"0": "禁用", "1": "启用"}},
    {"bound_object_key": "software_version", "name": "是否应用官网", "name_en": "official_website_scope",
     "target_column": "official_website_scope", "category": "属性",
     "value_labels": {"0": "应用全部官网", "1": "应用国内官网", "2": "应用海外官网", "3": "不应用官网"}},
    {"bound_object_key": "isv_integration", "name": "集成来源类型", "name_en": "source_type",
     "target_column": "source_type", "category": "属性",
     "value_labels": {"0": "案例", "1": "订单"}},
]

# 指标（doc 顶层 metrics[] → adh_metrics）：
# name=英文标识、display_name=业务名；公式里的列必须已在归属对象属性里登记（本脚本先补属性）
METRICS: list[dict[str, Any]] = [
    # 用户
    {"bound_object_key": "user", "name": "cancelled_users", "display_name": "注销用户数",
     "formula": "COUNT(CASE WHEN status = 3 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "用户", "description": "账号状态=注销的用户数"},
    {"bound_object_key": "user", "name": "internal_users", "display_name": "内部用户数",
     "formula": "COUNT(CASE WHEN user_type = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "用户", "description": "用户类型=内部的用户数"},
    {"bound_object_key": "user", "name": "hospital_users", "display_name": "医院用户数",
     "formula": "COUNT(CASE WHEN user_type = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "用户", "description": "用户类型=医院的用户数"},
    {"bound_object_key": "user", "name": "lab_users", "display_name": "技工所用户数",
     "formula": "COUNT(CASE WHEN user_type = 3 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "用户", "description": "用户类型=技工所的用户数"},
    # 企业
    {"bound_object_key": "company", "name": "approved_companies", "display_name": "审核通过企业数",
     "formula": "COUNT(CASE WHEN status = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "审核状态=审核通过的企业数"},
    {"bound_object_key": "company", "name": "companies_with_devices", "display_name": "拥有设备企业数",
     "formula": "COUNT(CASE WHEN equipment_count > 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "名下设备数大于 0 的企业数"},
    {"bound_object_key": "company", "name": "uncertified_companies", "display_name": "未认证企业数",
     "formula": "COUNT(CASE WHEN certification_status = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "认证状态=未认证的企业数"},
    # 案例
    {"bound_object_key": "case", "name": "uploaded_cases", "display_name": "已上传案例数",
     "formula": "COUNT(CASE WHEN case_status = 3 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "个", "category": "案例", "description": "案例状态=已上传的案例数"},
    {"bound_object_key": "case", "name": "ordered_cases", "display_name": "已下单案例数",
     "formula": "COUNT(CASE WHEN case_status = 4 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "个", "category": "案例", "description": "案例状态=已下单的案例数"},
    {"bound_object_key": "case", "name": "split_teeth_cases", "display_name": "拆牙请求案例数",
     "formula": "COUNT(CASE WHEN split_teeth_status = 1 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "个", "category": "案例", "description": "拆牙状态=请求拆牙的案例数"},
    {"bound_object_key": "case", "name": "implant_cases", "display_name": "种植修复案例数",
     "formula": "COUNT(CASE WHEN is_plant = 1 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "个", "category": "案例", "description": "是否种植修复=是的案例数"},
    # 案例文件
    {"bound_object_key": "case_file", "name": "case_file_storage_gb", "display_name": "案例文件总大小GB",
     "formula": "SUM(size) / 1073741824.0", "agg_type": "SUM",
     "unit": "GB", "category": "案例", "description": "案例文件字节数总和换算为 GB"},
    # 治疗
    {"bound_object_key": "case_treatment", "name": "all_on_x_treatments", "display_name": "全口种植治疗数",
     "formula": "COUNT(CASE WHEN is_all_on_x = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "案例", "description": "治疗类型为全口种植的治疗方案数"},
    # 订单
    {"bound_object_key": "order", "name": "pending_confirm_orders", "display_name": "待确认订单数",
     "formula": "COUNT(CASE WHEN order_status = 1 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "单", "category": "订单", "description": "订单状态=待确认的订单数"},
    {"bound_object_key": "order", "name": "received_orders", "display_name": "已收货订单数",
     "formula": "COUNT(CASE WHEN order_status = 5 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "单", "category": "订单", "description": "订单状态=已收货的订单数"},
    {"bound_object_key": "order", "name": "cancelled_orders", "display_name": "已取消订单数",
     "formula": "COUNT(CASE WHEN order_status = 9 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "单", "category": "订单", "description": "订单状态=已取消的订单数"},
    # 设备
    {"bound_object_key": "equipment", "name": "inactive_devices", "display_name": "未激活设备数",
     "formula": "COUNT(CASE WHEN status = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "台", "category": "设备", "description": "设备状态=未激活的设备数"},
    {"bound_object_key": "equipment", "name": "cancelled_devices", "display_name": "已注销设备数",
     "formula": "COUNT(CASE WHEN status = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "台", "category": "设备", "description": "设备状态=已注销的设备数"},
    {"bound_object_key": "equipment", "name": "repair_devices", "display_name": "返修设备数",
     "formula": "COUNT(CASE WHEN is_repair = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "台", "category": "设备", "description": "是否返修机=是的设备数"},
    {"bound_object_key": "equipment", "name": "under_warranty_devices", "display_name": "保修期内设备数",
     "formula": "COUNT(CASE WHEN warranty_period_deadline > NOW() THEN 1 END)",
     "agg_type": "COUNT", "unit": "台", "category": "设备",
     "description": "保修到期时间晚于当前时间的设备数"},
    # 工单
    # 工单状态枚举以列注释/现网为准：0-待反馈，1-已反馈，4-关闭（不是“待处理/处理中/已完成”）
    {"bound_object_key": "work_order", "name": "pending_work_orders", "display_name": "待反馈工单数",
     "formula": "COUNT(CASE WHEN status = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "单", "category": "运维", "description": "工单状态=待反馈（0）的工单数"},
    {"bound_object_key": "work_order", "name": "feedback_work_orders", "display_name": "已反馈工单数",
     "formula": "COUNT(CASE WHEN status = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "单", "category": "运维", "description": "工单状态=已反馈（1）的工单数"},
    {"bound_object_key": "work_order", "name": "closed_work_orders", "display_name": "已关闭工单数",
     "formula": "COUNT(CASE WHEN status = 4 THEN 1 END)", "agg_type": "COUNT",
     "unit": "单", "category": "运维", "description": "工单状态=关闭（4）的工单数"},
    # 商户（open_isv_info.status：1 启用，2 禁用）
    {"bound_object_key": "isv", "name": "approved_isv_count", "display_name": "已启用商户数",
     "formula": "COUNT(CASE WHEN status = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "生态", "description": "商户状态=已启用（1）的商户数"},
    {"bound_object_key": "isv", "name": "disabled_isv_count", "display_name": "已禁用商户数",
     "formula": "COUNT(CASE WHEN status = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "生态", "description": "商户状态=禁用（2）的商户数"},
    # 软件版本
    {"bound_object_key": "software_version", "name": "firmware_versions", "display_name": "固件包版本数",
     "formula": "COUNT(CASE WHEN category = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "版本类别=固件包的版本数"},
    {"bound_object_key": "software_version", "name": "software_versions", "display_name": "软件包版本数",
     "formula": "COUNT(CASE WHEN category = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "版本类别=软件包的版本数"},
    # AI 会话
    {"bound_object_key": "ai_conversation", "name": "ai_conversation_users", "display_name": "有会话用户数",
     "formula": "COUNT(DISTINCT user_id)", "agg_type": "COUNT",
     "unit": "人", "category": "AI", "description": "产生过 AI 会话的用户数"},
    # 合作
    {"bound_object_key": "partnership", "name": "requesting_partnerships", "display_name": "申请中合作数",
     "formula": "COUNT(CASE WHEN status = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "条", "category": "组织", "description": "合作状态=申请中的合作关系数"},
    {"bound_object_key": "partnership", "name": "released_partnerships", "display_name": "已解除合作数",
     "formula": "COUNT(CASE WHEN status = 4 THEN 1 END)", "agg_type": "COUNT",
     "unit": "条", "category": "组织", "description": "合作状态=解除的合作关系数"},
    # 集成
    {"bound_object_key": "isv_integration", "name": "integration_records", "display_name": "集成记录数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "条", "category": "生态", "description": "未被删除的集成记录数"},
    {"bound_object_key": "isv_integration", "name": "case_source_integrations", "display_name": "案例来源集成数",
     "formula": "COUNT(CASE WHEN source_type = 0 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "条", "category": "生态", "description": "来源类型=案例的集成记录数"},
    {"bound_object_key": "isv_integration", "name": "order_source_integrations", "display_name": "订单来源集成数",
     "formula": "COUNT(CASE WHEN source_type = 1 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "条", "category": "生态", "description": "来源类型=订单的集成记录数"},
    {"bound_object_key": "isv_integration", "name": "downloaded_integrations", "display_name": "已下载集成数",
     "formula": "COUNT(CASE WHEN is_downloaded = 1 AND del_flag = 0 THEN 1 END)",
     "agg_type": "COUNT", "unit": "条", "category": "生态", "description": "已下载的集成记录数"},
    # 组织与档案
    {"bound_object_key": "department", "name": "department_count", "display_name": "部门数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "组织", "description": "未删除的部门数"},
    {"bound_object_key": "role", "name": "role_count", "display_name": "角色数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "组织", "description": "未删除的角色数"},
    {"bound_object_key": "dentist", "name": "dentist_count", "display_name": "牙医档案数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "组织", "description": "未删除的企业牙医档案数"},
    # ---- 第二轮：按 wiki 口径/列注释确定性补（不涉猜测）----
    # 用户（列注释：status 1正常/2禁用/3注销）
    {"bound_object_key": "user", "name": "disabled_users", "display_name": "禁用用户数",
     "formula": "COUNT(CASE WHEN status = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "人", "category": "用户", "description": "账号状态=禁用（2）的用户数"},
    {"bound_object_key": "user", "name": "avg_user_age", "display_name": "用户平均年龄",
     "formula": "AVG(age)", "agg_type": "AVG",
     "unit": "岁", "category": "用户", "description": "用户年龄列的平均值"},
    # 企业（列注释：auto_finish_enable 0未开启/1已开启；auto_accept_partnership 0否1是；
    # certification_status 0未认证/1通过/2认证中/3注销/4不通过，与 wiki 枚举值域一致）
    {"bound_object_key": "company", "name": "auto_finish_companies", "display_name": "开启自动完成的企业数",
     "formula": "COUNT(CASE WHEN auto_finish_enable = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "自动完成功能已开启（1）的企业数"},
    {"bound_object_key": "company", "name": "auto_accept_companies", "display_name": "开启自动接受合作申请的企业数",
     "formula": "COUNT(CASE WHEN auto_accept_partnership = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "自动接受合作关系申请已开启的企业数"},
    {"bound_object_key": "company", "name": "company_total_equipment", "display_name": "企业设备总数",
     "formula": "SUM(equipment_count)", "agg_type": "SUM",
     "unit": "台", "category": "企业", "description": "企业名下设备数量列求和（企业侧口径）"},
    {"bound_object_key": "company", "name": "certifying_companies", "display_name": "认证中企业数",
     "formula": "COUNT(CASE WHEN certification_status = 2 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "认证状态=认证中（2）的企业数"},
    {"bound_object_key": "company", "name": "certification_cancelled_companies", "display_name": "注销认证企业数",
     "formula": "COUNT(CASE WHEN certification_status = 3 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "认证状态=注销认证资质（3）的企业数"},
    {"bound_object_key": "company", "name": "certification_rejected_companies", "display_name": "认证不通过企业数",
     "formula": "COUNT(CASE WHEN certification_status = 4 THEN 1 END)", "agg_type": "COUNT",
     "unit": "家", "category": "企业", "description": "认证状态=认证不通过（4）的企业数"},
    # 患者
    {"bound_object_key": "patient", "name": "patient_total_cases", "display_name": "患者关联案例总数",
     "formula": "SUM(case_count)", "agg_type": "SUM",
     "unit": "个", "category": "患者", "description": "患者档案关联案例数量列求和"},
    {"bound_object_key": "patient", "name": "avg_patient_age", "display_name": "患者平均年龄",
     "formula": "AVG(age)", "agg_type": "AVG",
     "unit": "岁", "category": "患者", "description": "患者年龄列的平均值"},
    # 案例（case_status 码值取自 wiki 状态机：5=COMPLETED；locked 列注释 0否1是）
    {"bound_object_key": "case", "name": "avg_case_patient_age", "display_name": "案例患者平均年龄",
     "formula": "AVG(patient_age)", "agg_type": "AVG",
     "unit": "岁", "category": "案例", "description": "案例上病人年龄的平均值"},
    {"bound_object_key": "case", "name": "locked_cases", "display_name": "上锁案例数",
     "formula": "COUNT(CASE WHEN locked = 1 AND del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "案例", "description": "数据已上锁且未删除的案例数"},
    {"bound_object_key": "case", "name": "completed_cases_wiki", "display_name": "已完成案例数",
     "formula": "COUNT(CASE WHEN case_status = 5 AND del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "案例", "description": "案例状态=COMPLETED（5，wiki 状态机）的案例数"},
    # 案例文件（size 单位字节，与存量 total_case_storage_gb 同源）
    {"bound_object_key": "case_file", "name": "packed_case_files", "display_name": "打包成功文件数",
     "formula": "COUNT(CASE WHEN is_success = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "案例", "description": "打包状态=成功的案例文件数"},
    {"bound_object_key": "case_file", "name": "avg_case_file_kb", "display_name": "案例文件平均大小KB",
     "formula": "AVG(size) / 1024.0", "agg_type": "AVG",
     "unit": "KB", "category": "案例", "description": "案例文件字节数均值换算为 KB"},
    # 回收站
    {"bound_object_key": "case_recycle_bin", "name": "recycle_bin_cases", "display_name": "回收站案例数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "案例", "description": "回收站内未删除的案例数"},
    {"bound_object_key": "case_recycle_bin", "name": "recycle_bin_storage_gb", "display_name": "回收站占用空间GB",
     "formula": "SUM(occupied_space_size) / 1073741824.0", "agg_type": "SUM",
     "unit": "GB", "category": "案例", "description": "回收站案例文件字节数总和换算为 GB"},
    # 订单（两侧逻辑删除标记，列注释 0否1是；订单状态类指标因列注释与 wiki 冲突故未新增）
    {"bound_object_key": "order", "name": "clinic_deleted_orders", "display_name": "诊所侧已删除订单数",
     "formula": "COUNT(CASE WHEN clinic_del_flag = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "单", "category": "订单", "description": "诊所侧删除标识=是（1）的订单数"},
    {"bound_object_key": "order", "name": "lab_deleted_orders", "display_name": "技工所侧已删除订单数",
     "formula": "COUNT(CASE WHEN lab_del_flag = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "单", "category": "订单", "description": "技工所侧删除标识=是（1）的订单数"},
    # 设备（status=5 DISABLED 取自 wiki 状态机；gift_space_size 列注释明确单位 GB）
    {"bound_object_key": "equipment", "name": "disabled_devices", "display_name": "已禁用设备数",
     "formula": "COUNT(CASE WHEN status = 5 THEN 1 END)", "agg_type": "COUNT",
     "unit": "台", "category": "设备", "description": "设备状态=DISABLED（5，wiki 状态机）的设备数"},
    {"bound_object_key": "equipment", "name": "gift_space_total_gb", "display_name": "赠送云空间总量GB",
     "formula": "SUM(gift_space_size)", "agg_type": "SUM",
     "unit": "GB", "category": "设备", "description": "赠送云空间大小（列注释单位 GB）求和"},
    {"bound_object_key": "equipment", "name": "avg_warranty_after_activation", "display_name": "平均激活后保修月数",
     "formula": "AVG(months_after_activation)", "agg_type": "AVG",
     "unit": "月", "category": "设备", "description": "激活后保修期（月）的平均值"},
    {"bound_object_key": "equipment", "name": "avg_warranty_after_shipment", "display_name": "平均发货后保修月数",
     "formula": "AVG(months_after_shipment)", "agg_type": "AVG",
     "unit": "月", "category": "设备", "description": "发货后保修期（月）的平均值"},
    # 软件版本（is_flaw/is_force/is_gray/is_publish 列注释均给出 0/1 含义）
    {"bound_object_key": "software_version", "name": "flaw_versions", "display_name": "有缺陷的版本数",
     "formula": "COUNT(CASE WHEN is_flaw = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "标记为有缺陷（1）的版本数"},
    {"bound_object_key": "software_version", "name": "force_update_versions", "display_name": "强制更新版本数",
     "formula": "COUNT(CASE WHEN is_force = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "强制更新=是（1）的版本数"},
    {"bound_object_key": "software_version", "name": "gray_versions", "display_name": "灰度发布版本数",
     "formula": "COUNT(CASE WHEN is_gray = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "灰度升级=是（1）的版本数"},
    {"bound_object_key": "software_version", "name": "published_versions", "display_name": "已发布版本数",
     "formula": "COUNT(CASE WHEN is_publish = 1 THEN 1 END)", "agg_type": "COUNT",
     "unit": "个", "category": "运维", "description": "已发布=是（1）的版本数"},
    {"bound_object_key": "software_version", "name": "install_package_total_gb", "display_name": "安装包总大小GB",
     "formula": "SUM(size) / 1073741824.0", "agg_type": "SUM",
     "unit": "GB", "category": "运维", "description": "升级包字节数总和换算为 GB"},
    # 合作
    {"bound_object_key": "partnership", "name": "partnership_count", "display_name": "合作总数",
     "formula": "COUNT(CASE WHEN del_flag = 0 THEN 1 END)", "agg_type": "COUNT",
     "unit": "条", "category": "组织", "description": "未删除的合作关系总数"},
]

# 字典名全局唯一：早期以旧名登记失败的条目标不在字典里，只能靠这份名单从 doc 顶层清掉。
SUPERSEDED_DIM_RENAMES: set[tuple[str, str]] = {
    ("order", "是否通知企业"),
    ("order", "联耀正畸模拟状态"),
}

# 逻辑删除列统一枚举（各表列名一致，逐个补）
_DEL_FLAG_ENUM = {"0": "未删除", "1": "已删除"}
_DEL_FLAG_TABLES = [
    "t_user_customer", "t_user_company", "t_user_role", "t_user_company_department",
    "t_user_partner", "t_user_company_dentists", "t_case_records", "t_case_files",
    "t_case_treatments", "t_case_recycle_bin", "t_order_records", "t_product_equipment",
    "t_product_work_order", "open_isv_info", "t_software_upgrade_version",
    "t_ai_conversation", "t_integration_record",
]
for _t in _DEL_FLAG_TABLES:
    ENUMS.setdefault((_t, "del_flag"), dict(_DEL_FLAG_ENUM))

_PAIR_RE = re.compile(r"(\d{1,3})\s*[:：\-]\s*([^,，;；/|()（）]+)")


def _clean_name(comment: str, column: str) -> str:
    """列注释 → 业务名（截掉枚举说明/括号解释；无法得出可读名时回空）。"""
    s = re.sub(r"\s+", " ", str(comment or "")).strip()
    if not s:
        return ""
    for sep in ("（", "(", "：", ":", "，", ",", "；", ";", "|", "、"):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    s = s.strip(" 。.、,，;；-—_")
    s = s.replace("状态状态", "状态")
    if len(s) < 2 or s.isdigit():
        return ""
    return s


def _parse_enum(comment: str) -> dict[str, str]:
    """列注释里的 `0:xxx，1:yyy` / `0-xxx，1-yyy` 枚举 → {code: label}（≥2 项才认）。"""
    out: dict[str, str] = {}
    for code, label in _PAIR_RE.findall(str(comment or "")):
        label = label.strip(" 。.")
        if not label or label.isdigit():
            continue
        out.setdefault(str(int(code)), label)
    return out if len(out) >= 2 else {}


def _unique_name(name: str, used: set[str], column: str) -> str:
    """对象内业务名必须唯一（column_map 以业务名为键，重名会互相覆盖映射）。"""
    if name not in used:
        return name
    alt = f"{name}（{column}）"
    if alt not in used:
        return alt
    i = 2
    while f"{alt}{i}" in used:
        i += 1
    return f"{alt}{i}"


def _load_columns(datasource_id: int, table: str) -> list[dict]:
    from backend.common.db import execute_query

    # 该表没有 ordinal_position 列（按 id 递增近似物理列序即可，顺序只影响生成清单的阅读体验）。
    rows = execute_query(
        "SELECT column_name, data_type, column_comment, business_desc, is_key "
        "FROM adh_column_metadata WHERE table_name = %s AND is_active = 1 "
        "AND (datasource_id = %s OR datasource_id = 0) ORDER BY id",
        (table, datasource_id))
    return list(rows or [])


def enrich_properties(doc: dict, datasource_id: int) -> dict:
    """为每个对象补齐未登记的实存列；归一旧形状属性。返回报告。"""
    added: list[str] = []
    skipped_no_name: list[str] = []
    normalized: list[str] = []
    for obj in doc.get("objects") or []:
        table = str(obj.get("primary_table") or "").strip()
        key = str(obj.get("key") or "")
        props = obj.get("properties")
        if not isinstance(props, list):
            props = obj.get("attributes") if isinstance(obj.get("attributes"), list) else []
        props = props or []
        # 存量的 {key, display_name, data_type} 形状：`_object_property_columns` 只认
        # `column`，不归一会让该对象"一个物理列都没有"，指标口径全部校验失败。
        for p in props:
            if not isinstance(p, dict) or str(p.get("column") or "").strip():
                continue
            loose = str(p.get("key") or "").strip()
            if not loose:
                continue
            p["column"] = f"{table}.{loose}" if table else loose
            if not p.get("name") and p.get("display_name"):
                p["name"] = p["display_name"]
            if not p.get("type") and p.get("data_type"):
                p["type"] = p["data_type"]
            normalized.append(f"{key}.{p['name'] or loose}")
        obj["properties"] = props
        if not table:
            continue
        used = set()
        for p in props:
            nm = str(p.get("name") or p.get("display_name") or "").strip()
            if nm:
                used.add(nm)
        covered = {str(p.get("column") or "").split(".")[-1].lower()
                   for p in props if str(p.get("column") or "").strip()}
        for col in _load_columns(datasource_id, table):
            cname = str(col.get("column_name") or "").strip()
            low = cname.lower()
            if not cname or low in covered or low in SKIP_COLUMNS:
                continue
            comment = str(col.get("column_comment") or "")
            biz = NAME_OVERRIDES.get((table, low)) or _clean_name(comment, cname)
            if not biz:
                skipped_no_name.append(f"{key}.{cname}")
                continue
            biz = _unique_name(biz, used, cname)
            used.add(biz)
            prop: dict[str, Any] = {
                "column": f"{table}.{cname}",
                "name": biz,
                "type": str(col.get("data_type") or ""),
                "is_key": str(col.get("is_key") or "").lower() in ("1", "true", "yes"),
                "description": comment.strip(),
            }
            enum = ENUMS.get((table, low)) or _parse_enum(comment)
            if enum:
                prop["enum"] = enum
            props.append(prop)
            added.append(f"{key}.{biz} <- {cname}")
    return {"added": added, "skipped_no_name": skipped_no_name, "normalized": normalized}


def _dedup_against_dict(items: list[dict], table: str, name_field: str) -> tuple[list[dict], list[str]]:
    """与字典中已存在的同名条目比对（字典名全局唯一，重复收编只会报 conflict）。"""
    from backend.common.db import execute_query

    rows = execute_query(f"SELECT name FROM {table} WHERE is_active = 1") or []
    existing = {str(r.get("name") or "") for r in rows}
    keep: list[dict] = []
    skipped: list[str] = []
    for it in items:
        if str(it.get(name_field) or "") in existing:
            skipped.append(str(it.get(name_field)))
        else:
            keep.append(it)
    return keep, skipped


def build_create_date_dimensions(doc: dict, existing_objects: set[str]) -> list[dict]:
    """给每个对象补「<对象名>创建日期」维度（照 t_case_records 「创建日期」存量样板）。

    确定性的根据有两层，不涉及猜测：
    - 列存在：主表已把 create_time 登记为对象属性（没有该列的对象自动跳过）；
    - 语义固定：创建时间按 time_grain(day/week/month) 自动分桶，与存量样例同口径。
    已有 create_time 维度的对象（如 case）不重复生成。
    """
    out: list[dict] = []
    for obj in doc.get("objects") or []:
        key = str(obj.get("key") or obj.get("object_key") or "").strip()
        if not key or key.lower() in {x.lower() for x in existing_objects}:
            continue
        cols = {str(p.get("column") or "").split(".")[-1] for p in (obj.get("properties") or [])}
        if "create_time" not in cols:
            continue
        disp = OBJECT_LABELS.get(key) or str(obj.get("display_name") or key).strip()
        out.append({
            "bound_object_key": key,
            "name": f"{disp}创建日期",
            "name_en": f"{key}_create_date",
            "target_column": "create_time",
            "category": "时间",
            "description": f"{disp}创建时间，语义查询按 time_grain(day/week/month) 自动分桶",
            "aliases": ["创建时间"],
        })
    return out


def inherit_dim_labels(doc: dict, dims: list[dict]) -> list[str]:
    """维度未写 value_labels 时，从归属对象同名属性的 enum 继承（同源，不另猜码值）。

    典型场景：列注释只写了"是否通知企业"没给 0/1 含义，而该列属性已有 enum（早前按
    列注释/文档录入）——继承它，而不是在这里自己编一对标签。
    """
    prop_enum: dict[tuple[str, str], dict] = {}
    for obj in doc.get("objects") or []:
        key = str(obj.get("key") or "").strip().lower()
        for p in (obj.get("properties") or []):
            col = str(p.get("column") or "").split(".")[-1]
            if col and p.get("enum"):
                prop_enum[(key, col)] = dict(p["enum"])
    filled: list[str] = []
    for d in dims:
        if d.get("value_labels"):
            continue
        enum = prop_enum.get((str(d.get("bound_object_key") or "").lower(),
                              str(d.get("target_column") or "")))
        if enum:
            d["value_labels"] = enum
            filled.append(str(d.get("name")))
    return filled


def _object_columns(doc: dict) -> dict[str, set[str]]:
    """对象 key（小写）→ 其 properties[].column 裸列名集合（与 `_object_property_columns` 同口径）。"""
    out: dict[str, set[str]] = {}
    for obj in doc.get("objects") or []:
        k = str(obj.get("key") or obj.get("object_key") or "").strip().lower()
        cols = {str(p.get("column") or "").split(".")[-1].strip().lower()
                for p in (obj.get("properties") or []) if isinstance(p, dict)}
        out[k] = {c for c in cols if c}
    return out


def precheck_assets(doc: dict, metrics: list[dict], dimensions: list[dict]) \
        -> tuple[list[dict], list[dict], list[str]]:
    """按字典写入口的校验口径**只读预演收编**，把注定 failed 的条目挡在 doc 之外。

    为什么不在 apply 之后看 failed 桶：收编失败的条目会**留在 doc 顶层**（吸就进不了字典，
    丢了又没线索），下次保存还会再试一遍 —— 脏条目永久堆积。预演三道关：
    - 归属对象不存在（`_require_object_keys`）→ 拒；
    - 指标公式引用的列不在归属对象已登记物理列里（`_require_formula_matches_object`）→ 拒；
    - 维度 target_column 不在归属对象物理列里（`_normalize_dim_physical_binding` 的 bad 分支）→ 拒。
    """
    from backend.common.db import execute_query
    from backend.modules.catalog.services.metrics_service import _formula_columns

    cols_by_obj = _object_columns(doc)
    held: dict[str, dict[str, str]] = {}
    for table in ("adh_metrics", "adh_dimensions"):
        held[table] = {}
        for r in execute_query(
                f"SELECT name, name_en, bound_object_key FROM {table} WHERE is_active = 1") or []:
            for nm in (r.get("name"), r.get("name_en")):
                if nm:
                    held[table].setdefault(str(nm), str(r.get("bound_object_key") or ""))

    problems: list[str] = []
    ok_m: list[dict] = []
    for it in metrics:
        biz = str(it.get("display_name") or it.get("name") or "").strip()
        en = str(it.get("name") or "").strip()
        obj = str(it.get("bound_object_key") or "").strip().lower()
        if obj not in cols_by_obj:
            problems.append(f"指标「{biz}」：归属对象「{obj or '(未声明)'}」不在本模型")
            continue
        cols = cols_by_obj[obj] or set()
        if not cols:
            problems.append(f"指标「{biz}」：对象「{obj}」没有任何已登记物理列，无法校验口径")
            continue
        want = _formula_columns(str(it.get("formula") or ""))
        bad = sorted(w for w in want if w not in cols)
        if bad:
            problems.append(f"指标「{biz}」：公式列 {', '.join(bad)} 不在对象「{obj}」的物理列里")
            continue
        clash = held["adh_metrics"].get(biz) or held["adh_metrics"].get(en)
        if clash is not None and clash.strip().lower() != obj:
            problems.append(f"指标「{biz}」：字典同名口径已挂在对象「{clash or '(未挂靠)'}」")
            continue
        ok_m.append(it)

    ok_d: list[dict] = []
    for it in dimensions:
        nm = str(it.get("name") or "").strip()
        obj = str(it.get("bound_object_key") or "").strip().lower()
        col = str(it.get("target_column") or "").split(".")[-1].strip().lower()
        if obj not in cols_by_obj:
            problems.append(f"维度「{nm}」：归属对象「{obj or '(未声明)'}」不在本模型")
            continue
        if not col:
            problems.append(f"维度「{nm}」：未声明物理列（target_column 为空），收编必失败")
            continue
        if col not in (cols_by_obj[obj] or set()):
            problems.append(f"维度「{nm}」：列 {col} 不在对象「{obj}」的物理列里")
            continue
        clash = held["adh_dimensions"].get(nm) or held["adh_dimensions"].get(str(it.get("name_en") or ""))
        if clash is not None and clash.strip().lower() != obj:
            problems.append(f"维度「{nm}」：字典同名口径已挂在对象「{clash or '(未挂靠)'}」")
            continue
        ok_d.append(it)
    return ok_m, ok_d, problems


def audit_existing_assets(doc: dict) -> list[str]:
    """存量字典行体检：归属本模型对象、但口径引用了该对象不存在的列 → 列出来（不自动改）。

    这类口径在取数时会**静默返回 0 or 直接报错**（实测存量：指标公式里写 `close_time`/
    `status`/`online_status`，而表里根本没有这些列）。改名/改公式是业务决策，脚本不擅自
    改写（no-silent-degradation 的“显式暴露”一面）：只点名，等人确认后再修。
    """
    from backend.common.db import execute_query
    from backend.modules.catalog.services.metrics_service import _formula_columns

    cols_by_obj = _object_columns(doc)
    keys = [k for k in cols_by_obj]
    if not keys:
        return []
    ph = ", ".join(["%s"] * len(keys))
    out: list[str] = []
    for r in execute_query(
            "SELECT id, name, formula, bound_object_key FROM adh_metrics "
            f"WHERE is_active = 1 AND LOWER(bound_object_key) IN ({ph})", tuple(keys)) or []:
        obj = str(r.get("bound_object_key") or "").strip().lower()
        cols = cols_by_obj.get(obj) or set()
        bad = sorted(w for w in _formula_columns(str(r.get("formula") or "")) if w not in cols)
        if bad:
            out.append(f"指标#{r['id']}「{r['name']}」（{obj}）：公式列 {', '.join(bad)} 不在对象物理列里")
    for r in execute_query(
            "SELECT id, name, target_column, bound_object_key FROM adh_dimensions "
            f"WHERE is_active = 1 AND LOWER(bound_object_key) IN ({ph})", tuple(keys)) or []:
        obj = str(r.get("bound_object_key") or "").strip().lower()
        col = str(r.get("target_column") or "").split(".")[-1].strip().lower()
        if col and col not in (cols_by_obj.get(obj) or set()):
            out.append(f"维度#{r['id']}「{r['name']}」（{obj}）：列 {col} 不在对象物理列里")
    return out


def _split_ambiguous(doc: dict, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """拆分「绑定层无法唯一解析主表」的条目（return direct, absorb）。

    绑定层 `_object_binding` 按 **object_key 全局**取主表，跨模型同名 key 就歧义：
    实测 `role` 同时存在于本模型（t_user_role）与 AS-BOT 系统本体（adh_roles），
    解析回空 → 维度收编报“没有可登记的物理表”、指标 target_table 落空（planner 加载不到）。
    这类条目改由脚本按 doc 的 primary_table 显式补 target_table，直接走字典写入口。
    """
    from backend.modules.catalog.services.metrics_service import _object_binding_by_key

    tables = {str(o.get("key") or "").strip().lower(): str(o.get("primary_table") or "").strip()
              for o in (doc.get("objects") or [])}
    direct: list[dict] = []
    absorb: list[dict] = []
    for it in items:
        obj = str(it.get("bound_object_key") or "").strip()
        tab, _ds = _object_binding_by_key(obj)
        if tab or not tables.get(obj.lower()):
            absorb.append(it)
        else:
            direct.append({**it, "target_table": tables[obj.lower()]})
    return direct, absorb


def register_direct_assets(direct_m: list[dict], direct_d: list[dict]) -> list[str]:
    """按显式 target_table 直接写字典（与 absorb 同一写入口，校验一条不落）。

    字典行一律 `datasource_id=0`（全局口径，与 absorb 同口径）：`adh_dimensions.datasource_id`
    是 INT 列，源雪花 id 直接写会 Out of range。
    """
    from backend.modules.catalog.services import metrics_service as ms

    log: list[str] = []
    for kind, items, fn in (("指标", direct_m, ms.create_metric),
                            ("维度", direct_d, ms.create_dimension)):
        for it in items:
            payload = {**it, "datasource_id": 0}
            if kind == "指标":
                payload = {**payload, "name": it.get("name") or it.get("display_name"),
                           "display_name": it.get("display_name") or it.get("name")}
            try:
                fn(payload)
                log.append(f"{kind}「{it.get('display_name') or it.get('name')}」→ {it['target_table']}（直挂）")
            except ValueError as e:
                log.append(f"{kind}「{it.get('display_name') or it.get('name')}」未登记：{e}")
    return log


def drop_registered_leftovers(doc: dict, superseded: set[tuple[str, str]] | None = None) -> list[str]:
    """清掉 doc 顶层「字典里已经存在」的遗留条目（absorb 失败留下的残炧）。

    背景：收编失败的条目会被 absorb **留在 doc 顶层**（留线索），但在它被其它通道
    （如本脚本的直挂）登记成功后，顶层那行就成了重复无用的脏数据：每次保存 都会重报
    “未收编”警告，而它已经进了字典。这里只删「字典已有同名行」的条目，并按
    canonical 形态给归属对象补 `metrics[]` 名字引用（与 absorb 成功分支同口径）。

    `superseded`：因重名被改名重登的旧条目（(对象 key, 旧名)）——字典里查不到旧名
    （吸就没进去），只能靠这份名单清理，否则会永久卡在顶层。
    """
    from backend.common.db import execute_query

    superseded = superseded or set()
    def names(table: str) -> set[str]:
        return {str(r.get("name") or "") for r in
                execute_query(f"SELECT name FROM {table} WHERE is_active = 1") or []}

    have_m, have_d = names("adh_metrics"), names("adh_dimensions")
    by_key = {str(o.get("key") or o.get("object_key") or "").strip().lower(): o
              for o in (doc.get("objects") or [])}
    dropped: list[str] = []
    for list_key, have in (("metrics", have_m), ("dimensions", have_d)):
        keep: list = []
        for item in doc.get(list_key) or []:
            if not isinstance(item, dict):
                keep.append(item)
                continue
            nm = str(item.get("display_name") or item.get("name") or "").strip()
            obj_key = str(item.get("bound_object_key") or "").strip().lower()
            if nm and (obj_key, nm) in superseded:
                dropped.append(f"{list_key[:-1]}:{nm}（旧名，已改名重登）")
                continue
            if nm and nm in have:
                dropped.append(f"{list_key[:-1]}:{nm}")
                owner = by_key.get(str(item.get("bound_object_key") or "").strip().lower())
                if owner is not None and list_key == "metrics":
                    refs = [x for x in (owner.get("metrics") or []) if isinstance(x, str)]
                    if nm not in refs:
                        owner["metrics"] = refs + [nm]
                continue
            keep.append(item)
        if keep:
            doc[list_key] = keep
        else:
            doc.pop(list_key, None)
    return dropped


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐联耀业务本体的属性/维度/指标")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只打印计划）")
    ap.add_argument("--model-name", default=MODEL_NAME)
    args = ap.parse_args()

    from backend.common.db import execute_query
    from backend.modules.catalog.services import ontology_service as svc

    rows = execute_query(
        "SELECT id, name, status, datasource_id FROM adh_ontology_models "
        "WHERE name = %s AND status <> 'archived' ORDER BY id DESC LIMIT 1", (args.model_name,))
    if not rows:
        print(f"[FAIL] 未找到模型：{args.model_name}")
        return 2
    model_id = int(rows[0]["id"])
    datasource_id = int(rows[0]["datasource_id"] or 0)
    if str(rows[0]["status"]) != "active":
        print(f"[FAIL] 模型 {model_id} 状态为 {rows[0]['status']}，本脚本只补生效版（先激活）")
        return 2
    model = svc.get_model(model_id)
    doc = json.loads(model["json_content"])

    prop_report = enrich_properties(doc, datasource_id)

    # 顶层 metrics/dimensions 先与字典查重，剩下的交给 save_draft 的收编（absorb）通道
    metrics, dup_m = _dedup_against_dict(list(METRICS), "adh_metrics", "display_name")
    have_date = {str(r.get("bound_object_key") or "") for r in execute_query(
        "SELECT DISTINCT bound_object_key FROM adh_dimensions "
        "WHERE is_active = 1 AND target_column = 'create_time'") or []}
    date_dims = build_create_date_dimensions(doc, have_date)
    dims, dup_d = _dedup_against_dict(list(DIMENSIONS) + date_dims, "adh_dimensions", "name")
    inherited = inherit_dim_labels(doc, dims)
    metrics, dims, asset_problems = precheck_assets(doc, metrics, dims)
    # 绑定层歧义（跨模型同名 object_key）的条目不能靠 absorb 派生主表 → 改走字典写入口直挂
    direct_m, metrics = _split_ambiguous(doc, metrics)
    direct_d, dims = _split_ambiguous(doc, dims)
    leftovers = drop_registered_leftovers(doc, SUPERSEDED_DIM_RENAMES)

    top_m = doc.get("metrics") or []
    top_d = doc.get("dimensions") or []
    have_m = {str(x.get("display_name") or x.get("name") or "") for x in top_m if isinstance(x, dict)}
    have_d = {str(x.get("name") or "") for x in top_d if isinstance(x, dict)}
    doc["metrics"] = top_m + [m for m in metrics if m["display_name"] not in have_m]
    doc["dimensions"] = top_d + [d for d in dims if d["name"] not in have_d]

    fmt_errors = svc.validate_doc_formats(doc)
    route_errors = [it for it in svc.find_business_route_issues(doc)
                    if it["severity"] == "error"]
    print(f"== 模型 {model_id} {args.model_name} (datasource_id={datasource_id})")
    print(f"属性新增: {len(prop_report['added'])}  属性归一: {len(prop_report['normalized'])}"
          f"  无注释跳过: {len(prop_report['skipped_no_name'])}")
    print(f"创建日期维度待生成: {len(date_dims)}（已存在 create_time 维度的对象 {len(have_date)} 个）")
    if inherited:
        print(f"维度标签从属性 enum 继承: {len(inherited)} 条 -> " + ", ".join(inherited))
    print(f"维度待收编: {len(doc['dimensions']) - len(top_d)}（字典已存在跳过 {len(dup_d)}）")
    print(f"指标待收编: {len(doc['metrics']) - len(top_m)}（字典已存在跳过 {len(dup_m)}）")
    if direct_m or direct_d:
        print(f"绑定层主表歧义、按对象主表直挂: 指标 {len(direct_m)} / 维度 {len(direct_d)}")
        for it in direct_m + direct_d:
            print(f"    · {it.get('display_name') or it.get('name')} → {it['target_table']}")
    if leftovers:
        print(f"清掉 doc 顶层已入字典的遗留条目 {len(leftovers)} 条: " + ", ".join(leftovers))
    if asset_problems:
        print(f"  维度/指标预检未通过 {len(asset_problems)} 条（不写进 doc）:")
        for line in asset_problems:
            print("    ! " + line)
    stale = audit_existing_assets(doc)
    if stale:
        print(f"  存量字典错口径 {len(stale)} 条（本脚本不改，需人工确认后修）:")
        for line in stale:
            print("    ! " + line)
    if prop_report["skipped_no_name"]:
        print("  无注释/无业务名跳过: " + ", ".join(prop_report["skipped_no_name"][:40])
              + (" …" if len(prop_report["skipped_no_name"]) > 40 else ""))
    if dup_d:
        print("  维度已存在（跳过）: " + ", ".join(dup_d))
    if dup_m:
        print("  指标已存在（跳过）: " + ", ".join(dup_m))
    if fmt_errors:
        print("[FAIL] 格式校验未通过: " + "; ".join(fmt_errors[:10]))
        return 3
    if route_errors:
        print("[FAIL] 业务路由校验未通过: " + "; ".join(it["message"] for it in route_errors[:10]))
        return 3

    if not args.apply:
        print("\n(dry-run) 计划已通过格式/路由校验；加 --apply 才写库")
        for line in prop_report["added"]:
            print("  + " + line)
        return 0

    result = svc.save_draft(model_id, json.dumps(doc, ensure_ascii=False, indent=2))
    assets = result.get("semantic_assets") or {}
    direct_log = register_direct_assets(direct_m, direct_d)
    print("\n== 已保存（级联已跑）")
    print(f"对象数: {result.get('object_count')}")
    for bucket in ("metrics_created", "metrics_merged", "dimensions_created", "dimensions_merged"):
        vals = assets.get(bucket) or []
        print(f"  {bucket}: {len(vals)}")
        for v in vals:
            print(f"    - {v}")
    for bucket in ("conflicts", "failed"):
        vals = assets.get(bucket) or []
        if vals:
            print(f"  {bucket}: {len(vals)}")
            for v in vals:
                print(f"    ! {v}")
    if direct_log:
        print("  直挂条目:")
        for line in direct_log:
            print("    · " + line)
    warnings = result.get("validation_warnings") or []
    if warnings:
        print("  warnings:")
        for w in warnings:
            print(f"    ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
