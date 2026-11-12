-- ============================================================================
-- 报表正文转对象存储迁移（DB 只存对象 key，正文出库）
--
-- 背景：报表正文原先整份存在 adh_reports.content（LONGTEXT）。语义层/会话/定时任务
--       产出的报表正文可以到几十 KB～更大，把大文本堆在元数据库里既撑大备份、
--       又让"报表清单"这类只读元信息的查询陪着扫长行。平台已有 S3 兼容对象存储
--       （backend/common/object_storage.py，归档资产与内容附件都是「DB 存 key、
--       读取时拼域名+签名」的同一形态），报表正文向它收敛。
-- 方案：新增 content_object_key（唯一事实源）+ content_size（核验/排障用）。
--       content 列**保留但退役**：迁移后一律写空串，不再作为正文来源。
--       读路径只认 key —— key 为空而 content 非空 = 该行还没搬，显式提示跑迁移脚本，
--       不回退去读 DB 正文（双真值源迟早飘，见 ontology-modeling §1 同一纪律）。
-- 数据搬迁：本文件只做 DDL；存量正文由 backend/scripts/migrate_report_content_to_oss.py
--       搬运（默认 dry-run，--apply 才写；幂等可重跑）。**先跑本迁移建列，再跑搬运脚本，
--       最后发布应用代码** —— 顺序反了会让新代码读到空 key 而报表中心打不开。
-- 约定：不含 USE，用 `mysql ... <METADATA_DB_DATABASE> < 本文件` 施加；
--       DDL 幂等（information_schema 守卫），可重复执行。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_rcoss_add_column$$
CREATE PROCEDURE adh_rcoss_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
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
DELIMITER ;

-- ----------------------------------------------------------------------------
-- 1. 正文对象 key + 字节数
--    key 布局 {prefix}/{user_id}/reports/{report_id}.{md|html}，唯一构建点是
--    object_storage.build_report_content_key（读写两侧都不得自行拼 key）。
--    存 key 而不是存完整 URI：域名随部署走配置（OBJECT_STORAGE_ENDPOINT/BUCKET），
--    签名是每次读取现算的短期授权 —— 落库的 URI 会把域名焊死并让签名过期变成死链。
-- ----------------------------------------------------------------------------
CALL adh_rcoss_add_column('adh_reports', 'content_object_key',
    'VARCHAR(512) NOT NULL DEFAULT '''' COMMENT ''正文对象存储 key(唯一事实源; 空=正文尚未搬迁)''');
CALL adh_rcoss_add_column('adh_reports', 'content_size',
    'BIGINT NOT NULL DEFAULT 0 COMMENT ''正文字节数(迁移核验与排障用, 不参与展示)''');

-- 搬迁进度核验：未迁移行数应为 0（人工看输出，本文件不改数据）
--   SELECT COUNT(*) AS pending FROM adh_reports
--    WHERE content_object_key='' AND content IS NOT NULL AND content<>'';
