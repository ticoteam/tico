"""Task caps (PLAN §7, scenarios 1-10): a sender's tasks to a manager per day, checked in the hub before any automation.
The count is the sender's tasks for the receiver today, minus the ones it closed before the receiver acted."""
import datetime as dt

from ..fakes import ReplayJudge, attach_judge
from ..harness import (DANA, DANA_ID, DANA_REF, CLAUDE, CLAUDE_ID, CLAUDE_REF, LEAD, LEAD_ID, LEAD_REF, OPENAI,
                       OPENAI_ID, OPENAI_REF, RELEASE_ID, RELEASE_REF, W2, W3, Check, Scenario, accepted, automation,
                       auto_id, body, close_task, comment, got, make_group, put_cap, put_pool, replied, rows, run_now, send,
                       setup, task_id_of)


# ---------------------------------------------------------------------------------------------- helpers
def payload_has(f, key, **want):
    setup(f, "cap")
    data = got(f, key).body if isinstance(got(f, key).body, dict) else {}
    wrong = {k: data.get(k) for k in want if data.get(k) != want[k]}
    return not wrong, f"wrong: {wrong}" if wrong else "error, cap, count and receiver as wanted"


def today_list(f, key, n):
    setup(f, "cap")
    data = got(f, key).body if isinstance(got(f, key).body, dict) else {}
    today = data.get("today") or []
    shaped = all({"id", "title", "status"} <= set(item) for item in today)
    message = bool(data.get("message"))
    return len(today) == n and shaped and message, f"{len(today)} listed, message {message}"


def refused_count(f, key, count, needs):
    setup(f, *needs)
    reply = got(f, key)
    data = reply.body if isinstance(reply.body, dict) else {}
    return reply.status == 409 and data.get("count") == count, f"{reply.status}, count {data.get('count')}"


def decided(f):
    setup(f, "cap", "sent")
    ran = rows(body(f, "runs"))
    # A Decide step's outputs are keyed by question id (CONTRACT §2 Runs): task-rank@1's needs_you.
    hit = any((step.get("outputs") or {}).get("needs_you") for run in ran for step in run.get("steps", [])
              if step.get("id") == "rank")
    return hit, f"{len(ran)} run(s) of the rank step"


def via_manager(f):
    setup(f, "cap")
    data = got(f, "skip").body if isinstance(got(f, "skip").body, dict) else {}
    return data.get("via") == LEAD_REF, f"via {data.get('via')}"


def self_run_ok(f):
    setup(f, "cap")
    run = body(f, "ran").get("run") or {}   # POST /{id}/run answers {"run": {...}} (CONTRACT §2)
    return run.get("status") == "ok", f"run {run.get('status')}"


def follows_group(f):
    setup(f, "move", "auto_after")
    want = f"group:{got(f, 'release')}"
    data = body(f, "auto_after")
    return data.get("group") == want, f"group {data.get('group')}, want {want}"


# ---------------------------------------------------------------------------------------------- 1 lead under cap
def s1_steps(org, f):
    # A scripted answer in task-rank@1's shape (questions/task-rank.json): the check reads the ranking, not the model.
    org.step(f, "judge", attach_judge, org, ReplayJudge([{"needs_you": {"type": "noul", "noul": 0.9},
                                                           "urgency": {"type": "score", "score": 2}}]))
    org.step(f, "cap", put_cap, org, CLAUDE_REF, LEAD_REF, 10)
    org.step(f, "auto", automation, org, "Rank · engineering-lead", LEAD_REF,
             trigger={"type": "task_received", "to": LEAD_REF},
             steps=[{"id": "rank", "type": "decide", "enabled": True, "set": "task-rank@1", "level": "low",
                     "out": "d1"}])
    org.step(f, "sent", send, org, CLAUDE, LEAD_ID, "Cap one")
    org.step(f, "runs", lambda: org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}/runs", wave=W3))


