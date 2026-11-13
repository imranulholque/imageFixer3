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
    for target in ("8.8.8.8", "10.255.255.255", "192.168.255.255"):       # the address the computer really uses to reach the network
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 1))
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
    timeout = 120                                       # a dead / idle connection is closed, not kept forever
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
        elif route == "/sw.js":                         # keeps the scanner page on the phone: it opens again even without a connection
            self._send(200, SW_JS, "application/javascript; charset=utf-8")
        elif route == "/manifest.webmanifest":
            self._send(200, MANIFEST, "application/manifest+json; charset=utf-8")
        elif route == "/icon.svg":
            self._send(200, ICON_SVG, "image/svg+xml")
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
        quad, found = scanner.detect_document(im)           # the very same finder the PC's Auto crop uses
        cx = sum(p[0] for p in quad) / 4.0                  # + the same hair-inset as the PC (no sliver of table at the edge)
        cy = sum(p[1] for p in quad) / 4.0
        quad = [(cx + (x - cx) * 0.995, cy + (y - cy) * 0.995) for x, y in quad]
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
    """Optional 'Internet' link: a free Cloudflare quick tunnel (no account). The small helper program 'cloudflared'
    is downloaded once (about 30 MB, from github.com) next to the program's settings, then started; Cloudflare gives
    this computer a public https address. Only this program's phone page is reachable, and only with the secret code in the link.
    Attributes used by the window: url, error, status_text, timed_out(), alive(), stop()."""
    URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"

    def __init__(self, port):
        self.port = port
        self.url = None
        self.error = None
        self.status_text = "Starting ..."
        self.proc = None
        self.started = time.time()
        self._stopped = False
        threading.Thread(target=self._run, daemon=True).start()

    # ---- the helper program
    @staticmethod
    def _bin_path():
        import sys
        name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        d = os.path.join(root, "ImageFixer")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    def _source(self):
        import platform
        import sys
        mach = platform.machine().lower()
        arm = mach in ("arm64", "aarch64")
        if sys.platform == "win32":
            return self.BASE + "cloudflared-windows-amd64.exe", False
        if sys.platform == "darwin":
            return self.BASE + ("cloudflared-darwin-arm64.tgz" if arm else "cloudflared-darwin-amd64.tgz"), True
        return self.BASE + ("cloudflared-linux-arm64" if arm else "cloudflared-linux-amd64"), False

    def _ensure(self):
        path = self._bin_path()
        if os.path.isfile(path) and os.path.getsize(path) > (1 << 20):
            return path
        import urllib.request
        src, tgz = self._source()
        self.status_text = "Downloading the connection helper (only the first time) ... 0%"
        tmp = path + ".part"
        with urllib.request.urlopen(src, timeout=60) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            buf = io.BytesIO() if tgz else None
            while True:
                if self._stopped:
                    raise RuntimeError("stopped")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                (buf or out).write(chunk)
                got += len(chunk)
                if total:
                    self.status_text = "Downloading the connection helper (only the first time) ... %d%%" % (got * 100 // total)
            if tgz:
                import tarfile
                buf.seek(0)
                with tarfile.open(fileobj=buf, mode="r:gz") as tf:
                    for m in tf.getmembers():
                        if os.path.basename(m.name) == "cloudflared":
                            out.write(tf.extractfile(m).read())
                            break
                    else:
                        raise RuntimeError("helper not found in the download")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o755)
        except Exception:
            pass
        return path

    # ---- the tunnel
    def _run(self):
        import subprocess
        import sys
        try:
            path = self._ensure()
        except Exception as ex:
            if not self._stopped:
                self.error = "Could not download the connection helper: %s" % str(ex)[:120]
            return
        if self._stopped:
            return
        self.started = time.time()                        # the 30 s clock starts when the helper starts
        self.status_text = "Connecting ... (can take up to 30 seconds)"
        cmd = [path, "tunnel", "--url", "http://127.0.0.1:%d" % self.port, "--no-autoupdate", "--protocol", "http2"]
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags)
        except Exception as ex:
            self.error = str(ex)[:150]
            return
        self._read()

    def _read(self):
        found = None
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace")
                if found is None:
                    for m in self.URL.findall(line):
                        if m != "https://api.trycloudflare.com":
                            found = m
                            break
                if found is not None and self.url is None and "Registered tunnel connection" in line:
                    time.sleep(1.5)                       # let the new address reach the DNS
                    self.url = found
        except Exception:
            pass
        if self._stopped:
            return
        if self.url is None and self.error is None:
            self.error = "The connection helper stopped. Check the internet connection of this computer."
        elif self.url is not None:
            self.url = None
            self.error = "The internet link was closed."

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def timed_out(self):
        return self.url is None and self.error is None and self.proc is not None and time.time() - self.started > 60

    def stop(self):
        self._stopped = True
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        self.proc = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128                            # many quick connections from the phone browser are not refused


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
            path = b["files"].get(i)                          # the phone sent this one again (bad connection): same file, no copy
            if path is None:
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


SW_JS = r"""const C='if-shell-v1';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll([self.registration.scope,'manifest.webmanifest','icon.svg'])).catch(()=>{}));});
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{const r=e.request,u=new URL(r.url);
 if(r.method!=='GET'||u.origin!==location.origin||u.pathname.indexOf('/api/')>=0)return;
 const net=Promise.race([fetch(r),new Promise((_,no)=>setTimeout(no,3500))]);          // computer reachable: always the newest page
 e.respondWith(net.then(x=>{if(x.ok){const y=x.clone();caches.open(C).then(c=>c.put(r,y));}return x;})
  .catch(()=>caches.match(r,{ignoreSearch:true}).then(m=>m||caches.match(self.registration.scope))));});   // not reachable: the copy kept on the phone
"""

