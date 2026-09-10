#!/usr/bin/env bash
# Has the MUMU brand system moved since we last looked?
#
# Watches two things in mumu-solutions/mumu-branding: the tip of main, and the
# newest tag. Either one moving means the brand a consumer should be following
# has changed, and this site needs re-checking against it.
#
#   exit 0   nothing changed
#   exit 10  main and/or the newest tag moved  ->  run the review
#   exit 1   could not reach the repo (auth, network, renamed repo)
#
# State lives outside the repo on purpose: this site's deploy ships the whole
# working tree, and a state file here would be published and churn the history.
#
# Usage:
#   tools/brand_watch.sh                 # check, report, leave state alone
#   tools/brand_watch.sh --commit-state  # check, then record what we saw
#
# Record the state only once the review has actually run — otherwise a crash
# mid-review swallows the event and nobody ever hears about that release.

set -uo pipefail

REPO="${BRAND_REPO:-mumu-solutions/mumu-branding}"
STATE="${BRAND_WATCH_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/mumu-brand-watch.json}"
COMMIT_STATE=0
[[ "${1:-}" == "--commit-state" ]] && COMMIT_STATE=1

command -v gh >/dev/null || { echo "brand-watch: gh is not installed" >&2; exit 1; }

main_sha=$(gh api "repos/$REPO/commits/main" --jq .sha 2>/dev/null) || {
  echo "brand-watch: cannot read $REPO (auth? renamed? network?)" >&2; exit 1; }
latest_tag=$(gh api "repos/$REPO/tags" --jq '.[0].name // ""' 2>/dev/null)

prev_sha=""; prev_tag=""
if [[ -f "$STATE" ]]; then
  prev_sha=$(sed -n 's/.*"main_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE")
  prev_tag=$(sed -n 's/.*"latest_tag"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE")
fi

changed=0
if [[ "$main_sha" != "$prev_sha" ]]; then
  echo "main: ${prev_sha:0:7}${prev_sha:+ -> }${main_sha:0:7}"
  changed=1
fi
if [[ "$latest_tag" != "$prev_tag" ]]; then
  echo "tag:  ${prev_tag:-none} -> ${latest_tag:-none}"
  changed=1
fi

write_state() {
  mkdir -p "$(dirname "$STATE")"
  printf '{\n  "repo": "%s",\n  "main_sha": "%s",\n  "latest_tag": "%s",\n  "seen_at": "%s"\n}\n' \
    "$REPO" "$main_sha" "$latest_tag" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE"
}

if [[ $changed -eq 0 ]]; then
  echo "brand-watch: no change (main ${main_sha:0:7}, tag ${latest_tag:-none})"
  [[ $COMMIT_STATE -eq 1 ]] && write_state
  exit 0
fi

echo "brand-watch: the brand moved — review the site against $REPO@${latest_tag:-main}"
[[ $COMMIT_STATE -eq 1 ]] && write_state
exit 10
