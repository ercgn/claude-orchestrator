#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostModelSwitch: record a mid-session model change (Claude Code 2.1.258+).

The payload keys for this event are read defensively: it never fires on
builds that do not emit it, and its exact shape is not pinned down.
"""
# Not registered in hooks/hooks.json by default: Claude Code 2.1.224 rejects unknown hook events and would drop every hook.
# See README "Optional: model-switch tracking on 2.1.258+".
import sys

import common


def main():
    payload = common.read_payload(sys.stdin)

    target = None
    for key in ("to_model", "new_model", "model"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            target = value
            break

    if not target:
        common.emit({})
        return

    common.write_state(payload.get("session_id"), target, "model-switch")

    if common.is_fable(target):
        common.emit({"systemMessage": "orchestrator: active (%s)" % target})
        return

    notice = common.standby_notice_once(target)
    if notice:
        common.emit({"systemMessage": notice})
    else:
        common.emit({"systemMessage": "orchestrator: standby (%s)" % target})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
