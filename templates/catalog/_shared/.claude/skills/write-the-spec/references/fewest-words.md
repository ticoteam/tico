# The fewest words

Use it in step 7, on a finished draft. The aim is not a short page. The aim is a page where nothing has to be guessed and nothing has to be skipped. Cut first, then put back anything whose absence makes a reader guess.

## Replace words with something better

| If the text does this | Use this instead |
|---|---|
| Describes what a screen looks like | A screenshot with numbered arrows |
| Compares options, plans, roles or before and after | A table |
| States a rule with exceptions | Two examples: one that passes, one that does not |
| Lists steps in a paragraph | A numbered list |
| Explains a calculation | One worked example with real numbers |
| Describes a flow between screens or systems | The screens in order, each with its arrow, or a short numbered list of who sends what to whom |
| Says how big a problem is with adjectives | The number, what it is out of, the source and the date |
| Describes what a customer feels | Their sentence, copied exactly, with its evidence id |

## Cut these

- **Background and history.** How the team got here, what was tried, who suggested it. If a past decision matters, link the ledger row.
- **Anything that restates the top.** No overview, summary, introduction or conclusion.
- **Sentences that fit another spec.** "This will improve the user experience." "We want this to be easy to use." Test: swap the feature's name for another; if the sentence still reads true, cut it.
- **Benefits.** The goal says who can do what. The reader does not need selling.
- **Hedges.** "Should", "ideally", "where possible", "may want to", "as needed". Decide: it does, or it does not.
- **Adjectives without a number.** Fast, simple, easy, clean, modern, intuitive. Give the time, the number of taps or the named check, or delete the word.
- **Implementation the builder will decide better.** Name the behaviour and the files it touches. Do not write the code in prose.
- **Options.** A spec states what will be built. Options that lost are in the ideas page and the ledger.
- **Thanks, apologies and commentary.** "As discussed", "note that", "it is worth mentioning".

Also refused outright by `page.py check`: "seamless", "leverage", "robust", "delightful", "cutting-edge", "best-in-class", and any request that the reader test or check something themselves.

## Keep these, however long they make it

- Every touched use case and its ending.
- Every number's source, date and denominator.
- Every Out with its reason.
- Every assumption, labelled.
- The exact words that will appear on the screen (labels, messages, emails). These are part of the design; write them in full.
- The exact limits (lengths, counts, times).

## Sentence rules

- One idea per sentence. Split at "and" when the two halves could be checked separately.
- Subject first, then verb: "The invoice opens", not "Upon completion, opening of the invoice occurs".
- Present tense for what the product does after the change: "Send saves the invoice", not "will save".
- One term for one thing. Pick the customer's word and use it on every line. If "job" is the word, it is never also "booking", "visit" and "appointment".
- Name the actor. "The tradesperson taps Send", not "Send is tapped".
- Numbers as numerals with units. Dates as full dates.
- Screen names and button labels exactly as the app shows them.

## Before and after

Before (74 words):

> In order to improve the invoicing experience for our users, we would like to introduce a more seamless way of generating invoices once a job has been completed. Currently users need to navigate to a separate area to create an invoice, which can be time consuming. Ideally the new flow should make it quick and easy to send an invoice straight away, while still allowing for flexibility where needed.

After (28 words, plus a picture):

> **Goal:** A tradesperson who marks a job done can send its invoice with one tap, before leaving the site.
> Today: Mark done returns to the job list (arrow 1).

What was lost: nothing a builder needs. What was gained: who, what, when, and the one behaviour that changes.

## The last pass

Read the page aloud, or as if aloud. Stop at every place you would say "that is" or "in other words" to a listener. That sentence was not clear the first time: rewrite the sentence, do not add the explanation.
