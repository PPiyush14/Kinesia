from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator

import websocket

logger = logging.getLogger(__name__)


class CartesiaSyncWS:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        version: str,
        timeout_seconds: float,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.version = version
        self.timeout_seconds = timeout_seconds
        self.headers = [
            f"X-API-Key: {self.api_key}",
            f"Cartesia-Version: {self.version}",
        ]
        self._active_ws = None

    def _create_connection(self):
        ws = websocket.create_connection(
            self.url,
            header=self.headers,
            enable_multithread=True,
            timeout=self.timeout_seconds,
        )
        logger.debug("CartesiaSyncWS connection established")
        return ws

    def close(self) -> None:
        if self._active_ws is not None:
            try:
                self._active_ws.close()
            except Exception as cleanup_error:  # noqa: BLE001 - Keep socket cleanup and future synthesis available.
                logger.debug("Provider cleanup/probe failed (%s)", type(cleanup_error).__name__)
            finally:
                self._active_ws = None

    def send_tts(self, payload: dict) -> tuple[object | None, bool]:
        self.close()

        ws = None
        try:
            ws = self._create_connection()
            ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001 - Keep socket cleanup and future synthesis available.
            logger.error("CartesiaSyncWS send_tts failed (%s)", type(exc).__name__)
            try:
                if ws is not None:
                    ws.close()
            except Exception as cleanup_error:  # noqa: BLE001 - Keep socket cleanup and future synthesis available.
                logger.debug("Provider socket cleanup failed (%s)", type(cleanup_error).__name__)
            return None, False

        self._active_ws = ws
        return ws, True

    def receive_chunks(self, ws=None) -> Iterator[bytes]:
        if ws is None:
            ws = self._active_ws
        if ws is None:
            return

        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Cartesia speech request exceeded its deadline.")
                ws.settimeout(remaining)
                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException as exc:
                    raise TimeoutError("Cartesia speech request timed out.") from exc
                except (websocket.WebSocketConnectionClosedException, OSError) as exc:
                    raise RuntimeError("Cartesia closed before completing speech.") from exc

                if not message:
                    raise RuntimeError("Cartesia closed before completing speech.")

                try:
                    response = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("CartesiaSyncWS received non-JSON message")
                    continue

                if response.get("type") == "chunk":
                    encoded = response.get("data")
                    if encoded:
                        yield base64.b64decode(encoded)

                if response.get("type") == "error":
                    raise RuntimeError(
                        "Cartesia websocket error: "
                        f"{response.get('message') or response.get('error') or response}"
                    )

                if response.get("done") is True or response.get("type") in {"done", "completed"}:
                    return
        finally:
            try:
                ws.close()
            except Exception as cleanup_error:  # noqa: BLE001 - Keep socket cleanup and future synthesis available.
                logger.debug("Provider socket cleanup failed (%s)", type(cleanup_error).__name__)
            if ws is self._active_ws:
                self._active_ws = None
