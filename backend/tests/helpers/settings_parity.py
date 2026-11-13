"""Helpers that other tests share, moved out of test_settings_parity.py so they import no test file."""

import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch
from backend import cloud_machines, providers, settings_catalog
from backend.store import H
from backend.tests.helpers.api import get, post, runner
from backend.tests.helpers.botops_parity import act


PEOPLE = {"owner": ("ana-test", "ana"), "admin": ("ben-test", "ben"), "member": ("cara-test", "cara")}


# Cara neither runs nor owns finance, made nothing below and operates no computer: "the bot's owners", "the person who
# made it", "the computer's operator" and the managers of a team rule's scope are Ana's and Ben's (as an admin) here.
MANAGERS = ("bot", "creator", "operator", "scope")


# A value each entry's route takes. An entry left out sends `{}` (json) or the type's plain sample; a route that does
# not take it says so with a 422, which the test reports as not proven rather than passing.
SAMPLES = {
    "team.rules.go_ahead_days": 3,
    "people.allowed": {"allowed": [], "allowed_domains": ["acme.example"], "expected_revision": 1},
    "owner.transfer": {"person": "ben", "confirm": True, "expected_revision": 1},
    "people.add": {"email": "dee@acme.example"},
    "people.member_bot_limit": 3,
    "people.person.role": "admin",
    "computer.harness": {"harness": "claude", "choice": "on"},
    "computer.harness.action": {"harness": "claude", "action": "update"},
    "computer.performance": "fast",
    "computer.turns_at_once": {"turns_at_once": 3},
    "sleep.hours": 2,
    "bot.fallback": "",
    "bot.task_model": "",
    "bot.browser_exit": {"runner_id": None},  # back to its own internet; a computer must first share with the bot
    "bot.change": {"kind": "model", "target": {}},
    "bot.restore": {},
    "bots.create": {"slug": "parity-bot", "display_name": "Parity Bot"},
    "bot.compact_at": 80,
    "bot.definition": {"description": "Keeps the books."},
    "bot.owners": "ben",
    "bot.co_owners": {"add": ["ben"]},
    "bot.archive": lambda o: {"expected_revision": o.get("bot-revision", 1)},
    "bot.access": lambda o: {
        "see": {"everyone": True},
        "read": {"everyone": True},
        "write": {"everyone": False, "people": ["ana"]},
        "revision": o.get("bot-revision", 1),
    },
    "team.budget": {"turns_per_hour": 10},
    # Both providers stay on (saving the subscription in _objects turned Anthropic on, and turning it off would be
    # refused), at the revision _objects read.
    "models.providers": lambda o: {
        "enabled": ["openai", "anthropic"],
        "expected_revision": o.get("providers-revision", 0),
    },
    "bot.profile": {"description": "Keeps the books."},
    "bot.repositories": {
        "mode": "chosen",
        "chosen": [{"full_name": "Acme/product", "access": "write", "run_workflows": True}],
    },
    "repo.setup_command": "make setup",
    "repos.new_bot_default": "own",
    "release.times": "09:30",
    "release.bot": "finance",
    "release.writer": "finance",
    "pages.listening.owner": "finance",
    "mail.legal_bot": {"legal_bot_id": "finance"},
    "rules.task_rule": {"on": False},
    "rules.task_rule.create": {"name": "Parity rule", "when": {}, "then": {}},
    "rules.system": {"enabled": True},
    "webhooks.incoming.create": {"name": "Orders"},
    "webhooks.incoming": {"name": "Orders again"},
    "tasks.type": {"name": "Parity type again"},
    "tasks.type.create": {"name": "Parity type 2"},
    "tasks.tag.create": {"key": "parity2", "label": "parity2"},
    "tasks.tag": {"label": "parity again"},
    "inbound.inbox.create": {"bot": "finance"},
    "inbound.inbox": {"enabled": True},
    "inbound.phone_request.cancel": {},
    "bot.budget": lambda o: {"turns_per_hour": 10, "expected_revision": o.get("bot-revision", 1)},
    "storage.move": {"expect_bytes": 0},
    # A computer given the subscription `_objects` made for it (a real computer and a real subscription, no run to stop).
    "models.computer_subscription": lambda o: {
        "runner_id": o.get("computers", ""),
        "runtime": "claude",
        "subscription": o.get("model-subscriptions", ""),
        "confirm": False,
    },
}


