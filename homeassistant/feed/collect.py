#!/usr/bin/env python3
"""Home feed for Home Assistant: who and what is on the network, and how knowhere is doing.

Runs every minute from cron (crontab of srihari) and writes /opt/homelab/homeassistant/feed/home.json,
which Home Assistant reads (mounted at /feed). Standard library only.

- tailscale: every tailnet device, online or not, and at_home when its direct path is a 192.168.29.x address
  (a `tailscale ping` sets the path up first), so a phone with Tailscale on says whether it is home.
- lan: every device that answered on the home Wi-Fi this run, named from names.json (edit it), with first_seen;
  new_devices lists MAC addresses first seen in the last 15 minutes.
- containers: docker state and health of every container.
- server: load, memory, disk and the hottest temperature sensor.
- internet: whether the internet answers (Cloudflare and Google), its latency and loss, and the router's.
- pihole: ad-blocking today (queries, blocked, top blocked domains, busiest device), read from Pi-hole's own
  database inside its container (read-only).
"""
import json, os, re, subprocess, time
from concurrent.futures import ThreadPoolExecutor

FEED = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(FEED, "home.json")
SEEN = os.path.join(FEED, "seen.json")
NAMES = os.path.join(FEED, "names.json")
LAN_PREFIX = "192.168.29."
NEW_WINDOW = 15 * 60


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def tailscale():
    try:
        st = json.loads(run(["tailscale", "status", "--json"]))
    except Exception:
        return []
    peers = list((st.get("Peer") or {}).values())

    def probe(p):
        ip = (p.get("TailscaleIPs") or [""])[0]
        via = ""
        if p.get("Online") and ip:
            out = run(["tailscale", "ping", "-c", "1", "--timeout", "2s", ip], timeout=5)
            m = re.search(r"via (\S+)", out)
            via = m.group(1) if m else ("DERP" if "DERP" in out else "")
        cur = p.get("CurAddr") or via or ""
        return {
            "name": (p.get("DNSName") or "").split(".")[0] or p.get("HostName"),
            "ip": ip,
            "os": p.get("OS"),
            "online": bool(p.get("Online")),
            "at_home": cur.startswith(LAN_PREFIX) or via.startswith(LAN_PREFIX),
            "path": cur,
            "last_seen": p.get("LastSeen"),
            "exit_node": bool(p.get("ExitNodeOption")),
        }

    with ThreadPoolExecutor(12) as ex:
        devices = list(ex.map(probe, peers))
    me = st.get("Self") or {}
    devices.append({"name": (me.get("DNSName") or "knowhere").split(".")[0], "ip": (me.get("TailscaleIPs") or [""])[0],
                    "os": me.get("OS"), "online": True, "at_home": True, "path": "self", "last_seen": None, "exit_node": False})
    return sorted(devices, key=lambda d: d["name"] or "")


def lan(now):
    # A quick sweep so the neighbour table is fresh, then read it.
    with ThreadPoolExecutor(64) as ex:
        list(ex.map(lambda i: run(["ping", "-c", "1", "-W", "1", f"{LAN_PREFIX}{i}"], timeout=3), range(1, 255)))
    names = load(NAMES, {})
    seen = load(SEEN, {})
    out = []
    for line in run(["ip", "-4", "neigh", "show"]).splitlines():
        m = re.match(r"(\S+) dev \S+ lladdr (\S+) (\S+)", line)
        if not m or not m.group(1).startswith(LAN_PREFIX) or m.group(3) in ("FAILED", "INCOMPLETE"):
            continue
        ip, mac = m.group(1), m.group(2).lower()
        first = seen.setdefault(mac, now)
        out.append({"ip": ip, "mac": mac, "name": names.get(mac) or names.get(ip) or "", "first_seen": first,
                    "private_mac": int(mac[1], 16) & 2 == 2})
    with open(SEEN + ".tmp", "w") as f:
        json.dump(seen, f)
    os.replace(SEEN + ".tmp", SEEN)
    return sorted(out, key=lambda d: int(d["ip"].rsplit(".", 1)[1]))


