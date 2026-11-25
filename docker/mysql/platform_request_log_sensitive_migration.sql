-- 平台请求日志本体：敏感列基线（幂等，可重复执行）
--
-- 为什么需要：SLS `logstash_cn`（中国站）与 `logstash`（国际站四站）是平台自身的请求日志。
--   * `headerMap` 是原始请求头，含 accessToken 明文 —— 不建模，但裸 SQL/Playground 仍可能 SELECT 到，
--     未登记敏感基线就会明文下发（护栏 §7）。
--   * `ip` 是**客户端 IP**（业务数据，不是基础设施 IP）：明细应打码，但「独立IP数」的
--     COUNT(DISTINCT ip) 不受影响 —— 聚合指标不包 mask 表达式（mask 只包在维度投影上，
--     见 security-guardrails §7）。
-- 口径：headerMap → block（整列剔除）；ip → partial（首尾各保 2 字符，与结果侧 _partial_mask 同口径）。
-- 不登记 userCode：账号要保留可分组分析（用户数/按用户下钻），是否进一步脱敏由治理侧裁决
-- （见交付说明「待治理确认」），脚本不擅自削弱可分析性。
--
-- 幂等：uk_field(workspace_id, datasource_id, table_name, column_name) + ON DUPLICATE KEY UPDATE；
-- datasource_id 从 adh_datasources.name 反查（不硬编码环境 id），重复执行 0 新增、只对齐口径。
-- 表名差异：中国站是 logstash_cn，国际站四站是 logstash（两套物理表，均需登记）。

INSERT INTO adh_sensitive_fields
    (workspace_id, datasource_id, table_name, column_name,
     sensitivity_level, mask_type, mask_config, description)
SELECT 0, d.id, t.tbl, t.col, t.lvl, t.mtype, NULL, t.descr
FROM (
              SELECT 'prd-sls-cn' AS ds, 'logstash_cn' AS tbl, 'headerMap' AS col,
                     'critical' AS lvl, 'block' AS mtype,
                     '请求原始头（含 accessToken 等凭据明文）：整列剔除，禁止下发与建模' AS descr
    UNION ALL SELECT 'prd-sls-cn', 'logstash_cn', 'ip', 'medium', 'partial',
                     '客户端 IP（业务数据）：明细打码（首尾各 2 字符），独立IP数按聚合计数不受影响'
    UNION ALL SELECT 'prd-sls-en', 'logstash', 'headerMap', 'critical', 'block',
                     '请求原始头（含 accessToken 等凭据明文）：整列剔除，禁止下发与建模'
    UNION ALL SELECT 'prd-sls-en', 'logstash', 'ip', 'medium', 'partial',
                     '客户端 IP（业务数据）：明细打码（首尾各 2 字符），独立IP数按聚合计数不受影响'
    UNION ALL SELECT 'prd-sls-jp', 'logstash', 'headerMap', 'critical', 'block',
                     '请求原始头（含 accessToken 等凭据明文）：整列剔除，禁止下发与建模'
    UNION ALL SELECT 'prd-sls-jp', 'logstash', 'ip', 'medium', 'partial',
                     '客户端 IP（业务数据）：明细打码（首尾各 2 字符），独立IP数按聚合计数不受影响'
    UNION ALL SELECT 'prd-sls-uk', 'logstash', 'headerMap', 'critical', 'block',
                     '请求原始头（含 accessToken 等凭据明文）：整列剔除，禁止下发与建模'
    UNION ALL SELECT 'prd-sls-uk', 'logstash', 'ip', 'medium', 'partial',
                     '客户端 IP（业务数据）：明细打码（首尾各 2 字符），独立IP数按聚合计数不受影响'
    UNION ALL SELECT 'prd-sls-eu', 'logstash', 'headerMap', 'critical', 'block',
                     '请求原始头（含 accessToken 等凭据明文）：整列剔除，禁止下发与建模'
    UNION ALL SELECT 'prd-sls-eu', 'logstash', 'ip', 'medium', 'partial',
                     '客户端 IP（业务数据）：明细打码（首尾各 2 字符），独立IP数按聚合计数不受影响'
) t
JOIN adh_datasources d ON d.name = t.ds
ON DUPLICATE KEY UPDATE
    sensitivity_level = VALUES(sensitivity_level),
    mask_type         = VALUES(mask_type),
    mask_config       = VALUES(mask_config),
    description       = VALUES(description);