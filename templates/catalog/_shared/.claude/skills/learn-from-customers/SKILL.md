---
name: learn-from-customers
description: Find out what customers actually do, need and struggle with, in two ways. One - interviews done right - pick who to talk to from the team's own data, ask about what they really did (never what they would do), record exact words, write each one up the same day. Two - synthesis at scale - read hundreds of tickets, call transcripts, reviews and requests, pull out evidence lines with exact quotes, and write findings where every claim links to its quotes, counts distinct accounts and states its confidence. Findings persist across runs in one evidence log. Use when a task or chat says customer interview, discovery, user research, talk to customers, voice of the customer, what are customers saying, why do they churn, why did they buy, synthesize feedback, themes, read the tickets, summarise the calls, or is this a real problem; before a spec that lacks real cases; and on a weekly research routine. Not for usability tests of a design (plan-a-usability-round) or win-loss calls for Sales.
metadata:
  function: product
  roles: [ux-researcher, customer-insights-analyst, feature-owner, product-manager, product-lead, product-analyst, growth-manager]
  actions: [read, draft, send]
  review: none
---
# Learn from customers

## What great looks like

Great customer research changes a decision, and anyone can check how it got there.

- **It starts from a decision.** "What will we do differently depending on the answer?" comes before any question to a customer. Research with no decision behind it is not run.
- **It trusts what people did over what they say.** The last time it happened, what they tried, what they paid, what they gave up on. Opinions, compliments and "I would use that" are not evidence.
- **Every claim shows its evidence.** A finding links to the exact quotes and records behind it. A reader can open any one and see the customer's own words in their place.
- **It counts customers, not mentions.** One account that wrote ten tickets is one account. The loudest few are never mistaken for the many.
- **It says how sure it is.** One account is an anecdote, two is emerging, three or more from different kinds of source is a pattern. It also says what it did not read, and what speaks against its own finding.
- **It builds up.** Each run adds to one evidence log, so the hundredth ticket strengthens or weakens a finding the team already holds.

## First time at a company

Read `knowledge/product.md`. If it has a **Customers** section, skip this part. If not, ask your owner these in one direct chat message, numbered, each with its default (`tico chat ask --to <person> "<the questions>"`). Start on the defaults at once; save the answers, dated, under **Customers** when they arrive.

1. **Where do customers' words live?** Default: tasks and their requesters, the support mailbox, call transcripts in Meetings, reviews and mentions that Listening collects, and the team's database.
2. **Who may I contact for research, and how often?** Default: customers who wrote to us or agreed to be contacted; at most one research request per customer in 90 days; never anyone marked do-not-contact, and never an account you name.
3. **Who does the talking?** Default: I interview in writing, by email or chat. A live call is run by a person you name, or by a bot where the team has set that up; I prepare the guide and read the transcript.
4. **Do we thank people with anything?** Default: no. A thank-you that costs money needs a budget from you first.
5. **What are our customer segments, and how big is each?** Default: the segments in the strategy (`tico strategy show --section serve`), with account counts from the team's database on the day.
6. **What may be kept?** Default: transcripts stay where the meeting or mail tool keeps them. Into `knowledge/` I copy only quotes, account ids and segment, never names or contact details.

## Steps

### 1. Name the decision

Write three lines on the task before reading anything:

- **Decision:** what the team will decide, and who decides it ("whether invoicing gets the next small appetite: Invoicing Feature Owner").
- **Questions:** one to three things you need to know to decide. Questions about the customer's world, not about your feature.
- **What would change it:** the answer that leads to "yes" and the answer that leads to "no". If no answer could change the decision, stop and say so.

### 2. Read what the team already knows

Open `knowledge/customers/findings.md` (if the team already keeps findings in another file, such as `knowledge/opportunities.md` or `knowledge/themes.md`, use that file; never start a second one). If a finding answers the question at pattern confidence and was checked in the last 90 days, answer from it with its evidence ids and stop.

### 3. Pick the method

Cheapest first. `references/pick-the-method.md` has the full table.

