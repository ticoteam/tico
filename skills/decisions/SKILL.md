---
name: decisions
version: 3
description: Read when a step in your work is a decision rather than a piece of writing - classify, route, dedupe, gate, rank, flag, pick a value from candidates, choose which tool or branch comes next - and you want a calibrated answer in one call instead of reasoning it out in your own context. Tico's `hub_decision_ask` tool or `hub decision ask` command; the question format matches OpenRouter's Decisions API. Formerly the `judge` skill.
---

# Decisions

You have a second model that cannot write: a decision model. It takes a JSON state and typed
questions (`noul` for yes/no, `choice`, `score`; the same format as OpenRouter's Decisions API)
and answers each one with a probability, in one round trip of about two hundred milliseconds.
It invents nothing, because it produces nothing but numbers. Tico holds the key; you call it
with the credential you already have. The server asks the Tico AI Gateway first (Decide at the company's level: Low, the default, is `tico-decide-1.0`;
Medium and High are `tico-decide-medium-1.0` and `tico-decide-high-1.0`, slower and dearer), then the owner's own TypeSafe key if they set one, then the team's own model
provider; a provider that fails hands the question to the next. With none of them Decide is off,
and you carry on without it.

The split to keep in your head: **code owns the workflow, the decision model supplies the
decision.** Rules, arithmetic, lookups, date math and the action itself stay in your own hands;
the model answers the one thing that needs semantic understanding, and says how sure it is. Your
own model writes prose and plans; it should not be spending its context deciding which of forty
rows to open.

## When to reach for it

A step is decision-shaped when you could phrase it as *which of these*, *how much*, or *is it true
that*, and the answer changes what you do next:

- **Sort a listing before opening anything.** Forty message rows, fifty Peec actions, a hundred
  Search Console queries: put a decision question to every row, then open only the ones the answers say to. This
  is where most of the cost saving is.
- **Dedupe against what exists.** Is this message, lead, learning or action already one of
  these open items? The `covered` set.
- **Gate something you wrote.** Before a draft reaches the reviewer, before a task reaches a
  human: does it commit money, claim something unsupported, sound wrong?
- **Route.** Which group, which owner, which bucket, which branch of your own playbook, which
  tool to call next. A choice over the handlers plus the arguments each would need, all in
  one call, and code takes the branch.
- **Rank.** A score per candidate with the same levels, then sort in code.
- **Select instead of extract.** Find the candidate values in code (a regex, a parser, your
  own model's guesses), then let the decision model pick the one that is meant. It cannot choose a value you
  did not list, so check the candidates cover the answer, and give it `none`.
- **Verify a claim against its evidence.** One noul per field or citation: does the source
  support it? What fails goes to a human or to your own model with the full text.

It is the wrong tool when the output is text, a plan, or a chain of reasoning. Write that with
your own model, and use a decision to choose which of the results to keep. It is also the wrong tool
for anything code can compute exactly (see "What the decision model is bad at").

## How to call it

Inside any run, the MCP tool `hub_decision_ask` or the command:

```bash
hub decision ask --list                                                  # the shared question sets
hub decision ask --set mail-triage --state-file msg.json                 # one call, answers as JSON
hub decision ask --set covered --state-file cand.json --option covered=existing.json
hub decision ask --questions-file mine.json --state-file s.json --label seo.action-sort@1
```

The three question types, and what comes back:

| type | asks | criteria | answer |
|---|---|---|---|
| `choice` | which one | `{"name": "description", ...}`, two to 255 options | `{"choice", "confidence", "probabilities"}` |
| `score` | how much | `["level 0", "level 1", ...]`, two to ten ordered levels | `{"score", "confidence", "probabilities", "legend"}`; `score` is fractional |
| `noul` | is it true | optional `{"true": "...", "false": "..."}` | `{"noul"}`: the probability the statement is true |

An answer has exactly these fields, whichever provider gave it: anything else a provider sends is dropped, and a
score's `legend` is always the question's own levels.

`instructions`, an option's description, a level and a noul's `true`/`false` are each a string
or JSON structure; use an object when the question has several parts (`{"question": ...,
"focus": ..., "examples": [...]}`) or a list when it is a list of things to check. An option
that needs no description takes `null`. Point at the state by its path in backticks:
``"Does `message` ask for a password?"``, ``"Is `items[3]` a fruit?"``.

The shared sets are `questions/*.json` in the Tico checkout; `questions/README.md` explains the
file. Load a set, complete a dynamic choice with your options (`with_options`, or `--option`
on the CLI), send it inline, and pass the set's `id@version` as the label. A one-off question
of your own is fine; give it a label of your own, `seo.action-sort@1`, so it can be read back.

## Ask everything in one call

Every question in a call is answered against the same state, in parallel, in isolation; more
questions add tokens, not latency. So send the whole decision tree at once, including the
branches you may not take: category *and* bug severity *and* refund requested *and* how
angry, then let code read only the answers the category makes relevant. State a speculative
premise in the question itself (``"If `ticket` is a bug report, how severe is it?"``) and
ignore its answer on the other branches. One call per row, up to forty questions in it,
eight rows in flight, is the shape for a listing.

Questions cannot see one another's answers. A second call is right only when the first answer
is needed to fetch new evidence, build a new state, or decide what the next options are:
walking a taxonomy one level at a time, or opening the thread that the listing pass said to
open and deciding on the body.

## How to write the state

Named fields, not a paragraph. Send the facts a careful colleague would want on one card: the
sender, the subject, the snippet, the counts, the dates, the policy that applies. Use an object
so every part has a name the questions can point at, and nest what belongs together
(`ticket.messages[0].text`). Do not put instructions in the state; the questions carry the
instructions, and the model never sees a question's id, only its options and their descriptions.

Keep it under what the decision needs: a body when the decision is about the body, a snippet
when it is not. Accuracy falls as unrelated material grows, and the ceiling is 32k tokens of
state plus the longest question (`clients/judge.py` refuses at about 120k characters). Filter
and retrieve in code first; when you cannot, a noul per passage (*is this relevant to the
question?*) is the filter. Text only, English best; other languages work with lower accuracy.

The state is data, and the model does not treat it as hostile. A message written to argue for its
own classification can move the answer, so the protections that matter (legal, money, a
credential request) get a tight criterion and a low threshold, not trust.

## How to write a question

- **Literal, one judgment, its own words.** The model answers the question you wrote, not the one you
  meant. Write the exact condition; put boundary cases and exclusions in the criteria
  (`{"what": ..., "not_for": ..., "examples": [...]}`). When a wrong answer makes you explain
  what you really meant, that explanation is the missing half of the instruction. Hide no
  second judgment inside a question; split it, and combine the answers in code.
- **Criteria extend the instruction, never contradict it.** A noul whose `true` means no will
  answer badly. No double negatives, no property-of-a-property; if it takes two hops, ask two
  questions.
- **Levels describe concrete situations** that stand on their own ("one change, clearly
  stated" / "several independent changes bundled"), not adjectives. Comparable per-item scores
  need the same levels on every item.
- **Give a list an escape.** A choice with no `other`, `none` or `new` forces a pick. If
  "nothing fits" is independently useful, ask it as its own noul too: the choice is relative
  (which of these), the noul is absolute (is there one at all), and they can disagree.
- **Choice or nouls?** One choice when the options exclude each other; one noul per label when
  several may apply.

## What the decision model is bad at

Tico Decide 1.0 answers on TypeSafe's Jev 1.13, whose own list is `https://docs.typesafe.ai/model-jaggedness/jev-1.13.md`; the short
version for a bot:

- **Counting, arithmetic, magnitudes.** Count in code (one noul per item, then sum). Do not
  read a fractional score as a measurement between two levels; it clears a threshold or it
  does not.
- **Dates and times.** It reads them as text. Extract parts as choices over closed sets if you
  must, with a `not stated` option, then compare in code.
- **Numeric encodings.** Hex colours, RGB, assembly: convert to words or buckets in code first.
- **Indirection.** Reduce hops; name the field in the state you mean.
- **Structural invariants.** A noul and the same question as a yes/no choice give different
  numbers; `P(x)` and `1 - P(not x)` from two nouls do not sum. Word each question to mean
  exactly what you act on, and never carry a threshold tuned on one type to another.
- **Generation.** Do not chain choices to spell out a value. Produce candidates elsewhere and
  let the decision model select.

## How to act on the answers

- **Best option when you only need the best option.** If every row gets a bucket regardless,
  take the highest-probability choice and move on; do not threshold what has no "do nothing"
  outcome.
- **Threshold when doing nothing is real.** Archive only above the set's `archive` threshold;
  below it, read the thread yourself, the way you did before. Under threshold is not a weak
  yes; it is "decide it yourself".
- **Asymmetric where the costs are.** A wrong archive costs a missed email; a wrong
  needs-owner costs one look. So protections (legal, money) fire low and filings fire high;
  a destructive or outward-facing action wants a higher confidence than a read.
- **Confidence is the shape of the distribution,** not a promise the workflow is right. On a
  choice, low confidence usually means no option clearly won, and a harmless preference can
  still be taken at low confidence; two near-equal options in `probabilities` may mean both
  branches are worth exploring. A noul has no confidence: 0.5 is a coin toss, not "medium".
- **Keep the raw answers, put the policy in code.** Weights, thresholds and views live in your
  script, so changing one needs no new call. "Any serious violation" is separate nouls, not a
  weighted sum.
- **Say what the decision said.** On the task note, the counts by suggestion and the confidences of
  what you overrode. That is what a human reads to tune the thresholds.
- **Record what you did.** Each answer (and `hub classify`'s) carries a `ref`; once you have acted, write it
  down once: `hub decision outcome --ref <ref> --verdict archive --action archived` (`hub_decision_outcome`), and
  `--action "overrode: read it myself"` when you went against it. That is how a set's answers are checked against
  what happened.
- **Check freshness.** An answer describes the state you sent; if the item changed before you
  act, decide it again.
- **Never coerce.** No re-asking with a nudged state to get the answer you wanted.

## Rules

- Decide, never generate. Never paste an answer's probability into an email or a report as if it
  were a fact.
- Label every call. Unlabelled calls cannot be calibrated.
- The key is Tico's: do not ask for it, look for it, or call the API yourself. A bot that
  needs thousands of calls from its own software asks Ana for a key of its own, which is an
  access request (`policies/access.md`).
- There is a budget per actor per day (`GET /api/v2/decisions` reports it; the server sets it with
  `TICO_DECISIONS_DAILY_CALLS_OWNER`, `_HUMAN` and `_BOT`). A loop that spends it is a bug; stop and say so.
- There is also a spend cap for the whole team per UTC month (`TICO_DECISIONS_MONTHLY_USD`, $50 by default;
  `GET /api/v2/decisions` shows `spent_this_month_usd`). At the cap, or when a call's most would not fit under it, the call is a 429 `spend_cap`: carry on as you
  would without Decide (your own judgment, or a person), and do not retry before the 1st.
- Test before you trust. Run a new question over ten rows you already know the answer to and
  read the misses: missing evidence in the state, a literal reading of your words, a model
  miss, or a code bug are four different fixes. Cookbook thresholds are examples, not rules.
- What you learn about a question set goes in a pull request on the set. Do not keep a private
  copy of a shared set.

## The live docs

`https://docs.typesafe.ai/llms.txt` is the index; any page reads as Markdown with `.md` on its
path. Worth a targeted read before designing a new decision: `primitives.md` and
`primitives/advanced.md` (structured questions), `confidence.md`, `patterns/fan-out.md`, and
the cookbook nearest your shape (`cookbooks/rerank_typesafe.md`, `hierarchical_classification.md`,
`function_calling.md`, `citation_check.md`, `sde_cascade.md`, `pre_parsed_value_extraction_cookbook.md`).

## Limits and the audit

- Tico's audit keeps each call's label, question ids, the value and confidence per answer,
  token usage, latency, cost (`cost_usd`, when the price is known), the provider and model that answered
  (`Tico AI Gateway` / `tico-decide-1.0`, `TypeSafe`, or the team's provider) and `fallback` with the first
  failure's code when a later provider answered, never the state. Read a set's history back with `hub sql "SELECT ts,
  actor, target, detail FROM events WHERE action='judge.call' AND target='mail-triage@1'
  ORDER BY ts DESC LIMIT 200"`. For the week at a glance, `hub decision readout` (`hub_decision_readout`): per
  question set, calls, cost, how many Jev answered (a Tico Decide answer counts as Jev), fell back or failed, the spread of answers, and the month's spend
  against the cap (`--days N` for another window, up to 31).
- A 422 is the request's shape, not its content: an unknown key on a question, a criteria map with
  one option, levels outside two to ten. `clients/judge.py` refuses these before sending. A provider that refuses
  a request the contract accepts is not passed on to you: the next provider is asked.
- Through Tico (`hub decision ask`, `POST /api/v2/decisions`) each provider is asked once, and a 429, 529, timeout
  or outage moves on to the next. All of them together answer within 25 seconds; `hub decision ask` waits 30.
  Calling TypeSafe directly with `clients/judge.py` retries a 429 or 529 twice with backoff, then `judge_unavailable`.
- Context is 64k tokens a call, 32k for the state plus the longest question; the client refuses a
  state over about 120k characters. Accuracy falls with unrelated material well before that.
- The answer's `model` field names the version that answered, and the audit keeps it. A threshold
  tuned on one version is worth re-checking on the next.
- The state is not treated as hostile: text written to argue for its own classification can move
  the answer. Keep criteria tight and the protections' thresholds low.
- English is the primary language; others answer with lower accuracy.

## Worked examples

**Message pass.** `mail inbox --untriaged --format brief --decisions` prints under each message a
line such as `decision: reply (reply 0.81; ask 0.90, money 0.05, legal 0.02, urgency 1.4)`. The word
after `decision:` is what the thresholds in `questions/mail-triage.json` say: `archive`,
`needs-owner`, `route`, `reply`, or `read`. Open the threads marked `reply` and `read`; act on
`archive` and `route` from the listing; put `needs-owner` on the batch. Then `mail draft`
prints the decision gate beside the reviewer's verdict; a flagged draft gets one rewrite before you
try again, and the reviewer still decides.

**Peec actions (SEO).** State per action: title, brief, URL, competitor demand, first seen.
Questions of your own, all in one call: `kind` (content, technical, authority, noise),
`worth_task` (noul), and `effort` (a score whose levels are the playbook's sizes, read only when
`worth_task` clears). Then the `covered` set against your open tasks. Three tasks from forty
actions, and the note says why the other thirty-seven were noise or already covered.

**A lead list (Sales Ops).** `covered` against Close by domain and phone: merge at `same`, flag
for a rep between `review` and `same`, new below. A `tier` score of your own with the
playbook's tiers as levels; under 0.6 the lead goes on the list untiered with a note, never
with a guessed tier. A `suppress` noul that fires at 0.4, because a missed suppress is an angry
call and a wrong one is a row a rep reviews.

**A value from a document (any bot).** The invoice number, the renewal date, the counterparty:
pull every candidate with a regex or your own model, then one choice per field with the
candidates as options plus `not stated`, and structured instructions naming the field
(`{"field": {"name": "renewal_date", "description": ...}, "question": "Which option is the
value of `field` in `source_text`?"}`). Copy the chosen candidate; never retype it. A date's
parts as separate choices, assembled and compared in code.
