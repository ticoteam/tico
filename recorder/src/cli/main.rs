use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

mod acceptance;
mod privacy_replay;

#[derive(Parser)]
#[command(
    name = "tico-recorder-cli",
    version,
    about = "Native Tico Recorder diagnostics"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    #[command(hide = true)]
    PrivacyReplayTest {
        #[arg(long)]
        tico_url: String,
        #[arg(long)]
        scenario: String,
    },
    Devices,
    Inspect {
        file: std::path::PathBuf,
    },
    /// Check real STT using a short WAV fixture, without recording or sending to Tico.
    VerifyTranscription {
        file: std::path::PathBuf,
        #[arg(long)]
        expected: String,
        #[arg(long)]
        report: std::path::PathBuf,
        /// Explicitly allow access to the native app's transcription credential.
        #[arg(long)]
        keychain: bool,
        /// Allow a loopback test router; this does not verify a hosted gateway.
        #[arg(long)]
        local_test_gateway: bool,
    },
    List,
    ImportLegacy {
        directory: std::path::PathBuf,
    },
    Record {
        #[arg(long, default_value_t = 5)]
        seconds: u64,
        #[arg(long)]
        microphone: Option<String>,
        #[arg(long)]
        meeting_audio: bool,
    },
    #[command(hide = true)]
    /// One meeting through a local Tico: private live meeting, a person and a bot added, an
    /// action item drafted and sent to the bot as a task, then filed. Also a recording filed
    /// without a live meeting, and a private question. Synthetic audio and a local model fixture.
    ReplayTest {
        #[arg(long)]
        tico_url: String,
        #[arg(long)]
        stt_url: String,
        /// A teammate to add to the private meeting (a roster person ID).
        #[arg(long)]
        person: String,
        /// The bot that joins the meeting and gets the drafted action item.
        #[arg(long)]
        bot: String,
    },
    /// Real speech through a local Tico the way the app sends it: 16 kHz WAV files played in real
    /// time as the microphone (and meeting audio), live text and transcription through Your Tico,
    /// the meeting shared live with a bot. Prints a timeline of what was shared and what the bot
    /// said, then ends the meeting without sending it. Needs an isolated TICO_RECORDER_DATA_DIR
    /// and the person's token in TICO_REPLAY_TEST_TOKEN.
    #[command(hide = true)]
    LiveSpeechTest {
        #[arg(long)]
        tico_url: String,
        /// The bot that joins the meeting.
        #[arg(long)]
        bot: String,
        #[arg(long)]
        me: std::path::PathBuf,
        #[arg(long)]
        them: Option<std::path::PathBuf>,
        /// How long recording goes on after the files end.
        #[arg(long, default_value_t = 15)]
        seconds_after: u64,
    },
    /// Runs the recorder's transcript pipeline on two 16 kHz WAV files (microphone and meeting
    /// audio): chunking, real STT, echo removal and speaker labels. Prints the transcript lines
    /// as JSON. Needs an isolated TICO_RECORDER_DATA_DIR and TICO_RECORDER_STT_KEY; used by
    /// `bench/meeting_bench.py`.
    #[command(hide = true)]
    TranscribeMeeting {
        #[arg(long)]
        me: std::path::PathBuf,
        #[arg(long)]
        them: std::path::PathBuf,
        #[arg(long, default_value = tico_recorder_native::settings::DEFAULT_MODEL)]
        model: String,
        #[arg(long, default_value = "https://openrouter.ai/api/v1")]
        url: String,
        #[arg(long, default_value = "en")]
        language: String,
    },
    /// Labels speakers again on the newest recording of a TICO_RECORDER_DATA_DIR that
    /// transcribe-meeting filled, with an optional DiarizationConfig override; prints the same JSON.
    /// Tunes speaker labels without paying for transcription again.
    #[cfg(feature = "diarize")]
    #[command(hide = true)]
    Relabel {
        #[arg(long)]
        config: Option<String>,
        /// Use these [start s, end s, voice] segments (another diarizer) instead.
        #[arg(long)]
        segments: Option<std::path::PathBuf>,
        /// Exactly this many other voices, as a calendar's attendee count would give.
        #[arg(long)]
        speakers: Option<usize>,
        #[arg(long)]
        max_speakers: Option<usize>,
        /// Voiceprint distance for joining the transcription service's turns; 0 turns it off.
        #[arg(long, default_value_t = tico_recorder_native::diarize::LINK_DISTANCE)]
        link: f32,
    },
    /// Speaker segments of one 16 kHz WAV as JSON, with an optional DiarizationConfig override.
    #[cfg(feature = "diarize")]
    #[command(hide = true)]
    DiarizeWav {
        wav: std::path::PathBuf,
        #[arg(long)]
        config: Option<String>,
        #[arg(long)]
        speakers: Option<usize>,
    },
    /// Browser sign-in, then one renewal; prints the URL to open and never touches the keychain.
    #[command(hide = true)]
    SignInTest {
        #[arg(long)]
        address: String,
    },
}

