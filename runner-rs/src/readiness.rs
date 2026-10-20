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
    let env: Option<Env> = profile.map(|p| p.environment(runtime, &std::env::vars().collect()));
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
            let mut file_env: Env = std::env::vars().collect();
            if caps.own_config_home {
                crate::hosts::use_own_config_home(runtime, profile, &mut file_env);
            }
            return match crate::hosts::file_signed_in(runtime, &file_env) {
                Some(true) => ("ready", status.ready_detail.clone()),
                None => ("failed", format!("{name} sign-in could not be checked (its config folder is not readable)")),
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
pub async fn runtime_report(config: &Config, sign: &SignIn, client: &Client, assignments: &[Value]) -> Map<String, Value> {
    let mut report = Map::new();
    for runtime in crate::harness_tools::runtimes() {
        let runtime = runtime.as_str();
        let mut used: std::collections::BTreeMap<String, Option<Profile>> = Default::default();
        for entry in assignments {
            let requested = text_of(&entry["profile"]);
            let profile = profiles::select(&config.data, Some(&text_of(&entry["bot"])), Some(&requested));
            if text_of(&entry["config"]["runtime"]) == runtime && (profile.is_some() || requested.is_empty()) {
                used.entry(profile.as_ref().map(|p| p.name.clone()).unwrap_or_default()).or_insert(profile);
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
            if let Some(r) = sign.rejection(runtime, profile.as_ref()) {
                let source = if name.is_empty() && sign.team_key_only(runtime) { "credentials" } else { "computer" };
                let detail: String = format!("Sign-in rejected: {}", r.reason).chars().take(500).collect();
                let s = status.as_object_mut().unwrap();
                s.insert("authenticated".into(), json!("rejected"));
                s.insert("rejected_at".into(), json!(r.at));
                s.insert("rejected_reason".into(), json!(r.reason));
                s.insert("credential_source".into(), json!(source));
                s.insert("detail".into(), json!(detail));
            }
        }
        let own = crate::profiles::windows("", runtime);
        report.insert(runtime.into(), combined(targets, own));
    }
    report
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

/// Each assigned bot's row: what blocks its turns here (`problems`) and what only warns.
pub async fn preflight(config: &Config, assignments: &[Value], runtimes: &Map<String, Value>) -> Vec<Map<String, Value>> {
    let mut rows = vec![];
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
        let requested = text_of(&entry["profile"]);
        let computer = text_of(&entry["computer_label"]);
        let profile = profiles::select(&config.data, Some(&bot), Some(&requested));
        let profile_name = profile.as_ref().map(|p| p.name.clone()).unwrap_or_default();
        // This bot's own subscription, not the worst of the machine's: a signed-out profile blocks its own bots only.
        let status = runtimes
            .get(&runtime)
            .and_then(|r| r["profiles"].get(&profile_name).cloned())
            .or_else(|| runtimes.get(&runtime).cloned())
            .unwrap_or(Value::Null);
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
        } else if status["authenticated"] == "rejected" {
            problems.push(signed_out(text_of(&status["detail"])));
        } else if status["authenticated"] == "missing" || status["authenticated"] == "failed" {
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
        row.insert("sign_in".into(), status.get("authenticated").cloned().unwrap_or(json!("unknown")));
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
            }
            (name.clone(), row)
        })
        .collect();
    let mut bots = Map::new();
    for row in checks {
        let mut out = Map::new();
        for key in ["ready", "runtime", "model", "repository", "repository_present", "repository_revision", "configuration_valid", "problems", "profile", "sign_in"] {
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
    if let Some((total, free)) = disk_usage(state_dir) {
        doc["disk"] = json!({"total_bytes": total, "free_bytes": free});
    }
    doc
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

    fn map(v: Value) -> Map<String, Value> {
        v.as_object().cloned().unwrap()
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
}
