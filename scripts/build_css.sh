#!/usr/bin/env bash
# Build the Tailwind CSS: app/static/css/input.css -> app/static/css/app.css
#
#   scripts/build_css.sh          one-off, minified build (Docker, before running the app)
#   scripts/build_css.sh --watch  rebuild on every template/CSS change (run in a second terminal)
#
# app.css is a build output and is not committed (.gitignore).
set -euo pipefail

# pytailwindcss downloads exactly this Tailwind CLI version (and checks its checksum).
export TAILWINDCSS_VERSION=v4.3.5

# Run from the repo root, wherever the script is called from.
cd "$(dirname "$0")/.."

INPUT=app/static/css/input.css
OUTPUT=app/static/css/app.css

if [[ "${1:-}" == "--watch" ]]; then
  # Not minified, so the output is readable while developing.
  uv run tailwindcss --input "$INPUT" --output "$OUTPUT" --watch
else
  uv run tailwindcss --input "$INPUT" --output "$OUTPUT" --minify
fi
