"""Permissions and audit (PLAN §7, scenarios 32-35 and 46): who may edit automations, caps and spend, what a bot may
propose, what every change records, and who may read an automation and its runs (PLAN decision 36). The same rules apply
in the UI, the CLI and MCP; these checks use the API only."""
import json

from ..harness import (CLAUDE, CLAUDE_REF, DANA, DANA_REF, LEAD, LEAD_ID, LEAD_REF, OMAR, OMAR_REF, OPENAI_ID,
                       OPENAI_REF, PRIYA, PRIYA_REF, RELEASE_ID, RELEASE_REF, W2, W3, Check, Scenario, Unmet,
                       approval_id_of, audit_of, hub_notices, auto_id, automation, body, decide, edit_auto, find, get_caps, got,
                       make_group, put_cap, put_limit, replied, rows, run_now, setup)

BOTOPS = {"botops": ("botops", "claude")}   # the helper that hears of every applied proposal


def noted(f, key, approval_key):
    """BotOps got exactly one hub notice naming the approval (CONTRACT §5: from keeper, kind notice, refs.approval)."""
    aid = approval_of(f, approval_key)
    hits = [m for m in got(f, key) if (m.get("refs") or {}).get("approval") == aid]
    return len(hits) == 1, f"{len(hits)} notice(s) naming approval {aid}"


# ---------------------------------------------------------------------------------------------- helpers
def approval_of(f, key):
    """The approval id a proposal filed (CONTRACT §1: {"approval": "<id>", ...}). Unmet when the reply filed none."""
    reply = got(f, key)
    aid = approval_id_of(reply)
    if not aid:
        raise Unmet(W2, f"{key} filed no approval: {reply.status} {reply.excerpt()}")
    return aid


def proposal_filed(f, key):
    """A bot's cap change answers 200 with an approval id and status pending (CONTRACT §1), and is not applied yet."""
    reply = got(f, key)
    data = reply.body if isinstance(reply.body, dict) else {}
    aid = approval_id_of(reply)
    ok = reply.status == 200 and bool(aid) and data.get("status") == "pending"
    return ok, f"{reply.status} approval {aid!r}, status {data.get('status')!r}"


def cap_at(f, key, sender, receiver, per_day, needs=()):
    """The cap row for sender -> receiver in the Budget read `key` has this per_day. Only the `caps` list counts: the
    `proposals` list also names the pair and must not stand in for the applied cap."""
    setup(f, *needs, key)
    data = body(f, key)
    caps = data.get("caps") if isinstance(data, dict) else None
    hit = next((c for c in caps or [] if c.get("sender") == sender and c.get("receiver") == receiver), None)
    return bool(hit) and hit.get("per_day") == per_day, f"row {hit}"


def audit_via(f, key, aid):
    """The audit rows for the cap change with via approval:<id>, from the read `key`."""
    setup(f, key)
    hit = [r for r in rows(body(f, key)) if r.get("via") == f"approval:{aid}"]
    return bool(hit), f"{len(hit)} audit row(s) via approval:{aid}"


def as_obj(value):
    """A before or after value as data: JSON text is parsed, anything else is kept."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def per_day_of(value):
    hit = find(as_obj(value), lambda d: "per_day" in d)
    return hit.get("per_day") if hit else None


def has_audit_shape(row):
    return {"actor", "object", "via", "before", "after"} <= set(row)


def sees_it(f, key):
    """The read `key` returns the automation under its own id (200), so the check is on the right row."""
    setup(f, "auto", key)
    hit = find(body(f, key), lambda d: d.get("id") == auto_id(f, "auto"))
    return bool(hit), f"{key} returned {'it' if hit else 'something else'}"


def sees_runs(f, key):
    """The runs read `key` lists at least one run, the one Priya's run made."""
    setup(f, "run", key)
    count = len(rows(body(f, key)))
    return count >= 1, f"{count} run(s) listed"


def unlisted(f, key, own=None):
    """The automation list `key` leaves Priya's automation out; with `own` it must still show that one (control)."""
    setup(f, key, "auto")
    ids = {row.get("id") for row in rows(body(f, key))}
    gone = auto_id(f, "auto") not in ids
    shown = own is None or own in ids
    return gone and shown, f"{len(ids)} listed; Priya's {'absent' if gone else 'present'}; own shown: {shown}"