MANIFEST = json.dumps({"name": "Image Fixer Scanner", "short_name": "Scanner", "start_url": "./", "scope": "./",
                       "display": "standalone", "background_color": "#0b0e14", "theme_color": "#0b0e14",
                       "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]})

ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#6366f1"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs>'
            '<rect width="512" height="512" rx="112" fill="url(#g)"/><path d="M150 190v-30a20 20 0 0 1 20-20h30M312 140h30a20 20 0 0 1 20 20v30'
            'M362 322v30a20 20 0 0 1-20 20h-30M200 372h-30a20 20 0 0 1-20-20v-30" fill="none" stroke="#fff" stroke-width="28" stroke-linecap="round"/>'
            '<rect x="196" y="204" width="120" height="104" rx="10" fill="#fff"/></svg>')


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
.lks{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.lks button{display:flex;flex-direction:column;align-items:center;gap:5px;padding:5px 5px 6px;border-radius:13px;background:var(--card);border:2px solid var(--line);font-size:12.5px;color:var(--mut);font-weight:600}
.lks button canvas{width:100%;height:64px;object-fit:contain;border-radius:7px;background:#000;display:block}
.lks button.on{border-color:var(--acc);color:#fff;background:#1d2140}
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
#camWrap{position:relative;border-radius:12px;overflow:hidden;background:#000}
#camV{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
#camO{position:absolute;inset:0;width:100%;height:100%;touch-action:none;max-width:none;max-height:none;border-radius:0;background:none}
#camMsg{position:absolute;left:10px;right:10px;bottom:10px;padding:9px 12px;border-radius:12px;background:#000b;font-size:14.5px;font-weight:600;text-align:center;line-height:1.3;pointer-events:none;transition:background .15s}
#camMsg small{display:block;font-weight:500;font-size:12.5px;opacity:.9;margin-top:2px}
#camMsg.ok{background:#1b7f4bee}#camMsg.warn{background:#8a5600ee}
#camShot{flex:none;width:74px;height:74px;border-radius:50%;background:#fff;border:5px solid var(--bg);box-shadow:0 0 0 3px #fff;margin:0 8px;transition:transform .08s}
#camShot:active{transform:scale(.9)}
.hide{display:none!important}
</style></head><body>
<div id="bar"><i></i></div>
<header><div class="logo">IF</div><div class="t"><b>Image Fixer</b><small id="st">connecting...</small></div><span id="dot"></span></header>
<div id="banner">Not connected to the computer. You can keep scanning - the pages stay on this phone. Connect to send them.</div>

<section class="scr on" id="home">
 <div class="body">
  <button class="hero" id="bScan"><span class="ic"><svg viewBox="0 0 24 24" style="width:30px;height:30px"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/></svg></span><span><b>Scan a paper</b><small>Take a photo. The page is found, cut out and straightened for you.</small></span></button>
  <div class="tiles">
   <button class="tile" id="bGal"><span class="ic"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/></svg></span><span><b>Scan a photo</b><small>Pick a saved photo and cut the page out.</small></span></button>
   <button class="tile" id="bChoose"><span class="ic"><svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg></span><span><b>Send pictures</b><small>Send photos as they are.</small></span></button>
  </div>
  <div class="hint" id="liveHint"></div>
  <div class="sent" id="sent"></div>
 </div>
</section>

<section class="scr" id="edit">
 <div class="pad"><div class="chips"><button class="chip" id="eAuto"><svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/></svg>Find paper</button><button class="chip" id="eFull">Whole photo</button><button class="chip" id="eRotL" aria-label="Turn left"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/></svg></button><button class="chip" id="eRotR" aria-label="Turn right"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg></button></div></div>
 <div class="stage"><canvas id="cv"></canvas><div class="wait" id="eWait"><div><div class="spin"></div>Finding the paper</div></div></div>
 <div class="hint" id="tip" style="padding:4px 16px"></div>
 <div class="dock"><div class="row"><button class="btn" id="eRetake">Retake</button><button class="btn pri" id="eOk" style="flex:2">Cut out</button></div></div>
</section>

<section class="scr" id="cam">
 <div class="stage" id="camStage"><div id="camWrap"><video id="camV" playsinline muted autoplay></video><canvas id="camO"></canvas><div id="camMsg">Starting the camera ...</div></div></div>
 <div class="dock"><div class="row" style="align-items:center"><button class="btn" id="camBack">Back</button><button id="camShot" aria-label="Take the photo"></button><button class="btn" id="camTorch">Light</button></div>
  <button class="btn" id="camAuto" style="min-height:38px;font-size:13.5px">Auto capture: off</button><button class="btn" id="camNative" style="min-height:38px;font-size:13.5px;color:var(--mut)">Use the phone's own camera app instead</button></div>
</section>

<section class="scr" id="review">
 <div class="stage"><canvas id="rcv"></canvas><div class="wait" id="rWait"><div><div class="spin"></div>Working</div></div></div>
 <div class="pad"><div class="lks" id="looks"><button data-l="orig"><canvas width="1" height="1"></canvas><span>Original</span></button><button data-l="clean" class="on"><canvas width="1" height="1"></canvas><span>Clean</span></button><button data-l="gray"><canvas width="1" height="1"></canvas><span>Gray</span></button><button data-l="bw"><canvas width="1" height="1"></canvas><span>B&amp;W</span></button></div></div>
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
const TUN=/^\/s\//.test(location.pathname), MAXSIDE=TUN?2400:3000, OUTSIDE=TUN?2000:2600, JQ=0.9;   // the page that is sent is a bit smaller over the internet link
let pages=[], retake=-1, cur=null, busy=false, sentLog=[], tt=0, seq=0, lastOk=Date.now(), active=0;
const DPR=()=>Math.min(window.devicePixelRatio||1,2.5);
const LOOKNAME={orig:'Original',clean:'Clean',gray:'Gray',bw:'Black & white'};
const api=async(p,opt,tries)=>{active++;prog(true);try{for(let k=0;;k++){try{const r=await fetch(base+p,opt);lastOk=Date.now();return r;}
  catch(e){if(k>=(tries==null?2:tries))throw e;await new Promise(r=>setTimeout(r,500*(k+1)));}}}   // a dropped connection is tried again
 finally{active--;prog(active>0);}};
function show(id){if(id!=='cam')stopCam();for(const s of ['home','edit','review','list','cam'])$(s).classList.toggle('on',s===id);}
function toast(t,err,ms){clearTimeout(tt);const e=$('toast');if(!t){e.classList.remove('on');return;}e.textContent=t;e.className='on'+(err?' err':'');tt=setTimeout(()=>e.classList.remove('on'),ms||(err?5000:2200));}
function prog(on){$('bar').classList.toggle('on',!!on);}
function buzz(){try{navigator.vibrate&&navigator.vibrate(15);}catch(e){}}
const raf=()=>new Promise(r=>requestAnimationFrame(()=>r()));
// ---- connection light (pings only when nothing else talked to the computer lately)
let fails=0;
async function ping(){if(document.hidden||active||Date.now()-lastOk<4000)return;
 try{const r=await fetch(base+'/api/ping');if(!r.ok)throw 0;lastOk=Date.now();fails=0;$('dot').className='on';$('st').textContent='connected to your computer';$('banner').classList.remove('on');}
 catch(e){if(++fails>=3){$('dot').className='';$('st').textContent='offline - scanning still works';$('banner').classList.add('on');}}}
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
 return {bmp:c};}      // the JPEG for the computer is made while the photo is already on screen
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
function nativeCam(){$('fCam').value='';$('fCam').click();}
function openCam(){if(LIVE())openLive();else nativeCam();}
$('bScan').onclick=()=>{retake=-1;openCam();};
$('bGal').onclick=()=>{retake=-1;$('fGal').value='';$('fGal').click();};
$('fCam').onchange=e=>gotPhoto(e.target.files[0]);$('fGal').onchange=e=>gotPhoto(e.target.files[0]);
// the photo goes to the computer ONCE (it keeps it, tagged with an id); after that only a few numbers travel
async function getBlob(){if(!cur.blob)cur.blob=await cur.blobP;return cur.blob;}
async function detect(){
 const url='/api/detect?sr='+cur.sr+(cur.id?'&id='+cur.id:'');
 let r=await api(url,{method:'POST',body:cur.id?undefined:await getBlob()});
 if(r.status===410){cur.id=null;r=await api('/api/detect?sr='+cur.sr,{method:'POST',body:await getBlob()});}
 const j=await r.json();if(!j.ok)throw new Error(j.error);cur.id=j.id;ok();return j;}
async function gotPhoto(f){if(!f)return;
 const my=++seq;toast('Opening the photo ...',false,60000);prog(true);
 try{const s=await shrink(f,MAXSIDE),o={blob:null,base:s.bmp,sr:0,view:s.bmp,w:bw(s.bmp),h:bh(s.bmp),quad:null,id:null,look:'clean',rot:0,res:null};
  o.blobP=toBlob(s.bmp,JQ);cur=o;toast('');prog(false);
  show('edit');$('tip').textContent='';await raf();layout();           // the photo is on screen first, the computer looks for the paper after
  findHere();                                                         // the paper is found right here on the phone: no waiting
 }catch(err){prog(false);toast('Could not open this photo.',true);}}
function findHere(){const r=detectLocal(cur.view);cur.quad=r.quad;cur.touched=false;setTip(r.found);draw();refine();}
async function refine(){ // the computer's finder (same as PC Auto crop) replaces the quick local guess when it arrives (if the corners were not touched)
 const me=cur,sr=cur.sr;   // the computer's finder (same as the PC's Auto crop) is the main answer, on Wi-Fi AND over the internet link; the quick local guess above only shows until it arrives
 try{const j=await detect();if(cur!==me||me.sr!==sr||me.touched)return;me.quad=j.quad.map(p=>({x:p[0],y:p[1]}));setTip(j.found);draw();}catch(e){}}
function setTip(found){$('tip').textContent=found?'Paper found. Drag a round corner or a square side if the outline is not exact.':'Paper not found. Drag the corners onto the paper, or tap "Whole photo".';}
$('eAuto').onclick=()=>{buzz();findHere();};
$('eFull').onclick=()=>{const m=0.01,w=cur.w,h=cur.h;cur.quad=[{x:w*m,y:h*m},{x:w*(1-m),y:h*m},{x:w*(1-m),y:h*(1-m)},{x:w*m,y:h*(1-m)}];draw();};
function turnSrc(cw){cur.touched=true;cur.quad=rotQuad(cur.quad,cur.w,cur.h,cw);cur.sr=(cur.sr+(cw?1:3))%4;cur.view=turned(cur.base,cur.sr);cur.w=bw(cur.view);cur.h=bh(cur.view);layout();draw();buzz();}
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
}
function pos(e){const r=cv.getBoundingClientRect(),k=cv.width/r.width;return{x:(e.clientX-r.left)*k,y:(e.clientY-r.top)*k};}
cv.onpointerdown=e=>{if(!cur.quad)return;const p=pos(e),d=DPR(),q=cur.quad.map(a=>({x:a.x*scale,y:a.y*scale})),m=mids(q);let best=null,bd=1e9;
 q.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y)-4*d;if(t<bd){bd=t;best={kind:'c',i};}});
 m.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y);if(t<bd){bd=t;best={kind:'m',i};}});
 if(best&&bd<64*d){cur.touched=true;drag=best;drag.start=cur.quad.map(a=>({x:a.x,y:a.y}));drag.p0=p;cv.setPointerCapture(e.pointerId);buzz();draw();}};
