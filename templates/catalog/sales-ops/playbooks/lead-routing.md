# Lead routing and territories

Triggered by a task about an unassigned lead, a territory question or a proposed rule change, and by
the weekly report's routing check. Budget 20 minutes. The outcome is each unassigned lead with the owner
the rules give, and any rule change as a proposal. Reassign requested work with your Tools.

---

## 1. Read the rules and the leads

    tico task show <id>

Read `knowledge/routing-rules.md` (territories, segments, round robin order, who covers whom, who is
out) and, from the CRM, the leads in question with source, organization size, region and created time.

## 2. Apply the rules in order

Existing account first (a lead from a customer goes to its account owner, not to round robin), then
named-account lists, then territory or segment, then round robin among the humans the rules list. Note
which rule decided each lead. A lead that matches no rule, or two, is a rule gap.

## 3. Propose

For each lead: the lead id, the owner the rule gives, the rule. Put the list on the task. Where the requested work and your Tools allow CRM writing, assign exactly those leads; otherwise
`tico task create --owner <human>` with the list for the human who assigns.

## 4. Fix the rules, not the leads

For a rule gap, propose the smallest rule change and show which of last month's leads it would have
moved. A change to a territory or the round robin order is the Sales Manager's decision. Record decided
changes, with date and the source request, in `knowledge/routing-rules.md`.

## 5. Finish

Commit and `tico task done <id> -m "..." --version N`: leads routed or proposed, rule gaps found, what
waits on a human.
