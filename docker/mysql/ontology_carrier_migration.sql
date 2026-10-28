-- ============================================================================
-- 业务本体跨源拓扑改造 M1：承载（carrier）事实源（幂等，可重复执行）
--
-- 为什么要独立一张表：业务本体原先只有 `deploy_sites`（模型级「一份契约 × N 站」），
-- 它表达的是**同构多站点复制**——一站一源、且源必须归属该站。而真实的跨源拓扑是
-- 异构的：同一个业务概念（用户/案例）可能在
--   · 各站点业务库（明细，同构）
--   · 数仓 Doris（全局汇总，可算全局去重，T+1）
--   · SLS 日志源（行为明细，方言与形状都不同）
-- 各有承载。把它们塞进 `deploy_sites` 会踩两个硬伤：Doris 未站点化（site_code=''）
-- 进不来；SLS 即使登记也会被契约行的 physical_table/column_map 当成同构替身 → 错数。
--
-- 分层纪律（与 ontology-modeling §1/§2 一致）：
--   carrier 只存**业务名级路由**（数据源业务名 + 指向哪个源本体的哪个对象 + 粒度/新鲜度/权威），
--   物理表与列映射的唯一事实源仍在 `adh_ontology_bindings`（源本体的部署绑定层）。
--   本表不是 canonical doc 的一部分（部署层不属于业务语义 doc，v0.5 B6b 同口径）。
--
-- 与 deploy_sites 的分工（不得混用，避免双口径）：
--   deploy_sites = 同构站点展开（detail 承载未显式登记时的回落路径）
--   carriers     = 跨源/跨层承载（detail 显式登记 + global/log/mirror）
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 承载清单表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_ontology_carriers (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  model_id      BIGINT       NOT NULL COMMENT '业务本体模型 id（归属模型，不是数据源）',
  object_key    VARCHAR(64)  NOT NULL COMMENT '业务对象 key（该模型 canonical doc 内的对象）',
  carrier_key   VARCHAR(64)  NOT NULL COMMENT '承载标识（对象内唯一，如 detail/global/sls_log；LLM 侧用它选路）',
  display_name  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '承载业务名（界面/提示词显示，如「数仓全局汇总」）',
  carrier_type  ENUM('detail','global','log','mirror') NOT NULL DEFAULT 'detail'
                COMMENT 'detail=各站点明细 | global=全局汇总(可算全局去重) | log=日志/埋点 | mirror=镜像副本',
  datasource    VARCHAR(128) NOT NULL DEFAULT '' COMMENT '承载数据源业务名（全局唯一 uk_datasource_name）；空=按 deploy_sites 逐站展开',
  sites         JSON NULL COMMENT '适用站点代码数组；NULL/空=不限定（global/mirror 通常不限站）',
  source_model  VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物理形状来源：源本体模型名（global/log/mirror 必填）',
  source_object VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '物理形状来源：该源本体的对象 key（悬空即拒，见 topology 校验）',
  grain         VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '数据粒度（明细/日汇总/用户级…），影响能否回答趋势与去重',
  freshness     ENUM('realtime','t_plus_1','lagged') NOT NULL DEFAULT 'realtime'
                COMMENT '新鲜度：问"今天"时 t_plus_1/lagged 承载不参与择路',
  as_of_note    VARCHAR(255) NOT NULL DEFAULT '' COMMENT '口径时效说明（对外可见文案，如「T+1，不含今天」）',
  is_authority  TINYINT      NOT NULL DEFAULT 0 COMMENT '1=该对象此口径的权威承载（每对象最多 1 条，写侧校验）',
  priority      INT          NOT NULL DEFAULT 100 COMMENT '同类型内择路顺序（小者优先）',
  size_class    VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '规模档位 small|large|huge（择路偏好与护栏参考）',
  status        ENUM('active','deprecated') NOT NULL DEFAULT 'active',
  created_by    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '创建人（服务端注入，不信任请求体）',
  -- 时间列用 UTC_TIMESTAMP 表达式默认值：写本地时会让按 UTC 窗口的监控页恒为空（历史坑）
  created_at    DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at    DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()) ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_carrier (model_id, object_key, carrier_key),
  KEY idx_carrier_object (object_key, status),
  KEY idx_carrier_model (model_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='业务本体承载清单(跨源拓扑：概念→可取数路径的唯一事实源)';

-- ----------------------------------------------------------------------------
-- 2. 接口权限：不新增权限码、不新增菜单
-- ----------------------------------------------------------------------------
-- 「取数路径」是「本体建模」页内的页签（不是新菜单项），读写端点
--   GET/PUT /api/catalog/ontology/models/{id}/carriers
-- 已被既有 pattern `/api/catalog/ontology/models*` 覆盖：
--   读 → ontology:read；写 → ontology:save / ontology:activate（api_pattern 是"持有即放行"，
--   只增不减，不在此重复登记，避免 menu-permission-sync 的"配了没用"漂移）。

-- 修存量漂移（纯增补，不收紧任何角色权限）：`ontology:create` / `ontology:publish`
-- 两个码的 api_pattern 写的是 `/api/ontology/...`，而路由实际挂在
-- `/api/catalog/ontology`（见 backend/app/main.py mount），即**从未匹配过任何请求**——
-- 角色页勾了这两个码等于没勾（空承诺）。补上真实路径，使其成为可用的独立授权。
UPDATE adh_perm_registry
   SET api_pattern = CONCAT(api_pattern, ',/api/catalog/ontology/models/*/objects')
 WHERE perm_code = 'ontology:create'
   AND api_pattern NOT LIKE '%/api/catalog/ontology/models/*/objects%';

UPDATE adh_perm_registry
   SET api_pattern = CONCAT(api_pattern, ',/api/catalog/ontology/models/*/publish')
 WHERE perm_code = 'ontology:publish'
   AND api_pattern NOT LIKE '%/api/catalog/ontology/models/*/publish%';

-- ----------------------------------------------------------------------------
-- 3. 核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- SELECT 'table' AS chk, COUNT(*) AS rows_total FROM adh_ontology_carriers;
-- SELECT 'perm' AS chk, perm_code, api_pattern FROM adh_perm_registry
--  WHERE perm_code IN ('ontology:create','ontology:publish');