cv.onpointermove=e=>{if(!drag)return;const p=pos(e),dx=(p.x-drag.p0.x)/scale,dy=(p.y-drag.p0.y)/scale,S=drag.start;
 const cl=(v,m)=>Math.max(0,Math.min(m,v));
 if(drag.kind==='c'){cur.quad[drag.i]={x:cl(S[drag.i].x+dx,cur.w),y:cl(S[drag.i].y+dy,cur.h)};}
 else{const a=S[drag.i],b=S[(drag.i+1)%4],ux=b.x-a.x,uy=b.y-a.y,L=Math.hypot(ux,uy)||1,nx=-uy/L,ny=ux/L,t=dx*nx+dy*ny;
  [drag.i,(drag.i+1)%4].forEach(k=>{cur.quad[k]={x:cl(S[k].x+nx*t,cur.w),y:cl(S[k].y+ny*t,cur.h)};});}
 draw();};
cv.onpointerup=cv.onpointercancel=()=>{drag=null;draw();};
addEventListener('resize',()=>{if($('edit').classList.contains('on')&&cur&&cur.view){layout();draw();}else if($('review').classList.contains('on')&&cur&&cur.res)paintReview();});
$('eRetake').onclick=()=>openCam();

// ---------- the scanner on the phone itself: finds the paper, cuts it out, cleans it. Nothing but the final picture goes to the computer.
// ======================= finding the paper (pure functions on pixel data: used by the live camera AND by the photo)
function blurF(a,w,h,r){ // box blur, Float32Array
 const t=new Float32Array(a.length),o=new Float32Array(a.length),k=2*r+1;
 for(let y=0;y<h;y++){let s=0;const row=y*w;for(let i=-r;i<=r;i++)s+=a[row+Math.min(w-1,Math.max(0,i))];
  for(let x=0;x<w;x++){t[row+x]=s/k;s+=a[row+Math.min(w-1,x+r+1)]-a[row+Math.max(0,x-r)];}}
 for(let x=0;x<w;x++){let s=0;for(let i=-r;i<=r;i++)s+=t[Math.min(h-1,Math.max(0,i))*w+x];
  for(let y=0;y<h;y++){o[y*w+x]=s/k;s+=t[Math.min(h-1,y+r+1)*w+x]-t[Math.max(0,y-r)*w+x];}}
 return o;}
function otsu(v){const n=v.length,hist=new Float64Array(256);for(let i=0;i<n;i++)hist[v[i]]++;let sum=0;for(let i=0;i<256;i++)sum+=i*hist[i];
 let wB=0,sB=0,best=-1,th=127;for(let t=0;t<256;t++){wB+=hist[t];if(!wB)continue;const wF=n-wB;if(!wF)break;sB+=t*hist[t];
  const mB=sB/wB,mF=(sum-sB)/wF,q=wB*wF*(mB-mF)*(mB-mF);if(q>best){best=q;th=t;}}return th;}
function bigBlob(mask,w,h){ // biggest 4-connected blob -> {lab:Uint8Array, n}
 const seen=new Uint8Array(w*h),st=new Int32Array(w*h);let bestStart=-1,bestN=0;
 for(let s=0;s<w*h;s++){if(!mask[s]||seen[s])continue;let sp=0,n=0;st[sp++]=s;seen[s]=1;
  while(sp){const p=st[--sp];n++;const x=p%w,y=(p-x)/w;
   if(x>0&&mask[p-1]&&!seen[p-1]){seen[p-1]=1;st[sp++]=p-1;}if(x<w-1&&mask[p+1]&&!seen[p+1]){seen[p+1]=1;st[sp++]=p+1;}
   if(y>0&&mask[p-w]&&!seen[p-w]){seen[p-w]=1;st[sp++]=p-w;}if(y<h-1&&mask[p+w]&&!seen[p+w]){seen[p+w]=1;st[sp++]=p+w;}}
  if(n>bestN){bestN=n;bestStart=s;}}
 if(bestStart<0)return null;const lab=new Uint8Array(w*h),st2=[bestStart];lab[bestStart]=1;
 while(st2.length){const p=st2.pop(),x=p%w,y=(p-x)/w;
  if(x>0&&mask[p-1]&&!lab[p-1]){lab[p-1]=1;st2.push(p-1);}if(x<w-1&&mask[p+1]&&!lab[p+1]){lab[p+1]=1;st2.push(p+1);}
  if(y>0&&mask[p-w]&&!lab[p-w]){lab[p-w]=1;st2.push(p-w);}if(y<h-1&&mask[p+w]&&!lab[p+w]){lab[p+w]=1;st2.push(p+w);}}
 return {lab,n:bestN};}
function polyArea(q){let a=0;for(let i=0;i<q.length;i++){const p=q[i],r=q[(i+1)%q.length];a+=p.x*r.y-r.x*p.y;}return Math.abs(a)/2;}
function hullOf(pts){ // convex hull (monotone chain)
 pts=pts.slice().sort((a,b)=>a.x-b.x||a.y-b.y);const cr=(o,a,b)=>(a.x-o.x)*(b.y-o.y)-(a.y-o.y)*(b.x-o.x);
 const lo=[];for(const p of pts){while(lo.length>=2&&cr(lo[lo.length-2],lo[lo.length-1],p)<=0)lo.pop();lo.push(p);}
 const up=[];for(let i=pts.length-1;i>=0;i--){const p=pts[i];while(up.length>=2&&cr(up[up.length-2],up[up.length-1],p)<=0)up.pop();up.push(p);}
 lo.pop();up.pop();return lo.concat(up);}
function lineX(p0,p1,p2,p3){ // intersection of line p0p1 with line p2p3 -> {x,y,t,s} (t along p0p1, s along p2p3) or null
 const d1x=p1.x-p0.x,d1y=p1.y-p0.y,d2x=p3.x-p2.x,d2y=p3.y-p2.y,den=d1x*d2y-d1y*d2x;if(Math.abs(den)<1e-9)return null;
 const t=((p2.x-p0.x)*d2y-(p2.y-p0.y)*d2x)/den,s=((p2.x-p0.x)*d1y-(p2.y-p0.y)*d1x)/den;return {x:p0.x+t*d1x,y:p0.y+t*d1y,t,s};}
function quadFromHull(h){ // smallest enclosing 4-gon: drop the hull edge whose removal adds the least area (neighbours are extended)
 let P=h.slice();
 while(P.length>4){const n=P.length;let bi=-1,ba=1e30,bx=null;
  for(let i=0;i<n;i++){const p0=P[(i-1+n)%n],p1=P[i],p2=P[(i+1)%n],p3=P[(i+2)%n],X=lineX(p0,p1,p3,p2);
   if(!X||X.t<=1||X.s<=1)continue;const a=Math.abs((p1.x-X.x)*(p2.y-X.y)-(p1.y-X.y)*(p2.x-X.x))/2;if(a<ba){ba=a;bi=i;bx={x:X.x,y:X.y};}}
  if(bi<0){let m=1e30,mi=0;for(let i=0;i<n;i++){const a=polyArea([P[(i-1+n)%n],P[i],P[(i+1)%n]]);if(a<m){m=a;mi=i;}}P.splice(mi,1);continue;}
  const j=(bi+1)%n;P[bi]=bx;P.splice(j,1);}
 return P;}
function orderQuad(q){ // -> TL,TR,BR,BL
 const s=q.map(p=>p.x+p.y),d=q.map(p=>p.x-p.y);let tl=0,br=0,tr=0,bl=0;
 for(let i=1;i<4;i++){if(s[i]<s[tl])tl=i;if(s[i]>s[br])br=i;if(d[i]>d[tr])tr=i;if(d[i]<d[bl])bl=i;}
 const set=new Set([tl,br,tr,bl]);if(set.size<4){const c={x:q.reduce((a,p)=>a+p.x,0)/4,y:q.reduce((a,p)=>a+p.y,0)/4};
  const o=q.slice().sort((a,b)=>Math.atan2(a.y-c.y,a.x-c.x)-Math.atan2(b.y-c.y,b.x-c.x));let k=0;for(let i=1;i<4;i++)if(o[i].x+o[i].y<o[k].x+o[k].y)k=i;return [o[k],o[(k+1)%4],o[(k+2)%4],o[(k+3)%4]];}
 return [q[tl],q[tr],q[br],q[bl]];}

