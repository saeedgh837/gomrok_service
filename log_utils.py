# -*- coding: utf-8 -*-
"""Minimal shared JSONL appender with a size cap.

Every event log in this project is append-only JSONL (logs.jsonl,
estelam_logs.jsonl, crm_logs.jsonl, site2 logs). Without a cap those files
grow without bound. append_jsonl() keeps only the newest `cap` lines so each
log stays small and readable while preserving recent history.
"""
import json
import os

DEFAULT_CAP = 400


def append_jsonl(path, record, cap=DEFAULT_CAP):
    """Append one JSON object as a line, then trim to the last `cap` lines.

    Never raises: a logging failure must not break the caller's flow.
    Non-dict records are ignored (guards against None/garbage entries).
    """
    if not isinstance(record, dict):
        return False
    try:
        path = os.fspath(path)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _trim(path, cap)
        return True
    except Exception:
        return False


def _trim(path, cap):
    """Keep only the newest `cap` lines; best-effort, never raises."""
    if not cap or cap <= 0:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        if len(lines) <= cap:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines[-cap:])
    except OSError:
        pass
