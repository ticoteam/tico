"""Read-only SQL over the hub database, with the caller's visibility built into the database layer.

Every guarded table is shadowed, for one request, by a temp view of the same name that carries
the caller's rules from `backend/auth.py`, so `SELECT * FROM messages` already means "the
messages you may read". The view reads its base table through an inner temp view whose name is
random for this connection, and SQLite's authorizer lets a base table be read only from that
inner view: neither `main.messages` nor a CTE named `messages` (both of which the authorizer
reports the same way as the view) reaches the rows underneath. Everything that is not a read is
denied outright. Nothing here depends on prompt text.
"""

import base64
from collections import OrderedDict
from collections.abc import Callable
from contextvars import copy_context
import hashlib
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import weakref

from fastapi import Request
from pydantic import Field

from . import outcome_stats
from . import question_marks
from . import run_triggers as RT
from . import task_privacy as privacy
from . import teammate_access as TA
from .privacy_index import ReadIndex
from .bot_access import q, qlist
from .models import Contract
from .store import H, Problem

# Rows and seconds per query; `max_rows` in the request may only lower the cap.
LIMITS = {"owner": (5000, 20.0), "human": (500, 5.0), "bot": (500, 5.0)}
# Readable as they are, by everyone: nothing personal, nothing secret, plus the schema.
OPEN = {"rate_limits", "service_health", "learnings", "sqlite_master"}
# A message's own read rule (guarded), as a view of the unfiltered messages that no caller reads by name: the views of
# the tables read it, `messages` among them with the timeline's cut (a person's timeline shows only messages with a seq).
READABLE_MESSAGES = "readable_messages"
# The Company page's tables the owner reads as the page's API does; the readers' own tables are not here at all.
COMPANY_PAGE_OWNER_TABLES = (
    "company_page_settings",
    "audiences",
    "audience_sources",
    "disclosure_revisions",
    "disclosure_rules",
    "editions",
    "edition_sources",
    "edition_checks",
    "page_stats",
    "stat_board_approvals",
    "page_releases",
)
# Table-valued functions SQLite reports like tables of the main schema.
TABLE_FUNCTIONS = {"json_each", "json_tree"}
SAFE_COLUMNS = {
    "slack_posts": {"message_id", "bot", "state", "attempts", "next_attempt_at", "slack_ts", "created_at", "updated_at"}
}
# Columns nobody reads through SQL. Tables not named in `guarded` (credentials, credential_keys,
# credential_grants, credential_uses, idempotency, runners, runner_states, enrollments, session_epochs, bot_session_epochs, settings_changes,
# backup_verified_blobs, _litestream_*, sqlite_* except sqlite_master) are denied outright.
HIDDEN = {"task_requesters": {"token_hash"}, "attempts": {"token_hash"}, "service_jobs": {"token_hash"}}
# Columns read as an expression over the caller's own views: a column naming a task the caller may not read gives
# its id to nobody. A bot's claim limit (bot_control.claim_task) on such a task reads '': a limit is on, as
# GET /api/v2/bots says with `claim_scoped` and no `claim_task`.
# A wait span on a task it filed or waits on names that task only to a reader who may read it, as task_waits does
# (waiting_on.unnamed).
SPAN_TASK = "CASE WHEN source.kind = 'tasks' AND source.who NOT IN (SELECT id FROM tasks) THEN '' ELSE {who} END"
MASKED = {
    "task_wait_spans": {"who": SPAN_TASK.format(who="source.who")},
    "bot_control": {
        "claim_task": "CASE WHEN source.claim_task IS NULL THEN NULL "
        "WHEN source.claim_task IN (SELECT id FROM tasks) THEN source.claim_task "
        "ELSE '' END"
    },
}
# changes()/total_changes()/last_insert_rowid() report a pooled connection's earlier work: another caller's visibility
# sets, whose sizes count rows this caller may not see.
DENIED_FUNCTIONS = {"load_extension", "changes", "total_changes", "last_insert_rowid"}


def teammate_masks(unseen, me, inner=None, names=None, bots=()):
    """MASKED for one request: participant lists drop the people the caller may not see (in a conversation the caller
    is not in), group lists the groups they may not see. A task's owner, requester and closer, the actor of an event
    or a task event, and a message's author and recipient read '' when they are such a person, unless the caller is a party to
    that task or in that conversation: whoever works with you there is known to you there. A thread's owner, its
    followers and mentions read the same, and its members also leave out the groups and `bots` the caller may not see.
    `names` are the thread_members values naming the caller (itself and its groups). A bot's operator, owners,
    manager, author and group (its columns and its configuration) leave out whoever the caller may not see."""
    people = sorted(t for t in unseen if t.startswith("human:"))
    groups = sorted(t.split(":", 1)[1] for t in unseen if t.startswith("group:"))
    bare = sorted(t[6:] for t in people)
    out = {}
    strangers = people + ["group:" + g for g in groups] + sorted(bots)
    if strangers and inner is not None:
        hidden = qlist(people)
        names = names or f"({me})"

        def member(conversation):
            # In it, as teammate_access.Known says: its participant list or its root's, or a member of its root.
            return (
                f"EXISTS (SELECT 1 FROM {inner('conversations')} v JOIN {inner('conversations')} vr "
                f"ON vr.id=COALESCE(v.root_id,v.id) WHERE v.id={conversation} AND ("
                f"EXISTS (SELECT 1 FROM json_each(v.participants) p WHERE p.value={me}) OR "
                f"EXISTS (SELECT 1 FROM json_each(vr.participants) p WHERE p.value={me}) OR "
                f"EXISTS (SELECT 1 FROM {inner('thread_members')} tm WHERE tm.thread_id=vr.id AND tm.actor IN {names})))"
            )

        def party(task):
            return (
                f"EXISTS (SELECT 1 FROM {inner('tasks')} t WHERE t.id={task} AND (t.owner_id={me} OR t.requester_id={me} "
                f"OR {member('t.conversation_id')}))"
            )

        def masked(column, known, among=hidden):
            return f"CASE WHEN source.{column} IN {among} AND NOT {known} THEN '' ELSE source.{column} END"

        out["thread_members"] = {
            column: masked(column, member("source.thread_id"), qlist(strangers)) for column in ("actor", "added_by")
        }
        # A wait span names a person, group or bot the caller may not see as task_waits does: as ''.
        out["task_wait_spans"] = {
            "who": SPAN_TASK.format(who=f"CASE WHEN source.who IN {qlist(strangers)} THEN '' ELSE source.who END")
        }
    if people and inner is not None:
        out["tasks"] = {
            column: masked(column, party("source.id")) for column in ("owner_id", "requester_id", "closed_by_id")
        }
        # A task's history row names its actor as the task does; the other subjects are everyone's to read.
        out["history_events"] = {
            "actor_id": (
                f"CASE WHEN source.subject_type='task' THEN "
                f"{masked('actor_id', party('source.subject_id'))} ELSE source.actor_id END"
            )
        }
        out["events"] = {"actor": masked("actor", f"({member('source.target')} OR {party('source.target')})")}
        # As REST and the stream say it (teammate_access.Known): the message stays, its author is not named.
        out["messages"] = {
            column: masked(column, member("source.conversation_id")) for column in ("sender_id", "recipient_id")
        }
        out["thread_followers"] = {"actor": masked("actor", member("source.thread_id"))}
        said = f"(SELECT mm.conversation_id FROM {inner('messages')} mm WHERE mm.id=source.message_id)"
        out["message_mentions"] = {"actor": masked("actor", member(said))}
    if people or groups:
        out["bot_config"] = bot_config_masks(people, bare, groups)
        out["bot_settings"] = bot_settings_masks(people, bare, groups)
    if people:
        out["conversations"] = {
            "participants": (
                "CASE WHEN source.participants IS NULL OR NOT json_valid(source.participants) THEN NULL "
                f"WHEN EXISTS (SELECT 1 FROM json_each(source.participants) WHERE value={me}) THEN source.participants "
                f"ELSE (SELECT json_group_array(value) FROM json_each(source.participants) WHERE value NOT IN {qlist(people)}) "
                "END"
            )
        }
        if inner is not None:
            out["conversations"]["owner_id"] = masked("owner_id", member("source.id"))
    if groups:
        out["humans"] = {
            "teams": (
                "CASE WHEN source.teams IS NULL OR NOT json_valid(source.teams) THEN source.teams "
                f"ELSE (SELECT json_group_array(value) FROM json_each(source.teams) WHERE value NOT IN {qlist(groups)}) END"
            )
        }
    return out


def bot_config_masks(people, bare, groups):
    """bot_config columns less the people (`human:<id>` in `people`, bare ids in `bare`) and groups the caller may
    not see: ids read '' and owner lists drop them."""
    nobody = "(SELECT NULL WHERE 0)"  # an IN list that matches nothing
    actors, ids, teams = (
        qlist(people) if people else nobody,
        qlist(bare) if bare else nobody,
        qlist(groups) if groups else nobody,
    )
    out = {"team": f"CASE WHEN source.team IN {teams} THEN NULL ELSE source.team END"}
    if not people:
        return out
    for column in ("owner_id", "reports_to"):
        out[column] = (
            f"CASE WHEN source.{column} IN {ids} OR source.{column} IN {actors} THEN '' ELSE source.{column} END"
        )
    for column in ("created_by", "definition_updated_by"):
        out[column] = f"CASE WHEN source.{column} IN {actors} THEN '' ELSE source.{column} END"
    for column in ("owner_ids", "bot_owners"):
        out[column] = (
            f"CASE WHEN source.{column} IS NULL OR NOT json_valid(source.{column}) THEN source.{column} "
            f"ELSE (SELECT json_group_array(value) FROM json_each(source.{column}) "
            f"WHERE value NOT IN {ids} AND value NOT IN {actors}) END"
        )
    return out


def bot_settings_masks(people, bare, groups):
    """bot_settings less the people and groups the caller may not see: the declaration's other keys (`extra`) lose a
    `team`, `reports_to` or `operator` naming one."""
    nobody = "(SELECT NULL WHERE 0)"
    actors, ids, teams = (
        qlist(people) if people else nobody,
        qlist(bare) if bare else nobody,
        qlist(groups) if groups else nobody,
    )
    return {
        "extra": (
            "CASE WHEN source.extra IS NULL OR NOT json_valid(source.extra) THEN source.extra "
            "ELSE json_patch(source.extra, (SELECT json_group_object(key, NULL) FROM json_each(source.extra) "
            f"WHERE (key='team' AND value IN {teams}) OR (key IN ('reports_to','operator') "
            f"AND (value IN {ids} OR value IN {actors})))) END"
        )
    }


def unsettled_watched(c, inner):
    """The ids of the live runs of bots org rules apply to, as a subquery over the inner attempts view, or "" when no
    rule watches a bot: their reply text stays out until it is checked and delivered, as their room, steps, log, events
    and capture keep it (backend/org_rule_check.py hides_run). A late result filed against an expired run included."""
    from .org_rule_check import UNSETTLED, watched_bots

    watched = watched_bots(c)
    return (
        f"SELECT id FROM {inner('attempts')} WHERE bot_id IN {qlist(watched)} AND state IN {qlist(UNSETTLED)}"
        if watched
        else ""
    )


def live_masks(unsettled):
    """MASKED for one request: a watched live run's reply-bearing columns read NULL (its result's `text` alone is
    removed, so the rest of the result stays readable); every other column of the run reads as it is."""
    if not unsettled:
        return {}
    hidden = lambda column: f"CASE WHEN source.id IN ({unsettled}) THEN NULL ELSE source.{column} END"
    return {
        "attempts": {
            "final_text": hidden("final_text"),
            "result": f"CASE WHEN source.id NOT IN ({unsettled}) THEN source.result "
            "WHEN json_valid(source.result) THEN json_remove(source.result,'$.text') "
            "ELSE NULL END",
            "summary": hidden("summary"),
        }
    }


# A person's own act on a task, written as the question engine takes a reply from them (question_marks.REPLY_VIA:
# their own hand, Slack, their personal token, their Assistant on their word): a row of its history, or a message they
# wrote on the task the server says it names (hubdb.refs_task_id: its refs' task or task_id, a list read as its first
# string that is not blank), else the task of the message it replies to (the same way, then that one's chat), else its
# own chat's task. BotOps or another agent acting with their rights is not theirs, and Tico's carrier copy of a message
# is not a second touch.
_OWN_VIA = "(" + ", ".join(f"'{via or ''}'" for via in question_marks.REPLY_VIA if via is not None) + ")"
# What Python's str.strip() takes off (hubdb._task_ref): every Unicode space.
_BLANKS = "char(" + ", ".join(str(n) for n in range(0x3001) if chr(n).isspace()) + ")"


def _ref_sql(alias: str, key: str) -> str:
    """The SQL twin of hubdb._task_ref over `alias`.refs at `key`: a string, trimmed, or the first string of a list that
    is not blank; nothing else names a task."""
    path = f"$.{key}"
    return (
        f"CASE json_type({alias}.refs, '{path}') "
        f"WHEN 'text' THEN nullif(trim(json_extract({alias}.refs, '{path}'), {_BLANKS}), '') "
        f"WHEN 'array' THEN (SELECT trim(value, {_BLANKS}) FROM json_each({alias}.refs, '{path}') "
        f"WHERE type = 'text' AND trim(value, {_BLANKS}) <> '' ORDER BY key LIMIT 1) END"
    )


