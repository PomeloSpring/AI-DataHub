# 设计文档：catalog 进入物理表引用（三段式联邦）

> 状态：设计方案（未实现） · 范围：元数据 / 取数执行 / 治理 / API / 前端
> 决策前提：采纳「**catalog 作为物理表引用的一部分**」——即 SQL 中允许并支持
> `catalog.database.table`（三段式）与 `database.table`（两段式）限定名，并以此打通
> **跨数据源联邦查询（联邦 JOIN）**。本文档给出决策完备的落地设计，实现按 §10 分期推进。

---

## 1. 背景与目标

当前系统的取数链路把「一个查询」死死绑定到「一个 datasource_id」：引擎每次查询只把单个
数据源注册进固定 catalog `datafusion`（`services/dataengine/src/engine/session.rs:149`），
`engine_client.query(sql, datasource_id, ...)` 一次只接受一个数据源
（`services/shared/common/engine_client.py:99`）。因此：

- **单数据源内 JOIN**：支持（且近期已修复别名/重复列名/RLS 注入相关问题）。
- **跨数据源联邦 JOIN**（不同 datasource 的两张表在一条 SQL 里 JOIN）：**不支持**。

`adh_catalogs` 表已建但为空；其字段 `catalog_type ENUM('internal','jdbc','es','hive','iceberg')`
+ `remote_props JSON` + `federation_enabled` **本就是为 Doris 外部 catalog 联邦预留**。
引擎侧也已存在三段式联邦下推逻辑：`services/dataengine/src/engine/pushdown.rs`
（`needs_pushdown` 检测 `catalog.db.table` → 整条 SQL 连同 RLS 注入下推到 Doris 原生执行）。

**目标**：把 catalog 升级为查询链路的一等公民——物理表引用采用 `catalog.database.table`，
以 Doris 为联邦枢纽统一跨源取数，同时让元数据与前端按
**catalog → datasource → table/view → column** 四级组织展示。

---

## 2. 核心架构决策：以 Doris 为联邦 / catalog 枢纽

三种候选执行形态，选型结论明确：

| 形态 | 做法 | 结论 |
|---|---|---|
| **A. Doris Catalog Hub（采用）** | 外部源(MySQL/PG/ES/Hive/Iceberg)登记为 **Doris 外部 catalog**（`CREATE CATALOG ... TYPE(...) PROPERTIES(...)`）；联邦查询一律走 Doris 数据源，靠已有 pushdown 下推执行 | ✅ 复用既有 pushdown/RLS 注入；Rust 改动最小；与 `adh_catalogs` schema 天然对齐 |
| B. DataFusion 多 catalog | 在单个 SessionContext 内把 N 个 datasource 注册成 N 个 catalog，纯 DataFusion 联邦 | ⚠️ 需自研 `CatalogProvider`/`SchemaProvider` 多源装配 + 跨源连接/下推，工作量大，作为「非 Doris 网关」兜底才考虑 |
| C. 维持单源 | 禁止三段式 | ❌ 与本次决策相悖 |

**采用 A**：所有「带 catalog 限定名 / 跨源」的查询，其**执行网关数据源恒为一个 Doris 数据源**；
其余数据源以 Doris 外部 catalog 形式存在，`catalog` 名即 Doris catalog 名。纯单库内查询可继续
走各自原生路径（MySQL 直连 / 单源 DataFusion），不强制绕 Doris。

```
                      ┌──────────────────────── Doris 网关 ───────────────────────┐
 SQL: SELECT ... FROM jdbc_crm.customdb.t_customer c                  Doris FE/BE │
        JOIN iceberg_dw.dwd_orders o ON ...                           (原生执行)  │
   └─ pushdown.rs: 检测三段式 → 注入 RLS → 直接下推 Doris 执行                      │
        │            │             │                                              │
   catalog=internal  catalog=jdbc_crm   catalog=iceberg_dw   ← adh_catalogs（Doris 外部 catalog 注册表）
                      └───────────────────────────────────────────────────────────┘
```

### 2.1 catalog / datasource / 网关 的关系（决策）
- **`catalog` = 物理寻址的第一段**，语义上等于「一个可查询的数据空间」。Doris 自身
  `internal` catalog 代表其本地库；每个被纳管的外部数据源登记为一个 Doris 外部 catalog。
