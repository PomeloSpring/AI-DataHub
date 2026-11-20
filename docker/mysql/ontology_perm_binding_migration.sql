-- ============================================================================
-- 本体域权限绑定对齐：让「每个本体能力只由它自己的权限码把关」
--
-- 背景（2026-11-20 实测 adh_perm_registry + 逐路由 fnmatch 核对）：
--   1. ontology:read 只写了 /models* 与 /search → GET /kb-options、/kb-sync-options、
--      /generate/tasks 只被 catalog:read 兜住；只持 ontology:read 的角色在这些接口 403。
--   2. ontology:activate 用 POST /models* 宽模式 → 把 publish / kb-sync / archive / restore
--      一并放行（publish 另有自己的码，但并集语义下 activate 单独就能发布）。
--   3. POST /models/{id}/kb-sync（模型→知识库）属知识库动作，却只由 ontology:activate 把关，
--      kb:manage 的 api_pattern 只有 /api/knowledge-bases*。
--   4. ontology:create 的 /api/ontology/models/*/objects 前缀根本没有挂载（main.py 只挂
--      /api/catalog/ontology），且网页侧新增对象走 canonical doc（PUT /draft）——
--      该码实际只有 AS-BOT 工具通道（create_ontology_object），HTTP 层无落点。
--
-- 口径：子路径一律用字面量后缀锚定（fnmatch 的 * 跨 "/"，宽模式会连带放行同前缀的其它动作）。
-- 幂等：全部 UPDATE，带条件，可重复执行；不含 USE。
--
-- 未纳入本迁移（需产品决策，见 ontology-modeling.md §9「已知口径风险」）：
--   * catalog:read/manage 的 /api/catalog* 仍覆盖整个 /api/catalog/ontology/*（并集放行）；
--     收窄它会连带影响 /api/catalog/data-products*（当前无独立码）。
--   * DELETE /models/{id} 与 DELETE /models/{id}/draft 只被 catalog:manage 写域覆盖，
--     属已声明的例外（丢弃草案端点内另要求 ontology:save，构成双码交集）。
-- ============================================================================

-- 1. 读：覆盖本体前缀下全部 GET（模型/草案/版本/同步状态/生成任务/知识库候选）
UPDATE adh_perm_registry
   SET api_pattern='/api/catalog/ontology/*', api_method='GET'
 WHERE perm_code='ontology:read';

-- 2. 激活：从宽 POST /models* 收窄到三个生命周期动作（publish / kb-sync 各有其码）
UPDATE adh_perm_registry
   SET api_pattern='/api/catalog/ontology/models/*/activate,/api/catalog/ontology/models/*/archive,/api/catalog/ontology/models/*/restore',
       api_method='POST'
 WHERE perm_code='ontology:activate';

-- 3. 导入：锚定实际路由 /import-yaml（原 /import* 同时会匹配 /import-anything）
UPDATE adh_perm_registry
   SET api_pattern='/api/catalog/ontology/import-yaml', api_method='POST'
 WHERE perm_code='ontology:import';

-- 4. 新增对象：删掉从未挂载的 /api/ontology 前缀，只保留真实前缀（HTTP 无落点，
--    该码的消费方是 AS-BOT 通道；保留 pattern 以免未来补回 POST /objects 时再漂一次）
UPDATE adh_perm_registry
   SET api_pattern='/api/catalog/ontology/models/*/objects', api_method='POST'
 WHERE perm_code='ontology:create';

-- 5. 知识库：模型→知识库同步与挂接知识库归 kb:manage（原只有 /api/knowledge-bases*）
UPDATE adh_perm_registry
   SET api_pattern='/api/knowledge-bases*,/api/catalog/ontology/models/*/kb-sync,/api/catalog/ontology/models/*/kb',
       api_method='POST,PUT,DELETE'
 WHERE perm_code='kb:manage';

-- 6. 原生 SPARQL / 最短路径：`POST /api/graph/query` 会按语句 form 走 query **或 update**
--    （见 graph.py `_sparql_form`），本质是写通道，且与 /api/graph/{nodes,relations} 同前缀——
--    读码（graph:read 只 GET）无法安全表达"读一个 POST"，故归 graph:manage，不要塞给 graph:read。
--    历史缺陷：本体建模页 canonical doc 的 SPARQL 视图对非 admin 恒 403（两张图都打这个口）。
UPDATE adh_perm_registry
   SET api_pattern='/api/graph/rebuild*,/api/graph/sync*,/api/graph/query,/api/graph/shortest-path',
       api_method='POST'
 WHERE perm_code='graph:manage';