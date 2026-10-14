//! A bot's own repository onto the computer it is assigned to (service.py fetch_repository, github_access and
//! git_credentials.clone_repository / empty_clone / discard_partial): a bot placed on or moved to a computer that
//! has never held it has no checkout, and nothing else puts one there. A failure is remembered for FETCH_RETRY_S,
//! so every heartbeat is not a clone, and its cause is the bot's readiness problem.

use std::path::Path;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::env::Env;
use crate::gitrun::{self, Fault};
use crate::runner::Runner;
use crate::util::{log, text_of, truthy};

pub const FETCH_RETRY_S: u64 = 120;

type Note = (String, Instant, String);
static NOTES: std::sync::Mutex<Option<std::collections::HashMap<String, Note>>> = std::sync::Mutex::new(None);

fn note(bot: &str) -> Option<Note> {
    NOTES.lock().unwrap().as_ref().and_then(|m| m.get(bot).cloned())
}

/// The last fetch's problem for a bot still without its checkout, for its readiness row.
pub fn problem(bot: &str) -> Option<String> {
    note(bot).map(|n| n.2).filter(|p| !p.is_empty())
}

/// Remove what a failed clone left in a folder that did not exist before it: otherwise every later try finds a
/// folder that "already exists and is not empty" and the checkout never arrives.
pub fn discard_partial(path: &Path, fresh: bool) {
    if fresh && path.is_dir() && !path.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink()) {
        let _ = std::fs::remove_dir_all(path);
    }
}

fn empty_dir(path: &Path) -> bool {
    std::fs::read_dir(path).map(|mut d| d.next().is_none()).unwrap_or(false)
}

/// Whether `path` holds only a clone of a repository with no commits: what `git clone` leaves (and calls a
/// success) when GitHub's repository is still empty.
pub async fn empty_clone(path: &Path) -> bool {
    if !path.join(".git").is_dir() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(path) else { return false };
    if entries.flatten().any(|e| e.file_name() != ".git") {
        return false;
    }
    let env = crate::safegit::clean_environment(&crate::env::process_environment());
    let (Ok(head), Ok(refs)) = (
        gitrun::git(path, &env, &["rev-parse", "--verify", "--quiet", "HEAD"], 10).await,
        gitrun::git(path, &env, &["for-each-ref", "--count=1"], 10).await,
    ) else {
        return false;
    };
    !head.ok() && refs.ok() && refs.stdout.trim().is_empty()
}

/// (state, detail): `cloned`, or `failed` and why. Only a missing or empty folder is cloned into.
pub async fn clone_repository(path: &Path, repository: &str, env: &Env, url: Option<&str>) -> (&'static str, String) {
    if path.exists() && (!path.is_dir() || !empty_dir(path)) {
        return ("failed", format!("{} already exists here and is not an empty folder; left as it is", path.file_name().unwrap_or_default().to_string_lossy()));
    }
    let fresh = !path.exists() || empty_dir(path);
    let env = crate::safegit::environment(env);
    let wanted = url.map(str::to_string).unwrap_or_else(|| format!("https://github.com/{repository}.git"));
    let done = match gitrun::bare(&env, &["clone", "--quiet", &wanted, &path.display().to_string()], 180).await {
        Ok(o) => o,
        Err(Fault::Timeout(t)) => {
            discard_partial(path, fresh);
            return ("failed", format!("git clone timed out after {t} seconds; a large repository or a slow network needs longer"));
        }
        Err(Fault::Stopped) => {
            discard_partial(path, fresh);
            return ("failed", gitrun::STOPPED.into());
        }
        Err(Fault::Io(_)) => {
            discard_partial(path, fresh);
            return ("failed", "OSError".into());
        }
    };
    if done.ok() {
        if empty_clone(path).await {
            discard_partial(path, fresh);
            return ("failed", format!("GitHub's {repository} is empty: the computer that built the bot has not published its history yet; it is cloned once it has"));
        }
        return ("cloned", String::new());
    }
    discard_partial(path, fresh);
    let stderr = if done.stderr.trim().is_empty() { &done.stdout } else { &done.stderr };
    let text = Some(crate::redact::git_failure(stderr, &crate::redact::env_secrets(&env), 240)).filter(|t| !t.is_empty()).unwrap_or_else(|| format!("exit {}", done.code));
    if ["uthentication", "403", "Permission to", "denied to"].iter().any(|w| text.contains(w)) {
        return ("failed", format!("GitHub refused the bot's token: {text}"));
    }
    if text.to_lowercase().contains("not found") {
        return ("failed", format!("GitHub has no repository {repository} that this token can see: {text}"));
    }
    ("failed", text)
}

