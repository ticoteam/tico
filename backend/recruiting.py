"""Recruiting a seat (docs/onboarding.md, "Recruiting a seat"; bot-lifecycle-spec.md section 2, acceptance 6 to 9).

A Recruiting seat (backend/lifecycle.py) is filled in three steps, the same from the org chart and from the Team plus
menu's Post a job, which creates the seat first:

1. **Post it.** `draft` writes a job description from the title at once, on this server: the catalog card that is
     the job the title names (its name shares a word of work with the title, never only "Engineer" or "Manager"), as
     one line from its summary, what it owns and the tools it works in. With `redraft` and a Gemini key, one model call
     rewrites that text in under five seconds; without either, the composed text is the answer. When no card is the
     job, that one call writes the description from the title alone, or the box stays empty. Nothing is stored until the person posts the job (`POST /v2/seats`), and anyone who may change
     the seat edits it later (lifecycle `_may_place`).
2. **Recruit.** `candidates` answers at once, on this server, from the template catalog: up to eight Tico templates,
     the best fit to the seat's title and job description first, each with one line, what it does on day one, what it
     needs and how well its name fits the title (`match`). No trial task and no outside service.
3. **Pick.** `hire` fills the seat: the bot is created in Onboarding, in the seat's place on the chart with its
     reporting line, under the same rules as any bot the person adds (backend/settings_admin.py `create_bot`). Its
     repository comes only after that: a starter's computer builds it from the bot's definition, any other template's
     BotOps does on the "Set up" task it gets now. The seat is then closed, filled by that bot."""
import json
import re
import socket
import threading
import time
from dataclasses import replace
from urllib.request import Request, urlopen

from clients import gateway as G

from . import hubdb as H
from . import lifecycle
from . import models as M
from . import releases
from . import recruit_rank as R
from .store import Problem

CANDIDATES = 8
CHART = "chart"                         # `description_by` for a chart model's text
DRAFT_OWNS = 5                          # bullets under "What you'll do"
REWRITE_SECONDS = 5                     # the whole rewrite: key, request and answer
REWRITE_BYTES = 64 * 1024               # the most of the model's answer read
REWRITE_CHUNK = 8 * 1024                # read in pieces, so the deadline is checked as the answer arrives
_REWRITES = threading.BoundedSemaphore(2)   # rewrites at once; a third keeps the composed text instead of queueing
REWRITE_MODEL = "gemini-3.7-flash"
# With no Gemini key (a hosted install), the Tico AI Gateway's General · Low tier at version 1.0 (Claude Haiku 5.5, the
# cheapest general chat tier; backend/gateway_tiers.py). Pinned, so a later version never changes what a draft runs on.
GATEWAY_REWRITE_MODEL = "tico-general-low-1.0"
# Gateway models reason before they answer, and the reasoning counts against the cap: 400 left a rewrite (the longer
# prompt) with no answer at all. The least reasoning, and room for it plus a 150-word answer.
GATEWAY_MAX_TOKENS = 2000
GATEWAY_EFFORT = "low"
# The tools a card's `prerequisites` name, as a person reads them. Tico's own tasks, docs and the open web need nothing
# set up, so a candidate needing them has them; any other tool is set up on hire (required) or later (optional).
TOOL_LABELS = {"hub": "Tico tasks", "docs": "Docs", "web": "Web", "mail": "Email", "calendar": "Calendar",
               "meetings": "Meetings", "github": "GitHub", "crm": "CRM", "chat": "Chat"}
BUILT_IN_TOOLS = frozenset({"hub", "docs", "web"})
# Words of rank, not of the work: "Support lead" and "Engineering lead" share no job.
RANKS = frozenset(stem for word in ("lead", "head", "manager", "senior", "junior", "chief", "director", "specialist",
                                    "associate", "officer", "staff", "principal") for stem, _ in R._words(word)) | {
    "vp", "svp", "evp", "sr", "jr", "ii", "iii", "iv"}      # short ranks and levels: "VP, Product Marketing", "Designer II"
# Words naming a kind of job, not its work, as written: "Rust engineer" and "QA Engineer" share no work, and neither do
# "Data analyst" and "Pricing Analyst". "Engineering" is a field of work and stays.
ROLE_WORDS = frozenset("""engineer engineers developer developers analyst analysts coordinator coordinators administrator
administrators agent agents assistant assistants rep reps representative representatives generalist generalists
consultant consultants writer writers designer designers producer producers accountant accountants scientist
scientists programmer programmers""".split())


# The only small words a job's name holds that say nothing: "Head of People" keeps "People", which the recommender's
# stop words drop from free text.
NAME_STOP = frozenset("of and the for to in on at with by a an".split())


