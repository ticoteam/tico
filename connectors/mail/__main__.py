#!/usr/bin/env python3
"""The company's one mail tool. Every bot calls this; nobody calls the Gmail API directly.

  scripts/mail.sh doctor
  scripts/mail.sh whoami --as influencer
  scripts/mail.sh inbox --as influencer --new --since 24h --format brief
  scripts/mail.sh inbox --as influencer --untriaged --format brief
  scripts/mail.sh thread 18f... --as influencer
  scripts/mail.sh search "from:stripe.com newer_than:7d" --as ana --mailbox ana@acme.example
  scripts/mail.sh attachments 18f... --as legal --mailbox ana@acme.example --format json
  scripts/mail.sh attachment 18f... a1 --out private/case.pdf --as legal --mailbox ana@acme.example
  scripts/mail.sh label add 18f... 18g... hub/needs-owner --as influencer
  scripts/mail.sh rules run --as ana --mailbox ana@acme.example --dry-run
  scripts/mail.sh rules backtest --as inbox --since 14d [--rules candidate.yaml]
  scripts/mail.sh draft --as influencer --to a@b.example --subject "..." --body-file f --issue 128
  scripts/mail.sh draft --as legal --reply-to 18f... --body-file f --attach private/letter.pdf
  scripts/mail.sh discard r-88... --as legal          (only a draft this bot made, never sent mail)
  scripts/mail.sh send  --as influencer --draft r-88... --issue 128 [--approval-issue 131]
  scripts/mail.sh reply --as legal --thread 18f... --body-file f --issue 128
  scripts/mail.sh lint  --body-file f --subject "..." [--check-calendar]
  scripts/mail.sh slots --for ana@acme.example --n 2 --minutes 20
  scripts/mail.sh upcoming --for ana@acme.example --hours 24 --json
  scripts/mail.sh schedule --thread 18f... --slot 2026-09-08T13:00:00-07:00 --body-file f
  scripts/mail.sh policy show --as influencer      scripts/mail.sh sent-log --since 7d
  scripts/mail.sh audit --since 24h
  scripts/mail.sh sync --as ana --all-roster --backfill 90d
  scripts/mail.sh sync export --as ana --json
  scripts/mail.sh sync ack m1 m2 --as ana --mailbox ana@acme.example

One Google service account acts as every mailbox (connectors/mail/auth.py). Who may act as
which mailbox comes from the employee's `access:` block, never from a prompt
(hub policies/access.md). Every read of a body and every state change is audited to
<projects>/runtime/mail/audit.jsonl and mail.db.

Every write goes through five gates in order: policy (registry/mail-policy.yaml plus
employee.yaml), lint (connectors/mail/lint.py), a second model from another vendor
(connectors/mail/review.py), execution with an idempotency key, and the audit log. A gate that
refuses a *send* never errors and never drops the words: the send becomes a draft with the
reason attached, exit 0. Every write command takes --dry-run.

Exit codes: 0 ok, 1 failure (missing key, network, Google said no), 2 policy refusal.
"""

import argparse, hashlib, json, mimetypes, os, sys, weakref
from datetime import datetime, timedelta
from pathlib import Path

from . import AUDIT_PATH, DB_PATH, DEFAULT_TZ, INTERNAL_DOMAIN, Failure, RULES_FILE, Refused
from . import now_utc, parse_since, report, stamp, zone
from . import access, audit as audit_log, auth, db, gmail as gm, labels as lb, rules as rl
from . import calendar as cal, compose, judge as jd, lint as ln, policy as pl, review as rv

MAX_SEARCH = 50                         # docs/mail-service.md: search is bounded to 50 results
REVIEW_FRESH_HOURS = 24                 # a send re-uses a verdict only while it is this new
SYNC_LIMIT = 500                        # default page for `mail sync` backfill / export
HISTORY_FALLBACK = timedelta(days=2)    # when a history id has expired: after:<last_run − 2d>


# ---------------------------------------------------------------- seams (tests replace these)

def open_gmail(mailbox):
    """The only place a real Gmail service is built."""
    return gm.Gmail(auth.gmail_service(mailbox), mailbox)


def open_calendar(mailbox):
    return auth.calendar_service(mailbox)


def open_db():
    return db.connect(DB_PATH)


# ---------------------------------------------------------------- context

class Ctx:
    """Employee, mailbox, verbs, database - resolved once per command."""

    def __init__(self, args, verb="read", need_mailbox=True, soft=False, mailbox=None):
        self.args = args
        self.dry = bool(getattr(args, "dry_run", False))
        self.issue = str(getattr(args, "issue", "") or os.environ.get("HUB_ISSUE", "") or "")
        self.slug = access.employee(getattr(args, "as_slug", None))
        self.mailbox, self.verbs = (None, [])
        if need_mailbox:
            want = mailbox or getattr(args, "mailbox", None)
            try:
                self.mailbox, self.verbs = access.resolve(self.slug, want, verb)
            except Refused:
                if not soft:                            # `lint` and `review` need no mailbox
                    raise
        self.conn = open_db()
        self._close_conn = weakref.finalize(self, self.conn.close)
        self._gmail, self._calendar, self._policy = None, None, None

    @property
    def gmail(self):
        if self._gmail is None:
            self._gmail = open_gmail(self.mailbox)
        return self._gmail

    @property
    def calendar(self):
        if self._calendar is None:
            self._calendar = cal.Calendar(open_calendar(self.mailbox), self.mailbox)
        return self._calendar

    @property
    def policy(self):
        if self._policy is None:
            self._policy = pl.load(slug=self.slug)
        return self._policy

    def audit(self, action, target="", detail=None):
        return audit_log.record(self.slug, action, self.mailbox or "", target,
                                dict(detail or {}, **({"dry_run": True} if self.dry else {})),
                                self.issue, conn=self.conn)

    def normalize(self, raw):
        return gm.normalize(raw, self.gmail.labels())


def out(args, payload, text):
    if getattr(args, "json", False) or getattr(args, "format", "") == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


# ---------------------------------------------------------------- doctor

class Checks:
    def __init__(self):
        self.rows = []

    def ok(self, what, detail=""):
        self.rows.append({"status": "ok", "check": what, "detail": detail, "fix": ""})

    def fail(self, what, detail="", fix=""):
        self.rows.append({"status": "FAIL", "check": what, "detail": detail, "fix": fix})

    def skip(self, what, detail="", fix=""):
        self.rows.append({"status": "skip", "check": what, "detail": detail, "fix": fix})

    @property
    def failed(self):
        return [r for r in self.rows if r["status"] == "FAIL"]

    def render(self):
        lines = []
        for r in self.rows:
            lines.append(f"{r['status']:<5} {r['check']}"
                         + (f": {r['detail']}" if r["detail"] else ""))
            if r["fix"]:
                lines.append(f"      fix: {r['fix']}")
        n = len(self.failed)
        skipped = sum(1 for r in self.rows if r["status"] == "skip")
        lines += ["", f"{len(self.rows) - n - skipped} ok, {n} failing"
                  + (f", {skipped} not checked" if skipped else "")
                  + ("" if n else " - mail is ready.")]
        return "\n".join(lines) + "\n"


def cmd_e2e(args):
    """The end-to-end check, against the sandbox mailbox and nothing else.

    draft to itself -> label it -> send it to itself -> search it back -> create and delete a
    throwaway event -> create and delete a second draft. Everything it makes, it removes; the
    one thing it leaves behind is the sent message in the sandbox mailbox.
    """
    policy = pl.load()
    box = policy["sandbox_mailbox"]
    if not box:
        raise Refused("no sandbox_mailbox in registry/mail-policy.yaml.",
                      "Set sandbox_mailbox: hub-test@acme.example there; --e2e will not touch a "
                      "real mailbox.")
    asked = (args.mailbox or "").strip().lower()
    if asked and asked != box:
        raise Refused(f"--e2e only ever runs against the sandbox mailbox {box}, not {asked}.",
                      "It sends real mail. If you want a different sandbox, change "
                      "sandbox_mailbox in registry/mail-policy.yaml, which is a hub PR.")
    c = Checks()
    tag = stamp().replace(":", "").replace("-", "")
    subject = f"hub mail e2e {tag}"
    body = ("This is the hub mail service checking itself end to end.\n\nNothing here needs "
            "an answer.\n\nAna\n")
    g, draft_id, event_id, second = None, "", "", ""
    try:
        g = open_gmail(box)
        raw = compose.build([box], subject, body, from_addr=box)
        d = g.create_draft(raw)
        draft_id = d.get("id", "")
        message_id = (d.get("message") or {}).get("id", "")
        c.ok("draft to itself", f"draft {draft_id}")
        try:
            lb.ensure(g, [lb.DRAFTED])
            g.modify(message_id, [lb.resolve(g, lb.DRAFTED)], [])
            c.ok("label the draft", lb.DRAFTED)
        except Failure as e:
            c.fail("label the draft", e.msg, e.hint)
        sent = g.send_draft(draft_id)
        draft_id = ""
        c.ok("send to itself", f"message {sent.get('id', '?')}")
        found = g.list_ids(f'subject:"{subject}"', limit=5)
        if found:
            c.ok("search it back", f"{len(found)} hit(s) for subject:\"{subject}\"")
        else:
            c.fail("search it back", f"no hit for subject:\"{subject}\"",
                   "Gmail may not have indexed it yet; run doctor --e2e again in a minute.")
        try:
            calendar = cal.Calendar(open_calendar(box), box)
            start = datetime.now(zone(DEFAULT_TZ)) + timedelta(days=2)
            start = start.replace(hour=12, minute=0, second=0, microsecond=0)
            ev = calendar.create_event(start, start + timedelta(minutes=15),
                                       f"hub mail e2e {tag} (delete me)")
            event_id = ev.get("id", "")
            calendar.delete_event(event_id)
            event_id = ""
            c.ok("throwaway event", "created and deleted")
        except Failure as e:
            c.fail("throwaway event", e.msg, e.hint)
        second = (g.create_draft(compose.build([box], subject + " (draft only)", body,
                                               from_addr=box)) or {}).get("id", "")
        g.delete_draft(second)
        second = ""
        c.ok("delete a draft", "created and deleted")
    except Failure as e:
        c.fail("end to end", e.msg, e.hint)
    finally:
        for cleanup, what in ((draft_id, "draft"), (second, "draft")):
            if cleanup and g:
                try:
                    g.delete_draft(cleanup)
                except Failure:                          # pragma: no cover
                    c.fail("cleanup", f"could not delete the {what} {cleanup}",
                           f"Delete it by hand in {box}'s Drafts.")
    rc = 1 if c.failed else 0
    if args.json:
        print(json.dumps({"ok": rc == 0, "mailbox": box, "checks": c.rows}, indent=2))
    else:
        print(f"end-to-end against {box} (the sandbox mailbox)\n")
        print(c.render(), end="")
    return rc


def cmd_mint_token(args):
    """A short-lived access token for one mailbox, for the runner's supervisor to hand a turn
    (runner/credential_socket.py). Prints the token and its expiry; the key itself never leaves."""
    token, expiry = auth.mint_token(args.mailbox, args.service)
    print(json.dumps({"token": token, "expiry": expiry}))
    return 0


def cmd_doctor(args):
    if getattr(args, "e2e", False):
        return cmd_e2e(args)
    c = Checks()
    info = None
    socket_mode = bool(os.environ.get(auth.SOCKET_ENV))
    if socket_mode:
        c.ok("credential source", "runner supervisor socket (key remains on the runner)")
    else:
        try:
            info = auth.key_info()
            c.ok("service-account key", f"{info['path']} (mode 600, parses)")
            c.ok("service account", info["client_email"])
            c.ok("client id", info["client_id"] + "  (this is what the Admin console wants)")
            if info["project_id"]:
                c.ok("google cloud project", info["project_id"])
        except Failure as e:
            c.fail("service-account key", e.msg, e.hint)

    try:
        import google.oauth2                              # noqa: F401
        import googleapiclient                            # noqa: F401
        c.ok("google client libraries", "installed")
    except ImportError:
        c.fail("google client libraries", "not importable",
               "Run mail through scripts/mail.sh; it builds the venv from "
               "connectors/mail/requirements.txt on first use.")

    holders = access.mailbox_holders()
    if args.mailbox and args.all_mailboxes:
        raise Refused("--mailbox and --all-mailboxes ask for different things; pick one.")
    boxes = [args.mailbox.strip().lower()] if args.mailbox else sorted(holders)
    if not boxes:
        c.fail("mailboxes", "no employee declares a gmail identity",
               "Add a `tools:` entry with service: gmail and an identity to a "
               "bot's bot.yaml (hub policies/access.md).")

    for box in boxes:
        slugs = holders.get(box) or []
        if c.failed:                                      # no key, no libraries: nothing to try
            c.skip(box, "impersonation, labels and calendar not checked",
                   "fix the failures above, then run doctor again")
            continue
        try:
            g = open_gmail(box)
            prof = g.profile()
            c.ok(f"{box} impersonation",
                 f"{prof.get('emailAddress', box)}, {prof.get('messagesTotal', '?')} messages"
                 + (f", used by {', '.join(slugs)}" if slugs else ""))
        except Failure as e:
            c.fail(f"{box} impersonation", e.msg,
                   e.hint or auth.delegation_hint(auth.GMAIL_SCOPE,
                                                  (info or {}).get("client_id", "NNN")))
            continue
        except Exception as e:
            c.fail(f"{box} impersonation", f"unexpected {type(e).__name__}",
                   "Check the runner credential socket and mailbox access, then run doctor again.")
            continue
        want = sorted({n for s in (slugs or [access.OWNER]) for n in lb.wanted(s)})
        try:
            existing = g.label_ids()
            missing = [name for name in want if name not in existing]
            if missing:
                c.fail(f"{box} hub labels", f"{len(missing)} missing: {', '.join(missing)}",
                       "Create the missing hub/* labels in Gmail, then rerun doctor.")
            else:
                c.ok(f"{box} hub labels", f"{len(want)} present")
        except Failure as e:
            c.fail(f"{box} hub labels", e.msg, e.hint)
        except Exception as e:
            c.fail(f"{box} hub labels", f"unexpected {type(e).__name__}",
                   "Check mailbox label access, then rerun doctor.")
        try:
            open_calendar(box).calendarList().list(maxResults=1).execute()
            c.ok(f"{box} calendar", "readable")
        except Failure as e:
            c.fail(f"{box} calendar", e.msg, e.hint)
        except Exception as e:
            c.fail(f"{box} calendar", str(e),
                   auth.delegation_hint(auth.CALENDAR_SCOPE,
                                        (info or {}).get("client_id", "NNN")))

    rc = 1 if c.failed else 0
    if args.json:
        print(json.dumps({"ok": rc == 0, "checks": c.rows}, indent=2))
    else:
        print(c.render(), end="")
    return rc


# ---------------------------------------------------------------- whoami

def cmd_whoami(args):
    slug = access.employee(args.as_slug)
    d = access.describe(slug)
    d["key"] = str(auth.key_path())
    d["rules_file"] = str(RULES_FILE)
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    print(f"employee  {d['employee']}" + ("  (owner: every verb on every mailbox)"
                                          if d["owner"] else ""))
    calendars = d.get("company_calendars") or {}
    if calendars.get("mailboxes"):
        print(f"calendars company-wide: {', '.join(calendars['can'])} on "
              f"{', '.join(calendars['mailboxes'])}")
    if not d["mailboxes"]:
        print("mailboxes none declared" + (f" - {d['note']}" if d["note"] else ""))
        print("          Adding one is the owner's call: open an Issue with owner:" + access.OWNER + " and "
              "type:decision (policies/access.md).")
        return 0
    for m in d["mailboxes"]:
        print(f"mailbox   {m['mailbox']}")
        print(f"          gmail: {', '.join(m['gmail']) or 'none'}"
              f"   calendar: {', '.join(m['google-calendar']) or 'none'}")
    print("          read also allows label, archive, mark-read, star and triaged on that "
          "mailbox (docs/mail.md).")
    return 0


