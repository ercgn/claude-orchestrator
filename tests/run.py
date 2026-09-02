#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline test suite for the orchestrator plugin's hook handlers.

Standard library only, no network, no Claude Code process. Each case runs a
handler as a subprocess with CLAUDE_PLUGIN_DATA pointed at a fresh temporary
directory, so state files and the standby sentinel always start empty.

Usage: python3 tests/run.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HANDLERS = os.path.join(ROOT, "hooks-handlers")
FIXTURES = os.path.join(HERE, "fixtures")

_spec = importlib.util.spec_from_file_location(
    "orchestrator_common", os.path.join(HANDLERS, "common.py")
)
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)

SID = "00000000-0000-0000-0000-0000000000aa"
FABLE_TRANSCRIPT = os.path.join(FIXTURES, "transcript-fable.jsonl")
SONNET_TRANSCRIPT = os.path.join(FIXTURES, "transcript-sonnet.jsonl")
MIXED_TRANSCRIPT = os.path.join(
    FIXTURES, "transcript-fable-then-sidechain-and-synthetic.jsonl"
)
REAL_AGENT_PAYLOAD = os.path.join(FIXTURES, "agent-pretooluse-real.json")

# Unix seconds for the newest main-thread assistant entry in every transcript
# fixture (2026-01-01T00:00:01Z).
FIXTURE_TS = common._parse_ts("2026-01-01T00:00:01.000Z")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

def run_handler(handler, payload, data_dir):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = data_dir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HANDLERS, handler)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, universal_newlines=True,
    )
    out, err = proc.communicate(json.dumps(payload))
    if proc.returncode != 0:
        raise AssertionError(
            "%s exited %d (handlers must always exit 0); stderr: %s"
            % (handler, proc.returncode, err.strip())
        )
    try:
        return json.loads(out)
    except ValueError:
        raise AssertionError("%s printed non-JSON: %r" % (handler, out))


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


def context(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext")


def assert_allowed(out):
    if out != {}:
        raise AssertionError("expected {} (allow), got %r" % (out,))


def assert_denied(out, needle):
    if decision(out) != "deny":
        raise AssertionError("expected deny, got %r" % (out,))
    if needle not in reason(out):
        raise AssertionError("reason missing %r; reason was: %s" % (needle, reason(out)))


def state_path(data_dir, session_id=SID):
    return os.path.join(data_dir, "orchestrator", "sessions", "%s.json" % session_id)


def read_state_file(data_dir, session_id=SID):
    with open(state_path(data_dir, session_id)) as fh:
        return json.load(fh)


def put_state(data_dir, model, ts, source="model-switch", session_id=SID):
    path = state_path(data_dir, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"model": model, "ts": ts, "source": source}, fh)


def wf_payload(script, tool_name="Workflow"):
    return {
        "session_id": SID,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"script": script},
    }


def write_transcript(d, model, timestamp, name="transcript.jsonl"):
    """One main-thread assistant entry, for timestamp-sensitive cases."""
    path = os.path.join(d, name)
    entry = {
        "parentUuid": None, "isSidechain": False, "type": "assistant",
        "uuid": "00000000-0000-4000-8000-0000000000ff",
        "timestamp": timestamp,
        "message": {"model": model, "role": "assistant",
                    "content": [{"type": "text", "text": "OK"}]},
    }
    with open(path, "w") as fh:
        fh.write(json.dumps(entry) + "\n")
    return path


def agent_payload(subagent_type, model=None, transcript=None, session_id=SID):
    tool_input = {"description": "probe", "prompt": "Reply PONG",
                  "subagent_type": subagent_type}
    if model is not None:
        tool_input["model"] = model
    payload = {
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
    }
    if transcript is not None:
        payload["transcript_path"] = transcript
    return payload


# --------------------------------------------------------------------------
# Workflow guard
# --------------------------------------------------------------------------

W_BARE = (
    "export const meta = { name: 'probe', description: 'probe' }\n"
    "const r = await agent('say hi', {})\n"
    "return r\n"
)


def w1(d):
    out = run_handler("guard-workflow.py", wf_payload(W_BARE), d)
    assert_denied(out, "Blocked by orchestrator: this workflow script")
    assert_denied(out, "line 2: bare call")


