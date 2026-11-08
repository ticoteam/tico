# Keep "How we know they want it" current

The `proof` section says how the team knows people want what it makes: who uses it, how often, whether they come
back, and what they say. It is evidence, not belief. You fill it from what Tico already records about customers and
propose it to the owner. You never write the page.

Run this when:
- you draft the strategy (`playbooks/draft-the-strategy.md`, step 4);
- the Monday note finds proof empty or older than 30 days (`playbooks/weekly-goals-review.md`);
- the owner asks for it through their Assistant.

At Passive, run it only when the owner asks.

**Budget.** About 15 minutes. Read the sources below once; do not search the same source twice.

## 1. Check what is there

- `hub strategy show`: `current.sections.proof` is the published text. `proof_since` is when it last changed (null when
  it is empty or nothing is published).
- `hub proposal list`: a pending `strategy_draft` that already changes `proof` is waiting for the owner. Do not file
  another. Say in your note that it is waiting.

## 2. Gather the evidence

Read each source. Keep only facts that say whether customers want the product.

1. **Growth numbers.** `hub growth numbers`: new customers by month, and the share kept at 30, 90 and 180 days. Use the
   newest full month. Blank is not zero: a blank share is "not known yet", never 0%. Then `hub kpi list` for usage
   KPIs (active users, repeat use), with each reading's period.
2. **Support work.** `hub team show` names the bots that talk to customers (Support, Customer success, Sales). For
   each, `hub task list --owner <slug> --updated-since <30 days ago, as 2026-10-03T00:00:00Z> --brief`, then
   `hub task show <id>` for the few that quote a customer: what they asked for, what they praised, why they left.
3. **Meetings.** `hub meeting search "<the product's name>" --since <30 days ago, as 2026-10-03>`, then
   `hub meeting read <id>` for customer calls. Team meetings only; you never see private ones.
4. **Listening.** `hub market briefing latest` and `hub market delta`: what people said in public about the product
   or the problem, with links.
5. **Learnings.** Ask the customer-facing bots once: `hub question ask <slug> "What did customers say about <product>
   in the last 30 days? Quote them, with the task or message each came from."` Use only answers that name a source.

## 3. Write it

Three to six short lines, newest evidence first. Each line is one fact and its source in brackets:

    - 38 new customers in October; 61% of July's customers still customers at 90 days. (Growth, October 2026)
    - Weekly bookings per home: 1.0, flat since August. (KPI weekly-bookings, readings to 2026-10-31)
    - A property manager: "We dropped our spreadsheet after the first week." (Support task 412, 2026-10-14)
    - Two of five lost customers said the price was fine but scheduling was slow. (Meeting "October churn calls")

Rules:
- **Never invent a number.** A count, a share or a rate appears only when a source above states it, with its period.
- **Quote, don't paraphrase,** when you write what a customer said. Name them by role and kind of company, never by
  name or email.
- **Say what is missing.** If a source has nothing, write one line: "No retention numbers yet: Growth has no cohort
  older than 30 days." That line is evidence too.
- **Bad news stays in.** Churn, complaints and a flat number belong here as much as praise.

## 4. Propose it

If the text says what the published one says, stop: there is nothing to propose. Otherwise file one proposal:

    hub proposal create --kind strategy_draft --payload-file proof.json --reason "<one sentence: what changed in the evidence>"

with `{"sections": {"proof": "..."}}`. It is one item in the owner's Needs you and counts toward their 5 a day
(`asked_today` in `hub goal strategy-bot`); at 5, keep it for tomorrow. When a fact bears on a bet (retention under a
bet's wrong-if, say), name the bet in the reason; `playbooks/bet-tripped.md` handles a bet that trips.

**Done when:** one proposal changing `proof` is pending, or you found nothing new and said so. Every number in it has a
source and a period.