# ---------------------------------------------------------------- reading

def remember_message(ctx, msg):
    """Opportunistic local persist. No-op on dry-run or a ctx without a db."""
    if getattr(ctx, "dry", False):
        return
    conn, mailbox = getattr(ctx, "conn", None), getattr(ctx, "mailbox", None)
    if conn is None or not mailbox or not msg.get("id"):
        return
    db.upsert_message(conn, mailbox, msg)


def fetch(ctx, ids):
    msgs = []
    for i in ids:
        m = ctx.normalize(ctx.gmail.get(i))
        remember_message(ctx, m)
        msgs.append(m)
    return msgs


def render(args, msgs, title):
    """md is the full record; brief is the listing (no bodies). json is handled by out()."""
    if getattr(args, "format", "") == "brief":
        return gm.render_brief(msgs, title)
    return gm.render_md(msgs, title)


INBOX_LIMIT, UNTRIAGED_LIMIT = 50, 200


def command_mailboxes(args, verb):
    """One mailbox, or every mailbox this employee can use for `verb`."""
    slug = access.employee(getattr(args, "as_slug", None))
    if getattr(args, "mailbox", None) and getattr(args, "all_mailboxes", False):
        raise Refused("--mailbox and --all-mailboxes ask for different things; pick one.")
    if getattr(args, "all_mailboxes", False):
        boxes = access.readable_mailboxes(slug, verb)
        if not boxes:
            raise Refused(f"{slug} has no mailbox for {verb}.",
                          "Declare gmail access or set inbox_bot / org_read on the roster.")
        return boxes
    box, _ = access.resolve(slug, getattr(args, "mailbox", None), verb)
    return [box]


def hub_labels(gmail):
    """Every hub/* label that exists in this mailbox, sorted, names only."""
    return sorted(n for n in gmail.labels().values() if str(n).startswith(lb.PREFIX))


def cmd_inbox(args):
    boxes = command_mailboxes(args, "read")
    if len(boxes) > 1:
        payloads, texts, total = [], [], 0
        for box in boxes:
            ctx = Ctx(args, "read", mailbox=box)
            if args.untriaged:
                payload, text = inbox_untriaged(ctx, args, emit=False)
            else:
                payload, text = inbox_listed(ctx, args, emit=False)
            payload["mailbox"] = box
            payloads.append(payload)
            texts.append(text.rstrip())
            total += payload.get("count") or 0
        return out(args, {"ok": True, "employee": access.employee(args.as_slug),
                          "all_mailboxes": True, "count": total, "mailboxes": payloads},
                   "\n\n".join(texts) + "\n")
    ctx = Ctx(args, "read", mailbox=boxes[0])
    if args.untriaged:
        return inbox_untriaged(ctx, args)
    return inbox_listed(ctx, args)


def inbox_listed(ctx, args, emit=True):
    triaged = lb.triaged(ctx.slug)
    q = ['in:inbox', f'-label:"{triaged}"']
    if args.label:
        q.append(f'label:"{args.label}"')
    floor = None
    if args.since:
        floor = parse_since(args.since)
    elif args.new:
        _, updated = db.get_watermark(ctx.conn, ctx.mailbox, ctx.slug)
        if updated:
            floor = parse_since(updated)
    if floor:
        q.append(f"after:{int(floor.timestamp())}")
    limit = args.limit or INBOX_LIMIT
    ids = ctx.gmail.list_ids(" ".join(q), limit=limit)
    if args.new:
        already = db.seen_ids(ctx.conn, ctx.mailbox, ctx.slug)
        ids = [i for i in ids if i not in already]
    msgs = [m for m in fetch(ctx, ids) if triaged not in m["labels"]]
    if args.new:
        for m in msgs:
            db.mark_seen(ctx.conn, ctx.mailbox, ctx.slug, m["id"], m["thread_id"])
        db.set_watermark(ctx.conn, ctx.mailbox, ctx.slug, _history_id(ctx))
    ctx.audit("inbox", ctx.mailbox,
              {"count": len(msgs), "new_only": bool(args.new), "query": " ".join(q),
               "ids": [m["id"] for m in msgs]})
    title = f"Inbox for {ctx.mailbox} ({'new to ' + ctx.slug if args.new else 'all'})"
    payload = {"ok": True, "mailbox": ctx.mailbox, "employee": ctx.slug,
               "query": " ".join(q), "count": len(msgs), "messages": msgs}
    if getattr(args, "judge", False):
        payload["judge"] = judge_listing(ctx, msgs)
    text = render(args, msgs, title)
    if emit:
        return out(args, payload, text)
    return payload, text


def inbox_untriaged(ctx, args, emit=True):
    """`in:inbox` minus anything already carrying a hub/* label. Label-based and idempotent:
    it reads no `seen` row and moves no watermark, so a bot can run it after a pass to prove
    the pass is complete, and again tomorrow, and get the same answer for the same mailbox."""
    excluded = hub_labels(ctx.gmail)
    q = ["in:inbox"] + [f'-label:"{n}"' for n in excluded]
    if args.label:
        q.append(f'label:"{args.label}"')
    limit = args.limit or UNTRIAGED_LIMIT
    ids = ctx.gmail.list_ids(" ".join(q), limit=limit)
    msgs = [m for m in fetch(ctx, ids)                  # Gmail's index can lag a relabel
            if not any(str(l).startswith(lb.PREFIX) for l in m["labels"])]
    ctx.audit("inbox", ctx.mailbox,
              {"untriaged": True, "count": len(msgs), "limit": limit, "query": " ".join(q),
               "excluded_labels": excluded, "ids": [m["id"] for m in msgs]})
    title = f"Inbox for {ctx.mailbox} (untriaged: no hub/* label)"
    payload = {"ok": True, "mailbox": ctx.mailbox, "employee": ctx.slug,
               "untriaged": True, "excluded_labels": excluded, "query": " ".join(q),
               "limit": limit, "count": len(msgs), "messages": msgs}
    if getattr(args, "judge", False):
        payload["judge"] = judge_listing(ctx, msgs)
    text = render(args, msgs, title)
    if emit:
        return out(args, payload, text)
    return payload, text


def judge_listing(ctx, msgs):
    """`--decisions`: the decision model reads the listing before any thread is opened (connectors/mail/judge.py).

    Read-only: a `judgment` on each message, a `decision:` line under it in the brief listing, one
    audit line with the counts and the suggestion per id, never a label or a body."""
    engine = jd.engine()
    if engine is None:
        raise Failure("no decision model is available to this process.", jd.unavailable_hint())
    stats = jd.triage(msgs, engine, ctx.mailbox, ctx.conn)
    ctx.audit("judge", ctx.mailbox, stats)
    return stats


def _history_id(ctx):
    try:
        return str((ctx.gmail.profile() or {}).get("historyId") or "")
    except Failure:
        return ""


def cmd_thread(args):
    ctx = Ctx(args, "read")
    raw = ctx.gmail.thread(args.thread_id)
    msgs = sorted((ctx.normalize(m) for m in raw), key=lambda m: m["epoch"])
    for m in msgs:
        remember_message(ctx, m)
    ctx.audit("thread", args.thread_id, {"messages": len(msgs)})
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "thread": args.thread_id,
                      "count": len(msgs), "messages": msgs},
               render(args, msgs, f"Thread {args.thread_id} ({ctx.mailbox})"))


def unsubscribe_message(ctx, message_id):
    """Shared by the explicit command and the deterministic rules pass."""
    import base64
    from . import unsubscribe as unsub
    access.resolve(ctx.slug, ctx.mailbox, "unsubscribe")
    raw = ctx.gmail.get(message_id, fmt="raw")
    ctx.audit("unsubscribe-read", message_id)
    try:
        encoded = raw.get("raw", "")
        if not encoded:
            raise unsub.Unsupported("Raw message unavailable; manual unsubscribe needed.")
        url = unsub.endpoint(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        result = unsub.submit(ctx.conn, ctx.mailbox, url, dry_run=ctx.dry)
    except unsub.Unsupported as e:
        result = {"accepted": False, "status": "manual_required", "reason": str(e)}
    result.update(ok=True, message=message_id, dry_run=ctx.dry, archived=False)
    ctx.audit("unsubscribe", message_id, result)
    return result


def cmd_unsubscribe(args):
    ctx = Ctx(args, "unsubscribe")
    result = unsubscribe_message(ctx, args.message_id)
    if args.archive and result.get("accepted") and not ctx.dry:
        modify(ctx, [args.message_id], remove=["INBOX"], action="archive",
               detail={"after_unsubscribe": True})
        result["archived"] = True
    return out(args, result, "Unsubscribe: " + result["status"] +
               ("; sender accepted the request" if result["accepted"] else
                "; no successful cancellation confirmed") + "\n")


def cmd_search(args):
    ctx = Ctx(args, "read")
    if args.limit > MAX_SEARCH:
        raise Refused(f"search is bounded to {MAX_SEARCH} results, --limit was {args.limit}.",
                      "Narrow the query instead: Gmail's own operators (from:, newer_than:, "
                      "has:attachment) are cheaper than a big page.")
    ids = ctx.gmail.list_ids(args.query, limit=args.limit)
    msgs = fetch(ctx, ids)
    ctx.audit("search", args.query, {"count": len(msgs), "ids": [m["id"] for m in msgs]})
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "query": args.query,
                      "count": len(msgs), "messages": msgs},
               render(args, msgs, f"Search: {args.query}"))


def _public_attachment(item):
    return {k: item[k] for k in ("ref", "name", "type", "size", "part_id",
                                  "attachment_id", "inline")}


def cmd_attachments(args):
    ctx = Ctx(args, "read")
    raw = ctx.gmail.get(args.message_id)
    attachments = [_public_attachment(a)
                   for a in gm.attachment_refs((raw or {}).get("payload") or {})]
    ctx.audit("attachments", args.message_id,
              {"count": len(attachments), "refs": [a["ref"] for a in attachments]})
    lines = [f"attachments for {args.message_id} in {ctx.mailbox}"]
    lines += [f"{a['ref']}  {a['name']}  {a['type']}  {a['size']}B"
              f"  attachment_id={a['attachment_id'] or '-'}  part_id={a['part_id']}"
              for a in attachments]
    if not attachments:
        lines.append("no downloadable attachments")
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "message_id": args.message_id,
                      "count": len(attachments), "attachments": attachments},
               "\n".join(lines) + "\n")


def _private_output_path(value):
    """Validate a caller-selected output without resolving through any symlink."""
    text = str(value or "")
    if not text or "\0" in text:
        raise Refused("--out must be an explicit file path.")
    candidate = Path(text)
    if any(part == ".." for part in candidate.parts):
        raise Refused("--out may not contain '..' path traversal.",
                      "Choose the exact destination file without parent-directory shortcuts.")
    if candidate.name in ("", ".", ".."):
        raise Refused("--out must name a file, not a directory.")
    target = candidate if candidate.is_absolute() else Path.cwd() / candidate
    current = Path(target.anchor)
    for part in target.parts[1:-1]:
        current = current / part
        if current.is_symlink():
            raise Refused(f"--out may not pass through symlink {current}.",
                          "Choose a real directory and a new destination filename.")
    if not target.parent.exists() or not target.parent.is_dir():
        raise Failure(f"the output directory does not exist: {target.parent}",
                      "Create the private destination directory first.")
    if target.is_symlink():
        raise Refused(f"--out may not be a symlink: {target}",
                      "Choose a new destination filename.")
    if target.exists():
        raise Refused(f"refusing to overwrite existing file: {target}",
                      "Choose a new --out path; attachment downloads never overwrite.")
    return target


