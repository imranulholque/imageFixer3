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

from PIL import Image
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

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
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
        data = self._body()
        if data is None:
            return
        try:
            if route == "/api/detect":
                im = Image.open(io.BytesIO(data))
                im.load()
                quad, found = scanner.detect_document(im)
                self._json({"ok": True, "w": im.width, "h": im.height, "found": found,
                            "quad": [[x, y] for x, y in quad]})
            elif route == "/api/warp":
                vals = [float(v) for v in q.get("q", [""])[0].split(",")]
                if len(vals) != 8:
                    raise ValueError("4 corners needed")
                im = Image.open(io.BytesIO(data)).convert("RGB")
                quad = [(vals[i], vals[i + 1]) for i in range(0, 8, 2)]
                out = scanner.straighten(im, quad)
                out = scanner.enhance(out, q.get("look", ["orig"])[0])           # orig | clean | gray | bw
                turns = int(q.get("rot", ["0"])[0]) % 4
                if turns:
                    out = out.rotate(-90 * turns, expand=True)                      # clockwise quarter turns
                buf = io.BytesIO()
                out.save(buf, "JPEG", quality=93, subsampling=0)
                self._send(200, buf.getvalue(), "image/jpeg")
            elif route == "/api/upload":
                self._upload(data, q)
            else:
                self._send(404, "")
        except Exception as ex:
            self._json({"ok": False, "error": str(ex)[:200]}, 400)

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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0e1116">
<title>Image Fixer</title>
<style>
:root{--bg:#0e1116;--card:#171b24;--card2:#1e2330;--line:#2a3144;--txt:#e8eaed;--mut:#8d95a8;--acc:#6366f1;--acc2:#a855f7;--ok:#30a46c;--bad:#e5484d}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
body{min-height:100vh;display:flex;flex-direction:column;padding-bottom:env(safe-area-inset-bottom)}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:calc(12px + env(safe-area-inset-top)) 16px 12px;background:#0e1116ee;backdrop-filter:blur(8px);border-bottom:1px solid #1c2130}
.logo{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,var(--acc),var(--acc2));display:grid;place-items:center;font-weight:800;font-size:15px}
header b{font-size:17px;display:block}header small{color:var(--mut);font-size:12px}
header .t{flex:1}
#dot{width:11px;height:11px;border-radius:50%;background:var(--bad);box-shadow:0 0 0 4px #e5484d22}#dot.on{background:var(--ok);box-shadow:0 0 0 4px #30a46c22}
main{flex:1;padding:16px;display:flex;flex-direction:column;gap:14px;max-width:760px;width:100%;margin:0 auto}
button{font:inherit;color:inherit}
.card{display:flex;align-items:center;gap:14px;text-align:left;width:100%;padding:18px;border-radius:20px;background:var(--card);border:1px solid var(--line);min-height:84px}
.card:active{transform:scale(.985)}
.card .ic{flex:none;width:52px;height:52px;border-radius:16px;display:grid;place-items:center;background:var(--card2);font-size:26px}
.card b{display:block;font-size:19px}.card small{color:var(--mut);font-size:13.5px;line-height:1.3}
.card.main{background:linear-gradient(120deg,var(--acc),var(--acc2));border:0;min-height:110px}
.card.main .ic{background:#ffffff26}.card.main small{color:#fff;opacity:.88}
.btn{border:1px solid var(--line);border-radius:14px;padding:14px 16px;background:var(--card2);font-size:16px;min-height:50px}
.btn.pri{background:linear-gradient(90deg,var(--acc),var(--acc2));border:0;color:#fff;font-weight:650}
.btn.red{background:#3a1b22;border-color:#5a2630;color:#ffb4bd}
.btn:disabled{opacity:.5}
.row{display:flex;gap:10px;align-items:center}.row>.btn{flex:1}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:9px 14px;font-size:14.5px;min-height:40px}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
.hide{display:none!important}
.hint{color:var(--mut);font-size:13.5px;line-height:1.35}
#msg{min-height:20px;color:var(--mut);text-align:center;font-size:14px}
canvas{width:100%;touch-action:none;border-radius:14px;background:#000;display:block}
#bar{height:6px;background:#222836;border-radius:3px;overflow:hidden}#bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .2s}
.pages{display:flex;flex-direction:column;gap:10px}
.pg{display:flex;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:8px}
.pg img{width:64px;height:84px;object-fit:cover;border-radius:10px;background:#000}
.pg .n{flex:1;font-weight:600;min-width:0}.pg .n small{display:block;color:var(--mut);font-weight:400}
.pg button{padding:0;width:42px;height:42px;font-size:16px;border-radius:12px}
.rev{width:100%;border-radius:14px;background:#000;display:block}
.sw{display:flex;align-items:center;gap:10px;color:var(--mut);font-size:14px}
.sw input{width:20px;height:20px;accent-color:var(--acc)}
#viewer{position:fixed;inset:0;background:#000e;display:flex;align-items:center;justify-content:center;padding:12px;z-index:20}
#viewer img{max-width:100%;max-height:100%;border-radius:8px}
#banner{position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:15;background:#3a1b22;border:1px solid #5a2630;color:#ffb4bd;padding:12px 14px;border-radius:14px;font-size:14px;text-align:center}
.sent{display:flex;flex-direction:column;gap:6px}.sent div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;font-size:14px;color:var(--mut)}.sent b{color:var(--ok)}
</style></head><body>
<header><div class="logo">IF</div><div class="t"><b>Image Fixer</b><small id="st">connecting...</small></div><span id="dot"></span></header>
<main>
 <section id="home" style="display:contents">
  <button class="card main" id="bScan"><span class="ic">&#128247;</span><span><b>Scan a paper</b><small>Take a photo. The page is found, cut out and straightened for you.</small></span></button>
  <button class="card" id="bChoose"><span class="ic">&#128193;</span><span><b>Send pictures</b><small>Choose photos from this phone. They arrive as they are.</small></span></button>
  <button class="card" id="bGal"><span class="ic">&#9986;</span><span><b>Scan a saved photo</b><small>Pick a photo of a page and cut the page out.</small></span></button>
  <div class="sent" id="sent"></div>
 </section>
 <section id="edit" class="hide" style="display:none;flex-direction:column;gap:12px">
  <div class="hint" id="tip"></div>
  <div class="chips"><button class="chip" id="eAuto">&#10024; Find paper</button><button class="chip" id="eFull">Whole photo</button><button class="chip" id="eRotL">&#10226; Turn</button><button class="chip" id="eRotR">Turn &#10227;</button></div>
  <canvas id="cv"></canvas>
  <div class="row"><button class="btn" id="eRetake">Retake</button><button class="btn pri" id="eOk">Cut out</button></div>
 </section>
 <section id="review" class="hide" style="display:none;flex-direction:column;gap:12px">
  <img id="rimg" class="rev">
  <div class="chips" id="looks"><button class="chip" data-l="orig">Original</button><button class="chip on" data-l="clean">Clean</button><button class="chip" data-l="gray">Gray</button><button class="chip" data-l="bw">Black &amp; white</button></div>
  <div class="row"><button class="btn" id="rRotL">&#10226; Turn</button><button class="btn" id="rRotR">Turn &#10227;</button><button class="btn" id="rAdj">Corners</button></div>
  <button class="btn pri" id="rUse">Use this page</button>
 </section>
 <section id="list" class="hide" style="display:none;flex-direction:column;gap:12px">
  <div class="row"><b id="lt" style="flex:2;font-size:19px"></b><button class="btn" id="lHome" style="flex:1">Home</button></div>
  <div class="pages" id="pages"></div>
  <label class="sw"><input type="checkbox" id="auto"> Open the camera again after each page</label>
  <button class="btn" id="lAdd">+ Add another page</button>
  <button class="btn pri" id="lSend">Send to computer</button>
 </section>
 <div id="bar" class="hide"><i></i></div>
 <div id="msg"></div>
</main>
<div id="banner" class="hide">Not connected to the computer. Scan the QR code on the computer again.</div>
<input type="file" id="fFiles" accept="image/*" multiple class="hide">
<input type="file" id="fCam" accept="image/*" capture="environment" class="hide">
<input type="file" id="fGal" accept="image/*" class="hide">
<div id="viewer" class="hide"><img id="vimg"></div>
<script>
const $=id=>document.getElementById(id), base=location.pathname.replace(/\/$/,'');
let pages=[], retake=-1, cur=null, busy=false, sentLog=[];
const api=(p,opt)=>fetch(base+p,opt);
const DPR=()=>window.devicePixelRatio||1;
function show(id){for(const s of ['home','edit','review','list']){const e=$(s);const on=s===id;e.classList.toggle('hide',!on);
  e.style.display=on?(s==='home'?'contents':'flex'):'none';}window.scrollTo(0,0);}
function msg(t,err){$('msg').textContent=t||'';$('msg').style.color=err?'#ffb4bd':'';}
function prog(f){$('bar').classList.toggle('hide',f==null);if(f!=null)$('bar').firstChild.style.width=(f*100)+'%';}
function buzz(){try{navigator.vibrate&&navigator.vibrate(18);}catch(e){}}
let fails=0;
async function ping(){try{const r=await api('/api/ping');if(!r.ok)throw 0;fails=0;$('dot').className='on';$('st').textContent='connected to your computer';$('banner').classList.add('hide');}
 catch(e){if(++fails>=2){$('dot').className='';$('st').textContent='not connected';$('banner').classList.remove('hide');}}}
setInterval(ping,3000);ping();
function loadImg(src){return new Promise((ok,no)=>{const i=new Image();i.onload=()=>ok(i);i.onerror=no;i.src=src;});}
async function shrink(file,maxSide){ // photo -> upright JPEG blob (EXIF turn applied by the browser), at most maxSide
 const u=URL.createObjectURL(file);const im=await loadImg(u);
 const w=im.naturalWidth,h=im.naturalHeight,s=Math.min(1,maxSide/Math.max(w,h));
 const c=document.createElement('canvas');c.width=Math.round(w*s);c.height=Math.round(h*s);
 const g=c.getContext('2d');g.imageSmoothingQuality='high';g.drawImage(im,0,0,c.width,c.height);URL.revokeObjectURL(u);
 return new Promise(r=>c.toBlob(r,'image/jpeg',0.94));}
async function rotateBlob(blob,turns){ // clockwise quarter turns
 const u=URL.createObjectURL(blob);const im=await loadImg(u);const t=((turns%4)+4)%4;
 const c=document.createElement('canvas');c.width=t%2?im.naturalHeight:im.naturalWidth;c.height=t%2?im.naturalWidth:im.naturalHeight;
 const g=c.getContext('2d');g.translate(c.width/2,c.height/2);g.rotate(t*Math.PI/2);g.drawImage(im,-im.naturalWidth/2,-im.naturalHeight/2);URL.revokeObjectURL(u);
 return new Promise(r=>c.toBlob(r,'image/jpeg',0.94));}
function rotQuad(q,w,h,cw){ // image w x h turned a quarter: clockwise (x,y)->(h-y,x), counter-clockwise (x,y)->(y,w-x)
 return q.map(p=>cw?{x:h-p.y,y:p.x}:{x:p.y,y:w-p.x});}

// ---------- send pictures as they are
$('bChoose').onclick=()=>{$('fFiles').value='';$('fFiles').click();};
$('fFiles').onchange=async e=>{
 const fs=[...e.target.files];if(!fs.length)return;
 const batch=Date.now().toString(36)+Math.random().toString(36).slice(2,6);let ok=0;
 for(let i=0;i<fs.length;i++){msg('Sending '+(i+1)+' of '+fs.length+' ...');prog(i/fs.length);
  try{const r=await api('/api/upload?kind=file&batch='+batch+'&i='+i+'&n='+fs.length+'&name='+encodeURIComponent(fs[i].name),{method:'POST',body:fs[i]});
   const j=await r.json();if(j.ok)ok++;else msg(fs[i].name+': '+j.error,true);}
  catch(err){msg('Connection lost. Scan the QR code on the computer again.',true);prog(null);return;}}
 prog(null);buzz();logSent(ok+' picture'+(ok==1?'':'s'));msg('');};
function logSent(t){const d=new Date();sentLog.unshift('<b>&#10003;</b> '+t+' sent &middot; '+d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
 sentLog=sentLog.slice(0,4);$('sent').innerHTML=sentLog.map(x=>'<div>'+x+'</div>').join('');}

// ---------- scan
function openCam(){$('fCam').value='';$('fCam').click();}
$('bScan').onclick=()=>{retake=-1;openCam();};
$('bGal').onclick=()=>{retake=-1;$('fGal').value='';$('fGal').click();};
$('fCam').onchange=e=>gotPhoto(e.target.files[0]);$('fGal').onchange=e=>gotPhoto(e.target.files[0]);
async function detect(blob){const r=await api('/api/detect',{method:'POST',body:blob});const j=await r.json();if(!j.ok)throw new Error(j.error);return j;}
async function gotPhoto(f){if(!f)return;msg('Finding the paper ...');prog(0.35);
 try{const blob=await shrink(f,3000);const j=await detect(blob);
  cur={src:blob,w:j.w,h:j.h,quad:j.quad.map(p=>({x:p[0],y:p[1]})),img:await loadImg(URL.createObjectURL(blob)),look:'clean',rot:0};
  setTip(j.found);prog(null);msg('');startEdit();
 }catch(err){prog(null);msg('Could not reach the computer. Scan the QR code again.',true);}}
function setTip(found){$('tip').textContent=found?'Paper found. Drag a round corner or a square side if the outline is not exact.':'Paper not found. Drag the corners onto the paper, or tap "Whole photo".';}
$('eAuto').onclick=async()=>{msg('Finding the paper ...');prog(0.35);try{const j=await detect(cur.src);cur.quad=j.quad.map(p=>({x:p[0],y:p[1]}));setTip(j.found);prog(null);msg('');draw();}catch(e){prog(null);msg('Failed. Try again.',true);}};
$('eFull').onclick=()=>{const m=0.01,w=cur.w,h=cur.h;cur.quad=[{x:w*m,y:h*m},{x:w*(1-m),y:h*m},{x:w*(1-m),y:h*(1-m)},{x:w*m,y:h*(1-m)}];draw();};
async function turnSrc(cw){msg('');prog(0.4);cur.src=await rotateBlob(cur.src,cw?1:3);cur.quad=rotQuad(cur.quad,cur.w,cur.h,cw);const w=cur.w;cur.w=cur.h;cur.h=w;
 cur.img=await loadImg(URL.createObjectURL(cur.src));prog(null);startEdit();}
$('eRotL').onclick=()=>turnSrc(false);$('eRotR').onclick=()=>turnSrc(true);

const cv=$('cv'),cx=cv.getContext('2d');let drag=null,scale=1;
function startEdit(){show('edit');const W=Math.min(innerWidth-32,740),H=Math.min(W*cur.h/cur.w,innerHeight*0.62),Wd=H*cur.w/cur.h,d=DPR();
 cv.style.width=Wd+'px';cv.style.height=H+'px';cv.width=Math.round(Wd*d);cv.height=Math.round(H*d);scale=cv.width/cur.w;draw();}
function mids(q){return q.map((p,i)=>({x:(p.x+q[(i+1)%4].x)/2,y:(p.y+q[(i+1)%4].y)/2}));}
function draw(){const d=DPR();cx.clearRect(0,0,cv.width,cv.height);cx.imageSmoothingQuality='high';cx.drawImage(cur.img,0,0,cv.width,cv.height);
 const q=cur.quad.map(p=>({x:p.x*scale,y:p.y*scale})),m=mids(q);
 cx.save();cx.beginPath();cx.rect(0,0,cv.width,cv.height);cx.moveTo(q[0].x,q[0].y);for(let i=3;i>=0;i--)cx.lineTo(q[i].x,q[i].y);cx.closePath();cx.fillStyle='rgba(0,0,0,.5)';cx.fill('evenodd');cx.restore();
 cx.beginPath();q.forEach((p,i)=>i?cx.lineTo(p.x,p.y):cx.moveTo(p.x,p.y));cx.closePath();cx.lineWidth=2.4*d;cx.strokeStyle='#a5b4fc';cx.stroke();
 m.forEach((p,i)=>{const s=9*d;cx.fillStyle='rgba(255,255,255,.92)';cx.strokeStyle='#4f46e5';cx.lineWidth=2.5*d;cx.beginPath();cx.rect(p.x-s,p.y-s,2*s,2*s);cx.fill();cx.stroke();});
 q.forEach(p=>{cx.beginPath();cx.arc(p.x,p.y,13*d,0,7);cx.fillStyle='rgba(255,255,255,.95)';cx.fill();cx.lineWidth=3*d;cx.strokeStyle='#4f46e5';cx.stroke();});
 if(drag){const p=drag.kind==='c'?q[drag.i]:m[drag.i],M=64*d,z=3,mx=Math.min(Math.max(p.x,M),cv.width-M),my=p.y>M*2.6?p.y-M*1.7:p.y+M*1.7;
  cx.save();cx.beginPath();cx.arc(mx,my,M,0,7);cx.clip();cx.fillStyle='#000';cx.fillRect(mx-M,my-M,2*M,2*M);
  const sw=2*M/z/scale;cx.drawImage(cur.img,p.x/scale-sw/2,p.y/scale-sw/2,sw,sw,mx-M,my-M,2*M,2*M);cx.restore();
  cx.beginPath();cx.arc(mx,my,M,0,7);cx.lineWidth=3*d;cx.strokeStyle='#fff';cx.stroke();
  cx.beginPath();cx.moveTo(mx-10*d,my);cx.lineTo(mx+10*d,my);cx.moveTo(mx,my-10*d);cx.lineTo(mx,my+10*d);cx.strokeStyle='#f43f5e';cx.lineWidth=2*d;cx.stroke();}}
function pos(e){const r=cv.getBoundingClientRect(),k=cv.width/r.width;return{x:(e.clientX-r.left)*k,y:(e.clientY-r.top)*k};}
cv.onpointerdown=e=>{const p=pos(e),d=DPR(),q=cur.quad.map(a=>({x:a.x*scale,y:a.y*scale})),m=mids(q);let best=null,bd=1e9;
 q.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y)-4*d;if(t<bd){bd=t;best={kind:'c',i};}});
 m.forEach((c,i)=>{const t=Math.hypot(c.x-p.x,c.y-p.y);if(t<bd){bd=t;best={kind:'m',i};}});
 if(best&&bd<60*d){drag=best;drag.start=cur.quad.map(a=>({x:a.x,y:a.y}));drag.p0=p;cv.setPointerCapture(e.pointerId);buzz();draw();}};
cv.onpointermove=e=>{if(!drag)return;const p=pos(e),dx=(p.x-drag.p0.x)/scale,dy=(p.y-drag.p0.y)/scale,S=drag.start;
 const cl=(v,m)=>Math.max(0,Math.min(m,v));
 if(drag.kind==='c'){cur.quad[drag.i]={x:cl(S[drag.i].x+dx,cur.w),y:cl(S[drag.i].y+dy,cur.h)};}
 else{const a=S[drag.i],b=S[(drag.i+1)%4],ux=b.x-a.x,uy=b.y-a.y,L=Math.hypot(ux,uy)||1,nx=-uy/L,ny=ux/L,t=dx*nx+dy*ny; // move a side along its normal
  [drag.i,(drag.i+1)%4].forEach(k=>{cur.quad[k]={x:cl(S[k].x+nx*t,cur.w),y:cl(S[k].y+ny*t,cur.h)};});}
 draw();};
cv.onpointerup=cv.onpointercancel=()=>{drag=null;draw();};
$('eRetake').onclick=()=>openCam();
function warpUrl(){const q=cur.quad.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)).join(',');return '/api/warp?q='+q+'&look='+cur.look+'&rot='+cur.rot;}
async function crop(){msg('Cutting out the page ...');prog(0.5);
 try{const r=await api(warpUrl(),{method:'POST',body:cur.src});if(!r.ok)throw 0;
  if(cur.url)URL.revokeObjectURL(cur.url);cur.result=await r.blob();cur.url=URL.createObjectURL(cur.result);$('rimg').src=cur.url;
  for(const b of document.querySelectorAll('#looks .chip'))b.classList.toggle('on',b.dataset.l===cur.look);
  prog(null);msg('');show('review');}
 catch(e){prog(null);msg('Could not cut out the page. Try again.',true);}}
