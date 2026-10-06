"""A computer whose Docker answers but cannot start a container is reported, not silently ready."""
import subprocess
import threading
import types
import unittest
from unittest import mock


from backend.models import StructuredReadiness
from runner import container_probe

IMAGES = ("sha256:" + "b" * 64 + " node:20\n" + "sha256:" + "c" * 64 + " <none>:<none>\n"
          + "sha256:" + "a" * 64 + " ghcr.io/ticoteam/tico:latest\n")
FAIL = {"ok": False, "seconds": 20.0, "error": "a container did not start within 20 s", "checked_at": "t"}
PASS = {"ok": True, "seconds": 1.0, "error": "", "checked_at": "t"}


class SyncThread:
    """Runs a cleanup thread's work at start(), so the test sees it without waiting."""

    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


class Probe(unittest.TestCase):
    def probe(self, images=IMAGES, run=0, hang=None, listing=0, leftovers="", stderr="docker: Error response from daemon: no space left"):
        calls = []

        def run_(args, **kwargs):
            calls.append(args)
            if args[1] in (hang or ()):
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            if args[1] == "images":
                return subprocess.CompletedProcess(args, listing, images, "Cannot connect to the Docker daemon")
            if args[1] == "ps":
                return subprocess.CompletedProcess(args, 0, leftovers, "")
            if args[1] == "run":
                return subprocess.CompletedProcess(args, run, "", stderr)
            return subprocess.CompletedProcess(args, 0, "", "")
        with mock.patch.object(container_probe.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(container_probe.subprocess, "run", side_effect=run_), \
                mock.patch.object(container_probe, "threading", types.SimpleNamespace(Thread=SyncThread)):
            return container_probe.probe(), calls

    @staticmethod
    def call(calls, verb):
        return next(args for args in calls if args[1] == verb)

    def test_a_cached_image_starts_offline_labelled_and_never_pulls(self):
        result, calls = self.probe()
        self.assertTrue(result["ok"])
        run = self.call(calls, "run")
        self.assertEqual(run[-1], "sha256:" + "a" * 64)    # by ID: a name could be pulled
        self.assertIn("--rm", run)
        self.assertNotIn("--pull", run)          # Docker before 20.10 refuses it; a listed image is never pulled
        for flag in (["--network", "none"], ["--label", "tico.probe=1"]):
            self.assertTrue(any(run[i:i + 2] == flag for i in range(len(run))), flag)
        StructuredReadiness.model_validate({"container_exec": result})     # the server takes the report
        with mock.patch.dict(container_probe.os.environ, {container_probe.IMAGE_ENV: "busybox:latest"}):
            self.assertIsNone(self.probe()[0])   # a named image that is not here would be pulled: skip

    def test_leftover_probe_containers_are_removed_with_their_volumes_first(self):
        _, calls = self.probe(leftovers="abc\ndef\n")
        verbs = [args[1] for args in calls]
        self.assertEqual(self.call(calls, "ps")[-2:], ["--filter", "label=tico.probe=1"])
        self.assertEqual(self.call(calls, "rm")[2:], ["-fv", "abc", "def"])
        self.assertLess(verbs.index("rm"), verbs.index("run"))

    def test_a_hung_start_is_reported_and_its_container_removed(self):
        result, calls = self.probe(hang=("run",))
        self.assertEqual((result["ok"], result["error"]), (False, "a container did not start within 20 s"))
        name = self.call(calls, "run")[self.call(calls, "run").index("--name") + 1]
        self.assertEqual([args[2:] for args in calls if args[1] == "rm"], [["-fv", name]])

    def test_a_hung_listing_or_a_failed_start_is_reported(self):
        result, calls = self.probe(hang=("images",))
        self.assertEqual((result["ok"], result["error"]), (False, "docker did not answer within 10 s"))
        self.assertNotIn("run", [args[1] for args in calls])
        result, _ = self.probe(run=125)
        self.assertFalse(result["ok"])
        self.assertIn("no space left", result["error"])

    def test_nothing_is_reported_without_docker_a_daemon_or_an_image(self):
        self.assertIsNone(self.probe(listing=1)[0])
        self.assertIsNone(self.probe(images="")[0])
        with mock.patch.object(container_probe.shutil, "which", return_value=None):
            self.assertIsNone(container_probe.probe())


class Schedule(unittest.TestCase):
    def schedule(self, *results, **kwargs):
        results = list(results)
        seen = []
        probe = container_probe.ContainerProbe(check=lambda: seen.append(1) or results.pop(0), enabled=True, **kwargs)
        return probe, seen

    def later(self, probe, seconds):
        return mock.patch.object(container_probe.time, "monotonic", return_value=probe.at + seconds)

    def test_only_two_failures_in_a_row_are_reported(self):
        probe, seen = self.schedule(FAIL, PASS, FAIL, FAIL, FAIL, PASS, background=False)
        self.assertEqual(probe.report(), PASS)          # a slow first start, then a good one: no warning
        with self.later(probe, container_probe.EVERY_S):
            self.assertEqual(probe.report(), FAIL)      # failed, and failed again at once
        with self.later(probe, container_probe.FAILING_EVERY_S):
            self.assertEqual(probe.report(), FAIL)      # still failing: one probe, sooner than usual
        with self.later(probe, container_probe.FAILING_EVERY_S):
            self.assertEqual(probe.report(), PASS)      # Docker recovered: the warning clears
        self.assertEqual(len(seen), 6)

    def test_probes_run_in_the_background_at_most_every_fifteen_minutes(self):
        release = threading.Event()
        probe, seen = self.schedule(PASS, PASS)
        probe.check = lambda: release.wait(5) and (seen.append(1) or PASS)
        self.assertIsNone(probe.report())               # the heartbeat does not wait for a probe
        release.set()
        probe.thread.join(5)
        self.assertEqual(probe.report(), PASS)
        with self.later(probe, container_probe.FAILING_EVERY_S):
            probe.report()                              # not due yet: nothing failed
        with self.later(probe, container_probe.EVERY_S):
            probe.report()
            probe.thread.join(5)
        self.assertEqual(len(seen), 2)

    def test_the_test_suite_never_starts_containers(self):
        self.assertTrue(container_probe.OFF)
        probe = container_probe.ContainerProbe(check=lambda: self.fail("probed"))
        self.assertIsNone(probe.report())

if __name__ == "__main__":
    unittest.main()
