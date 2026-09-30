# 角色为中心的访问模型改造设计（看板 / 智能问数 / 工作空间）

> 状态：设计评审稿 v1（2026-09-30）
> 范围：数据看板可见性、智能问数（Waker+数据源）授权来源、工作空间从"必选上下文"降级为"可选模块"
> 不变项：数据护城河（permission_enforcer 敏感基线/RBAC/列级/RLS）、Waker 逐工具授权（tool_policy）、AS-BOT 系统域边界均不动

---

## 1. 背景与问题

### 1.1 现状模型（工作空间为中心）

```
用户登录 → 必须落在某个工作空间（workspace_id 必选）
  ├─ 看板：按 adh_dashboards.workspace_id + owner/is_public 过滤
  ├─ 智能问数：Waker = adh_workspace_wakers ∩ adh_role_wakers
  │            数据源 = adh_workspace_datasources ∩ Waker 配置 ∩ 角色数据源权限
  └─ 工作空间切换：adh_workspace_users（个人成员制）
```

证据（现状代码锚点）：
- 前端入口强制进工作空间：`frontend/src/components/WorkspaceRoute.tsx` 的 `WorkspaceEntry` 把
  `/dashboards`、`/ask` 强制重定向到 `/{module}/{workspaceId}`；无工作空间时显示
  "暂无可访问的工作空间，请联系管理员分配权限"。
- 看板列表按空间归属过滤：`services/dataviz/services/dashboard_design_service.py` options()
  `WHERE owner_id=%s AND (workspace_id=%s OR is_public=1)`。
- Waker 解析以空间为锚：`services/datamind/execution/wakers.py: resolve_wakers(workspace_id, ...)`。
- 数据源三方交集：`services/datamind/execution/resource_guard.py: bind_resources`。

### 1.2 问题

1. **看板、问数与工作空间错误耦合**：两者本质是"角色授予的能力"，不该要求用户先选工作空间。
2. **工作空间从"可选资源域"变成"必选上下文"**：没有被授权任何空间的用户连问数都用不了。
3. **角色侧表已存在但未闭环**：`adh_role_wakers`（角色→Waker）、`adh_role_datasource_access`
   （角色→数据源）已存在且被执行层消费；`adh_workspace_roles`（空间→角色授权）有表、有 demo
   种子（`docker/mysql/permission_demo_migration.sql` §7），但前后端零消费，是死表。
4. **看板缺少角色授权维度**：可见性只有 `owner_id` / `workspace_id` / `is_public` 三个口径，
   没有"把看板授权给某个角色"的能力。

### 1.3 目标模型（用户确认的期望）

```
用户角色是授权核心：
  ├─ 看板：看板授权给角色 → 角色用户直接可见（与工作空间无关）
  ├─ 智能问数：使用角色授予的 Waker + 数据源（与工作空间无关）
  └─ 工作空间：额外授权给角色 → 被授权才有"工作空间"切换模块；
               未被授权任何空间 → 前端不出现该模块（不是报错）
```

---

## 2. 目标架构

### 2.1 三种会话域（Scope）

引入显式的会话域概念，替换隐式的"必须有 workspace_id"：

| 域 | workspace_id 取值 | 进入条件 | Waker 来源 | 数据源来源 | 看板来源 |
|---|---|---|---|---|---|
| **个人域（personal）** | 0 | 所有已认证用户（默认） | 角色白名单 `adh_role_wakers` | 角色权限 ∩ Waker 配置 | 角色授权 + is_public |
| **工作空间域（workspace）** | >0 | 角色 ∈ `adh_workspace_roles`（∪ 存量成员制，见 §6） | 空间绑定 ∩ 角色白名单（现状逻辑） | 空间绑定 ∩ Waker 配置 ∩ 角色权限（现状逻辑） | 空间归属 + 角色授权 + is_public |
| **系统域（system）** | 0 + waker_key=`__system_bot__` | 仅 admin（现状不变） | `resolve_system_bot_waker` 强制覆盖 | 系统源(0) | 系统本体大屏 |

