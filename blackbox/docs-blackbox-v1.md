# Knowhere Black Box V1.1

Status: working prototype. All parser and manual-entry tests pass. Prepared for deployment on `knowhere`.

## Ongoing workflow — no Fuelio required

- GPSLogger on the Samsung S25 Ultra records new drives. Export a GPX after each trip and upload it to Black Box. Automatic GPSLogger file upload is not configured in this release.
- Fuel fill-ups and costs are recorded in the Black Box web UI. Full-to-full efficiency is calculated when valid prior/current full-tank entries include odometer readings.
- Fuelio-format CSV import is a legacy/historical import path only; it is not needed going forward.

## Data inspected

- One-time historical CSV: 311 trip summaries from 2026-07-22 through 2026-10-18; `TripDist` sums to 5,990.4 km after converting metres to kilometres.
- Historical CSV: 3 fuel entries totalling ₹9,514.77 and 85.17 litres; these records do not represent a complete fuel history through October.
- Historical CSV: 1 vehicle cost entry.
- GPSLogger: four GPX files, 1,195 stored points and 1,192 unique samples. Some samples contain GPS speed, with long gaps and several network-derived locations.
- Current detector creates 11 candidate drives from the supplied samples. These are heuristic results, not verified vehicle trips.

## Implementation

- Dependency-free Python HTTP server, SQLite database, local HTML/CSS/JavaScript UI.
- GPX importer skips exact duplicate files by SHA-256.
- Optional legacy CSV importer retains historical records and never stores VIN/plate.
- Authenticated POST endpoints `/api/fuel` and `/api/costs` create new entries directly in Black Box.
- Manual full-tank efficiency estimate is computed only with chronological full-tank entries, usable odometers, positive distance, positive litres and no intervening partial fill; otherwise efficiency remains unknown.
- DB path: `/srv/appdata/blackbox/blackbox.sqlite3` on OS SSD.
- HTTP Basic authentication on all personal data. Static health endpoint only is public for container checks.
- Route plot is local by default. Opening external OpenStreetMap requires confirmation.

## Known caveats

- Archived trip odometer readings are zero in the supplied trip rows.
- Six historical summaries have zero distance; thirteen exceed 100 km.
- Current GPX sampling is sparse and some points are network-source. Trip distance/speed outputs are estimates.
- No direct car ECU data is available without a supported vehicle adapter. This phone-only setup cannot provide engine RPM, fuel level, coolant temperature or DTC codes.
- Original CSV/GPX files are personal location data; keep them out of GitHub.

## Deployment

`sudo bash ./bootstrap.sh` in `blackbox/` generates `.env`, creates `/srv/appdata/blackbox`, and builds/starts the Compose stack. LAN URL `http://192.168.29.68:8787`; Tailscale URL `http://100.104.30.99:8787`. No router port-forwarding.
