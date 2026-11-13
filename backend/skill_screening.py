"""Skill screening: every proposed skill and update is checked before an admin can approve it (docs/skills.md "Screening").

A background job, not a door. The scheduler's tick starts a worker when a proposal waits (`kick`); the worker claims one
proposal at a time with a conditional UPDATE (`screen_status` pending -> screening), so two passes never screen the same
row, and writes one `skill_screenings` row plus the proposal's `screen_status` when it is done. Approval
(backend/skills.py `approve`) reads that row and refuses anything but a `screened` one for the exact sha and base.

What the screen is made of:

- **Patterns** (code, free, deterministic): hidden Unicode, HTML comments, encoded blobs, URLs, pipe-to-shell, reads of
  secrets, writes to agent settings, hooks, other skills or AGENTS.md/CLAUDE.md, network calls in scripts, and the
  injection phrases screening.py already knows. Each hit names its file and line. A hidden-Unicode, secret-read or
  pipe-to-shell hit raises the risk to at least medium whatever the model says.
- **The model**: Gateway Decide High alone (backend/judge.py `strict_engine`), question set `questions/skill-screen.json`,
  with evidence (a summary and findings by file and line). The whole bundle is read, scripts included, each file fenced as
  data, with the diff against the base for an update. No fallback to another model: a lower-quality answer is not a screen.

Unlike screening.py's doors there is no switch, no length bypass, no cut and no fail-open: a bundle too big for the state
is `not_screened` (`oversize`), and no model, the spend cap, a timeout or an error is `not_screened` with that reason until
someone screens it again. A proposal is never `screened` without a model answer. A `screened` row for the same sha, base and
question set version is reused instead of paying again; a new question set version sends pending proposals back to be
screened (approved versions are not re-gated). Events name the proposal, the skill and the outcome, never a file's text.
"""

import difflib
import json
import logging
import re
import threading
import time
import uuid

from clients import judge as J
from clients import skills as bundles
from . import screening
from .judge import STRICT_SECONDS, over_spend_cap, strict_engine
from .store import H

LOG = logging.getLogger("tico.skill_screening")

SET = "skill-screen"
JOB = "skill_screening"
BATCH = 3  # proposals claimed per pass: each may take a model call of up to 90 seconds
LEASE_MINUTES = 10  # a claim older than this was left by a stopped worker and is taken again
VERDICTS = ("safe", "review", "unsafe")
RISKS = ("low", "medium", "high")
FLAGS = (
    "description_mismatch",
    "hidden_or_conditional_behaviour",
    "exfiltration",
    "permission_or_safety_bypass",
    "instructions_to_other_agents_or_reviewers",
)
FLAG_AT = 0.5  # a concern at or over this raises the risk to at least medium (the set's `flag` threshold)
FLOOR_KINDS = ("hidden_unicode", "secret_read", "pipe_to_shell")
PER_KIND = 50  # hits kept per pattern kind, so one noisy kind never hides another
URL_CHARS = 300
FENCE = "skill-file"

