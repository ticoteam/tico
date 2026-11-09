# Build a salary band

Schedule: triggered by a task asking for a band for a role family, or by a role with no band. Budget 45 minutes. Inputs: the role
family and levels named on the task, and the source for each figure. Outcome: a proposed band per level with its source, for an
owner to accept. Nothing is published.

Done when each level in the role family has a proposed minimum, midpoint and maximum with a source and date, each band is
marked provisional or not, the overlap with the levels around it is stated, and the proposal is attached to the task.

---

## 1. Define the role

From the level guide (`hub doc ask "What does <level> mean for <role family>?"`) and the role brief: scope, level, location
rule. A band is for a role family and level, not for a person.

## 2. Gather market data

In this order of trust: the salary survey the team buys (attached to the task), the payroll provider's benchmark, public ranges
published for comparable roles in the same market (`hub doc fetch <url>`, at least three, with dates). Record each data point's
source, date, location and level match. Public ranges show what employers advertise; weigh them below paid data.

## 3. Set the midpoint

Apply `knowledge/philosophy.md`: the market median for "pay at market", a higher point for roles the team chose to lead on.
Adjust for location only if the philosophy says so, and say by how much.

## 4. Set the spread

Minimum and maximum around the midpoint. The team convention is about 30 percent spread for entry levels and 40 to 50 percent for
senior levels. It is a planning convention, not a finding, so the philosophy can change it. State the overlap with the level
above and below.

## 5. Check against current pay

Using the payroll export attached for this purpose: how many people in the role family fall below the minimum or above the
maximum. The page carries counts; any references go in an attachment for the owners. Run a pay equity check with
`skills/run-a-pay-equity-check/SKILL.md` only if the owner asks for one.

## 6. Hand over

Attach the proposal with its sources, and record requested bands in `knowledge/bands.md` with the date and owner. A band from
fewer than three data points is marked provisional. Publishing a band needs the task to ask for it and a granted Tool to allow
it; otherwise it stays on the task for the owner.
