//! The environment a turn runs in (`Runner.environment` in the old Python runner): the process's own settings,
//! the bot's live vault grants, and the hub variables, PATH and git identity Tico sets itself.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use serde_json::Value;

use crate::client::Client;
use crate::util::text_of;

pub type Env = BTreeMap<String, String>;

/// The process settings a turn and the runner's own git keep (`runner_support/safe_git.py` _SETTINGS), plus `LC_*`.
pub const SETTINGS: &[&str] = &[
    "HOME", "PATH", "SHELL", "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP", "LANG", "TZ", "TERM", "COLORTERM", "CODEX_HOME",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "GIT_SSL_CAINFO", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "https_proxy", "http_proxy", "no_proxy", "VIRTUAL_ENV",
    "CONDA_PREFIX", "PYENV_ROOT", "CARGO_HOME", "RUSTUP_HOME", "GOPATH", "JAVA_HOME", "SSH_AUTH_SOCK",
];
pub const RUNNER_HUB_ENV: &[&str] = &[
    "HUB_TOKEN", "HUB_API_URL", "HUB_WORKSPACE", "HUB_EMPLOYEE", "HUB_OPERATION_ID", "HUB_BOT", "HUB_DIR", "HUB_DB",
    "HUB_HUMAN_OVERRIDE", "HUB_INGEST_TOKEN", "HUB_RUNNER_CONFIG", "HUB_TASK_ID",
];
pub const RESERVED_ENV: &[&str] = &["HOME", "PATH", "SHELL", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "CODEX_HOME", "CLAUDE_CONFIG_DIR"];
pub const RESERVED_PREFIXES: &[&str] = &["TICO_", "DYLD_", "LD_"];
/// Keys a turn never inherits unless a grant brings them (`runner_support/op.py` SECRET_KEYS).
pub const SECRET_KEYS: &[&str] = &["OPENROUTER_API_KEY", "HUB_INGEST_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN"];

pub fn process_environment() -> Env {
    std::env::vars().filter(|(k, _)| SETTINGS.contains(&k.as_str()) || k.starts_with("LC_")).collect()
}

/// Whether a granted credential's variable is one the turn sets itself or the computer runs on.
pub fn reserved_credential(key: &str) -> bool {
    static NAME: OnceLock<Regex> = OnceLock::new();
    let name = NAME.get_or_init(|| Regex::new(r"^[A-Z_][A-Z0-9_]*$").unwrap());
    !key.is_empty()
        && (!name.is_match(key)
            || RESERVED_ENV.contains(&key)
            || RUNNER_HUB_ENV.contains(&key)
            || RESERVED_PREFIXES.iter().any(|p| key.starts_with(p)))
}

/// The Tico checkout this runner belongs to (`HUB_DIR`): `TICO_ROOT`, else the first directory above the
/// executable that holds `clients/hubmcp.py`.
pub fn root() -> PathBuf {
    static ROOT: OnceLock<PathBuf> = OnceLock::new();
    ROOT.get_or_init(|| {
        if let Ok(r) = std::env::var("TICO_ROOT") {
            if !r.is_empty() {
                return PathBuf::from(r);
            }
        }
        let exe = std::env::current_exe().ok().and_then(|p| p.canonicalize().ok());
        let mut dir = exe.as_deref().and_then(Path::parent).map(Path::to_path_buf);
        while let Some(d) = dir {
            if d.join("clients").join("hubmcp.py").is_file() {
                return d;
            }
            dir = d.parent().map(Path::to_path_buf);
        }
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    })
    .clone()
}

