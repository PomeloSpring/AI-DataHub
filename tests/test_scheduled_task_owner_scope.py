"""任务调度按创建者（用户）隔离 —— 同角色不得共享任务清单/详情。

用户口径（2026-11-17）：任务调度跟随用户，不跟随角色。历史缺陷是 admin 旁路
（`owner_id=None if role == 'admin'`）+ 一批新用户绑定同一个 admin 角色，导致
「同一个角色能看到相同的任务调度」，且可改删他人任务。

全局运维处置的正道是系统「任务监控」页（task_monitor_service，独立 admin 门禁），
不在用户面放开越权读取。
"""
import asyncio

import pytest
from fastapi import HTTPException

from backend.modules.flow.api import scheduled as api
from backend.modules.flow.api.scheduled import _scheduled_access, list_scheduled_tasks

OWNER = {"user_id": 2, "role": "admin"}
OTHER_ADMIN = {"user_id": 1, "role": "admin"}
VIEWER = {"user_id": 3, "role": "viewer"}


class _State:
    def __init__(self, user):
        self.current_user = user


class _URL:
    def __init__(self, path):
        self.path = path


class _Req:
    """最小 Request 替身：_scheduled_access 只消费这几个属性。"""

    def __init__(self, method="GET", path="/api/scheduled-tasks/tasks",
                 params=None, user=None, query=None):
        self.method = method
        self.url = _URL(path)
        self.path_params = params or {}
        self.query_params = query or {}
        self.headers = {}
        self.state = _State(user)


def _patch_task(monkeypatch, owner_id):
    monkeypatch.setattr(api.scheduled_task_service, "get_task",
                        lambda tid: {"id": tid, "owner_id": owner_id, "workspace_id": 3})
    monkeypatch.setattr(api.scheduled_task_service, "get_log",
                        lambda lid: {"id": lid, "scheduled_task_id": 77})
    monkeypatch.setattr(api, "authorize_resource_scope", lambda user, ws: int(ws or 0))


def _patch_list(monkeypatch, captured):
    def fake_list_tasks(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0}

    monkeypatch.setattr(api.scheduled_task_service, "list_tasks", fake_list_tasks)


@pytest.mark.parametrize("user", [OWNER, OTHER_ADMIN, VIEWER])
def test_list_tasks_is_owner_scoped_for_every_role(monkeypatch, user):
    """列表恒按当前登录人过滤：admin 也不再拿「全量」。"""
    monkeypatch.setattr(api, "authorize_workspace", lambda u, ws: int(ws or 0))
    captured = {}
    _patch_list(monkeypatch, captured)

    list_scheduled_tasks(workspace_id=0, page=1, size=20, user=user)

    assert captured["owner_id"] == user["user_id"]


def test_list_tasks_workspace_is_optional_narrowing(monkeypatch):
    """workspace_id=0 保持跨工作空间（任务跟随用户），>0 才收窄。"""
    monkeypatch.setattr(api, "authorize_workspace", lambda u, ws: int(ws or 0))
    captured = {}
    _patch_list(monkeypatch, captured)

    list_scheduled_tasks(workspace_id=7, page=1, size=20, user=OWNER)

    assert captured["workspace_id"] == 7
    assert captured["owner_id"] == OWNER["user_id"]


@pytest.mark.parametrize("method,path,params", [
    ("GET", "/api/scheduled-tasks/tasks/77", {"task_id": "77"}),
    ("PUT", "/api/scheduled-tasks/tasks/77", {"task_id": "77"}),
    ("DELETE", "/api/scheduled-tasks/tasks/77", {"task_id": "77"}),
    ("PATCH", "/api/scheduled-tasks/tasks/77/toggle", {"task_id": "77"}),
    ("POST", "/api/scheduled-tasks/tasks/77/trigger", {"task_id": "77"}),
    ("GET", "/api/scheduled-tasks/tasks/77/logs", {"task_id": "77"}),
    ("GET", "/api/scheduled-tasks/logs/9", {"log_id": "9"}),
])
def test_same_role_admin_cannot_reach_other_users_task(monkeypatch, method, path, params):
    """同角色（admin）改删他人任务一律按不存在处理，不再靠角色旁路放行。"""
    _patch_task(monkeypatch, owner_id=OWNER["user_id"])
    req = _Req(method=method, path=path, params=params, user=OTHER_ADMIN)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_scheduled_access(req))

    assert exc.value.status_code == 404


def test_owner_can_reach_own_task(monkeypatch):
    _patch_task(monkeypatch, owner_id=OWNER["user_id"])
    req = _Req(method="GET", path="/api/scheduled-tasks/tasks/77",
               params={"task_id": "77"}, user=OWNER)

    asyncio.run(_scheduled_access(req))  # 不抛异常即放行


def test_service_list_tasks_fails_closed_without_owner():
    """服务层不再提供「不过滤属主」的全量读口。"""
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service

    with pytest.raises(PermissionError):
        scheduled_task_service.list_tasks(owner_id=0)


def test_function_tool_lists_only_own_tasks(monkeypatch):
    """AI 通道同口径：不再按 workspace 列出同空间他人任务。"""
    from backend.modules.mind.execution import function_tools as ft

    seen = {}
    monkeypatch.setattr(ft, "_identity",
                        lambda: {"user_id": 9, "workspace_id": 3, "role": "admin"})

    def fake_select(sql, params):
        seen["sql"], seen["params"] = sql, params
        return []

    monkeypatch.setattr(ft, "_select", fake_select)

    ft._list_scheduled_tasks({})

    assert "owner_id = %s" in seen["sql"]
    assert "workspace_id = %s" not in seen["sql"]
    assert seen["params"][0] == 9
