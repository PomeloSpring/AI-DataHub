"""AS-BOT 系统能力本体 · 声明式目录（项目版本发布，非数据驱动）。

系统本体（`adh_ontology_models.kind='system'`）回答的是「平台自身有哪些能力、边界在哪」，
不是某个业务域的数据语义。它随项目版本维护（`backend/scripts/refresh_system_ontology.py`
合并落库），不走 AS-BOT 写通道；业务能力变化时**同批**更新本文件，避免本体与代码漂移
（历史问题：`adh_wakers` / `adh_as_bot_approvals` 两张表已不存在，本体仍在描述它们）。

建模纪律：
- 每个对象只登记**业务可读属性**，物理列名放 `column`（服务端使用，投影只回 `name`）；
  凭据类列（password/hash/token/secret/host/url/api_key/connection）、
  内容正文（sql/content/question/answer/detail）与基础设施地址一律不登记。
- 行为（actions）只声明 AI 侧真实存在的能力；数据同步/DAG/UDF 不登记可执行行为，
  并在对象描述里显式写明「运维专属，不对 AI 开放」（data-sync-engineering §2）。
- 关系（links）的 target 必须是本目录内对象 key；规则/场景对行为与对象的引用必须可解析
  （`ontology_service.find_m234_issues` 在保存/激活时会校验）。
"""


def _p(col: str, name: str, typ: str, key: bool = False, desc: str = "") -> tuple:
    return (col, name, typ, key, desc)


def _l(target: str, rel: str, card: str, desc: str) -> dict:
    return {"target": target, "type": rel, "cardinality": card, "description": desc}


def _a(key: str, label: str, effect: str, *, risk: str = "", approval: bool = False,
       read_only: bool = False, params: dict | None = None) -> dict:
    out: dict = {
        "key": key,
        "label": label,
        "effect": effect,
        "risk": risk,
        "requires_approval": approval,
        "read_only": read_only,
        "params_schema": params or {},
    }
    return out


def _obj(key: str, display: str, aliases: list, desc: str, table: str,
         props: list, links: list | None = None, actions: list | None = None,
         metrics: list | None = None) -> dict:
    """组件装对象；`column` 自动带上主表前缀（skill 等无表对象传空列）。"""
    properties = []
    for spec in props:
        col, name, typ, is_key, pdesc = spec
        properties.append({
            "column": f"{table}.{col}" if (table and col) else col,
            "name": name,
            "type": typ,
            "is_key": bool(is_key),
            "description": pdesc,
        })
    return {
        "key": key,
        "display_name": display,
        "aliases": list(aliases),
        "description": desc,
        "primary_table": table,
        "properties": properties,
        "actions": [dict(x) for x in (actions or [])],
        "links": [dict(x) for x in (links or [])],
        "metrics": list(metrics or []),
    }