- **`datasource`（`adh_datasources`）= 连接凭据载体 + 治理归属域**：一个 datasource 至多承载
  一个 catalog（外部源）或即网关自身（Doris=internal）。
- **网关（gateway）**：一个被指定为 `federation_gateway` 的 Doris 数据源，是所有限定名/跨源
  查询的执行落点。新增 `adh_datasources.is_federation_gateway`（或在 `adh_system_config` 记
  全局网关 datasource_id）。

---

## 3. 命名与解析规范

### 3.1 规范引用（canonical reference）
统一四段式内部表示 `catalog.database.table.column`，展示与治理以此为准：
- **三段式表引用**：`catalog.database.table`（联邦/跨库推荐写法）。
- **两段式**：`database.table` → `catalog` 解析为「当前查询上下文默认 catalog」。
- **单段式**：`table` → 解析为「默认 catalog.默认 database.table」（完全向后兼容现状）。

### 3.2 默认 catalog 解析优先级（入口无歧义）
1. 请求显式 `catalog` 参数（前端目录树选中的 catalog / API query）；
2. 数据源 `adh_datasources.default_catalog_id` 指向的 catalog；
3. 网关 Doris 的 `internal`。
> 与既往「语义层 planner 因三段式 `catalog_ref` 破坏执行」的坑对照：本方案下 planner/执行侧
> **必须真正识别 catalog 段**（见 §6），不再忽略。

### 3.3 向后兼容矩阵
| 输入写法 | 解析结果 | 执行路径 | 兼容 |
|---|---|---|---|
| `FROM t` | defaultCatalog.defaultDB.t | 单源原生（若同库）或网关 | ✅ |
| `FROM db.t` | defaultCatalog.db.t | 同上 | ✅ |
| `FROM cat.db.t` | cat.db.t | **网关 Doris（pushdown）** | ✅ 新能力 |
| 跨 catalog JOIN | 多段限定 | **网关 Doris（pushdown）** | ✅ 新能力 |

---

## 4. 数据模型变更（增列/新增，向后兼容、可空带默认）

### 4.1 `adh_catalogs`（启用为 Doris 外部 catalog 注册表）
```
id, datasource_id            -- 该 catalog 背后的凭据 datasource（external 必填；internal 指向网关）
catalog_name                 -- = Doris CREATE CATALOG 的 catalog 名（全局唯一，规范小写）
catalog_type enum(internal/jdbc/es/hive/iceberg)
remote_props json            -- 映射 Doris PROPERTIES：type/jdbc_url/user/password/catalog-type...
federation_enabled tinyint   -- 是否参与联邦（internal 恒 true）
+ is_default tinyint         -- 新增：是否网关默认 catalog
+ status / last_synced_at    -- 新增：健康与同步态
```
- `remote_props` 内敏感项（password）沿用 `crypto` 加密存储；下发 Doris 前解密。
- 迁移：为每个现存 datasource 派生一个默认 catalog（external 源→`jdbc`/`es`；Doris→`internal`），
  回填 `adh_datasources.default_catalog_id`。

### 4.2 `adh_datasources`
- `+ is_federation_gateway tinyint`（标记网关 Doris）。
- `default_catalog_id` 由 NULL 回填为派生 catalog。

### 4.3 `adh_table_info`（表/视图级）
- `+ catalog_id bigint`（已存在列，回填）。
- `+ schema_name varchar`（新增：库名；PG 为 schema）。
- `+ table_type enum('BASE TABLE','VIEW','MATERIALIZED VIEW')`（新增：区分表/视图）。
- `+ physical_ref varchar`（新增：规范三段式 `catalog.database.table`，治理/血缘主键用）。
- 现有 `query_mode('materialized','federated','raw_source')` 启用：限定名/跨源 = `federated`。

### 4.4 `adh_column_metadata`
- `+ catalog_id`, `+ schema_name`（便于四级树懒加载与限定名寻址）；唯一键升级为
  `(datasource_id, catalog_id, schema_name, table_name, column_name)`。

