"""The brand and go-to-market pages: what the company sounds like and how it sells, written down, versioned and read by
the bots that face customers.

Both pages share one shape. Each is one draft row (`brand_drafts` / `gtm_drafts`, id 1, with a `revision`) and numbered
published versions (`brand_versions` / `gtm_versions`). The draft starts as a copy of the published body the first time
it is written; a write names the sections it changes (null removes one) and the revision it read, and an older one
answers 409 `stale_revision`. Publishing stamps the draft as version n+1, clears the draft and emits `brand.published`
or `gtm.published`. It files no Decide card: the admin who publishes is the decision.

Who may: everyone signed in reads the published page; the owner and admins (people, on their own click) see and write
the draft and publish. A bot proposes (`goal_proposals` kind `brand_draft` / `gtm_draft`, payload `{sections}`), and a
confirmed proposal is merged into the draft, never published.

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

KINDS = ("brand", "gtm")
SECTIONS = {
    "brand": ("name", "positioning", "voice", "tone", "words", "naming", "visual", "claims", "examples"),
    "gtm": ("motion", "buyer", "playbooks"),
}
# Sections that were removed: a row written before the removal still holds them, so reads drop them. Writes that
# name one get the same 422 as any unknown section.
RETIRED = {"gtm": ("wishes",)}
BODIES = {"brand": M.BrandBody, "gtm": M.GtmBody}
TITLES = {"brand": "brand", "gtm": "go-to-market"}
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
def published(c, kind, n=None):
    row = H._one(c, f"SELECT * FROM {kind}_versions " + ("WHERE n=?" if n is not None else "ORDER BY n DESC LIMIT 1"),
                 (n,) if n is not None else ())
    if not row:
        return None
    return {"n": row["n"], "body": _body(kind, _loads(row["body_json"], {})), "note": row["note"],
            "published_by": row["published_by"], "published_at": row["published_at"]}


def _draft_row(c, kind):
    return H._one(c, f"SELECT * FROM {kind}_drafts WHERE id=1")


def _body(kind, value):
    """A stored body as read: its sections, without the retired ones."""
    body = dict(value) if isinstance(value, dict) else {}
    for name in RETIRED.get(kind, ()):
        body.pop(name, None)
    return body


def draft(c, kind, current=None):
    """The draft with the sections that differ from the published body, or None when there is none."""
    row = _draft_row(c, kind)
    body = _loads(row["body_json"]) if row else None
    if not isinstance(body, dict):
        return None
    body = _body(kind, body)
    base = (current or {}).get("body") or {}
    changed = [s for s in SECTIONS[kind] if body.get(s) != base.get(s)]
    return {"body": body, "revision": row["revision"], "updated_by": row["updated_by"], "updated_at": row["updated_at"],
            "changed": changed}


def versions(c, kind, limit=VERSIONS_SHOWN):
    return H._rows(c.execute(f"SELECT n, note, published_by, published_at FROM {kind}_versions ORDER BY n DESC LIMIT ?",
                             (limit,)))


def view(c, kind, editor):
    current = published(c, kind)
    return {"published": current, "draft": draft(c, kind, current) if editor else None, "versions": versions(c, kind),
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


def summary_text(c, kind, current=None, hidden=None):
    """About 300 characters a growth-facing bot reads on every run, or "" before the first version. `hidden`: the KPI
    owners the reader may not see (None hides every bot's KPI, the safe default)."""
    current = current if current is not None else published(c, kind)
    if not current:
        return ""
    body, n = current["body"], current["n"]
    if kind == "brand":
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
    else:
        parts = [f"GTM v{n}."]
        motion = body.get("motion") or {}
        if motion.get("primary"):
            parts.append("Motion: " + motion["primary"].replace("_", " ")
                         + (f" (then {motion['secondary'].replace('_', ' ')})" if motion.get("secondary") else "") + ".")
        buyer = body.get("buyer") or {}
        who = _clip(buyer.get("who_buys"), 90, first=True)
        if who:
            deal = _clip(buyer.get("deal_size"), 50, first=True)
            parts.append(_end("Buyer: " + who.rstrip(".") + (f", {deal}" if deal else "")))
        parts += _segments_line(c)
        parts += _north_star_line(c, hidden)
        parts += _channels_line(c)
    text = " ".join(parts)
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX - 1].rstrip() + "…"


SEGMENTS_CHARS = 100


