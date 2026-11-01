//! The recorder's state and actions. It owns the library, the recording in progress and the
//! background jobs; the window only draws `view()` and calls the actions below. Nothing here
//! knows about Tauri, so it is tested as plain Rust.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex, mpsc},
    time::{Duration, Instant},
};
use tico_recorder_native::{
    assist,
    audio::Channel,
    capture,
    engine::RecordingHandle,
    live::LiveSync,
    network::{self, Tico, Transcriber},
    notices,
    settings::{self, AppLock, Settings},
    signin, speech,
    store::{self, Recording, Store, Summary},
    stream,
    workspace::{Context, Item},
};

/// How one input has behaved during the recording in progress, for telling a real problem
/// from a blip: when it last had signal, and since when it has been losing audio.
struct InputWatch {
    nonzero: u64,
    lost: u64,
    signal_at: Instant,
    loss_since: Option<Instant>,
    loss_at: Instant,
}
/// The microphone counts as silent after this long without any signal.
const SILENT_FOR: Duration = Duration::from_secs(10);
/// Audio counts as being lost when it keeps dropping for this long.
const LOSING_FOR: Duration = Duration::from_secs(5);

/// Someone chosen in People: waiting for its turn, being added in Tico, or added and waiting for
/// the meeting's people list to show it.
#[derive(Clone, Debug)]
struct Adding {
    meeting: String,
    actor: String,
    name: String,
    step: AddStep,
    /// When Tico said yes; the row waits for the people list a little while, then goes.
    added: Option<Instant>,
}
#[derive(Clone, Copy, Debug, PartialEq)]
enum AddStep {
    Queued,
    Sending,
    Added,
}
/// How long Send waits for the last words of a stopped recording.
const SEND_WAIT: Duration = Duration::from_secs(600);
/// Jobs that run beside the person's own actions instead of disabling the controls.
/// Shown when a recording or a send is asked for while an update installs.
const UPDATING: &str = "Tico Recorder is updating. Try again in a moment.";
const BACKGROUND: [&str; 7] = [
    "live",
    "calendar",
    "autodraft",
    "task",
    "ask",
    "roster",
    "speech",
];
/// Drafting reads streamed text too, so it can follow speech closely; one model call per
/// interval at most.
pub const DRAFT_INTERVAL: Duration = Duration::from_secs(10);
/// How long a confirmation such as "Settings saved" stays in view.
const MESSAGE_FOR: Duration = Duration::from_secs(6);

struct Reply {
    kind: String,
    meeting: Option<String>,
    result: Result<Value, String>,
}
/// One conversational turn: consecutive speech from one speaker, in paragraphs.
#[derive(Debug, Serialize)]
pub struct Block {
    pub speaker: String,
    pub me: bool,
    pub start_ms: u64,
    pub paragraphs: Vec<String>,
}
/// `TICO_RECORDER_SYNTHETIC_INPUT=1` records a test tone instead of the microphone, for
/// profiling without a device. Only in an isolated library.
pub fn synthetic_input() -> bool {
    std::env::var_os("TICO_RECORDER_SYNTHETIC_INPUT").is_some()
        && std::env::var_os("TICO_RECORDER_DATA_DIR").is_some()
}
pub fn clock(ms: u64) -> String {
    let seconds = ms / 1000;
    if seconds >= 3600 {
        format!(
            "{}:{:02}:{:02}",
            seconds / 3600,
            seconds / 60 % 60,
            seconds % 60
        )
    } else {
        format!("{:02}:{:02}", seconds / 60, seconds % 60)
    }
}
/// Consecutive lines from one speaker are one block; each line is a paragraph, and a long line
/// breaks into paragraphs at sentence ends.
pub fn transcript_blocks(record: &Recording) -> Vec<Block> {
    let mut blocks: Vec<Block> = vec![];
    for line in tico_recorder_native::transcript::lines(record) {
        let mut paragraphs = vec![String::new()];
        for word in line.text.split_whitespace() {
            let paragraph = paragraphs.last_mut().expect("one paragraph");
            if paragraph.len() >= 480 && paragraph.ends_with(['.', '?', '!']) {
                paragraphs.push(word.to_owned());
            } else {
                if !paragraph.is_empty() {
                    paragraph.push(' ');
                }
                paragraph.push_str(word);
            }
        }
        match blocks.last_mut() {
            Some(block) if block.speaker == line.speaker && block.me == line.me => {
                block.paragraphs.extend(paragraphs)
            }
            _ => blocks.push(Block {
                speaker: line.speaker,
                me: line.me,
                start_ms: line.start_ms,
                paragraphs,
            }),
        }
    }
    blocks
}
type Signature = (String, String, usize, usize, usize);
fn signature(record: &Recording) -> Signature {
    (
        record.id.clone(),
        record.speakers.clone(),
        record.chunks.len(),
        record.chunks.iter().map(|chunk| chunk.text.len()).sum(),
        record
            .chunks
            .iter()
            .filter(|chunk| chunk.status == "done")
            .count(),
    )
}
pub fn sent_to(deliveries: &[store::Delivery], destination: &str) -> bool {
    deliveries
        .iter()
        .any(|row| row.destination_id == destination && row.status == "sent")
}
/// The Tico address in a `tico-recorder://connect?server=…` link.
pub fn connect_address(link: &str) -> Option<String> {
    let url = reqwest::Url::parse(link).ok()?;
    if url.scheme() != "tico-recorder" || url.host_str() != Some("connect") {
        return None;
    }
    let server = url.query_pairs().find(|(key, _)| key == "server")?.1;
    settings::safe_url(&server).ok()
}
/// The server the Tico desktop app on this Mac uses, to fill in the sign-in address.
fn tico_desktop_server() -> String {
    let Some(home) = std::env::var_os("HOME") else {
        return String::new();
    };
    std::fs::read_to_string(
        std::path::Path::new(&home).join("Library/Application Support/team.tico.app/server.txt"),
    )
    .map(|text| {
        text.trim()
            .trim_start_matches("https://")
            .trim_end_matches('/')
            .to_owned()
    })
    .unwrap_or_default()
}
/// A library row's status, only when it says something the date doesn't. Tones are
/// `red`, `amber`, `muted` and `accent`.
pub fn record_status(record: &Summary) -> Option<(String, &'static str)> {
    let (label, tone) = if record.state == "recording" {
        ("Recording".to_owned(), "red")
    } else if record.chunks == 0 {
        ("No audio".into(), "amber")
    } else if record.failed > 0 {
        ("Needs retry".into(), "amber")
    } else if record.pending > 0 {
        (format!("Transcribing {}", record.pending), "muted")
    } else if record.silent == record.chunks {
        ("No signal".into(), "amber")
    } else if record.deliveries.iter().any(|entry| entry.status == "sent") {
        ("In Tico".into(), "accent")
    } else if record
        .deliveries
        .iter()
        .any(|entry| entry.status == "failed")
    {
        ("Send failed".into(), "amber")
    } else if record.live_state == "live" {
        ("Live in Tico".into(), "accent")
    } else {
        return None;
    };
    Some((label, tone))
}

/// What the window already has, by part, so `view` only sends what changed.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct Have {
    pub library: String,
    pub meeting: String,
    pub transcript: String,
    pub settings: String,
}
/// The editable part of Settings, as the window's form sends it.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct SettingsForm {
    pub microphone: String,
    pub meeting_audio: bool,
    pub transcription_url: String,
    pub transcription_model: String,
    pub transcription_language: String,
    pub calendar: bool,
    /// A new transcription key; empty keeps the saved one.
    pub key: String,
    /// Live text while recording. None, or the current effective value, leaves the setting as
    /// it is (unset follows whether an xAI key is saved).
    pub streaming: Option<bool>,
    /// Empty uses the default streaming model.
    pub streaming_model: String,
    /// A new xAI key for live text; empty keeps the saved one.
    pub xai_key: String,
    /// "tico" (Your Tico) or "own" (Own keys); None leaves it as it is.
    pub speech_source: Option<String>,
}

pub struct Recorder {
    pub store: Store,
    _lock: Option<AppLock>,
    transcriber: Transcriber,
    pub live_sync: Option<LiveSync>,
    pub config: Settings,
    pub records: Vec<Summary>,
    current: Option<Recording>,
    blocks: (Signature, Vec<Block>),
    pub meeting: Option<String>,
    pub context: Context,
    active: Option<(String, RecordingHandle)>,
    /// Recordings made in this session; only these are drafted automatically.
    recorded_here: BTreeSet<String>,
    refresh: Instant,
    refresh_library: Instant,
    last_sync: Instant,
    last_calendar: Instant,
    last_draft: Instant,
    draft_wait: Duration,
    tx: mpsc::Sender<Reply>,
    rx: mpsc::Receiver<Reply>,
    pub busy: bool,
    /// An update is installing (update.rs): recordings and sends don't start until it is done.
    pub updating: bool,
    background: BTreeSet<String>,
    message: (String, Instant),
    pub error: String,
    draft_status: String,
    chat_error: String,
    /// A question or message that failed, offered back in the input.
    chat_retry: String,
    /// The Tico whose people and bots are being chosen in People.
    pub adding: Option<String>,
    pub destination: String,
    tico_url: String,
    signing_in: bool,
    /// A Tico address from a Connect recorder link, waiting for the person to confirm.
    pub connect: Option<String>,
    microphones: Vec<String>,
    pub calendar: Value,
    pub live: Value,
    pub roster: Vec<(String, String)>,
    chat_private: bool,
    asking: String,
    asking_for: Option<String>,
    pub demo: bool,
    /// The action item being sent to Tico right now.
    sending: Option<String>,
    /// People and bots chosen in People, shown at once and added in Tico in the background.
    adding_people: Vec<Adding>,
    /// Why Tico refused the last people or bots added; shown in People.
    people_error: String,
    /// The meeting being sent and the step it is on, written by the send job as it goes.
    send_step: Arc<Mutex<Option<(String, String)>>>,
    /// The action item whose send failed, and why.
    item_error: Option<(String, String)>,
    /// Who the last action item went to, offered first for the next one.
    last_owner: String,
    /// Choosing the Tico that action items go to.
    choosing_org: bool,
    /// The Tico whose people and bots `roster` holds.
    roster_for: String,
    /// Moves whenever what the window shows may have changed; the window redraws on a change.
    ui_version: u64,
    /// The version the window last drew.
    viewed: u64,
    /// The library version the open meeting and the library list were last read at.
    seen_open: u64,
    seen_library: u64,
    /// State that changes outside this struct (the recording, live text, workers), as last seen.
    outside: String,
    message_shown: bool,
    inputs: std::collections::BTreeMap<String, InputWatch>,
    /// The input notice the person closed; it stays closed until a different one appears.
    notice_dismissed: String,
    /// Whether the window is on screen; level updates stop while it is not.
    pub visible: bool,
    /// When the default Tico was last asked what speech it can handle, and which Tico it was.
    services_at: Option<(Instant, String)>,
}

