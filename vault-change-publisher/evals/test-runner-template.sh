#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
RUNNER="$SCRIPT_DIR/../assets/run-codex-automation.sh"

/usr/bin/grep -F -- '- run_id: $RUN_ID' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '- started_at: $STARTED_AT' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '- result_schema: $RESULT_SCHEMA' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--output-schema "$RESULT_SCHEMA"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'RESULT_INTERPRETATION="$("$RESULT_INTERPRETER" "$PROCESS_STATUS" "$LAST_MESSAGE")"' "$RUNNER" >/dev/null

DAILY_SCOPE="$(/usr/bin/awk '
  /# Web入力を扱うdaily job/ {capture=1}
  capture {print}
  capture && /EXTRA_ARGS=\(/ {exit}
' "$RUNNER")"

print -r -- "$DAILY_SCOPE" | /usr/bin/grep -F -- "/dev/_git/Agents-Vault.git" >/dev/null
print -r -- "$DAILY_SCOPE" | /usr/bin/grep -F -- "/dev/_git/Yasu-Vault.git" >/dev/null
if print -r -- "$DAILY_SCOPE" | /usr/bin/grep -E '/dev/skills|/dotfiles' >/dev/null; then
  echo "daily scope must not make skills or dotfiles writable" >&2
  exit 1
fi

echo "runner template contract: 6/6 passed"
