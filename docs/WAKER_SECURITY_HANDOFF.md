# Waker 安全修复：任务、进度与跨账号交接

> 状态：实施中，因用户切换账号暂停；不是已验收完成的版本。
> 工作区：`/home/star/project/feature/AI-DataHub`。
> 接续者先读本文和 `.qoder/rules/`，再检查当前文件及 Git diff。无需依赖原账号的对话记忆。
> 本轮没有提交 Git，没有重启应用服务，没有完成真实 SDK 聊天端到端验收。

## 1. 用户需求与已经确认的范围

用户报告：Waker 工具配置为空，模型仍宣称可以读取、写入、编辑、检索、执行命令、抓取网页及派发子任务；自定义工具配置后反而没有准确的权限提示。

已经确认的要求：
- Waker 工具、工作空间和资源权限必须严格限制，未授权不能执行。
- 每个 session 独立工作目录；同一 session 多轮对话不能丢失工作区。
- 用户最初询问一致性哈希和分布式工作区，随后明确决定：**先解决单实例，不依赖共享文件系统或多实例网关改造**。
- 用户选择了每 session 独立沙箱方案；本期使用本地 Docker 工具沙箱与本地持久目录。
- 已有 MySQL 用于会话身份、SDK resume 标识及执行互斥，不新增 Redis/NFS/OSS 快照依赖。
- 本期不承诺跨节点恢复。未来落到没有工作区的节点必须明确失败，不能静默新建目录冒充恢复。
- 用户已批准实施计划；最新指令是先将任务与进度写入 docs，方便换账号继续。

原批准计划保存在账号缓存：`/root/.config/Qoder/SharedClientCache/cache/plans/Waker_单实例安全隔离_6c9dd7b0.md`。用户要求不要修改该文件。本文包含接续所需的方案和实际状态，不要求新账号能够访问缓存。

## 2. 安全规范与工作区纪律

- 空权限、空交集必须拒绝；权限查询错误、初始化错误、resume 错误不得变成全量授权或自动新会话重试。
- 提示词不是安全边界，必须验证工具注册、执行前守卫及实际副作用。
- 跨请求状态落 MySQL；不恢复进程内 `_POOL` 作为会话真值源。
- Chat/Agent 取数只能经语义层和既有治理入口，不重新开放 `execute_sql`。
- 身份、workspace、SDK resume 标识由服务端确定，不能信任模型/客户端填写的身份字段。
- AS-BOT 是系统 Waker，仅允许系统本体域，不能借此访问业务本体；写操作继续审批。
- 本轮开始前仓库已经有大量修改、删除和未跟踪文件。**不要将整个 Git diff 当成本轮修改，不要覆盖用户原有改动。**
- 不修改凭据、不把 `services/.env` 内容写入文档、不自行提交 Git。

## 3. 已确认的原始问题

1. Qoder SDK 空工具组被 `or None` 转成“注册全部工具”。
2. 空标准工具列表跳过 deny-list；工具目录本身也将空列表解释为不限制。
3. Waker 没有 MCP 引用时，仍保留预加载的工作空间 MCP。
4. 执行层/工作空间白名单交集为空，被解释为“不限制”。
5. Waker 角色没有授权、无交集、非法选择时仍可能扩大候选集合。
6. Claude SDK 没有采用同一套 Waker 策略，SDK 均使用 `bypassPermissions`。
7. 工作目录只按 workspace 划分，不按 session 划分。
8. 客户端可回传、保存 SDK session 标识；恢复失败会新建重试；同会话忙碌会另开执行进程。

截图是模型能力自述，不能独立证明当时已经执行过越权操作；以上问题通过代码核查确认。

## 4. 方案与实际实施状态

