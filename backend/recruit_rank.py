"""The org builder's catalog and its local recommender: which bots to suggest for one department.

Standard library only, because the same file runs in two places: the Tico server (backend/recruit.py, the answer when
Tico HQ is off or unreachable) and Tico HQ (`POST /v1/recruit`, the answer when its model is capped or not configured).
HQ's code is in ticoteam/tico-services, which copies this file and builds its catalog with `build()` from a published Tico
release (`scripts/build_catalog_json.py --out DIR`), so both sides agree on `catalog_version`.

`build(departments, cards)` turns templates/groups.yaml and the card.yaml files (already parsed) into
{version, departments, cards}. `rank(catalog, department, briefing, about)` answers {bots: [{template_id, why}],
suggested_default: [template_id]} with template ids from that department only, never free text a bot would follow.
"""
import hashlib
import json
import re

DEPARTMENT_IDS = ("sales", "marketing", "support", "finance", "operations", "legal", "hr", "product", "engineering")
# A card written before departments existed has only a `pack`; this is the department it sits in.
PACK_DEPARTMENT = {"sales": "sales", "marketing": "marketing", "support": "support", "operations": "operations",
                   "engineering": "engineering", "basics": "operations", "finance": "finance", "legal": "legal",
                   "hr": "hr", "product": "product"}
SUGGEST = ("default", "common", "niche")
MAX_BOTS = 8
WHY_LIMIT = 140
BRIEFING_LIMIT = 500
# A tag of up to this many words, said whole, is a why; a longer `pains` phrase only scores.
REASON_WORDS = 3
STOP_WORDS = frozenset("""a an and are as at be but by can do does for from get gets had has have how i if in into is it
its just like more most much my no not of on one or our out over so than that the their them then there these they this
those to too up us was we were what when where which who why will with without you your really still every again things
thing going about people lot lots some any all via per etc""".split())


