"""Copy a bot and copy a skill, done by BotOps as the person who asked (backend/bot_copy.py, clients/botcopy.py).

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). A copy is an ordinary bot its requester owns; the
server decides and records, BotOps's computer moves the files.
"""
import json
import subprocess

import pytest

from backend.credentials import effective_grant
from backend import built_on
from backend.store import H
from backend.tests.test_api import api, get, headers, post, restrict, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act, bot_id
from backend.tests.test_credential_sharing import jira, local
from backend.tests.test_member_bots import botops, call, turn  # noqa: F401  (fixture)
from clients import botcopy, hubtools
from clients.tico import APIError

SHA = "a" * 40


@pytest.fixture
def scribe(api):
    """Ana's bot, open to everyone to read and write."""
    return post(api, "bots", {"slug": "scribe", "display_name": "Scribe", "description": "Takes meeting notes", "status": "planned",
                              "model": "gpt-6.1-sol", "effort": "high", "harness": None, "runner_id": None})


def copy(api, body=None, token="cara-test", bot="scribe", expected=200):
    r = api.post(f"/api/v2/bots/{bot}/copy", json={"sha": SHA, **(body or {})}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def config_of(api, slug):
    bot = bot_id(api, slug)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
        return dict(row), json.loads(row["config"])


# ------------------------------------------------------------------ the copy
def test_a_copy_is_an_independent_bot_owned_by_whoever_asked(api, scribe):
    made = copy(api, {"slug": "cara-scribe", "display_name": "Cara's Scribe"})
    assert made["slug"] == "cara-scribe" and made["status"] == "planned" and made["bot_owners"] == ["cara"]
    scribe_id = bot_id(api, "scribe")
    assert made["repo"] == "bot-cara-scribe" and made["copied_from"] == {"bot": scribe_id, "sha": SHA}
    row, config = config_of(api, "cara-scribe")
    assert (row["created_by"], row["owner_id"], row["reports_to"]) == ("human:cara", "cara", "human:cara")
    assert row["description"] == "Takes meeting notes" and config["copied_from"] == {"bot": scribe_id, "sha": SHA}
    assert (config["model"], config["reasoning_effort"]) == ("gpt-6.1-sol", "high")         # the original's model settings
    # Nothing is linked: the original is as it was, and the copy is a bot of its own (its owner may change it, Ana's original she may not).
    assert config_of(api, "scribe")[0]["owner_id"] == "ana" and "copied_from" not in config_of(api, "scribe")[1]
    current = get(api, "bots", "cara-test")
    revision = next(b for b in current if b["slug"] == "cara-scribe")["revision"]
    assert post(api, "bots/cara-scribe/definition", {"display_name": "Renamed", "expected_revision": revision}, "cara-test")
    post(api, "bots/scribe/definition", {"display_name": "Mine", "expected_revision": 1}, "cara-test", expected=403)
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail FROM events WHERE action='bot.copied'").fetchone()
        assert event["actor"] == "human:cara" and json.loads(event["detail"])["from"] == scribe_id
    # Left out, the slug and the name follow the original; asking again makes another.
    again = copy(api)
    assert again["slug"] == "scribe-copy" and again["display_name"] == "Copy of Scribe" and copy(api)["slug"] == "scribe-copy-2"
    copy(api, {"slug": "cara-scribe"}, expected=409)                                            # a slug someone holds


def test_who_may_copy_what_and_how_many(api, botops, scribe):
    scribe_id = bot_id(api, "scribe")
    with api.app.state.store.transaction() as c:
        restrict(c, scribe_id, see={"everyone": True}, read={"people": ["ana"]}, write={"people": ["ana"]})
    # Cara may see it but not read it: read is the right a copy needs.
    assert copy(api, expected=403)["error"]["code"] == "forbidden"
    copy(api, token="ben-test", expected=200)                                                    # an admin reads every bot
    restrict_open = {"see": {"everyone": True}, "read": {"everyone": True}, "write": {"everyone": True}}
    with api.app.state.store.transaction() as c:
        restrict(c, scribe_id, **restrict_open)
    copy(api, {"slug": "botops-copy"}, bot="botops", expected=409)                                # the company's own bots are not copied
    copy(api, bot="nobody", expected=404)
    # It counts toward the requester's limit like any bot they add; a member switched off from adding bots may not copy.
    assert call(api, "put", "access/limits", "ben-test", {"member_bot_limit": 1}).status_code == 200
    copy(api)
    assert copy(api, expected=409)["error"]["code"] == "bot_limit"
    assert call(api, "post", "access/people/cara", "ben-test", {"create_bots": False}).status_code == 200
    copy(api, expected=403)


def test_a_credential_the_original_holds_is_granted_only_by_a_credential_administrator(api, scribe):
    local(api)
    stored = jira(api)
    post(api, f"credentials/{stored['id']}/grants", {"subject": "bot:scribe"})
    tools = [{"service": "jira", "env": "JIRA_BASIC_AUTH"}, {"service": "zoom", "env": "ZOOM_TOKEN"}]
    by_ben = copy(api, {"slug": "ben-scribe", "tools": tools}, token="ben-test")["credentials"]
    assert [(g["env"], g["credential"]) for g in by_ben["granted"]] == [("JIRA_BASIC_AUTH", "Jira")]
    assert [n["needs"] for n in by_ben["needs"]] == ["needs credential ZOOM_TOKEN"]              # the original has none stored
    ben = bot_id(api, "ben-scribe")
    with api.app.state.store.read() as c:
        assert effective_grant(c, stored["id"], H.bot_actor(ben)) and not effective_grant(c, stored["id"], "bot:ben-scribe-2")
    # A member is told who can: nothing is granted, and no value moves.
    by_cara = copy(api, {"slug": "cara-scribe", "tools": tools})["credentials"]
    assert by_cara["granted"] == [] and [n["env"] for n in by_cara["needs"]] == ["JIRA_BASIC_AUTH", "ZOOM_TOKEN"]
    assert "credential administrator" in by_cara["needs"][0]["detail"] and "hunter" not in json.dumps(by_cara)
    cara = bot_id(api, "cara-scribe")
    with api.app.state.store.read() as c:
        assert not effective_grant(c, stored["id"], H.bot_actor(cara))


def placed_on(api, slug):
    bot = bot_id(api, slug)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        return row and row["runner_id"]


def test_a_copy_named_onto_another_computer_is_not_stranded_there(api, botops, scribe):
    """The copy's history is made in BotOps's workspace after the copy route answers: an older BotOps that still names
    `--computer` in the route gets the copy recorded as built on BotOps's computer, and another computer refused."""
    box = runner(api, label="Box")
    attempt = turn(api, botops, text="Copy the scribe onto the box")
    hub = Hub(api, attempt["token"])
    made = hub.call("POST", "bots/scribe/copy", {"sha": SHA, "slug": "cara-scribe", "computer": box["runner_id"]}, delegate=True)
    assert made["placement"]["placed"] is False and "Put it on Test Mac first" in made["placement"]["problem"], made
    assert not placed_on(api, "cara-scribe")
    cara = bot_id(api, "cara-scribe")
    with api.app.state.store.read() as c:
        assert built_on.computer(c, cara)["id"] == botops["runner_id"]
    # Named onto BotOps's own computer, it goes there, which publishes it.
    again = hub.call("POST", "bots/scribe/copy", {"sha": SHA, "slug": "cara-notes", "computer": botops["runner_id"]}, delegate=True)
    assert again["placement"].get("placed") is not False and placed_on(api, "cara-notes") == botops["runner_id"], again
    # A person's own call (not BotOps) records nothing: where the files are made is not known.
    copy(api, {"slug": "cara-own", "computer": box["runner_id"]})
    own = bot_id(api, "cara-own")
    with api.app.state.store.read() as c:
        assert not built_on.computer(c, own)


def test_a_build_record_is_replaced_only_once_its_computer_is_removed(api, botops, scribe):
    box = runner(api, label="Box")
    copy(api, {"slug": "cara-scribe"})
    cara = bot_id(api, "cara-scribe")
    with api.app.state.store.transaction() as c:
        built_on.remember(c, cara, botops["runner_id"])
        built_on.remember(c, cara, box["runner_id"])                              # the first record stands
        assert built_on.computer(c, cara)["id"] == botops["runner_id"]
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", ("2026-10-14T00:00:00Z", botops["runner_id"]))
        built_on.remember(c, cara, box["runner_id"])                              # ...until its computer is removed
        assert built_on.computer(c, cara)["id"] == box["runner_id"]


# ------------------------------------------------------------------ update from the original, and suggesting back
class GitHub:
    """What the GitHub App is asked to do, recorded, for one repository."""
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, **kw):
        self.calls.append((method, path, kw.get("json")))

        class Answer:
            status_code, content = 200, b"{}"

            def json(self_):
                return self_.body
        answer = Answer()
        answer.body = {}
        if path == "/repos/Acme/bot-scribe":
            answer.body = {"default_branch": "main"}
        elif path.endswith("/git/ref/heads/main"):
            answer.body = {"object": {"sha": "f" * 40}}
        elif method == "GET" and path.endswith("/contents/AGENT.md"):
            answer.body = {"sha": "old-sha"}
        elif method == "GET":
            answer.status_code = 404
        elif path.endswith("/git/refs"):
            answer.status_code = 201
        elif path.endswith("/pulls"):
            answer.status_code, answer.body = 201, {"html_url": "https://github.com/Acme/bot-scribe/pull/7"}
        elif method == "PUT" and "/contents/" in path and "sha" not in (kw.get("json") or {}):
            answer.status_code = 201
        return answer


