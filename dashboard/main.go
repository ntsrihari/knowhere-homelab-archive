package main

import (
    "bufio"
    "bytes"
    "crypto/tls"
    "embed"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "io"
    "log"
    "net"
    "net/http"
    "os"
    "path/filepath"
    "regexp"
    "sort"
    "strconv"
    "strings"
    "sync"
    "time"
)

//go:embed index.html
var indexHTML []byte

//go:embed icons
var iconFS embed.FS

var iconNameRe = regexp.MustCompile(`^[a-z0-9-]+\.svg$`)

type Reach struct {
    Iface  string `json:"iface"`
    Group  string `json:"group"`
    IP     string `json:"ip"`
    Family string `json:"family"`
    Scope  string `json:"scope"`
    URL    string `json:"url,omitempty"`
}

type PortInfo struct {
    Proto    string  `json:"proto"`
    Bind     string  `json:"bind"`
    Port     int     `json:"port"`
    Wildcard bool    `json:"wildcard"`
    Scheme   string  `json:"scheme,omitempty"`
    Reach    []Reach `json:"reach"`
    Aliases  []Alias `json:"aliases,omitempty"`
}

type Alias struct {
    Label string `json:"label"`
    URL   string `json:"url"`
}

type Service struct {
    ID       string     `json:"id"`
    Name     string     `json:"name"`
    Source   string     `json:"source"`
    State    string     `json:"state"`
    Health   string     `json:"health,omitempty"`
    StatusText string   `json:"status_text,omitempty"`
    Image    string     `json:"image,omitempty"`
    Compose  string     `json:"compose,omitempty"`
    NetMode  string     `json:"network_mode,omitempty"`
    Ports    []PortInfo `json:"ports"`
    Internal []string   `json:"internal_ports,omitempty"`
}

type Iface struct {
    Name  string   `json:"name"`
    Group string   `json:"group"`
    Addrs []string `json:"addrs"`
}

type Snapshot struct {
    Host        string    `json:"host"`
    Interfaces  []Iface   `json:"interfaces"`
    Services    []Service `json:"services"`
    Binds       []string  `json:"dashboard_binds"`
    DockerError string    `json:"docker_error,omitempty"`
}

// ---------- published snapshot (shared with HTTP handlers) ----------

var (
    mu      sync.RWMutex
    current []byte
    changed = make(chan struct{})
)

func publish(b []byte) {
    mu.Lock()
    defer mu.Unlock()
    if bytes.Equal(b, current) {
        return
    }
    current = b
    close(changed)
    changed = make(chan struct{})
}

// ---------- kernel sockets ----------

type sock struct {
    proto string
    ip    net.IP
    port  int
    inode uint64
}

func parseHexIP(s string) net.IP {
    b, err := hex.DecodeString(s)
    if err != nil || (len(b) != 4 && len(b) != 16) {
        return nil
    }
    ip := make(net.IP, len(b))
    for i := 0; i < len(b); i += 4 {
        ip[i], ip[i+1], ip[i+2], ip[i+3] = b[i+3], b[i+2], b[i+1], b[i]
    }
    return ip
}

func readSocks(path, proto string) []sock {
    f, err := os.Open(path)
    if err != nil {
        return nil
    }
    defer f.Close()
    var out []sock
    sc := bufio.NewScanner(f)
    sc.Scan()
    for sc.Scan() {
        fl := strings.Fields(sc.Text())
        if len(fl) < 10 {
            continue
        }
        if proto == "tcp" && fl[3] != "0A" {
            continue
        }
        if proto == "udp" && (fl[3] != "07" || !strings.HasSuffix(fl[2], ":0000")) {
            continue
        }
        la := strings.Split(fl[1], ":")
        if len(la) != 2 {
            continue
        }
        ip := parseHexIP(la[0])
        port, e1 := strconv.ParseUint(la[1], 16, 32)
        inode, e2 := strconv.ParseUint(fl[9], 10, 64)
        if ip == nil || e1 != nil || e2 != nil || port == 0 {
            continue
        }
        out = append(out, sock{proto, ip, int(port), inode})
    }
    return out
}

