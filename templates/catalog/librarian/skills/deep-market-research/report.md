# The research report's shape

Written with `hub doc write _librarian/research/<slug>.md --title "<question>" --body-file <file.md> --note "Round <n>"`,
rewritten whole each round. Plain sentences. Every sentence that says something about the market ends with its source as a link:
`[example.com](https://example.com/page)`. A count says what it counts: "31 of 164 reviews". An assumption is
labelled as one in the same sentence. Leave out a section with nothing in it, except Open questions.

```markdown
# <The question>

<Date> · Deep research by the Librarian · <rounds> rounds · <n> sources read · status: <in progress | done>

## Answer
Two to five sentences: the answer to the question, the strongest evidence for it, and how sure it is.

## What we read
| Kind | How many | Where | Dates |
(reviews, threads, rival sites, filings and calls, statistics, other; a last row for what's missing)

## Findings
Short sections in the order that answers the question: the players, the buyers, the numbers, the channels.
Each claim cited.

## What the industry takes for granted
| Belief | Who acts on it | Evidence | False if |
Six to ten. "False if" names something that could be seen or tested.

## What winners know that buyers won't say
Two or three, each with its evidence. Only what the sources show.

## Pains nobody fixes
| Pain | How often | Who has it | Who fixes it today |

## Investors hear one thing, buyers another
Where an incumbent's investor words and buyer words disagree, with both quotes. Leave out if none.

## Where sources disagree
Each disagreement with both sources and their dates. Neither is called right.

## What would change the answer
The one or two facts that, if wrong, flip the Answer, and how to check them (often: talk to five buyers).

## Open questions
What we could not find or read, and who could tell us fastest.
```

The Strategy Bot's Industry beliefs lens reads the beliefs, winners and pains sections, so keep their shape.
