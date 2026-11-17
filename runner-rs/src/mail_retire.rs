//! On a computer's first start after the update, remove the mail copy and the Google credentials the retired mail
//! job kept here: mail and calendar run on the server now (docs/mail.md), so no computer holds a Google key or token.
//! A one-time step that 0.7 removes. Idempotent: a later start finds nothing and says nothing.
//!
//! Best effort and never fatal: one log line per thing removed and one per failure. A symlink is removed itself,
//! never followed, and a file GOOGLE_SA_KEY or GOOGLE_OAUTH_GRANTS names is the operator's and stays.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use crate::config::Config;
use crate::util::log;

/// The Workspace key and connected-account grants, their half-written copies, and the re-enrollment carry markers
/// (the retired mail_key.rs kept them in `<projects>/secrets/` and in the state directories).
const KEY_FILES: &[&str] = &[
    "google-sa.json",
    "google-sa.json.new",
    "google-oauth.json",
    "google-oauth.json.new",
    ".mail-key-carried",
    ".google-oauth-carried",
];

/// The mail tool's database and audit log in `<projects>/runtime/mail`, removed before the folder itself.
const MAIL_FILES: &[&str] = &[
    "mail.db",
    "mail.db-wal",
    "mail.db-shm",
    "mail.db-journal",
    "audit.jsonl",
];

/// Remove what the retired mail job left on this computer (see the module comment).
pub fn once(config: &Config) {
    retire(config, &operator_files());
}

fn retire(config: &Config, kept: &[PathBuf]) {
    let projects = config.projects_dir();
    if !projects.as_os_str().is_empty() {
        retire_mail_folder(&projects.join("runtime").join("mail"), kept);
        let secrets = projects.join("secrets");
        for name in KEY_FILES {
            remove(&secrets.join(name), kept);
        }
    }
    for dir in state_dirs(config) {
        for name in KEY_FILES {
            remove(&dir.join(name), kept);
        }
    }
}

/// The files the operator named for the connectors (GOOGLE_SA_KEY, GOOGLE_OAUTH_GRANTS): never touched.
fn operator_files() -> Vec<PathBuf> {
    let home = std::env::var("HOME").unwrap_or_default();
    ["GOOGLE_SA_KEY", "GOOGLE_OAUTH_GRANTS"]
        .into_iter()
        .filter_map(|name| std::env::var(name).ok())
        .filter(|named| !named.is_empty())
        .map(|named| {
            PathBuf::from(
                named
                    .strip_prefix("~/")
                    .map(|r| format!("{home}/{r}"))
                    .unwrap_or(named),
            )
        })
        .collect()
}

/// This registration's state directory and every other `state-*` directory beside the registration file: a
/// previous registration's, where the retired key carry looked (`state-<runner_id>` of `runner.json.stale`).
/// A symlink named `state-*` is not followed.
fn state_dirs(config: &Config) -> BTreeSet<PathBuf> {
    let mut dirs = BTreeSet::from([config.state_dir()]);
    let parent = config
        .path
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    if let Ok(entries) = std::fs::read_dir(parent) {
        for entry in entries.flatten() {
            if entry.file_name().to_string_lossy().starts_with("state-")
                && entry.file_type().is_ok_and(|t| t.is_dir())
            {
                dirs.insert(entry.path());
            }
        }
    }
    dirs
}

/// `path` with its parent directory resolved, the file name itself not followed.
fn resolved(path: &Path) -> Option<PathBuf> {
    let name = path.file_name()?;
    let parent = path
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    Some(parent.canonicalize().ok()?.join(name))
}

fn same_place(a: &Path, b: &Path) -> bool {
    a == b || resolved(a).is_some_and(|r| Some(r) == resolved(b))
}

fn failed(path: &Path, e: &std::io::Error) {
    log(&format!(
        "Tico runner: could not remove {} the retired mail job left ({:?})",
        path.display(),
        e.kind()
    ));
}

fn removed(path: &Path) {
    log(&format!(
        "Tico runner: removed {}; mail and calendar run on the server",
        path.display()
    ));
}

/// Remove one file (a symlink itself, never its target), unless the operator named it.
fn remove(path: &Path, kept: &[PathBuf]) {
    match std::fs::symlink_metadata(path) {
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
        Err(e) => {
            failed(path, &e);
            return;
        }
    }
    if kept.iter().any(|k| same_place(k, path)) {
        return;
    }
    match std::fs::remove_file(path) {
        Ok(()) => removed(path),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => failed(path, &e),
    }
}