def listed(f, key):
    """The automation list `key` shows Priya's automation."""
    setup(f, key, "auto")
    ids = {row.get("id") for row in rows(body(f, key))}
    return auto_id(f, "auto") in ids, f"{len(ids)} listed"


# ---------------------------------------------------------------------------------------------- 32 group lead
def s32_steps(org, f):
    org.step(f, "eng", make_group, org, "Engineering", bots=[LEAD_ID, OPENAI_ID], lead=LEAD_REF)
    org.step(f, "release", make_group, org, "Release", bots=[RELEASE_ID], lead=RELEASE_REF)
    org.step(f, "own_auto", lambda: automation(org, "Inbox · builder-openai", OPENAI_REF,
                                               group=f"group:{got(f, 'eng')}"))
    org.step(f, "other_auto", lambda: automation(org, "Release notes", RELEASE_REF,
                                                 group=f"group:{got(f, 'release')}"))
    org.step(f, "new_ok", lambda: automation(org, "Lead's inbox", OPENAI_REF, group=f"group:{got(f, 'eng')}", who=LEAD))
    org.step(f, "new_out", lambda: automation(org, "Lead's release", RELEASE_REF,
                                              group=f"group:{got(f, 'release')}", who=LEAD))
    org.step(f, "edit_ok", lambda: edit_auto(org, f, "own_auto", LEAD, enabled=False))
    org.step(f, "edit_out", lambda: edit_auto(org, f, "other_auto", LEAD, enabled=False))
    org.step(f, "cap_ok", put_cap, org, OPENAI_REF, LEAD_REF, 3, who=LEAD)
    org.step(f, "cap_out", put_cap, org, RELEASE_REF, DANA_REF, 3, who=LEAD)
    org.step(f, "limit_ok", put_limit, org, OPENAI_ID, {"hourly_usd": 4.0}, who=LEAD)
    org.step(f, "limit_out", put_limit, org, RELEASE_ID, {"hourly_usd": 4.0}, who=LEAD)


# ---------------------------------------------------------------------------------------------- 33 bot's own and reports
def s33_steps(org, f):
    org.step(f, "own", automation, org, "Own · builder-claude", CLAUDE_REF, who=CLAUDE)
    org.step(f, "report", automation, org, "Report · builder-claude", CLAUDE_REF)
    org.step(f, "peer", automation, org, "Peer · builder-openai", OPENAI_REF)
    org.step(f, "own_edit", lambda: edit_auto(org, f, "own", CLAUDE, enabled=False))
    org.step(f, "lead_edit", lambda: edit_auto(org, f, "report", LEAD, enabled=False))
    org.step(f, "peer_edit", lambda: edit_auto(org, f, "peer", CLAUDE, enabled=False))
    org.step(f, "peer_make", automation, org, "Sneaky · builder-openai", OPENAI_REF, who=CLAUDE)


# ---------------------------------------------------------------------------------------------- 34 bot proposes a cap
def s34_steps(org, f):
    org.step(f, "cap", put_cap, org, CLAUDE_REF, LEAD_REF, 5)
    org.step(f, "propose", put_cap, org, CLAUDE_REF, LEAD_REF, 2, who=CLAUDE)
    org.step(f, "caps", get_caps, org)
    org.step(f, "decide", lambda: decide(org, approval_of(f, "propose"), "approved"))
    org.step(f, "caps_after", get_caps, org)
    org.step(f, "audit", audit_of, org, obj=f"task_cap:{CLAUDE_REF}>{LEAD_REF}")
    org.step(f, "botops", hub_notices, org, "botops")


# ---------------------------------------------------------------------------------------------- 35 audit
def s35_steps(org, f):
    org.step(f, "cap_1", put_cap, org, CLAUDE_REF, LEAD_REF, 5)
    org.step(f, "cap_2", put_cap, org, CLAUDE_REF, LEAD_REF, 7)
    org.step(f, "auto", automation, org, "Audit · builder-claude", CLAUDE_REF)
    org.step(f, "auto_edit", lambda: edit_auto(org, f, "auto", DANA, enabled=False))
    org.step(f, "own", automation, org, "Omar's inbox", OMAR_REF, who=OMAR)
    org.step(f, "audit", audit_of, org)
    org.step(f, "audit_omar", audit_of, org, who=OMAR)


