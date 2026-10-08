"""assets 工具组 — 用户资产清单与磁盘治理（LLM 可感知并与用户交互）。

资产清单跟随用户(跨工作空间/跨会话)：会话产物可"归档收藏"进清单（OSS 托管），
一旦归档跟着用户走；本地产物受工作空间磁盘配额限制，超限会拒绝新建会话。

交互契约（写进工具描述，要求 LLM 遵守）：
- 产物可归档收藏供后续会话复用；本地磁盘受配额限制，用量高时应主动建议用户归档/清理。
- 归档与清理是"两步确认"动作：先用 confirm=false 列出将影响的文件/目录并与用户确认，
  用户同意后才以 confirm=true 执行；严禁未经确认直接删除。
"""

import logging
from typing import Annotated, Optional

from services.datamind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)


def _ctx_ids():
    """当前会话上下文 (user_id, workspace_id, conversation_id); 无上下文返回 (0,0,0)。"""
    from services.datamind.execution.sdk_tools.context import get_execution_context
    ctx = get_execution_context()
    if not ctx:
        return 0, 0, 0
    return (int(ctx.user_id or 0), int(ctx.workspace_id or 0),
            int((ctx.extra or {}).get("conversation_id") or 0))


async def asset_list(args):
    """列出我的资产清单（只读, 跟随用户跨工作空间）。"""
    uid, _, _ = _ctx_ids()
    if not uid:
        return _text({"error": "缺少用户上下文"}, is_error=True)
    from services.datamind.api.user_assets import list_user_assets
    try:
        items = list_user_assets(uid)
    except Exception as e:  # noqa: BLE001
        return _text({"error": f"资产清单不可用: {e}"}, is_error=True)
    return _text({"count": len(items), "assets": items,
                  "note": "资产清单跟随用户(跨工作空间/跨会话)，托管在对象存储；download 资产请告知用户在「资产清单」页获取。"})


async def asset_get(args):
    """读取资产内容（文本类资产直接返回，二进制只返回元信息）。"""
    uid, _, _ = _ctx_ids()
    asset_id = (args.get("asset_id") or "").strip()
    if not uid or not asset_id:
        return _text({"error": "缺少 asset_id 或用户上下文"}, is_error=True)
    from services.shared.common.db.metadata_db import get_metadata_conn
    from services.shared.common.object_storage import get_object_storage
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, filename, object_key, size, category FROM adh_user_assets "
                "WHERE id=%s AND user_id=%s", (asset_id, uid))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return _text({"error": "资产不存在"}, is_error=True)
    data = get_object_storage().download_bytes(row["object_key"])
    if data is None:
        return _text({"error": "资产内容不可用"}, is_error=True)
    try:
        text = data.decode("utf-8")
        return _text({"name": row["name"], "filename": row["filename"], "content": text[:20000],
                      "truncated": len(text) > 20000})
    except UnicodeDecodeError:
        return _text({"name": row["name"], "filename": row["filename"], "size": row["size"],
                      "category": row["category"], "note": "二进制资产，请在「资产清单」页下载"})


