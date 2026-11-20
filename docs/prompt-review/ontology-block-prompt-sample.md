# 本体语义层（分块呈现）

每个对象固定按六块呈现：描述 = 是什么；属性 = 可问的字段；关系 = 对象之间怎么连；
行为 = 业务上会发生什么（状态迁移与后果）；动作 = 平台能做的一次操作；
可算指标 = 口径权威在指标字典。模型级另有规则（不可违反的口径）与场景（这套建模要回答什么问题）。
块内标注「未登记」的，表示该维度当前没有建模依据，**不得凭常识补**。

## 模型：AS-BOT 系统能力本体（域别 system）

对象 50　|　规则 8　|　场景 8

AI-DataHub 平台自身能力与边界的本体模型：登记平台能力域、对象、可用动作与权限口径，供 AS-BOT 回答「平台能做什么、边界在哪、该走哪个入口」。业务数据语义不在本模型，见各业务本体（kind=business）。

---

### 对象：本体模型（`ontology_model`）
别名：本体、Ontology

#### 描述
平台本体建模的唯一事实源（canonical JSON）。按 kind 分 system/business；改动先落草案，发布（独立权限码）后才级联重展开对象、重建图谱并同步知识库。

#### 属性（10）
- 模型ID（bigint，主键）
- 模型名称（varchar）
- 本体类型（varchar）：system=系统能力本体 / business=业务本体
- 业务域（varchar）：业务本体的归属键；系统本体为空
- 状态（varchar）：draft/active/archived
- 对象数量（int）
- 适用站点（json）：业务本体多站点声明；系统本体为空
- 对象站点白名单（json）：只能收紧、不能扩权
- 草案更新时间（datetime）：有值=有未发布改动
- 更新时间（datetime）

#### 关系（3）
- declares → **指标**（1:N）：模型声明该对象的指标口径（口径正文登记在字典）
- declares → **维度**（1:N）：模型声明该对象的可下钻维度
- published_as → **数据产品**（N:N）：对象经数据产品对外提供稳定契约

#### 行为（0）
- （未登记）

#### 动作（4）
- 生成本体草案（`ontology.generate`）｜写入｜风险 medium｜按数据源元数据生成草案（只产草案，不激活）
- 保存本体模型（`ontology.save`）｜写入｜风险 medium｜保存 canonical 内容；生效态编辑会重展开对象并重建图谱
- 激活本体模型（`ontology.activate`）｜写入｜风险 high｜旧 active 归档、本模型生效（含动作/规则/场景投影）
- 导入 Palantir YAML（`ontology.import_yaml`）｜写入｜风险 high｜从 YAML 目录导入本体定义

---

### 对象：AS-BOT 角色智能体（`as_bot`）
别名：智能体、Waker、AS-BOT

#### 描述
角色智能体配置（AS-BOT 角色智能体）：与角色一对一绑定，定义提示词/persona/工具组/知识库/技能/可选模型。写动作由菜单与功能权限码 + 工具授权直接把关（fail-closed），不再有提议→审批→执行回路。

#### 属性（12）
- ID（bigint，主键）
- 标识（varchar）：唯一英文标识
- 名称（varchar）
- 显示名称（varchar）
- 描述（varchar）
- 分类（varchar）：nl2sql/analysis/chart/report/custom
- 工具授权（json）：{groups, standard}：逐工具授权，未勾选即不注册
- 内联技能（json）：技能片段（按需渐进披露）
- 可选模型（json）：Chat 端可选模型引用
- 是否启用（tinyint）
- 是否内置（tinyint）：内置智能体不可删除
- 更新时间（datetime）

#### 关系（5）
- bound_to → **角色**（1:1）：与角色一对一绑定，能力随角色继承
- uses → **知识库**（N:N）：可检索的知识库
- uses → **技能 (Skill)**（N:N）：可用的技能扩展
- uses → **MCP 服务**（N:N）：引用的共享 MCP 服务
- runs_on → **执行层**（N:1）：实际承载 agent loop 的执行层

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

---

### 对象：知识库（`knowledge_base`）
别名：KB、QMind

#### 描述
知识库管理：支持 qMind（云端 Notebook）/local/vector_db 等类型；知识库只对口径解释与业务背景权威，名称与指标清单以实时目录为准。

