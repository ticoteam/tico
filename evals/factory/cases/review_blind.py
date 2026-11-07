"""A later check reads its ticket blind to the earlier reviews (backend/review_blind.py). Each turn on a check that comes
after a review reads the check, its ticket and the ticket's comments as its own token, and nothing it reads holds an
earlier review's subtask, a message that names one, or the text only those messages carry. A person reads those
findings on the same tickets meanwhile, so the reads are judged against findings that are there, not an empty ticket."""

from ..scenarios import Scenario, standard, tickets

PERSON = "ana-test"  # the person's token: she reads the findings that a later check must not
SHORT = 8  # a finding's text shorter than this is too common to tell from other messages


def _steps_before(w, step):
    """The review steps on the board before `step`: the earlier reviews a check at `step` is blind to. Only a review
    check is blind to them: a QA check after the reviews reads their findings as it always did."""
    names = []
    for s in w.board.steps:
        if s.name == step:
            return names if s.check and s.check.kind == "review" else []
        if s.check and s.check.kind == "review":
            names.append(s.name)
    return []


def _strings(value):
    """Every text in a reply, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _findings(w, ticket, earlier):
    """What the earlier reviews left on the ticket: their subtasks' ids, the messages that name them (the findings
    forwarded to the ticket and the subtasks' own comments), and the texts that only those messages carry."""
    subs = [c["id"] for c in w.checks(ticket) if str(c["owner_id"]).startswith("bot:") and w.check_step(c) in earlier]
    if not subs:
        return [], [], []
    marks = ",".join("?" * len(subs))
    named = w.sql(
        f"SELECT id, body FROM messages WHERE json_extract(refs,'$.subtask') IN ({marks}) "
        f"OR json_extract(refs,'$.task') IN ({marks})",
        (*subs, *subs),
    )
    mids = [m["id"] for m in named]
    candidates = {m["body"] for m in named if m["body"] and len(m["body"].strip()) >= SHORT}
    only = set()
    if candidates:
        same = w.sql(
            f"SELECT id, body FROM messages WHERE body IN ({','.join('?' * len(candidates))})", tuple(candidates)
        )
        named_ids = set(mids)
        only = {b for b in candidates if all(m["id"] in named_ids for m in same if m["body"] == b)}
    return subs, mids, sorted(only)


def _scan(run):
    """Each read a later check's turn made of its ticket since the last scan, with what the earlier reviews left on
    that ticket. Reads of a check at the first review have no earlier review, so they are not kept."""
    w = run.w
    if not hasattr(run, "later"):
        run.later, run.scanned, run.person, run.paths = [], 0, {}, {}
    for _, _, check, ticket, reads in w.context_reads[run.scanned :]:
        row = next((c for c in w.checks(ticket) if c["id"] == check), None)
        step = w.check_step(row) if row else None
        earlier = _steps_before(w, step) if step else []
        if earlier:
            subs, mids, texts = _findings(w, ticket, earlier)
            run.later.append({"ticket": ticket, "reads": reads, "subs": subs, "mids": mids, "texts": texts})
    run.scanned = len(w.context_reads)


def during(run, w, team, i):
    """Each tick, a person reads each ticket a later check has read findings on, once: the findings are there for her,
    so a read that leaves them out is leaving out something real. A finding can sit on the ticket, on its comments or
    on the earlier review's own subtask, so she reads each of those; `run.paths` keeps what each one answered."""
    _scan(run)
    for read in run.later:
        ticket = read["ticket"]
        if ticket in run.person or not (read["mids"] or read["texts"]):
            continue
        paths = [f"tasks/{ticket}", f"tasks/{ticket}/comments", *(f"tasks/{sub}/comments" for sub in read["subs"])]
        seen = []
        for path in paths:
            status, reply = w.request("get", path, token=PERSON)
            shown = set(_strings(reply))
            found = status == 200 and (
                any(m in shown for m in read["mids"]) or any(t in s for t in read["texts"] for s in shown)
            )
            seen.append((path, status, found))
        run.paths[ticket] = seen
        run.person[ticket] = any(found for _, _, found in seen)


def reads_answer(run):
    """A later check's turn reads its ticket and the comments on it as its own token, and every read answered."""
    _scan(run)
    if not run.later:
        return False, "no later check's turn read its ticket"
    bad = [
        f"{run.w.title(r['ticket'])} {path}: {status}"
        for r in run.later
        for path, status, _ in r["reads"]
        if status != 200
    ]
    return not bad, ", ".join(bad[:4])


def no_earlier_findings(run):
    """No read a later check's turn made holds an earlier review's subtask, a message that names one, or the text only
    those messages carry."""
    _scan(run)
    holding = [r for r in run.later if r["subs"] or r["mids"] or r["texts"]]
    if not holding:
        return False, "no later check's turn read a ticket with an earlier review's findings on it"
    leaks = []
    for r in holding:
        for path, _, reply in r["reads"]:
            shown = set(_strings(reply))
            hit = [i for i in r["subs"] + r["mids"] if i in shown] + [
                t for t in r["texts"] if any(t in s for s in shown)
            ]
            if hit:
                leaks.append(f"{run.w.title(r['ticket'])} {path}: {str(hit[0])[:40]}")
    return not leaks, "; ".join(leaks[:4]) or f"{len(holding)} reads of tickets with earlier findings held none"


def person_reads_findings(run):
    """A person reads the findings on each ticket a later check was kept from: the positive control for the reads. A
    miss names each read path the person made on that ticket and what it answered (status, and * when it had them)."""
    _scan(run)
    if not run.person:
        return False, "no person read a ticket with an earlier review's findings on it"
    label = ["ticket", "ticket comments"]
    missing = [t for t, shown in run.person.items() if not shown]
    detail = "; ".join(
        f"{run.w.title(t)}: "
        + ", ".join(
            f"{label[k] if k < len(label) else 'earlier review comments'} {status}{'*' if found else ''}"
            for k, (_, status, found) in enumerate(run.paths.get(t, []))
        )
        for t in missing
    )
    return not missing, detail[:600]


SCENARIOS = [
    Scenario(
        "review_blind",
        "Six tickets go through Review 1 and Review 2: each later check's turn reads its ticket as its own token and holds "
        "none of the earlier review's findings, while a person reads those findings on the same tickets.",
        setup=lambda run, w, team: tickets(run, w, 6),
        during=during,
        checks=standard(
            ("a later check's turn reads its ticket and its comments", None, reads_answer),
            ("a later check's readable context holds no earlier review's findings", None, no_earlier_findings),
            ("a person reads the findings the later check was kept from", None, person_reads_findings),
        ),
    ),
]
