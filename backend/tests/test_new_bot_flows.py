"""A bot made after bots got stable ids (a UUIDv7 id, a slug that is only a label) works end to end, by id and by
slug, and keeps working after it is renamed (backend/hubdb.py resolve_bot, rename_bot; docs/how-it-works.md).

Every surface that names a bot is driven through the API the way its client drives it: the web app's routes, the
runner's claim, the MCP tool table and the `hub` CLI (clients/hubtools.py, clients/remotecli.py). Stored references
must hold the id; reads give both the id and the slug.
"""
import json
import re
import uuid

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, runner  # noqa: F401  (fixture)
from backend.tests.test_automation_cli import Client
from clients import hubcli, hubtools, remotecli


def create_bot(api, slug, token="ana-test", **fields):
    body = {"slug": slug, "display_name": slug.replace("-", " ").title(), "description": "Answers sales questions",
            "status": "active", **fields}
    r = api.post("/api/v2/bots", json=body, headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        bot_id = H.resolve_bot(c, slug)["id"]
        # It runs on the test runtime the fixture's bots run on, so the test computer can claim its work.
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot_id,)).fetchone()[0])
        config.update({"runtime": "fake", "harness": None, "model": "", "model_managed_by": None})
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (json.dumps(config), bot_id))
    return bot_id


def bot_token(api, bot_id):
    """A test token that acts as the bot (an external agent's identity), as bot:<id>."""
    from backend.auth import Identity
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (bot_id, "test", "x-" + bot_id, H.now(), "human:ana"))
    token = "bot-" + bot_id + "-test"
    api.app.state.auth.settings.test_identities[token] = Identity(H.bot_actor(bot_id), "bot", agent="test")
    return token


def tool(api, token, name, args):
    return hubtools.BY_NAME[name]["fn"](Client(api, token), args)


def rename(api, bot, slug, token="ana-test", expected=200):
    revision = get(api, f"bots/{bot}")["revision"]
    r = api.post(f"/api/v2/bots/{bot}/definition", json={"slug": slug, "expected_revision": revision},
                 headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def ready(api, machine, bot_ids):
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                "readiness": {bot: True for bot in bot_ids}}, token=machine["token"])


def finish(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done", "last_seq": 0},
         token=machine["token"])


def column(api, sql, *args):
    with api.app.state.store.read() as c:
        return [row[0] for row in c.execute(sql, args)]


@pytest.fixture
def sales(api):
    """`sales-helper`, made through the API as an owner would, so its id is a UUIDv7."""
    bot_id = create_bot(api, "sales-helper")
    assert uuid.UUID(bot_id).version == 7 and bot_id != "sales-helper"
    return bot_id


# ----------------------------------------------------------------------------- reads
def test_every_read_names_a_new_bot_by_its_id_and_its_slug(api, sales):
    for ref in (sales, "sales-helper", "bot:" + sales):
        row = get(api, "bots/" + ref)
        assert (row["id"], row["slug"]) == (sales, "sales-helper"), ref
    listed = next(b for b in get(api, "bots") if b["id"] == sales)
    assert listed["slug"] == "sales-helper"
    employee = next(e for e in api.get("/api/employees", headers=headers()).json() if e["id"] == sales)
    assert (employee["slug"], employee["name"]) == ("sales-helper", "sales-helper")
    org = next(b for b in get(api, "org")["bots"] if b["id"] == sales)
    assert (org["slug"], org["name"]) == ("sales-helper", "sales-helper")


def test_every_bot_route_answers_the_same_by_slug_and_by_id(api, sales):
    """A read route that names a bot in its path finds it by either (a route that forgot resolve_bot answers 404
    for the slug of a bot whose id is not its slug)."""
    differ, checked = [], 0
    for route in api.app.routes:
        path = getattr(route, "path", "")
        if "{bot}" not in path or "GET" not in (getattr(route, "methods", None) or ()):
            continue
        codes = [api.get(re.sub(r"\{[^}]+\}", "x", path.replace("{bot}", ref)), headers=headers()).status_code
                 for ref in ("sales-helper", sales)]
        checked += 1
        if codes[0] != codes[1]:
            differ.append((path, codes))
    assert checked > 20 and not differ, differ


