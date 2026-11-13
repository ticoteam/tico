"""Wording rules for text the hub shows people.

Lint of titles, asks, questions, approvals and support text, and repairs to generated prose."""

import hashlib
import re

from .hubdb_core import actor_id


APPROVAL_FIELDS = {
    "send": ("to", "cc", "subject", "body_sha256", "mailbox"),
    "letter": ("letter_id", "approval_hash", "proof", "recipient", "return_address", "mail_class", "total_micros"),
    "spend": ("amount", "account", "what"),
    "publish": ("url", "content_sha256"),
    "merge": ("repo", "pr"),
    # A bot's own task cap (backend/task_caps.py): the pair; per_day null asks to remove the cap.
    "task_cap": ("sender", "receiver"),
    # The redesign's other proposal (backend/proposals.py checks its whole shape): a spend limit.
    "spend_limit": (),
    # A bot's mail sending a person has not approved (backend/proposals.py `MailAlert`, raised by Check health).
    "mail_alert": ("bot",),
    # A report to the Tico team (backend/support.py `file_approved`): the message, its hash, and whether
    # the version and install ID go with it. Nothing else may: no email, no diagnostics bundle.
    "support": ("message", "content_sha256"),
}


SUPPORT_APPROVAL_KEYS = ("message", "content_sha256", "include_ids")


# ----------------------------------------------------------------------------- lint (rule 7)
LINT_MAX_WORDS = 120


# `status: doing`, `owner:bot:cmo`. A colon that opens a plain clause ("the order status: it says pending") is prose.
LINT_CODES = re.compile(r"\b(owner|status)\s*:\s*(?!(?:it|this|that|the|an?|there|they|we|you|i)\b)[\w:./-]+", re.I)


# Plain-English titles: an identifier is a bare
# reference number - `DC040302`, `03QEZT3C`, `#18823` - that belongs in the body or a link.
# Dates, versions (3.5) and short model names are not identifiers.
LINT_IDENT = re.compile(
    r"(?<![\w/.-])(?:#\d{3,}|[A-Z]{1,4}-?\d{4,}|\d{6,}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,})(?![\w/.-])"
)


LINT_CAPS = re.compile(r"\b[A-Z]{4,}\b")


LINT_CAPS_OK = {
    "HTML",
    "JSON",
    "YAML",
    "HTTP",
    "HTTPS",
    "README",
    "TODO",
    "HEAD",
    "MAIN",
    "AWS",
    "CSV",
    "PDF",
    "SAAS",
    "OKR",
    "OKRS",
    "CEO",
    "COO",
    "CTO",
    "CPO",
    "CMO",
    "CRO",
    "SEO",
    "ASAP",
    "USA",
    "NPS",
    "CRM",
    "SQL",
    "URL",
    "API",
    "MCP",
    "AI",
    "PR",
    "PRS",
}


LINT_JARGON = re.compile(r"\b(keeper|GEO citation|routine \d|class:\s*\w+/\w+|paste-ready|see spec)\b", re.I)


