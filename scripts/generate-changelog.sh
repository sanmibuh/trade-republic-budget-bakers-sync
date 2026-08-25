#!/usr/bin/env bash
# Generate a new CHANGELOG.md section and prepend it to the file.
#
# Usage:
#   generate-changelog.sh <NEW_VERSION> <CURRENT_VERSION> <REPO> <CHANGELOG_FILE>
#
# Arguments:
#   NEW_VERSION      - The version being released (e.g. 9.0.0)
#   CURRENT_VERSION  - The previous version (e.g. 8.0.0)
#   REPO             - GitHub repository in owner/name format (e.g. sanmibuh/my-repo)
#   CHANGELOG_FILE   - Path to the CHANGELOG.md file to update
#
# The script expects a git tag "v${CURRENT_VERSION}" to exist.
# It exits non-zero if the tag is missing or if any command fails.

set -euo pipefail

NEW="$1"
CURRENT="$2"
REPO="$3"
CHANGELOG_FILE="$4"
BASE_URL="https://github.com/${REPO}"
TODAY=$(date -u +%Y-%m-%d)

# Validate that the previous tag exists so git log does not silently produce empty output.
if ! git tag --list "v${CURRENT}" | grep -q .; then
    echo "ERROR: tag v${CURRENT} not found — cannot generate CHANGELOG range v${CURRENT}..HEAD" >&2
    exit 1
fi

# Build "* title — [#N](url)" lines from merge commits since the last tag.
ITEMS=""
while IFS= read -r subject; do
    [ -z "$subject" ] && continue
    if echo "$subject" | grep -qE '\(#[0-9]+\)$'; then
        PR_NUM=$(echo "$subject" | grep -oE '#[0-9]+' | tail -1 | tr -d '#')
        TITLE=$(echo "$subject" | sed -E 's/ \(#[0-9]+\)$//')
        ITEMS="${ITEMS}* ${TITLE} — [#${PR_NUM}](${BASE_URL}/pull/${PR_NUM})\n"
    else
        ITEMS="${ITEMS}* ${subject}\n"
    fi
done < <(git log "v${CURRENT}..HEAD" --pretty=format:"%s")

if [ -z "$ITEMS" ]; then
    ITEMS="<!-- add release notes here -->\n"
fi

FULL_CHANGELOG="**Full Changelog**: ${BASE_URL}/compare/v${CURRENT}...v${NEW}"

printf -v NEW_SECTION "## [%s] - %s\n\n### What's Changed\n%s\n%s\n\n" \
    "$NEW" "$TODAY" "$(printf '%b' "$ITEMS")" "$FULL_CHANGELOG"

awk -v section="$NEW_SECTION" '
    /^The format is based on/ && !inserted {
        print
        print ""
        printf "%s", section
        inserted=1
        next
    }
    { print }
' "$CHANGELOG_FILE" > "${CHANGELOG_FILE}.tmp" && mv "${CHANGELOG_FILE}.tmp" "$CHANGELOG_FILE"
