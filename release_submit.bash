#!/bin/bash
# release_submit.bash — build aichallenge_submit.tar.gz and publish it as a GitHub Release.
#
# Usage:
#   ./release_submit.bash [tag] [options]
#
# Arguments:
#   tag                 Release tag (default: aichallenge-$(date +%Y-%m%d), e.g. aichallenge-2026-0620)
#
# Options:
#   --title <title>     Release title (default: same as tag)
#   --notes <notes>     Release notes body (default: auto-generated)
#   --draft             Create the release as a draft
#   -h, --help          Show this help
#
# Behavior:
#   - Rebuilds submit/aichallenge_submit.tar.gz via create_submit_file.bash so the
#     published asset is always up to date.
#   - If the release tag does not exist, gh creates it on the current HEAD.
#   - If the release already exists, only the asset is re-uploaded (--clobber).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ASSET="submit/aichallenge_submit.tar.gz"

usage() {
    # Print the leading comment block (skip the shebang, stop at the first blank/code line).
    awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
}

TAG=""
TITLE=""
NOTES=""
DRAFT_FLAG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
    --title)
        TITLE="${2:?--title requires a value}"
        shift 2
        ;;
    --notes)
        NOTES="${2:?--notes requires a value}"
        shift 2
        ;;
    --draft)
        DRAFT_FLAG="1"
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    -*)
        echo "Error: unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    *)
        if [[ -z $TAG ]]; then
            TAG="$1"
            shift
        else
            echo "Error: unexpected argument: $1" >&2
            exit 1
        fi
        ;;
    esac
done

TAG="${TAG:-aichallenge-$(date +%Y-%m%d)}"
TITLE="${TITLE:-$TAG}"
NOTES="${NOTES:-aichallenge_submit.tar.gz release ($TAG)}"

# --- Preconditions -----------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI (gh) is not installed. See https://cli.github.com/" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: not logged in to GitHub. Run: gh auth login" >&2
    exit 1
fi

# --- Build the submission archive --------------------------------------------
echo "==> Building submission archive..."
./create_submit_file.bash

if [[ ! -f $ASSET ]]; then
    echo "Error: expected asset not found: $ASSET" >&2
    exit 1
fi

# --- Publish the release -----------------------------------------------------
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "==> Release '$TAG' already exists; re-uploading asset (overwrite)..."
    gh release upload "$TAG" "$ASSET" --clobber
else
    echo "==> Creating release '$TAG'..."
    create_args=(--title "$TITLE" --notes "$NOTES")
    if [[ -n $DRAFT_FLAG ]]; then
        create_args+=(--draft)
    fi
    gh release create "$TAG" "$ASSET" "${create_args[@]}"
fi

url="$(gh release view "$TAG" --json url --jq .url)"
echo "==> Done: $url"
