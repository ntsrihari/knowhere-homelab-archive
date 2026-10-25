# Knowhere Black Box — V1.1

Knowhere Black Box is a private, self-hosted vehicle-history app. **Fuelio is not required for ongoing use.** GPSLogger is the source for new trip tracks; the Black Box web UI is where you record new fuel fill-ups and vehicle expenses. A past Fuelio-format CSV can be imported once to preserve old history, but no repeated Fuelio export is part of the workflow.

The app uses Python's standard library, SQLite, and a small local HTML/CSS/JavaScript interface. It requires no external Python packages, cloud accounts, analytics, remote fonts, or CDN scripts. The default route display is a local coordinate plot; opening an OpenStreetMap view is an explicit opt-in.

## Normal workflow (no Fuelio)

1. Record drives with GPSLogger on the S25 Ultra, with GPS/GNSS or fused location enabled and a sensible sampling interval.
2. After a drive, export the track as GPX from GPSLogger and use **Import data** or **Add GPX** in Black Box. Byte-identical files are skipped, so retrying an upload does not duplicate points.
3. After refuelling, open **Fuel & costs → Log refuelling** and enter date/time, odometer, litres, total amount and whether the tank was filled fully. When consecutive full-tank entries have odometer readings and no partial fill occurred between them, the app calculates a full-to-full km/L estimate; otherwise it leaves efficiency unknown.
4. Use **Add expense** for service, tolls, parking, insurance, cleaning and other vehicle costs.
5. Download a SQLite backup regularly from the UI and keep it on healthy storage.

GPSLogger recording and Black Box ingestion are separate steps in this version: export/upload the GPX after the drive. Automatic upload from GPSLogger is not enabled by this package yet.

## Optional one-time historical import

The seeded deployment archive includes the user's supplied historical CSV and four GPX exports under `private-imports/`. On first boot, the app imports these once into the database if the database contains no imported source files. This preserves older trip summaries and the old fuel/expense rows while leaving future entries under Black Box control.

A source-only archive has an empty `private-imports/` directory; import a GPX through the UI to begin, and/or add your old CSV if you want the historical archive. You do **not** need to continue maintaining Fuelio or exporting new CSVs. The existing files contain sensitive location and vehicle history. Never commit `private-imports/` or the SQLite database to GitHub; `.gitignore` excludes them.

The app does not store the VIN or registration plate from the old export. Source trip coordinates and timestamps, GPS points, fill-ups and vehicle expenses are stored in the local database because they are needed by Black Box.

## Features

- Imports GPSLogger GPX tracks, including timestamps, coordinates, elevation, speed, source, satellites and HDOP/VDOP/PDOP where present.
- Optional legacy CSV parser for `TripLog`, `Log`, and `Costs`; refreshed historical exports upsert by stable row GUID without deleting rows absent from a later export.
- Manual fuel fill-up and expense entry in the web UI. Actual fill-up purchases remain separate from estimated historic trip costs.
- Dashboard for historic trip summaries, GPS samples, candidate drives, recorded fill-ups, litres and costs.
- Local coordinate plots; gaps longer than four minutes are broken instead of connected into fictitious straight segments.
- Heuristic candidate-drive detection from GPS speed samples. Candidates are approximate, not verified trips or odometer readings.
- Data-quality flags for incomplete old records and sparse GPS sampling.
- Duplicate upload detection using SHA-256.
- Downloadable SQLite database backup.
- HTTP Basic authentication; startup refuses an empty/default password.

## Deploy on Knowhere

Requirements: Docker Engine and the Docker Compose v2 plugin.

1. Copy/extract this directory to `/opt/homelab/blackbox` on Knowhere, preserving `private-imports/` if you want the supplied history seeded.
2. Run:

   ```bash
   cd /opt/homelab/blackbox
   sudo bash ./bootstrap.sh
   ```

   Bootstrap generates a high-entropy password in `.env` if absent, sets mode `600`, prepares `/srv/appdata/blackbox` on the OS SSD, builds the image and starts the container.
3. Save the printed `blackbox` password in a password manager. The password remains in the local `.env`, excluded from Git.
4. Open the app from a trusted device:
   - LAN: `http://192.168.29.68:8787` (update if Knowhere's DHCP address changes)
   - Tailscale: `http://100.104.30.99:8787`
   - `http://knowhere:8787` if the client resolves that name.

Do not port-forward TCP 8787 on the router. Use Tailscale for remote access. HTTP Basic authentication is not transport encryption on an untrusted LAN; only use the service on trusted networks or add private HTTPS before accessing it on untrusted networks.

## Data caveats from the current export

- The old CSV's trip distance is in metres and is converted to km. Its trip start/end odometer fields are zero, so Black Box does not infer those readings.
- The archived CSV has only three historical fill-up entries (July) and one expense entry. All future fill-ups and costs should be entered directly in Black Box.
- Six historical trip rows have zero distance and thirteen exceed 100 km; they are retained and flagged, not silently modified.
- Four supplied GPX files contain 1,195 points and 1,192 unique point samples after overlap deduplication. Many intervals are sparse and some points use the network provider.
- GPS-derived speed/distance is not a substitute for the car's odometer or ECU telemetry. This phone-only setup cannot provide engine RPM, tank level, coolant temperature or diagnostic trouble codes.

## Tests

On Python 3.12+:

```bash
python -m unittest discover -s tests -v
```

## Operations

```bash
cd /opt/homelab/blackbox
docker compose ps
docker compose logs --tail=100 blackbox
docker compose restart blackbox
docker compose down
```

`docker compose down` leaves `/srv/appdata/blackbox` and `private-imports/` in place.

## Privacy and storage

- No outbound calls are made by the backend.
- No third-party JS libraries, fonts or analytics are loaded.
- The default route plot is local. Clicking **External street map…** asks for confirmation and shares the selected route's approximate centre with OpenStreetMap via the browser URL.
- The unauthenticated `/api/health` endpoint only returns static service health; all personal-data endpoints require authentication.
- The SQLite database is stored at `/srv/appdata/blackbox/blackbox.sqlite3` on the Crucial system SSD, **not** on the failing internal Seagate HDD.
- GPX tracks reveal precise movement history. Treat the database, backups, and seeded ZIP as private personal data.
