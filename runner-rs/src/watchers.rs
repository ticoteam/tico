//! Watchers: programs in a bot's repository this runner runs on a schedule with no model (clients/watchers.py
//! for the declaration and output protocol, runner/watchers.py for the runs; docs/watchers.md).
//!
//! For each active bot hosted here, each program `watchers:` in bot.yaml declares runs no more often than its
//! `every`, never twice at once, as the bot's user (crate::isolation), in the bot's repository, with the bot's
//! granted credentials and no hub token, under a timeout after which its process group is killed. Lines that
//! start with `tico-event ` are events, the rest a log; both lose every secret value, then go to
//! `POST runners/watchers`. A report the hub did not take puts the program's state directory back as it was.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::io::AsyncReadExt;

use crate::client::Client;
use crate::env::{Env, EnvContext, Vault};
use crate::util::{log, text_of};

pub const FIELDS: &[&str] = &["enabled", "every", "name", "run", "timeout"];
pub const MIN_EVERY: i64 = 60;
pub const MAX_EVERY: i64 = 24 * 3600;
pub const DEFAULT_TIMEOUT: i64 = 60;
pub const MAX_TIMEOUT: i64 = 300;
pub const MAX_WATCHERS: usize = 10;
pub const EVENT_PREFIX: &str = "tico-event ";
pub const OPS: &[&str] = &["task", "comment", "done"];
pub const MAX_EVENTS: usize = 50;
pub const LIMITS: &[(&str, usize)] = &[("key", 200), ("ref", 100), ("title", 300), ("body", 20_000), ("text", 8_000), ("note", 2_000)];

pub const SCAN_EVERY: Duration = Duration::from_secs(30);
pub const OUTPUT_CAP: usize = 256 * 1024;
pub const REPORT_CAP: usize = 2000;
pub const STATE_CAP: u64 = 1024 * 1024;
pub const SECRET_NAMES: &[&str] = &["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"];
pub const PLAIN_NAMES: &[&str] = &["URL", "URI", "HOST", "REPO", "REPOS", "DIR", "PATH", "HOME", "EMAIL", "NAME", "LABEL", "TIMEZONE", "TZ"];
/// A parked starter bot takes no work, so what its watchers find would only pile up.
pub const PARKED_STATES: &[&str] = &["needs_setup", "needs_onboarding"];
pub const PARALLEL: usize = 3;

#[derive(Clone, Debug, PartialEq)]
pub struct Spec {
    pub name: String,
    pub argv: Vec<String>,
    pub every: i64,
    pub timeout: i64,
}

/// `30s`, `5m`, `2h` (or a bare number of seconds) as seconds (watchers.seconds).
fn seconds(value: Option<&Value>, default: Option<i64>) -> Result<Option<i64>, String> {
    let empty = matches!(value, None | Some(Value::Null)) || value == Some(&json!(""));
    if empty && default.is_some() {
        return Ok(default);
    }
    match value {
        Some(Value::Bool(_)) => return Err("a duration is like 5m".into()),
        Some(Value::Number(n)) => return Ok(Some(n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(0.0).trunc() as i64))),
        _ => {}
    }
    static DURATION: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = DURATION.get_or_init(|| Regex::new(r"^\s*(\d{1,5})\s*([smh])\s*$").unwrap());
    let text = value.map(crate::manifest::py_str).unwrap_or_else(|| "None".into());
    let found = re.captures(&text).ok_or("a duration is like 30s, 5m or 2h")?;
    let n: i64 = found[1].parse().unwrap_or(0);
    Ok(Some(n * match &found[2] { "s" => 1, "m" => 60, _ => 3600 }))
}

/// Python's `shlex.split(text)`: POSIX quoting, no comments.
pub fn shlex_split(text: &str) -> Result<Vec<String>, String> {
    let mut words = vec![];
    let mut word = String::new();
    let mut in_word = false;
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        match c {
            ' ' | '\t' | '\n' | '\r' => {
                if in_word {
                    words.push(std::mem::take(&mut word));
                    in_word = false;
                }
            }
            '\\' => {
                in_word = true;
                word.push(chars.next().ok_or("No escaped character")?);
            }
            '\'' => {
                in_word = true;
                loop {
                    match chars.next().ok_or("No closing quotation")? {
                        '\'' => break,
                        ch => word.push(ch),
                    }
                }
            }
            '"' => {
                in_word = true;
                loop {
                    match chars.next().ok_or("No closing quotation")? {
                        '"' => break,
                        '\\' => match chars.next().ok_or("No closing quotation")? {
                            ch @ ('"' | '\\') => word.push(ch),
                            ch => {
                                word.push('\\');
                                word.push(ch);
                            }
                        },
                        ch => word.push(ch),
                    }
                }
            }
            ch => {
                in_word = true;
                word.push(ch);
            }
        }
    }
    if in_word {
        words.push(word);
    }
    Ok(words)
}

