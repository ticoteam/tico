"""Shared by the dependency tests (test_task_depends.py, test_private_task_names.py): a task's merged PR and a release
holding commits, for a dependency held until production runs its release (backend/task_depends.py)."""

from backend.store import H

APP = "acme/app"


def merged_pr(c, task_id, number, sha, repo=APP):
    """A PR of the task, merged as `sha`."""
    from backend import item_metadata as IM

    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (
            H.new_id(),
            task_id,
            "pr",
            f"https://github.com/{repo}/pull/{number}",
            repo,
            number,
            f"{repo}#{number}",
            H.now(),
        ),
    )
    IM.save(c, "github", "pr", f"{repo}#{number}", {"state": "merged", "head_sha": sha, "merged_sha": sha}, 1)


def published(c, tag, *holding, repo=APP):
    """A release Tico has saved, whose history holds the commits `holding`."""
    rid, at = H.new_id(), H.now()
    c.execute(
        "INSERT INTO github_releases (id, repo, tag, commit_sha, published_at, metadata_version, read_at, created_at) "
        "VALUES (?,?,?,?,?,1,?,?)",
        (rid, repo, tag, "x" + tag, at, at, at),
    )
    for sha in holding:
        c.execute(
            "INSERT INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,1,?)",
            (rid, sha, at),
        )
