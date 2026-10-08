"""Sync people from the company directory instead of adding them one by one.

Three feeds, one engine. Google Workspace and Microsoft Entra ID are pulled on the server with a
read-only credential (backend/directory_sources.py); an identity provider can instead push over
SCIM (backend/scim.py). Every feed hands the engine the same record and the engine applies the
same rules to the roster:

  * a person a feed created is linked to it (`directory`), and only that feed can mark them left;
  * a person added by hand is never marked left, and is only filled in where a field is blank;
  * the owner is never marked left;
  * leavers are marked left (tokens and sessions ended), never deleted, and come back when the
    directory says so, unless the owner marked them left by hand.

A pull always produces a plan first (adds, updates, leaves). The first sync, and any sync that
would mark more than `mass_leave_limit` people left, is applied only with the owner's confirmation
of that exact plan (`plan_hash`); the interval sync holds instead and says why.
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from pydantic import Field

from . import access as Access
from . import directory_sources as Sources
from . import hubdb as H
from . import people as P
from . import people_photos, personal_tokens
from .models import Contract
from .store import Problem

KEY = "directory"
SOURCES = ("", "google", "entra", "scim")
DEFAULT_LIMIT = 10
DEFAULT_INTERVAL = 360
AAD = b"tico-directory:v1"
PHOTO_BUDGET = 200          # photos fetched per run; the rest arrive on a later sync
SCHEMA = """
CREATE TABLE IF NOT EXISTS directory_credentials(
 source TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, hint TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, created_by TEXT NOT NULL);
