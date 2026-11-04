# Launch brief

Triggered by a task naming a launch. Budget 40 minutes. The outcome is one brief in
`reports/YYYY-MM-DD-<launch>/brief.md` ready to use in ten minutes.

---

## 1. Read the request and the record

    hub task show <id>

Read `knowledge/positioning.md`, `knowledge/tiers.md` and `knowledge/launches.md`. Ask the requester
once (`hub task ask <id>`) if the launch, its date or its owner is unclear, and stop.

## 2. Size it

Read `skills/pick-a-launch-tier/SKILL.md`. Pick the tier from expected business impact: reach, revenue, strategic weight. Say why in a line. A
small update is Tier 3: a changelog entry and an in-app note, not a campaign.

## 3. Write the brief

One page, in this order: **Goal** and one success metric; **Tier and date**; **Audience** (who it is for,
who it is not for); **Positioning** for this launch (alternative, what is different, value, category);
**Messaging** (one line, three pillars, one proof point each with its source); **Assets** with owner
and due date (page, email, post, sales one-pager, support answers, in-app note); **Who is told
first** (sales, support, leadership); **Risks and gaps**; **Review date**.

## 4. Check the claims

Run `skills/check-a-competitor-claim/SKILL.md` on every comparison. Every comparison and number is cited with its date, or marked a gap for the product owner. No date,
price or feature is promised to customers: the brief says "to confirm".

## 5. Hand over

Update `knowledge/launches.md`, commit, then `hub task update <id> --status done --note`: the
launch in one line, the tier, the path and the gaps. Assets go to the owning bot or human when
the brief is ready; create requested tasks with your Tools.

Done when: the brief has every section in order, each claim has a dated source or a gap, the tier has its reason, and sales and support know the date.
