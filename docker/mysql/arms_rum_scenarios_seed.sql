-- ============================================================================
-- 分析场景种子：官网 ARMS RUM 体验分析 + 按 URL/IP 关联用户操作路径
--
-- 用户 2026-11-06 拍板：把这两类问题各做成一个**分析场景**（knowledge_type 内部值仍
-- 为 quick_path，用户可见名「分析场景」）。场景的实质是"一类问题的通用 workflow"
-- （对已建模对象/指标/维度的编排），不是具体问题清单 —— 上一轮为凑首页空位把三条
-- 写死问句建成场景的做法已被否决，本批只登记真能复用的作业流程。
--
-- 正文口径全部来自实测（不是照抄通用最佳实践）：
--   * 「ARMS RUM 观测本体」deploy_sites = 中国站点(arms-prd-cn) + 新加坡站点(arms-prd-en)，
--     对象 `rum_event`「Web 端 RUM 观测事件（官网/H5）」，19 个生效指标、29 个生效维度；
--   * 每个指标 description 都写着「直查 logstore：不隐式过滤环境，生产口径请显式加
--     应用环境=生产」→ 场景正文把这条列为第 2 步硬要求（漏加会把测试环境算进官网口径）；
--   * `应用环境` 枚举 {"prod":"生产","test":"测试"}，`事件类型` 枚举
--     {"view":"页面","action":"交互","resource":"资源","exception":"异常",
--      "session":"会话","longtask":"长任务","custom":"自定义"}；
--   * 语义层能力边界：意图契约只支持单层聚合（不支持事件时序/两级聚合），窗口函数
--     仅 rank/rolling/period_compare/share，跨对象 BFS ≤2 跳且禁止同表自关联
--     → 路径序列还原必须走「先 check_sql 再 execute_sql」的受治理旁路，不能假装语义层能出；
--   * 出站守卫的键名黑名单含裸 `ip`（ASCII 前缀的 client_ip 不命中）→ 结果列别名
--     必须写 client_ip，否则整条数据行会被拒发（这是实测过的坑，不是猜测）；
--   * SLS/Presto 端点：SQL 别名必须 ASCII，带点的物理列（app.name/net.ip/session.id）
--     不能当标识符裸用；
--   * 关键路径（scenarios[].path）在本体里**尚未建模**（该模型 json_content 中 scenario/path
--     出现 0 次）→ 正文不声称已有固定关键路径口径，只说明要沉淀该去哪登记；
--   * 敏感基线：`username`/`user_name` 全局 block；RUM 的 `net.ip`/`session.id`/`user.id`
--     未登记屏蔽，且「独立IP数」口径写明"RUM IP 明文可查，产品口径不脱敏"。
--
-- 幂等：表 adh_knowledge_items 无唯一键（只有 PRIMARY + 普通索引），故按
--   (knowledge_type, title) 判重 INSERT ... WHERE NOT EXISTS；**已存在就不覆盖**，
--   避免重跑迁移把管理员在「分析场景」页上的手工修订抹掉。
-- created_by 不写（列是 bigint，而管理面创建也不写这一列）：迁移登记不属于某个用户，
--   写成 'admin' 字符串会直接报 1366 Incorrect integer value。
-- 生效：quick_paths.match_quick_paths 有 60s TTL 缓存（QUICK_PATH_CACHE_TTL），
--   应用后最多 1 分钟自动生效，或在「分析场景」页保存一次触发 clear_cache()。
-- ============================================================================
USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 场景一：官网 Web 端体验与流量分析（ARMS RUM）
-- ----------------------------------------------------------------------------
INSERT INTO adh_knowledge_items
  (id, workspace_id, datasource_id, knowledge_type, title, content, metadata,
   related_tables, priority, is_active, created_at, updated_at)
SELECT 1796000000000001, 0, 0, 'quick_path',
  '官网 Web 端体验与流量分析',
  '## 适用问题（官网 / H5 的 Web 端体验与流量）

访问量、UV/PV、会话数与时长、页面性能（FCP/LCP）、资源与接口成功率、前端异常与长任务、按页面/地域/设备/版本拆分等一类问题。数据源是阿里云 ARMS RUM（SLS logstore 直查），业务对象为「Web 端 RUM 观测事件（官网/H5）」（内部 key `rum_event`）。

