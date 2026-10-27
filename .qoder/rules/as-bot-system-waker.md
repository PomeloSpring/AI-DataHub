---
trigger: always_on
description: AS-BOT=角色智能体（与智能问数同载体、不同入口）；本体可见域按 kind 分域（system/business/source），系统能力（system 组）是**权限叠加**（admin 专属）非域互斥；本体写域仅限系统本体；写动作由工具授权 + 菜单与功能权限码把关，直执行 fail-closed。
---

# AS-BOT 定位：角色智能体统一体（权限码把关 + 直执行）— 强制规范

> AS-BOT 与智能问数中的 LLM 是**同一载体、不同入口**：配置随角色继承（`adh_as_bots`，键 `as_bot_key`），
> 不再有独立的「AS-BOT 动作」配置面，也没有 `__system_bot__` 哨兵。写动作由**菜单与功能权限码**
> （`adh_role_perms` + `adh_perm_registry.ai_access`，经 `backend/core/perm_link.py` 裁决）
> + **AS-BOT 工具授权**直接把关执行（fail-closed），无提议→审批→执行回路。

## 1. 本体可见域（kind 硬边界 + 能力叠加）
- 分域判别**只用 `kind`**：系统本体 `kind='system'`、业务本体 `kind='business'`、源本体 `kind='source'`。**不得**用 `datasource_id=0` 判系统本体（业务本体的 datasource_id 也是 0，旧口径会把业务本体当系统本体暴露/可写）。
- **域规则（能力叠加，非互斥）**：系统域只是**权限的一部分**——策略授权 `system` 工具组（`system_usage`/`system_overview`）即拥有系统能力面（系统本体可见、系统工具可调、系统源 0 可访问），**业务能力照常叠加**（角色数据源授权继承、跨源业务本体 + 本绑定源的源本体、业务检索），不因系统能力而屏蔽业务。
- 无 `system` 组授权（如 analyst/viewer 形态）：仅业务域（跨源业务本体 + 本绑定源的源本体），会话源必须在用户角色授权集内。
- 无执行运行时（如设计服务显式传 `resource_scope` 的调用）一律按业务域处理。系统运营问题**必须**走 `system` 工具组回答，严禁拿业务知识充数（`no-silent-degradation.md`）——由 prompt 引导 + 检索来源分桶承担，不是检索硬限定。

## 2. 本体写域约束（按权限码裁决 + 草案/发布两段式）
> **2026-10-26 用户拍板：废除「AS-BOT 不得写业务本体」的硬禁令，改为权限控制。**

- **按权限码裁决**，不再按 kind 硬拒：只要调用方具备对应权限码，AS-BOT 即可写**业务本体与系统本体**。
  新增/修改/发布各有独立权限码，由 `adh_role_perms` 把关（fail-closed：无码即拒），
  不再用 `_assert_system_model` 一刀切拒业务模型。
- **两段式生效**：AS-BOT 的任何本体改动只落**草案**（draft），不直接生效；
  **发布（publish）是独立动作 + 独立权限码**，发布后才级联（重展开 objects → 重建图谱 → 同步知识库）。
  这样权限齐备的用户可以让 AS-BOT 改本体/指标/维度/关系，但不会直接污染线上口径。
- **UI 分工**（用户拍板）：本体建模页只暴露「新增对象」入口；**修改仅作为 AI 权限**（即 AS-BOT 通道），
  用户侧不提供修改操作入口。发布入口对有发布权限的用户开放。
- 仍然**不接受** LLM 传业务数据源标识（`_reject_datasource_arg`，守 §2 黑盒）。
- 任何整份覆盖仍受 `_reject_destructive_overwrite` 保护：不得抹掉已有对象的属性/关系（宁阻断勿损毁）。
  读接口脱敏只回对象摘要（properties 空），调用方**不得**凭摘要重建整份 JSON 提交。

## 3. 写动作唯一回路：菜单与功能权限码 + 工具授权直执行
- 权限口径**唯一**：`adh_user_roles ⋈ adh_role_perms ⋈ adh_perm_registry.ai_access` + 涉密硬上界（`perm_link.SECRET_BOUND_*`，代码层 cap 不可被配置覆盖）；**不得另造第二套「谁能做什么」判断**。
- 执行前 `perm_link.require_write_perm`（perm_code + `ai_access='write'`）把关，拒绝原因可解释；动作→权限码映射：`ontology.generate/save/activate/import`、`metadata.sync`→`sync:manage`、`task.claim_owner`→`scheduled:manage`、`alias.approve/reject`→`ontology:save`、`dashboard.publish`→`dashboard:manage`、`product.contract_change`→`dataset:manage`。
- 直执行 + **审计落库**：`decided_by`/`approved_by` 由服务端注入（不从请求体信任身份）；并发/重复执行由状态条件更新仲裁幂等（宁阻断勿重复落库）。
- canonical 本体 `objects[].actions[]` 与 `rules[] permission` 仅是**建模描述**（无运行时消费方），不作为执行把关依据。

## 4. 仪表盘设计例外（只读业务知识，写仍走发布把关）
- AS-BOT 判断需要业务上下文时，先提出设计并由用户确认业务范围；仅 `search_business_knowledge/get_business_semantics` 可消费该设计授权域，旧系统工具不切域。
- SQL 仅由服务端生成，在鉴权设计面板供人查看/编辑并重新治理预览，不进入 Agent 工具返回。正式发布走 `POST /dashboard-designs/{id}/publish`（`expected_version` + 预览 digest 失效校验，`dashboard:manage` 把关）；修改使旧预览失效。
- 设计会话绑定只校验 `user_id` 归属（会话历史合并共用，不按入口区分）。

## 5. 改动纪律
- 为 AS-BOT 新增能力时，先问：**这个动作的对象是系统本体/系统元数据，还是业务本体？** 业务侧不塞进 AS-BOT。
- 新增写动作必须同批落地：权限码映射 + `require_write_perm` 把关 + 审计字段服务端注入 + fail-closed 用例（`tests/test_as_bot_execution.py`）；禁止任何"直接 UPDATE 元数据表"的旁路。
- 新增"系统问题→数据源路由"逻辑（prompt、planner、工具描述）时，同步在评测中加用例断言：系统运营问题不得命中业务本体，否则视为定位漂移。
- 与 `security-guardrails.md` §7、`waker-datasource-domain.md` 域边界保持一致；冲突时以**更严**的脱敏/域约束为准。