fn main() -> Result<()> {
    let args = Args::parse();
    match args.command {
        Command::Devices => println!(
            "{}",
            serde_json::to_string_pretty(&tico_recorder_native::capture::sources()?)?
        ),
        Command::Inspect { file } => println!(
            "{}",
            serde_json::to_string_pretty(&tico_recorder_native::audio::inspect_wav(
                &std::fs::read(file)?
            )?)?
        ),
        Command::VerifyTranscription {
            file,
            expected,
            report,
            keychain,
            local_test_gateway,
        } => {
            acceptance::verify_transcription(
                &file,
                &expected,
                &report,
                keychain,
                local_test_gateway,
            )?;
        }
        #[cfg(feature = "diarize")]
        Command::DiarizeWav {
            wav,
            config,
            speakers,
        } => {
            let config = match config {
                Some(text) => serde_json::from_str(&text)?,
                None => Default::default(),
            };
            let bounds = pyannote_local::SpeakerBounds {
                num_speakers: speakers,
                ..Default::default()
            };
            let segments = tico_recorder_native::diarize::diarize_file(&wav, config, bounds)?;
            println!("{}", serde_json::to_string(&segments)?);
        }
        Command::SignInTest { address } => {
            use std::io::Write;
            use tico_recorder_native::{network::Tico, signin};
            let (destination, access, grant) = signin::sign_in(&address, |url| {
                println!("OPEN {url}");
                let _ = std::io::stdout().flush();
            })?;
            let (renewed, _) = signin::renew(&grant)?;
            let who = Tico::new(destination.clone(), renewed.clone())?.verify()?;
            let stale = Tico::new(destination.clone(), access.clone())?
                .verify()
                .is_err();
            println!(
                "{}",
                serde_json::json!({"base_url":destination.base_url,"name":destination.name,
                    "actor":destination.actor,"renewed_actor":who["actor"],
                    "rotated":renewed != access,"old_token_refused":stale})
            );
        }
        command => {
            use tico_recorder_native::{engine::RecordingHandle, settings, store::Store};
            let root = settings::root();
            let _lock = settings::AppLock::acquire(&root)?;
            let store = Store::open(root)?;
            store.recover()?;
            match command {
                Command::List => println!("{}", serde_json::to_string_pretty(&store.list()?)?),
                Command::ImportLegacy { directory } => {
                    println!("Imported {} recordings", store.import_legacy(&directory)?)
                }
                Command::Record {
                    seconds,
                    microphone,
                    meeting_audio,
                } => {
                    anyhow::ensure!((1..=3600).contains(&seconds), "Choose 1–3600 seconds");
                    let record = store.create("Native audio diagnostic")?;
                    let handle = RecordingHandle::start(
                        store.clone(),
                        record.id.clone(),
                        microphone,
                        meeting_audio,
                    )?;
                    std::thread::sleep(std::time::Duration::from_secs(seconds));
                    println!("{}", serde_json::to_string_pretty(&handle.status())?);
                    handle.finish()?;
                    println!("{}", serde_json::to_string_pretty(&store.get(&record.id)?)?);
                }
                Command::TranscribeMeeting {
                    me,
                    them,
                    model,
                    url,
                    language,
                } => transcribe_meeting(&store, &me, &them, model, url, language)?,
                #[cfg(feature = "diarize")]
                Command::Relabel {
                    config,
                    segments,
                    speakers,
                    max_speakers,
                    link,
                } => {
                    anyhow::ensure!(
                        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
                        "Set TICO_RECORDER_DATA_DIR to a transcribe-meeting folder"
                    );
                    let record = store.list()?.into_iter().next().context("No recording")?;
                    let config = match config {
                        Some(text) => serde_json::from_str(&text)?,
                        None => Default::default(),
                    };
                    let voices = match segments {
                        Some(path) => {
                            let rows: Vec<(f64, f64, usize)> =
                                serde_json::from_slice(&std::fs::read(path)?)?;
                            let rows = rows
                                .into_iter()
                                .map(|(start, end, speaker)| pyannote_local::SpeakerSegment {
                                    start,
                                    end,
                                    speaker,
                                })
                                .collect();
                            tico_recorder_native::diarize::label_segments(
                                &store,
                                &record.id,
                                rows,
                                (link > 0.).then_some(link),
                            )
                        }
                        None => tico_recorder_native::diarize::label_with(
                            &store,
                            &record.id,
                            config,
                            pyannote_local::SpeakerBounds {
                                num_speakers: speakers,
                                max_speakers,
                                ..Default::default()
                            },
                            (link > 0.).then_some(link),
                        ),
                    }
                    .map_err(|error| format!("{error:#}"));
                    let record = store.get(&record.id)?;
                    println!(
                        "{}",
                        meeting_json(
                            &record,
                            serde_json::json!({"voices":voices.as_ref().ok(),"speaker_error":voices.as_ref().err()})
                        )
                    );
                }
                Command::PrivacyReplayTest { tico_url, scenario } => {
                    privacy_replay::run(&store, &tico_url, &scenario)?
                }
                Command::ReplayTest {
                    tico_url,
                    stt_url,
                    person,
                    bot,
                } => replay_test(&store, tico_url, stt_url, person, bot)?,
                Command::LiveSpeechTest {
                    tico_url,
                    bot,
                    me,
                    them,
                    seconds_after,
                } => live_speech_test(&store, tico_url, bot, &me, them.as_deref(), seconds_after)?,
                _ => unreachable!(),
            }
        }
    }
    Ok(())
}

