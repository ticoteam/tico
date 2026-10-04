"""What a turn's prompt says: who spoke each room line, and whose company this is
(runner/service.py `prompt`).

A committed batch writes the person's responses into each bot's room; the label says so. The
Live voice session that also wrote into the assistant's room was retired on 2026-09-24."""
import unittest

from clients.tico import APIError
from runner.service import Runner


def live(kind, **more):
    return {"live": {"kind": kind, "session": "s1", **more}}


def attempt(message, history):
    return {"bot": "coo", "conversation": {"id": "room", "scope": "personal", "kind": "chat",
                                          "owner_actor": "human:ana"},
            "message": message, "history": history + [message], "principal": "human:ana"}


def prompt_of(runner, payload, **kw):
    """A resumed thread that last answered before this page: every line on it is new."""
    kw.setdefault("after", "before-the-page"); kw.setdefault("resumed", True)
    return runner.prompt(payload, **kw)


class PromptLabels(unittest.TestCase):
    def setUp(self):
        self.runner = Runner.__new__(Runner)

    def test_update_acknowledgement_uses_the_final_answer_without_a_task(self):
        current = {"id": "m1", "from_actor": "human:ana", "body": "Thanks!", "refs": {"update": "u1"}}
        prompt = prompt_of(self.runner, attempt(current, []))
        self.assertIn("final answer is automatically attached to that update", prompt)
        self.assertIn("hub_update_reply is for humans", prompt)
        self.assertIn("short reply and no task", prompt)

    def test_next_run_tasks_ride_in_the_same_prompt_under_one_header(self):
        # Bot Desk finds this block in the transcript by its header and shows each task apart.
        from runner.service import NEXT_RUN_HEADER
        current = {"id": "m1", "from_actor": "human:ana", "body": "Daily check", "refs": {}}
        payload = attempt(current, [])
        payload["next_run"] = [{"id": "t9", "title": "Watch the new refund requests", "body": "Released today.",
                                "requester": "bot:product-manager", "created": "2026-09-24T10:00:00Z"}]
        prompt = prompt_of(self.runner, payload)
        self.assertIn("Current message from human:ana:\nDaily check", prompt)
        self.assertIn(NEXT_RUN_HEADER + '\n[{"id": "t9", "title": "Watch the new refund requests"', prompt)
        self.assertLess(prompt.index("Daily check"), prompt.index(NEXT_RUN_HEADER))
        self.assertNotIn(NEXT_RUN_HEADER, prompt_of(self.runner, attempt(current, [])))


class SetupTurn(unittest.TestCase):
    """A person's chat with a parked starter bot is its onboarding: the template's flow, not the generic chat rules that
    made a Support Agent file tasks, run tools and write a "contract" into its own AGENT.md."""

    def turn(self, **more):
        ask = {"id": "m1", "from_actor": "human:ana", "refs": {}, "body": "Let's set you up."}
        payload = attempt(ask, [])
        payload.update({"bot": "support", "conversation": {**payload["conversation"], "owner_actor": "human:ana"}, **more})
        return Runner.__new__(Runner).prompt(payload)

    def test_a_setup_turn_follows_the_onboarding_flow_and_leaves_out_the_rules_that_push_it_to_work(self):
        from runner.service import SETUP_TURN
        prompt = self.turn(onboarding="needs_setup")
        self.assertIn(SETUP_TURN, prompt)
        for generic in ("Human chat response contract", "file each distinct ask as a hub task", "Do not end the turn with only a plan"):
            self.assertNotIn(generic, prompt)
        self.assertIn("Current message from human:ana:\nLet's set you up.", prompt)

    def test_any_other_turn_is_unchanged(self):
        for more in ({}, {"onboarding": "onboarded"}, {"onboarding": "needs_setup", "task": {"id": "t1"}},
                     {"onboarding": "needs_setup", "routine": {"id": "r1"}}):
            prompt = self.turn(**more)
            self.assertNotIn("Setup: a human is setting you up", prompt)
            self.assertIn("Do not end the turn with only a plan", prompt)
        self.assertIn("Human chat response contract", self.turn(onboarding="onboarded"))


class FakeConfig:
    """The runner's client, answering only GET /api/v2/config."""

    def __init__(self, config=None, error=None):
        self.config, self.error, self.calls = config, error, 0

    def get(self, path):
        assert path == "config"
        self.calls += 1
        if self.error:
            raise self.error
        return self.config


