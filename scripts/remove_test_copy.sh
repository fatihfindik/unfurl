#!/usr/bin/env bash
# Remove a TEST copy of the personal instance (D-190), never the real one:
#   scripts/remove_test_copy.sh unfurl-importtest
# Stops and removes its containers, then deletes its two volumes by exact name after
# you type yes. Set UNFURL_ENV_FILE if the copy used another env file.

set -euo pipefail

project="${1:-}"
# Only test names: "unfurl-" and lowercase letters, digits, - and _. The real instance
# is refused here, and below every volume must carry the test label (unfurl.test=1)
# that start_personal.sh gives a test copy's volumes.
if [[ ! "$project" =~ ^unfurl-[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Usage: scripts/remove_test_copy.sh unfurl-<name> (lowercase letters, digits, - and _)" >&2
  exit 1
fi
if [[ "$project" == "unfurl-personal" ]]; then
  echo "unfurl-personal is your real instance: this script never removes it." >&2
  exit 1
fi

# Set before personal.sh is read, so compose() works on the test project.
export UNFURL_PROJECT="$project"
source "$(dirname "${BASH_SOURCE[0]}")/personal.sh"

volumes=()
for volume in "${project}_db" "${project}_uploads"; do
  if docker volume inspect "$volume" > /dev/null 2>&1; then
    if [[ "$(docker volume inspect -f '{{ index .Labels "unfurl.test" }}' "$volume")" != "1" ]]; then
      echo "$volume has no test label (unfurl.test=1): not a test copy, nothing done." >&2
      exit 1
    fi
    volumes+=("$volume")
  fi
done

echo "This stops the test copy $project and deletes these volumes and everything in them:"
for volume in ${volumes[@]+"${volumes[@]}"}; do echo "  $volume"; done
read -r -p "Type yes to continue: " answer
if [[ "$answer" != "yes" ]]; then
  echo "Nothing changed."
  exit 1
fi
compose down   # no -v: the volumes are external and deleted below by name
for volume in ${volumes[@]+"${volumes[@]}"}; do docker volume rm "$volume" > /dev/null; done
echo "Removed the test copy $project."