def audited(f):
    """Each change leaves one row with actor, object, via, before and after: two cap saves and the automation's create
    and edit (the owner's read carries all of them)."""
    setup(f, "audit", "cap_1", "cap_2", "auto", "auto_edit")
    every = rows(body(f, "audit"))
    cap = f"task_cap:{CLAUDE_REF}>{LEAD_REF}"
    auto = auto_id(f, "auto")
    cap_rows = [r for r in every if r.get("object") == cap]
    auto_rows = [r for r in every if auto in str(r.get("object"))]
    shaped = bool(every) and all(has_audit_shape(r) for r in every)
    ok = shaped and len(cap_rows) >= 2 and len(auto_rows) >= 2 and all(r.get("actor") for r in cap_rows + auto_rows)
    shape = "ok" if shaped else "missing keys"
    return ok, f"{len(cap_rows)} cap row(s), {len(auto_rows)} automation row(s), shape {shape}"


def change_shown(f):
    """The 5 to 7 cap change reads back with its before (per_day 5) and after (per_day 7)."""
    setup(f, "audit", "cap_1", "cap_2")
    cap = f"task_cap:{CLAUDE_REF}>{LEAD_REF}"
    hit = [r for r in rows(body(f, "audit")) if r.get("object") == cap
           and per_day_of(r.get("before")) == 5 and per_day_of(r.get("after")) == 7]
    return bool(hit), f"{len(hit)} row(s) with before 5 and after 7"


def owner_reads_all(f):
    """Dana, the owner of the org, reads Omar's change as well as her own."""
    setup(f, "audit", "own")
    objects = [str(r.get("object")) for r in rows(body(f, "audit"))]
    own = auto_id(f, "own")
    return any(own in o for o in objects) and any(f"task_cap:{CLAUDE_REF}" in o for o in objects), \
        f"{len(objects)} row(s) read"


def self_reads_own(f):
    """Omar reads only his own changes: every row is his, his automation is in it, and Dana's rows are not."""
    setup(f, "audit_omar", "own", "auto")
    mine = rows(body(f, "audit_omar"))
    objects = [str(r.get("object")) for r in mine]
    own, auto = auto_id(f, "own"), auto_id(f, "auto")
    ok = bool(mine) and all("omar" in str(r.get("actor")) for r in mine) \
        and any(own in o for o in objects) \
        and not any(f"task_cap:{CLAUDE_REF}" in o for o in objects) and not any(auto in o for o in objects)
    return ok, f"{len(mine)} row(s) for Omar; actors {sorted({str(r.get('actor')) for r in mine})}"


# ---------------------------------------------------------------------------------------------- 46 visibility
def read_as(org, f, who):
    return org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}", who=who, wave=W3)


def runs_as(org, f, who):
    return org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}/runs", who=who, wave=W3)


def list_as(org, who):
    return org.api("get", "/api/v2/automations", who=who, wave=W3)


def s46_steps(org, f):
    org.step(f, "auto", automation, org, "Priya's triage", PRIYA_REF, who=PRIYA)
    org.step(f, "run", run_now, org, f, "auto", who=PRIYA)
    org.step(f, "own", automation, org, "Omar's inbox", OMAR_REF, who=OMAR)
    for name, who in (("dana", DANA), ("priya", PRIYA), ("omar", OMAR), ("lead", LEAD)):
        org.step(f, f"{name}_read", read_as, org, f, who)
        org.step(f, f"{name}_runs", runs_as, org, f, who)
        org.step(f, f"{name}_list", list_as, org, who)