# Imperative openers a human item may start with. The shape is policies/handoffs.md's
# "Asking the owner for anything": the ask first, in a verb. The list is generous on purpose:
# refusing a real verb ("Connect", "Authorize") costs a bot a turn, while letting through an
# unlisted verb costs nothing. `_is_verb_opener` refuses only what is clearly not a verb.
LINT_VERBS = {
    "accept",
    "access",
    "acknowledge",
    "activate",
    "add",
    "adjust",
    "advise",
    "agree",
    "align",
    "allocate",
    "allow",
    "analyse",
    "analyze",
    "announce",
    "answer",
    "apply",  # the growth packs' "Apply: <the change>" for the person who puts it live
    "appoint",
    "approve",
    "archive",
    "arrange",
    "ask",
    "assign",
    "attach",
    "audit",
    "authenticate",
    "authorise",
    "authorize",
    "back",
    "backfill",
    "backup",
    "ban",
    "block",
    "book",
    "boost",
    "bring",
    "browse",
    "budget",
    "build",
    "buy",
    "call",
    "cancel",
    "capture",
    "certify",
    "change",
    "check",
    "choose",
    "claim",
    "clarify",
    "clear",
    "click",
    "close",
    "collect",
    "comment",
    "commit",
    "compare",
    "complete",
    "configure",
    "confirm",
    "connect",
    "consider",
    "contact",
    "continue",
    "convert",
    "coordinate",
    "copy",
    "correct",
    "create",
    "cut",
    "debug",
    "decide",
    "decline",
    "define",
    "delegate",
    "delete",
    "deliver",
    "deploy",
    "describe",
    "designate",
    "disable",
    "disconnect",
    "dismiss",
    "document",
    "download",
    "draft",
    "drop",
    "edit",
    "email",
    "enable",
    "enroll",
    "enrol",
    "ensure",
    "enter",
    "escalate",
    "establish",
    "evaluate",
    "examine",
    "expand",
    "expedite",
    "explain",
    "export",
    "extend",
    "extract",
    "file",
    "fill",
    "find",
    "finalise",
    "finalize",
    "finish",
    "fix",
    "flag",
    "follow",
    "forward",
    "freeze",
    "generate",
    "get",
    "give",
    "grant",
    "handle",
    "help",
    "hire",
    "hold",
    "identify",
    "implement",
    "import",
    "improve",
    "inspect",
    "install",
    "instruct",
    "introduce",
    "investigate",
    "invite",
    "issue",
    "join",
    "keep",
    "kick",
    "launch",
    "leave",
    "let",
    "link",
    "list",
    "load",
    "locate",
    "log",
    "look",
    "make",
    "map",
    "mark",
    "match",
    "meet",
    "merge",
    "migrate",
    "monitor",
    "move",
    "name",
    "negotiate",
    "nominate",
    "note",
    "notify",
    "obtain",
    "onboard",
    "open",
    "opt",
    "order",
    "outline",
    "own",
    "pause",
    "pay",
    "pick",
    "pin",
    "ping",
    "plan",
    "post",
    "prepare",
    "present",
    "prioritise",
    "prioritize",
    "proceed",
    "process",
    "procure",
    "promote",
    "propose",
    "provide",
    "provision",
    "publish",
    "pull",
    "purchase",
    "push",
    "put",
    "quote",
    "raise",
    "rank",
    "rate",
    "read",
    "reassign",
    "rebuild",
    "recommend",
    "reconcile",
    "record",
    "recover",
    "reduce",
    "refresh",
    "refund",
    "register",
    "reject",
    "release",
    "reload",
    "remind",
    "remove",
    "rename",
    "renew",
    "reopen",
    "reorder",
    "replace",
    "reply",
    "report",
    "request",
    "require",
    "reschedule",
    "reset",
    "resolve",
    "respond",
    "restart",
    "restore",
    "restrict",
    "resume",
    "retire",
    "retry",
    "return",
    "review",
    "revise",
    "revoke",
    "roll",
    "rotate",
    "route",
    "run",
    "sanity-check",
    "save",
    "schedule",
    "scope",
    "screen",
    "search",
    "secure",
    "select",
    "send",
    "set",
    "settle",
    "share",
    "sign",
    "sign-off",
    "simplify",
    "skip",
    "snooze",
    "sort",
    "specify",
    "split",
    "spot-check",
    "start",
    "stop",
    "submit",
    "subscribe",
    "suggest",
    "supply",
    "supervise",
    "suspend",
    "swap",
    "switch",
    "sync",
    "take",
    "talk",
    "tell",
    "test",
    "thank",
    "throttle",
    "tighten",
    "track",
    "train",
    "transfer",
    "translate",
    "triage",
    "trim",
    "try",
    "turn",
    "unblock",
    "undo",
    "unpause",
    "unsubscribe",
    "update",
    "upgrade",
    "upload",
    "use",
    "validate",
    "verify",
    "view",
    "visit",
    "vote",
    "wait",
    "waive",
    "walk",
    "watch",
    "weigh",
    "whitelist",
    "wire",
    "write",
}


# Openers that are clearly not verbs: articles, pronouns, prepositions, conjunctions, and the
# labels and status words people put where the verb goes ("Needs you: ...", "New: ...", "Urgent").
LINT_NOT_VERBS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "my",
    "our",
    "your",
    "their",
    "his",
    "her",
    "its",
    "i",
    "we",
    "you",
    "they",
    "he",
    "she",
    "it",
    "there",
    "here",
    "who",
    "what",
    "when",
    "where",
    "why",
    "how",
    "which",
    "and",
    "or",
    "but",
    "if",
    "so",
    "then",
    "of",
    "for",
    "to",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "about",
    "re",
    "fwd",
    "fw",
    "new",
    "urgent",
    "important",
    "fyi",
    "todo",
    "task",
    "blocked",
    "needs",
    "need",
    "needed",
    "waiting",
    "pending",
    "done",
    "ready",
    "final",
    "action",
    "decision",
    "question",
    "approval",
    "reminder",
    "status",
    "is",
    "are",
    "was",
    "were",
    "be",
    "can",
    "could",
    "should",
    "would",
    "will",
    "may",
    "might",
    "must",
    "not",
    "no",
    "yes",
    "please",
}


