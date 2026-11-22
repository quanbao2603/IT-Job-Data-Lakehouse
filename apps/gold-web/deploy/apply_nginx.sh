#!/usr/bin/env bash
# Apply the /insights/ location to the existing Nginx site (requires root).
#
#   sudo bash deploy/apply_nginx.sh
#
# Behaviour:
#   * idempotent: exits if the location already exists;
#   * backs up the site file before editing;
#   * inserts the location before the TLS listener line of the womtech.io.vn server;
#   * runs `nginx -t`; restores the backup and exits non-zero on failure;
#   * reloads Nginx only after a successful test;
#   * probes the public route locally and prints the rollback command.
#
# Access: the initial deployment reuses the existing Nginx basic-auth account
# (/etc/nginx/.htpasswd) so the page is not public by accident. Remove the two
# auth_basic lines below to publish the page openly.
set -euo pipefail

SITE="${SITE:-/etc/nginx/sites-enabled/9router}"
BACKUP_DIR="/home/womcloud/services/it-insight/backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNIPPET="$(mktemp)"
trap 'rm -f "$SNIPPET"' EXIT

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo bash $0)" >&2
  exit 1
fi
if [[ ! -f "$SITE" ]]; then
  echo "ERROR: site file not found: $SITE" >&2
  exit 1
fi
if grep -q 'location /insights/' "$SITE"; then
  echo "OK: /insights/ location already present in $SITE"
  exit 0
fi

cat > "$SNIPPET" <<'NGINX'
    # IT Insight web (read-only Gold API + SPA). Prefix is stripped by proxy_pass.
    location = /insights {
        return 301 /insights/;
    }

    location /insights/ {
        # Reuse the existing admin account until a dedicated web account exists.
        auth_basic "IT Insight";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:8010/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }
NGINX

mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/9router.$STAMP.conf"
cp -a "$SITE" "$BACKUP"
echo "backup: $BACKUP"

python3 - "$SITE" "$SNIPPET" <<'PY'
import sys
site_path, snippet_path = sys.argv[1], sys.argv[2]
lines = open(site_path, encoding="utf-8").read().splitlines(keepends=True)
snippet = open(snippet_path, encoding="utf-8").read()
marker = None
for index, line in enumerate(lines):
    if line.strip().startswith("listen 443 ssl") and "server_name" not in line:
        marker = index
        break
if marker is None:
    raise SystemExit("ERROR: could not find the TLS listener line to anchor the insertion")
lines.insert(marker, snippet + "\n")
open(site_path, "w", encoding="utf-8").write("".join(lines))
print("inserted before line", marker + 1)
PY

if ! nginx -t; then
  cp -a "$BACKUP" "$SITE"
  echo "ERROR: nginx -t failed; original configuration restored from $BACKUP" >&2
  exit 1
fi

systemctl reload nginx
echo "nginx reloaded"

sleep 1
PUBLIC_CODE=$(curl -s -o /dev/null -w '%{http_code}' --resolve womtech.io.vn:443:127.0.0.1 https://womtech.io.vn/insights/ || true)
echo "public /insights/ without credentials: HTTP $PUBLIC_CODE (401 expected while basic auth is on)"
echo "rollback: sudo cp -a $BACKUP $SITE && sudo nginx -t && sudo systemctl reload nginx"
