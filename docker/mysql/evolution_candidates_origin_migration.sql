-- ---------------------------------------------------------------------------
-- 进化候选：补会话回链列 origin_conversation_id / origin_message_uuid
--
-- 为什么需要它
-- ------------
-- 候选表原本只有「信号管道」（source = parse_fail / feedback / script / eval_failure），
-- 没有「这个话题是在哪一轮对话里问出来的」。于是：
--   1) 审核面看到某个词只知道"有人问过"，点不进原会话，判断不了上下文；
--   2) 语义层补完之后无法回到原问题验证（"补完了还能不能答对"没有锚点）；
--   3) 无法区分"哪些缺口来自真实用户提问、哪些来自脚本/评测批次"。
--
-- 口径（2026-11-20 定，勿改回）
-- ----------------------------
-- * **不扩 source 枚举加 'chat'**：source 是信号管道，chat 是场景。同一个词在对话里既可能
--   经 parse_fail（解析不出来）进来、也可能经 feedback（点踩）进来；塞进 source 是二维压一维，
--   且会让历史统计按枚举值分裂。判定「来自真实对话」的唯一口径 = origin_conversation_id <> 0。
-- * 两列只是**回链定位**，不是对外展示的资源引用：前端呈现"查看原会话"链接，
--   不得直接铺裸 id（ui-resource-display §1）。
-- * 消息标识用 `observability.message_uuid()`（与兜底落库同一口径），不新造 message id。
--
-- 幂等 / 边界
-- -----------
-- * 先查 information_schema（库由 DATABASE() 决定，不写 `USE <db>`）；
-- * 表不存在 / 列已存在 / 索引已存在 一律 no-op（`DO 0`）；
-- * 可重复执行：第二次执行报 0 处变更；
-- * 存量行保持默认值（0 / ''，即"非对话来源"），不猜测历史归属。
-- ---------------------------------------------------------------------------

-- 1) origin_conversation_id：首次出现的对话会话 id
SET @evo_has_table = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
);
SET @evo_has_conv = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
    AND COLUMN_NAME = 'origin_conversation_id'
);
SET @evo_ddl = IF(
  @evo_has_table = 0 OR @evo_has_conv > 0,
  'DO 0',
  "ALTER TABLE `adh_evolution_candidates` ADD COLUMN `origin_conversation_id` BIGINT NOT NULL DEFAULT 0 COMMENT '首次出现的对话会话 id（0=非对话来源，如脚本/评测批次）'"
);
PREPARE evo_stmt FROM @evo_ddl;
EXECUTE evo_stmt;
DEALLOCATE PREPARE evo_stmt;

-- 2) origin_message_uuid：首次出现那轮的消息标识（观测未开启时为空串）
SET @evo_has_uuid = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
    AND COLUMN_NAME = 'origin_message_uuid'
);
SET @evo_ddl = IF(
  @evo_has_table = 0 OR @evo_has_uuid > 0,
  'DO 0',
  "ALTER TABLE `adh_evolution_candidates` ADD COLUMN `origin_message_uuid` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '首次出现那轮的消息标识（observability.message_uuid，空=未知/非对话来源）'"
);
PREPARE evo_stmt FROM @evo_ddl;
EXECUTE evo_stmt;
DEALLOCATE PREPARE evo_stmt;

-- 3) 回链查询索引（审核面按会话回溯 / 统计对话来源占比）
SET @evo_has_idx = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_evolution_candidates'
    AND INDEX_NAME = 'idx_evo_origin'
);
SET @evo_ddl = IF(
  @evo_has_table = 0 OR @evo_has_idx > 0,
  'DO 0',
  "ALTER TABLE `adh_evolution_candidates` ADD INDEX `idx_evo_origin` (`origin_conversation_id`, `origin_message_uuid`)"
);
PREPARE evo_stmt FROM @evo_ddl;
EXECUTE evo_stmt;