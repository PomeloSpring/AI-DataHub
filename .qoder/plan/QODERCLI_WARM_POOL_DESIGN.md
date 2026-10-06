# qodercli 会话级运行池（Warm Pool）设计

> 状态：设计评审稿（未实施）
> 目标读者：执行层改造的实现者与评审者
> 相关规范：`distributed-first.md`（进程内状态即缺陷源）、`security-guardrails.md`、`WAKER_SECURITY_HANDOFF.md`

## 1. 背景与问题

当前 qodercli 执行是**每轮对话冷启动一次**：

```
chat_service.py:311  adapter.execute_stream(task)
  └─ secure_sdk.execute_stream            # pump + 超时包裹 + SSE 事件队列
      └─ _execute_stream
          claim_session                   # MySQL 行锁认领执行权（409 = 会话互斥）
          bind_resources → sandbox_ensure → place_attachments → build_options
          QoderSDKClient(options).connect()   ← spawn qodercli Node 子进程
          client.query(prompt) → 流式接收
          finally:
            client.disconnect()               ← 直接杀掉子进程
            runtime.confirm_stopped()         # 核验 sdk_pid 已死 + 沙箱容器已清
            runtime.finish(sdk_session_id)    # status→idle/interrupted，释放执行权
```

每轮固定开销 = Node 进程启动 + JS bundle 加载 + SDK 握手 + `resume=sdk_session_id` 历史重放，通常 1–4 秒（`secure_sdk.py` 已有 `[timing]` 埋点可在灰度期量化）。多轮对话每轮都付一遍。

## 2. 目标与非目标

**目标**
- 同一会话第二轮起免 spawn：复用保活的 qodercli 进程，直接 `client.query()`。
- 首 token 延迟下降 1–4s（灰度用 `[timing]` 数据验证）。
- 可选预热：会话建立/用户开始输入时提前 spawn。

**非目标（红线）**
- ❌ 不做跨会话/跨用户进程复用。qodercli 进程内持有历史、工作目录、MCP/工具授权与凭据上下文，跨会话复用 = 会话串数据，违反安全边界。
- ❌ 不改变会话执行互斥语义（`claim_session` 的 running 409 / 乐观锁不变）。
- ❌ 不做跨实例进程迁移。warm 进程永远是实例本地的。
- ❌ CLI（非 SDK）模式不池化：`CLIProcessAdapter.execute_stream` 是一次性子进程命令，无会话内状态可复用。

## 3. 总体设计：正确性与性能严格分层

```
正确性真值源（不变）：MySQL adh_agent_sessions
    status / execution_token / sdk_session_id / policy_hash / sdk_pid / sdk_start
性能缓存（可丢弃）：进程内 WarmPool（每 datamind 实例一份）
    {session_key: WarmEntry}
```

**铁律：warm 命中与否，执行结果必须等价。** 请求落到没有 warm 进程的实例（多实例、重启后、被淘汰后）走冷启动，行为一致。池不承载任何正确性状态——这是过 `distributed-first` 多实例拷问的前提。

**为什么按 session_key 保活是安全的**：单会话执行互斥（`claim_session`：`UPDATE ... WHERE status='idle'` 乐观锁 + running 拒绝）保证同一时刻只有一个执行使用该会话；warm 进程归属唯一，闲置期间不存在并发使用者。

## 4. 状态机

### 4.1 会话执行状态（MySQL `adh_agent_sessions.status`，不变）
```
idle ──claim──▶ running ──finish(success)──▶ idle
                   │ ──finish(fail)────▶ interrupted ──(禁止续跑，需新建会话)
                   ├─ deleting / closed（终态分支，禁止执行）
```

### 4.2 进程存活状态（新增，WarmPool 视角）
```
absent ─spawn/connect─▶ alive(idle) ─claim+query─▶ alive(executing) ─┐
   ▲                        ▲                                          │
   │                        └── 保活回池 ◀──执行成功且指纹未变───────────┤
   │                                                                   │
   └──TTL/LRU/轮换/指纹失效/删除会话/服务关停── evict(disconnect) ◀──────┤
   └──────────────执行失败/超时/异常── 直接 disconnect（现状行为）───────┘
```

