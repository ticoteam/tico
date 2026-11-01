"""The bot list reads every bot's rows at once (backend/bot_rows.py): the same answer, privacy included,
as reading them one bot at a time, and about the same number of statements for 7 bots as for 30."""

import collections
import json

from backend import (auto_tasks, agents, execution, lifecycle, pipeline_stats, profiles, rooms, start_places,
                     report_managers, update_feeds, views, task_models, wake_presets)
from backend import task_privacy as privacy
from backend.chat_goals import readable_active
from backend.harnesses import resolve_harness
from backend.settings_admin import SettingsAdmin, compact_at as compact_at_of
from backend.store import H, P, encode, repo_url
from backend.tests import test_api as T
from backend.tests.test_api import api  # noqa: F401

BASE = ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")
SEE_ONLY = ("slug", "display_name", "state", "description", "team", "operator", "reports_to", "owners",
            "thread_mode", "temp", "access", "bot_owners", "onboarding_state", "private_tasks_default", "stage",
           "can_complete_onboarding")


def per_bot_view(c, settings, auth, bot, level, access, registry_roster, registry_entries, who):
    """The bot list's row as it was read before backend/bot_rows.py: one bot at a time."""
    row = {k: v for k, v in bot.items() if k not in ("token_hash", "cwd", "thread_id")}
    config = c.execute("SELECT team,owner_id,owner_ids,revision,description,reports_to,repo,"
                       "thread_mode,config,onboarding_state FROM bot_config WHERE bot=?", (bot["slug"],)).fetchone()
    assignment = c.execute("SELECT a.bot,a.runner_id,a.generation,r.label,r.owner_id,r.last_seen_at,"
                           "r.revoked_at FROM assignments a JOIN runners r ON r.id=a.runner_id "
                           "WHERE a.bot=?", (bot["slug"],)).fetchone() if level["read"] else None
    row["access"] = level
    row["team"] = config["team"] if config else None
    row["owner_id"] = config["owner_id"] if config else None
    row["revision"] = config["revision"] if config else None
    row["onboarding_state"] = (config["onboarding_state"] or "") if config else ""
    row["stage"] = lifecycle.stage(row["onboarding_state"])
    if row["stage"] == lifecycle.ONBOARDING:
        row["can_complete_onboarding"] = lifecycle.may_complete(auth, c, who, bot["slug"])
        row["onboarding_ready"] = lifecycle.ready_of(config["config"]) if config else None
    if config:
        repo = config["repo"] or ("emp-" + bot["slug"])
        from backend.shared_bots import follow
        declared = follow(c, bot["slug"], json.loads(config["config"]) if config["config"] else {})
        if declared.get("shared_from"):
            repo = declared.get("repo") or repo
            row.update({"model": declared.get("model") or "", "runtime": declared.get("runtime") or "",
                        "effort": declared.get("reasoning_effort") or "", "session": declared.get("session"),
                        "fallback": declared.get("fallback")})
        row.update({"description": config["description"] or "",
                    "harness": resolve_harness(declared, bot.get("runtime")),
                    "reports_to": config["reports_to"], "repo": repo,
                    "repo_url": repo_url(repo, settings.github_owner),
                    "folder": H.bot_folder(bot["slug"], declared),
                    "bot_contact": declared.get("bot_contact") or "open",
                    "private_tasks_default": H.private_tasks_default(c, "bot:" + bot["slug"]),
                    "template": declared.get("template") or "",
                    "template_version": declared.get("template_version") or "",
                    "update_subscriptions": update_feeds.chosen(declared),
                    "shared": bool(declared.get("shared")),
                    "shared_from": str(declared.get("shared_from") or ""),
                    "temp": bool(declared.get("temp")),
                    "compact_at": compact_at_of(declared),
                    "task_model": task_models.of(declared),
                    "keep_tasks_going": auto_tasks.of(json.loads(config["config"]) or {}),
                    "turns_at_once": start_places.bot_limit(json.loads(config["config"]) or {}),
                    "task_model_warning": task_models.warning(c, settings, bot["slug"], declared,
                                                              task_models.of(declared)),
                    "manages_reports": report_managers.switch_on(declared),
                    "wake": wake_presets.effective(declared),
                    "profile": profiles.effective(declared),
                    "thread_mode": config["thread_mode"] or rooms.thread_mode(c, bot["slug"])})
    configured = json.loads(config["owner_ids"]) if config and config["owner_ids"] else None
    owner_rows = ([H.human(c, owner) for owner in configured] if configured is not None
                  else P.primary_users(bot["slug"], registry_roster, registry_entries))
    row["owners"] = [P.brief(owner) for owner in owner_rows if owner]
    row["bot_owners"] = SettingsAdmin.owner_rows(SettingsAdmin, c, bot["slug"]) if config else []
    if row.get("reports_to") and not str(row["reports_to"]).startswith("human:") \
            and not access.get(row["reports_to"], auth.FULL)["see"]:
        row["reports_to"] = ""
    if not level["read"]:
        return {k: v for k, v in row.items() if k in SEE_ONLY}
    row["task_flow"] = auto_tasks.counts(c, bot["slug"], who)
    row["draining"] = bool(c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot["slug"],)).fetchone())
    scope = execution.claim_scope(c, bot["slug"])
    row["claim_scoped"] = bool(scope)
    row["claim_task"] = scope if scope and privacy.task_readable(c, who, H.task(c, scope)) else None
    row["status"] = privacy.status(c, who, H.status(c, bot["slug"]))
    row["assignment"] = dict(assignment) if assignment else None
    row["online"] = bool(assignment and not assignment["revoked_at"] and assignment["last_seen_at"]
                         and assignment["last_seen_at"] > H.shift(H.now(), seconds=-60))
    row["queued"] = privacy.job_count(c, who, bot["slug"])
    row["next_run"] = sum(privacy.task_readable(c, who, t) for t in H.next_run_tasks(c, bot["slug"]))
    row["notes"] = len(H.notes_waiting(c, bot["slug"], limit=500))
    external = agents.presence(c, bot["slug"])
    row["agent"] = external["agent"] if external else None
    from backend import app_delivery
    row["app_delivery"] = app_delivery.view(c, declared) if config and external else None
    row["app_delivery_app"] = app_delivery.HARNESS_APP.get(row.get("harness"))
    if external and not (row["app_delivery"] or {}).get("active"):
        row["online"] = external["online"]
    return row