| 子任务 | 当前状态 | 说明 |
|---|---|---|
| 改前评测基线 | 已执行 | golden eval 42/42，所有 tag 100% |
| 统一权限策略 | 已写代码，部分回归通过 | 空集合拒绝、精确工具名、角色/工作空间交集、禁止裸 SQL |
| 服务端 session 绑定 | 已写代码，真实 MySQL 核心回归通过 | conversation/owner/workspace/Waker/backend 绑定、条件领取、服务端 resume |
| 独立本地目录 | 已写代码，路径单测通过 | workspace 与 runtime 分离、存储节点标识、禁止宽目录覆盖 |
| 双 SDK 统一执行 | 已重构，单测/参数契约通过 | 删除旧 Qoder 连接池及自动重试，关闭原生工具，统一守卫 |
| Docker 文件/命令工具 | 已写代码，镜像已构建 | **真实容器隔离测试已编写但尚未执行** |
| 外部 MCP 逐工具代理 | 已写代码 | 精确发现/注册及描述注入；新测试尚需重跑，远端身份契约未验收 |
| 子任务 | 明确阻断 | 尚未证明子代理隔离，不开放原生 Agent/Task；不能宣称已支持 |
| 资源域与元数据投影 | 已写代码，需专项验证 | 新增业务投影，语义取数校验 binding 域，存在待复核边界 |
| 前端权限展示与会话隔离 | 已写代码，部分测试通过 | SSE 能力清单、外部工具勾选、停止客户端维护 SDK ID |
| 最终评测与构建 | 未完成 | 改后 golden eval 未跑；完整前端构建失败；末次修改未全量重跑 |
| 服务部署与真实聊天验收 | 未完成 | **尚未重启 datamind/aiplatform，不能认定运行服务已应用修复** |

### 4.1 权限策略

`services/datamind/execution/tool_policy.py`：
- `normalize_tools` 严格解析工具配置；Waker 空配置为无授权。
- 显式 `tools.mcp={}` 不回退粗粒度 groups；旧 groups 转成已知工具清单。
- 外部工具使用 `tools.external = {"MCP服务ID": ["工具名"]}`，同时必须引用该 MCP 服务。
- 只有服务绑定而无逐工具清单会明确报错。
- Bash 要求同时显式授权 read；未配置 write/edit 时沙箱挂载只读。
- `compile_policy` 合并上层白名单；`ToolPolicy.manifest` 输出名称与描述。
- `resolve_policy` 重新读取服务端身份与 Waker；`check_tool` 校验执行 token 和 Waker 策略摘要。
- 所选模型也要属于 Waker 配置的 models；未指定时采用其第一个模型或执行层默认。

`wakers.py`、`manager.py`、`tool_catalog.py`、`service.py`：
- 非零 workspace 不再无绑定时回退全局 Waker。
- 非 admin 依据当前角色授权集合取交集；非法选择明确拒绝。
- 上层限制区分 `None`（继承）与 `[]`（全部禁止）。
- manager 拒绝非 Qoder/Claude SDK 的旧 CLI 执行路径；直接执行 API 同时限制旧 builtin 路径。
- AS-BOT 强制系统域工具策略，标准工具和外部 MCP 清空。

### 4.2 会话与执行互斥

`services/datamind/execution/session_workspace.py`：
- `claim_session` 先核对 conversation 归属及授权，使用 MySQL 事务/条件更新领取执行。
- 新表 `adh_agent_sessions` 保存随机 session key、conversation、owner、workspace、Waker、backend、节点、SDK ID、policy hash、status、token、version，以及宿主/SDK PID 和进程启动标识。
- 相同 conversation 唯一执行记录；并发失败为 409。
- 目录：`${ADH_WORKSPACES_DIR}/ws_<id>/sessions/<session_key>/{workspace,runtime}`。
- SDK HOME、配置目录、临时目录位于 runtime；工具容器仅挂载 workspace。
- `cwd`/`allowed_dirs` 宽目录配置明确拒绝，不自动忽略。
- 旧 SDK 会话不盲目接管，缺少目录/节点不匹配明确拒绝。
- `.storage-node` 在本地持久卷内原子发布，用作存储归属标识。
- `record_client` 依赖 SDK `_transport._process.pid`，并读取 Linux `/proc` 启动标识。
- `reconcile_stale_sessions` 只核对当前节点、宿主已退出的记录；无法确认旧进程/容器停止就保持阻断。已接入 datamind startup，尚未运行部署验证。
- `interrupted` 会话当前要求新建会话；`closed` 用于已清空的会话；不自动重放请求。

