#!/usr/bin/env bash
# Prepare and start Immich v3.1.0 using the existing One Touch data tree.
# Does not format disks or alter the One Touch directory contents, except a tiny
# temporary write test. The production PostgreSQL database is newly restored
# from /home/srihari/immich-current-20261017.dump on the OS SSD.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
IMMICH_DIR="$REPO_ROOT/immich"
BACKUP="/home/srihari/immich-current-20261017.dump"
MOUNT="/mnt/immich"
UUID="AD95-6C91"
DB_DIR="/srv/appdata/immich/postgres"

echo "=== Preflight ==="
test -s "$BACKUP" || { echo "ABORT: missing database dump: $BACKUP"; exit 1; }
test -d "$MOUNT/ImmichData/library" || { echo "ABORT: Immich library directory missing"; exit 1; }
test -d "$MOUNT/ImmichData/upload" || { echo "ABORT: Immich upload directory missing"; exit 1; }
test -d "$MOUNT/Photos" || { echo "ABORT: external Photos library missing"; exit 1; }

expected_dev="$(readlink -f "/dev/disk/by-uuid/$UUID")"
mounted_dev="$(findmnt --noheadings --output SOURCE --mountpoint "$MOUNT")"
[ "$expected_dev" = "$mounted_dev" ] || {
  echo "ABORT: $MOUNT is not mounted from UUID $UUID ($expected_dev); found $mounted_dev"
  exit 1
}

for name in immich_server immich_machine_learning immich_redis immich_postgres; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "ABORT: container $name already exists; inspect before proceeding"
    exit 1
  fi
done

cd "$IMMICH_DIR"
if [ ! -e .env ]; then
  cp .env.example .env
  password="$(openssl rand -hex 24)"
  sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=$password/" .env
  chmod 600 .env
  unset password
else
  grep -Fxq 'UPLOAD_LOCATION=/mnt/immich/ImmichData' .env || { echo "ABORT: existing .env has unexpected UPLOAD_LOCATION"; exit 1; }
  grep -Fxq 'EXTERNAL_LIBRARY_LOCATION=/mnt/immich/Photos' .env || { echo "ABORT: existing .env has unexpected external library path"; exit 1; }
  grep -Fxq 'DB_DATA_LOCATION=/srv/appdata/immich/postgres' .env || { echo "ABORT: existing .env has unexpected database path"; exit 1; }
  if grep -q '^DB_PASSWORD=REPLACE_WITH_RANDOM_ALPHANUMERIC_PASSWORD$' .env; then
    echo "ABORT: replace the DB_PASSWORD placeholder in immich/.env first"
    exit 1
  fi
  chmod 600 .env
fi

if [ -d "$DB_DIR" ] && [ -n "$(sudo find "$DB_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  echo "ABORT: $DB_DIR is non-empty; refusing to overwrite database files"
  exit 1
fi

echo "=== Back up fstab and enable read-write on the One Touch mount ==="
FSTAB_BACKUP="/etc/fstab.pre-immich-$(date +%Y%m%d-%H%M%S)-$$"
sudo cp -a /etc/fstab "$FSTAB_BACKUP"
if ! sudo python3 - "$FSTAB_BACKUP" <<'PY'
import os
import sys

fstab = "/etc/fstab"
with open(fstab, "r", encoding="utf-8") as f:
    lines = f.readlines()

matches = []
for i, line in enumerate(lines):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    fields = stripped.split()
    if (len(fields) >= 4 and fields[0] == "UUID=AD95-6C91"
            and fields[1] == "/mnt/immich" and fields[2] == "exfat"):
        matches.append((i, fields))

if len(matches) != 1:
    print("ABORT: expected exactly one fstab entry: UUID=AD95-6C91 /mnt/immich exfat ...")
    sys.exit(2)

i, fields = matches[0]
opts = fields[3].split(",")
if "ro" in opts and "rw" in opts:
    print("ABORT: fstab entry includes both ro and rw")
    sys.exit(2)
if "ro" in opts:
    opts = ["rw" if x == "ro" else x for x in opts]
elif "rw" not in opts:
    opts.insert(0, "rw")

fields[3] = ",".join(opts)
lines[i] = "\t".join(fields) + "\n"

st = os.stat(fstab)
tmp = fstab + ".immich-tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.writelines(lines)
os.chmod(tmp, st.st_mode)
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, fstab)
print("Updated only the One Touch fstab entry to use rw.")
PY
then
  echo "ABORT: fstab change failed; backup is $FSTAB_BACKUP"
  exit 1
