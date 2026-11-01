"""The Librarian's first-run setup of Docs and Market (docs/librarian.md#first-run-setup).

The Librarian is built in and active on day one, and still has two guided first runs:

- **Docs.** On a first Docs visit, while Docs holds nothing a person wrote or linked, it asks where the team's docs
  are. The addresses become linked sources at once (a public GitHub repository is indexed, as with Add source), and
  one task, "Set up Docs", has the Librarian read them and build the map of the docs (`playbooks/docs-setup.md`).
  "Skip for now" is each person's own choice; Set up with the Librarian in the Docs menu brings it back.
- **Market.** On a first Market visit the owner answers four questions, one at a time: what you sell, who buys it,
  who you compete with, what to watch. Each answer is kept as it is given, so the owner can skip and pick up where
  they left off, from any browser. The last one files "Set up the market map" (`playbooks/market-setup.md`), which
  builds the map from the answers and writes what Market tracks.

What a person chose is a row in `preferences`, theirs alone (the same store as `/api/v2/preferences/{key}`). The
market answers are the owner's, since only the owner and the Librarian change the market.
"""

import json
from typing import Annotated

from fastapi import Request
from pydantic import Field

from . import getting_started as GS
from . import librarian as L
from . import models as M
from .store import H, Problem, encode

DOCS_KEY = "librarian.docs_setup"
MARKET_KEY = "librarian.market_setup"
DOCS_TASK = "Set up Docs"
DOCS_PLAYBOOK = "playbooks/docs-setup.md"
MAX_SOURCES = 20
# The four market questions, in the order the Librarian asks them. Only the first must be answered.
QUESTIONS = ("sell", "buyers", "competitors", "watch")
HEADINGS = {"sell": "What we sell", "buyers": "Who buys it", "competitors": "Who we compete with",
            "watch": "What to watch"}
# The Librarian's own map and FAQ are not a person's docs: Docs is still empty with only those.
OWN_DOCS = "path NOT LIKE '\\_librarian/%' ESCAPE '\\' AND path != 'FAQ.md'"


class DocsSetup(M.Contract):
    sources: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=MAX_SOURCES)
    note: str = Field(default="", max_length=4000)
    skip: bool = False


class MarketAnswers(M.Contract):
    sell: str = Field(default="", max_length=4000)
    buyers: str = Field(default="", max_length=4000)
    competitors: str = Field(default="", max_length=4000)
    watch: str = Field(default="", max_length=4000)


class MarketSetup(M.Contract):
    answers: MarketAnswers = Field(default_factory=MarketAnswers)
    step: int = Field(default=0, ge=0, le=len(QUESTIONS) - 1)
    action: str = Field(default="save", pattern="^(save|skip|finish)$")


