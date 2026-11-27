---
trigger: always_on
description: 本体建模规范（Ontology Modeling Spec）。本体是唯一语义事实源；新增/修改本体对象、指标、维度、术语、别名、枚举或上云文档时的强制标准。与 security-guardrails.md 互补：本规则管"建模正确"，护栏管"取数安全"。
---

# 本体建模规范（Ontology Modeling Spec）— 强制规范

> 本体是 ChatBI 的唯一权威语义层（对标 Palantir Ontology）。以下任一条被违反即视为建模缺陷，必须修复而非妥协。
> 事实源代码：`backend/modules/catalog/services/ontology_service.py`（canonical JSON）、`backend/semantics/`（planner/binding/models/intent）。

## 1. 事实源与派生分层（单一可编辑源）
- **canonical JSON doc 是唯一可编辑事实源**（`adh_ontology_models.json_content`）；YAML、内部 md（`to_md`）、RDF 图谱、qmind 上云文档（`to_cloud_md`）、`adh_ontology_objects` 展开行**全部是派生视图**，严禁独立编辑派生物。
- 修改本体只有一条路径：编辑 doc → `save_draft`/`activate` → 服务层自动级联（重算 execution_binding → 重展开 objects → 重写 bindings → 重建图谱 → 同步知识库）。**禁止**绕过级联直接 UPDATE 派生表。
- 枚举的唯一可编辑源是**维度字典 `value_labels`**；本体 `properties[].enum` 经 `sync_enums_to_dimensions` 单向联动到字典，图谱 `owl:oneOf` 只是只读投影。不得在图/文档侧独立改枚举。
- **多站点适用性以 `adh_ontology_models.deploy_sites` 为唯一事实源**（模型级声明：`[{site_code, datasource(业务名)}]`，一次配置全对象共享），站点清单事实源 = `adh_sites`、数据源归属 = `adh_datasources.site_code`。**不得**回到逐对象逐站登记 `adh_ontology_bindings` 的旧口径（历史 27 对象 × 5 站 = 135 行机械复制），也不得在 doc/图谱/提示词里另写一份站点映射（`data-analysis/SKILL.md` 已去硬编码，由服务端「数据源拓扑」段注入，该段同时说明“默认分站呈现不合计”的口径）。某站 schema 真不同 → 为该站单独配一个本体或使用源本体，不在契约层开例外。
- **对象级站点例外以 `adh_ontology_models.object_site_rules` 为唯一事实源**（`{object_key: [site_code,...]}`）：「同构多站 + 个别站点才有某对象」时用它收紧——**未登记的对象 = 沿用模型级全部声明站点（默认免配，业务一多不必逐个配）**，登记过的对象只在所列站点可查。白名单**只能收紧不能扩权**（所列站点必须 ⊆ `deploy_sites`，保存与激活均校验；对象 key 不得悬空）；生效三处同源，不得只写其中一处：`binding_resolver` 取数硬门（路由改写与本站精确命中**两条路径都拦**）、`cross_site.list_deployments`（LLM 部署清单不再广告未部署站点）、`find_deploy_site_issues`（激活门禁）。**不得**为此场景回到逐对象逐站登记绑定行或在 doc/图谱/提示词里另写第二份对象×站点表。

## 2. 对象/属性建模要求
- 对象面向业务命名（订单/客户/案例），**不得直接照搬表名堆砌**；一个对象绑定一张 `primary_table`（宽对象或 SQL 模板承载复杂形状）。
- 属性（properties）**必须有业务中文名**：云端渲染与用户提示只暴露业务名，无业务名的属性不渲染（物理列名即泄露源）。
- `links[].join` 物理 JOIN 表达式仅存在于内部 md/图谱；**上云文档一律剔除**（`to_cloud_md` 白名单）。
- 对象命名/别名：`{key, display_name, aliases[]}`；别名维护原则见 FDE 规则（由失败数据驱动，不靠拍脑袋堆）。
- 数据源关联**以 `datasource_name` 为全局唯一标识**参与绑定解析（删除重建 id 会变，name 不变）；`datasource_id` 仅服务端使用，严禁进 LLM 视野或请求体。
- 一个对象在契约层**只登记一行形状描述**（物理表/`column_map`/`bind_kind`）；跨站取数由 `binding_resolver._route_by_site` 用模型 `deploy_sites` 展开并把生效源改写到请求站（同构假设）。路由目标与请求源不一致时**必须拒**（如拿 SLS 源查业务本体对象），不得静默拿别站/别库数据充数；改写后需同步刷新 `catalog_ref` 与产品身份（`product_ref` 清空重解析，产品是按站登记的）。

