# 统一认证与权限管理设计方案

## 目标

1. **OpenLDAP + Kerberos** 统一认证：企业用户通过 LDAP 目录登录，Kerberos 实现 SSO
2. **Apache Ranger** 统一授权：行级、列级权限管控，覆盖所有数据源（Doris/MySQL/ES）
3. 为后续 DataFusion 统一查询引擎的权限集成打好基础

---

## 现状分析

### 当前认证
- JWT + bcrypt，纯本地认证（`services/shared/common/auth.py`）
- 用户名/密码存储在 `adh_users` 表
- 无外部认证集成

### 当前授权
- 微服务 `services/authservice/` 有完整 RBAC：
  - `adh_roles` — 角色表（workspace_id, name, is_system）
  - `adh_permissions` — 权限表（resource, action）
  - `adh_role_permissions` — 角色-权限关联
- `rbac_service.py` 实现了角色 CRUD、权限 CRUD、用户权限检查
- 前端 Roles 页面已有权限矩阵 UI
- **但**：只有 resource:action 级别（如 `dashboard:create`），没有表/列/行级权限

### 关键差距
| 能力 | 现状 | 目标 |
|------|------|------|
| 认证来源 | 本地 bcrypt | LDAP + Kerberos SSO |
| 应用级权限 | resource:action | resource:action（已有） |
| 数据级权限 | 无 | 表/列/行级（Ranger） |
| 审计 | 应用层审计 | Ranger Audit 集中审计 |

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      认证层 (Authentication)                  │
│                                                               │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐               │
│  │ OpenLDAP │    │ Kerberos │    │  Local   │               │
│  │ (主认证)  │    │  (SSO)   │    │ (Fallback)│              │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘               │
│       │               │               │                      │
│       └───────────────┼───────────────┘                      │
│                       ▼                                       │
│              AuthService (port 8006)                          │
│              ├── ldap_backend.py                              │
│              ├── kerberos_backend.py                          │
│              └── auth_service.py (改造)                       │
└───────────────────────┬─────────────────────────────────────┘
                        │ JWT Token
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                      授权层 (Authorization)                   │
│                                                               │
│  ┌──────────────────────┐    ┌──────────────────────────┐   │
│  │  应用级 RBAC (已有)    │    │  数据级 Ranger (新增)     │   │
│  │  ├── roles            │    │  ├── Ranger Admin        │   │
│  │  ├── permissions      │    │  ├── Policy Engine       │   │
│  │  └── role_permissions │    │  ├── UserSync (LDAP)     │   │
│  │                       │    │  └── Audit               │   │
│  │  资源:操作 级别         │    │  表/列/行 级别            │   │
│  └──────────┬────────────┘    └────────────┬─────────────┘   │
│             │                              │                  │
│             └──────────┬───────────────────┘                  │
│                        ▼                                      │
│              权限中间件 (Permission Middleware)                │
│              ├── require_permission() — 应用级                │
│              ├── ranger_check() — 数据级预检查                │
│              └── SQL 行过滤注入                               │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                      数据源层                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  Doris   │  │  MySQL   │  │    ES    │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
│  (可选: Ranger Plugin 做最终拦截)                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 1: OpenLDAP 认证集成

### 1.1 新增 LDAP 后端

**新建文件**: `services/authservice/services/ldap_backend.py`

```python
"""LDAP authentication backend using ldap3."""
import ldap3
from ldap3 import Server, Connection, ALL, NTLM, SIMPLE

class LDAPBackend:
    def __init__(self, server_url, base_dn, bind_dn, bind_password):
        self.server = Server(server_url, get_info=ALL)
        self.base_dn = base_dn
        self.bind_dn = bind_dn
        self.bind_password = bind_password

    def authenticate(self, username: str, password: str) -> dict | None:
        """LDAP bind 认证，返回用户属性或 None"""
        # 1. 用 service account 搜索用户 DN
        # 2. 用用户 DN + password 做 bind 验证
        # 3. 返回用户属性（uid, cn, mail, memberOf）

    def sync_user_to_local(self, ldap_user: dict) -> dict:
        """首次 LDAP 登录时同步用户到 adh_users (lazy provisioning)"""
        # 查找或创建 adh_users 记录
        # 设置 auth_source = 'ldap'
        # 同步 LDAP 组 → 本地角色映射

    def search_users(self, keyword: str) -> list[dict]:
        """搜索 LDAP 用户（用于用户管理页面）"""

    def search_groups(self) -> list[dict]:
        """搜索 LDAP 组"""
```

