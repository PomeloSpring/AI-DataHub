-- ============================================================================
-- 进化中心 P1：角色域查询、版本化决策、幂等账本与异步归纳作业 (幂等, 无 USE)
--
-- 为什么需要本迁移：
--   1. 旧列表全库拉取后在前端 filter/slice，total 与翻页都不真实；
--   2. 候选/提议处置没有版本和幂等约束，并发点击可能覆盖已有结论；
--   3. 同步 /digest 只留下最近运行态，无法按 job 查询 queued/running/succeeded/failed；
--   4. 候选 evidence 历史上可能带物理字段，新查询层必须以白名单 DTO 输出。
--
-- 边界：
--   * 只补结构与索引，不改写历史结论，不伪造历史 applied_ref/decision_note；
--   * decision_version 从 1 开始，HTTP/AI 后续统一用条件 UPDATE 仲裁；
--   * 决策账本只存安全结果引用与错误码，不复制正文、SQL、凭据或基础设施地址；
--   * 角色授权仍由 adh_user_roles/adh_role_datasource_access 裁决，本迁移不扩大授权。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_evo_p1_add_column$$
CREATE PROCEDURE adh_evo_p1_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
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
DROP PROCEDURE IF EXISTS adh_evo_p1_add_index$$
CREATE PROCEDURE adh_evo_p1_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
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
DELIMITER ;

-- 1) 候选与提议的乐观版本、处置理由和查询索引。
CALL adh_evo_p1_add_column('adh_evolution_candidates', 'decision_version',
    "INT NOT NULL DEFAULT 1 COMMENT '处置版本；每次成功决策 +1，用于 HTTP/AI 条件更新'");
CALL adh_evo_p1_add_column('adh_evolution_candidates', 'decision_note',
    "VARCHAR(500) NOT NULL DEFAULT '' COMMENT '人工驳回理由或机器 stale/expired 原因'");
CALL adh_evo_p1_add_index('adh_evolution_candidates', 'idx_evo_p1_queue',
    'INDEX idx_evo_p1_queue (status, kind, datasource_name, id)');
CALL adh_evo_p1_add_index('adh_evolution_candidates', 'idx_evo_p1_source',
    'INDEX idx_evo_p1_source (status, source, datasource_name, id)');

CALL adh_evo_p1_add_column('adh_knowledge_update_proposals', 'decision_version',
    "INT NOT NULL DEFAULT 1 COMMENT '处置版本；每次成功决策 +1，用于 HTTP/AI 条件更新'");
CALL adh_evo_p1_add_index('adh_knowledge_update_proposals', 'idx_kup_p1_queue',
    'INDEX idx_kup_p1_queue (status, target_type, datasource_name, created_at, id)');
CALL adh_evo_p1_add_index('adh_knowledge_update_proposals', 'idx_kup_p1_source',
    'INDEX idx_kup_p1_source (status, source, datasource_name, id)');

-- 2) 决策账本：同一主体 + 幂等键唯一，重复请求返回首次结果。
CREATE TABLE IF NOT EXISTS adh_evolution_decisions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  subject_type ENUM('candidate','proposal') NOT NULL COMMENT '决策主体类型',
  subject_id BIGINT NOT NULL COMMENT '候选/提议内部 id，仅服务端账本使用',
  action_key VARCHAR(64) NOT NULL COMMENT '稳定动作 key',
  channel ENUM('http','ai') NOT NULL COMMENT '处置入口',
  actor VARCHAR(64) NOT NULL DEFAULT '' COMMENT '服务端身份注入的操作人',
  user_id BIGINT NOT NULL DEFAULT 0 COMMENT '可信用户 id',
  workspace_id BIGINT NOT NULL DEFAULT 0 COMMENT '可信工作空间 id',
  idempotency_key VARCHAR(191) NOT NULL COMMENT '客户端/工具调用幂等键',
  request_hash CHAR(64) NOT NULL DEFAULT '' COMMENT '同键不同请求的冲突校验',
  expected_version INT NOT NULL DEFAULT 1 COMMENT '请求时的主体版本',
  result_status VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'succeeded/conflict/failed',
  domain_ref VARCHAR(191) NOT NULL DEFAULT '' COMMENT '领域产物业务名/安全落点，不存物理标识',
  error_code VARCHAR(64) NOT NULL DEFAULT '' COMMENT '安全错误码',
  result_json JSON COMMENT '安全结果摘要；禁止正文、SQL、凭据、连接信息',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_evo_decision_idem (subject_type, subject_id, idempotency_key),
  INDEX idx_evo_decision_actor (workspace_id, user_id, created_at),
  INDEX idx_evo_decision_subject (subject_type, subject_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='进化中心决策账本（版本、幂等、通道与安全结果引用）';

-- 3) 异步归纳作业：每次 POST 都留下可查询状态，不用最近运行态冒充 job 明细。
CREATE TABLE IF NOT EXISTS adh_evolution_digest_jobs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  workspace_id BIGINT NOT NULL COMMENT '作业所属可信工作空间',
  requested_by BIGINT NOT NULL COMMENT '发起人 id（服务端注入）',
  actor VARCHAR(64) NOT NULL DEFAULT '' COMMENT '发起人业务名',
  status ENUM('queued','running','succeeded','failed') NOT NULL DEFAULT 'queued',
  error_code VARCHAR(64) NOT NULL DEFAULT '',
  error_message VARCHAR(500) NOT NULL DEFAULT '' COMMENT '对外安全错误；原始堆栈只进服务端日志',
  result_json JSON COMMENT '归纳结果摘要，不存敏感正文',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  INDEX idx_evo_digest_jobs_ws (workspace_id, id),
  INDEX idx_evo_digest_jobs_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='进化中心异步归纳作业（支持 job 查询与失败重试）';

-- 4) 权限说明与代码动作镜像保持一致；不改菜单 key、不新增角色授权。
UPDATE adh_perm_registry
SET ai_note='只读：按角色授权数据源与全局词汇查询进化队列/提议/计数；内部 id 与物理字段不下发'
WHERE perm_code='evolution:read';
UPDATE adh_perm_registry
SET ai_note='双闸第一道：候选/提议按版本与幂等键处置；动作本身仍由 ontology:save/metrics:manage/knowledge:manage 裁决'
WHERE perm_code='evolution:manage';

-- 核验（应用后执行）：
-- SHOW COLUMNS FROM adh_evolution_candidates LIKE 'decision_version';
-- SHOW COLUMNS FROM adh_knowledge_update_proposals LIKE 'decision_version';
-- SELECT perm_code, ai_access, ai_action_key, ai_note FROM adh_perm_registry
--   WHERE perm_code IN ('evolution:read','evolution:manage');
-- SELECT subject_type, subject_id, action_key, channel, result_status
--   FROM adh_evolution_decisions ORDER BY id DESC LIMIT 20;
