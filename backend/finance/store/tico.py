"""What Finance reads from and calls in the rest of Tico, typed at the border: the roster and org chart, the install
owner, the audit log and approvals. Every function takes the caller's connection."""
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from ... import hubdb as _hubdb
from ..core.org import Org, Team

# Tico's own modules are untyped: Finance reaches them only through this file, typed at each call.
_H: Any = _hubdb

Row = Mapping[str, Any]
Refused: type[Exception] = _H.Refused


def new_id() -> str:
    return str(_H.new_id())


def now() -> str:
    return str(_H.now())


def shift(at: str, days: int) -> str:
    return str(_H.shift(at, days=days))


def _metadata(c: sqlite3.Connection, key: str) -> Any:
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def owner_person(c: sqlite3.Connection, fallback: str = "") -> str:
    """The install owner as `human:<id>`: the stored owner record's person, else `fallback` (the person Finance was
    set up for). Admins are not owners."""
    stored = _metadata(c, "owner")
    email = str(cast(dict[str, Any], stored).get("email") or "").strip().lower() if isinstance(stored, dict) else ""
    if email:
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if row:
            return "human:" + str(row[0])
    return fallback


def _actor(value: str, bots: set[str]) -> str:
    """A reports-to or lead reference as an actor: `bot:x`/`human:x` kept, `person:x` is a human, a bare slug of a
    bot is that bot, anything else a person id."""
    text = str(value or "").strip()
    if not text:
        return ""
    kind, _, ident = text.partition(":")
    if ident and kind in ("bot", "human"):
        return text
    if ident and kind == "person":
        return "human:" + ident
    return "bot:" + text if text in bots else "human:" + text


def org(c: sqlite3.Connection, owner: str) -> Org:
    """The org chart now: groups with their leads, who is in which group, and who reports to whom. Archived bots are
    off it."""
    from ... import groups, people
    G: Any = groups
    P: Any = people
    doc = _metadata(c, "people")
    roster = cast(dict[str, Any], P.load(doc if isinstance(doc, dict) else {"people": []}))
    configs: dict[str, dict[str, Any]] = {}
    archived = {str(r[0]) for r in c.execute("SELECT slug FROM bots WHERE state='archived'")}
    for row in c.execute("SELECT bot, config_json, reports_to FROM bot_config"):
        if str(row[0]) in archived:
            continue
        try:
            config = json.loads(row[1] or "{}")
        except ValueError:
            config = {}
        config = cast(dict[str, Any], config) if isinstance(config, dict) else {}
        config["reports_to"] = row[2] or config.get("reports_to") or ""
        configs[str(row[0])] = config
    slugs = set(configs)
    groups = cast(dict[str, dict[str, Any]], roster.get("org_groups") or {})
    teams: dict[str, Team] = {}
    for gid, group in groups.items():
        lead = str(G.lead_of(gid, roster, configs) or "")
        teams[gid] = Team(id=gid, name=str(group.get("name") or gid), parent=str(group.get("parent") or ""),
                          lead=_actor(lead, slugs))
    team_of: dict[str, str] = {}
    reports: dict[str, str] = {}
    humans: set[str] = set()
    for person in cast(list[dict[str, Any]], roster.get("people") or []):
        actor = "human:" + str(person.get("id") or "")
        humans.add(actor)
        if person.get("hidden"):      # left the company (access.leave): on no team and reporting to no one (spec 7.5)
            continue
        if person.get("team") in teams:
            team_of[actor] = str(person["team"])
        if person.get("reports_to"):
            reports[actor] = _actor(str(person["reports_to"]), slugs)
    for row in c.execute("SELECT id FROM humans"):
        humans.add("human:" + str(row[0]))
    for slug, config in configs.items():
        actor = "bot:" + slug
        team = str(P.team_of(slug, configs, roster) or "")
        if team in teams:
            team_of[actor] = team
        if config.get("reports_to"):
            reports[actor] = _actor(str(config["reports_to"]), slugs)
    return Org(owner=owner, teams=teams, team_of=team_of, reports_to=reports, humans=frozenset(humans),
               bots=frozenset("bot:" + s for s in slugs))


