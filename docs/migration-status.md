# Migration Status and Acceptance Gates

**As of:** 2026-10-17  
**Status:** ON HOLD — Seagate SATA I/O incident unresolved.

## Existing system

- Ubuntu 26.04.1 LTS on the Crucial BX500 500 GB SSD.
- Repository clone previously reported at `/opt/homelab`.
- Checked-in Compose projects: Portainer CE, Jellyfin and Navidrome.
- Jellyfin and Navidrome have systemd startup units.
- The current Windows Docker Desktop/WSL2 Immich instance is the source of truth while migration is pending.
- One Touch external 1 TB HDD with serial `00000000NACNBMA9` was last observed at `/mnt/immich`.
- Kingston external 512 GB SSD with serial `50026B7684B4198D` was last observed at `/mnt/media`.
- Internal Seagate 1 TB HDD serial `WL114BWW` had a failed ext4 format attempt, SATA link failures, and subsequent SMART findings of 31,440 reallocated sectors, 104 pending sectors and 104 offline-uncorrectable sectors. It is not an approved primary-storage destination; replace it or use another verified healthy disk.

## Blocked work

- Diagnose the SATA command/link failure and determine whether the cause is drive, connection/power, controller/port or software interaction.
- Do not use the Seagate for irreplaceable data. Decide replacement/alternate primary storage; preserve the incident findings in the incident record.
- Do not retry format or stress tests on the Seagate as part of the Immich migration.
- Finalize target storage location, expected to be `/srv/storage`, and update media bind mounts and systemd startup safeguards.
- Migrate the PostgreSQL database, managed Immich data and external library to Linux.
- Verify library references, photo/asset counts, metadata, albums, uploads and database integrity.
- Configure the portable file-level mirror with safety checks and retention policy.

## Acceptance gates

1. **Primary-storage replacement selected:** do not approve the current Seagate based on its SMART pass flag; the sector counts are unacceptable for irreplaceable data. Choose a replacement or alternate drive and verify its health.
2. **Filesystem verified:** create ext4 on the serial-confirmed replacement only after its health checks pass, record the UUID, and confirm controlled reads/writes without kernel I/O errors.
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
- Do not begin migration or configure automated synchronization.
- Avoid rebooting or changing boot/kernel settings remotely without an explicit maintenance window and recovery access.
- Keep the Windows Immich instance running/available as the source of truth, where operationally possible.
