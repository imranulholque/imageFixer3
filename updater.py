"""Auto update: finds a newer build on GitHub, downloads it and swaps the program folder.
No Qt in here (so it can be tested alone). The window part is in updatedlg.py.

How it works
  * The GitHub build (build.yml) publishes every finished build as a Release called  build-<number>  with the file
    ImageFixer-windows.zip, and writes the number + repository into buildinfo.py before packing.
  * The program asks   https://api.github.com/repos/<repo>/releases/latest   and compares the number with its own.
  * Install = download the zip, unpack it next to the temp folder, close the program, a small .bat copies the new
    files over the program folder and starts it again.  Settings are not in that folder, so they stay."""
import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

try:
    import buildinfo
    BUILD = int(buildinfo.BUILD)
    REPO = str(buildinfo.REPO)
except Exception:
    BUILD, REPO = 0, ""

API_BASE = "https://api.github.com"
ASSET = "ImageFixer-windows.zip"
EXE_NAME = "ImageFixer.exe"
TAG_RE = re.compile(r"^build-(\d+)$")
WORK_DIR = os.path.join(tempfile.gettempdir(), "ImageFixerUpdate")
LOG_FILE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ImageFixer", "update.log")


class UpdateError(Exception):
    pass


def log(text):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def install_dir():
    return os.path.dirname(os.path.abspath(sys.executable))


def can_self_update():
    """True only for the packed Windows program that knows its GitHub repository."""
    return sys.platform == "win32" and is_frozen() and bool(REPO) and BUILD > 0


def version_text():
    return "build %d" % BUILD if BUILD else "development copy"


# ------------------------------------------------------------------ checking
def parse_release(data, repo, current):
    """GitHub 'latest release' json -> dict about the update, or None when there is nothing newer to install."""
    m = TAG_RE.match(str(data.get("tag_name", "")))
    if not m or data.get("draft") or data.get("prerelease"):
        return None
    build = int(m.group(1))
    if build <= current:
        return None
    for a in data.get("assets") or []:
        if a.get("name") != ASSET:
            continue
        url = str(a.get("browser_download_url", ""))
        if not url.startswith("https://github.com/%s/releases/download/" % repo):
            raise UpdateError("The update has an unexpected download address.")
        digest = str(a.get("digest") or "")
        return {"build": build, "tag": data["tag_name"], "name": data.get("name") or data["tag_name"],
                "notes": (data.get("body") or "").strip(), "url": url, "size": int(a.get("size") or 0),
                "sha256": digest[7:].lower() if digest.startswith("sha256:") else "",
                "page": data.get("html_url") or "https://github.com/%s/releases" % repo}
    return None                                    # release still being uploaded: ask again next time


def check(repo=None, current=None, timeout=15):
    """Asks GitHub. Returns the update dict, or None = you are up to date. Raises UpdateError when it cannot ask."""
    repo = REPO if repo is None else repo
    current = BUILD if current is None else current
    if not repo:
        raise UpdateError("This copy was not made by the GitHub build, so it does not know where to look for updates.")
    req = urllib.request.Request("%s/repos/%s/releases/latest" % (API_BASE, repo),
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "ImageFixer-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                            # no release published yet
        raise UpdateError("GitHub answered %s %s." % (e.code, e.reason))
    except Exception as e:
        raise UpdateError("Could not reach GitHub (%s)." % (getattr(e, "reason", None) or e))
    return parse_release(data, repo, current)


# --------------------------------------------------------------- downloading
def download(url, dest, size=0, sha256="", progress=None, cancelled=None):
    """Streams url to dest. progress(done, total). cancelled() -> True stops it."""
    req = urllib.request.Request(url, headers={"User-Agent": "ImageFixer-updater"})
    h = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("cancelled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError("Download failed (%s)." % (getattr(e, "reason", None) or e))
    if size and done != size:
        raise UpdateError("The download is incomplete (%d of %d bytes)." % (done, size))
    if sha256 and h.hexdigest() != sha256.lower():
        raise UpdateError("The downloaded file is damaged (checksum does not match).")


def stage(zip_path, stage_dir):
    """Unpacks the zip into stage_dir and returns the folder that holds ImageFixer.exe."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            if z.testzip() is not None:
                raise UpdateError("The downloaded file is damaged.")
            z.extractall(stage_dir)                # zipfile drops '..' and absolute parts itself
    except zipfile.BadZipFile:
        raise UpdateError("The downloaded file is not a valid zip.")
    if os.path.isfile(os.path.join(stage_dir, EXE_NAME)):
        return stage_dir
    for name in os.listdir(stage_dir):
        sub = os.path.join(stage_dir, name)
        if os.path.isfile(os.path.join(sub, EXE_NAME)):
            return sub
    raise UpdateError("The update does not contain %s." % EXE_NAME)


def download_and_stage(info, progress=None, cancelled=None):
    """Whole download step. Returns the staged folder (ready to be copied over the program folder)."""
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    zip_path = os.path.join(WORK_DIR, ASSET)
    try:
        download(info["url"], zip_path, info.get("size", 0), info.get("sha256", ""), progress, cancelled)
        root = stage(zip_path, os.path.join(WORK_DIR, "new"))
    except Exception:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        raise
    try:
        os.remove(zip_path)
    except OSError:
        pass
    return root


def discard():
    shutil.rmtree(WORK_DIR, ignore_errors=True)


# ---------------------------------------------------------------- installing
def _bat_quote(path):
    return path.replace("%", "%%")


def installer_script(stage_root, target_dir, exe_path, pid):
    exe_name = ntpath.basename(exe_path)
    q = _bat_quote
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        'set "LOG=%s"' % q(LOG_FILE),
        'echo %%date%% %%time%%  installer: waiting for the program (pid %d) to close >> "%%LOG%%"' % pid,
        "set n=0",
        ":wait",
        'tasklist /FI "PID eq %d" /FI "IMAGENAME eq %s" /NH 2>NUL | find /I "%s" >NUL' % (pid, exe_name, exe_name),
        "if errorlevel 1 goto copy",
        "set /a n+=1",
        "if %n% GTR 120 goto stuck",
        "timeout /t 1 /nobreak >NUL",
        "goto wait",
        ":copy",
        'robocopy "%s" "%s" /E /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >> "%%LOG%%" 2>&1' % (q(stage_root), q(target_dir)),
        "if errorlevel 8 goto failed",
        'echo %date% %time%  installer: files copied >> "%LOG%"',
        "goto start",
        ":failed",
        'echo %date% %time%  installer: COPY FAILED (is the folder writable?) >> "%LOG%"',
        ":start",
        'start "" "%s"' % q(exe_path),
        'rd /s /q "%s"' % q(WORK_DIR),
        "exit /b 0",
        ":stuck",
        'echo %date% %time%  installer: program did not close, update not installed >> "%LOG%"',
        "exit /b 1",
        "",
    ])


def launch_installer(stage_root):
    """Starts the copy script and returns. The program must exit right after this."""
    exe = os.path.abspath(sys.executable)
    bat = os.path.join(WORK_DIR, "install.bat")
    with open(bat, "w", encoding="utf-8", newline="") as f:
        f.write(installer_script(stage_root, install_dir(), exe, os.getpid()))
    flags = 0x08000000 | 0x00000200      # hidden console + own process group (NOT detached: a detached script opens a window for every command it runs)
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("update to build started; installer launched")


def folder_writable():
    return os.access(install_dir(), os.W_OK)
