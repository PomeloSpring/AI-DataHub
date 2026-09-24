-- 只读体检：不写任何 DDL，仅列出仍缺失的列/表/ENUM 值（期望：三张表都返回 0 行）
SELECT t.tbl, t.col, IF(c.COLUMN_NAME IS NULL, 'MISSING', 'OK') AS state
FROM (
    SELECT 'adh_reports' tbl, 'run_key' col UNION ALL
    SELECT 'adh_reports', 'generation_status' UNION ALL
    SELECT 'adh_reports', 'publication_status' UNION ALL
    SELECT 'adh_reports', 'security_context' UNION ALL
    SELECT 'adh_reports', 'evidence_summary' UNION ALL
    SELECT 'adh_reports', 'analysis_source' UNION ALL
    SELECT 'adh_reports', 'stage_error_code' UNION ALL
    SELECT 'adh_reports', 'lease_expires_at' UNION ALL
    SELECT 'adh_reports', 'share_token_hash' UNION ALL
    SELECT 'adh_reports', 'share_expires_at' UNION ALL
    SELECT 'adh_reports', 'share_revoked' UNION ALL
    SELECT 'adh_scheduled_logs', 'run_key' UNION ALL
    SELECT 'adh_scheduled_logs', 'lease_expires_at' UNION ALL
    SELECT 'adh_scheduled_logs', 'stage_error_code' UNION ALL
    SELECT 'adh_saved_queries', 'datasource_id' UNION ALL
    SELECT 'adh_chat_attachments', 'storage_type' UNION ALL
    SELECT 'adh_metrics', 'formula_dialects' UNION ALL
    SELECT 'adh_ontology_bindings', 'template_ref' UNION ALL
    SELECT 'adh_sql_templates', 'dialect' UNION ALL
    SELECT 'adh_datasources', 'ssl_mode' UNION ALL
    SELECT 'adh_conversations', 'waker_key' UNION ALL
    SELECT 'adh_workspaces', 'workspace_type'
) t
LEFT JOIN information_schema.COLUMNS c
       ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = t.tbl AND c.COLUMN_NAME = t.col
WHERE c.COLUMN_NAME IS NULL;

SELECT want.table_name AS missing_table
FROM (
    SELECT 'adh_skill_templates' table_name UNION ALL
    SELECT 'adh_skill_template_versions' UNION ALL
    SELECT 'adh_skill_scripts' UNION ALL
    SELECT 'adh_alias_suggestions'
) want
LEFT JOIN information_schema.tables i
       ON i.TABLE_SCHEMA = DATABASE() AND i.TABLE_NAME = want.table_name
WHERE i.TABLE_NAME IS NULL;

SELECT 'adh_as_bot_approvals.status' AS check_item,
       IF(COLUMN_TYPE LIKE '%executing%', 'OK', CONCAT('ENUM_MISSING: ', COLUMN_TYPE)) AS state
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bot_approvals' AND COLUMN_NAME = 'status'
  AND COLUMN_TYPE NOT LIKE '%executing%';
