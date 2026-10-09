# 可信分析平台 — 实施进度与续接指南

> 当前新增任务：**Waker 单实例安全修复仍在实施中，尚未完成最终验收或服务重启**。跨账号接续请先阅读 [Waker 安全修复交接文档](WAKER_SECURITY_HANDOFF.md)，其中记录了代码范围、真实验证结果、环境变更、待修风险和接续命令。下文保留原有可信分析平台任务进度，不代表本次 Waker 修复已完成。

> 本文档用于跨 session 续接。完整执行计划见 `/root/.config/Qoder/SharedClientCache/cache/plans/可信分析平台执行计划_c79b35e8.md`（本地缓存），或重新根据对话上下文重建。
> 最后更新：2026-09-21

> **路径迁移映射（防口径漂移，2026-10 架构重构后适用）**：下文历史叙述中的路径为迁移前旧路径，按此表换算为当前真源——
> `services/shared/common/` → `backend/common/`；`services/shared/semantics/` → `backend/semantics/`；`services/shared/eval/` → `backend/eval/`；
> `services/{datamind,datacatalog,dataviz,datagov,dataflow,aiplatform,authservice,semhub}/` → `backend/modules/{mind,catalog,viz,gov,flow,platform,auth,semhub}/`；
> `services/graphservice/`（已并入 semhub）→ `backend/modules/semhub/graph/`；治理内核（query_executor/governed_query/enforcer/role_service/rls_service/task_runtime/perm_link）→ `backend/core/`；
> 各 `services/<svc>/main.py` 进程壳已删（Phase 4）→ 唯一 web 入口 `backend/processes/main.py`（单进程绑 8001-8007/8012），进程拓扑 = web + celery-worker + celery-beat + dataengine；
> eval 命令 `services.shared.eval.runner`/`tests.eval.runner` → `venv/bin/python -m backend.eval.runner`；`.env` 加载口径（`services/.env` 优先、`backend/.env` 兜底）见 `backend/common/config.py`。

---

## 一、核心定位（不可遗忘）

- **AS-BOT** 是应用内操作智能体：管理本体、元数据、应用配置，复用审批通道。
- **业务本体** 仅服务数据分析（定义对象/属性/指标/维度/关系/分析约束），没有业务 Action 是正常设计。
- **本轮目标**：任何入口取数都受治理；任务状态真实；同一口径计算一致；报告结论有证据；变更收益可以评测。
- **不做**：业务写回（工单/订单）、业务状态机、新菜单体系、新图数据库、新审批平台。

---

## 二、里程碑总览

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 基线 | ✅ 已完成 | 编译基线 42/42；安全回归测试 95 项通过 |
| M1 治理补漏 | ✅ 大部分完成 | 身份/权限/SQL 护栏/质量/报告读取/前端渲染已收敛；分享 token 生命周期待补 |
| M2 执行收敛 | ✅ 完成（待部署验证） | 统一执行核心+时区/租约/重试/取消、报告生命周期、AS-BOT 原子审批与真实同步均落地并回归；真实队列 e2e 需部署环境复核 |
| M3 分析契约 | ⏳ 未开始 | 指标维度约束、质量上下文、语义版本一致性 |
| M4 证据化报告 | ⏳ 未开始 | 确定性统计、EvidenceBundle、事实校验、数字校验 |
| M5 发布验收 | ⏳ 未开始 | 三层评测扩展、端到端回归、灰度交付 |

---

## 三、M0 已完成工作

### 3.1 编译基线
- `venv/bin/python -m tests.eval.runner --json` → **42/42 全部通过**，accuracy=1.0
- 基线 JSON 已保存：`tests/eval/compiler_baseline.json`
- 26 个 tag 全覆盖：alias(8), combo(3), cross_source(3), dimension(10), enum(4), exact(4), filter(5), metric(8), time_window(5) 等

### 3.2 安全回归测试基线
新建测试文件（全部通过）：
- `tests/test_trusted_analysis_security.py` — 17 项：空权限拒绝、AST 护栏、JWT 伪造、工作空间越权、报告公开入口等
- `tests/test_quality_moat.py` — 质量检查必须走治理入口
- `tests/test_report_access.py` — 报告私有/公开/外链/token 过期/跨空间/禁用创建者

### 3.3 运行证据
```
venv/bin/python -m pytest tests/test_trusted_analysis_security.py tests/test_quality_moat.py tests/test_report_access.py tests/test_data_moat_enforcement.py tests/test_permission_enforcer.py tests/test_permission_e2e.py -q
→ 95 passed
```

