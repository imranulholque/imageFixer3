package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Device access control:
//   - every phone has a random device ID (kept in its browser) and gets a session cookie
//   - a new device needs approval on the PC (unless trusted), and/or a password
//   - sessions end automatically when the phone leaves (no heartbeat)
//
// Lock order: dmu may be held while taking mu (config). Never the other way round.

const (
	approvalWait = 60 * time.Second  // no answer in this time = Deny
	liveIdle     = 10 * time.Second  // a visible phone page beats every 2.5 s: silent this long = gone
	sessionIdle  = 120 * time.Second // a page in the background (or a transfer running) beats rarely
	byeGrace     = 4 * time.Second   // page closed: wait a little in case it is just a reload
	resumeWin    = 10 * time.Minute  // a phone that dropped out comes back without asking again
)

type TrustedDev struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Label    string `json:"label,omitempty"` // name the person gave on the PC (wins over Name)
	Added    int64  `json:"added"`
	LastSeen int64  `json:"lastSeen"`
}

type pending struct {
	Name    string
	IP      string
	Remote  bool
	Created time.Time // zero until the device is actually asking the PC (after password)
	Touched time.Time
	State   string // wait | allowed | denied
	Trust   bool
	PwOK    bool
}

type sess struct {
	DevID    string
	Name     string
	IP       string
	Remote   bool
	Created  time.Time
	LastSeen time.Time
	ByeAt    time.Time
	Hidden   bool          // the phone page is in the background: it beats rarely
	done     chan struct{} // closed the moment this session ends (the phone's event stream hears it)
	Why      string        // kick | idle | bye | replaced (written before done is closed)
	Ended    time.Time
}

type tryInfo struct {
	fails int
	until time.Time
}

var (
	dmu      sync.Mutex
	pend     = map[string]*pending{} // by device ID
	sessions = map[string]*sess{}    // by session token
	lapsed   = map[string]*sess{}    // sessions that just timed out (not kicked), by token: the same phone may resume
	tries    = map[string]*tryInfo{} // by IP

	idRe = regexp.MustCompile(`^[A-Za-z0-9]{16,64}$`)
)

// ---------- helpers ----------

func isRemote(r *http.Request) bool {
	return r.Header.Get("Cf-Connecting-Ip") != "" || r.Header.Get("X-Forwarded-For") != ""
}

func clientIP(r *http.Request) string {
	if v := r.Header.Get("Cf-Connecting-Ip"); v != "" {
		return v
	}
	h, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return h
}

var androidModelRe = regexp.MustCompile(`Android [0-9.]+; ([^;)]+)`)

// androidModel finds the phone model in the User-Agent (e.g. "SM-A546E", "Redmi Note 11").
// New Chrome versions hide it behind a plain "K"; then there is nothing to show.
func androidModel(ua string) string {
	m := androidModelRe.FindStringSubmatch(ua)
	if m == nil {
		return ""
	}
	v := strings.TrimSpace(m[1])
	if i := strings.Index(v, " Build"); i > 0 {
		v = v[:i]
	}
	if len(v) < 2 || strings.EqualFold(v, "wv") || strings.HasPrefix(strings.ToLower(v), "linux") {
		return ""
	}
	return v
}

func devName(ua string) string {
	os := "Device"
	l := strings.ToLower(ua)
	switch {
	case strings.Contains(l, "iphone"):
		os = "iPhone"
	case strings.Contains(l, "ipad"):
		os = "iPad"
	case strings.Contains(l, "android"):
		os = "Android"
		if m := androidModel(ua); m != "" {
			os = "Android " + m
		}
	case strings.Contains(l, "windows"):
		os = "Windows PC"
	case strings.Contains(l, "mac os"):
		os = "Mac"
	case strings.Contains(l, "linux"):
		os = "Linux"
	}
	br := ""
	switch {
	case strings.Contains(l, "edg/"):
		br = "Edge"
	case strings.Contains(l, "opr/") || strings.Contains(l, "opera"):
		br = "Opera"
	case strings.Contains(l, "samsungbrowser"):
		br = "Samsung Browser"
	case strings.Contains(l, "firefox") || strings.Contains(l, "fxios"):
		br = "Firefox"
	case strings.Contains(l, "chrome") || strings.Contains(l, "crios"):
		br = "Chrome"
	case strings.Contains(l, "safari"):
		br = "Safari"
	}
	if br == "" {
		return os
	}
	return os + " · " + br
}

