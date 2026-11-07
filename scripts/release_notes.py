# pyright: strict
"""Release notes for one candidate: the pull requests merged between a stable release and the candidate, grouped for
the people who approve it. scripts/release_train.py posts them as the body of the release's approval issue.

    text = build(api, "v0.5.202", "v0.5.203")

`api` needs get(path), which returns a REST GET's decoded JSON (release_train.GitHub.get). Read-only. Standard library
only; release_train loads this file by path, because the Actions checkout is sparse.

A pull request is in the notes when a commit in the range names it (a merge commit's subject, or a squash commit's
"(#N)") and that commit is the pull request's merge commit. Order: high risk, then user-facing (ui/, clients/, app/),
then backend, then internal, which prints one line each. Every other block shows the Spec link, the demo steps, the
risk, the checks on the pull request's head commit, the reviews and receipts, and the areas its files touch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any, Protocol


class Api(Protocol):
    def get(self, path: str) -> Any: ...


PAGE = 100
COMMIT_PAGES = 10  # a range of up to 1000 commits; a longer one is cut, and the notes say so
LIST_PAGES = 5  # the files, reviews, comments and check runs of one pull request
MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from ")
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)\s*$")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
HEADING = re.compile(r"^##\s+(.+?)\s*$")
RISK = re.compile(r"^(high|medium|low)\b(?:\s+risk\b)?[\s:,.;—–-]*(.*)$", re.IGNORECASE)
RISK_LABELS = {"risk:high": "high", "high-risk": "high", "risk:medium": "medium", "risk:low": "low"}
RECEIPT = re.compile(r"^\s*[-*]?\s*\**(verdict|receipt)\**\s*:\s*(.+)$", re.IGNORECASE)
FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
AREAS = (
    ("ui/", "ui"),
    ("clients/", "clients"),
    ("app/", "app"),
    ("backend/", "backend"),
    ("runner-rs/", "runner"),
    ("runner_support/", "runner"),
    ("recorder/", "recorder"),
    ("templates/", "templates"),
    ("docs/", "docs"),
    ("scripts/", "scripts"),
    (".github/", "ci"),
)
USER_AREAS = frozenset({"ui", "clients", "app"})
BACKEND_AREAS = frozenset({"backend", "runner", "recorder", "templates"})
GROUPS = (("high", "High risk"), ("user", "User-facing"), ("backend", "Backend"), ("internal", "Internal"))

# Per-pull-request evidence beyond the fields above. A provider takes the parsed pull request and returns the lines to
# add under its block (a demo recording's link, say). None is registered yet, so no block prints an evidence line.
EVIDENCE: list[Callable[[Pull], list[str]]] = []


@dataclass(frozen=True)
class Pull:
    number: int
    title: str
    url: str
    author: str
    merged_at: str
    risk: str  # high, medium or low; "" when the pull request does not say
    risk_text: str
    sections: dict[str, str]
    areas: tuple[str, ...]
    checks: str
    failed: tuple[str, ...]
    reviews: tuple[str, ...]


def area_of(path: str) -> str:
    """The area a changed file belongs to. Tests are their own area: a pull request that only touches tests is internal."""
    name = path.rsplit("/", 1)[-1]
    if "/tests/" in "/" + path or name.startswith("test_") or name == "conftest.py":
        return "tests"
    for prefix, area in AREAS:
        if path.startswith(prefix):
            return area
    return "other"


def group_of(pull: Pull) -> str:
    """high (whatever the area), user, backend or internal: the order the notes list them in."""
    if pull.risk == "high":
        return "high"
    areas = set(pull.areas)
    if areas & USER_AREAS:
        return "user"
    if areas & BACKEND_AREAS:
        return "backend"
    return "internal"


def login(item: dict[str, Any]) -> str:
    user: dict[str, Any] = item.get("user") or {}
    return str(user.get("login") or "")


def sections_of(body: str) -> dict[str, str]:
    """The body's `## ` sections by lower-case heading, with HTML comments (the template's prompts) taken out."""
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in HTML_COMMENT.sub("", body).splitlines():
        heading = HEADING.match(line)
        if heading:
            current = found.setdefault(heading.group(1).lower(), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def risk_of(sections: dict[str, str], labels: list[str]) -> tuple[str, str]:
    """(level, the rest of the Risk section). The section starts with the level; a risk label is the fallback."""
    text = sections.get("risk", "")
    first = text.splitlines()[0] if text else ""
    found = RISK.match(first)
    if found:
        rest = "\n".join([found.group(2)] + text.splitlines()[1:]).strip()
        return found.group(1).lower(), rest
    for name in labels:
        level = RISK_LABELS.get(name.lower().replace(" ", ""))
        if level:
            return level, text
    return "", text


def checks_of(runs: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    """(a summary such as "51 passed, 0 failed", the names of the failed checks) for the runs on a head commit."""
    if not runs:
        return "none recorded", ()
    passed, pending = 0, 0
    failed: list[str] = []
    for run in runs:
        if run.get("status") != "completed":
            pending += 1
        elif run.get("conclusion") == "success":
            passed += 1
        elif run.get("conclusion") in FAILED:
            failed.append(str(run.get("name") or "unnamed check"))
    text = "%d passed, %d failed" % (passed, len(failed))
    if pending:
        text += ", %d not finished" % pending
    return text, tuple(failed)


def reviews_of(reviews: list[dict[str, Any]], comments: list[dict[str, Any]]) -> tuple[str, ...]:
    """Review verdicts (approved, changes requested) and receipt lines (`Verdict:`, `Receipt:`) from reviews and comments."""
    found: list[str] = []
    for review in reviews:
        state = str(review.get("state") or "")
        if state in ("APPROVED", "CHANGES_REQUESTED"):
            found.append("%s by @%s" % (state.lower().replace("_", " "), login(review)))
    for item in reviews + comments:
        for line in str(item.get("body") or "").splitlines():
            receipt = RECEIPT.match(line)
            if receipt:
                found.append("%s: %s" % (receipt.group(1).lower(), receipt.group(2).strip(" *")))
    return tuple(found[:6])


def paged(api: Api, path: str, key: str | None = None, pages: int = LIST_PAGES) -> list[dict[str, Any]]:
    """Every item of a REST list, PAGE at a time, up to `pages` pages."""
    items: list[dict[str, Any]] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, pages + 1):
        data = api.get("%s%sper_page=%d&page=%d" % (path, sep, PAGE, page))
        batch: list[dict[str, Any]] = data[key] if key else data
        items.extend(batch)
        if len(batch) < PAGE:
            break
    return items


def pr_numbers(commits: list[dict[str, Any]]) -> list[int]:
    """The pull request numbers the commits' subjects name, in the order they first appear."""
    numbers: list[int] = []
    for commit in commits:
        inner: dict[str, Any] = commit.get("commit") or {}
        message = str(inner.get("message") or "")
        subject = message.split("\n", 1)[0]
        found = MERGE_SUBJECT.match(subject) or SQUASH_SUBJECT.search(subject)
        if found and int(found.group(1)) not in numbers:
            numbers.append(int(found.group(1)))
    return numbers


