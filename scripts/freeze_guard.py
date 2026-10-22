"""Freeze-time release guard: net reverts of shipped work, and merged pull requests whose final head nobody approved.

The Release Manager runs it on the frozen candidate, before asking for Release validation:

    python scripts/freeze_guard.py --head <candidate> [--previous vX.Y.Z] [--receipts receipts.json]

It walks every first-parent commit from the previous release tag to the candidate (a merged pull request is one
such commit) and reports, naming the pull request or commit each time:

1. NET REVERT: a change that undoes what a pull request or commit shipped in an earlier release did: all of it,
   however small, or most of its meaningful lines (it deletes the lines that one added, or puts back the lines it
   deleted). A stale tree committed over newer work looks exactly like this, and so does re-applying a fix that was
   reverted. Every earlier release is compared unless --releases limits it.
2. UNREVIEWED: a merged pull request whose final head has neither an approving GitHub review at that commit nor a
   review receipt from its ticket naming that commit. An approval of an earlier head counts only when the change at
   that head is the same as the change at the final head, line for line with whitespace (a merge of main into the
   branch, say).
3. NO PR: a commit that landed without a pull request and has no receipt. Commits that only touch CHANGELOG.md
   or changes/ (release notes and their fragments) are exempt.

Review receipts come from the tickets when GitHub reviews aren't used, as a JSON list:

    [{"pr": 639, "head": "<commit the reviewer approved, at least 7 characters>", "source": "<where it is written>"},
     {"commit": "<commit landed without a PR>", "source": "..."}]

Exit status: 0 when nothing is found, 1 with findings, 2 when it cannot check.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

# A change is a net revert when it undoes everything an earlier change did, however small, or at least this share of
# the meaningful lines an earlier change added or deleted, and at least MIN_LINES of them.
REVERT_SHARE = 0.6
MIN_LINES = 5
# Release notes move between sections at every cut, and the cut deletes the changelog fragments it compiled; they are
# never evidence of a revert or need a review.
NOTES_FILES = {"CHANGELOG.md"}
NOTES_DIR = "changes/"


def is_notes(path):
    return path in NOTES_FILES or path.startswith(NOTES_DIR)
# Cutting tests is routine here (the suite is kept small), so only product code counts toward a revert.
TEST_PATH = re.compile(r"(^|/)(tests?|testdata|fixtures)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.test\.[a-z]+$")
PR_SUBJECT = (re.compile(r"^Merge pull request #(\d+)\b"), re.compile(r"\(#(\d+)\)\s*$"))
CHERRY_PICK = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")


class GuardError(Exception):
    pass


def git(repo, *args, check=True):
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and done.returncode:
        raise GuardError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout if not done.returncode else ""


def meaningful(text):
    """Blank lines, lone brackets and one-word lines appear everywhere; they don't count toward a partial revert."""
    return sum(ch.isalnum() for ch in text) >= 3


def parse_log(text):
    """Commits from `git log -p -U0 --format=%x00%H%x00%P%x00%s%x00%b%x00`: each one's added and removed product lines
    per file, and every path it touched."""
    commits, parts = [], text.split("\0")
    for i in range(1, len(parts) - 4, 5):
        sha, parents, subject, body, diff = parts[i:i + 5]
        files, path = {}, None
        for line in diff.splitlines():
            if line.startswith("diff --git "):
                path = None
            elif line.startswith("--- "):
                path = line[6:] if line.startswith("--- a/") else None
            elif line.startswith("+++ "):
                # A deleted file names itself only on its "---" line.
                path = line[6:] if line.startswith("+++ b/") else path
            elif line[:1] in "+-" and line and path is not None and not is_notes(path) \
                    and not TEST_PATH.search(path):
                text = line[1:].strip()
                if text:
                    added, removed = files.setdefault(path, (Counter(), Counter()))
                    (added if line[0] == "+" else removed)[text] += 1
        commits.append({"sha": sha.strip(), "parents": parents.split(), "subject": subject, "body": body,
                        "files": files, "meaningful": {path: tuple(Counter({k: v for k, v in c.items() if meaningful(k)})
                                                                   for c in pair) for path, pair in files.items()},
                        "touched": _touched(diff)})
    return commits


