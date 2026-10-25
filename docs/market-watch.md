# Market watch

Market watch reads the pages of the organizations on your [market map](strategy.md) and tells the Librarian when one
changes. It is for the pages where a change is news: pricing, changelog, careers, docs and the home page.

[Strategy](strategy.md) · [Librarian](librarian.md) · [Weekly briefing](market-briefing.md) · [System routines](system-routines.md)

## What it does

- Once a day the server reads each watched page. No model is called.
- The first read is a baseline and files nothing.
- A later read that differs from the baseline by at least 40 characters files three things: a piece of evidence (source kind
  `page-change`, the added and removed lines as its quote), a market insight for the Librarian to curate, and an event on
  the entity. The baseline moves only when a change is filed, so small edits add up.
- A change alone never trips a bet: it may be layout or wording. When the Librarian applies the insight to the entity, a
  bet that cites the entity, or whose thesis wrong-if watches it, trips the fit check like any other market change
  ([Strategy](strategy.md#the-fit-check)). A rejected insight trips nothing.
- Lines that are only numbers, dates or a copyright notice are ignored, so a changing year files nothing.
- Pages are read on the server through the same guard as `hub doc fetch`: public addresses only, every redirect checked,
  5 MB and 20 seconds. A page that cannot be read is marked `error` or `blocked` on its watch; the others still run.

Watches are never deleted. Stopping one ends it, and the same page can be watched again.

## Who can watch

The owner and the Librarian add and stop watches. Everyone who can read the market sees the list. The Librarian adds
watches for each core competitor while it sets up the first map ([playbook](../templates/catalog/librarian/playbooks/market-setup.md)).

| Limit | Value |
|---|---|
| Watched pages at once | 200 |
| Pages read a day | 50, the longest unchecked first |
| Page kinds | pricing, careers, changelog, docs, home, other |
| Smallest change filed | 40 characters |

## Commands

| Command | Does |
|---|---|
| `hub market watch add <entity id> <url> [--kind pricing]` | Watch a page of a market entity |
| `hub market watch list [--entity <id>]` | The watched pages, with when each was checked and changed |
| `hub market watch stop <id>` | Stop watching a page |
| `hub market watch check` | Read the watched pages now, in the background |

The same four are hub tools (`hub_market_watch_add`, `hub_market_watch_list`, `hub_market_watch_stop`,
`hub_market_watch_check`).

## API

| Route | Does |
|---|---|
| `GET /api/v2/market/watches` | The watches (`entity_id` filters). Market readers |
| `POST /api/v2/market/watches` | `{entity_id, url, kind}`. Owner or Librarian. 422 for an internal address, 409 when already watched or at the limit |
| `POST /api/v2/market/watches/{id}/stop` | Stop it. Owner or Librarian |
| `POST /api/v2/market/watches/check` | Read now; returns 202. Owner or Librarian |

The daily run appears in **Settings > Routines** as Page monitors ([System routines](system-routines.md)). It does not
run on a test or rehearsal server.