def _text(value, limit=400):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _first_sentence(text, limit=160):
    first = re.split(r"(?<=[.!?])\s", _text(text, 2000), maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _words(text):
    """(stem, the word as written) for each meaningful word, in order."""
    out = []
    for word in re.findall(r"[a-z0-9@']+", str(text or "").lower().replace("'", "")):
        if len(word) > 2 and word not in STOP_WORDS:
            out.append((_stem(word), word))
    return out


def _stems(text):
    return {stem for stem, _ in _words(text)}


def _strings(value, limit=24):
    return [_text(item, 80) for item in value if _text(item, 80)][:limit] if isinstance(value, list) else []


def _department_of(card, heads, teams):
    wanted = str(card.get("group") or card.get("department") or "").strip()
    if wanted:
        # A department the builder does not offer (the Leadership extra) keeps its cards out of every department.
        return wanted if wanted in DEPARTMENT_IDS else ""
    template = str(card.get("template") or "")
    if template in heads:
        return heads[template]
    if template in teams:
        return teams[template]
    return PACK_DEPARTMENT.get(str(card.get("pack") or ""), "")


def department(document):
    """One department as served: every field present, nothing else."""
    return {"id": _text(document.get("id"), 40), "name": _text(document.get("name"), 60),
            "description": _text(document.get("description"), 160), "goal": _text(document.get("goal"), 160),
            "question": _text(document.get("question"), 160), "placeholder": _text(document.get("placeholder"), 160),
            "icon": _text(document.get("icon"), 40), "head": _text(document.get("head"), 80),
            "software_only": bool(document.get("software_only"))}


def build(departments, cards):
    """The org builder's catalog from parsed groups.yaml and card.yaml documents. Built-in (`required`) cards,
    helpers (`kind: helper`, which serve a person and sit outside the org chart) and cards in no offered department are
    left out. A card with no `icon` takes its department's; with no `suggest`, it is `common`; with no `tags`, its
    `pains` phrases stand in. A department's head is its card with `lead: true`, else the `head` groups.yaml
    names; the served `head` and each card's `lead` say which."""
    rows = departments.get("departments") if isinstance(departments, dict) else departments
    known = [department(row) for row in (rows or []) if isinstance(row, dict)]
    known = [row for row in known if row["id"] in DEPARTMENT_IDS]
    heads = {row["head"]: row["id"] for row in known if row["head"]}
    teams = {}
    for card in cards:
        home = heads.get(str(card.get("template") or "")) or str(card.get("group") or card.get("department") or "")
        for member in (card.get("team_templates") or []) if home else []:
            teams.setdefault(str(member), home)
    by_id = {row["id"]: row for row in known}
    out = []
    for card in sorted(cards, key=lambda card: str(card.get("template") or "")):
        template = str(card.get("template") or "").strip()
        if not template or card.get("required") or str(card.get("kind") or "") == "helper":
            continue
        home = _department_of(card, heads, teams)
        if home not in by_id:
            continue
        suggest = str(card.get("suggest") or "").strip()
        when = set(_strings(card.get("recommend_when")))
        out.append({"template": template, "name": _text(card.get("name") or template, 100), "department": home,
                    "icon": _text(card.get("icon"), 40) or by_id[home]["icon"],
                    "tags": _strings(card.get("tags")) or _strings(card.get("pains")),
                    "suggest": suggest if suggest in SUGGEST else "common",
                    "summary": _first_sentence(card.get("summary")),
                    "lead": bool(card.get("lead")),
                    "business_only": "sells_to_businesses" in when and "sells_to_consumers" not in when})
    for row in known:
        members = [card for card in out if card["department"] == row["id"]]
        leads = [card["template"] for card in members if card["lead"]]
        row["head"] = leads[0] if leads else row["head"] if any(card["template"] == row["head"] for card in members) else ""
        for card in members:
            card["lead"] = card["template"] == row["head"]
    body = {"departments": known, "cards": out}
    version = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return {"version": version, **body}


def head_of(catalog, department_id):
    """The department's head: its `lead` card (build() settles it, and the department's `head` agrees)."""
    return next((card["template"] for card in catalog["cards"] if card["department"] == department_id and card["lead"]), "")


def _match(card, found, common=frozenset()):
    """How well the answer fits this card (a tag, then a word of its name, then two or more words of its summary) and the
    reasons to show for it, as the person wrote them. A reason is a short tag of two or more words the answer says whole
    ("meetings booked"), unless every word of it is `common` in the department. A single word, a word of the name, or
    two words picked out of a summary or a long `pains` phrase only score: as a why they read as keyword matching
    ("Matches “managers”" on four Sales cards). Returns (score, reasons)."""
    stems = {stem for stem, _ in found}
    said = {}
    for stem, word in found:
        said.setdefault(stem, word)
    score, reasons = 0, []
    for tag in card["tags"]:
        # A short tag ("cold email") matches whole; a longer phrase (a card's `pains` sentence) on any two of its words.
        wanted = list(dict.fromkeys(stem for stem, _ in _words(tag)))
        matched = [stem for stem in wanted if stem in stems]
        if wanted and len(matched) >= min(len(wanted), 2):
            score += 3 * len(matched)
            if 2 <= len(wanted) <= REASON_WORDS and matched == wanted and not set(wanted) <= common:
                reasons.append(" ".join(said[stem] for stem in wanted))
    score += 2 * len(_stems(card["name"]) & stems)
    shared = _stems(card["summary"]) & stems
    if len(shared) >= 2:
        score += len(shared)
    return score, list(dict.fromkeys(reasons))


def _common(cards):
    """The words most of a department's cards carry in their tags ("github" on 9 of 11 in Engineering, "b2b" in Sales):
    an answer that says one fits them all, so it is no reason to pick any one of them."""
    counts = {}
    for card in cards:
        for stem in {stem for tag in card["tags"] for stem, _ in _words(tag)}:
            counts[stem] = counts.get(stem, 0) + 1
    return frozenset(stem for stem, n in counts.items() if len(cards) > 1 and 2 * n >= len(cards))


def rank(catalog, department_id, briefing="", about=None, limit=MAX_BOTS):
    """The suggestions for one department: its head, its `default` and `common` cards and any `niche` card the answer
    names, the head first and then the best matches. Words in the briefing count double those in "What you do". A
    card written for business customers sinks when the company sells only to consumers. Deterministic: the same
    answer gives the same list."""
    about = about if isinstance(about, dict) else {}
    dept = next((row for row in catalog["departments"] if row["id"] == department_id), None)
    if not dept:
        return {"bots": [], "suggested_default": []}
    head = head_of(catalog, department_id)
    # The department's own name matches every card in it ("support" in Customer Support), so it says nothing.
    own = _stems(dept["name"] + " " + dept["id"])
    briefing_words = [row for row in _words(_text(briefing, BRIEFING_LIMIT)) if row[0] not in own]
    about_words = [row for row in _words(_text(about.get("what"), BRIEFING_LIMIT)) if row[0] not in own]
    consumers = str(about.get("sells_to") or "") == "consumers"
    members = [card for card in catalog["cards"] if card["department"] == department_id]
    common = _common(members)
    scored = []
    for card in members:
        said, hits = _match(card, briefing_words, common)
        context, more = _match(card, about_words, common)
        text = 2 * said + context
        if card["template"] != head and card["suggest"] == "niche" and text < 3:
            continue
        # The head, then everything else by how well the answer fits (a match outweighs being a default), then
        # a business-only card when the company sells only to consumers.
        tier = 0 if card["template"] == head else 2 if consumers and card["business_only"] else 1
        score = 10 * text + {"default": 30, "common": 10}.get(card["suggest"], 0)
        if card["template"] == head:
            why = "Heads " + dept["name"] + " and reports to you"
        elif hits or more:
            why = "Matches “" + "”, “".join((hits + [m for m in more if m not in hits])[:2]) + "”"
        elif card["suggest"] == "default":
            why = "A starting point for " + dept["name"]
        elif card["suggest"] == "common":
            why = "Common in " + dept["name"]
        else:                               # a niche card the answer brought in on single words: no why line
            why = ""
        scored.append((tier, -score, card["name"].lower(), card["template"], why[:WHY_LIMIT]))
    scored.sort()
    bots = [{"template_id": template, "why": why} for _, _, _, template, why in scored[:limit]]
    chosen = {row["template_id"] for row in bots}
    defaults = [head] if head in chosen else []
    defaults += [card["template"] for card in catalog["cards"] if card["department"] == department_id
                 and card["suggest"] == "default" and card["template"] in chosen and card["template"] != head]
    return {"bots": bots, "suggested_default": defaults}
