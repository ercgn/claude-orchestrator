#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse guard for the Workflow tool.

Denies a workflow script that contains either

  * a bare agent() call — neither `agentType` nor `model` — which falls back
    to the harness's built-in workflow-subagent and inherits the session
    model at full tool access, or
  * a call that names an `orchestrator:` role via `agentType` *and* passes a
    `model`, which silently overrides the role's own binding.

The script is scanned as code: comments and string-literal contents are
blanked out first, so prose about agent() calls never trips the guard.
Code inside a template literal's ${...} interpolation is not string
content — it is copied through and scanned like any other code, so a
call hidden in an interpolation is caught.

Quoted property keys (``{"agentType": ...}``) are normalized to bare keys of
the same length before tokenizing, so quoting a key cannot hide it. Nested
agent() calls are masked out of their enclosing call's span, so each call is
judged on its own arguments only.

Known limitation: regex literals are not tokenized. A `/.../` regex is scanned
as ordinary code, so a regex containing `agent(` is reported as a call, and a
`}` inside a regex literal within a `${...}` interpolation ends that
interpolation early. Tokenizing regex literals requires full expression
context (`/` is ambiguous between division and a literal), so this scanner
deliberately does not attempt it.

The scan is bounded three ways, every one of them fail-closed: a script longer
than 128 KB is denied before it is tokenized at all, a script with more than
200 agent call tokens is denied before it is scanned, and a scan that runs past
a 2.5 s wall-clock budget stops where it is and denies. A PreToolUse hook that
exceeds its 5 s timeout is treated as absent and the tool call PROCEEDS, so the
guard must never be able to reach that timeout.
"""
import json
import re
import sys
import time

import common

AGENT_CALL = re.compile(r"(?<![A-Za-z0-9_$.])agent\s*\(")
MODEL_ARG = re.compile(r"\bmodel\s*:")
ROLE_ARG = re.compile(r"""agentType\s*:\s*['"`]orchestrator:""")

# Quoted property keys, normalized to bare keys of identical length so the
# blanking pass cannot swallow them as string content. `"agentType"` and
# `'agentType'` are 11 characters, as is ` agentType `; `"model"` and
# `'model'` are 7, as is ` model `.
QUOTED_TYPE_KEY = re.compile(r"""(["'])agentType\1(?=\s*:)""")
QUOTED_MODEL_KEY = re.compile(r"""(["'])model\1(?=\s*:)""")

# Fail-closed caps and budget. A PreToolUse hook that exceeds its 5 s timeout
# is treated as absent and the call PROCEEDS, so an oversized script is denied
# outright rather than scanned, and any scan still running after the budget is
# cut short and denied. Real workflow scripts are well under 20 KB with a few
# dozen calls.
MAX_SCRIPT_CHARS = 131072
MAX_AGENT_CALLS = 200
MAX_SCAN_SECONDS = 2.5
MAX_LISTED_VIOLATIONS = 20

REASON_HEAD = (
    "Blocked by orchestrator: this workflow script has agent call(s) that "
    "break the routing rule. A bare call (neither agentType nor model) falls "
    "back to the harness's built-in workflow-subagent, which inherits the "
    "session model at full tool access; a named orchestrator: role given a "
    "model argument has its own binding overridden. Add agentType with one of "
    + ", ".join("orchestrator:%s" % r for r in common.ROLES)
    + " and no model (or an explicit model with no orchestrator role, for a "
    "genuinely ad-hoc read-only stage), then resubmit:\n"
)

OVERSIZE_REASON = (
    "Blocked by orchestrator: workflow script too large to scan safely "
    "(%d agent calls, %d characters); split the workflow into smaller "
    "scripts and resubmit."
)

BUDGET_REASON = (
    "Blocked by orchestrator: workflow script scan exceeded its time budget "
    "(%.2f s, %d agent calls, %d characters); split the workflow into smaller "
    "scripts and resubmit."
)


class Denied(Exception):
    """Scan stopped early; the message is the ready-to-emit deny reason."""


def check_budget(start, calls, chars):
    """Raise Denied when the scan has run past its wall-clock budget."""
    elapsed = time.monotonic() - start
    if elapsed > MAX_SCAN_SECONDS:
        raise Denied(BUDGET_REASON % (elapsed, calls, chars))


def normalize_keys(src):
    """Rewrite quoted `agentType`/`model` property keys as bare keys.

    Length and newlines are preserved, so every index into the result also
    indexes the original.
    """
    src = QUOTED_TYPE_KEY.sub(" agentType ", src)
    return QUOTED_MODEL_KEY.sub(" model ", src)


def code_only(src, keep_strings=False):
    """Blank comments and string contents, preserving length and newlines.

    Inside a backtick template literal the literal text is blanked, but a
    ``${...}`` interpolation is code: it is copied through unchanged so the
    scanner sees it, with nested braces, nested quoted strings, and nested
    template literals tracked so the interpolation ends at its real ``}``.

    With ``keep_strings=True`` only comments are blanked and string contents
    are copied through, for checks that need to read inside a literal (the
    `agentType: 'orchestrator:...'` role test). Quote tracking is identical
    either way, so both variants align index-for-index.
    """
    out = []
    i = 0
    n = len(src)
    # One entry per open template literal: the brace depth of the ${...}
    # interpolation currently being scanned, or None while scanning the
    # template's own literal text.
    stack = []
    while i < n:
        ch = src[i]
        if stack and stack[-1] is None:
            if ch == "\\":
                out.append("\\" if keep_strings else " ")
                i += 1
                if i < n:
                    if keep_strings:
                        out.append(src[i])
                    else:
                        out.append("\n" if src[i] == "\n" else " ")
                    i += 1
                continue
            if ch == "`":
                out.append("`")
                stack.pop()
                i += 1
                continue
            if ch == "$" and i + 1 < n and src[i + 1] == "{":
                out.append("${")
                stack[-1] = 0
                i += 2
                continue
            if keep_strings:
                out.append(ch)
            else:
                out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            out.append("  ")
            i += 2
            while i < n and src[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            out.append("  ")
            i += 2
            while i < n:
                if src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    out.append("  ")
                    i += 2
                    break
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            continue
        if ch == "`":
            out.append(ch)
            stack.append(None)
            i += 1
            continue
        if ch in ("'", '"'):
            out.append(ch)
            quote = ch
            i += 1
            while i < n:
                cur = src[i]
                if cur == "\\":
                    out.append("\\" if keep_strings else " ")
                    i += 1
                    if i < n:
                        if keep_strings:
                            out.append(src[i])
                        else:
                            out.append("\n" if src[i] == "\n" else " ")
                        i += 1
                    continue
                if cur == quote:
                    out.append(quote)
                    i += 1
                    break
                if keep_strings:
                    out.append(cur)
                else:
                    out.append("\n" if cur == "\n" else " ")
                i += 1
            continue
        if stack:
            if ch == "{":
                stack[-1] += 1
            elif ch == "}":
                if stack[-1] == 0:
                    stack[-1] = None
                else:
                    stack[-1] -= 1
        out.append(ch)
        i += 1
    return "".join(out)


def paren_map(code):
    """One left-to-right pass -> {index of '(' : index of its ')' , or -1}.

    Linear in the length of the script: every parenthesis is visited once.
    Unmatched opens map to -1.
    """
    pairs = {}
    opens = []
    for i, ch in enumerate(code):
        if ch == "(":
            opens.append(i)
        elif ch == ")" and opens:
            pairs[opens.pop()] = i
    for i in opens:
        pairs[i] = -1
    return pairs


def mask_nested(text, start, open_idx, close_idx, calls, index):
    """text[start:close_idx+1] with any nested agent call blanked to spaces.

    ``calls`` is ordered by start offset, so every call nested in this one
    follows it in the list; blanking only the outermost of them keeps the
    total work linear in the script even at the 200-call cap.
    """
    span = list(text[start:close_idx + 1])
    covered = open_idx
    for other in range(index + 1, len(calls)):
        s2, _o2, c2 = calls[other]
        if s2 >= close_idx:
            break
        if s2 <= covered:
            continue
        end = close_idx if c2 == -1 else min(c2, close_idx)
        lo = s2 - start
        hi = end - start + 1
        span[lo:hi] = " " * (hi - lo)
        covered = end
    return "".join(span)


def violations_for(script, start):
    """List of violation lines, or raise Denied with a ready deny reason.

    ``start`` is the ``time.monotonic()`` reading taken when the handler began.
    The character cap is checked on the raw source before anything is
    tokenized, so an oversized script is never walked; the call cap is checked
    on the comments-blanked view, so `agent(` tokens written in a comment do
    not count against it; and the budget is re-checked after each whole-script
    pass and at every per-call iteration.
    """
    if len(script) > MAX_SCRIPT_CHARS:
        raise Denied(
            OVERSIZE_REASON % (len(AGENT_CALL.findall(script)), len(script)))

    src = normalize_keys(script)
    uncommented = code_only(src, keep_strings=True)
    call_count = len(AGENT_CALL.findall(uncommented))
    if call_count > MAX_AGENT_CALLS:
        raise Denied(OVERSIZE_REASON % (call_count, len(script)))
    check_budget(start, call_count, len(script))

    code = code_only(src)
    check_budget(start, call_count, len(script))
    pairs = paren_map(code)
    check_budget(start, call_count, len(script))

    calls = []
    for match in AGENT_CALL.finditer(code):
        open_idx = match.end() - 1
        calls.append((match.start(), open_idx, pairs.get(open_idx, -1)))

    found = []
    for index, (at, open_idx, close_idx) in enumerate(calls):
        check_budget(start, call_count, len(script))
        line_no = src.count("\n", 0, at) + 1
        if close_idx == -1:
            found.append("line %d: unbalanced agent call" % line_no)
            continue
        code_span = mask_nested(code, at, open_idx, close_idx, calls, index)
        role_span = mask_nested(
            uncommented, at, open_idx, close_idx, calls, index)
        snippet = src[at:close_idx + 1].replace("\n", " ")[:80]
        has_type = "agentType" in code_span
        has_model = MODEL_ARG.search(code_span) is not None
        role_type = ROLE_ARG.search(role_span) is not None
        if not has_type and not has_model:
            found.append("line %d: bare call — %s" % (line_no, snippet))
        elif role_type and has_model:
            found.append(
                "line %d: named role with model argument — %s" % (line_no, snippet)
            )
    return found


def deny(reason):
    common.emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })


def main():
    start = time.monotonic()
    payload = common.read_payload(sys.stdin)
    if payload.get("tool_name") != "Workflow":
        common.emit({})
        return

    tool_input = payload.get("tool_input") or {}
    script = tool_input.get("script") if isinstance(tool_input, dict) else None
    if not isinstance(script, str) or not script:
        common.emit({})
        return

    try:
        found = violations_for(script, start)
    except Denied as exc:
        deny(str(exc))
        return
    if not found:
        common.emit({})
        return

    if len(found) > MAX_LISTED_VIOLATIONS:
        extra = len(found) - MAX_LISTED_VIOLATIONS
        found = found[:MAX_LISTED_VIOLATIONS] + ["(+%d more)" % extra]
    deny(REASON_HEAD + "\n".join(found))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({
            "systemMessage": "orchestrator: workflow guard failed (%s: %s); "
                             "call allowed" % (type(exc).__name__, exc)
        }))