func allSocks() []sock {
    var s []sock
    s = append(s, readSocks("/proc/net/tcp", "tcp")...)
    s = append(s, readSocks("/proc/net/tcp6", "tcp")...)
    s = append(s, readSocks("/proc/net/udp", "udp")...)
    s = append(s, readSocks("/proc/net/udp6", "udp")...)
    return s
}

// socket inode -> pids holding it (needs pid: host and ptrace rights)
func inodeOwners() map[uint64][]int {
    m := map[uint64][]int{}
    ents, _ := os.ReadDir("/proc")
    for _, e := range ents {
        pid, err := strconv.Atoi(e.Name())
        if err != nil {
            continue
        }
        dir := "/proc/" + e.Name() + "/fd"
        fds, err := os.ReadDir(dir)
        if err != nil {
            continue
        }
        for _, fd := range fds {
            l, err := os.Readlink(dir + "/" + fd.Name())
            if err != nil || !strings.HasPrefix(l, "socket:[") {
                continue
            }
            n, err := strconv.ParseUint(l[8:len(l)-1], 10, 64)
            if err != nil {
                continue
            }
            if p := m[n]; len(p) > 0 && p[len(p)-1] == pid {
                continue
            }
            m[n] = append(m[n], pid)
        }
    }
    return m
}

func procName(pid int) string {
    if exe, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid)); err == nil {
        exe = strings.TrimSuffix(exe, " (deleted)")
        if b := filepath.Base(exe); b != "" && b != "." && b != "/" {
            return b
        }
    }
    c, _ := os.ReadFile(fmt.Sprintf("/proc/%d/comm", pid))
    return strings.TrimSpace(string(c))
}

var cidRe = regexp.MustCompile(`[0-9a-f]{64}`)

func procContainer(pid int) string {
    b, err := os.ReadFile(fmt.Sprintf("/proc/%d/cgroup", pid))
    if err != nil {
        return ""
    }
    return cidRe.FindString(string(b))
}

func hostName(pids []int) string {
    for _, p := range pids {
        if p != 1 {
            if n := procName(p); n != "" {
                return n
            }
        }
    }
    if len(pids) > 0 {
        if n := procName(pids[0]); n != "" {
            return n
        }
    }
    return "unknown"
}

// ---------- interfaces ----------

type nic struct {
    name, group string
    ips         []net.IP
}

var groupRank = map[string]int{"lan": 0, "tailnet": 1, "loopback": 2, "other": 3, "docker": 4}

func defaultIface() string {
    b, err := os.ReadFile("/proc/net/route")
    if err != nil {
        return ""
    }
    for i, ln := range strings.Split(string(b), "\n") {
        f := strings.Fields(ln)
        if i == 0 || len(f) < 4 || f[1] != "00000000" {
            continue
        }
        if fl, err := strconv.ParseUint(f[3], 16, 32); err == nil && fl&2 != 0 {
            return f[0]
        }
    }
    return ""
}

func groupOf(name, def string) string {
    switch {
    case name == "lo":
        return "loopback"
    case strings.HasPrefix(name, "tailscale"):
        return "tailnet"
    case name == "docker0" || strings.HasPrefix(name, "br-") || strings.HasPrefix(name, "veth"):
        return "docker"
    case name == def:
        return "lan"
    }
    return "other"
}

func gatherNics() []nic {
    def := defaultIface()
    ifs, _ := net.Interfaces()
    var out []nic
    for _, in := range ifs {
        if in.Flags&net.FlagUp == 0 {
            continue
        }
        n := nic{name: in.Name, group: groupOf(in.Name, def)}
        addrs, _ := in.Addrs()
        for _, a := range addrs {
            if ipn, ok := a.(*net.IPNet); ok {
                n.ips = append(n.ips, ipn.IP)
            }
        }
        out = append(out, n)
    }
    return out
}

