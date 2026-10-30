---
trigger: always_on
description: AI-DataHub 数据与安全护栏铁律（数据护城河）。任何取数、权限、SQL 执行、LLM 工具产物、可观测改动务必遵循；后续迭代不得绕过。
---

# 数据安全护栏（数据护城河）— 强制规范

> 本项目**不是数据库连接工具**。任何能返回数据行的路径都必须"过护城河"。
> 违反以下任一条即视为安全缺陷，必须修复而非妥协。改错代价：敏感数据泄露 / RLS 旁路 / 越权。

## 1. 统一取数入口（不可旁路）
- **`backend.semantics.execute` 是全平台唯一取数执行口**（Phase 6.4 起，语义/nl2sql 双轨执行就此统一）：声明式意图走 `execute()` / `execute_plan()`，SQL 形状走 `execute_sql()`；内部统一经 `backend/core/query_executor.py: execute_query_with_permission`（permission_enforcer 敏感/RLS/RBAC + 审计）。
- 任何返回数据行的执行**必须**经该执行口（或其既有封装 `backend/core/governed_query.py: governed_execute`——内部已收口 `execute_sql`）。
- **禁止**新增任何直连数据源、绕过 `permission_enforcer` 的裸执行通道；**禁止**新增"返回数据行但不经治理"的 `/execute` 类端点。
- 语义层 `run_semantic_query` 与 nl2sql `execute_sql` 是**同一执行口的两条入口**（都过 `semantics.execute`），不是"一条合规一条旁路"；判合不合规只看**是否过治理入口**，不看走的是语义层还是 SQL。
- SQL Playground / 看板 / 报表 / 组件的 raw_sql 取数一律走治理入口，与主链路同源（`execute_via_playground`、`governed_execute`）。

## 2. 身份 fail-closed
- 身份**只信服务端**：JWT / 嵌入 AK / 报表创建者，由服务端解析后传入；**绝不**从请求体 `body.user_id` 等不可信字段取值。
- **无可信身份 → 拒绝取数**（`NoIdentityError`），不得回退到无过滤裸执行（I3/I5）。
- 系统/内部调用（`user_id=0`）走 `sensitive_only`：**只套治理敏感基线，不做 RBAC/RLS**，但敏感屏蔽基线仍强制生效。

## 3. 权限模型顺序与"只增不减"原则（`enforcer.check_access`）
生效顺序，缺一不可：
1. **敏感字段基线**（datagov `adh_sensitive_fields`）—— `block`→剔除、`mask`(full/partial/hash)→脱敏，**对所有人生效含 admin**；
2. `sensitive_only`（无身份）到此返回；
3. admin 旁路（**敏感脱敏除外**）；
4. 数据源 / 表 RBAC；
5. 列级限制（hidden/masked）；
6. RLS 行级过滤 `row_filter`。
- **硬约束**：角色策略、RLS 列策略**永远不得弱化或覆盖**敏感基线（不得把已 block 的列放行、不得改已 mask 的方式）。合并时只能追加 hidden、只能对非敏感列追加 mask。

## 4. RLS 注入正确性（`enforcer._inject_row_filter`）
- 行过滤以**包裹子查询**方式注入：`FROM t` → `FROM (SELECT * FROM t WHERE <filter>) AS t`。
- **必须保留原表别名**：`FROM t t1` → `... ) AS t1`，**严禁**生成非法双重别名 `AS t t1`。
- **必须同时覆盖 FROM 与 JOIN 两侧所有该表引用**，杜绝关联查询对 JOIN 侧的 RLS 旁路。
- 下一词属于 `_SQL_RESERVED_AFTER_TABLE`（关键字）时视为无别名，不得误当别名。

## 5. SQL 校验（`validate_sql`）
- 仅允许 `SELECT` / `WITH`(CTE)；**禁** DDL（CREATE/ALTER/DROP/TRUNCATE/RENAME/GRANT/REVOKE）、DML（INSERT/UPDATE/DELETE/REPLACE）、多语句（引号/反引号/注释外的分号）。
- 默认要求 `LIMIT`；确有需要（count 包裹、Playground 内部已另行补限流）才可传 `require_limit=False`，但**不得**用于绕过安全关键字与多语句校验。
- 缺 LIMIT 的扫描应"自动补默认 LIMIT 后再校验"，而非直接放行（见 `sdk_tools/query_tools.py`）。

