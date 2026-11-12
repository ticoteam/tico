"""The Product and Engineering cases of the fault rig (plan gap G18, track E): the acceptance case `idea-to-done`, the
refusals, and the spec scenarios they cover (docs/pipelines-v3.md, "Scenarios the rig and the spot checks must pass").

Each case runs on a software company's world (`software.soft_rig`): the real presets, engine and requirement kinds,
the rig's GitHub and Jev, a check pass and the rig's per-step checks after every step.

- CASES: name -> Case. `covers` lists the spec scenarios a case passes for (they leave scenarios.LATER).
- PENDING: name -> the tracks a case waits on. On the shared base the presets, kinds and queue are stubs, so these end
  in `software.NotBuiltYet`; the test suite runs them as strict expected failures of exactly that, so each turns red the
  day its tracks land, and its name comes off this list. A case that fails any other way is a real failure."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import software as S
from .fake_github import Delivery
from .fake_jev import FAIL, NOT_MET, UNSURE
from .scenarios import expect
from .software import ENGINEERING, PRODUCT, SoftRig, soft_rig
from .world import ANA, BUILDER, EM, PM, PPM, RELEASE, REVIEWER_1, REVIEWER_2, Refusal

REF_1, REF_2 = "acme/app#1", "acme/app#2"


@dataclass(frozen=True)
class Case:
    name: str
    what: str
    run: Callable[[SoftRig], None]
    covers: tuple[int, ...] = ()


CASES: dict[str, Case] = {}

# The tracks each case waits on, as they stand on the shared base (chris/pe-base). Lift a name when they land.
PENDING: dict[str, str] = {}


def case(name: str, what: str, covers: tuple[int, ...] = ()) -> Callable[[Callable[[SoftRig], None]], Any]:
    def add(fn: Callable[[SoftRig], None]) -> Callable[[SoftRig], None]:
        assert name not in CASES, name
        CASES[name] = Case(name, what, fn, covers)
        return fn

    return add


def run(c: Case, seed: int = 1) -> None:
    with soft_rig(seed) as r:
        c.run(r)


def covered() -> set[int]:
    return {n for c in CASES.values() for n in c.covers}


# ----------------------------------------------------------------------------- the acceptance case
@case("idea-to-done", "One idea from Ideas to Done through every Engineering stage", covers=(27, 28, 29, 51))
def idea_to_done(r: SoftRig) -> None:
    from backend import task_engine as E

    # 1. A person files the request: once Jev's duplicate check answered, it goes into Spec by itself on the next pass.
    #    The PM attaches a spec, Jev passes it, a person OKs it.
    p = r.idea("Export to CSV")
    r.at(p, "Ideas")
    expect([s for s, _ in r.waits(p)] == ["jev"], f"a person's request in Ideas waits on {r.waits(p)}, not Jev")
    r.jev()
    r.check()
    r.at(p, "Spec")
    expect(r.owner(p) == PM, f"Spec's owner is {r.owner(p)}")
    r.says(p, "No page attached")
    spec = r.page(PM, "Spec: export to CSV")
    r.attach(p, spec, PM)
    expect([s for s, _ in r.waits(p)] == ["jev"], f"after the attach it waits on {r.waits(p)}, not Jev")
    r.says(p, "Jev is checking")
    r.jev()
    r.check()
    person = r.person_on(p)
    expect(person.startswith("human:"), f"the spec's OK is {person}'s")
    r.says(p, f"version {spec.version}")
    r.ok_page(p, person)

    # 2. In development files one Engineering task, linked, with the spec at the version that was OK'd.
    r.at(p, "In development")
    filed = r.filed(p)
    expect(len(filed) == 1, f"{len(filed)} tasks filed")
    e = filed[0]
    row = r.row(e)
    expect(row["pipeline_id"] == ENGINEERING, f"filed into {row['pipeline_id']}")
    expect(row["title"] == "Export to CSV", f"the filed task is {row['title']!r}")
    expect(spec.ref in str(row.get("body") or ""), f"the filed task's body does not name {spec.ref}")
    expect(any(ev["kind"] == "handoff_filed" for ev in r.events(p)), "no handoff_filed in the history")
    expect(r.waits(p) == [("tasks", e)], f"the Product task waits on {r.waits(p)}")
    r.check()
    r.at(e, "Build")
    expect(r.owner(e) == BUILDER, f"Build's owner is {r.owner(e)}")
    r.says(e, "No PR linked")

    # 3. The Builder opens two PRs; checks run, then pass.
    # PR 2 is linked a second before PR 1: the task shows its first linked PR's line, and with both linked in the same
    # frozen millisecond the seeded ids would decide which that is (step 7 reads PR 2's).
    r.pr(e, 2)
    r.w.shift(1)
    r.pr(e, 1)
    r.settle()
    r.says(e, "Checks running")
    expect(("source", "github") in r.waits(e), f"running checks wait on {r.waits(e)}, not GitHub")
    r.settle(r.gh.ci_s)

    # 4. Review 1 at the head; a new commit puts it back to the reviewer (scenario 28), who attaches again.
    r.at(e, "Review 1")
    expect(r.owner(e) == REVIEWER_1, f"Review 1's owner is {r.owner(e)}")
    review = r.page(REVIEWER_1, "Review 1: the export")
    r.attach(e, review, REVIEWER_1)
    r.gh.push(1)
    r.settle()
    r.jev()
    r.check()
    r.at(e, "Review 1")
    r.says(e, "New commits since the review")
    expect(r.waits(e) == [("bot", REVIEWER_1)], f"the new commit left it waiting on {r.waits(e)}")
    r.attach(e, r.page(REVIEWER_1, "Review 1: the export", page=review), REVIEWER_1)
    r.jev()
    r.settle()

    # 5. Fixes 1: done and green. Review 2 is another bot.
    r.at(e, "Fixes 1")
    r.settle(r.gh.ci_s)
    expect(r.say_done(e, BUILDER) is not None, "the Builder could not say done")
    r.check()
    r.at(e, "Review 2")
    expect(r.owner(e) == REVIEWER_2 and REVIEWER_2 != REVIEWER_1, f"Review 2's owner is {r.owner(e)}")
    r.attach(e, r.page(REVIEWER_2, "Review 2: the export"), REVIEWER_2)
    r.jev()
    r.settle()

    # 6. Fixes 2: what was built, Jev, then a person's OK. On to Merge, and the PRs are queued.
    r.at(e, "Fixes 2")
    r.attach(e, r.page(BUILDER, "What was built: the export"), BUILDER)
    r.jev()
    r.check()
    r.ok_page(e)
    r.at(e, "Merge")
    expect(r.owner(e) == RELEASE, f"Merge's owner is {r.owner(e)}")
    r.effects()
    r.settle()
    expect(r.gh.state(1)["queue_state"] == "queued" and r.gh.state(2)["queue_state"] == "queued", "not both queued")
    r.says(e, "In the merge queue")

    # 7. A commit after the OK (scenario 51): GitHub takes PR 2 out; the task stays in Merge, the Release Owner is
    # told once, the manager once. No bot may queue the new commit (the OK was for the old one): it goes back to
    # Fixes 2. A person may queue it, once its checks pass.
    r.gh.push(2)
    r.settle()
    r.at(e, "Merge")
    r.says(e, "Dropped from the merge queue: A new commit was pushed")
    expect(r.waits(e) == [("bot", RELEASE)], f"the drop waits on {r.waits(e)}")
    removed = [w for w in r.wakes(e, RELEASE) if w[0].startswith("queue_removed")]
    expect(len(removed) == 1, f"the Release Owner was told {len(removed)} times: {removed}")
    changed = [w for w in r.wakes(e, EM) if w[0].startswith("changed_after_ok")]
    expect(len(changed) == 1, f"the manager was told of the change after the OK {len(changed)} times")
    out = r.queue_again(e)
    expect(isinstance(out, Refusal) and "Move the task back to Fixes 2" in out.detail, f"a bot queued it: {out!r}")
    out = r.queue_again(e, ANA)
    expect(isinstance(out, Refusal) and out.rule == "task_requirement", f"queue again while checks run: {out!r}")
    expect(f"The checks on {REF_2} are still running" in out.detail, out.detail)
    r.settle(r.gh.ci_s)
    expect(not isinstance(r.queue_again(e, ANA), Refusal), "the person's queue again once green was refused")
    r.effects()
    r.settle()
    expect(len([w for w in r.wakes(e, RELEASE) if w[0].startswith("queue_removed")]) == 1, "told again")

    # Two PRs, one merges: it stays in Merge. Both merge: it moves (scenario 29).
    r.gh.merge(1)
    r.settle()
    r.at(e, "Merge")
    r.gh.merge(2)
    r.settle()

    # 8. Release: a published pre-release containing the merge counts (prereleases are on by default).
    r.at(e, "Release")
    r.says(e, "Waiting for the next release of acme/app")
    r.gh.publish("v1.1.0-rc.1", prerelease=True)
    r.settle(300)
    expect(r.row(e)["status"] == "done", f"the Engineering task is {r.row(e)['status']}: {r.lines(e)}")

    # 9. The Product task's Linked work done is met: Validate.
    r.check()
    r.at(p, "Validate")
    expect(r.owner(p) == PM, f"Validate's owner is {r.owner(p)}")

    # 10. Production still runs the older version: Jev says not yet, nothing is filed (scenario 27), the PM snoozes.
    older = r.page(PM, "Checked in production: production runs v1.0.0", verdict=NOT_MET)
    r.attach(p, older, PM)
    r.jev()
    r.check()
    r.says(p, "Jev sent it back")
    expect(r.waits(p) == [("bot", PM)], f"Jev's not yet waits on {r.waits(p)}")
    expect(r.filed(p) == [e], f"a not yet filed {r.filed(p)[1:]}")
    until = S._h().shift(r.w.now(), seconds=3600)
    r.ok(PM, lambda c: E.snooze(c, p, PM, "Waiting for the deploy", until, r.version(p)), "the PM snoozes")
    r.later(hours=1, seconds=10)
    r.attach(p, r.page(PM, "Checked in production: production runs v1.1.0", page=older), PM)
    r.jev()
    r.check()
    expect(r.row(p)["status"] == "done", f"the Product task is {r.row(p)['status']}: {r.lines(p)}")

    # 11. Every stage in order.
    expect(r.visited(p) == ["Ideas", "Spec", "In development", "Validate", "Done"], f"Product: {r.visited(p)}")
    want = ["On deck", "Build", "Review 1", "Fixes 1", "Review 2", "Fixes 2", "Merge", "Release", "Done"]
    expect(r.visited(e) == want, f"Engineering: {r.visited(e)}")


# ----------------------------------------------------------------------------- presets
@case("presets", "A new org gets the presets: owners, requirements, On deck and Ideas empty", covers=(19,))
def presets(r: SoftRig) -> None:
    from backend import pipeline_settings as PS

    product = r.w.read(lambda c: PS.read(c, r.preset(PRODUCT)["id"]))
    engineering = r.w.read(lambda c: PS.read(c, r.preset(ENGINEERING)["id"]))
    expect([s["name"] for s in product["stages"]] == list(S.PRODUCT_STAGES), f"Product: {product['stages']}")
    expect([s["name"] for s in engineering["stages"]] == list(S.ENGINEERING_STAGES), "Engineering's stages")
    expect(product["manager_id"] == PPM and engineering["manager_id"] == EM, "the managers")
    for record in (product, engineering):
        for stage in record["stages"]:
            expect(stage["owner"], f"{stage['name']} has no owner")
            if stage["name"] not in ("On deck", "Ideas"):  # nothing asked: a task moves on when there is room
                expect(stage["requirements"], f"{stage['name']} has no requirement")
    on_deck = next(s for s in engineering["stages"] if s["name"] == "On deck")
    expect(not on_deck["requirements"], f"On deck has {on_deck['requirements']}")
    stages = {s["name"]: s for s in [*product["stages"], *engineering["stages"]]}
    expect(not stages["Ideas"]["by_hand"], "tasks leave Ideas by hand")
    expect(stages["Spec"]["task_limit"] == 20 and stages["Build"]["task_limit"] == 3, "the limits")
    owners = {name: stages[name]["owner"] for name in ("Build", "Review 1", "Review 2", "Merge", "Release")}
    want = {"Build": BUILDER, "Review 1": REVIEWER_1, "Review 2": REVIEWER_2, "Merge": RELEASE, "Release": RELEASE}
    expect(owners == want, f"owners {owners}")
    expect(stages["In development"]["on_enter"] == {"file_into": ENGINEERING}, "In development files")
    expect(stages["Merge"]["on_enter"] == {"act": "github.queue"}, "Merge queues")
    for name, kind in (("Merge", "pr_merged"), ("Release", "in_release")):
        found = [q for q in stages[name]["requirements"] if q["condition"] == kind]
        expect(found and not found[0]["can_push_past"], f"{name}'s {kind} can be pushed past")


@case("no-software", "A company that makes no software gets no Product or Engineering pipeline", covers=())
def no_software(r: SoftRig) -> None:
    from backend import onboarding as O
    from backend import pipeline_settings as PS

    r.preset(PRODUCT)
    r.preset(ENGINEERING)

    def say_no(c: Any) -> None:
        record = O.load(c)
        record["answers"]["software_product"] = "no"
        O.Onboarding._store(c, record, ANA)
        PS.ensure_presets(c)  # the next start

    r.raw(say_no)
    from backend import task_engine as E

    for pid in (PRODUCT, ENGINEERING):
        found = r.w.read(lambda c, pid=pid: E.pipeline(c, pid))
        expect(not found or found.get("retired_at"), f"{pid} is still live after the company said no")
    r.raw(lambda c: PS.ensure_presets(c))
    for pid in (PRODUCT, ENGINEERING):
        found = r.w.read(lambda c, pid=pid: E.pipeline(c, pid))
        expect(not found or found.get("retired_at"), f"a restart brought {pid} back")
    r.check()


# ----------------------------------------------------------------------------- refusals
@case("bot-past-people-only-ok", "A bot tries to push past the people-only Spec OK", covers=())
def bot_past_people_only_ok(r: SoftRig) -> None:
    from backend import task_engine as E

    p = r.idea("Dark mode")
    r.by_hand(p)
    # The preset's Spec OK is "People, with exceptions" (the default: a bot may push past with a reason); the owner
    # makes it People only, as a requirement can be at any time.
    r.raw(
        lambda c: c.execute(
            "UPDATE requirements SET who_oks='people_only' WHERE stage_id=? AND kind='page'", (r.row(p)["stage_id"],)
        )
    )
    r.attach(p, r.page(PM, "Spec: dark mode"), PM)
    r.jev()
    r.check()
    r.person_on(p)
    version = r.version(p)
    out = r.refused(PM, lambda c: E.move(c, p, E.NEXT, PM, "Nobody is around", version), "task_needs_person", "PM")
    expect("needs a person" in out.detail, out.detail)
    r.at(p, "Spec")
    r.ok(ANA, lambda c: E.move(c, p, E.NEXT, ANA, "I read it in the call"), "a person pushes past")
    r.at(p, "In development")


@case("push-past-pr-merged", "Anyone tries to push past PR merged: refused, offered Move back and Close", covers=(17,))
def push_past_pr_merged(r: SoftRig) -> None:
    from backend import task_engine as E

    r.preset(ENGINEERING)
    e = r.task(ANA, "Fix the export header", ENGINEERING)
    r.to_merge(e)
    for who in (RELEASE, ANA):
        version = r.version(e)
        out = r.refused(
            who,
            lambda c, who=who, version=version: E.move(c, e, E.NEXT, who, "Merged it by hand", version),
            "task_no_push_past",
            f"{who} pushes past PR merged",
        )
        expect("Move back or close" in out.detail, f"no Move back and Close: {out.detail}")
    r.at(e, "Merge")
    r.ok(RELEASE, lambda c: E.move(c, e, "Fixes 2", RELEASE, "Needs the Builder", r.version(e)), "Move back")
    r.effects()
    r.settle()
    expect(r.gh.state(1)["queue_state"] == "none", f"the PR stayed queued: {r.gh.state(1)}")


@case(
    "spec-full",
    "A third idea into Spec at its limit of 2 waits; a freed slot takes it in, with no notice",
    covers=(20,),
)
def spec_full(r: SoftRig) -> None:
    from backend import task_engine as E

    pid = r.preset(PRODUCT)["id"]
    with r.w.tx() as c:  # the preset gives Spec 20; a limit of 2 keeps the case small
        c.execute("UPDATE stages SET task_limit=2 WHERE pipeline_id=? AND name='Spec'", (pid,))
    ideas = [r.idea(f"Idea {i}") for i in range(1, 4)]  # a person's: not snoozed
    brief = r.idea("The PM's own brief", by=PM)  # filed snoozed: it waits for a person
    r.jev()
    r.check()
    for p in ideas[:2]:
        r.at(p, "Spec")
    third = ideas[2]
    expect(r.stage_of(third) == "Ideas", "a third idea went into a full Spec")
    expect([s for s, _ in r.waits(third)] == ["room"], f"the third idea waits on {r.waits(third)}, not room")
    r.ok(ANA, lambda c: E.move(c, ideas[0], E.CLOSED, ANA, "Not this quarter"), "an idea in Spec closes")
    r.check()
    r.at(third, "Spec")
    r.later(hours=2)
    r.check()
    expect(not r.room_wakes(PM), f"the PM was sent a room notice: {r.room_wakes(PM)}")
    expect(r.stage_of(brief) == "Ideas", "the PM's own brief left Ideas while snoozed")


@case("close-product-while-queued", "Close the Product task while its PR is queued: the PR is pulled", covers=(24, 31))
def close_product_while_queued(r: SoftRig) -> None:
    from backend import task_engine as E

    p, e = r.to_development("Bulk delete")
    r.to_merge(e)
    expect(r.gh.state(1)["queue_state"] == "queued", f"the PR is not queued: {r.gh.state(1)}")
    r.ok(ANA, lambda c: E.move(c, p, E.CLOSED, ANA, "The customer cancelled"), "close the Product task")
    expect(r.row(e)["status"] == "closed", f"the Engineering task is {r.row(e)['status']}")
    note = [ev for ev in r.events(e) if ev["kind"] == "move"][-1]["note"]
    expect(note and "Bulk delete" in note, f"its note: {note!r}")
    r.effects()
    r.settle(r.gh.land_after_s)
    expect(r.gh.state(1)["state"] == "open" and r.gh.state(1)["queue_state"] == "none", f"{r.gh.state(1)}")
    expect(any(ev["kind"] == "pr_dequeued" for ev in r.events(e)), "no pr_dequeued in the history")


@case("close-product-after-merge", "Close a Product task whose Engineering task's PR merged: that task stays open")
def close_product_after_merge(r: SoftRig) -> None:
    from backend import task_engine as E

    p, e = r.to_development("Saved filters")
    r.to_merge(e)
    r.settle(r.gh.land_after_s)
    r.at(e, "Release")
    r.ok(ANA, lambda c: E.move(c, p, E.CLOSED, ANA, "Dropped from the roadmap"), "close the Product task")
    expect(r.row(e)["status"] == "open", f"a task whose PR merged was {r.row(e)['status']}")


@case("engineering-closed", "Close the Engineering task: the Product task stays, the PM's turn, with why", covers=(23,))
def engineering_closed(r: SoftRig) -> None:
    from backend import task_engine as E

    p, e = r.to_development("Import from Excel")
    r.check()
    version = r.version(e)
    r.ok(BUILDER, lambda c: E.move(c, e, E.CLOSED, BUILDER, "Needs no code: a setting does it", version), "close E")
    r.check()
    r.at(p, "In development")
    expect(r.waits(p) == [("bot", PM)], f"the Product task waits on {r.waits(p)}")
    r.says(p, "Needs no code: a setting does it")


@case("not-validated-twice", "'Not validated' twice: each is a new linked Engineering task", covers=(26,))
def not_validated_twice(r: SoftRig) -> None:
    from backend import task_engine as E

    p, first = r.to_development("Faster search")
    r.ship(first, "v2.0.0")
    r.check()
    r.at(p, "Validate")
    tasks = [first]
    for n, tag in ((2, "v2.0.1"), (3, "v2.0.2")):
        version = r.version(p)
        r.ok(
            PM,
            lambda c, n=n, version=version: E.move(c, p, "In development", PM, f"Not validated, try {n}", version),
            "not validated",
        )
        r.at(p, "In development")
        filed = r.filed(p)
        expect(len(filed) == n and filed[:-1] == tasks, f"filing {n}: {filed}")
        tasks = filed
        new = filed[-1]
        expect(r.row(new)["status"] == "open" and r.row(new)["pipeline_id"] == ENGINEERING, "the new filing")
        expect(all(r.row(t)["status"] == "done" for t in filed[:-1]), "an earlier filing changed")
        r.ship(new, tag)
        r.check()
        r.at(p, "Validate")


@case("handoff-loop", "Engineering filing back into Product would loop: refused", covers=())
def handoff_loop(r: SoftRig) -> None:
    from backend import pipeline_settings as PS
    from backend import task_engine as E

    r.preset(PRODUCT)
    record = r.w.read(lambda c: PS.read(c, r.preset(ENGINEERING)["id"]))
    release = next(s for s in record["stages"] if s["name"] == "Release")
    release["on_enter"] = {"file_into": PRODUCT}
    release["requirements"].append({"kind": "linked_done"})
    out = r.refused(E.SYSTEM, lambda c: PS.save(c, E.SYSTEM, record), "task_settings", "the loop")
    expect("would loop" in out.detail, out.detail)


# ----------------------------------------------------------------------------- the merge queue
@case(
    "queue-removed-three-choices",
    "GitHub drops a PR: the Release Owner is told once; queue again (refused while red), fix it, or send it back",
    covers=(30,),
)
def queue_removed_three_choices(r: SoftRig) -> None:
    from backend import task_engine as E

    r.preset(ENGINEERING)
    e = r.task(ANA, "Paginate the API", ENGINEERING)
    r.to_merge(e)
    out = r.queue_again(e)
    expect(isinstance(out, Refusal) and out.rule == "task_requirement", f"queue again with nothing removed: {out!r}")
    # GitHub drops it with a failing check.
    r.gh.finish_checks(1, "failure")
    r.gh.drop(1, "Checks failed in the merge queue", "check-1")
    r.settle()
    r.at(e, "Merge")
    r.says(e, "Dropped from the merge queue: Checks failed in the merge queue (check-1)")
    expect(r.waits(e) == [("bot", RELEASE)], f"the drop waits on {r.waits(e)}")
    r.settle(3600)
    expect(len([w for w in r.wakes(e, RELEASE) if w[0].startswith("queue_removed")]) == 1, "told more than once")
    expect(r.gh.state(1)["queue_state"] == "removed", "Tico queued it again by itself")
    # (a) while red: refused with the reason.
    out = r.queue_again(e)
    expect(isinstance(out, Refusal) and out.rule == "task_requirement", f"queue again while red: {out!r}")
    expect(f"Checks failed on {REF_1}" in out.detail, out.detail)
    # (b) a fix is pushed: the person's OK was for the old commit, so the Release Owner may not queue it (it goes
    # back to Fixes 2); a person may, once its checks pass.
    r.gh.push(1)
    r.settle()
    out = r.queue_again(e)
    expect(isinstance(out, Refusal) and "Move the task back to Fixes 2" in out.detail, f"a bot queued it: {out!r}")
    out = r.queue_again(e, ANA)
    expect(isinstance(out, Refusal) and f"The checks on {REF_1} are still running" in out.detail, f"{out!r}")
    r.settle(r.gh.ci_s)
    expect(not isinstance(r.queue_again(e, ANA), Refusal), "the person's queue again once green was refused")
    r.effects()
    r.settle()
    expect(r.gh.state(1)["queue_state"] == "queued", f"not queued again: {r.gh.state(1)}")
    # (c) dropped again: sent back to Fixes 2, where the Builder has it.
    r.gh.drop(1, "Merge conflict", None)
    r.settle()
    r.ok(RELEASE, lambda c: E.move(c, e, "Fixes 2", RELEASE, "A conflict the Builder must fix", r.version(e)), "back")
    r.at(e, "Fixes 2")
    expect(r.owner(e) == BUILDER, f"Fixes 2 is {r.owner(e)}'s")


@case("github-refuses-the-merge", "GitHub is down: the queue call is tried 3 times, then it is the owner's turn")
def github_refuses_the_merge(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    from backend import task_engine as E

    e = r.task(ANA, "Rate limits", ENGINEERING)
    r.pr(e, 1)
    r.settle(r.gh.ci_s)  # the PR is read and green: its head is known, so entering Merge owes its queue call
    r.gh.down_until = S._h().shift(r.w.now(), seconds=10 * 86400)
    if r.stage_of(e) != "Merge":
        r.ok(ANA, lambda c: E.move(c, e, "Merge", ANA, "Reviewed in the call"), "Ana drags it to Merge")
    r.at(e, "Merge")
    r.says(e, "Queuing the PR")
    r.effects()
    for gap in (60, 300, 900):
        r.settle(gap + 10)
    r.at(e, "Merge")
    r.says(e, "GitHub refused the merge: GitHub is unavailable")
    expect(r.waits(e) == [("bot", RELEASE)], f"waits on {r.waits(e)}")
    tried = r.gh.stats["call ready"]  # each try marks the PR ready for review first, then queues it
    expect(tried == 3, f"the queue call was tried {tried} times, not 3")


@case("github-down-for-days", "GitHub is down for days before the PR is read: Late hands it on, then a person has it")
def github_down_for_days(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    e = r.task(ANA, "Webhook retries", ENGINEERING)
    r.gh.down_until = S._h().shift(r.w.now(), seconds=10 * 86400)
    r.pr(e, 1)  # linked while GitHub is down: no webhook arrives and every read fails
    r.settle()  # On deck: it moves into Build on the next pass
    r.at(e, "Build")
    r.says(e, "Reading the PR from GitHub")
    r.settle(3600)
    expect(not r.wakes(e, EM), f"the manager was told before Build's expected day: {r.wakes(e, EM)}")
    r.settle(86400)
    late = [w for w in r.wakes(e, EM) if w[0].endswith(":late") or w[0].startswith("late") or "Late" in w[2]]
    expect(late, f"a day in Build waiting on GitHub did not reach the manager: {r.wakes(e)}")
    r.settle(2 * 86400 + 60)
    expect(r.visit(e).get("level") == "person", f"three days on GitHub did not reach a person: {r.visit(e)}")


@case("closed-task-pr-merged-anyway", "Close a task in Merge after GitHub merged its PR: the manager is told once")
def closed_task_pr_merged_anyway(r: SoftRig) -> None:
    from backend import task_engine as E

    r.preset(ENGINEERING)
    e = r.task(ANA, "Audit log", ENGINEERING)
    r.to_merge(e)
    r.gh.merge(1)
    r.gh.lose_all()  # the merge's webhook never arrives
    r.ok(ANA, lambda c: E.move(c, e, E.CLOSED, ANA, "Not needed"), "close it")
    r.effects()
    r.settle()
    r.settle()
    told = [w for w in r.wakes(e, EM) if w[0].startswith("merged_anyway")]
    expect(len(told) == 1, f"the manager was told {len(told)} times that the PR merged anyway")


# ----------------------------------------------------------------------------- checks
@case("no-checks", "A repo with no checks: Checks pass is met 10 minutes after the commit, with the note", covers=(34,))
def no_checks(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    e = r.task(ANA, "Typo in the footer", ENGINEERING)
    r.check()
    r.at(e, "Build")
    r.pr(e, 1, checks=None)
    r.settle()
    r.at(e, "Build")
    r.says(e, "Waiting for checks")
    r.settle(600)
    r.at(e, "Review 1")


@case("checks-run-too-long", "Checks still running after 2 hours: the owner's turn, with why")
def checks_run_too_long(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    e = r.task(ANA, "Slow suite", ENGINEERING)
    r.check()
    r.pr(e, 1)
    r.gh.ci(S.REPO, r.gh.head(1), "success", after=10 * 3600)
    r.settle()
    r.says(e, "Checks running")
    r.settle(2 * 3600 + 10)
    r.says(e, "Checks still running after 2 hours")
    expect(("bot", BUILDER) in r.waits(e), f"it waits on {r.waits(e)}, not the Builder")


# ----------------------------------------------------------------------------- GitHub never answers
def _no_expected_time(r: SoftRig, stage: str) -> None:
    """The stage expects no time, so Late never backs up a GitHub wait there: the wait has to end by itself."""
    with r.w.tx() as c:
        c.execute("UPDATE stages SET expected_minutes=NULL WHERE pipeline_id=? AND name=?", (ENGINEERING, stage))


@case(
    "checks-never-report", "A check starts and its result never comes, by webhook or read: the owner's turn at 2 hours"
)
def checks_never_report(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    _no_expected_time(r, "Build")
    e = r.task(ANA, "Flaky runner", ENGINEERING)
    r.check()
    r.pr(e, 1)
    r.gh.ci(S.REPO, r.gh.head(1), "success", after=30 * 86400)  # the runner died: the check stays pending
    r.settle()
    r.says(e, "Checks running")
    r.gh.lose_all()
    r.gh.down_until = S._h().shift(r.w.now(), seconds=30 * 86400)  # and GitHub can't be read either
    r.settle(3600)
    r.says(e, "Checks running")
    r.settle(3600 + 10)
    r.says(e, "Checks still running after 2 hours")
    expect(("bot", BUILDER) in r.waits(e), f"it waits on {r.waits(e)}, not the Builder")


@case(
    "queue-entry-vanishes", "The PR leaves the merge queue with no webhook and no reason: a drop, the owner's at once"
)
def queue_entry_vanishes(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    _no_expected_time(r, "Merge")
    e = r.task(ANA, "Cache headers", ENGINEERING)
    r.gh.land_after_s = 30 * 86400  # the queue holds it
    r.to_merge(e)
    r.says(e, "In the merge queue")
    r.gh.vanish(1)
    r.settle(310)  # the next 5-minute read
    r.at(e, "Merge")
    r.says(e, "Dropped from the merge queue: GitHub gave no reason")
    expect(r.waits(e) == [("bot", RELEASE)], f"the vanished entry waits on {r.waits(e)}")
    expect(len([w for w in r.wakes(e, RELEASE) if w[0].startswith("queue_removed")]) == 1, "the owner was not told")
    expect(r.gh.state(1)["queue_state"] == "none", "Tico queued it again by itself")
    expect(not isinstance(r.queue_again(e), Refusal), "queue again after the vanished entry was refused")
    r.effects()
    r.settle()
    r.says(e, "In the merge queue")


@case(
    "late-webhook-after-merge",
    "A webhook sent before the merge arrives after it, its PR still open: merged stays merged",
)
def late_webhook_after_merge(r: SoftRig) -> None:
    from backend.item_sources import github as TF

    r.preset(ENGINEERING)
    e = r.task(ANA, "Late hook", ENGINEERING)
    r.gh.land_after_s = 30 * 86400
    r.to_merge(e)
    r.says(e, "In the merge queue")
    r.gh._pr_hook(S.REPO, 1)  # a `dequeued` whose snapshot was taken while the PR was still open: 90 s late
    late = r.gh.outbox[-1]
    late.due = S._h().shift(r.w.now(), seconds=90)
    r.gh.merge(1)
    late.version = r.gh._version + 1  # stamped after the merge's own webhook, as GitHub's may be
    r.settle(30)
    r.at(e, "Release")
    r.settle(90)  # the late one arrives, newer by its arrival than the merge
    with r.w.tx() as c:
        fact = TF.pr(c, S.REPO, 1)
    expect(fact is not None and fact["state"] == "merged", f"a late webhook unmerged the PR: {fact and fact['state']}")
    r.at(e, "Release")


@case(
    "branch-rules-refuse", "The base branch's rules refuse the queue (a review is required): told once, never retried"
)
def branch_rules_refuse(r: SoftRig) -> None:
    from backend import task_engine as E

    r.preset(ENGINEERING)
    e = r.task(ANA, "Needs approval", ENGINEERING)
    r.pr(e, 1)
    r.gh.gh.rule(S.REPO, 1)
    r.settle(r.gh.ci_s)
    if r.stage_of(e) != "Merge":
        r.ok(ANA, lambda c: E.move(c, e, "Merge", ANA, "Reviewed in the call"), "Ana drags it to Merge")
    r.effects()
    r.settle()
    reason = "At least 1 approving review is required by reviewers with write access."
    r.says(e, f"GitHub refused the merge: {reason}")
    expect(r.waits(e) == [("bot", RELEASE)], f"the refusal waits on {r.waits(e)}")
    for _ in range(3):
        r.effects()
        r.settle(900)
    expect(r.gh.stats["call enqueue"] == 1, f"a permanent refusal was tried {r.gh.stats['call enqueue']} times")
    expect(len([w for w in r.wakes(e, RELEASE) if "queue_refused" in w[0]]) == 1, "not told exactly once")
    r.gh.gh.rule(S.REPO, 1, None)  # a person approves it on GitHub
    expect(not isinstance(r.queue_again(e), Refusal), "queue again after the approval was refused")
    r.effects()
    r.settle()
    r.says(e, "In the merge queue")


@case(
    "github-5xx-for-hours", "GitHub answers 5xx for hours while a PR is in the merge queue: the owner's turn at 2 hours"
)
def github_5xx_for_hours(r: SoftRig) -> None:
    r.preset(ENGINEERING)
    _no_expected_time(r, "Merge")
    e = r.task(ANA, "Retry budget", ENGINEERING)
    r.gh.land_after_s = 30 * 86400
    r.to_merge(e)
    r.says(e, "In the merge queue")
    r.gh.down_until = S._h().shift(r.w.now(), seconds=6 * 3600)
    r.settle(3600)
    r.says(e, "In the merge queue")
    expect(("source", "github") in r.waits(e), f"an hour of 5xx waits on {r.waits(e)}, not GitHub")
    r.settle(3600 + 10)
    r.says(e, f"GitHub hasn't answered about {REF_1} for 2 hours")
    expect(r.waits(e) == [("bot", RELEASE)], f"two hours of 5xx wait on {r.waits(e)}, not the Release Owner")
    r.settle(4 * 3600)  # GitHub is back: the read gets through and the wait is GitHub's again
    r.says(e, "In the merge queue")
    r.gh.land_after_s = 0
    for _ in range(3):
        r.settle(300)
    r.at(e, "Release")


# ----------------------------------------------------------------------------- Jev
@case("jev-fails-three-times", "Jev's call fails 3 times: a person decides, and their OK moves it")
def jev_fails_three_times(r: SoftRig) -> None:
    p = r.idea("Webhooks")
    r.by_hand(p)
    r.attach(p, r.page(PM, "Spec: webhooks", verdict=FAIL), PM)
    for gap in (0, 60, 300, 900):
        r.w.shift(gap + 1)
        r.jev()
        r.check()
    r.says(p, "Jev couldn't check this")
    expect(r.fake_jev.counts[FAIL] == 3, f"Jev was called {r.fake_jev.counts[FAIL]} times, not 3")
    person = r.person_on(p)
    r.ok_page(p, person)
    r.at(p, "In development")


@case("jev-unsure", "Jev is unsure: a person's turn with Jev's reasons; their OK moves it")
def jev_unsure(r: SoftRig) -> None:
    p = r.idea("Audit trail")
    r.by_hand(p)
    r.attach(p, r.page(PM, "Spec: audit trail", verdict=UNSURE), PM)
    r.jev()
    r.check()
    r.says(p, "Jev is unsure")
    r.ok_page(p)
    r.at(p, "In development")


@case(
    "jev-unsure-bot-first",
    "Jev unsure of a Review page: back to the reviewer once, then the pipeline's manager decides with why; no person",
)
def jev_unsure_bot_first(r: SoftRig) -> None:
    from backend import answers as A
    from backend import task_requirements as R

    _, e = r.to_development("Tags")
    r.pr(e, 1)
    r.settle(r.gh.ci_s)
    r.at(e, "Review 1")
    review = r.page(REVIEWER_1, "Review 1: tags", verdict=UNSURE)
    r.attach(e, review, REVIEWER_1)
    r.jev()
    r.check()
    r.says(e, R.FIX_OR_EXPLAIN)
    expect(r.waits(e) == [("bot", REVIEWER_1)], f"Jev's first unsure left it waiting on {r.waits(e)}")
    r.attach(e, r.page(REVIEWER_1, "Review 1: tags, read and run", verdict=UNSURE, page=review), REVIEWER_1)
    r.jev()
    r.check()
    r.says(e, R.DECIDE)
    expect(r.waits(e) == [("bot", EM)], f"Jev's second unsure left it waiting on {r.waits(e)}, not the manager")
    rid = r.requirement(e, "page")
    version = r.version(e)
    why = "It names the head, says what was run, and nothing blocks"
    r.ok(EM, lambda c: A.answer(c, e, rid, EM, "ok", why, seen_version=version), "the manager OKs the review")
    r.settle()
    r.at(e, "Fixes 1")


def _github_says(r: SoftRig, number: int, conflicts: bool | None) -> None:
    """GitHub's pull_request webhook for the PR at its head through backend/github.py, which keeps whether it conflicts
    with its base branch (None: GitHub has not worked it out yet, as after a push). The rig's GitHub sends only what
    item_metadata reads."""
    from backend import github as G

    pr: dict[str, Any] = {
        "html_url": f"https://github.com/{S.REPO}/pull/{number}",
        "number": number,
        "state": "open",
        "head": {"sha": r.gh.head(number), "ref": f"pr-{number}"},
    }
    if conflicts is not None:
        pr["mergeable"] = not conflicts
    with r.w.tx() as c:
        G.pull_request(c, {"action": "synchronize", "pull_request": pr}, read_refresh=True)


@case("conflict-in-review", "A PR conflicts while a reviewer holds Review 2: the Builder's turn, then the manager's")
def conflict_in_review(r: SoftRig) -> None:
    from backend import task_engine as E

    _p, e = r.to_development("Bulk export")
    r.pr(e, 1)
    r.settle(r.gh.ci_s)
    r.at(e, "Review 1")
    r.ok(ANA, lambda c: E.move(c, e, "Review 2", ANA, "Reviewed in the call"), "Ana drags it to Review 2")
    r.attach(e, r.page(REVIEWER_2, "Review 2: bulk export", verdict=UNSURE), REVIEWER_2)
    r.jev()
    r.check()
    r.says(e, "Jev is unsure")

    # The PR conflicts (Acme, Nov 11): the Builder is woken at once; the reviewer, who cannot push, gets a quiet line.
    before = len(r.wakes(e, REVIEWER_2))
    _github_says(r, 1, conflicts=True)
    r.check()
    r.at(e, "Review 2")
    r.says(e, f"{REF_1} conflicts with its base branch")
    expect(("bot", BUILDER) in r.waits(e), f"the conflict waits on {r.waits(e)}")
    told = [w for w in r.wakes(e, BUILDER) if w[0].startswith("conflict:")]
    expect(len(told) == 1 and "rebase its branch" in told[0][2], f"the Builder was told {told}")
    noted = [w for w in r.wakes(e, REVIEWER_2) if w[0].startswith("conflict:")]
    expect(len(noted) == 1 and "not yours to fix" in noted[0][2], f"the reviewer was told {noted}")

    # The Builder's runs end with nothing pushed: the ladder tries it again, then hands it to the manager bot.
    for _ in range(2000):
        if (r.visit(e).get("level") or "owner") != "owner":
            break
        if r.queued(e, BUILDER):
            r.ran(e, BUILDER)
            r.check()
            continue
        r.later(10)
    expect(r.visit(e).get("level") == "manager", f"the ladder left it at {r.visit(e).get('level')}")
    expect(r.wakes(e, EM), "the manager bot was not told")
    chased = [w for w in r.wakes(e, REVIEWER_2)[before:] if not w[0].startswith("conflict:")]
    expect(not chased, f"the reviewer was chased: {chased}")
    r.at(e, "Review 2")

    # The rebase is pushed: the conflict is gone, and the review is of the old commit, so it is the reviewer's again.
    r.gh.push(1)
    r.settle()
    _github_says(r, 1, conflicts=None)
    r.check()
    r.at(e, "Review 2")
    r.says(e, "New commits since the review")
    expect(r.waits(e) == [("bot", REVIEWER_2)], f"after the rebase it waits on {r.waits(e)}")


# ----------------------------------------------------------------------------- built on the base
@case(
    "webhooks-out-of-order", "Webhooks repeated and out of order: the newest fact wins, each taken once", covers=(32,)
)
def webhooks_out_of_order(r: SoftRig) -> None:
    from backend.item_sources import github as TF

    task = r.task(ANA, "Wire up the API", assignee=BUILDER)
    r.pr(task, 7)
    first = r.gh.head(7)
    newest = r.gh.push(7)
    expect(first != newest, "the push made no new commit")
    hooks = list(r.gh.outbox)
    expect(len(hooks) == 2, f"{len(hooks)} webhooks sent, not 2")
    # The newer one arrives first, then the older one, then the newer one again.
    for d, seq in zip(hooks, (2, 1), strict=True):
        d.seq = seq
    r.gh.outbox.append(Delivery(**{**hooks[1].__dict__, "seq": 3}))
    r.w.shift(1)
    r.gh.deliver(r.w)
    fact = r.w.read(lambda c: TF.pr(c, S.REPO, 7))
    expect(fact and fact["head_sha"] == newest, f"the head is {fact and fact['head_sha']}, not the newest")
    seen = r.w.read(lambda c: c.execute("SELECT COUNT(*) FROM github_deliveries").fetchone()[0])
    expect(seen == len(hooks), f"{seen} deliveries taken of {len(hooks)} sent")
    r.check()
