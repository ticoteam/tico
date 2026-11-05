use crate::{
    settings::{self, Destination, Settings},
    store::{Delivery, Recording, Store, Word},
    transcript,
    workspace::Context as MeetingContext,
};
use anyhow::{Context, Result, ensure};
use base64::Engine;
use reqwest::blocking::Client;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::Read,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};

pub(crate) fn client() -> Result<Client> {
    static HTTP: OnceLock<Client> = OnceLock::new();
    if let Some(client) = HTTP.get() {
        return Ok(client.clone());
    }
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(8))
        .timeout(Duration::from_secs(60))
        .user_agent("Tico-Client/0.2.0")
        .build()?;
    Ok(HTTP.get_or_init(|| client).clone())
}
/// A Tico without an endpoint the recorder uses answers 404 or 405 with no Tico error code.
pub const OLD_TICO: &str = "This Tico doesn't support this yet. Update Tico to use it.";
pub(crate) fn response(response: reqwest::blocking::Response) -> Result<Value> {
    let status = response.status();
    let mut bytes = Vec::new();
    response.take(2_000_001).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= 2_000_000,
        "The server response was too large"
    );
    if status.is_redirection() {
        // Cloudflare Access sends token requests to its login page; the runner hostname lets them through.
        anyhow::bail!(
            "This address sends sign-ins to a login page. Use your Tico's runner address, like https://runner.your-tico.example"
        );
    }
    if !status.is_success() {
        let detail = serde_json::from_slice::<Value>(&bytes).ok();
        let code = detail
            .as_ref()
            .and_then(|value| value["error"]["code"].as_str())
            .unwrap_or("request_failed");
        if matches!(status.as_u16(), 404 | 405) && code == "request_failed" {
            anyhow::bail!("{OLD_TICO}");
        }
        // Tico's own words, for a refusal the person acts on there.
        if code == "already_sent"
            && let Some(detail) = detail
                .as_ref()
                .and_then(|value| value["error"]["detail"].as_str())
        {
            anyhow::bail!("{detail}");
        }
        if code == "meeting_router" {
            anyhow::bail!(
                "The Tico meeting-question model profile is not configured on this server"
            );
        }
        anyhow::bail!(
            "Server returned HTTP {} ({code}). Check the connection or retry.",
            status.as_u16()
        );
    }
    Ok(serde_json::from_slice(&bytes).context("The server returned invalid JSON")?)
}
pub fn transcribe_audio(config: &Settings, key: &str, bytes: &[u8]) -> Result<String> {
    Ok(transcribe_words(config, key, bytes, false)?.0)
}
/// OpenRouter provider options that turn on a model's own speaker labels, when it has them.
fn speaker_options(model: &str) -> Option<Value> {
    let (provider, _) = model.split_once('/')?;
    match provider {
        "elevenlabs" => Some(json!({"elevenlabs":{"diarize":true}})),
        _ => None,
    }
}
/// Text plus word timings (milliseconds from the start of `bytes`). Timings feed echo removal,
/// turn order and speaker labels. A provider that rejects word timings (HTTP 400) is asked for
/// segment timings, then for text only; segments stand in for words. With `speakers`, models that
/// can also label speakers within the audio do, and each word's label goes in `turn`.
pub fn transcribe_words(
    config: &Settings,
    key: &str,
    bytes: &[u8],
    speakers: bool,
) -> Result<(String, Vec<Word>)> {
    ensure!(
        !key.is_empty() && !config.transcription_model.trim().is_empty(),
        "Choose a transcription model and credential"
    );
    let url = format!(
        "{}/audio/transcriptions",
        settings::safe_url(&config.transcription_url)?
    );
    let mut plain = json!({"model":config.transcription_model,"input_audio":{"data":base64::engine::general_purpose::STANDARD.encode(bytes),"format":"wav"}});
    let language = config.transcription_language.trim();
    if !language.is_empty() {
        plain["language"] = json!(language);
    }
    let mut segments = plain.clone();
    segments["response_format"] = json!("verbose_json");
    let mut words = segments.clone();
    words["timestamp_granularities"] = json!(["word"]);
    let mut requests = vec![];
    if let Some(options) = speaker_options(&config.transcription_model).filter(|_| speakers) {
        let mut labelled = words.clone();
        labelled["provider"] = json!({ "options": options });
        requests.push(labelled);
    }
    requests.extend([words, segments, plain]);
    let send = |body: &Value| response(client()?.post(&url).bearer_auth(key).json(body).send()?);
    let mut reply = send(&requests[0]);
    for body in &requests[1..] {
        match &reply {
            Err(error) if error.to_string().contains("HTTP 400") => reply = send(body),
            _ => break,
        }
    }
    let reply = reply?;
    let text = reply
        .get("text")
        .and_then(Value::as_str)
        .context("Transcription response had no text")?
        .trim()
        .to_owned();
    let ms = |value: &Value| (value.as_f64().unwrap_or(0.).max(0.) * 1000.).round() as u64;
    let timed = |list: &str, field: &str| -> Vec<Word> {
        reply
            .get(list)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|item| {
                let text = item.get(field)?.as_str()?.trim();
                (!text.is_empty()).then(|| Word {
                    start_ms: ms(&item["start"]),
                    end_ms: ms(&item["end"]),
                    text: text.into(),
                    speaker: String::new(),
                    turn: match item.get("speaker") {
                        Some(Value::String(label)) => label.clone(),
                        Some(Value::Number(label)) => label.to_string(),
                        _ => String::new(),
                    },
                })
            })
            .collect()
    };
    let mut words = timed("words", "word");
    if words.is_empty() {
        words = timed("segments", "text");
    }
    Ok((text, words))
}
pub fn hash(value: &Value) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).expect("JSON"))
    )[..24]
        .into()
}
/// Transcribes the next queued chunk; false when nothing is queued.
pub fn transcribe_next(store: &Store, config: &Settings, key: &str) -> Result<bool> {
    transcribe_next_with(store, config, key, |error| error)
}
/// `transcribe_next`, with a failure put in words for where the audio went (`speech::plain`).
pub fn transcribe_next_with(
    store: &Store,
    config: &Settings,
    key: &str,
    describe: impl Fn(anyhow::Error) -> anyhow::Error,
) -> Result<bool> {
    let Some(chunk) = store.claim_transcription()? else {
        return Ok(false);
    };
    let result = (|| -> Result<(String, Vec<Word>)> {
        let bytes = std::fs::read(store.audio_path(&chunk)?)?;
        transcribe_words(config, key, &bytes, chunk.channel != "me").map_err(&describe)
    })();
    let (text, mut words, error) = match result {
        Ok((text, words)) => (text, words, String::new()),
        Err(error) => (String::new(), vec![], error.to_string()),
    };
    for word in &mut words {
        word.start_ms += chunk.start_ms;
        word.end_ms += chunk.start_ms;
    }
    let outcome = if !error.is_empty() {
        "error"
    } else if text.is_empty() {
        "no_speech"
    } else {
        "transcribed"
    };
    store.transcription_words(
        &chunk,
        &text,
        words,
        &error,
        json!({"model":config.transcription_model,"outcome":outcome}),
    )?;
    Ok(true)
}