def per_bot_list(api, token):
    app = api.app
    settings, auth, who = app.state.store.settings, app.state.auth, app.state.store.settings.test_identities[token]
    with app.state.store.read() as c:
        roster, entries = views.roster(c), views.entries(c, settings.github_owner)
        access = auth.bot_accesses(c, who)
        out = []
        for bot in H.bots(c):
            level = access.get(bot["slug"], auth.FULL)
            if not level["see"] or bot.get("state") == "archived":
                continue
            out.append({**per_bot_view(c, settings, auth, bot, level, access, roster, entries, who),
                        "goal_active": readable_active(c, auth, who, bot["slug"])})
        return json.loads(json.dumps(out))


def add_bots(api, extra):
    with api.app.state.store.transaction() as c:
        bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in (*BASE, *extra)}
        H.sync_registry(c, bots, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for slug, config in extra.items():
            c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES(?,?,?,?)",
                      (slug, encode({**bots[slug], **config}), None, "ana"))


def test_bot_list_answers_as_read_one_bot_at_a_time(api):
    """Private tasks, queued jobs, notes, statuses, approvals, questions, a branch, an external agent,
    a bot one member may only see and a manager another may not see: each caller's list is the same."""
    T.as_member(api, "ben@acme.example")
    add_bots(api, {"ops-ben": {"shared_from": "ops", "model": "m1"}, "scout": {"harness": "hermes"}})
    store = api.app.state.store
    with store.transaction() as c:
        T.restrict(c, "cpo", see={"people": ["ana", "cara"]}, read={"people": ["ana"]}, write={"people": ["ana"]})
        c.execute("UPDATE bot_config SET reports_to='inbox' WHERE bot='doc-updater'")
        c.execute("UPDATE bot_config SET owner_ids=? WHERE bot='finance'", (encode(["ben", "nobody"]),))
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'",
                  (encode({"name": "ops", "runtime": "fake", "private_tasks_default": True, "model": "m2"}),))
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('finance',1)")
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by,last_seen_at) VALUES(?,?,?,?,?,?)",
                  ("scout", "hermes", "h", H.now(), "human:ana", H.now()))
    r = T.runner(api)
    for bot in ("ops", "finance"):
        T.assign(api, r, bot)
    T.ready(api, r, ["ops", "finance"])
    secret = T.post(api, "tasks", {"owner_id": "ops", "title": "Secret", "body": "x", "private": True})
    T.post(api, "tasks", {"owner_id": "finance", "title": "Open", "body": "y"})
    bens = T.post(api, "tasks", {"owner_id": "finance", "title": "Ben's", "body": "z", "private": True}, token="ben-test")
    T.post(api, "chat/ops", {"text": "hello"})
    T.post(api, "chat/finance", {"text": "hi"}, token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE tasks SET next_run=1,status='open' WHERE id IN (?,?)", (secret["id"], bens["id"]))
        for bot, to in (("ops", "ops"), ("finance", "finance"), ("ops", "finance")):
            c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
                      (H.new_id(), "human:ana", "bot:" + to, "note from " + bot, H.now()))
        c.execute("INSERT INTO bot_status(bot,state,focus,task_id,last_result,open_tasks,needs_human) "
                  "VALUES('ops','running','the secret',?,'done',1,1)", (secret["id"],))
        c.execute("INSERT INTO bot_status(bot,state,focus,open_tasks,needs_human) VALUES('finance','idle','',2,2)")
        cid = c.execute("SELECT conversation_id FROM messages WHERE recipient_id='bot:ops' LIMIT 1").fetchone()[0]
        for kind, refs in (("ask", {"task": bens["id"], "questions": []}), ("notice", {"task": bens["id"]})):
            mid = H.new_id()
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (mid, cid, "bot:finance", "human:ben", kind, "?", encode(refs), H.now()))
            if kind == "notice":
                c.execute("INSERT INTO approvals(id,kind,task_id,message_id,requested_by_id,created_at) VALUES(?,?,?,?,?,?)",
                          (H.new_id(), "spend", bens["id"], mid, "bot:finance", H.now()))
        c.execute("INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
                  "VALUES('g1',?,'ops','Ship it','active','human:ana',?,?)", (cid, H.now(), H.now()))
    for token in ("ana-test", "ben-test", "cara-test"):
        listed = T.get(api, "bots", token=token)
        assert listed == per_bot_list(api, token), token
        for row in listed:
            detail = T.get(api, "bots/" + row["slug"], token=token)
            assert {k: detail[k] for k in row if k != "goal_active"} == {k: v for k, v in row.items() if k != "goal_active"}
    cara = {row["slug"]: row for row in T.get(api, "bots", token="cara-test")}
    assert "inbox" not in cara and cara["doc-updater"]["reports_to"] == ""
    assert set(cara["cpo"]) - {"goal_active"} <= set(SEE_ONLY)
    assert cara["ops"]["status"]["focus"] == "" and cara["ops"]["next_run"] == 0
    ana = {row["slug"]: row for row in T.get(api, "bots", token="ana-test")}
    assert ana["ops"]["status"]["focus"] == "the secret" and ana["ops"]["goal_active"] and ana["scout"]["agent"]


