//! Warm sessions (`removed Python runner: warm.py`): a bounded cache of persistent Antigravity processes, one per conversation
//! scope, each with a revocable credential. The process reads `HUB_TOKEN=tico-file:<home>/lease-token`, a file
//! rewritten with each turn's attempt token and blanked when the turn ends, so a process kept between turns holds
//! no live token. A process is replaced when its environment (HUB_TOKEN aside) changes or it has died, and is
//! stopped after a failed turn, after 15 idle minutes, when the cache is full, and before its harness is swapped.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::env::Env;
use crate::hosts::antigravity::AntigravityHost;
use crate::util::py_dumps;

pub const IDLE: Duration = Duration::from_secs(900);

struct Entry {
    host: AntigravityHost,
    fingerprint: String,
    token_file: PathBuf,
    busy: bool,
    used: Instant,
}

pub struct WarmSessions {
    pub directory: PathBuf,
    limit: usize,
    idle: Duration,
    entries: Mutex<HashMap<String, Entry>>,
}

/// Python's `json.dumps` (ASCII only): the scope must match the Python runner's, so both engines use one home.
fn ascii_dumps(v: &Value) -> String {
    let mut out = String::new();
    for c in py_dumps(v).chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut units = [0u16; 2];
            for u in c.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{u:04x}"));
            }
        }
    }
    out
}

fn sha256_hex(text: &str) -> String {
    ring::digest::digest(&ring::digest::SHA256, text.as_bytes()).as_ref().iter().map(|b| format!("{b:02x}")).collect()
}

/// The conversation scope of an attempt: bot, conversation, principal, session epoch, model, effort, harness.
pub fn scope(attempt: &Value) -> String {
    let config = &attempt["config"];
    let get = |v: &Value, k: &str| v.get(k).cloned().unwrap_or(Value::Null);
    let epoch = attempt.get("session_epoch").cloned().unwrap_or(json!(0));
    sha256_hex(&ascii_dumps(&json!([
        attempt["bot"],
        attempt["conversation"]["id"],
        get(attempt, "principal"),
        epoch,
        get(config, "model"),
        get(config, "reasoning_effort"),
        get(config, "harness"),
    ])))
}

/// The environment without HUB_TOKEN, hashed: a change means the process must start again.
pub fn fingerprint(env: &Env) -> String {
    let rest: serde_json::Map<String, Value> = env.iter().filter(|(k, _)| *k != "HUB_TOKEN").map(|(k, v)| (k.clone(), json!(v))).collect();
    sha256_hex(&ascii_dumps(&Value::Object(rest)))
}

pub fn write_token(path: &Path, token: &str) {
    write_token_for(path, token, None)
}

/// `write_token`, the file owned by `owner` (a leased turn user) in place of the bot user.
pub fn write_token_for(path: &Path, token: &str, owner: Option<(u32, u32)>) {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
    let written = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .and_then(|mut f| {
            f.write_all(token.as_bytes())?;
            match owner {
                // SAFETY: fchown on a descriptor this process owns.
                Some((uid, gid)) if unsafe { libc::fchown(std::os::fd::AsRawFd::as_raw_fd(&f), uid, gid) } != 0 => Err(std::io::Error::last_os_error()),
                Some(_) => Ok(()),
                None => crate::isolation::chown_file(&f),
            }
        })
        .and_then(|_| std::fs::rename(&tmp, path));
    let _ = std::fs::remove_file(&tmp);
    if let Err(e) = written {
        crate::util::log(&format!("Tico runner: could not write a warm session's token file ({e})"));
    }
}

impl WarmSessions {
    pub fn new(directory: PathBuf, limit: usize) -> WarmSessions {
        WarmSessions { directory, limit, idle: IDLE, entries: Mutex::new(HashMap::new()) }
    }

