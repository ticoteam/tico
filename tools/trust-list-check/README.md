# Daily bundled trust-list check

Run this from Upgrades' **daily Tico routine**, separately from PR and release validation. It reads an
immutable Git revision, checks crates.io / PyPI, compares the published certificate bundles, and creates
or updates one internal Dev ticket for Engineering. It changes no dependencies or TLS behavior.

```sh
cargo run --locked --manifest-path tools/trust-list-check/Cargo.toml -- --repo . --ref origin/main
# Explicitly write findings through the authenticated, cloud-backed hub CLI:
cargo run --locked --manifest-path tools/trust-list-check/Cargo.toml -- --repo . --ref origin/main --apply
```

Requires Rust 1.92+, Git, curl, and (only for `--apply`) the normal authenticated `hub` CLI. No extra API key,
local Hub database, or GitHub Issues permission is used. The default is a read-only JSON report. Finding
stale dependencies is a successful check, not a failed check. Network, parse and cloud failures return
nonzero and say **incomplete**, so they cannot masquerade as an all-current result.

The inventory includes every tracked Cargo.lock, requirements*.txt and pyproject.toml in that revision.
It checks all actual webpki-roots bundles. A package that explicitly delegates to another locked
webpki-roots version is a shim; its target is checked instead. Older non-shim 0.26 releases still count.
It handles exact certifi requirements, Python dependency groups and Poetry exact-version tables. An
unresolved/nonexact certifi declaration fails explicitly rather than making an unsupported current claim.
Only stable published versions are compared; package archives are checked against registry SHA-256 hashes
and read in memory, never extracted or executed. PEM contents determine removals, not certificate counts.
Unrecognized bundle layouts fail explicitly.

An urgent ticket names the removed roots and says to **bump and ship in the next release**. Urgency appears
in the title and new urgent work goes at the top of Engineering's queue. This implements the root-removal
rule without certificate-authority exceptions. Existing urgent titles are retained. Ticket updates preserve
the owner's factory step and original description, with the complete current findings in its note. Keep
these tickets with Engineering: the search is paged over that owner's work, including snoozed tickets.
The marker at the start of the description identifies the check's ticket. A completed/closed/declined
lifecycle permits a new ticket. Writing is supported only as Upgrades' runner-lease identity on its
single assigned Unix computer, using its shared runner home (the runner's normal native execution;
do not override HOME or run in an isolated home/container). Every invocation takes the OS file lock at
`$HOME/.cache/tico-trust-list-check/upgrades.lock` before cloud discovery and holds it through the final
write. The shared home covers separate task checkouts and sandbox-private temporary directories. The lock lives outside checkouts, is released automatically on process exit, and its
file must never be deleted while a check could run. Contention waits at most 30 seconds, then reports
incomplete without writing. Cloud lease fencing rejects writes from a previous computer assignment;
external-agent, human and other-bot writers are rejected. Idempotency keys protect retries within one
attempt only. An older routine report retains earlier urgent root-removal evidence.
Identical repeated findings need no update. Multiple existing open matches stop the check for reconciliation.
Never retry a refused Hub write inside a routine; retain the error's operation id and ask on its owning task.

## Schedule setup (operator action after review/merge)

Creating or changing a routine requires a person's approval. No routine is armed by building this tool.
The operator should create the following routine on Upgrades, preserving its existing upgrade routines:

- Key: `bundled-trust-list-currency`
- Title: `Check bundled certificate lists`
- Cron: `23 8 * * *`, timezone `Etc/UTC` (daily at 08:23 UTC)
- Text:

> Check the bundled certificate lists in the latest main of ticoteam/tico once. Use your own clean
> managed checkout; fetch origin/main and fast-forward your clean checkout to it before building the
> helper, so the helper and scanned sources are from current main. Run cargo run --locked
> --manifest-path tools/trust-list-check/Cargo.toml -- --repo . --ref origin/main --apply.
> Run cargo through scripts/heavy.sh and keep long work detached as required by your working rules.
> Use the shared cloud Hub CLI only. Keep one open Engineering ticket; the helper handles creation,
> updates, removed-root names and urgent next-release wording. Read its complete result. If the scan
> or a write fails, report incomplete and preserve the operation id; never call a failed scan current,
> retry a refused write, create a second ticket manually, or close an Engineering ticket. Do not add
> this command to PR checks, required checks or release gates. Save the scan receipt in your repository.

An operator who owns Upgrades can schedule it with `hub automation create --file flow.json`, where the flow
has a `schedule` trigger (`"cron": "23 8 * * *"`, `"tz": "Etc/UTC"`) and one `bot_turn` step for `bot:upgrades`
whose `prompt` is that text. Follow the team's approval policy before that write. Afterwards verify the saved
schedule with `hub automation show <id>` and run the first check with `hub automation run <id>`. An ordinary
scheduled finding does not run or fail any PR workflow; no workflow or required-check setting is changed.

## Focused verification

```sh
scripts/heavy.sh cargo test --locked --manifest-path tools/trust-list-check/Cargo.toml
```

Synthetic registries and an in-memory CLI fixture cover stale/current lists, repeated findings on a
later cloud page, urgent root removal, compatibility shims, multiple versions, dependency formats,
registry/refusal failure and same-attempt create idempotency. A shared synthetic cloud contract starts
two distinct attempts while the first is paused after discovery: the second must wait, then update the
one ticket with urgent root evidence. A third attempt after completion creates one new lifecycle. These tests never touch a live Hub database or file test
tickets. Live checks use the read-only mode first. The all-current live acceptance case can only be
recorded when main's pins really are current; do not change main merely to manufacture that receipt.