## 6. Chat / Agent 取数：语义层与 nl2sql 双路径（均须受治理）
> 只要满足本护栏全部安全要求（§1–§5、§7–§10），**语义层与 nl2sql 都是允许的取数路径**；
> 二者不是"合规 vs 裸连"的关系，只是**权限施加机制**不同。走哪条由 **Waker 职责**划分，不是代码硬禁。
- **语义层 `run_semantic_query`（主路）**：LLM 只产**声明式意图** `{object, metrics[], dimensions[], filters[], order[], limit, time_window…}`，
  经 intent→binding→plan→**七闸门**（identity / permission / preflight / proposal / approval / execute / audit，`backend/semantics/gates.py`）执行；
  **权限由语义层自身控制**（`permission_token` 校验 + RLS sqlglot 改写 + 审计）。
- **nl2sql `execute_sql`（受治理旁路）**：只读 `SELECT`/`WITH`，**必须**经 `execute_query_with_permission` → `permission_enforcer.enforce_sql`（敏感基线/RBAC/列级）改写后才执行，
  **行级权限识别由语义执行引擎（`semantics.execution`：enforce_sql 改写 + RLS 策略二次注入纵深）在执行前施加**，成功/拒绝均落审计。用于语义层无法表达的形状（多表 JOIN、窗口函数、未建模对象）。
  **红线**：严禁把 LLM 生成的原始 SQL 直接下发数据源执行；`enforce_sql` 抛 `PermissionError` 必须在到达数据源**之前**中止（`execute_query_with_permission` 已 `raise`），不得回退到未审核的 `execute_query`/`get_connection` 直连。
- **`check_sql`（nl2sql 执行前的治理预检，必用）**：`execute_sql` 前先调 `check_sql`（`sdk_tools/query_tools.py`），不执行、不返回数据行，校验安全（只读/DDL-DML/多语句/LIMIT/huge 全扫）+ 逐表 `check_access` 权限预览（表级/列隐藏脱敏/行级 RLS），返回 `verdict=ok/warn/blocked`；`blocked` 不得再执行。它是纵深防御与可诊断提示，**不替代** `execute_sql` 自身的强制审核。
- **路径选择归 Waker**：语义 Waker 只勾 `semantic` 组，nl2sql Waker 显式勾 `query` 组（`waker.tools.mcp` 逐工具授权）；未勾选即不注册、LLM 无从调用。AS-BOT 系统 Waker 仍禁 `query`（域边界，见 `as-bot-system-waker.md` §2）。
- **两条路径不得互串**：语义 intent 中任何 `sql/raw_sql/statement` 字段在 `parse_intent` 直接**拒收**（语义层不吃裸 SQL）；`execute_sql` 是独立工具，不是把 SQL 塞进语义 intent。
- 本体仍是已建模对象的权威入口：nl2sql **不得**用于绕过本体去查已治理对象（那是定位漂移），只补语义层覆盖不到的查询形状。

