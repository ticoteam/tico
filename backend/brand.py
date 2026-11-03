"""The brand page: what the company sounds like, written down, versioned and read by the bots that face customers.

The page is one draft row (`brand_drafts`, id 1, with a `revision`) and numbered published versions
(`brand_versions`). The draft starts as a copy of the published body the first time it is written; a write names the
sections it changes (null removes one) and the revision it read, and an older one answers 409 `stale_revision`.
Publishing stamps the draft as version n+1, clears the draft and emits `brand.published`. It files no Decide card: the
admin who publishes is the decision.

Who may: everyone signed in reads the published page; the owner and admins (people, on their own click) see and write
the draft and publish. A bot proposes (`goal_proposals` kind `brand_draft`, payload `{sections}`), and a confirmed
proposal is merged into the draft, never published.

`check` is the brand's programmatic lint: avoided words, wrong spellings of names and claims the brand never makes,
matched case-insensitively at word boundaries. No model runs.
"""
import json
import re

from fastapi import Request
from pydantic import ValidationError

from . import hubdb as H
from . import models as M
from .store import Problem

SECTIONS = ("name", "positioning", "voice", "tone", "words", "naming", "visual", "claims", "examples")
SUMMARY_MAX = 300
AVOID_CHARS = 110
VERSIONS_SHOWN = 50


def _loads(text, default=None):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value


def admin(auth, who):
    """The owner or an admin, a person acting on their own click: never a bot, never someone acting for them."""
    return who.role in ("owner", "human") and not getattr(who, "via", None) and auth.company_role(who) in ("owner", "admin")


# ----------------------------------------------------------------------------- reads
def published(c, n=None):
    row = H._one(c, "SELECT * FROM brand_versions " + ("WHERE n=?" if n is not None else "ORDER BY n DESC LIMIT 1"),
                 (n,) if n is not None else ())
    if not row:
        return None
    return {"n": row["n"], "body": _body(_loads(row["body"], {})), "note": row["note"],
            "published_by": row["published_by"], "published_at": row["published_at"]}


def _body(value):
    return dict(value) if isinstance(value, dict) else {}


def draft(c, current=None):
    """The draft with the sections that differ from the published body, or None when there is none."""
    row = H._one(c, "SELECT * FROM brand_drafts WHERE id=1")
    body = _loads(row["body"]) if row else None
    if not isinstance(body, dict):
        return None
    body = _body(body)
    base = (current or {}).get("body") or {}
    changed = [s for s in SECTIONS if body.get(s) != base.get(s)]
    return {"body": body, "revision": row["revision"], "updated_by": row["updated_by"], "updated_at": row["updated_at"],
            "changed": changed}


def versions(c, limit=VERSIONS_SHOWN):
    return H._rows(c.execute("SELECT n, note, published_by, published_at FROM brand_versions ORDER BY n DESC LIMIT ?",
                             (limit,)))


def view(c, editor):
    current = published(c)
    return {"published": current, "draft": draft(c, current) if editor else None, "versions": versions(c),
            "may_edit": bool(editor)}


def _names(items, key, limit):
    return [str(i.get(key) or "").strip() for i in (items or [])[:limit] if str(i.get(key) or "").strip()]