# Routes whose request body this synthetic fixture can't yet satisfy: the rights a direct entry claims are not proven
# for them here. The test fails when one of these runs after all (take it out) or a new one turns up (fix the sample).
NOT_PROVEN = {
    "computer.harness": "422: harness choices need a computer that reported harnesses",
    "computer.harness.action": "404: no harness action on a synthetic computer",
    "computer.performance": "422: the mode sample",
    "sleep.hours": "409: needs a Gateway",
    "bot.fallback": "422: needs a model id",
    "bot.task_model": "422: needs a model id",
    "bot.change": "422: a transition body",
    "bot.restore": "409: finance is not archived",
    "bot.compact_at": "422: the value's bounds",
    "bot.definition": "422: needs expected_revision",
    "bot.turns_at_once": "422: the body needs expected_revision",
    "bot.manages_reports": "422: the body needs expected_revision",
    "bot.owners": "422: the entry sends owners without expected_revision",
    "bot.profile": "422: the body shape",
    "rules.system": "422: the body shape",
    "rules.task_rule": "404: no synthetic task rule yet",
    "rules.task_rule.create": "422: the body shape",
    "tasks.tag": "422: the body shape",
    "inbound.inbox.create": "422: the body shape",
    "inbound.inbox": "422: no synthetic inbox yet",
    "inbound.phone_request.cancel": "409: inbound is not configured",
    "inbound.inbox.audience": "503: inbound is not configured",
    # Confirm entries: the card is made, but the tap can't run on this fixture.
    "bot.agent_credential.revoke": "tap 404: finance has no agent credential",
    "webhooks.gateway.wake": "tap 404: the trigger has no Tico Gateway address (a member's tap is refused first)",
    "me.tokens.revoke": "tap 404: no synthetic API token",
    "storage.s3.remove": "tap 409: no company storage is set",
    "people.person.delete": "tap 409: the fixture person can't be removed for good",
    "repos.product.create": "422: the body shape (and no GitHub App here)",
    "roadmap.public": "tap 422: the route takes no empty settings",
    "inbound.inbox.release": "tap 404: no synthetic inbox",
    "inbound.phone_request": "tap 409: inbound is not configured",
    "inbound.phone_request.accept": "422: needs the price shown (and inbound is not configured)",
    "bot.cloud_machine": "tap 409: the fixture company is not on Tico Cloud",
    "bot.cloud_machine.replace": "tap 409: the fixture company is not on Tico Cloud",
    "cloud.shared_machine": "tap 409: the fixture company is not on Tico Cloud",
    "cloud.shared_machine.replace": "tap 409: the fixture company is not on Tico Cloud",
    "mail.address.order": "422: the body shape (an address order with its Idempotency-Key)",
}


# Where the route and the catalogue disagree: (entry, person) -> what the route does. None now (settings stage 3 made
# the who-may of creator and operator entries, wake rules and a person's role match their routes); the test fails when
# a new one appears, or one here is fixed (take it out).
KNOWN_GAPS = {}


def _sample(e, objects=None):
    if e.key in SAMPLES:
        value = SAMPLES[e.key]
        return value(objects or {}) if callable(value) else value
    return {"bool": True, "int": 1, "number": 1, "text": "x", "list": "x", "json": {}}.get(
        e.value.split(":")[0], e.value.split(":", 1)[-1].split("|")[0]
    )


def _objects(api, machine):
    """One of each object an `{id}` entry names, made by Ana (synthetic rows only)."""
    api.app.state.store.settings.github_owner = "Acme"
    ids = {}
    ids["tags"] = post(api, "tags", {"key": "parity", "label": "parity"}, "ana-test")
    ids["triggers"] = post(api, "triggers", {"name": "Parity trigger"}, "ana-test")
    ids["webhooks"] = post(
        api,
        "webhooks",
        {"name": "Parity hook", "url": "https://hooks.acme.example/parity", "events": ["custom.*"]},
        "ana-test",
    )["webhook"]
    found = {}
    for key, made in ids.items():
        made = made.get(next(iter(made))) if isinstance(made, dict) and len(made) == 1 else made
        found[key] = (made or {}).get("id") or (made or {}).get("key")
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?)",
            (H.new_id(), "Acme/product", now),
        )
        c.execute(
            "INSERT INTO bot_transitions(id,bot,kind,target,requested_by,expected_revision,state,created_at,"
            "updated_at) VALUES('tr-parity','finance','model',?,'human:ana',1,'prepared',?,?)",
            ('{"model": "fake-model", "runtime": "fake", "effort": ""}', now, now),
        )
        system = (
            c.execute("SELECT id FROM system_routines LIMIT 1").fetchone()
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='system_routines'").fetchone()
            else None
        )
    found["repositories"] = "Acme/product"
    found["usage/pools"] = "company"
    found["settings/transitions"] = "tr-parity"
    found["system-routines"] = system[0] if system else "daily_update"
    found["computers"] = machine["runner_id"]
    with api.app.state.store.read() as c:
        found["providers-revision"] = providers.load(c, api.app.state.store.settings)["revision"]
    return found


def _saved_subscription(api, objects):
    """A saved subscription for the remove and give entries (a pasted key, synthetic, sealed in the test vault). Made
    when the first such entry is asked, not in `_objects`: a saved login makes the Assistant's turn place the helper
    bots on the computer, and the entries before it are asked with the fixture as it was."""
    if "model-subscriptions" not in objects:
        made = post(
            api,
            "model-subscriptions",
            {"kind": "anthropic_key", "name": "Parity key", "secret": "sk-ant-parity-synthetic-0000"},
            "ana-test",
        )
        objects["model-subscriptions"] = made["id"]
    return objects["model-subscriptions"]


