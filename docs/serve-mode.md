# Harness serve mode

Persistent HTTP server for integrations (Discord bot, etc.).

```
harness --serve --port 8080
```

## What it does

- Starts an HTTP server on `127.0.0.1:<port>` (localhost only, stdlib only)
- Holds a `Loop` per session ID in memory — no process startup per message
- Sessions live under `<sessions>/serve/<session_id>/` (full harness sessions: pruning, sats, history, everything)

## API

```
POST /turn
{"session": "discord-dm-123", "message": "hello"}

-> {"response": "...", "session": "discord-dm-123"}

GET /health -> {"status": "ok"}
```

## Discord integration

The [discord-bot](https://github.com/PhishyBongwaters/discord-bot-opencode)
can use the harness as its backend instead of opencode:

1. Start the harness: `harness --serve --port 8080`
2. In the bot's `.env`: `BACKEND=harness` and `HARNESS_URL=http://127.0.0.1:8080`
3. DM the bot — turns go to the harness, replies come back to Discord

Session IDs are the bot's session keys (e.g. `dm-<user_id>`), so each
Discord user/channel gets a persistent harness session.

Limitations over HTTP (vs opencode subprocess):
- File attachments are not forwarded (logged and ignored)
- Per-turn model overrides are not supported