OBJECTS: list[dict] = [
    _obj(
        "ontology_model", "本体模型", ["本体", "Ontology"],
        "平台本体建模的唯一事实源（canonical JSON）。按 kind 分 system/business；"
        "改动先落草案，发布（独立权限码）后才级联重展开对象、重建图谱并同步知识库。",
        "adh_ontology_models",
        [
            _p("id", "模型ID", "bigint", True),
            _p("name", "模型名称", "varchar"),
            _p("kind", "本体类型", "varchar", False, "system=系统能力本体 / business=业务本体"),
            _p("domain", "业务域", "varchar", False, "业务本体的归属键；系统本体为空"),
            _p("status", "状态", "varchar", False, "draft/active/archived"),
            _p("object_count", "对象数量", "int"),
            _p("deploy_sites", "适用站点", "json", False, "业务本体多站点声明；系统本体为空"),
            _p("object_site_rules", "对象站点白名单", "json", False, "只能收紧、不能扩权"),
            _p("draft_updated_at", "草案更新时间", "datetime", False, "有值=有未发布改动"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("metric", "declares", "1:N", "模型声明该对象的指标口径（口径正文登记在字典）"),
            _l("dimension", "declares", "1:N", "模型声明该对象的可下钻维度"),
            _l("data_product", "published_as", "N:N", "对象经数据产品对外提供稳定契约"),
        ],
        [
            _a("ontology.generate", "生成本体草案",
               "按数据源元数据生成草案（只产草案，不激活）", risk="medium"),
            _a("ontology.save", "保存本体模型",
               "保存 canonical 内容；生效态编辑会重展开对象并重建图谱", risk="medium"),
            _a("ontology.activate", "激活本体模型",
               "旧 active 归档、本模型生效（含动作/规则/场景投影）", risk="high"),
            _a("ontology.import_yaml", "导入 Palantir YAML",
               "从 YAML 目录导入本体定义", risk="high"),
        ],
    ),
    _obj(
        "as_bot", "AS-BOT 角色智能体", ["智能体", "Waker", "AS-BOT"],
        "角色智能体配置（adh_as_bots）：与角色一对一绑定，定义提示词/persona/工具组/"
        "知识库/技能/可选模型。写动作由菜单与功能权限码 + 工具授权直接把关（fail-closed），"
        "不再有提议→审批→执行回路。",
        "adh_as_bots",
        [
            _p("id", "ID", "bigint", True),
            _p("as_bot_key", "标识", "varchar", False, "唯一英文标识"),
            _p("name", "名称", "varchar"),
            _p("display_name", "显示名称", "varchar"),
            _p("description", "描述", "varchar"),
            _p("category", "分类", "varchar", False, "nl2sql/analysis/chart/report/custom"),
            _p("tools", "工具授权", "json", False, "{groups, standard}：逐工具授权，未勾选即不注册"),
            _p("skills", "内联技能", "json", False, "技能片段（按需渐进披露）"),
            _p("models", "可选模型", "json", False, "Chat 端可选模型引用"),
            _p("is_active", "是否启用", "tinyint"),
            _p("is_builtin", "是否内置", "tinyint", False, "内置智能体不可删除"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("role", "bound_to", "1:1", "与角色一对一绑定，能力随角色继承"),
            _l("knowledge_base", "uses", "N:N", "可检索的知识库"),
            _l("skill", "uses", "N:N", "可用的技能扩展"),
            _l("mcp_server", "uses", "N:N", "引用的共享 MCP 服务"),
            _l("execution_layer", "runs_on", "N:1", "实际承载 agent loop 的执行层"),
        ],
    ),
    _obj(
        "knowledge_base", "知识库", ["KB", "QMind"],
        "知识库管理：支持 qMind（云端 Notebook）/local/vector_db 等类型；"
        "知识库只对口径解释与业务背景权威，名称与指标清单以实时目录为准。",
        "adh_knowledge_bases",
        [
            _p("id", "ID", "int", True),
            _p("name", "名称", "varchar"),
            _p("kb_type", "类型", "varchar", False, "qmind/local/vector_db/cloud_rag"),
            _p("status", "状态", "varchar", False, "active/inactive"),
            _p("document_count", "文档数", "int"),
            _p("chunk_count", "分段数", "int"),
            _p("last_sync_at", "最近同步时间", "datetime"),
        ],
        [
            _l("ontology_model", "documents", "1:N", "知识库文档化本体语义（上云脱敏投影）"),
        ],
        [
            _a("knowledge_base.list", "查看知识库",
               "列出可用知识库及其同步状态（不含文档正文）", read_only=True),
        ],
    ),
    _obj(
        "datasource", "数据源", ["DS", "数据连接"],
        "注册的数据源（MySQL/SLS/Doris/ES 等）。可用性唯一由用户角色授权裁决（fail-closed），"
        "工作空间不再绑定数据源；连接地址与凭据属涉密配置，不对外。",
        "adh_datasources",
        [
            _p("id", "ID", "bigint", True),
            _p("name", "名称", "varchar", False, "全局唯一业务标识，删除重建后按名重关联"),
            _p("db_type", "类型", "varchar", False, "mysql/sls/doris/es 等"),
            _p("site_code", "归属站点", "varchar", False, "空=未站点化/平台库；不得填聚合态"),
            _p("is_default", "是否默认", "tinyint"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("site", "belongs_to", "N:1", "数据源按站点归属，站点清单是授权集的派生视图"),
            _l("dataset", "serves", "1:N", "数据集在该源上取数"),
        ],
        [
            _a("datasource.list_safe", "查看数据源清单",
               "列出角色授权集内的数据源业务名与类型（不回内部标识/地址/账号）", read_only=True),
        ],
    ),
    _obj(
        "skill", "技能 (Skill)", ["SKILL", "技能包"],
        "文件夹化技能配置：每个技能包含 SKILL.md，AS-BOT 勾选后按需渐进披露；"
        "技能是操作规范，不构成新的取数或写权限。",
        "",
        [
            _p("", "name", "string", True, "技能标识"),
            _p("", "display_name", "string", False, "显示名称"),
            _p("", "category", "string", False, "分类"),
            _p("", "description", "text", False, "技能适用场景"),
        ],
    ),
    _obj(
        "role", "角色", ["RBAC", "权限角色"],
        "系统角色：授权菜单与功能权限码、数据源访问、看板/数据集范围；"
        "AS-BOT 与角色一对一，权限码是 AI 能力与写动作的唯一裁决依据。",
        "adh_roles",
        [
            _p("id", "ID", "int", True),
            _p("name", "标识", "varchar", False, "admin/analyst/viewer 等"),
            _p("display_name", "显示名称", "varchar"),
            _p("description", "角色说明", "varchar"),
            _p("is_system", "是否内置", "tinyint"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("as_bot", "grants", "1:1", "角色绑定的智能体（能力面）"),
            _l("permission", "granted", "N:N", "角色勾选的权限码（菜单显隐与写动作裁决）"),
            _l("user", "assigned_to", "1:N", "归属该角色的用户"),
        ],
    ),
    _obj(
        "report", "报表", ["分析报告", "REPORT"],
        "系统产出的分析报表。只暴露标题/格式/状态/来源分型等业务属性；"
        "报表正文、分享令牌与证据摘要不在本体可见范围。",
        "adh_reports",
        [
            _p("id", "报表ID", "bigint", True),
            _p("title", "报表标题", "varchar"),
            _p("format", "输出格式", "varchar"),
            _p("access_mode", "访问方式", "varchar"),
            _p("generation_status", "生成状态", "varchar"),
            _p("publication_status", "发布状态", "varchar"),
            _p("source_type", "来源分型", "varchar", False, "scheduled/analysis=数据派生；manual/conversation=内容型"),
            _p("view_count", "浏览次数", "int"),
            _p("created_at", "生成时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "based_on", "N:N", "报表基于数据集取数"),
            _l("scheduled_task", "scheduled_by", "N:1", "报表由定时任务调度生成"),
            _l("report_template", "rendered_by", "N:1", "报表按模板版式渲染"),
        ],
        [
            _a("report.list", "查看报表清单",
               "列出可见报表摘要（标题/格式/状态/浏览次数，不含正文）", read_only=True),
            _a("report.generate", "生成分析报告",
               "提交异步报告生成（数据集/声明式意图/已保存查询三选一，不接受裸 SQL）",
               risk="medium", params={"required": ["title"]}),
            _a("report.save", "保存分析报表",
               "把本会话已完成的分析结论保入报告中心（同步入库、不重新取数）",
               risk="low", params={"required": ["title", "content"]}),
        ],
    ),
    _obj(
        "dashboard", "看板", ["数据看板", "大屏", "仪表盘", "DASHBOARD"],
        "可视化看板/大屏。只暴露名称/说明/状态/标签等业务属性；"
        "布局、筛选参数、图表配置与取数 SQL 不在本体可见范围。",
        "adh_dashboards",
        [
            _p("id", "看板ID", "bigint", True),
            _p("name", "看板名称", "varchar"),
            _p("description", "看板说明", "text"),
            _p("status", "状态", "varchar"),
            _p("is_public", "是否公开", "tinyint"),
            _p("is_default", "是否默认看板", "tinyint"),
            _p("tags", "看板标签", "json"),
            _p("sort_order", "排序值", "int"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "references", "N:N", "看板引用数据集取数"),
            _l("as_bot", "owned_by", "N:1", "看板由该智能体设计/发布"),
            _l("dashboard_design", "published_from", "1:N", "看板由确认后的设计草稿发布"),
            _l("vis_component", "styled_by", "N:N", "看板按字模/主题包统一渲染"),
        ],
        [
            _a("dashboard.list", "查看看板清单",
               "列出角色可见看板（名称/说明/状态）", read_only=True),
            _a("dashboard.publish", "发布看板设计",
               "将已确认的设计草稿发布为看板（需 dashboard:manage）", risk="medium",
               params={"required": ["design_id", "expected_version"]}),
        ],
    ),
    _obj(
        "scheduled_task", "调度任务", ["定时任务", "计划任务", "SCHEDULED"],
        "自动化调度任务（查询或 Agent 分析）。任务跟随创建者，能力与可查范围按创建者角色实时继承；"
        "Webhook 密钥与上次错误原文属敏感信息，不在本体可见范围。",
        "adh_scheduled_tasks",
        [
            _p("id", "任务ID", "bigint", True),
            _p("name", "任务名称", "varchar"),
            _p("description", "任务说明", "text"),
            _p("task_type", "执行模式", "varchar", False, "query=固定查询 / agent=问题清单"),
            _p("cron_expression", "执行周期表达式", "varchar"),
            _p("trigger_type", "触发方式", "varchar", False, "cron/webhook/both"),
            _p("timezone", "时区", "varchar"),
            _p("report_template_key", "报告模板", "varchar"),
            _p("notify_on_success", "成功时通知", "tinyint"),
            _p("notify_on_failure", "失败时通知", "tinyint"),
            _p("is_active", "是否启用", "tinyint"),
            _p("last_run_at", "上次运行时间", "datetime"),
            _p("last_status", "上次状态", "varchar"),
            _p("run_count", "累计运行次数", "int"),
            _p("timeout_seconds", "超时秒数", "int"),
            _p("max_retries", "失败重试次数", "int"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "produces", "1:N", "定时任务产出/刷新数据集"),
            _l("notification_channel", "notifies", "N:1", "运行结果通知渠道"),
        ],
        [
            _a("scheduled_task.list", "查看调度任务",
               "列出可见定时任务（名称/周期/状态，不含 Webhook 密钥）", read_only=True),
            _a("scheduled_task.create", "创建调度任务",
               "创建定时 Agent 分析任务（只带问题清单与周期，能力按创建者角色继承）",
               risk="medium", params={"required": ["name", "cron_expression", "task_config"]}),
        ],
    ),
    _obj(
        "quality_rule", "质量检核规则", ["质量规则", "质检规则", "QUALITY"],
        "数据质量检核规则。只暴露规则名称/类型/严重程度等业务属性；"
        "物理表列名与规则表达式不在本体可见范围。",
        "adh_quality_rules",
        [
            _p("id", "规则ID", "bigint", True),
            _p("rule_name", "规则名称", "varchar"),
            _p("description", "规则说明", "text"),
            _p("rule_type", "规则类型", "enum"),
            _p("severity", "严重程度", "enum"),
            _p("schedule_cron", "执行周期表达式", "varchar"),
            _p("is_active", "是否启用", "tinyint"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "governs", "N:N", "质量规则治理数据集"),
            _l("quality_result", "produces", "1:N", "每次检核产出结果行"),
        ],
        [
            _a("quality_rule.list", "查看质量规则",
               "列出可见质量规则（名称/类型/严重程度，不含规则表达式）", read_only=True),
            _a("quality_rule.run_check", "执行质量检核",
               "按规则触发一次检核并返回通过率（不含失败明细采样）",
               risk="low", params={"required": ["rule_id"]}),
        ],
    ),
    _obj(
        "quality_result", "质量检核结果", ["质量结果", "通过率"],
        "质量检核结果：通过率/总行数/失败行数/耗时，供质量概览与趋势分析。",
        "adh_quality_results",
        [
            _p("id", "结果ID", "bigint", True),
            _p("check_time", "检核时间", "datetime"),
            _p("passed", "是否通过", "tinyint"),
            _p("total_rows", "总行数", "int"),
            _p("failed_rows", "失败行数", "int"),
            _p("pass_rate", "通过率", "decimal"),
            _p("elapsed_ms", "耗时毫秒", "bigint"),
        ],
        [
            _l("quality_rule", "produced_by", "N:1", "结果由该规则产出"),
        ],
    ),
    _obj(
        "dataset", "数据集", ["分析数据集", "DATASET"],
        "平台登记的数据集（语义对象或 SQL 来源）。只暴露名称/来源类型/状态/认证等业务属性；"
        "SQL 正文、字段配置与物理表名不在本体可见范围。",
        "adh_datasets",
        [
            _p("id", "数据集ID", "bigint", True),
            _p("name", "数据集名称", "varchar"),
            _p("description", "数据集说明", "varchar"),
            _p("source_type", "来源类型", "varchar", False, "semantic/sql/both"),
            _p("object_key", "来源本体对象", "varchar", False, "semantic 源的对象 key"),
            _p("status", "状态", "varchar"),
            _p("certified", "口径是否认证", "tinyint"),
            _p("origin", "口径来源", "varchar", False, "manual/chart_backfill/ai_session"),
            _p("paginable", "是否可翻页明细", "tinyint"),
            _p("available_datasources", "可用数据源", "json", False, "跨源执行范围（业务名）"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("datasource", "from", "N:1", "数据集在该源上取数"),
            _l("data_product", "published_as", "N:N", "数据集以数据产品对外提供契约"),
            _l("dashboard", "consumed_by", "N:N", "看板引用该数据集"),
        ],
        [
            _a("dataset.list", "查看数据集", "列出可访问数据集摘要（不含 SQL 与字段配置）",
               read_only=True),
        ],
    ),
    _obj(
        "metric", "指标", ["指标字典", "度量"],
        "指标字典行（adh_metrics）：指标口径的唯一权威登记处。对象只引用指标名；"
        "公式/聚合/归属/认证状态都留在字典，物理列仅服务端使用。写入口受 ontology:save 把关。",
        "adh_metrics",
        [
            _p("id", "ID", "int", True),
            _p("name", "指标名称", "varchar", False, "业务权威名"),
            _p("name_en", "英文标识", "varchar"),
            _p("aliases", "别名", "json", False, "合法别名清单"),
            _p("agg_type", "聚合方式", "varchar", False, "sum/count/count_distinct/avg/min/max/custom"),
            _p("unit", "单位", "varchar"),
            _p("category", "分类", "varchar"),
            _p("tags", "口径标签", "json"),
            _p("bound_object_key", "归属对象", "varchar", False, "指向已激活本体对象 key"),
            _p("certified", "已认证", "tinyint"),
            _p("owner", "口径负责人", "varchar"),
            _p("description", "口径说明", "text", False, "面向业务的文字口径"),
        ],
        [
            _l("ontology_model", "declared_in", "N:1", "指标归属对象所在本体模型"),
        ],
        [
            _a("dict.save_metric", "保存指标",
               "创建/更新指标字典行（只落库+审计；图谱与知识库需显式同步）",
               risk="medium", params={"required": ["display_name", "bound_object_key", "agg_type"]}),
        ],
    ),
    _obj(
        "dimension", "维度", ["维度字典", "分析维度"],
        "维度字典行（adh_dimensions）：可下钻/过滤的分析维度登记处；"
        "枚举码→业务标签由 value_labels 承载，物理列由服务端按属性业务名解析。",
        "adh_dimensions",
        [
            _p("id", "ID", "int", True),
            _p("name", "维度名称", "varchar", False, "业务权威名"),
            _p("name_en", "英文标识", "varchar"),
            _p("aliases", "别名", "json"),
            _p("category", "分类", "varchar", False, "时间/属性/状态等"),
            _p("hierarchy", "层级关系", "varchar"),
            _p("level", "当前层级", "int"),
            _p("bound_object_key", "归属对象", "varchar"),
            _p("value_labels", "枚举标签", "json", False, "码→业务标签；与本体 enum 同源联动"),
            _p("certified", "已认证", "tinyint"),
            _p("description", "维度说明", "text"),
        ],
        [
            _l("ontology_model", "declared_in", "N:1", "维度归属对象所在本体模型"),
        ],
        [
            _a("dict.save_dimension", "保存维度",
               "创建/更新维度字典行（只落库+审计；图谱与知识库需显式同步）",
               risk="medium", params={"required": ["display_name", "bound_object_key"]}),
        ],
    ),
    _obj(
        "user", "用户", ["账号", "成员", "USER"],
        "平台用户账号。只暴露账号/显示名/状态/角色等业务属性；"
        "口令散列、手机号、邮箱等联系方式属个人信息，不在本体可见范围。",
        "adh_users",
        [
            _p("id", "用户ID", "bigint", True),
            _p("username", "登录账号", "varchar"),
            _p("display_name", "显示名称", "varchar"),
            _p("user_role", "角色标识", "varchar"),
            _p("role_id", "所属角色", "bigint"),
            _p("status", "状态", "varchar", False, "active/disabled/locked"),
            _p("last_login", "最近登录", "datetime"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("role", "assigned", "N:1", "用户归属角色，能力与可查范围随角色实时继承"),
            _l("workspace", "belongs_to", "N:N", "用户可见的工作空间"),
            _l("user_asset", "owns", "1:N", "用户产出的个人资产"),
        ],
    ),
    _obj(
        "workspace", "工作空间", ["空间", "WORKSPACE"],
        "工作空间是协作与隔离的容器（会话、看板、数据集、资产按空间归属）。"
        "工作空间**不绑定数据源**：数据源可用性唯一由用户角色授权裁决。",
        "adh_workspaces",
        [
            _p("id", "空间ID", "bigint", True),
            _p("name", "空间名称", "varchar"),
            _p("description", "空间说明", "varchar"),
            _p("workspace_type", "空间类型", "varchar", False, "personal/team 等"),
            _p("owner_id", "所有者", "bigint"),
            _p("is_default", "是否默认空间", "tinyint"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("user", "owned_by", "N:1", "空间的所有者"),
            _l("conversation", "contains", "1:N", "空间内的会话"),
            _l("dashboard", "contains", "1:N", "空间内的看板"),
        ],
    ),
    _obj(
        "menu", "菜单", ["导航菜单", "MENU"],
        "菜单登记表（adh_menu_registry）：侧栏入口与角色权限页「菜单与功能」的同一份真值源。"
        "菜单显隐由权限码反查决定；ai_access 显式标注该域是否对 AI 开放（none=不开放）。",
        "adh_menu_registry",
        [
            _p("id", "ID", "int", True),
            _p("menu_key", "菜单键", "varchar", False, "路由式命名，如 workspace:dashboards"),
            _p("label", "菜单名称", "varchar", False, "与侧栏文案一致"),
            _p("section", "分组", "varchar"),
            _p("module", "模块", "varchar"),
            _p("perm_code", "关联权限码", "varchar", False, "显隐反查用；空则该菜单不可配"),
            _p("ai_access", "AI 开放级别", "enum", False, "none/read/write；none 表示不接 AI"),
            _p("sort", "排序值", "int"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("permission", "gated_by", "1:N", "菜单挂载的权限点（读权限归消费菜单、写权限归管理菜单）"),
        ],
    ),
    _obj(
        "permission", "权限点", ["功能权限", "PERMISSION"],
        "权限点登记表（adh_perm_registry）：角色勾选的权限码（adh_role_perms）由此取标签与 AI 口径。"
        "ai_access=write 的权限码才会映射为 AI 写动作；数据工程域恒为 none（运维专属）。",
        "adh_perm_registry",
        [
            _p("id", "ID", "int", True),
            _p("perm_code", "权限码", "varchar", False, "如 ontology:save / dashboard:manage"),
            _p("label", "权限名称", "varchar"),
            _p("module", "模块", "varchar"),
            _p("module_label", "模块名称", "varchar"),
            _p("description", "权限说明", "varchar"),
            _p("menu_key", "归属菜单", "varchar"),
            _p("ai_access", "AI 开放级别", "enum", False, "none/read/write"),
            _p("ai_action_key", "AI 动作键", "varchar", False, "为空表示不对应任何 AI 动作"),
            _p("ai_note", "AI 口径说明", "varchar", False, "不开放给 AI 的域在此写明原因"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("menu", "belongs_to", "N:1", "权限点归属的菜单"),
            _l("role", "granted_to", "N:N", "授权该权限点的角色"),
        ],
    ),
    _obj(
        "site", "站点", ["部署站点", "SITE"],
        "站点清单（adh_sites）：数据源按 site_code 归属站点，模型以 deploy_sites 声明适用站点。"
        "可查站点是角色授权数据源的派生视图，不是第二套授权；聚合态「全球」仅在可查站点≥2 时提供。",
        "adh_sites",
        [
            _p("site_code", "站点编码", "varchar", True),
            _p("display_name", "站点名称", "varchar"),
            _p("is_aggregate", "是否聚合态", "tinyint", False, "聚合态仅用于展示，不得绑定数据源"),
            _p("sort_order", "排序值", "int"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("datasource", "has", "1:N", "归属该站点的数据源"),
            _l("ontology_model", "deployed_to", "N:N", "按 deploy_sites 声明部署到该站点的业务本体"),
        ],
    ),
    _obj(
        "data_product", "数据产品", ["数据产品契约", "DATA_PRODUCT"],
        "经治理后对外提供的数据产品契约：登记生产方、上游引用、schema 版本与兼容性、"
        "分级分类与 SLA。契约变更走 dataset:manage 权限码，不允许绕过登记直接改物理表。",
        "adh_data_products",
        [
            _p("id", "产品ID", "bigint", True),
            _p("product_name", "产品标识", "varchar", False, "稳定标识，契约引用以此为准"),
            _p("display_name", "产品名称", "varchar"),
            _p("description", "产品说明", "varchar"),
            _p("product_class", "产品类别", "varchar"),
            _p("site", "所属站点", "varchar"),
            _p("domain", "业务域", "varchar"),
            _p("datasource_name", "数据源业务名", "varchar", False, "按业务名关联，不暴露内部标识"),
            _p("producer_kind", "生产方类型", "varchar", False, "dataset/table/metric 等"),
            _p("compatibility", "兼容性级别", "varchar", False, "backward/forward/full/none"),
            _p("schema_version", "schema 版本", "int"),
            _p("data_version", "数据版本", "varchar"),
            _p("tier", "数据分级", "varchar"),
            _p("classification", "分类标签", "varchar"),
            _p("owner", "责任人", "varchar"),
            _p("status", "状态", "varchar"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "produced_from", "N:1", "数据集是常见生产方"),
            _l("ontology_model", "contract_of", "N:N", "产品以本体对象为口径契约来源"),
        ],
        [
            _a("product.contract_change", "变更数据产品契约",
               "提交契约变更（schema 版本与兼容性校验，需 dataset:manage）", risk="high",
               params={"required": ["product_name"]}),
        ],
    ),
    _obj(
        "report_template", "报告模板", ["报表模板", "REPORT_TEMPLATE"],
        "报告版式模板（系统内置或空间自定义）。只暴露名称/格式/是否内置；"
        "模板正文属内容资产，不在本体可见范围。",
        "adh_report_templates",
        [
            _p("id", "模板ID", "bigint", True),
            _p("name", "模板名称", "varchar"),
            _p("description", "模板说明", "text"),
            _p("format", "输出格式", "varchar"),
            _p("is_system", "是否内置", "tinyint"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("report", "renders", "1:N", "该模板渲染出的报表"),
            _l("scheduled_task", "used_by", "N:N", "定时任务按模板生成报告"),
        ],
    ),
    _obj(
        "dashboard_design", "看板设计草稿", ["仪表盘设计", "DASHBOARD_DESIGN"],
        "AS-BOT 看板设计的两段式草稿：先产设计供人确认业务范围，预览经治理执行；"
        "发布（dashboard:manage）后才成为看板。修改会使旧预览失效（版本+摘要校验）。",
        "adh_as_bot_dashboard_designs",
        [
            _p("id", "设计ID", "char", True),
            _p("version", "版本号", "bigint", False, "并发发布校验依据"),
            _p("status", "状态", "varchar", False, "draft/previewed/published"),
            _p("preview_expires_at", "预览过期时间", "datetime", False, "过期需重新治理预览"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("conversation", "derived_from", "N:1", "设计会话来源"),
            _l("dashboard", "publishes_to", "1:1", "发布后生成的看板"),
        ],
    ),
    _obj(
        "vis_component", "字模库组件", ["字模", "主题包", "VIS_LIBRARY"],
        "统一样式的事实源（adh_vis_components）：主题包定义色板/卡片/图表线型，"
        "控件样式与图表样式分命名空间。一切图形渲染面经同一解析管线，不得另写配色。",
        "adh_vis_components",
        [
            _p("id", "组件ID", "bigint", True),
            _p("code", "组件编码", "varchar", False, "稳定引用键（如 tp_datafoundry）"),
            _p("name", "组件名称", "varchar"),
            _p("category", "分类", "varchar", False, "theme_pack/chart_style/widget_style/kpi_card 等"),
            _p("chart_type", "适用图表类型", "varchar", False, "空=不限；多类逗号分隔"),
            _p("source", "来源", "varchar", False, "builtin/custom"),
            _p("is_active", "是否上架", "tinyint"),
            _p("is_enabled", "是否启用", "tinyint"),
            _p("is_builtin", "是否内置", "tinyint"),
            _p("sort_order", "排序值", "int"),
        ],
        [
            _l("dashboard", "styles", "N:N", "看板引用字模/主题包统一渲染"),
        ],
    ),
    _obj(
        "content_article", "内容条目", ["文章", "内容中心", "内容草稿"],
        "内容中心的统一条目（新闻/公告/文档/案例/链接等，按 category 与 kind 区分）。"
        "支持草稿→提审→发布→归档流程；正文存于修订版，不在本体可见范围。",
        "adh_content_articles",
        [
            _p("id", "条目ID", "bigint", True),
            _p("slug", "路径标识", "varchar"),
            _p("title", "标题", "varchar"),
            _p("summary", "摘要", "varchar"),
            _p("kind", "内容类型", "varchar", False, "article/news/case/doc/link 等"),
            _p("category_id", "分类", "bigint"),
            _p("tags", "标签", "json"),
            _p("status", "发布状态", "varchar", False, "draft/review/published/archived"),
            _p("pinned", "是否置顶", "tinyint"),
            _p("featured", "是否推荐", "tinyint"),
            _p("lang", "语言", "varchar"),
            _p("version", "版本号", "int"),
            _p("view_count", "浏览数", "int"),
            _p("publish_at", "计划发布时间", "datetime"),
            _p("published_at", "实际发布时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("content_category", "belongs_to", "N:1", "条目归属的内容分类"),
            _l("tag", "tagged_by", "N:N", "条目标签"),
        ],
        [
            _a("content_article.list", "查看内容条目",
               "列出可见内容条目摘要（不含修订正文）", read_only=True),
            _a("content_article.get", "查看内容详情",
               "读取单条内容条目及其修订状态（正文仅供人阅读）", read_only=True),
            _a("content_draft.create", "创建内容草稿",
               "新建草稿（只落草稿态，不直接发布）", risk="low",
               params={"required": ["title"]}),
            _a("content_draft.update", "修改内容草稿",
               "更新草稿内容与元信息（不影响线上）", risk="low",
               params={"required": ["article_id"]}),
            _a("content_review.submit", "提交内容审核",
               "把草稿提交人工审核；审核通过才发布生效", risk="medium",
               params={"required": ["article_id"]}),
        ],
    ),
    _obj(
        "content_category", "内容分类", ["栏目", "CONTENT_CATEGORY"],
        "内容中心的分类/栏目登记（新闻、公告、文档、案例…），供条目归属与导航。",
        "adh_content_categories",
        [
            _p("id", "分类ID", "bigint", True),
            _p("category_key", "分类键", "varchar", False, "稳定 key，条目引用以此为准"),
            _p("label", "分类名称", "varchar"),
            _p("description", "分类说明", "varchar"),
            _p("icon", "图标", "varchar"),
            _p("color", "颜色", "varchar"),
            _p("sort", "排序值", "int"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("content_article", "contains", "1:N", "该分类下的内容条目"),
        ],
        [
            _a("content_taxonomy.list", "查看内容分类",
               "列出内容分类与栏目骨架（供选题与归类）", read_only=True),
        ],
    ),
    _obj(
        "decision_advice", "决策建议", ["管理建议", "DECISION_ADVICE"],
        "平台运行/治理的决策建议条目（按动作与严重程度归类），供管理端审阅采纳。"
        "依据引用只登记知识库线索，不登记内部推理正文。",
        "adh_decision_advice",
        [
            _p("id", "建议ID", "bigint", True),
            _p("advice_key", "建议键", "varchar", False, "稳定 key，评估结果按此引用"),
            _p("action", "建议动作", "varchar"),
            _p("severity", "严重程度", "enum", False, "info/warning/critical"),
            _p("is_active", "是否启用", "tinyint"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("knowledge_base", "grounded_by", "N:N", "建议依据的知识库线索"),
        ],
        [
            _a("decision.list", "查看决策建议",
               "列出当前生效的决策建议（不含内部推理正文）", read_only=True),
            _a("decision.evaluate", "评估决策建议",
               "按当前平台状态评估建议是否适用（不直接修改任何配置）", read_only=True),
        ],
    ),
    _obj(
        "tag", "标签", ["标签体系", "TAG"],
        "标签定义（手动/规则/枚举等类型），用于数据资产与内容的归类与筛选。",
        "adh_tags",
        [
            _p("id", "标签ID", "bigint", True),
            _p("name", "标签名称", "varchar"),
            _p("tag_type", "标签类型", "enum", False, "manual/rule/ml 等"),
            _p("entity_type", "适用实体", "enum", False, "table/column/dataset/dashboard 等"),
            _p("data_type", "取值类型", "enum"),
            _p("description", "标签说明", "text"),
            _p("color", "颜色", "varchar"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("dataset", "marks", "N:N", "标签标记数据集"),
            _l("business_term", "classifies", "N:N", "标签与业务术语的归类关系"),
        ],
    ),
    _obj(
        "business_term", "业务术语", ["黑话", "同义词", "BUSINESS_TERM"],
        "业务术语缓冲区（adh_business_terms）：只保留尚未收编到对象/字典的纯业务黑话；"
        "术语解析按数据源分桶，避免跨源串味。收编后应归位到对象/字典 aliases。",
        "adh_business_terms",
        [
            _p("id", "术语ID", "bigint", True),
            _p("term_cn", "中文术语", "varchar"),
            _p("term_en", "英文术语", "varchar"),
            _p("term_aliases", "同义词", "varchar", False, "逗号分隔的合法说法"),
            _p("term_type", "术语类型", "varchar", False, "metric/dimension/filter/other"),
            _p("description", "术语说明", "varchar"),
            _p("usage_count", "命中次数", "int"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("datasource", "scoped_to", "N:1", "术语按数据源分桶（空=全局）"),
            _l("tag", "tagged_by", "N:N", "术语归类标签"),
        ],
    ),
    _obj(
        "alias_suggestion", "别名建议", ["未识别词", "ALIAS_SUGGESTION"],
        "解析失败/模糊被拒时自动落入的别名候选台账。"
        "由失败数据驱动别名维护；处理动作走 ontology:save 权限码。",
        "adh_alias_suggestions",
        [
            _p("id", "建议ID", "bigint", True),
            _p("term", "被拒词", "varchar"),
            _p("target_type", "目标类型", "enum", False, "object/metric/dimension"),
            _p("source", "来源", "enum", False, "fuzzy_rejected/unresolved/user"),
            _p("hit_count", "命中次数", "int"),
            _p("status", "处理状态", "enum", False, "pending/approved/rejected"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("ontology_model", "suggests_for", "N:1", "建议补充到哪个本体的对象/字典"),
        ],
        [
            _a("alias.approve", "采纳别名建议",
               "把被拒词写回对象/字典 aliases（需 ontology:save）", risk="low",
               params={"required": ["term", "target_type"]}),
            _a("alias.reject", "驳回别名建议",
               "标记建议不采纳（不写回任何别名）", risk="low",
               params={"required": ["term", "target_type"]}),
        ],
    ),
    _obj(
        "sensitive_field", "敏感字段", ["数据脱敏", "SENSITIVE_FIELD"],
        "敏感字段基线（adh_sensitive_fields）：block=剔除列 / mask=脱敏，对所有人生效（含管理员）。"
        "角色策略与 RLS 永远不得弱化敏感基线；表列名仅供服务端使用。",
        "adh_sensitive_fields",
        [
            _p("id", "配置ID", "bigint", True),
            _p("table_name", "所属数据表", "varchar", False, "空=全局基线"),
            _p("column_name", "受控列", "varchar", False, "空=全局基线"),
            _p("sensitivity_level", "敏感级别", "enum", False, "public/internal/confidential/secret"),
            _p("mask_type", "脱敏方式", "enum", False, "block/full/partial/hash"),
            _p("description", "脱敏说明", "varchar"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("datasource", "applies_to", "N:1", "按数据源限定（空=全部数据源）"),
        ],
    ),
    _obj(
        "rls_policy", "行级权限策略", ["RLS", "行过滤"],
        "行级隔离策略（adh_rls_policies）：按用户属性注入行过滤，随查询者施加。"
        "过滤表达式与用户属性映射仅供服务端使用，不对 AI/前端暴露。",
        "adh_rls_policies",
        [
            _p("id", "策略ID", "bigint", True),
            _p("name", "策略名称", "varchar"),
            _p("description", "策略说明", "text"),
            _p("table_name", "适用数据表", "varchar"),
            _p("policy_type", "策略类型", "varchar"),
            _p("filter_type", "过滤类型", "varchar"),
            _p("user_attribute", "绑定用户属性", "varchar", False, "如 department/user_id"),
            _p("is_active", "是否启用", "tinyint"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("datasource", "applies_to", "N:1", "策略作用的数据源"),
            _l("role", "applies_to_role", "N:N", "策略生效的角色范围"),
        ],
    ),
    _obj(
        "data_standard", "数据标准", ["规范", "DATA_STANDARD"],
        "数据标准/规范（命名、编码、质量基线等）。规则配置供服务端校验，不直接下发。",
        "adh_data_standards",
        [
            _p("id", "标准ID", "bigint", True),
            _p("standard_type", "标准类型", "enum", False, "naming/format/quality 等"),
            _p("name", "标准名称", "varchar"),
            _p("description", "标准说明", "text"),
            _p("is_active", "是否启用", "tinyint"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "applies_to", "N:N", "标准约束的数据集"),
        ],
    ),
    _obj(
        "lineage_node", "血缘节点", ["血缘", "LINEAGE_NODE"],
        "血缘图节点（表/任务/报表等）的登记视图。节点元数据仅供图渲染，本体只登记可读标识。",
        "adh_lineage_nodes",
        [
            _p("id", "节点ID", "bigint", True),
            _p("node_type", "节点类型", "enum", False, "table/job/report 等"),
            _p("node_name", "节点名称", "varchar", False, "可读限定名，不展示裸 id"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("lineage_edge", "connects", "1:N", "节点的上下游关系"),
            _l("dataset", "represents", "N:1", "节点对应数据集（如有）"),
        ],
    ),
    _obj(
        "lineage_edge", "血缘边", ["上下游关系", "LINEAGE_EDGE"],
        "血缘图的上下游边：源节点→目标节点，可标记血缘类型与置信度。",
        "adh_lineage_edges",
        [
            _p("id", "边ID", "bigint", True),
            _p("source_node_id", "上游节点", "bigint"),
            _p("target_node_id", "下游节点", "bigint"),
            _p("edge_type", "血缘类型", "enum", False, "table/column/job 等"),
            _p("confidence", "置信度", "decimal"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("lineage_node", "from_to", "N:N", "边的上下游节点"),
        ],
    ),
    _obj(
        "execution_layer", "执行层", ["Agent 执行层", "EXECUTION_LAYER"],
        "AS-BOT 的执行承载层注册表（本地进程/远程服务等）：能力与工具清单由服务端注册，"
        "心路状态可观测。连接配置与会话凭据不在本体可见范围。",
        "adh_execution_layers",
        [
            _p("id", "执行层ID", "int", True),
            _p("name", "标识", "varchar"),
            _p("display_name", "显示名称", "varchar"),
            _p("description", "说明", "text"),
            _p("layer_type", "类型", "varchar", False, "local/remote 等"),
            _p("status", "状态", "varchar"),
            _p("source", "注册来源", "varchar"),
            _p("is_default", "是否默认", "tinyint"),
            _p("last_heartbeat_at", "最近心跳", "datetime"),
        ],
        [
            _l("as_bot", "serves", "1:N", "在该执行层上运行的智能体"),
        ],
    ),
    _obj(
        "mcp_server", "MCP 服务", ["MCP", "工具服务"],
        "MCP 服务注册（stdio/SSE/HTTP 等传输）。只暴露名称/传输类型/可用工具数等业务属性；"
        "地址、命令、环境变量与凭据不在本体可见范围。",
        "adh_mcp_servers",
        [
            _p("id", "服务ID", "bigint", True),
            _p("name", "服务名称", "varchar"),
            _p("description", "服务说明", "varchar"),
            _p("transport", "传输方式", "varchar", False, "stdio/sse/http 等"),
            _p("is_active", "是否启用", "tinyint"),
            _p("is_builtin", "是否内置", "tinyint"),
            _p("last_test_status", "最近连通状态", "varchar"),
            _p("version", "版本号", "int"),
        ],
        [
            _l("as_bot", "used_by", "N:N", "引用该服务的智能体"),
        ],
    ),
    _obj(
        "llm_model", "大模型配置", ["LLM", "模型服务"],
        "可选大模型配置（提供商/模型名/上下文窗口/能力排序等）。只暴露业务可读属性；"
        "接口地址与密钥属凭据，不在本体可见范围。",
        "adh_llm_models",
        [
            _p("id", "配置ID", "bigint", True),
            _p("name", "配置名称", "varchar"),
            _p("provider", "提供商", "varchar"),
            _p("model_name", "模型名", "varchar"),
            _p("context_window", "上下文窗口", "int"),
            _p("max_tokens", "最大输出 tokens", "int"),
            _p("supports_thinking", "支持思考模式", "tinyint"),
            _p("supports_vision", "支持视觉", "tinyint"),
            _p("capability_rank", "能力排序", "int"),
            _p("is_default", "是否默认", "tinyint"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("as_bot", "selectable_by", "N:N", "智能体会话中可选的模型"),
        ],
    ),
    _obj(
        "sandbox_environment", "沙箱环境", ["沙箱", "SANDBOX"],
        "代码/工具执行的隔离环境注册（类型/状态/默认标记）。资源明细与运行配置不在本体可见范围。",
        "adh_sandbox_environments",
        [
            _p("id", "环境ID", "bigint", True),
            _p("name", "环境标识", "varchar"),
            _p("display_name", "环境名称", "varchar"),
            _p("description", "环境说明", "text"),
            _p("sandbox_type", "类型", "varchar", False, "docker/process 等"),
            _p("status", "状态", "varchar"),
            _p("is_default", "是否默认", "tinyint"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("execution_layer", "hosted_by", "N:1", "沙箱所属执行层"),
        ],
    ),
    _obj(
        "notification_channel", "通知渠道", ["告警通知", "NOTIFICATION_CHANNEL"],
        "运行结果与告警的通知渠道注册（邮件/Webhook/企微等）。只暴露名称/类型/启用状态；"
        "地址与密钥属凭据，不在本体可见范围。",
        "adh_notification_channels",
        [
            _p("id", "渠道ID", "bigint", True),
            _p("name", "渠道名称", "varchar"),
            _p("channel_type", "渠道类型", "varchar", False, "email/webhook/wecom 等"),
            _p("is_active", "是否启用", "tinyint"),
            _p("last_test_status", "最近连通状态", "varchar"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("scheduled_task", "notified_by", "1:N", "使用该渠道通知的调度任务"),
        ],
    ),
    _obj(
        "conversation", "会话", ["对话", "CONVERSATION"],
        "智能问数/AS-BOT 会话（按工作空间、站点、智能体归档）。只暴露标题/时间/归属；"
        "对话消息正文属用户内容，不在本体可见范围。",
        "adh_conversations",
        [
            _p("id", "会话ID", "bigint", True),
            _p("title", "会话标题", "varchar"),
            _p("as_bot_key", "智能体", "varchar", False, "该会话使用的 AS-BOT"),
            _p("site_code", "站点", "varchar"),
            _p("created_at", "创建时间", "datetime"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("user", "owned_by", "N:1", "会话所属用户"),
            _l("workspace", "in", "N:1", "会话所属工作空间"),
            _l("as_bot", "handled_by", "N:1", "会话由该智能体处理"),
        ],
    ),
    _obj(
        "eval_case", "评测用例", ["黄金问题", "EVAL_CASE"],
        "问数/检索能力评测用例（问题、期望、所属套件）。用于回归正确率；题目与答案属测试资产。",
        "adh_eval_cases",
        [
            _p("id", "用例ID", "bigint", True),
            _p("case_key", "用例键", "varchar", False, "稳定标识，跨版本对比以此为准"),
            _p("suite", "套件", "varchar", False, "golden/retrieval 等"),
            _p("capability", "能力面", "varchar"),
            _p("case_status", "用例状态", "varchar"),
            _p("sort", "排序值", "int"),
            _p("is_active", "是否启用", "tinyint"),
        ],
        [
            _l("ontology_model", "validates", "N:N", "用例验证的本体解析/检索能力"),
        ],
    ),
    _obj(
        "llm_trace", "调用轨迹", ["Tracing", "LLM_TRACE"],
        "一次问答的调用链路汇总（入口/处理器/模型名/状态/耗时/token/费用/渠道）。"
        "原始问题与回答正文、错误堆栈不在本体可见范围。",
        "adh_llm_traces",
        [
            _p("trace_id", "轨迹ID", "varchar", True),
            _p("entrypoint", "入口", "varchar", False, "chat/api/scheduled 等"),
            _p("handled_by", "处理器", "varchar", False, "处理该请求的智能体/引擎"),
            _p("model_ref", "模型名", "varchar"),
            _p("status", "状态", "varchar"),
            _p("duration_ms", "耗时毫秒", "bigint"),
            _p("input_tokens", "输入 tokens", "int"),
            _p("output_tokens", "输出 tokens", "int"),
            _p("total_tokens", "总 tokens", "int"),
            _p("credits", "计费点数", "decimal"),
            _p("channel", "通道", "varchar"),
            _p("started_at", "开始时间", "datetime"),
        ],
        [
            _l("conversation", "belongs_to", "N:1", "轨迹所属会话"),
            _l("llm_model", "used_model", "N:1", "实际使用的模型配置"),
        ],
    ),
    _obj(
        "audit_log", "审计日志", ["审计", "AUDIT_LOG"],
        "关键行为审计（谁在何时对哪个对象做了什么）。只暴露动作/模块/目标摘要；"
        "详情正文与访问来源地址不在本体可见范围（成功与拒绝均入库）。",
        "adh_audit_logs",
        [
            _p("id", "日志ID", "bigint", True),
            _p("action", "动作", "varchar"),
            _p("module", "模块", "varchar"),
            _p("target_type", "目标类型", "varchar"),
            _p("target_id", "目标ID", "bigint"),
            _p("created_at", "发生时间", "datetime"),
        ],
        [
            _l("user", "performed_by", "N:1", "执行人（系统调用为空）"),
        ],
    ),
    _obj(
        "saved_query", "已保存查询", ["查询模板", "SAVED_QUERY"],
        "可复用的已保存查询/查询数据集（SQL 正文与数据源属敏感信息，不在本体可见范围）。",
        "adh_saved_queries",
        [
            _p("id", "查询ID", "bigint", True),
            _p("name", "查询名称", "varchar"),
            _p("description", "查询说明", "text"),
            _p("is_dataset", "是否数据集", "tinyint"),
            _p("dataset_keywords", "数据集关键词", "varchar"),
            _p("updated_at", "更新时间", "datetime"),
        ],
        [
            _l("dataset", "materialized_as", "N:1", "查询可沉淀为数据集"),
            _l("user", "owned_by", "N:1", "创建人"),
        ],
    ),
    _obj(
        "release_note", "发布公告", ["版本公告", "RELEASE_NOTE"],
        "平台版本发布公告（版本号/标题/摘要/是否大版本/发布状态），供公告页与引导展示。",
        "adh_release_notes",
        [
            _p("id", "公告ID", "bigint", True),
            _p("version", "版本号", "varchar"),
            _p("title", "标题", "varchar"),
            _p("summary", "摘要", "varchar"),
            _p("is_major", "是否大版本", "tinyint"),
            _p("is_published", "是否已发布", "tinyint"),
            _p("published_at", "发布时间", "datetime"),
        ],
        [],
    ),
    _obj(
        "application", "集成应用", ["嵌入应用", "APPLICATION"],
        "外部集成应用（AK 嵌入）：控制是否开启对话、可访问看板/表白名单与限流。"
        "应用密钥散列属凭据，不在本体可见范围。",
        "adh_applications",
        [
            _p("id", "应用ID", "bigint", True),
            _p("name", "应用名称", "varchar"),
            _p("status", "状态", "varchar"),
            _p("enable_chat", "开启对话", "tinyint"),
            _p("rate_limit", "限流阈值", "int"),
            _p("description", "应用说明", "varchar"),
            _p("last_used_at", "最近使用", "datetime"),
        ],
        [
            _l("dashboard", "exposes", "N:N", "应用可访问的看板"),
            _l("user", "created_by_user", "N:1", "创建人"),
        ],
    ),
    _obj(
        "user_asset", "个人资产", ["用户文件", "USER_ASSET"],
        "会话/分析产出的个人资产（文件/对象引用）。只暴露名称/类别/大小/来源会话；"
        "存储路径不在本体可见范围。",
        "adh_user_assets",
        [
            _p("id", "资产ID", "char", True),
            _p("name", "资产名称", "varchar"),
            _p("filename", "文件名", "varchar"),
            _p("category", "类别", "varchar"),
            _p("object_key", "关联对象", "varchar"),
            _p("storage_type", "存储类型", "varchar"),
            _p("size", "字节数", "bigint"),
            _p("created_at", "创建时间", "datetime"),
        ],
        [
            _l("user", "owned_by", "N:1", "资产归属用户"),
            _l("conversation", "from", "N:1", "产出该资产的会话"),
        ],
    ),
    # ── 数据工程域（运维专属，不对 AI 开放；只做只读边界描述，见 data-sync-engineering §2）──
    _obj(
        "sync_task", "数据同步任务", ["同步任务", "SYNC_TASK"],
        "数据搬运任务（adh_sync_tasks）：读源表 → 计算 → 写授权表，不向人或 LLM 返回数据行。"
        "**属运维专属动作，不对 AI 开放**：不生成工具、不作为任何 AI 动作的裁决码，"
        "只能由具备 sync:* 权限码的人在运维界面操作。",
        "adh_sync_tasks",
        [
            _p("id", "任务ID", "bigint", True),
            _p("name", "任务名称", "varchar"),
            _p("description", "任务说明", "text"),
            _p("source_type", "源类型", "varchar"),
            _p("target_type", "目标类型", "varchar"),
            _p("sync_mode", "同步模式", "varchar", False, "full/incremental"),
            _p("schedule_cron", "调度表达式", "varchar"),
            _p("status", "任务状态", "varchar"),
            _p("is_active", "是否启用", "tinyint"),
            _p("last_run_at", "上次运行时间", "datetime"),
            _p("last_status", "上次运行状态", "varchar"),
            _p("run_count", "累计运行次数", "int"),
            _p("timeout_seconds", "超时秒数", "int"),
            _p("max_retries", "失败重试次数", "int"),
        ],
        [
            _l("datasource", "reads_from", "N:1", "源端数据源（凭据仅服务端解析）"),
            _l("dag_workflow", "orchestrated_by", "N:N", "被 DAG 工作流调度的同步任务"),
        ],
    ),
    _obj(
        "dag_workflow", "DAG 工作流", ["数据开发工作流", "DAG_WORKFLOW"],
        "作业编排工作流（adh_dag_workflows）：控制面独占调度与依赖，节点失败必显式 failed/partial。"
        "**属运维专属动作，不对 AI 开放**（否则血缘、口径版本与可复现性同时失守）。",
        "adh_dag_workflows",
        [
            _p("id", "工作流ID", "bigint", True),
            _p("name", "工作流名称", "varchar"),
            _p("description", "工作流说明", "text"),
            _p("kind", "类型", "varchar", False, "data/schedule 等"),
            _p("version", "版本号", "int"),
            _p("cron_expression", "调度表达式", "varchar"),
            _p("timezone", "时区", "varchar"),
            _p("is_active", "是否启用", "tinyint"),
            _p("last_run_at", "上次运行时间", "datetime"),
            _p("last_status", "上次运行状态", "varchar"),
            _p("run_count", "累计运行次数", "int"),
            _p("timeout_seconds", "超时秒数", "int"),
            _p("max_retries", "失败重试次数", "int"),
        ],
        [
            _l("sync_task", "orchestrates", "N:N", "工作流调度的同步任务"),
            _l("notification_channel", "alerts_to", "N:1", "运行告警通知渠道"),
        ],
    ),
    _obj(
        "udf", "自定义函数", ["UDF", "自定义算子"],
        "自定义函数注册表（adh_udfs）：供数据工程作业复用的算子，带版本与启用状态。"
        "**属运维专属动作，不对 AI 开放**（表达式正文不登记）。",
        "adh_udfs",
        [
            _p("id", "函数ID", "bigint", True),
            _p("name", "函数名", "varchar"),
            _p("udf_kind", "函数类型", "varchar"),
            _p("return_type", "返回类型", "varchar"),
            _p("description", "函数说明", "text"),
            _p("is_active", "是否启用", "tinyint"),
            _p("is_current", "是否当前版本", "tinyint"),
            _p("version", "版本号", "int"),
            _p("usage_count", "引用次数", "int"),
        ],
        [
            _l("sync_task", "used_by", "N:N", "引用该算子的同步任务"),
        ],
    ),
]


RULES: list[dict] = [
    {
        "key": "perm_admin_full_by_role_perms",
        "type": "permission",
        "statement": "管理员按角色勾选的权限码可发起全部系统动作；敏感基线仍对所有人生效，不因任何规则弱化",
        "source": "as-bot-system-waker §3 / security-guardrails §3",
        "enforcement": {"subject": "role:admin", "action": "*", "level": "allow"},
    },
    {
        "key": "perm_ai_write_fail_closed",
        "type": "permission",
        "statement": "AI 写动作必须持对应权限码且 ai_access=write，否则直接拒绝（fail-closed）；"
                     "无提议→审批→执行回路，不存在待审批队列",
        "source": "as-bot-system-waker §3",
        "enforcement": {"subject": "ai:as_bot", "action": "*", "level": "deny"},
    },
    {
        "key": "perm_analyst_write_deny",
        "type": "permission",
        "statement": "业务分析师默认不发起系统治理动作（建模/发布等归治理岗，确需开放时由管理员勾权限码）",
        "source": "最小权限原则",
        "enforcement": {"subject": "role:analyst", "action": "*", "level": "deny"},
    },
    {
        "key": "perm_viewer_write_deny",
        "type": "permission",
        "statement": "只读角色不发起任何系统治理动作",
        "source": "最小权限原则",
        "enforcement": {"subject": "role:viewer", "action": "*", "level": "deny"},
    },
    {
        "key": "constraint_metric_single_source",
        "type": "metric_constraint",
        "statement": "指标/维度口径唯一登记在字典（adh_metrics/adh_dimensions）；"
                     "本体对象只引用指标名，不得把指标当属性写进 properties（会造出幽灵列）",
        "source": "ontology-modeling §3",
        "enforcement": {"rule": "metrics_by_name_reference"},
    },
    {
        "key": "constraint_data_engineering_ai_closed",
        "type": "business_constraint",
        "statement": "数据同步/DAG/UDF/定时物化属运维专属：不生成 AI 工具、不参与 AI 动作裁决、"
                     "不向模型广告；确需变更时由人在运维界面操作",
        "source": "data-sync-engineering §2",
        "enforcement": {"rule": "ai_zero_access"},
    },
    {
        "key": "quality_sensitive_baseline_immutable",
        "type": "quality",
        "statement": "敏感字段基线（block/mask）对所有人生效（含管理员），角色策略与 RLS 不得弱化或覆盖",
        "source": "security-guardrails §3",
        "enforcement": {"rule": "sensitive_baseline_wins"},
    },
    {
        "key": "constraint_ontology_two_phase",
        "type": "business_constraint",
        "statement": "本体改动只落草案，发布（独立权限码）后才级联重展开对象/重建图谱/同步知识库；"
                     "不得绕过级联直接改派生表",
        "source": "ontology-modeling §1",
        "enforcement": {"rule": "draft_then_publish"},
    },
]


SCENARIOS: list[dict] = [
    {
        "key": "ops_modeling_progress",
        "title": "建模进度巡检",
        "goal": "回答业务方「本体建到哪了、还缺什么」",
        "actors": ["role:admin"],
        "uses_objects": ["ontology_model", "dataset"],
        "uses_actions": ["ontology.generate"],
        "uses_rules": ["perm_admin_full_by_role_perms"],
        "eval_seed": ["本体建模进度如何？还有哪些表没有进本体？"],
    },
    {
        "key": "ops_sensitive_marking",
        "title": "敏感列补标",
        "goal": "发现未登记敏感基线的列并推动补标（数据护城河完整性）",
        "actors": ["role:admin"],
        "uses_objects": ["sensitive_field", "rls_policy", "dataset"],
        "uses_actions": [],
        "uses_rules": ["quality_sensitive_baseline_immutable"],
        "eval_seed": ["哪些敏感列还没有登记脱敏基线？"],
    },
    {
        "key": "ops_metric_certification",
        "title": "口径认证推进",
        "goal": "推动指标/维度口径认证与责任人落实",
        "actors": ["role:admin"],
        "uses_objects": ["metric", "dimension", "ontology_model"],
        "uses_actions": ["dict.save_metric"],
        "uses_rules": ["constraint_metric_single_source"],
        "eval_seed": ["还有多少指标口径没有认证？谁负责？"],
    },
    {
        "key": "ops_contract_change",
        "title": "契约变更影响分析",
        "goal": "数据产品契约变更前评估影响面（对象/口径/看板）",
        "actors": ["role:admin"],
        "uses_objects": ["data_product", "dataset", "dashboard"],
        "uses_actions": ["product.contract_change"],
        "uses_rules": ["perm_admin_full_by_role_perms"],
        "eval_seed": ["改这张表的契约会影响哪些看板？"],
    },
    {
        "key": "ops_dashboard_publish",
        "title": "看板设计到发布",
        "goal": "先确认业务范围与治理预览，再发布为看板（不跳过预览）",
        "actors": ["role:admin"],
        "uses_objects": ["dashboard_design", "dashboard", "dataset"],
        "uses_actions": ["dashboard.publish"],
        "uses_rules": ["perm_ai_write_fail_closed"],
        "eval_seed": ["发布看板需要什么条件？"],
    },
    {
        "key": "ops_permission_audit",
        "title": "权限与菜单核查",
        "goal": "核对角色权限码、菜单显隐与 AI 开放级别的对应关系",
        "actors": ["role:admin"],
        "uses_objects": ["role", "permission", "menu", "as_bot"],
        "uses_actions": [],
        "uses_rules": ["perm_ai_write_fail_closed"],
        "eval_seed": ["哪些权限点配了 AI 级别但没有消费方？"],
    },
    {
        "key": "ops_content_publish",
        "title": "内容草稿到发布",
        "goal": "新建内容草稿走审核后发布，不直接改线上",
        "actors": ["role:admin"],
        "uses_objects": ["content_article", "content_category"],
        "uses_actions": ["content_draft.create", "content_review.submit"],
        "uses_rules": ["constraint_ontology_two_phase"],
        "eval_seed": ["怎么发布一篇平台公告？"],
    },
    {
        "key": "ops_data_engineering_boundary",
        "title": "数据工程边界说明",
        "goal": "解释同步/DAG/UDF 为什么不在对话里操作（运维专属，需到对应页面人工执行）",
        "actors": ["role:admin"],
        "uses_objects": ["sync_task", "dag_workflow", "udf"],
        "uses_actions": [],
        "uses_rules": ["constraint_data_engineering_ai_closed"],
        "eval_seed": ["能让 AI 帮我建一个同步任务吗？"],
    },
]


SYSTEM_MODEL_NAME = "AS-BOT 系统能力本体"
SYSTEM_DOMAIN = "系统自身能力"
SYSTEM_DESCRIPTION = (
    "AI-DataHub 平台自身能力与边界的本体模型：登记平台能力域、对象、可用动作与权限口径，"
    "供 AS-BOT 回答「平台能做什么、边界在哪、该走哪个入口」。业务数据语义不在本模型，"
    "见各业务本体（kind=business）。"
)

# 不得登记到系统本体的列（凭据/内容正文/基础设施地址）：本体只投影业务可读属性。
_FORBIDDEN_COLUMNS = frozenset({
    "password", "password_hash", "email", "phone", "token", "api_key", "secret",
    "host", "url", "base_url", "endpoint_url", "connection", "command", "env",
    "sql", "sql_query", "content", "question", "answer", "detail", "ip_address",
    "filter_expr", "graph_json", "messages", "app_key_hash", "job_secret",
    "storage_path", "source_path", "target_config", "source_config",
})


def validate_catalog() -> list[str]:
    """目录自检（fail-loud）：key 唯一、引用可解析、无涉密列。返回错误清单。"""
    errors: list[str] = []
    keys = [str(o.get("key") or "") for o in OBJECTS]
    if len(keys) != len(set(keys)):
        errors.append("对象 key 重复: " + ", ".join(sorted({k for k in keys if keys.count(k) > 1})))
    key_set = set(keys)
    action_keys: list[str] = []
    for o in OBJECTS:
        okey = str(o.get("key") or "")
        for a in (o.get("actions") or []):
            action_keys.append(str(a.get("key") or ""))
        for link in (o.get("links") or []):
            tgt = str(link.get("target") or "")
            if tgt and tgt not in key_set:
                errors.append(f"对象 {okey} 的关系 target 不存在: {tgt}")
        for p in (o.get("properties") or []):
            col = str(p.get("column") or "")
            name = col.rsplit(".", 1)[-1].lower()
            if name in _FORBIDDEN_COLUMNS:
                errors.append(f"对象 {okey} 登记了不得外露的列: {col}")
        if not str(o.get("display_name") or "").strip():
            errors.append(f"对象 {okey} 缺业务中文名")
    dup_actions = sorted({a for a in action_keys if action_keys.count(a) > 1})
    if dup_actions:
        errors.append("动作 key 重复: " + ", ".join(dup_actions))
    akey_set = set(action_keys)
    for r in RULES:
        enf = r.get("enforcement") or {}
        act = str(enf.get("action") or "")
        if act and act != "*" and act not in akey_set:
            errors.append(f"规则 {r.get('key')} 引用了不存在的动作: {act}")
    for s in SCENARIOS:
        for field, valid in (("uses_objects", key_set), ("uses_actions", akey_set),
                             ("uses_rules", {str(r.get('key') or '') for r in RULES})):
            for ref in (s.get(field) or []):
                if str(ref) not in valid:
                    errors.append(f"场景 {s.get('key')} 的 {field} 引用了不存在的项: {ref}")
    return errors


def build_system_ontology_doc() -> dict:
    """组装系统本体 canonical doc（确定性输出，不含时间戳）。"""
    import copy

    problems = validate_catalog()
    if problems:
        raise ValueError("系统本体目录自检失败: " + "; ".join(problems[:20]))
    return {
        "domain": SYSTEM_DOMAIN,
        "description": SYSTEM_DESCRIPTION,
        "kind": "system",
        "sources": [],
        "objects": copy.deepcopy(OBJECTS),
        "rules": copy.deepcopy(RULES),
        "scenarios": copy.deepcopy(SCENARIOS),
    }