**消歧**：个人域与系统域都用 `workspace_id=0`，用 `waker_key` 区分——
`__system_bot__` 走系统域（现状校验 `ctx.user_role != "admin" or ctx.workspace_id != 0` 即拒绝，
保持不变）；其余 Waker 在 workspace_id=0 时一律按个人域解析。系统域不需要新增字段。

### 2.2 授权关系总览（改造后）

```
adh_roles（角色）
  ├─ adh_role_permissions      权限码 → 菜单/API（现状不动）
  ├─ adh_role_wakers           角色 → 可用 Waker        （现状已有，个人域升为主来源）
  ├─ adh_role_datasource_access 角色 → 可用数据源        （现状已有，个人域升为主来源）
  ├─ adh_role_dashboards       角色 → 可见看板           （【新增】本设计）
  └─ adh_workspace_roles       角色 → 可进入的工作空间   （【启用】现有死表转正）
```

---

## 3. 数据模型变更

### 3.1 新增 `adh_role_dashboards`（角色→看板授权）

```sql
CREATE TABLE IF NOT EXISTS adh_role_dashboards (
    id            BIGINT NOT NULL AUTO_INCREMENT,
    role_id       BIGINT NOT NULL COMMENT '角色 ID(adh_roles.id)',
    dashboard_id  BIGINT NOT NULL COMMENT '看板 ID(adh_dashboards.id)',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_role_dashboard (role_id, dashboard_id),   -- 幂等授权
    INDEX idx_role (role_id),
    INDEX idx_dashboard (dashboard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色-看板可见性授权';
```

迁移文件：`docker/mysql/role_dashboards_migration.sql`（建表 + 回填，见 §7）。

### 3.2 启用 `adh_workspace_roles`（空间→角色授权）

表已存在（`permission_demo_migration.sql` §7 已有种子），补齐正式迁移与消费逻辑：

```sql
-- 确认结构（已存在则跳过）
CREATE TABLE IF NOT EXISTS adh_workspace_roles (
    id           BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id BIGINT NOT NULL,
    role_id      BIGINT NOT NULL,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_role (workspace_id, role_id),          -- 幂等授权
    INDEX idx_role (role_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作空间-角色可见性授权';
```

### 3.3 字段语义降级（不删不改结构）

| 字段/表 | 原语义 | 新语义 |
|---|---|---|
| `adh_dashboards.workspace_id` | 可见性归属 | **管理归属**（管理域组织/检索用），不再单独构成可见性门控 |
| `adh_dashboards.is_public` | 所有用户可见 | 保留：登录用户可见（公开大屏链接 `/screen` 兼容） |
| `adh_workspace_users` | 空间成员制（唯一入口） | 兼容期保留（owner 记录 + 存量授权并集来源），见 §6.3 收口计划 |

### 3.4 明确不新增的东西

- 不给 `adh_wakers` 加"角色可见性"新字段——角色→Waker 授权就用 `adh_role_wakers`。
- 不引入"个人工作空间"实体——个人域是 `workspace_id=0` 的解析分支，不落库实体。
- 不动 `adh_role_datasource_access` 结构（`access_type` 继续只认 read 语义，写入侧不变）。

---

## 4. 执行层改造（datamind）

### 4.1 `resolve_wakers`（wakers.py）

```python
def resolve_wakers(workspace_id, user_role="", waker_key="", user_id=0, ...):
    # workspace_id == 0 且非系统 bot → 个人域：
    #   候选 = adh_role_wakers 绑定的 active Waker（角色主导，不要求空间绑定）
    #   admin 个人域 = 全部 active Waker（排除 __system_bot__，系统 bot 仍走独立解析）
    # workspace_id > 0 → 工作空间域：现状逻辑不变（空间候选 ∩ 角色白名单）
```

