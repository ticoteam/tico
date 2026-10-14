//! What the heartbeat says about this computer (`runtime_readiness`, `preflight` and `readiness` in
//! runner/service.py). Only the checks the chat path needs: runtime install, version, sign-in and models for
//! Claude, Codex, Gemini (the Gemini CLI, and `agy` for Antigravity bots) and Muse Code, and per bot its repository, AGENT.md and bot.yaml. The server gates claims on these rows.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::config::Config;
use crate::env::{which, Env};
use crate::profiles::{self, Profile};
use crate::signin::{self, SignIn};
use crate::util::text_of;

pub const RUNTIMES: &[&str] = &["codex", "grok", "claude", "gemini", "pi", "cursor", "musecode"];
/// The runtimes this runner can drive; the rest report as not installed so no bot of theirs is sent here.
pub const DRIVEN: &[&str] = &["claude", "codex", "gemini", "musecode"];
pub const CLAUDE_MODELS: &[&str] = &["claude-fable-5-1", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"];
pub const NO_RUNTIME: &str = "No AI provider is chosen: the owner picks providers and a default model in Settings > AI providers";
const CLAUDE_AUTH_TIMEOUT_S: u64 = 30;

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
    // Muse Code's program is `muse` (harness_tools.executable_for once its manifest ships).
    let program = if runtime == "musecode" { crate::hosts::musecode::EXECUTABLE.to_string() } else { crate::harness_tools::executable_for(runtime) };
    which(&program, &std::env::var("PATH").unwrap_or_default())
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
    let exe = if DRIVEN.contains(&runtime) { executable(runtime) } else { None };
    let env: Option<Env> = profile.map(|p| p.environment(runtime, &std::env::vars().collect()));
    let env = env.as_ref();
    let authenticated: &str;
    let detail: String;
    let mut version = String::new();
    let timed_out = if profile.is_some() { "unknown" } else { "failed" };
    match &exe {
        None if !DRIVEN.contains(&runtime) => {
            authenticated = "missing";
            detail = format!("This runner does not run {runtime} yet");
        }
        None => {
            authenticated = "missing";
            detail = "Runtime executable is not on PATH".into();
        }
        Some(path) => {
            let p = path.to_string_lossy().to_string();
            if runtime == "codex" {
                let mut status = run_env(&[&p, "login", "status"], 8, env).await;
                let signed = |s: &Result<(bool, String, String), bool>| s.as_ref().is_ok_and(|(ok, out, err)| *ok && format!("{out}\n{err}").to_lowercase().contains("logged in"));
                // A box with an API key and no browser: sign in once, for the bots that use Codex, or with the
                // key the company gave every computer. Never into a profile's own home.
                if status.is_ok() && !signed(&status) && profile.is_none() {
                    sign.team_model_keys(client, "codex").await;
                    if sign.headless_secret("codex").is_some()
                        && (sign.team_key_only("codex") || signin::assigned(assignments, "codex"))
                        && sign.codex_key_login(path).await
                    {
                        status = run_env(&[&p, "login", "status"], 8, env).await;
                    }
                }
                match status {
                    Ok((ok, out, err)) => {
                        let output = format!("{out}\n{err}").trim().to_lowercase();
                        if ok && output.contains("logged in") {
                            authenticated = "ready";
                            detail = if output.contains("api key") { "Signed in with an API key" } else { "Signed in with ChatGPT" }.into();
                        } else {
                            authenticated = "missing";
                            detail = "Codex login required".into();
                        }
                    }
                    Err(true) => {
                        authenticated = timed_out;
                        detail = "Codex sign-in check timed out".into();
                    }
                    Err(false) => {
                        authenticated = "failed";
                        detail = "Codex sign-in could not be checked".into();
                    }
                }
            } else if runtime == "gemini" {
                // The Gemini CLI signs in with each bot's own GEMINI_API_KEY grant; Antigravity-only bots need none.
                let (a, d) = crate::hosts::gemini::sign_in(assignments);
                authenticated = a;
                detail = d;
            } else if runtime == "musecode" {
                // Muse Code keeps a Meta-account login in auth.json (`muse login`) and has no status command, so the
                // file's presence is the sign-in; its contents are never read. META_API_KEY wins over it.
                // Turns run on Tico's own Muse folder (musecode::config_home), so that is where the login is looked for.
                let shared_key = profile.is_none_or(|p| p.share_operator);
                if profile.is_none() {
                    sign.team_model_keys(client, "musecode").await;
                }
                let mut env: Env = std::env::vars().collect();
                env.remove("MUSE_AUTH_PATH");
                env.insert("XDG_CONFIG_HOME".into(), crate::hosts::musecode::config_home(profile).display().to_string());
                let signed = crate::hosts::musecode::signed_in(&crate::hosts::musecode::auth_file(&env));
                if shared_key && sign.headless_secret("musecode").is_some() {
                    authenticated = "ready";
                    detail = "Signed in with META_API_KEY".into();
                } else if signed == Some(true) {
                    authenticated = "ready";
                    detail = "Signed in with a Meta account".into();
                } else if signed.is_none() {
                    authenticated = "failed";
                    detail = "Muse sign-in could not be checked (its config folder is not readable)".into();
                } else {
                    authenticated = "missing";
                    detail = "Muse sign-in required (sign in from Tico) or META_API_KEY".into();
                }
            } else {
                let status = run_env(&[&p, "auth", "status", "--json"], CLAUDE_AUTH_TIMEOUT_S, env).await;
                let parsed: Option<Value> = status.as_ref().ok().and_then(|(_, out, _)| serde_json::from_str::<Value>(out).ok()).filter(|v| v.is_object());
                if !parsed.as_ref().is_some_and(|s| s["loggedIn"] == true) && profile.is_none() {
                    sign.team_model_keys(client, "claude").await;
                }
                // A key in the environment, `_shared.env` or the company's file signs the CLI in on a headless box
                // even when `auth status` says otherwise; a profile has only its own login.
                let token = if profile.is_none() { sign.headless_secret("claude").map(|(name, _)| name) } else { None };
                if let Some(s) = parsed.as_ref().filter(|s| s["loggedIn"] == true) {
                    authenticated = "ready";
                    let method = text_of(&s["authMethod"]);
                    detail = format!("Signed in with {}", if method.is_empty() { "Claude".into() } else { method });
                } else if let Some(name) = token {
                    authenticated = "ready";
                    detail = format!("Signed in with {name}");
                } else if status == Err(true) {
                    authenticated = timed_out;
                    detail = "Claude sign-in check timed out".into();
                } else if parsed.is_none() {
                    authenticated = "failed";
                    detail = "Claude sign-in could not be checked".into();
                } else {
                    authenticated = "missing";
                    detail = "Claude login required".into();
                }
            }
            let quiet: Option<Env> = (runtime == "musecode").then(|| {
                let mut e = env.cloned().unwrap_or_else(crate::env::process_environment);
                e.extend(crate::hosts::musecode::QUIET.iter().map(|(k, v)| (k.to_string(), v.to_string())));
                e
            });
            if let Ok((true, out, err)) = run_env(&[&p, "--version"], 5, quiet.as_ref().or(env)).await {
                let text = if out.trim().is_empty() { err } else { out };
                version = text.trim().lines().next().unwrap_or_default().chars().take(100).collect();
            }
        }
    }
    let models: Vec<String> = match runtime {
        "codex" => codex_models(profile.and_then(|p| p.home("codex"))),
        "claude" => CLAUDE_MODELS.iter().map(|s| s.to_string()).collect(),
        "gemini" => crate::hosts::gemini::MODELS.iter().map(|s| s.to_string()).collect(),
        "musecode" => crate::hosts::musecode::MODELS.iter().map(|s| s.to_string()).collect(),
        _ => vec![],
    };
    let mut row = Map::new();
    row.insert("installed".into(), json!(exe.is_some()));
    row.insert("authenticated".into(), json!(authenticated));
    row.insert("version".into(), json!(version));
    row.insert("models".into(), json!(models));
    row.insert("controls".into(), if exe.is_some() { json!(["interrupt", "new-session"]) } else { json!([]) });
    row.insert("detail".into(), json!(detail));
    row.extend(crate::goals::capabilities(runtime, exe.as_deref(), &version).await);
    row
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
    let mut names: Vec<&str> = RUNTIMES.to_vec();
    names.sort();
    for runtime in names {
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
        let row = if targets.len() == 1 && targets.contains_key("") {
            targets.remove("").unwrap()
        } else {
            let worst = targets
                .iter()
                .min_by_key(|(name, row)| (row["authenticated"] != "rejected", if row["authenticated"] == "rejected" { 0 } else { order(row) }, (*name).clone()))
                .map(|(_, row)| row.clone())
                .unwrap_or(Value::Null);
            let mut row = worst.as_object().cloned().unwrap_or_default();
            row.insert("detail".into(), json!(joined(&targets)));
            row.insert("profiles".into(), Value::Object(targets));
            Value::Object(row)
        };
        report.insert(runtime.into(), row);
    }
    report
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

/// What an assigned bot needs that this runner does not do yet, each named for the log and the bot's
/// readiness problems. A bot with any of these stays on the Python runner.
pub fn unsupported(entry: &Value) -> Vec<String> {
    let c = &entry["config"];
    let runtime = text_of(&c["runtime"]);
    let mut needs = vec![];
    if !runtime.is_empty() && !DRIVEN.contains(&runtime.as_str()) {
        needs.push(format!("the {runtime} runtime"));
    }
    let harness = text_of(&c["harness"]);
    if !harness.is_empty() && harness != runtime && !(runtime == "gemini" && harness == "antigravity") {
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
        if !repository_present {
            problems.push(crate::fetchrepo::problem(&bot).unwrap_or_else(|| "Missing bot repository or AGENT.md".into()));
        } else {
            if runtime == "claude" {
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
        if runtime.is_empty() {
            problems.push(NO_RUNTIME.into());
        } else if !RUNTIMES.contains(&runtime.as_str()) {
            problems.push("Unsupported runtime".into());
        } else if !DRIVEN.contains(&runtime.as_str()) {
            // Named below with the other features this runner lacks.
        } else if runtime == "gemini" && expected["harness"] == "antigravity" {
            // Antigravity has its own executable and Google sign-in, which a Gemini CLI key does not prove; the real
            // turn checks the sign-in, as preflight makes no model call.
            if executable("agy").is_none() {
                problems.push("Antigravity CLI is not on PATH".into());
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
            problems.push(format!("This computer's runner does not do {need} yet; run it on the Python runner for this bot"));
        }
        let mut configuration_valid = RUNTIMES.contains(&runtime.as_str());
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
        // A Claude Code bot's own skills and commands run like /compact; a harness too old for commands offers none.
        let builtins = capability["commands"].as_array().cloned().unwrap_or_default();
        if row["runtime"] == "claude" && !builtins.is_empty() && row["repository_present"] == true && !text_of(&row["repository"]).is_empty() {
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
    let mut doc = json!({"schema_version": 1, "runtimes": runtimes, "bots": bots, "worktrees": true});
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