def statements(api, method, path, token, body=None):
    flight = api.app.state.flight
    flight.drain()
    flight.samples.clear()
    response = api.request(method, "/api/v2/" + path, json=body, headers=T.headers(token))
    assert response.status_code == 200, response.text
    return sum(1 for sample in list(flight.samples) if sample[0] == "q")


def costs(api, extra):
    add_bots(api, {slug: {} for slug in extra})
    r = T.runner(api)
    hosted = ["ops", "finance", *extra]
    for bot in hosted:
        T.assign(api, r, bot)
    heartbeat = {"version": "test", "platform": "test", "readiness": {bot: True for bot in hosted}}
    T.post(api, "runners/heartbeat", heartbeat, token=r["token"])
    for bot in ("ops", "finance"):
        T.post(api, "chat/" + bot, {"text": "hello"})
    return collections.Counter({
        "bots": statements(api, "GET", "bots", "ana-test"),
        "bots (member)": statements(api, "GET", "bots", "cara-test"),
        "heartbeat": statements(api, "POST", "runners/heartbeat", r["token"], heartbeat),
        "repositories": statements(api, "GET", "runners/me/repositories", r["token"]),
        "assignments": statements(api, "GET", "runners/assignments", r["token"])})


def test_hot_reads_do_not_grow_with_bots(tmp_path, monkeypatch):
    """A request reads each kind of row once for all bots: 30 bots cost what 7 do, give or take a few."""
    from backend import flight
    from backend.config import Settings
    init = Settings.__init__

    def recording(self, *args, **kwargs):
        kwargs.setdefault("flight_recorder", True)
        init(self, *args, **kwargs)
    monkeypatch.setattr(Settings, "__init__", recording)
    # Count the raw SQL samples before their background consumer can drain or flush them.
    # The thread still starts and stops normally; only its work is paused for this measurement.
    monkeypatch.setattr(flight.Flight, "run", lambda self: self.halt.wait())
    measured = []
    for name, extra in (("few", []), ("many", [f"b{i:02d}" for i in range(23)])):
        (tmp_path / name).mkdir()
        install = T.api.__wrapped__(tmp_path / name, None)
        try:
            measured.append(costs(next(install), extra))
        finally:
            install.close()
    few, many = measured
    assert all(few[k] > 0 and many[k] > 0 for k in few), (few, many)
    assert all(many[k] - few[k] <= 4 for k in few), (few, many)


