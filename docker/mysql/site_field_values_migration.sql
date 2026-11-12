-- ============================================================================
-- 站点 → 业务字段取值映射（2026-11-10）
--
-- 背景（全部为活库实测，不是推测）：同一个"新加坡站点"在不同字段上写法不同——
--   业务表 t_equipment_warning_log.site ：新加坡库 100% 写成 'global'（989766 行），
--                                         'int' 一条都没有 → 看板下发 'int' 得 0 行
--   日志侧 observability_rum/syslog.region：新加坡写成 'en'（cn/jp/eu/uk 与站点代码同名）
--   站点事实源 adh_sites.site_code：新加坡是 'int'（display_name=新加坡站点）
-- 所以「站点」与「字段取值」不是一回事，必须有一张按字段区分的映射，且映射是配置：
--   ① 不 hardcode 在代码里（管理员可在「系统管理 → 站点维护」看到并改）；
--   ② 翻译只发生在唯一翻译点 backend/semantics/param_plan.bound_args；
--   ③ 哪个入参该按站点映射由**数据集入参声明的 value_domain** 显式登记，
--      不靠变量名白名单猜（换个名字叫 area/zone 也要能配，见 ontology-modeling 纪律）。
--
-- 本文件可重复执行：加列走 information_schema 判定；种子只在"没配过"时写，不覆盖人工改动。
-- ============================================================================

-- ── 1) adh_sites.field_values：{字段名: 该站点在此字段上的取值} ────────────────
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_sites'
      AND COLUMN_NAME = 'field_values') = 0,
  'ALTER TABLE adh_sites ADD COLUMN field_values JSON NULL COMMENT ''站点在各业务字段上的取值映射 {字段名: 值}，如 {"site":"global","region":"en"}；缺键按站点代码原样下发''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2) 种子：按实测值填，只填还没配过的行 ────────────────────────────────────
-- 中国/日本/欧洲/英国：两个字段都等于站点代码（配出来是为了可见可改，不是留空靠猜）
UPDATE adh_sites
SET field_values = CASE site_code
      WHEN 'cn' THEN CAST('{"site": "cn", "region": "cn"}' AS JSON)
      WHEN 'int' THEN CAST('{"site": "global", "region": "en"}' AS JSON)
      WHEN 'jp' THEN CAST('{"site": "jp", "region": "jp"}' AS JSON)
      WHEN 'eu' THEN CAST('{"site": "eu", "region": "eu"}' AS JSON)
      WHEN 'uk' THEN CAST('{"site": "uk", "region": "uk"}' AS JSON)
    END
WHERE site_code IN ('cn', 'int', 'jp', 'eu', 'uk')
  AND field_values IS NULL;

-- 聚合态「全部站点」不配映射：选它就是不给值、不参与筛选（空串），
-- 给它编一个字段值等于把"不筛"变成"筛某个具体站点"，属于造错数。

-- ── 3) 数据集入参登记值域：已声明 site / region 变量的数据集标 value_domain='site' ─
-- 只补没登记过的（value_domain 缺省或空串），人工改成别的判据不覆盖。
UPDATE adh_datasets
SET variables = JSON_SET(variables, '$.site.value_domain', 'site')
WHERE variables IS NOT NULL AND JSON_VALID(variables)
  AND JSON_CONTAINS_PATH(variables, 'one', '$.site')
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(variables, '$.site.value_domain')), '') = '';

UPDATE adh_datasets
SET variables = JSON_SET(variables, '$.region.value_domain', 'site')
WHERE variables IS NOT NULL AND JSON_VALID(variables)
  AND JSON_CONTAINS_PATH(variables, 'one', '$.region')
  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(variables, '$.region.value_domain')), '') = '';
