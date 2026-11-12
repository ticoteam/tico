"""The Release stage cuts its release without a person (docs/pipelines-v3.md "GitHub", Release;
backend/item_sources/github_release.py): the Release Owner is woken on entering Release, asks with ONE Tico tool
(`tico task release`), and Tico carries it out through its GitHub App on the Heartbeat. GitHub is
backend/tests/helpers/fake_github.py; nothing here reaches the real one."""

import pytest

from backend import never_stuck as NS
from backend import task_engine as E
from backend import task_requirements as R
from backend import waiting_on as W
from backend.item_sources import github as F
from backend.item_sources import github_release as GR
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub

REPO = "acme/app"
B, RM, EM, ANA = "bot:builder", "bot:release", "bot:eng-manager", X.ANA
WORKFLOW = {"release_with": "workflow:release.yml"}


def engineering(c, release=WORKFLOW, sign_off=True):
    """Build -> Sign off (a person's OK, the pre-release sign-off) -> Merge (queues PRs) -> Release (In a release)."""
    for slug in ("builder", "release", "eng-manager"):
        X.bot(c, slug)
    stages = [
        {"name": "Build", "owner": B, "requirements": [X.github("pr_open"), X.github("checks_pass")]},
        {
            "name": "Sign off",
            "owner": B,
            "requirements": [{"kind": "ok", "person": ANA, "who_oks": "people_only", "can_push_past": 0}],
        },
        {"name": "Merge", "owner": RM, "on_enter": {"act": "github.queue"}, "requirements": [X.github("pr_merged")]},
        {"name": "Release", "owner": RM, "requirements": [X.github("in_release", settings=release)]},
    ]
    if not sign_off:
        stages.pop(1)
    return X.pipeline(c, "eng", "Engineering", stages=tuple(stages), manager=EM)


def new(c, number):
    task = X.new_task(c, ANA, f"Ship {number}", "eng")
    H.task_link(c, ANA, task["id"], f"https://github.com/{REPO}/pull/{number}")
    return task["id"]


def seen(c, gh, number):
    return F.save_and_settle(c, REPO, number, F.read_pr(gh, REPO, number), F.version_now())


def stage(c, task_id):
    return E.task(c, task_id)["stage"]["name"]


def line(c, task_id):
    row = E.task(c, task_id)
    found = next(s for s in R.standings(c, row) if s.requirement["condition"] == "in_release")
    return (found.state, found.on, found.line)


def wakes(c, task_id, who=RM):
    return [
        (str(key), str(body or ""))
        for key, body in c.execute(
            "SELECT wake_key, body FROM messages WHERE wake_key LIKE ? AND recipient_id=? ORDER BY created_at, rowid",
            (task_id + ":%", who),
        ).fetchall()
    ]


def version(c, task_id):
    return E.task(c, task_id)["version"]


def refused(fn, rule):
    with pytest.raises(H.Refused) as raised:
        fn()
    assert raised.value.rule == rule, raised.value
    return raised.value.detail


def to_release(company, gh, number=1, sign_off=True, **settings):
    """A task whose PR merged, now in Release; the repo publishes releases and none holds the merge yet."""
    with company.transaction() as c:
        if not c.execute("SELECT 1 FROM pipelines WHERE id='eng'").fetchone():
            engineering(c, settings.get("release", WORKFLOW), sign_off)
            for release in gh.published[REPO]:  # Tico has read the repo's releases before
                F.save_release(c, REPO, release, F.version_now())
            F.saw_releases(c, REPO, F.version_now())
        task_id = new(c, number)
        gh.set_checks(REPO, gh.open_pr(REPO, number), "success")
        seen(c, gh, number)
        if sign_off:
            assert stage(c, task_id) == "Sign off"
            row = E.task(c, task_id)
            rid = c.execute("SELECT id FROM requirements WHERE stage_id='eng-sign-off'").fetchone()[0]
            X.answer(c, row, rid, "ok", ANA)
            E.advance(c, task_id)
        assert stage(c, task_id) == "Merge"
    F.run(company, gh)
    gh.merge(REPO, number)
    F.refresh(company, gh, H.shift(H.now(), minutes=10))
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"
    return task_id


@pytest.fixture
def gh():
    found = FakeGitHub()
    found.history[REPO] = [found.sha()]
    found.publish(REPO, "v0.9.0")  # the repo publishes releases
    return found


