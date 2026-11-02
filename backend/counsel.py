"""Counsel: the built-in helper that reads a document someone adds to Rules and suggests the obligations that bind us,
and answers "can I...?" from the rules in force (docs/rules-plan.md §2, §5).

- Adding a document (`POST /api/v2/rules/read`) reads it first, outside any write: a file's text (a PDF is read as a
  link's PDF is: clients/doc_fetch.py `pdf_to_text`), or a public link fetched with clients/doc_fetch.py with no
  credential (`env={}`), either held to DOC_CHARS characters. The text is screened for prompt injection
  (backend/screening.py, source `document`): at `warn` the task starts with the warning line; at `hold` Counsel is not
  given the text at all. Then the write opens one private task for Counsel, its body naming the file and version and
  holding the document fenced as untrusted data, and records the version in `rule_reads`. A link is saved as a Files
  version attached to that task, one file per link and requester, so a later change never alters an earlier cite and a
  teammate who cannot read the task cannot read the text. A document cut at DOC_CHARS is marked truncated.
- Counsel has no credentials and sends nothing: its tool list is `clients/hubtools.py` COUNSEL_TOOLS. It reads its task and
  the rules in force, and writes suggestions through `POST /api/v2/rules` as `suggested`. A cite is checked here: Counsel
  cites only the exact (file, version) it was asked to read, named in the cite, and the quote must appear in that
  version's normalized text. A quote that does not is dropped (the response says so, and `rules.quote_dropped` counts
  it), never stored. Only Counsel and a person cite; no other bot can.
- `POST /api/v2/rules/ask` answers "can I...?" with the active rules that share words with the question, at once, and
  opens a private Counsel task for the full answer. Every answer carries the one line that it is not legal advice.

Counsel is a helper (backend/assistant.py `HELPERS`): built in, off the org chart, seeded by onboarding like the
Librarian (templates/catalog/tico-counsel). Its tasks are private by default (backend/hubdb.py PRIVATE_BY_TEMPLATE). Only the
owner can turn it on or off.
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

SLUG = H.COUNSEL
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

# Each read Counsel was asked for, one row per read task: the file version (the only versions it may cite,
# backend/files.py asked_to_read) and the From it was asked for. Two people reading one version are two rows.
RULE_READS = """CREATE TABLE IF NOT EXISTS rule_reads(
 task_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, version INTEGER NOT NULL, requested_by TEXT NOT NULL,
 created_at TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, source_id TEXT)"""
RULE_READS_INDEX = "CREATE INDEX IF NOT EXISTS rule_reads_version ON rule_reads(file_id, version)"
# A quote shorter than this from a private read could be a word that is in any document: too little to tie the
# suggestion to the version it cites.
PRIVATE_QUOTE_MIN = 20


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
    columns = {r[1]: r[5] for r in c.execute("PRAGMA table_info(rule_reads)")}
    if columns and columns.get("task_id") != 1:
        # A development table keyed by (file, version): a second reader replaced the first read's row. One row per task.
        c.execute("ALTER TABLE rule_reads RENAME TO rule_reads_by_version")
        c.execute(RULE_READS)
        source = "source_id" if "source_id" in columns else "NULL"
        c.execute("INSERT OR IGNORE INTO rule_reads(task_id, file_id, version, requested_by, created_at, truncated, "
                  f"source_id) SELECT task_id, file_id, version, requested_by, created_at, truncated, {source} "
                  "FROM rule_reads_by_version WHERE task_id IS NOT NULL")
        c.execute("DROP TABLE rule_reads_by_version")
    c.execute(RULE_READS)
    c.execute(RULE_READS_INDEX)


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


def _version(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def document(c, blobs, file_id, number):
    """One version's text as Counsel reads it: the whole text up to DOC_CHARS, and whether it was cut. A PDF is read the
    way a link's PDF is (doc_fetch.pdf_to_text); a PDF that cannot be read is a 422 that says why. (None, False) when the
    version does not exist or is of a type Tico reads no text from."""
    row = c.execute("SELECT v.mime, b.digest FROM bot_file_versions v JOIN blobs b ON b.id=v.blob_id "
                    "WHERE v.file_id=? AND v.version=?", (file_id, number)).fetchone()
    if not row:
        return None, False
    data = blobs.get(row["digest"])
    if row["mime"] == "application/pdf":
        if len(data) > doc_fetch.MAX_BYTES:
            raise Problem("unreadable", "That PDF is larger than 5 MB; Counsel reads a PDF up to that size", 422)
        try:
            text = doc_fetch.pdf_to_text(data)
        except doc_fetch.FetchError as exc:
            raise Problem("unreadable", exc.message, 422) from exc
    else:
        text = file_library.extract(row["mime"], data, full=True)
    if not text or not text.strip():
        return None, False
    return text[:DOC_CHARS], len(text) > DOC_CHARS or len(data) > file_library.READ_MAX


def version_text(c, blobs, file_id, number):
    return document(c, blobs, file_id, number)[0]


def _words(text):
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", str(text or "")).casefold())


def named(source_name, credential_name):
    """Whether the From's words appear whole and in order in a credential's name: "Meta" names "Meta Ads", never "Metabase"."""
    want, have = _words(source_name), _words(credential_name)
    return bool(want) and any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


def suggested_who(c, source_name):
    """The departments of the bots granted a credential whose name carries this From (backend/credentials.py). Empty
    lists when none is granted: Counsel then leaves Who to the owner. A credential has no vendor field, so the match is
    on the name's words (`named`)."""
    if not str(source_name or "").strip():
        return {"groups": [], "bots": []}
    ids = [r["id"] for r in c.execute("SELECT id, name FROM credentials") if named(source_name, r["name"])]
    if not ids:
        return {"groups": [], "bots": []}
    marks = ",".join("?" * len(ids))
    subjects = {r["subject"] for r in c.execute(
        f"SELECT DISTINCT subject FROM credential_grants WHERE credential_id IN ({marks}) AND revoked_at IS NULL "
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


def _link_plan(c, who, url, text):
    """Where a link's text is saved, decided before the task is written (the task names the file and version): a new
    file, the file's next version, or, when the text is what the file already holds, its current version. A link's file
    is its requester's (one per link and person), so it is readable exactly as the requester's task is."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = "rule-doc:" + hashlib.sha256(f"{url}\n{who.actor}".encode("utf-8")).hexdigest()[:24]
    row = c.execute("SELECT * FROM bot_files WHERE bot_id=? AND identity=?", (SLUG, identity)).fetchone()
    if not row:
        return {"identity": identity, "digest": digest, "row": None, "id": F.new_id(), "number": 1, "changed": True}
    held = c.execute("SELECT COALESCE(v.source_digest, b.digest) AS digest FROM bot_file_versions v "
                     "JOIN blobs b ON b.id=v.blob_id WHERE v.file_id=? AND v.version=?",
                     (row["id"], row["current_version"])).fetchone()
    changed = not held or held["digest"] != digest
    return {"identity": identity, "digest": digest, "row": row, "id": row["id"],
            "number": row["current_version"] + (1 if changed else 0), "changed": changed}


def _store_link(c, blobs, files, plan, task_id, text, title):
    """Save a link's text as the planned version: the file starts on the read task, or keeps the task it started on."""
    if not plan["changed"]:
        return
    data = text.encode("utf-8")
    blobs.put(data, "text/plain; charset=utf-8")
    blob = register(c, Identity("bot:" + SLUG, "bot"), plan["digest"], len(data), "document.txt", "text/plain")
    row = plan["row"]
    scope, task = (row["scope"], row["task_id"]) if row else ("task:" + task_id, task_id)
    saved, _, _ = files.add_version(c, bot=SLUG, actor="bot:" + SLUG, scope=scope, task=task, conversation=None,
                                    attempt="", identity=plan["identity"], title=title, name="document.txt",
                                    mime="text/plain", blob_id=blob["id"], digest=plan["digest"], size=len(data),
                                    source_digest=plan["digest"], fid=plan["id"])
    if saved["current_version"] != plan["number"]:
        raise Problem("conflict", "The document changed while it was added; add it again", 409)
    file_library.index(c, saved["id"], saved["current_version"], saved["title"], text)


# Outward routes a Counsel token may not call (403, from the API's middleware: backend/app.py), said in their own words;
# COUNSEL_ROUTES below refuses every route it does not name in any case. Counsel speaks only on its own task: a task
# comment is allowed there and nowhere else (TASK_COMMENT).
OUTWARD = tuple(re.compile(pattern) for pattern in (
    r"^/api/v2/gateway/(search|fetch|images)$",                 # the Gateway: web search, page fetch, images
    r"^/api/v2/messages(/[^/]+/answer)?$",                      # a message to anyone, or an answer to a question
    r"^/api/v2/conversations/[^/]+/messages$",
    r"^/api/v2/assistant/messages$",
    r"^/api/send$",
    r"^/api/meetings/[^/]+/(send|comments)$",
    r"^/api/v2/meetings/[^/]+/comments$",
    r"^/api/v2/updates/[^/]+/reply$",
    r"^/api/v2/automations/mail-actions/claim$",                 # a mail action to send
    r"^/api/v2/connectors/(mail/google/claim|calendar/actions/claim)$",
    r"^/api/v2/slack/",                                         # Slack, every write (a read is a GET)
    r"^/api/v2/webhooks",                                       # outbound webhooks: create, send, replay
    r"^/api/v2/triggers/[^/]+/relay",
))
TASK_COMMENT = re.compile(r"^/api/v2/tasks/([^/]+)/comments(/|$)")
SENDS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REFUSED = "Counsel sends nothing outside Tico and speaks only on its own task: this call is not its to make."


def is_counsel(who):
    return who.role == "bot" and who.actor == "bot:" + SLUG


ATTACH_REFUSED = "Counsel attaches no files: it quotes the document it was asked to read in its words and its rule."


def no_attachments(who, items):
    """Counsel's comments carry no files (visuals.attach, before any is read): an attachment is a version of a file on
    its task, which is not the one version its read grants (run_read), and its response would carry a signed address."""
    if items and is_counsel(who):
        raise Problem("privacy", ATTACH_REFUSED, 403)


def refused(store, who, path, method):
    """Whether a call by Counsel is refused: an outward route (OUTWARD), or a task comment on a task that is not Counsel's
    own (the task's owner is Counsel). Any other call is the runner's or a read, and is left to the route."""
    if not is_counsel(who) or method.upper() not in SENDS:
        return False
    if any(pattern.match(path) for pattern in OUTWARD):
        return True
    match = TASK_COMMENT.match(path)
    if match:
        with store.read() as c:
            row = c.execute("SELECT owner_id FROM tasks WHERE id=?", (match.group(1),)).fetchone()
        return bool(row) and row["owner_id"] != "bot:" + SLUG
    return False


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
            raise Problem("unreadable", "Counsel reads text, Markdown, HTML, CSV and PDF; this file has no text Tico can "
                          "read", 422)
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


def _open_read(c, who, file_id, number, source_id):
    """The open task for this person, file version and source; identical re-reads return it."""
    row = c.execute("SELECT r.task_id FROM rule_reads r JOIN tasks t ON t.id=r.task_id WHERE r.file_id=? AND r.version=? "
                    "AND r.requested_by=? AND r.source_id=? AND t.closed_at IS NULL AND t.status NOT IN ('done','closed','declined')",
                    (file_id, number, who.actor, source_id)).fetchone()
    return row["task_id"] if row else None


def _receipt(source, file_id, number, title, who_hint, cut, action, task_id, existing):
    return {"task_id": task_id, "source": {"id": source["id"], "name": source["name"], "kind": source["kind"]},
            "file": {"id": file_id, "version": number, "title": title}, "suggested_who": who_hint,
            "truncated": cut, "screening": action, "existing": existing}


def read(c, auth, who, files, blobs, body, found):
    """Add a document: its one private Counsel task, the file version it names (a link's text is saved as one), and its
    rule_reads row. `found` is what prepare read and screened before this write; a file must still be at the version
    that was read. The page's own title is untrusted: it goes inside the fence, never on a line of its own."""
    H._writer(c, who.actor)
    if not available(c):
        raise Problem("counsel_off", "The " + NAME + " is off", 409)
    if found is None:
        raise Problem("idempotency", "This document was not read for this request; send it again", 409)
    note = str(body.note or "").strip()
    source = _source(c, who, body)
    text, cut, verdict, title = found["text"], found["truncated"], found["verdict"], found["title"]
    if found["file_id"]:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (found["file_id"],)).fetchone()
        if not row or not F.file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "That file is not one you can read", 404)
        if row["current_version"] != found["number"]:
            raise Problem("conflict", "The document changed while it was added; add it again", 409)
        file_id, number, plan = found["file_id"], found["number"], None
    else:
        plan = _link_plan(c, who, body.url, text)
        file_id, number = plan["id"], plan["number"]
    who_hint = suggested_who(c, source["name"])
    open_task = _open_read(c, who, file_id, number, source["id"])
    if open_task:
        # Asked again while the last task for this version and source is open: the same task, no second one (people re-read
        # on purpose only once it is closed).
        return _receipt(source, file_id, number, title, who_hint, cut, verdict["action"], open_task, existing=True)
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
        lines += ["", f"The document is {len(text)} characters, too long for this task. Its text is not here.",
                  f"Read the whole of this version with `hub file read {file_id} --version {number}` and work from that.",
                  "", fence(f"Title: {title}")]
    if verdict["action"] == "warn":
        lines = [screening.warning_line(verdict), "", *lines]
    labels = [*(["truncated"] if cut else []), *(["held"] if verdict["action"] == "hold" else [])]
    # Task creation also deduplicates by title, so keep its identity source-specific.
    task = _task(c, who, f"Read document (file {file_id} v{number}; source {source['id']})",
                 "\n".join(lines), labels=labels)
    if plan:
        _store_link(c, blobs, files, plan, task["id"], text, title)
    H.event(c, who.actor, "rules.read_requested", file_id, {"source": source["id"], "version": number,
                                                          "task": task["id"], "bot_id": SLUG, "truncated": cut,
                                                          "screening": verdict["action"]})
    c.execute("INSERT INTO rule_reads(task_id, file_id, version, requested_by, created_at, truncated, source_id) "
              "VALUES(?,?,?,?,?,?,?)", (task["id"], file_id, number, who.actor, H.now(), int(cut), source["id"]))
    screening.note(c, verdict, "document", file_id, task=task["id"])
    return _receipt(source, file_id, number, title, who_hint, cut, verdict["action"], task["id"], existing=False)


