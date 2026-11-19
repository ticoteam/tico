//! Once per start, every assigned bot's checkout is pushed (service.py `push_backlog`): a laptop that has been the
//! only copy of a bot's memory catches up. Pushing only reads the tree, so a turn that starts meanwhile is
//! unaffected; a push refused as behind is left for the bot's next turn, which rebases.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::runner::Runner;
use crate::util::text_of;

/// How long the backlog waits for the run loop's first read of this computer's bots before reading them itself.
const OWN_READ_AFTER: Duration = Duration::from_secs(60);

/// The credential helper `gitcred::apply` sets when no token could be had (git_credentials.is_failed_helper).
fn failed_helper(value: &str) -> bool {
    value == crate::gitcred::FAILED_HELPER
        || (value.starts_with("!f() { echo ") && value.ends_with(" >&2; echo quit=true; }; f"))
}

impl Runner {
    pub async fn push_backlog(self: Arc<Self>) {
        // The run loop's first full pass reads this computer's bots as it starts: the backlog takes that answer, and
        // asks the hub itself only if the pass has not read them within OWN_READ_AFTER.
        let began = Instant::now();
        let assignments = loop {
            if self.stopping() {
                return;
            }
            if self.assignments_read() {
                break self.assignments_seen().as_ref().clone();
            }
            if began.elapsed() < OWN_READ_AFTER {
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
            match self.client.get("runners/assignments").await {
                Ok(a) => break a.as_array().cloned().unwrap_or_default(),
                Err(_) => tokio::time::sleep(Duration::from_secs(30)).await,
            }
        };
        let mut pushed = HashSet::new();
        for row in assignments {
            if self.stopping() {
                return;
            }
            let bot = text_of(&row["bot"]);
            let path = crate::sessions::local_path(&self.config, &bot, &row["config"]);
            if pushed.contains(&path) || !path.join(".git").exists() {
                continue;
            }
            let mut env = crate::env::process_environment();
            // A temporary socket capability, never the registration token.
            let token = self.socket.as_ref().map(|s| {
                let token = uuid::Uuid::new_v4().simple().to_string();
                s.register(&token, &bot);
                env.insert("HUB_TOKEN".into(), token.clone());
                token
            });
            let socket = self.socket.as_ref().map(|s| s.path.clone());
            crate::gitcred::apply(
                &mut env,
                &self.client,
                &self.tokens,
                &bot,
                socket.as_deref(),
                &self.state.directory,
            )
            .await;
            // Never fall back to another login when scoped access failed.
            let failed = env
                .iter()
                .any(|(k, v)| k.starts_with("GIT_CONFIG_VALUE_") && failed_helper(v));
            if !failed && self.push_checkout_as(&path, &env, false).await {
                pushed.insert(path);
            }
            if let (Some(s), Some(t)) = (&self.socket, token) {
                s.unregister(&t);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_failed_helper_is_known_with_or_without_its_reason() {
        assert!(super::failed_helper(crate::gitcred::FAILED_HELPER));
        assert!(super::failed_helper(
            "!f() { echo 'no app' >&2; echo quit=true; }; f"
        ));
        assert!(!super::failed_helper(
            "!f() { exec tico-runner git-credential; }; f"
        ));
    }
}
