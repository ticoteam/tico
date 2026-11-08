//! Git the runner runs for checkouts, mirrors and worktrees (`Repositories.run_git`, `worktrees.git`,
//! `task_sessions._git` in the Python runner): each command in its own process group, so a timeout or the
//! computer's shutdown takes its credential helper and ssh with it, never a half-killed parent.

use std::collections::HashSet;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use sha2::{Digest, Sha256};

use crate::env::Env;

static STOPPING: AtomicBool = AtomicBool::new(false);
static RUNNING: Mutex<Option<HashSet<i32>>> = Mutex::new(None);

pub const STOPPED: &str = "Repository sync interrupted by computer shutdown";

/// What one git command did.
#[derive(Debug, Clone, Default)]
pub struct Out {
    pub code: i32,
    /// Lossy text: invalid bytes read as U+FFFD, so a name read from it may not be the name on disk.
    pub stdout: String,
    /// The exact bytes git wrote. Read names from these, never from `stdout`.
    pub raw: Vec<u8>,
    pub stderr: String,
}

impl Out {
    pub fn ok(&self) -> bool {
        self.code == 0
    }
}

/// Why a command has no exit status: it ran past its time, the computer is shutting down, or it never started.
#[derive(Debug, Clone)]
pub enum Fault {
    Timeout(u64),
    Stopped,
    Io(String),
}

impl std::fmt::Display for Fault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Fault::Timeout(s) => write!(f, "git timed out after {s} seconds"),
            Fault::Stopped => f.write_str(STOPPED),
            Fault::Io(e) => f.write_str(e),
        }
    }
}

pub type Ran = Result<Out, Fault>;

fn group_signal(pgid: i32, signal: i32) {
    // SAFETY: a plain signal to a process group this runner started.
    unsafe {
        libc::killpg(pgid, signal);
    }
}

/// Stop every git command still running and refuse new ones (the runner's shutdown; `Repositories.close`).
pub fn stop_all() {
    STOPPING.store(true, Ordering::SeqCst);
    let groups: Vec<i32> = RUNNING.lock().unwrap().as_ref().map(|s| s.iter().copied().collect()).unwrap_or_default();
    for pgid in &groups {
        group_signal(*pgid, libc::SIGTERM);
    }
    if !groups.is_empty() {
        std::thread::sleep(Duration::from_millis(300));
        for pgid in &groups {
            // The parent may have exited while a credential helper kept running.
            group_signal(*pgid, libc::SIGKILL);
        }
    }
}

pub fn stopping() -> bool {
    STOPPING.load(Ordering::SeqCst)
}

/// Run `argv` with exactly `env`, from `cwd` (else the empty hooks folder), for at most `timeout_s`.
pub async fn run(argv: &[String], env: &Env, cwd: Option<&Path>, timeout_s: u64) -> Ran {
    run_as(argv, env, cwd, timeout_s, false).await
}

/// Checkout commands run as the bot; authenticated mirrors and updater commands keep the supervisor.
pub async fn run_as_bot(argv: &[String], env: &Env, cwd: Option<&Path>, timeout_s: u64) -> Ran {
    run_as(argv, env, cwd, timeout_s, true).await
}

async fn run_as(argv: &[String], env: &Env, cwd: Option<&Path>, timeout_s: u64, bot: bool) -> Ran {
    let began = std::time::Instant::now();
    let ran = run_once(argv, env, cwd, timeout_s, bot).await;
    let fault = match &ran {
        Ok(_) => None,
        Err(Fault::Timeout(_)) => Some("timeout"),
        Err(Fault::Stopped) => Some("stopped"),
        Err(Fault::Io(_)) => Some("io"),
    };
    crate::flight::git(argv, began.elapsed().as_millis() as u64, ran.as_ref().ok().map(|o| o.code), fault);
    ran
}

