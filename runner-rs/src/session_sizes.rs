//! Each bot's session worktrees on this computer (`sessions/bot-<bot>/<task>`, crate::sessions) and their size, for
//! the readiness report (`session_worktrees`, backend/models.py SessionWorktreesReadiness). Health shows them per
//! computer and warns about a pile-up before the disk fills (one QA bot held 68 of them, 102 GB, and nothing said so).
//! Measuring walks every file, so it runs on a blocking thread at most once per MEASURE_EVERY and the last result is
//! reported until the next.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// How often the walk runs: a pile-up grows over days, and the walk reads every file of every checkout.
pub const MEASURE_EVERY: Duration = Duration::from_secs(30 * 60);
/// The most bots reported (backend/models.py SESSION_WORKTREE_BOTS), largest first.
const MOST_BOTS: usize = 200;

fn is_real_dir(path: &Path) -> bool {
    path.symlink_metadata().is_ok_and(|m| m.is_dir())
}

/// (count, bytes) of each bot's session worktrees under `sessions`: every real folder in `bot-<bot>/`, symlinks
/// never followed, a folder on another volume not counted.
pub fn measure(sessions: &Path) -> BTreeMap<String, (u64, u64)> {
    let mut found = BTreeMap::new();
    for entry in std::fs::read_dir(sessions).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let Some(bot) = name.strip_prefix("bot-").filter(|b| !b.is_empty()) else {
            continue;
        };
        if !is_real_dir(&entry.path()) {
            continue;
        }
        let (mut count, mut bytes) = (0u64, 0u64);
        for tree in std::fs::read_dir(entry.path())
            .into_iter()
            .flatten()
            .flatten()
        {
            if is_real_dir(&tree.path()) {
                count += 1;
                bytes += crate::disk_clean::size_of(&tree.path());
            }
        }
        if count > 0 {
            found.insert(bot.to_string(), (count, bytes));
        }
    }
    found
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
            let report = report(&measure(&sessions), crate::util::epoch_s());
            *me.report.lock().unwrap() = Some(report);
        }));
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

        let found = measure(&sessions);
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
    fn no_sessions_folder_reports_no_bots() {
        let root = temp();
        assert!(measure(&root.join("sessions")).is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }
}