def audit(c: sqlite3.Connection, actor: str, action: str, target: str, detail: Mapping[str, object]) -> None:
    """The `events` row every Finance write leaves, in the same transaction."""
    _H.event(c, actor, action, target, dict(detail))


def approval_request(c: sqlite3.Connection, requester: str, kind: str, payload: Mapping[str, object],
                     to: str) -> str:
    """A Tico approval with no task, decided by `to` (one person). The id."""
    row = cast(Row, _H.approval_request(c, requester, kind, dict(payload), to=to))
    return str(row["id"])


def approval(c: sqlite3.Connection, approval_id: str) -> Row | None:
    return cast(Row | None, _H.approval(c, approval_id))


def approval_consume(c: sqlite3.Connection, actor: str, approval_id: str) -> None:
    _H.approval_consume(c, actor, approval_id)


def approval_withdraw(c: sqlite3.Connection, approval_id: str, note: str) -> None:
    _H.approval_withdraw(c, approval_id, note)


def payload_hash(payload: Mapping[str, object]) -> str:
    return str(_H.payload_hash(dict(payload)))


def known_actor(c: sqlite3.Connection, actor: str) -> bool:
    """Whether hubdb would accept `actor` as a writer (a roster person, a bot, or the keeper)."""
    kind, _, ident = actor.partition(":")
    if actor == "keeper":
        return True
    if kind == "human":
        return c.execute("SELECT 1 FROM humans WHERE id=?", (ident,)).fetchone() is not None
    if kind == "bot":
        return c.execute("SELECT 1 FROM bots WHERE slug=?", (ident,)).fetchone() is not None
    return False


# ------------------------------------------------------------------------------------------------ Gateway-billed usage
# A run is Gateway-billed when it ran on a Tico model (`gateway_connection.is_tico`) and not on a subscription. Costs
# come back as decimal text (SQLite's printf), so no float reaches Finance.
def _gateway(p: str = "") -> str:
    return f"{p}model LIKE 'tico-%' AND coalesce({p}billing, 'api') != 'subscription'"


_GATEWAY = _gateway()


def _runs() -> str:
    from ... import usage_records
    return str(cast(Any, usage_records).RUNS)


def _video(models: Sequence[str], p: str = "") -> tuple[str, list[str]]:
    """The SQL test for a run segment on one of the Gateway's image models (`tico-image*`, and any model a meter row
    puts in category `image`), and its parameters."""
    test = f"{p}model LIKE 'tico-image%'"
    if not models:
        return test, []
    return f"({test} OR {p}model IN ({','.join('?' * len(models))}))", list(models)


def gateway_run_costs(c: sqlite3.Connection, after: str, through: str,
                      video_models: Sequence[str] = ()) -> dict[tuple[str, str], str]:
    """{(bot slug, `ai` | `video`): USD as decimal text} for Gateway-billed runs that finished in (after, through]; a
    segment on an image model counts on `video`."""
    video, params = _video(video_models)
    rows = c.execute(f"SELECT bot, CASE WHEN {video} THEN 'video' ELSE 'ai' END, "
                     f"printf('%.9f', sum(coalesce(est_cost_usd, 0))) FROM {_runs()} WHERE {_GATEWAY} "
                     "AND finished > ? AND finished <= ? GROUP BY 1, 2", (*params, after, through)).fetchall()
    return {(str(r[0] or ""), str(r[1])): str(r[2]) for r in rows}


def video_jobs(c: sqlite3.Connection, job_ids: Sequence[str], video_models: Sequence[str] = ()) -> set[str]:
    """The jobs whose Gateway-billed runs were all on image models."""
    video, params = _video(video_models, "r.")
    out: set[str] = set()
    for i in range(0, len(job_ids), 500):
        chunk = list(job_ids[i:i + 500])
        rows = c.execute(f"SELECT a.job_id FROM attempts a JOIN {_runs()} r ON r.id = a.id WHERE a.job_id IN "
                         f"({','.join('?' * len(chunk))}) AND {_gateway('r.')} GROUP BY a.job_id "
                         f"HAVING sum(CASE WHEN {video} THEN 0 ELSE 1 END) = 0", (*chunk, *params)).fetchall()
        out.update(str(r[0]) for r in rows)
    return out


