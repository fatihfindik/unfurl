#!/usr/bin/env bash
# Restore the personal instance from a backup (D-155). REPLACES all current data.
#   scripts/restore_personal.sh ~/Backups/unfurl/unfurl-YYYYMMDD-HHMMSS-NNNN.dump
# The photos come from the matching …-uploads.tar.gz next to it, if it exists.
# Before anything is changed, the backup is checked and the current data is backed up.

set -euo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/personal.sh"

dump="${1:-}"
if [[ -z "$dump" || ! -f "$dump" ]]; then
  echo "Usage: scripts/restore_personal.sh <backup .dump file>" >&2
  exit 1
fi
photos="${dump%.dump}-uploads.tar.gz"

mkdir -p "$UNFURL_BACKUP_DIR"
take_lock
# The decoded backup inside the db container holds real data: always removed.
cleanup() {
  compose exec -T db rm -f /tmp/restore.dump /tmp/restore.sql 2>/dev/null || true
  release_lock
}
trap cleanup EXIT

# 1. Check the whole backup before touching anything: decode it to SQL (this reads
# every byte, so a damaged file fails here) and test the photo archive.
echo "Checking the backup…"
compose exec -T db sh -c 'cat > /tmp/restore.dump && pg_restore --no-owner -f /tmp/restore.sql /tmp/restore.dump' < "$dump"
if [[ -f "$photos" ]]; then
  gzip -t "$photos"
  tar tzf "$photos" > /dev/null
else
  echo "Note: no photo archive ($photos); the current photos stay as they are."
fi

echo
echo "This replaces ALL data of the personal instance ($UNFURL_PROJECT) with:"
echo "  $dump"
read -r -p "Type yes to continue: " answer
if [[ "$answer" != "yes" ]]; then
  echo "Nothing changed."
  exit 1
fi

# 2. A way back: a backup of what is there now. No pruning (it could delete the
# backup being restored) and no second lock (this script holds it).
UNFURL_SKIP_PRUNE=1 UNFURL_LOCK_HELD=1 "$(dirname "${BASH_SOURCE[0]}")/backup_personal.sh"

# 3. The database, in ONE transaction: empty the schema and load the backup. If any
# statement fails, everything is rolled back and the current data stays.
# (Emptying first, not pg_restore --clean: tables from newer migrations would stay
# and the app could not start.)
compose stop app
compose exec -T db sh -c 'PGOPTIONS="-c client_min_messages=warning" psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" -f /tmp/restore.sql' > /dev/null

# 4. The photos (the archive was checked above; the pre-restore backup has the old ones).
if [[ -f "$photos" ]]; then
  compose run --rm --no-deps -T app sh -c 'find /app/uploads -mindepth 1 -delete && tar xzf - -C /app' < "$photos"
fi

# 5. Start again: the app runs `alembic upgrade head` first, so an older backup is
# brought up to the current version.
compose up -d --wait app
echo "Restored from $dump"
