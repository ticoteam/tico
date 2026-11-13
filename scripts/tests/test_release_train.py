"""The release train: which candidate ships and when, when stable may take the canary's approved release (by a person, or
automatically when no person is needed), and that a failure reaches the release-train issue (scripts/release_train.py)."""

from datetime import datetime, timedelta, timezone
import re
import sys
from zoneinfo import ZoneInfo

import pytest

from scripts import release_train as rt

PACIFIC = ZoneInfo("America/Los_Angeles")


def pt(*when):
    """A Los Angeles wall-clock time as UTC: pt(2026, 11, 6, 20, 30) is 04:30 UTC on the 7th (PST)."""
    return datetime(*when, tzinfo=PACIFIC).astimezone(timezone.utc)


A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
NOW = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)  # Thursday 07:30 PST
MORNING = pt(2026, 11, 5, 7, 0)  # half an hour before NOW


def run(sha, version="v0.5.201", previous="v0.5.200", *, status="completed", conclusion="success", at=NOW, id=1):
    return {
        "id": id,
        "display_title": rt.title(sha, version, previous),
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://github.example/run/%d" % id,
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }


class FakeGitHub:
    """main is A <- B <- C (C is the head); v0.5.200 is tagged at A and was published six hours before MORNING."""

    def __init__(self, runs=(), tags=None, head=C, published=MORNING - timedelta(hours=6)):
        self.tags_ = dict(tags if tags is not None else {"v0.5.199": "0" * 40, "v0.5.200": A, "v0.6.0-rc.1": B})
        self.order = [A, B, C]
        self.head_ = head
        self.runs = list(runs)
        self.others = {"ci.yml": [], "hotfix.yml": []}  # runs on other branches/events (recent_runs only)
        self.release_runs = []
        self.releases = (
            {"v0.5.200": dict(released("v0.5.200"), published_at=published.isoformat())} if published else {}
        )
        self.dispatched, self.tagged = [], []
        self.comments, self.closed = [], []  # (number, body) of the comments this run posted; the numbers it closed
        self.issue_list, self.created = [], []  # every issue, and the ones this run filed
        self.events, self.user_comments = {}, {}  # per issue number: its timeline events, and people's comments
        self.ceiling_ids = set()  # the run ids ci.yml's ceiling job stopped
        self.blockers = []
        self.writers = {"chris"}  # the logins with write access
        self.latest = []
        self.latest_ = "v0.5.200"  # GitHub's latest release before this run
        self.on_dispatch = None
        self.numbers = iter(range(100, 100000))
        self.failed = {}  # per run id: the tests its failed jobs name (None: a failure nobody can name)

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

    def failed_tokens(self, run_id):
        return self.failed.get(run_id)

    def ceiling_stopped(self, run):
        return run.get("id") in self.ceiling_ids

    def recent_runs(self, workflow):
        return (list(self.runs) if workflow == "ci.yml" else []) + self.others[workflow]

    def workflow_runs(self, workflow, branch):
        return list(self.release_runs)

    def dispatch(self, workflow, ref, inputs):
        self.dispatched.append((workflow, ref, inputs))
        if self.on_dispatch:
            self.on_dispatch(workflow, ref, inputs)

    def create_tag(self, name, sha):
        if name in self.tags_:
            if self.tags_[name] != sha:
                raise rt.Fail("%s already exists at another commit" % name)
            return False
        self.tags_[name] = sha
        self.tagged.append((name, sha))
        return True

    def tag_commit(self, name):
        return self.tags_.get(name)

    def release(self, tag):
        return self.releases.get(tag)

    def latest_tag(self):
        return self.latest[-1] if self.latest else self.latest_

    def make_latest(self, release):
        self.latest.append(release["tag_name"])

    def find(self, number):
        return next(issue for issue in self.issue_list if issue["number"] == number)

    def add_issue(self, title, *, labels=(), body="", state="open", number=None):
        number = number if number is not None else next(self.numbers)
        issue = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "labels": [{"name": name} for name in labels],
            "html_url": "https://github.example/issues/%d" % number,
        }
        self.issue_list.append(issue)
        return issue

    def issues(self, label, state="open"):
        if label == rt.BLOCKER_LABEL:
            return list(self.blockers)
        names = lambda issue: [item["name"] for item in issue["labels"]]  # noqa: E731
        return [i for i in self.issue_list if label in names(i) and state in ("all", i["state"])]

    def pages(self, path, key=None, limit=10):
        found = re.match(r"issues/(\d+)/(events|comments)", path)
        number = int(found.group(1))
        if found.group(2) == "events":
            return list(self.events.get(number, []))
        return list(self.user_comments.get(number, []))

    def permission(self, login):
        return login in self.writers

    def ensure_label(self, *args):
        pass

    def create_issue(self, title, body, labels):
        issue = self.add_issue(title, labels=labels, body=body)
        self.created.append(issue)
        return issue

    def edit(self, number, body, state):
        issue = self.find(number)
        issue["body"], issue["state"] = body, state

    def last_text(self, issue):
        said = [body for number, body in self.comments if number == issue["number"]]
        return said[-1] if said else issue.get("body") or ""

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.closed.append(number)
        self.find(number)["state"] = "closed"


def filed(gh, label=rt.ISSUE_LABEL):
    """The issues this run filed under a label (the release-train issue by default), oldest first."""
    return [issue for issue in gh.created if label in [item["name"] for item in issue["labels"]]]


def open_issues(gh, label=None):
    return [
        issue
        for issue in gh.issue_list
        if issue["state"] == "open" and (label is None or label in [item["name"] for item in issue["labels"]])
    ]


def approval_of(gh, version):
    return next(issue for issue in gh.issue_list if issue["title"] == rt.approval_title(version))


# An approval body whose notes list a High risk item, as scripts/release_notes.py writes them.
HIGH_RISK_NOTES = (
    "Candidate v0.5.200 at aaaaaaaaaaaa (validated: u)\n\n"
    "Changes since v0.5.199: 0 user-facing, 1 high risk, 0 backend, 0 internal\n\n"
    "## High risk\n\n### #9 Rewrite the task store\n- Risk: high — moves every task row\n"
)


def approval_for(gh, version, *, approved_by=None, held=False, comment_by=None, account_type="User", body=None):
    """An open approval record for version: approved by a login's `approved` label, held, or with a `/approve` comment.
    The approving account is a person (type User) unless account_type says otherwise. Its notes list no High risk item
    unless `body` says otherwise."""
    labels = [rt.APPROVAL_LABEL] + ([rt.APPROVED] if approved_by else []) + ([rt.HOLD] if held else [])
    issue = gh.add_issue(rt.approval_title(version), labels=labels, body=body or "notes for %s" % version)
    if approved_by:
        gh.events[issue["number"]] = [
            {
                "event": "labeled",
                "label": {"name": rt.APPROVED},
                "actor": {"login": approved_by, "type": account_type},
            }
        ]
    if comment_by:
        gh.user_comments[issue["number"]] = [{"body": "/approve", "user": {"login": comment_by, "type": account_type}}]
    return issue


def released(tag):
    return {
        "id": 1,
        "tag_name": tag,
        "draft": False,
        "published_at": NOW.isoformat(),
        "html_url": "https://github.example/r",
    }


def publishes(gh):
    """release.yml, dispatched on the tag, publishes the release at once."""

    def on_dispatch(workflow, ref, inputs):
        if workflow == "release.yml":
            gh.releases[ref] = released(ref)
        elif workflow == "ci.yml":
            gh.runs.insert(
                0,
                run(
                    inputs["candidate_sha"],
                    inputs["version"],
                    inputs["previous_release"],
                    status="queued",
                    conclusion=None,
                    at=datetime.now(timezone.utc),
                    id=50,
                ),
            )

    gh.on_dispatch = on_dispatch
    return gh


COMMANDS = []


@pytest.fixture(autouse=True)
def clean_log(monkeypatch):
    monkeypatch.setattr(rt, "LOG", [])
    # The tests' world is a 0.5 main (v0.5.200); the 0.6 line has tests of its own (test_a_new_line_...).
    monkeypatch.setattr(rt, "LINE", (0, 5))
    monkeypatch.setattr(rt, "sleep", lambda seconds: None)
    COMMANDS.clear()
    monkeypatch.setattr(rt, "run_command", lambda argv, timeout=180: COMMANDS.append(argv) or (0, ""))
    # The notes module is tested on its own (test_release_notes.py); here they are a marker naming the range.
    monkeypatch.setattr(rt.release_notes, "build", lambda api, base, head: "NOTES %s..%s" % (base, head))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


ENV = {
    "CANARY_URL": "https://canary.acme.example",
    "CANARY_UPDATE_KEY": "k1",
    "STABLE_URL": "https://stable.acme.example",
    "STABLE_UPDATE_KEY": "k2",
    "RELEASE_TRAINS": "on",
    # Most tests are about a person's approval; AUTO turns it off (the default when the variable is not set).
    "RELEASE_NEEDS_PERSON": "on",
}
AUTO = {key: value for key, value in ENV.items() if key != "RELEASE_NEEDS_PERSON"}


# ---- loading the scripts' modules ----