## 3. 指标/维度字典绑定纪律
- `adh_metrics` / `adh_dimensions` 生效行**必须**有 `bound_object_key` 指向本体对象 key；指标须含 `formula|formula_dsl|formula_dialects` 之一与 `agg_type`，维度须含 `target_column`。
- 字典行四元组语义：`name`（业务权威名）/ `name_en+aliases`（合法别名，解析按此命中）/ `value_labels`（枚举码→业务标签）/ `description`（口径文字）。**同一概念的别名只登记在字典/对象层，不复制到别处。**
- 指标口径（formula）可能含物理列名：仅限服务端使用；对 LLM 与云端**只回文字性口径说明**（见护栏 §7 与 `to_cloud_md`）。

## 4. 引用一致性（级联校验，宁阻断勿悬空）
- 触碰对象 key 的任何变更（重命名/删除/合并），必须保证字典 `bound_object_key` 不悬空：
  - `save_draft`（草案）→ `find_orphan_dict_bindings` 告警不阻断（返回 `validation_warnings`）；
  - `activate` → 孤儿引用**硬阻断**（ValueError），不得为"先激活再修"开口子。
- 定期巡检：`backend/scripts/check_alias_projection.py` 单向校验「对象 aliases（源）→ 图谱 skos:altLabel（投影）」一致性并报术语-字典重叠（明细跑 `backend/scripts/absorb_business_terms.py`），只报告不自动改。
- **任何级联失效语句不得触碰 `deploy_sites`**：它是模型级人工配置（不是派生表），与 `adh_ontology_bindings` 同属“多归属行”——历史事故是 `_persist_bindings` 按整个 model_id 废弃导致跨站部署被打废（见 common_pitfalls）。站点收敛/回退一律走 `backend/scripts/deploy_contract_bindings.py`（dry-run 默认，不同构行保留 active 并出例外清单，不静默删差异数据）。

## 5. 术语分层（business_terms 是缓冲区，不是第二事实源）
- `adh_business_terms` 只保留**尚未绑定到任何对象/字典的纯业务黑话**；一旦收编，其同义词应落对象/字典 aliases，术语行退役或改为溯源注释。
- 术语/同义词解析**必须按 datasource 分桶**（`terminology_manager.expand_synonyms(keywords, datasource_id)`）：ds>0 只加载本源+全局(ds=0)术语，杜绝跨数据源串味；ds=0 视为系统/全局调用加载全量。
- 新增术语消费方（检索、选表、解析）一律经 `terminology_manager` 缓存，不得各自 SELECT `adh_business_terms`。

## 6. 发布链路一致性（本体→图→云）
- 上云文档**只能**经 `ontology_service.to_cloud_md`（白名单渲染 + 版本戳头 `model-version-stamp`）由 `ontology_kb_sync` 推送；**严禁**直接把 `md_content`（含物理信息）上传。渲染→提取（`extract_object_keys`）、写戳→读戳（`_evaluate_doc_freshness`）均有跨层契约测试，改格式必须同步改两侧并跑契约测试。
- 同步成败必须落水位线表 `adh_ontology_kb_sync_state`（`_write_state`）；静默失败（只 warning）不允许——失败要可告警、可被 `reconcile()` 自愈。
- 知识库与字典的权威划分（写入 `prompt_composer.SEMANTIC_QUERY_RULES`，改动时不得删弱）：**名称以 `get_metrics` 实时目录为唯一权威；知识库只对口径解释/业务背景/模板 variables 权威；doc_stale=true 时 KB 内容仅供参考**。

