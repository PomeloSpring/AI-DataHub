from backend.modules.mind.api.chat import FeedbackRequest, save_message_feedback


def test_reason_only_feedback_is_saved_without_claiming_it_will_be_digested(monkeypatch):
    writes = []
    monkeypatch.setattr("backend.common.db.execute_write", lambda *args: writes.append(args))

    result = save_message_feedback(
        FeedbackRequest(satisfied=False, dissatisfaction_reason="wrong_answer"),
        {"user_id": 23},
    )

    assert result["ok"] is True
    assert result["will_be_digested"] is False
    assert result["message_uuid"]
    assert len(writes) == 1


def test_feedback_with_expected_business_ref_is_queued_for_digest(monkeypatch):
    writes = []
    monkeypatch.setattr("backend.common.db.execute_write", lambda *args: writes.append(args))

    result = save_message_feedback(
        FeedbackRequest(
            satisfied=False,
            dissatisfaction_reason="missing_metric",
            expected_ref="案例数量",
        ),
        {"user_id": 23},
    )

    assert result["will_be_digested"] is True
    assert len(writes) == 1
