# Office request

Triggered by a task from anyone: "the meeting room screen is broken", "we need a desk for a new
starter on Monday", "can we get oat milk". Budget 10 minutes. The outcome is the request logged with a
fixer and a date, and the human who raised it told what happens next.

Inputs: the task and its attachments, `knowledge/fixers.md`, `knowledge/supplies.md`, `knowledge/requests.md`.

Done when: the request is a row in `knowledge/requests.md` with a fixer and a promised date (or "not given"), the requester has
a one-line reply, and any order or message is on the task as a draft or as placed under a granted Tool.

---

## 1. Understand it

    hub task show <id>

What, where, since when, how bad. If it is unsafe (water, gas, electrics, a lock, a blocked exit), say
so in the first line and tell the Operations Manager now; do not wait for the weekly page.

## 2. Route it

Skill: `skills/triage-an-office-request/SKILL.md`. Route by type:

- IT (laptops, accounts, Wi-Fi password, software): hand to `it-support` with `hub task create --owner
  it-support` and tell the requester.
- A fix in the building: the fixer from `knowledge/fixers.md`. Building-owned items (heating, lifts,
  doors) go to the landlord or building manager.
- A purchase: under the limit, prepare it for the owner; over it, a purchase request to
  `procurement`.
- A preference (a new snack, a plant): add it to the supplies list proposal, not an order.

## 3. Prepare the action

The message to the fixer or landlord (what, where, since when, access times, a photo if the requester
attached one), or the order. Record it on the task. Place a requested order only when the task asks for it and a granted Tool allows it. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 4. Log and reply

Add the row to `knowledge/requests.md` with the promised date. Reply to the requester in one line:
who fixes it and by when. When it is fixed, close the row and `hub message send --fyi` the requester.
