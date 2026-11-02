"""The weekly Decide readout: per question set, the window's calls, cost, model (Jev or the fallback) and answer
spread, and the month's spend against the team's cap (`GET /api/v2/decisions/readout`, `hub decision readout`).

Plain code over the audit, no model: calls and models from the `judge.call` rows, cost from the `judge.spend` rows
(`backend/judge.py` `metered` writes one after every paid call, so a batch or a nightly run counts each call it paid
for). Rows are grouped by question set, the id before the `@` of their label, so a set's versions read as one.

It names nothing a caller wrote. A label is free text, so only the shipped question sets (`questions/*.json`, the
company's registry copy, and the few sets the code asks inline) are named; every other label is one "(other labels)"
row with counts and cost. The text after a named set's `@` is free text too: a version is shown only when the set
defines it (1 to the set's version, or the nightly learnings run's own marker), and any other suffix is counted under
the set and never shown. An answer is a band or an option of the set's own questions: a yes/no as under 0.3, 0.3
to 0.7 or 0.7 and over; a score as its level; a choice as the set's fixed option, and "(an open item)" for an option
the caller added (a roadmap item, a bot). A question the set does not define (one per bot, as Slack routing and live
meetings ask) is counted under "(other questions)".
"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from clients import judge as J
from .judge import spent_this_month

OTHER_LABELS = "(other labels)"
OTHER_QUESTIONS = "(other questions)"
OPEN_ITEM = "(an open item)"
BANDS = ("under 0.3", "0.3 to 0.7", "0.7 and over")
MAX_DAYS = 31
RUN = {"learnings-route": "run"}     # backend/learnings.py audits a night's run as one `learnings-route@run` row


def _inline_sets():
    """The sets the code asks without a questions/ file: {set id: {question id: fixed options or None}}."""
    from clients.hubtools import CLASSIFY_OPTIONS
    from .assistant import INTENT_CHOICES
    return {"support-text": {"verdict": tuple(CLASSIFY_OPTIONS)}, "assistant-intent": {"intent": tuple(INTENT_CHOICES)},
            "live-meeting-route": {}}


def known_sets(registry_dir=None):
    """({set id: {question id: (type, fixed options or None)}}, {set id: the versions it may show}) for every set the
    readout may name. A set's versions come from the set itself, never from a label."""
    out, versions = {}, {}
    for name, questions in _inline_sets().items():
        out[name] = {qid: ("choice", options) for qid, options in questions.items()}
        versions[name] = {"1"}
    for entry in J.list_sets():
        if entry.get("error"):
            continue
        try:
            data = J.load_set(entry["id"], registry_dir=registry_dir)
        except J.JudgeError:
            continue
        out[data["id"]] = {qid: (q.get("type"), tuple(q["criteria"]) if q.get("type") == "choice"
                                 and isinstance(q.get("criteria"), dict) else None)
                           for qid, q in data["questions"].items() if isinstance(q, dict)}
        versions[data["id"]] = {str(n) for n in range(1, data["version"] + 1)}
    for name, marker in RUN.items():
        versions.setdefault(name, set()).add(marker)
    return out, versions


def set_of(label, sets):
    name = str(label or "").split("@", 1)[0]
    return name if name in sets else OTHER_LABELS


def version_of(label, name, versions):
    """The version after the `@` of a named set's label when the set defines it, else None (never the label's text)."""
    suffix = str(label).split("@", 1)[1] if "@" in str(label) else None
    return suffix if suffix in versions.get(name, ()) else None


def band(p):
    return BANDS[0] if p < 0.3 else BANDS[1] if p < 0.7 else BANDS[2]


def spread_key(qid, answer, questions):
    """(question shown, value shown) for one audited answer, or None when it cannot be read."""
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):     # live meetings keep {slug: p}
        kind, value = "noul", float(answer)
    elif isinstance(answer, dict):
        kind, value = answer.get("type"), answer.get("value")
    else:
        return None
    defined = questions.get(qid)
    shown = qid if defined else OTHER_QUESTIONS
    try:
        if kind == "noul":
            return shown, band(float(value))
        if kind == "score":
            return shown, str(int(round(float(value))))
    except (TypeError, ValueError):
        return None
    if kind == "choice":
        options = defined[1] if defined else None
        return shown, value if options and isinstance(value, str) and value in options else OPEN_ITEM
    return None


