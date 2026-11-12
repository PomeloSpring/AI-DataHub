-- =====================================================================
-- 「App 使用」看板应用维度分桶：主应用与联耀自研单列，第三方集成合并，零头未归类
--
-- 背景（2026-11-11 需求）：
--   应用维度图原先把 32 个 app_code 平铺（柱状/环形挤成一排、x 轴全部截断），
--   而业务上只有 ASSCANPRO 与 CONNECT 是主应用，另有 5 个联耀自研产品需要单看，
--   其余是第三方集成 —— 把它们混在同一维度里既读不出重点，也把长尾噪声放大。
--
-- 口径（唯一表达式源，两个数据集共用同一段 CASE）：
--   主应用  ASSCANPRO / CONNECT                         → 各自单列
--   自研    ModelBuilder / OralHealthReport / MeshCompare / SmileDesign / PressureImpression → 各自单列
--   自研    NewOrthoSim + OrthoSim                      → 合并为「Ortho Simulation」（同一产品新老埋点）
--   第三方  17 个 *Integration / UnizDental 等集成码     → 合并为「第三方集成App」
--   其余    空 appCode / ASWEB / CAOffice / Crown / CrownDesign / ASCONNECT_ANDROID
--           及**未来新增未登记码**                       → 落「其他未归类App」
--   为什么 ELSE 走未归类而不是走第三方：新增 app 静默计入第三方会把没登记的量贴错标签，
--   落进未归类则桶会 visibly 变大 —— 宁缺勿错（no-silent-degradation）。
--
-- 影响面（只动 App 使用页，其余消费方零变化）：
--   ① 新建 ds#56（派生自 ds#35，维度别名 app_code → app_group）；
--      ds#35 **正文不动** —— 它还服务「数字化大屏」「Test」的按应用案例数与本页三个 KPI 总量卡。
--   ② ds#36 正文原地分桶（只有 App 使用页消费，不新建以免留孤儿口径）。
--   ③ 三张图重挂/改列名绑定并改名（挂在别的口径上却留原标题就是骗人）：
--      按应用·案例数 → 按应用分组·案例数（ds#35 → ds#56, xCol app_code → app_group）
--      使用次数·按应用分布（环形）→ 使用次数·按应用分组分布（环形）（ds#35 → ds#56）
--      按应用与业务类型·操作次数（分组柱状）→ 按应用分组×业务类型·操作次数（仍 ds#36）
--   ④ 不动：三个 KPI 总量卡（ds#35）、操作次数·按业务类型环形（ds#36 的 business_type 边际，
--      分桶不改变它）、按天趋势（ds#37）、App 目录（ds#50，本就是逐 app 明细）。
--
-- 已核验（固定窗口 2026-10-01 ~ 2026-10-31）：
--   Σ event_quantity 分桶 = 未分桶 = 657,100（差异 0，分桶不丢行不重计）；
--   business_type 边际 改前=改后 = {openApp 508738, caseExport 125171, orderExport 23191}；
--   默认窗口出 10 个分组（含「第三方集成App」「其他未归类App」）。
--
-- 幂等：①按 name 唯一键 upsert；②③按 id + 值不变即 0 行。重跑终态一致。
--   注意：ds#56 的 id 与图表 id 是本环境实测值；换环境执行前先核 id 是否同值
--   （执行末尾核验段必须逐条看到期望行，插不进去就是没生效，不得当成功）。
-- =====================================================================