### 1.2 修改认证流程

**修改文件**: `services/authservice/services/auth_service.py`

```python
def login(username: str, password: str, ip_address: str = "") -> dict:
    # 1. 先尝试 LDAP 认证
    if ldap_enabled:
        ldap_user = ldap_backend.authenticate(username, password)
        if ldap_user:
            local_user = ldap_backend.sync_user_to_local(ldap_user)
            return generate_tokens(local_user)

    # 2. 回退到本地认证
    return local_authenticate(username, password, ip_address)
```

### 1.3 数据库变更

**新增字段** (migration SQL):

```sql
-- adh_users 增加 LDAP 相关字段
ALTER TABLE adh_users ADD COLUMN auth_source VARCHAR(32) DEFAULT 'local'
    COMMENT '认证来源: local/ldap/kerberos';
ALTER TABLE adh_users ADD COLUMN ldap_dn VARCHAR(512) DEFAULT ''
    COMMENT 'LDAP DN';
ALTER TABLE adh_users ADD COLUMN ldap_sync_time DATETIME NULL
    COMMENT '上次 LDAP 同步时间';

-- LDAP 组 → 本地角色映射表
CREATE TABLE adh_ldap_role_mapping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ldap_group_dn VARCHAR(512) NOT NULL,
    local_role VARCHAR(100) NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_group_role (ldap_group_dn, workspace_id)
) ENGINE=InnoDB COMMENT='LDAP组-角色映射';
```

### 1.4 配置项

```env
# .env 新增
LDAP_ENABLED=false
LDAP_SERVER_URL=ldap://ldap.example.com:389
LDAP_BASE_DN=dc=example,dc=com
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=secret
LDAP_USER_SEARCH_BASE=ou=users,dc=example,dc=com
LDAP_USER_SEARCH_FILTER=(uid={username})
LDAP_GROUP_SEARCH_BASE=ou=groups,dc=example,dc=com
LDAP_STARTTLS=true
```

---

## Phase 2: Apache Ranger 授权集成

### 2.1 Ranger 部署架构

```yaml
# docker-compose.ranger.yml
services:
  ranger-admin:
    image: apache/ranger:2.4.0
    ports:
      - "6080:6080"
    environment:
      RANGER_DB_TYPE: mysql
      RANGER_DB_HOST: mysql
      RANGER_DB_NAME: ranger
    depends_on:
      - mysql
      - zookeeper

  ranger-usersync:
    image: apache/ranger:2.4.0
    command: usersync
    environment:
      RANGER_USERSYNC_SOURCE: ldap
      RANGER_USERSYNC_LDAP_URL: ldap://openldap:389
      RANGER_USERSYNC_LDAP_BASE_DN: dc=example,dc=com

  zookeeper:
    image: zookeeper:3.8
    ports:
      - "2181:2181"
```

### 2.2 Ranger 策略模型

Ranger 策略通过 Ranger Admin Web UI (port 6080) 管理，结构如下：

```
Service Type: ai-datahub (自定义)
├── Database Policy (库级)
│   ├── resource: database=mydb
│   ├── users: [analyst_group]
│   └── permissions: [select]
├── Table Policy (表级)
│   ├── resource: database=mydb, table=orders
│   ├── users: [analyst_group]
│   └── permissions: [select]
├── Column Policy (列级)
│   ├── resource: database=mydb, table=orders, column={phone, email, id_card}
│   ├── users: [analyst_group]
│   ├── permissions: [select]
│   └── conditions: 脱敏(masking) 或 拒绝(deny)
└── Row Filter Policy (行级)
    ├── resource: database=mydb, table=orders
    ├── users: [analyst_group]
    ├── permissions: [select]
    └── row_filter: "region = '${USER.region}'"
```

