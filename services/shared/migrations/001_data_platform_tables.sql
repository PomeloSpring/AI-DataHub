-- ============================================================
-- AI-DataHub 数据中台 — 数据库 Schema 变更
-- 新增表：数据质量、数据血缘、数据标准、敏感数据、指标管理、标签管理、数据同步
-- ============================================================

USE adh;

-- ============================================================
-- 1. 数据治理 (DataGov) — 数据质量
-- ============================================================

-- 数据质量规则
CREATE TABLE IF NOT EXISTS adh_quality_rules (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    rule_name VARCHAR(200) NOT NULL,
    description TEXT,
    rule_type ENUM('not_null','unique','range','format','referential','custom_sql','freshness','row_count','distribution') NOT NULL,
    target_datasource_id BIGINT,
    target_table VARCHAR(200),
    target_column VARCHAR(200),
    rule_config JSON NOT NULL COMMENT '规则参数（阈值、表达式等）',
    severity ENUM('low','medium','high','critical') DEFAULT 'medium',
    schedule_cron VARCHAR(50) COMMENT '定时执行cron表达式',
    is_active TINYINT DEFAULT 1,
    created_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_target (target_table),
    INDEX idx_type (rule_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量规则';

-- 数据质量检查结果
CREATE TABLE IF NOT EXISTS adh_quality_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_id BIGINT NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    check_time DATETIME NOT NULL,
    passed TINYINT NOT NULL,
    total_rows BIGINT DEFAULT 0,
    failed_rows BIGINT DEFAULT 0,
    pass_rate DECIMAL(5,2) DEFAULT 0.00,
    detail JSON COMMENT '详细失败记录采样',
    elapsed_ms INT DEFAULT 0,
    INDEX idx_rule (rule_id),
    INDEX idx_time (check_time),
    INDEX idx_workspace (workspace_id),
    INDEX idx_rule_time (rule_id, check_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量检查结果';

-- 数据质量报告
CREATE TABLE IF NOT EXISTS adh_quality_reports (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    report_date DATE NOT NULL,
    total_rules INT DEFAULT 0,
    passed_rules INT DEFAULT 0,
    failed_rules INT DEFAULT 0,
    overall_score DECIMAL(5,2) DEFAULT 0.00,
    summary JSON COMMENT '各维度得分',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace_date (workspace_id, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量报告';

-- ============================================================
-- 2. 数据治理 (DataGov) — 数据血缘
-- ============================================================

-- 血缘节点
CREATE TABLE IF NOT EXISTS adh_lineage_nodes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    node_type ENUM('table','column','etl_job','report','metric') NOT NULL,
    node_id VARCHAR(500) NOT NULL COMMENT '如 datasource_id.schema.table_name',
    node_name VARCHAR(500),
    datasource_id BIGINT,
    metadata JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_node (workspace_id, node_type, node_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_type (node_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='血缘节点';

-- 血缘边
CREATE TABLE IF NOT EXISTS adh_lineage_edges (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    source_node_id BIGINT NOT NULL,
    target_node_id BIGINT NOT NULL,
    edge_type ENUM('transform','derive','join','aggregate','filter') DEFAULT 'transform',
    transform_expr TEXT COMMENT '转换表达式',
    confidence DECIMAL(3,2) DEFAULT 1.00 COMMENT '置信度（自动解析 vs 手动标注）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_source (source_node_id),
    INDEX idx_target (target_node_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='血缘边';

-- ============================================================
-- 3. 数据治理 (DataGov) — 数据标准
-- ============================================================

CREATE TABLE IF NOT EXISTS adh_data_standards (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    standard_type ENUM('naming','encoding','measurement','format') NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    rule_config JSON NOT NULL COMMENT '标准规则配置',
    is_active TINYINT DEFAULT 1,
    created_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_type (standard_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据标准';

-- ============================================================
-- 4. 数据治理 (DataGov) — 敏感数据
-- ============================================================

CREATE TABLE IF NOT EXISTS adh_sensitive_fields (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    datasource_id BIGINT NOT NULL,
    table_name VARCHAR(200) NOT NULL,
    column_name VARCHAR(200) NOT NULL,
    sensitivity_level ENUM('low','medium','high','critical') DEFAULT 'medium',
    mask_type ENUM('full','partial','hash','none') DEFAULT 'partial',
    mask_config JSON COMMENT '脱敏配置',
    description VARCHAR(500),
    created_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_datasource (datasource_id),
    UNIQUE KEY uk_field (workspace_id, datasource_id, table_name, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='敏感字段标记';

-- ============================================================
-- 5. 数据目录 (DataCatalog) — 指标管理
-- ============================================================

-- 指标定义
CREATE TABLE IF NOT EXISTS adh_metrics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(200) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    metric_type ENUM('basic','derived','composite') DEFAULT 'basic',
    calculation_type ENUM('sum','count','avg','max','min','count_distinct','custom') NOT NULL,
    expression TEXT COMMENT '计算公式（派生指标）',
    unit VARCHAR(50) COMMENT '单位（次、元、%）',
    data_type VARCHAR(50) DEFAULT 'decimal',
    target_datasource_id BIGINT,
    target_table VARCHAR(200),
    target_column VARCHAR(200),
    dimensions JSON COMMENT '可分析维度列表',
    granularity ENUM('minute','hour','day','week','month','quarter','year') DEFAULT 'day',
    owner_id BIGINT,
    is_active TINYINT DEFAULT 1,
    tags JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_type (metric_type),
    INDEX idx_owner (owner_id),
    UNIQUE KEY uk_name (workspace_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标定义';

-- 指标维度
CREATE TABLE IF NOT EXISTS adh_metric_dimensions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    metric_id BIGINT NOT NULL,
    dimension_name VARCHAR(200) NOT NULL,
    dimension_column VARCHAR(200),
    dimension_table VARCHAR(200),
    dimension_type ENUM('categorical','temporal','geographical','numerical') DEFAULT 'categorical',
    sort_order INT DEFAULT 0,
    INDEX idx_metric (metric_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标维度';

-- ============================================================
-- 6. 数据目录 (DataCatalog) — 标签管理
-- ============================================================

-- 标签分类
CREATE TABLE IF NOT EXISTS adh_tag_categories (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    parent_id BIGINT DEFAULT NULL,
    sort_order INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标签分类';

-- 标签定义
CREATE TABLE IF NOT EXISTS adh_tags (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    category_id BIGINT,
    name VARCHAR(200) NOT NULL,
    tag_type ENUM('manual','rule','computed','ml') DEFAULT 'manual',
    entity_type ENUM('user','table','column','metric','custom') DEFAULT 'user',
    rule_config JSON COMMENT '规则标签的配置（SQL条件等）',
    data_type ENUM('string','number','boolean','date','enum') DEFAULT 'string',
    enum_values JSON COMMENT '枚举值列表',
    description TEXT,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_category (category_id),
    INDEX idx_entity_type (entity_type),
    UNIQUE KEY uk_name (workspace_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标签定义';

-- 标签值
CREATE TABLE IF NOT EXISTS adh_tag_values (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    tag_id BIGINT NOT NULL,
    entity_id VARCHAR(500) NOT NULL COMMENT '实体ID（用户ID、表名等）',
    value VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT 1.00,
    source ENUM('manual','rule','ml','import') DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_tag (tag_id),
    INDEX idx_entity (entity_id),
    INDEX idx_workspace (workspace_id),
    UNIQUE KEY uk_tag_entity (workspace_id, tag_id, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标签值';

-- ============================================================
-- 7. 数据集成 (DataFlow) — 数据同步
-- ============================================================

-- 同步任务
CREATE TABLE IF NOT EXISTS adh_sync_tasks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    source_type VARCHAR(50) NOT NULL COMMENT 'mysql, postgres, api, file',
    source_config JSON NOT NULL COMMENT '源连接配置',
    target_type VARCHAR(50) NOT NULL COMMENT 'doris, mysql, es',
    target_config JSON NOT NULL COMMENT '目标连接配置',
    sync_mode ENUM('full','incremental','cdc') DEFAULT 'incremental',
    column_mapping JSON COMMENT '字段映射',
    schedule_cron VARCHAR(50) COMMENT '调度cron表达式',
    airflow_dag_id VARCHAR(200) COMMENT '关联的Airflow DAG ID',
    is_active TINYINT DEFAULT 1,
    owner_id BIGINT,
    last_run_at DATETIME,
    last_status VARCHAR(20),
    run_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_owner (owner_id),
    INDEX idx_airflow_dag (airflow_dag_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同步任务';

-- 同步日志
CREATE TABLE IF NOT EXISTS adh_sync_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sync_task_id BIGINT NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    status ENUM('running','success','failed','cancelled') NOT NULL,
    trigger_type ENUM('schedule','manual','retry') DEFAULT 'schedule',
    airflow_run_id VARCHAR(200),
    rows_read BIGINT DEFAULT 0,
    rows_written BIGINT DEFAULT 0,
    rows_failed BIGINT DEFAULT 0,
    error_message TEXT,
    elapsed_ms INT DEFAULT 0,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    INDEX idx_task (sync_task_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同步日志';

-- ============================================================
-- 8. 权限管理 (AuthService) — RBAC
-- ============================================================

-- 角色定义
CREATE TABLE IF NOT EXISTS adh_roles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    is_system TINYINT DEFAULT 0 COMMENT '是否系统内置角色',
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    UNIQUE KEY uk_name (workspace_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色定义';

-- 权限定义
CREATE TABLE IF NOT EXISTS adh_permissions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    resource VARCHAR(100) NOT NULL COMMENT '资源类型（dashboard, metric, tag, quality_rule, sync_task等）',
    action VARCHAR(50) NOT NULL COMMENT '操作（create, read, update, delete, execute）',
    description VARCHAR(200),
    UNIQUE KEY uk_resource_action (resource, action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限定义';

-- 角色-权限关联
CREATE TABLE IF NOT EXISTS adh_role_permissions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    role_id BIGINT NOT NULL,
    permission_id BIGINT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_role_permission (role_id, permission_id),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限关联';

-- ============================================================
-- 9. 初始化数据
-- ============================================================

-- 插入默认权限
INSERT IGNORE INTO adh_permissions (resource, action, description) VALUES
('dashboard', 'create', '创建看板'),
('dashboard', 'read', '查看看板'),
('dashboard', 'update', '编辑看板'),
('dashboard', 'delete', '删除看板'),
('metric', 'create', '创建指标'),
('metric', 'read', '查看指标'),
('metric', 'update', '编辑指标'),
('metric', 'delete', '删除指标'),
('tag', 'create', '创建标签'),
('tag', 'read', '查看标签'),
('tag', 'update', '编辑标签'),
('tag', 'delete', '删除标签'),
('quality_rule', 'create', '创建质量规则'),
('quality_rule', 'read', '查看质量规则'),
('quality_rule', 'update', '编辑质量规则'),
('quality_rule', 'delete', '删除质量规则'),
('quality_rule', 'execute', '执行质量检查'),
('lineage', 'read', '查看数据血缘'),
('lineage', 'update', '编辑血缘关系'),
('sync_task', 'create', '创建同步任务'),
('sync_task', 'read', '查看同步任务'),
('sync_task', 'update', '编辑同步任务'),
('sync_task', 'delete', '删除同步任务'),
('sync_task', 'execute', '执行同步任务'),
('data_standard', 'create', '创建数据标准'),
('data_standard', 'read', '查看数据标准'),
('data_standard', 'update', '编辑数据标准'),
('data_standard', 'delete', '删除数据标准'),
('sensitive_data', 'read', '查看敏感数据'),
('sensitive_data', 'update', '编辑敏感数据'),
('user', 'create', '创建用户'),
('user', 'read', '查看用户'),
('user', 'update', '编辑用户'),
('user', 'delete', '删除用户'),
('workspace', 'create', '创建工作空间'),
('workspace', 'read', '查看工作空间'),
('workspace', 'update', '编辑工作空间'),
('workspace', 'delete', '删除工作空间'),
('audit_log', 'read', '查看审计日志')
ON DUPLICATE KEY UPDATE description=VALUES(description);

-- 插入默认角色
INSERT IGNORE INTO adh_roles (workspace_id, name, display_name, description, is_system) VALUES
(0, 'super_admin', '超级管理员', '拥有所有权限', 1),
(0, 'admin', '管理员', '管理权限', 1),
(0, 'analyst', '数据分析师', '分析和查看权限', 1),
(0, 'viewer', '查看者', '只读权限', 1);

-- 为超级管理员分配所有权限
INSERT IGNORE INTO adh_role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM adh_roles r, adh_permissions p
WHERE r.name = 'super_admin' AND r.workspace_id = 0;