def age(c, task_id, hours=2):
    """The task's release asks were carried out `hours` ago."""
    at = H.shift(H.now(), hours=-hours)
    c.execute(
        "UPDATE effects SET updated_at=?, created_at=? WHERE action='release' AND visit_id IN "
        "(SELECT id FROM visits WHERE task_id=?)",
        (at, at, task_id),
    )


# ----------------------------------------------------------------------------- the path with nobody
def test_entering_release_wakes_the_release_owner_whose_one_ask_brings_the_release_and_done(company, gh):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        expected = f"No release of {REPO} asked for yet: cut one with `tico task release {task_id}`"
        assert line(c, task_id) == (R.NOT_MET, RM, expected)
        told = [body for key, body in wakes(c, task_id) if expected in body]
        assert told, wakes(c, task_id)  # woken on entering Release, with what is missing
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
        assert line(c, task_id) == (
            R.WAITING,
            "github",
            f"Asking GitHub for a release of {REPO} (workflow release.yml)",
        )
        assert "release_asked" in X.events(c, task_id)
        before = wakes(c, task_id)
    F.run(company, gh)  # the Heartbeat runs the workflow through the App
    assert gh.cuts == [(REPO, "workflow", "release.yml", "")]
    with company.transaction() as c:
        state, on, said = line(c, task_id)
        assert (state, on) == (R.WAITING, "github") and said.startswith(f"Waiting for a release of {REPO}: workflow")
        assert "release_cut" in X.events(c, task_id)
        for seconds in (10, 150, 600):  # waiting on GitHub wakes nobody
            NS.check(c, H.shift(H.now(), seconds=seconds))
        assert wakes(c, task_id) == before
    gh.publish(REPO, "v1.0.0")  # the release workflow published it
    F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"


def test_one_workflow_run_covers_every_task_merged_before_it(company, gh):
    first = to_release(company, gh, 1)
    second = to_release(company, gh, 2)
    with company.transaction() as c:
        GR.cut(c, first, RM, seen_version=version(c, first))
        assert line(c, second)[:2] == (R.WAITING, "github")  # the first task's ask covers this merge too
        detail = refused(lambda: GR.cut(c, second, RM, seen_version=version(c, second)), "task_requirement")
        assert "Tico waits for it" in detail
    gh.release_on_cut = "v1.1.0"
    F.run(company, gh)
    F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, first) == stage(c, second) == "Done"
    assert len(gh.cuts) == 1


def test_a_tag_repo_takes_the_tag_the_release_owner_names_at_the_merge_commit(company, gh):
    task_id = to_release(company, gh, release={"release_with": "tag"})
    with company.transaction() as c:
        assert line(c, task_id)[2].endswith(f"`tico task release {task_id} --tag <name>`")
        assert "--tag" in refused(lambda: GR.cut(c, task_id, RM, seen_version=version(c, task_id)), "task_requirement")
        GR.cut(c, task_id, RM, "v1.2.0", seen_version=version(c, task_id))
        merged = F.pr(c, REPO, 1)["merged_sha"]
    F.run(company, gh)
    assert gh.cuts == [(REPO, "tag", "v1.2.0", merged)]
    F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"


# ----------------------------------------------------------------------------- refused
def test_the_tool_is_refused_off_release_without_a_sign_off_or_a_way(company, gh):
    with company.transaction() as c:
        engineering(c)
        building = new(c, 9)
        assert "doesn't wait for a release" in refused(
            lambda: GR.cut(c, building, RM, seen_version=version(c, building)), "task_stage"
        )
        assert refused(lambda: GR.cut(c, building, B, seen_version=version(c, building)), "task_stage")
    signed = to_release(company, gh, 1)
    with company.transaction() as c:
        assert "Only the stage owner" in refused(
            lambda: GR.cut(c, signed, B, seen_version=version(c, signed)), "task_requirement"
        )
        # The sign-off was taken back: a bot may not cut it, a person still may.
        c.execute("UPDATE answers SET answer='no' WHERE by_id=?", (ANA,))
        detail = refused(lambda: GR.cut(c, signed, RM, seen_version=version(c, signed)), "task_needs_person")
        assert "Sign off" in detail
        assert GR.cut(c, signed, ANA)


