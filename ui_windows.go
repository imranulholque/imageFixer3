//go:build windows

// Copyright (c) 2026 Imranul Hoque Sabbir. All rights reserved.

package main

import (
	_ "embed"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"fyne.io/systray"
	"github.com/jchv/go-webview2"
)

//go:embed icon.ico
var trayIcon []byte

var (
	user32         = syscall.NewLazyDLL("user32.dll")
	pShowWindow    = user32.NewProc("ShowWindow")
	pSetForeground = user32.NewProc("SetForegroundWindow")
	pIsIconic      = user32.NewProc("IsIconic")
	pSysParams     = user32.NewProc("SystemParametersInfoW")
	winMu          sync.Mutex
	winHwnd        uintptr // 0 while the window is closed (app lives in the tray)
	showCh         = make(chan struct{}, 1)
	updItem        *systray.MenuItem
)

const (
	swRestore = 9
	swShow    = 5
)

func init() {
	// A window that is minimized, covered by other windows or left alone for a few minutes used to be
	// slowed down by the browser engine (timers once a second, later once a minute, animation frames
	// stopped). The window then showed old things, such as "turn on your hotspot", for a long time after
	// the PC had already joined it. These switches keep the window fully awake. Must be set before the
	// first window is created.
	if os.Getenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") == "" {
		os.Setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
			"--disable-background-timer-throttling --disable-renderer-backgrounding "+
				"--disable-backgrounding-occluded-windows "+
				"--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling")
	}
}

// uiShow opens the window, or brings it to the front if it is already open.
func uiShow() {
	kickUpdate()
	winMu.Lock()
	h := winHwnd
	winMu.Unlock()
	if h != 0 {
		if r, _, _ := pIsIconic.Call(h); r != 0 {
			pShowWindow.Call(h, swRestore)
		} else {
			pShowWindow.Call(h, swShow)
		}
		pSetForeground.Call(h)
		return
	}
	select {
	case showCh <- struct{}{}:
	default:
	}
}

// uiQuit takes the tray icon away. Exiting the process without this leaves a dead icon in
// the tray until the mouse moves over it (and a second one appears after an update).
func uiQuit() {
	systray.Quit()
	time.Sleep(350 * time.Millisecond) // let Windows really remove the icon
}

func windowOpen() bool {
	winMu.Lock()
	defer winMu.Unlock()
	return winHwnd != 0
}

// windowLoop owns one OS thread (WebView2 needs that). Closing the window only
// destroys the view; the app keeps running in the tray until "Quit".
func windowLoop() {
	runtime.LockOSThread()
	for range showCh {
		openWindow()
	}
}

// windowSize picks a window that fits this screen: up to 1100x820, otherwise 92% x 88% of
// the usable screen area (the part not covered by the taskbar).
func windowSize() (int, int) {
	var rc struct{ l, t, r, b int32 }
	w, h := 1100, 820
	if ok, _, _ := pSysParams.Call(0x0030, 0, uintptr(unsafe.Pointer(&rc)), 0); ok != 0 {
		sw, sh := int(rc.r-rc.l), int(rc.b-rc.t)
		if sw > 0 && sh > 0 {
			if v := sw * 92 / 100; v < w {
				w = v
			}
			if v := sh * 88 / 100; v < h {
				h = v
			}
		}
	}
	if w < 420 {
		w = 420
	}
	if h < 520 {
		h = 520
	}
	return w, h
}

func openWindow() {
	ww, wh := windowSize()
	data := filepath.Join(os.Getenv("LOCALAPPDATA"), "shareUp", "webview")
	create := func() webview2.WebView {
		return webview2.NewWithOptions(webview2.WebViewOptions{
			AutoFocus: true,
			DataPath:  data,
			WindowOptions: webview2.WindowOptions{
				Title:  "shareUp",
				Width:  uint(ww),
				Height: uint(wh),
				IconId: 2, // the icon embedded in the exe by the build
				Center: true,
			},
		})
	}
	w := create()
	// Right after an update the old copy's WebView2 processes can still hold the data folder for a
	// moment, so creating the view may fail: try again before giving up on the native window.
	for i := 0; w == nil && i < 8; i++ {
		time.Sleep(600 * time.Millisecond)
		w = create()
	}
	if w == nil { // WebView2 runtime missing: fall back to the browser
		openBrowser(adminURL())
		return
	}
	defer w.Destroy()
	w.SetSize(420, 520, webview2.HintMin) // resizable, with a sensible minimum
	winMu.Lock()
	winHwnd = uintptr(w.Window())
	winMu.Unlock()
	w.Navigate(adminURL())
	// A blank white window (seen right after an update) means the page never loaded. The page asks for
	// /admin/state as soon as it starts, so if that has not happened, load it again.
	t0 := time.Now().UnixNano()
	go func() {
		for i := 0; i < 8; i++ {
			time.Sleep(2 * time.Second)
			if lastAdminHit.Load() > t0 || !windowOpen() {
				return
			}
			w.Dispatch(func() { w.Navigate(adminURL()) })
		}
	}()
	w.Run()
	winMu.Lock()
	winHwnd = 0
	winMu.Unlock()
}

func runUI(silent bool) {
	go windowLoop()
	if !silent {
		uiShow()
	}
	uiUpdateHook = func(tag string) {
		if updItem != nil {
			updItem.SetTitle("Update available: " + tag)
			updItem.Show()
		}
		systray.SetTooltip("shareUp – update available (" + tag + ")")
	}
	systray.Run(func() {
		systray.SetIcon(trayIcon)
		systray.SetTitle("shareUp")
		systray.SetTooltip("shareUp")
		systray.SetOnTapped(uiShow) // left-click the tray icon opens the window
		open := systray.AddMenuItem("Open shareUp", "Show the window")
		updItem = systray.AddMenuItem("", "Open shareUp to install the update")
		updItem.Hide()
		systray.AddSeparator()
		quit := systray.AddMenuItem("Quit shareUp", "Stop shareUp")
		go func() {
			for {
				select {
				case <-open.ClickedCh:
					uiShow()
				case <-updItem.ClickedCh:
					uiShow()
				case <-quit.ClickedCh:
					quitApp()
				}
			}
		}()
		if u := updateSnapshot(); u.Available {
			uiUpdateHook(u.Latest)
		}
	}, nil)
}
