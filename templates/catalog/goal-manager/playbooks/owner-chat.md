# Work the owner asks for through their Assistant

The owner never chats with you directly. They ask their Assistant, and it hands you a task that opens with their
words quoted. You work it as yourself, with your own rights and within your level in `AGENT.md`: the Assistant's
rights for its person are not yours.

Inputs: the task, whose opening quotes the owner's words, and the record it points to.

1. **Read what they asked.** One sentence: do they want advice, or a change made?
2. **Look before you answer.** `hub strategy show`, `hub goal list`, `hub team show`, `hub group list`,
   `hub doc search --market`. For a KPI, `hub kpi show <id>` gives its latest reading and its pace or target. What you cannot
   find, say you could not find.
3. **Advice**: 2 to 4 short lines, plain words, with links where they help (`[title](#/task/<id>)`, `[bot](#/bot/<slug>)`).
   The answer first, one line per item that matters with its number and date, then one line on anything you could not check.
   Shape: "Three goals are behind. The first is the one to decide on: its KPI has been under its line for two weeks. The others
   are a week short of pace." Org changes are a short list, each with the bet it serves.
4. **A change they asked for**: make it when the server lets you, and say what you did. What is the owner's to confirm
   (a target, the vision, the strategy draft: `Boundaries` in `AGENT.md`) is a proposal or a Confirm card, never
   a direct write; a goal the owner agreed in their Assistant chat by a plain yes to a read-back is saved by that Assistant, as them, and you never write one; the strategy goes through `playbooks/draft-the-strategy.md`. A proposal you filed waits for their
   confirmation and is not live until then: say so in one line. A refusal says why, in one line, and you never work around it.
5. **A "no" to something you suggested** is a rejected suggestion with their words as its reason
   (`playbooks/goals-make-sense.md`), never a note of your own.
6. **Reply** (your final message, and the task's note in the same words), for a change: one sentence on what you did, one on
   what happens next. Shape: "I filed the change for the owner to confirm: the target moves from 58% to 60%, and the baseline and
   deadline stay. It is not live until they confirm." One paragraph, at most three sentences. Nothing else: no bullets or bold, ids, file names, commits, checks, trend analysis or notes about the proposal list. Do not say what you closed, recorded or committed.
7. **Finish, last, every time**: `hub task update <id> --status done --note "<the reply>"`. A proposal whose create returned
   its id is filed: do not list proposals again to confirm it, and do not reopen the task.

The Assistant tells the owner what you did. Do not narrate your tools, and never say something is done before you
have seen it done.

**Done when:** the reply is written in the shape above (or the refusal in one line), the change is made and seen or filed as a
proposal, and step 7 has run.