### 4.5 治理键升级（**关键安全项**）
- 行级/列级策略、敏感字段标记、RBAC 表授权当前按**裸表名**匹配（`enforcer.check_access`、
  `rls_service`、`adh_sensitive_fields`；引擎 `pushdown.rs:314` 也按剥 qualifier 的基名）。
- 三段式后**同名跨库会串味**（`crm.t_user` vs `dw.t_user`）。策略：治理 key 升级为
  `physical_ref`（`catalog.database.table`），并保留「按裸表名」的历史策略作为**兜底通配**
  （命中任一即生效，取更严格者），确保不弱化现状。

---

## 5. 元数据同步（catalog / schema / 视图 一并纳管）

### 5.1 放开视图 + 记录类型/库
- `sync/metadata_sync.py:_fetch_tables`（L81-88）与
  `services/datacatalog/services/datasource_service.py:_list_db_tables`（L716-741）：
  把 `TABLE_TYPE = 'BASE TABLE'` 改为 `TABLE_TYPE IN ('BASE TABLE','VIEW')`
  （PG：`table_type IN ('BASE TABLE','VIEW','MATERIALIZED VIEW')`），并 `SELECT TABLE_TYPE`
  写入 `adh_table_info.table_type`，同批写 `schema_name`/`catalog_id`。
- 引擎侧 `discovery.rs` 取列本就不按 `TABLE_TYPE` 过滤（视图列已可发现）→ **视图在网关可查**，
  放开仅影响展示层，不新增执行风险。

### 5.2 外部 catalog 元数据发现
- 对登记为 Doris 外部 catalog 的源，元数据**统一从网关 Doris 侧发现**：
  `information_schema.tables/columns` 在 Doris 下带 `TABLE_CATALOG`，一次拿全
  `catalog → database → table/view → column`。新增 `sync_catalog_metadata(gateway_ds)`。
- 单源原生 datasource 仍保留自身直连同步（不依赖网关在线）。

### 5.3 Doris catalog 供给（provisioning）
- 新增服务 `catalog_provisioner`：把 `adh_catalogs`（external）翻译成
  `CREATE CATALOG IF NOT EXISTS <name> PROPERTIES(...)`，通过网关执行；变更/删除走
  `ALTER/DROP CATALOG`；`federation_enabled=0` 不下发。凭据轮换需重建 catalog 并清池。

---

## 6. 取数执行链改造

### 6.1 Python 侧
- `services/shared/common/engine_client.py`：
  - `query()` 增加 `catalogs: list[str]` / `gateway_datasource_id` 透传；联邦查询的
    `datasource_id` 恒解析为**网关 Doris**。
  - 新增 `provision_catalog()` / `drop_catalog()` 对接引擎新端点（§6.2）。
- `services/datamind/nl2sql/sql/query_executor.py`：
  - 新增 `resolve_execution_target(sql, datasource_id, catalog)`：解析 SQL 内出现的
    catalog 段集合；**只要出现限定名或跨 catalog → 改路由到网关 Doris（pushdown 路径）**，
    否则维持现状单源。
  - `execute_query_with_permission()` 入口先做「引用规范化 + 目标解析」，再进护城河
    `permission_enforcer.enforce_sql`（其治理键按 §4.5 用 `physical_ref`）。
- 各取数出口（Playground `execute_via_playground`、dataviz `governed_execute`、Chat 语义、
  报表/看板刷新）**统一复用同一 `resolve_execution_target`**，保持"所见即所执行"同源。
  序列化仍走已修复的 `services/shared/common/df_serialize.py`（限定名结果列消歧不受影响）。

### 6.2 Rust 引擎侧（复用为主，改动有限）
- **已具备**：`pushdown.rs` 三段式检测 + RLS 注入 + 下推 Doris（`needs_pushdown`、
  `has_federation_reference`、`walk_query_for_federation`）。联邦执行主路径可直接复用。
- **需补**：
  1. `session.rs`/`datasources.rs`：新增「按 `adh_catalogs` 在网关上 provision
     Doris 外部 catalog」的端点与幂等逻辑（`/api/catalogs` POST/DELETE）。
  2. `pushdown.rs:314` 策略匹配由「剥 qualifier 基名」升级为「**按 `catalog.db.table` 全限定**
     匹配，回退基名」，与 §4.5 对齐（避免跨库误过滤/漏过滤）。
  3. `api/query.rs`：响应携带实际执行模式（`federated_pushdown` / `datafusion` / `direct`）
     供可观测与 Playground 展示。
