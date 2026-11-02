"""An owner-configured reviewer App and reviews bound to an explicit PR head."""
import json
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H
from .auth import validate_identity
from .github_app import GitHubApp, LIVE_TTL, REFRESH_MARGIN, repo_of
from .store import Problem, digest, encode


class ReviewerApp(GitHubApp):
    """Separate caches and signer; repository authority still comes from the primary App."""

    def __init__(self, settings, store, primary):
        super().__init__(settings, store)
        self.primary = primary

    def for_bot(self, bot):
        if bot not in self.settings.github_reviewer_bots:
            return self.primary
        self.row()  # A selected but broken identity must never silently become the author.
        return self

    def row(self, c=None):
        primary = self.primary.row(c)
        app_id = self.settings.github_reviewer_app_id
        installation = self.settings.github_reviewer_installation_id
        if (not primary or not app_id.isdecimal() or int(app_id) < 1
                or not installation.isdecimal() or int(installation) < 1
                or not self.settings.github_reviewer_private_key_file
                or int(app_id) == primary["app_id"]):
            raise Problem("github_reviewer_config", "Configure a separate reviewer App ID, installation ID and private key file, "
                          "or clear TICO_GITHUB_REVIEWER_BOTS to roll back to the shared App and COMMENT", 409)
        return {"app_id": int(app_id), "org": primary["org"], "installation_id": int(installation)}

    def private_key(self, c, row):
        try:
            return Path(self.settings.github_reviewer_private_key_file).read_text()
        except (OSError, UnicodeError):
            raise Problem("github_reviewer_key", "The reviewer App private key file cannot be read on the server", 503) from None

    def _jwt(self):
        try:
            return self.app_jwt(None, self.row())
        except Problem:
            raise
        except Exception:
            raise Problem("github_reviewer_key", "The reviewer App private key cannot sign a GitHub token", 503) from None

    def installation(self, refresh=False):
        row = self.row()
        key = (row["app_id"], row["installation_id"], row["org"].lower())
        with self.lock:
            hit = self.live.get("reviewer-installation")
        if not refresh and hit and hit[0] == key and hit[1] > time.time():
            return row["installation_id"]
        response = self._call("GET", f"/app/installations/{row['installation_id']}",
                              headers={"Authorization": "Bearer " + self._jwt()})
        if response.status_code != 200 or str((response.json().get("account") or {}).get("login", "")).lower() != key[2]:
            raise Problem("github_reviewer_installation", "The reviewer App must be installed on the connected GitHub organization", 409)
        with self.lock:
            self.live["reviewer-installation"] = (key, time.time() + LIVE_TTL)
        return row["installation_id"]

    def mint_reachable(self, repos, permissions, *, include_cache_status=False, refresh_margin=REFRESH_MARGIN, **kw):
        # A repository omitted from the reviewer installation is not missing from the primary installation.
        # Do not let secondary failures poison the team's shared reachability cache or broaden the mint.
        missing = {name.lower() for name in kw.get("missing", ())}
        own = str(kw.get("own") or "").lower()
        repos = [name for name in repos if name.lower() not in missing or name.lower() == own]
        if not repos:
            result = (None, None, [])
            return (*result, False, False) if include_cache_status else result
        token, expires, cached = self.mint(repos, permissions, diagnose=False,
                                          refresh_margin=refresh_margin, include_cache_status=True)
        result = (token, expires, list(repos))
        return (*result, cached, False) if include_cache_status else result


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=3, max_length=201)
    number: int = Field(gt=0)
    head: str = Field(pattern=r"^[0-9a-f]{40}$")
    verdict: Literal["approve", "changes"]
    body: str = Field(default="", max_length=60000)


def throttled(response):
    """GitHub answers 403 for a primary or secondary rate limit too; that clears on its own and is nobody's task."""
    if response.headers.get("retry-after") or response.headers.get("x-ratelimit-remaining") == "0":
        return True
    try:
        body = response.json()
    except ValueError:
        body = response.text
    message = str(body.get("message") or "") if isinstance(body, dict) else str(body or "")
    return bool(re.search(r"rate[\s_-]*limit|abuse", message, re.I))