def _name_stem(word):
    """A plural is its singular first ("Advocates" is "Advocate"), so both read the same to the recommender's stem."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return R._stem(word)


# Words that make a title a department head's: its card is the department's `lead`.
HEAD_WORDS = frozenset("head chief director vp".split())


# A job named in full and by its letters is one job, in a title and in a template's name alike: "Human Resources
# Generalist" is the HR Generalist, "SRE" the Site Reliability Engineer. The letters go in front of the words, which
# stay ("Site Reliability Manager" still says "reliability").
SHORT = ((r"human resources?", "hr"), (r"financial planning", "fp"), (r"public relations", "pr"),
         (r"quality assurance", "qa"), (r"user experience", "ux"), (r"information technology", "it"),
         (r"search engine optimi[sz]ation", "seo"), (r"site reliability", "sre"), (r"sales development", "sdr"))


LETTERS = frozenset(short for _, short in SHORT)


# A job's other names, said as the catalog says the job: "CMO" is the Chief Marketing Officer, "SDET" the QA Engineer,
# "PPC Specialist" the Paid Media Manager, "Content Writer" the Content Marketer's work, "Communications" PR's. Applied
# before SHORT, to a title and a template's name alike.
ALIASES = tuple((re.compile(pattern), short) for pattern, short in (
    (r"\bcmo\b", "chief marketing officer"), (r"\bcto\b", "chief technology officer"),
    (r"\bcfo\b", "chief finance officer"), (r"\bcoo\b", "chief operations officer"),
    (r"\bcro\b", "chief revenue officer"), (r"\bpmm\b", "product marketing manager"),
    (r"\bsoftware (?:development )?engineer in test\b|\bsdets?\b", "qa engineer"),
    (r"\bsoftware development\b", "software"),
    (r"\b(?<!qa )test(?:ing|ers?)?\b", "qa \\g<0>"), (r"\bappsec\b", "application security"),
    (r"\b(?:developer relations|devrel)(?:\s+(?:engineer|manager|lead|specialist)s?)?\b", "developer advocate"),
    (r"\b(?:ppc|sem|paid search|paid social|paid acquisition|user acquisition|performance marketing)\b", "paid media"),
    (r"\bmarketers?\b", "marketing"),
    (r"\b(?:content|copy|blog)\s*writers?\b|\bcopywriters?\b", "content writing"),
    (r"\bcommunications?\b", "pr \\g<0>"), (r"\b(?<!code )(?:online )?reviews?\b", "reputation \\g<0>"),
    (r"\bmachine learning\b", "ml"), (r"\bdata scien(?:ce|tists?)\b", "\\g<0> analytics"), (r"\baccounts? payable\b", "payable"),
    (r"\baccounts? receivable\b", "receivable"), (r"\bops\b", "operations"),
    (r"\b(?:lifecycle|retention|crm) marketing\b", "email marketing \\g<0>"),
    (r"\bfield marketing\b", "event \\g<0>"), (r"\bmarketing analy(?:st|tics)\b", "marketing operations \\g<0>"),
    (r"\b(?:build|ci(?:/cd)?|continuous (?:integration|delivery|deployment)|platform|infrastructure|cloud)\b(?= engineer)",
     "devops \\g<0>"),
    (r"\bcode review(?:ers?|\s+engineers?)\b", "senior software engineer"),
    (r"\b(?:documentation|docs) (?:engineer|writer|specialist)s?\b", "technical writer")))


def _expand(text):
    """A title or a template's name in lower case, with its other names said as the catalog says them (ALIASES)."""
    text = re.sub(r"\bvice[\s-]+president\b", "vp", str(text or "").lower().replace("'", ""))
    for pattern, short in ALIASES:
        text = pattern.sub(short, text)
    return text


# Words of a field so wide that sharing one names no job: "Influencer Marketing Manager" is not the Head of Marketing,
# nor "Software Engineer in Test" the Software Architect. A title made only of these must be a template's whole work
# ("Product Marketing Manager", "Software Engineer", "Engineering Manager").
GENERIC = frozenset(_name_stem(word) for word in "marketing product software engineering technology tech digital".split())

# Words that pin a title to a department: "Partner Marketing Manager" is Marketing's, never the HR Business Partner.
PINS = {"marketing": "marketing", "engineering": "engineering", "sales": "sales", "legal": "legal",
        "finance": "finance", "product": "product", "hr": "hr"}

# The department a title's words point to when no template is the job, the last such word winning: "Rust engineer"
# and "Growth Engineer" are Engineering's, "Product Designer" Product's. Applicants from it come first.
DEPARTMENT_WORDS = {word: department for department, words in {
    "engineering": "engineer engineering developer software programmer architect devops sre qa technology ml backend "
                   "frontend fullstack ios android mobile code platform infrastructure tech",
    "marketing": "marketing brand seo content social pr growth campaign campaigns influencer affiliate lifecycle demand",
    "product": "product designer ux data", "sales": "sales sdr", "finance": "finance accountant bookkeeping",
    "legal": "legal counsel paralegal", "hr": "hr people recruiter recruiting", "support": "support",
    "operations": "operations ops"}.items() for word in words.split()}



# How a job is held, not what it is: a qualifier made only of these words names no work.
HELD = re.compile(r"(?:(?:contract(?:or)?|to|hire|remote|hybrid|on[\s-]?site|in[\s-]office|temporary|temp|part|full|"
                  r"time|freelance|interim|fractional|permanent|intern(?:ship)?|us|usa|eu|uk|emea|apac|amer|americas|"
                  r"latam|anz|global|worldwide|nyc|sf|\d+)[\s/-]*)+")


def _name_words(text):
    """(stem, word) for each word of a job's name, two letters and up: "QA", "HR" and "UX" name the work."""
    text = _expand(text)
    # A qualifier after a comma, dash or in brackets names the work ("Software Engineer, Security", "Analyst
    # (Pricing)", "Manager, Engineering") unless it says how the job is held ("UX Designer (Contract)", "Rust engineer
    # - remote"). The work goes in front, so the job's last word still names its kind.
    brackets = re.findall(r"\(([^)]*)\)", text)
    parts = re.split(r"\s+[-\u2013\u2014|]\s+|,|\|", re.sub(r"\([^)]*\)", " ", text))
    work = [part for part in parts[1:] + brackets if part.strip() and not HELD.fullmatch(part.strip())]
    text = " ".join(work + [parts[0]])
    for full, short in SHORT:
        text = re.sub(rf"\b{full}\b", lambda found, short=short: f"{short} {found.group(0)}", text)
    text = re.sub(r"\baccounting\b", "bookkeeping", text)      # the books, not a client account ("Account Manager")
    text = re.sub(r"\bcustomer service\b", "support customer service", text)     # the Support Agent's work
    text = re.sub(r"\bemployee relations?\b", lambda found: f"hr {found.group(0)}", text)    # HR's, not investors'
    words = re.findall(r"[a-z0-9]+", text)
    return [(_name_stem(word), word) for word in words if len(word) > 1 and word not in NAME_STOP]


