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
  if (
    (.outcome == "success" or .outcome == "blocked" or .outcome == "partial_publication")
    and (.agents_vault.commit_status | IN("complete", "not_required", "failed", "not_started"))
    and (.user_vault.commit_status | IN("complete", "not_required", "failed", "not_started"))
    and (.agents_vault.push_status | IN("complete", "not_required", "failed", "not_started"))
    and (.user_vault.push_status | IN("complete", "not_required", "failed", "not_started"))
  ) then .outcome else empty end
' "$RESULT_FILE" 2>/dev/null)"

if [[ -z "$OUTCOME" ]]; then
  printf 'invalid_result\t65\n'
  exit 0
fi

if [[ "$OUTCOME" == "success" ]]; then
  if /usr/bin/jq -e '
    .agents_vault.clean == true
    and .user_vault.clean == true
    and (.agents_vault.commit_status | IN("complete", "not_required"))
    and (.user_vault.commit_status | IN("complete", "not_required"))
    and (.agents_vault.push_status | IN("complete", "not_required"))
    and (.user_vault.push_status | IN("complete", "not_required"))
    and (.agents_vault.local_head | type == "string" and length > 0)
    and (.agents_vault.remote_head | type == "string" and length > 0)
    and (.user_vault.local_head | type == "string" and length > 0)
    and (.user_vault.remote_head | type == "string" and length > 0)
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
