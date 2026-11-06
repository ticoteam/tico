#!/usr/bin/env python3
"""The Librarian eval (docs/librarian.md): load a small fixture of docs into a live Tico, ask each question in
questions.yaml through POST /api/v2/docs/ask, wait for the answers, and score them.

    scripts/docs-eval.sh [--only ID] [--keep] [--wait SECONDS] [--json FILE] [--fail-under 0.8]

Run it against a Tico of your own that has a running Librarian, never a team's real one: the Librarian
logs what it is asked in its own docs. TICO_URL is the address (https://tico.example.com) and TICO_TOKEN a
personal API token of a person on it (Settings > Computers > API tokens).

It reports three rates: how often an answerable question's answer cited every doc it should have (and said
something rather than "Not in the docs"), and how often an unanswerable one said "Not in the docs." Both and the fact rate should be near 100%; `--fail-under` checks all three. Nothing here runs in CI.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from clients import docs_ask

PREFIX = "eval-fixture/"


def load_questions(path=HERE / "questions.yaml"):
    questions = (yaml.safe_load(Path(path).read_text()) or {}).get("questions") or []
    for q in questions:
        if not q.get("id") or not q.get("question") or not (q.get("unknown") or q.get("cite") or q.get("manual")):
            raise ValueError(f"question {q.get('id')!r} needs an id, a question and cite, manual or unknown")
    return questions


def fixture_files(root=HERE / "fixture"):
    return sorted(p for p in Path(root).rglob("*.md"))


def exact_fact(text, expected):
    pattern = re.escape(str(expected)).replace(r"\ ", r"\s+")
    # A whole value: $29 cannot pass for $299 or $29.99, nor 14 days for 114 days.
    suffix = r"(?!\w|[.,]\d)" if str(expected)[-1:].isdigit() else r"(?!\w)"
    return bool(re.search(r"(?<!\w)" + pattern + suffix, text, re.I))


# Sentence openers and common words that are capitalized without naming another plan or person.
ARTICLES = {
    "the",
    "its",
    "it",
    "this",
    "that",
    "a",
    "an",
    "in",
    "actually",
    "however",
    "instead",
    "reality",
    "you",
    "your",
    "yes",
    "no",
    "per",
    "each",
    "every",
    "so",
    "but",
    "and",
    "which",
    "price",
    "cost",
    "plan",
    "monthly",
    "month",
    "about",
    "roughly",
    "approximately",
    "note",
    "also",
    "only",
    "now",
    "today",
    "then",
    "to",
    "be",
    "clear",
    "practice",
    "fact",
    "honestly",
    "basically",
    "overall",
    "well",
    "ok",
    "just",
    "still",
    "really",
    "usually",
    "typically",
    "currently",
    "normally",
    "generally",
    "after",
    "before",
    "with",
    "without",
    "for",
    "on",
    "at",
    "if",
    "when",
    "we",
    "i",
    "they",
    "customers",
    "users",
    "everyone",
    "anyone",
    "there",
    "here",
}


def fact_matches(text, fact):
    subject, predicate = fact["subject"], fact["predicate"]
    # Markdown emphasis says nothing about the value, and a negated uncertainty ("no price variation") confirms it.
    text = re.sub(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)", "", text)
    text = re.sub(
        r"\b(?:no|not|never|without(?: any)?)\s+(?:\w+\s+)?(?:variation|varies|vary)\b|\b\w+n't\s+vary\b",
        " ",
        text,
        flags=re.I,
    )
    claims, related = [], False
    for sentence in re.split(r"(?<!\d)\.|\.(?!\d)|[;!?\n]|\b(?:and|but|while|whereas)\b", text, flags=re.I):
        sentence = re.sub(r"^\s*(?:actually|however|instead|in fact|in reality)\b[, :]*", "", sentence, flags=re.I)
        named = bool(re.search(subject, sentence, re.I))
        continuation = related and bool(
            re.match(r"\s*(?:it|its|they|their|this(?: plan)?|the plan|the price|is|are|was|were)\b", sentence, re.I)
            or re.match(r"\s*(?:" + predicate + r")", sentence, re.I)
        )
        related = named or continuation
        if related and re.search(predicate, sentence, re.I):
            claims.append(sentence)
    if not claims:
        return False
    negative = re.compile(r"\b(?:not|never|cannot|can't|aren't|isn't|doesn't|don't|no|non)\b", re.I)

    def negations(claim):
        match = re.search(predicate, claim, re.I)
        before = re.split(r"\b(?:and|but|while|whereas)\b", claim[: match.start()], flags=re.I)[-1]
        after = claim[match.end() :].split(",", 1)[0]
        # Negation after the predicate must be adjacent; a later clause can describe another plan.
        adjacent = re.match(
            r"^\W+(?:(?:is|are|was|were|will|can|may|be)\W+)*"
            r"(?:(?:not|never|no)\W+)+",
            after,
            re.I,
        )
        return len(negative.findall(" ".join(before.split()[-6:]))) + (
            len(negative.findall(adjacent.group(0))) if adjacent else 0
        )

    if "value" in fact:
        expected = str(fact["value"])
        pattern = fact.get("value_pattern")
        if not pattern:
            if expected.startswith("$"):
                pattern = r"\$\d+(?:[.,]\d+)*"
            elif expected.endswith("%"):
                pattern = r"\d+(?:\.\d+)?%"
            else:
                unit = re.sub(r"^[\d.,]+\s*", "", expected)
                pattern = r"\d+(?:\.\d+)?\s+" + re.escape(unit)
        number = re.match(r"^\$?(\d+(?:[.,]\d+)*)", expected)
        approximate = r"\b(?:between|from|less than|more than|at least|at most|under|over|up to|about|approximately|roughly|around|circa|nearly|almost|close to|approx\.?)\s*[:,\-–—(]?\s*(?:USD\s*|US\s*)?\$?\d"
        # Uncertainty words count unless negated: "with some variation" fails, "with no variation" does not.
        approximate += r"|\b(?:variation|varies|vary|variable|estimated?|ballpark)\b"
        approximate += r"|\(\s*(?:approx\w*\.?|about|roughly|around|circa|estimated?)\s*\)"
        approximate += (
            r"|\d[\d.,]*\s*(?:USD|US dollars|dollars)?\s*[,(]?\s*(?:or so|or thereabouts|give or take|more or less|approximately|roughly|"
            r"about|or less|or more|tops|at most|at least|-?ish)\b"
        )
        if number:
            value = r"(?<!\d)\$?" + re.escape(number.group(1)) + r"(?!\d)"
            separator = r"\s*(?:[-–—]|to|through)\s*"
            approximate += r"|[~≈]\s*\$?\d|" + value + separator + r"\$?\d|\d[\d.,]*" + separator + value
        if not all(
            negations(claim) == 0
            and not re.search(approximate, claim, re.I)
            and exact_fact(claim, expected)
            and all(exact_fact(value, expected) for value in re.findall(pattern, claim, re.I))
            for claim in claims
        ):
            return False
        # Anywhere in the answer, another value of the same kind contradicts it unless its sentence plainly names
        # something else (another capitalized plan or name that is not the subject): "Its price is $99", "In reality
        # you pay $99" and "The price is $99" fail; "The Team plan is $99" does not.
        about = True  # a sentence naming nothing refers back to the last thing named
        for sentence in re.split(r"(?<!\d)\.|\.(?!\d)|[;!?\n]|\b(?:and|but|while|whereas)\b", text, flags=re.I):
            # A clause's first word is capitalized anyway ("Ultimately", "Subscription pricing"): it names
            # something only when the price follows it ("Team costs $99").
            start = len(sentence) - len(sentence.lstrip())
            names = [
                m[0]
                for m in re.finditer(r"\b[A-Z][a-zA-Z]+\b", sentence)
                if not re.fullmatch(subject, m[0], re.I)
                and m[0].lower() not in ARTICLES
                and (
                    m.start() != start
                    or re.match(r"(?:'s\s+(?:" + predicate + r")|\s+plan\b|\s+costs?\b)", sentence[m.end() :], re.I)
                )
            ]
            about = bool(re.search(subject, sentence, re.I)) or (about and not names)
            others = [v for v in re.findall(pattern, sentence, re.I) if not exact_fact(v, expected)]
            if others and about:
                return False
        return True
    return all(negations(claim) == (1 if fact["polarity"] == "negative" else 0) for claim in claims)


def score(question, answer, id_by_path):
    """How one answer did. `answer` is the Librarian's text, or None when it never came."""
    if answer is None:
        return {"answered": False, "cited": False, "unknown_ok": False, "facts": False}
    said_unknown = not docs_ask.covered(answer)
    if question.get("unknown"):
        return {
            "answered": True,
            "cited": None,
            "unknown_ok": said_unknown and answer.lstrip().startswith("Not in the docs."),
            "facts": None,
        }
    cited = {c["url_or_id"] for c in docs_ask.parse_citations(answer) if c["type"] == "internal"}
    wanted = {id_by_path.get(PREFIX + path) for path in question.get("cite") or []}
    manual = {
        Path(c["url_or_id"].split("#", 1)[0]).stem for c in docs_ask.parse_citations(answer) if c["type"] == "manual"
    }
    manual_ok = set(question.get("manual") or []) <= manual
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", answer)
    facts = all(exact_fact(text, expected) for expected in question.get("contains") or []) and all(
        fact_matches(text, fact) for fact in question.get("facts") or []
    )
    return {
        "answered": True,
        "cited": (not said_unknown) and None not in wanted and wanted <= cited and manual_ok,
        "unknown_ok": None,
        "facts": facts and not said_unknown,
    }