def test_with_no_sign_off_stage_a_bot_is_refused_and_with_no_way_set_everyone_is(company, gh):
    task_id = to_release(company, gh, sign_off=False)
    with company.transaction() as c:
        refused(lambda: GR.cut(c, task_id, RM, seen_version=version(c, task_id)), "task_needs_person")
        c.execute("UPDATE requirements SET settings='{}' WHERE condition='in_release'")
        assert "Tico does not cut releases here" in refused(lambda: GR.cut(c, task_id, ANA), "task_requirement")


# ----------------------------------------------------------------------------- failures never wait silently
def test_a_missing_permission_is_one_clear_item_for_a_person_and_a_health_warning(company, gh):
    from backend import health

    task_id = to_release(company, gh)
    gh.withheld = {"actions"}
    with company.transaction() as c:
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
    F.run(company, gh)
    assert gh.cuts == []
    with company.transaction() as c:
        state, on, said = line(c, task_id)
        assert state == R.WAITING and on is None
        assert said.startswith("Tico's GitHub App lacks Actions: write") and f"tico task release {task_id}" in said
        waits = W.of(c, E.task(c, task_id))
        assert [w.state for w in waits] == [W.PERSON] and waits[0].who == ANA  # one item, a person's
        assert "release_refused" in X.events(c, task_id)
        told = [key for key, _ in wakes(c, task_id) if ":release_refused:" in key]
        assert len(told) == 1  # the Release Owner hears why, once

        class App:
            def row(self, _c):
                return {"slug": "acme-tico", "org": "acme", "account_type": None}

        warned = health._github_release_cut(c, App())
        assert warned and warned["label"] == "Tico can't cut releases" and "Actions: write" in warned["summary"]
    gh.withheld = set()  # a person accepted the permission; a person asks again
    with company.transaction() as c:
        GR.cut(c, task_id, ANA)
    F.run(company, gh)
    with company.transaction() as c:
        assert health._github_release_cut(c, App()) is None


def test_no_release_after_an_ask_is_the_owner_s_then_a_person_s(company, gh):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
    F.run(company, gh)
    with company.transaction() as c:
        age(c, task_id)
        state, on, said = line(c, task_id)
        assert (state, on) == (R.NOT_MET, RM) and said.startswith(f"No release of {REPO} an hour after Tico ran")
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))  # it may ask again
    gh.fail_next("cut_release", "Workflow does not have 'workflow_dispatch' trigger")
    F.run(company, gh)  # a failed call is tried again later, not given up at once
    with company.transaction() as c:
        assert line(c, task_id)[0] == R.WAITING
        c.execute("UPDATE effects SET next_try_at=NULL")
    F.run(company, gh)
    with company.transaction() as c:
        age(c, task_id)
        state, on, said = line(c, task_id)
        assert (state, on) == (R.WAITING, None)  # the second failure since the merge: a person's
        assert [w.state for w in W.of(c, E.task(c, task_id))] == [W.PERSON]


def test_a_repo_whose_releases_a_person_cuts_waits_for_the_next_one_and_a_person_hears_once_after_a_week(company, gh):
    """ticoteam/tico: Release with stays empty and a person tags each rc. Only that release ends the wait: no bot is
    woken, climbs a ladder or is told Late, and a person hears once if no release comes for a week."""
    task_id = to_release(company, gh, release={})
    waiting = (R.WAITING, "github", f"Waiting for the next release of {REPO}")
    with company.transaction() as c:
        c.execute("UPDATE stages SET expected_minutes=1440 WHERE id='eng-release'")  # as the preset: a day
        assert line(c, task_id) == waiting
        assert [w.state for w in W.of(c, E.task(c, task_id))] == [W.SOURCE]
        for days in (0.1, 1, 3, 6.9):  # past Release's expected day, and its 3 days
            assert NS.check(c, H.shift(H.now(), hours=days * 24))["late"] == 0
        assert line(c, task_id) == waiting
        assert wakes(c, task_id) == [] and wakes(c, task_id, EM) == []  # neither the Release Owner nor the manager
        assert "late" not in X.events(c, task_id)
        done = NS.check(c, H.shift(H.now(), days=7, minutes=1))
        assert (done["late"], done["to_person"], done["woken"]) == (1, 1, 0)
        waits = W.of(c, E.task(c, task_id))
        assert [w.state for w in waits] == [W.PERSON, W.SOURCE] and waits[0].who == ANA
        assert waits[1].line == waiting[2]
        assert NS.check(c, H.shift(H.now(), days=9))["late"] == 0  # once
        assert wakes(c, task_id) == [] and wakes(c, task_id, EM) == []
    gh.publish(REPO, "v1.0.0-rc.1", prerelease=True)  # the person's prerelease counts by default
    F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"


