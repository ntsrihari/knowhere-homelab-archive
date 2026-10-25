#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install/configure Docker Engine and Compose first." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required (docker compose)." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  password="$(openssl rand -hex 24)"
  cat > .env <<EOF
BLACKBOX_USERNAME=blackbox
BLACKBOX_PASSWORD=${password}
BLACKBOX_DATA_DIR=/srv/appdata/blackbox
BLACKBOX_IMPORT_DIR=./private-imports
EOF
  chmod 600 .env
  echo "Created a private .env with a random password."
else
  chmod 600 .env
fi

if grep -q 'BLACKBOX_PASSWORD=GENERATE_WITH_BOOTSTRAP' .env || ! grep -q '^BLACKBOX_PASSWORD=.' .env; then
  echo "Set BLACKBOX_PASSWORD in .env to a unique random value before continuing." >&2
  exit 1
fi

DATA_DIR="$(sed -n 's/^BLACKBOX_DATA_DIR=//p' .env | tail -n1)"
DATA_DIR="${DATA_DIR:-/srv/appdata/blackbox}"
if [[ "$DATA_DIR" != /* ]]; then
  echo "BLACKBOX_DATA_DIR must be an absolute path." >&2
  exit 1
fi
mkdir -p "$DATA_DIR"
# Container runs as UID/GID 10001. chown fails without privileges; bootstrap is intended to run with sudo if needed.
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R 10001:10001 "$DATA_DIR"
else
  sudo chown -R 10001:10001 "$DATA_DIR"
fi

if [[ ! -d private-imports ]]; then
  echo "private-imports directory is missing; restore the supplied private import bundle." >&2
  exit 1
fi

docker compose up -d --build
sleep 2

echo
echo "Knowhere Black Box started. Open:"
echo "  http://knowhere:8787  (if local name resolution works)"
echo "  http://192.168.29.68:8787  (home LAN; update if knowhere's DHCP address changed)"
echo "  http://100.104.30.99:8787  (Tailscale)"
echo
echo "Login username: $(sed -n 's/^BLACKBOX_USERNAME=//p' .env | tail -n1)"
echo "Login password (private): $(sed -n 's/^BLACKBOX_PASSWORD=//p' .env | tail -n1)"
echo "The CSV/GPX files bundled in private-imports are personal data. Do not commit them to GitHub."
