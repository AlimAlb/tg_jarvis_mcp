# OpenCode Telegram MCP

Local `stdio` [Model Context Protocol](https://modelcontextprotocol.io/) server for an OpenCode agent that needs to continue working after you leave its terminal. The agent can send concise milestones to a Telegram bot, ask a question, wait for a direct answer, and send a final summary.

It is deliberately **not** a remote shell and Telegram messages are never treated as commands. An answer is accepted only when an allowlisted user either replies to the exact question message or taps a button belonging to that question.

## Architecture

```text
OpenCode agent ── stdio MCP ── telegram-mcp (Python, on your workstation)
                                      │
                            SQLite state + exclusive lock
                                      │ outgoing HTTPS / getUpdates long polling
                                      ▼
                                 Telegram Bot API
                                      │
                              allowlisted personal chat
```

There is no inbound port, webhook, SSH tunnel, or public OpenCode endpoint. The MCP server is the only process polling Telegram. It persists the `getUpdates` offset, conversations, questions, and answers in SQLite.

## Requirements

- Python 3.10 or newer
- Python virtual environment with `pip` (standard Python installation); [uv](https://docs.astral.sh/uv/) is optional
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user and chat IDs

## Install and configure

```sh
cd /absolute/path/to/opencode-telegram-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`. `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_ALLOWED_CHAT_IDS` are both required. The default user and chat must each be in their corresponding allowlist, so a forwarded, group, or unexpected chat cannot answer an agent prompt.

Run locally while setting up:

```sh
.venv/bin/telegram-mcp
```

The program uses stdin/stdout for MCP; logs go only to stderr. Do not run a second instance against the same database and lock path: startup will fail safely.

### OpenCode configuration

Add this local MCP server to OpenCode, replacing the absolute path. Keep the token in OpenCode's protected environment settings, your shell environment, or the local ignored `.env` file — never in a committed configuration file.

```jsonc
{
  "mcp": {
    "telegram": {
      "type": "local",
      "command": ["/absolute/path/to/opencode-telegram-mcp/.venv/bin/telegram-mcp"],
      "cwd": "/absolute/path/to/opencode-telegram-mcp",
      "enabled": true,
      "environment": {
        "TELEGRAM_BOT_TOKEN": "...",
        "TELEGRAM_ALLOWED_USER_IDS": "123456789",
        "TELEGRAM_ALLOWED_CHAT_IDS": "123456789",
        "TELEGRAM_DEFAULT_USER_ID": "123456789",
        "TELEGRAM_DEFAULT_CHAT_ID": "123456789",
        "TELEGRAM_MCP_DB_PATH": "/absolute/path/to/private/telegram-mcp.sqlite"
      }
    }
  }
}
```

The console command is installed from `pyproject.toml`. The server loads a local `.env` if present, without overriding explicit process environment variables. If you prefer `uv`, the equivalent setup is `uv sync --extra dev`, followed by `uv run telegram-mcp`.

## MCP tools

| Tool | Intended use |
| --- | --- |
| `telegram_begin` | Starts a remote conversation and announces the task. Returns `conversation_id`. |
| `telegram_update` | Sends one concise milestone or meaningful status. |
| `telegram_ask` | Sends a direct-reply question, or inline buttons for 2–8 choices. Returns `question_id`. |
| `telegram_wait` | Waits up to 300 seconds for that question. It returns answered or pending, and reports expiration as a blocker. |
| `telegram_finish` | Sends a final summary and closes the conversation. |

When you tell the agent “for further communication use Telegram MCP,” it should call `telegram_begin`, retain the returned ID, and follow [AGENTS.md](AGENTS.md). A typical sequence is:

```text
telegram_begin(title: "Repair OAuth callback")
telegram_update(summary: "Found the URL construction bug", next_step: "Adding a regression test")
telegram_ask(question: "Which staging host should receive callbacks?", choices: ["staging.example.com", "app.example.com"])
telegram_wait(question_id: "...")
telegram_finish(summary: "Fixed callback URL construction", changes: ["Updated callback builder", "Added regression test"], tests: "pytest: passed")
```

## Security properties and limits

- Long polling means this server does not expose a network listener.
- Every accepted response must match both an allowlisted chat ID and an allowlisted user ID. It must also match the stored question's exact chat/user pair.
- Open-text replies must be direct replies to the exact registered question. Button callback data contains an unguessable UUID and is checked against the question, its chat, and its user.
- `getUpdates` offsets, conversations, pending questions, and answers persist in SQLite. A restart does not discard an answer.
- The lock prevents two local processes from consuming the same Telegram update stream. Remove a stale lock only after verifying no server process is running.
- Tool validation caps every Telegram message below Telegram's 4096-character limit. Never send secrets, credentials, environment variables, or large logs.
- A question is valid for one hour by default. `telegram_wait` intentionally waits no more than five minutes per call, so the MCP client does not appear hung indefinitely.
- This first version permits one pending question per conversation. Start separate conversations for independent tasks.

## Development

```sh
.venv/bin/python -m pytest
```

The tests do not contact Telegram. They cover configuration allowlists, durable offset/question state, and direct-reply authorization. Before relying on notifications for real work, use a separate throwaway bot and chat for an end-to-end check.