def summary(results):
    hits = [r["score"]["cited"] for r in results if r["score"]["cited"] is not None]
    unknowns = [r["score"]["unknown_ok"] for r in results if r["score"]["unknown_ok"] is not None]
    facts = [r["score"]["facts"] for r in results if r["score"]["facts"] is not None]
    rate = lambda xs: (sum(bool(x) for x in xs) / len(xs)) if xs else None
    seconds = [r["seconds"] for r in results if r["seconds"] is not None]
    return {
        "citation_hit_rate": rate(hits),
        "citation_hits": [sum(bool(x) for x in hits), len(hits)],
        "unknown_rate": rate(unknowns),
        "unknown_hits": [sum(bool(x) for x in unknowns), len(unknowns)],
        "fact_rate": rate(facts),
        "median_seconds": statistics.median(seconds) if seconds else None,
    }


class Tico:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            with exc:
                detail = exc.read().decode(errors="replace")[:300]
                raise SystemExit(f"{method} {path} answered {exc.code}: {detail}") from None


def load_fixture(tico, ids=None):
    """Track every successfully imported doc, so a later failure still cleans up this run."""
    ids = ids if ids is not None else {}
    for file in fixture_files():
        rel = file.relative_to(HERE / "fixture").as_posix()
        text = file.read_text()
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), rel)
        doc = tico.call("POST", "/api/v2/docs", {"path": PREFIX + rel, "title": title, "body": text})["doc"]
        ids[doc["path"]] = doc["id"]
    return ids


