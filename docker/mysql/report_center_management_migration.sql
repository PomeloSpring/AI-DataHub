-- ============================================================================
-- 报表中心「管理报表」迁移（内容型报表 + 来源分型）
--
-- 背景：报表中心原先只承载「定时任务成功执行产出的报告」，且所有查看都要过
--       数据权限快照（report_access.snapshot_is_current）。两件事让它不可用：
--       1) 人写的/会话里存下来的分析结论没有快照 → 属主自己也打不开；
--       2) 存量 M2 之前的行 generation_status='legacy'、security_context 为 NULL，
--          同样永远打不开，报表中心只剩一个空壳列表。
-- 方案：给报表加来源分型 source_type，把报表分成两类（口径见 report_access）：
--       · 数据派生（scheduled / analysis）：由受治理取数产出，继续要求权限快照，
--         快照失效即不可读（防历史重放），不可在线编辑正文。
--       · 内容型（manual / conversation / legacy）：正文是人写或会话里已展示给
--         属主本人的内容，属主在应用内直接实时查看，不参与外链/公开分享。
-- 不猜历史：新增列全部保守默认（source_type='legacy' = 未知来源按内容型处理，
--       只对属主可见，不会扩大任何人的可见范围）；存量行只按已有事实重分类。
-- 约定：不含 USE，用 `mysql ... <METADATA_DB_DATABASE> < 本文件` 施加；
--       DDL 幂等（information_schema 守卫），可重复执行。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_rc_add_column$$
CREATE PROCEDURE adh_rc_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_rc_add_index$$
CREATE PROCEDURE adh_rc_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.statistics
               WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_rc_allow_null$$
CREATE PROCEDURE adh_rc_allow_null(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col
                 AND IS_NULLABLE='NO') THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` MODIFY COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ----------------------------------------------------------------------------
-- 0. 修正部署库与仓库定义的漂移：task_id 被建成 NOT NULL
--    仓库定义（trusted_execution_migration.sql）是 `task_id BIGINT NULL`，
--    部署库却是 NOT NULL —— 任何“不是定时任务产出”的报表（会话保存/手动新建/
--    分析来源异步生成）都插不进去（1048 Column 'task_id' cannot be null）。
-- ----------------------------------------------------------------------------
CALL adh_rc_allow_null('adh_reports', 'task_id', 'BIGINT NULL');

-- ----------------------------------------------------------------------------
-- 1. adh_reports：来源分型 + 修改时间（报表跟随属主，跨工作站清单按人+时间取）
-- ----------------------------------------------------------------------------
CALL adh_rc_add_column('adh_reports', 'source_type',
    'VARCHAR(20) NOT NULL DEFAULT ''legacy'' COMMENT ''报表来源分型: scheduled/analysis=数据派生(须过权限快照), manual/conversation/legacy=内容型(仅属主应用内查看)''');
CALL adh_rc_add_column('adh_reports', 'updated_at', 'DATETIME NULL COMMENT ''内容型报表最后修改时间''');
CALL adh_rc_add_index('adh_reports', 'idx_report_owner_created',
    'INDEX idx_report_owner_created (owner_id, created_at)');

-- 存量重分类（只依据已有事实，认不出的一律留 'legacy' 按内容型处理）
UPDATE adh_reports SET source_type='scheduled'
 WHERE task_id IS NOT NULL AND generation_status <> 'legacy';
UPDATE adh_reports SET source_type='analysis'
 WHERE task_id IS NULL AND generation_status NOT IN ('legacy','');
UPDATE adh_reports SET updated_at=created_at WHERE updated_at IS NULL;

-- ----------------------------------------------------------------------------
-- 2. 功能权限注册表：report:manage 现在挂两个 LLM 功能动作
--    （ai_action_key 与 api_pattern/api_method 同口径，允许逗号分隔多项）
-- ----------------------------------------------------------------------------
UPDATE adh_perm_registry
   SET ai_action_key='report.generate,report.save',
       ai_note='生成报表(异步取数产出) + 保存报表(把已展示的分析结论存为内容型报表); 输出不含正文以外的凭据字段'
 WHERE perm_code='report:manage' AND ai_action_key='report.generate';

-- ----------------------------------------------------------------------------
-- 3. 管理端点补 DELETE（report:manage 的 api_method 需覆盖 DELETE /api/reports/{id}）
-- ----------------------------------------------------------------------------
UPDATE adh_perm_registry SET api_method='POST,PUT,DELETE'
 WHERE perm_code='report:manage' AND api_method NOT LIKE '%DELETE%';
