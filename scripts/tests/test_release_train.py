"""Release windows: when a window is open, which candidate ships, when stable may take the canary's approved release, and
that a failure reaches the release-train issue (scripts/release_train.py)."""

from datetime import date, datetime, timedelta, timezone
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
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)  # Thursday 10:00 PST: 07:30 is the latest start
OPENED = pt(2026, 11, 5, 7, 30)


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
    """main is A <- B <- C (C is the head); v0.5.200 is tagged at A and was published before the window opened."""

    def __init__(self, runs=(), tags=None, head=C, published=OPENED - timedelta(hours=6)):
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


def approval_for(gh, version, *, approved_by=None, held=False, comment_by=None, account_type="User"):
    """An open approval record for version: approved by a login's `approved` label, held, or with a `/approve` comment.
    The approving account is a person (type User) unless account_type says otherwise."""
    labels = [rt.APPROVAL_LABEL] + ([rt.APPROVED] if approved_by else []) + ([rt.HOLD] if held else [])
    issue = gh.add_issue(rt.approval_title(version), labels=labels, body="notes for %s" % version)
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
}


# ---- loading the scripts' modules ----


def test_loaded_modules_are_registered_by_name():
    # A dataclass looks its module up in sys.modules while the module runs, so each loader registers it first.
    assert sys.modules["tico_release_notes"] is rt.release_notes
    assert sys.modules["tico_rollout"] is rt.rollout


# ---- versions and the clock ----


def test_versions_order_numerically_and_skip_prereleases():
    tags = {"v0.5.9", "v0.5.10", "v0.6.0-rc.1", "latest", "v1.0"}
    assert rt.newest_stable(tags) == "v0.5.10"
    assert rt.next_patch("v0.5.10") == "v0.5.11"
    assert rt.newest_stable({"v0.6.0-rc.1"}) is None


def test_validation_title_round_trips_for_release_yml():
    assert rt.parse_title(rt.title(C, "v0.5.201", "v0.5.200")) == (C, "v0.5.201", "v0.5.200")
    assert rt.parse_title("Release window opens") is None


UTC = timezone.utc


@pytest.mark.parametrize(
    "now, start, following",
    [
        pytest.param(
            datetime(2026, 7, 10, 4, 0, tzinfo=UTC),
            pt(2026, 7, 9, 20, 30),
            pt(2026, 7, 10, 7, 30),
            id="thu-2030-pdt-seen-0400-utc-next-day",
        ),
        pytest.param(
            datetime(2026, 11, 6, 4, 40, tzinfo=UTC),
            pt(2026, 11, 5, 20, 30),
            pt(2026, 11, 6, 7, 30),
            id="thu-2030-pst-seen-0440-utc-next-day",
        ),
        pytest.param(
            pt(2026, 11, 6, 20, 31),
            pt(2026, 11, 6, 20, 30),
            pt(2026, 11, 7, 7, 30),
            id="fri-2031-the-2030-window-is-the-latest",
        ),
        pytest.param(
            pt(2026, 11, 6, 21, 0), pt(2026, 11, 6, 20, 30), pt(2026, 11, 7, 7, 30), id="fri-2100-next-is-sat-0730"
        ),
        pytest.param(
            pt(2026, 11, 7, 0, 30),
            pt(2026, 11, 6, 20, 30),
            pt(2026, 11, 7, 7, 30),
            id="sat-0030-across-midnight-keeps-fri-2030",
        ),
        pytest.param(
            pt(2026, 7, 11, 7, 31), pt(2026, 7, 11, 7, 30), pt(2026, 7, 11, 15, 0), id="sat-0731-pdt-opened-0730"
        ),
        pytest.param(
            pt(2026, 11, 7, 7, 31), pt(2026, 11, 7, 7, 30), pt(2026, 11, 7, 15, 0), id="sat-0731-pst-opened-0730"
        ),
        pytest.param(
            pt(2026, 11, 5, 11, 5), pt(2026, 11, 5, 11, 0), pt(2026, 11, 5, 14, 0), id="thu-1105-pst-opened-1100"
        ),
        pytest.param(
            pt(2026, 7, 11, 14, 5),
            pt(2026, 7, 11, 7, 30),
            pt(2026, 7, 11, 15, 0),
            id="sat-1405-no-1400-window-at-weekends",
        ),
        pytest.param(
            pt(2026, 7, 11, 15, 5), pt(2026, 7, 11, 15, 0), pt(2026, 7, 12, 7, 30), id="sat-1505-opens-the-1500-window"
        ),
        pytest.param(
            pt(2026, 7, 11, 20, 31),
            pt(2026, 7, 11, 15, 0),
            pt(2026, 7, 12, 7, 30),
            id="sat-2031-no-2030-window-at-weekends",
        ),
        pytest.param(
            pt(2026, 11, 9, 7, 0), pt(2026, 11, 8, 15, 0), pt(2026, 11, 9, 7, 30), id="mon-0700-after-sunday-1500"
        ),
    ],
)
def test_window_start_and_next_window_follow_los_angeles_time(now, start, following):
    assert rt.window_start(now) == start, "latest window start"
    assert rt.next_window(now) == following, "next window start"
    assert rt.window_start(now) <= now < rt.next_window(now)


