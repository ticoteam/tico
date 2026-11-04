"""Who may act for a mailbox on the connector and Connect Google routes: one owner per mailbox (plan v3 A/B, v4 O, v5 T/V).

`publisher()` (backend/connectors.py) is unchanged. It still decides every mailbox that no Google Workspace and no
personal grant covers, exactly as before. A mailbox one of them covers has a single owner:
- `workspace`: the computer hosting the mailbox's message bot (`google_workspace.owned_mailboxes`);
- `personal`: the computer that takes the person's live Connect Google grant (`takes`): the live computer hosting their
  message bot, or the holder while no computer hosts it.
Any other computer is refused with 403 `workspace_owned` or `personal_grant`, a publisher included. Work already leased
stays with the computer that leased it (`connectors.py`, `automations.py`), so a move never turns finished work into a
retry. Health is kept per computer and per account (`connector_reports`), and `service_health` is derived from it.
"""

import json

from . import google_workspace as workspace
from .store import H, Problem, encode

# A computer that answered this recently is online; the bot's computer then takes a first connect (plan v5 V).
ONLINE_SECONDS = 60
# A computer's report older than this is stale: its failing accounts stop counting for the service (plan v5 T).
FRESH_SECONDS = 1800

FORBIDDEN = "This machine is not assigned the private app-connector capability"
MESSAGES = {
    "workspace_owned": "{box} is read by the computer that runs its message bot, not by this one",
    "personal_grant": "{box} is connected through its person's Google grant, which the computer holding that grant reads",
}


def boxes(c):
    """The message-bot mailboxes, as routines.message_bot_mailboxes lists them with the roster the routes use."""
    from . import routines
    from .views import roster
    return routines.message_bot_mailboxes(c, roster(c))


def runner_of(execution, c, who):
    """The computer making the call. A person or a bot asking is refused by execution.runner; a revoked one gets 403 here."""
    runner = execution.runner(c, who)
    if not runner:
        raise Problem("forbidden", FORBIDDEN, 403)
    return runner


def publishes(store, execution, c, runner):
    """Whether this computer is a publisher: connectors.publisher's test, asked without raising."""
    return runner["owner_id"] in (store.settings.processing_operators or (execution.auth.owner_id(c),))


def refusal(c, runner_id, named, mailbox, owned=None):
    """Why this computer may not act for `mailbox`, as a refusal kind, or None when it may.

    A legacy mailbox (no Workspace or grant covers it) is today's rule: publishers only. A covered one is its owner's
    alone: `owned` is `google_workspace.owned_mailboxes(c, runner_id)`, read here when not given."""
    box = str(mailbox or "").strip().lower()
    auth = workspace.authority(c, box)
    if auth == "legacy":
        return None if named else "forbidden"
    mine = workspace.owned_mailboxes(c, runner_id) if owned is None else owned
    if box in mine:
        return None
    return "personal_grant" if auth == "personal" else "workspace_owned"


def check(c, runner_id, named, mailbox, owned=None):
    """Raise the 403 for `refusal`, if there is one."""
    kind = refusal(c, runner_id, named, mailbox, owned)
    if kind:
        box = str(mailbox or "").strip().lower()
        raise Problem(kind, FORBIDDEN if kind == "forbidden" else MESSAGES[kind].format(box=box), 403,
                      extra={"kind": kind})


def visible(c, runner_id, named, found=None, owned=None):
    """The message-bot mailboxes this computer may act for (`found`: a list already read, to save a read)."""
    mine = workspace.owned_mailboxes(c, runner_id) if owned is None else owned
    listed = boxes(c) if found is None else found
    return [box for box in listed if refusal(c, runner_id, named, box["address"], mine) is None]


def reportable(c, runner_id, named, owned=None):
    """The accounts this computer may report health for: the mailboxes it may act for (plan v5 T)."""
    return {box["address"] for box in visible(c, runner_id, named, owned=owned)}


