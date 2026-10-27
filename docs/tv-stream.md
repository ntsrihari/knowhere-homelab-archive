# TV viewer (tv-stream)

See and control the Fire TV's screen in a browser from anywhere on Tailscale: **http://knowhere:8765** (also embedded
in Home Assistant's "Home control" dashboard). Pick the TV in the list, then a player ("H264 Converter" works on
phones). Picture and touch/keys only, no sound. DRM apps (Netflix, Prime) show black.

- Built from [NetrisTV/ws-scrcpy](https://github.com/NetrisTV/ws-scrcpy) (MIT), cloned into `tv-stream/src` at
  commit `cd6cea6ebde17af6685c2b325183fc24bef9d52b` (not committed here). Build options (a copy is `tv-stream/build.config.override.json`) in
  `src/build.config.override.json`: Android only, no adb shell, no file browser, no dev tools, video proxied through
  adb.
- `compose.yaml` publishes port 8765 on knowhere's Tailscale address only (`100.104.30.99`), because the viewer has no
  login and anyone who can open it can control the TV.
- `start.sh` uses Home Assistant's adb key (mounted from `tv-stream/keys/`, not committed), which the TV already
  allows, so there is no approval prompt on the TV. It connects to the TV's LAN address `192.168.29.60:5555`: the
  container's bridge network can't reach Tailscale addresses.

Rebuild: `git -C src pull && docker compose build && docker compose up -d`.
To recreate the key: `docker exec homeassistant cat /config/.storage/androidtv_adbkey > keys/adbkey && chmod 600 keys/adbkey`.
