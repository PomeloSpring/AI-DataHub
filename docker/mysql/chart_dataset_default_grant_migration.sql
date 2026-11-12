-- ============================================================================
-- 看板图表取数通道收口 → 存量角色数据集默认授权迁移（幂等, 可重复执行）
--
-- 背景 (2026-11-06):
--   看板图表不再自带 SQL，取数口径一律来自数据集，且可挂清单由**角色数据集授权**
--   (adh_role_dataset_access) 裁决 —— 与编辑器/组件库的 `GET /datasets/` 下拉同源。
--   收口前"图上有 SQL"就能用，与该角色是否被授权数据集无关；收口后若内置角色
--   analyst / viewer 的授权为空，存量看板的编辑者会看到空清单（入口凭空消失）。
--
-- 本迁移只做一件事：把**存量看板上真有引用的 active 数据集**默认授权给
--   analyst / viewer，维持收口前的可用现状。管理员仍可在
--   「角色权限 → 数据集」按角色收回。
--   未被任何图表引用的数据集**不放权**（保持 fail-closed，不借迁移扩权）。
--
-- 幂等性: INSERT IGNORE + UNIQUE KEY uk_role_dataset(role_id, dataset_id)。
--
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE，docker 环境为 adh，
--   联调环境可能为 adh2），跟 conversation_messages_split / site_scope 迁移同口径。
--   执行方式：`venv/bin/python logs/apply_migration.py docker/mysql/chart_dataset_default_grant_migration.sql`。
-- ============================================================================

INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id)
SELECT r.id, d.id
FROM adh_roles r
JOIN adh_datasets d ON d.status = 'active'
WHERE r.name IN ('analyst', 'viewer')
  AND EXISTS (
      SELECT 1 FROM adh_charts c
      WHERE c.source_type = 'dataset' AND c.source_id = d.id
  );

-- 核验（执行后手工查）：
--   SELECT r.name, COUNT(*) FROM adh_role_dataset_access a JOIN adh_roles r ON r.id=a.role_id
--    GROUP BY r.name;
--   -- 应为 0：被图表引用但无任何角色授权的数据集
--   SELECT DISTINCT c.source_id FROM adh_charts c
--    WHERE c.source_type='dataset' AND NOT EXISTS
--      (SELECT 1 FROM adh_role_dataset_access a WHERE a.dataset_id=c.source_id);
