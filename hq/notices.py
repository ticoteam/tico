"""What HQ tells installs beside the latest release, and the fixed-in notice a request gets when a release ships its fix.

    GET    /v1/staff/notices                   the per-version messages                      (HQ_STAFF_KEY)
    POST   /v1/staff/notices                   {versions, body, url?}: a message for installs on those versions
    DELETE /v1/staff/notices/{id}              stop showing it
    GET    /v1/staff/known-issues              the known issues                               (HQ_STAFF_KEY)
    POST   /v1/staff/known-issues              {title, since, url?, ref?, fixed_in?}: an issue in `since` and later
    POST   /v1/staff/known-issues/{id}         {fixed_in}: the release that fixes it
    DELETE /v1/staff/known-issues/{id}         remove one
    POST   /v1/staff/tickets/{id}/fix          {ref}: the GitHub issue or pull request (#123) that will fix a request
    POST   /v1/staff/releases                  {tag, refs?}: a release shipped; post "Fixed in" on every request linked to
        one of `refs`, or to a reference in its commits when `refs` is left out

`GET /v1/latest` carries the messages and known issues (`notices`, `known_issues`), so an install learns them with the
update check it already makes: nothing new is sent or collected. Every install gets the same lists and picks its own
version's entries. A release before the one that shows `notices` keeps only the release fields, so for an install that
names its version on a release that predates them, the release link (`html_url`) points at its message's `url`
instead: the one thing its "New version" popover opens.

HQ watches the latest release itself: when a new tag appears it reads the commits since the tag it last saw, collects
every `#123` they name (a merge commit names its pull request, a commit can name the issue it fixes), and posts the
fixed-in reply on every open or answered request linked to one of them. The Support bot links a request to its fix
(`hq-tickets link`). A request gets the notice once, whichever way it arrives.
"""
import re
import threading
from contextlib import contextmanager

import httpx
from fastapi import Request

from .support import CONTROL, MAX_STAFF_REQUEST, TICKET_ID, VERSION, Invalid, read_json, refuse, no_store, stamp, \
    text, utcnow

COMPARE_URL = "https://api.github.com/repos/ticoteam/tico/compare/{base}...{head}"
MAX_NOTICE = 280
MAX_TITLE = 200
MAX_LISTED = 50
REF = re.compile(r"#?([0-9]{1,7})")
# What a commit says it fixes: the pull request a merge commit names, a squash merge's trailing "(#123)" on its subject,
# and "fixes/closes/resolves #123" anywhere in it. A bare "#123" ("part 1 of #415", "refs #700") is not a fix.
MERGED = re.compile(r"^Merge pull request #([0-9]{1,7})\b")
SQUASHED = re.compile(r"\(#([0-9]{1,7})\)\s*$")
FIXES = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+#([0-9]{1,7})\b", re.I)
PER_PAGE = 100
MAX_PAGES = 30             # 3000 commits between two releases; more than that is read as GitHub not answering
URL = re.compile(r"https://[^\s<>\"']{1,490}")
# A message's versions: a release ("0.3.50") or a whole minor line ("0.2"). Matched the same way on both sides.
VERSIONS = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}(?:\.[0-9]{1,4})?")
TAG = re.compile(r"v?" + VERSION.pattern)
# The first release that shows a notice in the app. An install that says it runs something older is given the
# message's link in place of the release link.
SHOWS_NOTICES = (0, 3, 52)

SCHEMA = """
CREATE TABLE IF NOT EXISTS version_notices(
 id INTEGER PRIMARY KEY AUTOINCREMENT, versions TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS known_issues(
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, since TEXT NOT NULL, fixed_in TEXT NOT NULL DEFAULT '',
 url TEXT NOT NULL DEFAULT '', ref INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticket_fixes(
 ticket_id TEXT NOT NULL, ref INTEGER NOT NULL, linked TEXT NOT NULL, fixed_in TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(ticket_id, ref));
CREATE TABLE IF NOT EXISTS release_sweeps(tag TEXT PRIMARY KEY, swept TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shipped_refs(ref INTEGER PRIMARY KEY, tag TEXT NOT NULL, url TEXT NOT NULL, shipped TEXT NOT NULL);
"""


