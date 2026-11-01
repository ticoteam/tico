# Turn a request into a task

Triggered by anything a human asks you for that you are not going to answer yourself. Budget five
minutes. The outcome is one task, on the right owner, that the owner can start without asking
anybody anything.

Inputs: the human's message, `hub task list` and `hub task list --all`, and `knowledge/routing.md`.

---

## 1. Decide what it actually is

Read what they wrote, then read the record before you decide anything:

    hub task list
    hub task list --all

Four outcomes, and only four:

| What it is | What you do |
|---|---|
| The record already answers it | Answer, name where you read it, and stop. No task. |
| Work a bot owns | One task on that bot. |
| A new bot, a broken bot, a change to a bot's instructions or schedule | One task on `botops`. |
| A decision, a price, a promise, an exception | One task on the human who decides. |

An existing open task already covering it is not a second task. Add what is new to that task and
tell the requester which one it is.

## 2. Pick the owner

`knowledge/routing.md` is the standing answer. If the request does not match anything in it, do not
guess and do not give it to the nearest bot: ask the requester one question about who should own it,
and add the answer to `knowledge/routing.md` in the same run so you never ask again.

## 3. Write it

    hub task create --owner <owner> --title "..." --body "..."

The title is the ask in one line. The body has, in this order:

1. **What is wanted**, in the requester's own words. Quote them. Your paraphrase loses the thing
   they actually meant.
2. **Why now**, if there is a reason or a date.
3. **What the owner needs to start**: the link, the account, the file, the answer they would
   otherwise have to come back and ask you for.
4. **What good looks like**, in one line, so the owner knows when they are finished.

Leave out anything you are guessing at. A gap you name is workable; a gap you fill with an
assumption is a wrong result nobody catches.

## 4. Close the loop

Tell the requester what you filed and where, in one sentence. Say what will happen, never that
something has already been done that has not. If the request needed a decision first, say what you
are waiting on and from whom.

## 5. Record it

If the request revealed something durable, write it down in the same run: a new kind of work and its
owner into `knowledge/routing.md`, a human and what they are responsible for into
`knowledge/people.md`, a fact about the team into `knowledge/company.md`. Then rewrite `state.md`
and commit.

## When you should not file anything

- The request is a decision only the requester can make. Give them the options and let them decide.
- Two humans have asked for opposite things. One task for whoever decides, naming both, not two
  tasks that will collide.
- It would need a send, a spend, or a publish. That is an approval on an existing task, not a new
  task that quietly authorises it.

**Done when:** one task is on the owner the routing names, it carries the requester's words with the gaps marked, and the requester has one sentence on what was filed and where.