# ---------------------------------------------------------------------------------------------- 2 at the cap
def s2_steps(org, f):
    org.step(f, "cap", put_cap, org, LEAD_REF, DANA_REF, 10)
    for n in range(1, 12):
        org.step(f, f"task{n}", send, org, LEAD, DANA_ID, f"Lead task {n}")
    org.step(f, "queued", lambda: org.sql("SELECT count(*) n FROM tasks WHERE title=?",
                                          ("Lead task 11",))[0]["n"])


def nothing_queued(f):
    setup(f, "cap")
    n = got(f, "queued")
    return n == 0, f"{n} task row(s) for the 11th"


# ---------------------------------------------------------------------------------------------- 3 skip-level
def s3_steps(org, f):
    org.step(f, "cap", put_cap, org, CLAUDE_REF, DANA_REF, 0)
    org.step(f, "skip", send, org, CLAUDE, DANA_ID, "Skip level")
    org.step(f, "via_lead", send, org, CLAUDE, LEAD_ID, "Through the lead")
    org.step(f, "lead_on", send, org, LEAD, DANA_ID, "Lead passes it on")
    org.step(f, "not_manager", put_cap, org, CLAUDE_REF, RELEASE_REF, 5)


# ---------------------------------------------------------------------------------------------- 4 no cap
def s4_steps(org, f):
    for n in range(1, 13):
        org.step(f, f"u{n}", send, org, CLAUDE, LEAD_ID, f"Unlimited {n}")


# ---------------------------------------------------------------------------------------------- 5 next day
def s5_steps(org, f):
    org.step(f, "cap", put_cap, org, CLAUDE_REF, LEAD_REF, 1)
    org.step(f, "first", send, org, CLAUDE, LEAD_ID, "Day one")
    org.step(f, "second", send, org, CLAUDE, LEAD_ID, "Day one again")
    org.pass_time(dt.timedelta(days=1))
    org.step(f, "resubmit", send, org, CLAUDE, LEAD_ID, "Day one again")


# ---------------------------------------------------------------------------------------------- 6 close before act
def s6_steps(org, f):
    org.step(f, "cap", put_cap, org, LEAD_REF, DANA_REF, 1)
    org.step(f, "closed_task", send, org, LEAD, DANA_ID, "Closed before Dana acts")
    org.step(f, "needs_before", org.api, "get", "/api/v2/needs-you", who=DANA, wave=W2)
    org.step(f, "closed", close_task, org, f, "closed_task", LEAD)
    org.step(f, "needs_after", org.api, "get", "/api/v2/needs-you", who=DANA, wave=W2)
    org.step(f, "history", lambda: org.api("get", f"/api/v2/tasks/{task_id_of(got(f, 'closed_task'))}", who=DANA))
    org.step(f, "after", send, org, LEAD, DANA_ID, "After the close")
    org.step(f, "over", send, org, LEAD, DANA_ID, "Still over?")


def closed_out(f):
    """Dana's Needs you lists the task before the close and not after (CONTRACT §5 GET /api/v2/needs-you), and the task
    itself reads back as closed."""
    setup(f, "needs_before", "closed", "needs_after", "history")
    tid = task_id_of(got(f, "closed_task"))
    ids = lambda key: {str(item.get("id")) for item in body(f, key).get("items") or []}  # noqa: E731
    status = (body(f, "history").get("task") or body(f, "history")).get("status")
    ok = tid in ids("needs_before") and tid not in ids("needs_after") and status == "closed"
    return ok, f"in Needs you before {tid in ids('needs_before')}, after {tid in ids('needs_after')}; status {status}"


# ---------------------------------------------------------------------------------------------- 7 close after act
def s7_steps(org, f):
    org.step(f, "cap", put_cap, org, LEAD_REF, DANA_REF, 1)
    org.step(f, "acted", send, org, LEAD, DANA_ID, "Dana acts on this")
    org.step(f, "comment", comment, org, f, "acted", DANA, "Looking into it")
    org.step(f, "closed", close_task, org, f, "acted", LEAD)
    org.step(f, "over", send, org, LEAD, DANA_ID, "Still counts")


