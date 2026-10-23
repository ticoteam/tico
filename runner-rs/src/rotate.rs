//! Session rotation (token saving): every turn on a resumed provider thread re-reads its whole transcript, so a
//! thread that has grown large makes each later turn dearer. Once a thread's newest turn read `rotate_at` input
//! tokens or more (or it has run `rotate_after_turns` turns), the next turn on it starts a fresh thread instead,
//! seeded only with what the prompt already gives a fresh one: the task's checkpoint, what the room said since the
//! old thread's last turn, and the instruction to read the bot's state.md. The run reports that it rotated and why
//! (`rotated` in its result, backend/run_triggers.py), for the token table.

use serde_json::{json, Value};

/// Input tokens of one turn (cached reads included) past which the next turn starts a fresh thread.
pub const DEFAULT_ROTATE_AT: i64 = 400_000;

#[derive(Clone, Debug, PartialEq)]
pub struct Rotation {
    /// `tokens` or `turns`: which limit the old thread reached.
    pub why: &'static str,
    /// The old thread's newest turn's input tokens, and its turns here.
    pub tokens: i64,
    pub turns: i64,
    /// The limit it reached, in the unit of `why`.
    pub limit: i64,
}

fn whole(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// The bot's `rotate_at` in its run config: tokens, 0 (or less) for never; DEFAULT_ROTATE_AT when unset.
pub fn rotate_at(config: &Value) -> i64 {
    whole(&config["rotate_at"]).unwrap_or(DEFAULT_ROTATE_AT).max(0)
}

/// The bot's `rotate_after_turns`: turns on one thread, 0 or unset for no limit.
pub fn rotate_after_turns(config: &Value) -> i64 {
    whole(&config["rotate_after_turns"]).unwrap_or(0).max(0)
}

/// Whether the next turn leaves a thread of this size (`size`: State::session_size) for a fresh one. A thread no
/// turn here has measured (one from before this runner) is resumed, and measured on that turn.
pub fn due(config: &Value, size: Option<(i64, i64)>) -> Option<Rotation> {
    let (turns, tokens) = size?;
    let at = rotate_at(config);
    if at > 0 && tokens >= at {
        return Some(Rotation { why: "tokens", tokens, turns, limit: at });
    }
    let most = rotate_after_turns(config);
    (most > 0 && turns >= most).then_some(Rotation { why: "turns", tokens, turns, limit: most })
}

impl Rotation {
    /// What the run's result carries (`rotated`, backend/models.py RunRotation).
    pub fn report(&self) -> Value {
        json!({"reason": self.why, "tokens": self.tokens, "turns": self.turns, "limit": self.limit})
    }

    fn because(&self) -> String {
        match self.why {
            "turns" => format!("it had run {} turns (this bot's limit is {})", self.turns, self.limit),
            _ => format!("its last turn read {} input tokens (this bot's limit is {})", self.tokens, self.limit),
        }
    }

    /// The run's diagnostic, for its Runs and the runner's log.
    pub fn diagnostic(&self) -> String {
        format!("Started a fresh session instead of resuming the last one: {}.", self.because())
    }

    /// Told to the fresh thread, after the rest of its prompt (`checkpoint`: the prompt gives the task's).
    pub fn prompt_note(&self, checkpoint: bool) -> String {
        let seed = if checkpoint {
            "this prompt gives where the task stood after its last turn and the conversation since that turn"
        } else {
            "this prompt gives only the conversation since your last turn, your own replies included"
        };
        format!(
            "Fresh session: your previous session here had grown large ({}), so this turn starts a new one without its \
             transcript, to save tokens; {seed}. Read state.md and memory/ before acting; `hub task show` and \
             `hub conversation show` have anything older.",
            self.because()
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_thread_rotates_once_its_last_turn_reaches_the_limit_and_not_before() {
        let none = json!({});
        assert_eq!(rotate_at(&none), DEFAULT_ROTATE_AT);
        assert_eq!(due(&none, None), None, "a thread never measured here resumes");
        assert_eq!(due(&none, Some((3, DEFAULT_ROTATE_AT - 1))), None);
        let r = due(&none, Some((3, DEFAULT_ROTATE_AT))).unwrap();
        assert_eq!((r.why, r.tokens, r.turns, r.limit), ("tokens", DEFAULT_ROTATE_AT, 3, DEFAULT_ROTATE_AT));
        assert_eq!(r.report(), json!({"reason": "tokens", "tokens": DEFAULT_ROTATE_AT, "turns": 3, "limit": DEFAULT_ROTATE_AT}));
        // The bot's own limit, as a number or text; 0 never rotates on size.
        assert!(due(&json!({"rotate_at": 1000}), Some((1, 1000))).is_some());
        assert!(due(&json!({"rotate_at": "2000"}), Some((1, 1000))).is_none());
        assert!(due(&json!({"rotate_at": 0}), Some((1, 50_000_000))).is_none());
    }

    #[test]
    fn a_turn_limit_rotates_a_thread_of_small_turns() {
        let config = json!({"rotate_after_turns": 20});
        assert!(due(&config, Some((19, 10))).is_none());
        let r = due(&config, Some((20, 10))).unwrap();
        assert_eq!((r.why, r.limit), ("turns", 20));
        assert!(r.diagnostic().contains("20 turns"));
        assert!(due(&json!({}), Some((500, 10))).is_none(), "no turn limit unless one is set");
    }
}
