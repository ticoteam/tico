use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

/// Settings and credentials share a generation because workers depend on both.
static VERSION: AtomicU64 = AtomicU64::new(0);

pub fn version() -> u64 {
    VERSION.load(Ordering::SeqCst)
}

fn changed() {
    VERSION.fetch_add(1, Ordering::SeqCst);
    crate::store::settings_changed();
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Destination {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub actor: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub microphone: String,
    pub meeting_audio: bool,
    pub transcription_url: String,
    pub transcription_model: String,
    /// Spoken language code such as "en"; empty lets the model guess per chunk, which turns
    /// short sounds like "mm-hmm" into other languages.
    pub transcription_language: String,
    pub destinations: Vec<Destination>,
    pub calendar_connection: String,
    /// Chat model, through the same provider and key, that drafts action items and answers
    /// private questions.
    pub assist_model: String,
    /// Live text from a streaming transcription service while someone talks. Unset means on
    /// when an xAI key is saved (keychain account `xai`). The chunked transcript stays the record.
    pub streaming: Option<bool>,
    pub streaming_url: String,
    pub streaming_model: String,
    /// Where speech goes (`speech::source`): "tico" through the default Tico, "own" with the
    /// keys saved here. Empty follows whether a Tico is connected.
    pub speech_source: String,
}
/// Best on the meeting benchmark (`bench/README.md`) and returns word timings, which echo removal
/// and speaker labels need.
pub const DEFAULT_MODEL: &str = "elevenlabs/scribe-v2";
/// Fast and cheap enough to review new speech every half minute.
pub const ASSIST_MODEL: &str = "anthropic/claude-haiku-5.5";
/// Earlier default; it returns text without word timings, so saved settings move to the new one.
const OLD_DEFAULT_MODEL: &str = "meta/muse-voice-transcribe-1.0";
impl Default for Settings {
    fn default() -> Self {
        Self {
            microphone: String::new(),
            meeting_audio: true,
            transcription_url: "https://openrouter.ai/api/v1".into(),
            transcription_model: DEFAULT_MODEL.into(),
            transcription_language: "en".into(),
            destinations: vec![],
            calendar_connection: String::new(),
            assist_model: ASSIST_MODEL.into(),
            streaming: None,
            streaming_url: crate::stream::DEFAULT_URL.into(),
            streaming_model: crate::stream::DEFAULT_MODEL.into(),
            speech_source: String::new(),
        }
    }
}
pub fn root() -> PathBuf {
    if let Some(path) = std::env::var_os("TICO_RECORDER_DATA_DIR") {
        return path.into();
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    #[cfg(target_os = "macos")]
    {
        // Kept from the earlier build (product name "Tico Recorder Native") so existing libraries and
        // keychain entries still open after the rename to "Tico Recorder". Do not rename this folder.
        home.join("Library/Application Support/Tico Recorder Native")
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or(home)
            .join("Tico Recorder")
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".local/share"))
            .join("tico-recorder")
    }
}
pub fn read(root: &Path) -> Result<Settings> {
    match fs::read(root.join("settings.json")) {
        Ok(bytes) => {
            let mut settings: Settings = serde_json::from_slice(&bytes)?;
            if settings.transcription_model == OLD_DEFAULT_MODEL {
                settings.transcription_model = DEFAULT_MODEL.into();
            }
            Ok(settings)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Settings::default()),
        Err(error) => Err(error.into()),
    }
}
pub fn write(root: &Path, settings: &Settings) -> Result<()> {
    safe_url(&settings.transcription_url)?;
    ensure!(
        ["", crate::speech::TICO, crate::speech::OWN].contains(&settings.speech_source.as_str()),
        "Choose Your Tico or Own keys"
    );
    if settings.streaming != Some(false) {
        safe_stream_url(&settings.streaming_url)?;
    }
    for destination in &settings.destinations {
        safe_url(&destination.base_url)?;
    }
    let temporary = root.join("settings.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(settings)?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))?;
    }
    fs::rename(temporary, root.join("settings.json"))?;
    changed();
    Ok(())
}
pub fn safe_url(value: &str) -> Result<String> {
    let url = reqwest::Url::parse(value.trim()).context("Enter a valid server URL")?;
    ensure!(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        "Server URL must not contain credentials, a query or a fragment"
    );
    ensure!(
        url.scheme() == "https"
            || url.scheme() == "http"
                && ["localhost", "127.0.0.1", "[::1]", "::1"]
                    .contains(&url.host_str().unwrap_or("")),
        "Use HTTPS, or HTTP on localhost"
    );
    Ok(url.as_str().trim_end_matches('/').into())
}
/// A streaming endpoint: `wss://`, or `ws://` on this computer (tests and local gateways).
pub fn safe_stream_url(value: &str) -> Result<String> {
    let url = reqwest::Url::parse(value.trim()).context("Enter a valid streaming URL")?;
    ensure!(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        "Streaming URL must not contain credentials, a query or a fragment"
    );
    ensure!(
        url.scheme() == "wss"
            || url.scheme() == "ws"
                && ["localhost", "127.0.0.1", "[::1]", "::1"]
                    .contains(&url.host_str().unwrap_or("")),
        "Use wss://, or ws:// on localhost"
    );
    Ok(url.as_str().trim_end_matches('/').into())
}
/// Secrets read this launch. Each keychain read can ask the person to allow it, so read once.
static SECRETS: std::sync::Mutex<std::collections::BTreeMap<String, String>> =
    std::sync::Mutex::new(std::collections::BTreeMap::new());
