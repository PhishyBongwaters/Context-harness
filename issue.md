# Issue: scratch.md accumulates unpurged tool outputs

## Problem
scratch.md grows without bound because tool results are written into it and never offloaded or purged. This creates context bloat that eats into the token budget.

## Evidence
- scratch.md contained 634 lines in a single turn, including full tool outputs from `read` calls
- Tool results from reading history.md (466 lines) were embedded in scratch.md
- Assembled context hit 34,632 tokens (51% of budget) in one turn, mostly from scratch.md bloat

## Expected Behavior
- Tool results should be offloaded to separate files or purged after use
- scratch.md should contain only active working notes, not raw tool output dumps
- Hints to offloaded results may remain, but not the full content

## Current State
- history.md: ✅ old turns archived correctly
- archive/: ✅ old turns offloaded
- facts/decisions/tasks.md: ✅ clean (no entries yet)
- scratch.md: ❌ accumulates raw tool outputs indefinitely

## Suggested Fix
Auto-curation rules should detect when tool results are no longer needed and:
1. Move them to a temporary offload directory
2. Replace with a reference/hint in scratch.md
3. Purge on next turn if not referenced
