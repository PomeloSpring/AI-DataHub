"""平台内置 MCP 服务的注册表 —— 执行侧与展示侧的单一事实源。

为什么需要这一层：内置服务在 AS-BOT 里与外部 MCP 服务**同一个入口配置**（「引用 MCP 服务」
绑定 + 逐工具授权），因此执行侧必须能从 `adh_mcp_servers` 的内置行分派出进程内 handler；
而展示侧（MCP 服务页 / 工具目录）必须给出一致的名称、描述与就绪状态。两边各自维护一份就会
漂移（出现"页面说有、执行说不认"）。

内置行与外部行的边界（安全约定）：
- `adh_mcp_servers` 里的内置行 **不持有连接配置**：url/command/args/env 恒为空，
  因此任何响应体与页面都没有配置字段可泄（护栏 §7）。
- 工具经 `compat.make_tool` 注册，与平台内工具组同一条收口：真实身份、
  `check_tool` 授权、`outbound_guard` 出站检查。
- 库里存在内置行但代码未登记实现：`build_builtin_server` 显式拒，不能"当没这个服务"静默跳过
  （否则就是页面勾了、执行时模型拿不到工具的空承诺）。

当前注册表为空（2026-11-13 阶段三）：唯一的内置服务 `elasticsearch` 已退役 —— ES 现在是
**正式数据源**（`adh_datasources.db_type='es'` + `extra_config` 开放范围），取数一律过治理执行口
（角色源授权 + RLS/列级 + 审计，护栏 §1）；原先那三个不过治理口的裸 SQL/元数据工具
（`list_es_indexes`/`describe_es_index`/`query_es_sql`）随之下线，ES 的探索与取数统一走
`list_datasources` + `search_metadata`/`get_table_schema` + `check_sql`/`execute_sql`。
机制本身保留（将来确实需要“凭据由部署侧托管、不进库”的内置服务时直接往这里登记）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 内置服务的 transport 取值（DB 行用它标识自己，执行侧据此跳过远程连接）
BUILTIN_TRANSPORT = "builtin"


# key = adh_mcp_servers.name（不是自增 id：删除重建 id 会变，name 不变）
#
# 登记表为空 = 当前没有内置 MCP 服务（口径见模块头）。每个成员必须提供：
#   label / description / tool_specs() / state(config) / probe()
#   —— `state` 只给结论与业务可读原因，绝不回显配置值；未就绪时 `probe` 不得去碰外部系统。
BUILTIN_MCP_SERVICES: dict[str, dict[str, Any]] = {}


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
