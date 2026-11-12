"""A seat's bot reaches the repositories In use by its template's role (docs/repositories.md "Bot repository access").

The Builder and the Release Owner write, the reviewers read, declared on their cards (`repo_access_mode`,
`repo_all_access`), never by slug. Only where nobody chose: a person's change stands. With the App installed on selected
repositories, the product repositories a person picked on GitHub are In use at once.
"""

import json

from backend import bot_settings as BS
from backend import hubdb as H
from backend import repositories as R
from backend.tests.helpers.github_app import api, gh, auth, runner_token, turn_token  # noqa: F401
from backend.tests.helpers.repositories import catalog, put

# The fixture's bots, seated: cpo builds, cmo reviews.
BUILDER, REVIEWER = "cpo", "cmo"


def _seat(c, bot, template):
    BS.update(c, bot, {"template": template})
    c.execute(
        "INSERT INTO seats(id,title,reports_to,created_at,created_by,updated_at,updated_by,template,filled_by,closed_at) "
        "VALUES(?,?,'',?,'human:ana',?,'human:ana',?,?,?)",
        (H.new_id(), template.title(), H.now(), H.now(), template, bot, H.now()),
    )


def _start(api):
    """What a start does for seats' repository access (Onboarding.seat_repositories): the bots it changed."""
    with api.app_state.store.transaction() as c:
        return api.app_state.onboarding.seat_repositories(c)


def _seated(api):
    with api.app_state.store.transaction() as c:
        _seat(c, BUILDER, "builder")
        _seat(c, REVIEWER, "reviewer")


def _effective(api, bot):
    data = api.get(f"/api/v2/bots/{bot}/repositories", headers=auth()).json()
    return {r["full_name"]: r["access"] for r in data["effective"]}


def _defaults(api, bot):
    with api.app_state.store.read() as c:
        rows = c.execute(
            "SELECT detail FROM events WHERE action='bot.repos_changed' AND target=? ORDER BY rowid", (bot,)
        ).fetchall()
    return [json.loads(row[0]).get("default", False) for row in rows]


def test_the_builder_writes_and_a_reviewer_reads_every_repository_in_use_now_and_later(api, gh):
    catalog(api, gh)
    put(api, "repositories/Acme/product", {"enabled": True})
    _seated(api)
    assert sorted(_start(api)) == [REVIEWER, BUILDER]
    assert _effective(api, BUILDER) == {"Acme/emp-cpo": "write", "Acme/product": "write"}
    assert _effective(api, REVIEWER) == {"Acme/emp-cmo": "write", "Acme/product": "read"}
    assert _defaults(api, BUILDER) == _defaults(api, REVIEWER) == [True]  # audited, and said to be the default
    # Never Run workflows or Edit workflow files.
    builder = api.get(f"/api/v2/bots/{BUILDER}/repositories", headers=auth()).json()
    assert builder["all_repos"] == [] and not any(
        r.get("run_workflows") or r.get("edit_workflows") for r in builder["effective"]
    )

    # The Builder pushes to the team's repository with its own write token: no fork is needed.
    runner_token(api, BUILDER)
    token = turn_token(api, BUILDER).json()
    write = [r[2] for r in gh.of("/access_tokens") if r[2]["permissions"].get("contents") == "write"][-1]
    assert sorted(write["repositories"]) == ["emp-cpo", "product"] and token["token"]
    # A reviewer cannot push: product is in its read token only.
    runner_token(api, REVIEWER)
    turn_token(api, REVIEWER)
    bodies = [r[2] for r in gh.of("/access_tokens")[-2:]]
    assert {(tuple(b["repositories"]), b["permissions"]["contents"]) for b in bodies} == {
        (("emp-cmo",), "write"),
        (("product",), "read"),
    }

    # A repository put in use later reaches them too, at the same level.
    put(api, "repositories/Acme/docs", {"enabled": True})
    assert _effective(api, BUILDER)["Acme/docs"] == "write" and _effective(api, REVIEWER)["Acme/docs"] == "read"
    assert _start(api) == []  # once: the next start changes nothing