def test_a_dst_change_day_uses_the_offset_of_each_window():
    # Sunday 8 March 2026 springs forward at 02:00 PST (10:00 UTC): 07:30 and 15:00 are daylight time after that.
    assert rt.window_start(datetime(2026, 3, 8, 14, 31, tzinfo=UTC)) == datetime(2026, 3, 8, 14, 30, tzinfo=UTC), (
        "spring forward: 07:30 PDT is 14:30 UTC"
    )
    assert rt.window_start(datetime(2026, 3, 8, 14, 29, tzinfo=UTC)) == datetime(2026, 3, 7, 23, 0, tzinfo=UTC), (
        "spring forward: before 07:30 PDT, Saturday's 15:00 PST window is the latest start"
    )
    assert rt.next_window(datetime(2026, 3, 8, 14, 29, tzinfo=UTC)) == datetime(2026, 3, 8, 14, 30, tzinfo=UTC), (
        "spring forward: the next window is 07:30 PDT"
    )
    assert rt.window_start(datetime(2026, 3, 8, 22, 1, tzinfo=UTC)) == datetime(2026, 3, 8, 22, 0, tzinfo=UTC), (
        "spring forward: 15:00 PDT is 22:00 UTC"
    )
    # Sunday 1 November 2026 falls back at 02:00 PDT (09:00 UTC): 07:30 and 15:00 are standard time after that.
    assert rt.window_start(datetime(2026, 11, 1, 15, 31, tzinfo=UTC)) == datetime(2026, 11, 1, 15, 30, tzinfo=UTC), (
        "fall back: 07:30 PST is 15:30 UTC"
    )
    assert rt.window_start(datetime(2026, 11, 1, 15, 29, tzinfo=UTC)) == datetime(2026, 10, 31, 22, 0, tzinfo=UTC), (
        "fall back: before 07:30 PST, Saturday's 15:00 PDT window is the latest start"
    )
    assert rt.window_start(datetime(2026, 11, 1, 23, 1, tzinfo=UTC)) == datetime(2026, 11, 1, 23, 0, tzinfo=UTC), (
        "fall back: 15:00 PST is 23:00 UTC"
    )


