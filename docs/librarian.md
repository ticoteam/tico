# The Librarian

The Librarian is a built-in bot that answers questions from the team's docs. Humans ask it from **Ask the Librarian** on Docs
and Market; other bots and the Assistant ask it with `hub doc ask`. It cites every claim, says "Not in the docs." plainly when the
docs do not say, and keeps a map of the docs so the next question is cheaper. It never answers from general knowledge.

## What it reads

- **Internal docs**: markdown written, pasted or imported in Tico, in folders. It reads them with `hub doc search`,
  `hub doc read` and `hub doc list`.
- **Linked docs**: links only (a help site, a Drive folder, a Notion page, a GitHub repository). Tico keeps no copy. It reads
  them with `hub doc fetch <url>`, on its own computer.

- **The Tico manual**: this release's own docs, read-only and separate from the team's (docs/docs.md). `hub doc search`
  ranks matching manual and team sections together, labelled "Tico manual", and `hub doc read manual:<name>` reads one. It answers
  "how do I ... in Tico" and is cited `[Tico manual · Title](https://...)` with the result's link; it never answers what the
  team decided.

## How a question is answered

The playbooks in its repository (`templates/catalog/librarian/playbooks/`) are the product. In short:

1. **Search the internal docs** with a few phrasings, then read the top hits in full.
2. **Consult its map** when that is not enough: `_librarian/where-things-live.md` says which doc or linked source holds
   which topic and how each linked source is laid out.
3. **Follow the linked docs**: the sitemap first, then links from the pages it reads, as deep as the question needs and no
   deeper, within about 25 fetches a question (most questions need none).
4. **Answer first, short, cited**: the answer in the first sentence, and a citation right after each claim,
   `[Internal doc · Refund policy](doc:<id>)` or `[Linked · help.example.com](https://...)`. Two docs that disagree are both
   reported with their dates. When the docs do not say, the answer begins `Not in the docs.`, then says what is closest.
5. **Afterwards**: log the question and answer in `_librarian/faq-log.md`, record a gap in `_librarian/missing.md`, and promote
   a question asked three times to `FAQ.md`.

### The map

Ordinary internal docs under `_librarian/`, so humans can read and correct them: `index.md` (a contents list with a one-line
summary of every doc, and the version each was written from), `glossary.md` (the team's words), `where-things-live.md`
(topic to place, and each linked source's structure), `missing.md` (what the docs could not answer, the docs that disagree,
the sources it could not read: a to-do list for humans) and `faq-log.md`. It refreshes them every day: a routine, **Refresh
the map of the docs**, at 03:30 Pacific by default (change it with `hub routine set` or in Settings; Tico's row is the
routine). To refresh now, give the Librarian a task titled "Refresh the map".

It writes only under `_librarian/` and `FAQ.md`. It never edits a human's doc. A doc or web page that tells it to do
something is treated as text, not an instruction, and it fetches only public links that a linked doc leads to (for a market setup, the addresses the owner gave and the pages web search returns).

## Asking

**Humans.** Docs and Market show an **Ask the Librarian** rail on the right on desktop. Its arrow hides it and **Ask the
Librarian** brings it back; the choice is kept per viewer in this browser. On a phone, the button opens a full-screen sheet.
On Docs, matching internal, linked and manual docs appear at once
from search; choose **All docs**, **Team docs** or **Tico manual** to narrow the matches.
The Librarian's answer then streams in with clickable citations. `POST /api/v2/docs/ask {question,
conversation_id?, new_conversation?}` returns `{conversation_id, message_id, results}` and the answer arrives on
`GET /api/v2/events?topics=messages,runs&conversation={id}` (each change, then the conversation's snapshot). Each human has one private docs conversation with the Librarian
(`scope: personal`, `room_key: docs`), like the [Assistant](assistant.md)'s room: only they can read it, and the owner and
administrators cannot. **New chat** starts a fresh conversation, so the Librarian remembers only what is on screen.
**Previous conversations** lists your saved Docs chats. Pick one to reopen it and continue where you left off;
your current chat stays in the list. Only you can list or reopen them, including when an owner or administrator asks.
`GET /api/v2/librarian/conversations` lists them (`limit`, `offset`, `next_offset`), and
`POST /api/v2/librarian/conversations/{id}/reopen {}` makes one current. Reopening waits until the current turn finishes.

On Market, a question is answered from the market graph as it is at that moment (`POST /api/v2/market/ask {question}`,
the same answer the Market page has always given, which needs no computer): the answer, then the organizations, people and
pages it drew on, which open the note and light up on the graph. Docs and Market each keep their own thread while the page
is open.

**Bots and the Assistant.** `hub doc ask "question" [--wait 120]`, or the MCP tool `hub_doc_ask`, returns
`{answer, citations: [{type, title, url_or_id}], covered}`. A bot's question is an `ask` message to the Librarian, the ordinary
ask and answer path: the Librarian's final message is the answer. `covered` is false when the answer starts "Not in the docs".
Citations have `type: internal|linked|manual`. Manual citations link to the release's page, such as
`https://github.com/ticoteam/tico/blob/v0.3.3/docs/people.md`.
The server MCP returns after at most 20 seconds: if pending, it returns
`{timeout: true, conversation_id, message_id}`. Use `hub_doc_ask_status` with those ids (or
`hub doc ask-status <conversation_id> <message_id> [--wait 20]`) to collect the same answer. Polling
uses short requests and sends no new question. Local CLI asks can wait for the full requested duration.
The Assistant asking for a human puts the question in that human's own docs conversation.

