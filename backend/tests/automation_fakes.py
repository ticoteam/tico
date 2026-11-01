"""Fake services for the automation executor (backend/automation_engine.py): each records what it was asked."""

ANSWERS = {"bucket": {"type": "choice", "choice": "reply", "confidence": 0.8,
                      "probabilities": {"archive": 0.2, "reply": 0.8}},
           "is_ask": {"type": "noul", "noul": 0.9}}


class Services:
    def __init__(self, answers=None, items=(), spent=0.0, now="2026-10-28T09:00:00Z", fail=(), judge_cost=0.01):
        self.answers, self.items, self.spent, self.clock = answers or ANSWERS, list(items), spent, now
        self.fail, self.judge_cost = set(fail), judge_cost
        self.judged, self.acted, self.turns, self.found, self.saved = [], [], [], [], []
        self.fields = {}
        self.batch_at = "2026-10-28T13:00:00Z"

    def now(self):
        return self.clock

    def next_batch(self):
        return self.batch_at

    def spent_30m(self):
        return self.spent

    def question_set(self, ref):
        return {"set": {"type": "noul", "instructions": ref}}

    def state_fields(self, ref):
        return self.fields.get(ref)

    def checkpoint(self, state):
        self.saved.append(len(state["steps"]))

    def on_hold(self):
        return False

    def judge(self, values, questions, label, level):
        if "judge" in self.fail:
            raise RuntimeError("the judge is down")
        if "decide" in self.fail:   # Decide is off
            return None
        self.judged.append({"values": values, "questions": questions, "label": label, "level": level})
        return {"answers": {k: v for k, v in self.answers.items()}, "cost_usd": self.judge_cost}

    def act(self, do, args, ctx):
        self.acted.append((do, args))
        self.keys = [*getattr(self, "keys", []), ctx["key"]]
        if do in self.fail:
            return {"ok": False, "detail": f"{do} failed"}
        return {"ok": True, "detail": f"{do} done"}

    def start_turn(self, who, prompt, ctx, wait):
        if "turn" in self.fail:
            raise RuntimeError("no such bot")
        if ctx.get("cap") is not None and self.spent_30m() + ctx["cost"] >= ctx["cap"]:
            return None
        self.turns.append({"who": who, "prompt": prompt, "wait": wait})
        return {"ref": f"msg_{len(self.turns)}"}

    def find(self, source, query, limit):
        self.found.append((source, query, limit))
        return self.items[:limit]
