#!/usr/bin/env bash
# Persist one workflow's outputs onto the branch without losing them to a push race.
#
#   scripts/persist.sh <workflow> "<commit message>"
#
# signal-sonic lesson (runs 69 and 72, a night each): a plain push at the end of a long
# job loses the job when anything else landed meanwhile. So copy the outputs aside,
# reset to the new head, lay them back and retry. Laying files back is only safe because
# every file under data/ is owned by exactly one workflow (adtone/guard.py), and the
# guard refuses anything this workflow does not own before a commit is attempted.
set -euo pipefail

WF="$1"
MSG="$2"
BR="${GITHUB_REF_NAME:-main}"

git config user.name "adtone-${WF}-bot"
git config user.email "actions@users.noreply.github.com"

mapfile -t CHANGED < <(git status --porcelain --untracked-files=all | sed -E 's/^.{3}//' | sed -E 's/^"(.*)"$/\1/')
if [ "${#CHANGED[@]}" -eq 0 ]; then
  echo "nothing to persist"
  exit 0
fi

python -m adtone.guard "$WF" "${CHANGED[@]}"

STASH="$(mktemp -d)"
for f in "${CHANGED[@]}"; do
  if [ -f "$f" ]; then
    mkdir -p "$STASH/$(dirname "$f")"
    cp -p "$f" "$STASH/$f"
  fi
done

for attempt in 1 2 3 4 5; do
  git fetch -q origin "$BR"
  git reset -q --hard "origin/$BR"
  (cd "$STASH" && find . -type f) | while read -r f; do
    mkdir -p "$(dirname "$f")"
    cp -p "$STASH/$f" "$f"
  done
  git add -A -- "${CHANGED[@]}"
  if git diff --staged --quiet; then
    echo "nothing changed against the current head"
    exit 0
  fi
  git commit -q -m "$MSG $(date -u +%Y-%m-%dT%H:%MZ)"
  if git push -q origin "HEAD:$BR"; then
    echo "persisted on attempt $attempt"
    exit 0
  fi
  echo "push race on attempt $attempt, replaying onto the new head"
  sleep $((attempt * 5))
done
echo "::error::could not persist after 5 attempts; this run's outputs are lost"
exit 1