// coarse finder: several colour / brightness masks on a small picture -> paper-like blobs -> enclosing 4-gons; the best one is the one
// whose 4 sides all have a real colour step across them (this is what keeps it away from a printed frame, a red box, a hand ...)
function sideContrast(chb,w,h,q){
 const smp=(x,y,c)=>{x=x<0?0:x>w-1.001?w-1.001:x;y=y<0?0:y>h-1.001?h-1.001:y;const x0=x|0,y0=y|0,fx=x-x0,fy=y-y0,i=y0*w+x0,a=chb[c];
  return (a[i]*(1-fx)+a[i+1]*fx)*(1-fy)+(a[i+w]*(1-fx)+a[i+w+1]*fx)*fy;};
 const cen={x:q.reduce((a,p)=>a+p.x,0)/4,y:q.reduce((a,p)=>a+p.y,0)/4},sides=[],off=Math.max(2.5,0.009*Math.max(w,h));
 for(let i=0;i<4;i++){const a=q[i],b=q[(i+1)%4],L=Math.hypot(b.x-a.x,b.y-a.y)||1,ux=(b.x-a.x)/L,uy=(b.y-a.y)/L;let nx=uy,ny=-ux;
  if(nx*((a.x+b.x)/2-cen.x)+ny*((a.y+b.y)/2-cen.y)<0){nx=-nx;ny=-ny;}
  const K=24,E=[];for(let k=0;k<K;k++){const s=0.1+0.8*(k+0.5)/K,px=a.x+ux*s*L,py=a.y+uy*s*L;let best=0;
   for(const sh of [-1.5,0,1.5]){let e=0;for(let c=0;c<3;c++){const df=smp(px+nx*(off+sh),py+ny*(off+sh),c)-smp(px-nx*(off-sh),py-ny*(off-sh),c);e+=df*df;}best=Math.max(best,Math.sqrt(e));}E.push(best);}
  E.sort((x,y)=>x-y);sides.push(E[E.length>>1]);}
 return 0.5*(sides.reduce((a,v)=>a+v,0)/4)+0.5*Math.min(...sides);}
function coarseCands(d,w,h){
 const N=w*h,L=new Float32Array(N);for(let i=0;i<N;i++)L[i]=0.299*d[4*i]+0.587*d[4*i+1]+0.114*d[4*i+2];
 const Lb=blurF(blurF(L,w,h,2),w,h,2),u1=new Uint8Array(N);for(let i=0;i<N;i++)u1[i]=Lb[i];
 const chb=[0,1,2].map(c=>{const a=new Float32Array(N);for(let i=0;i<N;i++)a[i]=d[4*i+c];return blurF(a,w,h,1);});
 const dist=(cr,cg,cb)=>{const D=new Float32Array(N);for(let i=0;i<N;i++)D[i]=Math.hypot(d[4*i]-cr,d[4*i+1]-cg,d[4*i+2]-cb);
  const Db=blurF(blurF(D,w,h,2),w,h,2),u=new Uint8Array(N);for(let i=0;i<N;i++)u[i]=Math.min(255,Db[i]*1.3);return u;};
 let br=0,bg=0,bb=0,bn=0;const m=Math.max(2,Math.round(Math.min(w,h)*0.03));
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){if(x<m||y<m||x>=w-m||y>=h-m){const i=4*(y*w+x);br+=d[i];bg+=d[i+1];bb+=d[i+2];bn++;}}
 const u2=dist(br/bn,bg/bn,bb/bn);
 // the paper's own colour = the middle of the picture (median of a small patch, so a few letters do not matter)
 const px0=Math.round(w*0.42),px1=Math.round(w*0.58),py0=Math.round(h*0.42),py1=Math.round(h*0.58),R=[],G=[],B=[];
 for(let y=py0;y<py1;y++)for(let x=px0;x<px1;x++){const i=4*(y*w+x);R.push(d[i]);G.push(d[i+1]);B.push(d[i+2]);}
 const med=a=>{a.sort((x,y)=>x-y);return a[a.length>>1];};
 const u4=dist(med(R),med(G),med(B));                       // small = like the paper
 const t1=otsu(u1),t2=otsu(u2),t4=otsu(u4),masks=[],pm=new Uint8Array(N);
 const mk=f=>{const a=new Uint8Array(N);for(let i=0;i<N;i++)a[i]=f(i)?1:0;return a;};
 masks.push(mk(i=>u2[i]>t2),mk(i=>u1[i]>t1&&u2[i]>t2*0.7),mk(i=>u1[i]>t1),mk(i=>u4[i]<t4),mk(i=>u4[i]<t4*0.6));
 const cands=[];
 for(const mkm of masks){
  let a=new Float32Array(N);for(let i=0;i<N;i++)a[i]=mkm[i];
  a=blurF(a,w,h,3);const c1=new Uint8Array(N);for(let i=0;i<N;i++)c1[i]=a[i]>0.4?1:0;
  a=new Float32Array(N);for(let i=0;i<N;i++)a[i]=c1[i];a=blurF(a,w,h,3);const c2=new Uint8Array(N);for(let i=0;i<N;i++)c2[i]=a[i]>0.6?1:0;
  const bl=bigBlob(c2,w,h);if(!bl)continue;const frac=bl.n/N;if(frac<0.08||frac>0.985)continue;
  const pts=[];let cx=0,cy=0;
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const p=y*w+x;if(!bl.lab[p])continue;cx+=x;cy+=y;
   const edge=x===0||y===0||x===w-1||y===h-1||!bl.lab[p-1]||!bl.lab[p+1]||!bl.lab[p-w]||!bl.lab[p+w];if(edge)pts.push({x:x+0.5,y:y+0.5});}
  if(pts.length<8)continue;const hull=hullOf(pts);if(hull.length<4)continue;const q=orderQuad(quadFromHull(hull));
  const area=Math.max(1,polyArea(q)),fill=bl.n/area;cx/=bl.n;cy/=bl.n;if(fill<0.7||fill>1.25)continue;
  const centre=1-0.5*Math.hypot(cx-w/2,cy-h/2)/Math.hypot(w/2,h/2),con=sideContrast(chb,w,h,q);
  cands.push({q,fill,frac,score:con*Math.sqrt(frac)*Math.sqrt(centre)*Math.min(1,fill),con});}
 return cands;}
function coarseQuad(d,w,h){const c=coarseCands(d,w,h);if(!c.length)return null;c.sort((a,b)=>b.score-a.score);return c[0];}