def _is_verb_opener(head):
    """True when a title's first word can start an imperative. Only what is clearly not a verb is refused."""
    first = re.split(r"[\s,;]+", head.strip(), maxsplit=1)[0]
    word = first.strip("\"'`*_-()[]").lower()
    if not word:
        return False
    label = first.rstrip("*_`\"'").endswith(":") and not word.rstrip(":") in LINT_VERBS
    word = word.rstrip(":")
    if word in LINT_VERBS:
        return True
    if label or word in LINT_NOT_VERBS or any(ch.isdigit() for ch in word):
        return False
    letters = re.sub(r"[^A-Za-z]", "", first)
    if len(letters) >= 2 and letters.isupper():  # NEEDS-YOU, URGENT, PR: a label, not a verb
        return False
    if len(word) > 5 and word.endswith("ing"):  # Reviewing X: a gerund, not the ask
        return False
    if (
        word.endswith("s")
        and not word.endswith(("ss", "us", "is"))
        and (word[:-1] in LINT_VERBS or word[:-2] in LINT_VERBS)
    ):  # Needs, Reviews: third person, not imperative
        return False
    if "-" in word and word.split("-")[0] in LINT_NOT_VERBS:  # Needs-you
        return False
    return True  # an unlisted word may still be a verb


def _first_line(text):
    return next((l.strip() for l in str(text or "").strip().splitlines() if l.strip()), "")


def _outside_quotes(text):
    """The text a human actually has to read: quoted drafts and URLs do not count."""
    kept = "\n".join(l for l in str(text or "").splitlines() if not l.lstrip().startswith(">"))
    return re.sub(r"https?://\S+", "", kept)


def lint_human_item(text, title=None):
    """Rule 7. Return the problems with something addressed to a human; empty means it passes.

    The same shape as the legacy Issue dispatcher's `lint_request`, moved here so the server and the
    CLI share one check: the ask in the first line, a verb at the front of a title, under
    120 words outside quoted drafts, no `owner:`/`status:` codes.
    """
    problems = []
    body = str(text or "")
    if "mistake to fix or a limit to keep" in body or "touches something you care about" in body:
        problems.append(
            "write a concrete human decision, recommendation, and next step; do not forward a refusal diagnostic"
        )
    if title is not None:
        problems += lint_human_title(title)
    if not _first_line(body):
        problems.append("write the ask in the first line, with your recommendation")
    outside = _outside_quotes(body)
    n = len(outside.split())
    if n > LINT_MAX_WORDS:
        problems.append(f"{n} words outside the quoted draft; keep it under {LINT_MAX_WORDS}")
    m = LINT_CODES.search(body)
    if m:
        problems.append(f'internal codes: "{m.group(0)}"')
    m = LINT_JARGON.search(body)
    if m:
        problems.append(f'internal jargon: "{m.group(0)}"')
    return problems


def lint_human_title(title):
    """Rule 7's title half: a verb at the front, no `owner:`/`status:` codes. What a renamed task
    for a person is checked on, since its body was checked when it was filed."""
    head = str(title or "").strip()
    if not head:
        return ["give it a title that says what you are asking for"]
    problems = []
    if not _is_verb_opener(head):
        problems.append(f'start the title with a verb (it starts "{head.split()[0]}")')
    m = LINT_CODES.search(head) or LINT_JARGON.search(head)
    if m:
        problems.append(f'internal codes in the title: "{m.group(0)}"')
    return problems


def lint_title(title):
    """Plain English in a title a bot wrote: no reference numbers, no shouting. The problems,
    empty when it passes. Applied to every bot-written General task title, whoever the owner is."""
    head = str(title or "")
    problems = []
    m = LINT_IDENT.search(head)
    if m:
        problems.append(
            f'reference number in the title: "{m.group(0)}" - say what it is in plain words '
            f'and put the number in the body or a link'
        )
    caps = [w for w in LINT_CAPS.findall(head) if w not in LINT_CAPS_OK]
    if caps:
        problems.append(f'all-caps word in the title: "{caps[0]}" - write it in plain words')
    return problems


# A waiting note is put in front of the person as the ask: a task or message id (`db14c907`, a uuid) or a commit
# hash (7 to 40 hex characters) is the bot's own shorthand, and so is a page of status. A run of hex needs a digit
# and a letter, so a word ("deadbeef", "defaced") or a plain number is not an id. One may end a sentence.
LINT_HEX_ID = re.compile(
    r"(?<![\w/.-])(?=[0-9a-f-]*\d)(?=[0-9a-f-]*[a-f])"
    r"(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){1,4}(?:[0-9a-f]{8})?|[0-9a-f]{7,40})(?![\w/-]|\.[\w/-])",
    re.I,
)


LINT_CURRENCY = "$£€¥"


# A year after a few letters (FY2026, CY2025, ES2022) or a standard's number (ISO27001) names a thing, not a record.
LINT_NAMED_NUMBER = re.compile(r"[A-Z]{1,4}-?(?:19|20)\d{2}|ISO-?\d+")