# ---------------------------------------------------------------------------------------------- 8 own work
def s8_steps(org, f):
    org.step(f, "self_work", send, org, CLAUDE, CLAUDE_ID, "Own work")
    org.step(f, "cap", put_cap, org, CLAUDE_REF, LEAD_REF, 0)
    org.step(f, "own_capped", send, org, CLAUDE, CLAUDE_ID, "Own work under a zero cap")
    org.step(f, "auto", automation, org, "Own work · builder-claude", CLAUDE_REF,
             steps=[{"id": "make", "type": "action", "enabled": True, "do": "create_task",
                     "args": {"owner_id": CLAUDE_ID, "title": "Automation own work"}}])
    org.step(f, "ran", run_now, org, f, "auto")


# ---------------------------------------------------------------------------------------------- 9 moved bot
def s9_steps(org, f):
    org.step(f, "eng", make_group, org, "Engineering", bots=[LEAD_ID, CLAUDE_ID, OPENAI_ID])
    org.step(f, "release", make_group, org, "Release", bots=[RELEASE_ID])
    org.step(f, "cap", put_cap, org, OPENAI_REF, LEAD_REF, 0)
    org.step(f, "goal", org.api, "post", "/api/v2/goals", {"title": "Ship the importer", "owner_id": OPENAI_REF})
    org.step(f, "pool", lambda: put_pool(org, got(f, "release"), {"daily_usd": 50}))
    org.step(f, "auto", lambda: automation(org, "Inbox · builder-openai", OPENAI_REF,
                                           group=f"group:{got(f, 'eng')}"))
    org.step(f, "move", lambda: org.api("patch", f"/api/v2/groups/{got(f, 'release')}",
                                        {"add": {"bots": [OPENAI_ID]}}, wave=W2))
    org.step(f, "old_pair", send, org, OPENAI, LEAD_ID, "After the move")
    org.step(f, "new_pair", send, org, OPENAI, RELEASE_ID, "New pair")
    org.step(f, "auto_after", lambda: org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}", wave=W3))
    org.step(f, "goal_after", lambda: org.api("get", f"/api/v2/goals/{goal_of(body(f, 'goal')).get('id')}"))
    org.step(f, "limits_after", org.api, "get", "/api/v2/usage/limits")


def goal_of(data):
    return (data.get("goal") or data) if isinstance(data, dict) else {}


def goals_and_spend_follow(f):
    """The bot's goal keeps its owner (goals belong to the bot, not its group), and its limits row counts it against the
    new group's pool (GET /api/v2/usage/limits `pools`, redesign on)."""
    setup(f, "move", "goal", "pool", "goal_after", "limits_after")
    owner = goal_of(body(f, "goal_after")).get("owner_id")
    pools = ((body(f, "limits_after").get("bots") or {}).get(OPENAI_ID) or {}).get("pools")
    want = "group:" + got(f, "release")
    return owner == OPENAI_REF and pools == [want], f"goal owner {owner}, pools {pools}, want [{want}]"


# ---------------------------------------------------------------------------------------------- 10 before automations
def s10_steps(org, f):
    org.step(f, "auto", automation, org, "Triage · engineering-lead", LEAD_REF,
             trigger={"type": "task_received", "to": LEAD_REF})
    org.step(f, "cap", put_cap, org, CLAUDE_REF, LEAD_REF, 0)
    org.step(f, "refused", send, org, CLAUDE, LEAD_ID, "Never runs")
    org.step(f, "runs", lambda: org.api("get", f"/api/v2/automations/{auto_id(f, 'auto')}/runs", wave=W3))


def no_run(f):
    setup(f, "cap")
    got(f, "refused")
    ran = rows(body(f, "runs"))
    return not ran, f"{len(ran)} run(s)"


def refused_409(f):
    return replied(f, "refused", 409, needs=("cap",))