def _touched(diff):
    paths = set()
    for line in diff.splitlines():
        match = re.match(r"diff --git a/(.+) b/(.+)$", line)
        if match:
            paths.update(match.groups())
    return paths


def first_parent_log(repo, rev_range):
    out = git(repo, "log", "--first-parent", "--diff-merges=first-parent", "-p", "-U0", "--no-renames",
              "--no-color", "--no-ext-diff", "--format=%x00%H%x00%P%x00%s%x00%b%x00", rev_range)
    return parse_log(out)


def pr_number(repo, commit):
    """The pull request a first-parent commit merged: from its subject, or from the commit it cherry-picks."""
    for pattern in PR_SUBJECT:
        match = pattern.search(commit["subject"])
        if match:
            return int(match.group(1))
    picked = CHERRY_PICK.search(commit["body"])
    if picked:
        subject = git(repo, "log", "-1", "--format=%s", picked.group(1), check=False).strip()
        for pattern in PR_SUBJECT:
            match = pattern.search(subject)
            if match:
                return int(match.group(1))
    return None


def label(repo, commit):
    number = pr_number(repo, commit)
    return f"PR #{number} ({commit['sha'][:8]})" if number else f"commit {commit['sha'][:8]} \"{commit['subject']}\""


def undone(change, earlier, lines="files"):
    """How much of `earlier` the `change` undoes, over all its lines or only its meaningful ones: (lines it added now
    deleted, of how many; lines it deleted now back, of how many)."""
    if ("net", lines) not in change:
        net = Counter()
        for added, removed in change[lines].values():
            net.update(added)
            net.subtract(removed)
        change["net", lines] = ({k: -v for k, v in net.items() if v < 0}, {k: v for k, v in net.items() if v > 0})
    gone, back = (Counter(side) for side in change["net", lines])
    added_total = removed_total = added_undone = removed_undone = 0
    for path, (added, removed) in earlier[lines].items():
        added_total += sum(added.values())
        removed_total += sum(removed.values())
        if path not in change["files"]:
            continue
        for text, count in added.items():
            take = min(count, gone[text])
            gone[text] -= take
            added_undone += take
        for text, count in removed.items():
            take = min(count, back[text])
            back[text] -= take
            removed_undone += take
    return added_undone, added_total, removed_undone, removed_total


def reverts(change, earlier):
    """The share of `earlier` that `change` undoes when it is a net revert, else None."""
    added_undone, added_total, removed_undone, removed_total = undone(change, earlier)
    if added_total + removed_total and (added_undone, removed_undone) == (added_total, removed_total):
        return 1.0   # an exact inverse, whatever its size
    added_undone, added_total, removed_undone, removed_total = undone(change, earlier, "meaningful")
    shares = [(n, total) for n, total in ((added_undone, added_total), (removed_undone, removed_total))
              if n >= MIN_LINES and n >= REVERT_SHARE * total]
    if not shares:
        return None
    return max(n / total for n, total in shares)


def shipped_in(repo, sha):
    name = git(repo, "describe", "--contains", "--match", "v[0-9]*", sha, check=False).strip()
    return re.split(r"[~^]", name)[0] if name else "an earlier release"


def net_reverts(repo, changes, shipped):
    by_path = {}
    for earlier in shipped:
        for path in earlier["files"]:
            by_path.setdefault(path, []).append(earlier)
    findings = []
    for change in changes:
        seen = set()
        for path in change["files"]:
            for earlier in by_path.get(path, ()):
                if earlier["sha"] in seen:
                    continue
                seen.add(earlier["sha"])
                share = reverts(change, earlier)
                if share is None:
                    continue
                paths = sorted(set(earlier["files"]) & set(change["files"]))
                findings.append({
                    "kind": "NET REVERT", "commit": change["sha"], "pr": pr_number(repo, change),
                    "reverts": earlier["sha"], "reverts_pr": pr_number(repo, earlier),
                    "text": f"{label(repo, change)} undoes {share:.0%} of {label(repo, earlier)}, shipped in "
                            f"{shipped_in(repo, earlier['sha'])}: {', '.join(paths[:4])}"
                            + (f" and {len(paths) - 4} more" if len(paths) > 4 else "")})
    return findings


