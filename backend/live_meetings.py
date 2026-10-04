"""Internal text-only meetings, scoped bot participation, deterministic router windows and SSE replay."""

import asyncio
import json
import re
import uuid

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictStr, model_validator

from clients import judge as J
from . import providers
from .judge import DAILY_CALLS, fallback_engine, used_today
from .imports import MeetingImport
from .models import Contract
from .store import H, Problem, encode
from .views import human_only


class Connect(Contract):
    title: str = Field(default="Live meeting", min_length=1, max_length=200)
    client_id: str | None = Field(default=None, min_length=1, max_length=120,
                                  pattern=r"^[A-Za-z0-9_.:@/-]+$")
    fallback_window_seconds: int = Field(default=30, ge=30, le=45)
    threshold: float = Field(default=0.7, ge=0.5, le=1)

    @model_validator(mode="after")
    def nonblank_title(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("Meeting title is required")
        return self


class Chunk(Contract):
    seq: int = Field(ge=1)
    speaker: str = Field(default="", max_length=200)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: StrictStr = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def valid_span(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Transcript text is required")
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be at or after start_ms")
        return self


class ChunkBatch(Contract):
    chunks: list[Chunk] = Field(min_length=1, max_length=500)


class Correction(Contract):
    revision: int = Field(ge=2)
    speaker: str = Field(default="", max_length=200)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: StrictStr = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def valid_span(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Correction text is required")
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be at or after start_ms")
        return self


class Join(Contract):
    pass


class AttachBots(Contract):
    bots: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_bots(self):
        self.bots = list(dict.fromkeys(s.strip().lower() for s in self.bots if s.strip()))
        if not self.bots:
            raise ValueError("Choose at least one org-chart bot")
        return self


class Chat(Contract):
    text: StrictStr = Field(min_length=1, max_length=20_000)
    at_ms: int | None = Field(default=None, ge=0)
    transcript_seq: int | None = Field(default=None, ge=1)
    turn_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def nonblank(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Chat text is required")
        return self


class Control(Contract):
    action: str = Field(pattern=r"^(pause|resume|disconnect|end)$")


class ClaimTurn(Contract):
    pass


def _event(c, rid, kind, payload):
    row = c.execute("SELECT next_event_id FROM live_meetings WHERE id=?", (rid,)).fetchone()
    if not row:
        raise Problem("not_found", "Live meeting not found", 404)
    event_id = row[0] + 1
    created = H.now()
    c.execute("UPDATE live_meetings SET next_event_id=?,updated=? WHERE id=?", (event_id, created, rid))
    c.execute("INSERT INTO live_meeting_events VALUES(?,?,?,?,?)",
              (rid, event_id, kind, encode(payload), created))
    return event_id


def _meeting(c, rid):
    row = c.execute("SELECT * FROM live_meetings WHERE id=?", (rid,)).fetchone()
    if not row:
        raise Problem("not_found", "Live meeting not found", 404)
    return dict(row)


def _require_read(c, auth, who, rid):
    row = _meeting(c, rid)
    if who.role in ("human", "owner"):
        auth.domain(who)
    elif who.role == "bot":
        slug = H.actor_id(who.actor)
        if not c.execute("SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, slug)).fetchone():
            raise Problem("forbidden", "This bot is not attached to the live meeting", 403)
    else:
        raise Problem("forbidden", "Live meetings are available to team members and attached bots", 403)
    return row


def _connector(c, auth, who, rid, *, states=None):
    human_only(who)
    auth.domain(who)
    row = _meeting(c, rid)
    if who.actor != row["owner_actor"]:
        raise Problem("forbidden", "Only the person who connected this meeting can control its transcript", 403)
    if states and row["state"] not in states:
        raise Problem("meeting_state", f"This operation requires a { ' or '.join(states) } meeting", 409)
    return row


def _view(c, auth, who, rid):
    row = _require_read(c, auth, who, rid)
    chunks = [dict(r) for r in c.execute(
        "SELECT seq,revision,speaker,start_ms,end_ms,text,created FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq",
        (rid,))]
    chat = [dict(r) for r in c.execute(
        "SELECT id,actor,role,text,at_ms,transcript_seq,created FROM live_meeting_chat WHERE meeting_id=? ORDER BY created,id",
        (rid,))]
    bots = [dict(r) for r in c.execute("SELECT bot,joined_by,joined_at FROM live_meeting_bots WHERE meeting_id=? ORDER BY bot", (rid,))]
    humans = []
    for r in c.execute("SELECT actor,joined_at FROM live_meeting_humans WHERE meeting_id=? ORDER BY joined_at,actor", (rid,)):
        person = H.human(c, H.actor_id(r["actor"]))
        email = (person or {}).get("email") or "" if who.role != "bot" else ""
        humans.append({"actor": r["actor"], "name": (person or {}).get("name") or H.actor_id(r["actor"]),
                       "email": email, "joined_at": r["joined_at"]})
    windows = [dict(r) for r in c.execute(
        "SELECT window_index,start_ms,end_ms,status,outcome,trace_json,started,finished FROM live_meeting_windows "
        "WHERE meeting_id=? ORDER BY window_index", (rid,))]
    for window in windows:
        window["trace"] = json.loads(window.pop("trace_json"))
    pending_turns = [dict(r) for r in c.execute(
        "SELECT id,bot,window_index,source_key,transcript_seq,status,skip_reason,created FROM live_meeting_turns "
        "WHERE meeting_id=? ORDER BY created,id", (rid,))]
    bypasses = []
    for event in c.execute("SELECT event_id,created,payload_json FROM live_meeting_events "
                            "WHERE meeting_id=? AND type='meeting.router' ORDER BY event_id", (rid,)):
        payload = json.loads(event["payload_json"])
        if payload.get("bypass") == "named":
            bypasses.append({"event_id": event["event_id"], "created": event["created"], **payload})
    return {
        "id": row["id"], "title": row["title"], "state": row["state"], "owner_actor": row["owner_actor"],
        "seq": row["seq"], "event_id": row["next_event_id"], "window_ms": row["window_ms"],
        "cooldown_ms": row["cooldown_ms"], "threshold": row["threshold"],
        "created": row["created"], "started_at": row["started_at"], "ended_at": row["ended_at"],
        "imported_meeting_id": row["imported_meeting_id"], "humans": humans, "bots": bots,
        "chunks": chunks, "chat": chat,
        "router": {"windows": windows, "turns": pending_turns, "bypasses": bypasses},
    }


def _active_bot(c, slug):
    bot = H.bot(c, slug)
    if not bot:
        raise Problem("not_found", f"Org-chart bot {slug} was not found", 404)
    if bot.get("state") != "active":
        raise Problem("bot_unavailable", f"{slug} is not an active org-chart bot", 409)
    return bot


def _attached(c, rid):
    return c.execute("SELECT bot,last_reply_ms FROM live_meeting_bots WHERE meeting_id=? ORDER BY bot", (rid,)).fetchall()


def _enqueue(c, rid, bots, *, source_key, window_index=None, transcript_seq=None, at_ms=None, cap=3):
    """Persist concurrent bot turns; cooldown and busy checks are meeting-scoped."""
    skipped, selected = [], []
    meeting = _meeting(c, rid)
    if at_ms is None:
        at_ms = c.execute("SELECT COALESCE(MAX(end_ms),0) FROM live_meeting_chunks WHERE meeting_id=?",
                          (rid,)).fetchone()[0]
    for slug in bots:
        attached = c.execute("SELECT last_reply_ms FROM live_meeting_bots WHERE meeting_id=? AND bot=?",
                             (rid, slug)).fetchone()
        if not attached:
            skipped.append({"bot": slug, "reason": "not_attached"})
            continue
        busy = c.execute("SELECT 1 FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND status IN ('pending','claimed') LIMIT 1",
                         (rid, slug)).fetchone()
        if busy:
            skipped.append({"bot": slug, "reason": "busy"})
            continue
        last = attached["last_reply_ms"]
        if last is not None and at_ms is not None and at_ms < last + meeting["cooldown_ms"]:
            skipped.append({"bot": slug, "reason": "cooldown"})
            continue
        if len(selected) >= cap:
            skipped.append({"bot": slug, "reason": "cap"})
            continue
        tid = str(uuid.uuid4())
        c.execute("INSERT OR IGNORE INTO live_meeting_turns "
                  "(id,meeting_id,bot,window_index,source_key,transcript_seq,status,created) VALUES(?,?,?,?,?,?,?,?)",
                  (tid, rid, slug, window_index, source_key, transcript_seq, "pending", H.now()))
        if c.execute("SELECT changes()").fetchone()[0]:
            selected.append({"id": tid, "bot": slug, "window_index": window_index,
                             "transcript_seq": transcript_seq})
    if selected:
        _event(c, rid, "meeting.bot_turn", {"turns": selected, "source_key": source_key})
    return selected, skipped


def _route_mentions(c, rid, text, *, source_key, transcript_seq=None, at_ms=None):
    rows = _attached(c, rid)
    names = []
    for row in rows:
        bot = H.bot(c, row["bot"])
        names.append({"bot": row["bot"], "name": (bot or {}).get("display_name") or (bot or {}).get("name") or row["bot"]})
    found = []
    folded = text.casefold()
    for row in names:
        for name in (row["bot"].replace("-", " "), row["name"]):
            name = str(name or "").casefold().strip()
            if name and re.search(r"(?<![\w-])@?" + re.escape(name) + r"(?![\w-])", folded):
                found.append(row["bot"])
                break
    if not found:
        return [], []
    selected, skipped = _enqueue(c, rid, found, source_key=source_key, transcript_seq=transcript_seq,
                                 at_ms=at_ms)
    _event(c, rid, "meeting.router", {"bypass": "named", "source_key": source_key,
                                       "targets": [v["bot"] for v in selected], "skipped": skipped,
                                       "trace": {"decision": "bypassed", "reason": "named mention"}})
    return selected, skipped


def _window_ms(app, body):
    # Jev is the optional TypeSafe decision provider. Without it, keep the configured 30–45 s
    # fallback so the router can collect more transcript context before using another provider.
    jev = bool(getattr(getattr(app, "state", None), "judge", None))
    return 15_000 if jev else body.fallback_window_seconds * 1000


def _decision(app, rid, index, transcript, bots, threshold, owner_actor):
    """One multi-bot decision using the existing calibrated decisions service."""
    if app.state.store.settings.rehearsal:
        return {"outcome": "pass", "targets": [],
                "trace": {"reason": "rehearsal_mode", "answers": {}, "threshold": threshold}}
    state = {"meeting_id": rid, "window_index": index, "transcript": transcript,
             "bots": [{"slug": r["bot"], "name": r["display_name"] or r["bot"]} for r in bots]}
    questions = {"reply_" + r["bot"]: {
        "type": "noul", "instructions": f"Should {r['display_name'] or r['bot']} reply to this live meeting window? "
                                            "Choose true only when a useful response from this teammate would help."}
        for r in bots}
    if not questions:
        return {"outcome": "pass", "targets": [], "trace": {"reason": "no attached bots", "answers": {}}}
    try:
        with app.state.store.read() as c:
            who = app.state.auth.identity_for_actor(c, owner_actor)
            used = used_today(c, owner_actor)
            if used >= DAILY_CALLS[who.role]:
                return {"outcome": "pass", "targets": [],
                        "trace": {"reason": "daily_decision_budget", "answers": {}, "threshold": threshold}}
            engine = (getattr(app.state, "live_meeting_decider", None) or getattr(app.state, "judge", None)
                      or fallback_engine(providers.load(c, app.state.store.settings)))
        if engine is None:
            return {"outcome": "pass", "targets": [], "trace": {"reason": "decisions service unconfigured", "answers": {}}}
        J.validate(state, questions, "live-meeting-route@1")
        result = engine(state, questions, "live-meeting-route@1")
        answers = J.check_answers(result.get("answers"), questions)
        targets = []
        scores = {}
        for row in bots:
            key = "reply_" + row["bot"]
            probability = float(answers[key]["noul"])
            scores[row["bot"]] = probability
            if probability >= threshold:
                targets.append((probability, row["bot"]))
        targets.sort(key=lambda pair: (-pair[0], pair[1]))
        trace = {"model": str(result.get("model") or ""), "ms": result.get("ms"),
                 "answers": scores, "threshold": threshold}
        return {"outcome": "route" if targets else "pass", "targets": [slug for _, slug in targets], "trace": trace}
    except Exception as exc:
        # A route failure is a hidden PASS with a durable trace; it must never turn into transcript text.
        return {"outcome": "pass", "targets": [],
                "trace": {"reason": "decision_error", "error": type(exc).__name__, "threshold": threshold}}


def _run_windows(app, rid, *, final=False):
    """Close every elapsed transcript window once. Model calls happen outside write transactions."""
    with app.state.store.read() as c:
        meeting = _meeting(c, rid)
        if meeting["state"] == "paused":
            return
        last = c.execute("SELECT COALESCE(MAX(window_index),-1) FROM live_meeting_windows "
                          "WHERE meeting_id=? AND status!='deciding'", (rid,)).fetchone()[0]
        chunks = [dict(r) for r in c.execute(
        "SELECT seq,speaker,start_ms,end_ms,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (rid,))]
        bots = []
        for r in c.execute("SELECT bm.bot,bc.description FROM live_meeting_bots bm "
                            "LEFT JOIN bot_config bc ON bc.bot=bm.bot WHERE bm.meeting_id=? ORDER BY bm.bot", (rid,)):
            bot = H.bot(c, r["bot"])
            bots.append({"bot": r["bot"], "display_name": (bot or {}).get("name") or r["bot"],
                         "description": r["description"] or ""})
    if not chunks:
        return
    latest = max(row["end_ms"] for row in chunks)
    window_ms = meeting["window_ms"]
    index = last + 1
    while index * window_ms < latest or (final and index * window_ms < latest + 1):
        start_ms, end_ms = index * window_ms, (index + 1) * window_ms
        included = [row for row in chunks if start_ms <= row["start_ms"] < end_ms]
        if not included:
            index += 1
            continue
        if latest < end_ms and not final:
            break
        with app.state.store.transaction() as c:
            current = c.execute("SELECT status,started FROM live_meeting_windows WHERE meeting_id=? AND window_index=?",
                                (rid, index)).fetchone()
            if current:
                elapsed = H.parse_ts(H.now()) - H.parse_ts(current["started"]) if current["started"] else None
                if current["status"] == "deciding" and elapsed and elapsed.total_seconds() > 60:
                    c.execute("UPDATE live_meeting_windows SET status='interrupted',outcome='pass',"
                              "trace_json=?,finished=? WHERE meeting_id=? AND window_index=?",
                              (encode({"reason": "interrupted decision; not retried"}), H.now(), rid, index))
                    _event(c, rid, "meeting.router", {"window_index": index, "outcome": "pass",
                                                       "trace": {"reason": "interrupted decision; not retried"}})
                    index += 1
                    continue
                break
            c.execute("INSERT INTO live_meeting_windows(meeting_id,window_index,start_ms,end_ms,status,started) "
                      "VALUES(?,?,?,?,?,?)", (rid, index, start_ms, end_ms, "deciding", H.now()))
        outcome = _decision(app, rid, index, included, bots, meeting["threshold"], meeting["owner_actor"])
        with app.state.store.transaction() as c:
            current = c.execute("SELECT status FROM live_meeting_windows WHERE meeting_id=? AND window_index=?",
                                (rid, index)).fetchone()
            if not current or current[0] != "deciding":
                index += 1
                continue
            current_state = _meeting(c, rid)["state"]
            if current_state == "paused" or (current_state == "ended" and not final):
                reason = "meeting_paused_during_decision" if current_state == "paused" else "meeting_ended_during_decision"
                outcome = {"outcome": "pass", "targets": [],
                           "trace": {"reason": reason, "threshold": meeting["threshold"]}}
            selected, skipped = _enqueue(c, rid, outcome["targets"], source_key=f"window:{index}",
                                         window_index=index, transcript_seq=included[-1]["seq"],
                                         at_ms=end_ms)
            trace = {**outcome["trace"], "selected": [r["bot"] for r in selected], "skipped": skipped,
                     "chunk_range": [included[0]["seq"], included[-1]["seq"]]}
            if outcome["trace"].get("model"):
                H.event(c, meeting["owner_actor"], "judge.call", "live-meeting-route@1", {
                    "label": "live-meeting-route@1", "questions": len(bots), "types": ["noul"],
                    "ms": outcome["trace"].get("ms"), "model": outcome["trace"].get("model"),
                    "answers": outcome["trace"].get("answers", {})})
            # PASS remains in Router trace only; only selected bot turns enter Chat.
            c.execute("UPDATE live_meeting_windows SET status='complete',outcome=?,trace_json=?,finished=? "
                      "WHERE meeting_id=? AND window_index=?",
                      ("route" if selected else "pass", encode(trace), H.now(), rid, index))
            _event(c, rid, "meeting.router", {"window_index": index, "start_ms": start_ms,
                                               "end_ms": end_ms, "outcome": "route" if selected else "pass",
                                               "decision_ms": outcome["trace"].get("ms"), "trace": trace})
        index += 1


def _reply(c, rid, who, turn_id, text, *, at_ms=None, transcript_seq=None):
    turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=? AND meeting_id=?", (turn_id, rid)).fetchone()
    if not turn or who.role != "bot" or H.actor_id(who.actor) != turn["bot"]:
        raise Problem("forbidden", "This bot does not own the requested meeting turn", 403)
    if not c.execute("SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, turn["bot"])).fetchone():
        raise Problem("forbidden", "This bot is not attached to the live meeting", 403)
    if turn["status"] == "replied":
        existing = c.execute("SELECT * FROM live_meeting_chat WHERE meeting_id=? AND turn_id=?",
                             (rid, turn_id)).fetchone()
        if existing:
            return dict(existing)
        raise Problem("turn_state", "This bot turn already has a reply", 409)
    if turn["status"] not in ("pending", "claimed"):
        raise Problem("turn_state", "This bot turn is no longer available", 409)
    if turn["status"] != "claimed":
        raise Problem("turn_claim", "Claim this meeting turn before replying", 409)
    current = _meeting(c, rid)
    if current["state"] == "ended":
        raise Problem("meeting_state", "Ended meetings cannot receive bot replies", 409)
    if transcript_seq is None:
        transcript_seq = turn["transcript_seq"]
    if transcript_seq is not None and not c.execute(
            "SELECT 1 FROM live_meeting_chunks WHERE meeting_id=? AND seq=?", (rid, transcript_seq)).fetchone():
        raise Problem("transcript_ref", "Unknown transcript sequence", 422)
    timeline_ms = c.execute("SELECT COALESCE(MAX(end_ms),0) FROM live_meeting_chunks WHERE meeting_id=?",
                            (rid,)).fetchone()[0]
    at_ms = max(at_ms, timeline_ms) if at_ms is not None else timeline_ms
    created = H.now()
    cid = str(uuid.uuid4())
    c.execute("INSERT INTO live_meeting_chat(id,meeting_id,actor,role,text,at_ms,transcript_seq,turn_id,created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (cid, rid, who.actor, "bot", text, at_ms, transcript_seq, turn_id, created))
    c.execute("UPDATE live_meeting_turns SET status='replied',replied_at=? WHERE id=?", (created, turn_id))
    c.execute("UPDATE live_meeting_bots SET last_reply_ms=? WHERE meeting_id=? AND bot=?", (at_ms, rid, turn["bot"]))
    _event(c, rid, "meeting.bot_reply", {"id": cid, "actor": who.actor, "text": text,
                                         "at_ms": at_ms, "transcript_seq": transcript_seq,
                                         "turn_id": turn_id, "window_index": turn["window_index"]})
    return dict(c.execute("SELECT * FROM live_meeting_chat WHERE id=?", (cid,)).fetchone())


def install_live_meetings(app, store, auth, mutate):
    @app.post("/api/v2/live-meetings")
    def connect(request: Request, body: Connect):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        def work(c):
            if body.client_id:
                existing = c.execute("SELECT id FROM live_meetings WHERE owner_actor=? AND client_id=?",
                                     (who.actor, body.client_id)).fetchone()
                if existing:
                    return _view(c, auth, who, existing[0])
            rid = str(uuid.uuid4())
            stamp = H.now()
            window_ms = _window_ms(app, body)
            c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,client_id,window_ms,threshold,created,updated,started_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (rid, who.actor, body.title, "live", body.client_id, window_ms, body.threshold,
                       stamp, stamp, stamp))
            c.execute("INSERT INTO live_meeting_humans VALUES(?,?,?)", (rid, who.actor, stamp))
            _event(c, rid, "meeting.state", {"state": "live", "connected_by": who.actor,
                                              "visibility": "team", "window_ms": window_ms})
            return _view(c, auth, who, rid)
        return mutate(request, body, work)

    @app.get("/api/v2/live-meetings")
    def listing(request: Request):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        with store.read() as c:
            rows = c.execute("SELECT id FROM live_meetings WHERE state!='ended' ORDER BY updated DESC,id").fetchall()
            meetings = [_view(c, auth, who, row[0]) for row in rows]
            return {"meetings": meetings, "count": len(meetings)}

    @app.get("/api/v2/live-meetings/bot-candidates")
    def bot_candidates(request: Request):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        with store.read() as c:
            visible = auth.bot_accesses(c, who)
            rows = []
            for bot in H.bots(c, state="active"):
                slug = bot["slug"]
                if not visible.get(slug, auth.FULL)["see"]:
                    continue
                config = c.execute("SELECT team,description FROM bot_config WHERE bot=?", (slug,)).fetchone()
                rows.append({"slug": slug, "name": bot.get("name") or slug,
                             "description": config["description"] if config else "",
                             "team": config["team"] if config else None})
            return {"bots": rows}

    @app.get("/api/v2/live-meetings/{rid}")
    def detail(request: Request, rid: str):
        with store.read() as c:
            return _view(c, auth, request.state.identity, rid)

    @app.post("/api/v2/live-meetings/{rid}/join")
    def join(request: Request, rid: str, body: Join):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        def work(c):
            row = _meeting(c, rid)
            if row["state"] == "ended":
                raise Problem("meeting_state", "Ended meetings cannot be joined", 409)
            cursor = c.execute("INSERT OR IGNORE INTO live_meeting_humans VALUES(?,?,?)", (rid, who.actor, H.now()))
            if cursor.rowcount:
                _event(c, rid, "meeting.joined", {"actor": who.actor})
            return _view(c, auth, who, rid)
        return mutate(request, body, work)

    @app.post("/api/v2/live-meetings/{rid}/chunks")
    def chunks(request: Request, rid: str, body: ChunkBatch):
        who = request.state.identity
        def work(c):
            row = _connector(c, auth, who, rid)
            expected = row["seq"] + 1
            accepted, added = [], []
            previous_start = c.execute("SELECT COALESCE(MAX(start_ms),-1) FROM live_meeting_chunks WHERE meeting_id=?",
                                       (rid,)).fetchone()[0]
            for chunk in body.chunks:
                data = (chunk.speaker, chunk.start_ms, chunk.end_ms, chunk.text)
                found = c.execute("SELECT speaker,start_ms,end_ms,text FROM live_meeting_chunks WHERE meeting_id=? AND seq=?",
                                  (rid, chunk.seq)).fetchone()
                if found:
                    known = c.execute("SELECT 1 FROM live_meeting_chunk_versions WHERE meeting_id=? AND seq=? "
                                      "AND speaker=? AND start_ms=? AND end_ms=? AND text=? LIMIT 1",
                                      (rid, chunk.seq, *data)).fetchone()
                    if not known:
                        raise Problem("sequence_conflict", f"Sequence {chunk.seq} already has different text", 409)
                    accepted.append(chunk.seq)
                    continue
                if row["state"] != "live":
                    raise Problem("meeting_state", f"A {row['state']} meeting cannot accept new transcript text", 409)
                if chunk.seq != expected:
                    raise Problem("sequence_gap", f"Expected sequence {expected}", 409, extra={"expected_seq": expected})
                if chunk.start_ms < previous_start:
                    raise Problem("sequence_order", "Transcript start times must not go backwards", 422)
                stamp = H.now()
                c.execute("INSERT INTO live_meeting_chunks(meeting_id,seq,revision,speaker,start_ms,end_ms,text,created) "
                          "VALUES(?,?,?,?,?,?,?,?)", (rid, chunk.seq, 1, chunk.speaker, chunk.start_ms,
                                                       chunk.end_ms, chunk.text, stamp))
                c.execute("INSERT INTO live_meeting_chunk_versions VALUES(?,?,?,?,?,?,?,?,?)",
                          (rid, chunk.seq, 1, chunk.speaker, chunk.start_ms, chunk.end_ms, chunk.text,
                           who.actor, stamp))
                c.execute("UPDATE live_meetings SET seq=?,updated=? WHERE id=?", (chunk.seq, stamp, rid))
                _event(c, rid, "meeting.chunk", {"seq": chunk.seq, "speaker": chunk.speaker,
                                                  "start_ms": chunk.start_ms, "end_ms": chunk.end_ms,
                                                  "text": chunk.text, "revision": 1})
                _route_mentions(c, rid, chunk.text, source_key=f"chunk:{chunk.seq}",
                                transcript_seq=chunk.seq, at_ms=chunk.end_ms)
                accepted.append(chunk.seq)
                added.append(chunk.seq)
                expected += 1
                previous_start = chunk.start_ms
            return {"id": rid, "state": row["state"], "seq": row["seq"] + len(added),
                    "accepted": accepted, "event_id": _meeting(c, rid)["next_event_id"]}
        result = mutate(request, body, work)
        _run_windows(app, rid)
        return result

    @app.post("/api/v2/live-meetings/{rid}/chunks/{seq}/corrections")
    def correct_chunk(request: Request, rid: str, seq: int, body: Correction):
        who = request.state.identity
        def work(c):
            _connector(c, auth, who, rid, states=("live", "paused"))
            current = c.execute("SELECT * FROM live_meeting_chunks WHERE meeting_id=? AND seq=?", (rid, seq)).fetchone()
            if not current:
                raise Problem("not_found", f"Transcript sequence {seq} was not found", 404)
            data = (body.speaker, body.start_ms, body.end_ms, body.text)
            version = c.execute("SELECT speaker,start_ms,end_ms,text FROM live_meeting_chunk_versions "
                                "WHERE meeting_id=? AND seq=? AND revision=?", (rid, seq, body.revision)).fetchone()
            if version:
                if tuple(version) != data:
                    raise Problem("revision_conflict", f"Revision {body.revision} already has different content", 409)
                return {"id": rid, "seq": seq, "revision": body.revision, "replayed": True,
                        "event_id": _meeting(c, rid)["next_event_id"]}
            expected = current["revision"] + 1
            if body.revision != expected:
                raise Problem("revision_gap", f"Expected revision {expected}", 409, extra={"expected_revision": expected})
            previous = c.execute("SELECT MAX(start_ms) FROM live_meeting_chunks WHERE meeting_id=? AND seq<?",
                                 (rid, seq)).fetchone()[0]
            following = c.execute("SELECT MIN(start_ms) FROM live_meeting_chunks WHERE meeting_id=? AND seq>?",
                                  (rid, seq)).fetchone()[0]
            if previous is not None and body.start_ms < previous or following is not None and body.start_ms > following:
                raise Problem("sequence_order", "A correction must preserve transcript sequence time order", 422)
            stamp = H.now()
            c.execute("INSERT INTO live_meeting_chunk_versions VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, seq, body.revision, body.speaker, body.start_ms, body.end_ms, body.text, who.actor, stamp))
            c.execute("UPDATE live_meeting_chunks SET revision=?,speaker=?,start_ms=?,end_ms=?,text=?,created=? "
                      "WHERE meeting_id=? AND seq=?",
                      (body.revision, body.speaker, body.start_ms, body.end_ms, body.text, stamp, rid, seq))
            _event(c, rid, "meeting.chunk_corrected", {"seq": seq, "revision": body.revision,
                                                       "speaker": body.speaker, "start_ms": body.start_ms,
                                                       "end_ms": body.end_ms, "text": body.text,
                                                       "corrected_by": who.actor})
            return {"id": rid, "seq": seq, "revision": body.revision, "replayed": False,
                    "event_id": _meeting(c, rid)["next_event_id"]}
        result = mutate(request, body, work)
        _run_windows(app, rid)
        return result

    @app.post("/api/v2/live-meetings/{rid}/bots")
    def attach_bots(request: Request, rid: str, body: AttachBots):
        who = request.state.identity
        def work(c):
            human_only(who)
            auth.domain(who)
            meeting = _meeting(c, rid)
            if meeting["state"] not in ("live", "paused"):
                raise Problem("meeting_state", "Bots can only be attached to a live or paused meeting", 409)
            if not c.execute("SELECT 1 FROM live_meeting_humans WHERE meeting_id=? AND actor=?",
                             (rid, who.actor)).fetchone():
                raise Problem("forbidden", "Join this live meeting before attaching bots", 403)
            attached, added = [], []
            for slug in body.bots:
                _active_bot(c, slug)
                if not auth.bot_access(c, who, slug)["see"]:
                    raise Problem("forbidden", f"You cannot see org-chart bot {slug}", 403)
                stamp = H.now()
                cursor = c.execute("INSERT OR IGNORE INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,?,?,?)",
                                   (rid, slug, who.actor, stamp))
                if cursor.rowcount:
                    added.append(slug)
                attached.append(slug)
            if added:
                _event(c, rid, "meeting.bot_joined", {"bots": added, "joined_by": who.actor,
                                                       "rights": "meeting_read_and_reply"})
            return {"id": rid, "bots": attached, "added": added, "event_id": _meeting(c, rid)["next_event_id"]}
        return mutate(request, body, work)

    @app.post("/api/v2/live-meetings/{rid}/control")
    def control(request: Request, rid: str, body: Control):
        who = request.state.identity
        def work(c):
            row = _connector(c, auth, who, rid)
            target = {"pause": "paused", "disconnect": "paused", "resume": "live", "end": "ended"}[body.action]
            if row["state"] == target or (body.action == "end" and row["state"] == "ended"):
                return _view(c, auth, who, rid)
            allowed = {"pause": ("live",), "disconnect": ("live",), "resume": ("paused",),
                       "end": ("live", "paused")}[body.action]
            if row["state"] not in allowed:
                raise Problem("meeting_state", f"Cannot {body.action} a {row['state']} meeting", 409)
            ended = H.now() if body.action == "end" else row["ended_at"]
            c.execute("UPDATE live_meetings SET state=?,ended_at=?,updated=? WHERE id=?",
                      (target, ended, H.now(), rid))
            _event(c, rid, "meeting.state", {"state": target, "by": who.actor,
                                               "action": body.action})
            return _view(c, auth, who, rid)
        result = mutate(request, body, work)
        if body.action == "end":
            _run_windows(app, rid, final=True)
        elif body.action == "resume":
            _run_windows(app, rid)
        return result

    @app.post("/api/v2/live-meetings/{rid}/chat")
    def chat(request: Request, rid: str, body: Chat):
        who = request.state.identity
        def work(c):
            _require_read(c, auth, who, rid)
            row = _meeting(c, rid)
            if row["state"] == "ended":
                raise Problem("meeting_state", "Ended meetings cannot receive chat", 409)
            if who.role == "bot":
                if not body.turn_id:
                    raise Problem("turn", "An attached bot reply must name its live meeting turn", 422)
                reply = _reply(c, rid, who, body.turn_id, body.text, at_ms=body.at_ms,
                               transcript_seq=body.transcript_seq)
                return {"message": reply, "event_id": _meeting(c, rid)["next_event_id"]}
            if who.role not in ("human", "owner"):
                raise Problem("forbidden", "Only joined people and attached bots can chat", 403)
            if not c.execute("SELECT 1 FROM live_meeting_humans WHERE meeting_id=? AND actor=?",
                             (rid, who.actor)).fetchone():
                raise Problem("forbidden", "Join this live meeting before chatting", 403)
            if body.turn_id:
                raise Problem("turn", "People do not reply to bot turn ids", 422)
            if body.transcript_seq is not None and not c.execute(
                    "SELECT 1 FROM live_meeting_chunks WHERE meeting_id=? AND seq=?", (rid, body.transcript_seq)).fetchone():
                raise Problem("transcript_ref", "Unknown transcript sequence", 422)
            cid, stamp = str(uuid.uuid4()), H.now()
            at_ms = body.at_ms
            c.execute("INSERT INTO live_meeting_chat(id,meeting_id,actor,role,text,at_ms,transcript_seq,created) "
                      "VALUES(?,?,?,?,?,?,?,?)", (cid, rid, who.actor, "human", body.text, at_ms,
                                                   body.transcript_seq, stamp))
            _event(c, rid, "meeting.chat", {"id": cid, "actor": who.actor, "role": "human", "text": body.text,
                                            "at_ms": at_ms, "transcript_seq": body.transcript_seq})
            if row["state"] == "live":
                _route_mentions(c, rid, body.text, source_key=f"chat:{cid}", transcript_seq=body.transcript_seq,
                                at_ms=None)
            return {"message": dict(c.execute("SELECT * FROM live_meeting_chat WHERE id=?", (cid,)).fetchone()),
                    "event_id": _meeting(c, rid)["next_event_id"]}
        return mutate(request, body, work)

    @app.get("/api/v2/live-meetings/{rid}/turns")
    def turns(request: Request, rid: str):
        who = request.state.identity
        if who.role != "bot":
            raise Problem("identity", "Only attached bots pull meeting turns", 403)
        with store.read() as c:
            _require_read(c, auth, who, rid)
            rows = c.execute("SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND status='pending' "
                             "ORDER BY created,id", (rid, H.actor_id(who.actor))).fetchall()
            view = _view(c, auth, who, rid)
            return {"turns": [{"id": r["id"], "window_index": r["window_index"],
                               "transcript_seq": r["transcript_seq"], "source_key": r["source_key"],
                               "transcript": [chunk for chunk in view["chunks"]
                                              if r["transcript_seq"] is None or chunk["seq"] <= r["transcript_seq"]],
                               "chat": view["chat"]} for r in rows]}

    @app.post("/api/v2/live-meetings/{rid}/turns/{turn_id}/claim")
    def claim_turn(request: Request, rid: str, turn_id: str, body: ClaimTurn):
        who = request.state.identity
        if who.role != "bot":
            raise Problem("identity", "Only attached bots claim meeting turns", 403)
        def work(c):
            _require_read(c, auth, who, rid)
            turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=? AND meeting_id=? AND bot=?",
                             (turn_id, rid, H.actor_id(who.actor))).fetchone()
            if not turn:
                raise Problem("not_found", "Meeting turn not found", 404)
            if turn["status"] == "claimed":
                return {"id": turn_id, "status": "claimed"}
            if turn["status"] != "pending":
                raise Problem("turn_state", "This meeting turn is no longer available", 409)
            c.execute("UPDATE live_meeting_turns SET status='claimed',claimed_at=? WHERE id=?", (H.now(), turn_id))
            _event(c, rid, "meeting.bot_turn_claimed", {"turn_id": turn_id, "bot": turn["bot"]})
            return {"id": turn_id, "status": "claimed"}
        return mutate(request, body, work)

    @app.post("/api/v2/live-meetings/{rid}/finalize")
    def finalize(request: Request, rid: str, body: Join):
        who = request.state.identity
        def work(c):
            row = _connector(c, auth, who, rid, states=("ended",))
            if row["imported_meeting_id"]:
                return {"id": rid, "meeting_id": row["imported_meeting_id"], "existing": True,
                        "changed": False, "event_id": row["next_event_id"]}
            chunks = [dict(r) for r in c.execute(
                "SELECT speaker,start_ms,end_ms,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (rid,))]
            if not chunks:
                raise Problem("transcript", "A live meeting needs transcript text before finalization", 409)
            duration_ms = max(chunk["end_ms"] for chunk in chunks)
            body_import = MeetingImport(title=row["title"], started_at=row["started_at"],
                                        duration_seconds=duration_ms / 1000, source="tico-live", external_id=rid,
                                        transcript=[{"speaker": chunk["speaker"], "start_ms": chunk["start_ms"],
                                                     "end_ms": chunk["end_ms"], "text": chunk["text"]}
                                                    for chunk in chunks], review="live")
            result = app.state.import_meeting(c, who, body_import, [])
            imported_id = result["id"]
            c.execute("UPDATE live_meetings SET imported_meeting_id=?,updated=? WHERE id=?",
                      (imported_id, H.now(), rid))
            _event(c, rid, "meeting.finalized", {"id": rid, "meeting_id": imported_id,
                                                  "source": "tico-live", "external_id": rid})
            return {"id": rid, "meeting_id": imported_id, "existing": result.get("existing", False),
                    "changed": result.get("changed", False), "event_id": _meeting(c, rid)["next_event_id"]}
        return mutate(request, body, work)

    @app.get("/api/v2/live-meetings/{rid}/events")
    async def events(request: Request, rid: str, after: int = 0):
        who = request.state.identity
        if request.headers.get("last-event-id") and after == 0:
            try:
                after = int(request.headers["last-event-id"])
            except ValueError:
                raise Problem("event_cursor", "Last-Event-ID must be a non-negative integer", 400) from None
        if after < 0:
            raise Problem("event_cursor", "Event cursor must be non-negative", 400)
        cursor = max(after, 0)
        with store.read() as c:
            _require_read(c, auth, who, rid)

        def poll(current):
            auth.authenticate(request.headers)
            with store.read() as c:
                _require_read(c, auth, who, rid)
                rows = c.execute("SELECT event_id,type,payload_json FROM live_meeting_events "
                                 "WHERE meeting_id=? AND event_id>? ORDER BY event_id LIMIT 200",
                                 (rid, current)).fetchall()
                return [dict(r) for r in rows]

        async def generate():
            current = cursor
            # Reconnectable, bounded SSE; clients reconnect with the last event id.
            for _ in range(55):
                if await request.is_disconnected():
                    return
                try:
                    rows = await asyncio.to_thread(poll, current)
                except Problem:
                    yield "event: expired\ndata: {}\n\n"
                    return
                for row in rows:
                    current = row["event_id"]
                    payload = json.loads(row["payload_json"])
                    yield f"id: {current}\nevent: {row['type']}\ndata: {encode(payload)}\n\n"
                yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
