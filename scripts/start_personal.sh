#!/usr/bin/env bash
# Start (or update and restart) the personal instance (D-155):
#   scripts/start_personal.sh
# Builds the image from the current code; the app brings the database up to date
# (alembic upgrade head) before it starts. Back up before updating.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/personal.sh"
check_keys
compose up -d --build --wait
echo "Unfurl runs on http://127.0.0.1:${UNFURL_PORT:-8100}"
