# Role scorecard

One page that defines a role by its results. It is written before anyone, bot or person, is chosen, and it is the
standard the teammate is judged against at day 30. Save it as `knowledge/roles/<role-slug>.md`.

## Template

```markdown
# Role: <name>
Written <date> by <bot>. Evidence: <task ids or links that show the work exists>.

## Mission
<One sentence. Why the role exists, in terms of a result for the company.>

## Outcomes
| # | Outcome | Target | By | Read from | Tolerance |
|---|---------|--------|----|-----------|-----------|
| 1 | <result, not activity> | <number + unit> | <date or "every week"> | <KPI, report or Tool> | <how far off is still a pass> |

## Owns
- <A thing this role is answerable for. Each line is a noun someone could ask "who owns this?" about.>

## Never does
- <Actions outside the role, especially sending, spending, signing and deciding about people.>

## Needs
- Inputs: <records, files, people it depends on>
- Tools: <Tools already connected / Tools to connect>
- Access: <the least that does the job>

## Reports to
<Teammate name>. Asks for an OK from: <person>.

## Cost ceiling
<Per week or month, with unit. For a bot: model, reasoning effort and run budget. For a person: pay range.>

## Filled by
<empty until someone holds the role; then name, kind (bot, contractor, employee), start date, and each verdict with date>
```

## Rules for each part

**Mission.** If the sentence would still be true after deleting the role, it is too vague. "Keep customers informed"
fails. "Every customer with a late order hears from us before they ask" passes.

**Outcomes.** Three to five. Fewer than three means the role is a task; more than five means it is two roles.

- Write results, not activity. "Send 40 outreach messages a week" is activity. "8 qualified meetings a month" is a
  result. Keep an activity number only as a second line under the result it drives.
- Each needs a number, a unit, a date or cadence, and a source that exists today. If the source does not exist, the
  first outcome is "the number is being read by <date>".
- The baseline comes from the record, with its date. If there is no baseline, write "baseline: not measured" and set
  the target after two weeks of readings. Do not make one up.
- Put a tolerance on each: the reading that still counts as a pass (for example, "90% target, 85% passes").
- Add one guardrail outcome that stops the role from winning the wrong way: a quality, cost or complaint number that
  must not get worse.

**Owns.** Things, not verbs: "the refunds queue", "the carrier claims list", "the weekly numbers page". A reader should
be able to settle "whose is this?" from this list alone. Check it against the "Owns" of every bot already on the team.
An overlap is fixed here, before the bot exists, by giving the thing to one of them.

**Never does.** The actions that would do damage if the teammate got them wrong, and the work that belongs to another
role. Typical lines: never pays, never signs, never sends outside the team without an OK, never decides about a
person, never changes a price.

**Needs.** If the role needs a Tool the team has not connected, say so here. A role that cannot read its own source
will fail at day 7 for a reason that has nothing to do with the teammate.

**Cost ceiling.** For a bot, name the model, the reasoning effort and a weekly budget, and compare `auto:<bot>:cost_7d`
to it at each review. Reasoning effort is the first thing to lower when cost is the problem and quality is fine.

## How to test a scorecard before using it

Read it as the teammate on day one and answer four questions from the page alone:

1. What would I do first tomorrow?
2. How would I know, on Friday, whether the week went well?
3. What am I not allowed to do?
4. Who do I ask when I am stuck?

If any answer needs something that is not on the page, add it.

## Filled example (invented)

```markdown
# Role: Freight claims
Written 2026-03-02 by Operations Manager. Evidence: tasks 412-447 (11 damaged deliveries, 5 claims filed late).

## Mission
Recover what carriers owe us for damaged and lost freight.

## Outcomes
| # | Outcome | Target | By | Read from | Tolerance |
|---|---------|--------|----|-----------|-----------|
| 1 | Claims filed inside the carrier's own deadline | 100% | every week | claims list, filed date vs deadline | none |
| 2 | Claim value recovered | 70% | quarter end | finance: carrier credits vs claimed | 60% passes |
| 3 | Carrier invoices matched to quotes | 100% | every week | carrier invoices vs quote files | 95% passes |
| 4 | Guardrail: claims refused as unsupported | under 10% | every month | carrier replies | 15% passes |

## Owns
- The carrier claims list
- The carrier invoice match

## Never does
- Accepts a settlement offer
- Pays or disputes a payment
- Contacts a customer

## Needs
- Inputs: damage reports with photos from the warehouse lead; carrier terms files
- Tools: email (Ask first), the shipping Tool (read)

## Reports to
Operations Manager. Asks for an OK from: the owner.

## Cost ceiling
Team default model, medium reasoning effort, weekly budget set by the Operations Manager.
```