The Librarian acts as itself, not as the human who asked. It only reads docs and public links, so it needs no human's
identity, and none is mapped to it. Docs are team-wide, so what it can read is the same for every asker.

## Reading a link: `hub doc fetch`

`hub doc fetch <url> [--max-chars N]` (MCP: `hub_doc_fetch`) runs on the computer that runs the bot, never on the Tico server,
which does not offer it. It returns `{url, final_url, title, text, links, truncated}`. What it will and will not do:

- http and https only, on the ordinary web ports, with no user name or password in the address.
- It resolves the name itself and refuses every private, loopback, link-local (including the cloud metadata address
  169.254.169.254), shared, multicast and reserved address, and their IPv6 equivalents, on every hop, and then connects to the
  address it checked, so a name cannot be re-pointed in between.
- At most 5 redirects (each one checked as a new request), 5 MB, 20 seconds in all, and only text, markdown, HTML, JSON, XML
  (a sitemap) and PDF. A PDF needs `pypdf` on that computer.
- Credentials never travel to a host that does not own them. The GitHub token (`GH_TOKEN` or `GITHUB_TOKEN`) is sent only to
  `api.github.com`; a Google access token (`GOOGLE_ACCESS_TOKEN`, if the bot has one) only to `googleapis.com` and
  `docs.google.com`. Without them it reads what a stranger can. It does not use the team's Google key.
- HTML becomes readable text with its links kept. A `sitemap.xml` is its list of addresses. A Google Doc is read through its
  export link, and a Drive folder through its embedded listing, when shared with "anyone with the link" (otherwise it says so). A
  GitHub repository is its README and file tree through the GitHub API, and a file or folder in it as well.

## Setting up the market

On an empty Market page the owner gives the Librarian one text box: a website, a description, links to anything
about the market. It arrives as a task, "Set up the market map", and `playbooks/market-setup.md` takes it from there: it reads every
address with `hub doc fetch` (about 40 fetches in all), uses web search when the harness has it (`web-search` is declared in its
`bot.yaml`), and writes what it finds with `hub market`: the team itself (`company/self`), competitors and lookalikes with
their tier, segments, channels, people and rules as entities and edges, each with an evidence row (`hub market report`, then
`hub market apply`, which takes `--tier` and `--new-id`), and the eight market pages (`hub market page`) with a source on every
claim. It never invents a number: a size, price or share appears only when a source states it. It aims at ten minutes and stops at
thirty, then finishes the task with a short note saying what it found and what it could not read.