def triple(version):
    """(major, minor, patch) of a version or tag, ignoring a prerelease; None when it is not one."""
    found = re.match(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", str(version or ""))
    return tuple(int(p) for p in found.groups()) if found else None


def covers(versions, version):
    """Whether a message for `versions` ("0.2" or "0.3.50") is for an install on `version`."""
    have, want = triple(version), [int(p) for p in str(versions).split(".")]
    return bool(have) and list(have[:len(want)]) == want


def affected(issue, version):
    """Whether a known issue is in `version`: from `since`, until the release that fixed it."""
    have, since, fixed = triple(version), triple(issue["since"]), triple(issue.get("fixed_in"))
    return bool(have and since) and since <= have and not (fixed and have >= fixed)


def fixed_refs(message):
    """The issue and pull request numbers one commit message says it fixes. A revert fixes nothing."""
    message = str(message or "")
    subject = message.split("\n", 1)[0].strip()
    if subject.lower().startswith("revert"):
        return set()
    found = {int(n) for n in FIXES.findall(message)}
    for pattern in (MERGED, SQUASHED):
        match = pattern.search(subject)
        if match:
            found.add(int(match.group(1)))
    return found


def fixed_text(tag, url):
    return f"Fixed in {tag}, update here: {url}"


def _url(value, field):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not URL.fullmatch(value) or CONTROL.search(value):
        raise Invalid(field)
    return value


def _ref(value):
    found = REF.fullmatch(str(value).strip()) if isinstance(value, (str, int)) and not isinstance(value, bool) else None
    if not found or int(found.group(1)) == 0:
        raise Invalid("ref")
    return int(found.group(1))


def _version(value, field, pattern=VERSION):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Invalid(field)
    return value


class Notices:
    """The three tables. Shares the installs database's connection and lock."""

    def __init__(self, db, now=utcnow):
        self.db, self.now = db, now
        with db.lock:
            db.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ what installs see
    def public(self):
        """The lists `GET /v1/latest` carries: every message and every known issue, newest first, each capped."""
        with self.db.lock:
            notices = self.db.conn.execute("SELECT id, versions, body, url FROM version_notices ORDER BY id DESC LIMIT ?",
                                           (MAX_LISTED,)).fetchall()
            issues = self.db.conn.execute("SELECT id, title, since, fixed_in, url FROM known_issues ORDER BY id DESC "
                                          "LIMIT ?", (MAX_LISTED,)).fetchall()
        return {"notices": [dict(r) for r in notices], "known_issues": [dict(r) for r in issues]}

    def release_for(self, release, version):
        """The release answer for an install on `version`. One on a release that cannot show a notice gets its
        message's link as the release link, the one place its app shows a link from HQ."""
        out = {**release, **self.public()}
        have = triple(version)
        if have and have < SHOWS_NOTICES:
            for notice in out["notices"]:
                if notice["url"] and covers(notice["versions"], version):
                    out["html_url"] = notice["url"]
                    break
        return out

    # ------------------------------------------------------------------ staff
    def add_notice(self, data):
        if not isinstance(data, dict) or not {"versions", "body"} <= set(data) or set(data) - {"versions", "body", "url"}:
            raise Invalid("fields")
        versions = _version(data["versions"], "versions", VERSIONS)
        body, url = text(data["body"], "body", MAX_NOTICE), _url(data.get("url"), "url")
        with self.db.lock:
            new = self.db.conn.execute("INSERT INTO version_notices(versions,body,url,created) VALUES(?,?,?,?)",
                                       (versions, body, url, stamp(self.now()))).lastrowid
        return {"id": new, "versions": versions, "body": body, "url": url}

    def add_issue(self, data):
        allowed = {"title", "since", "url", "ref", "fixed_in"}
        if not isinstance(data, dict) or not {"title", "since"} <= set(data) or set(data) - allowed:
            raise Invalid("fields")
        title, since = text(data["title"], "title", MAX_TITLE), _version(data["since"], "since")
        url = _url(data.get("url"), "url")
        ref = _ref(data["ref"]) if data.get("ref") not in (None, "") else None
        fixed = _version(data["fixed_in"], "fixed_in", TAG).lstrip("v") if data.get("fixed_in") else ""
        moment = stamp(self.now())
        with self.db.lock:
            new = self.db.conn.execute("INSERT INTO known_issues(title,since,fixed_in,url,ref,created,updated) "
                                       "VALUES(?,?,?,?,?,?,?)", (title, since, fixed, url, ref, moment, moment)).lastrowid
        return {"id": new, "title": title, "since": since, "fixed_in": fixed, "url": url, "ref": ref}

    def fix_issue(self, issue_id, data):
        if not isinstance(data, dict) or set(data) != {"fixed_in"}:
            raise Invalid("fields")
        fixed = _version(data["fixed_in"], "fixed_in", TAG).lstrip("v")
        with self.db.lock:
            done = self.db.conn.execute("UPDATE known_issues SET fixed_in=?, updated=? WHERE id=?",
                                        (fixed, stamp(self.now()), issue_id)).rowcount
        return {"id": issue_id, "fixed_in": fixed} if done else None

    def remove(self, table, row_id):
        with self.db.lock:
            return bool(self.db.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,)).rowcount)

    def staff_lists(self):
        with self.db.lock:
            notices = self.db.conn.execute("SELECT * FROM version_notices ORDER BY id DESC").fetchall()
            issues = self.db.conn.execute("SELECT * FROM known_issues ORDER BY id DESC").fetchall()
        return [dict(r) for r in notices], [dict(r) for r in issues]

    def link(self, ticket_id, ref):
        """Link a request to the issue or pull request that will fix it. None for an unknown request."""
        with self.db.lock:
            if not self.db.conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                return None
            self.db.conn.execute("INSERT OR IGNORE INTO ticket_fixes(ticket_id,ref,linked) VALUES(?,?,?)",
                                 (ticket_id, ref, stamp(self.now())))
            refs = [r[0] for r in self.db.conn.execute("SELECT ref FROM ticket_fixes WHERE ticket_id=? ORDER BY ref",
                                                       (ticket_id,))]
        return {"ticket_id": ticket_id, "refs": refs}

    def shipped(self, tag, url, refs):
        """A release shipped `refs`: the fixed-in reply on each open or answered request linked to one, once per
        request, and each known issue linked to one marked fixed in it. Running it again posts nothing new. A request
        held as spam gets none; if a person releases it later, `catch_up` posts it then."""
        refs, version = sorted(set(refs)), tag.lstrip("v")
        posted, issues = [], []
        if not refs:
            return {"tag": tag, "notified": posted, "known_issues_fixed": issues}
        marks, moment = ",".join("?" * len(refs)), stamp(self.now())
        with self.db.lock, self._transaction():
            self.db.conn.executemany("INSERT OR IGNORE INTO shipped_refs(ref,tag,url,shipped) VALUES(?,?,?,?)",
                                     [(ref, tag, url, moment) for ref in refs])
            rows = self.db.conn.execute(
                f"SELECT DISTINCT t.* FROM ticket_fixes f JOIN tickets t ON t.ticket_id=f.ticket_id WHERE f.ref IN ({marks}) "
                "AND f.fixed_in='' AND t.held=0", refs).fetchall()
            for row in rows:
                done = self._notify(row, tag, url, refs, moment)
                if done:
                    posted.append(done)
            for row in self.db.conn.execute(f"SELECT id FROM known_issues WHERE ref IN ({marks}) AND fixed_in=''",
                                            refs).fetchall():
                self.db.conn.execute("UPDATE known_issues SET fixed_in=?, updated=? WHERE id=?", (version, moment, row["id"]))
                issues.append(row["id"])
        return {"tag": tag, "notified": posted, "known_issues_fixed": issues}

    def catch_up(self, ticket_id):
        """A request a person released from the spam hold: if a release already shipped a fix it is linked to, the
        fixed-in reply now, once (the first release that shipped one). Returns the notice posted, or None."""
        moment = stamp(self.now())
        with self.db.lock, self._transaction():
            row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=? AND held=0", (ticket_id,)).fetchone()
            first = self.db.conn.execute(
                "SELECT s.tag, s.url FROM ticket_fixes f JOIN shipped_refs s ON s.ref=f.ref WHERE f.ticket_id=? "
                "AND f.fixed_in='' ORDER BY s.shipped, s.tag LIMIT 1", (ticket_id,)).fetchone()
            if not row or not first:
                return None
            refs = [r[0] for r in self.db.conn.execute(
                "SELECT f.ref FROM ticket_fixes f JOIN shipped_refs s ON s.ref=f.ref WHERE f.ticket_id=? AND f.fixed_in=''",
                (ticket_id,))]
            return self._notify(row, first["tag"], first["url"], refs, moment)

    def _notify(self, row, tag, url, refs, moment):
        """One request's fixed-in reply, unless it is closed or already has it; its links to `refs` are marked either
        way. Caller holds the lock, in a transaction."""
        marks, body = ",".join("?" * len(refs)), fixed_text(tag, url)
        self.db.conn.execute(f"UPDATE ticket_fixes SET fixed_in=? WHERE ticket_id=? AND ref IN ({marks})",
                             (tag.lstrip("v"), row["ticket_id"], *refs))
        if row["status"] == "closed" or self.db.conn.execute(
                "SELECT 1 FROM ticket_replies WHERE ticket_id=? AND author='staff' AND body=?", (row["ticket_id"], body)).fetchone():
            return None
        reply = self.db.conn.execute("INSERT INTO ticket_replies(ticket_id,created,body,author) VALUES(?,?,?,'staff')",
                                     (row["ticket_id"], moment, body)).lastrowid
        self.db.conn.execute("UPDATE tickets SET status='answered', updated=?, email_pending=? WHERE ticket_id=?",
                             (moment, 1 if row["email"] else row["email_pending"], row["ticket_id"]))
        return {"ticket_id": row["ticket_id"], "reply_id": reply, "email_pending": bool(row["email"] or row["email_pending"])}

    @contextmanager
    def _transaction(self):
        """All of a release's notices and marks, or none of them. Caller holds the lock."""
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.conn.execute("ROLLBACK")
            raise
        self.db.conn.execute("COMMIT")

    # ------------------------------------------------------------------ the release watch
    def last_swept(self):
        with self.db.lock:
            row = self.db.conn.execute("SELECT tag FROM release_sweeps ORDER BY swept DESC, rowid DESC LIMIT 1").fetchone()
        return row["tag"] if row else ""

    def mark_swept(self, tag):
        with self.db.lock:
            self.db.conn.execute("INSERT OR IGNORE INTO release_sweeps(tag,swept) VALUES(?,?)", (tag, stamp(self.now())))


