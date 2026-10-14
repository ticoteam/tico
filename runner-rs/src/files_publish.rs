//! A bot's reports and artifacts published as Tico files after a completed turn (runner/files_publish.py,
//! clients/bot_files.py; docs/files.md).
//!
//! The folders are `files: {publish: [...]}` (the attempt's config, else bot.yaml), else `reports/` and
//! `artifacts/`. What goes is decided here: allowed types, 25 MB at most, no credential-like names, no symbolic
//! or hard links, nothing outside the checkout. Each upload is a row in an outbox in the runner's state database
//! keyed by bot, path and content digest; the key is also the request's idempotency key, so a retry lands once.
//! A file that could not go is reported as not synced, never shown with a link that opens nothing.

use std::collections::HashSet;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use rusqlite::{params, OptionalExtension};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::client::{quote_plus, ApiError, Client};
use crate::state::State;
use crate::util::log;

pub const MAX_PER_TURN: usize = 40;
pub const MAX_DEPTH: usize = 6;
pub const DRAIN_BATCH: usize = 20;
pub const GIVE_UP_AFTER: i64 = 20;
pub const UPLOAD_TIMEOUT: u64 = 120;
pub const MAX_BYTES: u64 = 25 * 1024 * 1024;
pub const DEFAULT_FOLDERS: &[&str] = &["reports", "artifacts"];
pub const TYPES: &[&str] = &[
    ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".ogg", ".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml",
    ".yml", ".html", ".htm", ".pdf", ".rtf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".png",
    ".jpg", ".jpeg", ".gif", ".webp",
];

pub const TABLES: &str = "
CREATE TABLE IF NOT EXISTS file_outbox(
  key TEXT PRIMARY KEY, bot TEXT NOT NULL, attempt_id TEXT NOT NULL, root TEXT NOT NULL, rel TEXT NOT NULL,
  digest TEXT NOT NULL, commit_sha TEXT NOT NULL DEFAULT '', phase TEXT NOT NULL DEFAULT 'pending',
  tries INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS file_published(
  bot TEXT NOT NULL, rel TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(bot, rel));
CREATE TABLE IF NOT EXISTS file_refused(
  bot TEXT NOT NULL, rel TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(bot, rel, reason));
";

/// Python's `Path(name).suffix.lower()`.
fn suffix(name: &str) -> String {
    let base = name.rsplit('/').next().unwrap_or(name);
    let trimmed = base.trim_start_matches('.');
    match trimmed.rfind('.') {
        Some(i) if i + 1 < trimmed.len() => trimmed[i..].to_lowercase(),
        _ => String::new(),
    }
}

/// The display name of an allowed file, or why not (bot_files.check_name).
pub fn check_name(name: &str) -> Result<String, String> {
    static CREDENTIAL_NAME: OnceLock<Regex> = OnceLock::new();
    let credential = CREDENTIAL_NAME.get_or_init(|| {
        Regex::new(r"(?i)(^|[._\-\s])(env|credentials?|secrets?|passwords?|passwd|api[-_ ]?keys?|private[-_ ]?keys?|id_rsa|id_ed25519|netrc|npmrc|pypirc|kubeconfig)($|[._\-\s])|\.(pem|key|p12|pfx|keystore|jks)$").unwrap()
    });
    let replaced = name.replace('\\', "/");
    let base: String = replaced.rsplit('/').next().unwrap_or("").trim().chars().filter(|c| !matches!(*c as u32, 0..=0x1f | 0x7f)).take(200).collect();
    let ext = suffix(&base);
    if base.is_empty() || (base.starts_with('.') && !TYPES.contains(&ext.as_str())) {
        return Err(format!("{} is not a file name Tico publishes", if base.is_empty() { "that name" } else { &base }));
    }
    if credential.is_match(&base) || base.to_lowercase().starts_with(".env") {
        return Err(format!("{base} looks like a credential, so it is never published"));
    }
    if !TYPES.contains(&ext.as_str()) {
        let mut sorted = TYPES.to_vec();
        sorted.sort();
        return Err(format!("{} files are not published. Allowed types: {}", if ext.is_empty() { &base } else { &ext }, sorted.join(", ")));
    }
    Ok(base)
}

pub fn check_size(size: u64) -> Result<(), String> {
    if size == 0 {
        return Err("An empty file is not published".into());
    }
    if size > MAX_BYTES {
        return Err(format!("A file is at most {} MB; this one is {} MB", MAX_BYTES / (1024 * 1024), size / (1024 * 1024)));
    }
    Ok(())
}

/// (real path, relative name) of a file inside `root` a bot may publish, or why not (bot_files.local_file).
pub fn local_file(root: &Path, rel: &str) -> Result<(PathBuf, String), String> {
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    if rel.contains('\0') {
        return Err("That path is not valid".into());
    }
    let relative = if rel.starts_with('/') {
        Path::new(rel).strip_prefix(&root).map_err(|_| "That path is outside the bot's checkout".to_string())?.to_string_lossy().to_string()
    } else {
        rel.to_string()
    };
    let parts: Vec<&str> = relative.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    if parts.is_empty() || parts.contains(&"..") {
        return Err("That path is outside the bot's checkout".into());
    }
    let mut walk = root.clone();
    for part in &parts {
        walk.push(part);
        if std::fs::symlink_metadata(&walk).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(format!("{part} is a symbolic link, which is never published"));
        }
    }
    let joined = parts.join("/");
    let meta = std::fs::metadata(&walk).ok().filter(|m| m.is_file()).ok_or_else(|| format!("{joined} is not a regular file"))?;
    if !walk.canonicalize().is_ok_and(|r| r.starts_with(&root) && r != root) {
        return Err("That path is outside the bot's checkout".into());
    }
    check_name(&walk.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default())?;
    if meta.nlink() > 1 {
        return Err(format!("{joined} has more than one hard link, so it may be another file under a new name"));
    }
    check_size(meta.len())?;
    Ok((walk, joined))
}

