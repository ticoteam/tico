"""A computer whose Docker answers but cannot start a container is reported, not silently ready."""
import subprocess
import threading
import time
import unittest
from unittest import mock

from pydantic import ValidationError

from backend.models import StructuredReadiness
from runner import container_probe


def fake(images="ghcr.io/ticoteam/tico:latest\nnode:20\n", run=0, hang=False, listing=0):
    calls = []

    def run_(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "ls"]:
            return subprocess.CompletedProcess(args, listing, images, "Cannot connect to the Docker daemon")
        if args[1] == "run":
            if hang:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, run, "", "docker: Error response from daemon: no space left")
        return subprocess.CompletedProcess(args, 0, "", "")
    return calls, run_


class Probe(unittest.TestCase):
    def probe(self, **kwargs):
        calls, run = fake(**kwargs)
        with mock.patch.object(container_probe.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(container_probe.subprocess, "run", side_effect=run):
            return container_probe.probe(), calls

    def test_a_cached_image_starts_offline_without_a_pull(self):
        result, calls = self.probe()
        self.assertTrue(result["ok"])
        run = next(args for args in calls if args[1] == "run")
        self.assertEqual(run[-1], "ghcr.io/ticoteam/tico:latest")
        for flag in (["--network", "none"], ["--pull", "never"], ["--rm"]):
            self.assertTrue(any(run[i:i + len(flag)] == flag for i in range(len(run))), flag)
        StructuredReadiness.model_validate({"container_exec": result})     # the server takes the report

    def test_a_hung_or_failed_start_is_reported(self):
        result, calls = self.probe(hang=True)
        self.assertEqual((result["ok"], result["error"]), (False, "a container did not start within 20 s"))
        result, _ = self.probe(run=125)
        self.assertFalse(result["ok"])
        self.assertIn("no space left", result["error"])

    def test_nothing_is_reported_without_docker_a_daemon_or_an_image(self):
        self.assertIsNone(self.probe(listing=1)[0])
        self.assertIsNone(self.probe(images="")[0])
        with mock.patch.object(container_probe.shutil, "which", return_value=None):
            self.assertIsNone(container_probe.probe())

    def test_probes_run_in_the_background_at_most_every_few_minutes(self):
        seen, release = [], threading.Event()
        probe = container_probe.ContainerProbe(check=lambda: release.wait(5) and (seen.append(1) or {"ok": True}))
        self.assertIsNone(probe.report())                 # the heartbeat does not wait for a probe
        release.set()
        for _ in range(100):
            if not probe.running:
                break
            time.sleep(0.01)
        self.assertEqual(probe.report(), {"ok": True})    # not probed again yet
        with mock.patch.object(container_probe.time, "monotonic", return_value=probe.at + container_probe.EVERY_S):
            probe.report()
        for _ in range(100):
            if len(seen) == 2:
                break
            time.sleep(0.01)
        self.assertEqual(len(seen), 2)

    def test_the_contract_bounds_the_report(self):
        with self.assertRaises(ValidationError):
            StructuredReadiness.model_validate({"container_exec": {"ok": "yes", "seconds": 1, "checked_at": "x"}})
        self.assertNotIn("container_exec", StructuredReadiness().model_dump())


if __name__ == "__main__":
    unittest.main()
