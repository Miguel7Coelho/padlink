#!/usr/bin/env bash
# Apply padlink's branch/tag protection and merge settings to the GitHub repository.
# Needs the GitHub CLI logged in as a repository admin:   gh auth login
#
#   scripts/apply-repo-protection.sh                     # Miguel7Coelho/padlink
#   scripts/apply-repo-protection.sh owner/repo          # another repo / fork
set -euo pipefail

REPO="${1:-Miguel7Coelho/padlink}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.github/rulesets"

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com/"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Log in first: gh auth login"; exit 1; }

echo "Repository settings for $REPO…"
# Merge commits only (keeps the --no-ff history), delete branches after merge,
# keep PR branches up to date, private vulnerability reporting on.
gh api -X PATCH "repos/$REPO" --silent \
  -F allow_merge_commit=true -F allow_squash_merge=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_update_branch=true \
  -f merge_commit_title=PR_TITLE -f merge_commit_message=PR_BODY
gh api -X PUT "repos/$REPO/private-vulnerability-reporting" --silent || \
  echo "  (couldn't enable private vulnerability reporting; turn it on under Settings → Security)"

for file in "$DIR"/*.json; do
  name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$file")
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true)
  if [[ -n "$id" ]]; then
    gh api -X PUT "repos/$REPO/rulesets/$id" --input "$file" --silent
    echo "Updated ruleset: $name"
  else
    gh api -X POST "repos/$REPO/rulesets" --input "$file" --silent
    echo "Created ruleset: $name"
  fi
done

echo
echo "Done. Review it at https://github.com/$REPO/settings/rules"
