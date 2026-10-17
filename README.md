# Knowhere Homelab Archive

Infrastructure configuration and operating documentation for the always-on Ubuntu home server, hostname `knowhere`.

## Services in this repository

- Portainer CE — `portainer/compose.yaml`
- Jellyfin — `jellyfin/compose.yaml`
- Navidrome — `navidrome/compose.yaml`

Systemd startup units for Jellyfin and Navidrome are stored under `systemd/`.

Immich is currently the Windows Docker Desktop/WSL2 source deployment and is not yet configured as a Compose project in this repository. Pi-hole and Nextcloud are planned services; their Compose definitions are not present in the current repository snapshot.

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

An ext4 format attempt on the internal Seagate 1 TB HDD failed during filesystem metadata writes, followed by SATA read/write timeouts and failed link resets. The root cause is unresolved; this does not prove that the HDD itself is defective. Formatting and Immich migration are on hold. Both external data drives must be preserved unchanged until the incident is resumed and migration acceptance is complete.
