-- ============================================================
-- 统一认证与权限管理 Migration
-- 1. OpenLDAP 认证集成
-- 2. Apache Ranger 授权集成
-- ============================================================

-- 1. adh_users 增加认证源和 LDAP 相关字段
ALTER TABLE adh_users
    ADD COLUMN IF NOT EXISTS auth_source VARCHAR(32) DEFAULT 'local'
        COMMENT '认证来源: local/ldap/kerberos',
    ADD COLUMN IF NOT EXISTS ldap_dn VARCHAR(512) DEFAULT ''
        COMMENT 'LDAP DN (Distinguished Name)',
    ADD COLUMN IF NOT EXISTS ldap_sync_time DATETIME NULL
        COMMENT '上次LDAP同步时间';

CREATE INDEX IF NOT EXISTS idx_users_auth_source ON adh_users(auth_source);

-- 2. LDAP 组-本地角色映射表
CREATE TABLE IF NOT EXISTS adh_ldap_role_mapping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ldap_group_dn VARCHAR(512) NOT NULL COMMENT 'LDAP 组 DN',
    local_role VARCHAR(100) NOT NULL COMMENT '映射到的本地角色名',
    workspace_id BIGINT DEFAULT 0 COMMENT '工作空间ID (0=全局)',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_group_role (ldap_group_dn, workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LDAP组-本地角色映射';

-- 3. Ranger 策略缓存表（加速策略查询，避免频繁调用 Ranger Admin）
CREATE TABLE IF NOT EXISTS adh_ranger_policy_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    cache_key VARCHAR(512) NOT NULL COMMENT '缓存键: user:resource:action',
    policy_data JSON COMMENT '策略数据',
    cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME COMMENT '过期时间',
    UNIQUE KEY uk_cache_key (cache_key),
    INDEX idx_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Ranger策略缓存';

-- 4. 数据级访问审计表（Ranger 审计 + 应用层审计）
CREATE TABLE IF NOT EXISTS adh_data_access_audit (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username VARCHAR(64),
    datasource_id BIGINT,
    database_name VARCHAR(100),
    table_name VARCHAR(200),
    columns JSON COMMENT '访问的列列表',
    row_filter TEXT COMMENT '注入的行过滤条件',
    action VARCHAR(50) COMMENT '操作类型: select/insert/update/delete',
    allowed TINYINT COMMENT '是否允许: 1=允许, 0=拒绝',
    deny_reason VARCHAR(500) COMMENT '拒绝原因',
    query_text TEXT COMMENT 'SQL查询文本',
    source_ip VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_time (created_at),
    INDEX idx_table (database_name, table_name),
    INDEX idx_allowed (allowed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据访问审计';

-- 5. 默认 LDAP 角色映射示例（需根据实际 LDAP 组调整）
-- INSERT IGNORE INTO adh_ldap_role_mapping (ldap_group_dn, local_role, workspace_id) VALUES
-- ('cn=admins,ou=groups,dc=example,dc=com', 'admin', 0),
-- ('cn=analysts,ou=groups,dc=example,dc=com', 'analyst', 0),
-- ('cn=developers,ou=groups,dc=example,dc=com', 'analyst', 0),
-- ('cn=users,ou=groups,dc=example,dc=com', 'viewer', 0);
