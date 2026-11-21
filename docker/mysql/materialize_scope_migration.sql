-- ════════════════════════════════════════════════════════════════════════════
-- 定时物化收窄迁移（幂等；不写库名，库由连接配置决定，禁止 USE xxx）
--
-- 为什么需要它：
--   「数据同步」原本设想成通用数据搬运（同步任务 + DAG + UDF），范围过宽且
--   极易被误解成"接个库往里搬数"。实际需求已收窄为**本项目自用的定时物化**：
--   以数据集为来源、编译后写入已登记目标表，按分区增量、原子切换、目标侧对账。
--   本迁移把界面/权限文案与 AI 边界一并钉到"定时物化"口径，并补上节点执行结果
--   落库列（物化要能被审计到"写了哪些分区、是否对账通过"）。
--
-- 幂等/边界：
--   - result_json 列用 information_schema 守卫，重复执行不报错；
--   - ai_access 语句在列不存在时降级为 DO 0（列由 perm_ai_access_migration.sql
--     引入，不假设执行顺序）；列存在则把数据工程域权限码钉为 none；
--   - 只改 label/section/ai_access/ai_note，不动 api_pattern（接口落点不变）；
--   - 不新建权限码，不删除既有授权（存量角色入口不消失）。
--
-- 执行: mysql -u root -p < materialize_scope_migration.sql
-- ════════════════════════════════════════════════════════════════════════════

-- ── 1) 节点执行结果摘要列（物化分区/对账/写入策略；不含数据行、SQL、凭据）──
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_dag_node_runs'
      AND COLUMN_NAME = 'result_json'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_dag_node_runs ADD COLUMN result_json JSON NULL COMMENT ''节点执行结果摘要(物化分区/对账/写入策略, 不含数据行/SQL/凭据)'' AFTER error_message',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_exists2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_dag_node_runs'
      AND COLUMN_NAME = 'node_type'
);
SET @ddl2 = IF(@col_exists2 = 1,
    'ALTER TABLE adh_dag_node_runs MODIFY COLUMN node_type VARCHAR(32) NOT NULL COMMENT ''节点类型: materialize / sync / sql_task / control''',
    'DO 0');
PREPARE stmt2 FROM @ddl2; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

-- ── 2) 菜单登记（幂等 upsert；新环境也能起出来）────────────────────
-- 真值 key 以侧栏 `toMenuKey(routeKey)` 为准（DataPlatformLayout：`/data/sync/logs`
-- → `data:sync/logs`）；`data:sync-logs` 是已退役的 is_active=0 旧行，本段不碰。
-- 历史缺口：`data:sync/logs` 从无迁移 INSERT（只有 UPDATE），新库上根本没有这行 →
-- 角色页「菜单与功能」看不到「运行记录」，故这里补齐为 upsert。
-- label/sort 与侧栏 menu.json（data.sync / data.syncLogs / data.udfs）与侧栏顺序逐项一致。
INSERT INTO adh_menu_registry (menu_key, label, section, module, perm_code, ai_access, sort, is_active)
VALUES ('data:sync', '物化任务', '定时物化', 'data', 'sync:read', 'none', 31, 1)
ON DUPLICATE KEY UPDATE label=VALUES(label), section=VALUES(section), module=VALUES(module),
    perm_code=IF(perm_code IS NULL OR perm_code='', VALUES(perm_code), perm_code),
    sort=VALUES(sort), is_active=1;

INSERT INTO adh_menu_registry (menu_key, label, section, module, perm_code, ai_access, sort, is_active)
VALUES ('data:sync/logs', '运行记录', '定时物化', 'data', 'sync-logs:read', 'none', 32, 1)
ON DUPLICATE KEY UPDATE label=VALUES(label), section=VALUES(section), module=VALUES(module),
    perm_code=IF(perm_code IS NULL OR perm_code='', VALUES(perm_code), perm_code),
    sort=VALUES(sort), is_active=1;

INSERT INTO adh_menu_registry (menu_key, label, section, module, perm_code, ai_access, sort, is_active)
VALUES ('data:udfs', 'UDF 管理', '定时物化', 'data', 'udf:read', 'none', 33, 1)
ON DUPLICATE KEY UPDATE label=VALUES(label), section=VALUES(section), module=VALUES(module),
    perm_code=IF(perm_code IS NULL OR perm_code='', VALUES(perm_code), perm_code),
    sort=VALUES(sort), is_active=1;

-- ── 3) 权限点文案收窄 + 接口落点对齐 ───────────────────────────────
-- 产能集合不变，只改文案；
UPDATE adh_perm_registry SET label = '物化任务查看' WHERE perm_code = 'sync:read';
UPDATE adh_perm_registry SET label = '物化任务管理' WHERE perm_code = 'sync:manage';
UPDATE adh_perm_registry SET label = '物化任务执行' WHERE perm_code = 'sync:execute';
UPDATE adh_perm_registry SET label = '物化运行记录查看' WHERE perm_code = 'sync-logs:read';
UPDATE adh_perm_registry SET label = '物化编排查看' WHERE perm_code = 'dag:read';
UPDATE adh_perm_registry SET label = '物化编排管理' WHERE perm_code = 'dag:manage';

