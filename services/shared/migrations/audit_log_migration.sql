-- ============================================================================
-- 审计日志增强：增加 module 字段
-- ============================================================================

ALTER TABLE adh_audit_logs ADD COLUMN IF NOT EXISTS module VARCHAR(32) DEFAULT '' COMMENT '操作模块';
CREATE INDEX IF NOT EXISTS idx_audit_log_module ON adh_audit_logs(module);
