//! Native goals and harness commands on a host (`start_goal`, `start_goal_turn`, `start_command`, `poll_goal`,
//! `active_turn`). Kept apart from `Host`'s core calls so new hosts add a line here only
//! when they take goals or commands.

use serde_json::{json, Value};

use super::{failed, Host, HostError};

impl Host {
    /// Set (`set`, which also starts the turn on the objective), pause, or clear the thread's native goal.
    pub async fn start_goal(&self, thread: &str, action: &str, objective: &str, effort: Option<&str>) -> Result<String, HostError> {
        match self {
            Host::Codex(h) => h.start_goal(thread, action, objective, effort).await,
            Host::Claude(h) => h.start_goal(thread, action, objective, effort),
            #[allow(unreachable_patterns)]
            _ => Err(failed("this harness has no native goals")),
        }
    }

    /// A task goal and the task's prompt in one turn.
    pub async fn start_goal_turn(&self, thread: &str, objective: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        match self {
            Host::Codex(h) => h.start_goal_turn(thread, objective, text, effort).await,
            Host::Claude(h) => h.start_goal_turn(thread, objective, text, effort),
            #[allow(unreachable_patterns)]
            _ => Err(failed("this harness has no native goals")),
        }
    }

    /// A harness command (`/compact`, `/clear`, `/model ...`, `/review ...`) as its own turn.
    pub async fn start_command(&self, thread: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
        match self {
            Host::Codex(h) => h.start_command(thread, text, effort).await,
            #[allow(unreachable_patterns)]
            _ => self.start_turn(thread, text, effort).await,
        }
    }

    /// Goal records the harness keeps outside its event stream (Claude's transcript).
    pub fn poll_goal(&self, thread: &str) {
        if let Host::Claude(h) = self {
            h.poll_goal(thread);
        }
    }

    /// The turn the harness is running on `thread` now, when it says (Codex).
    pub fn active_turn(&self, thread: &str) -> Option<String> {
        match self {
            Host::Codex(h) => h.active_turn(thread),
            Host::Pi(h) => h.active_turn(thread),
            Host::Cursor(h) => h.active_turn(thread),
            Host::AppDriver(h) => h.active_turn(thread),
            #[cfg(test)]
            Host::Claude(h) => h.running_turn(thread),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    /// A resume snapshot has no revision: verify a Codex `cleared` before stopping a goal that a later set may
    /// already have activated (goals.reconcile_codex_clear). A failed query propagates.
    pub async fn reconcile_codex_clear(&self, thread: &str, event: Value, objective: &str) -> Result<Value, HostError> {
        let Host::Codex(h) = self else { return Ok(event) };
        if event["status"] != "cleared" {
            return Ok(event);
        }
        let result = h.request("thread/goal/get", json!({"threadId": thread}), 120).await?;
        let goal = &result["goal"];
        if !goal.is_object() {
            return Ok(event);
        }
        let mut out = event.clone();
        if crate::goals::tico_objective(&goal["objective"]) != crate::goals::tico_objective(&json!(objective)) {
            out["status"] = json!("stopped");
            out["note"] = json!("The native goal changed");
            return Ok(out);
        }
        let status = crate::goals::codex_status(goal);
        if !["active", "paused", "met", "stopped"].contains(&status.as_str()) {
            return Err(failed("The native goal returned an unsupported status"));
        }
        out["status"] = json!(status);
        out["objective"] = json!(objective);
        out["note"] = goal["note"].as_str().filter(|n| !n.is_empty()).map(|n| json!(n)).unwrap_or_else(|| {
            if status == "stopped" { goal.get("status").cloned().unwrap_or(json!("")) } else { json!("") }
        });
        Ok(out)
    }
}
