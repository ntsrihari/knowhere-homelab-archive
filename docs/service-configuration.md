# Service Configuration Reference

Source of truth for actual Compose and systemd syntax is the file linked for each service below. This page summarizes values inspected from repository main at commit `916d6cdeeda8173153ebb165e41abdb10c738695`. Later config changes must be reconciled into this document.

## Portainer CE

File: [portainer/compose.yaml](../portainer/compose.yaml)

- Image: `portainer/portainer-ce:lts`
- Container: `portainer`
- Restart: `unless-stopped`
- Published port: `9443:9443`
- Docker socket bind: `/var/run/docker.sock:/var/run/docker.sock`
- Named volume: `portainer_data:/data`

## Jellyfin

File: [jellyfin/compose.yaml](../jellyfin/compose.yaml)

- Compose project name: `jellyfin`
- Image: `jellyfin/jellyfin:latest`
- Container: `jellyfin`
- Restart: `unless-stopped`
- Published port: `8096:8096`
- Time zone: `Asia/Kolkata`
- Named volumes: `jellyfin_config:/config`, `jellyfin_cache:/cache`
- Read-only media bind mounts:
  - `/mnt/media/Media/Movies:/media/ssd-movies:ro`
  - `/mnt/media/Media/Shows:/media/shows:ro`
  - `/mnt/internal-hdd/MOVIES:/media/hdd-movies:ro`

Startup unit: [systemd/homelab-jellyfin.service](../systemd/homelab-jellyfin.service). It requires and starts after Docker, uses `Type=oneshot`, `RemainAfterExit=yes`, runs `docker compose up -d` from `/opt/homelab/jellyfin`, and stops with `docker compose stop`. It includes `RequiresMountsFor=/mnt/media/Media/Movies /mnt/media/Media/Shows /mnt/internal-hdd/MOVIES`.

Last reported endpoint: `http://knowhere:8096`.

## Navidrome

File: [navidrome/compose.yaml](../navidrome/compose.yaml)

- Compose project name: `navidrome`
- Image: `deluan/navidrome:latest`
- Container: `navidrome`
- Restart: `unless-stopped`
- Published port: `4533:4533`
- Environment: `ND_MUSICFOLDER=/music`, `ND_DATAFOLDER=/data`, `ND_SCANSCHEDULE=1h`, `ND_LOGLEVEL=info`, `TZ=Asia/Kolkata`
- Named volume: `navidrome_data:/data`
- Read-only media bind mounts:
  - `/mnt/media/Media/Music:/music/ssd:ro`
  - `/mnt/internal-hdd/MUSIC:/music/hdd:ro`

Startup unit: [systemd/homelab-navidrome.service](../systemd/homelab-navidrome.service). It requires and starts after Docker, uses `Type=oneshot`, `RemainAfterExit=yes`, runs `docker compose up -d` from `/opt/homelab/navidrome`, and stops with `docker compose stop`. It includes `RequiresMountsFor=/mnt/media/Media/Music /mnt/internal-hdd/MUSIC`.

Last reported endpoint: `http://knowhere:4533`.

## Image pinning and persistent state

The inspected manifests use moving tags `latest` or `lts`, not pinned image digests. Image updates can therefore change the deployed versions. Pinning is a separate maintenance change; do not modify running services as part of the current storage incident hold.

Named volumes are declared for application state. Their actual host-side locations are managed by Docker and should be captured with `docker volume inspect` when the operator is available. Do not assume a named volume is backed up just because it is declared.

The repository's `.gitignore` excludes `.env`, secret directories, key files, backups, SQL and dump files. Never commit database passwords, tokens, private keys, database dumps, or personal media.

## Required follow-up after storage hold

The Compose files and systemd units still use `/mnt/internal-hdd`; this is historical and must be reconciled after storage is finalized. Review mount dependencies and explicit presence guards so missing drives cannot cause containers to write to ordinary directories on the OS SSD. No such changes are included while the Seagate incident is on hold.
