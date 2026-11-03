"""What every meeting importer shares: credentials that stay on this computer, sanitized provider
errors, a bounded rolling window, and the one hub door (`POST /api/v2/meetings/import`).

An importer only says how to read its tool (`scopes`, `fetch`); this file decides when, how far
back, what is remembered, and what is allowed to leave the computer.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clients.tico import APIError

from ..outage import log

SECRETS_DIR = "secrets"
MAX_RESPONSE = 20_000_000
TIMEOUT = 60
MAX_TURNS = 10_000
MAX_TRANSCRIPT = 1_000_000
RECENT_HOURS = 72          # notes and recordings are revisited for this long: summaries arrive late
PENDING_DAYS = 7           # how long a meeting whose transcript is not ready holds the cursor
MAX_BACKFILL_DAYS = 365

# What the Settings card and `importers-doctor` say about each code. Nothing else about a
# failure ever leaves this computer: no URL, header, key or provider body.
CODES = {
    "missing_credentials": "The credential file on this computer is missing or incomplete",
    "auth_failed": "The tool refused the credential",
    "forbidden": "The credential lacks a scope or the account lacks a plan feature",
    "rate_limited": "The tool asked us to slow down",
    "unreachable": "The tool could not be reached",
    "provider_error": "The tool returned an error",
    "bad_response": "The tool returned something unreadable",
    "hub_rejected": "The Tico server refused the import",
    "sync_error": "The sync failed",
}


class ProviderError(RuntimeError):
    """A sanitized failure: a code from CODES and a sentence with no secrets in it."""

    def __init__(self, code, message="", status=0):
        super().__init__(message or CODES.get(code, CODES["sync_error"]))
        self.code = code if code in CODES else "sync_error"
        self.status = status


def load_env(path):
    """KEY=value lines, with `#` comments; the file is the only place a secret is written."""
    values = {}
    try:
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, val = line.partition("=")
            if sep:
                values[key.strip()] = val.strip().strip("'\"")
    except OSError:
        pass
    return values


def moment(value, default=None):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return default
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def text(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def email_of(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[^@\s<>]{1,64}@[^@\s<>]{1,255}", value) else ""


def https_url(value):
    value = str(value or "").strip()
    return value if re.fullmatch(r"https://[^\s<>]{1,480}", value) else ""


def safe_id(value):
    """The hub keeps A-Za-z0-9_.:@/- ; provider ids that use base64 characters are mapped, not hashed."""
    return re.sub(r"[^A-Za-z0-9_.:@/-]", "", str(value or "").replace("+", "-").replace("=", ""))[:200]


def people(*entries):
    """Participants for the hub: unique, emails when known, at most 100."""
    out, seen = [], set()
    for entry in entries:
        name, email = (entry if isinstance(entry, tuple) else ("", entry))
        email, name = email_of(email), text(name, 120)
        key = email or name.lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name, "email": email} if email and name else email or name)
    return out[:100]


def turns_of(rows, merge_gap_ms=8000):
    """[(speaker, start_ms, end_ms, text)] -> segments. Adjacent lines by one speaker become one
    turn so a two-hour call stays under the hub's 10,000-segment limit."""
    out = []
    for speaker, start, end, words in rows:
        words = text(words, 20_000)
        if not words:
            continue
        speaker = text(speaker, 100)
        start = max(0, min(86_400_000, int(start)))
        end = max(start, min(86_400_000, int(end)))
        last = out[-1] if out else None
        if last and last["speaker"] == speaker and start - last["end_ms"] <= merge_gap_ms and len(last["text"]) < 1500:
            last["text"] += " " + words
            last["end_ms"] = max(last["end_ms"], end)
        else:
            out.append({"speaker": speaker, "start_ms": start, "end_ms": end, "text": words})
    if len(out) > MAX_TURNS or sum(len(t["text"]) for t in out) > MAX_TRANSCRIPT:
        raise ProviderError("bad_response", "The transcript exceeds the import limit")
    return out