### 4.3 SDK 与工具沙箱

- `adapters/qoder_sdk_adapter.py` 和 `claude_sdk_adapter.py` 已重写为薄适配器，共用 `secure_sdk.py`；旧 pool、bypass、resume 失败自动重试代码已移除。
- `secure_sdk.py` 关闭原生工具：`tools=[]`、`permission_mode="default"`、`setting_sources=[]`、空 agents。
- 只注册受控业务 MCP、会话标准工具代理和显式外部 MCP。
- SDK `PreToolUse` hook、权限回调、`sdk_tools/compat.py` handler 三处校验。
- 模型进程不直接获得宿主文件/Shell工具；命令进程只存在于独立 Docker 工具容器内。
- `sandbox_worker.py` 使用标准库，不导入平台配置；路径按 `/workspace` 限制，新增逐层 dir_fd/O_NOFOLLOW 打开。
- `SessionToolSandbox` 位于 `services/aiplatform/services/sandbox_executor.py`；非 root、只读根、无网络、资源上限、cap-drop、no-new-privileges、只挂载会话目录。
- `workspace_tools.py` 实现 read/write/edit/glob/grep/bash/webfetch。webfetch 使用公共地址校验与自定义 DNS resolver。
- `external_tools.py` 精确发现 MCP 工具并做代理；stdio 放容器，HTTP/SSE 转发由服务端生成的上下文头；服务配置或工具 schema 变化会拒绝继续使用旧目录。
- `scoped_metadata.py` 对目录、本体、术语、指标工具返回授权域内业务投影，不返回物理表结构。它是新路径，不能用旧 handler 的测试通过代替该路径验收。
- `semantic_query.py` 强制服务端身份与源上下文，并在 Waker 执行中核验 binding 的资源域。

### 4.4 API 与前端

- `api/chat.py`、`pipeline.py`、`execution.py` 及 `services/chat_service.py` 接入会话/权限检查。
- 客户端不再决定 SDK resume；conversation 更新接口拒绝 `executor_session_id` 写入。
- 附件按 ID 在安全执行层重新查所有者与 workspace，再物化到当前会话目录。
- 禁止把绑定 Waker 的会话切换到旧 quick/deep 管线以绕过限制。
- SSE 新增 `capabilities` 事件；Chat 页展示当前会话工具清单。
- Waker 管理页从后端加载工具目录，支持外部 MCP 逐工具配置，显示空配置与高风险能力说明。
- `chatStore` / `asBotStore` 停止回传及持久化 SDK ID；切换 Waker/workspace 清空当前会话绑定；恢复会话采用服务端 workspace/Waker。
- 最后修正了 clear：取消当前请求、切换到新的前端会话上下文；后端清空空闲会话记录时关闭原执行会话，避免隐藏历史继续 resume。**这部分最后修改尚未重测。**

## 5. 本轮文件范围

新增：
- `docker/agent-sandbox/Dockerfile`
- `docker/mysql/agent_sessions_migration.sql`
- `scripts/agent_session_admin.py`
- `services/datamind/execution/tool_policy.py`
- `services/datamind/execution/session_workspace.py`
- `services/datamind/execution/secure_sdk.py`
- `services/datamind/execution/resource_guard.py`
- `services/datamind/execution/sandbox_worker.py`
- `services/datamind/execution/sdk_tools/workspace_tools.py`
- `services/datamind/execution/sdk_tools/external_tools.py`
- `services/datamind/execution/sdk_tools/scoped_metadata.py`
- `tests/test_waker_security.py`

