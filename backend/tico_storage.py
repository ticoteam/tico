"""Tico file hosting (Gateway): a company's files in a bucket Tico runs, served from its ticofiles.com address.

It is the company-bucket option (backend/company_storage.py) pointed at Tico's bucket and the company's own folder
in it, with short-lived keys and the address handed over by Tico HQ instead of typed by the owner. Whether the company
uses it is HQ's word (the owner buys and stops it on the Gateway page, tico-services); the app asks with its gateway
key and applies the answer without a restart.

The handoff, agreed with HQ on Dev 315/316 (tico-services gateway/files.py), each call with
`Authorization: Bearer <gateway key>`:
- `GET <gateway>/files`: at start and hourly, every 5 minutes while the company has file hosting, and 10 minutes
  before its keys expire (`sync_later`). HQ answers `{state, writes, reason, access, address, bucket, region, prefix,
  credentials {access_key_id, secret_access_key, session_token, expires}, signing {key_id, secret}, keep_until}`.
- `POST <gateway>/files/total` `{"total_bytes": n}`, hourly: what this server stores, for HQ's estimate before buying.
The state the app keeps (`STATES`) from HQ's state, writes and reason:
- `off`: not bought, or HQ keeps the files no longer. Nothing is read from Tico's bucket; a company that never had it
  has nothing saved.
- `on` (writes): new files go to Tico's bucket. Files already stored keep opening where they are; Move existing
  files copies them over (`Move`).
- `credits_out` (on, no writes, out_of_credit): files in Tico's bucket keep opening; new files go to the company's
  own storage (its bucket, else the server's storage). If that storage can't take a file, the upload is refused with
  a plain reason (`PAUSED`).
- `stopped`: the owner stopped it. Reads and writes as for `credits_out`; the export and Move files back are offered.
  HQ keeps the files until `keep_until`, emails the owner and deletes them (Dev 316); the app deletes nothing.
Keys last an hour at most. A new key for the same place is swapped in where it is used (`Blobs.rekey`): nothing is
checked again and a Move under way carries on. A new place is checked first, while it takes new files, by writing
and reading back a small object (company_storage.check). Keys are sealed with the credential vault like the
company's own; no read returns them.

Reads. A picture, video or audio file whose bytes are in Tico's bucket is not streamed through this server: after
the usual access check the reader is sent (302) to a link on the company's address that lasts four minutes
(`edge_link`), which Tico's edge checks before serving it from the bucket:
    https://<address><path>?e=<unix expiry>&k=<signing key id>&s=<sig>
`<path>` is the file's key below the company's folder (`/blobs/<2 hex>/<sha256>`), and `<sig>` base64url (no
padding) of HMAC-SHA256(signing secret, "<address>\\n<path>\\n<e>"). A file is linked only once the bucket was seen
to hold it as this server would serve it (size, hash, type and disposition); anything else streams from this server,
which reads it through the same address (`edge_read`), as it does for the export and Move files back. So every byte
that leaves Tico's bucket passes Tico's edge, which counts it as delivered to the company (Dev 316 meters the edge's
logs by address); nothing is read from the bucket directly. A copy Move existing files makes there is checked without
reading it back (S3's own SHA-256 of what it received, `copy_one`): the company is not charged delivery for Tico's
own check.

A 404 or `not_found` from HQ means it does not offer file hosting yet: nothing changes.

Pages and SVG (Dev 458, spec test 1b). A page or SVG held in Tico's bucket also gets its cleaned copy there, beside it
(`visual_link`): the bytes visuals.serve would show (visuals.clean, and the height reporter for a page) at
`/visuals/<2 hex>/<name>`, typed `text/html; charset=utf-8` or `image/svg+xml`, inline, with the content policy's two
slots in its metadata (`tico-visual-policy-version: 1`, `tico-visual-ancestors`, and `tico-visual-script` for a page),
as agreed with DevOps on Dev 457 (docs/files.md, infra/files-edge). The edge builds the policy from its fixed templates
and those slots, so nothing written there can lift the sandbox. `<name>` is a hash of the original's digest, the kind,
the slots and `VISUAL_COPY`, so a new reporter or a new app address gets a new copy and an old one is never
overwritten. The copy is made when the file is uploaded there (`Blobs._landing`), when Move existing files copies it
there (`copy_one`), or else on its first view, and is checked like a moved file: S3's SHA-256 of what it received,
size, type and disposition, and the metadata, by a body-less HEAD. After the usual access check a reader is sent to a
four-minute link to it; the app's own address never serves a page or SVG. A first view reads the original once through
the company's address (counted as delivery, as the view itself would be); uploads and moves have the bytes in hand. A
place that no longer takes new files (credits out, stopped) makes no copy: such a file with none yet stays a card.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from clients import gateway as G

from . import models as M
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS tico_storage(
 id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL CHECK(state IN ('off','on','credits_out','stopped')),
 bucket TEXT NOT NULL DEFAULT '', prefix TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
 access_key_id TEXT NOT NULL DEFAULT '', secret TEXT NOT NULL DEFAULT '', expires_at TEXT,
 signing_key_id TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', keep_until TEXT,
 changed TEXT, checked TEXT, error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS storage_moves(
 id TEXT PRIMARY KEY, target TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('running','done','failed','stopped')),
 started_at TEXT NOT NULL, started_by TEXT NOT NULL, finished_at TEXT,
 estimate_files INTEGER NOT NULL, estimate_bytes INTEGER NOT NULL,
 moved_files INTEGER NOT NULL DEFAULT 0, moved_bytes INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS storage_moves_one_running ON storage_moves(state) WHERE state='running';
"""
PATH, TOTAL = "/files", "/files/total"
CONTEXT = "tico_storage:keys"
STATES = ("off", "on", "credits_out", "stopped")
HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ticofiles\.com$")
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")
KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LABEL = "Tico file hosting"
PAUSED = ("Gateway credits have run out, so new files go to the company's own storage, and it can't take this one "
          "right now ({reason}). Add Gateway credits, or fix the company's storage in Settings › General, and try again.")
