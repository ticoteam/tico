from __future__ import annotations

import re

from . import contract

_PLAIN = re.compile(r"^[A-Za-z0-9_@%+=:,./-]*$")
MASK = "********"


def _quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("a .env value cannot contain a newline")
    value = value.replace("$", "$$")
    if _PLAIN.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(values: dict[str, str]) -> str:
    """Known keys first in the documented order, then any others; empty values are omitted."""
    keys = [k for k in contract.ENV_ORDER if values.get(k)]
    keys += sorted(k for k in values if k not in contract.ENV_ORDER and values[k])
    lines = ["# Written by `tico setup`. Holds secrets: keep it private (0600). Re-run the wizard to change it."]
    lines += [f"{k}={_quote(values[k])}" for k in keys]
    return "\n".join(lines) + "\n"


def keys(text: str) -> list[str]:
    """The keys of a .env render() wrote, line by line as sh and grep see it: split on \\n only (a value may hold other
    line separators, such as U+2028)."""
    return [line.split("=", 1)[0] for line in text.split("\n") if "=" in line and not line.startswith("#")]


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = re.sub(r"\\(.)", r"\1", v[1:-1])
        out[k.strip()] = v.replace("$$", "$")
    return out


def redact_env(text: str) -> str:
    """The .env as it is safe to show: secret values masked."""
    out = []
    for line in text.splitlines():
        k, sep, _ = line.partition("=")
        out.append(f"{k}={MASK}" if sep and k.strip() in contract.SECRET_KEYS else line)
    return "\n".join(out) + "\n"


def scrub(text: str, secrets: list[str]) -> str:
    """Remove any known secret value from free text (command output, error messages)."""
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, MASK)
    return text
