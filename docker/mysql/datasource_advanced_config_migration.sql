-- ============================================================================
-- 数据源级高级配置落地：ES 预算从"全局 .env"迁到数据源行 + 内置通道数据集收尾
--
-- 背景 (2026-11-13):
--   超时/连接池这类预算**按数据源**配才成立（不同源体量与链路不同），但实测发现两件
--   不一致：
--   1) ES 数据源 `cloud-platform-es` 的 `extra_config.timeout_sec` 还是 20s，而 App 使用
--      日志族里 ASSCANPRO 级大 App 的单条聚合实测 11.6~18.1s（同一形状两次实测差 35%），
--      叠加集群抖动会间歇越线；配套改动是把高基数去重列拆到单独数据集 + 同参数查询去重。
--   2) ES 早已是正式数据源（不再走内置 MCP 通道，`backend/.env` 的 ES_* 段已删除），
--      但后建的 #81/#88 仍是 `source_type='es'` + `datasource_id=0` 的内置通道形态，
--      连接参数没落处 → 取数显式报错（不是静默空结果）。本迁移把这两行收敛到数据源形态。
--
-- ⚠ 不写 `USE adh;`：本仓迁移在本机跑在 `.env` 指定的 schema（当前为 adh2），
--   带着 USE 会 Alter 到另一个库上并"执行成功但一行没改"（已撞过，见 common_pitfalls）。
--   全部语句幂等：数值只在"缺省或低于目标值"时抬，运维后续手动调低不会被本迁移抬回去；
--   数据集转换带 WHERE 形态判定，重跑 0 行。
-- ============================================================================

-- ── 一、ES 数据源预算：查询超时 30s + 连接池 10 ──────────────────────────
-- timeout_sec：20 → 30（只在现值 < 30 或缺失时抬，避免覆盖运维已定的更高/更低预算）
UPDATE adh_datasources
SET extra_config = JSON_SET(
        COALESCE(extra_config, JSON_OBJECT()),
        '$.timeout_sec',
        CAST(30 AS SIGNED)),
    updated_at = NOW()
WHERE name = 'cloud-platform-es'
  AND (extra_config IS NULL
       OR JSON_EXTRACT(extra_config, '$.timeout_sec') IS NULL
       OR CAST(JSON_UNQUOTE(JSON_EXTRACT(extra_config, '$.timeout_sec')) AS SIGNED) < 30);

-- pool_size：新增键（HTTP 连接复用数）。一块看板 30 张图 = 过去 30 次 TLS 握手，
-- 现在按池复用；消费点 = `es_client._session_for`（Session + HTTPAdapter pool_maxsize）。
-- 只在缺失时补，已配过的不覆盖。
UPDATE adh_datasources
SET extra_config = JSON_SET(
        COALESCE(extra_config, JSON_OBJECT()),
        '$.pool_size',
        CAST(10 AS SIGNED)),
    updated_at = NOW()
WHERE name = 'cloud-platform-es'
  AND JSON_EXTRACT(COALESCE(extra_config, JSON_OBJECT()), '$.pool_size') IS NULL;

-- ── 二、内置通道数据集收尾（#81/#88 这类"后建的 ES 集"）──────────────────
-- 与存量 #64-68/82-87 的形态逐字对齐：正文引用**登记的索引别名**（不是 `t-别名*` 物理
-- 模式），血缘表 touched_tables 同步换成别名（两处必须一起换，否则血缘与取数不同源）。
UPDATE adh_datasets d
JOIN adh_datasources s ON s.name = 'cloud-platform-es'
SET d.sql_query = REPLACE(
        d.sql_query,
        '"t-t_software_app_operation_log*"',
        '"t_software_app_operation_log"'),
    d.touched_tables = JSON_ARRAY('t_software_app_operation_log'),
    d.source_type = 'sql',
    d.datasource_id = s.id,
    d.es_pattern = '',
    d.updated_at = NOW()
WHERE d.source_type = 'es'
  AND d.es_pattern = 't-t_software_app_operation_log*';

-- ── 三、核验（人工执行，不属于迁移本体）─────────────────────────────────
-- SELECT JSON_EXTRACT(extra_config, '$.timeout_sec') AS timeout_sec,
--        JSON_EXTRACT(extra_config, '$.pool_size')   AS pool_size
--   FROM adh_datasources WHERE name = 'cloud-platform-es';
-- SELECT id, name, source_type, datasource_id FROM adh_datasets
--   WHERE source_type = 'es';        -- 期望 0 行
-- 实机出数核验走 logs/repoint_es_datasets_to_source.py（转完立刻按真实入参跑一次取数）。