# ----------------------------------------------------------------------------------------------- the patterns
HIDDEN = re.compile(r"[\u200b-\u200d\u2060\ufeff\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")
HTML_COMMENT = re.compile(r"<!--")
BLOB = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}|(?:\\x[0-9a-fA-F]{2}){16,}")
URL = re.compile(r"\b(?:https?|ftp|wss?)://[^\s<>\"'`)\]]+", re.I)
PIPE = re.compile(
    r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^\n|]*\|\s*(?:sudo\s+)?(?:(?:ba|z|da|k)?sh|python3?|node|perl|ruby|iex)\b"
    r"|\b(?:ba|z)?sh\s+(?:-c\s+)?[\"']?\$\(\s*(?:curl|wget)\b"
    r"|\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b",
    re.I,
)
SECRET = re.compile(
    r"(?<![\w.-])\.env(?![\w.-])|~/\.ssh\b|\.ssh/|\bid_(?:rsa|ed25519|ecdsa)\b"
    r"|\bsecurity\s+(?:find|dump)-(?:generic|internet)-password\b|\bsecurity\s+dump-keychain\b|\bprintenv\b"
    r"|\b(?:dict|json\.dumps|print|str|repr|pprint)\(\s*os\.environ\s*\)|\bos\.environ\.(?:items|copy|keys|values)\(\)"
    r"|JSON\.stringify\(\s*process\.env\s*\)|Object\.(?:entries|keys|values)\(\s*process\.env\s*\)"
    r"|\.aws/(?:credentials|config)\b|\bAWS_SECRET_ACCESS_KEY\b|\.config/gcloud\b|application_default_credentials\.json"
    r"|\bGOOGLE_APPLICATION_CREDENTIALS\b|\.netrc\b|\.git-credentials\b|\.docker/config\.json|\.kube/config\b"
    r"|^\s*env\s*(?:\||>|$)",
    re.I | re.M,
)
AGENT_PATH = re.compile(
    r"\.claude/(?:settings[\w.-]*|hooks)\b|\bhooks?\.json\b|[\"']hooks[\"']\s*:|\bAGENTS?\.md\b|\bCLAUDE\.md\b"
    r"|(?:\.claude|\.codex|\.agents)/skills/(?P<other>[a-z0-9._-]+)|\.codex/(?:config|hooks)",
    re.I,
)
WRITE = re.compile(
    r">>?|\btee\b|\bwrite|\bopen\([^\n]*[\"'][wa]\+?b?[\"']|\bcp\b|\bmv\b|\bsed\s+-i|\becho\b|\bappend|\bmodif|\bedit"
    r"|\bupdate|\binsert|\breplace|\bln\s+-s|\bdump\(|writeFile|fs\.write|json\.dump|\bset\b|\badd\b",
    re.I,
)
NETWORK = re.compile(
    r"\brequests\.(?:get|post|put|patch|delete|head|request|Session)\b|\burllib(?:\.request|3)?\b|\bhttp\.client\b"
    r"|\bhttpx\b|\baiohttp\b|\bsocket\.(?:socket|create_connection)\b|\bfetch\s*\(|\baxios\b|\bXMLHttpRequest\b"
    r"|\bcurl\b|\bwget\b|\bnc\s+-|\bnetcat\b|\bncat\b|Invoke-WebRequest|Invoke-RestMethod|/dev/tcp/|\bscp\s",
    re.I,
)
SCRIPTS = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".php", ".go", ".lua")


def _is_script(path, content):
    return path.lower().endswith(SCRIPTS) or content.startswith("#!")


def _line_hits(line, own, script):
    """The kinds one line hits, as (kind, url or None) pairs."""
    hits = []
    if HIDDEN.search(line):
        hits.append(("hidden_unicode", None))
    if HTML_COMMENT.search(line):
        hits.append(("html_comment", None))
    if BLOB.search(line):
        hits.append(("encoded_blob", None))
    hits += [("url", url[:URL_CHARS]) for url in dict.fromkeys(m.group(0).rstrip(".,;:") for m in URL.finditer(line))]
    if PIPE.search(line):
        hits.append(("pipe_to_shell", None))
    if SECRET.search(line):
        hits.append(("secret_read", None))
    paths = [m for m in AGENT_PATH.finditer(line) if (m.group("other") or "").lower() != own]
    if paths and WRITE.search(line):
        hits.append(("agent_config_write", None))
    if script and NETWORK.search(line):
        hits.append(("network_call", None))
    hits += [(kind, None) for kind, rx in screening.PATTERNS if rx.search(line)]
    return hits


def pattern_hits(files, own=""):
    """Every pattern hit in the bundle: [{file, line, pattern}] (a URL hit also names its `url`), in file and line order,
    each (file, line, pattern, url) once. Lines count from 1."""
    out, seen = [], set()
    for item in sorted(files, key=lambda f: f["path"]):
        path, content = item["path"], item["content"]
        script = _is_script(path, content)
        for n, line in enumerate(content.split("\n"), 1):
            for kind, url in _line_hits(line, own.lower(), script):
                key = (path, n, kind, url)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"file": path, "line": n, "pattern": kind, **({"url": url} if url else {})})
    return out


def kept_hits(hits):
    """The hits a screening row and the model's state keep: at most PER_KIND of each kind, in order."""
    counts, out = {}, []
    for hit in hits:
        counts[hit["pattern"]] = counts.get(hit["pattern"], 0) + 1
        if counts[hit["pattern"]] <= PER_KIND:
            out.append(hit)
    return out


# ----------------------------------------------------------------------------------------------- the state
def _fenced(path, text):
    # The writer cannot close the fence early: its own closing tag is broken where it appears.
    return f"<{FENCE} path={json.dumps(path)}>\n" + text.replace(f"</{FENCE}", f"</ {FENCE}") + f"\n</{FENCE}>"


def full_diff(before, after):
    """The unified diff of every changed file, uncut: the state must hold all of it or the screen is `oversize`."""
    old = {item["path"]: item["content"] for item in before}
    new = {item["path"]: item["content"] for item in after}
    return "".join(
        _unified(rel, old.get(rel), new.get(rel)) for rel in sorted(set(old) | set(new)) if old.get(rel) != new.get(rel)
    )


