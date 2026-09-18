# 设计文档：LLM 交互全链路可观测（Observability）

> 状态：设计方案（未实现） · 对应 `docs/todo.txt` 第 2 条 + 赞踩补充
> 目标：在管理端提供 **session 清单 / 用户用量 / 模型用量 / 对话执行 trace / 非 chat 输出的
> 工具与 MCP 产物（语义层SQL、nl2sql产出SQL、权限改写前后SQL、SQL执行输出）** 的完整可观测链路；
> Chat 侧对每次回答做 **满意/不满意（赞踩）+ 原因** 收集，并与 trace 关联。

---

## 1. 现状盘点（务必先读，决定"新增 vs 复用"）

### 1.1 已有的可观测底座（可复用）
| 能力 | 位置 | 现状 |
|---|---|---|
| 逐次提问审计 | 表 `adh_query_audit`（**MySQL 元库**，[init.sql:120](file:///home/star/project/feature/AI-DataHub/docker/mysql/init.sql#L120)），写于 [query_executor.py:889-926](file:///home/star/project/feature/AI-DataHub/services/datamind/nl2sql/sql/query_executor.py#L889)（走 `get_metadata_conn`），读于 [history.py](file:///home/star/project/feature/AI-DataHub/services/datamind/api/history.py) | ⚠️ 代码注释误标 "in Doris"，实为写元库 MySQL；有 `question/generated_sql/execution_status/row_count/execution_time_ms/error_message/username/user_role/workspace_id/datasource_id`；**仅 NL2SQL 单跳**，无多步 trace、无 token/成本 |
| 权限改写审计 | 表 `adh_rls_audit_logs`，写于 enforcer `_write_audit` / [query_executor._log_permission_audit](file:///home/star/project/feature/AI-DataHub/services/datamind/nl2sql/sql/query_executor.py) | 有改写策略留痕；**未存改写前后完整 SQL 对**、未与 trace 关联 |
| LLM token 用量 | [llm_client.py:117-121,185-188,270-273,373-376](file:///home/star/project/feature/AI-DataHub/services/shared/common/llm/llm_client.py#L117) | 每次调用**已计算 `tokens{input,output,total}`** 并随 `done` 事件返回；**从未落库/聚合**，无成本 |
| 会话与消息 | 表 `adh_conversations`（MySQL），[chat.py](file:///home/star/project/feature/AI-DataHub/services/datamind/api/chat.py) CRUD | `messages` 为**整段 LONGTEXT JSON**（仅回放用）；无按条行、无 per-message id/trace 关联 |
| 工具/MCP 流事件 | [execution/stream_utils.py:83-159](file:///home/star/project/feature/AI-DataHub/services/datamind/execution/stream_utils.py) ToolTracker、[agent/agent_loop.py:53-188](file:///home/star/project/feature/AI-DataHub/services/datamind/agent/agent_loop.py) | 产出 `tool_start/tool_result`（含耗时、tool_calls 汇总）到 **SSE**；**不落库** |
| Agent 编排事件 | [nl2sql/orchestrator/agent_pipeline.py](file:///home/star/project/feature/AI-DataHub/services/datamind/nl2sql/orchestrator/agent_pipeline.py) 事件 `thinking/token/tool_use/done/error`、`final_sql`、`_exec_q→(df,exec_ms,exec_rows)` | 全流程产物在内存/流中；**无结构化持久** |
| 答案质检 | 表 `adh_quality_reviews`（datagov）+ [quality_service.py](file:///home/star/project/feature/AI-DataHub/services/datagov/services/quality_service.py) `update_feedback_by_question(user_id,question,satisfied)`、LLM-review；页 [QualityReview.tsx](file:///home/star/project/feature/AI-DataHub/frontend/src/pages/admin/QualityReview.tsx)（满意率/thumbs 统计） | 有一套"答案质量+满意"评审，但**按 (user,question) 文本匹配**，无 trace/message 主键，未与用量/trace 打通 |
| 系统监控 | 页 [Monitoring.tsx](file:///home/star/project/feature/AI-DataHub/frontend/src/pages/admin/Monitoring.tsx)、node-metrics router | **基础设施**监控（磁盘/节点），非 LLM 用量 |

### 1.2 明确的缺口（本设计要补）
1. **无统一 trace**：一次用户提问跨 {LLM 调用 / tool / MCP / 语义层SQL / nl2sqlSQL / 权限前后SQL / 执行输出 / token / 延迟 / 错误} 没有 `trace_id` 串联，产物只在 SSE 流给浏览器，**不落库、不可事后查看**。
2. **无用量聚合**：token 在 llm_client 产出即丢弃；**没有**用户维度/模型维度的用量与成本汇总，无 `成本`（缺 model 单价）。
3. **无跨用户 session 清单**：conversations 按 user 自己可见，管理端**看不到全站 session 列表 + 各自用量/状态**。
4. **工具/MCP 产物不可集中检索**：语义层SQL、nl2sqlSQL、权限前后SQL、执行输出**散落或只读不回看**。
5. **赞踩链路断裂**：Chat.tsx `POST /chat/feedback` 在 datamind **无对应路由**；存储零散于 `adh_quality_reviews` 与 `adh_search_feedback`（后者**全仓库无建表 DDL、无写入**，仅 aiplatform 读取）。需统一到 trace/message 主键并接通。
6. **Langfuse 零接入**：全仓库 `services/` 无任何 langfuse 代码（仅 wiki/memory 提及）→ 现状"trace"外部工具为**无**。

### 1.3 关键取舍：自建 DB 原生 trace（唯一事实源），Langfuse 暂不接入
- **以数据库为唯一事实源**（MySQL 存 trace 摘要/feedback，Doris 存 span 明细与用量 rollup）：离线可用、与现有管理端/鉴权/审计一致、无外部强依赖。
- **Langfuse：暂不接入（已定）**。仅保留 `OBSERVABILITY_LANGFUSE_ENABLED` 作为**远期可选镜像导出**的接口位，本期不排期、不实现（见 §7 O4、§10 项 3）。

---

## 2. 数据模型（新增；与现有审计并存、可回填）

### 2.0 存储分层原则（**已定决策**：MySQL=OLTP，Doris=OLAP，不整体迁移）
全平台只有 `METADATA_DB_*` 一套连接指向 **MySQL**（现为 `47.103.50.8:611/adh2`）；Doris 在本项目里是
"被治理的数据源 / 联邦网关"，**没有** metadata 层直连。经评估：**不把 METADATA_DB 迁 Doris**，按负载分库：

| 数据 | 归属 | 理由 |
|---|---|---|
| 用户/权限/RLS/数据源凭据/本体/模型配置/会话/反馈/trace 摘要 | **MySQL** | 典型 OLTP：每次 API 调用点查 + 小事务 + 多语句 `commit`/`ON DUPLICATE KEY`（10+ 文件在用）；Doris 无 ACID 多语句事务、无 FK、小 `UPDATE/DELETE` 与整段 blob 覆写代价高，且会把 FE 规划延迟压到全站每次请求 |
| LLM spans 明细、`usage_daily` rollup（append 海量 + 时间扫描 + GROUP BY 聚合） | **Doris** ✅ | OLAP 强项：列存、按 `dt` 分区、聚合看板秒开 |

> 结论落点：`adh_llm_traces` / `adh_message_feedback` → **MySQL**；`adh_llm_spans` / `adh_llm_usage_daily` → **Doris**。
> `adh_query_audit` 现于 MySQL，本次**不与其强绑**；若将来量大需分析，另立单独排期平滑迁 Doris。

### 2.1 `adh_llm_traces`（一次"用户回合"一条，**MySQL 元库**）
```
trace_id        VARCHAR(64) PK      -- uuid, 贯穿一次 chat turn / playground / agent 调用
conversation_id BIGINT  DEFAULT 0    -- 关联 adh_conversations(0=非会话入口)
message_uuid    VARCHAR(36)            -- 助手回合稳定标识(uuid4, 赞踩/trace 定位主键, 见 §2.6)
user_id BIGINT, username VARCHAR, user_role VARCHAR, workspace_id BIGINT
datasource_id   BIGINT  DEFAULT 0
entrypoint      ENUM('chat','playground','agent','scheduled','embed','report')
model_id BIGINT, model_ref VARCHAR          -- 实际使用的模型
question        TEXT                          -- 用户输入
final_answer    LONGTEXT                      -- 助手最终文本(可截断+指针)
status          ENUM('success','error','cancelled','running')
error_message   TEXT
started_at, finished_at, duration_ms BIGINT
input_tokens, output_tokens, total_tokens INT
credits         DECIMAL(14,6)                 -- 本回合各次模型请求 credit 之和(主口径, 见 §2.5)
cost_usd        DECIMAL(12,6)                 -- 辅助/估算(非 Qoder 直连路径才填)
span_count INT, tool_call_count INT
llm_call_count INT                            -- 回合内 LLM 调用次数(多步)
INDEX(conv), INDEX(user,started_at), INDEX(model,started_at), INDEX(ws,started_at)
```
### 2.2 `adh_llm_spans`（trace 内部步骤树，**Doris** 明细表，按 `dt` 分区；UNIQUE KEY(trace_id,span_id) 容乱序）
```
span_id VARCHAR(64), trace_id VARCHAR(64), parent_span_id VARCHAR(64) DEFAULT ''
kind  ENUM('llm_call','tool_call','mcp_call','semantic_gen','nl2sql_gen',
           'permission_rewrite','sql_exec','retrieval','post_process','agent_step')
name  VARCHAR, datasource_id BIGINT, tool_call_id VARCHAR
status ENUM('success','error'), start_ts, end_ts, duration_ms
input_tokens,output_tokens,total_tokens INT, model_ref VARCHAR
credits DECIMAL(14,6), original_credits DECIMAL(14,6), billable TINYINT, cost_usd DECIMAL(12,6)  -- 请求级 credit(从 Assistant 消息读, 见 §2.5/§3.2)
-- 产物(核心：非 chat 输出的中间产物都在这)：
input_text   LONGTEXT   -- 例: nl2sql 的 prompt 摘要 / 语义层声明式意图 JSON
output_text  LONGTEXT   -- 例: 生成的SQL / 改写后SQL / MCP 返回 JSON
sql_original LONGTEXT   -- 改写/执行前 SQL(semantic/nl2sql 原始产出)
sql_final    LONGTEXT   -- 权限改写后、实际执行的 SQL
sql_diff_meta JSON      -- {hidden_columns,masked_columns,rls_applied,policies,...}
exec_meta    JSON       -- {row_count,columns,execution_mode,pushdown,engine}
exec_preview JSON       -- 结果前 N 行预览(截断, 见 §6 PII)
error_text   TEXT
dt DATE (分区键)
```
### 2.3 `adh_llm_usage_daily`（用量 rollup，**Doris**，AGGREGATE/UNIQUE KEY(dt,dim_type,dim_id)，供看板秒开）
```
dt DATE, dim_type ENUM('user','model','workspace','datasource'), dim_id BIGINT,
trace_count, llm_call_count, input_tokens, output_tokens, cost,
avg_duration_ms, p95_duration_ms, error_count, thumbs_up, thumbs_down
PK(dt,dim_type,dim_id)
```
### 2.4 `adh_message_feedback`（统一赞踩，**MySQL**，事务 upsert，取代零散写入）
```
id PK, trace_id VARCHAR(64), conversation_id BIGINT, message_uuid VARCHAR(36),
user_id BIGINT, workspace_id BIGINT,
satisfied TINYINT,            -- 1赞/0踩
expected_table VARCHAR(128),  -- 复用 Chat 现有"期望表"
reason TEXT, tags JSON,       -- 新增：不满意原因/标签(自由+枚举)
created_at, updated_at, UNIQUE(message_uuid)   -- 一条助手消息一条反馈, 赞/踩可切换
```
### 2.5 计量口径：以 Qoder 原生 **credit** 为主（官方 docs.qoder.com/zh/cli/sdk/cost-usage）
Qoder Agent SDK 直接透出 Credits（Qoder 资源用量单位），**无需自建单价**。三档口径与数据源：

| 口径 | 数据源 | 采集落点 |
|---|---|---|
| **单次模型请求**（原子计量） | **Assistant** 消息 `usage.credits / original_credits / billable` | 每个 `llm_call` span；⚠️ 一次回合可能多条（多次请求） |
| **回合合计** | 本轮各 Assistant `usage.credits` 求和 | `adh_llm_traces.credits` |
| **会话累计** | **Result** 消息 `total_credits`、`model_usage[model].credits` | conversation 级 rollup + 对账；⚠️ **会话累计值，禁止与多条 Result 相加** |
| **账号额度**（配额预警） | `client.get_usage_info()`：`userQuota.remaining / totalUsagePercentage / isQuotaExceeded` | 配额看板/告警，**不计入单 trace** |

**红线（官方兼容性说明）**：
1. 请求级 `credits` 从 **Assistant 消息**读，**不要从 Result 的 `usage` 字段读**；
2. `total_credits` 是会话累计，不要当作"每回合"、不要重复累加；请求级与会话累计是同一批消耗的不同范围，**二者不相加**；
3. credit 字段**可选**（旧 CLI/未登录可能缺）→ 读前判存在，**缺失≠0**（记 `credits=NULL` 并标计费口径不可用）；
4. 账号剩余额度以 `get_usage_info()` 为准，不要靠累加会话消息推算。

**兜底（仅非 Qoder 路径）**：内置 anthropic 直连的 `llm_client`（NL2SQL 生成/playground 语义）只有 tokens、
无 credit → `adh_llm_models` 增列 `input_price_per_1k/output_price_per_1k/currency`（MVP 手工填、自托管可留 0/NULL），
`cost_usd = in/1000*in_price + out/1000*out_price` 作为**估算**；credit 列对这类路径留 NULL。

### 2.6 消息稳定标识 `message_uuid`（**已定引入**）
现状 `adh_conversations.messages` 是整段 JSON blob，赞踩按数组下标定位不稳（改标题/回放重排即错位）。方案：
- 每条**助手回合**在生成 trace 时分配 `message_uuid`(uuid4)：写入该回合的消息 JSON 元素 + `adh_llm_traces.message_uuid` + `adh_message_feedback.message_uuid`，作为赞踩/反馈/trace 的**关联主键**。
- 用户消息不强制 uuid（仅助手回合需被评价/回溯）；下标 `message_id` 仅作展示序号，不作外键。
- 后端 `done` 事件回传 `message_uuid`，前端随消息保存；反馈提交时携带。
- 兼容：历史 blob 无 uuid 的行，回填时按 `(conversation_id, 顺序)` 生成一次性 uuid。若后续拆为 `adh_messages` 行表（可选重构），`message_uuid` 直接延续为主键，trace/feedback 无需再改。

---

## 3. 埋点架构：trace_id 贯穿 + TraceRecorder

### 3.1 上下文传播
- 新增包 `services/shared/observability/`：
  - `context.py`：`ContextVar` 承载 `trace_id / conversation_id / user_id / workspace_id / entrypoint`；
    FastAPI 中间件在入口生成 `trace_id`（uuid），响应头回 `X-Trace-Id`；服务间内部调用透传该头。
  - `recorder.py`：`TraceRecorder` 攒当前 trace 的 span，**异步批量 flush**（不阻塞对话），
    写 §2 表；提供 `span(kind,name)` 上下文管理器（自动计时/捕获异常/取 token usage）。
- **入口即开 trace**：`chat.py /send/stream`、`/send`、`playground.py /execute`、`agent.py /dispatch`、
  `execution /execute`、scheduled 任务，进入时 `recorder.begin_trace(...)`，`done` 时 `finalize()`。

### 3.2 各产物采集点（对应"非 chat 输出的 SQL/执行产物"）
| 产物 | 采集位置 | span.kind | 记录字段 |
|---|---|---|---|
| **LLM 调用 credit/token/延迟** | ① **Qoder 路径**：[QoderSDKAdapter._map_messages](file:///home/star/project/feature/AI-DataHub/services/datamind/execution/adapters/qoder_sdk_adapter.py#L381) —— **当前只取 duration/num_turns/session/subtype，丢了 credit**；改为读 **Assistant `message.usage.{credits,original_credits,billable}`**（请求级 span）+ **Result `total_credits`/`model_usage`**（会话累计, 见 §2.5）。② 直连路径 [llm_client.py](file:///home/star/project/feature/AI-DataHub/services/shared/common/llm/llm_client.py) 4 处 `tokens` → span | `llm_call` | credits(或tokens+估算 cost),model,latency,prompt 摘要,billable |
| **nl2sql 产出 SQL** | pipeline `final_sql` 收敛处（[agent_pipeline.py:2374/2384/2497](file:///home/star/project/feature/AI-DataHub/services/datamind/nl2sql/orchestrator/agent_pipeline.py)）/ llm generate 出口 | `nl2sql_gen` | sql_original,thinking 摘要 |
| **语义层生成 SQL** | `run_semantic_query` 工具与 `shared/common/semantic_client.py` | `semantic_gen` | input_text=声明式意图, sql_original |
| **权限改写前后 SQL** | [enforcer.enforce_sql](file:///home/star/project/feature/AI-DataHub/services/datamind/permission/enforcer.py) 返回处 | `permission_rewrite` | sql_original=改写前, sql_final=改写后, sql_diff_meta(策略/隐藏/脱敏/RLS) |
| **SQL 执行输出** | [query_executor.execute_query_with_permission](file:///home/star/project/feature/AI-DataHub/services/datamind/nl2sql/sql/query_executor.py) / engine_client 返回 (df,rows,ms) | `sql_exec` | sql_final,exec_meta(row_count/列/execution_mode/pushdown),exec_preview |
| **工具 / MCP 执行** | [stream_utils ToolTracker.on_tool_use/on_tool_result](file:///home/star/project/feature/AI-DataHub/services/datamind/execution/stream_utils.py)、[agent_loop.tool_calls_log](file:///home/star/project/feature/AI-DataHub/services/datamind/agent/agent_loop.py) | `tool_call`/`mcp_call` | tool_call_id,name,input/output,耗时,错误 |
| **子 Agent/waker 步骤** | agent_loop 每轮迭代 | `agent_step` | 轮次、决策摘要 |

> 原则：**不改变现有 SSE 事件语义**（前端交互不受影响）；只是在同一批数据旁路写 trace。产物文本做长度上限（如 32KB 截断 + `truncated` 标记），大结果集只存预览。

---

## 4. 后端 API（新 `observability` 路由，datamind 承载，admin 鉴权）
挂载：`app.include_router(observability_router, prefix="/api/observability", tags=["LLM 可观测"])`
- `GET /observability/sessions`：跨用户 **session 清单**（按 conversation 聚合：标题、用户、轮数、
  token、**credit**（会话累计 `total_credits`）、错误数、满意率、最近活跃）；过滤 user/ws/model/时间/数据源；分页。
- `GET /observability/sessions/{conversation_id}`：该会话下 trace 列表。
- `GET /observability/traces`：trace 查询/分页/过滤（trace_id、user、model、status、时间、entrypoint、仅不满意）。
- `GET /observability/traces/{trace_id}`：**trace 详情** = trace 摘要 + **完整 span 树**（含各产物 SQL
  原文/改写后、diff 元数据、执行预览、每步 token/延迟/成本、tool/mcp 输入输出）。
- `GET /observability/usage/users`：**用户用量** rollup（调用数/token/**credit**/延迟/错误/满意）。
- `GET /observability/usage/models`：**模型用量** rollup（按 model 维度 credit/tokens + 趋势，源自 `model_usage[*].credits`）。
- `POST /chat/feedback`（补到 datamind chat 路由，接通现有 Chat.tsx）：写 `adh_message_feedback`，
  携 `message_uuid`(或 `trace_id`)/`satisfied`/`expected_table`/`reason`/`tags`；幂等 upsert。
- `GET /observability/feedback`：反馈清单（联动现有 QualityReview 页；可调用 datagov LLM-review 复核）。
> 鉴权：`role=admin` 看全站；其余仅本人 sessions（`user_id` 从 JWT 注入，body 里的 user 不作数）。
> 与历史 `adh_query_audit`：保留，`/observability/traces` 为增强视图；可把旧 audit 行按 question/user 映射成"轻量 trace"回填一次以便平滑。

---

## 5. 前端（管理端 + Chat）

### 5.1 管理端新增「LLM 可观测」菜单组（`pages/admin/observability/`）
1. **Session 清单**：表格（标题/用户/轮数/模型/token/成本/错误/满意率/时间）→ 点进 trace 列表。
2. **Trace 详情（核心页）**：左侧 **span 瀑布/树**（按 kind 图标，节点显示耗时/token/状态），
   右侧选中 span 的产物：**SQL 改写前后 diff（左右对照 + 高亮）**、语义层意图、执行输出预览表、
   tool/mcp 输入输出 JSON、错误堆栈。顶部 trace 摘要（用户/模型/总 token/成本/端到端延迟/命中策略）。
3. **用量看板**：用户用量 TopN、模型用量与成本趋势、延迟 P95、错误率、满意率（读 §2.3 rollup）。
4. **反馈(赞踩)清单**：不满意优先，展示 question/answer 摘要 + reason/tags + 跳转对应 trace；
   复用/并入现有 QualityReview 的 LLM 复核。
- 新增 `frontend/src/api/observability.ts` 封装上述端点；沿用 `client`。

### 5.2 Chat 侧
- 赞踩按钮**已存在**（[Chat.tsx:193-215](file:///home/star/project/feature/AI-DataHub/frontend/src/pages/Chat.tsx)）→ 修正调用契约：`/chat/feedback` 带
  `message_uuid`（`done` 事件回传 `message_uuid`+`trace_id`，前端随消息保存，见 §2.6），赞/踩后弹**原因**（多选标签+可选文本）。
- 助手气泡角落显示 `trace_id`（简写，点开可复制），便于反馈与管理员定位。

---

## 6. 安全 / PII / 留存 / 性能
- **最小暴露**：`question/answer/prompt/sql 产物` 可能含敏感数据 → trace 详情仅 admin/本人可见；
  执行预览**截断**（默认前 50 行 + 单元格截断）；敏感列（命中 `adh_sensitive_fields`）在预览/`exec_preview`
  中**脱敏**；`error_text` 去堆栈绝对路径。
- **绝不入库**：api_key、JWT、明文凭据（含 `remote_props`）；`adh_llm_models.api_key` 展示恒掩码。
- **留存**：`adh_llm_spans`/`usage_daily` 在 Doris 按 `dt` 分区，明细默认留 90 天；trace 摘要与 feedback 长期保留；
  提供 TTL/归档任务（可挂 scheduled task）。
- **性能**：span **异步批量 flush**，与对话主链路解耦；用量看板走 rollup 表不扫明细。

## 7. 分期实施（里程碑）
- **O0 地基**：`services/shared/observability/`（contextvar+middleware+TraceRecorder）+ §2 建表 +
  `llm_client`(直连 token) 与 [QoderSDKAdapter._map_messages](file:///home/star/project/feature/AI-DataHub/services/datamind/execution/adapters/qoder_sdk_adapter.py#L414)（**补读 Assistant `usage.credits` + Result `total_credits/model_usage`**）挂 `llm_call` span → **先让 credit/用量有数据**（无 UI）。
- **O1 全产物 trace**：埋 semantic/nl2sql/permission(sql前后)/exec/tool/mcp/agent_step span +
  `done` 落 trace 摘要与 totals；`X-Trace-Id` 贯通。
- **O2 管理端**：`/api/observability/*`（sessions/traces/trace 详情/usage）+ 前端三页（清单/详情瀑布+SQL diff/用量看板）。
- **O3 反馈闭环**：修 `/chat/feedback` + `adh_message_feedback` + Chat 原因弹层 + 满意率 rollup +
  与 QualityReview/LLM 复核打通。
- **O4 增强（远期可选，本期不排期）**：告警（错误率/延迟/credit 阈值）、credit 配额预警（读 `get_usage_info`）、
  按本体/数据源的调用热点分析。**Langfuse 导出暂不做**（仅保留接口位）。

## 8. 兼容与回滚
- 纯新增表 + 旁路埋点；不改 SSE 事件、不改 `adh_query_audit`/`adh_rls_audit_logs` 现有写入；
  旧 history/quality 页继续可用，新页为增强视图。
- `OBSERVABILITY_ENABLED=false` 时 recorder 全为 no-op（零开销），可灰度与快速回滚。
- feedback 迁移：把 `adh_quality_reviews.satisfied` / 前端旧 `adh_search_feedback` 读路径收敛到
  `adh_message_feedback`（旧表只读保留过渡）。

## 9. 验收 / 测试
- 单测（离线）：contextvar 传播、span 计时/异常捕获、cost 计算、`/chat/feedback` upsert 幂等、
  trace 详情聚合 span 树、rollup 累加正确、敏感列预览脱敏。
- 契约：`done` 事件含 `trace_id` 与 `message_uuid`；`/observability/traces/{id}` 能同时取到 semantic/nl2sql/permission前后/exec 各 span。
- 端到端：一次带工具+权限改写的 chat → 管理端 trace 里可复原"意图→nl2sql/语义SQL→改写前→改写后→执行输出→
  token/credit/延迟"全链；点赞/点踩落 `adh_message_feedback`（按 `message_uuid`）并计入满意率与模型用量看板。
- 权限：非 admin 不能看他人 session/trace（横向越权用例）。

## 10. 待确认（实现前定）
1. ~~明细落 Doris 还是统一 MySQL 元库？~~ **已定（见 §2.0）**：MySQL=OLTP（trace 摘要、feedback、配置、会话），
   Doris=OLAP（`adh_llm_spans` 明细 + `adh_llm_usage_daily` rollup，按 `dt` 分区）；**METADATA_DB 不迁 Doris**。
   附带：顺手纠正 `query_executor.py` 中 "adh_query_audit in Doris" 的误导注释（实为写元库 MySQL）。
2. ~~成本单价来源~~ **已定（见 §2.5）**：主力 Qoder 路径**直采 SDK 原生 credit**（请求级 `Assistant.usage.credits`，会话级 `Result.total_credits`），无需单价表；单价表仅用于非 Qoder 直连路径的兜底估算（MVP 手工填 `*_price_per_1k`，自托管可留 NULL）。
3. ~~是否需要 Langfuse~~ **已定：暂不上**（见 §1.3）。DB 原生 trace 为唯一事实源；仅保留导出接口位，不排期。
4. ~~是否引入稳定 `message_uuid`~~ **已定：引入**（见 §2.6）。助手回合用 `message_uuid`(uuid4) 作为赞踩/trace 关联主键，取代 JSON blob 数组下标。