def _write_private(target, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise Refused(f"refusing to overwrite existing file: {target}",
                      "Choose a new --out path; attachment downloads never overwrite.")
    except OSError as e:
        raise Failure(f"could not create private output file {target}: {e}")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception as e:
        if fd >= 0:
            os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        raise Failure(f"could not write private output file {target}: {e}")


def cmd_attachment(args):
    # Resolve the employee's read authority before either Gmail call can happen.
    ctx = Ctx(args, "read")
    if not gm.valid_attachment_ref(args.attachment_ref):
        raise Refused(f"unsafe attachment reference: {args.attachment_ref!r}.",
                      "Run `mail attachments MESSAGE_ID` and use a reference like a1.")
    target = _private_output_path(args.out)
    raw = ctx.gmail.get(args.message_id)
    attachments = gm.attachment_refs((raw or {}).get("payload") or {})
    item = next((a for a in attachments if a["ref"] == args.attachment_ref), None)
    if item is None:
        raise Refused(f"message {args.message_id} has no attachment {args.attachment_ref}.",
                      "Run `mail attachments MESSAGE_ID` to list valid references.")
    if item["size"] > gm.MAX_ATTACHMENT_BYTES:
        raise Failure(f"attachment is {item['size']} bytes; the download limit is "
                      f"{gm.MAX_ATTACHMENT_BYTES} bytes.",
                      "Retrieve unusually large files directly in Gmail.")
    if item["inline"]:
        encoded, declared = item["_data"], item["size"]
    elif item["attachment_id"]:
        fetched = ctx.gmail.get_attachment(args.message_id, item["attachment_id"]) or {}
        encoded = fetched.get("data")
        try:
            fetched_size = max(0, int(fetched.get("size") or 0))
        except (TypeError, ValueError):
            fetched_size = 0
        declared = max(item["size"], fetched_size)
    else:
        raise Failure(f"attachment {args.attachment_ref} has neither inline data nor a Gmail "
                      "attachment id.", "Open the message in Gmail and retrieve the file there.")
    data = gm.attachment_bytes(encoded, declared)
    _write_private(target, data)
    detail = {"ref": item["ref"], "attachment_id": item["attachment_id"],
              "part_id": item["part_id"], "type": item["type"], "bytes": len(data),
              "path": str(target)}
    ctx.audit("attachment-download", args.message_id, detail)
    payload = {"ok": True, "mailbox": ctx.mailbox, "message_id": args.message_id,
               "attachment_ref": item["ref"], "attachment_id": item["attachment_id"],
               "part_id": item["part_id"], "name": item["name"], "type": item["type"],
               "size": len(data), "path": str(target)}
    return out(args, payload,
               f"saved {len(data)} bytes from message {args.message_id} attachment "
               f"{item['ref']} ({item['attachment_id'] or item['part_id']}) to {target}\n")


# ---------------------------------------------------------------- state changes

def modify(ctx, ids, add=(), remove=(), action="modify", detail=None):
    """One state change over a list of messages, audited per message."""
    add_ids = [lb.resolve(ctx.gmail, n) for n in add] if not ctx.dry else list(add)
    rm_ids = [lb.resolve(ctx.gmail, n) for n in remove] if not ctx.dry else list(remove)
    done = []
    for mid in ids:
        if not ctx.dry:
            ctx.gmail.modify(mid, add_ids, rm_ids)
        ctx.audit(action, mid, dict(detail or {}, add=list(add), remove=list(remove)))
        done.append(mid)
    return done


def cmd_label(args):
    ctx = Ctx(args, "label")
    if len(args.rest) < 2:
        raise Refused("label needs at least one message id and one label name.",
                      "mail label add <msg-id> [<msg-id> ...] hub/needs-owner")
    ids, name = args.rest[:-1], lb.check_name(args.rest[-1])
    add, remove = ([name], []) if args.op == "add" else ([], [name])
    done = modify(ctx, ids, add, remove, f"label-{args.op}", {"label": name})
    return out(args, {"ok": True, "op": args.op, "label": name, "messages": done,
                      "dry_run": ctx.dry},
               f"{'would ' if ctx.dry else ''}{args.op} {name} "
               f"{'to' if args.op == 'add' else 'from'} {len(done)} message(s) in {ctx.mailbox}")


def cmd_archive(args):
    ctx = Ctx(args, "archive")
    done = modify(ctx, args.ids, remove=["INBOX"], action="archive")
    return out(args, {"ok": True, "messages": done, "dry_run": ctx.dry},
               f"{'would archive' if ctx.dry else 'archived'} {len(done)} message(s) "
               f"in {ctx.mailbox}")


def cmd_mark_read(args):
    ctx = Ctx(args, "mark_read")
    done = modify(ctx, args.ids, remove=["UNREAD"], action="mark-read")
    return out(args, {"ok": True, "messages": done, "dry_run": ctx.dry},
               f"{'would mark' if ctx.dry else 'marked'} {len(done)} message(s) read "
               f"in {ctx.mailbox}")


def cmd_star(args):
    ctx = Ctx(args, "star")
    done = modify(ctx, [args.msg_id], add=["STARRED"], action="star")
    return out(args, {"ok": True, "messages": done, "dry_run": ctx.dry},
               f"{'would star' if ctx.dry else 'starred'} {args.msg_id} in {ctx.mailbox}")


def cmd_triaged(args):
    ctx = Ctx(args, "triaged")
    name = lb.triaged(ctx.slug)
    done = modify(ctx, args.ids, add=[name], action="triaged", detail={"label": name})
    if not ctx.dry:
        for mid in done:
            db.mark_triaged(ctx.conn, ctx.mailbox, ctx.slug, mid)
        db.set_watermark(ctx.conn, ctx.mailbox, ctx.slug, _history_id(ctx))
    return out(args, {"ok": True, "label": name, "messages": done, "dry_run": ctx.dry},
               f"{'would mark' if ctx.dry else 'marked'} {len(done)} message(s) {name}")


# ---------------------------------------------------------------- rules

def rule_set(ctx):
    return rl.load(RULES_FILE, ctx.mailbox, access.bot_rules_file(ctx.mailbox))


def with_thread_state(ctx, msgs):
    """Fill in last_reply_by, but only when a rule actually needs it."""
    for m in msgs:
        m["last_reply_by"] = gm.thread_last_reply_by(
            [ctx.normalize(x) for x in ctx.gmail.thread(m["thread_id"])])
    return msgs


def with_judgments(ctx, rules, msgs):
    """Put each set the rules name to every message, before the rules run (`judge` in
    connectors/mail/rules.py). No decision model available is not an error here: the decision rules do not
    fire, the run says so in its audit line and its report, and every other rule runs as before."""
    sets = rl.uses_judge(rules)
    if not sets or not msgs:
        return None
    engine = jd.engine()
    if engine is None:
        ctx.audit("judge-skipped", ctx.mailbox, {"sets": sets, "reason": "no decision model available"})
        return {"skipped": True, "sets": sets, "reason": jd.unavailable_hint()}
    stats = jd.judge_sets(msgs, sets, engine, ctx.mailbox, ctx.conn)
    ctx.audit("judge", ctx.mailbox, {"for": "rules", "sets": stats, "messages": len(msgs)})
    return stats


def judge_line(judged):
    if not judged:
        return ""
    if judged.get("skipped"):
        return f"decision rules skipped ({', '.join(judged['sets'])}): no decision model available to this process"
    return "judged with " + ", ".join(f"{s['label']} over {s['count']} message(s)"
                                      + (f", {s['errors']} failed" if s["errors"] else "")
                                      for s in judged.values())


def apply_plan(ctx, msg, plan):
    """Turn a rule plan into Gmail calls, one audit line per rule that fired."""
    if plan.get("unsubscribe"):
        try:
            plan["unsubscribe_result"] = unsubscribe_message(ctx, msg["id"])
        except Refused:
            plan["unsubscribe_result"] = {"status": "not_authorized", "accepted": False}
            ctx.audit("unsubscribe-skipped", msg["id"], plan["unsubscribe_result"])
    add = list(plan["labels"])
    if plan["star"]:
        add.append("STARRED")
    remove = (["INBOX"] if plan["archive"] else []) + (["UNREAD"] if plan["mark_read"] else [])
    if not ctx.dry and (add or remove):
        add_ids = [lb.resolve(ctx.gmail, n) for n in add]
        rm_ids = [lb.resolve(ctx.gmail, n) for n in remove]
        ctx.gmail.modify(msg["id"], add_ids, rm_ids)
        labels = [n for n in (msg.get("labels") or []) if n not in remove]
        for n in add:
            if n not in labels:
                labels.append(n)
        msg["labels"] = labels
        remember_message(ctx, msg)
    for hit in plan["hits"]:
        if not ctx.dry:
            db.record_rule_hit(ctx.conn, ctx.mailbox, ctx.slug, msg["id"], hit["rule"],
                               hit["actions"])
        ctx.audit("rule", msg["id"], {"rule": hit["rule"], "actions": hit["actions"],
                                      "subject": msg["subject"][:120]})


def rules_run_one(ctx, args):
    rules = rule_set(ctx)
    floor = parse_since(args.since or "24h")
    ids = ctx.gmail.list_ids(f"in:inbox after:{int(floor.timestamp())}", limit=args.limit)
    msgs = fetch(ctx, ids)
    if rl.uses_thread(rules):
        with_thread_state(ctx, msgs)
    judged = with_judgments(ctx, rules, msgs)
    counts, lines = {}, []
    for m in msgs:
        plan = rl.apply(rules, m)
        if not plan["hits"]:
            continue
        apply_plan(ctx, m, plan)
        for hit in plan["hits"]:
            counts[hit["rule"]] = counts.get(hit["rule"], 0) + 1
        lines.append({"id": m["id"], "from": m["from"], "subject": m["subject"],
                      "mailbox": ctx.mailbox,
                      "rules": [h["rule"] for h in plan["hits"]], "labels": plan["labels"],
                      "archive": plan["archive"], "mark_read": plan["mark_read"],
                      "star": plan["star"], "never_archive": plan["never_archive"],
                      "blocked": plan["blocked"], "stopped_by": plan["stopped_by"],
                      "unsubscribe": plan.get("unsubscribe_result")})
    body = [f"# Rules for {ctx.mailbox}" + (" (dry run, nothing changed)" if ctx.dry else ""), "",
            f"{len(msgs)} message(s) since {args.since or '24h'}, {len(lines)} touched"]
    if judged:
        body.append(judge_line(judged))
    body += ["", f"{'rule':<40} messages"]
    for rid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        body.append(f"{rid:<40} {n}")
    if not counts:
        body.append("(no rule fired)")
    body.append("")
    for ln in lines:
        acts = ", ".join(filter(None, [
            "label " + "+".join(ln["labels"]) if ln["labels"] else "",
            "archive" if ln["archive"] else "",
            "mark-read" if ln["mark_read"] else "",
            "star" if ln["star"] else "",
            "never_archive" if ln["never_archive"] else ""]))
        body.append(f"- {ln['id']} {ln['from']}: {ln['subject'][:60]}")
        body.append(f"    {', '.join(ln['rules'])} -> {acts or 'nothing'}")
        for b in ln["blocked"]:
            body.append(f"    blocked: {b['rule']} wanted {b['action']} ({b['reason']})")
    payload = {"ok": True, "mailbox": ctx.mailbox, "dry_run": ctx.dry,
               "scanned": len(msgs), "counts": counts, "messages": lines, "judge": judged}
    return payload, "\n".join(body) + "\n"


def cmd_rules_run(args):
    boxes = command_mailboxes(args, "rules")
    if len(boxes) == 1:
        payload, text = rules_run_one(Ctx(args, "rules", mailbox=boxes[0]), args)
        return out(args, payload, text)
    payloads, texts, scanned = [], [], 0
    counts = {}
    for box in boxes:
        payload, text = rules_run_one(Ctx(args, "rules", mailbox=box), args)
        payloads.append(payload)
        texts.append(text.rstrip())
        scanned += payload.get("scanned") or 0
        for rid, n in (payload.get("counts") or {}).items():
            counts[rid] = counts.get(rid, 0) + n
    return out(args, {"ok": True, "all_mailboxes": True, "dry_run": bool(getattr(args, "dry_run", False)),
                      "scanned": scanned, "counts": counts, "mailboxes": payloads},
               "\n".join(texts) + "\n")


def cmd_rules_explain(args):
    ctx = Ctx(args, "rules")
    rules = rule_set(ctx)
    msg = ctx.normalize(ctx.gmail.get(args.msg_id))
    if rl.uses_thread(rules):
        with_thread_state(ctx, [msg])
    with_judgments(ctx, rules, [msg])
    detail = rl.explain(rules, msg)
    plan = rl.apply(rules, msg)
    body = [f"# {args.msg_id} in {ctx.mailbox}", "",
            f"from: {msg['from']}", f"subject: {msg['subject']}",
            f"unsubscribe: {'yes' if msg['unsubscribe'] else 'no'}",
            f"internal: {msg['is_internal']}", ""]
    for d in detail:
        body.append(f"{'FIRES ' if d['fired'] else '     '} {d['rule']}")
        for w in d["why"]:
            body.append(f"        {w}")
        if d["fired"]:
            body.append(f"        do: {json.dumps(d['do'], sort_keys=True)}")
    body += ["", "outcome: " + json.dumps(
        {k: plan[k] for k in ("labels", "archive", "mark_read", "star", "never_archive",
                              "stopped_by")}, sort_keys=True)]
    return out(args, {"ok": True, "id": args.msg_id, "rules": detail, "outcome": plan},
               "\n".join(body) + "\n")


def cmd_rules_test(args):
    """Run the rules against fixture files. Also what the unit tests and preflight use.

    Each fixture is JSON: {"mailbox": "...", "message": <raw gmail message>, "expect": {...}}.
    """
    d = Path(args.fixture_dir)
    files = sorted(d.glob("*.json"))
    if not files:
        raise Failure(f"no *.json fixtures in {d}")
    results, bad = [], 0
    for f in files:
        case = json.loads(f.read_text())
        mailbox = case.get("mailbox") or "ana@acme.example"
        msg = gm.normalize(case["message"])
        msg.setdefault("last_reply_by", case.get("last_reply_by", ""))
        if case.get("last_reply_by"):
            msg["last_reply_by"] = case["last_reply_by"]
        if case.get("judgments"):             # what the judge said, recorded with the fixture; offline
            msg["judgments"] = case["judgments"]
        plan = rl.apply(rl.load(args.rules or RULES_FILE, mailbox,
                                None if args.rules else access.bot_rules_file(mailbox)), msg)
        got = {"labels": sorted(plan["labels"]), "archive": plan["archive"],
               "mark_read": plan["mark_read"], "star": plan["star"],
               "never_archive": plan["never_archive"],
               "rules": [h["rule"] for h in plan["hits"]]}
        expect = case.get("expect") or {}

        def norm(v):                     # lists (labels, rules) compare as sets; order is a rules-file test
            return sorted(v) if isinstance(v, list) else v
        diff = {k: {"want": norm(v), "got": got.get(k)}
                for k, v in expect.items() if norm(v) != norm(got.get(k))}
        bad += 1 if diff else 0
        results.append({"fixture": f.name, "ok": not diff, "got": got, "diff": diff})
    body = [f"{'PASS' if r['ok'] else 'FAIL'}  {r['fixture']}"
            + ("" if r["ok"] else "  " + json.dumps(r["diff"], sort_keys=True))
            for r in results]
    body.append(f"\n{len(results) - bad}/{len(results)} fixtures match")
    out(args, {"ok": bad == 0, "results": results}, "\n".join(body) + "\n")
    return 1 if bad else 0


# ---------------------------------------------------------------- rules backtest (a read)

CONFLICT_LABELS = (lb.NEEDS_OWNER, lb.DRAFTED, lb.handled("owner"))
MACHINE_LOCAL_PARTS = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
                       "notifications", "notification", "mailer-daemon", "postmaster")
BACKTEST_SAMPLES = 5                    # sample subjects per rule
BACKTEST_LISTED = 40                    # subjects per diff bucket in md; json carries them all
BUCKETS = (
    ("newly_archived", "newly archived",
     lambda cur, cand: not cur["archive"] and cand["archive"]),
    ("no_longer_archived", "no longer archived",
     lambda cur, cand: cur["archive"] and not cand["archive"]),
    ("newly_protected", "newly protected",
     lambda cur, cand: not cur["never_archive"] and cand["never_archive"]),
    ("no_longer_protected", "no longer protected",
     lambda cur, cand: cur["never_archive"] and not cand["never_archive"]),
)
LISTED_BUCKETS = ("newly_archived", "no_longer_protected")


def human_internal_sender(addr):
    """A person at acme.example, as opposed to one of its machines."""
    addr = str(addr or "").lower()
    if not rl.in_domain(addr, [INTERNAL_DOMAIN]):
        return False
    local = addr.rsplit("@", 1)[0]
    return not any(t in local for t in MACHINE_LOCAL_PARTS)


def outcome(plan):
    """The slice of a rule plan the backtest keeps per message: no body, no why-lines."""
    return {"archive": bool(plan["archive"]), "never_archive": bool(plan["never_archive"]),
            "unsubscribe": bool(plan["unsubscribe"]), "labels": list(plan["labels"]),
            "hits": [{"rule": h["rule"], "actions": h["actions"]} for h in plan["hits"]]}


def line_of(row):
    return {"id": row["id"], "thread_id": row["thread_id"], "from": row["from"],
            "subject": gm.clip(row["subject"] or "(no subject)", 80)}


def rule_stats(rules, rows, key):
    """Per rule, in file order: how often it fired and what those hits did."""
    def blank(rid):
        return {"rule": rid, "hits": 0, "archive": 0, "protect": 0, "unsubscribe": 0,
                "blocked": 0, "samples": []}
    stats = {}
    for r in rules:
        stats.setdefault(r["id"], blank(r["id"]))
    for row in rows:
        for hit in row[key]["hits"]:
            s, a = stats.setdefault(hit["rule"], blank(hit["rule"])), hit["actions"]
            s["hits"] += 1
            s["archive"] += int(a.get("archive") is True)
            s["protect"] += int(bool(a.get("never_archive")))
            s["unsubscribe"] += int(a.get("unsubscribe") is True)
            s["blocked"] += int(any(v == "blocked" for v in a.values()))
            subj = gm.clip(row["subject"] or "(no subject)", 80)
            if subj not in s["samples"] and len(s["samples"]) < BACKTEST_SAMPLES:
                s["samples"].append(subj)
    return list(stats.values())


def diff_buckets(rows):
    buckets = {k: [] for k, _, _ in BUCKETS}
    for row in rows:
        for k, _, test in BUCKETS:
            if test(row["current"], row["candidate"]):
                buckets[k].append(line_of(row))
    return buckets


def find_conflicts(ctx, rows, key):
    """Messages the rule set would archive that something else says a human still owns."""
    drafted = db.draft_thread_ids(ctx.conn, ctx.mailbox)
    scheduled = db.scheduling_thread_ids(ctx.conn, ctx.mailbox)
    found = []
    for row in rows:
        if not row[key]["archive"]:
            continue
        why = [f"carries {name}" for name in CONFLICT_LABELS if name in row["labels"]]
        if row["thread_id"] in drafted:
            why.append("thread has a hub draft (mail.db drafts)")
        if row["thread_id"] in scheduled:
            why.append("thread has a scheduling action (mail.db scheduling_actions)")
        if human_internal_sender(row["from"]):
            why.append(f"human sender at {INTERNAL_DOMAIN}")
        if why:
            rules = [h["rule"] for h in row[key]["hits"] if h["actions"].get("archive") is True]
            found.append(dict(line_of(row), rules=rules, why=why))
    return found


