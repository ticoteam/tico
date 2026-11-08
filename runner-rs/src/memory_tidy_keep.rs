//! Reviewer-authorized deterministic Keep, outside model turns. The same locks as
//! ordinary bot pushes cover fetch, checks, commit and every retry/completion.
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use serde_json::{json, Value};
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

fn settle(dir: &std::path::Path, id: &str, outcome: Result<Value, String>) -> Option<Value> {
    // Save a terminal result before reporting: a lost completion response cannot
    // restart a refused operation. Only uncertain outcomes consume this budget.
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Some(json!({"status":"refused", "commit":"", "reason":"Invalid Keep operation"}));
    }
    let done = dir.join(format!("memory-keep-{id}.result"));
    if let Ok(bytes) = std::fs::read(&done) {
        if let Ok(result) = serde_json::from_slice(&bytes) { return Some(result); }
    }
    let result = match outcome {
        Ok(v) if v["status"] == "kept" || v["status"] == "refused" => v,
        other => {
            let attempts = dir.join(format!("memory-keep-{id}.attempts"));
            let pushed = std::fs::read_to_string(attempts).ok().and_then(|n| n.parse::<u32>().ok()).unwrap_or(0) > 0;
            if !pushed && other.is_err() {
                json!({"status":"refused", "commit":"", "reason":"Keep checker unavailable; check computer repository access and try again"})
            } else {
                let counter = dir.join(format!("memory-keep-{id}.checks"));
                let n = std::fs::read_to_string(&counter).ok().and_then(|n| n.parse::<u32>().ok()).unwrap_or(0) + 1;
                let temp = counter.with_extension("tmp");
                if std::fs::write(&temp, n.to_string()).and_then(|_| std::fs::rename(temp, counter)).is_err() {
                    return Some(json!({"status":"refused", "commit":"", "reason":"Cannot save Keep recovery state; check computer storage"}));
                }
                if n < 3 { return None; }
                json!({"status":"refused", "commit":"", "reason":"Keep outcome could not be verified after three checks; inspect main before trying again"})
            }
        }
    };
    let tmp = done.with_extension("tmp");
    if std::fs::write(&tmp, result.to_string()).and_then(|_| std::fs::rename(tmp, done)).is_err() {
        return Some(json!({"status":"refused", "commit":"", "reason":"Cannot save Keep recovery state; check computer storage"}));
    }
    Some(result)
}

async fn deliver(runner: &Runner, request: &Value) {
    let id = text_of(&request["id"]);
    let saved = runner.state.directory.join(format!("memory-keep-{id}.result"));
    let outcome = match std::fs::read(saved).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()) {
        Some(v) => Ok(v),
        None => apply(&runner, request).await,
    };
    let result = settle(&runner.state.directory, &id, outcome);
    if let Some(result) = result {
        let _ = runner.client.post(&format!("runners/me/memory-tidy-keeps/{id}/complete"), &result, None).await;
    }
}

pub async fn run_loop(runner: Arc<Runner>) {
    while !runner.stopping() {
        if let Ok(payload) = runner.client.get("runners/me/memory-tidy-keeps").await {
            for request in payload["keeps"].as_array().into_iter().flatten() {
                if runner.stopping() { break; }
                deliver(&runner, request).await;
            }
        }
        tokio::time::sleep(Duration::from_secs(15)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn ordinary_push_lock_excludes_keep_before_repository_access() {
        let dir = std::env::temp_dir().join(format!("tico-keep-lock-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let server = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", server.local_addr().unwrap());
        let config = crate::config::Config {
            path: dir.join("runner.json"),
            data: json!({"projects_dir": dir, "url": url, "token": "synthetic", "runner_id": "test"}).as_object().unwrap().clone(),
        };
        let runner = Arc::new(Runner::new(config, crate::client::Client::new(&url, "synthetic", 2, 0).unwrap(),
                                        crate::state::State::open(&dir.join("state")).unwrap()));
        let ordinary_push = runner.push_lock.lock().await;
        let other = runner.clone();
        let keep = tokio::spawn(async move { deliver(&other, &json!({"id": "test-operation", "bot": "ops", "config": {}})).await });
        assert!(tokio::time::timeout(Duration::from_millis(50), server.accept()).await.is_err());
        drop(ordinary_push);
        let (mut stream, _) = tokio::time::timeout(Duration::from_secs(2), server.accept()).await.unwrap().unwrap();
        let mut request = [0u8; 8192];
        let n = stream.read(&mut request).await.unwrap();
        assert!(String::from_utf8_lossy(&request[..n]).contains("github/token"));
        let body = r#"{"configured":false}"#;
        stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
        let (mut completion, _) = tokio::time::timeout(Duration::from_secs(2), server.accept()).await.unwrap().unwrap();
        let mut bytes = vec![];
        loop {
            let mut chunk = [0u8; 8192];
            let n = tokio::time::timeout(Duration::from_secs(2), completion.read(&mut chunk)).await.unwrap().unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&chunk[..n]);
            let text = String::from_utf8_lossy(&bytes);
            if let Some((headers, body)) = text.split_once("\r\n\r\n") {
                let len = headers.lines().find_map(|line| line.to_lowercase().strip_prefix("content-length:").and_then(|n| n.trim().parse::<usize>().ok())).unwrap();
                if body.len() >= len { break; }
            }
        }
        let request = String::from_utf8_lossy(&bytes);
        assert!(request.contains("memory-tidy-keeps/test-operation/complete"));
        assert!(request.contains("refused"));
        completion.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").await.unwrap();
        keep.await.unwrap();
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn uncertain_errors_have_durable_bound_and_terminal_reporting_replays() {
        let dir = std::env::temp_dir().join(format!("tico-keep-errors-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("memory-keep-test.attempts"), "1").unwrap();
        assert!(settle(&dir, "test", Err("timeout".into())).is_none());
        assert!(settle(&dir, "test", Ok(json!({"status":"retry"}))).is_none());
        let terminal = settle(&dir, "test", Err("timeout".into())).unwrap();
        assert_eq!(terminal["status"], "refused");
        assert_eq!(settle(&dir, "test", Ok(json!({"status":"kept"}))).unwrap(), terminal);
        std::fs::remove_dir_all(dir).unwrap();
    }

}
