# Lenses

A **lens** is a short set of questions about your strategy or your market, from a well-known framework, answered once.
The server sends the answers and the strategy to one model call and returns a result you can read: a summary, a few
sections, a chart, findings that point at your bets, what it assumed, and changes you may add to your draft. A lens
never publishes anything and never changes the draft by itself.

Lenses are on the [Strategy](strategy.md) page, under **Lenses**. Market lenses are also under the Market tab.

## What is on offer

| Lens | For | Style of | Chart |
|---|---|---|---|
| Playing to Win | strategy | Playing to Win | a five-step cascade |
| Blue Ocean | strategy | Blue Ocean Strategy | a value curve |
| Scenarios | strategy | scenario planning | a 2x2 |
| Seven Powers | strategy | Seven Powers | a table of bets and powers |
| Good Strategy | strategy | Rumelt's kernel | a table |
| Pre-mortem | strategy | a pre-mortem | a table |
| Positioning | strategy | Dunford positioning | none |
| Where to Play | market | Playing to Win | a table of segments |
| Five Forces | market | Porter's five forces | a table |

Each is "in the style of" its framework and is not endorsed by anyone. A lens file lists the questions, what each
answer looks like (words, a list, a score table or a choice), and what the result holds. OGSM, V2MOM and Plain are
older guide files kept for reference; they are not listed.

## Running one

1. Pick a lens. Answer its questions, or skip any of them. A skipped question is told to the model, which says in the
   result what it assumed instead.
2. Choose what it reads: the **published** strategy (the default) or your **draft** (owner only; the run is private
   to you). A strategy lens needs a published version unless you run it on the draft. A market lens reads the market
   pages and the strategy, and ignores the choice.
3. The run answers at once with `running`, then finishes in under a minute. The page and `hub lens show` poll it.

If no model provider is set up, the run is `no_model` and your answers are saved; nothing else happens.
A run that is cut off (a restart, or no answer in ten minutes) reads **failed** with "The run was cut off; run it
again".

Who may run one: the owner, the [Strategy Bot](strategy-bot.md), and for a market lens the [Librarian](librarian.md). Nobody
else, and no other bot. A run on the draft is the owner's alone.

## The result

- **Summary** (600 characters), up to eight **sections** of up to 4,000 characters, and a **chart** of the lens's kind.
- **Findings**: each says a bet is supported, challenged, or that there is a gap, and cites the bet when it is one. A
  finding that names a bet that does not exist is dropped.
- **Assumptions**: what the model filled in for a skipped question.
- **Changes**: up to eight, each a label and a partial `strategy_draft` payload (the same shape as in
  [Strategy](strategy.md#hub-and-the-api)). The server checks each one; a change that would be refused on apply is
  dropped, and it never carries a source of its own.
- **Sharpen**: up to six things that more research would improve.

The result is checked part by part: a bad chart is dropped and the rest is kept; a run with nothing usable fails with
the code `empty`.

## Acting on it

- **Add to draft** (owner only): puts one change into your draft, as you, with the revision you were looking at. If
  the draft moved, it answers 409 and nothing is written. A change can be added once. A run that was sent as a proposal
  answers 409 `proposed`: decide it on its card.
- **Sharpen** (owner only): files one research task for the Librarian for one item, once. Its findings go to the
  market page Open questions.
- A run the **Strategy Bot** starts files one `strategy_draft` proposal with the changes, marked as from the lens. It
  shows as a Decide card, "Confirm or reject a change to the strategy from the Blue Ocean lens". Confirming writes the
  draft as you; publishing stays your click. The Strategy Bot suggests a lens when a bet trips and in its weekly note, and
  never runs one unasked.

## `hub` and the API

```
hub lens list [--market]                           the lenses, with their questions
hub lens run <lens> [--answers-file f.json] [--draft] [--no-wait]
                                                   waits up to a minute; --draft is the owner's; --market lenses ignore it
hub lens show <run id>                             one run, in full
hub lens runs [--lens ID] [--limit N]              runs you may see, newest first, without answers
```

The answers file is `{"<step key>": answer}`: a list step is a list of short strings, a score step is
`{"columns": [...], "rows": [{"factor": "...", "scores": [0-5, ...]}]}`. The MCP tools are `hub_lens_list`,
`hub_lens_run`, `hub_lens_show` and `hub_lens_runs`. The routes, under `/api/v2`:

| Route | Who | What it does |
|---|---|---|
| `GET /lenses?applies_to=` | any reader | the listed lenses, strategy first |
| `POST /lenses/{id}/runs` | owner, Strategy Bot, Librarian (market) | `{answers, on?: published|draft}`; answers `202` with the run; `422 invalid` for a bad answer, `422 no_published` with nothing published, `403` for anyone else |
| `GET /lenses/runs`, `/lenses/runs/{id}` | any reader | runs the caller may see; a draft run is `404` to everyone but the owner |
| `POST /lenses/runs/{id}/apply` | the owner only | `{index, revision}`; `409` if the draft moved, `409 applied` if added already |
| `POST /lenses/runs/{id}/sharpen/{i}` | the owner only | files the research task once and returns its id |

Runs are rows in `lens_runs`, readable in [Hub SQL](hub-sql.md) by the same rule: a draft run only for the owner.
Answers are limited to 2,000 characters for words, twelve items of 200 characters for a list, and six columns by ten
rows for scores. The whole model input is capped at 24,000 characters.

Related: [Strategy](strategy.md), [Your Strategy Bot](strategy-bot.md), [Librarian](librarian.md).
