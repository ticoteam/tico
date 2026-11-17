# pyright: strict
"""A listening's GitHub source: one item per pull request merged into a repository's default branch, or per release.

The server reads each GitHub source once an hour (backend/listening.py `read_github`). A read lists what changed since
the source's cursor, kept in its `snapshot` as JSON (`{"follow": "prs"|"releases", "since": <ISO time>}`):

- The first read, and the first after `follow` changed, only sets the cursor: as a Page source, a GitHub source starts
  from now on and makes no item of what was there before.
- Pull requests are listed most recently updated first. A merged PR's `updated_at` is never before its `merged_at`, so
  the listing stops at the first PR updated before the cursor: nothing past it can have been merged since.
- A read GitHub refuses part way (a rate limit, an error) saves nothing and keeps the old cursor, so the next read
  lists the same changes again and none is skipped. A listing longer than MAX_PAGES (more PRs updated in an hour than
  that) saves what it listed and moves on, so a very busy repository cannot hold its source at one cursor for ever.

Each item's id is `gh:<owner/repo>#pr:<number>` or `gh:<owner/repo>#release:<id>`, unique in a listening
(listening_items' UNIQUE (automation_id, native_id)), so a PR read twice is one item. Items never carry a diff.

It reads with a token from the team's GitHub App when one can be minted (the repository's own, else the
installation's, which reads public repositories), otherwise with GitHub's public API.
"""

import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol, cast

import httpx

from .store import Problem

log = logging.getLogger(__name__)

API = "https://api.github.com"
FOLLOWS = ("prs", "releases")
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
PER_PAGE = 100
MAX_PAGES = 5  # a read lists at most this many pages; the next carries on from the same cursor
PR_BODY = 1_500
RELEASE_NOTES = 4_000

Item = dict[str, str | None]
Result = dict[str, Any]
Fetch = Callable[[str, str], tuple[int, Mapping[str, str], Any]]

# The GET this module makes: `(path, token) -> (status, headers, json)`. Tests set their own.
FETCH: Fetch | None = None


def _map(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


class Minter(Protocol):
    def mint(self, repos: list[str], permissions: dict[str, str], diagnose: bool = True) -> tuple[str, str]: ...


def repo_of(text: object) -> str | None:
    """`owner/repo` from what a person typed: the name itself or a github.com address. None when it is neither."""
    value = str(text or "").strip()
    value = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", value, flags=re.I)
    value = value.split("?")[0].split("#")[0].strip("/")
    value = re.sub(r"\.git$", "", value)
    parts = value.split("/")
    if len(parts) > 2:  # github.com/owner/repo/releases, /pulls and the like
        value = "/".join(parts[:2])
    return value if REPO.fullmatch(value) else None


def native_id(repo: str, kind: str, key: object) -> str:
    return f"gh:{repo.lower()}#{kind}:{key}"


def _fetch(path: str, token: str) -> tuple[int, Mapping[str, str], Any]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "TicoListening/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with httpx.Client(timeout=httpx.Timeout(15, connect=3), follow_redirects=False, trust_env=False) as client:
        response = client.get(API + path, headers=headers)
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    return response.status_code, response.headers, body


def token_for(service: Minter | None, repo: str) -> str:
    """A token that reads `repo`: the App's for that repository, else its installation's, else none (public API)."""
    if not service:
        return ""
    choices: list[list[str]] = [[repo], []]
    for repos in choices:
        try:
            return service.mint(repos, {"metadata": "read"}, diagnose=False)[0]
        except Problem:
            continue
        except (httpx.HTTPError, OSError, ValueError, KeyError):  # an App half set up must not stop the read
            log.warning("GitHub App token for the listening read of %s failed", repo, exc_info=True)
            return ""
    return ""


class Refused(Exception):
    def __init__(self, status: str, error: str):
        super().__init__(error)
        self.status, self.error = status, error


def _get(path: str, token: str, repo: str) -> Any:
    status, headers, body = (FETCH or _fetch)(path, token)
    if status == 200:
        return body
    lower = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    said = str(_map(body).get("message") or "")
    if status == 429 or (status == 403 and (lower.get("x-ratelimit-remaining") == "0" or "rate limit" in said.lower())):
        reset = lower.get("x-ratelimit-reset")
        when = ""
        if reset and str(reset).isdigit():
            when = " until " + datetime.fromtimestamp(int(reset), timezone.utc).strftime("%H:%M UTC")
        raise Refused("rate_limited", f"GitHub's rate limit{when}; the next read carries on")
    if status in (401, 403, 404):
        raise Refused(
            "blocked",
            f"Tico can't see {repo}: no such repository, or it is private and the GitHub App is not installed on it",
        )
    raise Refused("error", f"GitHub answered {status} for {repo}")


def _iso(value: Any) -> str | None:
    """GitHub's `2026-11-16T09:30:00Z` as the hub's own `...T09:30:00.000000Z`, so it sorts with the hub's times."""
    if not value:
        return None
    at = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _pr_item(repo: str, pr: Any) -> Item:
    labels = ", ".join(
        str(_map(label).get("name") or "") for label in _list(pr.get("labels")) if _map(label).get("name")
    )
    title = str(pr.get("title") or "").strip()
    body = str(pr.get("body") or "").strip()
    text = title + (f"\nLabels: {labels}" if labels else "") + (f"\n\n{body[:PR_BODY]}" if body else "")
    return {
        "native_id": native_id(repo, "pr", pr["number"]),
        "url": pr.get("html_url") or f"https://github.com/{repo}/pull/{pr['number']}",
        "author": str(_map(pr.get("user")).get("login") or ""),
        "title": f"{title} (#{pr['number']})",
        "text": text,
        "posted_at": _iso(pr["merged_at"]),
    }


def _release_item(repo: str, release: Any) -> Item:
    tag = str(release.get("tag_name") or "").strip()
    name = str(release.get("name") or "").strip()
    title = tag if not name or name == tag else f"{tag} — {name}"
    notes = str(release.get("body") or "").strip()[:RELEASE_NOTES]
    return {
        "native_id": native_id(repo, "release", release["id"]),
        "url": release.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}",
        "author": str(_map(release.get("author")).get("login") or ""),
        "title": title,
        "text": (title + ("\n\n" + notes if notes else "")).strip(),
        "posted_at": _iso(release.get("published_at")),
    }