---

## 四、M1 已完成工作

### 4.1 可信身份与工作空间

**`services/shared/common/auth.py`**：
- 新增 `resolve_current_user(token)` — JWT 验签 + 用户状态校验，旧 JWT 不保留已撤销权限
- 新增 `authorize_workspace(user, workspace_id)` — 客户端 workspace_id 只是选择器，必须服务端校验访问资格

**`services/shared/common/api_permission.py`**：
- `_CACHE_TTL = 0.0` — 不再缓存权限（安全优先）
- 重写白名单：`_PUBLIC_ROUTES`（登录/健康）、`_SELF_ROUTES`（个人信息）、`_REPORT_SHARE`（具体分享入口正则）、`_INTERNAL_ROUTES`
- 禁止宽泛前缀绕过，报告分享只放行具体路由
- 导入 `HTTPException` 和 `run_in_threadpool`

**`services/authservice/services/role_service.py`**：
- 角色查询增加 `workspace_id IN (0, %s)` 作用域限制

**`services/authservice/api/roles.py`**：
- 未配置权限 = 拒绝（不再 `unrestricted: True`）

### 4.2 SQL AST 护栏

**新建 `services/shared/semantics/sql_guard.py`**（105 行）：
- `parse_query()` — sqlglot AST 解析，拒绝 DDL/DML/Command/Into/Lock，拒绝外部表函数
- `extract_tables()` — 基于 AST 提取真实表（覆盖 CTE、子查询、JOIN 两侧、限定名）
- `inject_filters()` — 基于 AST 注入 RLS 包裹子查询，保留别名，覆盖 FROM 和 JOIN 两侧
- `bounded_query()` — 自动补 LIMIT

**`services/datamind/permission/enforcer.py`**：
- 改用 `sql_guard.extract_tables` 替代旧正则
- 改用 `sql_guard.inject_filters` 替代旧字符串替换 RLS 注入
- 始终 AST 解析真实查询，不信任调用方传入表清单

**`services/shared/semantics/rls.py`**：
- 改用 `sql_guard.inject_filters`，失败时拒绝执行而非回退裸查询

**`services/datamind/nl2sql/sql/query_executor.py`**：
- `validate_sql` 改用 `sql_guard.parse_query` AST 校验

### 4.3 质量检查治理

**`services/datagov/services/quality_engine.py`**：
- 新增 `_open_target_conn()` — 必须可信身份 + 工作空间授权，不再打开裸连接
- 新增 `_governed_rows()` — 通过治理执行器取数
- 导入 `authorize_workspace`、`governed_execute`、`sql_guard`

**`services/datagov/api/quality.py`**：
- 新增 `_quality_access()` 依赖 — 所有质量入口显式校验工作空间，规则 ID 不能越权

### 4.4 报告访问控制

**新建 `services/dataviz/services/report_access.py`**（100 行）：
- `policy_snapshot()` — 报告创建时快照化权限策略
- `can_access_report()` — 统一私有/公开/外链访问判定
- `public_report()` — 公开入口校验
- `snapshot_is_current()` — 检查策略快照是否过期
- token 使用 SHA256 摘要存储，支持过期校验

**`services/dataviz/services/report_service.py`**：
- 导入 `report_access` 模块
- `list_reports()` 增加 `user` 参数，校验身份和工作空间

**`services/dataviz/api/report.py`**：
- 所有端点增加 `Depends(get_current_user)` 和 `authorize_workspace`
- 列表/详情/生成统一走 `report_service`

### 4.5 服务路由注册

以下服务的 `main.py` 已补注册 `add_api_permission_middleware`：
- `services/datagov/main.py` ✅
- `services/dataviz/main.py` ✅
- `services/graphservice/main.py` ✅
- `services/semhub/main.py` ✅

### 4.6 执行层身份注入

**`services/datamind/api/execution.py`**：
- `get_workspace_layers` 增加 `Depends(get_current_user)` + `authorize_workspace`
- `execute_task` 增加 `Depends(get_current_user)` + `authorize_workspace`

### 4.7 前端安全渲染

**新建 `frontend/src/components/ReportContent.tsx`**（18 行）：
- DOMPurify 白名单清洗 HTML 报告
- 统一 Markdown/HTML 渲染入口

**`frontend/src/pages/ReportView.tsx`**：
- 改用 `ReportContent` 组件
- 支持 `access_warning` 和 `generation_status` 字段

