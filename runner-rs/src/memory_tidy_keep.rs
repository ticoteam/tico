//! Reviewer-authorized deterministic Keep, outside model turns. The same locks as
//! ordinary bot pushes cover fetch, checks, commit and every retry/completion.
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use serde_json::Value;
use tokio::io::AsyncWriteExt;
use crate::{runner::Runner, util::text_of};

struct Group(u32);
impl Drop for Group {
    fn drop(&mut self) {
        if self.0 != 0 {
            // SAFETY: this child was started in its own process group below.
            unsafe { libc::kill(-(self.0 as i32), libc::SIGKILL); }
        }
    }
}

async fn apply(runner: &Runner, request: &Value) -> Result<Value, String> {
    let bot = text_of(&request["bot"]);
    let id = text_of(&request["id"]);
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Invalid Keep operation".into());
    }
    let main = crate::sessions::local_path(&runner.config, &bot, &request["config"]);
    let _checkout = runner.lanes.checkout(&bot).lock_owned().await;
    let _git = runner.lanes.git_lock(&main).lock_owned().await;
    let _push = runner.push_lock.lock().await;
    let mut env = crate::env::process_environment();
    if !crate::gitcred::apply(&mut env, &runner.client, &runner.tokens, &bot,
                            runner.socket.as_ref().map(|s| s.path.as_path()), &runner.state.directory).await {
        return Err("Repository credentials unavailable; card remains open".into());
    }
    let journal = runner.state.directory.join(format!("memory-keep-{id}.attempts"));
    let mut command = tokio::process::Command::new(crate::env::python());
    command.args(["-m", "runner.memory_tidy_keep"]).arg(&main).arg(&journal)
        .current_dir(crate::env::root()).env_clear().envs(env)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .kill_on_drop(true).process_group(0);
    let mut child = command.spawn().map_err(|_| "Could not start Keep checker".to_string())?;
    let _group = Group(child.id().unwrap_or(0));
    let mut stdin = child.stdin.take().ok_or("Keep checker input unavailable")?;
    stdin.write_all(request.to_string().as_bytes()).await.map_err(|_| "Keep checker input failed")?;
    drop(stdin);
    let output = tokio::time::timeout(Duration::from_secs(900), child.wait_with_output()).await
        .map_err(|_| "Keep checker timed out; decision will be reconciled on retry")?
        .map_err(|_| "Keep checker could not finish")?;
    if !output.status.success() { return Err("Keep checker failed; card remains open".into()); }
    serde_json::from_slice(&output.stdout).map_err(|_| "Keep checker returned an invalid result".into())
}

pub async fn run_loop(runner: Arc<Runner>) {
    while !runner.stopping() {
        if let Ok(payload) = runner.client.get("runners/me/memory-tidy-keeps").await {
            for request in payload["keeps"].as_array().into_iter().flatten() {
                if runner.stopping() { break; }
                let id = text_of(&request["id"]);
                // An interrupted/uncertain push must be reconciled by the checker,
                // never reported as refusal: it may already be in main.
                if let Ok(result) = apply(&runner, request).await {
                    let _ = runner.client.post(&format!("runners/me/memory-tidy-keeps/{id}/complete"), &result, None).await;
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(15)).await;
    }
}
