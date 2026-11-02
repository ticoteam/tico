"""Document import's conversion, run in a short-lived worker process under three budgets.

The file is parsed only in docs_import_worker.py. This module starts that worker with a time budget and a memory
budget, refuses a conversion at once when the server already runs its maximum, and removes the temporary copy of
the file on every end state (success, failure, time, memory, cancel). The memory budget is the image decoder's
(file_image.py): an address-space limit on Linux, and a resident-memory check from outside on macOS, as
file_metadata.py does.
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TIME_LIMIT = 30.0                 # seconds from start to result, per import
MEMORY_LIMIT = 1024 ** 3          # bytes, the image decoder's limit
MAX_CONCURRENT = 2                # conversions at once per server process
WORKER = Path(__file__).with_name("docs_import_worker.py")

TIME_MESSAGE = "That file took too long to convert. Split it into smaller files and try again."
MEMORY_MESSAGE = "That file is too large to convert on this server. Split it into smaller files and try again."
BUSY_MESSAGE = "Imports are busy right now. Try again in a minute."
TOO_LONG_MESSAGE = "That file is too long to import (over a million characters)"
FAILED_MESSAGE = "That file could not be converted on this server."

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class Busy(Exception):
    """Both conversion slots are in use. The import is refused at once and nothing starts."""


class Cancelled(Exception):
    """The person's request ended while the file was converting; the worker has been stopped."""


def to_markdown(filename, data, *, max_chars, cancel=None):
    """(markdown, title guess) for an uploaded file. ValueError says what is wrong with it; Busy and Cancelled
    say the conversion did not finish. Nothing is written here: the caller writes only a result returned."""
    if not _slots.acquire(blocking=False):
        raise Busy(BUSY_MESSAGE)
    try:
        return _convert(filename, data, max_chars, cancel)
    finally:
        _slots.release()


def _convert(filename, data, max_chars, cancel):
    with tempfile.TemporaryDirectory(prefix="tico-import-") as work:
        root = Path(work)
        (root / "input").write_bytes(data)
        (root / "request.json").write_text(json.dumps(
            {"filename": filename, "max_chars": max_chars, "memory_bytes": MEMORY_LIMIT}))
        child = subprocess.Popen([sys.executable, "-I", str(WORKER), work], cwd=work,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _watch(child, cancel)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
        return _outcome(root / "result.json")


def _watch(child, cancel):
    deadline = time.monotonic() + TIME_LIMIT
    while True:
        try:
            child.wait(timeout=0.05)
            return
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if time.monotonic() >= deadline:
            raise ValueError(TIME_MESSAGE)
        if sys.platform == "darwin" and _resident_kb(child.pid) > MEMORY_LIMIT // 1024:
            raise ValueError(MEMORY_MESSAGE)


def _resident_kb(pid):
    usage = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], check=False, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=1)
    try:
        return int(usage.stdout)
    except ValueError:
        return 0


def _outcome(path):
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError(FAILED_MESSAGE) from None
    status = result.get("status")
    if status == "ok":
        return result["text"], result["guess"]
    if status == "refused":
        raise ValueError(result["message"])
    if status == "too_long":
        raise ValueError(TOO_LONG_MESSAGE)
    if status == "memory":
        raise ValueError(MEMORY_MESSAGE)
    raise ValueError(FAILED_MESSAGE)