def load(api: Api, number: int, shas: set[str]) -> Pull | None:
    """The pull request, when it is merged and its merge commit is in the range (`shas`)."""
    data: dict[str, Any] = api.get("pulls/%d" % number)
    if not data.get("merged_at") or str(data.get("merge_commit_sha") or "") not in shas:
        return None
    raw_labels: list[dict[str, Any]] = data.get("labels") or []
    labels = [str(label.get("name") or "") for label in raw_labels]
    sections = sections_of(str(data.get("body") or ""))
    level, rest = risk_of(sections, labels)
    files = paged(api, "pulls/%d/files" % number)
    areas = sorted({area_of(str(item.get("filename") or "")) for item in files} - {"tests"})
    head_ref: dict[str, Any] = data.get("head") or {}
    head = str(head_ref.get("sha") or "")
    runs = paged(api, "commits/%s/check-runs" % head, key="check_runs") if head else []
    checks, failed = checks_of(runs)
    reviews = paged(api, "pulls/%d/reviews" % number)
    comments = paged(api, "issues/%d/comments" % number)
    return Pull(
        number=number,
        title=str(data.get("title") or ""),
        url=str(data.get("html_url") or ""),
        author=login(data),
        merged_at=str(data.get("merged_at") or ""),
        risk=level,
        risk_text=rest,
        sections=sections,
        areas=tuple(areas),
        checks=checks,
        failed=failed,
        reviews=reviews_of(reviews, comments),
    )