def test_a_persons_change_stands_an_off_included(api, gh):
    catalog(api, gh)
    put(api, "repositories/Acme/product", {"enabled": True})
    put(api, f"bots/{REVIEWER}/repositories", {"mode": "own"})  # a person chose: own repository only
    put(
        api,
        f"bots/{BUILDER}/repositories",
        {"mode": "chosen", "chosen": [{"full_name": "Acme/product", "access": "read"}]},
    )
    _seated(api)
    assert _start(api) == []
    assert _effective(api, REVIEWER) == {"Acme/emp-cmo": "write"}
    assert _effective(api, BUILDER)["Acme/product"] == "read"
    # And after a default, a person's change to it stands at every later start.
    with api.app_state.store.transaction() as c:
        assert not R.untouched(c, BUILDER) and not R.untouched(c, REVIEWER)


def test_a_bot_the_company_put_on_all_repos_keeps_it(api, gh):
    """New bots get All repos in use (write) is the company's choice: a seat's bot made under it is left as it is."""
    catalog(api, gh)
    with api.app_state.store.transaction() as c:
        BS.update(c, REVIEWER, {"repo_access_mode": "all", "repo_all_access": "write"})
    _seated(api)
    assert _start(api) == [BUILDER]


def test_a_bot_that_fills_no_seat_or_whose_card_declares_nothing_is_left_alone(api, gh):
    catalog(api, gh)
    with api.app_state.store.transaction() as c:
        BS.update(c, BUILDER, {"template": "builder"})  # a Builder outside the chart
        _seat(c, REVIEWER, "librarian")  # a seat whose template reaches no repository
    assert _start(api) == []
    with api.app_state.store.read() as c:
        assert (BS.read(c, BUILDER).get("repo_access_mode") or "own") == "own"
        assert (BS.read(c, REVIEWER).get("repo_access_mode") or "own") == "own"


def test_a_selected_install_puts_the_picked_product_repositories_in_use_once(api, gh):
    gh.selection = "selected"
    rows = {r["full_name"]: r for r in catalog(api, gh)["repositories"]}
    assert {name for name, r in rows.items() if r["enabled"]} == {"Acme/product", "Acme/docs"}  # never a bot's
    with api.app_state.store.read() as c:
        said = c.execute("SELECT actor, detail FROM events WHERE action='repositories.changed'").fetchall()
    assert sorted((row[0], json.loads(row[1])["after"]["full_name"]) for row in said) == [
        ("system", "Acme/docs"),
        ("system", "Acme/product"),
    ]
    # Taken out of use by a person, it stays out.
    put(api, "repositories/Acme/docs", {"enabled": False})
    rows = {r["full_name"]: r for r in api.post("/api/v2/repositories/refresh", headers=auth()).json()["repositories"]}
    assert not rows["Acme/docs"]["enabled"]
    # A repository picked later is in use on the next read of GitHub.
    gh.repositories.append({"full_name": "Acme/web", "default_branch": "main"})
    rows = {r["full_name"]: r for r in api.post("/api/v2/repositories/refresh", headers=auth()).json()["repositories"]}
    assert rows["Acme/web"]["enabled"]


def test_an_all_repositories_install_puts_nothing_in_use(api, gh):
    rows = catalog(api, gh)["repositories"]
    assert not any(r["enabled"] for r in rows)


def test_a_second_builder_made_by_the_owner_writes_and_a_person_can_still_narrow_it(api, gh):
    """Builder 2, a copy or a renamed seat: the card decides, not the slug."""
    catalog(api, gh)
    put(api, "repositories/Acme/product", {"enabled": True})
    body = {"slug": "builder-2", "display_name": "Builder 2", "description": "Builds", "status": "planned"}
    made = api.post("/api/v2/bots", json={**body, "template": "builder"}, headers={**auth(), "Idempotency-Key": "b2"})
    assert made.status_code == 200, made.text
    with api.app_state.store.read() as c:
        bot = H.resolve_bot(c, "builder-2")["id"]
    assert _effective(api, bot)["Acme/product"] == "write"
    assert _defaults(api, bot) == [True]
    put(api, f"bots/{bot}/repositories", {"mode": "all", "all_access": "read"})
    with api.app_state.store.transaction() as c:
        _seat(c, bot, "builder")
    assert _start(api) == [] and _effective(api, bot)["Acme/product"] == "read"
