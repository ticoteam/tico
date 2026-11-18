# pyright: strict
"""A listening's GitHub source: one item per pull request merged into a repository's default branch, or per release.

The server reads each GitHub source once an hour (backend/listening.py `read_github`). A read lists what changed since
the source's cursor, kept in its `snapshot` as JSON (`{"follow": "prs"|"releases", "since": <ISO time>}`):

- The first read, and the first after `follow` changed, only sets the cursor: as a Page source, a GitHub source starts
  from now on and makes no item of what was there before.
- Releases are listed whole at each read, and past GitHub's first 1,000 found by their tags: see `_releases`.
- Pull requests are listed most recently updated first. A merged PR's `updated_at` is never before its `merged_at`, so
  the listing stops at the first PR updated before the cursor: nothing past it can have been merged since.
- A read GitHub refuses part way (a rate limit, an error) saves nothing and keeps the old cursor, so the next read
  lists the same changes again and none is skipped. A listing longer than MAX_PAGES saves what it listed and the next
  read carries on where it stopped (`_listing`); the cursor moves on only once the listing is read to its end.

Each item's id is `gh:<owner/repo>#pr:<number>` or `gh:<owner/repo>#release:<id>`, unique in a listening
(listening_items' UNIQUE (automation_id, native_id)), so a PR read twice is one item. Items never carry a diff.

It reads with a token from the team's GitHub App when one can be minted (the repository's own, else the
installation's, which reads public repositories), otherwise with GitHub's public API.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, NamedTuple, Protocol, cast
from urllib.parse import quote

import httpx

from .store import Problem

log = logging.getLogger(__name__)

API = "https://api.github.com"
FOLLOWS = ("prs", "releases")
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9._-]{1,100}")  # never `.` or `..`
PER_PAGE = 100
MAX_PAGES = 5  # pages a read lists; a longer listing carries on at the next read (`_listing`)
RELEASES_LISTED = 1_000  # GitHub lists only a repository's first 1,000 releases ("Only the first 1000 results")
TAG_LOOKUPS = 20  # tags past that list a read looks up as releases (`_releases`)
MAX_SEGMENTS = 8  # unread stretches a cursor keeps (`_listing`)
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
    refused: tuple[str, str] | None = None  # (status, error) when a read kept what it listed before GitHub refused


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
    # The repository's own token reads its PRs and releases, private ones too when the App is installed on it, as a
    # docs source's does (backend/doc_sources.py); the installation's reads public repositories at the App's rate.
    choices: list[tuple[list[str], dict[str, str]]] = [
        ([repo], {"metadata": "read", "pull_requests": "read", "contents": "read"}),
        ([], {"metadata": "read"}),
    ]
    for repos, permissions in choices:
        try:
            return service.mint(repos, permissions, diagnose=False)[0]
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
    return _answer(status, headers, body, repo)


def _answer(status: int, headers: Mapping[str, str], body: Any, repo: str) -> Any:
    """GitHub's body when it answered 200, else why it refused, raised as `Refused`."""
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


