---
name: brainstorm-a-feature
description: Turn a problem, a request or a hunch into two or three feature ideas worth a spec. Gathers ideas from four places (the bots that own each area, the owner, customers, and usage data), challenges the premise before any solution is offered, writes at least seven options including "do nothing" and "remove something", argues against the strongest ones, then converges to two or three candidates, each with its evidence, what it adds to the product, its biggest unknown and the cheapest test that would settle it. Use when a task or chat says brainstorm, ideas for, options for, how might we, what should we build, what could we do about, explore, or stress-test this idea; when a problem has no chosen solution yet; when the owner drops an idea in chat ("what if we..."); or on a weekly ideas routine. Not for cutting scope (use decide-what-not-to-build) and not for writing the spec (use write-the-spec).
metadata:
  function: product
  roles: [feature-owner, product-manager, product-lead, ux-researcher, product-analyst]
  actions: [read, draft]
  review: none
---
# Brainstorm a feature

## What great looks like

A great brainstorm starts from a problem a real customer has and ends with a short list a builder could start on tomorrow. It does four things most brainstorms skip:

- It **doubts the problem first.** Half of all feature ideas answer a question nobody checked. The premise is tested before a single solution is written.
- It **goes wide on purpose.** The first three ideas are the ones anyone would have. The useful ones come after, and at least one of them removes something instead of adding it.
- It **attacks its own favourites.** Each strong idea gets the best argument against it, written down, before it is allowed onto the short list.
- It **ends small and honest.** Two or three candidates, one recommended. Each says what is known, what is guessed, and the one unknown that could sink it.

It is fast: one run, not a workshop. Nothing is built from a brainstorm. Its output feeds `decide-what-not-to-build` and then `write-the-spec`.

## First time at a company

Read `knowledge/product.md`. If it has an **Ideas** section, skip this part. If not, ask your owner (the person your line in `tico team show` reports up to) these questions in one direct chat message, numbered, each with its default: `tico chat ask --to <person> "<the questions>"`. Do not wait for the reply. Start on the defaults, and save the answers, dated, under **Ideas** in `knowledge/product.md` when they arrive.

1. **What number should new features move right now?** Default: the KPI of the goal tied to an active bet (`tico goal list --all`); with none, the activation event in `knowledge/area.md`.
2. **Which customers come first?** Default: the segments the strategy serves now (`tico strategy show --section serve`).
3. **What will we never build?** Default: the strategy's "What we won't do" section, plus every "Don't build" row in `knowledge/not-building.md`.
4. **How many ideas may I put forward from one brainstorm?** Default: three candidates on the page, one carried forward.
5. **Where do your own ideas reach me?** Default: any chat with me, any task you file, and your comments on pages. I keep your words as you wrote them.

## Steps

### 1. Frame it

Read the task and its chat (`tico task show <id>`). Write the frame in five lines:

- **Who**: the kind of customer, by segment.
- **Trying to do what**: their goal in their words.
- **Blocked how**: what goes wrong today.
- **Why now**: what triggered this brainstorm, with its date.
- **Number**: the KPI or goal this would move, and its current value with source and date.

If the request arrived as a solution ("add an export button"), write the problem behind it and keep the solution as option A. If you cannot write "blocked how" from the record, that is the first unknown: say so and go to step 2 to find it.

### 2. Gather from the four sources

Read first. Ask a teammate only for what the record does not hold. For each source, note what you read and its date. A source you could not read is named as a gap, never skipped silently.

| Source | Where to read | What to take |
|---|---|---|
| **Area-owner bots** | Each Feature Owner's `knowledge/problems.md`; `tico update list --kind weekly`; the Support Owner's digest | The ranked problems near this frame, and each owner's own idea |
| **The owner** | The chat this came from; `tico strategy show` (bets, "What we won't do"); `memory/decisions.md`; `tico meeting search "<term>" --since <date>` | What they asked for in their own words, and the decisions already made |
| **Customers** | `knowledge/customers/findings.md` and its evidence log (from `learn-from-customers`); `knowledge/requests.md`; the requesters on related tasks (`tico task requesters`; run it with `--help` for its options); calls with `tico meeting search` and `tico meeting read <id>` | Findings with their account counts and quotes, by evidence id |
| **Data** | `tico kpi show <id>`; the team's database through `tico db list` and its named queries (`tico tool query-search <database> <words>`) | Where people drop off, how many hit the problem, how often |

Then three quick reads:

