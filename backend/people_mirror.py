"""The install's sign-in list, mirrored to the Tico account (docs/people.md, "Tico account").

Only an install signed in with a Tico account sends it, and only to the issuer that advertises
`tico_people_endpoint`. The account uses it to show a person their companies and Tico servers after they sign in
at tico.dev. It never grants access: this install's roster alone decides who signs in here.

A change to the roster or the access lists asks for one push ten seconds later, off the request path; so does
startup (a minute on) and then every 24 hours. A failure is logged without any address and tried at the next
trigger. A 429 waits for the time the account names.
"""

import logging
import threading
import time

log = logging.getLogger("tico.people_mirror")

DEBOUNCE_SECONDS = 10
STARTUP_SECONDS = 60
EVERY_SECONDS = 24 * 3600
RETRY_MIN, RETRY_MAX = 60, 3600
MAX_PEOPLE = 5000               # the account refuses more (PEOPLE-CONTRACT.md)

_lock = threading.Lock()
_auth = None                    # the process's Auth, once start() found a Tico account sign-in
_timer = None
_looping = False


def start(auth):
    """Called at startup: with a Tico account sign-in, one push a minute on and then one a day."""
    global _auth, _looping
    from .oidc import Oidc
    if not isinstance(getattr(auth, "proxy", None), Oidc):
        return
    with _lock:
        _auth = auth
        if _looping:
            return
        _looping = True
    threading.Thread(target=_loop, name="tico-people-mirror", daemon=True).start()


def _loop():
    time.sleep(STARTUP_SECONDS)
    while True:
        send()
        time.sleep(EVERY_SECONDS)


def changed():
    """A roster or access change was saved: send the list again, ten seconds after the last one."""
    global _timer
    with _lock:
        if _auth is None:
            return
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, send)
        _timer.daemon = True
        _timer.start()


def payload(c, settings):
    """The people who may sign in to this install, with their role: no bots, nobody left or with sign-in off."""
    from . import access
    from .views import roster as load_roster
    roster = load_roster(c)
    owner = access.load_owner(c, settings)
    lists = access.load_access(c, settings)
    rows, seen = [], set()
    for person in roster["people"]:
        email = str(person.get("email") or "").strip().lower()
        if (not email or email in seen or person.get("bot") or person.get("hidden")
                or person.get("directory_left") or not person.get("sign_in", True)):
            continue
        seen.add(email)
        rows.append({"email": email, "role": access.role_of(lists, owner["email"], email)})
    rows.sort(key=lambda row: row["email"])
    if len(rows) > MAX_PEOPLE:
        log.warning("the sign-in list has %d people; the Tico account takes %d", len(rows), MAX_PEOPLE)
        rows = rows[:MAX_PEOPLE]
    return rows


def push_now(auth=None):
    """Send the list now. (status, answer) from the account, or None when nothing is sent (no Tico account sign-in,
    no endpoint, or a rehearsal). Errors are raised for the caller."""
    auth = auth or _auth
    if auth is None:
        return None
    from .oidc import Oidc
    if not isinstance(auth.proxy, Oidc):
        return None
    with auth.store.read() as c:
        rows = payload(c, auth.settings)
    return auth.proxy.push_people(rows)


def send():
    """The background push: logs the outcome and schedules a retry only for a 429."""
    global _timer
    try:
        reply = push_now()
    except Exception as exc:
        log.warning("the sign-in list was not sent to the Tico account: %s", getattr(exc, "reason", type(exc).__name__))
        return
    if reply is None:
        return
    status, answer = reply
    if status == 200:
        log.info("the sign-in list was sent to the Tico account: %s stored", answer.get("stored"))
        return
    if status == 429:
        wait = answer.get("retry_after")
        wait = max(RETRY_MIN, min(RETRY_MAX, int(wait))) if isinstance(wait, (int, float)) else RETRY_MIN
        log.warning("the Tico account is limiting the sign-in list; it will be sent again in %d s", wait)
        with _lock:
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(wait, send)
            _timer.daemon = True
            _timer.start()
        return
    log.warning("the Tico account refused the sign-in list: HTTP %s", status)