def cmd_rules_backtest(args):
    """Replay the rules over a window of all mail and report what they would do.

    A read: nothing is labelled, archived or unsubscribed, no `seen` row, no watermark. With
    --rules the candidate file is run alongside the registry and the two outcomes are diffed.
    Only the id, thread, sender, subject, labels and rule outcomes of each message are kept.
    """
    ctx = Ctx(args, "read")
    current = rl.load(args.baseline, ctx.mailbox) if args.baseline else rule_set(ctx)
    candidate = rl.load(args.rules, ctx.mailbox) if args.rules else None
    floor = parse_since(args.since)
    q = f"after:{int(floor.timestamp())}"
    limit = max(1, int(args.limit or 1))
    ids = ctx.gmail.list_ids(q, limit=limit)
    need_thread = rl.uses_thread(current) or bool(candidate and rl.uses_thread(candidate))
    # A judge rule (rules.py, `judge`) needs the judge's answers on the message before the replay; a
    # backtest with none available is refused rather than reported as "never fires".
    sets = sorted(set(rl.uses_judge(current)) | set(rl.uses_judge(candidate) if candidate is not None else []))
    engine = jd.engine() if sets else None
    if sets and engine is None:
        raise Failure(f"the rules name a decision set ({', '.join(sets)}) and no decision model is available here.",
                      jd.unavailable_hint())
    now, last_reply, rows, skipped, judged = now_utc(), {}, [], [], {}
    for start in range(0, len(ids), jd.PARALLEL):    # judged a few at a time, bodies dropped after
        batch = []
        for i in ids[start:start + jd.PARALLEL]:
            # a replay over thousands of messages must not die on one Gmail refusal (a thread in
            # spam or trash answers 400): skip it, count it, say so in the report
            try:
                m = ctx.normalize(ctx.gmail.get(i))
            except Failure as exc:
                skipped.append({"id": i, "why": str(exc)[:120]})
                continue
            if need_thread:
                tid = m["thread_id"]
                if tid not in last_reply:
                    try:
                        last_reply[tid] = gm.thread_last_reply_by(
                            [ctx.normalize(x) for x in ctx.gmail.thread(tid)])
                    except Failure:
                        last_reply[tid] = ""
                m["last_reply_by"] = last_reply[tid]
            batch.append(m)
        if sets and batch:
            for sid, stat in jd.judge_sets(batch, sets, engine, ctx.mailbox, ctx.conn).items():
                total = judged.setdefault(sid, {"label": stat["label"], "count": 0, "errors": 0,
                                                "cached": 0})
                total["count"] += stat["count"]
                total["errors"] += stat["errors"]
                total["cached"] += stat["cached"]
        for m in batch:
            row = {"id": m["id"], "thread_id": m["thread_id"], "from": m["from"],
                   "subject": m["subject"], "labels": list(m["labels"]),
                   "in_inbox": "INBOX" in m["labels"], "current": outcome(rl.apply(current, m, now))}
            if candidate is not None:
                row["candidate"] = outcome(rl.apply(candidate, m, now))
            rows.append(row)                            # the body goes with m, here
    key = "candidate" if candidate is not None else "current"

    def tally(k):
        archived = sum(1 for r in rows if r[k]["archive"])
        return {"archived": archived,
                "protected": sum(1 for r in rows if r[k]["never_archive"]),
                "unsubscribed": sum(1 for r in rows if r[k]["unsubscribe"]),
                "reach_model": len(rows) - archived,
                "reach_model_in_inbox": sum(1 for r in rows
                                            if r["in_inbox"] and not r[k]["archive"])}
    summary = {"current": tally("current")}
    if candidate is not None:
        summary["candidate"] = tally("candidate")
    stats = rule_stats(candidate if candidate is not None else current, rows, key)
    buckets = diff_buckets(rows) if candidate is not None else None
    conflicts = find_conflicts(ctx, rows, key)
    rules_file = str(args.rules) if args.rules else str(RULES_FILE)
    ctx.audit("backtest", ctx.mailbox,                  # counts only: no subject, no id
              {"since": args.since, "window_start": floor.isoformat(), "query": q,
               "limit": limit, "messages": len(rows), "skipped": len(skipped),
               "baseline": str(args.baseline or ""),
               "candidate": rules_file if candidate is not None else "",
               "summary": summary, "judge": judged or None,
               "diff": {k: len(v) for k, v in buckets.items()} if buckets is not None else None,
               "conflicts": len(conflicts)})

    payload = {"ok": True, "mailbox": ctx.mailbox, "employee": ctx.slug, "since": args.since,
               "window_start": floor.isoformat(), "query": q, "limit": limit,
               "messages": len(rows), "skipped": skipped, "rules_file": rules_file,
               "baseline": str(args.baseline or ""), "judge": judged or None,
               "reported": key, "summary": summary, "rules": stats,
               "diff": ({k: {"count": len(v), "messages": v} for k, v in buckets.items()}
                        if buckets is not None else None),
               "conflicts": conflicts, "outcomes": rows}
    return out(args, payload, render_backtest(payload, stats, buckets, conflicts))


def render_backtest(payload, stats, buckets, conflicts):
    n, key, summary = payload["messages"], payload["reported"], payload["summary"]
    against = payload.get("baseline") or "the registry"
    which = ("candidate " + payload["rules_file"] + " against " + against
             if key == "candidate" else "registry " + payload["rules_file"])
    body = [f"# Rules backtest for {payload['mailbox']} (a read: nothing changed)", "",
            f"window: since {payload['since']} ({payload['window_start'][:16]}), "
            f"{n} message(s) of all mail, not only the inbox (limit {payload['limit']})"
            + (f"; {len(payload['skipped'])} skipped (Gmail refused them)" if payload.get("skipped") else ""),
            f"rules: {which}",
            "", f"## Per rule ({key})", "",
            f"{'rule':<40} {'hits':>5} {'archive':>8} {'protect':>8} {'unsub':>6} {'blocked':>8}"]
    if payload.get("judge"):
        body.insert(4, "decisions: " + "; ".join(
            f"{sid} {stat['count']} judged, {stat['cached']} cached, {stat['errors']} errors"
            for sid, stat in payload["judge"].items()))
    for s in stats:
        body.append(f"{s['rule']:<40} {s['hits']:>5} {s['archive']:>8} {s['protect']:>8} "
                    f"{s['unsubscribe']:>6} {s['blocked']:>8}")
        body += [f"    - {subj}" for subj in s["samples"]]
    if not stats:
        body.append("(no rules for this mailbox)")
    if buckets is not None:
        body += ["", "## Candidate vs current", ""]
        body += [f"{label}: {len(buckets[k])}" for k, label, _ in BUCKETS]
        for k, label, _ in BUCKETS:
            if k not in LISTED_BUCKETS or not buckets[k]:
                continue
            body += ["", f"### {label} ({len(buckets[k])})"]
            body += [f"- {x['id']} {x['from']}: {x['subject']}"
                     for x in buckets[k][:BACKTEST_LISTED]]
            if len(buckets[k]) > BACKTEST_LISTED:
                body.append(f"- ... and {len(buckets[k]) - BACKTEST_LISTED} more "
                            "(--format json lists them all)")
    body += ["", f"## Conflicts ({len(conflicts)})", ""]
    if conflicts:
        body.append(f"Messages the {key} rules would archive that a human still owns:")
        for c in conflicts:
            body.append(f"- {c['id']} {c['from']}: {c['subject']}")
            body.append(f"    would archive ({', '.join(c['rules']) or '?'}); "
                        + "; ".join(c["why"]))
    else:
        body.append("none")
    body += ["", "## Would reach the model", ""]
    for k in ("current", "candidate"):
        if k in summary:
            t = summary[k]
            body.append(f"{k}: {t['reach_model']} of {n} not archived by the rules "
                        f"(of which {t['reach_model_in_inbox']} in the inbox now); "
                        f"{t['archived']} archived, {t['protected']} protected, "
                        f"{t['unsubscribed']} unsubscribed")
    return "\n".join(body) + "\n"


# ---------------------------------------------------------------- writing (stage 2)

REVIEW_RULES = (
    "forbidden phrases from docs/company.md",
    "URLs: acme.example hosts only, no calendly, no presigned links, the employee's required CTA",
    "placeholders, secret-shaped strings, internal paths and Issue numbers",
    "subject and body length, exclamation marks, a signature, one external recipient",
    "times offered: at least two, chronological, future, weekdays, business hours, named zone",
    "no confirmation without an invite",
)


def body_of(path):
    p = Path(path)
    if not p.exists():
        raise Failure(f"there is no body file at {p}",
                      "Write the message to a file first: --body-file <path>.")
    text = p.read_text(errors="replace").strip()
    if not text:
        raise Refused(f"{p} is empty; there is nothing to draft.")
    return text


MAX_ATTACH_BYTES = gm.MAX_ATTACHMENT_BYTES   # Gmail's own limit, for all of a message's files


def attachments_of(paths):
    """[{name, type, size, sha256, data}] for `--attach`, refused before any gate runs when a file
    is missing or the files together pass Gmail's 25 MB."""
    found, total = [], 0
    for path in paths or []:
        p = Path(path).expanduser()
        if not p.is_file():
            raise Refused(f"there is no file to attach at {p}.",
                          "Pass the path of a file on this computer: --attach <path>.")
        data = p.read_bytes()
        total += len(data)
        if total > MAX_ATTACH_BYTES:
            raise Refused(f"the attachments come to more than {MAX_ATTACH_BYTES // (1024 * 1024)} MB, "
                          "Gmail's limit for one message.",
                          "Attach fewer or smaller files, or put the big one on the task and link it.")
        kind = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        found.append({"name": gm.safe_filename(p.name), "type": kind, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "data": data})
    return found


def attachment_meta(atts):
    """What the audit, the payload and the reviewer see of each file: never its bytes."""
    return [{k: a.get(k) for k in ("name", "type", "size", "sha256") if a.get(k) is not None}
            for a in atts or []]


def thread_context(ctx, thread_id):
    """(messages, in-reply-to, references, subject, participants) for a reply."""
    raw = ctx.gmail.thread(thread_id)
    if not raw:
        raise Refused(f"there is no thread {thread_id} in {ctx.mailbox}.",
                      "Take the thread id from `mail inbox` or `mail thread`.")
    msgs = sorted((ctx.normalize(m) for m in raw), key=lambda m: m["epoch"])
    mid, refs, subject, people = compose.thread_headers(msgs)
    return msgs, mid, refs, subject, people


def thread_text(msgs, limit=6000):
    """The incoming conversation as plain text, for the reviewer."""
    blocks = [f"From: {m['from']}   Date: {m['date']}\nSubject: {m['subject']}\n\n{m['body']}"
              for m in msgs or []]
    return "\n\n----\n\n".join(blocks)[:limit]


def busy_window(ctx, days=60):
    now = datetime.now(zone(DEFAULT_TZ))
    return ctx.calendar.busy(now, now + timedelta(days=days))


def run_review(ctx, body, subject, incoming="", attachments=()):
    """The reviewer sees what the employee is *for*: its Role, plus its standing allowance.

    Without the allowance an approved standing offer reads as a fresh commitment, and every
    cold message fails. With it, the reviewer judges the draft against the permission the
    employee actually has, and anything past that edge is still a commitment.
    """
    purpose = rv.purpose_of(ctx.slug)
    if ctx.slug == access.OWNER:
        purpose = ("The owner, the founder, using the mail tool by hand from their own "
                   "mailboxes. What they write is their own decision; judge only accuracy and tone.")
    try:
        allowance = pl.allowance_for(ctx.policy, ctx.slug, ctx.mailbox)
    except Failure:                                     # a broken policy file: draft has already
        allowance = None                                # refused, but review must not crash
    if allowance and allowance.get("purpose"):
        purpose = (purpose + "\n\nStanding allowance (registry/mail-policy.yaml): "
                   + allowance["purpose"]).strip()
    if attachments:                                     # the reviewer reads the text; it is told the files
        body = (body + "\n\n[Attached files: "
                + "; ".join(f"{a['name']} ({a.get('type') or '?'}, {a.get('size') or 0} bytes)"
                            for a in attachments) + "]")
    return rv.review(body, subject, incoming, purpose=purpose,
                     company=rv.company_context(), rules=REVIEW_RULES)


def lint_line(res):
    return "; ".join(f"{x['id']} {x['message']}" for x in res["errors"])


def label_thread(ctx, name, msgs, fallback_ids=()):
    ids = [m["id"] for m in msgs or []] or [i for i in fallback_ids if i]
    if ids:
        modify(ctx, ids, add=[name], action="label-add", detail={"label": name})
    return ids


def make_draft(ctx, to, subject, body, reply_to="", cc=(), slot=None,
               check_calendar=False, minutes=cal.DEFAULT_MINUTES, approval=None, attachments=()):
    """Policy -> lint -> reviewer -> the Gmail draft. Raises Refused when a gate says no.

    `approval` is `--approval-issue`: a GitHub Issue number, a Tico send approval id, or
    the owner's Tico message telling this employee to send. A full yes (matching Issue, approved
    send payload, or owner instruction) allows external Cc and skips lint L056. The send half
    checks the same id again.

    Returns (payload, state). `state` carries the thread and the verdict so `reply` and
    `schedule` can go straight on to the send half without doing the work twice.
    """
    policy = ctx.policy
    msgs, in_reply_to, references, participants = [], "", "", None
    thread_id = str(reply_to or "").strip()
    if thread_id:
        msgs, in_reply_to, references, thread_subject, participants = thread_context(ctx,
                                                                                    thread_id)
        subject = subject or thread_subject
        if not to:
            to = [msgs[-1]["from"]] if msgs else []
    to = [str(a).strip().lower() for a in (to or []) if str(a).strip()]
    cc = [str(a).strip().lower() for a in (cc or []) if str(a).strip()]
    if not to:
        raise Refused("no recipient.", "Pass --to, or --reply-to a thread that has one.")
    outside = pl.externals(policy, cc)
    appr = (pl.approval_check(approval, pl.externals(policy, to), outside, thread_id,
                              slug=ctx.slug) if approval else None)
    approved = bool(appr and appr["full"])
    if outside and not approved:
        raise Refused(f"Cc outside {', '.join(policy['internal_domains'])} is not allowed "
                      f"({', '.join(outside)})."
                      + (f" {appr['detail']}." if appr else ""),
                      "Cc colleagues only. An external person on the message is a recipient, "
                      "and the policy allows one of those. The exception is --approval-issue: "
                      "a GitHub Issue that names every address, a Tico send approval, or "
                      "The owner's Tico message telling this employee to send.")
    pl.check_draft(policy, ctx.slug, ctx.mailbox, to, cc, attachments=len(attachments or ()),
                   is_reply=bool(thread_id))
    files = attachment_meta(attachments)

    allowance = pl.allowance_for(policy, ctx.slug, ctx.mailbox)
    busy = busy_window(ctx) if check_calendar else None
    res = ln.run(body, subject=subject, to=to, cc=cc, slug=ctx.slug, reply_to=thread_id,
                 allowance=allowance, thread_participants=participants,
                 internal_domains=policy["internal_domains"],
                 forbidden_phrases=policy["forbidden_phrases"],
                 signature_names=pl.signature_names(policy, ctx.mailbox), busy=busy,
                 check_calendar=check_calendar, slot=slot, minutes=minutes,
                 approved_externals=approved, forwarding=pl.is_forward(policy, ctx.slug, to))
    target = thread_id or ",".join(to)
    ctx.audit("lint", target, {"ok": res.ok, "errors": [x["id"] for x in res["errors"]],
                               "warnings": [x["id"] for x in res["warnings"]]})
    if not res.ok:
        raise Refused("the lint refused this draft: " + lint_line(res), res["errors"][0]["fix"])

    # The judge's gate, when this process has one: advisory, recorded, printed; the reviewer decides.
    judge = jd.engine()
    gate = (jd.draft_gate(judge, body, subject, thread_text(msgs), ctx.mailbox, ctx.slug)
            if judge is not None else None)
    if gate is not None:
        ctx.audit("gate", target, {"set": gate["set"], "available": gate["available"],
                                   "ok": gate["ok"], "flags": gate["flags"],
                                   "probabilities": gate["probabilities"], "error": gate.get("error")})
    verdict = run_review(ctx, body, subject, thread_text(msgs), files)
    review_id = db.record_review(ctx.conn, ctx.slug, ctx.mailbox, target, verdict)
    ctx.audit("review", target, {"backend": verdict.get("backend"), "ok": verdict.get("ok"),
                                 "available": verdict.get("available"),
                                 "attachments": [a["name"] for a in files],
                                 "commitments": verdict.get("commitments"),
                                 "claims_not_in_thread": verdict.get("claims_not_in_thread"),
                                 "problems": verdict.get("problems")})
    # A reviewer "no" does not drop the work: the draft is still written, but it is the owner's to
    # send, so it carries hub/needs-owner and the JSON says why (docs/mail-service.md, guardrails).
    review_no = bool(verdict.get("available") and not verdict.get("ok"))
    review_why = "; ".join((verdict.get("commitments") or []) + (verdict.get("claims_not_in_thread")
                                                                or []) + (verdict.get("problems")
                                                                          or []) or ["no reason"])

    key = compose.draft_key(ctx.slug, ctx.issue, to, subject, body)
    row = db.get_draft(ctx.conn, key)
    raw = compose.build(to, subject, body, from_addr=ctx.mailbox, cc=cc,
                        in_reply_to=in_reply_to, references=references, attachments=attachments)
    draft_id = (row or {}).get("draft_id", "")
    message_id, created = "", not row
    if not ctx.dry:
        d = (ctx.gmail.update_draft(draft_id, raw, thread_id) if draft_id
             else ctx.gmail.create_draft(raw, thread_id))
        draft_id = d.get("id") or draft_id
        message_id = (d.get("message") or {}).get("id") or ""
        thread_id = (d.get("message") or {}).get("threadId") or thread_id
        db.put_draft(ctx.conn, key, ctx.slug, ctx.mailbox, ctx.issue, to, subject,
                     thread_id, draft_id, message_id, review_id)
        label_thread(ctx, lb.DRAFTED, msgs, [message_id])
        if review_no:
            label_thread(ctx, lb.NEEDS_OWNER, msgs, [message_id])
    ctx.audit("draft", draft_id or key[:12],
              {"to": to, "cc": cc, "subject": subject, "thread": thread_id, "key": key,
               "created": created, "reply": bool(reply_to), "chars": len(body),
               "attachments": files, "review": verdict.get("backend"),
               "review_ok": None if not verdict.get("available") else verdict.get("ok")})
    payload = {"ok": True, "employee": ctx.slug, "mailbox": ctx.mailbox, "issue": ctx.issue,
               "draft": draft_id, "key": key, "thread": thread_id, "message": message_id,
               "to": to, "cc": cc, "subject": subject, "reply_to": reply_to or "",
               "attachments": files, "created": created, "updated": not created, "dry_run": ctx.dry,
               "label": lb.DRAFTED if not review_no else f"{lb.DRAFTED}+{lb.NEEDS_OWNER}",
               "needs_owner": review_no,
               "why_needs_owner": review_why if review_no else "",
               "lint": {"ok": res.ok, "findings": res["findings"], "summary": res["summary"]},
               "review": ("unavailable" if not verdict.get("available")
                          else ("ok" if verdict.get("ok") else "not ok")),
               "review_detail": verdict, "gate": gate}
    state = {"msgs": msgs, "verdict": verdict, "to": to, "cc": cc, "subject": subject,
             "body": body, "thread_id": thread_id, "draft_id": draft_id, "lint": res,
             "attachments": files}
    return payload, state


