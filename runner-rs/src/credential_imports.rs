//! A credential administrator asked for one variable of a bot's own secrets file to move into Credentials
//! (`hub credential import`; service.py `poll_credential_imports`, `import_credential`). The value is read from
//! `secrets/<bot>.env` and no other file, and goes into that one request only: never a log, never the state.

use std::sync::Arc;
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Value};

use crate::client::ApiError;
use crate::runner::Runner;
use crate::signin::read_env;
use crate::util::{log, text_of};

pub const POLL_S: f64 = 5.0;
const OP_REF: &str = "op://";
const OP_TIMEOUT: Duration = Duration::from_secs(20);

/// `op read` for one 1Password reference with the service-account token, or "" when it does not resolve.
async fn op_read(reference: &str, token: &str) -> String {
    if token.trim().is_empty() {
        log("op: OP_SERVICE_ACCOUNT_TOKEN is not set in secrets/_shared.env");
        return String::new();
    }
    let mut cmd = tokio::process::Command::new("op");
    cmd.args(["read", "--no-newline", reference]).env("OP_SERVICE_ACCOUNT_TOKEN", token.trim()).stdin(std::process::Stdio::null()).kill_on_drop(true);
    match tokio::time::timeout(OP_TIMEOUT, cmd.output()).await {
        Ok(Ok(out)) if out.status.success() && !out.stdout.is_empty() => String::from_utf8_lossy(&out.stdout).into_owned(),
        Ok(Ok(out)) => {
            let err = String::from_utf8_lossy(&out.stderr);
            log(&format!("op: value did not resolve from 1Password: {}", err.trim().lines().last().unwrap_or("op failed")));
            String::new()
        }
        Ok(Err(_)) => {
            log("op: the 1Password CLI (`op`) is not installed on PATH");
            String::new()
        }
        Err(_) => {
            log(&format!("op: op read timed out after {}s", OP_TIMEOUT.as_secs()));
            String::new()
        }
    }
}

pub async fn import_credential(runner: &Arc<Runner>, item: &Value) -> Result<(), ApiError> {
    let (iid, bot, key) = (text_of(&item["id"]), text_of(&item["bot"]), text_of(&item["env"]));
    let bot_re = Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").unwrap();
    let key_re = Regex::new(r"^[A-Z_][A-Z0-9_]*$").unwrap();
    let (mut value, mut problem) = (String::new(), String::new());
    if iid.is_empty() || !bot_re.is_match(&bot) || !key_re.is_match(&key) {
        problem = "That is not a variable or bot this computer can read".into();
    } else {
        let secrets = runner.config.projects_dir().join("secrets");
        value = read_env(&secrets.join(format!("{bot}.env"))).get(&key).cloned().unwrap_or_default();
        if value.starts_with(OP_REF) {
            // A 1Password reference: hand over what it resolves to, as a run would.
            let token = read_env(&secrets.join("_shared.env")).get("OP_SERVICE_ACCOUNT_TOKEN").cloned().unwrap_or_default();
            value = op_read(&value, &token).await;
            if value.is_empty() {
                problem = format!("{key} in that bot's file is a 1Password reference that did not resolve");
            }
        } else if value.is_empty() {
            problem = format!("{key} is not in that bot's secrets file on this computer");
        }
    }
    let body = if !value.is_empty() { json!({"value": value}) } else { json!({"error": problem}) };
    let sent = runner.client.post(&format!("runner-credential-imports/{iid}/report"), &body, None).await;
    drop(body);
    drop(value);
    sent?;
    log(&format!("Tico runner: credential import for {bot} {}", if problem.is_empty() { "sent" } else { "could not be read" }));
    Ok(())
}

/// Every import the server is waiting on.
pub async fn poll(runner: &Arc<Runner>) -> Result<(), ApiError> {
    let reply = runner.client.get("runner-credential-imports").await?;
    for item in reply["imports"].as_array().cloned().unwrap_or_default() {
        import_credential(runner, &item).await?;
    }
    Ok(())
}
