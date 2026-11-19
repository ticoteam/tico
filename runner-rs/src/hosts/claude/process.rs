//! One long-lived Claude Code process that takes turn after turn on stdin, as T3Code and Orca keep one: a voice
//! call's resident today, a chat thread's later. It owns the child, its stdin writer and its stream-json reader, so
//! nothing else writes to the session or reads its output. Each user message is a turn: `send_turn` writes it,
//! `next_event` reads the frames that answer it until one `ends_turn`, and `take_usage` hands over what that turn
//! cost. What a caller makes of the frames (a call's spoken text, a chat's messages) stays with the caller.

use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::oneshot;

use super::{
    failed, interrupt_line, request_usage, user_line, ClaudeHost, HostError, Usage, STOP_TIMEOUT,
};

/// How long `stop` waits for the process to exit after its signal.
const STOP_WAIT: Duration = Duration::from_secs(2);

type Exit = std::io::Result<std::process::ExitStatus>;

/// One turn's usage, taken when the turn ends (or the process is stopped mid-turn).
pub(crate) struct TurnUsage {
    /// The main context's size after the turn's last request of the bot's own.
    pub(crate) context_tokens: i64,
    /// The turn's token event, as a Meter adds it.
    pub(crate) tokens: Value,
}

/// Whether `frame` is the bot's own `result`, which ends the turn (a subagent's names its parent tool call).
pub(crate) fn ends_turn(frame: &Value) -> bool {
    frame["type"] == "result" && frame["parent_tool_use_id"].is_null()
}

pub(crate) struct ClaudeProcess {
    host: ClaudeHost,
    thread_id: String,
    effort: Option<String>,
    /// The turn-sweep mark everything the process starts carries (crate::turn_sweep::MARK_ENV).
    mark: String,
    /// What the process is, in its errors ("voice session").
    label: &'static str,
    stdin: tokio::process::ChildStdin,
    stdout: tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    stopper: super::super::Stopper,
    exited: oneshot::Receiver<Exit>,
    /// The process has exited and `exited` has given its status.
    gone: bool,
    usage: Usage,
}

/// A started process's own parts, which `restart` swaps for a new one's.
struct Parts {
    stdin: tokio::process::ChildStdin,
    stdout: tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    stopper: super::super::Stopper,
    exited: oneshot::Receiver<Exit>,
}

impl ClaudeProcess {
    /// Starts Claude on host thread `thread_id`'s own session (resumed when the thread has started) with the thread's
    /// settings, without background waits. Nothing is written yet: the first `send_turn` may come at once or later.
    pub(crate) fn spawn(
        host: &ClaudeHost,
        thread_id: &str,
        effort: Option<&str>,
        mark: &str,
        label: &'static str,
    ) -> Result<ClaudeProcess, HostError> {
        let parts = open(host, thread_id, thread_id, effort, mark, label)?;
        Ok(ClaudeProcess {
            host: host.clone(),
            thread_id: thread_id.into(),
            effort: effort.map(str::to_owned),
            mark: mark.into(),
            label,
            stdin: parts.stdin,
            stdout: parts.stdout,
            stopper: parts.stopper,
            exited: parts.exited,
            gone: false,
            usage: Usage::default(),
        })
    }

    /// Writes one user message: a new turn, or, while one runs, the next one Claude reads once it ends.
    pub(crate) async fn send_turn(&mut self, text: &str) -> Result<(), HostError> {
        self.write(&user_line(&uuid::Uuid::new_v4().to_string(), text, None))
            .await
    }

    /// Asks Claude to stop the running turn (an `interrupt` control request); its `result` still ends it.
    pub(crate) async fn cancel_turn(&mut self) -> Result<(), HostError> {
        self.write(&interrupt_line(&uuid::Uuid::new_v4().to_string()))
            .await
    }

    async fn write(&mut self, line: &str) -> Result<(), HostError> {
        self.stdin
            .write_all(line.as_bytes())
            .await
            .map_err(|e| failed(e.to_string()))
    }

    /// The next stream-json frame, its usage counted to the running turn. Cancel-safe: a frame half read when the
    /// future is dropped is read whole by the next call. An error once stdout closes.
    pub(crate) async fn next_event(&mut self) -> Result<Value, HostError> {
        while let Some(line) = self
            .stdout
            .next_line()
            .await
            .map_err(|e| failed(e.to_string()))?
        {
            if let Ok(value) = serde_json::from_str::<Value>(&line) {
                if let Some((id, tokens)) = request_usage(&value) {
                    self.usage.record_request(&value, id, tokens);
                }
                if ends_turn(&value) {
                    self.usage.add_result_counts(&value);
                }
                return Ok(value);
            }
        }
        Err(failed(format!("Claude closed the {}", self.label)))
    }

    /// What the turn so far used, and a fresh count for the next one.
    pub(crate) fn take_usage(&mut self) -> TurnUsage {
        let mut u = std::mem::take(&mut self.usage);
        if u.input == 0 && u.output == 0 {
            // When a process is stopped before its result, the requests it reported still cost tokens. Their writes'
            // TTL is not reported, so the turn's split is unknown.
            for (_, r, _) in &u.requests {
                u.input += r[0] + r[1];
                u.cached += r[1];
                u.cache_write += r[3];
                u.cache_write_unsplit |= r[3] > 0;
                u.output += r[2];
            }
        }
        TurnUsage {
            context_tokens: u.context_tokens(),
            tokens: u.token_event(),
        }
    }