修改：
- `.dockerignore`
- `services/aiplatform/api/wakers.py`
- `services/aiplatform/services/sandbox_executor.py`
- `services/datamind/main.py`、`requirements.txt`
- `services/datamind/api/{chat,pipeline,execution}.py`
- `services/datamind/services/chat_service.py`
- `services/datamind/execution/{wakers,manager,tool_catalog,service,prompt_composer}.py`
- `services/datamind/execution/adapters/{qoder_sdk_adapter,claude_sdk_adapter}.py`
- `services/datamind/execution/sdk_tools/{__init__,compat,semantic_query,semantic_tools}.py`
- `frontend/src/pages/Chat.tsx`、`frontend/src/pages/admin/WakerManager.tsx`
- `frontend/src/stores/{chatStore,asBotStore}.ts`
- `frontend/src/stores/__tests__/chatStore.test.ts`
- `tests/{test_waker_resolution,test_qoder_options_build,test_claude_sdk_adapter}.py`

这些文件中部分已有其他任务的改动，尤其语义工具、前端 store 和服务 API。接续时保留合并后的内容，不要按旧版本整文件还原。

## 6. 已实际执行的环境变更与验证

### 环境变更
- 已执行 `venv/bin/python scripts/agent_session_admin.py migrate`，当前配置的元数据库已创建 `adh_agent_sessions` 表。
- 没有主动修改用户的 Waker 配置、角色绑定或已有会话数据。真实 MySQL 测试插入专用测试记录并在 finally 中清理。
- 已安装缺少的依赖：aiohttp 3.14.3、claude-agent-sdk 0.2.157。requirements 增加 `aiohttp>=3.12.15,<4`；原本已有 Claude SDK 依赖声明。
- Docker 镜像 `adh-agent-sandbox:1` 已构建成功；最后一次 image ID 为 `b771b7c41848`。换账号后先确认使用相同 Docker context 且镜像仍存在。
- `.dockerignore` 增加凭据、Rust target、嵌套 node_modules 等排除项，构建上下文从数百 MB 收敛到约 17 MB。
- **未重启 datamind（8001）和 aiplatform（8007）。没有启动新的预览服务器。**

### 已通过的测试（注意执行时间先后）

改前：
```bash
venv/bin/python -m tests.eval.runner
```
结果：42/42，所有 tag 100%。来源档位：dict_exact=46、dict_alias=9、phys_col=2、physical_desc=1、fuzzy_rejected=1。

本轮较后期执行：
```bash
ADH_TEST_AGENT_MYSQL=1 venv/bin/python -m pytest tests/test_waker_security.py tests/test_waker_resolution.py tests/test_qoder_options_build.py tests/test_claude_sdk_adapter.py tests/test_data_moat_enforcement.py tests/test_permission_enforcer.py tests/test_permission_e2e.py tests/test_as_bot_system_tools.py tests/test_as_bot_execution.py -q --tb=short
```
结果：**159 passed, 1 skipped**。当时跳过的是需要 `ADH_TEST_AGENT_DOCKER=1` 的真实容器实测。

其中真实 MySQL 验证了两线程竞争只有一个领取成功、另一个 409、续聊目录/SDK ID 稳定、跨 owner 和伪造 SDK ID 拒绝。不是多实例端到端验收。

该次通过之后还修改/补充了元数据投影、MCP 测试、clear 行为、管线绕过检查和旧 CLI 拒绝等内容，**159 passed 不能代表当前最终工作树已经全绿**。

### 前端验证现状

```bash
npm --prefix frontend test -- src/stores/__tests__/chatStore.test.ts
```
最后结果：30 passed、1 failed。失败为 clear 未同步清空当前上下文；随后修改了 clear 与后端关闭会话逻辑，尚未重跑。

