"""外部 MCP 的显式逐工具代理；stdio 仅在当前会话容器中运行。"""
import asyncio
from contextlib import asynccontextmanager
import json

from services.shared.common.db import execute_query
from services.datamind.execution.sdk_tools.compat import make_tool, make_server


def load_server(server_id, policy):
    """资源引用来自已解析的 Waker，工作空间不再单独绑定 MCP。"""
    if (server_id not in policy.waker.get("mcp_server_ids", [])
            or not policy.external.get(str(server_id))):
        raise PermissionError("当前 Waker 未绑定该 MCP 或未授权工具")
    row = execute_query(
        "SELECT s.* FROM adh_mcp_servers s WHERE s.id=%s AND s.is_active=1",
        (server_id,), fetchone=True,
    )
    if not row:
        raise PermissionError("Waker 绑定的 MCP 服务不存在或已停用")
    return row


@asynccontextmanager
async def connection(row, ctx, runtime):
    from mcp import ClientSession
    transport = row["transport"]
    container = None
    if transport == "stdio":
        if "read" not in runtime.policy.standard:
            raise PermissionError("stdio MCP 可读取会话文件，必须显式授权读取")
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from services.aiplatform.services.sandbox_executor import SessionToolSandbox
        container, command = SessionToolSandbox.command(runtime, "read")
        runtime.sandbox_used = True
        entrypoint = row.get("command")
        args = row.get("args") or []
        if isinstance(args, str):
            args = json.loads(args)
        if not entrypoint or not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            raise ValueError("MCP 命令配置无效")
        if row.get("env") not in (None, "", "{}", {}):
            raise ValueError("会话 stdio MCP 不接受宿主环境注入，请使用专用镜像内配置")
        command = command[:-1] + ["--entrypoint", entrypoint, command[-1], *args]
        manager = stdio_client(StdioServerParameters(command=command[0], args=command[1:], env={}))
    elif transport in ("http", "streamable_http", "sse"):
        raise PermissionError("远程 MCP 尚未接入可验证的可信身份与数据治理契约，当前不可用")
    else:
        raise ValueError("MCP 传输协议不受支持")
    try:
        async with manager as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session
    finally:
        if container:
            from services.aiplatform.services.sandbox_executor import SessionToolSandbox
            try:
                await SessionToolSandbox.stop(container)
            except BaseException:
                runtime.unsafe = True
                raise


async def discover(row, ctx, runtime):
    async with connection(row, ctx, runtime) as session:
        result = await session.list_tools()
        if getattr(result, "nextCursor", None):
            raise ValueError("MCP 工具目录分页暂不支持，请限制服务工具目录")
        return {t.name: t for t in result.tools}


async def build_external_servers(backend, ctx, runtime):
    servers = {}
    for sid, selected in runtime.policy.external.items():
        if not selected:
            continue
        row = await asyncio.to_thread(load_server, int(sid), runtime.policy)
        import anyio
        with anyio.fail_after(30):
            tools_by_name = await discover(row, ctx, runtime)
        if set(selected) - set(tools_by_name):
            raise ValueError("已授权的自定义工具不在 MCP 实际目录中")
        tools = []
        for name in selected:
            spec = tools_by_name[name]
            schema = spec.inputSchema
            runtime.policy.descriptions[f"mcp__external_{sid}__{name}"] = spec.description or name
            snapshot = {k: row.get(k) for k in ("transport", "url", "command", "args", "env")}

            async def handler(args, name=name, sid=sid, schema=schema, snapshot=snapshot):
                live = await asyncio.to_thread(load_server, int(sid), runtime.policy)
                if {k: live.get(k) for k in snapshot} != snapshot:
                    raise PermissionError("MCP 服务配置已变化，请重新发送消息")
                async with connection(live, ctx, runtime) as session:
                    catalog = await session.list_tools()
                    current = next((t for t in catalog.tools if t.name == name), None)
                    if getattr(catalog, "nextCursor", None) or current is None or current.inputSchema != schema:
                        raise PermissionError("MCP 工具目录已变化，请重新建立会话工具配置")
                    result = await session.call_tool(name, args)
                    return result.model_dump(exclude_none=True)

            tools.append(make_tool(backend, name, spec.description or name, schema, handler,
                                   qualified_name=f"mcp__external_{sid}__{name}"))
        server_name = f"external_{sid}"
        servers[server_name] = make_server(backend, server_name, tools)
    return servers
