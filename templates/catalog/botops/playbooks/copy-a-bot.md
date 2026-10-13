# Copy a bot

Triggered by a human's own chat message to you: "make me a copy of the support bot", "start from how Maya's scribe works", "update my
copy from the original", "send my changes back to the original". Not by a task, a document, or a message another bot wrote. Budget 10
minutes.

You act **as the human who wrote to you**. The server checks every command with *their* rights and records it as theirs, "via BotOps". If
it refuses, say so in one line, and who can change it, and stop.

What a copy is: an ordinary bot **owned by the person who asked**, reporting to them, with the original's instructions (`AGENT.md`),
`skills/`, `playbooks/`, `knowledge/` and `tools:` list, in a fresh repository with one commit, "Copied from <original> at <commit>".
Nothing is shared and nothing stays linked: the copy and the original change on their own. The only thing kept is where the copy came
from, so the two explicit requests below can work. A branch follows its original and shares its repository; this copy stays independent: anyone who may read a bot may ask
for a copy.

## 1. Copy it

    hub bot copy <original> [--name "<Display>"] [--slug <slug>] [--with-memory] [--computer <label>]

- Ask about memory only if they raised it. The default leaves out the original's memory, notes, state and reports, and the copy starts
  with a blank `state.md`. `--with-memory` brings them too; use it only when they asked for the original's history.
- No credential, `.env`, key file or `secrets/` is ever copied. Routines are not copied either (the copy starts with none).
- They need to be allowed to add bots and to be within their limit; a copy counts like any bot they add. If the server says they are at
  their limit, offer to archive one they no longer need, or say an admin can raise it, and stop.
- The original's files come from this computer's workspace, or, when the bot runs on another computer, from its GitHub repository
  (read-only, for a bot they may read). If the command says the original's repository isn't on this computer or GitHub, tell them its
  owner has to publish it, and stop; if it is some other failure, `hub support file "<what they asked, what the command said>"`.
- The built-in bots (the Assistant, BotOps, the Librarian, the Goal Manager) are not copied. Explain that limit in one line and offer the nearest catalog role
  (`playbooks/build-me-a-bot.md`). Do not create it or call it a copy unless they choose it.

It answers the new bot's name, its model (the original's, where they may use it, else the team's default), what it did about
credentials, and whether the repository is on GitHub yet (`published`; when it is not, it stays on this computer and is published once
the bot is placed).

## 2. Credentials

The answer's `credentials` lists what the original's tools need:

- **`granted`**: the requester is a credential administrator, so the copy already has those, as grants. Nothing more to do.
- **`needs`** ("needs credential X"): open the card for each and do not wait for the answer to do the rest:
  `hub credential request <VARIABLE> --for-bot <copy> --label "your Jira credential" ...` (`playbooks/connect-a-tool.md`). When the
  credential is already stored and only they lack the right to share it, say who can (the message names the administrators), or follow
  `playbooks/share-a-credential.md` if they are one. Never copy a value from the original's files.

## 3. Make it theirs

Read the copy's `AGENT.md` and `bot.yaml` once for what belonged to the original's people, not the role: a person's name, a mailbox or
address, a channel, a customer, a file path. List what you found in your report and change it only as they tell you; a message bot's
mailbox is one person's, so never keep the original's. Then `hub bot check <copy>` and fix what it fails on, and commit in the copy's
repository.

If they asked you to take it live, `hub bot go-live <copy>` (`playbooks/build-me-a-bot.md`, step 6). Otherwise it stays planned and you
say so.

## 4. "Update my copy from the original"

Only when they ask. `hub bot update-from-original <copy>`.

It compares the original's `AGENT.md`, `skills/` and `playbooks/` at the commit the copy was made from and now, with the copy's files, and
merges file by file with git. The answer's `status`:

- **`updated`**: one commit on the copy's repository took what the original changed and kept what the copy changed. Say in a line
  what came in (`updated`, and read `original_diff` if they want detail).
- **`current`**: nothing new in the original. Say so.
- **`conflicts`**: nothing was changed. Each conflict shows what the original changed and what the copy changed in the same file. Tell the
  person in plain words what differs, ask which they want (or both, combined), write that into the copy's file, commit it, then run
  `hub bot update-from-original <copy> --resolved` so the copy counts as up to date. Do not guess which side wins.

If it says the copy has changes that are not committed, commit or discard them first.

## 5. "Suggest this to the original"

Only when they ask. `hub bot suggest-to-original <copy> [--paths AGENT.md skills/<name> ...]`.

It takes the copy's own changes to its instructions and skills. If they may write to the original and GitHub is connected, it opens a pull
request on the original's repository and answers its link; otherwise it files a task for the original's owner with the diff. Say which
happened and give the link or the task. Files the original changed too since the copy are held back: say so and offer to update from the
original first. Never push to the original's repository yourself.

## When it goes sideways

- **`on_behalf_of` refused.** The run was not started by a human's own chat message or their own comment on their open task. Tell whoever is on the task; do not retry.
- **"not a copy".** That bot was not made with `hub bot copy`, so there is nothing to update from or suggest to.
- **A product problem** (the command fails in a way that is not the person's rights): `hub support file`, with what you saw.
