-- ============================================================================
-- 阶段三（第 2 步）：退役 ES 内置通道遗产 —— 权限码 `dataset:es` 下架（幂等，可重复执行）
--
-- 背景（2026-11-13 拍板）：
--   ES 内置通道（`.env` 托管的 `datasource_id=0` ES 目标 + 数据集行的 `source_type='es'`
--   / `es_pattern`）的全部机件已随阶段三第 2 步删除：`config.ES_*`、`es_client.legacy_ctx`、
--   `dataset_service._query_es` / `es_pattern_options` / `es_field_options` / `validate_es_sql`
--   / `require_es_channel`、以及 `/api/datasets/es-patterns`·`/es-fields`·`/validate-es-sql`
--   ·`/channels` 四个配置面端点。ES 数据集现在只有一种形态：`source_type='sql'` +
--   执行源 `db_type='es'`，与 MySQL/Doris/SLS 走**同一条治理取数口**。
--
-- 于是 `dataset:es` 变成**死开关**：它的裁决点（`_require_es_config_perm`）已经不存在，
-- 代码里没有任何路径再检查它。留着会在「角色权限」页渲出一个勾了不生效的选项 ——
-- 属于 menu-permission-sync 里"配置看得见、行为说没有"的空承诺，比缺一个开关更难排查。
--
-- 现网事实（本迁移执行前已核对）：
--   `adh_perm_registry` 有 1 行 dataset:es；`adh_role_perms` **无任何角色持有**；
--   `adh_menu_registry` 的 `data:datasets` 菜单挂的是 `dataset:read`（本码 api_pattern 恒为空，
--   从未参与路径鉴权，也从未回填进菜单）；`adh_datasets` 里 `source_type='es'` 与
--   `es_pattern` 均 0 行（阶段二已全迁 ES 数据源）。
--
-- 为什么不做"改挂到 ES 型数据源数据集"的复用：
--   ES 源型数据集就是普通 SQL 数据集，其配置面已由 `dataset:manage` + 数据源授权
--   （`role_service.get_user_allowed_datasources`）双重把关；再叠一层 `dataset:es`
--   会造出"两个码管同一件事、少勾一个就配不了"的假门（治理域内不需要通道专属开关，
--   这正是当初把 ES 划在治理域之外才需要的东西）。
--
-- 幂等：三条语句各按 WHERE 命中，重复执行自然空转；菜单纠偏只在"确实挂成了本码"时生效。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/es_builtin_channel_retire_migration.sql
-- ============================================================================

-- ── 1. 菜单兜底纠偏（正常情况下不命中：本码从未回填进菜单）──────────────────
--    若某环境把菜单项直接挂到了 `dataset:es`，删码会让该项在权限页失去归属；
--    这里统一回落到数据集配置面的既有码 `dataset:manage`，避免"菜单在、码没了"。
UPDATE adh_menu_registry
   SET perm_code = 'dataset:manage'
 WHERE perm_code = 'dataset:es';

-- ── 2. 角色持有关系（现网 0 行，其他环境可能有人手工勾过）───────────────────
DELETE FROM adh_role_perms
 WHERE perm_code = 'dataset:es';

-- ── 3. 权限码登记表 ────────────────────────────────────────────────────────
DELETE FROM adh_perm_registry
 WHERE perm_code = 'dataset:es';

-- ── 4. 列注释与真实类型对齐（不改类型、不动数据，纯元数据）──────────────────
--    `source_type` 不再有 'es' 取值：内置通道退役后 ES 数据集是 'sql'，
--    执行通道的判定唯一来自 `execution_is_es`（数据源 db_type），注释必须同步。
--    `es_pattern` 列保留（存量结构不回滚、便于跨环境核对历史值），仅注释改为已退役。
--    列宽按现库实际定义写 varchar(256)：这里只改注释，不得顺手改长度（收窄会在存量值上直接截断）。
ALTER TABLE adh_datasets
  MODIFY COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'semantic'
  COMMENT 'semantic|sql|both（ES 数据集=sql + 执行源 db_type=es；内置通道已退役）';

ALTER TABLE adh_datasets
  MODIFY COLUMN es_pattern VARCHAR(256) NOT NULL DEFAULT ''
  COMMENT '已退役（内置 Elasticsearch 通道的数据集级范围收窄）；现网恒为空，请勿写入';