def containers():
    out = []
    for line in run(["docker", "ps", "-a", "--format", "{{json .}}"]).splitlines():
        try:
            c = json.loads(line)
        except Exception:
            continue
        status = c.get("Status", "")
        health = "unhealthy" if "unhealthy" in status else ("healthy" if "healthy" in status else "")
        out.append({"name": c.get("Names"), "state": c.get("State"), "health": health, "status": status})
    return sorted(out, key=lambda d: d["name"] or "")


def server():
    load1, load5, _ = os.getloadavg()
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0])
    used_pct = round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)
    disks = {}
    for mount in ("/", "/mnt/media", "/mnt/immich"):
        try:
            s = os.statvfs(mount)
            if s.f_blocks:
                disks[mount] = round(100 * (1 - s.f_bavail / s.f_blocks), 1)
        except Exception:
            pass
    temps = []
    import glob
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp") + glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            temps.append(int(open(path).read()) / 1000)
        except Exception:
            pass
    uptime = float(open("/proc/uptime").read().split()[0])
    return {"load1": round(load1, 2), "load5": round(load5, 2), "cpus": os.cpu_count(), "mem_used_pct": used_pct,
            "disk_used_pct": disks, "temp_max_c": max([t for t in temps if 0 < t < 120], default=None),
            "uptime_h": round(uptime / 3600, 1)}


def ping(host, count=3):
    out = run(["ping", "-c", str(count), "-W", "1", "-i", "0.3", host], timeout=8)
    m = re.search(r"(\d+)% packet loss", out)
    loss = int(m.group(1)) if m else 100
    m = re.search(r"= [\d.]+/([\d.]+)/", out)
    return {"loss_pct": loss, "ms": round(float(m.group(1)), 1) if m else None}


def internet():
    hosts = {"cloudflare": "1.1.1.1", "google": "8.8.8.8", "router": LAN_PREFIX + "1"}
    with ThreadPoolExecutor(3) as ex:
        res = dict(zip(hosts, ex.map(ping, hosts.values())))
    up = [res[k] for k in ("cloudflare", "google") if res[k]["loss_pct"] < 100]
    return {"online": bool(up), "ms": min((r["ms"] for r in up if r["ms"] is not None), default=None),
            "loss_pct": min((r["loss_pct"] for r in up), default=100), "router_ok": res["router"]["loss_pct"] < 100,
            "router_ms": res["router"]["ms"]}


BLOCKED = "(1,4,5,6,7,8,9,10,11,15,16)"


def pihole():
    t = time.localtime()
    midnight = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))

    def q(sql):
        out = run(["docker", "exec", "pihole", "pihole-FTL", "sqlite3", "-readonly", "-separator", "	",
                   "/etc/pihole/pihole-FTL.db", sql], timeout=15)
        return [line.split("	") for line in out.strip().splitlines() if line]

    rows = q(f"select count(*), coalesce(sum(status in {BLOCKED}),0) from queries where timestamp >= {midnight}")
    if not rows or len(rows[0]) != 2:
        return None
    total, blocked = int(rows[0][0]), int(rows[0][1])
    top = q(f"select domain, count(*) c from queries where timestamp >= {midnight} and status in {BLOCKED} "
            "group by domain order by c desc limit 5")
    busiest = q(f"select client, count(*) c from queries where timestamp >= {midnight} "
                "group by client order by c desc limit 1")
    return {"queries": total, "blocked": blocked, "blocked_pct": round(100 * blocked / total, 1) if total else 0,
            "top_blocked": [{"domain": d, "count": int(c)} for d, c in top],
            "busiest_client": busiest[0][0] if busiest else None}


def main():
    now = int(time.time())
    # The first run only learns what is already here: nothing is "new" until something joins after it.
    baseline = not os.path.exists(SEEN)
    devices = lan(now)
    feed = {
        "updated": now,
        "tailscale": tailscale(),
        "lan": devices,
        "new_devices": [] if baseline else [d for d in devices if now - d["first_seen"] < NEW_WINDOW],
        "containers": containers(),
        "server": server(),
        "internet": internet(),
        "pihole": pihole(),
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(feed, f, separators=(",", ":"))
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
