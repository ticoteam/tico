"""Whether a container actually starts on this computer, for readiness (backend/health.py warns).

Docker can answer `docker info` while every `docker run` hangs (a stalled disk under Docker Desktop's VM),
and then a bot's container work waits forever with nothing in Health. So, where a `docker` CLI is on PATH
and its daemon answers, the runner starts a throwaway container from an image already on the computer
every few minutes, in the background, with a time limit. It never pulls, and never touches the network.

A daemon that is simply not running is not a stall: nothing is reported then.
"""
import datetime
import os
import shutil
import subprocess
import threading
import time
import uuid

EVERY_S = 300           # a probe costs a container start; a heartbeat is far more often
LIMIT_S = 20            # a cached image starts in well under this on a healthy computer
LIST_LIMIT_S = 10
IMAGE_ENV = "TICO_RUNNER_PROBE_IMAGE"
# Images likely to have `true`, best first; any other local image is the fallback.
PREFERRED = ("ghcr.io/ticoteam/tico", "tico-runner", "busybox", "alpine", "debian", "ubuntu", "python")


def _run(args, limit):
    return subprocess.run(args, capture_output=True, text=True, timeout=limit, stdin=subprocess.DEVNULL)


def _image(docker):
    """A local image to start, or None when there is none. Raises TimeoutExpired when the daemon hangs."""
    if os.environ.get(IMAGE_ENV):
        return os.environ[IMAGE_ENV]
    listed = _run([docker, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], LIST_LIMIT_S)
    if listed.returncode != 0:
        return None       # no daemon running: not a stall
    images = [line for line in listed.stdout.split() if "<none>" not in line]
    return next((image for prefix in PREFERRED for image in images if image.startswith(prefix)),
                images[0] if images else None)


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
    except subprocess.TimeoutExpired:
        return result(False, f"docker did not list images within {LIST_LIMIT_S} s")
    except OSError as exc:
        return result(False, str(exc))
    if not image:
        return None
    try:
        ran = _run([docker, "run", "--rm", "--name", name, "--network", "none", "--pull", "never",
                    "--entrypoint", "true", image], LIMIT_S)
    except subprocess.TimeoutExpired:
        # The CLI is gone but the container may still be starting; remove it without waiting.
        threading.Thread(target=lambda: subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=60),
                         daemon=True).start()
        return result(False, f"a container did not start within {LIMIT_S} s")
    except OSError as exc:
        return result(False, str(exc))
    # 126/127: the image has no `true`. The daemon still created and started the container, which is what this checks.
    if ran.returncode in (0, 126, 127):
        return result(True)
    lines = (ran.stderr or ran.stdout).strip().splitlines()
    return result(False, lines[-1] if lines else f"docker run exited {ran.returncode}")


class ContainerProbe:
    """The last probe result, refreshed in the background at most every EVERY_S seconds."""

    def __init__(self, check=probe):
        self.check = check
        self.last = None
        self.at = None
        self.running = False

    def report(self):
        if not self.running and (self.at is None or time.monotonic() - self.at >= EVERY_S):
            self.running = True
            self.at = time.monotonic()
            threading.Thread(target=self._refresh, daemon=True).start()
        return self.last

    def _refresh(self):
        try:
            self.last = self.check()
        except Exception:     # a probe must never stop the heartbeat
            self.last = None
        finally:
            self.running = False
