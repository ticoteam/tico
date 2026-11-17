"""Job and step timings for one GitHub Actions run, read with `gh api`.

    python3 job_times.py --repo OWNER/REPO --run RUN_ID [--min 3] [--job NAME]

For each job: seconds queued, seconds after the run's first job started, duration and conclusion. Below each job are
its steps that took at least --min seconds. Needs `gh` signed in with read access to the repository.
"""

import argparse
from datetime import datetime
import json
import subprocess


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (stamp(end) - stamp(start)).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description="Job and step timings for one GitHub Actions run.")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--run", required=True, help="the run id (gh run list)")
    parser.add_argument("--min", type=float, default=3, help="show steps that took at least this many seconds")
    parser.add_argument("--job", default="", help="only jobs whose name contains this")
    args = parser.parse_args()
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{args.repo}/actions/runs/{args.run}/jobs?per_page=100"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    jobs = [job for page in json.loads(out) for job in page["jobs"] if args.job in job["name"]]
    ran = [job for job in jobs if job.get("started_at") and job.get("completed_at")]
    if not ran:
        print("no finished jobs")
        return
    first = min(stamp(job["started_at"]) for job in ran)
    for job in sorted(ran, key=lambda j: j["started_at"]):
        queued = seconds(job.get("created_at"), job["started_at"]) or 0
        at = (stamp(job["started_at"]) - first).total_seconds()
        took = seconds(job["started_at"], job["completed_at"]) or 0
        print(f"{at:>6.0f}s  {took:>5.0f}s  queued {queued:>3.0f}s  {job['name']}  ({job['conclusion']})")
        for step in job.get("steps") or []:
            spent = seconds(step.get("started_at"), step.get("completed_at"))
            if spent is not None and spent >= args.min:
                print(f"{'':>16}{spent:>5.0f}s  {step['name']}")


if __name__ == "__main__":
    main()
