"""MCP Servers API — Manage MCP server configurations.

Migrated from backend/api/admin.py (mcp-servers section)
Table: adh_mcp_servers
"""

import json
import logging

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, model_validator
from backend.common.auth import get_current_user, require_admin

from backend.common.db import DBConnection, execute_query, execute_insert, execute_write
from backend.common.versioned_config import get_mcp_version_store

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_current_user)])


class MCPServerPayload(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_workspace_binding(cls, data):
        if isinstance(data, dict) and {"workspace_id", "workspace_ids"}.intersection(data):
            raise ValueError("MCP 使用范围请在 AS-BOT 中绑定，不再支持工作空间绑定")
        return data


class MCPServerCreate(MCPServerPayload):
    name: str
    transport: str = "sse"
    url: str = ""
    command: str = ""
    args: list = []
    env: dict = {}
    tools_config: dict = {}
    description: str = ""
    is_active: bool = True
    created_by: str = "system"


class MCPServerUpdate(MCPServerPayload):
    name: Optional[str] = None
    transport: Optional[str] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list] = None
    env: Optional[dict] = None
    tools_config: Optional[dict] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    change_log: str = "Updated"
    updated_by: str = "system"


class MCPRollbackRequest(BaseModel):
    version: int
    rolled_by: str = "system"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _project_builtin(row: dict) -> dict:
    """内置服务行的响应投影：只给业务可读信息与就绪状态，**剥光配置字段**。

    库里内置行本就不存 url/command/args/env（地址与凭据只在 backend/.env），
    这里再弹一次是防止历史脏数据或误填把凭据带进页面（护栏 §7）。
    """
    from backend.modules.mind.execution import builtin_mcp

    name = row.get("name")
    spec = builtin_mcp.resolve(name)
    state = builtin_mcp.state_of(name)
    for key in ("url", "command", "args", "env", "tools_config"):
        row.pop(key, None)
    row["builtin"] = True
    row["label"] = (spec or {}).get("label") or name
    row["state"] = state["state"]
    row["status_message"] = state["status_message"]
    # 只给数量（已开放几个索引范围）：模式名属部署配置，不回显。
    # 有数量无名单是故意的：管理员要能判断“配了没配”，但不能在页面上读到配置本身。
    row["open_view_count"] = state["open_view_count"]
    # 未就绪时对外呈现为不可用：AS-BOT 的候选列表按 is_active 过滤，
    # 不会出现“能绑上但每次调用必报错”的假承诺；原因在 MCP 服务页可见。
    row["is_active"] = 1 if state["ready"] else 0
    return row


def update_builtin_test_result(server_id: int, probe: dict) -> None:
    """内置服务只回写测试状态，**不回写 discovered_tools**。

    外部服务的 update_test_result 在失败时会把工具清单清成空串；内置行的清单来自
    代码登记（迁移写入），被清后 AS-BOT 的逐工具授权就渲不出工具 —— 那等于把
    “探测一下”做成了破坏性写，所以内置行走本函数。
    """
    execute_write(
        "UPDATE adh_mcp_servers SET last_test_at=%s, last_test_status=%s, last_test_message=%s "
        "WHERE id=%s AND is_builtin=1",
        (_now(), "success" if probe.get("success") else "failed",
         str(probe.get("message") or "")[:500], server_id))


