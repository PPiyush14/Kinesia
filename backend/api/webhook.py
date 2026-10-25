"""Webhook dispatcher for session lifecycle events.

Dispatches an HTTP POST request asynchronously in a background thread when a session ends,
transmitting the session summary, metadata, and key points to an external endpoint
(e.g., Slack, Notion, CRM, custom backend).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests

logger = logging.getLogger(__name__)


class WebhookDispatcher:
    """Dispatches asynchronous webhooks on session completion."""

    def __init__(self, webhook_url: str | None = None, webhook_secret: str | None = None) -> None:
        self.webhook_url = webhook_url.strip() if webhook_url else None
        self.webhook_secret = webhook_secret.strip() if webhook_secret else None

    @property
    def is_enabled(self) -> bool:
        return bool(self.webhook_url)

    def dispatch_session_summary_async(
        self,
        session_id: str,
        summary_payload: dict[str, Any],
    ) -> threading.Thread | None:
        """Trigger background HTTP POST with summary payload. Non-blocking."""
        if not self.is_enabled:
            return None

        thread = threading.Thread(
            target=self._send_payload,
            args=(session_id, summary_payload),
            name=f"webhook-dispatcher-{session_id[:8]}",
            daemon=True,
        )
        thread.start()
        return thread

    def _send_payload(self, session_id: str, summary_payload: dict[str, Any]) -> None:
        if not self.webhook_url:
            return

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Kinesia-AI/1.0 Webhook-Dispatcher",
        }
        if self.webhook_secret:
            headers["X-Webhook-Secret"] = self.webhook_secret

        body = {
            "event": "session_completed",
            "session_id": session_id,
            "data": summary_payload,
        }

        try:
            logger.info("Dispatching webhook for session %s to %s", session_id, self.webhook_url)
            response = requests.post(
                self.webhook_url,
                json=body,
                headers=headers,
                timeout=10.0,
            )
            if response.ok:
                logger.info(
                    "Webhook delivered successfully for session %s (status %d)",
                    session_id,
                    response.status_code,
                )
            else:
                logger.warning(
                    "Webhook endpoint responded with status %d for session %s: %s",
                    response.status_code,
                    session_id,
                    response.text[:200],
                )
        except Exception as exc:  # noqa: BLE001 - Optional delivery failure must not abort a conversation.
            logger.warning("Failed to deliver webhook for session %s: %s", session_id, exc)
