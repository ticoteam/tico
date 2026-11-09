"""Fills the inputs block of the cloud-init files in infra/cloud-init/ (the same files people can paste by hand)."""

from __future__ import annotations

import re
from pathlib import Path

from . import envfile

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "infra" / "cloud-init"
BEGIN, END = "# --- inputs begin ---", "# --- inputs end ---"
VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$")


def _fill(template: str, values: dict[str, str]) -> str:
    lines = template.splitlines()
    try:
        a = next(i for i, l in enumerate(lines) if l.strip() == BEGIN)
        b = next(i for i, l in enumerate(lines) if l.strip() == END)
    except StopIteration:
        raise ValueError("template has no inputs block") from None
    indent = lines[a][: len(lines[a]) - len(lines[a].lstrip())]
    body = envfile.render(values).splitlines()[1:]  # drop the "Written by tico setup" header
    return "\n".join(lines[: a + 1] + [indent + l for l in body] + lines[b:]) + "\n"


def _template(name: str, template_dir: Path | None) -> str:
    return ((template_dir or TEMPLATE_DIR) / name).read_text()


def server_user_data(
    env: dict[str, str], *, version: str, server_ip: str = "", allow_ssh: bool = False, template_dir: Path | None = None
) -> str:
    """`env` is Settings.to_env(): the .env the server will get. Cloud-only inputs are prefixed TICO_CLOUD_."""
    if not VERSION_RE.match(version):
        raise ValueError(f"version must be a release such as v1.2.3, not {version!r}")
    values = {
        "TICO_CLOUD_VERSION": version,
        "TICO_CLOUD_SERVER_IP": server_ip,
        "TICO_CLOUD_ALLOW_SSH": "1" if allow_ssh else "0",
        **env,
    }
    values.pop("TICO_TAG", None)  # the script pins it to the version
    return _fill(_template("tico-server.yaml", template_dir), values)


def runner_user_data(
    *,
    url: str,
    code: str,
    label: str,
    version: str,
    allow_ssh: bool = False,
    screen: bool = True,
    template_dir: Path | None = None,
) -> str:
    if not VERSION_RE.match(version):
        raise ValueError(f"version must be a release such as v1.2.3, not {version!r}")
    values = {
        "TICO_CLOUD_VERSION": version,
        "TICO_CLOUD_ALLOW_SSH": "1" if allow_ssh else "0",
        "TICO_URL": url,
        "TICO_CODE": code,
        "TICO_RUNNER_LABEL": label,
        "TICO_SCREEN": "1" if screen else "0",
    }
    return _fill(_template("tico-runner.yaml", template_dir), values)
