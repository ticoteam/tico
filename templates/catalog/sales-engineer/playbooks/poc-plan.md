# Proof-of-concept plan

Triggered by a task asking for a proof of concept, pilot or technical trial for a named deal. Budget 30
minutes. The outcome is a one-page plan the buyer can agree to before anything is set up. No access is
given and nothing is sent outside the stated rules.

---

## 1. Read the deal

    hub task show <id>
    hub meeting search "<customer>"

From discovery: the business problem, the current state, the future state the buyer described, who
evaluates and who decides. No clear business problem means the POC is premature: say so to the Account
Executive with the questions to ask first.

## 2. Pick what to prove

Three to five of the buyer's workflows, never a feature list. For each, one measurable success criterion
("Front desk books a class for a returning client in under 30 seconds", "Nightly import of 5,000 bookings
completes without errors").

## 3. Write the plan

Scope in and out; the criteria; test steps per criterion; owners on both sides by name; environment and
data (synthetic or the buyer's own, never another customer's); start and end dates, two weeks by default;
what happens on success (the decision step in the mutual action plan). Changes after agreement are logged
as scope changes with a new date.

## 4. Hand over

Save it to `knowledge/poc/<deal>.md` and attach it. Send the requested plan to its recipient with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft. Access to a sandbox is a separate request
for a human. `hub task update <id> --status done --note`: the criteria, the dates, what is ready to act on.
