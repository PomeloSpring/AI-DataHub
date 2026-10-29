-- ============================================================================
-- 合并业务本体与源本体：kind='source' 退役（幂等，可重复执行）
--
-- 背景（决策已拍板）：源本体（kind='source'）与业务本体（kind='business'）彻底合并，
--   只保留 system（平台元数据层）与 business（非 system 统统是业务本体）两种 kind。
--   business 单例已解除：多个业务本体可并存，可见性按「授权数据源」收口
--   （模型触达源 = {主源 datasource_id} ∪ {deploy_sites 路由源}；与角色授权源有交集即可见）。
--
-- 迁移形态对齐既有事实：`联耀业务本体` 本就是 kind='business' + datasource_id>0 + deploy_sites，
--   即"业务本体带主源 + 多站部署"是既有形态；原 source 模型（带单一主源）合并后同样落到这条轨道。
--
-- 纪律：本文件**不改数据行内容**，只改 kind 标记与部署元数据（deploy_sites）。
--   执行前请对 adh_ontology_models 备份（见回归门禁）。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 回填 deploy_sites（必须在翻转 kind 之前做，用 kind='source' 精确圈定这批历史模型）
-- ----------------------------------------------------------------------------
-- 口径：按「主源归属站点」为原 source 模型生成单站部署 [{site_code, datasource(业务名)}]。
--   - datasource 存业务名（name 全局唯一、删除重建 id 会变而 name 不变，见 ontology-modeling §2）；
--   - 仅对"主源已站点化（site_code 非空）且当前 deploy_sites 为空"的模型回填，
--     不覆盖已有非空 deploy_sites（避免破坏管理员手工配置）；
--   - 主源未站点化的（如数仓 Doris：site_code=''）保持 deploy_sites NULL，其路由仍走
--     datasource_id 主源 + 服务端 scoped_metadata 授权源收口，与站点无关。
UPDATE adh_ontology_models m
    JOIN adh_datasources ds ON ds.id = m.datasource_id
   SET m.deploy_sites = JSON_ARRAY(JSON_OBJECT('site_code', ds.site_code, 'datasource', ds.name))
 WHERE m.kind = 'source'
   AND COALESCE(m.datasource_id, 0) > 0
   AND COALESCE(ds.site_code, '') <> ''
   AND (m.deploy_sites IS NULL OR JSON_LENGTH(m.deploy_sites) = 0);

-- ----------------------------------------------------------------------------
-- 2. kind='source' → 'business'（源本体退役）
-- ----------------------------------------------------------------------------
-- kind 列为 VARCHAR(16)（见 ontology_ownership_migration），无 ENUM/CHECK 约束，
--   因此无需放宽取值域；翻转后不残留 'source'。
UPDATE adh_ontology_models
   SET kind = 'business'
 WHERE kind = 'source';

-- ----------------------------------------------------------------------------
-- 3. 核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- 无 kind='source' 残留：
--   SELECT COUNT(*) AS source_left FROM adh_ontology_models WHERE kind='source';      -- 期望 0
-- 各 kind 分布（合并后只有 business/system）：
--   SELECT kind, COUNT(*) c FROM adh_ontology_models GROUP BY kind;
-- 被合并模型的 deploy_sites 生效情况（ARMS 观测本体→cn 单站；数据仓库模型主源未站点化→NULL）：
--   SELECT id, name, datasource_id, deploy_sites FROM adh_ontology_models
--    WHERE name IN ('ARMS RUM 观测本体', '数据仓库模型');
