#!/usr/bin/env bash
# Runs the UE Agent Toolkit tests.
# Usage: Scripts/run_tests.sh [Project.uproject] [test_module,...]
#   MODE=editor (default): full editor with -nullrhi (Level Editor, undo, actor factories work)
#   MODE=cmd             : faster commandlet (no undo buffer / actor factories; those tests skip)
set -u
ENGINE="${UE_ENGINE_DIR:-D:/UE/UE_5.8}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${1:-$HERE/../../AgentToolkitDev.uproject}"
export AGENT_TOOLKIT_TESTS="${2:-}"
export AGENT_TOOLKIT_QUIT=1
LOG="${TMPDIR:-/tmp}/agent_toolkit_tests.log"
SCRIPT="$HERE/Content/Python/agent_toolkit/tests/run_all.py"
rm -f "$LOG"
if [ "${MODE:-editor}" = "cmd" ]; then
  "$ENGINE/Engine/Binaries/Win64/UnrealEditor-Cmd.exe" "$PROJECT" -run=pythonscript -script="$SCRIPT" \
    -unattended -nullrhi -nosplash -nop4 -abslog="$LOG" > /dev/null 2>&1
else
  timeout "${TEST_TIMEOUT:-900}" "$ENGINE/Engine/Binaries/Win64/UnrealEditor.exe" "$PROJECT" \
    -ExecutePythonScript="$SCRIPT" -unattended -nullrhi -nosplash -nop4 -NoLiveCoding -abslog="$LOG" > /dev/null 2>&1
fi
grep -E "\[AgentToolkitTests\] (FAIL|ERROR|Traceback|  File \"F|AssertionError|.*Error:)|AGENT_TOOLKIT_TEST_SUMMARY|Critical error" "$LOG" \
  | sed 's/^.*LogPython: //' | cut -c1-${MAX_COLS:-700} | head -${MAX_LINES:-120}
grep -q '"ok": true' "$LOG"
