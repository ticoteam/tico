# Handle a data request

Triggered by a task holding a request from a person to access, correct, delete or move their data, or to stop a
use of it. Budget 20 minutes to set it up; then it is watched until closed. The outcome is a request run to its
deadline by the humans who own each system. Use your Tools for the requested data actions after verifying identity and scope; outside messages stay drafts until a person turns mail sending on in Tico.

---

## 1. Log it today

    hub task show <id>

Record in `knowledge/requests.md`: an id, the kind, the date it was **received** (the date it reached the team,
not the date the task was made), the deadline under the rules in `state.md` (for UK and EU requests one month from
the day of receipt), and the requester's first name only. Never copy their email, address or id document.

## 2. Check what must happen first

- **Identity**: is the requester who they say they are? If there is real doubt, a draft asking for the minimum
  proof goes on the task to send with Tools when a person has turned mail sending on in Tico. Under UK guidance the clock waits only while genuinely needed
  clarification or identity is outstanding; record the dates.
- **Scope**: which kind, which products, which period.

## 3. Write the steps per system

From `knowledge/processing.md`, one line per system: what to search for (the identifier the requester gave), what
to do (export, correct, delete, restrict), who runs it, and what to keep because a law or contract requires it
(invoices, for example). Put the list on the task and ask once: "Create these steps for the system owners?"

## 4. Carry out the requested work and record the result

Create one task per system: `hub task create --owner <human> --title "Data request <id>: <action> in <system>"
--due <date 5 days before the deadline> --parent <id>`. Check them each desk run. When all are done, a draft reply
(what was done, what was kept and why) goes on the task; send the requested reply with your Tools when a person has turned mail sending on in Tico, otherwise keep the draft.
Close the row with the date sent. An extension is a human's decision; record who made it and why.