## 7. 解析行为守则（确定性编译层）
- 名字解析优先级固定：字典精确名(name/name_en) → 字典别名 → `binding.column_map` → 物理列业务名(business_desc) → 物理列名兜底；每一档必须记录 `resolution_source` 入 provenance。
- **包含式模糊只回抛候选（`fuzzy_rejected` + `fuzzy_hint`），绝不静默绑定**——错数比无答案危害大（宁缺勿错）；被拒词自动落入候选真源 `adh_evolution_candidates`（旧 `adh_alias_suggestions` 仅冻结历史，处置统一走进化决策服务，见 FDE 规则）。
- 解析失败/未解析提示只允许出现业务名候选与字典清单，**不得含任何物理表/列名**（有 no_leak 用例把门）。

## 8. 建模回归门禁
触碰 `ontology_service.py` / `ontology_kb_sync.py` / `backend/semantics/{planner,models,intent,binding_resolver}.py` / `terminology_manager.py` 后必须跑：
- `venv/bin/python -m backend.eval.runner`（golden-question，**正确率不得回退**；`--suite retrieval` 另跑本体层检索评测）；
- `tests/test_cloud_md_redaction.py`、`tests/test_doc_freshness.py`、`tests/test_alias_suggestions.py`、`tests/test_terminology_scoping.py`、`tests/test_planner_resolution.py`；
- 涉及取数执行链（planner/semantic_query/gates）另按护栏 §11 跑护城河三件套。
- 触碰站点拓扑或 `binding_resolver` 的站点路由/`deploy_sites` 读写后，另跑 `tests/test_site_scope.py` + `tests/test_cross_site.py`（锁定：契约行只一行、跳站路由后产品身份随站刷新、未声明站点/路由目标不匹配均拒、未配置 deploy_sites 行为零变化、对象级白名单（未列对象=全部声明站点可用；列了=只这些站点可查，路由与本站精确命中两条路径都拒；越出声明站点/对象悬空均报错；`list_deployments` 与取数门同口径））。
新增建模/解析行为分支必须同步新增 eval 用例（尤其：别名命中档位、孤儿引用阻断、上云脱敏、模糊回抛）。
- 改本体/图谱**接口或权限绑定**后另跑 `tests/test_ontology_perm_binding.py`（见 §9）。
- 改系统能力本体的动作/规则/场景（`system_ontology_catalog.py`）后另跑 `tests/test_system_ontology_catalog.py`（目录自检 + 动作↔工具通道一致 + flow 串联 + 上云投影 + 图谱投影 + `system_capability_plan` 工具），见 §10。

## 9. 权限标识与能力可见性（能不能看/能不能改 = 角色是否持有权限码）

> 本体的每项能力与每个接口都由**权限码（perm_code）**把关，没有"登录了就能改"的通道：
> 能否使用，只看调用者所属角色是否持有对应码（`adh_role_perms`），且 **UI 显隐 / HTTP 裁决 / AI 把关三处必须同码**。
> 新增或改动本体能力时不补这一层，就是"看得见点不动"（点了必 403）或"看不见也能调"（权限形同虚设）。

### 9.1 真值源与生效点
- 三件真值源：`adh_perm_registry`（`perm_code` / `api_pattern` + `api_method` / `menu_key` / `ai_access` / `ai_action_key`）、`adh_role_perms`（角色持码）、`adh_menu_registry`（菜单显隐，按 `perm_code` 反查）。
- 四层生效点，缺一层即口径分裂：
  1. **菜单显隐**：`adh_menu_registry.perm_code` → `/roles/current/permissions` 下发 menus（规则见 `menu-permission-sync.md`）；
  2. **前端显隐**：`hasPerm('<码>')` 决定按钮/页签是否渲染——**不渲染点了必 403 的治理按钮**（样板：`GraphRebuildButton`）；
  3. **HTTP 裁决**：`backend/common/api_permission` 中间件按角色持码展开 `api_pattern`（逗号分隔多值、方法精确匹配；`fnmatch` 的 `*` **跨 `/`**；无匹配即 403；admin 旁路）；
  4. **AI 侧**：`require_write_perm(user_id, ws, '<码>', '<动作>')` 在 AS-BOT 写动作执行前把关，并把该码登记进 `perm_link.PERM_GATED_ACTION_PERMS`（否则角色页把它误报"未接 AI"）。