def attempt_read(c, aid):
    """The read a Counsel run may publish from: the `rule_reads` row of its read task, Counsel's own, when that task is
    the only task whose text the run carries (an assignment notice's list of other open tasks names them, it does not
    carry them). None for any other run: one that carries a second task could carry its text into a company-wide rule."""
    from .task_privacy import carried_tasks
    tasks = carried_tasks(c, aid)
    if len(tasks) != 1:
        return None
    row = c.execute("SELECT r.* FROM rule_reads r JOIN tasks t ON t.id=r.task_id WHERE r.task_id=? AND t.owner_id=?",
                    (next(iter(tasks)), "bot:" + SLUG)).fetchone()
    return dict(row) if row else None


def run_read(c, who):
    """attempt_read for a call Counsel's run makes; None for any other caller."""
    if not is_counsel(who) or not who.attempt_id:
        return None
    return attempt_read(c, who.attempt_id)


def private_suggestion(c, who, data):
    """A rule Counsel writes from a private run. Its read tasks are private, and a private run publishes nothing
    company-wide (backend/task_privacy.py guard_write), except this: on its own read task, Counsel suggests external
    rules for that read's From, each citing that read's file version with a quote of at least PRIVATE_QUOTE_MIN
    characters (check_cite then finds it in that version), whose quote only the file's readers see (org_rules.view).
    While it may, its reads are scoped to that read (read_refused). Anything else from a private run is refused here.
    A run that is not private is left to the route."""
    from .task_privacy import private_execution
    if not is_counsel(who) or not private_execution(c, who):
        return
    read = run_read(c, who)
    if not read:
        raise Problem("privacy", "Private task work cannot publish into company-wide resources", 403)
    cite = data.get("cite") or {}
    source = O._source_named(c, data.get("source_id")) if data.get("source_id") else None
    if data.get("kind") != "external" or not read["source_id"] or source != read["source_id"]:
        raise Problem("privacy", "From a document's read, Counsel suggests external rules for that read's From only", 403)
    if cite.get("file_id") != read["file_id"] or _version(cite.get("version")) != read["version"]:
        raise Problem("privacy", "From a document's read, Counsel's rule cites that document at the version its task "
                      f"names: file {read['file_id']} version {read['version']}", 403)
    if len(normal(cite.get("quote"))) < PRIVATE_QUOTE_MIN:
        raise Problem("validation", f"Quote at least {PRIVATE_QUOTE_MIN} characters of the document, copied exactly", 422)


