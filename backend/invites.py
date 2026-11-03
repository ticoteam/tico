"""Invite emails: the owner clicks Invite and the person gets "You're invited to Tico" from Tico's own sign-in mail,
the sender and family of the sign-in codes, never the company's connected mailbox. The install asks its Tico account
issuer to send it (`Oidc.invite`, the issuer's POST /oauth2/invite), so the mail says only who is invited and where to
sign in. An install that signs in some other way can't send one, and says so: the owner tells the person themselves.

Every outcome is the owner's to see: a refusal is a Problem with a plain reason, and a sent invite is recorded in
registry_metadata `invites` ({person id: {at, by}}) so the People page can say when. A refusal is a 422, never a 409,
429 or 502: the browser retries some of those writes as unconfirmed (ui/app/api.js), which would ask the issuer three
times, spend the address's daily invites and hide the reason.

One request sends at most one email (`once`). The browser repeats a write whose answer it lost with the same
Idempotency-Key, so the request is bound in `idempotency` (binding 'invites', subject the person's id) before the issuer
is asked: a repeat of a sent request answers from it, an overlapping copy or one whose outcome nobody recorded is
refused without sending, and a refusal replays as itself. A new click is a new key and sends again. A route that binds
a request does not also `mutate` under the same key: one row per key. A route asks `replay` before it
writes the roster, so a repeat never adds the person again, even one removed since (people_remove keeps the binding
without the address).

Create password is the other way in (Chris, Oct 25): an owner or admin asks the same issuer to make the person a Tico
account with a generated password (`Oidc.make_account`, the issuer's POST /oauth2/accounts) and the page shows it once.
Tico never keeps it: the route keeps no receipt (no `mutate`, so not the request cache), writes no event, log or
notification with it, and binds the request in `idempotency` like an invite, so a repeat whose answer was lost says
the account was made instead of asking again. Someone who already has a Tico account is refused: use Invite. The
account signs in only here until its owner proves the email with Forgot password on the sign-in page.
"""

import json

from .replication import rehearsal_on
from .store import H, Problem, digest, encode

KEY = "invites"
# The `idempotency` binding of an invite request (backend/store.py's table): subject is the person's id.
BINDING = "invites"
MADE = {
    "account_exists": "{name} already has a Tico account; use Invite.",
    "too_many": "Too many new accounts today. Try again tomorrow.",
    "invalid_email": "Tico accounts can't use {email}. Check the address.",
    "passwords_unavailable": "Tico accounts with a password aren't available right now. Try again later.",
}
# What a removed person's refused invite says on a repeat: the refusal named their address, which removal deletes.
REMOVED = "The person this invite was for was removed."
REASONS = {
    "too_many": "Too many invites to this address today. Try again tomorrow.",
    "invalid_email": "Tico's sign-in mail can't send to {email}. Check the address.",
    "mail_unavailable": "Tico's sign-in mail isn't available right now. Try again later.",
    "not_sent": "Tico's sign-in mail couldn't deliver the invite to {email}. Check the address, then try again.",
}


def sent(c):
    """{person id: {at, by}} for every invite sent."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    value = json.loads(row["value"]) if row else {}
    return value if isinstance(value, dict) else {}


def record(c, actor, pid):
    at = H.now()
    invites = {**sent(c), pid: {"at": at, "by": actor}}
    c.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, encode(invites)))
    H.event(c, actor, "person.invited", pid, {})
    return at


def record_send(store, who, pid, first):
    """The time the invite to `pid` is recorded at. The call that sent it (`first`, from `once`) records it; a repeat
    of the request keeps the time already recorded and writes nothing. A send whose record was lost is recorded now."""
    def work(c):
        recorded = sent(c).get(pid)
        if first or not recorded:
            return record(c, who.actor, pid)
        return recorded["at"]
    return store.write(who, work)


def request_key(request):
    """The write's Idempotency-Key, checked before anything is sent (Store.mutate asks for the same)."""
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 200:
        raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
    return key


def once(store, auth, settings, who, request, body, person):
    """Send `person` their invite for this request at most once (module doc). True when this call sent it, False when
    an earlier copy of the request already did; a refusal, then or now, raises."""
    _available(auth, settings, person)
    return _once(store, who, request, body, person, lambda: send(auth, settings, person)) is not None


