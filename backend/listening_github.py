# pyright: strict
"""A listening's GitHub source: one item per pull request merged into a repository's default branch, or per release.

The server reads each GitHub source once an hour (backend/listening.py `read_github`). A read lists what changed since
the source's cursor, kept in its `snapshot` as JSON (`{"follow": "prs"|"releases", "since": <ISO time>}`):

- The first read, and the first after `follow` changed, only sets the cursor: as a Page source, a GitHub source starts
  from now on and makes no item of what was there before.
- Pull requests are listed most recently updated first. A merged PR's `updated_at` is never before its `merged_at`, so
  the listing stops at the first PR updated before the cursor: nothing past it can have been merged since.
- A read GitHub refuses part way (a rate limit, an error) saves nothing and keeps the old cursor, so the next read
  lists the same changes again and none is skipped. A PR listing longer than MAX_PAGES saves what it listed and the
  next read carries on where it stopped (`_prs`). Only more PRs updated in one hour than a read lists, or more
  releases than that since the last read, leave changes unread: the read saves what it listed, moves on and its
  source shows the gap as an error.

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
from typing import Any, NamedTuple, Protocol, cast

import httpx

from .store import Problem

log = logging.getLogger(__name__)

API = "https://api.github.com"
FOLLOWS = ("prs", "releases")
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9._-]{1,100}")  # never `.` or `..`
PER_PAGE = 100
MAX_PAGES = 5  # pages a read lists; a longer PR listing carries on at the next read (`_prs`)
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


class Listing(NamedTuple):
    items: list[Item]
    newest: str | None  # the newest merge or release seen
    carry: dict[str, Any]  # where an unfinished listing carries on at the next read; {} when it finished
    gap: bool  # changes were left unread: more than a read lists


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
    with httpx.Client(timeout=httpx.Timeout(15, connect=3), follow_redirects=True, trust_env=False) as client:
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
            f"Tico can't see {repo}: no such public repository (private repositories are not read yet)",
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


def _gap(repo: str, since: str) -> str:
    return f"More than {MAX_PAGES * PER_PAGE} changes in {repo} since {since[:16].replace('T', ' ')} UTC; some were not read"


def _prs(repo: str, token: str, since: str | None, carry: dict[str, Any]) -> Listing:
    """PRs merged after `since`, listed most recently updated first.

    A listing longer than MAX_PAGES stops and leaves `carry` (`page`, the next page to read; `top`, the `updated_at`
    at the head of the listing; `newest`): the next read lists the head down to `top` (PRs updated since), then
    carries on from `page`. Rows only ever move down the listing (a newly updated PR goes to the head), so carrying
    on from the same page reads some rows twice, saved once by their id, and skips none. `since` stays where it was
    until the listing is finished."""
    branch = str(_map(_get(f"/repos/{repo}", token, repo)).get("default_branch") or "main")
    found: dict[str, Item] = {}
    newest: str | None = str(carry.get("newest") or "") or None
    budget = MAX_PAGES

    def page_of(n: int) -> list[Any]:
        nonlocal budget
        budget -= 1
        return _list(
            _get(
                f"/repos/{repo}/pulls?state=closed&base={branch}&sort=updated&direction=desc&per_page={PER_PAGE}&page={n}",
                token,
                repo,
            )
        )

    def take(rows: list[Any]) -> str | None:
        """Saves the merged ones; the last row's `updated_at`."""
        nonlocal newest
        for pr in rows:
            merged = _iso(pr.get("merged_at"))
            if merged:
                newest = max(newest or merged, merged)
                if since and merged > since:
                    item = _pr_item(repo, pr)
                    found[str(item["native_id"])] = item
        return _iso(rows[-1].get("updated_at")) if rows else None

    def walk(n: int, rows: list[Any], stop: str) -> int | None:
        """Pages from `n` until one ends before `stop` or the listing ends: None, or the next page when out of pages."""
        while True:
            last = take(rows)
            if len(rows) < PER_PAGE or not last or last <= stop:
                return None
            if budget <= 0:
                return n + 1
            n += 1
            rows = page_of(n)

    first = page_of(1)
    top = _iso(first[0].get("updated_at")) if first else None
    if not since:  # the first read only needs the newest merge
        take(first)
        return Listing([], newest, {}, False)
    if not carry:
        left = walk(1, first, since)
    else:
        if walk(1, first, str(carry.get("top") or since)) is not None:  # more updated in an hour than a read lists
            return Listing(list(found.values()), newest, {}, True)
        page = int(carry.get("page") or 2)
        left = walk(page, page_of(page), since) if budget > 0 else page
    if left is None:
        return Listing(list(found.values()), newest, {}, False)
    return Listing(list(found.values()), newest, {"page": left, "top": top, "newest": newest}, False)


def _releases(repo: str, token: str, since: str | None) -> Listing:
    """Releases published after `since`. More than a read lists (MAX_PAGES) is reported as a gap: releases come a few a
    day, so only a source turned off for a long time could see it."""
    found: list[Item] = []
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
                found.append(_release_item(repo, release))
            else:
                older = True
        if len(rows) < PER_PAGE or not since or older:
            return Listing(found, newest, {}, False)
    return Listing(found, newest, {}, True)


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
    same = cursor.get("follow") == follow
    since = (str(cursor.get("since") or "") or None) if same else None
    carry = _map(cursor.get("carry")) if same and follow == "prs" else {}
    try:
        listing = _prs(repo, token, since, carry) if follow == "prs" else _releases(repo, token, since)
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
            "snapshot": json.dumps({"follow": follow, "since": listing.newest or now}),
            "error": "",
        }
    items = sorted(listing.items, key=lambda item: item["posted_at"] or "")
    if listing.carry:  # unfinished: `since` stays until the listing is read to its end
        keep = {"follow": follow, "since": since, "carry": listing.carry}
        return {"status": "ok", "items": items, "snapshot": json.dumps(keep), "error": ""}
    keep = {"follow": follow, "since": max(since, listing.newest or since)}
    if listing.gap:  # moves on, so a very busy repository cannot hold its source at one cursor for ever, and says so
        log.warning("listening read of %s left changes unread: more than %d pages", repo, MAX_PAGES)
        return {"status": "error", "items": items, "snapshot": json.dumps(keep), "error": _gap(repo, since)}
    return {"status": "ok", "items": items, "snapshot": json.dumps(keep), "error": ""}
