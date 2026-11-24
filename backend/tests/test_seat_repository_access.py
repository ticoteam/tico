"""A seat's bot reaches the repositories In use by its template's role (docs/repositories.md "Bot repository access").

The Builder and the Release Owner write, the reviewers read, declared on their cards (`repo_access_mode`,
`repo_all_access`), never by slug. Only where nobody chose: a person's change stands. With the App installed on selected
repositories, the product repositories a person picked on GitHub are In use at once.
"""

import json

from backend import bot_settings as BS
from backend import hubdb as H
from backend import repositories as R
from backend.tests.helpers.github_app import api, gh, auth, connect, runner_token, turn_token  # noqa: F401
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
    assert all(json.loads(row[1]).get("picked_on_github") is True for row in said)  # a person's pick, not a new one
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


def test_a_new_agent_reads_every_repository_in_use_unless_the_team_chose_and_existing_bots_keep_theirs(api, gh):
    catalog(api, gh)
    put(api, "repositories/Acme/product", {"enabled": True})
    assert api.get("/api/v2/repositories", headers=auth()).json()["new_bot_default"] == "read"

    def make(slug):
        body = {"slug": slug, "display_name": slug.title(), "description": "Writes", "status": "planned"}
        made = api.post("/api/v2/bots", json=body, headers={**auth(), "Idempotency-Key": slug})
        assert made.status_code == 200, made.text
        with api.app_state.store.read() as c:
            bot = H.resolve_bot(c, slug)["id"]
            own = "Acme/" + c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]
        return bot, {name: level for name, level in _effective(api, bot).items() if name != own}

    seo, reached = make("seo")
    assert reached == {"Acme/product": "read"}  # never another bot's repository
    assert _defaults(api, seo) == [True]
    assert _effective(api, REVIEWER) == {"Acme/emp-cmo": "write"}  # a bot made before is unchanged
    put(api, "repositories/settings", {"new_bot_default": "own"})
    assert make("ads")[1] == {}


def test_a_bot_hired_into_a_builder_seat_writes_over_its_read_but_a_startup_never_lifts_read(api, gh):
    catalog(api, gh)
    put(api, "repositories/Acme/product", {"enabled": True})
    # Riley, a member the seat reports to, hires: a member's bot starts on the read default, not the card's write.
    seat = api.post(
        "/api/v2/seats",
        json={"title": "Builder", "reports_to": "human:riley"},
        headers={**auth(), "Idempotency-Key": "seat-builder"},
    )
    assert seat.status_code == 200, seat.text
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE seats SET template='builder' WHERE id=?", (seat.json()["id"],))
    hired = api.post(
        f"/api/v2/seats/{seat.json()['id']}/hire",
        json={"template": "builder"},
        headers={**auth("person-test"), "Idempotency-Key": "hire-builder"},
    )
    assert hired.status_code == 200, hired.text
    with api.app_state.store.read() as c:
        bot = H.resolve_bot(c, hired.json()["slug"])["id"]
    assert _effective(api, bot)["Acme/product"] == "write"

    # An existing bot started on read whose template becomes the Builder's keeps its read at the next start.
    body = {"slug": "maker", "display_name": "Maker", "description": "Builds", "status": "planned"}
    assert api.post("/api/v2/bots", json=body, headers={**auth(), "Idempotency-Key": "maker"}).status_code == 200
    with api.app_state.store.transaction() as c:
        maker = H.resolve_bot(c, "maker")["id"]
        _seat(c, maker, "builder")
    assert _start(api) == [] and _effective(api, maker)["Acme/product"] == "read"


# A product repository created after the App was connected starts In use (docs/repositories.md); GitHub dates each.
NEW, OLD = "2099-01-01T00:00:00Z", "2020-01-01T00:00:00Z"


def _listed(name, created=NEW, **extra):
    return {
        "full_name": "Acme/" + name,
        "default_branch": "main",
        **({"created_at": created} if created else {}),
        **extra,
    }


