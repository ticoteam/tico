"""The fault rig for a company that makes software: the Product and Engineering presets on the real engine, with the
rig's GitHub (fake_github.RigGitHub) and Jev (fake_jev.FakeJev).

`SoftRig` is the scenarios' hands (scenarios.Rig) plus what these pipelines need: pages to attach, PRs to open and
link, the Heartbeat steps the tracks add (Jev's verdicts, the 5-minute PR re-read, the merge queue's effects), and
webhooks. Everything is written against the Product and Engineering contract (reports/2026-11-09-pipelines-v3/resume/
pe-contract.md in the planning repo); nothing here stands in for the engine.

Not built yet: on the shared base the presets, the kinds and the queue are stubs of tracks A, B and C. A stub that
raises NotImplementedError ("<function>: track X"), a preset that does not exist yet, or a standing that says "<kind>
is not built yet" ends a case with `NotBuiltYet`, so the test suite marks it a strict expected failure that turns red
(an unexpected pass) the day the track lands. Any other failure is a real one."""

import random
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NoReturn

from .fake_github import REPO, Faults, RigGitHub
from .fake_jev import MET, FakeJev
from .scenarios import Rig, ScenarioFailed, expect
from .world import ANA, BUILDER, PM, RELEASE, Refusal, World

PRODUCT, ENGINEERING = "product", "engineering"
PRODUCT_STAGES = ("Ideas", "Spec", "In development", "Validate")
ENGINEERING_STAGES = ("On deck", "Build", "Review 1", "Fixes 1", "Review 2", "Fixes 2", "QA", "Merge", "Release")
NOT_BUILT = "is not built yet"


class NotBuiltYet(ScenarioFailed):
    """The case reached a part another track is still building (a stub, a missing preset, a kind not built yet)."""


class StubHit(BaseException):
    """A stub raised inside a write the rig would otherwise report as a bug: carried out to the case's edge. A
    BaseException, as world.Crash is, so the write rolls back and no `except Exception` swallows it."""


def not_built(what: str) -> NoReturn:
    raise NotBuiltYet(f"not built yet: {what}")


@contextmanager
def stubs_end_the_case() -> Iterator[None]:
    """A stub hit anywhere in the block ends the case as not built yet."""
    try:
        yield
    except (NotImplementedError, StubHit) as error:
        not_built(str(error))


def _h() -> Any:
    from backend.store import H

    return H


@dataclass(frozen=True)
class Page:
    """A Tico page at one version: what `tico task attach <task> page:<file>@<version>` names."""

    file_id: str
    version: int
    digest: str
    author: str

    @property
    def ref(self) -> str:
        return f"page:{self.file_id}@{self.version}"


def make_page(world: World, author: str, text: str, page: Page | None = None) -> Page:
    """A page `author` writes: a new file, or the next version of `page`. Its bytes go in the blob store, as a published
    page's do, so Jev's step reads them as it would on a server."""
    from backend.blobs import Blobs

    H = _h()
    slug = author.split(":", 1)[1]
    n = (page.version + 1) if page else 1
    body = f"<html><body><h1>{text}</h1><p>Version {n}.</p></body></html>".encode()
    digest = Blobs(world.settings).put(body, "text/html")
    now = H.now()

    def write(c: sqlite3.Connection) -> str:
        blob = H.new_id()
        c.execute(
            "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
            (blob, slug, digest, len(body), "page.html", "text/html", now),
        )
        if page:
            fid = page.file_id
        else:
            import uuid

            fid = "file-" + uuid.uuid4().hex[:24]
            c.execute(
                "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, current_version, state, "
                "first_activity_at, last_activity_at) VALUES (?,?,?,?,?,?,?,?,0,'ready',?,?)",
                (fid, slug, "bot", f"page:{fid}", text[:300], "page", "text/html", "tico_blob", now, now),
            )
        c.execute(
            "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at, "
            "media_state) VALUES (?,?,?,?,?,?,?,?,?,'none')",
            (fid, n, blob, digest, len(body), "page.html", "text/html", author, now),
        )
        c.execute("UPDATE bot_files SET current_version=?, last_activity_at=? WHERE id=?", (n, now, fid))
        return fid

    with world.tx() as c:
        file_id = write(c)
    return Page(file_id, n, digest, author)


# ----------------------------------------------------------------------------- the server's Heartbeat steps
def jev_step(world: World) -> int:
    """`jev_verdicts` (track B): Jev judges the pages owed a verdict; then `idea_checks`, the duplicate check on ideas
    filed into Product's Ideas, where the rig's Jev finds every idea new."""
    from backend import idea_checks as IC
    from backend import page_verdicts as PV

    def new(state: dict[str, Any], questions: dict[str, Any], label: str) -> dict[str, Any]:
        return {
            "answers": {q: {"choice": "new" if q.startswith("same") else "none", "confidence": 0.99} for q in questions}
        }

    was, IC.ENGINE = IC.ENGINE, new
    try:
        checked = int(IC.run(world.store, world.now()))
    finally:
        IC.ENGINE = was
    return int(PV.run(world.store, world.now())) + checked