// straight-edge refinement: every side of the outline is moved onto the real edge in the picture (many samples + a robust line fit),
// the corners are the meeting points of the 4 fitted lines -> straight sides, exact corners, no wobble.
function refineQuad(d,w,h,q0,rounds){
 const N=w*h,ch=[new Float32Array(N),new Float32Array(N),new Float32Array(N)];
 for(let c=0;c<3;c++){const a=new Float32Array(N);for(let i=0;i<N;i++)a[i]=d[4*i+c];ch[c]=blurF(a,w,h,1);}
 const smp=(x,y,c)=>{x=x<0?0:x>w-1.001?w-1.001:x;y=y<0?0:y>h-1.001?h-1.001:y;const x0=x|0,y0=y|0,fx=x-x0,fy=y-y0,i=y0*w+x0,a=ch[c];
  return (a[i]*(1-fx)+a[i+1]*fx)*(1-fy)+(a[i+w]*(1-fx)+a[i+w+1]*fx)*fy;};
 let q=q0.map(p=>({x:p.x,y:p.y})),band=Math.max(6,0.03*Math.max(w,h)),moved=0;
 for(let r=0;r<(rounds||2);r++){
  const cen={x:q.reduce((a,p)=>a+p.x,0)/4,y:q.reduce((a,p)=>a+p.y,0)/4},lines=[];
  for(let i=0;i<4;i++){
   const a=q[i],b=q[(i+1)%4],L=Math.hypot(b.x-a.x,b.y-a.y)||1,ux=(b.x-a.x)/L,uy=(b.y-a.y)/L;let nx=uy,ny=-ux;
   const mx=(a.x+b.x)/2-cen.x,my=(a.y+b.y)/2-cen.y;if(nx*mx+ny*my<0){nx=-nx;ny=-ny;}
   const K=Math.max(12,Math.min(90,Math.round(L/5))),P=[],E=[];
   for(let k=0;k<K;k++){const s=0.06+0.88*(k+0.5)/K,px=a.x+ux*s*L,py=a.y+uy*s*L;
    const prof=[],ts=[];let mxv=0;
    for(let t=-band;t<=band;t+=0.5){
     const xa=px+nx*(t+1.5),ya=py+ny*(t+1.5),xb=px+nx*(t-1.5),yb=py+ny*(t-1.5);
     let e=0;for(let c=0;c<3;c++){const df=smp(xa,ya,c)-smp(xb,yb,c);e+=df*df;}e=Math.sqrt(e);prof.push(e);ts.push(t);if(e>mxv)mxv=e;}
    // the OUTERMOST strong edge wins (a printed frame / header line inside the page is not the page edge)
    let bt=0,bi=-1;for(let j=prof.length-2;j>=1;j--){if(prof[j]>=0.55*mxv&&prof[j]>=prof[j-1]&&prof[j]>=prof[j+1]){bi=j;break;}}
    if(bi<0){let m0=-1;for(let j=0;j<prof.length;j++)if(prof[j]>m0){m0=prof[j];bi=j;}}bt=ts[bi];
    P.push({x:px+nx*bt,y:py+ny*bt});E.push(mxv);}
   const sorted=E.slice().sort((x,y)=>x-y),med=sorted[sorted.length>>1];
   let use=P.filter((p,k)=>E[k]>=Math.max(5,0.5*med));
   let ln=null;
   if(use.length>=Math.max(6,K*0.35)){
    for(let it=0;it<4;it++){
     let mx2=0,my2=0;for(const p of use){mx2+=p.x;my2+=p.y;}mx2/=use.length;my2/=use.length;
     let sxx=0,sxy=0,syy=0;for(const p of use){const dx=p.x-mx2,dy=p.y-my2;sxx+=dx*dx;sxy+=dx*dy;syy+=dy*dy;}
     const th=0.5*Math.atan2(2*sxy,sxx-syy),dx=Math.cos(th),dy=Math.sin(th);
     const res=use.map(p=>Math.abs((p.x-mx2)*(-dy)+(p.y-my2)*dx)),rs=res.slice().sort((x,y)=>x-y),mr=rs[rs.length>>1];
     ln={x:mx2,y:my2,dx,dy,n:use.length,res:mr};
     const lim=Math.max(1.0,2.5*mr),keep=use.filter((p,k)=>res[k]<=lim);if(keep.length<Math.max(6,K*0.3)||keep.length===use.length)break;use=keep;}
    if(ln.n<Math.max(6,K*0.3))ln=null;}
   lines.push(ln||{x:a.x,y:a.y,dx:ux,dy:uy,n:0,res:0,weak:true});}
  const nq=[];let ok=true;
  for(let i=0;i<4;i++){const l1=lines[(i+3)%4],l2=lines[i],X=lineX({x:l1.x,y:l1.y},{x:l1.x+l1.dx,y:l1.y+l1.dy},{x:l2.x,y:l2.y},{x:l2.x+l2.dx,y:l2.y+l2.dy});
   if(!X||Math.hypot(X.x-q[i].x,X.y-q[i].y)>band*2.2+0.02*Math.max(w,h)){ok=false;break;}nq.push({x:X.x,y:X.y});}
  if(!ok||polyArea(nq)<0.6*polyArea(q))break;q=nq;moved++;band=Math.max(2.5,band*0.4);}
 return {quad:q,moved};}

// d = RGBA of the picture (any size). Returns {quad:[TL,TR,BR,BL] in this picture's pixels, found}
function findPaper(d,W,H,small,smW,smH,opt){ // small = the same picture scaled down for the coarse step (RGBA, smW x smH)
 const best=coarseQuad(small,smW,smH),k=W/smW;
 if(!best){const mm=0.04;return {quad:[[mm,mm],[1-mm,mm],[1-mm,1-mm],[mm,1-mm]].map(p=>({x:p[0]*W,y:p[1]*H})),found:false};}
 let q=best.q.map(p=>({x:p.x*k,y:p.y*k}));
 let found=best.fill>0.82&&best.frac>0.15;
 if(!(opt&&opt.noRefine)){try{const r=refineQuad(d,W,H,q,(opt&&opt.rounds)||3);if(r.moved)q=r.quad;}catch(e){}}
 q=orderQuad(q).map(p=>({x:Math.max(0,Math.min(W,p.x)),y:Math.max(0,Math.min(H,p.y))}));
 return {quad:q,found};}

function detectLocal(src,live){ // -> {quad:[TL,TR,BR,BL] in src pixels, found}. live = quick version for the camera view
 const W=bw(src),H=bh(src),mk=(side)=>{const s=Math.min(1,side/Math.max(W,H)),w=Math.max(8,Math.round(W*s)),h=Math.max(8,Math.round(H*s)),c=document.createElement('canvas');
  c.width=w;c.height=h;const g=c.getContext('2d',{willReadFrequently:true});g.imageSmoothingQuality='high';g.drawImage(src,0,0,w,h);return {w,h,d:g.getImageData(0,0,w,h).data,s};};
 const sm=mk(live?320:420);
 if(live){const r=findPaper(sm.d,sm.w,sm.h,sm.d,sm.w,sm.h,{rounds:2});const k=1/sm.s;return {quad:r.quad.map(p=>({x:p.x*k,y:p.y*k})),found:r.found};}
 const fn=mk(1000),r=findPaper(fn.d,fn.w,fn.h,sm.d,sm.w,sm.h,{rounds:3}),k=1/fn.s;
 return {quad:r.quad.map(p=>({x:Math.max(0,Math.min(W,p.x*k)),y:Math.max(0,Math.min(H,p.y*k))})),found:r.found};}

// the real proportions of the sheet from its 4 photographed corners (same maths as the computer's straighten): a tilted A4 stays A4
function sheetRatio(q,W,H){const u0=W/2,v0=H/2,[tl,tr,br,bl]=q,dist=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
 const simple=(dist(tl,tr)+dist(bl,br))/Math.max(1e-6,dist(tl,bl)+dist(tr,br));
 try{const m1=[tl.x,tl.y,1],m2=[tr.x,tr.y,1],m3=[bl.x,bl.y,1],m4=[br.x,br.y,1];
  const cr=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dt=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const k2=dt(cr(m1,m4),m3)/dt(cr(m2,m4),m3),k3=dt(cr(m1,m4),m2)/dt(cr(m3,m4),m2);
  const n2=m2.map((v,i)=>k2*v-m1[i]),n3=m3.map((v,i)=>k3*v-m1[i]),den=n2[2]*n3[2];if(Math.abs(den)<1e-9)return simple;
  const f2=-((n2[0]*n3[0]-(n2[0]*n3[2]+n2[2]*n3[0])*u0+n2[2]*n3[2]*u0*u0)+(n2[1]*n3[1]-(n2[1]*n3[2]+n2[2]*n3[1])*v0+n2[2]*n3[2]*v0*v0))/den;
  if(!isFinite(f2)||f2<=0)return simple;const f=Math.sqrt(f2);
  // M = Ai^T Ai with A=[[f,0,u0],[0,f,v0],[0,0,1]]  ->  Ai=[[1/f,0,-u0/f],[0,1/f,-v0/f],[0,0,1]]
  const Ai=[[1/f,0,-u0/f],[0,1/f,-v0/f],[0,0,1]],ap=v=>[Ai[0][0]*v[0]+Ai[0][1]*v[1]+Ai[0][2]*v[2],Ai[1][0]*v[0]+Ai[1][1]*v[1]+Ai[1][2]*v[2],v[2]];
  const a2=ap(n2),a3=ap(n3),r=Math.sqrt(Math.max(1e-12,dt(a2,a2))/Math.max(1e-12,dt(a3,a3)));
  if(!isFinite(r)||r<0.2||r>5||Math.abs(Math.log(r/simple))>0.45)return simple;return r;}catch(e){return simple;}}