def _refs_task_sql(alias: str) -> str:
    """hubdb.refs_task_id in SQL: `refs.task`, else `refs.task_id`."""
    return f"coalesce({_ref_sql(alias, 'task')}, {_ref_sql(alias, 'task_id')})"


_MESSAGE_TASK = (
    f"coalesce({_refs_task_sql('m')}, (SELECT coalesce({_refs_task_sql('q')}, qc.task_id) "
    "FROM {messages} q LEFT JOIN {conversations} qc ON qc.id = q.conversation_id WHERE q.id = m.in_reply_to), "
    "cv.task_id)"
)
_TOUCHES = (
    "SELECT created_at AS at, subject_id AS task_id, actor_id AS person_id, kind FROM {history_events} "
    f"WHERE subject_type = 'task' AND actor_id LIKE 'human:%' AND coalesce(via, '') IN {_OWN_VIA} "
    f"UNION ALL SELECT m.created_at, {_MESSAGE_TASK}, m.sender_id, 'message' FROM {{messages}} m "
    "LEFT JOIN {conversations} cv ON cv.id = m.conversation_id WHERE m.sender_id LIKE 'human:%' "
    f"AND coalesce(json_extract(m.refs, '$.via'), '') IN {_OWN_VIA}"
)
# The history kinds that are a person deciding something about a task (one row each: a move's owner and status rows are
# its consequences, not more decisions, and so are the move back that taking an answer back makes, `undone_answer`,
# and what an act makes by itself, `consequence`: a snooze an answer, a done, a reply or the end of what a task filed
# ends, and the close of the tasks under one that closes).
_DECISIONS = (
    "('answer', 'answer_undone', 'give', 'move', 'snooze', 'unsnooze', 'reassign', 'try_again', 'requirement', "
    "'requirement_withdrawn')"
)
# An agent's action: its row in the audit log, less what run_triggers.BOOKKEEPING says changes nothing anyone else sees
# (reads, SQL, tool-call stats, refusals, its own status and run bookkeeping). The actor is a range, not a LIKE, so the
# rows come from the actor index (events_actor_action_created) and the per-row read check runs on agents' rows only.
_AGENT_ACTION = (
    f"actor >= 'bot:' AND actor < 'bot;' AND action NOT IN {qlist(sorted(RT.BOOKKEEPING))} AND "
    + " AND ".join(f"action NOT LIKE '{prefix}%'" for prefix in RT.BOOKKEEPING_PREFIXES)
)
_WEEK = "date({at}, '-6 days', 'weekday 1')"
# Whether the answer in history row `{row}` was taken back (answers.undo writes `answer_undone` naming it).
_UNDONE = (
    "EXISTS (SELECT 1 FROM {{history_events}} u WHERE u.subject_type = 'task' AND u.subject_id = {row}.subject_id "
    "AND u.kind = 'answer_undone' AND json_extract(u.data, '$.answer_id') = json_extract({row}.data, '$.answer_id'))"
)


# Product's Validate stages (validate_gate.STAGES): an agent's OK there waits for its page to pass before it is asked.
def _validate(stage: str) -> str:
    return (
        f"({stage} = 'product-validate' OR ({stage} LIKE 'product-validate-%' AND substr({stage}, 18) <> '' "
        f"AND substr({stage}, 18) NOT GLOB '*[^0-9]*'))"
    )


# Each person's answer to an OK or a page that was not taken back: its task, requirement, person, time and row.
_ANSWERS = (
    "SELECT a.subject_id AS task_id, json_extract(a.data, '$.requirement') AS requirement, a.actor_id AS person, "
    "a.created_at, a.id FROM {history_events} a WHERE a.subject_type = 'task' AND a.kind = 'answer' "
    "AND a.new IN ('ok', 'no') AND a.actor_id LIKE 'human:%' AND NOT " + _UNDONE.format(row="a")
)


# Read-only views built over the caller's connection: name -> (base tables it reads, SELECT over `{table}`, the
# caller's own filtered view of each, and `{all_table}`, its unfiltered inner view). A total adds up only rows the
# caller may read: a bucket of one run would otherwise tell them that run exists, what it cost and whether it acted.
# `{all_table}` may only narrow what is counted, never add a row.
DERIVED = {
    # Runs per week (Monday, UTC), bot, trigger and purpose, with tokens and outcome (backend/run_triggers.py).
    # A run from before triggers were recorded reads `unknown`, and is counted, never dropped. Input is the run's
    # measured usage (every primary and fallback segment). A run from before measured usage has its one-number
    # count in input_tokens (migration 76). An attempt with no run record (a job and no `trigger`: one that failed or
    # expired before it started) is counted too, as `unknown`, in the week it started, with no tokens or outcome.
    "run_weekly": (
        ("attempts",),
        """
        SELECT date(started_at, '-6 days', 'weekday 1') AS week, bot, coalesce(trigger_kind, 'unknown') AS trigger,
               coalesce(purpose, 'unknown') AS purpose,
               count(*) AS runs,
               sum(coalesce(input_tokens, 0) + coalesce(cached_tokens, 0) + coalesce(cache_write_tokens, 0))
                 AS input_tokens,
               sum(coalesce(cached_tokens, 0)) AS cached_input_tokens,
               sum(coalesce(output_tokens, 0)) AS output_tokens,
               sum(coalesce(outcome, '') = 'acted') AS acted,
               sum(coalesce(outcome, '') = 'no_change') AS no_change,
               sum(coalesce(outcome, '') = 'nothing_to_do') AS nothing_to_do,
               sum(outcome IS NULL) AS unknown,
               sum(coalesce(did_nothing, 0) = 1) AS did_nothing,
               sum(rotated IS NOT NULL) AS rotated,
               round(100.0 * sum(coalesce(outcome, '') = 'acted') / count(*), 1) AS acted_pct,
               round(100.0 * sum(coalesce(outcome, '') = 'no_change') / count(*), 1) AS no_change_pct,
               round(100.0 * sum(coalesce(outcome, '') = 'nothing_to_do') / count(*), 1) AS nothing_to_do_pct,
               round(100.0 * sum(outcome IS NULL) / count(*), 1) AS unknown_pct
        FROM (SELECT CASE WHEN trigger IS NULL THEN coalesce(started_at, created_at) ELSE created_at END AS started_at,
                     bot_id AS bot, trigger_kind, purpose, input_tokens, cached_tokens, cache_write_tokens,
                     output_tokens,
                     outcome, did_nothing, rotated
              FROM {attempts})
        GROUP BY 1, 2, 3, 4""",
    ),
    # Runs per week, bot and wake rule (backend/rules.py): the rule that woke the run, with its tokens and how many did
    # nothing. A run from before rules, or whose message carried none, reads `unknown`. Same token sums as run_weekly.
    "rule_weekly": (
        ("attempts",),
        """
        SELECT date(created_at, '-6 days', 'weekday 1') AS week, bot_id AS bot, coalesce(rule_id, 'unknown') AS rule,
               count(*) AS runs,
               sum(coalesce(input_tokens, 0) + coalesce(cached_tokens, 0) + coalesce(cache_write_tokens, 0))
                 AS input_tokens,
               sum(coalesce(cached_tokens, 0)) AS cached_input_tokens,
               sum(coalesce(output_tokens, 0)) AS output_tokens,
               sum(coalesce(outcome, '') = 'acted') AS acted,
               sum(coalesce(did_nothing, 0) = 1) AS did_nothing,
               round(100.0 * sum(coalesce(did_nothing, 0) = 1) / count(*), 1) AS did_nothing_pct
        FROM {attempts} WHERE (job_id IS NULL OR trigger IS NOT NULL)
        GROUP BY 1, 2, 3""",
    ),
    # Each touch a person made on a task (_TOUCHES): its time and week, the task, the person and what they did (a
    # history kind such as answer or move, or `message`).
    "human_touches_v": (
        ("history_events", "messages", "conversations", "tasks"),
        "SELECT at, " + _WEEK.format(at="at") + " AS week, task_id, person_id, kind FROM (" + _TOUCHES + ") "
        "WHERE task_id IN (SELECT id FROM {tasks})",
    ),
    # Per week: people's decisions on tasks (_DECISIONS), the agents' actions (_AGENT_ACTION) and decisions per 1,000 of
    # those actions. Both count only rows the reader may read: an agent reads its own actions alone.
    "human_decisions_weekly": (
        ("history_events", "events"),
        "SELECT week, sum(decision) AS decisions, sum(1 - decision) AS agent_actions, "
        "round(1000.0 * sum(decision) / nullif(sum(1 - decision), 0), 1) AS per_1000_actions "
        "FROM (SELECT " + _WEEK.format(at="created_at") + " AS week, 1 AS decision FROM {history_events} "
        f"WHERE subject_type = 'task' AND actor_id LIKE 'human:%' AND coalesce(via, '') IN {_OWN_VIA} "
        "AND kind IN " + _DECISIONS + " AND json_extract(data, '$.undone_answer') IS NULL "
        "AND json_extract(data, '$.consequence') IS NULL "
        "UNION ALL SELECT " + _WEEK.format(at="created_at") + ", 0 FROM {events} WHERE " + _AGENT_ACTION + ") "
        "GROUP BY week",
    ),
    # Per week: the Assistant's turns on a person's message, and the 50th and 90th percentile milliseconds from the
    # person's send to its first words (attempts.result timings: send_to_claim_ms plus the runner's first_text mark).
    "first_text_weekly": (
        ("attempts",),
        "SELECT week, count(*) AS turns, max(CASE WHEN n = (total + 1) / 2 THEN ms END) AS p50_ms, "
        "max(CASE WHEN n = (total * 9 + 9) / 10 THEN ms END) AS p90_ms "
        "FROM (SELECT week, ms, row_number() OVER (PARTITION BY week ORDER BY ms) AS n, "
        "count(*) OVER (PARTITION BY week) AS total "
        "FROM (SELECT " + _WEEK.format(at="created_at") + " AS week, "
        "CAST(json_extract(result, '$.timings.send_to_claim_ms') + json_extract(result, '$.timings.phases.first_text') "
        "AS INTEGER) AS ms FROM {attempts} WHERE bot_id = {assistant} AND trigger_kind = 'person_message' "
        "AND json_extract(result, '$.timings.phases.first_text') IS NOT NULL "
        "AND json_extract(result, '$.timings.send_to_claim_ms') IS NOT NULL)) "
        "GROUP BY week",
    ),
    # Per week: people's decisions on an OK or a page, one for each time it was asked of them (answers to the same ask,
    # a revised OK with new notes, are one; an answer taken back is none), and the repeats: a decision on an ask asked
    # of them again after a recorded re-ask (the owner saying done again after a send back, or the task moving, which
    # makes a new visit), written before the decision. Keyed by the requirement, never by later waits: each approver of a page several people approve
    # decides it once. A ❓ is a new ask each time, so it is not in it.
    "repeat_ask_weekly": (
        ("history_events",),
        "WITH answered AS (" + _ANSWERS + "), "
        "decisions AS (SELECT task_id, requirement, person, occurrence, min(created_at) AS at FROM ("
        "SELECT a.*, (SELECT count(*) FROM {history_events} h WHERE h.subject_type = 'task' "
        "AND h.subject_id = a.task_id AND (h.kind = 'move' OR (h.kind = 'answer' AND h.new = 'again' "
        "AND json_extract(h.data, '$.requirement') = a.requirement)) AND h.created_at < a.created_at) "
        "AS occurrence FROM answered a) GROUP BY task_id, requirement, person, occurrence) "
        "SELECT week, count(*) AS answers, sum(repeated) AS repeats, "
        "round(100.0 * sum(repeated) / count(*), 1) AS repeat_pct "
        "FROM (SELECT " + _WEEK.format(at="at") + " AS week, "
        "occurrence > min(occurrence) OVER (PARTITION BY task_id, requirement, person) AS repeated FROM decisions) "
        "GROUP BY week",
    ),
    # Per week: OKs and pages added to one task and asked of a person (not in Product's Validate, by the stage of the visit
    # the ask was added on: there an agent's OK waits for its page first) that were answered, and the median seconds
    # from the ask, or its newest give to someone before the answer, to its first answer not taken back (the mean of the
    # middle two for an even count). A stage's own OKs and pages, and ❓ questions, are not in it.
    "ask_answer_weekly": (
        ("history_events", "requirements", "visits"),
        # Subqueries, not WITH: the inner requirements and visits are read only from this view itself (connect's
        # authorizer names the view a read is made in, and a WITH clause's name is not this view's).
        "SELECT week, count(*) AS answers, "
        "round(avg(CASE WHEN n IN ((total + 1) / 2, (total + 2) / 2) THEN secs END), 3) AS median_s "
        "FROM (SELECT week, secs, row_number() OVER (PARTITION BY week ORDER BY secs) AS n, "
        "count(*) OVER (PARTITION BY week) AS total FROM (SELECT " + _WEEK.format(at="answered") + " AS week, "
        "(julianday(answered) - julianday(asked)) * 86400 AS secs FROM ("
        "SELECT f.answered, max(s.asked, coalesce(max(g.created_at), '')) AS asked "
        "FROM (SELECT r.subject_id AS task_id, r.new AS requirement, r.created_at AS asked "
        "FROM {history_events} r JOIN {all_requirements} q ON q.id = r.new JOIN {all_visits} v ON v.id = q.visit_id "
        "WHERE r.subject_type = 'task' AND r.kind = 'requirement' AND json_extract(r.data, '$.person') LIKE 'human:%' "
        "AND NOT " + _validate("v.stage_id") + ") s "
        "JOIN (SELECT task_id, requirement, min(created_at) AS answered FROM (" + _ANSWERS + ") "
        "GROUP BY task_id, requirement) f ON f.task_id = s.task_id AND f.requirement = s.requirement "
        "LEFT JOIN (SELECT subject_id AS task_id, json_extract(data, '$.requirement') AS requirement, created_at "
        "FROM {history_events} WHERE subject_type = 'task' AND kind = 'give') g "
        "ON g.task_id = s.task_id AND g.requirement = s.requirement AND g.created_at < f.answered "
        "GROUP BY s.task_id, s.requirement))) GROUP BY week",
    ),
}
# Totals over rows the reader may not read one by one, built only for the owner and a bot the owner granted them
# (backend/outcome_stats.py). Each groups by week and fixed names (status, proposer, bot, tool) and carries no text, no
# id and no person: a proposal's owner, a call's person and its token label never leave the base row.
GRANTED = {
    # Assistant and BotOps proposals per week, proposer and status (pending, running, done, failed, canceled,
    # expired); `share_pct` is that status's share of its week and proposer.
    "assistant_action_weekly": (
        ("assistant_actions",),
        """
        SELECT week, proposer, status, actions,
               round(100.0 * actions / sum(actions) OVER (PARTITION BY week, proposer), 1) AS share_pct
        FROM (SELECT date(created_at, '-6 days', 'weekday 1') AS week,
                     coalesce(proposer, proposed_via, 'assistant') AS proposer,
                     status, count(status) AS actions
              FROM {all_assistant_actions} GROUP BY 1, 2, 3)""",
    ),
    # Bot tool calls through MCP per week, by tool and by bot when the reader may read that bot; hidden bots count
    # with no `bot`. People's calls are excluded. A name that is not a Tico tool's counts as `other`.
    "mcp_call_weekly": (
        ("events", "bots"),
        """
        SELECT week, bot, tool, count(*) AS calls, sum(error) AS errors,
               round(100.0 * sum(error) / count(*), 1) AS error_pct,
               CAST(round(avg(ms)) AS INTEGER) AS avg_ms, max(ms) AS max_ms
        FROM (SELECT date(created_at, '-6 days', 'weekday 1') AS week,
                     CASE WHEN actor LIKE 'bot:%' AND substr(actor, 5) IN (SELECT id FROM {bots})
                          THEN substr(actor, 5) END AS bot,
                     CASE WHEN target IN {tool_names} THEN target ELSE 'other' END AS tool,
                     coalesce(json_extract(detail, '$.error'), 0) <> 0 AS error,
                     coalesce(json_extract(detail, '$.ms'), 0) AS ms
              FROM {all_events} WHERE action = 'mcp.call' AND actor LIKE 'bot:%')
        GROUP BY 1, 2, 3""",
    ),
}
# Base tables only a saved view (DERIVED or GRANTED) reads, and the columns it may see of them. ask_answer_weekly reads
# an ask's requirement and visit only by the id a history row the reader may read names, for the stage it was added in.
GRANTED_SOURCES = {
    "assistant_actions": ("created_at", "proposer", "proposed_via", "status"),
    "requirements": ("id", "visit_id"),
    "visits": ("id", "stage_id"),
}
# FTS5 reads these as main.<virtual>_* with no view context; they are not a public API.
FTS_SHADOWS = ("_data", "_idx", "_content", "_docsize", "_config")
STATEMENT = re.compile(r"^\s*(?:(?:--[^\n]*\n?|/\*.*?\*/)\s*)*(select|with|explain\s+query\s+plan)\b", re.I | re.S)
_SQL_TOKEN = re.compile(
    r"""
      --[^\n]*
    | /\*.*?\*/
    | '(?:''|[^'])*'
    | "(?:""|[^"])*"
    | `(?:``|[^`])*`
    | \[[^\]]*\]
    | [A-Za-z_][A-Za-z0-9_]*
    """,
    re.S | re.X,
)