/// The declared watchers, or the first thing wrong with them (watchers.parse). `argv[0]` is a path inside the
/// bot's repository, never looked up on PATH.
pub fn parse(entries: Option<&Value>) -> Result<Vec<Spec>, String> {
    let entries = match entries {
        None | Some(Value::Null) => return Ok(vec![]),
        Some(Value::Array(a)) if a.is_empty() => return Ok(vec![]),
        Some(Value::Array(a)) if a.len() <= MAX_WATCHERS => a,
        _ => return Err(format!("watchers is a list of at most {MAX_WATCHERS}")),
    };
    let static_name = Regex::new(r"^[a-z0-9][a-z0-9-]{0,39}$").unwrap();
    let mut out = vec![];
    let mut names = HashSet::new();
    for (index, entry) in entries.iter().enumerate() {
        let where_ = format!("watcher {}", index + 1);
        let Some(fields) = entry.as_object().filter(|m| m.keys().all(|k| FIELDS.contains(&k.as_str()))) else {
            return Err(format!("{where_} has fields other than {}", FIELDS.join(", ")));
        };
        let name = crate::manifest::text_or_empty(fields.get("name"));
        // `$` in Python's re also matches before a final newline.
        let bare = name.strip_suffix('\n').unwrap_or(&name);
        if !static_name.is_match(bare) || names.contains(&name) {
            return Err(format!("{where_}: name is lowercase words with hyphens, once"));
        }
        names.insert(name.clone());
        let where_ = format!("watcher {name}");
        let run = crate::manifest::text_or_empty(fields.get("run"));
        let argv = shlex_split(&run).map_err(|_| format!("{where_}: run has an unclosed quote"))?;
        let run_len = fields.get("run").map(crate::manifest::py_str).unwrap_or_else(|| "None".into()).chars().count();
        if argv.is_empty() || argv.len() > 20 || run_len > 300 {
            return Err(format!("{where_}: run is a file in the repository and its arguments"));
        }
        let first = &argv[0];
        let parts: Vec<&str> = first.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
        if first.starts_with('/') || parts.contains(&"..") || first.contains('\\') || parts.is_empty() {
            return Err(format!("{where_}: run starts with a path inside the repository, such as software/{name}"));
        }
        let every = seconds(fields.get("every"), None).map_err(|e| format!("{where_}: {e}"))?;
        let timeout = seconds(fields.get("timeout"), Some(DEFAULT_TIMEOUT)).map_err(|e| format!("{where_}: {e}"))?.unwrap_or(DEFAULT_TIMEOUT);
        if !every.is_some_and(|e| (MIN_EVERY..=MAX_EVERY).contains(&e)) {
            return Err(format!("{where_}: every is between 1m and 24h"));
        }
        if !(1..=MAX_TIMEOUT).contains(&timeout) {
            return Err(format!("{where_}: timeout is between 1s and 5m"));
        }
        if fields.get("enabled") == Some(&Value::Bool(false)) {
            continue;
        }
        out.push(Spec { name, argv, every: every.unwrap_or(MIN_EVERY), timeout });
    }
    Ok(out)
}

