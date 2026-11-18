-- =====================================================================
-- 「三方集成 App 使用量」语义补齐（幂等，可重复执行）
--
-- 背景（会话 1783069459340 实锤缺陷）：
--   用户问「三方集成app近7天的访问量」，语义层落到「集成记录」(isv_integration /
--   t_integration_record) 只回了 CircleOneIntegration —— 那是**对接台账的记录条数**，
--   不是 App 打开/使用量。正确口径是 ES 运行日志对象「云平台软件操作日志」
--   (`cloud_es_software_app_operation_log`, businessType='openApp')。
--   根因三条：
--     ① 字典缺「三方集成App」这一组（17 个集成码无处归并）与「访问量」近义词；
--     ② 维度「集成来源应用」的别名里塞了**示例值**「…如 CircleOneIntegration」，
--        形成字面吸附（问句里出现 CircleOne 类字样就被吸过去）；
--     ③ 「集成记录数」的说明没写边界，LLM 只能按字面理解成"访问量"。
--
-- 本文件的职责（ES 侧的对象别名/分组维度/三方集成指标由构建器收敛，不在本文件重复）：
--   * ES 域（对象别名 + 「应用分组」维度 + 「三方集成App访问量/使用用户数」指标）
--     走 `backend/scripts/build_es_platform_logs_ontology.py --apply`（本体规范 §1：
--     对象别名属 canonical doc，禁止直改派生表 adh_ontology_objects）；
--   * 本文件只做**字典行**（adh_metrics / adh_dimensions）的别名与边界说明收敛，
--     以及补一条 quick_path 分析场景（按标题判重，已存在不覆盖）。
--
-- 口径事实源：17 个三方集成码清单与看板「App 使用」分桶同源（ds#36 / ds#56），
--   见 docker/mysql/app_usage_group_bucket_migration.sql；改一处必须同步另一处。
-- =====================================================================

-- ① 维度「集成来源应用」：去掉示例值别名（字面吸附根因），补边界说明
--    说明写成**静态文本**（不 CONCAT 旧值）：重跑迁移必须收敛到同一结果，追加式改写会越长越长
UPDATE adh_dimensions
   SET aliases = JSON_ARRAY('应用域标识', '集成来源应用码'),
       description = '集成记录归属的应用域标识（码值，如 CircleOneIntegration）。'
                     '这是**集成台账**的归属维度：用它分组得到的是"每个对接应用登记了多少条集成记录"，'
                     '不是 App 的打开/使用量。问「三方集成 App 的访问量/打开量/使用量/使用用户数」请到'
                     '「云平台软件操作日志」（对象 cloud_es_software_app_operation_log）用'
                     '「应用分组 = 第三方集成App」或指标「三方集成App访问量」；'
                     '问接口回调量请到「云平台第三方接口调用日志」。'
 WHERE id = 142 AND name = '集成来源应用';

-- ② 指标「集成记录数」：写清边界（它是台账记录条数，不是访问量），补两个中性别名
UPDATE adh_metrics
   SET aliases = JSON_ARRAY('集成记录条数', '集成记录总量'),
       description = 'ISV/应用集成**台账**的记录条数（1 行 = 1 条集成记录，口径过滤 del_flag = 0）。'
                     '**不是 App 打开次数 / 访问量 / 使用用户数**：App 使用量请用「云平台软件操作日志」的'
                     '「三方集成App访问量」（17 个三方集成码的 openApp 事件数）或「打开应用次数」；'
                     '接口回调调用量请用「云平台第三方接口调用日志」的「接口调用次数」。'
 WHERE id = 132 AND name = '集成记录数';

-- ③ quick_path 分析场景：「三方集成 App 与自研 App 使用量」
--    触发词先落会话变量：INSERT 与后面的收敛 UPDATE 用同一份，不手抄两遍。
--    ⚠ 必须 CAST(... AS JSON)：用户变量是**字符串**，直接放进 JSON_OBJECT/JSON_SET 会被
--      二次编码成 "[\"...\"]" 字符串（triggers 变成 1 个元素的字符串，场景永不命中）。
SET @qp_triggers = JSON_ARRAY(
    '三方集成app', '第三方集成app', '三方集成 app', '第三方集成 app',
    '三方app', '第三方app', '集成app', '集成应用',
    '应用打开', '打开应用', '打开次数', '打开量', '应用访问量', 'app访问量', 'App访问量',
    '应用使用量', 'app使用量', 'App使用量', '软件使用量', '软件使用', '软件打开',
    '软件应用', '应用分组', '软件操作日志', 'App 使用'
);
--    按标题判重（已存在不覆盖：正文维护以库内现行版本为准，重跑迁移不得把人工修订打回去）
INSERT INTO adh_knowledge_items
       (`id`, `workspace_id`, `datasource_id`, `knowledge_type`, `title`, `content`,
        `metadata`, `related_tables`, `priority`, `usage_count`, `positive_count`,
        `negative_count`, `is_active`, `created_by`, `i18n`)