def _refresh(api):
    response = api.post("/api/v2/repositories/refresh", headers=auth())
    assert response.status_code == 200, response.text
    return {r["full_name"]: r for r in response.json()["repositories"]}


def _in_use(rows):
    return {name for name, r in rows.items() if r["enabled"]}


def _while_github_answers(api, monkeypatch, meanwhile):
    """Runs `meanwhile` in its own write as the sync reads its first setup file: between its snapshot and its write."""
    service, ran = api.app_state.github_app, []
    real = service._call

    def call(method, path, *args, **kwargs):
        if "/contents/" in path and not ran:
            ran.append(True)
            with api.app_state.store.transaction() as c:
                meanwhile(c)
        return real(method, path, *args, **kwargs)

    monkeypatch.setattr(service, "_call", call)
    return ran


def test_only_a_product_repository_created_after_connecting_starts_in_use(api, gh):
    connect(api)
    gh.repositories = [_listed("product", OLD), _listed("bot-sales", OLD), _listed("emp-cpo", OLD)]
    assert not _in_use(_refresh(api))  # an organization's first read: what it had before stays out of use
    gh.repositories += [
        _listed("web"),
        _listed("legacy", OLD),  # an old repository renamed or moved in
        _listed("undated", None),  # GitHub gave no time
        _listed("bot-ads"),
        _listed("emp-x"),  # a bot repository name no bot of this company holds
        _listed("attic", archived=True),
    ]
    assert _in_use(_refresh(api)) == {"Acme/web"}
    with api.app_state.store.read() as c:
        said = c.execute("SELECT actor,target,detail FROM events WHERE action='repositories.changed'").fetchall()
    assert [(r[0], r[1], json.loads(r[2])["new_on_github"]) for r in said] == [("system", "Acme/web", True)]
    # Taken out of use by a person, it stays out on every later read.
    put(api, "repositories/Acme/web", {"enabled": False})
    assert not _refresh(api)["Acme/web"]["enabled"]


def test_a_row_written_or_a_bot_given_the_repository_while_github_answers_keeps_it_out_of_use(api, gh, monkeypatch):
    catalog(api, gh)
    gh.repositories += [_listed("web"), _listed("ads-site")]

    def meanwhile(c):
        # A product repository's receipt wrote web's row and a person took it out of use; a bot was given ads-site.
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES('w','Acme/web',0,?)", (H.now(),))
        c.execute("UPDATE bot_config SET repo='ads-site' WHERE bot='cpo'")

    ran = _while_github_answers(api, monkeypatch, meanwhile)
    rows = _refresh(api)
    assert ran and not rows["Acme/web"]["enabled"] and not rows["Acme/ads-site"]["enabled"]
    with api.app_state.store.read() as c:  # nor is either audited as put In use
        assert not c.execute("SELECT 1 FROM events WHERE action='repositories.changed'").fetchone()


def test_a_reconnect_while_github_answers_writes_nothing(api, gh, monkeypatch):
    catalog(api, gh)
    gh.repositories.append(_listed("web"))
    ran = _while_github_answers(api, monkeypatch, lambda c: c.execute("UPDATE github_app SET app_id=app_id+1"))
    assert R.sync(api.app_state.github_app) == "replaced" and ran
    with api.app_state.store.read() as c:
        assert not c.execute("SELECT 1 FROM repositories WHERE full_name='Acme/web'").fetchone()


def test_past_the_cap_a_new_repository_starts_out_of_use(api, gh, monkeypatch):
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 2)  # GitHub names at most 500 in one token; the cap is 400
    catalog(api, gh)
    gh.repositories += [_listed("web"), _listed("api"), _listed("cli")]
    assert _in_use(_refresh(api)) == {"Acme/web", "Acme/api"}


