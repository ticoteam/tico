//! Release orchestration. The writer supplies a notes commit; validation and tagging use that object throughout.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::HashMap,
    env, fs,
    path::PathBuf,
    process::{self, Command},
    thread,
    time::Duration,
};

type Result<T> = std::result::Result<T, String>;

fn command(program: &str, args: &[&str]) -> Result<String> {
    let output = Command::new(program)
        .args(args)
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "{program} {} failed: {}{}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr),
            String::from_utf8_lossy(&output.stdout)
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}
fn git(args: &[&str]) -> Result<String> {
    command("git", args)
}
fn gh(args: &[&str]) -> Result<Value> {
    serde_json::from_str(&command("gh", args)?).map_err(|e| format!("Invalid GitHub response: {e}"))
}
fn require(ok: bool, reason: &str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(reason.into())
    }
}
fn sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn version(value: &str) -> bool {
    value.strip_prefix('v').is_some_and(|v| {
        v.split('.').count() == 3
            && v.split('.')
                .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
    })
}

#[derive(Serialize, Deserialize)]
struct Freeze {
    repo: String,
    version: String,
    previous: String,
    frozen: String,
    candidate: Option<String>,
    dispatching: bool,
    run: Option<u64>,
}
impl Freeze {
    fn save(&self, path: &PathBuf) -> Result<()> {
        let temp = path.with_extension("json.tmp");
        fs::write(
            &temp,
            serde_json::to_vec_pretty(self).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        fs::rename(temp, path).map_err(|e| e.to_string())
    }
    fn title(&self, candidate: &str) -> String {
        format!("Validate {candidate} from {}", self.previous)
    }
}
fn main_head() -> Result<String> {
    git(&["fetch", "origin", "main", "--tags"])?;
    git(&["rev-parse", "refs/remotes/origin/main"])
}
fn unchanged(expected: &str) -> Result<()> {
    require(
        main_head()? == expected,
        "main moved after freeze; use a fix PR and freeze again for a new full gate",
    )
}
fn runs(repo: &str) -> Result<Vec<Value>> {
    // Recent runs suffice to find the latest gate; uncertainty refuses freeze instead of scanning unbounded history.
    let page = gh(&[
        "api",
        &format!("repos/{repo}/actions/workflows/ci.yml/runs?branch=main&per_page=100"),
    ])?;
    Ok(page["workflow_runs"]
        .as_array()
        .ok_or("Missing workflow runs")?
        .clone())
}
fn full_gate_green(repo: &str) -> Result<()> {
    let mut eligible = Vec::new();
    for run in runs(repo)? {
        if run["head_branch"] != "main" {
            continue;
        }
        let title = run["display_title"].as_str().unwrap_or("");
        let candidate = if run["event"] == "schedule" && title == "Nightly gate on main" {
            run["head_sha"].as_str().unwrap_or("")
        } else if run["event"] == "workflow_dispatch" {
            title
                .strip_prefix("Validate ")
                .and_then(|s| s.split_once(" from "))
                .map(|p| p.0)
                .unwrap_or("")
        } else {
            continue;
        };
        // A validation of an off-main candidate cannot clear a failed nightly on main.
        if run["event"] == "workflow_dispatch" && run["head_sha"] != candidate {
            continue;
        }
        if !sha(candidate)
            || Command::new("git")
                .args([
                    "merge-base",
                    "--is-ancestor",
                    candidate,
                    "refs/remotes/origin/main",
                ])
                .status()
                .map_err(|e| e.to_string())?
                .success()
                == false
        {
            continue;
        }
        eligible.push(run);
    }
    eligible.sort_by_key(|r| {
        (
            r["created_at"].as_str().unwrap_or("").to_owned(),
            r["id"].as_u64().unwrap_or(0),
        )
    });
    let latest = eligible
        .last()
        .ok_or("No full gate on main found; run Release validation before freezing")?;
    require(latest["status"] == "completed" && latest["conclusion"] == "success", "Latest full gate on main is red or incomplete; a newer green full gate on main is required")
}
fn verify_run(state: &Freeze, candidate: &str, id: u64, completed: bool) -> Result<()> {
    let run = gh(&["api", &format!("repos/{}/actions/runs/{id}", state.repo)])?;
    require(
        run["id"].as_u64() == Some(id)
            && run["path"] == ".github/workflows/ci.yml"
            && run["event"] == "workflow_dispatch"
            && run["head_branch"] == "main",
        "Run is not Release validation dispatched on main",
    )?;
    require(
        run["display_title"] == state.title(candidate) && run["head_sha"] == candidate,
        "Validation run title or candidate commit does not match the notes commit",
    )?;
    if completed {
        require(
            run["status"] == "completed" && run["conclusion"] == "success",
            "Validation did not pass",
        )?;
    }
    Ok(())
}
fn freeze(options: &HashMap<String, String>, path: &PathBuf) -> Result<()> {
    require(!path.exists(), "Freeze record already exists; retain it for resume or remove it explicitly to freeze again")?;
    let ver = options.get("version").ok_or("--version is required")?;
    let previous = options.get("previous").ok_or("--previous is required")?;
    require(
        version(ver) && version(previous) && ver != previous,
        "Use distinct stable versions vX.Y.Z",
    )?;
    require(
        git(&["status", "--porcelain"])?.is_empty(),
        "Freeze requires a clean checkout",
    )?;
    let frozen = main_head()?;
    require(
        git(&["rev-parse", "HEAD"])? == frozen,
        "Checkout must be at main's current head",
    )?;
    let repo = command(
        "gh",
        &[
            "repo",
            "view",
            "--json",
            "nameWithOwner",
            "--jq",
            ".nameWithOwner",
        ],
    )?;
    full_gate_green(&repo)?;
    let mut args = vec![
        "scripts/freeze_guard.py",
        "--head",
        &frozen,
        "--previous",
        previous,
    ];
    if let Some(receipts) = options.get("receipts") {
        args.extend(["--receipts", receipts]);
    }
    println!("{}", command("python3", &args)?);
    unchanged(&frozen)?;
    Freeze {
        repo,
        version: ver.clone(),
        previous: previous.clone(),
        frozen,
        candidate: None,
        dispatching: false,
        run: None,
    }
    .save(path)?;
    println!("Frozen. Technical Writer: prepare a single CHANGELOG-only child commit with this version's notes. Then run publish --notes-commit <full SHA>.");
    Ok(())
}
fn publish(options: &HashMap<String, String>, path: &PathBuf) -> Result<()> {
    let mut state: Freeze = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let candidate = options
        .get("notes-commit")
        .ok_or("--notes-commit is required")?;
    require(sha(candidate), "Notes commit must be a full lowercase SHA")?;
    require(
        state.candidate.as_ref().is_none_or(|c| c == candidate),
        "Notes commit differs from the recorded candidate",
    )?;
    require(
        git(&["show", "-s", "--format=%P", candidate])? == state.frozen,
        "Notes commit must have exactly the frozen commit as its parent",
    )?;
    require(git(&["diff", "--name-only", &state.frozen, candidate])? == "CHANGELOG.md", "Only CHANGELOG.md may change after freeze; product changes require a fix PR and new freeze")?;
    let notes = git(&["show", &format!("{candidate}:CHANGELOG.md")])?;
    let heading = format!("## [{}] - ", state.version.trim_start_matches('v'));
    require(
        notes.lines().any(|l| l.starts_with(&heading)),
        "Missing versioned CHANGELOG section",
    )?;
    // Reuse the release publisher's parser, on the immutable commit rather than this checkout's working file.
    let mut parser = Command::new("python3").args(["-c", "import sys; from scripts.changelog_notes import section; assert section(sys.stdin.read(), sys.argv[1]), 'Empty release notes'", &state.version]).stdin(process::Stdio::piped()).spawn().map_err(|e| e.to_string())?;
    use std::io::Write;
    parser
        .stdin
        .take()
        .ok_or("Missing parser stdin")?
        .write_all(notes.as_bytes())
        .map_err(|e| e.to_string())?;
    require(
        parser.wait().map_err(|e| e.to_string())?.success(),
        "Empty release notes",
    )?;
    let head = main_head()?;
    require(
        head == *candidate && state.candidate.is_some()
            || head == state.frozen && !state.dispatching,
        "main moved after freeze; use a fix PR and freeze again for a new full gate",
    )?;
    if !state.dispatching {
        full_gate_green(&state.repo)?;
    }
    if head == state.frozen {
        // Record before pushing so a lost response can resume with main at either endpoint.
        state.candidate = Some(candidate.clone());
        state.save(path)?;
        git(&["push", "origin", &format!("{candidate}:refs/heads/main")])?;
    }
    unchanged(candidate)?;
    if let Some(id) = options.get("run") {
        require(
            state.dispatching && state.run.is_none(),
            "--run is only for recovery of an interrupted dispatch",
        )?;
        let id = id.parse().map_err(|_| "Invalid run id")?;
        verify_run(&state, candidate, id, false)?;
        state.run = Some(id);
        state.save(path)?;
    }
    if state.run.is_none() {
        require(
            !state.dispatching,
            "Dispatch may already have happened; resume with --run <id> after inspecting Actions",
        )?;
        let before = runs(&state.repo)?
            .iter()
            .filter_map(|r| r["id"].as_u64())
            .max()
            .unwrap_or(0);
        state.dispatching = true;
        state.save(path)?;
        command(
            "gh",
            &[
                "workflow",
                "run",
                "ci.yml",
                "--repo",
                &state.repo,
                "--ref",
                "main",
                "-f",
                &format!("candidate_sha={candidate}"),
                "-f",
                &format!("previous_release={}", state.previous),
            ],
        )?;
        for _ in 0..20 {
            let matches: Vec<_> = runs(&state.repo)?
                .into_iter()
                .filter(|r| {
                    r["id"].as_u64().unwrap_or(0) > before
                        && r["display_title"] == state.title(candidate)
                        && r["head_sha"] == *candidate
                })
                .collect();
            require(
                matches.len() <= 1,
                "Multiple matching validations; inspect Actions and resume with --run",
            )?;
            if let Some(run) = matches.first() {
                state.run = run["id"].as_u64();
                state.save(path)?;
                break;
            }
            thread::sleep(Duration::from_secs(3));
        }
    }
    let id = state
        .run
        .ok_or("Dispatch not visible yet; inspect Actions and resume with --run")?;
    verify_run(&state, candidate, id, false)?;
    // Release Manager runs this in the background; the durable record resumes the same run after interruption.
    let status = Command::new("gh")
        .args([
            "run",
            "watch",
            &id.to_string(),
            "--repo",
            &state.repo,
            "--exit-status",
            "--interval",
            "10",
        ])
        .status()
        .map_err(|e| e.to_string())?;
    require(
        status.success(),
        "Release validation failed or watch was interrupted; no tag pushed",
    )?;
    verify_run(&state, candidate, id, true)?;
    unchanged(candidate)?;
    full_gate_green(&state.repo)?;
    unchanged(candidate)?;
    git(&[
        "push",
        "origin",
        &format!("{candidate}:refs/tags/{}", state.version),
    ])?;
    println!("Tagged {} at validated commit {candidate} (run {id}). Retain the freeze record as the release receipt.", state.version);
    Ok(())
}
fn run() -> Result<()> {
    let mut args = env::args().skip(1);
    let mode = args.next().ok_or("Usage: tico-release freeze --version vX.Y.Z --previous vX.Y.Z [--receipts FILE] | publish --notes-commit SHA [--run ID]; optional --state FILE")?;
    let mut options = HashMap::new();
    while let Some(key) = args.next() {
        require(
            [
                "--version",
                "--previous",
                "--receipts",
                "--state",
                "--notes-commit",
                "--run",
            ]
            .contains(&key.as_str()),
            "Unknown option",
        )?;
        let value = args.next().ok_or("Option needs a value")?;
        require(
            options
                .insert(key.trim_start_matches("--").to_string(), value)
                .is_none(),
            "Duplicate option",
        )?;
    }
    let path = PathBuf::from(if let Some(path) = options.get("state") {
        path.clone()
    } else {
        git(&["rev-parse", "--git-path", "tico-release.json"])?
    });
    match mode.as_str() {
        "freeze" => freeze(&options, &path),
        "publish" => publish(&options, &path),
        _ => Err("Expected freeze or publish".into()),
    }
}
fn main() {
    if let Err(error) = run() {
        eprintln!("Release refused: {error}");
        process::exit(1);
    }
}
