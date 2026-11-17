"""A granted secret never leaves the runner's Python helpers as text (runner_support/redact.py)."""

import base64
import unittest
from urllib.parse import quote

import pytest

from runner_support import redact

SECRET = "jira-token/ABC def+123=="
TAIL = "AtlassianApiToken12345"
BASIC = "ana@acme.example:" + TAIL


class Scrub(unittest.TestCase):
    def test_a_secret_is_replaced_in_the_forms_it_turns_up_in(self):
        r = redact.Redactor([SECRET, BASIC])
        b64 = base64.b64encode(BASIC.encode()).decode()
        text = f"a {SECRET} b {quote(SECRET, safe='')} c Basic {b64} d {TAIL} e"
        out = r.scrub_text(text)
        for leak in (SECRET, quote(SECRET, safe=""), b64, TAIL):
            self.assertNotIn(leak, out)
        self.assertEqual(out.count(redact.MASK), 4)
        self.assertIn("Basic " + redact.MASK, out)

    def test_short_or_common_values_are_ignored(self):
        r = redact.Redactor([SECRET, "short", "localhost", "aaaaaaaaaaaa", ""])
        self.assertEqual(r.scrub_text("short localhost aaaaaaaaaaaa"), "short localhost aaaaaaaaaaaa")
        self.assertIsNone(redact.for_turn({"PATH": "/usr/bin", "NAME": "x"}, ["short", "localhost"]))

    def test_an_internal_error_fails_closed(self):
        r = redact.Redactor([SECRET])

        class Broken:
            def sub(self, *_):
                raise RuntimeError("boom")

        r.pattern = Broken()
        self.assertEqual(r.scrub_text("has " + SECRET), redact.ERROR)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- git's own reason, safe to show
APP_TOKEN = "ghs_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
# GitHub's stateless installation token (`ghs_<app id>_<JWT>`, the default since Oct 2026), synthetic.
STATELESS_TOKEN = "ghs_4242_eyJhbGciOiJSUzI1NiJ9.eyJpaWQiOjc3LCJub25jZSI6InN5bnRoZXRpYyJ9." + "S1g-n_aTuRe" * 30


@pytest.mark.parametrize(
    "stderr,wanted",
    [
        (
            "Cloning into 'web'...\nremote: Repository not found.\n"
            f"fatal: repository 'https://x-access-token:{APP_TOKEN}@github.com/acme/web.git/' not found\n",
            [
                "remote: Repository not found.",
                "fatal: repository 'https://" + redact.MASK + "@github.com/acme/web.git/' not found",
            ],
        ),
        (
            "Cloning into 'web'...\nerror: unable to create file src/" + "a" * 40 + ".tsx: Filename too long\n"
            "fatal: unable to checkout working tree\nwarning: Clone succeeded, but checkout failed.\n"
            "You can inspect what was checked out with 'git status'\nand retry with the restore command\n",
            ["Filename too long", "fatal: unable to checkout working tree"],
        ),
        (
            "error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\nfatal: expected flush after ref listing\n",
            ["error: RPC failed; HTTP 500", "fatal: expected flush after ref listing"],
        ),
        (
            "Downloading public/hero.mp4 (12 MB)\nError downloading object: public/hero.mp4 (abc123): Smudge error: "
            "batch response: This repository is over its data quota\nerror: external filter 'git-lfs filter-process' failed\n"
            "fatal: public/hero.mp4: smudge filter lfs failed\n",
            ["over its data quota", "smudge filter lfs failed"],
        ),
        (
            "hint: Using 'master' as the name for the initial branch.\nfatal: could not read Username for "
            "'https://github.com': terminal prompts disabled\n",
            ["fatal: could not read Username"],
        ),
    ],
)
def test_git_failure_keeps_gits_own_reason(stderr, wanted):
    text = redact.git_failure(stderr)
    for part in wanted:
        assert part in text
    assert "hint:" not in text and "Cloning into" not in text and "inspect what was checked out" not in text
    assert APP_TOKEN not in text and len(text) <= 300


def test_git_failure_masks_every_credential_shape_and_is_bounded():
    pat = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789"
    granted = "granted-secret-value-123"
    stderr = "\n".join(
        [
            f"fatal: unable to access 'https://user:{granted}@example.com/x.git/': The requested URL returned error: 403",
            f"error: {APP_TOKEN} and ghp_{'z' * 36} and {pat} were refused",
            f"error: {STATELESS_TOKEN} was refused",
            "error: > Authorization: Basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0c2VjcmV0",
            "error: helper said password=hunter2hunter2 and token=abc123def456",
        ]
    )
    text = redact.git_failure(stderr, secrets=[granted], limit=1000)
    for secret in (
        granted,
        APP_TOKEN,
        STATELESS_TOKEN,
        STATELESS_TOKEN.split(".")[-1],
        pat,
        "ghp_" + "z" * 36,
        "eC1hY2Nlc3MtdG9rZW46c2VjcmV0c2VjcmV0",
        "hunter2hunter2",
        "abc123def456",
    ):
        assert secret not in text
    first = redact.git_failure(stderr.splitlines()[0], secrets=[granted])
    assert granted not in first and "returned error: 403" in first
    long = redact.git_failure("fatal: " + "x" * 5000, limit=120)
    assert len(long) <= 120 and long.endswith("…")
    assert redact.git_failure("") == ""
