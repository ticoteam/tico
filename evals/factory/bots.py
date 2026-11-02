"""Simulated bots: each turn reads what woke it and acts through the API the way a real bot of its role does.

A bot's `style` is how its model behaves, as probabilities per turn that does real work:
- `changes`: a reviewer asks for changes; `fail`: QA fails the change
- `garbled`: the reply is cut off or garbled; a reviewer's verdict line is missing, so finishing its subtask is refused
- `silent`: the run never reports back (its lease lapses)
- `crash`: the run fails partway
- `cant_test`: QA says it can't test the change
- `off_script`: the bot moves its ticket somewhere the process doesn't allow (to Blocked, or back a step)
- A builder's `hold_approved`: it pushes nothing after an approval at the head; `dispute_fail`: once per ticket it
  sends a failed head back to the check unchanged; `try_waive`: it asks Tico to waive the last check before the merge
  (a person's call: refused); `push_after_waiver`: on its first n waived tickets it pushes once more instead of merging
- `receipt_wording`: a builder's test receipt is worded the messy ways real ones are (RECEIPTS), and on a PR whose CI
  only skips it posts none; a receipt the gate refused it puts right on its next turn
Every number is drawn from the scenario's seeded random generator, so a run repeats exactly."""
import os
from collections import Counter
from .board import MERGE
from .world import PRIMARY, Refusal

TOKENS = {  # (cached input, uncached input, output) per kind of turn: a real team's averages over a week
    "work": (3_700_000, 90_000, 17_000),     # a Builder's run
    "review": (2_100_000, 93_000, 29_000),   # a reviewer's or QA's run
    "noise": (760_000, 40_000, 5_300),       # woken, read the ticket, nothing to do (a lead's average run)
    "plan": (2_500_000, 150_000, 40_000),    # an architect's plan for a major feature, on a bigger model
}
FINISHED = ("done", "closed", "declined")
# Test receipts as builders really word them, by what they report at the head they name. `clean` is the default.
RECEIPTS = {
    "clean": "Tests: base {base} head {head}: 9 passed, 0 failed",
    # Passing at the head, with a failure on an earlier head since repaired: none of these blocks.
    "repaired_seen": "Tests: head {head}: 12 passed. 2 failures seen at the prior head have been fixed.",
    "repaired_prior": "Head {head}: 9 passed, 0 failed (pytest). 1 failure at prior head, now fixed.",
    "repaired_old": "Tests: head {head}: 9 passed, 0 failed; earlier failure on {old} repaired",
    # Failing at the head, however it is put: each blocks.
    "failing": "Tests: head {head}: 40 passed, 2 failing",
    "failing_words": "Tests at head {head}: 38 passed. Two failures remain in test_sync.py.",
    "failing_line": "Tests: head {head}: 40 passed\nfailing tests: 2",
    "rerun_failed": "Tests: head {head}: 9 passed, 0 failed\nRe-run at head {head}: 2 failed",
    # No test run at all: a lint pass whose repaired errors name no test.
    "lint_only": "Lint at head {head}: passed after 2 errors fixed",
}
PASSING_RECEIPTS = ("clean", "repaired_seen", "repaired_prior", "repaired_old")
FAILING_RECEIPTS = ("failing", "failing_words", "failing_line", "rerun_failed")
TRACE = os.environ.get("SIM_TRACE") == "1"
FAMILY_MODEL = {"claude": "claude-opus-5-5", "gpt": "gpt-6.1-sol", "grok": "grok-4.6"}   # a model Tico's catalog lists


