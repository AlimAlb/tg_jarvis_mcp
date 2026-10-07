from pathlib import Path

import pytest

from telegram_mcp.config import load_config


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "123456:token",
        "TELEGRAM_ALLOWED_USER_IDS": "10,20",
        "TELEGRAM_ALLOWED_CHAT_IDS": "10,-10030",
        "TELEGRAM_DEFAULT_USER_ID": "10",
        "TELEGRAM_DEFAULT_CHAT_ID": "10",
        "TELEGRAM_MCP_DB_PATH": str(tmp_path / "state.sqlite"),
    }


def test_load_config_parses_allowlists_and_defaults(environment: dict[str, str]) -> None:
    config = load_config(environment)
    assert "20" in config.allowed_user_ids
    assert "-10030" in config.allowed_chat_ids
    assert config.poll_timeout_seconds == 25


def test_load_config_rejects_default_outside_allowlist(environment: dict[str, str]) -> None:
    environment["TELEGRAM_DEFAULT_USER_ID"] = "999"
    with pytest.raises(ValueError, match="allowlisted"):
        load_config(environment)


def test_load_config_rejects_nonnumeric_ids(environment: dict[str, str]) -> None:
    environment["TELEGRAM_ALLOWED_CHAT_IDS"] = "my-chat"
    with pytest.raises(ValueError, match="numeric Telegram IDs"):
        load_config(environment)
