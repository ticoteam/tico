One file per recurring kind of work. A playbook is the checklist you would hand a new hire.

- `commands.md`: the hub commands for goal work, by job, with their arguments. Run only these; read it before your first hub call.
- `which-run.md`: which playbook a run needs, by the work it starts from.
- `levels.md`: what Passive, level 2 and level 3 each allow, and the limit on Needs you items.
- `strategy-and-bets.md`: the strategy page, bets, goals, KPIs, and the commands that read and write them.
- `draft-the-strategy.md`: a task "Draft the strategy", or the owner asking through their Assistant. From the market pages and the
  default guide, build claims the owner confirms or strikes, propose three to five bets and the four sections as
  one `strategy_draft` proposal, and ask the owner to confirm and publish. Never write the page, never publish.
- `bet-tripped.md`: a task "Bet N: may be wrong", filed by the server when a bet's wrong-if trips, at every level.
  One Decide card: retire, rewrite, or keep with a new wrong-if, and your pick.
- `run-a-lens.md`: the owner asks for a lens, or a tripped bet or a weekly review calls for one. Answer its questions
  from the published strategy and the market, run it, and the server files at most one `strategy_draft` proposal.
- `market-briefing.md`: a task "Market briefing", opened by the server when the Librarian's weekly briefing touches a bet.
  Weigh each touched bet; propose (source `briefing`) only when one is no longer right.
- `kpi-pass.md`: the daily routine. Every KPI due on its cadence, one pass, a time budget each, then the status pass.
- `new-kpi.md`: what to build the first time a KPI needs computing, and how to change one that is wrong.
- `status-pass.md`: `hub goal refresh` after the readings, and how to report what it did.
- `check-ins.md`: when a KPI slips, ask the goal's owner and record the answer.
- `goals-make-sense.md`: flag vague or duplicate goals; suggest a measure once or clearer words. Missing optional measures are not a problem.
- `weekly-goals-review.md`: the short review for the owner.
- `recoveries.md`: where the Strategy Bot is on at level 2, recoveries for red or stalled goals and merged asks.
- `acts-on-its-own.md`: at level 3, decisions on bot tasks for red or stalled goals and clashes between Heads,
  answering bots' questions from recorded rules and decisions, and BotOps asks for broken bots.
- `owner-chat.md`: a task the owner's Assistant hands you, quoting their words: advice, or the change they asked for.

## Proposals
Every change to a goal or a KPI that is not yours to make is a proposal the owner confirms. Write the payload
to a file, then `hub proposal create --kind <kind> [--goal ID] [--kpi ID] --payload-file f.json --reason "..."`.
`hub proposal list` shows what is pending; look before you propose the same thing again.

| kind | needs | payload |
|---|---|---|
| `flag` | `--goal` | `{"issue": "vague" \| "duplicate", "note": "one sentence"}` |
| `goal_wording` | `--goal` | `{"title": "...", "body": "...", "bet_id": "..."}` (any of them; `bet_id` links the goal to a bet) |
| `goal_kpi` | `--goal` | `{"kpi_id": "..."}` to link an existing KPI, or `{"kpi": {"name", "definition", "unit", "direction", "cadence", "source_note"}, "owner": "...", "target": {...}}` for a new one |
| `kpi_definition` | `--kpi` | any of `{"name", "definition", "unit", "direction", "cadence", "source_note"}` |
| `goal_why` | `--goal` | `{"why": "one line tying the goal to the vision"}` (only where the Strategy Bot is on, and only for a goal with no bet) |
| `recovery` | `--goal` | `{"evidence": "...", "options": [{"label": "...", "actions": [...]}], "pick": 0}` (the Strategy Bot at level 2; `playbooks/recoveries.md`) |
| `kpi_target` | `--kpi` and `--goal` | `{"kind": "improve", "baseline": 40, "target": 65, "deadline": "2026-12-31"}` or `{"kind": "maintain", "min": 40, "max": 60}` |
| `strategy_draft` | nothing | any of `{"summary", "vision", "sections": {"serve", "sell", "know", "not", "extra": {key: {"title", "body"}}}, "bets": [{"id"?, "statement", "why"?, "rules_out"?, "wrong_if"?, "cites"?, "status"?, "ideas"?}]}`, where `wrong_if` is `{"kind": "kpi", "kpi_id", "direction": "above" \| "below", "threshold", "check_by"}`, `{"kind": "thesis", "market_id", "check_by"}` or `{"kind": "text", "text", "check_by"}`. An optional `"source"` says what prompted it (`bet_tripped`, `lens`, `briefing`). Partial: only the fields you send change. The owner confirms, and confirming writes the draft and the bets as them, at every level (`playbooks/draft-the-strategy.md`, `playbooks/bet-tripped.md`). Publishing stays a separate click of theirs |

`direction` is up, down or range; `cadence` is daily, weekly or monthly. An improvement target needs a target and a
deadline; a range needs a min, a max or both. The server refuses anything else with the reason.