@pytest.mark.parametrize(
    "cron, now, opens, why",
    [
        pytest.param(
            "30 14 * * *",
            pt(2026, 7, 7, 7, 45),
            True,
            "07:30 PDT on a Tuesday, fifteen minutes late",
            id="in-season-pdt",
        ),
        pytest.param(
            "30 15 * * *",
            pt(2026, 7, 7, 8, 45),
            False,
            "its off-season twin: 08:30 PDT is no window",
            id="off-season-twin-pdt",
        ),
        pytest.param(
            "30 15 * * *", pt(2026, 11, 5, 7, 45), True, "07:30 PST on a Thursday, in season", id="in-season-pst"
        ),
        pytest.param(
            "30 14 * * *",
            pt(2026, 11, 5, 6, 45),
            False,
            "its off-season twin: 06:30 PST is no window",
            id="off-season-twin-pst",
        ),
        pytest.param(
            "0 21 * * *",
            pt(2026, 7, 9, 14, 5),
            True,
            "14:00 PDT on a Thursday is a weekday window",
            id="thu-1400-opens",
        ),
        pytest.param(
            "0 21 * * *",
            pt(2026, 7, 11, 14, 5),
            False,
            "14:00 PDT on a Saturday is no window",
            id="sat-1400-does-not-open",
        ),
        pytest.param(
            "0 18 * * *",
            pt(2026, 10, 14, 11, 5),
            True,
            "11:00 PDT on a Wednesday is a weekday window",
            id="wed-1100-pdt-opens",
        ),
        pytest.param(
            "0 19 * * *",
            pt(2026, 11, 11, 11, 5),
            True,
            "11:00 PST on a Wednesday is a weekday window",
            id="wed-1100-pst-opens",
        ),
        pytest.param(
            "0 18 * * *",
            pt(2026, 10, 17, 11, 5),
            False,
            "11:00 PDT on a Saturday is no window",
            id="sat-1100-does-not-open",
        ),
        pytest.param(
            "0 19 * * *",
            pt(2026, 10, 14, 12, 5),
            False,
            "its off-season twin: 12:05 PDT is no window",
            id="off-season-twin-1100-pdt",
        ),
        pytest.param(
            "0 18 * * *",
            pt(2026, 11, 11, 10, 5),
            False,
            "its off-season twin: 10:05 PST is no window",
            id="off-season-twin-1100-pst",
        ),
        pytest.param("0 22 * * *", pt(2026, 7, 11, 15, 5), True, "15:00 PDT on a Saturday opens", id="sat-1500-opens"),
        pytest.param("30 3 * * *", pt(2026, 7, 10, 20, 35), True, "Friday 20:30 PDT is a window", id="fri-2030-opens"),
        pytest.param(
            "30 3 * * *", pt(2026, 7, 11, 20, 35), False, "Saturday 20:30 PDT is no window", id="sat-2030-does-not-open"
        ),
        pytest.param(
            "30 14 * * *",
            pt(2026, 7, 9, 8, 50),
            True,
            "80 minutes late: still the current window",
            id="late-80-minutes",
        ),
        pytest.param(
            "30 14 * * *", pt(2026, 7, 9, 9, 5), True, "95 minutes late: still the current window", id="late-95-minutes"
        ),
        pytest.param(
            "30 14 * * *",
            pt(2026, 7, 9, 10, 30),
            True,
            "3 hours late: still the current window",
            id="late-3-hours-opens",
        ),
        pytest.param(
            "30 14 * * *",
            pt(2026, 7, 9, 14, 5),
            False,
            "superseded: the 14:00 PDT window has started since",
            id="superseded-does-not-open",
        ),
        pytest.param(
            "30 15 * * *",
            pt(2026, 7, 7, 11, 30),
            False,
            "11:30 PDT: the current window is 11:00, and this cron's 08:30 PDT is no window",
            id="late-3-hours-twin-does-not-open",
        ),
    ],
)
def test_a_window_cron_opens_only_at_its_own_real_window_start(cron, now, opens, why):
    assert rt.window_cron_opens(cron, now) is opens, why


@pytest.mark.parametrize(
    "env, opens",
    [
        pytest.param({"GITHUB_EVENT_NAME": "workflow_dispatch"}, True, id="manual-run"),
        pytest.param({"GITHUB_EVENT_NAME": "schedule", "SCHEDULE": "30 15 * * *"}, True, id="window-cron"),
        pytest.param({"GITHUB_EVENT_NAME": "schedule", "SCHEDULE": "30 14 * * *"}, False, id="off-season-twin"),
        pytest.param({"GITHUB_EVENT_NAME": "schedule", "SCHEDULE": rt.BACKSTOP_CRON}, False, id="backstop"),
        pytest.param({"GITHUB_EVENT_NAME": "push"}, False, id="push"),
    ],
)
def test_only_a_window_start_or_a_manual_run_opens_a_window(env, opens):
    assert rt.window_opens(env, pt(2026, 11, 5, 7, 45)) is opens


def test_crons_are_each_utc_window_start_once_and_backstop_minutes_are_clear():
    assert rt.WEEKDAY_WINDOWS == ((7, 30), (11, 0), (14, 0), (20, 30)) and rt.WEEKEND_WINDOWS == ((7, 30), (15, 0))
    starts, day = set(), date(2026, 1, 1)
    while day < date(2028, 1, 1):  # two years: both offsets, weekdays and weekends
        starts |= {(start.hour, start.minute) for start in rt.window_starts_on(day)}
        day += timedelta(days=1)
    crons = [rt.cron_time(cron) for cron in rt.CRONS]
    assert len(crons) == len(set(crons)), "a window cron is listed twice"
    assert set(crons) == starts, "CRONS are not exactly the UTC times a window starts at"
    assert {minute for _, minute in crons} <= {0, 30}, "a window cron is not on a window's minute (00 or 30)"
    assert rt.BACKSTOP_CRON == "5-59/20 * * * *" and not {5, 25, 45} & {minute for _, minute in crons}, (
        "the backstop shares a minute with a window cron"
    )


# ---- what an open window does ----


def plan(gh, now=NOW, finished=None, manual=False):
    return rt.plan_step(gh, now, finished, manual)


