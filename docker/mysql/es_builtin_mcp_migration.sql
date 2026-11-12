-- ============================================================================
-- 内置 Elasticsearch MCP 服务（迁移）
--
-- 定位：ES 是**平台内置 MCP 服务**，与外部注册服务同在 adh_mcp_servers 里被
-- AS-BOT「引用 MCP 服务」绑定并逐工具授权 —— 不另开数据工具组，避免双口径。
--
-- 与外部服务的关键差别：is_builtin=1 的行**不持有连接配置**。
--   url / command / args / env 一律留空，真实地址与凭据只在 backend/.env
--   （见 backend/common/config.py 的 ES_* 段），API 响应与 UI 均不回显（护栏 §7）。
--   执行侧由 external_tools.build_external_servers 识别 transport='builtin'
--   分派到进程内 handler，不建 HTTP 连接、不做配置漂移比对。
--
-- 幂等：uk_name 为键，可重复执行。
--
-- 本文件的权威程度：结构 + 内置行留档（新装库直接建好）。**工具清单与描述的单一
-- 事实源是代码**（sdk_tools/es_tools.py 的 TOOL_SPECS），由
-- backend/scripts/migrate_es_builtin_mcp.py 从代码生成并写库；改工具请重跑该脚本，
-- 不要只改本文件的 JSON（否则会出现“页面显示的工具与执行侧注册的不一致”）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_mcp_servers (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    transport VARCHAR(20) NOT NULL DEFAULT 'sse',
    url VARCHAR(500) DEFAULT '',
    command VARCHAR(200) DEFAULT '',
    args VARCHAR(1000) DEFAULT '',
    `env` TEXT,
    tools_config TEXT,
    discovered_tools TEXT,
    is_active TINYINT NOT NULL DEFAULT 1,
    datasource_id BIGINT DEFAULT 0,
    last_test_at DATETIME,
    last_test_status VARCHAR(20),
    last_test_message VARCHAR(500),
    version INT NOT NULL DEFAULT 1,
    created_by VARCHAR(100),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB;

-- 内置标记列（存量库补列；已存在时报错由幂等执行方忽略）
ALTER TABLE adh_mcp_servers ADD COLUMN is_builtin TINYINT NOT NULL DEFAULT 0 COMMENT '1=平台内置服务：配置由部署侧托管，页面不可编辑';

INSERT INTO adh_mcp_servers
    (id, name, description, transport, url, command, args, `env`,
     tools_config, discovered_tools, is_active, is_builtin, datasource_id, created_by)
VALUES
    (9001, 'elasticsearch',
     '平台内置 Elasticsearch 只读查询能力（列开放索引范围 / 看字段 / 跑 SQL）。索引按时间分片，以模式（如 logs-*）为查询单位；访问范围由部署侧索引白名单限定，不参与列级/行级数据权限治理。',
     'builtin', '', '', '', NULL, NULL,
     '[{"name":"list_es_indexes","description":"List the Elasticsearch index patterns (data views) open for querying in this deployment. The pattern itself is the queryable unit; do not enumerate concrete monthly indexes."},{"name":"describe_es_index","description":"Show the fields of an Elasticsearch index pattern (name, type, aggregatable, dotted sub-fields, .keyword exact-match field) before writing SQL."},{"name":"query_es_sql","description":"Run a read-only Elasticsearch SQL query and return rows. Single SELECT over one double-quoted index pattern, no JOIN, LIMIT is mandatory."}]',
     1, 1, 0, 'system')
ON DUPLICATE KEY UPDATE
    description      = VALUES(description),
    transport        = VALUES(transport),
    -- 内置行永远不带连接配置：重跑迁移会把误填的值清回空
    url              = '',
    command          = '',
    args             = '',
    `env`            = NULL,
    discovered_tools = VALUES(discovered_tools),
    is_builtin       = 1,
    datasource_id    = 0,
    updated_at       = CURRENT_TIMESTAMP;