```bash
npm --prefix frontend run build
```
失败。错误集中于 App、Layout、WorkspaceManagerV2、ExecutionResultCard、McpInstallProgress、SyncTasks 等模块，包括 unused、缺少导出类型和类型不匹配。本轮没有逐项核实全部报错的历史基线，也没有关闭 TS 检查来掩盖错误。不要把这个命令描述为通过。

### 尚未执行
- 当前工作树全部新增/修改测试的最终重跑。
- `ADH_TEST_AGENT_DOCKER=1` 真实容器测试。
- 修改后 golden eval 与来源档位对比。
- 真实 Qoder/Claude 进程的空工具/自定义工具调用、取消、续聊、重启恢复。
- 真实外部 MCP 调用及权限撤回端到端验证。
- 服务重启和浏览器验收。

## 7. 接续时必须优先核查的风险与缺口

以下是待核查/待完善项，不应理解为已经验证修复：

1. **取消和停止确认**：`secure_sdk.py` 当前主要依据 `client.disconnect()` 成功返回判断 SDK 已停止；需要独立核验已登记 PID/启动标识，验证断流清理、子进程及 Docker 容器没有残留。`asyncio.wait_for(iterator.__anext__())` 与 SDK/anyio 的任务/取消作用域需要真实进程测试。
2. **领取与取消的竞态**：会话 claim 在 `asyncio.to_thread` 中执行，取消不会自动终止线程；检查取消发生在领取完成前、runtime 尚未交回时是否遗留 running 记录，避免没有 owner 清理的状态。
3. **HTTP 409 的原子性**：入口有 preflight，真正 claim 在 SSE 生成器内；同时通过 preflight 的竞争者可能收到 SSE 内 status_code=409，而非 HTTP 409。若要求严格 HTTP 409，需要在返回 StreamingResponse 前原子领取并确保清理。
4. **撤权完整性**：每次工具调用重读 Waker/身份，但 `runtime.ceiling` 是本轮领取时的上层白名单快照；执行层/工作空间工具限制的中途撤回尚需实时重校验，不能仅依赖下一轮请求刷新。
5. **资源域覆盖**：复核 `resource_guard.py`、`scoped_metadata.py`、screen/ontology/knowledge 工具的每条路径。新业务投影应补专项回归，不能因为原 handler 测试通过就认为包装后的路径无问题。
6. **暂不支持的能力**：`task` 明确拒绝启用；`query_by_tags` 在资源守卫中明确拒绝执行。应完善隔离，或在“配置/实际可用/不可用原因”展示中准确表达，不能把这些当已实现能力。
7. **外部 MCP 身份契约**：现在 HTTP/SSE 使用服务端生成的 `X-ADH-*` 上下文头，但并未证明远端会验证可信身份、数据权限与治理；需要签名/既有可信内部身份契约等实际约束。绑定工具名不代表任意远端返回数据天然经过平台治理。
8. **工具错误显示**：Bash 的非零 exit_code 当前在结果内容中返回，检查 MCP `isError` 映射是否充分；不能让失败命令被当成成功。业务候选提示应保留，不应被通用错误文案吞掉。
9. **网页结果完整性**：webfetch 当前使用一次 `response.content.read(100001)`；需核查分块响应会不会提前返回部分内容且错误标记 truncated=false。
10. **首次初始化失败的状态**：沙箱未就绪等发生在发出用户请求前的失败，目前也可能把会话标记 interrupted 并要求新建。可以区分“确认无执行副作用的初始化失败”和“执行中断”，但不能恢复自动重放或静默换会话。
11. **配置错误对候选列表的影响**：`resolve_wakers` 最后逐条严格 normalize；候选中一个坏配置可能使整个可选列表失败。应明确显示不可用项及原因，而不是悄悄放权、跳过或令合法 Waker 无法选择。
12. **可观测与输出脱敏**：双 SDK 现在共用流事件映射，复核工具 span 是否仍完整；`PermissionError/ValueError` 对外文案要确保是安全业务错误，不把 SDK 原始细节或路径直接透传。
13. **旧配置兼容**：旧 cwd/allowed_dirs、旧裸 SQL 组、外部 MCP 缺少逐工具授权、旧 executor_session_id 都会明确拒绝。部署前应向用户列出实际受影响配置，不擅自扩大授权或自动迁移旧共享工作区内容。
14. **清空/切换会话**：最后修改未重测，需验证正在执行时清空、跨会话切换、删除会话、异步 loadWakers 结果与持久绑定不串台。AS-BOT store 的部分旧异常处理也仍待复核。
15. **原生/旧入口旁路**：manager 已拒绝未知 CLI；继续检查直接构造 CLIProcessAdapter/BuiltInAdapter、deep、附件和独立执行入口是否能绕过 Waker 会话约束。
16. **单实例边界**：PID/`/proc` 与本地节点标识方案只按本期单机环境验收，不要宣称已完成多 PID namespace、多宿主共享卷或高可用恢复。