def _prs(repo: str, token: str, since: str | None) -> tuple[list[Item], str | None, bool]:
    """(items merged after `since`, newest merge time seen, finished: False when the page cap cut the listing)."""
    branch = str(_map(_get(f"/repos/{repo}", token, repo)).get("default_branch") or "main")
    items: list[Item] = []
    newest: str | None = None
    for page in range(1, MAX_PAGES + 1):
        rows = _list(
            _get(
                f"/repos/{repo}/pulls?state=closed&base={branch}&sort=updated&direction=desc&per_page={PER_PAGE}&page={page}",
                token,
                repo,
            )
        )
        for pr in rows:
            merged = _iso(pr.get("merged_at"))
            if merged:
                newest = max(newest or merged, merged)
                if since and merged > since:
                    items.append(_pr_item(repo, pr))
        last = _iso(rows[-1].get("updated_at")) if rows else None
        if len(rows) < PER_PAGE or not since or (last and last < since):
            return items, newest, True
    return items, newest, False


def _releases(repo: str, token: str, since: str | None) -> tuple[list[Item], str | None, bool]:
    items: list[Item] = []
    newest: str | None = None
    for page in range(1, MAX_PAGES + 1):
        rows = _list(_get(f"/repos/{repo}/releases?per_page={PER_PAGE}&page={page}", token, repo))
        older = False
        for release in rows:
            if release.get("draft"):
                continue
            published = _iso(release.get("published_at"))
            if not published:
                continue
            newest = max(newest or published, published)
            if since and published > since:
                items.append(_release_item(repo, release))
            else:
                older = True
        if len(rows) < PER_PAGE or not since or older:
            return items, newest, True
    return items, newest, False


def read(target: str, follow: str, snapshot: str | None, now: str, token: str = "") -> Result:
    """One read: `{status, items, snapshot, error}`, never raising. `snapshot` is the new cursor to keep, or None to
    keep the old one (GitHub refused the read)."""
    repo = repo_of(target)
    if not repo:
        return {"status": "blocked", "items": [], "snapshot": None, "error": "A GitHub source is owner/repo"}
    follow = follow if follow in FOLLOWS else "prs"
    try:
        cursor = _map(json.loads(snapshot)) if snapshot else {}
    except ValueError:
        cursor = {}
    since = (str(cursor.get("since") or "") or None) if cursor.get("follow") == follow else None
    try:
        items, newest, finished = (_prs if follow == "prs" else _releases)(repo, token, since)
    except Refused as refused:
        return {"status": refused.status, "items": [], "snapshot": None, "error": refused.error}
    # GitHub can answer anything (a page that is not a list, a row missing a field): the source says so, the rest read.
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("Could not read GitHub %s for a listening (%s)", repo, type(exc).__name__)
        return {
            "status": "error",
            "items": [],
            "snapshot": None,
            "error": f"Could not read {repo}: {type(exc).__name__}",
        }
    if not since:  # the first read: where later ones start, and no items
        return {
            "status": "ok",
            "items": [],
            "snapshot": json.dumps({"follow": follow, "since": newest or now}),
            "error": "",
        }
    if not finished:
        log.warning(
            "listening read of %s listed %d pages and stopped; older changes since the last read are skipped",
            repo,
            MAX_PAGES,
        )
    items.sort(key=lambda item: item["posted_at"] or "")
    keep = json.dumps({"follow": follow, "since": max(since, newest or since)})
    return {"status": "ok", "items": items, "snapshot": keep, "error": ""}