# Per-person burst-tolerant rate limit for POST /api/v2/sql (120 burst, 2/s refill -> 72-query burst passes freely).
SQL_RATE_BURST = 120.0
SQL_RATE_REFILL = 2.0
SQL_POOL_IDLE = 16  # read-only connections kept open between POST /api/v2/sql calls

# Guarded tables whose row predicates or masks read other guarded views (transitive closure built per query).
_TABLE_DEPS: dict[str, tuple[str, ...]] = {
    "conversations": ("tasks",),
    READABLE_MESSAGES: ("conversations",),
    "messages": (READABLE_MESSAGES,),
    "thread_members": ("conversations",),
    "thread_followers": ("conversations",),
    "message_mentions": (READABLE_MESSAGES,),
    "task_tags": ("tasks",),
    "task_delegations": ("tasks",),
    "task_requesters": ("tasks",),
    "task_reminders": ("tasks",),
    "task_assets": ("tasks",),
    "task_documents": ("tasks", "bot_files"),
    "task_links": ("tasks",),
    "task_roles": ("tasks",),
    "task_relations": ("tasks",),
    "task_wait_spans": ("tasks",),
    "goal_asks": ("tasks",),
    "message_assets": (READABLE_MESSAGES,),
    "bot_control": ("tasks",),
    "bot_status": ("tasks",),
    "bot_status_history": ("tasks",),
    "schedule_config": ("schedules",),
    "schedule_occurrences": ("schedules", "tasks"),
    "deltas": ("attempts",),
    "jobs": (READABLE_MESSAGES,),
    "attempts": (READABLE_MESSAGES, "jobs", "tasks"),
    "job_recovery": ("jobs", "attempts"),
    "attempt_events": ("attempts",),
    "attempt_inputs": ("attempts",),
    "attempt_conversations": ("attempts",),
    "bot_transition_checkpoints": ("conversations",),
    "kpi_readings": ("kpis",),
    "kpi_definitions": ("kpis",),
    "goal_kpis": ("goals",),
    "goal_checkins": ("goals",),
    "goal_proposals": ("goals",),
    "history_events": ("tasks", "goals"),
    "events": ("tasks", "conversations", "bots"),
    "meeting_versions": ("meetings",),
    "meeting_deliveries": ("meetings",),
    "media_assets": ("meetings",),
    "media_control": ("meetings",),
    "meeting_items": ("meetings",),
    "meeting_brain": ("meetings",),
    "meeting_comments": ("meetings",),
    "import_refs": ("meetings",),
    "doc_versions": ("docs",),
    "bot_files": ("tasks", "conversations"),
    "bot_file_versions": ("bot_files", "attempts"),
    "bot_file_activity": ("bot_files", "tasks", "attempts"),
    "blobs": ("message_assets", "task_assets", "media_assets"),
    "task_file_reviews": ("bot_files", "blobs"),
    "slack_posts": (READABLE_MESSAGES,),
}

# Every guarded table name exposed as a temp view (kept in sync with `guarded`).
GUARDED_TABLES = frozenset(
    {
        "conversations",
        "messages",
        "thread_members",
        "thread_followers",
        "message_mentions",
        "tasks",
        "tags",
        "task_tags",
        "task_delegations",
        "task_requesters",
        "task_reminders",
        "task_assets",
        "task_documents",
        "task_links",
        "task_roles",
        "task_relations",
        "task_wait_spans",
        "message_assets",
        "bots",
        "bot_status",
        "bot_status_history",
        "schedules",
        "bot_config",
        "bot_settings",
        "bot_control",
        "bot_transitions",
        "assignments",
        "schedule_config",
        "schedule_occurrences",
        "deltas",
        "jobs",
        "attempts",
        "job_recovery",
        "attempt_events",
        "attempt_inputs",
        "attempt_conversations",
        "bot_transition_checkpoints",
        "humans",
        "seats",
        "goals",
        "kpis",
        "kpi_readings",
        "kpi_definitions",
        "goal_kpis",
        "goal_checkins",
        "goal_proposals",
        "goal_asks",
        "company_profile",
        "strategy_versions",
        "strategy_bets",
        "lens_runs",
        "entities",
        "entity_links",
        "market_evidence",
        "market_citations",
        "market_insights",
        "entity_watches",
        "market_briefings",
        "history_events",
        "market_sizes",
        "market_figures",
        "market_sources",
        "listening_sources",
        "listening_items",
        "registry_metadata",
        "events",
        "refusals",
        "meetings",
        "meeting_versions",
        "meeting_deliveries",
        "media_assets",
        "media_control",
        "meeting_items",
        "meeting_brain",
        "meeting_comments",
        "import_refs",
        "market_pages",
        "docs",
        "doc_versions",
        "linked_docs",
        "bot_files",
        "bot_file_versions",
        "bot_file_activity",
        "blobs",
        "archives",
        "service_jobs",
        "slack_posts",
        "read_index",
        "task_file_reviews",
        *COMPANY_PAGE_OWNER_TABLES,
    }
)
# Tables whose predicates or teammate masks consult TA.hidden(..., ("human", "group")).
_UNSEEN_TABLES = frozenset(
    {
        "humans",
        "thread_members",
        "thread_followers",
        "tasks",
        "task_wait_spans",
        "history_events",
        "events",
        "messages",
        "message_mentions",
        "bot_config",
        "bot_settings",
        "conversations",
    }
)
# Tables whose predicates consult auth.unreadable_bots.
_BOT_VISIBILITY_TABLES = frozenset(
    {
        "tasks",
        "bots",
        "bot_status",
        "bot_status_history",
        "schedules",
        "bot_config",
        "bot_settings",
        "bot_control",
        "bot_transitions",
        "assignments",
        "jobs",
        "attempts",
        "goals",
        "kpis",
        "bot_files",
    }
)
# Privacy UDFs backed by ReadIndex (others only read `source`).
_INDEX_FUNCTIONS = frozenset(
    {"message", "attempt", "conversation", "meeting_room", "event", "tag", "blob", "task", "content"}
)
_ALL_EXPOSED = GUARDED_TABLES | set(DERIVED) | set(GRANTED) | {"task_waits"}


def referenced_names(statement: str, known: frozenset[str] = _ALL_EXPOSED) -> set[str]:
    """Identifiers in `statement` matching `known` table/view names; skips SQL comments and string literals."""
    found = set()
    for match in _SQL_TOKEN.finditer(statement):
        tok = match.group(0)
        head = tok[0]
        if head in ("-", "/"):
            continue
        if head == "'":
            # SQLite also takes a single-quoted string as a table name; building a view it never reads is harmless.
            name = tok[1:-1].replace("''", "'").lower()
            if name in known:
                found.add(name)
            continue
        if head == '"':
            name = tok[1:-1].replace('""', '"').lower()
        elif head == "`":
            name = tok[1:-1].replace("``", "`").lower()
        elif head == "[":
            name = tok[1:-1].lower()
        else:
            name = tok.lower()
        if name in known:
            found.add(name)
    return found


def _expand_tables(seeds: set[str], role: str) -> set[str]:
    """Transitive closure of guarded tables needed to serve `seeds`."""
    needed = set(seeds)
    stack = list(seeds)
    while stack:
        table = stack.pop()
        if table == "events" and role in ("owner", "bot"):
            deps: tuple[str, ...] = ("tasks",)
        else:
            deps = _TABLE_DEPS.get(table, ())
        for dep in deps:
            if dep not in needed:
                needed.add(dep)
                stack.append(dep)
    return needed


class TokenBucket:
    """Per-actor burst-tolerant token bucket for POST /api/v2/sql."""

    def __init__(self, burst: float = SQL_RATE_BURST, refill: float = SQL_RATE_REFILL):
        self.burst = float(burst)
        self.refill = float(refill)
        self.lock = threading.Lock()
        self.buckets: dict[str, tuple[float, float]] = {}

    def take(self, actor: str) -> None:
        if self.burst <= 0 or self.refill <= 0:
            return
        with self.lock:
            now = time.monotonic()
            tokens, last = self.buckets.get(actor, (self.burst, now))
            tokens = min(self.burst, tokens + max(0.0, now - last) * self.refill)
            if tokens < 1.0:
                wait = max(1, math.ceil((1.0 - tokens) / self.refill))
                self.buckets[actor] = (tokens, now)
                raise Problem(
                    "rate_limited",
                    f"Too many SQL queries; retry in {wait} s.",
                    429,
                    retryable=True,
                    extra={"retry_after": wait},
                )
            self.buckets[actor] = (tokens - 1.0, now)
            if len(self.buckets) > 256:
                full_after = self.burst / self.refill
                for key in [k for k, (_, ts) in self.buckets.items() if now - ts >= full_after]:
                    del self.buckets[key]


_SCHEMAS = OrderedDict()
_SCHEMA_LOCK = threading.Lock()