impl Recorder {
    pub fn new(store: Store, lock: Option<AppLock>, demo: bool) -> anyhow::Result<Self> {
        // An install that already has its own keys keeps using them until the person chooses
        // Your Tico. The demo library never reads the keychain.
        if !demo && let Err(error) = speech::settle(store.root(), speech::own_keys) {
            eprintln!("Speech source unchanged: {error:#}");
        }
        let config = settings::read(store.root())?;
        let records = store.summaries()?;
        let (tx, rx) = mpsc::channel();
        let calendar = std::fs::read(store.root().join("calendar.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or(json!({}));
        let transcriber = if demo {
            Transcriber::disabled()
        } else {
            Transcriber::start(store.clone())
        };
        let live_sync = if demo {
            None
        } else {
            Some(LiveSync::start(store.clone()))
        };
        // Suggest the Tico desktop app's server only for a first connection.
        let tico_url = if config.destinations.is_empty() {
            tico_desktop_server()
        } else {
            String::new()
        };
        let mut app = Self {
            store,
            _lock: lock,
            transcriber,
            live_sync,
            destination: config
                .destinations
                .first()
                .map(|row| row.id.clone())
                .unwrap_or_default(),
            config,
            records,
            current: None,
            blocks: (Default::default(), vec![]),
            meeting: None,
            context: Context::default(),
            active: None,
            recorded_here: BTreeSet::new(),
            refresh: Instant::now(),
            refresh_library: Instant::now(),
            last_sync: Instant::now(),
            last_calendar: Instant::now(),
            last_draft: Instant::now(),
            draft_wait: DRAFT_INTERVAL,
            tx,
            rx,
            busy: false,
            updating: false,
            background: BTreeSet::new(),
            message: (String::new(), Instant::now()),
            error: String::new(),
            draft_status: String::new(),
            chat_error: String::new(),
            chat_retry: String::new(),
            adding: None,
            tico_url,
            signing_in: false,
            connect: None,
            microphones: vec![],
            calendar,
            live: json!({}),
            roster: vec![],
            chat_private: false,
            asking: String::new(),
            asking_for: None,
            demo,
            sending: None,
            send_step: Arc::default(),
            adding_people: vec![],
            people_error: String::new(),
            item_error: None,
            last_owner: String::new(),
            choosing_org: false,
            roster_for: String::new(),
            ui_version: 1,
            viewed: 0,
            seen_open: 0,
            seen_library: 0,
            outside: String::new(),
            message_shown: false,
            inputs: Default::default(),
            notice_dismissed: String::new(),
            visible: true,
            services_at: None,
        };
        if !demo {
            app.job("devices", || Ok(json!(capture::sources()?)));
        }
        Ok(app)
    }
    fn say(&mut self, message: impl Into<String>) {
        self.message = (message.into(), Instant::now());
    }
    /// Starts `work` in the background; false when a job holding the controls (or one of this
    /// kind) is already running.
    fn job(
        &mut self,
        kind: &str,
        work: impl FnOnce() -> anyhow::Result<Value> + Send + 'static,
    ) -> bool {
        let meeting = self.meeting.clone();
        self.job_for(kind, meeting, work)
    }
    fn job_for(
        &mut self,
        kind: &str,
        meeting: Option<String>,
        work: impl FnOnce() -> anyhow::Result<Value> + Send + 'static,
    ) -> bool {
        if BACKGROUND.contains(&kind) {
            if !self.background.insert(kind.into()) {
                return false;
            }
        } else if std::mem::replace(&mut self.busy, true) {
            return false;
        }
        let tx = self.tx.clone();
        let store = self.store.clone();
        let background = BACKGROUND.contains(&kind);
        let kind = kind.to_string();
        std::thread::spawn(move || {
            if background {
                tico_recorder_native::qos::utility();
            }
            let result = work().map_err(|error| format!("{error:#}"));
            let _ = tx.send(Reply {
                kind,
                meeting,
                result,
            });
            // Wakes the tick thread to handle the reply now.
            store.poke();
        });
        true
    }
    pub fn report(&mut self, result: anyhow::Result<()>) {
        if let Err(error) = result {
            self.error = error.to_string();
        }
    }
    pub fn save_context(&mut self) {
        if let Some(id) = self.meeting.clone() {
            let edited = self.context.clone();
            let result = self.store.update_context(&id, |current| {
                current.notes = edited.notes;
                // Drafts added and items sent in the background since this copy was loaded are
                // kept; removing an item goes through `remove_item`.
                let mut items = edited.items;
                for item in std::mem::take(&mut current.items) {
                    match items.iter_mut().find(|row| row.id == item.id) {
                        Some(row) if !item.task_id.is_empty() => *row = item,
                        Some(_) => {}
                        None => items.push(item),
                    }
                }
                current.items = items;
                current.participants = edited.participants;
                current.calendar = edited.calendar;
                Ok(())
            });
            self.report(result);
            if let Ok(current) = self.store.context(&id) {
                self.context = current;
            }
        }
    }
    pub fn open(&mut self, id: &str) -> anyhow::Result<()> {
        if self.meeting.as_deref() == Some(id) {
            return Ok(());
        }
        if self.meeting.is_some() {
            self.save_context();
        }
        let record = self.store.get(id)?;
        self.context = self.store.context(id).unwrap_or_default();
        self.current = Some(record);
        self.meeting = Some(id.to_owned());
        self.live = json!({});
        self.chat_error.clear();
        self.chat_retry.clear();
        self.draft_status.clear();
        self.item_error = None;
        self.chat_private = false;
        self.choosing_org = false;
        if !self.context.live_destination.is_empty() {
            self.destination = self.context.live_destination.clone();
        }
        // A shared meeting shows its people right away.
        if !self.context.live_id.is_empty() && self.context.live_state != "ended" {
            self.last_sync = Instant::now() - Duration::from_secs(5);
        }
        Ok(())
    }
    pub fn close(&mut self) {
        self.save_context();
        self.meeting = None;
        self.current = None;
        self.context = Context::default();
        self.reload();
    }
    /// Start a new recording, named after a calendar event when one is given. With a recording
    /// already running, that one opens instead. Returns the meeting to show.
    pub fn record(&mut self, event: Option<Value>) -> Option<String> {
        if let Some((id, _)) = &self.active {
            let id = id.clone();
            let _ = self.open(&id);
            return Some(id);
        }
        if self.updating {
            self.error = UPDATING.into();
            return None;
        }
        let title = event
            .as_ref()
            .and_then(|row| row["title"].as_str())
            .map(str::to_owned)
            .unwrap_or_else(|| {
                format!(
                    "Meeting {}",
                    chrono::Local::now().format("%b %-d, %-I:%M %p")
                )
            });
        match self.store.create(&title) {
            Ok(record) => {
                if let Some(event) = event {
                    let result = self.store.update_context(&record.id, |context| {
                        context.calendar = event;
                        Ok(())
                    });
                    self.report(result);
                }
                let mic = if self.config.microphone.is_empty() {
                    None
                } else {
                    Some(self.config.microphone.clone())
                };
                let started = if synthetic_input() {
                    RecordingHandle::with_sources(self.store.clone(), record.id.clone(), || {
                        Ok((
                            vec![capture::start_synthetic(Channel::Microphone)],
                            String::new(),
                        ))
                    })
                } else {
                    RecordingHandle::start(
                        self.store.clone(),
                        record.id.clone(),
                        mic,
                        self.config.meeting_audio,
                    )
                };
                match started {
                    Ok(handle) => self.active = Some((record.id.clone(), handle)),
                    Err(error) => self.error = error.to_string(),
                };
                self.recorded_here.insert(record.id.clone());
                let _ = self.open(&record.id);
                self.reload();
                Some(record.id)
            }
            Err(error) => {
                self.error = error.to_string();
                None
            }
        }
    }
    /// Stops the recording in progress; the audio worker flushes and finishes in the background.
    pub fn stop(&mut self) {
        if let Some((_, handle)) = &self.active {
            handle.stop();
        }
    }
    /// Used on quit: stop and wait for the final audio to be saved.
    pub fn shutdown(&mut self) {
        if let Some((_, handle)) = self.active.take() {
            let _ = handle.finish();
        }
        self.save_context();
    }
    #[cfg(test)]
    pub fn mark_sending(&mut self, item: Option<&str>, meeting_step: Option<&str>) {
        self.sending = item.map(str::to_owned);
        *self
            .send_step
            .lock()
            .unwrap_or_else(|error| error.into_inner()) =
            meeting_step.map(|label| ("m1".to_owned(), label.to_owned()));
    }
    #[cfg(test)]
    pub fn set_active(&mut self, id: String, handle: RecordingHandle) {
        self.active = Some((id, handle));
        self.reload();
    }
    pub fn reload(&mut self) {
        self.reload_open();
        self.seen_library = self.store.version();
        self.bump();
        match self.store.summaries() {
            Ok(records) => self.records = records,
            Err(error) => self.error = error.to_string(),
        };
        self.refresh_library = Instant::now();
    }
    /// The open meeting refreshes often; the whole library only every few seconds.
    fn reload_open(&mut self) {
        self.seen_open = self.store.version();
        if let Some(id) = &self.meeting {
            self.ui_version += 1;
            match self.store.get(id) {
                Ok(record) => self.current = Some(record),
                Err(error) => self.error = error.to_string(),
            }
        }
        self.refresh = Instant::now();
    }
    pub fn sync_error(&self, id: &str) -> String {
        self.live_sync
            .as_ref()
            .map(|worker| worker.error(id))
            .unwrap_or_default()
    }
    /// The connection whose router answers private questions and drafts for this meeting.
    pub fn router(&self) -> String {
        if self.context.live_destination.is_empty() {
            self.destination.clone()
        } else {
            self.context.live_destination.clone()
        }
    }
    pub fn router_name(&self) -> String {
        let id = self.router();
        self.config
            .destinations
            .iter()
            .find(|row| row.id == id)
            .map(|row| row.name.clone())
            .unwrap_or_default()
    }
    fn network_job(
        &mut self,
        kind: &str,
        work: impl FnOnce(Tico, Store, String) -> anyhow::Result<Value> + Send + 'static,
    ) -> bool {
        let config = self.config.clone();
        let destination = self.router();
        let store = self.store.clone();
        let id = self.meeting.clone().unwrap_or_default();
        self.job(kind, move || {
            work(Tico::saved(&config, &destination)?, store, id)
        })
    }
    fn sync(&mut self) {
        self.last_sync = Instant::now();
        self.network_job("live", |tico, store, id| {
            let view = tico.sync_live(&store, &id)?;
            // Notices are a side note: the meeting still syncs when they can't be read.
            if let (Some(live_id), Some(latest)) = (view["id"].as_str(), view["event_id"].as_u64())
                && let Err(error) = tico.read_notices(live_id, latest)
            {
                eprintln!("Meeting notices unread: {error:#}");
            }
            Ok(json!({"view":view}))
        });
    }
    /// Asks the default Tico what speech it handles: when it is connected or changes, and every
    /// few minutes while speech goes through it.
    fn refresh_services(&mut self) {
        if self.demo || speech::source(&self.config) != speech::TICO {
            return;
        }
        let Some(destination) = speech::tico(&self.config).cloned() else {
            return;
        };
        if self
            .services_at
            .as_ref()
            .is_some_and(|(at, id)| *id == destination.id && at.elapsed() < speech::FRESH)
        {
            return;
        }
        if self.job("speech", move || Ok(json!(speech::refresh(&destination)?))) {
            self.services_at = Some((
                Instant::now(),
                speech::tico(&self.config)
                    .map(|row| row.id.clone())
                    .unwrap_or_default(),
            ));
        }
    }
    fn sync_calendar(&mut self) {
        self.last_calendar = Instant::now();
        let config = self.config.clone();
        let destination = self.config.calendar_connection.clone();
        let store = self.store.clone();
        self.job("calendar", move || {
            let result = Tico::saved(&config, &destination)?.calendar()?;
            std::fs::write(
                store.root().join("calendar.json"),
                serde_json::to_vec(&result)?,
            )?;
            Ok(result)
        });
    }
    /// Private question about the open recording, answered by the assist model.
    pub fn ask(&mut self, question: &str) {
        let question = question.trim().to_owned();
        if question.is_empty() || self.background.contains("ask") || self.meeting.is_none() {
            return;
        }
        self.chat_error.clear();
        self.chat_retry.clear();
        self.asking = question.clone();
        self.asking_for = self.meeting.clone();
        let (config, store) = (self.config.clone(), self.store.clone());
        let id = self.meeting.clone().unwrap_or_default();
        self.job("ask", move || {
            let (config, key) = speech::assist(&config)?;
            assist::ask(&store, &config, &key, &id, &question)?;
            Ok(json!({}))
        });
    }
    /// A message to everyone in the shared meeting. On a meeting not in Tico yet, the first
    /// message connects it as a private meeting (only the person who sent it, plus anyone they
    /// add later); it is never opened to the team on the way.
    pub fn chat_send(&mut self, text: &str) {
        let text = text.trim().to_owned();
        if text.is_empty() || !self.can_message() || self.busy {
            return;
        }
        self.chat_error.clear();
        self.chat_retry = text.clone();
        let request = uuid::Uuid::new_v4().to_string();
        self.network_job("chat", move |tico, store, id| {
            let live_id = tico.ensure_live(&store, &id, false)?;
            let reply = tico.call(
                &format!("/api/v2/live-meetings/{live_id}/chat"),
                Some(&json!({"text":text})),
                &format!("recorder:chat:{request}"),
            )?;
            Ok(json!({"sent":reply["message"]}))
        });
    }
    /// Whether the dock can message the meeting: it is in Tico and still open, or it can be
    /// connected now.
    fn can_message(&self) -> bool {
        if !self.context.live_id.is_empty() {
            return self.context.live_state != "ended";
        }
        self.open_record()
            .map(|record| self.share_blocked(&record).is_empty())
            .unwrap_or(false)
    }
    pub fn set_chat_private(&mut self, private: bool) {
        self.chat_private = private;
    }
    /// Drafts from new speech while a recording made here is in progress or just finished.
    fn auto_draft(&mut self) {
        if self.background.contains("autodraft")
            || self.last_draft.elapsed() < self.draft_wait
            || self.demo
        {
            return;
        }
        let Some(id) = self.active.as_ref().map(|(id, _)| id.clone()).or_else(|| {
            self.meeting
                .clone()
                .filter(|id| self.recorded_here.contains(id))
        }) else {
            return;
        };
        // Checks run once per interval, not every tick, while speech accumulates.
        self.last_draft = Instant::now();
        if !assist::draft_due(&self.store, &id).unwrap_or(false) {
            return;
        }
        let (config, store) = (self.config.clone(), self.store.clone());
        self.job_for("autodraft", Some(id.clone()), move || {
            let (config, key) = speech::assist(&config)?;
            Ok(json!({"added":assist::draft(&store, &config, &key, &id)?}))
        });
    }
    /// Background work: finished jobs, the recording's end, refreshes, live sync, calendar and
    /// drafts. Runs on its own timer whether or not the window is visible.
    pub fn tick(&mut self) {
        while let Ok(reply) = self.rx.try_recv() {
            if reply.kind != "live" {
                self.bump();
            }
            if !self.background.remove(&reply.kind) {
                self.busy = false;
            }
            let here = reply.meeting == self.meeting;
            if reply.kind == "signin" {
                self.signing_in = false;
            }
            if reply.kind == "send" {
                *self
                    .send_step
                    .lock()
                    .unwrap_or_else(|error| error.into_inner()) = None;
            }
            // People and bots being added: a refusal takes them out again with the reason; once added,
            // the meeting's people list is fetched now instead of at the next sync.
            if ["attach", "access"].contains(&reply.kind.as_str()) {
                let meeting = reply.meeting.clone().unwrap_or_default();
                let mine = |row: &Adding| row.meeting == meeting && row.step == AddStep::Sending;
                if self.adding_people.iter().any(mine) {
                    match &reply.result {
                        Err(error) => {
                            self.adding_people.retain(|row| !mine(row));
                            self.people_error = format!("Not added: {error}");
                            continue;
                        }
                        Ok(_) => {
                            for row in self.adding_people.iter_mut().filter(|row| mine(row)) {
                                row.step = AddStep::Added;
                                row.added = Some(Instant::now());
                            }
                            if reply.kind == "attach" {
                                self.last_sync = Instant::now() - Duration::from_secs(5);
                            }
                        }
                    }
                }
            }
            let sent_item = if reply.kind == "task" {
                self.sending.take()
            } else {
                None
            };
            match reply.kind.as_str() {
                "ask" => {
                    if let Err(error) = &reply.result
                        && here
                    {
                        self.chat_retry = self.asking.clone();
                        self.chat_error = error.clone();
                    }
                    self.asking.clear();
                    if here && let Some(id) = &self.meeting {
                        self.context.chat = self.store.context(id).unwrap_or_default().chat;
                    }
                    continue;
                }
                "speech" => {
                    // Workers waiting for the Tico look again; the window shows the new state.
                    self.store.poke();
                    self.bump();
                    continue;
                }
                "autodraft" => {
                    match &reply.result {
                        Ok(_) => {
                            self.draft_wait = DRAFT_INTERVAL;
                            if here {
                                self.draft_status.clear();
                            }
                        }
                        Err(error) => {
                            // A missing key cannot fix itself; anything else is retried later.
                            self.draft_wait = if error.contains("key") {
                                Duration::from_secs(600)
                            } else {
                                DRAFT_INTERVAL * 4
                            };
                            if here {
                                self.draft_status = format!("Suggestions paused: {error}");
                            }
                        }
                    }
                    if here && let Some(id) = &self.meeting {
                        let stored = self.store.context(id).unwrap_or_default();
                        self.context.items = stored.items;
                        self.context.drafted_through_ms = stored.drafted_through_ms;
                    }
                    continue;
                }
                "task" => {
                    // A failed send stays on its item, not in the global notice.
                    if let (Err(error), Some(item)) = (&reply.result, sent_item)
                        && here
                    {
                        self.item_error = Some((item, error.clone()));
                        self.reload();
                        continue;
                    }
                }
                "chat" => {
                    if let Err(error) = &reply.result
                        && here
                    {
                        self.chat_error = error.clone();
                        continue;
                    }
                    if here {
                        self.chat_retry.clear();
                    }
                }
                _ => {}
            }
            if [
                "live", "task", "chat", "connect", "finish", "attach", "access",
            ]
            .contains(&reply.kind.as_str())
                && !here
            {
                self.reload();
                continue;
            }
            match reply.result {
                Err(error) => {
                    if reply.kind != "live" {
                        self.error = error;
                    }
                }
                Ok(value) => {
                    match reply.kind.as_str() {
                        "devices" => {
                            self.microphones = serde_json::from_value(value).unwrap_or_default()
                        }
                        "signin" => {
                            self.config =
                                settings::read(self.store.root()).unwrap_or(self.config.clone());
                            self.keep_destination();
                            self.services_at = None;
                            self.tico_url.clear();
                            self.say(value["message"].as_str().unwrap_or_default());
                        }
                        "settings" => {
                            self.config =
                                settings::read(self.store.root()).unwrap_or(self.config.clone());
                            self.keep_destination();
                            self.services_at = None;
                            self.say("Settings saved");
                            if !self.config.calendar_connection.is_empty() {
                                self.sync_calendar();
                            }
                        }
                        "calendar" => {
                            if value["stale"] == true {
                                self.say("Tico calendar is out of date");
                            }
                            self.calendar = value;
                        }
                        "live" => {
                            // Polled every few seconds; the window hears about it only on change.
                            if self.live != value["view"] {
                                self.live = value["view"].clone();
                                self.bump();
                            }
                        }
                        "chat" => {
                            // Shown at once; the next sync brings bot replies and other people.
                            let sent = &value["sent"];
                            if sent.is_object()
                                && let Some(chat) = self.live["chat"].as_array_mut()
                            {
                                if !chat.iter().any(|row| row["id"] == sent["id"]) {
                                    chat.push(sent.clone());
                                }
                            }
                            self.last_sync = Instant::now() - Duration::from_secs(5);
                        }
                        "access" => {
                            self.live = value["view"].clone();
                            self.last_sync = Instant::now() - Duration::from_secs(5);
                        }
                        "roster" => {
                            self.roster = serde_json::from_value(value).unwrap_or_default();
                        }
                        "send" => self.say("Sent to Tico"),
                        "import" => {
                            self.config =
                                settings::read(self.store.root()).unwrap_or(self.config.clone());
                            self.destination = self
                                .config
                                .destinations
                                .first()
                                .map(|row| row.id.clone())
                                .unwrap_or_default();
                            self.say(value["message"].as_str().unwrap_or("Import complete"));
                        }
                        _ => {
                            self.say(value["message"].as_str().unwrap_or("Done"));
                        }
                    }
                    if let Some(id) = &self.meeting {
                        self.context = self.store.context(id).unwrap_or(self.context.clone());
                    }
                    if reply.kind != "live" {
                        self.reload();
                    }
                }
            }
        }
        if self
            .active
            .as_ref()
            .is_some_and(|(_, handle)| handle.is_finished())
        {
            if let Some((_, handle)) = self.active.take() {
                let result = handle.finish();
                self.report(result);
            }
            self.reload();
            tico_recorder_native::qos::release_memory();
        }
        // The library is read again only after it changed, and at most this often while it
        // keeps changing (a recording in progress writes several times a second).
        let version = self.store.version();
        if version != self.seen_open && self.refresh.elapsed() > Duration::from_millis(400) {
            self.reload_open();
        }
        let library_every = if self.meeting.is_some() { 2000 } else { 500 };
        if version != self.seen_library
            && self.refresh_library.elapsed() > Duration::from_millis(library_every)
        {
            self.reload();
        }
        self.watch_inputs();
        let outside = self.outside_state();
        if outside != self.outside {
            self.outside = outside;
            self.bump();
        }
        let shown = !self.message.0.is_empty() && self.message.1.elapsed() < MESSAGE_FOR;
        if shown != self.message_shown {
            self.message_shown = shown;
            self.bump();
        }
        // Meeting chat and bot replies: every 2.5 s while the window shows the meeting.
        let sync_every = Duration::from_millis(if self.visible { 2500 } else { 15000 });
        if !self.context.live_id.is_empty()
            && self.context.live_state != "ended"
            && self.last_sync.elapsed() > sync_every
            && !self.background.contains("live")
        {
            self.sync();
        }
        if !self.config.calendar_connection.is_empty()
            && self.last_calendar.elapsed() > Duration::from_secs(60)
        {
            self.sync_calendar();
        }
        self.refresh_services();
        self.auto_draft();
        // Added rows go once Tico's people list shows them (or after a while, if it never does).
        let shown = self.people_listed();
        let before = self.adding_people.len();
        self.adding_people.retain(|row| {
            row.step != AddStep::Added
                || !shown.contains(&row.actor)
                    && row
                        .added
                        .is_some_and(|at| at.elapsed() < Duration::from_secs(15))
        });
        if self.adding_people.len() != before {
            self.bump();
        }
        self.add_people();
    }
    fn watch_inputs(&mut self) {
        let Some((_, handle)) = &self.active else {
            self.inputs.clear();
            return;
        };
        let now = Instant::now();
        for source in handle.status().sources {
            let lost = source.dropped_frames + source.errors;
            let watch = self
                .inputs
                .entry(source.channel.clone())
                .or_insert(InputWatch {
                    nonzero: source.nonzero,
                    lost,
                    signal_at: now,
                    loss_since: None,
                    loss_at: now,
                });
            if source.nonzero > watch.nonzero {
                watch.nonzero = source.nonzero;
                watch.signal_at = now;
            }
            if lost > watch.lost {
                watch.lost = lost;
                watch.loss_since.get_or_insert(now);
                watch.loss_at = now;
            } else if now.duration_since(watch.loss_at) > Duration::from_secs(3) {
                watch.loss_since = None;
            }
        }
    }
    /// A recording problem worth interrupting for: the mic has had no signal for a while, or an
    /// input keeps losing audio. A blip, or silent meeting audio (no one else on the call), is not.
    pub fn input_notice(&self) -> Option<&'static str> {
        self.active.as_ref()?;
        if self
            .inputs
            .get("me")
            .is_some_and(|mic| mic.signal_at.elapsed() > SILENT_FOR)
        {
            return Some("Your mic isn't picking anything up. Check the microphone in Settings.");
        }
        for (channel, watch) in &self.inputs {
            if watch
                .loss_since
                .is_some_and(|since| since.elapsed() > LOSING_FOR)
            {
                return Some(if channel == "me" {
                    "Your mic keeps dropping audio. Parts of what you say may be missing."
                } else {
                    "Meeting audio keeps dropping. Parts of the call may be missing."
                });
            }
        }
        None
    }
    pub fn dismiss_notice(&mut self) {
        self.notice_dismissed = self.input_notice().unwrap_or_default().to_owned();
    }
    /// Something the window shows may have changed.
    pub fn bump(&mut self) {
        self.ui_version += 1;
    }
    pub fn ui_version(&self) -> u64 {
        self.ui_version
    }
    /// True when the window has not drawn the latest state yet.
    pub fn stale(&self) -> bool {
        self.viewed != self.ui_version
    }
    pub fn recording(&self) -> bool {
        self.active.is_some()
    }
    /// Why an app update waits right now: a recording runs, or a live meeting is connected.
    pub fn update_hold(&self) -> Option<&'static str> {
        if self.active.is_some() {
            Some("After this recording")
        } else if self
            .records
            .iter()
            .any(|record| record.live_state == "live")
        {
            Some("After the live meeting ends")
        } else if self.sending.is_some()
            || self.send_step().is_some()
            || self.records.iter().any(|record| {
                record
                    .deliveries
                    .iter()
                    .any(|entry| entry.status == "sending")
            })
        {
            // A request to Tico is in flight; the outbox keeps unsent items and resumes them after a restart.
            Some("While a meeting is being sent")
        } else {
            None
        }
    }
    /// The clock and input levels of the recording in progress, sent to the window several
    /// times a second without rebuilding any view.
    pub fn levels(&self) -> Option<Value> {
        let (id, handle) = self.active.as_ref()?;
        let status = handle.status();
        let sources: Vec<Value> = status
            .sources
            .iter()
            .map(
                |source| json!({"channel":source.channel,"level":source.peak.clamp(0., 1.).sqrt()}),
            )
            .collect();
        Some(json!({"id":id,"elapsed_ms":status.elapsed_ms,"sources":sources}))
    }
    /// What the window shows from outside this struct: the recording's inputs and live text,
    /// the workers' state and send progress. Levels and the clock are left out; they travel
    /// in `levels`.
    fn outside_state(&self) -> String {
        use std::fmt::Write;
        let mut out = String::new();
        if let Some((id, handle)) = &self.active {
            let status = handle.status();
            let _ = write!(out, "{id}|{}|{}|", status.phase, status.error);
            for source in &status.sources {
                let _ = write!(
                    out,
                    "{}:{}:{};",
                    source.channel,
                    source.name,
                    source.nonzero == 0
                );
            }
            let _ = write!(out, "{:?}|", self.input_notice());
            for row in stream::status(id) {
                let _ = write!(
                    out,
                    "{}:{}:{}:{};",
                    row.channel, row.state, row.error, row.reconnects
                );
            }
            for partial in stream::partials(id) {
                let _ = write!(
                    out,
                    "{}:{}:{};",
                    partial.channel, partial.start_ms, partial.text
                );
            }
        }
        let _ = write!(
            out,
            "|{:?}|{}|",
            self.send_step(),
            self.transcriber.status()
        );
        if let Some(sync) = &self.live_sync {
            let _ = write!(out, "{:?}", sync.errors());
        }
        out
    }
    #[cfg(test)]
    pub fn adding_people_count(&self) -> usize {
        self.adding_people.len()
    }
    /// The people and bots Tico lists in the open meeting.
    fn people_listed(&self) -> Vec<String> {
        let mut actors = vec![];
        for key in ["humans", "access"] {
            for row in self.live[key].as_array().into_iter().flatten() {
                actors.push(row["actor"].as_str().unwrap_or("").to_owned());
            }
        }
        for row in self.live["bots"].as_array().into_iter().flatten() {
            actors.push(format!("bot:{}", row["bot"].as_str().unwrap_or("")));
        }
        actors
    }