func hashPwd(salt, pw string) string {
	h := sha256.Sum256([]byte(salt + pw))
	for i := 0; i < 50000; i++ {
		h = sha256.Sum256(append(h[:], salt...))
	}
	return hex.EncodeToString(h[:])
}

func setPassword(pw string) {
	b := make([]byte, 16)
	rand.Read(b)
	salt := hex.EncodeToString(b)
	mu.Lock()
	cfg.PwdSalt = salt
	cfg.PwdHash = hashPwd(salt, pw)
	mu.Unlock()
}

func checkPwd(c Config, pw string) bool {
	if c.PwdHash == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(hashPwd(c.PwdSalt, pw)), []byte(c.PwdHash)) == 1
}

func isTrusted(c Config, id string) bool {
	for _, t := range c.Trusted {
		if t.ID == id {
			return true
		}
	}
	return false
}

func touchTrusted(id, name string) {
	mu.Lock()
	for i := range cfg.Trusted {
		if cfg.Trusted[i].ID == id {
			cfg.Trusted[i].LastSeen = time.Now().Unix()
			if name != "" {
				cfg.Trusted[i].Name = name
			}
		}
	}
	saveLocked()
	mu.Unlock()
	notifyState()
}

func addTrusted(id, name string) {
	mu.Lock()
	defer mu.Unlock()
	defer notifyState()
	for i := range cfg.Trusted {
		if cfg.Trusted[i].ID == id {
			cfg.Trusted[i].LastSeen = time.Now().Unix()
			saveLocked()
			return
		}
	}
	now := time.Now().Unix()
	cfg.Trusted = append(append([]TrustedDev{}, cfg.Trusted...), TrustedDev{ID: id, Name: name, Added: now, LastSeen: now})
	saveLocked()
}

func removeTrusted(id string) {
	mu.Lock()
	defer mu.Unlock()
	defer notifyState()
	out := make([]TrustedDev, 0, len(cfg.Trusted))
	for _, t := range cfg.Trusted {
		if t.ID != id {
			out = append(out, t)
		}
	}
	cfg.Trusted = out
	saveLocked()
}

// dropSess ends one session: it leaves the list, the phone's event stream is told at once
// (so a kicked phone shows "disconnected" in an instant), and the PC window refreshes.
// A session that only timed out is remembered for a while so the same phone can resume.
// dmu must be held.
func dropSess(tok string, s *sess, why string) {
	if _, ok := sessions[tok]; !ok {
		return
	}
	delete(sessions, tok)
	s.Why, s.Ended = why, time.Now()
	close(s.done)
	if why == "idle" || why == "bye" {
		lapsed[tok] = s
	}
	notifyState()
}

// dmu must be held.
func dropSessionsOf(id, why string) {
	for tok, s := range sessions {
		if s.DevID == id {
			dropSess(tok, s, why)
		}
	}
}

// dmu must be held.
func grant(w http.ResponseWriter, r *http.Request, id, name, ip string, remote bool) {
	dropSessionsOf(id, "replaced")
	tok := newToken() + newToken()
	now := time.Now()
	sessions[tok] = &sess{DevID: id, Name: name, IP: ip, Remote: remote, Created: now, LastSeen: now, done: make(chan struct{})}
	http.SetCookie(w, &http.Cookie{Name: "su_s", Value: tok, Path: "/", HttpOnly: true,
		SameSite: http.SameSiteLaxMode, Secure: r.Header.Get("X-Forwarded-Proto") == "https"})
	notifyState() // the new phone shows in the PC window right now
}

