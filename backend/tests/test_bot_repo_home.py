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


def _old(api, bot="cpo", days=30):
    with api.app_state.store.read() as c:
        born = H.parse_ts(c.execute("SELECT created_at FROM bots WHERE id=?", (bot,)).fetchone()[0])
    return (born - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_bot_made_with_a_repository_github_already_had_clones_it_and_a_filled_in_default_name_does_not(api, gh):
    """TIDY, Nov 17: video-producer was made with TIDYAPP/emp-video-producer, which already existed. Creation stored it
    but never recorded the choice, so its computer was sent no repository and every turn failed."""
    _ready(api, gh)
    old = _old(api)
    _sync(api, gh, **{"emp-video-producer": {"created_at": old}, "bot-scout": {"created_at": old}})
    body = {"description": "Makes videos", "status": "planned"}
    made = api.post(
        "/api/v2/bots",
        json={**body, "slug": "video-producer", "display_name": "Video Producer", "repo": "Acme/emp-video-producer"},
        headers={**auth(), "Idempotency-Key": "vp"},
    )
    assert made.status_code == 200, made.text
    # The name the Add bot form fills in by itself is not a choice: GitHub had it first, so it may be someone else's.
    scout = api.post(
        "/api/v2/bots",
        json={**body, "slug": "scout", "display_name": "Scout", "repo": "bot-scout"},
        headers={**auth(), "Idempotency-Key": "scout"},
    )
    assert scout.status_code == 200, scout.text
    with api.app_state.store.read() as c:
        producer, scout = H.resolve_bot(c, "video-producer")["id"], H.resolve_bot(c, "scout")["id"]
    assert _repository(api, producer) == "Acme/emp-video-producer"
    assert _repository(api, scout) == ""


def test_the_backfill_records_named_repositories_of_bots_made_before_and_running_it_again_changes_nothing(api, gh):
    import importlib

    migration = importlib.import_module("backend.migrations.20261117210532_named_bot_repositories_chosen")
    _ready(api, gh)
    old = _old(api)
    _sync(api, gh, **{"marketing-site": {"created_at": old}, "emp-cpo": {"created_at": old}})
    with api.app_state.store.transaction() as c:  # as create_bot stored it before it recorded the choice
        c.execute("UPDATE bot_config SET repo='Acme/marketing-site' WHERE bot='cmo'")
        R.note_repository(c, R.TAKEN, "Acme/marketing-site")
    assert _repository(api, "cmo") == "" and _repository(api, "cpo") == ""
    with api.app_state.store.transaction() as c:
        migration.up(c)
    assert _repository(api, "cmo") == "Acme/marketing-site"
    # cpo's emp-cpo is the name Tico gives it unasked: GitHub had it first, so it stays someone else's.
    assert _repository(api, "cpo") == ""
    with api.app_state.store.read() as c:
        assert "acme/marketing-site" not in R.metadata(c, R.TAKEN)
        before = c.execute("SELECT key, value FROM registry_metadata WHERE key LIKE 'bot-repositories-%'").fetchall()
    with api.app_state.store.transaction() as c:
        migration.up(c)
    with api.app_state.store.read() as c:
        after = c.execute("SELECT key, value FROM registry_metadata WHERE key LIKE 'bot-repositories-%'").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def _at(api, bot="cpo", **delta):
    with api.app_state.store.read() as c:
        born = H.parse_ts(c.execute("SELECT created_at FROM bots WHERE id=?", (bot,)).fetchone()[0])
    return (born + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claims(api):
    with api.app_state.store.read() as c:
        return {
            (r[0], r[1], r[2])
            for r in c.execute(
                "SELECT actor, target, json_extract(detail,'$.how') FROM events WHERE action='bot.repository_claimed'"
            )
        }


def test_the_sync_gives_a_moved_bot_its_repository_back_only_when_nothing_says_it_is_someone_elses(api, gh):
    """TIDY, Nov 13-18: its bots were recreated on a new server with their September `TIDYAPP/emp-<slug>`
    repositories. GitHub made those before the bots existed here, so each committed on its computer only."""
    _ready(api, gh)
    with api.app_state.store.transaction() as c:
        # cmo was `marketing` there: its URL names the repository it had under that slug. cpo and newbie are named in
        # full too, cfo bare (as Tico's own forms write it).
        c.execute("UPDATE bot_config SET repo='https://github.com/Acme/emp-marketing' WHERE bot='cmo'")
        H.event(c, "human:ana", "bot.renamed", "cmo", {"from": "marketing", "to": "cmo"})
        c.execute("UPDATE bot_config SET repo='Acme/emp-cpo' WHERE bot='cpo'")
        c.execute("UPDATE bot_config SET repo='emp-cfo' WHERE bot='cfo'")
        c.execute("UPDATE bot_config SET repo='Acme/emp-newbie' WHERE bot='newbie'")
    old, quiet = _at(api, days=-60), _at(api, days=-3)
    _sync(
        api,
        gh,
        **{
            "emp-marketing": {"created_at": old, "pushed_at": quiet},
            # Pushed since cpo was made here, while Tico gave it no token: an old copy may still be running.
            "emp-cpo": {"created_at": old, "pushed_at": _at(api, hours=5)},
            "emp-cfo": {"created_at": old, "pushed_at": quiet},
            "emp-newbie": {"created_at": old, "pushed_at": quiet},  # planned here: not running here yet
        },
    )
    assert _repository(api, "cmo") == "Acme/emp-marketing"
    assert [_repository(api, bot) for bot in ("cpo", "cfo", "newbie")] == ["", "", ""]
    assert _claims(api) == {(H.TICO_ACTOR, "cmo", "moved")}
    # The next sync claims nothing new and records nothing twice.
    _sync(api, gh, **{"emp-marketing": {"created_at": old, "pushed_at": quiet}})
    assert len(_claims(api)) == 1


def test_a_person_claims_the_rest_from_health_and_a_repository_two_bots_share_is_left(api, gh):
    _ready(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='Acme/emp-cmo' WHERE bot='cfo'")  # cmo's too
    old, since = _at(api, days=-60), _at(api, hours=5)
    _sync(api, gh, **{name: {"created_at": old, "pushed_at": since} for name in ("emp-cpo", "emp-cmo")})
    assert [_repository(api, bot) for bot in ("cpo", "cmo", "cfo")] == ["", "", ""]
    [line] = _history_checks(api)
    assert line["fixes"] == [{"label": "Claim", "href": "", "tab": "", "click": "", "claim": True}]
    member = api.post("/api/v2/repositories/claim", json={}, headers=auth("person-test"))
    assert member.status_code == 403, member.text
    got = api.post("/api/v2/repositories/claim", json={}, headers=auth())
    assert got.status_code == 200, got.text
    assert got.json() == {
        "claimed": [{"bot": "cpo", "repository": "acme/emp-cpo"}],
        "left": [
            {"bot": "cfo", "repository": "acme/emp-cmo", "why": "shared"},
            {"bot": "cmo", "repository": "acme/emp-cmo", "why": "shared"},
        ],
    }
    assert _repository(api, "cpo") == "Acme/emp-cpo"
    assert _claims(api) == {("human:ana", "cpo", "person")}
    # One named bot: cfo points elsewhere now, so cmo's is its alone and is claimed; an unknown bot is a 404.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='Acme/emp-cfo' WHERE bot='cfo'")
    assert api.post("/api/v2/repositories/claim", json={"bots": ["nobody"]}, headers=auth()).status_code == 404
    one = api.post("/api/v2/repositories/claim", json={"bots": ["cmo"]}, headers=auth()).json()
    assert one == {"claimed": [{"bot": "cmo", "repository": "acme/emp-cmo"}], "left": []}
    assert _repository(api, "cmo") == "Acme/emp-cmo"


def test_a_bot_moved_from_another_server_keeps_its_repository_whatever_its_name(api, gh):
    _ready(api, gh)
    _sync(api, gh, **{"bot-funnel": {"created_at": _old(api), "pushed_at": _old(api, days=1)}})
    made = api.post(
        "/api/v2/bots",
        json={
            "slug": "funnel",
            "display_name": "Funnel",
            "status": "planned",
            "repo": "bot-funnel",  # the name Tico would give it anyway: chosen only because it moved
            "moved_from": "https://old.acme.example",
        },
        headers={**auth(), "Idempotency-Key": "funnel"},
    )
    assert made.status_code == 200, made.text
    with api.app_state.store.read() as c:
        bot = H.resolve_bot(c, "funnel")["id"]
        moved = c.execute("SELECT json_extract(detail,'$.from') FROM events WHERE action='bot.moved_in'").fetchone()
    assert _repository(api, bot) == "Acme/bot-funnel"
    assert moved[0] == "https://old.acme.example"