/// Python's `str.splitlines()`.
fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = vec![];
    let mut start = 0;
    let mut iter = text.char_indices().peekable();
    while let Some((i, c)) = iter.next() {
        let breaks = matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if breaks {
            lines.push(&text[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' {
                if let Some((j, '\n')) = iter.peek().copied() {
                    iter.next();
                    end = j + 1;
                }
            }
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// An event with only the known fields, cut to their limits, or None when it is not one.
fn check(event: &Map<String, Value>) -> Option<Value> {
    let op = event.get("op").and_then(|o| o.as_str()).filter(|o| OPS.contains(o))?;
    event.get("key").and_then(|k| k.as_str()).filter(|k| !k.trim().is_empty())?;
    let mut clean = Map::new();
    clean.insert("op".into(), json!(op));
    for (name, limit) in LIMITS {
        if let Some(Value::String(s)) = event.get(*name) {
            clean.insert(name.to_string(), json!(s.chars().take(*limit).collect::<String>()));
        }
    }
    Some(Value::Object(clean))
}

/// (events, log) from a watcher's output. A line with the prefix that is not a valid event stays in the log.
pub fn events(output: &str) -> (Vec<Value>, String) {
    let mut found = vec![];
    let mut lines = vec![];
    for line in splitlines(output) {
        if let Some(rest) = line.strip_prefix(EVENT_PREFIX) {
            let clean = serde_json::from_str::<Value>(rest).ok().and_then(|e| e.as_object().and_then(check));
            if let Some(c) = clean {
                if found.len() < MAX_EVENTS {
                    found.push(c);
                    continue;
                }
            }
        }
        lines.push(line);
    }
    (found, lines.join("\n"))
}

/// The argv to run: a Python file with this runner's interpreter whatever its mode, anything else as itself.
pub fn command(path: &Path, argv: &[String]) -> Option<Vec<String>> {
    let root = path.canonicalize().ok()?;
    let target = path.join(&argv[0]).canonicalize().ok()?;
    if target == root || !target.starts_with(&root) || !target.is_file() {
        return None;
    }
    let mut first = vec![0u8; 200];
    let n = {
        use std::io::Read;
        let mut f = std::fs::File::open(&target).ok()?;
        f.read(&mut first).ok()?
    };
    let first = &first[..n];
    let first = first.split_inclusive(|b| *b == b'\n').next().unwrap_or(&[]);
    let python = target.extension().is_some_and(|e| e == "py") || (first.starts_with(b"#!") && first.windows(6).any(|w| w == b"python"));
    let rest = argv[1..].iter().cloned();
    if python {
        return Some([crate::env::python().display().to_string(), target.display().to_string()].into_iter().chain(rest).collect());
    }
    let executable = std::fs::metadata(&target).is_ok_and(|m| m.permissions().mode() & 0o111 != 0);
    executable.then(|| std::iter::once(target.display().to_string()).chain(rest).collect())
}

/// The state directory's files, for the rollback; None past STATE_CAP.
pub fn snapshot(directory: &Path) -> Option<BTreeMap<String, Vec<u8>>> {
    let mut saved = BTreeMap::new();
    let mut size = 0u64;
    let Ok(items) = std::fs::read_dir(directory) else { return Some(saved) };
    for item in items.flatten() {
        let Ok(meta) = std::fs::symlink_metadata(item.path()) else { continue };
        if meta.is_file() {
            size += meta.len();
            if size > STATE_CAP {
                return None;
            }
            saved.insert(item.file_name().to_string_lossy().to_string(), std::fs::read(item.path()).ok()?);
        }
    }
    Some(saved)
}

pub fn restore(directory: &Path, saved: &Option<BTreeMap<String, Vec<u8>>>) {
    let Some(saved) = saved else { return };
    for item in std::fs::read_dir(directory).into_iter().flatten().flatten() {
        let name = item.file_name().to_string_lossy().to_string();
        if item.path().is_file() && !saved.contains_key(&name) {
            let _ = std::fs::remove_file(item.path());
        }
    }
    for (name, data) in saved {
        let _ = std::fs::write(directory.join(name), data);
    }
}

/// The values that must not appear in anything sent or logged: what the grants added to the environment and
/// any variable whose name says it is a secret. Longest first.
pub fn secret_values(env: &Env) -> Vec<String> {
    let mut found: HashSet<String> = HashSet::new();
    for (key, value) in env {
        let name = key.to_uppercase();
        let secret_name = SECRET_NAMES.iter().any(|w| name.contains(w));
        let plain_name = PLAIN_NAMES.iter().any(|w| name == *w || name.ends_with(&format!("_{w}"))) || name.starts_with("HUB_") || name.starts_with("TICO_");
        let inherited = std::env::var(key).ok().as_deref() == Some(value.as_str());
        if value.chars().count() >= 6 && (secret_name || (!inherited && !plain_name)) {
            found.insert(value.clone());
        }
    }
    let mut out: Vec<String> = found.into_iter().collect();
    out.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()).then(a.cmp(b)));
    out
}

fn redact(text: &str, values: &[String]) -> String {
    values.iter().fold(text.to_string(), |t, v| t.replace(v.as_str(), "[redacted]"))
}

pub fn now() -> String {
    let secs = crate::util::epoch_s();
    text_of(&crate::util::iso(&json!(secs))).replace("+00:00", "Z")
}

/// What one run needs from the runner.
#[derive(Clone)]
pub struct Ctx {
    pub client: Client,
    pub url: String,
    pub projects_dir: PathBuf,
    pub state_dir: PathBuf,
}

/// The program's exit code and output (up to OUTPUT_CAP), killed with its process group at the timeout.
async fn run_program(argv: &[String], cwd: &Path, env: &Env, timeout: Duration) -> Result<(i64, bool, Vec<u8>), String> {
    let (read_fd, write_fd) = {
        let mut fds = [0i32; 2];
        // SAFETY: pipe fills two fds on success.
        if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        (owned(fds[0]), owned(fds[1]))
    };
    let mut cmd = crate::isolation::command(&argv[0]);
    cmd.args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(write_fd.try_clone().map_err(|e| e.to_string())?))
        .stderr(std::process::Stdio::from(write_fd))
        .process_group(0)
        .kill_on_drop(true);
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    drop(cmd); // the parent's copies of the pipe's write end close here, so the read ends when the program does
    let pid = child.id().map(|p| p as i32);
    let reader = tokio::spawn(async move {
        let mut file = tokio::fs::File::from_std(std::fs::File::from(read_fd));
        let mut kept = Vec::new();
        let mut block = vec![0u8; 65536];
        loop {
            match file.read(&mut block).await {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if kept.len() < OUTPUT_CAP {
                        let take = n.min(OUTPUT_CAP - kept.len());
                        kept.extend_from_slice(&block[..take]);
                    }
                }
            }
        }
        kept
    });
    let (code, timed_out) = match tokio::time::timeout(timeout, child.wait()).await {
        Ok(status) => (status.ok().and_then(|s| s.code().map(i64::from).or_else(|| {
            use std::os::unix::process::ExitStatusExt;
            s.signal().map(|sig| -i64::from(sig))
        })).unwrap_or(-1), false),
        Err(_) => {
            if let Some(p) = pid {
                // SAFETY: signalling a process group this runner started.
                unsafe { libc::killpg(p, libc::SIGKILL) };
            }
            let _ = child.start_kill();
            let _ = tokio::time::timeout(Duration::from_secs(5), child.wait()).await;
            (-9, true)
        }
    };
    let output = match tokio::time::timeout(Duration::from_secs(5), reader).await {
        Ok(Ok(kept)) => kept,
        _ => vec![],
    };
    Ok((code, timed_out, output))
}

