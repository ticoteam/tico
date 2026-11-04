"""The screening eval's replay (evals/screening): the corpus through every door, mode and switch, holds released by
handing over, fail-open, the cache and the settings, on the redesign org through the real API. Deterministic and offline
(recordings, else each input's label), about 20 seconds. It runs in its own process: the eval sets the inbound env and
idles the mail sync at import, which must not leak into the tests sharing this worker."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_screening_eval_replay_passes():
    done = subprocess.run([sys.executable, "-W", "ignore", "-m", "evals.screening.run"], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    report = "\n".join(line for line in done.stdout.splitlines() if not line.startswith("tico: "))
    assert done.returncode == 0, report + "\n" + done.stderr[-2000:]
    assert " 0 failed" in report and "FAIL" not in report, report
