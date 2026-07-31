#!/bin/zsh
set -u

export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

AUTOMATION_ID="${1:-}"
if [[ -z "$AUTOMATION_ID" ]]; then
  echo "usage: $0 <automation-id>" >&2
  exit 64
fi

AUTOMATION_ROOT="/Users/takagiyasushi/AutomationWorkspaces/codex"
WORKDIR="$AUTOMATION_ROOT/$AUTOMATION_ID"
PROMPT_FILE="$WORKDIR/prompt.md"
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "prompt file not found: $PROMPT_FILE" >&2
  exit 66
fi

DATE_DIR="$(date '+%Y-%m-%d')"
RUN_ID="$(date '+%Y%m%dT%H%M%S%z')"
LOG_DIR="$WORKDIR/logs/$DATE_DIR"
mkdir -p "$LOG_DIR"
EVENT_LOG="$LOG_DIR/$RUN_ID.events.jsonl"
STDERR_LOG="$LOG_DIR/$RUN_ID.stderr.log"
LAST_MESSAGE="$LOG_DIR/$RUN_ID.last-message.md"
STATUS_FILE="$WORKDIR/last-status.txt"
STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
PROMPT_CONTENT="$(< "$PROMPT_FILE")"

WRITE_ARGS=(
  --add-dir "/Users/takagiyasushi/dev/skills"
  --add-dir "/Users/takagiyasushi/dotfiles"
  --add-dir "/Users/takagiyasushi/Library/Mobile Documents/iCloud~md~obsidian/Documents/Yasu's Vault"
  --add-dir "/Users/takagiyasushi/Library/Mobile Documents/iCloud~md~obsidian/Documents/Agents-Vault"
)
EXTRA_ARGS=()

if [[ "$AUTOMATION_ID" == "daily-it-news-vulnerability-check" ]]; then
  RESULT_SCHEMA="$WORKDIR/automation-result.schema.json"
  RESULT_INTERPRETER="$WORKDIR/interpret-automation-result.sh"
  if [[ ! -f "$RESULT_SCHEMA" ]]; then
    echo "result schema not found: $RESULT_SCHEMA" >&2
    exit 66
  fi
  if [[ ! -x "$RESULT_INTERPRETER" ]]; then
    echo "result interpreter not executable: $RESULT_INTERPRETER" >&2
    exit 66
  fi

  PROMPT_CONTENT="Runtime context:
- run_id: $RUN_ID
- started_at: $STARTED_AT
- result_schema: $RESULT_SCHEMA

$(< "$PROMPT_FILE")"

  # Web入力を扱うdaily jobでは、skillsとdotfilesをread-onlyのままにする。
  WRITE_ARGS=(
    --add-dir "/Users/takagiyasushi/Library/Mobile Documents/iCloud~md~obsidian/Documents/Yasu's Vault"
    --add-dir "/Users/takagiyasushi/Library/Mobile Documents/iCloud~md~obsidian/Documents/Agents-Vault"
    --add-dir "/Users/takagiyasushi/dev/_git/Agents-Vault.git"
    --add-dir "/Users/takagiyasushi/dev/_git/Yasu-Vault.git"
  )
  EXTRA_ARGS=(
    -c 'sandbox_workspace_write.network_access=true'
    --output-schema "$RESULT_SCHEMA"
  )
fi

/opt/homebrew/bin/codex --search -a never exec \
  --ignore-user-config \
  --ephemeral \
  --skip-git-repo-check \
  --sandbox workspace-write \
  -C "$WORKDIR" \
  "${WRITE_ARGS[@]}" \
  "${EXTRA_ARGS[@]}" \
  -c 'notify=[]' \
  --json \
  --output-last-message "$LAST_MESSAGE" \
  "$PROMPT_CONTENT" > "$EVENT_LOG" 2> "$STDERR_LOG"
PROCESS_STATUS=$?
SEMANTIC_STATUS="not_applicable"
STATUS=$PROCESS_STATUS

if [[ "$AUTOMATION_ID" == "daily-it-news-vulnerability-check" ]]; then
  RESULT_INTERPRETATION="$("$RESULT_INTERPRETER" "$PROCESS_STATUS" "$LAST_MESSAGE")"
  SEMANTIC_STATUS="${RESULT_INTERPRETATION%%$'\t'*}"
  STATUS="${RESULT_INTERPRETATION#*$'\t'}"
fi
ENDED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"

{
  print -r -- "run_id=$RUN_ID"
  print -r -- "started_at=$STARTED_AT"
  print -r -- "ended_at=$ENDED_AT"
  print -r -- "status=$STATUS"
  print -r -- "process_status=$PROCESS_STATUS"
  print -r -- "semantic_status=$SEMANTIC_STATUS"
  print -r -- "event_log=$EVENT_LOG"
  print -r -- "stderr_log=$STDERR_LOG"
  print -r -- "last_message=$LAST_MESSAGE"
} > "$STATUS_FILE"

exit "$STATUS"
