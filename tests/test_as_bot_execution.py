"""审批执行闭环回归：原子领取、真实同步与身份注入。"""
import asyncio
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from services.datamind.api import as_bot
from services.datamind.execution import wakers


@pytest.mark.parametrize("payload", [{}, {"datasource_id": 0}, {"datasource_id": -1}, {"datasource_id": True}])
def test_sync_requires_positive_source(payload):
    assert not wakers.validate_action_payload("metadata.sync", payload)[0]


def test_unregistered_action_schema_rejected():
    assert not wakers.validate_action_payload("not.registered", {})[0]


@pytest.mark.parametrize("success", [True, False])
def test_metadata_sync_tracks_actual_result(monkeypatch, success):
    from services.datacatalog.services.metadata_service import MetadataService
    sync = Mock(return_value={"success": success, "message": "private host"})
    monkeypatch.setattr(MetadataService, "sync_metadata", sync)
    result = as_bot._execute_approved_action("metadata.sync", {"datasource_id": 3})
    sync.assert_called_once_with(3)
    assert result["success"] is success
    assert "private host" not in str(result)


def test_lost_claim_does_not_execute(monkeypatch):
    monkeypatch.setattr(wakers, "get_approval", lambda aid: {"status": "pending", "action_key": "metadata.sync", "payload": {"datasource_id": 3}})
    monkeypatch.setattr(wakers, "check_as_bot_permission", lambda *a: True)
    monkeypatch.setattr(wakers, "claim_approval", lambda *a: False)
    execute = Mock()
    monkeypatch.setattr(as_bot, "_execute_approved_action", execute)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(as_bot.approve_action(as_bot.ApproveRequest(approval_id=1), {"role": "admin", "user_id": 7}))
    assert exc.value.status_code == 409
    execute.assert_not_called()


def test_decider_always_server_injected(monkeypatch):
    monkeypatch.setattr(wakers, "get_approval", lambda aid: {"status": "pending", "user_id": 8, "action_key": "metadata.sync", "payload": {"datasource_id": 3, "decided_by": 999}})
    monkeypatch.setattr(wakers, "check_as_bot_permission", lambda *a: True)
    monkeypatch.setattr(wakers, "claim_approval", lambda *a: True)
    monkeypatch.setattr(wakers, "update_approval_status", lambda *a, **kw: True)
    execute = Mock(return_value={"success": True})
    monkeypatch.setattr(as_bot, "_execute_approved_action", execute)
    asyncio.run(as_bot.approve_action(as_bot.ApproveRequest(approval_id=1), {"role": "admin", "user_id": 7}))
    assert execute.call_args.args[1]["decided_by"] == 7
    assert execute.call_args.args[1]["proposed_by"] == 8


def test_reject_cannot_race_execution(monkeypatch):
    monkeypatch.setattr(wakers, "get_approval", lambda aid: {"status": "pending", "action_key": "metadata.sync"})
    monkeypatch.setattr(wakers, "check_as_bot_permission", lambda *a: True)
    monkeypatch.setattr(wakers, "update_approval_status", lambda *a, **kw: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(as_bot.reject_action(as_bot.RejectRequest(approval_id=1), {"role": "admin", "user_id": 7}))
    assert exc.value.status_code == 409