-- ① 新建（或刷新）分桶数据集 ds#56
INSERT INTO adh_datasets (`id`, `name`, `description`, `source_type`, `object_key`, `es_pattern`, `datasource_id`, `available_datasources`, `sql_query`, `sql_bodies`, `variables`, `touched_tables`, `field_config`, `chart_type`, `chart_preset`, `paginable`, `owner_id`, `origin`, `status`)
VALUES ('56', 'App 使用·按应用分组案例与使用次数', '「App 使用」应用维度分桶版（派生自 ds#35，只本看板消费；ds#35 原样留给「数字化大屏」「Test」与本页三个 KPI 总量卡）。分桶：主应用 ASSCANPRO/CONNECT 与自研 ModelBuilder/OralHealthReport/MeshCompare/SmileDesign/PressureImpression 各自单列；NewOrthoSim+OrthoSim=「Ortho Simulation」；17 个第三方集成码合并「第三方集成App」；其余（空码/ASWEB/CAOffice/Crown*/ASCONNECT_ANDROID 及未登记新码）落「其他未归类App」—— 新增 app 不静默算作第三方。清单与 ds#36 同源，改一处要同步另一处。 案例数/用户数是 ES cardinality 近似值（±0.5%~2%）；时间留空按近 30 天滚动窗补齐并回显 default_window。', 'es', '', 't-t_software_app_operation_log*', '0', '[]', 'SELECT CASE WHEN "appCode.keyword" IN (''ASSCANPRO'', ''CONNECT'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''ModelBuilder'', ''OralHealthReport'', ''MeshCompare'', ''SmileDesign'', ''PressureImpression'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''NewOrthoSim'', ''OrthoSim'') THEN ''Ortho Simulation''
            WHEN "appCode.keyword" IN (''NHDataHubIntegration'', ''SprintRayIntegration'', ''FormlabsIntegration'', ''coDiagnostiXIntegration'', ''ClinuxCADIntegration'', ''AsigaComposerIntegration'', ''UnizDental'', ''DentreadIntegration'', ''ShapeWareIntegration'', ''CameoPrinterIntegration'', ''3DXWORKSIntegration'', ''LuxDesignIntegration'', ''NHKeaIntegration'', ''CircleOneIntegration'', ''RapidShapeIntegration'', ''HeyGearsIntegration'', ''DentbirdIntegration'') THEN ''第三方集成App''
            ELSE ''其他未归类App'' END AS app_group,
       COUNT(DISTINCT "caseUid.keyword") AS case_quantity,
       COUNT(DISTINCT "userCode.keyword") AS user_quantity,
       COUNT(*) AS event_quantity
FROM "t-t_software_app_operation_log*"
WHERE "caseUid.keyword" IS NOT NULL
  AND (${start_time} = '''' OR "evenTime.keyword" >= ${start_time})
  AND (${end_time} = '''' OR "evenTime.keyword" <= CONCAT(${end_time}, ''~''))
  AND (${site} = '''' OR "site.keyword" = ${site})
  AND (${appmanager} = '''' OR "appmanager.keyword" = ${appmanager})
  AND (${app_code} = '''' OR "appCode.keyword" = ${app_code})
GROUP BY CASE WHEN "appCode.keyword" IN (''ASSCANPRO'', ''CONNECT'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''ModelBuilder'', ''OralHealthReport'', ''MeshCompare'', ''SmileDesign'', ''PressureImpression'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''NewOrthoSim'', ''OrthoSim'') THEN ''Ortho Simulation''
            WHEN "appCode.keyword" IN (''NHDataHubIntegration'', ''SprintRayIntegration'', ''FormlabsIntegration'', ''coDiagnostiXIntegration'', ''ClinuxCADIntegration'', ''AsigaComposerIntegration'', ''UnizDental'', ''DentreadIntegration'', ''ShapeWareIntegration'', ''CameoPrinterIntegration'', ''3DXWORKSIntegration'', ''LuxDesignIntegration'', ''NHKeaIntegration'', ''CircleOneIntegration'', ''RapidShapeIntegration'', ''HeyGearsIntegration'', ''DentbirdIntegration'') THEN ''第三方集成App''
            ELSE ''其他未归类App'' END
ORDER BY case_quantity DESC
LIMIT 100', '{}', '{"site": {"type": "string", "default": "", "value_domain": "site"}, "app_code": {"type": "string", "default": ""}, "end_time": {"type": "string", "default": ""}, "appmanager": {"type": "string", "default": ""}, "start_time": {"type": "string", "default": ""}}', '["t-t_software_app_operation_log*"]', '[{"role": "dimension", "field": "app_group", "label": "应用分组"}, {"agg": "NONE", "role": "measure", "unit": "例", "field": "case_quantity", "label": "案例数"}, {"agg": "NONE", "role": "measure", "unit": "人", "field": "user_quantity", "label": "用户数"}, {"agg": "NONE", "role": "measure", "unit": "次", "field": "event_quantity", "label": "事件数"}]', 'bar', '{"xCol": "app_group", "yCol": "case_quantity"}', '0', '1778154925', 'manual', 'active')
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description), source_type = VALUES(source_type), object_key = VALUES(object_key), es_pattern = VALUES(es_pattern), datasource_id = VALUES(datasource_id), available_datasources = VALUES(available_datasources), sql_query = VALUES(sql_query), sql_bodies = VALUES(sql_bodies), variables = VALUES(variables), touched_tables = VALUES(touched_tables), field_config = VALUES(field_config), chart_type = VALUES(chart_type), chart_preset = VALUES(chart_preset), paginable = VALUES(paginable), owner_id = VALUES(owner_id), origin = VALUES(origin), status = VALUES(status);

-- ② ds#36 正文原地分桶（描述同步声明分桶口径）
UPDATE adh_datasets
   SET sql_query   = 'SELECT CASE WHEN "appCode.keyword" IN (''ASSCANPRO'', ''CONNECT'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''ModelBuilder'', ''OralHealthReport'', ''MeshCompare'', ''SmileDesign'', ''PressureImpression'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''NewOrthoSim'', ''OrthoSim'') THEN ''Ortho Simulation''
            WHEN "appCode.keyword" IN (''NHDataHubIntegration'', ''SprintRayIntegration'', ''FormlabsIntegration'', ''coDiagnostiXIntegration'', ''ClinuxCADIntegration'', ''AsigaComposerIntegration'', ''UnizDental'', ''DentreadIntegration'', ''ShapeWareIntegration'', ''CameoPrinterIntegration'', ''3DXWORKSIntegration'', ''LuxDesignIntegration'', ''NHKeaIntegration'', ''CircleOneIntegration'', ''RapidShapeIntegration'', ''HeyGearsIntegration'', ''DentbirdIntegration'') THEN ''第三方集成App''
            ELSE ''其他未归类App'' END AS app_group,
       "businessType.keyword" AS business_type,
       COUNT(*) AS quantity,
       COUNT(DISTINCT "userCode.keyword") AS user_quantity
FROM "t-t_software_app_operation_log*"
WHERE "caseUid.keyword" IS NOT NULL
  AND "appCode.keyword" IS NOT NULL
  AND "businessType.keyword" IS NOT NULL
  AND (${start_time} = '''' OR "evenTime.keyword" >= ${start_time})
  AND (${end_time} = '''' OR "evenTime.keyword" <= CONCAT(${end_time}, ''~''))
  AND (${site} = '''' OR "site.keyword" = ${site})
  AND (${app_code} = '''' OR "appCode.keyword" = ${app_code})
  AND (${business_type} = '''' OR "businessType.keyword" = ${business_type})
GROUP BY CASE WHEN "appCode.keyword" IN (''ASSCANPRO'', ''CONNECT'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''ModelBuilder'', ''OralHealthReport'', ''MeshCompare'', ''SmileDesign'', ''PressureImpression'') THEN "appCode.keyword"
            WHEN "appCode.keyword" IN (''NewOrthoSim'', ''OrthoSim'') THEN ''Ortho Simulation''
            WHEN "appCode.keyword" IN (''NHDataHubIntegration'', ''SprintRayIntegration'', ''FormlabsIntegration'', ''coDiagnostiXIntegration'', ''ClinuxCADIntegration'', ''AsigaComposerIntegration'', ''UnizDental'', ''DentreadIntegration'', ''ShapeWareIntegration'', ''CameoPrinterIntegration'', ''3DXWORKSIntegration'', ''LuxDesignIntegration'', ''NHKeaIntegration'', ''CircleOneIntegration'', ''RapidShapeIntegration'', ''HeyGearsIntegration'', ''DentbirdIntegration'') THEN ''第三方集成App''
            ELSE ''其他未归类App'' END, "businessType.keyword"
ORDER BY quantity DESC
LIMIT 300',
       description = '「App 使用」应用分组×业务类型使用次数（appCode 分桶口径同「App 使用·按应用分组案例与使用次数」：主应用与自研单列、第三方合并、零头未归类）。原接口在内存里过滤 appCode/businessType 为空的桶，这里用 IS NOT NULL 下推到 ES。时间参数留空时服务端按近 30 天滚动窗补齐并在 default_window 回显。',
       updated_at  = NOW()
 WHERE id = 36;

-- ③ 图表 1793969472383「按应用分组·案例数」重挂与列名绑定
UPDATE adh_charts
   SET name       = '按应用分组·案例数',
       source_id  = 56,
       config     = '{"limit": 20, "xCol": "app_group", "yCol": "case_quantity", "pageLayout": {"version": 3, "desktop": {"x": 0, "y": 61, "w": 24, "h": 78}}}',
       data_cache = NULL,
       updated_at = NOW()
 WHERE id = 1793969472383 AND dashboard_id = 1793969471815;

-- ③ 图表 1793970975877「使用次数·按应用分组分布（环形）」重挂与列名绑定
UPDATE adh_charts
   SET name       = '使用次数·按应用分组分布（环形）',
       source_id  = 56,
       config     = '{"limit": 20, "xCol": "app_group", "yCol": "event_quantity", "innerRadius": 0.55, "pageLayout": {"version": 3, "desktop": {"x": 24, "y": 64, "w": 12, "h": 80}}}',
       data_cache = NULL,
       updated_at = NOW()
 WHERE id = 1793970975877 AND dashboard_id = 1793969471815;

-- ③ 图表 1793970976379「按应用分组×业务类型·操作次数」重挂与列名绑定
UPDATE adh_charts
   SET name       = '按应用分组×业务类型·操作次数',
       source_id  = 36,
       config     = '{"limit": 200, "xCol": "app_group", "yCol": "quantity", "groupCol": "business_type", "pageLayout": {"version": 3, "desktop": {"x": 0, "y": 145, "w": 48, "h": 81}}}',
       data_cache = NULL,
       updated_at = NOW()
 WHERE id = 1793970976379 AND dashboard_id = 1793969471815;


-- 核验（人工看结果，不自动改）：
--   SELECT id,name,source_type,CHAR_LENGTH(sql_query) len FROM adh_datasets WHERE id IN (35,36,56);
--     期望 56 存在且 source_type='es'；35 正文长度未变（仍 690 上下）；36 变长（含 CASE）
--   SELECT id,name,source_id,JSON_UNQUOTE(JSON_EXTRACT(config,'$.xCol')) x FROM adh_charts
--     WHERE id IN (1793969472383,1793970975877,1793970976379);
--     期望 x 全为 app_group，source_id 为 56/56/36
--   SELECT name,source_id FROM adh_charts WHERE source_id=35 AND dashboard_id<>1793969471815;
--     期望「Test / 数字化大屏」的按应用案例数仍在（未受影响）
--   实出数对账（跑 logs/verify_app_group_bucket2.py）：
--     Σ event_quantity 分桶 = 未分桶；business_type 边际改前=改后
