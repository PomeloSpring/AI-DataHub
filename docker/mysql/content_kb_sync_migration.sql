-- ============================================================================
-- 内容中心 → 知识库语料同步（发布后的门户内容可推给 AS-BOT 检索）
--
-- 需求（用户 2026-11-05）：内容管理要能同步到知识库，并且**能选择目标知识库**。
--
-- 这张表不是新建的：`content_center_migration.sql` §5 早就把它作为「同步水位线」占位
-- 建好（注释写着"P1 启用；表先建好，避免 P1 再改结构"）。本迁移**沿用它的语义**
-- —— 一次同步尝试一行（article_id, kb_id, revision_id 的水位线），最新一行就是当前
-- 归属，历史行留着做排障与"上次同步失败"可见。
--
-- 但占位形状缺了三样实现必需的东西，必须补列（不是改设计，是占位时还不知道的事实）：
-- 1. `source_id`：云端**没有原地更新接口**（保存=传新件+删旧件），不记住上次生成的
--    云端文档 id 就无法"更新同一篇"，只会一次次堆副本。
-- 2. `doc_title`：显示与冲突判定用（云端同名文档不是本通道生成时不许覆盖）。
-- 3. `kb_name`：占位只有 `kb_id`。知识库删除重建后 id 会变而业务名不变（与本体绑定
--    以 datasource_name 为准同一条纪律），所以业务名必须落表；`kb_id` 保留继续作为
--    与 `adh_knowledge_bases` 的关联列，且**不外泄给前端**。
--
-- 行为口径（表结构之外的两条，写在这里避免实现时飘）：
-- · 一篇同一时刻只对一个库（用户 2026-11-05 拍板）：同步到新库时旧库里那篇会被撤掉，
--   撤不掉必须落 failed/warning 并显示出来 —— 两处同名语料会被同时检索命中。
-- · `state` 沿用占位枚举 pending|synced|failed|withdrawn|withdraw_failed。
--
-- 幂等：可重复执行。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_content_kb_sync (
  id          BIGINT      NOT NULL AUTO_INCREMENT,
  article_id  BIGINT      NOT NULL COMMENT '内容条目 id',
  kb_id       BIGINT      NOT NULL COMMENT '目标知识库(adh_knowledge_bases.id, 不外泄给前端)',
  kb_name     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '目标知识库业务名(删除重建后 id 会变、name 不变)',
  revision_id BIGINT      NOT NULL DEFAULT 0 COMMENT '本次同步的正文版本(adh_content_revisions.id)',
  state       VARCHAR(24) NOT NULL DEFAULT 'pending' COMMENT 'pending|synced|failed|withdrawn|withdraw_failed',
  error       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '失败原因(含 degraded 事实)',
  source_id   VARCHAR(64) NOT NULL DEFAULT '' COMMENT '云端文档 id(云端无原地更新, 每次同步都会换新 id)',
  doc_title   VARCHAR(255) NOT NULL DEFAULT '' COMMENT '云端文档标题(展示与冲突判定用)',
  parent_source_id VARCHAR(64) NOT NULL DEFAULT '' COMMENT '库内目录(空=根). 云端内部标识, 只服务端用',
  content_hash CHAR(64)   NOT NULL DEFAULT '' COMMENT '本次同步的语料正文 sha256(幂等判定)',
  synced_by   VARCHAR(64) NOT NULL DEFAULT '',
  synced_at   DATETIME    NULL,
  updated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_attempt (article_id, kb_id, revision_id),
  KEY idx_article_id (article_id),
  KEY idx_state (state),
  KEY idx_kb_name (kb_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容→知识库语料同步水位线';

-- ----------------------------------------------------------------------------
-- 存量库（占位形状）对齐：补列 + 换主键
--   占位表 PK 是 (article_id, kb_id, revision_id)：同一版本的重试会撞主键，
--   而每次尝试都必须能留痕（尤其失败要能反复记），所以改成自增 id + 普通索引。
--   仅在表为空时才动主键（有数据就停下让人决定，不静默改历史结构）。
-- ----------------------------------------------------------------------------
DELIMITER $$
DROP PROCEDURE IF EXISTS adh_ckbs_add_column$$
CREATE PROCEDURE adh_ckbs_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

DROP PROCEDURE IF EXISTS adh_ckbs_add_index$$
CREATE PROCEDURE adh_ckbs_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                   WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

-- 占位 PK 是复合键（无自增 id）时才重建；已经是 id 主键就跳过
DROP PROCEDURE IF EXISTS adh_ckbs_realign$$
CREATE PROCEDURE adh_ckbs_realign()
BEGIN
    DECLARE has_id TINYINT DEFAULT 0;
    DECLARE rows_n BIGINT DEFAULT 0;
    SELECT COUNT(*) INTO has_id FROM information_schema.columns
      WHERE table_schema=DATABASE() AND table_name='adh_content_kb_sync' AND column_name='id';
    SELECT COUNT(*) INTO rows_n FROM adh_content_kb_sync;
    IF has_id = 0 AND rows_n = 0 THEN
        -- 空表：直接按新形状重建（不搬数据，无需迁移）
        DROP TABLE adh_content_kb_sync;
        CREATE TABLE adh_content_kb_sync (
          id          BIGINT      NOT NULL AUTO_INCREMENT,
          article_id  BIGINT      NOT NULL,
          kb_id       BIGINT      NOT NULL,
          kb_name     VARCHAR(128) NOT NULL DEFAULT '',
          revision_id BIGINT      NOT NULL DEFAULT 0,
          state       VARCHAR(24) NOT NULL DEFAULT 'pending',
          error       VARCHAR(512) NOT NULL DEFAULT '',
          source_id   VARCHAR(64) NOT NULL DEFAULT '',
          doc_title   VARCHAR(255) NOT NULL DEFAULT '',
          parent_source_id VARCHAR(64) NOT NULL DEFAULT '',
          content_hash CHAR(64)   NOT NULL DEFAULT '',
          synced_by   VARCHAR(64) NOT NULL DEFAULT '',
          synced_at   DATETIME    NULL,
          updated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (id),
          KEY idx_attempt (article_id, kb_id, revision_id),
          KEY idx_article_id (article_id),
          KEY idx_state (state),
          KEY idx_kb_name (kb_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容→知识库语料同步水位线';
    ELSEIF has_id = 0 AND rows_n > 0 THEN
        -- 有历史水位线且是老形状：不自动改结构，显式报错让人决策（宁阻断勿静默改坏历史）
        SIGNAL SQLSTATE '45000'
          SET MESSAGE_TEXT = 'adh_content_kb_sync 仍是占位形状且有数据：请人工决策后再迁移（不自动改历史结构）';
    END IF;
END$$
DELIMITER ;

CALL adh_ckbs_realign();
DROP PROCEDURE adh_ckbs_realign;

CALL adh_ckbs_add_column('adh_content_kb_sync', 'kb_name',
                         "VARCHAR(128) NOT NULL DEFAULT '' COMMENT '目标知识库业务名'");
CALL adh_ckbs_add_column('adh_content_kb_sync', 'source_id',
                         "VARCHAR(64) NOT NULL DEFAULT '' COMMENT '云端文档 id'");
CALL adh_ckbs_add_column('adh_content_kb_sync', 'doc_title',
                         "VARCHAR(255) NOT NULL DEFAULT '' COMMENT '云端文档标题'");
CALL adh_ckbs_add_column('adh_content_kb_sync', 'parent_source_id',
                         "VARCHAR(64) NOT NULL DEFAULT '' COMMENT '库内目录(空=根)'");
CALL adh_ckbs_add_column('adh_content_kb_sync', 'content_hash',
                         "CHAR(64) NOT NULL DEFAULT '' COMMENT '语料正文 sha256'");
CALL adh_ckbs_add_column('adh_content_kb_sync', 'synced_by',
                         "VARCHAR(64) NOT NULL DEFAULT ''");

CALL adh_ckbs_add_index('adh_content_kb_sync', 'idx_article_id', 'INDEX `idx_article_id` (article_id)');
CALL adh_ckbs_add_index('adh_content_kb_sync', 'idx_kb_name', 'INDEX `idx_kb_name` (kb_name)');