class GitHub:
    """Pull request heads and approving reviews, read with the gh CLI."""

    def __init__(self, slug):
        self.slug = slug

    def _api(self, path):
        done = subprocess.run(["gh", "api", "--paginate", f"repos/{self.slug}/{path}"], capture_output=True, text=True)
        if done.returncode:
            raise GuardError(f"gh api {path}: {done.stderr.strip() or done.stdout.strip()}")
        # --paginate prints one JSON document per page; list pages are joined into one list.
        pages, text, at = [], done.stdout, 0
        decoder = json.JSONDecoder()
        while text[at:].strip():
            at += len(text[at:]) - len(text[at:].lstrip())
            page, at = decoder.raw_decode(text, at)
            pages.append(page)
        if pages and all(isinstance(page, list) for page in pages):
            return [item for page in pages for item in page]
        return pages[0] if pages else None

    def head(self, number):
        return self._api(f"pulls/{number}")["head"]["sha"]

    def approvals(self, number):
        return [r["commit_id"] for r in self._api(f"pulls/{number}/reviews") if r.get("state") == "APPROVED"]


def has_object(repo, sha):
    return subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


def change_id(repo, base, head):
    """The change `head` makes on top of its fork from `base`, so a branch merged up to date keeps it.

    Every changed line is kept verbatim (whitespace inside strings and indentation change behaviour); only line numbers
    and blob ids, which shift when main is merged in, are left out.
    """
    fork = git(repo, "merge-base", base, head, check=False).strip()
    if not fork:
        return None
    diff = git(repo, "diff", "--no-color", "--no-ext-diff", "--no-renames", "--binary", "-U0", fork, head, check=False)
    kept, in_body = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            kept.append(line)
            in_body = False
        elif line.startswith(("@@", "GIT binary patch")):
            in_body = True
            kept.append("@@" if line.startswith("@@") else line)
        elif in_body or line.startswith(("new file mode", "deleted file mode", "old mode", "new mode", "Binary files")):
            kept.append(line)
    return "\n".join(kept) if kept else None


def final_head(repo, commit, number, github):
    """The PR's last commit: a merge commit's second parent, or GitHub's record for a squash or rebase merge."""
    source = commit
    picked = CHERRY_PICK.search(commit["body"])
    if picked and len(commit["parents"]) == 1:
        parents = git(repo, "log", "-1", "--format=%P", picked.group(1), check=False).split()
        source = {"parents": parents} if parents else commit
    if len(source["parents"]) == 2:
        return source["parents"][1], None
    if github is None:
        return None, "its final head is on GitHub only (squash merge) and GitHub was not read"
    try:
        return github.head(number), None
    except GuardError as error:
        return None, f"GitHub could not be read: {error}"


def unreviewed(repo, changes, receipts, github, fetch=True):
    findings = []
    for commit in changes:
        number = pr_number(repo, commit)
        if number is None:
            if all(is_notes(path) for path in commit["touched"]):
                continue
            if any(r.get("commit") and commit["sha"].startswith(r["commit"]) for r in receipts):
                continue
            findings.append({"kind": "NO PR", "commit": commit["sha"], "pr": None,
                             "text": f"{label(repo, commit)} landed without a pull request and no receipt names it"})
            continue
        head, why = final_head(repo, commit, number, github)
        if head is None:
            findings.append({"kind": "UNREVIEWED", "commit": commit["sha"], "pr": number,
                             "text": f"PR #{number}: {why}"})
            continue
        approved = [(r["head"], r.get("source") or "ticket receipt") for r in receipts
                    if r.get("pr") == number and len(r.get("head", "")) >= 7]
        approval_error = None
        if github is not None:
            try:
                approved += [(sha, "GitHub review") for sha in github.approvals(number)]
            except GuardError as error:
                approval_error = str(error)
        if any(head.startswith(sha) for sha, _ in approved):
            continue
        same = None
        if approved:
            base = commit["parents"][0]
            if fetch and not has_object(repo, head):
                git(repo, "fetch", "--quiet", "origin", f"refs/pull/{number}/head", check=False)
            final = change_id(repo, base, head) if has_object(repo, head) else None
            for sha, source in approved:
                if final and has_object(repo, sha) and change_id(repo, base, sha) == final:
                    same = (sha, source)
                    break
        if same:
            continue
        if approved:
            named = ", ".join(f"{sha[:8]} ({source})" for sha, source in approved)
            text = f"PR #{number} merged at head {head[:8]}; approvals name {named} and the change differs"
        else:
            text = f"PR #{number} merged at head {head[:8]} with no approving GitHub review and no ticket receipt"
        if approval_error:
            text += f" (GitHub reviews not read: {approval_error})"
        findings.append({"kind": "UNREVIEWED", "commit": commit["sha"], "pr": number, "head": head, "text": text})
    return findings