def run_holds_settled(c: sqlite3.Connection, month: str, through: str, page: int = 1000) -> list[tuple[str, str]]:
    """(hold id, source id) of every settled run hold no entry has taken in yet whose run finished in `month`
    (YYYY-MM, the month the Gateway bills it in) at or before `through`, read a page at a time. A run's finish is its
    job's last turn's `finished`; a hold with no turn on record goes by when it settled."""
    out: list[tuple[str, str]] = []
    last = ""
    while True:
        rows = c.execute(
            "SELECT id, source_id FROM (SELECT h.id, h.source_id, coalesce((SELECT max(t.finished) FROM attempts a "
            "JOIN turns t ON t.id = a.id WHERE a.job_id = substr(h.source_id, 5)), h.settled_at) AS finished "
            "FROM fin_holds h WHERE h.source='run' AND h.status='settled' AND h.entry_id IS NULL AND h.id>?) "
            "WHERE substr(finished, 1, 7)=? AND finished<=? ORDER BY id LIMIT ?", (last, month, through, page)).fetchall()
        out += [(str(r[0]), str(r[1])) for r in rows]
        if len(rows) < page:
            return out
        last = str(rows[-1][0])


def live_expired_run_holds(c: sqlite3.Connection, at: str) -> list[str]:
    """Run holds past their expiry whose job is still out with a runner (leased, running, or not yet known to have
    ended)."""
    return [str(r[0]) for r in c.execute(
        "SELECT h.id FROM fin_holds h JOIN jobs j ON j.id = substr(h.source_id, 5) WHERE h.source='run' "
        "AND h.status='held' AND h.expires_at IS NOT NULL AND h.expires_at<=? AND substr(h.source_id, 1, 4)='job:' "
        "AND j.state IN ('leased', 'running', 'uncertain')", (at,))]


def gateway_decision_costs(c: sqlite3.Connection, after: str, through: str) -> dict[str, str]:
    """{caller actor: USD as decimal text} for decision calls on the Gateway's model in (after, through], from the
    judge audit (`judge.call` rows name who asked)."""
    rows = c.execute("SELECT actor, printf('%.9f', sum(coalesce(json_extract(detail_json, '$.cost_usd'), 0))) "
                     "FROM events WHERE action='judge.call' AND ts > ? AND ts <= ? "
                     "AND json_extract(detail_json, '$.model') LIKE 'tico-%' GROUP BY actor", (after, through)).fetchall()
    return {str(r[0] or ""): str(r[1]) for r in rows}


def run_gateway_cost(c: sqlite3.Connection, turn_id: str) -> str | None:
    """What one run cost on the Gateway (USD as decimal text), or None when no part of it was Gateway-billed."""
    row = c.execute(f"SELECT count(*), printf('%.9f', sum(coalesce(est_cost_usd, 0))) FROM {_runs()} WHERE id=? "
                    f"AND {_GATEWAY}", (turn_id,)).fetchone()
    return str(row[1]) if row and int(row[0]) else None


def bot_average_run(c: sqlite3.Connection, bot: str, since: str, most: int) -> str | None:
    """A bot's average Gateway-billed cost per run over its last `most` such runs started since `since`, or None."""
    row = c.execute(f"SELECT count(*), printf('%.9f', avg(cost)) FROM (SELECT id, sum(coalesce(est_cost_usd, 0)) AS cost "
                    f"FROM {_runs()} WHERE bot=? AND started >= ? AND finished IS NOT NULL AND {_GATEWAY} GROUP BY id "
                    "ORDER BY max(started) DESC LIMIT ?)", (bot, since, most)).fetchone()
    return str(row[1]) if row and int(row[0]) else None


