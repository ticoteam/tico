"""Read-only release evidence checklist; never tags, dispatches CI or changes tickets.

Run from a checkout with fetched release tags and a scoped hub/gh login:
    python scripts/release_checklist.py --head <candidate> --previous vX.Y.Z > checklist.md
    python scripts/release_checklist.py --head <candidate> --json > checklist.json

Exit 0 means no evidence gaps, 1 means gaps, 2 means collection failed. A checklist
is a snapshot of visible ticket evidence, not release approval or test execution.
"""
import argparse
import json
import re
import subprocess
import sys

try:
    from scripts import freeze_guard as freeze
except ModuleNotFoundError:  # Direct script invocation.
    import freeze_guard as freeze


VERDICT = re.compile(r"^VERDICT: (approve|changes) PR #(\d+) head ([0-9a-f]{40})\s*$", re.M)
ROLE = re.compile(r"^(Review [12]|QA|Docs|Documentation|Technical Writer)\s*:", re.I)
SHA = re.compile(r"\b[0-9a-f]{40}\b")
PASS = re.compile(r"\b(pass(?:ed)?|green|success(?:ful)?)\b", re.I)
TEST = re.compile(r"\b(tests?|pytest|cargo|browser|CI)\b", re.I)
FAIL = re.compile(r"\b(?:[1-9]\d*\s*(?:fail(?:s|ing|ed|ures?)?|errors?)|fail(?:s|ing|ed|ures?)?|timed?\s*out|timeouts?|not run|pending|"
                  r"will|planned|0\s*passed|(?:no|not|without)\s+(?:pass(?:ed)?|green|success))\b", re.I)
FAILED_RESULT = re.compile(r"\b(?:fail(?:s|ing|ed|ures?)?|errors?|timed?\s*out|timeouts?)\b", re.I)
MAX_PAGES = 100


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        # Errors can contain provider responses; don't copy credentials or payloads.
        raise freeze.GuardError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout


def hub(*args):
    return json.loads(command("hub", *args))


def history(task):
    messages, before, seen = [], None, set()
    for _ in range(MAX_PAGES):
        page = hub("conversation", "show", task["conversation_id"], *(["--before", before] if before else []))
        for message in page["messages"]:
            refs = message.get("refs") or json.loads(message.get("refs_json") or "{}")
            named = refs.get("task") or refs.get("task_id") or (page.get("conversation") or {}).get("task_id")
            if isinstance(named, list):
                named = next((v for v in named if isinstance(v, str) and v.strip()), None)
            if named == task["id"]:
                messages.append(message)
        if not page.get("has_more"):
            return messages
        before = page.get("next_before")
        if not before or before in seen:
            break
        seen.add(before)
    raise freeze.GuardError("ticket history incomplete: pagination limit or invalid cursor")


def collect_ticket(tid):
    result = hub("task", "show", tid)
    task = result["task"]
    task["messages"] = history(task)
    task["children"] = []
    for child in result.get("children", []):
        if not ROLE.match(child["title"]):
            continue
        detail = hub("task", "show", child["id"])["task"]
        detail["messages"] = history(detail)
        task["children"].append(detail)
    return task


def linked_tickets(slug, number):
    result = hub("sql", "SELECT DISTINCT l.task_id FROM task_links l "
                 "JOIN tasks t ON t.id=l.task_id JOIN task_types ty ON ty.id=t.type_id "
                 "WHERE l.kind='pr' AND lower(l.repo)=lower(:repo) AND l.number=:number "
                 "AND ty.name='Dev tickets' ORDER BY l.task_id LIMIT 101",
                 "--param", f"repo={slug}", "--param", f"number={number}", "--json", "--max-rows", "101")
    if result.get("truncated") or len(result["rows"]) > 100:
        raise freeze.GuardError("too many linked Dev tickets; checklist would be incomplete")
    return [row[0] for row in result["rows"]]


def source(task, message):
    return {"task": task["id"], "message": message.get("id"),
            "actor": message.get("from_actor"), "created": message.get("created"),
            "text": message.get("body", "")}


def owner_messages(task):
    if not task.get("owner"):
        return []
    return sorted((m for m in task.get("messages", []) if m.get("from_actor") == task.get("owner")
                   and not any(m.get(k) for k in ("deleted_at", "hidden_at", "superseded_at"))),
                  key=lambda m: (m.get("created") or "", m.get("id") or ""))


