"""报告所有入口共享私有、外链、策略撤销校验。"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from backend.modules.viz.services import report_access as access


@pytest.fixture
def report(monkeypatch):
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: True)
    monkeypatch.setattr(access, "authorize_workspace", lambda user, ws: ws)
    return {"id": 1, "owner_id": 7, "workspace_id": 3, "generation_status": "ready",
            "access_mode": "private", "publication_status": "draft", "security_context":
            {"version": 1, "share_eligible": True, "restricted": False},
            "share_expires_at": datetime.now(timezone.utc) + timedelta(days=1),
            "share_token_hash": hashlib.sha256(b"test-share").hexdigest()}


def test_private_owner_only(report):
    assert access.can_access_report(report, {"user_id": 7})
    assert not access.can_access_report(report, {"user_id": 8})
    assert not access.can_access_report(report)


def test_token_requires_explicit_publication(report):
    assert not access.can_access_report(report, token="test-share")
    report["publication_status"] = "published"
    assert access.can_access_report(report, token="test-share")
    assert not access.can_access_report(report, token="wrong")


@pytest.mark.parametrize("field,value", [("share_revoked", True), ("generation_status", "degraded"),
    ("share_expires_at", None), ("share_expires_at", datetime(2000, 1, 1))])
def test_invalid_share_closed(report, field, value):
    report.update(publication_status="published", access_mode="public")
    report[field] = value
    assert not access.can_access_report(report, token="test-share")


def test_public_report_requires_safe_evidence(report):
    report.update(publication_status="published", access_mode="public")
    report["security_context"]["restricted"] = True
    assert not access.can_access_report(report)


def test_policy_change_revokes_even_owner(report, monkeypatch):
    monkeypatch.setattr(access, "snapshot_is_current", lambda row: False)
    assert not access.can_access_report(report, {"user_id": 7})


def test_projection_never_returns_security_or_token(report):
    report.update(access_token="legacy-secret", content="报告正文")
    out = access.public_report(report)
    assert "access_token" not in out and "share_token_hash" not in out
    assert "security_context" not in out
    assert out["content"] == "报告正文"