def _listing(
    page_of: Callable[[int], list[Any]],
    order: Callable[[Any], str | None],
    take: Callable[[Any], None],
    since: str,
    carry: dict[str, Any],
) -> dict[str, Any]:
    """Reads a listing, newest first by `order`, down to `since`, at most MAX_PAGES pages. {} when it is read to its
    end, else where the next read carries on: `top`, the `order` at the head of the listing now, and `segments`, each
    `[page, stop, last]`: carry on from `page` down to `stop`; `last` is the `order` of the last row read. Newest first.

    A row moves up to the head of the listing (a PR updated or closed, a release made), which moves the rows it passes
    down: carrying on from a page reads some twice, saved once by their id. The next read lists the head down to the
    last read's `top` first, so a row that moved to the head is read there. A row can also leave the listing (a release
    deleted, a PR reopened), which moves the rows under it up: so a read carries on one page early, and further back
    while that page starts under `last`. A head longer than a read lists becomes one more segment; past MAX_SEGMENTS
    the two oldest merge into one from the earlier page, which reads more twice. Nothing is skipped."""
    budget = MAX_PAGES

    def get(n: int) -> list[Any]:
        nonlocal budget
        budget -= 1
        return page_of(n)

    def walk(n: int, rows: list[Any], stop: str, reach: int = 0) -> list[Any] | None:
        """From page `n`, already listed as `rows`, down to `stop`, and at least to page `reach`, so a read that
        carries on always reads one page it has not: None when there, else the segment left."""
        while True:
            for row in rows:
                take(row)
            last = order(rows[-1]) if rows else None
            if len(rows) < PER_PAGE or not last or last <= stop:
                return None
            if budget <= 0 and n >= reach:
                return [n + 1, stop, last]
            n += 1
            rows = get(n)

    def resume(segment: list[Any]) -> list[Any] | None:
        reach = int(segment[0])
        page, stop, last = max(1, reach - 1), str(segment[1]), str(segment[2])
        # From one page early: a row gone from the listing (a release deleted, a PR reopened) moves the rows under it
        # up, and a page that now starts under where the last read stopped steps back again, so none is skipped.
        rows = get(page)
        while page > 1 and rows and (order(rows[0]) or "") < last:  # past the budget: skipping is worse
            page -= 1
            rows = get(page)
        return walk(page, rows, stop, reach)

    first = get(1)
    top = order(first[0]) if first else None
    segments: list[list[Any]] = [list(_list(seg)) for seg in _list(carry.get("segments"))]
    if not segments:
        left = walk(1, first, since)
        return {} if left is None else {"top": top, "segments": [left]}
    left = walk(1, first, str(carry.get("top") or since))
    if left is not None:  # more changed since the last read than a read lists
        segments.insert(0, left)
        while (
            len(segments) > MAX_SEGMENTS
        ):  # the oldest two as one, from the earlier page: more read twice, none skipped
            newer, older = segments[-2], segments[-1]
            start = newer if int(newer[0]) <= int(older[0]) else older
            segments[-2:] = [[start[0], older[1], start[2]]]
        return {"top": top, "segments": segments}
    while segments and budget > 0:
        left = resume(segments[0])
        if left is None:
            segments.pop(0)
        else:
            segments[0] = left
    return {"top": top, "segments": segments} if segments else {}


def _prs(repo: str, token: str, since: str | None, carry: dict[str, Any]) -> Listing:
    """PRs merged into the default branch after `since`, from the closed PRs listed most recently updated first: a
    merged PR's `updated_at` is never before its `merged_at`."""
    branch = str(_map(_get(f"/repos/{repo}", token, repo)).get("default_branch") or "main")
    found: dict[str, Item] = {}
    newest: str | None = str(carry.get("newest") or "") or None

    def page_of(n: int) -> list[Any]:
        path = (
            f"/repos/{repo}/pulls?state=closed&base={branch}&sort=updated&direction=desc&per_page={PER_PAGE}&page={n}"
        )
        return _list(_get(path, token, repo))

    def take(pr: Any) -> None:
        nonlocal newest
        merged = _iso(pr.get("merged_at"))
        if merged:
            newest = max(newest or merged, merged)
            if since and merged > since:
                item = _pr_item(repo, pr)
                found[str(item["native_id"])] = item

    if not since:  # the first read only needs the newest merge
        for pr in page_of(1):
            take(pr)
        return Listing([], newest, {})
    left = _listing(page_of, lambda pr: _iso(pr.get("updated_at")), take, since, carry)
    return Listing(list(found.values()), newest, {**left, "newest": newest} if left else {})


def _tag_key(tag: str) -> str:
    return hashlib.sha1(tag.encode()).hexdigest()[:12]


