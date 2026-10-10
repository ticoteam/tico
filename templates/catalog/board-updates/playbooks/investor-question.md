# Answer an investor question

Triggered by a task that pastes or names a question from an investor or board member. Budget 20
minutes. The outcome is a short draft answer for the owner, with its evidence. Nothing is sent.

---

## 1. Read the question and the history

    hub task show <id>

Check `knowledge/asks.md` and the last two updates: has this been answered already? If so, the draft
points to it in one line. If mail is connected, read the thread with `$HUB_DIR/scripts/mail.sh search`.

## 2. Find the facts

Answer only from the record: `hub goal list --all`, `hub kpi show <kpi id>`, `hub update list`,
`hub meeting search`, the finance inputs. A fact you cannot source is a marked gap, never a guess. Check
`knowledge/exclusions.md` before naming anyone.

## 3. Draft

Two to six sentences, the answer first, the number with its date, and one sentence on what the team
is doing about it if the news is bad. Write it in the owner's voice, plain, no spin. If the question is
about a legal, tax or valuation matter, say so and draft only a holding reply for the owner to decide.

## 4. Hand over

Attach the draft to the task with the facts and sources under it, and say what you could not confirm.
Send the requested text to its recipient with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft. `hub task update <id> --status done --note`.