# The words a department head's title may use for the department's field beyond its id ("Head of Human Resources").
FIELDS = {"hr": "hr human resources people", "support": "support customer service",
          "operations": "operations ops", "product": "product", "legal": "legal", "finance": "finance",
          "marketing": "marketing growth", "sales": "sales revenue", "engineering": "engineering technology"}


def _kind(title):
    """The kind of job the title's last word names ("engineer" in "Rust engineer"), as a stem, or "" when it names none
    ("Developer relations", "Head of Sales")."""
    words = [word for stem, word in _name_words(title) if stem not in RANKS]
    if not words or words[-1] not in ROLE_WORDS:
        return ""
    # A rep is a representative, and a developer the same kind of job as an engineer (the catalog names engineers).
    return {"rep": "representative", "reps": "representative", "developer": "engineer",
            "developers": "engineer"}.get(words[-1]) or _name_stem(words[-1])


# Kinds of job that write code: a cloud coding agent is an applicant for one in Engineering.
CODING_KINDS = frozenset({R._stem("engineer"), R._stem("programmer")})


# Kinds of job that are one job: a Support Representative is the Support Agent.
SAME_KIND = {stem: {_name_stem("agent"), _name_stem("representative")} for stem in (_name_stem("agent"),
                                                                                    _name_stem("representative"))}


def _work(title):
    """The stems of the title's words of work: not of rank, and not the kind of job its last word names ("Rust
    engineer" is "Rust"; "Developer relations" keeps "Developer")."""
    words = [(stem, word) for stem, word in _name_words(title) if stem not in RANKS]
    if _kind(title):
        words = words[:-1]
    return {stem for stem, _ in words}


# ------------------------------------------------------------------ the seat as the recruiting page shows it
def _may_edit(auth, c, who, row):
    try:
        lifecycle._may_place(auth, c, who, row["reports_to"])
        return True
    except Problem:
        return False


def view(auth, c, who, row):
    seat = lifecycle._seat(row)
    by = str(row["description_by"] or "")
    seat.update(description_by="person" if by.startswith("human:") or by == lifecycle.EARLIER else "chart",
                can_edit=_may_edit(auth, c, who, row))
    return seat


def show(auth, c, who, seat_id):
    return view(auth, c, who, lifecycle.seat(c, seat_id))


def end_draft(c, row, why):
    """Close a draft task an earlier release asked the Strategy Bot for, once the seat is filled, closed or written by a
    person: nothing drafts a seat's description in the background any more."""
    task = H.task(c, row["draft_task"]) if row["draft_task"] else None
    if task and task["status"] in H.ACTIVE_STATUSES:
        H.task_update(c, task["owner"], task["id"], status="declined", note=why[:500])


# ------------------------------------------------------------------ 1. the job description, drafted from the title
def _tool(tool):
    return TOOL_LABELS.get(tool, tool.replace("-", " ").replace("_", " ").capitalize())


def _title(title):
    """What matching a template to a title needs from the title, worked out once."""
    named = _name_words(title)
    words = {word for _, word in named}
    work = _work(title)
    pins = {PINS[word] for word in words if word in PINS}
    kinds = [word for stem, word in named if stem not in RANKS]    # "Engineer II", "Engineers": the engineer
    kind_department = (DEPARTMENT_WORDS.get(kinds[-1]) or DEPARTMENT_WORDS.get(kinds[-1].removesuffix("s"), "")) if kinds else ""
    if pins and kind_department:
        pins.add(kind_department)           # "Product Security Engineer" is an engineer: Engineering's cards may be it
    return {"words": R._words(title), "work": work, "specific": work - GENERIC, "stems": {s for s, _ in named} - RANKS,
            "heads": bool(words & HEAD_WORDS), "kind": _kind(title), "pins": pins,
            "departments": {DEPARTMENT_WORDS[word] for word in words if word in DEPARTMENT_WORDS}}


def _ranked(card):
    """Whether the card's name ends in a word of rank ("Release Manager", "Tax Specialist"), not a kind of job ("Account
    Executive", "UX Researcher") nor a head's."""
    named = _name_words(card["name"])
    return bool(named) and named[-1][0] in RANKS and not card.get("lead")


def _fit(card, t):
    """How much of the title's work the card's name holds, 0 when the card is not the job (see _best_card)."""
    if not t["work"] or (t["pins"] and card["department"] not in t["pins"]):
        return 0
    name = {stem for stem, _ in _name_words(card["name"])}
    kind = t["kind"]
    ranked_here = _ranked(card) and name & t["specific"] and (not t["departments"] or card["department"] in t["departments"])
    if kind and not SAME_KIND.get(kind, {kind}) & name and not ranked_here:
        # A designer is not the UX Writer, an engineer not a specialist; a job of one kind may be a template named by
        # rank on the work itself ("Release Engineer" is the Release Manager, "Social Media Coordinator" the Social
        # Media Manager), never on a field ("Product Designer" is not the Product Manager) nor a head's, and only in the
        # departments its words name ("Software Development Engineer" is not the Learning and Development Specialist;
        # "Brand Designer" may be Marketing's Brand Manager).
        return 0
    if t["heads"] and card.get("lead"):
        # A head's title names the field it heads, which the head's card may not: "Head of HR" is the Head of People,
        # the head of the HR department. Only a title that is all field: "Director of Revenue Recognition" heads
        # revenue recognition, not Sales.
        field = {stem for stem, _ in _name_words(FIELDS.get(card["department"], card["department"]))}
        if t["work"] <= field:
            name |= field
    if t["specific"]:
        if not name & t["specific"]:
            return 0                        # "Influencer Marketing Manager" shares only "Marketing" with Marketing's cards
    elif not (t["heads"] and card.get("lead")) and _work(card["name"]) != t["work"]:
        return 0                            # "Digital Marketing Manager" is no template's whole work
    # A job's letters are a whole name ("PR" is Public Relations), so they outweigh one of its words ("Relations").
    return sum(2 if stem in LETTERS else 1 for stem in name & t["work"])


