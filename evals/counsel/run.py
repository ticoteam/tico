#!/usr/bin/env python3
"""Score Counsel's extraction against the hand-labeled obligations in evals/counsel/<slug>.json (docs/<slug>.txt is the
text Counsel reads). On demand only: never in CI.

    python3 evals/counsel/run.py --self-check
        Every expected and not-ours quote must be in its document (after the same normalization Counsel uses). Exit 1
        when one is not, so a label cannot drift from its text.

    python3 evals/counsel/run.py --suggestions out.json
        Score suggestions saved from a run: {"<slug>": [{"text", "quote", "section", "who"}, ...], ...}.

    python3 evals/counsel/run.py --url http://127.0.0.1:18300 --owner-token <owner API token> [--only slug,slug]
        Run the real thing on a running dev install with a Counsel bot: each document is attached to a task of the owner's
        (a person's upload into Files) and added through POST /api/v2/rules/read, as the Add document dialog adds it; each
        waits for Counsel's run to end (--wait seconds, 3600 by default: the run's own limit), and its From's suggestions
        are read back, every page, then scored.

Scoring, per document:
- a suggestion is "in the document" when its quote is in the text; otherwise it counts as quote-not-in-source;
- an expected obligation is found when one in-document suggestion has the quote (ratio >= 0.6 to the expected quote)
  or the same sentence (>= 0.5 word overlap) and a quote overlap of at least 0.3; otherwise it is missed;
- a suggestion that matches a not-ours quote is wrong scope; any other unmatched suggestion is extra.

Writes evals/counsel/results.md (the table, the command and the date) unless --self-check or --suggestions with --no-write.
"""
import argparse
import datetime
import difflib
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
STRAIGHT = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", " ": " "})
STOP = frozenset("the and for with that this from are can our your you any all not does into over under about what when "
                 "which will may must shall".split())


def normal(text):
    """The same form Counsel matches quotes in (backend/counsel.py normal): NFKC, straight quotes and dashes, one space."""
    value = unicodedata.normalize("NFKC", str(text or "")).translate(STRAIGHT)
    return re.sub(r"\s+", " ", value).strip().casefold()


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", normal(text)) if len(w) > 2 and w not in STOP}