# Every call Counsel may make, as (methods, path under /api/v2/), and nothing else: an allowlist, so a transport added
# later is not Counsel's until it is named here. It is what Counsel is offered and what runs it: its tools
# (clients/hubtools.py COUNSEL_TOOLS: hub_rules_list/get/add, hub_file_read, hub_task_comment, hub_task_close) and the
# calls they make, the MCP that carries them (each call inside comes back through here), `me` (which tools it is
# offered), and what the computer running it asks with the run's own token (its task, sessions.rs task_closed; its
# credentials, env.rs credential-runtime). Its runner's plumbing (claim, start, events, complete) is the runner's own
# token, not Counsel's. Each call is then scoped (read_refused): its own run, the tasks it carries, its read's version.
GET, WRITE, ANY = frozenset({"GET", "HEAD"}), frozenset({"POST"}), frozenset({"GET", "HEAD", "POST", "PUT", "PATCH",
                                                                              "DELETE"})
COUNSEL_ROUTES = tuple((methods, re.compile(r"^/api/v2/" + pattern + r"$")) for methods, pattern in (
    (WRITE, r"mcp"),
    (GET, r"me"),
    (GET, r"credential-runtime"),
    (GET, r"rules"),
    (GET, r"rules/(?!counts$|due$|stats$)[^/]+"),
    (WRITE, r"rules"),
    (GET | WRITE, r"tasks/(?!(?:flow|labels|stuck|dry-run)$)[^/]+"),     # one task, never a collection route
    (WRITE, r"tasks/[^/]+/comments"),
    (GET, r"files/[^/]+"),
    (GET, r"files/[^/]+/versions/\d+"),
    (ANY, r"(attempts|turns)/[^/]+(/.*)?"),
))
READ_SCOPE = "Counsel reads only its own task, the document version it was asked to read and its own run."
# Counsel outside a run: every turn it takes carries the run's own token (runner-rs env.rs and warm.rs HUB_TOKEN; its
# credentials are fetched with that token too), and it has no agent credential unless someone makes it an external
# agent. Such a token owns every read task, so it calls nothing but `me`.
STANDING = ((GET, re.compile(r"^/api/v2/me$")),)
STANDING_REFUSED = "Counsel works only inside a run: outside one it reads and writes nothing."
# SQL reads across the whole hub: never Counsel's, in a run or not (outside the allowlist too; its own words).
SQL_READS = re.compile(r"^/api/(v2/)?sql/?$")
SQL_REFUSED = "Counsel reads no SQL: it reads its own task and the document version it was asked to read."


