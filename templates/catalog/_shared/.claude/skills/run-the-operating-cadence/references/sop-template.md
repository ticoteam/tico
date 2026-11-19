# Writing an SOP

An SOP (standard operating procedure) is the written steps for work that repeats, good enough that a teammate who has
never done it gets the right result the first time. In a bot's repo an SOP is a file in `playbooks/`.

## When to write one

- The same work has been done three times in roughly the same way.
- A miss happened and the cause was "nobody knew the steps" or "it was in one teammate's head".
- A role is being handed to a new teammate.
- A step is risky enough that it must be done the same way every time.

Do not write one for work done once, for work a routine can do with no judgment (write the routine), or for work that
is still changing every time.

## How to write it

1. **Start from the record.** Read the last three times the work was done: the tasks, the runs, the messages. Write
   what was done, not what should ideally be done. Where the three differ, pick the one that gave the best result and
   note why.
2. **Fill the template** below.
3. **Test it cold.** Give it to a teammate who has never done the work, with no other help, on a real case. Watch where
   they stop, guess or ask. Each of those is a gap in the SOP. Fix and repeat until they finish without help.
4. **Publish** where the settings say, with an owner and a review date.
5. **Point to it.** Add one line to the instructions or the task template of whoever does the work, saying when to
   open it.

## Template

```markdown
# <What the work is, as a verb phrase>
Owner: <teammate>   Last tested: <date> by <teammate>   Review by: <date, at most 6 months on>

## When to use
<The trigger: the event, the task wording or the schedule.>

## Result
<What exists when this is done, and how to tell it is right.>

## Before you start
- You need: <access, Tools, files, inputs>
- Do not start if: <the conditions that mean stop and ask>

## Steps
1. <One action. Start with a verb. Name the Tool, the record and the exact field or command.>
   Expect: <what you should see if it worked>
2. ...

## If something goes wrong
| What you see | What to do |
|--------------|------------|
| <error or odd result> | <the action, or who to ask> |

## Undo
<How to put things back, or "cannot be undone: needs an OK before step N".>

## Limits
<What this SOP may do without asking, and the point where it needs an OK.>

## Changes
<date>: <what changed and why>
```

## Rules for good steps

- **One action per step.** If a step has "and", split it.
- **Name the exact thing.** The command, the field, the file path. "Update the record" is not a step.
- **Say what to expect.** After each step that can fail, what the teammate should see.
- **Put the decision in the step.** "If the amount is over the limit in `knowledge/...`, stop and ask" beats "use
  judgment".
- **Real values, no invented ones.** Thresholds come from a knowledge file by name, so one change updates every SOP.
- **Mark the one-way step.** If a step cannot be undone, say so in the step and name the OK it needs.
- **Short.** One page for most work. Long reference material goes in a linked file.
- **No secrets.** Credentials stay in Tico's Tools and Credentials, never in an SOP.

## Keeping SOPs alive

- Each SOP has one owner. An SOP with no owner is deleted or adopted in the quarterly tune-up.
- Review on the date, or sooner when the Tool it uses changes or it causes a miss.
- When a run finds a step that is wrong, fix the SOP in the same task and add a line under Changes.
- Keep an index: one line per SOP with its trigger, owner and review date, in `playbooks/README.md` or the team doc
  "How we work".