def _target(e, objects, person):
    target = {"bot": "finance", "computer": objects["computers"], "person": "cara"}
    if "{id}" in e.route:
        target["id"] = (
            "all"
            if e.route.startswith("postal/addresses/")
            else next((v for k, v in objects.items() if e.route.startswith(k + "/")), "parity-missing")
        )
    if e.who == "self" and e.target == "person":
        target["person"] = person
    return {k: v for k, v in target.items() if v and "{" + k + "}" in e.route}


def _allowed(c, e, role, person):
    if e.who == "owner":
        return role == "owner"
    if e.who == "admin" or e.who in MANAGERS:
        return role in ("owner", "admin")
    if e.who == "mover":
        return role == "owner" or H.can_move(c, "human:" + person)
    return True  # anyone, self


def expected(c, e, role, person):
    if not _allowed(c, e, role, person):
        return "refused"
    kind = settings_catalog.kind_for(e, settings_catalog.parse(e, _sample(e)) if e.value == "json" else {})
    return {"direct": "ran", "confirm": "card", "person": "refused"}[kind]


def outcome(answer):
    """ran, card or refused, from one HTTP answer; anything else is the status and error code, not proven."""
    data = answer.json() if answer.headers.get("content-type", "").startswith("application/json") else {}
    if answer.status_code == 403:
        return "refused"
    if answer.status_code < 300:
        return "card" if isinstance(data, dict) and data.get("needs_confirm") else "ran"
    return f"{answer.status_code} {((data or {}).get('error') or {}).get('code', '')}".strip()


TAP_DETAIL = {}


def tapped(api, card, token):
    """The person's own tap on a card: what the setting's route did with it (ran, refused, or the status it gave),
    with the test database put back as it was before the tap, so the next entry starts from the same fixture. The
    card is then canceled, so the next one isn't over the cap on open cards."""
    path = api.app.state.store.settings.db_path
    with closing(sqlite3.connect(":memory:")) as kept:
        with closing(sqlite3.connect(path)) as live:
            live.backup(kept)
        tap = post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token)["action"]
        with closing(sqlite3.connect(path)) as live:
            kept.backup(live)
    TAP_DETAIL["detail"] = json.dumps(tap.get("result") or tap.get("error") or {})[:300]
    post(api, f"assistant/actions/{card['action']['id']}/cancel", {}, token)
    if tap["status"] == "done":
        return "ran"
    code = (tap.get("result") or {}).get("status_code")
    return "refused" if code == 403 else f"tap {code}"


# What the last refused or unexpected answer of each entry said: status, error code and detail, for the assertion message.
LAST = {}


def ask(api, attempt, e, objects, person, token):
    """`tico settings set` as BotOps does it for the person: the plan, then the request the plan says to make. A card,
    from the plan or from the route, is tapped as the person (`tapped`): a catalogue card never reaches the route
    until then, so this is where the route says whether that person may."""
    if e.route.startswith("model-subscriptions/") or e.route == "computer-subscriptions":
        _saved_subscription(api, objects)
    # The bot's revision as it is now: an earlier entry may have changed it, and a stale one is a 409, not the setting.
    objects["bot-revision"] = get(api, "bots/finance", token)["revision"]
    if e.route.startswith("settings/transitions/"):
        # The fixture change is prepared at the bot's revision now, and an apply consumes it, so each entry gets it fresh.
        with api.app.state.store.transaction() as c:
            c.execute(
                "UPDATE bot_transitions SET state='prepared', expected_revision=? WHERE id=?",
                (objects["bot-revision"], objects["settings/transitions"]),
            )
    if "{computer}" in e.route or e.route == "computer-subscriptions":
        # A computer an earlier entry revoked (computer.revoke) is not found by the next one: each gets its own.
        objects["computers"] = runner(api)["runner_id"]
    target = _target(e, objects, person)
    planned = act(api, attempt, "POST", f"settings/catalog/{e.key}/plan", {"value": _sample(e, objects), **target})
    if planned.status_code == 200 and "run" in planned.json():
        run = planned.json()["run"]
        planned = act(api, attempt, run["method"], run["path"], run["body"])
    got = outcome(planned)
    LAST[e.key] = f"{planned.status_code} {planned.text[:300]}"
    if got != "card":
        return got
    if e.route == "cloud-machines/support":
        # This switch exists only on cloud companies. Give its tap an available company and a synthetic HQ, so
        # the shared sweep proves the owner's confirmation as well as refusing everyone else.
        hq = SimpleNamespace(support=lambda on=None, **_: {"on": on is not False, "machines": []})
        with (
            patch.object(cloud_machines, "cloud_company", return_value=True),
            patch.object(api.app.state.cloud_machines, "client", hq),
        ):
            tap = tapped(api, planned.json(), token)
    else:
        tap = tapped(api, planned.json(), token)
    if tap != "ran":
        LAST[e.key] = f"tap: {TAP_DETAIL.get('detail', '')}"
    return "card" if tap == "ran" else "card, then " + tap
