# Shared by the personal instance scripts (sourced, not run; D-155).
# Runs `docker compose` with the instance's own files: always its own env file,
# never the development .env.
#
# For testing the scripts on a separate copy, set (otherwise leave them unset):
#   UNFURL_PROJECT     compose project, default unfurl-personal
#   UNFURL_PORT        port on 127.0.0.1, default 8100
#   UNFURL_ENV_FILE    env file, default .env.personal
#   UNFURL_BACKUP_DIR  where backups go, default ~/Backups/unfurl

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNFURL_PROJECT="${UNFURL_PROJECT:-unfurl-personal}"
UNFURL_ENV_FILE="${UNFURL_ENV_FILE:-$REPO_DIR/.env.personal}"
UNFURL_BACKUP_DIR="${UNFURL_BACKUP_DIR:-$HOME/Backups/unfurl}"
export UNFURL_PROJECT UNFURL_PORT UNFURL_ENV_FILE

if [[ ! -f "$UNFURL_ENV_FILE" ]]; then
  echo "No $UNFURL_ENV_FILE. Create it first: cp .env.personal.example .env.personal" >&2
  exit 1
fi

compose() {
  docker compose -p "$UNFURL_PROJECT" -f "$REPO_DIR/docker-compose.personal.yml" \
    --env-file "$UNFURL_ENV_FILE" "$@"
}

# The two data volumes are "external" (D-190): Compose won't create them, so the start
# script does. `docker volume create` returns a volume that exists unchanged. Backup
# and restore don't call this: they need a running instance, whose volumes exist.
ensure_volumes() {
  docker volume create "${UNFURL_PROJECT}_db" > /dev/null
  docker volume create "${UNFURL_PROJECT}_uploads" > /dev/null
}

# The app runs in development mode, which skips its own production checks (D-156),
# so the start script checks the key here: at least 32 characters, like production.
check_keys() {
  local secret
  secret="$(grep -E '^SECRET_KEY=' "$UNFURL_ENV_FILE" | tail -n 1 | cut -d= -f2-)"
  if (( ${#secret} < 32 )); then
    echo "SECRET_KEY in $UNFURL_ENV_FILE must be at least 32 characters (see the command in the file)." >&2
    exit 1
  fi
}

# Only one backup or restore at a time: a weekly backup must not run in the middle
# of a restore. mkdir is atomic, so it works as a lock (macOS has no flock).
# Set UNFURL_LOCK_HELD=1 when the caller (restore) already holds it.
LOCK_DIR="$UNFURL_BACKUP_DIR/.lock"
take_lock() {
  [[ -n "${UNFURL_LOCK_HELD:-}" ]] && return 0
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another backup or restore is running ($LOCK_DIR exists)." >&2
    echo "If none is running, remove that folder and try again." >&2
    exit 1
  fi
  LOCK_TAKEN=1
}
release_lock() {
  if [[ -n "${LOCK_TAKEN:-}" ]]; then
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
}
