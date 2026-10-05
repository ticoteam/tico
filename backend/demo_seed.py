"""Builds the demo company's database through the same code a real one runs on.

Rows come from the domain layer and the HTTP API (so every rule a real install enforces holds for
the sample data), with two things made fixed: the clock is set to chosen moments relative to `now`
(a week of history ending just now), and ids and secrets come from a counter. Building twice with the
same `now` gives the same database.
"""

import contextlib
import dataclasses
import hashlib
import itertools
import secrets
import uuid
import warnings
from datetime import datetime, timedelta, timezone

import yaml

# Starlette prefers a different HTTP client for its test client; the seed needs only its own app.
warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
from fastapi.testclient import TestClient  # noqa: E402

from . import demo_content as D
from . import docs, goals as G, hubdb, market, onboarding, providers, routines, updates
from .auth import Identity
from .scheduler import next_due, stamp
from .store import H, encode

NAMESPACE = uuid.UUID("7d1c2f3e-5a4b-4c6d-8e9f-0a1b2c3d4e5f")
OWNER = "demo-owner"
TOKENS = {"human:ana": OWNER, "human:cara": "demo-cara", "human:ben": "demo-ben"}
RUNNERS = (("Ana's MacBook", "darwin", ("claude", "codex")), ("acme-runner-1", "linux", ("claude",)))
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

    saved = (hubdb.now, hubdb.new_id, secrets.token_urlsafe, secrets.token_hex)
    hubdb.now = clock
    hubdb.new_id = lambda: str(uuid.uuid5(NAMESPACE, str(next(counter))))
    secrets.token_urlsafe = lambda nbytes=32: (digest() * 3)[: (nbytes * 4 + 2) // 3]
    secrets.token_hex = lambda nbytes=32: (digest() * 3)[: nbytes * 2]
    try:
        yield
    finally:
        hubdb.now, hubdb.new_id, secrets.token_urlsafe, secrets.token_hex = saved


def write_registry(settings):
    """Who is on the roster and which bots exist: the files a new install's first boot imports."""
    registry = settings.registry_dir
    registry.mkdir(parents=True, exist_ok=True)
    cards = {card["template"]: card for card in onboarding.read_cards(settings)}
    names = {"company_name": D.COMPANY, "app_name": settings.app_name, "assistant_name": settings.assistant_name}
    employees = []
    for slug, template, reports_to in D.BOTS:
        card = onboarding.render(cards[template], names, cards[template]["name"]) if template in cards else {}
        employees.append({"name": slug, "display_name": settings.assistant_name if slug == "coo" else card.get("name", slug),
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
                "demo-ben": Identity("human:ben", "human", D.PEOPLE[1]["email"])})
        app = create_app(seeding)
        self.store = app.state.store
        self.at(days=7)
        with TestClient(app) as api:
            self.api = api
            self.company()
            self.computers()
            self.goals()
            self.docs()
            self.market()
            self.routines()
            self.tasks_and_chats()
            self.meetings()
            self.updates()
            self.history()
            self.messaging()
            self.files()
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
                "selected": {slug: {"template": template, "display_name": card_names.get(template, slug.title()),
                                    "instructions": ""}
                             for slug, template, _ in D.BOTS if slug not in ("coo", "botops")}}),))
            # The tour is over; the checklist stays because GitHub, its one optional step, is not connected.
            c.execute("INSERT INTO preferences VALUES('human:ana','onboarding.progress',?,?)",
                      (encode({"tour": True, "checklist": False, "skipped": []}), H.now()))
        self.write(record)

    def computers(self):
        self.at(days=6.9)
        for label, platform, runtimes in RUNNERS:
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
                    # accepting it and letting the Goal Manager set it leaves it automatic.
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
            for url, title, note in (("https://help.acme.example", "Acme help centre", "What customers read"),
                                     ("https://drive.google.com/drive/folders/acme-brand", "Brand assets", "Logos, colours and the tone guide")):
                docs.add_link(c, "human:ana", docs.LinkCreate(url=url, title=title, description=note))
        self.write(work)

    def market(self):
        self.at(days=6.2)
        document = {"watch_evidence": {"id": "ev-watchlist", "source_kind": "other",
                                       "our_read": "Names on the listening watchlist, each treated as competing with Acme."},
                    "companies": [{"id": row["id"], "name": row["name"], "type": "company", "tier": row["tier"],
                                   "aliases": row["aliases"], "external_ids": row["external_ids"], "summary": row["summary"]}
                                  for row in D.COMPANIES],
                    "segments": D.SEGMENTS, "channels": D.CHANNELS,
                    "edges": [{"id": "edge-competes-" + row["id"].split("/")[1], "src": row["id"], "rel": "competes_with",
                               "dst": "company/self", "confidence": "medium", "evidence_id": "ev-watchlist"}
                              for row in D.COMPANIES if row["tier"]]}

        def work(c):
            market.seed(c, document=document)
            market.ensure_curator(c)
            for index, (key, kind, url, quote, read) in enumerate(D.EVIDENCE):
                self.at(days=4 - index)
                ev, _ = market.create_evidence(c, market.CURATOR, evidence_key="ev-" + key, source_url=url, source_kind=kind,
                                               captured_at=H.now(), quote=quote, our_read=read)
            self.at(days=3.8)
            market.update_entity(c, market.CURATOR, "company/northwind",
                                 summary="A large suite vendor with a self-serve tool. Cheapest starter plan: $29, down from $39.",
                                 evidence_ids=["ev-northwind-pricing"], last_verified=self.ago(days=3.8).date().isoformat())
            self.at(days=3.0)
            market.update_entity(c, market.CURATOR, "company/brightline",
                                 summary="A venture-backed competitor. Launched a mobile app for studios last month.",
                                 evidence_ids=["ev-brightline-app"], last_verified=self.ago(days=3.0).date().isoformat())
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
                "sells": D.ANSWERS["what_we_do"], "customers": "Small studios with a few client projects",
                "competitors": "Northwind, Brightline, Pricewise, Harborly, Fernwood, Doorlark",
                "channels": "r/projectmanagement", "updated": H.now(), "updated_by": "human:ana"}),))
        self.write(work)

    def routines(self):
        self.at(days=6.0)

        def work(c):
            for bot, key, title, cron, text in D.ROUTINES:
                routines.create(c, "human:ana", bot, {"title": title, "cron": cron, "text": text,
                                                       "timezone": "America/Los_Angeles"}, key=key, at=self.ago(days=6))
            # The Librarian's market routines were made with the wall clock; every routine is next due after `now`.
            for row in c.execute("SELECT s.id,s.cron,coalesce(sc.timezone,'America/Los_Angeles') zone FROM schedules s "
                                 "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.event_name IS NULL AND s.cron<>''").fetchall():
                c.execute("UPDATE schedules SET next_due=?,updated_at=? WHERE id=?",
                          (stamp(next_due(row["cron"], self.now, row["zone"])), stamp(self.now), row["id"]))
            c.execute("UPDATE schedules SET updated_at=? WHERE event_name IS NOT NULL", (stamp(self.now),))
        self.write(work)

    # ------------------------------------------------------------------ work and conversation
    def tasks_and_chats(self):
        def work(c):
            for key, title, body, owner, requester, status, created, changed, note in D.TASKS:
                self.at(days=created)
                task = hubdb.task_create(c, requester, title, body, owner, allow_planned=True)
                self.tasks[key] = task["id"]
            for key, title, owner, target, body, created, _ in D.ASKS:
                self.at(days=created)
                task = hubdb.task_create(c, owner, title, body, target, allow_planned=True)
                self.tasks[key] = task["id"]
        self.write(work)

        def move(c):
            for key, title, body, owner, requester, status, created, changed, note in D.TASKS:
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
                if sender.startswith("human:"):
                    last = self.call("POST", f"chat/{bot}", {"text": text}, token=TOKENS[sender])["message"]
                else:
                    last = self.write(lambda c: hubdb.say(c, sender, last["from_actor"], text,
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
                "transcript": turns, "notes": entry["notes"]})
            for section, text, detail, quote, at in entry["items"]:
                self.call("POST", f"/api/meetings/{result['id']}/items",
                          {"section": section, "text": text, "detail": detail, "quote": quote, "at_ms": at})
            for actor, text, at in entry["comments"]:
                self.call("POST", f"/api/meetings/{result['id']}/comments", {"text": text, "at_ms": at})

    def updates(self):
        """A week of daily updates and Friday's review, posted mid-morning Pacific. Older ones are read. Eight days, so
        a Friday morning before the review is posted still has last Friday's on the Weekly tab."""
        def work(c):
            posted = []
            for back in range(7, -1, -1):
                day = updates.local_now(self.ago(days=back)).date()
                when = datetime.combine(day, datetime.min.time(), updates.ZONE).replace(hour=7, minute=10)
                kind = updates.kind_for(day.isoformat())
                for slot, (bot, _, _) in enumerate(D.BOTS):
                    if bot == "content" and back == 3:
                        continue        # one bot that skipped a day, as real ones do
                    at = when.astimezone(timezone.utc) + timedelta(minutes=slot * 9)
                    if at > self.now - timedelta(minutes=5):
                        continue
                    bodies = D.UPDATES[bot]["weekly" if kind == "weekly" else "daily"]
                    body = bodies[(6 - back) % len(bodies)]
                    self.clock.at = at
                    posted.append((updates.post(c, bot, body, kind=kind, day=day.isoformat()), at))
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
            for bot, hours, trigger, summary, tokens_in, tokens_out in D.TURNS:
                self.at(hours=hours)
                task = None
                routine = c.execute("SELECT id,title FROM schedules WHERE bot=? ORDER BY id LIMIT 1", (bot,)).fetchone() if trigger == "routine" else None
                if routine:
                    task = hubdb.task_create(c, hubdb.KEEPER, routine["title"], summary, "bot:" + bot, deduplicate=False)
                # The run's note is the bot's own words, never a message from a person; Runs finds the run by it.
                note = hubdb.say(c, "bot:" + bot, "human:ana", summary, refs={"task": task["id"]} if task else {})
                turn = hubdb.turn_start(c, hubdb.KEEPER, bot, trigger=trigger, message_id=note["id"])
                self.clock.at += timedelta(minutes=4)
                model, provider, billing = D.USAGE[bot]
                cached = tokens_in * 2 // 3
                hubdb.turn_finish(c, hubdb.KEEPER, turn["id"], "completed", tokens_in, tokens_out,
                                  round((tokens_in * 5 + tokens_out * 25) / 1_000_000, 2), summary,
                                  usage={"input_tokens": tokens_in - cached, "cached_tokens": cached,
                                         "output_tokens": tokens_out, "model": model, "provider": provider,
                                         "est_cost_usd": providers.estimate_cost(model, tokens_in - cached, cached, tokens_out),
                                         "billing": billing})
                if task:
                    hubdb.task_update(c, "bot:" + bot, task["id"], status="done", note=summary)
                    c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,'completed')",
                              (routine["id"], turn["started"], task["id"]))
                    c.execute("UPDATE schedules SET last_fired=max(coalesce(last_fired,''),?) WHERE id=?",
                              (turn["started"], routine["id"]))
                hubdb.status_result(c, hubdb.KEEPER, bot, last_result=summary, last_turn_at=H.now())
            self.at(hours=1)
            for bot, focus in D.FOCUS.items():
                hubdb.status_set(c, hubdb.KEEPER, bot, state="idle", focus=focus)
        self.write(work)

    def messaging(self):
        self.at(hours=2)
        address = "ben@acme.example"
        messages = []
        for index, (subject, body, labels, sender) in enumerate([
            ("Help with CSV exports", "Can you help us export this month's project list?", ["INBOX", "hub/needs-owner"], "sam@example.com"),
            ("Re: Help with CSV exports", "Draft: Open Projects, choose Export, then CSV. Tell us if you need help.", ["DRAFT", "hub/drafted"], address),
            ("Trial follow-up", "Thanks for the walkthrough. We are ready to try the Team plan.", ["INBOX"], "ana@example.com"),
        ]):
            at = self.ago(hours=2 - index / 4)
            messages.append({"msg_id": f"demo-mail-{index}", "thread_id": "demo-export" if index < 2 else "demo-trial",
                             "epoch": int(at.timestamp()), "date": at.isoformat(), "from_addr": sender,
                             "from_header": sender, "to": ["sam@example.com" if index == 1 else address],
                             "subject": subject, "snippet": body[:120], "body": body, "labels": labels})
        self.call("POST", "connectors/mail/messages", {"mailbox": address, "messages": messages, "synced_at": H.now()},
                  token=self.linux["token"])
        self.call("POST", "slack/channels", {"channel": "CDEMOTEAM1", "name": "support", "readers": ["support"],
                                              "post": False, "note": "Demo support channel"})

        def work(c):
            c.execute("INSERT INTO mail_agent_instructions VALUES(?,?,?,?)",
                      ("inbox", "# Instructions\n\nTriage mail and draft replies for review.", self.linux["runner_id"], H.now()))
            timestamp = str(int(self.ago(hours=1).timestamp())) + ".000001"
            for index, text in enumerate(["A customer needs the CSV export steps.", "Draft reply: Open Projects > Export > CSV."]):
                ts = str(int(self.ago(hours=1 - index / 4).timestamp())) + ".000001"
                c.execute("INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,ts,user_id,event_type,text,received,state,author,author_name,updated) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"demo-slack-{index}", "TDEMOTEAM1", "CDEMOTEAM1", "channel", timestamp, ts,
                           "UDEMOTEAM1", "message", text, H.now(), "stored", "human" if index == 0 else "bot", "Sam" if index == 0 else "Support", H.now()))
            conv = H.open_conversation(c, H.KEEPER, [H.KEEPER, "bot:support"], kind="chat", subject="Slack support", scope="direct")
            message = H.feed(c, "bot:support", "Review the support channel's CSV export thread.", conv)
            c.execute("INSERT INTO slack_threads VALUES(?,?,?,?,?,?)", ("CDEMOTEAM1", timestamp, "support", conv["id"], H.now(), H.now()))
            c.execute("INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                      (message["id"], "CDEMOTEAM1", timestamp, "support", "Draft: Open Projects > Export > CSV.", "ready", H.now(), H.now()))
            c.execute("INSERT INTO slack_reads VALUES(?,?,?,?,?)", ("CDEMOTEAM1", "support", timestamp, H.now(), 1))
        self.write(work)

    def files(self):
        from .blobs import register
        files, blobs = self.api.app.state.files, self.api.app.state.blobs
        examples = [
            ("support", "exports/ticket-summary.csv", "text/csv", "topic,tickets\nCSV exports,12\nBilling,8\n"),
            ("content", "reports/launch.md", "text/markdown", "# Launch report\n\nFirst draft: the Team plan walkthrough is ready.\n"),
            ("content", "reports/launch.md", "text/markdown", "# Launch report\n\nReviewed draft: added the CSV export steps and customer feedback.\n"),
        ]
        for index, (bot, path, mime, text) in enumerate(examples):
            self.at(hours=6 - index)
            data = text.encode()
            digest = blobs.put(data, mime)
            def work(c):
                item = register(c, Identity("bot:" + bot, "bot"), digest, len(data), path.rsplit("/", 1)[-1], mime)
                files.add_version(c, bot=bot, actor="bot:" + bot, scope="bot", task=None, conversation=None, attempt="",
                                  identity="repo:" + path, title=path, name=item["name"], mime=mime, blob_id=item["id"],
                                  digest=digest, size=len(data), repo_path=path)
            self.write(work)
        self.call("POST", "files/links", {"bot": "sales", "url": "https://docs.example.com/demo-renewal-brief",
                                         "title": "Demo renewal brief (sample link)"})

    def finish(self):
        """Nothing is left waiting for a bot: every message reads as delivered and no job is queued."""
        def work(c):
            c.execute("UPDATE messages SET delivered_at=created WHERE delivered_at IS NULL AND to_actor LIKE 'bot:%'")
            c.execute("UPDATE jobs SET state='completed' WHERE state IN ('queued','leased','running','input')")
            c.execute("DELETE FROM update_queue")
        self.write(work)
