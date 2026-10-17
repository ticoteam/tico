//! Subscription profiles (runner/profiles.py): one directory on this machine per provider login, named in
//! runner.json (`profiles`, `default_profile`, `bot_profiles`). A turn on a profile runs with that profile's
//! HOME (Claude) or CODEX_HOME (Codex); `share_operator` keeps the operator's own logins.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::config::Config;
use crate::env::Env;
use crate::util::{text_of, truthy};

/// Worst first: a runner row reports the profile that needs attention, not the healthy one.
pub const SIGN_IN_ORDER: &[&str] = &["failed", "missing", "unknown", "ready"];

/// The variable that points `runtime` at a profile's own login folder (`profile_home`), when it has one.
pub fn home_var(runtime: &str) -> Option<String> {
    Some(crate::harness_tools::caps(runtime, "").profile_home).filter(|v| !v.is_empty())
}

/// The command a person runs in a terminal to sign `runtime` in (its executable and `terminal_login`), when it has one.
pub fn login_command(runtime: &str) -> Option<Vec<String>> {
    let args = crate::harness_tools::caps(runtime, "").terminal_login;
    (!args.is_empty()).then(|| std::iter::once(crate::harness_tools::executable_for(runtime)).chain(args).collect())
}

pub fn valid_name(name: &str) -> bool {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").unwrap()).is_match(name) && name.len() <= 80
}

/// An assigned subscription that cannot run this turn here (SubscriptionUnavailable).
#[derive(Clone, Debug)]
pub struct Unavailable {
    pub problem: String,
    pub profile: String,
    pub runtime: String,
}

impl Unavailable {
    pub fn detail(&self) -> Value {
        json!({"profile": self.profile, "runtime": self.runtime, "problem": self.problem})
    }
}

pub fn covers(config: &Value) -> bool {
    crate::harness_tools::caps_of(config).subscription_profiles
}

#[derive(Clone, Debug, PartialEq)]
pub struct Profile {
    pub name: String,
    pub directory: PathBuf,
    pub share_operator: bool,
}

fn expand(dir: &str) -> PathBuf {
    match dir.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(rest),
        None if dir == "~" => PathBuf::from(std::env::var("HOME").unwrap_or_default()),
        None => PathBuf::from(dir),
    }
}

impl Profile {
    pub fn new(name: &str, dir: &str, share_operator: bool) -> Profile {
        Profile { name: name.into(), directory: expand(dir), share_operator }
    }

    /// Where this runtime keeps its login, or None when it uses the operator's own.
    pub fn home(&self, runtime: &str) -> Option<PathBuf> {
        (!self.share_operator).then(|| self.directory.join(runtime))
    }

    /// `env` with `runtime`'s home pointed at this profile.
    pub fn environment(&self, runtime: &str, env: &Env) -> Env {
        let mut env = env.clone();
        if let (Some(home), Some(var)) = (self.home(runtime), home_var(runtime)) {
            env.insert(var, home.display().to_string());
        }
        env
    }

    /// The binding a session key carries, so a thread begun on one login is never resumed on another
    /// (service.py: sha256(json.dumps([str(directory), share_operator]))).
    pub fn binding(&self) -> String {
        let text = format!("[{}, {}]", Value::String(self.directory.display().to_string()), if self.share_operator { "true" } else { "false" });
        sha256_hex(text.as_bytes())
    }
}

/// The server-requested profile, or the local assignment and default when none is requested. None means the
/// requested profile is not on this computer, or no local profile is configured.
pub fn select(config: &Map<String, Value>, bot: Option<&str>, requested: Option<&str>) -> Option<Profile> {
    let profiles = config.get("profiles").filter(|p| p.is_object()).cloned().unwrap_or(json!({}));
    let mut name = bot.and_then(|b| config.get("bot_profiles").and_then(|m| m.get(b))).map(text_of).filter(|n| !n.is_empty());
    if let Some(r) = requested.filter(|r| !r.is_empty()) {
        let entry = &profiles[r];
        if !entry.is_object() || !truthy(entry.get("dir")) {
            return None;
        }
        name = Some(r.to_string());
    }
    let name = name.unwrap_or_else(|| config.get("default_profile").map(text_of).unwrap_or_default());
    let entry = &profiles[&name];
    if name.is_empty() || !entry.is_object() || !truthy(entry.get("dir")) {
        return None;
    }
    Some(Profile::new(&name, &text_of(&entry["dir"]), truthy(entry.get("share_operator"))))
}

pub fn missing(config: &Map<String, Value>, requested: &str) -> String {
    let entry = config.get("profiles").and_then(|p| p.get(requested));
    if !requested.is_empty() && !entry.is_some_and(|e| e.is_object() && truthy(e.get("dir"))) {
        format!("profile {requested} not on this computer")
    } else {
        String::new()
    }
}

/// "Subscription X isn't (signed in) on <computer>" (service.py subscription_problem).
pub fn subscription_problem(config: &Map<String, Value>, assignments: &[Value], name: &str, signed_out: bool, computer: &str) -> String {
    let label = [computer.to_string(), config.get("label").map(text_of).unwrap_or_default()]
        .into_iter()
        .chain(assignments.iter().map(|r| text_of(&r["computer_label"])))
        .find(|l| !l.is_empty())
        .unwrap_or_else(|| "this computer".into());
    if signed_out { format!("Subscription {name} isn't signed in on {label}") } else { format!("Subscription {name} isn't on {label}") }
}

/// The profile this turn runs on (service.py turn_profile): None for the operator's own logins. A profile the
/// server asked for that is missing here, does not cover the runtime, or is signed out is an error the turn
/// reports instead of running on some other login.
pub fn turn_profile(
    config: &Map<String, Value>,
    attempt: &Value,
    runtime_rows: &Map<String, Value>,
    assignments: &[Value],
    rejected: &dyn Fn(&str, &Profile) -> bool,
) -> Result<Option<Profile>, Unavailable> {
    let requested = text_of(&attempt["profile"]);
    let bot = text_of(&attempt["bot"]);
    let profile = select(config, Some(&bot), Some(&requested));
    if requested.is_empty() {
        return Ok(profile);
    }
    let runtime = text_of(&attempt["config"]["runtime"]);
    let fail = |problem: String| Unavailable { problem, profile: requested.clone(), runtime: runtime.clone() };
    if !covers(&attempt["config"]) {
        return Err(fail(format!("Subscription {requested} doesn't cover {runtime}")));
    }
    let computer = text_of(&attempt["computer_label"]);
    let Some(profile) = profile else {
        return Err(fail(subscription_problem(config, assignments, &requested, false, &computer)));
    };
    let status = &runtime_rows.get(&runtime).map(|r| r["profiles"][&profile.name].clone()).unwrap_or(Value::Null);
    let signed_out = match reported_sign_in(&profile.name, &runtime) {
        Some(login) => login == false,
        None => status["authenticated"] == "missing" || status["authenticated"] == "rejected",
    };
    if signed_out || rejected(&runtime, &profile) {
        return Err(fail(subscription_problem(config, assignments, &requested, true, &computer)));
    }
    Ok(Some(profile))
}