# ---- versions and the clock ----


def test_versions_order_numerically_and_skip_prereleases():
    tags = {"v0.5.9", "v0.5.10", "v0.6.0-rc.1", "latest", "v1.0"}
    assert rt.newest_stable(tags) == "v0.5.10"
    assert rt.next_patch("v0.5.10") == "v0.5.11"
    assert rt.newest_stable({"v0.6.0-rc.1"}) is None


def test_validation_title_round_trips_for_release_yml():
    assert rt.parse_title(rt.title(C, "v0.5.201", "v0.5.200")) == (C, "v0.5.201", "v0.5.200")
    assert rt.parse_title("Release train check") is None


# ---- what the train does ----


def plan(gh, now=NOW, finished=None, manual=False):
    return rt.plan_step(gh, now, finished, manual)


@pytest.mark.parametrize(
    "now", [NOW, pt(2026, 11, 6, 3, 0), pt(2026, 11, 7, 23, 30)], ids=["morning", "3am", "late-saturday"]
)
def test_there_are_no_windows_a_green_candidate_ships_at_any_hour_right_after_the_last_release(now):
    # v0.5.200 was published a minute before C's validation finished: the next release goes out at once.
    gh = FakeGitHub([run(C, at=now)], published=now - timedelta(minutes=1))
    assert plan(gh, now=now)["action"] == "ship"
    # Main's head with no validation yet is validated at once, whatever the hour.
    assert plan(FakeGitHub(published=now - timedelta(minutes=1)), now=now)["action"] == "dispatch"


def release_run(status="completed", conclusion="failure", at=NOW - timedelta(minutes=40), id=70):
    return {
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://github.example/release/%d" % id,
        "created_at": at.isoformat(),
    }


def test_an_unpublished_tag_waits_for_its_release_run_then_runs_it_once_more_then_asks_for_a_person():
    gh = FakeGitHub([run(C)])
    gh.tags_["v0.5.201"] = B
    gh.release_runs = [release_run(status="in_progress", conclusion=None)]
    assert plan(gh)["action"] == "none" and "running" in plan(gh)["why"]
    gh.release_runs = [release_run(at=NOW - timedelta(minutes=10))]
    assert plan(gh)["action"] == "none" and "runs again at 07:50 PST" in plan(gh)["why"]
    gh.release_runs = [release_run()]
    again = plan(gh)
    assert (again["action"], again["sha"], again["version"], again["republish"]) == ("ship", B, "v0.5.201", True)
    gh.release_runs = [release_run(id=71), release_run()]
    stuck = plan(gh)
    assert stuck["action"] == "none" and rt.problems(stuck) == [stuck["stuck"]] and "release/71" in stuck["stuck"]


def test_nothing_to_do_when_main_has_not_moved():
    assert plan(FakeGitHub(head=A))["action"] == "none"


def test_the_first_green_candidate_of_the_next_release_ships():
    picked = plan(FakeGitHub([run(B, id=2, at=MORNING + timedelta(minutes=20))]))
    assert (picked["action"], picked["sha"], picked["version"]) == ("ship", B, "v0.5.201")
    # However long ago it went green: an older commit ships too, and the next push validates main's head.
    assert plan(FakeGitHub([run(C, at=MORNING - timedelta(hours=2))]))["action"] == "ship"
    assert plan(FakeGitHub([run(B, at=MORNING - timedelta(hours=2))]))["sha"] == B
    # A green run of another release (validated from an older one) ships nothing.
    assert plan(FakeGitHub([run(C, "v0.5.200", "v0.5.199", at=MORNING)]))["action"] != "ship"


HELD = "a full validation already ran in the last 24h (%s, %s): a person runs Release train by hand to validate again"


@pytest.mark.parametrize(
    "runs, action, mode",
    [
        ([run(D)], "wait", None),  # not on main: no anchor, but its full run is the day's one
        ([run(C, "v0.5.201", "v0.5.199")], "dispatch", "incremental"),  # an upgrade from another release: an anchor
        ([run(C, "v0.5.202", "v0.5.201")], "dispatch", "incremental"),  # another version: an anchor
        ([run(B, conclusion="cancelled")], "wait", None),  # cancelled: no anchor, and the day's full run holds
    ],
    ids=["off-main", "other-previous", "other-version", "older-cancelled"],
)
def test_only_a_green_run_of_main_anchors_a_candidate_and_any_full_run_holds_the_day(runs, action, mode):
    picked = plan(FakeGitHub(runs))
    assert (picked["action"], picked.get("mode")) == (action, mode)


def test_one_validation_at_a_time():
    assert plan(FakeGitHub([run(C, status="in_progress", conclusion=None)]))["action"] == "wait"
    gh = FakeGitHub()
    gh.others["ci.yml"] = [{"status": "queued", "html_url": "hotfix-validation"}]
    assert "hotfix-validation" in plan(gh)["why"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_red_or_cancelled_head_waits_for_a_push_is_reported_and_a_manual_run_validates_it_again(conclusion):
    waiting = plan(FakeGitHub([run(C, conclusion=conclusion, id=4)]))
    assert waiting["action"] == "wait"
    assert rt.problems(waiting) == [
        "Candidate v0.5.201 at %s ended %s: https://github.example/run/4" % (C[:12], conclusion)
    ]
    forced = plan(FakeGitHub([run(C, conclusion=conclusion, id=4)]), manual=True)
    assert forced["action"] == "dispatch" and rt.problems(forced) == []


def test_a_red_head_waits_for_a_push_and_a_red_older_commit_holds_the_day_until_a_person_runs_it():
    assert plan(FakeGitHub([run(C, conclusion="failure")]))["action"] == "wait"
    # An older commit's red run naming no failure is no anchor, so its full run holds the day; a manual run validates.
    older = plan(FakeGitHub([run(B, conclusion="failure")]))
    assert (older["action"], older["held"]) == ("wait", HELD % ("https://github.example/run/1", "failure"))
    forced = plan(FakeGitHub([run(B, conclusion="failure")]), manual=True)
    assert (forced["action"], forced["mode"]) == ("dispatch", "full")
    # Hours later, a red head still waits for a push: the same head is not validated again.
    later = pt(2026, 11, 5, 14, 5)
    assert plan(FakeGitHub([run(C, conclusion="failure", at=MORNING)]), now=later)["action"] == "wait"


def test_a_finished_red_candidate_of_the_next_release_is_reported():
    finished = {"conclusion": "failure", "title": rt.title(B, "v0.5.201", "v0.5.200"), "url": "red-url"}
    assert plan(FakeGitHub([run(B, conclusion="failure")]), finished=finished)["red"]["sha"] == B
    stale = dict(finished, title=rt.title(B, "v0.5.200", "v0.5.199"))
    assert plan(FakeGitHub(), finished=stale)["red"] is None
    assert plan(FakeGitHub(), finished=dict(finished, conclusion="cancelled"))["red"]["conclusion"] == "cancelled"
    assert plan(FakeGitHub(), finished=dict(finished, conclusion="success"))["red"] is None


CEILING_HOLD = "Full runs: ceiling reached (10/day) · next after 00:00 UTC"


def test_a_run_the_ceiling_stopped_holds_the_train_and_is_never_a_failure():
    stopped = run(C, conclusion="failure", id=9, at=NOW - timedelta(minutes=5))
    gh = FakeGitHub([stopped])
    gh.ceiling_ids = {9}
    for manual in (False, True):
        held = plan(gh, manual=manual)
        assert held["action"] == "wait" and held["why"].endswith(CEILING_HOLD)
        assert (held["red"], held["held"], rt.problems(held)) == (None, None, [])
    # Its failed finish, handed to the train job by the workflow_run event, is no red candidate either.
    finished = {"conclusion": "failure", "title": rt.title(C, "v0.5.201", "v0.5.200"), "url": stopped["html_url"]}
    assert plan(gh, finished=finished)["red"] is None


def test_a_real_red_on_main_is_still_reported_beside_the_ceiling_hold():
    gh = FakeGitHub(
        [run(C, conclusion="failure", id=4, at=NOW - timedelta(minutes=20)), run(C, conclusion="failure", id=9)]
    )
    gh.ceiling_ids = {9}
    red = plan(gh)
    assert red["action"] == "wait" and red["why"].endswith(CEILING_HOLD)
    assert rt.problems(red) == ["Candidate v0.5.201 at %s ended failure: https://github.example/run/4" % C[:12]]


def test_only_a_run_the_ceiling_stopped_on_this_utc_day_is_a_stop():
    today, earlier = run(C, conclusion="failure", id=9), run(C, conclusion="failure", id=8, at=NOW - timedelta(days=1))
    gh = FakeGitHub([today, earlier])
    gh.ceiling_ids = {8, 9}
    assert [item["id"] for item in rt.ceiling_stops(gh, [today, earlier], NOW)] == [9]


def test_the_ceiling_job_counts_as_a_stop_only_on_its_own_message(monkeypatch):
    gh = rt.GitHub("ticoteam/tico", "token")
    jobs = [{"id": 5, "name": "ceiling", "conclusion": "failure"}]
    monkeypatch.setattr(gh, "pages", lambda path, key=None, limit=10: jobs)
    monkeypatch.setattr(gh, "job_log", lambda job_id: "##[error]10 full runs today; ceiling reached")
    assert gh.ceiling_stopped({"id": 9})
    monkeypatch.setattr(gh, "job_log", lambda job_id: "##[error]gh api: HTTP 502")
    assert not gh.ceiling_stopped({"id": 9})
    jobs[0]["conclusion"] = "skipped"
    assert not gh.ceiling_stopped({"id": 9})


class FakeCheckout:
    """The checkout's answers: the tokens a range selects (None when its diff cannot be read) and the plan it makes."""

    def __init__(self, since=None, plan=None):
        self.since_, self.plan_text = since, plan
        self.calls = []
        self.previous_changed = None  # the flag the last plan was asked for

    def tokens_since(self, base, head):
        self.calls.append(("tokens_since", base, head))
        return self.since_

    def plan(self, base, head, carried, previous_changed=False):
        self.calls.append(("plan", base, head, set(carried)))
        if self.plan_text is None:
            raise RuntimeError("the anchor is not in the checkout")
        self.previous_changed = previous_changed
        return self.plan_text


PLAN_TEXT = '{"jobs":[{"name":"python-1","part":"python","files":["backend/tests/test_b.py"]}]}'


def out(sha, **fields):
    """A validation of the release that is out (v0.5.200, from v0.5.199). A green validation of the next release ships
    at once, so the green anchors the next candidates build on are the release's that went out before them."""
    return run(sha, "v0.5.200", "v0.5.199", **fields)


def red_then_anchor(anchor_at=MORNING - timedelta(hours=1), red_at=NOW - timedelta(hours=1)):
    """main is A <- B <- C <- D and D is the head. B was green earlier, as the release now out (the anchor); C went red
    after it. Newest first, as GitHub lists them."""
    gh = FakeGitHub([run(C, id=3, conclusion="failure", at=red_at), out(B, id=2, at=anchor_at)], head=D)
    gh.order = [A, B, C, D]
    return gh


def test_a_red_validation_reruns_incrementally_once_a_commit_selects_what_failed():
    # C went red naming its failure, so C is the anchor: the train reruns from it, carrying what failed. No full run.
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", C)
    assert picked["carried"] == ["backend/tests/test_b.py"] and picked["anchor_previous"] == "v0.5.200"
    assert "carries backend/tests/test_b.py" in picked["why"] and "incrementally on %s" % C[:12] in picked["why"]
    assert ("tokens_since", C, D) in checkout.calls and ("plan", C, D, {"backend/tests/test_b.py"}) in checkout.calls


def test_a_red_validation_waits_and_says_so_while_no_commit_selects_what_failed():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"ui/tests/threads.cjs"}, plan=PLAN_TEXT))
    assert picked["action"] == "wait" and "the train waits" in picked["why"]
    assert "backend/tests/test_b.py" in picked["why"] and gh.dispatched == []