def test_window_closes_once_a_release_is_published_after_it_opened():
    gh = FakeGitHub([run(C)], published=OPENED + timedelta(minutes=40))
    assert plan(gh)["action"] == "none" and "window closed" in plan(gh)["why"]
    # The 14:00 window opens and ships.
    assert plan(gh, now=pt(2026, 11, 5, 14, 5))["action"] == "ship"


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
    assert plan(gh)["action"] == "none" and "runs again at 10:20 PST" in plan(gh)["why"]
    gh.release_runs = [release_run()]
    again = plan(gh)
    assert (again["action"], again["sha"], again["version"], again["republish"]) == ("ship", B, "v0.5.201", True)
    gh.release_runs = [release_run(id=71), release_run()]
    stuck = plan(gh)
    assert stuck["action"] == "none" and rt.problems(stuck) == [stuck["stuck"]] and "release/71" in stuck["stuck"]


def test_nothing_to_do_when_main_has_not_moved():
    assert plan(FakeGitHub(head=A))["action"] == "none"


def test_the_first_green_candidate_of_the_window_ships():
    picked = plan(FakeGitHub([run(B, id=2, at=OPENED + timedelta(minutes=20))]))
    assert (picked["action"], picked["sha"], picked["version"]) == ("ship", B, "v0.5.201")
    # Green before the window opened: main's head ships, an older commit does not (main's head is validated instead).
    assert plan(FakeGitHub([run(C, at=OPENED - timedelta(hours=2))]))["action"] == "ship"
    assert plan(FakeGitHub([run(B, at=OPENED - timedelta(hours=2))]))["action"] == "dispatch"


@pytest.mark.parametrize(
    "runs",
    [
        [run(D)],  # not on main
        [run(C, "v0.5.201", "v0.5.199")],  # validated as an upgrade from another release
        [run(C, "v0.5.202", "v0.5.201")],  # another version
        [run(B, conclusion="cancelled")],  # an older commit cancelled: main's head is validated
    ],
    ids=["off-main", "other-previous", "other-version", "older-cancelled"],
)
def test_only_a_green_run_of_this_version_on_main_counts(runs):
    assert plan(FakeGitHub(runs))["action"] == "dispatch"


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


def test_a_red_head_waits_for_a_push_and_a_red_older_commit_validates_the_head():
    assert plan(FakeGitHub([run(C, conclusion="failure")]))["action"] == "wait"
    assert plan(FakeGitHub([run(B, conclusion="failure")]))["action"] == "dispatch"
    # A window still red at the next open carries on, without validating the same head again.
    later = pt(2026, 11, 5, 14, 5)
    assert plan(FakeGitHub([run(C, conclusion="failure", at=OPENED)]), now=later)["action"] == "wait"


def test_a_finished_red_candidate_of_this_window_is_reported():
    finished = {"conclusion": "failure", "title": rt.title(B, "v0.5.201", "v0.5.200"), "url": "red-url"}
    assert plan(FakeGitHub([run(B, conclusion="failure")]), finished=finished)["red"]["sha"] == B
    stale = dict(finished, title=rt.title(B, "v0.5.200", "v0.5.199"))
    assert plan(FakeGitHub(), finished=stale)["red"] is None
    assert plan(FakeGitHub(), finished=dict(finished, conclusion="cancelled"))["red"]["conclusion"] == "cancelled"
    assert plan(FakeGitHub(), finished=dict(finished, conclusion="success"))["red"] is None


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
        self.previous_changed = previous_changed
        return self.plan_text


PLAN_TEXT = '{"jobs":[{"name":"python-1","part":"python","files":["backend/tests/test_b.py"]}]}'


def red_then_anchor(anchor_at=OPENED - timedelta(hours=1), red_at=NOW - timedelta(hours=1)):
    """main is A <- B <- C <- D and D is the head. B was green before the window opened (the anchor); C went red after
    it. Newest first, as GitHub lists them."""
    gh = FakeGitHub([run(C, id=3, conclusion="failure", at=red_at), run(B, id=2, at=anchor_at)], head=D)
    gh.order = [A, B, C, D]
    return gh


def test_a_red_validation_reruns_incrementally_once_a_commit_selects_what_failed():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", B)
    assert picked["increment"] == PLAN_TEXT
    assert "carries backend/tests/test_b.py" in picked["why"] and "incrementally on %s" % B[:12] in picked["why"]
    assert ("tokens_since", C, D) in checkout.calls and ("plan", B, D, {"backend/tests/test_b.py"}) in checkout.calls


def test_a_red_validation_waits_and_says_so_while_no_commit_selects_what_failed():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"ui/tests/threads.cjs"}, plan=PLAN_TEXT))
    assert picked["action"] == "wait" and "the window waits" in picked["why"]
    assert "backend/tests/test_b.py" in picked["why"] and gh.dispatched == []


def test_a_red_naming_no_failure_runs_the_full_validation():
    gh = red_then_anchor()  # its log names nothing a commit could fix
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT))
    assert (picked["action"], picked["mode"]) == ("dispatch", "full")
    assert "named no failure a commit could fix" in picked["why"]


