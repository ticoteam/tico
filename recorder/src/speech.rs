//! Where speech goes. "Your Tico" (`tico`): the default Tico relays transcription, live text and
//! assist to its AI Gateway (`/api/v2/recorder/...`) with the person's own sign-in, so nobody needs
//! keys of their own. "Own keys" (`own`): OpenRouter and xAI keys saved in the keychain, called
//! directly. Unset follows whether a Tico is connected; startup settles it once for an install
//! that already has its own keys (`settle`), so they keep working as before.
use crate::{
    network::{self, OLD_TICO},
    settings::{self, Destination, Settings},
};
use anyhow::{Context, Result, ensure};
use serde::Serialize;
use std::{
    collections::BTreeMap,
    path::Path,
    sync::Mutex,
    time::{Duration, Instant},
};

pub const TICO: &str = "tico";
pub const OWN: &str = "own";
/// The relay's routes on a Tico: `{RELAY}/audio/transcriptions`, `{RELAY}/chat/completions`,
/// `{RELAY}/stream`.
const RELAY: &str = "/api/v2/recorder";
/// How long a services answer is used before asking again.
pub const FRESH: Duration = Duration::from_secs(180);

/// What the Tico can do for the recorder now (`GET /api/v2/recorder/services`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize)]
pub struct Services {
    pub transcribe: bool,
    pub assist: bool,
    pub stream: bool,
}

/// The source Settings chose: `tico` or `own`. Unset is `tico` once a Tico is connected.
pub fn source(config: &Settings) -> &'static str {
    match config.speech_source.as_str() {
        TICO => TICO,
        OWN => OWN,
        _ if config.destinations.is_empty() => OWN,
        _ => TICO,
    }
}

/// The Tico that "Your Tico" means: the default connection.
pub fn tico(config: &Settings) -> Option<&Destination> {
    config.destinations.first()
}

/// The source to save for an install that never chose one: `own` when it already has its own
/// keys (so they keep being used), else nothing (unset follows the connection).
pub fn settled(config: &Settings, own_keys: bool) -> Option<&'static str> {
    (config.speech_source.is_empty() && own_keys).then_some(OWN)
}

/// Saves `settled` once. `own_keys` is asked only when the source is unset, since it reads the
/// keychain.
pub fn settle(root: &Path, own_keys: impl FnOnce() -> bool) -> Result<bool> {
    let mut config = settings::read(root)?;
    if !config.speech_source.is_empty() || !root.join("settings.json").exists() {
        return Ok(false);
    }
    let Some(chosen) = settled(&config, own_keys()) else {
        return Ok(false);
    };
    config.speech_source = chosen.into();
    settings::write(root, &config)?;
    Ok(true)
}

/// Whether this install has keys of its own (transcription or live text) in the keychain.
pub fn own_keys() -> bool {
    ["transcription", crate::stream::KEY_NAME]
        .iter()
        .any(|name| settings::secret(name).is_ok_and(|key| !key.is_empty()))
}

/// How soon to ask again after the Tico could not be asked.
const RETRY: Duration = Duration::from_secs(15);

/// The last answer per connection, and when it came: what it can do, or why it could not say.
static KNOWN: Mutex<BTreeMap<String, (Instant, Result<Services, String>)>> =
    Mutex::new(BTreeMap::new());

/// The last services answer for this connection, however old; None before the first. A Tico
/// that could not be asked can do nothing for now.
pub fn known(destination: &Destination) -> Option<Services> {
    KNOWN
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .get(&destination.id)
        .map(|(_, answer)| answer.clone().unwrap_or_default())
}

/// Remembers what a Tico can do (also how tests and fixtures stand in for one).
pub fn remember(destination: &Destination, answer: Result<Services, String>) {
    KNOWN
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .insert(destination.id.clone(), (Instant::now(), answer));
}

/// Asks the Tico what it can do now, and remembers the answer. A Tico without the relay
/// answers as one that can do nothing.
pub fn refresh(destination: &Destination) -> Result<Services> {
    let result = crate::signin::token(&destination.id).and_then(|token| fetch(destination, &token));
    let answer = match &result {
        Ok(services) => Ok(*services),
        Err(error) if error.chain().any(|cause| cause.to_string() == OLD_TICO) => {
            Ok(Services::default())
        }
        Err(error) => Err(format!("{error:#}")),
    };
    remember(destination, answer);
    result
}

pub fn fetch(destination: &Destination, token: &str) -> Result<Services> {
    let reply = network::Tico::new(destination.clone(), token.into())?.call(
        &format!("{RELAY}/services"),
        None,
        "",
    )?;
    Ok(Services {
        transcribe: reply["transcribe"] == true,
        assist: reply["assist"] == true,
        stream: reply["stream"] == true,
    })
}