/// The Python that runs `hub` and the hub MCP server: `TICO_RUNNER_PYTHON`, the checkout's runner venv, its
/// cloud venv, else `python3` on PATH. Its directory goes first on the turn's PATH, as the Python runner puts
/// its own interpreter there.
pub fn python() -> PathBuf {
    static PY: OnceLock<PathBuf> = OnceLock::new();
    PY.get_or_init(|| {
        if let Ok(p) = std::env::var("TICO_RUNNER_PYTHON") {
            if !p.is_empty() {
                return PathBuf::from(p);
            }
        }
        for venv in ["runtime/runner-venv/bin/python", "runtime/cloud-venv/bin/python"] {
            let p = root().join(venv);
            if p.exists() {
                return p;
            }
        }
        which("python3", &std::env::var("PATH").unwrap_or_default()).unwrap_or_else(|| PathBuf::from("python3"))
    })
    .clone()
}

pub fn which(name: &str, path: &str) -> Option<PathBuf> {
    if name.contains('/') {
        let p = PathBuf::from(name);
        return is_executable(&p).then_some(p);
    }
    path.split(':').filter(|d| !d.is_empty()).map(|d| Path::new(d).join(name)).find(|p| is_executable(p))
}

fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

/// What one turn's vault grants put in its environment, kept to scrub and to clean up after it.
#[derive(Default)]
pub struct Vault {
    pub values: Vec<String>,
    pub names: BTreeSet<String>,
    pub files: Vec<PathBuf>,
    file_vars: BTreeMap<String, usize>,
    /// The turn's relay to the Tico AI Gateway (gateway_relay.rs), stopped with the vault.
    pub relay: Option<crate::gateway_relay::Relay>,
    pub turn_user: Option<crate::turn_users::Lease>,
    /// The company subscriptions this turn signs in with (crate::model_subscriptions).
    pub subscriptions: Vec<crate::model_subscriptions::Delivered>,
    /// The bot's browser proxy, and the OS users this turn lets use it (crate::browser_exit).
    pub browser_exit: Option<crate::browser_exit::Admission>,
}

impl Vault {
    /// A subscription host never receives the Gateway token. Its file grants keep their existing bot UID;
    /// a Gateway primary or fallback gets private copies accessible only by its leased user.
    /// The identity a hop runs as and the files it reads. `isolated` is the hop harness's `isolated_user` capability:
    /// a leased turn runs such a harness as its own user, with private copies of its file credentials, its own HOME and
    /// TMPDIR. Any other harness keeps owner-only sign-in files in the bot's home (Claude Code, Codex and the rest), so
    /// it runs as `bot` with the files in the bot's turn directory, lease or not.
    /// A Tico model needs the relay, and under isolation its own user too; on a cloud machine (gateway_relay.rs
    /// `Separation::TrustGroup`) it runs on the relay alone, as this runner's user.
    pub fn for_model(&mut self, env: &mut Env, model: Option<&str>, isolated: bool) -> Result<Option<(u32, u32)>, String> {
        self.for_model_under(env, model, isolated, crate::isolation::enabled())
    }

    pub(crate) fn for_model_under(&mut self, env: &mut Env, model: Option<&str>, isolated: bool, isolation: bool) -> Result<Option<(u32, u32)>, String> {
        let gateway = crate::hosts::pi::is_gateway(model);
        if gateway && (self.relay.is_none() || isolation && self.turn_user.is_none()) {
            return Err("Tico model turn has no private user and relay (not_isolated)".into());
        }
        if !gateway {
            for name in [crate::gateway_relay::KEY_ENV, crate::gateway_relay::URL_ENV, crate::gateway_relay::DIR_ENV] { env.remove(name); }
        }
        let Some(user) = &self.turn_user else { return Ok(None) };
        let own = gateway || isolated;
        let (id, dir) = if own {
            (user.identity(), user.dir.clone())
        } else {
            (crate::isolation::identity().ok_or("missing Docker bot identity")?, crate::isolation::turn_dir())
        };
        user.move_files(env, &mut self.files, id, &dir)?;
        if let Some(admission) = self.browser_exit.as_mut() {
            admission.allow(id.0); // the user this hop's browser runs as
        }
        for (name, index) in &self.file_vars { env.insert(name.clone(), self.files[*index].display().to_string()); }
        if own {
            user.private_home(env)?;
            for name in ["TMPDIR", "TMP", "TEMP"] { env.insert(name.into(), user.dir.display().to_string()); }
        }
        if gateway {
            env.insert(crate::gateway_relay::DIR_ENV.into(), user.dir.join("pi").display().to_string());
        }
        Ok(own.then_some(user.identity()))
    }

