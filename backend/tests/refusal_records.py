"""Recorded refusals for the end-to-end sweeps: one JSON file per tool or command, so two changes that add different
tools or commands never edit the same file.

A file holds the outcome a call must give, as a JSON string. Its path is the name it records: `<folder>/<tool>.json`
for an MCP tool, and `<folder>/task/create.json` for `hub task create` (a folder is a command group). A call that
succeeds has no file.
"""

import json
from pathlib import Path


def load(folder: Path) -> dict[str, str]:
    """{name: recorded outcome} for every .json file under `folder`."""
    return {
        " ".join(path.relative_to(folder).as_posix()[: -len(".json")].split("/")): json.loads(path.read_text())
        for path in sorted(folder.rglob("*.json"))
    }


def write(folder: Path, observed: dict[str, str]) -> None:
    """Replace the recorded outcomes under `folder` with `observed`, one file per name, and drop the folders left empty.
    The folder's diff is the review."""
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.rglob("*.json"):
        path.unlink()
    for name, outcome in sorted(observed.items()):
        path = folder.joinpath(*name.split(" "))
        path = path.with_name(path.name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(outcome, ensure_ascii=False) + "\n")
    for path in sorted(folder.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
