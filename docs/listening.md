# Listening

> **Beta**: Listening works today and may still change. See [Beta features](support.md#beta-features).

Listening reads the outside world and sends a short, ranked text into an automation. A listening has three parts:
**Sources** (what to read), **Rules** (remove copies, then keep and tag, or drop) and **Rate limits** (how many items
go out, and how often). What comes out is text. The automation's ordinary steps do the rest: post it, give it to a bot,
make a task.

Listening is not a pipeline. A post never becomes a task on its own, and nothing waits for a person to accept or reject
it.

[Use Tico](using-tico.md) · [API](api.md) · [Glossary](glossary.md)

## Start with one source

1. Open **Listening** in the sidebar, under Automations. One listening comes ready-made:
   [Market listening](#market-listening). Open it, or press **+** for a new one.
2. Add a source. A **Push** source needs nothing else in Tico. A **Search** source needs a bot that holds the Listening
   tool and a browser: add the Social Media Manager from **Settings > Bots > Add from template** and press **Set up**.
3. Write a Keep rule for each thing worth hearing, with a tag. Leave the rate limit at 10 a day to start.
4. Configure a server Decision provider ([Decision setup](../questions/README.md)). Rules are answered by it; without
   one, nothing is kept and the listening's dot goes hollow.

A listening is an automation of one special kind, so it has an owner, an on/off switch, versions, runs, cost and a spend
cap like any other. It is listed under Listening, not in the Automations list.

## Sources

A source is one thing to read. A listening has 1 to 50.

| Kind | What it reads | Who reads it |
|---|---|---|
| **Search** | A search or a community on a site (Reddit, X, LinkedIn and so on) | A bot with a browser that holds the Listening tool. It is due again 24 hours after its last read |
| **Page** | One web address | The server, once a day. A change is an item |
| **Push** | Nothing: an outside service (a monitoring service, a partner, a script) sends matches to an address | Nobody. Saving the source shows a secret address once |

Every read is saved with whether it worked. Saving the same post twice does nothing.

Each source has a dot:

- **Filled**: the last read worked, and it is not overdue.
- **Hollow**: blocked, failed, never read, or not read for two days.

A push source is never overdue. Its dot is filled once something has arrived. The Listening line on
[Tools](connect-tools.md#tools-list) shows the worst source's state, so a blocked source does not read as "on".

A blocked or rate-limited read is not evidence that nothing was posted.

## Rules

Rules are plain text, in a list. A listening has up to 20, besides the first one.

- **Remove duplicates** is always first, always on, and cannot be removed. Two posts are copies when their text is at
  least 95% the same after links, case and spacing are ignored. It looks back 30 days, inside the same listening. The
  first one stays and counts its copies ("3 copies").
- **Keep** rules: one sentence and a tag. "Someone asking how to plan or track work for a small team" → `question`.
- **Drop** rules: one sentence. "Vendors promoting themselves. Job posts."

How an item is decided:

- One call to the decision model answers every Keep and Drop rule at once, for up to ten small posts together
  (`backend/decide_groups.py`): each post is kept or dropped on its own answers only, a post whose answer is missing is
  asked again alone, and each post's cost is its share of the call. More rules do not cost more calls. Posts are asked
  one per call until grouped calls are checked against real verdicts (`GROUP` in `backend/listening.py`).
- A pass stops after 3 failed posts, so one failed call about 3 or more posts stops it; the rest wait for the next pass.
- A rule is a yes at 0.75 or more.
- An item is **kept** when at least one Keep rule is a yes and no Drop rule is. Its tag is the Keep rule with the highest
  score. Otherwise it is **dropped**.
- With no Keep rules, everything that is not a copy or a Drop is kept, with no tag.
- An edited rule applies to items read after the edit. Waiting items keep their tag.

Scoring cost is added to the run that sends the items and counts toward the automation's spend cap.

## Rate limits

A list of limits, each "up to N every hour, day, week or month". At most one per unit. All of them hold at once: they
stack. A new listening starts at 10 a day.

- **When it sends**: on the smallest unit in the list. Hour: at the top of each hour. Day: at the set time (08:00 unless
  changed). Week: Monday at that time. Month: the 1st at that time.
- **How many**: the smallest amount any limit still allows.
- **Which**: best-ranked first. Rank is the Keep score, raised a little for each copy, newest first on a tie.
- **Nothing kept, nothing sent.** There is no empty send.
- **Leftovers** wait and compete next time. An item that has waited 7 days is dropped.

Example, "3 an hour, 10 a day": it sends up to 3 each hour and stops when the day has had 10. With a full queue that
uses the day's 10 by 03:00. If that is not what you want, raise the daily limit or remove the hourly one.

A send that was missed while the server was down goes out once when it is back, not once per missed time.

## What is sent

One run of the automation, carrying text like this:

```
Market listening · Tue Nov 10, 14:00 · 3 of 30

1. competitor  Northwind Team plan is now $15 a seat
   Page · northwind.com/pricing
2. question    Which tracker works for a 6-person agency?
   Reddit · 3 copies · reddit.com/r/agency/…
3. buyer       We're 8 people and need to replace our spreadsheet this month
   Reddit · reddit.com/r/smallbusiness/…
```

An item's title is the post's own title, or the first 140 characters of its text. No model writes a summary.

The run also records the counts since the last send: read, duplicates removed, dropped, kept, sent, waiting, could not
score. The run's page shows the counts, the text that was sent and what the steps did.

## Steps and routing

The steps get the whole text. The simple case is one step: post it, or give it to a bot. A new listening starts with one
step, Post an update.

To route, use the **Paths** step. In a listening it can split what was sent:

- **By tag**: each tag's items travel together. A bot gets one turn with all of its items.
- **Each item**: every item goes alone. Use it for one task per item.

Each part takes the first path whose tag matches, or Otherwise. Untagged items travel as one group with no tag.

Sources, rules and rate limits never name a bot. Who gets what is set in the steps only.

## Market listening

One listening comes ready-made, named "Market listening":

- Keep `competitor`: "A competitor changed a price, a plan, a product or an offer".
- Keep `buyer`: "A buyer describing a problem, a need or what they pay, in their own words".
- Drop: "Vendors promoting themselves. Job posts."
- 10 a day.
- One step that gives the text to the [Librarian](librarian.md), which files what is worth keeping in Market. With no
  Librarian, the step is Post an update.

It has no sources, so it does nothing until you add one. It is made once and is yours to edit or delete; a deleted one
does not come back.

## Item states

```
new ──► duplicate
    ──► dropped
    ──► kept ──► sent
    │        └─► expired   (waited 7 days)
    ├─► failed             (could not be scored, 3 tries)
    └─► expired            (unscored for 7 days: decisions off or over the spend cap)
```

Every item reaches an end. A `sent` item names the run that carried it; a `duplicate` names the item it copies. Sent
items are kept 90 days, everything else 30.

A source is `never`, `ok`, `blocked`, `rate_limited` or `error`, set by each read.

A listening is on or off. Off means no sources are offered for reading, nothing is scored and nothing is sent. Waiting
items stay and expire as usual.

## Read and save

These are internal v2 APIs and may change between releases. Listenings are created and edited through the automations
API; there is no separate create route. Every POST has an `Idempotency-Key` for safe retries ([API](api.md)). The bodies
below contain fictional data.

| Call | Who | What |
|---|---|---|
| `GET /api/v2/listening/sources?due=1` (`tico_listening_sources`) | a bot holding the Listening tool | the sources the caller may read, overdue first |
| `POST /api/v2/listening/sources/{id}/items` (`tico_listening_save`) | a bot holding the Listening tool | saves one read of one source |
| `POST /api/v2/listening/in/<token>` | whoever has a push source's address | the same body, with no sign-in |
| `GET /api/v2/listening/items?automation=&status=&limit=` | | items, for a run's page and for debugging |
| `GET /api/v2/listening/stats` | | `daily` and `recent`, built from sent items, which Market's What are buyers saying? draws |

A read is saved as its status and up to 200 items:

```json
{"status": "ok",
 "items": [{"native_id": "example-1", "url": "https://example.com/posts/1", "author": "sam",
            "title": "Welcome guides for small teams", "text": "How can a small team write a helpful welcome guide?",
            "posted_at": "2026-11-10T14:00:00Z"}]}
```

`status` is `ok`, `blocked`, `rate_limited` or `error`. An `ok` read with no items means the source had nothing. An item
is known by its `native_id` inside its listening; saving it again leaves the first one as it was. `text` holds at most
20,000 characters. A save is refused while the listening is off.

A push address takes the same body. The token is the right to post: an unknown token gets 404, and a listening that is
off gets 409. A source accepts at most 200 items a call and 60 calls a minute. Tico keeps only a hash of the token, so the
address is shown once; **New address** replaces it and stops the old one.

In the automation, the listening is the trigger:

```json
{"type": "listening",
 "sources": [{"id": "s1", "kind": "search", "site": "reddit", "target": "project tracking small team"},
             {"id": "s2", "kind": "page", "target": "https://northwind.com/pricing"},
             {"id": "s3", "kind": "push", "site": "monitoring"}],
 "rules":   [{"do": "keep", "tag": "question", "text": "Someone asking how to plan or track work for a small team"},
             {"do": "drop", "text": "Vendors promoting themselves. Job posts."}],
 "limits":  [{"count": 3, "per": "hour"}, {"count": 10, "per": "day"}],
 "at": "08:00", "tz": "America/Los_Angeles"}
```

A tag is lower-case letters, digits and hyphens, up to 24 characters, and unique among the Keep rules. A rule's text
holds up to 300 characters. A limit's count is 1 to 100. A `paths` step may carry `"by": "tag"` or `"by": "item"`.

## Coming from the earlier Listening

Listening is now sources, rules and rate limits on an automation. The destinations form, `listening.yaml`, thresholds,
the inbox of posts and accept / reject / duplicate are gone, with their routes and tools. Old listening settings and
posts were not moved: set it up again under Listening.