# ----------------------------------------------------------------------------- chat and the runner
def test_chat_by_slug_or_id_reaches_the_bot_and_its_runner_claims_it_by_either(api, sales):
    machine = runner(api)
    assign(api, machine, "sales-helper")
    assert column(api, "SELECT bot FROM assignments WHERE runner_id=?", machine["runner_id"]) == [sales]
    ready(api, machine, [sales])
    first = post(api, "chat/sales-helper", {"text": "What did we sell?"})
    assert first["recipient_id"] == "bot:" + sales
    attempt = claim(api, machine, "sales-helper")
    assert attempt["bot_id"] == sales
    finish(api, machine, attempt)
    second = post(api, "chat/" + sales, {"text": "And this week?"})
    assert second["recipient_id"] == "bot:" + sales
    attempt = claim(api, machine, sales)
    assert attempt["bot_id"] == sales
    finish(api, machine, attempt)
    # the jobs the runner claimed are the bot's, by id
    assert set(column(api, "SELECT bot_id FROM jobs")) <= {sales, "coo", "ops", "cpo", "product-design", "finance",
                                                       "inbox", "doc-updater"}


def test_mcp_and_cli_chat_name_the_bot_by_slug(api, sales):
    sent = tool(api, "ana-test", "hub_chat_send", {"to": "sales-helper", "text": "Hello"})
    assert json.dumps(sent).count("bot:" + sales) >= 1
    assert column(api, "SELECT recipient_id FROM messages WHERE body='Hello'") == ["bot:" + sales]


# ----------------------------------------------------------------------------- tasks
def test_tasks_are_assigned_taken_and_closed_by_slug_or_id(api, sales):
    by_slug = post(api, "tasks", {"owner_id": "sales-helper", "title": "Draft the price sheet", "body": "For Acme."})
    by_actor = post(api, "tasks", {"owner_id": "bot:sales-helper", "title": "Call the lead", "body": "Today."})
    by_id = post(api, "tasks", {"owner_id": sales, "title": "Send the deck", "body": "To Acme."})
    assert {by_slug["owner_id"], by_actor["owner_id"], by_id["owner_id"]} == {"bot:" + sales}
    # handed to another bot and back, by slug
    moved = post(api, "tasks/" + by_slug["id"], {"owner_id": "ops", "version": by_slug["version"]})
    back = post(api, "tasks/" + by_slug["id"], {"owner_id": "sales-helper", "version": moved["version"]})
    assert back["owner_id"] == "bot:" + sales
    # the list filters by either
    for owner in ("sales-helper", "bot:sales-helper", sales, "bot:" + sales):
        ids = {t["id"] for t in get(api, "tasks?owner_id=" + owner)["tasks"]}
        assert {by_slug["id"], by_actor["id"], by_id["id"]} <= ids, owner
    # the bot takes and closes its own task through the MCP tools
    token = bot_token(api, sales)
    made = tool(api, token, "hub_task_create", {"owner_id": "me", "title": "Follow up with Acme", "body": "Next week."})
    assert column(api, "SELECT owner_id FROM tasks WHERE title='Follow up with Acme'") == ["bot:" + sales], made
    own = column(api, "SELECT id FROM tasks WHERE title='Follow up with Acme'")[0]
    tool(api, token, "hub_task_close", {"id": own, "note": "Sent"})
    assert column(api, "SELECT status FROM tasks WHERE id=?", own) == ["closed"]
    tool(api, "ana-test", "hub_task_close", {"id": by_id["id"], "note": "Sent"})
    assert column(api, "SELECT status FROM tasks WHERE id=?", by_id["id"]) == ["closed"]
    listed = tool(api, "ana-test", "hub_task_list", {"owner_id": "sales-helper"})
    rows = listed["tasks"] if isinstance(listed, dict) else listed
    assert by_slug["id"] in {t["id"] for t in rows}


def test_the_hub_cli_files_a_task_for_a_bot_by_slug(api, sales, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "http://test")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: Client(api, "ana-test"))
    args = hubcli.parser().parse_args(["task", "create", "--owner", "sales-helper", "--title", "Price the add-on",
                                       "--body", "For Acme."])
    made = remotecli.run(args)
    owner = column(api, "SELECT owner_id FROM tasks WHERE title='Price the add-on'")
    assert owner == ["bot:" + sales], made


# ----------------------------------------------------------------------------- automations, goals, chart
def test_automations_and_routines_owned_by_the_bot_store_its_id(api, sales):
    flow = {"name": "Morning sales check", "owner": "bot:sales-helper", "enabled": True,
            "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"},
            "steps": [{"id": "s1", "type": "bot_turn", "bot": "sales-helper", "prompt": "Check the pipeline"}]}
    made = tool(api, "ana-test", "hub_automation_create", flow)
    aid = made["id"]
    shown = tool(api, "ana-test", "hub_automation_get", {"id": aid})
    assert shown["owner_id"] == "bot:" + sales
    stored = json.dumps(shown.get("steps") or [])
    assert "sales-helper" not in stored and sales in stored
    # an update through the flow save path resolves the slug too
    updated = tool(api, "ana-test", "hub_automation_update", {
        "id": aid, "steps": [{"id": "s1", "type": "bot_turn", "bot": "bot:sales-helper", "prompt": "Check again"}]})
    stored = json.dumps(tool(api, "ana-test", "hub_automation_get", {"id": aid}).get("steps") or [])
    assert "sales-helper" not in stored and sales in stored, updated