class Watch:
    """Posts the fixed-in notices when the latest release changes. The first tag HQ sees is only remembered: there is
    nothing to compare it with."""

    def __init__(self, notices, latest, compare_url=COMPARE_URL, token="", transport=None):
        self.notices, self.latest, self.compare_url, self.transport = notices, latest, compare_url, transport
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "tico-hq"}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        self.lock = threading.Lock()

    def refs_between(self, base, head):
        """Every issue or pull request the commits from `base` to `head` say they fix (fixed_refs), or None when GitHub
        did not give all of them: the compare API pages its commits, so each page is read until `total_commits`."""
        commits, total = [], None
        try:
            with httpx.Client(timeout=10, transport=self.transport) as http:
                for page in range(1, MAX_PAGES + 1):
                    r = http.get(self.compare_url.format(base=base, head=head), headers=self.headers,
                                 params={"per_page": PER_PAGE, "page": page})
                    body = r.json() if r.status_code == 200 else None
                    if not isinstance(body, dict) or not isinstance(body.get("commits"), list):
                        return None
                    if total is None:
                        total = body.get("total_commits")
                    commits += body["commits"]
                    if not body["commits"] or not isinstance(total, int) or len(commits) >= total:
                        break
        except (httpx.HTTPError, ValueError):
            return None
        if isinstance(total, int) and len(commits) < total:
            return None                         # cut short: asked again on the next look rather than half-swept
        found = set()
        for commit in commits:
            found |= fixed_refs(((commit or {}).get("commit") or {}).get("message") or "")
        return found

    def check(self):
        """One look: a new latest tag gets its sweep. Returns the sweep's result, or None when there was nothing to do."""
        with self.lock:
            release = self.latest.get()
            tag = str((release or {}).get("tag_name") or "")
            if not TAG.fullmatch(tag):
                return None
            last = self.notices.last_swept()
            if last == tag:
                return None
            if not last:
                self.notices.mark_swept(tag)
                return None
            refs = self.refs_between(last, tag)
            if refs is None:
                return None                     # asked again on the next look
            done = self.notices.shipped(tag, release.get("html_url") or "", refs)
            self.notices.mark_swept(tag)
            return done


