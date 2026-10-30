"""One supervised server: Rust owns the public listener and screen sockets; Python owns API policy."""
import argparse
from dataclasses import replace
import os
import queue
import secrets
import signal
import socket
import subprocess
import threading


def run(*, host="127.0.0.1", port=8765, settings=None, log_level="info"):
    from .config import Settings
    from .screen_binary import prepare

    binary = prepare()
    key = secrets.token_urlsafe(48)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    bind = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    process = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "TICO_SCREEN_RELAY_KEY": key, "TICO_SCREEN_RELAY_BIND": "127.0.0.1:0",
             "TICO_TRUSTED_PROXIES": ','.join(settings.trusted_proxies) if settings else
                 os.environ.get('TICO_TRUSTED_PROXIES') or '127.0.0.1/32,::1/128',
             "TICO_SCREEN_PUBLIC_BIND": bind, "TICO_SCREEN_PYTHON_URL": origin})
    lines = queue.Queue()
    def output():
        for line in process.stdout:
            lines.put(line)
        lines.put("")
    threading.Thread(target=output, daemon=True).start()
    server = None
    previous_signals = {}
    stopping = threading.Event()
    def stop(signum, frame):
        stopping.set()      # a stop before the server exists is kept, not lost (see below)
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
        server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level=log_level, access_log=False,
            proxy_headers=False, timeout_graceful_shutdown=2,
            timeout_keep_alive=5))  # longer than the Rust relay's 4 s pool idle timeout
        if stopping.is_set():
            server.should_exit = True    # asked to stop while starting: uvicorn starts, then stops at once
        def watch():
            process.wait()
            server.should_exit = True
        threading.Thread(target=watch, daemon=True).start()
        server.run(sockets=[listener])
        if process.poll() is not None:
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
