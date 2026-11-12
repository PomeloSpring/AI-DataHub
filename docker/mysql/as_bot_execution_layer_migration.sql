-- ============================================================================
-- AS-BOT 可绑定执行层（adh_as_bots.execution_layer_id）
--
-- 需求：执行层支持配置默认执行层；AS-BOT 可以选择使用哪一条，不选则用默认执行层。
-- 口径：
--   - NULL / 0 = 未指定 → 走「工作空间默认执行层」（service.pick_active_layer 唯一实现）；
--   - 指定了层 → 必须 active + healthy + 已绑定到当前工作空间，否则**显式报错**，
--     不静默换层（否则用户以为在用百炼直连，实际在用 Qoder Credits，成本与数据通道都错）。
--   - 绑定关系必须走工作空间绑定表：层级的 allowed_tools 上限（层 ∩ 工作空间）由绑定行携带，
--     允许 AS-BOT 指向未绑定的层等于绕过工作空间的能力授权。
--
-- 幂等：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，先查 information_schema 再执行。
-- ============================================================================

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bots' AND COLUMN_NAME = 'execution_layer_id'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_as_bots ADD COLUMN execution_layer_id BIGINT NULL DEFAULT NULL COMMENT ''绑定的执行层 id；NULL/0=跟随工作空间默认执行层''',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
