//! The readiness rows beyond runtime sign-in and the checkout (service.py preflight and readiness): reserved-credential and Claude Code deny warnings, the GitHub
//! sign-in and browser launch probes (runner/tool_probes.py), the harness report, recent errors, and the
//! fields an older server refused, held back for ten minutes.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::util::text_of;

pub const PUBLISH_WARNING: &str = "GitHub history not published: ";
const DENIED_WARNING: &str = "Claude Code denies what AGENT.md asks for: ";
const DENIES_WARNING: &str = "Claude Code settings deny: ";
/// Readiness fields an older server may refuse; each is left out for ten minutes after a refusal.
pub const OPTIONAL: &[&str] = &["worktrees", "harnesses", "mail_key", "shared_env", "recent_errors", "container_exec", "github_auth", "browser_launch", "disk"];

// ------------------------------------------------------------------------------------- credential grants

fn grants() -> &'static Mutex<HashMap<String, Vec<String>>> {
    static G: OnceLock<Mutex<HashMap<String, Vec<String>>>> = OnceLock::new();
    G.get_or_init(Default::default)
}

/// The variable names each bot's vault grants hold (`runner-credential-grants`), read on a full maintain.
pub async fn fetch_grants(client: &Client) {
    let Ok(reply) = client.get("runner-credential-grants").await else { return };
    crate::hosts::gemini::set_credential_names(&reply["bots"]);
    let mut rows = HashMap::new();
    for (bot, names) in reply["bots"].as_object().into_iter().flatten() {
        rows.insert(bot.clone(), names.as_array().into_iter().flatten().map(text_of).collect());
    }
    *grants().lock().unwrap() = rows;
}

pub fn held(bot: &str) -> Vec<String> {
    grants().lock().unwrap().get(bot).cloned().unwrap_or_default()
}

pub fn credential_warnings(bot: &str) -> Vec<String> {
    held(bot)
        .into_iter()
        .filter(|k| crate::env::reserved_credential(k))
        .map(|k| format!("Credential {k} is left out of every turn: the name is reserved. Give it another variable name in Credentials"))
        .collect()
}

// ------------------------------------------------------------------------------------ Claude Code denies

/// The `gh` and `git` prefixes a bot's `.claude/settings.json` denies, sorted (service.py claude_denies).
pub fn claude_denies(path: &Path) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(path.join(".claude/settings.json")) else { return vec![] };
    let Ok(value) = serde_json::from_str::<Value>(&text) else { return vec![] };
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"^Bash\(((?:gh|git)(?:[ :*][^)]*)?)\)$").unwrap());
    let set: BTreeSet<String> = value["permissions"]["deny"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|rule| rule.as_str())
        .filter_map(|rule| r.captures(rule.trim()).map(|c| c[1].trim_end_matches(['*', ':', ' ']).split_whitespace().collect::<Vec<_>>().join(" ")))
        .collect();
    set.into_iter().collect()
}

/// The `gh` commands AGENT.md tells the bot to run that its settings deny (service.py claude_denied).
pub fn claude_denied(path: &Path) -> Vec<String> {
    let Ok(text) = std::fs::read(path.join("AGENT.md")) else { return vec![] };
    let text = String::from_utf8_lossy(&text);
    static R: OnceLock<regex::Regex> = OnceLock::new();
    let r = R.get_or_init(|| regex::Regex::new(r"`(gh [a-z-]+ [a-z-]+)").unwrap());
    let named: BTreeSet<String> = r
        .captures_iter(&text)
        .filter(|c| {
            let before = &text[..c.get(0).unwrap().start()];
            !before.ends_with("denies ") && !before.ends_with("deny ")
        })
        .map(|c| c[1].to_string())
        .collect();
    let prefixes: Vec<String> = claude_denies(path).into_iter().filter(|p| p.starts_with("gh")).collect();
    named.into_iter().filter(|c| prefixes.iter().any(|p| c == p || c.starts_with(&format!("{p} ")))).collect()
}

pub fn deny_warnings(path: &Path) -> Vec<String> {
    let mut out = vec![];
    let denied = claude_denied(path);
    if !denied.is_empty() {
        out.push(format!("{DENIED_WARNING}{}. Edit .claude/settings.json in its repository", denied.join(", ")));
    }
    let denies = claude_denies(path);
    if !denies.is_empty() {
        out.push(format!("{DENIES_WARNING}{}", denies.join(", ")).chars().take(500).collect());
    }
    out
}

// ---------------------------------------------------------------------------------------------- probes

const EVERY: Duration = Duration::from_secs(900);
const FAILING_EVERY: Duration = Duration::from_secs(300);
const GITHUB_LIMIT: Duration = Duration::from_secs(15);
const BROWSER_LIMIT_S: u64 = 30;

