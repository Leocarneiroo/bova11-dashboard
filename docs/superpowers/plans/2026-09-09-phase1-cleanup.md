# Phase 1 Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove confirmed-unused legacy and one-off scripts without changing dashboard behavior.

**Architecture:** Keep the active 23-module runner unchanged. Delete only `bova11_oi_stats.py`, which is superseded by `bova11_oi_stats_light.py`, and three unreferenced maintenance/probe scripts.

**Tech Stack:** Python 3 stdlib, existing `unittest` suite, Git.

---

### Task 1: Remove unused files

**Files:**
- Delete: `scripts/bova11_oi_stats.py`
- Delete: `fix_scripts.py`
- Delete: `patch_all.py`
- Delete: `scripts/test_brapi.py`

- [x] Confirm no active imports or runner entries reference the four files.
- [x] Delete the four confirmed-unused files.

### Task 2: Verify active code

**Files:**
- Test: `tests/`

- [x] Run `python3 -m compileall -q bova11_runner.py scripts tests`.
- [x] Run `.venv/bin/python -m unittest discover -s tests -q`.
- [x] Confirm `git diff --stat` contains only intended deletions plus this plan/context bookkeeping.
