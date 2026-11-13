"""The deterministic lint. Every rule has a stable id, a severity, and a one-line fix.

Pure: text in, findings out. No network, no Google, no model. `mail lint` runs it standalone, `draft`,
`reply` and `schedule` run it on the draft as writing feedback (errors stop the draft, warnings are recorded in
the audit line), and Tico runs it again on every bot's queued send (backend/email_sending.py), where an error asks
a person. A rule id never changes meaning, because playbooks and Issues quote them; a retired id is never reused.

    id    severity  what it catches
    L001  error     a forbidden phrase (docs/company.md "What we are not")
    L010  error     a URL on a host that is not acme.example
    L011  error     a forbidden URL (calendly, go.acme.example/get-demo, S3, presigned)
    L020  error     a placeholder survived ({{ }}, [NAME], TODO, XXX, lorem ipsum, <insert)
    L030  error     something shaped like a secret (xoxb-, AKIA, sk-, ghp_, -----BEGIN)
    L040  error     internal leakage (s3://, runtime/, emp-, bot-<slug>/, AGENTS.md, AGENT.md)
    L041  warn      what looks like a hub Issue reference (#123)
    L050  error     no subject, and this is not a reply
    L051  error     the subject is over 120 characters
    L052  error     the body is under 20 characters
    L053  error     the body is over 2500 characters
    L054  error     more than 3 exclamation marks
    L055  error     no signature (none of the last lines names the owner)
    L060  error     fewer than two times offered (skipped with --slot: that is a confirmation)
    L061  error     the times offered are not in chronological order
    L062  error     a time offered is in the past
    L063  error     a time offered is not a weekday
    L064  error     a time offered is outside 08:00-18:00 America/Los_Angeles
    L065  error     a time offered does not name its time zone
    L066  error     a time offered is not free (--check-calendar, 30-minute buffer)
    L067  error     confirmation language with no invite (no --slot)
    L070  error     a reply without --reply-to
    L071  error     the recipient is not on the thread being replied to

L041 is the only warning: an inbound thread about "invoice #4471" is a real customer sentence,
so it is recorded rather than blocking, and the owner can see it in the audit. Everything else is a
policy breach, a leak, or a scheduling promise the calendar cannot keep.

L060 is deliberately narrower than it first looks, because the wider version would block mail the playbooks
require: it only fires without --slot - one time in an offer is a mistake, one time in a confirmation is the point.

Retired: L012 (a standing allowance's required CTA; allowances are gone) and L056 (more than one outside
recipient; Tico's sending policy asks a person about that on the queued send).
"""

import re
from datetime import datetime, timedelta

from . import DEFAULT_TZ, zone

# ---------------------------------------------------------------- rule inputs

FORBIDDEN_PHRASES = ("we guarantee",)
FORBIDDEN_URL_PARTS = ("calendly.com",)
PRESIGNED = ("x-amz-signature", "x-amz-credential", "amazonaws.com", "s3.amazonaws.com")
PLACEHOLDERS = ("{{", "}}", "[NAME]", "[name]", "TODO", "XXX", "lorem ipsum", "<insert")
SECRET_SHAPES = ("xoxb-", "AKIA", "sk-", "ghp_", "-----BEGIN")
INTERNAL_MARKS = ("s3://", "runtime/", "emp-", "AGENTS.md", "AGENT.md")
# A bot's repository is `bot-<slug>` (older: `emp-<slug>`). `emp-` is flagged wherever it appears; `bot-` only where it names a
# repository, a `bot-<slug>/` path or link, or this message's own bot's folder, so a word like "bot-driven" is not a leak.
BOT_REPO = re.compile(r"(?<![\w-])bot-[a-z0-9][a-z0-9-]*/", re.I)
ISSUE_REF = re.compile(r"(?<![\w/])#\d+\b")
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.I)
MAX_SUBJECT = 120
MIN_BODY, MAX_BODY_CHARS = 20, 2500
MAX_BANGS = 3
SIGNATURE_TAIL = 3  # the name may sit above a title line ("Founder, Acme")
NO_SIGNATURE_SLUGS = ("legal",)

