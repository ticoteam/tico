"""Setup's research job (docs/strategy.md#setup): `POST /api/v2/strategy/draft-from {url?, text?}` starts it and
`GET /api/v2/strategy/draft-from/{job}` (or `latest`) reads it. The owner's own click, like every strategy write.

With a website the server reads a few of its pages (the home page, then about, pricing and customers, from the home
page's own links or their usual addresses) through `clients/doc_fetch.py`, the same guard `hub doc fetch` uses: http
and https on the ordinary ports, every address the name resolves to public (no loopback, private, link-local or
metadata address), each redirect checked again, 5 MB and 20 seconds a page, at most PAGES pages and BUDGET_S seconds
in all, and no credential of the server's ever sent. Then one model call, with no runner: the writer the daily-update
drafts use (backend/updates.py `writer_for`: the Tico AI Gateway, else the team's own provider with a server key), with
the shared prompt in backend/strategy_prompt.py (Tico HQ's setup page asks the same). It answers a summary, the four
sections and three bets the site implies, each with what it rules out and a suggested wrong-if. It asks for no vision:
`found` keeps `vision: ""` and the draft's vision is left as it is.

What it writes, as the owner who started it, and never published:
- the summary, and each of the four sections, only where the draft's is empty; each section starts with
  the UNCONFIRMED line, which the owner deletes by keeping or editing the text;
- each bet as `proposed`, its wrong-if a sentence with a check date CHECK_DAYS out.

It writes against the draft's `revision` as it was when the job started: when the owner has written since (or started
a newer job), it gives up and reports what it found instead (`gave_up`, `found`), so it never overwrites the owner.
With no model it answers `no_model` at once, and setup shows the same fields as a plain form.

The latest KEEP jobs are kept in `registry_metadata` (`strategy.draft_from`), so a reload finds them; a job still
`running` STALE_S after it started (the server restarted under it) reads as `failed`. A job's states:
running (step reading|thinking) -> done | gave_up | failed; no_model at once.
"""
import ipaddress
import json
import logging
import re
import threading
import time
from datetime import date, timedelta
from urllib.parse import urljoin, urlsplit

from clients import doc_fetch as F
from . import hubdb as H
from . import models as M
from . import strategy as S
from . import strategy_prompt as P
from .store import Problem, encode

log = logging.getLogger("tico.strategy_setup")

KEY = "strategy.draft_from"
KEEP = 5                    # jobs kept: the latest is what setup reads
PAGES = 4                   # the home page and at most three more
PAGE_CHARS = 8_000          # of each page's text sent to the model
BUDGET_S = 45               # no new page is read after this many seconds
STALE_S = 600               # a job running this long was cut off by a restart
BETS = P.BETS
CHECK_DAYS = 90             # a suggested wrong-if says when to look again
SECTION_CHARS = 1_500       # a drafted section: a start, not the owner's page
WHY_CHARS = 600
TEXT_MAX = 4_000
STATES = ("running", "done", "gave_up", "failed", "no_model")
UNCONFIRMED = "_Unconfirmed: drafted from {source}._"
# The pages after the home page, in order, and the words their addresses carry.
WANTED = (("about", r"about|company|our-story|who-we-are"),
          ("pricing", r"pricing|plans|price"),
          ("customers", r"customers|case-stud|stories|testimonials"))
INSTRUCTIONS = P.INSTRUCTIONS

# Test seams: names and sockets for the fetch, and how the job is started.
RESOLVER = None
OPENER = None


def spawn(work):
    threading.Thread(target=work, name="strategy-draft-from", daemon=True).start()


def writer(c, settings, store):
    """The model a draft is written with, or None: the daily-update drafts' writer (backend/updates.py)."""
    from . import updates
    return updates.writer_for(c, settings, store)


# ----------------------------------------------------------------------------- addresses
def normalize_url(value):
    """A website as typed ("acme.example", "https://acme.example/") as an http(s) address, "" for none. Raises
    ValueError for anything that is not one."""
    text = "".join(str(value or "").split())
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        parts.port                      # "acme.example:abc" raises here
    except ValueError:
        parts, host = None, ""
    if not parts or parts.scheme.lower() not in ("http", "https") or "@" in parts.netloc \
            or ("." not in host and not _literal(host)):
        raise ValueError("a website is an http or https address, like https://acme.example")
    return text