"""


# ------------------------------------------------------------------------------------ settings
def _blank():
    return {"source": "", "filter": {"groups": [], "org_units": [], "domains": []},
            "interval_minutes": DEFAULT_INTERVAL, "mass_leave_limit": DEFAULT_LIMIT, "confirmed": False,
            "revision": 0, "updated": "", "updated_by": "", "last": {}, "scim": {}}


def load(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return {**_blank(), **(stored if isinstance(stored, dict) else {})}


def save(c, cfg):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(cfg, sort_keys=True)))


def _lines(values, lower=False):
    out = []
    for value in values or []:
        item = str(value or "").strip()
        item = item.lower() if lower else item
        if item and item not in out:
            out.append(item)
    return out


# ---------------------------------------------------------------------------------- the engine
def _blank_name(p):
    return not p["name"] or p["name"] == p["id"].title()


def plan(roster, records, source, owner_email, complete):
    """What syncing `records` would do to `roster`. Pure. `complete` says the records are the
    whole directory (a pull), so a linked person missing from it has left."""
    people = roster["people"]
    by_email = {p["email"]: p for p in people if p["email"]}
    out = {"adds": [], "updates": [], "restores": [], "leaves": [], "protected": [], "skipped": []}
    seen = set()
    for r in records:
        email = r["email"]
        if email in seen:
            out["skipped"].append({"email": email, "reason": "listed twice"})
            continue
        seen.add(email)
        p = by_email.get(email)
        if p is None:
            if r["active"]:
                out["adds"].append({"email": email, "name": r["name"], "title": r["title"],
                                    "manager": r.get("manager") or "", "external_id": r.get("external_id") or ""})
            continue
        mine = p["directory"] == source
        if not r["active"]:
            if mine and not p["hidden"]:
                _leave(out, p, owner_email, "disabled in the directory")
            continue
        if p["hidden"]:
            if mine and p["directory_left"]:
                out["restores"].append({"id": p["id"], "email": email})
            else:
                out["skipped"].append({"email": email, "reason": "was marked left here; not restored"})
            continue
        changes = {}
        for field, new in (("name", r["name"]), ("title", r["title"])):
            if not new or new == p[field]:
                continue
            # Someone added by hand (or by another feed) is only ever filled in where blank.
            if mine or (_blank_name(p) if field == "name" else not p[field]):
                changes[field] = [p[field], new]
        if mine and r.get("external_id") and r["external_id"] != p["external_id"]:
            changes["external_id"] = [p["external_id"], r["external_id"]]
        if r.get("manager") and (mine or not p["reports_to"]):
            changes["manager"] = r["manager"]
        if changes:
            out["updates"].append({"id": p["id"], "email": email, "changes": changes})
    if complete:
        for p in people:
            if p["directory"] == source and not p["hidden"] and p["email"] and p["email"] not in seen:
                _leave(out, p, owner_email, "no longer in the directory scope")
    out["hash"] = hashlib.sha256(json.dumps(
        {k: out[k] for k in ("adds", "updates", "restores", "leaves")}, sort_keys=True).encode()).hexdigest()[:24]
    return out


def _leave(out, p, owner_email, reason):
    if p["email"] == owner_email:
        out["protected"].append({"email": p["email"], "reason": "the owner is never marked left by sync"})
    else:
        out["leaves"].append({"id": p["id"], "email": p["email"], "name": p["name"], "reason": reason})


def counts(p):
    return {k: len(p[k]) for k in ("adds", "updates", "restores", "leaves", "protected", "skipped")}


def _find(people, ref):
    ref = str(ref or "").strip()
    low = ref.lower()
    for p in people:
        if not p["hidden"] and ref and (p["email"] == low or p["id"] == ref or p["external_id"] == ref):
            return p
    return None


def apply(c, actor, source, p, roster, now=None):
    """Write a plan to the roster in the caller's transaction; returns the counts applied."""
    people = [dict(x) for x in roster["people"]]
    index = {x["id"]: i for i, x in enumerate(people)}
    done = {"adds": 0, "updates": 0, "restores": 0, "leaves": 0}
    managers = []
    touched = []
    for a in p["adds"]:
        if any(x["email"] == a["email"] for x in people):
            continue
        row = P._person({"id": Access._new_id(c, a["email"]), "name": a["name"], "email": a["email"],
                         "title": a["title"], "directory": source, "external_id": a["external_id"]})
        people.append(row)
        index[row["id"]] = len(people) - 1
        Access._sync_human(c, row)
        H.event(c, actor, "directory.person_added", row["id"], {"email": a["email"], "source": source})
        done["adds"] += 1
        touched.append(row["email"])
        if a["manager"]:
            managers.append((row["id"], a["manager"]))
    for u in p["updates"]:
        i = index.get(u["id"])
        if i is None:
            continue
        row = dict(people[i])
        for field, (_, new) in ((k, v) for k, v in u["changes"].items() if k != "manager"):
            row[field] = new
        people[i] = P._person(row)
        Access._sync_human(c, people[i])
        H.event(c, actor, "directory.person_updated", u["id"],
                {"email": u["email"], "source": source, "fields": sorted(u["changes"])})
        done["updates"] += 1
        touched.append(u["email"])
        if "manager" in u["changes"]:
            managers.append((u["id"], u["changes"]["manager"]))
    for r in p["restores"]:
        i = index.get(r["id"])
        if i is not None:
            people[i] = {**people[i], "hidden": False, "directory_left": False}
            H.event(c, actor, "directory.person_restored", r["id"], {"email": r["email"], "source": source})
            done["restores"] += 1
    for lv in p["leaves"]:
        i = index.get(lv["id"])
        if i is None or people[i]["hidden"]:
            continue
        row = Access.leave(c, people, people[i])
        people[i] = {**row, "directory_left": True}
        H.event(c, actor, "directory.person_left", lv["id"],
                {"email": lv["email"], "source": source, "reason": lv["reason"]})
        done["leaves"] += 1
    for pid, ref in managers:
        boss = _find(people, ref)
        if not boss or boss["id"] == pid:
            continue
        at, seen = boss["id"], set()
        while at and at not in seen and at != pid:      # never move someone under their own report
            seen.add(at)
            at = people[index[at]]["reports_to"] if at in index else ""
        if at == pid:
            continue
        people[index[pid]] = {**people[index[pid]], "reports_to": boss["id"]}
    Access._save_roster(c, {**roster, "people": people})
    return done, touched