/// A pipe fd this process owns, closed in programs it starts later.
fn owned(fd: i32) -> std::os::fd::OwnedFd {
    use std::os::fd::FromRawFd;
    // SAFETY: fcntl on a fresh fd this process owns, which OwnedFd then closes.
    unsafe {
        libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
        std::os::fd::OwnedFd::from_raw_fd(fd)
    }
}

#[derive(Default)]
struct Book {
    specs: HashMap<(String, String), (Value, PathBuf, Spec)>,
    next_scan: Option<Instant>,
    next_at: HashMap<(String, String), Instant>,
    running: HashSet<(String, String)>,
    /// The last problem logged per (bot, name), "" for the declaration, so a failing watcher is logged once.
    noted: HashMap<(String, String), String>,
}

pub struct Watchers {
    book: Mutex<Book>,
    slots: Arc<tokio::sync::Semaphore>,
}

impl Default for Watchers {
    fn default() -> Self {
        Watchers { book: Mutex::new(Book::default()), slots: Arc::new(tokio::sync::Semaphore::new(PARALLEL)) }
    }
}

impl Watchers {
    fn problem(&self, key: &(String, String), text: &str) {
        let mut book = self.book.lock().unwrap();
        if book.noted.get(key).map(String::as_str) != Some(text) {
            book.noted.insert(key.clone(), text.to_string());
            log(&format!("Tico runner: watcher {}/{} {text}", key.0, key.1));
        }
    }