def _literal(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def refuse_unsafe(url):
    """The refusals that need no DNS, answered at once: a scheme, port or user name doc_fetch never fetches, and a
    host that is this computer or an internal address. A name that resolves to one is refused by the fetch itself."""
    try:
        _, host, _ = F.Fetcher(env={}).check_url(url)
    except F.FetchError as exc:
        raise Problem("validation", exc.message, 422, extra={"reason": exc.code}) from None
    internal = host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
    if internal or (_literal(host) and not F.public_address(host.strip("[]"))):
        raise Problem("validation", "That address is this computer or an internal one; give the team's public website",
                      422, extra={"reason": "private_address"})


def source_of(url):
    host = (urlsplit(url).hostname or "") if url else ""
    return host.removeprefix("www.") or "your description"


def unconfirmed(text, source):
    return UNCONFIRMED.format(source=source) + "\n\n" + text


# ----------------------------------------------------------------------------- the jobs
def _jobs(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    found = S.loads(row[0], {}) if row else {}
    return [j for j in found.get("jobs") or [] if isinstance(j, dict)]


def _put(c, job):
    """Save a job: a new one first, a known one where it is, so the first is always the newest."""
    jobs = _jobs(c)
    at = next((i for i, j in enumerate(jobs) if j["id"] == job["id"]), None)
    if at is None:
        jobs.insert(0, job)
    else:
        jobs[at] = job
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, encode({"jobs": jobs[:KEEP]})))
    return job


def _find(c, job_id):
    jobs = _jobs(c)
    if job_id == "latest":
        return jobs[0] if jobs else None
    return next((j for j in jobs if j["id"] == job_id), None)


INTERRUPTED = {"code": "interrupted", "message": "The research was cut off; start again"}


def _stale(job):
    return job["state"] == "running" and H.shift(job["started"], seconds=STALE_S) < H.now()


def view(job):
    """What the API answers: the job without its bookkeeping, a run cut off (a restart, a hung lookup) as `failed`."""
    out = {k: v for k, v in job.items() if k not in ("claimed", "actor")}
    if _stale(out):
        out.update(state="failed", step=None, error=INTERRUPTED)
    return out


def stop_running(c, error=INTERRUPTED):
    """Every job still `running` becomes `failed`: at start-up (backend/app.py lifespan; no thread of this process
    runs them), and when the owner clears the website and the line in setup (nothing is left to draft from)."""
    for job in _jobs(c):
        if job["state"] == "running":
            _put(c, {**job, "state": "failed", "step": None, "finished": H.now(), "error": error})


def read(c, job_id):
    job = _find(c, job_id)
    if not job:
        raise Problem("not_found", "No such strategy research job", 404)
    return view(job)


def start(c, who, settings, store, url="", text=""):
    """A new job, `running` (the caller spawns `run` once this commits), or `no_model` at once."""
    try:
        url = normalize_url(url)
    except ValueError as exc:
        raise Problem("validation", str(exc), 422, extra={"reason": "url"}) from None
    text = str(text or "").strip()[:TEXT_MAX]
    if not url and not text:
        raise Problem("validation", "Give the team's website, a line about it, or both", 422)
    if url:
        refuse_unsafe(url)
    model = writer(c, settings, store) is not None
    job = {"id": H.new_id(), "state": "running" if model else "no_model",
           "step": ("reading" if url else "thinking") if model else None, "url": url, "text": text,
           "actor": who.actor, "revision": S.draft(c)["revision"], "started": H.now(),
           "finished": None if model else H.now(), "pages": [], "found": None, "written": None, "error": None}
    _put(c, job)
    H.event(c, who.actor, "strategy.draft_from", job["id"], {"url": bool(url), "text": bool(text),
                                                            "state": job["state"]})
    return view(job)


def _update(store, job_id, **changes):
    with store.transaction() as c:
        job = _find(c, job_id)
        if job:
            _put(c, {**job, **changes})
        return job


def _fail(store, job_id, code, message, **more):
    _update(store, job_id, state="failed", step=None, finished=H.now(), error={"code": code, "message": message},
            **more)


