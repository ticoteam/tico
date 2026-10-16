//! Keeps this runner on the release its server runs (runner/release_update.py `Follower`, docs/updates.md).
//! The server names its release (`GET runners/desired`, or the event stream's `ready.release`). A checkout (Mac,
//! Linux) hands the move to `python -m runner.release_update`, the same separate process the Python runner starts,
//! which checks the tag out, restarts this runner through its supervisor and rolls back if it does not report in.
//! A Docker runner asks its updater sidecar for the matching image. The heartbeat says where it stands (`release`,
//! `kind`, `commit`, `update`), so Settings > Computers and Health see a Rust computer as they see a Python one.
//!
//! A failed update is tried again after six hours, on the server's next release, or as soon as disk space frees
//! after one that ran out of it. On a server that names no release (a development server) a supervised checkout
//! follows origin/main instead (service.py checkout_status, self_update), restarting only once the new runner binary
//! is prepared, and the heartbeat's `checkout` says how it stands against main (backend/models.py Checkout).

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::util::{epoch_s, log, text_of};

const STATUS_FILE: &str = "update-status.json";
/// The Docker update this runner saw changing containers, until the updater reports how it ended (Follower::unsettled).
const HOLD_FILE: &str = "docker-update-hold.json";
const RETRY_AFTER_S: f64 = 6.0 * 3600.0;
const DRAIN_S: u64 = 20 * 60;
const STALE_UPDATING_S: f64 = 15.0 * 60.0;
const CHECKOUT_EVERY: Duration = Duration::from_secs(600);
const PREPARE_LIMIT_S: u64 = 1800; // a cold cargo build (engine.py BUILD_TIMEOUT_S)
const PREPARE_RETRY: Duration = Duration::from_secs(3600);
/// What a restart picks up: a merge touching nothing here needs none (service.py RUNNER_CODE, plus the Rust source).
const RUNNER_CODE: &[&str] = &["runner/", "runner-rs/", "clients/"];
const NO_SUPERVISOR: &str = "no supervisor would start this runner again after an update: run `scripts/tico install` once on this computer and it updates itself";

/// A semantic version's sort key (release_update.py `key`), or None.
fn key(value: &str) -> Option<(u64, u64, u64, Vec<(u8, u64, String)>)> {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$").unwrap());
    let c = r.captures(value.trim().trim_start_matches('v'))?;
    let n = |i: usize| c[i].parse::<u64>().ok();
    // A release sorts after its pre-releases: (1,) against (0, parts...).
    let order = match c.get(4) {
        None => vec![(1, 0, String::new())],
        Some(pre) => std::iter::once((0, 0, String::new()))
            .chain(pre.as_str().split('.').map(|p| p.parse::<u64>().map_or((1, 0, p.to_string()), |d| (0, d, String::new()))))
            .collect(),
    };
    Some((n(1)?, n(2)?, n(3)?, order))
}

/// Whether `have` should move to `want`: an untagged or unknown release always should.
pub fn behind(have: &str, want: &str) -> bool {
    match (key(have), key(want)) {
        (_, None) => false,
        (None, Some(_)) => true,
        (Some(h), Some(w)) => h < w,
    }
}

pub fn kind() -> &'static str {
    match std::env::var("TICO_RUNNER_KIND").as_deref() {
        Ok("mac") => "mac",
        Ok("linux") => "linux",
        Ok("docker") => "docker",
        _ if Path::new("/.dockerenv").exists() => "docker",
        _ if cfg!(target_os = "macos") => "mac",
        _ => "linux",
    }
}

/// launchd's job label, the systemd unit `scripts/tico install` writes, or TICO_SUPERVISED=1 (service.py supervised).
pub fn supervised() -> bool {
    let var = |k: &str| std::env::var(k).unwrap_or_default();
    var("XPC_SERVICE_NAME").starts_with("team.tico") || var("TICO_SYSTEMD_UNIT").starts_with("tico-") || var("TICO_SUPERVISED") == "1"
}

fn git(root: &Path, args: &[&str]) -> String {
    let out = Command::new("git").arg("-C").arg(root).args(args).env("GIT_TERMINAL_PROMPT", "0").stdin(Stdio::null()).output();
    match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => String::new(),
    }
}