async def asset_archive(args):
    """归档会话产物到资产清单（两步确认：先预览，用户同意后再执行）。

    confirm=false（默认）返回将归档的文件预览；confirm=true 执行归档。
    归档后可 delete_source=true 清理本地产物以节省磁盘配额（资产保存在对象存储不受影响）。
    """
    uid, _, cid = _ctx_ids()
    path = (args.get("path") or "").strip()
    name = (args.get("name") or "").strip()
    confirm = bool(args.get("confirm"))
    delete_source = bool(args.get("delete_source"))
    if not uid or not path:
        return _text({"error": "缺少 path 或用户上下文"}, is_error=True)
    conversation_id = int(args.get("conversation_id") or cid or 0)
    if not conversation_id:
        return _text({"error": "缺少 conversation_id（请指定要归档哪个会话的产物）"}, is_error=True)
    from services.datamind.api.user_assets import list_session_files, archive_session_file
    from services.shared.common.db.metadata_db import get_metadata_conn
    # 会话属主定位(不绑定工作空间, 资产跟人走)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT workspace_id FROM adh_agent_sessions "
                        "WHERE conversation_id=%s AND user_id=%s", (conversation_id, uid))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return _text({"error": "会话不存在或无权访问"}, is_error=True)
    try:
        files = list_session_files(row["workspace_id"], conversation_id, uid)
    except Exception as e:  # noqa: BLE001
        return _text({"error": f"会话产物不可用: {e}"}, is_error=True)
    match = next((f for f in files if f["path"] == path), None)
    if not match:
        return _text({"error": f"会话产物中无 {path}", "available_files": files}, is_error=True)
    preview = {"path": path, "size": match["size"], "name": name or path.split("/")[-1],
               "conversation_id": conversation_id, "delete_source": delete_source}
    if not confirm:
        return _text({"confirm_required": True, "preview": preview,
                      "note": "请与用户确认归档该产物(及是否删除本地)后，以 confirm=true 再次调用"})
    try:
        asset = archive_session_file(uid, conversation_id, path, preview["name"],
                                     delete_source=delete_source)
    except Exception as e:  # noqa: BLE001
        return _text({"error": f"归档失败: {e}"}, is_error=True)
    return _text({"archived": asset, "deleted_source": delete_source})


async def disk_status_tool(args):
    """查看工作空间磁盘用量/配额与会话产物占用（只读）。

    用量接近配额时应主动建议用户归档收藏重要产物并清理本地文件。
    """
    uid, ws, _ = _ctx_ids()
    if not uid or not ws:
        return _text({"error": "缺少工作空间上下文"}, is_error=True)
    from services.datamind.api.user_assets import disk_status
    try:
        status = disk_status(ws)
    except Exception as e:  # noqa: BLE001
        return _text({"error": f"用量查询不可用: {e}"}, is_error=True)
    quota = status.get("quota_bytes") or 0
    usage = status.get("usage_bytes") or 0
    pct = round(usage / quota * 100, 1) if quota else None
    advice = ""
    if quota and pct is not None:
        if pct >= 90:
            advice = "磁盘接近上限：建议把重要产物归档收藏(asset_archive)后清理本地(cleanup_execute)"
        elif pct >= 70:
            advice = "磁盘用量偏高：可提醒用户归档不再需要的产物"
    return _text({**status, "usage_percent": pct, "advice": advice})


async def cleanup_candidates_tool(args):
    """列出本地产物清理候选（只读）：已归档资产的来源文件 + 已关闭会话目录。"""
    uid, ws, _ = _ctx_ids()
    if not uid or not ws:
        return _text({"error": "缺少工作空间上下文"}, is_error=True)
    from services.datamind.api.user_assets import _cleanup_candidates
    try:
        candidates = _cleanup_candidates(ws)
    except Exception as e:  # noqa: BLE001
        return _text({"error": f"清理候选不可用: {e}"}, is_error=True)
    return _text({"count": len(candidates), "candidates": candidates,
                  "note": "请向用户展示将删除的文件/会话目录并征得同意后，用 cleanup_execute(confirm=true) 执行；资产本体在对象存储不受影响。"})