### 2.3 Ranger REST API 客户端

**新建文件**: `services/shared/services/ranger_client.py`

```python
"""Apache Ranger REST API client for policy checking."""
import httpx
from functools import lru_cache
from cachetools import TTLCache

class RangerClient:
    def __init__(self, admin_url: str, auth: tuple):
        self.admin_url = admin_url.rstrip("/")
        self.auth = auth
        self._policy_cache = TTLCache(maxsize=1000, ttl=300)  # 5min cache

    async def check_access(
        self,
        user: str,
        groups: list[str],
        resource_type: str,  # "database", "table", "column"
        resource: dict,      # {"database": "mydb", "table": "orders", "column": "phone"}
        action: str = "select",
    ) -> RangerAccessResult:
        """检查用户对资源的访问权限"""
        # 1. 查缓存
        # 2. 调 Ranger Admin REST API: GET /service/{service}/policy/resource
        # 3. 返回: allowed, row_filter, masking_type

    async def get_allowed_columns(
        self,
        user: str,
        groups: list[str],
        database: str,
        table: str,
    ) -> list[str]:
        """获取用户可访问的列列表"""

    async def get_row_filter(
        self,
        user: str,
        groups: list[str],
        database: str,
        table: str,
    ) -> str | None:
        """获取行级过滤条件，如 "region = '华东'" """

    async def get_column_masking(
        self,
        user: str,
        groups: list[str],
        database: str,
        table: str,
        column: str,
    ) -> MaskingRule | None:
        """获取列脱敏规则，如 phone → 138****1234"""

    def _build_cache_key(self, user, resource_type, resource, action) -> str:
        return f"{user}:{resource_type}:{resource}:{action}"


class RangerAccessResult:
    allowed: bool
    row_filter: str | None        # 行级过滤 SQL 片段
    column_masking: dict | None   # {column: masking_type}
    reason: str                   # 拒绝原因


class MaskingRule:
    masking_type: str  # "MASK_NULL", "MASK_HASH", "MASK_PARTIAL", "CUSTOM"
    masking_value: str # 自定义脱敏表达式
```

### 2.4 权限中间件改造

**修改文件**: `services/shared/common/auth.py`

```python
# 新增 Ranger 感知的权限检查
async def require_datasource_access(
    datasource_id: int,
    table: str = "",
    columns: list[str] = [],
    action: str = "select",
):
    """FastAPI dependency: 检查用户对数据源/表/列的访问权限"""
    async def _check(user: dict = Depends(get_current_user)):
        # 1. 应用级 RBAC 检查（已有）
        # 2. Ranger 数据级检查（新增）
        if ranger_enabled:
            groups = get_user_groups(user["user_id"])
            result = await ranger_client.check_access(
                user=user["username"],
                groups=groups,
                resource_type="table",
                resource={"database": db_name, "table": table},
                action=action,
            )
            if not result.allowed:
                raise HTTPException(403, f"无权访问 {db_name}.{table}")
        return user
    return _check
```

### 2.5 SQL 执行层改造

**修改文件**: `services/datamind/nl2sql/sql/query_executor.py`

在 SQL 执行前注入 Ranger 权限逻辑：

```python
def execute_query_with_ranger(
    sql: str,
    datasource_id: int,
    user_context: dict,  # {username, groups}
) -> tuple[DataFrame, int, int]:
    """带 Ranger 权限检查的查询执行"""
    # 1. 解析 SQL 中涉及的表和列
    tables_columns = parse_sql_tables_columns(sql)

    # 2. Ranger 预检查
    for table, columns in tables_columns.items():
        # 检查表级权限
        access = ranger_client.check_access(user_context, "table", table)
        if not access.allowed:
            raise PermissionError(f"无权访问表 {table}")

        # 获取允许的列
        allowed_cols = ranger_client.get_allowed_columns(user_context, table)
        denied_cols = set(columns) - set(allowed_cols)
        if denied_cols:
            raise PermissionError(f"无权访问列: {denied_cols}")

        # 注入行级过滤
        row_filter = ranger_client.get_row_filter(user_context, table)
        if row_filter:
            sql = inject_row_filter(sql, table, row_filter)

    # 3. 获取列脱敏规则
    masking_rules = ranger_client.get_column_masking_rules(user_context, tables_columns)

    # 4. 执行 SQL
    df, elapsed, count = execute_query(sql, datasource_id)

    # 5. 应用列脱敏
    if masking_rules:
        df = apply_column_masking(df, masking_rules)

    return df, elapsed, count
```

