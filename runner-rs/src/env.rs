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

/// The process settings a turn and the runner's own git keep (`runner/safe_git.py` _SETTINGS), plus `LC_*`.
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
/// Keys a turn never inherits unless a grant brings them (`runner/op.py` SECRET_KEYS).
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
    /// The turn's relay to the Tico AI Gateway (gateway_relay.rs), stopped with the vault.
    pub relay: Option<crate::gateway_relay::Relay>,
}

impl Vault {
    pub fn remove_files(&mut self) {
        for f in self.files.drain(..) {
            let _ = std::fs::remove_file(f);
        }
    }
}

impl Drop for Vault {
    fn drop(&mut self) {
        self.remove_files();
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
                let dir = if crate::isolation::enabled() { crate::isolation::turn_dir() } else { ctx.state_dir.to_path_buf() };
                let path = dir.join(format!("tico-credential-{}", uuid::Uuid::new_v4().simple()));
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&path)
                    .map_err(|e| format!("cannot write a credential file: {e}"))?;
                vault.files.push(path.clone());
                crate::isolation::chown(&path);
                f.write_all(value.as_bytes()).map_err(|e| e.to_string())?;
                env.insert(key.to_string(), path.display().to_string());
            } else {
                env.insert(key.to_string(), value);
            }
            vault_keys.insert(key.to_string());
        }
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
