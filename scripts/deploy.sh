#!/usr/bin/env bash
# Versioned deploy (T-1106). HUMAN ONLY for real deploys; the agent only runs --dry-run.
#
#   scripts/deploy.sh [--dry-run] [--version live-vX.Y] [--message "what changed"]
#
# Steps: (1) clean git tree  (2) make lint test  (3) ask version + description
#        (4) CHANGELOG "Versions" entry (date, tag, sha, config_hash, model_version, description) + commit
#        (5) git tag + push tags  (6) docker compose build with GIT_SHA, VERSION_TAG in env  (7) docker compose up -d
set -euo pipefail

DRY_RUN=0
VERSION=""
MESSAGE=""
PYTHON="${PYTHON:-python}"

usage() { sed -n '2,9p' "$0"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --version) VERSION="${2:-}"; shift ;;
        --message) MESSAGE="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

cd "$(git rev-parse --show-toplevel)"

# (1) clean tree
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "working tree is not clean: commit or stash first" >&2
    exit 1
fi

# (2) checks (also in dry-run: a release must be green)
make lint test

# (3) version and description
if [[ -z "$VERSION" ]]; then
    read -r -p "version (live-vX.Y): " VERSION
fi
if [[ ! "$VERSION" =~ ^live-v[0-9]+\.[0-9]+$ ]]; then
    echo "invalid version '$VERSION' (expected live-vX.Y)" >&2
    exit 2
fi
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    echo "tag $VERSION already exists" >&2
    exit 2
fi
if [[ -z "$MESSAGE" ]]; then
    read -r -p "what changed: " MESSAGE
fi

# (4) CHANGELOG entry + commit
SHA="$(git rev-parse --short HEAD)"
CONFIG_HASH="$("$PYTHON" -c 'from siavash.config import config_hash, load_config; print(config_hash(load_config("config")))')"
MODEL_VERSION="$("$PYTHON" -c 'from siavash.config import load_config; m = load_config("config").strategy.ml; print(m.model_version if m.enabled and m.model_version else "none")')"
DATE="$(date -u +%Y-%m-%d)"
ENTRY="- **${VERSION}** (${DATE}) — sha \`${SHA}\`, config \`${CONFIG_HASH}\`, model \`${MODEL_VERSION}\`: ${MESSAGE}"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] CHANGELOG.md ## Versions += ${ENTRY}"
else
    "$PYTHON" - "$ENTRY" <<'PY'
import sys
from pathlib import Path

entry = sys.argv[1]
p = Path("CHANGELOG.md")
text = p.read_text(encoding="utf-8").replace("_No live versions yet._\n", "")
marker = "## Versions\n"
if marker not in text:
    text = text.rstrip("\n") + "\n\n" + marker
head, tail = text.split(marker, 1)
p.write_text(head + marker + "\n" + entry + "\n" + tail.lstrip("\n"), encoding="utf-8")
PY
    git add CHANGELOG.md
    git commit -m "release: ${VERSION}"
fi

# (5) tag + push
run git tag -a "$VERSION" -m "$VERSION: $MESSAGE"
run git push
run git push --tags

# (6) build with the release sha, (7) start
BUILD_SHA="$(git rev-parse --short HEAD)"
export GIT_SHA="$BUILD_SHA"
export VERSION_TAG="$VERSION"
run docker compose build --build-arg "GIT_SHA=${BUILD_SHA}"
run docker compose up -d

echo "deployed ${VERSION} (sha ${BUILD_SHA})$([[ "$DRY_RUN" -eq 1 ]] && echo ' [dry-run: nothing changed]')"
