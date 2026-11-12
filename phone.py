"""Phone as a wireless scanner: a tiny web server on the local network.
The phone only needs a browser. Only the pages of this program are served - no file of the computer is reachable."""
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, Signal

import scanner

MAX_BODY = 60 * 1024 * 1024
EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "GIF": ".gif", "TIFF": ".tif",
       "MPO": ".jpg", "HEIF": ".heic"}


def local_ip():
    """The address of this computer on the Wi-Fi / LAN (None if there is no network)."""
    cands = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        cands.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        cands += socket.gethostbyname_ex(socket.gethostname())[2]
    except Exception:
        pass
    for ip in cands:
        try:
            a = ipaddress.ip_address(ip)
            if a.is_private and not a.is_loopback and not a.is_link_local:
                return ip
        except ValueError:
            pass
    return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "ImageFixer"
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True                      # small replies leave at once (no 40 ms wait)
    wbufsize = 64 * 1024                                # headers + body go out in one piece

    def log_message(self, *a):
        pass

    # ---- helpers
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _allowed(self):
        """(route, query) if the client is on the local network and the token is right, else None."""
        try:
            a = ipaddress.ip_address(self.client_address[0].split("%")[0])
            if not (a.is_private or a.is_loopback):
                self._send(403, "Only the local network can use this.")
                return None
        except ValueError:
            self._send(403, "")
            return None
        u = urlparse(self.path)
        m = re.match(r"^/s/([A-Za-z0-9_\-]+)(/.*)?$", u.path)
        if m:                                           # internet link (secret path)
            if not hmac.compare_digest(m.group(1), self.server.token):
                self._send(404, "This link is not valid any more.")
                return None
            route = m.group(2) or "/"
        elif a.is_loopback:                             # the tunnel arrives from this computer: secret path required
            self._send(404, "This link is not valid.")
            return None
        else:                                           # a device on the same Wi-Fi: plain address, no secret
            route = u.path or "/"
        self.server.owner.touch()
        return route, parse_qs(u.query)

    def _body(self, may_be_empty=False):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY or (n <= 0 and not may_be_empty):
            self._json({"ok": False, "error": "File too big or empty"}, 413)
            return None
        return self.rfile.read(n)

    # ---- routes
    def do_GET(self):
        r = self._allowed()
        if r is None:
            return
        route, _ = r
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/api/ping":
            self._json({"ok": True})
        else:
            self._send(404, "")

    def do_POST(self):
        r = self._allowed()
        if r is None:
            return
        route, q = r
        data = self._body(may_be_empty=route in ("/api/detect", "/api/warp"))    # these may refer to a photo already sent (?id=)
        if data is None:
            return
        try:
            if route == "/api/detect":
                self._detect(data, q)
            elif route == "/api/warp":
                self._warp(data, q)
            elif route == "/api/upload":
                self._upload(data, q)
            else:
                self._send(404, "")
        except Exception as ex:
            self._json({"ok": False, "error": str(ex)[:200]}, 400)

    # ---- fast scan path: the photo is sent ONCE, then every detect / warp / look change only sends a few numbers
    def _source(self, q, data):
        """(id, entry) of the photo: from the cache (?id=) or decoded from the body (and cached)."""
        cache = self.server.owner.cache
        sid = q.get("id", [""])[0]
        ent = cache.get(sid) if sid else None
        if ent is None and data:
            im = Image.open(io.BytesIO(data))
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")
            sid, ent = cache.put(im)
        return sid, ent

    def _detect(self, data, q):
        sid, ent = self._source(q, data)
        if ent is None:
            self._json({"ok": False, "gone": True, "error": "send the photo again"}, 410)
            return
        im = ent.view(int(q.get("sr", ["0"])[0]))
        quad, found = scanner.detect_document(im)
        self._json({"ok": True, "id": sid, "w": im.width, "h": im.height, "found": found,
                    "quad": [[x, y] for x, y in quad]})

    def _warp(self, data, q):
        sid, ent = self._source(q, data)
        if ent is None:
            self._json({"ok": False, "gone": True, "error": "send the photo again"}, 410)
            return
        vals = [float(v) for v in q.get("q", [""])[0].split(",")]
        if len(vals) != 8:
            raise ValueError("4 corners needed")
        look = q.get("look", ["orig"])[0]
        sr = int(q.get("sr", ["0"])[0]) % 4
        key = (sr, tuple(round(v, 1) for v in vals))
        jpg = ent.look_jpeg(key, look, lambda: scanner.straighten(
            ent.view(sr), [(vals[i], vals[i + 1]) for i in range(0, 8, 2)]), scanner.enhance)
        self._send(200, jpg, "image/jpeg")

    def _upload(self, data, q):
        try:
            im = Image.open(io.BytesIO(data))
            fmt = im.format
            im.verify()
        except Exception:
            self._json({"ok": False, "error": "Not a picture this program can open"}, 400)
            return
        batch = re.sub(r"[^A-Za-z0-9]", "", q.get("batch", ["x"])[0])[:24] or "x"
        i = max(0, int(q.get("i", ["0"])[0]))
        n = max(1, min(500, int(q.get("n", ["1"])[0])))
        kind = q.get("kind", ["file"])[0]
        name = os.path.basename(q.get("name", ["picture"])[0]) or "picture"
        stem = re.sub(r"[^\w\-. ]", "_", os.path.splitext(name)[0])[:60] or "picture"
        ext = EXT.get(fmt, ".jpg")
        self._json({"ok": True, **self.server.owner.store(batch, i, n, kind, stem, ext, data)})


