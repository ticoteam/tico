//! Side jobs on a runner with no launchd: with TICO_SIDE_JOBS=1 the runner also supervises
//! `importers`, `close-calls` and `connectors`, each only while it is wanted (the hub assigned meeting importers
//! here, the Close key is in this computer's secrets, the Google key is here or the hub names this computer for
//! mail). Each is a Python job (`python -m runner --config <path> <job>`), a separate child
//! process, so a crash in one never takes down the bot runner, and each restarts with backoff.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::sync::Notify;

use crate::client::{ApiError, Client};
use crate::config::Config;
use crate::util::log;

pub const POLL_SECONDS: u64 = 60;
pub const STABLE_SECONDS: f64 = 120.0;
pub const BACKOFF_START: u64 = 10;
pub const BACKOFF_MAX: u64 = 300;
pub const STOP_GRACE: Duration = Duration::from_secs(30);
pub const JOBS: &[&str] = &["importers", "close-calls", "connectors"];
pub const IMPORTERS: &[&str] = &["granola", "zoom", "google-meet"];

/// (failures, seconds to wait) after a job exited with `code` having run `ran` seconds.
pub fn backoff(code: i32, ran: f64, failures: u32) -> (u32, u64) {
    if code == 0 {
        return (0, 5); // it asked to restart on new code
    }
    let failures = if ran >= STABLE_SECONDS { 0 } else { failures + 1 };
    (failures, BACKOFF_MAX.min(BACKOFF_START * 2u64.pow(failures.saturating_sub(1).min(16))))
}

/// The Close key: CLOSE_API_KEY, else that line of `secrets/close-calls.env` (close_calls.load_key).
pub fn close_key(projects_dir: &std::path::Path) -> String {
    if let Ok(v) = std::env::var("CLOSE_API_KEY") {
        if !v.trim().is_empty() {
            return v.trim().to_string();
        }
    }
    let Ok(text) = std::fs::read_to_string(projects_dir.join("secrets").join("close-calls.env")) else { return String::new() };
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            if key.trim() == "CLOSE_API_KEY" {
                return value.trim().trim_matches(|c| c == '\'' || c == '"').to_string();
            }
        }
    }
    String::new()
}

struct Child {
    process: tokio::process::Child,
    started: Instant,
}

pub struct SideJobs {
    config: Config,
    client: Client,
    poll: Duration,
    children: HashMap<&'static str, Child>,
    failures: HashMap<&'static str, u32>,
    retry_at: HashMap<&'static str, Instant>,
    sources: HashMap<&'static str, Vec<String>>,
}

fn rows(answer: &Value, key: &str) -> Vec<Value> {
    answer.get(key).and_then(|r| r.as_array()).cloned().unwrap_or_default()
}

impl SideJobs {
    pub fn new(config: Config) -> Result<SideJobs, String> {
        let client = Client::new(&config.url(), &config.token(), 30, 1)?;
        // TICO_SIDE_JOBS_POLL exists so the container smoke test does not wait a minute per check.
        let poll = std::env::var("TICO_SIDE_JOBS_POLL").ok().and_then(|p| p.parse().ok()).filter(|p| *p > 0).unwrap_or(POLL_SECONDS);
        Ok(SideJobs {
            config,
            client,
            poll: Duration::from_secs(poll),
            children: HashMap::new(),
            failures: HashMap::new(),
            retry_at: HashMap::new(),
            sources: HashMap::new(),
        })
    }

    /// The sources a job would serve ([] stops it), or Err when the hub cannot be asked (keep what runs).
    async fn wanted(&self, name: &str) -> Result<Vec<String>, ApiError> {
        match name {
            "importers" => match self.client.get("runners/importers").await {
                Ok(answer) => Ok(rows(&answer, "importers")
                    .iter()
                    .filter_map(|r| r.get("source").and_then(|s| s.as_str()))
                    .filter(|s| IMPORTERS.contains(s))
                    .map(String::from)
                    .collect()),
                Err(e) if e.status == 403 => Ok(vec![]), // not an import operator
                Err(e) => Err(e),
            },
            "close-calls" => Ok(if close_key(&self.config.projects_dir()).is_empty() { vec![] } else { vec!["close".into()] }),
            _ => {
                crate::mail_key::protect(&self.config);
                if crate::mail_key::credential_present(&self.config) {
                    return Ok(vec!["mail".into(), "calendar".into()]);
                }
                match self.client.get("runners/connectors").await {
                    Ok(answer) => Ok(rows(&answer, "connectors").iter().filter_map(|r| r.as_str()).map(String::from).collect()),
                    Err(e) if e.status == 404 => Ok(vec![]), // a hub from before the assignment
                    Err(e) => Err(e),
                }
            }
        }
    }

    /// How a crash shows up in Settings (a connector's missing heartbeat shows it stale on its own).
    async fn report(&self, name: &str, sources: &[String]) {
        match name {
            "importers" => {
                for source in sources {
                    let body = json!({"state": "error", "error_code": "sync_error",
                                      "message": "The importers job on this computer stopped; it is restarting."});
                    let _ = self.client.post(&format!("imports/sources/{source}/status"), &body, None).await;
                }
            }
            "close-calls" => {
                let _ = self.client.post("imports/sources/close/status", &json!({"state": "error", "error_code": "sync_error"}), None).await;
            }
            _ => {}
        }
    }