/// The last result of one probe, refreshed in the background (container_probe.ContainerProbe).
#[derive(Default)]
struct Probe {
    last: Mutex<Option<Value>>,
    at: Mutex<Option<Instant>>,
    running: AtomicBool,
}

impl Probe {
    fn due(&self) -> bool {
        let failing = self.last.lock().unwrap().as_ref().is_some_and(|l| l["ok"] == false);
        let every = if failing { FAILING_EVERY } else { EVERY };
        self.at.lock().unwrap().is_none_or(|at| at.elapsed() >= every)
    }

    fn report<F, Fut>(self: &Arc<Self>, check: F) -> Option<Value>
    where
        F: Fn() -> Fut + Send + Sync + 'static,
        Fut: std::future::Future<Output = Option<Value>> + Send,
    {
        if std::env::var("TICO_RUNNER_CONTAINER_PROBE").as_deref() == Ok("off") {
            return None;
        }
        if !self.running.load(Ordering::SeqCst) && self.due() {
            *self.at.lock().unwrap() = Some(Instant::now());
            self.running.store(true, Ordering::SeqCst);
            let me = self.clone();
            tokio::spawn(async move {
                let mut result = check().await;
                let was_failing = me.last.lock().unwrap().as_ref().is_some_and(|l| l["ok"] == false);
                if result.as_ref().is_some_and(|r| r["ok"] == false) && !was_failing {
                    result = check().await; // only two failures in a row are reported
                }
                *me.last.lock().unwrap() = result;
                me.running.store(false, Ordering::SeqCst);
            });
        }
        self.last.lock().unwrap().clone()
    }
}

fn probes() -> &'static (Arc<Probe>, Arc<Probe>) {
    static P: OnceLock<(Arc<Probe>, Arc<Probe>)> = OnceLock::new();
    P.get_or_init(Default::default)
}

fn result(ok: bool, started: Instant, error: &str) -> Value {
    let now = crate::util::iso(&json!(crate::util::epoch_s().floor()));
    json!({"ok": ok, "seconds": (started.elapsed().as_secs_f64() * 10.0).round() / 10.0,
           "error": error.chars().take(300).collect::<String>(), "checked_at": text_of(&now)})
}

/// Whether a GitHub App token minted for a bot hosted here signs in (tool_probes.github).
async fn github(client: Client, bots: Vec<String>) -> Option<Value> {
    let started = Instant::now();
    for bot in bots {
        let Ok(granted) = client.post("github/token", &json!({"bot": bot}), None).await else { continue };
        if granted["configured"] != true {
            return None;
        }
        let token = text_of(&granted["token"]);
        if token.is_empty() {
            continue;
        }
        let http = reqwest::Client::builder().timeout(GITHUB_LIMIT).build().ok()?;
        let answer = http
            .get("https://api.github.com/installation/repositories?per_page=1")
            .header("Authorization", format!("token {token}"))
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "tico-runner")
            .send()
            .await
            .ok()?;
        let status = answer.status().as_u16();
        if (200..300).contains(&status) {
            return Some(result(true, started, ""));
        }
        if status != 401 {
            return None; // rate limits and outages are not a sign-in problem
        }
        let said = answer.json::<Value>().await.map(|v| text_of(&v["message"])).unwrap_or_default();
        let said = if said.is_empty() { "Bad credentials".to_string() } else { said };
        return Some(result(false, started, &format!("{bot}: GitHub answered 401 {said}")));
    }
    None
}

/// Playwright's Chromium executables, newest first (tool_probes.browsers).
pub fn browsers() -> Vec<PathBuf> {
    let set = std::env::var("PLAYWRIGHT_BROWSERS_PATH").unwrap_or_default();
    let root = if set.is_empty() || set == "0" {
        let home = PathBuf::from(std::env::var("HOME").unwrap_or_default());
        home.join(if cfg!(target_os = "macos") { "Library/Caches/ms-playwright" } else { ".cache/ms-playwright" })
    } else {
        PathBuf::from(set)
    };
    let patterns: &[(&str, &str)] = &[
        ("chromium_headless_shell-", "chrome-headless-shell"),
        ("chromium_headless_shell-", "headless_shell"),
        ("chromium-", "chrome"),
        ("chromium-", "Chromium.app/Contents/MacOS/Chromium"),
        ("chromium-", "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
    ];
    let entries = |dir: &Path| -> Vec<PathBuf> { std::fs::read_dir(dir).map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default() };
    let mut found = vec![];
    for (prefix, rest) in patterns {
        for top in entries(&root).into_iter().filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().starts_with(prefix))) {
            for inner in entries(&top) {
                let path = inner.join(rest);
                if crate::env::which(&path.to_string_lossy(), "").is_some() {
                    found.push(path);
                }
            }
        }
    }
    found.sort();
    found.reverse();
    found
}