def reread_step(world: World, github: RigGitHub) -> int:
    """`item_metadata` (track C): the 5-minute re-read of the PRs and releases open tasks wait on."""
    from backend.item_sources import github as TF

    return int(TF.refresh(world.store, github, world.now()))


def effects_step(world: World, github: RigGitHub) -> int:
    """`source_actions` (track C): queue and take out PRs."""
    from backend import source_actions as SA

    return int(SA.run(world.store, {"github": github}, world.now()))


# ----------------------------------------------------------------------------- the hands
class SoftRig(Rig):
    """Scenario hands for a software company's world."""

    def __init__(self, world: World, *, faults: bool = False) -> None:
        super().__init__(world)
        rng = random.Random(world.seed * 104729 + 7)
        self.gh = RigGitHub(rng=rng, faults=Faults.on(rng) if faults else Faults())
        # The repo shipped before the run (production runs v1.0.0), so it is one that publishes releases (C31): a task
        # in Release waits for the next one. The server learns of it on its first read of the repo's releases.
        self.gh.gh.publish(REPO, "v1.0.0", at=_h().now())
        self.fake_jev = FakeJev(rng)

    # -------------------------------------------------------------- not built yet
    def preset(self, pipeline_id: str) -> dict[str, Any]:
        from backend import task_engine as E

        found = self.w.read(lambda c: E.pipeline(c, pipeline_id))
        if not found or found.get("retired_at"):
            not_built(f"the {pipeline_id.title()} preset (track A)")
        return found

    def lines(self, task: str) -> list[str]:
        lines = self.wait_lines(task)
        stub = [line for line in lines if line.endswith(NOT_BUILT)]
        if stub:
            not_built("; ".join(stub))
        return lines

    def says(self, task: str, text: str, what: str = "") -> str:
        """The task's waiting line that holds `text`."""
        lines = self.lines(task)
        found = [line for line in lines if text in line]
        expect(found, f"{what or 'the task'} does not say {text!r}: {lines}")
        return found[0]

    def at(self, task: str, stage: str) -> None:
        if self.stage_of(task) != stage:
            self.lines(task)  # a kind not built yet is why, when it is
            expect(False, f"the task is in {self.stage_of(task)}, not {stage}: waits on {self.waits(task)}")

    def owner(self, task: str) -> str | None:
        return self.row(task).get("owner_id")

    def version(self, task: str) -> int:
        return int(self.row(task)["version"])

    # -------------------------------------------------------------- time, GitHub and Jev
    def settle(self, seconds: float = 10) -> dict[str, int]:
        """Time passes as the server sees it: GitHub does what is due, webhooks arrive, the Heartbeat's Product and
        Engineering steps run, then the never-stuck pass and the rig's checks."""
        self.w.shift(seconds)
        with stubs_end_the_case():
            self.gh.tick()
            self.gh.deliver(self.w)
            jev_step(self.w)
            reread_step(self.w, self.gh)
            effects_step(self.w, self.gh)
            self.gh.deliver(self.w)
        return self.check()

    def jev(self) -> int:
        with stubs_end_the_case():
            return jev_step(self.w)

    def effects(self) -> int:
        with stubs_end_the_case():
            return effects_step(self.w, self.gh)

    def reread(self) -> int:
        with stubs_end_the_case():
            return reread_step(self.w, self.gh)

    def ok(self, actor: str, fn: Callable[[sqlite3.Connection], Any], what: str) -> Any:
        with stubs_end_the_case():
            return super().ok(actor, fn, what)

    def refused(self, actor: str, fn: Callable[[sqlite3.Connection], Any], rule: str, what: str) -> Refusal:
        with stubs_end_the_case():
            return super().refused(actor, fn, rule, what)

    def act(self, actor: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with stubs_end_the_case():
            return super().act(actor, fn)

    def check(self) -> dict[str, int]:
        with stubs_end_the_case():
            return super().check()

    # -------------------------------------------------------------- tasks
    def idea(self, title: str, by: str = ANA) -> str:
        self.preset(PRODUCT)
        with stubs_end_the_case():
            return self.task(by, title, PRODUCT)

    def by_hand(self, task: str, to: str | None = None, by: str = ANA) -> None:
        """A person moves a task out of a by-hand stage (Ideas), saying done first if the stage asks it."""
        from backend import answers as A
        from backend import task_engine as E

        stage = self.row(task)["stage_id"]
        rid = self.w.read(
            lambda c: c.execute(
                "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
                (stage,),
            ).fetchone()
        )
        if rid:
            self.ok(by, lambda c: A.answer(c, task, str(rid[0]), by, "done"), "says the idea is ready")
        if self.row(task)["stage_id"] == stage:
            self.ok(by, lambda c: E.move(c, task, to or E.NEXT, by), f"moves it on by hand from {self.stage_of(task)}")

    def filed(self, task: str) -> list[str]:
        """The tasks a task filed into another pipeline (handoffs), oldest first."""
        return [
            str(r[0])
            for r in self.w.read(
                lambda c: c.execute(
                    "SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff' ORDER BY created_at, id",
                    (task,),
                ).fetchall()
            )
        ]

    def visited(self, task: str) -> list[str]:
        """The stages the task entered, in order."""
        return [
            str(r[0])
            for r in self.w.read(
                lambda c: c.execute(
                    "SELECT s.name FROM visits v JOIN stages s ON s.id = v.stage_id WHERE v.task_id=? "
                    "ORDER BY v.entered_at, v.rowid",
                    (task,),
                ).fetchall()
            )
        ]

    def room_wakes(self, bot: str) -> list[str]:
        """Any "Spec has room" wake for a bot, by its old `room:` key: there should be none (C10 tells nobody)."""
        return [
            str(r[0])
            for r in self.w.read(
                lambda c: c.execute(
                    "SELECT wake_key FROM messages WHERE wake_key LIKE 'room:%' AND recipient_id=? ORDER BY rowid",
                    (bot,),
                ).fetchall()
            )
        ]

    # -------------------------------------------------------------- pages
    def page(self, author: str, text: str, verdict: str = MET, page: Page | None = None) -> Page:
        made = make_page(self.w, author, text, page)
        self.fake_jev.say(made.digest, verdict)
        return made

    def attach(self, task: str, page: Page, by: str, requirement: str | None = None) -> None:
        from backend import task_pages as TP

        rid = requirement or self.requirement(task, "page")
        version = self.version(task)
        self.ok(
            by,
            lambda c: TP.attach(c, task, rid, page.ref, by, seen_version=version),
            f"{by} attaches {page.ref}",
        )

    def person_on(self, task: str) -> str:
        """The person whose turn it is on the task: the one on its people OK."""
        people = [who for state, who in self.waits(task) if state == "person" and who]
        expect(people, f"no person is asked: {self.waits(task)}; {self.lines(task)}")
        return str(people[0])

    def ok_page(self, task: str, person: str | None = None) -> str:
        """The person on the page's OK says OK."""
        from backend import answers as A

        who = person or self.person_on(task)
        rid = self.requirement(task, "page")
        self.ok(who, lambda c: A.answer(c, task, rid, who, "ok"), f"{who} OKs the page")
        return who

    # -------------------------------------------------------------- PRs
    def pr(self, task: str, number: int, by: str = BUILDER, checks: str | None = "success") -> str:
        """The bot opens a PR and links it to the task (`tico task link`). Returns its head."""
        head = self.gh.open_pr(number, checks=checks)
        url = f"https://github.com/{REPO}/pull/{number}"
        self.ok(by, lambda c: _h().task_link(c, by, task, url), f"{by} links PR {number}")
        return head

    def queue_again(self, task: str, by: str = RELEASE) -> Any:
        from backend.item_sources import github as MQ

        version = self.version(task)
        return self.act(by, lambda c: MQ.requeue(c, task, by, seen_version=version))

    # -------------------------------------------------------------- whole stretches
    def to_development(self, title: str) -> tuple[str, str]:
        """An idea through Spec (the PM's page, Jev's pass, a person's OK) into In development, where it files its
        Engineering task. Returns (Product task, Engineering task)."""
        self.preset(ENGINEERING)
        p = self.idea(title)
        self.by_hand(p)
        self.at(p, "Spec")
        self.attach(p, self.page(PM, f"Spec: {title}"), PM)
        self.jev()
        self.check()
        self.ok_page(p)
        self.at(p, "In development")
        filed = self.filed(p)
        expect(len(filed) == 1, f"In development filed {len(filed)} tasks, not one")
        return p, filed[0]

    def to_merge(self, e: str, prs: tuple[int, ...] = (1,)) -> None:
        """A person drags an Engineering task to Merge, its PRs open, linked and green, and the queue takes them."""
        from backend import task_engine as E

        for number in prs:
            self.pr(e, number)
        self.settle(self.gh.ci_s)
        if self.stage_of(e) != "Merge":
            self.ok(ANA, lambda c: E.move(c, e, "Merge", ANA, "Reviewed in the call"), "Ana drags it to Merge")
        self.at(e, "Merge")
        self.effects()
        self.settle()

    def ship(self, e: str, tag: str) -> None:
        """An Engineering task from wherever it is to Done: to Merge, GitHub merges its PRs, a release holds them."""
        if self.stage_of(e) not in ("Merge", "Release"):
            fresh = max((n for (_, n) in self.gh.gh.prs), default=0) + 1
            self.to_merge(e, (fresh,))
        for _ in range(6):
            if self.stage_of(e) != "Merge":
                break
            self.settle(self.gh.land_after_s)
        self.at(e, "Release")
        self.gh.publish(tag)
        for _ in range(4):
            if self.row(e)["status"] == "done":
                break
            self.settle(300)
        expect(self.row(e)["status"] == "done", f"the task is not Done after {tag}: {self.lines(e)}")


@contextmanager
def soft_rig(seed: int = 1, *, faults: bool = False) -> Iterator[SoftRig]:
    with World(seed, software=True) as world:
        r = SoftRig(world, faults=faults)
        with r.fake_jev.installed(), stubs_end_the_case():
            yield r
