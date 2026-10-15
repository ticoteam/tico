//! The company's Google Workspace key, where bots cannot read it (runner/mail_key.py). Under the two-user
//! layout (crate::isolation) the supervisor keeps it in its own state directory and an inbox bot's turn asks,
//! over the credential socket, for a short-lived token for one of the mailboxes the hub named for that attempt.
//! Without the layout the key stays where it was, bots can read it, and readiness says `exposed`.

use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use serde_json::{json, Value};

use crate::config::Config;
use crate::util::log;

pub const FILE: &str = "google-sa.json";
/// People's own connected Google accounts (connectors/mail/oauth.py), beside the key and protected the same way.
pub const GRANTS: &str = "google-oauth.json";
const CARRY_MARKERS: &[(&str, &str)] = &[(FILE, ".mail-key-carried"), (GRANTS, ".google-oauth-carried")];
pub const SERVICES: &[&str] = &["gmail", "calendar"];

/// Where the key lives when isolation is on: the supervisor's state directory.
pub fn protected_path(config: &Config) -> PathBuf {
    config.state_dir().join(FILE)
}

pub fn workspace_path(config: &Config) -> PathBuf {
    config.projects_dir().join("secrets").join(FILE)
}

/// The key the connectors read: GOOGLE_SA_KEY, else the protected copy under isolation, else the workspace's
/// (connectors.mail_secret_path).
pub fn mail_secret_path(config: &Config) -> PathBuf {
    if let Ok(named) = std::env::var("GOOGLE_SA_KEY") {
        if !named.is_empty() {
            let home = std::env::var("HOME").unwrap_or_default();
            return PathBuf::from(named.strip_prefix("~/").map(|r| format!("{home}/{r}")).unwrap_or(named));
        }
    }
    if crate::isolation::enabled() {
        return protected_path(config);
    }
    workspace_path(config)
}

/// The connected-account grants the connectors read: GOOGLE_OAUTH_GRANTS, else beside the key.
pub fn grants_path(config: &Config) -> PathBuf {
    match std::env::var("GOOGLE_OAUTH_GRANTS") {
        Ok(named) if !named.is_empty() => PathBuf::from(named),
        _ => mail_secret_path(config).with_file_name(GRANTS),
    }
}

/// Whether this computer can give mail access: the service-account key, a connected account, or both.
pub fn credential_present(config: &Config) -> bool {
    mail_secret_path(config).is_file() || grants_path(config).is_file()
}

/// Move a key or grants found in the workspace to the protected place. A no-op wherever isolation is off.
pub fn protect(config: &Config) -> bool {
    if !crate::isolation::enabled() || std::env::var("GOOGLE_SA_KEY").is_ok_and(|v| !v.is_empty()) {
        return false;
    }
    let mut moved = false;
    for name in [FILE, GRANTS] {
        moved |= protect_one(config, name);
    }
    moved
}

fn protect_one(config: &Config, name: &str) -> bool {
    let (old, new) = (workspace_path(config).with_file_name(name), protected_path(config).with_file_name(name));
    let moved = (|| -> std::io::Result<bool> {
        let meta = match std::fs::symlink_metadata(&old) {
            Ok(m) if m.is_file() => m,
            _ => return Ok(false),
        };
        let _ = meta;
        let data = std::fs::read(&old)?;
        if let Some(parent) = new.parent() {
            std::fs::DirBuilder::new().recursive(true).mode(0o700).create(parent)?;
        }
        let tmp = new.with_file_name(format!("{name}.new"));
        let _ = std::fs::remove_file(&tmp);
        let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp)?;
        f.write_all(&data)?;
        std::fs::rename(&tmp, &new)?;
        std::fs::remove_file(&old)?;
        Ok(true)
    })();
    match moved {
        Ok(true) => {
            log(&format!("Tico runner: moved {name} out of workspace/secrets to {}; bots ask the runner for mail access", new.display()));
            true
        }
        Ok(false) => false,
        Err(e) => {
            log(&format!("Tico runner: could not move {name} out of the bots' reach ({:?})", e.kind()));
            false
        }
    }
}

/// `protected`, `exposed` (a key bots can read) or None (no key on this computer).
pub fn status(config: &Config) -> Option<&'static str> {
    if crate::isolation::enabled() {
        if [FILE, GRANTS].iter().any(|n| workspace_path(config).with_file_name(n).is_file()) {
            return Some("exposed");
        }
        return [FILE, GRANTS].iter().any(|n| protected_path(config).with_file_name(n).is_file()).then_some("protected");
    }
    credential_present(config).then_some("exposed")
}

/// Whether this computer holds the Google key or a connected account's grant for its bots (isolated, side jobs supervised here, key present):
/// a bot's GOOGLE_SA_KEY then counts as present though its turn never has it.
pub fn held_by_computer(config: &Config) -> bool {
    crate::isolation::enabled() && std::env::var("TICO_SIDE_JOBS").as_deref() == Ok("1") && credential_present(config)
}

