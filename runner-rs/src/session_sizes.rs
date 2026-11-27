//! Each bot's session worktrees on this computer (`sessions/bot-<bot>/<task>`, crate::sessions) and their size, for
//! the readiness report (`session_worktrees`, backend/models.py SessionWorktreesReadiness). Health shows them per
//! computer and warns about a pile-up before the disk fills (one QA bot held 68 of them, 102 GB, and nothing said so).
//! Measuring walks every file, so it runs on a blocking thread at most once per MEASURE_EVERY and the last result is
//! reported until the next.

use std::collections::{BTreeMap, HashSet};
use std::io::ErrorKind;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// How often the walk runs: a pile-up grows over days, and the walk reads every file of every checkout.
pub const MEASURE_EVERY: Duration = Duration::from_secs(30 * 60);
/// The most bots reported (backend/models.py SESSION_WORKTREE_BOTS), largest first.
const MOST_BOTS: usize = 200;

/// Metadata of `path` itself (a symlink not followed); None when it went away meanwhile (a worktree removed while the
/// walk ran), an error for anything else, so a scan that could not read everything is never reported as complete.
fn meta(path: &Path) -> Result<Option<std::fs::Metadata>, String> {
    match path.symlink_metadata() {
        Ok(m) => Ok(Some(m)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

/// The entries of folder `path`, or none when it went away meanwhile; an error when it cannot be read.
fn entries(path: &Path) -> Result<Vec<PathBuf>, String> {
    let read = match std::fs::read_dir(path) {
        Ok(read) => read,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    read.map(|e| {
        e.map(|e| e.path())
            .map_err(|e| format!("{}: {e}", path.display()))
    })
    .collect()
}

/// The space everything under `root` takes on volume `dev`: allocated blocks, each file counted once however many
/// names it has (`seen`, kept per bot), symlinks never followed and folders on another volume never entered. This is
/// what the worktrees use, not what removing them frees (the clean-up's own walk, crate::disk_clean, counts that).
fn usage(root: &Path, dev: u64, seen: &mut HashSet<u64>) -> Result<u64, String> {
    let mut bytes = 0u64;
    let mut stack = vec![root.to_path_buf()];
    while let Some(path) = stack.pop() {
        let Some(m) = meta(&path)? else {
            continue;
        };
        if m.dev() != dev {
            continue;
        }
        if m.nlink() > 1 && !m.is_dir() && !seen.insert(m.ino()) {
            continue;
        }
        bytes = bytes.saturating_add(m.blocks() * 512);
        if m.is_dir() {
            stack.extend(entries(&path)?);
        }
    }
    Ok(bytes)
}

/// (count, bytes) of each bot's session worktrees under `sessions`: every real folder in `bot-<bot>/` on the same
/// volume as `sessions`, symlinks never followed. A folder whose name is no bot id the server takes (sessions.rs
/// slug_ok) is left out rather than sent. No `sessions` folder is no worktrees; any folder or file that cannot be read
/// is an error, so a partial count never replaces the last whole one.
pub fn measure(sessions: &Path) -> Result<BTreeMap<String, (u64, u64)>, String> {
    let mut found = BTreeMap::new();
    let Some(root) = meta(sessions)? else {
        return Ok(found);
    };
    if !root.is_dir() {
        return Ok(found);
    }
    let dev = root.dev();
    for folder in entries(sessions)? {
        let name = folder
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let Some(bot) = name
            .strip_prefix("bot-")
            .filter(|b| crate::sessions::slug_ok(b))
        else {
            continue;
        };
        if !meta(&folder)?.is_some_and(|m| m.is_dir() && m.dev() == dev) {
            continue;
        }
        let (mut count, mut bytes, mut seen) = (0u64, 0u64, HashSet::new());
        for tree in entries(&folder)? {
            if meta(&tree)?.is_some_and(|m| m.is_dir() && m.dev() == dev) {
                count += 1;
                bytes = bytes.saturating_add(usage(&tree, dev, &mut seen)?);
            }
        }
        if count > 0 {
            found.insert(bot.to_string(), (count, bytes));
        }
    }
    Ok(found)
}

/// The readiness `session_worktrees` document: when it was measured and each bot's count and bytes, at most
/// MOST_BOTS of them, largest first.
pub fn report(found: &BTreeMap<String, (u64, u64)>, at: f64) -> Value {
    let mut rows: Vec<(&String, &(u64, u64))> = found.iter().collect();
    rows.sort_by(|a, b| b.1 .1.cmp(&a.1 .1).then(a.0.cmp(b.0)));
    let bots: serde_json::Map<String, Value> = rows
        .into_iter()
        .take(MOST_BOTS)
        .map(|(bot, (count, bytes))| (bot.clone(), json!({"count": count, "bytes": bytes})))
        .collect();
    json!({"at": crate::util::iso(&json!(at.floor())), "bots": bots})
}

/// Measures in the background at most once per MEASURE_EVERY and keeps the last report for readiness.
#[derive(Default)]
pub struct SessionSizes {
    last: Mutex<Option<Instant>>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    report: Mutex<Option<Value>>,
}

impl SessionSizes {
    pub fn new() -> SessionSizes {
        SessionSizes::default()
    }

    /// The last measurement, for readiness `session_worktrees`; None until one finished.
    pub fn report(&self) -> Option<Value> {
        self.report.lock().unwrap().clone()
    }

    /// Start a measurement of `projects`/sessions unless one is still running or one started within MEASURE_EVERY.
    pub fn check(self: &Arc<Self>, projects: &Path) {
        let mut task = self.task.lock().unwrap();
        let mut last = self.last.lock().unwrap();
        if task.as_ref().is_some_and(|t| !t.is_finished())
            || last.is_some_and(|at| at.elapsed() < MEASURE_EVERY)
        {
            return;
        }
        *last = Some(Instant::now());
        let (me, sessions): (Arc<Self>, PathBuf) = (self.clone(), projects.join("sessions"));
        *task = Some(tokio::task::spawn_blocking(move || {
            // A scan that could not read everything keeps the last whole one, which says when it was taken.
            me.keep(measure(&sessions), crate::util::epoch_s());
        }));
    }
}

impl SessionSizes {
    /// Report a whole measurement; one that failed keeps the last whole one, which says when it was taken.
    fn keep(&self, measured: Result<BTreeMap<String, (u64, u64)>, String>, at: f64) {
        match measured {
            Ok(found) => *self.report.lock().unwrap() = Some(report(&found, at)),
            Err(e) => crate::util::log(&format!(
                "Tico runner: session worktree sizes not measured, the last measurement stays: {e}"
            )),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("session-sizes-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn file(path: &Path, bytes: usize) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, vec![b'x'; bytes]).unwrap();
    }

    #[test]
    fn each_bots_session_worktrees_are_counted_with_their_size() {
        let root = temp();
        let sessions = root.join("sessions");
        for task in ["t1", "t2", "t3"] {
            file(
                &sessions.join(format!("bot-qa/{task}/evidence.bin")),
                64 * 1024,
            );
            file(&sessions.join(format!("bot-qa/{task}/.git")), 10);
        }
        file(&sessions.join("bot-dev/t9/src/main.rs"), 4096);
        file(&sessions.join("bot-qa/stray-file"), 10); // a file, not a worktree
        file(&root.join("elsewhere/big.bin"), 1024 * 1024);
        std::os::unix::fs::symlink(root.join("elsewhere"), sessions.join("bot-qa/linked")).unwrap();
        std::fs::create_dir_all(sessions.join("bot-empty")).unwrap();
        std::fs::create_dir_all(sessions.join("not-a-bot/t1")).unwrap();

        let found = measure(&sessions).unwrap();
        assert_eq!(found.keys().collect::<Vec<_>>(), ["dev", "qa"]);
        let (count, bytes) = found["qa"];
        assert_eq!(
            count, 3,
            "the symlinked folder and the stray file are not worktrees"
        );
        assert!(
            (3 * 64 * 1024..1024 * 1024).contains(&bytes),
            "{bytes}: the symlink's target is not counted"
        );
        assert_eq!(found["dev"].0, 1);

        let doc = report(&found, 1_795_000_000.0);
        assert_eq!(doc["bots"]["qa"]["count"], 3);
        assert!(doc["at"].as_str().unwrap().starts_with("2026-"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_hard_linked_file_counts_once_and_is_not_left_out() {
        let root = temp();
        let sessions = root.join("sessions");
        let big = sessions.join("bot-qa/t1/video.webm");
        file(&big, 2 * 1024 * 1024);
        std::fs::hard_link(&big, sessions.join("bot-qa/t1/copy.webm")).unwrap();
        std::fs::create_dir_all(sessions.join("bot-qa/t2")).unwrap();
        std::fs::hard_link(&big, sessions.join("bot-qa/t2/same.webm")).unwrap();
        let (count, bytes) = measure(&sessions).unwrap()["qa"];
        assert_eq!(count, 2);
        assert!(
            (2 * 1024 * 1024..3 * 1024 * 1024).contains(&bytes),
            "{bytes}: three names, one file"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_folder_named_for_no_bot_the_server_takes_is_left_out() {
        let root = temp();
        let sessions = root.join("sessions");
        file(&sessions.join(format!("bot-{}/t1/a", "x".repeat(101))), 10);
        file(&sessions.join("bot-.hidden/t1/a"), 10);
        file(&sessions.join("bot-qa/t1/a"), 10);
        let found = measure(&sessions).unwrap();
        assert_eq!(found.keys().collect::<Vec<_>>(), ["qa"]);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_scan_that_cannot_read_a_folder_keeps_the_last_whole_measurement() {
        let sizes = SessionSizes::new();
        let whole = BTreeMap::from([("qa".to_string(), (68u64, 102u64 << 30))]);
        sizes.keep(Ok(whole), 1_795_000_000.0);
        sizes.keep(
            Err("sessions/bot-qa: Permission denied".into()),
            1_795_001_800.0,
        );
        let kept = sizes.report().unwrap();
        assert_eq!(kept["bots"]["qa"]["count"], 68);
        assert_eq!(kept["at"], report(&BTreeMap::new(), 1_795_000_000.0)["at"]);

        // An unreadable worktree is an error, not a smaller count (root reads everything, so only as another user).
        if unsafe { libc::geteuid() } != 0 {
            use std::os::unix::fs::PermissionsExt;
            let root = temp();
            let sessions = root.join("sessions");
            file(&sessions.join("bot-qa/t1/inner/a"), 10);
            let inner = sessions.join("bot-qa/t1/inner");
            std::fs::set_permissions(&inner, std::fs::Permissions::from_mode(0o000)).unwrap();
            let measured = measure(&sessions);
            std::fs::set_permissions(&inner, std::fs::Permissions::from_mode(0o755)).unwrap();
            assert!(measured.is_err(), "{measured:?}");
            let _ = std::fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn no_sessions_folder_reports_no_bots() {
        let root = temp();
        assert!(measure(&root.join("sessions")).unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }
}
