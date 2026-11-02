"""Which computer runs each meeting importer, and how each one is doing.

The owner turns an importer on in Settings and picks the enrolled computer that holds its
credentials. That computer's `python -m runner importers` job asks `GET /api/v2/runners/importers`
what to run and reports through `POST /api/v2/imports/sources/{source}/status`. Credentials are never
stored here: they live in `secrets/<file>` on the computer (docs/meetings.md).
"""

import json
from typing import Literal

from fastapi import Request
from pydantic import Field

from . import models as M
from .store import H, Problem, encode

IMPORTERS = {
    "zoom": {"name": "Zoom", "file": "secrets/zoom.env",
             "keys": ["ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"], "interval": 600},
    "google-meet": {"name": "Google Meet", "file": "secrets/google-meet.env",
                    "keys": ["GOOGLE_MEET_USERS", "GOOGLE_SERVICE_ACCOUNT_FILE"], "interval": 600},
    "granola": {"name": "Granola API key (Business/Enterprise)", "file": "secrets/granola.env", "keys": ["GRANOLA_API_KEY"], "interval": 300},
}
RETIRED_IMPORTERS = {"fireflies"}
ERROR_TEXT = {
    "missing_credentials": "The credential file on that computer is missing or incomplete.",
    "auth_failed": "The tool refused the credential.",
    "forbidden": "The credential lacks a scope, or the account lacks a plan feature.",
    "rate_limited": "The tool asked to slow down; the importer retries on its own.",
    "unreachable": "The tool could not be reached from that computer.",
    "provider_error": "The tool returned an error.",
    "bad_response": "The tool returned something unreadable.",
    "hub_rejected": "This server refused an import.",
    "sync_error": "The sync failed; see the computer's importers log.",
}


class ImporterConfig(M.Contract):
    enabled: bool
    runner_id: str = Field(default="", max_length=80)


class ImporterHeartbeat(M.Contract):
    state: Literal["ok", "error"]
    imported: int = Field(default=0, ge=0, le=1_000_000)
    error_code: str = Field(default="", pattern=r"^[a-z_]{0,40}$")
    message: str = Field(default="", max_length=200)


def status_of(enabled, row, interval, now):
    if not enabled:
        return "off"
    if not row:
        return "waiting"
    if row["last_error"] and (not row["last_succeeded_at"] or row["last_error"] >= row["last_succeeded_at"]):
        return "error"
    if row["last_succeeded_at"] and row["last_succeeded_at"] >= H.shift(now, seconds=-(3 * interval + 300)):
        return "syncing"
    return "delayed"


