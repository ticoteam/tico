# pyright: strict
"""Paid by selections and the payer for the next turn."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Any, Literal

from pydantic import Field, model_validator

from . import claude_login, gateway_connection, holds, login_holds, providers, read_cache
from . import hubdb
from . import model_subscriptions as model_subs
from .auth import Identity
from .models import ID, Contract
from .store import Problem

MS: Any = model_subs
H: Any = hubdb
RC: Any = read_cache
GATEWAY: Any = gateway_connection
PROVIDERS: Any = providers
Row = dict[str, Any]
Window = tuple[str, str, float, datetime, datetime]
HOLD: Any = login_holds
CAPABILITY = "attempt_credentials_v1"


class Pick(Contract):
    kind: Literal["pool", "plan", "key"]
    person: str = Field(default="", max_length=120)
    subscription_id: ID | Literal[""] = ""

    @model_validator(mode="after")
    def target(self) -> "Pick":
        if self.kind == "pool":
            if not self.person.strip() or self.subscription_id:
                raise ValueError("Choose a person for the pool")
        elif not self.subscription_id or self.person:
            raise ValueError("Choose one plan or key")
        return self


class ChangePicks(Contract):
    subscriptions: dict[str, Pick | None] = Field(max_length=4)


def can_edit(c: Connection, auth: Any, who: Identity, bot: str) -> bool:
    return not who.support and auth.bot_manager(c, who, bot)


def can_assign(auth: Any, who: Identity, person: str) -> bool:
    return not who.support and (person == who.actor or MS._may_manage(auth, who))


PICKS = (
    "SELECT bot,runtime,kind,person,subscription_id,set_by,updated_at "
    "FROM agent_subscriptions WHERE bot=? ORDER BY runtime"
)


def picks(c: Connection, bot: str) -> dict[str, Row]:
    return {r["runtime"]: dict(r) for r in RC.rows(c, PICKS, (bot,))}


def wait_fixer(c: Connection, runner: Any, pick: Row, payment: "Payment") -> str:
    owner = runner["owner_id"] if payment.update else pick["person"]
    return f"{MS._name_of(c, owner)} or an admin" if owner else "An admin"


def loaded_picks(c: Connection, bot: str, loads: Any = None) -> dict[str, Row]:
    snapshot = loads.payers if loads is not None else None
    if snapshot is not None and bot in snapshot.picks:
        return snapshot.picks[bot]
    return picks(c, bot)


def seed_owner_pool(c: Connection, bot: str, owner: str | None, actor: str, now: str) -> None:
    """Start a newly created agent on its human owner's pool."""
    try:
        person = MS.owner_of(c, owner)
    except Problem:
        return
    tools = {
        MS.KINDS[kind]["runtime"]
        for (kind,) in c.execute(
            "SELECT DISTINCT s.kind FROM model_subscriptions s LEFT JOIN credentials v ON v.id=s.credential_id "
            "WHERE s.owner_id=? AND s.status<>'needs_reconnect' AND (v.source IS NULL OR v.source NOT LIKE 'computer:%')",
            (person,),
        )
        if MS.KINDS[kind]["billing"] == "subscription"
    }
    c.executemany(
        "INSERT INTO agent_subscriptions(bot,runtime,kind,person,set_by,updated_at) VALUES(?,?,'pool',?,?,?)",
        [(bot, runtime, person, actor, now) for runtime in sorted(tools)],
    )


def plan_runtimes() -> set[str]:
    return {spec["runtime"] for spec in MS.KINDS.values() if spec["billing"] == "subscription"}