class _Entry:
    """One photo from the phone, decoded once. The straightened page and every 'look' of it are kept, so turning
    between Original / Clean / Gray / B&W (or coming back to one) costs nothing the second time."""

    def __init__(self, im):
        self.im = im
        self.t = time.time()
        self.lock = threading.RLock()
        self._views = {0: im}
        self._warp_key = None
        self._warp = None
        self._looks = {}

    def view(self, turns):
        turns %= 4
        with self.lock:
            v = self._views.get(turns)
            if v is None:
                v = self.im.transpose({1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180,
                                       3: Image.Transpose.ROTATE_90}[turns])        # clockwise quarter turns
                self._views[turns] = v
            return v

    def look_jpeg(self, key, look, make_warp, enhance):
        with self.lock:                                   # one page at a time per photo (the same work is never done twice)
            self.t = time.time()
            if self._warp_key != key:
                self._warp_key, self._warp, self._looks = key, make_warp(), {}
            jpg = self._looks.get(look)
            if jpg is None:
                out = enhance(self._warp, look)
                buf = io.BytesIO()
                out.save(buf, "JPEG", quality=92, subsampling=0)
                jpg = self._looks[look] = buf.getvalue()
            return jpg


class _Cache:
    MAX = 6                                               # photos kept in memory

    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, sid):
        with self._lock:
            e = self._d.get(sid)
            if e is not None:
                e.t = time.time()
            return e

    def put(self, im):
        sid = secrets.token_hex(6)
        e = _Entry(im)
        with self._lock:
            self._d[sid] = e
            while len(self._d) > self.MAX:
                del self._d[min(self._d, key=lambda k: self._d[k].t)]
        return sid, e


