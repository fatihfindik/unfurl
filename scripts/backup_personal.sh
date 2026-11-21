#!/usr/bin/env bash
# Back up the personal instance (D-155): the database and the profile photos.
#   scripts/backup_personal.sh
# Writes ~/Backups/unfurl/unfurl-YYYYMMDD-HHMMSS.dump and …-uploads.tar.gz and keeps
# the newest 14 backups. Run it before every update and about once a week.
# The instance must be running (its database answers the dump).

set -euo pipefail
# New files and folders are readable only by you: the backups hold your real data.
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/personal.sh"

KEEP=14
mkdir -p "$UNFURL_BACKUP_DIR"
chmod 700 "$UNFURL_BACKUP_DIR"   # also when the folder existed with wider rights
take_lock
# The process id makes the name unique even for two backups in the same second.
stamp="$(date +%Y%m%d-%H%M%S)-$$"
dump="$UNFURL_BACKUP_DIR/unfurl-$stamp.dump"
photos="$UNFURL_BACKUP_DIR/unfurl-$stamp-uploads.tar.gz"

# Written as *.partial first and renamed only when complete and checked: a broken
# backup never looks like a good one and never pushes a good one out of the newest
# 14. On any failure the half-written files are removed and the lock is released.
trap 'rm -f "$dump.partial" "$photos.partial"; release_lock' EXIT

echo "Backing up the database…"
compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dump.partial"
# Decode the whole file (not just its table of contents): fails if it is damaged.
compose exec -T db pg_restore -f /dev/null < "$dump.partial"
mv -n "$dump.partial" "$dump"

echo "Backing up the photos…"
# A one-off container from the app image: works even if the app itself doesn't start.
compose run --rm --no-deps -T app tar czf - -C /app uploads > "$photos.partial"
gzip -t "$photos.partial"
mv -n "$photos.partial" "$photos"

# Keep the newest $KEEP backups. Only finished ones count; the glob lists them
# oldest first (the names start with the date). A restore skips this step, so it
# never deletes the backup it is about to load.
if [[ -z "${UNFURL_SKIP_PRUNE:-}" ]]; then
  shopt -s nullglob
  dumps=("$UNFURL_BACKUP_DIR"/unfurl-*.dump)
  for ((i = 0; i < ${#dumps[@]} - KEEP; i++)); do
    rm -f -- "${dumps[i]}" "${dumps[i]%.dump}-uploads.tar.gz"
  done
fi

echo "Done: $dump"
echo "      $photos"
