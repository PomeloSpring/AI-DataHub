-- ============================================================================
-- 修复：会话 1783069459334「三方集成 App 网页端入口点击分析」被答成「官网 ARMS + 桌面 App 二选一」
--
-- 两个真缺陷（本文件改数据面；本体对象名与技能正文在代码/本体发布链路里改）：
--   ① ISV 分析场景触发词过宽：裸「集成」「获取」让“三方**集成** App 网页端入口点击”命中
--      《外部合作方系统对接分析》，把一次“网页点击”顶成“三方系统调接口”；且其红线把
--      “启动器里点三方 App”写成“App 使用 / RUM 口径”，把两个对象并成一层。
--   ② RUM 域命名把整条通道叫“官网/H5”：AS Connect 等平台网页的浏览与「点开某个 App」
--      也记在同一条 RUM 通道上（内容中心《…三方集成 App 网页端入口点击分析》的数据源
--      就是 observability_rum + action='click'），名字里没有“平台网页”就会让模型把
--      “网页端”判成官网专属，并把桌面遥测 app_usage_event 并成同层候选。
--
-- 幂等：每条 UPDATE 都带“旧文案/旧触发词仍存在”的守卫，重复执行影响 0 行。
-- 生效：quick_paths 命中结果有 60s TTL 缓存（QUICK_PATH_CACHE_TTL），应用后最多 1 分钟自动生效，
--   或在「分析场景」页保存一次触发 clear_cache()。
-- 注意：本体对象业务名 `rum_event` 属派生视图，必须走「canonical doc → 保存草案 → 发布」
--   级联（本体建模规范 §1），不在本文件里直接 UPDATE adh_ontology_objects。
--   同名改动见 backend/scripts/build_app_usage_ontology.py 与 arms-observability/SKILL.md。
-- ============================================================================
USE adh2;

-- ----------------------------------------------------------------------------
-- 1) ISV 场景：触发词收窄（删裸「集成」「获取」→ 系统集成 / 数据集成 / 合作方获取）
--    「三方系统」「对接」「推送」「回调」「开放平台」保留，正常 ISV 问法仍能命中。
-- ----------------------------------------------------------------------------
UPDATE adh_knowledge_items
   SET metadata = JSON_SET(metadata, '$.triggers', JSON_ARRAY(
         '合作方', 'ISV', '三方系统', '第三方系统', '开放平台', '对接', '系统集成', '数据集成',
         '推送', '回调', '合作方获取', '读取数据', '牙谷云', '恒久', '康泰健', '时代天使',
         'Smartee', '领健', '家鸿', '轻松牙医', 'Laxmi', '维视科技', 'DPMS', '只拉不推',
         '停用', '新接入', '重复推送', '放大倍数', '识别率', '单据数')),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title = '外部合作方系统对接分析（平台推送与合作方获取）'
   AND JSON_CONTAINS(metadata, '"集成"', '$.triggers');

-- ----------------------------------------------------------------------------
-- 2) ISV 场景：红线改成三层分述（桌面客户端 → app_usage_event；平台网页 → rum_event；
--    三方系统调接口 → 本场景），不再把“App 使用 / RUM”并成一层。
-- ----------------------------------------------------------------------------
UPDATE adh_knowledge_items
   SET content = REPLACE(content,
         '「启动器里点三方 App」（App 使用 / RUM 口径）与「三方系统调接口」（本场景）',
         '「启动器或平台网页里点三方 App」（桌面客户端里点 → 对象 app_usage_event 的桌面遥测；平台网页里点 → 对象 rum_event 的 Web 端点击事件）与「三方系统调接口」（本场景，走接口日志）'),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title = '外部合作方系统对接分析（平台推送与合作方获取）'
   AND content LIKE '%「启动器里点三方 App」（App 使用 / RUM 口径）%';

UPDATE adh_knowledge_items
   SET content = REPLACE(content,
         '是两个主体口径，一次点击 ≠ 一次调用，同一厂商在两层都出现时也不相加。',
         '是三个互不相同的口径，一次点击 ≠ 一次调用，同一厂商在几层都出现时也不相加。'),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title = '外部合作方系统对接分析（平台推送与合作方获取）'
   AND content LIKE '%是两个主体口径，一次点击%';

UPDATE adh_knowledge_items
   SET content = REPLACE(content, '**不混算两层"三方"**', '**不混算三层"三方"**'),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title = '外部合作方系统对接分析（平台推送与合作方获取）'
   AND content LIKE '%不混算两层"三方"%';