    // ---- The open meeting ----

    fn open_record(&self) -> anyhow::Result<Recording> {
        let id = self
            .meeting
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("Open a recording first"))?;
        match &self.current {
            Some(record) if record.id == id => Ok(record.clone()),
            _ => self.store.get(id),
        }
    }
    pub fn rename(&mut self, title: &str) {
        let Some(id) = self.meeting.clone() else {
            return;
        };
        let title: String = title.trim().chars().take(200).collect();
        let Ok(record) = self.open_record() else {
            return;
        };
        if title.is_empty() || title == record.title || !record.deliveries.is_empty() {
            return;
        }
        let result = self.store.mutate(&id, |r| {
            r.title = title;
            Ok(())
        });
        self.report(result);
        self.reload();
    }
    pub fn finish_live(&mut self) {
        if self.context.live_id.is_empty() {
            return;
        }
        self.network_job("finish", |tico, store, id| {
            tico.finish_live(&store, &id)?;
            Ok(json!({"message":"Live meeting finished in Tico"}))
        });
    }
    pub fn retry(&mut self, id: &str) {
        let result = self.store.retry_transcription(id).map(|_| ());
        self.report(result);
        self.reload();
    }
    pub fn set_notes(&mut self, notes: &str) {
        if self.meeting.is_none() || !self.notes_editable() {
            return;
        }
        self.context.notes = notes.chars().take(200_000).collect();
        self.save_context();
    }
    fn notes_editable(&self) -> bool {
        self.open_record()
            .map(|record| record.deliveries.is_empty())
            .unwrap_or(false)
            && self.context.finalize_payload.is_none()
    }
    /// A typed action item. While recording it belongs to what was just said.
    pub fn add_item(&mut self, title: &str) {
        let title: String = title.trim().chars().take(300).collect();
        let Ok(record) = self.open_record() else {
            return;
        };
        if title.is_empty() {
            return;
        }
        let at_ms = if record.state == "recording" {
            record
                .chunks
                .iter()
                .map(|chunk| chunk.end_ms)
                .max()
                .unwrap_or(0)
        } else {
            0
        };
        self.context.items.push(Item {
            id: uuid::Uuid::new_v4().to_string(),
            kind: "task".into(),
            title,
            at_ms,
            ..Default::default()
        });
        self.save_context();
    }
    pub fn edit_item(&mut self, item_id: &str, title: &str) {
        let title: String = title.trim().chars().take(300).collect();
        if title.is_empty() {
            return;
        }
        if let Some(item) = self
            .context
            .items
            .iter_mut()
            .find(|item| item.id == item_id && item.task_id.is_empty())
        {
            item.title = title;
            self.save_context();
        }
    }
    pub fn set_owner(&mut self, item_id: &str, owner: &str) {
        if let Some(item) = self
            .context
            .items
            .iter_mut()
            .find(|item| item.id == item_id && item.task_id.is_empty())
        {
            item.owner = owner.into();
            self.save_context();
        }
    }
    pub fn remove_item(&mut self, item_id: &str) {
        let Some(id) = self.meeting.clone() else {
            return;
        };
        let result = self.store.update_context(&id, |current| {
            current
                .items
                .retain(|item| item.id != item_id || !item.task_id.is_empty());
            Ok(())
        });
        self.report(result);
        self.context
            .items
            .retain(|item| item.id != item_id || !item.task_id.is_empty());
    }
    /// Sends one action item to `owner` as a private Tico task with its quote and the meeting link.
    pub fn send_item(&mut self, item_id: &str, owner: &str) {
        if self.updating {
            self.error = UPDATING.into();
            return;
        }
        let Some(id) = self.meeting.clone() else {
            return;
        };
        let org = self.router_name();
        let Some(item) = self.context.items.iter().find(|item| item.id == item_id) else {
            return;
        };
        if org.is_empty()
            || self.choosing_org
            || self.sending.is_some()
            || owner.is_empty()
            || item.title.trim().is_empty()
            || !item.task_id.is_empty()
        {
            return;
        }
        let name = self
            .roster
            .iter()
            .find(|row| row.0 == owner)
            .map_or(owner.to_owned(), |row| row.1.clone());
        self.last_owner = owner.to_owned();
        if let Some(item) = self
            .context
            .items
            .iter_mut()
            .find(|item| item.id == item_id)
        {
            item.owner = owner.to_owned();
        }
        self.save_context();
        self.item_error = None;
        // A task from a private meeting is between me and whoever gets it.
        let private = self.live["visibility"] != "team";
        self.sending = Some(item_id.to_owned());
        let (config, store, destination) = (self.config.clone(), self.store.clone(), self.router());
        let (item_id, owner) = (item_id.to_owned(), owner.to_owned());
        self.job_for("task", Some(id.clone()), move || {
            Tico::saved(&config, &destination)?
                .send_task(&store, &id, &item_id, &owner, &name, private)?;
            Ok(json!({"message":format!("Sent to {name}")}))
        });
    }
    /// The Tico that action items go to, when several are connected.
    pub fn choose_org(&mut self, destination: &str) {
        if self
            .config
            .destinations
            .iter()
            .any(|row| row.id == destination)
            && self.context.live_destination.is_empty()
        {
            self.destination = destination.into();
            self.choosing_org = false;
            self.roster_for.clear();
        }
    }
    pub fn change_org(&mut self) {
        if self.context.live_destination.is_empty() {
            self.choosing_org = true;
        }
    }
    pub fn load_roster(&mut self) {
        if !self.router_name().is_empty() {
            self.roster_for = self.router();
            self.network_job("roster", |tico, _, _| Ok(json!(tico.roster()?)));
        }
    }

    // ---- People: sharing the meeting in Tico ----

    fn share_blocked(&self, record: &Recording) -> &'static str {
        if self.destination.is_empty() && self.context.live_id.is_empty() {
            "Sign in to a Tico first"
        } else if !record.deliveries.is_empty() {
            "This recording was already sent to Tico"
        } else if self.context.live_state == "ended" {
            "This meeting has ended in Tico"
        } else {
            ""
        }
    }
    /// Whether this meeting can still be opened in Tico to bots or people.
    fn can_share(&self, record: &Recording) -> bool {
        self.share_blocked(record).is_empty() && !self.busy
    }
    fn shareable(&mut self) -> bool {
        match self.open_record() {
            Ok(record) => {
                let blocked = self.share_blocked(&record);
                if !blocked.is_empty() {
                    self.error = blocked.into();
                    return false;
                }
                !self.busy
            }
            Err(_) => false,
        }
    }
    fn access_job(&mut self, team: Option<bool>, add: Vec<String>, remove: Vec<String>) -> bool {
        self.network_job("access", move |tico, store, id| {
            let live_id = tico.ensure_live(&store, &id, team == Some(true))?;
            let mut body = json!({"add":add,"remove":remove});
            if let Some(team) = team {
                body["visibility"] = json!(if team { "team" } else { "private" });
            }
            let view = tico.call(
                &format!("/api/v2/live-meetings/{live_id}/access"),
                Some(&body),
                &format!("recorder:{id}:access:{}", uuid::Uuid::new_v4()),
            )?;
            Ok(json!({"message":"Access updated","view":view}))
        })
    }
    /// "Add people": with one Tico it is chosen; with several, People asks which.
    pub fn start_adding(&mut self) {
        let Some(id) = self.meeting.clone() else {
            return;
        };
        self.adding = Some(id);
        if self.context.live_id.is_empty() {
            if self.config.destinations.len() == 1 {
                self.destination = self.config.destinations[0].id.clone();
                self.load_roster();
            } else {
                self.destination.clear();
            }
        }
    }
    pub fn choose_people_org(&mut self, destination: &str) {
        if self.context.live_id.is_empty()
            && self
                .config
                .destinations
                .iter()
                .any(|row| row.id == destination)
        {
            self.destination = destination.into();
            self.load_roster();
        }
    }
    /// Adds a bot or person from Tico. The meeting opens there privately if it isn't open yet.
    /// Adds a person or bot from the roster. It shows in People at once, marked as being added,
    /// and leaves again with the reason if Tico refuses.
    pub fn pick(&mut self, actor: &str) {
        let Some(meeting) = self.meeting.clone() else {
            return;
        };
        if !(actor.starts_with("bot:") || actor.starts_with("human:"))
            || self
                .adding_people
                .iter()
                .any(|row| row.meeting == meeting && row.actor == actor)
        {
            return;
        }
        // Picks queue behind a change on its way, so only a meeting that cannot be shared stops one.
        let Ok(record) = self.open_record() else {
            return;
        };
        let blocked = self.share_blocked(&record);
        if !blocked.is_empty() {
            self.error = blocked.into();
            return;
        }
        let name = self
            .roster
            .iter()
            .find(|row| row.0 == actor)
            .map_or(actor, |row| row.1.as_str())
            .to_owned();
        self.people_error.clear();
        self.adding_people.push(Adding {
            meeting,
            actor: actor.into(),
            name,
            step: AddStep::Queued,
            added: None,
        });
        self.add_people();
    }
    /// Sends the queued people and bots of the open meeting to Tico, bots first, each kind in one
    /// request; called again on every tick until none wait.
    fn add_people(&mut self) {
        let Some(meeting) = self.meeting.clone() else {
            return;
        };
        let queued = |rows: &[Adding], prefix: &str| -> Vec<String> {
            rows.iter()
                .filter(|row| {
                    row.meeting == meeting
                        && row.step == AddStep::Queued
                        && row.actor.starts_with(prefix)
                })
                .map(|row| row.actor.clone())
                .collect()
        };
        let bots = queued(&self.adding_people, "bot:");
        let humans = queued(&self.adding_people, "human:");
        let started = if !bots.is_empty() {
            let slugs: Vec<String> = bots
                .iter()
                .map(|actor| actor["bot:".len()..].to_owned())
                .collect();
            let ok = self.network_job("attach", move |tico, store, id| {
                let live_id = tico.ensure_live(&store, &id, false)?;
                tico.call(
                    &format!("/api/v2/live-meetings/{live_id}/bots"),
                    Some(&json!({"bots":slugs})),
                    &format!("recorder:{id}:attach:{}", slugs.join(",")),
                )?;
                Ok(json!({}))
            });
            ok.then_some(bots)
        } else if !humans.is_empty() {
            self.access_job(None, humans.clone(), vec![])
                .then_some(humans)
        } else {
            None
        };
        for row in &mut self.adding_people {
            if started
                .as_ref()
                .is_some_and(|actors| actors.contains(&row.actor))
                && row.meeting == meeting
            {
                row.step = AddStep::Sending;
            }
        }
    }
    /// Someone not in Tico: the name stays with the transcript only.
    pub fn add_participant(&mut self, name: &str) {
        let name: String = name.trim().chars().take(100).collect();
        if name.is_empty() || self.meeting.is_none() {
            return;
        }
        self.context.participants.push(name);
        self.save_context();
    }
    pub fn remove_access(&mut self, actor: &str) {
        if self.shareable() {
            let _ = self.access_job(None, vec![], vec![actor.into()]);
        }
    }
    /// Everyone in the Tico (`team`) or only the people and bots added (private).
    pub fn set_team(&mut self, team: bool) {
        if self.shareable() {
            let _ = self.access_job(Some(team), vec![], vec![]);
        }
    }

    // ---- Library ----

    /// Whether this meeting is already in every connected Tico, so there is nowhere left to send it.
    pub fn sent_everywhere(&self, deliveries: &[store::Delivery]) -> bool {
        !self.config.destinations.is_empty()
            && self
                .config
                .destinations
                .iter()
                .all(|row| sent_to(deliveries, &row.id))
    }
    /// Where this recording can be sent: a meeting already open in one Tico, with its people and
    /// bots, goes only there.
    pub fn send_targets(&self, id: &str, deliveries: &[store::Delivery]) -> Vec<Value> {
        let live = self.store.context(id).unwrap_or_default().live_destination;
        self.targets(&live, deliveries)
    }
    /// `send_targets` for a meeting whose live connection is already known, without a read.
    fn targets(&self, live: &str, deliveries: &[store::Delivery]) -> Vec<Value> {
        self.config
            .destinations
            .iter()
            .filter(|entry| live.is_empty() || entry.id == live)
            .map(|entry| {
                json!({"id":entry.id,"name":entry.name,"already":sent_to(deliveries, &entry.id)})
            })
            .collect()
    }
    /// Send does every step in one go: it stops this recording, waits for its last words,
    /// finishes a live meeting in Tico, then files it. Only what no step can fix stops it here.
    pub fn send_meeting(&mut self, id: &str, destination: &str) {
        if self.updating {
            self.error = UPDATING.into();
            return;
        }
        let Ok(record) = self.store.get(id) else {
            return;
        };
        let recording_here = self.active.as_ref().is_some_and(|(active, _)| active == id);
        if !recording_here && let Some(error) = self.send_blocked(&record) {
            self.error = error;
            return;
        }
        if self.busy
            || !self
                .send_targets(id, &record.deliveries)
                .iter()
                .any(|row| row["id"] == destination && row["already"] == false)
        {
            return;
        }
        if recording_here {
            self.stop();
        }
        if self.meeting.as_deref() == Some(id) {
            self.save_context();
        }
        self.destination = destination.into();
        let (config, store, step) = (
            self.config.clone(),
            self.store.clone(),
            self.send_step.clone(),
        );
        let (id, destination) = (id.to_owned(), destination.to_owned());
        let say = move |label: &str| {
            *step.lock().unwrap_or_else(|error| error.into_inner()) =
                Some((id.clone(), label.into()));
        };
        say(if recording_here {
            "Stopping…"
        } else {
            "Sending…"
        });
        let id = record.id.clone();
        self.job("send", move || {
            let deadline = Instant::now() + SEND_WAIT;
            loop {
                let record = store.get(&id)?;
                let label = if record.state == "recording" {
                    "Stopping…"
                } else if record
                    .chunks
                    .iter()
                    .any(|chunk| ["pending", "running"].contains(&chunk.status.as_str()))
                {
                    "Transcribing…"
                } else {
                    break;
                };
                anyhow::ensure!(
                    Instant::now() < deadline,
                    "The last words are still being transcribed. Send again when they finish."
                );
                say(label);
                std::thread::sleep(Duration::from_millis(250));
            }
            network::ready(&store.get(&id)?)?;
            let tico = Tico::saved(&config, &destination)?;
            if !store.context(&id)?.live_id.is_empty() {
                say("Finishing…");
                tico.finish_live(&store, &id)?;
            }
            say("Sending…");
            tico.send(&store, &id, false)?;
            Ok(json!({}))
        });
    }
    /// Why Send cannot work now, when waiting would not help: a recording made in another
    /// session, a failed or missing transcript.
    fn send_blocked(&self, record: &Recording) -> Option<String> {
        let waiting = record
            .chunks
            .iter()
            .any(|chunk| ["pending", "running"].contains(&chunk.status.as_str()));
        if waiting && record.state != "recording" && !self.config.transcription_model.is_empty() {
            return None;
        }
        network::ready(record).err().map(|error| error.to_string())
    }
    fn send_step(&self) -> Option<(String, String)> {
        self.send_step
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
    }
    /// Removes the recording's audio and transcript from this Mac. The window asks first.
    pub fn delete(&mut self, id: &str) {
        let result = self.store.delete(id);
        let deleted = result.is_ok();
        self.report(result);
        if deleted && self.meeting.as_deref() == Some(id) {
            self.meeting = None;
            self.current = None;
            self.context = Context::default();
        }
        self.reload();
    }

    // ---- Settings and connections ----

    /// Keep the chosen Tico when it still exists; otherwise use the default (first).
    fn keep_destination(&mut self) {
        if !self
            .config
            .destinations
            .iter()
            .any(|row| row.id == self.destination)
        {
            self.destination = self
                .config
                .destinations
                .first()
                .map(|row| row.id.clone())
                .unwrap_or_default();
        }
    }
    /// Change the saved connection list now.
    fn connections(&mut self, change: impl Fn(&mut Settings)) {
        let mut saved = settings::read(self.store.root()).unwrap_or(self.config.clone());
        change(&mut saved);
        change(&mut self.config);
        let result = settings::write(self.store.root(), &saved);
        self.report(result);
        self.keep_destination();
    }
    pub fn make_default(&mut self, id: &str) {
        self.connections(|config| {
            if let Some(at) = config.destinations.iter().position(|row| row.id == id) {
                let row = config.destinations.remove(at);
                config.destinations.insert(0, row);
            }
        });
        if self.config.destinations.iter().any(|row| row.id == id) {
            self.destination = id.into();
        }
    }
    pub fn sign_out(&mut self, id: &str) {
        let Some(destination) = self
            .config
            .destinations
            .iter()
            .find(|row| row.id == id)
            .cloned()
        else {
            return;
        };
        self.connections(|config| {
            config.destinations.retain(|row| row.id != id);
            if config.calendar_connection == id {
                config.calendar_connection.clear();
            }
        });
        let name = destination.name.clone();
        self.job("signout", move || {
            signin::sign_out(&destination)?;
            Ok(json!({"message": format!("Signed out of {name}")}))
        });
    }
    /// Browser sign-in to the Tico at this address; the first connection becomes the default.
    pub fn sign_in(&mut self, address: &str) {
        let address = address.trim().to_owned();
        if address.is_empty() || self.busy {
            return;
        }
        let root = self.store.root().to_path_buf();
        self.signing_in = true;
        self.connect = None;
        self.job("signin", move || {
            let (mut destination, access, grant) = signin::sign_in(&address, signin::open_browser)?;
            let mut config = settings::read(&root)?;
            // Signing in again to the same Tico renews that connection in place, keeping its id and position.
            let existing = config
                .destinations
                .iter_mut()
                .find(|row| row.base_url == destination.base_url);
            if let Some(row) = &existing {
                destination.id = row.id.clone();
            }
            signin::save(&destination.id, &access, &grant)?;
            let name = destination.name.clone();
            match existing {
                Some(row) => *row = destination,
                None => config.destinations.push(destination),
            }
            settings::write(&root, &config)?;
            Ok(json!({"message": format!("Signed in to {name}")}))
        });
    }
    /// Saves the form over the saved settings, so connection changes made meanwhile are kept.
    /// A new transcription key goes to the keychain and is never read back.
    pub fn save_settings(&mut self, form: SettingsForm) {
        let root = self.store.root().to_path_buf();
        let mut config = settings::read(&root).unwrap_or(self.config.clone());
        config.microphone = form.microphone;
        config.meeting_audio = form.meeting_audio;
        config.transcription_url = form.transcription_url.trim().into();
        config.transcription_model = form.transcription_model.trim().into();
        config.transcription_language = form.transcription_language.trim().into();
        config.calendar_connection = if !form.calendar {
            String::new()
        } else if config
            .destinations
            .iter()
            .any(|row| row.id == config.calendar_connection)
        {
            config.calendar_connection.clone()
        } else {
            self.destination.clone()
        };
        if let Some(source) = form.speech_source {
            config.speech_source = source.trim().into();
        }
        // Unset follows the source, so compare against the source being saved.
        if let Some(on) = form
            .streaming
            .filter(|on| *on != self.streaming_for(&config))
        {
            config.streaming = Some(on);
        }
        config.streaming_model = match form.streaming_model.trim() {
            "" => stream::DEFAULT_MODEL.into(),
            model => model.into(),
        };
        let key = form.key.trim().to_owned();
        let xai = form.xai_key.trim().to_owned();
        self.job("settings", move || {
            if !key.is_empty() {
                settings::save_secret("transcription", &key)?;
            }
            if !xai.is_empty() {
                settings::save_secret(stream::KEY_NAME, &xai)?;
            }
            settings::write(&root, &config)?;
            Ok(json!({}))
        });
    }
    /// Microphone and meeting audio for the next recording, from the record button's menu.
    pub fn set_audio(&mut self, microphone: &str, meeting_audio: bool) {
        let (microphone, meeting_audio) = (microphone.to_owned(), meeting_audio);
        self.connections(|config| {
            config.microphone = microphone.clone();
            config.meeting_audio = meeting_audio;
        });
    }
    pub fn import_legacy(&mut self, directory: &str) {
        let directory = directory.trim().to_owned();
        if directory.is_empty() || self.active.is_some() {
            return;
        }
        let store = self.store.clone();
        self.job("import", move || {
            let count = store.import_legacy(std::path::Path::new(&directory))?;
            Ok(json!({"message":format!("Imported {count} recordings")}))
        });
    }
    /// A link macOS delivered to the app.
    pub fn link(&mut self, link: &str) {
        match connect_address(link) {
            Some(address) => self.connect = Some(address),
            None => self.error = "That link is not a Tico Recorder connect link".into(),
        }
    }
    pub fn confirm_connect(&mut self) {
        if let Some(address) = self.connect.take() {
            self.sign_in(&address);
        }
    }
    pub fn dismiss_connect(&mut self) {
        self.connect = None;
    }
    pub fn dismiss_error(&mut self) {
        self.error.clear();
    }
    /// Opens a Tico page (a sent task) in the browser. Only addresses of a connected Tico.
    pub fn open_url(&mut self, url: &str) {
        let allowed = self.config.destinations.iter().any(|row| {
            settings::safe_url(&row.base_url).is_ok_and(|base| url.starts_with(&format!("{base}/")))
        });
        if allowed {
            signin::open_browser(url);
        }
    }

    // ---- What the window draws ----

    /// Everything the window shows. Parts the window already has (by hash) come back as null.
    pub fn view(&mut self, have: &Have) -> Value {
        self.viewed = self.ui_version;
        let mut out = json!({"status": self.status_view(), "live": self.live_view()});
        let parts: [(&str, &str, Value); 4] = [
            ("library", &have.library, self.library_view()),
            ("settings", &have.settings, self.settings_view()),
            ("meeting", &have.meeting, self.meeting_view()),
            ("transcript", &have.transcript, self.transcript_view()),
        ];
        for (name, known, data) in parts {
            let hash = network::hash(&data);
            out[name] = if hash == known {
                Value::Null
            } else {
                json!({"hash":hash,"data":data})
            };
        }
        out
    }
    /// The open meeting's notices about its bots (`notices`), newest last, named as in People.
    pub fn live_view(&self) -> Value {
        let live_id = self.live["id"].as_str().unwrap_or("");
        let notices: Vec<Value> = if live_id.is_empty() || live_id != self.context.live_id {
            vec![]
        } else {
            notices::notices(live_id)
                .iter()
                .map(|notice| {
                    let name = self
                        .roster
                        .iter()
                        .find(|row| row.0 == format!("bot:{}", notice.bot))
                        .map_or(notice.bot.as_str(), |row| row.1.as_str());
                    json!({"id":notice.id,"kind":notice.kind,"bot":name,
                        "text":notice.text(name),"at":notice.at})
                })
                .collect()
        };
        json!({"notices": notices})
    }
    pub fn status_view(&self) -> Value {
        let recording = self.active.as_ref().map(|(id, handle)| {
            let status = handle.status();
            let sources: Vec<Value> = status
                .sources
                .iter()
                .map(|source| {
                    json!({"channel":source.channel,
                        "label":if source.channel == "me" { "Mic" } else { "Meeting" },
                        "name":source.name,
                        "level":source.peak.clamp(0., 1.).sqrt(),
                        "no_signal":source.channel == "me" && self.inputs.get("me")
                            .is_some_and(|mic| mic.signal_at.elapsed() > SILENT_FOR)})
                })
                .collect();
            json!({"id":id,"elapsed_ms":status.elapsed_ms,"phase":status.phase,
                "error":status.error,"sources":sources,"stream":stream::status(id)})
        });
        let message = if self.message.1.elapsed() < MESSAGE_FOR {
            self.message.0.clone()
        } else {
            String::new()
        };
        json!({
            "busy": self.busy,
            "demo": self.demo,
            "error": self.error,
            "message": message,
            "signing_in": self.signing_in,
            "connect": self.connect,
            "first_run": self.config.destinations.is_empty() && !self.demo,
            "suggested": self.tico_url,
            "recording": recording,
            "meeting": self.meeting,
            "sending": self.send_step().map(|(id, label)| json!({"id": id, "label": label})),
            "profile": synthetic_input(),
            "notice": self.input_notice().filter(|notice| *notice != self.notice_dismissed),
            "speech": speech::view(&self.config, self.key_saved("transcription"),
                self.streaming() && self.key_saved(stream::KEY_NAME)),
        })
    }
    fn library_view(&self) -> Value {
        let rows: Vec<Value> = self
            .records
            .iter()
            .map(|record| {
                let sync_error = self.sync_error(&record.id);
                // The recording in progress reads as one before its first chunk is saved.
                let recording = record.state == "recording"
                    || self.active.as_ref().is_some_and(|(id, _)| id == &record.id);
                let status = if recording {
                    Some(("Recording".into(), "red"))
                } else if sync_error.is_empty() {
                    record_status(record)
                } else {
                    Some(("Tico sync needs attention".into(), "amber"))
                };
                let sent = self.sent_everywhere(&record.deliveries);
                json!({
                    "id": record.id,
                    "title": record.title,
                    "started_at": record.started_at,
                    "duration_ms": record.duration_ms,
                    "recording": recording,
                    "status": status.map(|(label, tone)| json!({"label":label,"tone":tone})),
                    "sync_error": sync_error,
                    "sent": sent,
                    "can_send": !self.config.destinations.is_empty() && !sent && !recording && record.chunks > 0 && !self.busy,
                    "targets": self.targets(&record.live_destination, &record.deliveries),
                })
            })
            .collect();
        let now = chrono::Utc::now();
        let events: Vec<Value> = self.calendar["events"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|row| {
                chrono::DateTime::parse_from_rfc3339(row["end"].as_str().unwrap_or(""))
                    .is_ok_and(|time| time > now)
            })
            .take(2)
            .cloned()
            .collect();
        json!({
            "rows": rows,
            "events": events,
            "calendar_stale": self.calendar["stale"] == true,
            "connected": !self.config.destinations.is_empty(),
        })
    }
    fn settings_view(&self) -> Value {
        let connections: Vec<Value> = self
            .config
            .destinations
            .iter()
            .enumerate()
            .map(|(index, row)| {
                let host = row
                    .base_url
                    .trim_start_matches("https://")
                    .trim_end_matches('/');
                json!({"id":row.id,"name":row.name,"host":if host == row.name { "" } else { host },
                    "default":index == 0})
            })
            .collect();
        json!({
            "microphone": self.config.microphone,
            "microphones": self.microphones,
            "meeting_audio": self.config.meeting_audio,
            "transcription_url": self.config.transcription_url,
            "transcription_model": self.config.transcription_model,
            "transcription_language": self.config.transcription_language,
            "calendar": !self.config.calendar_connection.is_empty(),
            "calendar_available": !self.destination.is_empty(),
            "connections": connections,
            "recording": self.active.is_some(),
            "key_saved": self.key_saved("transcription"),
            "streaming": self.streaming(),
            "streaming_model": self.config.streaming_model,
            "xai_saved": self.key_saved(stream::KEY_NAME),
            "speech_source": speech::source(&self.config),
            "tico": speech::tico(&self.config).map(|row| row.name.clone()),
        })
    }
    /// Whether a keychain credential exists; never its value. The demo library (and every test)
    /// never reads the keychain.
    fn key_saved(&self, name: &str) -> bool {
        !self.demo && settings::secret(name).is_ok_and(|key| !key.is_empty())
    }
    /// `stream::enabled`, without the keychain read in the demo library.
    fn streaming(&self) -> bool {
        self.streaming_for(&self.config)
    }
    fn streaming_for(&self, config: &Settings) -> bool {
        config.streaming.unwrap_or_else(|| {
            speech::source(config) == speech::TICO || self.key_saved(stream::KEY_NAME)
        })
    }
    fn transcript_view(&mut self) -> Value {
        let Some(record) = self
            .current
            .as_ref()
            .filter(|record| Some(&record.id) == self.meeting.as_ref())
        else {
            return Value::Null;
        };
        let recording = record.state == "recording"
            || self.active.as_ref().is_some_and(|(id, _)| id == &record.id);
        if recording {
            // While recording: finished chunks, streamed final words, then the words still
            // being spoken, which the window shows softer and updates in place.
            if let Ok(turns) = stream::live_turns(&self.store, &record.id) {
                let blocks: Vec<Value> = turns
                    .iter()
                    .filter(|turn| !turn.text.trim().is_empty())
                    .map(|turn| {
                        json!({"speaker":turn.speaker,"me":turn.me,"start_ms":turn.start_ms,
                            "paragraphs":[turn.text.trim()],"partial":turn.partial,
                            "streamed":turn.streamed})
                    })
                    .collect();
                return json!({"id": record.id, "live": true, "blocks": blocks});
            }
        }
        let current = signature(record);
        if self.blocks.0 != current {
            self.blocks = (current, transcript_blocks(record));
        }
        json!({"id": record.id, "blocks": self.blocks.1})
    }
    /// Keep the destination pinned to this live meeting, even if the default Tico changes.
    fn live_url(&self) -> Option<String> {
        if self.context.live_id.is_empty() {
            return None;
        }
        let destination = self
            .config
            .destinations
            .iter()
            .find(|row| row.id == self.context.live_destination)?;
        let base = settings::safe_url(&destination.base_url).ok()?;
        let mut url = reqwest::Url::parse(&format!("{}/", base.trim_end_matches('/'))).ok()?;
        url.query_pairs_mut()
            .append_pair("live", &self.context.live_id);
        let query = url.query()?.to_owned();
        url.set_query(None);
        url.set_fragment(Some(&format!("/meetings?{query}")));
        Some(url.into())
    }
    fn meeting_view(&mut self) -> Value {
        let Some(record) = self
            .current
            .clone()
            .filter(|record| Some(&record.id) == self.meeting.as_ref())
        else {
            return Value::Null;
        };
        let org = self.router_name();
        let router = self.router();
        // People and owners come from the meeting's Tico, loaded once per Tico.
        if !org.is_empty() && !self.choosing_org && self.roster_for != router && !self.busy {
            self.load_roster();
        }
        let recording_here = self.active.as_ref().is_some_and(|(id, _)| id == &record.id);
        let live = !self.context.live_id.is_empty();
        let ended = self.context.live_state == "ended";
        let team = self.live["visibility"] == "team";
        let has_text = record
            .chunks
            .iter()
            .any(|chunk| !chunk.text.trim().is_empty());
        let blocked = if recording_here {
            None
        } else {
            self.send_blocked(&record)
        };
        let sync_error = self.sync_error(&record.id);
        json!({
            "id": record.id,
            "title": record.title,
            "started_at": record.started_at,
            "duration_ms": record.chunks.iter().map(|chunk| chunk.end_ms).max().unwrap_or(0),
            "state": record.state,
            "recording_here": recording_here,
            "title_editable": record.deliveries.is_empty(),
            "sent": self.sent_everywhere(&record.deliveries),
            "connected": !self.config.destinations.is_empty(),
            "send_blocked": if self.busy { None } else { blocked },
            "targets": self.targets(&self.context.live_destination, &record.deliveries),
            "live": if live { Some(match (ended, team) {
                (true, _) => "Ended in Tico",
                (false, true) => "Team can join",
                (false, false) => "Private in Tico",
            }) } else { None },
            "live_url": self.live_url(),
            "can_finish": live && record.state != "recording" && record.deliveries.is_empty()
                && (!ended || has_text),
            "place": if live { format!("Shared live · {org}") } else { "Private · on this Mac".into() },
            "sync_error": if sync_error.is_empty() { String::new() } else {
                format!("Tico sync needs attention: {sync_error}. Audio and transcript remain saved on this Mac.")
            },
            "setup": self.setup_notices(),
            "audio": audio_view(&record),
            "actions": self.actions_view(&org),
            "notes": {"text": self.context.notes, "editable": record.deliveries.is_empty() && self.context.finalize_payload.is_none()},
            "people": self.people_view(&record, &org, team),
            "chat": self.chat_view(),
        })
    }
    fn setup_notices(&self) -> Vec<String> {
        let mut notices = vec![];
        if self.config.transcription_model.is_empty() {
            notices
                .push("Choose a transcription model and key in Settings. Audio is saved.".into());
        }
        let status = self.transcriber.status();
        if !status.is_empty() {
            notices.push(status);
        }
        notices
    }
    fn actions_view(&self, org: &str) -> Value {
        let mode = if self.config.destinations.is_empty() {
            "signin"
        } else if self.choosing_org || org.is_empty() {
            "choose"
        } else {
            "ready"
        };
        // Bots first: most action items go to one.
        let mut owners = self.roster.clone();
        owners.sort_by_key(|row| !row.0.starts_with("bot:"));
        let can_send = mode == "ready" && self.sending.is_none();
        let items = &self.context.items;
        let order = items
            .iter()
            .filter(|item| item.task_id.is_empty())
            .chain(items.iter().filter(|item| !item.task_id.is_empty()));
        let rows: Vec<Value> = order
            .map(|item| {
                let chosen = if item.owner.is_empty() {
                    self.last_owner.clone()
                } else {
                    item.owner.clone()
                };
                let owner_name = owners
                    .iter()
                    .find(|row| row.0 == chosen)
                    .map(|row| row.1.clone())
                    .unwrap_or_default();
                let error = self
                    .item_error
                    .as_ref()
                    .filter(|(id, _)| id == &item.id)
                    .map(|(_, error)| error.clone())
                    .unwrap_or_default();
                json!({
                    "id": item.id,
                    "title": item.title,
                    "quote": item.quote,
                    "at_ms": item.at_ms,
                    "owner": chosen,
                    "owner_name": owner_name,
                    "sent": !item.task_id.is_empty(),
                    "sent_to": item.sent_to,
                    "task_url": item.task_url,
                    "sending": self.sending.as_deref() == Some(item.id.as_str()),
                    "can_send": can_send && !chosen.is_empty() && !item.title.trim().is_empty(),
                    "error": error,
                })
            })
            .collect();
        json!({
            "mode": mode,
            "org": org,
            "orgs": self.config.destinations.iter().map(|row| json!({"id":row.id,"name":row.name})).collect::<Vec<_>>(),
            "multiple": self.config.destinations.len() > 1,
            "can_change": self.context.live_destination.is_empty(),
            "draft_status": self.draft_status,
            "items": rows,
            "waiting": items.iter().filter(|item| item.task_id.is_empty()).count(),
            "owners": owners.iter().map(|(actor, name)| json!({"actor":actor,"name":name,"bot":actor.starts_with("bot:")})).collect::<Vec<_>>(),
        })
    }
    /// Everyone in the meeting in one list: me, people and bots in Tico, and names typed here.
    fn people_view(&self, record: &Recording, org: &str, team: bool) -> Value {
        let me = self
            .config
            .destinations
            .iter()
            .find(|row| row.id == self.router())
            .map(|row| row.actor.clone())
            .unwrap_or_default();
        let mut rows = vec![json!({"kind":"person","name":"Me","me":true})];
        let mut listed = vec![me];
        for human in self.live["humans"].as_array().into_iter().flatten() {
            let actor = human["actor"].as_str().unwrap_or("").to_owned();
            if !listed.contains(&actor) {
                rows.push(
                    json!({"kind":"person","name":human["name"].as_str().unwrap_or("Participant")}),
                );
                listed.push(actor);
            }
        }
        let enabled = self.can_share(record);
        for person in self.live["access"].as_array().into_iter().flatten() {
            let actor = person["actor"].as_str().unwrap_or("").to_owned();
            if listed.contains(&actor) {
                continue;
            }
            rows.push(
                json!({"kind":"person","name":person["name"].as_str().unwrap_or(&actor),
                "actor":actor,"removable":enabled}),
            );
            listed.push(actor);
        }
        for bot in self.live["bots"].as_array().into_iter().flatten() {
            let slug = bot["bot"].as_str().unwrap_or("Bot");
            let name = self
                .roster
                .iter()
                .find(|row| row.0 == format!("bot:{slug}"))
                .map_or(slug, |row| row.1.as_str());
            rows.push(json!({"kind":"bot","name":name}));
            listed.push(format!("bot:{slug}"));
        }
        for row in &self.adding_people {
            if row.meeting == record.id && !listed.contains(&row.actor) {
                let kind = if row.actor.starts_with("bot:") {
                    "bot"
                } else {
                    "person"
                };
                rows.push(json!({"kind":kind,"name":row.name,"actor":row.actor,"pending":true}));
                listed.push(row.actor.clone());
            }
        }
        for name in &self.context.participants {
            rows.push(json!({"kind":"person","name":name,"local":true}));
        }
        let started = !self.context.live_id.is_empty();
        let adding = self.adding.as_deref() == Some(record.id.as_str());
        let stage = if !started && !adding {
            "invite"
        } else if !started && self.destination.is_empty() && !self.config.destinations.is_empty() {
            "org"
        } else {
            "add"
        };
        // Picks queue while another change is on its way, so the list stays open while busy.
        let candidates: Vec<Value> = if self.share_blocked(record).is_empty() {
            self.roster
                .iter()
                .filter(|row| !listed.contains(&row.0))
                .map(|(actor, name)| json!({"actor":actor,"name":name,"bot":actor.starts_with("bot:")}))
                .collect()
        } else {
            vec![]
        };
        json!({
            "rows": rows,
            "stage": stage,
            "orgs": self.config.destinations.iter().map(|row| json!({"id":row.id,"name":row.name})).collect::<Vec<_>>(),
            "org": org,
            "team": team,
            "started": started,
            "can_share": enabled,
            "blocked": self.share_blocked(record),
            "candidates": candidates,
            "error": self.people_error,
        })
    }
    /// The meeting's chat: people and bots in the shared meeting, and this person's private
    /// questions to Tico, in time order. Private turns are marked; they never leave this Mac.
    fn chat_view(&self) -> Value {
        let connected = !self.context.live_id.is_empty();
        let can_message = self.can_message();
        let private = self.chat_private || !can_message;
        let me = self
            .config
            .destinations
            .iter()
            .find(|row| row.id == self.router())
            .map(|row| row.actor.clone())
            .unwrap_or_default();
        let millis = |at: &str| {
            chrono::DateTime::parse_from_rfc3339(at)
                .map(|time| time.timestamp_millis())
                .unwrap_or(0)
        };
        let mut rows: Vec<(i64, usize, Value)> = vec![];
        for message in self.live["chat"].as_array().into_iter().flatten() {
            let actor = message["actor"].as_str().unwrap_or("");
            let bot = actor.starts_with("bot:") || message["role"] == "bot";
            let mine = !me.is_empty() && actor == me;
            let name = if mine {
                "You".to_owned()
            } else {
                self.actor_name(actor)
            };
            let at = message["created"].as_str().unwrap_or("");
            rows.push((
                millis(at),
                rows.len(),
                json!({
                    "id": message["id"], "name": name, "bot": bot, "me": mine, "private": false,
                    "text": message["text"].as_str().unwrap_or(""), "time": at,
                }),
            ));
        }
        for (index, turn) in self.context.chat.iter().enumerate() {
            let mine = turn.role == "you";
            rows.push((
                millis(&turn.at),
                rows.len(),
                json!({
                    "id": format!("private-{index}"), "name": if mine { "You" } else { "Tico" },
                    "bot": !mine, "me": mine, "private": true, "text": turn.text, "time": turn.at,
                }),
            ));
        }
        rows.sort_by_key(|(at, order, _)| (*at, *order));
        let mut messages: Vec<Value> = rows.into_iter().map(|(_, _, row)| row).collect();
        // What is on its way: a private question, or a message to the meeting.
        let asking_here = !self.asking.is_empty() && self.asking_for == self.meeting;
        if asking_here {
            messages.push(json!({"id":"asking","name":"You","me":true,"private":true,"pending":true,"text":self.asking}));
        } else if self.busy && !self.chat_retry.is_empty() && self.chat_error.is_empty() {
            messages.push(json!({"id":"sending","name":"You","me":true,"private":false,"pending":true,"text":self.chat_retry}));
        }
        // Bots that took a turn and are writing their reply now.
        let replying: Vec<String> = self.live["router"]["turns"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|turn| turn["status"] == "claimed")
            .filter_map(|turn| turn["bot"].as_str())
            .map(|slug| self.actor_name(&format!("bot:{slug}")))
            .collect();
        json!({
            "connected": connected,
            "can_message": can_message,
            "private": private,
            "messages": messages,
            "replying": replying,
            "thinking": asking_here,
            "bots": self.live["bots"].as_array().map_or(0, |bots| bots.len()),
            "error": self.chat_error,
            "retry": self.chat_retry,
            "ready": if private { self.asking.is_empty() } else { !self.busy },
        })
    }
    /// A name for a Tico actor: people from the meeting's list, bots from the org roster.
    fn actor_name(&self, actor: &str) -> String {
        for key in ["humans", "access"] {
            for row in self.live[key].as_array().into_iter().flatten() {
                if row["actor"] == actor
                    && let Some(name) = row["name"].as_str()
                {
                    return name.to_owned();
                }
            }
        }
        if let Some(row) = self.roster.iter().find(|row| row.0 == actor) {
            return row.1.clone();
        }
        let slug = actor.split_once(':').map_or(actor, |(_, id)| id);
        // A bot slug reads as a name: head-of-engineering -> Head of engineering.
        let mut name: String = slug.replace('-', " ");
        if let Some(first) = name.get(0..1) {
            name = first.to_uppercase() + &name[1..];
        }
        name
    }
}
/// Audio health behind the transcript's Audio toggle: per-input signal, warnings, failed chunks.
fn audio_view(record: &Recording) -> Value {
    let pending = record
        .chunks
        .iter()
        .filter(|chunk| ["pending", "running"].contains(&chunk.status.as_str()))
        .count();
    let failed = record
        .chunks
        .iter()
        .filter(|chunk| chunk.status == "failed")
        .count();
    let mut inputs = vec![];
    for (channel, label) in [("me", "Microphone"), ("them", "Meeting audio")] {
        let chunks: Vec<_> = record
            .chunks
            .iter()
            .filter(|chunk| chunk.channel == channel)
            .collect();
        if chunks.is_empty() {
            continue;
        }
        let silent = chunks.iter().filter(|chunk| chunk.signal.silent()).count();
        let state = if silent == chunks.len() && channel == "them" {
            // Normal when no one else is on the call.
            "quiet".to_owned()
        } else if silent == chunks.len() {
            "no signal".to_owned()
        } else if silent > 0 {
            format!("{silent} of {} segments silent", chunks.len())
        } else {
            "OK".to_owned()
        };
        inputs.push(json!({"label":label,"state":state,"ok":silent == 0 || channel == "them"}));
    }
    let failures: Vec<_> = record
        .chunks
        .iter()
        .filter(|chunk| chunk.status == "failed" || !chunk.error.is_empty())
        .collect();
    // Error and drop counts are bookkeeping, not news; the live notice says when audio is
    // really being lost. A stalled input is kept.
    let warning = record
        .warning
        .split_inclusive(". ")
        .filter(|part| !part.contains("input errors"))
        .collect::<String>()
        .trim()
        .to_owned();
    json!({
        "attention": !warning.is_empty() || failed > 0,
        "warning": warning,
        "pending": pending,
        "failed": failed,
        "finding_speakers": pending == 0 && record.speakers == "running",
        "inputs": inputs,
        "failures": failures.iter().take(12).map(|chunk| format!("{} {} · {}", clock(chunk.start_ms), chunk.speaker, chunk.error)).collect::<Vec<_>>(),
        "more": failures.len().saturating_sub(12),
    })
}
impl Drop for Recorder {
    fn drop(&mut self) {
        if let Some((_, handle)) = self.active.take() {
            let _ = handle.finish();
        }
    }
}

