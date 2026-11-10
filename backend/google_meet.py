"""Google Meet's REST API (v2), for Google Meet calls in threads (backend/calls.py, docs/meet.md).

Four requests, each with an access token the Workspace key minted for one mailbox and the Meet scope
(`google_workspace.mint(c, mailbox, "meet")`): make a space, read a space (its active conference, when someone is in
it), list the people still in a conference, and end a space's active conference. Every request goes through
`meet_call`, which tests replace; nothing here logs a token, and Google's refusals reach a caller as a kind, never as
Google's text.

The shapes are Google's (developers.google.com/workspace/meet/api/reference/rest/v2): a Space is {name: "spaces/<id>",
meetingUri, meetingCode, config: {accessType}, activeConference: {conferenceRecord: "conferenceRecords/<id>"}}; a
Participant is {name, earliestStartTime, latestEndTime, and one of signedinUser {user, displayName}, anonymousUser
{displayName} or phoneUser {displayName}}. `latest_end_time IS NULL` keeps the participants still in the conference.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://meet.googleapis.com/v2/"
SECONDS = 10  # the sweep polls every live call each beat, so one slow answer must not hold the others long
# TRUSTED: the host's organization, invited guests and dial-ins join without knocking; anyone else knocks.
ACCESS_TYPE = "TRUSTED"
ACTIVE = "latest_end_time IS NULL"
PAGE_SIZE = 250  # Google's largest page
PARTICIPANT_PAGES = 4  # at most 1,000 people read per poll; a call that large is counted, not listed further
SPACE_NAME = re.compile(r"spaces/[A-Za-z0-9_-]{1,128}")
CONFERENCE_NAME = re.compile(r"conferenceRecords/[A-Za-z0-9_-]{1,128}")


class MeetUnavailable(Exception):
    """Google did not do what was asked. `kind`: `refused` (401/403: the scope, the API or the mailbox), `not_found`,
    `unreachable` (no answer, or Google's own error) or `other`."""

    def __init__(self, kind, status=0):
        super().__init__(kind)
        self.kind, self.status = kind, status


def meet_call(method, url, *, token, body=None):
    """(HTTP status, JSON object) from Google: a GET, or a POST with a JSON `body` (an empty one when None). A refusal
    comes back as its status; an unreachable Google as status 0. Tests replace this function."""
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    data = None
    if method == "POST":
        data = json.dumps(body or {}).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            status, raw = answer.status, answer.read()
    except urllib.error.HTTPError as refused:
        with refused:
            try:
                status, raw = refused.code, refused.read()
            except OSError:
                status, raw = refused.code, b""
    except (OSError, ValueError):
        return 0, {}
    try:
        parsed = json.loads(raw.decode() or "{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _kind(status):
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "not_found"
    if status == 0 or status >= 500:
        return "unreachable"
    return "other"


def _ask(method, path, token, body=None, query=None):
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    status, answer = meet_call(method, url, token=token, body=body)
    if status != 200:
        raise MeetUnavailable(_kind(status), status)
    return answer


def _space(answer):
    name, uri = str(answer.get("name") or ""), str(answer.get("meetingUri") or "")
    if not SPACE_NAME.fullmatch(name) or not uri.startswith("https://meet.google.com/"):
        raise MeetUnavailable("other")
    conference = str((answer.get("activeConference") or {}).get("conferenceRecord") or "")
    return {
        "name": name,
        "meeting_uri": uri,
        "meeting_code": str(answer.get("meetingCode") or "")[:64],
        "conference": conference if CONFERENCE_NAME.fullmatch(conference) else None,
    }


def create_space(token):
    """A new space, made as the token's mailbox: {name, meeting_uri, meeting_code, conference}."""
    return _space(_ask("POST", "spaces", token, body={"config": {"accessType": ACCESS_TYPE}}))


def get_space(token, name):
    """The space as it is now; `conference` is its active conference record, or None when nobody is in it."""
    if not SPACE_NAME.fullmatch(str(name or "")):
        raise MeetUnavailable("not_found")
    return _space(_ask("GET", name, token))


def active_participants(token, conference):
    """The people still in `conference`, as [{"name": display name, "user": "users/<id>" or None, "signed_in": bool}].
    Guests and dial-ins are listed with their display name and no user."""
    if not CONFERENCE_NAME.fullmatch(str(conference or "")):
        raise MeetUnavailable("not_found")
    out, page = [], ""
    for _ in range(PARTICIPANT_PAGES):
        query = {"filter": ACTIVE, "pageSize": PAGE_SIZE}
        if page:
            query["pageToken"] = page
        answer = _ask("GET", conference + "/participants", token, query=query)
        for item in answer.get("participants") or []:
            if not isinstance(item, dict) or item.get("latestEndTime"):
                continue
            signed = item.get("signedinUser") if isinstance(item.get("signedinUser"), dict) else None
            who = signed or item.get("anonymousUser") or item.get("phoneUser") or {}
            who = who if isinstance(who, dict) else {}
            out.append(
                {
                    "name": str(who.get("displayName") or "")[:200],
                    "user": (str(signed.get("user") or "")[:200] or None) if signed else None,
                    # Google's Participant has no email today; one that ever carries it is matched by it too.
                    "email": str(who.get("email") or "").strip().lower()[:320] or None,
                    "signed_in": signed is not None,
                }
            )
        page = str(answer.get("nextPageToken") or "")
        if not page:
            break
    return out


def end_active_conference(token, name):
    """Ask Google to end the space's active conference: everyone in it is removed. True when Google did, False when it
    had none or would not; best effort, so it never raises."""
    if not SPACE_NAME.fullmatch(str(name or "")):
        return False
    try:
        _ask("POST", name + ":endActiveConference", token, body={})
    except MeetUnavailable:
        return False
    return True
