"""Release windows: when a window is open, which candidate ships, when stable may take the canary's release, and that
a failure reaches the release-train issue (scripts/release_train.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from scripts import release_train as rt

A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)          # the 14:30 UTC window is the latest start
OPENED = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)


def run(sha, version="v0.5.201", previous="v0.5.200", *, status="completed", conclusion="success", at=NOW, id=1):
    return {"id": id, "display_title": rt.title(sha, version, previous), "status": status, "conclusion": conclusion,
            "html_url": "https://github.example/run/%d" % id, "created_at": at.isoformat(), "updated_at": at.isoformat()}


class FakeGitHub:
    """main is A <- B <- C (C is the head); v0.5.200 is tagged at A and was published before the window opened."""

    def __init__(self, runs=(), tags=None, head=C, published=OPENED - timedelta(hours=6)):
        self.tags_ = dict(tags if tags is not None else {"v0.5.199": "0" * 40, "v0.5.200": A, "v0.6.0-rc.1": B})
        self.order = [A, B, C]
        self.head_ = head
        self.runs = list(runs)
        self.others = {"ci.yml": [], "hotfix.yml": []}     # runs on other branches/events (recent_runs only)
        self.release_runs = []
        self.releases = {"v0.5.200": {"draft": False, "published_at": published.isoformat()}} if published else {}
        self.dispatched, self.tagged, self.comments, self.closed = [], [], [], []
        self.open_issues, self.blockers, self.created = [], [], []
        self.on_dispatch = None

    def _sha(self, ref):
        return self.tags_.get(ref) or (self.head_ if ref == "main" else ref)

    def tags(self):
        return set(self.tags_)

    def head(self, ref="main"):
        return self._sha(ref)

    def compare(self, base, head):
        base, head = self._sha(base), self._sha(head)
        if base == head:
            return "identical"
        if base not in self.order or head not in self.order:
            return "diverged"
        return "ahead" if self.order.index(head) > self.order.index(base) else "behind"

    def contains(self, ref, sha):
        return self.compare(sha, ref) in ("ahead", "identical")

    def validation_runs(self):
        return list(self.runs)

    def recent_runs(self, workflow):
        return (list(self.runs) if workflow == "ci.yml" else []) + self.others[workflow]

    def workflow_runs(self, workflow, branch):
        return list(self.release_runs)

    def dispatch(self, workflow, ref, inputs):
        self.dispatched.append((workflow, ref, inputs))
        if self.on_dispatch:
            self.on_dispatch(workflow, ref, inputs)

    def create_tag(self, name, sha, token=None):
        if name in self.tags_:
            if self.tags_[name] != sha:
                raise rt.Fail("%s already exists at another commit" % name)
            return False
        self.tags_[name] = sha
        self.tagged.append((name, sha, token))
        return True

    def release(self, tag):
        return self.releases.get(tag)

    def issues(self, label, state="open"):
        return list(self.blockers if label == rt.BLOCKER_LABEL else self.open_issues)

    def ensure_label(self, *args):
        pass

    def create_issue(self, title, body, labels):
        issue = {"number": 100 + len(self.created), "title": title, "body": body}
        self.created.append(issue)
        self.open_issues.append(issue)
        return issue

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.closed.append(number)
        self.open_issues = [i for i in self.open_issues if i["number"] != number]


def publishes(gh):
    """release.yml, dispatched on the tag, publishes the release at once."""
    def on_dispatch(workflow, ref, inputs):
        if workflow == "release.yml":
            gh.releases[ref] = {"draft": False, "published_at": NOW.isoformat(), "html_url": "https://github.example/r"}
        elif workflow == "ci.yml":
            gh.runs.insert(0, run(inputs["candidate_sha"], inputs["version"], inputs["previous_release"],
                                  status="queued", conclusion=None, at=datetime.now(timezone.utc), id=50))
    gh.on_dispatch = on_dispatch
    return gh


@pytest.fixture(autouse=True)
def clean_log(monkeypatch):
    monkeypatch.setattr(rt, "LOG", [])
    monkeypatch.setattr(rt, "sleep", lambda seconds: None)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


ENV = {"CANARY_URL": "https://canary.acme.example", "CANARY_UPDATE_KEY": "k1",
       "STABLE_URL": "https://stable.acme.example", "STABLE_UPDATE_KEY": "k2", "BAKE_HOURS": "4",
       "RELEASE_TRAINS": "on"}


# ---- versions and the clock ----

def test_versions_order_numerically_and_skip_prereleases():
    tags = {"v0.5.9", "v0.5.10", "v0.6.0-rc.1", "latest", "v1.0"}
    assert rt.newest_stable(tags) == "v0.5.10"
    assert rt.next_patch("v0.5.10") == "v0.5.11"
    assert rt.newest_stable({"v0.6.0-rc.1"}) is None


def test_validation_title_round_trips_for_release_yml():
    assert rt.parse_title(rt.title(C, "v0.5.201", "v0.5.200")) == (C, "v0.5.201", "v0.5.200")
    assert rt.parse_title("Release window opens") is None


@pytest.mark.parametrize("now, start", [
    (datetime(2026, 11, 5, 14, 29, tzinfo=timezone.utc), datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)),
    (datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc), datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)),
    (datetime(2026, 11, 5, 21, 59, tzinfo=timezone.utc), datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)),
    (datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc), datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)),
    (datetime(2027, 1, 1, 1, 0, tzinfo=timezone.utc), datetime(2026, 12, 31, 22, 0, tzinfo=timezone.utc)),
])
def test_window_start_is_the_latest_utc_open(now, start):
    assert rt.window_start(now) == start
    assert rt.window_start(now) <= now < rt.next_window(now)


def test_crons_are_the_window_starts():
    assert sorted(tuple(int(part) for part in cron.split()[1::-1]) for cron in rt.CRONS) == sorted(rt.WINDOWS)


# ---- what an open window does ----

def plan(gh, now=NOW, finished=None):
    return rt.plan_step(gh, now, finished)


def test_window_closes_once_a_release_is_published_after_it_opened():
    gh = FakeGitHub([run(C)], published=OPENED + timedelta(minutes=40))
    assert plan(gh)["action"] == "none" and "window closed" in plan(gh)["why"]
    # The next window opens at 22:00 UTC and ships.
    assert plan(gh, now=datetime(2026, 11, 5, 22, 5, tzinfo=timezone.utc))["action"] == "ship"


def test_an_unpublished_newest_tag_holds_the_window():
    gh = FakeGitHub([run(C)])
    gh.tags_["v0.5.201"] = B
    assert plan(gh)["action"] == "none" and "not published" in plan(gh)["why"]


def test_nothing_to_do_when_main_has_not_moved():
    assert plan(FakeGitHub(head=A))["action"] == "none"


def test_the_first_green_candidate_of_the_window_ships():
    picked = plan(FakeGitHub([run(B, id=2, at=OPENED + timedelta(minutes=20))]))
    assert (picked["action"], picked["sha"], picked["version"]) == ("ship", B, "v0.5.201")
    # Green before the window opened: main's head ships, an older commit does not (main's head is validated instead).
    assert plan(FakeGitHub([run(C, at=OPENED - timedelta(hours=2))]))["action"] == "ship"
    assert plan(FakeGitHub([run(B, at=OPENED - timedelta(hours=2))]))["action"] == "dispatch"


@pytest.mark.parametrize("runs", [
    [run(D)],                                     # not on main
    [run(C, "v0.5.201", "v0.5.199")],             # validated as an upgrade from another release
    [run(C, "v0.5.202", "v0.5.201")],             # another version
    [run(C, conclusion="cancelled")],             # canceled is no verdict: validate again
], ids=["off-main", "other-previous", "other-version", "cancelled"])
def test_only_a_green_run_of_this_version_on_main_counts(runs):
    assert plan(FakeGitHub(runs))["action"] == "dispatch"


def test_one_validation_at_a_time():
    assert plan(FakeGitHub([run(C, status="in_progress", conclusion=None)]))["action"] == "wait"
    gh = FakeGitHub()
    gh.others["ci.yml"] = [{"status": "queued", "html_url": "hotfix-validation"}]
    assert "hotfix-validation" in plan(gh)["why"]


def test_a_red_head_waits_for_a_push_and_a_red_older_commit_validates_the_head():
    assert plan(FakeGitHub([run(C, conclusion="failure")]))["action"] == "wait"
    assert plan(FakeGitHub([run(B, conclusion="failure")]))["action"] == "dispatch"
    # A window still red at the next open carries on, without validating the same head again.
    later = datetime(2026, 11, 6, 14, 35, tzinfo=timezone.utc)
    assert plan(FakeGitHub([run(C, conclusion="failure", at=OPENED)]), now=later)["action"] == "wait"


def test_a_finished_red_candidate_of_this_window_is_reported():
    finished = {"conclusion": "failure", "title": rt.title(B, "v0.5.201", "v0.5.200"), "url": "red-url"}
    assert plan(FakeGitHub([run(B, conclusion="failure")]), finished=finished)["red"]["sha"] == B
    stale = dict(finished, title=rt.title(B, "v0.5.200", "v0.5.199"))
    assert plan(FakeGitHub(), finished=stale)["red"] is None
    assert plan(FakeGitHub(), finished=dict(finished, conclusion="cancelled"))["red"] is None


# ---- the guard ----

def guard(gh, env, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rt.cmd_guard(gh, env, NOW)
    return dict(line.split("=", 1) for line in out.read_text().split())


def test_guard_runs_the_window_job_only_when_it_has_work(tmp_path, monkeypatch):
    on = {"RELEASE_TRAINS": "on"}
    assert guard(FakeGitHub(), dict(on, GITHUB_EVENT_NAME="schedule"), tmp_path, monkeypatch) == {
        "go": "true", "open": "true"}
    assert guard(FakeGitHub(), dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch) == {
        "go": "true", "open": "false"}
    busy = FakeGitHub([run(C, status="in_progress", conclusion=None)])
    assert guard(busy, dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch)["go"] == "false"
    red = {"GITHUB_EVENT_NAME": "workflow_run", "RUN_EVENT": "workflow_dispatch", "RUN_CONCLUSION": "failure",
           "RUN_TITLE": rt.title(C, "v0.5.201", "v0.5.200"), "RUN_URL": "u"}
    assert guard(FakeGitHub([run(C, conclusion="failure")]), dict(on, **red), tmp_path, monkeypatch)["go"] == "true"


def test_guard_respects_the_switch_and_a_running_hotfix(tmp_path, monkeypatch):
    for event in ("schedule", "push", "workflow_run"):
        assert guard(FakeGitHub(), {"GITHUB_EVENT_NAME": event}, tmp_path, monkeypatch)["go"] == "false"
    assert guard(FakeGitHub(), {"GITHUB_EVENT_NAME": "workflow_dispatch"}, tmp_path, monkeypatch)["go"] == "true"
    gh = FakeGitHub()
    gh.others["hotfix.yml"] = [{"status": "pending", "html_url": "hotfix"}]
    # Entering the lane now would cancel the hotfix waiting there.
    assert guard(gh, {"GITHUB_EVENT_NAME": "workflow_dispatch"}, tmp_path, monkeypatch)["go"] == "false"


# ---- installs, through the update key's routes only ----

@pytest.fixture
def installs(monkeypatch):
    """Two fake installs answering GET/POST /api/v2/system/update; an update finishes healthy at once."""
    state = {
        "canary": {"state": "healthy", "running": "0.5.200", "to": "0.5.200",
                   "history": [{"version": "0.5.200", "since": (NOW - timedelta(hours=5)).isoformat()}]},
        "stable": {"state": "healthy", "running": "0.5.199", "to": "0.5.199",
                   "history": [{"version": "0.5.199", "since": (NOW - timedelta(days=1)).isoformat()}]},
        "updated": [], "paths": [], "outcome": "healthy", "down": set()}

    def call(target, method, path, body=None, key=True):
        name = target["name"]
        state["paths"].append((name, method, path, key))
        if name in state["down"]:
            return 0, {"error": "URLError"}
        if (method, path) == ("POST", rt.UPDATE_PATH):
            version = body["version"]
            state["updated"].append((name, version))
            ok = state["outcome"] == "healthy"
            state[name] = dict(state[name], to=version, state=state["outcome"],
                               running=version if ok else state[name]["running"],
                               history=state[name]["history"] + ([{"version": version, "since": NOW.isoformat()}]
                                                                 if ok else []))
            return 200, {"ok": True}
        return 200, dict(state[name])

    monkeypatch.setattr(rt.rollout, "call", call)
    return state


def test_updates_use_only_the_update_key_routes(installs):
    rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    assert installs["updated"] == [("canary", "v0.5.201".lstrip("v"))]
    assert {path for _, _, path, _ in installs["paths"]} <= {rt.UPDATE_PATH, rt.UPDATE_PATH + "/check"}
    assert all(key for *_, key in installs["paths"])


def test_a_rolled_back_or_unreadable_update_fails(installs):
    installs["outcome"] = "rolled_back"
    with pytest.raises(rt.Fail, match="rolled_back"):
        rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    installs["down"].add("stable")
    with pytest.raises(rt.Fail, match="cannot be read"):
        rt.update_install(rt.install("STABLE", ENV), "v0.5.201")


# ---- the bake gate ----

def gate(**overrides):
    args = dict(version="0.5.201", canary_status={"state": "healthy", "running": "0.5.201", "history": [
        {"version": "0.5.201", "since": (NOW - timedelta(hours=5)).isoformat()}]}, now=NOW, bake_hours=4, hold="",
        blockers=[])
    args.update(overrides)
    return rt.bake_gate(**args)


def test_bake_gate_passes_a_healthy_baked_release():
    assert gate() == ([], [])


@pytest.mark.parametrize("overrides, why", [
    ({"now": NOW - timedelta(hours=1, minutes=10)}, "3.8 h of 4 h"),
    ({"hold": "waiting on the billing fix"}, "RELEASE_HOLD"),
    ({"blockers": [{"number": 7, "title": "Tasks vanish"}]}, "release-blocker #7"),
])
def test_bake_gate_holds_quietly(overrides, why):
    waits, faults = gate(**overrides)
    assert faults == [] and any(why in w for w in waits)


@pytest.mark.parametrize("status, why", [
    (None, "cannot be read"),
    ({"state": "rolled_back", "running": "0.5.201", "message": "health check",
      "history": [{"version": "0.5.201", "since": "2026-11-05T10:00:00Z"}]}, "rolled_back"),
    ({"state": "", "running": "0.5.201", "history": [{"version": "0.5.201", "since": "2026-11-05T10:00:00Z"}]},
     "unknown"),
    ({"state": "healthy", "running": "0.5.200", "history": []}, "runs 0.5.200"),
    ({"state": "healthy", "running": "0.5.201", "history": [{"version": "0.5.200", "since": "2026-11-05T10:00:00Z"}]},
     "since when"),
])
def test_bake_gate_holds_and_reports_what_it_cannot_tell(status, why):
    _, faults = gate(canary_status=status)
    assert any(why in f for f in faults)


def test_promote_updates_stable_to_the_baked_canary_release(installs):
    rt.promote(FakeGitHub(), ENV, now=NOW)
    assert installs["updated"] == [("stable", "0.5.200")]


def test_promote_holds_on_bake_hold_unknowns_and_never_goes_backwards(installs):
    gh = FakeGitHub()
    rt.promote(gh, ENV, now=NOW - timedelta(hours=2))
    rt.promote(gh, dict(ENV, RELEASE_HOLD="yes"), now=NOW)
    installs["down"].add("stable")
    with pytest.raises(rt.Fail, match="holding"):
        rt.promote(gh, ENV, now=NOW)
    installs["down"].clear()
    installs["canary"]["running"] = "0.5.198"
    rt.promote(gh, ENV, now=NOW)
    assert installs["updated"] == []


def test_promote_skips_without_keys(installs):
    rt.promote(FakeGitHub(), dict(ENV, STABLE_UPDATE_KEY=" "), now=NOW)
    assert installs["updated"] == [] and any("STABLE_UPDATE_KEY" in line for line in rt.LOG)


# ---- the window job ----

def test_window_open_promotes_then_ships_the_green_candidate_and_closes_the_issue(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    gh.open_issues.append({"number": 7, "title": "Release window failing"})
    assert rt.cmd_window(gh, ENV, True, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200"), ("canary", "0.5.201")]
    assert gh.tagged == [("v0.5.201", C, None)]
    assert gh.dispatched == [("release.yml", "v0.5.201", {"desktop": "false"})]
    assert gh.closed == [7]
    # Published: the window is closed, and the next event does nothing.
    assert rt.plan_step(gh, NOW + timedelta(minutes=5))["action"] == "none"


def test_a_push_promotes_before_the_canary_moves_on(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200"), ("canary", "0.5.201")]


def test_a_tag_token_lets_the_tag_push_start_release(installs):
    gh = FakeGitHub([run(C)])
    original = gh.create_tag

    def create_tag(name, sha, token=None):
        made = original(name, sha, token)
        gh.releases[name] = {"draft": False, "published_at": NOW.isoformat()}      # the push-triggered release.yml
        return made

    gh.create_tag = create_tag
    rt.cmd_window(gh, dict(ENV, RELEASE_TAG_TOKEN="app-token"), False, NOW)
    assert gh.tagged == [("v0.5.201", C, "app-token")] and gh.dispatched == []


def test_window_validates_main_and_waits_until_the_run_is_listed(installs):
    gh = publishes(FakeGitHub())
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    assert gh.dispatched == [("ci.yml", "main", {"candidate_sha": C, "previous_release": "v0.5.200",
                                                  "version": "v0.5.201"})]
    assert any("run/50" in line for line in rt.LOG)
    assert gh.tagged == [] and installs["updated"] == [] and gh.created == []
    # The next event sees the run and waits.
    assert rt.plan_step(gh, NOW)["action"] == "wait"


def test_a_red_candidate_goes_on_the_issue_and_main_moving_validates_again(installs):
    gh = publishes(FakeGitHub([run(B, conclusion="failure")]))
    env = dict(ENV, GITHUB_EVENT_NAME="workflow_run", RUN_EVENT="workflow_dispatch", RUN_CONCLUSION="failure",
               RUN_TITLE=rt.title(B, "v0.5.201", "v0.5.200"), RUN_URL="https://github.example/run/red")
    assert rt.cmd_window(gh, env, False, NOW) == 1
    assert len(gh.created) == 1 and "run/red" in gh.created[0]["body"]
    assert [d[0] for d in gh.dispatched] == ["ci.yml"] and gh.tagged == []


def test_missing_canary_key_fails_without_tagging(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, dict(ENV, CANARY_UPDATE_KEY=""), False, NOW) == 1
    assert gh.tagged == [] and gh.created and "CANARY_UPDATE_KEY" in gh.created[0]["body"]


def test_a_failed_release_workflow_is_reported(installs):
    gh = FakeGitHub([run(C)])
    gh.release_runs = [{"status": "completed", "conclusion": "failure", "html_url": "release-run",
                        "created_at": datetime.now(timezone.utc).isoformat()}]
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
    assert "release-run" in gh.created[0]["body"] and ("canary", "0.5.201") not in installs["updated"]


def test_wait_release_fails_on_this_release_run_only():
    gh = FakeGitHub()
    gh.release_runs = [{"status": "completed", "conclusion": "failure", "html_url": "old",
                        "created_at": (NOW - timedelta(hours=1)).isoformat()}]
    with pytest.raises(rt.Fail, match="no GitHub release"):
        rt.wait_release(gh, "v0.5.201", NOW, timeout=0)


# ---- the release-train issue ----

def test_failures_share_one_issue_and_a_shipped_window_closes_it():
    gh = FakeGitHub()
    rt.report(gh, False, ["ship: canary rolled back"])
    rt.report(gh, False, ["promote: stable failed"])
    assert len(gh.created) == 1 and gh.comments[0][0] == gh.created[0]["number"]
    rt.report(gh, True, ["shipped"])
    assert gh.closed == [gh.created[0]["number"]] and gh.open_issues == []
    rt.report(gh, True, ["shipped again"])
    assert len(gh.created) == 1


# ---- hotfix ----

def hotfix_github():
    gh = FakeGitHub()

    def on_dispatch(workflow, ref, inputs):
        if workflow == "ci.yml":
            gh.runs.insert(0, run(inputs["candidate_sha"], inputs["version"], inputs["previous_release"],
                                  at=datetime.now(timezone.utc), id=9))
        else:
            gh.releases[ref] = {"draft": False, "published_at": NOW.isoformat()}

    gh.on_dispatch = on_dispatch
    return gh


def test_hotfix_needs_a_real_reason_and_a_commit_on_main(installs):
    gh = FakeGitHub()
    with pytest.raises(rt.Fail, match="reason"):
        rt.hotfix(gh, ENV, "", "fix", True)
    with pytest.raises(rt.Fail, match="not on main"):
        rt.hotfix(gh, ENV, D, "Tasks vanish from the board after an update", True)
    assert gh.dispatched == [] and gh.tagged == []


def test_hotfix_validates_publishes_and_updates_canary_then_stable(installs):
    gh = hotfix_github()
    assert rt.hotfix(gh, ENV, "", "Tasks vanish from the board after an update", True) == "v0.5.201"
    assert gh.dispatched[0] == ("ci.yml", "main", {"candidate_sha": C, "previous_release": "v0.5.200",
                                                    "version": "v0.5.201"})
    assert gh.tagged == [("v0.5.201", C, None)]
    assert installs["updated"] == [("canary", "0.5.201"), ("stable", "0.5.201")]


def test_hotfix_without_promote_leaves_stable_and_records_the_reason(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = hotfix_github()
    assert rt.cmd_hotfix(gh, ENV, B, "Tasks vanish from the board after an update", False) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    assert gh.created and gh.created[0]["title"].startswith("Hotfix v0.5.201: Tasks vanish")
    assert gh.closed == [gh.created[0]["number"]]


def test_failed_hotfix_validation_stops_before_tagging(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = FakeGitHub()
    gh.on_dispatch = lambda workflow, ref, inputs: gh.runs.insert(0, run(
        inputs["candidate_sha"], inputs["version"], conclusion="failure", at=datetime.now(timezone.utc)))
    assert rt.cmd_hotfix(gh, ENV, "", "Tasks vanish from the board after an update", True) == 1
    assert gh.tagged == [] and installs["updated"] == []
    assert gh.open_issues and "Release validation ended failure" in gh.created[0]["body"]