fn is_commit(s: &str) -> bool {
    (s.len() == 40 || s.len() == 64) && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// An update that ran out of disk (release_update.py disk_space_error).
fn disk_space_error(error: &str) -> bool {
    let e = error.to_lowercase();
    ["no space left", "not enough disk space", "disk full", "enospc"].iter().any(|w| e.contains(w))
}

fn disk_error_message(error: &str) -> String {
    if disk_space_error(error) {
        "Not enough disk space to update. Free space on this computer (Docker: `docker image prune -a`); the update retries when space frees.".into()
    } else {
        error.to_string()
    }
}

/// `git -C root <args>` with the runner's whole environment and no prompt (service.py self_update's git):
/// (exit code, stdout), or None when it did not run or ran past `timeout_s`.
async fn git_in(root: &Path, args: &[&str], timeout_s: u64) -> Option<(i32, String)> {
    let mut env: crate::env::Env = std::env::vars().collect();
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    let mut argv = vec!["git".to_string(), "-C".into(), root.to_string_lossy().into_owned()];
    argv.extend(args.iter().map(|a| a.to_string()));
    let out = crate::gitrun::run(&argv, &env, None, timeout_s).await.ok()?;
    Some((out.code, out.stdout.trim().to_string()))
}

/// How the checkout stands against origin/main (service.py checkout_status, #492): commits behind, commits ahead
/// (someone committed here; it never fast-forwards), and whether HEAD moved past what this process runs. None
/// when unknown.
async fn checkout_status(root: &Path, running: &str) -> Option<Value> {
    let ok = |r: Option<(i32, String)>| r.filter(|(c, _)| *c == 0).map(|(_, o)| o);
    ok(git_in(root, &["fetch", "--quiet", "origin", "main"], 30).await)?;
    let head = ok(git_in(root, &["rev-parse", "HEAD"], 30).await)?;
    let counts = ok(git_in(root, &["rev-list", "--left-right", "--count", "HEAD...origin/main"], 30).await)?;
    let n: Vec<u64> = counts.split_whitespace().filter_map(|n| n.parse().ok()).collect();
    let [ahead, behind] = n[..] else { return None };
    let running = if running.is_empty() { head.clone() } else { running.to_string() };
    let now = crate::util::iso(&json!(epoch_s().floor()));
    Some(json!({"head": head, "running": running, "ahead": ahead, "behind": behind, "checked_at": text_of(&now).replace("+00:00", "Z")}))
}

/// Whether the runner's own code differs between two commits; unknown counts as changed (service.py runner_code_changed).
async fn runner_code_changed(root: &Path, old: &str, new: &str) -> bool {
    if old.is_empty() || new.is_empty() || old == new {
        return old != new;
    }
    match git_in(root, &["diff", "--name-only", old, new], 30).await {
        Some((0, out)) => out.split_whitespace().any(|p| RUNNER_CODE.iter().any(|c| p.starts_with(c))),
        _ => true,
    }
}

/// Fast-forward the checkout to origin/main when that is safe (service.py self_update): (restart, note), restart
/// when HEAD is now newer than what this process runs, the note saying why nothing was done.
async fn self_update(root: &Path, running: &str) -> (bool, String) {
    let out = |r: Option<(i32, String)>| r.map(|(_, o)| o).unwrap_or_default();
    let branch = out(git_in(root, &["rev-parse", "--abbrev-ref", "HEAD"], 30).await);
    if branch != "main" {
        return (false, format!("checkout is on {}, not main", if branch.is_empty() { "an unknown branch" } else { &branch }));
    }
    if !out(git_in(root, &["status", "--porcelain", "--untracked-files=no"], 30).await).is_empty() {
        return (false, "checkout has uncommitted changes".into());
    }
    let old = out(git_in(root, &["rev-parse", "HEAD"], 30).await);
    if git_in(root, &["fetch", "--quiet", "origin", "main"], 60).await.is_none_or(|(c, _)| c != 0) {
        return (false, "fetch failed".into());
    }
    let changed = out(git_in(root, &["diff", "--name-only", "HEAD", "origin/main"], 30).await);
    if changed.split_whitespace().any(|p| p == "backend/requirements.txt") {
        return (false, "backend/requirements.txt changed; a person runs pip install and scripts/tico update".into());
    }
    if !changed.is_empty() && git_in(root, &["merge", "--ff-only", "--quiet", "origin/main"], 60).await.is_none_or(|(c, _)| c != 0) {
        return (false, "main does not fast-forward".into());
    }
    let head = out(git_in(root, &["rev-parse", "HEAD"], 30).await);
    if head != old {
        log(&format!("Tico runner: updated its checkout {}..{}; restarting when no turn is running", &old[..old.len().min(7)], &head[..head.len().min(7)]));
    }
    (!running.is_empty() && !head.is_empty() && head != running, String::new())
}

fn checkout_commit(root: &Path) -> String {
    Some(git(root, &["rev-parse", "HEAD"])).filter(|c| is_commit(c)).unwrap_or_default()
}

/// The release a checkout's HEAD is exactly on, the image's TICO_VERSION in Docker, or "".
fn current_release(root: &Path) -> String {
    let value = if kind() == "docker" {
        std::env::var("TICO_VERSION").unwrap_or_default()
    } else {
        git(root, &["describe", "--tags", "--exact-match", "HEAD"])
    };
    let value = value.trim().trim_start_matches('v').to_string();
    if key(&value).is_some() {
        value
    } else {
        String::new()
    }
}

fn manifest_commit(root: &Path) -> String {
    let text = std::fs::read_to_string(root.join("release-manifest.json")).unwrap_or_default();
    let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    Some(text_of(&value["commit"])).filter(|c| is_commit(c)).unwrap_or_default()
}

/// Which commit this process runs, for `scripts/tico status` and the updater's health check (service.py record_revision).
pub fn record_revision(directory: &Path) {
    let revision = checkout_commit(&crate::env::root());
    let path = directory.join("runner-revision");
    if revision.len() != 40 {
        let _ = std::fs::remove_file(path);
        return;
    }
    let _ = std::fs::write(path, json!({"version": crate::runner::RUNNER_VERSION, "revision": revision, "started": epoch_s()}).to_string());
}

/// Record `head` as what this process runs, after a pull that changed nothing it runs (service.py adopt_revision).
fn adopt_revision(directory: &Path, head: &str) {
    let path = directory.join("runner-revision");
    let mut data: Value = std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(json!({}));
    if !data.is_object() {
        data = json!({});
    }
    data["version"] = json!(crate::runner::RUNNER_VERSION);
    data["revision"] = json!(head);
    let _ = std::fs::write(path, data.to_string());
}

fn read_status(directory: &Path) -> Value {
    let v: Value = std::fs::read_to_string(directory.join(STATUS_FILE)).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
    if v.is_object() {
        v
    } else {
        json!({})
    }
}

/// The saved Docker update hold: None when there is none, an error when one is there but cannot be read.
fn read_hold(path: &Path) -> Result<Option<String>, String> {
    match std::fs::read_to_string(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.to_string()),
        Ok(text) => match serde_json::from_str::<Value>(&text) {
            Ok(v) if v["to"].as_str().is_some_and(|to| !to.is_empty()) => Ok(Some(text_of(&v["to"]))),
            _ => Err("it does not name an update".into()),
        },
    }
}

fn write_hold(directory: &Path, to: &str) -> std::io::Result<()> {
    let tmp = directory.join("docker-update-hold.tmp");
    std::fs::write(&tmp, json!({"to": to}).to_string())?;
    std::fs::rename(tmp, directory.join(HOLD_FILE))
}

fn write_status(directory: &Path, fields: Value) {
    let path = directory.join(STATUS_FILE);
    let tmp = directory.join("update-status.tmp");
    if std::fs::write(&tmp, fields.to_string()).is_ok() {
        let _ = std::fs::rename(tmp, path);
    }
}

/// What a fast-forward to main meant for this process (Follower::advance).
enum Moved {
    /// Nothing moved; why, when there is a reason.
    Not(String),
    /// Moved, and nothing the runner runs changed: the new head is what runs.
    Adopted(String),
    /// Moved onto new runner code whose binary is ready: restart once no turn runs.
    Restart,
}

/// The updater sidecar next to a Docker runner (release_update.py `Sidecar`).
struct Sidecar {
    url: String,
    token_file: PathBuf,
}

impl Sidecar {
    fn from_env() -> Sidecar {
        Sidecar {
            url: std::env::var("TICO_UPDATER_URL").unwrap_or_default().trim_end_matches('/').to_string(),
            token_file: PathBuf::from(std::env::var("TICO_UPDATER_TOKEN_FILE").unwrap_or_else(|_| "/control/updater-token".into())),
        }
    }

