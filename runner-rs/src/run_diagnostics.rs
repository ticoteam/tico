//! Always-on bounded harness stderr. Only scrubbed snapshots enter the per-attempt outbox.
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::json;
use tokio::io::AsyncReadExt;
use tokio::process::Child;

use crate::hosts::Events;
use crate::redact::{self, Redactor};

const BYTES: usize = 64 * 1024;
const LINES: usize = 200;
const LINE_BYTES: usize = 8192;

fn safe(text: &str, scrub: &Redactor) -> String {
    static SHAPES: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let shapes = SHAPES.get_or_init(|| regex::Regex::new(r"(?i)(?:bearer|basic)\s+\S+|\b(?:sk|pk|xai|gsk|ghp|github_pat)[-_][\w-]{6,}|\b[A-Za-z0-9_+=-]{32,}\b|(?:password|token|api[_ -]?key|secret)\s*[:=]\s*\S+").unwrap());
    shapes.replace_all(&redact::scrub_credentials(&scrub.scrub_text(text), &[]), "[redacted]").into_owned()
}

#[derive(Clone, Default)]
pub struct Stderr(Arc<Mutex<VecDeque<String>>>, Arc<std::sync::atomic::AtomicBool>, Arc<tokio::sync::Notify>, Arc<tokio::sync::Notify>);

impl Stderr {
    pub fn capture(child: &mut Child, tx: Events, env: &crate::env::Env) -> Self {
        Self::capture_with(child, move |value| { let _ = tx.send(value); }, env)
    }

    pub fn capture_with(child: &mut Child, send: impl Fn(serde_json::Value) + Send + 'static, env: &crate::env::Env) -> Self {
        let tail = Self::default();
        let Some(mut pipe) = child.stderr.take() else { tail.1.store(true, std::sync::atomic::Ordering::SeqCst); return tail };
        let kept = tail.clone();
        let scrub = Redactor::new(&redact::env_secrets(env));
        tokio::spawn(async move {
            let mut buf = [0u8; 4096];
            let mut pending = Vec::new();
            let mut oversized = false;
            let mut dirty = false;
            let mut tick = tokio::time::interval(Duration::from_secs(1));
            loop {
                tokio::select! {
                    read = async { tokio::select! {
                        read = pipe.read(&mut buf) => read,
                        _ = kept.3.notified() => Ok(0),
                    }} => {
                        let n = read.unwrap_or(0);
                        if n == 0 {
                            if !pending.is_empty() || oversized {
                                kept.push(if oversized { "[oversized stderr line omitted]".into() } else {
                                    safe(&String::from_utf8_lossy(&pending), &scrub)
                                });
                                dirty = true;
                            }
                            if dirty { send(json!({"kind": "diagnostic", "stderr_tail": kept.tail()})); }
                            kept.1.store(true, std::sync::atomic::Ordering::SeqCst);
                            kept.2.notify_one();
                            break;
                        }
                        for b in &buf[..n] {
                            if *b == b'\n' {
                                let line = if oversized { "[oversized stderr line omitted]".into() } else {
                                    safe(&String::from_utf8_lossy(&pending), &scrub)
                                };
                                kept.push(line);
                                pending.clear();
                                oversized = false;
                                dirty = true;
                            } else if pending.len() < LINE_BYTES && !oversized { pending.push(*b); }
                            else { pending.clear(); oversized = true; }
                        }
                    }
                    _ = tick.tick() => {
                        if dirty {
                            send(json!({"kind": "diagnostic", "stderr_tail": kept.tail()}));
                            dirty = false;
                        }
                    }
                }
            }
        });
        tail
    }

    fn push(&self, line: String) {
        let mut lines = self.0.lock().unwrap();
        lines.push_back(line);
        while lines.len() > LINES || lines.iter().map(|l| l.len() + 1).sum::<usize>() > BYTES {
            lines.pop_front();
        }
    }

    /// A reaped process may have unread stderr in its pipe. Background children can keep it open, so bound the drain.
    pub async fn drained(&self) {
        let done = self.2.notified();
        if !self.1.load(std::sync::atomic::Ordering::SeqCst) {
            if tokio::time::timeout(Duration::from_millis(100), done).await.is_err() {
                self.3.notify_one();
                let _ = tokio::time::timeout(Duration::from_millis(100), self.2.notified()).await;
            }
        }
    }

    pub fn tail(&self) -> String { self.0.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n") }
}

pub fn process_reason(code: i32, stderr: &str) -> String {
    let _ = stderr; // Stderr may quote a child tool; it cannot establish a kernel OOM kill.
    if code < 0 { format!("Process exited (signal {})", -code) }
    else { format!("Process exited (exit code {code})") }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tail_is_bounded_and_a_kill_is_not_guessed_to_be_oom() {
        let t = Stderr::default();
        for n in 0..300 { t.push(format!("line {n}")); }
        assert_eq!(t.tail().lines().count(), 200);
        assert!(t.tail().starts_with("line 100\n"));
        for _ in 0..100 { t.push("x".repeat(8000)); }
        assert!(t.tail().len() <= BYTES);
        assert_eq!(safe("/workspace/project/runner/src/hosts/claude.rs", &Redactor::new(&[])), "/workspace/project/runner/src/hosts/claude.rs");
        assert_eq!(process_reason(-15, ""), "Process exited (signal 15)");
        assert_eq!(process_reason(-9, ""), "Process exited (signal 9)");
        assert_eq!(process_reason(-9, "out of memory"), "Process exited (signal 9)");
    }

    #[tokio::test]
    async fn drain_closes_a_pipe_inherited_by_a_live_child() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let mut child = tokio::process::Command::new("sh").args(["-c", "exec 3<&0; cat <&3 >&2 & echo $!; echo before >&2"])
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped()).spawn().unwrap();
        // Keep cat's stdin open so it retains stderr after its parent exits.
        let input = child.stdin.take();
        child.wait().await.unwrap();
        let tail = Stderr::capture(&mut child, tx, &crate::env::Env::new());
        let started = std::time::Instant::now();
        tail.drained().await;
        assert!(started.elapsed() >= Duration::from_millis(90));
        assert_eq!(tail.tail(), "before");
        while tokio::time::timeout(Duration::from_secs(1), rx.recv()).await.unwrap().is_some() {}
        drop(input);
    }

    #[tokio::test]
    async fn stderr_is_drained_and_scrubbed_without_a_provider_stream() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let mut child = tokio::process::Command::new("sh").args(["-c", "printf 'failure sk-test_abcdefghijklmnop0123456789\\n' >&2; kill -TERM $$"])
            .stderr(std::process::Stdio::piped()).spawn().unwrap();
        let tail = Stderr::capture(&mut child, tx, &crate::env::Env::new());
        let code = crate::hosts::return_code(&child.wait().await);
        let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv()).await.unwrap().unwrap();
        assert!(ev["stderr_tail"].as_str().unwrap().contains("failure"));
        assert!(!tail.tail().contains("abcdefghijklmnop"));
        assert_eq!(code, -15);
    }
}
