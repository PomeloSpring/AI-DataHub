-- ═══════════════════════════════════════════════════════════════
-- 离线数据同步 DAG 模块 — 建表 SQL
-- 执行: mysql -u root -p < sync_dag_migration.sql
--
-- 覆盖:
--   1. adh_sync_tasks / adh_sync_logs —— 同步任务（单节点快捷形态，含此前缺失的 DDL）
--   2. adh_dag_workflows / adh_dag_runs / adh_dag_node_runs —— DAG 定义与运行实例
--   3. adh_sync_watermarks —— 增量同步水位线（比较推进，幂等）
--   4. adh_udfs —— SQL 表达式 UDF 注册（多版本行，is_current 标记生效版本）
-- ID 一律应用端生成（_generate_id），表内不使用 AUTO_INCREMENT（与全库约定一致）。
-- 执行: mysql -u root -p < sync_dag_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh2;

-- ───────────────────────────────────────────────────────────────
-- 同步任务定义（单节点快捷形态；DAG sync 节点与之共用执行器）
-- 源/目标以 datasource_name 引用 adh_datasources（name 全局唯一），
-- 连接凭据永不落本表（waker-datasource-domain §2）
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_sync_tasks (
    id BIGINT PRIMARY KEY,
    name VARCHAR(200) NOT NULL COMMENT '任务名称',
    description TEXT COMMENT '任务描述',
    source_type VARCHAR(32) NOT NULL COMMENT '来源类型: mysql / postgres / doris（随数据源 db_type）',
    source_config JSON COMMENT '兼容旧结构的补充配置（筛选/批大小等，不含连接凭据）',
    target_type VARCHAR(32) NOT NULL COMMENT '目标类型: mysql / doris / postgres',
    target_config JSON COMMENT '兼容旧结构的补充配置（写入模式等，不含连接凭据）',
    source_datasource_name VARCHAR(128) COMMENT '源数据源名称（adh_datasources.name）',
    source_table VARCHAR(256) COMMENT '源表名',
    target_datasource_name VARCHAR(128) COMMENT '目标数据源名称（adh_datasources.name）',
    target_table VARCHAR(256) COMMENT '目标表名',
    sync_mode VARCHAR(20) NOT NULL DEFAULT 'full' COMMENT '同步模式: full / incremental',
    incremental_column VARCHAR(128) COMMENT '增量列（sync_mode=incremental 时必填）',
    schedule_cron VARCHAR(50) COMMENT 'Cron 表达式（NULL=仅手动触发）',
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai' COMMENT '调度时区',
    dag_id VARCHAR(200) COMMENT '关联 DAG 标识（预留）',
    task_config JSON COMMENT '任务配置（批大小、超时等）',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用',
    status VARCHAR(20) DEFAULT 'idle' COMMENT '任务状态: idle / running / error',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    owner_id BIGINT DEFAULT 0 COMMENT '创建人（0=未认领，未认领不参与调度）',
    last_run_at DATETIME COMMENT '上次运行时间',
    last_status VARCHAR(20) COMMENT '上次状态: success / failed / partial / timeout / cancelled',
    run_count INT DEFAULT 0 COMMENT '累计运行次数',
    timeout_seconds INT DEFAULT 1800 COMMENT '单次执行超时（秒）',
    max_retries INT DEFAULT 0 COMMENT '失败重试次数（0=不重试）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sync_task_name (workspace_id, name),
    INDEX idx_sync_owner (owner_id),
    INDEX idx_sync_active (is_active),
    INDEX idx_sync_cron (is_active, schedule_cron)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据同步任务定义';

-- 同步执行日志（执行实例；任务表为唯一队列真值，认领用条件更新 + 租约）
CREATE TABLE IF NOT EXISTS adh_sync_logs (
    id BIGINT PRIMARY KEY,
    sync_task_id BIGINT NOT NULL COMMENT '关联同步任务 ID（adh_sync_tasks.id）',
    workspace_id BIGINT DEFAULT 0 COMMENT '冗余 workspace_id',
    dag_run_id VARCHAR(100) COMMENT '所属 DAG 运行键（单任务形态为空）',
    node_run_id BIGINT DEFAULT 0 COMMENT '所属 DAG 节点实例 ID（单任务形态为 0）',
    status VARCHAR(20) NOT NULL COMMENT '状态: queued / running / success / failed / partial / timeout / cancelled',
    trigger_type VARCHAR(20) NOT NULL COMMENT '触发方式: cron / manual / retry',
    rows_read BIGINT DEFAULT 0 COMMENT '读取行数',
    rows_written BIGINT DEFAULT 0 COMMENT '写入行数',
    rows_failed BIGINT DEFAULT 0 COMMENT '失败行数',
    records_synced BIGINT DEFAULT 0 COMMENT '累计同步行数（兼容列）',
    elapsed_ms INT COMMENT '执行耗时（毫秒）',
    error_code VARCHAR(50) COMMENT '错误码（对外可诊断，不含原始报错）',
    error_message TEXT COMMENT '错误信息（已脱敏）',
    worker_id VARCHAR(64) COMMENT '执行 Worker 标识',
    lease_expires_at DATETIME COMMENT '租约到期时间（超时清理依据）',
    started_at DATETIME NOT NULL COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sync_log_task (sync_task_id),
    INDEX idx_sync_log_status (status),
    INDEX idx_sync_log_started (started_at),
    INDEX idx_sync_log_lease (status, lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据同步执行日志';

-- ───────────────────────────────────────────────────────────────
-- DAG 工作流（graph_json 为唯一可编辑事实源）
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_dag_workflows (
    id BIGINT PRIMARY KEY,
    name VARCHAR(200) NOT NULL COMMENT '工作流名称',
    description TEXT COMMENT '工作流描述',
    graph_json JSON NOT NULL COMMENT 'DAG 定义 {nodes:[{key,type,config,name}],edges:[{from,to}]}',
    version INT DEFAULT 1 COMMENT '乐观锁版本（保存时校验）',
    cron_expression VARCHAR(50) COMMENT 'Cron 表达式（NULL=仅手动触发）',
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai' COMMENT '调度时区',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用调度',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    owner_id BIGINT DEFAULT 0 COMMENT '创建人（0=未认领，未认领不参与调度）',
    timeout_seconds INT DEFAULT 3600 COMMENT '单次运行超时（秒）',
    max_retries INT DEFAULT 0 COMMENT '节点失败重试次数',
    last_run_at DATETIME COMMENT '上次运行时间',
    last_status VARCHAR(20) COMMENT '上次运行状态',
    run_count INT DEFAULT 0 COMMENT '累计运行次数',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dag_name (workspace_id, name),
    INDEX idx_dag_owner (owner_id),
    INDEX idx_dag_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DAG 工作流定义';

-- DAG 运行实例（run_key 幂等：cron 触发按墙钟分钟生成，重投不产生重复运行）
CREATE TABLE IF NOT EXISTS adh_dag_runs (
    id BIGINT PRIMARY KEY,
    workflow_id BIGINT NOT NULL COMMENT '关联工作流 ID（adh_dag_workflows.id）',
    run_key VARCHAR(150) NOT NULL COMMENT '运行幂等键',
    trigger_type VARCHAR(20) NOT NULL COMMENT '触发方式: cron / manual / retry',
    status VARCHAR(20) NOT NULL COMMENT '状态: queued / running / success / failed / partial / timeout / cancelled',
    workspace_id BIGINT DEFAULT 0 COMMENT '冗余 workspace_id',
    worker_id VARCHAR(64) COMMENT '执行 Worker 标识',
    stats_json JSON COMMENT '运行统计（节点计数、行数汇总）',
    error_code VARCHAR(50) COMMENT '错误码',
    error_message TEXT COMMENT '错误信息（已脱敏）',
    lease_expires_at DATETIME COMMENT '租约到期时间',
    started_at DATETIME COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dag_run_key (run_key),
    INDEX idx_dag_run_wf (workflow_id),
    INDEX idx_dag_run_status (status),
    INDEX idx_dag_run_lease (status, lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DAG 运行实例';

-- DAG 节点运行实例（认领 = 条件更新 queued→running 并写租约，两实例并发只执行一次）
CREATE TABLE IF NOT EXISTS adh_dag_node_runs (
    id BIGINT PRIMARY KEY,
    run_id BIGINT NOT NULL COMMENT '所属运行实例 ID（adh_dag_runs.id）',
    workflow_id BIGINT DEFAULT 0 COMMENT '冗余 workflow_id',
    node_key VARCHAR(100) NOT NULL COMMENT '节点 key（graph_json.nodes[].key）',
    node_type VARCHAR(32) NOT NULL COMMENT '节点类型: sync / sql_task / control',
    node_config JSON COMMENT '运行时配置快照（含 UDF 版本锁定）',
    status VARCHAR(20) NOT NULL COMMENT '状态: queued / running / success / failed / skipped / timeout / cancelled',
    attempt INT DEFAULT 1 COMMENT '执行尝试次数（重试递增）',
    rows_read BIGINT DEFAULT 0 COMMENT '读取行数',
    rows_written BIGINT DEFAULT 0 COMMENT '写入行数',
    elapsed_ms INT COMMENT '执行耗时（毫秒）',
    error_code VARCHAR(50) COMMENT '错误码',
    error_message TEXT COMMENT '错误信息（已脱敏）',
    worker_id VARCHAR(64) COMMENT '执行 Worker 标识',
    lease_expires_at DATETIME COMMENT '租约到期时间',
    started_at DATETIME COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_node_attempt (run_id, node_key, attempt),
    INDEX idx_node_run (run_id),
    INDEX idx_node_status (status),
    INDEX idx_node_lease (status, lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DAG 节点运行实例';

-- ───────────────────────────────────────────────────────────────
-- 增量同步水位线（服务端比较推进，重复投递不产生重复副作用）
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_sync_watermarks (
    id BIGINT PRIMARY KEY,
    node_key VARCHAR(160) NOT NULL COMMENT '水位线维度键（sync_task:{id} 或 dag:{wf}:{node_key}）',
    watermark_value VARCHAR(256) COMMENT '当前水位值（增量列最大值，字符串化存储）',
    last_rows BIGINT DEFAULT 0 COMMENT '上次推进时的行数',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_watermark_node (node_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='增量同步水位线';

-- ───────────────────────────────────────────────────────────────
-- UDF 注册（SQL 表达式 UDF；多版本行 + is_current 标记生效版本）
-- 变更产生新版本行，历史 run 按 node_config 锁定的版本回溯
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_udfs (
    id BIGINT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT 'UDF 名称（全局唯一，SQL 中引用名）',
    udf_kind VARCHAR(20) NOT NULL DEFAULT 'scalar_expr' COMMENT 'UDF 类型: scalar_expr（二期预留 python / agg）',
    expression TEXT NOT NULL COMMENT 'SQL 表达式定义（纯表达式，禁子查询/表引用）',
    params JSON COMMENT '参数签名 [{name, type}]',
    return_type VARCHAR(50) DEFAULT 'auto' COMMENT '返回类型（auto=按表达式推断）',
    description TEXT COMMENT '口径说明',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用（停用后 SQL 引用即校验失败）',
    is_current TINYINT DEFAULT 1 COMMENT '是否生效版本（同一 name 仅一行=1）',
    version INT DEFAULT 1 COMMENT '版本号（同 name 递增）',
    owner_id BIGINT DEFAULT 0 COMMENT '创建人',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    usage_count INT DEFAULT 0 COMMENT '累计被引用执行次数',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_udf_name_version (name, version),
    INDEX idx_udf_current (name, is_current),
    INDEX idx_udf_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UDF 注册表（SQL 表达式函数）';