#### 属性（7）
- ID（int，主键）
- 名称（varchar）
- 类型（varchar）：qmind/local/vector_db/cloud_rag
- 状态（varchar）：active/inactive
- 文档数（int）
- 分段数（int）
- 最近同步时间（datetime）

#### 关系（1）
- documents → **本体模型**（1:N）：知识库文档化本体语义（上云脱敏投影）

#### 行为（0）
- （未登记）

#### 动作（1）
- 查看知识库（`knowledge_base.list`）｜只读｜风险 未登记｜列出可用知识库及其同步状态（不含文档正文）

---

### 对象：数据源（`datasource`）
别名：DS、数据连接

#### 描述
注册的数据源（MySQL/SLS/Doris/ES 等）。可用性唯一由用户角色授权裁决（fail-closed），工作空间不再绑定数据源；连接地址与凭据属涉密配置，不对外。

#### 属性（6）
- ID（bigint，主键）
- 名称（varchar）：全局唯一业务标识，删除重建后按名重关联
- 类型（varchar）：mysql/sls/doris/es 等
- 归属站点（varchar）：空=未站点化/平台库；不得填聚合态
- 是否默认（tinyint）
- 创建时间（datetime）

#### 关系（2）
- belongs_to → **站点**（N:1）：数据源按站点归属，站点清单是授权集的派生视图
- serves → **数据集**（1:N）：数据集在该源上取数

#### 行为（0）
- （未登记）

#### 动作（1）
- 查看数据源清单（`datasource.list_safe`）｜只读｜风险 未登记｜列出角色授权集内的数据源业务名与类型（不回内部标识/地址/账号）

---

### 对象：技能 (Skill)（`skill`）
别名：SKILL、技能包

#### 描述
文件夹化技能配置：每个技能包含 SKILL.md，AS-BOT 勾选后按需渐进披露；技能是操作规范，不构成新的取数或写权限。

#### 属性（4）
- name（string，主键）：技能标识
- display_name（string）：显示名称
- category（string）：分类
- description（text）：技能适用场景

#### 关系（0）
- （未登记）

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

---

### 对象：角色（`role`）
别名：RBAC、权限角色

#### 描述
系统角色：授权菜单与功能权限码、数据源访问、看板/数据集范围；AS-BOT 与角色一对一，权限码是 AI 能力与写动作的唯一裁决依据。

#### 属性（6）
- ID（int，主键）
- 标识（varchar）：admin/analyst/viewer 等
- 显示名称（varchar）
- 角色说明（varchar）
- 是否内置（tinyint）
- 是否启用（tinyint）

#### 关系（3）
- grants → **AS-BOT 角色智能体**（1:1）：角色绑定的智能体（能力面）
- granted → **权限点**（N:N）：角色勾选的权限码（菜单显隐与写动作裁决）
- assigned_to → **用户**（1:N）：归属该角色的用户

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

---

### 规则（8）
- [permission] `perm_admin_full_by_role_perms`：管理员按角色勾选的权限码可发起全部系统动作；敏感基线仍对所有人生效，不因任何规则弱化
- [permission] `perm_ai_write_fail_closed`：AI 写动作必须持对应权限码且 ai_access=write，否则直接拒绝（fail-closed）；无提议→审批→执行回路，不存在待审批队列
- [permission] `perm_analyst_write_deny`：业务分析师默认不发起系统治理动作（建模/发布等归治理岗，确需开放时由管理员勾权限码）
- [permission] `perm_viewer_write_deny`：只读角色不发起任何系统治理动作
- [metric_constraint] `constraint_metric_single_source`：指标/维度口径唯一登记在字典（指标/维度）；本体对象只引用指标名，不得把指标当属性写进 properties（会造出幽灵列）
- [business_constraint] `constraint_data_engineering_ai_closed`：数据同步/DAG/UDF/定时物化属运维专属：不生成 AI 工具、不参与 AI 动作裁决、不向模型广告；确需变更时由人在运维界面操作
- [quality] `quality_sensitive_baseline_immutable`：敏感字段基线（block/mask）对所有人生效（含管理员），角色策略与 RLS 不得弱化或覆盖
- [business_constraint] `constraint_ontology_two_phase`：本体改动只落草案，发布（独立权限码）后才级联重展开对象/重建图谱/同步知识库；不得绕过级联直接改派生表

