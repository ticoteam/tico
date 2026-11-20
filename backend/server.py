"""One supervised server: Rust owns the public listener and screen sockets; Python owns API policy."""

import argparse
from dataclasses import replace
import logging
import os
import queue
import secrets
import signal
import socket
import sys
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uvicorn import Server as _UvicornServer  # imported lazily at run time (start-up speed)
else:
    _UvicornServer = object


def _logging_config(log_level):
    from uvicorn.config import LOG_LEVELS, LOGGING_CONFIG
    from .diagnostics import RING

    level = LOG_LEVELS[log_level.lower()]
    # One stdout path per call log. The warning ring still receives warnings at any console level.
    call_logger = {"handlers": ["calls", "call_diagnostics"], "level": min(level, logging.WARNING), "propagate": False}
    return {
        **LOGGING_CONFIG,
        "handlers": {
            **LOGGING_CONFIG["handlers"],
            "calls": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
                "level": level,
            },
            "call_diagnostics": {"()": lambda: RING},
        },
        "loggers": {
            **LOGGING_CONFIG["loggers"],
            "tico.calls": call_logger,
            "tico.call_media": call_logger,
            "tico.call_voice": call_logger,
        },
    }


def _freeze_startup_heap():
    """Once the app has started: what it holds from then on (every module, the catalogs, the open store) moves out of
    the cycle collector's generations (gc.freeze), so a full collection walks only what requests made since. On a
    TIDY-sized heap (~410k tracked objects) a full collection inside a request took ~60 ms, about one request in four;
    frozen it takes 2-6 ms. Frozen objects are still freed by reference count, and what exists at start lives as long
    as the process. The cost: a reference cycle that holds a frozen object is never collected, with everything it
    reaches, even what is made later (say, a relay response in flight at the freeze, kept alive by a cycle through it).
    That can only happen to what was alive at this one moment at start, so it is bounded and does not grow."""
    import gc

    gc.collect()
    gc.freeze()


class _FreezeAfterStartup(_UvicornServer):
    """Mixed into the uvicorn server: once startup succeeds (the app's lifespan has run and the sockets listen), the
    startup heap is frozen. A failed lifespan raises out of startup (uvicorn exits), and a stop asked for while starting
    skips it; either way nothing is frozen."""

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if not self.should_exit:
            _freeze_startup_heap()


def run(*, host="127.0.0.1", port=8765, settings=None, log_level="info"):
    from .config import Settings
    from .screen_binary import prepare
    from . import background, web_build

    # A checkout builds the web app it serves at / when that build is missing or out of date (backend/web_build.py).
    unbuilt = web_build.prepare(settings.web_dir if settings else web_build.ROOT / "build" / "web")
    if unbuilt:
        print(f"tico server: {unbuilt}; / answers 404 until it is built", file=sys.stderr, flush=True)
    binary = prepare()
    key = secrets.token_urlsafe(48)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    bind = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    process = subprocess.Popen(
        [str(binary)],
        stdout=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "TICO_SCREEN_RELAY_KEY": key,
            "TICO_SCREEN_RELAY_BIND": "127.0.0.1:0",
            "TICO_TRUSTED_PROXIES": ','.join(settings.trusted_proxies)
            if settings
            else os.environ.get('TICO_TRUSTED_PROXIES') or '127.0.0.1/32,::1/128',
            "TICO_CLIENT_IP_HEADER": settings.client_ip_header
            if settings
            else os.environ.get('TICO_CLIENT_IP_HEADER') or 'x-forwarded-for',
            "TICO_SCREEN_PUBLIC_BIND": bind,
            "TICO_SCREEN_PYTHON_URL": origin,
        },
    )
    lines = queue.Queue()

    def output():
        for line in process.stdout:
            lines.put(line)
        lines.put("")

    background.start("tico-screen-relay-output", output)
    server = None
    stopping = threading.Event()
    previous_signals = {}

    def stop(signum, frame):
        stopping.set()  # a stop before the server exists is kept, not lost (see below)
        if server:
            server.should_exit = True

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_signals[signum] = signal.signal(signum, stop)
    try:
        # Imported after the relay is spawned, so its start overlaps the import instead of following it; inside this
        # try, so a failed import still stops the relay.
        import uvicorn
        from .app import create_app

        line = lines.get(timeout=15)
        if not line.startswith("screen relay listening on "):
            raise RuntimeError("Rust screen relay could not start")
        url = "http://" + line.strip().split()[-1]
        # Discard obsolete pilot settings; a start creates its own private control secret and ports.
        env = {**os.environ, "TICO_SCREEN_RELAY_URL": url, "TICO_SCREEN_RELAY_KEY": key}
        if settings is None:
            old = {name: os.environ.get(name) for name in ("TICO_SCREEN_RELAY_URL", "TICO_SCREEN_RELAY_KEY")}
            os.environ.update({name: env[name] for name in old})
            try:
                settings = Settings.from_env()
            finally:
                for name, value in old.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        else:
            settings = replace(settings, screen_relay_url=url, screen_relay_key=key)

        class Server(_FreezeAfterStartup, uvicorn.Server):
            def handle_exit(self, sig, frame):  # uvicorn takes SIGINT and SIGTERM while it runs: a stop starts here
                # The public port closes with the server, so a new connect is refused (Caddy holds it), not a relay 502.
                stopping.set()
                process.send_signal(signal.SIGTERM)
                super().handle_exit(sig, frame)

        server = Server(
            uvicorn.Config(
                create_app(settings),
                log_config=_logging_config(log_level),
                log_level=log_level,
                access_log=False,
                proxy_headers=False,
                timeout_graceful_shutdown=2,
                timeout_keep_alive=5,
            )
        )  # longer than the Rust relay's 4 s pool idle timeout
        if stopping.is_set():
            server.should_exit = True  # asked to stop while starting: uvicorn starts, then stops at once

        def watch():
            process.wait()
            server.should_exit = True

        background.start("tico-screen-relay-watch", watch)
        server.run(sockets=[listener])
        if process.poll() is not None and not stopping.is_set():
            raise RuntimeError("Rust screen relay exited; the supervisor must restart the server")
        if not server.started:
            raise RuntimeError("Backend startup failed")
    finally:
        listener.close()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    run(host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
