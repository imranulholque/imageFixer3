package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Live updates (PC window and phones).
//
//   - netFast checks the adapters every 0.7 s and wakes the other loops the moment they change;
//   - notifyState tells every open admin window "something changed". The window does NOT ask for
//     the new state any more: the whole state (PC + devices) is pushed down the event stream, so
//     it appears at once, even when the window is hidden or its timers are slowed down by Windows;
//   - notifyPhones tells every connected phone page "something changed on the PC" (shared files);
//   - the 2 s refresh in the window stays only as a safety net.

var (
	stMu sync.Mutex
	stCh = make(chan struct{})

	phMu sync.Mutex
	phCh = make(chan struct{})

	kickNW = make(chan struct{}, 1) // wakes netWatch (the internet check)
)

// notifyState wakes every admin window that is waiting for news.
func notifyState() {
	stMu.Lock()
	close(stCh)
	stCh = make(chan struct{})
	stMu.Unlock()
}

func stateWait() <-chan struct{} {
	stMu.Lock()
	defer stMu.Unlock()
	return stCh
}

// notifyPhones wakes every phone page that is connected (the shared files may have changed).
func notifyPhones() {
	phMu.Lock()
	close(phCh)
	phCh = make(chan struct{})
	phMu.Unlock()
}

func phoneWait() <-chan struct{} {
	phMu.Lock()
	defer phMu.Unlock()
	return phCh
}

// netSig is a short fingerprint of the PC's real network addresses; it changes when Wi-Fi
// is joined or lost, or the address changes.
func netSig() string {
	var parts []string
	ifs, _ := net.Interfaces()
	for _, i := range ifs {
		if i.Flags&net.FlagUp == 0 || i.Flags&net.FlagLoopback != 0 || virtualAdapter(i.Name) {
			continue
		}
		addrs, _ := i.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok {
				if v4 := n.IP.To4(); v4 != nil && !v4.IsLinkLocalUnicast() && !v4.IsLoopback() {
					parts = append(parts, i.Name+"="+v4.String())
				}
			}
		}
	}
	sort.Strings(parts)
	return strings.Join(parts, ",") + "|" + strings.Join(localIPs(), ",")
}

// netFast notices a network change within a second.
func netFast() {
	last := netSig()
	for {
		time.Sleep(500 * time.Millisecond)
		s := netSig()
		if s == last {
			continue
		}
		last = s
		notifyState() // the QR address may already be different
		select {
		case kickNW <- struct{}{}:
		default:
		}
		kickHotspot()
		kickSmartNow() // tell the fixed link (Worker) about the new address right away
		go func() {
			// A network is back: drop the "turn on your hotspot" guide now, do not wait for the
			// next Wi-Fi scan (which can take several seconds).
			if hasNetwork() {
				hotReset()
			}
		}()
	}
}

// stateJSON is what the admin window shows, in one message: the PC state and the device lists.
func stateJSON() []byte {
	b, err := json.Marshal(map[string]any{"s": stateMap(), "d": devicesSnapshot()})
	if err != nil {
		return []byte("{}")
	}
	return b
}

// adminEvents is a Server-Sent-Events stream. Every time something changed it carries the new
// state; every few seconds it carries a small "ka" message so the window can tell the stream is alive.
func adminEvents(w http.ResponseWriter, r *http.Request) {
	fl, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", 500)
		return
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Accel-Buffering", "no")
	fmt.Fprint(w, "retry: 700\n\n")
	send := func() bool {
		if _, err := fmt.Fprintf(w, "data: %s\n\n", stateJSON()); err != nil {
			return false
		}
		fl.Flush()
		return true
	}
	ch := stateWait() // taken before the first snapshot, so a change right after it is never lost
	if !send() {
		return
	}
	tick := time.NewTicker(4 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ch:
			time.Sleep(30 * time.Millisecond) // a Wi-Fi switch fires many changes: send them as one message
			ch = stateWait()
			if !send() {
				return
			}
		case <-tick.C:
			if _, err := fmt.Fprint(w, "event: ka\ndata: 1\n\n"); err != nil {
				return
			}
			fl.Flush()
		case <-r.Context().Done():
			return
		}
	}
}
