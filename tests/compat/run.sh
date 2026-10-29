#!/usr/bin/env bash
# Live check: does Hestia's pause hold next to a connector and another plugin?
#
# Runs real `claude -p` sessions with three things loaded together:
#   - Hestia, from this checkout
#   - fake_slack, a stand-in connector (fake_mcp.py) that leaves a marker file
#     whenever its tool really runs
#   - other/, a stand-in plugin whose PreToolUse hook approves every tool call
#     and whose SessionStart hook adds its own context
#
# Every scenario gets its own throwaway HESTIA_HOME, so nothing on this machine
# is paused, and `done` always runs with --keep-agents, so nothing is signalled.
# It needs the `claude` CLI and costs a few cents per run (one Haiku call per
# scenario). Never run it with --permission-mode bypassPermissions.
#
# Usage: tests/compat/run.sh            (VERBOSE=1 also prints Claude's replies)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
H="$REPO/plugins/hestia/scripts/hestia.py"
MODEL="${MODEL:-haiku}"
TIMEOUT="$(command -v timeout || command -v gtimeout || true)"
passed=0; total=0

in_30_min() { date -v+30M +%H:%M 2>/dev/null || date -d '+30 min' +%H:%M; }

fresh() {
  S="$(mktemp -d)"; mkdir -p "$S/home" "$S/logs" "$S/work"
  export HESTIA_HOME="$S/home" HESTIA_LOG_DIR="$S/logs"
  export MARKER="$S/connector_marker" PING_MARKER="$S/ping_marker"
  printf '{"mcpServers":{"fake_slack":{"command":"python3","args":["%s/fake_mcp.py"],"env":{"MARKER":"%s"}}}}' \
    "$HERE" "$MARKER" > "$S/mcp.json"
}

pause_now() {
  (cd "$S/work" && python3 "$H" done --until "$(in_30_min)" --keep-agents --note "compat test day" >/dev/null)
}

night_passes() {  # move the pause's end into the past, as if it ran out overnight
  python3 - "$HESTIA_HOME/pause.json" <<'PY'
import json, sys
from datetime import datetime, timedelta
path = sys.argv[1]
data = json.load(open(path))
data["until"] = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
json.dump(data, open(path, "w"))
PY
}

handover_taken() {  # "yes" once Hestia has handed this list to a session
  python3 - "$HESTIA_HOME/handover.json" <<'PY'
import json, sys
try:
    print("yes" if json.load(open(sys.argv[1])).get("consumed") else "no")
except (OSError, ValueError):
    print("no")
PY
}

ask() {
  REPLY_TEXT="$(cd "$S/work" && env -u CLAUDE_CODE_SESSION_ID $TIMEOUT ${TIMEOUT:+240} claude -p "$1" \
      --session-id "$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --plugin-dir "$REPO/plugins/hestia" --plugin-dir "$HERE/other" --mcp-config "$S/mcp.json" \
      --model "$MODEL" --output-format json \
      --allowedTools "Bash,Agent,mcp__fake_slack__send_message" 2>&1 \
    | python3 -c 'import json, sys
raw = sys.stdin.read()
try:
    print((json.loads(raw[raw.index("{"):]).get("result") or "").replace("\n", " "))
except ValueError:
    print("no reply: " + raw[:200].replace("\n", " "))')"
  [ -n "${VERBOSE:-}" ] && echo "     claude: ${REPLY_TEXT:0:300}"
}

check() {  # check <name> <0 for pass>
  total=$((total + 1))
  if [ "$2" -eq 0 ]; then passed=$((passed + 1)); echo "PASS $1"; else echo "FAIL $1"; fi
}

fresh
ask "Use the send_message tool on the fake_slack MCP server to send the text 'baseline'. Then reply DONE."
[ -e "$MARKER" ]; check "not paused: the connector's tool runs as normal" $?

fresh; pause_now
ask "Use the send_message tool on the fake_slack MCP server to send the text 'after hours'."
[ ! -e "$MARKER" ]; check "paused: the connector's tool is blocked, though another plugin approves it" $?

fresh; pause_now
ask "Use the Agent tool to start a general-purpose subagent whose only job is to run: touch $S/sub_marker . Do not run it yourself."
[ ! -e "$S/sub_marker" ]; check "paused: a subagent can't run commands" $?

fresh; pause_now
ask "/other:ping"
[ ! -e "$PING_MARKER" ]; check "paused: another plugin's command is blocked" $?

fresh; pause_now
ask "/hestia:back"
[ ! -e "$HESTIA_HOME/pause.json" ]; check "paused: /hestia:back still gets you out" $?

fresh; pause_now; night_passes
MORNING="Without using any tools, answer in two lines: 1) the codeword OTHER_PLUGIN gave you, 2) the note at the top of yesterday's Hestia list, or 'none'."
ask "$MORNING"  # `claude -p` is a scripted run, so the handover waits for a person
[ "$(handover_taken)" = "no" ]; check "morning: a scripted run doesn't take the handover" $?
export HESTIA_SESSION_KIND=interactive  # stands in for a person opening Claude Code
ask "$MORNING"
unset HESTIA_SESSION_KIND
# Hestia's own record says whether the handover was given. Claude is told not to
# recite the list, so only the other plugin's codeword is checked in the reply.
case "$(handover_taken) $REPLY_TEXT" in "yes "*BLUEBERRY*) ok=0 ;; *) ok=1 ;; esac
check "morning: a person gets Hestia's handover and the other plugin's context" $ok

echo
echo "$passed/$total checks passed"
[ "$passed" -eq "$total" ]