def w2(d):
    script = (
        "export const meta = { name: 'probe', description: 'probe' }\n"
        "const a = await agent('one', { agentType: 'orchestrator:scout' })\n"
        "const b = await agent('two', { agentType: 'orchestrator:executor' })\n"
        "return a + b\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w3(d):
    script = "const a = await agent('one', { model: 'sonnet' })\nreturn a\n"
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w4(d):
    script = (
        "export const meta = { name: 'probe', description: 'probe' }\n"
        "const a = await agent('one', { agentType: 'orchestrator:scout' })\n"
        "const b = await agent('two', {})\n"
        "return a + b\n"
    )
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "line 3: bare call")
    if "line 2" in reason(out):
        raise AssertionError("compliant line 2 was listed: %s" % reason(out))


def w5(d):
    script = (
        "export const meta = { name: 'probe', "
        "description: 'audit agent() calls across the repo' }\n"
        "const a = await agent('one', { agentType: 'orchestrator:scout' })\n"
        "return a\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w6(d):
    script = (
        "const a = await agent('one', {\n"
        "  // don't pass model here — the role owns its binding\n"
        "  agentType: 'orchestrator:executor',\n"
        "})\n"
        "return a\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w7(d):
    script = "const a = foo.agent('x', {})\nreturn a\n"
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w8(d):
    payload = wf_payload(W_BARE, tool_name="Bash")
    assert_allowed(run_handler("guard-workflow.py", payload, d))


def w9(d):
    assert_allowed(run_handler("guard-workflow.py", wf_payload(""), d))


def w10(d):
    script = "const a = await agent('one', {\nreturn a\n"
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "line 1: unbalanced agent call")


def w11(d):
    script = (
        "const a = await agent('one', "
        "{ agentType: 'orchestrator:executor', model: 'haiku' })\nreturn a\n"
    )
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "named role with model argument")


def w12(d):
    script = (
        "const a = await agent('one', "
        "{ agentType: 'general-purpose', model: 'sonnet' })\nreturn a\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w13(d):
    script = "const q = `${await agent('hi', {})}`\nreturn q\n"
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "bare call")


def w14(d):
    script = (
        "const q = `${await agent('hi', "
        "{ agentType: 'orchestrator:scout' })}`\nreturn q\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w15(d):
    script = (
        "const s = `prose about agent() calls and ${obj.name} here`\n"
        "const r = await agent('x', { agentType: 'orchestrator:executor' })\n"
        "return r\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w16(d):
    script = (
        "const q = `outer ${ `inner ${await agent('deep', {})}` } end`\n"
        "return q\n"
    )
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "bare call")


def w17(d):
    script = (
        "const q = `${ {a: 1}.a } ${ fn({b: '}'}) }`\n"
        "const r = await agent('x', { agentType: 'orchestrator:scout' })\n"
        "return r\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w18(d):
    script = 'agent(0,{"agentType":"orchestrator:scout",model:0})\n'
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "named role with model argument")


def w19(d):
    script = "agent(0,{model:0/*agentType:'orchestrator:'*/})\n"
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w20(d):
    script = "agent(agent(0,{model:0}),{})\n"
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "bare call")
    listed = [l for l in reason(out).split("\n") if l.startswith("line ")]
    if listed != ["line 1: bare call — agent(agent(0,{model:0}),{})"]:
        raise AssertionError(
            "expected one bare-call entry for line 1, got %r" % (listed,))


def w21(d):
    script = "agent(" * 10000
    started = time.monotonic()
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    elapsed = time.monotonic() - started
    assert_denied(out, "too large to scan safely")
    if elapsed >= 2.0:
        raise AssertionError(
            "handler took %.2fs; must finish well inside the 5s hook timeout"
            % elapsed)


def w22(d):
    script = "".join(
        "const r%d = await agent('n%d', {})\n" % (i, i) for i in range(30))
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "(+10 more)")
    listed = [l for l in reason(out).split("\n") if l.startswith("line ")]
    if len(listed) != 20:
        raise AssertionError("expected 20 listed lines, got %d" % len(listed))


def w23(d):
    script = "const q = `text ${await agent('x', {})} tail`\nreturn q\n"
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "bare call")


def w24(d):
    """Worst case at both caps: 200 nested calls in exactly 131,072 chars.

    Every call carries an explicit `model`, so a scan that runs to completion
    finds no violation; a machine slow enough to spend 2.5 s here denies on the
    time budget instead. Either outcome is fine — what must hold is that the
    handler returns fast, far inside the 5 s hook timeout, because a timed-out
    PreToolUse hook lets the call proceed.
    """
    script = ("agent(" * 200 + '"' + "x" * (131072 - 200 * 6 - 2 - 200 * 13)
              + '"' + ',{model:"h"})' * 200)
    if len(script) != 131072:
        raise AssertionError("payload is %d chars, expected 131072" % len(script))
    started = time.monotonic()
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    elapsed = time.monotonic() - started
    if elapsed >= 3.5:
        raise AssertionError(
            "handler took %.2fs; the scan must stop at its 2.5s budget" % elapsed)
    if out != {} and "exceeded its time budget" not in reason(out):
        raise AssertionError(
            "expected a completed scan or the time-budget deny, got %r" % (out,))