def pulls_between(api: Api, base: str, head: str) -> tuple[list[Pull], bool]:
    """The merged pull requests from base (exclusive) to head, and whether the commit range was cut short."""
    commits = paged(api, "compare/%s...%s" % (base, head), key="commits", pages=COMMIT_PAGES)
    shas = {str(commit.get("sha") or "") for commit in commits}
    loaded = [load(api, number, shas) for number in pr_numbers(commits)]
    pulls = [pull for pull in loaded if pull is not None]
    return pulls, len(commits) >= PAGE * COMMIT_PAGES


def field(name: str, text: str, empty: str) -> list[str]:
    if not text:
        return ["- %s: %s" % (name, empty)]
    parts = [part for part in text.splitlines() if part.strip()]
    if len(parts) == 1:
        return ["- %s: %s" % (name, parts[0].strip())]
    return ["- %s:" % name] + ["  " + part.rstrip() for part in parts]


def risk_line(pull: Pull) -> str:
    rest = " ".join(pull.risk_text.split())
    if not pull.risk:
        return "- Risk: not stated"
    return "- Risk: " + " — ".join(part for part in (pull.risk, rest) if part)


def block(pull: Pull) -> list[str]:
    lines = [
        "### #%d %s" % (pull.number, pull.title),
        "%s · @%s · merged %s" % (pull.url, pull.author, pull.merged_at[:10]),
        "",
    ]
    lines += field("Spec", pull.sections.get("spec", ""), "none linked")
    lines += field("Demo steps", pull.sections.get("demo steps", ""), "none written")
    lines.append(risk_line(pull))
    checks = "- Checks: %s" % pull.checks
    if pull.failed:
        checks += " (failed: %s)" % ", ".join(pull.failed)
    lines.append(checks)
    if pull.reviews:
        lines.append("- Review: " + "; ".join(pull.reviews))
    lines.append("- Touches: " + (", ".join(pull.areas) or "tests only"))
    for provider in EVIDENCE:
        lines += ["- " + line for line in provider(pull)]
    return lines + [""]


def build(api: Api, base: str, head: str) -> str:
    """The notes for head, the changes since base, as Markdown."""
    pulls, truncated = pulls_between(api, base, head)
    count = len(pulls)
    lines = ["Changes since %s: %d merged pull request%s." % (base, count, "" if count == 1 else "s"), ""]
    if truncated:
        lines += ["The range has more commits than these notes read; the list stops at the last one read.", ""]
    if not pulls:
        lines.append("No merged pull request names a commit in this range.")
    order = [key for key, _ in GROUPS]
    ordered = sorted(pulls, key=lambda pull: (order.index(group_of(pull)), pull.number))
    for key, title in GROUPS:
        group = [pull for pull in ordered if group_of(pull) == key]
        if not group:
            continue
        lines += ["## " + title, ""]
        for pull in group:
            if key == "internal":
                lines.append("- #%d %s (@%s)" % (pull.number, pull.title, pull.author))
            else:
                lines += block(pull)
    return "\n".join(lines).rstrip() + "\n"
