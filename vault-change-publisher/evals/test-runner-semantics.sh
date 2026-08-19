#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
INTERPRETER="$SCRIPT_DIR/../scripts/interpret-automation-result.sh"
FIXTURE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

write_result() {
  local outcome="$1"
  local agents_commits="$2"
  local user_commits="$3"
  local agents_push="$4"
  local user_push="$5"
  local same_heads="$6"
  local next_action="${7:-repair state}"
  local remote="head"
  [[ "$same_heads" == "true" ]] || remote="remote"
  /usr/bin/jq -n \
    --arg outcome "$outcome" \
    --argjson ac "$agents_commits" \
    --argjson uc "$user_commits" \
    --arg ap "$agents_push" \
    --arg up "$user_push" \
    --arg remote "$remote" \
    --arg next "$next_action" '{
      mode:(if $outcome == "blocked" then "blocked" else "sweep" end),
      outcome:$outcome,phase:"publication",daily_pipeline_status:"complete",
      summary_path:"/staging/summary.md",advisory_path:"/staging/advisory.md",
      notification_result:"none",
      agents_vault:{commit_status:(if ($ac|length)>0 then "complete" else "not_started" end),commit_hashes:$ac,push_status:$ap,local_head:"head",remote_head:$remote,clean:true,publication_mode:(if $outcome == "blocked" then "blocked" else "sweep" end),deferred_cleanup:[]},
      user_vault:{commit_status:(if ($uc|length)>0 then "complete" else "not_started" end),commit_hashes:$uc,push_status:$up,local_head:"head",remote_head:$remote,clean:true,publication_mode:(if $outcome == "blocked" then "blocked" else "sweep" end),deferred_cleanup:[]},
      publication_mode:{agents_vault:(if $outcome == "blocked" then "blocked" else "sweep" end),user_vault:(if $outcome == "blocked" then "blocked" else "sweep" end)},
      deferred_cleanup:{agents_vault:[],user_vault:[]},
      evidence_finalization_commit:null,next_action:$next
    } | del(.mode)' > "$FIXTURE_ROOT/result.json"
}

assert_result() {
  local expected="$1"
  local actual
  actual="$("$INTERPRETER" "${2:-0}" "$FIXTURE_ROOT/result.json")"
  [[ "$actual" == "$expected" ]] || {
    printf 'expected=%q actual=%q\n' "$expected" "$actual" >&2
    exit 1
  }
}

missing_tool="$(JQ_BIN=/definitely/missing "$INTERPRETER" 0 "$FIXTURE_ROOT/result.json")"
[[ "$missing_tool" == $'missing_tool\t69' ]] || {
  printf 'expected missing_tool status, got %q\n' "$missing_tool" >&2
  exit 1
}

write_result blocked '[]' '[]' failed not_started true
assert_result $'blocked\t75'

write_result blocked '[]' '[]' failed not_started false
assert_result $'blocked\t75'

write_result blocked '["a"]' '[]' failed not_started false
assert_result $'invalid_result\t65'

write_result partial_publication '["a"]' '["b"]' failed failed false
assert_result $'partial_publication\t75'

write_result partial_publication '["a"]' '["preexisting"]' failed not_started false
/usr/bin/jq '
  .user_vault.publication_mode="blocked"
  | .publication_mode.user_vault="blocked"
' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/blocked-peer.json"
mv "$FIXTURE_ROOT/blocked-peer.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result partial_publication '["a"]' '["b"]' failed failed false
/usr/bin/jq '.user_vault.commit_status="failed"' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/failed-hashes.json"
mv "$FIXTURE_ROOT/failed-hashes.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

write_result partial_publication '[]' '[]' failed failed true
assert_result $'invalid_result\t65'

write_result partial_publication '[]' '[]' failed failed true
/usr/bin/jq '.agents_vault.clean=false' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/dirty.json"
mv "$FIXTURE_ROOT/dirty.json" "$FIXTURE_ROOT/result.json"
assert_result $'partial_publication\t75'

write_result success '["a"]' '["b"]' complete complete true ""
/usr/bin/jq '.evidence_finalization_commit="final" | .next_action=null' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/success.json"
mv "$FIXTURE_ROOT/success.json" "$FIXTURE_ROOT/result.json"
assert_result $'success\t0'

/usr/bin/jq '
  .user_vault.publication_mode="own_only"
  | .user_vault.clean=false
  | .user_vault.deferred_cleanup=[{"path":".codex-handoff/unsafe.md","reason":"guard rejection"}]
  | .publication_mode.user_vault="own_only"
  | .deferred_cleanup.user_vault=.user_vault.deferred_cleanup
' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/own-only.json"
mv "$FIXTURE_ROOT/own-only.json" "$FIXTURE_ROOT/result.json"
assert_result $'success\t0'

write_result success '[]' '["b"]' complete complete true ""
/usr/bin/jq '.agents_vault.commit_status="complete" | .evidence_finalization_commit="final" | .next_action=null' "$FIXTURE_ROOT/result.json" > "$FIXTURE_ROOT/invalid.json"
mv "$FIXTURE_ROOT/invalid.json" "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'

printf '{"outcome":"partial_publication"}\n' > "$FIXTURE_ROOT/result.json"
assert_result $'invalid_result\t65'
assert_result $'process_error\t42' 42

echo "runner semantic interpretation: 14/14 passed"