# A pull request is public and a person can look it up, so a line put in front of them may name one, in exactly the
# form "PR #1555". A bare "#1555" is still a reference number, also where it ends a sentence or follows a word
# ("Merge #1555.", "PR#1555", "#1555/files").
LINT_PR_REF = re.compile(r"(?<![\w#/.-])PR #\d+(?![\w#/-]|\.\w)")


LINT_BARE_REF = re.compile(r"(?<![&#/.-])#\d{3,}(?!\w)")


# The ask a bot puts to a person, when it sets a task waiting on them or gives them a task: what their card leads
# with, read by someone busy. Unlike the warnings above it is always refused, whatever TICO_STYLE_LINT says: a card
# with no plain ask costs the person more than the bot's retry costs the bot. Paths, links to stored objects and
# tool or field names are the bot's own shorthand; an http(s) link is something a person can open, so it stays.
LINT_ASK_WORDS = 40


LINT_ASK_EXAMPLE = "Approve the video tool trial on Thursday? If yes, I'll send the invite."


LINT_S3 = re.compile(r"\bs3://\S*", re.I)


LINT_PATH = re.compile(
    r"(?<![\w:/.-])(?:~|\.{1,2})?/[\w.@-]+/[\w.@/-]*"  # /Users/x, ~/x, ./x/y
    r"|(?<![\w/.-])[\w.-]+(?:/[\w.-]+)+\.[A-Za-z][\w]{0,4}\b"  # reports/x.md
    r"|\b(?!(?-i:[A-Z][\w-]*\.(?:js|ts))\b)[\w-]+\.(?:py|js|cjs|mjs|ts|tsx|md|json|ya?ml|toml|sh|sql|log|ipynb)\b",  # not Node.js
    re.I,
)


LINT_TOOL = re.compile(
    r"\b(?:hub|tico)\s+(?:task|message|question|approval|note|chat|doc|file|goal|kpi|bot|routine|update|tool|calendar|meeting|support|team|tag|human|group|conversation|whoami|sql)\b|(?<![\w-])--[a-z][\w-]*|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|`[^`]+`"
)


# A sentence ends at . ! or ? before a capital letter, a digit or a quote, so "3 p.m. on Friday" and "U.S. customers"
# stay one sentence; initials (U.S.), these short titles and months never end one, so "Dr. Smith" and "Oct. 20" do not.
LINT_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9])")


LINT_ABBREV = re.compile(
    r"(?:\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|Prof|St|vs|etc|Inc|Ltd|Co|Jr|Sr|No|approx|e\.g|i\.e|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.)$"
)


def ask_sentences(text):
    """An ask's sentences, a stop after an abbreviation or initials not counting as an end."""
    parts, start = [], 0
    for m in LINT_SENTENCE_END.finditer(text):
        if not LINT_ABBREV.search(text[start : m.start()]):
            parts.append(text[start : m.start()])
            start = m.end()
    return [" ".join(s.split()) for s in [*parts, text[start:]] if s.strip()]


def lint_sentences(text):
    """How many sentences an ask has, not counting a stop after an abbreviation or initials as an end."""
    return len(ask_sentences(text))


def lint_person_ask(text):
    """The problems with an ask to a person, empty when it passes: there is one, it is one or two sentences of at
    most LINT_ASK_WORDS words, and it carries no ids, hashes, file paths, s3:// links, tool and field names or error
    output.
    Deterministic on purpose: the same words always pass or fail, so a bot can fix them in one retry."""
    text = str(text or "").strip()
    if not text:
        return ["is missing"]
    problems = []
    n = len(text.split())
    sentences = lint_sentences(text)
    if n > LINT_ASK_WORDS or sentences > 2:
        problems.append(f"is {n} words in {sentences} sentence{'s' if sentences != 1 else ''}")
    return problems + lint_plain(text)


# What a too-long ask is cut down to: the one sentence that asks for something, and the one after it that says what
# happens then ("If yes, I'll ...", "Then ...", "I will ..."). The rest is the bot's context and goes under Details.
# A sentence that answers the request ("If yes, ...") is the outcome over one that only opens like one ("Then the
# runner failed."), wherever it comes after the request.
LINT_IF_YES = re.compile(r"if (?:yes|so|approved|you (?:agree|approve|do|say yes))\b", re.I)


# A matching opener can still decline the request ("If you do not approve", "If you agree not to merge").
# Inspect only the condition: a negation in what the bot does afterward does not change the person's answer.
LINT_NEGATIVE_CONDITION = re.compile(r"\b(?:no|not|never)\b|\b\w+n['’]t\b", re.I)


LINT_OUTCOME = re.compile(
    rf"(?:{LINT_IF_YES.pattern}|if (?:not|no)\b|then\b|once\b"
    r"|after (?:that|you|it)\b|i(?:'ll|\s+will|\s+can|\s+then)\b|we(?:'ll|\s+will)\b"
    r"|th(?:at|is) (?:lets|unblocks|means)\b)",
    re.I,
)