var cgnat = func() *net.IPNet { _, n, _ := net.ParseCIDR("100.64.0.0/10"); return n }()

func scopeOf(ip net.IP) string {
    switch {
    case ip.IsLoopback():
        return "loopback"
    case ip.IsLinkLocalUnicast():
        return "link-local"
    case cgnat.Contains(ip):
        return "tailnet"
    case ip.IsPrivate():
        return "private"
    }
    return "global"
}

func mkURL(scheme string, ip net.IP, port int) string {
    host := ip.String()
    if ip.To4() == nil {
        host = "[" + host + "]"
    }
    if (scheme == "http" && port == 80) || (scheme == "https" && port == 443) {
        return scheme + "://" + host + "/"
    }
    return scheme + "://" + host + ":" + strconv.Itoa(port) + "/"
}

// Which interface addresses can actually reach a socket bound to `bind`.
func reachFor(bind net.IP, port int, scheme string, ifs []nic) []Reach {
    wild := bind.IsUnspecified()
    v6 := bind.To4() == nil
    out := []Reach{}
    for _, n := range ifs {
        for _, ip := range n.ips {
            if (ip.To4() == nil) != v6 {
                continue
            }
            if wild && ip.IsLinkLocalUnicast() {
                continue
            }
            if !wild && !ip.Equal(bind) {
                continue
            }
            r := Reach{Iface: n.name, Group: n.group, IP: ip.String(), Scope: scopeOf(ip), Family: "v4"}
            if v6 {
                r.Family = "v6"
            }
            if scheme != "" && !ip.IsLinkLocalUnicast() {
                r.URL = mkURL(scheme, ip, port)
            }
            out = append(out, r)
        }
    }
    sort.Slice(out, func(i, j int) bool {
        a, b := out[i], out[j]
        if groupRank[a.Group] != groupRank[b.Group] {
            return groupRank[a.Group] < groupRank[b.Group]
        }
        if a.Iface != b.Iface {
            return a.Iface < b.Iface
        }
        return a.IP < b.IP
    })
    return out
}

// ---------- HTTP(S) detection (cached, asynchronous) ----------

type probeRes struct {
    kind string
    at   time.Time
}

var (
    probeMu    sync.Mutex
    probeCache = map[string]probeRes{}
    probing    = map[string]bool{}
)

func doProbe(ip string, port int) string {
    addr := net.JoinHostPort(ip, strconv.Itoa(port))
    c, err := net.DialTimeout("tcp", addr, time.Second)
    if err != nil {
        return "unreachable"
    }
    buf := make([]byte, 8)
    c.SetReadDeadline(time.Now().Add(250 * time.Millisecond))
    n, _ := c.Read(buf)
    c.Close()
    if n > 0 { // server-first protocol (ssh, smtp...) or plain HTTP error banner
        if bytes.HasPrefix(buf[:n], []byte("HTTP/")) {
            return "http"
        }
        return "other"
    }
    if c, err = net.DialTimeout("tcp", addr, time.Second); err == nil {
        c.SetDeadline(time.Now().Add(2 * time.Second))
        tc := tls.Client(c, &tls.Config{InsecureSkipVerify: true}) // identification only, nothing sensitive is sent
        if tc.Handshake() == nil {
            fmt.Fprint(tc, "HEAD / HTTP/1.0\r\n\r\n")
            _, err = io.ReadFull(tc, buf[:5])
            tc.Close()
            if err == nil && string(buf[:5]) == "HTTP/" {
                return "https"
            }
            return "other"
        }
        c.Close()
    }
    if c, err = net.DialTimeout("tcp", addr, time.Second); err == nil {
        c.SetDeadline(time.Now().Add(2 * time.Second))
        fmt.Fprint(c, "HEAD / HTTP/1.0\r\n\r\n")
        _, err = io.ReadFull(c, buf[:5])
        c.Close()
        if err == nil && string(buf[:5]) == "HTTP/" {
            return "http"
        }
    }
    return "other"
}

