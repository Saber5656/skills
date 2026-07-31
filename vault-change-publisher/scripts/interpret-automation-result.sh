#!/bin/zsh
set -u

PROCESS_STATUS="${1:-64}"
RESULT_FILE="${2:-}"
JQ_BIN="${JQ_BIN:-$(command -v jq 2>/dev/null || true)}"

if [[ -z "$JQ_BIN" || ! -x "$JQ_BIN" ]]; then
  printf 'missing_tool\t69\n'
  exit 0
fi

if [[ "$PROCESS_STATUS" -ne 0 ]]; then
  printf 'process_error\t%s\n' "$PROCESS_STATUS"
  exit 0
fi
if [[ -z "$RESULT_FILE" || ! -f "$RESULT_FILE" ]]; then
  printf 'invalid_result\t65\n'
  exit 0
fi

if ! "$JQ_BIN" -e '
  def valid_vault:
    type == "object"
    and (.commit_status | IN("complete", "not_required", "failed", "not_started"))
    and (.commit_hashes | type == "array" and all(.[]; type == "string" and length > 0))
    and (if .commit_status == "complete" then (.commit_hashes | length > 0) else true end)
    and (.push_status | IN("complete", "not_required", "failed", "not_started"))
    and (.local_head | type == "string" or . == null)
    and (.remote_head | type == "string" or . == null)
    and (.clean | type == "boolean");
  type == "object"
  and (.outcome | IN("success", "blocked", "partial_publication"))
  and (.phase | type == "string" and length > 0)
  and (.daily_pipeline_status | IN("complete", "blocked"))
  and (.summary_path | type == "string" or . == null)
  and (.advisory_path | type == "string" or . == null)
  and (.notification_result | type == "string" or . == null)
  and (.agents_vault | valid_vault)
  and (.user_vault | valid_vault)
  and (.evidence_finalization_commit | type == "string" or . == null)
  and (.next_action | type == "string" or . == null)
' "$RESULT_FILE" >/dev/null 2>&1; then
  printf 'invalid_result\t65\n'
  exit 0
fi

OUTCOME="$("$JQ_BIN" -r .outcome "$RESULT_FILE")"

case "$OUTCOME" in
  success)
    VALID='
      .daily_pipeline_status == "complete"
      and (.summary_path | type == "string" and length > 0)
      and (.advisory_path | type == "string" and length > 0)
      and (.evidence_finalization_commit | type == "string" and length > 0)
      and .next_action == null
      and all(.agents_vault, .user_vault;
        (.commit_status | IN("complete", "not_required"))
        and (.push_status | IN("complete", "not_required"))
        and .clean == true
        and (.local_head | type == "string" and length > 0)
        and .local_head == .remote_head)
    '
    ;;
  blocked)
    VALID='
      (.next_action | type == "string" and length > 0)
      and .evidence_finalization_commit == null
      and all(.agents_vault, .user_vault;
        (.commit_hashes | length == 0)
        and (.commit_status | IN("failed", "not_started", "not_required"))
        and (.push_status | IN("failed", "not_started", "not_required"))
      )
    '
    ;;
  partial_publication)
    VALID='
      (.next_action | type == "string" and length > 0)
      and (any(.agents_vault, .user_vault;
        (.commit_hashes | length > 0)
        or .push_status == "complete"
        or .local_head != .remote_head
        or .clean == false))
    '
    ;;
esac

if ! "$JQ_BIN" -e "$VALID" "$RESULT_FILE" >/dev/null 2>&1; then
  printf 'invalid_result\t65\n'
  exit 0
fi

if [[ "$OUTCOME" == "success" ]]; then
  printf 'success\t0\n'
else
  printf '%s\t75\n' "$OUTCOME"
fi