- **Already tried.** `tico task list --q "<two or three key words>" --status open --status done --status closed`. An open task for the same problem gets your evidence as a comment; you do not start a second one.
- **Market.** `tico market delta` and `tico market find "<competitor>"`. What others built is an input. It is never a reason.
- **Ask two area owners.** Send the owners of the two areas this touches most one question each: `tico message send <bot> "For <frame>: what would you do, and what would you remove?" --needs-action`. Carry on without waiting. A reply that comes later is added to the task.

If there is no customer synthesis from the last 30 days for this area, run `learn-from-customers` on a narrow window first, or go on and cap every candidate's evidence at "thin".

Before you quote a rate or a trend, follow `skills/check-event-counts-before-a-trend/SKILL.md` and `skills/read-an-area-retention-cohort/SKILL.md` where your repository has them.

### 3. Challenge the premise

Write no solution yet. List the three to five things that must be true for this problem to be worth solving, each as a sentence that could be false:

1. The problem is real: it happened to named accounts, recently.
2. It is a root problem, not a symptom. Ask "why does that happen?" up to three times, each answer from evidence. Stop at something structural.
3. It is frequent or costly enough to matter: how many accounts, how often, what it costs them.
4. These are customers the strategy serves.
5. It is the product's job. The fix is not better docs, a support reply, a price change or a clearer signup message.

Beside each, write its evidence with source and date, or the word **unproven**. Then answer three questions in one line each: What happens if we do nothing? Who is left out of the evidence we have? Who is content with how it works today?

If premise 1, 4 or 5 fails, stop. The result of the brainstorm is "not a feature": write why, route it (docs to the Librarian, the message to the Marketing Owner, a ticket pattern to the Support Owner) and finish. That is a good result.

### 4. Diverge

Write at least seven options, one line each, before you judge any. The list must contain:

- **Do nothing**, with what that costs.
- **Remove something**: delete the step, the setting or the screen that causes the problem.
- **The smallest version**: one screen, one rule, no settings.
- **A fix outside the product**: a default, a doc, an onboarding step, a support macro.
- **The opposite** of the obvious answer.
- **A bot does it**: the customer's work disappears instead of getting a better screen.
- **The owner's idea and each area owner's idea**, as they stated them.

Use the lenses in `references/lenses.md` when the list stalls at four. No option is scored, costed or dismissed in this step.

### 5. Provoke

Take the four or five strongest options. For each, write:

- **The strongest argument against it**, as its smartest critic would put it.
- **What must be true** for it to work: customers want it, they find it, it can be built in the appetite, it moves the number. Mark each as known (with source) or guessed.
- **What it adds**: new screens, settings, words a customer must learn, states to test. Count them.
- **What it makes worse**, and for whom.

Change or drop options that do not survive. For a second view from another model, file `tico task child <id> --check-model --title "Argue against these ideas" --body "<the frame and the options>"` and fold in what it finds.

### 6. Converge to two or three

Keep at most three. Rank them on four things, in this order:

1. **Evidence**: distinct accounts and kinds of source behind the problem this option solves. Three or more accounts from two or more kinds of source is a pattern; less is thin.
2. **Directness**: how straight the line is from the option to the number in the frame.
3. **Size**: small or big, in the team's appetite units (`knowledge/product.md`, Taste).
4. **Weight**: what it adds to the product, from step 5. Between two options that solve the problem, the one that adds less wins.

Recommend one and say why in two sentences. For each candidate, name its **biggest unknown** as a sentence that could be false, the **cheapest test** that could prove it false within a week (a count from records, a query, five conversations, a usability round), and the **result that would change the decision**, written before the test runs. `skills/choose-the-next-test/SKILL.md` has the method where your repository has it.

### 7. Capture and hand on

1. Build the ideas page from `references/idea-page.md` and publish it on the task: `tico page publish ideas/<slug>.html --task <id> --title "Ideas: <frame in six words>"`.
2. Add every option you set aside to `knowledge/ideas.md`: the option, the reason, the date, and what would bring it back.
3. Run the cheapest test for the recommended candidate now if it is a read, a count or a query. If it needs customers, follow `learn-from-customers`.
4. Carry the recommended candidate forward:
   - If this task is already the feature's Product task, go straight to `decide-what-not-to-build`, then `write-the-spec`. A person approves the Spec; that is their decision point, and you add no earlier one.
   - Otherwise file one task: `tico task create --pipeline product --owner <feature owner> --title "<the problem, in the customer's words>" --message "<frame, evidence with ids, the candidate in one line, its biggest unknown>"`. For a person's request add `--for human:<slug> --from-message <message id>`, so the task is theirs.
   - If a person asked you for the options, they choose: put one question on the task with your recommendation worded so "yes" accepts it (`tico task ask <id> --to <person> "<question>" --version N`).