def install_meeting_importers(app, store, auth, execution, mutate, importer):
    def eligible_operators(c):
        return store.settings.processing_operators or (auth.owner_id(c),)

    def computers(c):
        allowed = set(eligible_operators(c))
        now = H.now()
        return [{"id": r["id"], "label": r["label"], "platform": r["platform"] or "",
                 "online": bool(r["last_seen_at"] and r["last_seen_at"] >= H.shift(now, minutes=-5)),
                 "last_seen_at": r["last_seen_at"]}
                for r in c.execute("SELECT id,label,platform,last_seen_at,owner_id FROM runners "
                                   "WHERE revoked_at IS NULL ORDER BY label") if r["owner_id"] in allowed]

    def configured(c):
        return {r["source"]: dict(r) for r in c.execute("SELECT * FROM meeting_importers")}

    @app.get("/api/v2/meeting-importers")
    def listing(request: Request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner manages meeting importers", 403)
        with store.read() as c:
            machines = computers(c)
            labels = {m["id"]: m for m in machines}
            saved, now, rows = configured(c), H.now(), []
            for source, spec in IMPORTERS.items():
                cfg = saved.get(source) or {}
                health = c.execute("SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service=?",
                                   ("recording:" + source,)).fetchone()
                detail = json.loads(health["detail"] or "{}") if health else {}
                code = detail.get("error_code") or ""
                enabled = bool(cfg.get("enabled"))
                machine = labels.get(cfg.get("runner_id") or "")
                rows.append({
                    "source": source, "name": spec["name"], "enabled": enabled,
                    "runner_id": cfg.get("runner_id") or "", "runner_label": machine["label"] if machine else "",
                    "status": status_of(enabled, health, spec["interval"], now),
                    "last_succeeded_at": health["last_succeeded_at"] if health else None,
                    "last_attempt": detail.get("last_attempt"), "last_import": detail.get("last_import"),
                    "imported_total": detail.get("imported_total", 0),
                    "error_code": code, "error": (detail.get("message") or ERROR_TEXT.get(code, "")) if code else "",
                    "setup": {"file": spec["file"], "keys": spec["keys"], "doc": "docs/meetings.md#" + source}})
            return {"importers": rows, "computers": machines}

    @app.post("/api/v2/meeting-importers/{source}")
    def configure(request: Request, source: str, body: ImporterConfig):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner manages meeting importers", 403)
        if source not in IMPORTERS and source not in RETIRED_IMPORTERS:
            raise Problem("not_found", "No such importer", 404)
        if source in RETIRED_IMPORTERS and body.enabled:
            raise Problem("importer_retired", "This meeting importer is no longer available", 410)

        def work(c):
            runner_id = body.runner_id
            if body.enabled and runner_id not in {m["id"] for m in computers(c)}:
                raise Problem("validation", "Choose an enrolled computer that may run importers", 422)
            old = c.execute("SELECT runner_id FROM meeting_importers WHERE source=?", (source,)).fetchone()
            c.execute("INSERT INTO meeting_importers(source,enabled,runner_id,updated_by,updated_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(source) DO UPDATE SET enabled=excluded.enabled,runner_id=excluded.runner_id,"
                      "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                      (source, int(body.enabled), runner_id or None, who.actor, H.now()))
            if source not in RETIRED_IMPORTERS and (not old or (old["runner_id"] or "") != runner_id):
                # A different computer starts with a clean bill of health, not the last one's.
                c.execute("DELETE FROM service_health WHERE service=?", ("recording:" + source,))
            H.event(c, who.actor, "meeting_importer.configured", source,
                    {"enabled": body.enabled, "runner_id": runner_id})
            return {"ok": True}
        return mutate(request, body, work)

    @app.get("/api/v2/runners/importers")
    def assigned(request: Request):
        with store.read() as c:
            runner = importer(c, request.state.identity)
            return {"importers": [{"source": r["source"]} for r in c.execute(
                "SELECT source FROM meeting_importers WHERE enabled=1 AND runner_id=?", (runner["id"],))
                if r["source"] in IMPORTERS]}

    @app.post("/api/v2/imports/sources/{source}/status")
    def heartbeat(request: Request, source: str, body: ImporterHeartbeat):
        if source not in IMPORTERS and source not in RETIRED_IMPORTERS:
            raise Problem("not_found", "No such importer", 404)

        def work(c):
            importer(c, request.state.identity)
            if source in RETIRED_IMPORTERS:
                # Old runners may finish an in-flight pass; authorize before acknowledging it.
                return {"ok": True}
            old = c.execute("SELECT detail FROM service_health WHERE service=?", ("recording:" + source,)).fetchone()
            detail = json.loads(old[0]) if old else {}
            detail.update(last_attempt=H.now())
            if body.imported:
                detail.update(last_import=H.now(), imported_total=detail.get("imported_total", 0) + body.imported)
            if body.state == "ok":
                detail.update(error_code="", message="")
                c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,NULL,?) "
                          "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,last_error=NULL,"
                          "detail=excluded.detail", ("recording:" + source, H.now(), encode(detail)))
            else:
                detail.update(error_code=body.error_code or "sync_error", message=body.message)
                c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,NULL,?,?) "
                          "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,"
                          "detail=excluded.detail", ("recording:" + source, H.now(), encode(detail)))
            return {"ok": True}
        return mutate(request, body, work)
