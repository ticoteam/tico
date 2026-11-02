"""op:// values in a secrets file are read from 1Password before a turn; the token stays home."""
import unittest
from unittest.mock import MagicMock

from runner import op


class OnePassword(unittest.TestCase):
    def fake_op(self, answers):
        def run(cmd, **kw):
            ref = cmd[-1]
            r = MagicMock()
            if ref in answers:
                r.returncode, r.stdout, r.stderr = 0, answers[ref], ""
            else:
                r.returncode, r.stdout, r.stderr = 1, "", "[ERROR] 2026/09/06 could not find item"
            self.seen.append((cmd, kw.get("env", {}).get("OP_SERVICE_ACCOUNT_TOKEN")))
            return r
        self.seen = []
        return run

    def test_references_resolve_and_the_token_is_stripped(self):
        env = {"OP_SERVICE_ACCOUNT_TOKEN": "ops_secret", "UPFLUENCE_EMAIL": "op://vault/Upfluence/username",
               "UPFLUENCE_PASSWORD": "op://vault/Upfluence/password", "PANGRAM_API_KEY": "plain"}
        out = op.resolve_op_refs(env, run=self.fake_op({"op://vault/Upfluence/username": "bots@acme.example",
                                                         "op://vault/Upfluence/password": "hunter2"}))
        self.assertEqual(out, {"UPFLUENCE_EMAIL": "ok", "UPFLUENCE_PASSWORD": "ok"})
        self.assertEqual(env["UPFLUENCE_EMAIL"], "bots@acme.example")
        self.assertEqual(env["UPFLUENCE_PASSWORD"], "hunter2")
        self.assertEqual(env["PANGRAM_API_KEY"], "plain")
        self.assertTrue(all(tok == "ops_secret" for _, tok in self.seen))
        for k in op.SECRET_KEYS:
            env.pop(k, None)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", env)

if __name__ == "__main__":
    unittest.main(verbosity=2)