## Gates

- No solution is written before the premise table exists.
- Every candidate carries at least one piece of real evidence with its source and date. A candidate with none is labelled **owner's judgment** or **bot's hypothesis** and is never described as customer demand.
- No number, quote or customer is invented. A missing fact is written "missing", with how to get it.
- A competitor having a feature is never the reason for a candidate.
- An idea that contradicts the strategy's "What we won't do" is not filed. It goes to the owner as one question, with the evidence that made you raise it.
- Nothing is built, and no spec is started, until the candidate has passed `decide-what-not-to-build`.
- One task per problem. New evidence for an open task is a comment on it.
- Nothing in this skill tells a customer what will be built or when.

## Autonomy

Do on your own: read every source, ask other bots, run the whole brainstorm, publish the page, park ideas, file the recommended candidate, and run any test that is a read, a count or a query.

Needs an OK or a person:

- Contacting customers when your sending policy is **Ask first** (`tico email policy --bot <your slug>`): one OK for the exact message and list (`learn-from-customers` has the steps).
- A test that spends money above your budget.
- An idea that goes against the written strategy: the owner decides.
- Where the Product pipeline files a bot's own idea snoozed, only a person unsnoozes it. Leave it snoozed; do not work around it.

Never wait on a person to pick between your candidates unless they asked for the choice. Carry your recommendation to the Spec, where a person approves or sends it back.

## Output

- **The ideas page** on the task: the frame and the recommendation in three lines at the top; a table of the two or three candidates; the opportunity map (number, problems, options, tests) as a nested list or inline SVG; the premise table; what was set aside and why. Where the problem shows on a screen, put a real screenshot of that screen today at the top (`tico bot app-shot /#/<screen> --width 1280 --out shots/<screen>.png`), never a drawing.
- **`knowledge/ideas.md`**: the parked options.
- **One Product task** for the recommended candidate, or the next step on the task you are in.
- **A one-line note on the task**: the recommendation, the page, the test started, what you could not read.

## Checks before you finish

- The frame names a customer segment, a blocker and a number with its source and date.
- The premise table is on the page, and every row has evidence or says "unproven".
- At least seven options were written, including do nothing and remove something.
- Each surviving option has an argument against it in writing.
- There are at most three candidates and exactly one recommendation.
- Each candidate has its evidence, size, weight, biggest unknown, cheapest test and the result that would change the decision.
- Every quote has an evidence id and every number a source and a date.
- Set-aside options are in `knowledge/ideas.md` with a reason.
- No customer was promised anything.

## Example

Invented, for illustration. The team, its customers and every number are made up.

Larkspur makes invoicing software for self-employed tradespeople. Task: "Customers say they get paid late. Ideas?"

- **Frame.** Who: solo electricians and plumbers. Trying to: get paid for a finished job. Blocked: the invoice goes out days after the job. Why now: finding F-03 reached pattern on 2026-11-10. Number: median days from "job done" to "invoice sent", 3.2 (query `invoice-lag`, 2026-11-12, 1,904 jobs).
- **Premise.** Real: 17 accounts, tickets and calls (F-03). Root: "why late?" they write invoices at night; "why at night?" the invoice screen needs a desk, not a van. Product's job: yes, no doc fixes it. Unproven: that sending sooner gets them paid sooner.
- **Options (9).** Do nothing. Remove the separate invoice step: marking a job done sends the invoice. A one-tap "Send invoice" on the job screen. Reminders to the tradesperson at 6 pm. Automatic reminders to the customer who owes. A payment link by text message. Deposits before the job. A bot drafts the invoice from the job notes. Weekly batch invoicing.
- **Provoke.** Against "done sends the invoice": prices often change on site, and a wrong invoice costs more than a late one. Changed to: done opens a ready invoice with one Send button.
- **Candidates.** 1 (recommended): done opens a ready invoice, one tap to send. Evidence: pattern, 17 accounts. Size: small. Adds: one button, no settings. Biggest unknown: "Most jobs are invoiced at the quoted price." Test: query the share of invoices equal to their quote in the last 90 days; under 60% changes the design to an edit-first screen. 2: payment link by text. Evidence: thin, 2 accounts. 3: reminders to the customer who owes. Evidence: thin, owner's judgment.
- **Handed on.** Candidate 1 goes to `decide-what-not-to-build`. Seven options parked in `knowledge/ideas.md`.