| You need to know | Use | Cost to customers |
|---|---|---|
| What people already told us, and how many | **Synthesis** of existing tickets, calls, reviews, requests (steps 9 to 14) | None |
| Why they do it, or what happened around it | **Interviews** (steps 4 to 8) | 20 to 30 minutes each |
| How many do it, how often | A query: ask the Product Analyst, or `tico db <database> --query <id>` | None |
| Whether they can use a design | A usability round: `skills/plan-a-usability-round/SKILL.md` | A session each |

Run synthesis before interviews whenever the team holds more than a handful of records on the topic. Interview to fill the gaps it leaves.

**Track A: interviews (steps 4 to 8).** Skip to step 9 when you are only reading existing records.

### 4. Choose who, from the team's own data

Pick from records, not from whoever is friendly. For each segment the decision concerns, draw from four groups:

- **Did it recently:** met the situation in the last 30 days (a query, a ticket, an order).
- **Tried and stopped:** started and gave up.
- **Left:** cancelled or went quiet in the last 90 days.
- **Asked:** requesters on a related task (`tico task requesters`; run it with `--help` for its options).

Start with five per segment, mixed across the groups. Leave out anyone contacted for research in the last 90 days and anyone marked do-not-contact. Write the selection in the task: the query or list used, its date, how many qualified, how many you chose and why those.

### 5. Invite them

Write the invitation from `references/interview-guide.md`: who you are (a bot says it is a bot), why them in particular ("you sent three invoices last week"), what you want to learn, how long, how their words are kept, and that "no" is fine. No pitch, no hint of what might be built.

Check your sending policy with `tico email policy --bot <your slug>`. **On their own:** draft and send (`tico email draft <address> --to <addr> --subject "..." --body-file <path>`, then `tico email send <address> --draft <id> --task <id>`). **Ask first:** the send waits for a person's OK on the exact text. **Never:** hand the drafts to the bot or person who owns that customer.

### 6. Write the guide

Follow `references/interview-guide.md`. In short:

- Open with the last time it happened: "Tell me about the last time you sent an invoice."
- Every question is about something that occurred. Never "would you", "do you think", "how likely".
- Two follow-ups ready for each: "What happened next?" and "What did you do about it?"
- Ask what they tried, what it cost them (time, money, a customer), and what they use instead.
- A **never** list on the guide itself: no pitching, no describing a feature, no leading ("was that frustrating?"), no promising.
- Close with: "What should I have asked that I didn't?" and "Who else deals with this?"

### 7. Run it

- **Consent first, on the record.** Before a call is recorded or transcribed, say so and get a yes from everyone on it. In writing, say how answers will be used. No yes: take notes only, or stop. See the recording note in `references/interview-guide.md`.
- **Mostly listen.** One question at a time. Let silence stand.
- **Follow the story, not the guide.** When they mention something they did, ask about that before returning to your list.
- **Steer away from fluff.** A compliment ("love the app"), a hypothetical ("I'd pay for that") or a wish list is a cue to ask "when did you last need that?" It is not written down as evidence.
- **Never promise** that anything will be built or when. If they ask: "I can't say what we'll build. What you've told me goes straight to the people who decide."
- **In writing:** three or four questions per message at most, each about a real event; one round of follow-ups; then thanks.

### 8. Write it up the same day

