# Draft the strategy

A task titled "Draft the strategy" (a number after it means the owner asked again), or the owner asking for the
strategy through their Assistant. The owner wants the Strategy page to say how the team means to win, in words they
recognise, so that every bot reads the same summary on every run and the goals have bets to hang from. You
propose; they write, confirm and publish. A strategy with no trade-offs is a description, so "What we won't do" is
part of every draft.

Inputs: `tico strategy show` and `tico strategy versions`, the market pages (`tico market show company/self`, `tico doc search --market`), `knowledge/company.md`, the goals and KPIs, the Growth numbers and what customers said (`playbooks/keep-proof-current.md`), and the owner's words in the task.

The outcome is one `strategy_draft` proposal the owner can confirm and publish after twenty minutes of their time:
a summary of at most 400 characters, the vision (at most 400), the seven sections filled or marked unknown, and three
to five bets, each with a statement, why, what it rules out and what would prove it wrong. Nothing else counts as
good.

**Budget.** About 30 minutes of your time and at most eight claims put to the owner over the whole run. Your
working draft is in a file within five minutes; after that you rewrite it as you learn, never from scratch. When
the budget runs out, propose what you have, mark what is missing, and hand it to the owner.

**Never invent a number.** A market size, a price, a share, a head count, a growth rate or a wrong-if threshold
appears only when a market page, a KPI reading or a source you read this run states it, cited the way the Librarian
cites it, or when the owner said it. Otherwise the section says "unknown" and the gap is a claim for the owner or an
Open question for the Librarian. No round figures, no "typically", no "roughly" from your own memory.

**You propose at every level.** The draft, the bets and every version are the owner's: `tico strategy draft`,
`tico strategy bet` and `tico strategy publish` refuse you, and asking another way is not a workaround. This is the
same at Passive, Suggests actions and Acts on its own: the result is always one proposal.

---

## 1. Read what the team already knows

Start from the record, not from memory.

- `tico strategy show`: the published version, if there is one, with its bets, `goals_by_bet` and `health_by_bet`.
  You do not see the owner's draft; only they do. If a version is published, this run is a revision: read
  `tico strategy versions` and propose only what should change, saying why in the proposal's reason.
- The questions to turn into claims are the default guide's, in the style of the YC application
  and a16z's go-to-market writing: what you make and who needs it; how bad the problem is and what they use instead;
  the wedge you enter through; how you know people want it; why now; why this team; what you understand that others
  don't; who you fear and what stops a bigger company copying you; how you make money, how much, and the price that
  breaks; where the next 100 customers come from; the bet most people would call wrong; what you won't do.
  Lenses (`playbooks/run-a-lens.md`) come after a first strategy is published, not before.
- The market: `tico market show company/self` (who the team is, from the Librarian's research), then
  `tico doc search --market "<the team's name>"`, the Overview and Theses pages, and Open questions. The market
  pages are the evidence the bets cite. If the market is empty, say so in the proposal's reason: the Librarian's
  "Set up the market map" task fills it, and you do not do its research.
- `knowledge/company.md` and the setup answers in the task: what the team does, for whom, and the words it uses.
- The goals and KPIs: `tico goal list --all`, `tico kpi list`. A live goal often shows a bet the team already made
  without writing it down, and a KPI is the best wrong-if a bet can have.

## 2. Build claims, not questions

Write the draft as if you were right, then put each position to the owner as a claim they keep, edit or strike.
"Your site implies you compete on price; that rules out white-glove service. Keep, edit or strike." is a claim;
"How do you compete?" is a question. The owner answers a claim in one word; they answer a question with an essay,
or not at all. Where the evidence supports two readings, pick the one the team's own behaviour shows (its pricing
page, its customers, its goals) and name the fact that made you pick it.

Each claim is one sentence with its evidence and what it rules out. Start with the claims that would change the
draft most: the bets, then "What we know that others don't", then the rest.

## 3. Propose three to five bets

The bets are the spine of the page: decisions the team already lives by, most of them unwritten. Find them in what
the team does, not in what it says: a pricing page that undercuts every competitor is a bet on price; a market page
that shows one segment and five lookalikes is a bet on a niche; a goal to automate a workflow is a bet on cost.
Each bet has four fields:

- **statement**: one sentence, a decision that could turn out wrong.
- **why**: the evidence or belief behind it, in one or two sentences, with the market page or source it rests on.
  Put those market ids in `cites`.
- **rules_out**: what the team gives up by choosing it. A bet that rules out nothing is not a bet: rewrite it or
  drop it.
- **wrong_if**: the observation that would retire it. In this order of preference:
  1. a KPI with a threshold: `{"kind": "kpi", "kpi_id": "...", "direction": "above", "threshold": 4, "check_by":
     "2027-03-31"}` ("wrong if monthly churn passes 4%"). The threshold comes from the owner, the KPI's target or its
     readings, cited; never from you;
  2. a market entity or page the bet rests on: `{"kind": "thesis", "market_id": "...", "check_by": "..."}`, so the
     bet trips when that entity changes;
  3. a sentence with a check-by date: `{"kind": "text", "text": "...", "check_by": "..."}`. Say in the claim that
     it is **not yet measurable**, and name the KPI that would make it so.

Propose a bet as `active` when it has a rules-out and a wrong-if (OKing the proposal is the owner keeping it),
and as `proposed` when it still lacks one.