$('eOk').onclick=crop;
for(const b of document.querySelectorAll('#looks .chip'))b.onclick=()=>{if(cur.look===b.dataset.l)return;cur.look=b.dataset.l;crop();};
$('rRotL').onclick=()=>{cur.rot=(cur.rot+3)%4;crop();};$('rRotR').onclick=()=>{cur.rot=(cur.rot+1)%4;crop();};
$('rAdj').onclick=()=>startEdit();
$('auto').checked=localStorage.getItem('auto')==='1';$('auto').onchange=()=>localStorage.setItem('auto',$('auto').checked?'1':'0');
$('rUse').onclick=()=>{buzz();if(retake>=0)pages[retake]=cur;else pages.push(cur);const again=retake<0&&$('auto').checked;retake=-1;cur=null;renderList();if(again)openCam();};

// ---------- page list
function renderList(){show('list');$('lt').textContent=pages.length+' page'+(pages.length==1?'':'s');const box=$('pages');box.innerHTML='';
 pages.forEach((p,i)=>{const d=document.createElement('div');d.className='pg';
  d.innerHTML='<img src="'+p.url+'"><div class="n">Page '+(i+1)+'<small>'+({orig:'Original',clean:'Clean',gray:'Gray',bw:'Black & white'}[p.look]||'')+'</small></div>';
  const mk=(t,fn,c)=>{const b=document.createElement('button');b.className='btn '+(c||'');b.textContent=t;b.onclick=ev=>{ev.stopPropagation();fn();};d.appendChild(b);};
  mk('\u25B2',()=>{if(i>0){[pages[i-1],pages[i]]=[pages[i],pages[i-1]];renderList();}});
  mk('\u25BC',()=>{if(i<pages.length-1){[pages[i+1],pages[i]]=[pages[i],pages[i+1]];renderList();}});
  mk('\u270E',()=>{cur=p;retake=i;startEdit();});
  mk('\u2715',()=>{pages.splice(i,1);pages.length?renderList():home();},'red');
  d.querySelector('img').onclick=()=>{$('vimg').src=p.url;$('viewer').classList.remove('hide');};
  box.appendChild(d);});
 $('lSend').textContent='Send '+pages.length+' page'+(pages.length==1?'':'s')+' to computer';msg('');}
$('viewer').onclick=()=>$('viewer').classList.add('hide');
function home(){show('home');msg('');}
$('lHome').onclick=home;$('lAdd').onclick=()=>{retake=-1;openCam();};
$('lSend').onclick=async()=>{if(busy||!pages.length)return;busy=true;$('lSend').disabled=true;
 const batch=Date.now().toString(36)+Math.random().toString(36).slice(2,6),n=pages.length;
 try{for(let i=0;i<n;i++){msg('Sending page '+(i+1)+' of '+n+' ...');prog(i/n);
   const r=await api('/api/upload?kind=scan&batch='+batch+'&i='+i+'&n='+n+'&name=page'+(i+1)+'.jpg',{method:'POST',body:pages[i].result});
   const j=await r.json();if(!j.ok)throw new Error(j.error);}
  prog(null);pages=[];home();buzz();logSent(n+' page'+(n==1?'':'s'));
 }catch(e){prog(null);msg('Sending failed. Check the Wi-Fi and try again (scan the QR code again if needed).',true);}
 busy=false;$('lSend').disabled=false;};
show('home');
</script></body></html>"""