def _unified(rel, before, after):
    """One file's unified diff. A last line with no newline is marked as git marks it, so it never runs into the next
    line (clients/botcopy.py `unified` joins them)."""

    def lines(text):
        out = (text or "").splitlines(keepends=True)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out

    return "".join(
        difflib.unified_diff(
            lines(before),
            lines(after),
            "a/" + rel if before is not None else "/dev/null",
            "b/" + rel if after is not None else "/dev/null",
        )
    )


def build_state(name, description, files, base_files, patterns):
    """What the model reads: the skill's name and description, every file fenced as data, the diff against the base for
    an update, and the pattern hits."""
    state = {
        "name": name,
        "description": description,
        "files": [
            {"path": f["path"], "content": _fenced(f["path"], f["content"])}
            for f in sorted(files, key=lambda f: f["path"])
        ],
        "patterns": patterns,
    }
    if base_files is not None:
        state["diff"] = _fenced("diff", full_diff(base_files, files))
    return state


def state_chars(state):
    return len(json.dumps(state, default=str))


# ----------------------------------------------------------------------------------------------- the question set
_SET = {"stamp": object(), "set": None}


def question_set():
    """The skill-screen set, read again only when its file changes; None when it does not load."""
    stamp = screening._set_stamp(SET)
    if _SET["stamp"] == stamp:
        return _SET["set"]
    try:
        loaded = J.load_set(SET)
    except J.JudgeError as exc:
        LOG.warning("The skill-screen question set does not load: %s", J.error_code(exc))
        loaded = None
    _SET.update(stamp=stamp, set=loaded)
    return loaded


# ----------------------------------------------------------------------------------------------- the queue
# The question set of a proposal's latest screen ('' when it has none); `{table}` names the proposal row.
_LATEST_SET = (
    "COALESCE((SELECT s.question_set FROM skill_screenings s WHERE s.proposal_id = {table}.id "
    "ORDER BY s.created_at DESC, s.rowid DESC LIMIT 1), '')"
)


def _lease_cutoff():
    return H.shift(H.now(), minutes=-LEASE_MINUTES)


def requeue(c, label):
    """Pending proposals whose latest screen was made with another question set version go back to be screened. Approved
    versions are left as they are. Returns how many."""
    latest = _LATEST_SET.format(table="skill_proposals")
    return c.execute(
        f"UPDATE skill_proposals SET screen_status='pending', updated_at=? WHERE status='pending' AND "
        f"screen_status IN ('screened','not_screened') AND {latest} <> ?",
        (H.now(), label),
    ).rowcount


def recover(c):
    """A claim a stopped worker left past its lease goes back to pending."""
    return c.execute(
        "UPDATE skill_proposals SET screen_status='pending', updated_at=? WHERE status='pending' AND "
        "screen_status='screening' AND updated_at<?",
        (H.now(), _lease_cutoff()),
    ).rowcount


def due(c, label=None):
    """Whether a pass has anything to do: a proposal waiting, a lapsed claim, or a screen from an older set version."""
    stale = (
        f" OR (p.screen_status IN ('screened','not_screened') AND {_LATEST_SET.format(table='p')} <> ?)"
        if label
        else ""
    )
    args = (_lease_cutoff(), label) if label else (_lease_cutoff(),)
    return bool(
        c.execute(
            "SELECT 1 FROM skill_proposals p WHERE p.status='pending' AND (p.screen_status='pending' OR "
            f"(p.screen_status='screening' AND p.updated_at<?){stale}) LIMIT 1",
            args,
        ).fetchone()
    )


def claim(c, pid):
    """The claim stamp when this pass took the proposal (pending -> screening), else None: someone else has it."""
    stamp = H.now()
    taken = c.execute(
        "UPDATE skill_proposals SET screen_status='screening', updated_at=? WHERE id=? AND status='pending' "
        "AND screen_status='pending'",
        (stamp, pid),
    ).rowcount
    return stamp if taken else None


# ----------------------------------------------------------------------------------------------- one screen
def _json(text, default):
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return default
    return default if value is None else value


def _outcome(status, question_set, patterns, reason=None, **fields):
    return {
        "status": status,
        "question_set": question_set,
        "patterns": patterns,
        "reason": reason,
        "verdict": fields.get("verdict"),
        "risk": fields.get("risk"),
        "summary": fields.get("summary"),
        "findings": fields.get("findings") or [],
        "judge_call_id": fields.get("judge_call_id"),
        "cached_from": fields.get("cached_from"),
    }


