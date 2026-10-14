//! The commits that changed a bot's memory/ and knowledge/, reported to Tico (runner/memory_history.py;
//! docs/learnings.md). After a completed turn, and every few minutes for every bot here, the newest memory
//! commits Tico has not heard about (or whose state changed: pushed, or their run became known) go to
//! `POST bots/{bot}/memory/report`, idempotent on the server.
//!
//! A commit's run is its `Tico-Run:` trailer; for a bot that is not shared, a commit made during a turn this
//! computer ran is that turn's without one. A diff goes only once the commit is on the upstream, and
//! `learnings.md` / `decisions.md` only as the upstream has them.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::time::Duration;

use rusqlite::params;
use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::state::State;
use crate::util::log;

pub const FOLDERS: &[&str] = &["memory", "knowledge"];
pub const DOCUMENTS: &[&str] = &["memory/learnings.md", "memory/decisions.md"];
pub const MAX_COMMITS: usize = 40;
pub const MAX_DIFF: usize = 20_000;
pub const EVERY: Duration = Duration::from_secs(300);

pub const TABLES: &str = "
CREATE TABLE IF NOT EXISTS memory_reported(
  bot TEXT NOT NULL, sha TEXT NOT NULL, shared INTEGER NOT NULL, attempt TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(bot, sha));
CREATE TABLE IF NOT EXISTS memory_documents(
  bot TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(bot, path));
";

/// git's stdout as text with universal newlines (Python's `text=True`), and whether it exited 0.
async fn git(root: &Path, args: &[&str]) -> Option<(bool, String)> {
    let mut cmd = crate::isolation::command("git");
    cmd.args(crate::safegit::args(root))
        .args(args)
        .env_clear()
        .envs(crate::safegit::clean_environment(&crate::env::process_environment()))
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    let out = tokio::time::timeout(Duration::from_secs(30), cmd.output()).await.ok()?.ok()?;
    let text = String::from_utf8_lossy(&out.stdout).replace("\r\n", "\n").replace('\r', "\n");
    Some((out.status.success(), text))
}

async fn git_out(root: &Path, args: &[&str]) -> Option<String> {
    git(root, args).await.filter(|(ok, _)| *ok).map(|(_, t)| t)
}

fn cut(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

#[derive(Debug)]
struct Commit {
    sha: String,
    author: String,
    committed: String,
    subject: String,
    run: String,
}

/// The newest commits that touched memory/ or knowledge/.
async fn commits(root: &Path) -> Vec<Commit> {
    let mut args = vec![
        "log".to_string(),
        format!("-n{MAX_COMMITS}"),
        "--no-merges".into(),
        "--format=%H%x1f%an%x1f%aI%x1f%s%x1f%(trailers:key=Tico-Run,valueonly,separator=%x2c)%x1e".into(),
        "HEAD".into(),
        "--".into(),
    ];
    args.extend(FOLDERS.iter().map(|f| f.to_string()));
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    let out = git_out(root, &refs).await.unwrap_or_default();
    out.split('\x1e')
        .filter_map(|record| {
            let parts: Vec<&str> = record.trim_matches('\n').split('\x1f').collect();
            (parts.len() == 5 && parts[0].len() == 40).then(|| Commit {
                sha: parts[0].into(),
                author: cut(parts[1], 200),
                committed: parts[2].into(),
                subject: cut(parts[3], 300),
                run: cut(parts[4].trim().split(',').next().unwrap_or("").trim(), 100),
            })
        })
        .collect()
}

async fn details(root: &Path, sha: &str, with_diff: bool) -> (Vec<String>, String, bool) {
    let mut names = vec!["show", "--format=", "--name-only", sha, "--"];
    names.extend_from_slice(FOLDERS);
    let files: Vec<String> = git_out(root, &names).await.unwrap_or_default().lines().filter(|l| !l.is_empty()).map(String::from).collect();
    let diff = if with_diff {
        let mut show = vec!["show", "--format=", "--no-color", "--unified=2", sha, "--"];
        show.extend_from_slice(FOLDERS);
        git_out(root, &show).await.unwrap_or_default()
    } else {
        String::new()
    };
    let truncated = diff.chars().count() > MAX_DIFF;
    (files, cut(&diff, MAX_DIFF), truncated)
}

/// Python's `json.dumps(value, sort_keys=True)`: ASCII only, `", "` and `": "`.
pub fn dumps_sorted(v: &Value) -> String {
    fn string(s: &str, out: &mut String) {
        out.push('"');
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                c if (c as u32) < 0x20 || (c as u32) > 0x7f => {
                    let mut buf = [0u16; 2];
                    for unit in c.encode_utf16(&mut buf) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
                c => out.push(c),
            }
        }
        out.push('"');
    }
    fn write(v: &Value, out: &mut String) {
        match v {
            Value::String(s) => string(s, out),
            Value::Array(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    write(item, out);
                }
                out.push(']');
            }
            Value::Object(m) => {
                let mut keys: Vec<&String> = m.keys().collect();
                keys.sort();
                out.push('{');
                for (i, k) in keys.into_iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    string(k, out);
                    out.push_str(": ");
                    write(&m[k], out);
                }
                out.push('}');
            }
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Null => out.push_str("null"),
            Value::Number(n) => out.push_str(&n.to_string()),
        }
    }
    let mut out = String::new();
    write(v, &mut out);
    out
}