def draft_text(p):
    lines = [("would draft" if p["dry_run"] else
              ("updated draft" if p["updated"] else "created draft"))
             + f" in {p['mailbox']} as {p['employee']}",
             f"  to:      {', '.join(p['to'])}" + (f"   cc: {', '.join(p['cc'])}"
                                                   if p["cc"] else ""),
             f"  subject: {p['subject']}",
             f"  thread:  {p['thread'] or '(new)'}",
             *(["  attach:  " + ", ".join(f"{a['name']} ({a['size']} bytes)"
                                          for a in p["attachments"])] if p.get("attachments") else []),
             f"  draft:   {p['draft'] or '(not created: dry run)'}",
             f"  key:     {p['key'][:16]}...",
             f"  lint:    {p['lint']['summary']}",
             f"  review:  {p['review']} ({p['review_detail'].get('backend', '?')})"]
    if p.get("gate"):
        lines.append(jd.render_gate(p["gate"]))
    for x in p["lint"]["findings"]:
        lines.append(f"  {x['severity']} {x['id']}: {x['message']}")
    if p["review"] == "unavailable":
        lines.append("  the reviewer was unreachable: the draft stands, a send would be "
                     "downgraded to a draft.")
    if not p["dry_run"]:
        lines.append(f"  labelled {p['label']}; the owner can see it in Gmail.")
    return "\n".join(lines) + "\n"


def cmd_draft(args):
    ctx = Ctx(args, "draft")
    payload, _ = make_draft(ctx, args.to, args.subject, body_of(args.body_file),
                            reply_to=args.reply_to, cc=args.cc or [],
                            approval=args.approval_issue,
                            attachments=attachments_of(args.attach))
    return out(args, payload, draft_text(payload))


# -- the send half -------------------------------------------------

def fresh_verdict(conn, row, now=None):
    """A stored verdict, if there is one and it is less than 24 hours old."""
    if not row or not row.get("review_id"):
        return None
    rec = db.get_review(conn, row["review_id"])
    if not rec:
        return None
    now = now or datetime.now(zone("UTC"))
    try:
        age = now - parse_since(rec["ts"], now=now, tz_name="UTC")
    except Failure:                                     # pragma: no cover
        return None
    if age > timedelta(hours=REVIEW_FRESH_HOURS):
        return None
    return rec["verdict"]


def send_result(ctx, payload):
    """`send` always answers in JSON: a bot has to be able to read the reason."""
    print(json.dumps(payload, indent=2, default=str))
    return 0


def attempt_send(ctx, draft_id, to, cc, subject, thread_id, msgs, verdict, approval=None,
                 extra=None, attachments=()):
    """The full send chain. Returns a payload; a policy problem is a downgrade, not an error.
    Attachments ride the same chain: they are counted for the policy's caps and named in the
    audit, and lift nothing."""
    names = [a["name"] for a in attachments or ()]
    base = {"ok": True, "employee": ctx.slug, "mailbox": ctx.mailbox, "issue": ctx.issue,
            "draft": draft_id, "to": to, "cc": cc, "subject": subject, "thread": thread_id,
            "attachments": names, "dry_run": ctx.dry}
    base.update(extra or {})

    def downgrade(reason, gate="", checks=None):
        ctx.audit("send-downgraded", draft_id or thread_id,
                  {"reason": reason, "gate": gate, "to": to, "attachments": names})
        return dict(base, sent=False, downgraded="draft", reason=reason, gate=gate,
                    checks=checks or [],
                    note=f"the draft is still in {ctx.mailbox} Drafts; the owner can send it, or "
                         "close the gate that refused it.")

    # A per-message approval (the Issue names every address on this message) is the owner's own
    # yes to these exact words and recipients. The second reviewer still runs and its verdict
    # is recorded, but it advises rather than blocks: it does not get to overrule the owner.
    appr = (pl.approval_check(approval, pl.externals(ctx.policy, to),
                              pl.externals(ctx.policy, cc), thread_id,
                              slug=ctx.slug) if approval else None)
    full = bool(appr and appr["full"])
    review_note = ""
    if not verdict.get("available"):
        why = ("the second reviewer is unavailable: " + str(verdict.get("error", "unknown")))
        if not full:
            return downgrade(why + " (a draft may stand without it, a send may not)", "review")
        review_note = why + "; sent anyway under " + appr["detail"]
    elif not verdict.get("ok"):
        why = ("the second reviewer refused the draft: "
               + "; ".join((verdict.get("commitments") or [])
                           + (verdict.get("claims_not_in_thread") or [])
                           + (verdict.get("problems") or []) or ["no reason given"]))
        if not full:
            return downgrade(why, "review")
        review_note = why + "; sent anyway under " + appr["detail"]
    if review_note:
        ctx.audit("review-advisory", draft_id or thread_id,
                  {"reason": review_note, "approval": appr["issue"], "to": to, "cc": cc})
        base["review_advisory"] = review_note

    decision = pl.check_send(ctx.policy, ctx.slug, ctx.mailbox, to, cc,
                             attachments=len(names), thread_id=thread_id,
                             approval=approval, verbs=None, conn=ctx.conn,
                             thread_senders=pl.inbound_senders(msgs, ctx.mailbox))
    if not decision.allowed:
        return downgrade(decision["reason"], decision["gate"], decision["checks"])
    if ctx.dry:
        return dict(base, sent=False, would_send=True, checks=decision["checks"],
                    reason="", note="dry run: every gate passed, nothing was sent.")

    key = compose.send_key(ctx.slug, ctx.issue, draft_id)
    day = datetime.now(zone(DEFAULT_TZ)).strftime("%Y-%m-%d")
    row, claimed = db.claim_send(ctx.conn, key, ctx.slug, ctx.mailbox, ctx.issue, to, subject,
                                 thread_id, draft_id, day)
    if not claimed:
        ctx.audit("send-duplicate", draft_id, {"key": key, "message": row.get("message_id")})
        return dict(base, sent=True, already_sent=True, message=row.get("message_id", ""),
                    key=key, note="this exact send was already made; nothing was sent again.")
    try:
        res = ctx.gmail.send_draft(draft_id)
    except Failure:
        db.drop_send(ctx.conn, key)                     # never sent: let a retry try again
        raise
    message_id = res.get("id", "")
    thread_id = res.get("threadId") or thread_id
    db.finish_send(ctx.conn, key, message_id, "sent")
    handled = lb.handled(ctx.slug)
    label_thread(ctx, handled, msgs, [message_id])
    ctx.audit("send", message_id or draft_id,
              {"to": to, "cc": cc, "subject": subject, "thread": thread_id, "key": key,
               "draft": draft_id, "attachments": names, "review": verdict.get("backend")})
    return dict(base, sent=True, message=message_id, thread=thread_id, key=key,
                label=handled, checks=decision["checks"])


def cmd_send(args):
    ctx = Ctx(args, "draft")
    policy = ctx.policy
    draft = ctx.gmail.get_draft(args.draft)
    msg = ctx.normalize(draft.get("message") or {})
    to, cc, subject, body = msg["to"], msg["cc"], msg["subject"], msg["body"]
    thread_id = msg["thread_id"]
    files = msg.get("attachments") or []
    if not to:
        raise Refused(f"draft {args.draft} has no recipient.",
                      "Draft it again with --to; a draft with no To: cannot be sent.")
    pl.check_draft(policy, ctx.slug, ctx.mailbox, to, cc, attachments=len(files),
                   is_reply=bool(thread_id))
    msgs = []
    if thread_id:
        try:
            msgs = [m for m in (ctx.normalize(x) for x in ctx.gmail.thread(thread_id))
                    if m["id"] != msg["id"]]
        except Failure:
            msgs = []
    participants = compose.thread_headers(msgs)[3] + to if msgs else None

    allowance = pl.allowance_for(policy, ctx.slug, ctx.mailbox)
    appr = (pl.approval_check(args.approval_issue, pl.externals(policy, to),
                              pl.externals(policy, cc), thread_id, slug=ctx.slug)
            if args.approval_issue else None)
    res = ln.run(body, subject=subject, to=to, cc=cc, slug=ctx.slug,
                 reply_to=thread_id if msgs else "", allowance=allowance,
                 thread_participants=participants,
                 internal_domains=policy["internal_domains"],
                 forbidden_phrases=policy["forbidden_phrases"],
                 signature_names=pl.signature_names(policy, ctx.mailbox), slot=args.slot,
                 approved_externals=bool(appr and appr["full"]),
                 forwarding=pl.is_forward(policy, ctx.slug, to))
    ctx.audit("lint", args.draft, {"ok": res.ok, "errors": [x["id"] for x in res["errors"]],
                                   "warnings": [x["id"] for x in res["warnings"]]})
    if not res.ok:
        ctx.audit("send-downgraded", args.draft, {"reason": "lint", "gate": "lint"})
        return send_result(ctx, {
            "ok": True, "sent": False, "downgraded": "draft", "gate": "lint",
            "reason": "the lint refused the draft: " + lint_line(res),
            "employee": ctx.slug, "mailbox": ctx.mailbox, "draft": args.draft, "to": to,
            "subject": subject, "thread": thread_id, "dry_run": ctx.dry,
            "lint": {"ok": False, "findings": res["findings"]}})

    row = db.draft_by_gmail_id(ctx.conn, ctx.mailbox, args.draft)
    # A draft with files is reviewed again as it stands: the files may have changed in Gmail
    # since the stored verdict was given.
    verdict = None if files else fresh_verdict(ctx.conn, row)
    reused = verdict is not None
    if verdict is None:
        verdict = run_review(ctx, body, subject, thread_text(msgs), files)
        db.record_review(ctx.conn, ctx.slug, ctx.mailbox, args.draft, verdict)
        ctx.audit("review", args.draft,
                  {"backend": verdict.get("backend"), "ok": verdict.get("ok"),
                   "available": verdict.get("available"),
                   "attachments": [a["name"] for a in files]})
    payload = attempt_send(ctx, args.draft, to, cc, subject, thread_id, msgs, verdict,
                           approval=args.approval_issue, attachments=files,
                           extra={"lint": {"ok": True, "findings": res["findings"]},
                                  "review": ("ok" if verdict.get("ok") else
                                             ("unavailable" if not verdict.get("available")
                                              else "not ok")),
                                  "review_reused": reused,
                                  "review_backend": verdict.get("backend", "")})
    return send_result(ctx, payload)


def cmd_reply(args):
    ctx = Ctx(args, "draft")
    payload, state = make_draft(ctx, args.to, None, body_of(args.body_file),
                                reply_to=args.thread, cc=args.cc or [],
                                approval=args.approval_issue)
    sent = attempt_send(ctx, state["draft_id"], state["to"], state["cc"], state["subject"],
                        state["thread_id"], state["msgs"], state["verdict"],
                        approval=args.approval_issue, attachments=state["attachments"])
    payload = dict(payload, sent=sent.get("sent", False))
    for k in ("downgraded", "reason", "gate", "message", "key", "already_sent", "would_send",
              "note", "checks"):
        if k in sent:
            payload[k] = sent[k]
    return send_result(ctx, payload)


def cmd_discard(args):
    """Delete an unsent draft, and only one this employee made: the drafts table is the proof.
    It takes a draft id, never a message id, so received and sent mail cannot reach it."""
    ctx = Ctx(args, "draft")
    did = str(args.draft_id or "").strip()
    row = db.draft_by_gmail_id(ctx.conn, ctx.mailbox, did) if did else None
    if not row or row["employee"] != ctx.slug:
        ctx.audit("discard-refused", did, {"reason": "not this employee's draft",
                                           "made_by": (row or {}).get("employee", "")})
        raise Refused(f"draft {did} in {ctx.mailbox} was not made by {ctx.slug}; "
                      "only a draft this employee wrote with `mail draft` can be discarded.",
                      "Leave it for the person who wrote it, or ask the owner to delete it in Gmail.")
    if db.draft_was_sent(ctx.conn, ctx.mailbox, did):
        ctx.audit("discard-refused", did, {"reason": "already sent"})
        raise Refused(f"draft {did} was already sent; sent mail is never deleted.")
    draft = ctx.gmail.get_draft(did)                    # notFound: gone already, a Failure
    labels = set((draft.get("message") or {}).get("labelIds") or [])
    if "SENT" in labels:                                # pragma: no cover - Gmail drafts carry DRAFT
        ctx.audit("discard-refused", did, {"reason": "already sent"})
        raise Refused(f"draft {did} was already sent; sent mail is never deleted.")
    thread_id = row["thread_id"]
    if not ctx.dry:
        ctx.gmail.delete_draft(did)
        db.drop_draft(ctx.conn, ctx.mailbox, did)
        if thread_id and thread_id not in db.draft_thread_ids(ctx.conn, ctx.mailbox):
            try:                                        # no hub draft left on the thread
                ids = [m["id"] for m in ctx.gmail.thread(thread_id) or []]
                modify(ctx, ids, remove=[lb.DRAFTED], action="label-remove",
                       detail={"label": lb.DRAFTED})
            except (Failure, Refused):
                pass
    ctx.audit("discard", did, {"key": row["key"], "to": row["recipients"].split(","),
                               "subject": row["subject"], "thread": thread_id})
    return out(args, {"ok": True, "employee": ctx.slug, "mailbox": ctx.mailbox, "draft": did,
                      "discarded": not ctx.dry, "thread": thread_id, "subject": row["subject"],
                      "dry_run": ctx.dry},
               f"{'would discard' if ctx.dry else 'discarded'} draft {did} in {ctx.mailbox} "
               f"({row['subject']})")


