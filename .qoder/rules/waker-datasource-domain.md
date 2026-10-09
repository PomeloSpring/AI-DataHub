---
trigger: always_on
description: 数据源可用性唯一由"用户角色授权"裁决（工作空间不再绑定数据源）；§7 黑盒禁止向 LLM 暴露/要求 datasource_id，选源用业务名；元数据工具按 AS-BOT 工具授权（是否授权 execute_sql / system 组）分域返回物理元数据或本体业务投影。
---

# AS-BOT 数据源与元数据分域（防 datasource_id 猜值 / 物理元数据错配）— 强制规范

> 本类问题反复出现：工具 schema 暴露 `datasource_id` 诱导 LLM 猜值被资源护栏拒；catalog 元数据工具对 nl2sql AS-BOT 只回本体投影、拿不到写 SQL 所需的物理列。以下为本类问题的收敛规则，新增/改动工具与资源守卫前必读。

## 1. 数据源可用性唯一裁决点（角色唯一裁决）
- 一个数据源能否被使用，**唯一**由 **用户角色授权** 决定（`adh_user_roles ⋈ adh_role_datasource_access`，经 `role_service.get_user_allowed_datasources(user_id, workspace_id)`），即 `backend/modules/mind/execution/resource_guard.py: bind_resources` 返回的 `available_sources`。**工作空间不再绑定数据源**：`adh_workspace_datasources` 已退役为冻结表（保留结构与删除工作空间时的级联清理，任何读路径不得消费）；AS-BOT 也不持有数据源边界。**空角色授权不得解释为全量**（fail-closed），admin 同样纯角色裁决、不 bypass。不得另造第二套"谁能查哪个源"的判断。
- 会话生效源 `ctx.datasource_id` 必须 ∈ `available_sources`：唯一候选自动选定、多候选要求显式选择、已选源被撤回则报错不静默切换。

## 2. §7 黑盒：不得要求 LLM 传它看不到的内部 id
- `datasource_id` 属 `security-guardrails.md` §7 禁止外泄项。任何工具 **不得** 在入参 schema 里要求 LLM 提供 `datasource_id`——LLM 拿不到真实值只能猜，必被 `_check_source` 拒并空耗多轮工具调用。
- LLM 需要选源时 **用业务名**：`list_datasources` 返回授权集内候选 `{name, db_type}`（不含 id）；查询工具（`check_sql`/`execute_sql`）用可选 `datasource`(名) 参数；服务端在 `available_sources` 内按 name 解析为 id（数据源 `name` 全局唯一 `uk_datasource_name`，无歧义）。未命中抛含候选名的可操作错误 → 引导 `ask_user`，**禁止猜名/猜 id**。
- 反模式（发现即修）：工具入参 schema 出现 `datasource_id`/内部 `id` 且期望 LLM 填；工具返回体含 `datasource_id`/host/账号/IP/连接串。

## 3. 元数据工具按 AS-BOT 工具授权分域
- `search_metadata` / `get_table_schema` 等元数据/目录工具的返回 **按工具授权分域**（路由见 `backend/modules/mind/execution/sdk_tools/compat.py`）：
  - **可写 SQL 的 AS-BOT**（策略授权了 `execute_sql`，如 `nl2sql_expert`/数据分析师）：走原始 catalog handler，返回真实表/列元数据（`adh_table_info`/`adh_column_metadata` 的表名/列名/类型/注释/business_desc），输出前经 `catalog_tools._strip_physical_ids` 剥除 `datasource_id`/内部 id。
  - **语义层 AS-BOT**（无 `execute_sql`，如 `data_analyst`/ChatBI分析师）：走 `scoped_metadata` 本体业务投影，只回已授权业务对象属性，不给物理结构。
- 判定口径统一读**工具授权**（policy），不得另写一套：物理/投影分域用 `compat._as_bot_can_write_sql(runtime)`（读 `policy.allowed` 是否含 `__execute_sql`）；系统/业务可见域用 `tool_policy.is_system_scope(policy)`（策略是否授权 `system` 工具组）——**能力叠加**（域规则更新：系统域只是权限的一部分）：授权则在业务双轨（业务+本源源）之上**叠加** `kind='system'` 系统本体，不屏蔽业务；未授权仅业务双轨（旧 `as_bot_key='__system_bot__'` 哨兵判定已退役）。
- 同一工具对不同 AS-BOT 返回不同视图是 **有意设计**，不得"图省事统一成一种"；新增元数据/目录类工具时必须先声明面向哪类 AS-BOT、返回物理元数据还是业务投影。

## 4. 改动纪律与回归
- 触碰 `resource_guard.py` / `sdk_tools/compat.py` / `scoped_metadata.py` / `catalog_tools.py` / `query_tools.py` 的数据源作用域或元数据返回逻辑后，必须跑 `tests/test_as_bot_security.py`（含 `test_sql_capable_as_bot_metadata_tools_bypass_projection`、`test_tool_selects_source_by_authorized_name`、`test_tool_rejects_unknown_datasource_name`、`test_catalog_strip_physical_ids_keeps_modeling_columns`）+ 护城河三件套，并为新分支补用例。
- 这些是 web 单进程（`backend/app/main.py`，绑 8001-8007/8012，无 `--reload`）常驻代码，改后重启 web 即生效；AS-BOT 的 `tools`/`system_prompt` 存 `adh_as_bots`，按 `as_bot_key` 幂等更新且注意 DB 常比迁移文件新（只应用目标那条 UPDATE，勿整体重跑）。
- 与 `security-guardrails.md` §7、`as-bot-system-waker.md` 域边界保持一致；冲突时以**更严**的脱敏/域约束为准。
