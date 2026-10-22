//! Built-in bots (the assistant, BotOps, Librarian, CEO Bot and the starters onboarding made) follow the
//! catalog the release ships (service.py bootstrap and refresh_product_files). This binary carries no templates:
//! the Python helper does the work, as `python -m runner --config <path> catalog` (runner/catalog_job.py),
//! the way side jobs run (crate::sidejobs). A bot assigned here without a repository is set up from its template
//! before its repository is fetched; once per start of this runner, before a built-in bot's turn, the product files
//! its template changed are brought up and committed. A failure is a log line, never a failed turn.
//!
//! Task worktrees sync from the upstream, so a refresh commit is pushed before a task gets its worktree: a task
//! lane's turn waits for that push (crate::sessions), a main-lane turn pushes it with its own work at the end.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;

use crate::config::Config;
use crate::fetchrepo::FETCH_RETRY_S;
use crate::util::{log, text_of};

/// Registrations from before the catalog carry no template: these slugs are their own (runner/catalog_job.py BOOTSTRAP_TEMPLATES).
const BOOTSTRAP_SLUGS: &[&str] = &["coo", "botops", "librarian", "goal-manager"];
const ASK_TIMEOUT: Duration = Duration::from_secs(300);

static REFRESHED: Mutex<Option<HashSet<String>>> = Mutex::new(None);
static UNPUSHED: Mutex<Option<HashSet<String>>> = Mutex::new(None);
/// Per bot without a repository: the generation asked about, when, and the problem the set-up found.
type Tried = (String, Instant, String);
static TRIED: Mutex<Option<HashMap<String, Tried>>> = Mutex::new(None);

/// Whether the catalog may know this bot; any other is never sent to Python.
pub fn could_be_built_in(bot: &str, config: &Value) -> bool {
    !text_of(&config["template"]).is_empty() || BOOTSTRAP_SLUGS.contains(&bot)
}

/// The set-up's problem for a bot still without its repository, for its readiness row.
pub fn problem(bot: &str) -> Option<String> {
    TRIED.lock().unwrap().as_ref().and_then(|m| m.get(bot)).map(|t| t.2.clone()).filter(|p| !p.is_empty())
}

pub fn unpushed(bot: &str) -> bool {
    UNPUSHED.lock().unwrap().as_ref().is_some_and(|s| s.contains(bot))
}

/// The bot's checkout was pushed: its refreshed product files are upstream now.
pub fn pushed(bot: &str) {
    if let Some(s) = UNPUSHED.lock().unwrap().as_mut() {
        s.remove(bot);
    }
}

/// One request to `python -m runner catalog`; its answer, or None (logged) when Python could not give one.
async fn ask(config: &Config, request: &Value) -> Option<Value> {
    let mut cmd = tokio::process::Command::new(crate::env::python());
    cmd.args(["-m", "runner", "--config"]).arg(&config.path).arg("catalog").current_dir(crate::env::root())
        .env_remove("TICO_SIDE_JOBS").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit()).kill_on_drop(true);
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => {
            log(&format!("Tico runner: could not start Python for the bot catalog ({e})"));
            return None;
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(request.to_string().as_bytes()).await;
    }
    let output = match tokio::time::timeout(ASK_TIMEOUT, child.wait_with_output()).await {
        Ok(Ok(output)) => output,
        Ok(Err(e)) => {
            log(&format!("Tico runner: the bot catalog job failed ({e})"));
            return None;
        }
        Err(_) => {
            log("Tico runner: the bot catalog job did not finish in 5 minutes");
            return None;
        }
    };
    let text = String::from_utf8_lossy(&output.stdout);
    let answer = text.lines().rev().find(|l| !l.trim().is_empty()).and_then(|l| serde_json::from_str::<Value>(l).ok());
    if !output.status.success() || answer.is_none() {
        log(&format!("Tico runner: the bot catalog job failed ({})", output.status));
    }
    answer.filter(Value::is_object)
}

/// The built-in bots assigned here with no AGENT.md yet, each asked about at most every FETCH_RETRY_S (or when its
/// placement changes): `bootstrap` sets them up from the catalog unless GitHub holds their history, which
/// crate::fetchrepo clones next.
pub async fn bootstrap_missing(config: &Config, candidates: &[Value]) {
    let mine = config.runner_id();
    let mut due = vec![];
    {
        let mut tried = TRIED.lock().unwrap();
        let tried = tried.get_or_insert_with(HashMap::new);
        for entry in candidates {
            let bot = text_of(&entry["bot"]);
            let path = crate::sessions::local_path(config, &bot, &entry["config"]);
            if mine.is_empty() || text_of(&entry["runner_id"]) != mine || path.join("AGENT.md").is_file() {
                tried.remove(&bot);
                continue;
            }
            if !could_be_built_in(&bot, &entry["config"]) {
                continue;
            }
            let generation = entry["generation"].to_string();
            if tried.get(&bot).is_some_and(|t| t.0 == generation && t.1.elapsed() < Duration::from_secs(FETCH_RETRY_S)) {
                continue;
            }
            let problem = tried.get(&bot).map(|t| t.2.clone()).unwrap_or_default();
            tried.insert(bot.clone(), (generation, Instant::now(), problem));
            due.push(json!({"bot": bot, "entry": entry, "path": path.display().to_string(), "action": "bootstrap"}));
        }
    }
    if due.is_empty() {
        return;
    }
    let Some(answer) = ask(config, &json!({"bots": due})).await else { return };
    let mut tried = TRIED.lock().unwrap();
    let tried = tried.get_or_insert_with(HashMap::new);
    for (bot, row) in answer.as_object().into_iter().flatten() {
        if let Some(t) = tried.get_mut(bot) {
            t.2 = text_of(&row["problem"]);
        }
    }
}

/// Once per start of this runner, before a built-in bot's turn in its checkout: the product files its template
/// changed are written and committed there, and the bot stays `unpushed` until a push of the checkout succeeds.
pub async fn refresh(config: &Config, bot: &str, bot_config: &Value, path: &Path) {
    if !REFRESHED.lock().unwrap().get_or_insert_with(HashSet::new).insert(bot.to_string()) || !could_be_built_in(bot, bot_config) {
        return;
    }
    let request = json!({"bots": [{"bot": bot, "entry": {"bot": bot, "config": bot_config}, "path": path.display().to_string(), "action": "refresh"}]});
    let Some(answer) = ask(config, &request).await else { return };
    if answer[bot]["changed"].as_array().is_some_and(|c| !c.is_empty()) {
        UNPUSHED.lock().unwrap().get_or_insert_with(HashSet::new).insert(bot.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_templated_bot_or_a_built_in_slug_is_sent_to_the_catalog() {
        assert!(could_be_built_in("botops", &json!({})));
        assert!(could_be_built_in("coo", &Value::Null));
        assert!(could_be_built_in("sales-1", &json!({"template": "sales"})));
        assert!(!could_be_built_in("ops", &json!({"runtime": "claude"})));
    }
}