    /// The watchers of the active bots hosted here that have finished setup. A bad declaration is logged once.
    fn scan(&self, config: &crate::config::Config, assignments: &[Value]) -> HashMap<(String, String), (Value, PathBuf, Spec)> {
        let mine = config.runner_id();
        let mut found = HashMap::new();
        for entry in assignments {
            let bot = text_of(&entry["bot"]);
            if entry["state"] != "active" || mine.is_empty() || text_of(&entry["runner_id"]) != mine {
                continue;
            }
            if PARKED_STATES.contains(&entry["onboarding_state"].as_str().unwrap_or_default()) {
                continue;
            }
            let path = crate::readiness::local_path(config, &bot);
            if !path.join("AGENT.md").is_file() || !crate::manifest::manifest_path(&path).is_file() {
                continue;
            }
            let Some(manifest) = crate::manifest::read(&path) else { continue };
            let Some(doc) = manifest.as_object() else { continue };
            let key = (bot.clone(), String::new());
            match parse(doc.get("watchers")) {
                Ok(specs) => {
                    self.book.lock().unwrap().noted.remove(&key);
                    for spec in specs {
                        found.insert((bot.clone(), spec.name.clone()), (entry.clone(), path.clone(), spec));
                    }
                }
                Err(e) => {
                    let mut book = self.book.lock().unwrap();
                    if book.noted.get(&key) != Some(&e) {
                        book.noted.insert(key, e.clone());
                        log(&format!("Tico runner: {bot} watchers ignored: {e}"));
                    }
                }
            }
        }
        found
    }

    /// Called from the run loop: starts what is due and returns at once.
    pub fn tick(self: &Arc<Self>, config: &crate::config::Config, assignments: &[Value], ctx: Ctx) {
        let now = Instant::now();
        if self.book.lock().unwrap().next_scan.is_none_or(|t| now >= t) {
            let specs = self.scan(config, assignments);
            let mut book = self.book.lock().unwrap();
            book.specs = specs;
            // Before the first assignments arrive there is nothing to read: look again on the next tick.
            book.next_scan = (!assignments.is_empty()).then_some(now + SCAN_EVERY);
        }
        let due: Vec<_> = {
            let mut book = self.book.lock().unwrap();
            let Book { specs, next_at, running, .. } = &mut *book;
            let mut due = vec![];
            for (key, item) in specs.iter() {
                if running.contains(key) || next_at.get(key).is_some_and(|t| *t > now) {
                    continue;
                }
                running.insert(key.clone());
                next_at.insert(key.clone(), now + Duration::from_secs(item.2.every as u64));
                due.push((key.clone(), item.clone()));
            }
            due
        };
        for (key, (entry, path, spec)) in due {
            let me = self.clone();
            let ctx = ctx.clone();
            tokio::spawn(async move {
                let Ok(_slot) = me.slots.clone().acquire_owned().await else { return };
                // A watcher never stops the runner: a panic in one run ends that run only.
                let run = tokio::spawn({
                    let me = me.clone();
                    let key = key.clone();
                    async move { me.run_once(&ctx, &key, &entry, &path, &spec).await }
                });
                match run.await {
                    Ok(Ok(())) => {}
                    Ok(Err(why)) => me.problem(&key, &format!("failed to run ({why})")),
                    Err(_) => me.problem(&key, "failed to run (the run stopped on an error)"),
                }
                me.book.lock().unwrap().running.remove(&key);
            });
        }
    }

    async fn environment(&self, ctx: &Ctx, bot: &str, entry: &Value, vault: &mut Vault, aid: &str) -> Result<Env, String> {
        let granted = ctx.client.get_query("runner-watcher-credentials", &[("bot", bot.to_string())]).await.map_err(|e| crate::runner::describe(&e))?;
        let env_ctx = EnvContext { url: &ctx.url, projects_dir: &ctx.projects_dir, state_dir: &ctx.state_dir };
        let attempt = json!({"id": aid, "bot": bot, "config": entry.get("config").cloned().unwrap_or(Value::Null), "token": "",
                             "profile": entry.get("profile").cloned().unwrap_or(Value::Null),
                             "computer_label": entry.get("computer_label").cloned().unwrap_or(Value::Null)});
        let mut env = crate::env::environment_with(&env_ctx, &attempt, vault, Some(granted)).await?;
        env.remove("HUB_TOKEN");
        Ok(env)
    }