-- 跑得通才算配上了：页面已从 `/api/sync/*` 改读 `/api/dag/*`（任务=workflow，运行记录=run），
-- 若不动 api_pattern，持 sync:* 的角色会“看得见菜单、点进去 403”（看得见点不动）。
-- 归属仍按职责：读=查看、写=管理、run/retry=执行、运行记录=只读日志↔任务名。
UPDATE adh_perm_registry
   SET api_pattern = '/api/sync*,/api/dag/workflows*,/api/dag/runs*', api_method = 'GET'
 WHERE perm_code = 'sync:read';
UPDATE adh_perm_registry
   SET api_pattern = '/api/sync*,/api/dag/workflows*,/api/dag/runs*,/api/dag/translate/*,/api/dag/explain',
       api_method = 'POST,PUT,PATCH,DELETE'
 WHERE perm_code = 'sync:manage';
UPDATE adh_perm_registry
   SET api_pattern = '/api/sync/*/trigger,/api/sync/*/run,/api/dag/workflows/*/run,/api/dag/runs/*/retry,/api/dag/runs/*/stop',
       api_method = 'POST'
 WHERE perm_code = 'sync:execute';
UPDATE adh_perm_registry
   SET api_pattern = '/api/sync/logs*,/api/sync/*/logs*,/api/dag/runs*,/api/dag/workflows*',
       api_method = 'GET'
 WHERE perm_code = 'sync-logs:read';

-- 新环境补齐「运行记录」权限点（历史上只有 UPDATE，无 INSERT）
INSERT INTO adh_perm_registry (perm_code, label, module, module_label, api_pattern, api_method, menu_key, sort)
VALUES ('sync-logs:read', '物化运行记录查看', 'data', '数据中台',
        '/api/sync/logs*,/api/sync/*/logs*,/api/dag/runs*,/api/dag/workflows*', 'GET', 'data:sync/logs', 2121)
ON DUPLICATE KEY UPDATE label=VALUES(label), menu_key=VALUES(menu_key),
    api_pattern=VALUES(api_pattern), api_method=VALUES(api_method);

-- 存量角色默认授权：只跟“已能看到物化任务”的角色等齐（不扩权：没有 sync:read 的角色仍看不到）；
-- 幂等（uk_role_perm）。漏了这步 = 存量用户入口凭空消失。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT DISTINCT rp.role_id, 'sync-logs:read'
  FROM adh_role_perms rp
 WHERE rp.perm_code = 'sync:read';

-- ── 4) AI 零接入（数据工程域；配置侧 none + ai_note 写明原因）──────────────
-- 代码层硬上界在 backend/core/perm_link.py: SECRET_BOUND_NONE_PERMS，配置不可覆盖。
SET @has_ai = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_perm_registry'
      AND COLUMN_NAME = 'ai_access'
);

SET @ai_perms = IF(@has_ai = 1,
    'UPDATE adh_perm_registry SET ai_access=''none'', ai_action_key='''', ai_note=''数据工程域(定时物化): 作业影响面是一批下游数据, LLM 无法核验对错, 属人专属运维动作, 不开放给 AI'' WHERE perm_code IN (''sync:read'',''sync:manage'',''sync:execute'',''sync-logs:read'',''dag:read'',''dag:manage'')',
    'DO 0');
PREPARE ai_stmt FROM @ai_perms; EXECUTE ai_stmt; DEALLOCATE PREPARE ai_stmt;

SET @ai_udf = IF(@has_ai = 1,
    'UPDATE adh_perm_registry SET ai_access=''none'', ai_action_key='''', ai_note=''数据工程域(UDF): SQL 表达式是注入面, 且属人专属运维动作, 不开放给 AI'' WHERE perm_code IN (''udf:read'',''udf:manage'')',
    'DO 0');
PREPARE ai_udf_stmt FROM @ai_udf; EXECUTE ai_udf_stmt; DEALLOCATE PREPARE ai_udf_stmt;

SET @has_menu_ai = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_menu_registry'
      AND COLUMN_NAME = 'ai_access'
);
SET @menu_ai = IF(@has_menu_ai = 1,
    'UPDATE adh_menu_registry SET ai_access=''none'' WHERE menu_key IN (''data:sync'',''data:sync/logs'',''data:udfs'')',
    'DO 0');
PREPARE menu_ai_stmt FROM @menu_ai; EXECUTE menu_ai_stmt; DEALLOCATE PREPARE menu_ai_stmt;

-- ── 5) 核验（人工执行后可复核，非迁移逻辑）──────────────────────────────
-- SELECT menu_key, label, section, perm_code FROM adh_menu_registry
--  WHERE menu_key IN ('data:sync','data:sync/logs','data:udfs');
-- SELECT perm_code, label, ai_access, ai_action_key, ai_note FROM adh_perm_registry
--  WHERE perm_code IN ('sync:read','sync:manage','sync:execute','sync-logs:read',
--                      'dag:read','dag:manage','udf:read','udf:manage');
-- SELECT COUNT(*) FROM information_schema.COLUMNS
--  WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_dag_node_runs' AND COLUMN_NAME='result_json';