LINT_REQUEST = re.compile(r"\b(?:please|(?:need|want|would like) you to|you (?:must|should|need to|have to))\b", re.I)


# Words that open a condition or a time ("If yes", "Once approved", "After you approve"): the clause after one leads.
LINT_LEAD_IN = {
    "if",
    "once",
    "after",
    "when",
    "whenever",
    "before",
    "as",
    "unless",
    "until",
    "since",
    "because",
    "while",
    "assuming",
    "given",
}


def _asks_for_something(sentence):
    """A question, an indirect request, or an imperative clause, including one after a condition."""
    if sentence.rstrip("\"')] ").endswith("?"):
        return True
    # An outcome opener does not make the rest of its sentence safe to discard: "Once approved, please sign ..."
    # still asks for a second decision. Treat possible requests conservatively rather than silently losing them.
    if LINT_REQUEST.search(sentence):
        return True

    def first_word(clause):
        clause = re.sub(r"^(?:(?:and|please|also|then)\s+)+", "", clause.strip(), flags=re.I)
        return re.split(r"[\s,;:]+", clause, maxsplit=1)[0].strip("\"'*_()").lower()

    if not LINT_OUTCOME.match(sentence):
        return any(first_word(clause) in LINT_VERBS for clause in re.split(r"[,;]\s*", sentence))
    # An outcome lists what happens ("If yes, I'll sign it, send it and close this task."), so only the clause that
    # leads each part, past its condition, can tell the person to do something ("Once approved, sign the contract.").
    for part in sentence.split(";"):
        for clause in part.split(","):
            word = first_word(clause)
            if word in LINT_VERBS:
                return True
            if word not in LINT_LEAD_IN:
                break
    return False


def person_ask_rewrite(text):
    """For an ask too long or with too many sentences: (rewrite, "") where the rewrite keeps its one request and what
    happens after, in one or two plain sentences of at most LINT_ASK_WORDS words, or ("", repair) saying exactly what
    to change when no shorter wording keeps the same decision. Deterministic, like lint_person_ask: the bot retries
    once with the rewrite and puts the other sentences under Details, so nothing is lost or decided for it."""
    sentences = ask_sentences(str(text or "").strip())
    # Every question and every request counts: an ask that asks one thing and requests another is two decisions,
    # and keeping only one of them would change what the person is asked.
    asks = [i for i, s in enumerate(sentences) if _asks_for_something(s)]
    if not asks:
        return "", (
            "none of its sentences asks for anything: open it with the one question or request, "
            "then say what happens after"
        )
    if len(asks) > 1:
        quoted = "; ".join(f'"{sentences[i]}"' for i in asks)
        return "", (
            f"it asks {len(asks)} things ({quoted}): keep the one decision this ask is for in one sentence, "
            "ask any other separately, and put the reasons in the details"
        )
    i = asks[0]
    after = [s for s in sentences[i + 1 :] if LINT_OUTCOME.match(s)]
    outcome = next(
        (
            s
            for s in after
            if LINT_IF_YES.match(s) and not LINT_NEGATIVE_CONDITION.search(re.split(r"[,;:]", s, maxsplit=1)[0])
        ),
        after[0] if after else None,
    )
    kept = [sentences[i], *([outcome] if outcome else [])]
    rewrite = " ".join(kept)
    n = len(rewrite.split())
    if n > LINT_ASK_WORDS:
        what = "its request and what happens after are" if outcome else "its request alone is"
        return "", (
            f'{what} {n} words: shorten "{rewrite}" to at most {LINT_ASK_WORDS} words, keeping what you '
            "need and what happens after, and put the reasons in the details"
        )
    unclear = lint_plain(rewrite)
    if unclear:
        return "", (
            f'its request "{rewrite}" {"; ".join(unclear)}: say that in plain words and put the reference '
            "in the details"
        )
    return rewrite, ""


# Error output a bot pasted: a traceback, an exception or error class name (bare or with its message), an exit code, an
# HTTP error status. "an error in the invoice" is prose; "TypeError", "ValueError: bad date" and "HTTP 502" are the
# bot's own record, so they go under Details.
LINT_ERROR = re.compile(
    r"\bTraceback\b|(?-i:\b[A-Z]\w*(?:Error|Exception)\b)|(?-i:\b(?:ERROR|FATAL|PANIC|Error|Fatal)\b)\s*:"
    r"|\bexit(?:ed)?\s+(?:code|status)\s+-?\d+|\bHTTP\s*[45]\d\d\b|(?-i:\bE[A-Z]{3,}\b)(?=:)"
    r"|\bstack ?trace\b",
    re.I,
)