### 9.2 能力 ↔ 权限码（当前落地；绑定迁移：`docker/mysql/ontology_perm_binding_migration.sql` + `docker/mysql/capability_perm_gap_migration.sql`）
| 能力 | 权限码 | 接口落点 |
|---|---|---|
| 查看本体/对象/草案/版本/同步状态/生成任务/KB 候选 | `ontology:read` | GET `/api/catalog/ontology/*`（全前缀） |
| 保存 canonical doc（对象增删改、站点白名单、挂 KB） | `ontology:save` | PUT `/api/catalog/ontology/models*` |
| 发布草案（级联生效：objects→bindings→图→KB） | `ontology:publish` | POST `/models/*/publish` |
| 激活 / 归档 / 恢复 | `ontology:activate` | POST `/models/*/{activate,archive,restore}` |
| 生成本体草案、取消生成任务 | `ontology:generate` | POST `/generate*` |
| 导入 YAML | `ontology:import` | POST `/import-yaml` |
| 重建命名图（系统图 ds:-1 / 聚合图 ds:0 / 本源图 ds:N） | `graph:manage` | POST `/api/graph/{sync,rebuild}` |
| 原生 SPARQL / 最短路径（`/query` 可执 update） | `graph:manage` | POST `/api/graph/{query,shortest-path}` |
| 读图（节点/统计/检索/邻接） | `graph:read` | GET `/api/graph*` |
| 同步模型到知识库、挂接知识库 | `kb:manage` | POST `/models/*/kb-sync`、`PUT /models/*/kb`、`/api/knowledge-bases*` |
| 网页侧新增对象 | 走 `ontology:save`（无独立 POST `/objects`） | — |
| 新增本体对象（**仅 AI 通道**） | `ontology:create` | `create_ontology_object`（HTTP 无落点） |
| 标签清单/分类/取值（读写） | `tags:read` / `tags:manage` | GET `/api/tags*` / POST,PUT,DELETE `/api/tags*` |
| 已保存查询/查询数据集（读/改删） | `playground:read` / `playground:manage` | GET `/api/playground/queries*` / PUT,DELETE 同前缀（POST 建归 `playground:execute`） |
| 手工登记血缘、SQL 血缘解析 | `lineage:manage` | POST `/api/lineage/{nodes,edges,parse-sql}` |
| 集成应用（AK 嵌入）读/写 | `embed:read` / `embed:manage` | GET `/api/embed/applications*,/api/embed/logs` / POST,PUT,DELETE `/api/embed/applications*` |
| 定时任务/同步任务启停 | `scheduled:manage` / `sync:manage` | PATCH `/api/scheduled-tasks/tasks/*/toggle`、PATCH `/api/sync/tasks/*/toggle` |
| 站点清单 / 可查站点 | `datasource:read`、`datasource:manage`（清单）/ `chat:use`（`authorized-sites` 派生视图） | GET/POST/DELETE `/api/datasources/sites*`、GET `/api/datasources/authorized-sites` |
| 个人资产（列表/下载/归档） | **无域码**：中间件 `_SELF_ROUTE_PATTERNS` 按属主放行；菜单显隐用 `asset:read` | `/api/assets*` |

### 9.3 纪律（新增/改本体能力必须同批完成）
1. `api_pattern` / `api_method` 登记：子路径用**字面量后缀**锚定（如 `.../models/*/publish`），**禁用 `/models*` 这类宽模式兜**——一行的方法集是跨 pattern 求笛卡尔积，宽模式会把同前缀的其它动作一起放行；
2. `menu_key` 归属（读码归消费菜单、写码归管理菜单，同菜单多码靠 `perm_registry.menu_key` 并集反查）；
3. 前端 `hasPerm` 同码显隐；
4. 给 AI 的写动作补 `require_write_perm` + `PERM_GATED_ACTION_PERMS`，并核对 `ai_access` 级别；
5. 绑定变更走**幂等迁移**（`docker/mysql/*.sql`，UPDATE 带条件）并**应用到当前库**核验（改完等 60s 中间件缓存过期或用 `invalidate_perm_cache()`）。

