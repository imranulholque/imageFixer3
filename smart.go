package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Smart connection:
//   - a fixed QR (Cloudflare Worker link) that never changes; the Worker sends each phone to
//     the PC's current LAN address (same network) or its current tunnel (anywhere else)
//   - the PC tells the Worker its current addresses (register loop) and keeps the internet tunnel up
//   - when the internet is gone: step-by-step guide, Offline mode, phone-hotspot auto connect

// Transfers in progress. The app never changes the PC's network (hotspot) or restarts the
// internet link while a file is being sent, so nothing is cut in half.
var (
	xferActive atomic.Int32
	xferLast   atomic.Int64 // unix nano of the last upload/download activity
)

func transferBusy() bool {
	if xferActive.Load() > 0 {
		return true
	}
	t := xferLast.Load()
	return t != 0 && time.Now().UnixNano()-t < int64(20*time.Second)
}

func trackXfer(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		xferActive.Add(1)
		xferLast.Store(time.Now().UnixNano())
		defer func() {
			xferActive.Add(-1)
			xferLast.Store(time.Now().UnixNano())
		}()
		next(w, r)
	}
}

var (
	onlineNow  atomic.Bool
	wasOffline atomic.Bool
	backAt     atomic.Int64 // unix nano: internet came back
	doneAt     atomic.Int64 // unix nano: a phone connected while offline

	hotMu    sync.Mutex
	hotLast  time.Time
	hotSel   string          // hotspot the person picked
	hotNets  []string        // hotspots shown to pick from
	hotBase  map[string]bool // Wi-Fi names already in range when the network was lost
	hotJoin  time.Time       // when we last tried to join (zero = not joining)
	hotOKAt  atomic.Int64    // unix nano: hotspot joined
	hotEpi   bool            // this PC was without any network (a "no network" episode is running)
	hotPanel bool            // the Wi-Fi list was already opened by itself in this episode
	hotSeen  map[string]int  // how many scans in a row each Wi-Fi name has been in range
	hotJoinNet string        // name of the Wi-Fi the PC joined (shown with the green tick)

	regMu   sync.Mutex
	regOK   bool
	regErr  string
	regLast time.Time
	regSig  string
)

func randID(n int) string {
	b := make([]byte, n/2)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// defaultWorker is baked into the build (-X main.defaultWorker=https://...) so users of the
// released app need no setup. A URL saved in Settings overrides it.
var defaultWorker = "https://shareup.imranul-hoque-sabbir.workers.dev"

func workerBase(c Config) string {
	u := strings.TrimRight(strings.TrimSpace(c.WorkerURL), "/")
	if u == "" {
		u = strings.TrimRight(strings.TrimSpace(defaultWorker), "/")
	}
	if !strings.HasPrefix(u, "https://") || strings.ContainsAny(u, " \t\r\n?#") {
		return ""
	}
	return u
}

func fixedURL(c Config) string {
	b := workerBase(c)
	if b == "" || c.PubID == "" {
		return ""
	}
	return b + "/d/" + c.PubID
}

func smartReset() {
	regMu.Lock()
	regSig, regOK, regErr = "", false, ""
	regLast = time.Time{}
	regMu.Unlock()
}

// stamp remembers "this just happened" for a message that the window shows for a few seconds,
// and wakes the window again when that time is over, so a message can never stay on screen
// longer than it should (the window used to learn about the end only at its next refresh).
func stamp(a *atomic.Int64, show time.Duration) {
	a.Store(time.Now().UnixNano())
	time.AfterFunc(show+150*time.Millisecond, notifyState)
}

var kickSmart = make(chan struct{}, 1)

// kickSmartNow makes smartLoop work now (new address -> tell the Worker, internet back -> tunnel).
func kickSmartNow() {
	select {
	case kickSmart <- struct{}{}:
	default:
	}
}

// ---------- network checks ----------

// internetUp tries two well-known addresses at the same time and says yes as soon as one answers.
func internetUp() bool {
	res := make(chan bool, 2)
	for _, a := range []string{"1.1.1.1:443", "8.8.8.8:443"} {
		go func(a string) {
			c, err := net.DialTimeout("tcp", a, 2*time.Second)
			if err == nil {
				c.Close()
			}
			res <- err == nil
		}(a)
	}
	for i := 0; i < 2; i++ {
		if <-res {
			return true
		}
	}
	return false
}

// routeIP is the local address the OS would use to reach the outside (the real network
// adapter, not WSL/VM adapters). "" when the PC is not on any network.
func routeIP() string {
	c, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil {
		return ""
	}
	defer c.Close()
	if a, ok := c.LocalAddr().(*net.UDPAddr); ok && a.IP != nil {
		return a.IP.String()
	}
	return ""
}

// virtualAdapter spots adapters that exist even when the PC is on no real network
// (WSL / Hyper-V / VM / VPN / Docker / Bluetooth ...). They must not count as "online".
func virtualAdapter(name string) bool {
	n := strings.ToLower(name)
	for _, p := range []string{"tun", "utun", "tap", "veth", "br-", "virbr", "vboxnet", "vmnet", "docker", "awdl", "llw", "ap1", "gif", "stf", "anpi"} {
		if strings.HasPrefix(n, p) {
			return true
		}
	}
	for _, p := range []string{"vethernet", "wsl", "virtualbox", "vmware", "hyper-v", "bluetooth", "loopback",
		"tailscale", "zerotier", "vpn", "npcap", "docker", "host-only"} {
		if strings.Contains(n, p) {
			return true
		}
	}
	return false
}

// hasNetwork is true only when a real (non-virtual) adapter is up and has an IPv4 address.
// A disconnected Wi-Fi is reported as down by the OS, so this turns false right away.
func hasNetwork() bool {
	ifs, _ := net.Interfaces()
	for _, i := range ifs {
		if i.Flags&net.FlagUp == 0 || i.Flags&net.FlagLoopback != 0 || virtualAdapter(i.Name) {
			continue
		}
		addrs, _ := i.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok {
				if v4 := n.IP.To4(); v4 != nil && !v4.IsLinkLocalUnicast() && !v4.IsLoopback() {
					return true
				}
			}
		}
	}
	return false
}

