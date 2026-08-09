# AI-DataHub MCP 能力 SDK 化架构设计

## 1. 设计目标

将系统各项可复用能力(元数据、数据执行、治理、同步、可视化、向量检索等)封装为 **MCP 能力模块**,使任意 AI 工具(Claude Desktop、Cursor、opencode、Qoder、自研 Agent)都能通过 MCP 协议使用平台能力。

**模块形态的核心约定(本设计的前提)**:

- MCP 能力模块**具备脱离本项目独立运行为 MCP 服务的能力**(自包含、可移植)
- 但在本项目中,**模块不独立对外暴露服务**——以 SDK 形式被宿主微服务包裹,挂载在宿主已有的 HTTP 服务内部
- 鉴权、工作空间隔离、审计、限流全部由宿主项目的现有体系承担,MCP 模块自身零鉴权逻辑

由此得出四条原则:

1. **不开新端口、不起新进程**:MCP 端点挂载在宿主服务的现有 FastAPI app 内(如 `/mcp` 路径),复用现有 HTTP 端口
2. **MCP 模块只做能力暴露,不新增业务逻辑** —— 工具实现一律委托宿主服务已有的 service 层
3. **身份必须可传递** —— 任何 MCP 调用都由宿主的鉴权中间件还原出 `user_context`,权限链路(RLS/脱敏/审计)与内置 pipeline 完全一致
4. **数据执行只有一条受控路径** —— SQL 执行统一走 DataEngine 网关,凭据永不出服务端

## 2. 现状盘点

### 2.1 已有的 MCP 相关代码

| 位置 | 形态 | 状态 |
|---|---|---|
| `services/datacatalog/mcp_server.py` | MCP SDK + Starlette SSE,提供 `create_mcp_app()` 工厂 | **最接近目标形态**(独立 ASGI app,可被 mount),但尚未挂载、无鉴权 |
| `services/shared/vectorservice/mcp_server.py` | MCP SDK + SSE,独立端口运行 | 与目标形态相悖(独立对外),需收编为宿主内挂载 |
| `services/datamind/mcp_server.py` | 手写 JSON-RPC,仅 stdio | 需重写为 MCP SDK 模块 |
| `services/dataflow/mcp_server.py` | 手写 JSON-RPC | 需重写为 MCP SDK 模块 |
| `services/shared/mcp_base.py` | SSE 传输基座 `create_mcp_server()` / `create_mcp_starlette_app()` | 保留并升级为 SDK 基座 |
| `services/shared/mcp_client/`(客户端) | 已支持 SSE / Streamable HTTP / stdio 三种传输 | 平台调用外部 MCP 的通道,本设计不涉及改动 |

### 2.2 与目标形态的差距

1. **形态不统一**:独立端口(vectorservice)、stdio(datamind)、未挂载(datacatalog)三种形态并存
2. **无宿主包裹**:现有实现均绕过了平台的鉴权/审计体系,直接暴露或根本不跑
3. **`MCP_PORTS` 独立端口规划应废弃** —— 与本设计的"不单独开口"原则冲突

## 3. 目标架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ 接入方                                                                │
│  ChatBI 前端 │ Embed SDK │ Claude/Cursor/opencode/Qoder │ 自研 Agent  │
└───────┬───────────────────────────┬──────────────────────────────────┘
        │ HTTP (REST/SSE)           │ MCP 协议(SSE/Streamable HTTP)
        └───────────┬───────────────┘
                    ▼ 同一个服务端口(8001~8011)
