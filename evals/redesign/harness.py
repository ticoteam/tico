"""Plumbing for the redesign scenarios: one org per scenario on the factory's disposable hub, the API calls a teammate
makes, and the Unmet marker that turns a missing route or refused behaviour into "not built yet".

A scenario is setup + steps + checks. Steps run in order and keep what they got in a facts dict. A step the product
cannot do yet keeps an Unmet there and the run goes on, so each check that reads it names the wave it waits on. A check
passes only on a real reply; nothing is stubbed."""
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import quote

from ..factory.world import World

W1 = "wave 1 · renames & moves"
W2 = "wave 2 · budgets"
W3 = "wave 3 · automations core"
W4 = "wave 4 · branches"
W5 = "wave 5 · migration"
W6 = "wave 6 · strategy content"
GAP = "CONTRACT gap"   # a check the CONTRACT cannot back yet: it names no route or read shape

# Waves that have landed on main. The PR that merges a wave adds its label here: from then on a failing check prints
# its detail instead of "not built yet".
BUILT = {W1, W2, W3, W4, W5, W6}

# Teammates: the test token each signs in with, the CONTRACT §3 ref and the bare id the API takes as an owner.
# The Acme cast (DECISIONS.md): Dana owns the hub; Omar and Priya are people. Omar is a peer of the Engineering Lead and
# Priya reports to Dana, so "everyone above you sees yours, peers don't" is checked with real people (PLAN decision 36).
DANA, OMAR, PRIYA = "dana-test", "omar-test", "priya-test"
LEAD, CLAUDE, OPENAI, RELEASE = "lead-test", "claude-test", "openai-test", "release-test"
DANA_REF, OMAR_REF, PRIYA_REF, LEAD_REF = "person:dana", "person:omar", "person:priya", "bot:engineering-lead"
CLAUDE_REF, OPENAI_REF, RELEASE_REF = "bot:builder-claude", "bot:builder-openai", "bot:release-manager"
DANA_ID, OMAR_ID, PRIYA_ID = "dana", "omar", "priya"
LEAD_ID, CLAUDE_ID, OPENAI_ID, RELEASE_ID = "engineering-lead", "builder-claude", "builder-openai", "release-manager"


class Unmet(Exception):
    """Part of the contract the product does not have yet: a route it does not register, or a refusal."""

    def __init__(self, wave, what):
        super().__init__(what)
        self.wave, self.what = wave, what


@dataclass
class Reply:
    status: int
    body: Any
    wave: str

    def excerpt(self):
        return json.dumps(self.body, default=str)[:160]


@dataclass
class Check:
    label: str
    wave: str
    fn: Callable[[dict], tuple]


@dataclass
class Scenario:
    num: int
    name: str
    about: str
    steps: Callable
    checks: list = field(default_factory=list)
    bots: dict = field(default_factory=dict)   # extra org bots ({slug: (role, harness)}), e.g. the migration fixture's
    first_tick: bool = True   # the owner's built-ins made on the first automations tick; False when the scenario cuts over


@dataclass
class Result:
    label: str
    ok: bool
    text: str
    wave: str


# ---------------------------------------------------------------------------------------------- reading facts
def got(facts, key):
    """The step's result; raises its Unmet when the step could not run."""
    value = facts[key]
    if isinstance(value, Unmet):
        raise value
    return value


def setup(facts, *keys):
    """Raise unless each named step ran and got a 2xx. A check that depends on a step fails with the reason."""
    for key in keys:
        reply = got(facts, key)
        if not 200 <= reply.status < 300:
            raise Unmet(reply.wave, f"{key}: {reply.status} {reply.excerpt()}")


def body(facts, key):
    setup(facts, key)
    return facts[key].body


def replied(facts, key, *want, needs=()):
    """The step's reply has one of the statuses in `want`, once the `needs` steps were accepted."""
    setup(facts, *needs)
    reply = got(facts, key)
    return reply.status in want, f"{reply.status} {reply.excerpt()}"


def accepted(facts, *keys, needs=()):
    """Every named send was accepted (200 or 201), after the `needs` steps."""
    setup(facts, *needs)
    codes = {key: got(facts, key).status for key in keys}
    return all(code in (200, 201) for code in codes.values()), str(codes)


def rows(data):
    """The list in a listing: the body itself, or its one list field."""
    if isinstance(data, list):
        return data
    return next((v for v in (data or {}).values() if isinstance(v, list)), [])


def find(data, pred):
    """The first dict in a JSON value that `pred` accepts, or None."""
    if isinstance(data, dict):
        if pred(data):
            return data
        children = list(data.values())
    elif isinstance(data, list):
        children = data
    else:
        return None
    return next((hit for child in children if (hit := find(child, pred))), None)