def _segments_line(c):
    """The Growth segments served and tested, as `Segments: serving A, B; testing C.`, cut at a word boundary."""
    rows = H._rows(c.execute("SELECT g.status, coalesce(e.name, g.segment_id) AS name FROM growth_segments g "
                             "LEFT JOIN market_entities e ON e.id=g.segment_id WHERE g.status IN ('serving','testing') "
                             "ORDER BY g.sort, name COLLATE NOCASE"))
    groups = [f"{status} " + ", ".join(r["name"] for r in rows if r["status"] == status)
              for status in ("serving", "testing") if any(r["status"] == status for r in rows)]
    if not groups:
        return []
    return [_end(_clip("Segments: " + "; ".join(groups), SEGMENTS_CHARS))]


def _channels_line(c):
    rows = H._rows(c.execute("SELECT name, stage FROM growth_channels WHERE retired_at IS NULL "
                             "AND stage IN ('scaling','testing') ORDER BY sort, name"))
    out = []
    for stage in ("scaling", "testing"):
        names = [r["name"] for r in rows if r["stage"] == stage][:4]
        if names:
            out.append(f"{stage.title()}: " + ", ".join(names) + ".")
    return out


def _north_star_line(c, hidden=None):
    from . import kpis as K
    row = H._one(c, "SELECT * FROM kpis WHERE growth_role='north_star' AND archived_at IS NULL LIMIT 1")
    if not row:
        return []
    owner = row.get("owner") or ""
    if owner in (hidden or ()) or (hidden is None and owner.startswith("bot:")):
        return []
    newest = K.latest(K.readings(c, row["id"], effective=True))
    link = H._one(c, "SELECT target FROM goal_kpis WHERE kpi_id=? AND target IS NOT NULL ORDER BY updated DESC LIMIT 1",
                  (row["id"],))
    line = f"North Star: {row['name']}"
    if newest:
        line += " " + K.amount(newest["value"], row.get("unit") or "")
    if link:
        line += " (goal " + K.amount(link["target"], row.get("unit") or "") + ")"
    return [line + "."]


# ----------------------------------------------------------------------------- writes
def validate(kind, body):
    """The body checked against its kind's shape, as stored: unset sections left out. Raises Problem 422."""
    unknown = sorted(set(body) - set(SECTIONS[kind]))
    if unknown:
        raise Problem("validation", f"The {TITLES[kind]} page has no section {', '.join(unknown)}; its sections are "
                      + ", ".join(SECTIONS[kind]), 422)
    try:
        model = BODIES[kind].model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise Problem("validation", f"{where}: {first.get('msg')}", 422) from None
    return model.model_dump(exclude_none=True, by_alias=True)


def merge(c, kind, actor, sections, revision=None):
    """Write `sections` onto the draft (null removes one), starting from the published body when there is no draft."""
    H._writer(c, actor)
    row = _draft_row(c, kind)
    current_revision = row["revision"] if row else 0
    if revision is not None and revision != current_revision:
        raise Problem("stale_revision", f"The {TITLES[kind]} draft changed since it was read (revision "
                      f"{current_revision}); read it again and reapply the change", 409,
                      extra={"revision": current_revision})
    body = _loads(row["body_json"]) if row else None
    if not isinstance(body, dict):
        body = dict((published(c, kind) or {}).get("body") or {})
    body = _body(kind, body)
    for name, value in (sections or {}).items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    body = validate(kind, body)
    now = H.now()
    c.execute(f"INSERT INTO {kind}_drafts (id, body_json, revision, updated_by, updated_at) VALUES (1,?,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json, revision=excluded.revision, "
              "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
              (json.dumps(body, sort_keys=True), current_revision + 1, actor, now))
    H.event(c, actor, f"{kind}.draft", "draft", {"sections": sorted(sections or {})})
    return body


