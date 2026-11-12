-- ============================================================================
-- 品牌设置增列：tagline（品牌标语）
--
-- 背景：登录页品牌块（图标 + 名称 + 那行副标题）里，名称/图标走品牌设置，
--       副标题「企业级智能数据分析平台」却硬编码在 pages/Login.tsx —— 管理员
--       改了品牌名、换了图标，登录页那行字不动，品牌口径出现第二套真值源。
--       本列把标语也纳入「系统配置 → 品牌设置」，登录页与品牌设置同源同变。
--
-- 口径：
--   * 列默认值 = 存量登录页原文案，加列即保持改造前的显示效果（不是新造文案）；
--   * 空串 '' 是合法取值，语义为「不显示标语」（与 app_name 空值回落默认名不同，
--     标语没有可派生的来源，清空就是清空，登录页只显图标+名称）；
--   * 只影响展示层文案，不参与任何取数/权限判定。
--
-- 幂等：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，先查 information_schema 判定
--       列是否已存在；存量行由列 DEFAULT 自动落值，第二次执行不改动任何数据。
--       不写 USE 语句：以连接的当前库为准（元数据库名随部署而变，写死会打错库）。
-- ============================================================================

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_brand_settings'
      AND COLUMN_NAME = 'tagline'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_brand_settings ADD COLUMN tagline VARCHAR(255) NOT NULL DEFAULT ''企业级智能数据分析平台'' COMMENT ''品牌标语：登录页品牌名称下方那行字；空串=不显示（唯一真值源，前端不得硬编码兜底文案以外的第二套）''',
    'DO 0');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