class Tunnel:
    """Optional 'Internet' link: asks the free localhost.run service (through the OpenSSH program that comes with
    Windows 10 / 11) to give this computer a public https address. Nothing is installed.
    Only this program's phone page is reachable, and only with the secret code in the link."""
    URL = re.compile(r"https://[A-Za-z0-9.-]+\.(?:lhr\.life|localhost\.run)")

    def __init__(self, port):
        self.port = port
        self.url = None
        self.error = None
        self.proc = None
        self.started = time.time()
        self._start()

    def _start(self):
        import subprocess
        import sys
        cmd = ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
               "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
               "-R", "80:127.0.0.1:%d" % self.port, "nokey@localhost.run"]
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags)
        except FileNotFoundError:
            self.error = "The 'ssh' program was not found. Install the Windows optional feature 'OpenSSH Client'."
            return
        except Exception as ex:
            self.error = str(ex)
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace")
                m = self.URL.search(line)
                if m and self.url is None:
                    self.url = m.group(0)
        except Exception:
            pass
        if self.url is None and self.error is None:
            self.error = "Could not open the internet link (no internet, or the service is busy)."
        elif self.url is not None:
            self.url = None
            self.error = "The internet link was closed."

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def timed_out(self):
        return self.url is None and self.error is None and time.time() - self.started > 30

    def stop(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        self.proc = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class PhoneServer(QObject):
    received = Signal(list)          # file paths, in the order the phone sent them
    activity = Signal()

    def __init__(self, inbox, parent=None):
        super().__init__(parent)
        self.inbox = inbox
        os.makedirs(inbox, exist_ok=True)
        self.token = secrets.token_urlsafe(16)
        self.port = 0
        self.httpd = None
        self.last_seen = 0.0
        self.total = 0
        self._batches = {}
        self._lock = threading.Lock()
        self.cache = _Cache()

    # ---- control
    def start(self, preferred_port=0):
        if self.httpd is not None:
            return True
        for port in ([preferred_port] if preferred_port else []) + [0]:
            try:
                self.httpd = _Server(("0.0.0.0", port), _Handler)
                break
            except OSError:
                self.httpd = None
        if self.httpd is None:
            return False
        self.httpd.token = self.token
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None

    def running(self):
        return self.httpd is not None

    def url(self):
        ip = local_ip()
        return None if ip is None or not self.running() else "http://%s:%d" % (ip, self.port)

    def link_for(self, base):
        """Same secret link behind another address (the internet tunnel)."""
        return base.rstrip("/") + "/s/%s/" % self.token

    def phone_online(self):
        return time.time() - self.last_seen < 12.0

    # ---- called from the server threads
    def touch(self):
        self.last_seen = time.time()
        self.activity.emit()

    def store(self, batch, i, n, kind, stem, ext, data):
        with self._lock:
            b = self._batches.setdefault(batch, {"n": n, "files": {}, "t": time.time()})
            b["n"] = n
            fname = ("Scan_%s_%02d" % (time.strftime("%Y%m%d_%H%M%S"), i + 1)) if kind == "scan" else stem
            os.makedirs(self.inbox, exist_ok=True)
            path = os.path.join(self.inbox, fname + ext)
            n = 2
            while os.path.exists(path):
                path = os.path.join(self.inbox, "%s (%d)%s" % (fname, n, ext))
                n += 1
            with open(path, "wb") as f:
                f.write(data)
            b["files"][i] = path
            done = len(b["files"]) >= b["n"]
            if done:
                paths = [b["files"][k] for k in sorted(b["files"])]
                del self._batches[batch]
            for k in [k for k, v in self._batches.items() if time.time() - v["t"] > 3600]:
                del self._batches[k]
        if done:
            self.total += len(paths)
            self.received.emit(paths)
        return {"stored": len(b["files"]), "done": done}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#0b0e14">
<title>Image Fixer</title>
<style>
:root{--bg:#0b0e14;--card:#151a24;--card2:#1c2230;--line:#273046;--txt:#eef0f4;--mut:#8b93a7;--acc:#6366f1;--acc2:#a855f7;--ok:#30a46c;--bad:#e5484d;--r:18px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;-webkit-touch-callout:none}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;overscroll-behavior:none}
body{display:flex;flex-direction:column;height:100vh;height:100dvh;overflow:hidden;touch-action:manipulation;user-select:none;-webkit-user-select:none}
button{font:inherit;color:inherit;border:0;background:none;padding:0;cursor:pointer;touch-action:manipulation}
header{flex:none;display:flex;align-items:center;gap:12px;padding:calc(10px + env(safe-area-inset-top)) 16px 10px;background:var(--bg);border-bottom:1px solid #161b27}
.logo{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--acc),var(--acc2));display:grid;place-items:center;font-weight:800;font-size:14px}
header .t{flex:1;min-width:0}header b{font-size:16px;display:block;line-height:1.15}header small{color:var(--mut);font-size:12px}
#dot{width:10px;height:10px;border-radius:50%;background:var(--bad);box-shadow:0 0 0 4px #e5484d22;transition:background .2s}#dot.on{background:var(--ok);box-shadow:0 0 0 4px #30a46c22}
#bar{position:fixed;left:0;right:0;top:0;height:3px;z-index:50;opacity:0;transition:opacity .15s}
#bar.on{opacity:1}#bar i{display:block;height:100%;width:40%;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:0 3px 3px 0;animation:slide 1s ease-in-out infinite}
@keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(260%)}}
/* screens: only one is shown, switching is instant (no layout work) */
.scr{flex:1;min-height:0;display:none;flex-direction:column}.scr.on{display:flex;animation:in .14s ease-out}
@keyframes in{from{opacity:.0;transform:translateY(6px)}to{opacity:1;transform:none}}
.body{flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:14px 16px;display:flex;flex-direction:column;gap:12px;max-width:760px;width:100%;margin:0 auto}
.dock{flex:none;padding:10px 16px calc(10px + env(safe-area-inset-bottom));background:linear-gradient(0deg,var(--bg) 70%,#0b0e1400);display:flex;flex-direction:column;gap:10px;max-width:760px;width:100%;margin:0 auto}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--line);border-radius:15px;padding:0 16px;background:var(--card2);font-size:16px;font-weight:550;min-height:52px;transition:transform .06s,opacity .1s}
.btn:active{transform:scale(.97);opacity:.85}
.btn.pri{background:linear-gradient(90deg,var(--acc),var(--acc2));border:0;color:#fff;font-weight:700;min-height:56px;font-size:17px}
.btn.red{background:#33191f;border-color:#59262f;color:#ffb4bd}
.btn:disabled{opacity:.45;pointer-events:none}
.row{display:flex;gap:10px}.row>.btn{flex:1;min-width:0}
.hero{display:flex;align-items:center;gap:16px;padding:22px 20px;border-radius:24px;background:linear-gradient(125deg,var(--acc),var(--acc2));text-align:left;width:100%;box-shadow:0 10px 30px -12px #6366f1aa}
.hero:active{transform:scale(.98)}
.hero .ic{flex:none;width:60px;height:60px;border-radius:18px;background:#ffffff2e;display:grid;place-items:center}
.hero b{display:block;font-size:21px}.hero small{display:block;margin-top:3px;opacity:.9;font-size:14px;line-height:1.3}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.tile{display:flex;flex-direction:column;gap:10px;align-items:flex-start;text-align:left;padding:16px;border-radius:var(--r);background:var(--card);border:1px solid var(--line);min-height:112px}
.tile:active{transform:scale(.97)}
.tile .ic{width:42px;height:42px;border-radius:13px;background:var(--card2);display:grid;place-items:center}
.tile b{font-size:16px}.tile small{color:var(--mut);font-size:12.5px;line-height:1.3;display:block;margin-top:2px}
svg{width:26px;height:26px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}
.btn svg{width:20px;height:20px}
.sent{display:flex;flex-direction:column;gap:8px}.sent div{display:flex;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 14px;font-size:14px;color:var(--mut)}.sent b{color:var(--ok)}
.hint{color:var(--mut);font-size:13.5px;line-height:1.35;text-align:center;padding:0 4px}
.chips{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;padding:2px 0}.chips::-webkit-scrollbar{display:none}
.chip{flex:none;display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:0 16px;font-size:14.5px;min-height:44px;white-space:nowrap;transition:background .1s}
.chip:active{transform:scale(.96)}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
.chip svg{width:18px;height:18px}
/* corner editor / review: the picture takes all the free space */
.stage{flex:1;min-height:0;position:relative;display:grid;place-items:center;padding:4px 12px;max-width:760px;width:100%;margin:0 auto}
.stage canvas{max-width:100%;max-height:100%;touch-action:none;border-radius:12px;background:#000;display:block}
.stage .wait{position:absolute;inset:4px 12px;border-radius:12px;display:none;place-items:center;background:#0b0e1488;font-weight:600}
.stage .wait.on{display:grid}
.spin{width:30px;height:30px;border-radius:50%;border:3px solid #ffffff33;border-top-color:#fff;animation:sp .7s linear infinite;margin:0 auto 8px}
@keyframes sp{to{transform:rotate(360deg)}}
.pad{padding:8px 16px 0;max-width:760px;width:100%;margin:0 auto}
.seg{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:4px;background:var(--card);border:1px solid var(--line);border-radius:15px}
.seg button{min-height:42px;border-radius:11px;font-size:14px;color:var(--mut);font-weight:550}
.seg button.on{background:var(--acc);color:#fff}
.pages{display:flex;flex-direction:column;gap:10px}
.pg{display:flex;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:10px}
.pg .th{position:relative;flex:none}
.pg img{width:66px;height:88px;object-fit:cover;border-radius:10px;background:#000;display:block}
.pg .th i{position:absolute;left:-6px;top:-6px;min-width:22px;height:22px;border-radius:11px;background:var(--acc);font-style:normal;font-size:12px;font-weight:700;display:grid;place-items:center}
.pg .n{flex:1;min-width:0;font-weight:600}.pg .n small{display:block;color:var(--mut);font-weight:400;font-size:13px}
.pg .ab{display:grid;grid-template-columns:repeat(2,44px);gap:6px}
.pg .ab button{width:44px;height:40px;border-radius:12px;background:var(--card2);border:1px solid var(--line);display:grid;place-items:center}
.pg .ab button:active{transform:scale(.92)}
.pg .ab button.red{background:#33191f;border-color:#59262f;color:#ffb4bd}
.pg .ab svg{width:18px;height:18px}
.sw{display:flex;align-items:center;justify-content:space-between;gap:10px;color:var(--mut);font-size:14px;padding:4px 2px}
.tg{position:relative;width:46px;height:28px;border-radius:14px;background:#2a3144;flex:none;transition:background .15s}
.tg::after{content:"";position:absolute;left:3px;top:3px;width:22px;height:22px;border-radius:50%;background:#fff;transition:transform .15s}
.tg.on{background:var(--ok)}.tg.on::after{transform:translateX(18px)}
#viewer{position:fixed;inset:0;background:#000f;display:none;align-items:center;justify-content:center;padding:12px;z-index:40}
#viewer.on{display:flex}#viewer img{max-width:100%;max-height:100%;border-radius:8px}
#toast{position:fixed;left:16px;right:16px;bottom:calc(86px + env(safe-area-inset-bottom));z-index:45;background:#232a3b;border:1px solid var(--line);padding:12px 14px;border-radius:14px;font-size:14px;text-align:center;display:none;box-shadow:0 8px 30px #0008}
#toast.on{display:block}#toast.err{background:#3a1b22;border-color:#5a2630;color:#ffb4bd}
#banner{position:fixed;left:12px;right:12px;top:calc(62px + env(safe-area-inset-top));z-index:44;background:#3a1b22;border:1px solid #5a2630;color:#ffb4bd;padding:10px 14px;border-radius:14px;font-size:13.5px;text-align:center;display:none}
#banner.on{display:block}
.hide{display:none!important}
</style></head><body>
<div id="bar"><i></i></div>
<header><div class="logo">IF</div><div class="t"><b>Image Fixer</b><small id="st">connecting...</small></div><span id="dot"></span></header>
<div id="banner">Not connected to the computer. Scan the QR code on the computer again.</div>

<section class="scr on" id="home">
 <div class="body">
  <button class="hero" id="bScan"><span class="ic"><svg viewBox="0 0 24 24" style="width:30px;height:30px"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/></svg></span><span><b>Scan a paper</b><small>Take a photo. The page is found, cut out and straightened for you.</small></span></button>
  <div class="tiles">
   <button class="tile" id="bGal"><span class="ic"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/></svg></span><span><b>Scan a photo</b><small>Pick a saved photo and cut the page out.</small></span></button>
   <button class="tile" id="bChoose"><span class="ic"><svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg></span><span><b>Send pictures</b><small>Send photos as they are.</small></span></button>
  </div>
  <div class="sent" id="sent"></div>
 </div>
</section>

<section class="scr" id="edit">
 <div class="pad"><div class="chips"><button class="chip" id="eAuto"><svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/></svg>Find paper</button><button class="chip" id="eFull">Whole photo</button><button class="chip" id="eRotL" aria-label="Turn left"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/></svg></button><button class="chip" id="eRotR" aria-label="Turn right"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg></button></div></div>
 <div class="stage"><canvas id="cv"></canvas><div class="wait" id="eWait"><div><div class="spin"></div>Finding the paper</div></div></div>
 <div class="hint" id="tip" style="padding:4px 16px"></div>
 <div class="dock"><div class="row"><button class="btn" id="eRetake">Retake</button><button class="btn pri" id="eOk" style="flex:2">Cut out</button></div></div>
</section>

<section class="scr" id="review">
 <div class="stage"><canvas id="rcv"></canvas><div class="wait" id="rWait"><div><div class="spin"></div>Cleaning the page</div></div></div>
 <div class="pad"><div class="seg" id="looks"><button data-l="orig">Original</button><button data-l="clean" class="on">Clean</button><button data-l="gray">Gray</button><button data-l="bw">B&amp;W</button></div></div>
 <div class="dock"><div class="row"><button class="btn" id="rRotL"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/></svg>Turn</button><button class="btn" id="rRotR">Turn<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg></button><button class="btn" id="rAdj">Corners</button></div><button class="btn pri" id="rUse">Use this page</button></div>
</section>

<section class="scr" id="list">
 <div class="body">
  <div class="row" style="align-items:center"><b id="lt" style="flex:1;font-size:20px"></b><button class="btn" id="lHome" style="flex:none;min-height:42px;padding:0 16px;font-size:14.5px">Home</button></div>
  <div class="pages" id="pages"></div>
  <button class="btn" id="lAdd" style="border-style:dashed;min-height:60px"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>Add another page</button>
  <div class="sw" id="autoRow"><span>Open the camera again after each page</span><span class="tg" id="auto"></span></div>
 </div>
 <div class="dock"><button class="btn pri" id="lSend">Send to computer</button></div>
</section>

<div id="toast"></div>
<input type="file" id="fFiles" accept="image/*" multiple class="hide">
<input type="file" id="fCam" accept="image/*" capture="environment" class="hide">
<input type="file" id="fGal" accept="image/*" class="hide">
<div id="viewer"><img id="vimg"></div>
<script>
const $=id=>document.getElementById(id), base=location.pathname.replace(/\/$/,'');
let pages=[], retake=-1, cur=null, busy=false, sentLog=[], tt=0, seq=0, lastOk=Date.now(), active=0;
const DPR=()=>Math.min(window.devicePixelRatio||1,2.5);
const LOOKNAME={orig:'Original',clean:'Clean',gray:'Gray',bw:'Black & white'};
const api=async(p,opt)=>{active++;try{const r=await fetch(base+p,opt);lastOk=Date.now();return r;}finally{active--;prog(active>0);}};
function show(id){for(const s of ['home','edit','review','list'])$(s).classList.toggle('on',s===id);}
function toast(t,err,ms){clearTimeout(tt);const e=$('toast');if(!t){e.classList.remove('on');return;}e.textContent=t;e.className='on'+(err?' err':'');tt=setTimeout(()=>e.classList.remove('on'),ms||(err?5000:2200));}
function prog(on){$('bar').classList.toggle('on',!!on);}
function buzz(){try{navigator.vibrate&&navigator.vibrate(15);}catch(e){}}
const raf=()=>new Promise(r=>requestAnimationFrame(()=>r()));
// ---- connection light (pings only when nothing else talked to the computer lately)
let fails=0;
async function ping(){if(document.hidden||active||Date.now()-lastOk<4000)return;
 try{const r=await fetch(base+'/api/ping');if(!r.ok)throw 0;lastOk=Date.now();fails=0;$('dot').className='on';$('st').textContent='connected to your computer';$('banner').classList.remove('on');}
 catch(e){if(++fails>=3){$('dot').className='';$('st').textContent='not connected';$('banner').classList.add('on');}}}
setInterval(ping,4000);ping();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)ping();});
function ok(){fails=0;$('dot').className='on';$('st').textContent='connected to your computer';$('banner').classList.remove('on');}

// ---- images: decode once, rotate on the GPU canvas (no JPEG encode / decode, no computer needed)
function loadImg(src){return new Promise((res,no)=>{const i=new Image();i.onload=()=>res(i);i.onerror=no;i.src=src;});}
const toBlob=(c,q)=>new Promise(r=>c.toBlob(r,'image/jpeg',q||0.92));
async function decode(blobOrFile){
 try{return await createImageBitmap(blobOrFile,{imageOrientation:'from-image'});}catch(e){}
 const u=URL.createObjectURL(blobOrFile);try{return await loadImg(u);}finally{setTimeout(()=>URL.revokeObjectURL(u),3000);}}
const bw=b=>b.width||b.naturalWidth, bh=b=>b.height||b.naturalHeight;
async function shrink(file,maxSide){ // photo -> {blob (upright JPEG), bmp}
 const im=await decode(file),w=bw(im),h=bh(im),s=Math.min(1,maxSide/Math.max(w,h));
 const c=document.createElement('canvas');c.width=Math.round(w*s);c.height=Math.round(h*s);
 const g=c.getContext('2d');g.imageSmoothingQuality='high';g.drawImage(im,0,0,c.width,c.height);if(im.close)im.close();
 return {blob:await toBlob(c,0.9),bmp:c};}
function turned(src,t){ // clockwise quarter turns of a bitmap/canvas, returns a canvas
 t=((t%4)+4)%4;const w=bw(src),h=bh(src),c=document.createElement('canvas');c.width=t%2?h:w;c.height=t%2?w:h;
 const g=c.getContext('2d');g.translate(c.width/2,c.height/2);g.rotate(t*Math.PI/2);g.drawImage(src,-w/2,-h/2);return c;}
function rotQuad(q,w,h,cw){return q.map(p=>cw?{x:h-p.y,y:p.x}:{x:p.y,y:w-p.x});}
function thumb(src,side){const w=bw(src),h=bh(src),s=side/Math.max(w,h),c=document.createElement('canvas');c.width=Math.max(1,Math.round(w*s));c.height=Math.max(1,Math.round(h*s));
 c.getContext('2d').drawImage(src,0,0,c.width,c.height);return c.toDataURL('image/jpeg',0.7);}
function fit(el,w,h,box){ // size a canvas to the free space of its stage, keeping the shape
 const r=box.getBoundingClientRect(),aw=r.width-24,ah=r.height-8,k=Math.min(aw/w,ah/h),d=DPR();
 const cw=Math.max(40,Math.floor(w*k)),ch=Math.max(40,Math.floor(h*k));el.style.width=cw+'px';el.style.height=ch+'px';
 el.width=Math.round(cw*d);el.height=Math.round(ch*d);}

// ---------- send pictures as they are
$('bChoose').onclick=()=>{$('fFiles').value='';$('fFiles').click();};
$('fFiles').onchange=async e=>{
 const fs=[...e.target.files];if(!fs.length)return;
 const batch=Date.now().toString(36)+Math.random().toString(36).slice(2,6);let n=0;
 for(let i=0;i<fs.length;i++){toast('Sending '+(i+1)+' of '+fs.length+' ...',false,60000);
  try{const r=await api('/api/upload?kind=file&batch='+batch+'&i='+i+'&n='+fs.length+'&name='+encodeURIComponent(fs[i].name),{method:'POST',body:fs[i]});
   const j=await r.json();if(j.ok)n++;else toast(fs[i].name+': '+j.error,true);}
  catch(err){toast('Connection lost. Scan the QR code on the computer again.',true);return;}}
 buzz();toast('');ok();logSent(n+' picture'+(n==1?'':'s'));};
function logSent(t){const d=new Date();sentLog.unshift('<b>&#10003;</b> '+t+' sent &middot; '+d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
 sentLog=sentLog.slice(0,4);$('sent').innerHTML=sentLog.map(x=>'<div>'+x+'</div>').join('');}

// ---------- scan
function openCam(){$('fCam').value='';$('fCam').click();}
$('bScan').onclick=()=>{retake=-1;openCam();};
$('bGal').onclick=()=>{retake=-1;$('fGal').value='';$('fGal').click();};
$('fCam').onchange=e=>gotPhoto(e.target.files[0]);$('fGal').onchange=e=>gotPhoto(e.target.files[0]);
// the photo goes to the computer ONCE (it keeps it, tagged with an id); after that only a few numbers travel
async function detect(){
 const url='/api/detect?sr='+cur.sr+(cur.id?'&id='+cur.id:'');
 let r=await api(url,{method:'POST',body:cur.id?undefined:cur.blob});
 if(r.status===410){cur.id=null;r=await api('/api/detect?sr='+cur.sr,{method:'POST',body:cur.blob});}
 const j=await r.json();if(!j.ok)throw new Error(j.error);cur.id=j.id;ok();return j;}
async function gotPhoto(f){if(!f)return;
 const my=++seq;show('edit');$('eWait').classList.add('on');$('tip').textContent='';
 try{const s=await shrink(f,3000);cur={blob:s.blob,base:s.bmp,sr:0,view:s.bmp,w:bw(s.bmp),h:bh(s.bmp),quad:null,id:null,look:'clean',rot:0,res:null};
  await raf();layout();                                   // the photo is on screen while the computer looks for the paper
  cur.quad=[[.04,.04],[.96,.04],[.96,.96],[.04,.96]].map(p=>({x:p[0]*cur.w,y:p[1]*cur.h}));draw();
  const j=await detect();if(my!==seq)return;
  cur.quad=j.quad.map(p=>({x:p[0],y:p[1]}));setTip(j.found);$('eWait').classList.remove('on');draw();
 }catch(err){$('eWait').classList.remove('on');toast('Could not reach the computer. Scan the QR code again.',true);if(cur&&cur.view)setTip(false);}}
function setTip(found){$('tip').textContent=found?'Paper found. Drag a round corner or a square side if the outline is not exact.':'Paper not found. Drag the corners onto the paper, or tap "Whole photo".';}
$('eAuto').onclick=async()=>{$('eWait').classList.add('on');try{const j=await detect();cur.quad=j.quad.map(p=>({x:p[0],y:p[1]}));setTip(j.found);draw();}catch(e){toast('Failed. Try again.',true);}$('eWait').classList.remove('on');};
$('eFull').onclick=()=>{const m=0.01,w=cur.w,h=cur.h;cur.quad=[{x:w*m,y:h*m},{x:w*(1-m),y:h*m},{x:w*(1-m),y:h*(1-m)},{x:w*m,y:h*(1-m)}];draw();};
function turnSrc(cw){cur.quad=rotQuad(cur.quad,cur.w,cur.h,cw);cur.sr=(cur.sr+(cw?1:3))%4;cur.view=turned(cur.base,cur.sr);cur.w=bw(cur.view);cur.h=bh(cur.view);layout();draw();buzz();}
$('eRotL').onclick=()=>turnSrc(false);$('eRotR').onclick=()=>turnSrc(true);

const cv=$('cv'),cx=cv.getContext('2d');let drag=null,scale=1;
function layout(){fit(cv,cur.w,cur.h,cv.parentNode);scale=cv.width/cur.w;}
function startEdit(){show('edit');$('eWait').classList.remove('on');layout();draw();}
function mids(q){return q.map((p,i)=>({x:(p.x+q[(i+1)%4].x)/2,y:(p.y+q[(i+1)%4].y)/2}));}
let drawQ=0;
function draw(){if(drawQ)return;drawQ=requestAnimationFrame(()=>{drawQ=0;paint();});}
function paint(){if(!cur||!cur.view||!cur.quad)return;const d=DPR();cx.clearRect(0,0,cv.width,cv.height);cx.drawImage(cur.view,0,0,cv.width,cv.height);
 const q=cur.quad.map(p=>({x:p.x*scale,y:p.y*scale})),m=mids(q);
 cx.save();cx.beginPath();cx.rect(0,0,cv.width,cv.height);cx.moveTo(q[0].x,q[0].y);for(let i=3;i>=0;i--)cx.lineTo(q[i].x,q[i].y);cx.closePath();cx.fillStyle='rgba(0,0,0,.5)';cx.fill('evenodd');cx.restore();
 cx.beginPath();q.forEach((p,i)=>i?cx.lineTo(p.x,p.y):cx.moveTo(p.x,p.y));cx.closePath();cx.lineWidth=2.4*d;cx.strokeStyle='#a5b4fc';cx.stroke();
 m.forEach(p=>{const s=9*d;cx.fillStyle='rgba(255,255,255,.92)';cx.strokeStyle='#4f46e5';cx.lineWidth=2.5*d;cx.beginPath();cx.rect(p.x-s,p.y-s,2*s,2*s);cx.fill();cx.stroke();});
 q.forEach(p=>{cx.beginPath();cx.arc(p.x,p.y,13*d,0,7);cx.fillStyle='rgba(255,255,255,.95)';cx.fill();cx.lineWidth=3*d;cx.strokeStyle='#4f46e5';cx.stroke();});
 if(drag){const p=drag.kind==='c'?q[drag.i]:m[drag.i],M=Math.min(64*d,cv.width*0.22),z=3,mx=Math.min(Math.max(p.x,M),cv.width-M),my=p.y>M*2.6?p.y-M*1.7:p.y+M*1.7;
  cx.save();cx.beginPath();cx.arc(mx,my,M,0,7);cx.clip();cx.fillStyle='#000';cx.fillRect(mx-M,my-M,2*M,2*M);
  const sw=2*M/z/scale;cx.drawImage(cur.view,p.x/scale-sw/2,p.y/scale-sw/2,sw,sw,mx-M,my-M,2*M,2*M);cx.restore();
  cx.beginPath();cx.arc(mx,my,M,0,7);cx.lineWidth=3*d;cx.strokeStyle='#fff';cx.stroke();
  cx.beginPath();cx.moveTo(mx-10*d,my);cx.lineTo(mx+10*d,my);cx.moveTo(mx,my-10*d);cx.lineTo(mx,my+10*d);cx.strokeStyle='#f43f5e';cx.lineWidth=2*d;cx.stroke();}}
function pos(e){const r=cv.getBoundingClientRect(),k=cv.width/r.width;return{x:(e.clientX-r.left)*k,y:(e.clientY-r.top)*k};}
cv.onpointerdown=e=>{if(!cur.quad)return;const p=pos(e),d=DPR(),q=cur.quad.map(a=>({x:a.x*scale,y:a.y*scale})),m=mids(q);let best=null,bd=1e9;
 q.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y)-4*d;if(t<bd){bd=t;best={kind:'c',i};}});
 m.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y);if(t<bd){bd=t;best={kind:'m',i};}});
 if(best&&bd<64*d){drag=best;drag.start=cur.quad.map(a=>({x:a.x,y:a.y}));drag.p0=p;cv.setPointerCapture(e.pointerId);buzz();draw();}};
cv.onpointermove=e=>{if(!drag)return;const p=pos(e),dx=(p.x-drag.p0.x)/scale,dy=(p.y-drag.p0.y)/scale,S=drag.start;
 const cl=(v,m)=>Math.max(0,Math.min(m,v));
 if(drag.kind==='c'){cur.quad[drag.i]={x:cl(S[drag.i].x+dx,cur.w),y:cl(S[drag.i].y+dy,cur.h)};}
 else{const a=S[drag.i],b=S[(drag.i+1)%4],ux=b.x-a.x,uy=b.y-a.y,L=Math.hypot(ux,uy)||1,nx=-uy/L,ny=ux/L,t=dx*nx+dy*ny;
  [drag.i,(drag.i+1)%4].forEach(k=>{cur.quad[k]={x:cl(S[k].x+nx*t,cur.w),y:cl(S[k].y+ny*t,cur.h)};});}
 draw();};
cv.onpointerup=cv.onpointercancel=()=>{drag=null;draw();};
addEventListener('resize',()=>{if($('edit').classList.contains('on')&&cur&&cur.view){layout();draw();}else if($('review').classList.contains('on')&&cur&&cur.res)paintReview();});
$('eRetake').onclick=()=>openCam();

// ---------- cut out + review: the page appears at once (shown as photographed), the cleaning follows and swaps in
const rcv=$('rcv'),rx=rcv.getContext('2d');
function warpUrl(look){const q=cur.quad.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)).join(',');return '/api/warp?id='+cur.id+'&sr='+cur.sr+'&q='+q+'&look='+look;}
async function fetchLook(look){
 let r=await api(warpUrl(look),{method:'POST'});
 if(r.status===410){await detect();r=await api(warpUrl(look),{method:'POST'});}   // the computer forgot the photo: it is sent again, once
 if(!r.ok)throw new Error('warp');ok();const b=await r.blob();return {blob:b,bmp:await decode(b)};}
function paintReview(){if(!cur||!cur.res)return;const src=cur.res.bmp,t=cur.rot,w=t%2?bh(src):bw(src),h=t%2?bw(src):bh(src);
 fit(rcv,w,h,rcv.parentNode);rx.imageSmoothingQuality='high';rx.save();rx.translate(rcv.width/2,rcv.height/2);rx.rotate(t*Math.PI/2);
 const dw=(t%2?rcv.height:rcv.width),dh=(t%2?rcv.width:rcv.height);rx.drawImage(src,-dw/2,-dh/2,dw,dh);rx.restore();}
function markLook(){for(const b of document.querySelectorAll('#looks button'))b.classList.toggle('on',b.dataset.l===cur.look);}
async function setLook(look,first){
 const my=++cur.lseq||(cur.lseq=1);cur.look=look;markLook();
 if(cur.cache[look]){cur.res=cur.cache[look];paintReview();$('rWait').classList.remove('on');return;}
 try{
  if(look!=='orig'&&!cur.cache.orig){            // quick look first: only the cut-out, no cleaning yet
   cur.cache.orig=await fetchLook('orig');if(my!==cur.lseq)return;cur.res=cur.cache.orig;paintReview();}
  $('rWait').classList.toggle('on',look!=='orig');
  if(look!=='orig'){const res=await fetchLook(look);cur.cache[look]=res;if(my!==cur.lseq)return;cur.res=res;paintReview();}
  else{cur.cache.orig=cur.cache.orig||await fetchLook('orig');if(my===cur.lseq){cur.res=cur.cache.orig;paintReview();}}
 }catch(e){toast('Could not cut out the page. Try again.',true);}
 if(my===cur.lseq)$('rWait').classList.remove('on');}
async function crop(){ // from the corners screen
 cur.cache={};cur.res=null;cur.rot=0;cur.lseq=0;show('review');await raf();
 rx.clearRect(0,0,rcv.width,rcv.height);fit(rcv,cur.w,cur.h,rcv.parentNode);
 await setLook(cur.look,true);}
$('eOk').onclick=()=>{if(!cur||!cur.quad||!cur.id){toast('Wait: the computer is still looking at the photo.',false);return;}crop();};
for(const b of document.querySelectorAll('#looks button'))b.onclick=()=>{if(cur.look===b.dataset.l)return;buzz();setLook(b.dataset.l);};
$('rRotL').onclick=()=>{cur.rot=(cur.rot+3)%4;paintReview();buzz();};$('rRotR').onclick=()=>{cur.rot=(cur.rot+1)%4;paintReview();buzz();};
$('rAdj').onclick=()=>{cur.lseq++;startEdit();};
const autoOn=()=>{try{return localStorage.getItem('auto')==='1';}catch(e){return false;}};
$('auto').classList.toggle('on',autoOn());
$('autoRow').onclick=()=>{const v=!autoOn();try{localStorage.setItem('auto',v?'1':'0');}catch(e){}$('auto').classList.toggle('on',v);};
$('rUse').onclick=async()=>{
 if(!cur.res)return;buzz();cur.lseq++;const p=cur;let blob=p.res.blob;
 const t=p.rot;p.th=thumb(turned(p.res.bmp,t),200);               // small picture for the list: cheap
 p.final=t?null:blob;p.pending=t?(async()=>{p.final=await toBlob(turned(p.res.bmp,t),0.92);})():null;   // a turned page is encoded in the background
 p.cache=null;p.res=null;p.base=null;p.view=null;               // free the big pictures; the page keeps only the small ones
 if(retake>=0)pages[retake]=p;else pages.push(p);
 const again=retake<0&&autoOn();retake=-1;cur=null;renderList();if(again)openCam();};

// ---------- page list
function renderList(){show('list');$('lt').textContent=pages.length+' page'+(pages.length==1?'':'s');const box=$('pages');box.textContent='';
 pages.forEach((p,i)=>{const d=document.createElement('div');d.className='pg';
  d.innerHTML='<div class="th"><img alt=""><i></i></div><div class="n">Page '+(i+1)+'<small></small></div><div class="ab"></div>';
  d.querySelector('img').src=p.th;d.querySelector('i').textContent=i+1;d.querySelector('small').textContent=LOOKNAME[p.look]||'';
  const ab=d.querySelector('.ab');
  const ic={u:'<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>',dn:'<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',ed:'<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',x:'<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>'};
  const mk=(k,fn,c)=>{const b=document.createElement('button');if(c)b.className=c;b.innerHTML=ic[k];b.onclick=ev=>{ev.stopPropagation();fn();};ab.appendChild(b);};
  mk('u',()=>{if(i>0){[pages[i-1],pages[i]]=[pages[i],pages[i-1]];renderList();}});
  mk('dn',()=>{if(i<pages.length-1){[pages[i+1],pages[i]]=[pages[i],pages[i+1]];renderList();}});
  mk('ed',async()=>{cur=p;retake=i;cur.cache={};try{cur.base=await decode(cur.blob);}catch(e){toast('Could not open the page.',true);return;}cur.view=turned(cur.base,cur.sr);cur.w=bw(cur.view);cur.h=bh(cur.view);startEdit();});
  mk('x',()=>{pages.splice(i,1);pages.length?renderList():home();},'red');
  d.querySelector('img').onclick=async()=>{if(p.pending)await p.pending;if(!p.final)return;if(p.vurl)URL.revokeObjectURL(p.vurl);p.vurl=URL.createObjectURL(p.final);$('vimg').src=p.vurl;$('viewer').classList.add('on');};
  box.appendChild(d);});
 $('lSend').textContent='Send '+pages.length+' page'+(pages.length==1?'':'s')+' to computer';}
$('viewer').onclick=()=>$('viewer').classList.remove('on');
function home(){show('home');}
$('lHome').onclick=home;$('lAdd').onclick=()=>{retake=-1;openCam();};
$('lSend').onclick=async()=>{if(busy||!pages.length)return;busy=true;$('lSend').disabled=true;
 const batch=Date.now().toString(36)+Math.random().toString(36).slice(2,6),n=pages.length;
 try{for(let i=0;i<n;i++){toast('Sending page '+(i+1)+' of '+n+' ...',false,60000);
   if(pages[i].pending)await pages[i].pending;
   const r=await api('/api/upload?kind=scan&batch='+batch+'&i='+i+'&n='+n+'&name=page'+(i+1)+'.jpg',{method:'POST',body:pages[i].final});
   const j=await r.json();if(!j.ok)throw new Error(j.error);}
  toast('');pages=[];home();buzz();ok();logSent(n+' page'+(n==1?'':'s'));
 }catch(e){toast('Sending failed. Check the Wi-Fi and try again (scan the QR code again if needed).',true);}
 busy=false;$('lSend').disabled=false;};
show('home');
</script></body></html>
"""
