"""Observer settings reader frozen from 1.5.1, commit cf02936."""

import re


LEVELS = ("notes", "assistant", "everything")
MODEL = r"[A-Za-z0-9_.:-]{1,100}"
DEFAULTS = {"enabled": False, "mode": "shadow", "publisher_id": "", "level": "", "model": "", "sort_model": "",
            "strong_model": "", "max_invocations": 70, "max_sort": 80, "max_strong": 8, "sort_tokens": 800000,
            "middle_tokens": 700000, "strong_tokens": 320000, "window_hours": 24, "max_input_chars": 48000,
            "timeout_seconds": 90, "max_candidates": 6, "max_pending_bytes": 134217728,
            "max_pending_observations": 10000, "retention_days": 7, "sync_interval_seconds": 300, "max_posts": 25,
            "max_tasks": 3, "max_proposals": 2, "max_summaries": 4, "max_renders": 30}
LIMITS = ("max_invocations", "max_sort", "max_strong", "sort_tokens", "middle_tokens", "strong_tokens", "window_hours",
          "max_input_chars", "timeout_seconds", "max_candidates", "max_pending_bytes", "max_pending_observations",
          "retention_days", "sync_interval_seconds", "max_posts", "max_tasks", "max_proposals", "max_summaries",
          "max_renders")


def complete(raw):
    """A stored settings dict with the defaults of every key it lacks (the keys 1.5.0 added, for a 1.4.0 entry). No
    validation and no clamping: settings() does those. The level of a 1.4.0 entry is "": not chosen."""
    out = dict(DEFAULTS)
    out.update(raw)
    return out


def settings(config):
    """Validate observer settings; upper resource bounds cannot be enlarged. `config` is the effective configuration
    (local_config), whose `observer` entry comes from this machine's private approvals."""
    raw = config.get("observer", {}) if isinstance(config, dict) else {}
    if not isinstance(raw, dict):
        raise ValueError("observer_settings")
    if set(raw) - set(DEFAULTS):
        raise ValueError("observer_settings")
    out = complete(raw)
    if type(out["enabled"]) is not bool or out["mode"] not in ("shadow", "automatic"):
        raise ValueError("observer_mode")
    if not isinstance(out["publisher_id"], str) or out["publisher_id"] and not re.fullmatch(r"[0-9a-f]{32}", out["publisher_id"]):
        raise ValueError("observer_publisher")
    if out["level"] != "" and out["level"] not in LEVELS:
        raise ValueError("observer_level")
    for name in ("model", "sort_model", "strong_model"):
        if not isinstance(out[name], str) or out[name] and not re.fullmatch(MODEL, out[name]):
            raise ValueError("observer_model")
    for name in LIMITS:
        if type(out[name]) is not int or out[name] < 1:
            raise ValueError("observer_limit")
        if name == "window_hours":
            out[name] = max(24, min(168, out[name]))
        elif name == "sync_interval_seconds":
            out[name] = max(300, min(86400, out[name]))
        else:
            out[name] = min(DEFAULTS[name], out[name])
    return out