def test_a_red_naming_no_failure_reruns_from_the_green_anchor_without_a_second_full():
    # C's log names nothing, so C is no anchor; B (green, within the day) is. Its next candidate is incremental on B:
    # a full run is never planned while an anchor is under a day old.
    gh = red_then_anchor()
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", B)
    assert ("plan", B, D, set()) in checkout.calls


def test_a_stale_anchor_runs_the_full_validation_once_a_day():
    # The green anchor and the red validation after it are both over a day old: no anchor is left, so one full run.
    gh = red_then_anchor(anchor_at=NOW - timedelta(hours=26), red_at=NOW - timedelta(hours=25))
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT))
    assert (picked["action"], picked["mode"]) == ("dispatch", "full")
    assert "no full validation in the last 24 hours" in picked["why"]


def test_an_incremental_plan_that_cannot_be_made_waits_rather_than_running_a_second_full_validation():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=None))
    assert picked["action"] == "wait" and gh.dispatched == []
    assert "cannot be made" in picked["why"] and "the anchor is not in the checkout" in picked["why"]


def test_without_a_checkout_the_validation_is_incremental_on_the_anchor_so_the_guard_dispatches_it():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", C)
    assert picked["carried"] == ["backend/tests/test_b.py"]


def test_a_cancelled_full_is_no_anchor_and_holds_the_day_so_the_train_waits_for_a_person():
    gh = FakeGitHub([run(C, id=3, conclusion="cancelled", at=NOW - timedelta(hours=1))], head=D)
    gh.order = [A, B, C, D]
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since=None, plan=PLAN_TEXT))
    assert (picked["action"], picked["held"]) == ("wait", HELD % ("https://github.example/run/3", "cancelled"))
    assert gh.dispatched == [] and rt.problems(picked) == [picked["held"]]


def test_a_red_full_naming_no_failure_holds_the_day_and_a_manual_run_validates_again():
    # (c): an unnamed red full is no anchor either, so it waits for a person; a manual run still dispatches in full.
    gh = FakeGitHub([run(C, id=3, conclusion="failure", at=NOW - timedelta(hours=1))], head=D)
    gh.order = [A, B, C, D]
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["held"]) == ("wait", HELD % ("https://github.example/run/3", "failure"))
    assert gh.dispatched == []
    forced = rt.plan_step(gh, NOW, manual=True, checkout=checkout)
    assert (forced["action"], forced["mode"], forced["anchor"]) == ("dispatch", "full", None)


@pytest.mark.parametrize(
    "runs, anchor, failed",
    [
        (
            [
                run(C, id=3, conclusion="cancelled", at=NOW - timedelta(hours=1)),
                out(B, id=2, at=MORNING - timedelta(hours=1)),
            ],
            B,
            {},
        ),
    ],
    ids=["cancelled"],
)
def test_a_second_full_is_never_planned_while_an_anchor_under_a_day_old_exists(runs, anchor, failed):
    gh = FakeGitHub(runs, head=D)
    gh.order = [A, B, C, D]
    gh.failed = failed
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT))
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", anchor)


def test_an_incremental_validation_names_its_anchor_and_plan_and_a_full_one_names_neither():
    gh = FakeGitHub()
    rt.dispatch_candidate(gh, C, "v0.5.201", "v0.5.200", timeout=0)
    rt.dispatch_candidate(
        gh,
        C,
        "v0.5.201",
        "v0.5.200",
        timeout=0,
        anchor=B,
        carried={"backend/tests/test_b.py"},
        anchor_previous="v0.5.199",
    )
    assert gh.dispatched[0][2] == {"candidate_sha": C, "previous_release": "v0.5.200", "version": "v0.5.201"}
    assert gh.dispatched[1][2] == {
        "candidate_sha": C,
        "previous_release": "v0.5.200",
        "version": "v0.5.201",
        "anchor": B,
        "carried": '["backend/tests/test_b.py"]',
        "anchor_previous": "v0.5.199",
    }
    assert any("(incremental on %s)" % B in line for line in rt.LOG)


def test_an_incremental_title_keeps_the_prefix_release_yml_finds_and_names_its_anchor():
    text = rt.title(C, "v0.5.201", "v0.5.200", anchor=B)
    assert text == "Validate %s as v0.5.201 from v0.5.200 (incremental on %s)" % (C, B)
    assert text.startswith("Validate %s as v0.5.201 from " % C)
    assert rt.parse_title(text) == (C, "v0.5.201", "v0.5.200")
    assert rt.parse_anchor(text) == B
    assert rt.parse_anchor(rt.title(C, "v0.5.201", "v0.5.200")) is None


def test_validation_records_say_which_runs_are_incremental_and_only_full_green_ones_anchor():
    full = rt.validation_record(run(B, id=2))
    incremental = rt.validation_record(dict(run(C, id=3), display_title=rt.title(C, "v0.5.201", "v0.5.200", anchor=B)))
    assert (full["incremental"], full["sha"], full["id"]) == (False, B, 2)
    assert (incremental["incremental"], incremental["sha"], incremental["id"]) == (True, C, 3)
    assert full["previous"] == incremental["previous"] == "v0.5.200"


def test_a_red_superseded_by_a_later_green_validation_is_neither_carried_nor_waited_for():
    # main is A <- B <- C <- D (the head). B is this release's red validation naming its failure, the newest full one
    # within the day, so it is the anchor. C is an earlier release's green incremental run after B: it supersedes B's
    # red, so nothing is carried and nothing waits.
    earlier = dict(
        run(C, version="v0.5.200", previous="v0.5.199", id=3, at=MORNING - timedelta(minutes=30)),
        display_title=rt.title(C, "v0.5.200", "v0.5.199", anchor=A),
    )
    gh = FakeGitHub(
        [
            earlier,
            run(B, id=2, conclusion="failure", at=MORNING - timedelta(hours=2)),
            run(A, id=1, at=MORNING - timedelta(hours=3)),
        ],
        head=D,
    )
    gh.order = [A, B, C, D]
    gh.failed = {2: {"backend/tests/test_b.py"}}
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", C)
    assert checkout.calls == [("plan", C, D, set())]  # the green incremental C anchors: nothing carried, no red read
    assert "carries" not in picked["why"] and "the train waits" not in picked["why"]