    async fn call(&self, post: Option<Value>) -> Result<Value, String> {
        let token = std::fs::read_to_string(&self.token_file).unwrap_or_default().trim().to_string();
        let http = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().map_err(|e| e.to_string())?;
        let request = match &post {
            Some(body) => http.post(format!("{}/update", self.url)).json(body),
            None => http.get(format!("{}/status", self.url)),
        };
        let reply = request.bearer_auth(token).send().await.map_err(|_| "unreachable".to_string())?;
        let status = reply.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(status.to_string());
        }
        Ok(reply.json().await.unwrap_or(json!({})))
    }
}

#[derive(Default)]
struct Inner {
    desired: Option<String>,
    features: Value,
    state: String,
    error: String,
    target: String,
    pending: Option<String>,
    pending_since: Option<Instant>,
    running: bool,
    /// The last disk-full failure seen and the free bytes then: (marker, baseline) (release_update.py space_freed).
    disk_failure: Option<(String, u64)>,
    /// The commit this process runs; a pull that changes no runner code moves it without a restart.
    revision: String,
    checkout: Option<Value>,
    checkout_at: Option<Instant>,
    update_note: String,
    /// A main whose runner could not be prepared was backed out; main is not taken again before this.
    prepare_after: Option<Instant>,
    /// The checkout moved to newer runner code: exit once no turn runs, never draining for it (service.py restart_due).
    restart_due: bool,
    /// The Docker update this process saw changing containers (Follower::unsettled): held here as well as on disk,
    /// so a hold that could not be saved is not forgotten while this process runs.
    docker_hold: Option<String>,
}

pub struct Follower {
    directory: PathBuf,
    root: PathBuf,
    kind: &'static str,
    release: String,
    started: String,
    commit: String,
    pinned: bool,
    /// Supervised, and neither `self_update: false` nor TICO_RUNNER_SELF_UPDATE=0 (service.py under_supervisor).
    self_update: bool,
    /// Gets the runner binary for the checkout as it now is (`python -m runner.engine prepare`); non-zero on failure.
    prepare: Vec<String>,
    sidecar: Sidecar,
    inner: Mutex<Inner>,
}

static SHARED: OnceLock<Follower> = OnceLock::new();

/// Set up once at start, before the loop.
pub fn init(config: &Map<String, Value>, directory: &Path) {
    let root = crate::env::root();
    let kind = kind();
    let env = |k: &str| std::env::var(k).unwrap_or_default();
    // A checkout with `self_update: false` stays where it is; a container always has that flag, so it uses the sidecar.
    let pinned = config.get("pinned") == Some(&Value::Bool(true))
        || env("TICO_RUNNER_PINNED") == "1"
        || (kind != "docker" && (config.get("self_update") == Some(&Value::Bool(false)) || env("TICO_RUNNER_SELF_UPDATE") == "0"));
    let started = if kind == "docker" { String::new() } else { checkout_commit(&root) };
    let commit = Some(manifest_commit(&root)).filter(|c| !c.is_empty()).unwrap_or_else(|| started.clone());
    let release = current_release(&root);
    let self_update = supervised() && config.get("self_update") != Some(&Value::Bool(false)) && env("TICO_RUNNER_SELF_UPDATE") != "0";
    let _ = SHARED.set(Follower {
        directory: directory.to_path_buf(),
        root,
        kind,
        release,
        commit,
        pinned,
        self_update,
        prepare: [crate::env::python().to_string_lossy().into_owned(), "-m".into(), "runner.engine".into(), "prepare".into()].into(),
        sidecar: Sidecar::from_env(),
        inner: Mutex::new(Inner { state: "idle".into(), revision: started.clone(), ..Default::default() }),
        started,
    });
}

pub fn shared() -> Option<&'static Follower> {
    SHARED.get()
}

impl Follower {
    /// Ask the server's release (`desired` when the stream already said it), then decide.
    pub async fn poll(&self, client: &Client, desired: Option<String>) {
        let desired = match desired {
            Some(d) => d,
            None => match client.get("runners/desired").await {
                Ok(answer) => {
                    self.inner.lock().unwrap().features = answer["features"].clone();
                    text_of(&answer["version"])
                }
                // A server from before this existed: nothing is sent it.
                Err(e) if e.status == 404 || e.status == 405 => {
                    self.inner.lock().unwrap().desired = None;
                    return self.decide().await;
                }
                Err(_) => return, // a blip: keep what we knew
            },
        };
        self.inner.lock().unwrap().desired = Some(desired);
        self.decide().await;
    }

    /// After an update that ran out of disk: whether space has freed since, so it is tried again at once
    /// (release_update.py space_freed). The baseline is the status's `disk_free`, else the free space first seen.
    fn space_freed(&self, status: &Value, error: &str) -> bool {
        if !disk_space_error(error) {
            return false;
        }
        let at = if self.kind == "docker" { PathBuf::from("/") } else { self.directory.clone() };
        let Some((_, free)) = crate::readiness::disk_usage(&at) else { return false };
        let to = Some(text_of(&status["to"])).filter(|t| !t.is_empty()).unwrap_or_else(|| text_of(&status["target"]));
        let marker = format!("{to}\u{0}{error}\u{0}{}", status["at"]);
        let mut s = self.inner.lock().unwrap();
        let baseline = status["disk_free"].as_u64().unwrap_or_else(|| match &s.disk_failure {
            Some((m, b)) if *m == marker => *b,
            _ => free,
        });
        s.disk_failure = Some((marker, baseline));
        free > baseline
    }