def test_only_a_person_changes_how_releases_are_cut(company):
    from backend import pipeline_settings as PS

    with company.transaction() as c:
        engineering(c, release={})
        # The test pipeline's PR merged and In a release are written straight to the table; saved, they can't be pushed
        # past (C3).
        c.execute("UPDATE requirements SET can_push_past=0 WHERE condition IN ('pr_merged', 'in_release')")

        def edit(by, change):
            record = PS.read(c, "eng")
            change(next(s for s in record["stages"] if s["name"] == "Release"))
            return PS.save(c, by, record, seen_version=record["settings_version"])

        def setting(**settings):
            return lambda stage: stage["requirements"][0]["settings"].update(settings)

        def added(stage):
            new = {"kind": "item", "source": "github", "condition": "in_release", "settings": {"release_with": "tag"}}
            stage["requirements"].append(new)

        for change in (
            setting(release_with="tag"),
            setting(release_with="workflow:release.yml"),
            setting(prereleases=False),
            setting(release_repo="acme/server"),
            added,
        ):
            with pytest.raises(H.Refused, match="Only a person changes how releases are cut") as raised:
                edit(EM, change)  # the pipeline's manager bot
            assert raised.value.rule == "task_settings"
        edit(EM, lambda stage: stage.update(expected_minutes=600))  # the rest of it a manager bot still changes
        assert edit(EM, setting(prereleases=True))  # explicitly enabling the default changes no policy
        assert edit(ANA, setting(prereleases=False))
        with pytest.raises(H.Refused, match="Only a person changes how releases are cut"):
            edit(EM, lambda stage: stage["requirements"][0]["settings"].pop("prereleases"))
        assert edit(ANA, lambda stage: stage["requirements"][0]["settings"].pop("prereleases"))
        assert edit(ANA, setting(release_with="tag", prereleases=True))  # a person may
        with pytest.raises(H.Refused, match="Only a person changes how releases are cut"):
            edit(EM, setting(release_with=""))  # and a bot may not take it back either


def test_release_with_is_checked_when_the_pipeline_is_saved():
    assert GR.valid("") and GR.valid("tag") and GR.valid("workflow:release.yml") and GR.valid("workflow:ci-cd.yaml")
    assert (
        not GR.valid("workflow:") and not GR.valid("workflow:../x.yml") and not GR.valid("dispatch") and not GR.valid(1)
    )


# ----------------------------------------------------------------------------- never a second release
def test_a_release_published_since_tico_last_read_is_found_and_none_is_cut(company, gh):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
    gh.publish(REPO, "v1.0.0")  # someone released the merge before the Heartbeat carried the ask out
    F.run(company, gh)
    assert gh.cuts == []
    with company.transaction() as c:
        state = c.execute("SELECT state FROM effects WHERE action='release'").fetchone()[0]
        assert state == "moot"  # not done: no other task counts it as an ask of its own
        assert "release_found" in X.events(c, task_id)
        assert stage(c, task_id) == "Done"


def test_the_tool_reads_github_first_and_names_the_release_that_already_holds_the_merge(company, gh):
    task_id = to_release(company, gh)
    gh.publish(REPO, "v1.0.0")
    assert F.read_releases_now(company, gh, task_id)  # what the route does before the tool's write
    with company.transaction() as c:
        detail = refused(lambda: GR.cut(c, task_id, RM, seen_version=version(c, task_id)), "task_requirement")
        assert detail == f"Already in v1.0.0 of {REPO}: there is no release to cut"
    assert gh.cuts == []


def test_a_pre_release_satisfies_in_release_by_default(company, gh):
    task_id = to_release(company, gh)
    gh.publish(REPO, "v1.0.0-rc.1", prerelease=True)
    assert F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"