SELECT 1796000000000004, 0, 0, 'quick_path',
       '三方集成 App 与自研 App 使用量分析',
       '## 适用问题（App 打开/使用量，不是页面流量、不是对接台账）

问「某个 App / 三方集成 App / 第三方集成 App 近 N 天的打开量、访问量、使用量、使用用户数、使用趋势、按应用/按站点拆分」时用本场景。
业务对象是「云平台软件操作日志」（内部 key `cloud_es_software_app_operation_log`，ES 源 `cloud-platform-es`），
核心口径是 `businessType = openApp`。

**三条红线（这三处最容易被字面吸走，答错都不是数字偏差而是口径错误）**：
1. 不是「集成记录」(isv_integration)：那是 ISV 对接**台账**的记录条数，衡量"登记/同步了多少条数据"，
   与 App 有没有被打开无关；
2. 不是「云平台第三方接口调用日志」的「接口调用次数」：那是平台与三方系统之间的回调调用量；
3. 不是「Web 端 RUM 观测事件」的 PV/UV：那是官网/网页浏览，桌面 App 遥测不在那里。

## 第 1 步 定范围

- 应用：用维度「软件应用」（码值）锁定单个 App，或「应用分组」（业务标签分桶）看一类；
- 时间：一律 `time_window`（7d/24h/1M 等），不要手算绝对日期；看趋势要配 `time_grain`（day/week/month），
  ES 不支持季度粒度；
- 站点：维度「软件站点区域」（cn / global / eu / jp / uk，源内另有 us / ru 与空值）。
  默认**分站呈现不合计**，要合计口径必须在结论里显式说明；
- 业务类型：默认只取 `openApp`（指标已内建），若用户问导出量用「订单导出次数」「病例导出次数」。

## 第 2 步 选指标与维度（名称从 get_metrics / 维度清单复制，不要凭印象造）

指标：
- 「三方集成App访问量」= openApp 且 appCode ∈ 17 个三方集成码（问"三方集成 App 访问量"直接用这个）；
- 「三方集成App使用用户数」= 上述范围内的去重用户数（ES cardinality 近似值，±0.5%~2%）；
- 「打开应用次数」= 全部 openApp 事件（按「软件应用」或「应用分组」过滤即该 App/该组使用量）；
- 辅助：「软件使用设备数」「软件使用用户数」「使用应用种类数」。

维度：
- 「应用分组」：17 个三方集成码合并为「第三方集成App」、NewOrthoSim+OrthoSim 合并为「Ortho Simulation」、
  其余应用按码值原样呈现。**可直接分组，也可按业务标签过滤**（如 应用分组 = 第三方集成App，
  语义层会展开成 17 个码值，不会只取其中一个）；未登记的新应用码原样出现（看板 ds#36/56 把它归入
  「其他未归类App」，语义层不静默归并——这是两边已知的差异，答数时要说明）；
- 「软件应用」：单 App 粒度（原始码值）；
- 「软件站点区域」「业务类型」「事件来源」「应用管理器」：可分组/可过滤；
- 高基数列（「软件事件用户账号」「软件事件设备序列号」「公司编码」）**只能过滤或去重计数，不要分组**（ES 30 秒预算会超时）。

## 第 3 步 17 个三方集成码（口径事实源，新增集成 App 不会自动计入）

NHDataHubIntegration / SprintRayIntegration / FormlabsIntegration / coDiagnostiXIntegration /
ClinuxCADIntegration / AsigaComposerIntegration / UnizDental / DentreadIntegration / ShapeWareIntegration /
CameoPrinterIntegration / 3DXWORKSIntegration / LuxDesignIntegration / NHKeaIntegration /
CircleOneIntegration / RapidShapeIntegration / HeyGearsIntegration / DentbirdIntegration

- 该清单与看板「App 使用」分桶（ds#36 / ds#56）同源；数据集侧「三方 App 使用强度/明细」（ds#61 / ds#70）
  用的是**16 码**清单（不含 Dentbird）：Dentbird 实测全历史仅 5 次打开、2025-08-27 后为 0（已停用），
  所以两种口径的差异只落在历史尾巴上——汇报时说明用的是哪一档，不要把两个数混着讲。
