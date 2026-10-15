"""Workspace file utilities — shared across execution layer adapters.

Migrated from backend.modules.mind.agent.file_tools to decouple
the workspace root resolution from the (removed) built-in agent framework.

目录布局: {ADH_WORKSPACES_DIR}/{user_id}/workspace/{workspace_id}/...
"""

import time
from pathlib import Path

_WORKSPACES_DIR_ENV = None

# workspace_id → owner_id 缓存（workspace 属主不变）
_owner_cache: dict[int, tuple[int, float]] = {}
_OWNER_TTL = 600


def _get_workspaces_dir() -> Path:
    global _WORKSPACES_DIR_ENV
    if _WORKSPACES_DIR_ENV is None:
        from backend.common.config import ADH_WORKSPACES_DIR
        _WORKSPACES_DIR_ENV = Path(ADH_WORKSPACES_DIR)
    return _WORKSPACES_DIR_ENV


def get_workspace_owner_id(workspace_id: int) -> int:
    """解析工作空间属主 user_id（带 TTL 缓存）。"""
    now = time.monotonic()
    cached = _owner_cache.get(workspace_id)
    if cached and (now - cached[1]) < _OWNER_TTL:
        return cached[0]
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM adh_workspaces WHERE id = %s", (workspace_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"工作空间 {workspace_id} 不存在")
    owner_id = int(row["owner_id"])
    _owner_cache[workspace_id] = (owner_id, now)
    return owner_id


def workspace_root(workspace_id: int, user_id: int) -> Path:
    """工作空间文件根目录(不存在则创建)。

    路径: {ADH_WORKSPACES_DIR}/{user_id}/workspace/{workspace_id}
    """
    root = _get_workspaces_dir() / str(user_id) / "workspace" / str(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    return root