class Bot:
    def __init__(self, world, slug, role, rng, style=None, family="claude", model=None, slots=4):
        self.world, self.slug, self.role, self.rng = world, slug, role, rng
        self.architects = []
        self.style = style or {}
        # Its runs report a model of its own family, so Tico's QA pool sees the companies a real team has (Dev 646).
        self.family, self.model, self.slots = family, model or FAMILY_MODEL.get(family, "claude-opus-5-5"), slots
        self.available = True       # False: its computer is off (claims nothing)
        self.limited_until = None   # set: every run is refused on a usage limit until then
        self.turns = []             # one dict per finished turn
        self.lost = []              # turns its computer lost going off, filed when it comes back
        self.unfinished = set()     # messages whose turn ended without an answer: running one again is a retry
        self.runtime, self.held = PRIMARY, False   # where its turn runs: held, the turn runs on a fallback

    @property
    def token(self):
        return self._token

    def chance(self, what):
        return self.rng.random() < self.style.get(what, 0.0)

    # ------------------------------------------------------------------ one turn
    # Minutes a turn takes, as (shortest, longest, most likely): a build can take two minutes or two hours.
    MINUTES = {"build": (2, 120, 30), "fix": (2, 60, 12), "review": (2, 30, 8), "qa": (3, 40, 12), "plan": (10, 60, 25),
               "short": (1, 4, 2)}
    # One turn in twenty runs long: a build of three or four hours, an hour-long review.
    TAIL = {"build": (180, 240), "fix": (60, 120), "review": (30, 60), "qa": (40, 60), "plan": (60, 120)}

    def start(self, attempt):
        """Begin a claimed turn: read what woke the bot, and decide how long the turn runs. It acts when it ends."""
        w = self.world
        self._token = attempt["token"]
        self.attempt = attempt
        # Where the turn runs: its own login, or the fallback a hold sends it to (the runner's choice, made on the claim).
        self.runtime, self.held = w.turn_runtime(attempt.get("job_id"))
        message = attempt.get("message") or {}
        refs = message.get("refs") or {}
        task = attempt.get("task")
        self.record = {"at": w.now(), "bot": self.slug, "news": refs.get("handoff") or refs.get("news") or refs.get("rule")
                       or message.get("kind"), "task": task and task["id"], "kind": "noise",
                       "stale": self.stale(task, message), "action": None,
                       "text": str(message.get("body") or "").replace("\n", " "), "runtime": self.runtime,
                       "attempt": attempt["id"],
                       # The claimed message is the turn's prompt, carried in the claim's reply: the inputs poll never
                       # returns it, since no input row is written for it.
                       "prompt": message.get("id")}
        w.post(f"attempts/{attempt['id']}/started", {"thread_id": "sim"}, token=w.runner["token"])
        if self.slug in w.steer_bots:
            self.steer()
        self.ends = w.now() + __import__("datetime").timedelta(minutes=self.minutes(task))

    def minutes(self, task):
        if not task or self.limited_until and self.world.now() < self.limited_until:
            return 0
        kind = self.kind_of_turn(task)
        if kind in self.TAIL and self.rng.random() < 0.05:
            return self.rng.uniform(*self.TAIL[kind])
        return self.rng.triangular(*self.MINUTES[kind])

    def kind_of_turn(self, task):
        w = self.world
        if task["owner"] != "bot:" + self.slug or task["status"] in ("done", "closed", "declined"):
            return "short"
        if self.role == "builder":
            step = w.board.step(w.step_of(task))
            if step and step.work:
                return "fix" if w.board.fixes_for(step.name) else "build"
            return "short"
        if self.role in ("reviewer", "qa", "architect") and w.parent_of(task["id"]):
            return {"reviewer": "review", "qa": "qa", "architect": "plan"}[self.role]
        return "short"

    def end(self):
        """Finish the running turn: act on the task as it is now, and report the run finished, failed or silent."""
        w, attempt, record = self.world, self.attempt, self.record
        self.attempt = self.record = None
        task = attempt.get("task") and w.task(attempt["task"]["id"])
        message = attempt.get("message") or {}
        if self.limited_until and w.now() < self.limited_until:
            self.unfinished.add(message.get("id"))
            record["kind"] = "limited"
            self.finish(attempt, "failed", limited=True, resets=self.limited_until, tokens=None)
            return self.log(record)
        hit = w.limit_next.pop(self.runtime, None)
        if hit:
            # The runtime hit its usage limit: its error line says so, and the login hold reads the reset from that line.
            self.unfinished.add(message.get("id"))
            record["kind"] = "limited"
            w.limit_events(attempt, hit)
            self.finish(attempt, "failed", limited=True, seq=1)
            window = w.limit_window(self.slug)
            if window:
                w.limit_windows.append(window)
            return self.log(record)
        if self.chance("silent"):
            self.unfinished.add(message.get("id"))
            record["kind"] = "silent"
            return self.log(record)
        if self.chance("crash") or task and task["title"] in self.style.get("crash_titles", ()):
            self.unfinished.add(message.get("id"))
            record["kind"] = "crash"
            self.finish(attempt, "failed", tokens=TOKENS["noise"])
            return self.log(record)
        try:
            if record["news"] == "update":
                # Every real bot answers Tico's daily update request by posting one.
                w.post("updates", {"body": "- Worked my tickets."}, token=self.token)
                action = "posted update"
            else:
                settled = self.settle()
                action = self.act(task, message) if task else None
                action = ", ".join(filter(None, (settled, action))) or None
        except Refusal as exc:
            action = "refused: " + exc.body[:160]
        if action:
            record["kind"] = {"reviewer": "review", "qa": "review", "architect": "plan"}.get(self.role, "work")
            record["action"] = action
        self.finish(attempt, "completed", tokens=TOKENS[record["kind"]])
        return self.log(record)

    @property
    def running(self):
        return getattr(self, "attempt", None) is not None

    def lose(self):
        """The computer went off mid-turn: the turn did not finish, and the runner remembers it."""
        self.lost.append(self.attempt)
        self.unfinished.add((self.attempt.get("message") or {}).get("id"))
        self.attempt = self.record = None

    def recover(self):
        """Back on, the runner files each turn it lost as the real runner does on a restart."""
        while self.lost:
            attempt = self.lost.pop()
            self.world.call("post", f"attempts/{attempt['id']}/complete",
                            {"outcome": "interrupted", "text": "Runner restarted during execution",
                             "last_seq": attempt.get("last_seq", 0)},
                            token=self.world.runner["token"], ok=False)

    def steer(self):
        """Read the inputs queued for the turn as it starts (the runner's first poll, which the sim makes once per turn),
        and ack each one as delivered to the runtime. The read does not answer them: a turn that ends on its limit leaves
        its inputs unanswered, and a later run takes them over."""
        w, attempt = self.world, self.attempt
        status, reply = w.request("post", f"attempts/{attempt['id']}/inputs", {}, token=w.runner["token"])
        if status >= 500:
            return      # recorded by the world; the turn runs without them, and the run's checks report the error
        if status >= 400:
            raise Refusal(status, str(reply))
        mids = [message["id"] for message in reply["messages"]]
        w.input_reads.append((w.now(), self.slug, attempt["id"], mids))
        for mid in mids:
            w.call("post", f"attempts/{attempt['id']}/inputs/{mid}/ack", {}, token=w.runner["token"])

    def renew(self):
        # A computer that is off sends nothing: its turn's lease lapses on the server, as a real lost computer's does.
        if self.running and self.available:
            self.world.call("post", f"attempts/{self.attempt['id']}/renew", {}, token=self.world.runner["token"],
                            ok=False)

    def log(self, record):
        self.turns.append(record)
        if TRACE:
            print(f"{record['at']:%H:%M} {self.slug:16} {record['news'] or '-':18} {record['kind']:7}{'*' if record['stale'] else ' '}"
                  f"{(self.world.title(record['task']) if record['task'] else '')[:28]:28} {record['action'] or ''}"
                  + ("" if record["task"] and not record["stale"] else f" [{record['text'][:90]}]"))
        return record

    def finish(self, attempt, outcome, limited=False, resets=None, tokens=None, seq=0):
        w = self.world
        body = {"outcome": outcome, "text": "done", "last_seq": seq, "limited": limited}
        if resets:
            body["limit_resets_at"] = resets.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.held:
            # A turn run on a fallback names it: without that the server reads the turn as one on the held login and
            # clears the hold.
            body["primary_held"], body["fallback"] = True, self.runtime
        if tokens:
            cached, fresh, out = tokens
            body["usage"] = {"input_tokens": fresh, "cached_tokens": cached, "output_tokens": out,
                             "model": self.model, "runtime": self.runtime,
                             "billing": "api"}
        w.post(f"attempts/{attempt['id']}/complete", body, token=w.runner["token"])

    def stale(self, task, message):
        """Whether Tico's notice was out of date when the bot read it: its task had already finished, its CI news was
        for a head the PR has moved past, or it had moved to another step after the notice was written."""
        if not task or message.get("from_actor") not in (None, "keeper"):
            return False   # a colleague's comment is theirs to judge; the claim's cleanup is for Tico's own notices
        if message.get("id") in self.unfinished:
            return False   # its first turn ended unanswered (a limit, a crash, a lost computer): this one resumes it
        if task["status"] in ("done", "closed", "declined"):
            return True
        head = (message.get("refs") or {}).get("pr_head")
        if head:   # CI news is about a head, not a step: it is out of date once the PR moved on
            pr = self.world.pr_of(task["id"])
            return bool(pr) and self.world.prs[pr]["head"] != head
        moved = self.world.step_entered_at(task["id"], claimed_by="bot:" + self.slug)
        return bool(moved and message.get("created_at") and message["created_at"] < moved)

    # ------------------------------------------------------------------ role behaviour
    def act(self, task, message):
        raise NotImplementedError

    def settle(self):
        """What the bot owes from an earlier turn, done first on its next one, whatever woke it."""
        return None


