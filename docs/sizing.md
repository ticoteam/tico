# Sizing

Two things to size: the server (small, and stays small) and the computers that run bots (where the
capacity is).

## The server

**1 to 2 GB of RAM and 1 to 2 vCPUs has been enough for a small pilot.** Larger numbers are untested. The server holds state in
SQLite, replicated by Litestream, and runs no bot work; its only model calls are decision questions, when those are on. Give it 20 GB or more of disk
for the database, files and Docker images, and more if you store many meeting recordings or
attachments (or point blob storage at an S3 bucket).

Consider a bigger server only if you see it: sustained high CPU during busy hours, heavy file or
transcript ingest, or the database growing into many gigabytes. Then move up a size first (memory helps
SQLite's cache) before considering anything more complex. A single server is the design; back it up
rather than clustering it.

## Computers

Bots use computers only while a run is going.

- **Memory:** budget roughly 0.5 to 1 GB of RAM per bot working at the same time. What matters is
  concurrent runs, not the number of bots. Most bots are idle most of the time.
- **CPU:** bursty. A harness mostly waits on the model, but builds, tests and package installs spike
  it. 2 vCPUs per 4 to 6 concurrent bots is a fair start.
- **Disk:** each bot's repository plus its dependencies and caches. Plan 2 to 10 GB per active bot, more
  for large codebases, and about 20 GB for the OS, tools and harnesses.

Concurrency is the number to estimate: how many bots will realistically be mid-run at once (often
20 to 30 percent of the total in office hours).

## Examples

| Bots | Concurrent (about) | Server | Computers |
|---|---|---|---|
| 20 | 4 to 6 | 1 GB, 1 vCPU | One computer with 8 GB RAM and 4 vCPUs (a Mac mini or a 4 vCPU cloud VM), 100 GB disk |
| 100 | 20 to 30 | 2 GB, 2 vCPUs | Three or four computers of 16 GB and 4 to 8 vCPUs each, 250 GB disk each |
| 300 | 60 to 90 | 2 GB, 2 vCPUs (4 GB if uploads are heavy) | Eight to twelve computers of 16 GB, or five or six of 32 GB, 250 to 500 GB disk each |

Only the smallest row resembles anything measured. The 100 and 300 rows are estimates and untested. They are starting points. Watch memory on the computers and add another when they run hot. Each computer's **Performance** page (Settings > Computers, open the computer, Performance) shows its CPU and memory over the last 14 days, the overloads it had and which bots were running then.

## Adding computers and spreading bots

Add a computer from **Settings > Computers > Add computer** (a Mac runs the printed `tico` commands; a
Linux computer runs `python3 -m setup runner` or starts the `tico-runner` image with the one-time code). Then
assign bots to it in each bot's settings. Spread by:

- **Trust:** bots that read untrusted email or web pages on separate computers from bots that hold
  powerful credentials ([security model](../SECURITY.md)).
- **Load:** move heavy build-and-test bots apart from light chat and research bots.
- **Harness:** install only the harnesses that computer's bots need.

Bot repositories live in Git, so moving a bot is a reassignment plus a pull on the new computer.

## Mac or Linux

| | Mac (native runner) | Linux or cloud (`tico-runner`) |
|---|---|---|
| Availability | A laptop sleeps, closes its lid and travels; a Mac mini or Studio on power is fine | Always on |
| Bots that need | Desktop apps, a human's files, local sign-ins, browsers | Servers, builds, headless work |
| Cost and scale | You own the hardware; capacity is one computer | Elastic; add or remove VMs |
| Setup | Native runner, exactly as long-time users run it | Container image, `python3 -m setup runner` |

Work sent to a sleeping computer waits until it wakes, so use always-on computers for routines and
anything time-sensitive, and keep laptops for bots that work with the human carrying them. Many
teams use both: Linux for the bulk, a Mac for the few bots that need macOS.