def test_goals_chart_groups_and_caps_name_the_bot_by_slug_and_store_its_id(api, sales):
    goal = tool(api, "ana-test", "hub_goal_create", {"owner_id": "sales-helper", "title": "Close ten deals this month"})
    assert goal.get("owner_id") in ("bot:" + sales, sales), goal
    lead = create_bot(api, "sales-lead")
    tool(api, "ana-test", "hub_org_reports_to", {"teammate": "bot:sales-helper", "reports_to": "sales-lead"})
    assert column(api, "SELECT reports_to FROM bot_config WHERE bot=?", sales) == [lead]
    org = {b["id"]: b for b in get(api, "org")["bots"]}
    assert org[sales]["reports_to"] == lead and org[sales]["org_parent"] == "b:" + lead
    group = tool(api, "ana-test", "hub_group_update", {"name": "Sales", "add_bots": ["sales-helper", sales]})
    gid = group.get("id") or group.get("group", {}).get("id")
    listed = next(g for g in get(api, "groups") if g["id"] == gid)
    assert listed["bots"] == [sales] and listed["bot_slugs"] == ["sales-helper"]
    tool(api, "ana-test", "hub_task_cap_set", {"sender": "bot:sales-helper", "receiver": "bot:sales-lead", "per_day": 5})
    caps = json.dumps(tool(api, "ana-test", "hub_task_caps", {}))
    assert "bot:" + sales in caps and "bot:sales-helper" not in caps


def test_files_and_status_by_slug(api, sales):
    assert api.get("/api/v2/bots/sales-helper/files", headers=headers()).status_code == 200
    listed = tool(api, "ana-test", "hub_file_list", {"bot": "sales-helper"})
    assert listed is not None
    tool(api, "ana-test", "hub_bot_pause", {"bot": "sales-helper"})
    assert column(api, "SELECT draining FROM bot_control WHERE bot=?", sales) in ([1], [True])


# ----------------------------------------------------------------------------- rename
def test_after_a_rename_everything_follows_the_id_and_the_old_slug_is_free(api, sales):
    machine = runner(api)
    assign(api, machine, "sales-helper")
    ready(api, machine, [sales])
    task = post(api, "tasks", {"owner_id": "sales-helper", "title": "Draft the price sheet", "body": "For Acme."})
    post(api, "chat/sales-helper", {"text": "Before the rename"})
    finish(api, machine, claim(api, machine, "sales-helper"))

    renamed = rename(api, "sales-helper", "revenue-helper")
    assert (renamed["id"], renamed["slug"]) == (sales, "revenue-helper")
    assert get(api, "bots/revenue-helper")["id"] == sales
    assert get(api, "bots/" + sales)["slug"] == "revenue-helper"
    get(api, "bots/sales-helper", expected=404)                   # the old slug names nobody now
    # history and references keep the id
    assert column(api, "SELECT owner_id FROM tasks WHERE id=?", task["id"]) == ["bot:" + sales]
    assert column(api, "SELECT recipient_id FROM messages WHERE body='Before the rename'") == ["bot:" + sales]
    assert column(api, "SELECT target FROM events WHERE action='bot.renamed'") == [sales]
    # chat, claim, tasks and tools work by the new slug and by the id
    post(api, "chat/revenue-helper", {"text": "After the rename"})
    assert claim(api, machine, "revenue-helper")["bot"] == sales
    assert post(api, "tasks", {"owner_id": "revenue-helper", "title": "New sheet", "body": "x"})["owner_id"] == "bot:" + sales
    lost = api.post("/api/v2/tasks", json={"owner_id": "sales-helper", "title": "Lost", "body": "x"}, headers=headers())
    assert lost.status_code in (404, 422), lost.text                # the old slug takes no task
    tool(api, "ana-test", "hub_task_create", {"owner_id": sales, "title": "By id", "body": "x"})
    tool(api, "ana-test", "hub_task_create", {"owner_id": "revenue-helper", "title": "By new slug", "body": "x"})
    assert column(api, "SELECT DISTINCT owner_id FROM tasks WHERE title IN ('By id','By new slug')") == ["bot:" + sales]
    # the roster shows the new label on the same id
    employee = next(e for e in api.get("/api/employees", headers=headers()).json() if e["id"] == sales)
    assert employee["slug"] == "revenue-helper"
    # a new bot may take the old slug, and is a different bot
    newcomer = create_bot(api, "sales-helper")
    assert newcomer != sales and get(api, "bots/sales-helper")["id"] == newcomer