def clear_for_move(
    c: Connection, auth: Any, who: Identity, bot: str, *, seed_runner_owner: str | None = None
) -> list[str]:
    if MS._may_manage(auth, who):
        return []
    if who.role == "system":
        if (
            seed_runner_owner
            and c.execute(
                "SELECT 1 FROM agent_subscriptions p JOIN bot_config b ON b.bot=p.bot "
                "WHERE p.bot=? AND p.person NOT IN ('','human:'||?) "
                "AND coalesce(b.owner_id,'')<>? LIMIT 1",
                (bot, seed_runner_owner, seed_runner_owner),
            ).fetchone()
        ):
            raise Problem("payer_placement", "Choose a computer approved for this agent's Paid by", 409)
        return []
    # The default pool may go to its owner's computer (or company cloud) on first placement.
    condition = (
        "person<>? AND NOT ("
        "? IS NOT NULL AND kind='pool' AND (?='' OR person='human:'||?) "
        "AND person=(SELECT 'human:'||owner_id FROM bot_config WHERE bot=?) "
        "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.target=agent_subscriptions.bot "
        "AND e.action IN ('agent_subscription.changed','bot.archived','bot.assigned') "
        "AND (e.action<>'agent_subscription.changed' OR json_extract(e.detail,'$.runtime')=agent_subscriptions.runtime)))"
    )
    args = (who.actor, seed_runner_owner, seed_runner_owner, seed_runner_owner, bot)
    removed = [
        row[0]
        for row in c.execute(
            f"DELETE FROM agent_subscriptions WHERE bot=? AND {condition} RETURNING runtime", (bot, *args)
        )
    ]
    for runtime in sorted(removed):
        H.event(c, who.actor, "agent_subscription.cleared_on_move", bot, {"runtime": runtime})
    return sorted(removed)


def save(c: Connection, auth: Any, who: Identity, bot: str, changes: dict[str, Pick | None]) -> dict[str, Row]:
    """Patch only the supplied tools, with permission checked on each changed payer."""
    MS._reader(who)
    if not can_edit(c, auth, who, bot):
        raise Problem("forbidden", "You may change Paid by only for agents you manage", 403)
    current = picks(c, bot)
    for runtime, pick in changes.items():
        if runtime not in MS.RUNTIMES:
            raise Problem("wrong_runtime", "Choose a supported AI tool", 422)
        had = current.get(runtime)
        if pick is None:
            if had:
                c.execute("DELETE FROM agent_subscriptions WHERE bot=? AND runtime=?", (bot, runtime))
                H.event(c, who.actor, "agent_subscription.cleared", bot, {"runtime": runtime})
            continue
        if pick.kind == "pool":
            if runtime not in plan_runtimes():
                raise Problem("wrong_runtime", "This AI tool has no plans", 422)
            pid = H.actor_id(pick.person.strip())
            person = (
                "human:" + pid if pid and (":" not in pick.person or pick.person.strip().startswith("human:")) else ""
            )
            if had and had["kind"] == "pool" and had["person"] == person:
                continue
            person = MS.owner_of(c, pick.person)
        else:
            if had and had["kind"] == pick.kind and had["subscription_id"] == pick.subscription_id:
                continue
            sub = MS.row(c, pick.subscription_id)
            if not sub:
                raise Problem("not_found", "Provider not found", 404)
            if not portable(c, sub):
                raise Problem("computer_local", "This key belongs to its computer", 422)
            spec = MS.KINDS[sub["kind"]]
            if spec["runtime"] != runtime or pick.kind != ("plan" if spec["billing"] == "subscription" else "key"):
                raise Problem("wrong_runtime", "Choose a provider of that kind for this AI tool", 422)
            person = sub["owner_id"]
            if person:
                person = MS.owner_of(c, person)
        if not can_assign(auth, who, person):
            raise Problem("forbidden", "Only the pool's owner or an admin can assign it or one of its providers", 403)
        c.execute(
            "INSERT INTO agent_subscriptions(bot,runtime,kind,person,subscription_id,set_by,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(bot,runtime) DO UPDATE SET kind=excluded.kind,person=excluded.person,"
            "subscription_id=excluded.subscription_id,set_by=excluded.set_by,updated_at=excluded.updated_at,parent_grant=NULL",
            (bot, runtime, pick.kind, person, pick.subscription_id, who.actor, H.now()),
        )
        H.event(c, who.actor, "agent_subscription.changed", bot, {"runtime": runtime, "kind": pick.kind})
    return picks(c, bot)