function warpLocal(src,quad,maxSide){ // cut the quad out of src and remove the camera angle -> canvas
 const [p0,p1,p2,p3]=quad,dist=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
 const w0=Math.max(dist(p0,p1),dist(p3,p2)),h0=Math.max(dist(p0,p3),dist(p1,p2)),ratio=sheetRatio(quad,bw(src),bh(src));let w,h;
 if(w0/Math.max(h0,1e-6)>ratio){h=h0;w=h0*ratio;if(w<w0){w=w0;h=w0/ratio;}}else{w=w0;h=w0/ratio;if(h<h0){h=h0;w=h0*ratio;}}
 const k=Math.min(1,maxSide/Math.max(w,h));
 w=Math.max(8,Math.round(w*k));h=Math.max(8,Math.round(h*k));
 const sw=bw(src),sh=bh(src),sc=document.createElement('canvas');sc.width=sw;sc.height=sh;const sg=sc.getContext('2d',{willReadFrequently:true});sg.drawImage(src,0,0);
 const S=sg.getImageData(0,0,sw,sh).data,out=document.createElement('canvas');out.width=w;out.height=h;const og=out.getContext('2d'),O=og.createImageData(w,h),D=O.data;
 const sx=p0.x-p1.x+p2.x-p3.x,sy=p0.y-p1.y+p2.y-p3.y;let a,b,c,d,e,f,gg=0,hh=0;
 if(Math.abs(sx)<1e-9&&Math.abs(sy)<1e-9){a=p1.x-p0.x;b=p3.x-p0.x;c=p0.x;d=p1.y-p0.y;e=p3.y-p0.y;f=p0.y;}
 else{const dx1=p1.x-p2.x,dx2=p3.x-p2.x,dy1=p1.y-p2.y,dy2=p3.y-p2.y,det=dx1*dy2-dx2*dy1;
  gg=(sx*dy2-dx2*sy)/det;hh=(dx1*sy-sx*dy1)/det;a=p1.x-p0.x+gg*p1.x;b=p3.x-p0.x+hh*p3.x;c=p0.x;d=p1.y-p0.y+gg*p1.y;e=p3.y-p0.y+hh*p3.y;f=p0.y;}
 const mx=sw-1.001,my=sh-1.001;let o=0;
 for(let y=0;y<h;y++){const v=(y+0.5)/h;for(let x=0;x<w;x++){const u=(x+0.5)/w,z=gg*u+hh*v+1;
  let X=(a*u+b*v+c)/z,Y=(d*u+e*v+f)/z;X=X<0?0:X>mx?mx:X;Y=Y<0?0:Y>my?my:Y;
  const x0=X|0,y0=Y|0,fx=X-x0,fy=Y-y0,i=4*(y0*sw+x0),j=i+4*sw,w00=(1-fx)*(1-fy),w10=fx*(1-fy),w01=(1-fx)*fy,w11=fx*fy;
  D[o]=S[i]*w00+S[i+4]*w10+S[j]*w01+S[j+4]*w11;D[o+1]=S[i+1]*w00+S[i+5]*w10+S[j+1]*w01+S[j+5]*w11;D[o+2]=S[i+2]*w00+S[i+6]*w10+S[j+2]*w01+S[j+6]*w11;D[o+3]=255;o+=4;}}
 og.putImageData(O,0,0);return out;}

function lookLocal(src,look){ // 'clean' = white paper + even light (colour kept), 'gray', 'bw'
 if(look==='orig')return src;const w=src.width,h=src.height,g=src.getContext('2d'),I=g.getImageData(0,0,w,h),D=I.data;
 const f=8,sw=Math.max(4,Math.ceil(w/f)),sh=Math.max(4,Math.ceil(h/f)),sm=document.createElement('canvas');sm.width=sw;sm.height=sh;
 const sg=sm.getContext('2d',{willReadFrequently:true});sg.imageSmoothingQuality='high';sg.drawImage(src,0,0,sw,sh);const sd=sg.getImageData(0,0,sw,sh).data;
 let L=new Float32Array(sw*sh);for(let i=0;i<sw*sh;i++)L[i]=0.299*sd[4*i]+0.587*sd[4*i+1]+0.114*sd[4*i+2];
 const R=Math.max(2,Math.round(Math.min(sw,sh)*0.035)),M=new Float32Array(sw*sh);          // paper brightness = the brightest nearby (text is darker)
 const T=new Float32Array(sw*sh);for(let y=0;y<sh;y++)for(let x=0;x<sw;x++){let m=0;for(let i=Math.max(0,x-R);i<=Math.min(sw-1,x+R);i++){const v=L[y*sw+i];if(v>m)m=v;}T[y*sw+x]=m;}
 for(let y=0;y<sh;y++)for(let x=0;x<sw;x++){let m=0;for(let i=Math.max(0,y-R);i<=Math.min(sh-1,y+R);i++){const v=T[i*sw+x];if(v>m)m=v;}M[y*sw+x]=m;}
 const B=blurF(blurF(M,sw,sh,R),sw,sh,R);
 for(let y=0;y<h;y++){const fy=Math.min(sh-1.001,Math.max(0,y/f-0.5)),y0=fy|0,ty=fy-y0;
  for(let x=0;x<w;x++){const fx=Math.min(sw-1.001,Math.max(0,x/f-0.5)),x0=fx|0,tx=fx-x0,i0=y0*sw+x0;
   const bg=(B[i0]*(1-tx)+B[i0+1]*tx)*(1-ty)+(B[i0+sw]*(1-tx)+B[i0+sw+1]*tx)*ty,k=255/Math.max(bg,70),o=4*(y*w+x);
   let r=D[o]*k,gn=D[o+1]*k,b=D[o+2]*k;
   if(look==='bw'){const l=0.299*r+0.587*gn+0.114*b;let t=(l-150)/45;t=t<0?0:t>1?1:t;t=t*t*(3-2*t)*255;r=gn=b=t;}
   else{r=(r-22)*1.12;gn=(gn-22)*1.12;b=(b-22)*1.12;if(look==='gray'){const l=0.299*r+0.587*gn+0.114*b;r=gn=b=l;}}
   D[o]=r;D[o+1]=gn;D[o+2]=b;}}
 const out=document.createElement('canvas');out.width=w;out.height=h;out.getContext('2d').putImageData(I,0,0);return out;}

// ---------- cut out + review: all on the phone
const rcv=$('rcv'),rx=rcv.getContext('2d');
function paintReview(){if(!cur||!cur.res)return;const src=cur.res,t=cur.rot,w=t%2?bh(src):bw(src),h=t%2?bw(src):bh(src);
 fit(rcv,w,h,rcv.parentNode);rx.imageSmoothingQuality='high';rx.save();rx.translate(rcv.width/2,rcv.height/2);rx.rotate(t*Math.PI/2);
 const dw=(t%2?rcv.height:rcv.width),dh=(t%2?rcv.width:rcv.height);rx.drawImage(src,-dw/2,-dh/2,dw,dh);rx.restore();}
function markLook(){for(const b of document.querySelectorAll('#looks button'))b.classList.toggle('on',b.dataset.l===cur.look);}
async function setLook(look){
 cur.look=look;markLook();const my=cur;
 if(look!=='orig'){$('rWait').classList.add('on');await raf();await raf();if(cur!==my)return;}
 try{my.res=lookLocal(my.warp,look);if(cur===my)paintReview();}catch(e){toast('Could not clean the page.',true);}
 $('rWait').classList.remove('on');}
async function crop(){ // from the corners screen
 cur.rot=0;cur.res=null;cur.warp=null;show('review');$('rWait').classList.add('on');await raf();await raf();
 try{cur.warp=warpLocal(cur.view,cur.quad,OUTSIDE);}catch(e){toast('Could not cut out the page. Try again.',true);$('rWait').classList.remove('on');return;}
 cur.res=cur.warp;paintReview();fillLooks(cur);await setLook(cur.look);}
function fillLooks(p){ // the four looks as small previews, made from a small copy of the cut-out page
 try{const sm=warpLocal(p.view,p.quad,170);
  for(const b of document.querySelectorAll('#looks button')){const o=lookLocal(sm,b.dataset.l),c=b.querySelector('canvas'),k=Math.min(1,2/1)*1;
   c.width=o.width;c.height=o.height;c.getContext('2d').drawImage(o,0,0);}}catch(e){}}