### 场景（8）
#### 建模进度巡检（`ops_modeling_progress`）
- 目标：回答业务方「本体建到哪了、还缺什么」
- 参与：role:admin
- 用到对象：`ontology_model`、`dataset`
- 用到动作：`ontology.generate`
- 受限于规则：`perm_admin_full_by_role_perms`
- 典型问句：本体建模进度如何？还有哪些表没有进本体？
#### 敏感列补标（`ops_sensitive_marking`）
- 目标：发现未登记敏感基线的列并推动补标（数据护城河完整性）
- 参与：role:admin
- 用到对象：`sensitive_field`、`rls_policy`、`dataset`
- 受限于规则：`quality_sensitive_baseline_immutable`
- 典型问句：哪些敏感列还没有登记脱敏基线？
#### 口径认证推进（`ops_metric_certification`）
- 目标：推动指标/维度口径认证与责任人落实
- 参与：role:admin
- 用到对象：`metric`、`dimension`、`ontology_model`
- 用到动作：`dict.save_metric`
- 受限于规则：`constraint_metric_single_source`
- 典型问句：还有多少指标口径没有认证？谁负责？
#### 契约变更影响分析（`ops_contract_change`）
- 目标：数据产品契约变更前评估影响面（对象/口径/看板）
- 参与：role:admin
- 用到对象：`data_product`、`dataset`、`dashboard`
- 用到动作：`product.contract_change`
- 受限于规则：`perm_admin_full_by_role_perms`
- 典型问句：改这张表的契约会影响哪些看板？
#### 看板设计到发布（`ops_dashboard_publish`）
- 目标：先确认业务范围与治理预览，再发布为看板（不跳过预览）
- 参与：role:admin
- 用到对象：`dashboard_design`、`dashboard`、`dataset`
- 用到动作：`dashboard.publish`
- 受限于规则：`perm_ai_write_fail_closed`
- 典型问句：发布看板需要什么条件？
#### 权限与菜单核查（`ops_permission_audit`）
- 目标：核对角色权限码、菜单显隐与 AI 开放级别的对应关系
- 参与：role:admin
- 用到对象：`role`、`permission`、`menu`、`as_bot`
- 受限于规则：`perm_ai_write_fail_closed`
- 典型问句：哪些权限点配了 AI 级别但没有消费方？
#### 内容草稿到发布（`ops_content_publish`）
- 目标：新建内容草稿走审核后发布，不直接改线上
- 参与：role:admin
- 用到对象：`content_article`、`content_category`
- 用到动作：`content_draft.create`、`content_review.submit`
- 受限于规则：`constraint_ontology_two_phase`
- 典型问句：怎么发布一篇平台公告？
#### 数据工程边界说明（`ops_data_engineering_boundary`）
- 目标：解释同步/DAG/UDF 为什么不在对话里操作（运维专属，需到对应页面人工执行）
- 参与：role:admin
- 用到对象：`sync_task`、`dag_workflow`、`udf`
- 受限于规则：`constraint_data_engineering_ai_closed`
- 典型问句：能让 AI 帮我建一个同步任务吗？

## 模型：云平台运行日志本体（设备·软件·接口遥测）（域别 business）

对象 4　|　规则 0　|　场景 0

云平台 Elasticsearch 侧运行日志业务本体（主源 cloud-platform-es）：以「云平台设备操作日志」「云平台设备告警日志」「云平台软件操作日志」「云平台第三方接口调用日志」四个事实对象，覆盖设备使用强度、设备故障、软件功能使用（含三方集成 App 使用量）、三方接口调用成功率四类分析。与「数据仓库模型」的分界：该模型里的「设备操作日志」对象绑 Doris 数仓的清洗后物化宽表（dwd 层），本对象是**未清洗的原始运行日志**——粒度更细、带明细文本与固件版本，但不做补全与去重。两者条数口径不同，不得相加、也不得互相替代：问原始流水/明细/固件相关用本对象；问已汇总或已清洗口径用「数据仓库模型」的对应对象。数据质量提醒：源内偶有远期脏时间（实测设备操作日志有 3 条落在 2036/2063/2112 年），按时间粒度看趋势时可能出现异常远期桶，属源端脏数据；汇报趋势时应以正常日期区间为准。本域只覆盖云平台 Elasticsearch 侧的四类运行日志：设备操作、设备告警、软件操作、第三方接口调用。桌面 app 的埋点使用事件在另一模型「应用使用事件本体（桌面 app 遥测）」（SLS 通道，对象 app_usage_event），两者的条数口径不同，不得相加或互相替代。站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。

