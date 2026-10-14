//! Bot code as another user (runner/isolation.py): on the Docker runner the supervisor keeps runner.json and
//! its state to itself and starts every process that runs bot-controlled code (the model CLI, git in a bot's
//! checkout, a watcher, any runtime probe) as `TICO_RUNNER_BOT_UID` with no capabilities, through `setpriv`.
//! Off wherever that variable is unset (a Mac is a trust group of one user).

use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const UID_ENV: &str = "TICO_RUNNER_BOT_UID";
pub const GID_ENV: &str = "TICO_RUNNER_BOT_GID";
pub const SETPRIV: &str = "/usr/bin/setpriv";

/// (uid, gid) bot code runs as, or None when this process is not separating them.
pub fn identity() -> Option<(u32, u32)> {
    // SAFETY: geteuid has no preconditions.
    identity_from(std::env::var(UID_ENV).ok(), std::env::var(GID_ENV).ok(), unsafe { libc::geteuid() })
}

fn identity_from(uid: Option<String>, gid: Option<String>, euid: u32) -> Option<(u32, u32)> {
    let uid: i64 = uid?.trim().parse().ok()?;
    let gid: i64 = match gid {
        Some(g) if !g.is_empty() => g.trim().parse().ok()?,
        _ => uid,
    };
    (uid > 0 && uid != euid as i64 && gid >= 0 && uid <= u32::MAX as i64 && gid <= u32::MAX as i64).then_some((uid as u32, gid as u32))
}

pub fn enabled() -> bool {
    identity().is_some()
}

/// The `setpriv` arguments that switch to the bot user and empty every capability set.
pub fn prefix() -> Option<Vec<String>> {
    identity().map(prefix_for)
}

fn prefix_for((uid, gid): (u32, u32)) -> Vec<String> {
    vec![
        SETPRIV.into(),
        format!("--reuid={uid}"),
        format!("--regid={gid}"),
        "--clear-groups".into(),
        "--inh-caps=-all".into(),
        "--ambient-caps=-all".into(),
    ]
}

fn umask_group() -> std::io::Result<()> {
    // SAFETY: umask is async-signal-safe and only changes this (forked) process's mask.
    unsafe { libc::umask(0o002) };
    Ok(())
}

/// A command that runs `program` as the bot user when isolation is on (with umask 002: the bot's files are
/// group-writable), else as itself. Arguments added later follow the program.
pub fn command(program: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    match prefix() {
        None => tokio::process::Command::new(program),
        Some(p) => {
            let mut cmd = tokio::process::Command::new(&p[0]);
            cmd.args(&p[1..]).arg(program);
            // SAFETY: the closure only calls umask.
            unsafe { cmd.pre_exec(umask_group) };
            cmd
        }
    }
}

/// `command` for std's blocking Command.
pub fn std_command(program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    use std::os::unix::process::CommandExt;
    match prefix() {
        None => std::process::Command::new(program),
        Some(p) => {
            let mut cmd = std::process::Command::new(&p[0]);
            cmd.args(&p[1..]).arg(program);
            // SAFETY: the closure only calls umask.
            unsafe { cmd.pre_exec(umask_group) };
            cmd
        }
    }
}

fn lchown(path: &Path, uid: u32, gid: u32) -> std::io::Result<()> {
    std::os::unix::fs::lchown(path, Some(uid), Some(gid))
}

/// Hand a file the supervisor made to the bot user. A no-op when isolation is off.
pub fn chown(path: &Path) {
    if let Some((uid, gid)) = identity() {
        let _ = lchown(path, uid, gid);
    }
}

/// `mkdir -p` where every directory it creates belongs to the bot user when isolation is on.
pub fn mkdir(path: &Path, mode: u32) -> std::io::Result<PathBuf> {
    let missing: Vec<PathBuf> = path.ancestors().take_while(|p| !p.as_os_str().is_empty() && !p.exists()).map(Path::to_path_buf).collect();
    std::fs::DirBuilder::new().recursive(true).mode(mode).create(path)?;
    for part in missing {
        chown(&part);
    }
    Ok(path.to_path_buf())
}

/// Where per-turn host state lives: `default` unless isolated, where the supervisor's state is closed to bot
/// code, so it is a bot-owned `$HOME/.tico-host`.
#[allow(dead_code)] // the Gemini and Antigravity hosts keep their state here
pub fn bot_state(default: &Path) -> PathBuf {
    if !enabled() {
        return default.to_path_buf();
    }
    let home = std::env::var("HOME").ok().filter(|h| !h.is_empty()).unwrap_or_else(|| "/home/runner".into());
    let dir = PathBuf::from(home).join(".tico-host");
    let _ = mkdir(&dir, 0o700);
    dir
}

/// A directory a turn can read files the supervisor prepared for it (vault files).
pub fn turn_dir() -> PathBuf {
    let base = std::env::var("TICO_RUNNER_TURN_DIR").ok().filter(|d| !d.is_empty()).unwrap_or_else(|| "/run/tico-runner/turn".into());
    let base = PathBuf::from(base);
    let _ = std::fs::DirBuilder::new().recursive(true).mode(0o755).create(&base);
    base
}

/// Give the bot user anything under `paths` that root made (a login run with `docker exec`). Never follows
/// symlinks.
pub fn adopt(paths: &[PathBuf]) {
    let Some((uid, gid)) = identity() else { return };
    fn walk(path: &Path, uid: u32, gid: u32) {
        let Ok(meta) = std::fs::symlink_metadata(path) else { return };
        if meta.uid() != uid {
            let _ = lchown(path, uid, gid);
        }
        if meta.is_dir() {
            if let Ok(entries) = std::fs::read_dir(path) {
                for entry in entries.flatten() {
                    walk(&entry.path(), uid, gid);
                }
            }
        }
    }
    for path in paths {
        walk(path, uid, gid);
    }
}

/// The state directory's modes: 0700, or under isolation 0711 with every entry but the public ones private, so
/// the `gh` shim (state/bin) and the read-only mirrors stay reachable for the bot user (state.py State).
pub fn protect_state(directory: &Path) -> std::io::Result<()> {
    if !enabled() {
        return std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700));
    }
    for entry in std::fs::read_dir(directory)?.flatten() {
        let name = entry.file_name();
        let Ok(meta) = std::fs::symlink_metadata(entry.path()) else { continue };
        if name == "mirrors" || meta.file_type().is_symlink() {
            continue;
        }
        let mode = if name == "bin" { 0o755 } else if meta.is_dir() { 0o700 } else { 0o600 };
        let _ = std::fs::set_permissions(entry.path(), std::fs::Permissions::from_mode(mode));
    }
    std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o711))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn off_without_a_bot_uid_and_for_the_supervisor_itself() {
        let s = |v: &str| Some(v.to_string());
        assert_eq!(identity_from(None, None, 501), None);
        assert_eq!(identity_from(s("501"), None, 501), None, "the supervisor's own uid is no separation");
        assert_eq!(identity_from(s("0"), None, 501), None);
        assert_eq!(identity_from(s("bot"), None, 501), None);
        assert_eq!(identity_from(s("10003"), None, 10002), Some((10003, 10003)));
        assert_eq!(identity_from(s("10003"), s("10002"), 10002), Some((10003, 10002)));
        let p = prefix_for((10003, 10002));
        assert_eq!(p, ["/usr/bin/setpriv", "--reuid=10003", "--regid=10002", "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all"]);
    }
}
