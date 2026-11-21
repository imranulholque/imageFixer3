"""Test of the PC Print screen that runs inside Image Fixer (pcprint/): python test_pcprint.py
Starts the page's server, adds a picture and asks for the page, the file list and a thumbnail (no window, no printer)."""
import os
import sys
import tempfile

from PIL import Image

from pcprint import server

OK = True


def check(name, cond):
    global OK
    print(("PASS " if cond else "FAIL ") + name)
    OK = OK and bool(cond)


def main():
    c = server.app.test_client()
    r = c.get("/", base_url="http://127.0.0.1:53187")
    html = r.get_data(as_text=True)
    check("page opens", r.status_code == 200 and "id=\"bprint\"" in html)
    check("page knows it is inside Image Fixer", "window.EMBED=1" in html)
    check("page numbers + profile controls are there", "id=\"pnumon\"" in html and "id=\"profile\"" in html and "id=\"bprof\"" in html)
    check("a phone address is refused", c.get("/api/files", base_url="http://127.0.0.1:53187",
                                              environ_overrides={"REMOTE_ADDR": "192.168.1.5"}).status_code == 403)
    check("a web page name is refused", c.get("/api/files", base_url="http://evil.example.com").status_code == 403)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "a.png")
    Image.new("RGB", (300, 400), (200, 30, 30)).save(p)
    added, errors = server.register([p], sort=False)
    check("a picture is added", added == 1 and not errors)
    r = c.get("/api/state", base_url="http://127.0.0.1:53187")
    files = r.get_json().get("files", [])
    check("file list has it", len(files) == 1 and files[0]["name"] == "a.png")
    r = c.get("/api/settings", base_url="http://127.0.0.1:53187")
    check("settings answer", r.status_code == 200)
    r = c.get("/api/profiles?printer=x", base_url="http://127.0.0.1:53187")
    check("profile list answers", r.status_code == 200 and r.get_json() == [])
    with server.SLOCK:
        for fid in list(server.FILES):
            server.drop_file(fid)
        server.ORDER.clear()
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
