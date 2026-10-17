# Immich Linux Migration Runbook

**Last updated:** 2026-10-17  
**Target:** `knowhere` (Ubuntu, Docker Compose)  
**Immich version:** `v3.1.0`  
**Status:** Linux deployment is prepared; bootstrap has **not yet been run**.

This runbook records the commands used during this migration and the intended next steps. Read the status and safety notes before repeating any step. The checked-in script `immich/bootstrap-linux.sh` is the main guarded bootstrap procedure.

## Target storage layout

| Purpose | Host path / mount | Container path / notes |
|---|---|---|
| Current Immich managed files | `/mnt/immich/ImmichData` | Immich server `/data` |
| Existing external library | `/mnt/immich/Photos` | Immich server `/mnt/photos`, read-only |
| Immich PostgreSQL data | `/srv/appdata/immich/postgres` | `/var/lib/postgresql/data`, on the Crucial OS SSD |
| Movies and shows | `/mnt/media/Media/Movies`, `/mnt/media/Media/Shows` | Jellyfin read-only |
| Music | `/mnt/media/Media/Music` | Navidrome read-only |
| Intended future mirror / device-cloud storage | Internal Seagate, serial `WL114BWW` | **Blocked**: repeated SATA I/O and uncorrectable-read failures; no usable filesystem/mount confirmed |

Drive identities must be checked by UUID/model/serial, not a remembered `/dev/sdX` letter:
- One Touch external HDD: serial `00000000NACNBMA9`, filesystem UUID `AD95-6C91`, mount `/mnt/immich`.
- Kingston external SSD: serial `50026B7684B4198D`, filesystem UUID `D09481079480F0F0`, mount `/mnt/media`.
- Internal Seagate: serial `WL114BWW`. Do not retry format/write operations; see [incident record](incidents/2026-10-16-seagate-sata-io-failure.md).

## Current state and important constraints

- Windows `immich_server` is stopped. Keep it stopped while Linux Immich is active to avoid two servers managing the same media paths.
- Windows `immich_postgres` remains available as the source database. Do not delete its container, volume, or database.
- The custom-format dump `/home/srihari/immich-current-20261017.dump` was transferred to `knowhere`.
- A restore of that dump succeeded in isolated container `immich-current-restore-test-pg`, using volume `immich-current-restore-data`; key table counts matched the Windows source. Keep this test container/volume for now.
- The earlier SQL dump restore test also remains intact as `immich-restore-test-pg` / `immich-restore-test-data`.
- The Linux production Compose project is in `immich/compose.yaml`; the environment template is `immich/.env.example`; startup is guarded by `systemd/homelab-immich.service`.
- `immich/bootstrap-linux.sh` is designed to switch the One Touch mount to read-write, restore the fresh dump into a **new** database directory on the OS SSD, verify key row counts, then start the Linux application. The script has **not** been run as of this note.
- The One Touch source was measured earlier at roughly 829 GB used and about 104 GB free. Treat remaining capacity as limited and check it before relying on uploads or derived data growth.
- The Seagate cannot currently be used as a mirror or as cloud storage for devices. A mirroring job or Nextcloud data location must not be pointed at it while its I/O failures remain unresolved.

## 1. Read-only inventory on `knowhere`

Run after reconnecting:

```bash
echo "=== Disk identities ==="
lsblk -o NAME,MODEL,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS

echo
echo "=== Immich source mount ==="
findmnt --target /mnt/immich

echo
echo "=== Media SSD mount ==="
findmnt --target /mnt/media

echo
echo "=== Expected directories ==="
for p in \
  /mnt/immich/ImmichData/library \
  /mnt/immich/ImmichData/upload \
  /mnt/immich/Photos \
  /mnt/immich/ImmichData/backups \
  /mnt/media/Media/Movies \
  /mnt/media/Media/Shows \
  /mnt/media/Media/Music
do
  if [ -d "$p" ]; then printf 'PRESENT  %s\n' "$p"; else printf 'MISSING  %s\n' "$p"; fi
done
```

Expected critical identities: `AD95-6C91` at `/mnt/immich` and `D09481079480F0F0` at `/mnt/media`. The external drives had been mounted read-only while being preserved.

## 2. Confirm the Windows source containers

On Windows PowerShell with Docker Desktop running:

```powershell
docker ps --format "table {{.Names}}\t{{.Image}}"
```

The source container names observed were `immich_server`, `immich_postgres`, `immich_machine_learning`, and `immich_redis`. On Windows, the `immich_server` was explicitly stopped after its host-drive paths became unavailable:

```powershell
docker stop immich_server
```

Do not stop or delete `immich_postgres` before a verified backup exists.

## 3. Create and transfer a fresh database dump (Windows PowerShell)

This produced a fresh custom-format backup from the current source DB, validated that PostgreSQL could list its archive entries, copied it to the Windows profile, printed its SHA-256 hash, then sent it to `knowhere` over SSH:

```powershell
$ErrorActionPreference = "Stop"

$name = "immich-current-20261017.dump"
$containerPath = "/tmp/$name"
$hostPath = Join-Path $env:USERPROFILE $name

docker exec -u postgres immich_postgres pg_dump `
    -U postgres -d immich -Fc -f $containerPath
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }

docker exec -u postgres immich_postgres pg_restore --list $containerPath *> $null
if ($LASTEXITCODE -ne 0) { throw "Dump archive validation failed" }

docker cp "immich_postgres:$containerPath" $hostPath
if ($LASTEXITCODE -ne 0) { throw "docker cp failed" }

Get-Item $hostPath | Select-Object FullName, Length
Get-FileHash $hostPath -Algorithm SHA256