### 9.4 已知口径风险与例外（诚实记账，收敛前不得当成"已隔离"）
- **`catalog:read` / `catalog:manage` 仍按 `/api/catalog*` 覆盖整个 `/api/catalog/ontology/*`** → 收回 `ontology:*` 码并不等于收回能力（中间件按并集放行）。当前只有 admin/analyst 同时持有两组码，尚无角色差异暴露；在新建"只读本体"角色前必须先把 catalog 码收窄（同时给 `/api/catalog/data-products*` 独立码，否则会连带打挂数据产品）。
- **`DELETE /models/{id}` 与 `DELETE /models/{id}/draft` 只由 `catalog:manage` 写域把关**（本体码无 DELETE 落点；丢弃草案端点内另要求 `ontology:save`，构成双码交集）。收敛方向：给删除动作单独码，或改成受本体写码覆盖的子路径。
- **`POST /api/graph/{nodes,relations}`（裸三元组写入）无本体域码**，目前 admin 专用；面向用户的 SPARQL 视图走 `/graph/query`（归 `graph:manage`）。

### 9.5 回归
- `tests/test_ontology_perm_binding.py` 锁四件事：每个本体/图谱路由都被本体域码覆盖（例外集合只能少不能多）；`publish` 只归 `ontology:publish`；`kb-sync` 归 `kb:manage`；本体前缀下全部 GET 归 `ontology:read`。改绑定先改 `docker/mysql/ontology_perm_binding_migration.sql` 再跑此用例。
- `tests/test_system_ontology_catalog.py` 锁系统能力本体这一层：`permission` 必须投影 `api_pattern`/`api_method`；**每个对象**都必须在 `CAPABILITY_PERM_CODES` 或 `CAPABILITY_NO_OWN_CODE` 里声明，未声明即红；绑定会以 `requires_permission` 关系进 canonical doc；本体点名/规则点名的码必须在 `adh_perm_registry` 真实生效（库不可用时 skip）。
- `tests/test_capability_perm_bindings.py` 锁「有页面/有接口就有码」：此前无码的 16 条路由（标签读、已保存查询读写、血缘写、集成应用、两个 PATCH 启停）必须仍被码覆盖；新码的 pattern/method 不得漂移；新码必须已授权给持有同域码的既有角色；个人自助面不得再套域码；「无码 = 仅管理员」规则不得回引已补码路径。

### 9.6 系统能力本体是权限口径的 LLM 可读层（动态本体的「更全面」就落在这里）

> `adh_perm_registry` 里的绑定对模型是黑盒：AS-BOT 只能看到系统本体与知识库。
> 不让本体携带「能力 → 码 → 接口」，模型就只能猜「这个功能谁都能用」，比不知道更危险。

**单一事实源与镜像关系**
- 结构化声明：`backend/modules/catalog/services/system_ontology_catalog.py` 的 `CAPABILITY_PERM_CODES`（能力 → 码）与 `CAPABILITY_NO_OWN_CODE`（无码能力 + 原因）。
- 接口绑定：`permission` 对象的 `api_pattern` / `api_method` 是 `adh_perm_registry` 同名列的投影（只读，不在本体侧编辑）。
- 口径矩阵：本文 §9.2 是人工可读版；三处（catalog 表 / 迁移 SQL / §9.2）**同批修改**。

**落地纪律**
1. 新增能力对象必须同批声明权限码；确实暂无码的，进 `CAPABILITY_NO_OWN_CODE` 并写清**真实原因**（个人自助属主校验 / 路由层 `require_admin` 把门），**禁止省略**，也禁止笼统写「仅管理员可达」——它会让人误以为补码就能开放。
2. 规则（M3）里凡声明某能力由某码把关，该码必须**写进 `statement`**——上云投影（`to_cloud_md`）只渲染 statement/source，`enforcement` 不上云，码只放 enforcement 等于没告诉模型。
3. 绑定进 doc 用关系 `requires_permission`（与菜单的 `gated_by` 区分：后者是「菜单挂载权限点」）；每个对象恰好一条。
4. 改完必须重跑 `./venv/bin/python -m backend.scripts.refresh_system_ontology --apply`（走 `save_draft → 级联 → 图谱 → KB 同步`），并在库里核 `object_count` 与 `adh_ontology_objects` 行数一致；只改 py 不落库 = 本体继续教旧口径。