@router.get("/")
def list_mcp_servers(workspace_id: Optional[int] = Query(None), user: dict = Depends(get_current_user)):
    """系统管理目录或已授权 AS-BOT 的资源投影，不构成执行授权。"""
    try:
        if workspace_id is not None:
            from backend.modules.mind.execution.as_bots import visible_resource_ids
            ids = visible_resource_ids(user, workspace_id, "mcp_server_ids")
            if not ids:
                return []
            marks = ','.join(['%s'] * len(ids))
            rows = execute_query(f"SELECT * FROM adh_mcp_servers WHERE id IN ({marks}) AND is_active=1 ORDER BY name", tuple(ids))
        else:
            if user.get("role") != "admin":
                raise HTTPException(403, "系统 MCP 目录仅管理员可访问，请选择已授权工作空间")
            rows = execute_query("SELECT * FROM adh_mcp_servers ORDER BY name")

        for r in rows:
            for field in ("args", "env", "tools_config", "discovered_tools"):
                if isinstance(r.get(field), str):
                    try:
                        r[field] = json.loads(r[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            for k in ("created_at", "updated_at", "last_test_at"):
                if hasattr(r.get(k), "isoformat"):
                    r[k] = r[k].isoformat()
        from backend.modules.mind.execution import builtin_mcp
        if user.get("role") != "admin":
            keep = ("id", "name", "description", "transport", "is_active")
            out = []
            for r in rows:
                if builtin_mcp.is_builtin_row(r):
                    item = _project_builtin(dict(r))
                    out.append({k: item[k] for k in keep + ("builtin", "label") if k in item})
                else:
                    out.append({k: r[k] for k in keep if k in r})
            return out
        return [_project_builtin(r) if builtin_mcp.is_builtin_row(r) else
                {k: v for k, v in r.items() if k != "workspace_id"}
                for r in rows]
    except HTTPException:
        raise
    except Exception:
        logger.exception("MCP 目录加载失败")
        raise HTTPException(status_code=503, detail="MCP 目录暂不可用，请稍后重试") from None





def _reject_builtin_mutation(server_id: int, verb: str) -> None:
    """内置服务的名称/描述/工具清单来自代码与迁移，页面不给改也不给删。

    不是"静默忽略"：显式 409 并告知配置托管位置，否则管理员会以为已经改成功了。
    """
    from backend.modules.mind.execution import builtin_mcp

    row = execute_query(
        "SELECT name, transport, is_builtin FROM adh_mcp_servers WHERE id = %s",
        (server_id,), fetchone=True)
    if row and builtin_mcp.is_builtin_row(row):
        raise HTTPException(
            409,
            f"平台内置 MCP 服务不支持{verb}：能力定义与工具清单由系统维护，"
            "连接配置由部署侧托管（不显示在本页面）；"
            "启用范围请在 AS-BOT 绑定与逐工具授权中控制。")


@router.get("/builtin")
def list_builtin_mcp_services(user: dict = Depends(get_current_user)):
    """内置服务的运行态清单（状态/原因/工具）：与主列表同源，供需要只看内置的调用方使用。

    路由声明顺序有意放在 /{server_id} 之前：否则 "builtin" 会被当 server_id 转 int 报 422。
    """
    from backend.modules.mind.execution import builtin_mcp
    return builtin_mcp.catalog()


@router.get("/{server_id}", dependencies=[Depends(require_admin)])
def get_mcp_server(server_id: int):
    """Get MCP server by ID."""
    try:
        row = execute_query(
            "SELECT * FROM adh_mcp_servers WHERE id = %s",
            (server_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

        from backend.modules.mind.execution import builtin_mcp
        if builtin_mcp.is_builtin_row(row):
            return _project_builtin(dict(row))

        for field in ("args", "env", "tools_config", "discovered_tools"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        for k in ("created_at", "updated_at", "last_test_at"):
            if hasattr(row.get(k), "isoformat"):
                row[k] = row[k].isoformat()
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", dependencies=[Depends(require_admin)])
def create_mcp_server(req: MCPServerCreate):
    """Create a new MCP server (records an initial v1 snapshot)."""
    # 内置服务只能由迁移登记：允许页面"新建 builtin"等于允许伪造一个
    # 无实现、无收口的服务入口（执行侧会拒，但配了没用的假承诺不应由页面造成）。
    if str(req.transport or "").strip().lower() == "builtin":
        raise HTTPException(409, "平台内置 MCP 服务由系统登记，不支持在页面新建")
    try:
        store = get_mcp_version_store()
        server_id = store.create(
            {
                "name": req.name,
                "transport": req.transport,
                "url": req.url,
                "command": req.command,
                "args": req.args,
                "env": req.env,
                "tools_config": req.tools_config,
                "description": req.description,
                "is_active": 1 if req.is_active else 0,
            },
            created_by=req.created_by,
        )
        return {"id": server_id, "success": True}
    except Exception as e:
        logger.error("Create MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{server_id}", dependencies=[Depends(require_admin)])
def update_mcp_server(server_id: int, req: MCPServerUpdate):
    """Update MCP server. Automatically creates a version snapshot."""
    _reject_builtin_mutation(server_id, "修改")
    try:
        store = get_mcp_version_store()
        data = {}
        for field in ("name", "transport", "url", "command", "args",
                      "env", "tools_config", "description"):
            val = getattr(req, field, None)
            if val is not None:
                data[field] = val
        if req.is_active is not None:
            data["is_active"] = 1 if req.is_active else 0

        if not data:
            return {"success": True, "message": "No changes"}
        data["change_log"] = req.change_log

        ok = store.update(server_id, data, updated_by=req.updated_by)
        if not ok:
            raise HTTPException(status_code=404, detail="MCP server not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_id}", dependencies=[Depends(require_admin)])
def delete_mcp_server(server_id: int):
    """Delete MCP server."""
    _reject_builtin_mutation(server_id, "删除")
    try:
        execute_write("DELETE FROM adh_mcp_servers WHERE id = %s", (server_id,))
        return {"success": True}
    except Exception as e:
        logger.error("Delete MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_id}/test", dependencies=[Depends(require_admin)])
def test_mcp_server(server_id: int):
    """真实连接测试：连接 → 初始化 → 工具发现；结果与发现的工具清单落库。"""
    try:
        row = execute_query(
            "SELECT * FROM adh_mcp_servers WHERE id = %s",
            (server_id,),
            fetchone=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

        from backend.modules.mind.execution import builtin_mcp
        if builtin_mcp.is_builtin_row(row):
            # 内置服务不建远程连接：探测由注册表自己负责（结果不回集群标识）
            spec = builtin_mcp.resolve(row.get("name"))
            probe = spec["probe"]() if spec and spec.get("probe") else {
                "success": False, "message": "内置服务未登记实现，请联系管理员"}
            update_builtin_test_result(server_id, probe)
            return probe

        args = row.get("args") or []
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else []
            except (json.JSONDecodeError, TypeError):
                args = [a.strip() for a in args.split(",") if a.strip()]
        env = row.get("env") or {}
        if isinstance(env, str):
            try:
                env = json.loads(env) if env.strip() else {}
            except (json.JSONDecodeError, TypeError):
                env = {}
        transport = row.get("transport") or "sse"
        if transport == "http":
            transport = "streamable_http"

        import asyncio

        from backend.modules.platform.services.mcp_server_service import update_test_result
        from backend.mcp_client.client import MCPClient

        client = MCPClient(
            server_id=row["id"],
            name=row["name"],
            transport=transport,
            url=row.get("url") or "",
            command=row.get("command") or "",
            args=args,
            env=env,
        )
        result = asyncio.run(client.test_connection())
        # 成败均落库（last_test_* + discovered_tools），供前端展示真实状态与工具清单。
        update_test_result(
            server_id,
            bool(result.get("success")),
            result.get("message", ""),
            result.get("tools") or [],
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Test MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Version history & rollback ─────────────────────────────────────────

@router.get("/{server_id}/versions", dependencies=[Depends(require_admin)])
def list_mcp_versions(server_id: int):
    """List version snapshots for an MCP server."""
    try:
        store = get_mcp_version_store()
        versions = store.get_versions(server_id)
        for v in versions:
            content = v.get("content")
            if isinstance(content, str):
                try:
                    v["content"] = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    pass
            for k in ("created_at",):
                if hasattr(v.get(k), "isoformat"):
                    v[k] = v[k].isoformat()
        return versions
    except Exception as e:
        logger.error("List MCP versions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_id}/rollback", dependencies=[Depends(require_admin)])
def rollback_mcp_server(server_id: int, req: MCPRollbackRequest):
    """Rollback an MCP server config to a specific version."""
    try:
        store = get_mcp_version_store()
        ok = store.rollback(server_id, req.version, rolled_by=req.rolled_by)
        if not ok:
            raise HTTPException(status_code=404, detail="MCP server or target version not found")
        return {"success": True, "message": f"Rolled back to version {req.version}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rollback MCP server failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
