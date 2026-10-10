# Check a deal registration

Triggered by a new registration from a partner (a form, an email, a task), and for each waiting one in the
weekly review. Budget 10 minutes. The outcome is a proposed decision with the rule that decides it.
Apply requested registration decisions with your Tools; partner messages stay drafts until a person turns mail sending on in Tico.

---

## 1. Read the registration

    hub task show <id>

Note the partner, the prospect organization, the contact's role, the opportunity (what, how big, when), the
date and time received, and what the partner has done so far.

## 2. Check completeness

Against `knowledge/rules-of-engagement.md`: every required field present, the prospect in scope (size,
region, new customer or expansion as the rules allow), the partner eligible. An incomplete registration
gets a request for the missing fields, ready to use; its clock starts when complete.

## 3. Check conflicts

Search the pipeline (CRM read), `knowledge/registrations.md` and `hub meeting search "<prospect>"`: an
open deal with our own seller, another partner's registration, an existing customer. Note each with its
date.

## 4. Propose

One line: approve, decline or ask for more, with the clause that decides it, the protection end date on
approve, and who works the deal. Draft the reply to the partner in two to four sentences. Ask on the task.

## 5. Carry out the requested work

Send the requested reply with your Tools when a person has turned mail sending on in Tico, otherwise keep the draft. Record the decision, owner and date in `knowledge/registrations.md`, and for an accepted registration create `hub task create --owner sales` with the partner noted
as source. `hub task update <id> --status done --note`.
