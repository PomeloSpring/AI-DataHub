-- =====================================================================
-- 「设备使用（扫描仪分析）」三张耗时/记录数 KPI 卡挪进「设备性能（操作画像）」，并让前者 tab 退役
--
-- 背景（2026-11-11 需求）：
--   统计分析（BI）组（gid=5）里「设备使用（扫描仪分析）」#1793969418496 只有 4 张 KPI 卡
--   （全部挂数据集 ds#43「设备使用·活跃与平均耗时」）。其中 扫描平均耗时 / 单次优化最大耗时均值 /
--   操作记录数 本质是**性能与操作画像**指标，与「设备性能（操作画像）」#1793969438727 的
--   活跃设备数、环境分布、画像明细同源同问题；拆在两页还得各配一套筛选控件。
--   故：三张卡挪进设备性能页凑成一行 4 卡 KPI，设备使用页整体删除。
--
-- 口径：
--   ① 只挪数据图表，数据集绑定（source_type/source_id=43）与 config.yCol 一律不动 —— 口径没变，只是换页；
--   ② 目标页 KPI 行统一为 48 列网格 4 卡各 12 列、同 y=12/h=41（原「活跃设备数」独占 w=14 一并收窄）；
--      position(像素) 与 config.pageLayout(列/行) 同批改写，大屏/画布口径与页面布局保持一致
--      （列宽 =(1440+16)/48=30.33px、行高 4px、渲染宽 = span×列宽 − gap16）；
--   ③ 参数无需迁移：两页 pageParams 同为 start_time/end_time，且都有 区域(site) /
--      设备序列号(serial_number) 控件，ds#43 的入参在目标页同样能绑定（不会退化成按默认口径出数）；
--   ④ 设备使用页连同剩余 6 张页面控件、组成员登记、角色授权一并删除
--      （adh_dashboard_group_items / adh_role_dashboard_access 对 adh_dashboards **无外键级联**，
--       不清就留悬空 dashboard_id，组里出现"配了但打不开"的死 tab）。
--
-- 幂等：①②以"仍挂在这两个看板之一"为条件，重跑终态一致（已被人工挪去第三看板的不回拖）；
--   ③④按 id 删除，重跑 0 行受影响。回滚凭执行前备份（见交付说明），本文件不做自动回退。
-- =====================================================================

-- ① 目标页原有「活跃设备数」收窄到 12 列，为四卡并排腾位
UPDATE adh_charts
   SET config     = '{"limit": 100, "yCol": "active_quantity", "pageLayout": {"version": 3, "desktop": {"x": 0, "y": 12, "w": 12, "h": 41}}}',
       position   = '{"x": 0, "y": 48, "w": 344, "h": 164}',
       updated_at = NOW()
 WHERE id = 1793970945242 AND dashboard_id = 1793969438727;

-- ② 扫描平均耗时（秒）→ 设备性能页 KPI 行第 2 格
UPDATE adh_charts
   SET dashboard_id = 1793969438727,
       config       = '{"limit": 100, "yCol": "scan_avg_seconds", "pageLayout": {"version": 3, "desktop": {"x": 12, "y": 12, "w": 12, "h": 41}}}',
       position     = '{"x": 364, "y": 48, "w": 344, "h": 164}',
       updated_at   = NOW()
 WHERE id = 1793969419339 AND dashboard_id IN (1793969418496, 1793969438727);

-- ②' 单次优化最大耗时均值（秒）→ 第 3 格
UPDATE adh_charts
   SET dashboard_id = 1793969438727,
       config       = '{"limit": 100, "yCol": "refine_max_avg_seconds", "pageLayout": {"version": 3, "desktop": {"x": 24, "y": 12, "w": 12, "h": 41}}}',
       position     = '{"x": 728, "y": 48, "w": 344, "h": 164}',
       updated_at   = NOW()
 WHERE id = 1793969419592 AND dashboard_id IN (1793969418496, 1793969438727);

-- ②'' 操作记录数 → 第 4 格
UPDATE adh_charts
   SET dashboard_id = 1793969438727,
       config       = '{"limit": 100, "yCol": "record_count", "pageLayout": {"version": 3, "desktop": {"x": 36, "y": 12, "w": 12, "h": 41}}}',
       position     = '{"x": 1092, "y": 48, "w": 344, "h": 164}',
       updated_at   = NOW()
 WHERE id = 1793969419852 AND dashboard_id IN (1793969418496, 1793969438727);

-- ④ 设备使用页退役：先清子表，再清看板行（无外键级联，顺序不能反）
DELETE FROM adh_charts WHERE dashboard_id = 1793969418496;
DELETE FROM adh_dashboard_group_items WHERE dashboard_id = 1793969418496;
DELETE FROM adh_role_dashboard_access WHERE dashboard_id = 1793969418496;
DELETE FROM adh_dashboards WHERE id = 1793969418496;

-- 核验（人工看结果，不自动改）：
--   SELECT id,name,dashboard_id FROM adh_charts WHERE dashboard_id=1793969438727 ORDER BY id;
--     期望 9 行：5 控件 + 4 KPI（活跃设备数/扫描平均耗时/单次优化最大耗时均值/操作记录数）
--            + 按操作系统的设备数/记录数 + 操作画像明细；且 KPI 四卡 pageLayout.desktop 为 x=0/12/24/36, y=12, w=12, h=41
--   SELECT id,name FROM adh_dashboards WHERE id=1793969418496;                 期望空
--   SELECT dashboard_id FROM adh_dashboard_group_items WHERE group_id=5;       期望不含 1793969418496
--   SELECT JSON_VALID(config) FROM adh_charts WHERE dashboard_id=1793969438727; 期望全 1
