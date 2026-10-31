"""Counsel: the built-in helper that reads a document someone adds to Rules and suggests the obligations that bind us,
and answers "can I...?" from the rules in force (docs/rules-plan.md §2, §5).

- Adding a document (`POST /api/v2/rules/read`) reads it first, outside any write: a file's text, or a public link
  fetched with clients/doc_fetch.py with no credential (`env={}`), either held to DOC_CHARS characters. The text is
  screened for prompt injection (backend/screening.py, source `document`): at `warn` the task starts with the warning
  line; at `hold` Counsel is not given the text at all. Then the write saves a link's text as a Files version (so each
  cite points at a version that later changes never alter), records the version in `rule_reads`, and opens one private
  task for Counsel with the document fenced as untrusted data. A document cut at DOC_CHARS is marked truncated.
- Counsel has no credentials and sends nothing. It reads its task and the rules in force, and writes suggestions through
  `POST /api/v2/rules` as `suggested`. A cite is checked here: Counsel cites only the exact (file, version) it was asked
  to read, and the quote must appear in that version's normalized text. A quote that does not is dropped (the response
  says so, and `rules.quote_dropped` counts it), never stored. Only Counsel and a person cite; no other bot can.
- `POST /api/v2/rules/ask` answers "can I...?" with the active rules that share words with the question, at once, and
  opens a private Counsel task for the full answer. Every answer carries the one line that it is not legal advice.

Counsel is a helper (backend/assistant.py `HELPERS`): built in, off the org chart, seeded by onboarding like the
Librarian (templates/catalog/counsel). Only the owner can turn it on or off.
"""
import hashlib
import re
import secrets
import unicodedata

from fastapi import Request
from pydantic import Field

from clients import doc_fetch

from . import credentials as C
from . import file_library
from . import groups as G
from . import files as F
from . import hubdb as H
from . import org_rules as O
from . import people as P
from . import screening
from .auth import Identity
from .blobs import register
from .models import Contract
from .store import Problem

SLUG = "counsel"
NAME = "Counsel"
NOT_ADVICE = "Not legal advice. For real exposure, ask a lawyer."
QUESTION_MAX = 500
NOTE_MAX = 2000
# The most of a document Counsel reads: a link is fetched to this bound, a file is read to it. Longer text is cut here and
# the read says so (truncated), in the response, the task and the rule_reads row.
DOC_CHARS = 400_000
# A document's text goes into Counsel's task only up to this many characters (a run's prompt budget). A longer one is
# named by file and version, and Counsel reads it with `hub file read`.
TEXT_INLINE_MAX = 20_000
ASK_MATCHES = 8
STOP_WORDS = frozenset("the and for with that this from are can our your you any all not does into over under about "
                       "what when which will may must".split())
# Quote characters and dashes that differ in a copy of a document but say the same thing.
_STRAIGHT = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
                           "–": "-", "—": "-", " ": " "})

# The versions Counsel was asked to read: the only versions it may cite (backend/files.py asked_to_read).
RULE_READS = """CREATE TABLE IF NOT EXISTS rule_reads(
 file_id TEXT NOT NULL, version INTEGER NOT NULL, task_id TEXT, requested_by TEXT NOT NULL,
 created TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(file_id, version))"""


class RuleRead(Contract):
    file_id: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2000)
    source_id: str = Field(min_length=1, max_length=200)     # a From by id or name
    kind: str | None = Field(default=None, max_length=20)    # the kind, when this From is new
    note: str = Field(default="", max_length=NOTE_MAX)


class RuleAsk(Contract):
    question: str = Field(min_length=3, max_length=QUESTION_MAX)


def ensure(c):
    """The table of the document versions Counsel was asked to read. Unversioned, as the org-rules tables are."""
    c.execute(RULE_READS)


def available(c):
    row = H.bot(c, SLUG)
    return bool(row) and row["state"] == "active"


def normal(text):
    """The form a quote is matched in: Unicode-normalized, quote marks and dashes straightened, whitespace collapsed,
    case folded. The same for the source and the quote, so a copy that differs only in those still matches."""
    value = unicodedata.normalize("NFKC", str(text or "")).translate(_STRAIGHT)
    return re.sub(r"\s+", " ", value).strip().casefold()


def quote_in(source_text, quote):
    needle = normal(quote)
    return bool(needle) and needle in normal(source_text)


def fence(text):
    """The document between markers a document cannot predict: nothing inside it can close the fence early."""
    token = secrets.token_hex(8)
    return f"<<UNTRUSTED DOCUMENT {token}>>\n{text}\n<<END UNTRUSTED DOCUMENT {token}>>"


