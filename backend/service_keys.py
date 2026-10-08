"""Service keys: what another system holds to reach a few routes of this install, and nothing else.

A key has one scope. A `tasks` key (the default) lets a product backend put work in front of a person
when something happens there (a weekly report is ready for its account manager to review) and take it
away when the work is done there: one route, POST /api/v2/inbound/tasks. An `update` key lets a release
bot on another install update this one: check for updates, start an update to a named release and read
how it stands (the owner's "Update now", UPDATE_ROUTES). A person's own token would carry everything
that person may do; `Auth.authenticate` refuses a key on every route outside its scope. It is stored as a
hash, shown once, and lasts until it is revoked; the owner and the bot administrators make, list and
revoke keys, and only the owner makes an `update` key, since only the owner updates the install. A key made with
a personal token ends when that token is revoked or expires (`made_with_token`), so a leaked token leaves no key behind.

The route is an upsert of the work as the other system sees it now. The pair (service key, that
system's own `key` for the work) names one task, so a call never depends on an earlier one: the
first call for a pair files the task, a later call changes what differs, `close` closes it, a call
without `close` reopens a closed one, and a close for work that never had a task files nothing.
Tasks are filed by Tico itself (the keeper, as routines file theirs), with every rule a create
has, and their description ends with a line naming the key's label, which is what a person sees.
"""

import secrets

from fastapi import Request

from . import models as M
from . import rooms
from .store import H, Problem, digest

PREFIX = "tico_sk_"
INBOUND_PATH = "/api/v2/inbound/tasks"
UPDATE_PATH = "/api/v2/system/update"
# The (method, path) pairs each scope reaches. /healthz needs no key at all.
ROUTES = {
    "tasks": {("POST", INBOUND_PATH)},
    "update": {("GET", UPDATE_PATH), ("POST", UPDATE_PATH), ("POST", UPDATE_PATH + "/check")},
}
FIELDS = ("id", "label", "scope", "created", "created_by", "last_used", "revoked_at", "revoked_by", "made_with_token")


def allowed(scope, method, path):
    return (method, path) in ROUTES.get(scope or "tasks", set())


def token_ended(c, token_id):
    """Whether the personal token a key was made with is revoked, expired or gone; False for a key made signed in."""
    if not token_id:
        return False
    row = c.execute("SELECT revoked_at,expires_at FROM human_tokens WHERE id=?", (token_id,)).fetchone()
    return not row or bool(row["revoked_at"]) or bool(row["expires_at"] and row["expires_at"] <= H.now())


def refusal(scope):
    if scope == "update":
        return "An update key only checks for updates, starts one and reads its status (" + UPDATE_PATH + ")"
    return "A service key only files tasks, with POST " + INBOUND_PATH


def _admin(auth, who):
    """The owner and the admins, as themselves: never a bot, never the Assistant."""
    if who.role not in ("owner", "human") or who.via or not auth.bot_admin(who):
        raise Problem("forbidden", "Service keys are made and revoked by the owner and the admins", 403)


def listing(c, auth, who):
    """Every service key, newest first, with the label of the token it ends with; never the secret or its hash."""
    _admin(auth, who)
    return [dict(row) for row in c.execute("SELECT " + ",".join("k." + f for f in FIELDS) + ",t.label AS token_label "
                                           "FROM service_keys k LEFT JOIN human_tokens t ON t.id=k.made_with_token "
                                           "ORDER BY k.created DESC")]


def create(c, auth, who, body):
    """Mint a key and return its plaintext, the one time it is shown."""
    _admin(auth, who)
    if body.scope == "update" and who.role != "owner":
        raise Problem("forbidden", "Only the owner makes an update key: only the owner updates this install", 403)
    key, key_id, now = PREFIX + secrets.token_urlsafe(30), H.new_id(), H.now()
    made_with = who.token_id or None
    c.execute("INSERT INTO service_keys(id,label,key_hash,created,created_by,scope,made_with_token) VALUES(?,?,?,?,?,?,?)",
              (key_id, body.label, digest(key), now, who.actor, body.scope, made_with))
    H.event(c, who.actor, "service_key.create", key_id, {"label": body.label, "scope": body.scope, "made_with_token": made_with})
    return {"id": key_id, "key": key, "label": body.label, "scope": body.scope, "created": now, "made_with_token": made_with}


