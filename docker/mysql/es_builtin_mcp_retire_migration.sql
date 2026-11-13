-- ============================================================================
-- 阶段三（第 1 步）：退役内置 Elasticsearch MCP 服务（幂等，可重复执行）
--
-- 背景 (2026-11-13 拍板):
--   ES 已从「内置 MCP 通道」变成**正式数据源**（`adh_datasources.db_type='es'`，
--   开放范围进 `extra_config`），阶段二已把 29 个 ES 数据集全部迁到该源并逐值对账通过。
--   于是原来那三个内置工具（`list_es_indexes` / `describe_es_index` / `query_es_sql`）
--   失去存在依据，而且它们是**不过治理执行口的裸路**：直调 `es_client`，既不查角色数据源
--   授权，也不套行级/列级策略 —— 其合法性来自已作废的 2026-11-04「ES 不属治理域」拍板。
--   ES 既已入治理域（且上面有真 RLS 策略），留着就是护栏 §1 的旁路，因此整体下线。
--
-- 退役后的等价能力（不新造入口，全部现成且已过治理）：
--   看有什么    → `list_datasources`（能列出 cloud-platform-es）
--   看字段      → `search_metadata` / `get_table_schema`（阶段一 `_field_caps` 已同步 4 别名 210 列入 catalog）
--   跑只读 SQL  → `check_sql` → `execute_sql`（已按 `db_type='es'` 分派，治理改写 + 审计 + RLS 齐备）
--
-- 代码侧同批落地（缺一边就是"页面勾了、执行说没有"的空承诺）：
--   `builtin_mcp.BUILTIN_MCP_SERVICES` 摘掉 elasticsearch 条目（机制保留，注册表为空）；
--   `sdk_tools/es_tools.py` 删除；`sdk_tools/../migrate_es_builtin_mcp.py`（本行的种子脚本）删除。
--
-- 为什么是硬删（DELETE）而不是软删（2026-11-13 用户拍板修正）：
--   第一版只做了 is_active=0，结果 MCP 服务页仍渲出这张卡，而且卡上写着「提供工具 (3)」——
--   那 3 条 schema 是 `discovered_tools` 列里的旧清单，代码里根本不存在这些工具，属于
--   「页面看得见、永远用不了」的空承诺（菜单/权限规范里的同类反模式）。行本身不携带任何
--   需回溯的配置（内置行的 url/command/args/env 恒为空），可追溯性由本迁移与备份
--   `logs/es_mcp_retire_backup.json` 承担，所以直接删行。代价：`uk_name` 不再占住
--   'elasticsearch' 这个名字，将来真要登记内置服务时不会撞上死行。
--
-- AS-BOT 的绑定存在**两处**，必须同批清（只清一处会留下"配置残留但服务已死"的残影）：
--   ① `adh_as_bots.mcp_server_ids`（JSON 数组，绑定哪个服务）
--   ② `adh_as_bots.tools.external["<服务id>"]`（逐工具授权清单）
--
-- 幂等：先清绑定再删服务行；重复执行时各条语句自然空转（WHERE 不命中）。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/es_builtin_mcp_retire_migration.sql
-- ============================================================================

-- ── 1. 先清 AS-BOT 绑定①：mcp_server_ids 里的 9001（还有其他绑定的保住其余项）────
--     内层用 JOIN + WHERE 过滤（不是 LEFT JOIN 后聚合）：`JSON_ARRAYAGG` 会把 NULL
--     聚成 JSON null，留下 `[null]` 这种脏数组（该坑在 dashboard_legacy_params_dedup 已踩过）。
UPDATE adh_as_bots b
JOIN (
    SELECT bot_id, JSON_ARRAYAGG(sid) AS ids
    FROM (
        SELECT a.id AS bot_id, j.sid
        FROM adh_as_bots a
        JOIN JSON_TABLE(a.mcp_server_ids, '$[*]' COLUMNS (sid BIGINT PATH '$')) j
        WHERE a.mcp_server_ids IS NOT NULL
          AND JSON_VALID(a.mcp_server_ids)
          AND JSON_TYPE(a.mcp_server_ids) = 'ARRAY'
          AND j.sid <> 9001
    ) keep
    GROUP BY bot_id
) n ON n.bot_id = b.id
SET b.mcp_server_ids = n.ids
WHERE b.mcp_server_ids IS NOT NULL
  AND JSON_CONTAINS(b.mcp_server_ids, '9001');

-- ── 2. 上一步没命中的 = 只绑了 9001 → 置空数组（不得留 [9001] 指向已删服务）────
UPDATE adh_as_bots
SET mcp_server_ids = JSON_ARRAY()
WHERE mcp_server_ids IS NOT NULL
  AND JSON_CONTAINS(mcp_server_ids, '9001');

-- ── 3. 清 AS-BOT 绑定②：tools.external 里那个服务 id 的逐工具清单 ──────
--     key 是纯数字，JSON path 里必须加引号（`$.external."9001"`）。
UPDATE adh_as_bots
SET tools = JSON_REMOVE(tools, '$.external."9001"')
WHERE tools IS NOT NULL
  AND JSON_VALID(tools)
  AND JSON_CONTAINS_PATH(tools, 'one', '$.external."9001"');

-- ── 4. 删内置服务行（放在最后：先解绑再删，避免中途出现"绑着一个不存在的 id"）────
--     条件里带 name/transport/is_builtin 三重限定：宁可不删，也不得误删同名外部服务。
DELETE FROM adh_mcp_servers
WHERE id = 9001 AND name = 'elasticsearch' AND transport = 'builtin' AND is_builtin = 1;

-- ============================================================================
-- 核验（执行后手工查，四条都应为 0 行 / 空）：
--   -- ① 内置服务行已删净
--   SELECT id, name FROM adh_mcp_servers WHERE transport='builtin' OR is_builtin=1;
--   -- ② 没有任何 AS-BOT 还绑着它
--   SELECT id, as_bot_key FROM adh_as_bots
--    WHERE JSON_CONTAINS(COALESCE(mcp_server_ids, JSON_ARRAY()), '9001');
--   -- ③ 也没有残留的逐工具授权
--   SELECT id, as_bot_key FROM adh_as_bots
--    WHERE JSON_CONTAINS_PATH(tools, 'one', '$.external."9001"');
--   -- ④ 工作空间级绑定不应有历史遗留（本环境实测为空）
--   SELECT * FROM adh_workspace_mcp_servers WHERE mcp_server_id = 9001;
--   -- ⑤ 版本表无孤儿行（本环境实测为空；若有则按 config_id/config_key 一并清）
--   SELECT id, config_id, config_key FROM adh_mcp_server_versions WHERE config_id = 9001;
-- ============================================================================