def lint_plain(text):
    """What in `text` belongs under Details, not in front of a person, empty when nothing does: an s3:// link, an id
    or hash (a pull request named "PR #1555" is neither), a file path, a tool or field name, or error output. The part of the ask rules (lint_person_ask,
    lint_question) that holds for every line a card shows above Details."""
    text = str(text or "").strip()
    problems = []
    m = LINT_S3.search(text)
    if m:
        problems.append(f'has an s3:// link: "{m.group(0)}"')
    plain = LINT_PR_REF.sub("PR", LINT_S3.sub(" ", re.sub(r"https?://\S+", " ", text)))
    m = (
        LINT_HEX_ID.search(plain)
        or next(
            (
                i
                for i in LINT_IDENT.finditer(plain)
                if not (i.start() and plain[i.start() - 1] in LINT_CURRENCY)
                and not LINT_NAMED_NUMBER.fullmatch(i.group(0))
            ),
            None,
        )
        or LINT_BARE_REF.search(plain)
    )
    if m:
        problems.append(f'has an id or hash: "{m.group(0)}"')
    m = LINT_PATH.search(plain)
    if m:
        problems.append(f'has a file path: "{m.group(0)}"')
    m = LINT_TOOL.search(plain) or LINT_CODES.search(plain) or LINT_JARGON.search(plain)
    if m:
        problems.append(f'has a tool or field name: "{m.group(0)}"')
    m = LINT_ERROR.search(plain)
    if m:
        problems.append(f'has error output: "{m.group(0)}"')
    return problems


def person_ask_of_body(body, title=""):
    """The ask in a task given to a person: the body's first paragraph, after a first line that only repeats the
    title. The rest of the body is details. A body that is only the title is that ask."""
    lines = str(body or "").strip().splitlines()
    if len(lines) > 1 and str(title or "").strip() and lines[0].strip() == str(title).strip():
        lines = lines[1:]
    return re.split(r"\n\s*\n", "\n".join(lines).strip(), maxsplit=1)[0].strip()


def person_ask_refusal(person, text, where):
    """What a refusal of this ask says, or "" when it passes: what failed, what to do about it in this same turn, the
    rule and an example, so the bot fixes it in one retry. An ask refused for its length carries a rewrite to retry
    with (person_ask_rewrite), or, when none keeps the decision, the exact repair: a blocker is never left unasked
    for want of a shorter wording. `where` is "ask" (an ask on a task, `tico task ask`) or "assign" (the body of a task
    given to them)."""
    text = str(text or "").strip()
    problems = lint_person_ask(text)
    if not problems:
        return ""
    name = actor_id(person)
    place = "the ask (the sentence in quotes)" if where == "ask" else "the first paragraph of the task body (--body)"
    fix = ""
    if len(text.split()) > LINT_ASK_WORDS or lint_sentences(text) > 2:
        rewrite, repair = person_ask_rewrite(text)
        details = "a comment on the task" if where == "ask" else "the body under it, after a blank line"
        fix = (
            f' Retry once with this ask, which keeps what you need and what happens after: "{rewrite}" Put the '
            f"rest of your text in {details}, so nothing is lost."
            if rewrite
            else f" No shorter wording keeps the same decision, so nothing was sent and the task is unchanged. To fix "
            f"it, {repair}; then retry once."
        )
    return (
        f"Your ask to {name} {'; '.join(problems)}.{fix} The rule: {place} is one or two plain sentences saying "
        f"what you need from {name} and what happens after, at most {LINT_ASK_WORDS} words, with no ids, hashes, "
        f"file paths, s3:// links, tool names or error output (a pull request may be named as \"PR #<number>\"); "
        f'put details in a comment or further down the body. Example: "{LINT_ASK_EXAMPLE}"'
    )


# A question that lists its picks in its own words ("1. Calls ... 2. Pages ...", "(a) ... (b) ...") instead of as
# options: the card could only show them as text, with nothing to tick.
# A question that lists its picks in its own words ("1. Calls ... 2. Pages ...", "(a) ... (b) ...") instead of as
# options: the card could only show them as text, with nothing to tick.
LINT_LISTED_PICKS = re.compile(
    r"(?:^|\s)(?:\(?[1-9][.)]|\([a-f]\)|[a-f]\))\s+\S.*?(?:^|\s)(?:\(?[2-9][.)]|\([b-f]\)|[b-f]\))\s+\S", re.S
)