def attempt_job(c: sqlite3.Connection, attempt_id: str) -> str | None:
    row = c.execute("SELECT job_id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return str(row[0]) if row and row[0] else None


# ------------------------------------------------------------------------------------------------ telling people
def notice(c: sqlite3.Connection, person: str, text: str) -> None:
    """A keeper notice to one person, the kind usage limits send."""
    _H.say(c, _H.KEEPER, person, text, kind="notice")


def needs_you(c: sqlite3.Connection, person: str, title: str, body: str, *, private: bool | None = None) -> str | None:
    """One Needs-you item (a keeper task the person owns); an open one with the same title is reused. None when the
    person cannot be given one. `private` (True for a notice that names an amount) keeps it to the person and its
    requester; left None, Tico's default applies."""
    try:
        made: Any = _H.task_create(c, _H.KEEPER, title[:200], body, person, deduplicate=True, private=private)
    except _H.Refused:
        return None
    task: Mapping[str, Any] = cast(Mapping[str, Any], made) if isinstance(made, Mapping) else {}
    found: object = task.get("id")
    return str(found) if found else None


def emit(c: sqlite3.Connection, name: str, subject: str, data: Mapping[str, object]) -> None:
    """A Finance event to Tico's rules (a sourceless `judge_event`) and automations, with ids only."""
    from ... import rules
    R: Any = rules
    facts = dict(data)
    R.judge_event(c, name, {"subject": subject, "type": subject, "delivery": str(facts.get("event_id") or subject),
                            "data": facts})
    _H.hook(c, name, subject, lambda: facts, _H.KEEPER)


def person_by_email(c: sqlite3.Connection, email: str) -> str:
    row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email.strip().lower(),)).fetchone() if email else None
    return "human:" + str(row[0]) if row else ""


# ------------------------------------------------------------------------------------------------ the Gateway link
def gateway_connected(c: sqlite3.Connection, settings: object) -> bool:
    from ... import gateway_connection
    return bool(cast(Any, gateway_connection).connected(c, settings))


def gateway_target(c: sqlite3.Connection, vault: object, settings: object) -> tuple[str, str]:
    """(key, base URL) for a call to the Gateway; key '' when none is set. Never logged."""
    from ... import gateway_connection
    secret, base, _ = cast(tuple[str, str, object], cast(Any, gateway_connection).target(c, vault, settings))
    return str(secret or ""), str(base or "")


def credential_secret(c: sqlite3.Connection, vault: object, credential_id: str) -> str:
    """A stored credential's secret, read at use time ("" when it is gone or holds none). Never logged or kept."""
    row = c.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if row is None or row["ciphertext"] is None or vault is None:
        return ""
    return str(cast(Any, vault).cipher.decrypt(c, row) or "")


# The credential vault (backend/credentials.py `Vault`), registered once at startup (finance/actions `use_vault`). A
# payee's bank details are sealed into a vault row the moment they are asked for; Finance keeps only the row's id.
_VAULT: list[Any] = []
SEALED_KIND, SEALED_SOURCE = "password", "finance"


def use_vault(vault: object | None) -> None:
    _VAULT[:] = [vault] if vault is not None else []


def _cipher() -> Any:
    if not _VAULT or not getattr(_VAULT[0], "configured", False):
        raise Refused("the credential vault is not set up, so bank details cannot be kept")
    return _VAULT[0].cipher


def fingerprint(c: sqlite3.Connection, context: str, value: str) -> str:
    """A keyed fingerprint (HMAC-SHA256 with the vault's data key) of `value`: compares bank details without keeping
    them, and cannot be guessed offline from the database alone."""
    import hashlib
    import hmac
    key = cast(bytes, _cipher().key(c))
    return hmac.new(key, f"tico-finance:{context}:".encode() + value.encode(), hashlib.sha256).hexdigest()