## 第 1 步 定范围（先说清再取数）

- 站点：官网 RUM 只部署在**中国站点**（`arms-prd-cn`）与**新加坡站点**（`arms-prd-en`）。用户没指定站点就分站呈现，不要擅自合计（合计口径应走数仓）。
- 应用与端：用维度「应用名称」锁定具体应用，用「应用类型」区分 `browser`（浏览器/Web，官网属这一类）/`app`（原生应用）/`miniapp`（小程序）。桌面 app 的遥测不属本场景（那是「应用使用事件」对象）。
- 版本与渠道：需要时用「应用版本」「应用渠道」过滤。
- 时间：一律用相对时间窗（`time_window`，如 7d/24h/1M），不要手算绝对日期。时间维度是「统计日期」（天）与「统计小时」（小时）。

## 第 2 步 生产口径必须显式声明（本场景最容易漏的一步）

该源是 logstore 直查，**不隐式过滤环境**。凡是要给"官网生产口径"的结论，必须显式加维度过滤「应用环境 = 生产」（物理列 `app.env`，值 prod；枚举 {"prod":"生产","test":"测试"}）。漏加会把测试环境流量算进官网，数字看着合理但口径是错的。若用户明确要全环境或要看测试环境，就在结论里如实说明范围。

## 第 3 步 选指标（名称必须从 get_metrics 实时目录复制，不要凭印象造）

- 流量：页面浏览量、事件总数、独立用户数、会话数、独立IP数、人均会话数
- 体验与性能：首屏时间FCP(ms)、最大内容绘制LCP(ms)、会话平均时长(ms)、人均会话时长(秒)
- 稳定性：异常数、异常用户数、异常会话数、长任务数、资源请求数、资源成功率(%)、资源错误数、资源加载平均耗时(ms)、资源加载P95耗时(ms)

字典里没有的口径词（例如"跳出率""使用率""转化率"）**不要自己发明指标**。字典没有就直说没有，并给出可用替代口径（如用「会话数」与「页面浏览量」分别陈述），不要拿近似指标改名冒充。

## 第 4 步 选维度做拆分

页面名称（别名 路由/页面）、页面URL、页面域名、国家、省份、城市、设备类型、操作系统、浏览器、网络运营商、异常类型/异常子类型/异常名称、资源名称/资源类型/资源请求结果、事件类型（枚举见下）。

「事件类型」枚举语义：view=页面、action=交互、resource=资源(接口)、exception=异常、session=会话、longtask=长任务、custom=自定义。按事件类型拆分前先确认用户问的是哪一类。

## 第 5 步 输出与回显

- 趋势用时序图，维度对比用柱状/表值，单点结论给数字与时间窗。
- 结论里必须回显口径：站点 + 应用名称 + 应用环境（是否只取生产）+ 时间窗 + 指标名。
- 慢查询注意：SLS 端点 SQL 别名必须是 ASCII（中文别名会报错），带点的物理列（`app.name`/`net.ip`/`session.id`）不能当裸标识符使用——优先用业务指标名让语义层编译，需要手写 SQL 时先 check_sql 预检。

## 隐私与合规边界

`username`/`user_name` 属全局敏感屏蔽列，任何路径都拿不到，不要尝试绕。官网用户规模用「独立用户数」（按 `user.id` 去重）表达即可，不需要用户名明细。',
  '{"scenario":"官网/H5 的访问量、UV/PV、会话时长、页面性能、接口成功率、前端异常与长任务等 Web 端体验与流量问题","subjects":["rum_event"],"subject_all":false,"triggers":["官网","网站","H5","web端","Web 端","页面浏览","PV","UV","独立用户","访问用户","会话数","会话时长","跳出","加载耗时","资源加载","接口成功率","FCP","LCP","首屏","白屏","长任务","前端异常","JS异常","报错","ARMS","RUM","性能指标","应用环境","生产环境"]}',
  'rum_event', 60, 1, NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM (SELECT 1 FROM adh_knowledge_items
                 WHERE knowledge_type = 'quick_path' AND title = '官网 Web 端体验与流量分析') AS t
);

