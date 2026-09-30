"""Workspace file utilities — shared across execution layer adapters.

Migrated from services.datamind.agent.file_tools to decouple
the workspace root resolution from the (removed) built-in agent framework.
"""

from pathlib import Path

_WORKSPACES_DIR_ENV = None


def _get_workspaces_dir() -> Path:
    global _WORKSPACES_DIR_ENV
    if _WORKSPACES_DIR_ENV is None:
        from services.shared.common.config import ADH_WORKSPACES_DIR
        _WORKSPACES_DIR_ENV = Path(ADH_WORKSPACES_DIR)
    return _WORKSPACES_DIR_ENV


def workspace_root(workspace_id: int) -> Path:
    """工作空间文件根目录(不存在则创建);workspace_id=0 用 global."""
    name = f"ws_{workspace_id}" if workspace_id else "global"
    root = _get_workspaces_dir() / name
    root.mkdir(parents=True, exist_ok=True)
    return root