$('eOk').onclick=()=>{if(cur&&cur.quad)crop();};
for(const b of document.querySelectorAll('#looks button'))b.onclick=()=>{if(cur.look===b.dataset.l||!cur.warp)return;buzz();setLook(b.dataset.l);};
$('rRotL').onclick=()=>{cur.rot=(cur.rot+3)%4;paintReview();buzz();};$('rRotR').onclick=()=>{cur.rot=(cur.rot+1)%4;paintReview();buzz();};
$('rAdj').onclick=()=>{startEdit();};
const autoOn=()=>{try{return localStorage.getItem('auto')==='1';}catch(e){return false;}};
$('auto').classList.toggle('on',autoOn());
$('autoRow').onclick=()=>{const v=!autoOn();try{localStorage.setItem('auto',v?'1':'0');}catch(e){}$('auto').classList.toggle('on',v);};
$('rUse').onclick=()=>{
 if(!cur.res)return;buzz();const p=cur,bm=p.res,t=p.rot;
 p.th=thumb(t?turned(bm,t):bm,200);                                 // small picture for the list: cheap
 p.final=null;p.saved=false;p.pending=(async()=>{await raf();p.final=await toBlob(t?turned(bm,t):bm,0.92);persist();})();     // the JPEG is made in the background
 p.res=null;p.warp=null;p.base=null;p.view=null;                    // free the big pictures; the page keeps only the small ones
 if(retake>=0)pages[retake]=p;else pages.push(p);
 const again=retake<0&&autoOn();retake=-1;cur=null;renderList();if(again)openCam();};


// ---------- live camera guide: a + in the middle of the view, the paper outline drawn live, plain instructions, tap = focus there
const LIVE=()=>!!(window.isSecureContext&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia);
let camS=null,camT=null,camIC=null,camTimer=0,camQ=null,camMiss=0,camStable=0,camBusy=false,camTap=null,camTorchOn=false,camSay='',camVib=false,camGrab=null,camGood=false;
if(!LIVE())$('liveHint').innerHTML='The live paper outline needs a secure link: use the <b>Internet</b> link of the computer, or on Android Chrome open <b>chrome://flags/#unsafely-treat-insecure-origin-as-secure</b>, add <b>'+location.origin+'</b>, enable it and restart Chrome. Until then the phone\'s own camera app opens.';
function stopCam(){clearTimeout(camTimer);camTimer=0;if(camS){try{camS.getTracks().forEach(t=>t.stop());}catch(e){}}
 camS=camT=camIC=null;camQ=null;camStable=0;camBusy=false;camTorchOn=false;const v=$('camV');if(v){try{v.srcObject=null;}catch(e){}}}
async function openLive(){
 let st;
 try{st=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:4096},height:{ideal:3072}},audio:false});}
 catch(e){toast('The camera is blocked here. Opening the phone\'s camera app.',true);nativeCam();return;}
 show('cam');camS=st;camT=st.getVideoTracks()[0];camQ=null;camMiss=0;camStable=0;camBusy=false;camTap=null;camTorchOn=false;camVib=false;
 const v=$('camV');v.srcObject=st;try{await v.play();}catch(e){}
 for(let i=0;i<40&&!v.videoWidth;i++)await new Promise(r=>setTimeout(r,50));
 if(!v.videoWidth||camS!==st){stopCam();toast('Could not start the camera.',true);nativeCam();return;}
 let caps={};try{caps=camT.getCapabilities?camT.getCapabilities():{};}catch(e){}
 try{if((caps.focusMode||[]).includes('continuous'))await camT.applyConstraints({advanced:[{focusMode:'continuous'}]});}catch(e){}
 $('camTorch').style.visibility=caps.torch?'visible':'hidden';$('camTorch').textContent='Light';
 camIC=window.ImageCapture?new ImageCapture(camT):null;
 camLayout();camSet('','Starting ...');camTick();}
function camLayout(){const v=$('camV');if(!v.videoWidth)return;const r=$('camStage').getBoundingClientRect(),k=Math.min((r.width-24)/v.videoWidth,(r.height-8)/v.videoHeight),d=DPR();
 const w=Math.max(80,Math.floor(v.videoWidth*k)),h=Math.max(80,Math.floor(v.videoHeight*k)),W=$('camWrap'),o=$('camO');
 W.style.width=w+'px';W.style.height=h+'px';o.width=Math.round(w*d);o.height=Math.round(h*d);}
addEventListener('resize',()=>{if(camS)camLayout();});
function camSet(cls,txt,sub){const m=$('camMsg');m.className=cls;const key=cls+'|'+txt+'|'+(sub||'');if(key===camSay)return;camSay=key;
 m.textContent=txt;if(sub){const s=document.createElement('small');s.textContent=sub;m.appendChild(s);}}
async function camFocusAt(nx,ny){if(!camT)return;let caps={};try{caps=camT.getCapabilities();}catch(e){}
 const modes=caps.focusMode||[],mode=modes.includes('continuous')?'continuous':modes.includes('single-shot')?'single-shot':null;
 try{await camT.applyConstraints({advanced:[mode?{focusMode:mode,pointsOfInterest:[{x:nx,y:ny}]}:{pointsOfInterest:[{x:nx,y:ny}]}]});}catch(e){}}   // best effort: phones that cannot take a focus point just keep their own autofocus
$('camO').onpointerdown=e=>{const r=$('camO').getBoundingClientRect(),nx=(e.clientX-r.left)/r.width,ny=(e.clientY-r.top)/r.height;
 camTap={x:nx,y:ny,t:Date.now()};camFocusAt(nx,ny);buzz();};
$('camTorch').onclick=async()=>{if(!camT)return;camTorchOn=!camTorchOn;try{await camT.applyConstraints({advanced:[{torch:camTorchOn}]});$('camTorch').textContent=camTorchOn?'Light on':'Light';}catch(e){camTorchOn=false;}};
$('camBack').onclick=()=>{pages.length?renderList():home();};
$('camNative').onclick=()=>{stopCam();nativeCam();};
$('camShot').onclick=async()=>{if(camBusy||!camS)return;camBusy=true;buzz();clearTimeout(camTimer);
 let blob=null;const v=$('camV');
 if(camIC){try{blob=await Promise.race([camIC.takePhoto(),new Promise((_,no)=>setTimeout(no,4000))]);}catch(e){blob=null;}}   // the camera's full-size photo (sharper, focused)
 if(!blob){try{const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;c.getContext('2d').drawImage(v,0,0);blob=await toBlob(c,0.92);}catch(e){}}
 if(!blob){camBusy=false;toast('Could not take the photo. Try again.',true);camTick();return;}
 try{v.pause();}catch(e){}gotPhoto(blob);};
function camTick(){clearTimeout(camTimer);if(!camS||camBusy)return;
 const v=$('camV');
 if(v.videoWidth&&v.readyState>=2){
  try{
   const m=420,s=Math.min(1,m/Math.max(v.videoWidth,v.videoHeight)),gw=Math.max(8,Math.round(v.videoWidth*s)),gh=Math.max(8,Math.round(v.videoHeight*s));
   if(!camGrab||camGrab.width!==gw||camGrab.height!==gh){camGrab=document.createElement('canvas');camGrab.width=gw;camGrab.height=gh;}
   camGrab.getContext('2d',{willReadFrequently:true}).drawImage(v,0,0,gw,gh);
   const r=detectLocal(camGrab,true);let q=r.found?r.quad.map(p=>({x:p.x/gw,y:p.y/gh})):null,move=1;
   if(q){if(camQ){move=q.reduce((a,p,i)=>a+Math.hypot(p.x-camQ[i].x,p.y-camQ[i].y),0)/4;q=q.map((p,i)=>({x:(p.x+camQ[i].x)/2,y:(p.y+camQ[i].y)/2}));}camQ=q;camMiss=0;}
   else if(++camMiss>2)camQ=null;
   camStable=(q&&move<0.014)?camStable+1:0;
   camJudge(v);camDraw();
  }catch(e){}}
 camTimer=setTimeout(camTick,150);}
