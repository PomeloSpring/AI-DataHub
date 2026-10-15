"""Qoder 账号 credit 配额读取 —— 执行层 `get_usage_info()` 的唯一消费口。

为什么读它：Qoder **按 credit 计量、不回报美元**（实测 `ResultMessage.total_cost_usd`
与 `model_usage[].costUSD` 恒为 0，`UsageInfo` 各配额桶 `unit` 均为 `credits`）。
所以"实际费用"的权威口径就是这个账号级 credit 配额进度，而不是折算出来的美元估算值。

代价与约束：
- 读一次要起一个隔离的 CLI 子进程（不挂任何工具/MCP、不碰会话工作区），故按 TTL 缓存；
- 读取失败**显式返回 `available=false` + 原因**供前端提示，不回退空数据、不伪造进度
  （见 no-silent-degradation 规则）。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 配额是账号级慢变量：默认 2 分钟缓存；失败只缓存 30s，便于修好后立刻恢复
QUOTA_TTL_SECONDS = int(os.getenv("OBSERVABILITY_QUOTA_TTL_SECONDS", "120"))
QUOTA_FAIL_TTL_SECONDS = 30
_CLI_TIMEOUT_SECONDS = 30.0

# (UsageInfo 字段, 面向用户的名称) —— 顺序即前端展示顺序
_BUCKET_SOURCES = (
    ("userQuota", "个人套餐"),
    ("addOnQuota", "加量包"),
    ("orgResourcePackage", "组织资源包"),
)


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _normalize_buckets(info: dict) -> list[dict]:
    """把 UsageInfo 的三个配额桶归一为 {label, used, total, remaining, percentage}。

    组织资源包用 `cap` 而非 `total`，加量包多一个 `detailUrl` —— 差异在此收敛。
    """
    buckets: list[dict] = []
    for key, label in _BUCKET_SOURCES:
        raw = info.get(key)
        if not isinstance(raw, dict) or not raw:
            continue
        total = _num(raw.get("total", raw.get("cap")))
        used = _num(raw.get("used"))
        if total is None and used is None:
            continue
        remaining = _num(raw.get("remaining"))
        if remaining is None and total is not None and used is not None:
            remaining = total - used
        buckets.append({
            "source": key,
            "label": label,
            "unit": str(raw.get("unit") or "credits"),
            "used": used,
            "total": total,
            "remaining": remaining,
            "percentage": _num(raw.get("percentage")),
            "available": raw.get("available") is not False,
            "detail_url": str(raw.get("detailUrl") or ""),
        })
    return buckets


async def _fetch_from_cli() -> dict:
    """起一个隔离 CLI 子进程读账号配额（凭据/CLI 路径沿用当前生效执行层）。"""
    import anyio

    from backend.modules.mind.execution import service as layer_service
    from backend.modules.mind.execution.manager import get_execution_layer_manager
    # _sdk_cli_path 是"执行层 CLI 唯一入口"的钉死逻辑（防短路到宿主机旧 Global CLI），
    # 复用而非另写一套路径解析。
    from backend.modules.mind.execution.secure_sdk import _sdk_cli_path, resolve_access_token

    try:
        layers = [r for r in layer_service.list_layers()
                  if r.get("status") == "active" and (r.get("config") or {}).get("mode") == "sdk"]
    except Exception as e:  # noqa: BLE001 — 读配置失败也要变成对用户可见的原因
        logger.exception("读取执行层配置失败")
        return {"available": False, "reason": f"读取执行层配置失败：{e}"}
    if not layers:
        return {"available": False, "reason": "当前无启用的 Qoder SDK 执行层，无法读取账号配额"}

    layer = next((r for r in layers if r.get("is_default")), layers[0])
    try:
        adapter = get_execution_layer_manager().build_adapter(layer)
    except Exception as e:  # noqa: BLE001
        logger.exception("构建执行层适配器失败: %s", layer.get("name"))
        return {"available": False, "reason": f"执行层「{layer.get('name')}」不可用：{e}"}

    config = getattr(adapter, "config", None) or {}
    token = resolve_access_token(config.get("env") or {})
    if not token:
        return {"available": False, "reason": "执行层凭据未配置（QODERCN_PERSONAL_ACCESS_TOKEN）"}

    home = tempfile.mkdtemp(prefix="adh-quota-")
    cli_path = "(未解析)"   # 供 except 分支引用，避免在赋值前抛错时 NameError 掩盖真因
    try:
        from qodercn_agent_sdk import QoderAgentOptions, QoderSDKClient
        from qodercn_agent_sdk.auth import AccessTokenAuthOptions

        # 先解析 CLI 路径：失败时要把“是哪个可执行文件”带进步骤原因（SDK 的 ProcessError
        # 只会回 “exit code 1 / Check stderr”，不标路径无法定位）。
        cli_path = _sdk_cli_path(adapter)
        env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
        env.update({"HOME": home, "XDG_CONFIG_HOME": home, "QODERCN_CONFIG_DIR": home + "/qoder-cn"})
        options = QoderAgentOptions(
            tools=[], allowed_tools=[], permission_mode="default", setting_sources=[],
            cwd=home, add_dirs=[], env=env, mcp_servers={}, agents={},
            system_prompt="quota probe", max_turns=1, include_partial_messages=False,
            cli_path=cli_path, auth=AccessTokenAuthOptions(access_token=token),
            strict_mcp_config=True, allowed_mcp_server_names=[], skills=[],
        )
        info: Any = None
        with anyio.move_on_after(_CLI_TIMEOUT_SECONDS) as scope:
            async with QoderSDKClient(options=options) as client:
                info = await client.get_usage_info()
        if scope.cancelled_caught:
            return {"available": False,
                    "reason": f"读取超时（>{int(_CLI_TIMEOUT_SECONDS)}s）：执行层 CLI 未响应"}
    except FileNotFoundError as e:
        return {"available": False, "reason": f"执行层 CLI 缺失：{e}"}
    except Exception as e:  # noqa: BLE001 — 显式暴露原因，不静默返回空配额
        logger.exception("读取 Qoder 账号配额失败")
        return {"available": False,
                "reason": f"执行层 CLI({cli_path}) 启动/握手失败：{type(e).__name__}: {e}；"
                          f"完整 stderr 见服务端日志"}
    finally:
        shutil.rmtree(home, ignore_errors=True)

    if not isinstance(info, dict) or not info:
        return {"available": False, "reason": "执行层未返回账号用量信息（访问令牌可能已失效）"}
    buckets = _normalize_buckets(info)
    if not buckets:
        return {"available": False, "reason": "执行层返回的用量信息里没有可用的 credit 配额桶"}
    expires_at = _num(info.get("expiresAt"))
    return {
        "available": True,
        "layer": layer.get("name"),
        "user_type": info.get("userType") or "",
        "unit": buckets[0]["unit"],
        "buckets": buckets,
        "total_usage_percentage": _num(info.get("totalUsagePercentage")),
        "is_quota_exceeded": bool(info.get("isQuotaExceeded")),
        "expires_at": (datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc)
                       .strftime("%Y-%m-%d %H:%M:%S") if expires_at else ""),
        "upgrade_url": info.get("upgradeUrl") or "",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _cache():
    from backend.common.ttl_cache import TTLCache
    return TTLCache(name="qoder_quota", maxsize=1, ttl=QUOTA_TTL_SECONDS)


async def read_quota(*, refresh: bool = False) -> dict:
    """读取账号 credit 配额（带 TTL 缓存；失败短缓存避免反复起子进程）。"""
    cache = _cache()
    key = "account"
    if not refresh:
        hit = cache.get(key)
        if isinstance(hit, dict) and hit:
            return {**hit, "cached": True}
    payload = await _fetch_from_cli()
    payload["cached"] = False
    try:
        cache.set(key, payload,
                  ttl=QUOTA_TTL_SECONDS if payload.get("available") else QUOTA_FAIL_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001 — 缓存不可用只意味着下次回源，不影响本次结果
        logger.debug("qoder_quota 缓存写入失败: %s", e)
    return payload