### 2.6 NL2SQL 阶段的权限感知

**修改文件**: `services/datamind/rag/rag_retriever.py`

在 RAG 元数据检索阶段就过滤掉无权限的表/列：

```python
def retrieve_with_ranger(
    question: str,
    datasource_id: int,
    user_context: dict,
    strategy: str = "hybrid",
) -> dict:
    """带 Ranger 权限过滤的元数据检索"""
    # 1. 正常检索元数据
    metadata = retrieve_with_strategy(question, datasource_id, strategy)

    # 2. Ranger 过滤：移除无权限的表
    allowed_tables = ranger_client.get_allowed_tables(user_context, db_name)
    metadata["table_info"] = [
        t for t in metadata["table_info"]
        if t["table_name"] in allowed_tables
    ]

    # 3. Ranger 过滤：移除无权限的列
    for table_meta in metadata["table_info"]:
        allowed_cols = ranger_client.get_allowed_columns(
            user_context, db_name, table_meta["table_name"]
        )
        table_meta["columns"] = [
            c for c in table_meta.get("columns", [])
            if c["column_name"] in allowed_cols
        ]

    return metadata
```

### 2.7 用户组同步

Ranger UserSync 从 OpenLDAP 同步用户/组到 Ranger。AI-DataHub 侧需要：

**新建文件**: `services/authservice/services/ranger_sync.py`

```python
"""Sync user-group info from LDAP to Ranger-compatible format."""

def get_user_groups(user_id: int) -> list[str]:
    """获取用户的 LDAP 组列表（用于 Ranger 策略匹配）"""
    # 1. 从 adh_users.ldap_dn 获取用户 DN
    # 2. 从 LDAP 查询 memberOf 属性
    # 3. 返回组 DN 列表

def sync_ranger_user(user_id: int):
    """同步单个用户信息到 Ranger (触发 UserSync)"""
```

---

## Phase 3: Kerberos SSO（可选）

### 3.1 新增 Kerberos 后端

**新建文件**: `services/authservice/services/kerberos_backend.py`

```python
"""Kerberos SPNEGO authentication backend."""
import gssapi

class KerberosBackend:
    def __init__(self, keytab_path: str, service_principal: str):
        self.keytab_path = keytab_path
        self.service_principal = service_principal

    def validate_spnego_token(self, token: bytes) -> dict | None:
        """验证 SPNEGO token，返回用户 principal"""
        # 1. 使用 gssapi 验证 token
        # 2. 提取 client principal
        # 3. 映射到本地用户

    def principal_to_username(self, principal: str) -> str:
        """user@REALM → user"""
```

### 3.2 登录流程扩展

```python
# auth_service.py
def login_with_kerberos(spnego_token: bytes) -> dict:
    """Kerberos SSO 登录"""
    principal = kerberos_backend.validate_spnego_token(spnego_token)
    if not principal:
        raise AuthError("Invalid Kerberos token")
    username = kerberos_backend.principal_to_username(principal)
    # 同步到本地，生成 JWT
    ...
```

---

## 数据库 Migration 文件

**新建文件**: `docker/mysql/permission_management_migration.sql`