# ---------------------------------------------------------------------- the routes
def install(app, notices, staff_only, watch):
    """Add the staff routes. `staff_only(request)` is support.py's key check; `watch` reads a release's commits."""

    async def body_of(request):
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        return (None, refuse(data, status)) if status != 200 else (data, None)

    @app.get("/v1/staff/notices")
    def staff_notices(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        listed, _ = notices.staff_lists()
        return no_store({"notices": listed})

    @app.post("/v1/staff/notices")
    async def staff_add_notice(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        data, bad = await body_of(request)
        if bad:
            return bad
        try:
            return no_store(notices.add_notice(data), 201)
        except Invalid as wrong:
            return refuse("invalid", 422, field=wrong.field)

    @app.delete("/v1/staff/notices/{row_id}")
    def staff_remove_notice(request: Request, row_id: int):
        denied = staff_only(request)
        if denied:
            return denied
        return no_store({"deleted": True}) if notices.remove("version_notices", row_id) else refuse("not_found", 404)

    @app.get("/v1/staff/known-issues")
    def staff_issues(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        _, listed = notices.staff_lists()
        return no_store({"known_issues": listed})

    @app.post("/v1/staff/known-issues")
    async def staff_add_issue(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        data, bad = await body_of(request)
        if bad:
            return bad
        try:
            return no_store(notices.add_issue(data), 201)
        except Invalid as wrong:
            return refuse("invalid", 422, field=wrong.field)

    @app.post("/v1/staff/known-issues/{row_id}")
    async def staff_fix_issue(request: Request, row_id: int):
        denied = staff_only(request)
        if denied:
            return denied
        data, bad = await body_of(request)
        if bad:
            return bad
        try:
            done = notices.fix_issue(row_id, data)
        except Invalid as wrong:
            return refuse("invalid", 422, field=wrong.field)
        return no_store(done) if done else refuse("not_found", 404)

    @app.delete("/v1/staff/known-issues/{row_id}")
    def staff_remove_issue(request: Request, row_id: int):
        denied = staff_only(request)
        if denied:
            return denied
        return no_store({"deleted": True}) if notices.remove("known_issues", row_id) else refuse("not_found", 404)

    @app.post("/v1/staff/tickets/{ticket_id}/fix")
    async def staff_link_fix(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        data, bad = await body_of(request)
        if bad:
            return bad
        try:
            if not isinstance(data, dict) or set(data) != {"ref"}:
                raise Invalid("fields")
            ref = _ref(data["ref"])
        except Invalid as wrong:
            return refuse("invalid", 422, field=wrong.field)
        done = notices.link(ticket_id, ref) if TICKET_ID.fullmatch(ticket_id) else None
        return no_store(done) if done else refuse("not_found", 404)

    @app.post("/v1/staff/releases")
    async def staff_release(request: Request):
        """A release shipped: the fixed-in notices for `refs`, or for every reference in its commits since the last
        release HQ saw when `refs` is left out. What the watch does by itself, for a release it has not seen yet."""
        denied = staff_only(request)
        if denied:
            return denied
        data, bad = await body_of(request)
        if bad:
            return bad
        try:
            if not isinstance(data, dict) or "tag" not in data or set(data) - {"tag", "refs", "url"}:
                raise Invalid("fields")
            tag = _version(data["tag"], "tag", TAG)
            tag = tag if tag.startswith("v") else "v" + tag
            url = _url(data.get("url"), "url") or f"https://github.com/ticoteam/tico/releases/tag/{tag}"
            refs = data.get("refs")
            if refs is not None and (not isinstance(refs, list) or len(refs) > 500):
                raise Invalid("refs")
            refs = [_ref(r) for r in refs] if refs is not None else None
        except Invalid as wrong:
            return refuse("invalid", 422, field=wrong.field)
        from_commits = refs is None
        if from_commits:
            base = notices.last_swept()
            refs = watch.refs_between(base, tag) if base and base != tag else set()
            if refs is None:
                return refuse("github_unavailable", 502)
        done = notices.shipped(tag, url, refs)
        if from_commits:                        # named refs leave the release's other commits to the watch
            notices.mark_swept(tag)
        return no_store(done)

