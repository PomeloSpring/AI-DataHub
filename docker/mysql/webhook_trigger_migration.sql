-- ═══════════════════════════════════════════════════════════════
-- Webhook 触发机制 — 增量 Migration
-- 执行: mysql -u root -p < webhook_trigger_migration.sql
-- ═══════════════════════════════════════════════════════════════

-- 为 adh_scheduled_tasks 新增 webhook 相关字段
ALTER TABLE adh_scheduled_tasks
  ADD COLUMN trigger_type VARCHAR(20) DEFAULT 'cron' COMMENT '触发方式: cron / webhook / both' AFTER cron_expression,
  ADD COLUMN webhook_token VARCHAR(64) COMMENT 'Webhook 认证 Token' AFTER trigger_type,
  ADD COLUMN webhook_secret VARCHAR(128) COMMENT 'Webhook HMAC 签名密钥（可选）' AFTER webhook_token,
  ADD UNIQUE INDEX idx_webhook_token (webhook_token);

-- 为已有记录设置默认 trigger_type
UPDATE adh_scheduled_tasks SET trigger_type = 'cron' WHERE trigger_type IS NULL;
