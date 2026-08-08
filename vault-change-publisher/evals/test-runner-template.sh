#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
RUNNER="$SCRIPT_DIR/../assets/run-daily-it-news-vulnerability-check.sh"

/usr/bin/grep -F -- 'automation.local.env' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.collect.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'commit-reviewed-publication.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.evidence-review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--add-dir "$STAGING_ROOT"' "$RUNNER" >/dev/null

COLLECTION_BLOCK="$(sed -n '/CODEX_BIN.*--search/,/COLLECTION_STATUS=/p' "$RUNNER")"
REVIEW_BLOCK="$(sed -n '/^"\$CODEX_BIN" -a never exec/,/REVIEW_STATUS=/p' "$RUNNER" | sed -n '1,/REVIEW_STATUS=/p')"
PUBLICATION_BLOCK="$(sed -n '/^"\$LOCAL_COMMITTER"/,/PUBLICATION_STATUS=/p' "$RUNNER")"
EVIDENCE_REVIEW_BLOCK="$(sed -n '/EVIDENCE_REVIEW_PROMPT_CONTENT=/,/EVIDENCE_REVIEW_STATUS=/p' "$RUNNER")"

if print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -E 'AGENTS_GIT_DIR|USER_GIT_DIR|AGENTS_ROOT|USER_ROOT' >/dev/null; then
  echo "collection block contains Vault publication privileges" >&2
  exit 1
fi
if ! print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '-C "$STAGING_ROOT"' >/dev/null; then
  echo "collection cwd must be the run staging root" >&2
  exit 1
fi
if print -r -- "$COLLECTION_BLOCK" | /usr/bin/grep -F -- '-C "$WORKDIR"' >/dev/null; then
  echo "collection must not make the automation root writable" >&2
  exit 1
fi
if ! print -r -- "$REVIEW_BLOCK" | /usr/bin/grep -F -- '--sandbox read-only' >/dev/null; then
  echo "publication review must be read-only" >&2
  exit 1
fi
if print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -F -- 'CODEX_BIN' >/dev/null; then
  echo "local publication must not delegate Vault mutation to Codex" >&2
  exit 1
fi
if ! print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -F -- '"$REVIEW_RESULT"' >/dev/null; then
  echo "local publication must consume the approved review" >&2
  exit 1
fi
if ! print -r -- "$EVIDENCE_REVIEW_BLOCK" | /usr/bin/grep -F -- '--sandbox read-only' >/dev/null; then
  echo "evidence finalization review must be read-only" >&2
  exit 1
fi
/usr/bin/grep -F -- '"$REVIEW_RESULT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$REVIEW_RESULT_SHA256"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$PUBLICATION_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$ARTIFACT_PLAN"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'mkdir "$RUN_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'fail_run 75 collection_isolation' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$EVIDENCE_FINALIZER"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'git_diff_digest.py' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'fail_run 75 artifact_plan' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'COLLECTION_OUTPUT_ROOT="$STAGING_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'local exit_code="$1"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'FIXED_FETCHER="$WORKDIR/fetch-vault-main.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$FIXED_FETCHER" "$RUNTIME_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'SCHEMA_PROJECTOR="$WORKDIR/prepare-codex-output-schema.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'CANONICAL_VALIDATOR="$WORKDIR/validate-canonical-result.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'STANDING_TASK_STAGER="$WORKDIR/stage-standing-task.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'DIRTY_REVIEW_STAGER="$WORKDIR/stage-dirty-review-inputs.py"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '(.history_relation | IN("equal", "local_ahead"))' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'remote-ahead, diverged' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'phase=publication_preflight' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$STATE_CAPTURE" --include-local-history' "$RUNNER" >/dev/null
collection_line="$(/usr/bin/grep -n -F -- 'COLLECTION_STATUS=$?' "$RUNNER" | /usr/bin/cut -d: -f1)"
history_line="$(/usr/bin/grep -n -F -- '"$STATE_CAPTURE" --include-local-history' "$RUNNER" | /usr/bin/cut -d: -f1)"
if [[ -z "$collection_line" || -z "$history_line" || "$history_line" -le "$collection_line" ]]; then
  echo "local-only history materialization must happen after collection" >&2
  exit 1
fi
/usr/bin/grep -F -- '--arg standing_task "$STANDING_TASK_SNAPSHOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'AUTHORIZATION_TASK_SNAPSHOT="$REVIEW_INPUT_ROOT/authorization-task.md"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$RUNTIME_CONTEXT_FILE" "$REVIEW_INPUT_ROOT" authorization' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg authorization_task "$AUTHORIZATION_TASK_SNAPSHOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$RUNTIME_CONTEXT_FILE" "$PRE_COLLECTION_STATE" "$SEALED_REVIEW_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'chmod 700 "$SEALED_REVIEW_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--arg dirty_snapshot_manifest_file "$DIRTY_SNAPSHOT_MANIFEST"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--output-schema "$CODEX_COLLECTION_SCHEMA"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'INTERPRETER_PROCESS_STATUS=75' "$RUNNER" >/dev/null
if /usr/bin/grep -F -- 'fetch origin main' "$RUNNER" >/dev/null; then
  echo "runner must not trust a mutable remote name or ambiguous refspec" >&2
  exit 1
fi
if /usr/bin/grep -F -- 'local status="$1"' "$RUNNER" >/dev/null; then
  echo "fail_run must not shadow zsh's read-only status parameter" >&2
  exit 1
fi
FAIL_RUN_FUNCTION="$(sed -n '/^fail_run() {/,/^}/p' "$RUNNER")"
FAIL_RUN_ROOT="$(mktemp -d)"
if /bin/zsh -c '
  set -u
  RUN_ID=fixture-run
  STARTED_AT=2026-08-01T04:00:00+09:00
  STATUS_FILE="$1"
  eval "$2"
  fail_run 75 preflight fixture-reason
' -- "$FAIL_RUN_ROOT/status.txt" "$FAIL_RUN_FUNCTION"; then
  echo "fail_run unexpectedly returned success" >&2
  exit 1
else
  FAIL_RUN_STATUS=$?
fi
if [[ "$FAIL_RUN_STATUS" -ne 75 ]] \
  || ! /usr/bin/grep -Fx -- 'status=75' "$FAIL_RUN_ROOT/status.txt" >/dev/null \
  || ! /usr/bin/grep -Fx -- 'phase=preflight' "$FAIL_RUN_ROOT/status.txt" >/dev/null; then
  echo "fail_run did not preserve the structured status contract" >&2
  exit 1
fi
rm -rf "$FAIL_RUN_ROOT"
for forbidden in "/""Users/" "Library/Mobile"" Documents" "Yasu""'s Vault"; do
  if /usr/bin/grep -F -- "$forbidden" "$RUNNER" >/dev/null; then
    echo "tracked runner contains a personal path" >&2
    exit 1
  fi
done

echo "runner isolation contract: 30/30 passed"
