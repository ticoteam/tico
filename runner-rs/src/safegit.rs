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

/// A Gateway turn's own git: the model's, run as its leased user in a checkout the bot user and the shared group
/// can write. Like the runner's git it runs no repository-named program (hooks, fsmonitor), and it reads no config
/// a bot can write, nor `safe.directory = *`: its global config is `global`, a file the supervisor writes, owned by
/// the leased user `owner` so its cleanup removes it with the rest of its files. It trusts exactly `paths`.
/// Proxy, CA and default branch come only from a system config root owns and no group can write (trusted_system);
/// the shared HOME's ~/.gitconfig and ~/.config/git/config are any sibling bot's to write, and a proxy or CA bundle
/// from there would see the turn's GitHub token. The proxy and CA variables the supervisor set reach git unchanged.
/// The identity is the turn's GIT_COMMITTER_*/GIT_AUTHOR_* (as committing_environment), else the system's, else Tico's.
pub fn for_turn_user(env: &mut Env, paths: &[PathBuf], global: &Path, owner: Option<(u32, u32)>) -> std::io::Result<()> {
    let mut entries: Vec<(String, String)> = vec![];
    if let Some(system) = trusted_system(env) {
        let hooks = format!("core.hooksPath={}", hooks().display());
        let out = std::process::Command::new("git")
            .args(["--no-optional-locks", "-c", &hooks, "-c", "core.fsmonitor=false", "config", "--file"]).arg(&system)
            .args(["--no-includes", "--null", "--get-regexp", r"^(user\.(name|email)|init\.defaultbranch|http\.(.*\.)?(proxy|sslcainfo))$"])
            .current_dir("/").env_clear().env("PATH", env.get("PATH").map(String::as_str).unwrap_or("/usr/bin:/bin"))
            .env("GIT_CONFIG_NOSYSTEM", "1").env("GIT_CONFIG_GLOBAL", "/dev/null")
            .stdin(std::process::Stdio::null()).stderr(std::process::Stdio::null()).output();
        if let Ok(out) = out {
            for entry in String::from_utf8_lossy(&out.stdout).split('\0') {
                let Some((key, value)) = entry.split_once('\n') else { continue };
                if !value.contains('\n') { entries.push((key.to_string(), value.to_string())); }
            }
        }
    }
    let identity = committing_environment(env);
    for (key, var) in [("user.name", "GIT_COMMITTER_NAME"), ("user.email", "GIT_COMMITTER_EMAIL")] {
        let author = var.replace("COMMITTER", "AUTHOR");
        let set = [var, author.as_str()].iter().any(|v| env.get(*v).is_some_and(|x| !x.is_empty()));
        if set || !entries.iter().any(|(k, _)| k == key) {
            entries.retain(|(k, _)| k != key);
            entries.push((key.into(), identity[var].clone()));
        }
    }
    let quote = |v: &str| v.replace('\\', "\\\\").replace('"', "\\\"");
    let mut lines = String::new();
    for (key, value) in &entries {
        let Some((section, name)) = key.rsplit_once('.') else { continue };
        let section = match section.split_once('.') {
            Some((head, sub)) => format!("{head} \"{}\"", quote(sub)),
            None => section.to_string(),
        };
        lines.push_str(&format!("[{section}]\n\t{name} = \"{}\"\n", quote(value)));
    }
    let _ = std::fs::remove_file(global);
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o644).custom_flags(libc::O_NOFOLLOW).open(global)?;
        file.write_all(lines.as_bytes())?;
        if let Some((uid, gid)) = owner {
            use std::os::fd::AsRawFd;
            if unsafe { libc::fchown(file.as_raw_fd(), uid, gid) } != 0 { return Err(std::io::Error::last_os_error()); }
        }
    }
    env.insert("GIT_CONFIG_GLOBAL".into(), global.display().to_string());
    env.insert("GIT_CONFIG_SYSTEM".into(), "/dev/null".into());
    env.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into());
    crate::gitcred::add_config(env, "core.hooksPath", "/dev/null");
    crate::gitcred::add_config(env, "core.fsmonitor", "false");
    let trusted = |key: &str| entries.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v.clone());
    neutralize_repositories(env, paths, trusted("http.proxy"), trusted("http.sslcainfo"))?;
    crate::gitcred::trust_directories(env, paths);
    Ok(())
}

