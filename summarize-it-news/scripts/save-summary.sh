#!/bin/zsh
set -eu
set -o noclobber

ARCHIVE_ROOT="${1:-}"
SUMMARY_DATE="${2:-}"
CONTENT_FILE="${3:-}"
STARTED_AT="${4:-}"

fail() {
  /usr/bin/jq -n --arg reason "$1" '{
    summary_status: "failed",
    reason: $reason,
    summary_path: null
  }'
  exit 1
}

[[ "$ARCHIVE_ROOT" == /* ]] || fail "archive root must be absolute"
[[ "$SUMMARY_DATE" == <->-<->-<-> ]] || fail "summary date must use YYYY-MM-DD"
[[ -f "$CONTENT_FILE" && -r "$CONTENT_FILE" ]] || fail "content file is not readable"
[[ -n "$STARTED_AT" ]] || fail "collection started_at is required"

YEAR="${SUMMARY_DATE%%-*}"
REST="${SUMMARY_DATE#*-}"
MONTH="${REST%%-*}"
DAY="${SUMMARY_DATE##*-}"
[[ ${#YEAR} -eq 4 && ${#MONTH} -eq 2 && ${#DAY} -eq 2 ]] || fail "summary date must use YYYY-MM-DD"

/bin/mkdir -p "$ARCHIVE_ROOT" || fail "could not create archive root"
[[ ! -L "$ARCHIVE_ROOT" && -d "$ARCHIVE_ROOT" ]] || fail "archive root must be a real directory"
CANONICAL_ROOT="$(cd -P "$ARCHIVE_ROOT" 2>/dev/null && pwd -P)" || fail "could not resolve archive root"

TARGET_DIR="$ARCHIVE_ROOT"
for COMPONENT in "$YEAR" "$MONTH" "$DAY"; do
  NEXT="$TARGET_DIR/$COMPONENT"
  if [[ -e "$NEXT" || -L "$NEXT" ]]; then
    [[ ! -L "$NEXT" && -d "$NEXT" ]] || fail "target path contains a symlink or non-directory"
  else
    /bin/mkdir "$NEXT" || fail "could not create target directory"
  fi
  CANONICAL_NEXT="$(cd -P "$NEXT" 2>/dev/null && pwd -P)" || fail "could not resolve target directory"
  case "$CANONICAL_NEXT" in
    "$CANONICAL_ROOT"/*) ;;
    *) fail "target directory escapes archive root" ;;
  esac
  TARGET_DIR="$NEXT"
done

BASE="$TARGET_DIR/SUMMARY-IT-NEWS-$SUMMARY_DATE"
SUFFIX=""
INDEX=1
while true; do
  TARGET="$BASE$SUFFIX.md"
  if { /bin/cat "$CONTENT_FILE" > "$TARGET" } 2>/dev/null; then
    break
  fi
  [[ -e "$TARGET" ]] || fail "could not write summary"
  INDEX=$((INDEX + 1))
  SUFFIX="-$INDEX"
done

COMPLETED_AT="$(/bin/date '+%Y-%m-%dT%H:%M:%S%z')"
/usr/bin/jq -n \
  --arg path "$TARGET" \
  --arg started "$STARTED_AT" \
  --arg completed "$COMPLETED_AT" '{
    summary_status: "created",
    summary_path: $path,
    collection_started_at: $started,
    collection_completed_at: $completed
  }'
