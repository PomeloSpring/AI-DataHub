# Execution Layer Design — 多执行层架构设计

## 1. 设计目标

将 AI Agent 的执行能力抽象为独立的执行层（Execution Layer），支持多种执行后端：

- **内置执行层**（Default）：当前项目的 Agent 实现，作为默认执行层
- **本地 CLI 进程**：集成 Qoder CLI、OpenCode 等本地 CLI 工具
- **专用进程**：独立的 Agent 执行进程
- **Docker 容器**：携带 CLI 的静态执行层
- **远程 Agent**：函数计算、云服务等远端 Agent

## 2. 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                          系统管理层                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ MCP 服务  │  │  Skills  │  │  Agents  │  │ 知识库   │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓ 注入
┌─────────────────────────────────────────────────────────────────────┐
│                    Execution Layer Manager                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                 ExecutionLayerAdapter (抽象接口)              │   │
│  │  - execute(task, context) → Result                          │   │
│  │  - list_tools() → Tool[]                                    │   │
│  │  - health_check() → Status                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              ↓                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ BuiltIn  │  │ CLI Proc │  │  Docker  │  │  Remote  │          │
│  │ Adapter  │  │ Adapter  │  │ Adapter  │  │ Adapter  │          │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘          │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      工作空间执行层配置                              │
│  Workspace A → [BuiltIn, CLI-Qoder]                                │
│  Workspace B → [BuiltIn, Docker-Claude]                            │
│  Workspace C → [Remote-Agent-FC]                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## 3. 核心接口设计

### 3.1 ExecutionLayerAdapter（抽象基类）

```python
class ExecutionLayerAdapter(ABC):
    """执行层适配器抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def layer_type(self) -> str: ...  # builtin | cli | docker | remote

    @abstractmethod
    async def execute(self, task: ExecutionTask) -> ExecutionResult: ...

    @abstractmethod
    async def list_tools(self) -> list[Tool]: ...

    @abstractmethod
    async def health_check(self) -> HealthStatus: ...

    async def inject_context(self, context: ExecutionContext):
        """注入系统能力（MCP、Skills、Agents）"""
        ...
```

### 3.2 ExecutionContext（执行上下文）

```python
@dataclass
class ExecutionContext:
    """执行上下文 — 携带系统能力注入"""
    workspace_id: int
    mcp_servers: list[MCPServerConfig]  # 该工作空间可用的 MCP 服务
    skills: list[SkillConfig]           # 该工作空间可用的 Skills
    agents: list[AgentConfig]           # 该工作空间可用的 Agents
    knowledge_bases: list[KBConfig]     # 该工作空间关联的知识库
    datasources: list[DSConfig]         # 该工作空间绑定的数据源
    system_prompt: str                  # 注入的系统提示
```

### 3.3 ExecutionTask（执行任务）

```python
@dataclass
class ExecutionTask:
    """执行任务"""
    task_id: str
    question: str
    history: list[dict]
    context: ExecutionContext
    stream: bool = False
    timeout: int = 300
```

## 4. 执行层类型详解

### 4.1 BuiltIn Adapter（内置执行层）

当前项目的 Agent 实现，作为默认执行层：

```python
class BuiltInAdapter(ExecutionLayerAdapter):
    """内置执行层 — 使用当前项目的 Agent 架构"""

    name = "builtin"
    layer_type = "builtin"

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        # 1. 根据 context 构建可用工具列表
        tools = self._build_tools(task.context)

        # 2. 选择合适的 Agent（SQL/DataAnalysis/Configurable）
        agent = self._select_agent(task)

        # 3. 使用 AgentLoop 执行
        result = await agent.run(
            question=task.question,
            history=task.history,
            datasource_id=task.context.datasources[0].id if task.context.datasources else 0,
        )
        return result
```

### 4.2 CLI Process Adapter（本地 CLI 进程）

集成本地 CLI 工具（Qoder、OpenCode 等）：

```python
class CLIProcessAdapter(ExecutionLayerAdapter):
    """本地 CLI 进程执行层"""

    layer_type = "cli"

    def __init__(self, cli_path: str, cli_name: str):
        self.cli_path = cli_path  # e.g., /usr/local/bin/qoder
        self.cli_name = cli_name  # e.g., qoder

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        # 1. 构建 CLI 命令
        cmd = self._build_command(task)

        # 2. 通过子进程执行
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_env(task.context),
        )

        # 3. 通过 stdin 注入上下文（MCP、Skills 等）
        context_json = self._serialize_context(task.context)
        proc.stdin.write(context_json.encode())

        # 4. 收集输出
        stdout, stderr = await proc.communicate()
        return self._parse_output(stdout, stderr)
```

### 4.3 Docker Adapter（Docker 容器执行层）

使用携带 CLI 的 Docker 容器：

```python
class DockerAdapter(ExecutionLayerAdapter):
    """Docker 容器执行层"""

    layer_type = "docker"

    def __init__(self, image: str, config: dict):
        self.image = image  # e.g., "ai-agent/qoder:latest"
        self.config = config

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        # 1. 创建容器（挂载工作空间目录）
        container = await self._create_container(task.context)

        # 2. 注入上下文到容器
        await self._inject_context(container, task.context)

        # 3. 执行任务
        result = await self._exec_in_container(container, task)

        # 4. 清理容器
        await self._remove_container(container)
        return result
```

### 4.4 Remote Agent Adapter（远程 Agent 执行层）

