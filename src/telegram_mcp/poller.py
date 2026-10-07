"""Durable Telegram long-polling and reply validation."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .config import Config
from .store import Question, Store
from .telegram import TelegramClient

CALLBACK_PATTERN = re.compile(r"^tgmc:([0-9a-f-]{36}):(\d+)$")


class UpdatePoller:
    """The sole update consumer. SQLite offset advances even for malformed updates."""

    def __init__(self, config: Config, store: Store, telegram: TelegramClient) -> None:
        self._config = config
        self._store = store
        self._telegram = telegram
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._log = logging.getLogger(__name__)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="telegram-mcp-poller")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def wait_for_answer(self, question_id: str, timeout_seconds: int) -> Question:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            self._store.expire_question(question_id)
            question = self._store.get_question(question_id)
            if question is None:
                raise ValueError("Unknown Telegram question")
            if question.status != "pending" or asyncio.get_running_loop().time() >= deadline:
                return question
            await asyncio.sleep(0.5)

    async def _loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                updates = await self._telegram.get_updates(self._store.get_offset(), self._config.poll_timeout_seconds)
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int):
                        self._log.warning("Ignoring Telegram update with invalid update_id")
                        continue
                    try:
                        await self._process(update)
                    except Exception:  # Never replay an untrusted malformed update forever.
                        self._log.exception("Could not process Telegram update %s", update_id)
                    finally:
                        self._store.set_offset(update_id + 1)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._log.warning("Telegram polling failed; retrying in %.0fs: %s", backoff, error)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _process(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            await self._process_message(message)
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._process_callback(callback)

    def _allowed(self, chat_id: str | None, user_id: str | None) -> bool:
        return bool(
            chat_id
            and user_id
            and chat_id in self._config.allowed_chat_ids
            and user_id in self._config.allowed_user_ids
        )

    async def _process_message(self, message: dict[str, Any]) -> None:
        chat_id = self._nested_id(message, "chat")
        user_id = self._nested_id(message, "from")
        reply = message.get("reply_to_message")
        text = message.get("text")
        if not self._allowed(chat_id, user_id) or not isinstance(reply, dict) or not isinstance(text, str) or not text.strip():
            return
        reply_message_id = reply.get("message_id")
        if not isinstance(reply_message_id, int):
            return
        question = self._store.find_reply_question(chat_id, user_id, reply_message_id)
        if question is None:
            return
        self._store.expire_question(question.id)
        self._store.answer_question(question.id, text.strip())

    async def _process_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        chat_id = self._nested_id(message, "chat") if isinstance(message, dict) else None
        user_id = self._nested_id(callback, "from")
        data = callback.get("data")
        callback_id = callback.get("id")
        if not self._allowed(chat_id, user_id) or not isinstance(data, str):
            return
        match = CALLBACK_PATTERN.fullmatch(data)
        if match is None:
            return
        question = self._store.get_question(match.group(1))
        choice_index = int(match.group(2))
        if (
            question is None
            or question.status != "pending"
            or question.chat_id != chat_id
            or question.user_id != user_id
            or choice_index >= len(question.choices)
        ):
            return
        self._store.expire_question(question.id)
        if self._store.answer_question(question.id, question.choices[choice_index]):
            try:
                await self._telegram.clear_keyboard(chat_id, question.telegram_message_id)
            except Exception as error:  # The durable answer matters more than this cosmetic operation.
                self._log.warning("Could not clear answered-question keyboard: %s", error)
        if isinstance(callback_id, str):
            await self._telegram.answer_callback_query(callback_id)

    @staticmethod
    def _nested_id(value: dict[str, Any], key: str) -> str | None:
        nested = value.get(key)
        identifier = nested.get("id") if isinstance(nested, dict) else None
        return str(identifier) if isinstance(identifier, (int, str)) else None
