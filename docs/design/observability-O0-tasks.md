# O0 可观测地基 — 可直接开工任务清单

> 目标（唯一）：**先让 credit/用量结构化落库、可按 trace 回看**（无管理端 UI、无全产物 span，那些是 O1+）。
> 依赖设计：[llm-observability.md](./llm-observability.md) §2.0/§2.1/§2.2/§2.4/§2.5/§2.6、§3.1、§7-O0。
> O0 只落 3 类：`adh_llm_traces`（摘要+credit）、`adh_llm_spans`（仅 `llm_call` 一种 kind）、
> `adh_message_feedback`（可先建表，写入口留到 O3）；`adh_llm_usage_daily` 可后置。
> 开关 `OBSERVABILITY_ENABLED=false` 时全链路 no-op（零开销、可回滚）。

## 0. 约定与红线（贯穿所有任务）
- `trace_id`=uuid4（一次用户回合）；`message_uuid`=uuid4（一条**助手回合**，赞踩/关联主键，见 §2.6）。
- credit 采集遵循官方红线：请求级从 **Assistant `msg.usage`**、会话级从 **Result `total_credits/model_usage`**；**缺失≠0**（记 NULL）。
- 任何 LLM 产物文本入库前截断（prompt 摘要 ≤4KB）；**绝不记** api_key/JWT/明文凭据。
- 埋点全部 try/except 兜底：观测失败**绝不影响**主对话（对齐现有 `_write_audit` 吞异常风格）。

---

## 1. 配置与开关
- [ ] **O0-T1** `services/shared/common/config.py`：新增
      `OBSERVABILITY_ENABLED`(默认 false)、`OBSERVABILITY_FLUSH_INTERVAL_S`、`OBSERVABILITY_SPAN_MAX_CHARS`(默认 4096)、
      `OBSERVABILITY_SPAN_SINK`(mysql|doris，默认 mysql)。复用既有 `DORIS_HOST/PORT/USER/PASSWORD`(config.py:131-134)。
      验收：env 覆盖生效；关时 recorder 为 no-op。

## 2. Doris 连接通道（spans 落 Doris 的前提）
- [ ] **O0-T2** 新增 `services/shared/common/db/doris_db.py`：`get_doris_conn()` / `get_doris_connection()`，
      用 `DorisMetadataDB`（metadata_db.py:166）指向 `DORIS_*`，**独立单例**（不复用 `_get_db()` 的 METADATA_DB_TYPE 全局，避免与元库串）。
      若 `OBSERVABILITY_SPAN_SINK=mysql` 或 Doris 不可用 → sink 回落 MySQL（见 O0-T5），O0 不阻塞。
      验收：能连 Doris 建/查一张表；连不上时抛可捕获异常且不崩调用方。

## 3. 建表迁移（手工跑，风格对齐现有 `docker/mysql/*_migration.sql`）
- [ ] **O0-T3** 新增 `docker/mysql/observability_migration.sql`（MySQL）：`adh_llm_traces`、`adh_message_feedback`
      （字段照 §2.1/§2.4，`message_uuid VARCHAR(36)` 唯一）。全部 `IF NOT EXISTS`、加索引。
- [ ] **O0-T4** 新增 Doris DDL（追加到 `docker/doris/init.sql` 或新建 `docker/doris/observability_migration.sql`）：
      `adh_llm_spans`（§2.2，`UNIQUE KEY(trace_id, span_id)`、`DISTRIBUTED BY HASH(trace_id)`、按 `dt` 分区、
      `credits/original_credits/billable/cost_usd` 列）。sink=mysql 时同结构 MySQL 表随 O0-T3 建。
      验收：两库各表可 DESC；重复执行不报错。

## 4. observability 包骨架
- [ ] **O0-T5** 新增目录 `services/shared/observability/`：
      - `context.py`：`ContextVar` 存 `trace_id/conversation_id/message_uuid/user_id/username/user_role/workspace_id/datasource_id/entrypoint/model_ref`；
        `begin_trace()/current_trace()/token` 工具。
      - `models.py`：`Span` dataclass（kind/name/start/end/duration/status/tokens/credits/original_credits/billable/cost_usd/model_ref/input_text/output_text/error）。
      - `sinks.py`：`SpanSink` 抽象 + `MySQLSpanSink`/`DorisSpanSink`（按 O0-T1 选；失败回落 MySQL）。
      - `recorder.py`：`TraceRecorder`
        · `span(kind,name)` 异步上下文管理器（自动计时、捕获异常→status=error、从 result 取 tokens/credits）
        · 内存缓冲当前 trace 的 spans；`finalize_trace()` 汇总 totals 并**异步 flush**（后台线程/队列，绝不阻塞响应）
        · `enabled=false` → 全方法 no-op、`span()` 直通。
      验收：单测——开/关两态；flush 不阻塞；span 异常被吞并记 status=error；credit 缺失记 NULL 不当 0。

