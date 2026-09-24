---
trigger: always_on
description: AS-BOT 默认只使用系统本体与运营数据；用户确认的仪表盘设计任务可经专用工具只读查询业务知识与语义，严禁用业务知识回答系统问题或写业务本体。
---

# AS-BOT 定位：系统 Waker，业务设计按需授权 — 强制规范

> AS-BOT（`waker_key='__system_bot__'`，见 `services/datamind/execution/wakers.py`）是**本系统的运维助手**，
> 服务对象是平台本身（元数据、建模进度、审批流水、可观测用量），不是任何业务数据域。
> 默认语义消费落在**系统本体模型**上。例外仅为用户确认的仪表盘设计任务：只读业务知识与语义，不写业务本体。

## 1. 本体分域（硬边界）
- **系统本体模型**：`adh_ontology_models` 中 `datasource_id IS NULL OR datasource_id = 0` 的模型（描述本系统元数据表：adh_datasources / adh_ontology_models / adh_as_bot_approvals / adh_llm_traces 等）。AS-BOT 的默认检索、生成、save、activate **只允许**作用于这一批模型。
- **业务本体模型**：`datasource_id > 0` 的模型（面向业务数据源）。AS-BOT **严禁**用其回答系统运营问题，严禁对其做生成/激活等写操作。
- 判别口径以 `system_tools.system_overview` 的 `ontology_models_system` / `ontology_models_business` 统计条件为准，任何新代码复用同一口径，不得另造定义。

## 2. 工具与数据通道约束
- AS-BOT 工具组固定为 `catalog + semantic + ontology + screen + system`（`resolve_system_bot_waker` 强制覆盖），**禁止 query 组（nl2sql 数据通道）**；不得为其放开。
  —— 这是 **AS-BOT 系统域边界**（系统 Waker 不取业务数据行），**不是**说 nl2sql 不受治理：`execute_sql` 经 DataFusion 是受治理路径（见 `security-guardrails.md` §6），只是不该进入系统 Waker 的职责。
- 系统运营类问题（用量/健康/待办/建模进度）**必须**走 `system` 工具组（`system_usage` / `system_overview`，`services/datamind/execution/sdk_tools/system_tools.py`），**严禁**用 `knowledge_search` 拿业务本体知识充数——那是降级掩盖（见 `no-silent-degradation.md`）。
- 涉及系统本体的 `run_semantic_query` / 检索请求必须显式落在系统本体模型域内；实现上通过系统数据源（元库）绑定解析，不得默认取当前会话的业务数据源上下文。

## 3. 仪表盘设计例外
- AS-BOT 判断是否需要业务上下文，先提出设计并由用户确认业务范围；仅 `search_business_knowledge/get_business_semantics` 可消费该设计授权域，旧系统工具不切域。
- 业务范围、选择、草稿和发布状态存 MySQL；每次校验身份、会话、工作空间、数据源和知识库权限。无绑定不查全库，有歧义列选项询问。
- SQL 仅由服务端生成，在鉴权设计面板供人查看/编辑并重新治理预览，不进入 Agent 工具返回。正式发布必须经 `dashboard.publish` 审批，修改使旧预览与审批失效。

## 4. 改动纪律
- 为 AS-BOT 新增能力（含自进化动作）时，先问：**这个动作的对象是系统本体/系统元数据，还是业务本体？** 业务侧建模动作属于用户在业务工作空间的操作，不塞进 AS-BOT。
- 写操作一律走 AS-BOT Action 审批通道（见 `fde-evolution.md` §2），且 `ontology.save/activate/generate` 的 payload 校验**必须实现**系统本体域约束（目标模型的 datasource_id 缺省/0 才可执行），防止经 AS-BOT 旁路写业务本体；若现有 schema 尚未覆盖此校验，视为待改造缺陷而非可接受现状。
- 新增"系统问题→数据源路由"逻辑（prompt、planner、工具描述）时，同步在评测中加用例断言：系统运营问题不得命中业务本体（unbound/正确来源断言），否则视为定位漂移。