class Builder(Bot):
    """Builds its tickets and takes each through the board. It acts on what the ticket shows now, whatever woke it,
    as a real builder reads its ticket, and on the step's settings, never its name. The board is to move the ticket
    on a verdict, on green CI after fixes and on the merge; when it has not, the builder makes the move itself and the
    run records it as a fallback (a task feature missing or not configured)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.disputed = set()           # tickets whose failed head it sent back to the check unchanged
        self.pushed_after_waiver = set()
        self.waiver_tries = []          # (when, ticket id, head, outcome) of its own waiver requests
        self.owed = {}     # ticket id -> what its refused move needs: "receipt", or "push" for a failure at the head
        self.posted = []   # (when, ticket id, head, wording) per receipt, "none" for one left out
        self.moves = []    # (when, ticket id, step, refusal text or None) per move this builder made

    def act(self, task, message):
        w, board = self.world, self.world.board
        if task["owner"] != "bot:" + self.slug or task["status"] in FINISHED:
            return None
        step = board.step(w.step_of(task))
        if not step or step.merged:
            return None
        pr = w.pr_of(task["id"])
        if pr and w.prs[pr]["merged"]:
            return self.fallback(task, board.on_merge, "merged, but still at " + step.name)
        if step.assign:
            if w.slots(self.slug) >= self.slots:
                return None
            nxt = board.steps[board.steps.index(step) + 1]
            w.update(task["id"], token=self.token, step=nxt.name)
            step = nxt
        if step.work:
            fixes = board.fixes_for(step.name)
            return self.fix(task, step, fixes, pr) if fixes else self.build(task, step, pr)
        if step.check:
            return self.at_check(task, step, pr, message)
        return None

    def build(self, task, step, pr):
        """A building step: plan first when the ticket is major and the board has an architect, then build, open the
        PR when the next step is a check, and hand the ticket on in the same turn."""
        w, board = self.world, self.world.board
        if "major" in labels(task) and self.architects:
            plan = next((c for c in w.checks(task["id"]) if c["title"].startswith("Plan:")), None)
            if not plan:
                w.post("tasks", {"owner": self.pick(self.architects, set()), "title": f"Plan: {task['title']}",
                                 "body": "Plan this feature: the approach, the risks, the order of work.",
                                 "relations": [{"task": task["id"], "kind": "parent"}]}, token=self.token)
                return "asked the architect for a plan"
            if plan["status"] not in FINISHED:
                return None
        nxt = board.step(step.next)
        if nxt and nxt.check:
            if not pr:
                pr = w.open_pr(task["id"], token=self.token)
                self.receipt(task["id"], pr)
            elif w.ci_state(pr) == "failing":
                self.push(task, pr)
        return self.move(task, step.next)

    def fix(self, task, step, check_step, pr):
        """A fixes step: push fixes for the check's comments, whatever form they came in. On a step whose owner moves
        the ticket on (`next`), the builder hands it on in the same turn, as it does after a build, unless it forgets
        (`forget_move`): then the ticket sits until something wakes the builder again. On a step the board moves on
        green CI (`after_green`), the builder waits for that."""
        w = self.world
        judged = w.judged(task["id"], check_step.name)
        tag = check_step.check.skip_tag
        if not pr:
            return None
        pushed = None
        kept = self.keeps(task, judged, pr)
        if not kept and (judged and str(judged.get("verdict_head") or "-") == w.prs[pr]["head"]
                         or w.ci_state(pr) == "failing" or not judged and self.untouched_since_step(task, pr)):
            if tag and tag not in labels(task) and self.chance("skip_check"):
                w.update(task["id"], token=self.token, labels=sorted(labels(task) | {tag}))
            self.push(task, pr)
            pushed = "pushed fixes"
        if step.next:
            if self.chance("forget_move"):
                return pushed or "forgot to hand it on"
            if step.next == MERGE:
                return self.merge(task, pr) or pushed
            return ((pushed + ", " if pushed else "") + ("disputed the fail, " if kept == "disputed" else "")
                    + self.move(task, step.next) + self.try_waive(task, pr))
        if pushed or w.ci_state(pr) != "passing":
            return pushed
        target = step.after_green
        if tag and tag in labels(task) and target == check_step.name:
            target = check_step.check.passed
        if target == MERGE:
            return self.merge(task, pr)
        return self.fallback(task, target, "green CI at the new head, still at " + step.name)

    def keeps(self, task, judged, pr):
        """Whether the builder leaves the judged head as it is: an approval with nothing to fix (`hold_approved`),
        or a fail it disputes, once per ticket, by sending the same head back to the check (`dispute_fail`). A pass
        at a head a fail still stands on is no approval: the builder pushes a fix. Each style draws from the generator
        only when the scenario sets it."""
        w = self.world
        if not judged or str(judged.get("verdict_head") or "") != w.prs[pr]["head"] or w.ci_state(pr) == "failing":
            return False
        if judged["verdict"] in ("approve", "pass"):
            standing = w.failed_at(task["id"], w.check_step(judged), w.prs[pr]["head"])
            return bool(self.style.get("hold_approved")) and not standing and self.chance("hold_approved") and "held"
        if self.style.get("dispute_fail") and task["id"] not in self.disputed and self.chance("dispute_fail"):
            self.disputed.add(task["id"])
            return "disputed"
        return False

    def try_waive(self, task, pr):
        """Having just sent the ticket to the last check before the merge, the builder asks Tico to waive it with its
        own token (`try_waive`). A waiver is a person's decision, so Tico refuses it."""
        w = self.world
        if not self.style.get("try_waive"):
            return ""
        step = w.board.step(w.step_of(w.task(task["id"])))
        if not step or not step.check or step.check.passed != MERGE or not self.chance("try_waive"):
            return ""
        head = w.prs[pr]["head"]
        try:
            w.post(f"tasks/{task['id']}/qa-waiver", {"head": head, "reason": "QA is slow; merging without it."},
                   token=self.token)
            outcome = "accepted"
        except Refusal as exc:
            outcome = f"refused ({exc.status})"
        self.waiver_tries.append((w.now(), task["id"], head, outcome))
        return f", asked to waive {step.name}: {outcome}"

    def waived(self, task):
        """Whether a person's waiver covers the PR's current head, as the task view says (`current`)."""
        w = self.world
        if not w.sql("SELECT 1 FROM task_events WHERE task_id=? AND field='qa_waiver' LIMIT 1", (task["id"],)):
            return False
        view = w.get("tasks/" + task["id"], token=self.token)["task"]
        return any(x.get("current") for x in view.get("qa_waivers") or [])

    def untouched_since_step(self, task, pr):
        """No push since the ticket came to this step (a check finished with no verdict sends it here too)."""
        entered = self.world.step_entered_at(task["id"])
        pushed = self.world.prs[pr].get("pushed")
        return not pushed or (entered and pushed < entered)

    def at_check(self, task, step, pr, message=None):
        """Waiting on a check. The builder merges when Tico says the merge is next (a pass, a skip or a skipped
        round), or when the check passed at the head and CI is green; a pass the board did not act on, or a skip tag
        the board ignored, the builder moves on by hand."""
        w = self.world
        check = step.check
        if check.passed == MERGE and pr and ((message or {}).get("refs") or {}).get("handoff") == "merge":
            return self.merge(task, pr)
        if check.passed == MERGE and pr and w.cleared(task["id"]):
            return self.merge(task, pr)
        if check.passed == MERGE and pr and self.waived(task):
            # A person waived the check for this exact head: the owner merges at it, unless it has one more fix.
            if task["id"] not in self.pushed_after_waiver \
                    and len(self.pushed_after_waiver) < self.style.get("push_after_waiver", 0):
                self.pushed_after_waiver.add(task["id"])
                self.push(task, pr)
                return "pushed after the waiver"
            return self.merge(task, pr)
        if check.skip_tag and check.skip_tag in labels(task):
            return self.merge(task, pr, fallback="skip tag ignored at " + step.name) if check.passed == MERGE else \
                self.fallback(task, check.passed, "skip tag ignored at " + step.name)
        if pr and w.ci_state(pr) == "failing":
            self.push(task, pr)
            return "pushed a fix for failing CI"
        # Only the round the ticket waits on counts: an earlier round's verdict, from before the ticket came back to
        # this step, is no reason to move it by hand while the new round is still open.
        current = w.current_check(task["id"], step.name)
        if not current or not current.get("verdict") or not pr or current.get("verdict_head") != w.prs[pr]["head"]:
            return None
        # A pass at a head a fail still stands on is no approval: only a new head clears it (World.failed_at).
        if current["verdict"] in ("approve", "pass") and w.failed_at(task["id"], step.name, w.prs[pr]["head"]):
            self.push(task, pr)
            return "pushed a fix for the head QA failed"
        if current["verdict"] in ("approve", "pass"):
            if check.passed == MERGE:
                return self.merge(task, pr)
            return self.fallback(task, check.passed, "passed, still at " + step.name)
        return self.fallback(task, check.other, current["verdict"] + ", still at " + step.name)

    def push(self, task, pr, clean=False):
        self.world.push(pr)
        self.world.prs[pr]["pushed"] = self.world.H.now()
        self.receipt(task["id"], pr, clean=clean)

    def move(self, task, step):
        try:
            self.world.update(task["id"], token=self.token, step=step)
        except Refusal as exc:
            self.moves.append((self.world.now(), task["id"], step, exc.body))
            if "test receipt" in exc.body and "receipt_wording" in self.style:
                last = next((p for p in reversed(self.posted) if p[1] == task["id"]), None)
                self.owed[task["id"]] = "push" if last and last[3] in FAILING_RECEIPTS else "receipt"
            raise
        self.moves.append((self.world.now(), task["id"], step, None))
        return "moved to " + step

    def settle(self):
        """A move the gate refused for its receipt, put right as a real builder does on reading the refusal: a clean
        receipt at the head, or, when its tests failed there, a fix pushed with one. Tico makes the move itself."""
        w, done = self.world, []
        for task_id, what in list(self.owed.items()):
            del self.owed[task_id]
            task, pr = w.task(task_id), w.pr_of(task_id)
            if not task.get("blocked_since") or not pr or w.prs[pr]["merged"]:
                continue   # moved on meanwhile: nothing left to put right
            if what == "push":
                self.push(task, pr, clean=True)
            else:
                self.receipt(task_id, pr, clean=True)
            done.append(("pushed a fix with a clean receipt" if what == "push" else "posted a clean receipt")
                        + " on " + task["title"])
        return "; ".join(done) or None

    def fallback(self, task, step, why):
        self.world.fallback(self.slug, task["id"], f"moved to {step} by hand: {why}")
        return self.move(task, step)

    def merge(self, task, pr, fallback=None):
        w = self.world
        if w.ci_state(pr) != "passing":
            return None
        w.merge(pr)
        if fallback:
            w.fallback(self.slug, task["id"], "merged by hand: " + fallback)
        after = w.board.step(w.step_of(w.task(task["id"])))
        if after and not after.merged:
            return self.fallback(task, w.board.on_merge, "merged, the board did not move it")
        return "merged #%d" % pr

    def receipt(self, task_id, pr, clean=False):
        w = self.world
        head, old = w.prs[pr]["head"], (w.prs[pr].get("old_heads") or [None])[-1]
        wording = "clean" if clean or "receipt_wording" not in self.style else self.wording(pr, old)
        self.posted.append((w.now(), task_id, head, wording))
        if wording != "none":
            w.update(task_id, token=self.token, note=RECEIPTS[wording].format(base="c" * 40, head=head, old=old))

    def wording(self, pr, old):
        """How this receipt is worded (`receipt_wording`). A PR whose CI only skips gets none: the builder takes the
        green-looking checks for a test run."""
        if self.world.prs[pr].get("skip_ci"):
            return "none"
        if not self.chance("receipt_wording"):
            return "clean"
        return self.rng.choice([k for k in RECEIPTS if k != "clean" and (old or k != "repaired_old")])

    def pick(self, bots, avoid):
        """The least busy bot, preferring another model family and one not used on this ticket."""
        ranked = sorted(bots, key=lambda b: (b.slug in avoid, b.family == self.family, self.world.open_checks(b.slug),
                                             b.slug))
        return ranked[0].slug


