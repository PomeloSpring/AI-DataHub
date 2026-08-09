-- Data Lineage Migration
-- 为数据血缘图创建数据源和ETL任务表

-- 数据源表（如果不存在则创建）
CREATE TABLE IF NOT EXISTS adh_datasources (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(200) NOT NULL COMMENT '数据源名称',
  type VARCHAR(50) NOT NULL COMMENT '数据源类型(mysql/doris/elasticsearch/api/file)',
  host VARCHAR(200) COMMENT '主机地址',
  port INT COMMENT '端口',
  database_name VARCHAR(100) COMMENT '数据库名',
  description TEXT COMMENT '描述',
  status VARCHAR(20) DEFAULT 'active' COMMENT '状态(active/inactive/error)',
  config JSON COMMENT '配置信息',
  owner VARCHAR(100) COMMENT '负责人',
  is_active TINYINT DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_type (type),
  INDEX idx_status (status),
  INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源表';

-- ETL任务表
CREATE TABLE IF NOT EXISTS adh_etl_tasks (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(200) NOT NULL COMMENT '任务名称',
  task_type VARCHAR(50) NOT NULL COMMENT '任务类型(export/import/transform/sync)',
  schedule VARCHAR(100) COMMENT '调度周期(cron表达式)',
  source_datasource_id INT COMMENT '源数据源ID',
  source_tables TEXT COMMENT '源表(JSON数组)',
  target_datasource_id INT COMMENT '目标数据源ID',
  target_tables TEXT COMMENT '目标表(JSON数组)',
  transform_rules JSON COMMENT '转换规则',
  status VARCHAR(20) DEFAULT 'active' COMMENT '状态(active/paused/error/running)',
  last_run_at TIMESTAMP NULL COMMENT '最后运行时间',
  next_run_at TIMESTAMP NULL COMMENT '下次运行时间',
  description TEXT COMMENT '描述',
  owner VARCHAR(100) COMMENT '负责人',
  is_active TINYINT DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_type (task_type),
  INDEX idx_status (status),
  INDEX idx_source (source_datasource_id),
  INDEX idx_target (target_datasource_id),
  INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETL任务表';

-- ETL任务依赖关系表
CREATE TABLE IF NOT EXISTS adh_etl_dependencies (
  id INT PRIMARY KEY AUTO_INCREMENT,
  task_id INT NOT NULL COMMENT '任务ID',
  depends_on_task_id INT NOT NULL COMMENT '依赖的任务ID',
  dependency_type VARCHAR(50) DEFAULT 'sequential' COMMENT '依赖类型(sequential/conditional/parallel)',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dependency (task_id, depends_on_task_id),
  INDEX idx_task (task_id),
  INDEX idx_depends (depends_on_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETL任务依赖关系表';

-- 数据血缘关系表（记录表级别的数据流转）
CREATE TABLE IF NOT EXISTS adh_data_lineage (
  id INT PRIMARY KEY AUTO_INCREMENT,
  source_type VARCHAR(50) NOT NULL COMMENT '源类型(datasource/table/task)',
  source_id VARCHAR(200) NOT NULL COMMENT '源标识',
  source_name VARCHAR(200) COMMENT '源名称',
  target_type VARCHAR(50) NOT NULL COMMENT '目标类型(datasource/table/task)',
  target_id VARCHAR(200) NOT NULL COMMENT '目标标识',
  target_name VARCHAR(200) COMMENT '目标名称',
  relation_type VARCHAR(50) NOT NULL COMMENT '关系类型(produces/consumes/transforms/feeds)',
  etl_task_id INT COMMENT '关联的ETL任务ID',
  description TEXT COMMENT '描述',
  is_active TINYINT DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_source (source_type, source_id),
  INDEX idx_target (target_type, target_id),
  INDEX idx_relation (relation_type),
  INDEX idx_task (etl_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据血缘关系表';

-- 插入示例数据源
INSERT INTO adh_datasources (name, type, host, port, database_name, description, status) VALUES
('主MySQL', 'mysql', 'localhost', 3306, 'ai_datahub', '主数据库', 'active'),
('Doris分析库', 'doris', 'localhost', 9030, 'analytics', '分析型数据库', 'active'),
('ES日志库', 'elasticsearch', 'localhost', 9200, 'logs', '日志存储', 'active');

-- 插入示例ETL任务
INSERT INTO adh_etl_tasks (name, task_type, source_datasource_id, source_tables, target_datasource_id, target_tables, description, status) VALUES
('用户数据同步', 'sync', 1, '["users"]', 2, '["dwd_users"]', 'MySQL用户表同步到Doris', 'active'),
('订单数据导出', 'export', 1, '["orders","order_items"]', 2, '["dwd_orders"]', '订单数据导出到Doris', 'active'),
('日志采集', 'import', 3, '["access_logs"]', 2, '["dwd_access_logs"]', 'ES日志导入Doris', 'active');

-- 插入ETL依赖关系
INSERT INTO adh_etl_dependencies (task_id, depends_on_task_id, dependency_type) VALUES
(2, 1, 'sequential');

-- 插入数据血缘关系
INSERT INTO adh_data_lineage (source_type, source_id, source_name, target_type, target_id, target_name, relation_type, etl_task_id) VALUES
('datasource', '1', '主MySQL', 'datasource', '2', 'Doris分析库', 'feeds', NULL),
('table', 'users', 'users', 'table', 'dwd_users', 'dwd_users', 'produces', 1),
('table', 'orders', 'orders', 'table', 'dwd_orders', 'dwd_orders', 'produces', 2),
('table', 'order_items', 'order_items', 'table', 'dwd_orders', 'dwd_orders', 'produces', 2),
('datasource', '3', 'ES日志库', 'table', 'dwd_access_logs', 'dwd_access_logs', 'produces', 3);
