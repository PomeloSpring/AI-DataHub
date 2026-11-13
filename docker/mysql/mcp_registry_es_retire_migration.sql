-- ============================================================================
-- MCP 市场清单下架 Elasticsearch 服务（幂等，可重复执行）
--
-- 背景 (2026-11-13 用户拍板「下掉」):
--   `adh_mcp_registry` 是「MCP 市场」的可安装清单（页面：MCP 市场 → 安装）。它里面有一条
--   官方 `docker.elastic.co/mcp/elasticsearch`（install_type=docker → 安装后落成 **stdio**
--   外部服务行）。而 `mcp_market_service.install_from_registry` 建的行是普通外部服务，
--   AS-BOT 绑上后模型就能拿 `ES_URL`/`ES_API_KEY` 直连集群：
--   既不查角色数据源授权（`resource_guard.bind_resources` 的 available_sources），
--   也不套行级/列级策略，还不落取数审计 —— 正是护栏 §1 禁止的"返回数据行但不经治理入口"。
--
--   这与已落地的架构直接冲突：ES 现在是**正式数据源**（`adh_datasources.db_type='es'`，
--   开放范围在 `extra_config`，取数一律过 `semantics.execute` / `execute_sql` 治理口），
--   内置 ES MCP 服务与它的三个裸 SQL/元数据工具已于阶段三第 1 步退役
--   （见 `es_builtin_mcp_retire_migration.sql`）。留着这条市场项等于一边拆旁路一边开门。
--
-- 为什么走 SQL 而不是页面：市场页当前只有「浏览 / 详情 / 安装」三个动作，
--   `mcp_market_service.create_registry/update_registry/delete_registry` **没有任何消费方**
--   （没有对应的 REST 端点），管理员无法在 UI 里下架，只能由迁移落库。
--
-- 判据（宁窄勿宽）：按包名含 elasticsearch 命中（覆盖 docker/npm/pip 三种发行形态的官方与
--   社区包），外加 name='Elasticsearch' 且 category='database' 的兜底；不动其它数据库类目
--   条目（MongoDB/MySQL/PostgreSQL/Redis/SQLite 属同一类风险，是否一并下架尚未拍板）。
--
-- 幂等：DELETE 无行可删时自然空转。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/mcp_registry_es_retire_migration.sql
--
-- 若将来接入"市场清单自动同步"，同步侧必须同样排除本判据，否则这条会被重新灌回来。
-- ============================================================================

DELETE FROM adh_mcp_registry
WHERE LOWER(COALESCE(package_name, '')) LIKE '%elasticsearch%'
   OR (name = 'Elasticsearch' AND category = 'database');

-- ============================================================================
-- 核验（执行后应为 0 行）：
--   SELECT id, name, package_name, install_type FROM adh_mcp_registry
--    WHERE LOWER(COALESCE(package_name,'')) LIKE '%elasticsearch%'
--       OR name = 'Elasticsearch';
--   -- 已安装成服务行的情况（本环境实测为空）：必须一并停用，否则旁路仍在
--   SELECT id, name, transport, is_active FROM adh_mcp_servers
--    WHERE LOWER(COALESCE(args,'')) LIKE '%elasticsearch%'
--       OR LOWER(COALESCE(command,'')) LIKE '%elastic%'
--       OR LOWER(name) LIKE '%elasticsearch%';
-- ============================================================================
