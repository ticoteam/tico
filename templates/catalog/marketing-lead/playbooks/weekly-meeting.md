# Weekly growth meeting

When: Mondays at 08:00 (routine `weekly-growth-meeting`), or when the owner asks. One turn, about 30 minutes. The outcome
is verdicts written, ideas scored, and one question to the owner with the picks ticked. Nothing else waits for a person.

If there is nothing to decide (no tests due, no unscored ideas, no ideas ready), finish the task with "Nothing to decide this
week" and the readings in one line. Do not ask.

---

## 1. Read the week

    hub growth week

It returns the readings (KPIs with a growth role), the tests due (testing, window ended or ending within 7 days), the top
ideas by ICE, the verdicts from the last 14 days and any guardrail breached. Note each guardrail: a breach goes on the
question in step 5.

Budget left per channel: `hub growth channel list` (`budget_month`), and the team's Finance budget if it is connected.

Who may do what: scoring an idea needs its creator, its assignee or an admin; starting a test and giving its verdict need
its assignee or an admin. Before you score, start or judge an idea someone else holds, take it:
`hub task update <task id> --owner marketing-lead`. If you will not take it, ask its assignee on the task instead.

## 2. Write the verdicts that are due

For each test in `tests_due` whose window has ended, read the idea and its judged KPI:

    hub growth idea show <task id>
    hub kpi show <judged_kpi_id>

Apply `playbooks/verdict.md` and write it with `hub growth idea verdict <task id> --status <won|lost|inconclusive> ...`.
A verdict you write stands unless the owner changes it. Do not wait for a second read.

A test whose window is still open is not judged. List it in the question as "ending soon", with its end date.

If a test was ended early by its stop rule, its verdict is Lost even if the window is still open. The tests due list does
not show a test that ends later, so also check `hub growth idea list --status testing` for a stop rule that has fired.

## 3. Score new ideas

    hub growth idea list --status idea

Score only an idea with no ICE yet (`ice` is null): `hub growth idea score <task id> --i <n> --c <n> --e <n>`. Never change
an idea's score once it has one; the owner may have set it. A new idea a person added with the + Idea button has no ICE
until this step.

## 4. Pick the tests to start

Candidates: the `top_ideas` from step 1 with status `idea`. Apply the capacity rule before you offer any pick:

- at most six tests running at once across the team;
- at most two new tests per channel owner at a time, counting tests already running for that owner;
- a test needs a channel with a stage of `testing` or `scaling`, and budget left for its spend.

A pick that breaks the rule waits for next Monday, and the question says so.

## 5. Ask one question

Put the picks to the owner as one multi-pick question on this task. Options are "LABEL -> WHAT HAPPENS" and there are two to
six of them. Recommend the picks you would start. Name the money on the label for any idea that spends. `--spend` takes one
pick: ticking that option approves its spend, and no other option does. Any other paid pick needs its own approval
(`hub approval request --kind spend`) and does not start until it is approved. Name any breached guardrail and every test
ending soon in `--why`.

    hub task question "Which of these should we test first?" --on <owner person ref> --multi \
      --title "Weekly growth, week <NN>" --parent <this task id> \
      --why "Verdicts: <idea> <verdict>. Ending soon: <idea and end date, or none>. Guardrails: <none or the breach>." \
      --option "Price calculator -> starts Monday, no spend" \
      --option "Competitor bids -> two weeks, <amount> on Google Ads" \
      --option "Manager meetups -> sponsor one for <amount>, after its own spend approval" \
      --recommend "Price calculator" --recommend "Competitor bids" \
      --spend "Competitor bids=<amount>:Google Ads, two weeks" \
      --if-no-answer "Start the ticked picks that cost nothing" --default-kind reversible

Use the owner's person ref from `hub team show`. Two rules for the `--if-no-answer` line: only reversible work goes ahead on
its own, and a pick with money waits for a yes.

## 6. On the answer

For each picked idea, in this order:

1. Start it, with a test window, a change, a stop rule and the judged KPI. The judged KPI is the North Star or a conversion
   to it; an input KPI is only a tripwire.

       hub growth idea start <task id> --from <YYYY-MM-DD> --to <YYYY-MM-DD> \
         --change "<what changes, in one line>" --stop-if "<rule, e.g. cost per booking above 120 after 400 spent>" \
         --judged-kpi <kpi id> --min-events <n> --audience "<who it reaches>"

2. File a subtask for each channel owner, with the channel label. Order matters: link later steps with `blocked_by`.

       hub task child <idea task id> --owner <channel owner slug> --title "<one step>"
       hub task label <child task id> --add channel:<channel slug>
       hub task relate <later task id> <earlier task id> --kind blocked_by

3. Spend that the question did not cover needs its own approval: `hub approval request --kind spend --payload-file <file> --task <idea task id>`.
   Money the owner ticked is already approved by `--spend`. The approval belongs to this bot, which asked the question: its id
   is `spend_approval` on the answer (`hub task show <question task id>`). Put that id in the spend subtask's body
   (`hub task child <idea task id> --owner <channel owner slug> --title "<step>" --body "Spend approval <id>: <amount> on <what>"`),
   and this bot spends it once, when the money is committed.

Picks the owner did not tick stay at status `idea`.

## 7. Finish

    hub task update <id> --status done --note "<verdicts written; picks started; waiting on the owner>"

Finish the task even when the question is open. The owner's answer arrives on the question task; run step 6 for it in a
turn of its own.