/// A checkout's own config is the bot user's to write, and every bot runs as that user: a sibling can put a program,
/// a proxy or a CA there for the leased user's git to run or trust with the turn's tokens. Command-line entries
/// outrank it. Those here fix the keys whose names are fixed and that have a harmless value (ssh command, proxy,
/// signing, editor, pager, askpass, alternate-refs command, transports), so a later change cannot reach them either;
/// GIT_ASKPASS and SSH_ASKPASS, which come before core.askPass, are unset. The turn's GitHub helper follows an empty
/// `credential.helper`, which clears every helper the checkout names. The rest, and the keys whose names a repository
/// picks (a filter or diff driver, one URL's http settings), are neutralized as they stand when the turn starts. What
/// no later entry undoes refuses the turn (an include, or a `url.*.insteadOf`/`pushInsteadOf` that would rewrite a
/// GitHub URL) or the file transport (a remote's own upload-pack or receive-pack). Proxy and CA come from trusted
/// system config or the runner's own environment (the supervisor's), never the turn's.
///
/// Known residual, all from the checkout's config changing during the turn: a key whose name the repository picks
/// (a new filter, diff or merge driver or textconv, a new URL's http settings, a new `url.*.insteadOf`, a changed
/// include target) and `diff.external`, which has no harmless value (an empty one, or GIT_EXTERNAL_DIFF, makes
/// `git diff` fail). Only what is present when the turn starts is neutralized. Taking the checkout away from the bot
/// user for the turn would break that bot's other turns and the runner's own git, which write the same `.git` as the
/// bot user. The per-turn user for every bot turn (task 0918d271) closes it: the shared bot UID then no longer writes
/// the repo.
fn neutralize_repositories(env: &mut Env, paths: &[PathBuf], proxy: Option<String>, ca: Option<String>) -> std::io::Result<()> {
    use crate::gitcred::push_config;
    let runner = |name: &str| std::env::var(name).ok().filter(|v| !v.is_empty());
    // An empty http.proxy turns every proxy off, the turn environment's included.
    let proxy = proxy.or_else(|| runner("HTTPS_PROXY")).or_else(|| runner("https_proxy")).unwrap_or_default();
    let existing = |p: &str| Path::new(p).exists().then(|| p.to_string());
    // Git takes these over any http.<url>.sslCAInfo or sslCAPath, so they hold however the config changes.
    if let Some(file) = ca.or_else(|| runner("SSL_CERT_FILE")).or_else(|| existing("/etc/ssl/certs/ca-certificates.crt")).or_else(|| existing("/etc/ssl/cert.pem")) {
        env.insert("GIT_SSL_CAINFO".into(), file.clone());
        env.insert("GIT_PROXY_SSL_CAINFO".into(), file);
    }
    if let Some(dir) = runner("SSL_CERT_DIR").or_else(|| existing("/etc/ssl/certs")) { env.insert("GIT_SSL_CAPATH".into(), dir); }
    // Each would run before core.askPass; git prompts nothing in a turn (GIT_TERMINAL_PROMPT=0).
    for name in ["GIT_SSL_NO_VERIFY", "GIT_ASKPASS", "SSH_ASKPASS"] { env.remove(name); }
    for (key, value) in [
        ("core.sshCommand", "ssh -F /dev/null"), ("core.gitProxy", "none"), ("core.editor", ":"), ("sequence.editor", ":"),
        ("core.askPass", ""), ("core.alternateRefsCommand", "true"),
        ("core.pager", "cat"), ("commit.gpgSign", "false"), ("tag.gpgSign", "false"), ("tag.forceSignAnnotated", "false"),
        ("push.gpgSign", "false"), ("gpg.program", "gpg"), ("gpg.openpgp.program", "gpg"), ("gpg.x509.program", "gpgsm"),
        ("gpg.ssh.program", "ssh-keygen"), ("protocol.ext.allow", "never"), ("protocol.fd.allow", "never"),
        ("http.sslVerify", "true"), ("http.extraHeader", ""), ("http.cookieFile", ""), ("http.curloptResolve", ""), ("http.proxy", &proxy),
    ] {
        push_config(env, key, value);
    }
    helpers_after_a_reset(env);
    let hooks = format!("core.hooksPath={}", hooks().display());
    let mut file_transport = false;
    for path in paths.iter().filter(|p| p.is_dir()) {
        // Read as the supervisor, which runs no program for `config --list`; only this listing trusts `*`.
        let out = std::process::Command::new("git")
            .args(["--no-optional-locks", "-c", &hooks, "-c", "core.fsmonitor=false", "-c", "safe.directory=*", "-C"]).arg(path)
            .args(["config", "--list", "--null"])
            .env_clear().env("PATH", env.get("PATH").map(String::as_str).unwrap_or("/usr/bin:/bin"))
            .env("GIT_CONFIG_NOSYSTEM", "1").env("GIT_CONFIG_GLOBAL", "/dev/null")
            .stdin(std::process::Stdio::null()).stderr(std::process::Stdio::null()).output()?;
        for entry in String::from_utf8_lossy(&out.stdout).split('\0').filter(|k| !k.is_empty()) {
            let (key, value) = entry.split_once('\n').unwrap_or((entry, ""));
            let Some((section, rest)) = key.split_once('.') else { continue };
            let (sub, name) = match rest.rsplit_once('.') { Some((sub, name)) => (Some(sub), name), None => (None, rest) };
            let (section, name) = (section.to_ascii_lowercase(), name.to_ascii_lowercase());
            let neutral = match (section.as_str(), sub, name.as_str()) {
                ("include", None, "path") | ("includeif", Some(_), "path") => {
                    return Err(std::io::Error::other(format!("{} includes other config, which a Tico model turn cannot read safely", path.display())));
                }
                ("remote", Some(_), "uploadpack" | "receivepack") => { file_transport = true; continue; }
                // The longest matching prefix wins, so no later rule can take a GitHub URL back.
                ("url", Some(_), "insteadof" | "pushinsteadof") if rewrites_github(value) => {
                    return Err(std::io::Error::other(format!("{} rewrites GitHub URLs ({key} = {value}), which a Tico model turn cannot allow", path.display())));
                }
                ("filter", Some(driver), "clean" | "smudge" | "process") => {
                    push_config(env, &format!("filter.{driver}.required"), "false");
                    ""
                }
                // An empty one of these is a command git then fails to run: the turn's git fails there instead.
                ("diff", Some(_), "command" | "textconv") | ("merge", Some(_), "driver") | ("tar", Some(_), "command") => "",
                ("diff", None, "external") => "", // core.askPass and core.alternateRefsCommand are always pinned
                ("gpg", Some(_), "defaultkeycommand") => "",
                ("pager", _, _) | ("alias", _, _) => "",
                ("http", Some(_), "proxy") | ("remote", Some(_), "proxy") => &proxy,
                ("http", Some(_), "sslverify") => "true",
                ("http", Some(_), "extraheader" | "cookiefile" | "curloptresolve") => "",
                _ => continue,
            };
            push_config(env, key, neutral);
        }
    }
    if file_transport { push_config(env, "protocol.file.allow", "never"); }
    Ok(())
}

