"""平台内置 MCP 服务的注册表 —— 执行侧与展示侧的单一事实源。

为什么需要这一层：内置服务（如 Elasticsearch）在 AS-BOT 里与外部 MCP 服务**同一个
入口配置**（「引用 MCP 服务」绑定 + 逐工具授权），因此执行侧必须能从 `adh_mcp_servers`
的内置行分派出进程内 handler；而展示侧（MCP 服务页 / 工具目录）必须给出一致的名称、
描述与就绪状态。两边各自维护一份就会漂移（出现"页面说有、执行说不认"）。

内置行与外部行的边界（安全约定）：
- `adh_mcp_servers` 里的内置行 **不持有连接配置**：url/command/args/env 恒为空，
  真实地址与凭据只在 `backend/common/config.py`（由 backend/.env 读取），
  因此任何响应体与页面都没有配置字段可泄（护栏 §7）。
- 工具经 `compat.make_tool` 注册，与平台内工具组同一条收口：真实身份、
  `check_tool` 授权、`outbound_guard` 出站检查。
- 未开启/未配齐的内置服务在目录里显示为不可用并给出原因，不做"可勾但必错"的死开关。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 内置服务的 transport 取值（DB 行用它标识自己，执行侧据此跳过远程连接）
BUILTIN_TRANSPORT = "builtin"


def _es_tool_specs() -> list[dict[str, Any]]:
    from backend.modules.mind.execution.sdk_tools.es_tools import TOOL_SPECS
    return TOOL_SPECS


def _es_state(config) -> dict[str, Any]:
    """Elasticsearch 就绪状态：只给结论与业务可读原因，不回显任何配置值。

    开放单位是**索引模式**（data view，如 `logs-*`），不是具体索引；模式名属部署配置，
    因此对外只给数量（护栏 §7：页面不明文显示配置）。
    """
    patterns = list(config.ES_ALLOWED_INDEXES or ())
    if not config.ES_MCP_ENABLED:
        return {"ready": False, "state": "disabled",
                "status_message": "未启用（部署侧开关关闭）", "open_view_count": len(patterns)}
    if not config.ES_URL:
        return {"ready": False, "state": "incomplete",
                "status_message": "已启用但缺少连接配置，工具调用会提示配置不完整",
                "open_view_count": len(patterns)}
    if not patterns:
        return {"ready": False, "state": "incomplete",
                "status_message": "已启用但未开放任何索引范围（白名单为空，默认不开放）",
                "open_view_count": 0}
    return {"ready": True, "state": "ready", "status_message": "",
            "open_view_count": len(patterns)}


def _es_health() -> dict[str, Any]:
    from backend.core import es_client
    return es_client.health()


def _es_probe() -> dict[str, Any]:
    """Elasticsearch 连通性探测：返回 {success, message}，文案不含地址/凭据/集群标识。

    未就绪时**不去碰集群**：否则管理员在“未启用”状态下看到的是“服务不可达”，
    把配置缺口误判成网络故障（错因要可诊断，不能靠猜）。
    错因加工由本函数消化（EsError 的文案按约定就是业务可读的），
    使 API 层不必引用具体异常类就能分派内置服务的探测。
    """
    from backend.common import config
    from backend.core import es_client

    state = _es_state(config)
    if not state["ready"]:
        return {"success": False, "message": state["status_message"] or "内置服务当前不可用"}
    try:
        info = _es_health()
    except es_client.EsError as exc:
        return {"success": False, "message": str(exc)}
    except Exception:  # noqa: BLE001 — 非预期异常只给通用文案，原始错因进日志
        logger.exception("[builtin-mcp] elasticsearch probe failed")
        return {"success": False, "message": "Elasticsearch 探测失败，请联系管理员查看服务端日志"}
    return {"success": True,
            "message": f"连接正常，已开放 {info['registered_scopes']} 个索引范围（按模式查询，不展开具体索引）"}


# key = adh_mcp_servers.name（不是自增 id：删除重建 id 会变，name 不变）
BUILTIN_MCP_SERVICES: dict[str, dict[str, Any]] = {
    "elasticsearch": {
        "label": "Elasticsearch 检索服务",
        "description": (
            "平台内置 Elasticsearch 只读查询能力（列开放索引范围 / 看字段 / 跑 SQL）。"
            "索引按时间分片，以模式（如 logs-*）为查询单位；访问范围由部署侧索引白名单限定，"
            "不参与列级/行级数据权限治理。"),
        "tool_specs": _es_tool_specs,
        "state": _es_state,
        # 内置行的“测试连接”走这里：不建远程连接，只探测并返回业务可读结果
        "probe": _es_probe,
    },
}


def resolve(name: Any) -> Optional[dict[str, Any]]:
    """按 DB 行的 name 取内置服务定义；未登记实现时返回 None（调用方 fail-loud）。"""
    return BUILTIN_MCP_SERVICES.get(str(name or "").strip())


def is_builtin_row(row: dict[str, Any]) -> bool:
    if str((row or {}).get("transport") or "").strip().lower() == BUILTIN_TRANSPORT:
        return True
    return bool((row or {}).get("is_builtin"))


def state_of(name: Any) -> dict[str, Any]:
    """内置服务的运行时就绪状态（配置事实来自 .env，每次现读，不缓存）。"""
    spec = resolve(name)
    if not spec:
        return {"ready": False, "state": "unknown", "status_message": "内置服务未登记实现",
                "open_view_count": 0}
    from backend.common import config
    return spec["state"](config)


def catalog() -> list[dict[str, Any]]:
    """内置服务清单（展示侧用）：名称/描述/工具名 + 就绪状态，无连接配置字段。"""
    out = []
    for key, spec in BUILTIN_MCP_SERVICES.items():
        st = state_of(key)
        out.append({
            "key": key,
            "name": key,
            "label": spec["label"],
            "description": spec["description"],
            "builtin": True,
            "transport": BUILTIN_TRANSPORT,
            "state": st["state"],
            "status_message": st["status_message"],
            "tools": [{"name": s["name"], "description": s["description"]}
                      for s in spec["tool_specs"]()],
            "open_view_count": st["open_view_count"],
        })
    return out


def build_builtin_server(backend: str, row: dict[str, Any],
                         server_name: str, qualified_prefix: str,
                         selected: list[str]):
    """把内置服务的进程内 handler 注册成 MCP server（供 external 授权链路复用）。

    Args:
        row: adh_mcp_servers 的内置行（只用 name 定位实现，不读任何连接字段）。
        server_name: SDK 侧 server 名，须与授权名前缀一致。
        qualified_prefix: 形如 `mcp__external_{sid}`，与 tool_policy 生成的授权名同源。
        selected: AS-BOT 勾选的工具名（逐工具授权结果）。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    spec = resolve(row.get("name"))
    if not spec:
        # 内置行存在但没有实现登记：显式拒绝，不能"当没这个服务"静默跳过
        raise PermissionError(f"内置 MCP 服务「{row.get('name')}」未登记实现，请联系管理员")

    by_name = {s["name"]: s for s in spec["tool_specs"]()}
    missing = [n for n in selected if n not in by_name]
    if missing:
        raise ValueError(f"已授权的内置工具不在服务目录中: {', '.join(missing)}")

    tools = [
        make_tool(backend, name, by_name[name]["description"], by_name[name]["schema"],
                  by_name[name]["handler"], annotations={"readOnlyHint": True},
                  qualified_name=f"{qualified_prefix}__{name}")
        for name in selected
    ]
    return make_server(backend, server_name, tools)
