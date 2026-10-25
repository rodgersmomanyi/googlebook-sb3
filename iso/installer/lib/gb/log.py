"""Installer log, @@GB markers and the plain-text console (5.1, 5.2). No process code.

Log lines: 'YYYY-mm-ddTHH:MM:SSZ <LEVEL> <text>' in /run/gb/log/installer.log. Markers are logged always and printed
on the UI console only when it is ttyS0 (/run/gb/markers exists). Never log volume labels, serials or user data:
callers pass such text with screen_only=True.
"""
import os
import time

from .util import utc_iso, marker_value

LOG_FILE = "/run/gb/log/installer.log"
MARKERS_FLAG = "/run/gb/markers"

_state = {"markers": None, "verbose": False, "fh": None, "last_progress": 0.0, "out": 1, "in": 0}


def configure(markers=None, verbose=False):
    _state["markers"] = markers
    _state["verbose"] = verbose


def markers_on():
    if _state["markers"] is None:
        _state["markers"] = os.path.exists(MARKERS_FLAG)
    return _state["markers"]


def _fh():
    fh = _state["fh"]
    if fh is None:
        if not os.path.isdir(os.path.dirname(LOG_FILE)):
            return None
        try:
            fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
        except OSError:
            return None
        _state["fh"] = fh
    return fh


def _write(level, text):
    fh = _fh()
    if fh is None:
        return
    ts = utc_iso()
    try:
        for ln in str(text).split("\n"):
            fh.write("%s %s %s\n" % (ts, level, ln))
        fh.flush()
    except OSError:
        _state["fh"] = None


def debug(text):
    if _state["verbose"]:
        _write("DEBUG", text)


def info(text):
    _write("INFO", text)


def warn(text):
    _write("WARN", text)


def error(text):
    _write("ERROR", text)


def close():
    fh = _state["fh"]
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass
    _state["fh"] = None


# ------------------------------------------------------------------------------------------------ console
def _out(s):
    data = s.encode("ascii", "replace")
    fd = _state["out"]
    while data:
        try:
            n = os.write(fd, data)
        except InterruptedError:
            continue
        except OSError:
            return
        data = data[n:]


def say(text="", screen_only=False):
    """A line on the UI console (and in the log unless screen_only)."""
    _out(text + "\n")
    if not screen_only:
        info("screen: " + text)


def say_lines(lines, screen_only=False):
    for ln in lines:
        say(ln, screen_only)


def marker(tag, *pairs):
    """@@GB <TAG> k=v ...; pairs are (key, value) tuples in order."""
    line = "@@GB " + tag + "".join(" %s=%s" % (k, marker_value(v)) for k, v in pairs)
    info("marker: " + line)
    if markers_on():
        _out(line + "\n")
    return line


def progress(text, force=False, every=5.0):
    """Progress output: at most one line per `every` seconds unless forced (one per chunk)."""
    now = time.time()
    if force or now - _state["last_progress"] >= every:
        _state["last_progress"] = now
        say(text)


# ------------------------------------------------------------------------------------------------ input
def _flush_input():
    try:
        import termios
        termios.tcflush(_state["in"], termios.TCIFLUSH)
    except Exception:
        pass


def read_line():
    """One line from the console (canonical mode). Raises EOFError on hang-up/EOF."""
    buf = b""
    while True:
        try:
            d = os.read(_state["in"], 1024)
        except InterruptedError:
            continue
        if not d:
            raise EOFError("console closed")
        buf += d
        if b"\n" in buf:
            line = buf.split(b"\n", 1)[0]
            return line.decode("utf-8", "replace").rstrip("\r")


def prompt(pid, text):
    """Show a prompt (5.3) and return the reply. Input is flushed right before the @@GB PROMPT line."""
    if markers_on():
        _out(text + "\n")
        _flush_input()
        marker("PROMPT", ("id", pid))
    else:
        _flush_input()
        info("marker: @@GB PROMPT id=%s" % pid)
        _out(text + " ")
    reply = read_line()
    info("reply to %s: %r" % (pid, reply))
    return reply.strip()