def test_a_pre_release_does_not_satisfy_in_release_when_explicitly_disabled(company, gh):
    from backend import pipeline_settings as PS

    task_id = to_release(company, gh)
    with company.transaction() as c:
        record = PS.read(c, "eng")
        for step in record["stages"]:
            for req in step["requirements"]:
                if req.get("condition") in ("pr_merged", "in_release"):
                    req["can_push_past"] = False  # saved requirements enforce C3
                if req.get("condition") == "in_release":
                    req["settings"]["prereleases"] = False  # the editor's explicit off payload
        PS.save(c, ANA, record, seen_version=record["settings_version"])
        reloaded = PS.read(c, "eng")
        release = next(s for s in reloaded["stages"] if s["name"] == "Release")["requirements"][0]
        assert release["settings"] == {**WORKFLOW, "prereleases": False}
    gh.publish(REPO, "v1.0.0-rc.1", prerelease=True)
    assert F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"
        assert GR.released(c, task_id) == []
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
    F.run(company, gh)
    assert gh.cuts == [(REPO, "workflow", "release.yml", "")]
    with company.transaction() as c:
        assert "release_found" not in X.events(c, task_id)


# ----------------------------------------------------------------------------- Health: true while it matters
class App:
    def row(self, _c):
        return {"slug": "acme-tico", "org": "acme", "account_type": None}


def permissions(company, at=None, **seen):
    """What GitHub last reported for the installation (github_app.live_permissions keeps it)."""
    from backend.github_app import PERMISSIONS_SEEN
    from backend.repositories import save_metadata

    base = {"metadata": "read", "contents": "write", "actions": "write", "checks": "write"}
    with company.transaction() as c:
        save_metadata(c, PERMISSIONS_SEEN, {"permissions": {**base, **seen}, "at": at or H.now()})


def release_warning(company):
    from backend import health

    with company.transaction() as c:
        return health._github_release_cut(c, App())


def test_the_release_warning_says_how_many_tasks_wait_and_clears_with_the_setting_or_the_release(company, gh):
    permissions(company, actions="read")
    task_id = to_release(company, gh)
    warned = release_warning(company)
    assert warned and warned["label"] == "Tico can't cut releases"
    assert "Actions: write" in warned["summary"] and "1 task in Release waits" in warned["summary"]
    assert warned["fixes"][0]["href"].endswith("/settings/apps/acme-tico/permissions")
    with company.transaction() as c:  # the release workflow is no longer Tico's to run
        c.execute("UPDATE requirements SET settings='{}' WHERE condition='in_release'")
    assert release_warning(company) is None
    with company.transaction() as c:
        c.execute("UPDATE requirements SET settings=? WHERE condition='in_release'", ('{"release_with": "tag"}',))
    assert release_warning(company) is None  # a tag needs Contents: write, which the App has
    with company.transaction() as c:
        c.execute(
            "UPDATE requirements SET settings=? WHERE condition='in_release'", ('{"release_with": "workflow:r.yml"}',)
        )
    assert release_warning(company)
    gh.publish(REPO, "v1.0.0")
    F.refresh(company, gh, H.shift(H.now(), minutes=20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"
    assert release_warning(company) is None  # its release came: nothing waits


def test_a_refusal_warns_until_github_reports_the_permission_accepted(company, gh):
    task_id = to_release(company, gh)
    gh.withheld = {"actions"}
    with company.transaction() as c:
        GR.cut(c, task_id, RM, seen_version=version(c, task_id))
    F.run(company, gh)
    assert release_warning(company)  # refused, and GitHub has reported nothing newer
    permissions(company, at=H.shift(H.now(), minutes=-5), actions="")
    assert release_warning(company)  # an older report changes nothing
    permissions(company, at=H.shift(H.now(), minutes=5))
    assert release_warning(company) is None  # a person accepted Actions: write since


def test_health_warns_once_when_the_app_cannot_post_tico_merge_ok_and_a_pipeline_queues(company):
    from backend import health

    def warning():
        with company.transaction() as c:
            return health._github_merge_ok(c, App())

    permissions(company, checks="read")
    assert warning() is None  # no pipeline queues pull requests
    with company.transaction() as c:
        engineering(c)
        X.pipeline(c, "eng2", "Engineering 2", stages=({"name": "Merge", "on_enter": {"act": "github.queue"}},))
    warned = warning()
    assert warned and warned["id"] == "github_merge_ok" and "tico/merge-ok" in warned["summary"]
    assert warned["fixes"][0]["href"].endswith("/settings/apps/acme-tico/permissions")
    permissions(company)
    assert warning() is None  # Checks: write accepted
    with company.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='github-app-permissions'")
    assert warning() is None  # nothing learned yet: no guess
    permissions(company, checks="read")
    with company.transaction() as c:
        c.execute("UPDATE pipelines SET retired_at=?", (H.now(),))
    assert warning() is None
