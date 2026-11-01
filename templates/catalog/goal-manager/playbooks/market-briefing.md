# Market briefing

A task titled "Market briefing: <title>". The server opens it when the Librarian's weekly briefing touches an active
bet: an item names a market entity the bet cites, or the entity its thesis wrong-if watches. Budget 10 minutes.

Inputs: the task's briefing title, `hub market briefing latest`, and the bets it touched.

**Never edit the strategy.** The only output is, at most, one `strategy_draft` proposal per bet that is no longer
right. Doing nothing is a normal result.

## 1. Read what touched

    hub market briefing latest

`touched` lists the bets (their number, id and statement); `items` lists what happened, each with the entity ids it
names. With `touched` empty, finish the task with one line and stop.

## 2. Weigh each touched bet

For each bet in `touched`:

- `hub strategy show`: the bet's statement, why, rules out, wrong-if and cites.
- The items that name its entities, and `hub market show <entity id>` for the evidence behind them.
- Ask one question: does this change what the bet rests on, or only add to it? A competitor shipping something the bet
  already expected, a price move inside the range the bet assumed, a hire: these keep the bet. A fact that the bet's
  "why" or "wrong if" says would be a problem: this is a reason to propose.
- `hub proposal list`: a pending `strategy_draft` about this bet means it was already said; do not file another.

## 3. Propose only when the bet is no longer right

One `strategy_draft` proposal per such bet, with `"source": "briefing"` in the payload, doing one of three things:
retire it, rewrite it, or keep it with a new wrong-if.

    hub proposal create --kind strategy_draft --payload-file bet.json --reason "<the item, with its week, then your pick and why>"

The reason is a few lines: what the briefing said, which of the three you recommend, and why in one sentence. Pick
one; do not leave a menu. Cite the entity so the evidence stays one click away. Never invent a number.

## 4. Finish

`hub task update <id> --status done --note "<bets weighed; proposals filed or none>"`. Add one line to `state.md` and
commit this repository.

**Done when:** every touched bet is weighed, at most one proposal per bet that is no longer right is filed, and the task is done with its note.
