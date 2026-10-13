#!/usr/bin/env python3
"""harness_discord_outbox user tool: harness -> Discord via bot TEXT_DIR.

The Discord bot watches TEXT_DIR for <channel_id>_<label>.txt files and
sends their contents as Discord messages. This is the fire-and-forget path
for the harness to post status updates through the already-running bot.

Differs from the bot repo's discord-send.py (direct REST, needs token):
this goes through the local outbox, no token required.

Stdin: JSON {"message": "...", "channel_id": "123...", "label": "harness"}
  channel_id optional if DISCORD_DEFAULT_CHANNEL_ID is set in .env.
Stdout: JSON {"result": "queued for delivery to channel 123..."}

Config via .env (cwd or $HOME) or environment:
  DISCORD_DEFAULT_CHANNEL_ID — default channel/DM to send to
  TEXT_DIR — bot outbox dir (default: text_queue)
  BOT_DIR — bot base dir; TEXT_DIR resolves relative to it if not absolute
"""
import json
import os
import sys
from pathlib import Path


def _load_dotenv():
    """Load .env from cwd and home, stdlib-only. Already-set vars win."""
    for p in (Path.cwd() / ".env", Path.home() / ".env"):
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            pass


_load_dotenv()


def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"bad input: {e}"}))
        sys.exit(1)

    channel_id = str(
        args.get("channel_id") or os.environ.get("DISCORD_DEFAULT_CHANNEL_ID") or ""
    ).strip()
    message = str(args.get("message") or "").strip()
    label = str(args.get("label") or "harness").strip().replace("/", "_")

    if not channel_id:
        print(json.dumps({
            "error": "channel_id is required (or set DISCORD_DEFAULT_CHANNEL_ID in .env)"
        }))
        sys.exit(1)
    if not message:
        print(json.dumps({"error": "message is required"}))
        sys.exit(1)

    text_dir = Path(os.environ.get("TEXT_DIR", "text_queue"))
    bot_dir = os.environ.get("BOT_DIR")
    if bot_dir and not text_dir.is_absolute():
        text_dir = Path(bot_dir) / text_dir

    try:
        text_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(json.dumps({"error": f"cannot create TEXT_DIR: {e}"}))
        sys.exit(1)

    fname = f"{channel_id}_{label}.txt"
    try:
        (text_dir / fname).write_text(message, encoding="utf-8")
    except OSError as e:
        print(json.dumps({"error": f"cannot write: {e}"}))
        sys.exit(1)

    print(json.dumps({"result": f"queued for delivery to channel {channel_id}"}))


if __name__ == "__main__":
    main()