func lanURL() string {
	ip := pickIP(get(), localIPs())
	if ip == "" {
		return ""
	}
	return fmt.Sprintf("http://%s:%d", ip, curPort.Load())
}

func sessionCount() int {
	dmu.Lock()
	defer dmu.Unlock()
	return len(sessions)
}

func netWatch() {
	last := 0
	for {
		select {
		case <-time.After(3 * time.Second):
		case <-kickNW: // the network just changed: check right now
		}
		up := internetUp()
		prev := onlineNow.Swap(up)
		if prev != up {
			notifyState()
			kickHotspot()
			kickSmartNow() // internet back: bring the tunnel and the fixed link up at once
		}
		if !up {
			wasOffline.Store(true)
		} else if !prev && wasOffline.Load() {
			stamp(&backAt, 8*time.Second)
			kickUpdate() // look for a new version right away
		}
		n := sessionCount()
		if !up && n > 0 && last == 0 {
			stamp(&doneAt, 5*time.Second)
		}
		last = n
	}
}

// ---------- Worker registration + automatic tunnel + hotspot ----------

func smartLoop() {
	var tunTry time.Time
	for {
		select {
		case <-time.After(5 * time.Second):
		case <-kickSmart:
		}
		c := get()
		up := onlineNow.Load()
		if !up {
			continue
		}
		if c.AutoNet { // keep the internet link ready by itself
			st := remoteSnapshot().Status
			if (st == "off" || st == "error") && time.Since(tunTry) > 10*time.Second && !transferBusy() {
				tunTry = time.Now()
				remoteStart()
			}
		}
		base := workerBase(c)
		if base == "" {
			continue
		}
		tunnel := ""
		if rs := remoteSnapshot(); rs.Status == "on" {
			tunnel = rs.Link
		}
		lan := lanURL()
		if lan == "" && tunnel == "" {
			continue
		}
		sig := lan + "|" + tunnel
		regMu.Lock()
		need := sig != regSig || time.Since(regLast) > 15*time.Minute || (!regOK && time.Since(regLast) > 20*time.Second)
		regMu.Unlock()
		if need {
			registerWorker(c, base, lan, tunnel, sig)
		}
	}
}

func registerWorker(c Config, base, lan, tunnel, sig string) {
	lans := []string{}
	if lan != "" {
		lans = append(lans, lan)
	}
	body, _ := json.Marshal(map[string]any{"key": c.DevKey, "lan": lans, "tunnel": tunnel})
	cl := http.Client{Timeout: 10 * time.Second}
	resp, err := cl.Post(base+"/r/"+c.PubID, "application/json", bytes.NewReader(body))
	regMu.Lock()
	defer regMu.Unlock()
	defer notifyState() // the "ready / cannot reach" chip in the window follows at once
	regLast = time.Now()
	if err != nil {
		regOK, regErr = false, "Cannot reach Worker"
		return
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case 200:
		regOK, regErr, regSig = true, "", sig
	case 403:
		regOK, regErr = false, "This QR ID belongs to another key. Use Reset fixed QR."
	case 500:
		regOK, regErr = false, "Worker setup incomplete (KV binding SU missing?)"
	default:
		regOK, regErr = false, fmt.Sprintf("Worker answered %d", resp.StatusCode)
	}
}

