---
name: check-a-lead-before-it-is-contacted
description: Before an event lead goes to sales or into an email, record the basis for contacting each person (a confirmed business contact, a sale or negotiation, or consent given at the stand), remove objectors, and attach the context and owner on the task.
---
# Check a lead before it is contacted

When to use: an event lead list is ready to route to sales, or a lead is to be added to a promotion or follow-up email.

Steps:
1. Sort each address by who holds it, not by its domain. A company domain can still be a person's own address, such as a
   sole trader's or an employee's. Whether that address gets individual-subscriber protection depends on the jurisdiction
   and the subscriber's legal status; check at source. Mark any address you cannot confirm as a business contact as personal.
2. For each personal address, check the rule for the person's jurisdiction, then record the basis for contact: consent
   given at the stand in words the person would recognise, or a sale or negotiation with an opt-out offered at collection.
   A badge scan alone is not a basis for marketing email. For each business contact, the marketing-email rule may not
   apply, but data protection rules still do: record the lawful basis for holding the address and keep the opt-out.
3. Remove anyone on the suppression source, or who objected at the event. The source is the opt-out list in a granted
   Tool, read now, or a dated `knowledge/do-not-email.md`. If neither has actually been checked, stop: route nothing, ask the owner on
   the task to name the source, and say on the task that the suppression check could not run. Keep a do-not-contact entry;
   do not delete the address.
4. Attach the context to the task: what was said, the question asked, the product area and the owner it goes to. One line
   per lead.
5. Write the sales follow-up deadline from `knowledge/rules.md` on the task.
6. Route to sales with `hub task create --owner <seller>` only when the task asks for it and a granted Tool allows it.
   Otherwise keep the list on the task for a person to route.

Checks before you finish:
- Every personal address has a recorded basis or is removed.
- Every business contact has a recorded data-protection basis and an opt-out.
- No do-not-contact person is left on the list.
- Each lead has its context and its owner on the task.

Example (generic): "Expo list of 60 badge scans: 41 addresses confirmed as business contacts, 19 treated as personal. Of the
personal ones, 12 asked at the stand for an update, and the basis is recorded beside each; 7 did not and are removed from
marketing. The business contacts each have a recorded basis and keep their opt-out address. The list goes to the sales owner with a same-day deadline and one line of context per lead."