BUSINESS_START, BUSINESS_END = 8, 18  # lint window; `slots` offers the narrower 9-17
BUFFER_MINUTES = 30
DEFAULT_MEETING_MINUTES = 20

CONFIRM_RE = re.compile(
    r"(?i)\b(confirm(?:ed|ing|s)?|is locked|locked in|booked you|i(?:'ve| have) booked|"
    r"invite is on its way|see you (?:then|on)|you(?:'re| are) (?:all )?set)\b"
)

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
TZ_RE = re.compile(
    r"(?i)\b(PT|PST|PDT|ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|UTC|GMT|"
    r"Pacific(?:\s+Time)?|Eastern(?:\s+Time)?|America/Los_Angeles)\b"
)

# The zone a recognized token means. A time that names one is that instant, whatever the lint's own zone.
TZ_ZONES = {
    **dict.fromkeys(("pt", "pst", "pdt", "pacific", "pacific time", "america/los_angeles"), "America/Los_Angeles"),
    **dict.fromkeys(("et", "est", "edt", "eastern", "eastern time"), "America/New_York"),
    **dict.fromkeys(("ct", "cst", "cdt"), "America/Chicago"),
    **dict.fromkeys(("mt", "mst", "mdt"), "America/Denver"),
    **dict.fromkeys(("utc", "gmt"), "UTC"),
}

# A time is a date plus a clock reading: minutes ("Sep 8, 13:00") or am/pm ("Sep 8 at 1pm"). A
# bare date ("August 24 notice", "January 19 letter") is a date, not an offered slot; the old regex split "August 24" into the 2nd at 4 o'clock and blocked an approved legal
# email (Tico #155).
TIME_RE = re.compile(r"""(?ix)
    (?:(?P<wd>mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)[a-z]*\.?,?\s+)?
    (?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+
    (?P<day>\d{1,2})(?:st|nd|rd|th)?\b
    (?:\s*,\s*|\s+at\s+|\s+)
    (?P<h>\d{1,2})(?=:\d{2}|\s*[ap]\.?m\.?\b)(?::(?P<min>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?
    (?:\s*(?:[-–—]|to)\s*
       (?P<h2>\d{1,2})(?::(?P<min2>\d{2}))?\s*(?P<ap2>[ap]\.?m\.?)?)?
    \s*(?P<tz>(?:PT|PST|PDT|ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|UTC|GMT|Pacific(?:\s+Time)?|
        Eastern(?:\s+Time)?|America/Los_Angeles)\b)?
""")


# ---------------------------------------------------------------- findings


def finding(rid, severity, message, fix):
    return {"id": rid, "severity": severity, "message": message, "fix": fix}


class Result(dict):
    @property
    def ok(self):
        return not self["errors"]


def result(findings):
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warn"]
    return Result(
        {
            "ok": not errors,
            "findings": findings,
            "errors": errors,
            "warnings": warnings,
            "summary": (f"{len(errors)} error(s), {len(warnings)} warning(s)" if findings else "clean"),
        }
    )


# ---------------------------------------------------------------- the scheduling parser


def _hour24(h, ampm, fallback_ampm=""):
    ap = (ampm or fallback_ampm or "").replace(".", "").lower()
    h = int(h)
    if ap.startswith("p"):
        return h + 12 if h < 12 else h
    if ap.startswith("a"):
        return 0 if h == 12 else h
    return h  # bare "13:00" is 24-hour; "1:00" is 1am