def labels(task):
    return {str(t).lower() for t in (task.get("labels") or [])}


class Checker(Bot):
    """Owns check subtasks (reviews, QA): judges the change at its head and finishes the subtask with a verdict. It
    never moves the ticket. Its style is how its model goes wrong."""
    GOOD, BAD = "approve", "changes"

    def act(self, task, message):
        w = self.world
        parent = w.parent_of(task["id"])
        if task["owner"] != "bot:" + self.slug or task["status"] in FINISHED or not parent:
            return None
        head = w.head_for(task)
        if self.chance("off_script"):
            target = next(s.name for s in w.board.steps if s.work)
            w.update(parent, token=self.token, step=target)   # refused: a checker never moves the ticket
            return "moved the ticket"
        if self.chance("fallback_stop"):
            # A run on a fallback model stops itself and leaves the check open, as a real one did.
            w.update(task["id"], token=self.token, note="Runtime blocker: this turn ran on a fallback model after the "
                                                          "usage limit; stopping without a verdict.")
            return "stopped on a fallback model"
        if self.chance("garbled"):
            w.post(f"tasks/{task['id']}/comments", {"text": "Findings: the hand"}, token=self.token)
            w.update(task["id"], token=self.token, status="done")   # no verdict line: today this is refused
            return "garbled"
        if self.chance("stale_head"):
            old = w.prs[w.pr_of(parent)].get("old_heads") or []
            head = old[-1] if old else head
        if self.chance("cant_test"):
            w.post(f"tasks/{task['id']}/comments", {"text": "I can't test this change here."}, token=self.token)
            w.update(task["id"], token=self.token, status="done", verdict=self.GOOD, verdict_head=head,
                     note="Can't test: passed through")
            return "can't test"
        verdict = self.BAD if self.chance(self.BAD_STYLE) else self.GOOD
        w.post(f"tasks/{task['id']}/comments", {"text": f"Findings: {verdict}"}, token=self.token)
        w.update(task["id"], token=self.token, status="done", verdict=verdict, verdict_head=head)
        return verdict