**`frontend/src/pages/ReportsCenter.tsx`**：
- 改用 `ReportContent` 组件

**`frontend/src/api/scheduledTask.ts`**：
- `ReportDetail` 接口增加 `access_warning`、`generation_status`、`evidence_summary`

**`frontend/package.json`**：
- 已安装 `dompurify` 依赖

### 4.8 定时任务 API

**`services/dataflow/api/scheduled.py`**：
- 新增 `_scheduled_access()` 依赖 — JWT 身份校验
- 导入 `resolve_current_user`、`authorize_workspace`、`report_service`

**`services/dataflow/services/scheduled_task_service.py`**：
- `create_report()` 委托给 `report_service.create_report`

---

## 五、M2 执行收敛（已完成，待部署环境端到端复核）

### 5.1 统一任务执行核心

**目标**：Celery、同步、FastAPI 后台三条路径共用同一执行核心。

**主要文件**：
- `services/dataflow/tasks/executor.py` — 共用 `_execute_core`，Celery / 同步 / 后台仅作适配
- `services/dataflow/tasks/celery_app.py` — Celery 配置
- `services/dataflow/api/scheduled.py` — HTTP API
- `services/dataflow/services/scheduled_task_service.py` — 任务服务

**本轮已实现并回归**：
1. 三种适配器共用 `_execute_core`，后台通过 `asyncio.to_thread` 传播上下文。
2. 创建任务拒绝无创建者；每次运行经 `resolve_execution_owner` 重查状态、角色、工作空间，并校验数据源关联。
3. `ownership_status=unclaimed` 显式展示；无归属任务不能手动执行，也不进入 Beat 调度。不自动归给 admin。
4. SQL 经 `bounded_query` 补外层 LIMIT、拒绝写操作后调用 `governed_execute`；移除已删除执行器引用。
5. 无人值守 Agent 使用 `execution/scheduled_analysis.py` 编排实时业务目录和语义七闸门；身份由服务端注入。无查询结果、需澄清、非法工具调用均不能标成功。
6. 状态统一为 success/partial/failed/timeout/cancelled；必需报告失败则任务失败。通知只发送状态及需登录的链接，不发送业务正文、数值或 token。
7. 手动/Beat 在派发前创建 queued 日志和运行键；唯一索引防重，CAS 领取运行；日志和任务终态事务提交。SQL 有限重试复用同一日志，不重复生成报告或发送通知。
8. 数据库租约 + 定期 reconcile 处理遗留 queued/running；取消后迟到结果不能覆盖终态。租约比较统一使用数据库时钟，已通过不同主机/数据库时区的集成回归。
9. 前端展示排队、部分成功、待认领及阶段错误码；删除人工“标记成功/失败/超时”按钮，保留取消操作。
10. **单任务时区**：`WallClockCrontab` 按任务 `timezone` 的墙钟分钟匹配，DST 跳时不补跑、回拨同一墙钟分钟仅一次；运行键含时区，同一 UTC 瞬间在跨时区任务不会相互覆盖。
11. **Beat 短租约**：`BeatLease`（Redis 锁 15s 持续续期）取代旧 24h 一次性锁；丢锁/Redis 故障即暂停派发，备用实例租约到期后接管，杜绝双 Beat 双发。
12. **取消/超时可中止**：`task_runtime.guarded_call` 在阻塞取数外层按 0.1s 轮询租约与 stop_event，取消后丢弃迟到结果、禁止后续操作与发布；守护线程不阻塞宿主关闭。
13. **Agent 有限重试**：瞬时故障（LLM/MCP 不可用）按 `max_retries` 复用同一日志重试；需人工处理（口径拒绝/需澄清）不重试。SQL 同样有限重试。
14. **外部 MCP/自定义 Agent 收敛**：无人值守路径只接受 `propose_semantic_intent` 契约——外部服务仅提议声明式意图，平台校验后走本地语义七闸门取数；外部返回的 rows/SQL/身份字段一律拒收。未授权该契约的 MCP、越权数据源的 Agent 直接拒绝（`TOOL_SCOPE_DENIED`），不回退裸 SQL。