- **非 Doris 网关兜底（形态 B，列为后续可选）**：实现多 catalog
  `CatalogProvider`/`SchemaProvider`，把 N 个 datasource 注册进一个 SessionContext。仅在
  「不允许引入 Doris 网关」的环境才启用，成本高，默认不做。

---

## 7. 治理（RLS / 列级 / 敏感 / 审计）改造

- **表解析器升级**：`enforcer._extract_tables` 现为 `\b(?:FROM|JOIN)\s+(\w+)`（只吃裸名，
  带点号会截断）。改为 **sqlglot 解析**：抽取全限定表引用集合
  `{(catalog, database, table)}`，无 catalog 段时按 §3.2 默认解析。注意 sqlglot 30.x
  `Select.args` 键 `from→from_` 的历史坑，遍历需同时兼容。
- **RLS 注入**：`_inject_row_filter` 已修复「保留别名 + 覆盖 JOIN 表」。三段式下改为
  **AST 级谓词注入**（与引擎 pushdown 一致的语义），对每个全限定表按其策略加
  `WHERE`；跨 catalog JOIN 时各表分别注入，杜绝旁路。
- **列屏蔽/脱敏**：`apply_post_processing` 与敏感基线仍按结果列名生效；限定名结果的重复列
  消歧由 `df_serialize` 处理，block 列按「表限定 + 列」精确匹配（避免误删同名合法列）。
- **审计**：`_log_permission_audit`（刚落地的数字/文本分列修复）扩展记录
  `catalog / physical_ref / execution_mode / gateway_datasource_id`；`adh_rls_audit_logs`
  增列 `catalog_id`、`physical_ref`、`exec_mode`。

---

## 8. API 层级（catalog → datasource → table/view → column）

现状端点：`/datasources`、`/datasources/{id}/tables`、`/datasources/{id}/tables/{t}/columns`、
`/table-info`、`/metadata`（`services/datacatalog/api/{datasources,metadata,catalog}.py`）。

新增/扩展：
- `GET /catalogs`、`GET /catalogs/{id}`：列 catalog（含 type/健康/是否网关）。
- `POST /catalogs`、`PUT /catalogs/{id}`、`DELETE /catalogs/{id}`：登记/编辑/下线外部 catalog
  （触发 §5.3 provision）。
- `GET /catalog/tree`：返回四级骨架（catalog → datasource → table/view），**列懒加载**：
  `GET /catalog/{cid}/databases/{db}/{table}/columns`。
- 现有 `/datasources/{id}/tables` 返回体补 `table_type` / `schema_name` / `catalog`；
  支持 `?include_views=true`。
- 取数端点（`/playground/execute`、`/datasources/{id}/execute`、语义/看板刷新）请求体接受
  可选 `catalog`/`workspace`，由 §6.1 `resolve_execution_target` 决定实际网关。

---

## 9. 前端（Admin「表 & 字段」metadata 页，`App.tsx` 路由 `tables`）

- **四级可折叠目录树**：`Catalog → Datasource → Table/View（表/视图徽标+图标区分）→ Column`
  （列懒加载）。视图节点显式标 `VIEW` 并可展开其列。
- **SQL 编辑器（Playground）catalog 感知**：
  - 数据源选择升级为「catalog / datasource」二选一层级；选中外 catalog 即令 SQL 以
    `catalog.db.table` 补全；提供表/列自动补全（读 `/catalog/tree`）。
  - 执行前展示「解析目标 / 执行模式（联邦下推/单源/直连）」回执，透明化 §6.1 决策。
- 新增 `frontend/src/api/catalog.ts` 封装上述端点；沿用现有 `client`。
- 结果表格兼容重复列名（限定名 JOIN）：按 `columns` 顺序渲染，列名已消歧（后端保证）。

---

## 10. 分期实施计划（里程碑）

- **M0（地基，低风险，不含限定名执行）**：视图纳管 + `table_type`/`schema_name` 落库 +
  前端三级 `datasource → table/view → column`。→ 立刻交付「视图/表结构展示」。
