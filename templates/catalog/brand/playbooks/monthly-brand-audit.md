# Monthly brand consistency audit

Schedule: the 1st of each month at 10:00 team time (routine `monthly-brand-audit`), after setup. Also run by hand. Budget 45 minutes.

Inputs: the brand card (`hub brand show`), `knowledge/rulings.md`, last month's audit, and what went public last month on the surfaces the book names (pages that changed, emails sent, posts, listings). Any sales deck a person attached to a task.

Outcome: one report in the shape of `knowledge/examples/brand-audit.md`: the verdict, three fixes with the line as it should read, the score table, brand book gaps, last month's fixes, and what could not be checked. Nothing is edited.

Done when: every sampled item has four scores, each miss has its quoted line, each of the three fixes has an owner, the report is published, and the task note carries the verdict.

---

## 1. Read where things stand

    hub task show <id>

Then the brand card (`hub brand show`), `knowledge/rulings.md` and last month's audit. Check whether last month's three fixes were made, slipped or dropped.

## 2. Take the sample

Follow `skills/score-a-brand-audit-sample/SKILL.md` to choose twelve to fifteen items published last month, spread across the surfaces in the brand card: the pages that changed, emails sent (from the Email Marketing Manager's reports), posts, app store or marketplace listings, and any sales deck a human attached. Weight toward what most people see, and add a few from the rest so the sample is not only the front door. Write down the count and how you chose.

## 3. Score each item

Score each item pass, minor or miss on four checks:
- **voice**: the traits, judged against their do and don't examples;
- **words**: the word list, and product and plan names spelled as the book has them;
- **claims**: matches the product and the prices as they stand on the date you check;
- **visual**: logo, colours and type per the visual rules, when you can see them.

Quote the line for every miss. A surface you could not see is "not checked", with the reason. It is never a pass.

## 4. Choose three fixes

Follow the ranking in the skill: audience size first, then harm (a wrong price outranks a tone slip). For each fix record the item, the line as it is and as it should read, the rule it breaks, and the owner of the item. Create a fix task for each owner with `hub task create --owner <owner>` only when the task asks for fix tasks and a granted Tool allows it. Otherwise the report carries the three fixes for a person to route.

## 5. Find gaps in the book

A question the audit could not answer from the brand card (how to write a price change, whether to use emoji in posts) becomes a proposed rule with one example, for the owner. Do not change the brand card without the owner's answer.

## 6. Write and hand over

Write `reports/YYYY-MM-brand-audit.md` in the shape of `knowledge/examples/brand-audit.md`, then:

    hub file publish reports/YYYY-MM-brand-audit.md

Commit this repository, then:

    hub task update <id> --status done --note "<verdict>; reports/YYYY-MM-brand-audit.md; not checked: <list or none>"