**部署环境待复核（非离线单测可覆盖）**：
- 真实 Celery/Redis broker 已验证：隔离 Redis 上真实 worker 收到并执行了 `execute_scheduled_task`、结构化结果经 broker 回传（未知任务安全返回 `TASK_NOT_FOUND`，不崩溃）。但 `config.py` 以 `load_dotenv(override=True)` 强制加载 `services/.env`，离线无法把 worker 的元数据库同时指向隔离 MySQL，故「broker+隔离DB 单进程全链路」需在按环境正确配置 `services/.env` 的部署环境复核。
- 完整 `_execute_core` DB 全路径（create_log 幂等 / claim CAS / 身份解析 / 数据源校验 / 终态事务 / run_count / 重投幂等 / 无归属拒绝）已在隔离 MySQL 集成测试中跑通（仅 mock 叶子数据源执行）。
- 进程崩溃后遗留 running、Worker 重启、Beat 锁续期与真实数据源超时的现场演练属部署验收项。

### 5.2 报告服务收敛

**本轮已实现并回归**：
1. 修复原 `/generate` 调用不存在的 `submit_report` 断链；共用 `ReportSubmission` 校验数据集/语义意图/已保存查询三选一，缺失或歧义来源返回 422。
2. 先持久化 `adh_reports` 的运行记录与来源，再真实派发；返回 202 + report_id/run_id，派发失败落 failed 并返回受控 503。
3. 新增 `queued → running → degraded/failed/timeout` 状态闭环，预留 `ready` 给 M4 通过证据校验后的产物；重复派发不能重复执行。
4. `/api/reports/generate` 与 `/api/scheduled-tasks/reports/generate` 共用服务；定时报告也委托该领域服务渲染与保存。
5. 保存查询按 owner + workspace 读取，缺少明确 datasource 绑定的历史查询拒绝执行；数据集消费原 scopes；语义意图经原编译和七闸门链，不接受客户端身份覆盖。
6. 捕获取数前后权限快照；数据集配置/行范围变化也会使旧报告不可重放；关联任务取消/失败的报告不开放正文。
7. 报告页面可轮询 queued/running 并展示降级状态；日志链接不携带分享 token。

**明确边界**：当前只生成私有、不可分享的降级事实表，展示受治理返回样本，不对总体、趋势或因果作推断。物理列未取得业务映射认证时使用“字段1…”列头；模板样式、语义版本、完整性证明、LLM 事实引用与数字校验属于 M3/M4，未完成，不能把 `degraded` 当作 `ready`。

### 5.3 AS-BOT 应用内动作

**主要文件**：
- `services/datamind/api/as_bot.py`
- `services/datamind/execution/wakers.py`

**本轮已实现并回归**：
1. `metadata.sync` 调用 `MetadataService.sync_metadata`，只有实际同步成功才返回成功；失败细节仅进服务端日志。
2. `pending → executing` 原子领取审批；重复批准/批准与拒绝并发只允许一方获胜；执行终态只能由领取者提交。
3. 参数在提议期与执行前双重校验；审核人、提议人由服务端注入。修复原 `LAST_INSERT_ID()` 跨连接读取导致的审批 ID 错误。
4. 注册 `task.claim_owner`，仅认领无创建者任务，目标创建者来自审批记录中的提议人；沿用同一审批流水，不提供直接认领写接口。

**已完成**：`data_analysis` 与 `orchestrator` 提示词已改为语义优先——对象已绑定一律走 `run_semantic_query`，仅当语义层未覆盖且会话绑定 SQL 工具时才 `generate_sql→execute_sql`，并明确 `execute_sql` 始终经统一治理执行器（非裸连旁路）。`data-analysis` SKILL 原本已语义优先。无人值守路径（`scheduled_analysis.py`）从结构上只暴露 `get_metrics`/`run_semantic_query`/`needs_clarification` + MCP 意图提议契约，不含任何裸 SQL 工具。面向可信开发人员的 SQL Playground 能力未改变。

### 5.4 迁移与最新验证证据

- 增量迁移：`docker/mysql/trusted_execution_migration.sql`，覆盖报告状态/来源/权限快照/分享安全字段、执行租约/运行键唯一索引、审批 executing 状态、保存查询来源字段。无演示数据，不猜历史归属或语义版本。
- 仅在本轮创建的独立临时 MySQL 容器中执行迁移与数据库测试；每个测试数据库均隔离，迁移连续执行两次验证幂等。**未迁移实际服务数据库，未重启实际 API/Worker，不代表线上生效。**
- 新增队列依赖 `celery[redis]>=5.3,<6`；本地虚拟环境补齐 Celery/Redis 与已有 LLM 依赖。Celery 任务与队列注册检查通过。
- 执行模式：默认 `ADH_TASK_EXECUTION_MODE=celery`；显式设置 `background` 才启用本地后台。Redis 地址使用 `REDIS_URL`。不自动把不可用队列伪装为执行成功。

