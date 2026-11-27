-- ============================================================================
-- P3：受控能力流程（capability flow）数据模型 + capability:run 权限（幂等, 无 USE）
--
-- 为什么需要本迁移（ontology-modeling §10.6 执行边界）：
--   1. flow 默认是声明式计划；受控自动执行（execution.mode=auto + 白名单 + 特性开关）
--      需要可审计的运行记录：每一步谁执行、过没过权限、停没停人工，都要能还原；
--   2. 自动执行必须单步单 worker（条件更新 + 租约），并发只执行一次；
--   3. 人工步骤停在 waiting_human，AI 不得代做；发布后由流程读内容服务状态复核，
--      不能信任客户端"已完成"；
--   4. capability:run 只是"创建/观察/取消流程"的编排入口权限，不是执行权限 ——
--      每个自动步骤仍由该动作原有权限码再次裁决。
--
-- 边界：
--   * 事件表只存业务引用与安全摘要（不复制正文/凭据/SQL/基础设施地址）；
--   * runs.input_json 存运行输入（用户提供的业务参数，步骤绑定表达式取值用）；
--   * 授权只给持有 ontology:save 的现有系统治理角色（INSERT IGNORE，不批量开放）；
--   * 同步/DAG/UDF/物化等数据工程动作永远进不了流程（契约层 auto_executable=false）。
-- ============================================================================