# ------------------------------------------------------------------------------------ credentials
class DirectoryCredentials(Contract):
    # Google: the service account's JSON key and the admin it acts as. Entra: tenant, client id, secret.
    service_account_json: str = Field(default="", max_length=20000)
    admin_email: str = Field(default="", max_length=320)
    tenant: str = Field(default="", max_length=200)
    client_id: str = Field(default="", max_length=100)
    client_secret: str = Field(default="", max_length=400)


class DirectoryFilter(Contract):
    groups: list[str] = Field(default_factory=list, max_length=50)
    org_units: list[str] = Field(default_factory=list, max_length=50)
    domains: list[str] = Field(default_factory=list, max_length=50)


class DirectoryUpdate(Contract):
    source: str = Field(default="", max_length=10)
    filter: DirectoryFilter = Field(default_factory=DirectoryFilter)
    interval_minutes: int = Field(default=DEFAULT_INTERVAL, ge=0, le=10080)
    mass_leave_limit: int = Field(default=DEFAULT_LIMIT, ge=0, le=10000)
    expected_revision: int = 0
    credentials: DirectoryCredentials | None = None


class SyncRequest(Contract):
    confirm: bool = False
    plan_hash: str = Field(default="", max_length=64)


class Empty(Contract):
    pass


def _credential_blob(source, body):
    if source == "google":
        try:
            info = json.loads(body.service_account_json)
        except ValueError:
            info = None
        if not isinstance(info, dict) or not info.get("client_email") or "PRIVATE KEY" not in str(info.get("private_key")):
            raise Problem("credentials", "Paste the service account's JSON key file", 422)
        admin = Access._valid_email(body.admin_email)
        keep = {k: info[k] for k in ("client_email", "private_key", "token_uri", "client_id") if k in info}
        return {"service_account": keep, "admin_email": admin}, info["client_email"] + " as " + admin
    tenant, client, secret = body.tenant.strip(), body.client_id.strip(), body.client_secret.strip()
    if not (tenant and client and secret) or "/" in tenant:
        raise Problem("credentials", "Enter the tenant, application (client) ID and client secret value", 422)
    return {"tenant": tenant, "client_id": client, "client_secret": secret}, f"app {client} in {tenant}"