/// The mail tool's folder: its database and audit log first, then the folder and its venv. A symlink in its place is
/// removed itself; a folder holding a file the operator named stays.
fn retire_mail_folder(folder: &Path, kept: &[PathBuf]) {
    match std::fs::symlink_metadata(folder) {
        Ok(meta) if meta.is_dir() => {}
        Ok(_) => {
            remove(folder, kept);
            return;
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
        Err(e) => {
            failed(folder, &e);
            return;
        }
    }
    for name in MAIL_FILES {
        remove(&folder.join(name), kept);
    }
    let canonical = folder.canonicalize().ok();
    let holds_kept = kept.iter().any(|k| {
        k.starts_with(folder)
            || resolved(k)
                .zip(canonical.as_ref())
                .is_some_and(|(r, c)| r.starts_with(c))
    });
    if holds_kept {
        return;
    }
    // remove_dir_all removes symlinks inside the folder, never what they point to.
    match std::fs::remove_dir_all(folder) {
        Ok(()) => removed(folder),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => failed(folder, &e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn registration(dir: &Path, data: Value) -> Config {
        let path = dir.join("runner.json");
        std::fs::write(&path, data.to_string()).unwrap();
        Config::load(&path).unwrap()
    }

    /// A computer as the retired mail job left it: the mail folder, keys in secrets/ and in two state directories.
    fn computer() -> (PathBuf, Config) {
        let dir =
            std::env::temp_dir().join(format!("tico-mailretire-{}", uuid::Uuid::new_v4().simple()));
        let projects = dir.join("projects");
        let mail = projects.join("runtime").join("mail");
        std::fs::create_dir_all(mail.join("venv").join("bin")).unwrap();
        for name in MAIL_FILES {
            std::fs::write(mail.join(name), "x").unwrap();
        }
        std::fs::write(mail.join("venv").join("bin").join("python"), "x").unwrap();
        std::fs::create_dir_all(projects.join("secrets")).unwrap();
        std::fs::write(projects.join("secrets").join("google-sa.json"), "{}").unwrap();
        std::fs::write(projects.join("secrets").join("google-oauth.json"), "{}").unwrap();
        std::fs::write(
            projects.join("secrets").join("close-calls.env"),
            "CLOSE_API_KEY=k\n",
        )
        .unwrap();
        for state in ["state-new", "state-old"] {
            let d = dir.join(state);
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join("google-sa.json"), "{}").unwrap();
            std::fs::write(d.join("google-oauth.json.new"), "{}").unwrap();
            std::fs::write(d.join(".mail-key-carried"), "").unwrap();
            std::fs::write(d.join("runner.lock"), "").unwrap();
        }
        let config = registration(
            &dir,
            json!({"runner_id": "new", "projects_dir": projects.to_string_lossy()}),
        );
        (dir, config)
    }

    #[test]
    fn the_mail_copy_and_google_credentials_go_once_and_nothing_else() {
        let (dir, config) = computer();
        let projects = dir.join("projects");
        retire(&config, &[]);
        assert!(!projects.join("runtime").join("mail").exists());
        assert!(
            projects.join("runtime").exists(),
            "only the mail folder goes"
        );
        for gone in ["google-sa.json", "google-oauth.json"] {
            assert!(!projects.join("secrets").join(gone).exists(), "{gone}");
        }
        assert_eq!(
            std::fs::read_to_string(projects.join("secrets").join("close-calls.env")).unwrap(),
            "CLOSE_API_KEY=k\n",
            "an unrelated secret stays"
        );
        for state in ["state-new", "state-old"] {
            let d = dir.join(state);
            for gone in [
                "google-sa.json",
                "google-oauth.json.new",
                ".mail-key-carried",
            ] {
                assert!(!d.join(gone).exists(), "{state}/{gone}");
            }
            assert!(d.join("runner.lock").exists(), "{state} keeps the rest");
        }
        retire(&config, &[]);
        assert!(
            projects.join("secrets").join("close-calls.env").exists(),
            "a second start is a no-op"
        );
        assert!(dir.join("state-new").join("runner.lock").exists());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_symlinked_key_goes_without_its_target() {
        let (dir, config) = computer();
        let target = dir.join("elsewhere.json");
        std::fs::write(&target, "{\"key\": 1}").unwrap();
        let link = dir.join("projects").join("secrets").join("google-sa.json");
        std::fs::remove_file(&link).unwrap();
        std::os::unix::fs::symlink(&target, &link).unwrap();
        retire(&config, &[]);
        assert!(std::fs::symlink_metadata(&link).is_err(), "the link goes");
        assert_eq!(
            std::fs::read_to_string(&target).unwrap(),
            "{\"key\": 1}",
            "its target is untouched"
        );
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_key_the_operator_named_stays() {
        let (dir, config) = computer();
        let named = dir.join("state-old").join("google-sa.json");
        retire(&config, std::slice::from_ref(&named));
        assert!(named.exists(), "GOOGLE_SA_KEY's file is the operator's");
        assert!(!dir.join("state-new").join("google-sa.json").exists());
        std::fs::remove_dir_all(dir).ok();
    }
}