/// Send what Tico has not heard about this bot's memory. Never fails; returns how many commits went.
/// `before` is the checkout's HEAD when the turn began; `own_turn` says commits since then are that turn's.
pub async fn report(state: &State, client: &Client, bot: &str, root: &Path, attempt: &str, before: &str, own_turn: bool) -> usize {
    if !root.join(".git").exists() {
        return 0;
    }
    type Known = (HashMap<String, (bool, String)>, HashMap<String, String>);
    let known: Result<Known, String> = state.with(|c| {
        c.execute_batch(TABLES)?;
        let mut q = c.prepare("SELECT sha, shared, attempt FROM memory_reported WHERE bot=?1")?;
        let known = q.query_map([bot], |r| Ok((r.get::<_, String>(0)?, (r.get::<_, i64>(1)? != 0, r.get::<_, String>(2)?))))?.collect::<rusqlite::Result<HashMap<_, _>>>()?;
        let mut q = c.prepare("SELECT path, digest FROM memory_documents WHERE bot=?1")?;
        let docs = q.query_map([bot], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?.collect::<rusqlite::Result<HashMap<_, _>>>()?;
        Ok((known, docs))
    });
    let Ok((known, sent_docs)) = known else {
        log(&format!("Tico runner: {bot}: memory history not reported (OperationalError)"));
        return 0;
    };
    let upstream = git_out(root, &["rev-parse", "--verify", "-q", "@{u}"]).await.is_some();
    let ours: HashSet<String> = if own_turn && !before.is_empty() {
        git_out(root, &["rev-list", &format!("{before}..HEAD")]).await.unwrap_or_default().split_whitespace().map(String::from).collect()
    } else {
        HashSet::new()
    };
    let mut rows: Vec<Value> = vec![];
    for commit in commits(root).await {
        let run = if !commit.run.is_empty() {
            commit.run.clone()
        } else if ours.contains(&commit.sha) {
            attempt.to_string()
        } else {
            String::new()
        };
        let old = known.get(&commit.sha);
        // A commit already reported as shared can only gain its run: skip the git call for the rest.
        if old.is_some_and(|(shared, a)| *shared && (!a.is_empty() || run.is_empty())) {
            continue;
        }
        let shared = upstream && git(root, &["merge-base", "--is-ancestor", &commit.sha, "@{u}"]).await.is_some_and(|(ok, _)| ok);
        if old.is_some_and(|(was, a)| (*was || !shared) && (!a.is_empty() || run.is_empty())) {
            continue;
        }
        let (files, diff, truncated) = details(root, &commit.sha, shared).await;
        if files.is_empty() {
            continue;
        }
        rows.push(json!({
            "sha": commit.sha, "subject": commit.subject, "author": commit.author, "committed": commit.committed,
            "files": files.into_iter().take(200).collect::<Vec<_>>(), "diff": diff, "truncated": truncated,
            "shared": shared, "attempt": run,
        }));
    }
    let mut documents = Map::new();
    if upstream {
        for rel in DOCUMENTS {
            // The upstream's copy: what is only on this computer stays here.
            let Some(text) = git_out(root, &["show", &format!("@{{u}}:{rel}")]).await else { continue };
            if text.len() > 100_000 {
                continue;
            }
            if sent_docs.get(*rel) != Some(&crate::files_publish::sha256_hex(text.as_bytes())) {
                documents.insert(rel.to_string(), json!(text));
            }
        }
    }
    if rows.is_empty() && documents.is_empty() {
        return 0;
    }
    rows.truncate(50);
    let body = json!({"commits": rows, "documents": documents});
    let key = format!("memory:{bot}:{}", &crate::files_publish::sha256_hex(dumps_sorted(&body).as_bytes())[..32]);
    if let Err(e) = client.post(&format!("bots/{bot}/memory/report"), &body, Some(&key)).await {
        // An older server has no such route: nothing to report to, and nothing lost (git still has it).
        if e.status != 404 {
            log(&format!("Tico runner: {bot}: memory history not reported ({}); it goes with the next report", crate::runner::describe(&e)));
        }
        return 0;
    }
    let _ = state.with(|c| {
        for row in &rows {
            c.execute(
                "INSERT INTO memory_reported(bot,sha,shared,attempt) VALUES(?1,?2,?3,?4) ON CONFLICT(bot,sha) DO UPDATE SET shared=excluded.shared,attempt=excluded.attempt",
                params![bot, row["sha"].as_str(), row["shared"].as_bool().unwrap_or(false) as i64, row["attempt"].as_str()],
            )?;
        }
        for (rel, text) in &documents {
            c.execute("INSERT OR REPLACE INTO memory_documents VALUES(?1,?2,?3)", params![bot, rel, crate::files_publish::sha256_hex(text.as_str().unwrap_or("").as_bytes())])?;
        }
        Ok(())
    });
    rows.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sh(dir: &Path, script: &str) {
        let out = std::process::Command::new("/bin/sh").arg("-c").arg(script).current_dir(dir).env("GIT_CONFIG_GLOBAL", "/dev/null").output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    }

    async fn hub() -> (String, std::sync::Arc<std::sync::Mutex<Vec<String>>>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = std::sync::Arc::new(std::sync::Mutex::new(vec![]));
        let log_ = seen.clone();
        tokio::spawn(async move {
            loop {
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
                let _ = sock.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").await;
            }
        });
        (url, seen)
    }

    #[tokio::test]
    async fn memory_commits_go_once_with_their_run_and_a_diff_only_once_pushed() {
        let dir = std::env::temp_dir().join(format!("tico-memory-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("origin")).unwrap();
        sh(&dir, "git init -q --bare origin.git && git init -q -b main bot && cd bot && git config user.email b@x && git config user.name Bot && mkdir memory && echo one > memory/learnings.md && git add . && git commit -qm 'First learning' && git remote add origin ../origin.git && git push -q -u origin main");
        let root = dir.join("bot");
        let before = crate::redact::head(&root);
        sh(&root, "echo two >> memory/learnings.md && git commit -qam 'Second learning' && echo x > README.md && git add README.md && git commit -qm 'Not memory'");
        let state = State::open(&dir.join("state")).unwrap();
        let (url, seen) = hub().await;
        let client = Client::new(&url, "t", 5, 0).unwrap();
        assert_eq!(report(&state, &client, "ops", &root, "att-9", &before, true).await, 2);
        let body = |i: usize| -> Value {
            let r = seen.lock().unwrap()[i].clone();
            serde_json::from_str(&r[r.find("\r\n\r\n").unwrap() + 4..]).unwrap()
        };
        let first = body(0);
        let commits = first["commits"].as_array().unwrap();
        assert_eq!(commits[0]["subject"], "Second learning");
        assert_eq!((commits[0]["attempt"].as_str(), commits[0]["shared"].as_bool(), commits[0]["diff"].as_str()), (Some("att-9"), Some(false), Some("")));
        assert_eq!((commits[1]["attempt"].as_str(), commits[1]["shared"].as_bool()), (Some(""), Some(true)));
        assert!(commits[1]["diff"].as_str().unwrap().contains("+one"));
        assert_eq!(first["documents"], json!({"memory/learnings.md": "one\n"}));
        let request = seen.lock().unwrap()[0].clone();
        assert!(request.to_lowercase().contains(&format!("idempotency-key: memory:ops:{}", &crate::files_publish::sha256_hex(dumps_sorted(&first).as_bytes())[..32])));
        assert_eq!(report(&state, &client, "ops", &root, "", "", false).await, 0, "nothing new");
        sh(&root, "git push -q");
        assert_eq!(report(&state, &client, "ops", &root, "", "", false).await, 1, "pushed: the commit goes again with its diff");
        let again = body(1);
        assert!(again["commits"][0]["diff"].as_str().unwrap().contains("+two"));
        assert_eq!(again["documents"], json!({"memory/learnings.md": "one\ntwo\n"}));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn the_key_text_is_pythons_sorted_ascii_json() {
        assert_eq!(dumps_sorted(&json!({"b": [1, true], "a": "\u{e9}\n\u{1F600}"})), "{\"a\": \"\\u00e9\\n\\ud83d\\ude00\", \"b\": [1, true]}");
    }
}
