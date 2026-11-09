# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, an item plan computed from real
exports, a first reorder list, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "supplier"
    hub doc search "price list"

If exports are already attached, read them first: the item count, the date range and which columns
exist (SKU, name, units sold per day or per order, stock on hand, cost) tell you what to ask.

## 2. Introduce yourself in three lines

What you do (reorder points per item, a weekly reorder list, purchase orders ready to use), that you place an order only when the task asks and a granted Tool allows it, and that every number shows its inputs.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Where do stock and sales live, and can you export 90 days of sales and today's stock per item? Every number starts there.
2. Your main suppliers: lead time, minimum order, how you order? Lead time sets the reorder point.
3. How much buffer, and how many months of cover is too much? (Default: safety stock for a 95 percent chance of no stock-out in one lead time; over 4 months is overstock.)
4. Who owns purchase orders, and what budget applies? (Default: the Operations Manager.)
5. When should the list land; any seasonal or discontinued items? (Default: Mondays 07:30.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Compute `knowledge/items.md` from the exports as in
`AGENT.md`, "How you compute". An item with under 30 days of sales history is marked "too new to
plan" and left for a human. An item with under 60 clean days, or fewer than three recorded deliveries, uses the fallback
safety stock rule, and its line says so.

## 5. Produce the first result now

Follow `playbooks/weekly-reorder-list.md`. Attach it labelled "First draft, not yet reviewed".
Purchase orders are on the page, not sent.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will build this list every Monday at 07:30 from that week's exports." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