def revoke(c, auth, who, key_id):
    """Stop a key at once. Its tasks stay as they are."""
    _admin(auth, who)
    row = c.execute("SELECT id,label,revoked_at FROM service_keys WHERE id=?", (key_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Service key not found", 404)
    if row["revoked_at"]:
        raise Problem("revoked", "This service key is already revoked", 409)
    c.execute("UPDATE service_keys SET revoked_at=?,revoked_by=? WHERE id=?", (H.now(), who.actor, key_id))
    H.event(c, who.actor, "service_key.revoke", key_id, {"label": row["label"]})
    return {"id": key_id, "revoked": True}


def _owner(c, value):
    """`human:<id>`, `bot:<slug>`, or the email of a person on the roster."""
    value = str(value).strip()
    if value.startswith(("human:", "bot:")):
        actor = H.resolve_actor(c, value)
    else:
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (value.lower(),)).fetchone() if "@" in value else None
        actor = H.human_actor(row["id"]) if row else None
    if not actor:
        raise Problem("owner", value + " is not a person or a bot on the roster: send human:<id>, bot:<slug> or "
                      "a person's email", 422)
    return actor


def _type_and_step(c, body, row):
    """The type and step asked for, by id or name, refused by name when there is none."""
    typ = H.type_get(c, body.type) if body.type else None
    if body.type and not typ:
        raise Problem("type", "No task type " + body.type, 422)
    step = None
    if body.step:
        within = typ or H.type_get(c, (row or {}).get("type_id") or H.GENERAL_TYPE)
        step = next((s["id"] for s in within["steps"] if body.step in (s["id"], s["name"])), None)
        if not step:
            raise Problem("step", "No step " + body.step + " in " + within["name"], 422)
    return (typ or {}).get("id"), step


def upsert(c, auth, who, body):
    """Make the task for (this key, `body.key`) look the way the other system says it is now."""
    key_id, footer = H.actor_id(who.actor), "\n\n_Filed by " + who.token_label + " (service key)._"
    if body.due and (not H.parse_ts(body.due) or H.parse_ts(body.due).tzinfo is None):
        raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
    pair = c.execute("SELECT task_id FROM service_key_tasks WHERE key_id=? AND external_key=?",
                     (key_id, body.key)).fetchone()
    row = H.task(c, pair["task_id"]) if pair else None
    if row and H.task_private(c, row):
        raise Problem("forbidden", "This work is unavailable to a service key", 403)
    created = changed = False
    if not row and not body.close:
        missing = [name for name in ("owner", "title", "body") if not getattr(body, name)]
        if missing:
            raise Problem("validation", "A new task needs " + ", ".join(missing), 422)
        owner = _owner(c, body.owner)
        type_id, step = _type_and_step(c, body, None)
        # The pair is what makes it one task, so two pieces of work may share a title and an owner.
        row = H.task_create(c, H.KEEPER, body.title, body.body, owner, body.due, deduplicate=False,
                            conversation_id=rooms.task_conversation_id(c, auth, owner, H.KEEPER),
                            labels=body.labels or [], type=type_id, step=step)
        # Rule 7 above read the other system's own words; the line naming it is Tico's.
        c.execute("UPDATE tasks SET body=? WHERE id=?", (body.body + footer, row["id"]))
        c.execute("INSERT INTO service_key_tasks(key_id,external_key,task_id,created) VALUES(?,?,?,?)",
                  (key_id, body.key, row["id"], H.now()))
        for url in dict.fromkeys(body.links):
            H.task_link(c, H.KEEPER, row["id"], url)
        created = changed = True
    elif row and not (body.close and row["status"] == "closed"):     # closed, and to stay closed: as it is
        changed = _apply(c, body, row, footer)
    H.event(c, who.actor, "service_key.use", key_id, {"key": body.key, "task": row["id"] if row else None,
                                                       "created": created, "changed": changed})
    # A caller may write its own work, but cannot retrieve changes or attachments from teammates.
    return {"task": {"id": row["id"]} if row else None, "created": created, "changed": changed}


def _apply(c, body, row, footer):
    """Write what differs between the task and the call, reopening or closing it; whether anything did.
    The title is the create's: a later call leaves it as it is."""
    type_id, step = _type_and_step(c, body, row)
    current = {"owner": row["owner"], "body": row["body"], "labels": H.task_labels(row), "due": row["due"],
               "type": row["type_id"], "step": row["step_id"]}
    wanted = {"owner": _owner(c, body.owner) if body.owner else None,
              "body": body.body + footer if body.body is not None else None,
              "labels": H._labels(body.labels) if body.labels is not None else None,
              "due": body.due, "type": type_id, "step": step}
    fields = {name: value for name, value in wanted.items() if value is not None and value != current[name]}
    if row["status"] == "closed" and "step" not in fields:
        fields["status"] = "open"               # no `close`: the work is open again over there
    if fields:
        H.task_update(c, H.KEEPER, row["id"], mover=True, **fields)
    have = {link["url"] for link in H.task_links(c, row["id"])}
    links = [url for url in dict.fromkeys(body.links) if url not in have]
    for url in links:
        H.task_link(c, H.KEEPER, row["id"], url)
    closing = body.close and H.task(c, row["id"])["status"] != "closed"
    if closing:
        H.task_close(c, H.KEEPER, row["id"], note=body.note or "")
    if fields or links or closing:
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (row["id"],))
    return bool(fields or links or closing)


def install_service_keys(app, store, auth, mutate):
    @app.get("/api/v2/service-keys")
    def list_service_keys(request: Request):
        with store.read() as c:
            return {"keys": listing(c, auth, request.state.identity)}

    @app.post("/api/v2/service-keys")
    def create_service_key(request: Request, body: M.ServiceKeyCreate):
        who = request.state.identity
        _admin(auth, who)
        secret = []
        def work(c):
            made = create(c, auth, who, body)
            # Idempotency stores only the acknowledgment; the one-time secret stays in this response.
            secret.append(made.pop("key"))
            return made
        made = mutate(request, body, work)
        return {**made, "key": secret[0]} if secret else made

    @app.post("/api/v2/service-keys/{key_id}/revoke")
    def revoke_service_key(request: Request, key_id: str, body: M.Empty):
        return mutate(request, body, lambda c: revoke(c, auth, request.state.identity, key_id))

    @app.post(INBOUND_PATH)
    def inbound_task(request: Request, body: M.InboundTask):
        """No Idempotency-Key: the pair is the idempotency, and a stored answer replayed after a
        later call for the same work would undo that call."""
        who = request.state.identity
        if who.role != "service" or who.scope != "tasks":
            raise Problem("forbidden", "This route takes a service key: Authorization: Bearer " + PREFIX + "...", 403)
        return store.write(who, lambda c: upsert(c, auth, who, body))