def remove_fixture(tico, ids):
    retained = []
    for doc_id in ids:
        try:
            doc = tico.call("GET", "/api/v2/docs/" + doc_id)["doc"]
            tico.call(
                "PATCH",
                "/api/v2/docs/" + doc_id,
                {"version": doc["version"], "archived": True, "note": "docs-eval finished"},
            )
        except (Exception, SystemExit) as exc:
            retained.append(doc_id)
            print(f"Cleanup failed for {doc_id}: {exc}", file=sys.stderr)
    if retained:
        print("Retained fixture IDs: " + ", ".join(retained), file=sys.stderr)
    return retained


def ask(tico, question, wait):
    started = time.monotonic()
    sent = tico.call("POST", "/api/v2/docs/ask", {"question": question, "new_conversation": True})
    while time.monotonic() - started < wait:
        snapshot = tico.call("GET", f"/api/v2/conversations/{sent['conversation_id']}/snapshot")
        if message := docs_ask.final_reply(snapshot, sent["message_id"]):
            return message["body"], time.monotonic() - started
        time.sleep(3)
    return None, None


def main(argv=None):
    global PREFIX
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", help="run only this question id (repeatable)")
    parser.add_argument("--keep", action="store_true", help="leave the fixture docs in place afterwards")
    parser.add_argument("--wait", type=int, default=240, help="seconds to wait for each answer")
    parser.add_argument("--json", help="write every answer and score to this file")
    parser.add_argument(
        "--fail-under", type=float, default=0.0, help="exit 1 if citation, unknown or fact rate is below this (0 to 1)"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.fail_under <= 1:
        parser.error("--fail-under must be between 0 and 1")
    available = load_questions()
    unknown_ids = set(args.only or []) - {q["id"] for q in available}
    if unknown_ids:
        parser.error("Unknown question IDs: " + ", ".join(sorted(unknown_ids)))
    questions = [q for q in available if not args.only or q["id"] in args.only]
    if not questions:
        parser.error("No questions selected")
    if not os.environ.get("TICO_URL") or not os.environ.get("TICO_TOKEN"):
        raise SystemExit("Set TICO_URL (https://your-tico) and TICO_TOKEN (a personal API token)")
    PREFIX = "eval-fixture/" + uuid.uuid4().hex[:12] + "/"
    tico = Tico(os.environ["TICO_URL"], os.environ["TICO_TOKEN"])
    if not tico.call("GET", "/api/v2/librarian").get("available"):
        raise SystemExit(
            "The Librarian is not running on that Tico: turn it on (Docs > Ask the Librarian) and enroll a computer"
        )
    print(f"Loading {len(fixture_files())} fixture docs under {PREFIX} ...")
    id_by_path, results, retained = {}, [], []
    try:
        load_fixture(tico, id_by_path)
        for q in questions:
            answer, seconds = ask(tico, q["question"], args.wait)
            outcome = score(q, answer, id_by_path)
            results.append(
                {"id": q["id"], "question": q["question"], "answer": answer, "seconds": seconds, "score": outcome}
            )
            verdict = (
                "no answer"
                if answer is None
                else ("unknown " + ("ok" if outcome["unknown_ok"] else "MISSED (it answered)"))
                if q.get("unknown")
                else (
                    "cited "
                    + ("ok" if outcome["cited"] else "MISSED")
                    + (", facts ok" if outcome["facts"] else ", facts off")
                )
            )
            print(f"  {q['id']:<18} {verdict:<28} {'' if seconds is None else f'{seconds:.0f}s'}")
    finally:
        if not args.keep:
            retained = remove_fixture(tico, id_by_path.values())
        elif id_by_path:
            print("Retained fixture IDs: " + ", ".join(id_by_path.values()), file=sys.stderr)
    totals = summary(results)
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    print(
        f"\nCitation hit rate  {totals['citation_hits'][0]}/{totals['citation_hits'][1]}  {pct(totals['citation_hit_rate'])}"
        f"\nSaid unknown       {totals['unknown_hits'][0]}/{totals['unknown_hits'][1]}  {pct(totals['unknown_rate'])}"
        f"\nFacts as expected  {pct(totals['fact_rate'])}"
        f"\nMedian time        {'n/a' if totals['median_seconds'] is None else format(totals['median_seconds'], '.0f') + 's'}"
    )
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": totals, "results": results}, indent=2) + "\n")
    rates = [r for r in (totals["citation_hit_rate"], totals["unknown_rate"], totals["fact_rate"]) if r is not None]
    return 1 if retained or (rates and min(rates) < args.fail_under) else 0


if __name__ == "__main__":
    sys.exit(main())
