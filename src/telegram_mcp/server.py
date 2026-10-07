"""The five-tool stdio MCP surface for safe Telegram task communication."""

from __future__ import annotations

import atexit
import asyncio
import logging
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from mcp.server import MCPServer

from .config import Config, load_config
from .lock import InstanceLock
from .poller import UpdatePoller
from .store import Conversation, Store, now_millis
from .telegram import TelegramClient

MAX_TELEGRAM_TEXT = 4000


def _clean_text(value: str, field: str, maximum: int = MAX_TELEGRAM_TEXT) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    if len(text) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return text


class TelegramMCPApplication:
    """Business operations behind MCP tools, kept separate from the protocol wrapper."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = InstanceLock(config.lock_path)
        self._lock.acquire()
        self._store = Store(config.database_path)
        self._telegram = TelegramClient(config)
        self._poller = UpdatePoller(config, self._store, self._telegram)
        self._closed = False

    async def _ensure_poller(self) -> None:
        self._poller.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._store.close()
        self._lock.release()

    async def begin(self, title: str, chat_id: str | None = None, user_id: str | None = None) -> str:
        selected_chat_id = chat_id or self._config.default_chat_id
        selected_user_id = user_id or self._config.default_user_id
        if (
            selected_chat_id not in self._config.allowed_chat_ids
            or selected_user_id not in self._config.allowed_user_ids
        ):
            raise ValueError("The requested chat_id or user_id is not allowlisted.")
        conversation_title = _clean_text(title, "title", 300)
        now = now_millis()
        conversation = Conversation(
            id=str(uuid4()),
            chat_id=selected_chat_id,
            user_id=selected_user_id,
            title=conversation_title,
            status="active",
            created_at=now,
            updated_at=now,
        )
        sent = await self._telegram.send_message(
            selected_chat_id,
            f"🤖 {conversation.title}\n\nRemote communication is enabled. I will send concise milestones and questions here.",
        )
        self._store.create_conversation(conversation)
        await self._ensure_poller()
        return f"Telegram remote mode is active. conversation_id: {conversation.id}; announcement message: {sent['message_id']}"

    async def update(self, conversation_id: str, summary: str, next_step: str | None = None) -> str:
        conversation = self._active_conversation(conversation_id)
        cleaned_summary = _clean_text(summary, "summary", 3600)
        cleaned_next_step = _clean_text(next_step, "next_step", 300) if next_step else None
        text = f"📍 {cleaned_summary}" + (f"\n\nNext: {cleaned_next_step}" if cleaned_next_step else "")
        sent = await self._telegram.send_message(conversation.chat_id, text)
        self._store.touch_conversation(conversation.id)
        await self._ensure_poller()
        return f"Telegram update sent (message {sent['message_id']})."

    async def ask(
        self,
        conversation_id: str,
        question: str,
        choices: list[str] | None = None,
        expires_in_seconds: int | None = None,
    ) -> str:
        conversation = self._active_conversation(conversation_id)
        self._store.expire_pending_questions()
        if self._store.get_pending_question_for_conversation(conversation.id):
            raise ValueError("There is already an unanswered Telegram question for this conversation. Wait for it or finish the task.")
        cleaned_question = _clean_text(question, "question", 3500)
        if choices is not None:
            if not 2 <= len(choices) <= 8:
                raise ValueError("choices must contain between 2 and 8 items")
            cleaned_choices = [_clean_text(choice, "choice", 120) for choice in choices]
            if len(set(cleaned_choices)) != len(cleaned_choices):
                raise ValueError("Choices must be distinct.")
        else:
            cleaned_choices = []
        lifetime = expires_in_seconds if expires_in_seconds is not None else 3600
        if not 30 <= lifetime <= 86_400:
            raise ValueError("expires_in_seconds must be an integer between 30 and 86400")
        question_id = str(uuid4())
        keyboard = (
            [[{"text": choice, "callback_data": f"tgmc:{question_id}:{index}"}]
             for index, choice in enumerate(cleaned_choices)]
            if cleaned_choices
            else None
        )
        sent = await self._telegram.send_message(
            conversation.chat_id,
            f"❓ {cleaned_question}",
            force_reply=not cleaned_choices,
            keyboard=keyboard,
        )
        now = now_millis()
        from .store import Question  # Avoid exposing persistence types in the MCP decorators.

        self._store.create_question(
            Question(
                id=question_id,
                conversation_id=conversation.id,
                chat_id=conversation.chat_id,
                user_id=conversation.user_id,
                question=cleaned_question,
                choices=cleaned_choices,
                telegram_message_id=sent["message_id"],
                status="pending",
                answer=None,
                created_at=now,
                expires_at=now + lifetime * 1000,
                answered_at=None,
            )
        )
        self._store.touch_conversation(conversation.id)
        await self._ensure_poller()
        return f"Question sent. question_id: {question_id}. Call telegram_wait with this ID when an answer is needed."

    async def wait(self, question_id: str, wait_seconds: int | None = None) -> str:
        if not self._is_uuid(question_id):
            raise ValueError("question_id must be a UUID")
        seconds = wait_seconds if wait_seconds is not None else 120
        if not 1 <= seconds <= 300:
            raise ValueError("wait_seconds must be an integer between 1 and 300")
        await self._ensure_poller()
        question = await self._poller.wait_for_answer(question_id, seconds)
        if question.status == "answered":
            return f"Answer received: {question.answer}"
        if question.status == "expired":
            raise RuntimeError("The Telegram question expired without an answer. Explain the blocker or ask a new question.")
        return (
            "No answer yet; the question remains pending. Do not assume an answer. "
            "You may continue independent work or call telegram_wait again."
        )

    async def finish(
        self,
        conversation_id: str,
        summary: str,
        changes: list[str] | None = None,
        tests: str | None = None,
        next_step: str | None = None,
    ) -> str:
        conversation = self._active_conversation(conversation_id)
        lines = ["✅ Task update", _clean_text(summary, "summary", 2800)]
        if changes:
            if len(changes) > 10:
                raise ValueError("changes cannot contain more than 10 items")
            lines.extend(["Changes:", *[f"• {_clean_text(change, 'change', 240)}" for change in changes]])
        if tests:
            lines.append(f"Tests: {_clean_text(tests, 'tests', 500)}")
        if next_step:
            lines.append(f"Next: {_clean_text(next_step, 'next_step', 300)}")
        text = "\n".join(lines)
        if len(text) > MAX_TELEGRAM_TEXT:
            raise ValueError("Final summary is too long for Telegram; shorten the fields.")
        sent = await self._telegram.send_message(conversation.chat_id, text)
        self._store.finish_conversation(conversation.id)
        return f"Telegram final summary sent (message {sent['message_id']}); conversation closed."

    def _active_conversation(self, conversation_id: str) -> Conversation:
        if not self._is_uuid(conversation_id):
            raise ValueError("conversation_id must be a UUID")
        conversation = self._store.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError("Unknown conversation_id")
        if conversation.status != "active":
            raise ValueError("This conversation is already finished; call telegram_begin for a new task")
        return conversation

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            from uuid import UUID

            UUID(value)
        except (ValueError, AttributeError):
            return False
        return True


def create_server(application: TelegramMCPApplication) -> MCPServer:
    """Register the public MCP tools against one locally-owned application instance."""

    mcp = MCPServer(
        "OpenCode Telegram",
        instructions=(
            "Use this only after the user explicitly asks to continue through Telegram. "
            "It is a notification and question channel, not command execution authorization."
        ),
    )

    @mcp.tool()
    async def telegram_begin(title: str, chat_id: str | None = None, user_id: str | None = None) -> str:
        """Start Telegram communication for the current task; call it once after the user requests Telegram mode."""
        return await application.begin(title, chat_id, user_id)

    @mcp.tool()
    async def telegram_update(conversation_id: str, summary: str, next_step: str | None = None) -> str:
        """Send one concise, meaningful task milestone; do not stream routine token-by-token work."""
        return await application.update(conversation_id, summary, next_step)

    @mcp.tool()
    async def telegram_ask(
        conversation_id: str,
        question: str,
        choices: list[str] | None = None,
        expires_in_seconds: int | None = None,
    ) -> str:
        """Ask the allowlisted Telegram user a question; choices become buttons, otherwise a direct reply is required."""
        return await application.ask(conversation_id, question, choices, expires_in_seconds)

    @mcp.tool()
    async def telegram_wait(question_id: str, wait_seconds: int | None = None) -> str:
        """Wait up to 300 seconds for a Telegram answer; do not infer an answer if it remains pending."""
        return await application.wait(question_id, wait_seconds)

    @mcp.tool()
    async def telegram_finish(
        conversation_id: str,
        summary: str,
        changes: list[str] | None = None,
        tests: str | None = None,
        next_step: str | None = None,
    ) -> str:
        """Send a final concise summary, including tests or a blocker, then close the Telegram conversation."""
        return await application.finish(conversation_id, summary, changes, tests, next_step)

    return mcp


def main() -> None:
    """Load local secrets, acquire the update lock, and run an stdio MCP server."""

    load_dotenv(override=False)
    logging.basicConfig(level=logging.INFO, format="[telegram-mcp] %(levelname)s %(message)s")
    application = TelegramMCPApplication(load_config(os.environ))
    atexit.register(application.close)
    create_server(application).run()


if __name__ == "__main__":
    main()