```sql
-- ============================================================
-- 统一认证与权限管理 Migration
-- ============================================================

-- 1. adh_users 增加认证源字段
ALTER TABLE adh_users
    ADD COLUMN IF NOT EXISTS auth_source VARCHAR(32) DEFAULT 'local'
        COMMENT '认证来源: local/ldap/kerberos',
    ADD COLUMN IF NOT EXISTS ldap_dn VARCHAR(512) DEFAULT ''
        COMMENT 'LDAP DN',
    ADD COLUMN IF NOT EXISTS ldap_sync_time DATETIME NULL
        COMMENT '上次LDAP同步时间';

-- 2. LDAP 组-角色映射
CREATE TABLE IF NOT EXISTS adh_ldap_role_mapping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ldap_group_dn VARCHAR(512) NOT NULL,
    local_role VARCHAR(100) NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_group_role (ldap_group_dn, workspace_id)
) ENGINE=InnoDB COMMENT='LDAP组-本地角色映射';

-- 3. Ranger 策略缓存表（可选，加速策略查询）
CREATE TABLE IF NOT EXISTS adh_ranger_policy_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    cache_key VARCHAR(512) NOT NULL,
    policy_data JSON,
    cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    UNIQUE KEY uk_cache_key (cache_key),
    INDEX idx_expires (expires_at)
) ENGINE=InnoDB COMMENT='Ranger策略缓存';

-- 4. 数据级权限审计表
CREATE TABLE IF NOT EXISTS adh_data_access_audit (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username VARCHAR(64),
    datasource_id BIGINT,
    database_name VARCHAR(100),
    table_name VARCHAR(200),
    columns JSON,
    row_filter TEXT,
    action VARCHAR(50),
    allowed TINYINT,
    deny_reason VARCHAR(500),
    query_text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_time (created_at),
    INDEX idx_table (database_name, table_name)
) ENGINE=InnoDB COMMENT='数据访问审计';
```

---

## 文件清单

### 新建文件
| 文件 | 说明 |
|------|------|
| `services/authservice/services/ldap_backend.py` | LDAP 认证后端 |
| `services/authservice/services/kerberos_backend.py` | Kerberos SSO 后端（可选） |
| `services/authservice/services/ranger_sync.py` | Ranger 用户组同步 |
| `services/shared/services/ranger_client.py` | Ranger REST API 客户端 |
| `docker/mysql/permission_management_migration.sql` | 数据库 Migration |

### 修改文件
| 文件 | 改动 |
|------|------|
| `services/authservice/services/auth_service.py` | 增加 LDAP/Kerberos 认证分支 |
| `services/shared/common/auth.py` | 新增 `require_datasource_access()` 中间件 |
| `services/shared/common/config.py` | 新增 LDAP/Ranger 配置项 |
| `services/datamind/nl2sql/sql/query_executor.py` | Ranger 权限预检查 + 行过滤注入 + 列脱敏 |
| `services/datamind/rag/rag_retriever.py` | RAG 元数据按 Ranger 策略过滤 |
| `docker-compose.yml` | 新增 Ranger Admin / UserSync / ZooKeeper 服务 |

---

## 实施阶段

```
Phase 1: OpenLDAP 认证 (1-2 周)
  ├── ldap_backend.py
  ├── auth_service.py 改造
  ├── adh_users 增加 auth_source 字段
  ├── adh_ldap_role_mapping 表
  └── 登录页面 LDAP 选项

Phase 2: Ranger 授权 (3-4 周)
  ├── Docker 部署 Ranger Admin + UserSync + ZooKeeper
  ├── ranger_client.py (REST API 客户端)
  ├── query_executor.py 改造（权限预检查 + 行过滤 + 列脱敏）
  ├── rag_retriever.py 改造（元数据过滤）
  ├── 数据访问审计表
  └── Ranger Admin 策略配置

Phase 3: Kerberos SSO (可选, 1-2 周)
  ├── kerberos_backend.py
  ├── SPNEGO token 验证
  └── 浏览器 SSO 配置
```

---

## 与 DataFusion 的衔接

当后续引入 DataFusion 时，Ranger 集成可以直接复用：

```
DataFusion CatalogProvider
    ├── 调用 ranger_client.check_access() — 同样的 API
    ├── 调用 ranger_client.get_row_filter() — 注入行过滤
    └── 调用 ranger_client.get_column_masking() — 应用列脱敏

ranger_client.py 是共享的，DataFusion 层和应用层复用同一个客户端。
```

这样 Ranger 策略只需要在 Ranger Admin 配置一次，AI-DataHub 的应用层和 DataFusion 层都能执行。
