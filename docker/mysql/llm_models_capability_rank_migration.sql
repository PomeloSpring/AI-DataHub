-- ============================================================================
-- adh_llm_models 增「能力序」列 capability_rank
--
-- 背景：模型中心与对话里的模型候选原先按 `is_default DESC, name` 排 —— 名称字典序
--       把「旗舰」和「最省」混成一列，用户与管理员都看不出该选哪个（用户诉求：
--       「多配置几个大模型，按照能力排序一下」）。排序必须有事实源，不能靠前端
--       硬编码模型名清单（一改就漂移，且新登记的模型永远沉不出正确位置）。
--
-- 口径：
--   * 1 = 能力最强，数字越大越弱/越省；未定档的行取 99（新登记默认沉底，
--     由管理员或登记脚本显式定档，不猜它的能力）。
--   * 读路径统一 `ORDER BY is_default DESC, capability_rank ASC, name`
--     （默认项仍置顶，其余按能力序；name 只作同档兜底）。
--   * 本迁移只加列，不写任何模型名 —— 具体档位由登记脚本/模型中心维护。
--
-- 幂等：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，先查 information_schema 判定
--       列是否已存在，重复执行安全。
-- ============================================================================

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_llm_models'
      AND COLUMN_NAME = 'capability_rank'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_llm_models ADD COLUMN capability_rank INT NOT NULL DEFAULT 99 COMMENT ''能力序：1=最强，数字越大越弱/越省；99=未定档（读路径按此排序，登记时显式定档）''',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