## 8. 建议接续顺序与命令

1. 阅读本文、`.qoder/rules/`，检查当前差异；先修上节安全缺口，尤其生命周期、撤权和外部 MCP 信任边界。
2. 重跑单测和真实 MySQL 回归，修复末次修改引入的问题。
3. 执行真实 Docker 用例；如 worker 有新改动，先重新构建镜像。
4. 重跑改后 golden eval 和护城河门禁，核对总量/tag/来源档位；补资源域、清空、取消和自定义工具的专项测试。
5. 重跑前端 store 测试；单独说明全项目构建阻塞，不能以关闭类型检查解决。
6. 最后才按已有启停脚本重启受影响服务，验证真实会话与界面，再给用户交付结论。

从工作区根目录执行：
```bash
venv/bin/python scripts/agent_session_admin.py status

ADH_TEST_AGENT_MYSQL=1 ADH_TEST_AGENT_DOCKER=1 venv/bin/python -m pytest tests/test_waker_security.py tests/test_waker_resolution.py tests/test_qoder_options_build.py tests/test_claude_sdk_adapter.py -q --tb=short

venv/bin/python -m tests.eval.runner
venv/bin/python -m pytest tests/test_data_moat_enforcement.py tests/test_permission_enforcer.py tests/test_permission_e2e.py tests/test_as_bot_system_tools.py tests/test_as_bot_execution.py tests/test_as_bot_graph_system_scope.py tests/test_as_bot_history.py -q --tb=short

npm --prefix frontend test -- src/stores/__tests__/chatStore.test.ts
npm --prefix frontend run build
```

需要重建镜像时：
```bash
docker build -f docker/agent-sandbox/Dockerfile -t adh-agent-sandbox:1 .
```

新环境才需要重新核对迁移；当前环境已经执行过：
```bash
venv/bin/python scripts/agent_session_admin.py migrate
```

`agent_session_admin.py reconcile` 会核对并可能终止已确认宿主退出的残留执行，不是纯只读命令。使用前检查实现、节点/PID namespace 与现场状态。不要盲目运行，也不要直接把 running 改 idle。

服务启停请先阅读 `services/datamind/start.sh`、`stop.sh`、`services/aiplatform/start.sh`、`stop.sh` 及共享启动器，确认只操作本任务相关服务。当前没有执行过重启。

## 9. 给新账号的接续指令

> 请继续实施 `docs/WAKER_SECURITY_HANDOFF.md` 中的 Waker 单实例安全修复。用户已批准方案，但当前尚未完成安全验收。先检查“必须优先核查的风险与缺口”，保留已有未提交改动，不修改原账号缓存计划文件，不恢复 bypass/空权限放开/失败自动新会话，不自行提交 Git。完成当前代码的安全修正、真实 Docker/SDK/MySQL 验证、前端回归和必要服务重启后，再报告已验证结果及剩余限制。