def _best_card(cards, title, team=""):
    """The catalog card that is the job the title names, or None when none is: its name must share a word of work with
    the title ("Support" in "Customer support lead", not "Engineer" in "Rust engineer", nor a tag like "software" on
    the Product Manager), more than a word of a whole field ("Marketing" in "Influencer Marketing Manager": GENERIC),
    and in the department the title names, if any (PINS). Among those, the one whose name and tags fit best; a card in
    the department the seat's group is named for wins a tie."""
    best = _ranked_fits(cards, title, team)
    return best[0][1] if best else None


def _ranked_fits(cards, title, team=""):
    """Every card that is the job the title names, best first, as (key, card); the key's first five are how well the
    name fits (see _best_card), on which a tie is as good a fit."""
    t = _title(title)
    common = R._common(cards)
    group = str(team or "").strip().lower()
    out = []
    for card in cards:
        shared = _fit(card, t)
        if not shared:
            continue
        name = {stem for stem, _ in _name_words(card["name"])}
        score, _ = R._match(card, t["words"], common)
        # The same kind of job first ("Sales rep" is the Sales Development Representative, not the Sales Manager), then
        # a name that is all of the title's work and no more ("Account Manager", not "Accounts Payable Specialist"), then
        # more of the work in the name ("Legal Operations Manager" for "Head of Legal Operations", not the head of
        # Legal), then a head's card for a head's title, then more of the title: "Security Engineer", not "Security and
        # Compliance Analyst".
        same_kind = bool(t["kind"]) and bool(SAME_KIND.get(t["kind"], {t["kind"]}) & name)
        # A title of no kind is first a template of none: "Product manager" is the Product Manager, not the Product
        # Analyst.
        plain = not t["kind"] and not _kind(card["name"])
        # A head's card is all of a head's title when the title is all its field ("Head of HR" is the Head of People).
        exact = _work(card["name"]) == t["work"] or (t["heads"] and bool(card.get("lead")) and t["work"] <= {
            stem for stem, _ in _name_words(FIELDS.get(card["department"], card["department"]))})
        # A head's card for a head's title, and for another only when no other card is as good ("Marketing manager" is
        # the Head of Marketing's work, "Engineering Manager" the Engineering Manager's).
        role = (2 if t["heads"] else 0) if card.get("lead") else 1
        key = ((same_kind, exact, shared, plain, role, len(name & t["stems"]), score),
               card["department"] == group, -len(card["name"]), card["template"])
        out.append((key, card))
    out.sort(key=lambda item: item[0], reverse=True)
    return out


def _department_hint(title):
    """The department the title's words point to, the last such word winning (DEPARTMENT_WORDS), or ""."""
    found = [DEPARTMENT_WORDS[word] for _, word in _name_words(title) if word in DEPARTMENT_WORDS]
    return found[-1] if found else ""


# What a card's bullet says for the bot's own files and limits, not for the person reading the job: "(knowledge/labels.md)",
# ", in knowledge/company.md" and "within the requested work and Tools" go.
_FILE = r"(?:knowledge|reports|docs|_[\w-]+)/[\w./-]*|[\w./-]*\.(?:md|json|ya?ml|csv)\b"
_PLAIN = re.compile(rf"\s*\((?:[^()]*?(?:{_FILE})[^()]*?)\)|,?\s+in\s+(?:{_FILE})\s*$"
                    r"|,?\s*(?:prepared\s+)?(?:to apply\s+)?within the requested work(?:\s+and\s+tools)?", re.I)


def _plain(item, limit=160):
    """One bullet of what a card owns, as a job description says it (see _PLAIN), cut to `limit`."""
    return R._text(_PLAIN.sub("", str(item or "")).strip(" ,"), limit)


def compose(card):
    """One line from the card's summary, what it owns as "What you'll do", and the tools it works in."""
    raw = card["raw"]
    lines = [R._first_sentence(raw["summary"] or card["summary"], 200)]
    owns = [_plain(item) for item in raw["owns"][:DRAFT_OWNS] if _plain(item)]
    if owns:
        lines += ["", "What you'll do", *("- " + item for item in owns)]
    tools = list(dict.fromkeys(_tool(row["tool"]) for row in raw["prerequisites"]))
    if tools:
        lines += ["", "Works in: " + ", ".join(tools)]
    return "\n".join(lines).strip()[:lifecycle.DESCRIPTION_MAX]


def boss_name(auth, c, who, reports_to):
    """Who a draft's seat would report to, by name: a person's, a bot's or another seat's title, but only one the caller
    could post under and, for a bot, may see. Anyone else is left out (""), so a draft never carries a name the caller
    may not read."""
    reports = str(reports_to or "").strip()
    if not reports:
        return ""
    try:
        reports = lifecycle._reports_to(c, reports)
        lifecycle._may_place(auth, c, who, reports)
    except Problem:
        return ""
    if reports.startswith("human:"):
        return (H.human(c, reports[6:]) or {}).get("name") or ""
    if reports.startswith(lifecycle.SEAT_PREFIX):
        row = c.execute("SELECT title FROM seats WHERE id=?", (reports[len(lifecycle.SEAT_PREFIX):],)).fetchone()
        return row["title"] if row else ""
    if not auth.bot_accesses(c, who, [reports]).get(reports, {}).get("see"):
        return ""
    return (H.bot(c, reports) or {}).get("display_name") or ""


