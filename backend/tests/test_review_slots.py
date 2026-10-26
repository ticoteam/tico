"""A Dev ticket moves past review (In QA, Pending Release, Post Release QA, Done) only while every required Review 1
and Review 2 slot approves the change its pull request makes now; a person may still move it (Dev 567)."""
import json

import pytest

from backend import review_gate
from backend.hubdb import Refused
from backend.store import H
from backend.tests.test_api import api  # noqa: F401

PR = "https://github.com/acme/app/pull/7"
A, B, C = "a" * 40, "b" * 40, "c" * 40
STEPS = ["In Progress", "Review 1", "Review 1 Fixes", "Review 2", "Review 2 Fixes", "In QA", "QA Fixes",
         "Pending Release", "Post Release QA", "Done"]
STATUS = {"In Progress": "doing", "Review 1 Fixes": "doing", "Review 2 Fixes": "doing", "QA Fixes": "doing",
          "Done": "done"}


def ticket(c, head=A, steps=STEPS):
    typ = H.type_create(c, H.KEEPER, "Dev tickets", [{"name": s, "status": STATUS.get(s, "review")} for s in steps],
                        bots="work")
    row = H.task_create(c, "human:ana", "Improve messages", "Build.", "bot:cpo", type=typ["id"], step="In Progress",
                        lint=False)
    H.task_link(c, "human:ana", row["id"], PR)
    c.execute("UPDATE task_links SET detail_json=?, state='open' WHERE task_id=? AND kind='pr'",
              (json.dumps({"head_sha": head}), row["id"]))
    return row


def child(c, row, title, owner):
    made = H.task_create(c, "bot:cpo", "Look over messages", "Review.", owner, parent_id=row["id"], lint=False)
    c.execute("UPDATE tasks SET title=? WHERE id=?", (title, made["id"]))  # the review gate's own filing is tested elsewhere
    return made


def verdict(c, review, value, head):
    H.record_verdict(c, review["owner"], H.task(c, review["id"]), value, head)


def head(c, row, sha):
    c.execute("UPDATE task_links SET detail_json=? WHERE task_id=? AND kind='pr'", (json.dumps({"head_sha": sha}), row["id"]))


def step(c, row):
    return c.execute("SELECT s.name FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.id=?", (row["id"],)).fetchone()[0]


def approved(c, row):
    r1 = child(c, row, "Review 1: PR #7", "bot:finance")
    verdict(c, r1, "approve", A)
    r2 = child(c, row, "Review 2: PR #7", "bot:inbox")
    verdict(c, r2, "approve", A)
    return r1, r2


@pytest.mark.parametrize("target", ["In QA", "Pending Release", "Post Release QA", "Done"])
def test_an_open_changes_verdict_refuses_every_move_past_review_and_names_slot_reviewer_and_head(api, target):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        verdict(c, r2, "changes", A)
        head(c, row, B)  # the fixes
        with pytest.raises(Refused) as refused:
            H.task_update(c, "bot:cpo", row["id"], step=target)
        text = str(refused.value)
        assert "Review 2 has `changes` at aaaaaaaaaaaa: ask inbox to re-review on head " + B in text
        # Review 1 approved A too, and the fixes changed the code since: it re-reviews the delta as well.
        assert "Review 1 approved aaaaaaaaaaaa" in text and "could not compare" in text
        assert step(c, row) == "In Progress"


