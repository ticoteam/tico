"""Document import's conversion, run in a short-lived worker process under three budgets.

The file is parsed only in docs_import_worker.py. This module starts that worker with a time budget and a memory
budget, refuses a conversion at once when the server already runs its maximum, and removes the temporary copy of
the file on every end state (success, failure, time, memory, cancel). The memory budget is the image decoder's
(file_image.py): an address-space limit on Linux, and a resident-memory check from outside on macOS, as
file_metadata.py does.
"""
import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("tico.docs_import")

TIME_LIMIT = 30.0                 # seconds from start to result, per import
MEMORY_LIMIT = 1024 ** 3          # bytes, the image decoder's limit
MAX_CONCURRENT = 2                # conversions at once per server: the server runs one process, so this bounds it
WORKER = Path(__file__).with_name("docs_import_worker.py")

TIME_MESSAGE = "That file took too long to convert. Split it into smaller files and try again."
MEMORY_MESSAGE = "That file is too large to convert on this server. Split it into smaller files and try again."
BUSY_MESSAGE = "Imports are busy right now. Try again in a minute."
TOO_LONG_MESSAGE = "That file is too long to import (over a million characters)"
FAILED_MESSAGE = "That file could not be converted on this server."

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
CRASH_LINE_LIMIT = 300            # characters of a crashed worker's last stderr line that reach the log


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
        stderr_path = root / "stderr.log"
        with open(stderr_path, "wb") as errors:
            child = subprocess.Popen([sys.executable, "-I", str(WORKER), work], cwd=work,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                _watch(child, cancel)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
        return _outcome(root / "result.json", stderr_path)


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
    # An unmeasurable size counts as zero: the time limit still bounds the worker, and a failed probe
    # is not a memory refusal.
    try:
        usage = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], check=False, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=1)
        return int(usage.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _outcome(path, stderr_path):
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        _log_crash(stderr_path)
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


def _log_crash(stderr_path):
    """Log the last line of a worker's stderr when it ended without a result. The person sees only the plain failure."""
    try:
        tail = stderr_path.read_bytes()[-4096:].decode("utf-8", "replace")
        lines = [line for line in tail.splitlines() if line.strip()]
    except OSError:
        lines = []
    last = lines[-1][:CRASH_LINE_LIMIT] if lines else "(no output)"
    log.warning("document import worker ended without a result: %s", last)
