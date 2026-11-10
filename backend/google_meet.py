# pyright: strict
"""Google Meet's REST API (v2), for Google Meet calls in threads (backend/calls.py, docs/meet.md).

Five requests, each with an access token the Workspace key minted for one mailbox and the Meet scope
(`google_workspace.mint(c, mailbox, "meet")`): make a space, read a space (its active conference, when someone is in
it), list the people still in a conference, end a space's active conference, and lock a space once its call is over. Every request goes through
`meet_call`, which tests replace; nothing here logs a token, and Google's refusals reach a caller as a kind, never as
Google's text.

The shapes are Google's (developers.google.com/workspace/meet/api/reference/rest/v2): a Space is {name: "spaces/<id>",
meetingUri, meetingCode, config: {accessType}, activeConference: {conferenceRecord: "conferenceRecords/<id>"}}; a
Participant is {name, earliestStartTime, latestEndTime, and one of signedinUser {user, displayName}, anonymousUser
{displayName} or phoneUser {displayName}}. `latest_end_time IS NULL` keeps the participants still in the conference.
"""

import json
import re
from typing import Any, cast
import urllib.error
import urllib.parse
import urllib.request

API = "https://meet.googleapis.com/v2/"
SECONDS = 10  # the sweep polls every live call each beat, so one slow answer must not hold the others long
# TRUSTED: the host's organization, invited guests and dial-ins join without knocking; anyone else knocks.
ACCESS_TYPE = "TRUSTED"
# RESTRICTED: only people the host invited join without knocking. An ended call's space is locked so its link is spent.
LOCKED_ACCESS_TYPE = "RESTRICTED"
ACTIVE = "latest_end_time IS NULL"
PAGE_SIZE = 250  # Google's largest page
PARTICIPANT_PAGES = 4  # at most 1,000 people read per poll; a call that large is counted, not listed further
SPACE_NAME = re.compile(r"spaces/[A-Za-z0-9_-]{1,128}")
CONFERENCE_NAME = re.compile(r"conferenceRecords/[A-Za-z0-9_-]{1,128}")


Json = dict[str, Any]


class MeetUnavailable(Exception):
    """Google did not do what was asked. `kind`: `refused` (401/403: the scope, the API or the mailbox), `not_found`,
    `unreachable` (no answer, Google's own error, or 429: Google asking Tico to slow down; each passes) or `other`."""

    def __init__(self, kind: str, status: int = 0) -> None:
        super().__init__(kind)
        self.kind, self.status = kind, status


def meet_call(method: str, url: str, *, token: str, body: Json | None = None) -> tuple[int, Json]:
    """(HTTP status, JSON object) from Google: a GET, or a POST or PATCH with a JSON `body` (an empty one when None). A
    refusal comes back as its status; an unreachable Google as status 0. Tests replace this function."""
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    data = None
    if method in ("POST", "PATCH"):
        data = json.dumps(body or {}).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            status, raw = int(answer.status), bytes(answer.read())
    except urllib.error.HTTPError as refused:
        with refused:
            try:
                status, raw = refused.code, refused.read()
            except OSError:
                status, raw = refused.code, b""
    except (OSError, ValueError):
        return 0, {}
    try:
        parsed: object = json.loads(raw.decode() or "{}")
    except ValueError:
        parsed = {}
    return status, cast(Json, parsed) if isinstance(parsed, dict) else {}


def _kind(status: int) -> str:
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "not_found"
    if status in (0, 429) or status >= 500:
        return "unreachable"
    return "other"


def _ask(method: str, path: str, token: str, body: Json | None = None, query: Json | None = None) -> Json:
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    status, answer = meet_call(method, url, token=token, body=body)
    if status != 200:
        raise MeetUnavailable(_kind(status), status)
    return answer


def _object(value: object) -> Json:
    return cast(Json, value) if isinstance(value, dict) else {}


def _text(value: object, size: int) -> str:
    return str(value or "")[:size]


def _space(answer: Json) -> Json:
    name, uri = _text(answer.get("name"), 200), _text(answer.get("meetingUri"), 300)
    if not SPACE_NAME.fullmatch(name) or not uri.startswith("https://meet.google.com/"):
        raise MeetUnavailable("other")
    conference = _text(_object(answer.get("activeConference")).get("conferenceRecord"), 200)
    return {
        "name": name,
        "meeting_uri": uri,
        "meeting_code": _text(answer.get("meetingCode"), 64),
        "conference": conference if CONFERENCE_NAME.fullmatch(conference) else None,
    }


def create_space(token: str) -> Json:
    """A new space, made as the token's mailbox: {name, meeting_uri, meeting_code, conference}."""
    return _space(_ask("POST", "spaces", token, body={"config": {"accessType": ACCESS_TYPE}}))


def get_space(token: str, name: str) -> Json:
    """The space as it is now; `conference` is its active conference record, or None when nobody has the Meet open.
    A conference alone is not someone in the call: Google opens one for the pre-join page (`active_participants`)."""
    if not SPACE_NAME.fullmatch(str(name or "")):
        raise MeetUnavailable("not_found")
    return _space(_ask("GET", name, token))


def active_participants(token: str, conference: str) -> list[Json]:
    """The people still in `conference`, as [{"name": display name, "user": "users/<id>" or None, "email", "signed_in"}].
    Guests and dial-ins are listed with their display name and no user. Google answers `{}` for a conference with
    nobody in it."""
    if not CONFERENCE_NAME.fullmatch(str(conference or "")):
        raise MeetUnavailable("not_found")
    out: list[Json] = []
    page = ""
    for _ in range(PARTICIPANT_PAGES):
        query: Json = {"filter": ACTIVE, "pageSize": PAGE_SIZE}
        if page:
            query["pageToken"] = page
        answer = _ask("GET", conference + "/participants", token, query=query)
        listed = answer.get("participants")
        for value in cast(list[object], listed) if isinstance(listed, list) else []:
            item = _object(value)
            if not item or item.get("latestEndTime"):
                continue
            signed = _object(item.get("signedinUser"))
            who = signed or _object(item.get("anonymousUser")) or _object(item.get("phoneUser"))
            out.append(
                {
                    "name": _text(who.get("displayName"), 200),
                    "user": _text(signed.get("user"), 200) or None,
                    # Google's Participant has no email today; one that ever carries it is matched by it too.
                    "email": _text(who.get("email"), 320).strip().lower() or None,
                    "signed_in": bool(signed),
                }
            )
        page = _text(answer.get("nextPageToken"), 2000)
        if not page:
            break
    return out


def end_active_conference(token: str, name: str) -> bool:
    """Ask Google to end the space's active conference: everyone in it is removed. True when Google did, False when it
    had none or would not; best effort, so it never raises."""
    if not SPACE_NAME.fullmatch(str(name or "")):
        return False
    try:
        _ask("POST", name + ":endActiveConference", token, body={})
    except MeetUnavailable:
        return False
    return True


def lock_space(token: str, name: str) -> bool:
    """Make the space RESTRICTED, so its link no longer lets anyone straight in once its call is over. True when Google
    did, False when it would not; best effort, so it never raises."""
    if not SPACE_NAME.fullmatch(str(name or "")):
        return False
    try:
        _ask(
            "PATCH",
            name,
            token,
            body={"config": {"accessType": LOCKED_ACCESS_TYPE}},
            query={"updateMask": "config.accessType"},
        )
    except MeetUnavailable:
        return False
    return True