/// Make `<root>/<name>` with a home per runtime (profiles.create); returns its runner.json entry.
pub fn create(root: &Path, name: &str, share_operator: bool) -> Result<Value, String> {
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
    if !valid_name(name) {
        return Err("A profile name is lowercase letters, digits, and single hyphens (up to 80 characters)".into());
    }
    let err = |e: std::io::Error| e.to_string();
    let parent = root.parent().unwrap_or(Path::new("/"));
    std::fs::create_dir_all(parent).map_err(err)?;
    let root = std::fs::canonicalize(parent).map_err(err)?.join(root.file_name().ok_or("no profiles folder name")?);
    let directory = |path: &Path, mode: u32| -> Result<(), String> {
        use std::os::unix::fs::MetadataExt;
        match std::fs::DirBuilder::new().mode(mode).create(path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e.to_string()),
        }
        // The opened directory, never a link put in its place, and only the supervisor's own: a home already
        // handed to the bot user keeps the mode its CLI gave it.
        let folder = std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY).open(path)
            .map_err(|_| "Subscription profile paths must not be symbolic links".to_string())?;
        let meta = folder.metadata().map_err(err)?;
        // SAFETY: geteuid has no preconditions.
        if meta.uid() == unsafe { libc::geteuid() } && meta.permissions().mode() & 0o7777 != mode {
            folder.set_permissions(std::fs::Permissions::from_mode(mode)).map_err(err)?;
        }
        Ok(())
    };
    let write_new = |path: &Path, content: &[u8]| -> Result<(), String> {
        use std::io::Write;
        match std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(path) {
            Ok(mut f) => f.write_all(content).map_err(err),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                if std::fs::symlink_metadata(path).map_err(err)?.is_file() {
                    Ok(())
                } else {
                    Err("Subscription profile paths must not be symbolic links".into())
                }
            }
            Err(e) => Err(e.to_string()),
        }
    };
    directory(&root, 0o711)?;
    let folder = root.join(name);
    let isolated = !share_operator && crate::isolation::enabled();
    directory(&folder, if isolated { 0o711 } else { 0o700 })?;
    let meta = serde_json::to_string_pretty(&json!({"name": name, "share_operator": share_operator})).unwrap();
    write_new(&folder.join("profile.json"), meta.as_bytes())?;
    if !share_operator {
        let gitconfig = PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".gitconfig");
        for runtime in crate::harness_tools::profile_runtimes() {
            let caps = crate::harness_tools::caps(&runtime, "");
            let home = folder.join(&runtime);
            directory(&home, 0o700)?;
            for dir in &caps.profile_dirs {
                directory(&home.join(dir), 0o700)?;
            }
            // A CLI whose profile home is HOME would otherwise lose the operator's git identity.
            if caps.profile_home == "HOME" && gitconfig.is_file() {
                write_new(&home.join(".gitconfig"), &std::fs::read(&gitconfig).map_err(err)?)?;
            }
        }
    }
    if isolated {
        hand_over(&folder)?;
    }
    Ok(json!({"dir": folder.display().to_string(), "share_operator": share_operator}))
}

/// Under runner isolation a profile's sign-in, its verdict and every turn on it run as the bot user, so each
/// runtime home is the bot user's. The profile folder stays the supervisor's and only traversable (0711): the
/// bot can neither read profile.json nor put a link in place of a home. Repairs a profile made before this
/// (folder 0700, homes the supervisor's). A no-op without isolation or for a profile on the operator's logins.
pub fn hand_to_bot(profile: &Profile) -> Result<(), String> {
    if profile.share_operator || !crate::isolation::enabled() {
        return Ok(());
    }
    hand_over(&profile.directory)
}

fn hand_over(folder: &Path) -> Result<(), String> {
    use std::os::unix::fs::{DirBuilderExt, MetadataExt};
    let Some((uid, _)) = crate::isolation::identity() else { return Ok(()) };
    crate::isolation::keep_supervisor_directory(folder, 0o711).map_err(|e| format!("Subscription profile folder: {e}"))?;
    let homes: Vec<PathBuf> = crate::harness_tools::profile_runtimes().iter().map(|r| folder.join(r)).collect();
    for home in &homes {
        // A runtime added after the profile was made: the bot user cannot make its home in the folder itself.
        match std::fs::DirBuilder::new().mode(0o700).create(home) {
            Err(e) if e.kind() != std::io::ErrorKind::AlreadyExists => return Err(e.to_string()),
            _ => {}
        }
    }
    // Pinned, link-free and leaving foreign hardlinks alone (isolation::adopt).
    crate::isolation::adopt(&homes);
    for home in &homes {
        match std::fs::symlink_metadata(home) {
            Ok(meta) if meta.is_dir() && meta.uid() == uid => {}
            _ => return Err(format!("{} is not a folder the bot user owns; subscription profile homes must not be symbolic links", home.display())),
        }
    }
    Ok(())
}

/// Every profile runner.json names.
pub fn configured(config: &Map<String, Value>) -> Vec<Profile> {
    let Some(entries) = config.get("profiles").and_then(|p| p.as_object()) else { return vec![] };
    entries
        .iter()
        .filter(|(name, entry)| valid_name(name) && entry.is_object() && truthy(entry.get("dir")))
        .map(|(name, entry)| Profile::new(name, &text_of(&entry["dir"]), truthy(entry.get("share_operator"))))
        .collect()
}