def _clip(text, limit, first=False):
    """Free text as one line: its first sentence when `first` (split on "; ", a newline, or ". " before a capital
    when the dot ends a word, so "U.S." and "Dr." stay), cut to `limit` characters at a word boundary with "…"."""
    text = str(text or "").strip()
    if first:
        text = re.split(r"(?<=[a-z0-9)]{2})\. (?=[A-Z])|; |\n", text, maxsplit=1)[0]
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    return (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(" ,;:-") + "…"


def _end(text):
    """The sentence with a full stop, unless it already ends in punctuation or an ellipsis."""
    return text if not text or text[-1] in ".!?…" else text + "."


def summary_text(c, current=None):
    """About 300 characters a growth-facing bot reads on every run, or "" before the first version."""
    current = current if current is not None else published(c)
    if not current:
        return ""
    body, n = current["body"], current["n"]
    name = str(body.get("name") or "").strip()
    parts = [f"Brand v{n}: {name.rstrip('.')}." if name else f"Brand v{n}."]
    positioning = _clip(body.get("positioning"), 120, first=True)
    if positioning:
        parts.append(_end(positioning))
    traits = _names(body.get("voice"), "trait", 4)
    if traits:
        parts.append("Voice: " + ", ".join(traits) + ".")
    # Avoided words, block-level first; a word that would push the part past AVOID_CHARS, or the summary past
    # SUMMARY_MAX, is dropped with the rest, so no avoided word is ever cut.
    room = min(AVOID_CHARS, SUMMARY_MAX - len(" ".join(parts)) - 1)
    avoid = []
    ranked = sorted((body.get("words") or {}).get("avoid") or [], key=lambda w: w.get("level") != "block")
    for word in _names(ranked, "word", None):
        if len("Avoid: " + ", ".join(avoid + [word]) + ".") > room:
            break
        avoid.append(word)
    if avoid:
        parts.append("Avoid: " + ", ".join(avoid) + ".")
    text = " ".join(parts)
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX - 1].rstrip() + "…"


# ----------------------------------------------------------------------------- writes
def validate(body):
    """The body checked against the brand's shape, as stored: unset sections left out. Raises Problem 422."""
    unknown = sorted(set(body) - set(SECTIONS))
    if unknown:
        raise Problem("validation", f"The brand page has no section {', '.join(unknown)}; its sections are "
                      + ", ".join(SECTIONS), 422)
    try:
        model = M.BrandBody.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise Problem("validation", f"{where}: {first.get('msg')}", 422) from None
    return model.model_dump(exclude_none=True, by_alias=True)


def merge(c, actor, sections, revision=None):
    """Write `sections` onto the draft (null removes one), starting from the published body when there is no draft."""
    H._writer(c, actor)
    row = H._one(c, "SELECT * FROM brand_drafts WHERE id=1")
    current_revision = row["revision"] if row else 0
    if revision is not None and revision != current_revision:
        raise Problem("stale_revision", f"The brand draft changed since it was read (revision {current_revision}); "
                      "read it again and reapply the change", 409, extra={"revision": current_revision})
    body = _loads(row["body"]) if row else None
    if not isinstance(body, dict):
        body = dict((published(c) or {}).get("body") or {})
    body = _body(body)
    for name, value in (sections or {}).items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    body = validate(body)
    now = H.now()
    c.execute("INSERT INTO brand_drafts (id, body, revision, updated_by, updated_at) VALUES (1,?,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET body=excluded.body, revision=excluded.revision, "
              "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
              (json.dumps(body, sort_keys=True), current_revision + 1, actor, now))
    H.event(c, actor, "brand.draft", "draft", {"sections": sorted(sections or {})})
    return body


def publish(c, actor, note=""):
    H._writer(c, actor)
    current = published(c)
    pending = draft(c, current)
    if not pending:
        raise Problem("no_draft", "There is no brand draft to publish", 409)
    n = (current or {}).get("n", 0) + 1
    note = " ".join(str(note or "").split())[:500]
    c.execute("INSERT INTO brand_versions (id, n, body, note, published_by, published_at) VALUES (?,?,?,?,?,?)",
              (H.new_id(), n, json.dumps(pending["body"], sort_keys=True), note, actor, H.now()))
    c.execute("UPDATE brand_drafts SET body='null', revision=revision+1, updated_by=?, updated_at=? WHERE id=1",
              (actor, H.now()))
    H.event(c, actor, "brand.publish", f"v{n}", {"n": n})
    H.hook(c, "brand.published", f"v{n}", lambda: {"version": n, "note": note, "published_by": actor}, actor,
           revision=0)
    return n


# ----------------------------------------------------------------------------- check
def _pattern(phrase):
    words = [re.escape(w) for w in str(phrase).split()]
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.I)