def inbox_hosts(c):
    """Each person whose message bot is placed on a computer: {person_id: {bot, runner, online}}. `runner` is None when
    the bot has no live computer; `online` says that computer answered within ONLINE_SECONDS."""
    placed = {row["bot"]: row for row in c.execute(
        "SELECT a.bot, a.runner_id, r.last_seen_at, r.revoked_at FROM assignments a "
        "JOIN runners_with_state r ON r.id=a.runner_id")}
    since = H.shift(H.now(), seconds=-ONLINE_SECONDS)
    out = {}
    for box in boxes(c):
        row = placed.get(box["bot"])
        live = row is not None and not row["revoked_at"]
        out[box["person_id"]] = {"bot": box["bot"], "runner": row["runner_id"] if live else None,
                                 "online": bool(live and row["last_seen_at"] and row["last_seen_at"] > since)}
    return out


def bot_mailboxes(c, runner_id):
    """The mailboxes the message bots placed on this computer read on their turns (routines.token_mailboxes)."""
    from . import routines
    out = set()
    for (bot,) in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,)).fetchall():
        out.update(routines.token_mailboxes(c, bot))
    return out


def live_runners(c):
    """The ids of the computers that are not revoked. A revoked computer is never a host or a holder."""
    return {row[0] for row in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL")}


def takes(runner_id, named, row, hosts, live):
    """Whether this computer is the one that acts on one person's Connect Google row (plan v4 O, v5 V). The first connect,
    reconnect, exchange, refresh, result and the mailbox's ownership all ask this one rule.

    - A row being disconnected or revoked goes to its holder while the holder is live. With the holder gone, a publisher
      `named` finishes it.
    - Otherwise, when the person's message bot has a live computer (`hosts`), only that computer takes it, online or not:
      a publisher waits for the host rather than taking the connect.
    - With no host, a live holder keeps its grant. With no live holder, a publisher `named` takes it (a first connect).
    The claim passes `named` narrowed to the orphan case (`google_connect.claimable`)."""
    holder = row["holder"] or ""
    holder_live = bool(holder) and holder in live
    if row["status"] in ("disconnecting", "revoking"):
        return holder == runner_id if holder_live else bool(named)
    host = (hosts.get(row["person_id"]) or {}).get("runner")
    if host:
        return host == runner_id
    if holder_live:
        return holder == runner_id
    return bool(named)


def report(c, service, runner_id, now, *, failing=None, ok=(), reportable_set=None):
    """Record one computer's report for a connector service, one row per account (plan v5 T).

    `failing` maps each account it could not read to (message, reason). `ok` lists the accounts it read this time; they
    clear their earlier failures and no others, so one account's success never hides another's failure. The heartbeat
    row (account '') is written every time. When `reportable_set` is given, rows of accounts it no longer reports are
    dropped (the new owner reports them). Then service_health is derived again."""
    failing = dict(failing or {})
    ok = [account for account in dict.fromkeys(ok) if account not in failing]
    c.execute("INSERT INTO connector_reports(service,runner_id,account,error,failing,ok_accounts,reported_at) "
              "VALUES(?,?,'','','[]',?,?) ON CONFLICT(service,runner_id,account) DO UPDATE SET "
              "ok_accounts=excluded.ok_accounts,reported_at=excluded.reported_at",
              (service, runner_id, encode(ok), now))
    for account, (message, reason) in failing.items():
        c.execute("INSERT INTO connector_reports(service,runner_id,account,error,failing,ok_accounts,reported_at) "
                  "VALUES(?,?,?,?,?,'[]',?) ON CONFLICT(service,runner_id,account) DO UPDATE SET error=excluded.error,"
                  "failing=excluded.failing,reported_at=excluded.reported_at",
                  (service, runner_id, account, str(message)[:1000], encode({"reason": reason}), now))
    for account in ok:
        c.execute("DELETE FROM connector_reports WHERE service=? AND runner_id=? AND account=?",
                  (service, runner_id, account))
    if reportable_set is not None:
        for (account,) in c.execute("SELECT account FROM connector_reports WHERE service=? AND runner_id=? AND account<>''",
                                    (service, runner_id)).fetchall():
            if account not in reportable_set and account not in failing:
                c.execute("DELETE FROM connector_reports WHERE service=? AND runner_id=? AND account=?",
                          (service, runner_id, account))
    derive(c, service, now)


def counted(c, service, now):
    """The failing rows that count for `service`, each with its computer's heartbeat (`beat`) and whether that is `stale`.

    A row counts while its computer is fresh (heartbeat within FRESH_SECONDS). The row's own age never decides it: a
    computer that reads one account again keeps its other failing accounts failing (plan v5 T). A computer gone quiet
    still counts for the covered accounts it owns, because its last failure is unknown, not over. Its rows for accounts
    it no longer owns, and rows of a revoked computer, count nowhere."""
    stamp = H.shift(now, seconds=-FRESH_SECONDS)
    beats = {row["runner_id"]: row["reported_at"] for row in c.execute(
        "SELECT runner_id, reported_at FROM connector_reports WHERE service=? AND account=''", (service,))}
    revoked = {row["id"] for row in c.execute("SELECT id FROM runners_with_state WHERE revoked_at IS NOT NULL")}
    owned, out = {}, []
    for row in c.execute("SELECT runner_id, account, error, failing FROM connector_reports WHERE service=? "
                         "AND account<>'' AND error<>'' ORDER BY account", (service,)).fetchall():
        if row["runner_id"] in revoked:
            continue
        beat = beats.get(row["runner_id"])
        if beat is None:
            continue
        stale = beat <= stamp
        if stale:
            if row["runner_id"] not in owned:
                owned[row["runner_id"]] = {box.lower() for box in workspace.owned_mailboxes(c, row["runner_id"])}
            if row["account"].lower() not in owned[row["runner_id"]]:
                continue
        out.append(dict(row, beat=beat, stale=stale))
    return out


def failing_accounts(c, service, now):
    """The accounts `counted` says are failing for `service`. An account one computer reads again does not recover while
    another still fails it, and it is judged by the same rule `derive` uses, so the two never disagree."""
    return {row["account"] for row in counted(c, service, now)}


def derive(c, service, now):
    """service_health for `service`, from every computer's rows (`counted`). It is an error while any counted account is
    failing: a fresh computer's own message, or, for a computer gone quiet, that it has not reported since its last
    heartbeat. It is stamped with the newest heartbeat. Its readers (views, health) are unchanged."""
    name = "connector:" + service
    beats = c.execute("SELECT runner_id, reported_at FROM connector_reports WHERE service=? AND account='' "
                      "AND runner_id NOT IN (SELECT id FROM runners_with_state WHERE revoked_at IS NOT NULL)",
                      (service,)).fetchall()
    if not beats:
        c.execute("DELETE FROM service_health WHERE service=?", (name,))
        return
    newest = max(beats, key=lambda row: row["reported_at"])
    failing = counted(c, service, now)
    if failing:
        labels = {row["id"]: row["label"] for row in c.execute("SELECT id,label FROM runners_with_state")}
        signin = [row["account"] for row in failing if json.loads(row["failing"]).get("reason") == "signin"]
        detail = {"runner_id": failing[0]["runner_id"], "needs_person": bool(signin), "signin": signin}
        if signin:
            detail["action"] = "Reconnect the Google account for " + ", ".join(signin)
        messages = []
        for row in failing:
            if row["stale"]:
                computer = labels.get(row["runner_id"]) or "A computer"
                messages.append(f"{computer} has not reported since {row['beat'][:16].replace('T', ' ')} UTC")
            else:
                messages.append(row["error"])
        error = "; ".join(dict.fromkeys(messages))[:1000]
    else:
        detail, error = {"runner_id": newest["runner_id"]}, None
    c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,?,?) "
              "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,"
              "last_error=excluded.last_error,detail=excluded.detail",
              (name, newest["reported_at"], error, encode(detail)))
