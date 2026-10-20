# Session Report

`session_report.py` produces human-readable and machine-exportable reports for context-harness sessions.

## Usage

```powershell
python scripts\session_report.py <session_id|path> [--summary] [--csv]
python scripts\session_report.py --latest [--summary]
python scripts\session_report.py --compare <S1> <S2>
```

## Flags

* `--summary` – concise human-readable summary
* `--csv` – export CSV/JSON evidence pack to `<session>_report\`
* `--compare S1 S2` – side-by-side comparison
* `--latest` – use most recent session under `%LOCALAPPDATA%\context-harness\sessions`

## Output

Summary includes:
* Total estimated / input / output tokens
* Usage entries and max transcript per step
* Handoff stamp and size trend
* Budget/Handoff-invalid events
* Tool usage counts
* Sats sizes, context.md, history.md, archive footprint
* Harness commit for reproducibility

CSV export includes:
* `per_turn.csv`
* `tool_usage.csv`
* `sats.csv`
* `handoff_trend.csv`
* `summary.json`

## Examples

```powershell
python scripts\session_report.py 20261019-160921 --summary
python scripts\session_report.py --latest --summary
python scripts\session_report.py 20261019-160921 --csv
python scripts\session_report.py --compare 20261019-154409 20261019-160921
```

## Notes

* Sessions are under `%LOCALAPPDATA%\context-harness\sessions`
* No session files are modified by this script
* All metrics are derived from `usage.json`, `debug.jsonl`, `sats/`, `archive/`
