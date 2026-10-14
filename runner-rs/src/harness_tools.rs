//! The model CLIs a runner uses, installed and kept current by the runner itself (runner/harness_tools.py).
//!
//! Manifests are runner/harnesses/*.toml, read from the checkout (`crate::env::root()`), else the copies
//! built into this binary. Installs go into the runner's own tools directory, never system-wide:
//!
//!     <tools>/<id>/<uid>/     one complete install (an npm prefix, a venv, or a binary)
//!     <tools>/<id>/current    symlink to the install in use
//!     <tools>/bin/<exe>       symlink put at the END of PATH
//!
//! A harness already on PATH is used as it is and never touched; a new version is staged beside the old one
//! (on a blocking worker) and switched in by `step` only when no running turn uses that host; a pinned
//! harness is never moved by the daily check. `harnesses.json` is the same file the Python runner keeps.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::config::Config;
use crate::util::{epoch_s, log, text_of};

const METHODS: &[&str] = &["npm", "pip", "script", "binary"];
const POLICIES: &[&str] = &["latest", "pinned"];
const AUTH_METHODS: &[&str] = &["device-code", "subscription", "api-key"];
const ACTIONS: &[&str] = &["update", "pin", "unpin"];
/// The adapters runner/hosts/ has (a manifest's `host` must name one).
const HOSTS: &[&str] = &["antigravity", "claude", "codex", "cursor", "gemini", "grok", "musecode", "pi", "fake"];
const VERSION_TIMEOUT_S: u64 = 60;
pub const CHECK_EVERY_S: f64 = 24.0 * 3600.0;
const RETRY_AFTER_S: f64 = 30.0 * 60.0;
const INSTALL_TIMEOUT_S: u64 = 900;
pub const POLL_S: f64 = 5.0;

const BUILT_IN: &[(&str, &str)] = &[
    ("claude-code.toml", include_str!("../../runner/harnesses/claude-code.toml")),
    ("codex.toml", include_str!("../../runner/harnesses/codex.toml")),
    ("cursor-agent.toml", include_str!("../../runner/harnesses/cursor-agent.toml")),
    ("gemini-cli.toml", include_str!("../../runner/harnesses/gemini-cli.toml")),
    ("grok.toml", include_str!("../../runner/harnesses/grok.toml")),
    ("muse-code.toml", include_str!("../../runner/harnesses/muse-code.toml")),
    ("pi.toml", include_str!("../../runner/harnesses/pi.toml")),
];

fn re(p: &str) -> Regex {
    Regex::new(p).unwrap()
}

#[derive(Clone, Debug)]
pub struct Login {
    pub command: Vec<String>,
    pub terminal: bool,
    #[allow(dead_code)] // the server decides the paste box; kept for the manifest's shape
    pub paste_code: bool,
}

#[derive(Clone, Debug)]
pub struct Manifest {
    pub id: String,
    pub name: String,
    pub host: String,
    pub executable: String,
    pub providers: Vec<String>,
    pub catch_all: bool,
    pub install: toml::Table,
    pub version_args: Vec<String>,
    pub version_pattern: String,
    pub policy: String,
    pub pin: String,
    #[allow(dead_code)]
    pub auth_methods: Vec<String>,
    #[allow(dead_code)]
    pub api_key_env: Vec<String>,
    pub login: Option<Login>,
    /// Variables every command the runner runs with this harness sets (version probes, logins), e.g. an
    /// auto-updater's off switch. Sorted by name.
    pub env: Vec<(String, String)>,
}

impl Manifest {
    pub fn method(&self) -> &str {
        self.install.get("method").and_then(|v| v.as_str()).unwrap_or("")
    }

    fn get(&self, key: &str) -> String {
        self.install.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string()
    }

    /// Where the executable sits inside one install directory.
    pub fn bin_relative(&self) -> String {
        match self.method() {
            "npm" => format!("node_modules/.bin/{}", self.executable),
            "pip" => format!("venv/bin/{}", self.executable),
            "script" => self.get("bin"),
            _ => format!("bin/{}", self.executable),
        }
    }
}

fn strings(v: Option<&toml::Value>, field: &str, at: &str, pattern: Option<&Regex>, minimum: usize, default: &[&str]) -> Result<Vec<String>, String> {
    let list: Vec<toml::Value> = match v {
        None => default.iter().map(|s| toml::Value::String(s.to_string())).collect(),
        Some(toml::Value::Array(a)) => a.clone(),
        Some(_) => vec![toml::Value::Integer(0)],
    };
    let ok = list.iter().all(|i| i.as_str().is_some_and(|s| !s.is_empty())) && list.len() >= minimum;
    if !ok {
        return Err(format!("{at}: {field} must be a list of {}string", if minimum > 0 { "at least one " } else { "" }));
    }
    let out: Vec<String> = list.iter().map(|i| i.as_str().unwrap().to_string()).collect();
    if let Some(p) = pattern {
        if let Some(bad) = out.iter().find(|s| !full(p, s)) {
            return Err(format!("{at}: {field} has an invalid entry '{bad}'"));
        }
    }
    Ok(out)
}

fn full(p: &Regex, s: &str) -> bool {
    p.find(s).is_some_and(|m| m.start() == 0 && m.end() == s.len())
}

fn table<'a>(data: &'a toml::Table, name: &str, at: &str) -> Result<std::borrow::Cow<'a, toml::Table>, String> {
    match data.get(name) {
        None => Ok(std::borrow::Cow::Owned(toml::Table::new())),
        Some(toml::Value::Table(t)) => Ok(std::borrow::Cow::Borrowed(t)),
        Some(_) => Err(format!("{at}: [{name}] must be a table")),
    }
}

fn groups(pattern: &str) -> Result<usize, String> {
    Regex::new(pattern).map(|r| r.captures_len() - 1).map_err(|e| e.to_string())
}