def task_id_of(reply):
    data = reply.body if isinstance(reply.body, dict) else {}
    return data.get("id") or (data.get("task") or {}).get("id")


# ---------------------------------------------------------------------------------------------- the org
class Org(World):
    """The factory's World (clock, server, runner) with the scenario org on it, in Acme names: Dana (owner) at the top;
    Omar and Priya, people, under her; Engineering Lead under her too (so Omar is a peer of the lead); two builders
    under the lead; and the Release Manager under Dana. Emails are @acme.example."""

    def __init__(self, extra_bots=None, first_tick=True):
        """`extra_bots` adds bots to the org (the migration fixture's Inbox Manager, Health Monitor, Librarian...), as
        {slug: (role, harness)} in the World's format. `first_tick` runs what a live server's first automations tick
        does once Dana owns the install (redesign_migration.catch_up: her built-ins)."""
        bots = {LEAD_ID: ("lead", "claude"), OPENAI_ID: ("builder", "gpt"), CLAUDE_ID: ("builder", "claude"),
                RELEASE_ID: ("release", "claude"), **(extra_bots or {})}
        board = SimpleNamespace(name="Redesign scenarios", steps=[SimpleNamespace(name="Work", status="open")])
        super().__init__(board, bots)
        self.log = []
        self.first_tick = first_tick

    def __enter__(self):
        super().__enter__()
        from backend.auth import Identity
        from backend.store import H, encode
        identities = self.app.state.auth.settings.test_identities
        identities.update({
            DANA: Identity("human:dana", "owner", email="dana@acme.example"),
            OMAR: Identity("human:omar", "human", email="omar@acme.example"),
            PRIYA: Identity("human:priya", "human", email="priya@acme.example"),
            LEAD: Identity(LEAD_REF, "bot", agent="test"),
            CLAUDE: Identity(CLAUDE_REF, "bot", agent="test"),
            OPENAI: Identity(OPENAI_REF, "bot", agent="test"),
        })
        people = [{"id": DANA_ID, "name": "Dana", "email": "dana@acme.example", "reports_to": "", "primary_for": ["*"]},
                  {"id": OMAR_ID, "name": "Omar", "email": "omar@acme.example", "reports_to": DANA_ID, "primary_for": []},
                  {"id": PRIYA_ID, "name": "Priya", "email": "priya@acme.example", "reports_to": DANA_ID,
                   "primary_for": []}]
        reports = {LEAD_ID: DANA_ID, CLAUDE_ID: LEAD_ID, OPENAI_ID: LEAD_ID, RELEASE_ID: DANA_ID}
        with self.store.transaction() as c:
            H.sync_registry(c, {}, {"people": people})   # the people rows the hub signs in and reports against
            c.execute("DELETE FROM registry_metadata WHERE key='people'")
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
            for bot in (LEAD_ID, CLAUDE_ID, OPENAI_ID):   # the agent credentials the test tokens stand for
                c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                          (bot, "test", "x-" + bot, H.now(), "human:dana"))
            for bot, boss in reports.items():
                c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (boss, bot))
            # The factory seeds one owner and one operator for every bot and its runner. Dana is the owner here: the
            # stored owner (backend/access.py OWNER, read once at seed time) and those operators move to her.
            from backend.access import OWNER
            owner = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key=?", (OWNER,)).fetchone()[0])
            owner.update(email="dana@acme.example", updated_by="environment")
            c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(owner, sort_keys=True), OWNER))
            # Dana admits her team to the sign-in list (backend/access.py ACCESS). The Slack gateway hears only an admitted
            # sender (backend/slack_gateway.py verify_sender), so a teammate's channel message is heard once they are on it.
            from backend.access import ACCESS, _load_json, _store
            access = _load_json(c, ACCESS) or {}
            _store(c, ACCESS, {**access, "allowed": sorted(set(access.get("allowed") or [])
                                                          | {"omar@acme.example", "priya@acme.example"})})
            c.execute("UPDATE bot_config SET operator=? WHERE operator<>?", (DANA_ID, DANA_ID))
            c.execute("UPDATE runners SET operator=? WHERE operator<>?", (DANA_ID, DANA_ID))   # the runner the mail publish checks
        if self.first_tick:   # startup's cutover ran before Dana was the owner: the first tick makes her built-ins
            from backend import redesign_migration
            redesign_migration.catch_up(self.store)
        return self

    def configure(self):
        """The factory's Dev board is not used: each scenario builds the org it needs."""

    def _registered(self, method, path):
        for route in self.app.routes:
            template = getattr(route, "path", "")
            pattern = "[^/]+".join(re.escape(part) for part in re.split(r"\{[^}]+\}", template))
            if method.upper() in (getattr(route, "methods", None) or ()) and re.fullmatch(pattern, path):
                return True
        return False

    def api(self, method, path, payload=None, *, who=DANA, wave=W2):
        """One real API call as `who` (a test token, or the runner's). A route the app does not register is Unmet,
        not a 404, so the scenario names the wave that builds it."""
        if not self._registered(method, path.split("?")[0]):   # the route is matched without its query string
            raise Unmet(wave, f"no route {method.upper()} {path}")
        headers = {"Authorization": "Bearer " + who}
        if method != "get":   # every write carries a key, as the hub requires
            headers["Idempotency-Key"] = str(uuid.uuid4())
        response = getattr(self.client, method)(
            path, headers=headers, **({"json": payload} if payload is not None else {}))
        try:
            data = response.json()
        except ValueError:
            data = response.text
        reply = Reply(response.status_code, data, wave)
        self.log.append(f"{method.upper()} {path} as {who} -> {reply.status} {reply.excerpt()}")
        return reply

    def step(self, facts, key, fn, *args, **kwargs):
        """Run one step; a step the product cannot do yet keeps its Unmet and the scenario goes on."""
        try:
            facts[key] = fn(*args, **kwargs)
        except Unmet as unmet:
            facts[key] = unmet

    def pass_time(self, delta):
        self.advance(delta)
        self.scheduler_pass()

    def turn(self, slug, tokens=2_000_000, act=None, text="done"):
        """The bot's next turn, run the way a runner runs it: claim, start, `act(attempt_token, attempt)` (the bot's
        own calls during the turn, as that bot), complete with `text` and its token use. None when the hub held the
        bot (no job was claimed)."""
        self.scheduler_pass()
        claim = self.api("post", "/api/v2/jobs/claim", {"bot": slug, "start_places": True},
                         who=self.runner["token"], wave=W2)
        attempt = claim.body.get("attempt") if isinstance(claim.body, dict) else None
        if not attempt:
            if claim.status in (200, 201, 204, 409, 423, 429):   # nothing handed out: held, or no job queued
                return None
            if claim.status in (403, 404):   # the runner may not claim this bot yet: a product gap, not a harness bug
                raise Unmet(W2, f"claim {slug}: {claim.status} {claim.excerpt()}")
            raise RuntimeError(f"claim {claim.status}: {claim.excerpt()}")   # a harness or auth bug, not a hold
        base = f"/api/v2/attempts/{attempt['id']}"
        for step, payload in (("started", {"thread_id": "redesign"}),
                              ("complete", {"outcome": "completed", "text": text, "last_seq": 0, "limited": False,
                                            "usage": {"input_tokens": tokens, "cached_tokens": 0,
                                                      "output_tokens": 10_000, "model": "claude-opus-5-5",
                                                      "runtime": "claude", "billing": "api"}})):
            if step == "complete" and act:
                act(attempt.get("token"), attempt)
            reply = self.api("post", f"{base}/{step}", payload, who=self.runner["token"], wave=W2)
            if not 200 <= reply.status < 300:
                raise RuntimeError(f"attempt {step} {reply.status}: {reply.excerpt()}")
        return attempt["id"]


