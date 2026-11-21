"""Builds imageFixer3-main/pcprint/ from PrintMerged-main: the SAME print screen (index.html) and the SAME server code,
only the parts that make no sense inside Image Fixer (phone link, internet tunnel, own auto update) are taken out."""
import os, sys, shutil
src = sys.argv[1]
dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pcprint")
os.makedirs(os.path.join(dst, "static"), exist_ok=True)
shutil.copy(os.path.join(src, "printing.py"), os.path.join(dst, "printing.py"))
shutil.copy(os.path.join(src, "static", "index.html"), os.path.join(dst, "static", "index.html"))

s = open(os.path.join(src, "server.py"), encoding="utf-8").read()

def cut(s, a, b, new=""):
    i = s.index(a); j = s.index(b, i)
    return s[:i] + new + s[j:]

def rep(s, a, b):
    assert s.count(a) == 1, a
    return s.replace(a, b)

s = rep(s, "import printing\nimport updatemgr\n", "from . import printing\n")
s = rep(s, "app.register_blueprint(updatemgr.bp)   # auto update routes (/api/update...), PC window only\n", "")
s = rep(s, 'updatemgr.HOOKS["busy"] = lambda: any(j.get("state") == "run" for j in list(JOBS.values()))\nupdatemgr.HOOKS["files"] = lambda: len(FILES)\n', "")
s = rep(s, '''def res(p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, p)''', '''def res(p):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), p)''')
s = rep(s, '''    try:
        tunnel_stop()
    except Exception:
        pass
    if _UP["dir"]:''', '''    if _UP["dir"]:''')
# phone / internet link / PIN: not needed inside Image Fixer (the server only listens on this PC)
new_guard = '''def origin():
    return "pc"       # inside Image Fixer only this PC's own window can reach the server (it listens on 127.0.0.1 only)


def host_ok():
    """Only an IP address or 'localhost' is a valid Host. A malicious web page reaches this PC under ITS OWN domain
    name, so any other name is refused."""
    raw = (request.host or "").strip().lower()
    host = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@app.before_request
def guard():
    if not host_ok():
        return "bad host", 403          # DNS rebinding: a web page cannot reach this program under its own domain name
    if request.remote_addr not in LOCAL:
        return "forbidden", 403


'''
s = cut(s, "PLOCK2 = threading.Lock()", '@app.post("/api/login")', new_guard)
s = cut(s, '@app.post("/api/login")', "# ------------------------------------------------------------------ files",
        '''@app.post("/api/login")
def login():
    return jsonify(ok=True)          # the page still asks once; no PIN inside Image Fixer


''')
# index: same page, only the phone / update buttons are hidden (they belong to the stand-alone PC Print program)
s = rep(s, '''    page = "index.html" if origin() == "pc" else "phone.html"
    r = send_file(res("static/" + page))
    r.headers["Cache-Control"] = "no-store"
    return r''', '''    with open(res("static/index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("</head>", EMBED_HEAD + "</head>", 1)
    r = app.response_class(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r''')
s = rep(s, '@app.get("/")\ndef index():', '''# Inside Image Fixer: no phone link and no PC Print auto update (Image Fixer has its own)
EMBED_HEAD = ("<script>window.EMBED=1</script>"
              "<style>#bph,#phpanel,#upddd{display:none!important}</style>")


@app.get("/")
def index():''')
open(os.path.join(dst, "server.py"), "w", encoding="utf-8").write(s)
open(os.path.join(dst, "__init__.py"), "w").write('"""The PrintMerged / PC Print print screen, running inside Image Fixer (see printweb.py)."""\n')

# index.html: do not start the auto update checks / start-with-windows lookup inside Image Fixer
p = os.path.join(dst, "static", "index.html")
h = open(p, encoding="utf-8").read()
h = rep(h, "(async()=>{initAuto();upLoad();svLoad();", "(async()=>{if(!window.EMBED){initAuto();upLoad()}svLoad();")
open(p, "w", encoding="utf-8").write(h)
print("ok", len(s.splitlines()), "lines in server.py")