def test_an_anchor_of_an_earlier_release_runs_the_product_part_when_the_previous_release_moved():
    # The newest tag is v0.5.200; the anchor validated v0.5.200 from v0.5.199, a green full run two hours ago.
    gh = FakeGitHub([run(B, version="v0.5.200", previous="v0.5.199", id=2, at=NOW - timedelta(hours=2))])
    checkout = FakeCheckout(plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", B)
    assert checkout.previous_changed is True
    assert "the product part runs: the previous release is v0.5.200, the anchor's was v0.5.199" in picked["why"]
    # The same previous release (a red full run of the next release that names its failure): not forced.
    gh = FakeGitHub([run(B, id=2, conclusion="failure", at=MORNING - timedelta(hours=1))])
    gh.failed = {2: {"backend/tests/test_b.py"}}
    same = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    assert rt.plan_step(gh, NOW, checkout=same)["mode"] == "incremental"
    assert same.previous_changed is False


# ---- the guard ----


def guard(gh, env, tmp_path, monkeypatch, now=NOW):
    out = tmp_path / "out"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rt.cmd_guard(gh, env, now)
    return dict(line.split("=", 1) for line in out.read_text().split())


def test_guard_runs_the_train_job_only_when_it_has_work(tmp_path, monkeypatch):
    on = {"RELEASE_TRAINS": "on"}
    push = dict(on, GITHUB_EVENT_NAME="push")
    backstop = dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON)
    # Main moved past the newest release: a push and the backstop both run the job, at any hour.
    for now in (NOW, pt(2026, 11, 6, 2, 0)):
        assert guard(FakeGitHub(), push, tmp_path, monkeypatch, now=now) == {"go": "true"}
        assert guard(FakeGitHub(), backstop, tmp_path, monkeypatch, now=now) == {"go": "true"}
    # Nothing new on main: the backstop idles.
    assert guard(FakeGitHub(head=A), backstop, tmp_path, monkeypatch) == {"go": "false"}
    busy = FakeGitHub([run(C, status="in_progress", conclusion=None)])
    assert guard(busy, push, tmp_path, monkeypatch)["go"] == "false"
    red = {
        "GITHUB_EVENT_NAME": "workflow_run",
        "RUN_EVENT": "workflow_dispatch",
        "RUN_CONCLUSION": "failure",
        "RUN_TITLE": rt.title(C, "v0.5.201", "v0.5.200"),
        "RUN_URL": "u",
    }
    assert guard(FakeGitHub([run(C, conclusion="failure")]), dict(on, **red), tmp_path, monkeypatch)["go"] == "true"


def test_the_backstop_runs_the_job_for_an_approved_release_that_stable_does_not_run_yet(tmp_path, monkeypatch):
    backstop = {
        "RELEASE_TRAINS": "on",
        "RELEASE_NEEDS_PERSON": "on",
        "GITHUB_EVENT_NAME": "schedule",
        "SCHEDULE": rt.BACKSTOP_CRON,
    }

    def idle():  # main has not moved: nothing to ship, only the approval can call the job
        return FakeGitHub([run(C)], head=A)

    assert guard(idle(), backstop, tmp_path, monkeypatch) == {"go": "false"}, "no approval record"
    labelled = idle()
    approval_for(labelled, "v0.5.200", approved_by="mallory")  # approved by someone without write access
    assert guard(labelled, backstop, tmp_path, monkeypatch) == {"go": "false"}
    approved = idle()
    approval_for(approved, "v0.5.200", approved_by="chris")
    assert guard(approved, backstop, tmp_path, monkeypatch) == {"go": "true"}
    held = idle()
    approval_for(held, "v0.5.200", approved_by="chris", held=True)
    assert guard(held, backstop, tmp_path, monkeypatch) == {"go": "false"}
    # With RELEASE_NEEDS_PERSON off (or unset), a release with no High risk item is approved automatically.
    auto = {key: value for key, value in backstop.items() if key != "RELEASE_NEEDS_PERSON"}
    assert guard(labelled, auto, tmp_path, monkeypatch) == {"go": "true"}
    risky = idle()
    approval_for(risky, "v0.5.200", body=HIGH_RISK_NOTES)
    assert guard(risky, auto, tmp_path, monkeypatch) == {"go": "false"}
    # While RELEASE_HOLD or a release-blocker holds stable, an approved release does not run the job every 20 minutes.
    assert guard(labelled, dict(auto, RELEASE_HOLD="billing fix first"), tmp_path, monkeypatch) == {"go": "false"}
    labelled.blockers = [{"number": 7, "title": "Tasks vanish"}]
    assert guard(labelled, auto, tmp_path, monkeypatch) == {"go": "false"}


def test_guard_reports_a_red_head_from_any_event_once(installs, tmp_path, monkeypatch):
    gh = FakeGitHub([run(C, conclusion="cancelled", id=4)])
    for event in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_EVENT_NAME": "schedule", "SCHEDULE": rt.BACKSTOP_CRON}):
        assert guard(gh, dict(event, RELEASE_TRAINS="on"), tmp_path, monkeypatch)["go"] == "true"
    assert rt.cmd_step(gh, dict(ENV, GITHUB_EVENT_NAME="push"), NOW) == 1
    assert len(gh.created) == 1 and "ended cancelled" in gh.created[0]["body"]
    # Reported: the next push and the backstop stay quiet, and a train job that runs anyway does not post it again.
    assert guard(gh, {"GITHUB_EVENT_NAME": "push", "RELEASE_TRAINS": "on"}, tmp_path, monkeypatch)["go"] == "false"
    rt.cmd_step(gh, dict(ENV, GITHUB_EVENT_NAME="push"), NOW)
    assert len(gh.created) == 1 and gh.comments == []


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
        "canary": {
            "state": "healthy",
            "running": "0.5.200",
            "to": "0.5.200",
            "history": [{"version": "0.5.200", "since": (NOW - timedelta(hours=5)).isoformat()}],
        },
        "stable": {
            "state": "healthy",
            "running": "0.5.199",
            "to": "0.5.199",
            "history": [{"version": "0.5.199", "since": (NOW - timedelta(days=1)).isoformat()}],
        },
        "updated": [],
        "paths": [],
        "outcome": "healthy",
        "down": set(),
    }

    def call(target, method, path, body=None, key=True):
        name = target["name"]
        state["paths"].append((name, method, path, key))
        if name in state["down"]:
            return 0, {"error": "URLError"}
        if (method, path) == ("POST", rt.UPDATE_PATH):
            version = body["version"]
            state["updated"].append((name, version))
            ok = state["outcome"] == "healthy"
            state[name] = dict(
                state[name],
                to=version,
                state=state["outcome"],
                running=version if ok else state[name]["running"],
                history=state[name]["history"] + ([{"version": version, "since": NOW.isoformat()}] if ok else []),
            )
            return 200, {"state": "pulling", "to": version, "running": state[name]["running"]}
        return 200, dict(state[name])

    monkeypatch.setattr(rt.rollout, "call", call)
    return state


def test_updates_use_only_the_update_key_routes(installs):
    rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    assert installs["updated"] == [("canary", "0.5.201")]
    assert {path for _, _, path, _ in installs["paths"]} <= {rt.UPDATE_PATH, rt.UPDATE_PATH + "/check"}
    assert all(key for *_, key in installs["paths"])


def test_a_rolled_back_or_unreadable_update_fails(installs):
    installs["outcome"] = "rolled_back"
    with pytest.raises(rt.Fail, match="rolled_back"):
        rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    installs["down"].add("stable")
    with pytest.raises(rt.Fail, match="cannot be read"):
        rt.update_install(rt.install("STABLE", ENV), "v0.5.201")


def test_a_near_instant_failure_is_judged_at_once(monkeypatch):
    polls = []
    answers = iter(
        [
            {"state": "healthy", "running": "0.5.200", "to": "0.5.200", "message": ""},
            {"state": "failed", "running": "0.5.200", "to": "0.5.201", "message": "pull: manifest unknown"},
        ]
    )

    def call(target, method, path, body=None):
        if method == "POST":
            return 200, {"ok": True}
        polls.append(path)
        return 200, next(answers)

    monkeypatch.setattr(rt.rollout, "call", call)
    with pytest.raises(rt.Fail, match="manifest unknown"):
        rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    assert len(polls) == 2  # the snapshot, then the first poll


def test_an_earlier_updates_outcome_is_not_judged(monkeypatch):
    """A rollback to the same version left from before this request, still shown until the updater picks it up."""
    stale = {"state": "rolled_back", "running": "0.5.200", "to": "0.5.201", "message": "earlier"}
    answers = iter(
        [
            stale,
            stale,
            {"state": "pulling", "running": "0.5.200", "to": "0.5.201"},
            None,
            {"state": "healthy", "running": "0.5.201", "to": "0.5.201"},
        ]
    )

    def call(target, method, path, body=None):
        if method == "POST":
            return 200, {"ok": True}  # a hub that does not echo the updater's status
        answer = next(answers)
        return (200, answer) if answer else (502, {})

    monkeypatch.setattr(rt.rollout, "call", call)
    rt.update_install(rt.install("CANARY", ENV), "v0.5.201")
    assert any("healthy" in line for line in rt.LOG)
    # Once this update is seen under way, a rollback fails it.
    answers = iter([stale, {"state": "restarting", "running": "0.5.200", "to": "0.5.201"}, stale])
    with pytest.raises(rt.Fail, match="rolled_back"):
        rt.update_install(rt.install("CANARY", ENV), "v0.5.201")