    /// Stops the process (SIGTERM to its group, SIGKILL after `grace`) and waits up to two seconds for it to exit.
    pub(crate) async fn stop(&mut self, grace: Duration) -> Result<(), HostError> {
        self.stopper.terminate(grace);
        if self.gone {
            return Ok(());
        }
        let label = self.label;
        tokio::time::timeout(STOP_WAIT, &mut self.exited)
            .await
            .map_err(|_| failed(format!("Claude did not stop for the {label} restart")))?
            .map_err(|e| failed(e.to_string()))?
            .map_err(|e| failed(e.to_string()))?;
        self.gone = true;
        Ok(())
    }

    /// Replaces the process with a new one on a new session, with the same thread settings, effort and mark, and
    /// a new usage count. `recap`, when given, is its first turn. A process still running is ended as on `kill`.
    pub(crate) async fn restart(&mut self, recap: Option<&str>) -> Result<(), HostError> {
        let session = uuid::Uuid::new_v4().to_string();
        let effort = self.effort.clone();
        let parts = open(
            &self.host,
            &self.thread_id,
            &session,
            effort.as_deref(),
            &self.mark,
            self.label,
        )?;
        self.stopper.terminate(STOP_TIMEOUT);
        self.stdin = parts.stdin;
        self.stdout = parts.stdout;
        self.stopper = parts.stopper;
        self.exited = parts.exited;
        self.gone = false;
        self.usage = Usage::default();
        if let Some(recap) = recap {
            self.send_turn(recap).await?;
        }
        Ok(())
    }

    /// Whether the process is still running.
    #[allow(dead_code)] // the resident chat caller checks a held process before each turn
    pub(crate) fn is_alive(&mut self) -> bool {
        if !self.gone {
            self.gone = !matches!(
                self.exited.try_recv(),
                Err(oneshot::error::TryRecvError::Empty)
            );
        }
        !self.gone
    }

    /// Ends the process and what it started: SIGTERM to its group now, SIGKILL after STOP_TIMEOUT. Dropping it
    /// does the same.
    #[allow(dead_code)] // the resident chat caller ends a session on a stop
    pub(crate) fn kill(self) {}
}

impl Drop for ClaudeProcess {
    fn drop(&mut self) {
        self.stopper.terminate(STOP_TIMEOUT);
    }
}

/// Starts Claude on `session_id`: the thread's own session when it is `thread_id`, else a new one by that id.
fn open(
    host: &ClaudeHost,
    thread_id: &str,
    session_id: &str,
    effort: Option<&str>,
    mark: &str,
    label: &str,
) -> Result<Parts, HostError> {
    let mut thread = host
        .inner()
        .threads
        .get(thread_id)
        .cloned()
        .ok_or_else(|| failed(format!("Unknown Claude {label}")))?;
    thread.settings.background_wait = None;
    if session_id != thread_id {
        thread.started = false;
        thread.fork_from = None;
        thread.session_id = None;
    }
    let (mut child, _stderr, _) = host
        .launch(session_id, &thread, effort, mark)
        .map_err(|e| failed(format!("Could not open Claude {label}: {e}")))?;
    let stdin = child.stdin.take().expect("Claude stdin is piped");
    let stdout = BufReader::new(child.stdout.take().expect("Claude stdout is piped")).lines();
    let (stopper, exited) = super::super::own(child);
    Ok(Parts {
        stdin,
        stdout,
        stopper,
        exited,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hosts::Settings;

    #[tokio::test]
    async fn a_restart_opens_a_new_session_that_takes_the_recap_first_and_a_stopped_process_is_not_alive(
    ) {
        let dir = std::env::temp_dir().join(format!("claude-process-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("claude");
        crate::util::write_script(
            &path,
            r#"#!/bin/sh
case "$1" in --version) echo '2.1.296 (Claude Code)'; exit 0;; esac
printf '%s\n' "$*" >> launches
while IFS= read -r line; do
  case "$line" in
    *Recap*) printf '%s\n' '{"type":"result","result":"recapped"}';;
    *) printf '%s\n' '{"type":"result","parent_tool_use_id":"child","result":"sub","usage":{"input_tokens":50}}' '{"type":"result","result":"one","usage":{"input_tokens":3,"output_tokens":1}}';;
  esac
done
"#,
        );
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let host = ClaudeHost::new("acme", vec![path.display().to_string()], tx);
        host.start().await.unwrap();
        let thread = host.start_thread(&Settings {
            cwd: dir.display().to_string(),
            ..Default::default()
        });
        let mut process =
            ClaudeProcess::spawn(&host, &thread, None, "process-test", "test session").unwrap();
        tokio::time::timeout(Duration::from_secs(10), async {
            process.send_turn("Hello").await.unwrap();
            assert!(!ends_turn(&process.next_event().await.unwrap()));
            let result = process.next_event().await.unwrap();
            assert!(ends_turn(&result));
            assert_eq!(process.take_usage().tokens["input"], 3);
            assert!(process.is_alive());
            process.stop(Duration::from_millis(200)).await.unwrap();
            assert!(!process.is_alive());
            process.restart(Some("Recap")).await.unwrap();
            assert!(process.is_alive());
            assert_eq!(process.next_event().await.unwrap()["result"], "recapped");
        })
        .await
        .expect("the process did not answer each turn");
        process.kill();
        let launches = std::fs::read_to_string(dir.join("launches")).unwrap();
        let sessions: Vec<_> = launches
            .lines()
            .map(|argv| argv.split("--session-id ").nth(1).unwrap().to_owned())
            .collect();
        assert_eq!(sessions.len(), 2);
        assert_eq!(sessions[0], thread);
        assert_ne!(sessions[1], thread);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
