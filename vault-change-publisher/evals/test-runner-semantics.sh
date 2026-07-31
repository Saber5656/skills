#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
INTERPRETER="$SCRIPT_DIR/../scripts/interpret-automation-result.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

make_result() {
  local outcome="$1"
  local pipeline="$2"
  local same_heads="$3"
  local remote="aaa"
  [[ "$same_heads" == "true" ]] || remote="different"
  /usr/bin/jq -n --arg outcome "$outcome" --arg pipeline "$pipeline" --arg remote "$remote" '{
    outcome: $outcome, phase: "test", daily_pipeline_status: $pipeline,
    summary_path: "/tmp/summary.md", advisory_path: "/tmp/advisory.md", notification_result: "none",
    agents_vault: {commit_status:"complete",commit_hashes:["a"],push_status:"complete",local_head:"aaa",remote_head:"aaa",clean:true},
    user_vault: {commit_status:"complete",commit_hashes:["b"],push_status:"complete",local_head:"aaa",remote_head:$remote,clean:true},
    evidence_finalization_commit:"final",next_action:null
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

make_result success complete true
assert_result $'success\t0' 0
make_result blocked blocked true
assert_result $'blocked\t75' 0
make_result partial_publication complete false
assert_result $'partial_publication\t75' 0
make_result success blocked true
assert_result $'invalid_result\t65' 0
make_result success complete false
assert_result $'invalid_result\t65' 0
assert_result $'process_error\t42' 42
printf '{"outcome":"unexpected"}\n' > "$TMP_ROOT/result.json"
assert_result $'invalid_result\t65' 0
printf '{"outcome":"partial_publication"}\n' > "$TMP_ROOT/result.json"
assert_result $'invalid_result\t65' 0
printf '{"outcome":"blocked"}\n' > "$TMP_ROOT/result.json"
assert_result $'invalid_result\t65' 0

echo "runner semantic interpretation: 9/9 passed"
