from pathlib import Path

from telegram_mcp.store import Conversation, Question, Store, now_millis


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.sqlite")


def conversation(now: int) -> Conversation:
    return Conversation("conversation", "100", "200", "Test", "active", now, now)


def test_store_persists_offsets_and_matches_only_expected_reply(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = now_millis()
    store.set_offset(42)
    store.create_conversation(conversation(now))
    store.create_question(
        Question("question", "conversation", "100", "200", "Proceed?", [], 7, "pending", None, now, now + 1000, None)
    )
    assert store.get_offset() == 42
    assert store.find_reply_question("100", "wrong-user", 7) is None
    assert store.answer_question("question", "yes") is True
    assert store.get_question("question").answer == "yes"  # type: ignore[union-attr]
    assert store.answer_question("question", "no") is False
    store.close()


def test_store_expires_only_past_deadline(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = now_millis()
    store.create_conversation(conversation(now))
    store.create_question(
        Question("expired", "conversation", "100", "200", "Old?", [], 8, "pending", None, now - 10_000, now - 1, None)
    )
    assert store.expire_question("expired") is True
    assert store.get_question("expired").status == "expired"  # type: ignore[union-attr]
    store.close()
