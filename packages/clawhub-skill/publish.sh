#!/usr/bin/env bash
# Publish the Caspian skill to ClawHub (https://clawhub.ai/trycaspian/skills/caspian).
#
# The skill body is the LIVE gateway doc (api.trycaspianai.com/SKILL.md) — the
# single source of truth for channel availability. Rerun this after any gateway
# doc change so the ClawHub listing never drifts.
#
# Usage: ./publish.sh <version> [--dry-run]
#   e.g. ./publish.sh 1.0.2
# Requires: clawhub CLI, logged in to an account that admins @trycaspian.
set -euo pipefail

VERSION="${1:?usage: ./publish.sh <version> [--dry-run]}"
shift

DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="$(mktemp -d)/caspian-skill"
mkdir -p "$BUILD"

curl -fsS https://api.trycaspianai.com/SKILL.md > "$BUILD/body.md"
cat "$DIR/frontmatter.md" <(echo) "$BUILD/body.md" > "$BUILD/SKILL.md"
rm "$BUILD/body.md"

clawhub --workdir "$(dirname "$BUILD")" publish caspian-skill \
  --slug caspian --name "Caspian" --owner trycaspian \
  --version "$VERSION" \
  --changelog "Sync with live gateway SKILL.md." \
  --topics "messaging,slack,discord,telegram,email" \
  "$@"