### 4.3 组合迁移条件（关键路径）
| 事件 | status 迁移 | 进程迁移 | 说明 |
|---|---|---|---|
| 执行成功且可保活 | running→idle | alive(executing)→alive(idle) | `finish()` 照常释放执行权；进程不杀 |
| 执行失败/超时/异常 | running→interrupted | alive(executing)→absent | 与现状一致，杀进程 |
| 会话删除/清空 | →deleting/closed | alive→absent | `delete_conversation_workspace` 同步 evict+杀 |
| policy_hash 变化 | 不变 | alive→absent | 下轮冷启动，拒绝带旧权限执行 |
| TTL/LRU/轮换到期 | 不变 | alive→absent | 纯回收，无状态影响 |
| 实例重启/崩溃 | 不变 | 全部 absent | `sdk_pid/sdk_start` 成 stale，reconcile 清理 |

## 5. WarmPool 设计（新增 `services/datamind/execution/warm_pool.py`）

```python
@dataclass
class WarmEntry:
    session_key: str
    client: object            # QoderSDKClient（已 connect，闲置）
    fingerprint: str          # 见 §5.1
    sdk_pid: int; sdk_start: str
    created_at: float; last_used: float; use_count: int

class WarmPool:
    """进程内 qodercli 保活注册表。仅是性能缓存：
    任何时刻可整体丢弃，执行正确性不得依赖其存在。"""
    def acquire(session_key, fingerprint) -> WarmEntry | None   # 命中即取出并标记使用中
    def release(entry, keep_alive: bool)                        # 回池或淘汰
    def evict(session_key, reason: str)                         # 同步 disconnect + 杀进程
    def evict_lru() / sweep_expired()                           # 后台回收
    def shutdown_all()                                          # 服务关停钩子
```

- **并发控制**：`asyncio.Lock` 保护 registry；entry 有 `in_use` 标志（虽然互斥锁已保证，双保险防误发）。
- **淘汰策略**：TTL（`EXEC_WARM_TTL`，默认 900s）+ LRU 上限（`EXEC_WARM_MAX`，默认 8）+ 轮换（`EXEC_WARM_MAX_TURNS`，默认 20 轮后重建，防 Node 长跑内存膨胀）。
- **失败降级 = 冷启动**（这不是降级掩盖：warm 只是省时间，用户不可感知差异）。

### 5.1 命中指纹（任一变化即淘汰重建）
```
fingerprint = hash(
    backend, layer_id, policy.digest,          # 权限/工具授权变化
    model_ref,                                  # 模型切换
    sorted(mcp_server_names),                   # MCP 注册集合
    sdk_cli_path,                               # 执行层升级
)
```
`policy.digest` 复用 `resolve_policy().digest`（现有 `policy_hash` 同源），杜绝 warm 进程持旧权限。

### 5.2 多轮语义对齐
- 冷启动续轮：`resume=runtime.sdk_session_id`，`include_history=False`（历史由 SDK 侧恢复）。
- warm 命中：进程内历史已完整，同样 `include_history=False`，直接 `query()`。
- 两者输出事件流结构一致，前端无感知。

## 6. 改造点清单（逐文件）

| 文件 | 改动 |
|---|---|
| `execution/warm_pool.py` | **新增**：WarmEntry/WarmPool（§5） |
| `execution/secure_sdk.py::_execute_stream` | ①入口：`WarmPool.acquire(session_key, fingerprint)` 命中则跳过 `QoderSDKClient` 创建/连接，直接 `query`；②`finally` 分支：成功且指纹未变→`release(keep_alive=True)`（不 disconnect），失败/超时/异常→现状杀进程 |
| `execution/session_workspace.py::confirm_stopped` | 拆两条路径：冷路径 `confirm_stopped()`（现状：核验进程已死）；warm 路径 `confirm_warm_retained()`（核验进程**仍存活且身份匹配** + 沙箱容器已清）。warm 不核验死亡是**有意设计**，须在注释与本档双向声明 |
| `execution/session_workspace.py::finish` | 语义不变（释放执行权）；warm 保留时 `sdk_pid/sdk_start` 列**继续指向活进程**（现有列即可作 warm 登记，无需加列） |
| `execution/session_workspace.py::delete_conversation_workspace` / `_session_cleanup_root` | 删会话/清空时同步 `WarmPool.evict(session_key)` 并杀进程（先 evict 再删目录，防进程写回） |
| `execution/adapters/qoder_sdk_adapter.py` | `execute_stream` 透传不变；仅确认 warm 分支的事件映射一致 |
| `datamind/main.py` | shutdown 钩子：`WarmPool.shutdown_all()`（SIGTERM 逐个 disconnect，避免孤儿 Node 进程） |
| 启动 reconcile | datamind 启动时扫 `adh_agent_sessions WHERE sdk_pid>0`，`process_identity(sdk_pid) != sdk_start` → 清 `sdk_pid/sdk_start`（stale 清理，与 `owner_pid` 现有机制同构） |
| `services/.env` / layer config | 新增配置（§8） |

