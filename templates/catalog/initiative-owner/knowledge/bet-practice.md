# Bet practice

How one strategy bet is read, checked and reported each week. The bet itself lives on the Strategy page (`hub strategy show`).
This file is the method for reading it, so each run does not re-derive it.

## What a bet is

- A bet is a decision the team has made that could turn out wrong. It has a statement, what it rules out, a why, and a
  wrong-if. An active bet has all four.
- The Strategy page holds the bets. A bet's status is `proposed`, `active` or `retired`. The check-in works on the active
  bet this seat owns. A proposed bet is reported as proposed, with no call on it.
- Goals link to the bet they serve. The bet's health is the colour of its KPI-backed goals, read from `goals_by_bet` and
  `health_by_bet` in `hub strategy show`.

## The three kinds of wrong-if

- **A KPI with a threshold.** A reading past the line trips it. The line is the threshold and the direction (above or
  below). A reading exactly on the line is not past it. Read each reading against the line yourself, with its period.
- **A market entity.** A change on a market page the bet cites. Read the entity with `hub market show <id>` and what
  changed, with its source, with `hub market delta`.
- **A dated sentence.** A claim the record cannot check yet, with a check-by date. Before the date it is "not yet
  checkable". On or after the date, say what the record shows, and say plainly when it shows nothing.

## Reading progress

- Progress is the milestones reached and the readings moved, each with its date. Effort is not progress.
- A milestone is reached when its owner or a dated source says so. A task marked done is not a milestone until its result is
  in a reading, a dated source or the plan's own record.
- One reading past a line after a run of good ones may be noise or a partial period. Say so, and name the next reading
  that would settle it.

## The three calls

- **On track.** The milestones and readings move the way the plan says, and the wrong-if has not tripped.
- **Watch.** A milestone or a reading is behind. Name the cause, and the date to look again.
- **Looks wrong.** The wrong-if has tripped, or the evidence points against what the bet rules out or claims. Quote the
  evidence with its date and source. If the wrong-if has tripped, the server has already filed the trip task to the Strategy
  Bot; otherwise tell the Strategy Bot. The owner decides whether to keep the bet, rewrite it or retire it.

A call is made from the evidence in the check-in, never from the mood of the week.

## Words

- Quote the bet's statement and wrong-if as the Strategy page has them. If the page changed since the last check-in, say
  so first, with the version.
- The seat's title is set per company from the bet. The bet's id is the stable key. Use the id in the call log.

## Sources

- 2026-11-07: template definition, written from the bet fields the Strategy page holds (statement, rules out, why, wrong-if,
  status, goals under each bet) as the Strategy Bot's playbooks describe them. Not yet checked against a team's own Strategy page.
