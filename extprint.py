"""Open pictures in the print program the user chose (Settings > Print program), and let Image Fixer sleep while
that program is used: the window goes to the tray, the memory it can spare is given back to Windows and the process
runs in the economy mode.  Nothing of the user's work is deleted: it all comes back when the window is opened again."""
import gc
import os
import subprocess

_DETACHED = 0x00000008           # DETACHED_PROCESS
_NEW_GROUP = 0x00000200          # CREATE_NEW_PROCESS_GROUP
_NORMAL_PRIORITY = 0x00000020    # the print program must not inherit our low priority
_BELOW_NORMAL = 0x00004000
_CMD_LIMIT = 30000               # Windows refuses a command line longer than 32767 characters


def program_name(path):
    """'C:\\Program Files\\Foo\\Foo.exe' -> 'Foo'."""
    return os.path.splitext(os.path.basename(path or ""))[0]


def _batches(exe, files):
    """The files in as few command lines as possible (one program start normally)."""
    batch, size = [], len(exe) + 4
    for f in files:
        add = len(f) + 3
        if batch and size + add > _CMD_LIMIT:
            yield batch
            batch, size = [], len(exe) + 4
        batch.append(f)
        size += add
    if batch:
        yield batch


def launch(exe, files):
    """Starts the program with the picture files (full resolution, edits included).  Returns None, or a message."""
    files = [f for f in files if f and os.path.isfile(f)]
    if not exe or not os.path.isfile(exe):
        return "The print program was not found. Choose it again in Settings > Print program."
    if not files:
        return "There is no picture file to open."
    flags = (_DETACHED | _NEW_GROUP | _NORMAL_PRIORITY) if os.name == "nt" else 0
    try:
        for part in _batches(exe, files):
            subprocess.Popen([exe] + part, cwd=os.path.dirname(exe) or None, close_fds=True, creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as ex:
        return "Could not start %s: %s" % (program_name(exe) or exe, ex)
    return None


# ------------------------------------------------------------------ sleeping (Windows; harmless elsewhere)
def trim_memory():
    """Gives back the memory that is not needed right now.  Windows keeps it in the page file and reads it back the
    moment the program uses it again, so nothing is lost."""
    gc.collect()
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            h = k32.GetCurrentProcess()
            try:
                ctypes.windll.psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
                ctypes.windll.psapi.EmptyWorkingSet(h)
            except Exception:
                k32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
                k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
        except Exception:
            pass
    else:
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


def economy_mode(on):
    """on: lower priority + Windows 'efficiency mode' (less power, runs on the efficient cores).  off: back to normal."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess()
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass(h, _BELOW_NORMAL if on else _NORMAL_PRIORITY)

        class _Throttle(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        st = _Throttle(1, 1, 1 if on else 0)               # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))      # 4 = ProcessPowerThrottling
    except Exception:
        pass