## 7. 异常路径与回收矩阵

| 异常 | 处置 | 用户可见行为 |
|---|---|---|
| 执行超时（900s 上限/自定义） | 杀进程（现状），不入池 | 与现状一致 |
| SSE 中途断线 | 泵任务继续跑完；结束时按结果决定入池与否 | 重连后从共享层恢复/明确失败（现状语义不变） |
| 工具容器残留（`confirm` 失败） | `unsafe=True` 阻断会话（现状），不入池 | 「会话已阻断」 |
| warm 进程僵死（query 无响应） | query 超时→disconnect→冷启动重试**一次**（显式标注 warm_evicted，不静默） | 本轮变慢但结果正确 |
| policy/MCP/模型变化 | 指纹不匹配→evict→冷启动 | 无感知 |
| 会话删除/清空/切换 Waker | 同步 evict + 杀进程 | 无感知 |
| 实例 SIGTERM/restart | shutdown_all 杀全部 warm；他实例请求走冷启动 | 无感知 |
| 实例崩溃（kill -9） | warm 进程成孤儿：启动 reconcile 按 `sdk_pid/sdk_start` 判 stale 并 kill+清列 | 无感知 |
| 多实例并发请求同会话 | DB 互斥不变，第二个 409 | 「该会话正在执行」（现状） |
| 内存超限（warm 数达上限） | LRU 淘汰最久未用 | 无感知 |

## 8. 配置项

```bash
# services/.env（全局默认）+ adh_execution_layers.config（按层覆盖）
EXEC_WARM_ENABLED=false      # 灰度开关，默认关
EXEC_WARM_TTL=900            # 闲置回收秒数
EXEC_WARM_MAX=8              # 单实例最大保活进程数（内存上限）
EXEC_WARM_MAX_TURNS=20       # 单进程最多服务轮数，超过重建
```

## 9. 多实例拷问（distributed-first §4 评审快查）

- **状态在共享层吗？** 正确性状态全部在 MySQL；WarmPool 是声明式的只读性能缓存（丢弃策略明确）。
- **两实例同时执行这段代码结果一致吗？** 一致：A 命中 warm、B 冷启动，输出事件流与结果等价（指纹保证权限/模型一致）。
- **进程重启/换实例后还能恢复吗？** 能：`sdk_session_id` 在 MySQL，冷启动 resume；stale 进程由 reconcile 清理。
- **进程内状态例外条款**：WarmPool 属 `distributed-first §1` 允许的「无一致性要求的只读缓存」，失效策略 = §7 全表。

## 10. 回归门禁与测试计划

**新增单测（先写用例再实现）**
1. warm 命中：同会话第二轮不调用 `client.connect`（mock 断言），事件流与冷启动等价。
2. 指纹失效：policy/模型/MCP 任一变化 → evict + 冷启动。
3. TTL/LRU/轮换：过期、超限、超轮数分别触发回收。
4. 删除会话/清空 → warm 进程被杀（断言 evict 调用 + disconnect）。
5. `confirm_warm_retained`：进程身份不匹配 → `unsafe` 阻断，不释放执行权。
6. 互斥不受影响：running 会话二次 `claim_session` 仍 409。
7. reconcile：stale `sdk_pid` 启动清理。
8. 失败/超时分支绝不入池（防脏状态复用）。

**既有门禁（触碰 `secure_sdk.py`/`session_workspace.py` 后必跑）**
- `tests/test_execution_stream_utils.py`、`tests/test_qoder_options_build.py`、`tests/test_claude_sdk_adapter.py`、`tests/test_workspace_assets.py`、`tests/test_session_clear_reset.py`（会话清空）
- 护城河三件套：`test_data_moat_enforcement.py` / `test_permission_enforcer.py` / `test_permission_e2e.py`
- `tests/test_waker_security.py`（执行会话身份/互斥契约）

**性能验收（灰度）**
- 用现有 `[timing]` 埋点对比：同会话第 2–5 轮 `sdk_connect` 耗时归零、`client_query_sent` 前总耗时下降 ≥1s。
- 压测：8 并发会话 × 5 轮，观察 RSS（Node 进程数×内存）与 LRU 命中率。

## 11. 灰度与回滚

1. `EXEC_WARM_ENABLED=false` 合入（默认关，零行为变化）。
2. 单机置 true，跑 §10 门禁 + timing 对比。
3. 观察一周内存曲线与 `warm_evicted` 日志，调 `TTL/MAX`。
4. 默认开；回滚 = 置 false（池即刻可整体丢弃，无需数据迁移）。
