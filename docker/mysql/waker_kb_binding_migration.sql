-- Waker 知识库绑定 — 在共享资源引用(MCP/数据源)基础上增加"知识库"引用
-- adh_wakers 新增 knowledge_base_ids JSON 列,存引用的 adh_knowledge_bases.id 列表
-- 幂等: DDL 经 information_schema 守卫,可重复执行。

USE adh;

-- ============================================================================
-- adh_wakers 增加 knowledge_base_ids 列(幂等)
-- ============================================================================
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_wakers'
      AND COLUMN_NAME = 'knowledge_base_ids'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_wakers ADD COLUMN knowledge_base_ids JSON COMMENT ''引用的共享知识库 ID 列表'' AFTER datasource_ids',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
