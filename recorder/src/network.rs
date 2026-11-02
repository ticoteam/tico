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
        Self::new(destination, token)
    }
    pub fn new(destination: Destination, token: String) -> Result<Self> {
        settings::safe_url(&destination.base_url)?;
        ensure!(!token.is_empty(), "Sign in to Tico in Settings");
        Ok(Self {
            destination,
            token,
            client: client()?,
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
    /// The events a Tico event stream (SSE) has now: `(id, type, payload)` until its first
    /// keep-alive, which follows what was already there. The stream is then closed.
    pub fn events(&self, path: &str) -> Result<Vec<(u64, String, Value)>> {
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
        self.verify()?;
        let record = store.get(id)?;
        let context = store.context(id)?;
        ready(&record)?;
        ensure!(
            !context.live_discarded,
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
            .filter(|item| item.task_id.is_empty())
            .collect();
        if push_items && selected.iter().any(|item| item.kind == "task") {
            let roster = self.roster()?;
            for item in &selected {
                if item.kind == "task" {
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
        let body = previous
            .and_then(|entry| entry.receipt.get("import_body"))
            .cloned()
            .unwrap_or_else(|| import_body(&record, &context));
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
            if delivery.meeting_id.is_empty() {
                let reply = self.call(
                    "/api/v2/meetings/import",
                    Some(&body),
                    &format!("recorder:{id}:import:{}", hash(&body)),
                )?;
                ensure!(
                    reply["status"] != "deleted",
                    "This meeting was deleted in Tico"
                );
                delivery.meeting_id = reply["id"]
                    .as_str()
                    .context("Tico did not return a meeting ID")?
                    .into();
                store.delivery(id, &delivery)?;
            }
            if ["live", "legacy-live"].contains(&delivery.receipt["source"].as_str().unwrap_or(""))
                && delivery.receipt["approved"] != true
            {
                self.call(
                    &format!("/api/v2/meetings/{}/review", delivery.meeting_id),
                    Some(&json!({"action":"approve","private":true})),
                    &format!("recorder:{id}:approve"),
                )?;
                delivery.receipt["approved"] = json!(true);
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
            if delivery.receipt["source"] == "live"
                && !notes.is_empty()
                && delivery.receipt["notes_saved"] != true
            {
                let meeting = self.call(
                    &format!("/api/v2/meetings/{}", delivery.meeting_id),
                    None,
                    "",
                )?;
                self.call_meeting(
                    &format!("/meetings/{}/edit", delivery.meeting_id),
                    Some(&json!({"version":meeting["version"].as_u64().unwrap_or(1),"note":notes})),
                    &format!("recorder:{id}:notes"),
                )?;
                delivery.receipt["notes_saved"] = json!(true);
                store.delivery(id, &delivery)?;
            }
            let path = format!("/meetings/{}/items", delivery.meeting_id);
            let listing = if selected.is_empty() {
                json!({})
            } else {
                self.call_meeting(&path, None, "")?
            };
            for item in selected {
                let section = if item.kind == "doc" { "doc" } else { "task" };
                let detail = if section == "doc" {
                    json!({"document":item.document,"change":item.change,"why":"Proposed from the meeting transcript"})
                } else {
                    json!({"owner":item.owner})
                };
                let at_ms = (!item.quote.is_empty()).then_some(item.at_ms);
                let payload = json!({"section":section,"text":item.title,"quote":item.quote,"at_ms":at_ms,"detail":detail});
                let mut item_id = delivery.receipt["items"][&item.id]["id"]
                    .as_str()
                    .unwrap_or("")
                    .to_owned();
                if item_id.is_empty() {
                    if let Some(existing) = listing["sections"][section]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|row| {
                            row["text"] == item.title
                                && row["quote"] == item.quote
                                && row["at_ms"] == json!(at_ms)
                                && row["detail"] == detail
                                && row["status"] != "dismissed"
                        })
                    {
                        item_id = existing["id"].as_str().unwrap_or("").into();
                    }
                }
                if item_id.is_empty() {
                    let reply = self.call_meeting(
                        &path,
                        Some(&payload),
                        &format!("recorder:{id}:{}:item:{}", item.id, hash(&payload)),
                    )?;
                    item_id = reply["item"]["id"]
                        .as_str()
                        .context("Tico did not return an item ID")?
                        .into();
                }
                delivery.receipt["items"][&item.id] = json!({"id":item_id,"status":"proposed"});
                store.delivery(id, &delivery)?;
                if push_items {
                    let reply = self.call_meeting(
                        &format!("{path}/{item_id}/push"),
                        Some(&json!({})),
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
            delivery.status = "sent".into();
            delivery.receipt["sent_at"] = json!(chrono::Utc::now().to_rfc3339());
            store.delivery(id, &delivery)?;
            Ok(delivery.meeting_id.clone())
        })();
        if let Err(error) = &result {
            delivery.status = "failed".into();
            delivery.error = error.to_string();
            store.delivery(id, &delivery)?;
        }
        result
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
            .context("This action item was removed")?
            .clone();
        if !item.task_id.is_empty() {
            return Ok(());
        }
        ensure!(
            paused_confirmed
                || context.paused_windows.is_empty()
                || (item.quote.trim().is_empty() && item.title.trim().is_empty()),
            "This action item may quote paused speech. Review its title and quote before sending"
        );
        let record = crate::live::shareable(&record, &context.paused_windows);
        ensure!(
            owner.starts_with("bot:") || owner.starts_with("human:"),
            "Choose who gets it"
        );
        let base = settings::safe_url(&self.destination.base_url)?;
        let filed = record.deliveries.iter().find(|entry| {
            entry.destination_id == self.destination.id && !entry.meeting_id.is_empty()
        });
        let link = match (filed, context.live_id.is_empty()) {
            (Some(entry), _) => format!("{base}/#/meetings?meeting={}", entry.meeting_id),
            (None, false) => format!("{base}/#/meetings?live={}", context.live_id),
            (None, true) => String::new(),
        };
        let sender = who["name"]
            .as_str()
            .or(who["email"].as_str())
            .unwrap_or("me");
        let body = json!({"title":item.title.chars().take(300).collect::<String>(),
            "body":crate::assist::task_body(&record, &item, sender, &link),"owner":owner,"private":private});
        let reply = self.call(
            "/api/v2/tasks",
            Some(&body),
            &format!("recorder:{id}:task:{item_id}:{}", hash(&body)),
        )?;
        let task = reply["task"]["id"]
            .as_str()
            .context("Tico did not return the new task")?
            .to_owned();
        store.update_context(id, |current| {
            current.live_destination = self.destination.id.clone();
            if let Some(item) = current.items.iter_mut().find(|item| item.id == item_id) {
                item.task_url = format!("{base}/#/task/{task}");
                item.task_id = task;
                item.owner = owner.into();
                item.sent_to = owner_name.into();
            }
            Ok(())
        })?;
        // Sent with the meeting earlier as a proposed item: the task replaces it there.
        if let Some(entry) = filed
            && let Some(filed_item) = entry.receipt["items"][item_id]["id"].as_str()
        {
            let _ = self.call_meeting(
                &format!("/meetings/{}/items/{filed_item}", entry.meeting_id),
                Some(&json!({"status":"dismissed"})),
                &format!("recorder:{id}:dismiss:{filed_item}"),
            );
        }
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
fn import_body(record: &Recording, context: &MeetingContext) -> Value {
    let mut body = json!({"title":record.title,"source":"tico-recorder","external_id":record.id,"started_at":record.started_at,"duration_seconds":record.chunks.iter().map(|chunk|chunk.end_ms).max().unwrap_or(0).div_ceil(1000),"private":true,"review":"live","notes":context.notes,"participants":context.participants,"transcript":transcript::lines(&record).iter().map(|line|json!({"speaker":line.speaker,"start_ms":line.start_ms,"end_ms":line.end_ms,"text":line.text})).collect::<Vec<_>>()});
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
    body
}

#[cfg(test)]
#[path = "network_tests.rs"]
mod tests;
