-- 本体知识库同步水位线 (T9 同步通道稳定性)
-- 记录每个 active 本体模型最后一次成功/失败同步到 qmind 的版本, 供定时对账判断漂移:
-- 本地模型 updated_at 与 synced_version 不一致(或无记录/上次失败) → 对账重推。
-- 静默失败不再"只记日志": 结果与错误写入本行, 可被巡检/告警读取。

CREATE TABLE IF NOT EXISTS adh_ontology_kb_sync_state (
  model_id       BIGINT NOT NULL PRIMARY KEY,
  datasource_id  BIGINT NOT NULL DEFAULT 0,
  notebook_id    VARCHAR(128) NOT NULL DEFAULT '',
  synced_version VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '成功同步时的模型 updated_at',
  status         ENUM('success','failed','removed') NOT NULL DEFAULT 'success',
  error          VARCHAR(512) DEFAULT '',
  synced_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