-- ----------------------------------------------------------------------------
-- 3) RUM 两条分析场景：正文里的对象业务名补上「平台网页」
-- ----------------------------------------------------------------------------
UPDATE adh_knowledge_items
   SET content = REPLACE(content,
         '「Web 端 RUM 观测事件（官网/H5）」',
         '「Web 端 RUM 观测事件（官网/H5 · 平台网页）」'),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title IN ('官网 Web 端体验与流量分析', '按页面 URL 或 IP 关联用户操作路径')
   AND content LIKE '%「Web 端 RUM 观测事件（官网/H5）」%';

-- ----------------------------------------------------------------------------
-- 4) RUM 场景一：场景口径补上“平台网页/页面内点击”，触发词补「网页」「网页端」「入口点击」
--    （这是本轮问句的路由缺口：原触发词只有“官网/web端”，问“网页端”时一条都不命中）
-- ----------------------------------------------------------------------------
UPDATE adh_knowledge_items
   SET metadata = JSON_SET(metadata,
         '$.scenario',
         'Web 端（官网/H5 与平台网页）的访问量、UV/PV、会话时长、页面性能、接口成功率、前端异常、长任务、页面内点击等 Web 端体验与流量问题',
         '$.triggers',
         JSON_ARRAY('官网', '网站', '网页', '网页端', 'H5', 'web端', 'Web 端', '页面浏览', '入口点击',
                    'PV', 'UV', '独立用户', '访问用户', '会话数', '会话时长', '跳出', '加载耗时',
                    '资源加载', '接口成功率', 'FCP', 'LCP', '首屏', '白屏', '长任务', '前端异常',
                    'JS异常', '报错', 'ARMS', 'RUM', '性能指标', '应用环境', '生产环境')),
       updated_at = NOW()
 WHERE knowledge_type = 'quick_path'
   AND title = '官网 Web 端体验与流量分析'
   AND NOT JSON_CONTAINS(metadata, '"网页端"', '$.triggers');

-- ----------------------------------------------------------------------------
-- 5) 字典口径：桌面遥测“不含 Web 端 RUM”的说明写清另一半是“官网/H5 与平台网页”，
--    避免「应用使用事件数」「事件形态」把模型引向“官网=RUM、App=桌面”的二选一。
--    （重跑本体构建脚本也会写入同样文案，此处保证未跑脚本的环境口径一致）
-- ----------------------------------------------------------------------------
UPDATE adh_metrics
   SET description = '桌面客户端埋点事件条数（不含 Web 端 RUM：官网/H5 与 AS Connect 平台网页的页面/点击事件）',
       updated_at = NOW()
 WHERE name = '应用使用事件数'
   AND bound_object_key = 'app_usage_event'
   AND description LIKE '%不含官网/H5 的 Web 端 RUM%';

UPDATE adh_dimensions
   SET description = 'app 埋点行为形态（桌面客户端遥测，与 Web 端 RUM 的事件类型是两套口径）',
       updated_at = NOW()
 WHERE name = '事件形态'
   AND bound_object_key = 'app_usage_event'
   AND description LIKE '%非 Web 端 RUM 的事件类型%';

-- ----------------------------------------------------------------------------
-- 6) 自检（只读）：应该看到 ISV 触发词里没有「集成」「获取」，
--    RUM 场景一触发词含「网页端」，字典文案含「平台网页」。
-- ----------------------------------------------------------------------------
SELECT id, title,
       JSON_CONTAINS(metadata, '"集成"', '$.triggers')     AS has_integration_trigger,
       JSON_CONTAINS(metadata, '"获取"', '$.triggers')     AS has_fetch_trigger,
       JSON_CONTAINS(metadata, '"网页端"', '$.triggers')   AS has_web_trigger
  FROM adh_knowledge_items
 WHERE knowledge_type = 'quick_path'
   AND title IN ('外部合作方系统对接分析（平台推送与合作方获取）', '官网 Web 端体验与流量分析');

SELECT name, bound_object_key, LEFT(description, 80) AS description
  FROM adh_metrics WHERE bound_object_key = 'app_usage_event' AND name = '应用使用事件数';
SELECT name, bound_object_key, LEFT(description, 80) AS description
  FROM adh_dimensions WHERE bound_object_key = 'app_usage_event' AND name = '事件形态';
