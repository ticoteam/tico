#!/usr/bin/env python3
"""A live check of Google Meet as Tico's calls use it (docs/meet.md, Google Meet): how soon Google's API shows who is in a
Meet. Run it once by hand with a Workspace service-account key whose client is authorized for the Meet scope.

    meet-live-check.py --key key.json --mailbox ana@acme.example [--minutes 5] [--every 10]

It makes a space as the mailbox (the key impersonating it, as Tico's server does), prints the join link, then every
`--every` seconds prints the active conference and the people still in it, for `--minutes`, ends the conference and locks the space.
Join and leave the Meet while it runs to see when each poll notices. Ctrl-C ends it early. It talks to Google only,
through Tico's own Meet client (backend/google_meet.py): no Tico server, database or vault is used, and nothing is
written but the space. Needs PyJWT and cryptography (backend/requirements.txt).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt

from backend import google_meet

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/meetings.space.created"


def token(key, mailbox):
    """An access token for `mailbox` and the Meet scope, from a JWT signed with the key."""
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": key["client_email"], "sub": mailbox, "scope": SCOPE, "aud": TOKEN_URI, "iat": now, "exp": now + 3000},
        key["private_key"],
        algorithm="RS256",
    )
    data = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
    ).encode()
    request = urllib.request.Request(TOKEN_URI, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return json.loads(answer.read())["access_token"]
    except urllib.error.HTTPError as refused:
        body = json.loads(refused.read() or b"{}")
        sys.exit(
            f"Google gave no token ({refused.code} {body.get('error', '')}): authorize {SCOPE} for the key's client "
            "in Google Admin (Security > API controls > Domain-wide delegation)."
        )


def stamp():
    return time.strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--key", required=True, help="the service-account key file (JSON)")
    parser.add_argument("--mailbox", required=True, help="the Workspace mailbox the space is made as")
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--every", type=float, default=10, help="seconds between polls")
    args = parser.parse_args()
    key = json.loads(Path(args.key).read_text())
    access = token(key, args.mailbox.strip().lower())
    try:
        space = google_meet.create_space(access)
    except google_meet.MeetUnavailable as exc:
        sys.exit(f"Google did not make the space ({exc.kind}, HTTP {exc.status}): is the Meet API on in the project?")
    print(f"{stamp()} made {space['name']}")
    print(f"{stamp()} join: {space['meeting_uri']}?authuser={urllib.parse.quote(args.mailbox)}")
    until, last = time.monotonic() + args.minutes * 60, None
    try:
        while time.monotonic() < until:
            try:
                now = google_meet.get_space(access, space["name"])
                people = google_meet.active_participants(access, now["conference"]) if now["conference"] else []
                shown = (now["conference"], tuple(sorted(p["name"] or "(no name)" for p in people)))
            except google_meet.MeetUnavailable as exc:
                shown = ("refused", (f"{exc.kind} HTTP {exc.status}",))
            change = "" if shown == last else "  <- changed"
            print(
                f"{stamp()} conference={shown[0] or '-'} in it ({len(shown[1])}): {', '.join(shown[1]) or '-'}{change}"
            )
            last = shown
            time.sleep(args.every)
    except KeyboardInterrupt:
        print()
    ended = google_meet.end_active_conference(access, space["name"])
    print(f"{stamp()} {'ended the conference' if ended else 'no conference to end'}")
    locked = google_meet.lock_space(access, space["name"])
    print(f"{stamp()} {'locked the space (RESTRICTED)' if locked else 'Google did not lock the space'}")


if __name__ == "__main__":
    main()
