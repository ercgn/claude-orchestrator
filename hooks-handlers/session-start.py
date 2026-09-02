#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart: activate orchestrator mode when the session model is Fable."""
import sys

import common


def main():
    payload = common.read_payload(sys.stdin)
    model, source = common.detect_model(payload)

    if source == "payload":
        common.write_state(payload.get("session_id"), model, "session-start")

    if common.is_fable(model):
        common.emit({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": common.ACTIVATE,
            },
            "systemMessage": "orchestrator: active (%s)" % model,
        })
        return

    if model:
        notice = common.standby_notice_once(model)
        common.emit({"systemMessage": notice} if notice else {})
        return

    common.emit({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": common.CONDITIONAL,
        }
    })


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
