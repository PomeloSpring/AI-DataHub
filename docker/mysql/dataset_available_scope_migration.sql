-- ============================================================================
-- 数据集「可用数据源范围」迁移（幂等：存储过程按 information_schema 判列是否存在）
--
-- 背景：adh_datasets 原先只有 datasource_id（执行数据源）一列，一份预制 SQL 只能
--   绑死一个源。角色只有数据源 B 授权的用户，即便数据集在 B 上同样能跑，也会在
--   enforcer Step 1（源级 RBAC, fail-closed）被拒 → 该数据集对他等于不存在。
-- 本迁移新增 available_datasources：可用数据源**业务名**数组（跨源执行范围）。
--   * 存 name 而非 id：数据源 name 全局唯一（uk_datasource_name），删除重建 id 会变
--     而 name 不变（ontology-modeling §2），与本体 deploy_sites 同口径；
--   * 空/NULL = 仅执行数据源（存量行为零变化）；
--   * 生效源由服务端解析（请求可带 site/datasource 业务名），LLM 与前端一律不回 id。
--
-- 消费方：backend/modules/viz/services/dataset_service.py
--   （available_source_names / usable_sources / resolve_effective_source）
-- ============================================================================

USE adh2;

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_ds_add_column$$
CREATE PROCEDURE adh_ds_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- 可用数据源范围（业务名数组）。执行数据源(datasource_id)隐式属于该范围，无需重复登记。
CALL adh_ds_add_column('adh_datasets', 'available_datasources',
    "JSON NULL COMMENT '可用数据源业务名数组(跨源执行范围); 空=仅执行数据源'");

-- 核验：列存在即迁移到位（可重复执行）
-- SELECT column_name, column_type, column_comment FROM information_schema.columns
--  WHERE table_schema=DATABASE() AND table_name='adh_datasets' AND column_name='available_datasources';