def may_draft(who):
    """Only a person drafts a job: whoever may post one (the owner, or a person under themselves)."""
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a person drafts a job description", 403)


def _rewrite_prompt(text, title, boss):
    """The instructions a rewrite is asked with: the composed text to rewrite for this role, or, with no template that is
    the job, the role written from its title. The same words go to Gemini and to the Tico Gateway."""
    role = f"the role \"{title}\"" + (f", reporting to {boss}" if boss else "")
    # The catalog's own voice: a template's summary has no subject ("Reviews every open pull request..."), so the
    # opening line never says "I", "You" or "AI teammate", nor repeats the title the page already shows.
    rules = ("Start the opening line with a verb and no subject, like \"Owns the Rust services that...\"; never write "
             "\"I\", \"you\", \"we\" or \"AI teammate\" and never repeat the job title. Plain words, under 150 words, no "
             "other headings, and never name a template, a model or a vendor. Answer with the text only.")
    if text:
        return (f"Rewrite this job description for {role}. Keep its shape: one opening line, then \"What you'll do\" "
                "with three to five short bullets starting with \"- \", then one \"Works in:\" line. " + rules
                + "\n\n" + text)
    # No template is this job: written from the title alone, in the same shape less the tools, which nobody named.
    return (f"Write a job description for an AI teammate in {role}. Its shape: one opening line saying what it "
            "owns, then \"What you'll do\" with three to five short bullets starting with \"- \", each a concrete "
            "piece of work it delivers. " + rules)


def _answer(request, opener, deadline):
    """The bytes of the answer to `request`, opened with `opener` (urlopen, or the Gateway's transport) and read until
    `deadline` (a monotonic time): each socket wait is at most the time left, and the answer is read in pieces with the
    deadline checked between them. It raises when the time is up or the answer is longer than REWRITE_BYTES."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the request came too late")
    raw = b""
    with opener(request, timeout=left) as response:
        # A server that keeps trickling bytes never lets a read time out, so the connection is cut at the deadline,
        # which ends any read waiting on it; each read also returns whatever has arrived and waits no longer than the
        # time left.
        sock = _socket_of(response)
        cut = threading.Timer(max(deadline - time.monotonic(), 0), _cut, (sock,))
        cut.daemon = True
        cut.start()
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("the answer came too slowly")
                if getattr(response, "isclosed", lambda: False)():
                    break                   # the whole body has arrived and http.client closed its reader
                if sock is not None and sock.fileno() != -1:
                    sock.settimeout(left)
                want = min(REWRITE_CHUNK, REWRITE_BYTES + 1 - len(raw))
                chunk = response.read1(want) if hasattr(response, "read1") else response.read(want)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > REWRITE_BYTES:
                    raise ValueError("the model's answer is too long")
        finally:
            cut.cancel()
    return raw


def _gateway_answer(base, key, body, deadline):
    """The Tico AI Gateway's answer to one chat request (`<base>/chat/completions`, OpenAI chat shape, not streamed), as
    the parsed JSON. Sent with Tico's user agent and never through a redirect (clients/gateway.py `_send`), and read
    under the same deadline as Gemini's answer. Its own function, so a test can answer without the network."""
    request = Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                               "Accept": "application/json"})
    return json.loads(_answer(request, G._send, deadline))