def run(store, job_id):
    """The job itself, outside any transaction: read the pages, ask the model, write the draft. Runs once per job,
    whatever retries the POST had."""
    with store.transaction() as c:
        job = _find(c, job_id)
        if not job or job["state"] != "running" or job.get("claimed"):
            return None
        _put(c, {**job, "claimed": True})
        write = writer(c, store.settings, store)
    if write is None:
        _update(store, job_id, state="no_model", step=None, finished=H.now())
        return None
    pages = []
    try:
        if job["url"]:
            pages = read_site(job["url"])
            _update(store, job_id, step="thinking", pages=[_page_row(p) for p in pages])
            if not any(p.get("text") for p in pages) and not job["text"]:
                first = next((p for p in pages if p.get("error")), {}).get("error") or {}
                return _fail(store, job_id, first.get("code") or "unreadable",
                             first.get("message") or "Nothing could be read from that website")
        found = draft(write, job, pages)
    except Exception as exc:      # a model's answer or a page can be anything: the job says so and the form remains
        from clients import judge as J
        code = exc.code if isinstance(exc, J.JudgeError) else "error"
        log.info("Strategy research failed (%s)", type(exc).__name__)
        return _fail(store, job_id, code, "The model did not answer usably; fill the fields in yourself")
    if not (any(found[k] for k in P.FIELDS) or found["bets"]):
        return _fail(store, job_id, "empty", "Nothing usable came back; fill the fields in yourself", found=found)
    with store.transaction() as c:
        return finish(c, job_id, found)


# ----------------------------------------------------------------------------- reading the website
def _fetch(url):
    return F.fetch(url, PAGE_CHARS, env={}, resolver=RESOLVER or F.resolve, opener=OPENER or F.connect)


def _page_row(page):
    """What the job keeps of a page: never its text."""
    return {k: page[k] for k in ("url", "title", "error") if page.get(k) is not None}


def _same_site(a, b):
    host = lambda u: (urlsplit(u).hostname or "").lower().removeprefix("www.")
    return host(a) == host(b)


def _picks(home):
    """{kind: address} for the wanted pages the home page links to on its own site."""
    out = {}
    for kind, words in WANTED:
        pattern = re.compile(r"/(?:[\w-]+/)?(?:" + words + r")[\w-]*/?$", re.I)
        for link in home.get("links") or []:
            target = str(link.get("url") or "").split("#")[0]
            if target and _same_site(target, home["final_url"]) and pattern.search(urlsplit(target).path or ""):
                out[kind] = target
                break
    return out


def read_site(url, clock=time.monotonic):
    """The home page, then about, pricing and customers: [{url, title, text} or {url, error: {code, message}}]. A
    home page that cannot be read ends it: the rest of that site would be refused the same way."""
    deadline = clock() + BUDGET_S
    try:
        home = _fetch(url)
    except F.FetchError as exc:
        return [{"url": url, "error": {"code": exc.code, "message": exc.message}}]
    pages = [{"url": home["final_url"], "title": home["title"], "text": home["text"]}]
    picks = _picks(home)
    seen = {home["final_url"].rstrip("/")}
    for kind, _ in WANTED:
        target = picks.get(kind) or urljoin(home["final_url"], "/" + kind)
        if len(pages) >= PAGES or clock() > deadline or target.rstrip("/") in seen:
            continue
        seen.add(target.rstrip("/"))
        try:
            page = _fetch(target)
            pages.append({"url": page["final_url"], "title": page["title"], "text": page["text"]})
        except F.FetchError as exc:
            pages.append({"url": target, "error": {"code": exc.code, "message": exc.message}})
    return pages