def verdicts(task, role, number):
    found = []
    for child in task.get("children", []):
        match = ROLE.match(child["title"])
        if not match or match[1].lower() != role.lower():
            continue
        for message in owner_messages(child):
            for match in VERDICT.finditer(message.get("body", "")):
                if int(match[2]) == number:
                    found.append({**source(child, message), "verdict": match[1], "head": match[3]})
    return sorted(found, key=lambda r: (r["created"] or "", r["message"] or ""))


def passing_test(text, head):
    # A green receipt for some other head, or a plan to run tests, is not evidence.
    # Match head and result on one line, so an old pass cannot bless a new failure.
    return any(head in SHA.findall(line) and TEST.search(line) and PASS.search(line)
               and not FAIL.search(re.sub(r"\b0\s*(?:failed|failures?|errors?)\b", "", line, flags=re.I))
               for line in text.splitlines())


def failing_test(line, head):
    # A subsequent passing rerun does not adjudicate a known failure at the same head.
    result = re.sub(r"\b(?:0|no)\s+(?:failed|failures?|errors?)\b", "", line, flags=re.I)
    return head in SHA.findall(line) and TEST.search(line) and FAILED_RESULT.search(result)


def ticket_evidence(task, pr):
    number, head = pr["number"], pr["head"]
    gaps, reviews = [], {}
    if not (task.get("step") or {}).get("name"):
        gaps.append("Ticket: missing factory step")
    qa = any((tag.get("key") if isinstance(tag, dict) else tag) == "qa" for tag in task.get("tags", []))
    for role in ("Review 1", "Review 2", "QA"):
        receipts = verdicts(task, role, number)
        reviews[role] = receipts
        required = role == "Review 1" or (role == "QA" and qa)
        required |= role == "Review 2" and bool(receipts or any(
            ROLE.match(c["title"]) and ROLE.match(c["title"])[1].lower() == "review 2"
            for c in task.get("children", [])))
        if required:
            last = receipts[-1] if receipts else None
            if not last:
                gaps.append(f"{role}: missing verdict")
            elif last["head"] != head:
                gaps.append(f"{role}: verdict names older/different head {last['head']}")
            elif last["verdict"] != "approve":
                gaps.append(f"{role}: changes requested")
    messages = owner_messages(task)
    tests = [source(task, m) for m in messages if TEST.search(m.get("body", ""))]
    head_tests = [line for receipt in tests for line in receipt["text"].splitlines()
                  if head in SHA.findall(line) and TEST.search(line)]
    if any(failing_test(line, head) for line in head_tests):
        gaps.append("Tests: recorded failure at the merged head needs resolution")
    elif not head_tests or not passing_test(head_tests[-1], head):
        gaps.append("Tests: no current passing receipt naming the merged head")
    docs = []
    for child in task.get("children", []):
        match = ROLE.match(child["title"])
        if match and match[1].lower() in ("docs", "documentation", "technical writer"):
            docs.append({"task": child["id"], "title": child["title"], "status": child["status"],
                         "links": child.get("links", []),
                         "receipts": [source(child, m) for m in owner_messages(child)]})
    if qa and not docs:
        gaps.append("Docs: no completed Technical Writer task")
    if any(d["status"] not in ("done", "closed") for d in docs):
        gaps.append("Docs: incomplete Technical Writer task")
    return {"id": task["id"], "number": task.get("number"), "title": task["title"],
            "step": (task.get("step") or {}).get("name", "unknown"),
            "qa": qa, "reviews": reviews, "test_receipts": tests,
            "docs": docs, "gaps": gaps}


def build(prs, tickets, *, head, previous, previous_sha):
    rows = []
    for pr in prs:
        linked = [t for t in tickets if any(l.get("kind") == "pr" and l.get("url") == pr["url"]
                                           for l in t.get("links", []))]
        evidence = [ticket_evidence(t, pr) for t in linked]
        gaps = [f"Ticket {t.get('number') or t['id']}: {gap}" for t in evidence for gap in t["gaps"]]
        if not linked:
            gaps.append("No visible linked Dev ticket")
        rows.append({**pr, "tickets": evidence, "gaps": gaps})
    return {"schema_version": 1, "head": head, "previous": previous, "previous_sha": previous_sha,
            "security": "Outside the release pipeline; no Security result required",
            "prs": rows, "gaps": sum(len(row["gaps"]) for row in rows)}


