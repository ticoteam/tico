---
name: decide-what-not-to-build
description: Decide what gets added to the product and what does not, with taste as the test. Taste here means simplicity - cut everything you can and still have it work, finish what stays to a high standard, and be very hard to convince about additions. Gives each request or scope item one of four verdicts (build now, build later, don't build, not a product problem), sets the appetite, keeps the NOT-in-scope ledger, and turns a no into a short message the requester accepts. Use when a task or chat says should we build, can you add, feature request, is this worth building, prioritise, what is in or out of scope, cut scope, trim, v1, the spec is growing, say no, decline or push back; before every spec; whenever a customer, a teammate or the owner asks for a feature; and when a build is running past its appetite.
metadata:
  function: product
  roles: [feature-owner, product-manager, product-lead, product-operations-manager]
  actions: [read, draft, send]
  review: none
---
# Decide what not to build

## What great looks like

Taste is simplicity. A product with taste does a few things completely, and every part of it earns its place. The people who build such products share four habits:

- **They subtract first.** Before adding anything they ask whether the problem goes away by removing a step, changing a default or using what is already there.
- **They cut to the core and then finish it.** The scope shrinks until one more cut would stop it working. What is left is built to a high standard: every state handled, every word chosen, nothing half-done.
- **They are very hard to convince about additions.** Every screen, setting and option is paid for by each customer who never needed it, and by the team that tests, documents and supports it for as long as the product lives. A good idea is not enough. It has to be worth that.
- **They say no well.** The answer is fast, plain and specific, the person feels understood, and the decision is written down so nobody argues it again next month.

"Not yet" is a real answer, different from "no", and it comes with the event that would turn it into "yes".

## First time at a company

Read `knowledge/product.md`. If it has a **Taste** section, skip this part. If not, ask your owner these in one direct chat message, numbered, each with its default (`tico chat ask --to <person> "<the questions>"`). Start on the defaults at once; save the answers, dated, under **Taste** when they arrive.

1. **In one sentence, what is the product for, and who is it not for?** Default: the summary and "What we won't do" from `tico strategy show`.
2. **How big may one feature be (the appetite)?** Default: **small** is one Product task with one pull request a reviewer reads in one sitting. **Big** is one parent task with at most five such tasks under it. Bigger than big is not one feature: it is split or declined. A team where people build may use time instead: small is one to two weeks for a designer and one or two engineers, big is six weeks.
3. **When may I add a setting or an option?** Default: only when two real customers need opposite behaviour. Otherwise we choose the behaviour and ship no setting.
4. **Which customers should hear a "no" from you first?** Default: I answer everyone myself, except your ten largest accounts by revenue and anyone you name; those answers come to you as a draft.
5. **When do we look again at "build later"?** Default: when its trigger happens, or after 90 days, whichever is first.
6. **Is there anything we have already decided never to add?** Default: nothing beyond the strategy. I record each decision in `knowledge/not-building.md` as it is made.

## Steps

### 1. State the problem behind the request

Read the request where it was made (`tico task show <id>`, the ticket, the call with `tico meeting read <id>`). Keep the requester's own words in a comment on the task.

Write the problem in one sentence: who is trying to do what, and what stops them. The feature named in the request is one possible answer, not the problem. Then ask the first taste question: **is this a root problem or a surface request?** Ask "why do they need that?" up to three times, answering from the record each time. If you cannot state the problem, ask the requester once, with your best guess worded so "yes" confirms it.

### 2. Look at what already exists

- **The product may already do it.** Try it in the app or the demo. If it does, the answer is a how-to, and the real finding is that people cannot find it: send that to the owner of the screen or the docs.
- **There may be a task.** `tico task list --q "<key words>" --status open --status done --status closed`. If one is open, add this requester to it (`tico task requester-save`; run it with `--help` for its options) and add the evidence as a comment. Stop here.
- **It may be decided.** Search `knowledge/not-building.md`. If it was declined and nothing in its "back if" line has happened, the answer is the same: cite the row, add the new requester to it, and go to step 9.

### 3. Weigh the evidence

From `knowledge/customers/findings.md`, the requests file (`knowledge/requests.md`, where the team keeps one) and usage data (`tico db list`, `tico kpi show <id>`), write:

- **Distinct accounts** that have the problem, and from how many kinds of source (tickets, calls, usage, reviews). One account that asked five times is one account.
- **Failure or wish.** Something broken outranks something missing, whatever the counts.
- **Who they are.** A segment the strategy serves now, later, or not at all.
- **Loud or broad.** The share of all customers who have it, beside the share of the noise they make. Three large accounts asking weekly is not a third of your customers.
- **What it costs them today**, in their words or in a number, with source and date.

Three or more distinct accounts from two or more kinds of source is a **pattern**. Less is **thin** (what `learn-from-customers` calls an anecdote or emerging). Use those two words.

### 4. Count the weight

Write what the addition would add to the product, as counts:

- New screens, new settings, new options, new words a customer must learn.
- New states: empty, loading, error, full, no permission, two people at once.
- New things to test, document and answer support questions about, for as long as it exists.
- What it overlaps or makes slower, and what a new customer now has to read past.

`references/cut-list.md` has the full list. An addition whose weight you cannot count is not understood well enough to approve.

### 5. Try to solve it by taking away

Go down this ladder and stop at the first rung that solves the problem from step 1:

1. **Remove the cause.** Delete the step, the field or the rule that creates the problem.
2. **Change a default.**
3. **Use what is there.** An existing screen or feature does it with a small change.
4. **Fix it outside the product.** Words on the screen, a doc, the setup flow, a support reply. Route it to the bot that owns that.
5. **Add the smallest thing.** One kind of customer, one path, one screen, no settings.

For rung 5, write the sketch and cut it: for each part ask **"if this is gone, does it still solve the problem?"** If yes, it is out. Keep cutting until the next cut would break it. That is the scope.

### 6. Fit the appetite

Set the appetite (small or big, from `knowledge/product.md`) before any design, from what the problem is worth, not from how long the solution seems to need. Then:

- If the scope from step 5 does not fit, cut scope. Never raise the appetite quietly: a raised appetite is the owner's decision, dated, with the reason.
- Find the **rabbit holes**: parts too unknown or open-ended to bet on (an integration with no docs, a data migration, permissions with many roles). Each one gets a fix written now, a cut, or a spike with its own time limit.
- Write the **no-gos**: the nearby things a reasonable reader would assume are included. One line each.

For a feature that is already chosen and growing, pick one scope mode and say which: **Reduce** (cut to the core; use it when over the appetite or when the evidence covers only part), **Hold** (scope stays, effort goes into finishing; the default for a fix), **Pick** (scope stays, and each proposed addition is judged on its own against step 7; the default for a new capability), or **Expand** (rare: only when the larger version is simpler for the customer, and only with the owner's word, because it raises the appetite).

### 7. Give one verdict

Each request, and each part of a scope, gets exactly one:

| Verdict | When | What it needs |
|---|---|---|
| **Build now** | All six tests below pass | The smallest version, its appetite, and a Product task |
| **Build later** | The problem is real, but the evidence is thin, something it depends on is missing, or stronger work is ahead of it | A trigger (an event or a number that makes it "now") and a look-again date |
| **Don't build** | Not a root problem, not for customers we serve, against "What we won't do", or the weight is more than it is worth | One specific reason, and what would have to change |
| **Not a product problem** | Rung 4 solved it | The bot or person it is routed to |

The six tests for **Build now**:

1. The problem is stated without naming the feature.
2. The evidence is a pattern; or it is a failure; or the owner said to build it, recorded with the date as "owner's decision".
3. It is for customers the strategy serves, and it does not contradict "What we won't do".
4. Nothing higher on the ladder in step 5 solves it.
5. The smallest version fits the appetite **and can be finished** to the quality bar in `references/cut-list.md`: every state it touches, phone and desktop, the customer's own words, the product's existing patterns. If the appetite cannot cover the finish, cut scope again. Never cut the finish.
6. It does not push aside work with stronger evidence (`tico pipeline show product`; the Feature Owner's `knowledge/problems.md`).

The tests decide; a score only orders. Where the team keeps scores (`knowledge/scoring.md`, `playbooks/score-a-request.md`), use them to rank the items that passed against each other. A high score never turns a failed test into a pass.

When the owner's own idea fails a test, say so once: the evidence, and your recommended alternative worded so "yes" accepts it. Then do what the owner says. Do not argue twice.

### 8. Write the ledger

Add one row per verdict that is not "Build now" to `knowledge/not-building.md`, in the format in `references/ledger.md`: the item, the verdict, the reason in one specific line, the evidence on the day (accounts, sources, date), who asked (task or ticket ids, never names), the "back if" trigger and the look-again date. When the decision belongs to a spec, the same lines become the spec's **Out** list.

After each change, publish the ledger so every teammate can find it: `tico file publish knowledge/not-building.md`.

### 9. Deliver the answer

A no is an answer, and it is owed quickly. Write it from `references/no-message.md`. In order, in under 100 words:

1. **Their problem, in their words.** One sentence that proves you understood what they are trying to do.
2. **The answer.** "We're not going to build this" or "not now". In the first two sentences, never buried.
3. **The real reason.** One sentence, specific to this request.
4. **What they can do today.** A workaround or an existing feature that you tested yourself. If there is none, say so.
5. **What would change it.** For "later", the trigger. No date and no promise.

Then send it where they asked:

- **Your own task or another bot's.** `tico task comment <id> "<the message>"`, then `tico task close <id> -m "<the reason>" --version N`. For "later": `tico task snooze <id> --until <look-again date> -m "<the trigger>" --version N`.
- **A task a person asked for.** A bot cannot close it. Comment with the message, then ask that person one question: `tico task ask <id> --to <person> "Not building this, for the reason above. Close it?" --version N`.
- **A customer.** Add them to the task's requesters so they hear if it changes (`tico task requester-save`). If the team has a Support Owner, give it the message on the ticket's task and it sends. If not, draft the reply (`tico email draft <address> --reply-to <thread-id> --body-file <path>`) and send it (`tico email send <address> --draft <id> --task <id>`). Tico applies your sending policy: **On their own** sends after a short undo window, **Ask first** waits for a person's OK on the exact text, **Never** means hand the draft to whoever owns that customer.

### 10. Look again when the trigger fires

Once a week (in your weekly review routine, such as the Feature Owner's `playbooks/weekly-area-review.md`), read the ledger for rows whose look-again date has passed or whose trigger happened (new accounts were added, a dependency shipped). Run steps 3 to 7 once on each, update the row in place with the date, and tell the requesters only if the verdict changed.

## Gates

- No verdict before the problem is written without the feature's name.
- **Build now** needs all six tests. A wish never outranks a failure. A competitor having it passes no test.
- No new setting or option unless two real customers need opposite behaviour (or the team's own rule from `knowledge/product.md`).
- The appetite is never raised without the owner's dated decision.
- What stays is finished. If the quality bar does not fit the appetite, scope is cut again.
- Every verdict except "Build now" has a ledger row **before** its message is sent.
- A no-message contains no promise, no date, no "it's on the roadmap", and no workaround you did not test.
- No number or quote in the reasoning is invented. Thin evidence is called thin.
- A bot never closes a task a person asked for, and never reverses a decision the owner recorded.
- Removing a feature customers already use is not a quiet cut: it needs the usage count (accounts in the last 90 days, from a query) and the owner's OK.

## Autonomy

Do on your own: every verdict on requests and scope in your area, cutting your own specs, the ledger, snoozing and closing bot-filed tasks, answering teammates, and answering customers where your sending policy is **On their own**, or through the Support Owner.

Needs an OK or the owner:

- Raising an appetite, or choosing **Expand**.
- Removing a shipped feature that has users.
- A "no" to one of the accounts the owner named in `knowledge/product.md` (default: the ten largest by revenue): the owner sees the draft first.
- A customer reply when your sending policy is **Ask first** (`tico email policy --bot <your slug>`): Tico holds the send until a person OKs that exact text.
- A request that only makes sense if the strategy changes: the owner decides, framed with `.claude/skills/frame-a-decision-for-the-owner/SKILL.md` where your repository has it.

Everything else you decide. Do not send routine verdicts to a person to confirm.

## Output

- **The verdict** as a comment on the task, five lines: the problem; the evidence (pattern or thin, accounts, sources, date); the weight; the verdict and its reason; what happens next.
- **The scope table** for a chosen feature, three columns: **In**, **Later** (with trigger), **Out** (with reason). It goes into the spec's Scope section as it is.
- **The ledger row** in `knowledge/not-building.md`, published.
- **The message** to the requester, sent or drafted.
- **A Product task** for each "Build now" that has none, handed to `write-the-spec`.

## Checks before you finish

- The problem sentence does not contain the feature's name.
- The evidence line says pattern or thin, with distinct accounts, kinds of source and a date.
- The ladder in step 5 was walked, and the verdict comment names the rung that solved it.
- Every part of the scope was asked "if this is gone, does it still work?"
- The appetite is written, with who set it and when; each rabbit hole has a fix, a cut or a timed spike; the no-gos name at least one thing a reader would assume.
- Each item has exactly one verdict; each "later" has a trigger and a date; each "don't" has a specific reason.
- The ledger row exists and is published.
- The message is under 100 words, leads with their problem and the answer, and makes no promise.
- Requesters are recorded on the task or the ledger row.

## Example

Invented, for illustration. The team, its customers and every number are made up.

**A scope.** Larkspur's candidate: "marking a job done opens a ready invoice with one Send button". Appetite: small (set by the Feature Owner, 2026-11-18). Mode: Reduce.

| In | Later | Out |
|---|---|---|
| Done opens the invoice, filled from the quote | Send by text message (back if: 5 accounts ask; 2 today) | Sending with no tap: a wrong invoice costs more than a late one |
| One Send button on the job screen | Editing lines on the job screen (back if: under 60% of invoices equal their quote) | A setting for send time: nobody needs opposite behaviour |
| The existing invoice email, unchanged | | A new invoice layout: not this problem |

Rabbit hole: jobs with no quote. Fix: the invoice opens empty with the customer filled in, as today.

**A request.** Account 3310 asks for "custom invoice templates: our colours, our layout". Problem: they want invoices that look like their business. Already exists: the logo and business details print on every invoice. Evidence: thin, 2 accounts, tickets only, as of 2026-11-18. Weight: a template editor, 6 or more settings, every invoice change tested against every layout. Verdict: **Don't build.** Ledger row written. Message:

> You want your invoices to look like your business, not like ours. We're not going to add custom layouts and colours: very few customers have needed them, and every layout option makes invoices slower to set up for everyone. Your logo and business details already print at the top of each invoice (Settings, Business, Logo); I checked it on a test account today. If the standard layout turns out to cost many trades real work, we'll look again, and you'll hear from us because you're on the request.