| 验证项 | 最新结果 |
|---|---|
| 后端执行/报告/审批/安全/建模/Waker 合并回归 | **281 passed** |
| 隔离 MySQL：迁移重跑、并发领取、终态保护、认领、通知幂等、报告租约过期、`_execute_core` 全路径与重投幂等 | **10 passed** |
| 真实 broker e2e（隔离 Redis+真实 worker） | worker 收到并执行 `execute_scheduled_task`、结果经 broker 回传；未知任务安全返回 `TASK_NOT_FOUND` |
| 原编译 eval | **42/42，accuracy=1.0**，26 个 tag 均无回退 |
| 分解析来源 | dict_exact=46、dict_alias=9、physical_desc=1、phys_col=2、fuzzy_rejected=1，与改前一致 |
| 新增前端执行历史专项 | **2 passed** |
| 前端全量测试 | **293 passed / 2 failed**；失败位于 `stores/__tests__/chatStore.test.ts`（会话切换 selectedDsId、clear），为 M2 之前已存在、与执行收敛无关 |
| 前端构建（tsc） | M2 所涉文件（ReportsCenter/ReportView/ScheduledTasks/ScheduledTaskLogs/ScheduledTaskForm/scheduledTask.ts）**零错误**；仓库存量错误由 89 降至 **63**（其余为 App/WorkspaceManagerV2/SkillsTemplateManager 等无关文件的遗留漂移，属 M5 发布门禁） |

本轮新增/修复红项：单任务时区与 DST、Beat 双发（短租约）、取消/超时不可中止、Agent 无重试、外部 MCP/Agent 工具范围未收敛、报告租约过期仍可写伪成功、`finish_report` 未校验终态。均新增回归用例并转绿。检索策略未调整，未触发回退决策。

---

### 5.5 AS-BOT 聚焦本系统 + 元数据同步归位（本轮增补）

背景：用户问 AS-BOT「近3天有用户使用 chat 吗」，它却检索到业务本体 test-alb；问「交互可观测近三天用量」，业务语义层答「未建模」。根因：系统用量属**平台自身可观测数据**（`adh_llm_traces`），既不在业务本体（也不应在），AS-BOT 又缺少查它的工具，于是回退知识检索并串入业务本体。

**已落地**：
1. `sync/metadata_sync.py` → `services/datacatalog/services/metadata_sync.py`（与 `metadata_service.py`/`relation_service.py` 同包），移除失效的 `sys.path` hack，更新 3 处引用与 CLI 用法；`sync/` 仅剩空 `__init__.py`。
2. 新增受控 `system` 工具组 `sdk_tools/system_tools.py`（server `datahub_system`）：`system_usage`（复用 `observability_service`，按 days/start-end/entrypoint/workspace/group_by 查用量与活跃）、`system_overview`（数据源/表列/本体/知识库/待审批/待处理别名/近24h失败任务只读快照）。两者**仅管理员**（经 `resolve_execution_owner` 服务端解析角色，fail-closed），只读、复用既有服务、不新建取数通道、不连业务数据源。已注册进 `TOOL_SERVER_BUILDERS/TOOLS`，并加入 `resolve_system_bot_waker` 强制工具组。
3. 检索系统级**硬限定**：`qmind_retrieve(..., system_scope=True)` 绝不回退 graphrag 业务元数据；`knowledge_search` 检测到系统 AS-BOT（`ctx.extra.waker_key == __system_bot__`）时只检索其绑定系统知识库并置 `system_scope`，无命中即空——杜绝 test-alb 业务本体串入系统问题。
4. 提示词/工具描述引导：`system_usage`/`knowledge_search` 描述与 AS-BOT 种子提示词明确「系统运营/用量走 system_* 工具，knowledge_search 只用于本体/口径建模」。种子提示词仅对全新安装生效；既有部署靠**代码内工具描述 + 强制工具组 + 检索硬限定**生效（`resolve_system_bot_waker` 覆盖 DB groups，无需迁移即拿到 system 工具）。

**边界**：业务数据分析 chat 对「交互可观测用量」回答「未建模」是**正确**的——系统遥测不是业务本体数据，不应进业务语义层；正确答案在 AS-BOT `system_usage` 或「LLM 交互可观测」看板（`/system/observability`）。

**验证**：新增 `tests/test_as_bot_system_tools.py`（15 项：管理员门禁 fail-closed、用量/总览、入口白名单防注入、系统级不回退业务、knowledge_search 传 system_scope、工具组注册）；后端合并回归 **286 passed**；eval **42/42** 且分来源分布不变（检索作用域改动不触业务 golden 集）。

