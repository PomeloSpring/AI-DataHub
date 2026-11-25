-- ============================================================================
-- 血缘能力开通（get_lineage 工具组）— 幂等，可重复执行
--
-- 为什么需要：`lineage` 工具组（`get_lineage`）是「血缘赋能 LLM」的授权面。
--   代码侧注册（sdk_tools/__init__.py 的 TOOL_SERVER_BUILDERS/TOOL_SERVER_TOOLS +
--   platform/api/as_bots.py 的组标签）只是「可以被勾」，**不代表已授权**：
--   AS-BOT 的工具授权来自 adh_as_bots.tools.mcp 逐工具清单，未写进配置的组
--   永远不注册给模型（fail-closed）。存量 AS-BOT 都是本组上线**之前**建的，
--   因此必须同批把本组补进配置——否则新能力只有管理员手工去编辑器里勾一遍才
--   生效，等于「配了没用」的空承诺（menu-permission-sync 同类缺陷）。
--
-- 判据（只做加法，不覆盖已有配置）：
--   1. 只处理「已有逐工具配置」的行（$.mcp 存在）。JSON 里没有 mcp 键的老行，
--      运行时是按 groups 整组展开的（tool_policy.normalize_tools），这里若直接
--      JSON_SET 会把 mcp 变成一个只含 lineage 的字典，反而**收窄**了原授权范围
--      —— 那才是真正的特立独行，故这类行不动，交由核验清单人工确认。
--   2. 只给面向数据探索的 bot（mcp 里已有 catalog 或 semantic）补血缘：
--      血缘读的表节点按角色数据源授权 fail-closed 过滤，与 catalog 同一暴露面；
--      只做运营/资产管理的 bot 不需要链路能力。
--   3. 已有 lineage 键的行（含显式配成空数组 = 管理员主动关闭）一律跳过，
--      不把人的决定改回去。
--
-- groups 字段同步追加 "lineage"：裁决不看它（normalize_tools 以 mcp 重算），
--   但表单/审计读它，两边不一致会让编辑器显示「组未启用、工具已勾」。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 补逐工具清单：$.mcp.lineage = ["get_lineage"]
-- ----------------------------------------------------------------------------
UPDATE adh_as_bots
   SET tools = JSON_SET(tools, '$.mcp.lineage', JSON_ARRAY('get_lineage'))
 WHERE JSON_CONTAINS_PATH(tools, 'one', '$.mcp') = 1
   AND JSON_CONTAINS_PATH(tools, 'one', '$.mcp.lineage') = 0
   AND (JSON_CONTAINS_PATH(tools, 'one', '$.mcp.catalog') = 1
        OR JSON_CONTAINS_PATH(tools, 'one', '$.mcp.semantic') = 1);

-- ----------------------------------------------------------------------------
-- 2. groups 同步追加（守卫：groups 是数组、尚未含 lineage）
-- ----------------------------------------------------------------------------
UPDATE adh_as_bots
   SET tools = JSON_ARRAY_APPEND(tools, '$.groups', 'lineage')
 WHERE JSON_CONTAINS_PATH(tools, 'one', '$.mcp.lineage') = 1
   AND JSON_TYPE(JSON_EXTRACT(tools, '$.groups')) = 'ARRAY'
   AND JSON_SEARCH(JSON_EXTRACT(tools, '$.groups'), 'one', 'lineage') IS NULL;

-- ----------------------------------------------------------------------------
-- 3. 核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- 已授权的 bot（期望：所有面向数据探索的 bot 都出现）：
--   SELECT id, name, JSON_EXTRACT(tools, '$.mcp.lineage') AS lineage
--     FROM adh_as_bots WHERE JSON_CONTAINS_PATH(tools, 'one', '$.mcp.lineage') = 1;
-- 需要人工确认的老行（没有逐工具配置、按 groups 整组展开；期望 0 行，
-- 若有请在 AS-BOT 编辑器里显式保存一次配置，不要用 SQL 猜）：
--   SELECT id, name, JSON_EXTRACT(tools, '$.groups') AS groups
--     FROM adh_as_bots
--    WHERE JSON_CONTAINS_PATH(tools, 'one', '$.mcp') = 0
--      AND JSON_CONTAINS(COALESCE(JSON_EXTRACT(tools, '$.groups'), JSON_ARRAY()),
--                        '"lineage"') = 1;
-- 二次执行应为 0 变更（UPDATE 的 WHERE 守卫保证幂等）：
--   SELECT ROW_COUNT();