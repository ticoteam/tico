"""A board as configuration: steps and what each one does, in generic terms a team could set up differently.

Nothing in the simulation names a step outside a Board. The bots read a step's settings (is it work, a check, where a
pass goes) and act on them, and `apply` sets the same settings on the server through its API. A setting the server
can't take yet is recorded as unsupported: the scenario then fails its "board is configuration" check, which names
the task feature to build."""
from dataclasses import dataclass, field


@dataclass
class Check:
    """The step needs a check subtask from a pool before the ticket moves on."""
    pool: str                       # role whose bots take the check ("reviewer", "qa")
    passed: str                     # where a pass goes: a step name, or MERGE (the builder merges next)
    other: str                      # where changes, a fail or a reply with no verdict goes
    different_checker_from: tuple = ()   # steps whose checker may not take this one (a second opinion)
    timeout_hours: float = 1.0      # an unanswered check is skipped after this long
    skip_tag: str | None = None     # a ticket with this tag skips the step (the skip is recorded)
    gate: bool = False              # entering needs green CI and a test receipt at the head
    kind: str | None = None         # what the server's rules know the check as: "review", "qa" or "release_qa"


@dataclass
class Step:
    name: str
    status: str                     # the task status a ticket has at this step
    work: bool = False              # the owner builds or fixes here; it takes one of the owner's slots
    next: str | None = None         # a work step: where the owner sends the ticket when its work is done (by design:
                                    # only the owner knows when it is)
    check: Check | None = None
    after_green: str | None = None  # a work step: green CI on a new head moves the ticket here (MERGE: merge next)
    can_hold: bool = True           # whether a ticket here may be put on hold (Blocked)
    wake_after_hours: float | None = None   # the owner is woken when a ticket sits here this long
    assign: str | None = None       # unowned tickets here go to the least busy bot of this role with a free slot
    backlog: dict | None = None     # more than `over` tickets waiting here (per step or per owner): who hears of it
    merged: bool = False            # a ticket is here only once its change merged
    release: bool = False           # a release moves tickets from here to the next step


MERGE = "<merge>"


@dataclass
class Board:
    name: str
    steps: list
    on_merge: str                   # a merged ticket goes here, from wherever it was
    hold_step: str = "Blocked"
    slots: int = 4
    lead_sweep_hours: float = 1.0   # the lead's scheduled sweep for stuck tickets
    lead_sweep: bool = True         # whether the lead has that routine
    roles: dict = field(default_factory=dict)   # role -> what its bots do on this board, for the record

    def step(self, name):
        return next((s for s in self.steps if s.name == name), None)

    def first(self):
        return self.steps[0]

    def checks(self):
        return [s for s in self.steps if s.check]

    def fixes_for(self, name):
        """The check step a work step takes fixes for: the check whose `other` leads here."""
        return next((s for s in self.steps if s.check and s.check.other == name), None)

    def merged_steps(self):
        return [s.name for s in self.steps if s.merged]

    def finished(self, name):
        s = self.step(name)
        return bool(s) and s.status in ("done", "closed")


def dev_board(qa=True, review_timeout=1.0):
    """The engineering board. A review is feedback, not a gate: whatever it says, the ticket goes back to its builder,
    who moves it on once the feedback is in (only the builder knows when). QA passes or fails; its pass makes the merge
    next. Then the release."""
    steps = [
        Step("On Deck", "open", assign="builder", backlog={"over": 10, "per": "step", "notify": ["manager"]}),
        Step("In Progress", "doing", work=True, next="Review 1"),
        Step("Review 1", "review", check=Check("reviewer", "Review 1 Fixes", "Review 1 Fixes",
                                               timeout_hours=review_timeout, gate=True, kind="review")),
        Step("Review 1 Fixes", "doing", work=True, next="Review 2", wake_after_hours=2),
        Step("Review 2", "review", check=Check("reviewer", "Review 2 Fixes", "Review 2 Fixes",
                                               different_checker_from=("Review 1",), timeout_hours=review_timeout,
                                               gate=True, kind="review")),
        Step("Review 2 Fixes", "doing", work=True, next="In QA" if qa else MERGE, wake_after_hours=2),
    ]
    if qa:
        steps += [Step("In QA", "review", check=Check("qa", MERGE, "QA Fixes", timeout_hours=2, skip_tag="skip-qa",
                                                            kind="qa")),
                  Step("QA Fixes", "doing", work=True, next="In QA", wake_after_hours=2)]
    steps += [Step("Pending Release", "review", can_hold=False, merged=True, release=True),
              Step("Post Release QA", "review", can_hold=False, merged=True, release=True),
              Step("Done", "done", can_hold=False, merged=True),
              Step("Closed", "closed", can_hold=False)]
    return Board("Dev tickets", steps, on_merge="Pending Release")


def design_board():
    """Another team's board, to prove nothing is hard-coded: a design step, one review, no QA, other names."""
    steps = [
        Step("Inbox", "open", assign="builder"),
        Step("Design", "doing", work=True, next="Build"),
        Step("Build", "doing", work=True, next="Peer Check"),
        Step("Peer Check", "review", check=Check("reviewer", MERGE, "Rework", timeout_hours=1.5,
                                                   kind="review")),
        Step("Rework", "doing", work=True, after_green="Peer Check", wake_after_hours=3),
        Step("Shipping", "review", can_hold=False, merged=True, release=True),
        Step("Live", "done", can_hold=False, merged=True),
        Step("Dropped", "closed", can_hold=False),
    ]
    return Board("Studio work", steps, on_merge="Shipping")


def flows(board, bots):
    """The board as the server's step settings: [{name, status, flow, check_pool}] for `POST task-types/{id}`.
    `bots` maps a role to its bot slugs."""
    out = []
    for s in board.steps:
        flow = {}
        if s.check:
            flow.update(on_pass="merge" if s.check.passed == MERGE else s.check.passed,
                        on_fail=s.check.other, timeout_hours=s.check.timeout_hours, gate=s.check.gate)
            if s.check.skip_tag:
                flow["skip_tag"] = s.check.skip_tag
            if s.check.kind:
                flow["check"] = s.check.kind     # a step's flow is replaced when saved: without it the step is no check
        if s.after_green:
            flow["after_green"] = "merge" if s.after_green == MERGE else s.after_green
        if s.name == board.on_merge:
            flow["on_merge"] = True
        if not s.can_hold:
            flow["can_hold"] = False
        if s.wake_after_hours:
            flow["status_hours"] = s.wake_after_hours
        if s.backlog:
            flow["backlog"] = s.backlog
        if s.assign:
            flow["assign"] = {"pool": bots.get(s.assign, []), "slots": board.slots}
        row = {"name": s.name, "status": s.status, "flow": flow}
        if s.check:
            row["check_pool"] = bots.get(s.check.pool, [])
        out.append(row)
    return out
