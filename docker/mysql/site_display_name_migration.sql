-- ============================================================================
-- 站点业务名改名（幂等：按 site_code 定位，可重复执行）
--
-- 为什么要单独一个迁移而不是改 site_scope_migration.sql 的种子：
-- 种子的 `ON DUPLICATE KEY UPDATE` **故意不回写 display_name**（管理员改名或停用后
-- 重跑迁移不得被覆盖，见 site_scope_migration.sql §1）。所以"改名"必须是一条
-- 显式的 UPDATE，而不是指望重建种子。
--
-- 改名后的口语兼容在代码侧收口：站点可解析别名由 display_name 反推
-- （site_service._alias_index()），改名会让用户/LLM 再说旧名时解析失败，
-- 因此 _LEGACY_SITE_ALIASES 把旧写法继续映射到同一 site_code。
-- ============================================================================

-- 聚合态：全球站点 → 全部站点（"全球"读起来像一个地理范围专名，
-- 实际语义是"我当前可查的站点全部各查一遍"）。
UPDATE adh_sites SET display_name = '全部站点'
 WHERE site_code = 'global' AND display_name <> '全部站点';

-- 新加坡部署：国际站点(新加坡) → 新加坡站点（括号后缀是内部解释，界面上
-- 一长串挤在站点选择器里读不清；站点名直接说部署地即可）。
UPDATE adh_sites SET display_name = '新加坡站点'
 WHERE site_code = 'int' AND display_name <> '新加坡站点';
