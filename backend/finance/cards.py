"""Cards (spec §4.5, §8): person cards on the company's card program, each linked to a budget. A card's provider limit
is its allocation, reserved against the budget tree when set, so the cards together cannot pass the budget. Every
change is an action; what Mercury must do is queued in fin_outbox under one key and sent by finance/sync.py outside
the transaction. Tico shows the last 4 only and never has the number or CVC. A bot's card is a standing card (Mercury
has no single-use cards); a person records the vendor accounts it is saved on (`card_save`), and that is where the bot
buys with it (spec §8.3).

The card row holds the desired state (status, limit_amount) and what Mercury last confirmed (provider_status,
provider_limit). One `card_sync` write per card at a time carries no values: the worker reads the latest desired state
when it sends, so a superseded change is never sent, and a card's creation reads it too: it is issued at its allocation
as it stands then (a freeze made before then follows once it exists); a card left with no allocation is not issued
(`WAITING` until it has one), and a cancelled one never is. A lower limit or a cancel keeps the old reservation until
Mercury confirms it (`allocation_due`), so the budget never counts on room the card can still spend."""
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from . import handlers
from .context import Ctx
from .core import dates
from .core import kinds as K
from .core.budgets import amount_at
from .core import validate as V
from .core.errors import CONFLICT, DENIED, INVALID, NOT_AVAILABLE, NOT_FOUND, FinanceError, Named, invalid, named_refusal
from .core.org import SYSTEM
from .handlers import Plan, Result
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import tico as T

STATE_OPS = {"card_freeze": ("freeze", "frozen"), "card_unfreeze": ("unfreeze", "active"),
             "card_cancel": ("cancel", "cancelled")}


def sees(ctx: Ctx, who: str, card: Any) -> bool:
    from .tab import sees_card
    if who == SYSTEM:
        return True
    b = ctx.budgets().get(str(card["budget_id"] or ""))
    return str(card["holder"]) == who or sees_card(ctx, ctx.view(who), card, b.account_id if b else "")


def _card(ctx: Ctx, who: str, card_id: str) -> Any:
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None or not sees(ctx, who, card):
        raise FinanceError(NOT_FOUND, "no such card")
    return card


def _program(ctx: Ctx, money_id: str | None) -> Any:
    programs = [m for m in SK.money_accounts(ctx.c, ctx.book.id) if str(m["kind"]) == "card_program"
                and str(m["status"] or "active") == "active"]
    if money_id:
        programs = [m for m in programs if str(m["id"]) == money_id]
    if not programs:
        raise invalid("connect a card program (Mercury IO) first")
    if len(programs) > 1:
        raise invalid("name the card program (money_account_id)")
    return programs[0]


def queue(ctx: Ctx, card: Any, op: str, body: dict[str, object], key: str, aid: str, by: str) -> None:
    """One write owed to the provider for this card; the card shows `pending` until the worker sends it. A card_sync
    already waiting (not yet claimed) covers any later change, so a second one is not queued. The worker is rung once
    the action commits, so Mercury hears of the change now rather than at its next sweep."""
    if op == "card_sync" and SK.card_sync_waiting(ctx.c, str(card["id"])):
        SK.update(ctx.c, "fin_cards", str(card["id"]), {"sync_status": "pending", "sync_error": None,
                                                        "updated_at": ctx.at})
        _ring_after_commit(ctx)
        return
    money = SK.get(ctx.c, "fin_money_accounts", str(card["money_account_id"] or ""))
    SK.outbox_add(ctx.c, connection_id=str(money["connection_id"]) if money is not None and money["connection_id"]
                  else None, action_id=aid, op=op, payload={"card_id": str(card["id"]), **body}, key=key, by=by,
                  at=ctx.at)
    SK.update(ctx.c, "fin_cards", str(card["id"]), {"sync_status": "pending", "sync_error": None,
                                                    "updated_at": ctx.at})
    _ring_after_commit(ctx)


def _ring_after_commit(ctx: Ctx) -> None:
    """Ring the bank worker after this transaction commits (the store runs `after_commit` callbacks then, and drops
    them on rollback), so the worker reads the outbox row it can see. Imported here: sync reads cards."""
    from . import sync
    hooks = cast(list[Callable[[], object]] | None, getattr(ctx.c, "after_commit", None))
    if hooks is not None:
        hooks.append(sync.ring)
    else:
        sync.ring()