pub struct Transcriber {
    stop: Arc<AtomicBool>,
    status: Arc<Mutex<String>>,
    store: Option<Store>,
}
/// With nothing queued, workers sleep until the library changes, and check again after this.
const IDLE_WAIT: Duration = Duration::from_secs(60);
/// Audio queued but waiting for a retry time: check again this often.
const RETRY_WAIT: Duration = Duration::from_secs(5);
impl Transcriber {
    pub fn disabled() -> Self {
        Self {
            stop: Arc::new(AtomicBool::new(true)),
            status: Arc::new(Mutex::new(String::new())),
            store: None,
        }
    }
    pub fn status(&self) -> String {
        self.status
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
    }
    /// Workers wake when the library changes (a saved chunk, a retry, `Store::poke` after
    /// Settings change) instead of polling. Settings are read when there is audio to send.
    pub fn start(store: Store) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let status = Arc::new(Mutex::new(String::new()));
        for index in 0..2 {
            let store = store.clone();
            let stop = stop.clone();
            let status = status.clone();
            thread::Builder::new()
                .name(format!("tico-transcription-{index}"))
                .spawn(move || {
                    crate::qos::utility();
                    let mut cached: Option<(u64, Settings)> = None;
                    while !stop.load(Ordering::Relaxed) {
                        let mark = store.changes();
                        let mut queued = false;
                        let result = (|| -> Result<bool> {
                            let count: i64 = store.db()?.query_row(
                                "SELECT COUNT(*) FROM chunks WHERE status='pending'",
                                [],
                                |row| row.get(0),
                            )?;
                            if count == 0 {
                                status
                                    .lock()
                                    .unwrap_or_else(|error| error.into_inner())
                                    .clear();
                                return Ok(false);
                            }
                            queued = true;
                            let version = store.settings_version();
                            if cached.as_ref().is_none_or(|(seen, _)| *seen != version) {
                                cached = Some((version, settings::read(store.root())?));
                            }
                            let config = &cached.as_ref().expect("settings read").1;
                            // Your Tico, or the saved endpoint and key.
                            let (config, key) = crate::speech::transcription(config)?;
                            *status.lock().unwrap_or_else(|error| error.into_inner()) =
                                String::new();
                            if crate::speech::source(&config) == crate::speech::TICO {
                                transcribe_next_with(&store, &config, &key, |error| {
                                    crate::speech::plain(error, "transcribe")
                                })
                            } else {
                                transcribe_next(&store, &config, &key)
                            }
                        })();
                        if let Err(error) = &result {
                            *status.lock().unwrap_or_else(|error| error.into_inner()) =
                                error.to_string();
                        }
                        match result {
                            Ok(true) => {}
                            // Queued audio not yet due for a retry is checked again soon.
                            Ok(false) if queued => {
                                store.wait_changes(mark, RETRY_WAIT);
                            }
                            _ => {
                                store.wait_changes(mark, IDLE_WAIT);
                            }
                        }
                    }
                })
                .expect("transcription worker");
        }
        #[cfg(feature = "diarize")]
        {
            let stop = stop.clone();
            let store = store.clone();
            thread::Builder::new()
                .name("tico-speakers".into())
                .spawn(move || {
                    crate::qos::background();
                    while !stop.load(Ordering::Relaxed) {
                        let mark = store.changes();
                        let Ok(Some(id)) = store.needs_speakers() else {
                            store.wait_changes(mark, IDLE_WAIT);
                            continue;
                        };
                        let _ = store.mutate(&id, |record| {
                            record.speakers = "running".into();
                            Ok(())
                        });
                        let outcome = match crate::diarize::label(&store, &id) {
                            Ok(_) => "done".to_owned(),
                            Err(error) => format!("Speakers not found: {error}"),
                        };
                        let _ = store.mutate(&id, |record| {
                            // New words while labelling reset this to "", which runs it again.
                            if record.speakers == "running" {
                                record.speakers = outcome;
                            }
                            Ok(())
                        });
                        crate::qos::release_memory();
                    }
                })
                .expect("speaker worker");
        }
        Self {
            stop,
            status,
            store: Some(store),
        }
    }
}
impl Drop for Transcriber {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(store) = &self.store {
            store.poke();
        }
    }
}