def schema_columns(conn, path):
    """Only schema metadata is shared. DDL and a restored/replaced file invalidate it."""
    path = Path(path).resolve()
    info = path.stat()
    version = conn.execute("PRAGMA main.schema_version").fetchone()[0]
    key = (str(path), info.st_dev, info.st_ino, version)
    with _SCHEMA_LOCK:
        entry = _SCHEMAS.setdefault(key, {"columns": {}, "objects": None})
        _SCHEMAS.move_to_end(key)
        if len(_SCHEMAS) > 16:
            _SCHEMAS.popitem(last=False)
    columns = entry["columns"]

    class Columns:
        def get(self, table, default=()):
            # Load only exposed tables; hidden/internal tables need no view metadata.
            with _SCHEMA_LOCK:
                if table not in columns:
                    columns[table] = tuple(
                        row[1] for row in conn.execute('PRAGMA main.table_info("' + table.replace('"', '""') + '")')
                    )
                return columns[table] or default

        def objects(self):
            with _SCHEMA_LOCK:
                if entry["objects"] is None:
                    entry["objects"] = frozenset(row[0] for row in conn.execute("SELECT name FROM sqlite_master"))
                return entry["objects"]

    return Columns()


class Audit:
    """Share a commit among callers queued behind the current audit writer.

    Each request waits for its own durable event. There is no background worker,
    fire-and-forget audit or lock held during query evaluation.
    """

    def __init__(self, store):
        self.store, self.pending = store, []
        self.condition, self.writing = threading.Condition(), False

    def record(self, actor, detail):
        entry = {"actor": actor, "detail": detail, "context": copy_context(), "done": False, "error": None}
        with self.condition:
            self.pending.append(entry)
            self.condition.notify_all()
            while not entry["done"]:
                if self.writing:
                    self.condition.wait()
                    continue
                batch, self.pending, self.writing = self.pending, [], True
                self.condition.release()
                error = None
                try:
                    with self.store.transaction() as c:
                        for item in batch:
                            item["context"].run(H.event, c, item["actor"], "sql.query", "", item["detail"])
                except BaseException as exc:
                    error = exc
                finally:
                    self.condition.acquire()
                    for item in batch:
                        item.update(done=True, error=error)
                    self.writing = False
                    self.condition.notify_all()
            if entry["error"] is not None:
                raise entry["error"]


class ReadConnection(sqlite3.Connection):
    # Empties the request's provenance caches (ReadIndex.release) when the connection closes or returns to its pool.
    release: Callable[[], None] | None = None
    read_cache: dict
    private_prefix: str = ""
    _fn_prefix: str = ""
    _pool: "SqlPool | None" = None
    _pool_identity: tuple | None = None
    _tainted: bool = False
    _registered_functions: list

    def _real_close(self):
        super().close()

    def close(self):
        release, self.release = self.release, None
        try:
            if release is not None:
                release()
            if hasattr(self, "read_cache"):
                self.read_cache.clear()
        finally:
            pool = self._pool
            if pool is not None:
                pool.give(self)
            else:
                super().close()


class SqlPool:
    """Small pool of read-only SQLite connections for POST /api/v2/sql so repeated queries reuse the parsed schema."""

    def __init__(self, max_idle: int = SQL_POOL_IDLE):
        self.max_idle = max_idle
        self.lock = threading.Lock()
        self.idle: list[ReadConnection] = []
        self.identity: tuple | None = None
        self.closed = False

    def take(self, path, pool_age: int = 0) -> ReadConnection:
        resolved = Path(path).resolve()
        info = resolved.stat()
        identity = (str(resolved), info.st_dev, info.st_ino, pool_age)
        with self.lock:
            if identity != self.identity:
                stale = list(self.idle)
                self.idle.clear()
                self.identity = identity
            else:
                stale = []
            conn = self.idle.pop() if self.idle and not self.closed else None
        for old in stale:
            try:
                old._real_close()
            except sqlite3.Error:
                pass
        if conn is None:
            conn = _open_ro(path)
            conn._pool = self
            conn._pool_identity = identity
        conn.read_cache = {}
        conn.private_prefix = ""
        conn.release = None
        conn._tainted = False
        conn._registered_functions = []
        return conn

    def give(self, conn: ReadConnection) -> None:
        if conn._tainted or self.closed:
            try:
                conn._real_close()
            except sqlite3.Error:
                pass
            return
        try:
            conn.set_authorizer(None)
            conn.set_progress_handler(None, 0)
            conn.set_trace_callback(None)
            conn.execute("PRAGMA query_only=0")
            if conn.in_transaction:
                # Commit temp-only DDL so SQLite keeps main's parsed schema (ROLLBACK after temp DDL resets all
                # schemas on the connection). The file is opened mode=ro, so main can never be modified.
                conn.execute("COMMIT")
            for typ, name in conn.execute(
                "SELECT type, name FROM sqlite_temp_master WHERE type IN ('view', 'table')"
            ).fetchall():
                conn.execute(f'DROP {typ.upper()} temp."{name.replace(chr(34), chr(34) * 2)}"')
            for fn_name, arity in conn._registered_functions:
                conn.create_function(fn_name, arity, None, deterministic=True)
            conn._registered_functions.clear()
            conn.row_factory = sqlite3.Row
            conn.private_prefix = ""
        except sqlite3.Error:
            try:
                conn._real_close()
            except sqlite3.Error:
                pass
            return
        with self.lock:
            if not self.closed and conn._pool_identity == self.identity and len(self.idle) < self.max_idle:
                self.idle.append(conn)
                return
        try:
            conn._real_close()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self.lock:
            self.closed = True
            stale = list(self.idle)
            self.idle.clear()
        for conn in stale:
            try:
                conn._real_close()
            except sqlite3.Error:
                pass


def _open_ro(path) -> ReadConnection:
    # Internal provenance SELECTs cannot leave authorized prepared statements for callers.
    conn = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=5,
        isolation_level=None,
        cached_statements=0,
        check_same_thread=False,
        factory=ReadConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.read_cache = {}
    conn.private_prefix = ""
    # Fixed per-connection secret prefix for UDF names so SQLite's function hash table stays bounded across reuses.
    conn._fn_prefix = "f" + secrets.token_hex(12) + "_"
    conn._registered_functions = []
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 10_000_000)  # no randomblob(1e9) memory bombs
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.execute("PRAGMA busy_timeout=5000")
    # Older SQLite versions initialize JSON virtual tables with schema authorization calls.
    # Initialize only these built-ins once before any read-only authorizer runs.
    for fn in sorted(TABLE_FUNCTIONS):
        conn.execute(f"SELECT value FROM {fn}('[]')").fetchall()
    return conn


class Query(Contract):
    sql: str = Field(min_length=1, max_length=20_000)
    params: list | dict = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=1, le=5000)


