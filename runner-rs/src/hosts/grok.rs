//! Grok Build's own glue: how its login is read and handed to the bot user. Its turns run on the generic ACP host
//! (hosts/acp_host.rs) from `runner-rs/harnesses/grok.toml`'s `[acp]` table; hosts/mod.rs reaches these through
//! `prepare_login_home`, `file_signed_in` and `file_sign_in_detail`.

use std::io::Read;

use serde_json::Value;

use crate::env::Env;

/// The computer's existing Grok login stays in place, but sign-in and token refresh run as the bot user.
/// Earlier supervisor commands can have made this folder before any bot ran. Only hand over
/// the known default store; custom auth paths are never adopted with supervisor privileges.
pub fn hand_login_to_bot() {
    if let Some(home) = std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        crate::isolation::adopt_store(&[std::path::PathBuf::from(home).join(".grok")]);
    }
}

/// A default home created after startup must also allow API-key turns to save their sessions.
/// Match the effective home, but never let a profile or custom auth path choose a privileged handoff target.
pub fn prepare_home(env: &Env) {
    if std::env::var("HOME")
        .ok()
        .filter(|home| !home.is_empty())
        .is_some_and(|home| env.get("HOME") == Some(&home))
    {
        hand_login_to_bot();
    }
}

/// Grok has no status subcommand. Its scoped auth store is the local sign-in; an empty store after logout is not.
/// Paths and entry fields follow xai-org/grok-build's xai-grok-login storage.rs and model.rs.
pub fn sign_in_detail(env: &Env) -> Option<&'static str> {
    let path = env
        .get("GROK_AUTH_PATH")
        .filter(|v| !v.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| {
            env.get("GROK_HOME")
                .filter(|v| !v.is_empty())
                .map(std::path::PathBuf::from)
                .or_else(|| {
                    env.get("HOME")
                        .map(|h| std::path::Path::new(h).join(".grok"))
                })
                .map(|h| h.join("auth.json"))
        })?;
    // This runs in the supervisor's heartbeat path against a bot-writable store. No component may be a link the
    // bot planted (the fixed label would still answer "does this path parse"), opening a FIFO must not wait for a
    // writer, and devices or growing files must not supply an unbounded stream.
    let file = match crate::isolation::open_unfollowed(&path, libc::O_NONBLOCK) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(""),
        Err(_) => return None,
    };
    const MAX_AUTH_BYTES: u64 = 1024 * 1024;
    let metadata = file.metadata().ok()?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_AUTH_BYTES {
        return None;
    }
    // A second name for a file someone else owns (a supervisor file linked into the store) is not the store.
    use std::os::unix::fs::MetadataExt;
    let owner =
        crate::isolation::identity().map_or_else(|| unsafe { libc::geteuid() }, |(uid, _)| uid);
    if metadata.nlink() != 1 && metadata.uid() != owner {
        return None;
    }
    let mut text = String::new();
    // The descriptor can grow after metadata was checked; allow one extra byte to detect overflow.
    file.take(MAX_AUTH_BYTES + 1)
        .read_to_string(&mut text)
        .ok()?;
    if text.len() as u64 > MAX_AUTH_BYTES {
        return None;
    }
    if text.trim().is_empty() {
        return Some("");
    }
    let store = serde_json::from_str::<Value>(&text).ok()?;
    let entries = store.as_object()?;
    let modes = || {
        entries
            .values()
            .filter(|entry| entry["key"].as_str().is_some_and(|k| !k.trim().is_empty()))
            .filter_map(|entry| entry["auth_mode"].as_str())
    };
    if modes().any(|mode| matches!(mode, "oidc" | "external")) {
        Some("Signed in with a Grok account")
    } else if modes().any(|mode| mode == "api_key") {
        Some("Signed in with an API key")
    } else {
        Some("")
    }
}