async fn run_once(argv: &[String], env: &Env, cwd: Option<&Path>, timeout_s: u64, bot: bool) -> Ran {
    if stopping() {
        return Err(Fault::Stopped);
    }
    let mut cmd = if bot { crate::isolation::command(&argv[0]) } else { tokio::process::Command::new(&argv[0]) };
    cmd.args(&argv[1..])
        .env_clear()
        .envs(env)
        .current_dir(cwd.unwrap_or_else(|| crate::safegit::hooks_dir()))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .process_group(0)
        .kill_on_drop(true);
    let child = cmd.spawn().map_err(|e| Fault::Io(format!("{}: {e}", argv[0])))?;
    let pgid = child.id().map(|p| p as i32).unwrap_or(0);
    if pgid > 0 {
        RUNNING.lock().unwrap().get_or_insert_with(HashSet::new).insert(pgid);
    }
    let waited = tokio::time::timeout(Duration::from_secs(timeout_s), child.wait_with_output()).await;
    if pgid > 0 {
        if let Some(s) = RUNNING.lock().unwrap().as_mut() {
            s.remove(&pgid);
        }
    }
    match waited {
        Ok(Ok(out)) => {
            if stopping() && out.status.code().is_none() {
                return Err(Fault::Stopped);
            }
            let raw = out.stdout;
            Ok(Out {
                code: out.status.code().unwrap_or(-1),
                stdout: String::from_utf8_lossy(&raw).into_owned(),
                raw,
                stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
            })
        }
        Ok(Err(e)) => Err(Fault::Io(e.to_string())),
        Err(_) => {
            if pgid > 0 {
                group_signal(pgid, libc::SIGTERM);
                tokio::time::sleep(Duration::from_millis(200)).await;
                group_signal(pgid, libc::SIGKILL);
            }
            Err(Fault::Timeout(timeout_s))
        }
    }
}

/// Runner git in a checkout: `git <safegit prefix> -C path <args>` (safe_git.prefix).
pub async fn git(path: &Path, env: &Env, args: &[&str], timeout_s: u64) -> Ran {
    let mut argv = vec!["git".to_string()];
    argv.extend(crate::safegit::args(path));
    argv.extend(args.iter().map(|a| a.to_string()));
    run(&argv, env, None, timeout_s).await
}

pub async fn git_as_bot(path: &Path, env: &Env, args: &[&str], timeout_s: u64) -> Ran {
    let mut argv = vec!["git".to_string()];
    argv.extend(crate::safegit::args(path));
    argv.extend(args.iter().map(|a| a.to_string()));
    run_as_bot(&argv, env, None, timeout_s).await
}

/// Git outside any checkout with the safegit prefix (safe_git.PREFIX): clones, `check-ref-format`.
pub async fn bare(env: &Env, args: &[&str], timeout_s: u64) -> Ran {
    let mut argv = vec!["git".to_string()];
    argv.extend(crate::safegit::prefix());
    argv.extend(args.iter().map(|a| a.to_string()));
    run(&argv, env, None, timeout_s).await
}

pub async fn bare_as_bot(env: &Env, args: &[&str], timeout_s: u64) -> Ran {
    let mut argv = vec!["git".to_string()];
    argv.extend(crate::safegit::prefix());
    argv.extend(args.iter().map(|a| a.to_string()));
    run_as_bot(&argv, env, None, timeout_s).await
}

pub fn sha256_hex(data: &[u8]) -> String {
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

/// JSON as Python's `json.dumps(value, sort_keys=True, separators=(",", ":"))` writes it (ASCII-escaped).
pub fn compact_sorted(v: &serde_json::Value) -> String {
    use serde_json::Value;
    fn string(s: &str, out: &mut String) {
        out.push('"');
        for ch in s.chars() {
            match ch {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
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
            Value::Object(m) => {
                let mut keys: Vec<&String> = m.keys().collect();
                keys.sort();
                out.push('{');
                for (i, k) in keys.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    string(k, out);
                    out.push(':');
                    write(&m[*k], out);
                }
                out.push('}');
            }
            Value::Array(a) => {
                out.push('[');
                for (i, item) in a.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    write(item, out);
                }
                out.push(']');
            }
            Value::String(s) => string(s, out),
            other => out.push_str(&other.to_string()),
        }
    }
    let mut out = String::new();
    write(v, &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_timed_out_command_takes_its_whole_group_with_it() {
        let mut env = Env::new();
        env.insert("PATH".into(), "/usr/bin:/bin".into());
        let marker = std::env::temp_dir().join(format!("tico-gitrun-{}", uuid::Uuid::new_v4().simple()));
        // The child of the shell outlives a plain kill of its parent; the group kill reaches it.
        let script = format!("(sleep 2; touch {}) & sleep 30", marker.display());
        let argv: Vec<String> = vec!["/bin/sh".into(), "-c".into(), script];
        assert!(matches!(run(&argv, &env, None, 1).await, Err(Fault::Timeout(1))));
        tokio::time::sleep(Duration::from_millis(2500)).await;
        assert!(!marker.exists());
    }

    #[test]
    fn markers_print_like_pythons_sorted_compact_json() {
        let v = serde_json::json!({"schema": 1, "b": "é", "a": null});
        assert_eq!(compact_sorted(&v), r#"{"a":null,"b":"\u00e9","schema":1}"#);
    }
}