// wifiAP is one Wi-Fi network in range.
type wifiAP struct {
	Name  string
	Sig   int  // signal strength in %, 0 when unknown
	Local bool // the radio address is "locally administered": what a phone hotspot uses, a router almost never does
}

func hotReset() {
	hotMu.Lock()
	was := hotSel != "" || hotEpi // the PC got a network again after a "no network" episode
	hotMu.Unlock()
	name := ""
	if was {
		name = wifiCurrent() // asked outside the lock: it runs a Windows command
	}
	hotMu.Lock()
	if was {
		hotJoinNet = name
		stamp(&hotOKAt, 10*time.Second) // show the green "connected" note, then take it away again
	}
	hotEpi, hotPanel = false, false
	hotSel, hotNets, hotBase, hotSeen = "", nil, nil, nil
	hotJoin = time.Time{}
	hotMu.Unlock()
	if was {
		notifyState()
	}
}

// maybeHotspot runs every few seconds. It does NOT scan, guess or join anything: when the PC has no
// network at all it only remembers "no network" (so the window shows the guide with its
// "Open Wi-Fi list" button), and when a network is back it tells the window "connected".
// The person picks the Wi-Fi / phone Hotspot by hand in the Windows Wi-Fi list.
func maybeHotspot(c Config, up bool) {
	if up || hasNetwork() {
		hotReset()
		return
	}
	hotMu.Lock()
	hotEpi = true
	hotMu.Unlock()
}

// adminWifi: the "Open Wi-Fi list" button of the hotspot guide.
func adminWifi(w http.ResponseWriter, r *http.Request) {
	go openWifiPanel()
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte("{}"))
}

// ---------- guide shown on the PC ----------

type guideInfo struct {
	Kind string   `json:"kind"` // "" | offline | back | done | joined
	Step int      `json:"step"` // offline: 1 guide, 4 joining a hotspot this PC already knows
	Nets []string `json:"nets,omitempty"`
	Sel  string   `json:"sel,omitempty"`
	Net  string   `json:"net,omitempty"` // joined: the Wi-Fi this PC is on now
}

func guide(c Config) guideInfo {
	now := time.Now().UnixNano()
	if t := hotOKAt.Load(); t != 0 && now-t < int64(10*time.Second) {
		hotMu.Lock()
		n := hotJoinNet
		hotMu.Unlock()
		return guideInfo{Kind: "joined", Net: n}
	}
	if onlineNow.Load() {
		if t := backAt.Load(); t != 0 && now-t < int64(8*time.Second) {
			return guideInfo{Kind: "back"}
		}
		return guideInfo{}
	}
	if t := doneAt.Load(); t != 0 && now-t < int64(5*time.Second) {
		return guideInfo{Kind: "done"}
	}
	if sessionCount() > 0 {
		return guideInfo{}
	}
	if hasNetwork() {
		return guideInfo{} // same Wi-Fi / hotspot: the normal QR just works
	}
	g := guideInfo{Kind: "offline", Step: 1}
	return g
}

func smartSnapshot(c Config) map[string]any {
	regMu.Lock()
	ok, er := regOK, regErr
	regMu.Unlock()
	return map[string]any{
		"configured": workerBase(c) != "",
		"fixedUrl":   fixedURL(c),
		"online":     onlineNow.Load(),
		"registered": ok,
		"regErr":     er,
		"offlineOn":  c.OfflineOn,
		"autoNet":    c.AutoNet,
		"workerUrl":  c.WorkerURL,
		"mdns":       fmt.Sprintf("shareup.local:%d", curPort.Load()),
		"tunnel":     remoteSnapshot().Status,
		"guide":      guide(c),
		"busy":       transferBusy(),
	}
}

var kickHot = make(chan struct{}, 1)

// kickHotspot makes hotLoop look at the network now instead of waiting for its timer.
func kickHotspot() {
	select {
	case kickHot <- struct{}{}:
	default:
	}
}

// hotLoop watches for "no network" and a phone hotspot. It runs on its own so that a slow
// Wi-Fi scan never delays the internet check or the worker registration. While the PC has
// no network it looks every 2 s, otherwise every 5 s (or at once when something changes).
func hotLoop() {
	for {
		maybeHotspot(get(), onlineNow.Load())
		wait := 5 * time.Second
		hotMu.Lock()
		if hotEpi {
			wait = 2 * time.Second
		}
		hotMu.Unlock()
		select {
		case <-time.After(wait):
		case <-kickHot:
		}
	}
}

func smartStart() {
	onlineNow.Store(internetUp())
	go netWatch()
	go netFast()
	go hotLoop()
	go smartLoop()
	go mdnsLoop()
}