def exact(c: Connection, sid: str) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in c.execute(
            "SELECT bot,runtime FROM agent_subscriptions WHERE subscription_id=? AND subscription_id<>'' ORDER BY bot,runtime",
            (sid,),
        )
    ]


def for_removal(c: Connection, sub: Row) -> list[tuple[str, str]]:
    sid, owner = sub["id"], sub["owner_id"]
    spec = MS.KINDS[sub["kind"]]
    affected = exact(c, sid)
    if owner and spec["billing"] == "subscription":
        siblings = [
            k for k, v in MS.KINDS.items() if v["runtime"] == spec["runtime"] and v["billing"] == "subscription"
        ]
        if not c.execute(
            f"SELECT 1 FROM model_subscriptions WHERE owner_id=? AND id<>? AND kind IN ({','.join('?' * len(siblings))})",
            (owner, sid, *siblings),
        ).fetchone():
            affected += [
                (r[0], r[1])
                for r in c.execute(
                    "SELECT bot,runtime FROM agent_subscriptions WHERE kind='pool' AND person=? AND runtime=?",
                    (owner, spec["runtime"]),
                )
            ]
    return sorted(affected)


def unset_removed(c: Connection, actor: str, affected: list[tuple[str, str]]) -> None:
    c.executemany("DELETE FROM agent_subscriptions WHERE bot=? AND runtime=?", affected)
    for bot, runtime in affected:
        H.event(c, actor, "agent_subscription.provider_removed", bot, {"runtime": runtime})


def revoke_parent(c: Connection, actor: str, grant: str) -> None:
    """Retire the converted choices that depended on this delegated grant."""
    from . import quarantine_stops

    for pick in c.execute(
        "DELETE FROM agent_subscriptions WHERE parent_grant=? RETURNING bot,runtime", (grant,)
    ).fetchall():
        H.event(c, actor, "agent_subscription.provider_removed", pick["bot"], {"runtime": pick["runtime"]})
    for attempt in c.execute(
        "SELECT DISTINCT a.* FROM attempts a JOIN attempt_subscriptions s ON s.attempt_id=a.id "
        "WHERE s.parent_grant=? AND a.state IN ('leased','running') AND a.stop_requested_at IS NULL",
        (grant,),
    ).fetchall():
        quarantine_stops.restart(c, attempt, "The model key grant was revoked")


@dataclass(frozen=True)
class Payment:
    subscription: Row | None
    reason: str = ""
    until: datetime | None = None
    update: bool = False
    held_subscription: str = ""
    delivery: Literal["claim", "legacy"] = "claim"


def portable(c: Connection, sub: Row) -> bool:
    return not c.execute(
        "SELECT 1 FROM credentials WHERE id=? AND source LIKE 'computer:%'", (sub["credential_id"],)
    ).fetchone()


@dataclass
class Inputs:
    picks: dict[str, dict[str, Row]]
    candidates: dict[tuple[str, str], list[Row]]
    parked: set[tuple[str, str]] = field(default_factory=set)


def prime(c: Connection, bots: list[str], *, legacy: bool = False) -> None:
    """Share payer inputs across readiness views in this snapshot."""
    values = RC.cache(c)
    if values is None:
        return
    bots = [bot for bot in bots if ("agent_subscriptions.inputs", bot) not in values]
    snapshot = read(c, bots)
    for bot in bots:
        values[("agent_subscriptions.inputs", bot)] = snapshot
    logins = {
        HOLD._login("", HOLD.subscription_login(sub["id"]), runtime)
        for (_, runtime), candidates in snapshot.candidates.items()
        for sub in candidates
    }
    if legacy:
        for (_, runtime), binding in MS._given(c).items():
            logins.update(
                HOLD._login("", HOLD.subscription_login(sub["id"]), runtime)
                for sub in (binding["members"] if binding.get("person") else [binding])
                if sub["id"]
            )
    holds.prime(c, holds.LOGIN_LIMIT, "login", logins)