pub struct Tico {
    pub destination: Destination,
    token: String,
    client: Client,
    browser: std::sync::Mutex<Option<String>>,
    oauth: bool,
}
impl Tico {
    pub fn saved(config: &Settings, id: &str) -> Result<Self> {
        let destination = config
            .destinations
            .iter()
            .find(|entry| entry.id == id)
            .context("Choose a Tico connection")?
            .clone();
        let token = crate::signin::token(id)?;
        let mut tico = Self::new(destination, token)?;
        tico.oauth = !settings::secret(&format!("tico-grant:{id}"))?.is_empty();
        Ok(tico)
    }
    pub fn new(destination: Destination, token: String) -> Result<Self> {
        settings::safe_url(&destination.base_url)?;
        ensure!(!token.is_empty(), "Sign in to Tico in Settings");
        Ok(Self {
            destination,
            token,
            client: client()?,
            browser: std::sync::Mutex::new(None),
            oauth: false,
        })
    }
    pub fn call(&self, path: &str, body: Option<&Value>, key: &str) -> Result<Value> {
        ensure!(
            path.starts_with('/') && !path.starts_with("//"),
            "Invalid Tico endpoint"
        );
        let url = format!(
            "{}{}",
            settings::safe_url(&self.destination.base_url)?,
            path
        );
        let request = match body {
            Some(body) => self
                .client
                .post(url)
                .header("Idempotency-Key", key)
                .json(body),
            None => self.client.get(url),
        };
        response(request.bearer_auth(&self.token).send()?)
            .with_context(|| format!("Tico request {path} failed"))
    }
    pub fn unlink_meeting_task(&self, meeting: &str, task: &str, key: &str) -> Result<Value> {
        let mut url = reqwest::Url::parse(&settings::safe_url(&self.destination.base_url)?)?;
        url.path_segments_mut()
            .map_err(|_| anyhow::anyhow!("Invalid Tico address"))?
            .extend(["api", "v2", "meetings", meeting, "tasks", task]);
        let reply = self
            .client
            .delete(url)
            .bearer_auth(&self.token)
            .header("Idempotency-Key", key)
            .send()?;
        if reply.status() == reqwest::StatusCode::NOT_FOUND {
            // A queued unlink also finishes when its target disappears before the request.
            Ok(json!({"results":[{"status":404}]}))
        } else {
            response(reply)
        }
    }
    /// The events a Tico event stream (SSE) has now: `(id, type, payload)` until its first
    /// keep-alive, which follows what was already there. The stream is then closed.
    pub fn events(&self, path: &str) -> Result<Vec<(u64, String, Value)>> {
        self.read_events(path, false)
    }
    /// A shared panel refetches as soon as an invalidation arrives, without waiting for a quiet stream.
    pub fn events_until_change(&self, path: &str) -> Result<Vec<(u64, String, Value)>> {
        self.read_events(path, true)
    }
    fn read_events(&self, path: &str, until_change: bool) -> Result<Vec<(u64, String, Value)>> {
        ensure!(
            path.starts_with('/') && !path.starts_with("//"),
            "Invalid Tico endpoint"
        );
        let url = format!(
            "{}{}",
            settings::safe_url(&self.destination.base_url)?,
            path
        );
        let reply = self
            .client
            .get(url)
            .bearer_auth(&self.token)
            .header("Accept", "text/event-stream")
            .send()
            .with_context(|| format!("Tico request {path} failed"))?;
        if !reply.status().is_success() {
            return response(reply)
                .map(|_| vec![])
                .with_context(|| format!("Tico request {path} failed"));
        }
        let mut out = vec![];
        let (mut id, mut kind, mut data) = (0, String::new(), String::new());
        // Bounded: a few hundred events at most come before the keep-alive.
        for line in std::io::BufRead::lines(std::io::BufReader::new(reply.take(4_000_000))) {
            let line = line?;
            if line.starts_with(':') {
                break;
            }
            if line.is_empty() {
                if !kind.is_empty() {
                    let payload = serde_json::from_str(&data).unwrap_or(Value::Null);
                    out.push((id, std::mem::take(&mut kind), payload));
                    if until_change
                        && out.last().is_some_and(|(_, kind, _)| {
                            !matches!(kind.as_str(), "ready" | "cursor")
                        })
                    {
                        break;
                    }
                }
                data.clear();
                continue;
            }
            match line
                .split_once(':')
                .map(|(field, value)| (field, value.trim_start()))
            {
                Some(("id", value)) => id = value.parse().unwrap_or(id),
                Some(("event", value)) => kind = value.into(),
                Some(("data", value)) => data.push_str(value),
                _ => {}
            }
        }
        if kind == "expired" || out.iter().any(|(_, kind, _)| kind == "expired") {
            anyhow::bail!("Sign in to Tico again in Settings");
        }
        Ok(out)
    }
    /// Meeting routes under `/api/v2`, the only API a runner hostname routes. A Tico from before
    /// they moved answers there as an old Tico; then the same request goes to the old `/api` path.
    pub fn call_meeting(&self, path: &str, body: Option<&Value>, key: &str) -> Result<Value> {
        match self.call(&format!("/api/v2{path}"), body, key) {
            Err(error) if error.chain().any(|cause| cause.to_string() == OLD_TICO) => {
                self.call(&format!("/api{path}"), body, key)
            }
            result => result,
        }
    }
    /// The server supplies the browser address; an OAuth runner host may serve only API routes.
    pub fn browser_url(&self) -> Result<String> {
        if let Some(browser) = self.browser.lock().unwrap().clone() {
            return Ok(browser);
        }
        let response = self.call("/api/v2/recorder/browser", None, "")?;
        let browser = settings::safe_url(
            response["url"]
                .as_str()
                .context("Update Tico to copy meeting links")?,
        )?;
        let remote = reqwest::Url::parse(&self.destination.base_url)?;
        let target = reqwest::Url::parse(&browser)?;
        let local = |u: &reqwest::Url| {
            ["localhost", "127.0.0.1", "[::1]", "::1"].contains(&u.host_str().unwrap_or(""))
        };
        ensure!(
            local(&remote) || !local(&target),
            "Tico returned a local browser address for a remote connection"
        );
        *self.browser.lock().unwrap() = Some(browser.clone());
        Ok(browser)
    }
    fn task_browser_url(&self) -> Result<String> {
        match self.browser_url() {
            Ok(browser) => Ok(browser),
            Err(error) if error.chain().any(|cause| cause.to_string() == OLD_TICO) => {
                if self.oauth {
                    Ok(String::new())
                } else {
                    settings::safe_url(&self.destination.base_url)
                }
            }
            Err(error) => Err(error).context("Could not resolve Tico links; the task was not sent"),
        }
    }
    pub fn verify(&self) -> Result<Value> {
        let who = self.call("/api/v2/me", None, "")?;
        let actor = who["actor"].as_str().unwrap_or("");
        ensure!(
            actor.starts_with("human:"),
            "Use a human personal token for this Tico"
        );
        ensure!(
            self.destination.actor.is_empty() || self.destination.actor == actor,
            "This Tico token belongs to a different person"
        );
        Ok(who)
    }
    /// People and the bots this person may give work to, from the team chart.
    pub fn roster(&self) -> Result<Vec<(String, String)>> {
        let org = self.call("/api/v2/org?can=write", None, "")?;
        let mut result = vec![];
        for row in org["people"].as_array().into_iter().flatten() {
            if row["left_at"].is_null()
                && let Some(id) = row["id"].as_str()
            {
                let name = row["name"].as_str().filter(|name| !name.is_empty());
                result.push((
                    format!("human:{id}"),
                    name.or(row["email"].as_str()).unwrap_or(id).into(),
                ));
            }
        }
        for row in org["bots"].as_array().into_iter().flatten() {
            if row["status"]
                .as_str()
                .is_none_or(|state| state.is_empty() || state == "active")
                && let Some(slug) = row["id"].as_str()
            {
                let name = row["display_name"].as_str().filter(|name| !name.is_empty());
                result.push((
                    format!("bot:{slug}"),
                    name.or(row["name"].as_str()).unwrap_or(slug).into(),
                ));
            }
        }
        Ok(result)
    }
    pub fn calendar(&self) -> Result<Value> {
        let who = self.verify()?;
        let email = who["email"]
            .as_str()
            .context("Tico did not return your calendar email")?;
        let mut query = reqwest::Url::parse("https://localhost/")?;
        query.query_pairs_mut().append_pair("calendar", email);
        self.call(
            &format!(
                "/api/v2/calendar/appointments?{}",
                query.query().unwrap_or("")
            ),
            None,
            "",
        )
    }
    pub fn send(&self, store: &Store, id: &str, push_items: bool) -> Result<String> {
        self.send_to(store, id, push_items, "")
    }
    /// Files the meeting. `audience` is `team` (everyone in the org reads it), `participants` (only
    /// the recorder and the people added to or joined from its live share; owners too are held to
    /// it) or empty (private to the recorder, as before). A Tico too old to know `participants`
    /// refuses the request, so nothing is shared wider than chosen.
    pub fn send_to(
        &self,
        store: &Store,
        id: &str,
        push_items: bool,
        audience: &str,
    ) -> Result<String> {
        let _guard = store.live_lock();
        ensure!(
            ["", "team", "participants"].contains(&audience),
            "Unknown audience"
        );
        self.verify()?;
        let record = store.get(id)?;
        let context = store.context(id)?;
        ready(&record)?;
        ensure!(
            !context.live_discarded || context.live_removed,
            "This meeting was discarded; its recording stays local"
        );
        ensure!(
            context.live_id.is_empty() || context.live_state == "ended",
            "Finish the connected live meeting first"
        );
        // Items already sent to a bot are tasks; the rest go along as the meeting's proposed items.
        let selected: Vec<_> = context
            .items
            .iter()
            .filter(|item| item.kind != "link")
            .collect();
        if push_items
            && selected
                .iter()
                .any(|item| item.kind == "task" && item.task_id.is_empty())
        {
            let roster = self.roster()?;
            for item in &selected {
                if item.kind == "task" && item.task_id.is_empty() {
                    ensure!(
                        roster.iter().any(|entry| entry.0 == item.owner),
                        "Choose a verified owner for: {}",
                        item.title
                    );
                }
            }
        }
        for item in &selected {
            // A task typed by hand has no quote; a suggested one must still match the transcript
            // it was drafted from.
            ensure!(
                crate::assist::quote_supported(item, &record),
                "The source quote is missing from the transcript: {}",
                item.title
            );
        }
        let previous = record
            .deliveries
            .iter()
            .find(|entry| entry.destination_id == self.destination.id);
        let fresh = import_body(&record, &context, audience);
        // A retry sends the same body (and so the same idempotency key) unless the audience changed.
        let body = previous
            .and_then(|entry| entry.receipt.get("import_body"))
            .filter(|cached| {
                cached["private"] == fresh["private"] && cached["audience"] == fresh["audience"]
            })
            .cloned()
            .unwrap_or(fresh);
        let mut delivery = previous.cloned().unwrap_or(Delivery {
            destination_id: self.destination.id.clone(),
            meeting_id: String::new(),
            status: "sending".into(),
            error: String::new(),
            receipt: json!({}),
        });
        delivery.receipt["import_body"] = body.clone();
        delivery.status = "sending".into();
        delivery.error.clear();
        store.delivery(id, &delivery)?;
        let result = (|| -> Result<String> {
            let live_source = ["live", "legacy-live"]
                .contains(&delivery.receipt["source"].as_str().unwrap_or(""));
            if live_source && delivery.receipt["dismissed"] == true {
                self.call(
                    &format!("/api/v2/meetings/{}/review", delivery.meeting_id),
                    Some(&json!({"action":"restore"})),
                    &format!("recorder:{id}:restore-local"),
                )?;
                delivery.receipt["dismissed"] = json!(false);
                store.delivery(id, &delivery)?;
            }
            let chosen = delivery.receipt["audience"].as_str().unwrap_or("") == audience;
            if !live_source && (delivery.meeting_id.is_empty() || !chosen) {
                let reply = self
                    .call(
                        "/api/v2/meetings/import",
                        Some(&body),
                        &format!("recorder:{id}:import:{}", hash(&body)),
                    )
                    .map_err(|error| refused_audience(error, audience))?;
                ensure!(
                    reply["status"] != "deleted",
                    "This meeting was deleted in Tico"
                );
                delivery.meeting_id = reply["id"]
                    .as_str()
                    .context("Tico did not return a meeting ID")?
                    .into();
                delivery.receipt["audience"] = json!(audience);
                store.delivery(id, &delivery)?;
            }
            // A retry after an approval applies the audience chosen now: Tico re-files an approved meeting for it.
            if live_source
                && (delivery.receipt["approved"] != true
                    || delivery.receipt["audience"] != audience)
            {
                // The Recorder's own save skips the review queue, filed for who was chosen.
                let (mut approve, mut key) = match audience {
                    "team" => (
                        json!({"action":"approve","private":false}),
                        "approve:team".to_owned(),
                    ),
                    "participants" => (
                        json!({"action":"approve","private":true,"audience":"participants"}),
                        "approve:participants".to_owned(),
                    ),
                    _ => (
                        json!({"action":"approve","private":true}),
                        "approve".to_owned(),
                    ),
                };
                // Approved before for another audience: name this one, so Tico re-files it (a first approval
                // leaves it out for the Team, which an older Tico would refuse).
                if delivery.receipt["approved"] == true {
                    let refiles = delivery.receipt["refiles"].as_u64().unwrap_or(0) + 1;
                    delivery.receipt["refiles"] = json!(refiles);
                    approve["audience"] = json!(match audience {
                        "team" | "participants" => audience,
                        _ => "private",
                    });
                    key = format!("{key}:refile:{refiles}");
                }
                let reviewed = self
                    .call(
                        &format!("/api/v2/meetings/{}/review", delivery.meeting_id),
                        Some(&approve),
                        &format!("recorder:{id}:{key}"),
                    )
                    .map_err(|error| refused_audience(error, audience))?;
                if let Some(title) = reviewed["title"].as_str() {
                    delivery.receipt["tico_title"] = json!(title);
                }
                delivery.receipt["approved"] = json!(true);
                delivery.receipt["audience"] = json!(audience);
                store.delivery(id, &delivery)?;
            }
            // A live meeting is filed from its shared transcript; my notes become its log.
            let notes = context
                .finalize_payload
                .as_ref()
                .and_then(|frozen| frozen["notes"].as_str())
                .unwrap_or(&context.notes)
                .trim()
                .to_owned();
            // It is filed with the live room's title, so a title changed here goes with the notes.
            let save_notes = !notes.is_empty() && delivery.receipt["notes_saved"] != true;
            let save_title = live_source
                && delivery.receipt["tico_title"]
                    .as_str()
                    .is_some_and(|title| title != record.title);
            if (delivery.receipt["source"] == "live" && save_notes) || save_title {
                let meeting = self.call(
                    &format!("/api/v2/meetings/{}", delivery.meeting_id),
                    None,
                    "",
                )?;
                let mut edit = json!({"version":meeting["version"].as_u64().unwrap_or(1)});
                if delivery.receipt["source"] == "live" && save_notes {
                    edit["note"] = json!(notes);
                }
                if save_title {
                    edit["title"] = json!(record.title);
                }
                self.call_meeting(
                    &format!("/meetings/{}/edit", delivery.meeting_id),
                    Some(&edit),
                    &format!("recorder:{id}:edit:{}", hash(&edit)),
                )?;
                if edit.get("note").is_some() {
                    delivery.receipt["notes_saved"] = json!(true);
                }
                if save_title {
                    delivery.receipt["tico_title"] = json!(record.title);
                }
                store.delivery(id, &delivery)?;
            }
            let path = format!("/meetings/{}/items", delivery.meeting_id);
            let listing = if selected.is_empty() {
                json!({})
            } else {
                self.call_meeting(&path, None, "")?
            };
            for item in selected {
                let payload = self.item_payload(item);
                let section = payload["section"].as_str().unwrap();
                let detail = &payload["detail"];
                let at_ms = payload["at_ms"].clone();
                let mut item_id = delivery.receipt["items"][&item.id]["id"]
                    .as_str()
                    .unwrap_or("")
                    .to_owned();
                let mut item_version = delivery.receipt["items"][&item.id]["version"]
                    .as_u64()
                    .unwrap_or(1);
                if item_id.is_empty() && delivery.receipt["uploads"][&item.id].is_null() {
                    if let Some(existing) = listing["sections"][section]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|row| {
                            row["text"] == item.title
                                && row["quote"] == item.quote
                                && row["at_ms"] == json!(at_ms)
                                && row["detail"] == *detail
                                && row["status"] != "dismissed"
                        })
                    {
                        item_id = existing["id"].as_str().unwrap_or("").into();
                        item_version = existing["version"].as_u64().unwrap_or(1);
                    }
                }
                let upload = delivery.receipt["uploads"][&item.id]["body"].clone();
                let baseline = delivery.receipt["items"][&item.id].clone();
                let mut server_row = listing["sections"][section]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .find(|row| row["id"] == item_id)
                    .cloned();
                if item_id.is_empty() {
                    let reply = self.upload_item(store, id, &mut delivery, &item.id, &payload)?;
                    server_row = Some(reply["item"].clone());
                    item_version = reply["item"]["version"].as_u64().unwrap_or(1);
                    item_id = reply["item"]["id"]
                        .as_str()
                        .context("Tico did not return an item ID")?
                        .into();
                } else if !item.task_id.is_empty() {
                    // A task can be created after only part of Save reached Tico. Reconcile
                    // its existing row before marking the delivery sent, including after a lost reply.
                    let current = listing["sections"][section]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|row| row["id"] == item_id);
                    if let Some(row) = current {
                        item_version = row["version"].as_u64().unwrap_or(item_version);
                    }
                    if !current.is_some_and(|row| {
                        row["status"] == "pushed" && row["result_ref"] == item.task_id
                    }) {
                        let reconcile = json!({"version":item_version,"result_ref":item.task_id});
                        let reply = self.call_meeting(
                            &format!("{path}/{item_id}/push"),
                            Some(&reconcile),
                            &format!(
                                "recorder:{id}:{}:reconcile:{item_id}:{}",
                                item.id,
                                hash(&reconcile)
                            ),
                        )?;
                        ensure!(
                            reply["item"]["result_ref"] == item.task_id,
                            "Tico did not return the created task reference"
                        );
                        item_version = reply["item"]["version"].as_u64().unwrap_or(item_version);
                    }
                }
                if item.task_id.is_empty() {
                    let expected = if !upload.is_null() {
                        &upload
                    } else {
                        &baseline
                    };
                    let changed = if !upload.is_null() {
                        upload["text"] != payload["text"] || upload["detail"] != payload["detail"]
                    } else {
                        !baseline["text"].is_null()
                            && (baseline["text"] != payload["text"]
                                || baseline["detail"] != payload["detail"])
                    };
                    if changed {
                        let version = expected["version"].as_u64().unwrap_or(1);
                        let mut detail = payload["detail"].clone();
                        if section == "task" {
                            detail["due"] = json!(item.due);
                            detail["body"] = json!(item.body);
                        }
                        let edit =
                            json!({"version":version,"text":payload["text"],"detail":detail});
                        let moved = server_row.as_ref().is_some_and(|row| {
                            row["status"] != "proposed"
                                || row["version"].as_u64().unwrap_or(1) != version
                        });
                        let edited = if moved {
                            None
                        } else {
                            match self.call_meeting(
                                &format!("{path}/{item_id}"),
                                Some(&edit),
                                &format!("recorder:{id}:{}:edit:{}", item.id, hash(&edit)),
                            ) {
                                Ok(reply) => Some(reply["item"].clone()),
                                Err(error)
                                    if error.to_string().contains("(version_conflict)")
                                        || error.to_string().contains("(status)") =>
                                {
                                    None
                                }
                                Err(error) => return Err(error),
                            }
                        };
                        if let Some(row) = edited {
                            item_version = row["version"].as_u64().unwrap_or(version + 1);
                            server_row = Some(row);
                        } else {
                            // Fetch after a raced edit so the closed card names the current actor and state.
                            let current = self.call_meeting(&path, None, "")?;
                            let row = current["sections"][section]
                                .as_array()
                                .into_iter()
                                .flatten()
                                .find(|row| row["id"] == item_id)
                                .cloned()
                                .or(server_row.clone())
                                .context("This item was removed from the meeting")?;
                            store.update_context(id, |c| {
                                if let Some(local) = c.items.iter_mut().find(|local| local.id == item.id) {
                                    local.closed_draft = Some(json!({"item":row,"text":item.title,"detail":payload["detail"]}));
                                }
                                Ok(())
                            })?;
                            server_row = Some(row);
                        }
                    }
                }
                delivery.receipt["items"][&item.id] = if item.task_id.is_empty() {
                    json!({"id":item_id,"status":"proposed","version":item_version})
                } else {
                    json!({"id":item_id,"status":"pushed","result_ref":item.task_id,"version":item_version})
                };
                if let Some(row) = server_row {
                    delivery.receipt["items"][&item.id] = row;
                }
                store.delivery(id, &delivery)?;
                if push_items && item.task_id.is_empty() {
                    let reply = self.call_meeting(
                        &format!("{path}/{item_id}/push"),
                        Some(&json!({"version":item_version})),
                        &format!("recorder:{id}:{}:push:{item_id}", item.id),
                    )?;
                    ensure!(
                        reply["item"]["result_ref"].as_str().is_some(),
                        "Tico did not return the created task reference"
                    );
                    delivery.receipt["items"][&item.id] = reply["item"].clone();
                    store.delivery(id, &delivery)?;
                }
            }
            // Links need an approved meeting and follow every durable item receipt.
            self.save_links(store, id, &context, &mut delivery)?;
            delivery.status = "sent".into();
            delivery.receipt["sent_at"] = json!(chrono::Utc::now().to_rfc3339());
            store.delivery(id, &delivery)?;
            store.update_context(id, |c| {
                c.kept_on_mac = false;
                c.live_discarded = false;
                c.live_removed = false;
                Ok(())
            })?;
            Ok(delivery.meeting_id.clone())
        })();
        if let Err(error) = &result {
            delivery.status = "failed".into();
            delivery.error = error.to_string();
            store.delivery(id, &delivery)?;
        }
        result
    }

    pub fn upload_pending_item(&self, store: &Store, id: &str, item_id: &str) -> Result<()> {
        let _guard = store.live_lock();
        let context = store.context(id)?;
        ensure!(!context.kept_on_mac, "This meeting stays on this Mac");
        let item = context
            .items
            .iter()
            .find(|item| item.id == item_id && item.kind != "link")
            .context("This item was removed")?;
        let mut delivery = store
            .get(id)?
            .deliveries
            .into_iter()
            .find(|entry| {
                entry.destination_id == self.destination.id && crate::live::saved_in_tico(entry)
            })
            .context("Save the meeting first")?;
        if delivery.receipt["items"][item_id]["id"].is_null() {
            self.upload_item(store, id, &mut delivery, item_id, &self.item_payload(item))?;
        }
        Ok(())
    }

    fn item_payload(&self, item: &crate::workspace::Item) -> Value {
        let section = if item.kind == "doc" { "doc" } else { "task" };
        let detail = if section == "doc" {
            json!({"document":item.document,"change":item.change,"why":"Proposed from the meeting transcript"})
        } else {
            let mut detail = json!({"owner":item.owner});
            if !item.due.is_empty() {
                detail["due"] = json!(item.due);
            }
            if !item.body.is_empty() {
                detail["body"] = json!(item.body);
            }
            detail
        };
        let mut payload = json!({"section":section,"text":item.title,"quote":item.quote,
            "at_ms":(!item.quote.is_empty()).then_some(item.at_ms),"detail":detail});
        if !item.proposed_task_id.is_empty()
            && (item.task_destination.is_empty() || item.task_destination == self.destination.id)
        {
            payload["proposed_task_id"] = json!(item.proposed_task_id);
        }
        if !item.task_id.is_empty() {
            payload["status"] = json!("pushed");
            payload["result_ref"] = json!(item.task_id);
        }
        payload
    }

    fn upload_item(
        &self,
        store: &Store,
        id: &str,
        delivery: &mut Delivery,
        item_id: &str,
        payload: &Value,
    ) -> Result<Value> {
        // Persist the exact request before sending: a lost reply must replay this upload.
        if delivery.receipt["uploads"][item_id].is_null() {
            delivery.receipt["uploads"][item_id] = json!({
                "body":payload,
                "key":format!("recorder:{id}:{item_id}:item:{}", hash(payload)),
            });
            store.delivery(id, delivery)?;
        }
        let upload = &delivery.receipt["uploads"][item_id];
        let reply = self.call_meeting(
            &format!("/meetings/{}/items", delivery.meeting_id),
            Some(&upload["body"]),
            upload["key"].as_str().context("Missing item upload key")?,
        )?;
        ensure!(
            reply["item"]["id"].as_str().is_some(),
            "Tico did not return an item ID"
        );
        delivery.receipt["items"][item_id] = reply["item"].clone();
        delivery.receipt["uploads"]
            .as_object_mut()
            .unwrap()
            .remove(item_id);
        store.delivery(id, delivery)?;
        Ok(reply)
    }
}
impl Tico {
    /// Sends one action item as a task for `owner` (`bot:<slug>` or `human:<id>`), now: during the
    /// meeting or after it. The meeting stays with this Tico from then on. Sending twice is a no-op.
    pub fn send_task(
        &self,
        store: &Store,
        id: &str,
        item_id: &str,
        owner: &str,
        owner_name: &str,
        private: bool,
    ) -> Result<()> {
        self.send_task_confirmed(store, id, item_id, owner, owner_name, private, false)
    }
    /// Explicit review can include the selected quote; surrounding transcript always excludes pauses.
    #[allow(clippy::too_many_arguments)]
    pub fn send_task_confirmed(
        &self,
        store: &Store,
        id: &str,
        item_id: &str,
        owner: &str,
        owner_name: &str,
        private: bool,
        paused_confirmed: bool,
    ) -> Result<()> {
        let _guard = store.live_lock();
        let context = store.context(id)?;
        if let Some(reason) = crate::live::item_send_blocked(&context) {
            anyhow::bail!(reason);
        }
        let who = self.verify()?;
        let record = store.get(id)?;
        let context = store.context(id)?;
        ensure!(
            context.live_destination.is_empty() || context.live_destination == self.destination.id,
            "This meeting belongs to another Tico"
        );
        // A remote control can have changed since the window last refreshed. Discover it
        // before constructing an export from local speech, under the same control lock.
        if !context.live_id.is_empty() {
            let view = self.call(
                &format!("/api/v2/live-meetings/{}", context.live_id),
                None,
                "",
            )?;
            crate::live::note_state(store, id, &view)?;
        }
        let context = store.context(id)?;
        if let Some(reason) = crate::live::item_send_blocked(&context) {
            anyhow::bail!(reason);
        }
        let item = context
            .items
            .iter()
            .find(|item| item.id == item_id)
            .context("This task was removed")?
            .clone();
        if !item.task_id.is_empty() {
            return Ok(());
        }
        ensure!(
            paused_confirmed
                || context.paused_windows.is_empty()
                || (item.quote.trim().is_empty() && item.title.trim().is_empty()),
            "This task may quote paused speech. Review its title and quote, then choose Create anyway"
        );
        let record = crate::live::shareable(&record, &context.paused_windows);
        ensure!(
            owner.starts_with("bot:") || owner.starts_with("human:"),
            "Choose an owner"
        );
        let mut filed = record
            .deliveries
            .iter()
            .find(|entry| {
                entry.destination_id == self.destination.id && !entry.meeting_id.is_empty()
            })
            .cloned();
        if let Some(entry) = filed.as_mut()
            && !entry.receipt["uploads"][item_id].is_null()
        {
            self.upload_item(store, id, entry, item_id, &Value::Null)?;
        }
        if let Some(entry) = filed.as_mut()
            && entry.receipt["items"][item_id]["id"].as_str().is_none()
            && (entry.status == "sent" || crate::live::saved_in_tico(entry))
        {
            self.upload_item(store, id, entry, item_id, &self.item_payload(&item))?;
        }
        let base = self.task_browser_url()?;
        let link = if base.is_empty() {
            String::new()
        } else {
            match (filed.as_ref(), context.live_id.is_empty()) {
                (Some(entry), _) => format!("{base}/#/meetings?meeting={}", entry.meeting_id),
                (None, false) => format!("{base}/#/meetings/live/{}", context.live_id),
                (None, true) => String::new(),
            }
        };
        let sender = who["name"]
            .as_str()
            .or(who["email"].as_str())
            .unwrap_or("me");
        let mut body = json!({"title":item.title.chars().take(300).collect::<String>(),
            "body":crate::assist::task_body(&record, &item, sender, &link),"owner":owner,"private":private});
        if !item.due.is_empty() {
            body["due"] = json!(item.due);
        }
        if !context.live_id.is_empty() {
            body["live_meeting_id"] = json!(context.live_id);
        }
        let (reply, adopted) = if let Some(entry) = filed
            .as_ref()
            .filter(|entry| entry.receipt["items"][item_id]["id"].as_str().is_some())
        {
            let server_item = entry.receipt["items"][item_id]["id"]
                .as_str()
                .context("This item is not saved on the meeting")?;
            let result = self.call(
                &format!(
                    "/api/v2/meetings/{}/items/{server_item}/push",
                    entry.meeting_id
                ),
                Some(&json!({"version":entry.receipt["items"][item_id]["version"].as_u64().unwrap_or(1),"text":item.title,"detail":{"owner":owner,"due":item.due,"body":item.body},"private":private})),
                &format!("recorder:{id}:push:{server_item}:{}", hash(&body)),
            )?;
            let adopted = (result["pushed"] == false).then(|| result["item"].clone());
            (json!({"task":{"id":result["item"]["result_ref"]}}), adopted)
        } else {
            (
                self.call(
                    "/api/v2/tasks",
                    Some(&body),
                    &format!("recorder:{id}:task:{item_id}:{}", hash(&body)),
                )?,
                None,
            )
        };
        let task = reply["task"]["id"]
            .as_str()
            .context("Tico did not return the new task")?
            .to_owned();
        store.update_context(id, |current| {
            current.live_destination = self.destination.id.clone();
            if let Some(item) = current.items.iter_mut().find(|item| item.id == item_id) {
                if let Some(server) = adopted
                    && (server["text"] != item.title || server["detail"]["owner"] != owner
                        || server["detail"]["due"].as_str().unwrap_or("") != item.due
                        || server["detail"]["body"].as_str().unwrap_or("") != item.body)
                {
                    item.closed_draft =
                        Some(json!({"item":server,"text":item.title,"detail":{"owner":owner,"due":item.due,"body":item.body}}));
                }
                item.task_url = if base.is_empty() {
                    String::new()
                } else {
                    format!("{base}/#/task/{task}")
                };
                item.task_id = task;
                item.owner = owner.into();
                item.sent_to = owner_name.into();
            }
            Ok(())
        })?;
        Ok(())
    }
}
pub fn ready(record: &Recording) -> Result<()> {
    ensure!(record.state != "recording", "Stop recording first");
    ensure!(
        record
            .chunks
            .iter()
            .all(|chunk| ["done", "silence"].contains(&chunk.status.as_str())),
        "Finish or retry transcription first"
    );
    ensure!(
        record
            .chunks
            .iter()
            .any(|chunk| !chunk.text.trim().is_empty()),
        "This recording has no transcript to send"
    );
    Ok(())
}
/// A Tico that refuses `audience` (HTTP 422) is older than "Participants only": nothing was filed.
fn refused_audience(error: anyhow::Error, audience: &str) -> anyhow::Error {
    // Narrowing after the transcript went into a task: Tico says where to change it.
    if let Some(sent) = error
        .chain()
        .map(|cause| cause.to_string())
        .find(|cause| cause.starts_with("Already sent to "))
    {
        return anyhow::anyhow!(sent);
    }
    if audience == "participants" && format!("{error:#}").contains("HTTP 422") {
        anyhow::anyhow!(
            "This Tico can't save for participants only yet, so nothing was shared. Update Tico or save it for the org."
        )
    } else {
        error
    }
}
fn import_body(record: &Recording, context: &MeetingContext, audience: &str) -> Value {
    let mut body = json!({"title":record.title,"source":"tico-recorder","external_id":record.id,"started_at":record.started_at,"duration_seconds":record.chunks.iter().map(|chunk|chunk.end_ms).max().unwrap_or(0).div_ceil(1000),"private":audience != "team","review":"live","notes":context.notes,"participants":context.participants,"transcript":transcript::lines(&record).iter().map(|line|json!({"speaker":line.speaker,"start_ms":line.start_ms,"end_ms":line.end_ms,"text":line.text})).collect::<Vec<_>>()});
    // Tico takes calendar attendees as participants; it has no calendar field.
    let attendees = context.calendar["attendees"]
        .as_array()
        .into_iter()
        .flatten();
    for attendee in attendees.filter_map(|row| row.as_str().or(row["email"].as_str())) {
        let participants = body["participants"].as_array_mut().expect("participants");
        if participants.len() < 100 && !participants.contains(&json!(attendee)) {
            participants.push(json!(attendee));
        }
    }
    if audience == "participants" {
        body["audience"] = json!("participants");
    }
    body
}

#[cfg(test)]
#[path = "network_tests.rs"]
mod tests;
