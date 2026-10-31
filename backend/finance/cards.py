"""Cards (spec §4.5, §8): person cards on the company's card program, each linked to a budget. A card's provider limit
is its allocation, reserved against the budget tree when set, so the cards together cannot pass the budget. Every
change is an action; what Mercury must do is queued in fin_outbox under one key and sent by finance/sync.py outside
the transaction. Tico shows the last 4 only and never has the number or CVC.

The card row holds the desired state (status, limit_amount) and what Mercury last confirmed (provider_status,
provider_limit). One `card_sync` write per card at a time carries no values: the worker reads the latest desired state
when it sends, so a superseded change is never sent, and a card's creation reads it too (a freeze, cancel or limit
made before Mercury issued the card applies once it does). A lower limit or a cancel keeps the old reservation until
Mercury confirms it (`allocation_due`), so the budget never counts on room the card can still spend."""
from typing import Any

from . import handlers
from .context import Ctx
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL

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
    already waiting (not yet claimed) covers any later change, so a second one is not queued."""
    if op == "card_sync" and SK.card_sync_waiting(ctx.c, str(card["id"])):
        SK.update(ctx.c, "fin_cards", str(card["id"]), {"sync_status": "pending", "sync_error": None,
                                                        "updated_at": ctx.at})
        return
    money = SK.get(ctx.c, "fin_money_accounts", str(card["money_account_id"] or ""))
    SK.outbox_add(ctx.c, connection_id=str(money["connection_id"]) if money is not None and money["connection_id"]
                  else None, action_id=aid, op=op, payload={"card_id": str(card["id"]), **body}, key=key, by=by,
                  at=ctx.at)
    SK.update(ctx.c, "fin_cards", str(card["id"]), {"sync_status": "pending", "sync_error": None,
                                                    "updated_at": ctx.at})


def sync(ctx: Ctx, card_id: str, aid: str, by: str) -> None:
    """Bring Mercury to this card's desired state (once the card exists there)."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is not None and card["provider_card_id"]:
        queue(ctx, card, "card_sync", {}, f"card-sync-{card_id}-{aid or ctx.at}", aid, by)


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


def confirmed(ctx: Ctx, card_id: str, done: dict[str, Any]) -> None:
    """What Mercury confirmed for a card. Once it matches the desired state, a lower reservation waiting on it
    (`allocation_due`) takes effect."""
    fields: dict[str, object] = {"synced_at": ctx.at}
    if "status" in done:
        fields["provider_status"] = str(done["status"])
    if "limit" in done:
        fields["provider_limit"] = int(done["limit"])
    SK.update(ctx.c, "fin_cards", card_id, fields)
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None or not in_step(card):
        return
    SK.update(ctx.c, "fin_cards", card_id, {"sync_status": "ok", "sync_error": None})
    if card["allocation_due"] is not None and card["budget_id"]:
        SB.upsert_allocation(ctx.c, budget_id=str(card["budget_id"]), target_kind="card", target_id=card_id,
                             amount=int(card["allocation_due"]), currency=ctx.book.base_currency, by=SYSTEM,
                             at=ctx.at)
        SK.update(ctx.c, "fin_cards", card_id, {"allocation_due": None})


def created(ctx: Ctx, card_id: str, aid: str) -> None:
    """Mercury issued the card: apply what changed meanwhile."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None:
        return
    confirmed(ctx, card_id, {})
    if not in_step(SK.get(ctx.c, "fin_cards", card_id)):
        sync(ctx, card_id, aid, SYSTEM)


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


def card_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A person card for a teammate under a budget, with `amount` a month set aside for it. `provider_user_id` is the
    Mercury user the card is issued to (Mercury issues cards to its own users)."""
    V.only(p, ("holder", "budget_id", "amount", "nickname", "money_account_id", "provider_user_id"))
    holder = V.text(p, "holder", 120)
    if holder not in ctx.org.humans | ctx.org.bots:
        raise invalid("no such teammate")
    program = _program(ctx, V.opt_text(p, "money_account_id", 80))
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a card's monthly limit is more than 0")
    card_id = SL.new_id()
    alloc = handlers.allocation_plan(ctx, who, {"budget_id": V.text(p, "budget_id", 80), "target_kind": "card",
                                                "target_id": card_id, "amount": amount})
    budget_id = V.text(p, "budget_id", 80)
    nickname = V.opt_text(p, "nickname", 50) or holder.split(":", 1)[-1]
    user = V.text(p, "provider_user_id", 120)

    def apply(aid: str) -> Result:
        alloc.apply(aid)
        SK.insert(ctx.c, "fin_cards", {
            "id": card_id, "provider": str(program["provider"] or "mercury"), "holder": holder, "budget_id": budget_id,
            "money_account_id": str(program["id"]), "kind": "person", "limit_amount": amount, "limit_interval": "month",
            "currency": ctx.book.base_currency, "status": "active", "nickname": nickname, "created_at": ctx.at,
            "created_by": who, "updated_at": ctx.at})
        queue(ctx, SK.get(ctx.c, "fin_cards", card_id), "card_create",
              {"user_id": user, "nickname": nickname, "monthly": amount}, f"card-create-{card_id}", aid, who)
        return {"card_id": card_id, "sync_status": "pending"}
    return Plan(K.default_gate(ctx.org, who, "card_create"), f"Issue a card to {holder} with {amount / 100:.2f} a "
                f"month from {ctx.budgets()[budget_id].name}", apply,
                facts={"amount": amount, "holder": holder, "budget_id": budget_id, "card.kind": "person"})


