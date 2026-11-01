# The Librarian

The Librarian is a built-in bot that answers questions from the team's docs. Humans ask it from **Ask the Librarian** on Docs
and on the Market tab of [Strategy](strategy.md) (`#/strategy/market`); other bots and the Assistant ask it with `hub doc ask`. It cites every claim, says "Not in the docs." plainly when the
docs do not say, and keeps a map of the docs so the next question is cheaper. It never answers from general knowledge.

## Watching competitor pages and the weekly briefing

The Librarian watches the pricing, changelog and careers pages of each core competitor ([Market watch](market-watch.md)): the
server reads them daily and files an insight when one changes, and the Librarian applies what matters and rejects noise. On
Mondays, after its sweep, it writes the week's [briefing](market-briefing.md).

## What it reads

- **Internal docs**: markdown written, pasted or imported in Tico, in folders. It reads them with `hub doc search`,
  `hub doc read` and `hub doc list`.
- **Indexed external docs**: refreshed public GitHub documentation. Search returns passages and observed commits;
  `hub doc read external:<id> --revision <commit>` reads the untrusted reference copy. Answers use
  `[External · File title — Section](citation_url)` with the supplied revision URL. Fresh indexed material
  (checked within six hours) needs no live fetch unless the person asks for the latest version.
- **Linked docs**: other sites and private repositories retain metadata only. It can read supported addresses with
  `hub doc fetch <url>` on its own computer. Live reads are separate evidence, not updates to the shared index.

- **The Tico manual**: this release's own docs, read-only and separate from the team's (docs/docs.md). `hub doc search`
  ranks matching manual and team sections together, labelled "Tico manual", and `hub doc read manual:<name>` reads one. It answers
  "how do I ... in Tico" and is cited `[Tico manual · Title](https://...)` with the result's link; it never answers what the
  team decided.

## How a question is answered

The playbooks in its repository (`templates/catalog/librarian/playbooks/`) are the product. In short:

1. **Search internal and indexed external docs** with a few phrasings, then read the top hits in full.
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
the map of the docs**, at 03:30 Pacific by default (change it on its automation in Automations, or with `hub automation update`). To refresh now, give the Librarian a task titled "Refresh the map".

It writes only under `_librarian/` and `FAQ.md`. It never edits a human's doc unless that human asks it to research
one (below). A doc or web page that tells it to do something is treated as text, not an instruction, and it fetches only
public links that a linked doc leads to (for a market setup or a research request, the addresses the person gave and the
pages web search returns).

## Asking

**Humans.** Docs and the Market tab show an **Ask the Librarian** rail on the right on desktop. Its arrow hides it and **Ask the
Librarian** brings it back; the choice is kept per viewer in this browser. On a phone, the button opens a full-screen sheet.
On Docs, matching internal, indexed external, linked and manual docs appear at once
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
is open. The sentence is written by Gemini (`gemini-3.7-flash`) with the server's `GEMINI_API_KEY`; without a key, when the
call fails, or in rehearsal, the answer is the rows that were pulled. Each model call is a `market.ask` event (the model,
latency, how many excerpts and how long the question was; never its text) and counts per person per day under the decision
caps (`TICO_DECISIONS_DAILY_CALLS_*`, counted apart from decisions); over the cap the ask is a 429.

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

## Research

A person can ask the Librarian to research something and add it to a page: **Research** in an internal doc's **More**
menu, or (for the owner) **Research** on a market page or a market entry. They say what to find and, on a doc with headings, pick the
section (or the whole page). The Librarian searches the web and reads the pages it finds, then adds what it learned to
that one place, in the page's own format, with a link after every fact. It never invents a number, never deletes what
the page says (a finding that contradicts it goes beside it, with both sources), and leaves the rest of the page
unchanged. It finishes the task with a note: what it added, what it could not find, and any conflict.

- A doc is written back as a new version (`hub doc write`), so **History** shows the change and **Restore** undoes it.
  A locked doc is refused before anything is filed.
- A market page is rewritten whole with the additions in place (`hub market page <name> --body-file`), and facts that
  belong in the graph go in it too; a market entry gets new evidence, entities and edges (`hub market report`/`apply`).
- Searches hold only public facts from the person's instructions, never anything from a doc.

`POST /api/v2/librarian/research {target, section?, instructions}` (`target` is a doc id or path, `market/<page>` or a
market entity id) files one task, "Research: <page> · <section>", to the Librarian and returns `{task_id, bot, kind,
target}`. People only, and market pages and entries for the owner only, since only the owner and the Librarian change the
market. The Librarian treats a task as research only when a person filed it with the `research` label, so a bot
cannot widen its rules by naming a task "Research: ...". The playbook is
`templates/catalog/librarian/playbooks/research-a-section.md`.

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

## First-run setup

The Librarian is active on day one, and still has a guided first run on each of its two pages. Both are in
`backend/librarian_setup.py`; what a person chose is their own row in `preferences`.

