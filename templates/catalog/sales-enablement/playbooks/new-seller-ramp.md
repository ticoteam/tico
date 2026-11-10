# New seller ramp

Triggered by a task naming a new seller and a start date, or by the setup answers. Budget 30
minutes. The outcome is a week-by-week plan for their first 90 days, built from this team's own calls and
deals. It is guidance for them and their manager, never an assessment.

---

## 1. Read the role

    tico task show <id>
    tico team show --person <seller>

Note the role (SDR, Account Executive, Account Manager), start date, manager, and segment or territory.

## 2. Pick the examples

From `knowledge/win-loss.md` and recent calls: three strong discovery calls, two demos, one negotiation,
and one lost deal with a clear lesson, each with why it was chosen. Prefer the last six months.

## 3. Write the plan

- **Days 1 to 30**: product, ideal customer, the stages and what enters each, the objection list; listen to
  the example calls; shadow two live calls.
- **Days 31 to 60**: run discovery with a colleague on the call; first talk tracks in their own words.
- **Days 61 to 90**: own deals end to end with review; first renewal or first closed deal for the role.

Each week has two or three concrete tasks and one milestone the manager can see happen.

## 4. Hand over

Save to `knowledge/ramp/<seller>.md` and attach it to the task for the manager. `tico task done <id>
-m "..." --version N`: the start date, the milestones, the example calls. Weekly check-ins on milestones
appear in the win/loss notes; a slip twice goes to the Sales Manager as a question about support, not a
judgment.
