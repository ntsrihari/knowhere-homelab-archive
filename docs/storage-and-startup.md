# Storage Mounts and Service Startup

## External media drives

The external drives were configured as read-only mounts in /etc/fstab.

| UUID | Mount point | Filesystem | Access | Role |
|---|---|---|---|---|
| AD95-6C91 | /mnt/immich | exFAT | Currently read-only; planned read-write for Immich | One Touch external 1 TB HDD; existing ImmichData and Photos trees |
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

Jellyfin and Navidrome use only the read-only Kingston mount at /mnt/media; stale /mnt/internal-hdd bind mounts were removed. Their systemd units verify the Kingston UUID (D09481079480F0F0) and required media directories before starting.

Immich Linux configuration is prepared under /opt/homelab/immich. It preserves the Windows container paths: /mnt/immich/ImmichData maps to /data, and /mnt/immich/Photos maps read-only to /mnt/photos. The One Touch mount must be changed to read-write for Immich uploads; the Immich systemd unit refuses to start unless UUID AD95-6C91 is mounted at /mnt/immich with rw options and all expected directories present. PostgreSQL data is planned for /srv/appdata/immich/postgres on the OS SSD. Do not start Immich until the current database dump is restored into that production database.

The Seagate serial WL114BWW has now failed a second filesystem creation attempt with kernel I/O errors including an UNC read error, failed revalidation, and failed writes. It is not mounted at /srv/storage. Do not retry formatting or use it as an active mirror/cloud-storage target until the failure is resolved.

See [the incident record](incidents/2026-10-16-seagate-sata-io-failure.md), [the server inventory](server-inventory.md), and [migration status](migration-status.md). Storage and Immich migration remain on hold until explicitly resumed.