class Directory:
    """Stored settings and credentials, plus the pull, preview and apply steps."""

    def __init__(self, settings, store, github_app):
        self.settings, self.store, self.github_app = settings, store, github_app
        self.lock = threading.Lock()
        with store.transaction() as c:
            c.executescript(SCHEMA)

    # -- credentials, encrypted like the GitHub App key (same key, its own context string)
    def put_credentials(self, c, actor, source, blob, hint):
        nonce = os.urandom(12)
        sealed = AESGCM(self.github_app.secret_key(c)).encrypt(nonce, json.dumps(blob).encode(), AAD + source.encode())
        c.execute("INSERT INTO directory_credentials VALUES(?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
                  "ciphertext=excluded.ciphertext, nonce=excluded.nonce, hint=excluded.hint, created=excluded.created, "
                  "created_by=excluded.created_by", (source, sealed, nonce, hint, H.now(), actor))

    def credentials(self, c, source):
        row = c.execute("SELECT * FROM directory_credentials WHERE source=?", (source,)).fetchone()
        if not row:
            raise Problem("credentials", "Save this source's credentials first", 409)
        try:
            return json.loads(AESGCM(self.github_app.secret_key(c)).decrypt(row["nonce"], row["ciphertext"], AAD + source.encode()))
        except Exception:
            raise Problem("directory_unavailable", "The stored directory credentials could not be decrypted", 503) from None

    def hint(self, c, source):
        row = c.execute("SELECT hint FROM directory_credentials WHERE source=?", (source,)).fetchone()
        return row["hint"] if row else ""

    # -- view
    def view(self, c):
        cfg = load(c)
        scim = cfg["scim"] or {}
        return {"source": cfg["source"], "filter": cfg["filter"], "interval_minutes": cfg["interval_minutes"],
                "mass_leave_limit": cfg["mass_leave_limit"], "confirmed": cfg["confirmed"], "revision": cfg["revision"],
                "last": cfg["last"],
                "credentials": {s: {"configured": bool(self.hint(c, s)), "hint": self.hint(c, s)} for s in ("google", "entra")},
                "scim": {"enabled": bool(scim.get("token_hash")), "created": scim.get("created", ""),
                         "url": self.settings.public_url + "/scim/v2"}}

    def configure(self, c, actor, body):
        cfg = load(c)
        if cfg["revision"] != body.expected_revision:
            raise Problem("conflict", "Directory sync changed since you opened it; reload and try again", 409)
        if body.source not in SOURCES:
            raise Problem("source", "Choose Google Workspace, Microsoft Entra ID, SCIM, or none", 422)
        if body.credentials is not None and body.source in ("google", "entra"):
            blob, hint = _credential_blob(body.source, body.credentials)
            self.put_credentials(c, actor, body.source, blob, hint)
        elif body.source in ("google", "entra") and not self.hint(c, body.source):
            raise Problem("credentials", "Add this source's credentials first", 422)
        if body.source == "scim" and not (cfg["scim"] or {}).get("token_hash"):
            raise Problem("credentials", "Create the SCIM token first", 422)
        flt = {"groups": _lines(body.filter.groups), "org_units": _lines(body.filter.org_units),
               "domains": [d.lstrip("@") for d in _lines(body.filter.domains, lower=True)]}
        after = {**cfg, "source": body.source, "filter": flt, "interval_minutes": body.interval_minutes,
                 "mass_leave_limit": body.mass_leave_limit, "revision": cfg["revision"] + 1,
                 "updated": H.now(), "updated_by": actor,
                 # A different source or scope is a different first sync.
                 "confirmed": cfg["confirmed"] and cfg["source"] == body.source and cfg["filter"] == flt}
        save(c, after)
        H.event(c, actor, "directory.configured", "",
                {"source": body.source, "filter": flt, "interval_minutes": body.interval_minutes,
                 "mass_leave_limit": body.mass_leave_limit, "credentials_changed": body.credentials is not None})
        return after["revision"]

    def new_scim_token(self, c, actor):
        import secrets
        from .store import digest
        token = "scim_" + secrets.token_urlsafe(32)
        cfg = load(c)
        cfg["scim"] = {"token_hash": digest(token), "created": H.now()}
        save(c, cfg)
        H.event(c, actor, "directory.scim_token_created", "", {})
        return token

    # -- pull
    def fetch(self, source):
        if self.settings.rehearsal:
            raise Problem("rehearsal", "Directory sync is off in a rehearsal: it would read the company's directory", 409)
        with self.store.read() as c:
            cfg, creds = load(c), None
            if cfg["source"] != source or source not in Sources.FETCH:
                raise Problem("source", "Choose Google Workspace or Microsoft Entra ID first", 409)
            creds = self.credentials(c, source)
        return Sources.FETCH[source](creds, cfg["filter"]), creds

    def preview(self):
        with self.store.read() as c:
            cfg = load(c)
        source = cfg["source"]
        records, _ = self.fetch(source)
        with self.store.read() as c:
            from .views import roster
            found = plan(roster(c), records, source, Access.load_owner(c, self.settings)["email"], True)
        return self._describe(cfg, found)

    @staticmethod
    def _describe(cfg, found):
        needs = {"first": not cfg["confirmed"] and any(found[k] for k in ("adds", "updates", "restores", "leaves")),
                 "mass_leave": len(found["leaves"]) > cfg["mass_leave_limit"]}
        return {"plan": found, "counts": counts(found), "needs_confirmation": needs,
                "mass_leave_limit": cfg["mass_leave_limit"]}

    def run(self, actor, confirm=False, plan_hash="", auto=False):
        """Fetch, plan and (when the rules allow) apply. Returns the described plan plus `applied`."""
        with self.lock:
            with self.store.read() as c:
                cfg = load(c)
            source = cfg["source"]
            try:
                records, creds = self.fetch(source)
            except Problem as exc:
                self._note(source, {"ok": False, "error": exc.detail, "auto": auto})
                raise
            result = {}

            def work(c):
                from .views import roster
                cfg = load(c)
                live = roster(c)
                found = plan(live, records, source, Access.load_owner(c, self.settings)["email"], True)
                described = self._describe(cfg, found)
                need = described["needs_confirmation"]
                held = (need["first"] or need["mass_leave"]) and not (confirm and plan_hash == found["hash"])
                if held:
                    if confirm:
                        described["stale"] = True       # they confirmed a plan that has since changed
                    if auto and cfg["last"].get("held_hash") != found["hash"]:
                        H.event(c, actor, "directory.sync_held", "", {"source": source, "counts": described["counts"], "needs": need})
                    cfg["last"] = {"at": H.now(), "ok": True, "auto": auto, "held": True, "held_hash": found["hash"],
                                   "counts": described["counts"]}
                    save(c, cfg)
                    return {**described, "applied": False}
                done, touched = apply(c, actor, source, found, live)
                cfg["confirmed"] = cfg["confirmed"] or bool(confirm)
                cfg["last"] = {"at": H.now(), "ok": True, "auto": auto, "held": False, "counts": described["counts"], "applied": done}
                save(c, cfg)
                H.event(c, actor, "directory.synced", "", {"source": source, "auto": auto, "applied": done,
                                                           "protected": [x["email"] for x in found["protected"]]})
                result["touched"] = touched
                return {**described, "applied": True, "done": done}
            out = self._tx(work)
            self._photos(source, creds, records, result.get("touched") or [])
            return out

    def _tx(self, work):
        with self.store.transaction() as c:
            return work(c)

    def _note(self, source, last):
        with self.store.transaction() as c:
            cfg = load(c)
            cfg["last"] = {"at": H.now(), "source": source, **last}
            save(c, cfg)

    def _photos(self, source, creds, records, touched):
        by_email = {r["email"]: r for r in records}
        for email in touched[:PHOTO_BUDGET]:
            if email not in by_email or people_photos.cached(self.settings, email):
                continue
            fetched = Sources.photo(source, creds, by_email[email])
            if fetched:
                people_photos.store(self.settings, email, fetched[0], fetched[1])

    # -- the interval
    def due(self, c):
        cfg = load(c)
        if cfg["source"] not in Sources.FETCH or not cfg["interval_minutes"] or not cfg["confirmed"]:
            return False
        try:
            last = datetime.fromisoformat(str(cfg["last"].get("at") or ""))
        except ValueError:
            return True
        return datetime.now(timezone.utc) - last >= timedelta(minutes=cfg["interval_minutes"])

    def tick(self):
        with self.store.read() as c:
            if not self.due(c):
                return
        try:
            self.run("directory", auto=True)
        except Problem:
            pass                                           # recorded in `last`; try again next interval