# ----------------------------------------------------------------------------- the model
def _clip(value, limit, one_line=False):
    text = " ".join(str(value or "").split()) if one_line else str(value or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def draft(write, job, pages):
    """{summary, vision, serve, sell, know, not, bets: [{statement, why, rules_out, wrong_if}]} from one model call,
    clipped to what the draft takes. The prompt asks for no vision, so `vision` is always ""."""
    user = json.dumps({"owner_words": job["text"], "website": job["url"],
                       "pages": [{"url": p["url"], "title": p.get("title") or "", "text": p["text"]}
                                 for p in pages if p.get("text")]}, ensure_ascii=False)
    raw = write(INSTRUCTIONS, user)
    raw = raw if isinstance(raw, dict) else {}
    bets = []
    for item in raw.get("bets") if isinstance(raw.get("bets"), list) else []:
        if not isinstance(item, dict) or not _clip(item.get("statement"), S.STATEMENT_MAX, True):
            continue
        bets.append({"statement": _clip(item.get("statement"), S.STATEMENT_MAX, True),
                     "why": _clip(item.get("why"), WHY_CHARS), "rules_out": _clip(item.get("rules_out"), WHY_CHARS),
                     "wrong_if": _clip(item.get("wrong_if"), S.WRONG_TEXT_MAX, True)})
        if len(bets) == BETS:
            break
    return {"summary": _clip(raw.get("summary"), S.SUMMARY_MAX, True), "vision": "",
            **{k: _clip(raw.get(k), SECTION_CHARS) for k in S.FIXED_KEYS}, "bets": bets}


# ----------------------------------------------------------------------------- writing the draft
def finish(c, job_id, found):
    """Write what the model found onto the draft, unless the owner wrote it since the job started or a newer job
    exists: then `gave_up`, with `found` for the owner to take by hand."""
    job = _find(c, job_id)
    if not job or job["state"] != "running":
        return None
    if _stale(job):          # the owner was already told it stopped and given the form
        return _put(c, {**job, "state": "failed", "step": None, "finished": H.now(), "error": INTERRUPTED})
    row = S.draft(c)
    newest = _jobs(c)[0]["id"] == job_id
    if row["revision"] != job["revision"] or not newest:
        reason = "edited" if newest else "superseded"
        return _put(c, {**job, "state": "gave_up", "step": None, "finished": H.now(), "found": found,
                        "error": {"code": reason, "message": "The draft changed while this ran, so nothing was "
                                  "written; what it found is below" if newest else "A newer research job replaced "
                                  "this one"}})
    actor, source = job["actor"], source_of(job["url"])
    sections = {k: unconfirmed(found[k], source) for k in S.FIXED_KEYS
                if found.get(k) and not row["sections"][k].strip()}
    lines = {k: found[k] for k in ("summary", "vision") if found.get(k) and not row[k].strip()}
    if sections or lines:
        S.update_draft(c, actor, row["revision"], sections=sections or None, **lines)
    made = []
    live = [b for b in S.bets(c) if b["status"] != "retired"]
    known = {b["statement"].strip().lower() for b in live}
    check_by = (date.today() + timedelta(days=CHECK_DAYS)).isoformat()
    for item in found["bets"]:
        if item["statement"].lower() in known or len(live) + len(made) >= S.LIVE_MAX:
            continue
        wrong = {"kind": "text", "text": item["wrong_if"], "check_by": check_by} if item["wrong_if"] else None
        c.execute("SAVEPOINT draft_from_bet")
        try:
            bet, _ = S.add_bet(c, actor, item["statement"], item["why"], item["rules_out"], wrong, None, "proposed")
        except H.Refused:
            c.execute("ROLLBACK TO draft_from_bet")
            bet = None
        c.execute("RELEASE draft_from_bet")
        if bet:
            made.append(bet["id"])
            known.add(item["statement"].lower())
    return _put(c, {**job, "state": "done", "step": None, "finished": H.now(), "found": found,
                    "written": {"sections": sorted(sections), "fields": sorted(lines), "bets": made,
                                "revision": S.draft(c)["revision"]}})


# ----------------------------------------------------------------------------- HTTP
def install(app, store, auth, mutate):
    from fastapi import Request

    from . import goal_routes

    def owner(who):
        auth.domain(who)
        if not goal_routes.company_editor(who):
            raise Problem("forbidden", "Only the owner drafts the strategy from the team's website, on their own click",
                          403)

    @app.post("/api/v2/strategy/draft-from")
    def strategy_draft_from(request: Request, body: M.StrategyDraftFrom):
        """Owner only: research the team's website and/or the owner's words into the draft. Answers the job; poll
        GET /strategy/draft-from/{id}."""
        who = request.state.identity
        owner(who)
        job = mutate(request, body, lambda c: start(c, who, store.settings, store, body.url, body.text))
        if job.get("state") == "running":
            spawn(lambda: run(store, job["id"]))
        return job

    @app.get("/api/v2/strategy/draft-from/{job_id}")
    def strategy_draft_from_job(request: Request, job_id: str):
        """Owner only: one research job (`latest` for the newest): its state, the pages read, what it found and what
        it wrote."""
        owner(request.state.identity)
        with store.read() as c:
            return read(c, job_id)