- **M1（catalog 元数据层）**：启用 `adh_catalogs` + 派生默认 catalog + 回填 `catalog_id` +
  四级树 + `catalog_provisioner`（Doris CREATE CATALOG）+ 外部 catalog 元数据发现。
  catalog 仍**可不参与执行**（纯元数据）。
- **M2（catalog 进入物理引用·治理）**：`_extract_tables`→sqlglot；RLS/敏感/审计治理键升级
  `physical_ref`；Playground/出口接入 `resolve_execution_target`（先只做同网关内限定名）。
- **M3（联邦 JOIN 打通）**：复用 pushdown 跑通跨 catalog JOIN；编辑器展示执行模式；
  端到端联调（含 RLS 跨源不误漏）。
- **M4（可选·非 Doris 网关）**：DataFusion 多 catalog provider（形态 B），按需评估。

---

## 11. 迁移、兼容与回滚
- 全部为**加列可空/带默认**，同步幂等；旧两段/单段 SQL 零改写继续可用（§3.3）。
- 提供 `is_federation_gateway` 开关：未启用网关时，限定名查询**明确报错**而非静默降级。
- 回滚：M2/M3 的治理解析改造以「新解析失败回退旧正则 + 记录告警」双轨一个版本，验证无回归后
  再移除旧路径。
- 凭据：`remote_props`/`adh_catalogs` 口令加密；日志/审计**绝不落明文凭据或原始 SQL 敏感片段**。

## 12. 安全与权限影响（务必逐条落实）
1. **跨库同名串味**：治理键必须全限定（§4.5），否则 A 库策略误命中 B 库。
2. **联邦越权**：跨源查询只能访问「该用户被授权的 catalog/datasource」；
   `resolve_execution_target` 须校验 SQL 内**每个** catalog 的访问权，缺一即拒（fail-closed，
   延续护城河 I2/I3/I5）。
3. **网关信任**：网关 Doris 的账号需仅具备被授权 catalog 的最小权限；避免借网关越界。
4. **身份不变**：入口仍只信服务端 JWT/签名内部头，body 内 catalog/datasource 为「意图」非「授权」。
5. **可观测**：审计记录 execution_mode 与命中策略，联邦查询可追溯。

## 13. 风险与未决问题
- **RLS 跨源正确性**：pushdown 现有基名匹配必须升级为全限定（否则联邦下推行级过滤错位）——M2 硬门槛。
- **Doris 版本对多类型 catalog（hive/iceberg/es）支持差异**：需按目标 Doris 版本验证连接器矩阵。
- **凭据下发与连接池**：外部 catalog 口令轮换后需重建 catalog + 清池（参考既往 DataFusion 池
  `host:port:database` 不含凭据导致复用旧口令的坑）。
- **元数据规模**：外部 catalog 全库表列发现开销大，需增量 + 分批 + 可关。
- **未决**：是否需要物化视图/落表（`query_mode='materialized'`）作为联邦性能兜底？跨源写路径是否放开（本设计默认**只读联邦**）。

## 14. 验收与测试
- 离线单测（不碰真库）：sqlglot 限定名抽取、`resolve_execution_target` 路由决策、
  治理键全匹配、RLS 注入对 `cat.db.table` 正确包裹、`df_serialize` 限定名重复列消歧。
- 引擎单测：`pushdown.rs` 三段式识别/RLS 全限定匹配/跨 catalog JOIN AST 注入（补 §466 用例族）。
- 集成（真网关 Doris + 一个 jdbc catalog）：
  `select * from <cat>.<db>.<view> limit 50`（视图取数）、
  跨 catalog JOIN、无权限 catalog 被 fail-closed、RLS 在联邦下不越权、审计含 execution_mode。
- 前端：四级树渲染、视图徽标、编辑器补全与执行模式回执。

---
附：本文引用的现状证据行号（截至撰写）——
`session.rs:149`（固定 catalog `datafusion`）、`pushdown.rs:51-61,314,466`（三段式下推/基名匹配/联邦测试）、
`engine_client.py:99`（单 datasource）、`metadata_sync.py:81-88` 与
`datasource_service.py:716-741`（`BASE TABLE` 过滤，视图被排除）、
`enforcer.py:321-333`（裸名抽取）、`df_serialize.py`（重复列消歧，已修复）。