/// Recover the mail key after re-enrollment, only from this operator's previous registration
/// (`runner.json.stale`): strict ownership and mode checks, a marker so it happens once per registration, and
/// a hard link so a complete file appears without replacing a key already there.
pub fn carry_protected(config: &Config) -> bool {
    let mut carried = false;
    for (name, marker) in CARRY_MARKERS {
        carried |= carry_one(config, name, marker);
    }
    carried
}

fn carry_one(config: &Config, file: &str, marker: &str) -> bool {
    let config_path = &config.path;
    let name = config_path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let stale = config_path.with_file_name(format!("{name}.stale"));
    let mut temporary: Option<PathBuf> = None;
    let result = carry(config, &stale, &mut temporary, file, marker);
    if let Some(t) = temporary {
        let _ = std::fs::remove_file(t);
    }
    match result {
        Ok(done) => done,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound || e.kind() == std::io::ErrorKind::AlreadyExists => false,
        Err(e) => {
            if stale.exists() {
                log(&format!(
                    "Tico runner: could not carry the protected {file} to this registration ({:?}); check free space and permissions on the runner volume; the old key is unchanged",
                    e.kind()
                ));
            }
            false
        }
    }
}

fn carry(config: &Config, stale: &Path, temporary: &mut Option<PathBuf>, file: &str, marker_name: &str) -> std::io::Result<bool> {
    // SAFETY: getuid has no preconditions.
    let uid = unsafe { libc::getuid() };
    let dir = config.path.parent().unwrap_or(Path::new("."));
    let parent = std::fs::metadata(dir)?;
    let info = std::fs::symlink_metadata(stale)?;
    if !info.is_file() || info.uid() != uid || info.dev() != parent.dev() || info.mode() & 0o077 != 0 {
        return Ok(false);
    }
    let Ok(previous) = serde_json::from_str::<Value>(&std::fs::read_to_string(stale)?) else { return Ok(false) };
    let Some(previous) = previous.as_object() else { return Ok(false) };
    let now = &config.data;
    if !crate::util::truthy(now.get("operator")) || ["operator", "url"].iter().any(|k| previous.get(*k) != now.get(*k)) {
        return Ok(false);
    }
    if crate::util::truthy(previous.get("environment")) && crate::util::truthy(now.get("environment")) && previous.get("environment") != now.get("environment") {
        return Ok(false);
    }
    let ident = |v: Option<&Value>| match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    };
    let (old_id, new_id) = (ident(previous.get("runner_id")), ident(now.get("runner_id")));
    if old_id.is_empty() || new_id.is_empty() || [&old_id, &new_id].iter().any(|i| i.contains('/') || *i == "." || *i == "..") {
        return Ok(false);
    }
    let (old, new) = (dir.join(format!("state-{old_id}")), dir.join(format!("state-{new_id}")));
    match std::fs::DirBuilder::new().mode(0o700).create(&new) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    let safe_directory = |d: &Path| -> std::io::Result<bool> {
        let i = std::fs::symlink_metadata(d)?;
        Ok(i.is_dir() && i.uid() == uid && i.dev() == parent.dev() && i.mode() & 0o066 == 0)
    };
    if !safe_directory(&new)? {
        return Ok(false);
    }
    let marker = new.join(marker_name);
    if std::fs::symlink_metadata(&marker).is_ok() {
        return Ok(false);
    }
    let carried = || std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&marker).map(|_| ());
    if std::fs::symlink_metadata(new.join(file)).is_ok() {
        carried()?;
        return Ok(false);
    }
    if !safe_directory(&old)? {
        return Ok(false);
    }
    let mut source = match std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(old.join(file)) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            carried()?;
            return Ok(false);
        }
        Err(e) => return Err(e),
    };
    let meta = source.metadata()?;
    if !meta.is_file() || meta.uid() != uid || meta.dev() != parent.dev() || meta.mode() & 0o077 != 0 {
        return Ok(false);
    }
    let mut data = vec![];
    source.read_to_end(&mut data)?;
    let tmp = new.join(format!(".{file}-{}", uuid::Uuid::new_v4().simple()));
    let mut out = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp)?;
    *temporary = Some(tmp.clone());
    out.write_all(&data)?;
    out.set_permissions(std::fs::Permissions::from_mode(meta.mode() & 0o7777))?;
    out.sync_all()?;
    match std::fs::hard_link(&tmp, new.join(file)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            carried()?;
            return Ok(false);
        }
        Err(e) => return Err(e),
    }
    carried()?;
    Ok(true)
}