def _on_jev(model):
    """Whether `model` is Jev: TypeSafe's own name, or a Tico model the Tico AI Gateway answers with on Jev."""
    return str(model or "").lower().startswith("jev") or model in J.GATEWAY_MODELS


def _calls(detail):
    """(answered on Jev, answered on another model, {error code: failed calls}) for one `judge.call` row."""
    if isinstance(detail.get("models"), dict):                  # one nightly learnings run: its calls added up
        models = {str(m): int(n) for m, n in detail["models"].items() if isinstance(n, int) and not isinstance(n, bool)}
        jev = sum(n for m, n in models.items() if _on_jev(m))
        failed = {str(code): int(n) for code, n in (detail.get("failed") or {}).items()
                  if isinstance(n, int) and not isinstance(n, bool) and n > 0} if isinstance(detail.get("failed"), dict) else {}
        return jev, sum(models.values()) - jev, failed
    calls = detail.get("calls") if isinstance(detail.get("calls"), int) and detail.get("calls") > 0 else 1
    if detail.get("error"):
        return 0, 0, {str(detail["error"]): calls}
    return (calls, 0, {}) if _on_jev(detail.get("model")) else (0, calls, {})


def _answers(detail):
    """Each call's answers on one `judge.call` row: a nightly learnings run keeps a list, one map per call."""
    answers = detail.get("answers")
    if isinstance(answers, dict):
        return [answers]
    return [a for a in answers if isinstance(a, dict)] if isinstance(answers, list) else []


def _empty():
    return {"calls": 0, "jev": 0, "fallback": 0, "failed": Counter(), "cost_usd": 0.0, "ceiling_priced": 0,
            "versions": set(), "answers": {}}


def readout(c, settings, days=7, at=None):
    """The readout over the `days` up to `at` (now), as data and as the text a person reads."""
    days = max(1, min(MAX_DAYS, int(days)))
    end = at or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"     # noqa: E731 hubdb.now()'s format
    sets, versions = known_sets(getattr(settings, "registry_dir", None))
    by = {}
    rows = c.execute("SELECT action, target, detail FROM events WHERE ts>=? AND ts<? "
                     "AND action IN ('judge.call','judge.spend')", (stamp(start), stamp(end))).fetchall()
    for action, target, raw in rows:
        try:
            detail = json.loads(raw or "{}")
        except ValueError:
            detail = {}
        detail = detail if isinstance(detail, dict) else {}
        label = detail.get("label") or target
        name = set_of(label, sets)
        row = by.setdefault(name, _empty())
        if action == "judge.spend":
            cost = detail.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                row["cost_usd"] += float(cost)
            row["ceiling_priced"] += detail.get("price") == "ceiling"
            continue
        version = version_of(label, name, versions)
        if version:
            row["versions"].add(version)
        jev, other, failed = _calls(detail)
        row["jev"] += jev
        row["fallback"] += other
        row["failed"].update(failed)
        row["calls"] += jev + other + sum(failed.values())
        if name == OTHER_LABELS:
            continue
        for answers in _answers(detail):
            for qid, answer in answers.items():
                key = spread_key(str(qid), answer, sets[name])
                if key:
                    row["answers"].setdefault(key[0], Counter())[key[1]] += 1
    out = []
    for name in sorted(by, key=lambda n: (n == OTHER_LABELS, n)):
        row = by[name]
        out.append({"set": name, "versions": sorted(row["versions"]), "calls": row["calls"], "jev": row["jev"],
                    "fallback": row["fallback"], "failed": dict(sorted(row["failed"].items())),
                    "jev_share": round(row["jev"] / row["calls"], 4) if row["calls"] else None,
                    "cost_usd": round(row["cost_usd"], 6), "ceiling_priced": row["ceiling_priced"],
                    "answers": {q: dict(sorted(v.items())) for q, v in sorted(row["answers"].items())}})
    # Ask ingest's own outcomes (backend/ask_ingest.py): through, merged, FYI, hands needed, sent back, failed open,
    # and how many a person let through. Counts only; the items are the person's to read.
    # Each of the set's judge calls is reconciled to its item row, or a named reason, or counted as unexplained.
    from .ask_ingest import QUESTION_SET, outcome_counts, reconcile
    gate = outcome_counts(c, stamp(start), stamp(end))
    recon = reconcile(c, stamp(start), stamp(end))
    if gate["outcomes"] or gate["rehearsal"] or recon["calls"] or recon["no_call"]:
        row = next((r for r in out if r["set"] == QUESTION_SET), None)
        if row is None:
            row = {"set": QUESTION_SET, "versions": [], "calls": 0, "jev": 0, "fallback": 0, "failed": {},
                   "jev_share": None, "cost_usd": 0.0, "ceiling_priced": 0, "answers": {}}
            out.append(row)
        row["gate"] = gate
        row["reconcile"] = recon
    calls = sum(r["calls"] for r in out)
    jev = sum(r["jev"] for r in out)
    cap = float(settings.decisions_monthly_usd or 0)
    spent = spent_this_month(c, stamp(end))
    data = {"from": stamp(start), "to": stamp(end), "days": days, "sets": out,
            "total": {"calls": calls, "jev": jev, "fallback": sum(r["fallback"] for r in out),
                      "failed": sum(sum(r["failed"].values()) for r in out),
                      "jev_share": round(jev / calls, 4) if calls else None,
                      "cost_usd": round(sum(r["cost_usd"] for r in out), 6)},
            "month": {"month": stamp(end)[:7], "spent_usd": round(spent, 6), "cap_usd": cap,
                      "share": round(spent / cap, 4) if cap > 0 else None}}
    data["text"] = text(data)
    return data