# ------------------------------------------------------------------ a skill
# ------------------------------------------------------------------ BotOps, as the requester
class Hub:
    """The `api` the tools call, as BotOps's runner makes it: its own token, and the requester's header when acting for a person."""
    def __init__(self, api, token):
        self.api, self.token = api, token

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        head = {**headers(self.token, key), **({"X-Tico-On-Behalf-Of": "turn"} if delegate else {})}
        r = self.api.request(method, "/api/v2/" + path, json=body, params=query, headers=head)
        if r.status_code >= 400:
            error = r.json()["error"]
            raise APIError(error["code"], error["detail"], r.status_code)
        return r.json()

    def get(self, path, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body if body is not None else {}, key)


def git(path, *argv):
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.slow
def test_the_tools_copy_update_and_share_a_skill_in_the_workspace_as_the_requester(api, botops, scribe, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    original = workspace / "bot-scribe"
    original.mkdir(parents=True)
    git(original, "init", "-q", "-b", "main")
    files = {"AGENT.md": "# Scribe\n\n## Owns\n- Notes\n\nOne.\nTwo.\nThree.\nFour.\nFive.\n", "state.md": "# State\nprivate\n",
             "bot.yaml": "name: scribe\ndisplay_name: Scribe\nlabels: [owner:scribe]\ntools:\n  - service: jira\n    can: [read]\n    env: JIRA_BASIC_AUTH\n",
             "skills/triage/SKILL.md": "Triage.\n", ".env": "JIRA_BASIC_AUTH=hunter2\n"}
    for rel, text in files.items():
        (original / rel).parent.mkdir(parents=True, exist_ok=True)
        (original / rel).write_text(text)
    git(original, "add", "-A")
    git(original, "commit", "-q", "-m", "Start")
    monkeypatch.setenv("HUB_WORKSPACE", str(workspace))
    attempt = turn(api, botops, text="Copy the scribe for me, and bring it up to date later")
    hub = Hub(api, attempt["token"])
    def tool(tool_name, **args):
        return hubtools.BY_NAME[tool_name]["fn"](hub, args)

    box = runner(api, label="Box")
    made = tool("hub_bot_copy", bot="scribe", slug="cara-scribe", name="Cara's Scribe", computer=box["runner_id"])
    assert made["slug"] == "cara-scribe" and made["copied_from"] == {"bot": bot_id(api, "scribe"), "sha": git(original, "rev-parse", "HEAD")}
    assert [n["needs"] for n in made["credentials"]["needs"]] == ["needs credential JIRA_BASIC_AUTH"]
    assert made["published"] is False and "Only the owner" in made["reason"]              # no GitHub here: it stays on this computer
    # ...so it is not put on another computer that could never get it (it used to be, before the files were made)...
    assert made["placement"]["placed"] is False and "Put it on Test Mac first" in made["placement"]["problem"], made
    assert not placed_on(api, "cara-scribe")
    # ...but named onto BotOps's own computer, the one that holds it, it goes there, GitHub or not.
    mine = tool("hub_bot_copy", bot="scribe", slug="cara-mine", computer=botops["runner_id"])
    assert mine["published"] is False and placed_on(api, "cara-mine") == botops["runner_id"], mine
    copy_dir = workspace / "bot-cara-scribe"
    assert git(copy_dir, "log", "--format=%s") == f"Copied from scribe at {made['from_sha'][:12]}"
    assert not (copy_dir / ".env").exists() and "private" not in (copy_dir / "state.md").read_text()
    assert config_of(api, "cara-scribe")[0]["created_by"] == "human:cara"

    # The original moves on; bringing the copy up to date is its own request, and the server remembers where it is now.
    (original / "AGENT.md").write_text(files["AGENT.md"].replace("Two.", "Two, better."))
    git(original, "commit", "-qam", "Better")
    (copy_dir / "AGENT.md").write_text(files["AGENT.md"].replace("Five.", "Five, mine."))
    git(copy_dir, "commit", "-qam", "Mine")
    updated = tool("hub_bot_update_from_original", bot="cara-scribe")
    assert updated["status"] == "updated" and updated["updated"] == ["AGENT.md"]
    merged = (copy_dir / "AGENT.md").read_text()
    assert "Two, better." in merged and "Five, mine." in merged
    assert config_of(api, "cara-scribe")[1]["copied_from"]["sha"] == git(original, "rev-parse", "HEAD")
    assert tool("hub_bot_update_from_original", bot="cara-scribe")["status"] == "current"

    # No GitHub and no write on the original: the suggestion is a task for its owner.
    scribe_id = bot_id(api, "scribe")
    with api.app.state.store.transaction() as c:
        restrict(c, scribe_id, see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
    suggested = tool("hub_bot_suggest_to_original", bot="cara-scribe")
    assert suggested["how"] == "task" and suggested["files"] == ["AGENT.md"]
    assert tool("hub_bot_suggest_to_original", bot="cara-scribe", paths=["skills"])["suggested"] is False

    # A skill goes to a bot she manages; one she does not is refused before any file moves.
    second = tool("hub_bot_copy", bot="cara-scribe", slug="cara-helper")
    (workspace / "bot-cara-scribe" / "skills" / "minutes").mkdir()
    (workspace / "bot-cara-scribe" / "skills" / "minutes" / "SKILL.md").write_text("Minutes.\n")
    git(workspace / "bot-cara-scribe", "add", "-A")
    git(workspace / "bot-cara-scribe", "commit", "-qm", "A skill")
    shared = tool("hub_skill_copy", skill="minutes", bot="cara-scribe", to=["cara-helper"])
    assert [(r["bot"], r["status"]) for r in shared["to"]] == [("cara-helper", "copied")] and second["slug"] == "cara-helper"
    assert (workspace / "bot-cara-helper" / "skills" / "minutes" / "SKILL.md").read_text() == "Minutes.\n"
    assert git(workspace / "bot-cara-helper", "log", "-1", "--format=%s") == "Copy the minutes skill from cara-scribe"
    with pytest.raises(APIError) as refused:
        tool("hub_skill_copy", skill="triage", bot="scribe", to=["ops"])
    assert refused.value.status == 403 and not (workspace / "bot-ops").exists()
    assert botcopy.head(workspace / "bot-cara-helper") and "botops" in hubtools.offered_to(hubtools.BY_NAME["hub_bot_copy"])
    assert "hub_bot_copy" in {t["name"] for t in hubtools.listing(local=True)} and "hub_bot_copy" not in {t["name"] for t in hubtools.listing()}


def rename(api, slug, new_slug, token="ana-test"):
    revision = get(api, f"bots/{slug}", token)["revision"]
    post(api, f"bots/{slug}/definition", {"slug": new_slug, "expected_revision": revision}, token)


@pytest.mark.slow
def test_renamed_bots_are_found_in_the_folders_they_were_made_with(api, botops, scribe, monkeypatch, tmp_path):
    # A rename keeps a bot's folder (bot-<slug it was made with>). The tools found a bot's checkout from the slug it has
    # now, so after a rename a skill copy answered no_repository, and copying or updating a renamed original failed.
    workspace = tmp_path / "workspace"
    original = workspace / "bot-scribe"
    original.mkdir(parents=True)
    git(original, "init", "-q", "-b", "main")
    for rel, text in {"AGENT.md": "# Scribe\n\nOne.\nTwo.\n", "bot.yaml": "name: scribe\n"}.items():
        (original / rel).parent.mkdir(parents=True, exist_ok=True)
        (original / rel).write_text(text)
    git(original, "add", "-A")
    git(original, "commit", "-q", "-m", "Start")
    monkeypatch.setenv("HUB_WORKSPACE", str(workspace))
    hub = Hub(api, turn(api, botops, text="Copy the scribe, then share its minutes skill")["token"])
    def tool(tool_name, **args):
        return hubtools.BY_NAME[tool_name]["fn"](hub, args)

    rename(api, "scribe", "note-taker")                              # the original, renamed: still in bot-scribe
    made = tool("hub_bot_copy", bot="note-taker", slug="cara-helper")
    assert made["folder"] == "bot-cara-helper" and (workspace / "bot-cara-helper" / "AGENT.md").is_file()
    rename(api, "cara-helper", "cara-assistant", token="cara-test")  # the copy, renamed: still in bot-cara-helper
    (original / "skills" / "minutes").mkdir(parents=True)
    (original / "skills" / "minutes" / "SKILL.md").write_text("Minutes.\n")
    git(original, "add", "-A")
    git(original, "commit", "-q", "-m", "A skill")
    shared = tool("hub_skill_copy", skill="minutes", bot="note-taker", to=["cara-assistant"])
    assert [(r["bot"], r["status"]) for r in shared["to"]] == [("cara-assistant", "copied")], shared
    assert (workspace / "bot-cara-helper" / "skills" / "minutes" / "SKILL.md").read_text() == "Minutes.\n"
    assert not (workspace / "bot-cara-assistant").exists()
    (original / "AGENT.md").write_text("# Scribe\n\nOne.\nTwo, better.\n")
    git(original, "commit", "-qam", "Better")
    assert tool("hub_bot_update_from_original", bot="cara-assistant")["status"] == "updated"
    assert "Two, better." in (workspace / "bot-cara-helper" / "AGENT.md").read_text()
    # A new bot given the renamed copy's first slug is built in a folder of its own, not into that checkout.
    again = tool("hub_bot_copy", bot="note-taker", slug="cara-helper")
    assert again["folder"].startswith("bot-cara-helper-") and (workspace / again["folder"] / "AGENT.md").is_file()
    # The renamed copy's own checkout is untouched: its committed skill is still there.
    assert git(workspace / "bot-cara-helper", "show", "HEAD:skills/minutes/SKILL.md") == "Minutes."


def test_botops_is_given_a_read_only_token_for_a_bot_on_another_computer_only_for_a_person_who_may_read_it(api, botops, scribe, monkeypatch, tmp_path):
    service, minted = api.app.state.github_app, []
    attempt = turn(api, botops, text="Copy the scribe")
    token = lambda who="botops": act(api, attempt, "POST", "bots/scribe/repository-read-token", {})
    assert token().json() == {"configured": False}                                   # GitHub is not connected: nothing to fetch
    monkeypatch.setattr(service, "row", lambda c=None: {"org": "Acme", "administration": 1})
    monkeypatch.setattr(service, "mint", lambda repos, permissions: minted.append((repos, permissions)) or ("ghs_read", "later"))
    granted = token().json()
    assert granted == {"configured": True, "token": "ghs_read", "expires_at": "later", "repository": "Acme/bot-scribe"}
    assert minted == [(["Acme/bot-scribe"], {"contents": "read", "metadata": "read"})]          # one repository, read only
    # Not a person's own call, and not for a bot the requester may not read.
    assert api.post("/api/v2/bots/scribe/repository-read-token", json={}, headers=headers("cara-test")).status_code == 403
    scribe_id = bot_id(api, "scribe")
    with api.app.state.store.transaction() as c:
        restrict(c, scribe_id, people=["ana"])
    assert token().status_code == 404 and len(minted) == 1
    # The client clones with it (the runner's own clone, mocked) into a temporary folder, and says so when it cannot.
    scribe_id = bot_id(api, "scribe")
    with api.app.state.store.transaction() as c:
        restrict(c, scribe_id, see={"everyone": True}, read={"everyone": True}, write={"everyone": True})
    seen = []
    import runner.git_credentials as credentials
    monkeypatch.setattr(credentials, "clone_repository", lambda path, repository, env=None, url=None: seen.append((repository, env["GH_TOKEN"])) or ("failed", "no"))
    with pytest.raises(botcopy.CopyError) as refused:
        botcopy.fetch_repository(botcopy_person(api, attempt), "scribe", tmp_path / "into")
    assert seen == [("Acme/bot-scribe", "ghs_read")] and "ask its owner to publish it" in refused.value.detail


def botcopy_person(api, attempt):
    from clients.hubtools import _Requester
    return _Requester(Hub(api, attempt["token"]))