def test_a_new_repository_in_use_tells_each_computer_once(api, gh):
    catalog(api, gh)
    runner_token(api, BUILDER)

    def told():
        with api.app_state.store.read() as c:
            return c.execute("SELECT COUNT(*) FROM changes WHERE topic='runner' AND kind='repositories'").fetchone()[0]

    before = told()
    gh.repositories.append(_listed("web"))
    assert "Acme/web" in _in_use(_refresh(api)) and told() - before == 1


def test_a_repository_renamed_on_github_starts_out_of_use_under_its_new_name(api, gh):
    catalog(api, gh)
    gh.repositories[0]["id"] = 7  # product, read before GitHub ids were kept: its id is filled now
    gh.repositories.append(_listed("payroll", id=901))
    assert "Acme/payroll" in _in_use(_refresh(api))
    put(api, "repositories/Acme/payroll", {"enabled": False})
    gh.repositories[-1]["full_name"] = "Acme/payroll-2026"  # renamed: GitHub's id stays
    rows = _refresh(api)
    assert not rows["Acme/payroll-2026"]["enabled"] and not rows["Acme/payroll"]["reachable"]
    with api.app_state.store.read() as c:
        ids = dict(c.execute("SELECT full_name, github_id FROM repositories WHERE github_id IS NOT NULL").fetchall())
        assert not c.execute("SELECT 1 FROM events WHERE target='Acme/payroll-2026'").fetchone()  # nor audited new
    assert ids == {"Acme/product": 7, "Acme/payroll": 901, "Acme/payroll-2026": 901}


def test_a_new_repository_under_an_old_name_inherits_no_workflow_approval(api, gh):
    catalog(api, gh)
    gh.repositories.append(_listed("payroll", id=901))
    _refresh(api)
    approved = {"full_name": "Acme/payroll", "run_workflows": True, "edit_workflows": True}
    put(api, f"bots/{BUILDER}/repositories", {"mode": "all", "all_access": "write", "all_repos": [approved]})
    put(api, "repositories/Acme/payroll", {"enabled": False})
    gh.repositories[-1]["full_name"] = "Acme/payroll-2026"  # renamed; later a new repository takes the old name
    _refresh(api)
    gh.repositories.append(_listed("payroll", id=902))
    rows = _refresh(api)
    assert not rows["Acme/payroll"]["enabled"] and not rows["Acme/payroll-2026"]["enabled"]
    reached = api.get(f"/api/v2/bots/{BUILDER}/repositories", headers=auth()).json()["effective"]
    assert not [r for r in reached if r["full_name"].startswith("Acme/payroll")]


def test_setup_files_are_read_only_for_the_repositories_put_in_use(api, gh, monkeypatch):
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 1)
    catalog(api, gh)
    gh.repositories += [_listed("web"), _listed("api"), _listed("legacy", OLD)]
    assert _in_use(_refresh(api)) == {"Acme/web"}
    assert {call[1].split("/")[3] for call in gh.calls if "/contents/" in call[1]} == {"web"}


def test_the_cap_counts_a_repository_in_use_by_who_owns_it_when_the_sync_writes(api, gh, monkeypatch):
    catalog(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='product' WHERE bot='cpo'")  # the Builder held product for a while
    put(api, "repositories/Acme/product", {"enabled": True})
    assert _refresh(api)["Acme/product"]["bot_repo"]  # kept as a bot's
    gh.repositories += [_listed("web"), _listed("api")]
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 2)
    # Let go while GitHub answers: product is the team's again, In use, so it takes one of the two places.
    ran = _while_github_answers(
        api, monkeypatch, lambda c: c.execute("UPDATE bot_config SET repo='emp-cpo' WHERE bot='cpo'")
    )
    assert _in_use(_refresh(api)) == {"Acme/product", "Acme/web"} and ran


