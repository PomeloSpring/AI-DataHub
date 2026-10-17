-- ============================================================================
-- 图谱生成入口登记修正：POST /api/graph/sync 之前不在任何权限码的 api_pattern 内
--
-- 背景：图谱重建端点是 /api/graph/sync（见 semhub/api/graph.py），而注册表里
--       graph:manage 只写了 /api/graph/rebuild*，graph:read 只覆盖 GET。
--       结果：非 admin 角色点「图谱生成」必被 api_permission 中间件 fail-closed 拒 403，
--       只有 admin 因旁路而看起来"能用"——这是入口与授权口径不一致，不是功能本身的问题。
-- 口径：graph:manage = 图谱重建管理（POST /api/graph/{rebuild,sync}），
--       并把 analyst 一并授予：analyst 已持有 ontology:activate/save，
--       而激活与保存本来就会级联重建同一张命名图，手动重建的爆炸半径并不更大。
-- 约定：不含 USE，幂等可重跑（UPDATE 带条件、授权用 NOT EXISTS 守卫，不用 INSERT IGNORE）。
-- ============================================================================

UPDATE adh_perm_registry
   SET api_pattern='/api/graph/rebuild*,/api/graph/sync*', api_method='POST'
 WHERE perm_code='graph:manage'
   AND api_pattern NOT LIKE '%/api/graph/sync%';

-- 授予 analyst（按名字取角色 id，不写死自增 id）
INSERT INTO adh_role_perms (role_id, perm_code)
SELECT r.id, 'graph:manage'
  FROM adh_roles r
 WHERE r.name='analyst' AND r.is_active=1
   AND NOT EXISTS (
     SELECT 1 FROM (SELECT * FROM adh_role_perms) rp
      WHERE rp.role_id=r.id AND rp.perm_code='graph:manage');