def _year_for(month, day, hour, minute, now, tz):
    """The year that puts this date nearest to now: last Dec is not next Dec."""
    best = None
    for y in (now.year - 1, now.year, now.year + 1):
        try:
            dt = datetime(y, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            continue
        if best is None or abs((dt - now).total_seconds()) < abs((best - now).total_seconds()):
            best = dt
    return best


def parse_times(text, now=None, tz_name=DEFAULT_TZ):
    """Every "Tue Sep 8, 1:00-1:20pm PT" in the text, in the order it appears.

    Each entry: {text, start, end, tz_named}. Times with no am/pm are read as 24-hour, which is
    why "1:00" with no marker lands at 01:00 and trips the business-hours rule instead of being
    guessed into the afternoon.
    """
    tz = zone(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    out = []
    for m in TIME_RE.finditer(text or ""):
        mon = MONTHS.get(m.group("mon")[:3].lower())
        if not mon:
            continue  # pragma: no cover - regex guarantees it
        hour = _hour24(m.group("h"), m.group("ap"), m.group("ap2") or "")
        minute = int(m.group("min") or 0)
        if hour > 23 or minute > 59:
            continue
        named_tz = m.group("tz") and zone(TZ_ZONES[" ".join(m.group("tz").lower().split())])
        start = _year_for(mon, int(m.group("day")), hour, minute, now, named_tz or tz)
        if start is None:
            continue
        end = None
        if m.group("h2"):
            h2 = _hour24(m.group("h2"), m.group("ap2") or m.group("ap"))
            min2 = int(m.group("min2") or 0)
            if h2 <= 23 and min2 <= 59:
                end = start.replace(hour=h2, minute=min2)
                if end <= start:
                    end = start + timedelta(minutes=DEFAULT_MEETING_MINUTES)
        start, end = start.astimezone(tz), end and end.astimezone(tz)
        # Search the whole text from the window's start so a window that begins inside "meet" keeps its real word edge.
        nearby = TZ_RE.search(text, max(0, m.start() - 40))
        named = bool(m.group("tz")) or bool(nearby and nearby.start() < m.end() + 40)
        out.append({"text": " ".join(m.group(0).split()), "start": start, "end": end, "tz_named": named})
    return out


def overlaps(start, end, busy, buffer_minutes=BUFFER_MINUTES):
    """The busy intervals this slot collides with once the buffer is applied both sides."""
    pad = timedelta(minutes=buffer_minutes)
    lo, hi = start - pad, end + pad
    return [(b0, b1) for b0, b1 in (busy or []) if b0 < hi and lo < b1]


# ---------------------------------------------------------------- the lint


def run(
    body,
    subject="",
    to=(),
    cc=(),
    slug="",
    reply_to="",
    is_reply=None,
    thread_participants=None,
    internal_domains=("acme.example",),
    signature_names=None,
    now=None,
    tz_name=DEFAULT_TZ,
    busy=None,
    check_calendar=False,
    slot=None,
    minutes=DEFAULT_MEETING_MINUTES,
):
    """Every rule, in id order. Returns a Result; nothing here raises on a bad draft.

    `signature_names` None skips L055: a caller that does not know whose name signs (the server) does not guess.
    """
    tz = zone(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    body = body if isinstance(body, str) else ""
    subject = subject or ""
    to = [str(a).strip().lower() for a in (to or []) if str(a).strip()]
    cc = [str(a).strip().lower() for a in (cc or []) if str(a).strip()]
    slug = str(slug or "").strip().lower()
    if is_reply is None:
        is_reply = bool(reply_to) or bool(re.match(r"(?i)^\s*re\s*:", subject))
    f = []
    low = body.lower()

    # -- content ----------------------------------------------------
    hits = [p for p in FORBIDDEN_PHRASES if p in low or p in subject.lower()]
    if hits:
        f.append(
            finding(
                "L001",
                "error",
                "forbidden phrase: " + ", ".join(repr(h) for h in hits),
                "Rephrase in the company's own voice (docs/company.md).",
            )
        )

    urls = URL_RE.findall(body)
    allowed_hosts = tuple(h for d in internal_domains for h in (d, "www." + d))
    bad_hosts = []
    for u in urls:
        host = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].split("@")[-1]
        host = host.split(":")[0].lower()
        if host not in allowed_hosts:
            bad_hosts.append(u)
    if bad_hosts:
        f.append(
            finding(
                "L010",
                "error",
                "URL on a host that is not an internal domain: " + ", ".join(bad_hosts[:3]),
                "Only " + " and ".join(allowed_hosts) + " may appear in mail the hub "
                "writes. Link the product page, not a third party.",
            )
        )
    bad_urls = [
        u for u in urls if any(p in u.lower() for p in FORBIDDEN_URL_PARTS) or any(p in u.lower() for p in PRESIGNED)
    ]
    if bad_urls:
        f.append(
            finding(
                "L011",
                "error",
                "forbidden URL: " + ", ".join(bad_urls[:3]),
                "No calendly.com, no S3 or presigned links.",
            )
        )

    left = [p for p in PLACEHOLDERS if p in body or p in subject]
    if left:
        f.append(
            finding(
                "L020",
                "error",
                "placeholder left in: " + ", ".join(left),
                "Fill every hole before drafting. Nothing in angle brackets or braces survives into a message.",
            )
        )
    secrets = [p for p in SECRET_SHAPES if p in body or p in subject]
    if secrets:
        f.append(
            finding(
                "L030",
                "error",
                "something shaped like a secret is in the text: " + ", ".join(secrets),
                "Secrets never leave the machine, and never appear in mail, Issues or "
                "chat (policies/shared-rules.md). Rotate it if it was real.",
            )
        )
    leaks = [p for p in INTERNAL_MARKS if p in body or p in subject]
    bot_repos = {m.group(0).lower() for m in BOT_REPO.finditer(body + "\n" + subject)}
    if slug and re.search(
        r"(?<![\w-])bot-" + re.escape(str(slug).lower()) + r"(?![\w-])", (body + "\n" + subject).lower()
    ):
        if not any(name.startswith("bot-" + str(slug).lower() + "/") for name in bot_repos):
            bot_repos.add("bot-" + str(slug).lower())
    leaks += sorted(bot_repos)
    if leaks:
        f.append(
            finding(
                "L040",
                "error",
                "internal-only reference: " + ", ".join(leaks),
                "The reader does not have the hub. Say the thing in plain words "
                "instead of pointing at a path, a bucket or a repo.",
            )
        )
    refs = ISSUE_REF.findall(body)
    if refs:
        f.append(
            finding(
                "L041",
                "warn",
                "looks like a hub Issue reference: " + ", ".join(refs[:3]),
                "If that is an invoice or order number the reader gave you, it is "
                "fine. If it is a hub Issue, take it out.",
            )
        )

    # -- structure --------------------------------------------------
    if not subject.strip() and not is_reply:
        f.append(finding("L050", "error", "no subject", "A new message needs a subject; only a reply inherits one."))
    if len(subject) > MAX_SUBJECT:
        f.append(
            finding(
                "L051",
                "error",
                f"the subject is {len(subject)} characters, the limit is {MAX_SUBJECT}",
                "Verb first, under 70 characters is better (policies/writing.md).",
            )
        )
    n = len(body.strip())
    if n < MIN_BODY:
        f.append(
            finding(
                "L052",
                "error",
                f"the body is {n} characters, the minimum is {MIN_BODY}",
                "An empty or one-word body is a mistake, not a message.",
            )
        )
    if n > MAX_BODY_CHARS:
        f.append(
            finding(
                "L053",
                "error",
                f"the body is {n} characters, the limit is {MAX_BODY_CHARS}",
                "Six or seven lines. If a sentence is not doing work, cut it (policies/writing.md).",
            )
        )
    bangs = body.count("!")
    if bangs > MAX_BANGS:
        f.append(
            finding(
                "L054",
                "error",
                f"{bangs} exclamation marks, the limit is {MAX_BANGS}",
                "The owner's voice: short, plain, no fake enthusiasm.",
            )
        )
    if slug not in NO_SIGNATURE_SLUGS and signature_names:
        tail = [ln for ln in body.strip().splitlines() if ln.strip()][-SIGNATURE_TAIL:]
        if not any(name in ln.lower() for ln in tail for name in signature_names):
            f.append(
                finding(
                    "L055",
                    "error",
                    "no signature: the last lines do not name the owner",
                    "The mailbox is the owner's, so the message signs off as the owner.",
                )
            )

    # -- replies ----------------------------------------------------
    if is_reply and not reply_to:
        f.append(
            finding(
                "L070",
                "error",
                "this reads as a reply but no --reply-to was given",
                "Pass --reply-to <thread-id> so the message lands in the thread with In-Reply-To and References set.",
            )
        )
    if reply_to and thread_participants is not None:
        known = {str(a).strip().lower() for a in thread_participants}
        strangers = [a for a in to if a not in known]
        if strangers:
            f.append(
                finding(
                    "L071",
                    "error",
                    "not on that thread: " + ", ".join(strangers),
                    "A reply goes to the people already on the thread. Writing to "
                    "someone new is a new message, with its own policy check.",
                )
            )

    # -- scheduling -------------------------------------------------
    times = parse_times(body, now=now, tz_name=tz_name)
    if times:
        if len(times) < 2 and not slot:
            # A confirmation names one time on purpose; --slot says which, and says the invite
            # exists. Without --slot, one time is an offer, and an offer needs two.
            f.append(
                finding(
                    "L060",
                    "error",
                    f"only one time is offered ({times[0]['text']})",
                    "Offer at least two, chronological, plus 'or send me whatever works' (policies/shared-rules.md).",
                )
            )
        starts = [t["start"] for t in times]
        if starts != sorted(starts):
            f.append(
                finding(
                    "L061",
                    "error",
                    "the times offered are not in chronological order",
                    "Earliest first. Reorder the lines.",
                )
            )
        past = [t["text"] for t in times if t["start"] <= now]
        if past:
            f.append(
                finding(
                    "L062",
                    "error",
                    "in the past: " + ", ".join(past),
                    "Get fresh slots: mail slots --for <mailbox> --n 2 --minutes 20.",
                )
            )
        weekend = [t["text"] for t in times if t["start"].weekday() >= 5]
        if weekend:
            f.append(finding("L063", "error", "not a weekday: " + ", ".join(weekend), "Weekdays only."))
        outside = [t["text"] for t in times if not (BUSINESS_START <= t["start"].hour < BUSINESS_END)]
        if outside:
            f.append(
                finding(
                    "L064",
                    "error",
                    "outside business hours: " + ", ".join(outside),
                    f"{BUSINESS_START:02d}:00-{BUSINESS_END:02d}:00 "
                    f"{tz_name}. Say am or pm; a bare '1:00' reads as 1am.",
                )
            )
        unnamed = [t["text"] for t in times if not t["tz_named"]]
        if unnamed:
            f.append(
                finding(
                    "L065",
                    "error",
                    "no time zone named: " + ", ".join(unnamed),
                    "Spell the zone out on every time: '1:00pm PT'.",
                )
            )
        if check_calendar:
            clashes = []
            for t in times:
                end = t["end"] or (t["start"] + timedelta(minutes=minutes))
                if overlaps(t["start"], end, busy):
                    clashes.append(t["text"])
            if clashes:
                f.append(
                    finding(
                        "L066",
                        "error",
                        "not free with a 30-minute buffer: " + ", ".join(clashes),
                        "Every calendar on that mailbox counts as busy "
                        "(policies/shared-rules.md). Take the times from `mail slots`.",
                    )
                )
    # A confirmation confirms a time. "Please confirm the conference details" is a request; it
    # only counts when the body also names a clock time or uses the unmistakable phrases.
    strong = re.search(
        r"(?i)\b(is locked|locked in|booked you|i(?:'ve| have) booked|"
        r"invite is on its way|see you (?:then|on)|you(?:'re| are) (?:all )?set)\b",
        body,
    )
    if (strong or (CONFIRM_RE.search(body) and times)) and not slot:
        f.append(
            finding(
                "L067",
                "error",
                "confirmation without invite: the body confirms a time but no --slot was passed",
                "Never confirm before the invite exists. `mail schedule --thread <id> "
                "--slot <iso>` creates the event and sends the confirmation together, "
                "or does neither.",
            )
        )
    return result(f)


# ---------------------------------------------------------------- rendering


def render(res, title="Lint"):
    lines = [f"# {title}", ""]
    if not res["findings"]:
        return "\n".join(lines + ["clean: no rule fired.", ""])
    for x in res["findings"]:
        lines.append(f"{x['severity'].upper():<5} {x['id']}  {x['message']}")
        lines.append(f"      fix: {x['fix']}")
    lines += ["", res["summary"] + ("" if res["ok"] else " - errors block the draft; fix them and run it again.")]
    return "\n".join(lines) + "\n"
