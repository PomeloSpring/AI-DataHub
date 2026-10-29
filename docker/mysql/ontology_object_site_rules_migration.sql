-- ============================================================================
-- 对象级站点白名单（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 背景：site_scope 改造后 deploy_sites 是**模型级**声明——一份契约 × N 站，
-- 全模型对象共享（cross_site 无条件做 对象×站点 笛卡尔积）。但「同构多站 +
-- 部分站点才有某对象」时，模型级一刀切会过度声明：把只在个别站点存在的对象
-- 也宣称在所有站点可用 → 路由到该站却没有这张表 → 查不到/错路由。
--
-- object_site_rules = {"object_key": ["cn","int",...]}
--   · 仅显式列出的对象受限：只在所列站点可查；
--   · 未列出的对象 = 沿用模型级全部声明站点（默认全可用，向后兼容）；
--   · 所列站点必须是该模型 deploy_sites 已声明的站点（保存/激活时校验）。
-- 生效三处：binding_resolver._route_by_site（取数硬门）、
--          cross_site._load_facts（LLM 部署清单不再广告未部署站点）、
--          ontology_service.find_deploy_site_issues（激活校验）。
-- ============================================================================

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_models'
      AND COLUMN_NAME = 'object_site_rules') = 0,
  'ALTER TABLE adh_ontology_models ADD COLUMN object_site_rules JSON NULL COMMENT ''对象级站点白名单 {object_key:[site_code]}；NULL/未列对象=沿用模型级全部声明站点''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 核验（人工看输出，不自动改数据）
-- SELECT 'object_site_rules' AS chk, id, name, object_site_rules
--   FROM adh_ontology_models WHERE object_site_rules IS NOT NULL;