pub fn signed_in(env: &Env) -> Option<bool> {
    sign_in_detail(env).map(|detail| !detail.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn auth_status_accepts_only_bounded_valid_stores() {
        let root = std::env::temp_dir().join(format!("grok-auth-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join(".grok")).unwrap();
        let env = Env::from([("HOME".into(), root.display().to_string())]);
        let path = root.join(".grok/auth.json");
        assert_eq!(sign_in_detail(&env), Some(""));
        for (text, expected) in [
            ("", Some("")),
            ("{}", Some("")),
            (
                r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#,
                Some("Signed in with a Grok account"),
            ),
            (
                r#"{"acme":{"auth_mode":"api_key","key":"synthetic"}}"#,
                Some("Signed in with an API key"),
            ),
            ("broken", None),
        ] {
            std::fs::write(&path, text).unwrap();
            assert_eq!(sign_in_detail(&env), expected);
        }
        // A valid JSON store padded to exactly the documented 1 MiB limit still works.
        let mut text = "{}".to_string();
        text.extend(std::iter::repeat_n(' ', 1_048_576 - text.len()));
        std::fs::write(&path, &text).unwrap();
        assert_eq!(sign_in_detail(&env), Some(""));
        text.push(' ');
        std::fs::write(&path, text).unwrap();
        assert_eq!(sign_in_detail(&env), None);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn auth_status_rejects_special_files_without_blocking() {
        const CHILD_HOME: &str = "TICO_TEST_GROK_SPECIAL_AUTH_HOME";
        if let Some(root) = std::env::var_os(CHILD_HOME) {
            use std::os::unix::{ffi::OsStrExt, fs::symlink};
            let root = std::path::PathBuf::from(root);
            let path = root.join(".grok/auth.json");
            let env = Env::from([("HOME".into(), root.display().to_string())]);
            let cpath = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
            assert_eq!(sign_in_detail(&env), None, "a FIFO has no sign-in status");
            std::fs::remove_file(&path).unwrap();
            std::fs::create_dir(&path).unwrap();
            assert_eq!(sign_in_detail(&env), None);
            std::fs::remove_dir(&path).unwrap();
            let target = root.join("synthetic.json");
            std::fs::write(
                &target,
                r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#,
            )
            .unwrap();
            symlink(&target, &path).unwrap();
            assert_eq!(
                sign_in_detail(&env),
                None,
                "do not follow a regular-file link"
            );
            std::fs::remove_file(&path).unwrap();
            symlink("/dev/zero", &path).unwrap();
            assert_eq!(sign_in_detail(&env), None, "do not follow a device link");
            let device_env = Env::from([("GROK_AUTH_PATH".into(), "/dev/zero".into())]);
            assert_eq!(
                sign_in_detail(&device_env),
                None,
                "reject a direct device override too"
            );
            return;
        }
        // Run the synchronous probe in a child so a regression can be killed without stranding
        // the test runner on a blocking open. Only synthetic files in this directory are read.
        let root = std::env::temp_dir().join(format!("grok-special-auth-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join(".grok")).unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "hosts::grok::tests::auth_status_rejects_special_files_without_blocking",
                "--nocapture",
            ])
            .env(CHILD_HOME, &root)
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let result = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break Some(status);
            }
            if std::time::Instant::now() >= deadline {
                child.kill().unwrap();
                child.wait().unwrap();
                break None;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        std::fs::remove_dir_all(root).unwrap();
        assert!(
            result.is_some_and(|status| status.success()),
            "credential status blocked or accepted a special file: {result:?}"
        );
    }

    #[test]
    fn auth_status_never_follows_a_linked_ancestor() {
        // A link a root test run makes is a trusted system alias, so only an ordinary user can plant one here.
        if unsafe { libc::geteuid() } == 0 {
            return;
        }
        let root = std::env::temp_dir().join(format!("grok-linked-auth-{}", uuid::Uuid::new_v4()));
        let elsewhere = root.join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        std::fs::write(
            elsewhere.join("auth.json"),
            r#"{"acme":{"auth_mode":"oidc","key":"synthetic"}}"#,
        )
        .unwrap();
        let real = Env::from([("GROK_HOME".into(), elsewhere.display().to_string())]);
        assert_eq!(
            sign_in_detail(&real),
            Some("Signed in with a Grok account"),
            "the real folder still reads"
        );
        std::os::unix::fs::symlink(&elsewhere, root.join(".grok")).unwrap();
        let env = Env::from([("HOME".into(), root.display().to_string())]);
        assert_eq!(
            sign_in_detail(&env),
            None,
            "a planted .grok link is not followed"
        );
        let env = Env::from([(
            "GROK_AUTH_PATH".into(),
            root.join(".grok/auth.json").display().to_string(),
        )]);
        assert_eq!(sign_in_detail(&env), None, "nor through a direct path");
        std::fs::remove_dir_all(root).unwrap();
    }
}