def _rewrite_call(text, title, boss, deadline, gateway=None):
    """One model call that rewrites the composed description for this role, given up at `deadline` (a monotonic time):
    Gemini when the server has a key, else the Tico AI Gateway with the company's gateway key (`gateway`: its key and
    base URL, from backend/gateway_connection.py `target`, or a function that reads them, called only here, inside the
    time bound, so a draft that needs no model never reads the vault). No request starts once the deadline has passed, and each
    socket wait is at most the time left. It raises when there is neither key, the call fails or time is up."""
    from clients.docs_qa import NoKey, key
    try:
        secret = key()
    except Exception:  # noqa: BLE001 - no Gemini key (NoKey) or it could not be read: the Gateway may still answer
        secret = ""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the key came too late")
    prompt = _rewrite_prompt(text, title, boss)
    if secret:
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        request = Request(f"https://generativelanguage.googleapis.com/v1beta/models/{REWRITE_MODEL}:generateContent",
                          data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": secret})
        parts = json.loads(_answer(request, urlopen, deadline))["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
    target = gateway() if callable(gateway) else gateway
    if time.monotonic() >= deadline:
        raise TimeoutError("the gateway key came too late")
    if target and target[0]:
        body = {"model": GATEWAY_REWRITE_MODEL, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": GATEWAY_MAX_TOKENS, "reasoning_effort": GATEWAY_EFFORT, "stream": False}
        answer = _gateway_answer(G.safe_base(target[1]), target[0], body, deadline)
        return str(answer["choices"][0]["message"]["content"] or "").strip()
    raise NoKey("no Gemini key and no Tico AI Gateway key")


def _socket_of(response):
    """The socket under an HTTP response, when there is one (http.client keeps it in its reader)."""
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    return sock if isinstance(sock, socket.socket) else None


def _cut(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass                                # already closed


def _rewrite(text, title, boss, gateway=None):
    """The rewrite, or "" when it fails, is not done within REWRITE_SECONDS in all (finding the key, which may wait on a
    secret store, the request and reading the answer), or two are already running. The worker holds its place until it
    ends, which is at most about one socket wait past the deadline, so slow calls never pile up. The model is Gemini,
    else the Tico Gateway with `gateway` (see `_rewrite_call`)."""
    if not _REWRITES.acquire(blocking=False):
        return ""
    deadline = time.monotonic() + REWRITE_SECONDS
    out = {}

    def work():
        try:
            out["text"] = _rewrite_call(text, title, boss, deadline, gateway)
        except Exception:  # noqa: BLE001 - no key, slow, failed or odd: the caller keeps the composed text
            out["text"] = ""
        finally:
            _REWRITES.release()

    worker = threading.Thread(target=work, name="seat-draft-rewrite", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _REWRITES.release()
        return ""
    worker.join(REWRITE_SECONDS)
    return "" if worker.is_alive() else out.get("text", "")


_TEAMMATE = re.compile(r"^\s*(?:an?\s+)?ai\s+teammate\b[^.:\n]*[.:]\s*", re.I)
_PERSON = re.compile(r"^\s*(?:i|you|we)(?:'ll| will)?\s+([a-z]+)\b", re.I)
_NOT_VERBS = frozenset("am are is was were will would can could should must may might shall".split())
_IRREGULAR = {"have": "has", "do": "does", "go": "goes"}


def _voice(text, title):
    """The model's opening line in the catalog's voice, whatever the model wrote: "AI teammate for the Rust engineer
    role. You own the services" and "Rust engineer owns the services" are "Owns the services"."""
    text = _TEAMMATE.sub("", str(text or ""), count=1)
    if title:
        text = re.sub(rf"^\s*(?:the\s+|an?\s+)?{re.escape(title)}\s*[:,]?\s+(?=[a-z])", "", text, count=1, flags=re.I)
    found = _PERSON.match(text)
    verb = found.group(1).lower() if found else ""
    if verb and verb not in _NOT_VERBS:
        verb = _IRREGULAR.get(verb) or (verb[:-1] + "ies" if re.search(r"[^aeiou]y$", verb) else
                                        verb + ("es" if verb.endswith(("s", "sh", "ch", "x", "o")) else "s"))
        text = verb.capitalize() + text[found.end():]
    return text[:1].upper() + text[1:]


def draft(onboarding, title, team="", boss="", redraft=False, gateway=None):
    """A job description for a seat not posted yet: composed in well under a second from the catalog card that is the
    job the title names (`source` "catalog"). `redraft` asks the model to rewrite the composed text once (`source`
    "model"); with no key, a slow or failed call, or in a rehearsal or demo (which never reach a model or read a key),
    the composed text stands. When no card is the job, the model writes one from the title within the same bound
    (`source` "model"), else "" (`source` "none"). The model is Gemini when the server has a key, else the Tico AI
    Gateway with `gateway` (the company's (key, base URL), or None)."""
    _, cards = _recruitable(onboarding)
    card = _best_card(cards, str(title or "")[:lifecycle.TITLE_MAX], team)
    offline = getattr(onboarding.settings, "rehearsal", False) or getattr(onboarding.settings, "demo", False)
    if not card:
        # Another job's description is no draft ("Rust engineer" is not the QA Engineer): the model writes one from the
        # title, under the same bound, or the box stays empty for the person's own words.
        written = "" if offline else _voice(_rewrite("", str(title).strip()[:lifecycle.TITLE_MAX], boss, gateway),
                                            str(title).strip())
        if len(written) >= 40:
            return {"description": written[:lifecycle.DESCRIPTION_MAX], "source": "model"}
        return {"description": "", "source": "none"}
    text = compose(card)
    if redraft and not offline:
        rewritten = _voice(_rewrite(text, str(title).strip()[:lifecycle.TITLE_MAX], boss, gateway), str(title).strip())
        if len(rewritten) >= 40:
            return {"description": rewritten[:lifecycle.DESCRIPTION_MAX], "source": "model"}
    return {"description": text, "source": "catalog"}


# ------------------------------------------------------------------ 2. candidates
def _recruitable(onboarding):
    """The catalog cards a seat may be filled from: the org builder's own (no built-in, no helper; recruit_rank.build),
    with each card's model, effort and whether it is a starter from the card itself."""
    from .recruit import catalog
    from .onboarding import read_cards
    full = {card["template"]: card for card in read_cards(onboarding.settings)}
    value = catalog(onboarding.settings)
    out = []
    for card in value["cards"]:
        raw = full.get(card["template"])
        if raw and not raw["bootstrap"] and not raw["required"] and raw["kind"] != "helper":
            out.append({**card, "raw": raw})
    return value, out


def _models(c, onboarding):
    """The models one may choose instead: current models of the company's enabled providers."""
    from . import providers
    enabled = providers.load(c, onboarding.settings)["enabled"]
    return [{"id": m["id"], "name": m.get("label") or m["id"], "provider": m.get("provider") or ""}
            for m in onboarding.models.values() if m.get("provider") in enabled and not m.get("deprecated")]


def _suggested(c, onboarding, raw):
    picked = onboarding._runtime(c, raw)
    if not picked:
        return {"model": "", "model_name": "Your team's default model, once one is chosen", "effort": ""}
    model, effort = picked
    return {"model": model, "model_name": onboarding.models[model].get("label") or model, "effort": effort}


def _needs(raw):
    """What the card needs, each with its state: a built-in tool is connected, any other is set up on hire when the
    card requires it and not set when it is optional."""
    out = []
    for row in raw["prerequisites"]:
        tool = row["tool"]
        state = "connected" if tool in BUILT_IN_TOOLS else "on_hire" if row["required"] else "not_set"
        out.append({"tool": tool, "label": _tool(tool), "required": row["required"], "state": state})
    return out


def candidates(auth, onboarding, c, who, seat_id):
    """Recruit: up to eight Tico templates for the seat, strong matches first, then good ones, each tier best first.
    `match` is "strong" for the template that is the job (the one a draft is written from, `_ranked_fits`) and any that
    fits as well, "good" when its tags, name or summary fit the title or job description inside the job's department,
    else "". A coding job ("Rust engineer") also gets the supported cloud agents as `agents`, placed after the first
    `agents_at` templates: no template writes features, so they lead unless a template is the job. Deterministic and
    local: the same seat gives the same list, and nothing leaves this server."""
    row = lifecycle.seat(c, seat_id)
    # Listing candidates changes nothing: BotOps lists them with the rights of the person it acts for, and the pick is
    # their own tap on a card (botops_act.CONFIRM).
    _may_hire(auth, c, replace(who, confirmed=True) if who.via == "botops" else who, row)
    value, cards = _recruitable(onboarding)
    departments = {d["id"]: d["name"] for d in value["departments"]}
    t = _title(row["title"])
    fits = _ranked_fits(cards, row["title"])
    best = fits[0][1] if fits else None
    # Strong: as good a fit by name as the template the description is drafted from ("Head of Sales" is the Sales
    # Manager's job, not the Sales Engineer's, though both say "Sales").
    strong = {card["template"] for key, card in fits if key[0][:5] == fits[0][0][0][:5]}
    # The department the job is in: the template that is the job's, else the one the title's words point to.
    department = best["department"] if best else _department_hint(row["title"])
    # The title's words of work, not of rank or its kind ("Developer" in "Go Developer" is no fit to the Developer
    # Advocate).
    title = [(stem, word) for stem, word in R._words(_expand(row["title"])) if stem not in RANKS and word not in ROLE_WORDS]
    words = title + [("", "")] + R._words(R._text(row["description"], 4000))
    common = R._common(cards)
    scored = []
    for card in cards:
        score, _ = R._match(card, words, common)
        named, _ = R._match(card, title, common)
        shared = _fit(card, t)
        score += 2 * named + 3 * shared
        here = not department or card["department"] == department
        match = "strong" if card["template"] in strong else "good" if (score > 0 and here) or shared else ""
        # Then, the department's own first; a head's template not for a job of a kind ("Head of Engineering" is no
        # applicant for "Rust engineer") and first for a head's title; a template named for the title's work ("QA
        # Engineer" for "QA Analyst"); the same kind of job ("Engineer"); the better fit; a template whose name is all
        # field ("Senior Software Engineer", "Head of Marketing") before a narrower one; the catalog's order.
        lead = bool(card.get("lead"))
        misplaced = lead != t["heads"] if (t["kind"] or t["heads"]) else False
        near = bool({stem for stem, _ in _name_words(card["name"])} & t["specific"])
        same_kind = bool(t["kind"]) and _kind(card["name"]) == t["kind"]
        work = _work(card["name"])
        scored.append((("strong", "good", "").index(match), card is not best, not here, misplaced, not near, not same_kind, -score,
                       (not work <= GENERIC, len(work)), {"default": 0, "common": 1}.get(card["suggest"], 2),
                       card["name"].lower(), card, match))
    scored.sort(key=lambda item: item[:10])
    out = []
    for *_, card, match in scored[:CANDIDATES]:
        raw = card["raw"]
        out.append({"template": card["template"], "name": card["name"], "icon": card["icon"],
                    "department": departments.get(card["department"], ""), "department_id": card["department"],
                    "line": R._first_sentence(card["summary"], 90), "summary": R._text(raw["summary"], 1000),
                    "tags": card["tags"][:6], "owns": [_plain(item, 400) for item in raw["owns"][:6] if _plain(item, 400)], "never": raw["never"][:6],
                    "onboarding": [item["ask"] for item in raw["onboarding"] if item["ask"]][:6],
                    "prerequisites": _needs(raw), "match": match, "source": "template", "starter": bool(raw["starter"]),
                    **_suggested(c, onboarding, raw)})
    # A job writing code in Engineering has the cloud coding agents Tico connects as applicants too: after the templates
    # that are the job, and first when none is ("Rust engineer": no template writes Rust, Claude Code and Codex do).
    agents_at = sum(1 for item in out if item["match"] == "strong")
    agents = []
    if department == "engineering" and t["kind"] in CODING_KINDS:
        from .team_build import _cloud_agents
        agents = [{**agent, "match": "good" if agents_at else "strong"} for agent in _cloud_agents() if agent["supported"]]
    return {"seat": view(auth, c, who, row), "candidates": out, "agents": agents, "agents_at": agents_at,
            "department": department, "models": _models(c, onboarding)}


# ------------------------------------------------------------------ 3. the pick
def _may_hire(auth, c, who, row):
    """Whoever may change the seat picks for it: the owner, or a person it reports up to, in person or on a request
    they confirmed. A bot never picks, and BotOps acting for someone does so only on their confirmed request."""
    if who.role not in ("owner", "human") or (who.via and not who.confirmed):
        raise Problem("forbidden", "Only a person picks a candidate for a seat: the owner, or someone the seat reports "
                      "up to", 403)
    lifecycle._may_place(auth, c, who, row["reports_to"])


def top_card(auth, onboarding, c, who, row):
    """The raw card of the template Recruit ranks first for the seat, or None when none fits it (no Strong or Good match,
    unless the seat is a coding job, which builds from its department's first). A cloud agent's add to a seat builds its bot
    from this card, since a bot's repository is built from a template."""
    found = candidates(auth, onboarding, c, who, row["id"])
    ranked = found["candidates"]
    _, cards = _recruitable(onboarding)
    # A coding seat with no Strong or Good template ("Engineer") still builds from Engineering's first: the agent writes
    # the code, the template only gives the bot its repository.
    fits = ranked and (ranked[0]["match"] or (found["agents"] and ranked[0]["department_id"] == found["department"]))
    template = ranked[0]["template"] if fits else ""   # a template that does not fit is not one
    return next((card["raw"] for card in cards if card["template"] == template), None)


def brief(c, onboarding, card, row):
    """The reviewed instructions a seat's bot starts with: the template rendered for this company, then the seat's job
    description. Returns the company's record, the rendered template, the description and the instructions."""
    from .onboarding import display_names, load, render
    record = load(c)
    rendered = render(card, display_names(onboarding.settings, record), row["title"])
    description = str(row["description"] or "").strip()
    instructions = rendered["instructions"].rstrip() + ("\n\n## Job description\n" + description + "\n" if description else "")
    return record, rendered, description, instructions


def onboard(c, who, onboarding, row, card, slug, *, name, description, instructions, rendered, answers, model):
    """Put the bot that fills a seat into Onboarding, in the seat's place, and only then give it its repository: a starter's
    computer builds it from this definition (`materialize`), any other template's BotOps does on its "Set up" task. A hire
    and a cloud agent's add to a seat both land here, so they land the same way. Returns the setup task's id, if any."""
    from . import message_bots
    if card.get("session") == "task":
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.session','task') WHERE bot=?", (slug,))
    if row["team"]:
        c.execute("UPDATE bot_config SET team=? WHERE bot=?", (row["team"], slug))
        from .finance import actions as FA
        FA.revalidate_org(c)                 # the seat's team is part of the org chart Finance's grants follow (spec 7.5)
    declared = onboarding._declared(c, slug)
    declared.update(template=card["template"], instructions=instructions, template_version=releases.version(),
                    recruited_from=row["id"])
    onboarding._write_config(c, slug, declared)
    # Onboarding from the start: it learns, takes no work, and a person above it marks it complete (lifecycle.py).
    c.execute("UPDATE bot_config SET onboarding_state=? WHERE bot=?", (lifecycle.statuses.NEEDS_SETUP, slug))
    message_bots.link(c, who.actor, slug, who.actor)
    now = H.now()
    c.execute("UPDATE seats SET closed_at=?,closed_by=?,filled_by=?,updated=?,updated_by=? WHERE id=?",
              (now, who.actor, slug, now, who.actor, row["id"]))
    # Seats that reported to this one move under the bot that fills it, rather than under a closed seat.
    c.execute("UPDATE seats SET reports_to=?,updated=?,updated_by=? WHERE reports_to=? AND closed_at IS NULL",
              (slug, now, who.actor, "seat:" + row["id"]))
    end_draft(c, row, "The seat was filled")
    H.event(c, who.actor, "seat.filled", row["id"], {"bot": slug, "template": card["template"], "model": model})
    if card["starter"]:
        onboarding._make_starter(c, who, slug, card["template"])
        return None
    choice = {"template": card["template"], "display_name": name, "instructions": instructions,
              "description": description}
    return onboarding._setup_task(c, who, slug, choice, rendered, answers)


def hire(auth, onboarding, c, who, seat_id, body):
    """Fill the seat with the picked candidate: the bot in Onboarding, in the seat's place, and only then its repo."""
    from .onboarding import BOTOPS
    row = c.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
    if row and row["filled_by"]:
        name = (H.bot(c, row["filled_by"]) or {}).get("display_name") or row["filled_by"]
        raise Problem("filled", f"This seat is already filled by {name}", 409)
    row = lifecycle.seat(c, seat_id)
    _may_hire(auth, c, who, row)
    row = lifecycle.refuse_if_requested(row, c)
    _, cards = _recruitable(onboarding)
    card = next((card["raw"] for card in cards if card["template"] == body.template), None)
    if not card:
        raise Problem("template", "Pick one of the candidates Recruit offered", 422)
    if not card["starter"] and not H.bot(c, BOTOPS):
        raise Problem("botops", "BotOps builds this bot, and this team has no BotOps; add it from Templates first", 409)
    if body.model:
        if body.model not in {m["id"] for m in _models(c, onboarding)}:
            raise Problem("model", "Choose one of your team's current models", 422)
        model, effort = body.model, ""
    else:
        suggested = _suggested(c, onboarding, card)
        model, effort = suggested["model"], suggested["effort"]
    stand_in = not model
    if stand_in:
        # No provider yet: a stand-in satisfies the definition, then the bot follows the company default (as setup does).
        model, effort = next((m["id"], m.get("default_effort") or "") for m in onboarding.models.values()
                             if m.get("provider") and not m.get("deprecated"))
    record, rendered, description, instructions = brief(c, onboarding, card, row)
    name = (body.name or row["title"]).strip()[:100] or row["title"][:100]
    slug = onboarding.admin.pick_slug(c, name)
    # The bot first, under the rules for any bot this person adds: who may create one, under whom, on which model and
    # computer.
    created = onboarding.admin.create_bot(c, who, M.BotDefinitionCreate(
        slug=slug, display_name=name, description=description[:2000], status="planned",
        repo="bot-" + slug, thread_mode="personal", reports_to=row["reports_to"] or None,
        shared=bool(card.get("shared")), template=card["template"], model=model, effort=effort,
        owners=[H.actor_id(who.actor)], runner_id=body.runner_id or None))
    if stand_in:
        onboarding._follow_default(c, slug)
        model = ""
    setup_task = onboard(c, who, onboarding, row, card, slug, name=name, description=description,
                         instructions=instructions, rendered=rendered, answers=record["answers"], model=model)
    return {"slug": slug, "name": name, "seat": seat_id, "stage": lifecycle.ONBOARDING, "reports_to": row["reports_to"],
            "template": card["template"], "model": model, "setup_task_id": setup_task,
            "note": (created or {}).get("note") if isinstance(created, dict) else None}