impl Runner {
    /// (environment, problem) for talking to this bot's GitHub repository: the bot's scoped token, or the
    /// machine's own git access when no GitHub App is connected.
    pub async fn github_access(&self, bot: &str, entry: &Value) -> Result<Env, String> {
        let granted = match self.tokens.git(&self.client, bot).await {
            Ok(g) => g,
            Err(e) => return Err(crate::gitcred::token_problem(&e, &text_of(&entry["repository"])).chars().take(400).collect()),
        };
        let mut env = crate::env::process_environment();
        let token = text_of(&granted["token"]);
        if truthy(granted.get("configured")) && !token.is_empty() {
            env.extend(crate::repositories::token_env(&token));
            let repository = text_of(&granted["repository"]);
            if !repository.is_empty() {
                env.insert(crate::gitcred::REPOSITORY_KEY.into(), repository);
            }
        }
        Ok(env)
    }

    /// Clone the bot's repository when it is assigned here and has no checkout. The readiness problem to show,
    /// or "" when the checkout is now here (or an independent bot has no link).
    pub async fn fetch_repository(&self, bot: &str, entry: &Value, path: &Path) -> String {
        let mut repository = text_of(&entry["repository"]);
        let config = &entry["config"];
        let personal = repository.is_empty() && truthy(config.get("shared_from"));
        if personal {
            repository = Some(crate::sessions::shared_repository_url(config)).filter(|u| !u.is_empty()).unwrap_or_else(|| text_of(&config["repo"]));
        }
        if repository.is_empty() && !personal {
            return String::new();
        }
        let key = format!("{repository}\u{0}{}\u{0}{}", path.display(), entry["generation"]);
        let last = note(bot);
        if let Some((k, at, problem)) = &last {
            if *k == key && at.elapsed() < Duration::from_secs(FETCH_RETRY_S) {
                return problem.clone();
            }
        }
        if path.join(".git").exists() && empty_clone(path).await {
            discard_partial(path, true); // an older runner's clone of the still-empty repository
        }
        let mut problem = if path.join(".git").exists() {
            format!("{repository} is checked out here but has no AGENT.md")
        } else if personal {
            let detail = crate::sessions::clone_shared(path, config, &crate::env::process_environment()).await;
            if detail.is_empty() {
                log(&format!("Tico runner: {bot} cloned from {repository} to {}", path.display()));
                if path.join("AGENT.md").is_file() { String::new() } else { format!("{repository} was cloned but has no AGENT.md") }
            } else {
                let named = if repository.is_empty() { text_of(&config["shared_from"]) } else { repository.clone() };
                format!("Could not clone the original repository {named}: {detail}")
            }
        } else {
            match self.github_access(bot, entry).await {
                Ok(env) => match clone_repository(path, &repository, &env, None).await {
                    ("cloned", _) => {
                        log(&format!("Tico runner: {bot} cloned from {repository} to {}", path.display()));
                        self.publish_notes.lock().unwrap().remove(bot);
                        if path.join("AGENT.md").is_file() { String::new() } else { format!("{repository} was cloned but has no AGENT.md") }
                    }
                    (_, detail) => format!("Could not clone {repository}: {detail}"),
                },
                Err(why) => format!("Cannot fetch {repository} onto this computer: {why}"),
            }
        };
        problem = problem.chars().take(500).collect();
        if !problem.is_empty() && last.as_ref().is_none_or(|(_, _, p)| *p != problem) {
            log(&format!("Tico runner: {bot}: {problem}"));
        }
        NOTES.lock().unwrap().get_or_insert_with(Default::default).insert(bot.to_string(), (key, Instant::now(), problem.clone()));
        problem
    }

    /// Before readiness: each bot assigned here without AGENT.md gets its repository fetched; the problem found
    /// goes to its readiness row (`problem`) in place of the plain "missing" line.
    pub async fn fetch_missing(&self, assignments: &[Value]) {
        let mine = self.config.runner_id();
        for entry in assignments {
            let bot = text_of(&entry["bot"]);
            if mine.is_empty() || text_of(&entry["runner_id"]) != mine {
                continue;
            }
            let path = crate::sessions::local_path(&self.config, &bot, &entry["config"]);
            if path.join("AGENT.md").is_file() {
                continue;
            }
            self.fetch_repository(&bot, entry, &path).await;
        }
    }
}
