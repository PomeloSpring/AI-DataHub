---
trigger: always_on
description: 数据源可用性唯一由"工作空间∩Waker∩用户角色"裁决；§7 黑盒禁止向 LLM 暴露/要求 datasource_id，选源用业务名；元数据工具按 Waker 能力（是否授权 execute_sql）分域返回物理元数据或本体业务投影。
---

# Waker 数据源与元数据分域（防 datasource_id 猜值 / 物理元数据错配）— 强制规范

> 本类问题反复出现：工具 schema 暴露 `datasource_id` 诱导 LLM 猜值被资源护栏拒；catalog 元数据工具对 nl2sql Waker 只回本体投影、拿不到写 SQL 所需的物理列。以下为本类问题的收敛规则，新增/改动工具与资源守卫前必读。

## 1. 数据源可用性唯一裁决点（三方交集）
- 一个数据源能否被使用，**唯一**由 **工作空间 ∩ Waker ∩ 用户角色** 决定，即 `services/datamind/execution/resource_guard.py: bind_resources` 返回的 `available_sources`（空 Waker 继承工作空间绑定；非空只收窄；普通用户再与角色数据源权限取交集，**空权限不得解释为全量**）。不得另造第二套"谁能查哪个源"的判断。
- 会话生效源 `ctx.datasource_id` 必须 ∈ `available_sources`：唯一候选自动选定、多候选要求显式选择、已选源被撤回则报错不静默切换。

## 2. §7 黑盒：不得要求 LLM 传它看不到的内部 id
- `datasource_id` 属 `security-guardrails.md` §7 禁止外泄项。任何工具 **不得** 在入参 schema 里要求 LLM 提供 `datasource_id`——LLM 拿不到真实值只能猜，必被 `_check_source` 拒并空耗多轮工具调用。
- LLM 需要选源时 **用业务名**：`list_datasources` 返回授权集内候选 `{name, db_type}`（不含 id）；查询工具（`check_sql`/`execute_sql`）用可选 `datasource`(名) 参数；服务端在 `available_sources` 内按 name 解析为 id（数据源 `name` 全局唯一 `uk_datasource_name`，无歧义）。未命中抛含候选名的可操作错误 → 引导 `ask_user`，**禁止猜名/猜 id**。
- 反模式（发现即修）：工具入参 schema 出现 `datasource_id`/内部 `id` 且期望 LLM 填；工具返回体含 `datasource_id`/host/账号/IP/连接串。

## 3. 元数据工具按 Waker 能力分域
- `search_metadata` / `get_table_schema` 等元数据/目录工具的返回 **按消费方 Waker 分域**（路由见 `services/datamind/execution/sdk_tools/compat.py`）：
  - **可写 SQL 的 Waker**（授权了 `execute_sql`，如 `nl2sql_expert`/数据分析师）：走原始 catalog handler，返回真实表/列元数据（`adh_table_info`/`adh_column_metadata` 的表名/列名/类型/注释/business_desc），输出前经 `catalog_tools._strip_physical_ids` 剥除 `datasource_id`/内部 id。
  - **语义层 Waker**（无 `execute_sql`，如 `data_analyst`/ChatBI分析师）：走 `scoped_metadata` 本体业务投影，只回已授权业务对象属性，不给物理结构。
- 判定口径统一用 `compat._waker_can_write_sql(runtime)`（读 `policy.allowed` 是否含 `__execute_sql`），不得另写一套。
- 同一工具对不同 Waker 返回不同视图是 **有意设计**，不得"图省事统一成一种"；新增元数据/目录类工具时必须先声明面向哪类 Waker、返回物理元数据还是业务投影。

## 4. 改动纪律与回归
- 触碰 `resource_guard.py` / `sdk_tools/compat.py` / `scoped_metadata.py` / `catalog_tools.py` / `query_tools.py` 的数据源作用域或元数据返回逻辑后，必须跑 `tests/test_waker_security.py`（含 `test_sql_capable_waker_metadata_tools_bypass_projection`、`test_tool_selects_source_by_authorized_name`、`test_tool_rejects_unknown_datasource_name`、`test_catalog_strip_physical_ids_keeps_modeling_columns`）+ 护城河三件套，并为新分支补用例。
- 这些是 datamind(8001) 无 `--reload` 常驻代码，改后须重启方生效；Waker 的 `tools`/`system_prompt` 存 `adh_wakers`，按 `waker_key` 幂等更新且注意 DB 常比迁移文件新（只应用目标那条 UPDATE，勿整体重跑）。
- 与 `security-guardrails.md` §7、`as-bot-system-waker.md` 域边界保持一致；冲突时以**更严**的脱敏/域约束为准。
