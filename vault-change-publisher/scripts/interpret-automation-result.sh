#!/bin/zsh
set -u

PROCESS_STATUS="${1:-64}"
RESULT_FILE="${2:-}"

if [[ "$PROCESS_STATUS" -ne 0 ]]; then
  printf 'process_error\t%s\n' "$PROCESS_STATUS"
  exit 0
fi

if [[ -z "$RESULT_FILE" || ! -f "$RESULT_FILE" ]]; then
  printf 'invalid_result\t65\n'
  exit 0
fi

OUTCOME="$(/usr/bin/jq -r '
  def valid_vault:
    type == "object"
    and (.commit_status | IN("complete", "not_required", "failed", "not_started"))
    and (.commit_hashes | type == "array" and all(.[]; type == "string"))
    and (.push_status | IN("complete", "not_required", "failed", "not_started"))
    and (.local_head | type == "string" or . == null)
    and (.remote_head | type == "string" or . == null)
    and (.clean | type == "boolean");
  if (
    type == "object"
    and (.outcome | IN("success", "blocked", "partial_publication"))
    and (.phase | type == "string")
    and (.daily_pipeline_status | IN("complete", "blocked"))
    and (.summary_path | type == "string" or . == null)
    and (.advisory_path | type == "string" or . == null)
    and (.notification_result | type == "string" or . == null)
    and (.agents_vault | valid_vault)
    and (.user_vault | valid_vault)
    and (.evidence_finalization_commit | type == "string" or . == null)
    and (.next_action | type == "string" or . == null)
  ) then .outcome else empty end
' "$RESULT_FILE" 2>/dev/null)"

if [[ -z "$OUTCOME" ]]; then
  printf 'invalid_result\t65\n'
  exit 0
fi

if [[ "$OUTCOME" == "success" ]]; then
  if /usr/bin/jq -e '
    .daily_pipeline_status == "complete"
    and (.summary_path | type == "string" and length > 0)
    and (.advisory_path | type == "string" and length > 0)
    and .agents_vault.clean == true
    and .user_vault.clean == true
    and (.agents_vault.commit_status | IN("complete", "not_required"))
    and (.user_vault.commit_status | IN("complete", "not_required"))
    and (.agents_vault.push_status | IN("complete", "not_required"))
    and (.user_vault.push_status | IN("complete", "not_required"))
    and .agents_vault.local_head == .agents_vault.remote_head
    and .user_vault.local_head == .user_vault.remote_head
    and (.evidence_finalization_commit | type == "string" and length > 0)
  ' "$RESULT_FILE" >/dev/null 2>&1; then
    printf 'success\t0\n'
  else
    printf 'invalid_result\t65\n'
  fi
  exit 0
fi

printf '%s\t75\n' "$OUTCOME"