def test_a_slot_with_no_verdict_or_never_asked_refuses(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        with pytest.raises(Refused, match="Review 1 was never asked: file Review 1 on head " + A):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")
        r1 = child(c, row, "Review 1: PR #7", "bot:finance")
        with pytest.raises(Refused, match="Review 1 has no verdict: ask finance"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")
        verdict(c, r1, "approve", A)
        H.task_update(c, "bot:cpo", row["id"], step="In QA")  # Review 2 never asked nor entered: a small change
        assert step(c, row) == "In QA"


def test_review_2_is_required_once_the_ticket_entered_it(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        verdict(c, child(c, row, "Review 1: PR #7", "bot:finance"), "approve", A)
        c.execute("UPDATE tasks SET note=? WHERE id=?", (f"Tests: head {A}: 9 passed, 0 failed", row["id"]))
        H.task_update(c, "human:ana", row["id"], step="Review 2")
        with pytest.raises(Refused, match="Review 2 was never asked"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")


def test_re_review_of_the_fixed_head_by_the_same_reviewer_lets_it_through(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        verdict(c, r2, "changes", A)
        head(c, row, B)
        again = child(c, row, "Review 2: PR #7", "bot:inbox")
        verdict(c, again, "approve", B)
        with pytest.raises(Refused, match="Review 1 approved aaaaaaaaaaaa") as refused:
            H.task_update(c, "bot:cpo", row["id"], step="In QA")
        assert "Review 2" not in str(refused.value)
        verdict(c, r1, "approve", B)  # the same child, reopened for the delta
        H.task_update(c, "bot:cpo", row["id"], step="In QA")
        assert step(c, row) == "In QA"


def test_a_later_changes_verdict_outranks_an_earlier_approve_in_another_child(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        verdict(c, child(c, row, "Review 1: PR #7", "bot:finance"), "changes", A)
        with pytest.raises(Refused, match="Review 1 has `changes`"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")


@pytest.mark.parametrize("verdict_value", ["approve", "pass"])
def test_a_qa_verdict_never_fills_a_review_slot(api, verdict_value):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        verdict(c, r2, "changes", A)
        head(c, row, B)
        verdict(c, child(c, row, "QA: PR #7", "bot:ops"), verdict_value, B)
        with pytest.raises(Refused, match="Review 2 has `changes`"):
            H.task_update(c, "bot:cpo", row["id"], step="Pending Release")


def test_an_approve_at_an_older_head_counts_only_when_the_change_is_the_same(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        approved(c, row)
        head(c, row, B)
        link = H.task_links(c, row["id"])[0]["id"]
        with pytest.raises(Refused, match="approved aaaaaaaaaaaa, and GitHub could not compare that change with the "
                                          "one at bbbbbbbbbbbb"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")  # nothing compared: refused
        differs = {link: {"head_sha": B, "at": H.now()}, review_gate.CHANGES: {f"{link} {A}": "+x", f"{link} {B}": "+y"}}
        with pytest.raises(Refused, match="differs"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA", review_prs=differs)
        unreadable = {link: {"head_sha": B, "at": H.now()}, review_gate.CHANGES: {f"{link} {A}": None, f"{link} {B}": "+x"}}
        with pytest.raises(Refused, match="could not compare"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA", review_prs=unreadable)
        main_merge = {link: {"head_sha": B, "at": H.now()}, review_gate.CHANGES: {f"{link} {A}": "+x", f"{link} {B}": "+x"}}
        H.task_update(c, "bot:cpo", row["id"], step="In QA", review_prs=main_merge)
        assert step(c, row) == "In QA"


def test_a_merged_pr_still_counts_and_a_person_may_move_it(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        verdict(c, r2, "changes", A)
        c.execute("UPDATE task_links SET state='merged' WHERE task_id=?", (row["id"],))
        with pytest.raises(Refused, match="Review 2"):
            H.task_update(c, "bot:cpo", row["id"], step="Pending Release")
        H.task_update(c, "human:ana", row["id"], step="Pending Release")
        assert step(c, row) == "Pending Release"


def test_a_failed_head_read_refuses_and_a_ticket_without_a_pr_moves(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        approved(c, row)
        link = H.task_links(c, row["id"])[0]["id"]
        with pytest.raises(Refused, match="cannot read the current head"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA", review_prs={link: {"error": True, "why": "HTTP 502"}})
        with pytest.raises(Refused, match="the PR link changed while GitHub was read") as moved:
            # The preflight read an older head than a webhook stored meanwhile: nobody compared the head it would judge.
            H.task_update(c, "bot:cpo", row["id"], step="In QA", review_prs={link: {"head_sha": B, "at": "2000-01-01"}})
        assert moved.value.retryable  # a fresh preflight reads the stored head, so a plain retry settles it
        typ = row["type_id"]
        board = H.task_create(c, "human:ana", "Plan", "Plan.", "bot:cpo", type=typ, step="In Progress", lint=False)
        H.task_update(c, "bot:cpo", board["id"], step="Done")


def test_moves_before_review_and_back_to_fixes_are_not_checked(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        H.task_update(c, "bot:cpo", row["id"], step="Review 1 Fixes")
        H.task_update(c, "bot:cpo", row["id"], step="QA Fixes")


def test_normalized_change_ignores_line_numbers_blob_ids_and_context():
    one = ("diff --git a/x.py b/x.py\nindex 111..222 100644\n--- a/x.py\n+++ b/x.py\n@@ -1,4 +1,4 @@\n ctx\n-old\n+new\n"
           " more\n")
    two = ("diff --git a/x.py b/x.py\nindex 333..444 100644\n--- a/x.py\n+++ b/x.py\n@@ -9,4 +9,4 @@\n other\n-old\n+new\n"
           " ctx2\n")
    assert review_gate.normalized_change(one) == review_gate.normalized_change(two)
    assert review_gate.normalized_change(one) != review_gate.normalized_change(one.replace("+new", "+new "))
    assert review_gate.normalized_change("") is None


def github(api, monkeypatch, head_now, diffs, posted, status=201, mint=None, merged=False):
    """A synthetic GitHub: PR #7's head, a compare diff per head, and the statuses posted. Once `merged` (with a merge
    commit whose first parent is `p`), every head is behind main, so comparing from main gives an empty diff."""
    from types import SimpleNamespace
    service = api.app.state.github_app
    monkeypatch.setattr(service, "row", lambda c: {"org": "acme"})
    monkeypatch.setattr(service, "mint", mint or (lambda *a, **k: ("synthetic", "later")))
    def call(method, path, **kw):
        if method == "POST":
            posted.append((path, kw["json"]))
            return SimpleNamespace(status_code=status, json=lambda: {})
        if "/compare/" in path:
            since, sha = path.rsplit("/", 1)[1].split("...")
            if merged and since == "main":
                return SimpleNamespace(status_code=200, text="", json=lambda: {})
            return SimpleNamespace(status_code=200 if sha in diffs else 404, text=diffs.get(sha, ""), json=lambda: {})
        pull = {"head": {"sha": head_now[0]}, "base": {"ref": "main"},
                **({"merged": True, "merge_commit_sha": "m" * 40} if merged else {})}
        data = (pull if "/pulls/" in path else {"check_runs": []} if path.endswith("check-runs")
                else [] if path.endswith("statuses") else {"parents": [{"sha": "p" * 40}]})
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, "_call", call)
    return service


DIFF = "diff --git a/x.py b/x.py\nindex {0}..{0} 100644\n--- a/x.py\n+++ b/x.py\n@@ -{1},2 +{1},2 @@\n-old\n+{2}\n"


def setup_ticket(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        c.execute("UPDATE task_links SET repo='acme/app', number=7 WHERE task_id=?", (row["id"],))
        r1, r2 = approved(c, row)
    return row, r1, r2


@pytest.mark.parametrize("main_merge,allowed", [(True, True), (False, False)])
def test_a_bot_move_reads_the_fresh_head_and_compares_changes_on_github(api, monkeypatch, main_merge, allowed):
    from backend.tests.test_ceo_bot import as_bot
    row, _, _ = setup_ticket(api)
    diffs = {A: DIFF.format("111", 3, "new"), B: DIFF.format("222", 40, "new" if main_merge else "newer")}
    github(api, monkeypatch, [B], diffs, [])
    with api.app.state.store.read() as c:
        version = H.task(c, row["id"])["version"]
    r = api.post("/api/v2/tasks/" + row["id"], headers=as_bot(api, "cpo"), json={"version": version, "step": "In QA"})
    assert r.status_code == (200 if allowed else 422), r.text
    if not allowed:
        assert "Review 1 approved aaaaaaaaaaaa" in r.text and B in r.text


def test_the_reviews_status_follows_the_slots_on_the_current_head(api, monkeypatch):
    from backend import review_status
    row, r1, r2 = setup_ticket(api)
    posted = []
    service = github(api, monkeypatch, [A], {}, posted)
    review_status.sync(service, row["id"])
    assert posted == [("/repos/acme/app/statuses/" + A, {"state": "success", "context": "tico/reviews",
                                                        "description": "Every required review approves this change"})]
    review_status.sync(service, row["id"])
    assert len(posted) == 1  # the same status is not posted twice
    review_status._dirty.clear()
    with api.app.state.store.transaction() as c:
        verdict(c, r2, "changes", A)
        assert row["id"] not in review_status._dirty
    assert row["id"] in review_status._dirty  # marked once the verdict commits
    review_status.sync(service, row["id"])
    state, text = posted[-1][1]["state"], posted[-1][1]["description"]
    assert state == "failure" and text.startswith("Review 2 has `changes`") and "inbox" in text and len(text) <= 140
    with api.app.state.store.transaction() as c:
        verdict(c, child(c, row, "QA: PR #7", "bot:ops"), "approve", A)
    review_status.sync(service, row["id"])
    assert posted[-1][1]["state"] == "failure"  # QA never fills Review 2


def test_without_the_statuses_permission_nothing_is_posted_and_the_link_says_why(api, monkeypatch):
    from backend import review_status
    from backend.store import Problem
    row, _, _ = setup_ticket(api)
    posted = []
    def mint(repos, permissions, **kw):
        if permissions.get("statuses") == "write":
            raise Problem("github_app_permissions", "missing", 403)
        return "synthetic", "later"
    service = github(api, monkeypatch, [A], {}, posted, mint=mint)
    review_status.sync(service, row["id"])
    assert posted == []
    with api.app.state.store.read() as c:
        status = json.loads(H.task_links(c, row["id"])[0]["detail_json"])["review_status"]
    assert status["posted"] is False and "github_app_permissions" in status["why"] and status["state"] == "success"


def test_a_new_pr_head_marks_the_ticket(api):
    from backend import github, review_status
    row, _, _ = setup_ticket(api)
    review_status._dirty.clear()
    with api.app.state.store.transaction() as c:
        github.pull_request(c, {"action": "synchronize", "pull_request": {"html_url": PR, "head": {"sha": B}}})
    assert row["id"] in review_status._dirty


@pytest.mark.parametrize("same_change", [True, False])
def test_a_merged_pr_compares_from_the_merge_commits_first_parent(api, monkeypatch, same_change):
    from backend.tests.test_ceo_bot import as_bot
    row, _, _ = setup_ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged' WHERE task_id=?", (row["id"],))
    # Approved at A; merged at B, which is A plus a clean main merge (or a further change).
    diffs = {A: DIFF.format("111", 3, "new"), B: DIFF.format("222", 40, "new" if same_change else "newer")}
    github(api, monkeypatch, [B], diffs, [], merged=True)
    with api.app.state.store.read() as c:
        version = H.task(c, row["id"])["version"]
    r = api.post("/api/v2/tasks/" + row["id"], headers=as_bot(api, "cpo"),
                 json={"version": version, "step": "Pending Release"})
    assert r.status_code == (200 if same_change else 422), r.text
    if not same_change:
        assert "differs from it" in r.text


def test_the_tickets_owner_never_fills_its_own_review_slot(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        own = child(c, row, "Review 1: PR #7", "bot:cpo")  # filed to itself
        verdict(c, own, "approve", A)
        with pytest.raises(Refused, match="Review 1 was never asked"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")
        r1 = child(c, row, "Review 1: PR #7", "bot:finance")
        c.execute("UPDATE tasks SET owner='bot:cpo' WHERE id=?", (r1["id"],))  # or takes a reviewer's child over
        verdict(c, H.task(c, r1["id"]), "approve", A)
        with pytest.raises(Refused, match="Review 1 was never asked"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")
        verdict(c, child(c, row, "Review 1: PR #7", "bot:finance"), "approve", A)  # control: a reviewer's own
        H.task_update(c, "bot:cpo", row["id"], step="In QA")


def test_the_reviews_status_ignores_the_owners_own_approval(api, monkeypatch):
    from backend import review_status
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        c.execute("UPDATE task_links SET repo='acme/app', number=7 WHERE task_id=?", (row["id"],))
        verdict(c, child(c, row, "Review 1: PR #7", "bot:cpo"), "approve", A)
    posted = []
    review_status.sync(github(api, monkeypatch, [A], {}, posted), row["id"])
    assert posted[-1][1]["state"] == "failure" and posted[-1][1]["description"].startswith("Review 1 was never asked")


@pytest.mark.parametrize("hide", ["decline", "unlink", "retitle"])
def test_a_changes_verdict_survives_declining_unlinking_or_retitling_its_child(api, hide):
    from backend import task_relations as TR
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1, r2 = approved(c, row)
        later = child(c, row, "Review 1: PR #7", "bot:finance")
        verdict(c, later, "changes", A)
        if hide == "decline":
            H.task_update(c, "bot:cpo", later["id"], status="declined")
        elif hide == "unlink":
            TR.relate(c, "bot:cpo", later["id"], row["id"], kind="parent", remove=True)
        else:
            H.task_update(c, "bot:cpo", later["id"], title="Look over messages again")
        with pytest.raises(Refused, match="Review 1 has `changes` at aaaaaaaaaaaa"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")


def test_a_verdict_from_before_the_ticket_kept_copies_counts_only_under_its_filed_title(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        r1 = child(c, row, "Review 1: PR #7", "bot:finance")
        # Given before verdicts were copied onto the ticket: only the child's own event exists.
        H._task_event(c, r1["id"], "bot:finance", "verdict", None, "approve " + A, "")
        H.task_update(c, "bot:cpo", row["id"], step="In QA")
        H.task_update(c, "human:ana", row["id"], step="In Progress")
        qa = child(c, row, "QA: PR #7", "bot:ops")
        H._task_event(c, qa["id"], "bot:ops", "verdict", None, "approve " + A, "")
        H.task_update(c, "bot:cpo", r1["id"], title="QA: PR #7")
        H.task_update(c, "bot:cpo", qa["id"], title="Review 1: PR #7")  # a QA approval renamed into the slot
        with pytest.raises(Refused, match="Review 1 has no verdict"):
            H.task_update(c, "bot:cpo", row["id"], step="In QA")


def test_normalized_change_keeps_hunk_breaks_and_gives_up_on_binary_files():
    one = "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n+a\n+b\n"
    split = "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n+a\n@@ -50 +50 @@\n+b\n"
    assert review_gate.normalized_change(one) != review_gate.normalized_change(split)
    binary = "diff --git a/x.png b/x.png\nindex 1..2 100644\nBinary files a/x.png and b/x.png differ\n"
    assert review_gate.normalized_change(binary) is None


def test_a_failed_post_is_tried_again_after_a_wait(api, monkeypatch):
    from backend import review_status
    row, _, _ = setup_ticket(api)
    posted = []
    service = github(api, monkeypatch, [A], {}, posted, status=500)
    review_status._dirty.clear()
    review_status._retry.clear()
    review_status._started["done"] = True
    review_status.mark(None, row["id"])
    review_status.tick(service)
    assert len(posted) == 1 and row["id"] in review_status._retry
    review_status.tick(service)
    assert len(posted) == 1  # not yet due
    review_status._retry[row["id"]] = (0, 1)
    github(api, monkeypatch, [A], {}, posted)
    review_status.tick(service)
    assert len(posted) == 2 and posted[-1][1]["state"] == "success" and row["id"] not in review_status._retry