fn replay_test(
    store: &tico_recorder_native::store::Store,
    tico_url: String,
    stt_url: String,
    person: String,
    bot: String,
) -> Result<()> {
    use serde_json::json;
    use tico_recorder_native::{
        assist,
        audio::Channel,
        capture::{AudioSink, CaptureStream, Source},
        engine::RecordingHandle,
        network::{Tico, transcribe_audio},
        settings::{Destination, Settings},
        workspace::Item,
    };
    anyhow::ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Replay fixtures require an isolated TICO_RECORDER_DATA_DIR"
    );
    for base in [&tico_url, &stt_url] {
        let url = reqwest::Url::parse(base)?;
        anyhow::ensure!(
            ["127.0.0.1", "localhost", "::1", "[::1]"].contains(&url.host_str().unwrap_or("")),
            "Replay fixtures only run against localhost"
        );
    }
    struct Synthetic;
    impl CaptureStream for Synthetic {
        fn stop(&mut self) -> Result<()> {
            Ok(())
        }
    }
    fn source(channel: Channel, frequency: f32) -> Source {
        let (mut sink, consumer) = AudioSink::channel();
        let health = sink.health.clone();
        let rate = 48000;
        let samples: Vec<_> = (0..rate)
            .map(|index| {
                (index as f32 * frequency * std::f32::consts::TAU / rate as f32).sin() * 0.2
            })
            .collect();
        sink.push_interleaved(&samples, 1);
        Source {
            channel,
            name: "Synthetic fixture".into(),
            sample_rate: rate,
            started: std::time::Instant::now(),
            health,
            consumer,
            stream: Box::new(Synthetic),
        }
    }
    let config = Settings {
        transcription_url: stt_url,
        ..Default::default()
    };
    let key = "synthetic-stt-key";
    let transcribe_all = || -> Result<()> {
        while let Some(chunk) = store.claim_transcription()? {
            let text = transcribe_audio(&config, key, &std::fs::read(store.audio_path(&chunk)?)?)?;
            store.transcription_result(
                &chunk,
                &text,
                "",
                json!({"source":"synthetic-provider-fixture"}),
            )?;
        }
        Ok(())
    };
    let record = store.create("Native end-to-end replay · synthetic")?;
    let handle = RecordingHandle::with_sources(store.clone(), record.id.clone(), || {
        Ok((
            vec![
                source(Channel::Microphone, 440.),
                source(Channel::Meeting, 660.),
            ],
            String::new(),
        ))
    })?;
    for _ in 0..100 {
        if handle.status().sources.len() == 2
            && handle
                .status()
                .sources
                .iter()
                .all(|source| source.frames > 0)
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    handle.finish()?;
    transcribe_all()?;
    let tico = Tico::new(
        Destination {
            id: "fixture".into(),
            name: "Local Tico fixture".into(),
            base_url: tico_url,
            actor: String::new(),
        },
        std::env::var("TICO_REPLAY_TEST_TOKEN")?,
    )?;
    // The People tab: adding someone opens the meeting in Tico, private.
    let live_id = tico.ensure_live(store, &record.id, false)?;
    tico.call(
        &format!("/api/v2/live-meetings/{live_id}/access"),
        Some(&json!({"add":[person],"remove":[]})),
        "fixture:access",
    )?;
    tico.call(
        &format!("/api/v2/live-meetings/{live_id}/bots"),
        Some(&json!({"bots":[bot]})),
        "fixture:attach",
    )?;
    let view = tico.sync_live(store, &record.id)?;
    // The Actions tab: the owner picker lists the bot; one drafted item goes to it now, and one
    // typed item waits for Send.
    anyhow::ensure!(
        tico.roster()?
            .iter()
            .any(|row| row.0 == format!("bot:{bot}")),
        "The owner list is missing the bot"
    );
    let drafted = assist::draft(store, &config, key, &record.id)?;
    let context = store.context(&record.id)?;
    let item = context
        .items
        .first()
        .context("No action item was drafted")?
        .clone();
    tico.send_task(
        store,
        &record.id,
        &item.id,
        &format!("bot:{bot}"),
        "Fixture bot",
        true,
    )?;
    tico.send_task(
        store,
        &record.id,
        &item.id,
        &format!("bot:{bot}"),
        "Fixture bot",
        true,
    )?;
    store.update_context(&record.id, |context| {
        context.items.push(Item {
            id: "typed-item".into(),
            kind: "task".into(),
            title: "Book the launch room".into(),
            ..Default::default()
        });
        context.notes = "Reviewed replay notes".into();
        Ok(())
    })?;
    tico.finish_live(store, &record.id)?;
    let first = tico.send(store, &record.id, false)?;
    let second = tico.send(store, &record.id, false)?;
    anyhow::ensure!(first == second, "Retry created another meeting");
    let sent = store.context(&record.id)?;
    // A recording never opened in Tico, sent at the end, with a private question first.
    let local = store.create("Selected local recording · synthetic")?;
    let source_chunk = store
        .get(&record.id)?
        .chunks
        .into_iter()
        .find(|chunk| chunk.channel == "me")
        .context("No microphone chunk")?;
    store.save_wav(
        &local.id,
        Channel::Microphone,
        0,
        1000,
        &std::fs::read(store.audio_path(&source_chunk)?)?,
    )?;
    transcribe_all()?;
    let answer = assist::ask(store, &config, key, &local.id, "What did I commit to?")?;
    store.update_context(&local.id, |context| {
        context.notes = "Selected local meeting notes".into();
        context.calendar =
            json!({"event_id":"selected-local-calendar-event","attendees":["ana@acme.example"]});
        Ok(())
    })?;
    let local_id = tico.send(store, &local.id, false)?;
    anyhow::ensure!(
        local_id == tico.send(store, &local.id, false)?,
        "Local import retry created another meeting"
    );
    let record = store.get(&record.id)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "recording_id":record.id,"meeting_id":first,"local_meeting_id":local_id,"live_id":live_id,
            "live_view":view,"chunks":record.chunks.len(),
            "transcripts":record.chunks.iter().map(|chunk|&chunk.text).collect::<Vec<_>>(),
            "drafts":drafted,"items":sent.items,"assist":answer,
            "receipt":record.deliveries,"synthetic":true}))?
    );
    Ok(())
}