func schemeFor(sk sock) string {
    var ip string
    switch {
    case sk.ip.IsUnspecified():
        ip = "::1"
        if sk.ip.To4() != nil {
            ip = "127.0.0.1"
        }
    case sk.ip.IsLinkLocalUnicast():
        return ""
    default:
        ip = sk.ip.String()
    }
    key := fmt.Sprintf("%s|%d|%d", ip, sk.port, sk.inode)
    probeMu.Lock()
    r, ok := probeCache[key]
    ttl := 5 * time.Minute
    switch r.kind {
    case "http", "https":
        ttl = 6 * time.Hour
    case "unreachable":
        ttl = 30 * time.Second
    }
    if (!ok || time.Since(r.at) > ttl) && !probing[key] {
        probing[key] = true
        go func(port int) {
            k := doProbe(ip, port)
            probeMu.Lock()
            probeCache[key] = probeRes{k, time.Now()}
            delete(probing, key)
            probeMu.Unlock()
        }(sk.port)
    }
    probeMu.Unlock()
    if r.kind == "http" || r.kind == "https" {
        return r.kind
    }
    return ""
}

func pruneProbes() {
    probeMu.Lock()
    defer probeMu.Unlock()
    for k, r := range probeCache {
        if time.Since(r.at) > 12*time.Hour {
            delete(probeCache, k)
        }
    }
}

// ---------- Docker (read-only, via socket proxy) ----------

type dockerPort struct {
    IP          string `json:"IP"`
    PrivatePort int    `json:"PrivatePort"`
    PublicPort  int    `json:"PublicPort"`
    Type        string `json:"Type"`
}

type dockerContainer struct {
    ID         string            `json:"Id"`
    Names      []string          `json:"Names"`
    Image      string            `json:"Image"`
    State      string            `json:"State"`
    Status     string            `json:"Status"`
    Ports      []dockerPort      `json:"Ports"`
    Labels     map[string]string `json:"Labels"`
    HostConfig struct {
        NetworkMode string `json:"NetworkMode"`
    } `json:"HostConfig"`
}

func fetchContainers() ([]dockerContainer, error) {
    base := os.Getenv("DOCKER_HOST")
    if base == "" {
        base = "tcp://127.0.0.1:2375"
    }
    base = strings.Replace(base, "tcp://", "http://", 1)
    cl := &http.Client{Timeout: 4 * time.Second}
    resp, err := cl.Get(base + "/containers/json?all=1")
    if err != nil {
        return nil, fmt.Errorf("docker api unreachable: %v", err)
    }
    defer resp.Body.Close()
    if resp.StatusCode != 200 {
        return nil, fmt.Errorf("docker api: %s", resp.Status)
    }
    var cs []dockerContainer
    if err := json.NewDecoder(io.LimitReader(resp.Body, 16<<20)).Decode(&cs); err != nil {
        return nil, fmt.Errorf("docker api decode: %v", err)
    }
    return cs, nil
}

func healthOf(status string) string {
    switch {
    case strings.Contains(status, "(unhealthy)"):
        return "unhealthy"
    case strings.Contains(status, "(healthy)"):
        return "healthy"
    case strings.Contains(status, "health: starting"):
        return "starting"
    }
    return ""
}

// ---------- MagicDNS names (resolved at runtime, never stored) ----------

var (
    tsMu   sync.Mutex
    tsIP   string
    tsFQDN string
    tsAt   time.Time
    tsBusy bool
)

