-- ============================================================================
-- 执行层「默认执行层」可配置（adh_execution_layers.is_default）
--
-- 背景：`manager.resolve_workspace_layer()` 读的是 `row.get("is_default")`，但
--       `adh_execution_layers` 从来没有这一列 —— 该判断恒为假，实际永远取 id 最小的
--       active 层。同时 `adh_workspace_execution_layers`（工作空间绑定 + 其 is_default）
--       的写入 API 已退役（代码注明"执行层全局生效, 不按工作空间绑定"），表内 5 行
--       属冻结遗留数据，不能再当作配置真值源。
--
-- 处置：默认层标记落在**层本体**上（与"全局生效"口径一致），并把现有生效层（id 最小
--       的 active 层）标为默认，保证改造前后选层结果不变。
--
-- 幂等：ADD COLUMN 先查 information_schema；UPDATE 只在"当前没有任何默认层"时补一次，
--       重复执行不会把管理员后来改设的默认层覆盖回去。
-- ============================================================================

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_execution_layers' AND COLUMN_NAME = 'is_default'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_execution_layers ADD COLUMN is_default TINYINT NOT NULL DEFAULT 0 COMMENT ''系统默认执行层：AS-BOT 未指定执行层时使用；全表至多一行为 1''',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 只在还没有默认层时，把"当前实际生效的那条"（active 中 id 最小）标为默认 → 行为零变化
SET @has_default = (SELECT COUNT(*) FROM adh_execution_layers WHERE is_default = 1);
SET @ddl = IF(@has_default = 0,
    'UPDATE adh_execution_layers SET is_default = 1 WHERE status = ''active''
       AND id = (SELECT min_id FROM (SELECT MIN(id) AS min_id FROM adh_execution_layers
                                    WHERE status = ''active'') t)',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 兜底不变量：至多一行为 1（历史上若出现多行，保留 id 最小的那条）
UPDATE adh_execution_layers l
JOIN (SELECT MIN(id) AS keep_id FROM adh_execution_layers WHERE is_default = 1) k
  ON l.id <> k.keep_id AND l.is_default = 1
SET l.is_default = 0;