def test_no_bot_takes_a_slug_that_is_another_bots_id(api, sales):
    r = api.post("/api/v2/bots", json={"slug": sales, "display_name": "Imposter", "status": "planned"},
                 headers=headers())
    assert r.status_code == 409, r.text
    other = create_bot(api, "sales-lead")
    rename(api, other, sales, expected=409)
    rename(api, other, "sales-helper", expected=409)             # nor another bot's slug


def test_a_built_in_bot_keeps_its_slug(api):
    rename(api, "coo", "chief-ops", expected=422)
    assert get(api, "bots/coo")["slug"] == "coo"


def test_mcp_and_the_hub_cli_rename_a_bot_by_id_or_slug(api, sales, monkeypatch):
    tool(api, "ana-test", "hub_bot_update", {"slug": sales, "new_slug": "revenue-helper"})
    assert get(api, "bots/" + sales)["slug"] == "revenue-helper"
    monkeypatch.setenv("HUB_API_URL", "http://test")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: Client(api, "ana-test"))
    remotecli.run(hubcli.parser().parse_args(["bot", "update", "revenue-helper", "--new-slug", "pipeline-helper"]))
    assert get(api, "bots/" + sales)["slug"] == "pipeline-helper"
    config = json.loads(column(api, "SELECT config FROM bot_config WHERE bot=?", sales)[0])
    assert config["folder"] == "bot-sales-helper"                  # its folder keeps the name it was made with


def config_of(api, bot_id):
    return json.loads(column(api, "SELECT config FROM bot_config WHERE bot=?", bot_id)[0])


def test_a_new_bot_given_a_renamed_bots_old_slug_gets_a_folder_and_repository_of_its_own(api, sales):
    # sales-helper is renamed but keeps its folder; a new sales-helper must not land in that checkout (both bots would
    # run in, and commit to, the same directory on a computer).
    rename(api, sales, "revenue-helper")
    newcomer = create_bot(api, "sales-helper")
    first, second = config_of(api, sales), config_of(api, newcomer)
    assert first["folder"] == "bot-sales-helper"
    assert second["folder"] != first["folder"] and second["folder"].startswith("bot-sales-helper-")
    assert second["folder"].removeprefix("bot-sales-helper-") in newcomer.replace("-", "")
    assert get(api, "bots/sales-helper")["folder"] == second["folder"]      # clients read the folder, not the slug
    assert get(api, "bots/revenue-helper")["folder"] == "bot-sales-helper"
    # its repository is named alike, so GitHub never hands it the renamed bot's history
    repos = column(api, "SELECT repo FROM bot_config WHERE bot IN (?,?)", sales, newcomer)
    assert len(set(repos)) == 2
    with api.app.state.store.read() as c:
        assert H.bot_repository_name(c, "sales-helper") == second["folder"]
        assert H.bot_repository_name(c, "revenue-helper") == "bot-sales-helper"
        assert H.bot_repository_name(c, "coo") == "bot-coo"             # an older bot keeps bot-<slug>
    # a third one, and a folder an older bot (id = slug) has, are taken too
    third = create_bot(api, "sales-helper-2")
    assert config_of(api, third)["folder"] == "bot-sales-helper-2"
    with api.app.state.store.read() as c:
        assert H.free_bot_folder(c, "coo", "01a19806-0460-77a7-860b-0c1d9f3c2a1b") == "bot-coo-9f3c2a1b"


def test_two_bots_with_one_folder_are_never_placed_on_one_computer(api, sales):
    # A bot whose recorded folder another bot on that computer already works in (data made before folders were kept
    # unique) is refused there: the runner would resolve both to the same checkout.
    machine = runner(api)
    assign(api, machine, sales)
    rename(api, sales, "revenue-helper")
    newcomer = create_bot(api, "sales-helper", status="planned")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.folder','bot-sales-helper') WHERE bot=?", (newcomer,))
    r = api.post(f"/api/v2/bots/{newcomer}/assignment", json={"runner_id": machine["runner_id"], "expected_generation": 0},
                 headers=headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "folder_taken", r.text
    assert column(api, "SELECT count(*) FROM assignments WHERE bot=?", newcomer) == [0]
    other = runner(api, label="Other Mac")
    assign(api, other, newcomer)                                        # another computer has its own folder