def document(c, blobs, file_id, number):
    """One version's text as Counsel reads it: the whole text up to DOC_CHARS, and whether it was cut. (None, False)
    when the version does not exist or is of a type Tico reads no text from."""
    row = c.execute("SELECT v.mime, b.digest FROM bot_file_versions v JOIN blobs b ON b.id=v.blob_id "
                    "WHERE v.file_id=? AND v.version=?", (file_id, number)).fetchone()
    if not row:
        return None, False
    data = blobs.get(row["digest"])
    text = file_library.extract(row["mime"], data, full=True)
    if not text:
        return None, False
    return text[:DOC_CHARS], len(text) > DOC_CHARS or len(data) > file_library.READ_MAX


def version_text(c, blobs, file_id, number):
    return document(c, blobs, file_id, number)[0]


def suggested_who(c, source_name):
    """The departments of the bots granted a credential whose name carries this From (backend/credentials.py). Empty
    lists when none is granted: Counsel then leaves Who to the owner."""
    name = str(source_name or "").strip().lower()
    if not name:
        return {"groups": [], "bots": []}
    ids = [r["id"] for r in c.execute("SELECT id FROM credentials WHERE instr(lower(name), ?) > 0", (name,))]
    if not ids:
        return {"groups": [], "bots": []}
    marks = ",".join("?" * len(ids))
    subjects = {r["subject"] for r in c.execute(
        f"SELECT DISTINCT subject FROM credential_grants WHERE credential_id IN ({marks}) AND revoked IS NULL "
        "AND subject LIKE 'bot:%'", ids)}
    ctx = O._ctx(c)
    bots, groups = [], set()
    for subject in sorted(subjects):
        slug = subject[len("bot:"):]
        if not any(C.effective_grant(c, cid, subject) for cid in ids):
            continue
        bots.append(slug)
        team = "" if slug in G.SYSTEM_BOTS else P.team_of(slug, ctx["configs"], ctx["roster"])
        if team:
            groups.add(team)
    return {"groups": sorted(groups), "bots": bots}


def _source(c, who, body):
    """The From a document is read for: an existing one by id or name, or a new one when `kind` is given."""
    found = c.execute("SELECT * FROM rule_sources WHERE id=? OR lower(name)=lower(?)",
                      (body.source_id, body.source_id)).fetchall()
    if len(found) > 1:
        raise Problem("validation", f"{body.source_id} is more than one From: use its id", 422)
    if found:
        return dict(found[0])
    if not body.kind:
        raise Problem("not_found", f"There is no From {body.source_id}; give its kind to add it", 404)
    return O.add_source(c, who, {"name": body.source_id, "kind": body.kind})


def _store_fetched(c, blobs, files, url, fetched):
    """The exact text a link was fetched as, saved as a Files version of Counsel's (the cite's target)."""
    data = fetched["text"].encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    blobs.put(data, "text/plain; charset=utf-8")
    blob = register(c, Identity("bot:" + SLUG, "bot"), digest, len(data), "document.txt", "text/plain")
    identity = "rule-doc:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    title = (fetched.get("title") or url)[:300]
    row, _, _ = files.add_version(c, bot=SLUG, actor="bot:" + SLUG, scope="bot", task=None, conversation=None,
                                  attempt="", identity=identity, title=title, name="document.txt",
                                  mime="text/plain", blob_id=blob["id"], digest=digest, size=len(data),
                                  source_digest=digest)
    file_library.index(c, row["id"], row["current_version"], row["title"], fetched["text"])
    return row["id"], row["current_version"], row["title"]


def _task(c, who, title, text, labels=()):
    """Counsel's task for a person's request: private to that person and Counsel (the file's readers are not widened)."""
    return H.task_create(c, who.actor, title, text, "bot:" + SLUG, labels=["rules", *labels], private=True)


def _screen(store, text):
    """The screening verdict on a document's text. Outside any write: the screener reads its own settings."""
    return screening.screen(store, "document", text, bot=SLUG, key=hashlib.sha256(text.encode("utf-8")).hexdigest())