# How often HQ is asked while the company has file hosting, and how long before its keys expire.
REFRESH, EARLY = 300, 600
# How long an edge link lasts, and how long a file the bucket doesn't hold (yet) streams before it is looked for again.
# The edge refuses an expiry more than 300 seconds ahead of its own clock (infra/files-edge/request.js), so a link
# lasts a minute less: a server clock running a little ahead never makes a link the edge refuses.
LINK_SECONDS, MISS_SECONDS = 240, 60
# How this server fetches from the edge (`edge_read`); a test hands in its own.
fetch = urllib.request.urlopen
LOG = logging.getLogger("tico.storage")
# Each live server's file store, for the scheduler (backend/scheduler.py), which has only the store; when HQ is due
# next (monotonic, None: hourly only); and whether an answer is being fetched now.
_live, _due, _asking = {}, {}, set()
_lock = threading.Lock()


class Move(M.Contract):
    """Copy every file not yet in the place new files go to. `expect_bytes` is the estimate the owner was shown."""
    expect_bytes: int = Field(ge=0)


# ---------------------------------------------------------------------------------------------- saved state
def row(c):
    try:
        found = c.execute("SELECT * FROM tico_storage WHERE id=1").fetchone()
    except sqlite3.OperationalError:          # a database from before file hosting, read by a backup
        return None
    return dict(found) if found else None


def _sealed(c, vault, saved):
    """((access key id, secret, session token), signing secret) from the sealed record."""
    secret, token, signing = json.loads(vault.cipher.unseal(c, CONTEXT, saved["secret"]))
    return (saved["access_key_id"], secret, token), signing


def _location(saved):
    return saved["bucket"] + ("/" + saved["prefix"] if saved["prefix"] else "")


def place(c, vault):
    """(the Tico place {bucket, prefix, region, keys} or None, its state). The place is None until HQ has handed over
    a bucket, and again once HQ says `off`; in between it stays whatever the state, so files written there keep
    opening."""
    saved = row(c)
    if not saved or saved["state"] == "off" or not saved["bucket"] or not saved["secret"]:
        return None, saved["state"] if saved else "off"
    return ({"bucket": saved["bucket"], "prefix": saved["prefix"], "region": saved["region"],
             "keys": _sealed(c, vault, saved)[0]}, saved["state"])


def edge(c, vault):
    """What `edge_link` signs with ({location, address, key_id, secret}), or None without an address or key."""
    saved = row(c)
    if not saved or saved["state"] == "off" or not saved["bucket"] or not saved["secret"] or not saved["address"]:
        return None
    signing = _sealed(c, vault, saved)[1]
    if not signing or not saved["signing_key_id"]:
        return None
    return {"location": _location(saved), "address": saved["address"], "key_id": saved["signing_key_id"],
            "secret": signing}


def visual_address(c):
    """(the company's files origin, whether new pages get a copy there): `https://<address>` while Tico's bucket
    holds files and links can be signed (`edge`), else "". Copies are made only while it takes new files (`on`); in
    the other states a page that has one still shows from it. For the client config (onboarding.config_view)."""
    saved = row(c)
    if not saved or saved["state"] == "off" or not saved["bucket"] or not saved["secret"] \
            or not saved["signing_key_id"] or not HOST.match(saved["address"] or ""):
        return "", False
    return "https://" + saved["address"], saved["state"] == "on"


def active_location(c):
    """{bucket, prefix, region} of Tico's place while new files go there, or None: for Health."""
    saved = row(c)
    if not saved or saved["state"] != "on" or not saved["bucket"]:
        return None
    return {"bucket": saved["bucket"], "prefix": saved["prefix"], "region": saved["region"]}


def arrange(active, earlier, hosting):
    """Where files are written and read, given the company's own places (company_storage.places) and Tico's.
    (write place or None for the server's own storage, earlier places newest first, copy this disk's files, paused)."""
    tico, state = hosting
    if tico and state == "on":
        # The company's own bucket becomes a place files are still read from; this disk's files stay where they are
        # until the owner moves them (each one moved is billed storage).
        return tico, ([active] if active else []) + list(earlier), False, False
    if tico:
        # Credits out or stopped: the company's own storage takes new files; Tico's still serves what it holds.
        return active, [tico] + list(earlier), True, state == "credits_out"
    return active, list(earlier), True, False


def totals(c):
    """{files, bytes}: every distinct file this server stores, every version, picture still and recording included."""
    usage = c.execute("SELECT COUNT(*) AS files, COALESCE(SUM(size),0) AS bytes FROM "
                      "(SELECT digest, MAX(size) AS size FROM blobs GROUP BY digest)").fetchone()
    return {"files": usage["files"], "bytes": usage["bytes"]}


