# Issue: Add first-class tools for file search, directory listing, and diff

## Summary

The harness currently relies on `exec` for several common operations that would be cleaner as native tools: searching file contents, listing directories, and computing diffs. These shell escapes work but introduce platform-specific quirks (path quoting, command availability) and are less discoverable.

## Proposed additions

### 1. `search` — file content search
Find files matching a pattern or containing text. Replaces `exec grep` / `exec findstr`.

**Example:**
```
search(path=".", pattern="TODO", regex=true)
```

**Benefits:**
- Cross-platform consistent behavior (no `grep` vs `findstr` differences)
- Returns structured results (file path, line number, matched line)
- No shell quoting issues

### 2. `list_dir` — directory listing
List files and subdirectories in a path. Replaces `exec ls` / `exec dir`.

**Example:**
```
list_dir(path=".", recursive=false)
```

**Benefits:**
- Returns structured data (name, type, size, modified time)
- No platform-specific output parsing
- Handles edge cases (permissions, symlinks) consistently

### 3. `diff` — compute diff between two texts or files
Compute a unified diff. Replaces `exec diff`.

**Example:**
```
diff(old_text="...", new_text="...")
# or
diff(old_path="file.py", new_path="file.py.bak")
```

**Benefits:**
- Structured output (hunks, line numbers, change type)
- Easier to parse and apply programmatically
- No dependency on external `diff` binary

## Optional additions

### 4. Paginated `exec` output
When `exec` output exceeds the ~30k char truncation limit, return a truncation flag and an offset/token so the caller can request the next page. Currently, large outputs silently lose data with no indication.

### 5. Parallel delegation
Currently only one subagent at a time. Allowing multiple concurrent subagents would speed up heavy curation or multi-file tasks significantly.

## Priority

Low-to-medium. The current `exec`-based approach works, but these would improve reliability, cross-platform consistency, and developer experience.
