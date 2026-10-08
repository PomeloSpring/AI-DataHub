"""标准工具的受控实现，不向模型暴露宿主机原生工具。"""
import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urljoin

from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool


async def fetch_public_url(url):
    import aiohttp
    from aiohttp.abc import AbstractResolver

    class PublicResolver(AbstractResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            result = []
            for fam, _, _, _, address in rows:
                ip = ipaddress.ip_address(address[0])
                if not ip.is_global:
                    raise PermissionError("禁止访问本机、私网及云元数据地址")
                result.append({"hostname": host, "host": str(ip), "port": port, "family": fam,
                               "proto": 0, "flags": socket.AI_NUMERICHOST})
            if not result:
                raise ValueError("网页域名无法解析")
            return result

        async def close(self):
            pass

    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                    timeout=aiohttp.ClientTimeout(total=20)) as client:
        for _ in range(5):
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise PermissionError("仅允许无凭据的 HTTP/HTTPS 网页地址")
            if parsed.port not in (None, 80, 443):
                raise PermissionError("网页抓取仅允许标准端口")
            try:
                ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                ip = None
            if ip and not ip.is_global:
                raise PermissionError("禁止访问本机、私网及云元数据地址")
            async with client.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("网页重定向缺少目标")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                data = bytearray()
                while len(data) <= 100_000:
                    chunk = await response.content.read(100_001 - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                return {"text": data[:100_000].decode("utf-8", errors="replace"), "truncated": len(data) > 100_000}
        raise ValueError("网页重定向次数过多")


SPECS = {
    "read": ("读取当前会话的文本文件", {"path": str}),
    "write": ("写入当前会话文件", {"path": str, "content": str}),
    "edit": ("唯一匹配后编辑当前会话文件", {"path": str, "old_string": str, "new_string": str}),
    "glob": ("在当前会话中按模式检索文件", {"path": str, "pattern": str}),
    "grep": ("在当前会话中搜索文件内容", {"path": str, "pattern": str}),
    "bash": ("在隔离且无网络的当前会话沙箱中执行命令", {"command": str}),
    "webfetch": ("抓取公网网页，禁止私网、本机与元数据地址", {"url": str}),
}


def build_workspace_server(backend, runtime):
    if "task" in runtime.policy.standard:
        raise ValueError("当前子代理隔离契约尚未验证，暂不能启用子任务；请移除该授权")
    tools = []
    for name in runtime.policy.standard:
        description, schema = SPECS[name]

        async def handler(args, name=name):
            if name == "webfetch":
                result = await fetch_public_url(args["url"])
            else:
                from backend.modules.platform.services.sandbox_executor import SessionToolSandbox
                result = await SessionToolSandbox.run(runtime, name, args)
            return {"isError": name == "bash" and result.get("exit_code", 0) != 0,
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

        tools.append(make_tool(backend, name, description, schema, handler,
                               qualified_name=f"mcp__datahub_workspace__{name}"))
    return make_server(backend, "datahub_workspace", tools)