## 4. Fill the sections

Markdown, in the owner's words where you have them, in this order:

1. **Who we serve** (`serve`): the customer, how badly they need it, and what they use today instead.
2. **What we sell, and for how much** (`sell`): the product and the pricing model, and the price that would break
   if doubled or halved.
3. **How we know they want it** (`proof`): who uses it, how often, who comes back, and what they say. Evidence only,
   each line with its source: fill it with `playbooks/keep-proof-current.md`. A new idea with no users yet says so.
4. **Why now** (`now`): what changed recently (technology, cost, rules, how people behave) that makes this possible
   or needed now.
5. **What we know that others don't** (`know`): the heart of the strategy. The thing the team understands about the
   business that the obvious competitors miss, and why this team is the one to build it; this is where "how we win"
   lives. Spend the most care here.
6. **How we sell** (`how`): how customers find the team and buy, and where the next 100 customers come from: a channel
   or a list of people, not "marketing".
7. **What we won't do** (`not`): required. Fill it from the bets' `rules_out` and from what the owner strikes or says
   no to; never leave it empty or soft.

Anything else the owner raises (brand, channels) is an extra section: `"extra": {"brand": {"title": "Brand",
"body": "..."}}`. A section you cannot fill yet says, in one line, what you would need to know.

Then the summary, at most 400 characters, drafted from the bets: who the team serves, what it sells, how it wins.
The vision is one or two sentences, at most 400 characters, in the owner's words where you have them.

An example, for a fictional cleaning team (the threshold is the owner's number, not yours):

```
Bet 1: Charge half what other cleaning companies charge, because bots do the office work.
  why        Booking, scheduling and billing are most of a cleaning company's overhead; bots do them for almost nothing.
  rules out  A premium tier; one-off deep cleans.
  wrong if   Monthly churn among property managers passes 4% (KPI), checked by 2027-03-31.
Who we serve       Property managers with ten or more units, who use a local cleaner today.
How we know        12 managers book weekly; 10 of them are still booking after 90 days. (Growth, October 2026)
Why now            Bots can now book, schedule and bill for almost nothing.
What we know       The office work, not the cleaning, is what makes cleaning expensive.
What we won't do   No premium tier, no one-off jobs, no phone support, no new cities yet.
Summary            Acme cleans for property managers at half the usual price, because bots run the office.
```

## 5. Put the claims to the owner

At most eight claims over the whole run, in rounds of up to four, numbered so they can answer "1 keep, 2 strike,
3 edit: ...".

- Every run is a task (the owner's Assistant hands you one too): `tico task ask <id> --to <owner> "<the round>" --version N`.
  That is one ask; you are woken with their answers. Each round counts toward the owner's 5 a day (`asked_today` in `tico goal strategy-bot`).

A kept claim goes in as it stands; an edit goes in in their words; a struck one comes out, and what it ruled out
often belongs under "What we won't do". Do not argue: a position you think wrong goes in as theirs, with the
evidence that worried you beside it, in one line. Stop when every bet has a rules-out and a wrong-if and every
section has a position, or at eight claims.

## 6. File one proposal

    tico proposal create --kind strategy_draft --payload-file draft.json --reason "<one sentence: what this draft rests on>"

The payload is partial: send only the fields you have something for.

    {"summary": "...", "vision": "...",
     "sections": {"serve": "...", "sell": "...", "proof": "...", "now": "...", "know": "...", "how": "...", "not": "..."},
     "bets": [{"statement": "...", "why": "...", "rules_out": "...",
               "wrong_if": {"kind": "kpi", "kpi_id": "...", "direction": "above", "threshold": 4, "check_by": "2027-03-31"},
               "cites": ["..."], "status": "active"}]}

A bet already on the page carries its `id`. Look at `tico proposal list` first: one pending `strategy_draft` from a
drafting run at a time; replace yours rather than file a second beside it. Confirming writes the draft and the bets
as the owner; nothing is published.

Then end with one message to the owner, as the task note: the summary, the bets in one line each with
their wrong-if, what is still unknown, and the ask: "Confirm the draft in Needs you, then publish it from the
Strategy page when it reads right; I never publish." Say what publishing needs: at least one active bet with a
rules-out and a wrong-if, and something under "What we won't do"; the page refuses it otherwise.

Note on the task what the draft rests on, which claims were kept, edited or struck, and what is still open; record
a durable lesson about this team's words or sources in `knowledge/`, and commit this repository. From a task,
finish it with `tico task done <id> -m "<the message above>" --version N`.

**Done when:** one `strategy_draft` proposal is pending (or it replaced an older one), every bet has a rules-out and a wrong-if or is left `proposed` with the gap named, the owner's message with its ask is the task note, and the task says what the draft rests on.

## After the owner publishes

Every bot gets the new summary and active bets in its next run's context under "Team strategy"; you do not tell them
yourself. When a bet was retired or rewritten, the server puts one realignment ask in the owner's Needs you
listing the goals tied to it; that ask is theirs.

Your next job is the goals: for each active bet with no KPI-backed goal (`goals_by_bet` and `health_by_bet` in
`tico strategy show`), suggest one or two goals that serve it, each with `bet_id` set and a KPI that could be the
bet's wrong-if, as proposals the owner accepts (`playbooks/goals-make-sense.md`). Never create a team goal
yourself.