## 7. 数据源黑盒脱敏（对 LLM 与前端）
- 取数结果**严禁**向 LLM / 普通客户端响应泄露：生成的 SQL（base_sql/secured_sql）、`datasource_id`、`catalog_ref`、`physical_table`、provenance、账号/主机、原始报错栈。
- **本条的「IP」指基础设施地址**（数据源 host、内网/公网主机 IP、`host:port`），**不是**业务实体携带的 IP（如客户端 IP、访问者 IP）。
  后者属**业务数据内容**，能不能下发**唯一由数据权限裁决**：`adh_sensitive_fields`（全局 `datasource_id=0`/`table_name=''` ∪ 指定数据源/表，按物理列名），对所有人生效含 admin。
  两个动作的**实际生效点**不同，不得凭印象当“已治理”：
  - `block`：语义路在 `gates._blocked_column_guard`（intent 点名 + 编译 SQL 文本兜底）硬拒，裸 SQL 路由 `enforcer` 剔除列 —— 两条路均落地；
  - `mask`：**必须在编译期写进下推 SQL** —— `planner.plan(..., column_policies)` 由 `rls.column_policies`
    （= enforcer 敏感基线 ∪ RLS 列策略）取策略，在**维度投影**上包 mask 表达式，**SELECT 与 GROUP BY 用同一份**
    （否则“按原值分组、只把展示值打星”会把 distinct 个数泄露出去）；聚合指标不包（`COUNT(DISTINCT ip)` 输出的是计数不是原值）。
    结果侧 `query_executor.apply_post_processing` **按 DataFrame 列名**匹配，只能作为纵深兜底，
    **不得当语义主路的执行手段**：语义投影别名是业务名/SLS ASCII 槽位 `o0/o1…`（`planner.output_header_map`），
    列名与物理列名对不上 → 静默不脱敏（历史缺陷，2026-10-30 已改为编译期注入）。
    策略加载失败时 planner 返回空计划 + 原因（fail-loud），绝不编译未脱敏 SQL。
  - **裸 SQL 路（`execute_sql` / Playground / 看板 raw_sql）同样必须改写进待执行 SQL**：
    `enforcer._restrict_columns_in_sql` 在 `enforce_sql` 里调 `rls.apply_column_restriction`，
    让明文不出数据源；结果侧处理退为兜底，仅在 AST 改写不可用（**渲染不保真** / `SELECT *` 拿不到列清单 /
    sqlglot 缺失）时使用，且必须在 `policies_applied` 里标 `column_restriction:result_side_fallback(原因)`
    供审计区分，不得静默。已落 SQL 的输出列要从 `masked_columns` 里移除，避免二次脱敏静默改数。
    AST 改写前必过 `rls.renderer_faithful`（渲染保真门：原样重渲染 ≡ 原 SQL），Playground 的
    `rls-diff` 展示同口径，保证“所见即所执行”。
  - mask 表达式**只在 `semantics/security/masking.mask_expr` 一处生成**（按方言族 + 引擎分支，策略层不携带 SQL 片段）；
    SLS 的函数写法以实机探针为凭（`backend/scripts/probe_sls_mask_functions.py`：`SUBSTR(x,-2)` 不报错但返回整串
    → 取尾必须 `RIGHT`；`to_hex/sha2/to_utf8` 在本端点不存在 → hash 用 `MD5`）。
    **不得**对探针 tuned 的语义 SQL 做整句 AST 重渲染（`now()` 会被改写成 `CURRENT_TIMESTAMP` 等同义但 SLS 不认的形式）。
- **禁止在出站层（`modules/mind/execution/outbound_guard.py`）hardcode 业务数据的内容形态规则**（IP 字面量、手机号、邮箱等正则）：
  它不读权限配置，会把已治理维度整体拒发，且管理员在「敏感数据」页查不到任何配置依据（代码常量不是规则）。
  历史缺陷（2026-10-30 已移除）：`ipv4_literal` 值正则 + 裸 `ip` 键名判定叠加，使「客户端IP」维度无论怎么配 mask 都出不来。
  出站守卫只管**配置面/内部标识**泄露：凭据、连接串、`host`/`ip`/`port`/`endpoint` 等**完整键名**、生成 SQL、报错栈、`datasource_id`/`catalog_ref`/`physical_table`/provenance。
  键名判定必须按完整键名取 Unicode 词边界（`\w`）：中文业务标签 `"客户端IP":` **不得**被当成内部键 `ip` 命中。
  业务敏感列未登记 = 治理缺口，走「敏感数据」页/`sensitive_suggest` 扫描补登记，不是往出站层加正则。
- **明确例外**：用户确认的 AS-BOT 仪表盘设计面板，经服务端 `playground:execute` 与目标编辑权校验，可通过专用 REST 查看/人工编辑业务查询 SQL；修改后必须重新安全校验、治理预览和确认发布。SQL 不进入 Agent、SSE、聊天历史或普通图表响应，权限改写 SQL 与连接凭据仍不展示。
- 执行阶段失败：原始细节**仅进服务端日志**，对外回通用文案 `_EXEC_FAIL_HINT`；告警经 `_safe_warnings` 按 `_LEAK_KEYWORDS` 过滤。
- 相对时间用 `time_window`（`7d/24h/2w/1M`），不得让 LLM 手算绝对日期。

## 8. 结果序列化无损
- JOIN `t1.*, t2.*` 等产生的**重复列名**必须经 `backend/common/df_serialize.py: df_to_columns_rows` 无损消歧（`col__1…`），再序列化。
- **禁止**直接 `df.to_json(orient="records")`（重名列抛 `ValueError` → 500）或用 `try/except` 把失败吞成 `rows=[]`。