SCENARIOS = [
    Scenario(1, "caps-lead-under-cap", "Bot sends to its lead under cap; Decide ranks it into Needs you", s1_steps, [
        Check("accepted under the cap", W2, lambda f: replied(f, "sent", 201, 200, needs=("cap",))),
        Check("Decide ranks it into Needs you", W3, decided),
    ]),
    Scenario(2, "caps-lead-at-cap", "Lead at 10/10 sends an 11th to Dana: refused in the same call, nothing queued", s2_steps, [
        Check("11th refused in the same call (409)", W2, lambda f: replied(f, "task11", 409, needs=("cap",))),
        Check("payload: task_cap, cap 10, count 10, receiver person:dana", W2,
              lambda f: payload_has(f, "task11", error="task_cap", cap=10, count=10, receiver=DANA_REF)),
        Check("payload lists today's ten tasks and a message", W2, lambda f: today_list(f, "task11", 10)),
        Check("nothing queued for the 11th", W2, nothing_queued),
    ]),
    Scenario(3, "caps-skip-level-zero", "Skip-level pair at 0 refused, go through your manager; via the lead accepted", s3_steps, [
        Check("skip-level send refused with 409", W2, lambda f: replied(f, "skip", 409, needs=("cap",))),
        Check("refusal names the manager to go through", W2, via_manager),
        Check("same task through the lead accepted", W2,
              lambda f: accepted(f, "via_lead", "lead_on", needs=("cap",))),
        Check("cap on a non-manager is a validation error", W2, lambda f: replied(f, "not_manager", 400, 422)),
    ]),
    Scenario(4, "caps-no-cap-unlimited", "No cap set: unlimited", s4_steps, [
        Check("twelve tasks with no cap row all accepted (holds today)", W2,
              lambda f: accepted(f, *[f"u{n}" for n in range(1, 13)])),
    ]),
    Scenario(5, "caps-next-day", "Rejected sender resubmits the next day: accepted", s5_steps, [
        Check("second task the same day refused (409)", W2, lambda f: replied(f, "second", 409, needs=("cap",))),
        Check("resubmitted the next day accepted", W2,
              lambda f: replied(f, "resubmit", 201, 200, needs=("cap", "first"))),
    ]),
    Scenario(6, "caps-close-frees-slot", "Sender closes a task before Dana acts: slot freed, out of its count", s6_steps, [
        Check("closing it as the sender succeeds", W2, lambda f: replied(f, "closed", 200, needs=("closed_task",))),
        Check("a new task is accepted once the closed one leaves the count", W2,
              lambda f: replied(f, "after", 201, 200, needs=("cap", "closed"))),
        Check("the next one is refused with count 1 (the closed task is not counted)", W2,
              lambda f: refused_count(f, "over", 1, needs=("cap", "closed", "after"))),
        Check("closed task leaves Needs you and shows as closed in history", W2, closed_out),
    ]),
    Scenario(7, "caps-close-after-acted", "Closing after Dana acted still counts", s7_steps, [
        Check("closing after Dana's comment still counts: next refused, count 1", W2,
              lambda f: refused_count(f, "over", 1, needs=("cap", "acted", "comment", "closed"))),
    ]),
    Scenario(8, "caps-own-work", "Own work (self-assigned, or an owner's automation) is never capped", s8_steps, [
        Check("self-assigned work accepted (holds today)", W2, lambda f: replied(f, "self_work", 201, 200)),
        Check("self-assigned work accepted under a zero cap on its pair", W2,
              lambda f: replied(f, "own_capped", 201, 200, needs=("cap",))),
        Check("automation-created work owned by its owner accepted under the zero cap", W3, self_run_ok),
    ]),
    Scenario(9, "caps-moved-bot", "Bot moved to another group: old pair cap gone, new pair uncapped, automations follow", s9_steps, [
        Check("old pair's cap is gone: send to the old lead accepted", W2,
              lambda f: accepted(f, "old_pair", needs=("cap", "move"))),
        Check("new pair is uncapped: send to the new lead accepted", W2,
              lambda f: accepted(f, "new_pair", needs=("cap", "move"))),
        Check("automations follow the bot into its new group", W3, follows_group),
        Check("goals and the group's spend limit follow too", W2, goals_and_spend_follow),
    ]),
    Scenario(10, "caps-before-automations", "Caps run before any automation: a rejected task never starts a run", s10_steps, [
        Check("rejected with 409 before any automation", W2, refused_409),
        Check("a rejected task never starts a run", W3, no_run),
    ]),
]