def _reason(exc, seconds, timeout):
    """Why a model call left the proposal unscreened, from the error's code (never its text)."""
    code = getattr(exc, "code", "")
    if code == "spend_cap":
        return "cap"
    if code in ("unconfigured", "auth"):
        return "unavailable"
    if (
        code == "unavailable"
        and not getattr(exc, "status", 0)
        and (seconds >= timeout - 1 or "time" in str(getattr(exc, "detail", "")).lower())
    ):
        return "timeout"
    return "error"


def raised_risk(risk, hits, flags, flag_at=FLAG_AT):
    """The model's risk, raised to at least medium by a hidden-Unicode, secret-read or pipe-to-shell hit, or by a concern
    the model rated at or over `flag_at`. Never lowered."""
    floor = any(h["pattern"] in FLOOR_KINDS for h in hits) or any(p >= flag_at for p in flags.values())
    return "medium" if floor and risk == "low" else risk


def _cached(c, row, label):
    """A `screened` row for this exact bundle, base and question set version, of a proposal with the same name."""
    found = c.execute(
        "SELECT s.* FROM skill_screenings s JOIN skill_proposals p ON p.id = s.proposal_id WHERE "
        "s.status='screened' AND s.sha256=? AND s.base_version_id IS ? AND s.question_set=? AND p.name=? "
        "ORDER BY s.created_at DESC, s.rowid DESC LIMIT 1",
        (row["sha256"], row["base_version_id"], label, row["name"]),
    ).fetchone()
    return dict(found) if found else None


def _judge_call(store, label, since):
    with store.read() as c:
        found = c.execute(
            "SELECT id FROM events WHERE action='judge.call' AND target=? AND created_at>=? "
            "ORDER BY rowid DESC LIMIT 1",
            (label, since),
        ).fetchone()
    return found[0] if found else None


def screen(store, row, base_files, engine_for=None):
    """The outcome of screening one claimed proposal (nothing written here): screened with a model answer, or not
    screened with its reason."""
    files = _json(row["files"], [])
    qset = question_set()
    label = qset["label"] if qset else SET
    if bundles.bundle_sha(files) != row["sha256"]:
        return _outcome("not_screened", label, [], "error")  # the stored bundle is not the one proposed
    every = pattern_hits(files, row["name"])
    hits = kept_hits(every)
    if not qset:
        return _outcome("not_screened", label, hits, "error")
    with store.read() as c:
        cached = _cached(c, row, label)
    if cached:
        return _outcome(
            "screened",
            label,
            _json(cached["patterns"], hits),
            verdict=cached["verdict"],
            risk=cached["risk"],
            summary=cached["summary"],
            findings=_json(cached["findings"], []),
            judge_call_id=cached["judge_call_id"],
            cached_from=cached["id"],
        )
    state = build_state(row["name"], row["description"], files, base_files, hits)
    if state_chars(state) > J.MAX_STATE_CHARS:
        return _outcome("not_screened", label, hits, "oversize")
    with store.read() as c:
        engine = (engine_for or strict_engine)(store, c)
        at_cap = engine is None and over_spend_cap(c, store.settings)
    if engine is None:
        return _outcome("not_screened", label, hits, "cap" if at_cap else "unavailable")
    since, started = H.now(), time.monotonic()
    try:
        result = engine(state, qset["questions"], label=label, evidence=True)
    except J.JudgeError as exc:
        return _outcome(
            "not_screened",
            label,
            hits,
            _reason(exc, time.monotonic() - started, STRICT_SECONDS),
            judge_call_id=_judge_call(store, label, since),
        )
    call_id = _judge_call(store, label, since)
    answers = (result or {}).get("answers") or {}
    try:
        verdict, risk = J.choice(answers, "verdict")[0], J.choice(answers, "risk")[0]
        flags = {qid: J.noul(answers, qid) for qid in FLAGS if qid in answers}
    except (KeyError, TypeError, ValueError):
        verdict = risk = None
    if verdict not in VERDICTS or risk not in RISKS:
        return _outcome("not_screened", label, hits, "error", judge_call_id=call_id)
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    flag_at = float((qset.get("thresholds") or {}).get("flag", FLAG_AT))
    return _outcome(
        "screened",
        label,
        hits,
        verdict=verdict,
        risk=raised_risk(risk, every, flags, flag_at),
        summary=evidence.get("summary"),
        findings=evidence.get("findings") or [],
        judge_call_id=call_id,
    )


