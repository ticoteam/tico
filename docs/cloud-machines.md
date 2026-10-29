# Cloud machines

On Tico Cloud each bot can have a computer of its own: a boat sandbox that Tico HQ starts, stops and deletes for the
server (`backend/cloud_machines.py` decides when; HQ talks to boat). This page is for whoever builds the machine
image: how the base snapshot is made and refreshed, and how a new machine becomes that bot's runner.

## How a machine becomes the bot's runner

1. The server mints a one-time enrollment code for the bot (15 minutes, single use; only its digest is kept, in
   `enrollments` and `cloud_machine_codes`, with the bot it binds to) and asks HQ for a machine.
2. HQ runs the equivalent of `boat new --from <HQ_BOAT_BASE_SNAPSHOT>` (default `tico-runner-base`) with two
   per-sandbox variables: `TICO_ENROLL_TOKEN` (that code) and `TICO_HUB_URL` (the server).
3. At boot, `tico-cloud-machine.service` (`python -m runner.cloud_machine boot`) finds the two variables, enrolls the
   runner once with `tico-runner enroll`, and runs `scripts/tico install`, which starts the runner and its helper jobs
   as systemd user units.
4. The server sees a cloud code: it names the computer "&lt;bot&gt; (cloud)", closes it to every other bot
   (`accepts_member_bots=0`, and placement never offers one bot's machine to another) and assigns the bot to it.
5. Later boots (a resume after an idle stop) find `~/.config/tico/runner.json` and only start the units.

A retried enroll with the same code returns the same runner, so a boot that lost the server's answer just tries
again (systemd reruns a failed boot step every 30 seconds). Without the marker `/etc/tico/cloud-machine.json` the
boot step does nothing, and without a code it does nothing either: that is the base sandbox itself.

The code never reaches a log or a command line. The enroll reads it from a 0600 file that is removed right after;
printed lines have it masked; the runner's own commands start with a clean environment; and once the runner is
enrolled, every line that sets `TICO_ENROLL_TOKEN` is taken out of `/etc/environment`, `~/.bashrc`, `~/.profile` and
the file `TICO_BOOT_ENV_FILE` names, at every boot. A code that survives somewhere still enrolls nothing: it is used
and expires in 15 minutes.

## The runner on the machine

The machine runs the native Linux runner from a checkout of the release (docs/install.md, "A Linux checkout"), as
the sandbox's own user `user`, not the Docker runner. One machine holds one bot, so the machine is one trust group,
and the bot may use `sudo` there: `apt-get install`, services, Docker. The Docker runner drops bot code to a user with
no capabilities, where `sudo` can never work. The base step writes `/etc/sudoers.d/tico-runner`
(`user ALL=(ALL) NOPASSWD:ALL`); no other install path writes it, so no other computer gets it. boat already gives
`user` passwordless sudo; the file keeps that true if boat's default changes.

The runner follows the server's release by itself, like any checkout runner (docs/updates.md), so a machine made from
an older base updates on its first heartbeat. Refreshing the base per release only saves that first update.

## Build the base snapshot

Once per release, from a computer with the boat CLI signed in to the account HQ uses. `--no-env` keeps the account's
stored secrets out of the sandbox, and so out of the snapshot. Never pass `-e` secrets to the build sandbox: a saved
snapshot hands its variables to every machine made from it.

```sh
V=v0.5.7   # the release the servers run
id=$(boat new --no-env --name "tico-runner-base-$V" --json | jq -r 'select(.event == "ready") | .id')
boat exec "$id" --timeout 1200 -- bash -lc "
  set -euo pipefail
  git clone --quiet --branch $V https://github.com/ticoteam/tico.git ~/tico
  cd ~/tico && sudo python3 -m runner.cloud_machine base --user user"
# not enrolled, service enabled, linger on
boat exec "$id" -- bash -lc 'test ! -e ~/.config/tico/runner.json && systemctl is-enabled tico-cloud-machine.service && loginctl show-user user -p Linger'
boat snapshot "$id" "tico-runner-base-${V#v}"   # a fixed version to pin or roll back to
boat snapshot "$id" tico-runner-base            # the name HQ uses by default; reusing a name replaces it
boat delete "$id"
```

`base` refuses a computer that is already enrolled (every machine made from it would be that one runner) and a
checkout that is not on a release tag. It builds the venv and the release's Rust runner as `user` (the same steps as
`scripts/setup-runner.sh`), writes the sudoers file, turns on lingering so the user's systemd starts at boot, writes
the marker and enables the boot service.

To pin HQ to one build, set `HQ_BOAT_BASE_SNAPSHOT=tico-runner-base-0.5.7` on HQ; to roll back, point it at the
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
- `systemctl --user status tico-bot` is active, and the computer shows as "&lt;bot&gt; (cloud)" in Settings >
  Computers.
- `test ! -e /.dockerenv`: the runner takes a machine with that file for a container and would not update itself.
- HQ creates the machines without the account's stored secrets (the API's equivalent of `--no-env`); otherwise every
  bot machine gets them.