def view(c, settings):
    saved = row(c)
    if not saved:
        return {"state": "off", "label": LABEL}
    return {"state": saved["state"], "label": LABEL, "address": saved["address"], "location": _location(saved),
            "keep_until": saved["keep_until"], "checked": saved["checked"], "changed": saved["changed"],
            "error": saved["error"]}


# ---------------------------------------------------------------------------------------------- HQ's word
def _when(value, field):
    if not isinstance(value, str) or not STAMP.match(value):
        raise ValueError(field)
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def parse(answer, now=None):
    """The checked fields of HQ's answer, or ValueError naming the field. Never echoes a secret."""
    hq, writes, reason, access = (answer.get(k) for k in ("state", "writes", "reason", "access"))
    if hq not in ("off", "on", "stopped"):
        raise ValueError("state")
    if access not in ("none", "read_only", "read_write") or (access == "none") != (hq == "off"):
        raise ValueError("access")
    if not isinstance(writes, bool) or writes != (hq == "on" and access == "read_write"):
        raise ValueError("writes")
    if reason not in (None, "out_of_credit", "stopped") or (reason is None) != (writes or hq == "off"):
        raise ValueError("reason")
    keep = answer.get("keep_until")
    if keep is not None:
        _when(keep, "keep_until")
    state = "off" if hq == "off" else "on" if writes else "stopped" if hq == "stopped" else "credits_out"
    out = {"state": state, "address": "", "keep_until": keep}
    if hq == "off":
        return out
    # Without an address and a signing key, Tico's bucket could only be read directly, past the edge that meters it.
    address = str(answer.get("address") or "").strip().lower().removeprefix("https://").rstrip("/")
    if not HOST.match(address):
        raise ValueError("address")
    from .company_storage import ACCESS_KEY, BUCKET, PREFIX, REGION
    bucket = str(answer.get("bucket") or "").strip()
    region, prefix = str(answer.get("region") or "").strip().lower(), str(answer.get("prefix") or "").strip().strip("/")
    if not BUCKET.match(bucket) or ".." in bucket:
        raise ValueError("bucket")
    if not REGION.match(region):
        raise ValueError("region")
    # A company's own folder: Tico's bucket is shared, so a place at its root would read other companies' files.
    if not prefix or not PREFIX.match(prefix) or any(part in ("", ".", "..") for part in prefix.split("/")):
        raise ValueError("prefix")
    given = answer.get("credentials")
    given = given if isinstance(given, dict) else {}
    key, secret = str(given.get("access_key_id") or ""), str(given.get("secret_access_key") or "")
    token = str(given.get("session_token") or "")
    if not ACCESS_KEY.match(key) or not secret or len(secret) > 128 or not token or len(token) > 4096:
        raise ValueError("credentials")
    # HQ's wire name (tico-services billing/files_aws.py credentials()); the stored column is expires_at.
    expires = _when(given.get("expires"), "credentials")
    if expires <= (now or datetime.now(timezone.utc)):
        raise ValueError("credentials")
    signing = answer.get("signing")
    if (not isinstance(signing, dict) or not KEY_ID.match(str(signing.get("key_id") or ""))
            or not isinstance(signing.get("secret"), str) or not 32 <= len(signing["secret"]) <= 256):
        raise ValueError("signing")
    signing = (signing["key_id"], signing["secret"])
    out.update(address=address, bucket=bucket, region=region, prefix=prefix, keys=(key, secret, token),
               expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"), signing=signing)
    return out


def ask(key, base, opener=None):
    """(status, answer, code) from HQ's file-hosting endpoint."""
    request = urllib.request.Request(G.safe_base(base) + PATH, method="GET", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with (opener or G._send)(request, timeout=G.TIMEOUT) as response:
            payload = json.load(response)
        return getattr(response, "status", 200), payload if isinstance(payload, dict) else {}, ""
    except urllib.error.HTTPError as exc:
        try:
            code = G._code(exc.read())
        except Exception:
            code = ""
        return exc.code, {}, code or str(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, {}, "unreachable"


def tell(key, base, total, opener=None):
    """Hand HQ this server's stored total for its estimate. Best effort: (status, answer, code)."""
    return G._post(G.safe_base(base) + TOTAL, {"total_bytes": int(total)}, {"Authorization": "Bearer " + key},
                   G.TIMEOUT, opener)


def _plan(store, saved):
    """When HQ is asked next: every REFRESH while the company has file hosting, sooner when its keys end first."""
    at = None
    if saved and saved["state"] != "off":
        wait = REFRESH
        if saved["expires_at"]:
            left = (datetime.fromisoformat(saved["expires_at"]) - datetime.now(timezone.utc)).total_seconds() - EARLY
            wait = max(60, min(wait, left))
        at = time.monotonic() + wait
    with _lock:
        _due[id(store)] = at


def sync(store, vault, blobs, settings=None, opener=None, report=False):
    """Ask HQ and apply its answer; with `report`, also hand it this server's total. Returns the view, or None when
    this server has no gateway key."""
    from . import gateway_connection
    settings = settings or store.settings
    with store.read() as c:
        (key, base, _), usage = gateway_connection.target(c, vault, settings), totals(c)
    if not key or settings.rehearsal or settings.demo:
        return None
    try:
        status, answer, code = ask(key, base, opener)
    except ValueError:
        status, answer, code = 0, {}, "bad_url"
    if status == 404 or code == G.NOT_FOUND:
        with store.read() as c:
            _plan(store, row(c))
            return view(c, settings)
    if report and status == 200:
        try:
            tell(key, base, usage["bytes"], opener)
        except ValueError:
            pass
    error = "" if status == 200 else f"{LABEL}: Tico HQ didn't answer ({code or status}); files stay where they are"
    parsed = None
    if not error:
        try:
            parsed = parse(answer)
        except ValueError as exc:
            error = f"{LABEL}: Tico HQ's answer has an unusable {exc}; files stay where they are"
    with store.read() as c:
        saved = row(c)
    same = bool(parsed and saved and parsed.get("keys") and saved["state"] != "off" and saved["secret"]
                and all(saved[k] == parsed[k] for k in ("bucket", "prefix", "region")))
    checked = False
    if parsed and parsed["state"] == "on" and parsed.get("keys") and not (same and saved["state"] == "on"):
        # New files go there from now on: only once a small object was written and read back with these keys.
        from .company_storage import check
        try:
            check(settings, parsed["bucket"], parsed["prefix"], parsed["region"], parsed["keys"])
            checked = True
        except Problem as exc:
            error, parsed = f"{LABEL}: {exc.detail}; new files stay where they go now", None
    now, moved = H.now(), False
    with store.transaction() as c:
        saved = row(c)
        if parsed is None:
            if saved:
                c.execute("UPDATE tico_storage SET checked=?, error=? WHERE id=1", (now, error))
            elif error:
                LOG.warning("%s", error)
        elif parsed["state"] == "off" and not saved:
            pass
        else:
            fields = {"state": parsed["state"], "address": parsed["address"], "keep_until": parsed["keep_until"],
                      "checked": now, "error": ""}
            if parsed.get("keys"):
                signing = parsed["signing"] or ("", "")
                fields.update(bucket=parsed["bucket"], prefix=parsed["prefix"], region=parsed["region"],
                              access_key_id=parsed["keys"][0], expires_at=parsed["expires_at"], signing_key_id=signing[0],
                              secret=vault.cipher.seal(c, CONTEXT, json.dumps([*parsed["keys"][1:], signing[1]])))
            elif parsed["state"] == "off":
                # HQ keeps the files no longer: nothing is read from its bucket, and its keys are dropped.
                fields.update(access_key_id="", secret="", expires_at=None, signing_key_id="")
            if not saved:
                c.execute("INSERT INTO tico_storage(id,state) VALUES(1,'off')")
            # Where files go changes only with the state or the place: an answer with new keys for the same place
            # swaps them in (`Blobs.rekey`) and leaves the file store alone, so uploads and a Move carry on.
            moved = not saved or saved["state"] != parsed["state"] or not same
            if moved:
                fields["changed"] = now
                H.event(c, "system:gateway", "storage.tico", parsed["state"],
                        {"from": saved["state"] if saved else "off", "address": parsed["address"]})
            c.execute("UPDATE tico_storage SET " + ",".join(k + "=?" for k in fields) + " WHERE id=1", tuple(fields.values()))
        saved = row(c)
    if moved:
        from . import company_storage
        company_storage.apply(store, vault, blobs, checked=checked)
    elif parsed and parsed.get("keys"):
        blobs.rekey(_location(parsed), parsed["keys"])
        with store.read() as c:
            blobs.edge = edge(c, vault)
    _plan(store, saved)
    with store.read() as c:
        return view(c, settings)


def sync_later(store, due_only=False, report=False):
    """`sync` on a thread, for the scheduler: hourly with `report`, and each tick with `due_only`, which asks only
    when `_plan` said so. Nothing when this store has no live file store, or an answer is being fetched already."""
    with _lock:
        live = _live.get(id(store))
        if not live or live[0] is not store or id(store) in _asking:
            return None
        if due_only:
            at = _due.get(id(store))
            if at is None or time.monotonic() < at:
                return None
            _due[id(store)] = time.monotonic() + 60       # a failed ask is tried again in a minute
        _asking.add(id(store))
    _, vault, blobs = live

    def run():
        try:
            sync(store, vault, blobs, report=report)
        except Exception:
            LOG.exception("Tico file hosting check failed")
        finally:
            with _lock:
                _asking.discard(id(store))
    thread = threading.Thread(target=run, name="tico-file-hosting", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------------------------- reads
def edge_link(blobs, digest, size, mime, now=None):
    """A four-minute link to the file on the company's address, or None to stream it from here. Only pictures (not
    SVG), video and audio, and only once the bucket was seen to hold the file as this server would serve it."""
    found = getattr(blobs, "edge", None)
    if not found or not mime.startswith(("image/", "video/", "audio/")) or "svg" in mime or "xml" in mime:
        return None
    if not _held(blobs, found["location"], digest, size, mime):
        return None
    return _signed(found, "/" + blobs.key(digest), now)


def _signed(found, path, now=None):
    expires = int(now if now is not None else time.time()) + LINK_SECONDS
    mac = hmac.new(found["secret"].encode(), f"{found['address']}\n{path}\n{expires}".encode(), hashlib.sha256)
    sig = base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()
    return f"https://{found['address']}{path}?" + urllib.parse.urlencode({"e": expires, "k": found["key_id"], "s": sig})


class EdgeRefused(Exception):
    """The edge answered but would not serve the file (missing, or a refused link). It is an answer, so the place
    counts as reachable (Blobs._open_earlier skips only a place that gave none)."""
    def __init__(self, status):
        super().__init__(f"{LABEL} answered {status}")
        self.response = {"Error": {"Code": str(status)}}


def edge_read(found, path, span=None):
    """The body of the file at `path` (below the company's folder) fetched by this server through the company's
    address on a fresh link, with `span`'s Range if any: Blobs._read, for every read of Tico-held bytes."""
    request = urllib.request.Request(_signed(found, path), headers=dict(span or {}))
    try:
        return fetch(request, timeout=60)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise EdgeRefused(exc.code) from None


def _held(blobs, location, digest, size, mime):
    """Whether Tico's bucket holds `digest` whole, typed `mime` and with the disposition this server would give it.
    A yes is kept (the bytes never change); a no is looked at again after MISS_SECONDS."""
    from .blobs import disposition
    seen = blobs._edge_seen
    known = seen.get((location, digest, mime))
    if known is not None and (known is True or time.monotonic() - known < MISS_SECONDS):
        return known is True
    at = blobs.place_at(location)
    held = False
    if at is not None:
        settings, sources = at
        try:
            head = sources.read_s3("head_object", Bucket=settings.blob_bucket, Key=blobs.s3_key(digest, settings),
                                   ChecksumMode="ENABLED")[0]
            checksum = base64.b64encode(bytes.fromhex(digest)).decode()
            held = (head.get("ContentLength") == size
                    and (head.get("ChecksumSHA256") == checksum or head.get("Metadata", {}).get("sha256") == digest)
                    and (head.get("ContentType") or "").split(";", 1)[0].strip().lower() == mime
                    and (head.get("ContentDisposition") or "").split(";", 1)[0].strip().lower() == disposition(mime))
        except Exception:
            held = False
    if len(seen) > 50_000:
        seen.clear()
    seen[(location, digest, mime)] = True if held else time.monotonic()
    return held


# ---------------------------------------------------------------------------------------------- pages and SVG
# The stored type of each kind's copy; the edge sends a 404 for any other (infra/files-edge/response.js).
VISUAL_TYPES = {"page": "text/html; charset=utf-8", "svg": "image/svg+xml"}
# Raise when visuals.clean or visuals.with_reporter change what a copy holds: every copy is then made again.
VISUAL_COPY = 1
# The frame-ancestors sources the edge keeps (docs/files.md): the app writes only those, at most 8.
ANCESTOR = re.compile(r"^(?:'self'|https://[a-z0-9.-]+(?::[0-9]{1,5})?|http://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?)$")


def visual_kind(mime, size):
    """"page" or "svg" for a version the copy is made for (by its stored type, within visuals.LIMITS), else None."""
    from .visuals import kind_of
    kind = kind_of(mime, size)
    return kind if kind in VISUAL_TYPES else None


def _visual(digest, kind, ancestors):
    """(path below the company's folder, metadata) of the cleaned copy of `digest` for these frame ancestors."""
    from .visuals import REPORTER_HASH
    meta = {"tico-visual-policy-version": "1",
            "tico-visual-ancestors": " ".join([a for a in dict.fromkeys(ancestors) if ANCESTOR.fullmatch(a)][:8])}
    if kind == "page":
        meta["tico-visual-script"] = "'" + REPORTER_HASH + "'"
    name = hashlib.sha256("\n".join(["tico-visual", str(VISUAL_COPY), kind, digest, meta["tico-visual-ancestors"],
                                      meta.get("tico-visual-script", "")]).encode()).hexdigest()
    return f"/visuals/{name[:2]}/{name}", meta


def _visual_ok(head, kind, meta, sha=None, size=None):
    """Whether a HEAD of a copy shows it whole as made: S3's SHA-256 of what it received is the one it was written
    with (`sha`, else the one in its metadata), and its type, disposition and policy slots are this kind's."""
    stored = head.get("Metadata") or {}
    sha = sha or stored.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return False
    return (head.get("ChecksumSHA256") == base64.b64encode(bytes.fromhex(sha)).decode()
            and stored.get("sha256") == sha and (size is None or head.get("ContentLength") == size)
            and (head.get("ContentType") or "").lower() == VISUAL_TYPES[kind]
            and (head.get("ContentDisposition") or "").lower() == "inline"
            and all(stored.get(k) == v for k, v in meta.items()) and set(meta) >= {
                k for k in stored if k.startswith("tico-visual-")})


def _visual_key(settings, path):
    return (settings.blob_prefix + "/" if settings.blob_prefix else "") + path.lstrip("/")


def _visual_held(blobs, found, path, kind, meta):
    """Whether Tico's bucket holds the copy at `path` as made; a yes is kept, a no looked at again after MISS_SECONDS."""
    seen, mark = blobs._edge_seen, (found["location"], path)
    known = seen.get(mark)
    if known is not None and (known is True or time.monotonic() - known < MISS_SECONDS):
        return known is True
    at = blobs.place_at(found["location"])
    held = False
    if at is not None:
        settings, sources = at
        try:
            held = _visual_ok(sources.read_s3("head_object", Bucket=settings.blob_bucket,
                                              Key=_visual_key(settings, path), ChecksumMode="ENABLED")[0], kind, meta)
        except Exception:
            held = False
    if len(seen) > 50_000:
        seen.clear()
    seen[mark] = True if held else time.monotonic()
    return held


def _writable(blobs, found):
    """The place new files go to now, when it is Tico's (copies are made only while it takes new files), else None."""
    from .blobs import location_of
    at = blobs.target()
    return at if at.settings.blob_bucket and location_of(at.settings) == found["location"] else None


def make_visual(blobs, digest, kind, data, ancestors):
    """Write the cleaned copy of the page or SVG `data` (the bytes of `digest`) beside it in Tico's bucket and check
    it there without reading it back. Whether it is held as made; never raises."""
    from .visuals import clean, with_reporter
    from .live_blocks import has_live
    if kind == "page" and has_live(data.decode("utf-8", errors="replace")):
        return False            # a page with live blocks is filled in for each reader: it never has a shared copy
    found = getattr(blobs, "edge", None)
    at = _writable(blobs, found) if found else None
    if at is None:
        return False
    path, meta = _visual(digest, kind, ancestors)
    try:
        body, _ = clean(data, kind)
        if kind == "page":
            body = with_reporter(body)
        sha = hashlib.sha256(body).hexdigest()
        key = _visual_key(at.settings, path)
        s3 = at.s3 if at.s3 is not None else at.sources.client_for(at.sources.kind)
        try:
            s3.put_object(Bucket=at.settings.blob_bucket, Key=key, Body=body, ContentType=VISUAL_TYPES[kind],
                          ContentDisposition="inline", CacheControl="private, no-cache", ServerSideEncryption="AES256",
                          Metadata={**meta, "sha256": sha}, IfNoneMatch="*",
                          ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode())
        except Exception as exc:
            # Made already (another server, or a view racing this one): the check below says whether it is whole.
            if (getattr(exc, "response", None) or {}).get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
                raise
        head = (at.s3.head_object if at.s3 is not None else
                lambda **kw: at.sources.read_s3("head_object", **kw)[0])(
            Bucket=at.settings.blob_bucket, Key=key, ChecksumMode="ENABLED")
        held = _visual_ok(head, kind, meta, sha, len(body))
    except Exception:
        LOG.warning("%s: the cleaned copy of a page or SVG could not be stored", LABEL, exc_info=True)
        held = False
    if held:
        blobs._edge_seen[(found["location"], path)] = True
    return held


def visual_ready(blobs, digest, size, mime, ancestors):
    """Whether a page or SVG version shows inline from the company's address: its file is held in Tico's bucket, and
    its cleaned copy is there or can be made now. A card (visuals.decorate) otherwise."""
    found = getattr(blobs, "edge", None)
    kind = visual_kind(mime, size) if found else None
    if not kind or not _held(blobs, found["location"], digest, size, mime):
        return False
    path, meta = _visual(digest, kind, ancestors)
    return _writable(blobs, found) is not None or _visual_held(blobs, found, path, kind, meta)


def visual_link(blobs, digest, size, mime, ancestors, now=None):
    """A four-minute link to the cleaned copy of a page or SVG version on the company's address, making the copy first
    when it is missing (`make_visual`, from the file read through that address), or None: then it can't show inline
    from there. The caller has checked access already."""
    found = getattr(blobs, "edge", None)
    kind = visual_kind(mime, size) if found else None
    if not kind or not _held(blobs, found["location"], digest, size, mime):
        return None
    path, meta = _visual(digest, kind, ancestors)
    if not _visual_held(blobs, found, path, kind, meta):
        if _writable(blobs, found) is None:
            return None
        try:
            data = blobs.get(digest)
        except Exception:
            return None
        if hashlib.sha256(data).hexdigest() != digest or not make_visual(blobs, digest, kind, data, ancestors):
            return None
    return _signed(found, path, now)


def landed(blobs, digest, size, mime, data):
    """A page or SVG just uploaded to Tico's bucket (Blobs._landing) or moved there (`copy_one`): its copy, now."""
    kind = visual_kind(mime, size)
    if kind and getattr(blobs, "visual_ancestors", None) is not None:
        make_visual(blobs, digest, kind, data, blobs.visual_ancestors)


# ---------------------------------------------------------------------------------------------- move
def _target(at):
    """(label of the place `at` (Blobs.target), its bucket location or '' for this computer's disk)."""
    from .blobs import location_of
    return (location_of(at.settings),) * 2 if at.settings.blob_bucket else ("this server's disk", "")


def pending(c, blobs, at=None):
    """[(digest, size, content type)] of every file not yet verified in the place new files go to (or `at`)."""
    _, location = _target(at or blobs.target())
    rows = c.execute("SELECT digest, MAX(size) AS size, MAX(content_type) AS content_type FROM blobs GROUP BY digest "
                     "ORDER BY digest").fetchall()
    if location:
        known = {r[0] for r in c.execute("SELECT digest FROM blob_locations WHERE bucket=?", (location,))}
        known |= {digest for place, digest in blobs._landed if place == location}
        return [(r["digest"], r["size"], r["content_type"]) for r in rows if r["digest"] not in known]
    return [(r["digest"], r["size"], r["content_type"]) for r in rows
            if not (blobs.directory / blobs.key(r["digest"])).is_file()]


def estimate(c, blobs):
    at = blobs.target()
    items = pending(c, blobs, at)
    label, _ = _target(at)
    size = sum(item[1] for item in items)
    return {"to": label, "files": len(items), "bytes": size, "gb": round(size / 1e9, 3)}


def _move_view(found):
    if not found:
        return None
    out = {k: found[k] for k in ("id", "target", "state", "started_at", "finished_at", "estimate_files", "estimate_bytes",
                                 "moved_files", "moved_bytes", "failed", "error")}
    out["matches"] = found["state"] == "done" and found["moved_bytes"] == found["estimate_bytes"] and not found["failed"]
    return out


def latest_move(c):
    return _move_view(c.execute("SELECT * FROM storage_moves ORDER BY started_at DESC LIMIT 1").fetchone())


def interrupted(store):
    """At start: a move that was running when the server stopped is stopped (the next one picks up where it left)."""
    with store.transaction() as c:
        c.execute("UPDATE storage_moves SET state='stopped', finished_at=?, error=? WHERE state='running'",
                  (H.now(), "The server restarted during the move; start it again to finish"))


def start_move(store, blobs, who, expect_bytes, stop=None, wait=False):
    # The place is fixed here, with the files the owner confirmed for it: if new files go elsewhere before or while
    # the copy runs, the move stops, and nothing is recorded for a place that was not written and read back.
    at = blobs.target()
    with store.transaction() as c:
        if c.execute("SELECT 1 FROM storage_moves WHERE state='running'").fetchone():
            raise Problem("move_running", "Files are already being moved", 409)
        items = pending(c, blobs, at)
        total = sum(item[1] for item in items)
        if total != expect_bytes:
            raise Problem("estimate_changed", "The files to move changed since the estimate; check the new one", 409,
                          extra={"estimate": estimate(c, blobs)})
        move_id = H.new_id()
        label, _ = _target(at)
        try:
            c.execute("INSERT INTO storage_moves(id,target,state,started_at,started_by,estimate_files,estimate_bytes) "
                      "VALUES(?,?,'running',?,?,?,?)", (move_id, label, H.now(), who.actor, len(items), total))
        except sqlite3.IntegrityError:  # another start got in after the check above (storage_moves_one_running)
            raise Problem("move_running", "Files are already being moved", 409) from None
        H.event(c, who.actor, "storage.move", label, {"files": len(items), "bytes": total})
    thread = threading.Thread(target=_move, args=(store, blobs, move_id, items, at, stop or threading.Event()),
                              name="tico-storage-move", daemon=True)
    thread.start()
    if wait:
        thread.join()
    return move_id


def _move(store, blobs, move_id, items, at, stop):
    """Copy each file from where it is now into the place `at` (where new files went when the move started), check
    the copy there (`copy_one`), and only then record it there, while new files still go there. Nothing is ever
    deleted: the source stays where it was."""
    _, location = _target(at)
    moved, size, failed, error = 0, 0, 0, ""
    for digest, length, content_type in items:
        if stop.is_set() or at.generation != blobs._generation:
            break
        try:
            copy_one(blobs, digest, length, content_type or "application/octet-stream", at)
            if at.generation != blobs._generation:
                break
            if location:
                with store.transaction() as c:
                    c.execute("INSERT OR REPLACE INTO blob_locations VALUES(?,?,?)", (digest, location, H.now()))
            moved, size = moved + 1, size + length
        except Exception as exc:
            if at.generation != blobs._generation:
                break
            failed += 1
            error = exc.detail if isinstance(exc, Problem) else "A file could not be copied: " + type(exc).__name__
        with store.transaction() as c:
            c.execute("UPDATE storage_moves SET moved_files=?, moved_bytes=?, failed=?, error=? WHERE id=?",
                      (moved, size, failed, error, move_id))
    state = ("stopped" if stop.is_set() or at.generation != blobs._generation else "failed" if failed else "done")
    if state == "stopped" and not error:
        error = "Where new files go changed during the move; start it again to finish"
    with store.transaction() as c:
        c.execute("UPDATE storage_moves SET state=?, finished_at=?, error=? WHERE id=?", (state, H.now(), error, move_id))
        H.event(c, "system:storage", "storage.moved", move_id, {"state": state, "files": moved, "bytes": size,
                                                               "failed": failed})


def copy_one(blobs, digest, length, content_type, at):
    """Copy one file into the place `at` and check the copy; `at` no longer being where new files go stops it before
    anything is written (Blobs._upload). Into Tico's bucket the check is S3's own SHA-256 of every byte it received,
    so nothing is read back: reading it back would pass the edge and count as delivery to the company for Tico's own
    check. A file it already holds whole, uploaded there since it was turned on, is not copied again. Elsewhere the
    copy is read back whole and hashed."""
    key = blobs.key(digest)
    tico = bool(blobs.edge) and at.settings.blob_bucket and _target(at)[1] == blobs.edge["location"]
    if tico and blobs._matches_s3(digest, length, at.settings, at.settings.blob_bucket, at.sources):
        return
    with tempfile.TemporaryFile(dir=_staging(blobs)) as staged:
        source = blobs._open_elsewhere(key, digest, 0, {})
        sha, got = hashlib.sha256(), 0
        try:
            while chunk := source.read(1024 * 1024):
                sha.update(chunk)
                got += len(chunk)
                staged.write(chunk)
        finally:
            source.close()
        if sha.hexdigest() != digest or got != length:
            raise Problem("blob_integrity", "A stored file failed its integrity check where it is now; it was not moved",
                          503)
        staged.seek(0)
        if tico:
            blobs._upload(staged, digest, length, content_type, at=at, whole=True)
            if not blobs.holds_whole(digest, length, at):
                raise Problem("blob_integrity", "A moved file failed its integrity check", 503)
            # Held whole, typed as uploaded: linked from now on, not after a "not there" seen before the move ends.
            mime = (content_type or "").split(";", 1)[0].strip().lower()
            blobs._edge_seen[(blobs.edge["location"], digest, mime)] = True
            if visual_kind(mime, length):
                # A page or SVG: its cleaned copy too, from the bytes in hand. A copy that fails is made on first view.
                staged.seek(0)
                landed(blobs, digest, length, mime, staged.read())
        elif at.settings.blob_bucket:
            blobs._upload(staged, digest, length, content_type, at=at)
            blobs.verify_s3(digest, at=at)
        else:
            blobs._local(staged, digest)
            with (blobs.directory / key).open("rb") as written:
                if hashlib.file_digest(written, "sha256").hexdigest() != digest:
                    raise Problem("blob_integrity", "A moved file failed its integrity check", 503)


def _staging(blobs):
    blobs.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return blobs.directory


# ---------------------------------------------------------------------------------------------- export
class _Sink:
    """What zipfile writes to: chunks taken by the response as they are made, never the whole archive."""
    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def take(self):
        out, self.parts = b"".join(self.parts), []
        return out


def _clean(name):
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", str(name or "")).strip(". ") or "file"
    return name[:150]


def export_entries(c, who, auth, files):
    """[(path in the archive, digest)] of every file version and attachment `who` may open, and how many were left
    out because they are private to someone else."""
    from .media import blob_for
    entries, seen, left_out = [], set(), 0
    cache = {}
    for found in c.execute("SELECT * FROM bot_files WHERE locator='tico_blob' ORDER BY first_activity_at, id"):
        versions = c.execute("SELECT version, blob_id, digest, name FROM bot_file_versions WHERE file_id=? "
                             "ORDER BY version", (found["id"],)).fetchall()
        if not files.visible(c, who, found, cache):
            left_out += len(versions)
            seen.update(v["blob_id"] for v in versions)
            continue
        for v in versions:
            seen.add(v["blob_id"])
            entries.append((f"files/{found['id']}/v{v['version']}/{_clean(v['name'])}", v["digest"]))
    # A video's poster and a page's preview are made from a version, not files of their own.
    for still in c.execute("SELECT poster_blob_id, thumb_blob_id FROM bot_file_versions UNION ALL "
                           "SELECT poster_blob_id, thumb_blob_id FROM blob_media"):
        seen.update(still)
    for blob in c.execute("SELECT id, digest, name FROM blobs ORDER BY created_at, id"):
        if blob["id"] in seen:
            continue
        try:
            blob_for(c, auth, who, blob["id"])
        except Problem:
            left_out += 1
            continue
        entries.append((f"attachments/{blob['id']}/{_clean(blob['name'])}", blob["digest"]))
    return entries, left_out


def export(store, blobs, auth, files, who):
    with store.read() as c:
        entries, left_out = export_entries(c, who, auth, files)

    def chunks():
        sink = _Sink()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path, digest in entries:
                with archive.open(path, "w", force_zip64=True) as out:
                    for chunk in blobs.open_range(digest):
                        out.write(chunk)
                        if sink.parts:
                            yield sink.take()
                yield sink.take()
            note = {"files": len(entries), "left_out": left_out,
                    "note": "Files private to someone else are left out; Move files back keeps every file."}
            archive.writestr("manifest.json", json.dumps(note, indent=1))
        yield sink.take()

    headers = {"Content-Disposition": "attachment; filename=\"tico-files.zip\"", "Cache-Control": "private, no-store",
               "X-Content-Type-Options": "nosniff", "X-Tico-Left-Out": str(left_out)}
    return StreamingResponse(chunks(), media_type="application/zip", headers=headers)


# ---------------------------------------------------------------------------------------------- routes
def startup(store, vault, blobs):
    interrupted(store)


def install(app, store, auth):
    from .company_storage import apply, require_owner
    from .visuals import ancestors
    vault, blobs, settings = app.state.vault, app.state.blobs, store.settings
    with _lock:
        _live[id(store)] = (store, vault, blobs)
    # Where pages and SVG on the company's address may be framed: the app's own address (`landed`).
    blobs.visual_ancestors = ancestors(settings)

    def owner(request):
        who = request.state.identity
        auth.domain(who)
        require_owner(who)
        return who

    @app.post("/api/v2/system/storage/tico/check")
    def storage_tico_check(request: Request):
        """Ask Tico HQ now whether this company uses Tico file hosting, and apply its answer."""
        owner(request)
        result = sync(store, vault, blobs)
        if result is None:
            raise Problem("no_gateway", "Connect the Tico AI Gateway first (Settings › Computers)", 409)
        return result

    @app.get("/api/v2/system/storage/move")
    def storage_move_read(request: Request):
        """What Move existing files would copy into the place new files go to, and the latest move."""
        owner(request)
        with store.read() as c:
            return {"estimate": estimate(c, blobs), "move": latest_move(c)}

    @app.post("/api/v2/system/storage/move")
    def storage_move(request: Request, body: Move):
        """Copy every file and every version into the place new files go to; each copy is checked before it counts,
        and nothing is deleted where it was."""
        who = owner(request)
        if settings.rehearsal or settings.demo:
            raise Problem("rehearsal", "Files can't be moved in a rehearsal or the demo", 409)
        start_move(store, blobs, who, body.expect_bytes)
        with store.read() as c:
            return {"move": latest_move(c)}

    @app.get("/api/v2/system/storage/export")
    def storage_export(request: Request):
        """A zip of every file and version the owner may open, streamed (files private to others are left out)."""
        who = owner(request)
        return export(store, blobs, auth, app.state.files, who)