---

### 5.6 AS-BOT 会话历史与恢复（本轮）

背景：AS-BOT 悬浮助手（`frontend/src/stores/asBotStore.ts`）此前仅内存留存 `messages`，刷新/关面板即丢，无历史列表、无法恢复、不带 `conversation_id` 做多轮衔接（主 `chatStore` 早已有，但 AS-BOT 面板未接入）。

**已落地**（复用主聊天的 `adh_conversations` 持久化机制，按 `waker_key` 隔离）：
1. 迁移 `docker/mysql/conversation_waker_key_migration.sql`：为 `adh_conversations` 幂等新增 `waker_key`（默认 `''`，存量不猜归属）+ `idx_user_waker_updated`（已在隔离 MySQL 连跑两次验证幂等）。
2. `datamind/api/chat.py`：`create` 接收并落库 `waker_key`；`list` 新增 `waker_key` 参数——传 `__system_bot__` 只看 AS-BOT 会话，未传的业务清单默认 `waker_key <> '__system_bot__'`，两套历史互不串台（所有权仍按 user_id）。
3. `asBotStore`：新增 `loadConversations/newConversation/switchConversation/deleteConversation`；首次发送惰性建会话（空会话不落库），每轮 done/error/取消/异常后与 approve/reject 后均 PUT 转录（截断工具输出防膨胀）+ 标题（首条用户消息派生）+ `executor_session_id`；发送带 `conversation_id`+`session_id`，恢复后服务端按会话池 resume 多轮。
4. `AsBotPanel`：顶部新增“新对话”“历史记录”按钮，历史浮层可点击恢复/删除。

**合规**：转录落库（共享层）而非仅浏览器/进程内存，符合分布式优先；持久化/切换失败静默不阻断当前对话（旁路降级，非功能主链掩盖）。**边界**：这是会话记录/恢复，不依赖 `CACHE_BACKEND`（属后续 Redis 改造项）。需重启 datamind(8001) 生效，并在服务库执行上述迁移。

**验证**：新增 `tests/test_as_bot_history.py`（4 项：系统/业务清单作用域、create 打标、默认空串）；AS-BOT 相关后端 95 passed；`asBotStore/AsBotPanel/chat.py` tsc/GetProblems 零错误。

---

### 5.7 统一 Redis 缓存（本轮，分布式优先）

背景：原缓存层虽预留 Redis 后端，但 `CACHE_BACKEND` 默认 `local` 且从未配置（实际全走进程内 `TTLCache`）；`cache/factory` 唯一的 Redis 消费者 `rag/metadata_cache.py` 无人引用（死代码）；`TTLCache` 为进程内 → 多实例各存各的、重启即丢、`/api/cache` 的 invalidate 不跨实例。均属分布式优先反模式。

**已落地**（按“去掉本地缓存、全部走 Redis”）：
1. `cache/factory.py` 改为 Redis-only（删 `LocalCache` 分支与“不可用则降级本地”回退），优先读 `REDIS_URL`（compose 已注入），回退 `REDIS_HOST/PORT/DB/PASSWORD`；`RedisCache` 新增 `url` 与命中计数器 `incr/get_counter`。删除 `cache/local.py`。
2. `ttl_cache.py` 重写为 **Redis 支撑的同名同接口外壳**（get/set/invalidate/invalidate_prefix/get_or_set/stats 不变），5 个实例名与 TTL 不变 → datasource/menu/dashboard/brand/metadata 缓存跨实例共享、重启不丢，**调用点零改动**。maxsize LRU 逐出改为逐 key TTL 回收（键空间按数据源/用户/菜单维度有界）。
3. 删除死代码 `datamind/rag/metadata_cache.py`；`.env.example` 补 `REDIS_URL`/`REDIS_*` 说明。`api_permission._CACHE_TTL=0`（权限不缓存）故意保留不变——缓存权限会重新引入 M1 移除的越权风险。

**降级口径（区别于 no-silent-degradation）**：缓存是性能旁路；Redis 不可达时 `RedisCache.get/set` 透明降级为“无缓存”（回源计算、记 warning），不返回错值也不切本地。与“功能主链失败必须显式”不同，不属掩盖。

