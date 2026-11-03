"""Builders' test receipts worded the messy ways real ones are, and a PR whose only CI check is skipped.

A receipt quotes a failure on an earlier head since repaired, reports a failing rerun at the current head (in digits,
in words, on its own line), or is a lint pass that ran no test; on a PR whose CI only skips, the builder posts none.
The review gate opens review only on a passing receipt at the current head, a repaired earlier failure does not block,
and a refused move is made by Tico once the builder's clean receipt is posted. What each receipt said is the bots'
own record (bots.Builder.posted), never the server's reading of it."""
from ..bots import FAILING_RECEIPTS, PASSING_RECEIPTS
from ..scenarios import Scenario, standard, tickets
from ..world import START

GATE = "review gate"
REPAIRED = tuple(k for k in PASSING_RECEIPTS if k != "clean")
NO_TEST = (*FAILING_RECEIPTS, "lint_only")


def setup(run, w, team):
    w.ci_skip = 0.2
    tickets(run, w, 10)


def during(run, w, team, i):
    w.retry_moves()


def posted(run):
    return sorted((p for b in run.t.builders for p in b.posted), key=lambda p: p[0])


def in_effect(receipts, ticket, when):
    """The ticket's receipt the gate reads at `when`: the newest one posted by then (a note replaces the last)."""
    found = [p for p in receipts if p[1] == ticket and p[0] <= when]
    return found[-1] if found else None


def gated(run):
    return {s.name for s in run.w.board.checks() if s.check.gate}


def entries(run):
    """[(when, ticket id, step, head, receipt in effect)] for every time a ticket entered a gated step."""
    w, receipts, out = run.w, posted(run), []
    names = gated(run)
    for t in run.final:
        pr = w.pr_of(t["id"])
        rows = w.sql("SELECT e.created_at, s.name FROM task_events e JOIN task_steps s ON s.id=e.new WHERE e.task_id=? "
                     "AND e.field='step' ORDER BY e.created_at, e.rowid", (t["id"],))
        for row in rows:
            if row["name"] in names and pr:
                when = w.H.parse_ts(row["created_at"])
                out.append((when, t["id"], row["name"], w.head_at(pr, when), in_effect(receipts, t["id"], when)))
    return out


def no_review_on_failure(run):
    """No ticket enters a gated step while its receipt reports a failure at that head, or no test run at all."""
    bad = [f"{run.w.title(tid)} {step} ({r[3]})" for _, tid, step, head, r in entries(run)
           if r and r[2] == head and r[3] in NO_TEST]
    seen = sum(1 for p in posted(run) if p[3] in NO_TEST)
    if not seen:
        return False, "no receipt reported a failure: the situation did not happen"
    return not bad, f"{seen} failing or no-test receipts; entered review on one: " + ", ".join(bad[:6])


def no_review_on_skipped(run):
    """No ticket whose PR's only check is skipped enters a gated step without a passing receipt at the head."""
    skipped = {n for n, pr in run.w.prs.items() if pr.get("skip_ci")}
    if not skipped:
        return False, "no PR had only skipped checks: the situation did not happen"
    bad = [f"{run.w.title(tid)} {step}" for _, tid, step, head, r in entries(run)
           if run.w.pr_of(tid) in skipped and not (r and r[2] == head and r[3] in PASSING_RECEIPTS)]
    return not bad, f"{len(skipped)} skipped-only PRs; entered review with no receipt: " + ", ".join(bad[:6])


def repaired_passes(run):
    """A move with a receipt that passes at the head and quotes a repaired earlier failure is not refused."""
    receipts, names = posted(run), gated(run)
    tried, bad = 0, []
    for b in run.t.builders:
        for when, tid, step, refused in b.moves:
            r = in_effect(receipts, tid, when)
            pr = run.w.pr_of(tid)
            if step not in names or not r or not pr or r[3] not in REPAIRED or r[2] != run.w.head_at(pr, when):
                continue
            tried += 1
            if refused and "test receipt" in refused:
                bad.append(f"{run.w.title(tid)} {step} ({r[3]})")
    if not tried:
        return False, "no move carried a repaired-failure receipt: the situation did not happen"
    return not bad, f"{tried} moves on a repaired-failure receipt; refused: " + ", ".join(bad[:6])


def retried_after_receipt(run):
    """A move refused for its receipt is made by Tico within a tick of the builder's clean receipt."""
    receipts, slack = posted(run), run.scenario.tick_minutes * 60 + 1
    rows = {}
    done, late = 0, []
    for b in run.t.builders:
        for when, tid, step, refused in b.moves:
            if not refused or "test receipt" not in refused:
                continue
            clean = next((p for p in receipts if p[1] == tid and p[0] > when and p[3] == "clean"), None)
            if not clean:
                continue
            if tid not in rows:
                rows[tid] = [(run.w.H.parse_ts(r["created_at"]), r["name"]) for r in run.w.sql(
                    "SELECT e.created_at, s.name FROM task_events e JOIN task_steps s ON s.id=e.new WHERE e.task_id=? "
                    "AND e.field='step' ORDER BY e.created_at, e.rowid", (tid,))]
            moved = next((at for at, name in rows[tid] if name == step and at >= clean[0]), None)
            done += 1
            if not moved or (moved - clean[0]).total_seconds() > slack:
                late.append(f"{run.w.title(tid)} {step} "
                            + (f"{(moved - clean[0]).total_seconds() / 60:.0f} min" if moved else "never")
                            + f" after the receipt at {clean[0] - START}")
    if not done:
        return False, "no refused move was followed by a clean receipt: the situation did not happen"
    return not late, f"{done} refused moves put right; late: " + ", ".join(late[:6])


SCENARIOS = [
    Scenario("receipt_wording",
             "Builders word test receipts the messy real ways (repaired earlier failures, failing reruns, lint only), "
             "and some PRs' only CI check is skipped: review opens only on a passing receipt at the head.",
             setup=setup, during=during, styles={"builder": {"receipt_wording": 0.7}},
             checks=standard(("no review on a head whose receipt reports a failure there", GATE, no_review_on_failure),
                             ("no review on a skipped-only PR without a receipt", GATE, no_review_on_skipped),
                             ("a repaired earlier-head failure does not block the move", GATE, repaired_passes),
                             ("a refused move is made within a tick of the clean receipt", GATE,
                              retried_after_receipt))),
]
