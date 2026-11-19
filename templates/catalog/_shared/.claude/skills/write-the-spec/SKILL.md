---
name: write-the-spec
description: Write the spec for a feature, a change or a bug as one visual page a person can OK from the page alone. The fewest words that get the idea across, real high-resolution (2x) before-and-after screenshots ahead of text, clarity above everything; one page by default, never cut clarity to fit a word count. Grounded in real customer requests, real data and real scenarios, with every use case the change touches walked through and either covered by a checkable criterion or named as out. Built with Tico's build-the-page templates and published as the task's Spec. Use when a task is in the Spec stage, when its instructions name write-a-spec or a spec page, or when a chat says spec, write up, PRD, requirements, brief for the builder, acceptance criteria, define done, or what exactly are we building; after brainstorm-a-feature and decide-what-not-to-build have picked what to build; and whenever a builder, reviewer or QA bot says the spec is unclear. Not for choosing what to build (use decide-what-not-to-build first).
metadata:
  function: product
  roles: [feature-owner, product-manager, product-lead]
  actions: [read, draft]
  review: none
---
# Write the spec

## What great looks like

A great spec is the shortest page from which a stranger would build the right thing. The person who OKs it reads the top in a minute, on a phone, and knows exactly what they are agreeing to. The bot that builds from it never has to guess.

- **Clarity above all.** One reading, one meaning. Where a sentence could be read two ways, it is rewritten or replaced by a picture or an example.
- **Pictures before words.** The real screen today and the real screen after, sharp at high resolution, with numbered arrows, carry the idea. Text only says what a picture cannot.
- **The fewest words.** Every sentence names a number, a screen, a customer or a rule. A sentence that would fit another spec is cut. Length follows clarity: one page by default, split when that helps, and as long as the use cases need, never cut below clear to meet a word count.
- **Real, not imagined.** It rests on requests customers actually made, numbers pulled from live data on a stated date, and scenarios that actually happened. Nothing is invented; a gap is marked "missing".
- **The use cases are thought through.** Each real scenario is walked from start to finish, and each state the change touches (empty, the limit, no permission, sent twice) ends in a criterion or in a plain "out".

## First time at a company

Read `knowledge/product.md` and, where the repository has it, `knowledge/spec-format.md`. If the **Specs** section exists, skip this part. If not, ask your owner these in one direct chat message, numbered, each with its default (`tico chat ask --to <person> "<the questions>"`). Start on the defaults at once; save the answers, dated, under **Specs** when they arrive.

1. **Who OKs specs, and how do they read them?** Default: any person on the team, on a phone, in about a minute. So the top of the page carries the whole decision.
2. **Does every spec need a section beyond the template's?** Default: no. Anything you name is saved in `knowledge/spec-format.md` and added to the details of every spec.
3. **How do I name customers in a spec?** Default: by account id and kind ("account 4417, solo electrician"). Never a name, an email address or a phone number.
4. **Where does the app run for the "after" screenshot?** Default: the demo or dev server described in `knowledge/standards.md`. Where there is none, the spec shows today's screen with arrows and says in the caption what will change.
5. **What do you want asked before a spec is written, not assumed?** Default: only what changes who the feature is for, what a customer pays, or what the company is legally bound to. Everything else I write as a labelled assumption that your OK confirms.
6. **What is the default measure of success?** Default: the area's activation event in `knowledge/area.md`. A target nobody has set is written "missing: to be set", never invented.

## Steps

### 1. Start from what is decided

    tico task show <id>

Read the chat the feature started from and every comment. Then read what earlier work already settled, and reuse it instead of asking again:

- The ideas page and its chosen candidate (`ideas/<slug>.html`, from `brainstorm-a-feature`).
- The verdict comment and the scope table (In, Later, Out) with the appetite, from `decide-what-not-to-build`. If there is none, run that skill's steps 5 to 7 now. A spec without a cut scope is not started.
- `knowledge/problems.md` and any spec already in `specs/` for the same problem. Extend an existing spec; never start a second file.

Pick the template: a bug (`bug-spec`), a new feature or a change on a screen (`screen-spec`), or a change with no screen such as an API, a command or a job (`change-spec`).

**A bug:** reproduce it before writing anything (a screenshot, the call and its output, or the query and its rows) and find the root cause in the code with `path:line`. If part of the cause is unknown, say which part and how it will be found. Never guess one.

### 2. Collect the real material

Three piles. Each item carries where it came from and its date.

