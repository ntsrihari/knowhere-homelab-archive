# Knowhere Homelab Archive

Infrastructure configuration and operating documentation for the always-on Ubuntu home server, hostname `knowhere`.

## Services in this repository

- Portainer CE — `portainer/compose.yaml`
- Jellyfin — `jellyfin/compose.yaml`
- Navidrome — `navidrome/compose.yaml`
- Immich v3.1.0 — `immich/compose.yaml` (prepared to use the preserved One Touch HDD data paths; activate only after the mount is switched to read-write and the current database dump is restored).

Systemd startup units for Jellyfin and Navidrome are stored under `systemd/`.

Immich's Linux Compose project is now prepared in `immich/compose.yaml`. The intended primary media location is the existing One Touch HDD at `/mnt/immich`; the Linux service must not start until its UUID is verified and the mount is read-write. The Windows Immich server remains stopped while the restored database is prepared. The internal Seagate has repeatedly failed SATA reads/writes and is not currently usable as the planned mirror/cloud-storage target. Pi-hole and Nextcloud Compose definitions are not present in the current repository snapshot.

## Operating principles

- Docker Engine and Docker Compose.
- Persistent application data outside this repository.
- Secrets, credentials, database dumps and personal media are excluded from Git.
- Identify disks by model and serial, not by a remembered `/dev/sdX` name.
- A container starting successfully does not prove that the expected disk is mounted.
- Migration and mirror automation require restore validation.

## Documentation

- [Storage mounts and service startup](docs/storage-and-startup.md) — current and historical mount paths, service startup caveats, and the latest disk-letter mapping.
- [Seagate SATA I/O incident (2026-10-16)](docs/incidents/2026-10-16-seagate-sata-io-failure.md) — failure timeline, diagnostics, and hold status.
- [Server inventory and target architecture](docs/server-inventory.md) — host, disks, services, data sources, and planned storage roles.
- [Migration status and acceptance gates](docs/migration-status.md) — conditions required before starting the Immich migration.

## Current hold

The internal Seagate 1 TB HDD has failed two ext4 format attempts with SATA read/write I/O errors and an uncorrectable read response. Do not retry writes or use it for a mirror/cloud-data target until storage reliability is addressed. Immich Linux configuration is prepared to reuse the One Touch HDD's existing data tree, preserving the internal container paths; it is not yet activated.