def replay(store, who, request, body):
    """The person an earlier copy of this request bound its key for, or None when the key is new and the route runs
    the request. A bound key answers from its binding (`_once`): nothing is written or sent."""
    key, operation = request_key(request), request.url.path
    with store.read() as c:
        row = c.execute("SELECT subject FROM idempotency WHERE binding=? AND actor=? AND operation=? AND key=?",
                        (BINDING, who.actor, operation, key)).fetchone()
    if not row:
        return None
    _once(store, who, request, body, None, None,
          lambda name: ("invite_unknown", "This invite is still being sent, or Tico couldn't tell whether it went. Check "
                                          "the People page before inviting again."))
    return row["subject"]


def make_password(store, auth, settings, who, request, body, person):
    """Make `person` a Tico account with a generated password, at most once for this request: the password, for this
    answer only. A repeat of a request that made one raises (its password is gone); a refusal raises."""
    _available(auth, settings, person, "password")
    name = person.get("name") or "this person"
    made = _once(store, who, request, body, person, lambda: make(auth, settings, person),
                 lambda name: ("password_unknown", f"This account for {name} is still being made, or Tico couldn't tell "
                                                   f"whether it was. Ask {name} to try \"Forgot password?\" at sign-in."))
    if made is None:
        raise Problem("password_shown", f"The account for {name} was made, but its password can't be shown again. Ask "
                                        f"{name} to choose \"Forgot password?\" on the sign-in page.", 422)
    return made


def _once(store, who, request, body, person, work, unknown=None):
    """`work()`'s answer the first time this request runs it, None when an earlier copy already did; a refusal, then
    or now, raises. Only the outcome is kept, never the answer."""
    from .auth import validate_identity

    key, operation, hashed = request_key(request), request.url.path, digest(encode(body.model_dump()))
    with store.transaction() as c:
        validate_identity(c, who)
        row = c.execute("SELECT * FROM idempotency WHERE binding=? AND actor=? AND operation=? AND key=?",
                        (BINDING, who.actor, operation, key)).fetchone()
        if row:
            # A binding whose person was removed has no hash (people_remove): any copy of its request gets its answer.
            if row["request_hash"] not in ("", hashed):
                raise Problem("idempotency_conflict", "This key was used for different content", 409)
            if row["state"] == "completed":
                return None
            if row["state"] == "refused":
                raise Problem(row["error_code"], row["error_detail"], 422)
            name = (person or {}).get("name") or "this person"
            if unknown:
                raise Problem(*unknown(name), 422)
            raise Problem("invite_unknown", f"This invite to {name} is still being sent, or Tico couldn't tell whether it "
                                            f"went. Check the People page or ask {name} before inviting again.", 422)
        now = H.now()
        c.execute("INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at,updated_at,binding,"
                  "subject,state) VALUES(?,?,?,?,NULL,?,?,?,?,'pending')",
                  (who.actor, operation, key, hashed, now, now, BINDING, person["id"]))
    try:
        answer = work()
    except Problem as problem:
        # A definite refusal replays as itself. Anything else leaves the request pending: its outcome is unknown, so
        # no copy of it ever asks the issuer again. A person removed while the issuer was asked (no hash) keeps no
        # refusal text that names their address.
        with store.transaction() as c:
            c.execute("UPDATE idempotency SET state='refused',error_code=?,"
                      "error_detail=CASE WHEN request_hash='' THEN ? ELSE ? END,updated_at=? "
                      "WHERE binding=? AND actor=? AND operation=? AND key=?",
                      (problem.code, REMOVED, problem.detail, H.now(), BINDING, who.actor, operation, key))
        raise
    with store.transaction() as c:
        c.execute("UPDATE idempotency SET state='completed',updated_at=? "
                  "WHERE binding=? AND actor=? AND operation=? AND key=?",
                  (H.now(), BINDING, who.actor, operation, key))
    return answer if answer is not None else True


def rehearsing(settings):
    """A rehearsal is a copy of real data that sends nothing out, mail included (docs/install.md, "Rehearse a
    migration"). Its sign-in settings are still the real install's, so the switch is checked, not the settings."""
    return bool(getattr(settings, "rehearsal", False)) or rehearsal_on()