/// Whether a `url.<base>.insteadOf` prefix would match a URL on the GitHub host (TICO_GITHUB_HOST), in any form.
fn rewrites_github(prefix: &str) -> bool {
    let host = std::env::var("TICO_GITHUB_HOST").ok().filter(|h| !h.is_empty()).unwrap_or_else(|| "github.com".into()).to_ascii_lowercase();
    let prefix = prefix.trim().to_ascii_lowercase();
    !prefix.is_empty() && [format!("https://{host}/"), format!("http://{host}/"), format!("ssh://git@{host}/"), format!("git@{host}:"), format!("git://{host}/"), format!("{host}:")]
        .iter().any(|url| url.starts_with(&prefix) || prefix.starts_with(url.as_str()))
}

/// Git reads helpers as one list across `credential.helper` and every `credential.<url>.helper`, and an empty one
/// clears what came before, the checkout's included. A URL-specific empty one (gitcred's) clears them only for that
/// URL, so the checkout's helpers would still run for every other host. The turn's own helpers must follow an empty
/// generic entry, and nothing may clear them afterwards: put it before the first command-line helper, or at the end
/// when there is none.
fn helpers_after_a_reset(env: &mut Env) {
    let count: usize = env.get("GIT_CONFIG_COUNT").and_then(|c| c.parse().ok()).unwrap_or(0);
    let mut entries: Vec<(String, String)> = (0..count)
        .map(|i| (env.get(&format!("GIT_CONFIG_KEY_{i}")).cloned().unwrap_or_default(), env.get(&format!("GIT_CONFIG_VALUE_{i}")).cloned().unwrap_or_default()))
        .collect();
    let helper = |key: &str| { let k = key.to_ascii_lowercase(); k.starts_with("credential.") && k.ends_with(".helper") };
    match entries.iter().position(|(k, _)| helper(k)) {
        Some(first) if entries[first].0.eq_ignore_ascii_case("credential.helper") && entries[first].1.is_empty() => return,
        Some(first) => entries.insert(first, ("credential.helper".into(), String::new())),
        None => entries.push(("credential.helper".into(), String::new())),
    }
    for (i, (key, value)) in entries.iter().enumerate() {
        env.insert(format!("GIT_CONFIG_KEY_{i}"), key.clone());
        env.insert(format!("GIT_CONFIG_VALUE_{i}"), value.clone());
    }
    env.insert("GIT_CONFIG_COUNT".into(), entries.len().to_string());
}

