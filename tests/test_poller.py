import asyncio
from pathlib import Path
from typing import Any

from telegram_mcp.config import load_config
from telegram_mcp.poller import UpdatePoller
from telegram_mcp.store import Conversation, Question, Store, now_millis


class FakeTelegram:
    def __init__(self) -> None:
        self.cleared: list[tuple[str, int]] = []
        self.callbacks: list[str] = []

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        await asyncio.sleep(3600)
        return []

    async def clear_keyboard(self, chat_id: str, message_id: int) -> None:
        self.cleared.append((chat_id, message_id))

    async def answer_callback_query(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


def test_poller_accepts_only_allowlisted_direct_reply(tmp_path: Path) -> None:
    environment = {
        "TELEGRAM_BOT_TOKEN": "123:token",
        "TELEGRAM_ALLOWED_USER_IDS": "200",
        "TELEGRAM_ALLOWED_CHAT_IDS": "100",
        "TELEGRAM_DEFAULT_USER_ID": "200",
        "TELEGRAM_DEFAULT_CHAT_ID": "100",
        "TELEGRAM_MCP_DB_PATH": str(tmp_path / "state.sqlite"),
    }
    config = load_config(environment)
    store = Store(config.database_path)
    now = now_millis()
    store.create_conversation(Conversation("conversation", "100", "200", "Test", "active", now, now))
    store.create_question(Question("question", "conversation", "100", "200", "Proceed?", [], 7, "pending", None, now, now + 100_000, None))
    poller = UpdatePoller(config, store, FakeTelegram())  # type: ignore[arg-type]

    asyncio.run(poller._process({"message": {"chat": {"id": 100}, "from": {"id": 999}, "text": "no", "reply_to_message": {"message_id": 7}}}))
    assert store.get_question("question").status == "pending"  # type: ignore[union-attr]
    asyncio.run(poller._process({"message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "yes", "reply_to_message": {"message_id": 7}}}))
    assert store.get_question("question").answer == "yes"  # type: ignore[union-attr]
    store.close()