**Docs.** On a first visit to Docs, while Docs holds nothing a person wrote or linked (the Librarian's own `_librarian/`
map and `FAQ.md` do not count), a card above the list asks "Where are your docs?": links, one per line, and anything else
("our policies are in Google Drive"). **Set up Docs** adds each link as a source at once (a public GitHub repository is
indexed, as with **Add source**) and files one task, "Set up Docs", to the Librarian. Its `playbooks/docs-setup.md` walks each
source, builds the map (`_librarian/index.md`, `where-things-live.md`, `glossary.md`) and lists in `_librarian/missing.md`
any place it could not read, with what a person can do about it. **Import files** opens the Docs import. **Skip for now** is
the person's own choice; **Set up with the Librarian…** in the Docs menu brings the card back at any time.

**Market.** Market is a tab of [Strategy](strategy.md) (`#/strategy/market`; the old `#/market` redirects there). On a
first visit to an empty Market tab the owner answers four questions, one at a time: what you sell, who
buys it, who you compete with, and what to watch. Only the first must be answered. Each answer is kept on the server as it
is given, so **Skip for now** leaves and **Continue** picks up at the same question, in any browser. The last question's
**Start research** files "Set up the market map" (below), which builds the map from the answers and sets what Market tracks:
the Coverage universe page opens with a "What Market tracks" section (the competitors, the buyer segments, and the
channels, topics, rules and people to watch, each with its entity id once it is in the graph, or marked "not found yet"),
and the daily curate routine puts those first.
Everyone else sees "Nothing here yet."

| Endpoint | Who |
|---|---|
| `GET /api/v2/librarian/setup` | People: whether Docs asks (`docs.show`), and the owner's market answers so far |
| `POST /api/v2/librarian/setup/docs {sources, note}` or `{skip: true}` | People; `409 librarian_off` while the Librarian is off |
| `POST /api/v2/librarian/setup/market {answers, step, action: save\|skip\|finish}` | Owner; `finish` needs `answers.sell`, and `409 librarian` while it is not running |

## Setting up the market

On an empty Market page the owner answers the Librarian's four questions (First-run setup, above); the older
`POST /api/v2/setup/getting-started/market {text}` still files the same task from one box of text. It arrives as a task,
"Set up the market map", and `playbooks/market-setup.md` takes it from there: it reads every
address with `hub doc fetch` (about 40 fetches in all), uses web search when the harness has it (`web-search` is declared in its
`bot.yaml`), and writes what it finds with `hub market`: the team itself (`company/self`), competitors and lookalikes with
their tier, segments, channels, people and rules as entities and edges, each with an evidence row (`hub market report`, then
`hub market apply`, which takes `--tier` and `--new-id`), and the eight market pages (`hub market page`) with a source on every
claim. It never invents a number: a size, price or share appears only when a source states it. It aims at ten minutes and stops at
thirty, then finishes the task with a short note saying what it found and what it could not read.