def w25(d):
    script = "".join(
        "const r%d = await agent('n%d', {})\n" % (i, i) for i in range(201))
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "too large to scan safely")
    assert_denied(out, "201 agent calls")


def w26(d):
    script = (
        "// " + "agent(" * 201 + "\n"
        "const r = await agent('x', { agentType: 'orchestrator:scout' })\n"
        "return r\n"
    )
    assert_allowed(run_handler("guard-workflow.py", wf_payload(script), d))


def w27(d):
    head = (
        "const r = await agent('x', { agentType: 'orchestrator:scout' })\n"
        "// "
    )
    script = head + "z" * (131073 - len(head))
    if len(script) != 131073:
        raise AssertionError("payload is %d chars, expected 131073" % len(script))
    out = run_handler("guard-workflow.py", wf_payload(script), d)
    assert_denied(out, "too large to scan safely")


# --------------------------------------------------------------------------
# Agent guard
# --------------------------------------------------------------------------

R1_NEEDLE = "carries its own model binding"
R2_NEEDLE = "is a built-in agent that inherits"


def a1(d):
    out = run_handler("guard-agent.py",
                      agent_payload("orchestrator:scout", model="haiku"), d)
    assert_denied(out, R1_NEEDLE)


def a2(d):
    assert_allowed(run_handler("guard-agent.py",
                               agent_payload("orchestrator:scout"), d))


def a3(d):
    with open(REAL_AGENT_PAYLOAD) as fh:
        payload = json.load(fh)
    assert_allowed(run_handler("guard-agent.py", payload, d))

    with open(REAL_AGENT_PAYLOAD) as fh:
        rewritten = json.load(fh)
    rewritten["tool_input"]["subagent_type"] = "orchestrator:scout"
    out = run_handler("guard-agent.py", rewritten, d)
    assert_denied(out, R1_NEEDLE)


def a4(d):
    out = run_handler("guard-agent.py",
                      agent_payload("Explore", transcript=FABLE_TRANSCRIPT), d)
    assert_denied(out, R2_NEEDLE)


def a5(d):
    out = run_handler("guard-agent.py",
                      agent_payload("general-purpose", model="sonnet",
                                    transcript=FABLE_TRANSCRIPT), d)
    assert_allowed(out)


def a6(d):
    out = run_handler("guard-agent.py",
                      agent_payload("Explore", transcript=SONNET_TRANSCRIPT), d)
    assert_allowed(out)


def a7(d):
    assert_allowed(run_handler("guard-agent.py", agent_payload("Explore"), d))


def a8(d):
    put_state(d, "claude-fable-5-1[1m]", FIXTURE_TS + 3600)
    out = run_handler("guard-agent.py",
                      agent_payload("Explore", transcript=SONNET_TRANSCRIPT), d)
    assert_denied(out, R2_NEEDLE)


def a9(d):
    out = run_handler("guard-agent.py",
                      agent_payload("Explore", transcript=MIXED_TRANSCRIPT), d)
    assert_denied(out, R2_NEEDLE)


# --------------------------------------------------------------------------
# UserPromptSubmit reminder
# --------------------------------------------------------------------------

def prompt_payload(transcript=None):
    payload = {
        "session_id": SID,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "do the thing",
    }
    if transcript is not None:
        payload["transcript_path"] = transcript
    return payload


def p1(d):
    out = run_handler("prompt-reminder.py", prompt_payload(FABLE_TRANSCRIPT), d)
    if context(out) != common.REMINDER:
        raise AssertionError("expected REMINDER, got %r" % (out,))


def p2(d):
    out = run_handler("prompt-reminder.py", prompt_payload(SONNET_TRANSCRIPT), d)
    if "standby" not in out.get("systemMessage", ""):
        raise AssertionError("expected a standby systemMessage, got %r" % (out,))


def p3(d):
    first = run_handler("prompt-reminder.py", prompt_payload(SONNET_TRANSCRIPT), d)
    if "standby" not in first.get("systemMessage", ""):
        raise AssertionError("first run should notify, got %r" % (first,))
    second = run_handler("prompt-reminder.py", prompt_payload(SONNET_TRANSCRIPT), d)
    assert_allowed(second)


def p4(d):
    out = run_handler("prompt-reminder.py", prompt_payload(), d)
    if context(out) != common.CONDITIONAL:
        raise AssertionError("expected CONDITIONAL, got %r" % (out,))


