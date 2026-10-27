# Home Assistant on knowhere

Home Assistant runs in Docker (`homeassistant/compose.yaml`, host networking, `TZ=Asia/Kolkata`) and is reachable at
`http://knowhere:8123` over Tailscale or `http://192.168.29.68:8123` at home. Its live config lives in the Docker volume
`homeassistant_homeassistant_config`; `homeassistant/config/` in this repository is a copy of the files written by hand.

## Files

| Repository path | In the container | What |
|-|-|-|
| `config/configuration.yaml` | `/config/configuration.yaml` | Base config: packages, the "Home control" YAML dashboard, srihari-pc ping sensor |
| `config/packages/home_center.yaml` | `/config/packages/` | Home feed sensors, presence, TV pop-up and Wake PC scripts, automations |
| `config/packages/msedcl.yaml` | `/config/packages/` | Electricity: estimated per-device power, kWh, MSEDCL slab bill and forecast |
| `config/packages/smart.yaml` | `/config/packages/` | Rain-soon and hot-day warnings, photo-disk days-left forecast, evening summary on the TV |
| `config/packages/extras.yaml` | `/config/packages/` | Internet health, Pi-hole stats, message board for the TV |
| `config/dashboards/home.yaml` | `/config/dashboards/home.yaml` | The "Home control" dashboard |
| `config/scripts.yaml`, `config/automations.yaml` | `/config/` | TV scripts (apps, HDMI, sleep, movie night) and the night auto-off |
| `feed/collect.py` | host only, mounted read-only at `/feed` | Collector run every minute from srihari's crontab |

The Wake-on-LAN MAC address in `home_center.yaml` is replaced with `XX:XX:XX:XX:XX:XX` in this copy.
`feed/home.json`, `feed/seen.json` and `feed/names.json` (device lists and MAC addresses) are not committed.

Copy a file in: `cat file.yaml | docker exec -i homeassistant sh -c "cat > /config/packages/file.yaml"`, then
`docker exec homeassistant python -m homeassistant --script check_config -c /config` and restart the container.

## Home feed (`feed/collect.py`)

Standard library Python, cron `* * * * * /usr/bin/python3 /opt/homelab/homeassistant/feed/collect.py`. Writes
`feed/home.json` with:

- **tailscale:** every tailnet device; `at_home` when its direct path is a 192.168.29.x address.
- **lan:** a ping sweep plus the neighbour table, names from `names.json`, new-device detection from `seen.json`.
- **containers:** state and health of every container.
- **server:** load, memory, disk use, hottest temperature, uptime.
- **internet:** ping to 1.1.1.1, 8.8.8.8 and the router: online, latency, loss.
- **pihole:** today's lookups and blocked lookups, top blocked domains, read-only from Pi-hole's FTL database
  (`docker exec pihole pihole-FTL sqlite3 -readonly`).

## The Fire TV

Android Debug Bridge integration at `100.64.183.0:5555` (Tailscale): `media_player.fire_tv`, `remote.fire_tv`.
Pop-up cards on the TV come from the custom launcher's `PopupReceiver` (`script.tv_popup`). They're skipped while the
TV is off.

## Electricity, estimated

There is no MSEDCL integration and no meter hardware here, so consumption is estimated: each device's watts come from
whether Home Assistant sees it on (TV state, PC ping, ThinkPad at home on Tailscale, knowhere linear in CPU load), plus
always-on network boxes and one "everything else" average. Each is integrated to kWh (`integration`, left Riemann,
5-minute sub-interval), summed into `sensor.msedcl_meter` (the Energy dashboard's grid source, priced by
`sensor.msedcl_price_per_unit`) and the per-device sensors are the Energy dashboard's individual devices.
Tariff defaults are LT-I residential figures from an aggregator, unverified; all watts and rates are editable on the
dashboard. Tune "Everything else" once from a real bill.

## Automations

Welcome home, TV on while away (notice only), new device on the Wi-Fi, knowhere needs attention, PC came on, bedtime
nudge 00:20, three hours of TV, good morning card, TV night auto-off, over 300 units this month, internet down and
back (with outage length), rain soon, hot day, photos disk filling up, evening summary at 21:30.