/// A validated Manifest from a parsed TOML document (harness_tools.parse).
pub fn parse(data: &toml::Table, source: &str) -> Result<Manifest, String> {
    let mut at = if source.is_empty() { "manifest".to_string() } else { source.to_string() };
    let known = ["id", "name", "host", "executable", "providers", "catch_all", "install", "version", "update", "auth", "login", "env"];
    let mut extra: Vec<&String> = data.keys().filter(|k| !known.contains(&k.as_str())).collect();
    if !extra.is_empty() {
        extra.sort();
        return Err(format!("{at}: unknown field(s) {}", extra.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")));
    }
    let name_re = re(r"^[a-z0-9][a-z0-9-]*$");
    let exe_re = re(r"^[A-Za-z0-9][A-Za-z0-9._-]*$");
    let env_re = re(r"^[A-Z][A-Z0-9_]*$");
    let ident = data.get("id").and_then(|v| v.as_str()).filter(|s| name_re.is_match(s));
    let Some(ident) = ident else { return Err(format!("{at}: id must be lowercase letters, digits and hyphens")) };
    at = format!("{at} ({ident})");
    let name = data.get("name").and_then(|v| v.as_str()).map(str::trim).filter(|s| !s.is_empty());
    let Some(name) = name else { return Err(format!("{at}: name is required")) };
    let host = data.get("host").and_then(|v| v.as_str()).unwrap_or("");
    if !name_re.is_match(host) || !HOSTS.contains(&host) {
        return Err(format!("{at}: host '{host}' is not an adapter in runner/hosts/"));
    }
    let executable = data.get("executable").and_then(|v| v.as_str()).filter(|s| full(&exe_re, s));
    let Some(executable) = executable else { return Err(format!("{at}: executable must be a bare file name")) };
    let providers = strings(data.get("providers"), "providers", &at, Some(&re(r"^[a-z][a-z0-9-]*$")), 1, &[])?;
    if providers.iter().collect::<HashSet<_>>().len() != providers.len() {
        return Err(format!("{at}: providers lists one twice"));
    }
    let install = table(data, "install", &at)?.into_owned();
    let s = |k: &str| install.get(k).and_then(|v| v.as_str());
    let method = s("method").unwrap_or("");
    if !METHODS.contains(&method) {
        return Err(format!("{at}: install.method must be one of {}", METHODS.join(", ")));
    }
    let https = |k: &str| s(k).is_some_and(|u| u.starts_with("https://"));
    match method {
        "npm" | "pip" => {
            if !s("package").is_some_and(|p| full(&re(r"^(@[a-z0-9][a-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$"), p)) {
                return Err(format!("{at}: install.package must be a package name"));
            }
        }
        "script" => {
            if !https("url") {
                return Err(format!("{at}: install.url must be an https URL"));
            }
            if !s("prefix_env").is_some_and(|p| full(&env_re, p)) {
                return Err(format!("{at}: install.prefix_env names the variable that tells the script where to install"));
            }
            let bin = s("bin").unwrap_or("");
            if bin.is_empty() || bin.starts_with('/') || Path::new(bin).components().any(|c| c.as_os_str() == "..") {
                return Err(format!("{at}: install.bin is the executable's path inside the install directory"));
            }
            if !["sh", "bash"].contains(&s("shell").unwrap_or("sh")) {
                return Err(format!("{at}: install.shell is sh or bash"));
            }
            strings(install.get("requires"), "install.requires", &at, Some(&exe_re), 0, &[])?;
        }
        _ => {
            if !https("url") {
                return Err(format!("{at}: install.url must be an https URL (with {{os}} and {{arch}} for the platform)"));
            }
            let hex = re(r"^[0-9a-f]{64}$");
            let ok = install
                .get("sha256")
                .and_then(|v| v.as_table())
                .is_some_and(|t| !t.is_empty() && t.values().all(|v| v.as_str().is_some_and(|x| hex.is_match(x))));
            if !ok {
                return Err(format!(
                    "{at}: a binary download needs install.sha256 per platform (for example linux-arm64), each a sha256 hex digest"
                ));
            }
        }
    }
    if install.contains_key("latest_url") {
        if !https("latest_url") {
            return Err(format!("{at}: install.latest_url must be an https URL"));
        }
        match s("latest_pattern").map(groups) {
            Some(Ok(1)) => {}
            Some(Err(e)) => return Err(format!("{at}: install.latest_pattern is not a regular expression ({e})")),
            _ => return Err(format!("{at}: install.latest_pattern needs exactly one capture group")),
        }
    }
    let version = table(data, "version", &at)?;
    let version_args = strings(version.get("args"), "version.args", &at, None, 0, &["--version"])?;
    let pattern = version.get("pattern").and_then(|v| v.as_str()).unwrap_or(r"(\d+\.\d+\.\d+)").to_string();
    match groups(&pattern) {
        Ok(1) => {}
        Ok(_) => return Err(format!("{at}: version.pattern needs exactly one capture group")),
        Err(e) => return Err(format!("{at}: version.pattern is not a regular expression ({e})")),
    }
    let update = table(data, "update", &at)?;
    let policy = update.get("policy").and_then(|v| v.as_str()).unwrap_or("latest").to_string();
    let pin = match update.get("pin") {
        Some(toml::Value::String(s)) => s.clone(),
        Some(toml::Value::Integer(i)) if *i != 0 => i.to_string(),
        Some(toml::Value::Float(f)) if *f != 0.0 => f.to_string(),
        _ => String::new(),
    };
    if !POLICIES.contains(&policy.as_str()) {
        return Err(format!("{at}: update.policy must be latest or pinned"));
    }
    if policy == "pinned" && !full(&re(r"^[0-9][0-9A-Za-z.+_-]*$"), &pin) {
        return Err(format!("{at}: a pinned harness needs update.pin, a version"));
    }
    let auth = table(data, "auth", &at)?;
    let methods = strings(auth.get("methods"), "auth.methods", &at, None, 0, &[])?;
    if methods.iter().any(|m| !AUTH_METHODS.contains(&m.as_str())) {
        return Err(format!("{at}: auth.methods are {}", AUTH_METHODS.join(", ")));
    }
    let keys = strings(auth.get("api_key_env"), "auth.api_key_env", &at, Some(&env_re), 0, &[])?;
    if methods.iter().any(|m| m == "api-key") && keys.is_empty() {
        return Err(format!("{at}: api-key sign-in needs auth.api_key_env"));
    }
    let login = if data.contains_key("login") {
        let raw = table(data, "login", &at)?;
        let command = strings(raw.get("command"), "login.command", &at, None, 1, &[])?;
        if !methods.iter().any(|m| m == "device-code" || m == "subscription") {
            return Err(format!("{at}: login is for device-code or subscription sign-in"));
        }
        let flag = |k: &str| raw.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
        Some(Login { command, terminal: flag("terminal"), paste_code: flag("paste_code") })
    } else if methods.iter().any(|m| m == "device-code") {
        return Err(format!("{at}: device-code sign-in needs a [login] command for the browser relay"));
    } else {
        None
    };
    let mut env = vec![];
    for (k, v) in table(data, "env", &at)?.iter() {
        match v {
            toml::Value::String(v) if full(&env_re, k) => env.push((k.clone(), v.clone())),
            _ => return Err(format!("{at}: [env] maps variable names to strings")),
        }
    }
    env.sort();
    Ok(Manifest {
        id: ident.into(),
        name: name.into(),
        host: host.into(),
        executable: executable.into(),
        providers,
        catch_all: data.get("catch_all").and_then(|v| v.as_bool()).unwrap_or(false),
        install,
        version_args,
        version_pattern: pattern,
        policy,
        pin,
        auth_methods: methods,
        api_key_env: keys,
        login,
        env,
    })
}

/// Every manifest, by id, from `files` as (file name, text). Two may not claim a provider, host or executable.
pub fn load_from(files: &[(String, String)]) -> Result<BTreeMap<String, Manifest>, String> {
    let mut found = BTreeMap::new();
    let mut files = files.to_vec();
    files.sort();
    for (file, text) in &files {
        let data: toml::Table = toml::from_str(text).map_err(|e| format!("{file}: {e}"))?;
        let m = parse(&data, file)?;
        if file.strip_suffix(".toml") != Some(m.id.as_str()) {
            return Err(format!("{file}: the file is named for the harness id ({})", m.id));
        }
        found.insert(m.id.clone(), m);
    }
    let mut claimed: HashMap<(String, String), String> = HashMap::new();
    for m in found.values() {
        let mut pairs: Vec<(&str, &String)> = m.providers.iter().map(|p| ("provider", p)).collect();
        pairs.push(("host", &m.host));
        pairs.push(("executable", &m.executable));
        for (kind, value) in pairs {
            if let Some(other) = claimed.insert((kind.into(), value.clone()), m.id.clone()) {
                return Err(format!("{}: {kind} {value} is already claimed by {other}", m.id));
            }
        }
    }
    if found.values().filter(|m| m.catch_all).count() > 1 {
        return Err("more than one catch-all harness".into());
    }
    Ok(found)
}

/// The shipped manifests: the checkout's runner/harnesses/, else the copies built in.
pub fn load_all() -> Result<BTreeMap<String, Manifest>, String> {
    let dir = crate::env::root().join("runner").join("harnesses");
    let mut files = vec![];
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name.ends_with(".toml") {
                let text = std::fs::read_to_string(e.path()).map_err(|err| format!("{name}: {err}"))?;
                files.push((name, text));
            }
        }
    }
    if files.is_empty() {
        files = BUILT_IN.iter().map(|(n, t)| (n.to_string(), t.to_string())).collect();
    }
    load_from(&files)
}