规则细节：
- 个人域角色无绑定 → 空（fail-closed，不回退全量；admin 例外见上）。
- `waker_key` 收窄语义不变：所选 Waker 不在生效集合即报 `PermissionError`。
- 系统域判定优先于个人域判定（`waker_key == '__system_bot__'` 先短路，现状代码顺序保留）。

### 4.2 `bind_resources`（resource_guard.py）

> 决策更新：Waker 不再持有数据源配置（`datasource_ids` 配置维度已移除，DB 列保留不消费）。
> Waker 必须依托用户或工作空间使用，数据源范围始终由「工作空间绑定 ∩ 用户角色权限」决定；
> 定时任务的 Waker 也继承任务创建者的角色权限，无需在 Waker 上单独配置数据源。

```python
# 现状分支保留：__system_bot__ → allowed = set()（系统源独立）
# 个人域分支（workspace_id == 0 且非系统 bot）：
#   allowed = 角色数据源权限全集          # 无空间绑定可交集，角色即唯一裁决层
#   空角色权限 = fail-closed（不解释为全量）
# 工作空间域：allowed = 空间绑定 ∩ 角色数据源权限（admin 跳过角色交集）
```

规则细节：
- 两个域共用同一裁决口径，仅“是否有空间绑定层”不同；不存在 Waker 自持范围的第三条分支。
- 会话已选源撤回 → 报错不静默切换（现状规则原样保留，个人域同样适用）。
- `ctx.extra["available_datasource_ids"]` / `tag_authorized_datasource_ids` 注入口径不变，
  个人域自然等于角色授权集。

### 4.3 `authorize_workspace`（shared/common/auth.py）契约点

- 个人域放行规则：`workspace_id=0` 且非 `__system_bot__` 会话 → 已认证用户放行
  （个人域不承载资源白名单，资源由角色绑定裁决；空间 0 仅是锚点）。
- `__system_bot__` + workspace 0 仍要求 admin（现状不变）。
- 此函数当前对 0 的行为需在实现时核对并补用例锁定（这是个人域的守门点，不允许"没查就放"）。

### 4.4 role_service 契约点

- `get_user_allowed_datasources(user_id, workspace_id=0)`：个人域按全局角色数据源权限解析
  （现状 `adh_role_datasource_access` 查询对 workspace 的依赖需核对；若现查询强依赖
  workspace_id 匹配，需提供"个人域=仅全局授权"分支，并保证空集 fail-closed）。
- `get_user_roles(user_id, workspace_id=0)`：返回用户全局角色集合（个人域语义）。

### 4.5 不变项（防止过度改造）

- `permission_enforcer.check_access`（敏感基线/RBAC/列级/RLS）：入参 workspace_id 原样透传，
  个人域传 0；enforcer 内部本就按 user_id+datasource 裁决，无空间白名单逻辑。
- `tool_policy.compile_policy / check_tool / live_ceiling`：逐工具授权与 digest 机制不动。
- 取数双路径（语义层主路 + nl2sql 受治理旁路）不动。

---

## 5. API 层改造

### 5.1 看板可见性（dataviz）

- 看板列表/详情查询从"owner + 空间归属"改为四来源并集，并在响应中标注来源
  （`visibility_source: role_grant | public | owner | workspace`，满足"行为可区分、可诊断"）：
  1. `role_grant`：用户角色 ∈ adh_role_dashboards 授权的看板；
  2. `public`：`is_public=1`；
  3. `owner`：`owner_id=user`；
  4. `workspace`：看板归属空间 ∈ 用户可进入空间（兼容期保留，见 §6.2）。
- 管理端（系统配置-看板管理）新增"角色授权"编辑入口（授权/回收 `adh_role_dashboards`），
  复用现有权限码 `dashboard:manage` 鉴权。

### 5.2 工作空间列表与授权（authservice）

- `GET /workspaces`（我的空间列表）改为：**角色制授权 ∪ 存量成员制**（并集，兼容期），
  响应项标注 `access_source: role_grant | member`。