func tailnetNames(ifs []nic, hostname string) (string, string) {
    ip := ""
    for _, n := range ifs {
        if n.group != "tailnet" {
            continue
        }
        for _, a := range n.ips {
            if a.To4() != nil {
                ip = a.String()
            }
        }
    }
    if ip == "" {
        return "", ""
    }
    tsMu.Lock()
    defer tsMu.Unlock()
    ttl := 10 * time.Minute
    if tsFQDN == "" {
        ttl = 30 * time.Second
    }
    if (ip != tsIP || time.Since(tsAt) > ttl) && !tsBusy {
        tsBusy = true
        tsIP, tsAt = ip, time.Now()
        go func(ip string) {
            f := strings.TrimSuffix(os.Getenv("DASHBOARD_TAILNET_FQDN"), ".")
            if f == "" {
                names, _ := net.LookupAddr(ip)
                for _, n := range names {
                    n = strings.TrimSuffix(n, ".")
                    if strings.HasSuffix(n, ".ts.net") {
                        f = n
                        break
                    }
                }
            }
            tsMu.Lock()
            tsFQDN, tsBusy = f, false
            tsMu.Unlock()
        }(ip)
    }
    short := strings.ToLower(strings.SplitN(hostname, ".", 2)[0])
    if tsFQDN != "" {
        short = strings.SplitN(tsFQDN, ".", 2)[0]
    }
    return short, tsFQDN
}

func mkURLHost(scheme, host string, port int) string {
    if (scheme == "http" && port == 80) || (scheme == "https" && port == 443) {
        return scheme + "://" + host + "/"
    }
    return scheme + "://" + host + ":" + strconv.Itoa(port) + "/"
}

func aliasURLs(scheme string, port int, short, fqdn string) []Alias {
    var out []Alias
    if short != "" {
        out = append(out, Alias{"MagicDNS", mkURLHost(scheme, short, port)})
    }
    if fqdn != "" && fqdn != short {
        out = append(out, Alias{"MagicDNS full", mkURLHost(scheme, fqdn, port)})
    }
    return out
}

// ---------- snapshot assembly ----------

func collect(ifs []nic, binds []string) Snapshot {
    hn, _ := os.Hostname()
    snap := Snapshot{Host: hn, Binds: binds, Interfaces: []Iface{}}
    short, fqdn := tailnetNames(ifs, hn)

    for _, n := range ifs {
        if strings.HasPrefix(n.name, "veth") {
            continue
        }
        var as []string
        for _, ip := range n.ips {
            if !ip.IsLinkLocalUnicast() {
                as = append(as, ip.String())
            }
        }
        if len(as) > 0 {
            snap.Interfaces = append(snap.Interfaces, Iface{n.name, n.group, as})
        }
    }
    sort.Slice(snap.Interfaces, func(i, j int) bool {
        a, b := snap.Interfaces[i], snap.Interfaces[j]
        if groupRank[a.Group] != groupRank[b.Group] {
            return groupRank[a.Group] < groupRank[b.Group]
        }
        return a.Name < b.Name
    })

    cs, derr := fetchContainers()
    if derr != nil {
        snap.DockerError = derr.Error()
    }
    svcs := map[string]*Service{}
    byCID := map[string]*Service{}
    pub := map[string]string{}
    for _, c := range cs {
        name := c.ID[:12]
        if len(c.Names) > 0 {
            name = strings.TrimPrefix(c.Names[0], "/")
        }
        s := &Service{ID: "c:" + name, Name: name, Source: "docker", State: c.State,
            Health: healthOf(c.Status), StatusText: c.Status, Image: c.Image, NetMode: c.HostConfig.NetworkMode, Ports: []PortInfo{}}
        if p := c.Labels["com.docker.compose.project"]; p != "" {
            s.Compose = p + "/" + c.Labels["com.docker.compose.service"]
        }
        seen := map[string]bool{}
        for _, p := range c.Ports {
            if p.PublicPort == 0 {
                k := fmt.Sprintf("%d/%s", p.PrivatePort, p.Type)
                if !seen[k] {
                    seen[k] = true
                    s.Internal = append(s.Internal, k)
                }
                continue
            }
            if ip := net.ParseIP(p.IP); ip != nil {
                pub[p.Type+"|"+ip.String()+"|"+strconv.Itoa(p.PublicPort)] = c.ID
            }
        }
        svcs[s.ID] = s
        byCID[c.ID] = s
    }

    cidCache := map[int]string{}
    cidOf := func(pid int) string {
        if v, ok := cidCache[pid]; ok {
            return v
        }
        v := procContainer(pid)
        cidCache[pid] = v
        return v
    }

    owners := inodeOwners()
    for _, sk := range allSocks() {
        pids := owners[sk.inode]
        var svc *Service
        if cid, ok := pub[sk.proto+"|"+sk.ip.String()+"|"+strconv.Itoa(sk.port)]; ok {
            svc = byCID[cid]
        }
        if svc == nil {
            for _, pid := range pids {
                if cid := cidOf(pid); cid != "" && byCID[cid] != nil {
                    svc = byCID[cid]
                    break
                }
            }
        }
        if svc == nil {
            name := hostName(pids)
            id := "h:" + name
            if svcs[id] == nil {
                svcs[id] = &Service{ID: id, Name: name, Source: "host", State: "running", Ports: []PortInfo{}}
            }
            svc = svcs[id]
        }
        scheme := ""
        if sk.proto == "tcp" {
            scheme = schemeFor(sk)
        }
        pi := PortInfo{Proto: sk.proto, Bind: sk.ip.String(), Port: sk.port,
            Wildcard: sk.ip.IsUnspecified(), Scheme: scheme, Reach: reachFor(sk.ip, sk.port, scheme, ifs)}
        if scheme != "" {
            for _, r := range pi.Reach {
                if r.Group == "tailnet" {
                    pi.Aliases = aliasURLs(scheme, sk.port, short, fqdn)
                    break
                }
            }
        }
        svc.Ports = append(svc.Ports, pi)
    }
    pruneProbes()

    snap.Services = []Service{}
    for _, s := range svcs {
        sort.Slice(s.Ports, func(i, j int) bool {
            a, b := s.Ports[i], s.Ports[j]
            if a.Port != b.Port {
                return a.Port < b.Port
            }
            if a.Proto != b.Proto {
                return a.Proto < b.Proto
            }
            return a.Bind < b.Bind
        })
        sort.Strings(s.Internal)
        snap.Services = append(snap.Services, *s)
    }
    sort.Slice(snap.Services, func(i, j int) bool {
        a, b := snap.Services[i], snap.Services[j]
        if a.Source != b.Source {
            return a.Source == "docker"
        }
        return a.Name < b.Name
    })
    return snap
}

