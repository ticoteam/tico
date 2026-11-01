//! One session per bot conversation and per task: a turn resumes the thread this computer saved for it, and the
//! harness's own auto-compaction (`compact_at`) is the only size limit. A new session starts only when the saved one
//! cannot be found or cannot continue, and the run says so and why (`session` in its result, backend/models.py).

use std::time::Duration;

use serde_json::{json, Map, Value};

/// Why a turn started a new session instead of resuming one (`session.why`).
pub const NONE_SAVED: &str = "none_saved";
pub const NOT_FOUND: &str = "not_found";
pub const RESUME_FAILED: &str = "resume_failed";
pub const COMPACT_FAILED: &str = "compact_failed";
pub const INTERRUPTED: &str = "interrupted";
pub const FALLBACK_SIDE: &str = "fallback_side";
/// A resumed session that was compacted first (`config.compact_first`): `kind` stays `resumed`.
pub const COMPACTED: &str = "compacted";

/// How long a refused resume waits before its one retry (an app server that has just been started, a session file
/// still being closed by the process that held it).
pub fn retry_pause() -> Duration {
    #[cfg(test)]
    return Duration::from_millis(10);
    #[cfg(not(test))]
    Duration::from_secs(2)
}

/// Whether the bot keeps nothing from one task to the next (`stateless` in its config, set by the server:
/// backend/hubdb.py stateless). Its turns resume no session of another task or of the chat, read and keep no memory/
/// or state.md, and copy no harness memory into its repository.
pub fn stateless(config: &Value) -> bool {
    crate::util::truthy(config.get("stateless"))
}

/// Whether this turn is a nightly memory tidy pass (`memory_pass` in its config, set by backend/check_models.py apply).
/// It reads its memory like any turn, but commits and pushes none of it: what it changed in its checkout is discarded
/// once the turn ends (sessions::discard_pass), so no commit of its own reaches the bot's main branch.
pub fn memory_pass(config: &Value) -> bool {
    crate::util::truthy(config.get("memory_pass"))
}

/// How a turn's session began.
#[derive(Clone, Debug, PartialEq)]
pub struct Session {
    pub resumed: bool,
    pub why: Option<&'static str>,
    pub detail: Option<String>,
}

impl Session {
    pub fn resumed() -> Session {
        Session { resumed: true, why: None, detail: None }
    }

    /// Resumed after `compact_first` compacted it.
    pub fn compacted() -> Session {
        Session { resumed: true, why: Some(COMPACTED), detail: None }
    }

    pub fn new(why: &'static str, detail: Option<String>) -> Session {
        Session { resumed: false, why: Some(why), detail: detail.map(|d| trim(&d)).filter(|d| !d.is_empty()) }
    }

    /// What the run's result carries (`session`, backend/models.py RunSession).
    pub fn report(&self) -> Value {
        json!({"kind": if self.resumed { "resumed" } else { "new" }, "why": self.why, "detail": self.detail})
    }

    /// The run's diagnostic line for a new session that is not simply the first one; None otherwise.
    pub fn diagnostic(&self) -> Option<String> {
        let detail = self.detail.as_deref().map(|d| format!(" ({d})")).unwrap_or_default();
        match self.why? {
            _ if self.resumed => None,
            NOT_FOUND => Some("Started a new session: the saved one is not on this computer.".into()),
            RESUME_FAILED => Some(format!("Started a new session: the saved one could not be resumed{detail}.")),
            COMPACT_FAILED => Some(format!("Started a new session: the saved one could not be compacted{detail}.")),
            INTERRUPTED => Some(format!("Started a new session: the saved one could not continue after an interruption{detail}.")),
            FALLBACK_SIDE => Some("Ran in a side session; this conversation's own session is kept for its next turn.".into()),
            _ => None,
        }
    }

    /// Whether a saved session was left behind: the new one is told what the room said since it, and has seen
    /// nothing older.
    pub fn replaces(&self) -> bool {
        !self.resumed && matches!(self.why, Some(NOT_FOUND | RESUME_FAILED | COMPACT_FAILED | INTERRUPTED))
    }