def read(c: Connection, bots: list[str], job: str | None = None) -> Inputs:
    """Read the selected agents, providers and their usage together for one claim snapshot."""
    result = Inputs({bot: {} for bot in bots}, {})
    from .subscription_refresh import PLAN_PREFIX

    for batch in H.chunks(bots):
        marks = ",".join("?" for _ in batch)
        for row in c.execute(f"SELECT * FROM agent_subscriptions WHERE bot IN ({marks})", batch):
            result.picks[row["bot"]][row["runtime"]] = dict(row)
        if not any(result.picks[bot] for bot in batch):
            continue
        for row in c.execute(
            "WITH chosen AS MATERIALIZED ("
            "SELECT p.bot,p.runtime,p.parent_grant,p.subscription_id AS provider_id FROM agent_subscriptions p "
            f"WHERE p.bot IN ({marks}) AND p.kind<>'pool' UNION ALL "
            "SELECT p.bot,p.runtime,p.parent_grant,s.id FROM agent_subscriptions p "
            "CROSS JOIN model_subscriptions s INDEXED BY model_subscriptions_pool ON s.owner_id=p.person "
            f"WHERE p.bot IN ({marks}) AND p.kind='pool' AND s.kind IN ('claude_token','claude_login','codex_login')) "
            "SELECT choice.bot,choice.runtime,choice.parent_grant AS payer_parent,s.*,m.value AS payer_usage "
            f"FROM chosen choice CROSS JOIN {MS.SUBS} s ON s.id=choice.provider_id "
            "LEFT JOIN credentials v ON v.id=s.credential_id "
            "LEFT JOIN registry_metadata m ON m.key=?||s.id "
            "LEFT JOIN credential_grants parent ON parent.id=choice.parent_grant "
            "LEFT JOIN bot_config bc ON bc.bot=choice.bot "
            "WHERE (v.source IS NULL OR v.source NOT LIKE 'computer:%') "
            "AND (choice.parent_grant IS NULL OR (parent.id IS NOT NULL AND parent.revoked_at IS NULL "
            "AND parent.credential_id=s.credential_id AND parent.subject='human:'||bc.owner_id)) "
            "AND ((s.credential_id IS NULL AND s.sealed<>'') OR v.ciphertext IS NOT NULL)",
            (*batch, *batch, PLAN_PREFIX),
        ):
            result.candidates.setdefault((row["bot"], row["runtime"]), []).append(dict(row))
        if any(row["kind"] == "claude_login" for bot in batch for row in result.candidates.get((bot, "claude"), [])):
            result.parked.update(claude_login.paid_parks(c, batch, job))
    return result


def _matches_model(label: str, model: str) -> bool:
    named = re.findall(r"[a-z]+|\d+", label.lower().removeprefix("claude"))
    running = re.findall(r"[a-z]+|\d+", model.lower().removeprefix("claude"))
    # A provider can report a family-wide window, such as "Sonnet", or name one version in that family.
    return bool(named) and running[: len(named)] == named


def _usage(c: Connection, sub: Row, runtime: str, model: str, context: Row) -> tuple[datetime | None, datetime | None]:
    windows: list[Window] = []
    MS._plan_usage(c, sub, runtime, context, windows=windows)
    now = datetime.now(timezone.utc)
    latest: dict[tuple[str, str], Window] = {}
    for window, label, used, reset, reported in windows:
        key = (window, label.casefold())
        if key not in latest or latest[key][4] <= reported:
            latest[key] = (window, label, used, reset, reported)
    relevant = [
        (used, reset)
        for window, label, used, reset, _ in latest.values()
        if reset > now and (window != "model" or _matches_model(label, model))
    ]
    next_reset = min((reset for _, reset in relevant), default=None)
    blocked = max((reset for used, reset in relevant if used >= 99.5), default=None)
    return next_reset, blocked


