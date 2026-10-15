"""Publish small provider-normalized app snapshots from this Mac to the cloud API."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import threading

from clients.tico import APIError, Client
from .outage import Outage, log

ROOT = Path(__file__).resolve().parents[1]
MAIL_BATCH = 100
CALENDAR_FIELDS = ("occurrence_id", "event_id", "title", "start", "end", "attendees", "meeting_url")
MAIL_FIELDS = ("thread_id", "epoch", "date", "from_addr", "from_header", "to", "cc", "subject",
               "snippet", "labels", "body", "body_truncated", "attachments", "list_id",
               "is_internal", "has_unsubscribe", "rule_hits")


def owner_handle(config=None):
    """`owner_handle` in the connector config, else TICO_OWNER_HANDLE, else the roster's owner."""
    named = str((config or {}).get("owner_handle") or "").strip().lower()
    if named:
        return named
    from connectors.mail import owner_handle as roster_owner                 # noqa: PLC0415
    return roster_owner()


def mail_secret_path(config=None):
    """The Google service-account key: GOOGLE_SA_KEY, else the supervisor's own copy where bots cannot
    read it (runner/mail_key.py), else <projects_dir>/secrets/google-sa.json."""
    named = os.environ.get("GOOGLE_SA_KEY")
    if named:
        return Path(named).expanduser()
    from . import isolation, mail_key
    if isolation.enabled():
        return mail_key.protected_path(config or {})
    return Path((config or {}).get("projects_dir") or "") / "secrets" / "google-sa.json"


def mail_grants_path(config=None):
    """People's own connected Google accounts (connectors/mail/oauth.py): google-oauth.json beside the key."""
    named = os.environ.get("GOOGLE_OAUTH_GRANTS")
    return Path(named).expanduser() if named else mail_secret_path(config).with_name("google-oauth.json")


def mail_credential_present(config=None):
    """True when this computer can give mail access: the service-account key, a connected account, or both."""
    return mail_secret_path(config).is_file() or mail_grants_path(config).is_file()


def mail_sync_seconds(config=None):
    raw = (config or {}).get("mail_sync_seconds")
    if raw is None or raw == "":
        raw = os.environ.get("TICO_MAIL_SYNC_SECONDS", "600")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 600


DELEGATION_WORDS = ("unauthorized_client", "client is unauthorized", "domain-wide delegation is not granted")
# A domain counts as not delegated only after this many consecutive delegation errors, spread over at least
# DELEGATION_CYCLES sync cycles or DELEGATION_SPAN seconds (Google sometimes answers unauthorized_client once,
# for a moment, on a delegation that is fine). From then on it is retried after these gaps, growing with each
# failed retry and held at the last; one success on the domain clears it all.
DELEGATION_FAILURES = 3
DELEGATION_CYCLES = 2
DELEGATION_SPAN = 300
DELEGATION_BACKOFF = (120, 300, 900, 1800, 3600)


def not_delegated(exc):
    """True when Google refused the key for the whole domain (no domain-wide delegation there), as
    opposed to one mailbox that does not exist or a sign-in that lapsed."""
    return not isinstance(exc, APIError) and any(word in str(exc).lower() for word in DELEGATION_WORDS)


def domain_of(address):
    return str(address or "").rpartition("@")[2].strip().lower()


def failure_reason(exc):
    """The kind of failure the cloud may see; the message itself stays in this Mac's log."""
    if isinstance(exc, APIError):                   # the hub, not Google
        return "network" if not exc.status or exc.status >= 500 else "error"
    if not_delegated(exc):
        return "delegation"
    text = str(exc).lower()
    if "invalid email or user id" in text:
        return "error"                  # no such mailbox: reconnecting would not help
    if any(word in text for word in ("invalid_grant", "unauthorized_client", "access_denied", "401", "403")):
        return "signin"
    if isinstance(exc, (OSError, TimeoutError, subprocess.TimeoutExpired)) or any(
            word in text for word in ("timed out", "timeout", "unreachable", "connection", "network", "name resolution")):
        return "network"
    return "error"


def mail_umask():
    """Where the two-user layout is on, the files the mail CLI makes in workspace/runtime/mail (mail.db and its
    -wal/-shm/-journal, audit.jsonl) are group-writable: a message bot's own `mail.sh` writes to the same
    database as this job, from another user in the same group. Nowhere else changes."""
    from . import isolation
    return {"umask": 0o002} if isolation.enabled() else {}