    fn spawn(&self, name: &str) -> std::io::Result<tokio::process::Child> {
        let mut cmd = tokio::process::Command::new(crate::env::python());
        cmd.args(["-m", "runner", "--config"])
            .arg(&self.config.path)
            .arg(name)
            .current_dir(crate::env::root())
            .env_remove("TICO_SIDE_JOBS")
            .stdin(std::process::Stdio::null());
        cmd.spawn()
    }

    async fn halt(&mut self, name: &str) {
        let Some(mut child) = self.children.remove(name) else { return };
        if child.process.try_wait().ok().flatten().is_some() {
            return;
        }
        if let Some(pid) = child.process.id() {
            // SAFETY: signalling a child this runner started and has not reaped.
            unsafe { libc::kill(pid as i32, libc::SIGTERM) };
        }
        if tokio::time::timeout(STOP_GRACE, child.process.wait()).await.is_err() {
            let _ = child.process.kill().await;
        }
    }

    async fn tick(&mut self) {
        for &name in JOBS {
            let exited = self.children.get_mut(name).and_then(|c| c.process.try_wait().ok().flatten());
            if let Some(status) = exited {
                let child = self.children.remove(name).unwrap();
                let code = status.code().unwrap_or(-1);
                let (failures, delay) = backoff(code, child.started.elapsed().as_secs_f64(), self.failures.get(name).copied().unwrap_or(0));
                self.failures.insert(name, failures);
                if code != 0 {
                    let sources = self.sources.get(name).cloned().unwrap_or_default();
                    self.report(name, &sources).await;
                }
                self.retry_at.insert(name, Instant::now() + Duration::from_secs(delay));
                log(&format!("Tico side jobs: {name} exited ({code}), restarting in {delay}s"));
            }
            let sources = match self.wanted(name).await {
                Ok(s) => s,
                Err(e) => {
                    log(&format!("Tico side jobs: cannot tell whether {name} is wanted, {}", crate::runner::describe(&e)));
                    continue;
                }
            };
            self.sources.insert(name, sources.clone());
            if sources.is_empty() {
                if self.children.contains_key(name) {
                    log(&format!("Tico side jobs: {name} is no longer assigned here; stopping it"));
                    self.halt(name).await;
                }
                continue;
            }
            if !self.children.contains_key(name) && self.retry_at.get(name).is_none_or(|t| Instant::now() >= *t) {
                match self.spawn(name) {
                    Ok(process) => {
                        self.children.insert(name, Child { process, started: Instant::now() });
                        log(&format!("Tico side jobs: started {name} ({})", sources.join(", ")));
                    }
                    Err(e) => log(&format!("Tico side jobs: {e}")),
                }
            }
        }
    }

    /// Supervise until `stop` is notified, then stop every job.
    pub async fn run(mut self, stop: Arc<Notify>) {
        loop {
            self.tick().await;
            if tokio::time::timeout(self.poll, stop.notified()).await.is_ok() {
                break;
            }
        }
        let names: Vec<&'static str> = self.children.keys().copied().collect();
        for name in names {
            self.halt(name).await;
        }
    }
}

/// Start the supervisor when TICO_SIDE_JOBS=1: (its task, what stops it).
pub fn start(config: &Config) -> Option<(tokio::task::JoinHandle<()>, Arc<Notify>)> {
    if std::env::var("TICO_SIDE_JOBS").as_deref() != Ok("1") {
        return None;
    }
    let jobs = match SideJobs::new(config.clone()) {
        Ok(j) => j,
        Err(e) => {
            log(&format!("Tico side jobs: {e}"));
            return None;
        }
    };
    let stop = Arc::new(Notify::new());
    Some((tokio::spawn(jobs.run(stop.clone())), stop))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_crashing_job_backs_off_and_one_that_ran_a_while_starts_over() {
        assert_eq!(backoff(0, 1.0, 4), (0, 5));
        let mut failures = 0;
        let mut delays = vec![];
        for _ in 0..7 {
            let (f, d) = backoff(1, 3.0, failures);
            failures = f;
            delays.push(d);
        }
        assert_eq!(delays, [10, 20, 40, 80, 160, 300, 300]);
        assert_eq!(backoff(1, 500.0, 6), (0, 10));
    }

    #[test]
    fn the_close_key_comes_from_the_environment_or_its_secrets_file() {
        let dir = std::env::temp_dir().join(format!("tico-close-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("secrets")).unwrap();
        assert_eq!(close_key(&dir), "");
        std::fs::write(dir.join("secrets/close-calls.env"), "# Close\nOTHER=1\n CLOSE_API_KEY = 'api_123' \n").unwrap();
        if std::env::var("CLOSE_API_KEY").is_err() {
            assert_eq!(close_key(&dir), "api_123");
        }
        std::fs::remove_dir_all(dir).ok();
    }
}
