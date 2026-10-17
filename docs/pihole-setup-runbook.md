# Pi-hole LAN DNS Runbook

**Target:** `knowhere` Ubuntu / Docker Compose  
**Pi-hole generation:** v6 (official `pihole/pihole:latest` image)  
**Status:** Pi-hole is running and passed host-side DNS/HTTP health checks on 2026-10-17 at candidate IP `192.168.29.68`. FTL is healthy, blocking is enabled, `example.com` resolved through Pi-hole, and the admin route returned HTTP 302. Router DHCP reservation and browser login remain pending. Do not advertise Pi-hole to the whole LAN until the address is reserved.

Pi-hole Docker v6 uses `FTLCONF_...` environment variables. This configuration follows the official Docker quick-start structure and v6 configuration method: [Pi-hole Docker docs](https://docs.pi-hole.net/docker/) and [Docker configuration docs](https://docs.pi-hole.net/docker/configuration/).

## Intended design

- `pihole/compose.yaml`: Pi-hole; DNS TCP/UDP 53 bound only to the configured LAN IP.
- Web admin: `http://<KNOWHERE-LAN-IP>:8081/admin`. Port 8081 avoids collisions with existing services.
- Persistent FTL/Pi-hole data: `/srv/appdata/pihole/etc-pihole` on the OS SSD.
- Upstream resolvers: Cloudflare `1.1.1.1` and Quad9 `9.9.9.9`.
- LAN DHCP remains on the home router. **Do not enable Pi-hole DHCP** as part of this setup.
- Keep DNS ports bound to the LAN address only. Do not set router port-forwarding for DNS or the Pi-hole admin page.
- Use a DHCP reservation on the router for the server IP; don't assume today's DHCP lease will remain stable.
- The current service does not configure itself as a public/remote DNS service.

## Observed LAN state — 2026-10-17

On the server when this runbook was updated:
- LAN interface: `enp0s31f6`
- Current DHCP IPv4: `192.168.29.68/24` (dynamic, so it must be reserved before using network-wide)
- Default gateway/router: `192.168.29.1`
- Ethernet MAC: `50:9a:4c:49:44:9d`
- `systemd-resolved` DNS listeners are only on loopback (`127.0.0.53:53` and `127.0.0.54:53`); the captured listener check showed no process on host ports 80, 443, or 8081.
- Candidate Pi-hole IPv4 for DHCP reservation: `192.168.29.68`. Verify reservation in the router before advertising it to LAN clients.

## DNS validation record — 2026-10-17

Read-only tests after initialization succeeded:
- Docker state: `running / healthy`.
- `pihole status`: FTL listening on TCP and UDP port 53; blocking enabled.
- `dig @192.168.29.68 example.com +time=3 +tries=1`: `NOERROR`, two A records returned, 109 ms.
- `curl` to `http://192.168.29.68:8081/admin/`: HTTP `302`, a redirect response consistent with the admin route.
- Not yet confirmed: interactive admin login, DNS query from another LAN client, DHCP reservation, or router-wide DNS cutover.

## First start record — 2026-10-17

Observed from the first `docker compose up -d`:
- Container `pihole` started with ports TCP/UDP 53 bound to `192.168.29.68` and the admin HTTP UI bound on `192.168.29.68:8081`.
- Initialization created its persistent gravity database and downloaded the default StevenBlack hosts source successfully: 73,634 unique gravity domains.
- Pi-hole log reported `DNS resolution is available` inside the container and then started FTL. At the recorded snapshot, Docker still reported `health: starting`; no successful client-side `dig`/`nslookup` or browser sign-in has yet been reported.
- The server IP remains a DHCP address until the user confirms a reservation in the router.

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

Use the LAN IPv4 address on the interface with the default route, not the Tailscale `100.x` address, `127.0.0.1`, or a Docker bridge address. Before making it the Pi-hole IP, create a DHCP reservation for it on your router or set a static LAN address outside the DHCP pool.

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
- `PIHOLE_IP=REPLACE_WITH_STATIC_LAN_IP` with the reserved IPv4 address of `knowhere`.
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

If the IP check prints no result, stop. Fix the reservation/address first. Verify with `ss` that no conflicting service already owns the selected IP's port 53 or port 8081.

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

## 4. Make Pi-hole DNS the default for the home network

**Recommended: configure DNS in the router's DHCP/LAN settings.** Use `<KNOWHERE-LAN-IP>` as the LAN DNS server distributed to clients. Keep the router as DHCP server. Do not add a public secondary DNS (e.g. 8.8.8.8) if the goal is to have every device use Pi-hole; clients may bypass Pi-hole by using the public secondary. Leave WAN/Internet DNS and port-forwarding alone unless the router's UI explicitly distinguishes these from LAN DHCP DNS.

Save the router change and renew one test client's Wi-Fi lease. Test browsing, streaming, and the Pi-hole query log. Only then renew the rest of the clients.

**Rollback:** restore the router's previous LAN DHCP DNS setting, then renew clients' leases. This returns devices to the old resolver without changing their per-device static settings.

Router settings vary by model. After Pi-hole passes tests, use the router's actual make/model UI to find LAN / DHCP Server / DNS Server (not WAN DNS, unless the router's interface explicitly says it advertises this resolver to LAN clients).

