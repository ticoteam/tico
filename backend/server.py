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
    import uvicorn
    from .app import create_app
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
             "TICO_CLIENT_IP_HEADER": settings.client_ip_header if settings else
                 os.environ.get('TICO_CLIENT_IP_HEADER') or 'x-forwarded-for',
             "TICO_SCREEN_PUBLIC_BIND": bind, "TICO_SCREEN_PYTHON_URL": origin})
    lines = queue.Queue()
    def output():
        for line in process.stdout:
            lines.put(line)
        lines.put("")
    threading.Thread(target=output, daemon=True).start()
    server = None
    stopping = threading.Event()
    previous_signals = {}
    def stop(signum, frame):
        if server:
            server.should_exit = True
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_signals[signum] = signal.signal(signum, stop)
    try:
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
        class Server(uvicorn.Server):
            def handle_exit(self, sig, frame):  # uvicorn takes SIGINT and SIGTERM while it runs: a stop starts here
                # The public port closes with the server, so a new connect is refused (Caddy holds it), not a relay 502.
                stopping.set()
                process.send_signal(signal.SIGTERM)
                super().handle_exit(sig, frame)
        server = Server(uvicorn.Config(create_app(settings), log_level=log_level, access_log=False,
            proxy_headers=False, timeout_graceful_shutdown=2,
            timeout_keep_alive=5))  # longer than the Rust relay's 4 s pool idle timeout
        def watch():
            process.wait()
            server.should_exit = True
        threading.Thread(target=watch, daemon=True).start()
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