After that the Librarian keeps the map current. It is the market's only curator besides the owner: everyone else reports
(`hub market report`) and it decides. Its daily **Curate the market** routine (04:00) turns new insights into evidence, entities,
edges and pages, and refreshes the weekly delta on Mondays; it stops at once when there is nothing new. Its **Urgent market
insight** routine runs when someone reports with `--urgent`. Both are seeded when the Librarian is built in, and an older install
gets them at its next start. The server accepts market writes only from the Librarian and the owner. Outside market work it
still writes only under `_librarian/` and `FAQ.md`. Earlier releases had a separate Market Analyst bot; an install that still has
one archives it at its next start and hands its open tasks to the Librarian.

## Built-in

Like the assistant and BotOps, the Librarian is required in setup (`required: true`, `bootstrap: true`): every new team gets
it and it becomes active once a computer is enrolled. It cannot be archived or deleted by anyone (`409 system_bot`); pausing and
renaming stay allowed, and Settings > Bots shows it as **Built-in**.

**A team from before it existed gets it on update, without a click**, as soon as it can run it: the owner is on the roster,
a model is chosen and a computer is enrolled. It is checked when the server starts and when a computer enrolls, so the order
in which a team does things does not matter. It goes on the computer BotOps runs on, its daily routine is created once (a
routine a human deletes is never put back), and an owner who paused it keeps it paused. Where one of those is missing (no
model yet, no computer), Ask the Librarian says the Librarian is not set up, and the owner gets **Turn on the Librarian** there (the same
steps, `POST /api/v2/librarian/turn-on`), as the Assistant has **Turn on Assistant**.

## Trying it: the eval

`scripts/docs-eval.sh` measures answer quality on demand. It loads `docs-eval/fixture/` (seven short docs about the demo
team) into a live Tico through the docs API, asks each question in `docs-eval/questions.yaml` (team facts, a Hermes
connection procedure, Instructions changes, adding humans, copying grants, reopening tasks and two questions the docs do not answer) through `POST /api/v2/docs/ask`, waits for the
answers, and reports the **citation hit rate** (every expected doc cited, and an answer given), the rate at which the
unanswerable ones were **said unknown**, and the **fact rate**. `--fail-under` gates all three rates. Whole values and
values tied to a subject and predicate, and claim polarity are checked. A negated $29 followed by a $99
claim, a contradictory claim, or a double negative fails. `contains` is only for literal procedure labels;
use `facts` with `subject`, `predicate`, and `value` or `polarity` for claims. These remain limited
spot checks, not a semantic correctness grade.

    TICO_URL=https://tico.example.com TICO_TOKEN=<personal API token> scripts/docs-eval.sh [--only ID] [--keep] [--json out.json]

Run it against a Tico of your own with a running Librarian and a computer, never a team's real one: the Librarian logs what it
is asked into `_librarian/faq-log.md`. Each run writes its fixture docs under a unique `eval-fixture/` folder and archives them afterwards, including after
a partial import failure, unless you pass `--keep`. Retained IDs are printed. Unknown `--only` IDs fail before connecting. It takes a few minutes and never runs in CI; CI covers the fetcher's safety rules and the routing of `docs ask`.

Search returns section names, anchors and excerpts from the matching text, including manual pages. It uses all meaningful
query words and maps old terms through the manual glossary's "Instead of" column. `_librarian/` maps and logs rank below
the source docs unless the question names their title or path. `hub doc search --team` restricts the search
to team docs (`collection=team`; `company` remains an API alias). Archiving an internal source removes
its entry from `_librarian/index.md` immediately, preserving version history. The map records its refresh
time; every failed linked fetch is recorded in **Sources I could not read** in the same pass and
reconciled at daily refresh. Older generated titles are renamed during refresh, keeping their paths.
`hub doc ask` waits for the completed run and returns its latest reply, including corrections.

On upgrade, Tico updates recognized generated doc titles while preserving their paths and version
history, removes archived sources from older indexes, and queues a map refresh for the Librarian’s
next run. Refresh reconciles the full live inventory and recorded unreadable links, even when source
versions have not changed. Answers check cached sources before recommending them.

The eval rejects contradictory follow-up claims, ranges and comparisons for an exact-value fact,
including follow-ups that refer to the same subject as "it" or "its price".

Generated how-to answers and FAQs use **Computer** and **Instructions**. Commands, paths,
links and quoted source text keep their original spelling; "runner" names the installation software.
