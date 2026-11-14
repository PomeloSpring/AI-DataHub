-- ============================================================================
-- 敏感基线补登：三方对接日志的请求/响应明细列（cloud-platform-es · sys_thrid_request_log）
--
-- 背景（本批「外部合作方对接分析」同批治理项）：出向推送日志的
--   `requestBody` / `responseBody` / `requestHeader` 三列 2026-11-14 实测在
--   `adh_sensitive_fields` 里**零登记**（`SELECT COUNT(*) WHERE column_name IN (...)` = 0 行），
--   而同表既有 41 条规则已经覆盖手机号/邮箱/密码/签名密钥 —— 这三列的内容比它们更宽：
--   * requestBody / responseBody：合作方接口的完整业务载荷（订单、病例、患者与医生信息、
--     附件直链等，形状由对方系统决定，不可枚举）；
--   * requestHeader：调用头，含签名与鉴权字段（与工作簿里 `open_isv_keys.secret` 同档，
--     该列库内已登记 critical + block）。
--   这是治理缺口，不是分析口径问题：补在**列级策略**上，而不是往出站层加正则（护栏 §3
--   的唯一裁决点就是 `adh_sensitive_fields`）。
--
-- 取值口径（照抄库内既有 block 行的值域，不新造档）：
--   * 明细载荷两列 → `high` + `block`：与全局 PII block 行（username/phone/email = high/block）同档；
--     内容里可能落到 critical（身份证/密钥级），但列本身是"混合业务载荷"，按可枚举 PII 的上一档登记，
--     避免把不可证实的等级写高（写高会掩盖真正 critical 列的定位）。
--   * requestHeader → `critical` + `block`：对齐同族凭证列 `open_isv_keys.secret`（critical/block）。
--   mask_type 一律 `block`（= 不能查询出来）：这三列没有任何分析场景需要原值，
--     做成 partial/hash 只会让人以为"脱敏后可读"，而载荷脱敏后仍可能带出身份信息。
--
-- 表级而非全局：`table_name` 写死 `sys_thrid_request_log`（ES 别名），datasource_id 按
--   `name='cloud-platform-es'` 子查询解析，不硬编码 id —— 别的数据源里叫 requestBody 的列
--   （如各业务库的接口日志）形状未知，不在本批凭猜测扩权/收权。
--   注意：ES SQL 里不可查 `_index`，别名内 admin 家族索引同受本规则约束（同名列同样被拦）。
--
-- 幂等：命中唯一键 uk_field(workspace_id, datasource_id, table_name, column_name) 时刷新
--   等级/方式/说明（本批是策略定义方，重跑要把口径收敛到最新），不新增重复行。
--   数据源未登记（cloud-platform-es 不存在）时 SELECT 返回空集 → 插入 0 行，不报错中断迁移。
--
-- 应用后核验（本批验收判据）：
--   1) `execute_sql` 选这三列 → 被剔除或被拒，审计可分辨（sensitive_block 标记）；
--   2) 本批合作方对接数据集真取数仍全部通过（正文未引用这三列，SLS 侧不 SELECT *）。
-- ============================================================================
USE adh2;

INSERT INTO adh_sensitive_fields
    (workspace_id, datasource_id, table_name, column_name, sensitivity_level, mask_type, description)
SELECT 0,
       d.id,
       'sys_thrid_request_log',
       c.column_name,
       c.sensitivity_level,
       'block',
       c.description
FROM adh_datasources d
JOIN (
    SELECT 'requestBody' AS column_name, 'high' AS sensitivity_level,
           '合作方接口请求明细载荷(含订单/病例/患者等不可枚举个人信息)｜ISV 对接分析同批治理' AS description
    UNION ALL
    SELECT 'responseBody', 'high',
           '合作方接口响应明细载荷(对方返回的完整报文)｜ISV 对接分析同批治理'
    UNION ALL
    SELECT 'requestHeader', 'critical',
           '调用请求头(含签名与鉴权字段, 同 open_isv_keys.secret 档)｜ISV 对接分析同批治理'
) c
WHERE d.name = 'cloud-platform-es'
ON DUPLICATE KEY UPDATE
    sensitivity_level = VALUES(sensitivity_level),
    mask_type         = VALUES(mask_type),
    description       = VALUES(description),
    updated_at        = CURRENT_TIMESTAMP;

-- 核验（人工执行，期望 3 行 block）：
--   SELECT f.column_name, f.sensitivity_level, f.mask_type, d.name
--   FROM adh_sensitive_fields f JOIN adh_datasources d ON d.id = f.datasource_id
--   WHERE f.table_name = 'sys_thrid_request_log' ORDER BY f.column_name;
