-- AS-BOT 动作注册投影表（幂等: CREATE IF NOT EXISTS，可重复执行）
--
-- 为什么需要：动作声明的唯一可编辑源是系统本体 canonical 的 objects[].actions[]（M2 行为建模），
-- 本表是**派生投影**（与 adh_ontology_objects 同款纪律），由
-- ontology_service.rebuild_action_registry 在 save_draft/activate 级联重建，禁止独立编辑。
-- wakers.py 的动作注册/参数校验/权限裁决统一读本表，不再硬编码动作字面量。
--
-- action_key 全局唯一（它是审批通道 create_approval 的注册键，与
-- adh_as_bot_role_actions.action_key 同宽对齐）；跨模型抢同一 key 时重建会显式报错，
-- 不静默覆盖归属。
--
-- 注意：应用实际连的元数据库是 adh2（见 services/.env 的 METADATA_DB_DATABASE）。

USE adh2;

CREATE TABLE IF NOT EXISTS adh_as_bot_action_registry (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  action_key        VARCHAR(64)  NOT NULL COMMENT '审批通道注册键（全局唯一）',
  object_key        VARCHAR(128) NOT NULL DEFAULT '' COMMENT '所属系统对象 key（M2 归属）',
  model_id          BIGINT       NOT NULL DEFAULT 0 COMMENT '来源本体模型 id',
  label             VARCHAR(256) NOT NULL DEFAULT '' COMMENT '对外展示名（业务名）',
  effect            VARCHAR(512) NOT NULL DEFAULT '' COMMENT '效果说明（人话）',
  read_only         TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '只读行为=1（分析行为，不进审批）',
  risk              VARCHAR(16)  NOT NULL DEFAULT '' COMMENT 'low|medium|high',
  requires_approval TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否需人工审批',
  params_schema_json JSON        NULL     COMMENT '参数 schema（required/ints/strs/enum…）',
  created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_action_key (action_key),
  KEY idx_ar_model (model_id),
  KEY idx_ar_object (object_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='AS-BOT 动作注册派生投影（事实源=系统本体 objects[].actions[]）';
