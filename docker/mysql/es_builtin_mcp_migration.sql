-- ============================================================================
-- MCP 服务表结构迁移（原：内置 Elasticsearch MCP 服务）
--
-- 【2026-11-13 阶段三已改性质】内置 `elasticsearch` 服务行已从本文件移除（退役见
-- `docker/mysql/es_builtin_mcp_retire_migration.sql`）：ES 现在是**正式数据源**
-- （`adh_datasources.db_type='es'` + `extra_config` 开放范围），不再需要“凭据由
-- 部署侧 .env 托管的内置 MCP 服务”这套承载。本文件只保留仍需的部分：
--   ① `adh_mcp_servers` 建表（外部 MCP 服务仍在用）；
--   ② `is_builtin` 列与“内置行不持有连接配置”的安全约定（`builtin_mcp` 机制保留，
--      注册表当前为空；将来登记新内置服务时直接往 `BUILTIN_MCP_SERVICES` 加）。
--
-- 历史上本文件还声称“ES 不进数据权限治理域——技术上不成立”。该断言已被推翻：
-- ES SQL 确实无 JOIN/派生表，但行级策略可“谓词 AND 进顶层 WHERE”、列级策略可编译期
-- 改写（实现见 `semantics/sql_guard._inject_filters_es` 与 `semantics/es_dialect.py`），
-- ES 源现已全部走治理执行口（角色源授权 + RLS/列级 + 审计）。
--
-- 幂等：uk_name 为键，可重复执行。
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

-- 【已退役、故意不插行】内置 `elasticsearch` 服务行（旧 id=9001）：
--   代码侧 `builtin_mcp.BUILTIN_MCP_SERVICES` 已摘掉该条目并删除 `sdk_tools/es_tools.py`，
--   新装库再插这行就是“库里有服务、代码无实现”的空承诺（AS-BOT 勾上也不会生成工具），
--   所以本文件不再创建它。存量库里的旧行由 `es_builtin_mcp_retire_migration.sql` 软删。
--   若历史上已执行过本文件的旧版本，补跑一次那条退役迁移即可。