def check(body, text):
    """Every rule the text breaks, in text order: avoided words, wrong spellings of names, claims never made."""
    hits = []

    def scan(kind, phrase, rule, level):
        pattern = _pattern(phrase)
        if pattern:
            for m in pattern.finditer(text):
                hits.append({"kind": kind, "match": m.group(0), "rule": rule, "level": level, "index": m.start()})

    for word in (body.get("words") or {}).get("avoid") or []:
        scan("avoid", word.get("word"), word.get("why") or f"Avoid \"{word.get('word')}\"", word.get("level") or "warn")
    for name in body.get("naming") or []:
        for wrong in name.get("wrong") or []:
            scan("naming", wrong, f"Write {name.get('name')}" + (f": {name['rule']}" if name.get("rule") else ""), "warn")
    for claim in (body.get("claims") or {}).get("never") or []:
        scan("never_claim", claim, f"The brand never claims \"{claim}\"", "block")
    hits.sort(key=lambda h: (h["index"], h["kind"]))
    return hits


# ----------------------------------------------------------------------------- proposals
def proposal_error(c, payload):
    """What is wrong with a `brand_draft` proposal, in a sentence, or None."""
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict) or not sections:
        return "a brand_draft proposal carries {sections: {name: value}} with at least one section"
    extra = sorted(set(payload) - {"sections", "source"})
    if extra:
        return f"a brand_draft proposal carries only sections, not {', '.join(extra)}"
    body = {k: v for k, v in sections.items() if v is not None}
    try:
        validate(body)
    except Problem as exc:
        return exc.detail
    return None


def apply_proposal(c, actor, payload):
    """A confirmed proposal, merged into the draft as the admin who confirmed it. Never published."""
    sections = _body(payload.get("sections"))
    merge(c, actor, sections)
    return {"sections": sorted(sections), "draft": True}


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate):
    def reader(who):
        auth.domain(who)

    def editor(who):
        auth.domain(who)
        if admin(auth, who):
            return
        if who.role == "bot":
            raise Problem("propose_instead", "A bot proposes a change to the brand draft and an admin confirms it: "
                          "hub proposal create --kind brand_draft", 403)
        raise Problem("forbidden", "Only the owner or an admin writes the brand draft and publishes it", 403)

    @app.get("/api/v2/brand")
    def brand_show(request: Request):
        """The published brand page, its versions, and for an admin the draft."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return view(c, admin(auth, who))

    @app.get("/api/v2/brand/versions/{n}")
    def brand_one_version(request: Request, n: int):
        """One published version, whole."""
        reader(request.state.identity)
        with store.read() as c:
            row = published(c, n)
        if not row:
            raise Problem("not_found", f"There is no brand version {n}", 404)
        return row

    @app.get("/api/v2/brand/summary")
    def brand_summary(request: Request):
        """The brand page in about 300 characters, as growth-facing bots read it: {n, text}."""
        reader(request.state.identity)
        with store.read() as c:
            current = published(c)
            return {"n": (current or {}).get("n", 0), "text": summary_text(c, current)}

    @app.put("/api/v2/brand/draft")
    def brand_write_draft(request: Request, body: M.PageDraft):
        """Owner or admin: merge sections into the brand draft (null removes one). A bot proposes instead (kind
        brand_draft)."""
        who = request.state.identity

        def work(c):
            editor(who)
            merge(c, who.actor, body.sections, body.revision)
            return view(c, True)
        return mutate(request, body, work)

    @app.post("/api/v2/brand/publish")
    def brand_do_publish(request: Request, body: M.PagePublish):
        """Owner or admin: the brand draft becomes the next version."""
        who = request.state.identity

        def work(c):
            editor(who)
            publish(c, who.actor, body.note)
            return view(c, True)
        return mutate(request, body, work)

    @app.post("/api/v2/brand/check")
    def brand_check(request: Request, body: M.PageCheck):
        """Every brand rule the text breaks: avoided words, wrong name spellings, claims the brand never makes."""
        reader(request.state.identity)
        with store.read() as c:
            current = published(c)
        return {"n": (current or {}).get("n", 0), "hits": check((current or {}).get("body") or {}, body.text)}
