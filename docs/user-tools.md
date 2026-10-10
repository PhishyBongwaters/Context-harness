# User tools

Scripts as tools. Write a script, add a JSON manifest, pass `--tool`
— the model can now call it.

## Quick start

Two files, same basename:

`discord_tts.py`:
```python
import json, sys

args = json.loads(sys.stdin.read() or "{}")
text = args.get("text", "")
voice = args.get("voice", "default")

# Talk to your bot here (HTTP, socket, file watcher, whatever).
# ...
audio_path = "/tmp/tts-output.mp3"  # whatever your bot produces

print(json.dumps({
    "result": f"Generated {len(text)} chars of speech with voice '{voice}'",
    "files": [audio_path],
}))
```

`discord_tts.json`:
```json
{
  "name": "discord_tts",
  "description": "Generate speech audio via the Discord bot's voice mode",
  "parameters": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "description": "Text to speak"},
      "voice": {"type": "string", "description": "Voice name (optional)"}
    },
    "required": ["text"]
  },
  "timeout": 60
}
```

Run with:
```powershell
harness --tool D:/tools/discord_tts.py
```

Or set `tools_dir` in config to load every script in a directory
(each `.py`/`.sh` with a `.json` sidecar).

## Script protocol

- **stdin:** JSON object of the tool arguments, e.g. `{"text": "hello"}`
- **stdout:** JSON object `{"result": "...", "files": [...]}`
  - `result`: text shown to the model (required)
  - `files`: list of file paths (optional, shown as `[file: path]`)
  - Non-JSON stdout is treated as raw result text
- **stderr:** captured to the session log, not shown to the model
- **exit 0:** success. Non-zero: `ERROR` returned to the model with stderr tail.
- **timeout:** kills the script, returns `ERROR: timed out`. Set per-tool in the manifest (default 120s).
- **cwd:** the session workdir.

A crash, timeout, or bad output returns an `ERROR` string to the model.
It never takes down the harness. After 3 consecutive failures the
circuit breaker stops the model from retrying.

## Manifest fields

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | Yes | Tool name for the model (sanitized to `[A-Za-z0-9_]`). |
| `description` | Yes | What the tool does — the model reads this. |
| `parameters` | Yes | JSON Schema for the arguments. |
| `timeout` | No | Seconds before kill (default 120). |
| `interpreter` | No | Command to run the script (default: current Python). Use `"bash"` for `.sh`. |

User tools cannot shadow built-ins (`read`, `write`, `exec`, etc.).
On name collision the built-in wins and the user tool is skipped.

## Config

```json
{
  "tools_dir": "~/.config/harness/tools",
  "include_file": "~/.config/harness/include.md",
  "include_subagents": false
}
```