fn live_speech_test(
    store: &tico_recorder_native::store::Store,
    tico_url: String,
    bot: String,
    me: &std::path::Path,
    them: Option<&std::path::Path>,
    seconds_after: u64,
) -> Result<()> {
    use serde_json::{Value, json};
    use std::{
        collections::BTreeMap,
        time::{Duration, Instant},
    };
    use tico_recorder_native::{
        audio::Channel,
        capture,
        engine::RecordingHandle,
        live::LiveSync,
        network::{Tico, Transcriber},
        notices,
        settings::{self, Destination, Settings},
        speech, stream,
    };
    anyhow::ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Use an isolated TICO_RECORDER_DATA_DIR"
    );
    let url = reqwest::Url::parse(&tico_url)?;
    anyhow::ensure!(
        ["127.0.0.1", "localhost", "::1", "[::1]"].contains(&url.host_str().unwrap_or("")),
        "The live speech test only runs against localhost"
    );
    // Signed in to the Tico as the person, with speech through it. The token is held for this
    // run only, not saved in the keychain.
    let destination = Destination {
        id: "speech-test".into(),
        name: "Local Tico".into(),
        base_url: tico_url,
        actor: String::new(),
    };
    let token = std::env::var("TICO_REPLAY_TEST_TOKEN").context("Set TICO_REPLAY_TEST_TOKEN")?;
    settings::hold_secret(&format!("tico-grant:{}", destination.id), "")?;
    settings::hold_secret(&format!("tico:{}", destination.id), &token)?;
    let config = Settings {
        destinations: vec![destination.clone()],
        speech_source: speech::TICO.into(),
        streaming: Some(true),
        ..Default::default()
    };
    settings::write(store.root(), &config)?;
    let services = speech::refresh(&destination)?;
    anyhow::ensure!(
        services.stream && services.transcribe,
        "This Tico can't stream and transcribe: {services:?}"
    );
    // The app's background workers: transcription (with speaker labels) and live sharing.
    let _transcriber = Transcriber::start(store.clone());
    let _live = LiveSync::start(store.clone());
    let mut inputs = vec![(Channel::Microphone, read_wav(me)?)];
    if let Some(them) = them {
        inputs.push((Channel::Meeting, read_wav(them)?));
    }
    let speech_ms = inputs
        .iter()
        .map(|(_, samples)| samples.len() as u64 * 1000 / 16000)
        .max()
        .unwrap_or(0);
    let record = store.create("Live speech test")?;
    let id = record.id.clone();
    let started = Instant::now();
    let handle = RecordingHandle::with_sources_streaming(
        store.clone(),
        id.clone(),
        stream::Plan::from_settings(&config),
        move || {
            let sources = inputs
                .into_iter()
                .map(|(channel, samples)| {
                    capture::start_paced(channel, "Speech file", 16000, move |frame| {
                        samples.get(frame as usize).copied().unwrap_or(0.)
                    })
                })
                .collect();
            Ok((sources, String::new()))
        },
    )?;
    let at = || started.elapsed().as_millis() as u64;
    // People: adding the bot opens the meeting in Tico, private, as the app does.
    let tico = Tico::saved(&config, &destination.id)?;
    let live_id = tico.ensure_live(store, &id, false)?;
    tico.call(
        &format!("/api/v2/live-meetings/{live_id}/bots"),
        Some(&json!({"bots":[bot]})),
        &format!("recorder:{id}:attach:{bot}"),
    )?;
    let attached_ms = at();
    // Everything below is in ms since recording started: streamed words as they arrive, turns
    // queued and acknowledged, corrections, finished chunks and the meeting's events.
    let mut words: BTreeMap<(String, u64, String), u64> = BTreeMap::new();
    let mut turns: BTreeMap<i64, Value> = BTreeMap::new();
    let mut corrections: BTreeMap<i64, Value> = BTreeMap::new();
    let mut chunks: BTreeMap<String, Value> = BTreeMap::new();
    let mut events: Vec<Value> = vec![];
    let mut errors: Vec<Value> = vec![];
    let (mut after, mut quiet_since, mut polled, mut synced) = (0, 0, 0, 0);
    let stop_at = speech_ms + seconds_after * 1000;
    let mut stopped = false;
    loop {
        let now = at();
        if !stopped && now >= stop_at {
            handle.stop();
            stopped = true;
        }
        for (channel, list) in store.live_words(&id)? {
            for word in list {
                words
                    .entry((channel.clone(), word.start_ms, word.text))
                    .or_insert(now);
            }
        }
        {
            let db = store.db()?;
            let mut query = db.prepare(
                "SELECT seq,payload_json,acknowledged FROM live_outbox WHERE recording_id=? ORDER BY seq",
            )?;
            for row in query.query_map([&id], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, bool>(2)?,
                ))
            })? {
                let (seq, payload, acknowledged) = row?;
                let turn = turns.entry(seq).or_insert_with(|| {
                    let mut turn: Value = serde_json::from_str(&payload).unwrap_or_default();
                    turn["queued_ms"] = json!(now);
                    turn
                });
                if acknowledged && turn["ack_ms"].is_null() {
                    turn["ack_ms"] = json!(now);
                }
            }
            drop(query);
            let mut query = db.prepare(
                "SELECT rowid,seq,revision,payload_json,state FROM live_corrections WHERE recording_id=?",
            )?;
            for row in query.query_map([&id], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                ))
            })? {
                let (rowid, seq, revision, payload, state) = row?;
                let correction = corrections.entry(rowid).or_insert_with(|| {
                    let payload: Value = serde_json::from_str(&payload).unwrap_or_default();
                    json!({"seq":seq,"revision":revision,"speaker":payload["speaker"],"text":payload["text"],"queued_ms":now})
                });
                if state != 0 && correction["posted_ms"].is_null() {
                    correction["posted_ms"] = json!(now);
                    correction["state"] = json!(if state == 1 {
                        "posted"
                    } else {
                        "kept streamed text"
                    });
                }
            }
        }
        let current = store.get(&id)?;
        for chunk in &current.chunks {
            let row = chunks.entry(chunk.id.clone()).or_insert_with(|| {
                json!({"channel":chunk.channel,"start_ms":chunk.start_ms,"end_ms":chunk.end_ms,"saved_ms":now})
            });
            if ["done", "silence"].contains(&chunk.status.as_str()) && row["done_ms"].is_null() {
                row["done_ms"] = json!(now);
                row["status"] = json!(chunk.status);
                row["text"] = json!(chunk.text);
            }
            if !chunk.error.is_empty() {
                row["error"] = json!(chunk.error);
            }
        }
        if now >= polled + 250 {
            polled = now;
            match tico.events(&format!(
                "/api/v2/live-meetings/{live_id}/events?after={after}"
            )) {
                Ok(rows) => {
                    for (event, kind, payload) in rows {
                        after = after.max(event);
                        quiet_since = at();
                        events.push(
                            json!({"id":event,"type":kind,"ms":quiet_since,"payload":payload}),
                        );
                    }
                }
                Err(error) => errors.push(json!({"ms":now,"events":format!("{error:#}")})),
            }
        }
        // The window's own sync while it shows the meeting: every 2.5 s, with the bots' notices.
        if now >= synced + 2500 {
            synced = now;
            let result = tico.sync_live(store, &id).and_then(|view| {
                view["event_id"]
                    .as_u64()
                    .map_or(Ok(()), |latest| tico.read_notices(&live_id, latest))
            });
            if let Err(error) = result {
                errors.push(json!({"ms":now,"sync":format!("{error:#}")}));
            }
        }
        let settled = stopped
            && handle.is_finished()
            && current
                .chunks
                .iter()
                .all(|chunk| ["done", "silence"].contains(&chunk.status.as_str()))
            && turns.values().all(|turn| !turn["ack_ms"].is_null())
            && corrections.values().all(|row| !row["posted_ms"].is_null());
        if settled && now > quiet_since.max(stop_at) + 8000 || now > stop_at + 120_000 {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    handle.finish()?;
    let finish = tico
        .finish_live(store, &id)
        .map_err(|error| format!("{error:#}"));
    // Each streamed turn that names the bot (its first word, so a misheard "PM" still counts):
    // from the end of its words to them arriving as final text, to Tico acknowledging the turn,
    // and to the bot's reply to it.
    let name = bot.split('-').next().unwrap_or_default().to_lowercase();
    let mut asks = vec![];
    for (seq, turn) in &turns {
        let text = turn["text"].as_str().unwrap_or_default().to_lowercase();
        if !text.contains(&name) {
            continue;
        }
        let end = turn["end_ms"].as_u64().unwrap_or(0);
        let final_ms = words
            .iter()
            .filter(|((_, start, _), _)| {
                (turn["start_ms"].as_u64().unwrap_or(0)..=end).contains(start)
            })
            .map(|(_, arrived)| *arrived)
            .max();
        let reply = events.iter().find(|event| {
            event["type"] == "meeting.bot_reply" && event["payload"]["transcript_seq"] == json!(seq)
        });
        let since = |ms: Option<u64>| ms.map(|ms| ms as i64 - end as i64);
        asks.push(json!({"seq":seq,"text":turn["text"],"end_ms":end,
            "final_after_ms":since(final_ms),"ack_after_ms":since(turn["ack_ms"].as_u64()),
            "reply_after_ms":since(reply.and_then(|event| event["ms"].as_u64())),
            "reply":reply.map(|event| &event["payload"]["text"])}));
    }
    let kinds = [
        "meeting.bot_reply",
        "meeting.model",
        "meeting.router",
        "meeting.chat",
    ];
    let record = store.get(&id)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "live_id":live_id,"recording_id":id,"speech_ms":speech_ms,"stopped_ms":stop_at,
            "attached_ms":attached_ms,"asks":asks,
            "streamed":turns.values().collect::<Vec<_>>(),
            "corrections":corrections.values().collect::<Vec<_>>(),
            "chunks":chunks.values().collect::<Vec<_>>(),
            "bot_events":events.iter().filter(|event| {
                let kind = event["type"].as_str().unwrap_or_default();
                kinds.contains(&kind) || kind.starts_with("meeting.bot_turn")
            }).collect::<Vec<_>>(),
            "notices":notices::notices(&live_id),
            "errors":errors,"finish":finish,
            "lines":meeting_json(&record, json!({}))["lines"]}))?
    );
    Ok(())
}