def lint_question(question, *, written=False):
    """The problems with one question a bot puts to a person, as a card shows it (ui/app/question-card.ts), empty
    when it passes. Its picks are options, each saying what happens when it is chosen (`then`); several picks
    (`multi`) mark the ones the bot recommends; and the question, its "why it matters" line and each `then` line
    carry nothing that belongs under Details (lint_plain), nor does any pick's label or description. A question with no options is a yes/no or a written
    answer, unless it lists picks in its words or is several picks; a `written` one (a dream's numbered questions,
    backend/dream.py) is answered in words, so it may list them. Deterministic, so a bot fixes it in one retry."""
    q = question if isinstance(question, dict) else question.model_dump()
    text, options, problems = str(q.get("question") or ""), q.get("options") or [], []
    for where, value in (("the question", text), ('the "why it matters" line (why)', q.get("why"))):
        problems += [f"{where} {p}" for p in lint_plain(value)]
    if not options and (q.get("multi") or not written and LINT_LISTED_PICKS.search(text)):
        problems.append("lists picks but has no options: give each pick as an option")
    missing = [o.get("label") for o in options if not str(o.get("then") or "").strip()]
    if missing:
        problems.append(
            f'option{"s" if len(missing) > 1 else ""} {", ".join(chr(34) + str(m) + chr(34) for m in missing)} '
            f'{"have" if len(missing) > 1 else "has"} no "what happens" line (then)'
        )
    for o in options:
        # Every visible part of a pick sits above Details, so it keeps the same plain rule as the question.
        problems += [f'option "{o.get("label")}" {p}' for p in lint_plain(o.get("label"))]
        problems += [f'the description of "{o.get("label")}" {p}' for p in lint_plain(o.get("description"))]
        problems += [f'the "what happens" line of "{o.get("label")}" {p}' for p in lint_plain(o.get("then"))]
    if q.get("multi") and options and not any(o.get("recommended") for o in options):
        problems.append("is several picks with none marked recommended: mark the ones you recommend")
    return problems


QUESTION_EXAMPLE = (
    '{"question": "Website sales chat: go with these picks?", "why": "Engineering can\'t start until '
    'these are set.", "multi": true, "options": [{"label": "Spend cap $10/day", "then": "Chat stops '
    'for the day at $10", "recommended": true}, ...]}'
)


def question_refusal(person, questions, *, written=False):
    """What a refusal of these questions to `person` says, or "" when they pass: what failed, the rule and an example."""
    problems = []
    for q in questions:
        label = (q if isinstance(q, dict) else q.model_dump()).get("question") or ""
        problems += [f'"{str(label)[:60]}": {p}' for p in lint_question(q, written=written)]
    if not problems:
        return ""
    return (
        f"Your question to {actor_id(person)} can't be shown as a clear card: {'; '.join(problems)}. The rule: a "
        f"question with picks gives each pick as an option with a short \"what happens\" line (then); several "
        f"picks (multi) mark the recommended ones (recommended); the question, its why line and each pick's "
        f"label, description and then line have no ids, hashes, file paths, tool names or error output. Put "
        f"those in the task body or a comment, which the card keeps under Details. Example: {QUESTION_EXAMPLE}"
    )


def lint_approval(kind, payload):
    """Rule 7 for an approval: the payload is the exact thing, so every field must be there."""
    problems = []
    fields = APPROVAL_FIELDS.get(kind, ())
    if not isinstance(payload, dict):
        return [f"{kind} needs a payload object with {', '.join(fields)}"]
    for f in fields:
        if f not in payload or payload[f] in (None, ""):
            if f == "cc" and "cc" in payload:
                continue  # an empty Cc is a real answer
            problems.append(f"{kind} needs {f}")
    digest = payload.get("content_sha256")
    if kind == "publish" and digest not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        # Every publisher compares against hashlib's hexdigest(), so any other shape could never publish.
        problems.append("content_sha256 must be 64 lower-case hex characters")
    if kind == "publish" and "@" in url_authority(payload.get("url")):
        # A card cuts a long field, so "https://trusted.example…@other.example" could hide the real host.
        problems.append("url must not carry a user name (user@host); give the plain address")
    if kind == "support":
        problems += lint_support(payload)
    if kind == "merge" and "merge needs pr" in problems:
        # A merge approval is for one pull request; a change with none is a decision for a person.
        problems[problems.index("merge needs pr")] = (
            "merge needs pr (the pull request number); for a change "
            "with no pull request, file a decision task for the person instead"
        )
    return problems


URL_SPECIAL = ("http", "https", "ws", "wss", "ftp", "file")


def url_authority(url):
    """The part of a url where user@ would go, read as a browser reads it; "" when there is none. After a web scheme a
    browser skips any run of "/" and "\\", or none ("https:a@b" is user a at host b); with no scheme, a leading pair of
    them makes the link leave the app for another host. A backslash later counts as part of it, so "https://a\\@b" is
    refused rather than guessed at."""
    text = re.sub(r"[\s\x00-\x1f]", "", str(url or ""))
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", text)
    rest = text[m.end() :] if m else text
    if m and m.group(1).lower() not in URL_SPECIAL and not rest.startswith("//"):
        return ""
    if not m and not re.match(r"[/\\]{2}", rest):
        return ""
    return re.split(r"[/?#]", rest.lstrip("/\\"), maxsplit=1)[0]


