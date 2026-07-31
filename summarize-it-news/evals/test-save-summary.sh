#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
SAVER="$SCRIPT_DIR/../scripts/save-summary.sh"
FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

CONTENT="$FIXTURE_ROOT/content.md"
print -r -- "# current run" > "$CONTENT"

BEFORE_SAVE="$(date '+%s')"
FIRST="$("$SAVER" "$FIXTURE_ROOT/archive" 2026-07-31 "$CONTENT" 2026-07-31T04:00:00+0900)"
FIRST_PATH="$(print -r -- "$FIRST" | /usr/bin/jq -r .summary_path)"
[[ "$FIRST_PATH" == "$FIXTURE_ROOT/archive/2026/07/31/SUMMARY-IT-NEWS-2026-07-31.md" ]]
[[ -f "$FIRST_PATH" ]]
[[ "$(< "$FIRST_PATH")" == "# current run" ]]
[[ "$(stat -f '%m' "$FIRST_PATH")" -ge "$BEFORE_SAVE" ]]

SECOND="$("$SAVER" "$FIXTURE_ROOT/archive" 2026-07-31 "$CONTENT" 2026-07-31T04:00:01+0900)"
SECOND_PATH="$(print -r -- "$SECOND" | /usr/bin/jq -r .summary_path)"
[[ "$SECOND_PATH" == "$FIXTURE_ROOT/archive/2026/07/31/SUMMARY-IT-NEWS-2026-07-31-2.md" ]]
[[ -f "$SECOND_PATH" ]]

set +e
FAILED="$("$SAVER" "$FIXTURE_ROOT/archive" 2026-07-31 "$FIXTURE_ROOT/missing.md" 2026-07-31T04:00:02+0900)"
FAILED_STATUS=$?
set -e
[[ "$FAILED_STATUS" -ne 0 ]]
[[ "$(print -r -- "$FAILED" | /usr/bin/jq -r .summary_status)" == "failed" ]]
[[ "$(print -r -- "$FAILED" | /usr/bin/jq -r .summary_path)" == "null" ]]

ESCAPE_ROOT="$FIXTURE_ROOT/escape-archive"
OUTSIDE="$FIXTURE_ROOT/outside"
mkdir -p "$ESCAPE_ROOT/2026/07" "$OUTSIDE"
ln -s "$OUTSIDE" "$ESCAPE_ROOT/2026/07/31"
set +e
ESCAPED="$("$SAVER" "$ESCAPE_ROOT" 2026-07-31 "$CONTENT" 2026-07-31T04:00:03+0900)"
ESCAPED_STATUS=$?
set -e
[[ "$ESCAPED_STATUS" -ne 0 ]]
[[ "$(print -r -- "$ESCAPED" | /usr/bin/jq -r .summary_status)" == "failed" ]]
[[ ! -e "$OUTSIDE/SUMMARY-IT-NEWS-2026-07-31.md" ]]

echo "summary save integration: 4/4 passed"
