"""构建「官网业务本体」（官网/帮助中心 t_help_center* 七表，仅中国站）+ 官网可观测侧命名收口。

背景（为什么需要它）：官网/帮助中心的 7 张业务表（在线咨询、技术支持反馈、询盘、演示预约、
合作伙伴申请、邮件路由、下载中心）此前**没有任何本体对象**——语义层与 `get_metrics` 拿不到
它们，AI 只能用裸 SQL 猜列名，问「官网这周收到多少咨询」只能得到各回合不一致的答案；
这些表与 ARMS RUM 同属"官网"业务面，但 RUM 是 SLS 单源、help center 是 MySQL 单源，
**一个模型只能有一个主源**（`_expand_objects` 把所有对象写到模型主源，
`deploy_sites` 每站一个源），因此拆成两个模型：

    官网业务本体（本脚本，主源 prd-cn，仅中国站）      ← 业务线索/工单/配置
    官网可观测本体（原「ARMS RUM 观测本体」，本脚本改名） ← Web 端 RUM 前端体验

建模范围（用户拍板 2026-11-25）：5 业务表 + 2 配置表；**不收** `t_help_center_question_copy`
（源表已不存在）与 `t_help_center_email`（0 行，且与 email_config 职责重叠）。

实测口径（以 prd-cn 实库 `SHOW FULL COLUMNS` + `GROUP BY` 取值分布为准，不照搬列名、不编造语义）：
- `del_flag` 源表注释写的是「0：是，1：否」（即 0=已删除），实测相反：`del_flag=0` 是活跃集
  （support 表 del_flag=0 的行最新到当日、有 253 条已回复；del_flag=1 的行停在 2026-04），
  故本脚本一律按 **0=未删除** 建模并作为全部指标的过滤条件；这是源表注释缺陷，不改库。
- `question_type` 取值 99% 为 0 且无业务标签来源 → 不设枚举（编造枚举就是把未知当已知）。
- `business_scope`（email_config）是多值串（`1` / `2,3,4`）→ 不当单值枚举，不按它做维度。
- `region`（email_config）取值随业务扩张而增 → 不设枚举，维度不带 value_labels。
- `t_help_center_question.question_code` 与 `t_help_center_support.question_code` 167 行可对上
  （支持站提交的工单在 question 记主单、support 记明细）→ 建一条 1:1 关系；询盘/预约/伙伴申请的
  `code` 各有前缀（I/D/P）互不相通，不建关系。

PII（用户拍板：建模并补登敏感字段）：`email` / `phone` 已被全局列名基线 block，不重复登记；
本批补登表级规则 —— 联系人名（first_name/last_name）、设备序列号、企业识别号（hash）、
配置收件/抄送邮箱。**公司名/诊所名/公司网站不脱敏**：它们是业务实体而非个人标识，
partial 脱敏会把不同公司合并成同一个分组值（错数比泄露更隐蔽）；邮编同理不脱敏。

写入路径（唯一合规路径，绝不直改派生表——本体建模规范 §1 / product-capability-first）：
    metadata_sync.sync_table_columns（补列元数据：support 在该源此前 0 列、email_config 缺 1 列）
    → save_generated_draft → save_draft（canonical doc，改名「官网业务本体」）→ 声明 deploy_sites
    → claim_ontology_tables_as_products（激活门禁 P2.3：对象主表必须是数据产品）
    → activate（级联：展开对象 → 部署绑定 → 枚举联动 → 重建图谱 → 知识库同步）
    → metrics_service 指标/维度字典 → 敏感字段（HTTP /api/security/sensitive-fields，权限+审计）
    → ARMS 模型改名「官网可观测本体」（save_draft 级联，前后对比绑定行不劣化）
    → 端到端负/正验证（semantics.execute 真实取数 + 跨站绑定拒绝）

用法（仓库根目录）：
    venv/bin/python -m backend.scripts.build_official_website_ontology                 # dry-run
    venv/bin/python -m backend.scripts.build_official_website_ontology --apply          # 落库并发布
    venv/bin/python -m backend.scripts.build_official_website_ontology --only model,activate --apply
    venv/bin/python -m backend.scripts.build_official_website_ontology --skip sensitive --apply

幂等/边界：字典按业务名收敛（已存在则对齐口径，不跳过）；指标/维度全部 `certified=0`
（认证必须有口径责任人，脚本给不出"谁负责"）；敏感字段按 (ds,表,列) 查重后新增或对齐
mask_type；ARMS 改名幂等（已是新名则跳过）。
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

CREATED_BY = "script:build_official_website_ontology"

MODEL_NAME = "官网业务本体"
DOMAIN = "官网域（咨询 · 支持 · 线索）"
DESCRIPTION = (
    "Alliedstar 官网/帮助中心的业务事实：在线咨询与支持反馈（工单）、官网询盘、演示预约、"
    "合作伙伴申请，以及邮件路由与下载中心两张配置表。数据来自官网/支持站表单提交与后台配置，"
    "仅中国站（prd-cn）。与「官网可观测本体」（Web 端 RUM 前端体验/性能/点击）同属官网业务面但"
    "互不相通：本域回答「收到多少咨询/线索/申请、处理到哪一步」，可观测域回答「页面体验如何」，"
    "两者对象与指标不得相加。"
)

DS_NAME = "prd-cn"                     # 主源（MySQL，stardb）
SITE_DS = [("cn", "prd-cn")]           # 仅中国站（用户拍板；prd-en 也有数据但本批不收）

ARMS_MODEL_NAME_OLD = "ARMS RUM 观测本体"
ARMS_MODEL_NAME_NEW = "官网可观测本体"

# 数据源/站点外的运行期身份（敏感字段走 HTTP 需要可信身份；admin 由中间件旁路裁决）
WEB_BASE = os.environ.get("ADH_WEB_BASE", "http://127.0.0.1:8000")
ADMIN_USER_ID = int(os.environ.get("ADH_ADMIN_USER_ID", "1778154925"))

# ── 枚举（只登记实测确证且码值稳定的列）──────────────────────────────
Q_STATUS = {"0": "未处理", "1": "处理中", "2": "处理完成"}
RETURN_VISIT = {"0": "未回访", "1": "已回访"}
DEL_FLAG = {"0": "未删除", "1": "已删除"}
SOURCE_TYPE = {"official_website": "官网在线提问", "support_website": "支持网站"}
CUSTOMER_TYPE = {"1": "经销商", "2": "终端用户"}
PRODUCT_MODEL = {"0": "AS 100", "1": "AS 200E", "2": "AS 260"}
YES_NO = {"0": "否", "1": "是"}
DOWNLOAD_TYPE = {"0": "文档下载", "1": "软件下载", "2": "远程协助工具下载"}
IS_VALID = {"0": "无效", "1": "有效"}

# ── 对象规格：properties 逐列按 prd-cn 实存列建模（163 列），四元组 = (列, 业务名, 类型[, 枚举]) ──
OBJ_SPECS = [
    {
        "key": "official_website_question",
        "display_name": "官网在线咨询（疑问工单）",
        # 别名只写业务名：物理表名不得进别名/描述（上云文档会原样渲染别名，护栏 §7）
        "aliases": ["官网提问", "在线咨询", "咨询工单", "疑问", "官网咨询"],
        "description": (
            "官网/支持站表单提交的在线咨询（疑问工单）主记录：一条提交一行，含标题、描述、"
            "来源渠道（official_website=官网在线提问，support_website=支持网站）、处理状态与"
            "回访状态。支持站提交的工单其明细在「技术支持反馈」对象（按问题编号 1:1 关联）。"
            "删除标记 del_flag=0 为未删除（源表注释写反，以数据实存为准）。"),
        "table": "t_help_center_question",
        "columns": [
            ("id", "主键ID", "int"),
            ("question_code", "问题编号", "string"),
            ("question_title", "问题标题", "string"),
            ("question_desc", "问题描述", "string"),
            ("email", "联系邮箱", "string"),
            ("phone", "联系电话", "string"),
            ("question_type", "问题类型", "int"),
            ("attachment", "附件", "string"),
            ("question_status", "处理状态", "int", Q_STATUS),
            ("question_feedback", "处理反馈", "string"),
            ("question_source_type", "问题来源渠道", "string", SOURCE_TYPE),
            ("return_visit_status", "回访状态", "string", RETURN_VISIT),
            ("first_name", "联系人名", "string"),
            ("last_name", "联系人姓", "string"),
            ("company_name", "公司名称", "string"),
            ("country", "国家", "string"),
            ("business_type", "业务类型", "string"),
            ("interest", "关注方向", "string"),
            ("learn_way", "了解渠道", "string"),
            ("language", "语言", "string"),
            ("appmanager", "业务域标识", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网提问数", "官网提问待处理数", "官网提问处理完成率(%)"],
    },
    {
        "key": "official_website_support",
        "display_name": "技术支持反馈（支持站工单明细）",
        "aliases": ["支持反馈", "技术支持", "支持工单", "售后反馈"],
        "description": (
            "支持站提交的技术支持反馈明细：设备序列号、客户类型、产品型号、故障步骤、处理与回访状态。"
            "与「官网在线咨询」按问题编号 1:1 对应（支持站提交时主单记在 question、明细记在此表）。"
            "del_flag=0 为未删除。"),
        "table": "t_help_center_support",
        "columns": [
            ("id", "主键ID", "int"),
            ("question_code", "关联问题编号", "string"),
            ("customer_type", "客户类型", "int", CUSTOMER_TYPE),
            ("product_model", "产品型号", "int", PRODUCT_MODEL),
            ("serial_number", "设备序列号", "string"),
            ("country", "国家", "string"),
            ("state", "省份/州", "string"),
            ("question_type", "问题类型", "int"),
            ("question_title", "问题标题", "string"),
            ("question_desc", "问题描述", "string"),
            ("troubleshooting_steps", "故障排查步骤", "string"),
            ("email", "联系邮箱", "string"),
            ("phone", "联系电话", "string"),
            ("attachment", "附件", "string"),
            ("question_status", "处理状态", "int", Q_STATUS),
            ("question_feedback", "处理反馈", "string"),
            ("return_visit_status", "回访状态", "int", RETURN_VISIT),
            ("language", "语言", "string"),
            ("appmanager", "业务域标识", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网支持反馈数", "官网支持待处理数"],
    },
    {
        "key": "official_website_inquiry",
        "display_name": "官网询盘（联系我们）",
        "aliases": ["询盘", "官网留言", "联系我们", "request info"],
        "description": (
            "官网「联系我们/咨询」表单提交的询盘：联系人、国家/省份、了解渠道、咨询内容、"
            "处理与回访状态。编码前缀 I（如 I26112209719684864）。del_flag=0 为未删除。"),
        "table": "t_help_center_official_request_info",
        "columns": [
            ("id", "主键ID", "int"),
            ("code", "询盘编号", "string"),
            ("first_name", "联系人名", "string"),
            ("last_name", "联系人姓", "string"),
            ("email", "联系邮箱", "string"),
            ("phone", "联系电话", "string"),
            ("postal_code", "邮政编码", "string"),
            ("country", "国家", "string"),
            ("state", "省份/州", "string"),
            ("learn_way", "了解渠道", "string"),
            ("question_desc", "咨询内容", "string"),
            ("language", "语言", "string"),
            ("appmanager", "业务域标识", "string"),
            ("return_visit_status", "回访状态", "int", RETURN_VISIT),
            ("question_status", "处理状态", "int", Q_STATUS),
            ("question_feedback", "处理反馈", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网询盘数", "官网询盘待处理数"],
    },
    {
        "key": "official_website_demo_request",
        "display_name": "官网演示预约",
        "aliases": ["演示预约", "试用预约", "demo 预约", "request demo"],
        "description": (
            "官网「预约演示」表单提交记录：职业、执业类型、诊所名称、是否已拥有口扫、在用品牌、"
            "首选经销商、是否订阅推送。编码前缀 D。del_flag=0 为未删除。"),
        "table": "t_help_center_official_request_demo",
        "columns": [
            ("id", "主键ID", "int"),
            ("code", "预约编号", "string"),
            ("first_name", "联系人名", "string"),
            ("last_name", "联系人姓", "string"),
            ("email", "联系邮箱", "string"),
            ("phone", "联系电话", "string"),
            ("postal_code", "邮政编码", "string"),
            ("profession", "职业", "string"),
            ("practice_type", "执业类型", "string"),
            ("practice_name", "诊所名称", "string"),
            ("country", "国家", "string"),
            ("state", "省份/州", "string"),
            ("learn_way", "了解渠道", "string"),
            ("return_visit_status", "回访状态", "int", RETURN_VISIT),
            ("preferred_dental_dealer", "首选经销商", "string"),
            ("has_scanner", "是否已购口扫", "int", YES_NO),
            ("intraoral_scanner", "在用口扫品牌", "string"),
            ("language", "语言", "string"),
            ("appmanager", "业务域标识", "string"),
            ("question_status", "处理状态", "int", Q_STATUS),
            ("question_feedback", "处理反馈", "string"),
            ("need_push", "是否订阅推送", "int", YES_NO),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网演示预约数", "官网演示预约待处理数"],
    },
    {
        "key": "official_website_partner_application",
        "display_name": "合作伙伴申请",
        "aliases": ["伙伴申请", "成为合作伙伴", "代理申请", "become partner"],
        "description": (
            "官网「成为合作伙伴」表单提交记录：公司/业务信息、目标市场、从业年限、是否持有口扫分销权、"
            "意向投入、企业识别号、处理与回访状态。编码前缀 P。del_flag=0 为未删除。"),
        "table": "t_help_center_official_become_partner",
        "columns": [
            ("id", "主键ID", "int"),
            ("code", "申请编号", "string"),
            ("first_name", "联系人名", "string"),
            ("last_name", "联系人姓", "string"),
            ("business_name", "公司名称", "string"),
            ("email", "联系邮箱", "string"),
            ("phone", "联系电话", "string"),
            ("postal_code", "邮政编码", "string"),
            ("company_website", "公司网站", "string"),
            ("business_type", "业务类型", "string"),
            ("employees_info", "员工规模", "string"),
            ("learn_way", "了解渠道", "string"),
            ("interest_countries", "目标市场", "string"),
            ("interest_reason", "合作动机", "string"),
            ("business_years", "从业年限", "string"),
            ("company_hq_based", "公司注册地", "string"),
            ("business_entity_incorporated", "实体注册地", "string"),
            ("ownership_structure", "所有权结构", "string"),
            ("represent", "代理设备品牌", "string"),
            ("software_represent", "代理软件品牌", "string"),
            ("has_distribution_rights_for_any_scanner", "是否持有口扫分销权", "int", YES_NO),
            ("intraoral_scanner", "分销口扫品牌", "string"),
            ("initial_investment", "意向投入", "string"),
            ("business_identification_number", "企业识别号", "string"),
            ("return_visit_status", "回访状态", "int", RETURN_VISIT),
            ("question_status", "处理状态", "int", Q_STATUS),
            ("question_feedback", "处理反馈", "string"),
            ("country", "国家", "string"),
            ("state", "省份/州", "string"),
            ("language", "语言", "string"),
            ("appmanager", "业务域标识", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网伙伴申请数", "官网伙伴申请待处理数"],
    },
    {
        "key": "official_website_email_route",
        "display_name": "官网邮件路由配置",
        "aliases": ["邮件路由", "表单收件人", "咨询邮件分配"],
        "description": (
            "官网各业务表单（支持/演示预约/询盘/伙伴申请）按区域与国家分配收件人/抄送人的配置表。"
            "业务范围是多值串（1=support、2=请求演示、3=询盘、4=伙伴申请，可组合如 2,3,4），"
            "区域取值随业务扩张而增，故都不设枚举。del_flag=0 为未删除。"),
        "table": "t_help_center_email_config",
        "columns": [
            ("id", "主键ID", "int"),
            ("region", "区域", "string"),
            ("country", "国家", "string"),
            ("state", "省份/州", "string"),
            ("business_scope", "业务范围(多值)", "string"),
            ("email_recipient", "收件邮箱", "string"),
            ("email_cc", "抄送邮箱", "string"),
            ("appmanager", "业务域标识", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网邮件路由配置数"],
    },
    {
        "key": "official_website_download_resource",
        "display_name": "官网下载中心资源",
        "aliases": ["下载中心", "资料下载", "软件下载"],
        "description": (
            "官网下载中心挂载的资源条目：下载类型（文档/软件/远程协助工具）、适用产品型号、"
            "文档类型、下载链接、是否有效与排序。del_flag=0 为未删除；是否有效见「是否有效」属性。"),
        "table": "t_help_center_download_center",
        "columns": [
            ("id", "主键ID", "int"),
            ("download_type", "下载类型", "int", DOWNLOAD_TYPE),
            ("equipment_model", "适用产品型号", "string"),
            ("document_type_code", "文档类型编码", "string"),
            ("document_type_name", "文档类型名称", "string"),
            ("language", "语言", "string"),
            ("show_sort", "排序", "int"),
            ("download_link", "下载链接", "string"),
            ("is_valid", "是否有效", "int", IS_VALID),
            ("remark", "备注", "string"),
            ("appmanager", "业务域标识", "string"),
            ("create_by", "创建人", "string"),
            ("create_time", "创建时间", "datetime"),
            ("update_by", "更新人", "string"),
            ("update_time", "更新时间", "datetime"),
            ("del_flag", "删除标记", "int", DEL_FLAG),
        ],
        "metrics": ["官网下载资源数"],
    },
]

# 对象间关系（仅 question ↔ support：实测 167 行 question_code 可对上，其余 code 前缀互不相通）
LINKS = {
    "official_website_question": [
        {
            "target": "official_website_support",
            "type": "has_support_detail",
            "join": "t_help_center_question.question_code = t_help_center_support.question_code",
            "cardinality": "1:1",
            "description": "支持站提交的工单：主单在本对象，明细在「技术支持反馈」（按问题编号关联）",
        },
    ],
}

# ── 指标字典（口径唯一登记在 adh_metrics；对象内 metrics 只存名字字符串）──────────
# 全部口径以 del_flag=0（未删除）为基线：源表注释写反，实测 del_flag=0 才是活跃集。
_ALIVE = "del_flag = 0"
METRICS = [
    {
        "name": "官网提问数", "name_en": "official_question_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_question", "table": "t_help_center_question",
        "aliases": ["官网咨询数", "在线咨询数", "咨询工单数", "官网提问量"],
        "description": "官网/支持站在线咨询（疑问工单）提交量，按未删除记录计数。",
    },
    {
        "name": "官网提问待处理数", "name_en": "official_question_pending", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND question_status = 0 THEN 1 ELSE 0 END)",
        "unit": "条",
        "object": "official_website_question", "table": "t_help_center_question",
        "aliases": ["官网待处理咨询数", "待处理提问数", "未处理咨询数"],
        "description": "处理状态=未处理（question_status=0）的官网咨询数。",
    },
    {
        "name": "官网提问处理完成率(%)", "name_en": "official_question_done_rate",
        "agg_type": "NONE",
        "formula": (f"SUM(CASE WHEN {_ALIVE} AND question_status = 2 THEN 1 ELSE 0 END) "
                    f"* 100.0 / NULLIF(SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END), 0)"),
        "unit": "%",
        "object": "official_website_question", "table": "t_help_center_question",
        "aliases": ["官网咨询处理完成率", "提问完成率"],
        "description": "处理状态=处理完成（question_status=2）占比 ×100，分母为未删除提问数。",
    },
    {
        "name": "官网支持反馈数", "name_en": "official_support_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_support", "table": "t_help_center_support",
        "aliases": ["技术支持反馈数", "支持工单数", "售后反馈数"],
        "description": "支持站提交的技术支持反馈明细数（未删除）。",
    },
    {
        "name": "官网支持待处理数", "name_en": "official_support_pending", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND question_status = 0 THEN 1 ELSE 0 END)",
        "unit": "条",
        "object": "official_website_support", "table": "t_help_center_support",
        "aliases": ["支持工单待处理数"],
        "description": "处理状态=未处理的技术支持反馈数。",
    },
    {
        "name": "官网询盘数", "name_en": "official_inquiry_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_inquiry", "table": "t_help_center_official_request_info",
        "aliases": ["询盘数", "官网留言数", "联系我们提交数"],
        "description": "官网「联系我们/咨询」表单提交的询盘数（未删除）。",
    },
    {
        "name": "官网询盘待处理数", "name_en": "official_inquiry_pending", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND question_status = 0 THEN 1 ELSE 0 END)",
        "unit": "条",
        "object": "official_website_inquiry", "table": "t_help_center_official_request_info",
        "aliases": ["询盘待处理数"],
        "description": "处理状态=未处理的询盘数。",
    },
    {
        "name": "官网演示预约数", "name_en": "official_demo_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_demo_request",
        "table": "t_help_center_official_request_demo",
        "aliases": ["演示预约数", "试用预约数", "demo 预约数"],
        "description": "官网「预约演示」表单提交数（未删除）。",
    },
    {
        "name": "官网演示预约待处理数", "name_en": "official_demo_pending", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND question_status = 0 THEN 1 ELSE 0 END)",
        "unit": "条",
        "object": "official_website_demo_request",
        "table": "t_help_center_official_request_demo",
        "aliases": ["演示预约待处理数"],
        "description": "处理状态=未处理的演示预约数。",
    },
    {
        "name": "官网伙伴申请数", "name_en": "official_partner_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_partner_application",
        "table": "t_help_center_official_become_partner",
        "aliases": ["伙伴申请数", "合作伙伴申请数", "代理申请数"],
        "description": "官网「成为合作伙伴」表单提交数（未删除）。",
    },
    {
        "name": "官网伙伴申请待处理数", "name_en": "official_partner_pending", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND question_status = 0 THEN 1 ELSE 0 END)",
        "unit": "条",
        "object": "official_website_partner_application",
        "table": "t_help_center_official_become_partner",
        "aliases": ["伙伴申请待处理数"],
        "description": "处理状态=未处理的伙伴申请数。",
    },
    {
        "name": "官网邮件路由配置数", "name_en": "official_email_route_count", "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} THEN 1 ELSE 0 END)", "unit": "条",
        "object": "official_website_email_route", "table": "t_help_center_email_config",
        "aliases": ["邮件路由数", "表单收件配置数"],
        "description": "官网表单邮件路由配置行数（未删除）。",
    },
    {
        "name": "官网下载资源数", "name_en": "official_download_resource_count",
        "agg_type": "SUM",
        "formula": f"SUM(CASE WHEN {_ALIVE} AND is_valid = 1 THEN 1 ELSE 0 END)", "unit": "个",
        "object": "official_website_download_resource", "table": "t_help_center_download_center",
        "aliases": ["下载中心资源数", "上架资料数"],
        "description": "下载中心当前上架（未删除且 is_valid=1）的资源数。",
    },
]

# ── 维度字典（名称全局唯一，按对象加前缀；value_labels 与 doc 枚举同源）────────────
DIMENSIONS = [
    {"name": "官网提问创建日期", "name_en": "official_question_date",
     "object": "official_website_question", "table": "t_help_center_question",
     "column": "create_time", "category": "时间",
     "description": "在线咨询提交日期（按天）。", "aliases": ["咨询日期", "提问日期"]},
    {"name": "官网提问处理状态", "name_en": "official_question_status",
     "object": "official_website_question", "table": "t_help_center_question",
     "column": "question_status", "category": "属性", "value_labels": Q_STATUS,
     "description": "在线咨询处理状态（未处理/处理中/处理完成）。", "aliases": ["咨询状态"]},
    {"name": "官网提问来源渠道", "name_en": "official_question_source",
     "object": "official_website_question", "table": "t_help_center_question",
     "column": "question_source_type", "category": "属性", "value_labels": SOURCE_TYPE,
     "description": "咨询提交来源（官网在线提问/支持网站）。", "aliases": ["咨询来源"]},
    {"name": "官网支持反馈创建日期", "name_en": "official_support_date",
     "object": "official_website_support", "table": "t_help_center_support",
     "column": "create_time", "category": "时间",
     "description": "技术支持反馈提交日期（按天）。", "aliases": ["支持反馈日期"]},
    {"name": "官网支持反馈客户类型", "name_en": "official_support_customer_type",
     "object": "official_website_support", "table": "t_help_center_support",
     "column": "customer_type", "category": "属性", "value_labels": CUSTOMER_TYPE,
     "description": "提交反馈的客户类型（经销商/终端用户）。", "aliases": ["反馈客户类型"]},
    {"name": "官网支持反馈产品型号", "name_en": "official_support_product_model",
     "object": "official_website_support", "table": "t_help_center_support",
     "column": "product_model", "category": "属性", "value_labels": PRODUCT_MODEL,
     "description": "反馈涉及的产品型号（AS 100 / AS 200E / AS 260）。", "aliases": ["反馈产品型号"]},
    {"name": "官网支持反馈处理状态", "name_en": "official_support_status",
     "object": "official_website_support", "table": "t_help_center_support",
     "column": "question_status", "category": "属性", "value_labels": Q_STATUS,
     "description": "技术支持反馈处理状态。", "aliases": ["支持反馈状态"]},
    {"name": "官网询盘创建日期", "name_en": "official_inquiry_date",
     "object": "official_website_inquiry", "table": "t_help_center_official_request_info",
     "column": "create_time", "category": "时间",
     "description": "询盘提交日期（按天）。", "aliases": ["询盘日期"]},
    {"name": "官网询盘处理状态", "name_en": "official_inquiry_status",
     "object": "official_website_inquiry", "table": "t_help_center_official_request_info",
     "column": "question_status", "category": "属性", "value_labels": Q_STATUS,
     "description": "询盘处理状态。", "aliases": ["询盘状态"]},
    {"name": "官网演示预约创建日期", "name_en": "official_demo_date",
     "object": "official_website_demo_request",
     "table": "t_help_center_official_request_demo",
     "column": "create_time", "category": "时间",
     "description": "演示预约提交日期（按天）。", "aliases": ["预约日期", "demo 日期"]},
    {"name": "官网演示预约是否已购设备", "name_en": "official_demo_has_scanner",
     "object": "official_website_demo_request",
     "table": "t_help_center_official_request_demo",
     "column": "has_scanner", "category": "属性", "value_labels": YES_NO,
     "description": "预约人是否已拥有口扫设备（是/否）。", "aliases": ["预约是否已有口扫"]},
    {"name": "官网演示预约处理状态", "name_en": "official_demo_status",
     "object": "official_website_demo_request",
     "table": "t_help_center_official_request_demo",
     "column": "question_status", "category": "属性", "value_labels": Q_STATUS,
     "description": "演示预约处理状态。", "aliases": ["预约状态"]},
    {"name": "官网伙伴申请创建日期", "name_en": "official_partner_date",
     "object": "official_website_partner_application",
     "table": "t_help_center_official_become_partner",
     "column": "create_time", "category": "时间",
     "description": "伙伴申请提交日期（按天）。", "aliases": ["伙伴申请日期"]},
    {"name": "官网伙伴申请是否持有分销权", "name_en": "official_partner_distribution_rights",
     "object": "official_website_partner_application",
     "table": "t_help_center_official_become_partner",
     "column": "has_distribution_rights_for_any_scanner", "category": "属性",
     "value_labels": YES_NO,
     "description": "申请人是否已持有任一品牌口扫的分销权（是/否）。", "aliases": ["伙伴是否持有分销权"]},
    {"name": "官网伙伴申请处理状态", "name_en": "official_partner_status",
     "object": "official_website_partner_application",
     "table": "t_help_center_official_become_partner",
     "column": "question_status", "category": "属性", "value_labels": Q_STATUS,
     "description": "伙伴申请处理状态。", "aliases": ["伙伴申请状态"]},
    {"name": "官网下载资源类型", "name_en": "official_download_type",
     "object": "official_website_download_resource",
     "table": "t_help_center_download_center",
     "column": "download_type", "category": "属性", "value_labels": DOWNLOAD_TYPE,
     "description": "下载中心资源类型（文档/软件/远程协助工具）。", "aliases": ["下载类型"]},
    {"name": "官网下载资源是否有效", "name_en": "official_download_valid",
     "object": "official_website_download_resource",
     "table": "t_help_center_download_center",
     "column": "is_valid", "category": "属性", "value_labels": IS_VALID,
     "description": "下载资源是否有效（无效=已下架但未删除）。", "aliases": ["下载资源是否上架"]},
    {"name": "官网邮件路由区域", "name_en": "official_email_region",
     "object": "official_website_email_route", "table": "t_help_center_email_config",
     "column": "region", "category": "属性",
     "description": "表单邮件分派区域（MEA / EUROPE / APAC ex CN/IN / AMERICAS / China / India 等，"
                    "取值随业务扩张而增，不设枚举）。", "aliases": ["邮件路由区域"]},
]

# ── 敏感字段（PII 补登；email/phone 已被全局列名基线 block，不重复登记）────────────────
# 说明：公司名/诊所名/公司网站属业务实体而非个人标识，partial 脱敏会把不同公司合并成同一分组值
# （错数比泄露更隐蔽），故不脱敏；邮编同理。企业识别号用 hash（不可逆、仍可按值分组）。
SENSITIVE_FIELDS = [
    ("t_help_center_question", "first_name", "high", "partial", "官网咨询联系人名（PII）"),
    ("t_help_center_question", "last_name", "high", "partial", "官网咨询联系人姓（PII）"),
    ("t_help_center_support", "serial_number", "medium", "partial", "设备序列号（可关联到具体客户）"),
    ("t_help_center_official_request_info", "first_name", "high", "partial", "询盘联系人名（PII）"),
    ("t_help_center_official_request_info", "last_name", "high", "partial", "询盘联系人姓（PII）"),
    ("t_help_center_official_request_demo", "first_name", "high", "partial", "演示预约联系人名（PII）"),
    ("t_help_center_official_request_demo", "last_name", "high", "partial", "演示预约联系人姓（PII）"),
    ("t_help_center_official_become_partner", "first_name", "high", "partial", "伙伴申请联系人名（PII）"),
    ("t_help_center_official_become_partner", "last_name", "high", "partial", "伙伴申请联系人姓（PII）"),
    ("t_help_center_official_become_partner", "business_identification_number", "critical", "hash",
     "企业识别号（营业执照/税号级标识）"),
    ("t_help_center_email_config", "email_recipient", "medium", "partial", "表单收件邮箱"),
    ("t_help_center_email_config", "email_cc", "medium", "partial", "表单抄送邮箱"),
]

STEPS = ["preflight", "columns", "model", "products", "activate", "bindings",
         "dicts", "sensitive", "rename-arms", "verify"]

METRIC_CATEGORY = "官网"


# ── 运行期小工具 ──────────────────────────────────────────────────────────
def _log(msg: str) -> None:
    print(msg, flush=True)


def _ds_row(name: str) -> dict:
    row = execute_query(
        "SELECT id, name, site_code, db_type, database_name FROM adh_datasources WHERE name = %s",
        (name,), fetchone=True)
    if not row:
        raise RuntimeError(f"数据源未登记: {name}（先登记数据源再跑本脚本）")
    return row


def _model_by_name(name: str, ds_id: int):
    return execute_query(
        "SELECT id, name, status, kind, datasource_id, deploy_sites, object_count "
        "FROM adh_ontology_models WHERE name = %s "
        "AND COALESCE(datasource_id, 0) = COALESCE(%s, 0) "
        "AND status IN ('draft', 'active') ORDER BY updated_at DESC LIMIT 1",
        (name, ds_id), fetchone=True)


def _model_doc(model_id: int) -> dict:
    row = execute_query("SELECT json_content FROM adh_ontology_models WHERE id = %s",
                        (model_id,), fetchone=True)
    if not row:
        raise RuntimeError(f"模型不存在: {model_id}")
    return json.loads(row["json_content"] or "{}")


def _as_json_list(raw) -> list:
    """JSON 列（aliases/column_map）→ Python 容器；损坏值原样当空，不猜。"""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "null")
        except ValueError:
            return []
    return raw if isinstance(raw, (list, dict)) else []


def _aliases_of(raw) -> list[str]:
    val = _as_json_list(raw)
    return [str(a).strip() for a in val if str(a).strip()] if isinstance(val, list) else []


def _column_meta(ds_id: int, table: str) -> set[str]:
    rows = execute_query(
        "SELECT column_name FROM adh_column_metadata "
        "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
        (ds_id, table)) or []
    return {str(r["column_name"]).lower() for r in rows}


# ── canonical doc（唯一可编辑事实源）─────────────────────────────────────
def build_doc(ds_id: int) -> dict:
    """按 OBJ_SPECS 生成 canonical doc。

    物理列以「表.列」限定写入 properties[].column（与「联耀业务本体」同口径）：
    部署绑定层据此生成 column_map（`_column_map_from_props` 命中实存列即剥前缀），
    字典的 `_require_formula_matches_object` 亦按裸列名比对。
    """
    objects: list[dict] = []
    for spec in OBJ_SPECS:
        props: list[dict] = []
        for col in spec["columns"]:
            name, biz, typ = col[0], col[1], col[2]
            enum = dict(col[3]) if len(col) > 3 and isinstance(col[3], dict) else []
            props.append({
                "column": f"{spec['table']}.{name}",
                "name": biz,
                "type": typ,
                "is_key": name == "id",
                "description": biz,
                "enum": enum,
            })
        objects.append({
            "key": spec["key"],
            "display_name": spec["display_name"],
            "aliases": list(spec["aliases"]),
            "description": spec["description"],
            "primary_table": spec["table"],
            "source_ref": f"stardb.{spec['table']}",
            "properties": props,
            "links": [dict(lk) for lk in LINKS.get(spec["key"], [])],
            "metrics": list(spec.get("metrics") or []),
        })
    return {
        "datasource_name": DS_NAME,
        "domain": DOMAIN,
        "description": DESCRIPTION,
        "kind": "business",
        "sources": [],
        "objects": objects,
    }


def _assert_spec_covers(existing_doc: dict, doc: dict) -> None:
    """库里已有对象/属性必须是本脚本规格的子集 —— 否则拒绝整份覆盖（宁缺勿错）。

    为什么拒：本 doc 由规格全量重算，若线上被人工加过对象/属性，覆盖即静默删除
    他人成果（no-silent-degradation 的“掩藏”同源）。此时应由人决定：是并入规格，
    还是先把人工改动迁走。
    """
    from backend.modules.catalog.services.ontology_service import object_identity_key
    ours = {object_identity_key(o.get("key") or ""): o for o in doc["objects"]}
    extra_objs: list[str] = []
    extra_cols: list[str] = []
    for o in existing_doc.get("objects") or []:
        ik = object_identity_key(o.get("key") or o.get("object_key") or "")
        mine = ours.get(ik)
        if mine is None:
            extra_objs.append(str(o.get("key") or o.get("object_key") or ""))
            continue
        my_cols = {str(p.get("column") or "").split(".")[-1].lower()
                   for p in mine.get("properties") or []}
        for p in o.get("properties") or []:
            c = str(p.get("column") or "").split(".")[-1].lower()
            if c and c not in my_cols:
                extra_cols.append(f"{o.get('key')}.{c}")
    if extra_objs or extra_cols:
        raise RuntimeError(
            "库里已有对象/属性不在本脚本规格内，拒绝整份覆盖（先人工确认是并入规格还是迁走改动）："
            f"对象={extra_objs or '无'} 属性={(extra_cols[:10] or '无')}"
            + (" 等" if len(extra_cols) > 10 else ""))


# ── 1. preflight：写入前的可诊断检查（任一不过即拒绝落库，不半截写入）───────
def _preflight(ds: dict, apply: bool) -> None:
    from backend.modules.catalog.services.ontology_service import object_identity_key

    ds_id = int(ds["id"])
    problems: list[str] = []

    if str(ds.get("site_code") or "") != "cn":
        problems.append(f"数据源 {DS_NAME} 站点={ds.get('site_code')!r}，本批只建模中国站(cn)")
    for code, _ds_name in SITE_DS:
        site = execute_query("SELECT site_code, display_name, is_active FROM adh_sites WHERE site_code = %s",
                             (code,), fetchone=True)
        if not site or not int(site.get("is_active") or 0):
            problems.append(f"站点未启用或缺失: {code}")

    # 表行（adh_table_info）：没有表行的对象无法生成 bound 契约（catalog_ref 缺失）
    tables = [s["table"] for s in OBJ_SPECS]
    ph = ",".join(["%s"] * len(tables))
    info_rows = execute_query(
        f"SELECT table_name, size_class, est_rows, query_mode, allow_full_scan FROM adh_table_info "
        f"WHERE datasource_id = %s AND table_name IN ({ph})",
        tuple([ds_id] + tables)) or []
    info = {str(r["table_name"]): r for r in info_rows}
    for t in tables:
        r = info.get(t)
        if not r:
            problems.append(f"adh_table_info 无表行: {DS_NAME}.{t}")
        else:
            _log(f"[preflight] 表 {t}: size_class={r.get('size_class')} est_rows={r.get('est_rows')} "
                 f"query_mode={r.get('query_mode')} allow_full_scan={r.get('allow_full_scan')}")

    # 列元数据缺口（不阻断：columns 步骤负责同步；这里只如实报告现状）
    for spec in OBJ_SPECS:
        have = _column_meta(ds_id, spec["table"])
        missing = [c[0] for c in spec["columns"] if c[0].lower() not in have]
        if missing:
            _log(f"[preflight] {spec['table']} 列元数据缺 {len(missing)} 列（columns 步骤补齐）: "
                 f"{missing[:8]}{' 等' if len(missing) > 8 else ''}")

    # 对象 key 占用：同 key 属别的模型，或身份相同但写法不同（激活会因重复 key 阻断）
    mine = {object_identity_key(s["key"]): s["key"] for s in OBJ_SPECS}
    our_row = _model_by_name(MODEL_NAME, ds_id)
    our_id = int(our_row["id"]) if our_row else 0
    for r in execute_query(
            "SELECT DISTINCT object_key, model_id FROM adh_ontology_objects WHERE is_active = 1") or []:
        key = str(r["object_key"] or "")
        ik = object_identity_key(key)
        if ik not in mine:
            continue
        if key != mine[ik]:
            problems.append(f"对象 key 身份与既有对象冲突: {key}（规格 {mine[ik]}）")
        elif our_id and int(r["model_id"] or 0) != our_id:
            problems.append(f"对象 key「{key}」已被模型 {r['model_id']} 展开占用")

    # 字典名/句柄/别名冲突（create_metric/create_dimension 的查重是 name OR name_en，全局）
    ours_objects = {s["object"] for s in METRICS} | {s["object"] for s in DIMENSIONS}
    for table, specs, label in (("adh_metrics", METRICS, "指标"),
                                ("adh_dimensions", DIMENSIONS, "维度")):
        taken: dict[str, str] = {}
        alias_owner: dict[str, str] = {}
        for r in execute_query(
                f"SELECT name, name_en, COALESCE(aliases, '[]') AS aliases, "
                f"COALESCE(bound_object_key, '') AS bok FROM {table}") or []:
            if str(r.get("bok") or "") in ours_objects:
                continue   # 本对象的既有行正是要收敛的对象，不算冲突
            for n in (r.get("name"), r.get("name_en")):
                n = str(n or "").strip()
                if n:
                    taken.setdefault(n, str(r.get("name") or ""))
            for a in _aliases_of(r.get("aliases")):
                alias_owner.setdefault(a, str(r.get("name") or ""))
        for spec in specs:
            for n in (spec["name"], spec["name_en"]):
                if n in taken:
                    problems.append(f"{label}名/句柄已被「{taken[n]}」占用: {n}（{spec['name']}）")
            for a in spec.get("aliases") or []:
                if a in taken or a in alias_owner:
                    problems.append(f"{label}别名冲突: {a}（{spec['name']}）")

    if problems:
        raise RuntimeError("preflight 未通过，拒绝任何写入：\n- " + "\n- ".join(problems))
    _log(f"[preflight] 通过：{len(OBJ_SPECS)} 对象 / {sum(len(s['columns']) for s in OBJ_SPECS)} 属性 / "
         f"{len(METRICS)} 指标 / {len(DIMENSIONS)} 维度 / {len(SENSITIVE_FIELDS)} 敏感字段；"
         f"主源 {DS_NAME}，站点 " + ", ".join(f"{c}→{d}" for c, d in SITE_DS))


# ── 2. columns：补列元数据（support 此前 0 列、email_config 缺 1 列）──────────
def _columns(ds: dict, apply: bool) -> None:
    from backend.modules.catalog.services.metadata_sync import sync_table_columns

    ds_id = int(ds["id"])
    for spec in OBJ_SPECS:
        table = spec["table"]
        want = [c[0] for c in spec["columns"]]
        have = _column_meta(ds_id, table)
        missing = [c for c in want if c.lower() not in have]
        if not apply:
            _log(f"[columns] dry-run {table}: 现有 {len(have)} 列 / 规格 {len(want)} 列；"
                 f"缺 {missing or '无'}")
            continue
        res = sync_table_columns(ds_id, table)
        _log(f"[columns] {table}: total={res.get('total_columns')} +{res.get('inserted')} "
             f"~{res.get('updated')} -{res.get('deleted')}")
        still = [c for c in want if c.lower() not in _column_meta(ds_id, table)]
        if still:
            raise RuntimeError(
                f"{table} 同步后仍缺列 {still}：源表结构与建模规格不一致，先核对表结构再继续")


# ── 3. model：草案新建/对齐 + deploy_sites ────────────────────────────────
def _model(ds: dict, apply: bool):
    from backend.core.site_service import set_model_deploy_sites
    from backend.modules.catalog.services import ontology_service

    ds_id = int(ds["id"])
    doc = build_doc(ds_id)
    row = _model_by_name(MODEL_NAME, ds_id)
    model_id = int(row["id"]) if row else 0
    if row:
        _log(f"[model] 已存在「{MODEL_NAME}」(id={model_id}, status={row['status']})，按规格整份对齐")
        if apply:
            _assert_spec_covers(_model_doc(model_id), doc)
    else:
        stale = execute_query(
            "SELECT id, name FROM adh_ontology_models WHERE datasource_id = %s "
            "AND kind = 'business' AND status = 'draft' ORDER BY updated_at DESC",
            (ds_id,)) or []
        if stale:
            model_id = int(stale[0]["id"])
            _log(f"[model] 复用该源已有草案 (id={model_id}, name={stale[0]['name']})，按规格整份对齐")
            if apply:
                _assert_spec_covers(_model_doc(model_id), doc)

    if not apply:
        _log(f"[model] dry-run：{'对齐到 ' + str(model_id) if model_id else '将新建草案'}；"
             f"对象 {len(doc['objects'])} 个；deploy_sites="
             + ", ".join(f"{c}→{d}" for c, d in SITE_DS))
        return model_id or None

    if not model_id:
        rec = ontology_service.save_generated_draft(
            ds_id, [dict(o) for o in doc["objects"]], domain=DOMAIN,
            description=DESCRIPTION, append=False, created_by=CREATED_BY)
        model_id = int(rec["id"])
        _log(f"[model] 草案已建 id={model_id}（name={rec.get('name')}）")

    saved = ontology_service.save_draft(
        model_id, json.dumps(doc, ensure_ascii=False, indent=2), name=MODEL_NAME)
    for w in saved.get("validation_warnings") or []:
        _log(f"[model][warn] {w}")
    set_model_deploy_sites(
        model_id, [{"site_code": c, "datasource": d} for c, d in SITE_DS])
    _log(f"[model] doc 已保存（name={MODEL_NAME}）；deploy_sites 已声明 "
         + ", ".join(f"{c}→{d}" for c, d in SITE_DS))
    return model_id


# ── 4. products：激活门禁 P2.3（对象主表必须是数据产品）──────────────────────
def _products(model_id, apply: bool) -> None:
    from backend.scripts import claim_ontology_tables_as_products as claim

    if not model_id:
        _log("[products] dry-run：模型尚未落库；激活前需把 "
             + ", ".join(f"{DS_NAME}.{s['table']}" for s in OBJ_SPECS) + " 认领为数据产品")
        return
    res = claim.claim_model(model_id, dry_run=not apply, owner=CREATED_BY)
    _log(f"[products] {'dry-run 待认领' if not apply else '已认领'}: "
         f"{res.get('claimed') or '（无缺项）'} | 门禁剩余: {res.get('gate_missing') or '（空=通过）'}")
    for w in res.get("warnings") or []:
        _log(f"[products][warn] {w}")
    if apply and (res.get("gate_missing") or []):
        raise RuntimeError(f"数据产品门禁未通过，激活会被拒: {res['gate_missing']}")


# ── 5. activate：级联（展开对象 → 部署绑定 → 枚举联动 → 图谱 → 知识库）──────
def _activate(model_id, apply: bool) -> None:
    from backend.modules.catalog.services import ontology_service

    if not model_id:
        _log("[activate] 模型未落库，跳过")
        return
    model = ontology_service.get_model(model_id)
    if str(model.get("status")) == "active":
        # active 模型再调 activate 会直接 return（不跑级联），如实说明而非假装刚激活
        _log(f"[activate] 已是 active(id={model_id})；本脚本 model 步骤的 save_draft 已跑过级联")
        return
    if not apply:
        _log("[activate] dry-run：将激活（级联：展开对象→部署绑定→枚举联动→图谱重建→知识库同步）")
        return
    m = ontology_service.activate(model_id)
    _log(f"[activate] 已激活 id={model_id} status={m.get('status')} objects={m.get('object_count')}")


# ── 6. bindings：部署绑定层校验（物理事实源，doc 不持绑定）────────────────
def _bindings(model_id, apply: bool) -> None:
    if not model_id:
        _log("[bindings] 模型未落库，跳过")
        return
    rows = execute_query(
        "SELECT object_key, datasource_name, physical_table, bind_kind, sync_state, column_map "
        "FROM adh_ontology_bindings WHERE model_id = %s AND status = 'active' ORDER BY object_key",
        (model_id,)) or []
    if not apply:
        _log(f"[bindings] dry-run：现有 {len(rows)} 条生效绑定行；"
             f"将校验 {len(OBJ_SPECS)} 对象的主源/物理表/列映射")
        return
    by_key = {str(r.get("object_key") or ""): r for r in rows}
    problems: list[str] = []
    for spec in OBJ_SPECS:
        r = by_key.get(spec["key"])
        if not r:
            problems.append(f"{spec['key']} 无生效部署绑定行（激活未跑或对象未展开）")
            continue
        if str(r.get("physical_table") or "") != spec["table"]:
            problems.append(f"{spec['key']} 物理表={r.get('physical_table')}，规格 {spec['table']}")
        if str(r.get("datasource_name") or "") != DS_NAME:
            problems.append(f"{spec['key']} 绑定源={r.get('datasource_name')}，规格 {DS_NAME}")
        if str(r.get("sync_state") or "") != "bound":
            problems.append(f"{spec['key']} sync_state={r.get('sync_state')}（非 bound = 列映射有漂移）")
        cmap = _as_json_list(r.get("column_map"))
        mapped = {str(v).split(".")[-1].lower() for v in (cmap or {}).values()} \
            if isinstance(cmap, dict) else set()
        missing = [c[0] for c in spec["columns"] if c[0].lower() not in mapped]
        if missing:
            problems.append(f"{spec['key']} column_map 缺列 {missing[:8]}"
                            + (" 等" if len(missing) > 8 else ""))
    if problems:
        raise RuntimeError("部署绑定层校验未通过: " + "; ".join(problems[:10]))
    extra = set(by_key) - {s["key"] for s in OBJ_SPECS}
    _log(f"[bindings] {len(rows)} 条生效绑定行全部 bound / 主源 {DS_NAME} / 列映射覆盖对象属性"
         + (f"；另有非规格对象绑定 {sorted(extra)}" if extra else ""))


# ── 7. dicts：指标/维度字典（幂等收敛，写入口 = metrics_service）──────────────
def _dicts(apply: bool) -> None:
    from backend.modules.catalog.services import metrics_service

    created: list[str] = []
    aligned: list[str] = []
    for spec in METRICS:
        payload = {
            "display_name": spec["name"], "name": spec["name_en"],
            "calculation_type": spec["agg_type"], "expression": spec["formula"],
            "unit": spec["unit"], "description": spec["description"],
            "aliases": spec["aliases"], "category": METRIC_CATEGORY,
            "bound_object_key": spec["object"], "target_table": spec["table"],
        }
        row = execute_query(
            "SELECT id FROM adh_metrics WHERE name = %s OR name_en = %s LIMIT 1",
            (spec["name"], spec["name_en"]), fetchone=True)
        if not apply:
            (aligned if row else created).append(f"指标:{spec['name']}")
            continue
        if row:
            metrics_service.update_metric(int(row["id"]), payload)
            aligned.append(f"指标:{spec['name']}(id={row['id']})")
        else:
            res = metrics_service.create_metric(payload)
            created.append(f"指标:{spec['name']}(id={res['id']})")
    for spec in DIMENSIONS:
        payload = {
            "display_name": spec["name"], "name_en": spec["name_en"],
            "target_table": spec["table"], "target_column": spec["column"],
            "category": spec["category"], "description": spec["description"],
            "bound_object_key": spec["object"], "aliases": spec["aliases"],
            "value_labels": spec.get("value_labels"), "certified": 0,
        }
        row = execute_query(
            "SELECT id FROM adh_dimensions WHERE name = %s OR (name_en <> '' AND name_en = %s) LIMIT 1",
            (spec["name"], spec["name_en"]), fetchone=True)
        if not apply:
            (aligned if row else created).append(f"维度:{spec['name']}")
            continue
        if row:
            metrics_service.update_dimension(int(row["id"]), payload)
            aligned.append(f"维度:{spec['name']}(id={row['id']})")
        else:
            res = metrics_service.create_dimension(payload)
            created.append(f"维度:{spec['name']}(id={res['id']})")

    if not apply:
        _log(f"[dicts] dry-run 将新建 {len(created)} 条、对齐 {len(aligned)} 条")
        _log(f"[dicts] 待新建: {created}")
        _log(f"[dicts] 待对齐: {aligned}")
        return
    _log(f"[dicts] 已建 {len(created)} 条: {created}")
    _log(f"[dicts] 已对齐 {len(aligned)} 条: {aligned}")

    # 落库后回读：target_table/target_column/bound_object_key 缺一，planner 就加载不到该口径
    problems: list[str] = []
    for spec in METRICS:
        r = execute_query(
            "SELECT name, formula, agg_type, target_table, COALESCE(bound_object_key, '') AS bok, is_active "
            "FROM adh_metrics WHERE name = %s LIMIT 1", (spec["name"],), fetchone=True)
        if not r:
            problems.append(f"指标未落库: {spec['name']}")
            continue
        if str(r.get("target_table") or "") != spec["table"]:
            problems.append(f"指标 {spec['name']} target_table={r.get('target_table')}")
        if str(r.get("bok") or "") != spec["object"]:
            problems.append(f"指标 {spec['name']} bound_object_key={r.get('bok')}")
        if str((r.get("formula") or "").strip()) != spec["formula"].strip():
            problems.append(f"指标 {spec['name']} 公式与规格不一致")
        if not int(r.get("is_active") or 0):
            problems.append(f"指标 {spec['name']} is_active=0（语义层检索不到）")
    for spec in DIMENSIONS:
        r = execute_query(
            "SELECT name, target_table, target_column, COALESCE(bound_object_key, '') AS bok, is_active "
            "FROM adh_dimensions WHERE name = %s LIMIT 1", (spec["name"],), fetchone=True)
        if not r:
            problems.append(f"维度未落库: {spec['name']}")
            continue
        if str(r.get("target_table") or "") != spec["table"]:
            problems.append(f"维度 {spec['name']} target_table={r.get('target_table')}")
        if str(r.get("target_column") or "") != spec["column"]:
            problems.append(f"维度 {spec['name']} target_column={r.get('target_column')}")
        if str(r.get("bok") or "") != spec["object"]:
            problems.append(f"维度 {spec['name']} bound_object_key={r.get('bok')}")
        if not int(r.get("is_active") or 0):
            problems.append(f"维度 {spec['name']} is_active=0（语义层检索不到）")
    if problems:
        raise RuntimeError("字典回读校验未通过（planner 将加载不到这些口径）: "
                           + "; ".join(problems[:10]))
    _log(f"[dicts] 回读校验通过：{len(METRICS)} 指标 / {len(DIMENSIONS)} 维度的物理锚点与归属对象齐备")


# ── 8. sensitive：PII 补登（唯一产品入口 = HTTP /api/security/sensitive-fields）──
def _admin_api(method: str, path: str, **kw):
    """敏感字段写入口只有 REST（权限码 + 审计在中间件/路由上），脚本不得直插表。"""
    import requests

    from backend.modules.auth.services.auth_service import create_access_token

    url = WEB_BASE.rstrip("/") + path
    headers = {"Authorization": "Bearer " + create_access_token({"user_id": ADMIN_USER_ID})}
    timeout = kw.pop("timeout", 30)
    try:
        resp = requests.request(method, url, headers=headers, timeout=timeout, **kw)
    except Exception as exc:  # noqa: BLE001 — 不可达要显式告知怎么补跑，不当已登记
        raise RuntimeError(
            f"敏感字段唯一写入口是 HTTP API（{WEB_BASE}），当前不可达：{exc}。"
            "请先 ./start-all.sh -d 起 web，再重跑 --only sensitive --apply") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {path} 响应不是 JSON: {resp.text[:200]}") from exc


def _find_sensitive(ds_id: int, table: str, column: str):
    data = _admin_api("GET", "/api/security/sensitive-fields", params={
        "workspace_id": 0, "datasource_id": ds_id, "keyword": column, "page": 1, "size": 100})
    for it in data.get("items") or []:
        if (str(it.get("table_name") or "") == table
                and str(it.get("column_name") or "").lower() == column.lower()
                and int(it.get("datasource_id") or 0) == int(ds_id)):
            return it
    return None


def _sensitive(ds: dict, apply: bool) -> None:
    ds_id = int(ds["id"])
    if not apply:
        _log(f"[sensitive] dry-run：将按 (ds={ds_id}, 表, 列) 查重后新增/对齐 "
             f"{len(SENSITIVE_FIELDS)} 条表级规则（全局 email/phone 列名基线已在库，不重复登记）")
        for table, col, level, mask, desc in SENSITIVE_FIELDS:
            _log(f"[sensitive]   {table}.{col}: {level}/{mask} — {desc}")
        return

    created: list[str] = []
    aligned: list[str] = []
    for table, col, level, mask, desc in SENSITIVE_FIELDS:
        row = _find_sensitive(ds_id, table, col)
        if row is None:
            _admin_api("POST", "/api/security/sensitive-fields", params={"workspace_id": 0}, json={
                "datasource_id": ds_id, "table_name": table, "column_name": col,
                "sensitivity_level": level, "mask_type": mask, "description": desc})
            created.append(f"{table}.{col}")
            continue
        if int(row.get("workspace_id") or 0) != 0:
            raise RuntimeError(
                f"{table}.{col} 命中既有行 id={row.get('id')} workspace_id={row.get('workspace_id')}，"
                "该行所属工作空间与本脚本(0)不同，PUT 会被路由过滤而静默不生效；请先在「敏感数据」页确认归属")
        if (str(row.get("mask_type") or "") != mask
                or str(row.get("sensitivity_level") or "") != level
                or str(row.get("description") or "") != desc):
            _admin_api("PUT", f"/api/security/sensitive-fields/{row['id']}",
                       params={"workspace_id": 0}, json={
                           "sensitivity_level": level, "mask_type": mask, "description": desc})
            aligned.append(f"{table}.{col}")

    # 回读确认：写接口报成功不等于已生效（PUT 的 WHERE 带 workspace_id）
    problems = [f"{t}.{c}" for t, c, level, mask, _d in SENSITIVE_FIELDS
                if not (_find_sensitive(ds_id, t, c) or {}).get("mask_type") == mask]
    if problems:
        raise RuntimeError(f"敏感字段回读未生效: {problems[:10]}")
    _log(f"[sensitive] 已新增 {len(created)} 条: {created}")
    _log(f"[sensitive] 已对齐 {len(aligned)} 条: {aligned}")


# ── 9. rename-arms：可观测侧模型改名（save_draft 级联，绑定行前后对比不劣化）──
def _arms_snapshot(model_id: int) -> dict:
    row = execute_query(
        "SELECT name, status, deploy_sites, object_count FROM adh_ontology_models WHERE id = %s",
        (model_id,), fetchone=True) or {}
    keys = [
        f"{r.get('object_key')}|{r.get('datasource_name')}|{r.get('physical_table')}|{r.get('sync_state')}"
        for r in execute_query(
            "SELECT object_key, datasource_name, physical_table, sync_state FROM adh_ontology_bindings "
            "WHERE model_id = %s AND status = 'active' ORDER BY object_key", (model_id,)) or []
    ]
    return {"name": str(row.get("name") or ""), "status": str(row.get("status") or ""),
            "deploy_sites": str(row.get("deploy_sites") or ""),
            "object_count": int(row.get("object_count") or 0), "binding_keys": keys}


def _purge_legacy_arms_doc(model_id: int, apply: bool) -> None:
    """下线旧模型名对应的上云文档（改名后必做，幂等）。

    为什么必须单独处理：同步逻辑只按**当前标题**删旧文档（`_delete_old_sources(nb, 新标题)`），
    改名后标题变了 → 云端残留「本体模型-<旧名>.md」旧口径文档，检索仍会命中旧名，
    等于改名没生效。这里按旧名显式下线（走既有 `remove_model_from_qmind`，不直连云端）。
    """
    from backend.modules.catalog.services import ontology_service, ontology_kb_sync

    if not apply:
        _log(f"[rename-arms] dry-run：将下线旧名上云文档「{ARMS_MODEL_NAME_OLD}」")
        return
    model = ontology_service.get_model(model_id)
    res = ontology_kb_sync.remove_model_from_qmind(ARMS_MODEL_NAME_OLD, model=model)
    # removed 计数不可信：qmind `source delete` 成功时返回非 JSON 文本，`_run_cli` 会计 0（删除已生效）。
    # 所以以**列表复查**为准（fail-loud）：旧标题仍在就是没清掉，不许当成功。
    title = f"{ontology_kb_sync.TITLE_PREFIX}{ARMS_MODEL_NAME_OLD}.md"
    for target in ontology_kb_sync.sync_targets_for_model(model):
        data, err = ontology_kb_sync._run_cli_ex(
            ["source", "list", "--nb", str(target.get("notebook_id") or ""), "--format", "json"])
        if data is None:
            raise RuntimeError(f"旧名上云文档清理后无法核验（source list 失败）: {err}")
        if any((s.get("title") or "") == title for s in data.get("sources") or []):
            raise RuntimeError(f"旧名上云文档仍在（{title}），云端会继续命中旧口径")
    _log(f"[rename-arms] 旧名上云文档已下线并核验（removed 计数={res.get('removed')}，"
         f"qmind delete 回非 JSON 时该计数为 0，以列表复查为准）")


def _rename_arms(apply: bool) -> None:
    from backend.modules.catalog.services import ontology_service

    row = execute_query(
        "SELECT id, name, json_content FROM adh_ontology_models "
        "WHERE name IN (%s, %s) AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
        (ARMS_MODEL_NAME_NEW, ARMS_MODEL_NAME_OLD), fetchone=True)
    if not row:
        _log("[rename-arms] 未找到 active 的可观测模型（旧名/新名都不存在）；本脚本不建模 RUM，跳过")
        return
    model_id = int(row["id"])
    before = _arms_snapshot(model_id)
    if before["name"] == ARMS_MODEL_NAME_NEW:
        _log(f"[rename-arms] 已是「{ARMS_MODEL_NAME_NEW}」(id={model_id})")
        _purge_legacy_arms_doc(model_id, apply)
        return
    if not apply:
        _log(f"[rename-arms] dry-run：「{before['name']}」(id={model_id}) → 「{ARMS_MODEL_NAME_NEW}」；"
             f"绑定 {len(before['binding_keys'])} 行、deploy_sites={before['deploy_sites']} 均不变")
        return

    doc = json.loads(row["json_content"] or "{}")
    saved = ontology_service.save_draft(
        model_id, json.dumps(doc, ensure_ascii=False, indent=2), name=ARMS_MODEL_NAME_NEW)
    for w in saved.get("validation_warnings") or []:
        _log(f"[rename-arms][warn] {w}")

    after = _arms_snapshot(model_id)
    problems = []
    if after["name"] != ARMS_MODEL_NAME_NEW:
        problems.append(f"改名未生效（库内仍是 {after['name']}）")
    if after["status"] != before["status"]:
        problems.append(f"状态变化 {before['status']}→{after['status']}")
    if after["deploy_sites"] != before["deploy_sites"]:
        problems.append(f"deploy_sites 被改写: {before['deploy_sites']} → {after['deploy_sites']}")
    lost = set(before["binding_keys"]) - set(after["binding_keys"])
    if lost:
        problems.append(f"绑定行劣化（改名不该动绑定层）: {sorted(lost)}")
    if problems:
        raise RuntimeError("可观测模型改名后校验未通过: " + "; ".join(problems))
    _log(f"[rename-arms] 「{before['name']}」→「{after['name']}」(id={model_id})；"
         f"绑定 {len(after['binding_keys'])} 行未变、deploy_sites 未变")
    _purge_legacy_arms_doc(model_id, apply)


# ── 10. verify：真实取数（正）+ 站点边界拒绝（负）────────────────────────────
def _verify(ds: dict, apply: bool) -> None:
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.execute import PolicyContext, execute

    if not apply:
        _log("[verify] dry-run：跳过端到端取数验证（apply 时跑 3 个正例 + 1 个站点边界负例）")
        return
    ds_id = int(ds["id"])
    ctx = PolicyContext(
        user_id=ADMIN_USER_ID, username="admin", role="admin", workspace_id=0,
        datasource_id=ds_id, question="脚本自检：官网业务本体端到端取数")
    checks = [
        ({"object": "official_website_question", "metrics": ["官网提问数"]}, "官网提问数"),
        ({"object": "official_website_support", "metrics": ["官网支持反馈数"],
          "dimensions": ["官网支持反馈处理状态"]}, "官网支持反馈数"),
        ({"object": "official_website_download_resource", "metrics": ["官网下载资源数"]},
         "官网下载资源数"),
    ]
    for intent, want_col in checks:
        try:
            res = execute(intent, ctx)
        except Exception as exc:  # noqa: BLE001 — 失败信息必须原样带出（含 SemanticError 文案）
            raise RuntimeError(f"[verify] 对象 {intent['object']} 取数失败: {exc}") from exc
        cols = [str(getattr(c, "name", "")) for c in res.columns]
        if want_col not in cols:
            raise RuntimeError(f"[verify] {intent['object']} 结果列里没有「{want_col}」: {cols}")
        if int(res.row_count or 0) <= 0:
            raise RuntimeError(
                f"[verify] {intent['object']} 取「{want_col}」得到 0 行：口径或权限有误，不得当成功")
        _log(f"[verify] ✓ {intent['object']} → 列={cols} 行数={res.row_count} "
             f"首行={res.rows[0] if res.rows else []}")

    # 负例（护栏 §5 负向验证）：本模型只声明 cn→prd-cn，拿国际站源查该对象必须被拒，
    # 不得路由到别站/别库拿“看起来能用的数”。
    other = execute_query(
        "SELECT id, name, site_code FROM adh_datasources "
        "WHERE site_code IS NOT NULL AND site_code <> '' AND site_code <> 'cn' "
        "ORDER BY is_default DESC, id LIMIT 1", fetchone=True)
    if not other:
        raise RuntimeError("[verify] 本环境没有中国站以外的数据源，负向用例无法执行（不得跳过当通过）")
    binding, warns = resolve_binding("official_website_question", datasource_id=int(other["id"]))
    if binding is not None:
        raise RuntimeError(
            f"[verify] 负向失败：用「{other['name']}」(站点 {other['site_code']}) 仍解析到绑定 "
            f"{binding.datasource_name}.{binding.physical_table}，站点边界失效")
    if not any("不适用站点" in w for w in warns or []):
        raise RuntimeError(f"[verify] 负向告警文案不符预期（应为「不适用站点」）: {warns}")
    _log(f"[verify] ✓ 负向通过：「{other['name']}」(站点 {other['site_code']}) 被拒：{warns[0]}")


# ── CLI ────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="构建「官网业务本体」（t_help_center* 七表，仅中国站）+ 可观测侧模型改名")
    ap.add_argument("--apply", action="store_true", help="真正落库/发布（缺省 dry-run）")
    ap.add_argument("--only", default="", help="只跑指定步骤（逗号分隔）: " + ",".join(STEPS))
    ap.add_argument("--skip", default="", help="跳过指定步骤（逗号分隔）")
    args = ap.parse_args(argv)

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = sorted((set(only) | skip) - set(STEPS))
    if unknown:
        raise SystemExit(f"未知步骤: {unknown}；可选: {STEPS}")
    steps = [s for s in STEPS if (not only or s in only) and s not in skip]
    if not steps:
        raise SystemExit("没有可执行步骤（--only/--skip 过滤后为空）")

    ds = _ds_row(DS_NAME)
    _log(f"== 官网业务本体：主源 {DS_NAME}(id={ds['id']}, site={ds.get('site_code')}) | "
         f"{'APPLY' if args.apply else 'DRY-RUN'} | 步骤={steps} ==")

    model_id = None
    if "preflight" in steps:
        _preflight(ds, args.apply)
    if "columns" in steps:
        _columns(ds, args.apply)
    if "model" in steps:
        model_id = _model(ds, args.apply)
    elif {"products", "activate", "bindings"} & set(steps):
        row = _model_by_name(MODEL_NAME, int(ds["id"]))
        model_id = int(row["id"]) if row else None
        _log(f"[model] 未在本轮执行；沿用现有模型 id={model_id}")
    if "products" in steps:
        _products(model_id, args.apply)
    if "activate" in steps:
        _activate(model_id, args.apply)
    if "bindings" in steps:
        _bindings(model_id, args.apply)
    if "dicts" in steps:
        _dicts(args.apply)
    if "sensitive" in steps:
        _sensitive(ds, args.apply)
    if "rename-arms" in steps:
        _rename_arms(args.apply)
    if "verify" in steps:
        _verify(ds, args.apply)

    _log(f"== 完成（{'APPLY' if args.apply else 'DRY-RUN'}）==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())