# AI 能力与人格按角色声明（AS-BOT 配置改造设计）

> 本文是 **AS-BOT 配置改造的独立设计文档**，不在 M2M3M4 建设计划的实施范围内。
> 配套计划：`M2M3M4行为规则场景建模`（行为/规则/场景三层进本体）。
> 裁决依据冲突时以 `.qoder/rules/` 的 always-on 规则为准（尤其 security-guardrails、as-bot-system-waker、waker-datasource-domain）。
>
> 注：AS-BOT/Waker 统一后（`adh_wakers`→`adh_as_bots`），AS-BOT 与智能问数同载体、不同入口；
> 旧「AS-BOT Action 审批通道/角色×动作矩阵」已退役，动作把关收敛为菜单与功能权限码直执行，
> 本文中相关表述已同步该口径。

## 0. 决策结论（已裁决）

1. **AI 可用能力 = 权限声明，归角色**：工具许可（`tool:execute_sql` 等）与动作许可（菜单与功能权限码 + `ai_access` 写级别）都是角色权限声明的一部分，不再由 AS-BOT 单独承载。
2. **AI 形态按角色，不做会话级切换**（用户裁决）：prompt/工作流人格作为角色的「AI 助手配置」段，一个角色一种形态。
3. **AS-BOT 统一（原 Waker 退役）**：`adh_wakers` 已改名 `adh_as_bots`，「权限载体」职责消解；prompt + 工具裁剪降级为角色上的配置段（或保留独立 `AIForma` 声明对象、由角色引用——见 §5 迁移）。
4. **元数据视图由工具许可推导**，不再逐 AS-BOT 配置：角色许可 `execute_sql` → 元数据工具回物理结构（剥内部 id）；未许可 → 只回本体业务投影（waker-datasource-domain §3 的分域语义不变，改由许可推导）。

## 1. 现状与问题

实测现状（`adh_as_bots` 3 行）：

```
admin   tools={query:[check_sql,execute_sql], screen:[...], ...}
analyst tools={semantic:[get_metrics,run_semantic_query,...], ...}
viewer  tools={catalog:[search_metadata,...], semantic:[...], ...}
```

问题：**AS-BOT 名就是角色名**（admin/analyst/viewer，且 `uk_as_bot_role` 1:1 绑定）——AS-BOT 实际在替角色承载权限。后果：

- 能力许可与安全主体两处配置，改一处漏一处（本会话已多次出现「同一判别口径两处漂移」类缺陷）；
- 「切换 AI 形态」与「切换身份」无法区分（若未来需要多形态，只能角色爆炸）；
- AS-BOT 的工具勾选不得成为第二套授权判断（违反「不得另造第二套谁能做什么」的收敛原则）；动作把关已收敛为菜单与功能权限码直执行（角色×动作矩阵/审批通道已退役）。

## 2. 目标形态：角色承载四面

```
角色（唯一安全主体）
├─ ① 数据权限：数据源/表 RBAC、RLS、敏感基线        （已有，不动）
├─ ② 动作权限：菜单与功能权限码 + ai_access 写级别   （fail-closed，直执行）
├─ ③ 工具许可：perm code 形式 tool:<tool_name>       （本设计新增）
└─ ④ AI 形态：prompt / 工作流人格 + 工具裁剪          （本设计新增，由角色挂载）

执行链（三层闸，各管各的）
  工具注册面 = 角色工具许可 ∩ 形态裁剪（只做减法）
  动作授权面 = 菜单与功能权限码裁决（perm_link，无记录 = deny）
  数据面     = RBAC/RLS/敏感基线（对所有人生效含 admin）
```

### 2.1 工具许可（perm code：`tool:<tool_name>`）

- 与既有权限码同注册表（`perm_registry`，参考 `role_menu_function_perms` 机制）；
- AS-BOT 工具组（`as_bot.tools.mcp` 逐工具勾选）整体迁入角色的工具许可；
- **fail-closed**：角色无 `tool:*` 记录 → 工具不注册，LLM 无从调用（保留「未勾选即不注册」语义）；
- AS-BOT 域边界保留：系统助手形态的工具裁剪仍禁 `query` 组（as-bot-system-waker §2 红线由**形态裁剪**承载，不靠角色——角色许可了 `execute_sql` 也不进系统助手形态）。