    /// (state, error) of the last update to `want` when it should not be tried again yet.
    async fn outcome(&self, want: &str) -> Option<(String, String)> {
        if self.kind == "docker" {
            // An updater that does not answer cannot start the update, so the update is not due: holding claims for
            // it would leave new work waiting until every running turn ends, for as long as the updater is away.
            let status = match self.sidecar.call(None).await {
                Ok(status) => status,
                Err(code) if code == "unreachable" => return Some(("failed".into(), "the updater did not answer".into())),
                Err(code) => return Some(("failed".into(), format!("the updater answered {code}"))),
            };
            let to = text_of(&status["to"]);
            if to.trim_start_matches('v') != want {
                return None;
            }
            return match status["state"].as_str().unwrap_or_default() {
                s @ ("rolled_back" | "failed") => {
                    let error = text_of(&status["message"]);
                    (!self.space_freed(&status, &error)).then(|| (s.into(), disk_error_message(&error)))
                }
                "pulling" | "restarting" => Some(("updating".into(), String::new())),
                // The image came up but does not say it is `want`: never loop on pulling it again.
                "healthy" => Some(("failed".into(), format!("updated to {want}, but this runner still reports {}", if self.release.is_empty() { "no release" } else { &self.release }))),
                _ => None,
            };
        }
        let status = read_status(&self.directory);
        if text_of(&status["target"]) != want {
            return None;
        }
        let at = status["at"].as_f64().or_else(|| text_of(&status["at"]).parse().ok()).unwrap_or(0.0);
        let age = epoch_s() - at;
        match status["state"].as_str().unwrap_or_default() {
            "updating" if age < STALE_UPDATING_S => Some(("updating".into(), String::new())),
            "updating" => Some(("failed".into(), "the update stopped without an outcome".into())),
            s @ ("failed" | "rolled_back" | "blocked") if age < RETRY_AFTER_S => {
                let error = text_of(&status["error"]);
                (!self.space_freed(&status, &error)).then(|| (s.into(), disk_error_message(&error)))
            }
            _ => None,
        }
    }

    fn no_updater_hint(want: &str) -> String {
        if std::env::var("TICO_RUNNER_COMPOSE").as_deref() == Ok("1") {
            return "no updater is set up; run docker compose pull && docker compose up -d on this box".into();
        }
        format!("no updater is set up; move this runner onto the compose file that has one: on this box run curl -fsSL https://github.com/ticoteam/tico/releases/download/v{want}/install.sh -o install.sh && sh install.sh --runner --url <your Tico address> --code <new code from Settings > Computers > Add computer> --label <name>")
    }

    /// Why this runner must hold work for a Docker update that may still change containers, or None. Once the updater
    /// says `restarting` or `uncertain`, Docker may stop this runner (an accepted stop outlives its client), so the
    /// hold is kept in memory and on disk and ends only when the updater answers with that update's outcome: not when
    /// it stops answering, loses its status, or this runner restarts. A saved hold that cannot be read holds until it
    /// is removed. Only no hold at all, with no such update, lets a missing updater leave the runner claiming.
    async fn unsettled(&self) -> Option<String> {
        if self.kind != "docker" {
            return None;
        }
        let mut unsaved = None;
        let reason = self.hold_reason(&mut unsaved).await?;
        // Until the hold is saved, every check says why a restart would lose it.
        Some(match unsaved {
            Some(e) => format!("{reason} (could not save this hold, so a restart would lose it: {e})").trim().to_string(),
            None => reason,
        })
    }