## 9. 审计与可观测不得旁路 / 不得影响主链路
- 成功与拒绝均落审计（`_log_permission_audit`）；仅真实用户调用落审计行；`policy_id` 数字入数值列、字符串标记入 `policy_name`，不得类型错配。
- 可观测（`backend/observability`）**未开启即全链路 no-op、绝不抛出**；span 文本按 `OBSERVABILITY_SPAN_MAX_CHARS` 截断，**不得**把敏感 SQL/PII 无上限写入。
- 深层埋点跨线程（`run_in_executor`）须 `contextvars.copy_context().run` 传播 recorder，否则用量静默丢失。

## 10. 元数据与视图一致性
- 表结构元数据按 `catalog → datasource → table/view → column` 组织；视图不得被 `TABLE_TYPE='BASE TABLE'` 过滤系统性丢弃（同步/在线浏览/引擎登记三处需一致放开）。
- 引入 catalog 三段式限定名时，须同步升级 `_extract_tables`（现仅 `\b(?:FROM|JOIN)\s+(\w+)`，不识别 `db.table`/`catalog.db.table`）与敏感/RLS 的匹配键（现按裸表名），否则跨库同名会串味/误过滤。

## 11. 改动这类文件时的硬性回归
- 触碰 `enforcer.py` / `query_executor.py` / `governed_query.py` / `playground.py` / `semantic_query.py` / `df_serialize.py` 后，**必须**跑：
  `tests/test_data_moat_enforcement.py`、`tests/test_permission_enforcer.py`（含 `TestColumnRestrictionInSql`：
  列级策略落 SQL、别名不二次脱敏、不引用受控列零改写、兜底路径可审计）、`tests/test_permission_e2e.py`，
  并为新分支补用例（尤其：带别名 JOIN 的 RLS、重名列、无可信身份拒绝、敏感 block 对 admin 生效）。
- 触碰 `sdk_tools/query_tools.py`（`execute_sql` / `check_sql`）后，**必须**跑 `tests/test_execute_sql_governance.py`：它锁定 execute_sql 只经 `execute_query_with_permission`、权限拒绝在执行前即中止、以及源码级禁止 `get_connection`/裸 `execute_query` 旁路。新增取数工具不得绕过该门禁。
- 触碰 `modules/mind/execution/outbound_guard.py`（出站收口）后，**必须**跑 `tests/test_function_ai_access.py`（含：基础设施键 `ip`/`host` 仍拦、中文业务维度名不误伤、值形态规则清单里不得重现业务内容正则）+ `tests/test_as_bot_security.py`（`test_tool_error_branches_all_pass_outbound_guard` 源码级接线）。
- 触碰 `semantics/{planner,rls}.py` 或 `semantics/security/masking.py`（列级安全策略编译进 SQL）后，**必须**跑
  `tests/test_planner_sls_engine.py`（`TestColumnPolicyCompiledIntoSlsSql`：投影+GROUP BY 同口径、聚合不受影响、
  策略源为数据权限、加载失败拒编译、探针 tuned 的谓词不被重写）+ `tests/test_semantic_security.py`（mask 表达式按方言/引擎）+ 护城河三件套。
- 服务以 uvicorn **无 `--reload`** 常驻（Phase 4 起 **web 单进程** `backend/app/main.py` 单进程绑定 8001-8007/8012 全部契约端口）：接口/权限代码改动后重启 web 即全局生效（`./stop-all.sh web && ./start-all.sh -d`），另有 celery-worker/beat（dataengine 已退役）；数据源配置来自 `.env`（唯一来源 `backend/.env`，见 `backend/common/config.py`）。

## 12. 禁止的反模式（速查）
- ❌ 为图方便直连数据源返回数据；❌ 从请求体读用户身份；❌ 让角色/RLS 弱化敏感基线；
- ❌ 给 LLM 开放**不经 `execute_query_with_permission` 治理**的裸 SQL 旁路（经统一治理入口的 `execute_sql` 不在此列）；
- ❌ 用 nl2sql 绕过本体去查已治理建模对象；❌ 把裸 SQL 塞进语义 intent（`parse_intent` 必拒）；
- ❌ 把 `execute_sql` 生成的原始 SQL 直连数据源（必过 `execute_query_with_permission`，审核不过即中止）；❌ 跳过 `check_sql` 预检直接 `execute_sql`；
- ❌ 对外回显 SQL/数据源/账号/基础设施 IP/报错栈；❌ 把业务敏感列的裁决 hardcode 在出站层（内容形态正则）而不去配 `adh_sensitive_fields`；❌ `to_json` 裸序列化或吞异常致空结果；❌ 观测/审计改动抛异常影响主链路。