# -- the record ----------------------------------------------------

def reconcile_sent(ctx, since, limit=200):
    """The owner's own sends, so a bot's cooldown counts them (docs/mail-service.md, Execution)."""
    floor = parse_since(since or "7d")
    ids = ctx.gmail.list_ids(f"in:sent after:{int(floor.timestamp())}", limit=limit)
    known = db.send_message_ids(ctx.conn, ctx.mailbox)
    added = []
    for m in fetch(ctx, ids):
        if m["id"] in known:
            continue
        day = (m["date"] or "")[:10]
        for addr in m["to"] or []:
            key = compose.key("gmail-sent", ctx.mailbox, m["id"], addr)
            row, claimed = db.claim_send(ctx.conn, key, "human", ctx.mailbox, "", [addr],
                                         m["subject"], m["thread_id"], "", day, "gmail-sent",
                                         when=stamp(parse_since(m["date"])) if m["date"] else None)
            if claimed:
                db.finish_send(ctx.conn, key, m["id"], "sent")
                added.append({"message": m["id"], "to": addr, "subject": m["subject"],
                              "date": m["date"]})
    if added:
        ctx.audit("reconcile", ctx.mailbox, {"added": len(added)})
    return added


def cmd_sent_log(args):
    ctx = Ctx(args, "read")
    added = reconcile_sent(ctx, args.since) if args.reconcile else []
    since = stamp(parse_since(args.since)) if args.since else None
    rows = db.send_rows(ctx.conn, employee=args.employee, mailbox=ctx.mailbox, since=since)
    body = [f"# Sent from {ctx.mailbox}"
            + (f" since {args.since}" if args.since else ""), ""]
    for r in rows:
        body.append(f"{r['ts']}  {r['employee']:<12} {r['recipient']:<32} "
                    f"{(r['subject'] or '')[:50]}  [{r['source']}/{r['status']}]")
    body.append("")
    body.append(f"{len(rows)} send(s)"
                + (f", {len(added)} newly reconciled from Gmail's Sent folder" if args.reconcile
                   else "")
                + ". Cooldowns count every row here, including the owner's own sends.")
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "count": len(rows), "sends": rows,
                      "reconciled": added}, "\n".join(body) + "\n")


# -- lint and review, standalone -----------------------------------

def cmd_lint(args):
    ctx = Ctx(args, "read", soft=True)
    policy = ctx.policy
    body = body_of(args.body_file)
    participants = None
    if args.reply_to and ctx.mailbox:
        participants = thread_context(ctx, args.reply_to)[4]
    if args.check_calendar and not ctx.mailbox:
        raise Refused("--check-calendar needs a mailbox to read the calendars of.",
                      "Pass --as <slug> for an employee that declares one, or --mailbox.")
    busy = busy_window(ctx) if args.check_calendar else None
    res = ln.run(body, subject=args.subject or "", to=args.to or [], slug=ctx.slug,
                 reply_to=args.reply_to or "",
                 allowance=pl.allowance_for(policy, ctx.slug, ctx.mailbox),
                 thread_participants=participants,
                 internal_domains=policy["internal_domains"],
                 forbidden_phrases=policy["forbidden_phrases"],
                 signature_names=pl.signature_names(policy, ctx.mailbox), busy=busy,
                 check_calendar=bool(args.check_calendar), slot=args.slot,
                 minutes=args.minutes)
    ctx.audit("lint", args.body_file, {"ok": res.ok, "errors": [x["id"] for x in res["errors"]],
                                       "warnings": [x["id"] for x in res["warnings"]]})
    out(args, dict(res, employee=ctx.slug, mailbox=ctx.mailbox, body_file=args.body_file),
        ln.render(res, f"Lint {args.body_file}"))
    return 0 if res.ok else 2


def cmd_review(args):
    ctx = Ctx(args, "read", soft=True)
    body = body_of(args.body_file)
    incoming = ""
    msgs = []
    if args.thread and ctx.mailbox:
        msgs = thread_context(ctx, args.thread)[0]
        incoming = thread_text(msgs)
    verdict = run_review(ctx, body, args.subject or "", incoming)
    db.record_review(ctx.conn, ctx.slug, ctx.mailbox or "", args.thread or args.body_file,
                     verdict)
    ctx.audit("review", args.thread or args.body_file,
              {"backend": verdict.get("backend"), "ok": verdict.get("ok"),
               "available": verdict.get("available")})
    out(args, dict(verdict, employee=ctx.slug, thread=args.thread or ""),
        rv.render(verdict, f"Review of {args.body_file}"))
    return 0 if verdict.get("ok") else (1 if not verdict.get("available") else 2)


def cmd_policy_show(args):
    slug = access.employee(args.as_slug)
    mailbox = args.mailbox
    if not mailbox:
        try:
            mailbox, _ = access.resolve(slug, None, "read")
        except Refused:
            mailbox = ""
    policy = pl.load(slug=slug)
    d = pl.describe(policy, slug, mailbox)
    manifest = access.load(slug)
    d["scheduling_send"] = (pl.scheduling_enabled(policy, slug, mailbox)
                            and manifest.get("scheduling_send") is True)
    d["scheduling_scope"] = "template-only influencer and BD-confirmed scheduling; excludes legal and investors"
    if args.json:
        print(json.dumps(d, indent=2, default=str))
        return 0
    lines = [f"policy for {slug}" + (f" as {mailbox}" if mailbox else ""), "",
             f"  global send switch      {'on' if d['send_enabled'] else 'OFF (everything is a draft)'}",
             f"  mailbox paused          {d['mailbox_paused']}",
             f"  outbound_send           {d['outbound_send']} (general email)",
             f"  forward_to              {', '.join(d['forward_to']) or '(none)'}",
             f"  policy                  {d['policy_source']}",
             f"  scheduling_send         {d['scheduling_send']} (mail scheduling only)",
             f"  internal domains        {', '.join(d['internal_domains'])}",
             f"  sends per day           {d['caps']['max_sends_per_day']}",
             f"  per-recipient cooldown  {d['caps']['per_recipient_cooldown_days']} days",
             f"  external recipients     {d['caps']['max_external_recipients']}",
             f"  cc outside acme.example     {d['caps']['allow_cc_external']}",
             f"  attachments             {d['caps']['allow_attachments']}"]
    if d["allowance"]:
        a = d["allowance"]
        lines += ["", "  standing allowance", f"    purpose   {a['purpose']}",
                  f"    from      {a['recipients'].get('path') or 'the list in the policy file'}",
                  f"    table     {a['table']}",
                  f"    caps      {json.dumps(a['caps'], sort_keys=True)}",
                  f"    urls      {a['urls']['required_pattern'] or '(no required pattern)'}"]
    else:
        lines += ["", "  no standing allowance. With outbound_send on, a send to an internal address, to a forward_to "
                      "target, or as a reply to the sender of the thread needs no approval; any other external "
                      "send needs --approval-issue (a matching GitHub Issue, a Tico send approval, or the "
                      "owner's message telling this employee to send)."]
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------- calendar

def cmd_scheduling(args):
    from . import scheduling
    ctx = Ctx(args, "schedule")
    payload = scheduling.run(ctx, args)
    return out(args, payload, json.dumps(payload, indent=2))


def cmd_slots(args):
    ctx = Ctx(args, "calendar_read", mailbox=args.for_mailbox)
    tz = zone(DEFAULT_TZ)
    now = datetime.now(tz)
    start_after = parse_since(args.start_after) if args.start_after else now
    if pl.scheduling_enabled(ctx.policy, ctx.slug, ctx.mailbox):
        from . import scheduling
        minutes = int(scheduling.DURATION.total_seconds() // 60)
        if args.minutes != minutes:
            raise Refused(f"This mailbox's meetings are {minutes} minutes; use --minutes {minutes}.")
        slots = scheduling.slots(ctx.calendar, n=args.n, days=args.days,
                                 start_after=start_after, now=now)
        busy = []  # availability was checked within scheduling.slots
    else:
        busy = ctx.calendar.busy(start_after, start_after + timedelta(days=args.days + 1))
        slots = cal.free_slots(busy, n=args.n, minutes=args.minutes, days=args.days,
                               start_after=start_after, now=now)
    ctx.audit("slots", ctx.mailbox, {"n": args.n, "minutes": args.minutes, "days": args.days,
                                     "found": len(slots), "busy_spans": len(busy)})
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "minutes": args.minutes,
                      "count": len(slots), "slots": cal.as_json(slots)},
               cal.render(slots, ctx.mailbox))


def cmd_upcoming(args):
    """Meetings for the signed-in person's desktop prompt; read-only and bounded."""
    ctx = Ctx(args, "calendar_read", mailbox=args.for_mailbox)
    now = datetime.now(zone("UTC"))
    include_private = bool(getattr(args, "include_private", False))
    events = ctx.calendar.upcoming(now - timedelta(minutes=2),
                                   now + timedelta(hours=max(1, min(args.hours, 168))),
                                   include_private=include_private)
    # This endpoint is background-polled. It exposes no message body or event description, and
    # deliberately does not add one audit row per poll.
    body = [f"# Upcoming meetings on {ctx.mailbox}", ""]
    body += [f"{event['start']}  {event['title']}" for event in events]
    if not events:
        body.append("No upcoming meetings.")
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "count": len(events),
                      "events": events}, "\n".join(body) + "\n")


def cmd_calendar_list(args):
    """List events on a company calendar in an interval, including private holds."""
    ctx = Ctx(args, "calendar_read", mailbox=args.for_mailbox)
    tz = zone(DEFAULT_TZ)
    now = datetime.now(tz)
    start = parse_since(args.start) if getattr(args, "start", None) else now
    if getattr(args, "end", None):
        end = parse_since(args.end)
    else:
        hours = getattr(args, "hours", 48) or 48
        end = start + timedelta(hours=hours)
    include_private = not getattr(args, "exclude_private", False)
    events = ctx.calendar.upcoming(start, end, include_private=include_private)
    ctx.audit("calendar-list", ctx.mailbox, {"start": start.isoformat(), "end": end.isoformat(), "count": len(events)})
    body = [f"# Events on {ctx.mailbox}", ""]
    body += [f"{event['start']}  {event['title']}" for event in events]
    if not events:
        body.append("No events.")
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "count": len(events), "events": events},
               "\n".join(body) + "\n")


def cmd_calendar_get(args):
    """Get one event on a company calendar by ID."""
    ctx = Ctx(args, "calendar_read", mailbox=args.for_mailbox)
    event_id = str(args.event or "").strip()
    if not event_id:
        raise Refused("Pass --event <id>.")
    event = ctx.calendar.get_event(event_id)
    if not event:
        raise Refused(f"Event {event_id} not found on {ctx.mailbox}.")
    ctx.audit("calendar-get", ctx.mailbox, {"event_id": event_id})
    body = [f"# {event['title']}", "", f"Start: {event['start']}", f"End: {event['end']}", f"ID: {event['event_id']}"]
    if event.get("meeting_url"):
        body.append(f"Meeting: {event['meeting_url']}")
    return out(args, {"ok": True, "mailbox": ctx.mailbox, "event": event}, "\n".join(body) + "\n")


def cmd_calendar_hold(args):
    """Add an audited, idempotent calendar hold; attendees are invited, and TICO_BLOCK_EXTERNAL_INVITES=1 limits them to the roster."""
    ctx = Ctx(args, "calendar_schedule", mailbox=args.for_mailbox)
    summary = str(args.summary or "").strip()
    if not summary or len(summary) > 240:
        raise Refused("--summary must be 1-240 characters.")

    start = parse_since(args.start)
    if getattr(args, "end", None):
        end = parse_since(args.end)
        minutes = int((end - start).total_seconds() / 60)
    else:
        minutes = args.minutes
        end = start + timedelta(minutes=minutes)

    if not 1 <= minutes <= 24 * 60:
        raise Refused("--minutes must be between 1 and 1440.")

    attendees = []
    for raw in (getattr(args, "attendee", None) or []):
        addr = str(raw or "").strip().lower()
        if addr and addr != ctx.mailbox and addr not in attendees:
            attendees.append(addr)
    if os.environ.get("TICO_BLOCK_EXTERNAL_INVITES") == "1":
        from . import access
        company = set(access.roster_mailboxes())
        outside = [addr for addr in attendees if addr not in company]
        if outside:
            raise Refused("calendar add only accepts company-roster attendees: " + ", ".join(outside),
                          "External invitations are turned off (TICO_BLOCK_EXTERNAL_INVITES). For partner/influencer scheduling, use the approved scheduling workflow.")
    if len(attendees) > 50:
        raise Refused("a calendar hold accepts at most 50 attendees.")
    attendees.sort()

    description = ""
    if getattr(args, "description_file", None):
        description = Path(args.description_file).read_text()
    elif getattr(args, "description", None):
        description = str(args.description)

    uid = getattr(args, "uid", None)
    if uid:
        event_id = "tico" + hashlib.sha256(str(uid).encode()).hexdigest()[:28]
    else:
        key_material = json.dumps([ctx.slug, ctx.mailbox, start.isoformat(), end.isoformat(),
                                   summary, attendees], separators=(",", ":"), ensure_ascii=False)
        event_id = "tico" + hashlib.sha256(key_material.encode()).hexdigest()[:28]

    # Fail-closed duplicate detection: query existing events in window WITH include_private=True
    existing_events = ctx.calendar.upcoming(start - timedelta(minutes=5), end + timedelta(minutes=5), include_private=True)

    for ev in existing_events:
        ev_id = str(ev.get("id") or ev.get("event_id") or "")
        ev_uid = str(ev.get("iCalUID") or "")
        ev_start = str(ev.get("start") or "")
        ev_title = str(ev.get("title") or ev.get("summary") or "").strip().lower()
        matches_id = bool(ev_id and ev_id == event_id)
        matches_uid = bool(uid and ev_uid and (ev_uid == str(uid).strip() or ev_uid.startswith(str(uid).strip())))
        matches_slot = (ev_title == summary.lower() and (ev_start == start.isoformat() or (ev_start and start.isoformat().startswith(ev_start[:16]))))
        if matches_id or matches_uid or matches_slot:
            url = ev.get("htmlLink") or f"https://calendar.google.com/calendar/event?eid={ev_id}"
            payload = {"ok": True, "mailbox": ctx.mailbox, "event": ev_id, "event_url": url,
                       "summary": summary, "start": start.isoformat(), "end": end.isoformat(),
                       "attendees": attendees, "duplicate": True, "dry_run": ctx.dry}
            ctx.audit("calendar-hold-duplicate", ev_id, {"summary": summary, "start": start.isoformat(), "end": end.isoformat()})
            return out(args, payload, f"existing event {summary} ({ev_id}) already on {ctx.mailbox}\n")

    payload = {"ok": True, "mailbox": ctx.mailbox, "event": event_id,
               "summary": summary, "start": start.isoformat(), "end": end.isoformat(),
               "attendees": attendees, "dry_run": ctx.dry}
    if ctx.dry:
        return out(args, payload, f"would hold {summary} on {ctx.mailbox} from {start.isoformat()} to {end.isoformat()}\n")

    event = ctx.calendar.create_event(
        start, end, summary, attendees=attendees,
        description=description or f"Private hold placed by {ctx.slug} through Tico.",
        send_updates="all" if attendees else "none", status="confirmed", event_id=event_id)

    real_id = event.get("id") or event_id
    url = event.get("htmlLink") or f"https://calendar.google.com/calendar/event?eid={real_id}"
    payload["event"] = real_id
    payload["event_url"] = url

    ctx.audit("event-create", real_id, {
        "summary": summary, "start": start.isoformat(), "end": end.isoformat(),
        "attendees": attendees, "hold": True
    })
    return out(args, payload, f"held {summary} on {ctx.mailbox}: {url}\n")

