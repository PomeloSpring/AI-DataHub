-- P4 系统本体版本化发布：发布运行日志表（幂等迁移，可重复执行）。
--
-- 版本内容复用 archived 模型行（发布前由服务层把旧 active doc 快照为 archived
-- 版本），本表只记「发布运行」这一事件：谁、什么时候、从哪个指纹发到哪个指纹、
-- diff、契约漂移、状态与失败原因。正文与凭据不复制进来，只留引用与摘要。
--
-- 失败口径（no-silent-degradation）：失败必须留 error + side_effects 显式记录
-- 已发生的业务副作用（图谱/对象展开/KB 同步），不得假回滚成"什么都没发生"。

CREATE TABLE IF NOT EXISTS adh_ontology_release_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    model_id BIGINT NOT NULL COMMENT '目标模型 id（active system model id 保持稳定）',
    actor VARCHAR(128) NOT NULL DEFAULT '' COMMENT '发布发起人（服务端注入，不从请求体取）',
    channel VARCHAR(32) NOT NULL DEFAULT 'script' COMMENT 'script/service/api',
    status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/succeeded/failed',
    fingerprint_before VARCHAR(64) NOT NULL DEFAULT '' COMMENT '发布前生效 doc 指纹',
    fingerprint_after VARCHAR(64) NOT NULL DEFAULT '' COMMENT '发布后生效 doc 指纹（失败为空）',
    snapshot_model_id BIGINT NOT NULL DEFAULT 0 COMMENT '旧 active doc 快照（archived 版本行）的模型 id，0=未快照',
    diff_json JSON NULL COMMENT '对象级 diff（added/removed/changed 计数与 key 清单）',
    contract_drift TINYINT(1) NOT NULL DEFAULT 0 COMMENT '发布前代码注册表与旧 active doc 是否契约漂移',
    side_effects TEXT NULL COMMENT '已发生的业务副作用显式清单（对象展开/图谱/KB），失败时不得抹掉',
    error TEXT NULL COMMENT '失败原因（安全文案，原始细节进服务端日志）',
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    KEY idx_release_model (model_id, started_at),
    KEY idx_release_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='系统本体版本化发布运行日志';
