//! What the heartbeat says about this computer (`runtime_readiness`, `preflight` and `readiness` in
//! the old Python runner). Only the checks the chat path needs: runtime install, version, sign-in and models for
//! Claude, Codex, Gemini (the Gemini CLI, and `agy` for Antigravity bots), Muse Code, Pi, Cursor and Grok Build, and per bot its repository, AGENT.md and bot.yaml. The server gates claims on these rows.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::config::Config;
use crate::env::{which, Env};
use crate::profiles::{self, Profile};
use crate::signin::{self, SignIn};
use crate::util::text_of;

pub const CLAUDE_MODELS: &[&str] = &["claude-fable-5-1", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"];
pub const NO_RUNTIME: &str = "No AI provider is chosen: the owner picks providers and a default model in Settings > Computers";

/// `run` in `env` (a profile's homes) when given. Err(true) is a timeout, Err(false) a command that did not start.
async fn run_env(argv: &[&str], timeout_s: u64, env: Option<&Env>) -> Result<(bool, String, String), bool> {
    let mut cmd = crate::isolation::command(argv[0]);
    cmd.args(&argv[1..]).stdin(std::process::Stdio::null()).kill_on_drop(true);
    if let Some(env) = env {
        cmd.env_clear().envs(env);
    }
    let out = tokio::time::timeout(Duration::from_secs(timeout_s), cmd.output()).await.map_err(|_| true)?.map_err(|_| false)?;
    Ok((out.status.success(), String::from_utf8_lossy(&out.stdout).into(), String::from_utf8_lossy(&out.stderr).into()))
}

fn executable(runtime: &str) -> Option<PathBuf> {
    which(&crate::harness_tools::executable_for(runtime), &std::env::var("PATH").unwrap_or_default())
}

pub fn codex_models(home: Option<PathBuf>) -> Vec<String> {
    let home = home.unwrap_or_else(|| {
        std::env::var("CODEX_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".codex"))
    });
    let Ok(text) = std::fs::read_to_string(home.join("models_cache.json")) else { return vec![] };
    let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    let mut slugs: Vec<String> = value["models"]
        .as_array()
        .map(|rows| rows.iter().filter_map(|r| r["slug"].as_str()).filter(|s| !s.is_empty()).map(String::from).collect())
        .unwrap_or_default();
    slugs.sort();
    slugs.dedup();
    slugs
}

/// One runtime's row: installed, signed in, version, models, controls, detail, goals, commands. With a
/// profile, every check runs in that profile's home and no shared key signs it in.
pub async fn runtime_readiness(runtime: &str, sign: &SignIn, client: &Client, assignments: &[Value], profile: Option<&Profile>) -> Map<String, Value> {
    let caps = crate::harness_tools::caps(runtime, "");
    let exe = if crate::harness_tools::runtimes().iter().any(|r| r == runtime) { executable(runtime) } else { None };
    let mut process: Env = std::env::vars().collect();
    if profile.is_some_and(|p| !p.share_operator) {
        for key in crate::harness_tools::api_key_env(runtime) {
            process.remove(&key);
        }
    }
    process.extend(crate::harness_tools::env_for(runtime));
    let env: Option<Env> = Some(profile.map_or_else(|| process.clone(), |p| p.environment(runtime, &process)));
    let env = env.as_ref();
    let mut version = String::new();
    let (authenticated, detail) = match &exe {
        None => ("missing", "Runtime executable is not on PATH".to_string()),
        Some(path) => {
            let state = sign_in_state(runtime, &caps, path, sign, client, assignments, profile, env).await;
            // The version probe runs with the manifest's [env] (an auto-updater's off switch) and no colour codes.
            let mut quiet = env.cloned().unwrap_or_else(crate::env::process_environment);
            quiet.extend(crate::harness_tools::env_for(runtime));
            quiet.insert("NO_COLOR".into(), "1".into());
            let p = path.to_string_lossy().to_string();
            if let Ok((true, out, err)) = run_env(&[&p, "--version"], 5, Some(&quiet)).await {
                let text = if out.trim().is_empty() { err } else { out };
                version = text.trim().lines().next().unwrap_or_default().chars().take(100).collect();
            }
            state
        }
    };
    // Signed in but unable to save a session (Muse Code's data folder): no turn could run, so not ready, and no
    // fallback hop picks it. `data_problem` tells it from a sign-in that could not be checked, so the Computers page
    // names the folder instead of asking for a sign-in.
    let data_problem = crate::hosts::data_problem(runtime, profile).filter(|_| authenticated == "ready");
    let (authenticated, detail) = match &data_problem {
        Some(problem) => ("failed", problem.clone()),
        None => (authenticated, detail),
    };
    let models: Vec<String> = if caps.model_list == "host" { crate::hosts::models(runtime, profile) } else { vec![] };
    let mut row = Map::new();
    // Which key signs the computer's own login in, by name and where it is kept; never its value.
    if let Some((name, source)) = sign.key_source(runtime).filter(|_| profile.is_none() && authenticated == "ready") {
        if detail == format!("Signed in with {name}") || detail.contains("API key") {
            row.insert("key".into(), json!({"name": name, "source": source}));
        }
    }
    // The account only beside a sign-in the check just confirmed; a signed-out login forgets it.
    let account_key = profile.map(|p| p.name.clone()).unwrap_or_default();
    if authenticated == "ready" && !caps.account.is_empty() {
        let stored = crate::accounts::without_keys(env.cloned().unwrap_or_else(crate::env::process_environment));
        crate::accounts::refresh_in_background(&account_key, runtime, stored);
        if let Some(account) = crate::accounts::account(&account_key, runtime) {
            row.insert("account".into(), account);
        }
    } else if authenticated == "missing" {
        crate::accounts::forget(&account_key, runtime);
        crate::profiles::forget_windows(&account_key, runtime);
    }
    row.insert("installed".into(), json!(exe.is_some()));
    row.insert("authenticated".into(), json!(authenticated));
    row.insert("version".into(), json!(version));
    row.insert("models".into(), json!(models));
    row.insert("controls".into(), if exe.is_some() { json!(["interrupt", "new-session"]) } else { json!([]) });
    row.insert("detail".into(), json!(detail));
    if let Some(problem) = data_problem.filter(|_| crate::fallback::server_takes_data_problem()) {
        row.insert("data_problem".into(), json!(problem));
    }
    row.extend(crate::goals::capabilities(runtime, exe.as_deref(), &version).await);
    row
}

/// Whether `runtime`'s CLI at `path` is signed in, and how, by its manifest's `sign_in_status` and key capabilities.
/// A computer key the CLI reads from each turn's environment (`api_key_in_turn`) wins over a stored login; a key the
/// CLI turns into a login (`api_key_login`) is logged in with when the probe says signed out; any other computer key
/// counts as the sign-in once the probe has said no. A profile is judged on its own login alone.
#[allow(clippy::too_many_arguments)]
async fn sign_in_state(
    runtime: &str,
    caps: &crate::harness_tools::Capabilities,
    path: &Path,
    sign: &SignIn,
    client: &Client,
    assignments: &[Value],
    profile: Option<&Profile>,
    env: Option<&Env>,
) -> (&'static str, String) {
    let status = &caps.sign_in_status;
    let name = crate::harness_tools::name_for(runtime);
    let missing = if status.missing_detail.is_empty() { format!("{name} login required") } else { status.missing_detail.clone() };
    let timed_out = if profile.is_some() { "unknown" } else { "failed" };
    let account_key = profile.map(|p| p.name.clone()).unwrap_or_default();
    let computer_key = caps.api_keys == "computer";
    // A key from the app is asked for again while it signs the runtime in, so one replaced or cleared there arrives.
    let from_app = |sign: &SignIn| sign.key_source(runtime).is_some_and(|(_, source)| source != "terminal");
    let mut changed = false;
    if computer_key && profile.is_none() && (from_app(sign) || (status.check != "command" && status.check != "json")) {
        changed = sign.team_model_keys(client, runtime).await;
    }
    // A key added for this computer in the app signs in a CLI that reads it on each turn (execute.rs turn_key), in
    // place of any login of its own.
    let own_key = computer_key && caps.api_key_login.is_empty() && sign.key_source(runtime).is_some_and(|(_, source)| source == "computer");
    if (caps.api_key_in_turn || own_key) && profile.is_none_or(|p| p.share_operator) {
        if profile.is_some() {
            sign.team_model_keys(client, runtime).await;
        }
        if let Some((key, _)) = sign.headless_secret(runtime) {
            return ("ready", format!("Signed in with {key}"));
        }
    }
    let p = path.to_string_lossy().to_string();
    let argv = |args: &[String]| -> Vec<String> { std::iter::once(p.clone()).chain(args.iter().cloned()).collect() };
    let timeout = if status.timeout_s > 0 { status.timeout_s } else { 15 };
    // Ok(Some(detail)) signed in; Ok(None) signed out; Err(true) timed out; Err(false) could not be checked.
    let probe = || async {
        let argv = argv(&status.args);
        let argv: Vec<&str> = argv.iter().map(String::as_str).collect();
        match status.check.as_str() {
            "command" => match run_env(&argv, timeout, env).await {
                Ok((ok, out, err)) => {
                    let output = format!("{out}\n{err}").trim().to_lowercase();
                    let signed = ok && output.contains(&status.ready) && (status.not_ready.is_empty() || !output.contains(&status.not_ready));
                    Ok(signed.then(|| if output.contains("api key") { "Signed in with an API key".to_string() } else { status.ready_detail.clone() }))
                }
                Err(e) => Err(e),
            },
            "json" => {
                let ran = run_env(&argv, timeout, env).await;
                match &ran {
                    Err(true) => Err(true),
                    _ => match ran.ok().and_then(|(_, out, _)| serde_json::from_str::<Value>(&out).ok()).filter(|v| v.is_object()) {
                        None => Err(false),
                        Some(s) if s[&status.field] == true => {
                            if caps.account == "status-json" {
                                match crate::accounts::from_status_json(&s) {
                                    Some(account) => crate::accounts::remember(&account_key, runtime, account),
                                    None => crate::accounts::forget(&account_key, runtime),
                                }
                            }
                            let method = text_of(&s[&status.method_field]);
                            Ok(Some(if method.is_empty() { status.ready_detail.clone() } else { format!("Signed in with {method}") }))
                        }
                        Some(_) => Ok(None),
                    },
                }
            }
            _ => Err(false),
        }
    };
    match status.check.as_str() {
        "bot-key" => return crate::hosts::bot_key_sign_in(runtime, assignments),
        "none" => return ("unknown", "Authentication cannot be checked without starting the runtime".into()),
        "file" => {
            // The host's login file in the config folder its turns run with (its own one, when it keeps one).
            let mut file_env = env.cloned().unwrap_or_else(crate::env::process_environment);
            if caps.own_config_home {
                crate::hosts::use_own_config_home(runtime, profile, &mut file_env);
            }
            return match crate::hosts::file_signed_in(runtime, &file_env) {
                Some(true) => ("ready", crate::hosts::file_sign_in_detail(runtime, &file_env).map(String::from).unwrap_or_else(|| status.ready_detail.clone())),
                None => ("failed", crate::hosts::file_problem(runtime, &file_env)
                    .unwrap_or_else(|| format!("{name} sign-in could not be checked (its login data is unreadable or unrecognized)"))),
                Some(false) => ("missing", missing),
            };
        }
        _ => {}
    }
    let mut result = probe().await;
    if profile.is_none() && computer_key {
        let signed = matches!(result, Ok(Some(_)));
        if !signed {
            changed |= sign.team_model_keys(client, runtime).await;
        }
        // A box with an API key and no browser: sign in once, for the bots that use this runtime, or with the key the
        // app gave this computer or every computer. A key that changed in the app replaces the login an older key
        // made, never a browser or subscription login. Never into a profile's own home.
        let by_key = matches!(&result, Ok(Some(detail)) if detail.to_lowercase().contains("api key"));
        if result.is_ok()
            && (!signed || (changed && by_key))
            && !caps.api_key_login.is_empty()
            && sign.headless_secret(runtime).is_some()
            && (from_app(sign) || signin::assigned(assignments, runtime))
        {
            if changed {
                sign.retry_key_login(runtime);
            }
            if sign.key_login(runtime, path).await {
                result = probe().await;
            }
        }
    }
    if let Ok(Some(detail)) = result {
        return ("ready", detail);
    }
    // A key in the environment, `_shared.env` or the company's file signs the CLI in on a headless box even when the
    // status says otherwise; a profile has only its own login.
    if computer_key && caps.api_key_login.is_empty() && profile.is_none() {
        if let Some((key, _)) = sign.headless_secret(runtime) {
            return ("ready", format!("Signed in with {key}"));
        }
    }
    match result {
        Err(true) => (timed_out, format!("{name} sign-in check timed out")),
        Err(false) => ("failed", format!("{name} sign-in could not be checked")),
        _ => ("missing", missing),
    }
}

fn order(row: &Value) -> usize {
    let a = text_of(&row["authenticated"]);
    profiles::SIGN_IN_ORDER.iter().position(|s| *s == a).unwrap_or(profiles::SIGN_IN_ORDER.len())
}

fn joined(rows: &Map<String, Value>) -> String {
    let parts: Vec<String> = rows
        .iter()
        .map(|(name, row)| if name.is_empty() { text_of(&row["detail"]) } else { format!("{name}: {}", text_of(&row["detail"])) })
        .collect();
    parts.join("; ").chars().take(500).collect()
}

/// Readiness per runtime, and per subscription profile for the profiles in use (service.py runtime_report): the
/// runtime row reports the profile that needs attention first and carries every profile's row under `profiles`.
/// A sign-in the provider refused on a real turn shows as `rejected` until its credentials change.
pub async fn runtime_report(config: &Config, sign: &SignIn, client: &Client, assignments: &[Value], computer_logins: bool) -> Map<String, Value> {
    let mut report = Map::new();
    for runtime in crate::harness_tools::runtimes() {
        let runtime = runtime.as_str();
        let mut used: std::collections::BTreeMap<String, Option<Profile>> = Default::default();
        if computer_logins {
            let own = profiles::computer_login(&config.data);
            used.insert(own.as_ref().map(|p| p.name.clone()).unwrap_or_default(), own);
        } else {
            for entry in assignments {
                let requested = text_of(&entry["profile"]);
                let profile = profiles::assignment_profile(&config.data, entry, false);
                if text_of(&entry["config"]["runtime"]) == runtime && (profile.is_some() || requested.is_empty()) {
                    used.entry(profile.as_ref().map(|p| p.name.clone()).unwrap_or_default()).or_insert(profile);
                }
            }
        }
        let mut targets = Map::new();
        if used.keys().all(|k| k.is_empty()) {
            targets.insert(String::new(), Value::Object(runtime_readiness(runtime, sign, client, assignments, None).await));
        } else {
            for (name, profile) in &used {
                targets.insert(name.clone(), Value::Object(runtime_readiness(runtime, sign, client, assignments, profile.as_ref()).await));
            }
        }
        for (name, status) in targets.iter_mut() {
            let profile = used.get(name).cloned().flatten();
            // A profile whose turns use the computer's own login on this runtime (`share_operator`): the server holds
            // that login once, not once per name (backend/login_holds.py canonical). Only to a server that keeps
            // login holds, since an older one refuses a field it does not know.
            if !name.is_empty() && crate::fallback::server_keeps_holds() && crate::fallback::login(runtime, "", profile.as_ref()).is_some_and(|l| l.is_empty()) {
                status.as_object_mut().unwrap().insert("shared_login".into(), json!(true));
            }
            if status["installed"] != true {
                continue;
            }
            // No bot in hand on the runtime's row. A bot-keyed runtime's own key is refused for one bot, so this row
            // shows no refusal for it: each refused bot's own row (preflight) says so, and the others stay ready.
            if let Some(r) = sign.rejection(runtime, profile.as_ref(), "") {
                let source = if name.is_empty() && sign.team_key_only(runtime) { "credentials" } else { "computer" };
                let detail: String = format!("Sign-in rejected: {}", r.reason).chars().take(500).collect();
                let s = status.as_object_mut().unwrap();
                s.insert("authenticated".into(), json!("rejected"));
                s.remove("data_problem");
                s.insert("rejected_at".into(), json!(r.at));
                s.insert("rejected_reason".into(), json!(r.reason));
                s.insert("credential_source".into(), json!(source));
                s.insert("detail".into(), json!(detail));
            }
        }
        let default = profiles::computer_login(&config.data).map(|p| p.name).unwrap_or_default();
        let own_name = if computer_logins { default.as_str() } else { "" };
        let own = crate::profiles::windows(own_name, runtime);
        let row = computer_row(targets, own, computer_logins);
        report.insert(runtime.into(), row);
    }
    report
}

/// Publish the active computer login's usage on its runtime row, even when its physical home has a name.
fn computer_row(targets: Map<String, Value>, own: Map<String, Value>, computer_logins: bool) -> Value {
    let mut row = combined(targets, own.clone());
    if computer_logins && row["authenticated"] == "ready" {
        row.as_object_mut().unwrap().extend(own);
    }
    row
}

/// One runtime's row from its logins' rows: the computer's own login alone, or the one that needs attention first with
/// every login's row under `profiles`. The own login's usage windows (`own`) go beside its ready sign-in, whichever
/// row is shown; a named profile's are in the profiles report instead.
fn combined(mut targets: Map<String, Value>, own: Map<String, Value>) -> Value {
    let own_ready = targets.get("").is_some_and(|r| r["authenticated"] == "ready");
    let own_key = targets.get("").and_then(|r| r.get("key")).cloned();
    let mut row = if targets.len() == 1 && targets.contains_key("") {
        targets.remove("").and_then(|r| r.as_object().cloned()).unwrap_or_default()
    } else {
        let worst = targets
            .iter()
            .min_by_key(|(name, row)| (row["authenticated"] != "rejected", if row["authenticated"] == "rejected" { 0 } else { order(row) }, (*name).clone()))
            .map(|(_, row)| row.clone())
            .unwrap_or_default();
        let mut row = worst.as_object().cloned().unwrap_or_default();
        row.insert("detail".into(), json!(joined(&targets)));
        row.insert("profiles".into(), Value::Object(targets));
        row
    };
    if own_ready {
        // The own login's key is shown whichever row is: it is the computer's, not a profile's.
        if let Some(key) = own_key {
            row.insert("key".into(), key);
        }
        row.extend(own);
    }
    Value::Object(row)
}

/// A bot's checkout: the configured repos entry, else `bot-<slug>` or the older `emp-<slug>` under projects_dir.
pub fn local_path(config: &Config, bot: &str) -> PathBuf {
    if let Some(p) = crate::sessions::shared_path(bot) {
        return p; // a branch of a bot with branches works in the original's checkout
    }
    if let Some(p) = config.repo_for(bot) {
        return p;
    }
    let root = config.projects_dir();
    for prefix in ["bot-", "emp-"] {
        if root.join(format!("{prefix}{bot}")).exists() {
            return root.join(format!("{prefix}{bot}"));
        }
    }
    root.join(format!("bot-{bot}"))
}

/// What an assigned bot needs that this runner does not run, each named in the bot's readiness problems: that
/// bot is not ready here, and the runner runs the others.
pub fn unsupported(entry: &Value) -> Vec<String> {
    let c = &entry["config"];
    let runtime = text_of(&c["runtime"]);
    let mut needs = vec![];
    let harness = text_of(&c["harness"]);
    if !harness.is_empty() && crate::appdriver::delivery(c).is_none() && !crate::harness_tools::is_harness_of(&runtime, &harness) {
        needs.push(format!("the {harness} harness"));
    }
    needs
}

pub fn manifest_path(folder: &Path) -> PathBuf {
    for name in ["bot.yaml", "employee.yaml"] {
        if folder.join(name).is_file() {
            return folder.join(name);
        }
    }
    folder.join("bot.yaml")
}

/// Read-only git in a bot's checkout, as runner git always runs (crate::safegit): no hooks or fsmonitor, no secrets.
async fn git(path: &Path, args: &[&str]) -> Option<(bool, String)> {
    let mut cmd = crate::isolation::command("git");
    cmd.args(crate::safegit::args(path))
        .args(args)
        .env_clear()
        .envs(crate::safegit::clean_environment(&crate::env::process_environment()))
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    let out = tokio::time::timeout(Duration::from_secs(5), cmd.output()).await.ok()?.ok()?;
    Some((out.status.success(), String::from_utf8_lossy(&out.stdout).into()))
}

/// Whether the server says this computer's company subscription signs `runtime` in for this bot (runners/assignments
/// `subscriptions`: {runtime: kind}).
fn subscribed(entry: &Value, runtime: &str) -> bool {
    entry["subscriptions"].get(runtime).and_then(Value::as_str).is_some_and(|k| !k.is_empty())
}

/// Each assigned bot's row: what blocks its turns here (`problems`) and what only warns.
pub async fn preflight(config: &Config, sign: &SignIn, assignments: &[Value], runtimes: &Map<String, Value>, computer_logins: bool) -> Vec<Map<String, Value>> {
    let mut rows = vec![];
    let grants = crate::hosts::gemini::grant_names();
    for entry in assignments {
        let bot = text_of(&entry["bot"]);
        let expected = &entry["config"];
        let runtime = text_of(&expected["runtime"]);
        let caps = crate::harness_tools::caps_of(expected);
        // A bot delivered through an app needs no repository, runtime or sign-in here; its app's state is the
        // readiness `appdriver` field, and a turn that cannot reach it fails saying why.
        let app = crate::appdriver::delivery(expected).is_some();
        let known = app || crate::harness_tools::runtimes().contains(&runtime);
        let path = crate::sessions::local_path(config, &bot, expected);
        let requested = if computer_logins { String::new() } else { text_of(&entry["profile"]) };
        let computer = text_of(&entry["computer_label"]);
        let profile = profiles::assignment_profile(&config.data, entry, computer_logins);
        let profile_name = profile.as_ref().map(|p| p.name.clone()).unwrap_or_default();
        // This bot's own subscription, not the worst of the machine's: a signed-out profile blocks its own bots only.
        let status = runtimes
            .get(&runtime)
            .and_then(|r| r["profiles"].get(&profile_name).cloned())
            .or_else(|| runtimes.get(&runtime).cloned())
            .unwrap_or(Value::Null);
        // A folder its turns can't write, from this bot's own login's row only: never another login's, as the
        // fallback above gives a bot whose subscription isn't here.
        let data_problem = runtimes
            .get(&runtime)
            .and_then(|r| match r["profiles"].as_object().filter(|p| !p.is_empty()) {
                Some(rows) => rows.get(&profile_name).cloned(),
                None => Some(r.clone()),
            })
            .filter(|_| requested.is_empty() || profile.is_some())
            .and_then(|r| r["data_problem"].as_str().map(String::from));
        // A bot-keyed runtime's key refused for this bot (signin.rs refusal_key); the runtime row never carries it.
        let refused = if signin::bot_keyed(&runtime) { sign.rejection(&runtime, profile.as_ref(), &bot) } else { None };
        let mut sign_in = status.get("authenticated").cloned().unwrap_or(json!("unknown"));
        let mut problems: Vec<String> = vec![];
        let mut warnings: Vec<String> = crate::readiness_extra::credential_warnings(&bot);
        if !requested.is_empty() && !profiles::covers(expected) {
            problems.push(format!("Subscription {requested} doesn't cover {runtime}"));
        }
        if !profiles::missing(&config.data, &requested).is_empty() {
            problems.push(profiles::subscription_problem(&config.data, assignments, &requested, false, &computer));
        }
        let repository_present = path.join("AGENT.md").is_file();
        if !repository_present && !app {
            problems.push(crate::product_files::problem(&bot).or_else(|| crate::fetchrepo::problem(&bot)).unwrap_or_else(|| "Missing bot repository or AGENT.md".into()));
        } else if repository_present {
            if caps.repo_permission_lint {
                warnings.extend(crate::readiness_extra::deny_warnings(&path));
            }
            if let Ok(text) = std::fs::read(path.join("AGENT.md")) {
                let lines = text.split(|b| *b == b'\n').count() - usize::from(text.ends_with(b"\n"));
                if lines > 150 {
                    warnings.push(format!("AGENT.md is {lines} lines; keep current rules under 150 and move dated history to memory/decisions.md"));
                }
            }
        }
        let signed_out = |detail: String| {
            if !requested.is_empty() {
                profiles::subscription_problem(&config.data, assignments, &requested, true, &computer)
            } else {
                detail
            }
        };
        if app {
            // Its app's state stands for install and sign-in (above).
        } else if runtime.is_empty() {
            problems.push(NO_RUNTIME.into());
        } else if !known {
            problems.push("Unsupported runtime".into());
        } else if !caps.readiness {
            // A variant with its own executable and sign-in, which the runtime's row does not prove; the real turn
            // checks the sign-in, as preflight makes no model call.
            if let Some(v) = crate::harness_tools::variant(&runtime, &text_of(&expected["harness"])) {
                if which(&v.executable, &std::env::var("PATH").unwrap_or_default()).is_none() {
                    problems.push(format!("{} CLI is not on PATH", v.name));
                }
            }
        } else if runtimes.get(&runtime).is_none_or(|r| r["installed"] != true) {
            let installing = crate::harness_tools::shared().and_then(|h| h.try_lock().ok()?.installing(&runtime));
            problems.push(installing.unwrap_or_else(|| "Runtime executable is not on PATH".into()));
        } else if subscribed(entry, &runtime) {
            // Its own or its team's subscription signs this runtime in at each turn (crate::model_subscriptions):
            // this computer's sign-in, missing or refused, does not decide. A missing one reads signed in; a refused one
            // stays as it is, because the claim check reads "ready" as this login being usable if the plan stops covering.
            if sign_in == "missing" {
                sign_in = json!("ready");
            }
        } else if let Some(problem) = &data_problem {
            // Signed in, but its turns can't save a session: the folder is the problem, not the sign-in.
            problems.push(problem.clone());
        } else if let Some(r) = refused {
            sign_in = json!("rejected");
            problems.push(signed_out(format!("Sign-in rejected: {}", r.reason).chars().take(500).collect()));
        } else if status["authenticated"] == "rejected" {
            problems.push(signed_out(text_of(&status["detail"])));
        } else if caps.sign_in_status.check == "bot-key" {
            // Each bot holds its own key on this runtime, so the runtime's row (missing while any bot lacks one)
            // does not decide for this bot: its own grant does, and another bot's missing key blocks nothing here.
            let problem = crate::hosts::bot_key_problem(&runtime, expected, &bot, &grants);
            sign_in = json!(if problem.is_some() { "missing" } else { "ready" });
            problems.extend(problem);
        } else if (status["authenticated"] == "missing" || status["authenticated"] == "failed")
            && !(caps.api_key_in_turn && crate::harness_tools::api_key_env(&runtime).iter().any(|key| crate::hosts::gemini::has_grant(&bot, key)))
        {
            // A turn's explicit credential grant is independent of its computer's stored subscription login.
            let d = text_of(&status["detail"]);
            let d = if d.is_empty() { "Runtime sign-in is not ready".to_string() } else { d };
            problems.push(signed_out(if profile_name.is_empty() { d } else { format!("{profile_name}: {d}") }));
        }
        for need in unsupported(entry) {
            problems.push(format!("This computer's runner does not run {need}; choose another harness for this bot"));
        }
        let mut configuration_valid = known;
        let manifest = manifest_path(&path);
        let (tools, mail_request): (Vec<Value>, Value) = (vec![], Value::Null);
        if repository_present && manifest.is_file() {
            configuration_valid = match std::fs::read_to_string(&manifest).map(|t| crate::manifest::parse(&t)) {
                Ok(Ok(Value::Object(declared))) => {
                    let cloud = expected["model_managed_by"] == "cloud";
                    let shared = text_of(&expected["shared_from"]);
                    let name = declared.get("name").map(text_of).filter(|n| !n.is_empty()).unwrap_or_else(|| bot.clone());
                    let same = |key: &str| {
                        cloud || declared.get(key).map(text_of).filter(|v| !v.is_empty()).is_none_or(|v| v == text_of(&expected[key]))
                    };
                    (name == bot || (!shared.is_empty() && name == shared)) && same("runtime") && same("model")
                }
                _ => false,
            };
            if !configuration_valid {
                problems.push("Configuration differs from server".into());
            }
        }
        let mut revision = String::new();
        let mut published = Value::Null;
        if repository_present && path.join(".git").exists() {
            if let Some((true, out)) = git(&path, &["rev-parse", "--short=12", "HEAD"]).await {
                revision = out.trim().chars().take(100).collect();
            }
            if let Some((ok, _)) = git(&path, &["rev-parse", "--abbrev-ref", "@{u}"]).await {
                published = json!(ok);
            }
        }
        let mut row = Map::new();
        row.insert("bot".into(), json!(bot));
        row.insert("repository".into(), json!(path.to_string_lossy()));
        row.insert("runtime".into(), json!(runtime));
        row.insert("profile".into(), json!(profile_name));
        row.insert("sign_in".into(), sign_in);
        if let Some(problem) = data_problem {
            row.insert("data_problem".into(), json!(problem));
        }
        row.insert("model".into(), json!(text_of(&expected["model"])));
        row.insert("ready".into(), json!(problems.is_empty()));
        row.insert("repository_present".into(), json!(repository_present));
        row.insert("repository_revision".into(), json!(revision));
        row.insert("published".into(), published);
        row.insert("configuration_valid".into(), json!(configuration_valid));
        row.insert("problems".into(), json!(problems));
        row.insert("tools".into(), json!(tools));
        row.insert("mail_request".into(), mail_request);
        row.insert("warnings".into(), json!(warnings));
        rows.push(row);
    }
    rows
}

/// The heartbeat's `readiness` document (backend/models.py RunnerReadiness).
pub fn document(runtimes: &Map<String, Value>, checks: &[Map<String, Value>], state_dir: &Path) -> Value {
    // The heartbeat contract forbids unknown fields, so the per-profile rows stay local; `detail` names each.
    let runtimes: Map<String, Value> = runtimes
        .iter()
        .map(|(name, row)| {
            let mut row = row.clone();
            if let Some(r) = row.as_object_mut() {
                r.remove("profiles");
                // Where bots run as their own user, whether this tool's turns also each run as their own leased user
                // ("own") or share the bot user ("bot"), which can enter every bot's checkout (`isolated_user`).
                if crate::isolation::enabled() && crate::fallback::server_takes_turn_user() {
                    let own = crate::harness_tools::caps(name, "").isolated_user && crate::turn_users::available();
                    r.insert("turn_user".into(), json!(if own { "own" } else { "bot" }));
                }
            }
            (name.clone(), row)
        })
        .collect();
    let mut bots = Map::new();
    for row in checks {
        let mut out = Map::new();
        for key in ["ready", "runtime", "model", "repository", "repository_present", "repository_revision", "configuration_valid", "problems", "profile", "sign_in", "data_problem"] {
            if let Some(v) = row.get(key) {
                out.insert(key.into(), v.clone());
            }
        }
        if out.get("sign_in") == Some(&json!("rejected")) {
            out.insert("sign_in".into(), json!("failed"));
        }
        let capability = &runtimes.get(&text_of(&row["runtime"])).cloned().unwrap_or(Value::Null);
        for key in ["goals", "commands"] {
            if let Some(v) = capability.get(key) {
                out.insert(key.into(), v.clone());
            }
        }
        // A bot's own skills and commands run like /compact where the harness reads them from the repository; a
        // harness too old for commands offers none.
        let builtins = capability["commands"].as_array().cloned().unwrap_or_default();
        if crate::harness_tools::caps(&text_of(&row["runtime"]), "").repo_commands && !builtins.is_empty() && row["repository_present"] == true && !text_of(&row["repository"]).is_empty() {
            out.insert("commands".into(), json!(crate::goals::with_repo_commands(&builtins, Path::new(&text_of(&row["repository"])))));
        }
        if !row["published"].is_null() {
            out.insert("published".into(), row["published"].clone());
        }
        // Declared access, no values (crate::declared_access), and what bot.yaml asks of its mail.
        for key in ["tools", "mail_request"] {
            if crate::util::truthy(row.get(key)) {
                out.insert(key.into(), row[key].clone());
            }
        }
        out.insert("warnings".into(), row.get("warnings").cloned().unwrap_or(json!([])));
        bots.insert(text_of(&row["bot"]), Value::Object(out));
    }
    // `worktree_waits`: a worktree the server reserves for a waiting task is prepared here while its bot is
    // idle (worktrees.rs prepare), so the server records a wait instead of refusing a task at its limit.
    let mut doc = json!({"schema_version": 1, "runtimes": runtimes, "bots": bots, "worktrees": true, "worktree_waits": true});
    // Bots run as their own user here, so the server may hand this computer a turn on a Tico model
    // (backend/models_preset.py); without it the server keeps such turns away (gateway_relay.rs refuses them).
    doc["isolated"] = json!(crate::isolation::enabled());
    // Not isolated, but a Tico cloud machine: one company's trust group, whose Gateway turns run on the relay alone
    // (gateway_relay.rs Separation::TrustGroup). Sent only to a server that takes the field.
    if let Some(trust) = trust_field(crate::gateway_relay::separation(), crate::fallback::server_takes_trust()) {
        doc["trust"] = json!(trust);
    }
    if let Some((total, free)) = disk_usage(state_dir) {
        doc["disk"] = json!({"total_bytes": total, "free_bytes": free});
    }
    doc
}

/// The readiness `trust` (backend/models.py StructuredReadiness): "cloud_machine" for a trust group, to a server that
/// takes it; isolation says itself (`isolated`).
fn trust_field(separation: crate::gateway_relay::Separation, server_takes: bool) -> Option<&'static str> {
    (server_takes && separation == crate::gateway_relay::Separation::TrustGroup).then_some("cloud_machine")
}

pub fn disk_usage(path: &Path) -> Option<(u64, u64)> {
    use std::os::unix::ffi::OsStrExt;
    let c = std::ffi::CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut s: libc::statvfs = unsafe { std::mem::zeroed() };
    // SAFETY: a valid C string and a zeroed struct the call fills.
    if unsafe { libc::statvfs(c.as_ptr(), &mut s) } != 0 {
        return None;
    }
    #[allow(clippy::unnecessary_cast)] // u32 on some platforms
    let frsize = s.f_frsize as u64;
    Some((s.f_blocks as u64 * frsize, s.f_bavail as u64 * frsize))
}

/// AGENT.md of each bot this computer hosts, once and then only when the file changes
/// (`changed_agent_instructions`). Versions are (mtime ns, size); None for a missing file.
#[allow(clippy::type_complexity)]
/// The AGENT.md each inbox bot hosted here runs from, for Mail (service.py mail_agent_instructions).
pub fn mail_agent_instructions(config: &Config, assignments: &[Value]) -> Map<String, Value> {
    let mine = config.runner_id();
    let mut result = Map::new();
    for entry in assignments {
        if !crate::util::truthy(entry.get("mail_agent")) || mine.is_empty() || text_of(&entry["runner_id"]) != mine {
            continue;
        }
        let bot = text_of(&entry["bot"]);
        let path = local_path(config, &bot).join("AGENT.md");
        match std::fs::symlink_metadata(&path).ok().filter(|m| m.is_file()) {
            None => {
                result.insert(bot, json!(""));
            }
            Some(m) if m.len() <= 100_000 => {
                result.insert(bot, json!(String::from_utf8_lossy(&std::fs::read(&path).unwrap_or_default())));
            }
            Some(_) => crate::util::log(&format!("Tico runner: {bot}: AGENT.md is too large to show in Mail")),
        }
    }
    result
}

pub fn changed_agent_instructions(
    config: &Config,
    assignments: &[Value],
    published: &std::collections::HashMap<String, Option<(i128, u64)>>,
) -> (Map<String, Value>, Vec<(String, Option<(i128, u64)>)>) {
    let mine = config.runner_id();
    let (mut result, mut versions) = (Map::new(), vec![]);
    for entry in assignments {
        if mine.is_empty() || text_of(&entry["runner_id"]) != mine {
            continue;
        }
        let bot = text_of(&entry["bot"]);
        let path = local_path(config, &bot).join("AGENT.md");
        let meta = std::fs::symlink_metadata(&path).ok().filter(|m| m.is_file());
        let (version, content) = match meta {
            None => (None, String::new()),
            Some(m) => {
                if m.len() > 100_000 {
                    continue;
                }
                use std::os::unix::fs::MetadataExt;
                let version = Some((m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128, m.len()));
                if published.get(&bot) == Some(&version) {
                    continue;
                }
                (version, String::from_utf8_lossy(&std::fs::read(&path).unwrap_or_default()).into_owned())
            }
        };
        if published.get(&bot) == Some(&version) {
            continue;
        }
        result.insert(bot.clone(), json!(content));
        versions.push((bot, version));
    }
    (result, versions)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_cloud_machine_says_trust_and_only_to_a_server_that_takes_it() {
        use crate::gateway_relay::Separation;
        assert_eq!(trust_field(Separation::TrustGroup, true), Some("cloud_machine"));
        // An older server forbids unknown readiness fields: it gets the heartbeat it knows (and holds Gateway work).
        assert_eq!(trust_field(Separation::TrustGroup, false), None);
        // Isolation is still `isolated`, never relabelled, and a plain computer claims nothing.
        assert_eq!(trust_field(Separation::Isolated, true), None);
        assert_eq!(trust_field(Separation::NoRelay, true), None);
    }

    #[test]
    fn a_runtime_a_subscription_signs_in_needs_no_sign_in_here() {
        let entry = serde_json::json!({"bot": "ops", "subscriptions": {"claude": "claude_token"}});
        assert!(subscribed(&entry, "claude"));
        assert!(!subscribed(&entry, "codex"));
        assert!(!subscribed(&serde_json::json!({"bot": "ops"}), "claude"));
    }

    fn map(v: Value) -> Map<String, Value> {
        v.as_object().cloned().unwrap()
    }

    #[test]
    fn computer_login_named_default_publishes_its_windows_and_folder_problem() {
        let own = map(json!({"weekly": {"used_percent": 45.0}, "five_hour": {"used_percent": 70.0}}));
        let targets = map(json!({"work": {"installed": true, "authenticated": "ready"}}));
        let row = computer_row(targets.clone(), own.clone(), true);
        assert_eq!(row["weekly"]["used_percent"], 45.0);
        assert_eq!(row["five_hour"]["used_percent"], 70.0);
        assert!(computer_row(targets, own.clone(), false).get("weekly").is_none());
        let row = computer_row(map(json!({"work": {"installed": true, "authenticated": "failed",
            "data_problem": "Sessions folder is not writable"}})), own, true);
        assert_eq!(row["data_problem"], "Sessions folder is not writable");
        assert!(row.get("weekly").is_none());
        let doc = document(&map(json!({"codex": row})), &[], Path::new("/tmp"));
        assert_eq!(doc["runtimes"]["codex"]["data_problem"], "Sessions folder is not writable");
    }

    #[test]
    fn the_own_logins_windows_stay_on_a_computer_whose_worst_row_is_a_named_profile() {
        let own = map(json!({"weekly": {"used_percent": 17.0}, "five_hour": {"used_percent": 53.0}}));
        let rows = |own_auth: &str, lab_auth: &str| {
            map(json!({"": {"installed": true, "authenticated": own_auth, "detail": "Signed in"},
                       "lab": {"installed": true, "authenticated": lab_auth, "detail": "Not signed in"}}))
        };
        // The AWS box: its own login runs most bots and is signed in; a named profile beside it is signed out.
        let row = combined(rows("ready", "missing"), own.clone());
        assert_eq!(row["authenticated"], "missing", "the row still shows the login that needs attention");
        assert_eq!((row["weekly"]["used_percent"].clone(), row["five_hour"]["used_percent"].clone()), (json!(17.0), json!(53.0)));
        // A rejected profile is shown first too.
        assert_eq!(combined(rows("ready", "rejected"), own.clone())["weekly"]["used_percent"], json!(17.0));
        // Own login the worst row, or alone: as before.
        assert_eq!(combined(rows("ready", "ready"), own.clone())["weekly"]["used_percent"], json!(17.0));
        assert_eq!(combined(map(json!({"": {"installed": true, "authenticated": "ready"}})), own.clone())["five_hour"]["used_percent"], json!(53.0));
        // Not beside an own login that is not signed in, nor on a computer whose bots all use named profiles.
        let row = combined(rows("missing", "ready"), own.clone());
        assert!(row.get("weekly").is_none() && row.get("five_hour").is_none());
        assert!(combined(map(json!({"lab": {"installed": true, "authenticated": "ready"}})), own).get("weekly").is_none());
    }

    #[test]
    fn the_own_logins_key_source_stays_whichever_row_is_shown() {
        let key = json!({"name": "OPENAI_API_KEY", "source": "computer"});
        let rows = map(json!({"": {"installed": true, "authenticated": "ready", "detail": "Signed in with an API key", "key": key},
                              "lab": {"installed": true, "authenticated": "missing", "detail": "Not signed in"}}));
        let row = combined(rows, Map::new());
        assert_eq!((row["authenticated"].clone(), row["key"].clone()), (json!("missing"), key.clone()));
        let alone = combined(map(json!({"": {"installed": true, "authenticated": "ready", "key": key}})), Map::new());
        assert_eq!(alone["key"], key);
    }

    #[tokio::test]
    async fn a_refused_bot_key_is_a_problem_for_that_bot_and_not_for_the_others_on_its_runtime() {
        let config = checkouts(&["readiness-alpha", "readiness-beta"]);
        let sign = SignIn::new(Path::new(&config.str("projects_dir")));
        sign.reject("pi", "401 invalid OPENROUTER_API_KEY", None, "readiness-alpha");
        let assignment = |bot: &str, model: &str| json!({"bot": bot, "config": {"runtime": "pi", "harness": "pi",
                                                                                  "model": model}});
        // beta runs the company's gateway model, which needs no key of its own, so only alpha's refusal can stop it.
        let assignments = vec![assignment("readiness-alpha", "openrouter/model"),
                               assignment("readiness-beta", "tico-mid-1.0")];
        let runtimes = map(json!({"pi": {"installed": true, "authenticated": "ready"}}));
        let rows = preflight(&config, &sign, &assignments, &runtimes, false).await;
        let (alpha, beta) = (row_of(&rows, "readiness-alpha"), row_of(&rows, "readiness-beta"));
        assert_eq!(alpha["ready"], json!(false));
        assert!(alpha["problems"].to_string().contains("Sign-in rejected"), "{:?}", alpha["problems"]);
        assert_eq!(beta["ready"], json!(true), "{:?}", beta["problems"]);
        let _ = std::fs::remove_dir_all(config.str("projects_dir"));
    }

    /// A config whose projects folder holds a checkout (an AGENT.md) for each bot.
    fn checkouts(bots: &[&str]) -> Config {
        let root = std::env::temp_dir().join(format!("tico-preflight-{}", uuid::Uuid::new_v4().simple()));
        for bot in bots {
            let path = root.join(format!("bot-{bot}"));
            std::fs::create_dir_all(&path).unwrap();
            std::fs::write(path.join("AGENT.md"), "# Bot\n").unwrap();
        }
        Config { path: PathBuf::new(), data: map(json!({"projects_dir": root.display().to_string()})) }
    }

    /// Each assigned bot's readiness row for `runtime`, with the runtime's row as its own sign-in check reports it.
    async fn bot_rows(config: &Config, assignments: &[Value], runtime: &str) -> Vec<Map<String, Value>> {
        let (state, detail) = crate::hosts::bot_key_sign_in(runtime, assignments);
        let mut runtimes = Map::new();
        runtimes.insert(runtime.into(), json!({"installed": true, "authenticated": state, "detail": detail}));
        let sign = SignIn::new(Path::new(&config.str("projects_dir")));
        preflight(config, &sign, assignments, &runtimes, false).await
    }

    fn row_of<'a>(rows: &'a [Map<String, Value>], bot: &str) -> &'a Map<String, Value> {
        rows.iter().find(|r| r["bot"] == bot).unwrap()
    }

    #[tokio::test]
    async fn a_bot_key_runtime_is_ready_per_bot_by_its_own_grant() {
        use crate::hosts::gemini::set_credential_names;
        let config = checkouts(&["has-key", "no-key", "gateway", "g-key", "g-none"]);
        let pi_bots = [
            json!({"bot": "has-key", "config": {"runtime": "pi", "harness": "pi", "model": "kimi-k3"}}),
            json!({"bot": "no-key", "config": {"runtime": "pi", "harness": "pi", "model": "kimi-k3"}}),
            json!({"bot": "gateway", "config": {"runtime": "pi", "harness": "pi", "model": "tico-mid-1.0"}}),
        ];
        let gemini_bots = [
            json!({"bot": "g-key", "config": {"runtime": "gemini", "harness": "gemini", "model": "gemini-3.7-flash"}}),
            json!({"bot": "g-none", "config": {"runtime": "gemini", "harness": "gemini", "model": "gemini-3.7-flash"}}),
        ];
        // The grants the server sent: one pi bot holds its OpenRouter key, one Gemini bot its Gemini key.
        set_credential_names(&json!({"has-key": ["OPENROUTER_API_KEY"], "g-key": ["GEMINI_API_KEY"]}));
        // One bot without its key marks the runtime missing, yet only that bot is not ready.
        // The Tico model needs no key of its own.
        assert_eq!(crate::hosts::bot_key_sign_in("pi", &pi_bots).0, "missing");
        let rows = bot_rows(&config, &pi_bots, "pi").await;
        let (has_key, no_key, gateway) = (row_of(&rows, "has-key"), row_of(&rows, "no-key"), row_of(&rows, "gateway"));
        assert_eq!((has_key["ready"].clone(), has_key["problems"].clone()), (json!(true), json!([])));
        assert_eq!(no_key["ready"], json!(false));
        assert_eq!(no_key["problems"], json!(["OpenRouter API key required"]));
        assert_eq!(no_key["sign_in"], json!("missing"));
        assert_eq!((gateway["ready"].clone(), gateway["problems"].clone()), (json!(true), json!([])));
        // The same for Gemini CLI bots.
        assert_eq!(crate::hosts::bot_key_sign_in("gemini", &gemini_bots).0, "missing");
        let rows = bot_rows(&config, &gemini_bots, "gemini").await;
        let (g_key, g_none) = (row_of(&rows, "g-key"), row_of(&rows, "g-none"));
        assert_eq!((g_key["ready"].clone(), g_key["problems"].clone()), (json!(true), json!([])));
        assert_eq!(g_none["ready"], json!(false));
        assert_eq!(g_none["problems"], json!(["Gemini API key required"]));
        // Once every bot holds its key, the runtimes and every bot are ready.
        set_credential_names(&json!({
            "has-key": ["OPENROUTER_API_KEY"], "no-key": ["OPENROUTER_API_KEY"],
            "g-key": ["GEMINI_API_KEY"], "g-none": ["GEMINI_API_KEY"]
        }));
        assert_eq!(crate::hosts::bot_key_sign_in("pi", &pi_bots).0, "ready");
        let pi_rows = bot_rows(&config, &pi_bots, "pi").await;
        let gemini_rows = bot_rows(&config, &gemini_bots, "gemini").await;
        for row in pi_rows.iter().chain(gemini_rows.iter()) {
            assert_eq!((row["ready"].clone(), row["problems"].clone()), (json!(true), json!([])), "{row:?}");
        }
        set_credential_names(&json!({}));
        let _ = std::fs::remove_dir_all(config.str("projects_dir"));
    }

    #[tokio::test]
    async fn a_bot_a_subscription_signs_in_reads_signed_in_and_one_without_it_still_reads_missing() {
        let config = checkouts(&["covered-bot", "bare-bot"]);
        let sign = SignIn::new(Path::new(&config.str("projects_dir")));
        let assignment = |bot: &str, subscriptions: Value| json!({"bot": bot, "subscriptions": subscriptions,
            "config": {"runtime": "claude", "harness": "claude", "model": "claude-sonnet-5"}});
        let assignments = vec![assignment("covered-bot", json!({"claude": "claude_token"})), assignment("bare-bot", json!({}))];
        // The computer's own Claude login is missing: only the company subscription covers covered-bot.
        let runtimes = map(json!({"claude": {"installed": true, "authenticated": "missing", "detail": "Not signed in"}}));
        let rows = preflight(&config, &sign, &assignments, &runtimes, false).await;
        let (covered, bare) = (row_of(&rows, "covered-bot"), row_of(&rows, "bare-bot"));
        assert_eq!((covered["ready"].clone(), covered["sign_in"].clone()), (json!(true), json!("ready")), "{covered:?}");
        assert_eq!((bare["ready"].clone(), bare["sign_in"].clone()), (json!(false), json!("missing")), "{bare:?}");
        // A refused login stays visible: the server's claim check must not read it as usable once the plan ends.
        let runtimes = map(json!({"claude": {"installed": true, "authenticated": "rejected", "detail": "Refused"}}));
        let rows = preflight(&config, &sign, &assignments, &runtimes, false).await;
        let covered = row_of(&rows, "covered-bot");
        assert_eq!((covered["ready"].clone(), covered["sign_in"].clone()), (json!(true), json!("rejected")), "{covered:?}");
        let _ = std::fs::remove_dir_all(config.str("projects_dir"));
    }
}
