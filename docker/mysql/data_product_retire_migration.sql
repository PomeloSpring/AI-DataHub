-- 数据产品状态流转（PATCH /api/catalog/data-products/{name}/status）
--
-- 为什么需要这条迁移：`catalog:manage` 的 api_method 原为 `POST,PUT,DELETE`，不含 PATCH。
-- `backend/common/api_permission.py` 按「api_pattern × api_method」精确匹配，不补 PATCH
-- 的话新端点对非 admin 角色一律 403（典型症状：角色页能勾目录管理、页面也看得见，
-- 但退役按钮点了必拒）。
--
-- 业务背景：产品是「表 → 本体/数据集」之间的治理身份。物理表在源上被删除后，
-- `adh_table_info` 会随元数据同步删行，但 `adh_data_products` 不会自动退场；
-- `build_asset_lineage` 只跳过 `status='retired'` 的产品，于是指向已不存在表的产品
-- 节点与 produces 边会一直挂在血缘图上，LLM 据此下钻会拿到不存在的依赖。
-- 该端点就是「让治理身份体面退场」的唯一入口（只改 status/updated_at，不动契约版本）。
--
-- 幂等：只在该行缺 PATCH 时追加；重复执行 0 变更。

UPDATE adh_perm_registry
   SET api_method = CONCAT(COALESCE(NULLIF(TRIM(api_method), ''), 'POST,PUT,DELETE'), ',PATCH')
 WHERE perm_code = 'catalog:manage'
   AND FIND_IN_SET('PATCH', UPPER(REPLACE(COALESCE(api_method, ''), ' ', ''))) = 0;

-- 核验（应为 POST,PUT,DELETE,PATCH）：
-- SELECT perm_code, api_pattern, api_method FROM adh_perm_registry WHERE perm_code='catalog:manage';