# Build a role learning plan

Schedule: triggered by a task naming a role and level, or a person's manager asking for a plan. Budget 40 minutes. Inputs: the
level guide for the role, the manager's request, and the roster for the role. Outcome: a one-page plan the manager can hand over,
mostly built from work and colleagues, with at most two courses. Requested spending follows the stated budget and your Tools.

Done when each skill in the plan has a practice block (`skills/design-a-practice-block/SKILL.md`), each course has been run
through `skills/decide-whether-a-course-earns-its-place/SKILL.md`, every skill has an observable sign, the plan is attached to the
task, and any role plan is written to `knowledge/plans/<role>.md`.

---

## 1. Read what the level asks for

    hub task show <id>
    hub doc ask "What does the level guide say for <role> at <level>?"

List the four to six skills that separate this level from the one below. If there is no level guide, ask the manager for the three
things a person at this level does that one below does not.

## 2. Work that builds each skill

For each skill, one piece of real work in the next quarter that stretches it (own a small project, run a customer review, lead an
incident review), named by type, not assigned to anyone. Write it up with the practice block skill.

## 3. People to learn from

For each skill, the role (not a name unless the manager gives one) who does it well, and what to do with them: shadow a call,
review each other's work, a monthly conversation.

## 4. Courses, only where they earn it

At most two. Each one goes through `skills/decide-whether-a-course-earns-its-place/SKILL.md`: the skill it builds, what work alone
will not teach, its practice and feedback, provider, length, cost, and a finish-by date. Read the course page (`hub doc fetch <url>`)
and note what it actually covers.

## 5. How to know it worked

One observable sign per skill that the manager can check at the next review, described in terms of the work.

## 6. Hand over

Write `knowledge/plans/<role>.md` if it is a role plan (no personal detail) and attach it to the task. Record any course cost. A
purchase happens only when the task asks for it and a granted Tool allows it; otherwise it stays a request for a person.
