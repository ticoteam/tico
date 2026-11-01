"""Zoom cloud recordings through a Server-to-Server OAuth app (https://developers.zoom.us).

Account id, client id and client secret stay in secrets/zoom.env on this computer. For each
active user (or the ones listed in ZOOM_USERS) the importer lists cloud recordings in a window,
downloads the TRANSCRIPT file (WebVTT) of each meeting, and files the meeting for its host.
Zoom must have cloud recording and "Audio transcript" turned on; only meetings that have a
transcript file are imported. Audio and video are never downloaded.
"""

import base64
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from .base import (Importer, Item, ProviderError, Scope, email_of, https_url, moment, people, request_json,
                   safe_id, text, MAX_TRANSCRIPT)

API = "https://api.zoom.us/v2"
TOKEN = "https://zoom.us/oauth/token"
USERS_PAGE = 300
RECORDINGS_PAGE = 300
PAGES = 50
DOWNLOAD_HOSTS = ("zoom.us", "zoomgov.com")


def zoom_host(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in DOWNLOAD_HOSTS)


class Redirects(urllib.request.HTTPRedirectHandler):
    """Follow a download redirect only to Zoom's own hosts: the bearer token rides along."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, newurl) if zoom_host(newurl) else None


class Zoom(Importer):
    source = "zoom"
    name = "Zoom"
    prefix = "ZOOM"
    secret_file = "zoom.env"
    required = ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")
    interval = 600
    window = timedelta(days=7)        # Zoom lists at most a month per request
    pace = 0.1

    def credential_names(self):
        return (*self.required, "ZOOM_USERS", "ZOOM_PRIVATE")

    def token(self):
        if getattr(self, "_token", None) and self._token[1] > time.time() + 60:
            return self._token[0]
        env = self.env()
        basic = base64.b64encode((env["ZOOM_CLIENT_ID"] + ":" + env["ZOOM_CLIENT_SECRET"]).encode()).decode()
        answer = self.send("POST", TOKEN, headers={"Authorization": "Basic " + basic},
                           form={"grant_type": "account_credentials", "account_id": env["ZOOM_ACCOUNT_ID"]})
        value = answer.get("access_token") if isinstance(answer, dict) else None
        if not value:
            raise ProviderError("bad_response", "Zoom returned no access token")
        try:
            lifetime = int(answer.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 3600
        self._token = (value, time.time() + lifetime)
        return value

    def auth(self):
        return {"Authorization": "Bearer " + self.token()}

    def get(self, path, /, **query):
        url = API + path + ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
                            if query else "")
        try:
            return self.send("GET", url, headers=self.auth())
        except ProviderError as exc:
            if exc.status == 401:
                self._token = None
            raise

    def scopes(self):
        wanted = {email_of(u) for u in self.env().get("ZOOM_USERS", "").replace(";", ",").split(",") if u.strip()}
        wanted.discard("")
        users, token = [], ""
        for _ in range(PAGES):
            page = self.get("/users", status="active", page_size=USERS_PAGE, next_page_token=token)
            rows = page.get("users") if isinstance(page, dict) else None
            if not isinstance(rows, list):
                raise ProviderError("bad_response", "Zoom returned no user list")
            users.extend(rows)
            token = page.get("next_page_token") or ""
            if not token:
                break
        out = []
        for user in users:
            email = email_of(user.get("email")) if isinstance(user, dict) else ""
            if email and user.get("id") and (not wanted or email in wanted):
                out.append(Scope(safe_id(user["id"]), email, {"id": str(user["id"]), "email": email}))
        return out

    def recordings(self, user_id, since, until):
        token, out = "", []
        for _ in range(PAGES):
            page = self.get("/users/" + urllib.parse.quote(user_id, safe="") + "/recordings",
                            **{"from": since.strftime("%Y-%m-%d"), "to": until.strftime("%Y-%m-%d"),
                               "page_size": RECORDINGS_PAGE, "next_page_token": token})
            rows = page.get("meetings") if isinstance(page, dict) else None
            if not isinstance(rows, list):
                raise ProviderError("bad_response", "Zoom returned no recording list")
            out.extend(r for r in rows if isinstance(r, dict) and r.get("uuid"))
            token = page.get("next_page_token") or ""
            if not token:
                return out
        raise ProviderError("provider_error", "Zoom returned more recordings than one pass reads")

    def fetch(self, scope, since, until):
        # Zoom's dates are whole UTC days and inclusive; a meeting can appear in two windows, and
        # the idempotency key makes that harmless.
        rows = self.recordings(scope.data["id"], since, until - timedelta(seconds=1))
        for row in sorted(rows, key=lambda r: (str(r.get("start_time")), str(r["uuid"]))):
            started = moment(row.get("start_time"), since)
            if not (since <= started < until):
                continue
            files = [f for f in row.get("recording_files") or [] if isinstance(f, dict)]
            revision = "|".join(sorted(str(f.get("id") or f.get("file_type")) + ":" + str(f.get("status"))
                                       for f in files))
            ident = safe_id(row["uuid"])
            if self.known(scope, ident, revision):
                continue
            item = self.item(scope, row, files, started, ident, revision)
            if item:
                yield item

    @staticmethod
    def transcript_file(files):
        done = [f for f in files if str(f.get("status") or "completed") == "completed"]
        for wanted in ("TRANSCRIPT", "CC"):
            for f in done:
                if str(f.get("file_type")) == wanted and str(f.get("file_extension") or "VTT").upper() == "VTT":
                    return f
        return None

    def download(self, url):
        if not zoom_host(url):
            raise ProviderError("bad_response", "Zoom named a download outside its own hosts")
        if self.transport:
            payload = self.send("GET", url, headers=self.auth(), raw=True)
        else:
            payload = request_json("GET", url, headers=self.auth(), tool="Zoom",
                                   opener=urllib.request.build_opener(Redirects()))
        if len(payload) > MAX_TRANSCRIPT * 4:
            raise ProviderError("bad_response", "The Zoom transcript exceeds the import limit")
        return payload.decode("utf-8-sig", "replace") if isinstance(payload, bytes) else str(payload)

    def participants(self, uuid):
        # A UUID that starts with "/" or holds "//" is encoded twice, as Zoom asks.
        ref = urllib.parse.quote(urllib.parse.quote(uuid, safe=""), safe="") if uuid.startswith("/") or "//" in uuid \
            else urllib.parse.quote(uuid, safe="")
        out, token = [], ""
        try:
            for _ in range(PAGES):
                page = self.get("/past_meetings/" + ref + "/participants", page_size=300, next_page_token=token)
                out.extend(p for p in (page.get("participants") or []) if isinstance(p, dict))
                token = page.get("next_page_token") or ""
                if not token:
                    break
        except ProviderError as exc:
            if exc.code in ("auth_failed", "rate_limited", "unreachable"):
                raise
            return []                # the scope is optional: without it, names come from the transcript
        return [(p.get("name"), p.get("user_email")) for p in out]

    def item(self, scope, row, files, started, ident, revision):
        chosen = self.transcript_file(files)
        if not chosen or not chosen.get("download_url"):
            if files and not any(str(f.get("status")) == "completed" for f in files):
                self.hold_at(started)
            elif files and self.now() - started < timedelta(hours=24):
                self.hold_at(started)          # Zoom writes the transcript some time after the video
            return None
        vtt = self.download(chosen["download_url"])
        if "-->" not in vtt:
            return None
        minutes = row.get("duration")
        body = {"title": text(row.get("topic"), 300) or "Zoom meeting", "started_at": started.isoformat(),
                "participants": people(*self.participants(str(row["uuid"])), scope.data["email"]),
                "format": "vtt", "transcript": vtt,
                "media_url": https_url(row.get("share_url")),
                "context": {k: v for k, v in {"zoom_meeting_id": text(row.get("id"), 40)}.items() if v}}
        if isinstance(minutes, (int, float)) and 0 < minutes <= 1440:
            body["duration_seconds"] = minutes * 60
        return Item(ident, scope.data["email"], body, started, revision)