class ConnectorPublisher:
    def __init__(self, config, *, client=None, fetch=None, mail=None, event=None):
        self.config = config
        self.client = client or Client(config["url"], config["token"], timeout=35, retries=1)
        # The script this checkout ships, not one named after a company's folder: an environment
        # whose workspace is ~/Companies/Acme has no `tico` beside it.
        self.script = ROOT / "scripts/mail.sh"
        # The owner's roster handle: the identity that holds every verb on the local mail CLI.
        self.owner = owner_handle(config)
        self.fetch = fetch or self.calendar
        self.run_mail = mail or self.mail_cli
        self.create_event = event or self.calendar_event
        self.stop = threading.Event()
        self.accounts = {}
        self.mailboxes = {}
        self.last_mail_sync = None
        self.mail_index = 0
        self.clock = time.monotonic
        self.cycle = 0                  # sync cycles so far (a calendar refresh or a mail sync turn)
        self.domains = {}               # domain -> delegation trouble: see note_delegation
        self.mail_interval = mail_sync_seconds(config)

    def ready(self):
        if not self.script.is_file():
            raise RuntimeError("The local calendar connector script is missing")
        if not self.script.stat().st_mode & 0o100:
            raise RuntimeError("The local calendar connector script is not executable")

    def mail_env(self):
        """The environment of the local mail CLI: the runner's, without its hub token, and with the
        key where the supervisor keeps it."""
        env = {key: value for key, value in os.environ.items() if key != "HUB_TOKEN"}
        env.setdefault("GOOGLE_SA_KEY", str(mail_secret_path(self.config)))
        return env

    def mail_secret(self):
        if not mail_credential_present(self.config):
            return None
        path = mail_secret_path(self.config)
        return path if path.is_file() else mail_grants_path(self.config)

    def prepare(self):
        """Build the mail venv now, with room to finish: the first build takes a minute and every
        lookup below has 40 seconds. A failure is logged and retried on the next start."""
        try:
            subprocess.run([str(self.script), "--setup"], cwd=self.script.parents[1], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=900, check=True)
        except Exception as exc:
            detail = getattr(exc, "stderr", None) or exc
            print("Tico connectors: could not prepare the mail environment: " + str(detail)[-300:], flush=True)

    def calendar(self, email, hours):
        result = subprocess.run([str(self.script), "upcoming", "--as", self.owner, "--for", email,
                                 "--hours", str(hours), "--json"], cwd=self.script.parents[1],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40,
                                env=self.mail_env(), **mail_umask())
        if result.returncode:
            # The CLI's own error stays on this Mac; failure_reason reads it for the kind only.
            raise RuntimeError("Local calendar lookup failed: " + (result.stdout or result.stderr or "")[-400:])
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("Local calendar returned invalid JSON") from exc
        events = payload.get("events")
        if not isinstance(events, list):
            raise RuntimeError("Local calendar returned no event list")
        # The local CLI also gives bots Google's own fields (id, iCalUID, summary, htmlLink,
        # status; 5b11f99). The hub's snapshot takes exactly these, and one extra key refused the
        # whole publish with a 422 for everyone.
        return [{key: event[key] for key in CALENDAR_FIELDS if key in event} for event in events if isinstance(event, dict)]

    def calendar_event(self, action):
        env = self.mail_env()
        result = subprocess.run(
            [str(self.script), "connector-event", "--as", self.owner, "--json"],
            cwd=self.script.parents[1], input=json.dumps(action), capture_output=True,
            text=True, timeout=40, env=env, **mail_umask())
        if result.returncode:
            raise RuntimeError("Local calendar event creation failed")
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("Local calendar event creation returned invalid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("event_id"):
            raise RuntimeError("Local calendar event creation returned no event")
        return payload

    def calendar_action_tick(self, limit=10):
        completed = 0
        for _ in range(limit):
            if self.stop.is_set():
                break
            claimed = self.client.post("connectors/calendar/actions/claim", {})
            action = claimed.get("action") if isinstance(claimed, dict) else None
            if not action:
                break
            try:
                result = self.create_event(action)
                body = {"status": "succeeded", "event_id": result.get("event_id") or "",
                        "meeting_url": result.get("meeting_url") or "", "error": ""}
            except subprocess.TimeoutExpired as exc:
                body = {"status": "unknown", "event_id": "", "meeting_url": "",
                        "error": type(exc).__name__}
            except Exception as exc:
                # The provider may have accepted the event before its response failed. Never
                # label that safe to retry: details stay local and the requester must inspect it.
                body = {"status": "unknown", "event_id": "", "meeting_url": "",
                        "error": type(exc).__name__}
            self.client.post(f"connectors/calendar/actions/{action['id']}/result", body)
            completed += 1
        return completed

    def mail_cli(self, args):
        # The CLI talks to Gmail only. The runner holds HUB_TOKEN and posts the batch itself.
        env = self.mail_env()
        timeout = 40
        if args and args[0] == "sync" and (len(args) < 2 or args[1] not in ("export", "ack")):
            timeout = 300
        result = subprocess.run([str(self.script), *args], cwd=self.script.parents[1],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, env=env, **mail_umask())
        if result.returncode:
            # The CLI's own error stays on this Mac; failure_reason reads it for the kind only.
            raise RuntimeError("Local mail lookup failed: " + (result.stdout or result.stderr or "")[-400:])
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("Local mail returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Local mail returned no object")
        return payload

    def tick(self):
        self.ready()
        self.cycle += 1
        self.calendar_action_tick()
        request = self.client.get("connectors/calendar/targets")
        snapshots, failing = [], []
        for person in request["people"]:
            email = person["email"]
            if self.blocked(email):
                continue
            # The account and error class go to this Mac's log only; provider details and local
            # paths never reach the cloud. A failed person retains their prior cloud snapshot
            # until the next successful refresh, and keeps failing quietly: one line per 10 minutes.
            account = self.accounts.setdefault(email, Outage(
                "Tico connectors", f"calendar refresh failed for {email}",
                f"calendar refresh for {email} still failing", f"calendar refresh for {email} working again",
                progress=600))
            try:
                snapshots.append({"email": email, "events": self.fetch(email, request["hours"])})
                account.recovered()
                self.delegated(email)
            except Exception as exc:
                if not_delegated(exc):          # the whole domain, once it keeps happening
                    self.note_delegation(email)
                    continue
                account.failed(exc)
                failing.append({"account": email, "reason": failure_reason(exc)})
        failing += self.delegation_failures(person["email"] for person in request["people"])
        if snapshots:
            self.client.post("connectors/calendar/snapshots", {"snapshots": snapshots})
        self.report("calendar", failing)
        return len(snapshots)

    def blocked(self, address):
        """True while this address's domain is waiting out a backoff after repeated delegation errors."""
        state = self.domains.get(domain_of(address))
        return bool(state and state["until"] and self.clock() < state["until"])

    def note_delegation(self, address):
        """One more delegation error for this address's domain. A single error (or a burst inside one cycle)
        changes nothing; the domain is treated as not delegated only at DELEGATION_FAILURES in a row across
        DELEGATION_CYCLES cycles or DELEGATION_SPAN seconds, and then backs off 2, 5, 15, 30, 60 minutes."""
        domain, now = domain_of(address), self.clock()
        state = self.domains.setdefault(domain, {"fails": 0, "cycles": 0, "cycle": None, "first": now,
                                                 "level": -1, "until": 0, "down": False})
        state["fails"] += 1
        if state["cycle"] != self.cycle:
            state["cycle"] = self.cycle
            state["cycles"] += 1
        if not state["down"]:
            if state["fails"] < DELEGATION_FAILURES or (
                    state["cycles"] < DELEGATION_CYCLES and now - state["first"] < DELEGATION_SPAN):
                return
            state["down"] = True
            log(f"Tico connectors: the Google key cannot act for the domain {domain} (mailbox {address} refused "
                f"{state['fails']} times in a row, no domain-wide delegation there); skipping it and checking "
                f"again in {DELEGATION_BACKOFF[0] // 60} minutes, then less often")
        state["level"] = min(state["level"] + 1, len(DELEGATION_BACKOFF) - 1)
        state["until"] = now + DELEGATION_BACKOFF[state["level"]]

    def delegated(self, address):
        """Any success on this address's domain: the delegation works, so forget every earlier error."""
        state = self.domains.pop(domain_of(address), None)
        if state and state["down"]:
            log(f"Tico connectors: the Google key can act for the domain {domain_of(address)} again")

    def delegation_failures(self, addresses):
        """The health entries for every target whose domain has reached the threshold and not yet recovered."""
        return [{"account": address, "reason": "delegation"} for address in addresses
                if self.domains.get(domain_of(address), {}).get("down")]

    def report(self, service, failing):
        """Tell the hub how this refresh went (Settings shows a failing account; a clean report is
        the heartbeat). Best effort: a hub it can't reach shows as a stale connector instead."""
        try:
            self.client.post("connectors/health", {"service": service, "failing": failing})
        except Exception:
            pass

    def mail_tick(self):
        if self.last_mail_sync is not None and time.monotonic() - self.last_mail_sync < self.mail_interval:
            return 0
        self.ready()
        self.cycle += 1
        request = self.client.get("connectors/mail/targets")
        boxes = request.get("mailboxes") or []
        posted = 0
        failing = []
        live = [box for box in boxes if not self.blocked(box["address"])]
        if live:
            # One mailbox per due tick: first-run backfill is one messages.get per id.
            box = live[self.mail_index % len(live)]
            self.mail_index += 1
            address = box["address"]
            account = self.mailboxes.setdefault(address, Outage(
                "Tico connectors", f"mail sync failed for {address}",
                f"mail sync for {address} still failing", f"mail sync for {address} working again",
                progress=600))
            try:
                posted = self._mail_mailbox(address, box, request.get("batch") or MAIL_BATCH)
                account.recovered()
                self.delegated(address)
            except Exception as exc:
                if not_delegated(exc):          # the whole domain, once it keeps happening
                    self.note_delegation(address)
                else:
                    account.failed(exc)
                    failing.append({"account": address, "reason": failure_reason(exc)})
        if boxes:
            self.report("mail", failing + self.delegation_failures(box["address"] for box in boxes))
        self.last_mail_sync = time.monotonic()
        return posted

    def _mail_mailbox(self, address, box, batch):
        args = ["sync", "--as", self.owner, "--mailbox", address, "--json"]
        if not box.get("message_count"):
            args = ["sync", "--as", self.owner, "--mailbox", address, "--backfill", "90d", "--json"]
        result = self.run_mail(args)
        history_id = result.get("history_id") if isinstance(result, dict) else None
        posted = 0
        for _ in range(50):
            if self.stop.is_set():          # a stop lands between batches, never inside one
                break
            export = self.run_mail(["sync", "export", "--as", self.owner, "--mailbox", address,
                                    "--limit", str(int(batch)), "--json"])
            rows = export.get("messages") if isinstance(export, dict) else None
            if not rows:
                break
            messages, deleted, ids = [], [], []
            for row in rows:
                mid = row.get("msg_id") or row.get("id")
                if not mid:
                    continue
                ids.append(mid)
                if row.get("deleted_at"):
                    deleted.append(mid)
                else:
                    messages.append(self._mail_message(row, mid))
            body = {"mailbox": address, "messages": messages, "deleted": deleted,
                    "synced_at": datetime.now(timezone.utc).isoformat()}
            if history_id:
                body["history_id"] = str(history_id)
            self.client.post("connectors/mail/messages", body)
            self.run_mail(["sync", "ack", *ids, "--as", self.owner, "--mailbox", address, "--json"])
            posted += len(ids)
        return posted

    @staticmethod
    def _mail_message(row, mid):
        message = {"msg_id": mid}
        for key in MAIL_FIELDS:
            if key in row and row[key] is not None:
                message[key] = row[key]
        return message

    def run(self):
        cloud = Outage("Tico connectors", "refresh failed", "refresh still failing", "refresh working again")
        # Mail keeps its own outage: a calendar that cannot be reached must not stop the mail
        # copies, and a mailbox that cannot be read must not stop the calendar.
        mail = Outage("Tico connectors", "mail refresh failed", "mail refresh still failing",
                      "mail refresh working again", progress=600)
        from .freshness import CodeWatch
        watch = CodeWatch("Tico connectors")
        self.prepare()
        while not self.stop.is_set():
            try:
                self.tick()
                cloud.recovered()
            except Exception as exc:
                cloud.failed(exc)
            try:
                self.mail_tick()
                mail.recovered()
            except Exception as exc:
                mail.failed(exc)
            if watch.wait(self.stop, 45):       # the checkout moved: the supervisor starts it on the new code
                return