# ---- the promotion gate ----


def gate(**overrides):
    args = dict(
        version="0.5.201",
        canary_status={"state": "healthy", "running": "0.5.201", "history": []},
        approval_waits=[],
        hold="",
        blockers=[],
    )
    args.update(overrides)
    return rt.promotion_gate(**args)


@pytest.mark.parametrize(
    "status, why",
    [
        (None, "cannot be read"),
        ({"state": "rolled_back", "running": "0.5.201", "message": "health check"}, "rolled_back"),
        ({"state": "", "running": "0.5.201"}, "unknown"),
        ({"state": "healthy", "running": "0.5.200"}, "runs 0.5.200"),
    ],
)
def test_promotion_gate_faults_on_what_it_cannot_tell(status, why):
    _, faults = gate(canary_status=status)
    assert any(why in f for f in faults)


# ---- promote: the canary's approved release to stable ----


@pytest.mark.parametrize(
    "setup, why",
    [
        (lambda gh: None, "no open approval"),
        (lambda gh: approval_for(gh, "v0.5.200"), "not approved yet"),
        (lambda gh: approval_for(gh, "v0.5.200", approved_by="mallory"), "not approved yet"),
        (lambda gh: approval_for(gh, "v0.5.200", comment_by="mallory"), "not approved yet"),
        (lambda gh: approval_for(gh, "v0.5.200", approved_by="chris", held=True), "on hold"),
    ],
    ids=["no-record", "unapproved", "label-by-non-writer", "comment-by-non-writer", "held"],
)
def test_promote_waits_unless_someone_with_write_access_approved_and_nothing_holds_it(installs, setup, why):
    gh = FakeGitHub()
    setup(gh)
    rt.promote(gh, ENV)
    assert installs["updated"] == [] and any(why in line for line in rt.LOG)


def test_promote_updates_stable_to_the_approved_canary_release_and_closes_its_approval(installs):
    gh = FakeGitHub()
    record = approval_for(gh, "v0.5.200", approved_by="chris")
    rt.promote(gh, ENV)
    assert installs["updated"] == [("stable", "0.5.200")]
    assert record["state"] == "closed" and record["number"] in gh.closed
    assert any("approved by @chris" in line for line in rt.LOG)
    assert gh.comments == [(record["number"], "Promoted to stable (TIDY), approved by @chris.")]


def test_a_comment_approve_by_someone_with_write_access_counts(installs):
    gh = FakeGitHub()
    approval_for(gh, "v0.5.200", comment_by="chris")
    rt.promote(gh, ENV)
    assert installs["updated"] == [("stable", "0.5.200")]


@pytest.mark.parametrize(
    "login, account_type",
    [("release-app", "Bot"), ("helper[bot]", "User")],
    ids=["bot-typed-account", "bot-login"],
)
@pytest.mark.parametrize("how", ["label", "comment"])
def test_a_bot_or_app_with_write_access_never_approves(installs, login, account_type, how):
    gh = FakeGitHub()
    gh.writers.add(login)  # write access does not make an account a person
    if how == "label":
        approval_for(gh, "v0.5.200", approved_by=login, account_type=account_type)
    else:
        approval_for(gh, "v0.5.200", comment_by=login, account_type=account_type)
    rt.promote(gh, ENV)
    assert installs["updated"] == [] and any("not approved yet" in line for line in rt.LOG)


def test_a_bots_newer_approve_comment_does_not_hide_a_persons_earlier_one(installs):
    gh = FakeGitHub()
    gh.writers.add("release-app")
    record = approval_for(gh, "v0.5.200", comment_by="chris")
    gh.user_comments[record["number"]].append({"body": "/approve", "user": {"login": "release-app", "type": "Bot"}})
    rt.promote(gh, ENV)
    assert installs["updated"] == [("stable", "0.5.200")]


def test_promote_holds_on_release_hold_blockers_and_faults_on_unknowns(installs):
    gh = FakeGitHub()
    approval_for(gh, "v0.5.200", approved_by="chris")
    rt.promote(gh, dict(ENV, RELEASE_HOLD="yes"))
    assert installs["updated"] == []
    gh.blockers = [{"number": 7, "title": "Tasks vanish"}]
    rt.promote(gh, ENV)
    assert installs["updated"] == [] and any("release-blocker #7" in line for line in rt.LOG)
    gh.blockers = []
    installs["down"].add("stable")
    with pytest.raises(rt.Fail, match="holding"):
        rt.promote(gh, ENV)
    installs["down"].clear()
    installs["canary"].update(state="rolled_back", message="health check")
    with pytest.raises(rt.Fail, match="rolled_back"):
        rt.promote(gh, ENV)
    assert installs["updated"] == []


def test_promote_never_moves_stable_backwards(installs):
    gh = FakeGitHub()
    approval_for(gh, "v0.5.198", approved_by="chris")
    installs["canary"]["running"] = "0.5.198"
    rt.promote(gh, ENV)
    assert installs["updated"] == []


def test_promote_skips_without_keys(installs):
    rt.promote(FakeGitHub(), dict(ENV, STABLE_UPDATE_KEY=" "))
    assert installs["updated"] == [] and any("STABLE_UPDATE_KEY" in line for line in rt.LOG)


# ---- 0.6 starts fresh: no 0.6 main ships as a 0.5 patch, and no 0.5 install is moved to 0.6 ----


def test_a_new_line_is_never_cut_as_a_patch_of_the_old_one_and_resumes_once_tagged(monkeypatch):
    monkeypatch.setattr(rt, "LINE", (0, 6))
    gh = FakeGitHub([run(C)])  # a green v0.5.201 candidate of main: it never ships
    held = plan(gh)
    assert held["action"] == "wait" and "tag v0.6.0 by hand" in held["held"]
    assert rt.problems(held) == [held["held"]]  # reported once on the release-train issue
    gh.tags_["v0.6.0"] = B
    gh.releases["v0.6.0"] = released("v0.6.0")
    resumed = plan(gh)
    assert resumed["version"] == "v0.6.1" and resumed["action"] == "dispatch"


@pytest.mark.parametrize(
    "have, want, crosses",
    [
        ("", "0.6.0", False),
    ],
)
def test_crossing_the_fresh_start(have, want, crosses):
    assert rt.crosses_fresh_start(have, want) is crosses


def test_an_install_on_0_5_is_never_updated_to_0_6(installs):
    installs["stable"]["running"] = "0.5.216"
    with pytest.raises(rt.Fail, match="0.6 starts fresh"):
        rt.update_install(rt.install("STABLE", ENV), "v0.6.0")
    assert installs["updated"] == []


def test_promote_leaves_a_0_5_stable_where_it_is_when_the_canary_runs_0_6(installs):
    gh = FakeGitHub()
    approval_for(gh, "v0.6.0", approved_by="chris")
    installs["canary"]["running"] = "0.6.0"
    installs["stable"]["running"] = "0.5.216"
    rt.promote(gh, ENV)
    assert installs["updated"] == [] and any("0.6 starts fresh" in line for line in rt.LOG)


# ---- approved automatically: no person needed (RELEASE_NEEDS_PERSON off, no High risk item) ----


def test_a_release_with_no_high_risk_item_is_approved_automatically_and_its_issue_says_so(installs):
    gh = FakeGitHub()
    record = approval_for(gh, "v0.5.200")  # nobody approved it
    rt.promote(gh, AUTO)
    assert installs["updated"] == [("stable", "0.5.200")]
    assert record["state"] == "closed"
    assert gh.comments == [
        (
            record["number"],
            "Promoted to stable (TIDY), approved automatically (no high-risk items; RELEASE_NEEDS_PERSON is off).",
        )
    ]
    assert any(rt.AUTO_APPROVED in line for line in rt.LOG)


@pytest.mark.parametrize(
    "body, env, why",
    [
        (HIGH_RISK_NOTES, AUTO, "its notes list a High risk item"),
        ("notes\n" + rt.release_notes.CUT_SHORT, AUTO, "its notes were cut short"),
        (None, dict(AUTO, RELEASE_NEEDS_PERSON="on"), "RELEASE_NEEDS_PERSON is on"),
    ],
    ids=["high-risk", "cut-short", "needs-person-on"],
)
def test_a_high_risk_release_or_needs_person_on_still_waits_for_a_person(installs, body, env, why):
    gh = FakeGitHub()
    record = approval_for(gh, "v0.5.200", body=body)
    rt.promote(gh, env)
    assert installs["updated"] == [] and record["state"] == "open"
    assert any("not approved yet (a person must approve: %s" % why in line for line in rt.LOG)
    # A person's approval takes it, and the issue names them.
    gh.events[record["number"]] = [
        {"event": "labeled", "label": {"name": rt.APPROVED}, "actor": {"login": "chris", "type": "User"}}
    ]
    record["labels"].append({"name": rt.APPROVED})
    rt.promote(gh, env)
    assert installs["updated"] == [("stable", "0.5.200")]
    assert gh.comments == [(record["number"], "Promoted to stable (TIDY), approved by @chris.")]