| Pile | Where | What you need |
|---|---|---|
| **Requests** | The task's requesters (`tico task requesters`; run it with `--help` for its options); `knowledge/customers/findings.md` and its evidence log; `knowledge/requests.md`; tickets and calls (`tico meeting search "<term>"`, `tico meeting read <id>`) | How many distinct accounts, from which kinds of source, and one or two quotes copied exactly, each with its evidence id |
| **Data** | `tico kpi show <id>`; the team's named queries (`tico db list`, `tico tool query-search <database> <words>`, `tico db <database> --query <id>`) | How many customers meet the problem and how often: the number, what it is out of, the query's name, the date |
| **Scenarios** | The same tickets and calls, and the records themselves | Three to five cases that really happened: which account (by id and kind), what they were doing, what went wrong, what it cost them |

Where a pile is empty, write `missing: <what>, <how to get it>` and carry that line into the spec. Do not fill the gap with a plausible number or a typical-sounding customer. If a quote or a number cannot be traced to its record, it does not go in.

### 3. Draft first, ask second

Write the whole spec before asking anyone anything. Where you had to choose, write the choice as **Assumed:** with one line of why. The OK on the spec confirms every assumption the page shows, so a labelled assumption is a question answered without a meeting.

Ask before publishing only when the answer changes what is built and falls under what the owner wants asked (question 5 above). Then ask once, on the task: numbered, each with your recommended answer worded so "yes" accepts it (`tico task ask <id> --to <person> "<questions>" --version N`). An open question holds the task until it is answered, so ask only these. Everything else goes in **Open questions** with who owes the answer and by when.

### 4. Walk the use cases

This is where specs fail: the main path is clear and everything around it is left to the builder.