┌──────────────────────────────────────────────────────────────────────┐
│ 宿主微服务(FastAPI app)                                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 平台统一入口层:JWT/Scoped Token 鉴权 → user_context           │   │
│  │                工作空间隔离 / 审计日志 / 限流                  │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
│         ┌─────────────────────┼──────────────────────┐              │
│         ▼                     ▼                      ▼              │
│  /api/* 业务路由        /mcp MCP 挂载点          内部 service 层      │
│  (前端/内部调用)      (MCP 模块 SDK,零鉴权)  ◄── 工具实现一律委托      │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼ user_context 强制传递
┌──────────────────────────────────────────────────────────────────────┐
│ 数据执行层  DataEngine (Rust DataFusion Gateway, 8082)                │
│  SQL 执行 + RLS 行过滤 + 列隐藏/脱敏 + request_id 审计                │
│  兜底: Python PermissionEnforcer (RBAC + RLS)                        │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 存储层  Doris(分析+向量)│MySQL(元数据)│Elasticsearch│Neo4j│Redis      │
└──────────────────────────────────────────────────────────────────────┘
```

关键变化:外部 AI 工具的 MCP 流量与前端 REST 流量**走同一个服务端口**,统一经过平台入口层;MCP 模块本身是被宿主"包进去"的 SDK。

## 4. MCP 模块的 SDK 设计

### 4.1 模块结构(每个能力域一个模块)

```
services/<host>/mcp_module/            # 与宿主服务同包,随宿主部署
├── __init__.py                        # 导出 create_mcp_app(context) 工厂
├── tools.py                           # @mcp.tool() 定义,实现委托宿主 service 层
└── __main__.py                        # 独立运行入口(可选,供脱离本项目场景)
```

### 4.2 宿主上下文注入(MCPHostContext)

模块不自己做鉴权、不自己连数据库配置,一切横切能力由宿主注入:

```python
@dataclass
class MCPHostContext:
    service_name: str
    # 宿主在连接建立时已完成鉴权,模块拿到的就是可信身份
    get_user_context: Callable[[], dict]     # {user_id, username, workspace_id}
    check_tool_scope: Callable[[str], bool]  # 该 token 是否允许调用此工具
    audit: Callable[[str, str, dict], None]  # 调用审计钩子
```

### 4.3 挂载方式

```python
# 宿主 main.py(以 datacatalog 为例)
from services.datacatalog.mcp_module import create_mcp_app

app.mount("/mcp", create_mcp_app(MCPHostContext(...)))
# 现有 HTTP 端口 8005 上即出现 /mcp/sse 与 /mcp/messages
# 鉴权由 app 级中间件统一完成,模块内无需感知
```

### 4.4 双形态(可移植性保证)

| 形态 | 场景 | 入口 |
|---|---|---|
| **嵌入形态(本项目使用)** | 随宿主服务部署,宿主负责鉴权/审计/限流 | `app.mount("/mcp", create_mcp_app(ctx))` |
| **独立形态(脱离本项目)** | 移植到其他系统单独作为 MCP 服务运行 | `python -m services.<host>.mcp_module --standalone`(自带最小鉴权桩) |

约束:工具实现只允许依赖宿主 service 层接口与注入的 context,禁止模块内部直接 `os.getenv` 读基础设施配置——这是"可脱离、可移植"的硬性要求(与项目配置规范一致:配置集中在 `services/shared/common/config.py`)。

### 4.5 基座升级(mcp_base.py)

- `create_mcp_server()` 增加:统一异常→错误文本格式化、审计钩子接线、工具 scope 检查钩子
- `create_mcp_starlette_app()` 保持返回独立 ASGI app(这是可 mount / 可 standalone 的关键),传输层预留 Streamable HTTP 升级位
- 废弃 `MCP_PORTS` 及独立 MCP 进程的一切规划

## 5. 能力开放矩阵(每宿主 MCP 工具规划)

| 宿主服务 | 挂载路径 | MCP 工具 | 实现基础 | 开放级别 |
|---|---|---|---|---|
| datacatalog | `/mcp` | `search_metadata`、`get_table_schema`、`get_metrics`、`query_tags`(已有);新增 `get_lineage`、`get_glossary_terms` | catalog_service 等 | 全量开放 |
| datamind | `/mcp` | `query_data`(NL2SQL)、`execute_sql`、`analyze_data`(已有);新增 `list_datasources` | execute_pipeline / execute_query_via_engine | 全量开放(强鉴权) |
| datagov | `/mcp` | `run_quality_check`、`get_quality_report`、`get_sensitive_fields`、`list_standards` | quality/standards/security service | 全量开放 |
| dataflow | `/mcp` | `create_sync_task`、`run_task`、`get_task_status`、`list_tasks`(已有);新增 `create_scheduled_task` | 任务 service | 全量开放(写操作需 scope 校验) |
| dataviz | `/mcp` | `create_chart`、`get_dashboard`、`render_chart_data` | chart/dashboard service | 全量开放 |
| vectorservice | `/mcp`(收编) | `search_vector`、`upsert_vector`(已有) | 已存在 | 全量开放 |
| graphservice | `/mcp` | `graph_query`、`get_entity_neighbors` | Neo4j service | 全量开放 |
| authservice | `/mcp`(内部) | `verify_token`、`list_workspaces` | 已存在 | **内部专用**(仅服务间 token) |
| aiplatform | 不挂载 | 管理面 CRUD | — | **不提供 MCP**(管理操作保留在 Web 控制台) |
| dataengine | 不挂载 | — | — | 由 datamind `execute_sql` 封装代理 |

> 决策:aiplatform 是管理面,不作为 AI 工具能力开放,避免"Agent 通过 MCP 自我提权";dataengine 不直接暴露,其执行能力经 datamind 的受控封装输出。

## 6. 鉴权与身份传递(宿主入口层职责)

**问题**:外部 AI 工具经宿主端口访问 `/mcp`,宿主必须还原"代表哪个用户";现有 `execute_query_with_permission` 在 `user_context` 为空时直接放行,对外部通道必须堵住。

**方案:Scoped Token + 宿主统一中间件(MCP 模块零感知)**

1. **Token 签发**:基于现有 JWT 体系(`ADH_SECRET_KEY`)签发短期 Scoped Token,payload 含 `user_id`、`workspace_id`、`datasource_ids`、工具白名单(`scope`)、`exp`
2. **签发入口**:
   - Web 控制台"连接外部 AI 工具"页面 → 展示/复制连接配置(含 token)
   - Embed SDK / 执行层 Adapter 派发任务时自动签发(与 execution-layer-design.md 衔接)
3. **宿主校验**:服务入口中间件对 `/mcp/*` 请求校验 token → 构造 `user_context` 与 scope → 注入 `MCPHostContext`;校验失败直接拒绝连接。**MCP 模块拿到的永远是已鉴权身份**
4. **强制传递**:涉数据工具(`execute_sql` 等)必须携带 `user_context` 调 `execute_query_via_engine`;封堵无身份直通路径
5. **内部服务间**:服务级 token(窄 scope),与用户 token 区分

**数据执行层的 MCP 化**:DataEngine 网关保持唯一 SQL 执行路径(RLS 在引擎内强制执行);`execute_sql` MCP 工具是薄封装,失败降级走 `PermissionEnforcer`,审计双写。外部 AI 工具的每条 SQL 与 ChatBI 用户的每条 SQL 经过同一套鉴权/脱敏/审计。

## 7. 数据库变更

```sql
-- Scoped Token 记录(签发/吊销/审计)
CREATE TABLE adh_mcp_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL,        -- SHA-256,不存明文
    user_id INT NOT NULL,
    workspace_id INT NOT NULL,
    scope JSON,                             -- {"tools": [...], "datasource_ids": [...]}
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_hash (token_hash)
);

-- MCP 工具调用审计
CREATE TABLE adh_mcp_call_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    service VARCHAR(50) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    user_id INT,
    workspace_id INT,
    request_id VARCHAR(64),
    status VARCHAR(20),                     -- success | denied | error
    elapsed_ms INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_time (user_id, created_at)
);
```

迁移文件:`docker/mysql/mcp_open_migration.sql`,与现有迁移约定一致。

## 8. 实施路线

### Phase 0:SDK 基座与宿主鉴权(最高优先级)
1. 升级 `mcp_base.py`:统一错误格式、审计钩子、`MCPHostContext` 定义
2. 实现 Scoped Token 签发/校验 + 宿主入口层的 `/mcp/*` 鉴权中间件
3. 建表 `adh_mcp_tokens`、`adh_mcp_call_logs`

### Phase 1:存量收编与挂载
4. datacatalog:现有 `create_mcp_app()` 按 SDK 规范接线 `MCPHostContext`,挂载到 `main.py`
5. vectorservice:拆除独立 MCP 端口运行方式,改为宿主内挂载
6. datamind/dataflow:手写 JSON-RPC 重写为 MCP SDK 模块并挂载
7. 删除 `MCP_PORTS` 相关规划与配置

### Phase 2:数据执行层闭环
8. datamind `execute_sql` 工具接入 `execute_query_via_engine` + 强制 `user_context`
9. 封堵 `execute_query_with_permission` 的无身份直通路径

### Phase 3:补齐能力矩阵与接入体验
10. datagov / dataviz / graphservice 新增 MCP 模块
11. 控制台 Token 管理页 + 外部工具连接配置一键导出(Claude Desktop / Cursor / opencode / Qoder)

### Phase 4:与执行层设计合流
12. CLIProcessAdapter 复用 §3 的连接配置生成,向 opencode/qoder 注入平台 MCP(SSE URL 即宿主服务地址 + `/mcp`)
13. 验证模块独立形态(`--standalone`)可脱离本项目运行,确认移植性

## 9. 与现有文档/体系的关系

| 对象 | 关系 |
|---|---|
| `execution-layer-design.md` | 本文档的 Scoped Token 与执行层 MCP 是其 CLI/Docker/Remote 适配器的安全前提;CLI 适配器注入的 MCP 配置即"宿主地址 + /mcp + token" |
| 权限体系(enforcer/Ranger/DataEngine RLS) | 不改动,宿主入口层只负责把身份传给它 |
| `services/shared/mcp_client/` | 平台作为 MCP **客户端**接入外部 MCP 的通道,与本设计(平台作为 MCP **服务端**)互为镜像,不冲突 |

## 10. 风险与约束

- **SSE 长连接占用宿主 worker**:MCP 长连接与业务请求共享 uvicorn worker,需设置连接数上限并评估 worker 数量(vectorservice 独立运行反而没这个问题——这是收编带来的代价,需监控)
- **Token 泄露**:短有效期 + 可吊销 + 只存 hash;导出配置页面明确提示勿提交代码库
- **手写 JSON-RPC 迁移**:datamind/dataflow 合计 ~700 行,工具定义可机械翻译为 `@mcp.tool()`
- **模块与宿主的依赖边界**:工具实现只准依赖宿主 service 层公开接口,防止模块与宿主内部实现纠缠导致"不可脱离"
- **aiplatform 不开放的取舍**:如后续有"Agent 管理 Agent"需求,以窄 scope 内部 token 补充,不默认开放