/// The system git config, if no bot can have written it: a regular file, not a link, that root owns and no group
/// or other user can write, in a directory likewise. Git's own `$GIT_CONFIG_SYSTEM`, else the image's /etc/gitconfig.
fn trusted_system(env: &Env) -> Option<PathBuf> {
    if env.get("GIT_CONFIG_NOSYSTEM").is_some_and(|v| !v.is_empty() && v != "0" && v != "false") { return None; }
    let path = PathBuf::from(env.get("GIT_CONFIG_SYSTEM").filter(|v| !v.is_empty()).map(String::as_str).unwrap_or("/etc/gitconfig"));
    use std::os::unix::fs::MetadataExt;
    let safe = |m: &std::fs::Metadata| m.uid() == 0 && m.mode() & 0o022 == 0;
    let file = std::fs::symlink_metadata(&path).ok()?;
    let parent = std::fs::symlink_metadata(path.parent()?).ok()?;
    (file.is_file() && safe(&file) && parent.is_dir() && safe(&parent)).then_some(path)
}

const IDENTITY: [&str; 4] = ["GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"];

/// `environment()` for a rebase, merge or commit the runner makes (safe_git.committing_environment): the bot's
/// identity when the turn set one, else Tico's. A clean environment has none, and git then refuses ("unable to
/// auto-detect email address"), leaving the bot's unpushed commits stuck behind its remote.
pub fn committing_environment(turn: &Env) -> Env {
    let mut env = environment(turn);
    for key in IDENTITY {
        if let Some(v) = turn.get(key).filter(|v| !v.is_empty()) {
            env.insert(key.into(), v.clone());
        }
    }
    let pick = |env: &Env, key: &str, other: &str, fallback: &str| {
        env.get(key).filter(|v| !v.is_empty()).or_else(|| env.get(other).filter(|v| !v.is_empty())).cloned().unwrap_or_else(|| fallback.into())
    };
    let name = pick(&env, "GIT_COMMITTER_NAME", "GIT_AUTHOR_NAME", "Tico");
    let email = pick(&env, "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_EMAIL", "runner@bots.tico.invalid");
    env.entry("GIT_COMMITTER_NAME".into()).or_insert_with(|| name.clone());
    env.entry("GIT_COMMITTER_EMAIL".into()).or_insert_with(|| email.clone());
    env.entry("GIT_AUTHOR_NAME".into()).or_insert(name);
    env.entry("GIT_AUTHOR_EMAIL".into()).or_insert(email);
    env
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn a_turn_users_git_runs_no_repository_program_and_reads_no_wider_trust() {
        let dir = std::env::temp_dir().join(format!("tico-turn-git-{}", uuid::Uuid::new_v4().simple()));
        let (repo, home) = (dir.join("repo"), dir.join("home"));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(home.join(".gitconfig"), "[safe]\n\tdirectory = *\n[user]\n\tname = Fixture\n\temail = fixture@example.invalid\n").unwrap();
        let mut env = Env::new();
        env.insert("HOME".into(), home.display().to_string());
        env.insert("PATH".into(), std::env::var("PATH").unwrap_or_default());
        let git = |env: &Env, args: &[&str]| std::process::Command::new("git").args(args).env_clear().envs(env).output().unwrap();
        assert!(git(&env, &["init", "-q", &repo.display().to_string()]).status.success());
        let mark = dir.join("ran");
        let hook = repo.join(".git/hooks/pre-commit");
        std::fs::write(&hook, format!("#!/bin/sh\ntouch '{}'\n", mark.display())).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&hook, std::fs::Permissions::from_mode(0o755)).unwrap();
        let global = dir.join("turn.gitconfig");
        for_turn_user(&mut env, &[repo.clone()], &global, None).unwrap();
        let written = std::fs::read_to_string(&global).unwrap();
        assert!(!written.contains("Fixture") && !written.contains("safe"), "{written}");
        assert_eq!((env["GIT_CONFIG_NOSYSTEM"].as_str(), env["GIT_CONFIG_GLOBAL"].as_str()), ("1", global.to_str().unwrap()));
        let listed = git(&env, &["-C", &repo.display().to_string(), "config", "--get-all", "safe.directory"]);
        assert_eq!(String::from_utf8_lossy(&listed.stdout).trim(), repo.display().to_string());
        assert!(git(&env, &["-C", &repo.display().to_string(), "commit", "-q", "--allow-empty", "-m", "turn"]).status.success());
        std::fs::write(repo.join("f.txt"), "one\n").unwrap();
        assert!(git(&env, &["-C", &repo.display().to_string(), "add", "f.txt"]).status.success());
        std::fs::write(repo.join("f.txt"), "two\n").unwrap();
        let diff = git(&env, &["-C", &repo.display().to_string(), "diff"]);
        assert!(diff.status.success() && String::from_utf8_lossy(&diff.stdout).contains("+two"), "the turn's overrides break plain git diff");
        assert!(!mark.exists(), "the repository's hook ran");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_sibling_writable_gitconfig_gives_the_turn_user_no_proxy_or_ca() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("tico-turn-proxy-{}", uuid::Uuid::new_v4().simple()));
        let home = dir.join("home");
        std::fs::create_dir_all(home.join(".config/git")).unwrap();
        // Any bot can write the shared HOME (1770), so these are a sibling's: a proxy it runs and a CA bundle it made.
        let hostile = "[http]\n\tproxy = http://127.0.0.1:9\n\tsslCAInfo = /home/runner/sibling-ca.pem\n[http \"https://github.com/\"]\n\tproxy = http://127.0.0.1:9\n[user]\n\tname = Sibling\n";
        let system = dir.join("gitconfig");
        for file in [home.join(".gitconfig"), home.join(".config/git/config"), system.clone()] {
            std::fs::write(&file, hostile).unwrap();
            std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o664)).unwrap();
        }
        let mut env = Env::new();
        env.insert("HOME".into(), home.display().to_string());
        env.insert("XDG_CONFIG_HOME".into(), home.join(".config").display().to_string());
        env.insert("GIT_CONFIG_SYSTEM".into(), system.display().to_string()); // not root's, so not trusted either
        env.insert("PATH".into(), std::env::var("PATH").unwrap_or_default());
        env.insert("GIT_AUTHOR_NAME".into(), "Ops Bot".into());
        env.insert("GIT_AUTHOR_EMAIL".into(), "ops@bots.invalid".into());
        let global = dir.join("turn.gitconfig");
        for_turn_user(&mut env, &[dir.join("repo")], &global, None).unwrap();
        let written = std::fs::read_to_string(&global).unwrap();
        assert!(!written.to_lowercase().contains("proxy") && !written.to_lowercase().contains("sslcainfo") && !written.contains("Sibling"), "{written}");
        assert!(written.contains("Ops Bot") && written.contains("ops@bots.invalid"), "{written}");
        let listed = std::process::Command::new("git").args(["config", "--get-regexp", "^http\\."]).current_dir(&dir).env_clear().envs(&env).output().unwrap();
        let listed = String::from_utf8_lossy(&listed.stdout);
        assert!(!listed.contains("127.0.0.1") && !listed.contains("sibling-ca"), "the turn user's git sees a sibling's http settings: {listed}");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_siblings_repository_config_runs_nothing_and_routes_nothing_for_the_turn_user() {
        let dir = std::env::temp_dir().join(format!("tico-turn-repo-config-{}", uuid::Uuid::new_v4().simple()));
        let (repo, remote) = (dir.join("repo"), dir.join("remote.git"));
        std::fs::create_dir_all(&dir).unwrap();
        let mut env = Env::new();
        env.insert("PATH".into(), std::env::var("PATH").unwrap_or_default());
        env.insert("HOME".into(), dir.display().to_string());
        let git = |env: &Env, args: &[&str]| std::process::Command::new("git").arg("-C").arg(&repo).args(args).env_clear().envs(env)
            .stdin(std::process::Stdio::null()).output().unwrap();
        let plain = |args: &[&str]| { let mut e = env.clone(); e.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into()); e.insert("GIT_CONFIG_GLOBAL".into(), "/dev/null".into()); git(&e, args) };
        assert!(std::process::Command::new("git").args(["init", "-q"]).arg(&repo).output().unwrap().status.success());
        assert!(std::process::Command::new("git").args(["init", "-q", "--bare"]).arg(&remote).output().unwrap().status.success());
        std::fs::write(repo.join("f.txt"), "one\n").unwrap();
        std::fs::write(repo.join(".gitattributes"), "* filter=evil diff=evil\n").unwrap();
        assert!(plain(&["-c", "user.name=t", "-c", "user.email=t@t", "add", "."]).status.success());
        assert!(plain(&["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"]).status.success());
        // What a sibling bot, which runs as the checkout's owner, plants in the checkout's own config before the turn.
        let mark = |what: &str| dir.join(format!("ran-{what}"));
        let touch = |what: &str| format!("touch '{}'", mark(what).display());
        let planted = format!(
            "[filter \"evil\"]\n\tclean = {}\n\tsmudge = {}\n\trequired = true\n[diff \"evil\"]\n\ttextconv = {}\n\tcommand = {}\n\
             [commit]\n\tgpgSign = true\n[gpg]\n\tprogram = {}\n[credential \"https://github.com\"]\n\thelper = !{}\n\
             [http]\n\tproxy = http://127.0.0.1:9\n\tsslVerify = false\n[http \"https://github.com/\"]\n\tproxy = http://127.0.0.1:9\n\tsslVerify = false\n\
             [remote \"origin\"]\n\turl = {}\n\tproxy = http://127.0.0.1:9\n[alias]\n\tst = !{}\n",
            touch("clean"), touch("smudge"), touch("textconv"), touch("diff"), touch("gpg"), touch("credential"), remote.display(), touch("alias"));
        std::fs::OpenOptions::new().append(true).open(repo.join(".git/config")).unwrap().write_all(planted.as_bytes()).unwrap();
        let global = dir.join("turn.gitconfig");
        let mut turn = env.clone();
        for_turn_user(&mut turn, &[repo.clone()], &global, None).unwrap();
        // ... and changes during the turn: a key with a fixed name stays overridden.
        std::fs::OpenOptions::new().append(true).open(repo.join(".git/config")).unwrap()
            .write_all(format!("[core]\n\tsshCommand = {}\n\tpager = {}\n", touch("ssh"), touch("pager")).as_bytes()).unwrap();
        std::fs::write(repo.join("f.txt"), "two\n").unwrap();
        for args in [&["add", "f.txt"][..], &["diff", "HEAD"], &["log", "-p", "-1"], &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "two"],
                     &["ls-remote", "ssh://example.invalid/x"], &["st"], &["push", "-q", "origin", "HEAD:refs/heads/main"]] {
            let _ = git(&turn, args);
        }
        let _ = std::fs::remove_file(repo.join("f.txt"));
        let _ = git(&turn, &["checkout-index", "-f", "f.txt"]);
        let filled = std::process::Command::new("git").arg("-C").arg(&repo).args(["credential", "fill"]).env_clear().envs(&turn)
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).spawn().unwrap();
        { let mut filled = filled; filled.stdin.take().unwrap().write_all(b"protocol=https\nhost=github.com\npath=o/r.git\n\n").unwrap(); let _ = filled.wait(); }
        for what in ["clean", "smudge", "textconv", "diff", "gpg", "credential", "ssh", "pager", "alias"] {
            assert!(!mark(what).exists(), "the checkout's config ran its {what} program as the turn user");
        }
        let get = |args: &[&str]| String::from_utf8_lossy(&git(&turn, args).stdout).trim().to_string();
        for url in ["https://github.com/o/r.git/info/refs", "https://example.invalid/x"] {
            assert_eq!(get(&["config", "--get-urlmatch", "http.proxy", url]), "", "{url}");
            assert_eq!(get(&["config", "--type=bool", "--get-urlmatch", "http.sslverify", url]), "true", "{url}");
        }
        assert_eq!(get(&["config", "--get", "remote.origin.proxy"]), "");
        assert_eq!(get(&["config", "--get", "core.sshcommand"]), "ssh -F /dev/null");
        // Neither an include nor a remote's own upload-pack can be overridden afterwards.
        let mut uploads = env.clone();
        plain(&["config", "remote.origin.uploadpack", &touch("upload")]);
        for_turn_user(&mut uploads, &[repo.clone()], &dir.join("uploads.gitconfig"), None).unwrap();
        let _ = git(&uploads, &["fetch", "-q", "origin"]);
        assert!(!mark("upload").exists(), "a remote's upload-pack ran as the turn user");
        plain(&["config", "include.path", &dir.join("sibling.gitconfig").display().to_string()]);
        let refused = for_turn_user(&mut env.clone(), &[repo.clone()], &dir.join("included.gitconfig"), None).unwrap_err();
        assert!(refused.to_string().contains("includes other config"), "{refused}");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_turn_users_github_helper_answers_after_the_checkouts_are_cleared() {
        let dir = std::env::temp_dir().join(format!("tico-turn-helper-{}", uuid::Uuid::new_v4().simple()));
        let repo = dir.join("repo");
        assert!(std::process::Command::new("git").args(["init", "-q"]).arg(&repo).output().unwrap().status.success());
        // Each planted helper leaves a mark: git swallows a helper's output, so only a mark shows that it ran.
        let mark = |what: &str| dir.join(format!("ran-{what}"));
        let planted = format!("[credential]\n\thelper = !touch '{}'\n[credential \"https://github.com\"]\n\thelper = !touch '{}'\n[credential \"https://example.invalid\"]\n\thelper = !touch '{}'\n\
                       [core]\n\taskPass = /bin/false\n\talternateRefsCommand = /bin/false\n[url \"https://mirror.invalid/\"]\n\tinsteadOf = https://gitlab.invalid/\n",
            mark("generic").display(), mark("github").display(), mark("other").display());
        std::fs::OpenOptions::new().append(true).open(repo.join(".git/config")).unwrap().write_all(planted.as_bytes()).unwrap();
        let mut env = Env::new();
        env.insert("PATH".into(), std::env::var("PATH").unwrap_or_default());
        env.insert("HOME".into(), dir.display().to_string());
        env.insert("GIT_ASKPASS".into(), "/bin/false".into());
        env.insert("SSH_ASKPASS".into(), "/bin/false".into());
        // The order execute.rs uses: the turn's GitHub credentials, then the leased user's git (twice on a fallback hop).
        crate::gitcred::git_environment(&mut env, "ghs_turn",
            "!f() { test \"$1\" = get && printf 'username=x-access-token\\npassword=%s\\n' \"$GH_TOKEN\"; }; f");
        for_turn_user(&mut env, &[repo.clone()], &dir.join("turn.gitconfig"), None).unwrap();
        for_turn_user(&mut env, &[repo.clone()], &dir.join("hop.gitconfig"), None).unwrap();
        assert!(!env.contains_key("GIT_ASKPASS") && !env.contains_key("SSH_ASKPASS"));
        let fill = |input: &str| {
            let mut child = std::process::Command::new("git").arg("-C").arg(&repo).args(["credential", "fill"]).env_clear().envs(&env)
                .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).spawn().unwrap();
            child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
            let out = child.wait_with_output().unwrap();
            format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr))
        };
        let github = fill("protocol=https\nhost=github.com\npath=o/r.git\n\n");
        assert!(github.contains("password=ghs_turn"), "{github}");
        for input in ["protocol=https\nhost=example.invalid\n\n", "protocol=https\nhost=mirror.invalid\npath=x\n\n", "protocol=http\nhost=github.com\n\n"] {
            let other = fill(input);
            assert!(!other.contains("ghs_turn"), "{other}");
        }
        for what in ["generic", "github", "other"] {
            assert!(!mark(what).exists(), "the checkout's {what} credential helper ran as the turn user");
        }
        // Control: without the turn's settings the same fill runs the checkout's helpers.
        let control = std::process::Command::new("git").arg("-C").arg(&repo).args(["credential", "fill"])
            .env_clear().env("PATH", std::env::var("PATH").unwrap_or_default()).env("HOME", &dir).env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_CONFIG_NOSYSTEM", "1").stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().unwrap();
        { let mut control = control; control.stdin.take().unwrap().write_all(b"protocol=https\nhost=example.invalid\n\n").unwrap(); let _ = control.wait(); }
        assert!(mark("generic").exists() && mark("other").exists(), "control: the planted helpers never run");
        let get = |key: &str| String::from_utf8_lossy(&std::process::Command::new("git").arg("-C").arg(&repo).args(["config", "--get", key])
            .env_clear().envs(&env).output().unwrap().stdout).trim().to_string();
        assert_eq!((get("core.askpass").as_str(), get("core.alternaterefscommand").as_str()), ("", "true"));
        // A rewrite of GitHub URLs could send the turn's token elsewhere, and none can be overridden: refuse the turn.
        for (key, prefix) in [("insteadOf", "https://github.com/"), ("insteadOf", "https://github.com/o/"), ("pushInsteadOf", "https://git"),
                              ("insteadOf", "git@github.com:"), ("insteadOf", "HTTPS://GitHub.com")] {
            let config = repo.join(".git/config");
            let before = std::fs::read(&config).unwrap();
            std::fs::OpenOptions::new().append(true).open(&config).unwrap()
                .write_all(format!("[url \"https://evil.invalid/\"]\n\t{key} = {prefix}\n").as_bytes()).unwrap();
            let refused = for_turn_user(&mut env.clone(), &[repo.clone()], &dir.join("rewrite.gitconfig"), None).unwrap_err();
            assert!(refused.to_string().contains("rewrites GitHub URLs"), "{key} = {prefix}: {refused}");
            std::fs::write(&config, before).unwrap();
        }
        assert!(!rewrites_github("https://github.company.invalid/") && !rewrites_github("gh:"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn runner_commits_carry_the_bots_identity_else_ticos() {
        let mut turn = Env::new();
        let bare = committing_environment(&turn);
        assert_eq!((bare["GIT_AUTHOR_NAME"].as_str(), bare["GIT_COMMITTER_EMAIL"].as_str()), ("Tico", "runner@bots.tico.invalid"));
        turn.insert("GIT_AUTHOR_NAME".into(), "Coo".into());
        turn.insert("GIT_AUTHOR_EMAIL".into(), "coo@bots.tico.invalid".into());
        let bot = committing_environment(&turn);
        assert_eq!((bot["GIT_COMMITTER_NAME"].as_str(), bot["GIT_COMMITTER_EMAIL"].as_str()), ("Coo", "coo@bots.tico.invalid"));
    }

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