def _usd(value):
    return f"${value:.4f}" if 0 < value < 0.01 else f"${value:.2f}"


def _pct(share):
    return "n/a" if share is None else f"{share * 100:.0f}%"


def text(data):
    """The readout in plain words, built from `data` alone."""
    lines = [f"Decide readout, {data['from'][:10]} to {data['to'][:10]} (UTC, {data['days']} days)"]
    total = data["total"]
    if not total["calls"]:
        lines.append("No Decide calls in this window.")
    for row in data["sets"]:
        failed = ", ".join(f"{code} {n}" for code, n in row["failed"].items())
        head = (f"{row['set']}{' v' + '/'.join(row['versions']) if row['versions'] else ''}: {row['calls']} calls, "
                f"{_usd(row['cost_usd'])}; Jev {row['jev']} ({_pct(row['jev_share'])}), fallback {row['fallback']}"
                + (f", failed {failed}" if failed else ""))
        if row["ceiling_priced"]:
            head += f"; {row['ceiling_priced']} counted at the ceiling price (no known price)"
        lines.append(head)
        for qid, values in row["answers"].items():
            lines.append(f"  {qid}: " + ", ".join(f"{value} {n}" for value, n in values.items()))
        if row["calls"] and not row["answers"] and row["set"] != OTHER_LABELS:
            lines.append("  answers: not kept in this set's audit")
        if row.get("gate"):
            gate = row["gate"]
            if gate["outcomes"]:
                lines.append("  outcomes: " + ", ".join(f"{k} {n}" for k, n in gate["outcomes"].items())
                             + f"; let through {gate['let_through']}")
            if gate["rehearsal"]:
                lines.append("  rehearsal, would have: " + ", ".join(f"{k} {n}" for k, n in gate["rehearsal"].items()))
        if row.get("reconcile"):
            rec = row["reconcile"]
            named = "".join(f", {k} {n}" for k, n in rec["reasons"].items())
            lines.append(f"  calls reconciled: {rec['calls']} = {rec['landed']} with an item row{named}; "
                         f"unexplained {rec['unexplained']}; rows with no call {rec['no_call']}")
    if total["calls"]:
        lines.append(f"All sets: {total['calls']} calls, {_usd(total['cost_usd'])}; Jev answered "
                     f"{_pct(total['jev_share'])} (target 90%), fallback {total['fallback']}, failed {total['failed']}")
    month = data["month"]
    cap = (f"of the {_usd(month['cap_usd'])} cap ({_pct(month['share'])})" if month["cap_usd"] > 0 else "(no cap set)")
    lines.append(f"This month ({month['month']}): {_usd(month['spent_usd'])} {cap}")
    return "\n".join(lines)