---

### 对象：云平台设备操作日志（`cloud_es_equipment_operation_log`）
别名：云平台设备操作日志、ES设备操作日志、设备操作原始日志、设备操作原始流水、云平台设备操作事件、cloud_es_equipment_operation_log

#### 描述
云平台记录的**单次设备操作事件**（一次扫描/取模/电量读取/事务/系统信息查询等），当前约 2.82 亿条，是设备侧使用强度的主事实表。「操作类型」是本对象最核心的分析维度；「设备序列号」「用户账号」用于过滤单台设备/单个用户或去重计数（按它们分组会超时）。与「数据仓库模型」的分界：该模型里的「设备操作日志」对象绑 Doris 数仓的清洗后物化宽表（dwd 层），本对象是**未清洗的原始运行日志**——粒度更细、带明细文本与固件版本，但不做补全与去重。两者条数口径不同，不得相加、也不得互相替代：问原始流水/明细/固件相关用本对象；问已汇总或已清洗口径用「数据仓库模型」的对应对象。数据质量提醒：源内偶有远期脏时间（实测设备操作日志有 3 条落在 2036/2063/2112 年），按时间粒度看趋势时可能出现异常远期桶，属源端脏数据；汇报趋势时应以正常日期区间为准。检索层能力边界（Elasticsearch）：只支持单索引聚合，不支持 JOIN / 窗口函数 / 派生表 / 季度粒度；按时间看趋势**必须**指定时间粒度（日/周/月），且默认只取前 1000 行、单次查询有 30 秒预算——时间长窗口请配时间粒度或收窄范围。站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。枚举只登记实采码值，不编中文标签（码值本身的业务含义请以产品定义为准确认）。

#### 属性（14）
- 操作时间（datetime）：设备侧事件时间（本地时间，形如 2026-11-18 14:12:30）
- 操作类型（string）：操作行为码值：tiptype, scan, battery, refine, transaction, sysinfo, cut, bitepressure, toothmark, lock, undercut, matching, impressionregion
- 操作应用（string）：上报操作的客户端产品码值：CONNECT, SCANPRO
- 应用名称（string）：应用名称字段（当前样本 99.99% 为空，仅少量带 cloud-platform-v2）
- 站点区域（string）：站点区域码值：cn / global / eu / jp / uk（与 site 列同值）
- 站点（string）：站点码值，与 region 列取值一致（分析时用「站点区域」一个维度即可）
- 操作客户端版本（string）：客户端版本号（形如 1.0.44.0）
- 操作固件版本（string）：设备固件版本号（形如 1.0.3.3）
- 操作设备序列号（string）：设备序列号（去重设备数口径的唯一键；仅用于过滤/去重，不要分组）
- 操作用户账号（string）：操作人账号码（去重用户数口径的唯一键；仅用于过滤/去重，不要分组）
- 操作明细（string）：操作明细文本（自由文本，适合 LIKE 关键词过滤，不适合分组）
- 操作电脑标识（string）：客户端机器标识（仅用于过滤/去重）
- 事务标识（string）：日志侧事务标识（高基数，仅用于定位单条链路）
- 记录写入时间（string）：日志写入时间（与操作时间不同，前者是落库时刻）

#### 关系（0）
- （未登记）

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

#### 可算指标（引用字典，5）
- 设备操作次数、设备操作设备数、设备操作用户数、扫描操作次数、电量操作次数

---

### 对象：云平台设备告警日志（`cloud_es_equipment_warning_log`）
别名：云平台设备告警日志、ES设备告警日志、设备告警原始日志、设备故障原始日志、云平台设备报警日志、cloud_es_equipment_warning_log

#### 描述
云平台记录的**单条设备告警事件**（硬件/固件/系统上报的 warning/error/crash 等），当前约 450 万条。核心维度是「告警级别」「告警硬件模块」「告警码」。「告警码」是高基数码值（TOP 分析可用，不做全量枚举）。检索层能力边界（Elasticsearch）：只支持单索引聚合，不支持 JOIN / 窗口函数 / 派生表 / 季度粒度；按时间看趋势**必须**指定时间粒度（日/周/月），且默认只取前 1000 行、单次查询有 30 秒预算——时间长窗口请配时间粒度或收窄范围。站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。枚举只登记实采码值，不编中文标签（码值本身的业务含义请以产品定义为准确认）。

