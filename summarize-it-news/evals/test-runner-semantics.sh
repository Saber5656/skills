#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
INTERPRETER="$SCRIPT_DIR/../scripts/interpret-automation-result.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

write_result() {
  local outcome="$1"
  local clean="$2"
  local agents_local="$3"
  local agents_remote="$4"
  local user_local="$5"
  local user_remote="$6"
  local finalization="$7"

  /usr/bin/jq -n \
    --arg outcome "$outcome" \
    --argjson clean "$clean" \
    --arg agents_local "$agents_local" \
    --arg agents_remote "$agents_remote" \
    --arg user_local "$user_local" \
    --arg user_remote "$user_remote" \
    --arg finalization "$finalization" \
    '{
      outcome: $outcome,
      phase: "test",
      summary_path: null,
      advisory_path: null,
      notification_result: null,
      agents_vault: {
        commit_status: "complete",
        commit_hashes: ["a"],
        push_status: "complete",
        local_head: $agents_local,
        remote_head: $agents_remote,
        clean: $clean
      },
      user_vault: {
        commit_status: "complete",
        commit_hashes: ["b"],
        push_status: "complete",
        local_head: $user_local,
        remote_head: $user_remote,
        clean: $clean
      },
      evidence_finalization_commit: $finalization,
      next_action: null
    }' > "$TMP_ROOT/result.json"
}

assert_result() {
  local expected="$1"
  local process_status="$2"
  local actual
  actual="$("$INTERPRETER" "$process_status" "$TMP_ROOT/result.json")"
  [[ "$actual" == "$expected" ]] || {
    printf 'expected=%q actual=%q\n' "$expected" "$actual" >&2
    exit 1
  }
}

write_result success true aaa aaa bbb bbb final
assert_result $'success\t0' 0

write_result blocked false aaa aaa bbb bbb ""
assert_result $'blocked\t75' 0

write_result partial_publication false aaa aaa bbb ccc ""
assert_result $'partial_publication\t75' 0

printf '{"outcome":"unexpected"}\n' > "$TMP_ROOT/result.json"
assert_result $'invalid_result\t65' 0

assert_result $'process_error\t42' 42

write_result success true aaa different bbb bbb final
assert_result $'invalid_result\t65' 0

echo "runner semantic interpretation: 6/6 passed"