def open_work(api, slugs):
    """Each bot owns two open tasks, one private, and has a status line, a goal and a claim limited to its task."""
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            first = None
            for k in range(2):
                task = H.task_create(c, "human:ana", f"Work {slug} {k}", "Do it.", "bot:" + slug, private=k == 1)
                first = first or task
            H.status_set(c, "bot:" + slug, slug, state="idle", focus="Working", task_id=first["id"])
            c.execute("INSERT INTO bot_control(bot,draining,claim_task) VALUES(?,?,?)", (slug, 0, task["id"]))
            c.execute("INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
                      "SELECT ?,conversation_id,?,'Ship it','active','human:ana',?,? FROM tasks WHERE id=?",
                      ("g-" + slug, slug, H.now(), H.now(), first["id"]))


def list_statements(api, monkeypatch, token="ana-test"):
    """The bot list as `token` gets it, and the statements it took. Each request starts from cold roster and row caches."""
    import contextlib
    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get("/api/v2/bots", headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_bot_list_statements_do_not_grow_with_bots_that_have_open_tasks(api, monkeypatch):
    """Five bots and forty bots, each with open tasks, asks and a private task: the list reads each table once for all
    of them, so the forty cost what the five do. Per bot, the waiting checks and the step names were read one bot at a time."""
    small = [f"s{n:02d}" for n in range(5)]
    add_bots(api, {slug: {} for slug in small})
    open_work(api, small)
    few_rows, few = list_statements(api, monkeypatch)
    big = [f"b{n:02d}" for n in range(35)]
    add_bots(api, {slug: {} for slug in big})
    open_work(api, big)
    many_rows, many = list_statements(api, monkeypatch)
    assert len(many_rows) == len(few_rows) + 35 and {row["slug"] for row in many_rows} >= set(small + big)
    assert few > 0 and many <= few + 2, (few, many)


def test_status_task_read_seeks_each_bots_tasks_on_a_big_task_table(api):
    """A long task history must not make the planner scan every task for the bots' `owner IN (...)` and
    `requester IN (...)` parts (on TIDY v0.5.1, which has no covering task indexes, that scan took 33 ms a read)."""
    from backend.task_privacy import status_tasks_sql
    store = api.app.state.store
    bots = [f"t{n:02d}" for n in range(60)]
    add_bots(api, {slug: {} for slug in bots})
    with store.transaction() as c:
        c.executemany("INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "Old", "Done.", "human:ana", "bot:" + bots[n % 60],
                        "done" if n % 9 else "open", H.now(), H.now(), 0) for n in range(4000)])
        c.execute("ANALYZE")
    marks = ",".join("?" * len(bots))
    with store.read_transaction() as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + status_tasks_sql(f" IN ({marks})"),
                                            [*("bot:" + b for b in bots)] * 4)]
    assert any(line.startswith("SEARCH tasks USING") and "tasks_owner_status_cover" in line for line in plan), plan
    assert any(line.startswith("SEARCH tasks USING") and "tasks_requester_status_cover" in line for line in plan), plan
    assert not [line for line in plan if line.startswith("SCAN tasks") and "tasks_private_" not in line], plan


def test_status_private_tasks_are_read_from_an_index_holding_their_status(api):
    """The private tasks of a bot's status rows come from tasks_private_status, which holds the columns status reads
    (status, waiting_on). On tasks_private_parties each private row was a table read for them, its body's pages
    included: on a multi-GB hub that was most of a status page's time, as random reads."""
    from backend.task_privacy import status_tasks_sql
    store = api.app.state.store
    bots = [f"t{n:02d}" for n in range(60)]
    add_bots(api, {slug: {} for slug in bots})
    with store.transaction() as c:
        c.executemany("INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "Old", "Done.", "human:ana", "bot:" + bots[n % 60],
                        "done" if n % 9 else "open", H.now(), H.now(), int(n % 5 == 0)) for n in range(4000)])
        c.execute("ANALYZE")
    marks = ",".join("?" * len(bots))
    with store.read_transaction() as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + status_tasks_sql(f" IN ({marks})"),
                                            [*("bot:" + b for b in bots)] * 4)]
    assert any("COVERING INDEX tasks_private_status" in line for line in plan), plan
    assert not any("tasks_private_parties" in line for line in plan), plan
