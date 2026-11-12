"""A bot's own GitHub repository is only its history's backup. When the App is installed on chosen repositories only,
or another company already has the name, nothing is made or adopted, the bot works from its computer, no token or
push touches the repository, and Health says so in one plain line (repositories.home)."""

from datetime import timedelta

from backend import hubdb as H
from backend import repositories as R
from backend.execution import runner_repository
from backend.tests.helpers.github_app import api, auth, botops_turn, connect, gh, runner_token, turn_token  # noqa: F401


def _sync(api, gh, **extra):
    gh.repositories = [{"full_name": "Acme/" + name, "default_branch": "main", **extra.get(name, {})} for name in extra]
    response = api.post("/api/v2/repositories/refresh", headers=auth())
    assert response.status_code == 200, response.text


def _ready(api, gh, selection="all"):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    gh.selection = selection
    botops_turn(api)


def _create(api, slug="newbie"):
    return api.post("/api/v2/github/repos", json={"slug": slug, "empty": True}, headers=auth("botops-test"))


def _history_checks(api):
    checks = api.get("/api/v2/health", headers=auth()).json()["checks"]
    return [c for c in checks if c["id"] in ("bot_history", "publish")]


def _repository(api, bot):
    state = api.app_state
    with state.store.read() as c:
        return runner_repository(c, state.store.settings, bot)


def test_chosen_repositories_only_makes_nothing_and_the_bots_keep_working_without_github(api, gh):
    _ready(api, gh, selection="selected")
    gh.selected_repositories = {"pipeline-sandbox"}
    _sync(api, gh, **{"pipeline-sandbox": {}})
    made = _create(api)
    error = made.json()["error"]
    assert made.status_code == 409 and error["code"] == "github_selected_repos", made.text
    assert "Nobody needs to act" in error["detail"]
    assert not [c for c in gh.calls if c[0] == "POST" and (c[1].startswith("/orgs/") or c[1].endswith("/generate"))]
    with api.app_state.store.read() as c:
        assert c.execute("SELECT value FROM registry_metadata WHERE key='bot-built-on:newbie'").fetchone(), (
            "its first placement is still the computer that built it"
        )
        assert not c.execute("SELECT 1 FROM events WHERE action='github.repo_created'").fetchone()
    # Its turns get no token, no refusal, and no repository to fetch from or push to.
    runner_token(api, "cpo")
    minted = len(gh.of("/access_tokens"))
    token = turn_token(api, "cpo")
    assert token.status_code == 200, token.text
    assert token.json()["token"] == "" and token.json()["repository"] == ""
    assert token.json()["history"] == R.HOME_WORDS[R.CHOSEN_ONLY]
    assert len(gh.of("/access_tokens")) == minted
    assert _repository(api, "cpo") == ""
    # One plain line for all of them, and no per-bot warning, however many bots there are.
    checks = _history_checks(api)
    assert checks == [
        {
            "id": "bot_history",
            "label": "Bot history",
            "status": "info",
            "summary": R.HOME_WORDS[R.CHOSEN_ONLY] + ".",
            "fixes": [],
        }
    ]


def test_a_chosen_repository_the_app_reaches_stays_the_bots_own(api, gh):
    _ready(api, gh, selection="selected")
    gh.selected_repositories = {"emp-cpo"}
    _sync(api, gh, **{"emp-cpo": {}})
    runner_token(api, "cpo")
    token = turn_token(api, "cpo")
    assert token.status_code == 200 and token.json()["token"].startswith("ghs_"), token.text
    assert token.json()["repository"] == "Acme/emp-cpo" and "history" not in token.json()
    assert _repository(api, "cpo") == "Acme/emp-cpo"


def test_a_name_another_company_took_is_never_adopted_and_a_retry_of_our_own_is(api, gh):
    _ready(api, gh)
    with api.app_state.store.transaction() as c:  # as registration names it ("bot-" + slug)
        c.execute("UPDATE bot_config SET repo='bot-newbie' WHERE bot='newbie'")
    gh.taken = {"Acme/bot-newbie"}
    taken = _create(api)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "github_repo_taken", taken.text
    with api.app_state.store.read() as c:
        assert R.bot_home(c, "newbie", "Acme/bot-newbie") == R.NOT_OURS
        assert c.execute("SELECT value FROM registry_metadata WHERE key='bot-built-on:newbie'").fetchone()
    assert _repository(api, "newbie") == ""
    checks = _history_checks(api)
    assert [c["id"] for c in checks] == ["bot_history"]
    assert "a repository with the bot's name was already there" in checks[0]["summary"]
    # One this company's App made is its own: a retry after a lost answer is that repository, not a refusal.
    gh.taken = set()
    assert _create(api, "cmo").status_code == 200
    gh.taken = {"Acme/bot-cmo"}
    again = _create(api, "cmo")
    assert again.status_code == 200 and again.json()["existing"] is True, again.text
    assert again.json()["repository"] == "Acme/bot-cmo"
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='github.repo_created'").fetchone()[0] == 1


def test_a_repository_github_made_before_the_bot_is_someone_elses_until_a_person_points_the_bot_at_it(api, gh):
    _ready(api, gh)
    with api.app_state.store.read() as c:
        born = H.parse_ts(c.execute("SELECT created_at FROM bots WHERE id='cpo'").fetchone()[0])
    old = (born - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    just = (born + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _sync(api, gh, **{"emp-cpo": {"created_at": old}, "emp-cmo": {"created_at": just}})
    assert _repository(api, "cpo") == "" and _repository(api, "cmo") == "Acme/emp-cmo"
    runner_token(api, "cpo")
    token = turn_token(api, "cpo")
    assert token.status_code == 200 and token.json()["repository"] == "", token.text
    assert "emp-cpo" not in [r.split("/")[-1] for r in token.json()["repositories"]]
    with api.app_state.store.transaction() as c:
        R.note_chosen(c, "emp-cpo")  # what Settings > Bots and `manage set-repo` record
    assert _repository(api, "cpo") == "Acme/emp-cpo"
    assert turn_token(api, "cpo").json()["repository"] == "Acme/emp-cpo"