**当前无码能力（诚实记账，收敛前不得当「已隔离」）**

2026-11-20 按 openapi 全路由 × `adh_perm_registry` 逐条 fnmatch 实探，把「无码」分成三类，不得混为一谈：

1. **已补码（本文 §9.2 已登记）**：标签读 `tags:read`、已保存查询写 `playground:manage`（`playground:read` 补 GET pattern）、血缘写 `lineage:manage`、集成应用 `embed:read`/`embed:manage`、定时/同步启停补 PATCH。迁移与角色默认授权见 `capability_perm_gap_migration.sql`。
2. **个人自助（不该补码）**：`/api/assets*`、`/api/release-notes*` 读、`/api/memory*`、`/api/workspace-assets/*`、`/api/chat/session-file*` —— 中间件 `_SELF_ROUTE_PATTERNS` + 路由层属主校验，域码会构成第二套裁决（规则 `perm_self_service_owner_only`）。
3. **路由层 `require_admin` 把门（补码也不放行）**：`/api/admin/terms*`、`/api/admin/metadata*`、`/api/admin/table-info*`、`/api/admin/relations*` 写面、`/api/admin/sync*`、`/api/admin/execution-layers*`、`/api/admin/cache*`、`/api/admin/menu-tree*` 写面、`/api/admin/brand` 写面、`/api/admin/release-notes*`、`/api/task-monitor*`、`/api/model-train*`、`POST /api/graph/{nodes,relations}`（另见 §9.4）。

**收敛方向**：要开放第 3 类，先改路由依赖（`require_admin` → 按码裁决）**再**补码并同批授权，顺序反了就是空承诺（角色页能勾、接口仍 403）。第 2 类不要补码。第 1 类改口径必须三处同批（catalog 表 / 迁移 SQL / §9.2）。

## 10. 任务流（`scenarios[].flow`）：行为/规则/场景的串联层

> 三张平铺清单（`objects[].actions[]` / `rules[]` / `scenarios[]`）只能回答「平台有什么」，
> 回答不了「这个任务按什么顺序做、上一步产出喂给谁、哪步 AI 能代做」。
> `scenarios[].flow` 就是补齐这一层的**声明式编排**——它仍然是建模描述层，**不是新的执行裁决**。

### 10.1 结构
- `flow` = 有序步骤数组，每步：`order`（1..N 连续）、`action`（必须是本场景 `uses_actions` 里的动作 key，且该动作挂在某个对象的 `actions[]` 上）、`purpose`（用户要看到的这步在做什么，必填）、`produces` / `consumes`（人话产出物名）、`gated_by`（必须是本场景 `uses_rules` 里的规则 key）。
- **步骤不手写 `perm_code` / `channel` / `action_label`**：三项一律由 `_attach_flow_bindings` 从 `ACTION_PERM_CODES` 与对象动作表派生。手写会形成第二套清单，与真实工具漂移。
- 无步骤的问答型场景**不写 `flow`**（不要写空数组）；`system_capability_plan` 对它回 `executable=false` 并明确「不得自行编造步骤顺序」。

### 10.2 单一真值源：`ACTION_PERM_CODES`
- `{action_key: {code, channel}}`，覆盖 `objects[].actions[]` 的全部动作（`_action_binding_errors` 双向锁：多一个/少一个都判红）。
- `channel` 只有两值：`ai`（AS-BOT 有真实工具可直调：功能动作在 `function_tools.FUNCTION_ACTION_SPECS`，本体动作在 `ontology_tools` 的 `_require_write_perm`）/ `human`（只有人能做的界面或 HTTP 端点）。**标 ai 必须真有工具，标 human 必须真没工具**（`test_ai_channel_actions_have_real_tooling` 锁）。