// ---------- the dashboard's own listeners ----------
// Bound only to IPv4 addresses of: the default-route interface (LAN), tailscale*, loopback.
// Never to IPv6, never to docker bridges. Re-evaluated every cycle, so DHCP changes and a late
// tailscale0 at boot are handled without configuration.

var (
    listeners = map[string]net.Listener{}
    failed    = map[string]bool{}
)

func syncBinds(srv *http.Server, port string, ifs []nic) []string {
    want := map[string]bool{}
    for _, n := range ifs {
        if n.group != "lan" && n.group != "tailnet" && n.group != "loopback" {
            continue
        }
        for _, ip := range n.ips {
            if ip.To4() != nil {
                want[ip.String()] = true
            }
        }
    }
    for addr, l := range listeners {
        if !want[addr] {
            l.Close()
            delete(listeners, addr)
            log.Printf("stopped listening on %s", addr)
        }
    }
    for addr := range want {
        if _, ok := listeners[addr]; ok {
            continue
        }
        l, err := net.Listen("tcp4", net.JoinHostPort(addr, port))
        if err != nil {
            if !failed[addr] {
                failed[addr] = true
                log.Printf("cannot listen on %s:%s: %v", addr, port, err)
            }
            continue
        }
        delete(failed, addr)
        listeners[addr] = l
        go srv.Serve(l)
        log.Printf("listening on %s", l.Addr())
    }
    var out []string
    for addr := range listeners {
        out = append(out, addr+":"+port)
    }
    sort.Strings(out)
    return out
}

// ---------- HTTP ----------