/// The key the socket's mail answers mint with, set once at start (None: this computer gives no mail access).
static MINT: OnceLock<(PathBuf, PathBuf)> = OnceLock::new();

/// Turn on mail answers: `<root>/scripts/mail.sh mint-token` with the protected key.
pub fn init(config: &Config) {
    let _ = MINT.set((crate::env::root().join("scripts").join("mail.sh"), protected_path(config)));
}

/// One short-lived token: the mail CLI, run here with the key, prints {"token", "expiry"}.
async fn mint(script: &Path, key: &Path, service: &str, mailbox: &str) -> Result<Value, String> {
    let mut cmd = tokio::process::Command::new(script);
    cmd.args(["mint-token", "--mailbox", mailbox, "--service", service])
        .current_dir(crate::env::root())
        .env_remove("HUB_TOKEN")
        .env("GOOGLE_SA_KEY", key)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    if crate::isolation::enabled() {
        // The mail CLI's database is shared with message bots in the same group (connectors.mail_umask).
        // SAFETY: the closure only calls umask.
        unsafe {
            cmd.pre_exec(|| {
                libc::umask(0o002);
                Ok(())
            })
        };
    }
    let out = tokio::time::timeout(Duration::from_secs(60), cmd.output())
        .await
        .map_err(|_| "TimeoutExpired".to_string())?
        .map_err(|_| "FileNotFoundError".to_string())?;
    if !out.status.success() {
        return Err("RuntimeError".into());
    }
    serde_json::from_slice(&out.stdout).map_err(|_| "JSONDecodeError".to_string())
}

/// The socket's answer to `{"mail": {"service", "mailbox"}}` for an attempt the hub named `allowed` for.
pub async fn answer(allowed: &BTreeSet<String>, asked: &Value) -> Value {
    let service = asked.get("service").and_then(|s| s.as_str()).unwrap_or_default();
    let mailbox = crate::manifest::text_or_empty(asked.get("mailbox")).trim().to_lowercase();
    let Some((script, key)) = MINT.get() else { return json!({"error": "no mail access"}) };
    if !asked.is_object() || !SERVICES.contains(&service) {
        return json!({"error": "no mail access"});
    }
    if allowed.is_empty() {
        return json!({"error": "no mailbox", "mailboxes": []});
    }
    if !allowed.contains(&mailbox) {
        return json!({"error": "not this bot's mailbox", "mailboxes": allowed.iter().collect::<Vec<_>>()});
    }
    match mint(script, key, service, &mailbox).await {
        Ok(granted) if granted.is_object() && crate::util::truthy(Some(&granted)) => {
            let mut reply = json!({"token": granted["token"], "expiry": granted.get("expiry").cloned().unwrap_or(json!(""))});
            // A person's own connected account (connectors/mail/oauth.py): the turn refuses a write it did not grant.
            if let Some(scopes) = granted.get("scopes").filter(|s| s.is_array()) {
                reply["scopes"] = scopes.clone();
            }
            reply
        }
        Ok(_) => json!({"error": "no token"}),
        Err(kind) => json!({"error": kind}),
    }
}