def _releases(repo: str, token: str, since: str | None, carry: dict[str, Any]) -> Listing:
    """Releases published after `since`, the time the source started (pre-releases too, drafts never).

    GitHub lists releases by the date of the commit each was made from, not by when it was published: a release
    published today from an older commit (a backport, an old draft) lies anywhere in the list, and no row marks where
    older publications start. So `since` never moves and each read lists the whole list: a release published since the
    last read is found by this one, wherever it lies, and one read again is saved once by its id.

    GitHub lists only a repository's first RELEASES_LISTED releases. Past them, a release is found by its tag: one call
    lists every tag, and a tag that is neither a listed release's nor one already looked up (`carry["beyond"]`, a short
    hash per tag) is looked up as a release, at most TAG_LOOKUPS a read; the rest wait for the next. The first read
    only notes the tags past the list. A read GitHub refuses part way keeps what it found and says why."""
    found: dict[str, Item] = {}
    listed: set[str] = set()
    known: set[str] | None = None if carry.get("beyond") is None else {str(k) for k in _list(carry.get("beyond"))}

    def take(rows: list[Any]) -> None:
        for release in rows:
            listed.add(str(release.get("tag_name") or ""))
            published = _iso(release.get("published_at"))
            if release.get("draft") or not published or not since or published <= since:
                continue
            item = _release_item(repo, release)
            found[str(item["native_id"])] = item

    def page_of(n: int) -> list[Any]:
        return _list(_get(f"/repos/{repo}/releases?per_page={PER_PAGE}&page={n}", token, repo))

    def kept() -> dict[str, Any]:
        return {} if known is None else {"beyond": sorted(known)}

    rows = page_of(1)  # a repository Tico can't see is Blocked at once
    take(rows)
    page = 1
    try:
        while len(rows) >= PER_PAGE and page * PER_PAGE < RELEASES_LISTED:
            page += 1
            rows = page_of(page)
            take(rows)
        if len(rows) < PER_PAGE:  # the list ended before GitHub's limit: every release was listed
            return Listing(list(found.values()), None, {})
        refs = _list(_get(f"/repos/{repo}/git/matching-refs/tags", token, repo))
        beyond = {_tag_key(tag): tag for ref in refs if (tag := str(_map(ref).get("ref") or "")[10:]) not in listed}
        if not since:  # the first read: the tags past the list are what was there before
            known = set(beyond)
            return Listing([], None, kept())
        known = (known or set()) & set(beyond)  # a tag that came back into the list is dropped
        lookups = 0
        for key, tag in sorted(beyond.items(), key=lambda kv: kv[1]):
            if key in known:
                continue
            if lookups >= TAG_LOOKUPS:  # the rest wait for the next read
                break
            lookups += 1
            path = f"/repos/{repo}/releases/tags/{quote(tag, safe='')}"
            status, headers, body = (FETCH or _fetch)(path, token)
            if status == 200:
                take([body])
            elif status != 404:  # a tag with no release is 404; anything else is GitHub refusing
                _answer(status, headers, body, repo)
            known.add(key)
    except Refused as refused:  # what was found is kept: `since` stays, so the next read looks again
        return Listing(list(found.values()), None, kept(), (refused.status, refused.error))
    return Listing(list(found.values()), None, kept())


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
    carry = _map(cursor.get("carry")) if same else {}
    try:
        listing = _prs(repo, token, since, carry) if follow == "prs" else _releases(repo, token, since, carry)
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
    if follow == "releases":  # `since` stays the time the source started (`_releases`)
        keep: dict[str, Any] = {
            "follow": follow,
            "since": since or now,
            **({"carry": listing.carry} if listing.carry else {}),
        }
        items = [] if not since else sorted(listing.items, key=lambda item: item["posted_at"] or "")
        status, error = listing.refused or ("ok", "")
        return {"status": status, "items": items, "snapshot": json.dumps(keep), "error": error}
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
    return {"status": "ok", "items": items, "snapshot": json.dumps(keep), "error": ""}
