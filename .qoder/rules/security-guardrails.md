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
- 取数结果**严禁**向 LLM / 普通客户端响应泄露：生成的 SQL（base_sql/secured_sql）、`datasource_id`、`catalog_ref`、`physical_table`、provenance、账号/IP/主机、原始报错栈。
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
  `tests/test_data_moat_enforcement.py`、`tests/test_permission_enforcer.py`、`tests/test_permission_e2e.py`，并为新分支补用例（尤其：带别名 JOIN 的 RLS、重名列、无可信身份拒绝、敏感 block 对 admin 生效）。
- 触碰 `sdk_tools/query_tools.py`（`execute_sql` / `check_sql`）后，**必须**跑 `tests/test_execute_sql_governance.py`：它锁定 execute_sql 只经 `execute_query_with_permission`、权限拒绝在执行前即中止、以及源码级禁止 `get_connection`/裸 `execute_query` 旁路。新增取数工具不得绕过该门禁。
- 服务以 uvicorn **无 `--reload`** 常驻（Phase 4 起 **web 单进程** `backend/processes/main.py` 单进程绑定 8001-8007/8012 全部契约端口）：接口/权限代码改动后重启 web 即全局生效（`./stop-all.sh web && ./start-all.sh -d`），另有 celery-worker/beat（dataengine 已退役）；数据源配置来自 `.env`（`services/.env` 优先、`backend/.env` 兜底，见 `backend/common/config.py`）。

## 12. 禁止的反模式（速查）
- ❌ 为图方便直连数据源返回数据；❌ 从请求体读用户身份；❌ 让角色/RLS 弱化敏感基线；
- ❌ 给 LLM 开放**不经 `execute_query_with_permission` 治理**的裸 SQL 旁路（经统一治理入口的 `execute_sql` 不在此列）；
- ❌ 用 nl2sql 绕过本体去查已治理建模对象；❌ 把裸 SQL 塞进语义 intent（`parse_intent` 必拒）；
- ❌ 把 `execute_sql` 生成的原始 SQL 直连数据源（必过 `execute_query_with_permission`，审核不过即中止）；❌ 跳过 `check_sql` 预检直接 `execute_sql`；
- ❌ 对外回显 SQL/数据源/账号/IP/报错栈；❌ `to_json` 裸序列化或吞异常致空结果；❌ 观测/审计改动抛异常影响主链路。