/// The mailboxes an attempt may open, as the socket compares them.
pub fn mailboxes(attempt: &Value) -> BTreeSet<String> {
    attempt["mailboxes"].as_array().into_iter().flatten().map(|m| crate::manifest::py_str(m).trim().to_lowercase()).filter(|m| !m.is_empty()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registration(dir: &Path, data: Value) -> Config {
        let path = dir.join("runner.json");
        std::fs::write(&path, data.to_string()).unwrap();
        Config::load(&path).unwrap()
    }

    fn private_file(path: &Path, text: &str) {
        std::fs::write(path, text).unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[test]
    fn the_key_follows_a_re_enrollment_once_and_only_from_the_same_operator() {
        let dir = std::env::temp_dir().join(format!("tico-mailkey-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("state-old")).unwrap();
        std::fs::set_permissions(dir.join("state-old"), std::fs::Permissions::from_mode(0o700)).unwrap();
        private_file(&dir.join("state-old").join(FILE), "{\"key\": 1}");
        let config = registration(&dir, json!({"runner_id": "new", "operator": "op-1", "url": "https://t.example"}));
        private_file(&dir.join("runner.json.stale"), &json!({"runner_id": "old", "operator": "op-2", "url": "https://t.example"}).to_string());
        assert!(!carry_protected(&config), "another operator's registration is never a source");
        private_file(&dir.join("runner.json.stale"), &json!({"runner_id": "old", "operator": "op-1", "url": "https://t.example"}).to_string());
        assert!(carry_protected(&config));
        let carried = dir.join("state-new").join(FILE);
        assert_eq!(std::fs::read_to_string(&carried).unwrap(), "{\"key\": 1}");
        assert_eq!(std::fs::metadata(&carried).unwrap().permissions().mode() & 0o777, 0o600);
        assert!(dir.join("state-old").join(FILE).exists(), "the old key stays for a rollback");
        std::fs::remove_file(&carried).unwrap();
        assert!(!carry_protected(&config), "a key deleted after the carry stays deleted");
        assert!(!dir.join("state-new").join(FILE).exists());
        assert_eq!(std::fs::read_dir(dir.join("state-new")).unwrap().count(), 2, "only the two markers, no temporary file");
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn connected_account_grants_follow_a_re_enrollment_beside_the_key() {
        let dir = std::env::temp_dir().join(format!("tico-grants-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("state-old")).unwrap();
        std::fs::set_permissions(dir.join("state-old"), std::fs::Permissions::from_mode(0o700)).unwrap();
        private_file(&dir.join("state-old").join(GRANTS), "{\"grants\": {}}");
        let config = registration(&dir, json!({"runner_id": "new", "operator": "op-1", "url": "https://t.example"}));
        private_file(&dir.join("runner.json.stale"), &json!({"runner_id": "old", "operator": "op-1", "url": "https://t.example"}).to_string());
        assert!(carry_protected(&config), "grants alone are carried: no service-account key is needed");
        let carried = dir.join("state-new").join(GRANTS);
        assert_eq!(std::fs::read_to_string(&carried).unwrap(), "{\"grants\": {}}");
        assert_eq!(std::fs::metadata(&carried).unwrap().permissions().mode() & 0o777, 0o600);
        assert!(!dir.join("state-new").join(FILE).exists());
        assert!(!carry_protected(&config), "once per registration");
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_stale_registration_others_could_read_is_not_trusted() {
        let dir = std::env::temp_dir().join(format!("tico-mailkey-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("state-old")).unwrap();
        std::fs::set_permissions(dir.join("state-old"), std::fs::Permissions::from_mode(0o700)).unwrap();
        private_file(&dir.join("state-old").join(FILE), "k");
        let config = registration(&dir, json!({"runner_id": "new", "operator": "op-1", "url": "u"}));
        private_file(&dir.join("runner.json.stale"), &json!({"runner_id": "old", "operator": "op-1", "url": "u"}).to_string());
        std::fs::set_permissions(dir.join("runner.json.stale"), std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(!carry_protected(&config));
        std::fs::set_permissions(dir.join("runner.json.stale"), std::fs::Permissions::from_mode(0o600)).unwrap();
        std::fs::set_permissions(dir.join("state-old").join(FILE), std::fs::Permissions::from_mode(0o640)).unwrap();
        assert!(!carry_protected(&config), "a key others could read is not carried");
        assert!(!dir.join("state-new").join(".mail-key-carried").exists(), "and it stays retryable");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn mail_answers_name_the_mailboxes_and_mint_only_for_one_of_them() {
        let dir = std::env::temp_dir().join(format!("tico-mint-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let script = dir.join("mail.sh");
        crate::util::write_script(&script, "#!/bin/sh\n[ -z \"$HUB_TOKEN\" ] || exit 9\ncase \"$3\" in ben@*) s=', \"scopes\": [\"gmail.readonly\"]';; *) s='';; esac\nprintf '{\"token\": \"ya29.%s-%s\", \"expiry\": \"soon\", \"key\": \"%s\"%s}' \"$3\" \"$5\" \"$GOOGLE_SA_KEY\" \"$s\"\n");
        let _ = MINT.set((script, dir.join(FILE)));
        let allowed: BTreeSet<String> = mailboxes(&json!({"mailboxes": ["Ana@Acme.example", "ben@acme.example"]}));
        assert_eq!(answer(&allowed, &json!({"service": "drive", "mailbox": "ana@acme.example"})).await, json!({"error": "no mail access"}));
        assert_eq!(answer(&BTreeSet::new(), &json!({"service": "gmail", "mailbox": "x@y"})).await, json!({"error": "no mailbox", "mailboxes": []}));
        assert_eq!(
            answer(&allowed, &json!({"service": "gmail", "mailbox": "eve@acme.example"})).await,
            json!({"error": "not this bot's mailbox", "mailboxes": ["ana@acme.example", "ben@acme.example"]})
        );
        let got = answer(&allowed, &json!({"service": "calendar", "mailbox": " ANA@acme.example "})).await;
        assert_eq!(got, json!({"token": "ya29.ana@acme.example-calendar", "expiry": "soon"}));
        let got = answer(&allowed, &json!({"service": "gmail", "mailbox": "ben@acme.example"})).await;
        assert_eq!(got, json!({"token": "ya29.ben@acme.example-gmail", "expiry": "soon", "scopes": ["gmail.readonly"]}),
                   "a connected account's granted scopes reach the turn");
        std::fs::remove_dir_all(dir).ok();
    }
}
