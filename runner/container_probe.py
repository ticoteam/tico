"""Whether a container actually starts on this computer, for readiness (backend/health.py warns).

Docker can answer `docker info` while every `docker run` hangs (a stalled disk under Docker Desktop's VM),
and then a bot's container work waits forever with nothing in Health. So, where a `docker` CLI is on PATH
and its daemon answers, the runner starts a throwaway container from an image already on the computer,
in the background, with a time limit. It never pulls: it starts a listed local image by its full ID, which
fails at once if the image has gone since, so it needs no `--pull` flag (Docker before 20.10 does not know it).

Probes are rare (Docker Desktop's Resource Saver lets its VM sleep between them). A failure is checked
again at once, and only a second failure in a row is reported, so one slow start on a waking VM is not
a warning. A daemon that is simply not running is not a stall: nothing is reported then.
"""
import datetime
import os
import shutil
import subprocess
import threading
import time
import uuid

EVERY_S = 900           # a probe costs a container start and wakes a sleeping Docker Desktop VM
FAILING_EVERY_S = 300   # while failing, look sooner so Health clears soon after Docker recovers
LIMIT_S = 20            # a cached image starts in well under this on a healthy computer
ANSWER_S = 10           # listing images or containers
IMAGE_ENV = "TICO_RUNNER_PROBE_IMAGE"
LABEL = "tico.probe=1"
# The test suite sets this (conftest.py): a readiness call there must not start real containers.
# Read once at import, so a test that clears the environment does not turn probes back on.
OFF = os.environ.get("TICO_RUNNER_CONTAINER_PROBE") == "off"
# Images likely to have `true`, best first; any other local image is the fallback.
PREFERRED = ("ghcr.io/ticoteam/tico", "tico-runner", "busybox", "alpine", "debian", "ubuntu", "python")


def _run(args, limit):
    return subprocess.run(args, capture_output=True, text=True, timeout=limit, stdin=subprocess.DEVNULL)


def _remove(docker, *names):
    if names:
        _run([docker, "rm", "-fv", *names], LIMIT_S)


def _image(docker):
    """The full ID of a local image to start, or None when there is none. Raises TimeoutExpired when the
    daemon hangs. A name could be pulled if the image went away before the run; an ID cannot."""
    listed = _run([docker, "images", "--no-trunc", "--format", "{{.ID}} {{.Repository}}:{{.Tag}}"], ANSWER_S)
    if listed.returncode != 0:
        return None       # no daemon running: not a stall
    images = {}
    for line in listed.stdout.splitlines():
        ident, _, name = line.strip().partition(" ")
        if ident and name and "<none>" not in name:
            images.setdefault(name, ident)
    if os.environ.get(IMAGE_ENV):
        return images.get(os.environ[IMAGE_ENV])
    name = next((name for prefix in PREFERRED for name in images if name.startswith(prefix)), next(iter(images), None))
    return images.get(name)


def probe():
    """{ok, seconds, error, checked_at}, or None when there is nothing to check."""
    docker = shutil.which("docker")
    if not docker:
        return None
    started = time.monotonic()
    name = "tico-probe-" + uuid.uuid4().hex[:8]

    def result(ok, error=""):
        return {"ok": ok, "seconds": round(time.monotonic() - started, 1), "error": error[:300],
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    try:
        image = _image(docker)
        if not image:
            return None
        # A wedged daemon leaves earlier probes in Created; clear them before adding another.
        _remove(docker, *_run([docker, "ps", "-aq", "--filter", "label=" + LABEL], ANSWER_S).stdout.split())
    except subprocess.TimeoutExpired as exc:
        return result(False, f"docker did not answer within {exc.timeout:g} s")
    except OSError as exc:
        return result(False, str(exc))
    try:
        ran = _run([docker, "run", "--rm", "--name", name, "--label", LABEL, "--network", "none",
                    "--entrypoint", "true", image], LIMIT_S)
    except subprocess.TimeoutExpired:
        # The CLI is gone but the container may still be starting; the next probe removes it if this cannot.
        threading.Thread(target=lambda: _remove(docker, name), daemon=True).start()
        return result(False, f"a container did not start within {LIMIT_S} s")
    except OSError as exc:
        return result(False, str(exc))
    # 126/127: the image has no `true`. The daemon still created and started the container, which is what this checks.
    if ran.returncode in (0, 126, 127):
        return result(True)
    if "no such image" in (ran.stderr or "").lower():
        return None       # removed since it was listed: nothing was checked, and nothing was pulled
    lines = (ran.stderr or ran.stdout).strip().splitlines()
    return result(False, lines[-1] if lines else f"docker run exited {ran.returncode}")


class ContainerProbe:
    """The last probe result, refreshed in the background (or inline, for the one-shot doctor)."""

    def __init__(self, check=probe, background=True, enabled=None):
        self.check = check
        self.background = background
        self.enabled = not OFF if enabled is None else enabled
        self.last = None
        self.at = None
        self.thread = None

    def due(self):
        every = FAILING_EVERY_S if self.last and self.last.get("ok") is False else EVERY_S
        return self.at is None or time.monotonic() - self.at >= every

    def report(self):
        if not self.enabled:
            return None
        if not (self.thread and self.thread.is_alive()) and self.due():
            self.at = time.monotonic()
            if self.background:
                self.thread = threading.Thread(target=self._refresh, daemon=True)
                self.thread.start()
            else:
                self._refresh()
        return self.last

    def _refresh(self):
        try:
            result = self.check()
            if result and result.get("ok") is False and not (self.last and self.last.get("ok") is False):
                result = self.check()       # once more at once: report only two failures in a row
            self.last = result
        except Exception:     # a probe must never stop the heartbeat
            self.last = None