/// Persist a UI-made profile (service.py add_profile): created under `<config dir>/profiles`, added to
/// runner.json without changing local assignments. Returns the entry.
pub fn add_profile(config: &Config, name: &str) -> Result<Value, String> {
    let root = config.path.parent().unwrap_or(Path::new(".")).join("profiles");
    let entry = create(&root, name, false)?;
    let mut stored = Config::load(&config.path)?;
    let profiles = stored.data.entry("profiles").or_insert(json!({}));
    if !profiles.is_object() {
        *profiles = json!({});
    }
    profiles[name] = entry.clone();
    stored.save()?;
    forget_report();
    Ok(entry)
}

/// Whether `profile`'s folder is one Tico made for it: `<config dir>/profiles/<name>`, not on the operator's own logins.
/// Only such a folder is ever emptied or deleted by Remove.
pub fn tico_made(config: &Config, profile: &Profile) -> bool {
    let root = config.path.parent().unwrap_or(Path::new(".")).join("profiles");
    !profile.share_operator
        && std::fs::canonicalize(&root).ok().zip(std::fs::canonicalize(&profile.directory).ok())
            .is_some_and(|(root, dir)| dir.parent() == Some(root.as_path()) && dir.file_name().is_some_and(|f| f == profile.name.as_str()))
}

/// Take a subscription off this computer (Remove, runner-rs/src/login.rs run_remove): out of runner.json, with any
/// local bot assignment to it, then its folder deleted when Tico made it (tico_made). A folder elsewhere is only
/// unlisted. Ok(true) when the folder is gone; a folder that could not be deleted is logged and left, unlisted.
pub fn remove_profile(config: &Config, name: &str) -> Result<bool, String> {
    let mut stored = Config::load(&config.path)?;
    let Some(entry) = stored.data.get("profiles").and_then(|p| p.get(name)).cloned() else {
        return Err(format!("Subscription {name} is not on this computer"));
    };
    if stored.data.get("default_profile").map(text_of).as_deref() == Some(name) {
        return Err(format!("Subscription {name} is this computer's own login; sign it out instead"));
    }
    let profile = Profile::new(name, &text_of(&entry["dir"]), truthy(entry.get("share_operator")));
    let ours = tico_made(config, &profile);
    if let Some(map) = stored.data.get_mut("profiles").and_then(|p| p.as_object_mut()) {
        map.remove(name);
    }
    if let Some(map) = stored.data.get_mut("bot_profiles").and_then(|p| p.as_object_mut()) {
        map.retain(|_, v| text_of(v) != name);
    }
    stored.save()?;
    forget_report();
    if !ours {
        return Ok(false);
    }
    // A link in place of the folder is never followed: remove_dir_all removes the link itself.
    match std::fs::remove_dir_all(&profile.directory) {
        Ok(()) => Ok(true),
        Err(e) => {
            crate::util::log(&format!("Tico runner: subscription {name} removed; its folder {} stays ({:?})", profile.directory.display(), e.kind()));
            Ok(false)
        }
    }
}

/// The `profile` subcommand: add, list, assign (runner/__main__.py profile_command). Writes runner.json the
/// way Python does: every key kept, two-space indent.
pub fn command(config: &mut Config, action: &str, name: Option<&str>, share_operator: bool, bot: Option<&str>) -> Result<String, String> {
    match action {
        "add" => {
            let name = name.ok_or("profile add needs a name")?;
            let root = config.path.parent().unwrap_or(Path::new(".")).join("profiles");
            let entry = create(&root, name, share_operator)?;
            let profiles = config.data.entry("profiles").or_insert(json!({}));
            if !profiles.is_object() {
                *profiles = json!({});
            }
            profiles[name] = entry.clone();
            config.data.entry("default_profile").or_insert(json!(name)); // the first profile added is the default
            config.save()?;
            let mut out = Map::new();
            out.insert("profile".into(), json!(name));
            out.extend(entry.as_object().cloned().unwrap_or_default());
            out.insert("default_profile".into(), config.data["default_profile"].clone());
            Ok(serde_json::to_string_pretty(&Value::Object(out)).unwrap())
        }
        "list" => Ok(serde_json::to_string_pretty(&json!({
            "default_profile": config.data.get("default_profile").cloned().unwrap_or(json!("")),
            "profiles": config.data.get("profiles").cloned().unwrap_or(json!({})),
            "bot_profiles": config.data.get("bot_profiles").cloned().unwrap_or(json!({})),
        }))
        .unwrap()),
        "assign" => {
            let name = name.ok_or("profile assign needs a profile")?;
            let bot = bot.ok_or("profile assign needs a bot")?;
            if !config.data.get("profiles").and_then(|p| p.get(name)).is_some_and(|e| truthy(Some(e))) {
                return Err("No such profile on this registration; add it with `profile add`".into());
            }
            let map = config.data.entry("bot_profiles").or_insert(json!({}));
            if !map.is_object() {
                *map = json!({});
            }
            map[bot] = json!(name);
            config.save()?;
            Ok(format!("{bot} runs on subscription profile {name}"))
        }
        other => Err(format!("unknown profile action {other}")),
    }
}

/// Whether this bot's stored Claude credential has already expired (service.py claude_token_cold): a plain read
/// of `<HOME>/.claude/.credentials.json`, no call and no sign-in.
pub fn claude_token_cold(profile: Option<&Profile>) -> bool {
    let home = profile.and_then(|p| p.home("claude")).unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap_or_default()));
    let Ok(text) = std::fs::read_to_string(home.join(".claude").join(".credentials.json")) else { return false };
    let Ok(stored) = serde_json::from_str::<Value>(&text) else { return false };
    let oauth = if stored["claudeAiOauth"].is_object() { &stored["claudeAiOauth"] } else { &stored };
    let expires = match &oauth["expiresAt"] {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    };
    expires.is_some_and(|ms| ms / 1000.0 <= crate::util::epoch_s())
}