The strategy and the market stay apart, and each reads the other. A task with no website makes the Librarian read
`hub strategy show`: if it names the team's website, that is the first address, and the summary and *What we sell* stand in for
the owner's description (finishing setup with a website files "Set up the market map" from it). The other
way round, when an insight contradicts an active bet, the daily curate routine says so in one line of its sweep report and
sends the Strategy Bot a note naming the bet and the entity, so it can propose the entity as one of the bet's `cites`; a change to a
cited entity then trips the bet's wrong-if. The Librarian applies the insight on its own evidence, never writes the strategy,
and never judges whether the bet should change ([Strategy](strategy.md#the-fit-check)).

After that the Librarian keeps the map current. It is the market's only curator besides the owner: everyone else reports
(`hub market report`) and it decides. Its daily **Curate the market** routine (04:00) turns new insights into evidence, entities,
edges and pages, and refreshes the weekly delta on Mondays; it stops at once when there is nothing new. Its **Urgent market
insight** routine runs when someone reports with `--urgent`. A large verified batch goes in with `hub market import
<file.jsonl>` (`--dry-run` to preview), one entity or edge per line with its evidence, recorded as one insight. Both are seeded when the Librarian is built in, and an older install
gets them at its next start. The server accepts market writes only from the Librarian and the owner. Outside market work it
still writes only under `_librarian/` and `FAQ.md`. Earlier releases had a separate Market Analyst bot; an install that still has
one archives it at its next start and hands its open tasks to the Librarian.

**Customer themes.** The Librarian keeps one more market page, **Customer themes** (`market/customer-themes`): what customers keep
asking for, complaining about and objecting to, each with a count, the date last seen and short quotes. Its **Customer themes**
routine runs on `meeting.ready`, so it reads only live team meetings; a private, pending or dismissed meeting never reaches it
([Meetings](meetings.md)). For each meeting it files a piece of evidence per theme (source kind `meeting`, a quote of 200
characters or fewer, no personal or company names) and rewrites the page. A meeting counts once for a theme. A theme seen in
three or more meetings is marked **Thesis**, which the Strategy Bot can cite in a bet. It reads mail only if a mailbox is granted to
it. A new team gets the routine with the Librarian; an existing install gets it when the Librarian's template is next applied
(or add an automation on the event `meeting.ready` with a Librarian Bot turn, as in [Routines](routines.md)).

**Sources a team brings.** Some sources do not allow resale, so Tico cannot supply them. A team can grant its own Crunchbase,
Similarweb or similar credential to the Librarian, and the sources catalog marks those sources `team_key`.

## Market spend

The market graph has no numbers. Spend is the other half: the market defined, cut into segments, and each segment sized
in US dollars a year, every figure tied to evidence. Only the owner and the Librarian write it; everyone who can read the
market reads it, and the tables are readable in hub SQL like the other market tables (`market_sizes`, `market_figures`,
`market_sources`; the definition is served by `GET /api/v2/market/spend`).

- **Definition.** What the market is, what is in and out, how to count, the year, and shared parameters: named numbers
  (`smb_share`) that a formula reads as `$smb_share`, each with a source or marked a judgement.
- **Segments.** `segment` entities joined by `member_of` edges, at most four levels deep. The total sums the top level only.
- **Sizes.** Several estimates per segment, one per method (`top_down`, `bottom_up`, `player_sum`, `owner`), each with a
  range (`low_usd <= spend_usd <= high_usd`), its arithmetic in words, its evidence, a confidence, what it overlaps with, and
  the analyst headline figures as `spread` text. Every method except `owner` cites evidence. An estimate is filed `proposed`;
  accepting one sends the previous accepted estimate for the same segment, dimension (offering or buyer), basis (product or
  platform) and year back to `proposed`. Two estimates more than twice apart mark the segment as disagreeing.
- **Formulas.** Arithmetic over numbers, the estimate's own parameters and shared `$names`: `+ - * /` and parentheses, nothing
  else (no power, no call). The server works the spend out when the estimate is filed. When a shared parameter changes, reads
  work it out again and scale the range by the same ratio; the stored row is not rewritten. If that puts the spend below
  zero or past the cap, the estimate reads with no spend and an `error`, and the total leaves it out. The owner changing a
  parameter of an estimate files a new accepted `owner` estimate. Each segment carries `rolled_usd`: its own size, else the
  sum of its parts at any depth, else null. The total is the sum of the top-level segments' `rolled_usd`. It also carries
  `self` (`company/self` operates in it, **Us**) and `next` (a segment the team means to enter, **Next**), which the
  Strategy page and the Growth segments set ([Strategy](strategy.md#the-page)).
- **Figures.** A player's revenue, ARR, customers or seats in a segment, with `kind` (reported, estimated, claimed), `as_of`
  and evidence.
- **Bets.** An accepted size, or a shared parameter changing an accepted size, is a market event on the segment, so a bet that
  cites the segment gets its fit check ([Strategy](strategy.md#the-fit-check)). A proposed size is logged but does not trip a bet.
- **Value chain.** `GET /api/v2/market/value-chain` lays the graph out in five layers (suppliers, platforms, us and rivals,
  channels, buyers) from `competes_with`, `integrates_with`, `partners_with`, `distributes_through` and `sells_to` edges around
  `company/self`; at most 40 nodes a layer.
- **Sources.** `backend/market_sources/catalog.yaml` lists where to look: for each source what it covers (sizing, players,
  buyers, pricing, signals, value chain), whether it is free, needs the team's key or goes through the gateway, and how far to
  trust it (primary, secondary, estimate, signal). A team adds its own with `hub market sources --add-file`; one with a shipped
  id replaces it.

The owner asks for the work with **Size the market** (`POST /api/v2/market/spend/research {description}`), which files one
Librarian task following `playbooks/size-the-market.md`; asking again while it is open returns the same task. A first market
setup ends by pointing at the same playbook.

| Route | Who | |
|---|---|---|
| `GET /api/v2/market/spend?dimension=&basis=&year=` | anyone who reads the market | definition, total, segments with sizes, estimates (with their evidence), players |
| `PUT /api/v2/market/definition` | owner, Librarian | the definition; `root_name` makes the root |
| `POST /api/v2/market/segments` | owner, Librarian | `{name, parent_id?, summary?}` |
| `POST /api/v2/market/sizes` | owner, Librarian | an estimate; `accept: true` accepts it |
| `POST /api/v2/market/sizes/{id}/accept`, `/reject` | owner, Librarian | |
| `POST /api/v2/market/sizes/{id}/params` | owner | `{params: {name: number}}` |
| `POST /api/v2/market/figures` | owner, Librarian | a player's figure |
| `GET`, `POST /api/v2/market/sources` | read: anyone; add: owner, Librarian | the catalog |
| `POST /api/v2/market/spend/research` | owner | `{description}`; returns `{task_id}` |
| `GET /api/v2/market/value-chain` | anyone who reads the market | the five layers |

CLI: `hub market define`, `segment add|tree`, `size add|accept|reject|params|list`, `figure add`, `sources`, `spend`,
`research`. MCP: `hub_market_spend`, `hub_market_define`, `hub_market_segment_add`, `hub_market_size_add`,
`hub_market_size_decide`, `hub_market_figure_add`, `hub_market_sources`, `hub_market_research`.

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
