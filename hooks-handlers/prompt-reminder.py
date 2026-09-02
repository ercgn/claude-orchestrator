#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit: keep orchestrator mode in view on Fable sessions."""
import sys

import common


def main():
    payload = common.read_payload(sys.stdin)
    model, _source = common.detect_model(payload)

    if common.is_fable(model):
        common.emit({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": common.REMINDER,
            }
        })
        return

    if model:
        notice = common.standby_notice_once(model)
        common.emit({"systemMessage": notice} if notice else {})
        return

    common.emit({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": common.CONDITIONAL,
        }
    })


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