cmd_calendar_add = cmd_calendar_hold


def cmd_schedule(args):
    ctx = Ctx(args, "draft")
    policy = ctx.policy
    body = body_of(args.body_file)
    start = parse_since(args.slot)
    end = start + timedelta(minutes=args.minutes)
    msgs, _, _, subject, participants = thread_context(ctx, args.thread)
    attendee = (args.attendee or (msgs[-1]["from"] if msgs else "")).strip().lower()
    if not attendee:
        raise Refused("no attendee.", "Pass --attendee <address>.")
    decision = pl.check_send(policy, ctx.slug, ctx.mailbox, [attendee], thread_id=args.thread,
                             approval=args.approval_issue, conn=ctx.conn)
    would = {"start": start.isoformat(), "end": end.isoformat(), "attendee": attendee,
             "summary": args.summary or subject or "Intro call",
             "mailbox": ctx.mailbox, "minutes": args.minutes}

    if not decision.allowed or ctx.dry:
        payload, state = make_draft(ctx, [attendee], None, body, reply_to=args.thread,
                                    slot=args.slot, minutes=args.minutes)
        payload.update({"scheduled": False, "would_schedule": would, "sent": False,
                        "reason": decision["reason"] or "dry run",
                        "gate": decision["gate"], "checks": decision["checks"],
                        "note": "nothing was put on the calendar: the confirmation and the "
                                "invite go together or not at all "
                                "(policies/shared-rules.md)."})
        return send_result(ctx, payload)

    event = ctx.calendar.create_event(start, end, would["summary"], attendees=[attendee],
                                      description=f"Booked by the hub mail service for "
                                                  f"{ctx.slug}, Issue {ctx.issue or '-'}.",
                                      send_updates="all")
    ctx.audit("event-create", event.get("id", ""), dict(would, thread=args.thread))
    try:
        payload, state = make_draft(ctx, [attendee], None, body, reply_to=args.thread,
                                    slot=args.slot, minutes=args.minutes)
        sent = attempt_send(ctx, state["draft_id"], state["to"], state["cc"], state["subject"],
                            state["thread_id"], state["msgs"], state["verdict"],
                            approval=args.approval_issue)
        if not sent.get("sent"):
            raise Refused(sent.get("reason", "the confirmation was not sent"),
                          "The event was rolled back; nothing is on the calendar and nothing "
                          "was said. Fix the reason and run it again.")
    except (Failure, Refused):
        ctx.calendar.delete_event(event.get("id", ""))
        ctx.audit("event-rollback", event.get("id", ""),
                  {"why": "the confirmation did not send", "thread": args.thread})
        raise
    payload.update({"scheduled": True, "event": event.get("id", ""), "slot": would,
                    "sent": True, "message": sent.get("message", ""), "key": sent.get("key")})
    return send_result(ctx, payload)


def cmd_connector_event(args):
    """Execute one server-validated Hub calendar action from stdin.

    This is the provider-local half of `hub_calendar_schedule`; the runner claims the durable
    action first and invokes this command as the human owner, so employee mail permissions and
    generic email sending are not broadened.
    """
    try:
        request = json.load(sys.stdin)
    except (ValueError, TypeError) as exc:
        raise Failure("The connector calendar action is not valid JSON.") from exc
    if not isinstance(request, dict):
        raise Refused("The connector calendar action must be an object.")
    required = [key for key in ("id", "calendar", "title", "start", "end")
                if not str(request.get(key) or "").strip()]
    if required:
        raise Refused("The connector calendar action is missing " + ", ".join(required) + ".")
    mailbox = str(request["calendar"]).strip().lower()
    attendees = []
    for value in request.get("attendees") or []:
        address = str(value).strip().lower()
        if not address or "@" not in address or address in attendees:
            raise Refused("The connector calendar action has an invalid or duplicate attendee.")
        attendees.append(address)
    start, end = cal.iso(request["start"]), cal.iso(request["end"])
    if end <= start or (end - start) > timedelta(days=7):
        raise Refused("The connector calendar action has an invalid interval.")
    ctx = Ctx(args, "schedule", mailbox=mailbox)
    event_id = hashlib.sha256(str(request["id"]).encode()).hexdigest()[:32]
    event = ctx.calendar.create_hub_event(
        start, end, str(request["title"]).strip(), attendees, event_id,
        str(request.get("description") or ""), bool(request.get("add_meet", True)))
    meeting_url = cal._meeting_url(event)  # normalized URL only; descriptions never leave here
    provider_id = str(event.get("id") or event_id)
    ctx.audit("event-create", provider_id,
              {"action": request["id"], "attendees": len(attendees),
               "start": start.isoformat(), "end": end.isoformat()})
    return out(args, {"ok": True, "event_id": provider_id, "meeting_url": meeting_url},
               provider_id + "\n")


# ---------------------------------------------------------------- local persist (inbox bots phase 1)

def sync_targets(args):
    """Mailboxes this sync run will touch: one, or every address on the people roster."""
    slug = access.employee(getattr(args, "as_slug", None))
    if getattr(args, "mailbox", None) and getattr(args, "all_roster", False):
        raise Refused("--mailbox and --all-roster ask for different things; pick one.")
    if getattr(args, "all_roster", False):
        boxes = access.roster_mailboxes()
        if slug != access.OWNER:
            allowed = set(access.readable_mailboxes(slug, "read"))
            boxes = [b for b in boxes if b in allowed]
        if not boxes:
            raise Refused(f"{slug} has no roster mailbox to sync.",
                          "Pass --mailbox, or run as the owner with --all-roster.")
        return boxes
    box, _ = access.resolve(slug, getattr(args, "mailbox", None), "read")
    return [box]


def history_message_ids(records):
    """(ids to fetch, ids to tombstone) from users.history.list records."""
    fetch, deleted, seen_f, seen_d = [], [], set(), set()
    for rec in records or []:
        for item in rec.get("messagesDeleted") or []:
            mid = ((item.get("message") or item) or {}).get("id")
            if mid and mid not in seen_d:
                seen_d.add(mid)
                deleted.append(mid)
        for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
            for item in rec.get(key) or []:
                mid = ((item.get("message") or item) or {}).get("id")
                if mid and mid not in seen_f and mid not in seen_d:
                    seen_f.add(mid)
                    fetch.append(mid)
    return fetch, deleted


def fallback_floor(state, backfill):
    raw = (state or {}).get("last_run_at") or (state or {}).get("last_full_at") or ""
    if raw:
        try:
            return parse_since(raw) - HISTORY_FALLBACK
        except Failure:
            pass
    return parse_since(backfill)


def sync_one(ctx, args):
    """Sync one mailbox into mail.db. One audit row, action='sync'."""
    backfill = getattr(args, "backfill", None) or "90d"
    limit = getattr(args, "limit", None) or SYNC_LIMIT
    state = db.get_sync_state(ctx.conn, ctx.mailbox)
    mode, fetched, deleted, skipped = "backfill", 0, 0, 0
    current_hid = _history_id(ctx)
    try:
        ids, deleted_ids = [], []
        if state and state.get("history_id"):
            try:
                result = ctx.gmail.history(state["history_id"])
                ids, deleted_ids = history_message_ids(result.get("history") or [])
                current_hid = result.get("historyId") or current_hid
                mode = "history"
            except gm.HistoryExpired:
                mode = "fallback"
                floor = fallback_floor(state, backfill)
                ids = ctx.gmail.list_ids(f"after:{int(floor.timestamp())}", limit=limit)
        else:
            floor = parse_since(backfill)
            ids = ctx.gmail.list_ids(f"after:{int(floor.timestamp())}", limit=limit)
            mode = "backfill"
        if ctx.dry:
            fetched, deleted = len(ids), len(deleted_ids)
        else:
            for mid in ids:
                try:
                    raw = ctx.gmail.get(mid)
                except Failure:
                    skipped += 1
                    continue
                remember_message(ctx, ctx.normalize(raw))
                fetched += 1
            for mid in deleted_ids:
                db.mark_deleted(ctx.conn, ctx.mailbox, mid)
                deleted += 1
            db.set_sync_state(ctx.conn, ctx.mailbox, history_id=current_hid,
                              last_full_at=stamp() if mode == "backfill" else None,
                              last_run_at=stamp(), last_error="",
                              message_count=db.message_count(ctx.conn, ctx.mailbox))
        detail = {"mode": mode, "fetched": fetched, "deleted": deleted, "skipped": skipped,
                  "limit": limit, "backfill": backfill, "history_id": current_hid}
        ctx.audit("sync", ctx.mailbox, detail)
        return dict(ok=True, mailbox=ctx.mailbox, employee=ctx.slug, dry_run=ctx.dry, **detail)
    except Failure as e:
        if not ctx.dry:
            db.set_sync_state(ctx.conn, ctx.mailbox, last_error=e.msg, last_run_at=stamp())
        ctx.audit("sync", ctx.mailbox, {"mode": mode, "error": e.msg})
        raise


def cmd_sync_run(args):
    boxes = sync_targets(args)
    results, failed = [], 0
    for box in boxes:
        ctx = Ctx(args, "read", mailbox=box)
        try:
            results.append(sync_one(ctx, args))
        except Failure as e:
            failed += 1
            results.append({"ok": False, "mailbox": box, "error": e.msg, "hint": e.hint})
    payload = {"ok": failed == 0, "count": len(results), "mailboxes": results}
    if len(results) == 1:
        payload.update(results[0])
    lines = [f"{'ok' if r.get('ok') else 'FAIL'}  {r.get('mailbox', '?')}  "
             + (r.get("error") or
                f"{r.get('mode', '')} fetched={r.get('fetched', 0)} deleted={r.get('deleted', 0)}"
                + (" (dry run)" if r.get("dry_run") else ""))
             for r in results]
    rc = out(args, payload, "\n".join(lines) + "\n")
    return 1 if failed else rc


def cmd_sync_export(args):
    """Print unpushed rows as JSON. The CLI never holds HUB_TOKEN; a runner pushes later."""
    slug = access.employee(getattr(args, "as_slug", None))
    mailbox = getattr(args, "mailbox", None)
    if mailbox:
        access.resolve(slug, mailbox, "read")
        boxes = [mailbox]
    elif slug == access.OWNER:
        boxes = None
    else:
        boxes = access.readable_mailboxes(slug, "read")
    conn = open_db()
    try:
        rows = db.unpushed(conn, mailbox=mailbox if mailbox else None,
                           mailboxes=None if mailbox or boxes is None else boxes,
                           limit=getattr(args, "limit", None) or SYNC_LIMIT)
    finally:
        conn.close()
    payload = {"ok": True, "count": len(rows), "messages": rows}
    lines = [f"{r['mailbox']}  {r['id']}  {(r.get('date') or '-')[:16]}  "
             f"{(r.get('from_addr') or '-'):<28} {(r.get('subject') or '')[:50]}"
             + ("  [deleted]" if r.get("deleted_at") else "")
             for r in rows]
    if not lines:
        lines = ["no unpushed messages"]
    return out(args, payload, "\n".join(lines) + "\n")


def cmd_sync_ack(args):
    """Mark exported ids as pushed. Accepts --mailbox + ids, or --json on stdin."""
    slug = access.employee(getattr(args, "as_slug", None))
    items = []
    ids = list(getattr(args, "ids", None) or [])
    mailbox = getattr(args, "mailbox", None)
    if ids:
        if not mailbox:
            raise Refused("ack with message ids needs --mailbox.",
                          "Or pass --json with a list of {mailbox, id} objects on stdin.")
        access.resolve(slug, mailbox, "read")
        items = [(mailbox, mid) for mid in ids]
    else:
        raw = sys.stdin.read() if (getattr(args, "json", False) or not sys.stdin.isatty()) else ""
        payload = json.loads(raw) if raw.strip() else {}
        rows = payload.get("ids") or payload.get("messages") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            box = (row.get("mailbox") or mailbox or "").strip().lower()
            mid = row.get("id") or row.get("msg_id") or ""
            if box and mid:
                items.append((box, mid))
        if not items:
            raise Refused("ack needs message ids or a JSON batch on stdin.")
        seen = set()
        for box, _ in items:
            if box not in seen:
                access.resolve(slug, box, "read")
                seen.add(box)
    if getattr(args, "dry_run", False):
        return out(args, {"ok": True, "acked": 0, "would_ack": len(items), "dry_run": True},
                   f"would ack {len(items)} message(s)\n")
    conn = open_db()
    try:
        by_box = {}
        for box, mid in items:
            by_box.setdefault(box, []).append(mid)
        for box, mids in by_box.items():
            db.mark_pushed(conn, box, mids)
    finally:
        conn.close()
    return out(args, {"ok": True, "acked": len(items), "dry_run": False},
               f"acked {len(items)} message(s)\n")


def cmd_sync(args):
    cmd = getattr(args, "sync_cmd", None)
    if cmd == "export":
        return cmd_sync_export(args)
    if cmd == "ack":
        return cmd_sync_ack(args)
    return cmd_sync_run(args)


# ---------------------------------------------------------------- audit