def test_the_hold_label_release_hold_and_blockers_still_stop_an_automatic_approval(installs):
    gh = FakeGitHub()
    record = approval_for(gh, "v0.5.200", held=True)
    rt.promote(gh, AUTO)
    assert installs["updated"] == [] and any("on hold" in line for line in rt.LOG)
    record["labels"] = [{"name": rt.APPROVAL_LABEL}]
    rt.promote(gh, dict(AUTO, RELEASE_HOLD="billing fix first"))
    gh.blockers = [{"number": 7, "title": "Tasks vanish"}]
    rt.promote(gh, AUTO)
    assert installs["updated"] == [] and record["state"] == "open"
    gh.blockers = []
    installs["canary"].update(state="rolled_back", message="health check")
    with pytest.raises(rt.Fail, match="rolled_back"):
        rt.promote(gh, AUTO)
    assert installs["updated"] == []


def test_with_no_person_needed_releases_flow_canary_then_stable_with_no_one_in_the_loop(installs):
    # v0.5.200 is on the canary and nobody approved it; C is green as v0.5.201.
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200")
    assert rt.cmd_step(gh, dict(AUTO, GITHUB_EVENT_NAME="push"), NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200"), ("canary", "0.5.201")]
    assert older["state"] == "closed"
    newer = approval_of(gh, "v0.5.201")
    assert newer["state"] == "open" and "approved automatically" in newer["body"]
    # The next run (the backstop, at 03:00) takes v0.5.201 to stable too.
    installs["canary"].update(running="0.5.201", state="healthy")
    backstop = dict(AUTO, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON)
    assert rt.cmd_step(gh, backstop, pt(2026, 11, 6, 3, 0)) == 0
    assert installs["updated"][-1] == ("stable", "0.5.201") and newer["state"] == "closed"


# ---- the train job ----


def test_a_step_promotes_then_ships_the_green_candidate_and_closes_the_issues(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200", approved_by="chris")
    gh.add_issue("Release train failing", labels=[rt.ISSUE_LABEL], number=7)
    assert rt.cmd_step(gh, ENV, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200"), ("canary", "0.5.201")]
    assert gh.tagged == [("v0.5.201", C)]
    assert gh.dispatched == [("release.yml", "v0.5.201", {"desktop": "false", "latest": "false"})]
    # Latest only once the canary is healthy: the GitHub release and each image's latest tag.
    assert gh.latest == ["v0.5.201"]
    assert COMMANDS == [
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "-t",
            "ghcr.io/ticoteam/%s:latest" % image,
            "ghcr.io/ticoteam/%s:v0.5.201" % image,
        ]
        for image in rt.IMAGES
    ]
    # The promoted release's approval closes, the train's failure issue closes, and the new release awaits approval.
    assert gh.closed == [older["number"], 7]
    assert [issue["title"] for issue in open_issues(gh, rt.APPROVAL_LABEL)] == [rt.approval_title("v0.5.201")]
    # Published, and main has nothing newer: the next event does nothing.
    assert rt.plan_step(gh, NOW + timedelta(minutes=5))["action"] == "none"


def test_without_an_approval_stable_waits_and_the_canary_still_ships(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_step(gh, ENV, NOW) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    assert any("no open approval" in line for line in rt.LOG)
    assert [issue["title"] for issue in open_issues(gh, rt.APPROVAL_LABEL)] == [rt.approval_title("v0.5.201")]


@pytest.mark.parametrize("event", ["push", "backstop", "workflow_run"])
def test_every_step_promotes_an_approved_release_even_with_nothing_to_ship(installs, event):
    gh = FakeGitHub([run(C)], head=A)  # main has not moved: nothing to ship
    record = approval_for(gh, "v0.5.200", approved_by="chris")
    env = dict(ENV, GITHUB_EVENT_NAME="schedule" if event == "backstop" else event, SCHEDULE=rt.BACKSTOP_CRON)
    assert rt.cmd_step(gh, env, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200")] and record["state"] == "closed"


def test_a_newer_canary_release_closes_the_older_approval_with_a_link(installs):
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200")  # on the canary, still waiting for approval
    assert rt.cmd_step(gh, ENV, NOW) == 0
    newer = approval_of(gh, "v0.5.201")
    assert older["state"] == "closed"
    assert [body for number, body in gh.comments if number == older["number"]] == [
        "Superseded by v0.5.201, which is on the canary: %s" % newer["html_url"]
    ]
    # The new release's notes start at the last stable release (the one stable runs), and the record says how to decide.
    assert newer["body"].startswith("Candidate v0.5.201 at %s" % C[:12])
    assert "NOTES v0.5.199..%s" % C in newer["body"] and "/approve" in newer["body"]


def test_a_canary_that_rolls_back_leaves_the_release_not_latest_and_its_approval_closed(installs):
    installs["outcome"] = "rolled_back"
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200")
    assert rt.cmd_step(gh, ENV, NOW) == 1
    assert "v0.5.201" in gh.releases and gh.latest == [] and COMMANDS == []
    assert "rolled_back" in filed(gh)[0]["body"]
    refused = approval_of(gh, "v0.5.201")
    assert refused["state"] == "closed"
    assert any("Not on the canary" in body for number, body in gh.comments if number == refused["number"])
    assert older["state"] == "open", "the canary still runs v0.5.200, so its approval still stands"


def test_a_failed_image_move_is_reported_and_the_release_stays_not_latest(installs, monkeypatch):
    monkeypatch.setattr(rt, "run_command", lambda argv, timeout=180: (1, "denied: permission_denied"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_step(gh, ENV, NOW) == 1
    assert gh.latest == [] and "permission_denied" in filed(gh)[0]["body"]


def test_latest_never_moves_backwards(installs):
    gh = publishes(FakeGitHub([run(C)]))
    gh.latest_ = "v0.5.202"  # pushed by a person while the canary was updating
    assert rt.mark_latest(gh, "v0.5.201") is False and COMMANDS == [] and gh.latest == []
    assert rt.cmd_step(gh, ENV, NOW) == 0
    assert ("canary", "0.5.201") in installs["updated"] and gh.latest == [] and COMMANDS == []
    assert filed(gh)[0]["title"] == "Release v0.5.201 shipped, not marked latest (a newer release is)"
    assert "v0.5.202, a newer release" in filed(gh)[0]["body"]


def test_the_github_release_is_marked_only_after_every_image_moved(installs, monkeypatch):
    monkeypatch.setattr(
        rt,
        "run_command",
        lambda argv, timeout=180: (
            COMMANDS.append(argv) or ((1, "manifest blob unknown") if "tico-runner" in argv[-1] else (0, ""))
        ),
    )
    with pytest.raises(rt.Fail, match="tico-runner"):
        rt.mark_latest(publishes(FakeGitHub()), "v0.5.200")
    assert not any("tico-updater" in argv[-1] for argv in COMMANDS)


def lagging():
    """v0.5.201 was published and the canary updated, but marking it latest did not finish."""
    gh = FakeGitHub([run(C)], tags={"v0.5.200": A, "v0.5.201": C}, head=C)
    gh.releases["v0.5.201"] = released("v0.5.201")
    return gh


def test_a_missed_latest_is_caught_up_once_the_canary_runs_it_healthy(installs, tmp_path, monkeypatch):
    gh = lagging()
    assert rt.lagging_latest(gh) == "v0.5.201"
    # The guard cannot read the canary: only the backstop runs the job for it; a push with nothing else to do does not.
    on = {"RELEASE_TRAINS": "on"}
    assert (
        guard(gh, dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON), tmp_path, monkeypatch)["go"]
        == "true"
    )
    assert guard(gh, dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch)["go"] == "false"
    # The canary still runs the older release (rolled back): it stays not latest, quietly.
    assert rt.cmd_step(gh, ENV, NOW) == 0 and gh.latest == [] and filed(gh) == []
    installs["canary"].update(running="0.5.201", state="healthy")
    assert rt.cmd_step(gh, ENV, NOW) == 0 and gh.latest == ["v0.5.201"] and len(COMMANDS) == 3
    assert rt.lagging_latest(gh) is None


def test_an_unpublished_tag_is_released_again_once_and_recorded(installs):
    gh = publishes(FakeGitHub([run(C)]))
    gh.tags_["v0.5.201"] = B
    gh.release_runs = [release_run()]
    assert rt.cmd_step(gh, dict(ENV, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON), NOW) == 0
    assert gh.dispatched == [("release.yml", "v0.5.201", {"desktop": "false", "latest": "false"})]
    assert gh.tagged == [] and ("canary", "0.5.201") in installs["updated"] and gh.latest == ["v0.5.201"]
    record = filed(gh)[0]
    assert record["title"] == "Release v0.5.201 republished" and gh.closed == [record["number"]]
    assert "release/70" in record["body"]


def test_a_step_validates_main_and_waits_until_the_run_is_listed(installs):
    gh = publishes(FakeGitHub())
    assert rt.cmd_step(gh, ENV, NOW) == 0
    assert gh.dispatched == [
        ("ci.yml", "main", {"candidate_sha": C, "previous_release": "v0.5.200", "version": "v0.5.201"})
    ]
    assert any("run/50" in line for line in rt.LOG)
    assert gh.tagged == [] and installs["updated"] == [] and gh.created == []
    # The next event sees the run and waits.
    assert rt.plan_step(gh, NOW)["action"] == "wait"


def test_a_red_candidate_goes_on_the_issue_and_holds_main_until_a_person_validates_it(installs):
    gh = publishes(FakeGitHub([run(B, conclusion="failure")]))
    env = dict(
        ENV,
        GITHUB_EVENT_NAME="workflow_run",
        RUN_EVENT="workflow_dispatch",
        RUN_CONCLUSION="failure",
        RUN_TITLE=rt.title(B, "v0.5.201", "v0.5.200"),
        RUN_URL="https://github.example/run/red",
    )
    assert rt.cmd_step(gh, env, NOW) == 1
    assert len(gh.created) == 1 and "run/red" in gh.created[0]["body"]
    # Its full validation named no failure, so it holds the day: main's head is not validated automatically.
    assert HELD % ("https://github.example/run/1", "failure") in gh.created[0]["body"]
    assert gh.dispatched == [] and gh.tagged == []
    # A person runs Release train by hand: main's head is validated.
    assert rt.cmd_step(gh, dict(ENV, GITHUB_EVENT_NAME="workflow_dispatch"), NOW) == 0
    assert [d[0] for d in gh.dispatched] == ["ci.yml"] and gh.tagged == []


def test_missing_canary_key_fails_without_tagging(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_step(gh, dict(ENV, CANARY_UPDATE_KEY=""), NOW) == 1
    assert gh.tagged == [] and gh.created and "CANARY_UPDATE_KEY" in gh.created[0]["body"]


def test_a_failed_release_workflow_is_reported(installs):
    gh = FakeGitHub([run(C)])
    gh.release_runs = [
        {
            "status": "completed",
            "conclusion": "failure",
            "html_url": "release-run",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    assert rt.cmd_step(gh, ENV, NOW) == 1
    assert "release-run" in gh.created[0]["body"] and ("canary", "0.5.201") not in installs["updated"]


def test_wait_release_fails_on_this_release_run_only():
    gh = FakeGitHub()
    gh.release_runs = [
        {
            "status": "completed",
            "conclusion": "failure",
            "html_url": "old",
            "created_at": (NOW - timedelta(hours=1)).isoformat(),
        }
    ]
    with pytest.raises(rt.Fail, match="no GitHub release"):
        rt.wait_release(gh, "v0.5.201", NOW, timeout=0)


# ---- the release-train issue ----


def test_the_same_failure_is_posted_once_and_a_new_one_again():
    gh = FakeGitHub()
    rt.report(gh, False, ["Run log", "Train: waiting", "promote: holding"], key=["promote: holding"])
    rt.report(gh, False, ["Run log", "Train: something else", "promote: holding"], key=["promote: holding"])
    assert len(gh.created) == 1 and gh.comments == []
    rt.report(gh, False, ["ship: canary rolled back"])
    rt.report(gh, False, ["promote: holding"], key=["promote: holding"])
    assert len(gh.comments) == 2


def test_failures_share_one_issue_and_a_shipped_release_closes_it():
    gh = FakeGitHub()
    rt.report(gh, False, ["ship: canary rolled back"])
    rt.report(gh, False, ["promote: stable failed"])
    assert len(gh.created) == 1 and gh.comments[0][0] == gh.created[0]["number"]
    rt.report(gh, True, ["shipped"])
    assert gh.closed == [gh.created[0]["number"]] and open_issues(gh) == []
    rt.report(gh, True, ["shipped again"])
    assert len(gh.created) == 1


def test_an_existing_tag_at_another_commit_or_a_refused_tag_says_so():
    def call(self, method, path, body=None, token=None):
        if method == "POST":
            return 422, {"message": "Reference already exists"}, ""
        if path == "git/ref/tags/v0.5.201":
            return 200, {"object": {"type": "commit", "sha": B}}, ""
        if path == "git/ref/tags/v0.5.202":
            return 200, {"object": {"type": "tag", "sha": "t" * 40}}, ""
        if path == "git/tags/" + "t" * 40:
            return 200, {"object": {"type": "commit", "sha": C}}, ""
        return 404, {"message": "Not Found"}, ""

    gh = rt.GitHub("acme/tico", "token")
    gh.call = call.__get__(gh)
    assert gh.create_tag("v0.5.201", B) is False
    assert gh.create_tag("v0.5.202", C) is False  # annotated, at the same commit
    with pytest.raises(rt.Fail, match="already exists at another commit"):
        gh.create_tag("v0.5.201", C)
    with pytest.raises(rt.Fail, match="creating tag v0.5.203: 422 Reference already exists"):
        gh.create_tag("v0.5.203", C)


# ---- hotfix ----


def hotfix_github():
    gh = FakeGitHub()

    def on_dispatch(workflow, ref, inputs):
        if workflow == "ci.yml":
            gh.runs.insert(
                0,
                run(
                    inputs["candidate_sha"],
                    inputs["version"],
                    inputs["previous_release"],
                    at=datetime.now(timezone.utc),
                    id=9,
                ),
            )
        else:
            gh.releases[ref] = released(ref)

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
    assert gh.dispatched[0] == (
        "ci.yml",
        "main",
        {"candidate_sha": C, "previous_release": "v0.5.200", "version": "v0.5.201"},
    )
    assert gh.tagged == [("v0.5.201", C)]
    assert installs["updated"] == [("canary", "0.5.201"), ("stable", "0.5.201")]
    assert gh.latest == ["v0.5.201"]
    # Stable took the hotfix at once, so its approval record is closed.
    assert approval_of(gh, "v0.5.201")["state"] == "closed"


@pytest.mark.parametrize("hold, blockers", [("billing fix first", []), ("", [{"number": 7, "title": "Tasks vanish"}])])
def test_hotfix_respects_holds_on_stable_unless_overridden(installs, tmp_path, monkeypatch, hold, blockers):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = hotfix_github()
    gh.blockers = blockers
    env = dict(ENV, RELEASE_HOLD=hold, GITHUB_ACTOR="chris")
    assert rt.cmd_hotfix(gh, env, "", "Tasks vanish from the board after an update", True) == 0
    assert installs["updated"] == [("canary", "0.5.201")] and "Stable: held" in filed(gh)[0]["body"]
    assert approval_of(gh, "v0.5.201")["state"] == "open", "a held hotfix waits for an approval"
    gh = hotfix_github()
    gh.blockers = blockers
    installs["updated"].clear()
    monkeypatch.setattr(rt, "LOG", [])
    assert rt.cmd_hotfix(gh, env, "", "Tasks vanish from the board after an update", True, True) == 0
    assert installs["updated"] == [("stable", "0.5.201")]
    assert "HOLD OVERRIDDEN by chris" in filed(gh)[0]["body"]


def test_hotfix_without_promote_leaves_stable_and_records_the_reason(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = hotfix_github()
    assert rt.cmd_hotfix(gh, ENV, B, "Tasks vanish from the board after an update", False) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    record = filed(gh)[0]
    assert record["title"].startswith("Hotfix v0.5.201: Tasks vanish") and gh.closed == [record["number"]]
    assert "NOTES v0.5.199..%s" % B in record["body"], "the hotfix's notes are on its issue"
    assert approval_of(gh, "v0.5.201")["state"] == "open", "the approval decides stable"


def test_each_failed_hotfix_run_gets_its_own_comment(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = FakeGitHub()
    for run_id in ("1", "2"):
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        rt.cmd_hotfix(gh, ENV, D, "Tasks vanish from the board after an update", True)
    assert len(gh.created) == 1 and len(gh.comments) == 1


def test_failed_hotfix_validation_stops_before_tagging(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = FakeGitHub()
    gh.on_dispatch = lambda workflow, ref, inputs: gh.runs.insert(
        0, run(inputs["candidate_sha"], inputs["version"], conclusion="failure", at=datetime.now(timezone.utc))
    )
    assert rt.cmd_hotfix(gh, ENV, "", "Tasks vanish from the board after an update", True) == 1
    assert gh.tagged == [] and installs["updated"] == []
    assert open_issues(gh) and "Release validation ended failure" in filed(gh)[0]["body"]


# ---- the release shots: a shipped candidate's pictures on its approval issue, and the train job's outputs for them ----


def step_outputs(path):
    lines = path.read_text().splitlines()
    keys = [line.split("=", 1)[0] for line in lines]
    assert len(keys) == len(set(keys)), "a key written twice, GitHub keeps only the last: %s" % lines
    return dict(line.split("=", 1) for line in lines)


def test_a_shipped_candidate_hands_its_version_sha_and_base_to_the_shots(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_step(gh, ENV, NOW) == 0
    # The base is the release stable runs: what the approver reads the changes since, and the pictures compare with.
    assert step_outputs(tmp_path / "out") == {
        "reported": "true",
        "shipped": "true",
        "version": "v0.5.201",
        "sha": C,
        "base": "v0.5.199",
    }


def test_the_shipped_candidate_is_in_the_outputs_even_if_the_step_dies_after_it(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))

    def dies(*args, **kwargs):
        raise RuntimeError("the runner went away")

    monkeypatch.setattr(rt, "report", dies)
    with pytest.raises(RuntimeError, match="runner went away"):
        rt.cmd_step(gh, ENV, NOW)
    # The canary runs the candidate, so its shots still run; reported is missing, so the job's fallback reports the death.
    assert installs["updated"] == [("canary", "0.5.201")]
    assert step_outputs(tmp_path / "out") == {
        "shipped": "true",
        "version": "v0.5.201",
        "sha": C,
        "base": "v0.5.199",
    }


def test_a_later_step_that_fails_keeps_the_shipped_candidate_in_the_outputs(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))

    def refused(gh, version):
        raise rt.Fail("the latest marking was refused")

    monkeypatch.setattr(rt, "mark_latest", refused)
    assert rt.cmd_step(gh, ENV, NOW) == 1
    assert step_outputs(tmp_path / "out") == {
        "reported": "true",
        "shipped": "true",
        "version": "v0.5.201",
        "sha": C,
        "base": "v0.5.199",
    }


def test_a_step_whose_canary_rolls_back_hands_no_candidate_to_the_shots(installs, tmp_path, monkeypatch):
    installs["outcome"] = "rolled_back"
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_step(gh, ENV, NOW) == 1
    assert step_outputs(tmp_path / "out") == {"reported": "true", "shipped": "false"}


def test_without_stable_the_pictures_compare_with_the_previous_release(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    no_stable = {key: value for key, value in ENV.items() if not key.startswith("STABLE")}
    assert rt.cmd_step(gh, no_stable, NOW) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    assert step_outputs(tmp_path / "out")["base"] == "v0.5.200"


def test_the_base_is_stables_release_and_an_unreachable_stable_falls_back_to_the_previous_tag(installs):
    assert rt.release_base(ENV, "v0.5.200") == "v0.5.199"
    installs["down"] = {"stable"}
    assert rt.release_base(ENV, "v0.5.200") == "v0.5.200"


def test_a_stable_that_cannot_be_read_is_noted_and_the_previous_tag_stands(installs, monkeypatch):
    def unreadable(target):
        raise rt.Fail("stable is down")

    monkeypatch.setattr(rt, "running", unreadable)
    assert rt.release_base(ENV, "v0.5.200") == "v0.5.200"
    assert any("stable's release cannot be read (stable is down)" in line for line in rt.LOG)


def test_the_pictures_replace_the_pending_line_and_the_same_pictures_again_change_nothing(tmp_path, monkeypatch):
    gh = FakeGitHub()
    issue = rt.open_approval(gh, "v0.5.201", C, "NOTES v0.5.199..%s" % C)
    shots = tmp_path / "release-shots.md"
    shots.write_text("## What it looks like\n\n| before | after |\n| --- | --- |\n| ![a](a.png) | ![b](b.png) |\n")
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(shots)) == 0
    body = issue["body"]
    assert rt.SHOTS_PENDING not in body and "| before | after |" in body
    assert body.count(rt.SHOTS_START) == 1 and "NOTES v0.5.199..%s" % C in body and rt.APPROVE_FOOTER in body
    edits = []
    monkeypatch.setattr(gh, "edit", lambda number, text, state: edits.append(number))
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(shots)) == 0
    assert edits == []


def test_reopening_an_approval_keeps_the_pictures_already_on_it(tmp_path):
    gh = FakeGitHub()
    issue = rt.open_approval(gh, "v0.5.201", C, "NOTES v0.5.199..%s" % C)
    shots = tmp_path / "release-shots.md"
    shots.write_text("## What it looks like\n\n| before | after |\n| --- | --- |\n| ![a](a.png) | ![b](b.png) |\n")
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(shots)) == 0
    # A republish or a retry of the same version reopens the same record: its pictures stay, and nothing goes pending.
    again = rt.open_approval(gh, "v0.5.201", C, "NOTES v0.5.199..%s" % C)
    assert again is issue and issue["state"] == "open"
    assert "| before | after |" in issue["body"] and rt.SHOTS_PENDING not in issue["body"]
    assert issue["body"].count(rt.SHOTS_START) == 1 and "NOTES v0.5.199..%s" % C in issue["body"]


def test_missing_pictures_put_the_failure_and_the_run_that_tried_on_the_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "ticoteam/tico")
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    gh = FakeGitHub()
    issue = rt.open_approval(gh, "v0.5.201", C, "NOTES")
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(tmp_path / "missing.md")) == 0
    assert "could not be made" in issue["body"] and rt.SHOTS_PENDING not in issue["body"]
    assert "https://github.example/ticoteam/tico/actions/runs/4242" in issue["body"]
    assert any("not made: see the run" in line for line in rt.LOG)
    # An empty file is no pictures either, and the failure is not put on the issue twice.
    (tmp_path / "empty.md").write_text("  \n")
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(tmp_path / "empty.md")) == 0
    assert issue["body"].count("could not be made") == 1


def test_a_closed_approval_keeps_its_state_when_its_pictures_go_in(tmp_path):
    gh = FakeGitHub()
    issue = approval_for(gh, "v0.5.201")
    gh.close(issue["number"])
    shots = tmp_path / "release-shots.md"
    shots.write_text("## What it looks like\n\npictures of the change\n")
    assert rt.cmd_shots_into_approval(gh, "v0.5.201", str(shots)) == 0
    assert issue["state"] == "closed" and "pictures of the change" in issue["body"]


def test_pictures_without_an_approval_record_fail_loudly(tmp_path):
    gh = FakeGitHub()
    shots = tmp_path / "release-shots.md"
    shots.write_text("## What it looks like\n\npictures\n")
    with pytest.raises(rt.Fail, match="no approval record for v0.5.201"):
        rt.cmd_shots_into_approval(gh, "v0.5.201", str(shots))
    assert gh.comments == [] and gh.created == []


def test_two_incrementals_in_a_row_select_only_their_own_diff_and_the_carried_failures():
    # Full A (green, 5 h ago), incremental B (green, 3 h ago, anchored on A), incremental C (red, names a failed test,
    # 1 h ago), head D. C anchors D: the plan runs C..D and what C failed, never A..D again. (A and B validated the
    # release that is out: a green of the next release ships at once.)
    gh = FakeGitHub(
        [
            dict(
                run(C, id=3, conclusion="failure", at=NOW - timedelta(hours=1)),
                display_title=rt.title(C, "v0.5.201", "v0.5.200", anchor=B),
            ),
            dict(
                out(B, id=2, at=NOW - timedelta(hours=3)), display_title=rt.title(B, "v0.5.200", "v0.5.199", anchor=A)
            ),
            out(A, id=1, at=NOW - timedelta(hours=5)),
        ],
        head=D,
    )
    gh.order = [A, B, C, D]
    gh.failed = {3: {"backend/tests/test_b.py"}}
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", C)
    assert [call for call in checkout.calls if call[0] == "plan"] == [("plan", C, D, {"backend/tests/test_b.py"})]
    # Green B and then green C: the next head selects only C..D, carrying nothing.
    gh = FakeGitHub(
        [
            dict(
                out(C, id=3, at=NOW - timedelta(hours=1)), display_title=rt.title(C, "v0.5.200", "v0.5.199", anchor=B)
            ),
            dict(
                out(B, id=2, at=NOW - timedelta(hours=3)), display_title=rt.title(B, "v0.5.200", "v0.5.199", anchor=A)
            ),
            out(A, id=1, at=NOW - timedelta(hours=5)),
        ],
        head=D,
    )
    gh.order = [A, B, C, D]
    checkout = FakeCheckout(since=None, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"], picked["carried"]) == ("dispatch", "incremental", C, [])
    assert [call for call in checkout.calls if call[0] == "plan"] == [("plan", C, D, set())]


def test_a_full_validation_older_than_a_day_runs_even_when_a_fresh_incremental_anchors_the_chain():
    gh = FakeGitHub(
        [
            dict(
                out(C, id=3, at=NOW - timedelta(hours=1)), display_title=rt.title(C, "v0.5.200", "v0.5.199", anchor=B)
            ),
            out(B, id=2, at=NOW - timedelta(hours=26)),
        ],
        head=D,
    )
    gh.order = [A, B, C, D]
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(plan=PLAN_TEXT))
    assert (picked["action"], picked["mode"]) == ("dispatch", "full")
    assert "no full validation in the last 24 hours" in picked["why"]


def test_a_github_reply_cut_short_is_retried(monkeypatch):
    import http.client

    class Reply:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http.client.IncompleteRead(b"x" * 10, 5)
        return Reply()

    monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(rt.time, "sleep", lambda seconds: None)
    assert rt.GitHub("o/r", "t").get("actions/runs/1/jobs") == {"ok": True}
    assert len(calls) == 2
