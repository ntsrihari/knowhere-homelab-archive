# Storage Mounts and Service Startup

## Media drives

The media drives are configured as read-only mounts in /etc/fstab.

| UUID | Mount point | Filesystem | Access |
|---|---|---|---|
| AD95-6C91 | /mnt/immich | exFAT | Read-only |
| D09481079480F0F0 | /mnt/media | NTFS via ntfs-3g | Read-only |
| 8ABA4CF5BA4CDF77 | /mnt/internal-hdd | NTFS via ntfs-3g | Read-only |

## Compose projects

- Jellyfin: /opt/homelab/jellyfin/compose.yaml
- Navidrome: /opt/homelab/navidrome/compose.yaml
- Portainer: /opt/homelab/portainer/compose.yaml

## Startup recovery

Systemd unit files are stored in systemd/.

Restore them with:

```bash
sudo cp systemd/homelab-jellyfin.service /etc/systemd/system/
sudo cp systemd/homelab-navidrome.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable homelab-jellyfin.service homelab-navidrome.service
```

Inspect services and mounts with systemctl status and findmnt.
Validate /etc/fstab using sudo findmnt --verify --verbose before rebooting after mount changes.

The media drives must remain read-only unless their write requirements are reviewed.
A successful service start does not prove that startup works correctly when a drive is missing.
