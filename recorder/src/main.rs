use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

mod acceptance;

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
                Command::ReplayTest {
                    tico_url,
                    stt_url,
                    person,
                    bot,
                } => replay_test(&store, tico_url, stt_url, person, bot)?,
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
            store.transcription_result(&chunk, &text, "", json!({"source":"synthetic-provider-fixture"}))?;
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
        tico.roster()?.iter().any(|row| row.0 == format!("bot:{bot}")),
        "The owner list is missing the bot"
    );
    let drafted = assist::draft(store, &config, key, &record.id)?;
    let context = store.context(&record.id)?;
    let item = context
        .items
        .first()
        .context("No action item was drafted")?
        .clone();
    tico.send_task(store, &record.id, &item.id, &format!("bot:{bot}"), "Fixture bot", true)?;
    tico.send_task(store, &record.id, &item.id, &format!("bot:{bot}"), "Fixture bot", true)?;
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
    let read = |path: &std::path::Path| -> Result<Vec<f32>> {
        let mut reader = hound::WavReader::open(path)?;
        anyhow::ensure!(
            reader.spec().sample_rate == 16000 && reader.spec().channels == 1,
            "Expected 16 kHz mono WAV"
        );
        Ok(reader
            .samples::<i16>()
            .map(|value| value.map(|value| value as f32 / 32768.))
            .collect::<std::result::Result<_, _>>()?)
    };
    let record = store.create("Meeting benchmark")?;
    store.mutate(&record.id, |record| {
        record.meeting_audio = true;
        Ok(())
    })?;
    engine::import_audio(store, &record.id, Channel::Microphone, read(me)?)?;
    engine::import_audio(store, &record.id, Channel::Meeting, read(them)?)?;
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