fn transcribe_meeting(
    store: &tico_recorder_native::store::Store,
    me: &std::path::Path,
    them: &std::path::Path,
    model: String,
    url: String,
    language: String,
) -> Result<()> {
    use tico_recorder_native::{audio::Channel, engine, network, settings::Settings};
    anyhow::ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Use an isolated TICO_RECORDER_DATA_DIR"
    );
    let key = std::env::var("TICO_RECORDER_STT_KEY").context("Set TICO_RECORDER_STT_KEY")?;
    let record = store.create("Meeting benchmark")?;
    store.mutate(&record.id, |record| {
        record.meeting_audio = true;
        Ok(())
    })?;
    engine::import_audio(store, &record.id, Channel::Microphone, read_wav(me)?)?;
    engine::import_audio(store, &record.id, Channel::Meeting, read_wav(them)?)?;
    let config = Settings {
        transcription_url: url,
        transcription_model: model,
        transcription_language: language,
        ..Default::default()
    };
    let started = std::time::Instant::now();
    std::thread::scope(|scope| {
        for _ in 0..8 {
            scope
                .spawn(|| while network::transcribe_next(store, &config, &key).unwrap_or(false) {});
        }
    });
    let transcribed_s = started.elapsed().as_secs_f64();
    #[cfg(feature = "diarize")]
    let voices = tico_recorder_native::diarize::label(store, &record.id)
        .map_err(|error| format!("{error:#}"));
    #[cfg(not(feature = "diarize"))]
    let voices: std::result::Result<usize, String> = Err("Built without diarize".into());
    let record = store.get(&record.id)?;
    let errors: Vec<_> = record
        .chunks
        .iter()
        .filter(|chunk| !chunk.error.is_empty())
        .map(|chunk| &chunk.error)
        .collect();
    println!(
        "{}",
        meeting_json(
            &record,
            serde_json::json!({"model":config.transcription_model,"chunks":record.chunks.len(),"errors":errors,
                "voices":voices.as_ref().ok(),"speaker_error":voices.as_ref().err(),"transcribe_seconds":transcribed_s,
                "total_seconds":started.elapsed().as_secs_f64()})
        )
    );
    Ok(())
}
/// The samples of a 16 kHz mono WAV file.
fn read_wav(path: &std::path::Path) -> Result<Vec<f32>> {
    let mut reader = hound::WavReader::open(path)?;
    anyhow::ensure!(
        reader.spec().sample_rate == 16000 && reader.spec().channels == 1,
        "Expected 16 kHz mono WAV"
    );
    Ok(reader
        .samples::<i16>()
        .map(|value| value.map(|value| value as f32 / 32768.))
        .collect::<std::result::Result<_, _>>()?)
}
/// `fields` plus the recording's transcript lines.
fn meeting_json(
    record: &tico_recorder_native::store::Recording,
    mut fields: serde_json::Value,
) -> serde_json::Value {
    fields["lines"] = tico_recorder_native::transcript::lines(record)
        .iter()
        .map(|line| serde_json::json!({"speaker":line.speaker,"me":line.me,"start_ms":line.start_ms,"end_ms":line.end_ms,"text":line.text}))
        .collect();
    fields
}
