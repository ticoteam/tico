//! Website tools' browser sessions on this computer (`browser_v1`). The hub queues a bot's or a person's browser
//! commands (backend/browser_commands.py); this computer holds `GET runner-browser-commands` open on a task of its
//! own, runs each command in the Chrome of the account it names, and reports the result.
//!
//! - One real Chrome per account (Google Chrome, or Chromium on Linux; its profile under the runner's state directory),
//!   with a window where there is a display, started on the account's first command and closed cleanly after 15
//!   minutes idle; at most `host::MOST_OPEN` at once.
//! - The runner drives it over the DevTools pipe only (`cdp`): bots get verbs, never the protocol, a port or cookies.
//! - A login value arrives in a `fill` or `copy` command, is typed or put on the clipboard, and is masked in every
//!   running turn's output here (`crate::redact::hide_everywhere`) and in what the session reports back.

mod cdp;
mod fill;
mod host;
mod launch;
#[cfg(test)]
mod live;
mod outline;
mod verbs;

use serde_json::{json, Value};

pub const CAPABILITY: &str = "browser_v1";

/// Whether this computer can run browser sessions: Chrome is installed; Linux can use headless mode without a display.
pub fn offers() -> bool {
    launch::chrome().is_some()
}

/// The readiness facts: whether Chrome is here, its version, and how many sessions are open.
pub fn readiness() -> Value {
    let exe = launch::chrome();
    json!({
        "chrome": exe.is_some(),
        "version": exe.as_deref().and_then(launch::version),
        "open": host::open_count(),
    })
}

/// Start the command loop; it idles while Chrome is missing.
pub fn start(config: crate::config::Config) {
    host::start(config);
}

/// Close every open Chrome cleanly, so each profile keeps its sign-in and page.
pub async fn shutdown() {
    host::shutdown().await;
}

/// A profile name is the account id: a short name safe as one directory.
pub fn valid_profile(profile: &str) -> bool {
    !profile.is_empty()
        && profile.len() <= 64
        && !profile.starts_with(['.', '-'])
        && profile
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_profile_is_one_safe_directory_name() {
        for good in ["c_0a1b2c", "acct-12", "A9"] {
            assert!(valid_profile(good), "{good}");
        }
        for bad in [
            "",
            "..",
            "../x",
            "a/b",
            ".hidden",
            "-flag",
            "a b",
            &"x".repeat(65),
        ] {
            assert!(!valid_profile(bad), "{bad}");
        }
    }
}
