# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| pstack, skill `architect` and the `feature` and `multi-phase-plan` playbooks. https://github.com/backnotprop/pstack | MIT | Trace every system the change touches; sketch two different designs and pick the one with one owner per piece of state, one way to do each task, and the least for callers to know; redesign when workarounds repeat; skip the plan for a one or two file change; settle facts by prototype and ask a person only for product calls; per-PR proof with a concrete live scenario. |
| Matt Pocock skills, `grilling`. https://github.com/mattpocock/skills | MIT | Facts are the bot's job and decisions are the person's; ask every open decision in one numbered round, each with a recommended answer worded so "yes" accepts it. |
| superpowers, `writing-plans`. https://github.com/obra/superpowers | MIT | A plan is the set of decisions the builder cannot make alone, written for a builder who has not seen the codebase; "Review focus" as the failure modes the spec implies and no task tests; the self-check for vague lines, name consistency and a plan longer than the code. |
| gstack, `plan-eng-review`. https://github.com/garrytan/gstack | MIT | Engineering preferences: smallest clear change, plain over clever, reversible by default, reshape the code in a separate change before changing behaviour, end with no undecided question. |
| Tico docs `docs/software-factory.md` and catalog skill `software-architect/skills/review-a-design-for-reversibility` | Tico | Product decides what and engineering decides how; one-way and two-way doors; add, move, then remove for stored data. |

The default PR size (about 400 changed lines, a 15 minute read) is a starting value for a team to tune, not a measured
fact. The 15 minute figure is the review budget in Tico's reviewer playbook.