def guarded(c, auth, who, inner, function, columns=None, unseen=None, private=None, only=None):
    """Every table a caller may read through a view: name -> (row predicate, hidden columns).

    The predicates say in SQL what `auth.bot_access`, `auth.conversation`, `auth.task` and
    `auth.approval` say in Python. A bot is in the SQL layer for a caller who may read it (a bot
    they may only see or write to is not; the API shows it); `inner(table)` names the connection's unfiltered view of a
    base table, and a plain table name inside a predicate is the filtered view of it. `private(name)`, when given, names
    this request's sets (materialize): the room checks then ask Python only about the few rooms with meeting provenance.
    When `only` is given (a set of table names), only predicates for those tables are built and unused lookups are skipped.
    """
    want = lambda name: only is None or name in only
    me, owner, bot = q(who.actor), who.role == "owner", who.role == "bot"
    # People and groups the caller may not see (backend/teammate_access.py): not in `humans`, and not named as authors
    # or recipients of messages outside the conversations they are in, nor in a participant or group list
    # (teammate_masks).
    if only is None or "humans" in only:
        unseen = TA.hidden(c, who, "see", ("human", "group"), auth) if unseen is None else unseen
    hidden = sorted(auth.unreadable_bots(c, who)) if only is None or (only & _BOT_VISIBILITY_TABLES) else []

    def bots_visible(column):
        return f"{column} NOT IN {qlist(hidden)}" if hidden else "1"

    # Who reads a thread is C1 (backend/threads.py), the same rule Auth.conversation asks in Python.
    conversations = "0"
    legacy_messages = "1"
    if want("conversations") or want(READABLE_MESSAGES):
        from . import threads

        viewer = threads.Viewer(auth, who)
        if want("conversations"):
            if private is None:
                meeting = lambda cid: function("meeting_room") + f"({cid})"
                conversation = lambda cid: function("conversation") + f"({cid})"
            else:
                # ReadIndex.meeting_room is no for a room outside `bound_rooms`, and ReadIndex.conversation yes for one outside
                # `meeting_rooms` (rooms=True): only those few rooms reach Python, not one callback per room of the company.
                # CASE, not AND/OR: SQLite may evaluate both sides of a boolean operator, the Python callback included.
                meeting = lambda cid: (
                    f'(CASE WHEN {cid} IN (SELECT id FROM "{private("bound_rooms")}") '
                    f'THEN {function("meeting_room")}({cid}) ELSE 0 END)'
                )
                conversation = lambda cid: (
                    f'(CASE WHEN {cid} IN (SELECT id FROM "{private("meeting_rooms")}") '
                    f'THEN {function("conversation")}({cid}) ELSE 1 END)'
                )
            conversations = threads.readable_sql(
                c, viewer, "", inner=inner, tasks="SELECT id FROM tasks", meeting=meeting, conversation=conversation
            )
        if want(READABLE_MESSAGES):
            # A message migrated from a shared bot room keeps that room's readers (threads.message_sql).
            legacy_messages = threads.message_sql(c, viewer, "", inner=inner)
    tasks = auth.task_sql(c, who, delegations=inner("task_delegations"), unreadable=hidden) if want("tasks") else "0"
    # Evaluate provenance only for candidate rows, never scan unrelated content first.
    msg_gate = function("message") + "(id,conversation_id,refs,in_reply_to)"
    if want(READABLE_MESSAGES):
        message_columns = (
            set(columns.get("messages", ()))
            if columns is not None
            else {r[1] for r in c.execute("PRAGMA table_info(messages)")}
        )
        for deleted in ("deleted_at", "deleted"):
            if deleted in message_columns:
                msg_gate += f" AND coalesce({deleted},0)=0"
    attempt_gate = function("attempt") + "(id)"
    event_gate = function("event") + "(target,actor,created_at,detail)"
    content_gate = lambda cols: function("content") + "(" + ",".join(cols) + ")"
    # A template or a risk lists whatever tasks carry it (task_privacy._listed_alone); a risk, as the API lists it, only
    # when its own text names nothing the reader may not read.
    tag_gate = (
        function("tag")
        + "(id,is_template OR kind='risk') AND (kind<>'risk' OR "
        + content_gate(("label", "markdown", "metadata"))
        + ")"
    )
    blob_gate = function("blob") + "(id)"
    file_gate = (
        "(task_id IS NULL OR task_id IN (SELECT id FROM tasks)) AND NOT EXISTS ("
        "SELECT 1 FROM "
        + inner("bot_file_versions")
        + " v WHERE v.file_id=id AND (NOT "
        + function("blob")
        + "(v.blob_id) OR (v.attempt_id IS NOT NULL AND NOT "
        + function("attempt")
        + "(v.attempt_id))))"
    )

    by_task = "task_id IN (SELECT id FROM tasks)"
    # A message's own rule: what the API lets the caller read of it. It is the hidden view READABLE_MESSAGES, which
    # runs' jobs, attempts, mentions and files read by name: they follow the message whether or not a person's timeline
    # shows it (the task's Runs show a notice the run acted on). Inlining the rule instead nests it too deep for
    # SQLite's parser in CI (parser stack overflow).
    readable_message = (
        "conversation_id IN (SELECT id FROM conversations) AND deleted_at IS NULL AND "
        + legacy_messages
        + " AND "
        + msg_gate
        + " AND "
        + function("page_message")
        + "(refs)"
    )
    # Correlate large history tables by primary key. An IN subquery materializes
    # every readable message/job/attempt before even a LIMIT 1 can return.
    by_message = f"EXISTS (SELECT 1 FROM {inner(READABLE_MESSAGES)} readable WHERE readable.id=source.message_id)"
    by_schedule = "schedule_id IN (SELECT id FROM schedules)"
    by_attempt = "EXISTS (SELECT 1 FROM attempts readable WHERE readable.id=source.attempt_id)"
    by_job = "EXISTS (SELECT 1 FROM jobs readable WHERE readable.id=source.job_id)"
    by_meeting = "meeting_id IN (SELECT id FROM meetings)"
    # A live run of a bot org rules apply to keeps its reply text out until it is checked and delivered (live_masks
    # hides its reply columns): its tool steps stay readable.
    unsettled = unsettled_watched(c, inner) if (want("deltas") or want("attempt_events")) else ""
    mine = f"actor={me}" if bot else "1"
    # Meetings are the company's; typed notes are their owner's (backend/media.py). A private
    # meeting narrows to the people its invite or its importer names, spelled either way.
    # A meeting saved "Participants only" is read only by the people it names, owner too.
    audience = "coalesce(json_extract(metadata,'$.audience'),'')='participants'"
    if bot or not who.email:
        meetings = f"NOT ({audience})" if owner else "0"
    else:
        email = q(who.email.lower())
        named = (
            f"EXISTS (SELECT 1 FROM json_each(json_extract(metadata,'$.audience_emails')) "
            f"WHERE lower(trim(value))={email})"
        )
        meetings = (
            "1"
            if owner
            else (
                f"json_extract(metadata,'$.kind')='meeting' AND ("
                "coalesce(json_extract(metadata,'$.private'),0)=0 OR EXISTS (SELECT 1 FROM "
                f"json_each(json_extract(metadata,'$.calendar.attendees')) WHERE lower(value)={email}) "
                "OR EXISTS (SELECT 1 FROM json_each(json_extract(metadata,'$.participants')) "
                f"WHERE lower(json_extract(value,'$.email'))={email}))"
            )
        )
        meetings = f"lower(owner)={email} OR (CASE WHEN {audience} THEN {named} ELSE ({meetings}) END)"
    goals = f"owner_id NOT IN {qlist(['bot:' + slug for slug in hidden])}" if hidden else "id IS NOT NULL"
    kpis = goals  # a KPI is owned like a goal: a bot's own are read by whoever may read the bot
    admin = who.role == "owner" or auth.bot_admin(who)
    # What happened is for whoever it involved: their own actions and what they may read. The rest is the
    # company's audit trail, which the owner reads whole.
    if owner:
        events = "1"
    elif bot:
        events = mine
    else:
        events = (
            f"actor={me} OR target IN (SELECT id FROM tasks) OR target IN (SELECT id FROM conversations) "
            "OR target IN (SELECT id FROM bots) OR target IN (SELECT 'bot:'||id FROM bots)"
        )
    # Personal alert activity belongs to its recipient, including in the owner's company audit. An accepted
    # non-test alert also needs its task's current permission, for both the person and any delegated principal.
    events = (
        f"({events}) AND (action NOT LIKE 'alerts.%' OR action='alerts.company_cap' OR actor={me}) "
        "AND (action<>'alerts.accepted' OR json_extract(detail,'$.kind')='test' OR "
        "json_extract(detail,'$.task_id') IN (SELECT id FROM tasks))"
    )
    # Imported drafts do not enter company search, exports or the audit feed.
    meetings = f"({meetings}) AND review_state='live'"
    events = f"({events}) AND target NOT IN (SELECT id FROM {inner('meetings')} WHERE review_state<>'live')"
    # A person reads a room's messages through its timeline: the ones with a seq (task_privacy.page, threads.timeline).
    # Tico's notices to a bot have none; they are the bot's run input (H.run_notice), so only a bot's view holds them.
    timeline = "1" if bot else "seq IS NOT NULL"
    rules = {
        "conversations": conversations,
        # The message rule itself, read by name below; connect builds it as a hidden view, never a table.
        READABLE_MESSAGES: readable_message,
        # Rooms are the small authorization set, unlike message/run history.
        # Build their id set once rather than re-evaluate the room view per message.
        "messages": f"EXISTS (SELECT 1 FROM {inner(READABLE_MESSAGES)} readable WHERE readable.id=source.id) AND "
        + timeline,
        # Thread membership and follows are read with their thread; mentions with their message.
        "thread_members": "thread_id IN (SELECT id FROM conversations)",
        "thread_followers": "thread_id IN (SELECT id FROM conversations)",
        "message_mentions": by_message,
        "tasks": tasks,
        "tags": tag_gate,
        "task_tags": by_task,
        "task_delegations": by_task,
        "task_requesters": by_task,
        "task_reminders": by_task,
        "task_assets": by_task,
        "task_documents": by_task,
        "task_links": by_task,
        "task_roles": by_task,
        # A relation is read only when the caller may read both of its tasks.
        "task_relations": "from_task IN (SELECT id FROM tasks) AND to_task IN (SELECT id FROM tasks)",
        "task_wait_spans": by_task,
        "message_assets": by_message,
        "bots": bots_visible("id"),
        "bot_status": bots_visible("bot"),
        "bot_status_history": bots_visible("bot"),
        "schedules": bots_visible("bot"),
        "bot_config": bots_visible("bot"),
        "bot_settings": bots_visible("bot_id"),
        "bot_control": bots_visible("bot"),
        "bot_transitions": bots_visible("bot"),
        "assignments": bots_visible("bot"),
        "schedule_config": by_schedule,
        "schedule_occurrences": f"{by_schedule} AND (task_id IS NULL OR {by_task})",
        "deltas": "attempt_id IN (SELECT id FROM attempts)"
        + (f" AND attempt_id NOT IN ({unsettled})" if unsettled else ""),
        "jobs": f"{bots_visible('bot_id')} AND {by_message}",
        # A run with a job is read with its job; one recorded with no lease (the demo seed, a run from before leases)
        # with its bot and the message that started it. Either way its task, when it has one, must be readable too.
        "attempts": (
            f"(CASE WHEN job_id IS NULL THEN {bots_visible('bot_id')} AND (message_id IS NULL OR {by_message}) "
            f"ELSE {by_job} END) AND (task_id IS NULL OR {by_task}) AND " + attempt_gate
        ),
        "job_recovery": by_job + " AND " + by_attempt,
        "attempt_events": by_attempt
        + " AND "
        + content_gate(("payload",))
        + (f" AND NOT (kind IN ('delta','message') AND attempt_id IN ({unsettled}))" if unsettled else ""),
        "attempt_inputs": by_attempt,
        "attempt_conversations": by_attempt,
        "bot_transition_checkpoints": "conversation_id IN (SELECT id FROM conversations)",
        "humans": TA.sql_visible(c, who, "id", bare="human", unseen=unseen) if want("humans") else "0",
        # Recruiting seats are roles on the company chart that everyone sees (backend/lifecycle.py): their titles,
        # job descriptions and reporting lines. Never a bare "1" (the same reason as listening_items below).
        "seats": "id IS NOT NULL",
        # Goals are the company's, except a bot's own: a caller reads those of bots they may read. Never a
        # bare "1" (the same reason as listening_items below).
        "goals": goals,
        "kpis": kpis,
        "kpi_readings": "kpi_id IN (SELECT id FROM kpis)",
        "kpi_definitions": "kpi_id IN (SELECT id FROM kpis)",
        "goal_kpis": "goal_id IN (SELECT id FROM goals)",
        "goal_checkins": "goal_id IN (SELECT id FROM goals)",
        # A strategy proposal carries the unpublished draft or every affected goal's title (goal_routes.strategy_visible):
        # the owner's, and the bot's that proposed it.
        "goal_proposals": "(goal_id IS NULL OR goal_id IN (SELECT id FROM goals)) AND "
        + ("1" if owner else f"(kind NOT IN ('strategy_draft','strategy_realign','brand_draft') OR proposed_by={me})"),
        "goal_asks": "task_id IN (SELECT id FROM tasks)",
        # The company's Vision and Description and their history: everyone's to read, like the company's goals.
        "company_profile": "id IS NOT NULL",
        # The team's strategy (backend/strategy.py): the versions are everyone's, like the goals. The draft (the same as
        # the newest version), the bets with the retired ones and the history of both are the owner's.
        "strategy_versions": "id IS NOT NULL" if owner else "status='published'",
        "strategy_bets": "id IS NOT NULL" if owner else "id IS NULL",
        # A lens run (backend/lenses.py) on the draft carries the unpublished strategy's words: the owner's. Every other
        # run only discusses what is published.
        "lens_runs": "id IS NOT NULL" if owner else "on_draft=0",
        # The market graph is the company's, the same way goals are.
        "entities": "1",
        "entity_links": "1",
        "market_evidence": "1",
        "market_citations": "1",
        "market_insights": "1",
        # Page monitors and the weekly briefings (backend/market_watch.py, backend/market_briefing.py): public pages the
        # team watches and what the Librarian wrote about them, the market's to read.
        "entity_watches": "1",
        "market_briefings": "1",
        # One history table (backend/migrations/baseline.sql), each row read as its subject is: a task's
        # with the task (and its words through the content gate), a goal's with the goal, the company's and the
        # market's by everyone, the strategy's by the owner (its draft's words may be unpublished).
        "history_events": (
            "CASE subject_type WHEN 'task' THEN subject_id IN (SELECT id FROM tasks) AND "
            + content_gate(("old", "new", "note", "data"))
            + " WHEN 'goal' THEN subject_id IN (SELECT id FROM goals)"
            + " WHEN 'strategy' THEN "
            + ("1" if owner else "0")
            + " WHEN 'company' THEN 1 ELSE subject_type IN "
            "('market_entity','market_edge','market_evidence','market_document') END"
        ),
        "market_sizes": "1",
        "market_figures": "1",
        "market_sources": "1",
        # A listening's sources and saved posts (backend/listening.py) carry third parties' names and handles, and a
        # push source's token hash: the owner's and the admins'. Never a bare "1" or "0": SQLite would answer
        # count(*) from the base table, which the authorizer rightly refuses.
        "listening_sources": "id IS NOT NULL" if admin and not bot else "id IS NULL",
        "listening_items": "id IS NOT NULL" if admin and not bot else "id IS NULL",
        # The roster, the sign-in lists and roles, onboarding: for the owner and the Admins. Never the key that
        # hashes roadmap visitors' addresses: with it the small IPv4 space turns every stored hash back into an address.
        # The setup research job (backend/strategy_setup.py) holds the owner's words and an unpublished draft, and the
        # imported company file (backend/company_import.py) its unpublished strategy: the owner's.
        # Nor the never-stuck reading's tasks and break sentences (health.NEVER_STUCK_TASKS_KEY): they name tasks an
        # Admin may not read. Its numbers (health:never_stuck) are readable.
        "registry_metadata": (
            "key NOT IN ('roadmap_ip_secret','health:never_stuck:tasks')"
            + ("" if owner else " AND key NOT IN ('strategy.draft_from','onboarding.company_file')")
        )
        if admin and not bot
        else "0",
        "events": f"({events}) AND " + event_gate,
        "refusals": "("
        + ("1" if owner else mine if bot else f"actor={me}")
        + ") AND "
        + function("event")
        + "('',actor,created_at,detail)",
        "meetings": f"({meetings}) AND id NOT IN (SELECT meeting_id FROM {inner('media_control')} WHERE deleted_at IS NOT NULL)",
        "meeting_versions": by_meeting,
        "meeting_deliveries": by_meeting,
        "media_assets": by_meeting,
        "media_control": by_meeting,
        "meeting_items": by_meeting,
        "meeting_brain": by_meeting,
        "meeting_comments": by_meeting,
        "import_refs": by_meeting,
        "market_pages": "1",
        "docs": "id IS NOT NULL",
        "doc_versions": "doc_id IN (SELECT id FROM docs)",
        "linked_docs": "id IS NOT NULL",
        "bot_files": (
            f"CASE WHEN id IN (SELECT file_id FROM {inner('pages')} WHERE audience IS NOT NULL) "
            "THEN "
            + function("file")
            + "(id) ELSE 1 END AND "
            + file_gate
            + " AND "
            + (f"bot_id={q(H.actor_id(who.actor))} AND " if bot else "")
            + f"(CASE WHEN scope LIKE 'task:%' THEN substr(scope,6) IN (SELECT id FROM tasks) "
            "WHEN scope LIKE 'conversation:%' THEN substr(scope,14) IN (SELECT id FROM conversations) "
            f"ELSE {bots_visible('bot_id')} END)"
        ),
        "bot_file_versions": "file_id IN (SELECT id FROM bot_files)",
        "bot_file_activity": "file_id IN (SELECT id FROM bot_files) AND "
        "(action<>'page.access_changed' OR " + function("page_manager") + "(file_id))",
        # The company owner is not thereby the owner of everyone's private attachments (media.py).
        "blobs": blob_gate
        + " AND ("
        + ("" if bot else f"owner={me} OR ")
        + "id IN (SELECT blob_id FROM message_assets) OR id IN (SELECT blob_id FROM task_assets) "
        "OR id IN (SELECT blob_id FROM media_assets))",
        "archives": "1" if owner else "0" if bot else f"owner={me}",
        "service_jobs": "1" if owner else "0" if bot else f"requested_by={me}",
        # The Company page's own rules, drafts, stats and approvals: the owner's, as the page's API shows them. The
        # readers' tables (who they are, their sessions and invites, what they wrote, what they read) are not listed, so
        # they are denied to everyone, the owner included.
        **{table: "1" if owner else "0" for table in COMPANY_PAGE_OWNER_TABLES},
        "slack_posts": by_message if owner else "message_id IS NULL",
        # Which history rows are settled (backend/read_deps.py): read by the message and run gates, by no caller.
        "read_index": "key IS NULL",
    }
    for table in ("bot_status", "bot_status_history"):
        rules[table] += " AND (task_id IS NULL OR " + by_task + ")"
        # Untagged free-form status can retain the previous private turn's content.
        rules[table] += (
            " AND NOT EXISTS (SELECT 1 FROM "
            + inner("tasks")
            + " t WHERE (t.owner_id='bot:'||bot OR t.requester_id='bot:'||bot) "
            "AND (t.private IS NULL OR t.private<>0) AND NOT " + function("task") + "(t.id))"
        )
    for table in ("docs", "doc_versions", "linked_docs", "market_pages"):
        if not want(table):
            continue
        cols = (
            columns.get(table, ())
            if columns is not None
            else [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
        )
        rules[table] = "(" + rules[table] + ") AND " + content_gate(cols)
    rules["task_assets"] += " AND " + function("blob") + "(blob_id)"
    rules["task_file_reviews"] = "file_id IN (SELECT id FROM bot_files) OR file_id IN (SELECT id FROM blobs)"
    rules["task_documents"] += " AND file_id IN (SELECT id FROM bot_files)"
    rules["bot_file_activity"] += (
        " AND (task_id IS NULL OR " + by_task + ") AND (attempt_id IS NULL OR " + by_attempt + ")"
    )
    rules["bot_file_versions"] += " AND (attempt_id IS NULL OR " + by_attempt + ")"
    if only is not None:
        rules = {table: rule for table, rule in rules.items() if table in only}
    hidden = {table: set(HIDDEN.get(table, ())) for table in rules}
    if bot and "humans" in hidden:
        hidden["humans"].add("email")
    return {table: (rules[table], hidden[table]) for table in rules}


# Per-request sets, each an indexed temp table only this request's views may read.
SETS = (
    "visible_tasks",
    "bound_rooms",
    "meeting_rooms",
    "readable_conversations",
    "denied_tasks",
    "unreadable_rooms",
    "run_carriers",
    "denied_reads",
)
RUN_EVENTS = "('task.next-run.carried','attempt.input.moved')"


def names_run(refs):
    return " OR ".join(f"""instr(coalesce({refs},''),'"{key}"')>0""" for key in ("turn_id", "run"))


class Gates:
    """SQL that answers yes, without Python, for a row the privacy functions in backend/privacy_index.py allow.

    Each gate is `CASE WHEN fast THEN 1 ELSE privacy function END`: anything but a fast yes still asks Python, so a
    fast clause may only be narrower than the function, never wider. A clause reads small sets built once per request
    (the caller's denied private tasks, the rooms whose plain messages they may not read, the runs that carry a denied
    task) and allows a message only when every message its provenance reaches is settled by its own row: its parent
    and the messages it answers have no parents, answers or inputs of their own, and its run's messages likewise.
    With no denied task, no string needs a lookup at all.

    Where SQLite and Python could read the same JSON differently the clause declines and Python decides: a backslash
    (an escaped string), a repeated key (SQLite reads the first, Python the last), a task given as anything but one
    string or a list of strings, a string that is itself JSON, a meeting reference, a moved input, and long provenance.
    """

    KEYS = ("task", "task_id", "turn_id", "run", "attempt_id", "answers", "inputs")

    def __init__(self, inner, private, denied, page):
        self.inner, self.denied, self.page = inner, bool(denied), page
        self.all_tasks = inner("tasks")
        self.messages = inner("messages")
        self.denied_tasks = '"' + private("denied_tasks") + '"'
        self.rooms = '"' + private("unreadable_rooms") + '"'
        self.carriers = '"' + private("run_carriers") + '"'

    def known(self, value):
        """A task with this id exists: one primary-key lookup in the unfiltered tasks view. Not a per-request copy of
        every task id, and not `IN (SELECT id ...)`, which through a view builds a list of all of them per statement.
        Only ever asked about a text value, so it reads as `IN` did."""
        return f"EXISTS (SELECT 1 FROM {self.all_tasks} known_task WHERE known_task.id={value})"

    def strings(self, value):
        """No string in this JSON names a denied task the way ReadIndex.references finds one."""
        if not self.denied:
            return "1"
        return (
            f"NOT EXISTS (SELECT 1 FROM json_tree({value}) s WHERE s.type='text' AND ("
            f"s.value IN (SELECT id FROM {self.denied_tasks}) OR instr(s.value,'/task')>0 "
            "OR substr(s.value,1,1) IN ('{','[') OR (substr(s.value,1,5)='task:' "
            f"AND substr(s.value,6) IN (SELECT id FROM {self.denied_tasks}))))"
        )

    def plain(self, refs):
        """`refs` is exactly a task-only object as json_object writes it, or with a space after the colon: {"task":"<id>"}
        or {"task_id":"<id>"}, naming a task that exists and is not denied, with no backslash, no `live_meeting` and no
        string `strings` would refuse. Such refs pass every check of `refs` below, so this is only a shortcut to the same
        yes, read from the text and one primary-key lookup instead of parsing JSON. It replaces a per-request table of
        every task's four spellings (74,400 rows at 18,600 tasks, built before even `SELECT 1`)."""
        spelled = " ".join(
            f"WHEN substr({refs},1,{len(head)})='{head}' THEN substr({refs},{len(head) + 1},length({refs})-{len(head) + 2})"
            for head in ('{"task":"', '{"task": "', '{"task_id":"', '{"task_id": "')
        )
        tid = "plain.id"
        denied = (
            f" AND {tid} NOT IN (SELECT id FROM {self.denied_tasks}) AND instr({tid},'/task')=0 "
            f"AND substr({tid},1,1) NOT IN ('{{','[') AND NOT (substr({tid},1,5)='task:' "
            f"AND substr({tid},6) IN (SELECT id FROM {self.denied_tasks}))"
            if self.denied
            else ""
        )
        # The ids "task", "answers" and "inputs" are left to the full check: in this shape `refs` (top=False) reads the
        # first as a repeated key and the others as inherited keys, so the old table of spellings never held them.
        return (
            f"EXISTS (SELECT 1 FROM (SELECT CASE {spelled} END AS id) plain WHERE {tid} IS NOT NULL "
            f"AND {refs} IN (json_object('task',{tid}),'{{\"task\": '||json_quote({tid})||'}}',"
            f"json_object('task_id',{tid}),'{{\"task_id\": '||json_quote({tid})||'}}') "
            f"AND instr({refs},'\\')=0 AND instr({refs},'live_meeting')=0 "
            f"AND {tid} NOT IN ('task','answers','inputs') AND {self.known(tid)}{denied})"
        )

    def task(self, refs, key):
        """`refs.<key>` is absent, one string naming a task that exists and is not denied, or a non-empty array in which
        every element is such a string. ReadIndex.listed_tasks_known reads a list the same way: any other element, an
        empty list, or a list naming one unknown or denied task is refused."""
        path = f"'$.{key}'"
        listed = (
            f"SELECT 1 FROM json_each({refs},{path}) element WHERE element.type<>'text' OR "
            f"NOT {self.known('element.value')} OR element.value IN (SELECT id FROM {self.denied_tasks})"
        )
        return (
            f"(json_type({refs},{path}) IS NULL OR (json_type({refs},{path})='text' AND "
            f"{self.known(f'json_extract({refs},{path})')} AND "
            f"json_extract({refs},{path}) NOT IN (SELECT id FROM {self.denied_tasks})) OR "
            f"(json_type({refs},{path})='array' AND json_array_length({refs},{path})>0 AND NOT EXISTS ({listed})))"
        )

    def refs(self, refs, top):
        """The row's own refs: an object naming no meeting, no denied string and only known readable tasks.

        Only the message being read (`top`) may name ancestors, a bounded few, and a run, which must itself pass."""
        once = {
            key: (f"(instr({refs},'\"{key}\"')=0 OR instr(substr({refs},instr({refs},'\"{key}\"')+1),'\"{key}\"')=0)")
            for key in self.KEYS
        }
        checks = (
            f"json_type({refs})='object' AND instr({refs},'\\')=0 "
            f"AND instr({refs},'live_meeting')=0 AND {once['task']} AND {once['task_id']} "
            f"AND {self.task(refs, 'task')} AND {self.task(refs, 'task_id')} AND {self.strings(refs)}"
        )
        # Sibling WHENs short-circuit validation before inherited checks without nesting another CASE around
        # the run and its roots. Plain refs need no JSON walk; malformed or undecided refs keep Python's gate.
        plain = f"WHEN {self.plain(refs)} THEN 1 "
        checked = (
            f"CASE WHEN coalesce({refs},'') IN ('','{{}}') THEN 1 {plain}"
            f"WHEN NOT json_valid({refs}) THEN 0 WHEN NOT coalesce({checks},0) THEN 0 "
        )
        if not top:
            return f"{checked}WHEN instr({refs},'\"answers\"')=0 AND instr({refs},'\"inputs\"')=0 THEN 1 ELSE 0 END"
        turn, run_id = f"json_extract({refs},'$.turn_id')", f"json_extract({refs},'$.run.attempt_id')"
        # With no inherited keys there is no run or ancestor array to parse or check for duplicates. A root's
        # run is not inherited at all, so only the top message needs these checks.
        direct = " AND ".join(f"instr({refs},'\"{key}\"')=0" for key in ("turn_id", "run", "answers", "inputs"))
        inherited = " AND ".join(once[key] for key in self.KEYS if key not in ("task", "task_id"))
        inherited += (
            f" AND coalesce(json_type({refs},'$.answers'),'array')='array' "
            f"AND coalesce(json_type({refs},'$.inputs'),'array')='array' "
            f"AND coalesce(json_array_length({refs},'$.answers'),0)+coalesce(json_array_length({refs},'$.inputs'),0)<=48 "
            f"AND CASE WHEN json_type({refs},'$.turn_id') IS NOT NULL "
            f"THEN json_type({refs},'$.turn_id')='text' AND {self.attempt(turn)} "
            f"WHEN json_type({refs},'$.run.attempt_id') IS NOT NULL "
            f"THEN json_type({refs},'$.run.attempt_id')='text' AND {self.attempt(run_id)} ELSE 1 END"
        )
        return f"{checked}WHEN {direct} THEN 1 WHEN {inherited} THEN 1 ELSE 0 END"

    def root(self, alias):
        """A message settled by its own row: no parent, answers or inputs, its file references readable, and (unread
        here) no run. Its file check is ReadIndex.message's own-refs check, so a parent with a refused file is not a yes."""
        # Preserve CASE's false result for NULL comparisons under the negated root checks in message/attempt.
        return (
            f"(coalesce({alias}.in_reply_to,'')='' AND coalesce({alias}.conversation_id NOT IN "
            f"(SELECT id FROM {self.rooms}),0) AND {self.refs(alias + '.refs', top=False)} "
            f"AND {self.page(alias + '.refs')})"
        )

    def message(self, alias="source"):
        """ReadIndex.message: the row, its run, and each message it replies to, answers or took in as input."""
        refs = alias + ".refs"
        ancestors = (
            f"SELECT {alias}.in_reply_to AS id UNION ALL "
            f"SELECT value FROM json_each({refs},'$.answers') WHERE type='text' UNION ALL "
            f"SELECT value FROM json_each({refs},'$.inputs') WHERE type='text'"
        )
        return (
            f"{alias}.conversation_id NOT IN (SELECT id FROM {self.rooms}) AND {self.refs(refs, top=True)} "
            f"AND (coalesce({alias}.in_reply_to,'')='' AND coalesce(instr({refs},'\"answers\"'),0)=0 "
            f"AND coalesce(instr({refs},'\"inputs\"'),0)=0 OR NOT EXISTS (SELECT 1 FROM ({ancestors}) ancestor JOIN {self.messages} parent "
            f"ON parent.id=ancestor.id WHERE parent.id<>{alias}.id AND (NOT {self.root('parent')} "
            f"OR (parent.id={alias}.in_reply_to AND ({names_run('parent.refs')})))))"
        )

    def attempt(self, aid):
        """ReadIndex.attempt: its message and inputs are settled by their own rows, it carries no denied task and its
        retained run events name none."""
        jobs, attempts, inputs, events = (self.inner(t) for t in ("jobs", "attempts", "attempt_inputs", "events"))
        return (
            f"EXISTS (SELECT 1 FROM {attempts} run JOIN {jobs} job ON job.id=run.job_id "
            f"JOIN {self.messages} root ON root.id=job.message_id WHERE run.id={aid} AND {self.root('root')}) "
            f"AND NOT EXISTS (SELECT 1 FROM {inputs} input JOIN {self.messages} root ON root.id=input.message_id "
            f"WHERE input.attempt_id={aid} AND NOT {self.root('root')}) "
            f"AND {aid} NOT IN (SELECT id FROM {self.carriers}) "
            f"AND NOT EXISTS (SELECT 1 FROM {events} retained WHERE retained.action IN {RUN_EVENTS} "
            f"AND retained.target={aid} AND NOT coalesce(retained.action='task.next-run.carried' AND "
            f"(coalesce(retained.detail,'')='' OR (json_valid(retained.detail) AND "
            f"json_type(retained.detail)='object' AND instr(retained.detail,'\\')=0 AND "
            f"{self.strings('retained.detail')})), 0))"
        )


def materialize(conn, index, tables, inner, function, private):
    """Build only the per-request small sets that `tables` actually need, then point predicates at them instead of
    re-deriving them per row.

    Visible tasks and readable rooms are what a LIMIT 1 would otherwise rebuild for every query: the room predicate
    asks Python about each room. Hidden things are the few denied tasks, unreadable rooms and runs that carry one."""

    def create(name, select, args=()):
        conn.execute(f'CREATE TEMP TABLE "{private(name)}"(id TEXT PRIMARY KEY) WITHOUT ROWID')
        if isinstance(select, str):
            conn.execute(f'INSERT OR IGNORE INTO "{private(name)}" ' + select, args)
        else:
            conn.executemany(
                f'INSERT OR IGNORE INTO "{private(name)}" VALUES(?)', ((v,) for v in select if v is not None)
            )
        return f'IN (SELECT id FROM "{private(name)}")'

    if "tasks" in tables:
        visible = create("visible_tasks", "SELECT id FROM main.tasks WHERE " + tables["tasks"][0])
        # Reuse the task-id set directly. Going through the tasks view rebuilds
        # the same task list for every nested room lookup inside a privacy UDF.
        tables = {
            table: (predicate.replace("IN (SELECT id FROM tasks)", visible), hidden)
            for table, (predicate, hidden) in tables.items()
        }
        tables["tasks"] = ("id " + visible, tables["tasks"][1])
    if "conversations" in tables:
        # The few rooms with meeting provenance (ReadIndex rooms=True): the room predicate asks Python about these alone.
        # Unsorted: a set may hold a NULL room id (create drops it), which sorted() cannot order against text.
        create("bound_rooms", index.bound_rooms)
        create("meeting_rooms", index.meeting_rooms)
        rooms = create(
            "readable_conversations", "SELECT id FROM main.conversations WHERE " + tables["conversations"][0]
        )
        tables = {
            table: (predicate.replace("IN (SELECT id FROM conversations)", rooms), hidden)
            for table, (predicate, hidden) in tables.items()
        }
        tables["conversations"] = ("id " + rooms, tables["conversations"][1])
    if READABLE_MESSAGES not in tables and "attempts" not in tables:
        return tables
    create("denied_tasks", sorted(index.denied))
    create("unreadable_rooms", sorted(index.unreadable_rooms()))
    # A run on a denied task (its `attempts.task_id`) is never a fast yes.
    create(
        "run_carriers",
        sorted(
            aid
            for aid, tid in conn.execute("SELECT id,task_id FROM main.attempts WHERE task_id IS NOT NULL")
            if tid in index.denied
        ),
    )
    # Settled history rows that touched one of the caller's denied tasks (backend/read_deps.py). It grows with the
    # rows those tasks reach, a busy private room included: about 35 ms per 100,000 rows here, once per request.
    create(
        "denied_reads",
        f"SELECT key FROM main.read_deps WHERE dep IN (SELECT 't:'||id FROM \"{private('denied_tasks')}\")",
    )
    page = function("page_message") + "(refs)"

    def page_gate(refs):
        """The file check for one row's refs. A page review can narrow a message independently of task/run provenance.
        Only these top-level keys enter that check; escaped keys and every non-object keep Python's JSON parsing and
        page permissions."""
        no_page = " AND ".join(f"instr({refs},'\"{key}\"')=0" for key in privacy.PAGE_MESSAGE_KEYS)
        return (
            f"(CASE WHEN coalesce({refs},'') IN ('','{{}}') THEN 1 WHEN json_valid({refs}) "
            f"AND json_type({refs})='object' AND instr({refs},'\\')=0 AND {no_page} THEN 1 "
            f"ELSE {function('page_message')}({refs}) END)"
        )

    gates = Gates(inner, private, index.denied, page_gate)
    message = function("message") + "(id,conversation_id,refs,in_reply_to)"
    attempt = function("attempt") + "(id)"

    def settled(prefix):
        """A settled row that touched no denied task is read at once; any other row keeps the gate and its walk. A row
        whose walk read a message naming a file is never settled (read_deps.names_file): its file refusals are per
        reader, so ReadIndex reads it."""
        key = f"'{prefix}:'||source.id"
        indexed = (
            f"{key} IN (SELECT key FROM {inner('read_index')}) AND {key} NOT IN "
            f'(SELECT id FROM "{private("denied_reads")}")'
        )
        if prefix == "m":
            # A settled reply can predate the meeting check on its ancestors' run references. Its dependencies
            # still name those messages, so run-linked replies go through the current gate.
            indexed += (
                f" AND (source.in_reply_to IS NULL OR NOT EXISTS (SELECT 1 FROM {inner('read_deps')} d "
                f"JOIN {inner('messages')} parent ON parent.id=substr(d.dep,3) "
                f"WHERE d.key={key} AND substr(d.dep,1,2)='m:' AND parent.id<>source.id "
                f"AND ({names_run('parent.refs')})))"
            )
        return indexed

    # CASE evaluates only the chosen branch. A fast yes skips the Python callback altogether; OR can evaluate
    # the function first, and passing the fast answer to a function still calls Python for every history row.
    # Each gate is rendered once: rendered per table, the gates' SQL text alone took tens of ms a request.
    gated = {
        message: f"(CASE WHEN {settled('m')} THEN 1 WHEN {gates.message()} THEN 1 ELSE {message} END)",
        attempt: f"(CASE WHEN {settled('a')} THEN 1 WHEN {gates.attempt('source.id')} THEN 1 ELSE {attempt} END)",
        page: page_gate("refs"),
    }

    def gate(predicate):
        for placeholder, sql in gated.items():
            if placeholder in predicate:
                predicate = predicate.replace(placeholder, sql)
        return predicate

    return {table: (gate(predicate), hidden) for table, (predicate, hidden) in tables.items()}


# Tables built in Python for one request, only when its statement names them.
WORKED_OUT = ("task_waits",)


def task_waits(conn, c, private, hidden):
    """What each open task the caller may read waits on, as the engine reads it (`H.task_waits`, backend/waiting_on.py):
    one row per wait, `position` 0 the headline. A person or bot the caller may not see is '' with no line; a sub-task
    they may not read is named by neither its title nor its id, as the task's own read says it (waiting_on.unnamed)."""
    from . import waiting_on as W
    from .store import H

    visible = f'SELECT id FROM "{private("visible_tasks")}"'
    conn.execute(
        'CREATE TEMP TABLE "task_waits"(task_id TEXT NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL, '
        "waiting_on TEXT, line TEXT NOT NULL, since TEXT, until TEXT, PRIMARY KEY(task_id, position)) WITHOUT ROWID"
    )
    ids = [r[0] for r in conn.execute(f"SELECT id FROM main.tasks WHERE status='open' AND id IN ({visible})")]
    if not ids:
        return
    readable = set(ids) | {r[0] for r in conn.execute(visible)}
    rows = []
    for tid, waits in H.task_waits(c, ids).items():
        for position, wait in enumerate(waits):
            wait = W.unnamed(wait, readable.__contains__)
            who, line = wait.who, wait.line or ""
            if who and who in hidden:
                who, line = "", ""
            rows.append((tid, position, wait.state, who, line, wait.since, wait.until))
    conn.executemany('INSERT INTO "task_waits" VALUES(?,?,?,?,?,?,?)', rows)


def connect(
    path, c, auth, who, trace=None, seconds=None, statement="", pool=None, pool_age=0, assistant_bot="assistant"
):
    """A read-only connection whose temp views and authorizer carry this caller's visibility.

    `seconds` bounds building this request's visibility sets as `run` bounds the query itself. When `statement` is
    non-empty, only the guarded views, inner views, materialized sets and UDFs that `statement` (and its transitive
    dependencies) reference are built. When `pool` is given, connections are reused across calls.
    """
    deadline = None if seconds is None else time.monotonic() + seconds
    prefix = "v" + secrets.token_hex(12) + "_"
    conn = pool.take(path, pool_age) if pool is not None else _open_ro(path)
    fn_prefix = conn._fn_prefix
    inner = lambda table: f'"{prefix}{table}"'
    function = lambda name: f'"{fn_prefix}privacy_{name}"'
    private = lambda name: prefix + name
    private_views = {"pages", "read_deps"}
    conn.read_cache, conn.private_prefix = {}, prefix
    if trace:
        conn.set_trace_callback(trace)
    trusted = [False]

    def in_time():
        # The Python steps between statements: the progress handler stops only SQLite's own work.
        if deadline is not None and time.monotonic() > deadline:
            raise sqlite3.OperationalError("interrupted")

    try:
        # The whole visibility build is under the caller's time limit, not only its materialized sets: a build that
        # outruns it is stopped like a query that does (422 `timeout`), so one slow call holds a worker only that long.
        if deadline is not None:
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
        # Predicates, provenance and result rows use one read snapshot.
        conn.execute("BEGIN")
        columns = schema_columns(conn, path)
        wanted = referenced_names(statement) if statement else None
        worked_out = {name for name in WORKED_OUT if wanted is not None and name in wanted}
        want_derived = {name: view for name, view in DERIVED.items() if wanted is None or name in wanted}
        want_granted_names = set(GRANTED) if wanted is None else (set(GRANTED) & wanted)
        # The Assistant creates its table on first use: a view over a table or column not there yet is left out.
        granted = (
            {
                name: GRANTED[name]
                for name in want_granted_names
                if outcome_stats.may_read(c, who)
                and all(set(GRANTED_SOURCES.get(base, ())) <= set(columns.get(base, ())) for base in GRANTED[name][0])
            }
            if want_granted_names
            else {}
        )
        views = {**want_derived, **granted}
        if wanted is None:
            needed_tables = None
        else:
            seed_tables = set(wanted & GUARDED_TABLES)
            for bases, select in views.values():
                for base in bases:
                    if f"{{{base}}}" in select and base in GUARDED_TABLES:
                        seed_tables.add(base)
            if "task_waits" in worked_out:
                seed_tables.add("tasks")
            needed_tables = _expand_tables(seed_tables, who.role)
        need_unseen = needed_tables is None or bool(needed_tables & _UNSEEN_TABLES) or "task_waits" in worked_out
        unseen = TA.hidden(conn, who, "see", ("human", "group"), auth) if need_unseen else set()
        in_time()
        if needed_tables is None or needed_tables:
            tables = guarded(
                conn, auth, who, inner, function, columns, unseen=unseen, private=private, only=needed_tables
            )
        else:
            tables = {}
        in_time()
        readable = tables.pop(READABLE_MESSAGES, None)
        masks = {table: dict(MASKED[table]) for table in tables if table in MASKED}
        if "attempts" in tables:
            for table, cols_masked in live_masks(unsettled_watched(conn, inner)).items():
                if table in tables:
                    masks[table] = {**masks.get(table, {}), **cols_masked}
        if "messages" in tables:
            masks["messages"] = {**masks.get("messages", {}), "refs": function("page_refs") + "(source.refs)"}
        need_bots = "thread_members" in tables or "task_wait_spans" in tables or "task_waits" in worked_out
        bots = TA.hidden(conn, who, "see", ("bot",), auth) if need_bots else set()
        if (unseen or bots) and (set(tables) & _UNSEEN_TABLES):
            from . import threads

            names = qlist(threads.Viewer(auth, who).member_names(conn))
            for table, cols_masked in teammate_masks(unseen, q(who.actor), inner, names, bots).items():
                if table in tables:
                    masks[table] = {**masks.get(table, {}), **cols_masked}
        # Discover every inner view and privacy UDF referenced by the selected predicates and masks.
        sql_fragments = [pred for pred, _ in tables.values()]
        if readable is not None:
            sql_fragments.append(readable[0])
        for cols_masked in masks.values():
            sql_fragments.extend(cols_masked.values())
        joined = "\n".join(sql_fragments)
        fn_refs = set(re.findall(re.escape(fn_prefix) + r"privacy_([a-z_]+)", joined))
        inner_refs = set(re.findall(re.escape(prefix) + r"([A-Za-z_][A-Za-z0-9_]*)", joined))
        inner_refs -= set(SETS) | {READABLE_MESSAGES}
        if readable is not None or "attempts" in tables:
            inner_refs |= {
                "read_index",
                "read_deps",
                "messages",
                "tasks",
                "jobs",
                "attempts",
                "attempt_inputs",
                "events",
            }
            fn_refs |= {"message", "attempt", "page_message"}
        sources = {table for bases, _ in views.values() for table in bases} & set(GRANTED_SOURCES)
        for bases, select in views.values():
            for base in bases:
                if f"{{all_{base}}}" in select and base not in GRANTED_SOURCES:
                    inner_refs.add(base)
        # Page visibility traverses letter proofs and current agent standing through trusted callbacks only.
        source_tables = GUARDED_TABLES | {
            "pages",
            "page_identities",
            "mail_items",
            "postal_mail_grants",
            "rule_reads",
            "pins",
            "pin_places",
            "teammate_accesses",
            "task_trash_assets",
            "home_merges",
            "home_merge_messages",
            "blob_media",
            "live_meetings",
            "live_meeting_access",
            "live_meeting_bots",
            "live_meeting_turns",
            "letters",
            "letter_proofs",
            "letter_outbox_grants",
            "agents",
            "ok_grants",  # an event about a bound OK reads only to its parties (task_asks.event_hidden)
            "row_clock",
            "provenance_clock",
            "roster_clock",
        }
        need_index = (
            bool(fn_refs & _INDEX_FUNCTIONS)
            or "conversations" in tables
            or readable is not None
            or "attempts" in tables
        )
        index = None
        if fn_refs or need_index:

            class Source:
                def __init__(self):
                    self.statements = {}

                # Provenance helpers share this request's snapshot cache through the restricted source wrapper.
                @property
                def read_cache(self):
                    return conn.read_cache

                # The store's shared row cache (row_clock.parties and access rows). This connection only reads and never
                # publishes, so it shares rows at the clock its own snapshot reads, like a pooled read connection does.
                row_cache = getattr(c, "row_cache", None)
                write_transaction = False

                @property
                def provenance_changes(self):
                    return conn.total_changes

                @property
                def in_transaction(self):
                    return conn.in_transaction

                @property
                def total_changes(self):
                    return conn.total_changes

                def execute(self, stmt, args=()):
                    src = self.statements.get(stmt)
                    if src is None:
                        src = re.sub(
                            r"\b(FROM|JOIN)\s+(\w+)",
                            lambda m: m[1] + ' main."' + m[2] + '"' if m[2] in source_tables else m[0],
                            stmt,
                            flags=re.I,
                        )
                        if len(self.statements) < 128:
                            self.statements[stmt] = src
                    stmt = src
                    trusted[0] = True
                    try:
                        return conn.execute(stmt, args)
                    finally:
                        trusted[0] = False

            source = Source()
            if need_index:
                index = ReadIndex(source, who, rooms=("conversations" in tables))
                conn.release = index.release
            if fn_refs:
                functions = {}
                if "page_message" in fn_refs:
                    functions["page_message"] = (
                        1,
                        lambda refs: privacy.page_message_readable(source, privacy.actor(who), H._json(refs, {}) or {}),
                    )
                if index is not None:
                    idx = index
                    functions.update(
                        {
                            "message": (4, idx.message),
                            "attempt": (1, idx.attempt),
                            "conversation": (1, idx.conversation),
                            "meeting_room": (1, idx.meeting_room),
                            "tag": (2, idx.tag),
                            "task": (1, lambda tid: tid not in idx.denied),
                            "content": (-1, lambda *parts: idx.content(list(parts))),
                        }
                    )
                if fn_refs & {"page_refs", "file", "page_manager", "event", "blob"}:
                    from .files import (
                        page_audience_visible,
                        page_blob_visible,
                        page_event_readable,
                        page_graph,
                        readable_page_refs,
                        restricted_pages,
                    )
                    from .pages import may_manage as page_manager

                    audience_cache = {}

                    def visible_file(fid):
                        row = restricted_pages(source).get(fid)
                        # SQL already enforces inherited scope/provenance below; only add the common narrowing.
                        return not row or page_audience_visible(source, auth, who, row, audience_cache)

                    def manages_page(fid):
                        row = source.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
                        return bool(row) and page_manager(source, auth, who, row)

                    # These run once per row of the whole query: read every page once rather than look up each row's.
                    functions["page_refs"] = (
                        1,
                        lambda refs: page_graph(source) or readable_page_refs(source, auth, who, refs),
                    )
                    functions["file"] = (1, visible_file)
                    functions["page_manager"] = (1, manages_page)
                    if index is not None:
                        idx = index
                        if "event" in fn_refs:
                            from .api_tools import hidden_ids

                            # A tool `who` may not see leaves no event: not its own, not its keys', live or deleted.
                            # Its rules need no credential administrators: an Only listed tool ignores them.
                            unseen_targets = hidden_ids(c, who, (), auth)
                            functions["event"] = (
                                4,
                                lambda target, actor, ts, detail: (
                                    target not in unseen_targets
                                    and not page_graph(source)
                                    and page_event_readable(source, auth, who, target, detail)
                                    and idx.event(target, actor, ts, detail)
                                ),
                            )
                        functions["blob"] = (
                            1,
                            lambda bid: (
                                idx.blob(bid) and not page_graph(source) and page_blob_visible(source, auth, who, bid)
                            ),
                        )
                for name in fn_refs:
                    arity, fn = functions[name]
                    fn_name = fn_prefix + "privacy_" + name
                    conn.create_function(fn_name, arity, fn, deterministic=True)
                    conn._registered_functions.append((fn_name, arity))
        exposed = {}
        inner_tables = (set(tables) | inner_refs) - private_views
        for table in inner_tables:
            table_hidden = tables[table][1] if table in tables else set(HIDDEN.get(table, ()))
            if who.role == "bot" and table == "humans":
                table_hidden = table_hidden | {"email"}
            exposed[table] = [
                name
                for name in columns.get(table, ())
                if name not in table_hidden and (table not in SAFE_COLUMNS or name in SAFE_COLUMNS[table])
            ]
            if not exposed[table]:
                raise Problem("schema", f"Table {table} is missing from this database", 500)
            names_sql = ",".join(f'"{column}"' for column in exposed[table])
            conn.execute(f'CREATE TEMP VIEW {inner(table)} AS SELECT {names_sql} FROM main."{table}"')
        built_private_views = inner_refs & private_views
        if "pages" in built_private_views:
            conn.execute(f'CREATE TEMP VIEW {inner("pages")} AS SELECT file_id,audience FROM main.pages')
        if "read_deps" in built_private_views:
            conn.execute(f'CREATE TEMP VIEW {inner("read_deps")} AS SELECT dep,key FROM main.read_deps')
        in_time()
        if "tasks" in tables or "conversations" in tables or readable is not None or "attempts" in tables:
            mat_input = {**tables, READABLE_MESSAGES: readable} if readable is not None else dict(tables)
            tables = materialize(conn, index, mat_input, inner, function, private)
            readable = tables.pop(READABLE_MESSAGES, None)
        if readable is not None:
            # The rule over the unfiltered messages: each row is `source`, as materialize's gates read it.
            conn.execute(
                f"CREATE TEMP VIEW {inner(READABLE_MESSAGES)} AS SELECT * FROM {inner('messages')} AS source WHERE {readable[0]}"
            )
        in_time()
        for table, (predicate, _) in tables.items():
            masked = masks.get(table, {})
            projection = (
                ",".join(
                    f'({masked[name]}) AS "{name}"' if name in masked else f'source."{name}"' for name in exposed[table]
                )
                if masked
                else "*"
            )
            conn.execute(
                f'CREATE TEMP VIEW "{table}" AS SELECT {projection} FROM {inner(table)} AS source WHERE {predicate}'
            )
        for table in sources:
            names_sql = ",".join(f'"{column}"' for column in GRANTED_SOURCES[table])
            conn.execute(f'CREATE TEMP VIEW {inner(table)} AS SELECT {names_sql} FROM main."{table}"')
        for name, (bases, select) in views.items():
            conn.execute(
                f'CREATE TEMP VIEW "{name}" AS '
                + select.format(
                    **{base: f'"{base}"' for base in bases},
                    **{"all_" + base: inner(base) for base in bases},
                    tool_names=qlist(sorted(outcome_stats.TOOL_NAMES)),
                    assistant=q(assistant_bot or "assistant"),
                )
            )
        if "task_waits" in worked_out:
            task_waits(conn, c, private, set(unseen) | set(bots))
        in_time()
        conn.set_progress_handler(None, 0)
        conn.execute("PRAGMA query_only=1")
        # Every other object in the file is off limits, even as a bare `count(*)`.
        denied = (columns.objects() | {"sqlite_temp_master"}) - (set(tables) | OPEN)
    except sqlite3.OperationalError as exc:
        conn._tainted = True
        conn.close()
        if deadline is not None and (time.monotonic() > deadline or "interrupted" in str(exc)):
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise
    except BaseException:
        conn._tainted = True
        conn.close()
        raise
    inner_base = inner_tables | sources | built_private_views
    inner_names = {prefix + table for table in inner_base} | (
        {prefix + READABLE_MESSAGES} if readable is not None else set()
    )
    sets = {private(name) for name in SETS}

    def fts_parent(name):
        for suffix in FTS_SHADOWS:
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return None

    def authorize(action, table, column, schema, context):
        # Who may read a private object: a table's view, or the message rule's view (which reads the same sets and
        # functions the table views read on its behalf).
        def reads(view):
            return view in tables or view == prefix + READABLE_MESSAGES

        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if column and column.startswith(fn_prefix + "privacy_"):
                return sqlite3.SQLITE_OK if reads(context) else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY if column in DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        # FTS5 checks main.data_version before reading; nothing else may PRAGMA.
        if action == sqlite3.SQLITE_PRAGMA:
            return (
                sqlite3.SQLITE_OK
                if table == "data_version" or trusted[0] and table == "table_info"
                else sqlite3.SQLITE_DENY
            )
        if action != sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY
        if schema is None:
            # A table named without columns (`count(*)`), a CTE or a subquery: an unqualified
            # name resolves to the temp view first, so only the objects with no view are denied.
            return sqlite3.SQLITE_DENY if table in denied else sqlite3.SQLITE_OK
        if schema == "main":
            if trusted[0] and table in source_tables:
                return sqlite3.SQLITE_OK
            if table in OPEN or table in TABLE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            # These are visible in full (predicate 1). SQLite answers count(*) by reading
            # the base table with no view context; that read is the same rows as the view.
            if table in {
                "entities",
                "entity_links",
                "market_evidence",
                "market_citations",
                "market_insights",
                "entity_watches",
                "market_briefings",
                "market_sizes",
                "market_figures",
                "market_sources",
            }:
                return sqlite3.SQLITE_OK
            # A base table is read only by its own inner view, whose name is this connection's secret.
            if table in inner_base and context == prefix + table:
                return sqlite3.SQLITE_OK
            # FTS5 reads the virtual table and its shadow tables with no view
            # context. Allow that only for the owner.
            parent = fts_parent(table or "")
            if who.role == "owner" and table in tables and str(table).endswith("_fts"):
                return sqlite3.SQLITE_OK
            if who.role == "owner" and parent and parent in tables:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if schema == "temp":
            if table in sets:
                return sqlite3.SQLITE_OK if reads(context) else sqlite3.SQLITE_DENY
            if table in tables or table in views or table in worked_out:
                return sqlite3.SQLITE_OK
            if table in inner_names and context in views:
                return sqlite3.SQLITE_OK if table[len(prefix) :] in views[context][0] else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if table in inner_names and reads(context) else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    return conn


def json_value(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def run(conn, sql, params, cap, seconds):
    """Execute one read statement; returns the response body or raises a Problem with SQLite's words."""
    deadline = time.monotonic() + seconds
    interrupted = False

    def progress():
        nonlocal interrupted
        interrupted = interrupted or time.monotonic() > deadline
        return int(interrupted)

    conn.set_progress_handler(progress, 2000)
    started = time.monotonic()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchmany(cap + 1)
    except sqlite3.OperationalError as exc:
        # SQLite wraps an interrupt from a nested SELECT inside a Python UDF
        # as "user-defined function raised exception". Preserve its cause.
        if interrupted or "interrupted" in str(exc):
            conn._tainted = True
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise Problem("sql", str(exc), 422) from exc
    except (sqlite3.Error, sqlite3.Warning) as exc:
        if interrupted:
            conn._tainted = True
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise Problem("sql", str(exc), 422) from exc
    finally:
        conn.set_progress_handler(None, 0)
    ms = round((time.monotonic() - started) * 1000)
    blobs = any(isinstance(value, bytes) for row in rows for value in row)
    kind = STATEMENT.match(sql)
    explain = bool(kind and kind[1].lower().startswith("explain"))
    result = {
        "columns": [d[0] for d in cursor.description or ()],
        "rows": [
            [
                json_value(value).replace(conn.private_prefix, "").replace(conn._fn_prefix, "")
                if isinstance(value, str) and explain
                else json_value(value)
                for value in row
            ]
            for row in rows[:cap]
        ],
        "row_count": min(len(rows), cap),
        "truncated": len(rows) > cap,
        "ms": ms,
    }
    if blobs:
        result["note"] = "BLOB values are base64"
    return result


def install_sql(app, store, auth):
    audit = Audit(store)
    pool = SqlPool()
    limiter = TokenBucket()
    store._sql_pool = pool
    weakref.finalize(store, pool.close)
    app.state.sql_pool = pool
    app.state.sql_rate_limiter = limiter

    @app.post("/api/v2/sql")
    def query(request: Request, body: Query):
        who = request.state.identity
        if not STATEMENT.match(body.sql):
            raise Problem("sql", "Send one SELECT, WITH or EXPLAIN QUERY PLAN statement", 422)
        if who.role != "runner":
            limiter.take(who.actor)
        # Audit metadata cannot copy literals or SQL error excerpts from private work.
        detail = {
            "query_hash": hashlib.sha256(body.sql.encode()).hexdigest(),
            "query_bytes": len(body.sql.encode()),
            "rows": 0,
            "ms": 0,
            "error": None,
        }
        audited = True
        try:
            with store.read() as c:
                principal = who
                if who.role == "runner":
                    # Only a personal Mac reads SQL with its registering person's rights.
                    row = c.execute("SELECT owner_id,platform FROM runners WHERE id=?", (who.runner_id,)).fetchone()
                    if not str(row["platform"] or "").startswith("darwin"):
                        raise Problem(
                            "forbidden",
                            "Only a Mac's runner credential reads as its operator; use a personal API token",
                            403,
                        )
                    principal = auth.identity_for_actor(c, "human:" + row["owner_id"])
                    try:
                        limiter.take(principal.actor)
                    except Problem:
                        audited = False
                        raise
                    detail["as"] = principal.actor
                cap, seconds = LIMITS[principal.role]
                if body.max_rows:
                    cap = min(cap, body.max_rows)
                conn = connect(
                    store.settings.db_path,
                    c,
                    auth,
                    principal,
                    seconds=seconds,
                    statement=body.sql,
                    pool=pool,
                    pool_age=store.pool_age,
                    assistant_bot=store.settings.assistant_bot,
                )
                try:
                    result = run(conn, body.sql, body.params, cap, seconds)
                    detail.update(rows=result["row_count"], ms=result["ms"])
                    return result
                finally:
                    conn.close()
        except Problem as exc:
            detail["error"] = exc.code
            raise
        finally:
            if audited:
                audit.record(who.actor, detail)
