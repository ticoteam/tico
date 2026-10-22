"""What a run remembers between runs, outside the repository: settings (no secrets) and a private copy of the .env."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .contract import SERVER_ONLY_KEYS


def home() -> Path:
    return Path(os.environ.get("TICO_SETUP_HOME") or Path.home() / ".config" / "tico-setup")


def _dir(domain: str) -> Path:
    return home() / domain


def write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(path, 0o600)


def save(domain: str, settings: dict, env_text: str | None = None) -> None:
    write_private(_dir(domain) / "state.json", json.dumps(settings, indent=2, sort_keys=True))
    if env_text is not None:
        write_private(_dir(domain) / "env", _local_copy(env_text))


def _local_copy(env_text: str) -> str:
    """The .env as this computer keeps it: without the values only the server should hold (contract.SERVER_ONLY_KEYS)."""
    lines = [line for line in env_text.splitlines() if line.partition("=")[0].strip() not in SERVER_ONLY_KEYS]
    return "\n".join(lines) + ("\n" if lines else "")


def load(domain: str) -> tuple[dict, str]:
    d = _dir(domain)
    try:
        s = json.loads((d / "state.json").read_text())
    except (OSError, ValueError):
        s = {}
    try:
        e = (d / "env").read_text()
    except OSError:
        e = ""
    return s, e


def known_domains() -> list[str]:
    try:
        return sorted(p.name for p in home().iterdir() if (p / "state.json").exists())
    except OSError:
        return []


def save_runner(name: str, data: dict) -> None:
    write_private(home() / "runners" / f"{name}.json", json.dumps(data, indent=2, sort_keys=True))


def load_runners(server_url: str = "") -> list[dict]:
    out = []
    try:
        for p in sorted((home() / "runners").glob("*.json")):
            d = json.loads(p.read_text())
            if not server_url or d.get("server_url") == server_url:
                out.append(d)
    except (OSError, ValueError):
        pass
    return out