# ---------------------------------------------------------------------------------------------- shared steps
def send(org, who, owner, title):
    """A task from `who` to `owner` (a bare slug or person id), as the sender creates it."""
    return org.api("post", "/api/v2/tasks", {"title": title, "body": "Scenario task.", "owner": owner}, who=who,
                   wave=W2)


def work(org, slug, title):
    """A person hands the bot a task, then the bot's next turn runs (None when the hub held it)."""
    made = send(org, DANA, slug, title)
    if made.status >= 300:
        raise Unmet(W2, f"task for {slug}: {made.status} {made.excerpt()}")
    return org.turn(slug)


def put_cap(org, sender, receiver, per_day, who=DANA):
    return org.api("put", "/api/v2/task-caps", {"sender": sender, "receiver": receiver, "per_day": per_day},
                   who=who, wave=W2)


def get_caps(org):
    return org.api("get", "/api/v2/task-caps", wave=W2)


def put_limit(org, slug, payload, who=DANA):
    return org.api("put", f"/api/v2/usage/limits/{slug}", payload, who=who, wave=W2)


def put_budget(org, slug, payload, who=DANA):
    return org.api("post", f"/api/v2/bots/{slug}/budget", payload, who=who, wave=W2)


def put_pool(org, gid, payload, who=DANA):
    return org.api("put", f"/api/v2/usage/pools/groups/{gid}", payload, who=who, wave=W2)