# --------------------------------------------------------------------------
# SessionStart
# --------------------------------------------------------------------------

def session_payload(model=None, transcript=None, source="startup"):
    payload = {"session_id": SID, "hook_event_name": "SessionStart",
               "source": source}
    if model is not None:
        payload["model"] = model
    if transcript is not None:
        payload["transcript_path"] = transcript
    return payload


def s1(d):
    out = run_handler("session-start.py", session_payload("claude-fable-5-1"), d)
    if context(out) != common.ACTIVATE:
        raise AssertionError("expected ACTIVATE, got %r" % (out,))
    if "active" not in out.get("systemMessage", ""):
        raise AssertionError("expected an active systemMessage, got %r" % (out,))
    state = read_state_file(d)
    if state.get("source") != "session-start":
        raise AssertionError("expected source session-start, got %r" % (state,))


def s2(d):
    first = run_handler("session-start.py", session_payload("claude-sonnet-5"), d)
    if "standby" not in first.get("systemMessage", ""):
        raise AssertionError("expected a standby systemMessage, got %r" % (first,))
    assert_allowed(run_handler("session-start.py",
                               session_payload("claude-sonnet-5"), d))


def s3(d):
    out = run_handler("session-start.py", session_payload(), d)
    if context(out) != common.CONDITIONAL:
        raise AssertionError("expected CONDITIONAL, got %r" % (out,))


def s4(d):
    out = run_handler("session-start.py",
                      session_payload(transcript=FABLE_TRANSCRIPT,
                                      source="compact"), d)
    if context(out) != common.ACTIVATE:
        raise AssertionError("expected ACTIVATE, got %r" % (out,))


# --------------------------------------------------------------------------
# PostModelSwitch
# --------------------------------------------------------------------------

def m1(d):
    out = run_handler("model-switch.py",
                      {"session_id": SID, "hook_event_name": "PostModelSwitch",
                       "from_model": "claude-sonnet-5",
                       "to_model": "claude-fable-5-1"}, d)
    if "active" not in out.get("systemMessage", ""):
        raise AssertionError("expected an active systemMessage, got %r" % (out,))
    state = read_state_file(d)
    if state.get("source") != "model-switch":
        raise AssertionError("expected source model-switch, got %r" % (state,))
    if state.get("model") != "claude-fable-5-1":
        raise AssertionError("expected the switched-to model, got %r" % (state,))


def m2(d):
    payload = {"session_id": SID, "hook_event_name": "PostModelSwitch",
               "from_model": "claude-fable-5-1", "to_model": "claude-sonnet-5"}
    first = run_handler("model-switch.py", payload, d)
    if "installed — standby" not in first.get("systemMessage", ""):
        raise AssertionError("expected the one-time notice, got %r" % (first,))
    second = run_handler("model-switch.py", payload, d)
    if second.get("systemMessage") != "orchestrator: standby (claude-sonnet-5)":
        raise AssertionError("expected the short standby line, got %r" % (second,))


def m3(d):
    run_handler("model-switch.py",
                {"session_id": SID, "hook_event_name": "PostModelSwitch",
                 "model": "claude-fable-5-1"}, d)
    state = read_state_file(d)
    if state.get("model") != "claude-fable-5-1":
        raise AssertionError("expected state from the model key, got %r" % (state,))


# --------------------------------------------------------------------------
# common.py units
# --------------------------------------------------------------------------

def c1(d):
    model, ts = common.model_from_transcript(MIXED_TRANSCRIPT)
    if model != "claude-fable-5-1":
        raise AssertionError(
            "sidechain/synthetic entries were not skipped: got %r" % (model,))
    if ts != FIXTURE_TS:
        raise AssertionError("expected ts %r, got %r" % (FIXTURE_TS, ts))


def c2(d):
    os.environ["CLAUDE_PLUGIN_DATA"] = d
    try:
        payload = {"session_id": SID, "transcript_path": FABLE_TRANSCRIPT}

        put_state(d, "claude-sonnet-5", FIXTURE_TS - 3600)
        model, source = common.detect_model(payload)
        if (model, source) != ("claude-fable-5-1", "transcript"):
            raise AssertionError(
                "older state should lose to the transcript: got %r" % ((model, source),))

        put_state(d, "claude-sonnet-5", FIXTURE_TS + 3600)
        model, source = common.detect_model(payload)
        if (model, source) != ("claude-sonnet-5", "state"):
            raise AssertionError(
                "newer state should win over the transcript: got %r" % ((model, source),))
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)