## 5. 入口中间件与生命周期
- [ ] **O0-T6** `services/datamind/main.py`：注册 FastAPI 中间件：请求入口 `begin_trace()` 生成 `trace_id`，
      响应头回 `X-Trace-Id`；从 JWT 注入 `user_id/username/role/workspace_id`（复用 `shared/common/auth.py` 解码）。
- [ ] **O0-T7** 各入口在 `done`/异常时 `finalize_trace()`：`api/chat.py`(`/send`,`/send/stream`)、
      `api/playground.py`、`api/agent.py`、`api/execution.py`、scheduled。**O0 至少打通 `/send/stream`**（主链路），其余可并行补。
      验收：一次 chat 请求结束后 `adh_llm_traces` 有 1 行、`adh_llm_spans` 有 N 行 `llm_call`。

## 6. Qoder 路径埋点（主力，credit 直采）
- [ ] **O0-T8** `services/datamind/execution/adapters/qoder_sdk_adapter.py::_map_messages`：
      - **AssistantMessage 分支(L397)**：读 `getattr(msg,"usage",None)` → 每个 `llm_call` span 记
        `credits/original_credits/billable`（存在才记，缺失 NULL）；一并取 input/output tokens。
      - **ResultMessage 分支(L414-422)**：`meta` 增补 `total_credits`、`model_usage`（会话累计，写 trace 级；**不与请求级相加**、不与多条 Result 相加）。
      复用 `tracker`/`meta` 现有通路，不改 SSE 事件结构。
      验收：真跑一条 agent 问答 → span 内 credits 有值、trace.total_credits=会话累计、二者不被错误累加。

## 7. 直连路径埋点（内置 anthropic llm_client，token→估算 cost）
- [ ] **O0-T9** `services/shared/common/llm_client.py` 4 处 usage 出口各包一个 `llm_call` span：
      `generate_sql`(L118-125)、`generate_sql_stream`(L185-191 done)、`generate_with_tools`(L270-277)、
      `generate_with_tools_stream`(L373-380 done)。记 tokens + `cost_usd`（见 O0-T10 单价），`credits=NULL`。
      验收：NL2SQL 生成一次 → 对应 trace 下有 1 条 `llm_call` span（tokens 有值、cost 估算或 NULL）。

## 8. 模型单价（仅直连路径兜底）
- [ ] **O0-T10** `adh_llm_models` 迁移加列 `input_price_per_1k/output_price_per_1k/currency`（可空）；
      `aiplatform` 模型配置读写（`model_config_service.py`）带出/写入；展示处 api_key 恒掩码。
      `cost_usd = in/1000*in_price + out/1000*out_price`，任一为空则 cost_usd=NULL（不臆造）。
      验收：填价后直连 span.cost_usd 正确；自托管留空 → NULL。

## 9. trace 终结与 message_uuid 回传
- [ ] **O0-T11** `finalize_trace()` 汇总写 `adh_llm_traces`：tokens/`credits`(本轮各次求和)/cost_usd/
      duration_ms/span_count/llm_call_count/status/error；分配并回传 `message_uuid`。
- [ ] **O0-T12** `done` 事件（stream_utils / chat 出口）新增 `trace_id`、`message_uuid` 字段（**只增不改**，前端旧字段不受影响）；
      写入 `adh_conversations.messages` 对应助手回合元素的 `message_uuid`（供 O3 赞踩定位，见 §2.6 兼容回填）。
      验收：curl `/send/stream` 末帧 done 含 trace_id+message_uuid；历史回放不破。

## 10. 冒烟 & 回归（离线为主）
- [ ] **O0-V1** 单测（不碰真库，mock sink）：contextvar 传播、span 计时/异常、credit 缺失→NULL、
      cost 估算、finalize 汇总、开关 off 零写入、Doris 不可用回落 MySQL。
- [ ] **O0-V2** 端到端冒烟（本地 Doris/MySQL 可用时）：开 `OBSERVABILITY_ENABLED=true` → 跑一条真实 agent 问答 →
      校验 traces/spans 落库、credit 口径正确、`X-Trace-Id` 响应头、done 携 trace_id/message_uuid。
- [ ] **O0-V3** 回归：关开关跑既有 datamind 用例，确认对主链路零影响。

---
## 完成定义（O0 DoD）
1) 开关开：一次 chat/agent 回合 → `adh_llm_traces` 1 行（含 credit/tokens/latency/status）+ 相应 `llm_call` span；
2) Qoder credit 直采正确（请求级 vs 会话累计不混加、缺失为 NULL）；直连路径有 tokens/估算 cost；
3) `X-Trace-Id` 响应头 + `done` 携 `trace_id/message_uuid`；
4) 开关关：全链路 no-op、既有测试全绿；
5) 无 UI 依赖即可用 SQL 查出用量（为 O2 看板与 O1 全产物打地基）。

## 依赖顺序（建议）
T1 → (T2, T3) → T5 → T6 → T7 → 并行{T8, T9, T10} → T11 → T12 → V*。
> 可先只交付 **T1–T7 + T8（仅 Qoder 主链路）+ T11/T12** 即拿到"credit 落库+trace 可回看"；T9/T10（直连兜底）随后。
