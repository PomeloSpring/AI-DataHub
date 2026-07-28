-- AI-DataHub Doris 向量表（RAG 元数据检索专用）
-- 仅包含需要 HNSW 向量索引的表，其他表使用 MySQL
-- Run: mysql -h <doris_host> -P <doris_port> -u <user> -p < init.sql

CREATE DATABASE IF NOT EXISTS adh;
USE adh;

-- ============================================================================
-- 表级元数据（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_info (
    id                  BIGINT NOT NULL,
    datasource_id       BIGINT NOT NULL DEFAULT 0,
    table_name          VARCHAR(128) NOT NULL,
    table_comment       VARCHAR(512) DEFAULT '',
    table_business_desc VARCHAR(1024) DEFAULT '',
    keywords            VARCHAR(512) DEFAULT '',
    region_tag          VARCHAR(16)  DEFAULT '',
    domain_tag          VARCHAR(64)  DEFAULT '',
    is_active           TINYINT      DEFAULT 1,
    sync_time           DATETIME     NOT NULL,
    embedding           ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id, table_name)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ============================================================================
-- 字段级元数据（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_column_metadata (
    id              BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    table_name      VARCHAR(128) NOT NULL,
    column_name     VARCHAR(128) NOT NULL,
    data_type       VARCHAR(64)  NOT NULL,
    column_comment  VARCHAR(512) DEFAULT '',
    business_desc   VARCHAR(1024) DEFAULT '',
    is_key          VARCHAR(8)   DEFAULT 'false',
    is_nullable     VARCHAR(8)   DEFAULT 'YES',
    is_active       TINYINT      DEFAULT 1,
    sync_time       DATETIME     NOT NULL,
    embedding       ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id, table_name, column_name)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ============================================================================
-- SQL 模板（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_templates (
    id              BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    template_id     VARCHAR(64)  NOT NULL,
    template_name   VARCHAR(256) NOT NULL,
    category        VARCHAR(64)  NOT NULL,
    intent_keywords VARCHAR(512) NOT NULL,
    sql_template    TEXT         NOT NULL,
    variables       TEXT         DEFAULT '',
    rules           TEXT         DEFAULT '',
    description     VARCHAR(512) DEFAULT '',
    usage_count     INT          DEFAULT 0,
    is_active       TINYINT      DEFAULT 1,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL,
    embedding       ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id, template_id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ============================================================================
-- 业务术语（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_business_terms (
    id              BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    term_cn         VARCHAR(128) NOT NULL,
    term_en         VARCHAR(128) DEFAULT '',
    term_aliases    VARCHAR(512) DEFAULT '',
    term_type       VARCHAR(32)  NOT NULL,
    target_table    VARCHAR(128) DEFAULT '',
    target_column   VARCHAR(128) DEFAULT '',
    calculation     TEXT         DEFAULT '',
    description     VARCHAR(512) DEFAULT '',
    usage_count     INT          DEFAULT 0,
    is_active       TINYINT      DEFAULT 1,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL,
    embedding       ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id, term_cn)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ============================================================================
-- 表关联关系（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_relations (
    id                BIGINT NOT NULL,
    datasource_id     BIGINT NOT NULL DEFAULT 0,
    source_table      VARCHAR(128) NOT NULL,
    source_column     VARCHAR(128) NOT NULL,
    target_table      VARCHAR(128) NOT NULL,
    target_column     VARCHAR(128) NOT NULL,
    relation_type     VARCHAR(16)  DEFAULT '1:N',
    join_type         VARCHAR(16)  DEFAULT 'INNER',
    description       VARCHAR(512) DEFAULT '',
    is_active         TINYINT      DEFAULT 1,
    created_at        DATETIME     NOT NULL,
    updated_at        DATETIME     NOT NULL,
    embedding         ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ============================================================================
-- SQL 纠错学习（向量检索）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_corrections (
    id              BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    question        TEXT NOT NULL,
    failed_sql      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    corrected_sql   TEXT NOT NULL,
    tables_used     VARCHAR(512) DEFAULT '',
    correction_type VARCHAR(32) DEFAULT 'auto',
    status          VARCHAR(16) DEFAULT 'pending',
    usage_count     INT DEFAULT 0,
    created_at      DATETIME NOT NULL,
    updated_at      DATETIME NOT NULL,
    embedding       ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");
