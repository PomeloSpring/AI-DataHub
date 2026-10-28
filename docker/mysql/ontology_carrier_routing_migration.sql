-- ============================================================================
-- 业务本体跨源拓扑改造 M2：承载路由生效（择路 + 指标口径约束）（幂等，可重复执行）
--
-- 本批只加一列：`adh_metrics.allowed_carrier` —— 指标层的"允许走哪类取数路径"约束。
--
-- 为什么约束在字典而不在承载上：可加性/全局性是**指标口径**的属性，不是部署属性。
-- 「全球用户数（去重）」天然只能走能算全集的路径（通常是数仓汇总），而
-- 「用户数（本站）」只能走明细。把这件事写进字典（口径唯一权威），
-- 择路函数（backend/semantics/topology.decide_carrier）只消费它，不另定义一套口径。
--
-- 配套行为（代码侧，不在本文件）：
--   · 消歧：一词多口径拆成两行指标（别名分别登记），无法唯一判定时回抛候选 → LLM ask_user；
--   · 降级边界：global 路径因授权不可见时退成"逐站分开呈现"，**不拿分站数字相加冒充全局值**，
--     也不向用户解释"因为缺权限"（作用域裁剪，与未授权站点同口径）；
--   · 时效：问"今天/实时"时 t_plus_1 / lagged 路径不参与择路。
-- ============================================================================

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_metrics'
      AND COLUMN_NAME = 'allowed_carrier') = 0,
  'ALTER TABLE adh_metrics ADD COLUMN allowed_carrier ENUM(''any'',''detail'',''global'',''log'',''mirror'') NOT NULL DEFAULT ''any'' COMMENT ''该口径允许的取数路径类型：any=不限 | detail=仅各站明细 | global=仅全局汇总(可算全集去重/全局比率) | log=仅日志源 | mirror=仅镜像；择路在 topology.decide_carrier 消费''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 接口权限：本批不新增端点（读写走既有 /api/metrics 与 AS-BOT save_metric），
-- 因此不登记新权限码、不动菜单（避免"配了没用"的漂移）。

-- ----------------------------------------------------------------------------
-- 核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- SELECT 'column' AS chk, COLUMN_TYPE, COLUMN_DEFAULT FROM information_schema.COLUMNS
--  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_metrics' AND COLUMN_NAME = 'allowed_carrier';
-- 消歧建议清单（同一业务名被多个口径共用时，应拆行并把 allowed_carrier 分别钉死）：
-- SELECT 'ambiguous_metric_name' AS chk, name, COUNT(*) c, GROUP_CONCAT(id) ids
--   FROM adh_metrics WHERE is_active = 1 GROUP BY name HAVING c > 1;