fn manifests() -> &'static BTreeMap<String, Manifest> {
    static M: OnceLock<BTreeMap<String, Manifest>> = OnceLock::new();
    M.get_or_init(|| {
        load_all().unwrap_or_else(|e| {
            log(&format!("Tico runner: harness manifests unusable ({e}); using the built-in ones"));
            load_from(&BUILT_IN.iter().map(|(n, t)| (n.to_string(), t.to_string())).collect::<Vec<_>>()).unwrap_or_default()
        })
    })
}

/// The program a runtime runs (`cursor` runs `cursor-agent`); the runtime's own name when no manifest says
/// otherwise. For readiness and hosts to find the executable (the parent wires it).
pub fn executable_for(runtime: &str) -> String {
    manifests().values().find(|m| m.host == runtime).map(|m| m.executable.clone()).unwrap_or_else(|| runtime.to_string())
}

/// The login command (argv after the executable) and whether it needs a terminal, per host (login.py COMMANDS).
/// The manifest's `[env]` for a host: set on its logins and version probes (e.g. MUSE_NO_AUTO_UPDATE).
pub fn env_for(runtime: &str) -> Vec<(String, String)> {
    manifests().values().find(|m| m.host == runtime).map(|m| m.env.clone()).unwrap_or_default()
}

pub fn login_command(runtime: &str) -> Option<Login> {
    manifests().values().find(|m| m.host == runtime).and_then(|m| m.login.clone())
}

/// Where this runner's tools live: TICO_TOOLS_DIR, the registration's `tools_dir`, else beside runner.json.
pub fn tools_dir(config: &Config) -> PathBuf {
    let chosen = std::env::var("TICO_TOOLS_DIR").ok().filter(|s| !s.is_empty()).or_else(|| {
        config.data.get("tools_dir").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(String::from)
    });
    if let Some(c) = chosen {
        return expand(&c);
    }
    config.path.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from(".")).join("tools")
}

fn expand(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(p),
    }
}

/// `path` with `bin_dir` appended last, unless already there: anything the person installed wins.
pub fn path_with(path: &str, bin_dir: &Path) -> String {
    let bin = bin_dir.display().to_string();
    if path.split(':').any(|e| e == bin) {
        return path.to_string();
    }
    if path.is_empty() {
        bin
    } else {
        format!("{path}:{bin}")
    }
}

/// Numbers of a version, for comparing releases.
pub fn version_key(text: &str) -> Vec<u64> {
    let head = text.split('-').next().unwrap_or("").split('+').next().unwrap_or("");
    re(r"\d+").find_iter(head).filter_map(|m| m.as_str().parse().ok()).collect()
}

pub fn newer(latest: &str, installed: &str) -> bool {
    !latest.is_empty() && !installed.is_empty() && version_key(latest) > version_key(installed)
}

fn platform() -> (String, String) {
    let system = match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    };
    let machine = match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        other => other,
    };
    (system.into(), machine.into())
}

fn sha256_hex(data: &[u8]) -> String {
    ring::digest::digest(&ring::digest::SHA256, data).as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

fn fetch(url: &str) -> Result<Vec<u8>, String> {
    let handle = tokio::runtime::Handle::try_current().map_err(|_| "no runtime for downloads".to_string())?;
    let url = url.to_string();
    handle.block_on(async move {
        let http = crate::client::http_client(Some(Duration::from_secs(120)));
        let reply = http.get(&url).header("User-Agent", "tico-runner").send().await.map_err(|e| format!("download failed ({e})"))?;
        if !reply.status().is_success() {
            return Err(format!("HTTP Error {}", reply.status().as_u16()));
        }
        reply.bytes().await.map(|b| b.to_vec()).map_err(|e| format!("download failed ({e})"))
    })
}

#[derive(Clone, Debug)]
struct Job {
    kind: String, // install | update | check
    harness: String,
    version: String,
    request: String,
    staged: Option<(PathBuf, String)>,
}

type Slot = Arc<Mutex<Option<Result<Value, String>>>>;

/// One runner's tools directory, its manifests, and the work of keeping them current.
pub struct Harnesses {
    pub tools: PathBuf,
    state_path: PathBuf,
    pub manifests: BTreeMap<String, Manifest>,
    check_every: f64,
    pub wanted: HashSet<String>,
    needed: HashSet<String>,           // what this computer's bots run on: always installed
    default: HashSet<String>,          // a computer with no bots: the company default, unless the owner turned it off
    pub loaded: bool,                  // the bots' runtimes have been read once; until then nothing is removed
    choices: BTreeMap<String, bool>,   // the owner's on/off per harness for this computer; empty from an older server
    requests: Vec<Job>,
    job: Option<(Job, Slot)>,
    staged: BTreeMap<String, Job>,
    versions: HashMap<(String, i128, i128), String>,
    pub results: Vec<(String, String, String)>,
    state: Map<String, Value>,
}

fn mtime(p: &Path) -> Option<i128> {
    let m = std::fs::metadata(p).ok()?.modified().ok()?;
    Some(m.duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos() as i128)
}

fn sorted(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            Value::Object(keys.into_iter().map(|k| (k.clone(), sorted(&m[k]))).collect())
        }
        other => other.clone(),
    }
}

/// `json.dumps(value, indent=2, sort_keys=True)`: Python's separators and key order.
pub fn py_pretty(v: &Value) -> String {
    let mut out = Vec::new();
    let fmt = serde_json::ser::PrettyFormatter::with_indent(b"  ");
    let mut ser = serde_json::Serializer::with_formatter(&mut out, fmt);
    serde::Serialize::serialize(&sorted(v), &mut ser).unwrap();
    String::from_utf8(out).unwrap()
}

impl Harnesses {
    pub fn new(tools: PathBuf, state_path: PathBuf, manifests: BTreeMap<String, Manifest>) -> Harnesses {
        let mut h = Harnesses {
            tools,
            state_path,
            manifests,
            check_every: CHECK_EVERY_S,
            wanted: HashSet::new(),
            needed: HashSet::new(),
            default: HashSet::new(),
            loaded: false,
            choices: BTreeMap::new(),
            requests: vec![],
            job: None,
            staged: BTreeMap::new(),
            versions: HashMap::new(),
            results: vec![],
            state: Map::new(),
        };
        h.state = h.load();
        h
    }