def card_update_limit(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Change a card's monthly limit: its allocation moves (and must fit), and the provider limit follows."""
    V.only(p, ("card_id", "amount"))
    card = _card(ctx, who, V.text(p, "card_id", 80))
    if str(card["status"]) == "cancelled":
        raise FinanceError(CONFLICT, "that card is cancelled")
    amount = V.amount(p)
    if amount <= 0:
        raise invalid("a card's monthly limit is more than 0; freeze or cancel it instead")
    check_lower(ctx, card, amount)
    alloc = handlers.allocation_plan(ctx, who, {"budget_id": str(card["budget_id"]), "target_kind": "card",
                                                "target_id": str(card["id"]), "amount": amount})

    def apply(aid: str) -> Result:
        alloc.apply(aid)                            # the limit follows the allocation (`limit_follows`)
        return {"card_id": str(card["id"]), "limit": amount}
    return Plan(K.default_gate(ctx.org, who, "card_update_limit"), f"Set card ••{card['last4'] or '----'}'s limit to "
                f"{amount / 100:.2f} a month", apply,
                facts={"amount": amount, "holder": str(card["holder"]), "budget_id": str(card["budget_id"] or "")})


def _state(kind: str) -> handlers.Planner:
    op, status = STATE_OPS[kind]

    def planner(ctx: Ctx, who: str, p: V.Payload) -> Plan:
        """Freeze, unfreeze or cancel a card (freeze also takes a budget: every active card on it and its children)."""
        V.only(p, ("card_id", "budget_id", "reason") if kind == "card_freeze" else ("card_id", "reason"))
        if kind == "card_freeze" and "budget_id" in p:
            from .budgeting import subtree
            bid = V.text(p, "budget_id", 80)
            b = ctx.budgets().get(bid)
            if b is None or (who != SYSTEM and not handlers.sees_budget(ctx, who, b)):
                raise FinanceError(NOT_FOUND, "no such budget")
            cards = list(SK.cards_on_budgets(ctx.c, subtree(ctx, bid)))
            if not cards:
                raise FinanceError(CONFLICT, "that budget has no active cards")
        else:
            cards = [_card(ctx, who, V.text(p, "card_id", 80))]
        for card in cards:
            if str(card["status"]) == status:
                raise FinanceError(CONFLICT, f"that card is already {status}")
            if str(card["status"]) == "cancelled":
                raise FinanceError(CONFLICT, "that card is cancelled")
        if kind == "card_unfreeze":
            from .budgeting import state
            from .core import dates
            b = ctx.budgets().get(str(cards[0]["budget_id"] or ""))
            if b is not None and b.at_limit == "decline":
                left = state(ctx, b.id, dates.day(ctx.today)).remaining
                if left is not None and left <= 0:
                    raise denied(f"the {b.name} budget is used up; raise it before unfreezing")

        def apply(aid: str) -> Result:
            for card in cards:
                fields: dict[str, object] = {"status": status, "updated_at": ctx.at}
                if kind == "card_cancel":
                    fields["allocation_due"] = 0         # its reservation goes when Mercury confirms the cancel
                SK.update(ctx.c, "fin_cards", str(card["id"]), fields)
                if kind == "card_cancel" and not card["provider_card_id"] and \
                        not SK.card_create_waiting(ctx.c, str(card["id"])):
                    not_issued(ctx, str(card["id"]))       # never issued and not being issued: nothing to undo
                    continue
                sync(ctx, str(card["id"]), aid, who)
            return {"cards": [str(card["id"]) for card in cards], "status": status}
        what = f"{op.capitalize()} {len(cards)} card(s)" if len(cards) > 1 else \
            f"{op.capitalize()} card ••{cards[0]['last4'] or '----'}"
        return Plan(K.default_gate(ctx.org, who, kind), what, apply,
                    facts={"holder": [str(card["holder"]) for card in cards], "cards": len(cards)})
    return planner


def check_lower(ctx: Ctx, card: Any, amount: int) -> None:
    """A card's limit can't go below what it has already used this period (holds and settled spend)."""
    have = used(ctx, card)
    if 0 < amount < have:
        raise invalid(f"the card has used {have / 100:.2f} this period; its limit can't go below that")


def limit_follows(ctx: Ctx, card_id: str, amount: int, aid: str, by: str, lowered: bool) -> None:
    """An allocation on a card target moves the card's provider limit too (0 freezes it). A lower one (`lowered`) left
    the reservation as it was: it takes effect when Mercury confirms (`allocation_due`)."""
    card = SK.get(ctx.c, "fin_cards", card_id)
    if card is None or str(card["status"]) == "cancelled":
        return
    fields: dict[str, object] = {"updated_at": ctx.at, "allocation_due": amount if lowered else None}
    if amount <= 0:
        fields["status"] = "frozen"
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


PLANNERS: dict[str, handlers.Planner] = {
    "card_create": card_create, "card_update_limit": card_update_limit, "card_freeze": _state("card_freeze"),
    "card_unfreeze": _state("card_unfreeze"), "card_cancel": _state("card_cancel")}

__all__ = ["PLANNERS", "check_lower", "confirmed", "created", "limit_follows", "not_issued", "queue", "sync",
           "used"]