def publish(c, kind, actor, note=""):
    H._writer(c, actor)
    current = published(c, kind)
    pending = draft(c, kind, current)
    if not pending:
        raise Problem("no_draft", f"There is no {TITLES[kind]} draft to publish", 409)
    n = (current or {}).get("n", 0) + 1
    note = " ".join(str(note or "").split())[:500]
    c.execute(f"INSERT INTO {kind}_versions (id, n, body_json, note, published_by, published_at) VALUES (?,?,?,?,?,?)",
              (H.new_id(), n, json.dumps(pending["body"], sort_keys=True), note, actor, H.now()))
    c.execute(f"UPDATE {kind}_drafts SET body_json='null', revision=revision+1, updated_by=?, updated_at=? WHERE id=1",
              (actor, H.now()))
    H.event(c, actor, f"{kind}.publish", f"v{n}", {"n": n})
    H.hook(c, f"{kind}.published", f"v{n}", lambda: {"version": n, "note": note, "published_by": actor}, actor,
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
def proposal_error(c, kind, payload):
    """What is wrong with a `brand_draft` / `gtm_draft` proposal, in a sentence, or None."""
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict) or not sections:
        return f"a {kind}_draft proposal carries {{sections: {{name: value}}}} with at least one section"
    extra = sorted(set(payload) - {"sections", "source"})
    if extra:
        return f"a {kind}_draft proposal carries only sections, not {', '.join(extra)}"
    body = {k: v for k, v in sections.items() if v is not None}
    try:
        validate(kind, body)
    except Problem as exc:
        return exc.detail
    return None


def apply_proposal(c, kind, actor, payload):
    """A confirmed proposal, merged into the draft as the admin who confirmed it. Never published."""
    sections = _body(kind, payload.get("sections"))   # a proposal filed before a section was retired
    merge(c, kind, actor, sections)
    return {"sections": sorted(sections), "draft": True}


def kind_of_proposal(proposal_kind):
    return {"brand_draft": "brand", "gtm_draft": "gtm"}.get(proposal_kind)


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate):
    def reader(who):
        auth.domain(who)

    def editor(who, kind):
        auth.domain(who)
        if admin(auth, who):
            return
        if who.role == "bot":
            raise Problem("propose_instead", f"A bot proposes a change to the {TITLES[kind]} draft and an admin "
                          f"confirms it: hub proposal create --kind {kind}_draft", 403)
        raise Problem("forbidden", f"Only the owner or an admin writes the {TITLES[kind]} draft and publishes it", 403)

    for kind in KINDS:
        _routes(app, store, auth, mutate, kind, reader, editor)

    @app.post("/api/v2/brand/check")
    def brand_check(request: Request, body: M.PageCheck):
        """Every brand rule the text breaks: avoided words, wrong name spellings, claims the brand never makes."""
        reader(request.state.identity)
        with store.read() as c:
            current = published(c, "brand")
        return {"n": (current or {}).get("n", 0), "hits": check((current or {}).get("body") or {}, body.text)}


def _routes(app, store, auth, mutate, kind, reader, editor):
    base = f"/api/v2/{kind}"

    def show(request: Request):
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return view(c, kind, admin(auth, who))

    def one_version(request: Request, n: int):
        reader(request.state.identity)
        with store.read() as c:
            row = published(c, kind, n)
        if not row:
            raise Problem("not_found", f"There is no {TITLES[kind]} version {n}", 404)
        return row

    def summary(request: Request):
        from .goal_routes import _hidden_owners
        who = request.state.identity
        reader(who)
        with store.read() as c:
            current = published(c, kind)
            return {"n": (current or {}).get("n", 0),
                    "text": summary_text(c, kind, current, _hidden_owners(c, auth, who))}

    def write_draft(request: Request, body: M.PageDraft):
        who = request.state.identity

        def work(c):
            editor(who, kind)
            merge(c, kind, who.actor, body.sections, body.revision)
            return view(c, kind, True)
        return mutate(request, body, work)

    def do_publish(request: Request, body: M.PagePublish):
        who = request.state.identity

        def work(c):
            editor(who, kind)
            publish(c, kind, who.actor, body.note)
            return view(c, kind, True)
        return mutate(request, body, work)

    title = TITLES[kind]
    show.__doc__ = f"The published {title} page, its versions, and for an admin the draft."
    summary.__doc__ = f"The {title} page in about 300 characters, as growth-facing bots read it: {{n, text}}."
    write_draft.__doc__ = (f"Owner or admin: merge sections into the {title} draft (null removes one). A bot proposes "
                           f"instead (kind {kind}_draft).")
    do_publish.__doc__ = f"Owner or admin: the {title} draft becomes the next version."
    for fn in (show, one_version, summary, write_draft, do_publish):
        fn.__name__ = f"{kind}_{fn.__name__}"
    app.get(base)(show)
    app.get(base + "/versions/{n}")(one_version)
    app.get(base + "/summary")(summary)
    app.put(base + "/draft")(write_draft)
    app.post(base + "/publish")(do_publish)