def read_refused(store, auth, who, method, path, query):
    """Why a call by Counsel is refused, or None. Only COUNSEL_ROUTES are its, whatever else its bot's rights would
    allow (another run's summary, an answer to another run's question, a page or a review of another version). On those:
    a run's attempt and steps are its own run's alone; a Counsel run reads only the tasks it carries
    (task_privacy.carried_tasks: its read task, or the question it answers) and, on a document's read (run_read), that
    file at that version, which it names. Counsel outside a run (no attempt) calls only STANDING."""
    if not is_counsel(who):
        return None
    if SQL_READS.match(path):
        return SQL_REFUSED
    method = method.upper()
    if not who.attempt_id:
        return None if any(method in methods and pattern.match(path) for methods, pattern in STANDING) \
            else STANDING_REFUSED
    if not any(method in methods and pattern.match(path) for methods, pattern in COUNSEL_ROUTES):
        return READ_SCOPE
    parts = path.split("/")[3:]
    kind, ident, rest = parts[0], (parts[1] if len(parts) > 1 else ""), parts[2:]
    if kind in ("attempts", "turns"):
        return READ_SCOPE if ident != who.attempt_id else None
    if kind not in ("tasks", "files"):
        return None
    from .task_privacy import carried_tasks
    with store.read() as c:
        if kind == "tasks":
            try:
                refused = auth.resolve_task(c, who, ident) not in carried_tasks(c, who.attempt_id)
            except Problem:
                refused = True
        else:
            read = run_read(c, who)
            refused = not read or ident != read["file_id"] or (
                rest != ["versions", str(read["version"])] if rest else _version(query.get("v")) != read["version"])
    return READ_SCOPE if refused else None


