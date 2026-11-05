# Cloud machines

On Tico Cloud a bot can run on a cloud machine: a boat sandbox that Tico HQ starts, stops and deletes for the
server (`backend/cloud_machines.py` decides when; HQ talks to boat). This page is for whoever builds the machine
image: how the base snapshot is made and refreshed, and how a new machine becomes a runner.

Machine status follows live runner contact: a runner seen within the normal online window shows **Running** when
HQ's last machine record still says **Starting**. **Stopping**, **Stopped**, **Deleted** and **Failed** stay as recorded,
even while a runner still checks in. Enrollment and subsequent heartbeats finish a
pending `starting` transition, so the next heartbeat repairs an old record too. A heartbeat does not cancel a stop
already reserved by the scheduler.

A stopped machine stays asleep while its queued tasks wait for a person, a snooze, a prerequisite or a check.
Health and Computers show those waits. With no other reason, Computers says **asleep; starts when work can run**.
Samples and load from the last boot do not mean an asleep machine is failing to report. They still hold new starts
once it is running, but raise no load warning after a planned stop, even with recent runner contact. Last-boot
readiness can wake the machine for a fresh report too; stale samples and memory holds do not block that wake.
Health ages the next job that can run past those last-boot holds, so old task waits do not age newly queued work.
Work that can run but has waited over 15 minutes for the machine to start is a Health problem.

## Shared or own