/// The keychain service. An isolated library (TICO_RECORDER_DATA_DIR) may name its own with
/// TICO_RECORDER_KEYCHAIN_SERVICE, so profiling and test runs never touch the real credentials.
fn service() -> String {
    std::env::var("TICO_RECORDER_KEYCHAIN_SERVICE")
        .ok()
        .filter(|name| !name.is_empty() && std::env::var_os("TICO_RECORDER_DATA_DIR").is_some())
        .unwrap_or_else(|| "team.tico.recorder.native".into())
}
pub fn secret(name: &str) -> Result<String> {
    let mut cache = SECRETS.lock().unwrap_or_else(|error| error.into_inner());
    if let Some(value) = cache.get(name) {
        return Ok(value.clone());
    }
    let value = match keyring::Entry::new(&service(), name)?.get_password() {
        Ok(value) => value,
        Err(keyring::Error::NoEntry) => String::new(),
        Err(error) => return Err(error.into()),
    };
    cache.insert(name.into(), value.clone());
    Ok(value)
}
/// Uses `value` as a secret for this run only, without the keychain, for headless tests that
/// sign in with a token from the environment. Only in an isolated library.
pub fn hold_secret(name: &str, value: &str) -> Result<()> {
    ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Use an isolated TICO_RECORDER_DATA_DIR"
    );
    SECRETS
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .insert(name.into(), value.into());
    changed();
    Ok(())
}
pub fn save_secret(name: &str, value: &str) -> Result<()> {
    let entry = keyring::Entry::new(&service(), name)?;
    if value.is_empty() {
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(error) => return Err(error.into()),
        }
    } else {
        entry
            .set_password(value)
            .context("Could not save the credential in the system keychain")?;
    }
    SECRETS
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .insert(name.into(), value.into());
    changed();
    Ok(())
}

/// The owner keeps this alive before performing recovery or starting background workers.
pub struct AppLock(std::fs::File);
impl AppLock {
    pub fn acquire(root: &Path) -> Result<Self> {
        fs::create_dir_all(root)?;
        let file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join("app.lock"))?;
        fs2::FileExt::try_lock_exclusive(&file)
            .context("Tico Recorder is already using this library")?;
        Ok(Self(file))
    }
}
impl Drop for AppLock {
    fn drop(&mut self) {
        let _ = fs2::FileExt::unlock(&self.0);
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A subprocess isolates the keyring backend, environment and generation from parallel tests.
    pub(crate) fn isolated(name: &str) -> bool {
        if std::env::var("TICO_SETTINGS_TEST_CHILD").as_deref() == Ok(name) {
            keyring::set_default_credential_builder(keyring::mock::default_credential_builder());
            return false;
        }
        let dir = tempfile::tempdir().unwrap();
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args([name, "--exact", "--nocapture"])
            .env("TICO_SETTINGS_TEST_CHILD", name)
            .env("TICO_RECORDER_DATA_DIR", dir.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        true
    }

    #[test]
    fn settings_reload_every_writer_advances_and_wakes_open_libraries() {
        if isolated(
            "settings::tests::settings_reload_every_writer_advances_and_wakes_open_libraries",
        ) {
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let first = crate::store::Store::open(dir.path()).unwrap();
        let second = crate::store::Store::open(dir.path()).unwrap();
        let saved = Settings::default();
        let grant = crate::signin::Grant {
            client_id: "fixture".into(),
            token_url: "https://acme.example/api/v2/oauth/token".into(),
            refresh_token: "fixture-refresh".into(),
            expires_at: i64::MAX,
        };
        let writers: Vec<Box<dyn Fn() -> Result<()>>> = vec![
            Box::new(|| write(dir.path(), &saved)),
            Box::new(|| save_secret("settings-reload", "saved")),
            Box::new(|| save_secret("settings-reload", "")),
            Box::new(|| hold_secret("settings-reload", "held")),
            Box::new(|| crate::signin::save("settings-reload", "access", &grant)),
            Box::new(|| {
                crate::signin::sign_out(&Destination {
                    id: "personal-token".into(),
                    ..Default::default()
                })
            }),
        ];
        for write in writers {
            let before = version();
            let marks = [first.changes(), second.changes()];
            let data = first.version();
            write().unwrap();
            assert!(version() > before);
            assert_eq!(first.settings_version(), version());
            for (store, mark) in [&first, &second].into_iter().zip(marks) {
                assert!(store.wait_changes(mark, std::time::Duration::ZERO) > mark);
            }
            assert_eq!(first.version(), data, "settings do not change library data");
        }
        assert_eq!(secret("settings-reload").unwrap(), "held");
        assert_eq!(crate::signin::token("settings-reload").unwrap(), "access");
        let before = version();
        let mut invalid = saved.clone();
        invalid.transcription_url = "http://acme.example".into();
        assert!(write(dir.path(), &invalid).is_err());
        assert!(write(&dir.path().join("missing"), &saved).is_err());
        assert_eq!(
            version(),
            before,
            "failed saves do not publish a generation"
        );
    }

    #[test]
    fn refusing_unsafe_destinations_and_holding_one_library_lock() {
        assert!(safe_url("http://localhost.evil.test").is_err());
        assert!(safe_url("https://user:pass@example.com").is_err());
        assert!(safe_url("http://127.0.0.1:7000").is_ok());
        assert!(safe_stream_url("wss://api.x.ai/v1/stt").is_ok());
        assert!(safe_stream_url("ws://127.0.0.1:9000/v1/stt").is_ok());
        assert!(safe_stream_url("ws://api.x.ai/v1/stt").is_err());
        assert!(safe_stream_url("wss://api.x.ai/v1/stt?key=1").is_err());
        let dir = tempfile::tempdir().unwrap();
        let lock = AppLock::acquire(dir.path()).unwrap();
        assert!(AppLock::acquire(dir.path()).is_err());
        drop(lock);
        assert!(AppLock::acquire(dir.path()).is_ok());
    }
}