**验证**：新增 `tests/test_redis_cache_backend.py`（get_or_set 只算一次、None 不缓存、invalidate/prefix、stats 计数、factory 仅 Redis、local 已移除、Redis 挂时降级）；真实 Redis(6379) 回环验证通过；消费方（query_executor/datasource/menu/dashboard）导入 + data_moat/permission/dataset 共 70+22 通过；ttl/cache 改动文件 GetProblems 零错。

**生效**：需重启使用缓存的服务 datamind(8001)、datacatalog(8005)、dataviz(8004)、aiplatform；确保它们环境有 `REDIS_URL`（compose 已给）。（未迁移生产库/未重启。）

---

## 六、M3 待完成（分析契约）

1. 指标增加 `analysis_contract` JSON（可加性、粒度、允许维度、除零策略等）
2. 维度增加 `analysis_contract` JSON（时区、日历、层级约束）
3. 缺失请求指标/过滤维度不能静默删除后继续查另一组数据
4. 质量状态进入分析上下文（复用 `adh_quality_rules/results`）
5. 语义快照 + revision 条件更新防并发覆盖
6. 图谱同步状态不能只比较对象数量

---

## 七、M4 待完成（证据化报告）

1. `EvidenceBundle` 数据结构（run_id、语义版本、时间锚点、授权范围、质量状态等）
2. 确定性分析算子（汇总、排名、同比/环比、维度贡献分解）
3. LLM 输出结构化段落 + evidence_id 引用 + 占位符填充
4. 发布前校验引用存在、数字一致、图表同源
5. 证据快照独立存储，关联报告
6. 报告中心展示数据期间、语义版本、质量状态、校验状态

---

## 八、M5 待完成（评测与发布）

1. 编译层新增聚合约束、过滤拒绝、时间对齐用例
2. 智能链路：至少 60 个自然语言问题
3. 业务结果：固定数据验证统计数值与权限隔离
4. 前端 `npm test` + `npm run build` 回归
5. 数据库迁移脚本（增量、幂等）
6. 服务重启清单

---

## 九、关键文件清单

### 累计新增关键文件
| 文件 | 用途 |
|---|---|
| `services/shared/semantics/sql_guard.py` | SQL AST 安全解析与 RLS 注入 |
| `services/dataviz/services/report_access.py` | 报告统一访问控制 |
| `frontend/src/components/ReportContent.tsx` | 报告安全渲染（DOMPurify） |
| `tests/test_trusted_analysis_security.py` | M0/M1 安全回归 17 项 |
| `tests/test_quality_moat.py` | 质量检查治理回归 |
| `tests/test_report_access.py` | 报告访问控制回归 |
| `tests/eval/compiler_baseline.json` | 编译基线快照 |
| `services/datamind/execution/scheduled_analysis.py` | 无人值守语义分析适配器（含 MCP 意图提议契约） |
| `services/shared/common/task_runtime.py` | 可中止等待 `guarded_call`/`RunInterrupted`（取消/超时不发布迟到结果） |
| `services/datamind/execution/sdk_tools/system_tools.py` | AS-BOT 本系统只读运营/可观测工具组（system_usage/system_overview，仅管理员） |
| `tests/test_as_bot_system_tools.py` | AS-BOT 系统能力 + 检索系统级硬限定回归 |
| `docker/mysql/trusted_execution_migration.sql` | M1/M2 增量迁移 |
| `tests/test_scheduled_execution.py` | 统一执行、状态、取消、治理与 Agent 边界 |
| `tests/test_report_generation.py` | 来源校验、派发、报告终态和范围变化 |
| `tests/test_as_bot_execution.py` | 原子审批、真实同步与身份注入 |
| `tests/test_trusted_execution_mysql.py` | 默认跳过、显式启用的隔离 MySQL 集成测试（含 `_execute_core` 全路径） |
| `frontend/src/components/__tests__/ScheduledTaskLogs.test.tsx` | 执行历史真实状态与取消回归 |