- 管理端（WorkspaceManagerV2）新增/改造"角色授权"Tab：维护 `adh_workspace_roles`
  （授权/回收），复用权限码 `workspace:manage`。
- 新增辅助接口（或扩展现有接口字段）：`GET /workspaces/accessible-summary` 返回
  `{has_workspaces: bool, items: [...]}`，供前端决定是否渲染工作空间模块。

### 5.3 问数（chat）个人域支持

- chat/执行链路入参 `workspace_id=0` 合法化：不再要求空间成员校验，
  走 §4 个人域解析。会话记录 `adh_conversations.workspace_id=0` 原样落库。
- AS-BOT 独立会话（现状已用 workspace 0）不受影响（waker_key 区分）。

---

## 6. 前端改造

### 6.1 路由与入口（App.tsx / WorkspaceRoute.tsx）

- `/dashboards`、`/ask` 不再经 `WorkspaceEntry` 强制重定向：
  - `/dashboards` → 看板模块首页（数据源=角色授权并集，无空间选择器）
  - `/ask` → 智能问数（个人域，workspace_id=0）
- `/ws/:workspaceId/*` 保留：工作空间域专属路由（含空间内只读看板等既有约束不变）。
- `WorkspaceBoundary` 保留在 `/ws/*` 路径，校验规则改为
  `role_grant ∪ member`（与后端列表接口同一口径，避免两端漂移——共用同一响应）。

### 6.2 工作空间模块条件渲染

- 顶栏/侧边栏的"工作空间"切换器：`accessible-summary.has_workspaces === true` 才渲染；
  否则整个模块不出现（不是禁用态、不是报错页）。
- 看板模块内提供"在空间中查看"入口时，同样按 has_workspaces 条件显示。

### 6.3 权限存储

- `workspaceStore` 保持现状（列表来自 `/workspaces`），新增 `accessibleSummary` 状态；
  授权撤回后下次拉取自动消失（实时读库，无本地缓存真值——符合分布式约束）。

---

## 7. 数据迁移方案

迁移文件：`docker/mysql/role_dashboards_migration.sql`（一次性、幂等、可重跑）。

1. 建 `adh_role_dashboards`（§3.1）。
2. 回填策略（宁缺勿滥，只回填有依据的授权）：
   ```sql
   -- 依据1：空间角色授权 → 该空间内看板授权给该角色
   INSERT IGNORE INTO adh_role_dashboards (role_id, dashboard_id)
   SELECT wr.role_id, d.id
   FROM adh_workspace_roles wr
   JOIN adh_dashboards d ON d.workspace_id = wr.workspace_id;
   -- 依据2：空间成员（存量成员制）所属角色 → 该空间看板
   --   （成员的 workspace_users.role 是字符串，与 adh_roles.name 关联映射）
   -- 依据3：is_public=1 不回填（继续由 public 口径放行，不制造重复授权）
   ```
3. `adh_workspace_roles` 结构确认（§3.2），保留现有 demo 种子数据。
4. 不自动改写任何 `adh_wakers` / `adh_role_wakers` / `adh_role_datasource_access`
   现有数据（符合"安全策略变更不静默迁移线上配置"的既有决策）。

---

## 8. 分期实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1 授权层数据 + 管理端** | 建表迁移；看板列表四来源并集（含来源标注）；WorkspaceManagerV2 空间角色授权 Tab；看板管理角色授权入口 | 管理员可完成"看板→角色""空间→角色"授权；列表接口来源字段可观测 |
| **P2 问数个人域** | resolve_wakers/bind_resources/authorize_workspace/role_service 四个契约点改造（§4）；chat 支持 workspace_id=0；`/ask` 独立路由不再强制空间 | 普通用户不选任何工作空间即可问数；Waker/数据源完全由角色绑定决定；权限撤回即时生效 |
| **P3 前端模块化收口** | `/dashboards` 脱离空间；工作空间切换器条件渲染；WorkspaceEntry 适配 | 未被授权空间的账号看不到工作空间模块，看板/问数功能完整可用 |
| **P4 存量收口（独立排期）** | `adh_workspace_users` 成员制退役评估；`adh_dashboards.workspace_id` 可见性口径退役评估 | 迁移完成后单独评审，不在本次范围 |

