-- ═══════════════════════════════════════════════════════════════
-- DAG 工作流 / UDF 管理 — 菜单与权限码种子
-- 风格对齐 task_monitor_migration.sql / permission_model_migration.sql；
-- IF NOT EXISTS / INSERT IGNORE 可重复执行。
-- 门控口径：读写均按码（工作流含 SQL 设计内容，非普通读放开域），
-- fail-closed：未授权角色不可见/不可用，由管理员在权限管理页按角色授予。
-- 执行: mysql -u root -p < dag_udf_perm_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh2;

-- 菜单：UDF 管理（数据中台-数据同步组，同步任务/DAG 编辑器引用 UDF 的查看入口）；
-- DAG 工作流入口挂在「同步任务」页内，复用既有菜单 data:sync，不新增独立菜单。
INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('data:udfs', 'UDF 管理', '数据同步', 'data', 48);

INSERT IGNORE INTO adh_perm_registry (perm_code, label, module, module_label, api_pattern, api_method, menu_key, sort) VALUES
('dag:read', 'DAG 工作流查看', 'data', '数据中台', '/api/dag*', 'GET', 'data:sync', 173),
('dag:manage', 'DAG 工作流管理', 'data', '数据中台', '/api/dag*', 'POST,PUT,PATCH,DELETE', 'data:sync', 174),
('udf:read', 'UDF 查看', 'data', '数据中台', '/api/udfs*', 'GET', 'data:udfs', 175),
('udf:manage', 'UDF 管理', 'data', '数据中台', '/api/udfs*', 'POST,PUT,PATCH,DELETE', 'data:udfs', 176);

-- 旧 'system:udfs' 菜单归属迁移为数据中台（可重复执行）
UPDATE adh_menu_registry SET menu_key='data:udfs', label='UDF 管理', section='数据同步', module='data', sort=48
 WHERE menu_key='system:udfs';
UPDATE adh_perm_registry SET menu_key='data:udfs' WHERE perm_code IN ('udf:read','udf:manage');

UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- 业务角色默认授权（fail-closed：未授权角色不可见，故按职责预置）：
-- analyst 数据开发可读写；data_viewer/viewer 只读。
INSERT IGNORE INTO adh_role_perms (id, role_id, perm_code) VALUES
(179101, 2, 'dag:read'),
(179102, 2, 'dag:manage'),
(179103, 2, 'udf:read'),
(179104, 2, 'udf:manage'),
(179105, 200, 'dag:read'),
(179106, 200, 'udf:read'),
(179107, 3, 'dag:read'),
(179108, 3, 'udf:read');