def seal_credential(c: sqlite3.Connection, name: str, value: str, preview: str, by: str, at: str) -> str:
    """`value` sealed into a new vault row (Tools > Credentials, source finance); its id."""
    cipher = _cipher()
    cid = new_id()
    ciphertext, nonce = cipher.encrypt(c, cid, value)
    c.execute("INSERT INTO credentials(id,name,kind,preview,ciphertext,nonce,source,created,updated,updated_by,"
              "created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (cid, name[:150], SEALED_KIND, preview, ciphertext, nonce, SEALED_SOURCE, at, at, by, by))
    return cid


def open_credential(c: sqlite3.Connection, credential_id: str) -> str:
    """A sealed value, read at send time ("" when it is gone). Never logged or kept."""
    row = c.execute("SELECT * FROM credentials WHERE id=? AND source=?", (credential_id, SEALED_SOURCE)).fetchone()
    if row is None or row["ciphertext"] is None:
        return ""
    return str(_cipher().decrypt(c, row) or "")


def drop_credential(c: sqlite3.Connection, credential_id: str) -> None:
    """Delete a vault row Finance sealed (an action that never ran: declined, withdrawn, expired or failed)."""
    c.execute("DELETE FROM credential_grants WHERE credential_id=? AND credential_id IN "
              "(SELECT id FROM credentials WHERE source=?)", (credential_id, SEALED_SOURCE))
    c.execute("DELETE FROM credentials WHERE id=? AND source=?", (credential_id, SEALED_SOURCE))


def credential_exists(c: sqlite3.Connection, credential_id: str) -> bool:
    return c.execute("SELECT 1 FROM credentials WHERE id=? AND ciphertext IS NOT NULL", (credential_id,)
                     ).fetchone() is not None


def after_commit(c: sqlite3.Connection, fn: Callable[[], None]) -> None:
    """Run `fn` once the caller's transaction commits (a doorbell); at once on a connection that has no such list."""
    pending = cast(list[Callable[[], None]] | None, getattr(c, "after_commit", None))
    if pending is None:
        fn()
    elif fn not in pending:
        pending.append(fn)


# ------------------------------------------------------------------------------------------------ Finance's webhooks
def finance_hook_calls(c: sqlite3.Connection, at: str, most: int) -> list[Row]:
    """Calls to Finance's triggers (consumer = finance, backend/triggers.py) still kept, newest first, bodies included."""
    return list(c.execute(
        "SELECT b.trigger_id, b.delivery, b.received_at, b.type, b.via, b.outcome, b.body FROM trigger_bodies b "
        "JOIN triggers t ON t.id=b.trigger_id WHERE t.deleted_at IS NULL AND t.consumer='finance' "
        "AND b.expires_at>? ORDER BY b.received_at DESC, b.delivery DESC LIMIT ?", (at, most)).fetchall())


# ------------------------------------------------------------------------------------------------ the view version
# Who sees what follows the org chart, so a change to it moves `fin_view_versions` (what cached views key on). SQLite
# triggers catch every writer: the roster document (groups, teams, people's managers), a bot's manager or team, a bot
# joining, leaving or being archived. A roster edit that changes none of these moves it too; that only costs a cache.
_BUMP = ("UPDATE fin_view_versions SET version=version+1, reason='{why}', "
         "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=1")
_TEAM = ("CASE WHEN json_valid(OLD.config_json) AND json_valid(NEW.config_json) "
         "THEN json_extract(OLD.config_json, '$.team') IS NOT json_extract(NEW.config_json, '$.team') "
         "OR json_extract(OLD.config_json, '$.reports_to') IS NOT json_extract(NEW.config_json, '$.reports_to') "
         "ELSE OLD.config_json IS NOT NEW.config_json END")
ORG_TRIGGERS = (
    ("fin_view_roster_insert", "AFTER INSERT ON registry_metadata WHEN NEW.key = 'people'", "roster"),
    ("fin_view_roster_update", "AFTER UPDATE OF value_json ON registry_metadata WHEN NEW.key = 'people' "
     "AND OLD.value_json IS NOT NEW.value_json", "roster"),
    ("fin_view_bot_insert", "AFTER INSERT ON bot_config", "bot added"),
    ("fin_view_bot_delete", "AFTER DELETE ON bot_config", "bot removed"),
    ("fin_view_bot_update", f"AFTER UPDATE OF reports_to, config_json ON bot_config WHEN "
     f"OLD.reports_to IS NOT NEW.reports_to OR {_TEAM}", "reports to"),
    ("fin_view_bot_archive", "AFTER UPDATE OF state ON bots WHEN (OLD.state = 'archived') IS NOT "
     "(NEW.state = 'archived')", "bot archived"),
)


def watch_org(c: sqlite3.Connection) -> None:
    """Make the org-chart triggers that move the view version. Idempotent."""
    for name, when, why in ORG_TRIGGERS:
        c.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {when} BEGIN {_BUMP.format(why=why)}; END")