# ---------------------------------------------------------------------------------------------- scenarios
SCENARIOS = [
    Scenario(32, "perm-group-lead", "Group lead edits automations, caps and spend in the group; outside it, refused", s32_steps, [
        Check("lead creates an automation in her group", W3, lambda f: replied(f, "new_ok", 200, 201)),
        Check("lead edits an automation in her group", W3, lambda f: replied(f, "edit_ok", 200)),
        Check("lead refused on an automation outside her group", W3,
              lambda f: replied(f, "edit_out", 403, needs=("edit_ok",))),
        Check("lead refused to create an automation outside her group", W3,
              lambda f: replied(f, "new_out", 403, needs=("new_ok",))),
        Check("lead sets a cap for a teammate in her group", W2, lambda f: replied(f, "cap_ok", 200, 201)),
        Check("lead refused a cap outside her group", W2,
              lambda f: replied(f, "cap_out", 403, needs=("cap_ok",))),
        Check("lead sets a spend limit for a teammate in her group", W2, lambda f: replied(f, "limit_ok", 200)),
        Check("lead refused a spend limit outside her group", W2,
              lambda f: replied(f, "limit_out", 403, needs=("limit_ok",))),
    ]),
    Scenario(33, "perm-bot-reports", "Bot edits its own and its reports' automations; a peer's is refused", s33_steps, [
        Check("a bot makes an automation for itself", W3, lambda f: replied(f, "own", 200, 201)),
        Check("a bot edits its own automation", W3, lambda f: replied(f, "own_edit", 200, needs=("own",))),
        Check("a manager edits a report's automation", W3, lambda f: replied(f, "lead_edit", 200, needs=("report",))),
        Check("a bot edits a peer's automation: refused", W3,
              lambda f: replied(f, "peer_edit", 403, needs=("own_edit", "peer"))),
        Check("a bot makes an automation for a peer: refused", W3,
              lambda f: replied(f, "peer_make", 403, needs=("own",))),
    ]),
    Scenario(34, "perm-bot-proposes-cap", "Bot proposes a cap change: pending; Dana approves; the cap applies",
             s34_steps, [
        Check("a bot's cap change answers 200 with an approval id, status pending", W2,
              lambda f: proposal_filed(f, "propose")),
        Check("until a person approves, the cap stays at 5", W2,
              lambda f: cap_at(f, "caps", CLAUDE_REF, LEAD_REF, 5, needs=("cap",))),
        Check("Dana's approval is accepted", W2, lambda f: replied(f, "decide", 200, needs=("propose",))),
        Check("after approval the cap reads back at 2", W2,
              lambda f: cap_at(f, "caps_after", CLAUDE_REF, LEAD_REF, 2, needs=("decide",))),
        Check("the cap change is audited with via approval:<id>", W2,
              lambda f: audit_via(f, "audit", approval_of(f, "propose"))),
        Check("BotOps gets a notice about the change", W2, lambda f: noted(f, "botops", "propose")),
    ], bots=BOTOPS),
    Scenario(35, "perm-audit", "Every change writes actor, object, before and after", s35_steps, [
        Check("cap saves and automation writes each leave a row with actor, object, via, before, after", W2, audited),
        Check("the 5 to 7 cap change shows its before and after", W2, change_shown),
        Check("the owner reads every row, Omar's change included", W2, owner_reads_all),
        Check("Omar reads only his own changes", W2, self_reads_own),
    ]),
    Scenario(46, "perm-visibility", "An automation and its runs: owner and everyone above see them; peers don't",
             s46_steps, [
                 Check("Priya saves an automation she owns", W3, lambda f: replied(f, "auto", 200, 201)),
                 Check("Priya runs it and the run is logged", W3,
                       lambda f: replied(f, "run", 200, 201, 202, needs=("auto",))),
                 Check("Dana, above Priya, reads the automation", W3, lambda f: sees_it(f, "dana_read")),
                 Check("Dana, above Priya, reads its runs", W3, lambda f: sees_runs(f, "dana_runs")),
                 Check("Dana's list shows it", W3, lambda f: listed(f, "dana_list")),
                 Check("Priya, its owner, reads it", W3, lambda f: sees_it(f, "priya_read")),
                 Check("Priya, its owner, reads its runs", W3, lambda f: sees_runs(f, "priya_runs")),
                 Check("Omar, a peer, cannot read it (404 or 403)", W3,
                       lambda f: replied(f, "omar_read", 403, 404, needs=("auto",))),
                 Check("Omar, a peer, cannot read its runs (404 or 403)", W3,
                       lambda f: replied(f, "omar_runs", 403, 404, needs=("auto",))),
                 Check("Omar's list leaves it out and still shows his own", W3,
                       lambda f: unlisted(f, "omar_list", own=auto_id(f, "own"))),
                 Check("the Engineering Lead, a peer, cannot read it (404 or 403)", W3,
                       lambda f: replied(f, "lead_read", 403, 404, needs=("auto",))),
                 Check("the Engineering Lead's list leaves it out", W3, lambda f: unlisted(f, "lead_list")),
             ]),
]
