#!/bin/sh
# File a task issue, or add to the open one with the same title: what
# .github/workflows/track.yml does when clang-repl's snapshot or release
# changes what cpp-repl's probes and tests see.
#
#   scripts/track_issue.sh "TITLE" BODY.md
#
# gh needs a token that writes issues (GITHUB_TOKEN in Actions). The board, a
# user project, takes the issue through its own auto-add workflow, since that
# token cannot add to it.
set -eu
title=$1
body=$2
number=$(TITLE="$title" gh issue list --state open --label task --limit 100 --json number,title \
           --jq 'map(select(.title == env.TITLE)) | .[0].number // empty')
if [ -n "$number" ]; then
  gh issue comment "$number" --body-file "$body" > /dev/null
  echo "added to #$number: $title"
else
  gh issue create --label task --title "$title" --body-file "$body"
fi