/// A recent answer, asking again when the last is older than `FRESH` (`RETRY` after a failure).
/// For background threads: it can wait on the network.
pub fn current(destination: &Destination) -> Result<Services> {
    let recent = KNOWN
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .get(&destination.id)
        .filter(|(at, answer)| at.elapsed() < if answer.is_ok() { FRESH } else { RETRY })
        .map(|(_, answer)| answer.clone());
    match recent {
        Some(Ok(services)) => Ok(services),
        Some(Err(error)) => Err(anyhow::anyhow!(error)),
        None => refresh(destination),
    }
}

/// Settings and bearer for transcription: the Tico relay with the person's token, or the saved
/// endpoint with the saved key.
pub fn transcription(config: &Settings) -> Result<(Settings, String)> {
    transcription_with(config, crate::signin::token)
}
/// `transcription`, with the person's token from `token` (a connection id in, its token out).
pub fn transcription_with(
    config: &Settings,
    token: impl FnOnce(&str) -> Result<String>,
) -> Result<(Settings, String)> {
    if source(config) == OWN {
        ensure!(
            !config.transcription_model.trim().is_empty(),
            "Choose a transcription model in Settings. Audio is saved locally."
        );
        let key = settings::secret("transcription")?;
        ensure!(
            !key.is_empty(),
            "Add a transcription API key in Settings, or use your Tico. Audio is saved locally."
        );
        return Ok((config.clone(), key));
    }
    let destination =
        tico(config).context("Sign in to Tico in Settings. Audio is saved locally.")?;
    let services = current(destination).map_err(|error| plain(error, "transcribe"))?;
    ensure!(
        services.transcribe,
        "Your Tico can't transcribe right now. Audio is saved locally."
    );
    Ok((relay(config, destination)?, token(&destination.id)?))
}

/// Settings and bearer for drafting and private questions.
pub fn assist(config: &Settings) -> Result<(Settings, String)> {
    if source(config) == OWN {
        return Ok((config.clone(), settings::secret("transcription")?));
    }
    let destination = tico(config).context("Sign in to Tico in Settings")?;
    let services = current(destination).map_err(|error| plain(error, "answer"))?;
    ensure!(services.assist, "Your Tico can't answer right now");
    Ok((
        relay(config, destination)?,
        crate::signin::token(&destination.id)?,
    ))
}

/// The settings that send transcription and chat through the Tico relay. The model asks for
/// Scribe v2 by its own name, so it still gets word timings and speaker labels.
fn relay(config: &Settings, destination: &Destination) -> Result<Settings> {
    Ok(Settings {
        transcription_url: format!("{}{RELAY}", settings::safe_url(&destination.base_url)?),
        transcription_model: settings::DEFAULT_MODEL.into(),
        ..config.clone()
    })
}

/// The relay's live text address: `wss://` for an HTTPS Tico, `ws://` only for one on this
/// computer (which `safe_url` already limits plain HTTP to).
pub fn stream_url(destination: &Destination) -> Result<String> {
    let base = settings::safe_url(&destination.base_url)?;
    let socket = match base.split_once("://") {
        Some(("https", rest)) => format!("wss://{rest}"),
        Some(("http", rest)) => format!("ws://{rest}"),
        _ => anyhow::bail!("Use HTTPS, or HTTP on localhost"),
    };
    settings::safe_stream_url(&format!("{socket}{RELAY}/stream"))
}

/// A Tico relay failure in plain words, keeping the HTTP status when there is one:
/// "Your Tico can't transcribe right now — HTTP 503". Nothing from the request (which carries
/// the token) is in it.
pub fn plain(error: anyhow::Error, what: &str) -> anyhow::Error {
    let text = format!("{error:#}");
    if text.contains(OLD_TICO) {
        return anyhow::anyhow!("Your Tico can't {what} yet. Update Tico, or use your own keys.");
    }
    match status(&text) {
        Some(code) => anyhow::anyhow!("Your Tico can't {what} right now — HTTP {code}"),
        None if text.contains("Sign in") || text.contains("sign-in") => error,
        None => anyhow::anyhow!("Your Tico can't {what} right now — it could not be reached"),
    }
}

/// The status in "… HTTP 503 …".
fn status(text: &str) -> Option<u16> {
    let (_, rest) = text.split_once("HTTP ")?;
    rest.get(..3)?.parse().ok()
}

/// What the window shows about speech: `{"source","stream","transcribe","tico"}`. `source` is
/// `none` when neither path can transcribe (no Tico for `tico`, no key for `own`). `own_key`
/// (a transcription key is saved) and `own_stream` (live text on, with an xAI key) describe Own
/// keys; Your Tico is described by its last services answer.
pub fn view(config: &Settings, own_key: bool, own_stream: bool) -> serde_json::Value {
    let name = tico(config).map(|row| row.name.clone()).unwrap_or_default();
    let (source, transcribe, stream) = match (source(config), tico(config)) {
        (TICO, Some(destination)) => {
            let services = known(destination).unwrap_or_default();
            (
                TICO,
                services.transcribe,
                services.stream && config.streaming != Some(false),
            )
        }
        (OWN, _) if own_key => (OWN, true, own_stream),
        _ => ("none", false, false),
    };
    serde_json::json!({"source": source, "stream": stream, "transcribe": transcribe, "tico": name})
}