scp $hostPath "srihari@knowhere:/home/srihari/$name"
if ($LASTEXITCODE -ne 0) { throw "Transfer failed" }
```

The resulting file was approximately 178 MB in PowerShell / 187 MB as reported by Docker transfer. Do not commit the dump to GitHub; the repository ignores `*.dump`.

## 4. Current database isolated restore check

The fresh custom-format dump was restored in a separately named PostgreSQL container with no network and no published ports. Names are intentionally distinct from production:

- Container: `immich-current-restore-test-pg`
- Volume: `immich-current-restore-data`
- Image: `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23`

Successful restore results:

| Check | Result |
|---|---:|
| Database size | 580 MB |
| `asset` | 28,149 |
| `album` | 38 |
| `album_asset` | 7,212 |
| `asset_file` | 57,572 |
| `integrity_report` | 72,282 |
| `kysely_migrations` | 88 |
| Foreign keys / unvalidated | 64 / 0 |
| Integrity reports: `checksum_mismatch` | 7,355 |
| Integrity reports: `missing_file` | 64,927 |

These counts matched the Windows source at the time of dump. The integrity-report rows are carried over as source database records; they are not automatically cleared by this process. Keep test resources until the production migration is accepted.

For inspection of the restored test DB:

```bash
docker exec -i immich-current-restore-test-pg \
  psql -X -U postgres -d immich -v ON_ERROR_STOP=1 <<'SQL'
SELECT current_database() AS database_name,
       current_setting('server_version') AS postgres_version,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT 'asset' AS table_name, COUNT(*) AS rows FROM public.asset
UNION ALL SELECT 'album', COUNT(*) FROM public.album
UNION ALL SELECT 'album_asset', COUNT(*) FROM public.album_asset
UNION ALL SELECT 'asset_file', COUNT(*) FROM public.asset_file
UNION ALL SELECT 'integrity_report', COUNT(*) FROM public.integrity_report
UNION ALL SELECT 'kysely_migrations', COUNT(*) FROM public.kysely_migrations
ORDER BY table_name;

SELECT type, COUNT(*) AS rows
FROM public.integrity_report
GROUP BY type
ORDER BY type;

SELECT COUNT(*) AS total_foreign_keys,
       COUNT(*) FILTER (WHERE NOT convalidated) AS unvalidated_foreign_keys
FROM pg_constraint
WHERE contype = 'f';
SQL
```

## 5. Prepared Linux Compose and startup guard

- `immich/compose.yaml` follows the official Immich `v3.1.0` layout and pins the matching PostgreSQL and Valkey image identities.
- `immich/.env.example` defines the preserved paths and the production DB data directory. The bootstrap script creates local `immich/.env` with a generated DB password if it does not exist; do not print or commit that file.
- `systemd/homelab-immich.service` verifies the One Touch filesystem UUID and requires `/mnt/immich` to be mounted read-write with the expected existing directories.
- The production database directory is `/srv/appdata/immich/postgres`, on the OS SSD. Do not place PostgreSQL data on exFAT/NTFS.
- The service should only be started after the source dump is verified and restored, and while the Windows Immich server remains stopped.

## 6. Bootstrap Linux Immich

On `knowhere`:

```bash
cd /opt/homelab
git pull --ff-only
bash immich/bootstrap-linux.sh
```

Review the script in GitHub before running. It performs these stages:

1. Checks the dump, One Touch UUID, existing data directories and conflicting production container names.
2. Creates `immich/.env` from the example with a random database password if absent.
3. Saves a timestamped backup of `/etc/fstab`, changes only the One Touch `AD95-6C91` mount entry from read-only to read-write, remounts it, and does a temporary create/delete write test under `ImmichData`.
4. Validates/pulls the Compose images and starts only the fresh production PostgreSQL container.
5. Restores the October 17 custom-format database backup into the empty production database directory.
6. Checks key row counts and foreign-key validation; aborts before starting the app if counts differ.
7. Installs the systemd unit, starts the Immich stack, and prints container status/logs.

The script does **not** format the One Touch disk, copy or mirror the media tree, or touch the Seagate. If any preflight, remount, restore, or count check fails, stop and review its output instead of rerunning it blindly. Keep the generated `immich/.env`, the SQL/custom-format backup, and the restore-test volumes intact.

## 7. Validate after startup

Expected endpoint: `http://knowhere:2283`.

Useful read-only commands:

```bash
cd /opt/homelab/immich
docker compose ps
docker logs --tail 100 immich_server
docker logs --tail 100 immich_postgres
docker logs --tail 100 immich_redis
sudo systemctl --no-pager --full status homelab-immich.service
findmnt --target /mnt/immich
df -h /mnt/immich /srv/appdata
```

Confirm that the login works, existing users/albums/assets are present, managed media thumbnails resolve, and the external library is available. Do not launch a scan, bulk re-upload, or clear integrity reports until the app and paths have been inspected.

## 8. Mirror and cloud storage — blocked

The intended architecture is:
- One Touch HDD: existing Immich primary data while migration is validated.
- Kingston SSD: media files for Jellyfin/Navidrome, read-only inside those containers.
- Internal Seagate: future mirror and personal-cloud storage **only if its device reliability is resolved**.

The Seagate (serial `WL114BWW`) returned an uncorrectable read (`UNC`), SATA recovery/revalidation failures and write I/O errors during a second ext4 creation attempt. Its partial filesystem UUID `8f43756c-4fcf-4b0e-ba43-f77beb4c905f` is not proof of a completed filesystem. `/srv/storage` is not mounted. Do not mount it, run `mkfs` again, repair it, or configure mirror/Nextcloud writes there. Keep this blocked until the drive/connection issue is resolved and the disk is trustworthy.

A future mirror must be a separate, logged, one-way file-level sync with mount-identity guards, dry-run review, deletion/retention policy, and a tested restore. It is not RAID1.