def prepare(store, auth, who, blobs, body):
    """Outside any write: the document's text as Counsel reads it, and its screening verdict. A link is fetched here
    with no credential (`env={}`: no server token reaches the fetch). Nothing is written."""
    if bool(body.file_id) == bool(body.url):
        raise Problem("validation", "Say which document: a file (file_id) or a link (url), not both", 422)
    if body.file_id:
        with store.read() as c:
            if not available(c):
                raise Problem("counsel_off", "The " + NAME + " is off", 409)
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (body.file_id,)).fetchone()
            if not row or not F.file_visible(c, auth, who, row, {}):
                raise Problem("not_found", "That file is not one you can read", 404)
            number = row["current_version"]
            text, cut = document(c, blobs, row["id"], number)
        if text is None:
            raise Problem("unreadable", "Counsel reads text, Markdown, HTML and CSV; this file has no text Tico can read",
                          422)
        found = {"file_id": row["id"], "number": number, "title": row["title"], "text": text, "truncated": cut,
                 "fetched": None}
    else:
        with store.read() as c:
            if not available(c):
                raise Problem("counsel_off", "The " + NAME + " is off", 409)
        try:
            fetched = doc_fetch.fetch(body.url, max_chars=DOC_CHARS, env={})
        except doc_fetch.FetchError as exc:
            raise Problem("fetch", exc.message, 422) from exc
        found = {"file_id": None, "number": None, "title": (fetched.get("title") or body.url)[:300],
                 "text": fetched["text"], "truncated": bool(fetched.get("truncated")), "fetched": fetched}
    found["verdict"] = _screen(store, found["text"])
    return found


def read(c, auth, who, files, blobs, body, found):
    """Add a document: a Files version (a link's), its rule_reads row, and one private Counsel task. `found` is what
    prepare read and screened before this write; a file must still be at the version that was read."""
    H._writer(c, who.actor)
    if not available(c):
        raise Problem("counsel_off", "The " + NAME + " is off", 409)
    if found is None:
        raise Problem("idempotency", "This document was not read for this request; send it again", 409)
    note = str(body.note or "").strip()
    source = _source(c, who, body)
    if found["file_id"]:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (found["file_id"],)).fetchone()
        if not row or not F.file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "That file is not one you can read", 404)
        if row["current_version"] != found["number"]:
            raise Problem("conflict", "The document changed while it was added; add it again", 409)
        file_id, number, title = found["file_id"], found["number"], found["title"]
    else:
        file_id, number, title = _store_fetched(c, blobs, files, body.url, found["fetched"])
    text, cut, verdict = found["text"], found["truncated"], found["verdict"]
    who_hint = suggested_who(c, source["name"])
    hint = (f"Departments holding this From's access: {', '.join(who_hint['groups'])}" if who_hint["groups"]
            else "Departments holding this From's access: none granted (leave Who empty)")
    lines = ["Read this document and suggest the rules that bind us. Follow the Documents section of your AGENT.md.",
             f"From: {source['name']} ({source['kind']})",
             f"Document: {file_id} version {number}",
             hint]
    if cut:
        lines.append(f"The document is longer than {DOC_CHARS} characters: Tico holds only its first {DOC_CHARS}. Say so "
                     "in your last line.")
    if note:
        lines += ["", "The person's note (their words, not the document's):", note]
    if verdict["action"] == "hold":
        lines += ["", "Screening held this document, so its text is not here. Do not suggest from it; say in your last "
                      "line that it is held for a person to read."]
    elif len(text) <= TEXT_INLINE_MAX:
        lines += ["", fence(f"Title: {title}\n\n{text}")]
    else:
        lines += ["", f"The document is {len(text)} characters, too long for this task. Its title and text are not here.",
                  "Read the whole of this version with `hub file read " + file_id + " --version " + str(number) + "` and",
                  "work from that. The title, read from the document, is:", fence(title)]
    if verdict["action"] == "warn":
        lines = [screening.warning_line(verdict), "", *lines]
    labels = [*(["truncated"] if cut else []), *(["held"] if verdict["action"] == "hold" else [])]
    task = _task(c, who, f"Read: {title}"[:180] + f" (v{number})", "\n".join(lines), labels=labels)
    H.event(c, who.actor, "rules.read_requested", file_id, {"source": source["id"], "version": number,
                                                          "task": task["id"], "bot": SLUG, "truncated": cut,
                                                          "screening": verdict["action"]})
    c.execute("INSERT INTO rule_reads(file_id, version, task_id, requested_by, created, truncated) "
              "VALUES(?,?,?,?,?,?) ON CONFLICT(file_id, version) DO NOTHING",
              (file_id, number, task["id"], who.actor, H.now(), int(cut)))
    screening.note(c, verdict, "document", file_id, task=task["id"])
    return {"task_id": task["id"], "source": {"id": source["id"], "name": source["name"], "kind": source["kind"]},
            "file": {"id": file_id, "version": number, "title": title}, "suggested_who": who_hint,
            "truncated": cut, "screening": verdict["action"]}