## 5. Device behavior

The router DHCP setting is the preferred way to cover Smart TVs, consoles, PCs, phones and tablets on home Wi-Fi without configuring each separately. Some devices or apps can use encrypted or hard-coded DNS and bypass network DNS settings.

- **Samsung/Android phones and tablets:** connect to the home Wi-Fi and leave **Private DNS** on `Automatic`; do not set a custom provider hostname such as `dns.google` or a third-party encrypted-DNS provider if you expect local Pi-hole filtering. If it still bypasses Pi-hole, test with Private DNS `Off` while on home Wi-Fi and compare the query log. Android settings path: Settings → Connections/Network & Internet → More connection settings or Private DNS, depending on the device. See [Google Android network settings](https://support.google.com/android/answer/9654714).
- **iPhone/iPad:** join the home Wi-Fi, ensure DNS is set to automatic from DHCP (unless deliberately doing a per-device manual test). Per-network controls are under Settings → Wi-Fi → the information button beside the connected network. See [Apple's Wi-Fi settings guide](https://support.apple.com/guide/iphone/manage-wi-fi-settings-iphw5gjwl8k2/ios). VPN/DNS profiles and iCloud Private Relay may affect some traffic.
- **Samsung/Android TV, Google TV, Chromecast, Fire TV, consoles:** use DHCP/automatic network settings after router DNS is changed. If a device has custom DNS fields, remove any manually entered public DNS values. Some hard-coded DNS devices may still bypass; consider router firewall rules only after confirming the exact router and avoiding rules that could break streaming.
- **Windows PCs:** DHCP/automatic DNS. Clear cached results with `ipconfig /flushdns` after renewing DHCP if needed.
- **Mobile data / away from home:** router DHCP DNS only applies while connected to the home network. For filtering on cellular or other Wi-Fi, configure a private VPN/Tailscale DNS path to an address the phone can reach; don't assume the LAN IP works remotely. This is a separate step once home DNS is stable.

Don't change all devices manually and don't force DNS through firewall rules yet. Verify the router-provided resolver first.

## 6. Confirm client DNS

On Windows PowerShell (while connected to home Wi-Fi):

```powershell
ipconfig /all
nslookup example.com <KNOWHERE-LAN-IP>
```

Check the DNS Servers line for the Wi-Fi adapter. On Android/iOS, check the connected Wi-Fi network details. Open the Pi-hole query log and confirm the client appears and that normal domain queries are received.

## Security and recovery notes

- Do not forward ports 53, 80, 443 or 8081 from the WAN to `knowhere`.
- The compose file binds ports to `PIHOLE_IP`, not all host addresses. Pi-hole v6 uses `FTLCONF_dns_listeningMode=ALL` because Docker bridge clients can appear to originate outside the Pi-hole container's local subnet; the host-side bind must remain LAN-IP-specific and firewall exposure must stay LAN-only.
- The password is local-only in `pihole/.env`, not in Git.
- Pi-hole provides DNS-based filtering, not a universal ad blocker; some apps and video platforms embed ads or use other DNS/HTTPS pathways.
- If Pi-hole goes down after router DNS is changed, restore prior router DHCP DNS or temporarily set a known working DNS server on the affected client.
