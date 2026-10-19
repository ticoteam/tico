"""The org builder's suggestions: which bots to recruit into one department (docs/onboarding.md, PRIVACY.md).

The browser never talks to Tico HQ. It asks `POST /api/v2/onboarding/recruit` with a department, the owner's one-line
answer and their "Suggestions from Tico HQ" toggle, and this module answers from one of two places:

- **Tico HQ** (`POST <TICO_HQ_URL>/v1/recruit`), only when the toggle is on *and* the install allows it: not in demo
  mode, not with `TICO_TELEMETRY=off` or `DO_NOT_TRACK`, and not with the anonymous usage count switched off in
  Settings. It sends the department, the answer, three short facts from "About the company" and the catalog version,
  waits at most HQ_TIMEOUT seconds, and keeps only template ids that are in this install's own catalog for that
  department.
- **The local recommender** (backend/recruit_rank.py) otherwise, and on any failure: no network at all.

Either way the answer is template ids and a short "why" per bot, never text a bot would follow.
"""
import logging
import re
from pathlib import Path

import httpx
import yaml

from . import census as C
from . import onboarding as O
from . import recruit_rank as R
from .store import Problem

log = logging.getLogger("tico.recruit")

HQ_TIMEOUT = 6.0
BUNDLED_DEPARTMENTS = Path(__file__).resolve().parents[1] / "templates" / "groups.yaml"
# Tests swap in an httpx.MockTransport; a real install always uses the network.
TRANSPORT = None


hq_url = C.hq_url


def off_reason(c, settings):
    """Why this install sends nothing to Tico HQ: "demo", "TICO_TELEMETRY", "DO_NOT_TRACK", "setting", or "" when it may.
    The same switches as the anonymous usage count (backend/census.py), so turning that off turns this off too."""
    if settings.demo:
        return "demo"
    if settings.rehearsal:
        return "TICO_REHEARSAL"
    return C.env_off() or ("" if C._load(c).get("enabled", True) else "setting")


def _install_id(c):
    """The usage count's random id, only once the owner has been shown its notice: nothing carries it before then."""
    record = C._load(c)
    return str(record.get("install_id") or "") if record.get("notice") else ""


def departments_file(settings):
    """templates/groups.yaml (older: departments.yaml) beside the catalog in use, else the one this release ships."""
    for name in ("groups.yaml", "departments.yaml"):
        beside = Path(settings.catalog_dir).parent / name
        if beside.is_file():
            return beside
    return BUNDLED_DEPARTMENTS


def catalog(settings):
    """The org builder's catalog ({version, departments, cards}), from the same code HQ's catalog.json is built with."""
    try:
        departments = yaml.safe_load(departments_file(settings).read_text()) or {}
    except (OSError, yaml.YAMLError):
        departments = {}
    return R.build(departments, O.read_cards(settings))


def template_groups(settings):
    """{template: {id, name}} from the org builder's catalog: the group a bot built from a template goes in."""
    value = catalog(settings)
    names = {row["id"]: row["name"] for row in value["departments"]}
    return {card["template"]: {"id": card["department"], "name": names[card["department"]]}
            for card in value["cards"] if card["department"] in names}


def _about(answers):
    return {"what": re.sub(r"\s+", " ", str(answers.get("what_we_do") or "")).strip()[:R.BRIEFING_LIMIT],
            "sells_to": str(answers.get("customers") or ""),
            "software": str(answers.get("software_product") or "") == "yes"}


def _from_hq(answer, cards):
    """HQ's answer, kept only as far as it names this department's templates. None when nothing usable came back."""
    if not isinstance(answer, dict) or not isinstance(answer.get("bots"), list):
        return None
    bots, seen = [], set()
    for row in answer["bots"][:R.MAX_BOTS]:
        if not isinstance(row, dict):
            continue
        template = str(row.get("template_id") or "")
        if template in cards and template not in seen:
            seen.add(template)
            why = re.sub(r"\s+", " ", str(row.get("why") or "")).strip()[:R.WHY_LIMIT]
            bots.append({"template_id": template, "why": why})
    if not bots:
        return None
    defaults = answer.get("suggested_default") if isinstance(answer.get("suggested_default"), list) else []
    return {"bots": bots, "suggested_default": [str(t) for t in defaults if str(t) in seen]}


def _plain_reasons(answer, value, department, briefing, about):
    """An HQ still on an older engine says "Matches “managers”" for any word the answer shares with a card. Each such why
    becomes the one this install's own recommender gives that bot (backend/recruit_rank.py), or none."""
    if not any(row["why"].startswith("Matches “") for row in answer["bots"]):
        return
    ours = {row["template_id"]: row["why"] for row in R.rank(value, department, briefing, about, limit=len(value["cards"]))["bots"]}
    for row in answer["bots"]:
        if row["why"].startswith("Matches “"):
            row["why"] = ours.get(row["template_id"], "")


class Recruiter:
    def __init__(self, store, auth, settings):
        self.store, self.auth, self.settings = store, auth, settings

    def _reader(self, who):
        """The owner and bot administrators build the org chart, as they run onboarding; nobody else asks."""
        if who.role not in ("owner", "human") or not self.auth.bot_admin(who):
            raise Problem("forbidden", "Only the owner or a bot administrator builds the org chart", 403)

    def departments(self, who):
        """`GET /api/v2/onboarding/departments`: the departments, every card with its department and icon, and whether
        this install may ask Tico HQ (`hq.available`, and `hq.off_by` when it may not)."""
        self._reader(who)
        with self.store.read() as c:
            off = off_reason(c, self.settings)
        value = catalog(self.settings)
        return {**value, "hq": {"available": not off, "off_by": off}}

    def recruit(self, who, body):
        """`POST /api/v2/onboarding/recruit`: the suggestions for one department, from HQ when the person's toggle is on
        and the install allows it, else from the local recommender. `source` says which answered."""
        self._reader(who)
        with self.store.read() as c:
            answers = O.load(c)["answers"]
            off = off_reason(c, self.settings)
            install_id = _install_id(c) if not off else ""
        value = catalog(self.settings)
        cards = {card["template"] for card in value["cards"] if card["department"] == body.department}
        about = _about(answers)
        local = R.rank(value, body.department, body.briefing, about)
        if not body.share or off:
            return {**local, "source": "local", "shared": False, "off_by": off}
        payload = {"department": body.department, "briefing": body.briefing, "about": about,
                   "catalog_version": value["version"]}
        if install_id:
            payload["install_id"] = install_id
        try:
            with httpx.Client(timeout=HQ_TIMEOUT, transport=TRANSPORT) as http:
                response = http.post(hq_url() + "/v1/recruit", json=payload, headers={"User-Agent": "tico"})
            answer = _from_hq(response.json() if response.status_code == 200 else None, cards)
        except (httpx.HTTPError, ValueError) as exc:
            # The type only: the request carried the person's answer, and no log line may.
            log.info("Tico HQ suggestions unavailable (%s); using the local recommender", type(exc).__name__)
            answer = None
        if answer is None:
            return {**local, "source": "local", "shared": True, "off_by": ""}
        _plain_reasons(answer, value, body.department, body.briefing, about)
        return {**answer, "source": "hq", "shared": True, "off_by": ""}