/// Whether the newest installed Chromium starts headless (tool_probes.browser).
async fn browser() -> Option<Value> {
    let found = browsers();
    let exe = found.first()?;
    let started = Instant::now();
    let mut cmd = tokio::process::Command::new(exe);
    cmd.args(["--headless", "--no-sandbox", "--disable-gpu", "--dump-dom", "about:blank"])
        .env_clear()
        .envs(crate::env::process_environment())
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    let out = match tokio::time::timeout(Duration::from_secs(BROWSER_LIMIT_S), cmd.output()).await {
        Err(_) => return Some(result(false, started, &format!("the browser did not start within {BROWSER_LIMIT_S} s"))),
        Ok(Err(e)) => return Some(result(false, started, &e.to_string())),
        Ok(Ok(out)) => out,
    };
    if out.status.success() {
        return Some(result(true, started, ""));
    }
    let err = String::from_utf8_lossy(&out.stderr);
    let text = if err.trim().is_empty() { String::from_utf8_lossy(&out.stdout).into_owned() } else { err.into_owned() };
    let lines: Vec<&str> = text.trim().lines().filter(|l| !l.trim().is_empty()).collect();
    let reason = lines
        .iter()
        .find(|l| l.contains("shared librar"))
        .or(lines.last())
        .map(|l| l.trim().to_string())
        .unwrap_or_else(|| format!("exit {}", out.status.code().unwrap_or(-1)));
    Some(result(false, started, &reason))
}

// ------------------------------------------------------------------------------------------ document

/// Add what `readiness.document` leaves out: per-bot tools, mail request and publish warnings, the probes,
/// harnesses and recent errors; then drop what an older server refused in the last ten minutes.
pub async fn extend(doc: &mut Value, runtimes: &Map<String, Value>, checks: &[Map<String, Value>], notes: &HashMap<String, String>, client: &Client, hosted: Vec<String>) {
    let held_back = crate::lanes::held_back;
    if let Some(bots) = doc["bots"].as_object_mut() {
        for row in checks {
            let bot = text_of(&row["bot"]);
            let Some(out) = bots.get_mut(&bot).and_then(|v| v.as_object_mut()) else { continue };
            if !held_back("readiness.tools") && row.get("tools").and_then(|t| t.as_array()).is_some_and(|t| !t.is_empty()) {
                out.insert("tools".into(), row["tools"].clone());
            }
            if !held_back("readiness.mail_request") && row.get("mail_request").is_some_and(|m| m.is_object()) {
                out.insert("mail_request".into(), row["mail_request"].clone());
            }
            if let Some(note) = notes.get(&bot).filter(|n| !n.is_empty()) {
                if let Some(w) = out.get_mut("warnings").and_then(|w| w.as_array_mut()) {
                    w.push(json!(format!("{PUBLISH_WARNING}{note}")));
                }
            }
            if held_back("readiness.repo_commands") {
                if let Some(builtins) = runtimes.get(&text_of(&row["runtime"])).map(|r| r["commands"].clone()) {
                    if out.contains_key("commands") {
                        out.insert("commands".into(), builtins);
                    }
                }
            }
            if held_back("readiness.bot_profiles") {
                out.remove("profile");
                out.remove("sign_in");
            }
        }
    }
    let (gh, br) = probes();
    let client = client.clone();
    if let Some(v) = gh.report(move || github(client.clone(), hosted.clone())) {
        doc["github_auth"] = v;
    }
    if let Some(v) = br.report(browser) {
        doc["browser_launch"] = v;
    }
    if let Some(h) = crate::harness_tools::report(runtimes).await {
        doc["harnesses"] = h;
    }
    let recent = crate::util::recent_errors();
    if !recent.is_empty() {
        doc["recent_errors"] = json!(recent);
    }
    if held_back("readiness.goals") {
        for section in ["runtimes", "bots", "harnesses"] {
            for row in doc[section].as_object_mut().into_iter().flat_map(|m| m.values_mut()) {
                if let Some(r) = row.as_object_mut() {
                    r.remove("goals");
                    r.remove("commands");
                }
            }
        }
    }
    if let Some(d) = doc.as_object_mut() {
        for field in OPTIONAL {
            if held_back(&format!("readiness.{field}")) {
                d.remove(*field);
            }
        }
    }
}