def install_reviews(app, store, primary, reviewer):
    with store.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS github_review_operations("
                  "actor TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,response TEXT,"
                  "PRIMARY KEY(actor,key))")

    def permitted_repository(c, who, name):
        from . import repositories as R
        validate_identity(c, who)
        row = primary.row(c)
        repository = repo_of(name, "")
        if not row or not repository or repository.lower().split("/")[0] != row["org"].lower():
            raise Problem("forbidden", "The review repository must be in the connected organization", 403)
        grant = next((g for g in R.access(c, H.actor_id(who.actor), row["org"])["effective"]
                      if g["full_name"].lower() == repository.lower() and
                      (g["access"] == "write" or H.actor_id(who.actor) in store.settings.github_reviewer_bots)), None)
        if not grant:
            raise Problem("forbidden", "Submitting a review requires an existing repository grant (write for the primary App)", 403)
        return grant["full_name"]

    @app.post("/api/v2/github/reviews")
    def review(request: Request, body: ReviewRequest):
        who = request.state.identity
        if who.role != "bot" or who.task_actor or who.agent:
            raise Problem("forbidden", "Only a bot's own run submits its GitHub review", 403)
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        if re.search(r"^VERDICT:", body.body, re.MULTILINE):
            raise Problem("validation", "Leave VERDICT out of body; it is appended from number, head and verdict", 422)
        hashed = digest(encode(body.model_dump()))
        bot = H.actor_id(who.actor)
        with store.read_transaction() as c:
            repository = permitted_repository(c, who, body.repository)
            previous = c.execute("SELECT * FROM github_review_operations WHERE actor=? AND key=?", (who.actor, key)).fetchone()
            if previous:
                if previous["request_hash"] != hashed:
                    raise Problem("idempotency_conflict", "This review key was used for different content", 409)
                if previous["response"]:
                    return json.loads(previous["response"])
                raise Problem("github_review_unknown", "A review with this key may already have been submitted; check GitHub before a fresh operation", 409)
        issuer = reviewer.for_bot(bot)
        token, _ = issuer.mint([repository], {"pull_requests": "write", "metadata": "read"},
                               diagnose=issuer is primary)
        headers = {"Authorization": "Bearer " + token}
        path = f"/repos/{repository}/pulls/{body.number}"
        current = issuer._call("GET", path, headers=headers)
        if current.status_code != 200:
            raise Problem("github_review", "GitHub could not read that pull request", 409)
        pr = current.json()
        if pr.get("state") != "open" or (pr.get("head") or {}).get("sha") != body.head:
            raise Problem("github_review_head", "The pull request is closed or its head changed; review the current head before submitting", 409)
        event = {"approve": "APPROVE", "changes": "REQUEST_CHANGES"}[body.verdict] if issuer is reviewer else "COMMENT"
        text = (body.body.rstrip() + "\n\n" if body.body.strip() else "") + f"VERDICT: {body.verdict} PR #{body.number} head {body.head}"
        # Commit the claim before the external write: an ambiguous response must not submit twice.
        with store.transaction() as c:
            permitted_repository(c, who, repository)
            inserted = c.execute("INSERT OR IGNORE INTO github_review_operations VALUES(?,?,?,NULL)",
                                 (who.actor, key, hashed)).rowcount
            if not inserted:
                raise Problem("github_review_unknown", "This review operation is already claimed; read its receipt before retrying", 409)
        response = issuer._call("POST", path + "/reviews", headers=headers,
                                json={"event": event, "commit_id": body.head, "body": text})
        if response.status_code != 200:
            if response.status_code in (400, 403, 404, 422):
                # A definite refusal created no review. Ambiguous responses retain the fence.
                with store.transaction() as c:
                    c.execute("DELETE FROM github_review_operations WHERE actor=? AND key=? AND response IS NULL",
                              (who.actor, key))
                    if response.status_code == 403 and not throttled(response):
                        # A permission blocker: DevOps fixes it, not Head of Engineering (backend/blockers.py).
                        from . import blockers
                        blockers.route(c, f"github_review_403:{repository}",
                                       f"GitHub refused {bot}'s review on {repository} (403)",
                                       f"GitHub answered 403 when {bot} submitted its {body.verdict} review of "
                                       f"{repository} PR #{body.number} at head {body.head}. The review App or the "
                                       "bot's repository grant lacks permission to write reviews there.",
                                       about=blockers.ticket_of_pr(c, repository, body.number))
            raise Problem("github_review", f"GitHub did not confirm the review (HTTP {response.status_code}); check the PR before retrying", 409)
        result = response.json()
        expected = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}[event]
        if result.get("commit_id") != body.head or result.get("state") != expected or not result.get("id"):
            raise Problem("github_review_unknown", "GitHub's review receipt did not confirm the requested head and event; inspect the PR", 502)
        receipt = {"repository": repository, "number": body.number, "head": body.head, "event": event,
                   "review_id": result.get("id"), "url": result.get("html_url"), "verdict": body.verdict}
        with store.transaction() as c:
            c.execute("UPDATE github_review_operations SET response=? WHERE actor=? AND key=?",
                      (encode(receipt), who.actor, key))
            H.event(c, who.actor, "github.review", repository,
                    {"number": body.number, "head": body.head, "event": event, "review_id": result.get("id")})
        return receipt
