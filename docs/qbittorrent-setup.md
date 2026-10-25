# qBittorrent on Knowhere

## Design

qBittorrent runs as a Docker Compose project and writes to the existing Kingston media drive mounted at `/mnt/media`. Jellyfin and Navidrome keep their current read-only bind mounts. qBittorrent can write into the shared media tree, while the media servers cannot change files.

- Compose: `qbittorrent/compose.yaml`
- Web UI: `http://knowhere:8080` on the trusted LAN or over Tailscale
- Config: `/srv/appdata/qbittorrent` on the OS SSD
- Shared media path inside qBittorrent: `/media`
- Suggested qBittorrent category paths:
  - Movies: `/media/Movies`
  - Shows: `/media/Shows`
  - Music: `/media/Music`
  - Optional incomplete/default downloads: `/media/Downloads`

The category paths are paths inside the container. The matching host paths are under `/mnt/media/Media`. Configure category save paths in qBittorrent after first login. Do not move or rename existing media.

## Important storage prerequisite

The Kingston mount was last documented as read-only. qBittorrent will not be able to write until the operator deliberately changes that mount to read-write. Verify the drive by UUID `D09481079480F0F0`, not by a device letter, and verify free space before making changes.

First inspect current state:

```bash
findmnt --mountpoint /mnt/media
findmnt --noheadings --output SOURCE,FSTYPE,OPTIONS --mountpoint /mnt/media
df -h /mnt/media
id srihari
```

Make a backup of `/etc/fstab` before editing it:

```bash
sudo cp -a /etc/fstab "/etc/fstab.pre-qbittorrent-$(date +%Y%m%d-%H%M%S)"
sudoedit /etc/fstab
```

Only edit the existing Kingston entry whose UUID is `D09481079480F0F0`. Change its mount options from read-only to read-write and set `uid` and `gid` to the numeric values returned by `id srihari`. A typical NTFS-3G entry uses `rw,nofail,norecover,uid=1000,gid=1000,umask=002`, but use the actual UID/GID and retain the correct filesystem type and other validated options from the live entry. Do not change the One Touch Immich mount or add the failed Seagate.

Validate before remounting:

```bash
sudo findmnt --verify --verbose
```

If verification passes and the drive is mounted at the expected UUID, remount it during a safe window:

```bash
sudo mount -o remount,rw /mnt/media
findmnt --noheadings --output SOURCE,FSTYPE,OPTIONS --mountpoint /mnt/media
```

Confirm the output identifies UUID `D09481079480F0F0` and includes `rw`. If remounting fails or the UUID does not match, stop and restore the backup instead of writing to the path.

Create the application config directory on the OS SSD:

```bash
sudo install -d -m 0750 -o 1000 -g 1000 /srv/appdata/qbittorrent
```

Replace 1000/1000 with the actual UID/GID from `id srihari` if different. Verify that the media directories exist before starting:

```bash
test -d /mnt/media/Media/Movies
test -d /mnt/media/Media/Shows
test -d /mnt/media/Media/Music
df -h /mnt/media
```

Do not create these directories if the Kingston UUID is not mounted at `/mnt/media`; otherwise files could accidentally be written to the OS SSD.

## Deploy

From the repository checkout on Knowhere:

```bash
cd /opt/homelab
git status --short
git fetch origin
git switch feat/qbittorrent-media-downloads
git pull --ff-only
```

Review the branch before deploying. Set `PUID` and `PGID` in `qbittorrent/compose.yaml` to the actual UID/GID for the account that owns the NTFS mount. Then validate and start:

```bash
cd /opt/homelab/qbittorrent
docker compose config
docker compose up -d
docker compose logs --tail=100 qbittorrent
```

The first start generates qBittorrent configuration. Open the Web UI and set a strong Web UI password immediately. Keep the UI limited to trusted LAN/Tailscale access; do not configure router port forwarding. Check the container logs for the temporary initial password if the image reports one.

## qBittorrent category setup

In the Web UI, set the default save path to `/media/Downloads` if that folder exists and you want a staging folder. For media you want indexed directly, create categories with these save paths:

- `movies` -> `/media/Movies`
- `shows` -> `/media/Shows`
- `music` -> `/media/Music`

These folders correspond to the existing host directories mounted read-only into Jellyfin and Navidrome. Do not enable any qBittorrent setting or script that deletes completed downloads automatically. If you seed torrents, leave completed files in place. Keep unrelated files and torrent metadata outside the media library folders where possible.

Navidrome scans hourly. Jellyfin may require a library scan or scheduled scan before new files appear. Ensure music is organised into artist/album folders and that downloads are legal to share and retain.

## Verify and rollback

Test with a small, lawful file first. Confirm qBittorrent can create and write a test download in the intended directory, then verify the file appears on the host and is visible to the relevant media server. Do not test by overwriting existing media.

If the drive is not mounted correctly, stop qBittorrent before doing anything else:

```bash
cd /opt/homelab/qbittorrent
docker compose down
```

If the mount must be returned to read-only, stop qBittorrent first, edit the backed-up fstab entry back to its previous options, validate with `sudo findmnt --verify --verbose`, then remount only when safe. Jellyfin and Navidrome retain their read-only container mounts regardless of the host's mount mode.