### 10.3 串联校验（`_flow_errors`，宁阻断勿悬空）
- 步序 1..N 连续；`action` ∈ `uses_actions`；`gated_by` ∈ `uses_rules`；
- **`consumes` 必须由更早步骤的 `produces` 提供**——这是「串联」与「平铺」的实质区别，断了必须报错而不是静默。

### 10.4 别名（`scenarios[].aliases`）
- 场景必须有业务别名（如 `看板发布` / `发布看板`），因为模型和用户都是拿口语名问的；别名随 `to_cloud_md` 上云（`（又称：…）`），否则知识库教不会。
- 别名与 `key`/`title` 归一后**不得跨场景撞车**（`normalize_scenario_name` + `_scenario_alias_errors`）；归一函数只此一份，`system_tools._pick_scenario` 复用，不得另写。

### 10.5 四个消费面必须同批接通
| 消费面 | 位置 | 要求 |
|---|---|---|
| 上云投影 | `ontology_service._cloud_flow_md` | 渲染「执行步骤（按序）」+ `AI 可直接执行`/`需人工到界面完成` + 权限码 + 依赖上一步产出 + 受规则约束；params_schema/enforcement 不上云 |
| 图谱 | `common/rdf/ontology_to_rdf.py:_emit_flows` + `graph_service` | `adh:TaskFlow`/`adh:FlowStep` + `flowOf/stepOfFlow/stepAction/stepChannel/stepPerm/stepProduces/stepConsumes/stepGatedBy/nextStep`；`sem_types`/关系中文名同批登记；步骤节点不得成为 SQL 关系端点 |
| AS-BOT 工具 | `sdk_tools/system_tools.py:system_capability_plan` | 只读计划（admin-only），每步回 `you_can_run`/`why_not`（channel=human 恒 false；ai 按持码 + `perm_link.effective_level`）；它是**描述**，真调用仍由 `require_write_perm` fail-closed 裁决 |
| 提示词 | `prompt_composer.CAPABILITY_PLAN_RULES` | 多步任务先取计划；`channel=human` 只能交给人；`you_can_run=false` 不得声称已完成。**只在本会话注册了 `system_capability_plan` 时注入**（`has_capability_plan`）：它属 `system` 组，与 `screen` 组是两套授权，写进 `SCREEN_DIRECT_RULES` 会出现「有 screen 没 system」时教模型调不存在的工具（空承诺） |

### 10.6 执行边界：flow 默认是声明式计划，自动执行是显式受控例外
- **默认（plan）**：`flow` 只是声明式计划（`execution.mode=plan` 或缺省）。执行仍逐步由 模型显式调用、逐步过权限码。
- **受控自动执行（auto）**仅当以下三条**同时**成立才可执行，缺一即拒（fail-loud，回 plan 语义）：
  1. 场景显式声明 `execution: {mode: "auto", enabled: true, input_schema, allowlist}`；
  2. 场景 key 命中服务端白名单（`CAPABILITY_FLOW_SCENARIOS`，当前且唯一试点：`ops_content_publish`）；
  3. 特性开关 `CAPABILITY_FLOW_AUTO_ENABLED=true`（默认 false）。
- 自动执行走 P2 运行时逐动作契约执行：每步权限码再裁、人工步骤停在 `waiting_human`（AI 不得代做）、绑定表达式（`input_bindings`/`output_bindings`）是机器执行的唯一依据（`consumes`/`produces` 只是可读说明）。

### 10.7 反模式
- ❌ flow 里手写 perm_code/channel（两套清单必然漂移）；
- ❌ 步骤 `consumes` 一个没有任何前置 `produces` 的东西（串联是假的）；
- ❌ 动作标 `ai` 但既不在功能工具、本体工具里也没有该码的写码把关（模型会去调不存在的工具）；
- ❌ 别名不登记、不回归一就上云（模型拿口语名取不到计划，联动入口等于不存在）；
- ❌ 未显式声明 `execution.mode=auto` 却在服务端按 `order` 自动连跑动作（默认就是声明式计划）；
- ❌ 开关关闭/白名单未命中时仍执行自动流程，或对人工步骤自动执行、跳过 `waiting_human`。
