#!/usr/bin/env bash
# Create the per-run working branch for a tech-partnerships migration rehearsal.
#
# The legacy branch (`tech-partnerships`) holds only the before-state and is
# never a PR target for migration work. Each run gets a fresh branch cut from
# it, and every unit PR targets that branch:
#
#   scripts/tp-run-branch.sh <track>        # track: mongodb | databricks | aws | modernize
#
# Prints the branch name on success. Pushes the branch so PRs can target it.
set -euo pipefail

track="${1:-}"
case "$track" in
  mongodb|databricks|aws|modernize) ;;
  *) echo "usage: $0 <mongodb|databricks|aws|modernize>" >&2; exit 2 ;;
esac

branch="tp-run/${track}-$(date -u +%Y%m%dT%H%M%SZ)"
git fetch origin tech-partnerships
git branch "$branch" origin/tech-partnerships
git push origin "$branch:$branch"
echo "$branch"