def request_json(method, url, *, headers=None, body=None, form=None, timeout=TIMEOUT, tool="The tool",
                 opener=None):
    """One provider call. Every failure is a ProviderError that never carries the URL or a body."""
    data, headers = None, dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        from urllib.parse import urlencode
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "Tico-MeetingImporter/1")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with (opener or urllib.request.build_opener(_NoRedirect())).open(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(*status_code(exc.code, tool), status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("unreachable", tool + " could not be reached") from None
    if len(payload) > MAX_RESPONSE:
        raise ProviderError("bad_response", tool + " response exceeds the import limit")
    return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def status_code(status, tool):
    if status == 401:
        return "auth_failed", tool + " refused the credential (HTTP 401)"
    if status == 403:
        return "forbidden", tool + " denied access (HTTP 403): check the scopes and the plan"
    if status == 429:
        return "rate_limited", tool + " rate limit reached; retrying later"
    return "provider_error", tool + " returned HTTP " + str(status)


def parse_json(payload, tool):
    try:
        value = json.loads(payload or b"{}")
    except ValueError:
        raise ProviderError("bad_response", tool + " returned an unreadable response") from None
    return value


@dataclass
class Scope:
    """One credential's view: a Granola key, a Zoom account, a Google user."""
    id: str
    label: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Item:
    external_id: str
    owner_email: str
    body: dict                 # what the hub takes, minus source, external_id and owner_email
    started: datetime
    revision: str = ""         # what the provider changed last; unchanged means no refetch


class Importer:
    source = ""
    name = ""
    prefix = ""                # credential names and options start with this
    secret_file = ""           # under <projects>/secrets/
    required = ()              # every one must be present
    interval = 300
    window = timedelta(days=1)
    max_lookback_days = MAX_BACKFILL_DAYS
    lookback_days = 30
    private_by_default = False
    fallback = True            # unmatched people's meetings go to the company owner; False skips them

    def __init__(self, config, state, client, *, env=None, transport=None, now=None, backfill_days=None):
        self.config, self.state, self.client = config, state, client
        self.secret_path = Path(config["projects_dir"]) / SECRETS_DIR / self.secret_file
        self._env = env
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.backfill_days = backfill_days
        self._owner = None
        self.hold = None

    # -- credentials ---------------------------------------------------------------------
    def env(self):
        if self._env is not None:
            return self._env
        values = load_env(self.secret_path)
        for key in self.credential_names():
            if os.environ.get(key):
                values[key] = os.environ[key].strip()
        return values

    def credential_names(self):
        return (*self.required, self.prefix + "_PRIVATE")

    def ready(self):
        env = self.env()
        missing = [k for k in self.required if not env.get(k)]
        if missing:
            raise ProviderError("missing_credentials", "Missing " + ", ".join(missing)
                                + " in secrets/" + self.secret_file + " on this computer")
        return env

    def private(self):
        flag = self.env().get(self.prefix + "_PRIVATE", "").strip().lower()
        return flag in ("1", "true", "yes") if flag else self.private_by_default

    pace = 0.0                 # seconds between calls to a rate-limited tool

    def send(self, method, url, *, headers=None, body=None, form=None, raw=False):
        """Every provider call goes through here: tests replace `transport` and never touch a network."""
        if self.transport:
            return self.transport(method, url, headers=headers, body=body, form=form, raw=raw)
        wait = self.pace - (time.monotonic() - getattr(self, "_last", -1e9))
        if wait > 0:
            time.sleep(wait)
        try:
            payload = request_json(method, url, headers=headers, body=body, form=form, tool=self.name)
        finally:
            self._last = time.monotonic()
        return payload if raw else parse_json(payload, self.name)

    # -- what a subclass supplies -----------------------------------------------------------
    def scopes(self):
        raise NotImplementedError

    def fetch(self, scope, since, until):
        """Items whose meeting started in [since, until). Return `not_ready` starts through self.hold_at."""
        raise NotImplementedError

    def hold_at(self, started):
        """A meeting whose transcript is not ready yet: keep the cursor from passing it."""
        if self.now() - started <= timedelta(days=PENDING_DAYS):
            self.hold = min(self.hold, started) if self.hold else started

    # -- the hub -------------------------------------------------------------------------------
    def fallback_owner(self):
        if self._owner is None:
            self._owner = str((self.client.get("config") or {}).get("owner_email") or "").strip().lower()
        if not self._owner:
            raise ProviderError("hub_rejected", "The Tico server did not name an owner for unmatched people")
        return self._owner

    def file(self, item):
        body = {**item.body, "source": self.source, "external_id": item.external_id}
        body["private"] = True if self.private() else body.get("private")
        if body["private"] is None:
            del body["private"]
        owner = item.owner_email or self.fallback_owner()
        try:
            try:
                result = self.client.post("meetings/import", {**body, "owner_email": owner})
            except APIError as exc:
                if exc.code != "not_found" or (self.fallback and owner == self.fallback_owner()):
                    raise
                if not self.fallback:
                    log("Tico " + self.name + ": " + owner + " is not on the Tico roster; skipped "
                        + item.external_id)
                    return {"changed": False, "status": "skipped"}
                # Someone outside the roster: the company owner keeps it rather than dropping it.
                log("Tico " + self.name + ": " + owner + " is not on the Tico roster; filing "
                    + item.external_id + " under the owner")
                result = self.client.post("meetings/import", {**body, "owner_email": self.fallback_owner()})
        except APIError as exc:
            raise ProviderError("hub_rejected", "The Tico server refused a meeting (" + exc.code + ")") from None
        return result

    def scope_state(self, scope):
        return "meetings:" + self.source + ":" + scope.id

    def known(self, scope, external_id, revision):
        return bool(revision) and self.state.import_phase(self.scope_state(scope), external_id) == revision

    def remember(self, scope, item):
        if item.revision:
            self.state.import_phase(self.scope_state(scope), item.external_id, item.revision)

    # -- one pass -------------------------------------------------------------------------
    def tick(self, stop=None):
        self.ready()
        imported = 0
        until = self.now()
        oldest = until - timedelta(days=self.max_lookback_days)
        for scope in self.scopes():
            cursor_key = self.scope_state(scope)
            stored = moment(self.state.import_cursor(cursor_key))
            cursor = stored or until - timedelta(days=min(self.lookback_days, self.max_lookback_days))
            recent = until - timedelta(hours=RECENT_HOURS)
            since = until - timedelta(days=self.backfill_days) if self.backfill_days else min(cursor, recent)
            start = max(since, oldest)
            self.hold = None
            while start < until and not (stop and stop.is_set()):
                end = min(start + self.window, until)
                for item in self.fetch(scope, start, end):
                    result = self.file(item)
                    imported += bool(result.get("changed")) and result.get("status") != "deleted"
                    self.remember(scope, item)
                    if stop and stop.is_set():
                        break
                if not (stop and stop.is_set()):
                    # Progress survives a later failure; a meeting still being transcribed holds it back.
                    reach = min(end, self.hold) if self.hold else end
                    if not self.backfill_days:
                        self.state.import_cursor(cursor_key, max(cursor, reach).isoformat())
                start = end
        return imported
