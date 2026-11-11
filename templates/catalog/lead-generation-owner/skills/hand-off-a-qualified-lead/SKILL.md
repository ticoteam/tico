---
name: hand-off-a-qualified-lead
description: Hand a qualified lead to Inbound or Outbound Sales as a task that carries its source, reasons, dated signal and pick-up time. Never send anything to the lead, and never change the CRM record.
---
# Hand off a qualified lead

When to use: each time a lead is counted as qualified and has no hand-off task yet.

Steps:
1. Confirm the lead is qualified with `skills/score-a-lead-against-the-profile/SKILL.md`. A lead that is a customer, a
   competitor, on the do-not-contact list, or without a source is not handed off. Ask the person who runs sales.
2. Choose the owner. An inbound lead (demo request, contact form, trial sign-up, referral, marketplace listing, event follow-up)
   goes to `inbound-sales-owner`. A reply to an outbound sequence goes to `outbound-sales-owner`. If the team has no such
   owner, ask `growth-manager` in a task.
3. Set the pick-up time from `knowledge/lead-method.md`: inbound the same business day, outbound within two business days.
4. Create the task:

       tico task create --owner <inbound-sales-owner or outbound-sales-owner> --title "<lead or company>: qualified, <source>" --body "<body>"

   The body carries: the source tag; the reason it qualified; the dated signal with its date; the profile match with its source;
   the pick-up time; and the CRM id or a link.
5. Do not message the lead. Do not change its stage, owner or source in the CRM. Those belong to the sales owner.
6. Record the task's id against the lead in the week's count in the report.

Checks before you finish:
- The task carries all five items in step 4.
- Nothing was sent to the lead, and the CRM record is unchanged.
- The task is addressed to the right owner for the lead's type.
