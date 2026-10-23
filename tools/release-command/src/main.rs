//! Release orchestration: freeze main, validate exactly that commit, tag it, then file one Technical Writer task
//! for the release notes once the release is published.
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
    #[serde(default)]
    tagged: bool,
    #[serde(default)]
    writer_filing: bool,
    #[serde(default)]
    writer_task: Option<String>,
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
    // A new dispatch is among the newest runs; an unseen one is reported rather than searched for in old history.
    let page = gh(&[
        "api",
        &format!("repos/{repo}/actions/workflows/ci.yml/runs?branch=main&event=workflow_dispatch&per_page=100"),
    ])?;
    Ok(page["workflow_runs"]
        .as_array()
        .ok_or("Missing workflow runs")?
        .clone())
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
        "Validation run title or candidate commit does not match the frozen commit",
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
        tagged: false,
        writer_filing: false,
        writer_task: None,
    }
    .save(path)?;
    println!("Frozen. Run publish to validate and tag this commit; release notes are written after publication.");
    Ok(())
}
fn publish(options: &HashMap<String, String>, path: &PathBuf) -> Result<()> {
    let mut state: Freeze = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let candidate = state.frozen.clone();
    let candidate = &candidate;
    if !state.tagged {
        validate_and_tag(&mut state, candidate, options, path)?;
    }
    file_writer_task(&mut state, options, path)
}
fn validate_and_tag(
    state: &mut Freeze,
    candidate: &String,
    options: &HashMap<String, String>,
    path: &PathBuf,
) -> Result<()> {
    unchanged(candidate)?;
    if state.candidate.is_none() {
        state.candidate = Some(candidate.clone());
        state.save(path)?;
    }
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
    git(&[
        "push",
        "origin",
        &format!("{candidate}:refs/tags/{}", state.version),
    ])?;
    state.tagged = true;
    state.save(path)?;
    println!("Tagged {} at validated commit {candidate} (run {id}).", state.version);
    Ok(())
}
// The Release workflow publishes the tag a few minutes after it is pushed; the notes task waits for that.
fn published(state: &Freeze) -> Result<()> {
    for _ in 0..180 {
        let view = Command::new("gh")
            .args(["release", "view", &state.version, "--repo", &state.repo, "--json", "isDraft"])
            .output()
            .map_err(|e| e.to_string())?;
        if view.status.success()
            && serde_json::from_slice::<Value>(&view.stdout).is_ok_and(|v| v["isDraft"] == false)
        {
            return Ok(());
        }
        thread::sleep(Duration::from_secs(10));
    }
    Err(format!("{} was not published within 30 minutes; check the Release workflow, then run publish again", state.version))
}
fn file_writer_task(state: &mut Freeze, options: &HashMap<String, String>, path: &PathBuf) -> Result<()> {
    if let Some(task) = &state.writer_task {
        println!("{} is published; its release notes task is {task}. Retain the freeze record as the release receipt.", state.version);
        return Ok(());
    }
    require(
        !state.writer_filing,
        "The notes task may already have been filed; check the Technical Writer's tasks and record it in the freeze record as writer_task",
    )?;
    published(state)?;
    let writer = options.get("writer").map(String::as_str).unwrap_or("docs-writer");
    let range = format!("{}..{}", state.previous, state.version);
    let body = format!(
        "{version} is published. Write its section in CHANGELOG.md from the pull requests merged in {range} \
         (https://github.com/{repo}/compare/{range}), moving their lines out of ## [Unreleased]. Land it as a \
         CHANGELOG-only pull request whose commits carry the trailer `Changelog-Release: {version}`.",
        version = state.version,
        repo = state.repo,
    );
    // Recorded first, so a lost response is reported instead of filing a second task.
    state.writer_filing = true;
    state.save(path)?;
    let filed = hub_json(&[
        "task",
        "create",
        "--owner",
        writer,
        "--title",
        &format!("Release notes for {}", state.version),
        "--body",
        &body,
    ])?;
    let task = ["short_id", "id"]
        .iter()
        .find_map(|key| filed["task"][key].as_str().or(filed[key].as_str()))
        .ok_or("hub task create returned no task id")?
        .to_owned();
    state.writer_task = Some(task.clone());
    state.writer_filing = false;
    state.save(path)?;
    println!("{} is published; filed release notes task {task} for {writer}. Retain the freeze record as the release receipt.", state.version);
    Ok(())
}
fn hub_json(args: &[&str]) -> Result<Value> {
    serde_json::from_str(&command("hub", args)?).map_err(|e| format!("Invalid hub response: {e}"))
}
fn run() -> Result<()> {
    let mut args = env::args().skip(1);
    let mode = args.next().ok_or("Usage: tico-release freeze --version vX.Y.Z --previous vX.Y.Z [--receipts FILE] | publish [--run ID] [--writer BOT]; optional --state FILE")?;
    let mut options = HashMap::new();
    while let Some(key) = args.next() {
        require(
            [
                "--version",
                "--previous",
                "--receipts",
                "--state",
                "--run",
                "--writer",
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