async def cleanup_execute_tool(args):
    """清理本地产物（两步确认的第二步：必须 confirm=true 且应先征得用户同意）。"""
    uid, ws, _ = _ctx_ids()
    if not uid or not ws:
        return _text({"error": "缺少工作空间上下文"}, is_error=True)
    if not bool(args.get("confirm")):
        return _text({"confirm_required": True,
                      "note": "请先用 cleanup_candidates 列出候选并与用户确认后，以 confirm=true 调用"})
    from services.datamind.api.user_assets import _cleanup_candidates
    from services.datamind.execution.session_workspace import (
        session_paths, workspace_base, _remove_session_directory)
    from pathlib import Path
    freed = 0
    cleaned = 0
    for c in _cleanup_candidates(ws):
        try:
            if c["kind"] == "file":
                p = Path(c["path"])
                if p.is_file() and not p.is_symlink():
                    freed += p.stat().st_size
                    p.unlink()
                    cleaned += 1
            elif c["kind"] == "session":
                root = session_paths(workspace_base(create=False), c["session_key"], ws, create=False)
                freed += c.get("bytes") or 0
                _remove_session_directory(root)
                cleaned += 1
        except Exception as e:  # noqa: BLE001 — 单项失败不阻断
            logger.warning("[cleanup_execute] candidate failed: %s (%s)", c, e)
    return _text({"cleaned": cleaned, "freed_bytes": freed})


TOOL_SPECS = [
    {
        "name": "asset_list",
        "description": (
            "列出我的资产清单（跟随用户，跨工作空间/跨会话）：用户归档收藏的会话产物（OSS 托管），"
            "供所有会话引用复用。回答“我们之前收藏/归档过哪些成果”类问题用本工具。"
        ),
        "schema": {},
        "handler": asset_list,
    },
    {
        "name": "asset_get",
        "description": "读取资产内容（文本资产返回内容，二进制返回元信息）。引用清单中的历史成果回答问题时使用。",
        "schema": {
            "asset_id": Annotated[str, "资产 ID（来自 asset_list）"],
        },
        "handler": asset_get,
    },
    {
        "name": "asset_archive",
        "description": (
            "把会话产物归档收藏进我的资产清单（两步确认，资产跟随用户跨工作空间）。"
            "产物可归档收藏供后续会话复用；本地磁盘受配额限制，重要的产物应主动建议用户归档。"
            "首次调用 confirm=false 预览将归档的文件，与用户确认后再以 confirm=true 执行；"
            "delete_source=true 归档同时删除本地产物以节省磁盘。"
        ),
        "schema": {
            "path": Annotated[str, "会话产物相对路径（来自会话输出/文件列表）"],
            "name": Annotated[Optional[str], "资产名称（默认用文件名）"],
            "conversation_id": Annotated[Optional[int], "来源会话 ID（默认当前会话）"],
            "confirm": Annotated[bool, "false=预览待确认；true=执行归档（须先经用户同意）"],
            "delete_source": Annotated[Optional[bool], "归档后删除本地产物文件（默认否）"],
        },
        "handler": asset_archive,
    },
    {
        "name": "disk_status",
        "description": (
            "查看工作空间磁盘用量/配额与会话产物占用。用量接近上限时应主动建议用户"
            "归档重要产物并清理本地文件，避免新建会话被配额拦截。"
        ),
        "schema": {},
        "handler": disk_status_tool,
    },
    {
        "name": "cleanup_candidates",
        "description": "列出本地产物清理候选（已归档来源文件 + 已关闭会话目录，只读预览）。",
        "schema": {},
        "handler": cleanup_candidates_tool,
    },
    {
        "name": "cleanup_execute",
        "description": (
            "清理本地产物（两步确认的第二步）。必须先用 cleanup_candidates 列出候选并与用户确认，"
            "用户同意后以 confirm=true 执行；严禁未经确认直接删除。已归档资产保存在对象存储不受影响。"
        ),
        "schema": {
            "confirm": Annotated[bool, "false=不执行仅提示；true=执行清理（须先经用户同意）"],
        },
        "handler": cleanup_execute_tool,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}
_MUTATING = {"asset_archive", "cleanup_execute"}


def build_assets_server(backend: str = "qoder", tool_names=None):
    """构建 assets 进程内 MCP server（qoder / claude）。tool_names 给定时按 AS-BOT 逐工具粒度注册。"""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(TOOL_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=None if s["name"] in _MUTATING else READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_assets", tools)
