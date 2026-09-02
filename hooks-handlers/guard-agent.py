#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse guard for the Agent tool.

R1  A named `orchestrator:` role invoked with an explicit `model` argument is
    denied: the role's own frontmatter binding would be silently overridden.
R2  When the session model is known to be Fable, the built-in agent types that
    inherit that model (Explore, general-purpose, Plan, claude) are denied
    unless an explicit cheaper model is passed. A plugin agent cannot shadow a
    built-in, so this is what keeps background work off the frontier tier.

When the session model is unknown, R2 deliberately does not fire.
"""
import sys

import common


def deny(reason):
    common.emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })


def main():
    payload = common.read_payload(sys.stdin)
    if payload.get("tool_name") != "Agent":
        common.emit({})
        return

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        common.emit({})
        return

    subagent_type = tool_input.get("subagent_type") or ""
    if not isinstance(subagent_type, str):
        subagent_type = ""
    model_arg = tool_input.get("model")
    has_model = isinstance(model_arg, str) and model_arg.strip() != ""

    if subagent_type.startswith("orchestrator:") and has_model:
        deny(
            "Blocked by orchestrator: %s carries its own model binding in its "
            "agent file; an invocation-level model argument silently overrides "
            "it. Omit model when invoking a named role. For a genuinely ad-hoc "
            "agent use general-purpose with an explicit model instead."
            % subagent_type
        )
        return

    if subagent_type in common.BUILTIN_INHERITING and not has_model:
        model, _source = common.detect_model(payload)
        if common.is_fable(model):
            deny(
                "Blocked by orchestrator: %s is a built-in agent that inherits "
                "this session's Fable model at full cost and full tool access. "
                "Use orchestrator:Explore or orchestrator:scout for searches, "
                "orchestrator:executor or orchestrator:mech-executor for "
                "changes, orchestrator:verifier for verification, or pass an "
                "explicit cheaper model for a genuinely ad-hoc agent."
                % subagent_type
            )
            return

    common.emit({})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
