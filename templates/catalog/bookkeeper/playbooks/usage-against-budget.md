# Usage against budget

Two numbers sit side by side, and the summary reports both.

- The Usage page (account menu > Usage) estimates what the bots' model runs cost: the tokens each run used, priced at the
  provider's list price. It is an estimate, not an invoice. Runs on a ChatGPT or Claude sign-in are shown as "API-equivalent"
  and stay out of the estimate. Usage limits are set there by the owner.
- Finance budgets are set in Finance. A Finance budget for AI services pauses the bots it covers at its limit, and only spend
  through the Tico AI Gateway counts against it.

You report these numbers for the owner. You never set a limit or a budget.

Schedule: in the weekly cash summary (`playbooks/weekly-cash-summary.md`), and when a person asks.

## Read

- `hub api GET usage` for the estimated spend in the last seven days (`from` and `to` take UTC dates for another period;
  `group=bot` groups by bot).
- `hub api GET usage/limits` for the limits set.
- `hub finance report budget_vs_actual --start <first day> --end <last day>` for the Finance budgets.

A bot's own credentials may see nothing on the Usage page. If `usage` returns no rows or is refused, say on the summary
"Usage not readable by this bot, the owner reads it on the Usage page", then continue with the Finance budgets. Do not work
around a refusal.

## Steps

1. The estimated API spend for the period (`est_cost_usd`) and the share of the top three bots. If it is null, say "not
   priced", never zero. If `low_estimate_runs` is above zero, say the figure may be low.
2. Against each limit and each Finance budget that has an amount: spend, the limit, and the percent used. A budget with no
   amount is listed as "no amount set", not scored.
3. Any bot that rose by more than half against the period before: name it, with its figure and the owner who runs it. Do not
   diagnose it yourself.
4. At 90 percent or more of a limit or budget: tell its owner once, on a task, with the number.

Done when: the figures and their source are in the weekly summary, each near-limit item has been told to its owner once, and
anything you could not read is marked "not available".