def cite_readers(c, auth, who):
    """Whom a rule's cite is shown as when Counsel reads rules: the people whose tasks its run carries (who asked for its
    read or its question), each of whom must be able to read the cited file, so a quote never enters a run whose asker
    could not read it. Nobody outside a run, or for a task with no person behind it: Counsel then sees no quote."""
    if not who.attempt_id:
        return []
    from .task_privacy import carried_tasks
    readers = []
    for task_id in sorted(carried_tasks(c, who.attempt_id)):
        task = H.task(c, task_id)
        requester = task["requester_id"] if task else None
        if not requester or not H.is_human(requester):
            return []
        try:
            readers.append(auth.identity_for_actor(c, requester))
        except Problem:
            return []
    return readers


def _disclaimer(line):
    """Whether a line of Counsel's reply is only its NOT_ADVICE line (markup and quote marks around it aside)."""
    return normal(line).strip(" *_>`\"'-") == normal(NOT_ADVICE)


def turn_done(c, attempt, outcome, task_id, text):
    """Counsel's turn on a document's read task ended (backend/execution.py complete). A completed turn finishes the
    read it was allowed to publish from (attempt_read): the task is marked done with the turn's last line other than
    NOT_ADVICE, so it never sits in doing after the work. A run that carried another task copies nothing and finishes
    nothing. A failed,
    stopped or cancelled turn leaves the task to the normal retry and cleanup; a task Counsel set waiting or already
    finished is left as it is."""
    if attempt["bot"] != SLUG or outcome != "completed" or not task_id:
        return
    read = attempt_read(c, attempt["id"])
    if not read or read["task_id"] != task_id:
        return          # a run that carried another task: its text stays on the run, the task to the normal cleanup
    row = H.task(c, task_id)
    if not row or row["owner_id"] != "bot:" + SLUG or row["status"] not in ("open", "doing"):
        return
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip() and not _disclaimer(line)]
    H.task_update(c, "bot:" + SLUG, task_id, status="done", note=(lines[-1][:500] if lines else None))


def check_cite(c, auth, who, blobs, cite):
    """A suggestion's cite, checked. Counsel cites only the (file, version) of its own run's read (run_read: the
    `rule_reads` row of the read task it runs on), and it must name that version: any other cite is a 404 before any
    quote is checked. A person cites a file they can read (F.file_visible); any other bot cites nothing. Returns the cite
    to store, or None when the quote is not in that version's text (the drop is counted as an event and no rule is
    written)."""
    row = c.execute("SELECT * FROM bot_files WHERE id=?", (cite["file_id"],)).fetchone()
    if is_counsel(who):
        read = run_read(c, who)
        number = _version(cite.get("version"))
        if not row or not read or read["file_id"] != row["id"] or number != read["version"]:
            raise Problem("not_found", "Counsel cites only the document its run was asked to read, at the version its "
                          "task gives, and names that version", 404)
    elif who.role == "bot":
        raise Problem("forbidden", "Only Counsel cites a document; a bot's rule is written without a quote", 403)
    else:
        if not row or not F.file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "That file is not one you can read", 404)
        number = _version(cite.get("version")) or row["current_version"]
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
