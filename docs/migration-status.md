# Migration Status and Acceptance Gates

**As of:** 2026-10-17  
**Status:** Immich deployment prepared; activation must use the One Touch HDD. Seagate mirror/cloud storage remains blocked by repeated SATA I/O failures.

## Existing system

- Ubuntu 26.04.1 LTS on the Crucial BX500 500 GB SSD.
- Repository clone previously reported at `/opt/homelab`.
- Checked-in Compose projects: Portainer CE, Jellyfin and Navidrome.
- Jellyfin and Navidrome have systemd startup units.
- The current Windows Docker Desktop/WSL2 Immich instance is the source of truth while migration is pending.
- One Touch external 1 TB HDD with serial `00000000NACNBMA9` was last observed at `/mnt/immich`.
- Kingston external 512 GB SSD with serial `50026B7684B4198D` was last observed at `/mnt/media`.
- Internal Seagate 1 TB HDD serial `WL114BWW` had a failed ext4 format attempt, SATA link failures, and subsequent SMART findings of 31,440 reallocated sectors, 104 pending sectors and 104 offline-uncorrectable sectors. It is not an approved primary-storage destination; replace it or use another verified healthy disk.

## Database backup restore test — 2026-10-17

**Result: SQL restore completed successfully in an isolated test environment.** This validates that the compressed dump can be loaded into the compatible PostgreSQL image, but not yet that the restored contents match the source database or that media files are all present.

- Backup: `/mnt/immich/ImmichData/backups/immich-db-backup-20261015T020000-v3.1.0-pg14.19.sql.gz`
- `gzip -t`: PASS
- Image: `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23`
- PostgreSQL version: 14.19
- Isolated container: `immich-restore-test-pg`
- Temporary named volume: `immich-restore-test-data`
- Container was created with `--network none` and no published ports. The volume uses Docker's default storage on the OS SSD, not the Seagate.
- Database size reported by PostgreSQL: 553 MB
- Extension versions observed: `cube 1.5`, `earthdistance 1.1`, `pg_trgm 1.6`, `unaccent 1.1`, `uuid-ossp 1.1`, `vchord 0.4.3`, `vector 0.8.1`
- Public table count: 66
- Restored row counts: `asset=28114`, `album=38`, `album_asset=7212`, `asset_metadata=0`, `library=1`, `activity=0`, `partner=0`, `kysely_migrations=88`
- Foreign-key check: 64 total foreign-key constraints; 0 unvalidated.
- The separate table-count query completed successfully for all 66 public tables. Highest row counts: `geodata_places=227901`, `asset_ocr=147209`, `asset_file=57502`, `asset=28114`, `asset_exif=28113`, `asset_job_status=28113`, `smart_search=27867`, `asset_face=26852`, `face_search=26852`, `ocr_search=10933`; `album_asset=7212`; `kysely_migrations=88`; `album=38`; `user=1`; `library=1`.
- Other zero-row tables observed include `asset_metadata`, `activity`, `partner`, `shared_link`, `stack`, `video_stream_session`, and various audit tables. These are not declared failures until compared against the source's actual schema/data state.
- The zero-row tables are observations to compare against the source database; they are not currently declared failures.
- Test resources are intentionally retained pending review: do not remove the container or volume yet.

**Next database gate:** run the same safe count queries against the source database or another trusted source-side report, and compare schema/migration versions. Do not start the Linux Immich application against this temporary database and do not use it as the production database.

## Fresh database backup and repeat storage failure — 2026-10-17

- A fresh custom-format PostgreSQL dump was created from the Windows `immich_postgres` container, copied to `/home/srihari/immich-current-20261017.dump` on `knowhere`, and restored successfully in isolated container `immich-current-restore-test-pg` using a separate named volume `immich-current-restore-data`.
- Fresh restore key counts match the current Windows source report: `asset=28149`, `album=38`, `album_asset=7212`, `asset_file=57572`, `integrity_report=72282`, `kysely_migrations=88`; all 64 foreign-key constraints are validated. Restore database size: 580 MB. The test container has no network and no published ports. Retain the container and volume for now.
- A second serial-identified format attempt on Seagate serial `WL114BWW` created partition/filesystem structures and printed UUID `8f43756c-4fcf-4b0e-ba43-f77beb4c905f`, but `mke2fs` failed on final filesystem accounting writes with `Input/output error`. A usable ext4 filesystem and `/srv/storage` mount are not confirmed. The SSH session closed immediately afterward; host reboot/crash status is unknown.
- No further writes or mount attempts against this Seagate. Next step is read-only host/kernel log inspection to determine whether the host rebooted and collect current/previous-boot SATA errors.

## Blocked work

- Diagnose the SATA command/link failure and determine whether the cause is drive, connection/power, controller/port or software interaction.
- Do not use the Seagate for irreplaceable data. Decide replacement/alternate primary storage; preserve the incident findings in the incident record.
- Do not retry format or stress tests on the Seagate as part of the Immich migration.
- One Touch HDD is the intended Immich primary location for now: `/mnt/immich/ImmichData` to container `/data`, with `/mnt/immich/Photos` mapped read-only to `/mnt/photos`. The Linux service must fail closed unless UUID `AD95-6C91` is mounted read-write.
- Migrate the PostgreSQL database, managed Immich data and external library to Linux.
- Verify library references, photo/asset counts, metadata, albums, uploads and database integrity.
- Configure the portable file-level mirror with safety checks and retention policy.

## Acceptance gates

1. **Current Immich primary:** use the existing One Touch source drive at `/mnt/immich` only after enabling read-write mount; keep its current `ImmichData` and `Photos` trees in place. The separate internal Seagate is not approved as mirror/cloud storage while I/O errors persist.
2. **Seagate mirror/cloud target:** no usable filesystem is confirmed on serial `WL114BWW`; a second ext4 format attempt failed. Do not retry writes until the drive/SATA communication failure is resolved.
3. **Mount guarded:** storage mount uses UUID, and services fail closed if required storage is missing rather than writing into an empty directory on the OS disk.
4. **Capacity checked:** real source sizes, filesystem overhead, generated data and growth fit the primary destination. Capacity for the selected mirror dataset is separately measured.
5. **Database restore proven:** the existing compressed dump is restored in a test context. A passing `gzip -t` alone is not enough.
6. **Compatible Immich deployment:** initially use a version compatible with the source database and data layout; preserve the external library path `/mnt/photos` inside the container if database paths depend on it.
7. **Data validated:** confirm originals, uploads, database metadata, users/albums and external-library references. Thumbnails and encoded videos may be regenerated only after original assets and the database are verified.
8. **Source retained:** do not delete, reformat, repair or reuse the Windows Immich deployment or either external disk until the Linux migration is accepted.
9. **Portable mirror tested:** the external mirror is one-way file-level sync, not RAID1. Check destination identity/mount, perform dry-run, log operations, define deletion/retention behaviour, and test restore before automation.

## While on hold

- Do not retry `mkfs`, `wipefs`, `badblocks`, filesystem repair or write tests on the Seagate. Its SMART data is now a replacement-level warning.
- Do not format or alter the One Touch HDD or Kingston SSD.
- Do not configure an automatic mirror to the Seagate until it can reliably write and read data.
- Avoid rebooting or changing boot/kernel settings remotely without an explicit maintenance window and recovery access.
- Keep the Windows Immich instance running/available as the source of truth, where operationally possible.