-- 1) 流程运行：场景、本体指纹、发起人、状态机、幂等键、租约
CREATE TABLE IF NOT EXISTS adh_capability_flow_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  scenario_key VARCHAR(128) NOT NULL COMMENT '系统本体场景 key',
  scenario_title VARCHAR(191) NOT NULL DEFAULT '' COMMENT '场景业务名（展示用）',
  ontology_fingerprint VARCHAR(64) NOT NULL DEFAULT '' COMMENT '启动时固定的 contract_digest',
  initiated_by BIGINT NOT NULL COMMENT '发起人 user_id（服务端注入）',
  initiated_by_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '发起人业务名',
  workspace_id BIGINT NOT NULL DEFAULT 0 COMMENT '可信工作空间 id',
  status ENUM('pending','running','waiting_human','succeeded','failed','cancelled',
              'compensating','compensated') NOT NULL DEFAULT 'pending',
  current_step INT NOT NULL DEFAULT 0 COMMENT '当前/最后推进到的步序',
  input_json JSON COMMENT '运行输入（业务参数；步骤 input_bindings 取值源）',
  output_json JSON COMMENT '运行输出摘要（安全字段，不存凭据/正文）',
  idempotency_key VARCHAR(191) NOT NULL COMMENT '客户端幂等键',
  lease_owner VARCHAR(64) NOT NULL DEFAULT '' COMMENT '推进 worker 标识',
  lease_until DATETIME NULL COMMENT '租约到期（过期可被其它 worker 接管）',
  error_code VARCHAR(64) NOT NULL DEFAULT '' COMMENT '安全错误码',
  error_message VARCHAR(500) NOT NULL DEFAULT '' COMMENT '对外安全错误；原始堆栈只进服务端日志',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  UNIQUE KEY uk_cap_flow_run_idem (scenario_key, initiated_by, idempotency_key),
  INDEX idx_cap_flow_run_status (status, created_at),
  INDEX idx_cap_flow_run_ws (workspace_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能力流程运行（场景执行实例，状态机见 capability_flow_service）';

-- 2) 流程步骤：单步单 worker（条件更新 + 租约），重试计数与执行契约快照
CREATE TABLE IF NOT EXISTS adh_capability_flow_steps (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id BIGINT NOT NULL COMMENT '所属运行',
  step_no INT NOT NULL COMMENT '步序（与场景 flow.order 对应）',
  action_key VARCHAR(128) NOT NULL COMMENT '动作 key（精确解析，不模糊匹配）',
  action_label VARCHAR(128) NOT NULL DEFAULT '' COMMENT '动作业务名（展示用）',
  channel ENUM('ai','human') NOT NULL DEFAULT 'ai' COMMENT '执行通道',
  status ENUM('pending','running','waiting_human','succeeded','failed','skipped',
              'cancelled') NOT NULL DEFAULT 'pending',
  input_json JSON COMMENT '步骤输入（绑定表达式求值结果）',
  output_ref VARCHAR(191) NOT NULL DEFAULT '' COMMENT '输出摘要引用（不存正文）',
  perm_code VARCHAR(64) NOT NULL DEFAULT '' COMMENT '执行时裁决的权限码',
  executor_ref VARCHAR(128) NOT NULL DEFAULT '' COMMENT '契约 executor_ref（审计快照）',
  attempts INT NOT NULL DEFAULT 0 COMMENT '尝试次数（重试仅限显式瞬态错误）',
  lease_owner VARCHAR(64) NOT NULL DEFAULT '',
  lease_until DATETIME NULL,
  error_code VARCHAR(64) NOT NULL DEFAULT '',
  error_message VARCHAR(500) NOT NULL DEFAULT '',
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_cap_flow_step (run_id, step_no),
  INDEX idx_cap_flow_step_status (status, lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能力流程步骤（条件更新 + 租约保证单步只执行一次）';

-- 3) 流程事件：追加式状态转换与审计（只留业务引用与摘要）
CREATE TABLE IF NOT EXISTS adh_capability_flow_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id BIGINT NOT NULL,
  step_no INT NOT NULL DEFAULT 0 COMMENT '0 = 运行级事件',
  event_type VARCHAR(64) NOT NULL COMMENT 'created/started/step_succeeded/step_failed/waiting_human/cancelled/...',
  detail VARCHAR(500) NOT NULL DEFAULT '' COMMENT '安全摘要（不复制正文/凭据/SQL）',
  actor VARCHAR(64) NOT NULL DEFAULT '' COMMENT '服务端注入的操作者/worker',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_cap_flow_event_run (run_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能力流程事件账（追加式，不改写）';

-- 4) capability:run 权限：编排入口（创建/观察/取消流程），不是执行权限。
--    ai_access=write + ai_action_key=capability.run_flow（AI 经 run_capability_flow 工具
--    启动流程；每步仍由动作自身权限码再次裁决，缺任一步权限则创建前即拒）。
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method,
     menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('capability:run', '运行能力流程', 'data', '数据中台',
     '创建/观察/取消受控能力流程（编排入口权限，不是执行权限）',
     '/api/capability-flows*', 'GET,POST', 'data:ontology', 2140, 1,
     'write', 'capability.run_flow',
     '编排入口：创建流程前预检全部自动步骤权限，缺任一权限直接拒绝；'
     '人工步骤停 waiting_human 由人完成；同步/DAG/UDF/物化动作永远进不了流程')
ON DUPLICATE KEY UPDATE
    api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
    menu_key = VALUES(menu_key), is_active = 1,
    ai_access = VALUES(ai_access), ai_action_key = VALUES(ai_action_key),
    ai_note = VALUES(ai_note);

-- 5) 存量角色默认授权：只授予持有 ontology:save 的现有系统治理角色
--    （与 plan 3.2 一致：INSERT IGNORE 给系统治理角色，不批量开放给所有角色；
--     uk_role_perm(role_id, perm_code) 唯一键保证幂等可重跑）。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT DISTINCT rp.role_id, 'capability:run', NOW()
FROM adh_role_perms rp
WHERE rp.perm_code = 'ontology:save';

-- 核验（应用后执行）：
-- SHOW TABLES LIKE 'adh_capability_flow_%';
-- SELECT perm_code, ai_access, ai_action_key, menu_key FROM adh_perm_registry
--   WHERE perm_code = 'capability:run';
-- SELECT r.name, rp.perm_code FROM adh_role_perms rp JOIN adh_roles r ON r.id = rp.role_id
--   WHERE rp.perm_code = 'capability:run' ORDER BY r.id;