def cmd_audit(args):
    conn = open_db()
    since = stamp(parse_since(args.since)) if args.since else ""
    rows = db.audit_rows(conn, since or None, args.employee)
    if args.json:
        print(json.dumps({"ok": True, "count": len(rows), "rows": rows}, indent=2))
        return 0
    if not rows:
        print(f"no audit lines" + (f" since {args.since}" if args.since else "")
              + f" (file: {AUDIT_PATH})")
        return 0
    for r in rows:
        extra = {k: v for k, v in (r["detail"] or {}).items() if k not in ("ids",)}
        print(f"{r['ts']}  {r['employee']:<14} {r['action']:<12} {r['mailbox']:<18} "
              f"{r['target'][:30]:<30} {json.dumps(extra, sort_keys=True)[:100]}")
    print(f"{len(rows)} line(s) - full record in {AUDIT_PATH}")
    return 0


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="mail", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def base(sp, mailbox=True, employee=True):
        if employee:
            sp.add_argument("--as", dest="as_slug", default=None,
                            help="employee slug; defaults to HUB_BOT from the runner")
        if mailbox:
            sp.add_argument("--mailbox", default=None,
                            help="the address to act as; required when the employee holds several")
        sp.add_argument("--issue", default=None, help="hub Issue number, recorded in the audit log")
        sp.add_argument("--json", action="store_true", help="machine-readable output and errors")
        return sp

    def writes(sp):
        sp.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
        return sp

    d = base(sub.add_parser("doctor", help="is mail set up, and for which mailboxes"))
    d.add_argument("--all-mailboxes", action="store_true",
                   help="every mailbox any employee declares (the default)")
    d.add_argument("--e2e", action="store_true",
                   help="really draft, label, send, search, and make an event, in the sandbox "
                        "mailbox only")
    d.set_defaults(func=cmd_doctor)

    w = base(sub.add_parser("whoami", help="which employee, which mailboxes, which verbs"),
             mailbox=False)
    w.set_defaults(func=cmd_whoami)

    u = writes(base(sub.add_parser("unsubscribe", help="submit a signed one-click unsubscribe request")))
    u.add_argument("message_id")
    u.add_argument("--archive", action="store_true", help="archive after the sender accepts the request")
    u.set_defaults(func=cmd_unsubscribe)

    i = base(sub.add_parser("inbox", help="INBOX messages this employee has not triaged"))
    i.add_argument("--new", action="store_true", help="only what this employee has not seen")
    i.add_argument("--since", default=None, help="24h, 7d, today, yesterday, or an ISO time")
    i.add_argument("--untriaged", action="store_true",
                   help="in:inbox minus every message with a hub/* label; label-based and "
                        "idempotent (no seen row, no watermark). Not with --new or --since")
    i.add_argument("--label", default=None, help="restrict to one Gmail label")
    i.add_argument("--limit", type=int, default=None,
                   help="default %d, or %d with --untriaged" % (INBOX_LIMIT, UNTRIAGED_LIMIT))
    i.add_argument("--format", choices=["md", "brief", "json"], default="md",
                   help="brief is the listing without bodies; open threads with `thread`")
    i.add_argument("--all-mailboxes", action="store_true",
                   help="every mailbox this employee may read (own plus reports)")
    i.add_argument("--decisions", "--judge", dest="judge", action="store_true",
                   help="ask the decision model about every listed message (questions/mail-triage.json): a decision: "
                        "line per message with a suggestion, before any thread is opened; "
                        "read-only, inside a hub turn or with TYPESAFE_API_KEY")
    i.set_defaults(func=cmd_inbox, _parser=i)

    t = base(sub.add_parser("thread", help="a whole thread, oldest first"))
    t.add_argument("thread_id")
    t.add_argument("--format", choices=["md", "brief", "json"], default="md")
    t.set_defaults(func=cmd_thread)

    s = base(sub.add_parser("search", help="a Gmail query, bounded to %d results" % MAX_SEARCH))
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=MAX_SEARCH)
    s.add_argument("--format", choices=["md", "brief", "json"], default="md")
    s.set_defaults(func=cmd_search)

    ats = base(sub.add_parser("attachments", help="list stable attachment refs on one message"))
    ats.add_argument("message_id", metavar="MESSAGE_ID")
    ats.add_argument("--format", choices=["md", "json"], default="md")
    ats.set_defaults(func=cmd_attachments)

    at = base(sub.add_parser("attachment", help="download one attachment to a new private file"))
    at.add_argument("message_id", metavar="MESSAGE_ID")
    at.add_argument("attachment_ref", metavar="ATTACHMENT_REF")
    at.add_argument("--out", required=True, metavar="PATH",
                    help="explicit destination file; must not already exist")
    at.add_argument("--format", choices=["md", "json"], default="md")
    at.set_defaults(func=cmd_attachment)

    l = writes(base(sub.add_parser("label", help="add or remove a label")))
    l.add_argument("op", choices=["add", "remove"])
    l.add_argument("rest", nargs="+", metavar="MSG_ID... LABEL")
    l.set_defaults(func=cmd_label)

    a = writes(base(sub.add_parser("archive", help="remove from INBOX")))
    a.add_argument("ids", nargs="+", metavar="MSG_ID")
    a.set_defaults(func=cmd_archive)

    m = writes(base(sub.add_parser("mark-read", help="remove UNREAD")))
    m.add_argument("ids", nargs="+", metavar="MSG_ID")
    m.set_defaults(func=cmd_mark_read)

    st = writes(base(sub.add_parser("star", help="star one message")))
    st.add_argument("msg_id")
    st.set_defaults(func=cmd_star)

    tr = writes(base(sub.add_parser("triaged", help="mark settled: adds hub/triaged/<slug>")))
    tr.add_argument("ids", nargs="+", metavar="MSG_ID")
    tr.set_defaults(func=cmd_triaged)

    r = sub.add_parser("rules", help="the deterministic inbox rules")
    rsub = r.add_subparsers(dest="rules_cmd", required=True)
    rr = writes(base(rsub.add_parser("run", help="apply registry/mail-rules.yaml to new mail")))
    rr.add_argument("--since", default="24h")
    rr.add_argument("--limit", type=int, default=200)
    rr.add_argument("--all-mailboxes", action="store_true",
                    help="run each mailbox's own rules (own plus reports)")
    rr.set_defaults(func=cmd_rules_run)
    re_ = base(rsub.add_parser("explain", help="which rules fire on one message, and why"))
    re_.add_argument("msg_id")
    re_.set_defaults(func=cmd_rules_explain)
    rt = rsub.add_parser("test", help="run the rules against fixture messages")
    rt.add_argument("fixture_dir")
    rt.add_argument("--rules", default=None, help="a rules file other than the registry one")
    rt.add_argument("--json", action="store_true")
    rt.set_defaults(func=cmd_rules_test)
    rb = base(rsub.add_parser("backtest", help="replay the rules over a window of all mail "
                                               "and report; a read, nothing changes"))
    rb.add_argument("--since", default="14d", help="the window: 14d, 30d, or an ISO time")
    rb.add_argument("--rules", default=None, metavar="FILE",
                    help="a candidate rules file; its outcomes are diffed against the registry")
    rb.add_argument("--baseline", default=None, metavar="FILE",
                    help="diff against this rules file instead of the registry (e.g. the previous "
                         "version, to measure a change already on disk)")
    rb.add_argument("--limit", type=int, default=2000)
    rb.add_argument("--format", choices=["md", "json"], default="md")
    rb.set_defaults(func=cmd_rules_backtest)

    # -- writing ------------------------------------------------------------
    df = writes(base(sub.add_parser("draft", help="write a draft (policy, lint, reviewer)")))
    df.add_argument("--to", action="append", default=None, metavar="ADDR",
                    help="recipient; repeat for several. Omit when --reply-to says who.")
    df.add_argument("--subject", default=None, help="required unless this is a reply")
    df.add_argument("--body-file", required=True, help="a text file holding the message")
    df.add_argument("--reply-to", default=None, metavar="THREAD",
                    help="thread id: keeps In-Reply-To, References and the subject")
    df.add_argument("--cc", action="append", default=None, metavar="ADDR",
                    help="internal addresses only, unless --approval-issue names every address")
    df.add_argument("--approval-issue", default=None, metavar="N",
                    help="GitHub Issue, Tico send approval id, or the owner's Tico message id; "
                         "a full yes allows external Cc")
    df.add_argument("--attach", action="append", default=None, metavar="PATH",
                    help="a file to attach; repeat for several (25 MB in all). Sending still "
                         "goes through every send gate")
    df.set_defaults(func=cmd_draft)

    dc = writes(base(sub.add_parser("discard", help="delete an unsent draft this employee made")))
    dc.add_argument("draft_id", metavar="DRAFT_ID", help="the Gmail draft id from `mail draft`")
    dc.set_defaults(func=cmd_discard)

    sd = writes(base(sub.add_parser("send", help="send a draft (the full policy chain)")))
    sd.add_argument("--draft", required=True, help="the Gmail draft id from `mail draft`")
    sd.add_argument("--approval-issue", default=None, metavar="N",
                    help="GitHub Issue, Tico send approval id, or the owner's Tico message id; "
                         "a full yes lifts recipient caps and outbound_send: false")
    sd.add_argument("--slot", default=None,
                    help="the ISO time this message confirms, when it confirms one")
    sd.set_defaults(func=cmd_send)

    rp = writes(base(sub.add_parser("reply", help="draft a reply, then send it if policy allows")))
    rp.add_argument("--thread", required=True)
    rp.add_argument("--body-file", required=True)
    rp.add_argument("--to", action="append", default=None, metavar="ADDR",
                    help="defaults to the last sender on the thread")
    rp.add_argument("--cc", action="append", default=None, metavar="ADDR")
    rp.add_argument("--approval-issue", default=None, metavar="N")
    rp.set_defaults(func=cmd_reply)

    sl = base(sub.add_parser("sent-log", help="what has been sent from this mailbox"))
    sl.add_argument("--since", default=None, help="24h, 7d, today, or an ISO time")
    sl.add_argument("--employee", default=None, help="filter the log by employee slug")
    sl.add_argument("--reconcile", action="store_true",
                    help="also read Gmail's Sent folder, so the owner's own sends count for the "
                         "cooldowns")
    sl.set_defaults(func=cmd_sent_log)

    li = base(sub.add_parser("lint", help="run the deterministic rules over a body file"))
    li.add_argument("--body-file", required=True)
    li.add_argument("--subject", default=None)
    li.add_argument("--to", action="append", default=None, metavar="ADDR")
    li.add_argument("--reply-to", default=None, metavar="THREAD")
    li.add_argument("--slot", default=None, help="the ISO time this body confirms, if any")
    li.add_argument("--minutes", type=int, default=cal.DEFAULT_MINUTES)
    li.add_argument("--check-calendar", action="store_true",
                    help="also check every time offered against the mailbox's calendars")
    li.set_defaults(func=cmd_lint)

    rv_ = base(sub.add_parser("review", help="ask the second model about a body file"))
    rv_.add_argument("--body-file", required=True)
    rv_.add_argument("--thread", default=None, help="the incoming thread it answers")
    rv_.add_argument("--subject", default=None)
    rv_.set_defaults(func=cmd_review)

    po = sub.add_parser("policy", help="what the mail policy allows this employee")
    posub = po.add_subparsers(dest="policy_cmd", required=True)
    ps = base(posub.add_parser("show", help="the gates, the caps and the allowance"),
              mailbox=True)
    ps.set_defaults(func=cmd_policy_show)

    # -- calendar -----------------------------------------------------------
    ss = writes(base(sub.add_parser("scheduling", help="authorized template-only scheduling offers or invitations")))
    ss.add_argument("mode", choices=["offer", "book"])
    ss.add_argument("--thread", required=True)
    ss.add_argument("--category", required=True, choices=["influencer", "business-development"])
    ss.add_argument("--bd-confirmation", help="Hub message ID authored by business-development")
    ss.add_argument("--slot", help="full ISO timestamp with offset; book only")
    ss.add_argument("--acceptance-message", help="latest inbound Gmail message ID accepting the slot")
    ss.set_defaults(func=cmd_scheduling)

    so = base(sub.add_parser("slots", help="real free slots, buffer applied"))
    so.add_argument("--for", dest="for_mailbox", default=None, metavar="ADDR",
                    help="the mailbox whose calendars to read")
    so.add_argument("--n", type=int, default=2)
    so.add_argument("--minutes", type=int, default=30)
    so.add_argument("--days", type=int, default=cal.DEFAULT_DAYS)
    so.add_argument("--start-after", default=None, help="ISO time; defaults to now")
    so.set_defaults(func=cmd_slots)

    up = base(sub.add_parser("upcoming", help="recordable meetings in the next few hours"))
    up.add_argument("--for", dest="for_mailbox", required=True, metavar="ADDR",
                    help="the roster mailbox whose calendars to read")
    up.add_argument("--hours", type=int, default=24,
                    help="look-ahead window, clamped to 1-168 hours")
    up.add_argument("--include-private", action="store_true", help="include private holds and solo calendar events")
    up.set_defaults(func=cmd_upcoming)

    ca = sub.add_parser("calendar", help="read or add events on company calendars")
    casub = ca.add_subparsers(dest="calendar_cmd", required=True)
    for p_name in ("add", "hold"):
        cad = writes(base(casub.add_parser(p_name, help="add an event or private hold to a roster person's calendar"),
                          mailbox=False))
        cad.add_argument("--for", dest="for_mailbox", required=True, metavar="ADDR",
                         help="the roster person's calendar")
        cad.add_argument("--start", required=True, help="ISO start time, preferably with an offset")
        cad.add_argument("--minutes", type=int, default=30)
        cad.add_argument("--end", default=None, help="optional ISO end time (overrides --minutes)")
        cad.add_argument("--summary", required=True, help="event title, 1-240 characters")
        cad.add_argument("--description", default="", help="optional event description")
        cad.add_argument("--description-file", default=None, help="path to description file")
        cad.add_argument("--attendee", action="append", default=None, metavar="ADDR",
                         help="attendee to invite; repeat for several")
        cad.add_argument("--uid", default=None, help="optional UID from ICS file")
        cad.set_defaults(func=cmd_calendar_hold)

    cl = base(casub.add_parser("list", help="list events on a company calendar, including private holds"), mailbox=False)
    cl.add_argument("--for", dest="for_mailbox", required=True, metavar="ADDR", help="the roster person's calendar")
    cl.add_argument("--start", default=None, help="ISO start time")
    cl.add_argument("--end", default=None, help="ISO end time")
    cl.add_argument("--hours", type=int, default=48, help="hours from start/now if end omitted")
    cl.add_argument("--exclude-private", action="store_true", help="exclude solo/private holds")
    cl.set_defaults(func=cmd_calendar_list)

    cg = base(casub.add_parser("get", help="get one event on a company calendar by ID"), mailbox=False)
    cg.add_argument("--for", dest="for_mailbox", required=True, metavar="ADDR", help="the roster person's calendar")
    cg.add_argument("--event", required=True, metavar="ID", help="event ID")
    cg.set_defaults(func=cmd_calendar_get)

    hd = writes(base(sub.add_parser("hold", help="add an audited private calendar hold"), mailbox=False))
    hd.add_argument("--for", dest="for_mailbox", required=True, metavar="ADDR",
                    help="the roster person's calendar")
    hd.add_argument("--start", required=True, help="ISO start time, preferably with an offset")
    hd.add_argument("--minutes", type=int, default=30)
    hd.add_argument("--end", default=None, help="optional ISO end time (overrides --minutes)")
    hd.add_argument("--summary", required=True, help="event title, 1-240 characters")
    hd.add_argument("--description", default="", help="optional event description")
    hd.add_argument("--description-file", default=None, help="path to description file")
    hd.add_argument("--attendee", action="append", default=None, metavar="ADDR",
                    help="attendee to invite; repeat for several")
    hd.add_argument("--uid", default=None, help="optional UID from ICS file")
    hd.set_defaults(func=cmd_calendar_hold)

    sc = writes(base(sub.add_parser("schedule", help="event and confirmation, both or neither")))
    sc.add_argument("--thread", required=True)
    sc.add_argument("--slot", required=True, help="ISO start time")
    sc.add_argument("--minutes", type=int, default=cal.DEFAULT_MINUTES)
    sc.add_argument("--attendee", default=None, help="defaults to the last sender on the thread")
    sc.add_argument("--body-file", required=True)
    sc.add_argument("--summary", default=None, help="the event title")
    sc.add_argument("--approval-issue", default=None, metavar="N")
    sc.set_defaults(func=cmd_schedule)

    mt = sub.add_parser("mint-token", help=argparse.SUPPRESS)
    mt.add_argument("--mailbox", required=True)
    mt.add_argument("--service", required=True, choices=sorted(auth.SERVICE_SCOPES))
    mt.add_argument("--json", action="store_true")
    mt.set_defaults(func=cmd_mint_token)

    ce = base(sub.add_parser("connector-event", help=argparse.SUPPRESS), mailbox=False)
    ce.set_defaults(func=cmd_connector_event)

    au = sub.add_parser("audit", help="print the mail audit log")
    au.add_argument("--since", default=None)
    au.add_argument("--employee", default=None)
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_audit)

    sy = writes(base(sub.add_parser(
        "sync", help="persist mailbox mail locally; export/ack a batch without a hub token")))
    sy.add_argument("--all-roster", action="store_true",
                    help="every address on registry/people.yaml this employee may read")
    sy.add_argument("--backfill", default="90d",
                    help="first-run window when there is no history cursor (90d, 30d, ...)")
    sy.add_argument("--limit", type=int, default=SYNC_LIMIT)
    sy.set_defaults(func=cmd_sync, sync_cmd=None)
    sysub = sy.add_subparsers(dest="sync_cmd", required=False)
    se = base(sysub.add_parser("export", help="print unpushed messages (JSON for a runner)"))
    se.add_argument("--limit", type=int, default=SYNC_LIMIT)
    se.set_defaults(func=cmd_sync_export)
    sa = writes(base(sysub.add_parser("ack", help="mark exported message ids as pushed")))
    sa.add_argument("ids", nargs="*", metavar="MSG_ID")
    sa.set_defaults(func=cmd_sync_ack)
    return p


def main(argv=None):
    import connectors.mail as pkg
    args = build_parser().parse_args(argv)
    if getattr(args, "untriaged", False) and (getattr(args, "new", False)
                                              or getattr(args, "since", None)):
        args._parser.error("--untriaged is label-based and cannot be combined with --new "
                           "or --since")
    pkg.JSON_ERRORS = bool(getattr(args, "json", False) or getattr(args, "format", "") == "json")
    try:
        return args.func(args) or 0
    except Refused as e:
        report(e, "refused")
        return 2
    except Failure as e:
        report(e, "failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
