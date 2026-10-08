"""Personal API tokens: a person's own bearer for scripts, without a browser sign-in.

Today only the browser sign-in makes a person; a bot administrator who wants to add a bot from
a script has no credential that is them. A personal token is that credential. It is stored as
a hash, shown once, expires, and is the person for every purpose but one: a token cannot make
or revoke tokens, so a leaked token cannot extend its own life (`Identity.via_token`).

Any person may create one, and it sees what they see. The owner's rule "Members make personal tokens" (backend/team_rules.py),
off, leaves it to the owner and the Admins. The owner and the Admins see everyone's tokens and may revoke any of them,
so a token found in the wrong place stops without its person.
"""

import secrets

from . import team_rules
from .store import H, Problem, digest

PREFIX = "tico_pt_"
FIELDS = ("id", "label", "created", "last_used", "expires_at", "revoked_at")


def _person(who):
    """The token routes take a signed-in person and never a token."""
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Personal tokens belong to people", 403)
    if who.via_token:
        raise Problem("forbidden", "Manage tokens from a signed-in browser", 403)


def no_minting(who, what):
    """A token makes no other standing credential (an agent credential, a service key, a SCIM token): one made by a
    leaked token would keep working after the token is revoked."""
    if who.via_token:
        raise Problem("forbidden", what + " from a signed-in browser: an API token cannot make other credentials", 403)


def can_create(c, auth, who):
    return bool(auth.bot_admin(who) or team_rules.load(c)["member_tokens"])


def listing(c, who):
    """This person's tokens, newest first; never the secret or its hash."""
    _person(who)
    rows = c.execute("SELECT " + ",".join(FIELDS) + " FROM human_tokens WHERE human=? "
                     "ORDER BY created DESC", (H.actor_id(who.actor),)).fetchall()
    return [dict(row) for row in rows]


def listing_all(c, auth, who):
    """Every person's tokens, newest first, with whose each is: the owner's and the Admins' view."""
    _person(who)
    if not auth.bot_admin(who):
        raise Problem("forbidden", "Only the owner and the admins see everyone's tokens", 403)
    rows = c.execute("SELECT " + ",".join("t." + f for f in FIELDS) + ",t.human,h.name,h.email FROM human_tokens t "
                     "LEFT JOIN humans h ON h.id=t.human ORDER BY t.created DESC").fetchall()
    return [dict(row) for row in rows]


def create(c, auth, who, body):
    """Mint a token for the caller and return its plaintext, the one time it is shown."""
    _person(who)
    if not can_create(c, auth, who):
        raise Problem("forbidden", "Personal tokens are for the owner and admins", 403)
    token = PREFIX + secrets.token_urlsafe(30)          # 30 bytes: 40 url-safe characters
    now = H.now()
    expires_at = H.shift(now, days=body.expires_in_days)
    token_id = H.new_id()
    c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created,created_by,expires_at) "
              "VALUES(?,?,?,?,?,?,?)",
              (token_id, H.actor_id(who.actor), body.label, digest(token), now, who.actor, expires_at))
    H.event(c, who.actor, "token.create", token_id, {"label": body.label, "expires_at": expires_at})
    return {"id": token_id, "token": token, "label": body.label, "expires_at": expires_at}


def revoke(c, auth, who, token_id):
    """Stop a token at once. Your own; the owner and the Admins may revoke anyone's."""
    _person(who)
    row = c.execute("SELECT id,human,label,revoked_at FROM human_tokens WHERE id=?", (token_id,)).fetchone()
    if not row or (not auth.bot_admin(who) and row["human"] != H.actor_id(who.actor)):
        raise Problem("not_found", "Token not found", 404)
    if row["revoked_at"]:
        raise Problem("revoked", "This token is already revoked", 409)
    c.execute("UPDATE human_tokens SET revoked_at=? WHERE id=?", (H.now(), token_id))
    H.event(c, who.actor, "token.revoke", token_id, {"label": row["label"], "human": row["human"]})
    return {"id": token_id, "revoked": True}
