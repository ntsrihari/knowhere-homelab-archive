# Server Inventory and Architecture

**Last reconciled:** 2026-10-17  
**Host:** `knowhere` — Dell OptiPlex 5050  
**Repository checkout on host:** `/opt/homelab`  
**Git remote:** `https://github.com/ntsrihari/knowhere-homelab-archive`

This document combines current observed state with the intended design. Planned configurations are labelled as plans, not as already implemented.

## Host and operating system

| Item | Value |
|---|---|
| Hostname | `knowhere` |
| Hardware | Dell OptiPlex 5050 |
| RAM | 8 GB (reported during rebuild planning) |
| Architecture | x86_64 |
| OS | Ubuntu 26.04.1 LTS, codename `resolute` |
| Running kernel at capture | `7.0.0-38-generic`, `#38-Ubuntu` |
| SATA controller | Intel 200 Series PCH SATA controller, PCI `00:17.0`, ID `8086:a282`, Dell subsystem `1028:07a2` |
| SATA driver | `ahci` |
| Affected controller host/port | `host4` / `ata3` |

The exact kernel and OS reflect the commands captured during the incident and should be refreshed after a kernel update.

## Disk map — latest observed 2026-10-17

Always identify disks by model and serial. Linux device letters have changed during this incident.

| Linux node at capture | Model | Serial | Capacity | Filesystem/mount observed | Intended role / protection |
|---|---|---|---|---|---|
| `/dev/sda` | One Touch w/PW | `00000000NACNBMA9` | 931.5 GiB | exFAT, label One Touch, UUID `AD95-6C91`, mounted `/mnt/immich` | External 1 TB HDD with current Windows Immich data. Preserve unchanged. |
| `/dev/sdb` | Crucial CT500BX500SSD1 | `2239E66F361E` | 465.8 GiB | EFI UUID `63F9-F146` at `/boot/efi`; ext4 UUID `d45b5f35-b38d-4de2-85cf-bd9c0f096e56` at `/boot`; LVM PV with 100 GB root LV UUID `759c7ad4-7d3e-43ec-8520-d396fb9a2abc` mounted at `/` | Ubuntu OS and intended Docker/config/database SSD. Do not format. |
| `/dev/sdc` | Seagate ST1000LM048-2E7172 / ST1000LM048-2E71 | `WL114BWW` | 931.5 GiB | `/dev/sdc1` present; no recognized filesystem/UUID/mountpoint | Do not use for primary photos or backups. SMART has 31,440 reallocated sectors, 104 pending sectors and 104 offline-uncorrectable sectors. |
| `/dev/sdd` | KINGSTON SNVS500G | `50026B7684B4198D` | 465.8 GiB | 16 MB partition and NTFS partition UUID `D09481079480F0F0`, label Kingston 500GB, mounted `/mnt/media` | External 512 GB SSD with an older Immich dataset, media, Nextcloud data and other files. Preserve unchanged. |

### Seagate SMART findings — 2026-10-17

After physical reconnection, `smartctl -x /dev/sdc` responded and reported the matching model/serial. Despite the overall SMART status `PASSED`, key counters are serious: reallocated sectors (attribute 5) = 31,440; current pending sectors (197) = 104; offline uncorrectable (198) = 104; reported uncorrectable raw value (187) = 38; power-on hours = 17,793; temperature = 31 C; UDMA CRC error count (199) = 0. The SMART self-test log contained no tests. These values do not prove the root cause of the SATA link loss, but they are sufficient to reject this disk as a trusted primary photo/backup target. See the full [incident record](incidents/2026-10-16-seagate-sata-io-failure.md).

Historical internal disk state before the incident: Seagate was NTFS UUID `8ABA4CF5BA4CDF77`, label reported as 1TB HDD, mounted `/mnt/internal-hdd`. That UUID is now stale and must not be used as the current filesystem ID.

## Mount configuration previously observed

External One Touch:
```fstab
UUID=AD95-6C91 /mnt/immich exfat ro,nofail,uid=0,gid=0,fmask=0022,dmask=0022 0 0
```