def install_directory(app, settings, store, mutate):
    service = app.state.directory = Directory(settings, store, app.state.github_app)

    def owner(request, what):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner " + what, 403)
        return who

    def refresh_access():
        with store.read() as c:
            app.state.auth.sync_access(c)

    @app.get("/api/v2/directory")
    def directory_view(request: Request):
        owner(request, "manages directory sync")
        with store.read() as c:
            return service.view(c)

    @app.put("/api/v2/directory")
    def directory_save(request: Request, body: DirectoryUpdate):
        who = owner(request, "manages directory sync")
        return mutate(request, body, lambda c: {"revision": service.configure(c, who.actor, body)})

    @app.post("/api/v2/directory/scim-token")
    def directory_scim_token(request: Request, body: Empty):
        who = owner(request, "manages directory sync")
        personal_tokens.no_minting(who, "Make a SCIM token")
        return mutate(request, body, lambda c: {"token": service.new_scim_token(c, who.actor)})

    @app.post("/api/v2/directory/preview")
    def directory_preview(request: Request, body: Empty):
        owner(request, "manages directory sync")
        return service.preview()

    @app.post("/api/v2/directory/sync")
    def directory_sync(request: Request, body: SyncRequest):
        who = owner(request, "manages directory sync")
        out = service.run(who.actor, confirm=body.confirm, plan_hash=body.plan_hash)
        refresh_access()
        return out
