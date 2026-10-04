"""context-harness: a daily-driver agent loop where the model treats its context as a file.

The session's entire persistent memory is a single file (context.md).
The model reads it, writes it, prunes it. The harness never silently
truncates -- on a hard budget breach the model gets a prune-only turn.
"""