External Kingston:
```fstab
UUID=D09481079480F0F0 /mnt/media ntfs-3g ro,nofail,norecover,uid=0,gid=0,umask=022 0 0
```

These are historical captured entries; re-check live `/etc/fstab` before changes. The previous internal Seagate entry must not be restored.

## Repository configuration and services

The last inspected tree at commit `916d6cdeeda8173153ebb165e41abdb10c738695` contained:
```text
.gitignore
README.md
docs/storage-and-startup.md
portainer/compose.yaml
jellyfin/compose.yaml
navidrome/compose.yaml
systemd/homelab-jellyfin.service
systemd/homelab-navidrome.service
```

A later commit updates storage documentation and adds this incident/inventory documentation. The version-controlled Compose projects are:
- Portainer CE: `portainer/compose.yaml`
- Jellyfin: `jellyfin/compose.yaml`
- Navidrome: `navidrome/compose.yaml`

The current snapshot does not contain an Immich, Pi-hole or Nextcloud Compose project. See [service configuration](service-configuration.md) for ports, mounts, named volumes, image tags and systemd startup details.

Previously reported local URLs:
- Portainer: `https://knowhere:9443`
- Jellyfin: `http://knowhere:8096`
- Navidrome: `http://knowhere:4533`

These are last-reported endpoints, not a current health check. Intended remote access is LAN/Tailscale only, without router port forwarding; live network/firewall state is not certified by this document.

## Immich source system and data

The existing Windows Docker Desktop/WSL2 Immich source was reported as:
- Immich image version `v3.1.0`
- `TZ=Asia/Kolkata`
- Windows managed data: `I:/ImmichData`, mounted in the Immich server container at `/data`
- External photo library: `I:/Photos`, mounted read-only inside the container at `/mnt/photos`
- PostgreSQL uses a Docker named volume on the WSL2 Linux filesystem, not on the NTFS photo disk.
- Credentials are in a local `.env` and must never be copied to Git.

Approximate previous sizes:
- One Touch HDD: about 829 GB used / 104 GB free; `Photos` about 515 GB; `ImmichData` about 309 GB.
- Within that ImmichData, managed `library` about 215 GB, `encoded-video` about 74 GB, `thumbs` about 16 GB, `upload` about 2.5 GB, `backups` about 2.4 GB.
- Kingston SSD: about 333 GB used / 134 GB free; its separate `ImmichData` about 292 GB plus `Media`, `NextcloudData` and other data.
- Prior comparison showed the Kingston Immich tree was not an exact mirror and appeared older/incomplete.

A PostgreSQL backup archive named `immich-db-backup-20261015T020000-v3.1.0-pg14.19.sql.gz` was reported around 183 MB and passed `gzip -t`; this checks compression integrity, not SQL restore correctness.

Preserve the Windows Immich instance, One Touch HDD and Kingston SSD until Linux migration has been restored and accepted.

## Target architecture — NOT YET IMPLEMENTED

- Crucial BX500 SSD: Ubuntu, Docker Engine/Compose, repository, databases and application state.
- Seagate 1 TB HDD (serial `WL114BWW`): intended to become primary bulk-storage filesystem mounted at `/srv/storage`, only after the SATA incident is resolved and a filesystem is verified.
- One Touch external HDD: preserve current Immich source during migration; later intended as portable one-way file-level mirror of selected primary data. This is not RAID1.
- Kingston external SSD: preserve its existing data until verified; potential staging/media use is a future decision.
- Update Compose bind mounts and systemd startup guards after storage is finalized; services must not write to empty mount directories if the expected drive is missing.
- Mirror automation must have mount identity checks, dry-run validation, logs, deletion/retention protection and a tested restore path before scheduling.

See [migration status](migration-status.md) and [the Seagate incident](incidents/2026-10-16-seagate-sata-io-failure.md). No primary-storage migration or mirror job is active from the configuration documented here.