连接远程 Agent 服务（函数计算等）：

```python
class RemoteAgentAdapter(ExecutionLayerAdapter):
    """远程 Agent 执行层"""

    layer_type = "remote"

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint  # e.g., "https://fc.aliyuncs.com/agent/xxx"
        self.api_key = api_key

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        # 1. 构建请求（携带上下文）
        payload = {
            "question": task.question,
            "history": task.history,
            "context": self._serialize_context(task.context),
        }

        # 2. 调用远程 API
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.endpoint}/execute",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=task.timeout),
            ) as resp:
                return await resp.json()
```

## 5. CLI 自动发现机制

```python
class CLIDiscovery:
    """自动发现物理机上的 CLI 工具"""

    KNOWN_CLIS = {
        "qoder": {
            "binary": "qoder",
            "version_cmd": ["qoder", "--version"],
            "capabilities": ["code", "search", "read", "write"],
        },
        "opencode": {
            "binary": "opencode",
            "version_cmd": ["opencode", "--version"],
            "capabilities": ["code", "search", "read", "write"],
        },
        "claude": {
            "binary": "claude",
            "version_cmd": ["claude", "--version"],
            "capabilities": ["code", "search", "read", "write", "web"],
        },
        "cursor": {
            "binary": "cursor",
            "version_cmd": ["cursor", "--version"],
            "capabilities": ["code", "search", "read", "write"],
        },
    }

    async def discover(self) -> list[DiscoveredCLI]:
        """扫描 PATH 中的已知 CLI 工具"""
        discovered = []
        for name, config in self.KNOWN_CLIS.items():
            path = shutil.which(config["binary"])
            if path:
                version = await self._get_version(path, config["version_cmd"])
                discovered.append(DiscoveredCLI(
                    name=name,
                    path=path,
                    version=version,
                    capabilities=config["capabilities"],
                ))
        return discovered
```

## 6. 数据库设计

### 6.1 执行层配置表

```sql
CREATE TABLE adh_execution_layers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200),
    layer_type VARCHAR(20) NOT NULL,  -- builtin | cli | docker | remote
    config JSON,                       -- 类型特定配置
    status VARCHAR(20) DEFAULT 'active',  -- active | inactive | error
    health_check_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_layer_type (layer_type),
    INDEX idx_status (status)
);
```

### 6.2 工作空间执行层关联表

```sql
CREATE TABLE adh_workspace_execution_layers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workspace_id INT NOT NULL,
    execution_layer_id INT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    priority INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ws_layer (workspace_id, execution_layer_id),
    INDEX idx_workspace (workspace_id)
);
```

## 7. API 设计

### 7.1 执行层管理 API

```
GET    /api/admin/execution-layers          # 列表
POST   /api/admin/execution-layers          # 创建
PUT    /api/admin/execution-layers/{id}     # 更新
DELETE /api/admin/execution-layers/{id}     # 删除
POST   /api/admin/execution-layers/{id}/test  # 测试连接
GET    /api/admin/execution-layers/discover   # 自动发现 CLI
```

### 7.2 工作空间执行层配置 API

```
GET    /api/workspaces/{id}/execution-layers       # 获取工作空间执行层
PUT    /api/workspaces/{id}/execution-layers       # 配置工作空间执行层
POST   /api/workspaces/{id}/execution-layers/{layer_id}/default  # 设为默认
```

### 7.3 执行 API

```
POST   /api/execution/execute               # 执行任务
POST   /api/execution/stream                # 流式执行
GET    /api/execution/tasks/{task_id}/status  # 查询任务状态
```

## 8. 工作空间隔离

每个执行层实例拥有独立的工作空间目录：

```
/var/lib/ai-datahub/execution/
├── ws-1/                          # 工作空间 1
│   ├── builtin/                   # 内置执行层工作目录
│   │   ├── cache/
│   │   └── temp/
│   ├── cli-qoder/                 # Qoder CLI 工作目录
│   │   ├── .qoder/
│   │   └── workspace/
│   └── docker-agent-1/            # Docker 执行层工作目录
│       └── workspace/
├── ws-2/                          # 工作空间 2
│   └── ...
```

## 9. 实现计划

### Phase 1: 核心框架
1. 创建 `services/datamind/execution/` 目录
2. 实现 `ExecutionLayerAdapter` 抽象基类
3. 实现 `BuiltInAdapter`（包装现有 Agent）
4. 实现 `ExecutionLayerManager`（执行层管理器）

### Phase 2: CLI 集成
1. 实现 `CLIDiscovery`（自动发现）
2. 实现 `CLIProcessAdapter`（CLI 进程执行）
3. 实现上下文注入机制

### Phase 3: Docker & Remote
1. 实现 `DockerAdapter`
2. 实现 `RemoteAgentAdapter`
3. 实现健康检查机制

### Phase 4: 系统集成
1. 创建数据库表
2. 实现管理 API
3. 实现工作空间配置
4. 前端页面开发

## 10. 与现有架构的关系

| 现有组件 | 执行层中的角色 |
|----------|---------------|
| `BaseAgent` | BuiltInAdapter 的执行单元 |
| `AgentLoop` | BuiltInAdapter 的执行引擎 |
| `MCPToolCaller` | 通过 ExecutionContext 注入到所有执行层 |
| `SandboxService` | DockerAdapter 的基础设施 |
| `adh_agents` | ExecutionContext 的一部分 |
| `adh_mcp_servers` | ExecutionContext 的一部分 |
