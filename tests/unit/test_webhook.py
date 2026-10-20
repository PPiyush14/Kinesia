"""Unit tests for WebhookDispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.api.webhook import WebhookDispatcher


def test_webhook_disabled_by_default() -> None:
    dispatcher = WebhookDispatcher(webhook_url=None)
    assert not dispatcher.is_enabled
    # Dispatching when disabled returns None immediately without error
    thread = dispatcher.dispatch_session_summary_async("sess_1", {"summary": "done"})
    assert thread is None


def test_webhook_dispatches_when_url_configured() -> None:
    dispatcher = WebhookDispatcher(
        webhook_url="https://mock.webhook.site/endpoint",
        webhook_secret="test_secret_123",
    )
    assert dispatcher.is_enabled

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        dispatcher._send_payload("sess_test", {"summary": "Executive briefing"})

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://mock.webhook.site/endpoint"
        assert kwargs["headers"]["X-Webhook-Secret"] == "test_secret_123"
        assert kwargs["json"]["session_id"] == "sess_test"
        assert kwargs["json"]["event"] == "session_completed"