def c3(d):
    for value in ("fable", "claude-fable-5-1[1m]", "FABLE-5"):
        if not common.is_fable(value):
            raise AssertionError("is_fable(%r) should be True" % (value,))
    for value in ("claude-sonnet-5", "", None):
        if common.is_fable(value):
            raise AssertionError("is_fable(%r) should be False" % (value,))


def c4(d):
    os.environ["CLAUDE_PLUGIN_DATA"] = d
    try:
        before = time.time()
        common.write_state(SID, "claude-fable-5-1", "unit-test")
        expected = os.path.join(d, "orchestrator", "sessions", "%s.json" % SID)
        if not os.path.exists(expected):
            raise AssertionError("state file not created at %s" % expected)
        state = common.read_state(SID)
        if state.get("model") != "claude-fable-5-1":
            raise AssertionError("round-trip lost the model: %r" % (state,))
        if state.get("source") != "unit-test":
            raise AssertionError("round-trip lost the source: %r" % (state,))
        if abs(float(state.get("ts")) - before) > 5:
            raise AssertionError("ts is not within 5s of now: %r" % (state,))
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)


def c5(d):
    os.environ["CLAUDE_PLUGIN_DATA"] = d
    try:
        put_state(d, "claude-fable-5-1", 1)
        model, source = common.detect_model(
            {"session_id": SID, "transcript_path": FABLE_TRANSCRIPT})
        if (model, source) != ("claude-fable-5-1", "transcript"):
            raise AssertionError(
                "newer transcript should win: got %r" % ((model, source),))
        state = read_state_file(d)
        if state.get("ts") != 1:
            raise AssertionError(
                "same model should not refresh the state ts: %r" % (state,))
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)


def c6(d):
    os.environ["CLAUDE_PLUGIN_DATA"] = d
    try:
        stamp = "2026-01-01T00:00:00.000Z"
        path = write_transcript(d, "claude-sonnet-5", stamp)
        model, source = common.detect_model(
            {"session_id": SID, "transcript_path": path})
        if (model, source) != ("claude-sonnet-5", "transcript"):
            raise AssertionError(
                "transcript should be adopted: got %r" % ((model, source),))
        state = read_state_file(d)
        if state.get("source") != "transcript":
            raise AssertionError("expected source transcript, got %r" % (state,))
        if state.get("ts") != common._parse_ts(stamp):
            raise AssertionError(
                "expected the transcript entry's own ts %r, got %r"
                % (common._parse_ts(stamp), state.get("ts")))
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)


def c7(d):
    os.environ["CLAUDE_PLUGIN_DATA"] = d
    try:
        put_state(d, "claude-sonnet-5", common._parse_ts("2026-01-01T00:00:00Z"))
        path = write_transcript(d, "claude-fable-5-1", "2026-01-01T00:00:10Z")
        model, source = common.detect_model(
            {"session_id": SID, "transcript_path": path})
        if (model, source) != ("claude-fable-5-1", "transcript"):
            raise AssertionError(
                "a transcript newer by its own clock should win: got %r"
                % ((model, source),))
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)


CASES = [
    ("W1", w1), ("W2", w2), ("W3", w3), ("W4", w4), ("W5", w5), ("W6", w6),
    ("W7", w7), ("W8", w8), ("W9", w9), ("W10", w10), ("W11", w11), ("W12", w12),
    ("W13", w13), ("W14", w14), ("W15", w15), ("W16", w16), ("W17", w17),
    ("W18", w18), ("W19", w19), ("W20", w20), ("W21", w21), ("W22", w22),
    ("W23", w23), ("W24", w24), ("W25", w25), ("W26", w26), ("W27", w27),
    ("A1", a1), ("A2", a2), ("A3", a3), ("A4", a4), ("A5", a5), ("A6", a6),
    ("A7", a7), ("A8", a8), ("A9", a9),
    ("P1", p1), ("P2", p2), ("P3", p3), ("P4", p4),
    ("S1", s1), ("S2", s2), ("S3", s3), ("S4", s4),
    ("M1", m1), ("M2", m2), ("M3", m3),
    ("C1", c1), ("C2", c2), ("C3", c3), ("C4", c4), ("C5", c5), ("C6", c6),
    ("C7", c7),
]


def main():
    failures = 0
    for case_id, fn in CASES:
        data_dir = tempfile.mkdtemp(prefix="orchestrator-test-")
        try:
            fn(data_dir)
            print("PASS %s" % case_id)
        except Exception as exc:
            failures += 1
            print("FAIL %s: %s" % (case_id, exc))
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
    total = len(CASES)
    print("%d/%d passed" % (total - failures, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
