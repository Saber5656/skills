#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
RUNNER="$SCRIPT_DIR/../assets/run-daily-it-news-vulnerability-check.sh"

/usr/bin/grep -F -- 'automation.local.env' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.collect.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.publish.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- 'daily-it-news.evidence-review.prompt.md' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--add-dir "$STAGING_ROOT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--add-dir "$AGENTS_GIT_DIR"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '--add-dir "$USER_GIT_DIR"' "$RUNNER" >/dev/null

COLLECTION_BLOCK="$(sed -n '/CODEX_BIN.*--search/,/COLLECTION_STATUS=/p' "$RUNNER")"
REVIEW_BLOCK="$(sed -n '/^"\$CODEX_BIN" -a never exec/,/REVIEW_STATUS=/p' "$RUNNER" | sed -n '1,/REVIEW_STATUS=/p')"
PUBLICATION_BLOCK="$(sed -n '/PUBLICATION_PROMPT_CONTENT=/,/PUBLICATION_STATUS=/p' "$RUNNER")"
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
if print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -- '--search' >/dev/null; then
  echo "publication block must not enable Web search" >&2
  exit 1
fi
if ! print -r -- "$PUBLICATION_BLOCK" | /usr/bin/grep -F -- 'network_access=false' >/dev/null; then
  echo "local publication process must have network disabled" >&2
  exit 1
fi
if ! print -r -- "$EVIDENCE_REVIEW_BLOCK" | /usr/bin/grep -F -- '--sandbox read-only' >/dev/null; then
  echo "evidence finalization review must be read-only" >&2
  exit 1
fi
/usr/bin/grep -F -- '"$REVIEW_RESULT"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$PUBLICATION_CONTEXT_FILE"' "$RUNNER" >/dev/null
/usr/bin/grep -F -- '"$ARTIFACT_PLAN"' "$RUNNER" >/dev/null
for forbidden in "/""Users/" "Library/Mobile"" Documents" "Yasu""'s Vault"; do
  if /usr/bin/grep -F -- "$forbidden" "$RUNNER" >/dev/null; then
    echo "tracked runner contains a personal path" >&2
    exit 1
  fi
done

echo "runner isolation contract: 20/20 passed"
