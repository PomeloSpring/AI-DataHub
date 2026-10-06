-- 数据产品层（L1）迁移（幂等: CREATE IF NOT EXISTS + ON DUPLICATE KEY 按 uk_product_name）
--
-- 为什么需要它：本体对象现在绑在「数据源连接 + 裸物理表名」上，回答不了
--   ① 这张表谁产出 ② 什么时候产出、跑成功没 ③ 列变了谁负责 ④ 能不能被本体引用
-- 数据产品给"可被本体引用的表"一个平台级稳定身份 + 数据契约 + 血缘 + 版本。
--
-- 职责划分（不得混淆）：
--   adh_table_info     = 物理元数据事实源（表/列/类型/注释），由元数据同步维护
--   adh_data_products  = 治理身份（谁产出/契约/版本/能否被引用），由加工层登记
--   adh_datasets       = BI 消费封装（语义/SQL 双来源），在数据产品之上
-- 身份用 product_name 全局唯一且不含 id：数据源删除重建后 id 会变、name 不变。

-- 注意：应用实际连的元数据库是 adh2（见 services/.env 的 METADATA_DB_DATABASE）。
-- 仓库里有 19 个历史迁移写的是 `USE adh;`，会建到另一个库，属存量隐患；
-- 本文件与那 9 个 `USE adh2;` 保持一致。
USE adh2;

CREATE TABLE IF NOT EXISTS adh_data_products (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_name      VARCHAR(128) NOT NULL COMMENT '数据产品名(全局唯一稳定键, 不含内部 id)',
  display_name      VARCHAR(256) DEFAULT '' COMMENT '可读名(对用户展示用 name 而非 id)',
  description       VARCHAR(1024) DEFAULT '',
  domain            VARCHAR(64)  DEFAULT '' COMMENT '业务域(与 adh_tag_values「业务域」同口径)',

  -- 物理定位：是身份的**属性**，不是身份本身（改名/换库不换 product_name）
  datasource_name   VARCHAR(128) NOT NULL DEFAULT '' COMMENT '稳定关联键: 数据源删除重建后按名重关联',
  catalog_name      VARCHAR(128) DEFAULT '',
  db_name           VARCHAR(128) DEFAULT '',
  physical_table    VARCHAR(200) NOT NULL,
  catalog_ref       VARCHAR(400) DEFAULT '' COMMENT 'catalog.db.table 三段式',

  -- 产出血统
  producer_kind     VARCHAR(24)  NOT NULL DEFAULT 'manual'
                    COMMENT 'sync_task|dag_workflow|manual|external',
  producer_ref      VARCHAR(200) DEFAULT '' COMMENT '产出任务/工作流标识(如 sync_task:12)',
  upstream_refs     JSON         COMMENT '上游资产清单 [字符串]，供血缘/影响分析',
  data_version      VARCHAR(64)  DEFAULT '' COMMENT '数据快照/水位线标记',

  -- 数据契约
  schema_hash       CHAR(64)     DEFAULT '' COMMENT '列集指纹(name+type 排序后哈希)',
  schema_version    INT          NOT NULL DEFAULT 1 COMMENT '契约版本，schema 变更时 +1',
  compatibility     VARCHAR(16)  NOT NULL DEFAULT 'backward'
                    COMMENT 'backward|forward|full|none 向后兼容策略',
  quality_rules     JSON         COMMENT '质量规则(非空率/唯一性/值域/波动阈值)',
  sla               JSON         COMMENT '产出时间承诺/新鲜度/可用性',

  -- 分级与责任
  classification    VARCHAR(16)  DEFAULT 'internal'
                    COMMENT 'public|internal|confidential|secret',
  tier              VARCHAR(16)  DEFAULT '' COMMENT '加工分层(可空; 也可由 producer_kind 表达)',
  owner             VARCHAR(128) DEFAULT '' COMMENT '责任人/团队',
  status            VARCHAR(16)  NOT NULL DEFAULT 'draft'
                    COMMENT 'draft|certified|deprecated|retired；certified 才可被本体引用',

  created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uk_product_name (product_name),
  KEY idx_dp_table (datasource_name, physical_table),
  KEY idx_dp_status (status),
  KEY idx_dp_producer (producer_kind, producer_ref)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据产品(治理身份+数据契约+血缘)';

CREATE TABLE IF NOT EXISTS adh_data_product_versions (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id        BIGINT NOT NULL,
  product_name      VARCHAR(128) NOT NULL COMMENT '冗余存名, 避免 id 变更后关联断',
  schema_version    INT NOT NULL,
  schema_hash       CHAR(64) DEFAULT '',
  columns_snapshot  JSON COMMENT '列快照 [{name,type,comment}]，供影响分析与回溯',
  added_columns     JSON COMMENT '本次新增列',
  dropped_columns   JSON COMMENT '本次删除列(破坏性变更)',
  changed_columns   JSON COMMENT '本次类型变更列',
  compatibility     VARCHAR(16) DEFAULT 'backward' COMMENT '本次变更的兼容性判定',
  is_breaking       TINYINT DEFAULT 0 COMMENT '是否破坏性变更(需审批+通知消费者)',
  note              VARCHAR(512) DEFAULT '',
  created_by        VARCHAR(64) DEFAULT 'system',
  created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dpv_version (product_id, schema_version),
  KEY idx_dpv_product (product_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据产品 schema 版本与变更记录';