/// A demo library for `--demo`, previews and tests: a short meeting with a drafted action item
/// and a recording that captured only silence.
pub fn seed_demo(store: &Store) -> anyhow::Result<()> {
    use tico_recorder_native::audio::Channel;
    let record = store.create("Product & architecture · demo")?;
    let samples: Vec<_> = (0..16000)
        .map(|n| (n as f32 * 440. * std::f32::consts::TAU / 16000.).sin() * 0.15)
        .collect();
    for (index, (speaker, text)) in [
        ("Chris", "The recorder should be small and dense, with a record button and a list of recordings."),
        ("Meeting audio", "We should review the transcript and decide which meetings to send to Tico."),
        ("Chris", "I will update the recording recovery plan and test microphone and meeting audio together."),
    ]
    .into_iter()
    .enumerate()
    {
        let chunk = store.save_audio(
            &record.id,
            if index == 1 { Channel::Meeting } else { Channel::Microphone },
            index as u64 * 5000,
            index as u64 * 5000 + 1000,
            &samples,
            16000,
        )?;
        let claimed = store.claim_transcription()?.expect("a chunk to transcribe");
        store.transcription_result(&claimed, text, "", json!({"source":"synthetic-demo"}))?;
        store.edit_transcript(&record.id, &chunk.id, speaker, text)?;
    }
    let mut context = store.context(&record.id)?;
    context.participants = vec!["Other speaker · unverified".into()];
    context.items = vec![Item {
        id: uuid::Uuid::new_v4().to_string(),
        kind: "task".into(),
        title: "Update the recording recovery plan".into(),
        quote: "I will update the recording recovery plan".into(),
        at_ms: 10000,
        document: "Recording recovery plan".into(),
        change: "Document recovery and the two-source capture test.".into(),
        selected: true,
        ..Default::default()
    }];
    store.save_context(&record.id, &context)?;
    let silent = store.create("Recording test · silence example")?;
    store.save_audio(
        &silent.id,
        Channel::Microphone,
        0,
        1000,
        &vec![0.; 16000],
        16000,
    )?;
    store.mutate(&silent.id, |row| {
        row.warning = "No signal was captured. Silent audio cannot produce a transcript.".into();
        Ok(())
    })?;
    Ok(())
}