### 修改文件（本次 session，关键项）
| 文件 | 变更摘要 |
|---|---|
| `services/shared/common/auth.py` | 新增 `resolve_current_user`、`authorize_workspace` |
| `services/shared/common/api_permission.py` | 重写白名单、TTL=0、禁止宽泛前缀 |
| `services/authservice/api/roles.py` | 未配置权限=拒绝 |
| `services/authservice/services/role_service.py` | 角色查询增加工作空间作用域 |
| `services/datamind/permission/enforcer.py` | 改用 sql_guard AST 表提取和 RLS 注入 |
| `services/shared/semantics/rls.py` | 改用 sql_guard inject_filters |
| `services/datamind/nl2sql/sql/query_executor.py` | validate_sql 改用 AST 校验 |
| `services/datagov/services/quality_engine.py` | 治理入口取数，不再裸连接 |
| `services/datagov/api/quality.py` | 增加 `_quality_access` 依赖 |
| `services/datagov/main.py` | 注册权限中间件 |
| `services/dataviz/main.py` | 注册权限中间件 |
| `services/graphservice/main.py` | 注册权限中间件 |
| `services/semhub/main.py` | 注册权限中间件 |
| `services/dataviz/services/report_service.py` | 导入 report_access，list_reports 增加身份校验 |
| `services/dataviz/api/report.py` | 全部端点增加身份+工作空间校验 |
| `services/datamind/api/execution.py` | 增加身份+工作空间校验 |
| `services/dataflow/api/scheduled.py` | 增加 `_scheduled_access` 依赖 |
| `services/dataflow/services/scheduled_task_service.py` | create_report 委托 report_service |
| `frontend/src/pages/ReportView.tsx` | 使用 ReportContent，支持 access_warning |
| `frontend/src/pages/ReportsCenter.tsx` | 使用 ReportContent |
| `frontend/src/api/scheduledTask.ts` | ReportDetail 增加安全字段 |
| `frontend/package.json` | 安装 dompurify |

---

## 十、运行测试命令

```bash
# 编译基线
venv/bin/python -m tests.eval.runner --json

# 安全回归（M0/M1）
venv/bin/python -m pytest tests/test_trusted_analysis_security.py tests/test_quality_moat.py tests/test_report_access.py tests/test_data_moat_enforcement.py tests/test_permission_enforcer.py tests/test_permission_e2e.py -q

# 护城河三件套
venv/bin/python -m pytest tests/test_data_moat_enforcement.py tests/test_permission_enforcer.py tests/test_permission_e2e.py -q

# 建模回归
venv/bin/python -m pytest tests/test_cloud_md_redaction.py tests/test_doc_freshness.py tests/test_alias_suggestions.py tests/test_terminology_scoping.py tests/test_planner_resolution.py tests/test_eval_baseline.py -q

# M2 新增专项
venv/bin/python -m pytest tests/test_scheduled_execution.py tests/test_report_generation.py tests/test_as_bot_execution.py -q

# 隔离 MySQL 集成测试默认跳过；仅给独立测试实例设置以下环境变量后执行
# ADH_TEST_MYSQL_ENABLE=1、ADH_TEST_MYSQL_PORT、ADH_TEST_MYSQL_PASSWORD
venv/bin/python -m pytest tests/test_trusted_execution_mysql.py -q

# 前端
npm --prefix frontend test
npm --prefix frontend run build
```

---

## 十一、服务重启端口

| 服务 | 端口 | 备注 |
|---|---|---|
| datamind | 8001 | uvicorn 无 --reload |
| datacatalog | 8005 | uvicorn 无 --reload |
| dataviz | 8004 | |
| datagov | | |
| semhub | | |
| graphservice | | |
| dataflow worker | | Celery worker |

---

## 十二、续接建议

1. **先跑测试**确认当前状态：`venv/bin/python -m pytest tests/test_trusted_analysis_security.py tests/test_quality_moat.py tests/test_report_access.py -q --tb=short`
2. **读取计划文件**：`/root/.config/Qoder/SharedClientCache/cache/plans/可信分析平台执行计划_c79b35e8.md`
3. **M2 已完成，下一步进 M3**：部署时先执行 `docker/mysql/trusted_execution_migration.sql` 并重启 dataflow Worker/Beat + dataviz + datamind；在按环境正确配置 `services/.env` 的部署环境复核「broker+DB 单进程全链路」、进程崩溃/Worker 重启/真实数据源超时演练。随后按 M3（分析契约）→M4（证据化报告）→M5（发布验收）推进；不能把降级事实表当成证据化报告完成。前端 2 项 chatStore 失败与 63 项存量 tsc 错误属 M5 发布门禁，与 M2 无关。
4. **遵守三条规则**：`.qoder/rules/` 下的 `fde-evolution.md`、`ontology-modeling.md`、`security-guardrails.md`
5. **不改计划文件**：保留既有计划，本轮仅更新实施进度。
6. **部署前先核准环境**：实际服务库仍需执行增量迁移，再重启相关 API 与 Worker/Beat；不要直接重跑含演示数据的全量迁移。未获得隔离验证与发布门禁通过前，不将本轮状态标为已上线。