def sync(ctx: Ctx, card_id: str, aid: str, by: str, after: str = "") -> None:
    """Bring Mercury to this card's desired state (once the card exists there). `after` names the write whose answer
    left the card out of step (its own key, so it never folds into that write's). A card whose creation was held back
    (`WAITING`: no allocation) is issued once it has a positive allocation."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None:
        return
    if card["provider_card_id"]:
        key = f"card-sync-{card_id}-after-{after}" if after else f"card-sync-{card_id}-{aid or ctx.at}"
        queue(ctx, card, "card_sync", {}, key, aid, by)
        return
    limit = issue_limit(ctx.c, card)
    if str(card["sync_status"]) == WAITING and limit > 0 and not SK.card_create_waiting(ctx.c, card_id):
        asked = SK.card_create_payload(ctx.c, card_id)
        queue(ctx, card, "card_create", {"user_id": str(asked.get("user_id") or ""),
                                         "nickname": str(asked.get("nickname") or card["nickname"] or ""),
                                         "monthly": limit}, f"card-create-{card_id}-{aid or ctx.at}", aid, by)


def issue_limit(c: sqlite3.Connection, card: Any) -> int:
    """The limit a card is issued with: its allocation as it stands now (or a lower limit not yet confirmed), and 0
    when it is cancelled or has no allocation, so it is not issued at all. (A frozen card with an allocation is issued
    and then frozen, as any change made before Mercury has the card follows once it does.)"""
    if str(card["status"]) == "cancelled" or not card["budget_id"]:
        return 0
    alloc = SB.allocation(c, str(card["budget_id"]), "card", str(card["id"]))
    have = int(alloc["amount"]) if alloc is not None else 0
    want = int(card["limit_amount"] or 0)
    return max(0, min(have, want) if want > 0 else have)


def used(ctx: Ctx, card: Any) -> int:
    """What the card has used this budget period: its live holds plus what settled (§8.2)."""
    from .budgeting import period_window, subtree
    bid = str(card["budget_id"] or "")
    if bid not in ctx.budgets():
        return 0
    start, end = period_window(ctx, bid)
    return SB.target_used(ctx.c, subtree(ctx, bid), start, end, f"card:{card['id']}", absorbed=True)


def in_step(card: Any) -> bool:
    """Whether Mercury has confirmed the card's desired state."""
    want = str(card["status"])
    if not card["provider_card_id"]:
        return False
    if want != str(card["provider_status"] or "active"):
        return False
    return want == "cancelled" or int(card["provider_limit"] or 0) == int(card["limit_amount"] or 0) or \
        (want == "frozen" and card["allocation_due"] is not None and int(card["allocation_due"]) == 0)


def confirmed(ctx: Ctx, card_id: str, done: dict[str, Any], aid: str = "", follow: str = "") -> None:
    """What Mercury confirmed for a card. Once it matches the desired state, a lower reservation waiting on it
    (`allocation_due`) takes effect. With `follow` (the outbox write whose final answer this is), a card still out of
    step with its desired state keeps a card_sync queued, so a change made while the write was at Mercury is never
    left behind."""
    fields: dict[str, object] = {"synced_at": ctx.at}
    if "status" in done:
        fields["provider_status"] = str(done["status"])
    if "limit" in done:
        fields["provider_limit"] = int(done["limit"])
    SK.update(ctx.c, "fin_cards", card_id, fields)
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None:
        return
    if str(card["provider_status"] or "") == "cancelled" and str(card["status"]) != "cancelled":
        # Cancelled at Mercury is final: the card can't come back, so it is cancelled here too.
        SK.update(ctx.c, "fin_cards", card_id, {"status": "cancelled", "allocation_due": 0, "updated_at": ctx.at})
        card = SK.get(ctx.c, "fin_cards", card_id)
    if not in_step(card):
        if follow:
            sync(ctx, card_id, aid, SYSTEM, after=follow)
        return
    SK.update(ctx.c, "fin_cards", card_id, {"sync_status": "ok", "sync_error": None})
    if card["allocation_due"] is not None and card["budget_id"]:
        SB.upsert_allocation(ctx.c, budget_id=str(card["budget_id"]), target_kind="card", target_id=card_id,
                             amount=int(card["allocation_due"]), currency=ctx.book.base_currency, by=SYSTEM,
                             at=ctx.at)
        SK.update(ctx.c, "fin_cards", card_id, {"allocation_due": None})


