"""Each merged Dev ticket gets its review page (backend/review_pages.py): built by its builder, pinned on the ticket, its
link written into the pull request body as `Review page: <url>`. The page shows the last review's pictures only when
that review attached them; a review that said no screens changed gets that line, and a ticket whose last review
attached nothing gets "No pictures."."""

import base64
import json
import re
from urllib.parse import unquote

from backend.auth import Identity
from evals.factory.scenarios import Scenario, standard, tickets

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
LINE = re.compile(r"^Review page: (https?://\S+)$", re.M)


def setup(run, w, team):
    w.connect_github()
    run.shots = {"ui": set(), "none": set(), "done": set()}  # ticket ids, and the review checks already answered
    tickets(run, w, 6)


def during(run, w, team, i):
    attach_last_reviews(run, w)


def _last_review_child(c, ticket, step_id):
    from backend import hubdb as H
    from backend import step_flow as F
    from backend import task_relations as TR

    found = None
    parent = H.task(c, ticket["id"])
    for child_id in TR.child_ids(c, ticket["id"]):
        child = H.task(c, child_id)
        step = F.check_step(c, child, parent) if child else None
        if step and step["id"] == step_id:
            found = child
    return found


def attach_last_reviews(run, w):
    """Each last review that has a verdict attaches its pictures, on every other ticket a manifest with pictures
    (the UI ones) and on the rest a manifest that changed no screens. Once per review."""
    from backend import step_flow as F

    with w.store.read() as c:
        reviews = F.checks(c, w.dev["id"], "review")
        if not reviews:
            return
        last = reviews[-1]
        todo = []
        for n, ticket in enumerate(w.tickets()):
            child = _last_review_child(c, ticket, last["id"])
            if child and child["verdict"] and child["id"] not in run.shots["done"]:
                todo.append((ticket, child, n % 2 == 0))
    for ticket, child, ui in todo:
        run.shots["done"].add(child["id"])
        (run.shots["ui"] if ui else run.shots["none"]).add(ticket["id"])
        who = Identity("bot:" + child["owner_id"].split(":", 1)[-1], "bot")
        files = {"manifest.json": manifest(child["verdict_head"], ui)}
        if ui:
            files["home-1280-before.png"] = PNG + b"before"
            files["home-1280-after.png"] = PNG + b"after"
        for name, data in files.items():
            w.app.state.files.publish(None, who, {"name": name, "task": child["id"]}, data)


def manifest(head, ui):
    shots = [
        {
            "page": "home",
            "width": "1280",
            "theme": "light",
            "before": "home-1280-before.png",
            "after": "home-1280-after.png",
            "changed_pixels_pct": 3.0,
        }
    ]
    selection = {"pages": ["home"] if ui else [], "every": False, "dark": False, "reason": "ui/", "unshown": []}
    return json.dumps(
        {
            "version": "sim",
            "base_ref": "v0",
            "base_sha": "0" * 40,
            "head_ref": "sim",
            "head_sha": head,
            "selection": selection,
            "shots": shots if ui else [],
            "problems": {"before": [], "after": []},
        }
    ).encode()


# ---------------------------------------------------------------------------------------------- checks
def _merged(run):
    tickets = {t["id"] for t in run.w.tickets()}
    return [(n, pr) for n, pr in run.w.prs.items() if pr["merged"] and pr["task"] in tickets]


def every_merged_pull_request_names_its_page(run):
    """Each merged Dev pull request's body carries one `Review page: <url>` line."""
    merged = _merged(run)
    bad = [n for n, pr in merged if not LINE.search(pr.get("body", ""))]
    return bool(merged) and not bad, f"{len(bad)} of {len(merged)} merged pull requests without a line"


def each_line_links_the_page_pinned_on_its_ticket(run):
    w = run.w
    bad = []
    for n, pr in _merged(run):
        found = LINE.search(pr.get("body", ""))
        item = "page:" + unquote(found.group(1).rsplit("/", 1)[-1]) if found else ""
        if not found or not w.sql("SELECT 1 FROM pins WHERE place=? AND item=?", ("task:" + pr["task"], item)):
            bad.append(n)
    return bool(_merged(run)) and not bad, f"{len(bad)} lines without their pinned page"


def every_merged_ticket_has_its_page_pinned(run):
    merged = {pr["task"] for _, pr in _merged(run)}
    pinned = {
        r["task_id"]
        for r in run.w.sql(
            "SELECT substr(place,6) AS task_id FROM pins WHERE place LIKE 'task:%' AND item LIKE 'page:%'"
        )
    }
    missing = merged - pinned
    return bool(merged) and not missing, f"{len(missing)} merged tickets without a pinned page"


def pictures_match_what_the_last_review_attached(run):
    """A page has pictures when its ticket's last review attached them, says the review changed no screens when it said
    so, and says "No pictures." otherwise."""
    w = run.w
    bad, checked = [], 0
    for r in w.sql(
        "SELECT item, substr(place,6) AS task_id FROM pins WHERE place LIKE 'task:%' AND item LIKE 'page:%'"
    ):
        version = w.sql(
            "SELECT v.digest FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id AND v.version=f.current_version "
            "WHERE f.id=?",
            (r["item"][5:],),
        )
        if not version:
            bad.append(w.title(r["task_id"]))
            continue
        checked += 1
        html = w.app.state.blobs.get(version[0]["digest"]).decode("utf-8")
        task = r["task_id"]
        if task in run.shots["ui"]:
            ok = "data:image/png;base64," in html
        elif task in run.shots["none"]:
            ok = "No screens changed." in html
        else:
            ok = "No pictures." in html
        if not ok:
            bad.append(w.title(task))
    return checked > 0 and not bad, f"{len(bad)} pages with the wrong pictures: " + ", ".join(bad[:4])


def every_page_belongs_to_its_builder(run):
    w = run.w
    rows = w.sql(
        "SELECT f.bot_id, t.owner_id, t.title FROM pins p JOIN bot_files f ON 'page:' || f.id = p.item "
        "JOIN tasks t ON t.id = substr(p.place,6) WHERE p.place LIKE 'task:%' AND p.item LIKE 'page:%'"
    )
    bad = [r["title"] for r in rows if (r["owner_id"] or "").split(":", 1)[-1] != r["bot_id"]]
    return bool(rows) and not bad, f"{len(bad)} pages published by someone other than the builder: " + ", ".join(
        bad[:4]
    )


SCENARIOS = [
    Scenario(
        "review_pages",
        "Merged Dev tickets get a review page: its link in the pull request body, pinned on the ticket, the last review's "
        "pictures when it attached them.",
        setup=setup,
        during=during,
        checks=standard(
            ("every merged Dev pull request names its review page", None, every_merged_pull_request_names_its_page),
            ("each line links the page pinned on its ticket", None, each_line_links_the_page_pinned_on_its_ticket),
            ("every merged Dev ticket has its page pinned", None, every_merged_ticket_has_its_page_pinned),
            ("pictures match what the last review attached", None, pictures_match_what_the_last_review_attached),
            ("every page is the builder's", None, every_page_belongs_to_its_builder),
        ),
    ),
]