#### 属性（15）
- 告警时间（datetime）：设备告警的发生时间（本地时间）
- 告警级别（string）：告警级别码值：warning, error, crash, critical, （空串）
- 告警硬件模块（string）：上报告警的硬件模块码值：config, msp430, fpga, sensor, i2c, dlpc, imu, spi, （空串）
- 告警码（string）：告警码（数值字符串，高基数；适合 TOP N 分布，不做全量枚举）
- 告警应用（string）：告警所属客户端产品码值：ASSCANPRO, SCANPRO, CONNECT
- 是否正式版（number）：1=正式版，0=非正式版（样本内全部为 1）
- 站点区域（string）：站点区域码值：cn / global / eu / jp / uk（与 site 列同值）
- 站点（string）：站点码值，与 region 列取值一致
- 告警客户端版本（string）：客户端版本号
- 告警固件版本（string）：设备固件版本号
- 告警设备序列号（string）：设备序列号（去重设备数口径；仅用于过滤/去重，不要分组）
- 告警用户账号（string）：关联用户账号码（仅用于过滤/去重，不要分组）
- 告警内容（string）：告警正文（自由文本，适合 LIKE 关键词过滤）
- 告警记录标识（string）：告警记录唯一标识（仅用于定位单条告警）
- 记录写入时间（string）：日志写入时间

#### 关系（0）
- （未登记）

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

#### 可算指标（引用字典，6）
- 设备告警次数、告警设备数、告警用户数、严重告警次数、崩溃告警次数、告警码种类数

---

### 对象：云平台软件操作日志（`cloud_es_software_app_operation_log`）
别名：云平台软件操作日志、ES软件操作日志、软件操作原始日志、软件使用原始日志、云平台客户端软件日志、三方集成App访问日志、第三方集成App使用日志、App使用日志、cloud_es_software_app_operation_log

#### 描述
云平台记录的**单条软件操作事件**（打开应用/订单导出/病例导出等），当前约 1388 万条。核心维度是「软件应用」「应用分组」「业务类型」「事件来源」「应用管理器」。**问「三方集成 App / 第三方集成 App 的打开量、访问量、使用量、使用用户数」就是本对象**（17 个第三方集成应用码归为「应用分组 = 第三方集成App」，或直接用同名指标）；那不是「第三方接口调用日志」（那是回调调用次数）、也不是联耀「集成记录」（那是对接台账，记录数不是 App 使用量）。注意：本对象是**软件侧行为**，设备操作与设备告警在另外两个对象。检索层能力边界（Elasticsearch）：只支持单索引聚合，不支持 JOIN / 窗口函数 / 派生表 / 季度粒度；按时间看趋势**必须**指定时间粒度（日/周/月），且默认只取前 1000 行、单次查询有 30 秒预算——时间长窗口请配时间粒度或收窄范围。站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。枚举只登记实采码值，不编中文标签（码值本身的业务含义请以产品定义为准确认）。

#### 属性（20）
- 软件事件时间（datetime）：软件操作事件时间（本地时间；列名拼写为 evenTime）
- 软件应用（string）：产生事件的软件应用码值：ASSCANPRO, ASWEB, CONNECT, ModelBuilder, OralHealthReport, NHDataHubIntegration, MeshCompare, NewOrthoSim, SmileDesign, SprintRayIntegration, PressureImpression, coDiagnostiXIntegration（共 25 个取值，按量降序）；其中第三方集成应用码：NHDataHubIntegration, SprintRayIntegration, FormlabsIntegration, coDiagnostiXIntegration, ClinuxCADIntegration, AsigaComposerIntegration, UnizDental, DentreadIntegration, ShapeWareIntegration, CameoPrinterIntegration, 3DXWORKSIntegration, LuxDesignIntegration, NHKeaIntegration, CircleOneIntegration, RapidShapeIntegration, HeyGearsIntegration, DentbirdIntegration（共 17 个第三方集成应用码）
- 事件来源（string）：事件上报来源码值：ASCONNECT, ASWEB
- 业务类型（string）：软件业务类型码值：openApp, orderExport, caseExport
- 事件描述（string）：事件描述文本（样本内多为空，少量如 OPEN ASCONNECT）
- 应用版本（string）：被操作软件的版本号（样本含空串/空值，统计版本分布时需排除）
- 应用管理器（string）：软件归属的应用管理器/品牌码值：ALLIEDSTAR, CEFLAMYSCAN, NEURALHIVE, OWANDYIOS, NEOSS, DYNAMIC, CEFLAVISIOSCAN, AIDITE3D, CEFLAALPHASCAN, CEFLAOPTISCAN
- 站点区域（string）：站点区域码值：cn / global / eu / jp / uk（与 site 列同值）
- 站点（string）：站点码值，与 region 列取值一致
- 软件事件设备序列号（string）：设备序列号（去重设备数口径；仅用于过滤/去重，不要分组）
- 软件事件用户账号（string）：用户账号码（去重用户数口径；仅用于过滤/去重，不要分组）
- 软件客户端版本（string）：客户端版本号
- 软件版本（string）：软件版本号
- 软件固件版本（string）：设备固件版本号
- 公司编码（string）：公司/租户编码（高基数，仅用于过滤，不要分组）
- 订单号（string）：关联订单号（仅用于定位单笔订单）
- 病例编号（string）：关联病例编号（仅用于定位单个病例）
- 操作参数（string）：操作附带参数文本
- 扩展信息（string）：扩展字段文本
- 记录写入时间（string）：日志写入时间