class Naming(unittest.TestCase):
    """A bot is its company's employee: every name in the prompt comes from the environment."""

    @staticmethod
    def runner(config=None, error=None):
        service = Runner.__new__(Runner)
        service.client = FakeConfig(config, error)
        return service

    @staticmethod
    def turn():
        ask = {"id": "a1", "from_actor": "human:dana", "refs": {}, "body": "The pipeline summary, please."}
        return attempt(ask, [])

class RoutinePlaybooks(unittest.TestCase):
    """A routine's text is saved once; when it is an older copy of a playbook the bot has since
    edited, the turn says to follow the file and to point the routine at it (2026-09-24)."""

    SWEEP = "# Morning competitor sweep\n\nRun software/listen.py --since 24h and save every card.\n"

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "playbooks").mkdir()
        (self.repo / "playbooks" / "sweep.md").write_text(self.SWEEP)
        (self.repo / "playbooks" / "weekly-site-diff.md").write_text("# Weekly site diff\n\nCompare pages.\n")
        self.runner = Runner.__new__(Runner)
        self.runner.config = {"repos": {"listening": str(self.repo)}, "projects_dir": str(self.repo.parent)}

    def note(self, body, routine=True):
        return self.runner.stale_playbook_note({"bot": "listening", "task": {"body": body},
                                                "routine": {"id": "listening:sweep", "title": "t"} if routine else None})

if __name__ == "__main__":
    unittest.main()


class SpokenTurns(unittest.TestCase):
    """2026-09-24: in voice mode the bot narrated its plan ("I'll read the workspace state...")
    before answering; a spoken message asks for a short spoken answer first."""

    def prompt(self, refs):
        runner = Runner.__new__(Runner)
        runner.names = lambda: {"app_name": "Tico", "company_name": "Acme", "assistant_name": "Tico"}
        message = {"id": "m1", "from_actor": "human:ana", "body": "Hello.", "refs": refs}
        return runner.prompt({"bot": "reputation", "conversation": {"id": "c", "kind": "chat"},
                              "message": message, "history": [message]})

class RequestsBecomeTasks(unittest.TestCase):
    """Ana, 2026-09-24: a request for work is filed as the bot's own tasks first."""

    def prompt(self, sender, task=None):
        runner = Runner.__new__(Runner)
        runner.names = lambda: {"app_name": "Tico", "company_name": "Acme", "assistant_name": "Tico"}
        message = {"id": "m1", "from_actor": sender, "body": "Do x, y and z.", "refs": {}}
        payload = {"bot": "seo", "conversation": {"id": "c", "kind": "chat"}, "message": message, "history": [message]}
        if task:
            payload["task"] = task
        return runner.prompt(payload)

class DesignAndVideoRequests(unittest.TestCase):
    """Ana, 2026-09-25: every bot knows it can ask Design for visuals and Video Producer for video."""

    def prompt(self, bot):
        runner = Runner.__new__(Runner)
        runner.names = lambda: {"app_name": "Tico", "company_name": "Acme", "assistant_name": "Tico"}
        message = {"id": "m1", "from_actor": "human:ana", "body": "Hello.", "refs": {}}
        return runner.prompt({"bot": bot, "conversation": {"id": "c", "kind": "chat"},
                              "message": message, "history": [message]})


class WhoRequestedTheTask(unittest.TestCase):
    """2026-09-29: BotOps read a task it filed itself as a newer instruction from the person."""

    def prompt(self, bot, requester):
        runner = Runner.__new__(Runner)
        runner.names = lambda: {"app_name": "Tico", "company_name": "Acme", "assistant_name": "Tico"}
        message = {"id": "m1", "from_actor": "keeper", "body": "Open: Remove Gmail access from inbox", "refs": {"task": "t1"}}
        task = {"id": "t1", "title": "Remove Gmail access from inbox", "requester": requester, "owner": "bot:" + bot}
        return runner.prompt({"bot": bot, "conversation": {"id": "c", "kind": "chat"}, "message": message,
                              "history": [message], "task": task})

    def test_botops_accepts_scoped_bot_work_without_treating_notices_as_human_authority(self):
        prompt = self.prompt("botops", "bot:botops")
        self.assertIn("This task was requested by bot:botops.", prompt)
        self.assertIn("assigned work within your bot-maintenance role", prompt)
        self.assertIn("does not grant a person's authority or override their instructions", prompt)
        self.assertIn("no fresh human permission is needed for that bookkeeping", prompt)
        self.assertIn("do not repeat, reverse or widen a human's request", prompt)
        self.assertIn("This task was requested by human:ana.", self.prompt("botops", "human:ana"))
        other = self.prompt("seo", "bot:botops")
        self.assertIn("This task was requested by bot:botops.", other)
        self.assertNotIn("assigned work within your bot-maintenance role", other)
