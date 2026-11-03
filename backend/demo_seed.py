"""Builds the demo company's database through the same code a real one runs on.

Rows come from the domain layer and the HTTP API (so every rule a real install enforces holds for
the sample data), with two things made fixed: the clock is set to chosen moments relative to `now`
(a week of history ending just now), and ids and secrets come from a counter. Building twice with the
same `now` gives the same database.
"""

import contextlib
import dataclasses
import hashlib
import io
import itertools
import json
import secrets
import uuid
import warnings
from datetime import datetime, timedelta, timezone

import yaml

# Starlette prefers a different HTTP client for its test client; the seed needs only its own app.
warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
from fastapi.testclient import TestClient  # noqa: E402

from . import demo_content as D
from . import docs, goals as G, hubdb, market, onboarding, providers, routines, slack_app, updates
from .auth import Identity
from .scheduler import next_due, stamp
from .store import H, encode

NAMESPACE = uuid.UUID("7d1c2f3e-5a4b-4c6d-8e9f-0a1b2c3d4e5f")
OWNER = "demo-owner"
TOKENS = {"human:ana": OWNER, "human:cara": "demo-cara", "human:ben": "demo-ben", "human:eli": "demo-eli"}
RUNNER_VERSION = "0.6.2"
SEED_SHA = "9f2c4e1ab7d3085c6e1f4a2b9d8c7e6f5a4b3c2d"


class Clock:
    """`hubdb.now` for the length of a build: reads the moment it was told, one microsecond apart."""

    def __init__(self, at):
        self.at = at

    def __call__(self):
        self.at += timedelta(microseconds=1)
        return self.at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