def finish(store, row, stamp, outcome):
    """Write the outcome, only while this pass still holds the claim (a rescreen or a lapsed lease took it back: the
    result is dropped and the proposal is screened again). Returns whether it was written."""
    with store.transaction() as c:
        moved = c.execute(
            "UPDATE skill_proposals SET screen_status=?, updated_at=? WHERE id=? AND screen_status='screening' "
            "AND (updated_at=? OR status<>'pending')",
            (outcome["status"], H.now(), row["id"], stamp),
        ).rowcount
        if not moved:
            return False
        c.execute(
            "INSERT INTO skill_screenings (id, proposal_id, sha256, base_version_id, question_set, status, reason, "
            "verdict, risk, summary, findings, patterns, judge_call_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sks_" + uuid.uuid4().hex[:24],
                row["id"],
                row["sha256"],
                row["base_version_id"],
                outcome["question_set"],
                outcome["status"],
                outcome["reason"],
                outcome["verdict"],
                outcome["risk"],
                outcome["summary"],
                json.dumps(outcome["findings"], ensure_ascii=False),
                json.dumps(outcome["patterns"], ensure_ascii=False),
                outcome["judge_call_id"],
                H.now(),
            ),
        )
        H.event(
            c,
            H.KEEPER,
            "skill.screened",
            row["id"],
            {
                "name": row["name"],
                "sha256": row["sha256"],
                "status": outcome["status"],
                "reason": outcome["reason"],
                "verdict": outcome["verdict"],
                "risk": outcome["risk"],
                "question_set": outcome["question_set"],
                "patterns": len(outcome["patterns"]),
                "cached": bool(outcome["cached_from"]),
            },
        )
    return True


def screen_one(store, pid, engine_for=None):
    """Claim one proposal, screen it and write the outcome. Returns its status, or None when it was not this pass's."""
    with store.transaction() as c:
        stamp = claim(c, pid)
        if not stamp:
            return None
        row = dict(c.execute("SELECT * FROM skill_proposals WHERE id=?", (pid,)).fetchone())
        base = (
            c.execute("SELECT files FROM skill_versions WHERE id=?", (row["base_version_id"],)).fetchone()
            if row["base_version_id"]
            else None
        )
    base_files = _json(base["files"], []) if base else None
    try:
        outcome = screen(store, row, base_files, engine_for)
    except Exception as exc:  # noqa: BLE001 - a bug here leaves the proposal unscreened with a reason, never stuck
        LOG.warning("Screening a skill failed: %s", type(exc).__name__)
        outcome = _outcome("not_screened", (question_set() or {}).get("label") or SET, [], "error")
    return outcome["status"] if finish(store, row, stamp, outcome) else None


def drain(store, limit=BATCH, engine_for=None):
    """One pass: older-version screens and lapsed claims go back to pending, then up to `limit` waiting proposals are
    screened, oldest first, one at a time. Returns their statuses."""
    qset = question_set()
    with store.transaction() as c:
        if qset:
            requeue(c, qset["label"])
        recover(c)
    with store.read() as c:
        ids = [
            r[0]
            for r in c.execute(
                "SELECT id FROM skill_proposals WHERE status='pending' AND screen_status='pending' "
                "ORDER BY created_at, id LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    done = []
    for pid in ids:
        status = screen_one(store, pid, engine_for)
        if status:
            done.append(status)
    return done


# ----------------------------------------------------------------------------------------------- the worker
_RUNNING = threading.Lock()


def _words(done):
    from . import system_routines as SR

    screened = done.count("screened")
    parts = [SR.count_words(screened, "skill screened", "skills screened")] if screened else []
    if len(done) - screened:
        parts.append(f"{len(done) - screened} not screened")
    return ", ".join(parts) or "Nothing to screen"


def _worker(store):
    from . import system_routines as SR

    try:
        with SR.recording(store, JOB) as run:
            done = drain(store)
            run.did(_words(done), quiet=not done)
    except Exception as exc:  # noqa: BLE001 - recorded as the run's failure; the next tick tries again
        LOG.warning("Skill screening pass failed: %s", type(exc).__name__)
    finally:
        _RUNNING.release()


def kick(store):
    """From the scheduler's tick: start a pass on its own thread when something is due and none is running, since a model
    call can take 90 seconds and the tick never waits on it. Nothing due reads one index."""
    qset = question_set()
    with store.read() as c:
        if not due(c, qset["label"] if qset else None):
            return False
    if not _RUNNING.acquire(blocking=False):
        return False
    threading.Thread(target=_worker, args=(store,), name="skill-screening", daemon=True).start()
    return True
