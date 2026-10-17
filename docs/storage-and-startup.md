# Storage Mounts and Service Startup

## External media drives

The external drives were configured as read-only mounts in /etc/fstab.

| UUID | Mount point | Filesystem | Access | Role |
|---|---|---|---|---|
| AD95-6C91 | /mnt/immich | exFAT | Read-only | One Touch external 1 TB HDD; current Immich source |
| D09481079480F0F0 | /mnt/media | NTFS via ntfs-3g | Read-only | Kingston external 512 GB SSD; older Immich dataset, media and NextcloudData |

## Internal Seagate — current status

The internal Seagate ST1000LM048-2E7172, serial WL114BWW, is intended for primary bulk storage but is **on hold** after an ext4 format attempt failed with SATA I/O errors on 2026-10-16.

The old NTFS UUID 8ABA4CF5BA4CDF77 and mount point /mnt/internal-hdd are historical only. Do not use that UUID in an active mount entry. No successful ext4 filesystem or /srv/storage mount is confirmed.

Device letters changed after re-enumeration. In the latest lsblk output on 2026-10-17:
- /dev/sda = One Touch serial 00000000NACNBMA9, mounted /mnt/immich.
- /dev/sdb = Crucial BX500 serial 2239E66F361E, Ubuntu OS disk.
- /dev/sdc = Seagate serial WL114BWW, partition /dev/sdc1 with no recognized filesystem or mountpoint.
- /dev/sdd = Kingston serial 50026B7684B4198D, mounted /mnt/media.

Always identify disks by model and serial, never by a remembered /dev/sdX letter.

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

Inspect services and mounts with systemctl status and findmnt. Validate /etc/fstab using sudo findmnt --verify --verbose before rebooting after mount changes.

The current Compose files and systemd units still reference /mnt/internal-hdd; revisit these paths only after the storage incident is resumed and the final storage path is safely established. Missing-drive behaviour needs an explicit safeguard so a container cannot silently write into an empty mountpoint on the OS disk.

See docs/incidents/2026-10-16-seagate-sata-io-failure.md if present, and keep the storage/Immich migration on hold until explicitly resumed.
