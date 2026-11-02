"""Google Meet through the Meet REST API v2 (https://developers.google.com/workspace/meet/api).

A Google Workspace service account with domain-wide delegation impersonates each user named in
GOOGLE_MEET_USERS, with the single read-only scope
https://www.googleapis.com/auth/meetings.space.readonly. The key file stays on this computer
(secrets/google-meet.env names it). Conference records expire 30 days after the meeting ends, so
the importer never looks back further than that.
"""

import base64
import json
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path

from .base import (Importer, Item, ProviderError, Scope, email_of, https_url, moment, people, safe_id, text,
                   turns_of)

API = "https://meet.googleapis.com/v2/"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
PAGE = 100
PAGES = 100


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_assertion(account, subject, now):
    """The RS256 JWT Google's service-account flow exchanges for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iss": account["client_email"], "sub": subject, "scope": SCOPE,
                             "aud": TOKEN, "iat": int(now), "exp": int(now) + 3600}).encode())
    try:
        key = serialization.load_pem_private_key(account["private_key"].encode(), None)
        signature = key.sign((header + "." + claims).encode(), padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError):
        raise ProviderError("missing_credentials", "The Google service account key is not a usable private key") from None
    return header + "." + claims + "." + b64(signature)


class GoogleMeet(Importer):
    source = "google-meet"
    name = "Google Meet"
    prefix = "GOOGLE_MEET"
    secret_file = "google-meet.env"
    required = ("GOOGLE_MEET_USERS",)
    interval = 600
    window = timedelta(days=7)
    max_lookback_days = 30
    lookback_days = 30
    pace = 0.1

    def credential_names(self):
        return ("GOOGLE_MEET_USERS", "GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_MEET_PRIVATE")

    def account(self):
        env = self.env()
        raw = env.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not raw and env.get("GOOGLE_SERVICE_ACCOUNT_FILE"):
            path = Path(env["GOOGLE_SERVICE_ACCOUNT_FILE"]).expanduser()
            if not path.is_absolute():
                path = self.secret_path.parent / path
            try:
                raw = path.read_text()
            except OSError:
                raise ProviderError("missing_credentials", "The Google service account key file is not readable on this computer") from None
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
            raise ProviderError("missing_credentials",
                                "Set GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_SERVICE_ACCOUNT_JSON) to a service account key")
        return data

    def ready(self):
        env = super().ready()
        self.account()
        return env

    def token(self, user):
        cached = getattr(self, "_tokens", {}).get(user)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        try:
            answer = self.send("POST", TOKEN, form={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": sign_assertion(self.account(), user, time.time())})
        except ProviderError as exc:
            if exc.status in (400, 401):
                raise ProviderError("auth_failed", "Google refused to impersonate " + user
                                    + ": check the client id and scope in Admin console domain-wide delegation",
                                    exc.status) from None
            raise
        value = answer.get("access_token") if isinstance(answer, dict) else None
        if not value:
            raise ProviderError("bad_response", "Google returned no access token")
        if not hasattr(self, "_tokens"):
            self._tokens = {}
        self._tokens[user] = (value, time.time() + int(answer.get("expires_in") or 3600))
        return value

    def get(self, user, path, /, **query):
        url = API + path + ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
                            if query else "")
        try:
            return self.send("GET", url, headers={"Authorization": "Bearer " + self.token(user)})
        except ProviderError as exc:
            if exc.status in (401, 403):
                raise ProviderError("forbidden" if exc.status == 403 else "auth_failed",
                                    "Google Meet denied " + user + ": check domain-wide delegation and the Meet API",
                                    exc.status) from None
            raise

    def pages(self, user, path, key, /, **query):
        token, out = "", []
        for _ in range(PAGES):
            page = self.get(user, path, pageSize=PAGE, pageToken=token, **query)
            rows = page.get(key) if isinstance(page, dict) else None
            if rows is None and isinstance(page, dict) and not page.get("nextPageToken"):
                return out                      # an empty list omits the key
            if not isinstance(rows, list):
                raise ProviderError("bad_response", "Google Meet returned an unreadable list")
            out.extend(r for r in rows if isinstance(r, dict))
            token = page.get("nextPageToken") or ""
            if not token:
                return out
        raise ProviderError("provider_error", "Google Meet returned more pages than one pass reads")

    def scopes(self):
        users = [email_of(u) for u in self.env()["GOOGLE_MEET_USERS"].replace(";", ",").split(",")]
        return [Scope(safe_id(u), u, {"user": u}) for u in dict.fromkeys(u for u in users if u)]

    def fetch(self, scope, since, until):
        user = scope.data["user"]
        stamp = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        records = self.pages(user, "conferenceRecords", "conferenceRecords",
                             filter='start_time>="%s" AND start_time<="%s"' % (stamp(since), stamp(until)))
        for record in sorted(records, key=lambda r: (str(r.get("startTime")), str(r.get("name")))):
            name = str(record.get("name") or "")
            if not name.startswith("conferenceRecords/"):
                continue
            started = moment(record.get("startTime"), since)
            if not record.get("endTime"):
                self.hold_at(started)           # still running
                continue
            transcripts = self.pages(user, name + "/transcripts", "transcripts")
            ready = [t for t in transcripts if t.get("state") == "FILE_GENERATED"]
            if len(ready) < len(transcripts) and not ready:
                self.hold_at(started)
            for index, transcript in enumerate(sorted(ready, key=lambda t: str(t.get("startTime")))):
                ident = safe_id(name.split("/", 1)[1] + ("" if index == 0 else "." + str(transcript.get("name")).rsplit("/", 1)[-1]))
                revision = str(transcript.get("endTime") or "") + "|" + str(transcript.get("state"))
                if self.known(scope, ident, revision):
                    continue
                item = self.item(scope, record, transcript, started, ident, revision)
                if item:
                    yield item

    def item(self, scope, record, transcript, started, ident, revision):
        user = scope.data["user"]
        name = record["name"]
        speakers = {}
        for p in self.pages(user, name + "/participants", "participants"):
            who = p.get("signedinUser") or p.get("anonymousUser") or p.get("phoneUser") or {}
            speakers[str(p.get("name"))] = text(who.get("displayName"), 100)
        entries = self.pages(user, str(transcript["name"]) + "/entries", "transcriptEntries")
        origin = moment(transcript.get("startTime")) or started
        rows = []
        for e in sorted(entries, key=lambda e: str(e.get("startTime"))):
            begin = moment(e.get("startTime"))
            if not begin:
                continue
            end = moment(e.get("endTime"), begin)
            rows.append((speakers.get(str(e.get("participant")), ""), (begin - origin).total_seconds() * 1000,
                         (end - origin).total_seconds() * 1000, e.get("text")))
        turns = turns_of(rows)
        if not turns:
            return None
        space = {}
        try:
            space = self.get(user, str(record.get("space") or "")) if str(record.get("space") or "").startswith("spaces/") else {}
        except ProviderError as exc:
            if exc.code in ("auth_failed", "rate_limited", "unreachable"):
                raise
        code = text(space.get("meetingCode") if isinstance(space, dict) else "", 40)
        docs = transcript.get("docsDestination") if isinstance(transcript.get("docsDestination"), dict) else {}
        doc = https_url(docs.get("exportUri"))
        end = moment(record.get("endTime"))
        body = {"title": "Google Meet " + code if code else "Google Meet", "started_at": started.isoformat(),
                "participants": people(*[(n, "") for n in dict.fromkeys(speakers.values())], user),
                "transcript": turns, "media_url": doc,
                "context": {k: v for k, v in {"meeting_url": https_url(space.get("meetingUri") if isinstance(space, dict) else ""),
                                              "transcript_url": doc, "meeting_code": code}.items() if v}}
        if end and 0 < (end - started).total_seconds() <= 86_400:
            body["duration_seconds"] = (end - started).total_seconds()
        return Item(ident, user, body, started, revision)
