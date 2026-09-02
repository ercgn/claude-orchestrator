# -*- coding: utf-8 -*-
"""Shared helpers for the orchestrator plugin's hook handlers.

Python 3 standard library only. Nothing here may raise: every hook handler
must print exactly one JSON object and exit 0, whatever the environment
looks like.
"""
import calendar
import json
import os
import time

PLUGIN = "orchestrator"

ROLES = [
    "scout",
    "Explore",
    "classifier",
    "plan-verifier",
    "security-reviewer",
    "mech-executor",
    "executor",
    "verifier",
    "security-executor",
]

# Built-in agent types that inherit the session model when no explicit model
# argument is passed.
BUILTIN_INHERITING = {"Explore", "general-purpose", "Plan", "claude"}

ACTIVATE = (
    "[orchestrator] Orchestrator mode is in force for this Fable session: "
    "invoke Skill(orchestrator:orchestrate) now if it is not already loaded, "
    "and delegate ALL code writing to the orchestrator:* role agents. Route by "
    "role name (subagent_type / agentType) and never pass a model argument to a "
    "named role. Fable does not write code."
)

REMINDER = (
    "[orchestrator] Fable session: orchestrator mode is in force. "
    "Skill(orchestrator:orchestrate) if not loaded; every repo-bound change "
    "goes through an orchestrator:* role; no model argument on a named role."
)

CONDITIONAL = (
    "[orchestrator] If this session's model is Fable 5.x: orchestrator mode is "
    "in force — Skill(orchestrator:orchestrate) if not loaded; every repo-bound "
    "change goes through an orchestrator:* role; no model argument on a named "
    "role. If the model is not Fable, ignore this."
)

TRANSCRIPT_TAIL_BYTES = 262144


def state_dir():
    """Directory holding this installation's state, or None if unavailable."""
    try:
        base = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
        if not base:
            base = os.path.expanduser("~/.cache/claude-orchestrator")
        path = os.path.join(base, PLUGIN)
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        return None


def _session_file(session_id):
    base = state_dir()
    if not base or not session_id:
        return None
    return os.path.join(base, "sessions", "%s.json" % session_id)


def write_state(session_id, model, source, ts=None):
    """Record the model believed to be in use for this session.

    ``ts`` defaults to now; the transcript path passes the timestamp of the
    entry it read, so a record never claims to be newer than its evidence.
    """
    try:
        path = _session_file(session_id)
        if not path or not model:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = time.time() if ts is None else ts
        with open(path, "w") as fh:
            json.dump({"model": model, "ts": stamp, "source": source}, fh)
    except Exception:
        pass


def read_state(session_id):
    """Return the recorded state dict for this session, or None."""
    try:
        path = _session_file(session_id)
        if not path or not os.path.exists(path):
            return None
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _parse_ts(value):
    """ISO 8601 with a trailing Z -> unix seconds. 0 when unparseable."""
    try:
        if not isinstance(value, str) or not value:
            return 0
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1]
        if "." in text:
            text = text.split(".", 1)[0]
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def model_from_transcript(path):
    """Model of the newest main-thread assistant entry -> (model, ts_unix)."""
    try:
        if not path or not isinstance(path, str) or not os.path.exists(path):
            return (None, None)
        with open(path, "rb") as fh:
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            except Exception:
                pass
            blob = fh.read()
        text = blob.decode("utf-8", "replace")
        for line in reversed(text.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "assistant":
                continue
            if entry.get("isSidechain"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            model = message.get("model")
            if not isinstance(model, str) or not model or model.startswith("<"):
                continue
            return (model, _parse_ts(entry.get("timestamp")))
        return (None, None)
    except Exception:
        return (None, None)


def detect_model(payload):
    """Best guess at this session's model -> (model or None, source)."""
    try:
        if not isinstance(payload, dict):
            return (None, None)

        direct = payload.get("model")
        if isinstance(direct, str) and direct.strip():
            return (direct, "payload")

        session_id = payload.get("session_id")
        t_model, t_ts = model_from_transcript(payload.get("transcript_path"))
        state = read_state(session_id)
        s_model = None
        s_ts = 0
        if isinstance(state, dict) and isinstance(state.get("model"), str):
            s_model = state.get("model")
            try:
                s_ts = float(state.get("ts") or 0)
            except Exception:
                s_ts = 0

        if t_model and s_model:
            if float(t_ts or 0) > s_ts:
                # Only refresh when the belief actually changes: rewriting an
                # identical record would move its ts forward and let a stale
                # decision outlive a newer transcript entry.
                if s_model != t_model:
                    write_state(session_id, t_model, "transcript", t_ts)
                return (t_model, "transcript")
            return (s_model, "state")
        if t_model:
            write_state(session_id, t_model, "transcript", t_ts)
            return (t_model, "transcript")
        if s_model:
            return (s_model, "state")
        return (None, None)
    except Exception:
        return (None, None)


def is_fable(model):
    return bool(model) and "fable" in str(model).lower()


def standby_notice_once(model):
    """One-shot 'installed — standby' notice per installation, else None."""
    try:
        if not model or is_fable(model):
            return None
        base = state_dir()
        if not base:
            return None
        sentinel = os.path.join(base, "standby-notified")
        if os.path.exists(sentinel):
            return None
        with open(sentinel, "w") as fh:
            fh.write(str(time.time()))
        return (
            "orchestrator: installed — standby on %s. Orchestrator mode "
            "activates on Fable 5.x (set \"model\": \"claude-fable-5-1[1m]\" in "
            "~/.claude/settings.json, or /model)." % model
        )
    except Exception:
        return None


def read_payload(stream):
    """Parse one JSON object from stdin; {} on anything unexpected."""
    try:
        data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def emit(obj):
    print(json.dumps(obj))
