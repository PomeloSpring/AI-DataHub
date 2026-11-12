-- =====================================================================
-- 遗留看板（Test #1779849441251 / 数字化大屏 #1780540626928）9 张图改挂现存可查数据集
--
-- 背景（2026-11-10 排查「操作记录流水明细刷新失败」时连带查出）：
--   这批图挂的 ds#25~29 正文引用的 Doris 表（dwd_t_case_records_all /
--   dwd_observability_rum）已不存在，是**确定性失败**（重试永远不好），看板长期空图。
--   对外文案又把它说成"数据源连接/凭据不可用，稍后重试"，把方向带偏（那部分另批已修）。
--
-- 口径：按"同一个问题、换到现存可查的数据集"改挂；列名绑定随之改写；
--   **图表标题一并改掉**——挂在别的口径上却留着原标题就是骗人（no-silent-degradation）。
--   映射依据（逐条实跑核验过出数）：
--     ds#25 各站点案例月统计增长率(Doris 缺表) → ds#37 App 使用·按天案例与用户趋势(ES, 可查)
--     ds#26 按创建月份统计案例数量(Doris 缺表) → ds#35 App 使用·按应用案例数与用户数(ES, 可查)
--     ds#27 用户月操作日志量(Doris 缺表)        → ds#30 用户月操作日志量-2(SLS, 同题重写版, 可查)
--     ds#28 近1个月关键路径转化率(Doris 缺表)   → ds#31 同名-2(SLS, 同题重写版, 列名一致, 可查)
--     ds#29 表值图=RUM 明细(Doris 缺表)         → ds#7 arms_rum_observability(SLS RUM 明细, 可查)
--   注：ds#37 是**按天**、ds#35 是**按应用**，与原"按月/按站点增长率"粒度不同，故标题改口径；
--       增长率与 prev_count 两列现存数据集没有，线图上不再出现（不编造第二份算法凑数）。
--
-- 幂等：每条都以"仍挂在旧数据集"为条件，重跑不产生二次变化；data_cache 一并清空，
--   避免旧口径的缓存行在新标题下继续被当成首屏预览。
-- =====================================================================

-- ① 案例趋势（线）：Test
UPDATE adh_charts
   SET source_id   = 37,
       name        = '按天案例数趋势',
       config      = JSON_SET(COALESCE(NULLIF(config, ''), '{}'),
                              '$.xCol', 'stat_day', '$.yCol', 'case_quantity'),
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780317379089 AND source_id = 25 AND (config IS NULL OR JSON_VALID(config));

-- ①' 案例趋势（线）：数字化大屏
UPDATE adh_charts
   SET source_id   = 37,
       name        = '按天案例数趋势',
       config      = JSON_SET(COALESCE(NULLIF(config, ''), '{}'),
                              '$.xCol', 'stat_day', '$.yCol', 'case_quantity'),
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780540627165 AND source_id = 25 AND (config IS NULL OR JSON_VALID(config));

-- ② 案例数量（柱）：Test —— 原"按创建月份"，现存口径是"按应用"
UPDATE adh_charts
   SET source_id   = 35,
       name        = '按应用案例数',
       config      = JSON_SET(COALESCE(NULLIF(config, ''), '{}'),
                              '$.xCol', 'app_code', '$.yCol', 'case_quantity'),
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780317441923 AND source_id = 26 AND (config IS NULL OR JSON_VALID(config));

-- ②' 案例数量（柱）：数字化大屏
UPDATE adh_charts
   SET source_id   = 35,
       name        = '按应用案例数',
       config      = JSON_SET(COALESCE(NULLIF(config, ''), '{}'),
                              '$.xCol', 'app_code', '$.yCol', 'case_quantity'),
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780540627554 AND source_id = 26 AND (config IS NULL OR JSON_VALID(config));

-- ③ 用户月操作日志量（大数字趋势）：Test —— 列名自动检测，只换源
UPDATE adh_charts
   SET source_id   = 30,
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780465901000 AND source_id = 27;

-- ④ 饼图（原挂在同一份月日志量上，按年月占比）：Test
UPDATE adh_charts
   SET source_id   = 30,
       name        = '按月操作日志量占比',
       config      = JSON_SET(COALESCE(NULLIF(config, ''), '{}'),
                              '$.xCol', 'month_str', '$.yCol', 'cnt'),
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780491475624 AND source_id = 27 AND (config IS NULL OR JSON_VALID(config));

-- ⑤ 关键路径转化率漏斗：Test —— ds#31 列名与 ds#28 一致（step_name/user_count），只换源
UPDATE adh_charts
   SET source_id   = 31,
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780466186288 AND source_id = 28;

-- ⑥ RUM 明细表值图：Test
UPDATE adh_charts
   SET source_id   = 7,
       name        = 'RUM 观测明细',
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780490671495 AND source_id = 29;

-- ⑥' RUM 明细表值图：数字化大屏
UPDATE adh_charts
   SET source_id   = 7,
       name        = 'RUM 观测明细',
       data_cache  = NULL,
       updated_at  = NOW()
 WHERE id = 1780540628521 AND source_id = 29;

-- 核验（人工执行）：
--   SELECT id, name, source_id FROM adh_charts
--    WHERE dashboard_id IN (1779849441251, 1780540626928) AND source_type='dataset' ORDER BY id;
--   期望 source_id 只出现 7/30/31/35/37，且不再有 25~29。
