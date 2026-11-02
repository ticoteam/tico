"""Historical user text import. Restricted tool traces are not a bot knowledge endpoint."""

import hashlib
import json
from pathlib import Path

from fastapi import Request

from .store import H, Problem, encode


def _archive_owner(c, runtime, relative):
    if relative.parts[:2] == ("help", "threads"):
        actor = "human:" + Path(relative.name).stem
        return actor if H.human(c, H.actor_id(actor)) else None
    if relative.parts and relative.parts[0] == "meetings" and len(relative.parts) >= 3:
        try:
            metadata = json.loads((runtime / "meetings" / relative.parts[1] / "meta.json").read_text())
        except (OSError, ValueError, TypeError):
            return None
        owner = str(metadata.get("owner") or "").strip().lower()
        if owner == "owner":
            owner = H.default_human(c)
        if owner.startswith("human:"):
            owner = owner.split(":", 1)[1]
        row = c.execute("SELECT id FROM humans WHERE lower(id)=? OR lower(email)=?", (owner, owner)).fetchone()
        return "human:" + row[0] if row else None
    return None


def import_text_archives(store, runtime):
    """Snapshot allowlisted user text, reject symlinks and retain immutable revisions.

    Canonical conversations already live in SQLite. Personal help threads carry their human
    owner; meeting files inherit their recorded owner. Structured run summaries are owner-only.
    Raw tool logs can contain terminal output and credentials, so this importer records their
    aggregate exclusion but never uploads their bytes as conversation history.
    """
    runtime = runtime.resolve()
    report = {"files": 0, "bytes": 0, "skipped": [], "excluded": {}}
    def excluded(reason, size):
        row = report["excluded"].setdefault(reason, {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += size
    for folder in ("help", "runs", "meetings"):
        base = runtime / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(runtime)
            if path.is_symlink() or not path.resolve().is_relative_to(runtime):
                report["skipped"].append({"source": str(relative), "reason": "symlink"})
                continue
            if folder == "runs" and path.suffix.lower() == ".log":
                excluded("restricted_raw_tool_log", path.stat().st_size)
                continue
            if path.suffix.lower() not in (".json", ".jsonl", ".md", ".txt", ".log"):
                excluded("non_text_asset", path.stat().st_size)
                continue
            raw = path.read_bytes()
            hashed = hashlib.sha256(raw).hexdigest()
            try:
                text = raw.decode("utf-8")
                payload = {"text": text, "encoding": "utf-8", "source": str(relative)}
            except UnicodeDecodeError:
                import base64
                payload = {"base64": base64.b64encode(raw).decode(), "encoding": "base64", "source": str(relative)}
            with store.transaction() as c:
                owner = _archive_owner(c, runtime, relative)
                kind = ("conversation" if relative.parts[:2] == ("help", "threads")
                        else "restricted_log" if folder == "runs" else "historical_text")
                c.execute("INSERT OR IGNORE INTO archives VALUES(?,?,?,?,?,?)",
                          (str(relative) + "@" + hashed, hashed, H.now(), owner, kind, encode(payload)))
            report["files"] += 1
            report["bytes"] += len(raw)
    return report


def install_archives(app, store):
    def authorized(who, row):
        return who.role == "owner" or who.role == "human" and row["owner_id"] == who.actor

    @app.get("/api/v2/archives")
    def listing(request: Request):
        who = request.state.identity
        with store.read() as c:
            return {"archives": [dict(row) for row in c.execute("SELECT source,digest,imported_at,owner,kind FROM archives ORDER BY imported_at,source")
                                 if authorized(who, row)]}

    @app.get("/api/v2/archive")
    def read(request: Request, source: str):
        with store.read() as c:
            row = c.execute("SELECT * FROM archives WHERE source=?", (source,)).fetchone()
            if not row or not authorized(request.state.identity, row):
                raise Problem("not_found", "Archive not found or unavailable to this account", 404)
            return {**{k: row[k] for k in ("source", "digest", "imported", "owner", "kind")},
                    "content": json.loads(row["payload"])}