class Reviewer(Checker):
    BAD_STYLE = "changes"


class QA(Checker):
    GOOD, BAD, BAD_STYLE = "pass", "fail", "fail"


class Architect(Bot):
    """A senior architect on a big model: builders ask it to plan major features, nobody else wakes it."""

    def act(self, task, message):
        w = self.world
        if task["owner"] != "bot:" + self.slug or task["status"] in FINISHED or not task["title"].startswith("Plan:"):
            return None
        w.update(task["id"], token=self.token, status="done",
                 note="Plan: 1. data model first; 2. API; 3. UI. Risks: migration order. Build in that order.")
        return "planned"


class Lead(Bot):
    """Head of Engineering: on its hourly sweep (a routine) it looks for stuck tickets and gets each moving the way a
    lead does: it tells the owner, with what it sees, flagged as needing action (a plain comment from one bot to another
    waits for the owner's next run). It never moves a ticket itself. Any other wake is noise."""

    def act(self, task, message):
        refs = message.get("refs") or {}
        if refs.get("step_late") and task["status"] not in FINISHED and str(task["owner"]).startswith("bot:"):
            self.world.post(f"tasks/{task['id']}/comments", {"text": f"@{task['owner'][4:]} this ticket is late at "
                                                                     f"{self.world.step_of(task)}: what does it wait on?",
                                                                     "needs_action": True},
                            token=self.token)
            return "asked the owner of a late ticket"
        if refs.get("step_backlog"):
            return self.spread(task)
        if not task["title"].startswith("Hourly board sweep") or task["owner"] != "bot:" + self.slug \
                or task["status"] in FINISHED:
            return None
        told = self.sweep()
        self.world.update(task["id"], token=self.token, status="done", note=told)
        return told

    def spread(self, task):
        """A backlog at a step: hand the busiest bot owner's surplus there to the least loaded bot of the same kind.
        Unowned tickets wait for the board, so a backlog of those is only acknowledged."""
        w = self.world
        step = w.step_of(task)
        here = [t for t in w.tickets() if t["step"] == step and t["status"] not in FINISHED]
        owned = Counter(t["owner"] for t in here if str(t["owner"]).startswith("bot:"))
        if not owned:
            return "noted a backlog the board is to assign"
        busiest, n = owned.most_common(1)[0]
        role = next((r for r, slugs in w.roles.items() if busiest[4:] in slugs), None)
        peers = ["bot:" + slug for slug in w.roles.get(role, []) if "bot:" + slug != busiest]
        if not peers:
            return f"noted the backlog on {busiest[4:]}"
        target = min(peers, key=lambda o: owned[o])
        moved = 0
        for t in [t for t in here if t["owner"] == busiest and t["status"] == "open"][: (n - owned[target]) // 2]:
            w.update(t["id"], token=self.token, owner=target)
            moved += 1
        return f"moved {moved} tickets from {busiest[4:]} to {target[4:]}" if moved else f"noted the backlog on {busiest[4:]}"

    def sweep(self):
        w, board = self.world, self.world.board
        told = []
        # A snoozed ticket is off the board the lead reads until its date (docs/tasks.md, Snoozing a task).
        snoozed = {r["id"] for r in w.sql("SELECT id FROM tasks WHERE snoozed_until>?", (w.H.now(),))}
        for t in w.tickets():
            step = board.step(t["step"])
            # Only work its owner holds: a ticket queued for a start or out for a check moves without the owner.
            if not step or not step.work or not str(t["owner"]).startswith("bot:") or t["status"] != "doing" \
                    or t["id"] in snoozed:
                continue
            limit = step.wake_after_hours or 2
            if w.idle_hours(t["id"]) < limit:
                continue
            w.post(f"tasks/{t['id']}/comments", {"text": f"@{t['owner'][4:]} this ticket has sat at {step.name} for "
                                                          f"{w.idle_hours(t['id']):.1f} h: what moves it on?",
                                                          "needs_action": True},
                   token=self.token)
            w.stuck_told.append((w.now(), t["id"]))
            told.append(t["title"])
        return ("told owners of " + ", ".join(told)) if told else "swept: nothing stuck"


class Quiet(Bot):
    """A bot whose turns are all noise for this simulation (Release Manager notes, for one)."""

    def act(self, task, message):
        return None