- 新增集成应用码必须同批更新「应用分组」分桶与「三方集成App访问量」公式（脚本
  `backend/scripts/build_es_platform_logs_ontology.py` 的 SW_TP_APP_LIST），否则新码原样出现、不计入三方口径。

## 第 4 步 输出与回显

- 结论必须回显：对象（云平台软件操作日志）+ 口径（businessType=openApp / 三方集成 App 17 码）+ 时间窗 + 站点范围 + 指标名；
- 单点结论给数字与时间窗，趋势用时序图，应用对比用柱状/表值；
- 若用户要的数只能由数据集口径给出（如看板上的「案例数」按 caseUid 去重），直说那是数据集口径，不要拿语义层指标顶替；
- 查不到/超出能力（如季度趋势、跨对象 JOIN）就直说并给替代方案，不要用近似指标改名冒充。',
       JSON_OBJECT(
           'scenario', '桌面软件 App 的打开/使用量：三方集成 App 与自研 App 的访问量、使用用户数、按应用分组与站点拆分（ES 运行日志，口径 businessType=openApp）',
           'subjects', JSON_ARRAY('cloud_es_software_app_operation_log'),
           'triggers', CAST(@qp_triggers AS JSON),
           'subject_all', FALSE
       ),
       'cloud_es_software_app_operation_log', 59, 0, 0, 0, 1, NULL,
       JSON_OBJECT(
           'title', JSON_OBJECT('en-US', 'Third-party Integrated App and In-house App Usage'),
           'scenario', JSON_OBJECT('en-US', 'Desktop app open/usage volume for third-party integrated apps and in-house apps: visits, distinct users, breakdown by app group and site (ES operation logs, businessType=openApp)')
       )
 WHERE NOT EXISTS (
       SELECT 1 FROM (SELECT id FROM adh_knowledge_items
                       WHERE knowledge_type = 'quick_path'
                         AND title = '三方集成 App 与自研 App 使用量分析') AS _existing);

-- ④ 场景路由参数收敛（只对齐 triggers / priority，不碰正文）：
--    存量环境里场景已存在时，上面的 INSERT 不生效；但触发词属于**路由能力**，必须与定义同步，
--    否则“打开量/软件应用”这类问法在新的环境里命中、在旧环境里漏召（同一份代码两套行为）。
UPDATE adh_knowledge_items
   SET metadata = JSON_SET(COALESCE(metadata, JSON_OBJECT()), '$.triggers',
                           CAST(@qp_triggers AS JSON)),
       priority = 59,
       is_active = 1
 WHERE knowledge_type = 'quick_path'
   AND title = '三方集成 App 与自研 App 使用量分析';

-- =====================================================================
-- 核验段（执行后逐条确认；任何一条不是期望值就是没生效，不得当成功）
-- =====================================================================
-- 1) 维度 142 别名不再含示例值：
--    SELECT id, name, aliases, LEFT(description, 60) FROM adh_dimensions WHERE id = 142;
--    期望 aliases = ["应用域标识", "集成来源应用码"]，description 以「集成记录归属的应用域标识」开头
-- 2) 指标 132 边界说明已写：
--    SELECT id, name, aliases, LEFT(description, 60) FROM adh_metrics WHERE id = 132;
--    期望 aliases 含「集成记录条数」，description 含「不是 App 打开次数」
-- 3) 场景已登记（且重复执行不会报错、不会覆盖人工修订）：
--    SELECT id, title, priority, is_active, JSON_LENGTH(metadata->'$.triggers') AS n_trig FROM adh_knowledge_items
--     WHERE knowledge_type = 'quick_path' AND title = '三方集成 App 与自研 App 使用量分析';
--    期望 1 行：id=1796000000000004, priority=59, is_active=1, n_trig=25
--    ⚠ n_trig 若为 1 且 metadata->'$.triggers' 是带转义的字符串串，就是漏了 CAST(... AS JSON)，场景不会命中
-- 4) ES 域语义（由构建器收敛，非本文件）：
--    SELECT id, name, aliases, value_labels IS NOT NULL AS has_labels FROM adh_dimensions WHERE name = '应用分组';
--    SELECT id, name, aliases FROM adh_metrics WHERE name IN ('三方集成App访问量', '三方集成App使用用户数');
--    期望：应用分组 has_labels=1；两个指标各 1 行