A bot in cloud mode is either on the company's **shared** cloud machine or on its **own**. Light bots (marketing,
ops, most roles) share one machine per company, so a company pays for one machine and its starts, not one per bot.
Heavy bots get their own: a bot made from the `video-producer` template, or a Builder (made from the `builder` template, a copy of one, or named "Builder", "Builder 2" or "Builder (...)"), starts on its own machine; every other bot starts on the shared one, the rest of Engineering included
(leads, reviewers and QA run short turns). That default is read once, when the
bot first gets a cloud machine row; after that a person switches it in the bot editor ("Shared cloud machine" / "Own
cloud machine" under Computer).

- The shared machine is one row and one HQ machine, key `_shared` (a bot's own machine is keyed by the bot id, and no
  bot id has an underscore). It is on while any bot on it wants it (work, a move, a watcher's wake) and pauses once
  every bot on it has had no turn out and no activity for its Pause after (`pause_minutes`: 30 by default, whole
  minutes from 5 to 1440; Always on never pauses). Its size, Pause after and Always on are the
  company's, set by owners and admins (`POST /api/v2/cloud-machines/shared`, which takes `pause_minutes` too). They can
  also delete it, to replace a broken one (`DELETE /api/v2/cloud-machines/shared`): HQ deletes its disk, its computer is revoked, its size, Always on
  and members stay, and a member's next work makes a fresh one. The row keeps the old id as `deleted`: HQ keeps the
  machine live until boat confirms, and a create meanwhile answers with it, which is not taken for the new one. A bot's own machine is deleted once the bot runs
  elsewhere (`DELETE /api/v2/bots/{bot}/cloud-machine`).
  To replace it while the bot stays on its own cloud machine, use **Replace machine** under Computer in the bot editor,
  or `POST /api/v2/bots/{bot}/cloud-machine/replace` with an `Idempotency-Key`: it uses the same deletion, keeps size, Always on, Pause after and placement, and refuses while a turn is running (409).
  The next work makes a fresh machine; its first enrollment gets the company plan under the admin placement rule below.
- Its computer is "Shared cloud machine", owned by the company owner and open to member bots: a bot that joins is
  assigned to it, one that leaves (to its own machine or a computer) is unassigned, and the machine keeps running.
  When the last bot leaves it idles to a stop like any other.
- A bot's own machine pauses by the same rule, with its own Pause after (`pause_minutes` on `POST /api/v2/bots/{bot}/cloud-machine`).
- A new cloud machine gets the company's plan when an admin moves a bot onto it. Before this, the bot waited on "login required" until someone
  signed in to the machine by hand. The machine takes a plan only when all of these hold: the machine is that bot's own cloud
  machine; the admin's choice put the bot there, either by moving it or by the machine's first enrollment for a bot an admin placed; no other bot on that machine already runs the same tool; and the machine has no plan or sign-in of its own for
  that tool, with no run in progress. The machine skips plans that need reconnecting, plans at their limit, and API keys. It
  prefers the plan on the shared machine, then one on another cloud machine, then the plan with the lowest known usage.
  Nothing is stopped or reset. A member's moves, and any other assignment, never give a plan. When no plan fits, nothing
  changes and the bot keeps its current message and its Gateway path.
- Bots on one machine share its disk and `sudo`: they are one trust group, like bots sharing a computer today. Put a
  bot that must not see the others' files on its own machine.
- Tico models run on a cloud machine even though its bots don't run as their own user: the runner keeps the company's
  Gateway key and hands each turn only a pass for its model calls, but a bot there could still read the key from the
  runner. That is the company's own key on its own machine, a trade-off accepted for cloud machines only.
- One noisy bot cannot starve the rest past the runner's turns-at-once limit (Settings > Computers); there is no
  separate CPU accounting.
- Billing and caps are HQ's: the shared machine is one machine against the company's machine cap, whatever the
  number of bots on it.

## A stop never lands under a turn

The server decides an idle stop on a read of the hub, and a runner can claim work between that read and HQ's stop.
So before it asks HQ to stop or delete a machine it reserves the stop in a write transaction, the lock the claim also
takes: it decides again on what it reads there, and finds no turn leased or running on the machine's computer, or it
sends nothing. While the reservation holds (`cloud_machines.stop_until`, two minutes from the clock read under that
lock), and while HQ says the machine is stopping or stopped, that computer's claims get no new turn (`paused`: "This
cloud machine is stopping"), for every bot on the shared one, and its company-wide waiters do not hold other
computers. HQ saying it did not stop (a 4xx) lets the reservation go at once. An answer that leaves it open (no
answer, one that cannot be read, a 5xx, 402, or 409 `sandbox_stopping`) keeps it two more minutes and asks HQ's list
at the next look: stopping or stopped there holds the claim from then on, running lets it lapse. A server that died
mid-stop lets it lapse, and a start clears it.

All three manual deletion routes use the same path. A deletion waits for the manager's current look, including any
start or stop awaiting HQ and its final write, before it reserves the machine. While it asks HQ, the manager cannot
start or stop that machine. The final write revokes the runner on the row at that moment, including one that enrolled
during the request, and invalidates all of the machine's enrollment codes. A code cannot enroll another runner after
the disk is deleted.

Before sending any DELETE, the server stores intent for that exact machine id in `cloud_machine_deletions`.
Only HQ's own state releases this hold: the machine is deleted or 404, or HQ reports it with `pending` other than
`delete`. A plain `{ok: true}` DELETE reply never releases it, nor does a first or retry 4xx such as 429 `starts_full`.
The hold survives restarts, unreadable lists, and lists that omit the machine or its `pending` field. A deleted
machine's current runner and codes are revoked under the write lock. A report that clears `pending` ends retries
without revoking a machine kept after an unanswered deletion.

Each manager look checks up to four due intents, including old ids no bot row names anymore, even if the full list
is unreadable or sizes are no longer offered. It reads `GET machines/{id}` when HQ supports it. A failed read falls
back to a DELETE retry for that id; a DELETE 404 or a reply with `state: deleted` settles it. A GET 404 alone cannot
settle it, since older HQs have no such route. Settled intent rows are removed. A failed or oversized list only waits
for the next list attempt; it cannot block these checks or a fresh machine.

Retries use the stored machine id and back off on errors, including 429 and 5xx, up to 15 minutes. Accepted retries
wait at least a minute; out of credit waits ten minutes. Extra lists, restarts, and repeated requests cannot rush
that clock. This rule also covers the manager's deletion of a machine that never enrolled.

Changing mode or placement never clears deletion intent. A bot can move to a shared machine or another computer
while its old own machine is being deleted. If it returns to own, the old computer gets no turn and is not bound
again. The fence is only on the old id: work can create a fresh machine and run on its new id before the old intent
settles. A create that returns a fenced id is refused as a replacement and tried again later. New enrollment codes
belong to the fresh request; unused old codes cannot enroll its runner. Settling the old id leaves the new runner
and its codes intact. The codes' `replacement_for` marker keeps fresh attempts together across a lost create reply,
so a machine that enrolls before the reply is recovered keeps its runner and code, even if the old id settles first.

## How a machine becomes the bot's runner

1. The server mints a one-time enrollment code for the machine (15 minutes, single use; only its digest is kept, in
   `enrollments` and `cloud_machine_codes`, with the machine's key: the bot, or `_shared`) and asks HQ for a machine.
2. HQ runs the equivalent of `boat new --from <HQ_BOAT_BASE_SNAPSHOT>` (default `tico-runner-base`) with two
   per-sandbox variables: `TICO_ENROLL_TOKEN` (that code) and `TICO_HUB_URL` (the server).
3. At boot, `tico-cloud-machine.service` (`python -m runner.cloud_machine boot`, forwarded to runner_support/cloud_machine.py) finds the two variables, enrolls the
   runner once with `tico-runner enroll`, and runs `scripts/tico install`, which starts the runner and its helper jobs
   as systemd user units.
4. The server sees a cloud code. For a bot's own machine it names the computer "&lt;bot&gt; (cloud)", closes it to
   every other bot (`accepts_member_bots=0`, and placement never offers one bot's machine to another) and assigns
   the bot to it. For the shared machine it names it "Shared cloud machine", opens it to member bots and assigns
   every bot on it.
5. Later boots (a resume after an idle stop) find `~/.config/tico/runner.json` and only start the units.

A retried enroll with the same code returns the same runner, so a boot that lost the server's answer just tries
again (systemd reruns a failed boot step every 30 seconds). A machine has until its code expires (plus a minute) to
enroll, and is not idle-stopped meanwhile. One that has not by then never can (a resume brings no new code), so the
server deletes it and the next work makes a new one with a fresh code; after three such machines in a row it makes no
more ("Made, but it never connected to this server", in Health) until one enrolls or a person changes the machine's
settings. Without the marker `/etc/tico/cloud-machine.json` the
boot step does nothing, and without a code it does nothing either: that is the base sandbox itself.

The code never reaches a log or a command line. The enroll reads it from a 0600 file that is removed right after;
printed lines have it masked; the runner's own commands start with a clean environment; and once the runner is
enrolled, every line that sets `TICO_ENROLL_TOKEN` is taken out of `/etc/environment`, `~/.bashrc`, `~/.profile` and
the file `TICO_BOOT_ENV_FILE` names, at every boot. A code that survives somewhere still enrolls nothing: it is used
and expires in 15 minutes.

## The runner on the machine

The machine runs the native Linux runner from a checkout of the release (docs/install.md, "A Linux checkout"), as
the sandbox's own user `user`, not the Docker runner. The bots on a machine (one on its own machine, several on the
shared one) are one trust group, and they may use `sudo` there: `apt-get install`, services, Docker. The Docker runner drops bot code to a user with
no capabilities, where `sudo` can never work. The base step writes `/etc/sudoers.d/tico-runner`
(`user ALL=(ALL) NOPASSWD:ALL`); no other install path writes it, so no other computer gets it. boat already gives
`user` passwordless sudo; the file keeps that true if boat's default changes.

The runner follows the server's release by itself, like any checkout runner (docs/updates.md), so a machine made from
an older base updates on its first heartbeat. Refreshing the base per release only saves that first update.

A stopped machine whose runner is behind the server's release is started once to update. That wake waits a fixed
0-10 minutes per runner and release after the server first ran the release (`update_due`: a hash of the runner id and
release, from `releases.history`), so a release on a shared host does not start every company's machines at once
against boat's start limits. Work, watchers and Always on never wait. No stop (idle, paused, drained, last bot gone)
lands while the runner reports a fresh `updating`, except out of credit (HQ stops those itself, so a paused machine
of a frozen company stops at once too) and a person's delete.

The runner knows it is on a cloud machine by the marker `/etc/tico/cloud-machine.json` (root's, naming the runner's
user), which the base step writes. Machines made before Tico models ran here have it too, so they take Tico models, and
keep their own harnesses current ([What's on a machine](#whats-on-a-machine)), once their runner updates.

## What's on a machine

The base step (below) installs these, so every machine made from the snapshot has them:

- **Google Chrome stable**, headed (x86_64; arm64 gets the distro's Chromium, since Google ships no Linux arm64 Chrome).
- **Xvfb, x11vnc and matchbox-window-manager**: the same packages as the computer screen (`scripts/tico screen on`),
  so Chrome can run headed on a virtual display. The runner picks the display (`:99` and up); nothing to configure.
  The screen is on by default on a cloud machine, so bots get a display, and the computer's operator and admins can
  open it in Settings > Computers. Screen Off there stops people opening it; the display stays up for bots.
  `scripts/tico screen off` takes the display down.
  A bot whose browser should come from a home connection sets **Browse through** to a computer that shares its internet
  ([Share internet with bots](computers.md#share-internet-with-bots)).
- **Fonts** (`fonts-liberation`, `fonts-noto-color-emoji`).
- **Node.js 22 or newer** and npm, from NodeSource's apt repository (a newer Node already on the image is kept). The
  runner installs each harness its bots use (Claude Code, Codex, Pi, Gemini, Grok) with npm, as below.

`base` installs no harness, but a machine can still start with one on `PATH`, as old as the snapshot. Tico owns this
disk and nobody else updates such a copy, so the runner treats it as missing when a bot here wants that harness: it
installs its own copy in its tools directory (`tools/` beside `runner.json`). Turns keep using the `PATH` copy until
the new one is switched in at a moment no turn uses that harness. From then on the runner's copy wins, because the tools
directory comes first on `PATH` here (last on any other computer), and it updates like any managed install
([Updating and pinning](harnesses.md#updating-and-pinning)). A failed install is retried after 30 minutes and leaves the
`PATH` copy working. A copy too old for its provider still fails turns until the install lands. A harness no bot here
wants stays as it is, and reports `source: path`.

Every runner start on a machine checks for new releases at once, so a machine that wakes does not wait out a saved
24 hours; a check that fails is asked again after 30 minutes.

Nothing else is in the base. A bot that needs ffmpeg, a toolchain or anything else installs it itself with `sudo`; it
stays on the machine's disk across stops and resumes. On the shared machine, tools a bot installs are there for every
bot on it.

Re-running the step is safe. A failed install stops `base` with the step's output, and no marker is written.

## Build the base snapshot

Once per release, from a computer with the boat CLI signed in to the account HQ uses. `--no-env` keeps the account's
stored secrets out of the sandbox, and so out of the snapshot. Never pass `-e` secrets to the build sandbox: a saved
snapshot hands its variables to every machine made from it.
`base` also removes boat's box-scoped `export ASCII_TOKEN=` line from the user's `~/.bashrc`, so the snapshot carries no token.

```sh
V=v0.5.7   # the release the servers run
id=$(boat new --no-env --name "tico-runner-base-$V" --json | jq -r 'select(.event == "ready") | .id')
boat exec "$id" --timeout 1200 -- bash -lc "
  set -euo pipefail
  git clone --quiet --branch $V https://github.com/ticoteam/tico.git ~/tico
  cd ~/tico && sudo python3 -m runner_support.cloud_machine base --user user"
# not enrolled, service enabled, linger on
boat exec "$id" -- bash -lc 'test ! -e ~/.config/tico/runner.json && systemctl is-enabled tico-cloud-machine.service && loginctl show-user user -p Linger'
boat snapshot "$id" "tico-runner-base-$(echo "${V#v}" | tr . -)"   # a fixed version to pin or roll back to (no dots in names)
boat snapshot "$id" tico-runner-base            # the name HQ uses by default; reusing a name replaces it
boat delete "$id"
```

`base` refuses a computer that is already enrolled (every machine made from it would be that one runner) and a
checkout that is not on a release tag. It builds the venv and the release's Rust runner as `user` (the same steps as
`scripts/setup-runner.sh`), writes the sudoers file, turns on lingering so the user's systemd starts at boot, writes
the marker and enables the boot service.

To pin HQ to one build, set `HQ_BOAT_BASE_SNAPSHOT=tico-runner-base-0-5-7` on HQ; to roll back, point it at the
previous version's name. Machines that exist keep running whatever they have.

## Verify on the first live machine

The boat docs say per-sandbox variables reach `boat exec`, `boat ssh` and every shell (managed `~/.bashrc` blocks);
they do not say what a systemd service sees. The boot step therefore reads its own environment, then
`TICO_BOOT_ENV_FILE`, `/etc/environment`, `~/.bashrc` and `~/.profile` (parsed, never sourced). On the first machine
HQ makes, check:

- `journalctl -u tico-cloud-machine` says "enrolling with ... (the code from ...)": where the code was found. If it
  says "no enroll code here yet", find where boat put it (`sudo grep -rl TICO_ENROLL_TOKEN /etc /home/user`) and set
  `TICO_BOOT_ENV_FILE` in the unit, or add that place to `env_files()`.
- `grep -r TICO_ENROLL_TOKEN /etc/environment ~/.bashrc ~/.profile` finds nothing after the enroll, and again after a
  stop and resume (boat may write its block again; the next boot takes it out again).
- `systemctl --user status tico-bot` is active, and the computer shows as "&lt;bot&gt; (cloud)" (or "Shared cloud
  machine") in Settings > Computers.
- `test ! -e /.dockerenv`: the runner takes a machine with that file for a container and would not update itself.
- HQ creates the machines without the account's stored secrets (the API's equivalent of `--no-env`); otherwise every
  bot machine gets them.
