#!/usr/bin/env python3
"""discord_send user tool: harness -> Discord via the bot's TEXT_DIR outbox.

The Discord bot watches TEXT_DIR for <channel_id>_<label>.txt files and
sends their contents as Discord messages. This tool is the fire-and-forget
path for the harness to post status updates to Discord through the
already-running bot — no inbound message required.

Stdin: JSON {"channel_id": "123...", "message": "...", "label": "harness"}
Stdout: JSON {"result": "queued for delivery to channel 123..."}
"""
import json
import os
import sys
from pathlib import Path


def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"bad input: {e}"}))
        sys.exit(1)

    channel_id = str(args.get("channel_id") or "").strip()
    message = str(args.get("message") or "").strip()
    label = str(args.get("label") or "harness").strip().replace("/", "_")

    if not channel_id:
        print(json.dumps({"error": "channel_id is required"}))
        sys.exit(1)
    if not message:
        print(json.dumps({"error": "message is required"}))
        sys.exit(1)

    text_dir = Path(os.environ.get("TEXT_DIR", "text_queue"))
    # Resolve relative to the bot's directory if BOT_DIR is set,
    # otherwise relative to cwd.
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