One snapshot per interview, in `knowledge/customers/snapshots/YYYY-MM-DD-<account id>.md` (or the team's existing snapshots folder), using the format in `references/evidence-and-findings.md`: the account by id, segment and group; their story in order; exact quotes; what they did; what surprised you. Then add its evidence lines to the log (step 10).

**When to stop:** when two interviews in a row add no new evidence line toward the questions. If twelve in one segment have not got you there, the questions are wrong: go back to step 1.

**Track B: synthesis at scale (steps 9 to 14).** Interview snapshots feed the same log, from step 10.

### 9. Define the pile and count it

Write in `knowledge/customers/read-log.md`: each source, the date range, and how many records it holds (for example: support mailbox, 2026-08-18 to 2026-11-18, 412 threads; `tico meeting search "invoice" --since 2026-08-18`, 23 calls; `tico task list --q "invoice" --status open --status done --status closed`, 31 tasks).

Read all of it when you can. When the pile is too big for one run, read everything from the last 30 days plus an even sample of the rest (every nth record, by date, across all sources), and write the share you read. Never describe a pile you only sampled as if you had read it all.

### 10. Extract evidence lines, not summaries

Go through the pile in batches. For each record that bears on the questions, add one line per distinct point to `knowledge/customers/evidence/YYYY-MM.md`, in the format in `references/evidence-and-findings.md`:

    E-0388 | 2026-11-03 | call M-1207 | account 4417 | solo electrician | did, unprompted | "I do the invoices on Sunday night, if I remember" | invoices four to six days after the job

Each line has: an id, the date, the source record's id, the account id, the segment, the kind (**did**, **said** or **asked**; **prompted** or **unprompted**), the quote copied exactly, and a context note of a few words. A summary of what "customers feel" is not an evidence line.

If you split the pile among helper agents, each returns evidence lines in this format and nothing else. You do the judging.

A teammate's report of what a customer said is secondhand: mark it so (this includes answers gathered with Growth's `learn-why-customers-buy`). Secondhand lines can support a finding, but a finding that rests only on them stays an anecdote.

### 11. Check every quote at its source

Before a quote is used in any finding, open the record it came from (`tico meeting read <id>`, `tico email thread <address> <thread-id>`, `tico task show <id>`) and confirm the words are there, exactly, said by the customer. A quote you cannot find is deleted, with its evidence line. This step is never skipped: a made-up quote is the worst error this skill can make.

### 12. Group into themes

Code each evidence line against the team's existing themes first. Open a new theme only when three or more lines from different accounts fit none. Name a theme as what the customer is trying to do or what stops them ("invoices go out days after the job"), never as a feature ("auto-invoicing"). Where the repository has `skills/tag-one-feedback-item/SKILL.md`, use its rules for a single item.

For piles too large to sort in one pass, Tico's `tico decision ask` can sort lines into themes from a questions file; you still check a sample of each theme by reading it.

### 13. Count, and check who is speaking

For each theme write:

- **Distinct accounts**, and how many kinds of source they came from.
- **By segment**, beside each segment's share of all customers. A theme that is 60% of the noise from a segment that is 5% of customers is a fact about that segment.
- **The loud few:** the share of the theme's lines that come from its three most frequent accounts. Over half means the theme may be three customers.
- **Did against said:** how many lines record something they did, and how many unprompted.

Then set the confidence: **anecdote** (1 account), **emerging** (2 accounts), **pattern** (3 or more distinct accounts, from 2 or more kinds of source).

### 14. Write findings

A finding is one sentence that could be wrong: who, in what situation, does or cannot do what. Write each in `knowledge/customers/findings.md` in the format in `references/evidence-and-findings.md`:

- The statement, its confidence, and the date checked.
- Counts: accounts, segments, sources.
- Two or three quotes with their evidence ids.
- **Against:** evidence that points the other way, or "none found in what was read".
- **Does not show:** what a reader might wrongly conclude ("does not show they would pay more").
- **Behaviour check:** a number from usage that agrees or disagrees, with query and date. Where what people say and what they do differ, write both and trust neither alone.

Update an existing finding in place when new evidence arrives: add the ids, recount, change the confidence if it moved, date it. Do not write a second finding for the same thing.

Keep to findings. A recommendation of what to build belongs to `brainstorm-a-feature` and to the Feature Owner.

### 15. Publish and hand on

Publish the findings as a page (layout in `references/evidence-and-findings.md`): `tico page publish research/<slug>.html --task <id> --title "<question> · Findings"`. Publish the findings file so teammates can search it: `tico file publish knowledge/customers/findings.md`.

Tell the owner of each affected area once, with the finding ids: `tico task comment <id> "..."` on their task, or `tico message send <bot> "<finding ids and one line each>" --needs-action` when a finding at pattern confidence touches their area and has no task. Where the repository has `skills/hand-off-feedback-evidence/SKILL.md`, use its format.

## Gates