/// Usage window snapshots per (profile, runtime, window) (runner/subscription_usage.py Reports): non-secret. The
/// window is `weekly` (10080 minutes) or `five_hour` (300); profile "" is the computer's own login.
fn weekly() -> &'static Mutex<HashMap<(String, String, &'static str), Value>> {
    static W: OnceLock<Mutex<HashMap<(String, String, &'static str), Value>>> = OnceLock::new();
    W.get_or_init(Default::default)
}

pub fn remember_weekly(profile: &str, runtime: &str, event: &Value) {
    // Only a runtime whose turns or refresh report a weekly window has one to keep.
    let weekly_usage = crate::harness_tools::caps(runtime, "").weekly_usage;
    if !["refresh", "turns"].contains(&weekly_usage.as_str()) {
        return;
    }
    if !event["plan_type"].is_null() {
        crate::accounts::remember_plan(profile, runtime, &event["plan_type"]);
    }
    let window = match event["window_minutes"].as_u64() {
        Some(10080) => "weekly",
        Some(300) => "five_hour",
        _ => return,
    };
    let percent = &event["used_percent"];
    if !percent.is_null() && !percent.as_f64().is_some_and(|p| p.is_finite() && (0.0..=100.0).contains(&p)) {
        return;
    }
    let mut reset = event["resets_at"].clone();
    if let Some(r) = reset.as_str().filter(|r| !r.is_empty()) {
        let at = crate::util::parse_iso(r);
        if !at.is_finite() || !(r.ends_with('Z') || r.contains('+') || r[10..].contains('-')) {
            return;
        }
        reset = crate::util::iso(&json!(at));
    }
    let status = event["status"].as_str().filter(|s| ["allowed", "allowed_warning", "rejected"].contains(s)).map_or(Value::Null, |s| json!(s));
    let now = crate::util::iso(&json!(crate::util::epoch_s()));
    weekly().lock().unwrap().insert((profile.into(), runtime.into(), window), json!({"used_percent": percent, "resets_at": reset, "reported_at": now, "status": status}));
}

/// The usage windows kept for `profile`'s `runtime` login, as `weekly` / `five_hour` fields.
pub fn windows(profile: &str, runtime: &str) -> Map<String, Value> {
    let weekly = weekly().lock().unwrap();
    ["weekly", "five_hour"].into_iter().filter_map(|w| weekly.get(&(profile.to_string(), runtime.to_string(), w)).map(|v| (w.to_string(), v.clone()))).collect()
}

/// When `profile`'s `runtime` login can work again after a usage limit: the latest reset of a kept window the
/// provider reported as spent (`rejected`, or used to 100%), if that reset is still ahead and within eight days.
/// None when no window says so: the server then waits its own default instead.
pub fn limit_reset(profile: &str, runtime: &str, now: f64) -> Option<String> {
    windows(profile, runtime)
        .values()
        .filter(|w| w["status"] == "rejected" || w["used_percent"].as_f64().is_some_and(|p| p >= 100.0))
        .filter_map(|w| w["resets_at"].as_str().map(crate::util::parse_iso))
        .filter(|at| at.is_finite() && *at > now && *at <= now + 8.0 * 86400.0)
        .reduce(f64::max)
        .map(|at| crate::util::text_of(&crate::util::iso(&json!(at))))
}

/// A signed-out login's windows go with it: the next sign-in may be someone else's.
pub fn forget_windows(profile: &str, runtime: &str) {
    weekly().lock().unwrap().retain(|(p, r, _), _| !(p == profile && r == runtime));
}

struct Cached {
    at: Instant,
    /// runner.json's `profiles` the rows were made from: a profile added or taken out since is not served stale.
    profiles: Value,
    rows: Vec<Value>,
}

fn report_cache() -> &'static Mutex<Option<Cached>> {
    static C: OnceLock<Mutex<Option<Cached>>> = OnceLock::new();
    C.get_or_init(Default::default)
}

/// Counts `forget_report` calls, so a report probed before one is not kept after it.
static FORGOTTEN: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

pub fn forget_report() {
    let mut cache = report_cache().lock().unwrap();
    FORGOTTEN.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    *cache = None;
}

/// The cached login state of `profile` for `runtime` (`signed_in`), when the last report has it.
pub fn reported_sign_in(profile: &str, runtime: &str) -> Option<Value> {
    let cache = report_cache().lock().unwrap();
    let rows = &cache.as_ref()?.rows;
    rows.iter().find(|r| r["name"] == profile).map(|r| r["runtimes"][runtime]["signed_in"].clone())
}