def created(ctx: Ctx, card_id: str, aid: str, outbox_id: str = "") -> None:
    """Mercury issued the card: apply what changed meanwhile."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None:
        return
    confirmed(ctx, card_id, {}, aid, follow=outbox_id or f"create-{aid or ctx.at}")


WAITING = "waiting"         # sync_status of a card whose creation was held back: no allocation yet
UNCERTAIN = "uncertain"     # provider_status of a card whose creation was cut off: Mercury may or may not have it


def uncertain(ctx: Ctx, card_id: str, why: str) -> None:
    """The card's creation may have gone through at Mercury: its reservation stays (a cancel keeps it too) until a
    person confirms its Mercury card or that there is none (card_resolve)."""
    SK.update(ctx.c, "fin_cards", card_id, {"provider_status": UNCERTAIN, "sync_status": "failed", "sync_error": why,
                                            "synced_at": None, "updated_at": ctx.at})


def _mercury_status(raw: object) -> str:
    s = str(raw or "")
    return s if s in ("active", "frozen") else "cancelled"     # inactive, expired, suspended: it can't spend


def _limit_cents(raw: object) -> int | None:
    value = raw.get("amountCents") if isinstance(raw, dict) else None   # type: ignore[union-attr]
    return int(cast(Any, value)) if isinstance(value, int) else None


def candidates(ctx: Ctx, card: Any, found: list[dict[str, Any]]) -> str:
    """What a person needs to settle a cut-off creation, from Mercury's whole card list for its user: the cards with
    its nickname made since and not already another card's. Never decides: the card stays uncertain, its reservation
    kept, until card_resolve. The text (also the card's sync_error)."""
    card_id = str(card["id"])
    asked = SK.card_create_payload(ctx.c, card_id)
    nickname = str(asked.get("nickname") or card["nickname"] or "")[:50]
    since = str(card["created_at"])[:10]
    matches = [f for f in found if str(f.get("nickname") or "") == nickname and str(f.get("createdAt") or "")[:10]
               >= since and f.get("id") and SK.card_by_provider(ctx.c, str(card["provider"]), str(f["id"])) is None]
    shown = ", ".join(f"{f['id']} (••{str(f.get('lastFour') or '')[-4:]}, {str(f.get('createdAt') or '')[:16]})"
                      for f in matches[:5])
    why = (f"Mercury may have issued this card; it lists {len(matches)} possible: {shown}." if matches else
           f"Mercury lists no card named {nickname!r} made since {since}; it may still have issued it.")
    SK.update(ctx.c, "fin_cards", card_id, {"sync_status": "failed", "sync_error": why[:300], "synced_at": ctx.at,
                                            "updated_at": ctx.at})
    return why


def adopt(ctx: Ctx, card_id: str, found: dict[str, Any], user: str, outbox_id: str) -> str:
    """The Mercury card a person confirmed for a cut-off creation, as Mercury describes it: adopted (then brought to
    the desired state, a cancel included) when it is that user's and no other card's; else the card stays uncertain
    with why. Returns what it decided."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None or str(card["provider_status"] or "") != UNCERTAIN:
        return "settled"
    pid = str(found.get("id") or "")
    owner = str(found.get("userId") or "")
    why = "" if pid else "Mercury did not describe that card"
    if pid and user and owner and owner != user:
        why = "that Mercury card is another user's"
    elif pid and SK.card_by_provider(ctx.c, str(card["provider"]), pid) is not None:
        why = "that Mercury card is already another card's"
    if why:
        SK.update(ctx.c, "fin_cards", card_id, {"sync_status": "failed", "sync_error": why, "updated_at": ctx.at})
        return "refused"
    limit = _limit_cents(found.get("spendLimit"))
    SK.update(ctx.c, "fin_cards", card_id, {
        "provider_card_id": pid, "last4": str(found.get("lastFour") or "")[-4:] or None,
        "provider_status": _mercury_status(found.get("status")), "provider_limit": limit,
        "sync_status": "ok", "sync_error": None, "synced_at": ctx.at, "updated_at": ctx.at})
    created(ctx, card_id, "", f"adopt-{outbox_id}")
    return "adopted"


def card_resolve(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A person settles a cut-off creation: `provider_card_id` names the Mercury card it made (read from Mercury, then
    adopted), or `none` says Mercury never issued it (a cancelled card's reservation goes; an active one is issued
    again)."""
    V.only(p, ("card_id", "provider_card_id", "none"))
    card = _card(ctx, who, V.text(p, "card_id", 80))
    card_id = str(card["id"])
    if str(card["provider_status"] or "") != UNCERTAIN:
        raise FinanceError(CONFLICT, "that card's creation is not in doubt")
    pid = V.opt_text(p, "provider_card_id", 120) or ""
    none = V.flag(p, "none")
    if bool(pid) == none:
        raise invalid("name the Mercury card (provider_card_id) or confirm there is none")
    if pid and SK.card_by_provider(ctx.c, str(card["provider"]), pid) is not None:
        raise FinanceError(CONFLICT, "that Mercury card is already another card's")
    asked = SK.card_create_payload(ctx.c, card_id)

    def apply(aid: str) -> Result:
        if pid:
            queue(ctx, card, "card_adopt", {"provider_card_id": pid, "user_id": str(asked.get("user_id") or "")},
                  f"card-adopt-{card_id}-{aid}", aid, who)
            return {"card_id": card_id, "sync_status": "pending"}
        SK.update(ctx.c, "fin_cards", card_id, {"provider_status": None, "sync_error": None, "updated_at": ctx.at})
        if str(card["status"]) == "cancelled":
            not_issued(ctx, card_id)
            return {"card_id": card_id, "status": "cancelled"}
        queue(ctx, card, "card_create", {"user_id": str(asked.get("user_id") or ""),
                                         "nickname": str(asked.get("nickname") or card["nickname"] or ""),
                                         "monthly": int(card["limit_amount"] or asked.get("monthly") or 0)},
              f"card-create-{card_id}-again-{aid}", aid, who)
        return {"card_id": card_id, "sync_status": "pending"}
    what = f"Use Mercury card {pid} for this card" if pid else "Mercury never issued this card"
    return Plan(K.default_gate(ctx.org, who, "card_resolve"), what, apply, facts={"holder": str(card["holder"])})


def not_issued(ctx: Ctx, card_id: str) -> None:
    """The card was cancelled before Mercury issued it: nothing was issued, so its reservation goes now."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None:
        return
    SK.update(ctx.c, "fin_cards", card_id, {"sync_status": "ok", "sync_error": None, "allocation_due": None,
                                            "provider_status": "cancelled", "synced_at": ctx.at})
    if card["budget_id"] and SB.allocation(ctx.c, str(card["budget_id"]), "card", card_id) is not None:
        SB.upsert_allocation(ctx.c, budget_id=str(card["budget_id"]), target_kind="card", target_id=card_id, amount=0,
                             currency=ctx.book.base_currency, by=SYSTEM, at=ctx.at)


def card_kind(p: V.Payload, bot: bool) -> str:
    """A card's kind: standing for a bot (the default for one), person for a teammate. Mercury has no single-use card
    (§8.1), so one is refused: a bot's card is a standing card with a tight limit (§8.3)."""
    kind = V.opt_text(p, "kind", 20) or ("standing" if bot else "person")
    if kind not in ("standing", "person", "single_use"):
        raise invalid("kind is standing|person|single_use")
    if kind == "single_use":
        raise FinanceError(NOT_AVAILABLE, "Mercury has no single-use cards yet: a bot's card is a standing card with a "
                           "tight limit")
    if kind != ("standing" if bot else "person"):
        raise invalid(f"a {'bot' if bot else 'teammate'}'s card is {'standing' if bot else 'person'}")
    return kind


def _mercury_user(ctx: Ctx, p: V.Payload, holder: str, program: Any) -> tuple[str, str]:
    """The Mercury user a card is issued to, and how it was found: the one the request names; else the one on the card's
    connection whose email is the holder's (a teammate, exactly one); else the connection's card user (set by
    `connection_settings`). Mercury issues a card to one of its own users, so nothing else is guessed: with none of
    these the card is refused."""
    named = V.opt_text(p, "provider_user_id", 120) or ""
    if named:
        return named, "named"
    connection = str(program["connection_id"] or "")
    email = T.person_email(ctx.c, holder)
    if email:
        found = [u for u in SK.provider_users_on(ctx.c, connection) if str(u["email"] or "").strip().lower() == email]
        if len(found) == 1:
            return str(found[0]["provider_user_id"]), "the holder's email"
    row = SK.get(ctx.c, "fin_connections", connection)
    setting = str(SK.loads(row["settings_json"] if row is not None else None).get("card_user_id") or "")
    if setting:
        return setting, "the connection's card user"
    raise invalid("a Mercury user is needed to issue the card: name one (provider_user_id), or set the connection's "
                  "card user")


def card_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A card for a teammate or a bot under a budget, with `amount` a month set aside for it. `provider_user_id` is the
    Mercury user the card is issued to (Mercury issues cards to its own users); left out, `_mercury_user` finds one.
    `merchant` is the merchant the card is
    asked for, kept as the request's intent: Mercury has no merchant locks (§8.1), so the limit is what it enforces."""
    V.only(p, ("holder", "budget_id", "amount", "kind", "nickname", "money_account_id", "provider_user_id", "merchant"))
    holder = V.text(p, "holder", 120)
    if holder not in ctx.org.humans | ctx.org.bots:
        raise invalid("no such teammate")
    bot = holder in ctx.org.bots
    kind = card_kind(p, bot)
    program = _program(ctx, V.opt_text(p, "money_account_id", 80))
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a card's monthly limit is more than 0")
    card_id = SL.new_id()
    budget_id = V.text(p, "budget_id", 80)
    route = handlers.figure_route(ctx, who, budget_id)
    alloc = handlers.allocation_plan(ctx, who, {"budget_id": budget_id, "target_kind": "card", "target_id": card_id,
                                                "amount": amount}, route)
    nickname = V.opt_text(p, "nickname", 50) or holder.split(":", 1)[-1]
    merchant = V.opt_text(p, "merchant", 120) or ""
    user, found = _mercury_user(ctx, p, holder, program)

    def apply(aid: str) -> Result:
        alloc.apply(aid)
        SK.insert(ctx.c, "fin_cards", {
            "id": card_id, "provider": str(program["provider"] or "mercury"), "holder": holder, "budget_id": budget_id,
            "money_account_id": str(program["id"]), "kind": kind, "limit_amount": amount, "limit_interval": "month",
            "currency": ctx.book.base_currency, "status": "active", "nickname": nickname, "merchant": merchant,
            "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        queue(ctx, SK.get(ctx.c, "fin_cards", card_id), "card_create",
              {"user_id": user, "nickname": nickname, "monthly": amount}, f"card-create-{card_id}", aid, who)
        return {"card_id": card_id, "sync_status": "pending"}
    gate = alloc.gate if route else K.default_gate(ctx.org, who, "card_create")
    return Plan(gate,
                f"Issue a {kind} card to {holder} with {amount / 100:.2f} a month from {ctx.budgets()[budget_id].name}",
                apply,
                facts={"amount": amount, "holder": holder, "budget_id": budget_id, "card.kind": kind,
                       "holder.kind": "bot" if bot else "human"},
                impact={"provider_user_id": user, "provider_user_from": found},
                resolved={"provider_user_id": user})


def card_update_limit(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Change a card's monthly limit: its allocation moves (and must fit), and the provider limit follows."""
    V.only(p, ("card_id", "amount"))
    card = _card(ctx, who, V.text(p, "card_id", 80))
    if str(card["status"]) == "cancelled":
        raise FinanceError(CONFLICT, "that card is cancelled")
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a card's monthly limit is more than 0; freeze or cancel it instead")
    budget_id = str(card["budget_id"] or "")
    route = handlers.figure_route(ctx, who, budget_id, card)
    if not route:
        check_lower(ctx, card, amount, who)
    alloc = handlers.allocation_plan(ctx, who, {"budget_id": budget_id, "target_kind": "card",
                                                "target_id": str(card["id"]), "amount": amount}, route)

    def apply(aid: str) -> Result:
        alloc.apply(aid)                            # the limit follows the allocation (`limit_follows`)
        return {"card_id": str(card["id"]), "limit": amount}
    gate = alloc.gate if route else K.default_gate(ctx.org, who, "card_update_limit")
    return Plan(gate, f"Set card ••{card['last4'] or '----'}'s limit to {amount / 100:.2f} a month", apply,
                facts={"amount": amount, "holder": str(card["holder"]), "budget_id": str(card["budget_id"] or "")})


def _state(kind: str) -> handlers.Planner:
    op, status = STATE_OPS[kind]

    def planner(ctx: Ctx, who: str, p: V.Payload) -> Plan:
        """Freeze, unfreeze or cancel a card (freeze also takes a budget: every active card on it and its children). A
        freeze records who made it: a budget's cutoff (`budget`) or a person (`person`). A person's freeze of a card a
        budget froze claims it, so the budget's room never lifts it (§8.2)."""
        V.only(p, ("card_id", "budget_id", "reason") if kind == "card_freeze" else ("card_id", "reason"))
        bid = ""
        if kind == "card_freeze" and "budget_id" in p:
            from .budgeting import subtree
            bid = V.text(p, "budget_id", 80)
            b = ctx.budgets().get(bid)
            if b is None or (who != SYSTEM and not handlers.sees_budget(ctx, who, b)):
                raise FinanceError(NOT_FOUND, "no such budget")
            cards = list(SK.cards_on_budgets(ctx.c, subtree(ctx, bid)))
            if who != SYSTEM:
                cards += list(SK.budget_frozen_cards(ctx.c, subtree(ctx, bid)))
            if not cards:
                raise FinanceError(CONFLICT, "that budget has no active cards")
        else:
            cards = [_card(ctx, who, V.text(p, "card_id", 80))]

        def claims(card: Any) -> bool:
            return kind == "card_freeze" and who != SYSTEM and str(card["status"]) == "frozen" and \
                str(card["freeze_by"] or "") == "budget"
        for card in cards:
            if str(card["status"]) == status and not claims(card):
                raise FinanceError(CONFLICT, f"that card is already {status}")
            if str(card["status"]) == "cancelled":
                raise FinanceError(CONFLICT, "that card is cancelled")
        if kind == "card_unfreeze":
            from .core import dates
            if who == SYSTEM and any(str(card["freeze_by"] or "") != "budget" for card in cards):
                raise FinanceError(CONFLICT, "only a budget's cutoff lifts its own freeze")
            leaf = str(cards[0]["budget_id"] or "")
            blocker = blocking_budget(ctx, leaf, dates.day(ctx.today))
            if blocker:
                raise _unfreeze_refusal(ctx, who, leaf, blocker)

        def apply(aid: str) -> Result:
            for card in cards:
                cid = str(card["id"])
                if claims(card):
                    SK.update(ctx.c, "fin_cards", cid, {"freeze_by": "person", "freeze_budget_id": None,
                                                        "updated_at": ctx.at})
                    continue
                fields: dict[str, object] = {"status": status, "updated_at": ctx.at}
                if kind == "card_cancel":
                    fields["allocation_due"] = 0         # its reservation goes when Mercury confirms the cancel
                elif kind == "card_freeze":
                    fields.update(freeze_by="budget" if who == SYSTEM else "person", freeze_budget_id=bid or None)
                else:
                    fields.update(freeze_by=None, freeze_budget_id=None)
                SK.update(ctx.c, "fin_cards", cid, fields)
                if kind == "card_cancel" and not card["provider_card_id"] and \
                        str(card["provider_status"] or "") != UNCERTAIN and \
                        not SK.card_create_waiting(ctx.c, cid):
                    not_issued(ctx, cid)       # never issued and not being issued: nothing to undo
                    continue
                sync(ctx, cid, aid, who)
            return {"cards": [str(card["id"]) for card in cards], "status": status}
        what = f"{op.capitalize()} {len(cards)} card(s)" if len(cards) > 1 else \
            f"{op.capitalize()} card ••{cards[0]['last4'] or '----'}"
        return Plan(K.default_gate(ctx.org, who, kind), what, apply,
                    facts={"holder": [str(card["holder"]) for card in cards], "cards": len(cards)})
    return planner


def unfreeze_with_room(ctx: Ctx, budget_id: str = "", aid: str = "") -> int:
    """Lift the freezes a budget's cutoff made, inside the action that gave the room (a raise): each lift goes to
    Mercury through `sync` as part of that action. The hourly look lifts through `lift_through_actions` instead."""
    lifted = 0
    for card in lift_eligible(ctx, budget_id):
        cid = str(card["id"])
        SK.update(ctx.c, "fin_cards", cid, {"status": "active", "freeze_by": None, "freeze_budget_id": None,
                                            "updated_at": ctx.at})
        sync(ctx, cid, aid, SYSTEM)
        lifted += 1
    return lifted


def lift_eligible(ctx: Ctx, budget_id: str = "") -> list[Any]:
    """The cards a budget's cutoff froze that every budget over them now has room for: a new period, a raise, or a
    hold or reserve released (§8.2). A card a person froze is never among them. With `budget_id`, only cards under
    that budget are looked at."""
    from .budgeting import subtree
    from .core import dates
    budgets = ctx.budgets()
    scope = subtree(ctx, budget_id) if budget_id else None
    today = dates.day(ctx.today)
    eligible: list[Any] = []
    for card in SK.budget_frozen_cards(ctx.c, scope):
        leaf = str(card["budget_id"] or "")
        if leaf in budgets and not blocking_budget(ctx, leaf, today):
            eligible.append(card)
    return eligible


def lift_through_actions(ctx: Ctx) -> int:
    """The hourly look's lift (§8.2): each card lift_eligible names is unfrozen as system work through the action path,
    one action a card, so the lift is logged, refused like any unfreeze and replay-safe. How many."""
    from . import actions
    lifted = 0
    for card in lift_eligible(ctx):
        cid = str(card["id"])
        # The key carries the hour: a lift refused this hour (its failed result is stored under the key) is tried again
        # the next hour, while a lift that committed is replayed by the same key and not sent again.
        res = actions.act(ctx.c, SYSTEM, "card_unfreeze", {"card_id": cid, "reason": "budget has room"},
                          f"cutoff-lift-{cid}-{card['updated_at']}-{ctx.at[:13]}", at=ctx.at)
        if res.status == "committed":
            lifted += 1
    return lifted


def freeze_paused(ctx: Ctx, budget_id: str, aid: str, by: str) -> int:
    """A budget set to 0 (a pause) freezes every active card on it and under it, as `frozen_by` budget. Their
    allocations stay; a raise that leaves room lifts them (`unfreeze_with_room`). Each freeze goes to Mercury through
    `sync`. How many."""
    from .budgeting import subtree
    cards = SK.cards_on_budgets(ctx.c, subtree(ctx, budget_id))
    for card in cards:
        cid = str(card["id"])
        SK.update(ctx.c, "fin_cards", cid, {"status": "frozen", "freeze_by": "budget", "freeze_budget_id": budget_id,
                                            "updated_at": ctx.at})
        sync(ctx, cid, aid, by)
    return len(cards)


def _over(ctx: Ctx, budget_id: str, at: date) -> bool:
    """Whether a budget's spend (actuals and holds) is at or past its amount on `at`: the cutoff a card freeze and its
    unfreeze read. An unused allocation, a card's own limit included, is not spend, so it never freezes the card that
    set it aside (§4.4, §8.2)."""
    from .budgeting import state
    return state(ctx, budget_id, at).at_limit


BLOCKING = ("decline", "hold_payment")     # the at-limit actions that refuse spend: a card under one stays frozen


def blocking_budget(ctx: Ctx, leaf: str, at: date) -> str:
    """The id of the first budget on a card's path (its own, then its parents) that stops the card on `at`, or "": one
    at zero (a pause, whatever its at-limit: a zero budget means no spending, §12), or one at its amount whose at-limit
    refuses spend. A pause or a cutoff reaches a card through these same parent links, so a card is lifted, or unfrozen
    by hand, only when none of them stops it. A warn or ask budget above zero blocks nothing."""
    from .budgeting import chain_of
    budgets = ctx.budgets()
    for bid in chain_of(ctx, leaf):
        if _paused(ctx, bid, at) or (budgets[bid].at_limit in BLOCKING and _over(ctx, bid, at)):
            return bid
    return ""


def _paused(ctx: Ctx, budget_id: str, at: date) -> bool:
    """Whether a budget's amount is zero on `at`, an explicit pause. A budget with no amount in force is not paused:
    it never cuts off."""
    return amount_at(SB.versions(ctx.c, [budget_id])[budget_id], at) == 0


def _unfreeze_refusal(ctx: Ctx, who: str, leaf: str, blocker: str) -> FinanceError:
    """Why a card cannot be unfrozen yet. The blocking budget is named only where `who` may see it (§7.5); the record
    keeps no name, so a replay names it again only while `who` may still see it."""
    b = ctx.budgets()[blocker]
    fallback = ("this card's budget is used up; raise it before unfreezing" if blocker == leaf
                else "a budget above this one is used up; raise it before unfreezing")
    named = Named((blocker,), "the {0} budget is used up; raise it before unfreezing", fallback)
    return named_refusal(DENIED, named, [b.name], handlers.sees_budget(ctx, who, b))


LIMIT_FALLBACK = "the card has used more than that this period; its limit can't go below that"


def check_lower(ctx: Ctx, card: Any, amount: int, who: str) -> None:
    """A card's limit can't go below what it has already used this period (holds and settled spend). The refusal is a
    Named record: its figure shows only to a reader who sees the card and whose own figure is the true one (named_reason)."""
    have = used(ctx, card)
    if 0 < amount < have:
        named = Named((), f"the card has used {have / 100:.2f} this period; its limit can't go below that",
                      LIMIT_FALLBACK, numbers=True, cards=(str(card["id"]),), on=dates.day(ctx.today).isoformat())
        shown = sees(ctx, who, card) and not figure_hidden(ctx, who, card)
        raise named_refusal(INVALID, named, [], shown)


def figure_hidden(ctx: Ctx, who: str, card: Any, on: str = "") -> bool:
    """Whether `who`'s figure for the card's spend, in the period holding `on` (today when none), differs from the true
    one: a settled private part is netted out of it."""
    from .budgeting import Netted, period_window, settled_used, subtree
    bid = str(card["budget_id"] or "")
    start, end = period_window(ctx, bid, on)
    netted = Netted(ctx.view(who))
    settled_used(ctx, netted, subtree(ctx, bid), start, end, f"card:{card['id']}", absorbed=True)
    return netted.excluded


def limit_follows(ctx: Ctx, card_id: str, amount: int, aid: str, by: str, lowered: bool) -> None:
    """An allocation on a card target moves the card's provider limit too (0 freezes it). A lower one (`lowered`) left
    the reservation as it was: it takes effect when Mercury confirms (`allocation_due`)."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None or str(card["status"]) == "cancelled":
        return
    fields: dict[str, object] = {"updated_at": ctx.at, "allocation_due": amount if lowered else None}
    if amount <= 0:
        fields.update(status="frozen", freeze_by="person", freeze_budget_id=None)    # a zero allocation is a person's
    else:
        fields["limit_amount"] = amount
    SK.update(ctx.c, "fin_cards", card_id, fields)
    if lowered and not card["provider_card_id"] and str(card["provider_status"] or "") == "" and \
            not SK.card_create_claimed(ctx.c, card_id):
        confirmed_now(ctx, card_id, amount)
        return
    sync(ctx, card_id, aid, by)


def confirmed_now(ctx: Ctx, card_id: str, amount: int) -> None:
    """A card Mercury has not issued yet (and is not issuing now): its creation will read the lower limit, so the
    lower reservation can take effect at once."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is not None and card["budget_id"]:
        SB.upsert_allocation(ctx.c, budget_id=str(card["budget_id"]), target_kind="card", target_id=card_id,
                             amount=amount, currency=ctx.book.base_currency, by=SYSTEM, at=ctx.at)
        SK.update(ctx.c, "fin_cards", card_id, {"allocation_due": None})


def _vendor(ctx: Ctx, text: str) -> tuple[str, str]:
    """The vendor a card is saved on: its key (a contact's id, or a name's key as store/ap.vendor_resolved gives it) and
    the label to show (the contact's name, else the text the vendor was posted under, else the text given)."""
    key = SAP.vendor_resolved(ctx.c, ctx.book.id, text)
    if not key:
        raise invalid("name the vendor")
    contact = SAP.contacts(ctx.c, [key]).get(key) if SAP.is_contact(ctx.c, key) else None
    label = str(contact["name"]) if contact is not None else \
        SL.vendor_label_texts(ctx.c, ctx.book.id, [key]).get(key) or text.strip()
    return key, label[:80]


def _saves(ctx: Ctx, card_id: str, key: str) -> list[Any]:
    """A card's saved-card records at a vendor under any key it goes by: its contact's id, the names it went by and the
    names phase 1 posted it under (SAP.vendor_keys, as a budget's vendor resolves)."""
    return SK.card_save_rows(ctx.c, card_id, SAP.vendor_keys(ctx.c, ctx.book.id, key))


def card_save(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Record that a card is saved on a vendor's account (§8.3), so the bot can buy there with it. One record per card
    and vendor: saving again after a removal reuses it, and saving one already saved is a conflict."""
    V.only(p, ("card_id", "vendor"))
    card = _card(ctx, who, V.text(p, "card_id", 80))
    card_id = str(card["id"])
    if str(card["status"]) == "cancelled":
        raise FinanceError(CONFLICT, "that card is cancelled")
    key, label = _vendor(ctx, V.text(p, "vendor", 120))
    rows = _saves(ctx, card_id, key)
    if any(int(r["active"]) for r in rows):
        raise FinanceError(CONFLICT, f"that card is already saved on {label}")
    row = next((r for r in rows if str(r["vendor_key"]) == key), None)

    def apply(aid: str) -> Result:
        if row is None:
            SK.insert(ctx.c, "fin_card_saves", {
                "book_id": ctx.book.id, "card_id": card_id, "vendor_key": key, "vendor_label": label, "active": 1,
                "saved_by": who, "saved_at": ctx.at, "removed_by": None, "removed_at": None, "created_at": ctx.at,
                "created_by": who, "updated_at": ctx.at})
        else:
            SK.update(ctx.c, "fin_card_saves", str(row["id"]), {
                "vendor_label": label, "active": 1, "saved_by": who, "saved_at": ctx.at, "removed_by": None,
                "removed_at": None, "updated_at": ctx.at})
        return {"card_id": card_id, "vendor": key, "vendor_label": label, "saved": True}
    return Plan(K.default_gate(ctx.org, who, "card_save"),
                f"Record card ••{card['last4'] or '----'} as saved on {label}", apply,
                facts={"holder": str(card["holder"]), "vendor": key})


def card_unsave(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Record that a card was removed from a vendor's account: the bot can no longer buy there with it. The record is
    found under the vendor's contact id or any name it went by, so a save made before the vendor became a contact
    still comes off."""
    V.only(p, ("card_id", "vendor"))
    card = _card(ctx, who, V.text(p, "card_id", 80))
    card_id = str(card["id"])
    key, label = _vendor(ctx, V.text(p, "vendor", 120))
    live = [r for r in _saves(ctx, card_id, key) if int(r["active"])]
    if not live:
        raise FinanceError(CONFLICT, f"that card is not saved on {label}")

    def apply(aid: str) -> Result:
        for row in live:
            SK.update(ctx.c, "fin_card_saves", str(row["id"]),
                      {"active": 0, "removed_by": who, "removed_at": ctx.at, "updated_at": ctx.at})
        return {"card_id": card_id, "vendor": key, "vendor_label": label, "saved": False}
    return Plan(K.default_gate(ctx.org, who, "card_unsave"),
                f"Record card ••{card['last4'] or '----'} as removed from {label}", apply,
                facts={"holder": str(card["holder"]), "vendor": key})


PLANNERS: dict[str, handlers.Planner] = {
    "card_create": card_create, "card_update_limit": card_update_limit, "card_freeze": _state("card_freeze"),
    "card_unfreeze": _state("card_unfreeze"), "card_cancel": _state("card_cancel"), "card_resolve": card_resolve,
    "card_save": card_save, "card_unsave": card_unsave}

__all__ = ["PLANNERS", "UNCERTAIN", "WAITING", "adopt", "candidates", "check_lower", "confirmed", "created",
           "issue_limit", "limit_follows", "not_issued", "queue", "sync", "uncertain", "used"]