#### 关系（0）
- （未登记）

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

#### 可算指标（引用字典，9）
- 软件操作事件数、软件使用设备数、软件使用用户数、打开应用次数、三方集成App访问量、三方集成App使用用户数、订单导出次数、病例导出次数、使用应用种类数

---

### 对象：云平台第三方接口调用日志（`cloud_es_third_party_request_log`）
别名：云平台第三方接口调用日志、ES接口调用日志、三方接口原始日志、云平台回调日志、接口调用原始记录、cloud_es_third_party_request_log

#### 描述
云平台与第三方系统之间的**单次接口调用记录**（订单/病例回调推送等），当前约 263 万条。核心维度是「业务模型」「响应状态」「请求地址」「调用方应用标识」。失败率口径：按「响应状态」码值 FAIL 计数除以总调用次数（可分组到业务模型/接口地址）。含请求/响应正文的列属系统敏感治理基线范围，本对象未建模。检索层能力边界（Elasticsearch）：只支持单索引聚合，不支持 JOIN / 窗口函数 / 派生表 / 季度粒度；按时间看趋势**必须**指定时间粒度（日/周/月），且默认只取前 1000 行、单次查询有 30 秒预算——时间长窗口请配时间粒度或收窄范围。站点口径：`region` 与 `site` 两列取值一致（日志侧重复写入），已合并为「站点区域」一个维度；取值码值 cn / global / eu / jp / uk。默认分站呈现不合计，跨站合计需显式按「全球」口径说明。枚举只登记实采码值，不编中文标签（码值本身的业务含义请以产品定义为准确认）。

#### 属性（12）
- 接口请求时间（datetime）：接口请求时间（本地时间；源内约 9% 记录该列为空，不计入时间趋势）
- 业务模型（string）：调用对应的业务模型码值：send_order_callback, upload_convert_completed_callback, authorization_completed_callback, upload_zip_completed_callback, upload_attachments_completed_callback, scan_completed_callback
- 响应状态（string）：调用结果码值：FAIL, SUCCESS, ERROR（FAIL 占多数）
- 请求地址（string）：被调用的第三方接口地址（含主机与路径，适合 TOP N 分布）
- 调用方应用标识（string）：调用方系统的应用标识（第三方分配的凭据标识，雪花 id 形态；仅用于过滤/TOP）
- 业务编码（string）：业务编码（高基数：适合 TOP N，不适合全量枚举）
- 其他业务编码（string）：备用业务编码（样本内全空）
- 接口类型码（number）：接口类型数值码（样本内恒为 1）
- 站点区域（string）：站点区域码值：cn / global / eu / uk（与 site 列同值）
- 站点（string）：站点码值，与 region 列取值一致
- 响应时间（string）：响应时间字段（样本内与请求时间同值，不能用于计算耗时，故不建耗时指标）
- 记录写入时间（string）：日志写入时间

#### 关系（0）
- （未登记）

#### 行为（0）
- （未登记）

#### 动作（0）
- （未登记）

#### 可算指标（引用字典，6）
- 接口调用次数、接口调用失败次数、接口调用成功次数、接口调用方数、接口地址数、业务模型数

---

### 规则（0）
- （未登记）

### 场景（0）
- （未登记）