    /// Follower::unsettled without the save error, which goes to `unsaved`.
    async fn hold_reason(&self, unsaved: &mut Option<String>) -> Option<String> {
        let path = self.directory.join(HOLD_FILE);
        let mut saved = read_hold(&path);
        let memory = self.inner.lock().unwrap().docker_hold.clone();
        // A hold only memory has is saved again on every check, whatever the updater answers, until it is on disk.
        if let Some(to) = memory.as_ref().filter(|to| !to.is_empty()) {
            if saved.as_ref().ok() != Some(&Some(to.clone())) {
                match write_hold(&self.directory, to) {
                    Ok(()) => saved = Ok(Some(to.clone())),
                    Err(e) => *unsaved = Some(e.to_string()),
                }
            }
        }
        let held: Option<Result<String, String>> = match (&memory, &saved) {
            (Some(to), _) => Some(Ok(to.clone())),
            (None, Ok(to)) => to.clone().map(Ok),
            (None, Err(why)) => Some(Err(why.clone())),
        };
        let unknown = |why: &str| {
            format!("holding work: the saved hold for a Docker update at {} cannot be read ({why}); it holds until it is removed, once the updater shows the runner settled", path.display())
        };
        if self.sidecar.url.is_empty() {
            return held.map(|h| match h {
                Ok(to) => format!("holding work: the updater was changing containers for {to} and no updater is set up"),
                Err(why) => unknown(&why),
            });
        }
        let answer = self.sidecar.call(None).await;
        let (state, to) = match &answer {
            Ok(status) => (status["state"].as_str().unwrap_or_default().to_string(), text_of(&status["to"])),
            Err(_) => (String::new(), String::new()),
        };
        if state == "restarting" || state == "uncertain" {
            self.inner.lock().unwrap().docker_hold = Some(to.clone());
            if saved.as_ref().ok() != Some(&Some(to.clone())) {
                *unsaved = write_hold(&self.directory, &to).err().map(|e| e.to_string());
            }
            return Some(answer.map(|status| text_of(&status["message"])).unwrap_or_default());
        }
        let held = match held? {
            Ok(to) => to,
            Err(why) => return Some(unknown(&why)),
        };
        // Settled only by that update's own outcome; a target that is not known never matches.
        if !held.is_empty() && to == held && matches!(state.as_str(), "rolled_back" | "failed" | "healthy") {
            return match std::fs::remove_file(&path) {
                Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
                    Some(format!("holding work: the update to {held} settled, but its saved hold could not be removed: {e}"))
                }
                _ => {
                    self.inner.lock().unwrap().docker_hold = None;
                    None
                }
            };
        }
        let held = if held.is_empty() { "an unknown release".to_string() } else { held };
        Some(match answer {
            Ok(_) => format!("holding work: the updater was changing containers for {held} and has not said how that ended (it says {})", if state.is_empty() { "nothing" } else { &state }),
            Err(code) if code == "unreachable" => format!("holding work: the updater was changing containers for {held} and does not answer"),
            Err(code) => format!("holding work: the updater was changing containers for {held} and answered {code}"),
        })
    }

    async fn decide(&self) {
        let want = self.inner.lock().unwrap().desired.clone().unwrap_or_default();
        let verdict: Option<(String, String)> = if let Some(error) = self.unsettled().await {
            Some(("updating".into(), error))
        } else if want.is_empty() || !behind(&self.release, &want) {
            Some(("idle".into(), String::new()))
        } else if self.pinned {
            Some(("pinned".into(), format!("pinned to this release; the server runs {want}")))
        } else if self.kind != "docker" && !supervised() {
            Some(("blocked".into(), NO_SUPERVISOR.into()))
        } else if self.kind == "docker" && self.sidecar.url.is_empty() {
            Some(("blocked".into(), Self::no_updater_hint(&want)))
        } else {
            self.outcome(&want).await
        };
        let mut s = self.inner.lock().unwrap();
        s.target = want.clone();
        s.pending = None;
        s.running = false;
        match verdict {
            Some((state, error)) => {
                s.running = state == "updating";
                s.state = state;
                s.error = error;
                s.pending_since = None;
            }
            None => {
                s.state = "waiting".into();
                s.error.clear();
                s.pending = Some(want);
                s.pending_since.get_or_insert_with(Instant::now);
            }
        }
    }

    /// True while an update is due or under way; starts it once nothing runs. After DRAIN_S it stops claiming.
    pub async fn blocks_claims(&self, active: bool) -> bool {
        let (pending, since) = {
            let s = self.inner.lock().unwrap();
            if s.running {
                return true;
            }
            match &s.pending {
                None => return false,
                Some(p) => (p.clone(), s.pending_since),
            }
        };
        if !active {
            self.launch(&pending).await;
            return true;
        }
        since.is_some_and(|t| t.elapsed().as_secs() >= DRAIN_S)
    }

    async fn launch(&self, want: &str) {
        let started = if self.kind == "docker" {
            match self.sidecar.call(Some(json!({"version": want}))).await {
                Ok(_) => Ok(()),
                Err(code) if code == "409" => Ok(()), // the updater is already on it
                Err(code) if code == "unreachable" => Err("the updater did not answer".to_string()),
                Err(code) => Err(format!("the updater answered {code}")),
            }
        } else {
            self.spawn(want)
        };
        let mut s = self.inner.lock().unwrap();
        s.pending = None;
        match started {
            Ok(()) => {
                log(&format!("Tico runner: updating to {want}"));
                s.state = "updating".into();
                s.running = true;
            }
            Err(why) => {
                s.state = "failed".into();
                s.error = format!("could not start the update: {why}");
                write_status(&self.directory, json!({"state": "failed", "target": want, "error": s.error, "at": epoch_s()}));
            }
        }
    }

    /// `python -m runner.release_update` as its own process, so it outlives the runner it restarts. Under systemd the
    /// runner's unit kills its whole cgroup on restart, so the update is a transient unit of its own (release_update.py spawn).
    fn spawn(&self, want: &str) -> Result<(), String> {
        let python = crate::env::python();
        let pid = std::process::id().to_string();
        let dir = self.directory.to_string_lossy().to_string();
        let root = self.root.to_string_lossy().to_string();
        let args = ["-m", "runner.release_update", "--root", &root, "--version", want, "--state-dir", &dir, "--pid", &pid, "--started", &self.started];
        let log_path = self.directory.join("update.log");
        let unit = std::env::var("TICO_SYSTEMD_UNIT").unwrap_or_default();
        if unit.ends_with(".service") && !unit.contains('/') {
            let mut cmd = Command::new("systemd-run");
            cmd.args(["--user", "--quiet", "--collect", &format!("--unit=tico-update-{pid}"), &format!("--working-directory={root}")]);
            for name in ["PATH", "HOME", "TICO_SUPERVISED", "TICO_RUNNER_KIND"] {
                if let Ok(v) = std::env::var(name) {
                    if !v.is_empty() {
                        cmd.arg(format!("--setenv={name}={v}"));
                    }
                }
            }
            cmd.arg(format!("--setenv=TICO_SYSTEMD_UNIT={unit}"));
            let log = log_path.to_string_lossy();
            cmd.args(["-p", &format!("StandardOutput=append:{log}"), "-p", &format!("StandardError=append:{log}")]);
            let done = cmd.arg(&python).args(args).stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
            return if done.status.success() { Ok(()) } else { Err(format!("systemd-run exited {}", done.status.code().unwrap_or(-1))) };
        }
        use std::os::unix::process::CommandExt;
        let out = std::fs::OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?;
        let err = out.try_clone().map_err(|e| e.to_string())?;
        let mut cmd = Command::new(&python);
        cmd.args(args).current_dir(&self.root).stdin(Stdio::null()).stdout(out).stderr(err);
        // SAFETY: setsid is async-signal-safe; the child gets its own session, as Popen(start_new_session=True).
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
    }

    /// What a heartbeat adds; nothing for a server that does not name a release.
    pub fn fields(&self, body: &mut Value) {
        let s = self.inner.lock().unwrap();
        if let Some(checkout) = s.checkout.as_ref().filter(|_| s.desired.as_deref().is_none_or(|d| d.is_empty())) {
            body["checkout"] = checkout.clone();
        }
        if s.desired.is_none() {
            return;
        }
        body["release"] = json!(if self.release.is_empty() { "dev" } else { &self.release });
        body["kind"] = json!(self.kind);
        if s.features["runner_commit"] == true && !self.commit.is_empty() {
            body["commit"] = json!(self.commit);
        }
        body["update"] = json!({"state": s.state, "target": s.target, "error": s.error.chars().take(300).collect::<String>()});
    }

    /// True when the server names a release: this runner follows it, not main.
    fn following(&self) -> bool {
        self.inner.lock().unwrap().desired.as_deref().is_some_and(|d| !d.is_empty())
    }

    /// On a server that names no release, every CHECKOUT_EVERY: how the checkout stands against origin/main, and,
    /// when supervised and only behind, fast-forward it and restart onto it once no turn runs; a pull that changes
    /// no runner code is adopted without one (service.py maintain). `restarting`: a restart is already due.
    pub async fn follow_main(&self, restarting: bool) {
        if self.kind == "docker" || self.following() {
            return;
        }
        let running = {
            let mut s = self.inner.lock().unwrap();
            if s.checkout_at.is_some_and(|t| t.elapsed() < CHECKOUT_EVERY) {
                return;
            }
            s.checkout_at = Some(Instant::now());
            s.revision.clone()
        };
        let mut checkout = checkout_status(&self.root, &running).await;
        let restarting = restarting || self.restart_due();
        let stands = checkout.clone().unwrap_or(json!({}));
        let stale = stands["behind"].as_u64().unwrap_or(0) > 0 || stands["head"] != stands["running"];
        if !restarting && self.self_update && stands["ahead"].as_u64().unwrap_or(0) == 0 && stale {
            let note = match self.advance(&running).await {
                Moved::Adopted(head) => {
                    if let Some(c) = checkout.as_mut() {
                        c["running"] = json!(head);
                    }
                    String::new()
                }
                Moved::Restart => {
                    self.inner.lock().unwrap().restart_due = true;
                    String::new()
                }
                Moved::Not(note) => {
                    if !note.is_empty() && note != self.inner.lock().unwrap().update_note {
                        log(&format!("Tico runner: not updating itself: {note}"));
                    }
                    note
                }
            };
            self.inner.lock().unwrap().update_note = note;
        }
        let mut s = self.inner.lock().unwrap();
        // Say why it is not updating, not only that it is behind: a dirty checkout can fall many commits behind.
        if let Some(c) = checkout.as_mut().filter(|c| c["behind"].as_u64().unwrap_or(0) > 0 && !s.update_note.is_empty()) {
            c["blocked"] = json!(s.update_note.chars().take(300).collect::<String>());
        }
        s.checkout = checkout;
    }

    /// The Restart button on a server that names no release: take main first when that is safe (service.py maintain).
    pub async fn take_main(&self) {
        if self.kind != "docker" && !self.following() {
            let running = self.inner.lock().unwrap().revision.clone();
            self.advance(&running).await;
        }
    }

    /// Fast-forward to main and say what that means for this process. New runner code is only restarted onto once
    /// its binary is ready: a computer on a downloaded release binary may have no cargo, and the shim would refuse the
    /// old binary for the new checkout and never start. When it cannot be prepared the checkout goes back to `running`.
    async fn advance(&self, running: &str) -> Moved {
        if self.inner.lock().unwrap().prepare_after.is_some_and(|t| Instant::now() < t) {
            return Moved::Not(self.inner.lock().unwrap().update_note.clone());
        }
        let (restart, note) = self_update(&self.root, running).await;
        let head = if restart { checkout_commit(&self.root) } else { String::new() };
        if !restart || head.is_empty() {
            return Moved::Not(note);
        }
        if !runner_code_changed(&self.root, running, &head).await {
            // Nothing this process runs changed: it is already running the new code.
            adopt_revision(&self.directory, &head);
            log(&format!("Tico runner: {} changes nothing the runner runs; no restart", &head[..7]));
            self.inner.lock().unwrap().revision = head.clone();
            return Moved::Adopted(head);
        }
        let mut env: crate::env::Env = std::env::vars().collect();
        env.insert("TICO_ROOT".into(), self.root.to_string_lossy().into_owned());
        let prepared = crate::gitrun::run(&self.prepare, &env, Some(&self.root), PREPARE_LIMIT_S).await;
        if prepared.as_ref().is_ok_and(|o| o.ok()) {
            return Moved::Restart;
        }
        let why = match prepared {
            Ok(o) => o.stderr.trim().lines().chain(o.stdout.trim().lines()).last().unwrap_or("").to_string(),
            Err(e) => e.to_string(),
        };
        let back = git_in(&self.root, &["reset", "--quiet", "--keep", running], 60).await.is_some_and(|(c, _)| c == 0);
        log(&format!("Tico runner: could not prepare the runner for main {} ({why}); {}", &head[..7], if back { "kept the checkout on what runs now" } else { "and could not move the checkout back; see scripts/tico status" }));
        self.inner.lock().unwrap().prepare_after = Some(Instant::now() + PREPARE_RETRY);
        let note: String = format!("the runner for main could not be prepared (it needs cargo to build, or a release to download): {why}").chars().take(300).collect();
        self.inner.lock().unwrap().update_note = note.clone();
        Moved::Not(note)
    }

    /// The checkout moved to newer runner code: the loop exits once no turn runs, and the supervisor starts it again.
    pub fn restart_due(&self) -> bool {
        self.inner.lock().unwrap().restart_due
    }

    /// After a 422 that names `commit`: an older server, so it is not sent again.
    pub fn drop_commit(&self) {
        self.inner.lock().unwrap().features["runner_commit"] = json!(false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_sort_as_release_update_does() {
        assert!(behind("0.3.44", "0.3.45"));
        assert!(behind("", "0.3.45")); // a checkout on main moves to the server's release
        assert!(behind("0.3.45-rc.1", "0.3.45"));
        assert!(!behind("0.3.45", "v0.3.45"));
        assert!(!behind("0.3.46", "0.3.45"));
        assert!(!behind("0.3.45", "")); // a server with no release asks nothing
    }

    #[tokio::test]
    async fn a_failed_update_waits_and_a_due_one_starts_once_idle() {
        let dir = std::env::temp_dir().join(format!("tico-follow-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = Follower {
            directory: dir.clone(),
            root: dir.clone(),
            kind: "linux",
            release: "0.3.44".into(),
            started: String::new(),
            commit: String::new(),
            pinned: false,
            self_update: true,
            prepare: vec!["true".into()],
            sidecar: Sidecar { url: String::new(), token_file: dir.join("t") },
            inner: Mutex::new(Inner { state: "idle".into(), desired: Some("0.3.45".into()), ..Default::default() }),
        };
        std::env::set_var("TICO_SUPERVISED", "1");
        write_status(&dir, json!({"state": "rolled_back", "target": "0.3.45", "error": "did not report in", "at": epoch_s()}));
        f.decide().await;
        let mut body = json!({});
        f.fields(&mut body);
        assert_eq!(body["update"], json!({"state": "rolled_back", "target": "0.3.45", "error": "did not report in"}));
        assert_eq!(body["release"], "0.3.44");
        assert!(!f.blocks_claims(false).await);

        write_status(&dir, json!({"state": "rolled_back", "target": "0.3.45", "at": epoch_s() - RETRY_AFTER_S - 1.0}));
        f.decide().await;
        assert_eq!(f.inner.lock().unwrap().state, "waiting");
        assert!(!f.blocks_claims(true).await); // a turn is running: keep claiming until the drain time

        // Out of disk: it waits until space frees past what was free when it failed, then tries at once.
        write_status(&dir, json!({"state": "failed", "target": "0.3.45", "error": "write: No space left on device", "at": epoch_s(), "disk_free": u64::MAX}));
        f.decide().await;
        assert_eq!(f.inner.lock().unwrap().state, "failed");
        assert!(f.inner.lock().unwrap().error.starts_with("Not enough disk space to update."));
        write_status(&dir, json!({"state": "failed", "target": "0.3.45", "error": "write: No space left on device", "at": epoch_s(), "disk_free": 0}));
        f.decide().await;
        assert_eq!(f.inner.lock().unwrap().state, "waiting");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn a_docker_runner_whose_updater_does_not_answer_keeps_claiming() {
        let dir = std::env::temp_dir().join(format!("tico-follow-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let drained = Instant::now().checked_sub(Duration::from_secs(DRAIN_S + 60));
        let f = Follower {
            directory: dir.clone(),
            root: dir.clone(),
            kind: "docker",
            release: "0.3.46".into(),
            started: String::new(),
            commit: String::new(),
            pinned: false,
            self_update: true,
            prepare: vec!["true".into()],
            // Nothing listens on the discard port: the sidecar is stopped.
            sidecar: Sidecar { url: "http://127.0.0.1:9".into(), token_file: dir.join("t") },
            inner: Mutex::new(Inner { state: "waiting".into(), desired: Some("0.3.47".into()), pending_since: drained, ..Default::default() }),
        };
        f.decide().await;
        let mut body = json!({});
        f.fields(&mut body);
        assert_eq!(body["update"], json!({"state": "failed", "target": "0.3.47", "error": "the updater did not answer"}));
        assert!(!f.blocks_claims(true).await); // past the drain time, a turn running: still claims
        assert!(!f.blocks_claims(false).await); // idle: claims rather than launching an update nobody can run
        let _ = std::fs::remove_dir_all(dir);
    }

    /// A Docker updater on a local port that answers `/status` with whatever `body` holds at the time.
    async fn fake_updater(body: std::sync::Arc<Mutex<String>>) -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let Ok((mut stream, _)) = listener.accept().await else { return };
                let mut buf = [0u8; 4096];
                let _ = stream.read(&mut buf).await;
                let text = body.lock().unwrap().clone();
                let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}", text.len());
                let _ = stream.write_all(reply.as_bytes()).await;
            }
        });
        format!("http://127.0.0.1:{port}")
    }

    fn docker_follower(dir: &Path, url: &str, release: &str) -> Follower {
        Follower {
            directory: dir.to_path_buf(),
            root: dir.to_path_buf(),
            kind: "docker",
            release: release.into(),
            started: String::new(),
            commit: String::new(),
            pinned: false,
            self_update: true,
            prepare: vec!["true".into()],
            sidecar: Sidecar { url: url.into(), token_file: dir.join("t") },
            inner: Mutex::new(Inner { state: "idle".into(), desired: Some(release.into()), ..Default::default() }),
        }
    }

    #[tokio::test]
    async fn a_docker_runner_holds_work_until_the_updater_reports_how_an_unsettled_update_ended() {
        // Docker accepted a stop of this runner that has not landed yet. The container still up is the new release,
        // so nothing is behind; the hold must outlast the updater going quiet and this runner restarting.
        let dir = std::env::temp_dir().join(format!("tico-follow-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let body = std::sync::Arc::new(Mutex::new(
            r#"{"state": "uncertain", "from": "v0.3.46", "to": "v0.3.47", "message": "Not settled: docker compose up did not finish"}"#.to_string()));
        let url = fake_updater(body.clone()).await;
        let holds = |f: &Follower| f.inner.lock().unwrap().running;
        let f = docker_follower(&dir, &url, "0.3.47");
        f.decide().await;
        let mut fields = json!({});
        f.fields(&mut fields);
        assert_eq!(fields["update"]["state"], "updating");
        assert_eq!(fields["update"]["error"], "Not settled: docker compose up did not finish");
        assert!(f.blocks_claims(true).await && f.blocks_claims(false).await);

        // The updater stops answering (nothing listens on the discard port), and this runner restarts.
        let f = docker_follower(&dir, "http://127.0.0.1:9", "0.3.47");
        f.decide().await;
        assert!(holds(&f) && f.blocks_claims(false).await);
        assert!(f.inner.lock().unwrap().error.contains("does not answer"));

        // Back, but with no outcome for that update: a lost status, or an older update's outcome.
        let f = docker_follower(&dir, &url, "0.3.47");
        for answer in [r#"{"state": "idle", "to": ""}"#, r#"{"state": "healthy", "to": "v0.3.46"}"#] {
            *body.lock().unwrap() = answer.into();
            f.decide().await;
            assert!(holds(&f) && f.blocks_claims(false).await, "{answer}");
        }

        // Settled: Docker confirmed the old version, and the updater says so for that update. Claims resume.
        *body.lock().unwrap() = r#"{"state": "rolled_back", "to": "v0.3.47", "message": "Docker then confirmed the runner on v0.3.46."}"#.into();
        let f = docker_follower(&dir, &url, "0.3.46");
        *f.inner.lock().unwrap() = Inner { state: "idle".into(), desired: Some("0.3.46".into()), ..Default::default() };
        f.decide().await;
        assert!(!holds(&f) && !f.blocks_claims(false).await);
        assert!(!dir.join(HOLD_FILE).exists());
        // With no unsettled update, an updater that does not answer still leaves the runner claiming.
        let f = docker_follower(&dir, "http://127.0.0.1:9", "0.3.46");
        f.decide().await;
        assert!(!f.blocks_claims(false).await);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn a_docker_hold_that_cannot_be_saved_or_read_still_holds() {
        let dir = std::env::temp_dir().join(format!("tico-follow-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let body = std::sync::Arc::new(Mutex::new(r#"{"state": "uncertain", "to": "v0.3.47", "message": "Not settled"}"#.to_string()));
        let url = fake_updater(body.clone()).await;
        let holds = |f: &Follower| f.inner.lock().unwrap().running;
        let behind = |f: &Follower| *f.inner.lock().unwrap() = Inner { state: "idle".into(), desired: Some("0.3.47".into()), ..Default::default() };

        // Seen but not saved, with nothing on disk (the save's temporary file is blocked): only memory holds it.
        std::fs::create_dir_all(dir.join("docker-update-hold.tmp")).unwrap();
        let f = docker_follower(&dir, &url, "0.3.47");
        f.decide().await;
        assert!(holds(&f) && f.inner.lock().unwrap().error.contains("could not save this hold"));
        assert!(!dir.join(HOLD_FILE).exists());
        let quiet = Follower { sidecar: Sidecar { url: "http://127.0.0.1:9".into(), token_file: dir.join("t") }, ..f };
        quiet.decide().await;
        assert!(holds(&quiet) && quiet.blocks_claims(false).await);
        assert!(quiet.inner.lock().unwrap().error.contains("could not save this hold"));

        // Storage recovers while the updater is still away: the next check saves it, and a restart still holds.
        std::fs::remove_dir(dir.join("docker-update-hold.tmp")).unwrap();
        quiet.decide().await;
        assert!(holds(&quiet) && !quiet.inner.lock().unwrap().error.contains("could not save"));
        assert_eq!(read_hold(&dir.join(HOLD_FILE)), Ok(Some("v0.3.47".to_string())));
        let restarted = docker_follower(&dir, "http://127.0.0.1:9", "0.3.47");
        restarted.decide().await;
        assert!(holds(&restarted) && restarted.blocks_claims(false).await);
        // The same with no updater set up: a hold only memory has is saved on that check too.
        std::fs::remove_file(dir.join(HOLD_FILE)).unwrap();
        let unset = Follower { sidecar: Sidecar { url: String::new(), token_file: dir.join("t") }, ..quiet };
        unset.decide().await;
        assert!(holds(&unset) && read_hold(&dir.join(HOLD_FILE)) == Ok(Some("v0.3.47".to_string())));
        std::fs::remove_file(dir.join(HOLD_FILE)).unwrap();

        // The same with the hold's own place blocked, so it can be neither saved nor read.
        std::fs::create_dir_all(dir.join(HOLD_FILE)).unwrap(); // a directory where the hold goes
        let f = docker_follower(&dir, &url, "0.3.47");
        f.decide().await;
        assert!(holds(&f) && f.inner.lock().unwrap().error.contains("could not save this hold"));
        let quiet = Follower { sidecar: Sidecar { url: "http://127.0.0.1:9".into(), token_file: dir.join("t") }, ..f };
        quiet.decide().await;
        assert!(holds(&quiet) && quiet.blocks_claims(false).await);

        // A fresh runner, on the new release or still behind it: the unreadable hold holds, even past an outcome.
        *body.lock().unwrap() = r#"{"state": "rolled_back", "to": "v0.3.47"}"#.into();
        for (url, release) in [("http://127.0.0.1:9", "0.3.47"), ("", "0.3.47"), (url.as_str(), "0.3.46"), ("http://127.0.0.1:9", "0.3.46")] {
            let f = docker_follower(&dir, url, release);
            behind(&f);
            f.decide().await;
            assert!(holds(&f) && f.blocks_claims(false).await, "{url} {release}");
            assert!(f.inner.lock().unwrap().error.contains("cannot be read"), "{url} {release}");
        }

        // A saved hold that does not name an update holds the same way, until it is removed.
        std::fs::remove_dir(dir.join(HOLD_FILE)).unwrap();
        for bad in ["{", r#"{"to": ""}"#] {
            std::fs::write(dir.join(HOLD_FILE), bad).unwrap();
            for release in ["0.3.47", "0.3.46"] {
                let f = docker_follower(&dir, "http://127.0.0.1:9", release);
                behind(&f);
                f.decide().await;
                assert!(holds(&f) && f.blocks_claims(false).await, "{bad} {release}");
            }
        }
        std::fs::remove_file(dir.join(HOLD_FILE)).unwrap();
        let f = docker_follower(&dir, "http://127.0.0.1:9", "0.3.46");
        behind(&f);
        f.decide().await;
        assert!(!holds(&f) && !f.blocks_claims(false).await); // no hold at all: a missing updater still claims
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn a_checkout_on_main_fast_forwards_and_a_merge_outside_runner_code_needs_no_restart() {
        let dir = std::env::temp_dir().join(format!("tico-main-{}", uuid::Uuid::new_v4().simple()));
        let (origin, here) = (dir.join("origin"), dir.join("here"));
        let sh = |cwd: &Path, args: &[&str]| {
            let ok = Command::new("git").args(["-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]).args(args).current_dir(cwd).output().unwrap();
            assert!(ok.status.success(), "{args:?}: {}", String::from_utf8_lossy(&ok.stderr));
        };
        std::fs::create_dir_all(origin.join("runner")).unwrap();
        std::fs::write(origin.join("runner/a.py"), "1").unwrap();
        sh(&origin, &["init", "-q"]);
        sh(&origin, &["add", "."]);
        sh(&origin, &["commit", "-qm", "one"]);
        sh(&dir, &["clone", "-q", origin.to_str().unwrap(), "here"]);
        let first = checkout_commit(&here);
        let stands = checkout_status(&here, &first).await.unwrap();
        assert_eq!((stands["ahead"].as_u64(), stands["behind"].as_u64()), (Some(0), Some(0)));
        assert!(stands["checked_at"].as_str().unwrap().ends_with('Z'));

        std::fs::write(origin.join("README.md"), "docs").unwrap();
        sh(&origin, &["add", "."]);
        sh(&origin, &["commit", "-qm", "docs"]);
        assert_eq!(checkout_status(&here, &first).await.unwrap()["behind"], 1);
        assert_eq!(self_update(&here, &first).await, (true, String::new()));
        let head = checkout_commit(&here);
        assert!(!runner_code_changed(&here, &first, &head).await);
        std::fs::write(origin.join("runner/a.py"), "2").unwrap();
        sh(&origin, &["commit", "-qam", "code"]);
        sh(&here, &["fetch", "-q"]);
        assert!(runner_code_changed(&here, &head, "origin/main").await);

        // New runner code whose binary cannot be prepared: the checkout goes back to what runs, and says why.
        let f = Follower {
            directory: dir.clone(),
            root: here.clone(),
            kind: "linux",
            release: String::new(),
            started: head.clone(),
            commit: String::new(),
            pinned: false,
            self_update: true,
            prepare: vec!["sh".into(), "-c".into(), "echo no cargo >&2; exit 1".into()],
            sidecar: Sidecar { url: String::new(), token_file: dir.join("t") },
            inner: Mutex::new(Inner { state: "idle".into(), desired: Some(String::new()), revision: head.clone(), ..Default::default() }),
        };
        assert!(matches!(f.advance(&head).await, Moved::Not(n) if n.ends_with("no cargo")));
        assert_eq!(checkout_commit(&here), head);
        assert!(matches!(f.advance(&head).await, Moved::Not(_)), "backed off: main is not taken again at once");
        let f = Follower { prepare: vec!["true".into()], inner: Mutex::new(Inner { revision: head.clone(), ..Default::default() }), ..f };
        assert!(matches!(f.advance(&head).await, Moved::Restart));
        assert_ne!(checkout_commit(&here), head);
        sh(&here, &["reset", "-q", "--hard", &head]);

        std::fs::write(here.join("runner/a.py"), "dirty").unwrap();
        assert_eq!(self_update(&here, &head).await.1, "checkout has uncommitted changes");
        let _ = std::fs::remove_dir_all(dir);
    }
}