/// The heartbeat's `profiles` list (service.py profile_report): each profile's sign-in where its CLI can say, probed
/// within ten seconds for the whole report and kept a minute, with the weekly allowance attached.
pub async fn report(config: &Map<String, Value>, rejected: &(dyn Fn(&str, &str) -> bool + Sync)) -> Vec<Value> {
    let profiles = config.get("profiles").cloned().unwrap_or(Value::Null);
    let fresh = report_cache().lock().unwrap().as_ref().filter(|c| c.at.elapsed() < Duration::from_secs(60) && c.profiles == profiles).map(|c| c.rows.clone());
    let rows = match fresh {
        Some(rows) => rows,
        None => {
            let forgotten = FORGOTTEN.load(std::sync::atomic::Ordering::SeqCst);
            let deadline = Instant::now() + Duration::from_secs(10);
            let mut entries: Vec<(String, Value)> = config.get("profiles").and_then(|p| p.as_object()).map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect()).unwrap_or_default();
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            let mut rows = vec![];
            for (name, entry) in entries {
                if !valid_name(&name) || !entry.is_object() || !truthy(entry.get("dir")) {
                    continue;
                }
                let profile = Profile::new(&name, &text_of(&entry["dir"]), truthy(entry.get("share_operator")));
                let mut runtimes = Map::new();
                // Every key a runtime signs in with leaves the probe's environment, so only the profile's own login counts.
                let mut clean = crate::env::process_environment();
                for k in crate::harness_tools::runtimes().iter().flat_map(|r| crate::harness_tools::api_key_env(r)).chain(["ANTHROPIC_AUTH_TOKEN".to_string()]) {
                    clean.remove(&k);
                }
                for runtime in crate::harness_tools::profile_runtimes() {
                    let runtime = runtime.as_str();
                    let status = crate::harness_tools::caps(runtime, "").sign_in_status;
                    let mut signed = Value::Null;
                    let budget = deadline.saturating_duration_since(Instant::now());
                    let exe = crate::env::which(&crate::harness_tools::executable_for(runtime), &std::env::var("PATH").unwrap_or_default());
                    match status.check.as_str() {
                        "command" | "json" => {
                            if let (Some(exe), false) = (exe, budget.is_zero()) {
                                let env = profile.environment(runtime, &clean);
                                // As the bot user, like readiness and turns: the CLI may write in the profile's home.
                                let mut cmd = crate::isolation::command(exe);
                                cmd.args(&status.args).env_clear().envs(&env).stdin(std::process::Stdio::null()).kill_on_drop(true);
                                if let Ok(Ok(out)) = tokio::time::timeout(budget.min(Duration::from_secs(3)), cmd.output()).await {
                                    let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr)).to_lowercase();
                                    signed = if status.check == "command" {
                                        json!(out.status.success() && text.contains(&status.ready) && (status.not_ready.is_empty() || !text.contains(&status.not_ready)))
                                    } else {
                                        serde_json::from_slice::<Value>(&out.stdout).ok().filter(|v| v.is_object()).map_or(Value::Null, |v| {
                                            if crate::harness_tools::caps(runtime, "").account == "status-json" {
                                                match crate::accounts::from_status_json(&v) {
                                                    Some(account) => crate::accounts::remember(&name, runtime, account),
                                                    None => crate::accounts::forget(&name, runtime),
                                                }
                                            }
                                            json!(v[&status.field] == true)
                                        })
                                    };
                                    if signed == true {
                                        crate::accounts::refresh_in_background(&name, runtime, env.clone());
                                    }
                                }
                            }
                        }
                        // The login file in the folder this profile's turns use.
                        "file" => {
                            let mut env = profile.environment(runtime, &clean);
                            crate::hosts::use_own_config_home(runtime, Some(&profile), &mut env);
                            signed = crate::hosts::file_signed_in(runtime, &env).map_or(Value::Null, |s| json!(s));
                        }
                        _ => {}
                    }
                    if rejected(runtime, &name) {
                        signed = json!(false);
                    }
                    // A signed-out login has no account: the next sign-in, maybe as someone else, reads it afresh.
                    if signed == false {
                        crate::accounts::forget(&name, runtime);
                    }
                    runtimes.insert(runtime.into(), json!({"signed_in": signed}));
                }
                rows.push(json!({"name": name, "runtimes": runtimes}));
                if rows.len() == 100 {
                    break;
                }
            }
            let mut cache = report_cache().lock().unwrap();
            // A sign-in that finished while the CLIs were asked: these rows may predate it, so the next report asks again.
            if FORGOTTEN.load(std::sync::atomic::Ordering::SeqCst) == forgotten {
                *cache = Some(Cached { at: Instant::now(), profiles, rows: rows.clone() });
            }
            drop(cache);
            rows
        }
    };
    let weekly = weekly().lock().unwrap();
    rows.into_iter()
        .map(|mut p| {
            let name = text_of(&p["name"]);
            if let Some(rts) = p["runtimes"].as_object_mut() {
                for (runtime, state) in rts.iter_mut() {
                    for window in ["weekly", "five_hour"] {
                        if let Some(w) = weekly.get(&(name.clone(), runtime.clone(), window)) {
                            state[window] = w.clone();
                        }
                    }
                    // The account only beside a login that is not known to be signed out.
                    if state["signed_in"] != false {
                        if let Some(account) = crate::accounts::account(&name, runtime) {
                            state["account"] = account;
                        }
                    }
                }
            }
            p
        })
        .collect()
}

