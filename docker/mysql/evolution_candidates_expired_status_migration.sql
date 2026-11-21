-- ---------------------------------------------------------------------------
-- 修复：adh_evolution_candidates.status 补 `expired` 值（schema 真源对齐）
--
-- 为什么需要这个迁移
-- ------------------
-- `evolution_service.recheck_candidates` 会把「长期不再命中且无人处理」的候选
-- 置为 `status='expired'`（与人工 `rejected` 区分：expired = 词不再出现，自动关单）。
-- 但本仓库 `evolution_candidates_migration.sql` 的 CREATE TABLE 里 status ENUM
-- 少这个值 —— 新环境按迁移建库后，过期分支写入会因 ENUM 越界失败，
-- 整条「候选自我清算」链路实际是死的（backlog 只进不出）。
--
-- 线上库之所以能跑，是因为历史上被手工 ALTER 过，于是形成
-- 「库比迁移文件新」的漂移。本次把真源补齐，并对存量库做幂等对齐。
--
-- 幂等 / 边界
-- -----------
-- * 先查 information_schema：表不存在或已含 `expired` 都直接 no-op；
-- * 只扩 ENUM 值，不改默认值（仍是 'pending'）、不动任何现有行；
-- * 可重复执行：第二次执行报 0 处变更；
-- * 不写 `USE <db>` —— 库由连接配置决定（不同环境库名不同）。
-- ---------------------------------------------------------------------------

SET @evo_has_table = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
);

SET @evo_has_expired = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
    AND COLUMN_NAME = 'status' AND COLUMN_TYPE LIKE '%expired%'
);

SET @evo_ddl = IF(
  @evo_has_table = 0 OR @evo_has_expired > 0,
  'DO 0',
  "ALTER TABLE `adh_evolution_candidates` MODIFY COLUMN `status`
     ENUM('pending','converted','rejected','stale','verified','expired')
     NOT NULL DEFAULT 'pending'
     COMMENT 'converted=已执行出口动作; verified=转成的评测用例已通过; stale=转出去了但用例仍失败; expired=长期不再命中由作业关单（≠ 人工 rejected）'"
);

PREPARE evo_stmt FROM @evo_ddl;
EXECUTE evo_stmt;
DEALLOCATE PREPARE evo_stmt;