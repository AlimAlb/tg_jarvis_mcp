"""Strict configuration parsing for the local Telegram MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    """Configuration that is safe to pass through the application."""

    token: str
    allowed_user_ids: frozenset[str]
    allowed_chat_ids: frozenset[str]
    default_user_id: str
    default_chat_id: str
    database_path: Path
    lock_path: Path
    poll_timeout_seconds: int
    http_timeout_seconds: int


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _ids(environment: Mapping[str, str], name: str) -> frozenset[str]:
    values = frozenset(value.strip() for value in _required(environment, name).split(",") if value.strip())
    if not values or any(not value.lstrip("-").isdigit() or value == "-" for value in values):
        raise ValueError(f"{name} must contain one or more numeric Telegram IDs")
    return values


def _integer(environment: Mapping[str, str], name: str, fallback: int, minimum: int, maximum: int) -> int:
    raw = environment.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def load_config(environment: Mapping[str, str]) -> Config:
    """Read and validate the environment before the server starts polling."""

    allowed_user_ids = _ids(environment, "TELEGRAM_ALLOWED_USER_IDS")
    allowed_chat_ids = _ids(environment, "TELEGRAM_ALLOWED_CHAT_IDS")
    default_user_id = _required(environment, "TELEGRAM_DEFAULT_USER_ID")
    default_chat_id = _required(environment, "TELEGRAM_DEFAULT_CHAT_ID")
    if default_user_id not in allowed_user_ids:
        raise ValueError("TELEGRAM_DEFAULT_USER_ID must be allowlisted")
    if default_chat_id not in allowed_chat_ids:
        raise ValueError("TELEGRAM_DEFAULT_CHAT_ID must be allowlisted")

    database_path = Path(environment.get("TELEGRAM_MCP_DB_PATH", "./data/telegram-mcp.sqlite").strip()).expanduser().resolve()
    lock_path = Path(
        environment.get("TELEGRAM_MCP_LOCK_PATH", f"{database_path}.lock").strip()
    ).expanduser().resolve()
    return Config(
        token=_required(environment, "TELEGRAM_BOT_TOKEN"),
        allowed_user_ids=allowed_user_ids,
        allowed_chat_ids=allowed_chat_ids,
        default_user_id=default_user_id,
        default_chat_id=default_chat_id,
        database_path=database_path,
        lock_path=lock_path,
        poll_timeout_seconds=_integer(environment, "TELEGRAM_POLL_TIMEOUT_SECONDS", 25, 1, 50),
        http_timeout_seconds=_integer(environment, "TELEGRAM_HTTP_TIMEOUT_SECONDS", 35, 5, 120),
    )
