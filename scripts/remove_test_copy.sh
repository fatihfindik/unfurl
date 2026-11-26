#!/usr/bin/env bash
# Remove a TEST copy of the personal instance (D-190), never the real one:
#   scripts/remove_test_copy.sh unfurl-importtest
# Stops and removes its containers, then deletes its two volumes by exact name after
# you type yes. Set UNFURL_ENV_FILE if the copy used another env file.

set -euo pipefail

project="${1:-}"
# Only simple lowercase names; the real instance is refused.
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Usage: scripts/remove_test_copy.sh <test project name> (lowercase letters, digits, - and _)" >&2
  exit 1
fi
if [[ "$project" == "unfurl-personal" ]]; then
  echo "unfurl-personal is your real instance: this script never removes it." >&2
  exit 1
fi

# Set before personal.sh is read, so compose() works on the test project.
export UNFURL_PROJECT="$project"
source "$(dirname "${BASH_SOURCE[0]}")/personal.sh"

compose down   # no -v: the volumes are external and deleted below by name

volumes=("${project}_db" "${project}_uploads")
echo "This deletes these volumes and everything in them:"
for volume in "${volumes[@]}"; do
  if docker volume inspect "$volume" > /dev/null 2>&1; then echo "  $volume"; fi
done
read -r -p "Type yes to continue: " answer
if [[ "$answer" != "yes" ]]; then
  echo "Nothing deleted (the containers are stopped)."
  exit 1
fi
for volume in "${volumes[@]}"; do
  if docker volume inspect "$volume" > /dev/null 2>&1; then docker volume rm "$volume" > /dev/null; fi
done
echo "Removed the test copy $project."
