#!/usr/bin/env python3
"""
Session report for context-harness.
Usage:
  python session_report.py <session_id|path> [--summary]
  python session_report.py --latest [--summary]

Full report includes per-turn metrics, tool usage, sats health, archive footprint.
--summary prints a concise human-readable summary.
"""
import json, sys, argparse
from pathlib import Path
from collections import Counter

def load_usage(usage_path: Path):
    if not usage_path.exists():
        return None
    with open(usage_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # usage.json is {totals:{...}, turns:[...]}
    if isinstance(data, dict) and 'turns' in data:
        return data
    # fallback for old list format
    if isinstance(data, list):
        return {'turns': data, 'totals': {}}
    return None

def summarize_sats(sats_dir: Path):
    out = {}
    if not sats_dir.exists():
        return out
    for p in sats_dir.glob('*.md'):
        try:
            txt = p.read_text(encoding='utf-8', errors='replace')
            out[p.name] = {'bytes': p.stat().st_size, 'chars': len(txt), 'lines': txt.count('\n')}
        except Exception:
            out[p.name] = {'bytes': 0, 'chars': 0, 'lines': 0}
    return out

def handoff_info(sats_dir: Path):
    h = sats_dir / 'handoff.md'
    info = None
    if h.exists():
        txt = h.read_text(encoding='utf-8', errors='replace')
        lines = txt.splitlines()
        stamp = None
        for line in lines:
            if line.startswith('## Handoff -- t'):
                stamp = line.strip()
                break
        info = {'bytes': h.stat().st_size, 'chars': len(txt), 'lines': len(lines), 'stamp': stamp}
    # handoff ledger for trend
    ledger = []
    ledger_path = sats_dir.parent / 'handoff.json'
    if ledger_path.exists():
        try:
            with open(ledger_path, 'r', encoding='utf-8') as f:
                ledger = json.load(f)
        except Exception:
            ledger = []
    return info, ledger

def per_turn_stats(usage):
    turns = usage.get('turns', [])
    stats = []
    for t in turns:
        turn = t.get('turn')
        step = t.get('step')
        bd = t.get('breakdown', {})
        total = bd.get('total', 0)
        transcript = bd.get('transcript', 0)
        tools = bd.get('tools', 0)
        stats.append({'turn': turn, 'step': step, 'total': total, 'transcript': transcript, 'tools': tools})
    return stats

def tool_usage(session_path: Path):
    debug = session_path / 'debug.jsonl'
    counter = Counter()
    events = []
    budget_status = Counter()
    if debug.exists():
        try:
            import json
            with open(debug, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except:
                        continue
                    kind = obj.get('kind')
                    data = obj.get('data', {})
                    if kind == 'tool' and isinstance(data, dict):
                        name = data.get('name')
                        if name:
                            counter[name] += 1
                    if kind == 'budget':
                        status = data.get('status')
                        if status:
                            budget_status[status] += 1
                    events.append((kind, obj))
        except Exception:
            pass
    return counter, events, budget_status

def main():
    parser = argparse.ArgumentParser(description='Session report')
    parser.add_argument('session', nargs='?', help='Session ID or path')
    parser.add_argument('--latest', action='store_true', help='Use most recent session')
    parser.add_argument('--summary', action='store_true', help='Human-readable summary only')
    parser.add_argument('--csv', action='store_true', help='Export CSV report to folder')
    parser.add_argument('--compare', nargs=2, metavar=('S1','S2'), help='Compare two sessions side-by-side')
    args = parser.parse_args()

    base_dir = Path(os.environ.get('LOCALAPPDATA', '')) / 'context-harness' / 'sessions'
    if args.latest or not args.session:
        sessions = sorted(base_dir.glob('202*'), key=lambda p: p.name, reverse=True)
        if not sessions:
            print('No sessions found')
            sys.exit(1)
        session_path = sessions[0]
        print(f'Using latest session: {session_path.name}')
    else:
        sess = Path(args.session)
        if sess.is_dir():
            session_path = sess
        else:
            session_path = base_dir / sess

    if not session_path.exists():
        print(f'Session not found: {session_path}')
        sys.exit(1)

    usage = load_usage(session_path / 'usage.json')
    sats = summarize_sats(session_path / 'sats')
    handoff, handoff_ledger = handoff_info(session_path / 'sats')
    ctx_path = session_path / 'context.md'
    hist_path = session_path / 'history.md'
    archive_path = session_path / 'archive'

    # Build info
    try:
        import subprocess
        commit = subprocess.check_output(['git', '-C', str(Path(__file__).parents[1]), 'rev-parse', '--short', 'HEAD'], text=True).strip()
    except Exception:
        commit = 'unknown'
    # Totals
    totals = usage.get('totals', {}) if usage else {}
    turns_stats = per_turn_stats(usage) if usage else []
    tool_counts, debug_events, budget_status = tool_usage(session_path)

    if args.csv:
        import csv, json
        out_dir = session_path.parent / f'{session_path.name}_report'
        out_dir.mkdir(exist_ok=True)
        # per_turn.csv
        with open(out_dir / 'per_turn.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['turn','step','total','transcript','tools'])
            for s in turns_stats:
                w.writerow([s['turn'], s['step'], s['total'], s['transcript'], s['tools']])
        # tool_usage.csv
        with open(out_dir / 'tool_usage.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['tool','count'])
            for name, cnt in tool_counts.most_common():
                w.writerow([name, cnt])
        # sats.csv
        with open(out_dir / 'sats.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['sat','bytes','lines','chars'])
            for name, info in sats.items():
                w.writerow([name, info['bytes'], info['lines'], info['chars']])
        # handoff_trend.csv
        if handoff_ledger:
            with open(out_dir / 'handoff_trend.csv', 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['turn','lines','tokens'])
                for e in handoff_ledger:
                    w.writerow([e.get('turn'), e.get('lines'), e.get('tokens')])
        # summary.json
        summary = {
            'session': str(session_path),
            'totals': totals,
            'harness_commit': commit,
            'handoff_stamp': handoff.get('stamp') if handoff else None,
            'budget_status': dict(budget_status),
            'tool_counts': dict(tool_counts),
        }
        with open(out_dir / 'summary.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        print(f'CSV report written to {out_dir}')
        return

    # Build info
    try:
        import subprocess
        commit = subprocess.check_output(['git', '-C', str(Path(__file__).parents[1]), 'rev-parse', '--short', 'HEAD'], text=True).strip()
    except Exception:
        commit = 'unknown'
    if args.summary:
        print(f'Session: {session_path}')
        print(f'Harness commit: {commit}')
        print()
        print(f'Total estimated tokens: {totals.get("estimated",0):,}')
        print(f'Input tokens: {totals.get("input",0):,}  Output tokens: {totals.get("output",0):,}')
        print(f'Usage entries: {len(turns_stats)}')
        if turns_stats:
            max_transcript = max(s['transcript'] for s in turns_stats)
            print(f'Max transcript per step: {max_transcript:,}')
            # Estimate when max context would have been hit without handoff
            # Simple heuristic: if transcript >= 35000, flag it
            over_35k = sum(1 for s in turns_stats if s['transcript'] >= 35000)
            if over_35k:
                print(f'Would have hit 35k transcript gate {over_35k} times without auto-handoff')
        # Budget events
        if budget_status:
            print('Budget status events:')
            for k,v in budget_status.items():
                print(f'  {k}: {v}')
        handoff_invalid = sum(1 for k,e in debug_events if k=='handoff-invalid')
        if handoff_invalid:
            print(f'Handoff invalid events: {handoff_invalid}')
        prune_attempts = sum(1 for k,e in debug_events if k=='prune')
        if prune_attempts:
            print(f'Prune events: {prune_attempts}')
            print(f'Handoff invalid events: {handoff_invalid}')
        if handoff:
            print(f'Handoff stamp: {handoff.get("stamp","?")}  Lines: {handoff.get("lines",0)}')
        if handoff_ledger:
            print('Handoff size trend:')
            for entry in handoff_ledger:
                print(f"  Turn {entry.get('turn')}: {entry.get('lines')} lines, {entry.get('tokens')} tokens")
        if ctx_path.exists():
            print(f'context.md: {ctx_path.stat().st_size:,} bytes')
        if hist_path.exists():
            print(f'history.md: {hist_path.stat().st_size:,} bytes')
        if archive_path.exists():
            total_files = sum(1 for _ in archive_path.rglob('*') if _.is_file())
            size = sum(p.stat().st_size for p in archive_path.rglob('*') if p.is_file())
            print(f'archive/: {total_files} files, {size:,} bytes')
        print()
        print('Tool usage:')
        for name, cnt in tool_counts.most_common():
            print(f'  {name}: {cnt}')
        print()
        print('Sats sizes:')
        for name, info in sorted(sats.items()):
            print(f'  {name}: {info["bytes"]:,} bytes, {info["lines"]} lines')
        return

    # Full report
    print(f'Session: {session_path}')
    print()
    print('=== Totals ===')
    print(json.dumps(totals, indent=2))
    print()
    print('=== Per-turn stats ===')
    for s in turns_stats:
        print(f"Turn {s['turn']} Step {s['step']}: total={s['total']:,} transcript={s['transcript']:,} tools={s['tools']:,}")
    print()
    print('=== Tool usage ===')
    for name, cnt in tool_counts.most_common():
        print(f'{name}: {cnt}')
    print()
    print('=== Sats ===')
    for name, info in sorted(sats.items()):
        print(f'{name}: {info["bytes"]:,} bytes, {info["lines"]} lines')
    print()
    if handoff:
        print('=== Handoff ===')
        print(f'Stamp: {handoff.get("stamp")}')
        print(f'Size: {handoff["bytes"]:,} bytes, {handoff["lines"]} lines')
    print()
    if archive_path.exists():
        files = list(archive_path.rglob('*'))
        files = [p for p in files if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        print(f'Archive: {len(files)} files, {size:,} bytes')

if __name__ == '__main__':
    import os
    main()