def quote_ratio(a, b):
    a, b = normal(a), normal(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def jaccard(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def load_docs():
    docs = {}
    for path in sorted(HERE.glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        text = (DOCS / f"{path.stem}.txt").read_text(encoding="utf-8")
        docs[path.stem] = {"meta": meta, "text": text}
    return docs


def self_check(docs):
    problems = []
    for slug, doc in docs.items():
        meta = doc["meta"]
        for key in ("url", "fetched_at", "from", "expected"):
            if key not in meta:
                problems.append(f"{slug}: no {key}")
        if len(meta.get("expected", [])) < 3:
            problems.append(f"{slug}: {len(meta.get('expected', []))} expected obligations (at least 3)")
        for group, items in (("expected", meta.get("expected", [])), ("not_ours", meta.get("not_ours", []))):
            for item in items:
                if not all(k in item for k in ("text", "quote", "section", "who_hint")):
                    problems.append(f"{slug}: {group} item missing a field: {item.get('text', '?')[:60]}")
                elif quote_ratio(item["quote"], doc["text"]) < 1.0 or normal(item["quote"]) not in normal(doc["text"]):
                    problems.append(f"{slug}: {group} quote not in the document: {item['quote'][:80]}")
    return problems


def match_expected(suggestion, expected):
    q = quote_ratio(suggestion["quote"], expected["quote"])
    if q >= 0.6:
        return True
    return jaccard(suggestion["text"], expected["text"]) >= 0.5 and q >= 0.3


def score(docs, suggestions):
    rows, bad = [], []
    for slug, doc in docs.items():
        meta, text = doc["meta"], doc["text"]
        mine = suggestions.get(slug, [])
        expected = meta.get("expected", [])
        not_ours = meta.get("not_ours", [])
        found, extra, wrong, missing_quote = set(), 0, 0, 0
        for s in mine:
            if normal(s.get("quote")) not in normal(text):
                missing_quote += 1
                bad.append((slug, s.get("quote", "")))
                continue
            hits = [i for i, e in enumerate(expected) if match_expected(s, e)]
            if hits:
                found.update(hits)
            elif any(match_expected(s, n) for n in not_ours):
                wrong += 1
            else:
                extra += 1
        rows.append({"slug": slug, "from": meta["from"]["name"], "expected": len(expected), "found": len(found),
                     "missed": len(expected) - len(found), "extra": extra, "wrong_scope": wrong,
                     "quote_not_in_source": missing_quote, "suggested": len(mine),
                     "missed_text": [expected[i]["text"] for i in range(len(expected)) if i not in found]})
    return rows, bad


def table(rows):
    head = ["document", "expected", "found", "missed", "extra", "wrong scope", "quote not in source", "suggested"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    tot = {k: sum(r[k] for r in rows) for k in ("expected", "found", "missed", "extra", "wrong_scope",
                                                 "quote_not_in_source", "suggested")}
    for r in rows:
        lines.append(f"| {r['slug']} | {r['expected']} | {r['found']} | {r['missed']} | {r['extra']} | "
                     f"{r['wrong_scope']} | {r['quote_not_in_source']} | {r['suggested']} |")
    lines.append(f"| **total** | {tot['expected']} | {tot['found']} | {tot['missed']} | {tot['extra']} | "
                 f"{tot['wrong_scope']} | {tot['quote_not_in_source']} | {tot['suggested']} |")
    recall = tot["found"] / tot["expected"] if tot["expected"] else 0.0
    return "\n".join(lines), recall


FINISHED = ("done", "closed", "declined")


def live(docs, url, token, only, wait=3600):
    """Each document as a person adds it in the app, then read back as Counsel's suggestions.

    A person puts a document in Files by attaching it to a task (the task's Files +, POST /tasks/{id}/files): one task of
    the person's own, so no bot is woken by it. The Add document dialog then picks that file from Files and sends it
    to POST /rules/read, as here. Each read waits for Counsel's run on its task to end (every run finished, and the task
    done, which Tico sets when the run completes), then reads every page of that From's suggestions."""
    import time
    base = url.rstrip("/") + "/api/v2/"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4())})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path}: {exc.code} {exc.read()[:300]!r}") from None

    def run_ended(task_id):
        """(ended, status, runs, finished): ended once the task is finished and every run on it has finished."""
        got = call("GET", f"tasks/{task_id}")
        status = (got.get("task") or got).get("status")
        runs = got.get("runs") or []
        finished = sum(1 for r in runs if r.get("finished_at"))
        return status in FINISHED and runs and finished == len(runs), status, len(runs), finished

    me = call("GET", "me")["actor"]
    # One holder per run: an open one from an earlier run would make this a duplicate ask.
    started = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    holder = call("POST", "tasks", {"owner": me, "title": f"Documents for the Counsel eval ({started})",
                                    "body": "Each document the Counsel eval adds to Rules, attached here."})
    holder_id = (holder.get("task") or holder)["id"]
    suggestions = {}
    for slug, doc in docs.items():
        if only and slug not in only:
            continue
        meta = doc["meta"]
        attached = call("POST", f"tasks/{holder_id}/files", {"name": f"{slug}.txt", "text": doc["text"]})
        file_id = attached["file_id"]
        source = call("POST", "rule-sources", {"name": meta["from"]["name"], "kind": meta["from"]["kind"]})
        source_id = source["source"]["id"]
        read = call("POST", "rules/read", {"file_id": file_id, "source_id": source_id})
        task_id = read["task_id"]
        deadline = time.time() + wait
        while True:
            ended, status, runs, finished = run_ended(task_id)
            if ended:
                break
            if time.time() >= deadline:
                print(f"{slug}: Counsel's run on task {task_id} did not end within {wait}s (task {status}, "
                      f"{finished} of {runs} runs finished); scoring the suggestions it wrote so far", file=sys.stderr)
                break
            time.sleep(10)
        listed, cursor = [], ""
        while True:
            page = call("GET", f"rules?status=suggested&source={urllib.parse.quote(source_id)}&limit=100"
                        + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""))
            listed += page["rules"]
            cursor = page.get("next_cursor")
            if not cursor:
                break
        out = []
        for rule in listed:
            cite = rule.get("cite") or {}
            if cite.get("file_id") != file_id:
                continue
            out.append({"text": rule["text"], "quote": cite.get("quote", ""), "section": cite.get("section", ""),
                        "who": rule.get("who")})
        suggestions[slug] = out
        print(f"{slug}: {len(out)} suggestions, task {status}", file=sys.stderr, flush=True)
    return suggestions


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--suggestions", help="a JSON file of suggestions per document to score")
    parser.add_argument("--url", help="a running dev install, for a live run")
    parser.add_argument("--owner-token", help="the owner's API token (Settings > Computers)")
    parser.add_argument("--only", default="", help="comma-separated document slugs")
    parser.add_argument("--wait", type=int, default=3600, help="seconds to wait for Counsel's run on each document")
    parser.add_argument("--no-write", action="store_true", help="print the table without writing results.md")
    args = parser.parse_args(argv)
    docs = load_docs()
    only = {s for s in args.only.split(",") if s}

    if args.self_check:
        problems = self_check(docs)
        for p in problems:
            print("FAIL", p)
        n_expected = sum(len(d["meta"].get("expected", [])) for d in docs.values())
        n_not_ours = sum(len(d["meta"].get("not_ours", [])) for d in docs.values())
        print(f"{len(docs)} documents, {n_expected} expected obligations, {n_not_ours} not-ours quotes: "
              f"{'all quotes in their documents' if not problems else str(len(problems)) + ' problems'}")
        return 1 if problems else 0

    if args.suggestions:
        suggestions = json.loads(Path(args.suggestions).read_text(encoding="utf-8"))
        command = f"python3 evals/counsel/run.py --suggestions {args.suggestions}"
    elif args.url and args.owner_token:
        suggestions = live(docs, args.url, args.owner_token, only, args.wait)
        command = f"python3 evals/counsel/run.py --url {args.url} --owner-token <owner token>"
    else:
        print("Give --self-check, --suggestions FILE, or --url and --owner-token for a live run.")
        return 2

    scored = {k: v for k, v in docs.items() if not only or k in only}
    rows, bad = score(scored, suggestions)
    text, recall = table(rows)
    print(text)
    print(f"\nrecall {recall:.2f}; quotes not in source: {len(bad)}")
    if not args.no_write:
        stamp = datetime.datetime.now(datetime.UTC).date().isoformat()
        body = (f"# Counsel eval results\n\nRun {stamp}: `{command}`\n\n{text}\n\nRecall (found / expected): {recall:.2f}."
                f" Quotes not in the source: {len(bad)}.\n\nWrong scope counts suggestions that match a not-ours "
                "sentence (a vendor's own duty). Extra counts suggestions that match no label.\n")
        (HERE / "results.md").write_text(body, encoding="utf-8")
        print(f"wrote {HERE / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