def make_group(org, name, bots=(), parent=None, lead=None, who=DANA):
    """A group with its teammates, as Dana makes it. Returns its id."""
    payload = {"name": name, "add": {"bots": list(bots)}}
    if parent:
        payload["parent"] = parent
    if lead:
        payload["lead"] = lead   # CONTRACT §3; GroupCreate has no lead field today
    reply = org.api("post", "/api/v2/groups", payload, who=who, wave=W2)
    if reply.status >= 300:
        raise Unmet(W2, f"group {name}: {reply.status} {reply.excerpt()}")
    return (reply.body.get("group") or reply.body).get("id")


def automation(org, name, owner, trigger=None, steps=None, group=None, spend_cap=None, who=DANA):
    payload = {"name": name, "owner": owner, "enabled": True, "builtin": False,
               "trigger": trigger or {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"},
               "steps": steps or [{"id": "gate", "type": "filter", "enabled": True, "rules": []}]}
    if group:
        payload["group"] = group
    if spend_cap:
        payload["spend_cap"] = spend_cap
    return org.api("post", "/api/v2/automations", payload, who=who, wave=W3)


def auto_id(facts, key):
    return body(facts, key)["id"]


def run_now(org, facts, key, sample=None, who=DANA):
    """Run an automation now. `sample` is the trigger's output (CONTRACT §2 Feeding input), the product's "Test with
    sample": {message, task, item, event or body}."""
    payload = {"input": sample} if sample is not None else None
    return org.api("post", f"/api/v2/automations/{auto_id(facts, key)}/run", payload, who=who, wave=W3)


def mail_message(msg_id, sender, subject, body="", thread=""):
    """One received mail in the shape the mail sync publishes (MailMessage in backend/connectors.py)."""
    from backend.store import H
    return {"msg_id": msg_id, "thread_id": thread or msg_id, "from_addr": sender, "from_header": sender,
            "subject": subject, "snippet": body[:200], "body": body, "date": H.now(), "labels": ["INBOX"]}


def publish_mail(org, mailbox, messages, deleted=()):
    """A mail sync batch through POST /api/v2/connectors/mail/messages, the route the runner's sync posts to. The
    runner's token only: it is the one caller allowed to publish (CONTRACT §2 Feeding input)."""
    from backend.store import H
    payload = {"mailbox": mailbox, "messages": list(messages), "deleted": list(deleted), "synced_at": H.now()}
    return org.api("post", "/api/v2/connectors/mail/messages", payload, who=org.runner["token"], wave=W3)


def mail_computer(org, mailbox):
    """The mail computer's pass (CONTRACT §1 Mail actions): it claims every queued action for `mailbox` and reports
    each done, as runner/connectors.py `mail_action_tick` does after Gmail took it. Returns the actions it claimed."""
    claimed = org.api("post", "/api/v2/automations/mail-actions/claim", {"mailbox": mailbox},
                      who=org.runner["token"], wave=W3)
    actions = ((claimed.body or {}).get("actions") or []) if claimed.status == 200 else []
    for action in actions:
        kind = action.get("action")
        done = {"ok": True, "detail": {"draft": "drafted", "send": "sent", "forward": "forwarded"}.get(kind, kind),
                **({"draft": "d-" + action["id"][:8]} if kind == "draft" else {}),
                **({"message": "s-" + action["id"][:8]} if kind in ("send", "forward") else {})}
        org.api("post", f"/api/v2/automations/mail-actions/{action['id']}/result", done, who=org.runner["token"],
                wave=W3)
    return actions


def inbox_send(org, inbox, send, who=DANA):
    """The inbox's send switch (CONTRACT §2 Send switch): true sends replies, false saves drafts."""
    return org.api("patch", f"/api/v2/inboxes/{inbox}", {"send": send}, who=who, wave=W3)


def inbox_read(org, inbox, who=DANA):
    return org.api("get", f"/api/v2/inboxes/{inbox}", who=who, wave=W3)


def assistant_of(org, person, who=DANA):
    """The person's Assistant bot (CONTRACT §2 Assistant): {"bot": slug} or {"bot": null}."""
    return org.api("get", f"/api/v2/people/{person}/assistant", who=who, wave=W3)


def audit_of(org, obj=None, limit=None, before=None, who=DANA):
    """GET /api/v2/audit (CONTRACT §5): the owner reads every row, anyone else their own."""
    query = [f"{key}={quote(str(value), safe='')}" for key, value in
             (("object", obj), ("limit", limit), ("before", before)) if value is not None]
    return org.api("get", "/api/v2/audit" + ("?" + "&".join(query) if query else ""), who=who, wave=W2)


def decide(org, approval_id, decision, who=DANA):
    """A person decides a bot's proposal. `decision` is "approved" or "declined" (the approval model's words)."""
    return org.api("post", f"/api/v2/approvals/{approval_id}", {"decision": decision}, who=who, wave=W2)


def hub_notices(org, slug, who=DANA):
    """The hub's notices to a bot (CONTRACT §5 BotOps note, §1 run guard): messages from `keeper` of kind `notice`, read
    as the owner reads a bot's rooms: GET /api/v2/conversations?bot=<slug>, then each room's messages."""
    rooms = org.api("get", f"/api/v2/conversations?bot={slug}", who=who, wave=W2)
    if rooms.status != 200:
        raise Unmet(W2, f"{slug}'s conversations: {rooms.status} {rooms.excerpt()}")
    found = []
    for room in rooms.body.get("conversations") or []:
        page = org.api("get", f"/api/v2/conversations/{room['id']}/messages", who=who, wave=W2)
        found += [m for m in (page.body.get("messages") or []) if isinstance(page.body, dict)
                  and m.get("from_actor") == "keeper" and m.get("kind") == "notice"]
    return found


def approval_id_of(reply):
    """The approval a bot's proposal filed (CONTRACT §1: {"approval": "<id>", ...}), or None."""
    data = reply.body if isinstance(reply.body, dict) else {}
    value = data.get("approval")
    return value.get("id") if isinstance(value, dict) else value


def hook_post(org, trigger_id, secret, payload=None):
    """A payload to an incoming trigger's URL, as the outside service sends it (CONTRACT §2 Webhook trigger)."""
    return org.api("post", f"/api/v2/hooks/{trigger_id}/{secret}", payload or {}, wave=W3)


def turn_of(run):
    """The turn an llm or bot_turn step ran on (CONTRACT §2 Turn origin: {bot, task, session}), or None."""
    hit = find(run, lambda d: isinstance(d.get("turn"), dict))
    return hit["turn"] if hit else None


def today_counts(data):
    """`counts.today` on an automation read (CONTRACT §2 Counts): {runs, dropped}, or None."""
    return ((data or {}).get("counts") or {}).get("today")


def builtin_rows(data):
    """The built-in automations in a listing (CONTRACT §2 Built-ins: builtin true)."""
    return [row for row in rows(data) if row.get("builtin") is True]


def edit_auto(org, facts, key, who, **payload):
    return org.api("patch", f"/api/v2/automations/{auto_id(facts, key)}", payload, who=who, wave=W3)


def close_task(org, facts, key, who):
    """The sender closes its own task (status closed) as `who`."""
    tid = task_id_of(got(facts, key))
    task = org.api("get", f"/api/v2/tasks/{tid}", who=who, wave=W2)
    version = (task.body.get("task") or task.body).get("version")
    return org.api("post", f"/api/v2/tasks/{tid}", {"version": version, "status": "closed"}, who=who, wave=W2)


def comment(org, facts, key, who, text):
    tid = task_id_of(got(facts, key))
    return org.api("post", f"/api/v2/tasks/{tid}/comments", {"text": text}, who=who, wave=W2)


def untestable(what):
    """A check the CONTRACT cannot back yet. It fails as untestable until the contract names the route or read."""
    def check(facts):
        raise Unmet(GAP, what)
    return check


# ---------------------------------------------------------------------------------------------- running
def evaluate(check, facts):
    try:
        ok, text = check.fn(facts)
        return ok, text, check.wave
    except Unmet as unmet:
        return False, unmet.what, unmet.wave
    except Exception as exc:  # a bug in a check, shown as such rather than as a missing feature
        return False, f"check error {type(exc).__name__}: {exc}", check.wave


def run_scenario(scenario):
    """Run one scenario on a fresh org: its results, the API calls it made, and the seconds it took."""
    started = time.perf_counter()
    results, error = [], None
    with Org(extra_bots=scenario.bots, first_tick=scenario.first_tick) as org:
        facts = {}
        try:
            scenario.steps(org, facts)
        except Exception as exc:  # a bug in a step: every check reports it
            error = exc
        for check in scenario.checks:
            if error:
                ok, text, wave = False, f"scenario error {type(error).__name__}: {error}", check.wave
            else:
                ok, text, wave = evaluate(check, facts)
            results.append(Result(check.label, ok, text, wave))
        log = list(org.log)
    return results, log, time.perf_counter() - started