func secure(w http.ResponseWriter) {
    h := w.Header()
    h.Set("Content-Security-Policy", "default-src 'none'; connect-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    h.Set("X-Content-Type-Options", "nosniff")
    h.Set("Referrer-Policy", "no-referrer")
    h.Set("Cache-Control", "no-store")
}

func handler() http.Handler {
    mux := http.NewServeMux()
    get := func(f http.HandlerFunc) http.HandlerFunc {
        return func(w http.ResponseWriter, r *http.Request) {
            if r.Method != http.MethodGet && r.Method != http.MethodHead {
                http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
                return
            }
            secure(w)
            f(w, r)
        }
    }
    mux.HandleFunc("/", get(func(w http.ResponseWriter, r *http.Request) {
        if r.URL.Path != "/" {
            http.NotFound(w, r)
            return
        }
        w.Header().Set("Content-Type", "text/html; charset=utf-8")
        w.Write(indexHTML)
    }))
    mux.HandleFunc("/healthz", get(func(w http.ResponseWriter, r *http.Request) {
        w.Write([]byte("ok\n"))
    }))
    mux.HandleFunc("/api/snapshot", get(func(w http.ResponseWriter, r *http.Request) {
        mu.RLock()
        b := current
        mu.RUnlock()
        if b == nil {
            http.Error(w, "starting", http.StatusServiceUnavailable)
            return
        }
        w.Header().Set("Content-Type", "application/json")
        w.Write(b)
    }))
    mux.HandleFunc("/events", get(func(w http.ResponseWriter, r *http.Request) {
        fl, ok := w.(http.Flusher)
        if !ok {
            http.Error(w, "streaming unsupported", http.StatusInternalServerError)
            return
        }
        w.Header().Set("Content-Type", "text/event-stream")
        w.Header().Set("X-Accel-Buffering", "no")
        send := func(b []byte) {
            if b != nil {
                fmt.Fprintf(w, "data: %s\n\n", b)
                fl.Flush()
            }
        }
        mu.RLock()
        b, ch := current, changed
        mu.RUnlock()
        send(b)
        tick := time.NewTicker(20 * time.Second)
        defer tick.Stop()
        for {
            select {
            case <-r.Context().Done():
                return
            case <-ch:
                mu.RLock()
                b, ch = current, changed
                mu.RUnlock()
                send(b)
            case <-tick.C:
                fmt.Fprint(w, ": ping\n\n")
                fl.Flush()
            }
        }
    }))
    mux.HandleFunc("/icons/", func(w http.ResponseWriter, r *http.Request) {
        if r.Method != http.MethodGet && r.Method != http.MethodHead {
            http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
            return
        }
        name := strings.TrimPrefix(r.URL.Path, "/icons/")
        if !iconNameRe.MatchString(name) {
            http.NotFound(w, r)
            return
        }
        b, err := iconFS.ReadFile("icons/" + name)
        if err != nil {
            http.NotFound(w, r)
            return
        }
        hd := w.Header()
        hd.Set("Content-Type", "image/svg+xml")
        hd.Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
        hd.Set("X-Content-Type-Options", "nosniff")
        hd.Set("Cache-Control", "public, max-age=86400")
        w.Write(b)
    })
    return mux
}

func main() {
    port := os.Getenv("DASHBOARD_PORT")
    if port == "" {
        port = "80"
    }
    if len(os.Args) > 1 && os.Args[1] == "-healthcheck" {
        c := http.Client{Timeout: 3 * time.Second}
        r, err := c.Get("http://127.0.0.1:" + port + "/healthz")
        if err != nil || r.StatusCode != 200 {
            os.Exit(1)
        }
        os.Exit(0)
    }
    srv := &http.Server{Handler: handler(), ReadHeaderTimeout: 5 * time.Second}
    for {
        ifs := gatherNics()
        binds := syncBinds(srv, port, ifs)
        b, err := json.Marshal(collect(ifs, binds))
        if err == nil {
            publish(b)
        }
        time.Sleep(5 * time.Second)
    }
}