    pub fn remove_files(&mut self) {
        for f in self.files.drain(..) {
            let _ = std::fs::remove_file(f);
        }
    }
}

impl Drop for Vault {
    fn drop(&mut self) {
        // Revoke the connection before a cleaned UID can be lent to another turn.
        self.relay.take();
        self.remove_files();
        // A turn that ended before its own `finish` (an error, a panic) still lets go of its subscription homes.
        crate::model_subscriptions::finish(&mut self.subscriptions);
        self.turn_user.take();
    }
}

pub struct EnvContext<'a> {
    pub url: &'a str,
    pub projects_dir: &'a Path,
    pub state_dir: &'a Path,
}

/// The turn's environment, and the vault values it carries.
pub async fn environment(ctx: &EnvContext<'_>, attempt: &Value, vault: &mut Vault) -> Result<Env, String> {
    environment_with(ctx, attempt, vault, None).await
}

/// `environment` with the grants already read (a watcher's, from `runner-watcher-credentials`).
pub async fn environment_with(ctx: &EnvContext<'_>, attempt: &Value, vault: &mut Vault, granted: Option<Value>) -> Result<Env, String> {
    let bot = attempt["bot"].as_str().unwrap_or_default();
    let token = attempt["token"].as_str().unwrap_or_default();
    let mut env = process_environment();
    let mut vault_keys = BTreeSet::new();
    if crate::util::truthy(attempt.get("credential_vault")) || granted.is_some() {
        let granted = match granted {
            Some(g) => g,
            None => {
                let client = Client::new(ctx.url, token, 15, 1)?;
                client.get("credential-runtime").await.map_err(|e| format!("credential-runtime: {}", e.detail))?
            }
        };
        let items = granted["credentials"].as_array().cloned().unwrap_or_default();
        vault.values = items.iter().map(|i| text_of(&i["value"])).collect();
        let usable: Vec<&Value> = items.iter().filter(|i| !reserved_credential(i["env"].as_str().unwrap_or_default())).collect();
        for item in &items {
            let key = item["env"].as_str().unwrap_or_default();
            if !key.is_empty() && reserved_credential(key) {
                // A credential under one of Tico's own names would replace what the turn runs on.
                let name = item["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(key);
                crate::util::diagnostic(&format!(
                    "Tico runner: {bot}: left the credential {name} out of this turn: {key} is a reserved variable name"
                ));
            }
        }
        vault.names = usable.iter().filter_map(|i| i["env"].as_str()).filter(|k| !k.is_empty()).map(String::from).collect();
        for item in &usable {
            let key = item["env"].as_str().unwrap_or_default();
            if key.is_empty() {
                continue;
            }
            if items.iter().any(|o| o["env"].as_str() == Some(key) && o["id"] != item["id"]) {
                return Err("Multiple granted credentials use the same environment name".into());
            }
            let value = text_of(&item["value"]);
            if item["kind"].as_str() == Some("file") {
                // Under isolation the state directory is closed to the bot user: the file goes where its turn reads.
                let dir = if let Some(user) = &vault.turn_user { user.dir.clone() }
                    else if crate::isolation::enabled() { crate::isolation::turn_dir() } else { ctx.state_dir.to_path_buf() };
                // A leased user's own directory holds one turn's at a time: the same path every attempt, so a pi kept
                // on that user (hosts/pi.rs Pool) is started with the same environment.
                let name = if vault.turn_user.is_some() { key.to_string() } else { uuid::Uuid::new_v4().simple().to_string() };
                let path = dir.join(format!("tico-credential-{name}"));
                if vault.turn_user.is_some() {
                    let _ = std::fs::remove_file(&path);
                }
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&path)
                    .map_err(|e| format!("cannot write a credential file: {e}"))?;
                vault.files.push(path.clone());
                vault.file_vars.insert(key.to_string(), vault.files.len() - 1);
                if let Some(user) = &vault.turn_user {
                    use std::os::fd::AsRawFd;
                    let (uid, gid) = user.identity();
                    if unsafe { libc::fchown(f.as_raw_fd(), uid, gid) } != 0 { return Err(std::io::Error::last_os_error().to_string()); }
                } else { crate::isolation::chown(&path); }
                f.write_all(value.as_bytes()).map_err(|e| e.to_string())?;
                env.insert(key.to_string(), path.display().to_string());
            } else {
                env.insert(key.to_string(), value);
            }
            vault_keys.insert(key.to_string());
        }
        // A company subscription the claim handed this turn: after the grants, so it decides how the runtime signs in.
        vault.subscriptions = crate::model_subscriptions::apply(ctx.state_dir, &granted["subscriptions"], &mut env,
                                                                 &mut vault.values, &mut vault_keys);
    }
    // 1Password references among the grants are read now (crate::op), with the token from `_shared.env`.
    let mut refs: BTreeMap<String, String> =
        vault_keys.iter().filter_map(|k| env.get(k).filter(|v| v.starts_with(crate::op::OP_REF)).map(|v| (k.clone(), v.clone()))).collect();
    if !refs.is_empty() {
        let shared = crate::signin::read_env(&ctx.projects_dir.join("secrets").join("_shared.env"));
        let token = shared.get(crate::op::TOKEN_KEY).filter(|t| !t.is_empty()).cloned().or_else(|| std::env::var(crate::op::TOKEN_KEY).ok()).unwrap_or_default();
        refs.insert(crate::op::TOKEN_KEY.into(), token);
        crate::op::resolve_op_refs(&mut refs).await;
        for k in &vault_keys {
            if k != crate::op::TOKEN_KEY {
                if let Some(v) = refs.get(k) {
                    env.insert(k.clone(), v.clone());
                }
            }
        }
        vault.values.extend(vault_keys.iter().filter_map(|k| env.get(k)).filter(|v| !v.is_empty()).cloned());
    }
    for key in SECRET_KEYS {
        if !vault_keys.contains(*key) {
            env.remove(*key);
        }
    }
    env.retain(|k, _| !(k.starts_with("TICO_") || k == "HUB_DB" || k == "HUB_HUMAN_OVERRIDE"));
    let root = root();
    let task_id = attempt.get("task").and_then(|t| t.get("id")).map(text_of).unwrap_or_default();
    let path = env.get("PATH").cloned().unwrap_or_else(|| "/usr/bin:/bin".into());
    let py_dir = python().parent().map(|p| p.display().to_string()).unwrap_or_default();
    let projects = ctx.projects_dir.display().to_string();
    for (k, v) in [
        ("HUB_API_URL", ctx.url.to_string()),
        ("HUB_TOKEN", token.to_string()),
        ("HUB_BOT", bot.to_string()),
        ("HUB_EMPLOYEE", bot.to_string()),
        ("HUB_TASK_ID", task_id),
        ("HUB_DIR", root.display().to_string()),
        ("HUB_WORKSPACE", projects.clone()),
        ("TICO_PROJECTS_DIR", projects),
        ("PATH", format!("{py_dir}:{}:{path}", root.join("scripts").display())),
    ] {
        env.insert(k.to_string(), v);
    }
    // Headed browsers and computer use draw on the computer's screen, where people can watch (crate::screen).
    if let Some(display) = crate::screen::display() {
        env.insert("DISPLAY".into(), display.into());
    }
    // A bot with a browser exit browses from that computer's internet: Chrome started by name goes through the proxy,
    // and nothing falls back to this computer's own address (crate::browser_exit). A proxy that can't start fails
    // the turn.
    if let Some(mut admission) = crate::browser_exit::turn_proxy(bot, attempt).await? {
        // Under isolation only this turn's own user may use the proxy: its leased user (Vault::for_model lets that in
        // once it is known), or the shared bot user a turn without a lease runs as.
        if let (Some((uid, _)), None) = (crate::isolation::identity(), &vault.turn_user) {
            admission.allow_always(uid);
        }
        let proxy = admission.url.clone();
        vault.browser_exit = Some(admission);
        let flags = format!("--proxy-server={proxy} --force-webrtc-ip-handling-policy=disable_non_proxied_udp");
        let chromium = match env.get("CHROMIUM_FLAGS").filter(|f| !f.is_empty()) {
            Some(f) => format!("{f} {flags}"),
            None => flags,
        };
        env.insert("CHROMIUM_FLAGS".into(), chromium);
        let path = env.get("PATH").cloned().unwrap_or_default();
        env.insert("PATH".into(), format!("{}:{path}", root.join("scripts").join("browser-exit").display()));
        env.insert("TICO_BROWSER_PROXY".into(), proxy);
    }
    // A bot's commits carry the bot's name, never the person whose computer runs it.
    let name = attempt["config"]["display_name"].as_str().filter(|n| !n.is_empty()).unwrap_or(bot).to_string();
    for role in ["AUTHOR", "COMMITTER"] {
        env.entry(format!("GIT_{role}_NAME")).or_insert_with(|| name.clone());
        env.entry(format!("GIT_{role}_EMAIL")).or_insert_with(|| format!("{bot}@bots.tico.invalid"));
    }
    Ok(env)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_gateway_hop_can_receive_the_relay_and_it_needs_a_lease() {
        let mut vault = Vault::default();
        let mut env: Env = [(crate::gateway_relay::KEY_ENV.into(), "synthetic-pass".into()),
                            (crate::gateway_relay::URL_ENV.into(), "http://127.0.0.1:9/gateway".into()),
                            (crate::gateway_relay::DIR_ENV.into(), "/synthetic/private".into())].into();
        assert!(vault.for_model(&mut env.clone(), Some("tico-mid-1.0"), true).is_err());
        assert_eq!(vault.for_model(&mut env, Some("opus-5-5"), true).unwrap(), None);
        for key in [crate::gateway_relay::KEY_ENV, crate::gateway_relay::URL_ENV, crate::gateway_relay::DIR_ENV] {
            assert!(!env.contains_key(key));
        }
    }

    #[test]
    fn reserved_names_are_the_python_list() {
        for k in ["HOME", "PATH", "HUB_TOKEN", "TICO_X", "DYLD_X", "LD_PRELOAD", "lower", "1ABC", "CODEX_HOME", "CLAUDE_CONFIG_DIR"] {
            assert!(reserved_credential(k), "{k}");
        }
        for k in ["GOOGLE_SA_KEY", "OPENROUTER_API_KEY", "_X", ""] {
            assert!(!reserved_credential(k), "{k}");
        }
    }

    #[tokio::test]
    async fn a_credential_granted_as_claude_config_dir_never_reaches_the_turn() {
        let dir = std::env::temp_dir().join(format!("tico-env-reserved-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let ctx = EnvContext { url: "http://hub.invalid", projects_dir: &dir, state_dir: &dir };
        let granted = serde_json::json!({"credentials": [
            {"id": "c1", "name": "Elsewhere", "env": "CLAUDE_CONFIG_DIR", "kind": "env", "value": "/elsewhere/.claude"},
            {"id": "c2", "name": "Acme", "env": "ACME_API_KEY", "kind": "env", "value": "acme-synthetic"},
        ]});
        let mut vault = Vault::default();
        let env = environment_with(&ctx, &serde_json::json!({"bot": "ops", "token": "t"}), &mut vault, Some(granted)).await.unwrap();
        assert!(!env.contains_key("CLAUDE_CONFIG_DIR"), "{env:?}");
        assert!(!vault.names.contains("CLAUDE_CONFIG_DIR"));
        assert_eq!(env["ACME_API_KEY"], "acme-synthetic");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
