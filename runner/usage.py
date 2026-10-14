"""What a run spent, counted on this computer and sent with its result (backend/usage.py adds it up).

Every host emits `tokens` events; a `usage` field on one is an increment to add: `{"input", "cached",
"output"}`, where `input` counts every prompt token including the `cached` ones. A host emits each
token once (Codex reports running totals, so its host subtracts; see `runner/hosts/codex.py`). The
meter here sums the increments of one turn and reports uncached input, cached input and output.
"""

import re


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class Meter:
    def __init__(self):
        self.input = self.cached = self.output = 0

    def add(self, event):
        """Add a `tokens` event's increment; events without one (older hosts) add nothing."""
        usage = event.get("usage") if isinstance(event, dict) else None
        if not isinstance(usage, dict):
            return
        self.input += _count(usage.get("input"))
        self.cached += _count(usage.get("cached"))
        self.output += _count(usage.get("output"))

    def report(self, model="", runtime="", billing="api"):
        """The completion's `usage` field, or None when the turn counted no tokens."""
        cached = min(self.cached, self.input)
        if not (self.input or self.output):
            return None
        return {"input_tokens": self.input - cached, "cached_tokens": cached, "output_tokens": self.output,
                "model": str(model or "")[:120], "runtime": str(runtime or "")[:60], "billing": billing}


# A sign-in that bills the person's plan, not a key: the run costs nothing extra, and its API-equivalent
# is shown apart from real spend. Read from the readiness detail (`runtime_readiness` in service.py).
API_KEY = re.compile(r"api[ _-]?key", re.I)


def billing_for(runtime, detail):
    """`subscription` when `runtime` is signed in with a ChatGPT, Claude, Cursor or Meta account, else `api`."""
    text = str(detail or "")
    if not text.lower().startswith("signed in") or API_KEY.search(text):
        return "api"
    if runtime == "codex":
        return "subscription" if "chatgpt" in text.lower() else "api"
    if runtime == "claude":
        # `Signed in with ANTHROPIC_API_KEY` is a key; `claude.ai` or a CLAUDE_CODE_OAUTH_TOKEN is the plan.
        return "subscription"
    if runtime == "cursor":
        return "subscription"
    if runtime == "musecode":
        return "subscription" if "meta account" in text.lower() else "api"
    return "api"
