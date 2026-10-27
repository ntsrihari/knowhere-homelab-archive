# MeshCentral (all my screens)

Live screen, control and files for my own PCs, laptops and Android phones, in the browser:
**https://100.104.30.99:4430** (knowhere's Tailscale address; reachable only on the tailnet). It uses its own
certificate, so a browser warns once. The IP is used instead of the MagicDNS name because some devices (the phone,
the TV) keep Tailscale DNS off for AdGuard.

- `meshcentral/compose.yaml`: official image `ghcr.io/ylianst/meshcentral:1.3.1`, host networking, bound to the
  Tailscale address only (`portBind`), no Intel AMT port, no plugins, no self-update.
- `meshcentral/data/config.json` is committed; everything else in `data/` (keys, certificates, database) is not.
- The first account created becomes the administrator; after that, set `"newAccounts": false` and restart.
- Agents: in MeshCentral, make a device group, then "Add Agent" for Windows (installer) or Android (MeshAgent app,
  scan the QR code). iPad/iOS can't be streamed continuously.
- A proper certificate would need `sudo tailscale set --operator=srihari` once on knowhere, then `tailscale serve`.