def test_a_repository_a_bot_lets_go_of_while_github_answers_waits_for_the_next_read(api, gh, monkeypatch):
    catalog(api, gh)
    gh.repositories += [_listed("web"), _listed("ads-site")]
    gh.setup_files["/repos/Acme/ads-site/contents/tico.json"] = {"setup": "npm ci"}
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='ads-site' WHERE bot='cmo'")
    ran = _while_github_answers(
        api, monkeypatch, lambda c: c.execute("UPDATE bot_config SET repo='emp-cmo' WHERE bot='cmo'")
    )
    rows = _refresh(api)
    assert ran and "Acme/ads-site" not in rows  # not ticked without its setup file, and still first sight
    rows = _refresh(api)
    assert rows["Acme/ads-site"]["enabled"] and rows["Acme/ads-site"]["setup_command"] == "npm ci"


def test_a_repository_github_lists_twice_is_put_in_use_once(api, gh, monkeypatch):
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 2)
    catalog(api, gh)
    gh.repositories += [_listed("web"), _listed("web"), _listed("api")]
    assert _in_use(_refresh(api)) == {"Acme/web", "Acme/api"}
    with api.app_state.store.read() as c:
        said = c.execute("SELECT target FROM events WHERE action='repositories.changed'").fetchall()
    assert sorted(r[0] for r in said) == ["Acme/api", "Acme/web"]


def test_a_repository_planned_first_keeps_its_place_under_the_cap(api, gh, monkeypatch):
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 1)
    catalog(api, gh)
    gh.repositories += [_listed("ads-site"), _listed("web")]  # ads-site, listed first, is a bot's when the sync starts
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='ads-site' WHERE bot='cmo'")
    _while_github_answers(api, monkeypatch, lambda c: c.execute("UPDATE bot_config SET repo='emp-cmo' WHERE bot='cmo'"))
    assert _in_use(_refresh(api)) == {"Acme/web"}


def test_a_ticked_bot_named_repository_takes_no_place_under_the_cap(api, gh, monkeypatch):
    monkeypatch.setattr(R, "AUTO_TICK_CAP", 1)
    catalog(api, gh)
    gh.repositories.append(_listed("emp-old", OLD))  # no bot of this company holds it
    _refresh(api)
    put(api, "repositories/Acme/emp-old", {"enabled": True})
    gh.repositories.append(_listed("web"))
    assert _in_use(_refresh(api)) == {"Acme/emp-old", "Acme/web"}  # its tick kept, and web still put In use


def test_a_new_repository_under_a_renamed_ones_name_starts_out_of_use(api, gh):
    catalog(api, gh)
    gh.repositories.append(_listed("payroll", id=901))
    assert "Acme/payroll" in _in_use(_refresh(api))  # In use, and left so
    gh.repositories[-1]["full_name"] = "Acme/payroll-2026"  # renamed: it starts out of use under its new name
    gh.repositories.append(_listed("payroll", id=902))  # another takes the old name
    rows = _refresh(api)
    assert not rows["Acme/payroll"]["enabled"] and not rows["Acme/payroll-2026"]["enabled"]
    with api.app_state.store.read() as c:
        said = c.execute("SELECT target,detail FROM events WHERE action='repositories.changed'").fetchall()
    taken = [(r[0], json.loads(r[1])["after"]["enabled"]) for r in said if json.loads(r[1]).get("replaced_on_github")]
    assert taken == [("Acme/payroll", 0)]


def test_a_repository_back_under_a_new_name_after_its_old_one_was_reused_stays_out_of_use(api, gh):
    catalog(api, gh)
    gh.repositories.append(_listed("payroll", id=901))
    _refresh(api)
    put(api, "repositories/Acme/payroll", {"enabled": False})
    gh.repositories.pop()  # moved out of the organization
    _refresh(api)
    gh.repositories.append(_listed("payroll", id=902))  # a new one takes the name
    _refresh(api)
    gh.repositories.append(_listed("payroll-2026", id=901))  # the first comes back, renamed
    assert not _refresh(api)["Acme/payroll-2026"]["enabled"]