-- ----------------------------------------------------------------------------
-- 2) 场景二：按页面 URL 或 IP 关联用户操作路径
-- ----------------------------------------------------------------------------
INSERT INTO adh_knowledge_items
  (id, workspace_id, datasource_id, knowledge_type, title, content, metadata,
   related_tables, priority, is_active, created_at, updated_at)
SELECT 1796000000000002, 0, 0, 'quick_path',
  '按页面 URL 或 IP 关联用户操作路径',
  '## 适用问题（给一个锚点，还原"先后做了什么"）

用户给出某个 **页面 URL / 客户端 IP / 会话 ID / 用户 ID**，要回答：这个页面里用户先后做了哪些操作、这条访问链路怎么走的、这个 IP 背后有哪些会话、某次会话里发生了什么异常。数据同属「Web 端 RUM 观测事件（官网/H5）」对象（内部 key `rum_event`，站点：中国站点 / 新加坡站点）。

## 第 1 步 认锚点，选对关联维度（名称从字典复制）

- 「页面URL」（物理 `view.url`，别名 URL/地址/访问链接/页面链接）——用户贴出一段网址时用这个；只要域名时用「页面域名」（别名 host/域名），要页面时用「页面名称」（别名 路由/页面）。
- 「客户端IP」（物理 `net.ip`，别名 IP/ip/来源IP/访问IP）——用户说"这个 IP 谁在用/做了什么"时用这个。另有「会话IP」（`session.ip`），两者不是一回事，别混用。
- 「会话ID」（`session.id`）与「用户ID」（`user.id`）——已经具体到单个主体时用它们收敛范围，比按 IP 更准（同一 IP 可能是 NAT 出口或共享出口）。
- 时间窗必须给：默认近 7 天，用户说了就按他说的（用 `time_window`，不要手算绝对日期）。

## 第 2 步 先聚合定位，再下钻明细（不要一上来拉全量明细）

先用语义层出规模：按「会话ID」或「页面URL」分组，取「事件总数」「页面浏览量」「会话数」「异常数」「资源错误数」，找出值得下钻的会话（Top 几条），再进第 3 步。语义层擅长这个，也确实只能做到这一步——**时序还原它做不到**。

## 第 3 步 序列还原走受治理 SQL（关键能力边界）

语义层意图契约只支持单层聚合，**不支持事件时序与两级聚合**，窗口函数只有 rank/rolling/period_compare/share，跨对象关联 ≤2 跳且禁止同表自关联。所以"按时间排出一条操作线"必须走 nl2sql 旁路：先 `check_sql` 预检，再 `execute_sql`（只读 SELECT/WITH，必带 LIMIT）。

写这条 SQL 的要求：

- 按 `session.id` 分组、按 `__time__`（SLS 内置时间列，单位秒）升序排列事件；
- 取回列：时间、`event_type`（事件类型）、`view.url` 或 `view.name`、`resource.name`（接口/资源）、`exception.name`/`exception.type`（异常）、必要时 `user.id`/`net.ip`；
- 别名必须 ASCII（中文别名在 SLS/Presto 端点会报错）；派生表必须带别名；
- **输出列名不要用裸 `ip`**（例如 `net.ip AS ip`）——裸键 `ip` 会被出站守卫按内部基础设施键拒发，整条结果出不来。写成 `client_ip` / `session_ip` 就正常。这是实测过的坑。
- 生产口径同样要显式加 `app.env` 过滤（见「官网 Web 端体验与流量分析」场景第 2 步）。

## 第 4 步 事件类型语义（"操作"和"访问页面"不是一回事）

view=页面浏览、action=交互操作、resource=资源/接口请求、exception=异常、session=会话、longtask=长任务、custom=自定义。用户说"做了什么操作"主要看 action 与 view；说"接口报了什么错"看 resource（`resource.success` 0=失败/1=成功）与 exception。别把自己关心的类型混着塞进一条时间线后又不说明。

## 第 5 步 输出

- 时间线：时间 + 事件类型 + 页面/资源/异常名，按会话分段呈现；
- 明确覆盖范围：站点、应用环境（是否只取生产）、时间窗、命中会话数与事件数；
- 结论克制：一次会话的序列只能说明"这台设备/这个会话做了什么"，不能直接推广成"用户群体行为"。