1. **List who acts**: each kind of customer and teammate the change touches, including the one who receives the result (the customer's customer, an admin, a bot).
2. **Walk each real scenario** from step 2 through the new behaviour, from what starts it to the end result. Note each point where the scenario does something the main path did not expect.
3. **Fill the grid** in `references/use-case-grid.md`: for each actor, the states the change touches (first time, empty, one, many, the limit, no permission, offline or slow, sent twice, two people at once, a time boundary, undo, data that exists already).
4. **Give every touched cell an ending**: a numbered criterion, or an **Out** line with its reason. No cell is left to be guessed.
5. **Stop where it stops mattering.** A state the change does not touch is not listed at all.

Each scenario from step 2 must end in at least one criterion. If a real case is not covered, either the scope is wrong or the case goes to **Out** by name.

### 5. Write the top

The top is all some readers will read. In `<div id="lead">`, at most 70 words:

- **Goal:** who it is for and what they can do afterwards that they cannot now. One sentence. (A bug spec opens with **Report:**, the customer's report quoted and cut to two lines, then the root cause, then the goal.)
- **Decide:** what the reader's OK means, and anything they must know before giving it: an assumption that matters, a cost, a privacy or legal point.

Then the pictures, before any detail. One before-and-after pair per screen, in the order a person meets the screens, at phone width (390 px) and desktop width. Put a red numbered arrow on each thing that changes, and one line under the picture for each number. Use the same number for the same thing in the before, the after and the criterion that checks it.

Pictures are real screenshots of the app, never drawings or mock-ups, taken at high resolution: retina, a device pixel ratio of 2 (a 390 px wide phone screen is captured 780 px wide). A blurry or 1x screenshot is retaken. "After" is the app running with the smallest local change that shows the result. `.claude/skills/build-the-page/SKILL.md` says how to take them and how to set arrows. A change with no screen shows the real call and its real output, before and after.

### 6. Write the details

Below the line, in this order. `references/spec-sections.md` says what belongs in each and shows a good and a bad line.

1. **Done when.** Numbered criteria, one behaviour each, as Given / When / Then, each tagged *before release* or *after launch*. Follow `skills/write-acceptance-criteria/SKILL.md` where your repository has it. A bot must be able to check each one with a test, a call or a screenshot.
2. **Scope.** **In** and **Out**, copied from the scope table. Every Out is something a reasonable reader would have assumed was included, with its reason. There is no "nice to have": a thing is in, or it is out.
3. **Real cases.** Add `<section id="scenarios">`. One line per scenario from step 2: the account by id and kind, what happened, and the criteria that now cover it.
4. **Why now and how we will know.** The evidence, each line with its count, source and date. The appetite. The measure, its baseline, the target, the read date and the guard that must not get worse (`skills/write-how-we-will-know/SKILL.md`).
5. **Plan.** The steps in order, with the files or screens each touches and the test that proves it. Read the code before writing this.
6. **Release needs.** What ships it; data changes, settings, flags. "None" where none.
7. **Open questions.** Each with who owes the answer and a date. "None" when agreed.

### 7. Cut the words

Go through the page once with `references/fewest-words.md`. The short version:

- Read each sentence and ask: would it fit another spec? If yes, cut it.
- A paragraph that compares things becomes a table. A paragraph that describes a screen becomes a picture. A rule with exceptions becomes two examples.
- One term for one thing, the customer's term, the same on every line.
- No background, no history, no summary of the top, no adjectives ("simple", "fast", "easy") without a number.

Then put back anything whose absence makes a reader guess. Short is not the goal. No guessing is the goal, in the fewest words.

### 8. Let length follow clarity

One page is the default, and most specs fit it. Some do not: many actors, many states, money or data that cannot be wrong. Then:

- **Never pad and never squeeze.** Do not cut a touched use case to save space. Do not add text to look thorough.
- **Split when that helps.** A spec that keeps growing is usually two features. Split it when each part can be understood, built and OK'd on its own: a parent task holds the goal, the map of parts and the scope table, and each part is its own task with its own spec (`tico task file --parent <id> --owner <slug> "<title>"`).
- **Length is a warning, not a refusal.** As of 2026-11-18, `page.py check` reports a screen or bug spec over 1,200 words and a change spec over 1,000, not counting pictures and code blocks; Tico's page check treats spec length as a warning, not a refusal. Treat the warning as a prompt to run step 7 again and to see whether a split helps. Never cut clarity to meet the word count: if the page still needs the words to be clear, keep them, keep them on the page (never "attached separately"), and leave the length warning standing. Say so in a task comment with the word count and the reason. Every other finding is always fixed.

### 9. Check it against reality

Before publishing, read the things a spec can quietly contradict:

- **Room.** `tico pipeline show product`: what is already in Build, and whether this depends on any of it (`tico task relate <id> depends_on <task>`).
- **Direction.** `tico strategy show`: it serves a segment the strategy names, and breaks nothing under "What we won't do".
- **Appetite.** The plan fits the appetite that was set. If not, cut scope with `decide-what-not-to-build`; do not raise the appetite.
- **Other areas.** Each screen or record the plan touches that another Feature Owner owns: name that bot in Open questions so Related input reaches it.
- **Money, privacy, law.** If the change bills a customer, shows one customer's data to another, sends messages to customers or changes what the terms promise, say so in the **Decide** line.

### 10. Read it as a stranger

Read the page top to bottom as someone who has never seen the task or the product, and list every place you would have to guess: an undefined word, a "should", a state with no ending, a number with no source. Fix each one.

For a big-appetite spec, get a second read from a different model: `tico task child <id> --check-model --title "Read this spec cold" --body "List everything a builder would have to guess, and every sentence that can be read two ways. Do not suggest features."` Fix what it finds; do not argue with it in the task.

### 11. Build, check, publish, look

    python3 .claude/skills/build-the-page/page.py build specs/<slug>-draft.html --out specs/<slug>.html
    python3 .claude/skills/build-the-page/page.py check specs/<slug>.html --kind <bug-spec|screen-spec|change-spec>
    tico page publish specs/<slug>.html --task <id> --as Spec --title "<Feature> · Spec"
    python3 .claude/skills/build-the-page/page.py render <file id> --kind <kind>

Fix everything `check` names. Open both pictures `render` writes and look: the Goal and Decide at the top, every screenshot drawn, each arrow on its target, nothing cut off at phone width. Fix and publish again (a new version) until it is right.

If `tico task show <id>` still lists the Spec as a missing requirement, attach the page to it: `tico task attach <id> page:<file id> --req "<requirement>" --version N`.

Publishing with `--as Spec` submits the spec. The stage's checker reads it for three things only: the goal, the scope with what is in and what is out, and what done looks like as criteria someone can check. A person, never a bot, gives the OK. Do not message anyone to ask for it.

### 12. Keep it true

- **Related input.** Other bots comment once. Take what makes the spec clearer or catches a missed case; publish a new version. What you do not take gets a line in the ledger (`knowledge/not-building.md`) with the reason.
- **Sent back.** Fix exactly what was named, publish a new version, say in one comment what changed.
- **During the build.** The spec is the reference. If the build shows the plan was wrong, change the spec first, then the code. A change to the goal, the scope or a criterion after the OK is a new version that says what changed at the top; it is never made silently.

## Gates

- The goal, the scope (in and out) and the criteria are all present and all checkable.
- Every number has its source, its date and what it is out of. Every gap is written "missing", with how to fill it.
- Every quote is copied exactly from a record you opened in this run, with its evidence id. No customer's name or contact details anywhere.
- Every picture is a real screenshot at high resolution (retina, device pixel ratio 2). None is drawn or mocked up, and none shows one person's real data.
- Every real scenario maps to a criterion or to an Out line by name. Every touched state has an ending.
- Nothing is "nice to have", "ideally", "should" or "phase 2" without a ledger row.
- No question that would change what is built is still open when the page is published as Spec.
- `page.py check` reports nothing (or only the length warning, under step 8's rule), and the published page was looked at, rendered, at both widths.
- The appetite on the page is the one that was set. It was not raised to fit the plan.

## Autonomy

Do on your own: the whole spec, from reading to publishing, including choosing between reasonable options (written as assumptions), splitting a feature into parts, and new versions after input.

Needs a person:

- **The OK on the spec.** The stage asks for it; you never do.
- **A raised appetite**, or anything in step 9's last line (billing, another customer's data, messages to customers, the terms): put it in the Decide line so the OK covers it knowingly.
- **Contacting a customer** to fill a gap in the evidence: follow `learn-from-customers`, which holds the sending rules.

Never promise a customer that something will be built or when, in the spec or anywhere else.

## Output

One page, `specs/<slug>.html`, published as the task's **Spec**:

1. **Top:** Goal (or Report, cause, goal) and Decide, in at most 70 words.
2. **Pictures:** real before and after screenshots at phone and desktop width, at device pixel ratio 2, with numbered arrows and one line per number.
3. **Details:** Done when, Scope, Real cases, Why now and how we will know, Plan, Release needs, Open questions.

Plus: a ledger row for every Out and Later (`knowledge/not-building.md`), sub-tasks where the feature was split, and one comment on the task naming what is assumed and what is missing.

## Checks before you finish

- A stranger can say from the top alone what will be built and what their OK means.
- Each arrow number appears in the before, the after and a criterion.
- Each criterion has one When, one observable Then and a check tag. None uses "fast", "easy" or "simple" without a number.
- Each Out names something a reader would have assumed, and why it is out.
- Each real case lists the criteria that cover it.
- Every number shows source, date and denominator. Every "missing" says how to get it.
- No sentence would fit another spec. No section restates the top.
- One term per thing, throughout.
- Every screenshot is real and sharp at 2x; none is a drawing or a 1x capture.
- No clear sentence or touched use case was cut to meet a word count.
- `check` reported nothing, or only the length warning with the reason in a task comment; the rendered page was opened and looked at, at 390 px and desktop width.
- The file id is attached where `tico task show` says the Spec goes.

## Example

Invented, for illustration. The team, its customers and every number are made up. Pictures are described, since this file cannot show them.

**Invoice on done · Spec · 2026-11-18** (template `screen-spec`)

> **Goal:** A tradesperson who marks a job done can send its invoice with one tap, before leaving the site.
> **Decide:** OK this spec, or send it back with what is wrong. Assumed: the invoice is filled from the quote and is never sent without a tap.

*Pictures.* Job screen, phone, today: arrow 1 on "Mark done", which returns to the job list. Job screen, phone, with the change: arrow 1 on "Mark done"; arrow 2 on the invoice that opens, filled; arrow 3 on Send. The same pair at desktop width.

**Done when**
1. Given a job with an accepted quote, when the tradesperson taps Mark done (arrow 1), then the invoice opens with the quote's lines, total and customer (arrow 2). *before release*
2. Given that invoice, when they tap Send (arrow 3), then the customer receives the existing invoice email once, and the job shows "Invoiced". *before release*
3. Given a job with no quote, when they tap Mark done, then the invoice opens with the customer filled and no lines, as today. *before release*
4. Given Send was tapped twice, then one invoice exists and one email was sent. *before release*
5. Given no connection, when they tap Send, then the invoice is saved as "Ready to send" and the screen says so. *before release*
6. Median days from done to sent falls below 1.0 for jobs done after release. *after launch, read 2026-12-16 by the Product Analyst*

**Scope.** In: done opens the invoice; one Send button; the existing email. Out: sending with no tap (a wrong invoice costs more than a late one); sending by text message (later: back if 5 accounts ask, 2 today); editing lines on the job screen; a new invoice layout.

**Real cases**
- Account 4417, solo electrician: finished four jobs on 2026-10-27 and invoiced all four on 2026-11-01, from home. Covered by 1, 2.
- Account 2871, two-person plumbing firm: half their jobs have no quote. Covered by 3.
- Account 3052, gardener: works where there is no signal. Covered by 5.

**Why now and how we will know.** Finding F-03 (pattern, 2026-11-10): 17 accounts send invoices days after the job, from tickets and calls. "I do the invoices on Sunday night, if I remember" (E-0388, account 4417). Median days from done to sent: 3.2 across 1,904 jobs (query `invoice-lag`, 2026-11-12). Appetite: small. Target: under 1.0; source: missing, to be set by the owner. Guard: invoices edited after sending must not rise above today's 4% (query `invoice-edits`, 2026-11-12).
