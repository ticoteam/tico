"""Bench for spec I26: Gmail calls and wall time to read and change many messages, before and after batching.

    .venv/bin/python -m pytest connectors/mail/tests/bench_batch.py -n 0 -s -q

The fake Gmail waits LATENCY seconds on every HTTPS call (a batch call is one); all mail is synthetic. "Before" is the
one-call-per-message path: a Gmail without the batch seam for reads, and change groups of one message (one modify
each). Not collected by the normal suite (only test*.py is); test_batch.py holds the assertions CI runs.
"""

import sys, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake  # noqa: E402
from test_batch import BOX, Batches, mail  # noqa: E402

from connectors.mail import gmail as gm  # noqa: E402
from connectors.mail import __main__ as cli  # noqa: E402

LATENCY = 0.1  # seconds per HTTPS call, as the spec's bench


class Bench(Batches):
    size = 200

    def measure(self, *argv, batch=True, prepare=None):
        service = fake.FakeGmailService(mail(self.size), batch=batch, latency=LATENCY)
        self.service = service
        if self.dbfile.exists():
            self.dbfile.unlink()
        if prepare:
            prepare(service)
        trips, start = service.round_trips, time.perf_counter()
        with mock.patch.object(gm, "MODIFY_GROUP", gm.MODIFY_GROUP if batch else 1):
            rc, out, err = self.run_cli(*argv)
        self.assertEqual(rc, 0, out + err)
        changes = len(service.batch_modifies) + (len(service.modified) if not service.batch_modifies else 0)
        return service.round_trips - trips, time.perf_counter() - start, changes

    def test_bench(self):
        newest50 = [m["id"] for m in mail(50)]
        cases = [
            ("1. inbox of 50", ("inbox", "--as", "inbox", "--mailbox", BOX, "--limit", "50", "--json"), None),
            ("2. untriaged 200", ("inbox", "--as", "inbox", "--mailbox", BOX, "--untriaged", "--json"), None),
            (
                "3. archive 50 (same change)",
                ("archive", "--as", "inbox", "--mailbox", BOX, *newest50, "--json"),
                None,
            ),
            (
                "3. label 50 (same change)",
                ("label", "--as", "inbox", "--mailbox", BOX, "--json", "add", *newest50, "hub/billing"),
                lambda s: s.labels_by_id.update({"Label_billing": "hub/billing"}),
            ),
        ]
        rows = []
        for name, argv, prepare in cases:
            before = self.measure(*argv, batch=False, prepare=prepare)
            after = self.measure(*argv, batch=True, prepare=prepare)
            rows.append((name, before, after))
        print(f"\nfake Gmail, {LATENCY * 1000:.0f} ms per HTTPS call, READ_GROUP={gm.READ_GROUP}")
        print(
            f"{'measure':<30} {'calls before':>12} {'s before':>9} {'calls after':>12} {'s after':>8} {'change calls':>13}"
        )
        for name, (c0, t0, m0), (c1, t1, m1) in rows:
            print(f"{name:<30} {c0:>12} {t0:>9.2f} {c1:>12} {t1:>8.2f} {f'{m0} -> {m1}':>13}")


del Batches  # only the bench runs here
