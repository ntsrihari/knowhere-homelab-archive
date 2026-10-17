# Pi-hole LAN DNS Runbook

**Target:** `knowhere` Ubuntu / Docker Compose  
**Pi-hole generation:** v6 (official `pihole/pihole:latest` image)  
**Status:** Pi-hole DNS is answering on both LAN IP `192.168.29.68` and Tailscale IP `100.104.30.99` as of 2026-10-17. Host-side `dig` tests returned `NOERROR` through both IPs; admin HTTP returned `302` through both. The container showed `health: starting` 9 seconds after recreation and requires a later health recheck. The user will not change router configuration; local clients will be configured individually and Tailscale clients can use the server's Tailscale IP. The LAN IP is DHCP-assigned and can change.

Pi-hole Docker v6 uses `FTLCONF_...` environment variables. This configuration follows the official Docker quick-start structure and v6 configuration method: [Pi-hole Docker docs](https://docs.pi-hole.net/docker/) and [Docker configuration docs](https://docs.pi-hole.net/docker/configuration/).

## Intended design

- `pihole/compose.yaml`: Pi-hole; DNS TCP/UDP 53 and admin HTTP bound separately to the configured LAN IP and Tailscale IP (not to a wildcard host address).
- Web admin: `http://<KNOWHERE-LAN-IP>:8081/admin`. Port 8081 avoids collisions with existing services.
- Persistent FTL/Pi-hole data: `/srv/appdata/pihole/etc-pihole` on the OS SSD.
- Upstream resolvers: Cloudflare `1.1.1.1` and Quad9 `9.9.9.9`.
- LAN DHCP remains on the home router. **Do not enable Pi-hole DHCP** as part of this setup.
- Bind DNS/admin only to the LAN address and Tailscale address. Do not set router port-forwarding for DNS or the Pi-hole admin page.
- The user will not change router configuration. Configure DNS manually on each target client. The server address is a DHCP lease; if it changes, device DNS settings must be updated. Never assume the current IP will remain stable.
- No router changes are required. Local-only devices point to `192.168.29.68`; remote devices with Tailscale use `100.104.30.99` or the tailnet's global nameserver setting. No port-forwarding or public DNS exposure is intended.

## Observed LAN state — 2026-10-17

On the server when this runbook was updated:
- LAN interface: `enp0s31f6`
- Current DHCP IPv4: `192.168.29.68/24` (dynamic, so it must be reserved before using network-wide)
- Default gateway/router: `192.168.29.1`
- Ethernet MAC: `50:9a:4c:49:44:9d`
- `systemd-resolved` DNS listeners are only on loopback (`127.0.0.53:53` and `127.0.0.54:53`); the captured listener check showed no process on host ports 80, 443, or 8081.
- Candidate Pi-hole IPv4 for DHCP reservation: `192.168.29.68`. Verify reservation in the router before advertising it to LAN clients.

## DNS validation record — 2026-10-17

Read-only tests after initialization and after enabling the Tailscale binding:
- Docker state: `running / healthy`.
- `pihole status`: FTL listening on TCP and UDP port 53; blocking enabled.
- `dig @192.168.29.68 example.com +time=3 +tries=1`: `NOERROR`, two A records returned, 109 ms.
- `curl` to `http://192.168.29.68:8081/admin/`: HTTP `302`, a redirect response consistent with the admin route.
- Not yet confirmed: interactive admin login, DNS query from another LAN client, DHCP reservation, or router-wide DNS cutover.

## Dual-address test after Compose recreation — 2026-10-17

The operator updated the local ignored `.env` with `PIHOLE_IP=192.168.29.68` and `PIHOLE_TAILSCALE_IP=100.104.30.99`, reloaded systemd, and ran the guarded startup service. Observed results:
- Container status at the 9-second snapshot: `running / starting` (health had not yet gone green at that moment).
- `dig @192.168.29.68 example.com +time=3 +tries=1`: `NOERROR`, two A records; 17 ms.
- `dig @100.104.30.99 example.com +time=3 +tries=1`: `NOERROR`, two A records; 3 ms.
- Admin HTTP response: LAN 302, Tailscale 302.
- Port listings show DNS TCP/UDP 53 and admin TCP 8081 bound only to `192.168.29.68` and `100.104.30.99`; not to a wildcard host address.
- These queries were run on `knowhere` itself. A test from a separate Tailscale client has not yet been recorded.

## First start record — 2026-10-17

Observed from the first `docker compose up -d`:
- Container `pihole` started with ports TCP/UDP 53 bound to `192.168.29.68` and the admin HTTP UI bound on `192.168.29.68:8081`.
- Initialization created its persistent gravity database and downloaded the default StevenBlack hosts source successfully: 73,634 unique gravity domains.
- Pi-hole log reported `DNS resolution is available` inside the container and then started FTL. At the recorded snapshot, Docker still reported `health: starting`; no successful client-side `dig`/`nslookup` or browser sign-in has yet been reported.
- The server IP remains a DHCP address until the user confirms a reservation in the router.

## Tailscale reachability (no router change)

A previous SSH connection resolved `knowhere` as `100.104.30.99`; always confirm the active Tailscale address with `tailscale ip -4` before adding it to local `pihole/.env`.

- Pi-hole LAN endpoint: DNS `192.168.29.68:53`; admin `http://192.168.29.68:8081/admin`.
- Pi-hole Tailscale endpoint: DNS `100.104.30.99:53` (verify current address); admin `http://100.104.30.99:8081/admin`.
- The Compose file binds host ports on both explicit host addresses. It must not publish DNS or admin on `0.0.0.0`.
- The server's Tailscale service must be up before the Compose container starts; the systemd unit requires `tailscaled.service` and checks the configured IP. After updating the `.env`, install and enable the unit so boot order is predictable.

### Configure Tailscale clients without touching the router

For devices that have Tailscale and should use Pi-hole both at home and away, the reliable central option is to add `100.104.30.99` as a **Global nameserver** in the Tailscale admin console → DNS. Keep MagicDNS enabled. Test it on one device before enabling **Override DNS servers** for the full tailnet; that override makes Tailscale devices use the tailnet-defined nameservers instead of their local DNS. Only do this after Pi-hole responds on the Tailscale IP and the device can reach it. Official docs: [DNS in Tailscale](https://tailscale.com/docs/reference/dns-in-tailscale) and [MagicDNS](https://tailscale.com/docs/features/magicdns).

For devices that must be configured only locally (such as most Smart TVs and consoles without Tailscale), set their DNS manually to `192.168.29.68`. These devices will use Pi-hole only while on the home LAN. If `knowhere`'s DHCP address changes, the manual entries need updating because router reservations are not being configured.

## 1. Detect LAN IP, gateway, and port conflicts (read-only)

On `knowhere`:

```bash
echo "=== IPv4 addresses ==="
ip -4 -br address

echo
echo "=== Default gateway and interface ==="
ip -4 route show default

echo
echo "=== DNS/HTTP listening sockets ==="
sudo ss -lntup | grep -E '(:53|:80|:443|:8081)([[:space:]]|$)' || true

echo
echo "=== Host resolver ==="
resolvectl status 2>/dev/null | sed -n '1,100p' || cat /etc/resolv.conf
```

Use the LAN IPv4 address on the interface with the default route, not the Tailscale `100.x` address, `127.0.0.1`, or a Docker bridge address. Router configuration is intentionally not part of this rollout. The LAN IP is a DHCP lease; per-device DNS works as long as it remains `192.168.29.68`. If the server receives a different LAN IP, update `PIHOLE_IP` in `.env`, recreate the Compose service, and update each device's manual DNS setting. Do not guess a static address outside the DHCP pool.

Inspect the existing `ss` output. A resolver listening only on `127.0.0.53:53` does not necessarily conflict with Pi-hole bound to the LAN IP; a listener on the LAN IP or wildcard `0.0.0.0:53` can conflict. Don't stop systemd-resolved blindly.

## 2. Prepare the environment file

```bash
cd /opt/homelab
git pull --ff-only

mkdir -p /srv/appdata/pihole/etc-pihole
cp pihole/.env.example pihole/.env
chmod 600 pihole/.env

openssl rand -hex 24
nano pihole/.env
```

In `pihole/.env`, replace:
- `PIHOLE_IP=REPLACE_WITH_LAN_IP` with the currently assigned LAN IPv4 (`192.168.29.68` unless changed).
- `PIHOLE_TAILSCALE_IP=REPLACE_WITH_TAILSCALE_IP` with the output of `tailscale ip -4` on `knowhere` (previously observed as `100.104.30.99`).
- `PIHOLE_PASSWORD=REPLACE_WITH_RANDOM_PASSWORD` with the output of `openssl rand -hex 24`.

Do not paste or commit the real password. The repository ignores `.env`.

## 3. Validate and start Pi-hole

Check that the chosen address is present on the server, then validate Compose:

```bash
cd /opt/homelab/pihole
set -a
. ./.env
set +a

ip -o -4 address show | awk '{print $4}' | cut -d/ -f1 | grep -Fx "$PIHOLE_IP"
docker compose config -q
```

If either IP check prints no result, stop and correct the local `.env` values. The LAN address remains a DHCP lease and may need to be changed in each device if it changes. Verify with `ss` that no conflicting service already owns the selected IP's port 53 or port 8081.

Then start:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker logs --tail 100 pihole
```

Test DNS from `knowhere` (install `dnsutils` if `dig` is unavailable):

```bash
dig @"$PIHOLE_IP" example.com
dig @"$PIHOLE_IP" doubleclick.net
```

The first query should return a normal answer; the second may be blocked or resolve to a configured block response depending on active lists and domain policy. Confirm the Pi-hole dashboard shows live queries at `http://$PIHOLE_IP:8081/admin`, log in with the password in your local `.env`, and add/confirm a maintained blocklist before changing network-wide DNS.

After testing succeeds, install the guarded startup unit:

```bash
cd /opt/homelab
sudo install -m 0644 systemd/homelab-pihole.service /etc/systemd/system/homelab-pihole.service
sudo systemctl daemon-reload
sudo systemctl enable homelab-pihole.service
```

Don't enable/restart the unit if the address isn't stable or the service tests fail.

## 4. Configure each device manually (no router changes)

The user will not change the router. Set DNS per local-only device, starting with one test device. Use `192.168.29.68` as the current Pi-hole DNS address. For devices with Tailscale that need filtering away from home, use the Tailscale nameserver path below. Do **not** enter a public secondary resolver if the goal is for this device's queries to be filtered by Pi-hole; a public secondary can bypass filtering.

### Windows 11 PC

1. Settings → Network & internet → Wi-Fi (or Ethernet) → the connected network / adapter.
2. Find **DNS server assignment** and select **Edit** → **Manual**.
3. Turn IPv4 on. Set **Preferred DNS** to `192.168.29.68`.
4. Leave Alternate DNS blank if the UI permits. Set DNS over HTTPS to **Off** for this manually configured LAN resolver unless you explicitly configure a matching DoH endpoint.
5. Save. In PowerShell run `ipconfig /flushdns`, then `nslookup example.com 192.168.29.68`.

To undo: return DNS server assignment to **Automatic (DHCP)**. Microsoft documents the adapter-specific manual DNS controls [here](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows).

### iPhone / iPad

1. Settings → Wi-Fi → tap the ⓘ next to the connected home network.
2. Configure DNS → Manual.
3. Remove existing DNS servers from the list, add `192.168.29.68`, then Save.
4. Open a few websites and check Pi-hole's Query Log.

To undo: return Configure DNS to **Automatic**. Apple documents per-network Wi-Fi settings [here](https://support.apple.com/en-ie/guide/iphone/iphw5gjwl8k2/27/ios/27). This affects that Wi-Fi network, not cellular data.

### Samsung Galaxy S25 Ultra (Android)

Android's **Private DNS** field expects a provider hostname, not a plain LAN IP address; Pi-hole in this deployment is not configured as a DNS-over-TLS hostname. Do not enter `192.168.29.68` in the Private DNS hostname field.

Two per-device options:

- **Preferred no-router option:** install the [Rethink DNS + Firewall](https://rethinkdns.com/download) app, then Configure → DNS → Other DNS → DNS 53, add `192.168.29.68` on port `53`, select it, and start the app. This uses Android's local VPN mechanism. Verify queries appear in Pi-hole's Query Log. Because the Pi-hole IP is private to the home LAN, DNS will not reach it while away from home; stop/pause this profile outside home or configure an appropriate fallback/automation before relying on it.
- **Built-in Wi-Fi manual DNS:** Settings → Connections → Wi-Fi → gear beside the home network → View more / IP settings → Static. Keep the current IP address, gateway and prefix displayed for that Wi-Fi network and set DNS 1 to `192.168.29.68`. Do not guess an IP address. This changes the phone from DHCP to static addressing and could cause an IP conflict after the router's lease expires, so prefer the app method if you cannot verify a safe static IP.

Leave Private DNS at **Automatic** when using the app-based method; don't set a public provider hostname such as `dns.google` or `one.one.one.one` if you expect Pi-hole to see the phone's lookups. Google's Android Private DNS modes are described [here](https://support.google.com/android/answer/9654714?hl=en); Samsung's network settings vary by software version.

### Samsung Smart TV

On many Samsung TV models: All Settings → Connection (or General) → Network → Network Status → IP Settings → DNS Setting → Enter Manually → DNS Server. Enter `192.168.29.68`, save, then test an app such as YouTube and review Pi-hole Query Log. Leave IP assignment automatic if DNS can be edited independently.

To undo: change DNS Setting back to **Obtain automatically**. Menu names vary by model. Samsung's support guide shows this manual DNS path [here](https://www.samsung.com/us/support/troubleshoot/TSG10002209/).

### PlayStation 5

1. Settings → Network → Settings → Set Up Internet Connection.
2. Highlight the connected network, open Advanced Settings, and set DNS Settings to Manual.
3. Set Primary DNS to `192.168.29.68`. If Secondary DNS is mandatory, use the same Pi-hole address rather than a public resolver.
4. Leave IP Address Settings on Automatic, then save and test the connection.

To undo: set DNS Settings back to Automatic. Sony documents DNS as an Advanced Setting [here](https://www.playstation.com/en-us/support/connectivity/internet-connect-playstation/).

### Other TVs, consoles, tablets and streaming devices

In the device's current network details, look for **DNS**, **DNS server**, or **IP settings**. If DNS can be edited independently, enter `192.168.29.68` and leave IP assignment automatic. If the UI requires the whole IP profile to be set to Static, do not guess IP/gateway values; get the currently assigned values from that device first and be aware of address-collision risk. For models that don't expose DNS settings, per-device Pi-hole may not be possible without an app/VPN or a router-side change.

### Mobile data and away from home

Per-Wi-Fi DNS settings apply only to the configured home network. They don't route mobile data through Pi-hole. To filter remotely, the phone must have a secure path to Pi-hole (for example, a properly configured private VPN/Tailscale path); Pi-hole must not be exposed directly to the public internet.

## 5. Test one device before rolling out to the rest

1. Configure just one client.
2. Browse normally.
3. Open `http://192.168.29.68:8081/admin` → Query Log. Confirm the client makes queries.
4. If internet DNS works but queries don't appear, the client may use encrypted DNS, VPN DNS, IPv6 DNS, or an app-specific resolver. Don't force firewall redirects yet.
5. After the first device is stable, repeat the same per-device change on the others.

For Windows, use:

```powershell
ipconfig /flushdns
nslookup example.com 192.168.29.68
```

For other devices, a query visible in Pi-hole's Query Log is the strongest quick confirmation that they are using this server.

## Security and recovery notes

- Do not forward ports 53, 80, 443 or 8081 from the WAN to `knowhere`.
- The compose file binds host ports separately to `PIHOLE_IP` and `PIHOLE_TAILSCALE_IP`, not to all host addresses. Pi-hole v6 uses `FTLCONF_dns_listeningMode=ALL` because Docker bridge clients can appear to originate outside the Pi-hole container's local subnet; host port binding is limited to the LAN and Tailscale addresses. Do not add router port-forwarding.
- The password is local-only in `pihole/.env`, not in Git.
- Pi-hole provides DNS-based filtering, not a universal ad blocker; some apps and video platforms embed ads or use other DNS/HTTPS pathways.
- If Pi-hole goes down after router DNS is changed, restore prior router DHCP DNS or temporarily set a known working DNS server on the affected client.
