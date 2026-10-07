"""Minimal, typed Telegram Bot API client used by the MCP server."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config


class TelegramClient:
    """Only the Telegram methods required for notifications and authenticated replies."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._base_url = f"https://api.telegram.org/bot{config.token}"

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        force_reply: bool = False,
        keyboard: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        reply_markup: dict[str, Any] | None = None
        if keyboard is not None:
            reply_markup = {"inline_keyboard": keyboard}
        elif force_reply:
            reply_markup = {"force_reply": True, "input_field_placeholder": "Reply to this message"}
        return await self._call("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        result = await self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
            timeout_seconds=timeout + 5,
        )
        if not isinstance(result, list):
            raise RuntimeError("Telegram getUpdates returned a non-list result")
        return result

    async def answer_callback_query(self, callback_id: str) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id})

    async def clear_keyboard(self, chat_id: str, message_id: int) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def _call(self, method: str, body: dict[str, Any], timeout_seconds: int | None = None) -> Any:
        timeout = timeout_seconds if timeout_seconds is not None else self._config.http_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.post(f"{self._base_url}/{method}", json=body)
        except httpx.HTTPError as error:
            raise RuntimeError(f"Telegram {method} request failed: {error}") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"Telegram {method} returned invalid JSON") from error
        if not response.is_success or not payload.get("ok"):
            message = payload.get("description") or response.reason_phrase or str(response.status_code)
            raise RuntimeError(f"Telegram {method} failed: {message}")
        return payload["result"]