@contextlib.contextmanager
def fixed(clock):
    """Time, ids and secrets that repeat, restored afterwards."""
    counter = itertools.count()

    def digest():
        return hashlib.sha256(f"{NAMESPACE}:{next(counter)}".encode()).hexdigest()

    from . import inbound, threads
    saved = (hubdb.now, hubdb.new_id, hubdb.uuid7, threads.uuid7, secrets.token_urlsafe, secrets.token_hex, uuid.uuid4,
             inbound.now)
    hubdb.now = clock
    # Inbound keeps a message's body for 90 days from when it arrived, counted on the demo's clock: a demo built months
    # after its dates keeps every body.
    inbound.now = lambda: clock.at.timestamp()
    hubdb.new_id = lambda: str(uuid.uuid5(NAMESPACE, str(next(counter))))
    # Time-ordered ids (messages, deliveries) and the product's own random ids for threads and calls.
    hubdb.uuid7 = threads.uuid7 = lambda: str(uuid.UUID(hex=digest()[:32]))
    uuid.uuid4 = lambda: uuid.UUID(hex=digest()[:32])
    secrets.token_urlsafe = lambda nbytes=32: (digest() * 3)[: (nbytes * 4 + 2) // 3]
    secrets.token_hex = lambda nbytes=32: (digest() * 3)[: nbytes * 2]
    try:
        yield
    finally:
        (hubdb.now, hubdb.new_id, hubdb.uuid7, threads.uuid7, secrets.token_urlsafe, secrets.token_hex, uuid.uuid4,
         inbound.now) = saved


def write_registry(settings):
    """Who is on the roster and which bots exist: the files a new install's first boot imports."""
    registry = settings.registry_dir
    registry.mkdir(parents=True, exist_ok=True)
    cards = {card["template"]: card for card in onboarding.read_cards(settings)}
    names = {"company_name": D.COMPANY, "app_name": settings.app_name, "assistant_name": settings.assistant_name}
    employees = []
    for slug, template, reports_to in D.BOTS:
        card = onboarding.render(cards[template], names, cards[template]["name"]) if template in cards else {}
        employees.append({"name": slug, "display_name": settings.assistant_name if slug == "coo" else D.BOT_NAMES.get(slug) or card.get("name", slug),
                          "reports_to": reports_to, "status": "active", "repo": "bot-" + slug, "template": template,
                          "description": card.get("summary", "")})
    (registry / "employees.yaml").write_text(yaml.safe_dump(
        {"defaults": {"reasoning_effort": "high", "max_run_minutes": 60}, "employees": employees}, sort_keys=False))
    people = [dict(person, **({"inbox_bot": "inbox"} if person["id"] == "ben" else {})) for person in D.PEOPLE]
    (registry / "people.yaml").write_text(yaml.safe_dump(
        {"default_user": "ana", "teams": {"leadership": {"root": "coo"}}, "people": people}, sort_keys=False))
    (registry / "hub-access.yaml").write_text(yaml.safe_dump(
        {"owner": D.PEOPLE[0]["email"], "allowed_domains": ["acme.example"],
         "allowed": [p["email"] for p in D.PEOPLE], "bot_admins": []}))


def populate(settings, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    write_registry(settings)
    clock = Clock(now - timedelta(days=7))
    with fixed(clock):
        Builder(settings, now, clock).build()


class Builder:
    def __init__(self, settings, now, clock):
        self.settings, self.now, self.clock = settings, now, clock
        self.serial = itertools.count()
        self.tasks = {}

    def ago(self, days=0.0, hours=0.0):
        return self.now - timedelta(days=days, hours=hours)

    def at(self, days=0.0, hours=0.0):
        self.clock.at = self.ago(days, hours)

    # ------------------------------------------------------------------ the api, as the owner
    def call(self, method, path, body=None, token=OWNER):
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": f"demo-{next(self.serial)}"}
        reply = self.api.request(method, "/api/v2/" + path if not path.startswith("/") else path, json=body, headers=headers)
        assert reply.status_code == 200, f"{method} {path}: {reply.status_code} {reply.text[:300]}"
        return reply.json()

    def build(self):
        from .app import create_app
        # The seed talks to its own app as the owner; the demo that people open has no such token.
        seeding = dataclasses.replace(
            self.settings, demo=False, demo_public=False, local_owner_token_file=None,
            test_identities={
                OWNER: Identity("human:ana", "owner", D.PEOPLE[0]["email"]),
                "demo-cara": Identity("human:cara", "human", D.PEOPLE[2]["email"]),
                "demo-ben": Identity("human:ben", "human", D.PEOPLE[1]["email"]),
                "demo-eli": Identity("human:eli", "human", D.PEOPLE[3]["email"])})
        app = create_app(seeding)
        self.app, self.store = app, app.state.store
        # The app's media worker would settle the scans at moments of its own choosing. The build settles them itself,
        # in order, in `finish` (drain_media), so the same build always writes the same rows.
        self.media = app.state.file_metadata
        self.media.loop = lambda: None
        self.at(days=7)
        with TestClient(app) as api:
            self.api = api
            self.company()
            self.org()
            self.computers()
            self.goals()
            self.docs()
            self.market()
            self.routines()
            self.tasks_and_chats()
            self.meetings()
            self.routine_tasks()
            self.updates()
            self.history()
            self.past_weeks()
            self.messaging()
            self.files()
            self.page()
            self.icon()
            from . import demo_sections   # here, not at the top: the sections import this module
            demo_sections.seed_all(self)
            self.routine_tasks(D.BRIEFING_DONE)
            self.finish()

    def write(self, work):
        with self.store.transaction() as c:
            return work(c)

    # ------------------------------------------------------------------ the company
    def company(self):
        store = self.store
        store.initialize()
        registry = yaml.safe_load((self.settings.registry_dir / "employees.yaml").read_text())
        entries = {e["name"]: {**registry["defaults"], **e, "host": "keeper", "tasks": "hub"} for e in registry["employees"]}
        store.seed(entries=entries)

        # The record names each bot as its card does, like the registry above, so the catalog reads the same.
        card_names = {card["template"]: card["name"] for card in onboarding.read_cards(self.settings)}

        def record(c):
            names = {"company_name": D.COMPANY, "app_name": self.settings.app_name,
                     "assistant_name": self.settings.assistant_name}
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({
                "names": names, "answers": D.ANSWERS, "completed": self.ago(days=6.9).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "selected": {slug: {"template": template, "display_name": D.BOT_NAMES.get(slug) or card_names.get(template, slug.title()),
                                    "instructions": ""}
                             for slug, template, _ in D.BOTS if slug not in ("coo", "botops")}}),))
            # The tour is over; the checklist stays because GitHub, its one optional step, is not connected.
            c.execute("INSERT INTO preferences VALUES('human:ana','onboarding.progress',?,?)",
                      (encode({"tour": True, "checklist": False, "skipped": []}), H.now()))
        self.write(record)

    def org(self):
        """The groups as an org chart reads them (D.GROUP_FIXES), through the groups API like an owner would."""
        self.at(days=6.95)
        for gid, bots, lead in D.GROUP_FIXES:
            self.call("PATCH", f"groups/{gid}", {"add": {"bots": list(bots)}, "lead": lead})

    def computers(self):
        self.at(days=6.9)
        for label, platform, runtimes in D.RUNNERS:
            code = self.call("POST", "enrollments", {"operator": "ana"})["code"]
            runner = self.call("POST", "runners/enroll", {"code": code, "label": label, "platform": platform})
            if platform == "darwin":
                self.mac = runner
            else:
                self.linux = runner
            bots = {slug: {"ready": True, "runtime": "claude", "model": self.settings.default_model, "repository_present": True,
                           "repository_revision": SEED_SHA[:7], "configuration_valid": True, "problems": []}
                    for slug in [*(slug for slug, _, _ in D.BOTS), "librarian", "goal-manager"]}
            readiness = {"schema_version": 1, "bots": bots,
                         "runtimes": {name: {"installed": True, "authenticated": "ready",
                                             "version": "2.4.1" if name == "claude" else "0.9.3",
                                             "models": [self.settings.default_model] if name == "claude" else ["gpt-6-luna"],
                                             "controls": ["interrupt", "new-session"]} for name in runtimes},
                         "harnesses": {("claude-code" if name == "claude" else name): {
                             "name": "Claude Code" if name == "claude" else "Codex", "runtime": name, "installed": True,
                             "version": "2.4.1" if name == "claude" else "0.9.3", "managed": True, "source": "tools",
                             "authenticated": "ready", "wanted": True} for name in runtimes}}
            self.at(hours=1)
            api = self.api
            api.request("POST", "/api/v2/runners/heartbeat", headers={
                "Authorization": "Bearer " + runner["token"], "Idempotency-Key": f"demo-{next(self.serial)}"},
                json={"version": RUNNER_VERSION, "platform": platform, "capacity": 4, "readiness": readiness,
                      "checkout": {"head": SEED_SHA, "running": SEED_SHA, "ahead": 0, "behind": 0,
                                   "checked_at": self.ago(hours=1).strftime("%Y-%m-%dT%H:%M:%SZ")}}).raise_for_status()
        # The Linux box hosts Inbox alone; the Mac hosts the other bots.
        for slug in [*(slug for slug, _, _ in D.BOTS), "librarian", "goal-manager"]:
            target = self.linux if slug == "inbox" else self.mac
            with self.store.read() as c:
                have = c.execute("SELECT runner_id,generation FROM assignments WHERE bot=?", (slug,)).fetchone()
            if have and have["runner_id"] == target["runner_id"]:
                continue
            self.call("POST", f"bots/{slug}/assignment",
                      {"runner_id": target["runner_id"], "expected_generation": have["generation"] if have else 0})

    def goals(self):
        self.at(days=6.5)
        keys = {}

        def work(c):
            for key, title, owner, parent, colour, why, measures in D.GOALS:
                row = G.create(c, "human:ana", title, owner, keys.get(parent))
                keys[key] = row["id"]
                who = "human:ana" if owner == G.COMPANY else owner
                if colour:
                    G.set_status(c, who, row["id"], colour, why)
                elif parent:
                    # A goal under another starts with no colour until the owner of the one above accepts it;
                    # accepting it and letting the Strategy Bot set it leaves it automatic.
                    G.set_status(c, "human:ana", row["id"], "green", "Accepted")
                    G.hand_back(c, "human:ana", row["id"])
                for name, unit, target, values, days in measures:
                    kpi = G.kpi_create(c, who, owner, {"name": name, "unit": unit, "cadence": "weekly"})
                    for index, value in enumerate(values):
                        self.at(days=6 - index * 2.5)
                        G.kpi_log(c, who, kpi["id"], value, quality="measured", period_end=self.clock.at.isoformat())
                    G.kpi_link(c, who, row["id"], kpi["id"],
                               {"kind": "improve", "baseline": values[0], "baseline_at": self.ago(days=6).isoformat(),
                                "target": target, "deadline": (self.ago(days=6) + timedelta(days=days + 6)).date().isoformat()})
        self.write(work)

    def docs(self):
        self.at(days=6.4)
        service = docs.Docs(None, None, None, None)

        def work(c):
            for doc in D.DOCS:
                if doc.get("collection") == "notes":
                    continue
                folder = "help" if doc["category"].startswith("External") else doc["category"].split("/")[-1].strip().lower()
                service.insert(c, "human:ana", doc["title"], doc["content"], folder + "/" + doc["id"].split("-", 1)[-1] + ".md")
            for url, title, note in D.LINKS:
                docs.add_link(c, "human:ana", docs.LinkCreate(url=url, title=title, description=note))
        self.write(work)

    @staticmethod
    def edge(row):
        """A company's line to Acme Lights: a competitor points at us; a partner is one we point at."""
        competitor = row["rel"] == "competes_with"
        src, dst = (row["id"], "company/self") if competitor else ("company/self", row["id"])
        return {"id": "edge-" + row["rel"].replace("_", "-") + "-" + row["id"].split("/")[1], "src": src, "rel": row["rel"],
                "dst": dst, "confidence": "medium", "evidence_id": D.WATCH_EVIDENCE["id"]}

    def market(self):
        self.at(days=6.2)
        document = {"watch_evidence": D.WATCH_EVIDENCE,
                    "companies": [{"id": row["id"], "name": row["name"], "type": "company", "tier": row["tier"],
                                   "aliases": row["aliases"], "external_ids": row["external_ids"], "summary": row["summary"]}
                                  for row in D.COMPANIES],
                    "segments": D.SEGMENTS, "channels": D.CHANNELS,
                    "edges": [self.edge(row) for row in D.COMPANIES if row.get("rel")]}

        def work(c):
            market.seed(c, document=document)
            market.ensure_curator(c)
            for index, (key, kind, url, quote, read) in enumerate(D.EVIDENCE):
                self.at(days=4 - index)
                market.create_evidence(c, market.CURATOR, evidence_key="ev-" + key, source_url=url, source_kind=kind,
                                       captured_at=H.now(), quote=quote, our_read=read)
            for entity, summary, evidence, days in D.ENTITY_UPDATES:
                self.at(days=days)
                market.update_entity(c, market.CURATOR, entity, summary=summary, evidence_ids=["ev-" + evidence],
                                     last_verified_at=self.ago(days=days).date().isoformat())
            for index, (actor, kind, about, claim, url, quote, confidence) in enumerate(D.INSIGHTS):
                self.at(days=1.4 - index * 0.3)
                market.report(c, actor, kind=kind, about=about, claim=claim, source_url=url, quote=quote,
                              confidence=confidence)
            self.at(days=0.2)
            market.seed_pages(c)
            for doc_id, title, category, body in D.MARKET_PAGES:
                market.write_page(c, market.CURATOR, doc_id, title, body, category, replace=True)
            market.write_page(c, market.CURATOR, "market/weekly-delta", "Weekly delta",
                              market.delta_body(c, self.now.date()), "Market / Delta", replace=True, record=False)
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('market-context',?)", (encode({
                "sells": D.ANSWERS["what_we_do"], **D.MARKET_CONTEXT,
                "updated_at": H.now(), "updated_by": "human:ana"}),))
        self.write(work)

    def routines(self):
        self.at(days=6.0)

        def work(c):
            for bot, key, title, cron, text, _quiet in D.ROUTINES:
                routines.create(c, "human:ana", bot, {"title": title, "cron": cron, "text": text,
                                                       "timezone": "America/Los_Angeles"}, key=key, at=self.ago(days=6))
            # The Librarian's market routines were made with the wall clock; every routine is next due after `now`.
            for row in c.execute("SELECT s.id,s.cron,coalesce(sc.timezone,'America/Los_Angeles') zone FROM schedules s "
                                 "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.event_name IS NULL AND s.cron<>''").fetchall():
                c.execute("UPDATE schedules SET next_due_at=?,updated_at=? WHERE id=?",
                          (stamp(next_due(row["cron"], self.now, row["zone"])), stamp(self.now), row["id"]))
            c.execute("UPDATE schedules SET updated_at=? WHERE event_name IS NOT NULL", (stamp(self.now),))
        self.write(work)

    # ------------------------------------------------------------------ work and conversation
    def tasks_and_chats(self):
        def work(c):
            for key, title, body, owner, requester, status, created, changed, note, parent in D.TASKS:
                self.at(days=created)
                task = hubdb.task_create(c, requester, title, body, owner, parent_id=self.tasks.get(parent), allow_planned=True)
                self.tasks[key] = task["id"]
            for key, title, owner, target, body, created, _ in D.ASKS:
                self.at(days=created)
                due = stamp(self.now + timedelta(days=D.ASK_DUE[key])) if key in D.ASK_DUE else None
                task = hubdb.task_create(c, owner, title, body, target, due=due, allow_planned=True)
                self.tasks[key] = task["id"]
        self.write(work)

        def move(c):
            for key, title, body, owner, requester, status, created, changed, note, parent in D.TASKS:
                tid = self.tasks[key]
                if status == "open":
                    continue
                self.at(days=changed)
                if status == "closed":
                    hubdb.task_update(c, owner, tid, status="done", note=note)
                    self.at(days=changed - 0.2)
                    hubdb.task_close(c, requester, tid, "Thanks.")
                else:
                    hubdb.task_update(c, owner, tid, status=status, note=note)
        self.write(move)

        def approve(c):
            self.at(days=D.APPROVAL["days"])
            hubdb.approval_request(c, D.APPROVAL["by"], D.APPROVAL["kind"], D.APPROVAL["payload"],
                                   self.tasks[D.APPROVAL["task"]])
        self.write(approve)

        for bot, (hours, lines) in D.CHATS.items():
            self.at(hours=hours)
            last = None
            for sender, text in lines:
                self.clock.at += timedelta(minutes=2)
                if bot == "coo":
                    # Ana's own Assistant: the product answers it, so only her words are seeded.
                    self.call("POST", "assistant/messages", {"text": text}, token=TOKENS[sender])
                elif sender.startswith("human:"):
                    last = self.call("POST", f"chat/{bot}", {"text": text}, token=TOKENS[sender])["message"]
                else:
                    last = self.write(lambda c: hubdb.say(c, sender, last["sender_id"], text,
                                                          conversation_id=last["conversation_id"], in_reply_to=last["id"]))

    def meetings(self):
        for entry in D.MEETINGS:
            started = self.ago(days=entry["days"])
            turns, at_ms = [], 0
            for speaker, text in entry["turns"]:
                length = max(8000, len(text.split()) * 420)
                turns.append({"speaker": speaker, "text": text, "start_ms": at_ms, "end_ms": at_ms + length})
                at_ms += length + 1500
            self.at(days=entry["days"] - at_ms / 86_400_000)
            result = self.call("POST", "meetings/import", {
                "title": entry["title"], "started_at": started.isoformat(), "duration_seconds": at_ms / 1000,
                "participants": entry["participants"], "source": entry["source"], "external_id": entry["external_id"],
                "transcript": turns, "notes": entry["notes"], "review": entry.get("review", "live")})
            items = {}
            for section, text, detail, quote, at in entry["items"]:
                items[text] = self.call("POST", f"/api/meetings/{result['id']}/items",
                                        {"section": section, "text": text, "detail": detail, "quote": quote, "at_ms": at})["item"]["id"]
            for text in entry.get("push", ()):
                self.call("POST", f"/api/meetings/{result['id']}/items/{items[text]}/push", {"force": True})
            if entry.get("send_to"):
                sent = self.call("POST", f"/api/meetings/{result['id']}/send", {"bot_id": entry["send_to"]})
                if entry.get("send_status") and sent.get("task"):
                    self.write(lambda c: hubdb.task_update(c, "bot:" + entry["send_to"], sent["task"], status=entry["send_status"],
                                                           note="Reading the call for follow-ups."))
            for actor, text, at in entry["comments"]:
                self.call("POST", f"/api/meetings/{result['id']}/comments", {"text": text, "at_ms": at})
        self.granola()

    def granola(self):
        """A connected Granola account for Ana, as Meetings shows it after a sync."""
        self.at(hours=D.GRANOLA["minutes_ago"] / 60)
        synced = hubdb.now()

        def work(c):
            cid = "granola:demo"
            ciphertext, nonce = self.app.state.vault.cipher.encrypt(c, cid, encode({"client_id": "demo"}))
            meta = {"state": "connected", "email": D.GRANOLA["email"], "last_sync": synced, "plan_hint": "free",
                    "imported_count": D.GRANOLA["imported_count"], "needs_signin": False, "last_error": None, "skipped": 0}
            c.execute("INSERT INTO granola_connections VALUES(?,?,?,?,?,?)",
                      ("human:ana", cid, D.GRANOLA["email"], ciphertext, nonce, encode(meta)))
        self.write(work)

    def updates(self):
        """A week of daily updates and Friday's review, posted from mid-morning Pacific. Every bot posts, and each bot
        posts after the bots that report to it, so a lead's summary reads its reports' same-day updates. Older ones are
        read. Eight days, so a Friday morning before the review is posted still has last Friday's on the Weekly tab."""
        parent = {slug: reports_to for slug, _, reports_to in D.BOTS}
        depth = {}

        def level(slug):
            if slug not in depth:
                up = parent[slug]
                depth[slug] = 0 if up is None or up.startswith("human:") else 1 + level(up)
            return depth[slug]

        roster = {slug: position for position, (slug, _, _) in enumerate(D.BOTS)}
        order = sorted(D.BOTS, key=lambda bot: -level(bot[0]))      # stable: roster order within each level
        # People post after every bot, leads before Ana, so her update reads her leads' same-day words.
        people = [f"human:{person['id']}" for person in [*D.PEOPLE[1:], D.PEOPLE[0]]]

        def work(c):
            posted = []
            for back in range(7, -1, -1):
                day = updates.local_now(self.ago(days=back)).date()
                when = datetime.combine(day, datetime.min.time(), updates.ZONE).replace(hour=7, minute=10)
                kind = updates.kind_for(day.isoformat())
                for slot, (bot, _, _) in enumerate(order):
                    if (bot, back) in D.UPDATE_GAPS:
                        continue
                    at = when.astimezone(timezone.utc) + timedelta(minutes=slot * 2)
                    if at > self.now - timedelta(minutes=5):
                        continue
                    goal, first, second, next_week, blockers = D.UPDATES[bot]
                    self.clock.at = at
                    if kind == "weekly":
                        done = [*first, second[0]]
                        slides = {"goal": goal, "done": done, "focus": list(next_week), "blockers": list(blockers)}
                        if bot in D.WEEK_KPIS:
                            slides["kpis"] = D.WEEK_KPIS[bot]
                        body = "\n".join("- " + line for line in done)
                        row = updates.post(c, bot, body, kind=kind, day=day.isoformat(), slides=slides)
                    else:
                        lines = (first, second)[(7 - back + roster[bot]) % 2]
                        body = "\n".join("- " + line for line in lines)
                        row = updates.post(c, bot, body, kind=kind, day=day.isoformat())
                    posted.append((row, at))
                for position, person in enumerate(people):
                    at = when.astimezone(timezone.utc) + timedelta(minutes=(len(order) + position) * 2)
                    if at > self.now - timedelta(minutes=5):
                        continue
                    content = D.PEOPLE_UPDATES[person]
                    self.clock.at = at
                    if kind == "weekly":
                        week = content["week"]
                        slides = {"goal": week["goal"], "done": list(week["done"]), "focus": list(week["focus"]),
                                  "blockers": list(week["blockers"])}
                        body = "\n".join("- " + line for line in week["done"])
                        row = updates.post(c, person, body, kind=kind, day=day.isoformat(), slides=slides, drafted_by="coo")
                    else:
                        lines = content["daily"][(7 - back + position) % 2]
                        body = "\n".join("- " + line for line in lines)
                        row = updates.post(c, person, body, kind=kind, day=day.isoformat(), drafted_by="coo")
                    posted.append((row, at))
            # Everything older than yesterday is read; the last day's stays unread so the page has a badge.
            edge = self.now - timedelta(hours=30)
            for row, at in posted:
                if at < edge:
                    self.clock.at = at + timedelta(hours=1)
                    updates.mark(c, "human:ana", [row["id"]])
            for slug, _, _ in D.BOTS:
                updates.set_settings(c, slug, daily=True, weekly=True)
        self.write(work)

    def history(self):
        """What the computers would have recorded: turns, each bot's status, and read receipts."""
        def work(c):
            # The routine turns of the last week are the scheduled runs (demo_sections/schedules.py), so these are left
            # out for those bots. The Inbox's routine is off, and its turns stay.
            scheduled = {routine[0] for routine in D.ROUTINES} | {"goal-manager"}
            scheduled.discard("inbox")
            for bot, hours, trigger, summary, tokens_in, tokens_out in D.TURNS:
                if trigger == "routine" and bot in scheduled:
                    continue
                self.at(hours=hours)
                task = None
                routine = c.execute("SELECT id,title FROM schedules WHERE bot=? ORDER BY id LIMIT 1", (bot,)).fetchone() if trigger == "routine" else None
                if routine:
                    task = hubdb.task_create(c, hubdb.KEEPER, routine["title"], summary, "bot:" + bot, deduplicate=False)
                # The run's note is the bot's own words, never a message from a person; Runs finds the run by it.
                note = hubdb.say(c, "bot:" + bot, "human:ana", summary, refs={"task": task["id"]} if task else {})
                turn = hubdb.turn_start(c, hubdb.KEEPER, bot, trigger=trigger, message_id=note["id"],
                                        task_id=task["id"] if task else None)
                self.clock.at += timedelta(minutes=4)
                model, provider, billing = D.USAGE[bot]
                tokens_in, tokens_out = tokens_in * D.RUN_REQUESTS, tokens_out * D.RUN_REQUESTS
                cached = tokens_in * 2 // 3
                hubdb.turn_finish(c, hubdb.KEEPER, turn["id"], "completed", summary,
                                  usage={"input_tokens": tokens_in - cached, "cached_tokens": cached,
                                         "output_tokens": tokens_out, "model": model, "provider": provider,
                                         "est_cost_usd": providers.estimate_cost(model, tokens_in - cached, cached, tokens_out),
                                         "billing": billing})
                if task:
                    hubdb.task_update(c, "bot:" + bot, task["id"], status="done", note=summary)
                    c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,'completed')",
                              (routine["id"], turn["started_at"], task["id"]))
                    c.execute("UPDATE schedules SET last_fired_at=max(coalesce(last_fired_at,''),?) WHERE id=?",
                              (turn["started_at"], routine["id"]))
                hubdb.status_result(c, hubdb.KEEPER, bot, last_result=summary, last_turn_at=H.now())
            # A change's reason is written on the row it closes, so each step's why goes in with the next one.
            for bot, steps in D.STATUS_STEPS.items():
                why = ""
                for hours, state, focus, next_why in steps:
                    self.at(hours=hours)
                    hubdb.status_set(c, hubdb.KEEPER, bot, state=state, focus=focus, reason=why)
                    why = next_why
            self.at(hours=1)
            for bot, focus in D.FOCUS.items():
                if bot not in D.STATUS_STEPS:
                    hubdb.status_set(c, hubdb.KEEPER, bot, state="idle", focus=focus)
        self.write(work)

    def past_weeks(self):
        """The weeks before the one the demo plays (D.PAST_WEEKS): each routine's runs on its schedule, the built-in
        helpers' daily runs and the bots' own finished work. Each is a task its bot finished in one run, priced as the
        week's runs are, so Overview's Done per week, Usage and Strategy › Budget read a team that has been at work."""
        start, end = self.now - timedelta(weeks=D.PAST_WEEKS + 1), self.now - timedelta(weeks=1)
        manager = {slug: up for slug, _, up in D.BOTS}
        work = []                                   # (when, bot, title, requester, trigger, note)
        for bot, _key, title, cron, _text, quiet in D.ROUTINES:
            at = start
            while (at := next_due(cron, at, "America/Los_Angeles")) < end:
                work.append((at, bot, title, hubdb.KEEPER, "routine", quiet[len(work) % len(quiet)]))
        for day in range(D.PAST_WEEKS * 7):
            for index, (bot, title) in enumerate(D.PAST_DAILY):
                work.append((start + timedelta(days=day, hours=13, minutes=20 * index), bot, title, hubdb.KEEPER,
                             "routine", "Done; nothing needs a person."))
        tasks = itertools.cycle(D.PAST_TASKS)
        for week, count in enumerate(D.PAST_ONE_OFFS):
            for number in range(count):
                bot, title = next(tasks)
                up = manager.get(bot)
                asker = "bot:" + up if up and not up.startswith("human:") else "bot:coo"
                work.append((start + timedelta(weeks=week, hours=15 + number * (7 * 24 - 20) / count), bot, title,
                             asker, "task", "Done."))
        # Each week most of the team also answered two chats; a few more bots each week, as the team grew.
        for week in range(D.PAST_WEEKS):
            share = len(D.BOTS) * (week + 6) // (D.PAST_WEEKS + 6)
            for rank, (bot, _, _) in enumerate(D.BOTS[:share]):
                for number in range(2):
                    work.append((start + timedelta(weeks=week, days=(rank * 2 + number * 3) % 7, hours=16 + rank % 6),
                                 bot, "", "", "message", "Answered a question in chat."))
        position = {slug: index for index, (slug, _, _) in enumerate(D.BOTS)}

        def run(c):
            for index, (at, bot, title, asker, trigger, note) in enumerate(sorted(work)):
                self.clock.at = at
                # Long finished: the notices are quiet, so no bot is woken for work already done. A chat has no task.
                task = hubdb.task_create(c, asker, title, note if trigger == "routine" else title, "bot:" + bot,
                                         deduplicate=False, quiet_notice=True) if title else None
                turn = hubdb.turn_start(c, hubdb.KEEPER, bot, trigger=trigger, task_id=task["id"] if task else None)
                self.clock.at += timedelta(minutes=3 + index % 9)
                model, provider, billing = D.USAGE.get(bot, ("claude-haiku-5-5", "anthropic", "api"))
                if trigger == "routine":
                    tokens_in, tokens_out = 21000 + 3500 * (index % 7), 1500 + 400 * (index % 5)
                else:
                    tokens_in = 26000 + (position.get(bot, 0) * 3100 + index * 700) % 52000
                    tokens_out = 1800 + (position.get(bot, 0) * 260) % 3600
                # A chat answer is a few requests; a task is the whole run's.
                requests = D.RUN_REQUESTS // 4 if trigger == "message" else D.RUN_REQUESTS
                tokens_in, tokens_out = tokens_in * requests, tokens_out * requests
                cached = tokens_in * 2 // 3
                hubdb.turn_finish(c, hubdb.KEEPER, turn["id"], "completed", note,
                                  usage={"input_tokens": tokens_in - cached, "cached_tokens": cached,
                                         "output_tokens": tokens_out, "model": model, "provider": provider,
                                         "est_cost_usd": providers.estimate_cost(model, tokens_in - cached, cached, tokens_out),
                                         "billing": billing})
                if task:
                    hubdb.task_update(c, "bot:" + bot, task["id"], status="done", note=note, quiet=True)
        self.write(run)

    def messaging(self):
        self.at(hours=2)
        address = D.INBOX
        messages = []
        for index, (subject, body, labels, sender, to) in enumerate(D.MAIL):
            at = self.ago(hours=2 - index / 4)
            messages.append({"msg_id": f"demo-mail-{index}", "thread_id": "demo-calibration" if index < 2 else "demo-second-kit",
                             "epoch": int(at.timestamp()), "date": at.isoformat(), "from_addr": sender,
                             "from_header": sender, "to": [to],
                             "subject": subject, "snippet": body[:120], "body": body, "labels": labels})
        self.call("POST", "connectors/mail/messages", {"mailbox": address, "messages": messages, "synced_at": H.now()},
                  token=self.linux["token"])
        self.call("POST", "slack/channels", {"channel": "CDEMOTEAM1", "name": D.SLACK["channel"], "readers": [D.SLACK["channel"]],
                                              "post": False, "note": D.SLACK["note"]})
        # The app is connected as far as this machine can show: tokens sealed with a local key, the gateway's line set to
        # connected. No gateway runs in the demo, so nothing dials Slack; the tokens are placeholders.
        slack_app.save(self.store, self.settings, "human:ana", "xoxb-demo-placeholder", "xapp-demo-placeholder")
        slack_app.pin(self.store, D.SLACK["team"], D.SLACK["app"])
        slack_app.report(self.store, "connected", "Connected to the Acme Lights workspace.", team=D.SLACK["team"])

        def work(c):
            c.execute("INSERT INTO mail_agent_instructions VALUES(?,?,?,?)",
                      ("inbox", D.INBOX_INSTRUCTIONS, self.linux["runner_id"], H.now()))
            timestamp = str(int(self.ago(hours=1).timestamp())) + ".000001"
            for index, (name, kind, text) in enumerate(D.SLACK["thread"]):
                ts = str(int(self.ago(hours=1 - index / 4).timestamp())) + ".000001"
                c.execute("INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,ts,user_id,event_type,text,received_at,state,author,author_name,updated_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"demo-slack-{index}", "TDEMOTEAM1", "CDEMOTEAM1", "channel", timestamp, ts,
                           "UDEMOTEAM1", "message", text, H.now(), "stored", kind, name, H.now()))
            conv = H.open_conversation(c, H.KEEPER, [H.KEEPER, "bot:support"], kind="chat", subject=D.SLACK["subject"], scope="direct")
            message = H.feed(c, "bot:support", D.SLACK["feed"], conv)
            c.execute("INSERT INTO slack_threads(channel,thread_ts,bot,conversation_id,created_at,last_routed_at) VALUES(?,?,?,?,?,?)", ("CDEMOTEAM1", timestamp, "support", conv["id"], H.now(), H.now()))
            c.execute("INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (message["id"], "CDEMOTEAM1", timestamp, "support", D.SLACK["draft"], "ready", H.now(), H.now()))
            c.execute("INSERT INTO slack_reads VALUES(?,?,?,?,?)", ("CDEMOTEAM1", "support", timestamp, H.now(), 1))
        self.write(work)

    def files(self):
        from .blobs import register
        files, blobs = self.api.app.state.files, self.api.app.state.blobs
        for index, (bot, path, mime, text) in enumerate(D.FILES):
            self.at(hours=6 - index)
            data = text.encode()
            digest = blobs.put(data, mime)
            def work(c):
                item = register(c, Identity("bot:" + bot, "bot"), digest, len(data), path.rsplit("/", 1)[-1], mime)
                files.add_version(c, bot=bot, actor="bot:" + bot, scope="bot", task=None, conversation=None, attempt="",
                                  identity="repo:" + path, title=path, name=item["name"], mime=mime, blob_id=item["id"],
                                  digest=digest, size=len(data), repo_path=path)
            self.write(work)
        self.call("POST", "files/links", {"bot": D.FILE_LINK["bot"], "url": D.FILE_LINK["url"], "title": D.FILE_LINK["title"]})

    def page(self):
        """The bot's own bot-wide page, as Home's Latest pages shows it: the bytes, the file and its version, the library
        entry and the page row, as files.publish and pages.publish write them. The seed writes them itself because a
        runner may not publish bot-wide outside a turn, and the seed runs no turn."""
        from . import file_library
        from .blobs import register
        self.at(hours=5)
        files, blobs = self.api.app.state.files, self.api.app.state.blobs
        bot, name, data = D.PAGE["bot"], D.PAGE["name"], D.PAGE["text"].encode()
        digest = blobs.put(data, "text/html")

        def work(c):
            item = register(c, Identity("bot:" + bot, "bot"), digest, len(data), name, "text/html")
            row, _, _ = files.add_version(c, bot=bot, actor="bot:" + bot, scope="bot", task=None, conversation=None, attempt="",
                                          identity="page:" + name, title=D.PAGE["title"], name=name, mime="text/html",
                                          blob_id=item["id"], digest=digest, size=len(data), repo_path="", explicit_title=True)
            file_library.index(c, row["id"], row["current_version"], row["title"], file_library.extract("text/html", data))
            c.execute("INSERT INTO pages(file_id,cadence,as_of,created_at,updated_at) VALUES(?,?,?,?,?)",
                      (row["id"], "none", None, H.now(), H.now()))
        self.write(work)

    def icon(self):
        """The team icon Settings shows, drawn here so its page has one; the owner sets it like anyone would."""
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (256, 256), D.ICON["background"])
        ImageDraw.Draw(image).ellipse((64, 64, 192, 192), fill=D.ICON["mark"])
        out = io.BytesIO()
        image.save(out, format="PNG")
        reply = self.api.request("POST", "/api/v2/team/icon", content=out.getvalue(), headers={
            "Authorization": "Bearer " + OWNER, "Content-Type": "image/png", "Idempotency-Key": f"demo-{next(self.serial)}"})
        assert reply.status_code == 200, f"team icon: {reply.status_code} {reply.text[:300]}"

    def read_task_threads(self):
        """Ana's task threads are read, except the decisions and the firmware rollout she still has to settle (KEEP_UNREAD),
        so the sidebar's unread list shows those. Read through the API as her own reads."""
        rows = self.write(lambda c: c.execute(
            "SELECT t.title, cv.id, cv.last_seq FROM tasks t JOIN conversations cv ON cv.id=t.conversation_id "
            "WHERE cv.last_seq>0 AND (t.owner_id='human:ana' OR t.requester_id='human:ana') ORDER BY cv.id").fetchall())
        for row in rows:
            if row["title"] not in D.KEEP_UNREAD:
                self.call("POST", f"threads/{row['id']}/read", {"seq": row["last_seq"]})

    def routine_tasks(self, done=D.ROUTINE_DONE):
        """A routine's task is read and closed a couple of hours on: the Librarian's task for each live meeting (before the
        sections), or the goal-manager's bet review of the market briefing (after them, once the scheduler has made it)."""
        def work(c):
            rows = c.execute("SELECT id, created_at FROM tasks WHERE owner_id=? AND title=? AND status='open'",
                             (done["owner"], done["title"])).fetchall()
            for row in rows:
                made = datetime.fromisoformat(row["created_at"].rstrip("Z")).replace(tzinfo=timezone.utc)
                self.clock.at = made + timedelta(hours=2)
                hubdb.task_update(c, done["owner"], row["id"], status="done", note=done["note"])
        self.write(work)

    def drain_media(self):
        """Settle every pending scan here, as the app's worker would in the background: no row is left pending."""
        while self.media.batch():
            pass

    def finish(self):
        """Nothing is left waiting for a bot: every message reads as delivered and no job is queued. SQLite's own clock
        and random bytes stamp some rows (triggers, the change feed, a census), so those take this build's time and ids:
        the same build then writes the same rows."""
        self.drain_media()
        self.read_task_threads()
        # Every bot gets its profile, as the next start would, without the notice that explains a change to bots an
        # owner already had (backend/profiles.py).
        from . import profiles as Profiles
        self.write(lambda c: Profiles.migrate(c, tell=False))

        def work(c):
            c.execute("UPDATE messages SET delivered_at=created_at WHERE delivered_at IS NULL AND recipient_id LIKE 'bot:%'")
            c.execute("UPDATE jobs SET state='completed' WHERE state IN ('queued','leased','running','input')")
            c.execute("DELETE FROM update_queue")
            c.execute("DELETE FROM thread_routings")
            when = stamp(self.now)
            for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                columns = {row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}
                for column in [name for name in ("created_at", "updated_at") if name in columns]:
                    c.execute(f'UPDATE "{table}" SET "{column}"=? WHERE length("{column}")=24', (when,))
            c.execute("UPDATE message_deliveries SET id='dlv-' || rowid WHERE id NOT LIKE 'dlv-%'")
            usage = c.execute("SELECT value FROM registry_metadata WHERE key='usage-count'").fetchone()
            if usage and json.loads(usage[0]).get("last_person"):
                value = json.loads(usage[0])
                value["last_person"] = when
                c.execute("UPDATE registry_metadata SET value=? WHERE key='usage-count'", (encode(value),))
        self.write(work)
