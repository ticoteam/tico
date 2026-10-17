# Golden corpus for content rewriting

Synthetic team files (an invented laundry business, Brightwater Laundry Co.) passed through every path that
rewrites content: the Librarian's wording repair, the display-name annotation of API answers and the file
storage round trip (`backend/tests/test_golden_corpus.py`). No real customer data.

- `kept/`: a team's own words. Every path returns each file byte for byte. They are full of ordinary words that
  older rewriters changed (company, coworkers, machine, the runner, hub, instructions) and of Tico's terms inside
  code, links, quotes and blockquotes, where they are the team's literal text.
- `rewritten/`: Tico's own terms in Librarian prose. `<name>` goes in, `<name>.expected` is the one right answer.

Add a file when a rewrite surprises someone: the input as the team wrote it, and (only in `rewritten/`) what the
Librarian should make of it.