### 2.2 AI 形态（声明式配置，挂角色）

```jsonc
{
  "key": "form_sql_expert",          // 形态声明（可复用，角色引用）
  "prompt": "取数专家人格…",
  "tool_whitelist": ["query", "catalog"],   // 只在角色许可内做减法
  "metadata_view": "auto"            // auto=按工具许可推导（默认）| business | physical
}
```

- 形态**不挂任何数据权限**（安全红线：切换/配置形态不得改变数据可见性）；
- 同一形态可被多个角色引用（如「SQL 专家」被订单分析师、设备分析师共用）；
- 形态裁剪只做减法：`tool_whitelist` ∩ 角色工具许可，形态永远不能放大能力。

### 2.3 元数据视图推导（删掉逐 AS-BOT 配置）

| 角色工具许可 | search_metadata / get_table_schema 返回 |
|---|---|
| 含 `tool:execute_sql` | 真实物理元数据（表/列/类型/注释），剥 `datasource_id`/内部 id |
| 不含 | 本体业务投影（已授权业务对象属性），不给物理结构 |

判定逻辑从 `compat._as_bot_can_write_sql(runtime)`（现口径：工具授权）迁到「当前角色许可含 execute_sql」，语义等价、口径唯一。

## 3. 安全边界（不因退役而弱化）

- 敏感基线对所有人生效含 admin（§3 顺序不变：敏感 block/mask 先于一切）；
- 动作执行走「菜单与功能权限码把关 + 直执行 + 审计落库」唯一回路（`create_approval` 审批通道已退役）；
- AS-BOT（系统助手形态）的本体域约束 `_assert_system_model` 保留（kind='system' 才可写）；
- 形态/许可变更审计落配置审计，动作审计（`decided_by`/`approved_by`）服务端注入身份。

## 4. 生命周期对比（为什么不叫「角色的一种属性」就够了）

| | 角色 | AI 形态 |
|---|---|---|
| 回答 | 你是谁、被允许什么 | 这次 AI 怎么帮你干活 |
| 变化频率 | 慢（受审计、变更走治理） | 快（产品迭代 prompt 常态） |
| 数据权限 | 有 | **无**（红线） |
| 复用 | 一人一角色组 | 一个形态多角色引用 |

本设计裁决「不做会话级切换」，但形态保留独立声明对象（`AIForma`），理由：prompt 迭代节奏远快于角色，独立对象避免频繁动角色表；且为将来会话级切换留口（挂载点从角色扩到会话即可，schema 不变）。

## 5. 迁移路径（原 Waker 职责消解，分三步、可回退）

1. **双读**：角色工具许可 + 形态落库（从 `adh_as_bots` 一次性导出），执行链按「角色许可 ∩ 形态裁剪」注册工具；`adh_as_bots` 保留但只读（回退开关）。
2. **切换**：判定口径（元数据视图、动作裁决）全部改读角色面；eval + 护城河全绿后，系统助手特化逻辑改挂「系统助手形态」（原 `resolve_system_bot_waker` 已删除，域判定改工具授权 `tool_policy.is_system_scope`）。
3. **退役**：`adh_as_bots` 冻结（参照 `adh_workspace_datasources` 退役先例：保留结构与级联清理，读路径零消费）。

每步验收：`tests/test_as_bot_security.py` + 护城河三件套 + eval 不低于基线；多实例口径（distributed-first）：许可/形态读共享层，无进程内缓存真值。

## 6. 与 M2M3M4 计划的关系

- M2M3M4 计划（Phase A–F）**照常实施**；动作许可口径以菜单与功能权限码为准（角色×动作矩阵已随审批通道退役，不再扩展）；
- 本设计的实施排在 M2M3M4 之后（语义稳定后再动 AS-BOT 配置面，避免同时动两处授权面）；
- `product.contract_change` 等动作归属、业务写动作范围外等口径与 M2M3M4 计划 §5 一致（映射见 as-bot-system-waker §3）。

## 7. 范围外 / 待决策

- 会话级形态切换：用户裁决**不做**（按角色）；
- `AIForma` 是否独立建表 vs 角色表内 JSON 段：迁移时按迁移成本定，语义等价；
- 执行器（Qoder/Claude SDK 执行层）维度与形态的关系：执行器是运行时宿主、形态是行为声明，互不替代，维持现状（工作空间绑定执行器）。