def collect(repo, head, previous=None, slug=None):
    head = freeze.git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    previous = previous or freeze.previous_release(repo, head)
    previous_sha = freeze.git(repo, "rev-parse", "--verify", f"{previous}^{{commit}}").strip()
    slug = slug or freeze.repo_slug(repo)
    if not slug or not re.fullmatch(r"[\w.-]+/[\w.-]+", slug):
        raise freeze.GuardError("GitHub owner/repository required")
    changes = freeze.first_parent_log(repo, f"{previous_sha}..{head}")
    if not changes:
        raise freeze.GuardError("empty release range; name an earlier release tag")
    prs, tickets, seen = [], {}, set()
    for commit in reversed(changes):
        number = freeze.pr_number(repo, commit)
        if number is None:
            # Direct release notes are already covered by the freeze guard.
            if commit["touched"] <= freeze.NOTES_FILES:
                continue
            raise freeze.GuardError(f"commit {commit['sha']} has no identifiable PR; run the freeze guard")
        if number in seen:
            continue
        seen.add(number)
        pr = json.loads(command("gh", "api", f"repos/{slug}/pulls/{number}"))
        if not pr.get("merged_at"):
            raise freeze.GuardError(f"PR #{number} is not merged")
        prs.append({"number": number, "title": pr["title"], "url": pr["html_url"],
                    "head": pr["head"]["sha"], "merge_commit": commit["sha"], "merged_at": pr["merged_at"]})
        for tid in linked_tickets(slug, number):
            if tid not in tickets:
                tickets[tid] = collect_ticket(tid)
    return build(prs, list(tickets.values()), head=head, previous=previous, previous_sha=previous_sha)


def cell(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ")


def markdown(result):
    lines = ["# Release checklist", "", f"Candidate: `{result['head']}`", "",
             f"Previous: {cell(result['previous'])} (`{result['previous_sha']}`)", "",
             f"{len(result['prs'])} merged PRs; {result['gaps']} evidence gaps.", "",
             result["security"], "", "Evidence snapshot only; gaps require resolution by the Release Manager.", "",
             "| PR | Ticket / step | Review 1 | Review 2 | Tests | QA | Docs | Gaps |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for pr in result["prs"]:
        for ticket in pr["tickets"] or [None]:
            if ticket is None:
                values = [f"#{pr['number']} {pr['title']}", "Missing", "Unknown", "Unknown", "Unknown",
                          "Unknown", "Unknown", "; ".join(pr["gaps"])]
            else:
                def review(role):
                    receipts = ticket["reviews"][role]
                    return "; ".join(f"{r['verdict']} {r['head']}" for r in receipts) or "No verdict recorded"
                values = [f"#{pr['number']} {pr['title']}", f"#{ticket['number']} / {ticket['step']}",
                          review("Review 1"), review("Review 2"), str(len(ticket["test_receipts"])) + " receipts",
                          ("qa; " + review("QA")) if ticket["qa"] else "Not tagged qa",
                          "; ".join(d["title"] + " / " + d["status"] for d in ticket["docs"]) or "None recorded",
                          "; ".join(ticket["gaps"]) or "None"]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
        lines.extend(["", f"## PR #{pr['number']}", "", f"PR: {pr['url']}", "",
                      f"Merged head: `{pr['head']}`; landed commit: `{pr['merge_commit']}`", ""])
        for ticket in pr["tickets"]:
            for role, receipts in ticket["reviews"].items():
                for receipt in receipts:
                    lines.extend([f"{role} evidence: task {receipt['task']}, message {receipt['message']}, "
                                  f"actor {cell(receipt['actor'])}", "",
                                  *["> " + cell(line) for line in receipt["text"].splitlines()], ""])
            for receipt in ticket["test_receipts"]:
                lines.extend([f"Test evidence: task {receipt['task']}, message {receipt['message']}", "",
                              *["> " + cell(line) for line in receipt["text"].splitlines()], ""])
            for doc in ticket["docs"]:
                lines.extend([f"Docs task: {doc['task']} ({doc['status']})", "",
                              *[cell(l.get("url", "")) for l in doc["links"]], ""])
                for receipt in doc["receipts"]:
                    lines.extend([f"Docs evidence: message {receipt['message']}", "",
                                  *["> " + cell(line) for line in receipt["text"].splitlines()], ""])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--previous")
    parser.add_argument("--github", help="GitHub owner/repository; defaults to origin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = collect(args.repo, args.head, args.previous, args.github)
        print(json.dumps(result, indent=2) if args.json else markdown(result), end="\n" if args.json else "")
        return int(bool(result["gaps"]))
    except (freeze.GuardError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"release checklist unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