/// What to leave out of the readiness document after a heartbeat's 422 (service.py _report_heartbeat), as a
/// `readiness.<name>` hold, or None when the refusal is not about an optional readiness field.
pub fn refused(detail: &str, readiness: &Value) -> Option<String> {
    let extra = detail.to_lowercase().contains("extra");
    let bots: Vec<&Map<String, Value>> = readiness["bots"].as_object().into_iter().flat_map(|m| m.values()).filter_map(|v| v.as_object()).collect();
    let any = |key: &str| bots.iter().any(|r| r.contains_key(key));
    if (detail.contains("goals") || detail.contains("commands")) && detail.contains("Extra inputs") {
        let has = ["runtimes", "bots", "harnesses"].iter().any(|s| {
            readiness[*s].as_object().into_iter().flat_map(|m| m.values()).any(|r| r.get("goals").is_some() || r.get("commands").is_some())
        });
        if has {
            return Some("readiness.goals".into());
        }
    }
    static CMD: OnceLock<regex::Regex> = OnceLock::new();
    if CMD.get_or_init(|| regex::Regex::new(r"bots\.[^. :;]+\.commands").unwrap()).is_match(detail) && any("commands") {
        return Some("readiness.repo_commands".into());
    }
    static PROF: OnceLock<regex::Regex> = OnceLock::new();
    let prof = PROF.get_or_init(|| regex::Regex::new(r"readiness\.(?:StructuredReadiness\.)?bots\.[^. :;]+\.(?:profile|sign_in)(?:[.: ;]|$)").unwrap());
    if extra && prof.is_match(detail) {
        return Some("readiness.bot_profiles".into());
    }
    if detail.contains("mail_request") && any("mail_request") {
        return Some("readiness.mail_request".into());
    }
    if detail.contains(".tools") && any("tools") {
        return Some("readiness.tools".into());
    }
    if !extra {
        return None;
    }
    let present: Vec<&str> = OPTIONAL.iter().copied().filter(|f| readiness.get(*f).is_some()).collect();
    for field in &present {
        let r = regex::Regex::new(&format!(r"readiness\.(?:StructuredReadiness\.)?{field}(?:[.: ;]|$)")).ok()?;
        if r.is_match(detail) {
            return Some(format!("readiness.{field}"));
        }
    }
    if !detail.contains("readiness.") {
        if let Some(field) = present.first() {
            return Some(format!("readiness.{field}"));
        }
        if any("tools") {
            return Some("readiness.tools".into());
        }
    }
    None
}

/// Take a held-back readiness field out of a heartbeat body about to be sent again.
pub fn drop_held(body: &mut Value) {
    let readiness = &mut body["readiness"];
    for field in OPTIONAL {
        if crate::lanes::held_back(&format!("readiness.{field}")) {
            if let Some(r) = readiness.as_object_mut() {
                r.remove(*field);
            }
        }
    }
    let goals = crate::lanes::held_back("readiness.goals");
    for section in ["runtimes", "bots", "harnesses"] {
        for row in readiness[section].as_object_mut().into_iter().flat_map(|m| m.values_mut()).filter_map(|v| v.as_object_mut()) {
            if goals {
                row.remove("goals");
                row.remove("commands");
            }
            if section == "bots" {
                for (hold, keys) in [("tools", &["tools"][..]), ("mail_request", &["mail_request"][..]), ("bot_profiles", &["profile", "sign_in"][..])] {
                    if crate::lanes::held_back(&format!("readiness.{hold}")) {
                        for k in keys {
                            row.remove(*k);
                        }
                    }
                }
            }
        }
    }
    if crate::lanes::held_back("readiness.repo_commands") {
        let builtins: HashMap<String, Value> = readiness["runtimes"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(k, v)| (k.clone(), v["commands"].clone()))
            .collect();
        for row in readiness["bots"].as_object_mut().into_iter().flat_map(|m| m.values_mut()).filter_map(|v| v.as_object_mut()) {
            if row.contains_key("commands") {
                let b = builtins.get(&text_of(&row["runtime"])).cloned().unwrap_or(json!([]));
                row.insert("commands".into(), b);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denies_and_refusals() {
        let dir = std::env::temp_dir().join(format!("tico-deny-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join(".claude")).unwrap();
        std::fs::write(dir.join(".claude/settings.json"), r#"{"permissions": {"deny": ["Bash(gh pr merge:*)", "Bash(git push --force)", "Read(x)"]}}"#).unwrap();
        std::fs::write(dir.join("AGENT.md"), "Run `gh pr merge --squash` when green. It denies `gh pr create x`.\n").unwrap();
        assert_eq!(claude_denies(&dir), vec!["gh pr merge", "git push --force"]);
        assert_eq!(claude_denied(&dir), vec!["gh pr merge"]);
        let _ = std::fs::remove_dir_all(&dir);
        let r = json!({"recent_errors": ["x"], "bots": {"a": {"tools": []}}});
        assert_eq!(refused("readiness.recent_errors: Extra inputs are not permitted", &r).as_deref(), Some("readiness.recent_errors"));
        assert_eq!(refused("body: extra fields", &r).as_deref(), Some("readiness.recent_errors"));
        assert_eq!(refused("readiness.bots.a.tools.0.service: too long", &r).as_deref(), Some("readiness.tools"));
    }
}
