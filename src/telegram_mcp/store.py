"""Durable SQLite state for Telegram offsets, conversations, and questions."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ConversationStatus = Literal["active", "finished"]
QuestionStatus = Literal["pending", "answered", "expired"]


@dataclass(frozen=True)
class Conversation:
    id: str
    chat_id: str
    user_id: str
    title: str
    status: ConversationStatus
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class Question:
    id: str
    conversation_id: str
    chat_id: str
    user_id: str
    question: str
    choices: list[str]
    telegram_message_id: int
    status: QuestionStatus
    answer: str | None
    created_at: int
    expires_at: int
    answered_at: int | None


def now_millis() -> int:
    return int(time.time() * 1000)


class Store:
    """SQLite repository. Its schema is compatible with the prior Bun implementation."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def get_offset(self) -> int | None:
        row = self._connection.execute("SELECT value FROM meta WHERE key = 'telegram_offset'").fetchone()
        return int(row["value"]) if row else None

    def set_offset(self, offset: int) -> None:
        self._connection.execute(
            "INSERT INTO meta(key, value) VALUES ('telegram_offset', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(offset),),
        )
        self._connection.commit()

    def create_conversation(self, conversation: Conversation) -> None:
        self._connection.execute(
            "INSERT INTO conversations(id, chat_id, user_id, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                conversation.id,
                conversation.chat_id,
                conversation.user_id,
                conversation.title,
                conversation.status,
                conversation.created_at,
                conversation.updated_at,
            ),
        )
        self._connection.commit()

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self._connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return self._conversation(row) if row else None

    def touch_conversation(self, conversation_id: str) -> None:
        self._connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_millis(), conversation_id))
        self._connection.commit()

    def finish_conversation(self, conversation_id: str) -> None:
        self._connection.execute(
            "UPDATE conversations SET status = 'finished', updated_at = ? WHERE id = ?", (now_millis(), conversation_id)
        )
        self._connection.commit()

    def get_pending_question_for_conversation(self, conversation_id: str) -> Question | None:
        row = self._connection.execute(
            "SELECT * FROM questions WHERE conversation_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return self._question(row) if row else None

    def create_question(self, question: Question) -> None:
        self._connection.execute(
            "INSERT INTO questions(id, conversation_id, chat_id, user_id, question, choices_json, telegram_message_id, "
            "status, answer, created_at, expires_at, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                question.id,
                question.conversation_id,
                question.chat_id,
                question.user_id,
                question.question,
                json.dumps(question.choices),
                question.telegram_message_id,
                question.status,
                question.answer,
                question.created_at,
                question.expires_at,
                question.answered_at,
            ),
        )
        self._connection.commit()

    def get_question(self, question_id: str) -> Question | None:
        row = self._connection.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        return self._question(row) if row else None

    def answer_question(self, question_id: str, answer: str) -> bool:
        result = self._connection.execute(
            "UPDATE questions SET status = 'answered', answer = ?, answered_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (answer, now_millis(), question_id),
        )
        self._connection.commit()
        return result.rowcount == 1

    def expire_question(self, question_id: str) -> bool:
        result = self._connection.execute(
            "UPDATE questions SET status = 'expired' WHERE id = ? AND status = 'pending' AND expires_at <= ?",
            (question_id, now_millis()),
        )
        self._connection.commit()
        return result.rowcount == 1

    def expire_pending_questions(self) -> int:
        result = self._connection.execute(
            "UPDATE questions SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?", (now_millis(),)
        )
        self._connection.commit()
        return result.rowcount

    def find_reply_question(self, chat_id: str, user_id: str, reply_to_message_id: int) -> Question | None:
        row = self._connection.execute(
            "SELECT * FROM questions WHERE chat_id = ? AND user_id = ? AND telegram_message_id = ? "
            "AND status = 'pending' LIMIT 1",
            (chat_id, user_id, reply_to_message_id),
        ).fetchone()
        return self._question(row) if row else None

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              chat_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              title TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('active', 'finished')),
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL REFERENCES conversations(id),
              chat_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              question TEXT NOT NULL,
              choices_json TEXT NOT NULL,
              telegram_message_id INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending', 'answered', 'expired')),
              answer TEXT,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              answered_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS questions_reply_lookup
              ON questions(chat_id, user_id, telegram_message_id, status);
            CREATE INDEX IF NOT EXISTS questions_conversation_pending
              ON questions(conversation_id, status);
            """
        )
        self._connection.commit()

    @staticmethod
    def _conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _question(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"],
            conversation_id=row["conversation_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            question=row["question"],
            choices=json.loads(row["choices_json"]),
            telegram_message_id=row["telegram_message_id"],
            status=row["status"],
            answer=row["answer"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            answered_at=row["answered_at"],
        )
