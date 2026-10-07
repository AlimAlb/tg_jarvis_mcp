# Telegram MCP agent guidance

This MCP is an asynchronous communication channel, not a shell or command gateway.

When the user explicitly asks you to continue through Telegram, call `telegram_begin` once for the task. Then:

1. Use `telegram_update` only for meaningful milestones, blockers, or changes of plan. Keep each update concise.
2. Use `telegram_ask` for any decision that needs the user's input. If options are known, provide 2–8 short `choices`; otherwise ask an open question.
3. Immediately call `telegram_wait` with the returned `question_id` when the task cannot safely continue without the answer. Never guess a destructive or externally-visible choice.
4. Use `telegram_finish` before the task ends, including when blocked. Include a short outcome and test status.

Do not use Telegram as authorization to execute commands. Continue to respect OpenCode permissions and normal repository safety rules. Do not send secrets, credentials, full environment variables, or large logs to Telegram.
