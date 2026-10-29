"""轮次起点的 AS-BOT/角色配置事实缓存 — 只缓存配置事实，不缓存授权裁决。

背景（性能）：一次 AS-BOT 轮次起点要串 4~6 组元数据库往返（用户角色 id、
AS-BOT 行、角色权限码、知识库元信息、提示词装饰用的 RLS/角色属性），远端 MySQL
跨公网时实测 claim_session 1.1~1.6s、prompt_build 0.12~0.34s。这些读的都是
**管理员低频改动的配置**，同一角色的多个用户逐轮重复读，纯属浪费。

边界（不得越界，改动前先读这段）：
1. **两档 TTL，按调用点隔离**：
   - 轮次起点（`preflight_request` / `claim_session` 的 resolve_policy 与提示词组装）
     用默认档（`AS_BOT_CFG_CACHE_TTL`，60s）；
   - 工具调用前的实时裁决 `tool_policy.check_tool` 用 **live 档**（`live=True`，
     `AS_BOT_CFG_LIVE_TTL`，默认 5s）——它读的是**同一批配置事实**，但必须与轮次起点
     分 key（前缀 `live:`），否则谁先写谁定 TTL，60s 的轮次缓存会把撤权检测窗口
     悄悄放大到 60s。分档后：写入口失效 = 立即生效；**漏挂失效钩子的最坏情况也只有 5s**。
     会话存活校验（`runtime.verify()`）与执行层有效性（`live_ceiling`）**恒不缓存**，
     每个调用点实时读。
2. **陈旧缓存不能多授权，只能显式重发**：轮次起点即便拿到旧配置，编译出的 digest
   会与 `check_tool` 的实时 digest 比对（tool_policy.py 末尾），不一致即
   PermissionError「AS-BOT 权限已变化，请重新发送消息」——失败可见，不会静默越权。
   数据源作用域同理（`resource_guard.bind_resources` 每次工具调用仍实时读，不缓存）。
3. **只缓存成功结果**：工厂抛错（知识库绑定漂移、未知工具配置等 fail-loud）绝不落
   缓存，否则一次瞬时抖动会被固化成整段 TTL 的错误答案。
4. **值是纯数据**（dict/list/int/str）：Redis 侧 JSON 序列化用 `default=str`，
   塞入不可序列化对象（datetime 等）会被静默转成字符串，缓存下来就是脏数据。
5. Redis 不可用时透明回源（`backend/common/ttl_cache.py` 既有语义）：少一次命中，
   不影响正确性。
6. 写入口必须**同批**接线 `invalidate()`：AS-BOT 增改、角色增删改（含 `adh_roles` 改名/删除）、
   角色权限码变更、权限点 ai_access 变更、用户角色绑定变更、知识库增删改、RLS 策略增删改。
   漏挂的后果由 live 档短 TTL（5s）兜底，且按 §1/§2 只表现为「多一次显式重发」；
   该纪律由 `tests/test_as_bot_config_cache.py` 的 AST 漂移守卫强制（写这些表却不接
   invalidate 即测试失败）。
7. **本缓存不得存「裁决结果」**：只存配置事实（角色 id/AS-BOT 行/权限码/知识库清单），
   编译与判定仍在每次调用发生；撤权后旧事实被失效即重读，digest 不一致就拒执行。

失效粒度：整命名空间清空。配置写操作是低频管理动作，精确逐 key 失效收益小、
漏挂风险大，故统一 blast（与 `api_permission.invalidate_perm_cache` 同口径）。
"""

import logging
import os

from backend.common.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 运维可一键停用（停用即每次回源，不改代码）：AS_BOT_CFG_CACHE=0
_ENABLED = os.getenv("AS_BOT_CFG_CACHE", "1") != "0"
_TTL = int(os.getenv("AS_BOT_CFG_CACHE_TTL", "60"))
# 工具级实时裁决档：一次工具调用有 4 个裁决点（PreToolUse / can_use_tool / handler 前后），
# 远端元库下单次全量重读实测 ~1.0s，4 点 ~4.0s。取 5s：同一工具调用的 4 点折叠成 1 次读，
# 而撤权检测窗口上限 5s（配写入口失效则立即）。
_LIVE_TTL = int(os.getenv("AS_BOT_CFG_LIVE_TTL", "5"))
KEY_LIVE = "live:"             # live 档 key 前缀（与轮次起点档物理隔离，避免 TTL 互相污染）

_cache = TTLCache(name="as_bot_cfg", maxsize=512, ttl=_TTL)

# ── key 前缀（集中登记，避免调用点散写字符串导致失效对不上）──────────────
KEY_ROLE_IDS = "roles:"          # 用户 → 角色 id 清单
KEY_AS_BOTS = "bots:"            # (角色 id 集 + as_bot_key) → 归一化 AS-BOT 配置
KEY_ROLE_PERMS = "perms:"        # 用户 → 角色权限码（AI 级别）
KEY_KB = "kb:"                   # 知识库 id 清单 → 启用的知识库元信息
KEY_RLS_LINES = "rls_lines:"     # (工作空间 + 数据源) → 提示词里的 RLS 摘要行
KEY_ROLE_ATTRS = "role_attrs:"   # 角色名 → 数据范围属性行


def enabled() -> bool:
    return _ENABLED


def get(key: str):
    """读缓存；停用或未命中返回 None（调用方回源）。"""
    if not _ENABLED:
        return None
    return _cache.get(key)


def put(key: str, value) -> None:
    if _ENABLED and value is not None:
        _cache.set(key, value)


def get_or_set(key: str, factory, *, live: bool = False):
    """命中返回缓存，未命中回源并写缓存。

    `factory()` 抛错时不写缓存（异常原样上抛），保证 fail-loud 语义每轮真实生效。
    空结果（空 list/空 dict）照常缓存 —— 空集是合法事实（fail-closed 由消费方判定，
    不得用"空=不缓存"把零权限用户退化成每轮回源）。

    `live=True` 走工具级裁决档：key 加 `live:` 前缀、TTL 用 `_LIVE_TTL`（默认 5s），
    与轮次起点档（60s）互不污染 —— 同一份配置事实分两档存，是为了让撤权检测窗口
    由**调用点**决定，而不是由谁先写缓存决定。
    """
    # 逃生阀：AS_BOT_CFG_LIVE_TTL=0 → 工具裁决回到“每个调用点实读 DB”的旧行为，不改代码。
    # 不能把 ttl=0 递给 Redis（语义不确定），直接在入口短路。
    if live and _LIVE_TTL <= 0:
        return factory()
    real_key = f"{KEY_LIVE}{key}" if live else key
    cached = get(real_key)
    if cached is not None:
        return cached
    value = factory()
    if value is not None and _ENABLED:
        _cache.set(real_key, value, ttl=_LIVE_TTL if live else _TTL)
    return value


def invalidate(reason: str = "") -> None:
    """配置写入口调用：清空整个命名空间（经 Redis 跨实例生效）。"""
    if not _ENABLED:
        return
    _cache.invalidate(None)
    logger.info("[as_bot_cfg] 配置缓存已失效: %s", reason or "未登记原因")


def stats() -> dict:
    """命中率（供 /api/cache 与排障观测）+ 两档 TTL 现值（撤权窗口是排障常问项）。"""
    return {**_cache.stats(), "live_ttl": _LIVE_TTL}
