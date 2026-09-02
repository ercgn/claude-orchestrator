#!/usr/bin/env bash
#
# orchestrator plugin — optional install helper.
#
# It checks prerequisites, validates the plugin manifest, tells you the exact
# command that would put this checkout where Claude Code looks for it, and can
# pin your session model to Fable 5.1. It never moves, links, or edits hooks,
# agents, or skills itself.
#
# Usage: ./install.sh [--set-model] [--check]
#   --set-model  also set "model": "claude-fable-5-1[1m]" in your Claude Code
#                settings.json (a timestamped backup is made first)
#   --check      run the prerequisite, validation, and location checks only

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SET_MODEL=0
CHECK_ONLY=0

usage() {
  echo "Usage: ./install.sh [--set-model] [--check]"
  echo "  --set-model  pin \"model\" to claude-fable-5-1[1m] in settings.json"
  echo "  --check      prerequisite, validation, and location checks only"
}

for arg in "$@"; do
  case "$arg" in
    --set-model) SET_MODEL=1 ;;
    --check)     CHECK_ONLY=1 ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "Unknown option: $arg"; usage; exit 1 ;;
  esac
done

# --- 1. prerequisites -------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required — every hook handler in this plugin runs" >&2
  echo "       under python3. Install it and run this script again." >&2
  exit 1
fi
echo "python3: $(command -v python3)"

HAVE_CLAUDE=1
if ! command -v claude >/dev/null 2>&1; then
  HAVE_CLAUDE=0
  echo "WARNING: claude not on PATH; skipping validation"
else
  echo "claude:  $(command -v claude)"
fi

# --- 2. manifest validation -------------------------------------------------

if [ "$HAVE_CLAUDE" -eq 1 ]; then
  claude plugin validate --strict "$SCRIPT_DIR"
  VALIDATE_STATUS=$?
  if [ "$VALIDATE_STATUS" -ne 0 ]; then
    echo "WARNING: validation failed (exit $VALIDATE_STATUS); continuing"
  fi
fi

# --- 3. location ------------------------------------------------------------

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILLS_DIR="$CFG/skills"
echo "Claude Code config dir: $CFG"

case "$SCRIPT_DIR/" in
  "$SKILLS_DIR"/*)
    echo "This checkout is already under $SKILLS_DIR — nothing to move."
    ;;
  *)
    echo "This checkout is NOT under $SKILLS_DIR, so Claude Code will not load"
    echo "it automatically. Either clone it there:"
    echo
    echo "  git clone git@github.com:ercgn/claude-orchestrator.git \"$SKILLS_DIR/orchestrator\""
    echo
    echo "or link this checkout into place yourself:"
    echo
    echo "  mkdir -p \"$SKILLS_DIR\" && ln -s \"$SCRIPT_DIR\" \"$SKILLS_DIR/orchestrator\""
    echo
    echo "(This script never moves or links anything on your behalf.)"
    ;;
esac

if [ "$CHECK_ONLY" -eq 1 ]; then
  exit 0
fi

# --- 4. session model -------------------------------------------------------

if [ "$SET_MODEL" -eq 1 ]; then
  SETTINGS="$CFG/settings.json"
  mkdir -p "$CFG"
  if [ -f "$SETTINGS" ]; then
    BACKUP="$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$SETTINGS" "$BACKUP"
    echo "Backed up $SETTINGS to $BACKUP"
  fi
  python3 - "$SETTINGS" <<'PY'
import io
import json
import os
import sys

path = sys.argv[1]
data = {}
if os.path.exists(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
if not isinstance(data, dict):
    data = {}

previous = data.get("model", "(unset)")
data["model"] = "claude-fable-5-1[1m]"
with io.open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write(u"\n")

print("  previous model: %s" % previous)
print("  new model:      %s" % data["model"])
PY
  SET_MODEL_STATUS=$?
  if [ "$SET_MODEL_STATUS" -ne 0 ]; then
    echo "WARNING: could not update $SETTINGS (exit $SET_MODEL_STATUS)"
  fi
fi

# --- 5. done ----------------------------------------------------------------

echo "Run /reload-plugins in open sessions, or start a new session."
exit 0