pub fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

fn file_digest(path: &Path) -> std::io::Result<String> {
    use std::io::Read;
    let mut f = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// The folders to publish from: `files.publish` in the attempt's config, else bot.yaml's, else the defaults.
pub fn folders_for(config: &Value, root: &Path) -> Vec<String> {
    let manifest = crate::manifest::read(root).unwrap_or(Value::Null);
    for source in [config, &manifest] {
        let files = source.get("files").filter(|f| crate::util::truthy(Some(f)));
        if let Some(Value::Array(list)) = files.and_then(|f| f.get("publish")) {
            return list
                .iter()
                .map(|f| crate::manifest::py_str(f).trim().trim_matches('/').to_string())
                .filter(|f| !f.is_empty())
                .collect();
        }
    }
    DEFAULT_FOLDERS.iter().map(|f| f.to_string()).collect()
}

fn mtime(path: &Path) -> f64 {
    std::fs::symlink_metadata(path).map(|m| m.mtime() as f64 + m.mtime_nsec() as f64 / 1e9).unwrap_or(0.0)
}

/// [(relative path, refused reason or "")] under the folders: paths not in `seen` first, then newest first.
pub fn candidates(root: &Path, folders: &[String], seen: &HashSet<String>) -> Vec<(String, String)> {
    let Ok(root) = root.canonicalize() else { return vec![] };
    let mut found: Vec<(f64, String, String)> = vec![];
    for folder in folders {
        let base = root.join(folder);
        let link = std::fs::symlink_metadata(&base).is_ok_and(|m| m.file_type().is_symlink());
        if link || !base.is_dir() || !base.canonicalize().is_ok_and(|b| b.starts_with(&root) && b != root) {
            continue;
        }
        let mut stack = vec![(base, 0usize)];
        while let Some((directory, depth)) = stack.pop() {
            let Ok(read) = std::fs::read_dir(&directory) else { continue };
            let mut entries: Vec<PathBuf> = read.flatten().map(|e| e.path()).collect();
            entries.sort();
            for entry in entries {
                let name = entry.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
                if name.starts_with('.') && !TYPES.contains(&suffix(&name).as_str()) {
                    continue;
                }
                let rel = entry.strip_prefix(&root).map(|r| r.to_string_lossy().to_string()).unwrap_or_default();
                let meta = std::fs::symlink_metadata(&entry);
                if meta.as_ref().is_ok_and(|m| m.file_type().is_symlink()) {
                    found.push((mtime(&entry), rel, "is a symbolic link".into()));
                } else if meta.as_ref().is_ok_and(|m| m.is_dir()) {
                    if depth < MAX_DEPTH {
                        stack.push((entry, depth + 1));
                    }
                } else {
                    found.push((mtime(&entry), rel, String::new()));
                }
            }
        }
    }
    // The cut bounds the work per turn. A fresh checkout gives every committed file the same time, so files
    // never published or refused go first; then newest first, so an edit made during the turn makes it.
    found.sort_by(|a, b| {
        (seen.contains(&a.1), b.0, &a.1).partial_cmp(&(seen.contains(&b.1), a.0, &b.1)).unwrap_or(std::cmp::Ordering::Equal)
    });
    found.into_iter().take(MAX_PER_TURN * 5).map(|(_, rel, why)| (rel, why)).collect()
}

/// The commit a file is committed at, once that commit is on the remote; "" otherwise (bot_files.pushed_commit).
pub fn pushed_commit(root: &Path, rel: &str) -> String {
    let env = crate::safegit::clean_environment(&crate::env::process_environment());
    let git = |args: &[&str]| -> Option<(bool, String)> {
        let mut cmd = crate::isolation::std_command("git");
        cmd.args(crate::safegit::args(root)).args(args).env_clear().envs(&env).stdin(std::process::Stdio::null());
        let out = cmd.output().ok()?;
        Some((out.status.success(), String::from_utf8_lossy(&out.stdout).trim().to_string()))
    };
    if !git(&["ls-files", "--error-unmatch", "--", rel]).is_some_and(|(ok, _)| ok) {
        return String::new();
    }
    if !git(&["diff", "--quiet", "HEAD", "--", rel]).is_some_and(|(ok, _)| ok) {
        return String::new();
    }
    match (git(&["rev-list", "--count", "@{u}..HEAD"]), git(&["rev-parse", "HEAD"])) {
        (Some((true, ahead)), Some((true, head))) if ahead == "0" => head,
        _ => String::new(),
    }
}

fn now() -> f64 {
    crate::util::epoch_s()
}

/// Queue what changed since the last publish; (queued, refused). `skip` names files that hold a granted secret.
pub fn prepare(state: &State, bot: &str, attempt_id: &str, root: &Path, config: &Value, pushed: bool, skip: &[String]) -> Result<(usize, usize), String> {
    let seen: HashSet<String> = state.with(|c| {
        c.execute_batch(TABLES)?;
        let mut q = c.prepare("SELECT rel FROM file_published WHERE bot=?1 UNION SELECT rel FROM file_refused WHERE bot=?1")?;
        let rows = q.query_map([bot], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<HashSet<_>>>();
        rows
    })?;
    let resolved = root.canonicalize().unwrap_or_else(|_| root.to_path_buf()).display().to_string();
    let (mut queued, mut refused) = (0, 0);
    for (rel, why) in candidates(root, &folders_for(config, root), &seen) {
        let checked: Result<(String, String), (String, String)> = (|| {
            if skip.contains(&rel) {
                return Err((rel.clone(), "holds a secret".to_string()));
            }
            if !why.is_empty() {
                return Err((rel.clone(), why.clone()));
            }
            let (path, rel) = local_file(root, &rel).map_err(|e| (rel.clone(), e))?;
            let digest = file_digest(&path).map_err(|e| (rel.clone(), e.to_string()))?;
            Ok((rel, digest))
        })();
        let (rel, digest) = match checked {
            Ok(ok) => ok,
            Err((rel, why)) => {
                refused += 1;
                // Logged once per file and reason, remembered on disk so a restart does not log it again.
                let first = state.with(|c| c.execute("INSERT OR IGNORE INTO file_refused VALUES(?1,?2,?3)", params![bot, rel, why]))?;
                if first > 0 {
                    log(&format!("Tico runner: {bot}: not publishing {rel}: {why}"));
                }
                continue;
            }
        };
        let key = format!("file:{bot}:{}:{}", &sha256_hex(rel.as_bytes())[..16], &digest[..32]);
        let commit = if pushed { pushed_commit(root, &rel) } else { String::new() };
        let stop = state.with(|c| {
            // Accepted now: a later refusal of this file is news again.
            c.execute("DELETE FROM file_refused WHERE bot=?1 AND rel=?2", params![bot, rel])?;
            let same = c.query_row("SELECT 1 FROM file_published WHERE bot=?1 AND rel=?2 AND digest=?3", params![bot, rel, digest], |_| Ok(())).optional()?;
            let queued_already = c.query_row("SELECT 1 FROM file_outbox WHERE key=?1", [&key], |_| Ok(())).optional()?;
            if same.is_some() || queued_already.is_some() {
                return Ok(Some(false));
            }
            if queued >= MAX_PER_TURN {
                return Ok(None);
            }
            c.execute(
                "INSERT INTO file_outbox(key,bot,attempt_id,root,rel,digest,commit_sha,created) VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
                params![key, bot, attempt_id, resolved, rel, digest, commit, now()],
            )?;
            Ok(Some(true))
        })?;
        match stop {
            None => break,
            Some(true) => queued += 1,
            Some(false) => {}
        }
    }
    Ok((queued, refused))
}

/// Whether a file under `root` still waits to be uploaded.
#[allow(dead_code)] // the task worktrees keep a folder until its files have gone
pub fn pending_under(state: &State, root: &Path) -> bool {
    let resolved = root.canonicalize().unwrap_or_else(|_| root.to_path_buf()).display().to_string();
    state
        .with(|c| {
            c.execute_batch(TABLES)?;
            c.query_row("SELECT 1 FROM file_outbox WHERE phase='pending' AND root=?1 LIMIT 1", [resolved], |_| Ok(())).optional()
        })
        .is_ok_and(|r| r.is_some())
}

struct Row {
    key: String,
    bot: String,
    attempt_id: String,
    root: String,
    rel: String,
    digest: String,
    commit_sha: String,
    tries: i64,
}

fn query(row: &Row, extra: &[(&str, &str)]) -> String {
    let name = row.rel.rsplit('/').next().unwrap_or(&row.rel);
    let mut pairs = vec![("bot", row.bot.as_str()), ("attempt", &row.attempt_id), ("path", &row.rel), ("name", name)];
    if !row.commit_sha.is_empty() {
        pairs.push(("commit", &row.commit_sha));
    }
    pairs.extend_from_slice(extra);
    pairs.iter().map(|(k, v)| format!("{}={}", quote_plus(k), quote_plus(v))).collect::<Vec<_>>().join("&")
}

static DRAINING: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Upload what is queued. Safe any time: a retry is the same request. Returns how many went.
pub async fn drain(state: &State, client: &Client) -> usize {
    let Ok(_one) = DRAINING.try_lock() else { return 0 };
    let rows: Vec<Row> = state
        .with(|c| {
            c.execute_batch(TABLES)?;
            let mut q = c.prepare("SELECT key,bot,attempt_id,root,rel,digest,commit_sha,tries FROM file_outbox WHERE phase='pending' ORDER BY created LIMIT ?1")?;
            let rows = q
                .query_map([DRAIN_BATCH as i64], |r| {
                    Ok(Row { key: r.get(0)?, bot: r.get(1)?, attempt_id: r.get(2)?, root: r.get(3)?, rel: r.get(4)?, digest: r.get(5)?, commit_sha: r.get(6)?, tries: r.get(7)? })
                })?
                .collect::<rusqlite::Result<Vec<_>>>();
            rows
        })
        .unwrap_or_default();
    if rows.is_empty() {
        return 0;
    }
    // Big files need longer than the runner's quick calls allow.
    let Ok(uploads) = Client::new(&client.url, &client.token, UPLOAD_TIMEOUT, 1) else { return 0 };
    let mut sent = 0;
    for row in rows {
        sent += upload(state, &uploads, &row).await;
    }
    sent
}

fn mark(state: &State, row: &Row, phase: &str, error: &str, tries: Option<i64>) {
    let _ = state.with(|c| {
        c.execute("UPDATE file_outbox SET phase=?1,error=?2,tries=coalesce(?3,tries) WHERE key=?4", params![phase, error, tries, row.key])?;
        if phase == "done" {
            c.execute("INSERT OR REPLACE INTO file_published VALUES(?1,?2,?3)", params![row.bot, row.rel, row.digest])?;
        }
        Ok(())
    });
}

async fn upload(state: &State, client: &Client, row: &Row) -> usize {
    let checked = local_file(Path::new(&row.root), &row.rel).and_then(|(path, _)| {
        let data = std::fs::read(&path).map_err(|e| e.to_string())?;
        Ok(data)
    });
    let data = match checked {
        Ok(data) if sha256_hex(&data) != row.digest => {
            mark(state, row, "stale", "changed on disk since it was queued; the next turn queues the new content", None);
            return 0;
        }
        Ok(data) => data,
        Err(why) => {
            mark(state, row, "failed", &why, None);
            return 0;
        }
    };
    match client.post_raw(&format!("/api/v2/files/uploads?{}", query(row, &[])), data, &row.key).await {
        Ok(_) => {
            mark(state, row, "done", "", None);
            1
        }
        Err(e) => {
            let tries = row.tries + 1;
            let why = crate::runner::describe(&e);
            if e.retryable && tries < GIVE_UP_AFTER {
                mark(state, row, "pending", &why, Some(tries));
                return 0;
            }
            log(&format!("Tico runner: {}: could not publish {} ({why}); it shows as not synced", row.bot, row.rel));
            mark(state, row, "failed", &why, Some(tries));
            report_not_synced(client, row).await;
            0
        }
    }
}

/// Tell the hub this file is not synced, so its page never shows a link that opens nothing.
async fn report_not_synced(client: &Client, row: &Row) {
    let _: Result<Value, ApiError> =
        client.post_raw(&format!("/api/v2/files/uploads?{}", query(row, &[("sync", "failed")])), vec![], &format!("{}:not-synced", row.key)).await;
}

/// The one call after a completed turn. Never fails the turn: a report that cannot be published is logged.
pub async fn after_turn(state: &State, client: &Client, attempt: &Value, root: &Path, pushed: bool, skip: &[String]) {
    let bot = crate::util::text_of(&attempt["bot"]);
    let config = if attempt["config"].is_object() { attempt["config"].clone() } else { Value::Object(Default::default()) };
    match prepare(state, &bot, &crate::util::text_of(&attempt["id"]), root, &config, pushed, skip) {
        Ok((queued, _)) => {
            if queued > 0 {
                log(&format!("Tico runner: {bot}: publishing {queued} file{}", if queued != 1 { "s" } else { "" }));
            }
            drain(state, client).await;
        }
        Err(e) => log(&format!("Tico runner: publishing files failed ({e}); will retry")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn checkout() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-files-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("reports/deep")).unwrap();
        std::fs::create_dir_all(dir.join("artifacts")).unwrap();
        dir
    }

    #[test]
    fn names_types_links_and_sizes_are_refused_with_reasons() {
        assert_eq!(check_name("reports/q3.md").unwrap(), "q3.md");
        assert!(check_name(".env").unwrap_err().contains("not a file name"));
        assert_eq!(check_name("prod.env.md").unwrap_err(), "prod.env.md looks like a credential, so it is never published");
        assert!(check_name("api_keys.csv").unwrap_err().contains("looks like a credential"));
        assert!(check_name("tool.exe").unwrap_err().starts_with(".exe files are not published. Allowed types: .csv, .doc"));
        assert!(check_name("Makefile").unwrap_err().starts_with("Makefile files are not published"));
        assert_eq!(check_size(0).unwrap_err(), "An empty file is not published");
        assert_eq!(check_size(26 * 1024 * 1024).unwrap_err(), "A file is at most 25 MB; this one is 26 MB");

        let dir = checkout();
        std::fs::write(dir.join("reports/a.md"), "# A").unwrap();
        std::fs::write(dir.join("outside.md"), "x").unwrap();
        std::os::unix::fs::symlink(dir.join("outside.md"), dir.join("reports/link.md")).unwrap();
        std::fs::hard_link(dir.join("outside.md"), dir.join("reports/hard.md")).unwrap();
        assert_eq!(local_file(&dir, "reports/a.md").unwrap().1, "reports/a.md");
        assert_eq!(local_file(&dir, "reports/../outside.md").unwrap_err(), "That path is outside the bot's checkout");
        assert_eq!(local_file(&dir, "reports/link.md").unwrap_err(), "link.md is a symbolic link, which is never published");
        assert!(local_file(&dir, "reports/hard.md").unwrap_err().contains("more than one hard link"));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_turn_queues_new_and_changed_files_once_and_logs_a_refusal_once() {
        let dir = checkout();
        let state = State::open(&dir.join("state")).unwrap();
        std::fs::write(dir.join("reports/a.md"), "# A").unwrap();
        std::fs::write(dir.join("reports/deep/b.csv"), "x,y").unwrap();
        std::fs::write(dir.join("artifacts/run.exe"), "MZ").unwrap();
        std::fs::write(dir.join("artifacts/leak.txt"), "token").unwrap();
        std::fs::write(dir.join("notes.md"), "not in a publish folder").unwrap();
        let config = json!({});
        let skip = vec!["artifacts/leak.txt".to_string()];
        assert_eq!(prepare(&state, "ops", "a1", &dir, &config, false, &skip).unwrap(), (2, 2));
        assert_eq!(prepare(&state, "ops", "a2", &dir, &config, false, &skip).unwrap(), (0, 2), "queued once");
        let refusals: i64 = state.with(|c| c.query_row("SELECT count(*) FROM file_refused", [], |r| r.get(0))).unwrap();
        assert_eq!(refusals, 2);
        let key: String = state.with(|c| c.query_row("SELECT key FROM file_outbox WHERE rel='reports/a.md'", [], |r| r.get(0))).unwrap();
        assert_eq!(key, format!("file:ops:{}:{}", &sha256_hex(b"reports/a.md")[..16], &sha256_hex(b"# A")[..32]));
        std::fs::write(dir.join("reports/a.md"), "# A, revised").unwrap();
        assert_eq!(prepare(&state, "ops", "a3", &dir, &config, false, &skip).unwrap().0, 1, "a changed file goes again");
        std::fs::create_dir_all(dir.join("out")).unwrap();
        std::fs::write(dir.join("out/c.pdf"), "%PDF").unwrap();
        std::fs::write(dir.join("bot.yaml"), "files:\n  publish: [/out/]\n").unwrap();
        assert_eq!(folders_for(&json!({}), &dir), vec!["out"]);
        assert_eq!(folders_for(&json!({"files": {"publish": ["reports"]}}), &dir), vec!["reports"]);
        std::fs::remove_dir_all(dir).ok();
    }

    async fn hub(statuses: Vec<u16>) -> (String, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = std::sync::Arc::new(std::sync::Mutex::new(vec![]));
        let log_ = seen.clone();
        tokio::spawn(async move {
            for status in statuses {
                let Ok((mut sock, _)) = listener.accept().await else { return };
                let mut got = vec![];
                let mut buf = vec![0u8; 65536];
                loop {
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    let done = text.find("\r\n\r\n").is_some_and(|h| {
                        let length = text[..h].lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                        got.len() >= h + 4 + length
                    });
                    if n == 0 || done {
                        break;
                    }
                }
                log_.lock().unwrap().push(String::from_utf8_lossy(&got).to_string());
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}");
                let _ = sock.write_all(reply.as_bytes()).await;
            }
        });
        (url, seen)
    }

    #[tokio::test]
    async fn uploads_send_the_bytes_under_the_outbox_key_and_a_refused_one_is_reported_not_synced() {
        let dir = checkout();
        let state = State::open(&dir.join("state")).unwrap();
        std::fs::write(dir.join("reports/a.md"), "# A").unwrap();
        prepare(&state, "ops", "att-1", &dir, &json!({}), false, &[]).unwrap();
        let (url, seen) = hub(vec![200]).await;
        let client = Client::new(&url, "t", 5, 0).unwrap();
        assert_eq!(drain(&state, &client).await, 1);
        let request = seen.lock().unwrap()[0].clone();
        assert!(request.starts_with("POST /api/v2/files/uploads?bot=ops&attempt=att-1&path=reports%2Fa.md&name=a.md HTTP/1.1"), "{request}");
        assert!(request.to_lowercase().contains("content-type: application/octet-stream"));
        assert!(request.to_lowercase().contains(&format!("idempotency-key: file:ops:{}", &sha256_hex(b"reports/a.md")[..16])));
        assert!(request.ends_with("\r\n\r\n# A"));
        assert_eq!(prepare(&state, "ops", "att-2", &dir, &json!({}), false, &[]).unwrap().0, 0, "published, so not queued again");

        std::fs::write(dir.join("reports/b.md"), "# B").unwrap();
        prepare(&state, "ops", "att-3", &dir, &json!({}), false, &[]).unwrap();
        let (url, seen) = hub(vec![413, 200]).await;
        let client = Client::new(&url, "t", 5, 0).unwrap();
        assert_eq!(drain(&state, &client).await, 0);
        let seen = seen.lock().unwrap().clone();
        assert_eq!(seen.len(), 2);
        assert!(seen[1].contains("&sync=failed HTTP/1.1"), "{}", seen[1]);
        assert!(seen[1].to_lowercase().contains(":not-synced"));
        let phase: String = state.with(|c| c.query_row("SELECT phase FROM file_outbox WHERE rel='reports/b.md'", [], |r| r.get(0))).unwrap();
        assert_eq!(phase, "failed");
        std::fs::remove_dir_all(dir).ok();
    }
}