    fn load(&self) -> Map<String, Value> {
        let value: Value = std::fs::read_to_string(&self.state_path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(json!({}));
        let mut out = Map::new();
        for key in ["overrides", "checked", "latest", "failed"] {
            out.insert(key.into(), value.get(key).filter(|v| v.is_object()).cloned().unwrap_or(json!({})));
        }
        out
    }

    fn save(&self) {
        let r = (|| -> std::io::Result<()> {
            if let Some(dir) = self.state_path.parent() {
                std::fs::create_dir_all(dir)?;
            }
            let tmp = self.state_path.with_extension("tmp");
            std::fs::write(&tmp, py_pretty(&Value::Object(self.state.clone())))?;
            std::fs::rename(&tmp, &self.state_path)
        })();
        if let Err(e) = r {
            log(&format!("Tico runner: could not save harness state ({:?})", e.kind()));
        }
    }

    fn section(&mut self, key: &str) -> &mut Map<String, Value> {
        self.state.entry(key.to_string()).or_insert(json!({})).as_object_mut().unwrap()
    }

    fn read(&self, key: &str, id: &str) -> Value {
        self.state.get(key).and_then(|s| s.get(id)).cloned().unwrap_or(Value::Null)
    }

    /// (policy, pin): the owner's choice on this computer, else the manifest's.
    pub fn policy(&self, m: &Manifest) -> (String, String) {
        let o = self.read("overrides", &m.id);
        if let Some(p) = o["policy"].as_str().filter(|p| POLICIES.contains(p)) {
            return (p.into(), text_of(&o["pin"]));
        }
        (m.policy.clone(), m.pin.clone())
    }

    pub fn bin_dir(&self) -> PathBuf {
        self.tools.join("bin")
    }

    /// (path, source) of the executable a turn would run: "path" for the person's own, "tools" for ours.
    pub fn locate(&self, m: &Manifest) -> (Option<PathBuf>, &'static str) {
        let bin = self.bin_dir();
        let path = std::env::var("PATH").unwrap_or_else(|_| "/bin:/usr/bin".into());
        let rest: Vec<&str> = path.split(':').filter(|e| !e.is_empty() && Path::new(e) != bin).collect();
        if let Some(found) = crate::env::which(&m.executable, &rest.join(":")) {
            return (Some(found), "path");
        }
        let ours = bin.join(&m.executable);
        if std::fs::metadata(&ours).is_ok_and(|md| md.is_file() && md.permissions().mode() & 0o111 != 0) {
            return (Some(ours), "tools");
        }
        (None, "")
    }

    /// The installed version, or "" when the executable does not answer.
    pub fn detect(&mut self, m: &Manifest, path: &Path, timeout_s: u64) -> String {
        let real = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        let (Some(a), Some(b)) = (mtime(path), mtime(&real)) else { return String::new() };
        let key = (path.display().to_string(), a, b);
        if let Some(v) = self.versions.get(&key) {
            return v.clone();
        }
        let version = run(path.as_os_str(), &m.version_args, &m.env, timeout_s)
            .ok()
            .filter(|(code, _)| *code == 0)
            .and_then(|(_, out)| Regex::new(&m.version_pattern).ok()?.captures(&out).map(|c| c[1].to_string()))
            .unwrap_or_default();
        if !version.is_empty() {
            self.versions.insert(key, version.clone());
        }
        version
    }

    pub fn harness_for_runtime(&self, runtime: &str) -> Option<&Manifest> {
        self.manifests.values().find(|m| m.host == runtime)
    }

    fn ids<S: AsRef<str>>(&self, runtimes: &[S]) -> HashSet<String> {
        runtimes.iter().filter_map(|r| self.harness_for_runtime(r.as_ref()).map(|m| m.id.clone())).collect()
    }

    /// Install the harnesses these runtimes (this computer's bots, primary and fallback) run on, plus the ones
    /// the owner turned on here. The company's other providers are not installed. `default` (the company
    /// default, for a computer with no bots yet) is installed unless the owner turned it off. Called once the
    /// bots have been read from the server; only after that is anything removed.
    pub fn want<S: AsRef<str>, D: AsRef<str>>(&mut self, runtimes: &[S], default: &[D]) -> HashSet<String> {
        (self.needed, self.default, self.loaded) = (self.ids(runtimes), self.ids(default), true);
        self.rewant()
    }

    /// The owner's on/off per harness for this computer (`{id: bool}`), as the server keeps it.
    pub fn choose(&mut self, choices: &Map<String, Value>) -> HashSet<String> {
        self.choices = choices
            .iter()
            .filter(|(k, _)| self.manifests.contains_key(*k))
            .map(|(k, v)| (k.clone(), crate::util::truthy(Some(v))))
            .collect();
        self.rewant()
    }

    fn rewant(&mut self) -> HashSet<String> {
        let default = self.default.iter().filter(|id| self.choices.get(*id) != Some(&false));
        self.wanted = self.needed.iter().chain(default).cloned().chain(self.choices.iter().filter(|(_, on)| **on).map(|(k, _)| k.clone())).collect();
        self.wanted.clone()
    }

    /// Off on this computer: the owner said so and none of its bots runs on it. Never before the bots have been
    /// read: a saved "off" must not remove what a bot needs while the server is still being asked.
    fn turned_off(&self, id: &str) -> bool {
        self.loaded && self.choices.get(id) == Some(&false) && !self.needed.contains(id)
    }

    /// The bot problem while this runner installs the runtime's harness ("Codex is installing"), else None.
    pub fn installing(&self, runtime: &str) -> Option<String> {
        let m = self.harness_for_runtime(runtime)?;
        (self.wanted.contains(&m.id) && !self.backing_off(m)).then(|| format!("{} is installing", m.name))
    }

    /// Apply an owner's action: (state, message) with state done | failed | queued.
    pub fn request(&mut self, action: &str, harness: &str, request_id: &str) -> (String, String) {
        let Some(m) = self.manifests.get(harness).cloned().filter(|_| ACTIONS.contains(&action)) else {
            return ("failed".into(), "This computer does not know that harness".into());
        };
        let (path, source) = self.locate(&m);
        if action == "unpin" {
            self.section("overrides").insert(m.id.clone(), json!({"policy": "latest", "pin": ""}));
            self.save();
            return ("done".into(), format!("{} follows the newest release again", m.name));
        }
        let Some(path) = path else { return ("failed".into(), format!("{} is not installed on this computer", m.name)) };
        if source != "tools" {
            return ("failed".into(), format!("{} was installed outside Tico ({}); update it where it was installed", m.name, path.display()));
        }
        if action == "pin" {
            let version = self.detect(&m, &path, 15);
            if version.is_empty() {
                return ("failed".into(), format!("Could not read the installed version of {}", m.name));
            }
            self.section("overrides").insert(m.id.clone(), json!({"policy": "pinned", "pin": version}));
            self.save();
            return ("done".into(), format!("{} is pinned to {version}", m.name));
        }
        if self.requests.iter().any(|j| j.harness == m.id) || self.job.as_ref().is_some_and(|(j, _)| j.harness == m.id && !j.request.is_empty()) {
            return ("queued".into(), "An update is already waiting".into());
        }
        self.requests.push(Job { kind: "update".into(), harness: m.id.clone(), version: String::new(), request: request_id.into(), staged: None });
        ("queued".into(), format!("{} updates when no turn is using it", m.name))
    }

    /// One pass: harvest the worker, switch in staged versions whose host no running turn uses (`busy`),
    /// start the next job. Returns owner requests that finished: [(id, state, message)].
    pub fn step(&mut self, busy: &HashSet<String>) -> Vec<(String, String, String)> {
        self.harvest();
        for id in self.staged.keys().cloned().collect::<Vec<_>>() {
            let m = self.manifests[&id].clone();
            if busy.contains(&m.host) {
                continue;
            }
            let job = self.staged.remove(&id).unwrap();
            if self.turned_off(&id) {
                if let Some((dir, _)) = &job.staged {
                    let _ = std::fs::remove_dir_all(dir);
                }
                if !job.request.is_empty() {
                    self.results.push((job.request.clone(), "failed".into(), format!("{} is turned off on this computer", m.name)));
                }
                continue;
            }
            self.commit(&m, job);
        }
        for (id, m) in self.manifests.clone() {
            // Turned off: removed at a moment no turn uses it and nothing is being installed for it.
            if self.turned_off(&id)
                && !busy.contains(&m.host)
                && !self.staged.contains_key(&id)
                && self.job.as_ref().is_none_or(|(j, _)| j.harness != id)
            {
                self.remove(&m);
            }
        }
        if self.job.is_none() {
            if let Some(job) = self.next() {
                let slot: Slot = Arc::new(Mutex::new(None));
                let (m, tools, into) = (self.manifests[&job.harness].clone(), self.tools.clone(), slot.clone());
                let job2 = job.clone();
                let work = move || {
                    let result = if job2.kind == "check" { newest(&m).map(Value::String) } else { stage(&m, &job2.version, &tools) };
                    *into.lock().unwrap() = Some(result);
                };
                if tokio::runtime::Handle::try_current().is_ok() {
                    tokio::task::spawn_blocking(work);
                } else {
                    std::thread::spawn(work);
                }
                self.job = Some((job, slot));
            }
        }
        std::mem::take(&mut self.results)
    }

    fn harvest(&mut self) {
        let done = self.job.as_ref().and_then(|(_, slot)| slot.lock().unwrap().take());
        let Some(result) = done else { return };
        let (mut job, _) = self.job.take().unwrap();
        let m = self.manifests[&job.harness].clone();
        match result {
            Err(e) => self.failed(&m, &job, &e),
            Ok(v) if job.kind == "check" => {
                self.section("checked").insert(m.id.clone(), json!(epoch_s()));
                self.section("latest").insert(m.id.clone(), v);
                self.save();
            }
            Ok(v) => {
                job.staged = Some((PathBuf::from(text_of(&v["dir"])), text_of(&v["version"])));
                self.staged.insert(m.id.clone(), job);
            }
        }
    }

    fn failed(&mut self, m: &Manifest, job: &Job, message: &str) {
        let words = message.split_whitespace().collect::<Vec<_>>().join(" ");
        let message: String = words.chars().rev().take(300).collect::<Vec<_>>().into_iter().rev().collect();
        if job.kind == "check" {
            self.section("checked").insert(m.id.clone(), json!(epoch_s()));
        } else {
            self.section("failed").insert(m.id.clone(), json!({"at": epoch_s(), "error": message}));
        }
        self.save();
        log(&format!("Tico runner: {} {} failed: {message}", m.name, job.kind));
        if !job.request.is_empty() {
            self.results.push((job.request.clone(), "failed".into(), message));
        }
    }

    fn backing_off(&self, m: &Manifest) -> bool {
        let f = self.read("failed", &m.id);
        f.is_object() && epoch_s() - f["at"].as_f64().unwrap_or(0.0) < RETRY_AFTER_S
    }

    fn next(&mut self) -> Option<Job> {
        if !self.requests.is_empty() {
            return Some(self.requests.remove(0));
        }
        let job = |kind: &str, h: &str, v: &str| Some(Job { kind: kind.into(), harness: h.into(), version: v.into(), request: String::new(), staged: None });
        for id in self.manifests.keys().cloned().collect::<Vec<_>>() {
            let m = self.manifests[&id].clone();
            if self.backing_off(&m) || self.staged.contains_key(&id) {
                continue;
            }
            let (path, source) = self.locate(&m);
            let (policy, pin) = self.policy(&m);
            let Some(path) = path else {
                if self.wanted.contains(&id) {
                    return job("install", &id, if policy == "pinned" { &pin } else { "" });
                }
                continue;
            };
            if source != "tools" || self.turned_off(&id) {
                continue; // the person's own install, or one on its way out
            }
            let installed = self.detect(&m, &path, 15);
            if policy == "pinned" && installed != pin {
                return job("update", &id, &pin);
            }
            if policy == "latest" && newer(&text_of(&self.read("latest", &id)), &installed) {
                return job("update", &id, "");
            }
            if epoch_s() - self.read("checked", &id).as_f64().unwrap_or(0.0) >= self.check_every {
                return job("check", &id, "");
            }
        }
        None
    }

    /// Uninstall what this runner installed for `m`. A person's own install is theirs.
    fn remove(&mut self, m: &Manifest) {
        let (root, entry) = (self.tools.join(&m.id), self.bin_dir().join(&m.executable));
        let link = std::fs::symlink_metadata(&entry).is_ok_and(|md| md.file_type().is_symlink());
        if !root.exists() && !link {
            return;
        }
        if link {
            let _ = std::fs::remove_file(&entry);
        }
        let _ = std::fs::remove_dir_all(&root);
        self.versions.clear();
        for key in ["checked", "latest", "failed"] {
            self.section(key).remove(&m.id);
        }
        self.save();
        log(&format!("Tico runner: removed {}; it is turned off on this computer", m.name));
    }

    /// Make the staged install the one in use. Called only with no running turn on this host.
    fn commit(&mut self, m: &Manifest, job: Job) {
        let root = self.tools.join(&m.id);
        let (dest, version) = job.staged.clone().unwrap();
        let bin = self.bin_dir();
        let switched = (|| -> std::io::Result<()> {
            let link = root.join(".current.new");
            let _ = std::fs::remove_file(&link);
            std::os::unix::fs::symlink(dest.file_name().unwrap(), &link)?;
            std::fs::rename(&link, root.join("current"))?;
            std::fs::create_dir_all(&bin)?;
            let entry = bin.join(".exe.new");
            let _ = std::fs::remove_file(&entry);
            // bin/<exe> -> ../<id>/current/<bin_relative>
            std::os::unix::fs::symlink(Path::new("..").join(&m.id).join("current").join(m.bin_relative()), &entry)?;
            std::fs::rename(&entry, bin.join(&m.executable))?;
            for old in std::fs::read_dir(&root)?.flatten() {
                let p = old.path();
                let link = std::fs::symlink_metadata(&p).map(|md| md.file_type().is_symlink()).unwrap_or(true);
                if p.is_dir() && !link && p != dest {
                    let _ = std::fs::remove_dir_all(&p);
                }
            }
            Ok(())
        })();
        if let Err(e) = switched {
            self.failed(m, &job, &format!("could not switch to the new version ({:?})", e.kind()));
            let _ = std::fs::remove_dir_all(&dest);
            return;
        }
        self.versions.clear();
        self.section("failed").remove(&m.id);
        if job.version.is_empty() {
            self.section("latest").insert(m.id.clone(), json!(version));
        }
        if self.policy(m).0 == "pinned" && job.kind == "update" && job.version.is_empty() {
            // An owner's Update on a pinned harness moves the pin with it.
            self.section("overrides").insert(m.id.clone(), json!({"policy": "pinned", "pin": version}));
        }
        self.save();
        log(&if job.kind == "update" {
            format!("Tico runner: {} {}d to {version}", m.name, job.kind)
        } else {
            format!("Tico runner: installed {} {version}", m.name)
        });
        if !job.request.is_empty() {
            self.results.push((job.request, "done".into(), format!("{} is at {version}", m.name)));
        }
    }

    /// {harness id: {name, runtime, installed, version, managed, source, pinned, pin, authenticated, ...}}.
    pub fn report(&mut self, runtimes: &Map<String, Value>) -> Value {
        let working: Option<Job> = self.job.as_ref().map(|(j, _)| j.clone());
        let queued: HashSet<String> = self.requests.iter().map(|j| j.harness.clone()).collect();
        let mut out = Map::new();
        for (id, m) in self.manifests.clone() {
            let (path, source) = self.locate(&m);
            let installed = path.as_ref().map(|p| self.detect(&m, p, 15)).unwrap_or_default();
            let (policy, pin) = self.policy(&m);
            let latest = text_of(&self.read("latest", &id));
            let row = runtimes.get(&m.host).cloned().unwrap_or(json!({}));
            let failure = self.read("failed", &id);
            let (state, detail) = match &working {
                Some(j) if j.harness == id && j.kind != "check" => (if j.kind == "install" { "installing" } else { "updating" }, String::new()),
                _ if self.staged.contains_key(&id) => ("updating", "Waiting for a running turn to finish".into()),
                _ if queued.contains(&id) => ("queued", "Waiting for its turn".into()),
                _ if failure.is_object() && self.backing_off(&m) => ("failed", text_of(&failure["error"]).chars().take(300).collect()),
                _ => ("idle", String::new()),
            };
            let authenticated = if path.is_some() { row.get("authenticated").filter(|v| truthy_str(v)).cloned().unwrap_or(json!("unknown")) } else { json!("missing") };
            let mut entry = json!({
                "name": m.name, "runtime": m.host, "installed": path.is_some(),
                "version": installed.chars().take(100).collect::<String>(), "managed": source == "tools", "source": source,
                "pinned": policy == "pinned", "pin": if policy == "pinned" { pin } else { String::new() },
                "authenticated": authenticated,
                "update_available": source == "tools" && newer(&latest, &installed),
                "latest": latest.chars().take(100).collect::<String>(), "wanted": self.wanted.contains(&id), "state": state, "detail": detail,
            });
            for key in ["goals", "commands"] {
                if let Some(v) = row.get(key) {
                    entry[key] = v.clone();
                }
            }
            out.insert(id, entry);
        }
        Value::Object(out)
    }
}

fn truthy_str(v: &Value) -> bool {
    !(v.is_null() || v.as_str() == Some("") || v == &Value::Bool(false))
}

/// Run a command to completion with a timeout: (exit code, stdout + "\n" + stderr).
fn run(program: &std::ffi::OsStr, args: &[String], env: &[(String, String)], timeout_s: u64) -> Result<(i32, String), String> {
    let mut cmd = Command::new(program);
    cmd.args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    let mut child = cmd.spawn().map_err(|e| format!("{} could not start ({:?})", Path::new(program).display(), e.kind()))?;
    let (mut so, mut se) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
    let a = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::Read::read_to_string(&mut so, &mut s);
        s
    });
    let b = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::Read::read_to_string(&mut se, &mut s);
        s
    });
    let deadline = std::time::Instant::now() + Duration::from_secs(timeout_s);
    let status = loop {
        if let Some(s) = child.try_wait().map_err(|e| e.to_string())? {
            break s;
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{} timed out after {timeout_s}s", Path::new(program).display()));
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let (out, err) = (a.join().unwrap_or_default(), b.join().unwrap_or_default());
    Ok((status.code().unwrap_or(-1), format!("{out}\n{err}")))
}

fn install_env() -> Vec<(String, String)> {
    [("npm_config_update_notifier", "false"), ("npm_config_fund", "false"), ("npm_config_audit", "false"), ("PIP_DISABLE_PIP_VERSION_CHECK", "1")]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn exec(argv: &[String], extra: &[(String, String)]) -> Result<String, String> {
    let mut env = install_env();
    env.extend(extra.iter().cloned());
    let (code, out) = run(std::ffi::OsStr::new(&argv[0]), &argv[1..], &env, INSTALL_TIMEOUT_S)?;
    if code != 0 {
        let lines: Vec<&str> = out.trim().lines().collect();
        let tail = lines[lines.len().saturating_sub(3)..].join(" | ");
        return Err(format!("{} exited {code}: {tail}", Path::new(&argv[0]).file_name().unwrap_or_default().to_string_lossy()));
    }
    Ok(out)
}

fn need(program: &str) -> Result<(), String> {
    if crate::env::which(program, &std::env::var("PATH").unwrap_or_default()).is_none() {
        return Err(format!("{program} is not installed on this computer, and this harness needs it"));
    }
    Ok(())
}

fn python() -> String {
    crate::env::python().display().to_string()
}

/// The newest release of the harness, or "" when there is no way to ask.
fn newest(m: &Manifest) -> Result<String, String> {
    let text = match m.method() {
        "npm" => {
            need("npm")?;
            exec(&["npm".into(), "view".into(), m.get("package"), "version".into()], &[])?
        }
        "pip" => {
            let t = exec(&[python(), "-m".into(), "pip".into(), "index".into(), "versions".into(), m.get("package")], &[])?;
            re(r"\(([0-9][^)]*)\)").captures(&t).map(|c| c[1].to_string()).unwrap_or_default()
        }
        _ if !m.get("latest_url").is_empty() => {
            let body = String::from_utf8_lossy(&fetch(&m.get("latest_url"))?).to_string();
            return Ok(Regex::new(&m.get("latest_pattern")).ok().and_then(|r| r.captures(&body).map(|c| c[1].to_string())).unwrap_or_default());
        }
        _ => return Ok(String::new()),
    };
    Ok(re(r"\d+(?:\.\d+)+\S*").find(&text).map(|f| f.as_str().to_string()).unwrap_or_default())
}

/// Install one complete copy beside whatever is there and prove it answers `--version`.
fn stage(m: &Manifest, version: &str, tools: &Path) -> Result<Value, String> {
    let root = tools.join(&m.id);
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let dest = root.join(&uuid::Uuid::new_v4().simple().to_string()[..10]);
    std::fs::create_dir(&dest).map_err(|e| e.to_string())?;
    let result = (|| {
        install(m, version, &dest)?;
        let path = dest.join(m.bin_relative());
        if !path.exists() {
            return Err(format!("the install did not produce {}", m.bin_relative()));
        }
        let found = run(path.as_os_str(), &m.version_args, &[], VERSION_TIMEOUT_S)
            .ok()
            .filter(|(c, _)| *c == 0)
            .and_then(|(_, out)| Regex::new(&m.version_pattern).ok()?.captures(&out).map(|c| c[1].to_string()))
            .unwrap_or_default();
        if found.is_empty() {
            return Err(format!("{} does not answer {}", m.executable, m.version_args.join(" ")));
        }
        Ok(json!({"dir": dest.display().to_string(), "version": found}))
    })();
    if result.is_err() {
        let _ = std::fs::remove_dir_all(&dest);
    }
    result
}

fn install(m: &Manifest, version: &str, dest: &Path) -> Result<(), String> {
    let d = dest.display().to_string();
    match m.method() {
        "npm" => {
            need("npm")?;
            let spec = format!("{}@{}", m.get("package"), if version.is_empty() { "latest" } else { version });
            exec(&["npm", "install", "--prefix", &d, "--no-audit", "--no-fund", "--loglevel=error", &spec].map(String::from), &[])?;
        }
        "pip" => {
            exec(&[python(), "-m".into(), "venv".into(), format!("{d}/venv")], &[])?;
            let spec = if version.is_empty() { m.get("package") } else { format!("{}=={version}", m.get("package")) };
            exec(&[format!("{d}/venv/bin/pip"), "install".into(), "--no-cache-dir".into(), spec], &[])?;
        }
        "script" => {
            let shell = m.install.get("shell").and_then(|v| v.as_str()).unwrap_or("sh").to_string();
            need(&shell)?;
            for r in m.install.get("requires").and_then(|v| v.as_array()).into_iter().flatten().filter_map(|v| v.as_str()) {
                need(r)?;
            }
            let url = m.get("url");
            if !version.is_empty() && !url.contains("{version}") {
                return Err(format!("this harness's installer only installs the newest release, so it cannot be moved to {version}"));
            }
            let body = fetch(&url.replace("{version}", if version.is_empty() { "latest" } else { version }))?;
            let expected = m.get("sha256");
            if !expected.is_empty() && sha256_hex(&body) != expected {
                return Err("the install script does not match its pinned checksum".into());
            }
            let script = dest.join("install.sh");
            std::fs::write(&script, &body).map_err(|e| e.to_string())?;
            exec(&[shell, script.display().to_string()], &[("NO_COLOR".into(), "1".into()), (m.get("prefix_env"), d.clone())])?;
            let _ = std::fs::remove_file(&script);
        }
        _ => {
            let (system, machine) = platform();
            let key = format!("{system}-{machine}");
            let digest = m.install.get("sha256").and_then(|t| t.get(&key)).and_then(|v| v.as_str()).map(String::from);
            let Some(digest) = digest else { return Err(format!("no download is published for {key}")) };
            let url = m.get("url").replace("{os}", &system).replace("{arch}", &machine).replace("{version}", if version.is_empty() { "latest" } else { version });
            let body = fetch(&url)?;
            if sha256_hex(&body) != digest {
                return Err("the download does not match its checksum".into());
            }
            let target = dest.join("bin");
            std::fs::create_dir(&target).map_err(|e| e.to_string())?;
            let exe = target.join(&m.executable);
            if url.ends_with(".tar.gz") || url.ends_with(".tgz") {
                let archive = dest.join("download.tgz");
                std::fs::write(&archive, &body).map_err(|e| e.to_string())?;
                let a = archive.display().to_string();
                let listing = exec(&["tar".into(), "-tzf".into(), a.clone()], &[])?;
                let member = listing.lines().find(|l| Path::new(l.trim()).file_name().is_some_and(|n| n == m.executable.as_str()) && !l.ends_with('/'));
                let Some(member) = member else { return Err(format!("the archive holds no {}", m.executable)) };
                let unpack = dest.join("unpack");
                std::fs::create_dir(&unpack).map_err(|e| e.to_string())?;
                exec(&["tar".into(), "-xzf".into(), a, "-C".into(), unpack.display().to_string(), member.trim().to_string()], &[])?;
                std::fs::rename(unpack.join(member.trim()), &exe).map_err(|e| e.to_string())?;
                let _ = std::fs::remove_dir_all(&unpack);
                let _ = std::fs::remove_file(&archive);
            } else {
                std::fs::write(&exe, &body).map_err(|e| e.to_string())?;
            }
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------- shared state

static SHARED: OnceLock<Arc<Mutex<Harnesses>>> = OnceLock::new();
static TURNS: OnceLock<Mutex<HashMap<String, Vec<String>>>> = OnceLock::new();

/// This runner's one installer, made on first use (with the tools bin put last on PATH). Call it before the
/// tokio runtime starts threads when possible (main), so PATH is changed while nothing else reads it.
pub fn init(config: &Config) -> Arc<Mutex<Harnesses>> {
    SHARED
        .get_or_init(|| {
            let h = Harnesses::new(tools_dir(config), config.state_dir().join("harnesses.json"), manifests().clone());
            expose_path(&h.bin_dir());
            Arc::new(Mutex::new(h))
        })
        .clone()
}

pub fn shared() -> Option<Arc<Mutex<Harnesses>>> {
    SHARED.get().cloned()
}

/// Put the tools bin last on this process's PATH (harness_tools.expose_path).
pub fn expose_path(bin_dir: &Path) {
    let path = std::env::var("PATH").unwrap_or_default();
    let new = path_with(&path, bin_dir);
    if new != path {
        std::env::set_var("PATH", new);
    }
}

/// A turn's hosts (its runtime and its fallback's), so no update is switched in under it. Execute calls this
/// when a turn starts and `turn_done` when it ends (the parent wires it; until then any running turn counts).
pub fn turn_runtimes(aid: &str, runtimes: &[String]) {
    TURNS.get_or_init(Default::default).lock().unwrap().insert(aid.into(), runtimes.to_vec());
}

pub fn turn_done(aid: &str) {
    TURNS.get_or_init(Default::default).lock().unwrap().remove(aid);
}

/// Host names running turns use; with turns active but none registered, every host (never switch mid-turn).
pub fn busy(active: &HashSet<String>) -> HashSet<String> {
    let turns = TURNS.get_or_init(Default::default).lock().unwrap();
    let mut busy: HashSet<String> = HashSet::new();
    for aid in active {
        match turns.get(aid) {
            Some(r) => busy.extend(r.iter().cloned()),
            None => busy.extend(HOSTS.iter().map(|h| h.to_string())),
        }
    }
    busy
}

/// The readiness `harnesses` value (readiness.document adds it); None before the installer exists.
/// Runs the version probes on a blocking thread.
pub async fn report(runtimes: &Map<String, Value>) -> Option<Value> {
    let h = shared()?;
    let runtimes = runtimes.clone();
    tokio::task::spawn_blocking(move || h.lock().unwrap().report(&runtimes)).await.ok()
}

/// Owner actions from the server (backend/harness_actions.py): take new requests, step the installer, report.
pub struct Relay {
    known: HashMap<String, String>,
}

impl Relay {
    pub fn new() -> Relay {
        Relay { known: HashMap::new() }
    }

    async fn send(client: &Client, id: &str, state: &str, message: &str) -> bool {
        let body = json!({"state": state, "message": message.chars().take(300).collect::<String>()});
        client.post(&format!("runner-harness-actions/{id}/report"), &body, None).await.is_ok()
    }

    pub async fn tick(&mut self, h: &Arc<Mutex<Harnesses>>, client: &Client, busy: HashSet<String>, ask: bool) -> Result<(), crate::client::ApiError> {
        if ask {
            let reply = client.get("runner-harness-actions").await?;
            // The owner's on/off per harness here. A server from before the setting sends none: the computer
            // then installs what its bots run on and keeps what it already has.
            if let Some(choices) = reply["choices"].as_object() {
                h.lock().unwrap().choose(choices);
            }
            for row in reply["actions"].as_array().cloned().unwrap_or_default() {
                let id = text_of(&row["id"]);
                if !row.is_object() || id.is_empty() || self.known.contains_key(&id) {
                    continue;
                }
                let (h2, action, harness, rid) = (h.clone(), text_of(&row["action"]), text_of(&row["harness"]), id.clone());
                let (state, message) = tokio::task::spawn_blocking(move || h2.lock().unwrap().request(&action, &harness, &rid))
                    .await
                    .unwrap_or_else(|_| ("failed".into(), "The request could not be applied".into()));
                self.known.insert(id.clone(), state.clone());
                Self::send(client, &id, if state == "queued" { "running" } else { &state }, &message).await;
            }
        }
        let h2 = h.clone();
        let done = tokio::task::spawn_blocking(move || h2.lock().unwrap().step(&busy)).await.unwrap_or_default();
        for (id, state, message) in done {
            self.known.insert(id.clone(), state.clone());
            if !Self::send(client, &id, &state, &message).await {
                h.lock().unwrap().results.push((id, state, message)); // said again next tick
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn built_in() -> BTreeMap<String, Manifest> {
        load_from(&BUILT_IN.iter().map(|(n, t)| (n.to_string(), t.to_string())).collect::<Vec<_>>()).unwrap()
    }

    #[test]
    fn shipped_manifests_parse_and_claim_once() {
        let all = built_in();
        assert_eq!(all.len(), 7);
        let claude = &all["claude-code"];
        assert_eq!((claude.host.as_str(), claude.method(), claude.bin_relative().as_str()), ("claude", "npm", "node_modules/.bin/claude"));
        let login = claude.login.as_ref().unwrap();
        assert_eq!(login.command, vec!["auth", "login", "--claudeai"]);
        assert!(login.terminal && login.paste_code);
        assert_eq!(all["cursor-agent"].bin_relative(), ".local/bin/cursor-agent");
        assert_eq!(all["muse-code"].env, vec![("MUSE_NO_AUTO_UPDATE".to_string(), "1".to_string())]);
        assert!(all["pi"].catch_all);
        let mut dup = BUILT_IN.iter().map(|(n, t)| (n.to_string(), t.to_string())).collect::<Vec<_>>();
        dup.push(("other.toml".into(), BUILT_IN[1].1.replace("id = \"codex\"", "id = \"other\"")));
        assert!(load_from(&dup).unwrap_err().contains("already claimed"));
        let bad: toml::Table = toml::from_str("id = \"x\"\nname = \"X\"\nhost = \"codex\"\nexecutable = \"x\"\nproviders = [\"x\"]\n[install]\nmethod = \"apt\"").unwrap();
        assert!(parse(&bad, "x.toml").unwrap_err().contains("install.method"));
    }

    #[test]
    fn versions_compare_by_numbers() {
        assert!(newer("2.1.300", "2.1.299"));
        assert!(!newer("2.1.29", "2.1.299"));
        assert!(!newer("", "1.0.0"));
        assert_eq!(version_key("2026.01.02-abc"), vec![2026, 1, 2]);
    }

    #[test]
    fn state_round_trips_like_python_and_pin_unpin() {
        let dir = std::env::temp_dir().join(format!("tico-harness-{}", uuid::Uuid::new_v4().simple()));
        let state = dir.join("harnesses.json");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&state, r#"{"latest": {"codex": "1.2.3"}, "overrides": [], "extra": 1}"#).unwrap();
        let mut h = Harnesses::new(dir.join("tools"), state.clone(), built_in());
        assert_eq!(h.read("latest", "codex"), json!("1.2.3"));
        assert_eq!(h.request("unpin", "codex", "r1").0, "done");
        assert_eq!(h.request("pin", "nope", "r2").1, "This computer does not know that harness");
        let text = std::fs::read_to_string(&state).unwrap();
        assert_eq!(
            text,
            "{\n  \"checked\": {},\n  \"failed\": {},\n  \"latest\": {\n    \"codex\": \"1.2.3\"\n  },\n  \"overrides\": {\n    \"codex\": {\n      \"pin\": \"\",\n      \"policy\": \"latest\"\n    }\n  }\n}"
        );
        assert_eq!(h.policy(&h.manifests["codex"].clone()), ("latest".into(), "".into()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn tools_bin_goes_last_on_path_once() {
        let bin = Path::new("/t/bin");
        assert_eq!(path_with("/usr/bin:/bin", bin), "/usr/bin:/bin:/t/bin");
        assert_eq!(path_with("/usr/bin:/t/bin", bin), "/usr/bin:/t/bin");
    }

    #[test]
    fn a_computer_wants_what_its_bots_run_on_and_removes_what_the_owner_turned_off() {
        let dir = std::env::temp_dir().join(format!("tico-harness-{}", uuid::Uuid::new_v4().simple()));
        let mut h = Harnesses::new(dir.join("tools"), dir.join("harnesses.json"), built_in());
        let none: [&str; 0] = [];
        let choices = json!({"codex": true, "claude-code": false, "cursor-agent": false, "unknown": true});
        h.choose(choices.as_object().unwrap());
        assert!(!h.turned_off("cursor-agent")); // nothing is off before the bots were read
        // An empty computer takes the company default, unless the owner turned it off.
        assert!(h.want(&none, &["gemini"]).contains("gemini-cli"));
        assert!(!h.want(&none, &["claude"]).contains("claude-code"));
        assert_eq!(h.want(&["claude", "nope"], &none), HashSet::from(["claude-code".to_string(), "codex".to_string()]));
        assert_eq!(h.choose(choices.as_object().unwrap()), HashSet::from(["claude-code".to_string(), "codex".to_string()]));
        assert!(!h.turned_off("claude-code") && h.turned_off("cursor-agent")); // a bot here still needs Claude Code
        assert_eq!(h.installing("codex").as_deref(), Some("Codex is installing"));
        assert!(h.installing("cursor").is_none());
        // A turned-off install this runner made is removed when no turn uses it.
        let cursor = h.manifests["cursor-agent"].clone();
        std::fs::create_dir_all(dir.join("tools").join("cursor-agent").join("x")).unwrap();
        std::fs::create_dir_all(h.bin_dir()).unwrap();
        std::os::unix::fs::symlink("../cursor-agent/current/x", h.bin_dir().join(&cursor.executable)).unwrap();
        h.step(&HashSet::from(["cursor".to_string()]));
        assert!(dir.join("tools").join("cursor-agent").exists()); // busy: kept
        h.check_every = f64::INFINITY;
        h.step(&HashSet::new());
        assert!(!dir.join("tools").join("cursor-agent").exists());
        assert!(std::fs::symlink_metadata(h.bin_dir().join(&cursor.executable)).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