    fn entries(&self) -> std::sync::MutexGuard<'_, HashMap<String, Entry>> {
        self.entries.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// This scope's process (`make` builds one when there is none to reuse) and the environment the turn gives it,
    /// with HUB_TOKEN pointing at the token file.
    pub async fn acquire(&self, attempt: &Value, env: &Env, make: impl FnOnce(&Path, &Env) -> AntigravityHost) -> Result<(AntigravityHost, Env), String> {
        let key = scope(attempt);
        let print = fingerprint(env);
        self.prune(false).await;
        let stale = {
            let mut entries = self.entries();
            match entries.get(&key) {
                Some(e) if e.busy => return Err("Conversation already has an active turn".into()),
                Some(e) if e.fingerprint != print || !e.host.alive() => entries.remove(&key).map(|e| e.host),
                _ => None,
            }
        };
        if let Some(h) = stale {
            h.stop().await;
        }
        let home = self.directory.join(&key);
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::create_dir_all(&home).map_err(|e| format!("could not make the warm session home: {e}"))?;
            for path in [&self.directory, &home] {
                if !std::fs::symlink_metadata(path).map_err(|e| e.to_string())?.is_dir() {
                    return Err("Warm session directories must not be symbolic links".into());
                }
            }
            let mode = if crate::isolation::enabled() { 0o711 } else { 0o700 };
            // Only traversal to a known token path: neither directory is writable by the bot.
            if crate::isolation::enabled() {
                std::fs::set_permissions(&self.directory, std::fs::Permissions::from_mode(mode))
                    .map_err(|e| format!("could not prepare the warm session parent: {e}"))?;
            }
            std::fs::set_permissions(&home, std::fs::Permissions::from_mode(mode))
                .map_err(|e| format!("could not prepare the warm session home: {e}"))?;
        }
        let token_file = home.join("lease-token");
        let mut turn_env = env.clone();
        let token = turn_env.insert("HUB_TOKEN".into(), format!("tico-file:{}", token_file.display())).unwrap_or_default();
        let mut entries = self.entries();
        if entries.get(&key).is_some_and(|e| e.busy) {
            return Err("Conversation already has an active turn".into());
        }
        let entry = entries.entry(key).or_insert_with(|| Entry {
            host: make(&home, &turn_env),
            fingerprint: print,
            token_file: token_file.clone(),
            busy: false,
            used: Instant::now(),
        });
        write_token(&token_file, &token);
        entry.busy = true;
        Ok((entry.host.clone(), turn_env))
    }

    /// The turn on `host` is over: blank its token; keep the process only after a turn that completed.
    pub async fn release(&self, host: &AntigravityHost, success: bool) {
        let stop = {
            let mut entries = self.entries();
            let Some(key) = entries.iter().find(|(_, e)| e.host.same(host)).map(|(k, _)| k.clone()) else { return };
            let e = entries.get_mut(&key).unwrap();
            write_token(&e.token_file, "");
            e.busy = false;
            e.used = Instant::now();
            if !success || !host.alive() {
                entries.remove(&key).map(|e| e.host)
            } else {
                None
            }
        };
        if let Some(h) = stop {
            h.stop().await;
        }
    }

    /// Stop this scope's idle process, so its next `acquire` starts a new one. A busy one is left to its turn.
    pub async fn discard(&self, attempt: &Value) {
        let key = scope(attempt);
        let stop = {
            let mut entries = self.entries();
            match entries.get(&key) {
                Some(e) if !e.busy => entries.remove(&key).map(|e| e.host),
                _ => None,
            }
        };
        if let Some(h) = stop {
            h.stop().await;
        }
    }

    /// Stop idle processes past their time or over the limit (oldest first), or every idle one with `close`.
    pub async fn prune(&self, close: bool) {
        let stopping: Vec<AntigravityHost> = {
            let mut entries = self.entries();
            let mut idle: Vec<(String, Instant)> = entries.iter().filter(|(_, e)| !e.busy).map(|(k, e)| (k.clone(), e.used)).collect();
            idle.sort_by_key(|(_, used)| *used);
            let mut out = vec![];
            for (key, used) in idle {
                if close || entries.len() >= self.limit || used.elapsed() > self.idle {
                    let e = entries.remove(&key).unwrap();
                    write_token(&e.token_file, "");
                    out.push(e.host);
                }
            }
            out
        };
        for h in stopping {
            h.stop().await;
        }
    }

    /// Stop every idle process of the `name` harness, before its executable is replaced on disk. For harness
    /// installs, which this runner does not do yet.
    #[allow(dead_code)]
    pub async fn close_runtime(&self, name: &str) {
        if name != crate::hosts::antigravity::NAME {
            return; // the only harness kept warm
        }
        self.prune(true).await;
    }

    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.entries().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    #[test]
    fn the_scope_is_the_python_runners() {
        let attempt = json!({"bot": "ops", "conversation": {"id": "c-1"}, "config": {"model": "gemini-3.8-flash", "reasoning_effort": "low", "harness": "antigravity"}});
        // hashlib.sha256(json.dumps(["ops", "c-1", None, 0, "gemini-3.8-flash", "low", "antigravity"]).encode()).hexdigest()
        assert_eq!(scope(&attempt), "be415f67ac4890f63e0fdf64bc0f0e45b2d97753e19366cf9d43b3e888e095fa");
        assert_eq!(ascii_dumps(&json!(["é😀"])), "[\"\\u00e9\\ud83d\\ude00\"]");
    }

    #[tokio::test]
    async fn a_discarded_idle_process_is_not_the_next_turns() {
        let dir = std::env::temp_dir().join(format!("warm-discard-{}", uuid::Uuid::new_v4().simple()));
        let warm = WarmSessions::new(dir, 8);
        let attempt = json!({"bot": "tico-counsel", "conversation": {"id": "c-chat"}, "config": {"harness": "antigravity"}});
        let make = |home: &Path, _: &Env| AntigravityHost::new(home, vec!["agy".into()], mpsc::unbounded_channel().0);
        let env = Env::new();
        let (first, _) = warm.acquire(&attempt, &env, make).await.unwrap();
        first.start().unwrap();
        warm.release(&first, true).await;
        assert_eq!(warm.len(), 1, "a completed turn keeps its process");
        warm.discard(&attempt).await;
        assert_eq!(warm.len(), 0);
        let (second, _) = warm.acquire(&attempt, &env, make).await.unwrap();
        assert!(!second.same(&first), "the next turn gets a new process");
        second.start().unwrap();
        warm.release(&second, true).await;
        warm.close_runtime("antigravity").await;
    }

    #[tokio::test]
    async fn a_turn_borrows_the_token_and_a_changed_environment_gets_a_new_process() {
        let dir = std::env::temp_dir().join(format!("warm-{}", uuid::Uuid::new_v4().simple()));
        let warm = WarmSessions::new(dir.clone(), 8);
        let attempt = json!({"bot": "ops", "conversation": {"id": "c-1"}, "config": {"harness": "antigravity"}});
        let make = |home: &Path, _: &Env| AntigravityHost::new(home, vec!["agy".into()], mpsc::unbounded_channel().0);
        let mut env = Env::new();
        env.insert("HUB_TOKEN".into(), "attempt-token-1".into());
        let (host, turn_env) = warm.acquire(&attempt, &env, make).await.unwrap();
        host.start().unwrap();
        let file = dir.join(scope(&attempt)).join("lease-token");
        assert_eq!(turn_env["HUB_TOKEN"], format!("tico-file:{}", file.display()));
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "attempt-token-1");
        assert!(warm.acquire(&attempt, &env, make).await.is_err(), "one turn per conversation");
        warm.release(&host, true).await;
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "");
        // The next attempt's token only changes the file; the process stays.
        env.insert("HUB_TOKEN".into(), "attempt-token-2".into());
        let (again, _) = warm.acquire(&attempt, &env, make).await.unwrap();
        assert!(again.same(&host));
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "attempt-token-2");
        warm.release(&again, true).await;
        env.insert("ACME".into(), "new".into());
        let (other, _) = warm.acquire(&attempt, &env, make).await.unwrap();
        assert!(!other.same(&host) && !host.alive());
        // A failed turn never leaves its process behind.
        other.start().unwrap();
        warm.release(&other, false).await;
        assert_eq!(warm.len(), 0);
        let (last, _) = warm.acquire(&attempt, &env, make).await.unwrap();
        last.start().unwrap();
        warm.release(&last, true).await;
        warm.close_runtime("antigravity").await;
        assert_eq!((warm.len(), last.alive()), (0, false));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