def _load(c, actor, key):
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (actor, key)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(c, actor, key, value):
    c.execute("INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
              "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (actor, key, encode(value), H.now()))


def _task(c, task_id, requester=None):
    """A setup task's id and status: only ever a task to the Librarian, and the person's own when one is named."""
    if not isinstance(task_id, str) or not task_id:
        return None
    row = c.execute("SELECT id,status,requester,created_at FROM tasks WHERE id=? AND owner=?",
                    (task_id, "bot:" + L.LIBRARIAN)).fetchone()
    if not row or requester and row["requester"] != requester:
        return None
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"]}


def _docs_task(c):
    """The newest "Set up Docs" task anyone filed: once there is one, Docs is past its first visit."""
    row = c.execute("SELECT id FROM tasks WHERE owner=? AND (title=? OR title LIKE ?) ORDER BY created_at DESC LIMIT 1",
                    ("bot:" + L.LIBRARIAN, DOCS_TASK, DOCS_TASK + " (%)")).fetchone()
    return _task(c, row["id"]) if row else None


def docs_empty(c):
    """Nothing a person wrote or linked: no live source and no internal doc besides the Librarian's own."""
    if c.execute("SELECT 1 FROM linked_docs WHERE archived=0 LIMIT 1").fetchone():
        return False
    return not c.execute(f"SELECT 1 FROM docs WHERE archived=0 AND {OWN_DOCS} LIMIT 1").fetchone()


def _answers(stored):
    raw = stored.get("answers") if isinstance(stored.get("answers"), dict) else {}
    return {key: str(raw.get(key) or "") for key in QUESTIONS}


def view(c, who):
    available = L.availability(c, who)["available"]
    docs = _load(c, who.actor, DOCS_KEY)
    task = _docs_task(c)
    empty = docs_empty(c)
    owner = who.role == "owner"
    market = _load(c, who.actor, MARKET_KEY) if owner else {}
    step = market.get("step") if isinstance(market.get("step"), int) else 0
    return {
        "available": available,
        "docs": {"show": available and empty and not task and not docs.get("skipped"),
                 "empty": empty, "skipped": bool(docs.get("skipped")), "task": task},
        "market": {"can_answer": owner, "answers": _answers(market),
                   "step": max(0, min(step, len(QUESTIONS) - 1)), "skipped": bool(market.get("skipped")),
                   "task": _task(c, market.get("task_id"), who.actor) if owner else None},
    }


def docs_text(sources, note):
    lines = [f"Set up our Docs from the places a person said our docs are. Follow {DOCS_PLAYBOOK}.", ""]
    if sources:
        lines += ["They are linked sources in Docs now; a public GitHub repository is being indexed:", ""]
        lines += [f"- {row['title']}: {row['url']} (source {row['id']})" for row in sources]
        lines.append("")
    if note:
        lines += ["What they said about where the docs are:", "", note, ""]
    lines += ["Read each source the way `playbooks/refresh-the-map.md` walks a linked doc, then build the map "
              "of the docs (`_librarian/index.md`, `_librarian/where-things-live.md`, `_librarian/glossary.md`). "
              "A place you cannot read (a login, a private drive) goes in `_librarian/missing.md` with what a person "
              "can do: import the files, or share a public link. Finish with a note that says what you read and "
              "what you could not."]
    return "\n".join(lines)


def setup_docs(c, auth, who, body):
    state = _load(c, who.actor, DOCS_KEY)
    if body.skip:
        state["skipped"] = True
        _save(c, who.actor, DOCS_KEY, state)
        return view(c, who)
    if not L.availability(c, who)["available"]:
        raise Problem("librarian_off", "The " + L.NAME + " is off", 409)
    note = body.note.strip()
    urls = list(dict.fromkeys(url.strip() for url in body.sources if url.strip()))
    if not urls and not note:
        raise Problem("validation", "Add a link to your docs, or say where they are", 422)
    from .docs import LinkCreate, add_link
    sources = []
    for url in urls:
        try:
            linked = add_link(c, who.actor, LinkCreate(url=url))
        except Problem as exc:
            if exc.code == "validation":
                raise Problem("validation", f"{url} is not a web address: use one that starts with https://", 422)
            if exc.code != "already_linked":
                raise
            linked = exc.extra["linked"]
        sources.append({"id": linked["id"], "url": linked["url"], "title": linked["title"], "kind": linked["kind"]})
    title = L.free_title(c, who.actor, DOCS_TASK)
    from . import rooms
    task = H.task_create(c, who.actor, title, docs_text(sources, note), "bot:" + L.LIBRARIAN, allow_planned=True,
                         labels=["docs-setup"],
                         conversation_id=rooms.task_conversation_id(c, auth, "bot:" + L.LIBRARIAN, who.actor))
    _save(c, who.actor, DOCS_KEY, {"skipped": False, "task_id": task["id"]})
    H.event(c, who.actor, "librarian.docs_setup_requested", task["id"], {"sources": len(sources)})
    return {"task_id": task["id"], "bot": L.LIBRARIAN, "sources": sources}


def market_text(answers):
    lines = ["Build our market map from the owner's answers, and set what Market tracks from them. "
             "Follow playbooks/market-setup.md.", "",
             "The owner answered the Librarian's four market questions on the Market page. "
             "A blank answer means: find it yourself.", ""]
    for key in QUESTIONS:
        lines += ["## " + HEADINGS[key], "", answers[key] or "(blank)", ""]
    lines += ["What Market tracks: start the Coverage universe page with a section \"What Market tracks\": the "
              "competitors (core unless a source shows otherwise), the buyer segments, and the channels, topics, "
              "rules and people to watch, each with its entity id once it is in the graph. The daily curate "
              "routine keeps these current.", "",
              "After the map, follow playbooks/size-the-market.md."]
    return "\n".join(lines)


def setup_market(c, auth, who, body):
    GS._owner(who, "sets up the market")
    answers = {key: getattr(body.answers, key).strip() for key in QUESTIONS}
    state = {**_load(c, who.actor, MARKET_KEY), "answers": answers, "step": body.step,
             "skipped": body.action == "skip"}
    if body.action == "finish":
        if not answers["sell"]:
            raise Problem("validation", "Say what you sell, or give your website", 422)
        if not GS._active(c, GS.LIBRARIAN):
            raise Problem("librarian", "The Librarian is not running yet. It starts once a computer hosts it.", 409)
        task = GS._task(c, auth, who, GS.LIBRARIAN, GS._market_title(c, "bot:" + GS.LIBRARIAN, who.actor),
                        market_text(answers))
        state["task_id"] = task["id"]
        H.event(c, who.actor, "getting_started.market_requested", GS.LIBRARIAN,
                {"task": task["id"], "questions": True})
    _save(c, who.actor, MARKET_KEY, state)
    return view(c, who)["market"]


def install(app, store, auth, mutate):
    @app.get("/api/v2/librarian/setup")
    def read(request: Request):
        who = L.person(request)
        auth.domain(who)
        with store.read() as c:
            return view(c, who)

    @app.post("/api/v2/librarian/setup/docs")
    def docs(request: Request, body: DocsSetup):
        who = L.person(request)
        auth.domain(who)
        return mutate(request, body, lambda c: setup_docs(c, auth, who, body))

    @app.post("/api/v2/librarian/setup/market")
    def market(request: Request, body: MarketSetup):
        who = L.person(request)
        auth.domain(who)
        return mutate(request, body, lambda c: setup_market(c, auth, who, body))