def resolve(
    c: Connection,
    bot: str,
    runtime: str,
    model: str,
    *,
    loads: Any = None,
    job: str | None = None,
    inputs: Inputs | None = None,
    prefer: str = "",
    exclude: str = "",
) -> Payment | None:
    """None means unset. A choice without a payable subscription must wait."""
    snapshot = inputs if inputs is not None else (loads.payers if loads is not None else None)
    if snapshot is None or bot not in snapshot.picks:
        snapshot = read(c, [bot], job)
    pick = snapshot.picks[bot].get(runtime)
    if pick is None:
        return None
    pool = pick["kind"] == "pool"

    def payer_label() -> str:
        return f"{MS._name_of(c, pick['person'])}'s pool" if pool else str(rows[0]["name"]) if rows else "Paid by"

    rows = snapshot.candidates.get((bot, runtime), [])
    catalog = PROVIDERS.MODEL_BY_ID.get(model)
    if GATEWAY.is_tico(model) or (catalog and not PROVIDERS.serves(PROVIDERS.runtime_provider(runtime), catalog)):
        return Payment(None, f"{payer_label()}: choose a model this provider can run")
    if not rows:
        return Payment(None, f"{payer_label()}: no provider for {MS.RUNTIME_LABELS.get(runtime, runtime)}")
    eligible: list[tuple[datetime, str, str, Row]] = []
    resets: list[tuple[datetime, str]] = []
    probing = refreshing = False
    for sub in rows:
        if sub["id"] == exclude or MS.KINDS[sub["kind"]]["runtime"] != runtime:
            continue
        if not MS.available(c, sub, runtime, loads):
            if (end := H.parse_ts(MS._hold_end(c, sub["id"], runtime, loads))) and end > H.parse_ts(H.now()):
                resets.append((end, sub["id"]))
            continue
        if (job, sub["id"]) in snapshot.parked:
            refreshing = True
            continue
        if HOLD.holding(c, "", HOLD.subscription_login(sub["id"]), runtime, job, loads=loads) == "probing":
            probing = True
            continue
        next_reset, blocked = _usage(c, sub, runtime, model, MS.payer_usage_context(sub)) if pool else (None, None)
        if blocked:
            resets.append((blocked, ""))
            continue
        eligible.append((next_reset or datetime.max.replace(tzinfo=timezone.utc), sub["created_at"], sub["id"], sub))
    for _, _, _, sub in sorted(eligible, key=lambda candidate: (candidate[2] != prefer, *candidate[:3])):
        if MS.payer_readable(c, sub):
            return Payment(sub)
    if eligible:
        return Payment(None, MS.PAYER_UNAVAILABLE)
    if resets:
        reset, held = min(resets)
        hold: Row = HOLD.get(c, "", HOLD.subscription_login(held), runtime, loads) if held else {}
        when = MS._when(reset.isoformat())
        if HOLD.payment(hold.get("error")):
            reason = f"payment past due, retry at {when}"
        else:
            reason = f"busy until {when}" if hold.get("capacity") else f"next reset {when}"
        return Payment(None, f"{payer_label()}: {reason}", reset, held_subscription=held)
    if probing:
        return Payment(None, f"{payer_label()}: checking usage after the reset")
    if refreshing:
        return Payment(None, f"{payer_label()}: refreshing sign-in")
    return Payment(None, f"{payer_label()}: no provider can sign in. Check Settings > Providers")