fi

if ! sudo mount -o remount,rw "$MOUNT"; then
  sudo cp -a "$FSTAB_BACKUP" /etc/fstab
  echo "ABORT: remount read-write failed; fstab was restored from $FSTAB_BACKUP"
  exit 1
fi

mounted_dev="$(findmnt --noheadings --output SOURCE --mountpoint "$MOUNT")"
opts="$(findmnt --noheadings --output OPTIONS --mountpoint "$MOUNT")"
if [ "$mounted_dev" != "$expected_dev" ] || [[ ",$opts," != *,rw,* ]]; then
  sudo cp -a "$FSTAB_BACKUP" /etc/fstab
  sudo mount -o remount,ro "$MOUNT" || true
  echo "ABORT: One Touch mount identity or rw mode verification failed; fstab backup: $FSTAB_BACKUP"
  exit 1
fi

probe="$MOUNT/ImmichData/.knowhere-write-test-$(date +%s)-$$"
if ! printf 'temporary Immich mount write test\n' | sudo tee "$probe" >/dev/null; then
  sudo cp -a "$FSTAB_BACKUP" /etc/fstab
  sudo mount -o remount,ro "$MOUNT" || true
  echo "ABORT: write test failed; fstab restored and read-only remount attempted"
  exit 1
fi
if ! sudo rm -f "$probe"; then
  echo "ABORT: temporary write test could not be removed; inspect $probe"
  exit 1
fi

echo "One Touch UUID $UUID is mounted read-write at $MOUNT."
df -h "$MOUNT" /srv/appdata

echo "=== Prepare fresh production database directory ==="
sudo install -d -m 0700 "$DB_DIR"
docker compose config -q
docker compose pull

echo "=== Start only the production PostgreSQL container ==="
docker compose up -d database
ready=0
for i in $(seq 1 90); do
  if docker exec -u postgres immich_postgres pg_isready -U postgres -d immich >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  docker logs --tail 100 immich_postgres || true
  echo "ABORT: production PostgreSQL did not become ready; Immich application was not started"
  exit 1
fi

echo "=== Restore fresh October 17 source dump ==="
docker cp "$BACKUP" immich_postgres:/tmp/immich-current-20261017.dump
if ! docker exec -u postgres immich_postgres pg_restore \
    --exit-on-error --no-owner --no-privileges \
    -U postgres -d immich /tmp/immich-current-20261017.dump \
    > /tmp/immich-production-restore.log 2>&1; then
  tail -n 100 /tmp/immich-production-restore.log
  echo "ABORT: restore failed. Database container and files are retained; app was not started."
  exit 1
fi

count_query="SELECT (SELECT count(*) FROM public.asset), (SELECT count(*) FROM public.album), (SELECT count(*) FROM public.album_asset), (SELECT count(*) FROM public.asset_file), (SELECT count(*) FROM public.integrity_report), (SELECT count(*) FROM public.kysely_migrations)"
counts="$(docker exec -u postgres immich_postgres psql -X -U postgres -d immich -At -F '|' -c "$count_query" | tr -d '\r')"
echo "asset|album|album_asset|asset_file|integrity_report|kysely_migrations"
echo "$counts"
if [ "$counts" != "28149|38|7212|57572|72282|88" ]; then
  echo "ABORT: restored counts differ from the validated source dump; Immich application was not started"
  exit 1
fi
fk="$(docker exec -u postgres immich_postgres psql -X -U postgres -d immich -At -F '|' -c "SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT convalidated) FROM pg_constraint WHERE contype='f'" | tr -d '\r')"
echo "foreign_keys|unvalidated_foreign_keys"
echo "$fk"
if [ "$fk" != "64|0" ]; then
  echo "ABORT: foreign-key validation check did not match; Immich application was not started"
  exit 1
fi

docker exec immich_postgres rm -f /tmp/immich-current-20261017.dump

echo "=== Install guarded Immich startup unit and start the stack ==="
sudo install -m 0644 "$REPO_ROOT/systemd/homelab-immich.service" /etc/systemd/system/homelab-immich.service
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-immich.service

sleep 12
echo "=== Immich container status ==="
docker ps --filter "name=immich_" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo
docker compose ps
echo
echo "=== Recent Immich server logs ==="
docker logs --tail 50 immich_server || true
echo
echo "Immich endpoint: http://knowhere:2283"
echo "Fstab backup retained at: $FSTAB_BACKUP"
echo "The Windows immich_server must remain stopped while Linux Immich is active."
