# Evidence

Every substantive change cites an evidence row. Write that row first. The quote is what the source said. `our_read` is your sentence, kept apart from the quote.

A source kind is one of: reddit, x, linkedin, news, filing, site, sales-call, meeting, peec, syften, other.

An entity or edge create needs at least one evidence id. Changing `summary`, `properties`, `since`, `until` or `tier` needs one too. Adding an alias or setting `last_verified` does not.

No evidence, no write. A report without a source you can point at is `rejected` with one sentence, or `needs-human` if it matters and you cannot check it.

## Competitor versus phrase-stealer

These words come from the listening watchlist. Use them as the entity's `tier`.

- **core** — an organization a customer would hire instead of your team. `competes_with` your team.
- **lookalike** — same job, smaller or adjacent. `competes_with` your team until you verify otherwise.
- **phrase-stealer** — the name collides with something else (a ticker, a game, a common word). Not a competitor until a source shows the competing product. Do not draw `competes_with` from the name alone.
- **secondary** — real, but not the comparison set. No `competes_with` edge until a source says a customer treats them as the alternative.

A phrase-stealer that launches a competing product becomes a lookalike or core when you have the evidence, not before.