def test_a_stale_anchor_runs_the_full_validation_once_a_day():
    gh = red_then_anchor(anchor_at=NOW - timedelta(hours=25))
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT))
    assert (picked["action"], picked["mode"]) == ("dispatch", "full")
    assert "no green full validation" in picked["why"]


def test_an_incremental_plan_too_big_to_dispatch_runs_the_full_validation():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    picked = rt.plan_step(gh, NOW, checkout=FakeCheckout(since={"backend/tests/test_b.py"}, plan=None))
    assert (picked["action"], picked["mode"]) == ("dispatch", "full")
    assert "too big" in picked["why"]


def test_without_a_checkout_every_validation_is_full():
    gh = red_then_anchor()
    gh.failed = {3: {"backend/tests/test_b.py"}}
    assert rt.plan_step(gh, NOW)["mode"] == "full"


def test_an_incremental_validation_names_its_anchor_and_plan_and_a_full_one_names_neither():
    gh = FakeGitHub()
    rt.dispatch_candidate(gh, C, "v0.5.201", "v0.5.200", timeout=0)
    rt.dispatch_candidate(gh, C, "v0.5.201", "v0.5.200", timeout=0, anchor=B, increment=PLAN_TEXT)
    assert gh.dispatched[0][2] == {"candidate_sha": C, "previous_release": "v0.5.200", "version": "v0.5.201"}
    assert gh.dispatched[1][2] == {
        "candidate_sha": C,
        "previous_release": "v0.5.200",
        "version": "v0.5.201",
        "anchor": B,
        "plan": PLAN_TEXT,
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
    # main is A <- B <- C <- D (the head). A is this release's green full run before the window; B went red before it;
    # C is an earlier release's green incremental run after B, so it supersedes B's red.
    earlier = dict(
        run(C, version="v0.5.200", previous="v0.5.199", id=3, at=OPENED - timedelta(minutes=30)),
        display_title=rt.title(C, "v0.5.200", "v0.5.199", anchor=A),
    )
    gh = FakeGitHub(
        [
            earlier,
            run(B, id=2, conclusion="failure", at=OPENED - timedelta(hours=2)),
            run(A, id=1, at=OPENED - timedelta(hours=3)),
        ],
        head=D,
    )
    gh.order = [A, B, C, D]
    gh.failed = {2: {"backend/tests/test_b.py"}}
    checkout = FakeCheckout(since={"backend/tests/test_b.py"}, plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", A)
    assert checkout.calls == [("plan", A, D, set())]  # nothing carried, and no commit was read against the red run
    assert "carries" not in picked["why"] and "the window waits" not in picked["why"]


def test_an_anchor_of_an_earlier_release_runs_the_product_part_when_the_previous_release_moved():
    # The newest tag is v0.5.200; the anchor validated v0.5.200 from v0.5.199, a green full run two hours ago.
    gh = FakeGitHub([run(B, version="v0.5.200", previous="v0.5.199", id=2, at=NOW - timedelta(hours=2))])
    checkout = FakeCheckout(plan=PLAN_TEXT)
    picked = rt.plan_step(gh, NOW, checkout=checkout)
    assert (picked["action"], picked["mode"], picked["anchor"]) == ("dispatch", "incremental", B)
    assert checkout.previous_changed is True
    assert "the product part runs: the previous release is v0.5.200, the anchor's was v0.5.199" in picked["why"]
    # The same previous release (this window's own full run before it opened): the product part is not forced.
    same = FakeCheckout(plan=PLAN_TEXT)
    assert (
        rt.plan_step(FakeGitHub([run(B, id=2, at=OPENED - timedelta(hours=1))]), NOW, checkout=same)["mode"]
        == "incremental"
    )
    assert same.previous_changed is False


# ---- the guard ----


def guard(gh, env, tmp_path, monkeypatch, now=NOW):
    out = tmp_path / "out"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rt.cmd_guard(gh, env, now)
    return dict(line.split("=", 1) for line in out.read_text().split())


def test_guard_runs_the_window_job_only_when_it_has_work(tmp_path, monkeypatch):
    on = {"RELEASE_TRAINS": "on"}
    # The 07:30 PST cron, fifteen minutes late: it opens the window (promotion, then a ship or a validation).
    assert guard(
        FakeGitHub(),
        dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE="30 15 * * *"),
        tmp_path,
        monkeypatch,
        now=pt(2026, 11, 5, 7, 45),
    ) == {"go": "true"}
    # The off-season twin ("30 14" is 06:30 PST) does exactly what a push does at the same moment, and opens nothing.
    twin = guard(
        FakeGitHub(),
        dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE="30 14 * * *"),
        tmp_path,
        monkeypatch,
        now=pt(2026, 11, 5, 7, 40),
    )
    push = guard(FakeGitHub(), dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch, now=pt(2026, 11, 5, 7, 40))
    assert twin == push == {"go": "true"}, "an off-season twin cron should act like a push"
    # The backstop takes a step in an open window like a push, never opens one, and idles otherwise.
    backstop = dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON)
    assert guard(FakeGitHub(), backstop, tmp_path, monkeypatch) == {"go": "true"}
    closed = FakeGitHub([run(C)], published=OPENED + timedelta(minutes=40))
    assert guard(closed, backstop, tmp_path, monkeypatch) == {"go": "false"}
    assert guard(FakeGitHub(), dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch) == {"go": "true"}
    busy = FakeGitHub([run(C, status="in_progress", conclusion=None)])
    assert guard(busy, dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch)["go"] == "false"
    red = {
        "GITHUB_EVENT_NAME": "workflow_run",
        "RUN_EVENT": "workflow_dispatch",
        "RUN_CONCLUSION": "failure",
        "RUN_TITLE": rt.title(C, "v0.5.201", "v0.5.200"),
        "RUN_URL": "u",
    }
    assert guard(FakeGitHub([run(C, conclusion="failure")]), dict(on, **red), tmp_path, monkeypatch)["go"] == "true"