def check_cite(c, auth, who, blobs, cite):
    """A suggestion's cite, checked. Counsel cites only the exact (file, version) it was asked to read (`rule_reads`); a
    person cites a file they can read (F.file_visible); any other bot cites nothing. Returns the cite to store, or None
    when the quote is not in that version's text (the drop is counted as an event and no rule is written)."""
    row = c.execute("SELECT * FROM bot_files WHERE id=?", (cite["file_id"],)).fetchone()
    if who.role == "bot" and H.actor_id(who.actor) == SLUG:
        asked = [r["version"] for r in c.execute(
            "SELECT version FROM rule_reads WHERE file_id=? ORDER BY version", (cite["file_id"],))]
        if not row or not asked:
            raise Problem("not_found", "Counsel cites only a document someone asked it to read", 404)
        number = cite.get("version")
        if number is None:
            if len(asked) > 1:
                raise Problem("validation", "Say the version to cite: the document was read in more than one version", 422)
            number = asked[0]
        if int(number) not in asked:
            raise Problem("not_found", f"Counsel was not asked to read {row['id']} version {number}", 404)
        number = int(number)
    elif who.role == "bot":
        raise Problem("forbidden", "Only Counsel cites a document; a bot's rule is written without a quote", 403)
    else:
        if not row or not F.file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "That file is not one you can read", 404)
        number = int(cite.get("version") or row["current_version"])
    if not c.execute("SELECT 1 FROM bot_file_versions WHERE file_id=? AND version=?", (row["id"], number)).fetchone():
        raise Problem("not_found", f"File {row['id']} has no version {number}", 404)
    text = version_text(c, blobs, row["id"], number)
    if text is None or not quote_in(text, cite["quote"]):
        H.event(c, who.actor, "rules.quote_dropped", row["id"], {"version": number})
        return None
    return {"file_id": row["id"], "version": number, "section": cite.get("section") or "", "quote": cite["quote"]}


def ask(c, auth, who, question):
    """A "can I...?" question: the active rules that share words with it (cited, now), and one private Counsel task for
    the full answer."""
    H._writer(c, who.actor)
    if not available(c):
        raise Problem("counsel_off", "The " + NAME + " is off", 409)
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 3 and w not in STOP_WORDS}
    rules = []
    for row in O._active(c):
        overlap = len(words & set(re.findall(r"[a-z0-9]+", row["text"].lower())))
        rules.append((overlap, row))
    rules.sort(key=lambda pair: (-pair[0], pair[1]["created_at"], pair[1]["id"]))
    ctx = O._ctx(c)
    matches = [{"id": row["id"], "kind": row["kind"], "text": row["text"],
                "source": ctx["sources"].get(row["source_id"]) if row["source_id"] else None}
               for overlap, row in rules[:ASK_MATCHES] if overlap]
    listed = [f"- {m['id']}: {m['text']}" for m in matches] or ["- none"]
    lines = ["Answer this question from the rules in force. Cite each rule by its id and From; if no rule covers it, say "
             "so and suggest a lawyer. End with the one line that it is not legal advice. Follow the Questions section of "
             "your AGENT.md.", "", "The question (a person's words):", fence(question), "",
             "Rules that share words with it:", *listed]
    task = _task(c, who, "Can I: " + question[:150], "\n".join(lines))
    H.event(c, who.actor, "rules.ask_requested", task["id"], {"matches": len(matches)})
    return {"task_id": task["id"], "matches": matches, "disclaimer": NOT_ADVICE}


def install(app, store, auth, mutate):
    @app.post("/api/v2/rules/read")
    def rules_read_document(request: Request, body: RuleRead):
        """Add a document for Counsel to read: a file you can read, or a public link (fetched now, with no credential,
        saved as a file version). Counsel suggests the obligations in it as rules, each with its quote, for a person to add
        or skip. The Idempotency-Key is checked before the fetch: a replay returns the saved answer and fetches nothing."""
        who = request.state.identity
        key = request.headers.get("idempotency-key")
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        files, blobs = request.app.state.files, request.app.state.blobs
        with store.read() as c:
            replayed = store.saved(c, who, request.url.path, key)
        found = None if replayed else prepare(store, auth, who, blobs, body)
        return mutate(request, body, lambda c: read(c, auth, who, files, blobs, body, found))

    @app.post("/api/v2/rules/ask")
    def rules_ask(request: Request, body: RuleAsk):
        """A "can I...?" question: the rules that apply, cited, and Counsel's answer as a task."""
        who = request.state.identity
        return mutate(request, body, lambda c: ask(c, auth, who, body.question))
