# 数据引擎层集成方案：engine-server-rust

## 概述

将 `engine-server-rust` 作为 AI-DataHub 的数据引擎层，替代当前直接 SQL 执行的方式。通过 MDL 语义模型实现：
- SQL 语义分析 + 自动转换
- RLS 行级安全自动注入
- 视图展开 + 关系链自动 JOIN
- SQL 方言转译（MySQL/Doris 等）

## 当前问题

1. **RLS 未集成**：`rls_filter.py` 存在但从未被调用
2. **Gateway 未集成**：`gateway_client.py` 存在但查询直接走数据源
3. **两套数据源查找路径**：`query_executor._get_ds_conn_params()` 和 `datasource_db.get_datasource_by_id()` 并行
4. **无语义层**：NL2SQL 生成的 SQL 直接执行，无 MDL 转换

## 集成架构

```
用户问题 → NL2SQL (LLM) → 生成逻辑 SQL
                              ↓
                    engine-server-rust (MDL 转换)
                    ├── RLS 注入
                    ├── 视图展开
                    ├── 关系链 JOIN
                    └── 方言转译
                              ↓
                    物理 SQL → 数据源执行 → 结果
```

## 实施步骤

### Step 1: 部署 engine-server-rust

将 engine-server-rust 作为独立服务部署，复用现有的 `services/gateway/` 目录。

**部署方式**：
- 方案 A：直接编译运行（推荐，已有 Rust 环境）
- 方案 B：Docker 容器部署

**配置**：
- 端口：8082（与现有 gateway 50051 分开）
- 数据源：通过 API 请求中的 `connectionInfo` 动态指定

### Step 2: 创建 Python 客户端

在 `services/shared/common/` 创建 `engine_client.py`，封装 engine-server-rust 的 API：

```python
class EngineClient:
    def query(sql, manifest, connection_info, session_properties) -> QueryResult
    def dry_plan(sql, manifest, data_source=None) -> str  # 只做 SQL 转换
    def validate(data_source, rule_name, parameters, connection_info) -> bool
    def metadata_tables(data_source, connection_info, database=None) -> list
```

### Step 3: 构建 MDL Manifest

从 AI-DataHub 的元数据（adh_table_info, adh_column_metadata, adh_table_relations）构建 MDL Manifest：

```python
def build_manifest(datasource_id: int) -> dict:
    """从数据库元数据构建 MDL Manifest"""
    # 1. 获取表信息 → Model
    # 2. 获取列信息 → Column
    # 3. 获取关联关系 → Relationship
    # 4. 获取 RLS 策略 → RowLevelAccessControl
    # 5. 组装 Manifest
```

### Step 4: 修改查询执行流程

修改 `services/datamind/nl2sql/sql/query_executor.py` 的 `execute_query()` 函数：

**Before**：
```python
def execute_query(sql, datasource_id, ...):
    conn_params = _get_ds_conn_params(datasource_id)
    # 直接执行 SQL
    result = pymysql.execute(sql, conn_params)
    return result
```

**After**：
```python
def execute_query(sql, datasource_id, user_context=None, ...):
    # 1. 构建 MDL Manifest
    manifest = build_manifest(datasource_id)

    # 2. 获取连接信息
    conn_params = _get_ds_conn_params(datasource_id)
    connection_info = {
        "host": conn_params["host"],
        "port": conn_params["port"],
        "user": conn_params["user"],
        "password": conn_params["password"],
        "database": conn_params["database"],
    }

    # 3. 构建 session properties (用于 RLS)
    session_properties = build_session_properties(user_context)

    # 4. 通过 engine-server 执行（RLS + MDL 转换 + 方言转译）
    result = engine_client.query(
        sql=sql,
        manifest=manifest,
        connection_info=connection_info,
        session_properties=session_properties,
    )

    return result
```

### Step 5: Session Properties 与 RLS 集成

创建 `services/datamind/nl2sql/sql/session_properties.py`：

```python
def build_session_properties(user_context: dict) -> dict:
    """从用户上下文构建 session properties"""
    props = {}
    if user_context:
        # 用户 ID
        if user_id := user_context.get("user_id"):
            props["session_user_id"] = str(user_id)
        # 角色
        if role := user_context.get("role"):
            props["session_role"] = role
        # 工作空间
        if ws_id := user_context.get("workspace_id"):
            props["session_workspace_id"] = str(ws_id)
        # 管理员 bypass
        if role == "admin":
            props["rls_policy_ignore"] = "true"
    return props
```

### Step 6: MDL 管理 API

在 `services/datamind/api/` 添加 MDL 管理端点：

- `POST /api/mdl/manifest` — 获取指定数据源的 MDL Manifest
- `POST /api/mdl/dry-plan` — SQL 语义转换（不执行）
- `POST /api/mdl/validate` — 验证列是否存在

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `services/shared/common/engine_client.py` | **新增**：engine-server-rust Python 客户端 |
| `services/datamind/nl2sql/sql/manifest_builder.py` | **新增**：从元数据构建 MDL Manifest |
| `services/datamind/nl2sql/sql/session_properties.py` | **新增**：Session Properties 构建 |
| `services/datamind/nl2sql/sql/query_executor.py` | **修改**：集成 engine client |
| `services/datamind/api/mdl.py` | **新增**：MDL 管理 API |
| `services/docker-compose.yml` | **修改**：添加 engine-server 服务 |
| `services/.env` | **修改**：添加 ENGINE_SERVER_URL |

## 验证检查点

1. engine-server-rust 可独立启动，健康检查通过
2. Python 客户端可调用 engine API
3. MDL Manifest 从元数据正确构建
4. SQL 通过 engine 转换后语法正确
5. RLS 策略正确注入到 SQL
6. 端到端查询流程正常
