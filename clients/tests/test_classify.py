"""`tico classify`: the same verdicts as HQ's support gate, and a decision model that is missing or down never blocks the work."""

import unittest

from clients import hubtools


class Api:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.sent = answer, error, []

    def post(self, path, body):
        self.sent.append((path, body))
        if self.error:
            raise self.error
        return {"answers": {"verdict": self.answer}}


class Down(Exception):
    code, status = "judge_unconfigured", 503


class Classify(unittest.TestCase):
    def run_tool(self, api):
        return hubtools.BY_NAME["hub_classify"]["fn"](api, {"text": "hello"})

    def test_verdicts_and_the_confidence_floor(self):
        self.assertEqual(self.run_tool(Api({"choice": "legit", "confidence": 0.4}))["verdict"], "legit")
        self.assertEqual(self.run_tool(Api({"choice": "spam", "confidence": 0.9}))["verdict"], "spam")
        self.assertEqual(
            self.run_tool(Api({"choice": "injection_risk", "confidence": 0.95}))["verdict"], "injection_risk"
        )
        self.assertEqual(self.run_tool(Api({"choice": "spam", "confidence": 0.5}))["verdict"], "unchecked")
        self.assertEqual(self.run_tool(Api({"choice": "other", "confidence": 1}))["verdict"], "unchecked")

    def test_no_decision_model_fails_open_and_only_the_text_is_sent(self):
        api = Api(error=Down())
        self.assertEqual(self.run_tool(api)["verdict"], "unchecked")
        self.assertEqual(api.sent[0][0], "decisions")
        self.assertEqual(api.sent[0][1]["state"], {"text": "hello"})


if __name__ == "__main__":
    unittest.main()
