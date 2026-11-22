#!/usr/bin/env bash
# Build a deployable IT Insight web bundle (no secrets, no virtualenvs).
#
#   ./scripts/package_release.sh [output-dir]
#
# Requires: a completed frontend build in server/static (npm run build) and a
# clean Git worktree for the version stamp. The bundle contains only runtime
# code, pinned dependencies and deployment templates.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$APP_DIR/dist}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMMIT="$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
NAME="it-insight-web-${STAMP}"

if [[ ! -f "$APP_DIR/server/static/index.html" ]]; then
  echo "ERROR: server/static/index.html is missing; run 'npm run build' in frontend/ first" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME"

cp -a "$APP_DIR/server" "$STAGE/$NAME/server"
rm -rf "$STAGE/$NAME/server/__pycache__"
cp "$APP_DIR/requirements.txt" "$APP_DIR/requirements.lock.txt" "$STAGE/$NAME/"
cp -a "$APP_DIR/deploy" "$STAGE/$NAME/deploy"
cp "$APP_DIR/README.md" "$STAGE/$NAME/README.md"

python3 - "$STAGE/$NAME/VERSION.json" "$COMMIT" <<'PY'
import json, sys
from datetime import datetime, timezone
path, commit = sys.argv[1], sys.argv[2]
json.dump(
    {
        "application": "it-insight-web",
        "version": "0.1.0",
        "source_commit": commit,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "ai_enabled": False,
        "production_deployed": False,
    },
    open(path, "w", encoding="utf-8"),
    indent=2,
)
PY

# Defence in depth: never ship a secret-bearing file even if one appears.
if find "$STAGE/$NAME" -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' \) | grep -q .; then
  echo "ERROR: refusing to package secret-bearing file" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
BUNDLE="$OUT_DIR/${NAME}.tar.gz"
tar -C "$STAGE" -czf "$BUNDLE" "$NAME"
( cd "$OUT_DIR" && sha256sum "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" )

echo "bundle: $BUNDLE"
echo "checksum: $OUT_DIR/${NAME}.tar.gz.sha256"
