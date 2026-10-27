"""Where a saved credential is sent, and whether an edit moves it: the one rule the Gateway connection
(backend/gateway_connection.py), outgoing webhooks with secret headers (backend/webhooks.py) and BotOps' Confirm card for a
person (backend/app.py `person_card`) all use.

A service is its scheme, host (lowercase, punycode) and effective port (443 for https and 80 for http when none is
written). An edit that keeps the service keeps the credential with it and saves with no question. An edit that changes
it, while a credential is saved, is held (`held`): the editor says whether the saved credentials go to the new address
(`KEEP`) or are cleared to be typed again (`CLEAR`), and the save says which in one audit event (`record`). A webhook
that holds secret headers also counts its path and query, because receivers shared by many senders tell recipients
apart by path (`path_counts`).

Nothing this module returns carries a credential, a key preview, a user or password, a query, a fragment or path text:
`safe` is scheme, host and port only, and where the path counts it says "same path" or "path changed" with a short
fingerprint keyed by the vault's data key, so two addresses can be told apart without showing either path.

Integrations saved before this rule are not migrated: an edit compares the address it replaces with the new one, so the
address saved today is the approved one.
"""

import hashlib
import hmac
import urllib.parse

from .store import H, Problem

KEEP, CLEAR = "keep", "clear"
CHOICES = (KEEP, CLEAR)
# What an edit that replaced every saved credential in the same save records: nothing saved went to the new address.
REPLACED = "replaced"
EVENT = "integration.destination_changed"
DEFAULT_PORTS = {"https": 443, "http": 80}
SAME_PATH, PATH_CHANGED = "same path", "path changed"


def identity(url):
    """(scheme, host, port, path, query) of `url`, normalized for comparing, or None when it has no scheme and host.
    The host is lowercase and punycode (an IDN and its xn-- form are one host); the port is the effective one."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if not scheme or not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass                                              # already ASCII, or not a name IDNA takes: compared as typed
    port = port if port is not None else DEFAULT_PORTS.get(scheme)
    return scheme, host.lower().rstrip("."), port, parts.path or "/", parts.query


def same_service(old, new, *, path_counts=False):
    """Whether `new` sends to the same service as `old`: scheme, host and effective port, and with `path_counts` (a
    webhook holding secret headers) the path and query too. An address that cannot be read is never the same."""
    a, b = identity(old), identity(new)
    if a is None or b is None:
        return False
    return a == b if path_counts else a[:3] == b[:3]


def address(url):
    """`scheme://host:port` of `url` with the port always written, or '' when it is not one: the only part of an
    address any prompt, event or log shows."""
    found = identity(url)
    if not found:
        return ""
    scheme, host, port = found[:3]
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}:{port}" if port is not None else f"{scheme}://{host}"


def fingerprint(key, url):
    """A short digest of `url`'s path and query keyed by `key` (the vault's data key): the same path gives the same
    one, and the path cannot be read back from it. '' with no key."""
    found = identity(url)
    if not key or not found:
        return ""
    return hmac.new(key, b"tico-destination-path:v1\n" + f"{found[3]}?{found[4]}".encode(), hashlib.sha256).hexdigest()[:10]


def safe(url):
    """What may be shown of a destination: its scheme, host and port, never more."""
    found = identity(url)
    if not found:
        return {"address": "", "scheme": "", "host": "", "port": None}
    return {"address": address(url), "scheme": found[0], "host": found[1], "port": found[2]}


def change(old, new, *, path_counts=False, key=b""):
    """None when `new` is the same service as `old`, else what the editor is shown and the audit event keeps: the old
    and new destination (`safe`), and with `path_counts` whether the path is the same and each one's fingerprint."""
    if same_service(old, new, path_counts=path_counts):
        return None
    out = {"old": safe(old), "new": safe(new)}
    if path_counts:
        a, b = identity(old), identity(new)
        out["path"] = SAME_PATH if a and b and a[3:] == b[3:] else PATH_CHANGED
        out["old"]["fingerprint"], out["new"]["fingerprint"] = fingerprint(key, old), fingerprint(key, new)
    return out


def choice(value):
    """The editor's answer to a held save: KEEP, CLEAR or '' when none was given. Anything else is refused."""
    value = str(value or "").strip().lower()
    if value and value not in CHOICES:
        raise Problem("credentials", "Answer a destination change with credentials \"keep\" or \"clear\"", 422)
    return value


def held(integration, moved, labels, revision, name=""):
    """The refusal a destination change gets while credentials are saved and the editor has not said what happens to
    them: the old and new destination and the credentials' labels, never their values. Nothing was saved."""
    old, new = moved["old"]["address"] or "an address that is not a URL", moved["new"]["address"] or "the default address"
    what = ", ".join(labels)
    detail = (f"This sends the saved {what} to {new} instead of {old}. Send again with credentials \"keep\" to send the saved "
              f"credentials to the new address, or \"clear\" to remove them and type them again. Nothing was saved.")
    return Problem("destination_change", detail, 409, extra={
        "integration": integration, "name": name, "old": moved["old"], "new": moved["new"],
        **({"path": moved["path"]} if "path" in moved else {}),
        "credentials": list(labels), "revision": int(revision or 0), "choices": list(CHOICES)})


def require_tap(who, moved):
    """A destination change asked for a person (BotOps or the Assistant acting for them) runs only from that person's
    own tap on its Confirm card, whatever the request says about the credentials: the card is made before the route
    runs (backend/app.py `person_card`), and this holds even where it was not."""
    if moved and getattr(who, "via", "") and not getattr(who, "confirmed", False):
        raise Problem("confirm_required", "Moving saved credentials to another address needs the person's own tap on "
                      "a Confirm card. Nothing was saved.", 403)


def record(c, who, integration, target, moved, labels, option, revision):
    """The one audit event a destination change leaves: who, when (the event's own time), the safe old and new
    destination, which credentials were kept or cleared and what the editor chose."""
    kept = list(labels) if option == KEEP else []
    cleared = list(labels) if option in (CLEAR, REPLACED) else []
    H.event(c, who.actor, EVENT, target, {
        "integration": integration, "old": moved["old"], "new": moved["new"],
        **({"path": moved["path"]} if "path" in moved else {}),
        "kept": kept, "cleared": cleared, "option": option, "revision": int(revision or 0),
        **({"confirmed_by": who.actor} if getattr(who, "confirmed", False) else {})})