def for_runner(
    c: Connection,
    runner: Any,
    bot: str,
    config: Row,
    *,
    loads: Any = None,
    job: str | None = None,
    inputs: Inputs | None = None,
) -> Payment | None:
    """The next turn's payer, including whether this computer can carry it."""
    runtime = str(config.get("runtime") or "")
    snapshot = inputs if inputs is not None else (loads.payers if loads is not None else None)
    if snapshot is None and job is None and (values := RC.cache(c)) is not None:
        snapshot = values.get(("agent_subscriptions.inputs", bot))
    if snapshot is None or bot not in snapshot.picks:
        snapshot = read(c, [bot], job)
    if runtime not in snapshot.picks[bot]:
        return None
    caps: list[str] = H._json(runner["capabilities"], []) or []
    current = MS.legacy_current(c, runner["id"], runtime, caps, loads)
    payment = resolve(
        c,
        bot,
        runtime,
        str(config.get("model") or ""),
        loads=loads,
        job=job,
        inputs=snapshot,
        prefer=current["id"] if current else "",
    )
    if payment is None or payment.subscription is None:
        return payment
    if CAPABILITY in caps:
        return payment
    sub = payment.subscription
    if current and (current["id"], current["revision"]) == (sub["id"], sub["revision"]):
        return replace(payment, delivery="legacy")
    return Payment(None, f"Update {runner['label'] or 'this computer'}", update=True)


def claim_entry(runtime: str, payment: Payment) -> Row:
    assert payment.subscription is not None
    return {
        "runtime": runtime,
        "subscription": payment.subscription,
        "delivery": payment.delivery,
    }


def provider_runners(c: Connection, sub: Row) -> set[str]:
    """Computers that can run a configured provider, including a plan waiting to be refreshed."""
    spec = MS.KINDS[sub["kind"]]
    rows = c.execute(
        "SELECT DISTINCT r.id,r.capabilities,g.subscription_id AS bound,g.person AS bound_pool,"
        "CASE WHEN g.person<>'' THEN EXISTS ("
        "SELECT 1 FROM attempt_subscriptions s JOIN attempts live ON live.id=s.attempt_id "
        "WHERE s.subscription_id=? AND s.delivery='legacy' AND live.runner_id=r.id "
        "AND live.state IN ('leased','running') AND live.stop_requested_at IS NULL) END AS legacy_live "
        "FROM agent_subscriptions p "
        "JOIN bots b ON b.id=p.bot AND b.state='active' JOIN assignments a ON a.bot=p.bot "
        "JOIN runners_with_state r ON r.id=a.runner_id AND r.revoked_at IS NULL "
        "LEFT JOIN computer_subscriptions g ON g.runner_id=r.id AND g.runtime=p.runtime "
        "LEFT JOIN credential_grants parent ON parent.id=p.parent_grant LEFT JOIN bot_config bc ON bc.bot=p.bot "
        "WHERE (p.parent_grant IS NULL OR (parent.id IS NOT NULL AND parent.revoked_at IS NULL "
        "AND parent.subject='human:'||bc.owner_id AND parent.credential_id=?)) AND p.runtime=? AND (p.subscription_id=? OR "
        "(?='subscription' AND p.kind='pool' AND p.person=?))",
        (sub["id"], sub["credential_id"], spec["runtime"], sub["id"], spec["billing"], sub["owner_id"]),
    )
    allowed: set[str] = set()
    for r in rows:
        caps: list[str] = H._json(r["capabilities"], []) or []
        if CAPABILITY in caps or (
            "model_subscriptions_v1" in caps
            and (sub["kind"] != "claude_login" or claude_login.CAPABILITY in caps)
            and (
                r["bound"] == sub["id"]
                or (
                    r["bound_pool"]
                    and r["bound_pool"] == sub["owner_id"]
                    and (
                        r["legacy_live"]
                        or (
                            (current := MS.legacy_current(c, r["id"], spec["runtime"], caps))
                            and current["id"] == sub["id"]
                        )
                    )
                )
            )
        ):
            allowed.add(r["id"])
    return allowed