    /// Told to the new thread, after the rest of its prompt (`checkpoint`: the prompt gives the task's; `stateless`: the
    /// bot reads no memory or state, so it is pointed at the task alone).
    pub fn prompt_note(&self, checkpoint: bool, stateless: bool) -> Option<String> {
        if !self.replaces() {
            return None;
        }
        let read = if stateless { "Read the task" } else { "Read state.md and memory/" };
        let because = match self.why {
            Some(NOT_FOUND) => "was not on this computer",
            Some(COMPACT_FAILED) => "could not be compacted",
            Some(INTERRUPTED) => "could not continue after an interruption",
            _ => "could not be resumed",
        };
        let seed = if checkpoint {
            "this prompt gives where the task stood after its last turn and the conversation since that turn"
        } else {
            "this prompt gives only the conversation since your last turn, your own replies included"
        };
        Some(format!(
            "New session: your previous session here {because}, so this turn starts a new one without its transcript; {seed}. \
             {read} before acting; `hub task show` and `hub conversation show` have anything older."
        ))
    }
}

/// Told to a resumed session whose last turn here was interrupted (a restart or a Stop): it may have acted.
pub const AFTER_INTERRUPT: &str = "Your previous turn in this session was interrupted before it finished (a restart or a Stop), and it may \
    already have done part of its work. Check what it did (`git status`, `git log`, `hub task show`) before repeating any step.";

/// Told to a resumed session after a turn on another harness ran in a side session: that turn's replies and work
/// are not in this transcript.
pub const AFTER_SIDE: &str = "Since your last turn here, a turn ran in a separate side session (a fallback harness), and its replies are in \
    the conversation below. Check what it did (`git log`, `hub task show`) before relying on your own picture of the work.";

/// That the turn began a new session or resumed one, and why, for a server that takes it (`run_session`; an older
/// one refuses the field, and a refused result would never arrive).
pub fn session_field(completion: &mut Map<String, Value>, session: Option<&Session>, feature: impl Fn(&str) -> bool) {
    if let Some(s) = session.filter(|_| feature("run_session")) {
        completion.insert("session".into(), s.report());
    }
}

/// Whether a host's refusal to resume says the thread is not on this computer (as opposed to a fault that may pass).
pub fn missing(error: &str) -> bool {
    let e = error.to_lowercase();
    ["no rollout found", "no conversation found", "thread not found", "session not found", "no such session", "no such thread", "unknown thread", "invalid session identifier", "does not exist"]
        .iter()
        .any(|p| e.contains(p))
}

/// A refusal trimmed for the run's record.
pub fn trim(text: &str) -> String {
    let one: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if one.chars().count() > 200 { format!("{}...", one.chars().take(200).collect::<String>()) } else { one }
}

/// The key under which the state remembers that a side session ran after this primary thread's last turn.
pub fn side_key(thread: &str) -> String {
    format!("side:{thread}")
}

/// The key under which the state remembers that this thread's last turn was interrupted.
pub fn interrupted_key(thread: &str) -> String {
    format!("interrupted:{thread}")
}

/// The key under which the state keeps the digest of the standing lines this thread holds (crate::prompt::Built);
/// empty while it may not hold them.
pub fn standing_key(thread: &str) -> String {
    format!("standing:{thread}")
}

/// The key under which the state keeps the id and digest of the task this thread holds, the same way.
pub fn task_key(thread: &str) -> String {
    format!("standing-task:{thread}")
}

/// The key under which the state keeps the size of this thread's context after its last completed turn.
pub fn context_key(thread: &str) -> String {
    format!("context:{thread}")
}