    /// One run of one watcher; Err only for what stops it before it reports.
    pub async fn run_once(&self, ctx: &Ctx, key: &(String, String), entry: &Value, path: &Path, spec: &Spec) -> Result<(), String> {
        let bot = &key.0;
        let Some(argv) = command(path, &spec.argv) else {
            self.problem(key, "does not run: its file is not in the repository or cannot be executed");
            return Ok(());
        };
        let state = path.join(".state").join(&spec.name);
        crate::isolation::mkdir(&state, 0o700).map_err(|e| e.to_string())?;
        let saved = snapshot(&state);
        let aid = format!("watcher:{bot}:{}", spec.name);
        let mut vault = Vault::default();
        let mut env = self.environment(ctx, bot, entry, &mut vault, &aid).await?;
        let mut secrets: Vec<String> = secret_values(&env);
        for v in &vault.values {
            if !v.is_empty() && !secrets.contains(v) {
                secrets.push(v.clone());
            }
        }
        secrets.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()).then(a.cmp(b)));
        let py_dir = crate::env::python().parent().map(|p| p.display().to_string()).unwrap_or_default();
        let path_var = format!("{py_dir}:{}", env.get("PATH").cloned().unwrap_or_else(|| "/bin:/usr/bin".into()));
        for (k, v) in [
            ("TICO_WATCHER", spec.name.clone()),
            ("TICO_WATCHER_STATE", state.display().to_string()),
            ("HUB_BOT", bot.clone()),
            ("HUB_EMPLOYEE", bot.clone()),
            ("HUB_API_URL", ctx.url.clone()),
            ("HUB_WORKSPACE", ctx.projects_dir.display().to_string()),
            ("PATH", path_var),
        ] {
            env.insert(k.into(), v);
        }
        let started = now();
        let (code, timed_out, output) = run_program(&argv, path, &env, Duration::from_secs(spec.timeout as u64)).await?;
        vault.remove_files();
        let text = redact(&String::from_utf8_lossy(&output), &secrets);
        let (found, log_text) = events(&text);
        let trimmed = log_text.trim();
        let tail = |n: usize| -> String {
            let count = trimmed.chars().count();
            trimmed.chars().skip(count.saturating_sub(n)).collect()
        };
        let body = json!({
            "bot": bot, "name": spec.name, "started": started, "finished": now(), "exit": code.clamp(-255, 255),
            "timed_out": timed_out, "every": spec.every, "output": tail(REPORT_CAP), "events": found,
        });
        if let Err(e) = ctx.client.post("runners/watchers", &body, None).await {
            restore(&state, &saved); // the events did not arrive, so the program must see them again
            self.problem(key, &format!("ran but the hub did not take its report ({}); it will run again", crate::runner::describe(&e)));
            return Ok(());
        }
        if timed_out {
            self.problem(key, "timed out");
        } else if code != 0 {
            self.problem(key, &format!("exited {code}: {}", tail(300)));
        } else {
            self.book.lock().unwrap().noted.remove(key);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bad(yaml: &str) -> String {
        parse(Some(&crate::manifest::parse(yaml).unwrap())).unwrap_err()
    }

    #[test]
    fn declarations_read_like_the_python_reader() {
        let doc = crate::manifest::parse(
            "- {name: hq, run: \"software/hq watch --since '1 h'\", every: 5m}\n- {name: quiet, run: software/x, every: 1h, enabled: no}\n- {name: n, run: software/n, every: 120, timeout: 2m}\n",
        )
        .unwrap();
        let specs = parse(Some(&doc)).unwrap();
        assert_eq!(specs.len(), 2);
        assert_eq!(specs[0], Spec { name: "hq".into(), argv: vec!["software/hq".into(), "watch".into(), "--since".into(), "1 h".into()], every: 300, timeout: 60 });
        assert_eq!((specs[1].every, specs[1].timeout), (120, 120));
        assert_eq!(parse(None).unwrap(), vec![]);
        assert_eq!(bad("x: 1"), "watchers is a list of at most 10");
        assert_eq!(bad("- {name: a, run: s/a, every: 5m, colour: red}"), "watcher 1 has fields other than enabled, every, name, run, timeout");
        assert_eq!(bad("- {name: A, run: s/a, every: 5m}"), "watcher 1: name is lowercase words with hyphens, once");
        assert_eq!(bad("- {name: a, run: s/a, every: 5m}\n- {name: a, run: s/a, every: 5m}"), "watcher 2: name is lowercase words with hyphens, once");
        assert_eq!(bad("- {name: a, run: \"s/a 'x\", every: 5m}"), "watcher a: run has an unclosed quote");
        assert_eq!(bad("- {name: a, run: /bin/sh, every: 5m}"), "watcher a: run starts with a path inside the repository, such as software/a");
        assert_eq!(bad("- {name: a, run: ../x, every: 5m}"), "watcher a: run starts with a path inside the repository, such as software/a");
        assert_eq!(bad("- {name: a, run: s/a}"), "watcher a: a duration is like 30s, 5m or 2h");
        assert_eq!(bad("- {name: a, run: s/a, every: 30s}"), "watcher a: every is between 1m and 24h");
        assert_eq!(bad("- {name: a, run: s/a, every: true}"), "watcher a: a duration is like 5m");
        assert_eq!(bad("- {name: a, run: s/a, every: 5m, timeout: 10m}"), "watcher a: timeout is between 1s and 5m");
        assert_eq!(shlex_split(r#"a "b\"c" 'd\e' f\ g # h"#).unwrap(), vec!["a", "b\"c", "d\\e", "f g", "#", "h"]);
    }

    #[test]
    fn events_are_checked_capped_and_the_rest_is_the_log() {
        let long = "x".repeat(400);
        let out = format!(
            "starting\ntico-event {{\"op\": \"task\", \"key\": \"hq:1\", \"title\": \"{long}\", \"extra\": 1}}\ntico-event {{\"op\": \"nope\", \"key\": \"k\"}}\ntico-event {{\"op\": \"done\", \"key\": \"  \"}}\ntico-event not json\r\ndone"
        );
        let (found, log_text) = events(&out);
        assert_eq!(found, vec![json!({"op": "task", "key": "hq:1", "title": "x".repeat(300)})]);
        assert_eq!(log_text, "starting\ntico-event {\"op\": \"nope\", \"key\": \"k\"}\ntico-event {\"op\": \"done\", \"key\": \"  \"}\ntico-event not json\ndone");
        let many: String = (0..60).map(|i| format!("tico-event {{\"op\": \"comment\", \"key\": \"k{i}\"}}\n")).collect();
        let (found, log_text) = events(&many);
        assert_eq!(found.len(), MAX_EVENTS);
        assert_eq!(log_text.lines().count(), 10);
    }

    #[test]
    fn secrets_are_what_the_grants_added_or_what_their_names_say() {
        let env: Env = [
            ("POSTHOG_KEY", "phc_inherited_or_not"),
            ("SERVICE_URL", "https://svc.example/x"),
            ("CUSTOM", "granted-value"),
            ("HUB_API_URL", "https://hub.example"),
            ("SHORT", "abc"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        assert_eq!(secret_values(&env), vec!["phc_inherited_or_not".to_string(), "granted-value".to_string()]);
    }

    #[test]
    fn only_files_inside_the_repository_run_and_python_runs_with_this_runners_interpreter() {
        let dir = std::env::temp_dir().join(format!("tico-watch-cmd-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("software")).unwrap();
        std::fs::write(dir.join("software/w.py"), "print(1)\n").unwrap();
        std::fs::write(dir.join("software/sh"), "#!/bin/sh\necho\n").unwrap();
        std::fs::write(dir.join("software/plain"), "#!/usr/bin/env python3\n").unwrap();
        std::fs::set_permissions(dir.join("software/sh"), std::fs::Permissions::from_mode(0o755)).unwrap();
        let argv = |s: &str| shlex_split(s).unwrap();
        let py = command(&dir, &argv("software/w.py a")).unwrap();
        assert_eq!(py[0], crate::env::python().display().to_string());
        assert_eq!(&py[2..], ["a"]);
        assert_eq!(command(&dir, &argv("software/plain")).unwrap().len(), 2, "a python shebang runs without the x bit");
        assert!(command(&dir, &argv("software/sh x")).unwrap()[0].ends_with("software/sh"));
        std::os::unix::fs::symlink("/bin/sh", dir.join("software/out")).unwrap();
        assert_eq!(command(&dir, &argv("software/out")), None, "a link out of the repository does not run");
        std::fs::write(dir.join("software/noexec"), "x").unwrap();
        assert_eq!(command(&dir, &argv("software/noexec")), None);
        std::fs::remove_dir_all(dir).ok();
    }

    /// A hub that answers the watcher's two calls: the credentials, then the report (with `report` as status).
    async fn hub(report: u16) -> (String, Arc<Mutex<Vec<String>>>) {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let log_ = seen.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else { return };
                let mut got = vec![];
                let mut buf = vec![0u8; 65536];
                loop {
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    if n == 0 {
                        break;
                    }
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    if let Some(head_end) = text.find("\r\n\r\n") {
                        let length = text[..head_end]
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0)))
                            .unwrap_or(0);
                        if got.len() >= head_end + 4 + length {
                            break;
                        }
                    }
                }
                let text = String::from_utf8_lossy(&got).to_string();
                let (status, body) = if text.starts_with("GET /api/v2/runner-watcher-credentials?bot=ops ") {
                    (200, json!({"credentials": [{"id": "c1", "env": "LINEAR_KEY", "value": "lin_secret_value", "kind": "env"}]}).to_string())
                } else {
                    (report, "{}".to_string())
                };
                log_.lock().unwrap().push(text);
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
            }
        });
        (url, seen)
    }

    #[tokio::test]
    async fn a_run_reports_events_without_secrets_and_a_refused_report_rolls_the_state_back() {
        let dir = std::env::temp_dir().join(format!("tico-watch-run-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("bot/software")).unwrap();
        std::fs::write(
            dir.join("bot/software/w.sh"),
            "#!/bin/sh\n[ -z \"$HUB_TOKEN\" ] || echo HAS_TOKEN\necho seen > \"$TICO_WATCHER_STATE/cursor\"\necho \"key is $LINEAR_KEY\"\necho 'tico-event {\"op\": \"task\", \"key\": \"hq:1\", \"title\": \"New\"}' >&2\nexit 3\n",
        )
        .unwrap();
        std::fs::set_permissions(dir.join("bot/software/w.sh"), std::fs::Permissions::from_mode(0o755)).unwrap();
        let spec = parse(Some(&crate::manifest::parse("- {name: w, run: software/w.sh, every: 1m, timeout: 5s}").unwrap())).unwrap().remove(0);
        let watchers = Watchers::default();
        let key = ("ops".to_string(), "w".to_string());
        for (status, kept) in [(200u16, true), (503, false)] {
            let (url, seen) = hub(status).await;
            let mut client = Client::new(&url, "runner-token", 5, 0).unwrap();
            client.backoff_scale = 0.0;
            let ctx = Ctx { client, url: url.clone(), projects_dir: dir.clone(), state_dir: dir.join("state") };
            let _ = std::fs::remove_dir_all(dir.join("bot/.state"));
            watchers.run_once(&ctx, &key, &json!({"bot": "ops"}), &dir.join("bot"), &spec).await.unwrap();
            let seen = seen.lock().unwrap().clone();
            let report = seen.iter().find(|r| r.starts_with("POST /api/v2/runners/watchers")).expect("a report");
            let body: Value = serde_json::from_str(&report[report.find("\r\n\r\n").unwrap() + 4..]).unwrap();
            assert_eq!(body["events"], json!([{"op": "task", "key": "hq:1", "title": "New"}]));
            assert_eq!(body["output"], "key is [redacted]");
            assert_eq!((body["exit"].as_i64(), body["timed_out"].as_bool(), body["every"].as_i64()), (Some(3), Some(false), Some(60)));
            assert!(body["finished"].as_str().unwrap().ends_with('Z'));
            assert_eq!(dir.join("bot/.state/w/cursor").exists(), kept, "status {status}");
        }
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn a_program_past_its_timeout_is_killed_with_its_children() {
        let dir = std::env::temp_dir().join(format!("tico-watch-kill-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let env: Env = [("PATH".to_string(), "/bin:/usr/bin".to_string())].into_iter().collect();
        let argv: Vec<String> = ["/bin/sh", "-c", "echo before; sleep 30 & sleep 30"].iter().map(|s| s.to_string()).collect();
        let begun = Instant::now();
        let (code, timed_out, output) = run_program(&argv, &dir, &env, Duration::from_millis(500)).await.unwrap();
        assert_eq!((code, timed_out), (-9, true));
        assert_eq!(String::from_utf8_lossy(&output), "before\n");
        assert!(begun.elapsed() < Duration::from_secs(8), "the background child did not keep the pipe open");
        std::fs::remove_dir_all(dir).ok();
    }
}