def can_email(settings):
    """Whether Send invite is offered: only the built-in Tico account sign-in sends invites, and never in a rehearsal."""
    return settings.proxy_kind == "oidc" and not rehearsing(settings)


def can_make(auth, settings):
    """Whether Create password is offered: Tico account sign-in whose issuer makes accounts, never in a rehearsal."""
    proxy = getattr(auth, "proxy", None)
    return can_email(settings) and callable(getattr(proxy, "offers", None)) and proxy.offers("tico_accounts_endpoint")


def _available(auth, settings, person, kind="invite"):
    """Refuse, before anything is bound or sent, an invite (or a made account) that cannot go: a rehearsal, no
    address, or no Tico account issuer."""
    email, name = person.get("email") or "", person.get("name") or "this person"
    proxy = getattr(auth, "proxy", None)
    if kind == "password":
        if rehearsing(settings):
            raise Problem("rehearsal", "Accounts aren't made in a rehearsal: this server is a copy of real data.", 422)
        if not email:
            raise Problem("password_email", f"{name} has no email address for a Tico account", 422)
        if not proxy or getattr(proxy, "name", "") != "oidc" or not hasattr(proxy, "make_account"):
            raise Problem("password_unavailable", f"This Tico doesn't sign in with Tico accounts, so it can't make "
                                                  f"one. Use your sign-in provider's own accounts for {name}.", 422)
        return
    if rehearsing(settings):
        raise Problem("rehearsal", f"Invites are off in a rehearsal: this server is a copy of real data and sends no "
                                   f"mail. Tell {name} to sign in at {settings.public_url}.", 422)
    if not email:
        raise Problem("invite_email", f"{name} has no email address to invite", 422)
    if not proxy or getattr(proxy, "name", "") != "oidc" or not hasattr(proxy, "invite"):
        raise Problem("invite_unavailable", f"This Tico doesn't sign in with Tico accounts, so it can't email invites. "
                                            f"Tell {name} to sign in at {settings.public_url} with {email}.", 422)


def send(auth, settings, person):
    """Mail `person` (a roster row) an invite to sign in here, or raise a Problem the owner sees. Runs outside a
    transaction: it waits on the issuer, which waits on the mail. Routes go through `once`."""
    from .oidc import SignInError

    _available(auth, settings, person)
    email, name = person["email"], person.get("name") or "this person"
    try:
        answer = auth.proxy.invite(email)
    except SignInError:
        raise Problem("invite_failed", "Tico's sign-in service couldn't be reached, so the invite wasn't sent. "
                                       "Try again in a minute.", 422) from None
    if answer is None:
        raise Problem("invite_unavailable", f"This Tico's sign-in provider doesn't send Tico invites. "
                                            f"Tell {name} to sign in at {settings.public_url} with {email}.", 422)
    status, body = answer
    if status == 202 and body.get("status") == "sent":
        return
    code = str(body.get("error") or "")
    reason = REASONS.get(code, "Tico's sign-in mail refused the invite, so it wasn't sent.").format(email=email)
    raise Problem("invite_limited" if code == "too_many" else "invite_failed", reason, 422)


def make(auth, settings, person):
    """Ask the issuer for `person`'s account: their generated password, or a Problem the owner sees. Runs outside a
    transaction. Routes go through `make_password`."""
    from .oidc import SignInError

    email, name = person["email"], person.get("name") or "this person"
    try:
        answer = auth.proxy.make_account(email)
    except SignInError:
        raise Problem("password_failed", "Tico's sign-in service couldn't be reached, so no account was made. "
                                         "Try again in a minute.", 422) from None
    if answer is None:
        raise Problem("password_unavailable", f"This Tico's sign-in provider doesn't make Tico accounts. "
                                              f"Use Invite for {name}.", 422)
    status, body = answer
    password = body.get("password")
    if status == 201 and body.get("status") == "created" and isinstance(password, str) and len(password) >= 16:
        return password
    code = str(body.get("error") or "")
    reason = MADE.get(code, "Tico's sign-in service refused, so no account was made.").format(email=email, name=name)
    raise Problem({"account_exists": "password_exists", "too_many": "password_limited"}.get(code, "password_failed"),
                  reason, 422)
