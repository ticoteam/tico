//! Git the runner itself runs in a bot's checkout (`runner/safe_git.py`): no supervisor secrets, no repository
//! hooks or fsmonitor program, no global or system config beyond the machine's credential and proxy settings.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use crate::env::Env;

/// An empty, read-only hooks directory, made once per process.
fn hooks() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let dir = std::env::temp_dir().join(format!("tico-empty-hooks-{}-{}", std::process::id(), uuid::Uuid::new_v4().simple()));
        let _ = std::fs::create_dir_all(&dir);
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o555));
        dir
    })
}

pub fn hooks_dir() -> &'static Path {
    hooks()
}

/// What every runner git command starts with (safe_git.PREFIX), for git outside a checkout.
pub fn prefix() -> Vec<String> {
    vec!["--no-optional-locks".into(), "-c".into(), format!("core.hooksPath={}", hooks().display()), "-c".into(), "core.fsmonitor=false".into()]
}

/// `git` with what every runner git command starts with (safe_git.PREFIX plus `safe.directory`), then `-C path`.
pub fn args(path: &Path) -> Vec<String> {
    vec![
        "--no-optional-locks".into(),
        "-c".into(),
        format!("core.hooksPath={}", hooks().display()),
        "-c".into(),
        "core.fsmonitor=false".into(),
        "-c".into(),
        format!("safe.directory={}", path.display()),
        "-C".into(),
        path.display().to_string(),
    ]
}

/// Process settings only, with Git's global and system config emptied (safe_git.clean_environment).
pub fn clean_environment(source: &Env) -> Env {
    let mut env: Env = source.iter().filter(|(k, _)| crate::env::SETTINGS.contains(&k.as_str()) || k.starts_with("LC_")).map(|(k, v)| (k.clone(), v.clone())).collect();
    env.insert("GIT_CONFIG_GLOBAL".into(), "/dev/null".into());
    env.insert("GIT_CONFIG_SYSTEM".into(), "/dev/null".into());
    env.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into());
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    env.insert("GIT_OPTIONAL_LOCKS".into(), "0".into());
    env
}

/// The clean environment plus the computer's own credential, URL-rewrite, SSH, proxy and identity settings from
/// system and global config, read without includes and outside every checkout (safe_git.machine_environment).
pub fn machine_environment(source: &Env) -> Env {
    let mut env = clean_environment(source);
    let mut trusted: Env = source.iter().filter(|(k, _)| crate::env::SETTINGS.contains(&k.as_str()) || k.starts_with("LC_")).map(|(k, v)| (k.clone(), v.clone())).collect();
    for key in ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"] {
        if let Some(v) = source.get(key) {
            trusted.insert(key.into(), v.clone());
        }
    }
    // The turn's `gh` shim is this binary: not the machine's GitHub sign-in, so out of PATH here.
    let me = std::env::current_exe().ok().and_then(|p| p.canonicalize().ok());
    let shim = |dir: &Path| me.is_some() && dir.join("gh").canonicalize().ok() == me;
    let dirs: Vec<PathBuf> = std::env::split_paths(&env.get("PATH").cloned().unwrap_or_default()).filter(|d| !shim(d)).collect();
    if let Ok(path) = std::env::join_paths(&dirs) {
        env.insert("PATH".into(), path.to_string_lossy().into_owned());
        trusted.insert("PATH".into(), path.to_string_lossy().into_owned());
    }
    let pattern = r"^(credential\..*|url\..*\.(insteadof|pushinsteadof)|core\.sshcommand|http\.(.*\.)?(proxy|sslcainfo)|user\.(name|email))$";
    let mut entries: Vec<(String, String)> = vec![];
    for scope in ["--system", "--global"] {
        let hooks = format!("core.hooksPath={}", hooks().display());
        let out = std::process::Command::new("git")
            .args(["--no-optional-locks", "-c", &hooks, "-c", "core.fsmonitor=false", "config", scope, "--no-includes", "--null", "--get-regexp", pattern])
            .current_dir("/")
            .env_clear()
            .envs(&trusted)
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output();
        let Ok(out) = out else { continue };
        for entry in String::from_utf8_lossy(&out.stdout).split('\0') {
            if let Some((k, v)) = entry.split_once('\n') {
                entries.push((k.to_string(), v.to_string()));
            }
        }
    }
    let gh = dirs.iter().map(|d| d.join("gh")).find(|p| p.is_file());
    if let Some(gh) = gh {
        if !entries.iter().any(|(k, _)| k.eq_ignore_ascii_case("credential.https://github.com.helper")) {
            entries.push(("credential.https://github.com.helper".into(), format!("!{} auth git-credential", crate::gitcred::shell_quote(&gh.display().to_string()))));
        }
    }
    entries.insert(0, ("credential.helper".into(), String::new()));
    env.insert("GIT_CONFIG_COUNT".into(), entries.len().to_string());
    for (i, (k, v)) in entries.iter().enumerate() {
        env.insert(format!("GIT_CONFIG_KEY_{i}"), k.clone());
        env.insert(format!("GIT_CONFIG_VALUE_{i}"), v.clone());
    }
    let ssh = entries.iter().rev().find(|(k, _)| k.eq_ignore_ascii_case("core.sshcommand")).map(|(_, v)| v.clone()).unwrap_or_else(|| "ssh".into());
    env.insert("GIT_SSH_COMMAND".into(), ssh);
    env
}

/// The environment for runner git in a turn's checkout (safe_git.environment): a clean environment, then only
/// the turn's per-repository GitHub helper settings (its scoped GH_TOKEN, the helper, the socket), never its
/// vault grants or the supervisor's own secrets. A turn without helper settings gets the machine's.
pub fn environment(turn: &Env) -> Env {
    let mut env = if turn.contains_key("GIT_CONFIG_COUNT") { clean_environment(turn) } else { machine_environment(turn) };
    for (k, v) in turn {
        let keep = ["GH_TOKEN", "GITHUB_TOKEN", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND", "HUB_TOKEN", crate::gitcred::SOCKET_ENV].contains(&k.as_str())
            || k.starts_with("TICO_GITHUB_")
            || k.starts_with("GIT_CONFIG_KEY_")
            || k.starts_with("GIT_CONFIG_VALUE_");
        if keep {
            env.insert(k.clone(), v.clone());
        }
    }
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    env.insert("GIT_OPTIONAL_LOCKS".into(), "0".into());
    env
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runner_git_keeps_the_turns_helper_and_drops_everything_else() {
        let mut turn = Env::new();
        for (k, v) in [
            ("PATH", "/usr/bin:/bin"),
            ("HOME", "/home/bot"),
            ("GH_TOKEN", "ghs_scoped"),
            ("GIT_CONFIG_COUNT", "1"),
            ("GIT_CONFIG_KEY_0", "credential.helper"),
            ("GIT_CONFIG_VALUE_0", "x"),
            ("OP_SERVICE_ACCOUNT_TOKEN", "machine-secret"),
            ("ACME_API_KEY", "granted"),
        ] {
            turn.insert(k.into(), v.into());
        }
        let env = environment(&turn);
        assert_eq!(env.get("GH_TOKEN").map(String::as_str), Some("ghs_scoped"));
        assert_eq!(env.get("GIT_CONFIG_GLOBAL").map(String::as_str), Some("/dev/null"));
        assert!(!env.contains_key("OP_SERVICE_ACCOUNT_TOKEN") && !env.contains_key("ACME_API_KEY"));
        let argv = args(Path::new("/x"));
        assert!(argv.contains(&"core.fsmonitor=false".to_string()) && argv.iter().any(|a| a.starts_with("core.hooksPath=")));
    }
}