function camJudge(v){camGood=false;
 if(!camQ){camSet('warn','Looking for the paper ...','Put it on a plain, darker table. Keep all 4 corners in the view.');return;}
 const q=camQ,W=v.videoWidth,H=v.videoHeight,area=polyArea(q),pct=Math.round(area*100),sub='The page fills '+pct+'% of the view - the rest is cut away';
 const cx=q.reduce((a,p)=>a+p.x,0)/4,cy=q.reduce((a,p)=>a+p.y,0)/4,dx=cx-0.5,dy=cy-0.5;
 const clip=q.some(p=>p.x<0.012||p.x>0.988||p.y<0.012||p.y>0.988);
 const d=(a,b)=>Math.hypot((a.x-b.x)*W,(a.y-b.y)*H),rt=Math.max(d(q[0],q[1]),d(q[3],q[2]))/Math.max(1,Math.min(d(q[0],q[1]),d(q[3],q[2]))),rl=Math.max(d(q[0],q[3]),d(q[1],q[2]))/Math.max(1,Math.min(d(q[0],q[3]),d(q[1],q[2])));
 if(clip)return camSet('warn','Move the phone back a little','All 4 corners of the paper must be inside the view.');
 if(area<0.3)return camSet('warn','Move closer','The paper is small in the view ('+pct+'%).');
 if(Math.abs(dx)>0.07||Math.abs(dy)>0.07){const h=Math.abs(dx)>=Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up');
  return camSet('warn','Move the phone a little '+h,'Keep the whole paper in the middle of the view.');}
 if(Math.max(rt,rl)>1.28)return camSet('warn','Hold the phone flatter, straight above the paper','The page looks tilted. Less tilt = a better scan.');
 if(camStable>=4){camGood=true;if(camAutoOn()&&camStable>=8&&!camBusy){setTimeout(()=>{if(camGood&&!camBusy&&camS)$('camShot').onclick();},0);}if(camStable===4){camFocusAt(cx,cy);if(!camVib){camVib=true;buzz();}}return camSet('ok','Good - hold still, tap the round button',sub);}
 camVib=false;camSet('ok','Almost - hold still ...',sub);}
function camDraw(){const o=$('camO'),g=o.getContext('2d'),W=o.width,H=o.height,d=DPR();g.clearRect(0,0,W,H);
 const good=camQ&&camGood;let aligned=false;
 if(camQ){const q=camQ.map(p=>({x:p.x*W,y:p.y*H}));
  g.fillStyle='rgba(0,0,0,.5)';g.beginPath();g.rect(0,0,W,H);g.moveTo(q[0].x,q[0].y);for(let i=3;i>=0;i--)g.lineTo(q[i].x,q[i].y);g.closePath();g.fill('evenodd');   // what is NOT paper is darkened = what the crop throws away
  g.lineWidth=3*d;g.lineJoin='round';g.strokeStyle=good?'#3ddc84':'#ffd166';g.beginPath();g.moveTo(q[0].x,q[0].y);for(let i=1;i<4;i++)g.lineTo(q[i].x,q[i].y);g.closePath();g.stroke();
  g.fillStyle=g.strokeStyle;for(const p of q){g.beginPath();g.arc(p.x,p.y,6*d,0,7);g.fill();}
  const cx=q.reduce((a,p)=>a+p.x,0)/4,cy=q.reduce((a,p)=>a+p.y,0)/4;aligned=Math.hypot(cx-W/2,cy-H/2)<0.07*Math.min(W,H);
  g.strokeStyle=aligned?'#3ddc84':'#ffd166';g.lineWidth=2.5*d;g.beginPath();g.arc(cx,cy,9*d,0,7);g.stroke();}                     // where the middle of the paper is now
 if(camTap&&Date.now()-camTap.t<1100){const x=camTap.x*W,y=camTap.y*H,r=30*d;g.strokeStyle='#fff';g.lineWidth=2.5*d;g.strokeRect(x-r,y-r,2*r,2*r);}}

const camAutoOn=()=>{try{return localStorage.getItem('autocap')==='1';}catch(e){return false;}};
function camAutoUi(){$('camAuto').textContent='Auto capture: '+(camAutoOn()?'on':'off');$('camAuto').style.color=camAutoOn()?'#3ddc84':'';}
$('camAuto').onclick=()=>{try{localStorage.setItem('autocap',camAutoOn()?'0':'1');}catch(e){}camAutoUi();};camAutoUi();

// ---------- pages stay on the phone (reload / lost connection safe) and the app shell is cached: only SENDING needs the computer
const IDBN='if-pages';
const idbOpen=()=>new Promise((res,no)=>{try{const r=indexedDB.open(IDBN,1);r.onupgradeneeded=()=>r.result.createObjectStore('p');r.onsuccess=()=>res(r.result);r.onerror=()=>no(r.error);}catch(e){no(e);}});
async function idbTx(fn){const db=await idbOpen();return new Promise((res,no)=>{const t=db.transaction('p','readwrite'),s=t.objectStore('p');let out;try{out=fn(s);}catch(e){no(e);return;}t.oncomplete=()=>{db.close();res(out&&out.result);};t.onerror=()=>no(t.error);});}
let pchain=Promise.resolve();
function persist(){pchain=pchain.then(async()=>{try{
 const keys=await idbTx(s=>s.getAllKeys()),ids=pages.filter(p=>p.final).map(p=>p.pid=p.pid||('p'+Date.now().toString(36)+Math.random().toString(36).slice(2,6)));
 for(const p of pages){if(!p.final||p.saved)continue;p.blob=p.blob||await p.blobP;
  await idbTx(s=>s.put({blob:p.blob,final:p.final,th:p.th,look:p.look,rot:p.rot,sr:p.sr,quad:p.quad,w:p.w,h:p.h},p.pid));p.saved=true;}
 await idbTx(s=>s.put(ids,'_order'));
 for(const k of keys)if(k!=='_order'&&!ids.includes(k))await idbTx(s=>s.delete(k));}catch(e){}});return pchain;}
(async()=>{try{const o=await idbTx(s=>s.get('_order'));if(!o||!o.length)return;const out=[];
 for(const id of o){const r=await idbTx(s=>s.get(id));if(r)out.push({pid:id,blob:r.blob,blobP:Promise.resolve(r.blob),final:r.final,th:r.th,look:r.look,rot:r.rot,sr:r.sr,quad:r.quad,w:r.w,h:r.h,saved:true,cache:{}});}
 if(out.length&&!pages.length){pages=out;renderList();toast(out.length+' saved page'+(out.length==1?'':'s')+' found on this phone',false,3500);}}catch(e){}})();
if('serviceWorker' in navigator&&window.isSecureContext){navigator.serviceWorker.register(base+'/sw.js').catch(()=>{});}
{const ml=document.createElement('link');ml.rel='manifest';ml.href=base+'/manifest.webmanifest';document.head.appendChild(ml);}

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
  mk('ed',async()=>{cur=p;retake=i;cur.cache={};try{cur.blob=cur.blob||await cur.blobP;cur.base=await decode(cur.blob);}catch(e){toast('Could not open the page.',true);return;}cur.view=turned(cur.base,cur.sr);cur.w=bw(cur.view);cur.h=bh(cur.view);startEdit();});
  mk('x',()=>{pages.splice(i,1);pages.length?renderList():home();},'red');
  d.querySelector('img').onclick=async()=>{if(p.pending)await p.pending;if(!p.final)return;if(p.vurl)URL.revokeObjectURL(p.vurl);p.vurl=URL.createObjectURL(p.final);$('vimg').src=p.vurl;$('viewer').classList.add('on');};
  box.appendChild(d);});
 $('lSend').textContent='Send '+pages.length+' page'+(pages.length==1?'':'s')+' to computer';persist();}
$('viewer').onclick=()=>$('viewer').classList.remove('on');
function home(){show('home');}
$('lHome').onclick=home;$('lAdd').onclick=()=>{retake=-1;openCam();};
$('lSend').onclick=async()=>{if(busy||!pages.length)return;busy=true;$('lSend').disabled=true;
 const batch=Date.now().toString(36)+Math.random().toString(36).slice(2,6),n=pages.length;
 try{for(let i=0;i<n;i++){toast('Sending page '+(i+1)+' of '+n+' ...',false,60000);
   if(pages[i].pending)await pages[i].pending;
   const r=await api('/api/upload?kind=scan&batch='+batch+'&i='+i+'&n='+n+'&name=page'+(i+1)+'.jpg',{method:'POST',body:pages[i].final});
   const j=await r.json();if(!j.ok)throw new Error(j.error);}
  toast('');pages=[];persist();home();buzz();ok();logSent(n+' page'+(n==1?'':'s'));
 }catch(e){toast('Not sent - no connection to the computer. Your pages are safe on this phone; press Send again when connected.',true,6000);}
 busy=false;$('lSend').disabled=false;};
show('home');
</script></body></html>
"""