pub fn sha256_hex(data: &[u8]) -> String {
    ring::digest::digest(&ring::digest::SHA256, data).as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_removed_profile_leaves_runner_json_and_its_folder_but_not_the_default_or_a_foreign_folder() {
        let dir = std::env::temp_dir().join(format!("tico-remove-profile-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("profiles/acme/codex")).unwrap();
        std::fs::create_dir_all(dir.join("elsewhere/beta")).unwrap();
        std::fs::write(dir.join("profiles/acme/codex/auth.json"), "{}").unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, serde_json::to_string(&json!({"server": "x", "default_profile": "own",
            "profiles": {"acme": {"dir": dir.join("profiles/acme").display().to_string()},
                         "beta": {"dir": dir.join("elsewhere/beta").display().to_string()},
                         "own": {"dir": dir.join("profiles/own").display().to_string()}},
            "bot_profiles": {"sales": "acme", "ops": "own"}})).unwrap()).unwrap();
        let config = Config::load(&path).unwrap();
        assert!(remove_profile(&config, "own").unwrap_err().contains("own login"));
        assert_eq!(remove_profile(&config, "acme"), Ok(true));
        assert!(!dir.join("profiles/acme").exists());
        let stored = Config::load(&path).unwrap();
        assert!(stored.data["profiles"].get("acme").is_none() && stored.data["profiles"].get("own").is_some());
        assert_eq!(stored.data["bot_profiles"], json!({"ops": "own"}));
        // Not a folder Tico made: unlisted, never deleted.
        assert_eq!(remove_profile(&config, "beta"), Ok(false));
        assert!(dir.join("elsewhere/beta").is_dir());
        assert!(remove_profile(&config, "beta").unwrap_err().contains("not on this computer"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn selection_follows_the_registration() {
        let config = json!({"profiles": {"acme": {"dir": "/p/acme"}, "beta": {"dir": "/p/beta", "share_operator": true}},
                            "default_profile": "acme", "bot_profiles": {"sales": "beta"}});
        let c = config.as_object().unwrap();
        assert_eq!(select(c, Some("ops"), None).unwrap().name, "acme");
        assert_eq!(select(c, Some("sales"), None).unwrap().name, "beta");
        assert_eq!(select(c, Some("ops"), Some("beta")).unwrap().name, "beta");
        assert!(select(c, Some("ops"), Some("gone")).is_none());
        assert!(select(json!({}).as_object().unwrap(), Some("ops"), None).is_none());
        let acme = select(c, None, None).unwrap();
        let env = acme.environment("codex", &Env::new());
        assert_eq!(env["CODEX_HOME"], "/p/acme/codex");
        assert_eq!(acme.environment("claude", &Env::new())["HOME"], "/p/acme/claude");
        assert!(select(c, Some("sales"), None).unwrap().environment("claude", &Env::new()).is_empty());
        assert_eq!(missing(c, "gone"), "profile gone not on this computer");
    }

    #[test]
    fn the_computers_own_login_keeps_its_windows_until_it_signs_out() {
        let reset = crate::util::iso(&json!(crate::util::epoch_s() + 3600.0));
        // "" is the computer's own login: AWS's bots run on it with no named profile.
        remember_weekly("", "claude", &json!({"used_percent": 53.0, "window_minutes": 300, "resets_at": reset, "status": "allowed"}));
        remember_weekly("", "claude", &json!({"used_percent": 17.0, "window_minutes": 10080, "resets_at": reset, "status": null}));
        let own = windows("", "claude");
        assert_eq!((own["five_hour"]["used_percent"].clone(), own["weekly"]["used_percent"].clone()), (json!(53.0), json!(17.0)));
        assert_eq!(own["five_hour"]["status"], "allowed");
        assert!(windows("", "codex").is_empty() && windows("other", "claude").is_empty());
        forget_windows("", "claude");
        assert!(windows("", "claude").is_empty());
    }

    #[test]
    fn turns_keep_the_weekly_and_five_hour_windows_and_the_plan() {
        let reset = crate::util::iso(&json!(crate::util::epoch_s() + 3600.0));
        remember_weekly("win-test", "codex", &json!({"used_percent": 12, "window_minutes": 10080, "resets_at": reset, "plan_type": "pro"}));
        remember_weekly("win-test", "codex", &json!({"used_percent": 40, "window_minutes": 300, "resets_at": reset}));
        remember_weekly("win-test", "codex", &json!({"used_percent": 1, "window_minutes": 60, "resets_at": reset}));
        // Claude's turns say when a window resets, never how much is used: the percentage stays unknown.
        remember_weekly("win-test", "claude", &json!({"used_percent": null, "window_minutes": 300, "resets_at": reset, "status": "allowed"}));
        let windows = weekly().lock().unwrap().clone();
        assert_eq!(windows[&("win-test".to_string(), "codex".to_string(), "weekly")]["used_percent"], 12);
        assert_eq!(windows[&("win-test".to_string(), "codex".to_string(), "five_hour")]["used_percent"], 40);
        assert!(windows[&("win-test".to_string(), "claude".to_string(), "five_hour")]["used_percent"].is_null());
        assert_eq!(windows.keys().filter(|k| k.0 == "win-test").count(), 3);
        assert_eq!(crate::accounts::account("win-test", "codex").unwrap()["plan"], "pro");
        weekly().lock().unwrap().retain(|k, _| k.0 != "win-test");
        crate::accounts::forget("win-test", "codex");
    }

    #[test]
    fn a_spent_window_gives_the_limits_reset() {
        let now = crate::util::epoch_s();
        let at = |s: f64| crate::util::iso(&json!(now + s));
        // Codex's `account/rateLimits/updated` / `rateLimits/read`: the five-hour window used up, the week not.
        let codex = json!({"rateLimits": {"limitId": "codex", "planType": "plus",
                           "primary": {"windowDurationMins": 300, "usedPercent": 100, "resetsAt": now + 5400.0},
                           "secondary": {"windowDurationMins": 10080, "usedPercent": 64, "resetsAt": now + 400000.0}}});
        crate::subscription_refresh::remember_windows("reset-test", &codex);
        assert_eq!(limit_reset("reset-test", "codex", now), Some(crate::util::text_of(&at(5400.0))));
        // Claude's `rate_limit_event` when it refuses: `rejected` with `resetsAt`, no percentage.
        remember_weekly("reset-test", "claude", &json!({"used_percent": null, "window_minutes": 300, "resets_at": at(2400.0), "status": "rejected"}));
        remember_weekly("reset-test", "claude", &json!({"used_percent": null, "window_minutes": 10080, "resets_at": at(90000.0), "status": "allowed"}));
        assert_eq!(limit_reset("reset-test", "claude", now), Some(crate::util::text_of(&at(2400.0))));
        // Both spent: work waits for the later one.
        remember_weekly("reset-test", "claude", &json!({"used_percent": null, "window_minutes": 10080, "resets_at": at(90000.0), "status": "rejected"}));
        assert_eq!(limit_reset("reset-test", "claude", now), Some(crate::util::text_of(&at(90000.0))));
        // Nothing reported spent, a reset already past or one beyond a week: no reset, the server's default holds.
        assert_eq!(limit_reset("reset-test", "gemini", now), None);
        remember_weekly("reset-test2", "claude", &json!({"used_percent": null, "window_minutes": 300, "resets_at": at(2400.0), "status": "allowed"}));
        assert_eq!(limit_reset("reset-test2", "claude", now), None);
        remember_weekly("reset-test2", "codex", &json!({"used_percent": 100, "window_minutes": 300, "resets_at": at(-60.0)}));
        assert_eq!(limit_reset("reset-test2", "codex", now), None);
        remember_weekly("reset-test2", "codex", &json!({"used_percent": 100, "window_minutes": 10080, "resets_at": at(9.0 * 86400.0)}));
        assert_eq!(limit_reset("reset-test2", "codex", now), None);
        weekly().lock().unwrap().retain(|k, _| !k.0.starts_with("reset-test"));
        crate::accounts::forget("reset-test", "codex");
    }

    #[tokio::test]
    async fn a_signed_out_profile_forgets_its_account() {
        let dir = std::env::temp_dir().join(format!("tico-signout-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("codex")).unwrap();
        let config = json!({"profiles": {"out-test": {"dir": dir.to_string_lossy()}}});
        let config = config.as_object().unwrap();
        let shown = |rows: &[Value]| rows.iter().find(|r| r["name"] == "out-test").map(|r| r["runtimes"]["codex"]["account"].clone()).unwrap_or(Value::Null);
        crate::accounts::remember("out-test", "codex", json!({"email": "old@acme.example", "plan": "pro"}));
        // Signed out (here the provider refused the login, which reports signed out whatever the CLI says).
        forget_report();
        let rows = report(config, &|runtime, name| runtime == "codex" && name == "out-test").await;
        assert_eq!(rows.iter().find(|r| r["name"] == "out-test").unwrap()["runtimes"]["codex"]["signed_in"], false);
        assert!(crate::accounts::account("out-test", "codex").is_none(), "signing out forgets the cached account");
        // Signed back in as someone else: the old email never shows, within the read interval or not.
        crate::accounts::remember_plan("out-test", "codex", &json!("plus"));
        forget_report();
        let rows = report(config, &|_, _| false).await;
        assert!(!shown(&rows).to_string().contains("old@acme.example"), "{rows:?}");
        crate::accounts::forget("out-test", "codex");
        crate::accounts::forget("out-test", "claude");
        forget_report();
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_profile_added_or_taken_out_is_reported_at_once_not_from_the_minute_cache() {
        let dir = std::env::temp_dir().join(format!("tico-report-set-{}", uuid::Uuid::new_v4().simple()));
        let names = |rows: &[Value]| rows.iter().map(|r| text_of(&r["name"])).filter(|n| n.starts_with("set-")).collect::<Vec<_>>();
        let one = json!({"profiles": {"set-one": {"dir": dir.join("one").to_string_lossy()}}});
        let two = json!({"profiles": {"set-one": {"dir": dir.join("one").to_string_lossy()}, "set-two": {"dir": dir.join("two").to_string_lossy()}}});
        assert_eq!(names(&report(one.as_object().unwrap(), &|_, _| false).await), ["set-one"]);
        assert_eq!(names(&report(two.as_object().unwrap(), &|_, _| false).await), ["set-one", "set-two"], "added");
        assert_eq!(names(&report(one.as_object().unwrap(), &|_, _| false).await), ["set-one"], "taken out");
        forget_report();
    }

    #[tokio::test]
    async fn rows_probed_while_a_sign_in_finished_are_not_kept() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let dir = std::env::temp_dir().join(format!("tico-report-race-{}", uuid::Uuid::new_v4().simple()));
        let config = json!({"profiles": {"race-test": {"dir": dir.to_string_lossy()}}});
        let config = config.as_object().unwrap();
        // The sign-in's verdict lands (forget_report) while this report is still asking the CLIs.
        report(config, &|runtime, _| {
            if runtime == "codex" {
                forget_report();
            }
            false
        })
        .await;
        // So the next report asks again rather than serve those rows for a minute.
        let asked = AtomicUsize::new(0);
        report(config, &|_, _| {
            asked.fetch_add(1, Ordering::SeqCst);
            false
        })
        .await;
        assert!(asked.load(Ordering::SeqCst) > 0, "served from the cache");
        forget_report();
    }

    #[test]
    fn the_binding_is_pythons_sha256() {
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let p = Profile::new("acme", "/p/acme", false);
        // hashlib.sha256(json.dumps(["/p/acme", False]).encode()).hexdigest()
        assert_eq!(p.binding(), sha256_hex(b"[\"/p/acme\", false]"));
    }

    #[test]
    fn add_list_and_assign_write_the_registration() {
        let dir = std::env::temp_dir().join(format!("tico-profiles-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, r#"{"runner_id":"r1","keep":1}"#).unwrap();
        let mut config = Config::load(&path).unwrap();
        let out: Value = serde_json::from_str(&command(&mut config, "add", Some("acme"), false, None).unwrap()).unwrap();
        assert_eq!(out["default_profile"], "acme");
        let folder = PathBuf::from(text_of(&out["dir"]));
        assert!(folder.join("claude/.claude").is_dir() && folder.join("codex").is_dir() && folder.join("profile.json").is_file());
        command(&mut config, "assign", Some("acme"), false, Some("sales")).unwrap();
        assert!(command(&mut config, "assign", Some("nope"), false, Some("sales")).is_err());
        let stored = Config::load(&path).unwrap();
        assert_eq!(stored.data["bot_profiles"]["sales"], "acme");
        assert_eq!(stored.data["keep"], 1);
        assert!(create(&dir.join("profiles"), "Bad Name", false).is_err());
        let names: Vec<String> = configured(&stored.data).into_iter().map(|p| p.name).collect();
        assert_eq!(names, ["acme"]);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn the_supervisor_folder_is_pinned_and_never_a_link() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("tico-profile-folder-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("acme")).unwrap();
        std::fs::set_permissions(dir.join("acme"), std::fs::Permissions::from_mode(0o700)).unwrap();
        crate::isolation::keep_supervisor_directory(&dir.join("acme"), 0o711).unwrap();
        assert_eq!(std::fs::metadata(dir.join("acme")).unwrap().permissions().mode() & 0o7777, 0o711);
        std::fs::create_dir(dir.join("elsewhere")).unwrap();
        std::fs::set_permissions(dir.join("elsewhere"), std::fs::Permissions::from_mode(0o700)).unwrap();
        std::os::unix::fs::symlink(dir.join("elsewhere"), dir.join("link")).unwrap();
        assert!(crate::isolation::keep_supervisor_directory(&dir.join("link"), 0o711).is_err());
        std::os::unix::fs::symlink(&dir, dir.join("acme/up")).unwrap();
        assert!(crate::isolation::keep_supervisor_directory(&dir.join("acme/up/elsewhere"), 0o711).is_err(), "no link on the way");
        assert_eq!(std::fs::metadata(dir.join("elsewhere")).unwrap().permissions().mode() & 0o7777, 0o700);
        // Without isolation a profile stays the operator's alone and handing it over changes nothing.
        let created = create(&dir.join("profiles"), "solo", false).unwrap();
        let folder = PathBuf::from(text_of(&created["dir"]));
        assert_eq!(std::fs::metadata(&folder).unwrap().permissions().mode() & 0o7777, 0o700);
        hand_to_bot(&Profile::new("solo", &folder.display().to_string(), false)).unwrap();
        assert_eq!(std::fs::metadata(&folder).unwrap().permissions().mode() & 0o7777, 0o700);
        std::fs::remove_dir_all(dir).ok();
    }

    /// Root plays the supervisor, `TICO_RUNNER_BOT_UID=10003 TICO_RUNNER_BOT_GID=10002` the bot user.
    #[cfg(target_os = "linux")]
    struct Disposable(PathBuf);
    #[cfg(target_os = "linux")]
    impl Disposable {
        fn new() -> Self {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(unsafe { libc::geteuid() }, 0, "disposable supervisor fixture");
            assert!(std::env::var_os("TICO_TEST_DISPOSABLE").is_some());
            assert_eq!(crate::isolation::identity(), Some((10003, 10002)));
            let path = std::env::temp_dir().join(format!("tico-profile-contract-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir(&path).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            Self(path)
        }
    }
    #[cfg(target_os = "linux")]
    impl Drop for Disposable {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    /// (uid, mode) of `path`, not following a link.
    #[cfg(target_os = "linux")]
    fn owner(path: &Path) -> (u32, u32) {
        use std::os::unix::fs::MetadataExt;
        let m = std::fs::symlink_metadata(path).unwrap();
        (m.uid(), m.mode() & 0o7777)
    }

    /// What a sign-in and its verdict do in `runtime`'s profile home, as the bot user and with the profile's
    /// environment (login::model_command): write there, and fail to read `private`.
    #[cfg(target_os = "linux")]
    fn bot_uses_home(profile: &Profile, runtime: &str, private: &Path) -> bool {
        let var = home_var(runtime).unwrap();
        let out = crate::isolation::std_command("/bin/sh")
            .args(["-c", &format!("id -u >\"${var}/probe\" && test \"$(cat \"${var}/probe\")\" = 10003 && ! cat \"$1\" 2>/dev/null"), "sh"])
            .arg(private)
            .env_clear()
            .envs(profile.environment(runtime, &[("PATH".to_string(), "/usr/bin:/bin".to_string())].into_iter().collect()))
            .output()
            .unwrap();
        out.status.success()
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn isolated_a_new_profile_signs_in_as_the_bot_user() {
        let f = Disposable::new();
        let entry = create(&f.0.join("profiles"), "chris-chatgpt", false).unwrap();
        let folder = PathBuf::from(text_of(&entry["dir"]));
        assert_eq!(owner(&folder), (0, 0o711), "the folder stays the supervisor's, traversable only");
        assert_eq!(owner(&folder.join("profile.json")), (0, 0o600));
        for runtime in crate::harness_tools::profile_runtimes() {
            assert_eq!(owner(&folder.join(&runtime)), (10003, 0o700), "{runtime} home");
        }
        assert_eq!(owner(&folder.join("claude/.claude")), (10003, 0o700));
        let profile = Profile::new("chris-chatgpt", &folder.display().to_string(), false);
        for runtime in ["codex", "claude"] {
            assert!(bot_uses_home(&profile, runtime, &folder.join("profile.json")), "{runtime}");
        }
        // Making it again (a retried sign-in) keeps what the bot user owns and its modes.
        create(&f.0.join("profiles"), "chris-chatgpt", false).unwrap();
        assert_eq!(owner(&folder.join("codex/probe")).0, 10003);
        assert_eq!(owner(&folder), (0, 0o711));
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn isolated_a_profile_made_before_is_repaired_with_its_bytes_and_modes() {
        use std::os::unix::fs::{chown, PermissionsExt};
        let f = Disposable::new();
        let set = |p: &Path, mode: u32| std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
        for (name, folder_uid) in [("before", 0), ("hand-repaired", 10003)] {
            let folder = f.0.join("profiles").join(name);
            std::fs::create_dir_all(folder.join("codex")).unwrap();
            std::fs::create_dir_all(folder.join("claude/.claude")).unwrap();
            std::fs::write(folder.join("profile.json"), "{}").unwrap();
            std::fs::write(folder.join("codex/config.toml"), "model = 'x'\n").unwrap();
            for p in ["", "codex", "claude", "claude/.claude"] { set(&folder.join(p), 0o700); }
            set(&folder.join("profile.json"), 0o600);
            set(&folder.join("codex/config.toml"), 0o640);
            chown(&folder, Some(folder_uid), Some(10002)).unwrap();
            let profile = Profile::new(name, &folder.display().to_string(), false);
            assert!(!bot_uses_home(&profile, "codex", &folder.join("profile.json")), "{name}: the reported Permission denied");
            hand_to_bot(&profile).unwrap();
            assert_eq!(owner(&folder), (0, 0o711), "{name}");
            assert_eq!(owner(&folder.join("codex")), (10003, 0o700));
            assert_eq!(owner(&folder.join("codex/config.toml")), (10003, 0o640));
            assert_eq!(std::fs::read(folder.join("codex/config.toml")).unwrap(), b"model = 'x'\n");
            assert_eq!(owner(&folder.join("claude/.claude")), (10003, 0o700));
            assert!(bot_uses_home(&profile, "codex", &folder.join("profile.json")), "{name}");
            assert!(bot_uses_home(&profile, "claude", &folder.join("profile.json")), "{name}");
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "requires a disposable Linux two-user environment"]
    fn isolated_hand_over_follows_no_link_and_no_foreign_alias() {
        use std::os::unix::fs::PermissionsExt;
        let f = Disposable::new();
        let private = f.0.join("private");
        std::fs::create_dir(&private).unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(private.join("runner.json"), "supervisor fixture").unwrap();
        std::fs::set_permissions(private.join("runner.json"), std::fs::Permissions::from_mode(0o600)).unwrap();
        let entry = create(&f.0.join("profiles"), "acme", false).unwrap();
        let folder = PathBuf::from(text_of(&entry["dir"]));
        let profile = Profile::new("acme", &folder.display().to_string(), false);
        // A home replaced by a link to the supervisor's files.
        std::fs::remove_dir_all(folder.join("codex")).unwrap();
        std::os::unix::fs::symlink(&private, folder.join("codex")).unwrap();
        assert!(hand_to_bot(&profile).is_err());
        assert!(create(&f.0.join("profiles"), "acme", false).is_err());
        assert_eq!(owner(&private), (0, 0o700));
        assert_eq!(owner(&private.join("runner.json")), (0, 0o600));
        // A hardlink to a supervisor file inside a home stays the supervisor's.
        std::fs::remove_file(folder.join("codex")).unwrap();
        std::fs::create_dir(folder.join("codex")).unwrap();
        std::fs::hard_link(private.join("runner.json"), folder.join("claude/alias")).unwrap();
        hand_to_bot(&profile).unwrap();
        assert_eq!(owner(&private.join("runner.json")), (0, 0o600));
        assert_eq!(owner(&folder.join("codex")).0, 10003);
        // The profile folder itself replaced by a link.
        let moved = f.0.join("moved");
        std::fs::rename(&folder, &moved).unwrap();
        std::os::unix::fs::symlink(&private, &folder).unwrap();
        assert!(hand_to_bot(&profile).is_err());
        assert_eq!(owner(&private), (0, 0o700));
    }
}