def test_the_backstop_runs_the_job_for_an_approved_release_that_stable_does_not_run_yet(tmp_path, monkeypatch):
    backstop = {"RELEASE_TRAINS": "on", "GITHUB_EVENT_NAME": "schedule", "SCHEDULE": rt.BACKSTOP_CRON}

    def closed():  # the window is closed: nothing to ship, only the approval can call the job
        return FakeGitHub([run(C)], published=OPENED + timedelta(minutes=40))

    assert guard(closed(), backstop, tmp_path, monkeypatch) == {"go": "false"}, "no approval record"
    labelled = closed()
    approval_for(labelled, "v0.5.200", approved_by="mallory")  # approved by someone without write access
    assert guard(labelled, backstop, tmp_path, monkeypatch) == {"go": "false"}
    approved = closed()
    approval_for(approved, "v0.5.200", approved_by="chris")
    assert guard(approved, backstop, tmp_path, monkeypatch) == {"go": "true"}
    held = closed()
    approval_for(held, "v0.5.200", approved_by="chris", held=True)
    assert guard(held, backstop, tmp_path, monkeypatch) == {"go": "false"}


def test_guard_reports_a_red_head_from_any_event_once(tmp_path, monkeypatch):
    gh = FakeGitHub([run(C, conclusion="cancelled", id=4)])
    for event in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_EVENT_NAME": "schedule", "SCHEDULE": rt.BACKSTOP_CRON}):
        assert guard(gh, dict(event, RELEASE_TRAINS="on"), tmp_path, monkeypatch)["go"] == "true"
    assert rt.cmd_window(gh, dict(ENV, GITHUB_EVENT_NAME="push"), False, NOW) == 1
    assert len(gh.created) == 1 and "ended cancelled" in gh.created[0]["body"]
    # Reported: the next push and the backstop stay quiet, and a window job that runs anyway does not post it again.
    assert guard(gh, {"GITHUB_EVENT_NAME": "push", "RELEASE_TRAINS": "on"}, tmp_path, monkeypatch)["go"] == "false"
    rt.cmd_window(gh, dict(ENV, GITHUB_EVENT_NAME="push"), False, NOW)
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


def test_promotion_gate_passes_an_approved_healthy_release():
    assert gate() == ([], [])


@pytest.mark.parametrize(
    "overrides, why",
    [
        ({"approval_waits": ["#7 is not approved yet"]}, "not approved"),
        ({"hold": "waiting on the billing fix"}, "RELEASE_HOLD"),
        ({"blockers": [{"number": 7, "title": "Tasks vanish"}]}, "release-blocker #7"),
    ],
)
def test_promotion_gate_holds_quietly(overrides, why):
    waits, faults = gate(**overrides)
    assert faults == [] and any(why in w for w in waits)


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
    assert any("approved by chris" in line for line in rt.LOG)


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


# ---- the window job ----


