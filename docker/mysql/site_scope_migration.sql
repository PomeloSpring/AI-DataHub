-- ============================================================================
-- 站点作用域改造（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 为什么要站点表：多站点部署原先靠「对象 × 站点部署绑定」表达——27 个对象 × 5 站
-- = 135 行机械复制（同构站的物理表名/列映射完全一致），既难维护又易漏配。
-- 改造后站点是一等配置，三层各司其职：
--   adh_sites                 = 站点清单（唯一事实源，含聚合态「全球」）
--   adh_datasources.site_code = 数据源属于哪个站点（部署拓扑事实）
--   adh_ontology_models.deploy_sites = 业务本体整体适用哪些站点 + 每站点路由到哪个源
--                                    （一次配置全对象共享，取代逐对象逐站登记）
-- 站点差异（某站 schema 真不同）一律「为该站单独配一个本体」或「直接用源本体」表达，
-- 不再回到逐对象逐站的绑定行。
--
-- 可选站点由用户角色配置决定 = **派生**（role 授权数据源 ⋈ site_code），不建
-- adh_role_site_access——避免第二套「谁能查什么」裁决（waker-datasource-domain §1）。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 站点清单表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_sites (
    site_code     VARCHAR(32)  NOT NULL COMMENT '站点代码(cn|int|eu|uk|jp|global)',
    display_name  VARCHAR(64)  NOT NULL COMMENT '站点业务名(界面/提示词显示)',
    is_aggregate  TINYINT      NOT NULL DEFAULT 0 COMMENT '1=聚合态(全球)，不对应单一数据源',
    sort_order    INT          NOT NULL DEFAULT 0 COMMENT '选择器展示顺序',
    is_active     TINYINT      NOT NULL DEFAULT 1,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (site_code),
    INDEX idx_site_active_sort (is_active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='站点清单(多站点部署拓扑)';

-- 种子：6 个站点。ON DUPLICATE 只回写顺序/聚合标记，不改 display_name/is_active
-- （管理员改名或停用后重跑迁移不得被覆盖）。
INSERT INTO adh_sites (site_code, display_name, is_aggregate, sort_order) VALUES
    ('global', '全球站点', 1, 0),
    ('cn',     '中国站点', 0, 10),
    ('int',    '国际站点(新加坡)', 0, 20),
    ('eu',     '欧洲站点', 0, 30),
    ('uk',     '英国站点', 0, 40),
    ('jp',     '日本站点', 0, 50)
ON DUPLICATE KEY UPDATE is_aggregate = VALUES(is_aggregate),
                        sort_order   = VALUES(sort_order);

-- ----------------------------------------------------------------------------
-- 2. 数据源站点属性
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasources'
      AND COLUMN_NAME = 'site_code') = 0,
  'ALTER TABLE adh_datasources ADD COLUMN site_code VARCHAR(32) NOT NULL DEFAULT '''' COMMENT ''归属站点(adh_sites.site_code)；空=未站点化(平台库/单站部署)；不得填聚合态 global''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasources'
      AND INDEX_NAME = 'idx_ds_site') = 0,
  'ALTER TABLE adh_datasources ADD KEY idx_ds_site (site_code)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 回填：按数据源业务名精确匹配（name 全局唯一 = uk_datasource_name）。
-- 只动列出的源，未列出的（如平台库 Doris）保持未站点化。
UPDATE adh_datasources SET site_code = 'cn'
 WHERE name IN ('prd-cn', 'prd-sls-cn', 'arms-prd-cn') AND COALESCE(site_code, '') = '';
UPDATE adh_datasources SET site_code = 'int' WHERE name = 'prd-en' AND COALESCE(site_code, '') = '';
UPDATE adh_datasources SET site_code = 'eu'  WHERE name = 'prd-eu' AND COALESCE(site_code, '') = '';
UPDATE adh_datasources SET site_code = 'uk'  WHERE name = 'prd-uk' AND COALESCE(site_code, '') = '';
UPDATE adh_datasources SET site_code = 'jp'  WHERE name = 'prd-jp' AND COALESCE(site_code, '') = '';

-- ----------------------------------------------------------------------------
-- 3. 业务本体模型级适用站点
-- ----------------------------------------------------------------------------
-- deploy_sites = [{"site_code":"cn","datasource":"prd-cn"}, ...]
-- datasource 存业务名（删除重建 id 会变、name 不变，见 ontology-modeling §2）。
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_models'
      AND COLUMN_NAME = 'deploy_sites') = 0,
  'ALTER TABLE adh_ontology_models ADD COLUMN deploy_sites JSON NULL COMMENT ''业务本体适用站点：[{site_code, datasource(业务名)}]；NULL=未配置(沿用逐对象部署绑定行)''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4. 会话站点（智能问数头部选择器的持久化）
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_conversations'
      AND COLUMN_NAME = 'site_code') = 0,
  'ALTER TABLE adh_conversations ADD COLUMN site_code VARCHAR(32) NOT NULL DEFAULT '''' COMMENT ''会话查询站点(adh_sites.site_code)；空=未选择(按会话数据源)''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 5. 接口权限：聊天站点选择器是「智能问答」的组成入口，不是数据源管理入口
-- ----------------------------------------------------------------------------
-- viewer 等无 datasource:read 码的角色也能用聊天，因此把**只读派生清单**
-- /api/datasources/authorized-sites 挂到既有 chat:use 的 api_pattern 上（不新增
-- 权限码、不新增菜单，避免 menu-permission-sync 的配了没用漂移）；
-- /api/datasources/sites（站点清单）仍按 datasource:read 门控（只服务管理页）。
-- 幂等：已包含则不重复追加。
UPDATE adh_perm_registry
   SET api_pattern = CONCAT(api_pattern, ',/api/datasources/authorized-sites')
 WHERE perm_code = 'chat:use'
   AND api_pattern NOT LIKE '%/api/datasources/authorized-sites%';

-- ----------------------------------------------------------------------------
-- 6. 存量核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- SELECT 'sites' AS chk, site_code, display_name, is_aggregate FROM adh_sites ORDER BY sort_order;
-- SELECT 'datasource_site' AS chk, name, site_code FROM adh_datasources ORDER BY name;
-- SELECT 'model_deploy_sites' AS chk, id, name, deploy_sites FROM adh_ontology_models WHERE kind='business';