def previous_release(repo, head):
    """The newest release tag that is earlier than the candidate (and below its own tag, when it has one).

    Release tags sit on release branches that are not always merged back, so the previous release need not be an
    ancestor; a tag is later than the candidate when it contains it.
    """
    tags = git(repo, "tag", "--list", "v[0-9]*", "--sort=-v:refname").split()
    own = set(git(repo, "tag", "--points-at", head, "--list", "v[0-9]*").split())
    below = tags[max(tags.index(t) for t in own) + 1:] if own else tags
    for tag in below:
        contains = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", head, tag], capture_output=True)
        if contains.returncode == 1:
            return tag
    raise GuardError(f"no release tag earlier than {head[:12]}; name one with --previous")


def lookback_base(repo, previous, releases):
    if not releases:
        return None
    tags = git(repo, "tag", "--merged", previous, "--list", "v[0-9]*", "--sort=-v:refname").split()
    older = [t for t in tags if t != previous]
    return older[releases - 1] if len(older) >= releases else None


def check(repo, head, previous=None, receipts=(), github=None, releases=None, fetch=True):
    """Check the candidate against every earlier release, or only the last `releases` of them when given."""
    head = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    previous = previous or previous_release(repo, head)
    changes = first_parent_log(repo, f"{previous}..{head}")
    if not changes:
        raise GuardError(f"nothing between {previous} and {head[:12]}: {previous} is not an earlier release of this "
                         f"candidate; name the right one with --previous")
    base = lookback_base(repo, previous, releases)
    shipped = first_parent_log(repo, f"{base}..{previous}" if base else previous)
    findings = net_reverts(repo, changes, shipped) + unreviewed(repo, changes, list(receipts), github, fetch=fetch)
    return {"head": head, "previous": previous, "changes": len(changes), "compared_since": base or "the first commit",
            "findings": findings}


def repo_slug(repo):
    url = git(repo, "remote", "get-url", "origin", check=False).strip()
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--head", default="HEAD", help="the frozen candidate (default HEAD)")
    parser.add_argument("--previous", help="the previous release tag (default: the newest v* tag below --head)")
    parser.add_argument("--receipts", help="JSON list of review receipts from the tickets")
    parser.add_argument("--repo", default=".", help="the git checkout (default .)")
    parser.add_argument("--github", help="owner/name on GitHub (default: from the origin remote)")
    parser.add_argument("--no-github", action="store_true", help="don't read GitHub; heads and receipts only")
    parser.add_argument("--releases", type=int,
                        help="compare against only the last N earlier releases (default: all history)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    try:
        receipts = json.loads(Path(args.receipts).read_text()) if args.receipts else []
        if not isinstance(receipts, list) or not all(isinstance(r, dict) for r in receipts):
            raise GuardError("--receipts must hold a JSON list of objects")
        github = None
        if not args.no_github:
            slug = args.github or repo_slug(args.repo)
            if not slug or not shutil.which("gh"):
                raise GuardError("GitHub can't be read (no gh or no GitHub origin); pass --no-github to use receipts only")
            github = GitHub(slug)
        result = check(args.repo, args.head, args.previous, receipts, github, args.releases)
    except (GuardError, OSError, ValueError) as error:
        print(f"freeze guard: cannot check: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for finding in result["findings"]:
            print(f"{finding['kind']:<11} {finding['text']}")
        print(f"freeze guard: {result['previous']}..{result['head'][:12]}, {result['changes']} changes compared "
              f"with history since {result['compared_since']}, {len(result['findings'])} findings")
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