def test_window_open_promotes_then_ships_the_green_candidate_and_closes_the_issues(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200", approved_by="chris")
    gh.add_issue("Release window failing", labels=[rt.ISSUE_LABEL], number=7)
    assert rt.cmd_window(gh, ENV, True, NOW) == 0
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
    # The promoted release's approval closes, the window's failure issue closes, and the new release awaits approval.
    assert gh.closed == [older["number"], 7]
    assert [issue["title"] for issue in open_issues(gh, rt.APPROVAL_LABEL)] == [rt.approval_title("v0.5.201")]
    # Published: the window is closed, and the next event does nothing.
    assert rt.plan_step(gh, NOW + timedelta(minutes=5))["action"] == "none"


def test_a_push_promotes_an_approved_release_before_the_canary_moves_on(installs):
    gh = publishes(FakeGitHub([run(C)]))
    approval_for(gh, "v0.5.200", approved_by="chris")
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200"), ("canary", "0.5.201")]


def test_without_an_approval_stable_waits_and_the_canary_still_ships(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    assert any("no open approval" in line for line in rt.LOG)
    assert [issue["title"] for issue in open_issues(gh, rt.APPROVAL_LABEL)] == [rt.approval_title("v0.5.201")]


def test_a_push_does_not_promote_but_the_backstop_does(installs):
    gh = FakeGitHub([run(C)], published=OPENED + timedelta(minutes=40))  # the window is closed: nothing to ship
    record = approval_for(gh, "v0.5.200", approved_by="chris")
    assert rt.cmd_window(gh, dict(ENV, GITHUB_EVENT_NAME="push"), False, NOW) == 0
    assert installs["updated"] == [] and record["state"] == "open"
    backstop = dict(ENV, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON)
    assert rt.cmd_window(gh, backstop, False, NOW) == 0
    assert installs["updated"] == [("stable", "0.5.200")] and record["state"] == "closed"


def test_a_newer_canary_release_closes_the_older_approval_with_a_link(installs):
    gh = publishes(FakeGitHub([run(C)]))
    older = approval_for(gh, "v0.5.200")  # on the canary, still waiting for approval
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
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
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
    assert "v0.5.201" in gh.releases and gh.latest == [] and COMMANDS == []
    assert "rolled_back" in filed(gh)[0]["body"]
    refused = approval_of(gh, "v0.5.201")
    assert refused["state"] == "closed"
    assert any("Not on the canary" in body for number, body in gh.comments if number == refused["number"])
    assert older["state"] == "open", "the canary still runs v0.5.200, so its approval still stands"


def test_a_failed_image_move_is_reported_and_the_release_stays_not_latest(installs, monkeypatch):
    monkeypatch.setattr(rt, "run_command", lambda argv, timeout=180: (1, "denied: permission_denied"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
    assert gh.latest == [] and "permission_denied" in filed(gh)[0]["body"]


def test_latest_never_moves_backwards(installs):
    gh = publishes(FakeGitHub([run(C)]))
    gh.latest_ = "v0.5.202"  # pushed by a person while the canary was updating
    assert rt.mark_latest(gh, "v0.5.201") is False and COMMANDS == [] and gh.latest == []
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
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
    # The guard cannot read the canary: only the backstop runs the job for it; a push, or an off-season twin with
    # nothing else to do, does not.
    on = {"RELEASE_TRAINS": "on"}
    assert (
        guard(gh, dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON), tmp_path, monkeypatch)["go"]
        == "true"
    )
    assert guard(gh, dict(on, GITHUB_EVENT_NAME="push"), tmp_path, monkeypatch)["go"] == "false"
    twin = dict(on, GITHUB_EVENT_NAME="schedule", SCHEDULE="30 14 * * *")
    assert guard(gh, twin, tmp_path, monkeypatch)["go"] == "false"
    # The canary still runs the older release (rolled back): it stays not latest, quietly.
    assert rt.cmd_window(gh, ENV, False, NOW) == 0 and gh.latest == [] and filed(gh) == []
    installs["canary"].update(running="0.5.201", state="healthy")
    assert rt.cmd_window(gh, ENV, False, NOW) == 0 and gh.latest == ["v0.5.201"] and len(COMMANDS) == 3
    assert rt.lagging_latest(gh) is None


def test_an_unpublished_tag_is_released_again_once_and_recorded(installs):
    gh = publishes(FakeGitHub([run(C)]))
    gh.tags_["v0.5.201"] = B
    gh.release_runs = [release_run()]
    assert rt.cmd_window(gh, dict(ENV, GITHUB_EVENT_NAME="schedule", SCHEDULE=rt.BACKSTOP_CRON), False, NOW) == 0
    assert gh.dispatched == [("release.yml", "v0.5.201", {"desktop": "false", "latest": "false"})]
    assert gh.tagged == [] and ("canary", "0.5.201") in installs["updated"] and gh.latest == ["v0.5.201"]
    record = filed(gh)[0]
    assert record["title"] == "Release v0.5.201 republished" and gh.closed == [record["number"]]
    assert "release/70" in record["body"]


def test_window_validates_main_and_waits_until_the_run_is_listed(installs):
    gh = publishes(FakeGitHub())
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    assert gh.dispatched == [
        ("ci.yml", "main", {"candidate_sha": C, "previous_release": "v0.5.200", "version": "v0.5.201"})
    ]
    assert any("run/50" in line for line in rt.LOG)
    assert gh.tagged == [] and installs["updated"] == [] and gh.created == []
    # The next event sees the run and waits.
    assert rt.plan_step(gh, NOW)["action"] == "wait"


def test_a_red_candidate_goes_on_the_issue_and_main_is_validated(installs):
    gh = publishes(FakeGitHub([run(B, conclusion="failure")]))
    env = dict(
        ENV,
        GITHUB_EVENT_NAME="workflow_run",
        RUN_EVENT="workflow_dispatch",
        RUN_CONCLUSION="failure",
        RUN_TITLE=rt.title(B, "v0.5.201", "v0.5.200"),
        RUN_URL="https://github.example/run/red",
    )
    assert rt.cmd_window(gh, env, False, NOW) == 1
    assert len(gh.created) == 1 and "run/red" in gh.created[0]["body"]
    assert [d[0] for d in gh.dispatched] == ["ci.yml"] and gh.tagged == []


def test_missing_canary_key_fails_without_tagging(installs):
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, dict(ENV, CANARY_UPDATE_KEY=""), False, NOW) == 1
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
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
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
    rt.report(gh, False, ["Run log", "Window: waiting", "promote: holding"], key=["promote: holding"])
    rt.report(gh, False, ["Run log", "Window: something else", "promote: holding"], key=["promote: holding"])
    assert len(gh.created) == 1 and gh.comments == []
    rt.report(gh, False, ["ship: canary rolled back"])
    rt.report(gh, False, ["promote: holding"], key=["promote: holding"])
    assert len(gh.comments) == 2


def test_failures_share_one_issue_and_a_shipped_window_closes_it():
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


# ---- the release shots: a shipped candidate's pictures on its approval issue, and the window's outputs for them ----


def window_outputs(path):
    lines = path.read_text().splitlines()
    keys = [line.split("=", 1)[0] for line in lines]
    assert len(keys) == len(set(keys)), "a key written twice, GitHub keeps only the last: %s" % lines
    return dict(line.split("=", 1) for line in lines)


def test_a_shipped_candidate_hands_its_version_sha_and_base_to_the_shots(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, ENV, False, NOW) == 0
    # The base is the release stable runs: what the approver reads the changes since, and the pictures compare with.
    assert window_outputs(tmp_path / "out") == {
        "reported": "true",
        "shipped": "true",
        "version": "v0.5.201",
        "sha": C,
        "base": "v0.5.199",
    }


def test_the_shipped_candidate_is_in_the_outputs_even_if_the_window_dies_after_it(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))

    def dies(*args, **kwargs):
        raise RuntimeError("the runner went away")

    monkeypatch.setattr(rt, "report", dies)
    with pytest.raises(RuntimeError, match="runner went away"):
        rt.cmd_window(gh, ENV, False, NOW)
    # The canary runs the candidate, so its shots still run; reported is missing, so the job's fallback reports the death.
    assert installs["updated"] == [("canary", "0.5.201")]
    assert window_outputs(tmp_path / "out") == {
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
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
    assert window_outputs(tmp_path / "out") == {
        "reported": "true",
        "shipped": "true",
        "version": "v0.5.201",
        "sha": C,
        "base": "v0.5.199",
    }


def test_a_window_whose_canary_rolls_back_hands_no_candidate_to_the_shots(installs, tmp_path, monkeypatch):
    installs["outcome"] = "rolled_back"
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    assert rt.cmd_window(gh, ENV, False, NOW) == 1
    assert window_outputs(tmp_path / "out") == {"reported": "true", "shipped": "false"}


def test_without_stable_the_pictures_compare_with_the_previous_release(installs, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gh = publishes(FakeGitHub([run(C)]))
    no_stable = {key: value for key, value in ENV.items() if not key.startswith("STABLE")}
    assert rt.cmd_window(gh, no_stable, False, NOW) == 0
    assert installs["updated"] == [("canary", "0.5.201")]
    assert window_outputs(tmp_path / "out")["base"] == "v0.5.200"


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


def test_an_approval_says_the_pictures_are_on_their_way_until_they_arrive():
    gh = FakeGitHub()
    issue = rt.open_approval(gh, "v0.5.201", C, "NOTES v0.5.199..%s" % C)
    assert rt.SHOTS_HEADING in issue["body"] and rt.SHOTS_PENDING in issue["body"]
    assert issue["body"].index(rt.SHOTS_START) < issue["body"].index("NOTES v0.5.199")


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