依赖关系：P2 的执行层改造不依赖 P1；P3 依赖 P1+P2 的接口就绪。

---

## 9. 测试计划

后端（pytest）：
- 新增 `tests/test_role_scope_resolution.py`：
  - 个人域 Waker 解析（角色绑定命中/未绑定 fail-closed/admin 全量/系统 bot 不受影响）；
  - 个人域数据源交集（Waker 空配置继承角色权限、非空收窄、空角色权限拒绝、撤回报错不切换）；
  - `authorize_workspace(0)` 契约（普通用户放行、`__system_bot__` 非 admin 拒绝）。
- 新增 `tests/test_role_dashboards.py`：四来源并集、来源标注、唯一键幂等授权、回收即时生效。
- 回归（按项目门禁）：
  - `tests/test_waker_resolution.py`（resolve_wakers 分支变化）；
  - 护城河三件套 `test_data_moat_enforcement.py` / `test_permission_enforcer.py` /
    `test_permission_e2e.py`（bind_resources 传 0 路径）；
  - `tests/test_waker_security.py`（个人域不影响沙箱与逐工具授权）。

前端（vitest）：
- WorkspaceEntry/切换器条件渲染（has_workspaces=false 无该模块）；
- `/dashboards` 无空间上下文渲染。

---

## 10. 风险与开放问题

| # | 风险/开放问题 | 处置 |
|---|---|---|
| R1 | 现存代码大量假设 `workspace_id>0`（审计落库、执行层目录 `ws_<id>`、df 归属等） | 个人域统一映射为 0；会话工作区目录沿用 `ws_0`（与 AS-BOT 系统会话同目录策略，已有先例）；实现时全量 grep `workspace_id` 消费点逐一确认 |
| R2 | AS-BOT 系统域与个人域共用 workspace 0 | waker_key 短路区分（§2.1），并补用例锁定两个分支互不可达 |
| R3 | `authorize_workspace`/`role_service` 对 0 的现行为未逐行核对 | 列为 P2 首个实现步骤 + 用例锁定（§4.3/§4.4 契约点） |
| R4 | 回填可能漏授权导致部分看板"消失" | is_public 与 workspace 口径兼容期保留（并集），且来源字段可诊断；回填 SQL 只增量不删减 |
| R5 | 成员制与角色制并存的口径漂移 | 列表接口单一实现输出 `access_source`，前端不做二次判断（共用解析） |
| Q1 | 空间域内看板是否仍按 `workspace_id` 归属展示 | P1-P3 兼容期：是（并集之一）；P4 评审是否退役 |
| Q2 | 个人域是否需要"个人空间"命名呈现在 UI | 本设计按"我的看板/智能问数"呈现，不暴露 workspace=0 概念 |

---

## 11. 改造文件清单（预期）

后端：
- `services/datamind/execution/wakers.py`（个人域解析）
- `services/datamind/execution/resource_guard.py`（个人域交集）
- `services/shared/common/auth.py`（authorize_workspace 契约）
- `services/authservice/services/role_service.py`（个人域角色/数据源解析）
- `services/authservice/api/workspaces.py`（列表口径 + 角色授权 API）
- `services/dataviz/`（看板可见性查询 + 授权管理 API）
- `docker/mysql/role_dashboards_migration.sql`（新增迁移）

前端：
- `frontend/src/App.tsx`、`frontend/src/components/WorkspaceRoute.tsx`（入口脱空间）
- `frontend/src/stores/workspaceStore.ts`（accessibleSummary）
- `frontend/src/pages/WorkspaceManagerV2.tsx`（空间角色授权 Tab）
- 看板管理页（角色授权入口）

测试：
- `tests/test_role_scope_resolution.py`、`tests/test_role_dashboards.py`（新增）
- 既有回归集（§9）