func sessionFrom(r *http.Request) *sess {
	ck, err := r.Cookie("su_s")
	if err != nil {
		return nil
	}
	return sessions[ck.Value]
}

// gate: only devices that were let in can use the phone routes.
func gate(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dmu.Lock()
		s := sessionFrom(r)
		if s != nil {
			s.LastSeen = time.Now()
			s.ByeAt = time.Time{}
		}
		dmu.Unlock()
		if s == nil {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		h(w, r)
	}
}

// ---------- phone-facing endpoints ----------

// POST /api/session {id, pw?}  ->  {status: ok|pending|password|denied|locked, ...}
func apiSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", 405)
		return
	}
	var q struct {
		ID string `json:"id"`
		Pw string `json:"pw"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&q) != nil || !idRe.MatchString(q.ID) {
		http.Error(w, "bad request", 400)
		return
	}
	c := get()
	ip := clientIP(r)
	remote := isRemote(r)
	name := devName(r.UserAgent())
	trusted := isTrusted(c, q.ID)
	now := time.Now()

	dmu.Lock()
	defer dmu.Unlock()

	// already in
	if s := sessionFrom(r); s != nil && s.DevID == q.ID {
		s.LastSeen = now
		s.ByeAt = time.Time{}
		writeJSON(w, map[string]any{"status": "ok"})
		return
	}

	// The same phone is back soon after its session timed out (the page was in the background, or the
	// Wi-Fi blinked): it was already let in, so let it straight back in.
	if ck, err := r.Cookie("su_s"); err == nil {
		if old := lapsed[ck.Value]; old != nil && old.DevID == q.ID && now.Sub(old.Ended) < resumeWin {
			delete(lapsed, ck.Value)
			delete(pend, q.ID)
			grant(w, r, q.ID, name, ip, remote)
			writeJSON(w, map[string]any{"status": "ok"})
			return
		}
	}

	p := pend[q.ID]
	if p == nil {
		p = &pending{State: "wait"}
		pend[q.ID] = p
	}
	p.Name, p.IP, p.Remote, p.Touched = name, ip, remote, now

	// 1) password (even trusted devices must give it while password protection is on)
	if c.PwdOn && !p.PwOK {
		t := tries[ip]
		if t != nil && now.Before(t.until) {
			writeJSON(w, map[string]any{"status": "locked", "wait": int(t.until.Sub(now).Seconds()) + 1})
			return
		}
		if q.Pw == "" {
			writeJSON(w, map[string]any{"status": "password"})
			return
		}
		if !checkPwd(c, q.Pw) {
			if t == nil {
				t = &tryInfo{}
				tries[ip] = t
			}
			t.fails++
			if t.fails >= 5 {
				t.fails = 0
				t.until = now.Add(60 * time.Second)
				writeJSON(w, map[string]any{"status": "locked", "wait": 60})
				return
			}
			writeJSON(w, map[string]any{"status": "password", "bad": true})
			return
		}
		delete(tries, ip)
		p.PwOK = true
	}

	// 2) approval (trusted devices skip it)
	if !c.ApprovalOn || trusted {
		delete(pend, q.ID)
		grant(w, r, q.ID, name, ip, remote)
		if trusted {
			go touchTrusted(q.ID, name)
		}
		writeJSON(w, map[string]any{"status": "ok"})
		return
	}

	switch p.State {
	case "allowed":
		trust := p.Trust
		delete(pend, q.ID)
		grant(w, r, q.ID, name, ip, remote)
		if trust {
			addTrusted(q.ID, name)
		}
		writeJSON(w, map[string]any{"status": "ok"})
		return
	case "denied":
		delete(pend, q.ID)
		writeJSON(w, map[string]any{"status": "denied"})
		return
	}
	if p.Created.IsZero() {
		p.Created = now
		notifyState() // the approval window opens on the PC right now
		go uiShow()   // bring the shareUp window to the front on the PC
	}
	left := int(approvalWait.Seconds() - now.Sub(p.Created).Seconds())
	if left < 0 {
		left = 0
	}
	writeJSON(w, map[string]any{"status": "pending", "left": left})
}

// POST /api/ping  (heartbeat)
func apiPing(w http.ResponseWriter, r *http.Request) {
	hidden := r.URL.Query().Get("h") == "1" // the page is in the background: it will beat rarely
	dmu.Lock()
	s := sessionFrom(r)
	if s != nil {
		s.LastSeen = time.Now()
		s.ByeAt = time.Time{}
		s.Hidden = hidden
	}
	dmu.Unlock()
	if s == nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// POST /api/bye  (page closed; sent with sendBeacon)
func apiBye(w http.ResponseWriter, r *http.Request) {
	dmu.Lock()
	if s := sessionFrom(r); s != nil {
		s.ByeAt = time.Now()
	}
	dmu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

// ---------- phone event stream ----------

// GET /api/events  (phone). The PC talks first, so the phone does not have to ask:
//   event: bye   -> this phone was disconnected on the PC (data: kick | idle | bye | replaced)
//   event: sync  -> something changed on the PC (shared files): refresh the lists
//   event: ka    -> still alive
func apiEvents(w http.ResponseWriter, r *http.Request) {
	fl, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", 500)
		return
	}
	dmu.Lock()
	s := sessionFrom(r)
	var done chan struct{}
	if s != nil {
		s.LastSeen = time.Now()
		s.ByeAt = time.Time{}
		done = s.done
	}
	dmu.Unlock()
	if s == nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Accel-Buffering", "no")
	fmt.Fprint(w, "retry: 1000\n\nevent: hello\ndata: 1\n\n")
	fl.Flush()
	ch := phoneWait()
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-done:
			fmt.Fprintf(w, "event: bye\ndata: %s\n\n", s.Why) // Why was written before done was closed
			fl.Flush()
			return
		case <-ch:
			ch = phoneWait()
			if _, err := fmt.Fprint(w, "event: sync\ndata: 1\n\n"); err != nil {
				return
			}
		case <-tick.C:
			if _, err := fmt.Fprint(w, "event: ka\ndata: 1\n\n"); err != nil {
				return
			}
		case <-r.Context().Done():
			return
		}
		fl.Flush()
	}
}

// ---------- janitor ----------

func devJanitor() {
	for {
		time.Sleep(time.Second)
		now := time.Now()
		changed := false
		dmu.Lock()
		for id, p := range pend {
			switch {
			case p.State == "wait" && !p.Created.IsZero() && now.Sub(p.Created) > approvalWait:
				p.State = "denied" // timed out; the phone will see "denied" on its next check
				p.Touched = now
				changed = true
			case now.Sub(p.Touched) > 5*time.Minute:
				delete(pend, id)
			}
		}
		busy := transferBusy() // a big file keeps the line full: its heartbeats may be late
		for tok, s := range sessions {
			limit := liveIdle
			if s.Hidden || busy {
				limit = sessionIdle
			}
			if now.Sub(s.LastSeen) > limit {
				dropSess(tok, s, "idle")
			} else if !s.ByeAt.IsZero() && now.Sub(s.ByeAt) > byeGrace {
				dropSess(tok, s, "bye")
			}
		}
		for tok, o := range lapsed {
			if now.Sub(o.Ended) > resumeWin {
				delete(lapsed, tok)
			}
		}
		for ip, t := range tries {
			if now.After(t.until) && t.fails == 0 {
				delete(tries, ip)
			}
		}
		dmu.Unlock()
		if changed {
			notifyState()
		}
	}
}

// ---------- admin endpoints (PC only) ----------

type devPending struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	IP     string `json:"ip"`
	Remote bool   `json:"remote"`
	Left   int    `json:"left"`
}
type devSession struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	IP      string `json:"ip"`
	Remote  bool   `json:"remote"`
	Since   int64  `json:"since"`
	Trusted bool   `json:"trusted"`
}
type devTrusted struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Added    int64  `json:"added"`
	LastSeen int64  `json:"lastSeen"`
	Online   bool   `json:"online"`
}

// shownName is the name the person gave a trusted device, else the guessed one.
func shownName(c Config, id, def string) string {
	for _, t := range c.Trusted {
		if t.ID == id && t.Label != "" {
			return t.Label
		}
	}
	return def
}

func devicesSnapshot() map[string]any {
	c := get()
	now := time.Now()
	dmu.Lock()
	defer dmu.Unlock()
	pl := []devPending{}
	for id, p := range pend {
		if p.State == "wait" && !p.Created.IsZero() {
			left := int(approvalWait.Seconds() - now.Sub(p.Created).Seconds())
			if left < 0 {
				left = 0
			}
			pl = append(pl, devPending{id, shownName(c, id, p.Name), p.IP, p.Remote, left})
		}
	}
	sort.Slice(pl, func(i, j int) bool { return pl[i].ID < pl[j].ID })
	sl := []devSession{}
	online := map[string]bool{}
	for _, s := range sessions {
		online[s.DevID] = true
		sl = append(sl, devSession{s.DevID, shownName(c, s.DevID, s.Name), s.IP, s.Remote, s.Created.Unix(), isTrusted(c, s.DevID)})
	}
	sort.Slice(sl, func(i, j int) bool { return sl[i].Since < sl[j].Since })
	tl := []devTrusted{}
	for _, t := range c.Trusted {
		tl = append(tl, devTrusted{t.ID, shownName(c, t.ID, t.Name), t.Added, t.LastSeen, online[t.ID]})
	}
	return map[string]any{"pending": pl, "sessions": sl, "trusted": tl}
}

func adminDevices(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, devicesSnapshot())
}

// POST /admin/dev {op: allow|deny|kick|trust|untrust, id, trust?}
func adminDev(w http.ResponseWriter, r *http.Request) {
	var q struct {
		Op    string `json:"op"`
		ID    string `json:"id"`
		Trust bool   `json:"trust"`
		Name  string `json:"name"`
	}
	if json.NewDecoder(r.Body).Decode(&q) != nil || !idRe.MatchString(q.ID) {
		http.Error(w, "bad request", 400)
		return
	}
	dmu.Lock()
	switch q.Op {
	case "allow":
		if p := pend[q.ID]; p != nil && p.State == "wait" {
			p.State, p.Trust = "allowed", q.Trust
			p.Touched = time.Now()
		}
	case "deny":
		if p := pend[q.ID]; p != nil && p.State == "wait" {
			p.State = "denied"
			p.Touched = time.Now()
		}
	case "kick":
		dropSessionsOf(q.ID, "kick")
		for tok, o := range lapsed { // a phone that was disconnected on purpose must not slip back in quietly
			if o.DevID == q.ID {
				delete(lapsed, tok)
			}
		}
	case "trust":
		name := "Device"
		for _, s := range sessions {
			if s.DevID == q.ID {
				name = s.Name
			}
		}
		addTrusted(q.ID, name)
	case "untrust":
		removeTrusted(q.ID)
	case "rename":
		nm := strings.TrimSpace(q.Name)
		if r := []rune(nm); len(r) > 30 {
			nm = string(r[:30])
		}
		mu.Lock()
		for i := range cfg.Trusted {
			if cfg.Trusted[i].ID == q.ID {
				cfg.Trusted[i].Label = nm
			}
		}
		saveLocked()
		mu.Unlock()
		if nm != "" {
			for _, s := range sessions { // a phone that is not trusted yet: the name lasts while it is connected
				if s.DevID == q.ID {
					s.Name = nm
				}
			}
		}
	default:
		dmu.Unlock()
		http.Error(w, "bad op", 400)
		return
	}
	dmu.Unlock()
	notifyState()
	writeJSON(w, devicesSnapshot())
}