## 想要固定的关键路径/漏斗时

本场景做的是"给定锚点后还原序列"。若要把某条链路沉淀成**固定关键路径口径**（例如 首页 → 产品页 → 注册 → 下载），应在本体建模侧的 `scenarios[].path` 登记步骤序列（校验要求：每步必须有业务名、order 连续、步骤引用已建模对象），当前 ARMS RUM 观测本体**尚未登记任何 path**，所以不要声称平台已有"关键路径"口径。

## 隐私边界

只做行为关联，不返回身份明细：`username`/`user_name` 是全局敏感屏蔽列，拿不到也不要试。IP 与用户ID/会话ID 属产品口径允许查看的关联标识（RUM 侧不脱敏），但输出时优先给聚合规模，明细只在用户明确要求下钻时给出。',
  '{"scenario":"给定页面 URL / 客户端 IP / 会话ID / 用户ID，还原该主体在时间轴上先后做了什么操作（含异常与接口失败）","subjects":["rum_event"],"subject_all":false,"triggers":["操作路径","行为路径","访问路径","路径分析","关键路径","转化路径","用户轨迹","会话轨迹","操作序列","先后做了什么","接下来做了什么","怎么走到","访问链路","这个URL","这个 IP","某个IP","来源IP","这台设备","这个会话","这个用户","谁访问了","下钻明细","时序还原"]}',
  'rum_event', 55, 1, NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM (SELECT 1 FROM adh_knowledge_items
                 WHERE knowledge_type = 'quick_path' AND title = '按页面 URL 或 IP 关联用户操作路径') AS t
);

-- ----------------------------------------------------------------------------
-- 3) 主体回填：两条场景都只编排「Web 端 RUM 观测事件」这一个主体
--
-- 为什么必须回填：消费侧按主体授权是 fail-closed 的（backend/core/scenario_access.py），
-- 没登记 subjects 又没勾 subject_all 的场景对所有角色都不会命中、也不进 / 候选。
-- 幂等：只在 subjects 缺失时写入，不覆盖管理员在「分析场景」页改过的主体范围。
-- ----------------------------------------------------------------------------
UPDATE adh_knowledge_items
   SET metadata = JSON_SET(metadata, '$.subjects', JSON_ARRAY('rum_event'), '$.subject_all', FALSE),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title IN ('官网 Web 端体验与流量分析', '按页面 URL 或 IP 关联用户操作路径')
   AND JSON_EXTRACT(metadata, '$.subjects') IS NULL;

-- ----------------------------------------------------------------------------
-- 4) 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- SELECT id, title, priority, is_active,
--        JSON_EXTRACT(metadata,'$.subjects') AS subjects,
--        JSON_EXTRACT(metadata,'$.subject_all') AS subject_all,
--        JSON_LENGTH(JSON_EXTRACT(metadata,'$.triggers')) AS triggers
--   FROM adh_knowledge_items WHERE knowledge_type='quick_path' ORDER BY priority DESC;
--
-- 命中自证（服务端同一段代码，不依赖前端）。生产调用点会显式传 allowed_subjects；
-- 这里传 None = 不做主体裁决，只验证触发词与正文本身能命中：
--   venv/bin/python -c "from backend.modules.mind.execution.quick_paths import match_quick_paths as m; \
--     print([p['title'] for p in m('官网最近7天UV和LCP怎么样', allowed_subjects=None)]); \
--     print([p['title'] for p in m('帮我查某个IP的用户操作路径', allowed_subjects=None)])"
--
-- 按身份验可见集（“看得到”与“用得上”必须同源）：
--   venv/bin/python -c "from backend.core import scenario_access as sa; print(sorted(sa.visible_subject_keys(<user_id>, 0, role='<role>')))"
--
-- 注意：本迁移不覆盖已存在的同名场景（管理员在「分析场景」页的修订优先），
--       主体回填也只在 subjects 缺失时动手。
-- 另：`/` 面板的场景候选已接入（GET /api/chat/slash-options 的 scenes 字段），
--     候选与提示词注入都走同一个主体判定，因此“看得到”与“真生效”同源。