/// Whether a session whose last turn ended at `context` tokens has clear room before it compacts at `compact_at`
/// (under 60% of it) with a prompt of `prompt` characters added, at four to a token. Unknown is no room.
pub fn headroom(context: Option<i64>, prompt: usize, compact_at: &Value) -> bool {
    let prompt = (prompt / 4) as i64;
    context.is_some_and(|c| c > 0 && (c + prompt) * 10 < crate::hosts::compact_at(compact_at) * 6)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_session_reports_its_kind_and_why() {
        assert_eq!(Session::resumed().report(), json!({"kind": "resumed", "why": null, "detail": null}));
        assert_eq!(Session::compacted().report(), json!({"kind": "resumed", "why": "compacted", "detail": null}));
        let s = Session::new(RESUME_FAILED, Some("  app server\nexited ".into()));
        assert_eq!(s.report(), json!({"kind": "new", "why": "resume_failed", "detail": "app server exited"}));
        assert!(s.diagnostic().unwrap().contains("could not be resumed (app server exited)"));
        assert_eq!(Session::new(NONE_SAVED, None).diagnostic(), None, "a first session needs no explanation");
        assert_eq!(Session::resumed().diagnostic(), None);
        assert!(Session::new(FALLBACK_SIDE, None).diagnostic().is_some());
    }

    #[test]
    fn only_a_session_that_left_one_behind_gets_the_new_session_note() {
        let n = Session::new(NOT_FOUND, None).prompt_note(true, false).unwrap();
        assert!(n.starts_with("New session:") && n.contains("was not on this computer") && n.contains("where the task stood"), "{n}");
        assert!(n.contains("Read state.md and memory/ before acting"), "{n}");
        assert!(Session::new(COMPACT_FAILED, None).prompt_note(false, false).unwrap().contains("could not be compacted"));
        for s in [Session::new(NONE_SAVED, None), Session::new(FALLBACK_SIDE, None), Session::resumed(), Session::compacted()] {
            assert_eq!(s.prompt_note(true, false), None, "{s:?}");
        }
    }

    #[test]
    fn a_stateless_bot_is_never_told_to_read_memory_or_state() {
        let n = Session::new(NOT_FOUND, None).prompt_note(true, true).unwrap();
        assert!(n.contains("Read the task before acting") && !n.contains("memory") && !n.contains("state.md"), "{n}");
        assert!(stateless(&json!({"stateless": true})));
        assert!(!stateless(&json!({"stateless": false})) && !stateless(&json!({"harness": "claude"})));
    }

    #[test]
    fn only_a_memory_pass_turn_is_marked_as_one() {
        assert!(memory_pass(&json!({"memory_pass": true})));
        assert!(!memory_pass(&json!({"memory_pass": false})) && !memory_pass(&json!({"harness": "claude"})));
    }

    #[test]
    fn only_a_session_well_under_its_compact_at_has_room_for_a_short_prompt() {
        assert!(headroom(Some(100_000), 0, &Value::Null), "under 60% of the 250k default");
        assert!(!headroom(Some(150_000), 0, &Value::Null), "at 60%");
        assert!(!headroom(Some(220_000), 0, &Value::Null));
        assert!(!headroom(Some(40_000), 0, &json!(60_000)), "a bot's own Compact at");
        assert!(headroom(Some(40_000), 0, &json!(100_000)));
        assert!(!headroom(Some(100_000), 200_000, &Value::Null), "the prompt counts too: 100k + 50k is 60%");
        assert!(headroom(Some(100_000), 100_000, &Value::Null));
        assert!(!headroom(None, 0, &Value::Null) && !headroom(Some(0), 0, &Value::Null), "unknown usage is no room");
    }

    #[test]
    fn long_refusals_are_trimmed_and_missing_threads_are_told_from_faults() {
        assert!(trim(&"x".repeat(500)).chars().count() <= 203);
        assert!(missing("no rollout found for thread id 019f"));
        assert!(missing("No conversation found with session ID: 5b1"));
        assert!(!missing("codex app-server exited"));
    }

    #[test]
    fn the_session_is_sent_only_to_a_server_that_takes_it() {
        let s = Session::new(NOT_FOUND, None);
        let mut sent = Map::new();
        session_field(&mut sent, Some(&s), |_| false);
        assert!(sent.is_empty());
        session_field(&mut sent, None, |_| true);
        assert!(sent.is_empty());
        session_field(&mut sent, Some(&s), |name| name == "run_session");
        assert_eq!(sent["session"], s.report());
    }
}