- No research without a written decision and what would change it.
- No quote in a finding that was not re-opened at its source in this run. No quote is tidied, shortened without "..." or translated without saying so.
- No number, quote, customer or source is invented. A gap is written as a gap.
- No customer's name, email address or phone number in any file under `knowledge/` or on any page. Account ids and segment only.
- No finding above anecdote from one account, whatever its size. No finding called a pattern without three distinct accounts and two kinds of source.
- Counts are of distinct accounts. The share of the pile that was read is stated.
- No recording or transcript without everyone's consent on the record.
- No pitch, no promise and no date in any message or call with a customer.
- No contact with a customer outside the rules in **Customers** (who, how often, do-not-contact).
- Only team meetings and records you are allowed to read. A private meeting or mailbox is not a source.

## Autonomy

Do on your own: reading every source open to you, choosing interviewees, writing guides, running written interviews where your sending policy is **On their own**, snapshots, the evidence log, findings, the findings page, and handing on.

Needs a person:

- **Sending to customers** when your policy is **Ask first**: one OK bound to the exact invitation. A batch of identical invitations is one OK.
- **Spending** on thank-yous or a recruiting service, above the budget set.
- **Contacting an account the owner named**, or anyone outside the default rule.
- **A live call**, unless the team has set up bot-run calls.
- **Research with children, patients or other protected groups**, or any topic about health, money troubles or legal trouble: ask the owner first, and have the wording checked locally.

## Output

- **Findings page** (one page): the decision and the answer in three lines at the top; then one block per finding with its statement, a confidence mark, the counts, two or three quotes with evidence ids, what speaks against it; then what was read (sources, counts, share read) and what was not.
- **`knowledge/customers/findings.md`**: the standing list of findings, updated in place.
- **`knowledge/customers/evidence/YYYY-MM.md`**: the evidence log.
- **`knowledge/customers/read-log.md`**: what was read, when, how much.
- **Snapshots**, one per interview.
- **One message or comment** to each affected area owner.

## Checks before you finish

- The decision, the questions and "what would change it" are on the task.
- Every finding names its evidence ids; every id resolves to a line; every line resolves to a record.
- Every quote was re-opened at its source in this run.
- Each finding has counts of distinct accounts, its confidence, an "against" line and a "does not show" line.
- The loud-few share and the segment's share of customers are beside each theme's count.
- The read log says what was read and what share, and names what was not.
- No names or contact details anywhere. No recommendation of a feature.
- Interviewees were chosen from a recorded query or list, and none was contacted twice in 90 days.

## Example

Invented, for illustration. The team, its customers, every quote and every number are made up.

**Decision:** whether Larkspur's invoicing area gets the next small appetite (Invoicing Feature Owner). **Question:** what happens between finishing a job and sending its invoice? **Would change it:** "yes" if a pattern of accounts invoices late and loses something by it; "no" if lateness is rare or harmless.

**Read log (2026-11-10):** support mailbox 2026-08-10 to 2026-11-10, 412 threads, all read; 23 call transcripts mentioning "invoice", all read; 31 tasks. Not read: app-store reviews (none collected yet).

**F-03 · pattern · checked 2026-11-10.** Solo tradespeople send invoices days after finishing the job, because invoicing happens at home, not on site.
- Counts: 17 distinct accounts (14 solo, 3 two-person firms); tickets and calls. Solo trades are 71% of accounts and 82% of this theme. Top three accounts: 24% of lines.
- "I do the invoices on Sunday night, if I remember" (E-0388, account 4417, solo electrician, call, did, unprompted).
- "By the time I bill them they've forgotten what I did" (E-0402, account 2871, plumbing firm, ticket, did, unprompted).
- Against: 2 accounts say they prefer to batch invoices weekly (E-0391, E-0420).
- Does not show: that they would send from the site if they could. No record covers it, so interviews come next.
- Behaviour check: median 3.2 days from "job done" to "invoice sent" across 1,904 jobs (query `invoice-lag`, 2026-11-12). Agrees.

**Next:** five written interviews with accounts that marked a job done in the last 30 days: two who invoice the same day, three who do not (query `invoice-lag`, 2026-11-12: 212 qualified, 5 chosen across trades).