def lint_support(payload):
    """A support report is sent exactly as the person read it: the message already clean (support.clean_message would
    leave it unchanged), its SHA-256 matching, and nothing but the three keys."""
    from .support import CONTROL, MAX_MESSAGE

    problems = []
    extra = sorted(set(payload) - set(SUPPORT_APPROVAL_KEYS))
    if extra:
        problems.append(f"support takes only {', '.join(SUPPORT_APPROVAL_KEYS)}, not {', '.join(extra)}")
    message, digest = payload.get("message"), payload.get("content_sha256")
    if "include_ids" in payload and not isinstance(payload["include_ids"], bool):
        problems.append("include_ids must be true or false")
    if message in (None, ""):
        return problems
    if not isinstance(message, str):
        return problems + ["message must be a string"]
    if len(message) > MAX_MESSAGE:
        problems.append(f"message must be at most {MAX_MESSAGE} characters")
    if "\r" in message or CONTROL.search(message) or message != message.strip():
        problems.append("message must be plain text with no control characters and no leading or trailing blank space")
    if digest not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        problems.append("content_sha256 must be 64 lower-case hex characters")
    elif digest and not support_text_matches(payload):
        problems.append("the message doesn't match content_sha256; send the exact message that will be sent")
    return problems


def support_text_matches(payload):
    """A support payload's message is still the one its hash names (SHA-256 of its UTF-8 bytes)."""
    message, digest = payload.get("message"), payload.get("content_sha256")
    if not isinstance(message, str) or not isinstance(digest, str):
        return False
    try:
        return hashlib.sha256(message.encode("utf-8")).hexdigest() == digest
    except UnicodeEncodeError:
        return False


LIBRARIAN_LITERAL = re.compile(
    r"^[ \t]{0,3}(?P<bf>`{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}(?P=bf)`*[ \t]*$|\Z)"  # fenced code: the closing line
    r"|^[ \t]{0,3}(?P<tf>~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}(?P=tf)~*[ \t]*$|\Z)"  # is the same character only
    r"|^(?:(?: {4}|\t)[^\n]*(?:\n|\Z))+"  # indented code
    r"|(?<!`)(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`)"  # code spans: equal-length backtick runs
    r"|\[[^\]\n]*\](?:\([^)]*\)|\[[^\]]*\])?"  # link labels, inline or reference
    r"|https?://\S+|[\w.-]+/[\w/.-]+"  # URLs and paths
    r'|"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)\'[^\']+\'(?!\w)'  # quotes, also across lines
    r"|^[ \t]*>[^\n]*",
    re.M,
)  # blockquotes


JARGON = (
    (r"\bthrough the runner\b", "through Tico"),
    (r"\bstanding instructions\b", "Instructions"),
    (r"\bHub docs\b", "Tico docs"),
)


# "the runner pulls/syncs" is the Computer only when Tico context follows before the next semicolon or sentence
# end, even inside code or a link ("syncs `AGENTS.md`"); an athlete who pulls a hamstring stays one when the Tico
# word comes after a semicolon. Colons and dashes do not stop it: they can introduce what is synced.
RUNNER = re.compile(
    r"\bthe runner(?=\s+(?:pulls?|syncs?)\b[^.!?;\n]*?(?:\b(?:updates?|releases?|changes|instructions|"
    r"repository|repo|next run|Tico)\b|AGENTS?\.md))",
    re.I,
)


def _keep_case(found, new):
    """`new` in the case of the words it replaces: ALL CAPS, Title Case, Sentence case or lower."""
    if found.isupper():
        return new.upper()
    if found.istitle() and " " in found.strip():
        return new.title()
    return new[0].upper() + new[1:] if found[0].isupper() else new


def librarian_text(body):
    """Repair generated prose (Tico's own jargon only) while keeping code, links, quotes and blockquotes as written:
    the team's prose about its business (its company, coworkers, machines, a race runner) is a source fact."""
    body = str(body or "")
    literals = [m.span() for m in LIBRARIAN_LITERAL.finditer(body)]
    runners = {m.start() for m in RUNNER.finditer(body)}
    out, at = [], 0
    for start, end in literals + [(len(body), len(body))]:
        text = ""
        if at < start:
            text = re.sub(
                r"\bthe runner\b",
                lambda m, base=at: _keep_case(m[0], "the Computer") if base + m.start() in runners else m[0],
                body[at:start],
                flags=re.I,
            ).replace(r"\n", "\n")
            for old, new in JARGON:
                text = re.sub(old, lambda m, new=new: _keep_case(m[0], new), text, flags=re.I)
        out += [text, body[start:end]]
        at = end
    return "".join(out)
