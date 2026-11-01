//! Behaviour of the recorder's actions and what the window is told to draw. These replace the
//! egui harness tests: the same scenarios, checked on `Recorder` and its `view`.
use super::recorder::*;
use serde_json::{Value, json};
use std::time::{Duration, Instant};
use tico_recorder_native::{
    settings::{self, AppLock, Destination},
    store::{self, Store},
    workspace::{Item, Turn},
};

fn app(store: Store, directory: &std::path::Path) -> Recorder {
    let lock = AppLock::acquire(directory).unwrap();
    Recorder::new(store, Some(lock), true).unwrap()
}
fn connection(id: &str, name: &str) -> Destination {
    Destination {
        id: id.into(),
        name: name.into(),
        base_url: format!("https://runner.{name}"),
        actor: "human:1".into(),
    }
}
fn full_view(app: &mut Recorder) -> Value {
    app.view(&Have::default())
}
fn row<'a>(view: &'a Value, title: &str) -> &'a Value {
    view["library"]["data"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["title"] == title)
        .unwrap_or_else(|| panic!("no library row {title}"))
}
/// Runs background work until `done` holds or two seconds pass.
fn settle(app: &mut Recorder, done: impl Fn(&Recorder) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(2);
    while !done(app) && Instant::now() < deadline {
        app.tick();
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn connect_links_carry_only_a_safe_tico_address() {
    assert_eq!(
        connect_address("tico-recorder://connect?server=https://tico.example.com").as_deref(),
        Some("https://tico.example.com")
    );
    assert_eq!(
        connect_address("tico-recorder://connect?server=http://localhost:8000").as_deref(),
        Some("http://localhost:8000")
    );
    for link in [
        "tico-recorder://connect?server=http://evil.example",
        "tico-recorder://delete?server=https://tico.example.com",
        "https://connect?server=https://tico.example.com",
        "tico-recorder://connect",
    ] {
        assert_eq!(connect_address(link), None, "{link}");
    }
    let directory = tempfile::tempdir().unwrap();
    let mut app = app(Store::open(directory.path()).unwrap(), directory.path());
    app.link("tico-recorder://connect?server=https://tico.example.com");
    assert_eq!(
        full_view(&mut app)["status"]["connect"],
        "https://tico.example.com"
    );
    app.dismiss_connect();
    app.link("tico-recorder://connect?server=http://evil.example");
    assert!(app.connect.is_none());
    assert_eq!(app.error, "That link is not a Tico Recorder connect link");
}

#[test]
fn library_rows_send_and_delete_only_what_is_stopped() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    app.config.destinations = vec![connection("fixture", "example.com")];
    let view = full_view(&mut app);
    let count = app.records.len();
    let demo = row(&view, "Product & architecture · demo");
    assert_eq!(demo["can_send"], true);
    assert_eq!(
        demo["targets"],
        json!([{"id":"fixture","name":"example.com","already":false}])
    );
    assert_eq!(
        demo["status"],
        Value::Null,
        "a finished private recording needs no status"
    );
    let silent = row(&view, "Recording test · silence example");
    assert_eq!(silent["status"]["label"], "No signal");
    let target = demo["id"].as_str().unwrap().to_owned();
    // A recording in progress is not deleted.
    app.store.state(&target, "recording").unwrap();
    app.delete(&target);
    assert_eq!(app.error, "Stop recording before deleting it");
    assert_eq!(app.records.len(), count);
    app.store.state(&target, "stopped").unwrap();
    app.dismiss_error();
    app.delete(&target);
    assert!(app.error.is_empty());
    assert_eq!(app.records.len(), count - 1);
    assert!(app.store.get(&target).is_err());
    assert!(!directory.path().join("audio").join(&target).exists());
}

#[test]
fn a_meeting_in_one_tico_can_still_be_sent_to_another() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    let target = app
        .records
        .iter()
        .find(|row| row.chunks > row.silent && row.deliveries.is_empty())
        .map(|row| row.id.clone())
        .unwrap();
    app.config.destinations = vec![
        connection("first", "example.com"),
        connection("second", "tico.team"),
    ];
    app.destination = "first".into();
    app.store
        .delivery(
            &target,
            &store::Delivery {
                destination_id: "first".into(),
                meeting_id: "meeting-1".into(),
                status: "sent".into(),
                error: String::new(),
                receipt: json!({}),
            },
        )
        .unwrap();
    app.reload();
    let record = app
        .records
        .iter()
        .find(|row| row.id == target)
        .cloned()
        .unwrap();
    assert!(!app.sent_everywhere(&record.deliveries));
    let view = full_view(&mut app);
    let listed = view["library"]["data"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["id"] == target)
        .unwrap();
    assert_eq!(listed["status"]["label"], "In Tico");
    assert_eq!(listed["can_send"], true);
    assert_eq!(
        listed["targets"],
        json!([{"id":"first","name":"example.com","already":true},
               {"id":"second","name":"tico.team","already":false}])
    );
    // Sending again to the Tico that has it does nothing.
    app.send_meeting(&target, "first");
    assert!(!app.busy);
    // Once open live in one Tico, it can only go there.
    app.store
        .update_context(&target, |context| {
            context.live_destination = "second".into();
            Ok(())
        })
        .unwrap();
    assert_eq!(
        app.send_targets(&target, &record.deliveries),
        vec![json!({"id":"second","name":"tico.team","already":false})]
    );
}

#[test]
fn opening_a_meeting_keeps_background_drafts_and_requires_explicit_sharing() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    let view = full_view(&mut app);
    assert_eq!(view["status"]["demo"], true);
    assert!(view["meeting"]["data"].is_null());
    let id = row(&view, "Product & architecture · demo")["id"]
        .as_str()
        .unwrap()
        .to_owned();
    app.open(&id).unwrap();
    let view = full_view(&mut app);
    let meeting = &view["meeting"]["data"];
    assert_eq!(meeting["title"], "Product & architecture · demo");
    assert_eq!(meeting["place"], "Private · on this Mac");
    assert_eq!(meeting["live"], Value::Null);
    assert_eq!(meeting["actions"]["waiting"], 1);
    assert_eq!(meeting["actions"]["mode"], "signin");
    assert_eq!(meeting["people"]["stage"], "invite");
    assert_eq!(meeting["chat"]["private"], true);
    assert_eq!(
        view["transcript"]["data"]["blocks"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    // A background draft survives the person's next edit of an older copy.
    app.store
        .update_context(&id, |context| {
            context.items.push(Item {
                id: "background".into(),
                kind: "task".into(),
                title: "Send the release notes".into(),
                ..Default::default()
            });
            Ok(())
        })
        .unwrap();
    app.set_notes("Decided: ship Friday");
    assert_eq!(app.context.items.len(), 2);
    assert_eq!(app.context.notes, "Decided: ship Friday");
    // Typed items, edits and removal stay on this Mac.
    app.add_item("Draft the release notes");
    let typed = app.context.items.last().unwrap().id.clone();
    app.edit_item(&typed, "Draft and send the release notes");
    assert_eq!(
        app.context.items[2].title,
        "Draft and send the release notes"
    );
    app.remove_item(&typed);
    assert_eq!(app.store.context(&id).unwrap().items.len(), 2);
    // Sending needs a Tico: without one nothing happens.
    app.send_item("background", "bot:librarian");
    assert!(!app.busy);
    // Nothing reaches Tico until a bot or person is added; Add people alone starts nothing.
    app.start_adding();
    assert_eq!(
        full_view(&mut app)["meeting"]["data"]["people"]["stage"],
        "add"
    );
    app.add_participant("Alex (not in Tico)");
    assert!(app.context.live_id.is_empty());
    assert_eq!(app.store.context(&id).unwrap().participants.len(), 2);
    app.pick("human:2");
    assert_eq!(app.error, "Sign in to a Tico first");
    assert!(app.context.live_id.is_empty());
    assert!(!app.busy);
    app.close();
    assert!(app.meeting.is_none());
    // A silent recording cannot be sent, and says why under Audio.
    let silent = row(&full_view(&mut app), "Recording test · silence example")["id"]
        .as_str()
        .unwrap()
        .to_owned();
    app.open(&silent).unwrap();
    let meeting = full_view(&mut app)["meeting"]["data"].clone();
    assert_eq!(meeting["audio"]["attention"], true);
    assert_eq!(
        meeting["audio"]["warning"],
        "No signal was captured. Silent audio cannot produce a transcript."
    );
    assert_eq!(
        meeting["send_blocked"],
        "This recording has no transcript to send"
    );
}

#[test]
fn a_sent_or_ended_meeting_is_never_reshared() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    app.config.destinations = vec![connection("fixture", "Tico Team")];
    app.destination = "fixture".into();
    let id = app
        .records
        .iter()
        .find(|row| row.title.starts_with("Product"))
        .unwrap()
        .id
        .clone();
    app.store
        .delivery(
            &id,
            &store::Delivery {
                destination_id: "fixture".into(),
                meeting_id: "meeting-1".into(),
                status: "sent".into(),
                error: String::new(),
                receipt: json!({}),
            },
        )
        .unwrap();
    app.open(&id).unwrap();
    for share in [
        (|app: &mut Recorder| app.pick("bot:librarian")) as fn(&mut Recorder),
        |app| app.set_team(true),
        |app| app.remove_access("human:2"),
    ] {
        app.dismiss_error();
        share(&mut app);
        assert_eq!(app.error, "This recording was already sent to Tico");
        assert!(!app.busy);
    }
    let meeting = full_view(&mut app)["meeting"]["data"].clone();
    assert_eq!(meeting["people"]["can_share"], false);
    // Its chat can only ask Tico privately; a message would have to connect it.
    assert_eq!(
        (
            meeting["chat"]["can_message"].as_bool(),
            meeting["chat"]["private"].as_bool()
        ),
        (Some(false), Some(true))
    );
    assert_eq!(meeting["notes"]["editable"], false);
    assert_eq!(meeting["title_editable"], false);
    // Renaming a sent meeting is refused.
    app.rename("Something else");
    assert_eq!(
        app.store.get(&id).unwrap().title,
        "Product & architecture · demo"
    );
}

#[test]
fn a_chosen_bot_shows_at_once_and_leaves_with_the_reason_if_tico_refuses() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    app.config.destinations = vec![connection("fixture", "Tico Team")];
    app.destination = "fixture".into();
    app.roster = vec![
        ("bot:librarian".into(), "Librarian".into()),
        ("human:2".into(), "Arthur Silva".into()),
    ];
    let id = app
        .records
        .iter()
        .find(|row| row.title.starts_with("Product"))
        .unwrap()
        .id
        .clone();
    app.open(&id).unwrap();
    app.pick("bot:librarian");
    let people = full_view(&mut app)["meeting"]["data"]["people"].clone();
    let pending: Vec<_> = people["rows"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|row| row["pending"] == true)
        .collect();
    assert_eq!(pending.len(), 1);
    assert_eq!(
        (pending[0]["name"].as_str(), pending[0]["kind"].as_str()),
        (Some("Librarian"), Some("bot"))
    );
    assert!(
        people["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["actor"] != "bot:librarian")
    );
    // Choosing it again while it is on its way does nothing.
    app.pick("bot:librarian");
    // This fixture's Tico cannot be reached, so the bot leaves People again with the reason.
    settle(&mut app, |app| !app.busy && app.adding_people_count() == 0);
    let people = full_view(&mut app)["meeting"]["data"]["people"].clone();
    assert!(
        people["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["pending"] != true)
    );
    assert!(
        people["error"].as_str().unwrap().starts_with("Not added: "),
        "{}",
        people["error"]
    );
    assert!(
        app.error.is_empty(),
        "the reason shows in People, not as a banner"
    );
}

#[test]
fn long_meetings_read_as_turns() {
    use tico_recorder_native::audio::Channel;
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Quarterly planning · long fixture").unwrap();
    let samples = vec![0.1; 1600];
    let lines = [
        "We agreed the recorder ships this week after the reliability pass.",
        "Who owns the release notes?",
        "I will draft them tonight and send them to the team.",
        "Then the onboarding doc needs the new microphone steps.",
    ];
    // Two hours of conversation in six-second chunks, with a pause every ten turns.
    for index in 0..1200u64 {
        let them = index / 3 % 2 == 1;
        store
            .save_audio(
                &record.id,
                if them {
                    Channel::Meeting
                } else {
                    Channel::Microphone
                },
                index * 6000,
                index * 6000 + 5800,
                &samples,
                16000,
            )
            .unwrap();
        let claimed = store.claim_transcription().unwrap().unwrap();
        let text = if index % 10 == 9 {
            ""
        } else {
            lines[index as usize % lines.len()]
        };
        store
            .transcription_result(&claimed, text, "", json!({"source":"fixture"}))
            .unwrap();
    }
    let blocks = transcript_blocks(&store.get(&record.id).unwrap());
    assert_eq!(blocks.len(), 400);
    assert!(blocks.iter().all(|block| block.paragraphs.len() <= 2));
    let mut app = app(store, directory.path());
    app.open(&record.id).unwrap();
    let started = Instant::now();
    let view = full_view(&mut app);
    assert_eq!(
        view["transcript"]["data"]["blocks"]
            .as_array()
            .unwrap()
            .len(),
        400
    );
    assert_eq!(view["meeting"]["data"]["chat"]["messages"], json!([]));
    // Polling an unchanged meeting sends nothing big again.
    let have = Have {
        library: view["library"]["hash"].as_str().unwrap().into(),
        meeting: view["meeting"]["hash"].as_str().unwrap().into(),
        transcript: view["transcript"]["hash"].as_str().unwrap().into(),
        settings: view["settings"]["hash"].as_str().unwrap().into(),
    };
    let again = app.view(&have);
    for part in ["library", "meeting", "transcript", "settings"] {
        assert!(again[part].is_null(), "{part} was sent again");
    }
    assert!(started.elapsed() < Duration::from_secs(2));
}

#[test]
fn background_delivery_failure_is_visible_in_the_library_and_meeting_without_losing_local_data() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Shared recording fixture").unwrap();
    store
        .create("Stopped before audio inputs connected")
        .unwrap();
    store
        .update_context(&record.id, |context| {
            context.live_id = "fixture-live-room".into();
            context.live_destination = "unconfigured-fixture-connection".into();
            context.live_state = "live".into();
            Ok(())
        })
        .unwrap();
    let worker = tico_recorder_native::live::LiveSync::start(store.clone());
    let deadline = Instant::now() + Duration::from_secs(2);
    while worker.error(&record.id).is_empty() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    let error = worker.error(&record.id);
    assert!(!error.is_empty());
    let mut app = app(store, directory.path());
    app.live_sync = Some(worker);
    let view = full_view(&mut app);
    assert_eq!(
        row(&view, "Shared recording fixture")["status"]["label"],
        "Tico sync needs attention"
    );
    assert_eq!(
        row(&view, "Stopped before audio inputs connected")["status"]["label"],
        "No audio"
    );
    app.open(&record.id).unwrap();
    let meeting = full_view(&mut app)["meeting"]["data"].clone();
    assert_eq!(
        meeting["sync_error"],
        format!(
            "Tico sync needs attention: {error}. Audio and transcript remain saved on this Mac."
        )
    );
    assert_eq!(meeting["live"], "Private in Tico");
    assert_eq!(meeting["place"], "Shared live · ");
    let stored = app.store.get(&record.id).unwrap();
    assert_eq!(stored.state, "stopped");
    assert!(stored.deliveries.is_empty());
}

#[test]
fn a_recording_shows_live_levels_and_stopping_saves_it() {
    use tico_recorder_native::capture::{AudioSink, CaptureStream, Source};
    use tico_recorder_native::engine::RecordingHandle;
    struct Synthetic;
    impl CaptureStream for Synthetic {
        fn stop(&mut self) -> anyhow::Result<()> {
            Ok(())
        }
    }
    let synthetic = |channel| {
        let (mut sink, consumer) = AudioSink::channel();
        let health = sink.health.clone();
        let samples: Vec<_> = (0..48000)
            .map(|n| (n as f32 * 440. * std::f32::consts::TAU / 48000.).sin() * 0.3)
            .collect();
        sink.push_interleaved(&samples, 1);
        Source {
            channel,
            name: "synthetic".into(),
            sample_rate: 48000,
            started: Instant::now(),
            health,
            consumer,
            stream: Box::new(Synthetic),
        }
    };
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let recording = store.create("Customer call").unwrap();
    let mut app = app(store.clone(), directory.path());
    let handle = RecordingHandle::with_sources(store, recording.id.clone(), move || {
        Ok((
            vec![
                synthetic(tico_recorder_native::audio::Channel::Microphone),
                synthetic(tico_recorder_native::audio::Channel::Meeting),
            ],
            String::new(),
        ))
    })
    .unwrap();
    app.set_active(recording.id.clone(), handle);
    // Record opens the recording in progress instead of starting another.
    assert_eq!(app.record(None).as_deref(), Some(recording.id.as_str()));
    settle(&mut app, |app| {
        app.status_view()["recording"]["sources"]
            .as_array()
            .is_some_and(|sources| sources.len() == 2)
    });
    let view = full_view(&mut app);
    assert_eq!(view["status"]["recording"]["id"], recording.id.as_str());
    assert_eq!(view["meeting"]["data"]["recording_here"], true);
    // While recording the transcript is the live one, with each channel's stream status.
    assert_eq!(view["transcript"]["data"]["live"], true);
    assert!(view["status"]["recording"]["stream"].is_array());
    assert_eq!(row(&view, "Customer call")["status"]["label"], "Recording");
    assert_eq!(row(&view, "Customer call")["can_send"], false);
    app.stop();
    settle(&mut app, |app| app.status_view()["recording"].is_null());
    assert!(full_view(&mut app)["status"]["recording"].is_null());
    let stored = app.store.get(&recording.id).unwrap();
    assert_ne!(stored.state, "recording");
    assert!(
        !stored.chunks.is_empty(),
        "the buffered audio was saved on stop"
    );
}

#[test]
fn send_while_recording_stops_waits_for_the_last_words_then_sends() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut saved = settings::read(directory.path()).unwrap();
    saved.destinations = vec![connection("first", "example.com")];
    settings::write(directory.path(), &saved).unwrap();
    let recording = store.create("Pressed Send").unwrap();
    let mut app = app(store.clone(), directory.path());
    let handle = tico_recorder_native::engine::RecordingHandle::with_sources(
        store.clone(),
        recording.id.clone(),
        || Ok((vec![], String::new())),
    )
    .unwrap();
    app.set_active(recording.id.clone(), handle);
    app.open(&recording.id).unwrap();
    assert!(full_view(&mut app)["meeting"]["data"]["send_blocked"].is_null());
    store
        .save_audio(
            &recording.id,
            tico_recorder_native::audio::Channel::Microphone,
            0,
            1000,
            &vec![0.2; 16000],
            16000,
        )
        .unwrap();
    app.send_meeting(&recording.id, "first");
    assert!(app.error.is_empty(), "{}", app.error);
    assert_eq!(app.status_view()["sending"]["id"], recording.id.as_str());
    // The send waits while the last chunk is transcribed, and says so on the button.
    settle(&mut app, |app| {
        app.status_view()["sending"]["label"] == "Transcribing…"
    });
    let claimed = store.claim_transcription().unwrap().unwrap();
    store
        .transcription_result(&claimed, "Ship it on Friday", "", json!({"fixture":true}))
        .unwrap();
    // Then it goes to Tico; this fixture has no sign-in, so that is where it stops.
    settle(&mut app, |app| !app.busy);
    assert_eq!(app.error, "Sign in to Tico in Settings");
    assert!(app.status_view()["sending"].is_null());
    assert!(
        app.status_view()["recording"].is_null(),
        "Send stopped the recording"
    );
}

#[test]
fn settings_save_over_connections_and_keep_keys_out_of_the_file() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut saved = settings::read(directory.path()).unwrap();
    saved.destinations = vec![
        connection("first", "example.com"),
        connection("second", "tico.team"),
    ];
    settings::write(directory.path(), &saved).unwrap();
    let mut app = app(store, directory.path());
    app.make_default("second");
    assert_eq!(
        settings::read(directory.path()).unwrap().destinations[0].id,
        "second"
    );
    // Connected and never chosen: speech goes through the default Tico, which hasn't said yet
    // what it can do.
    assert_eq!(
        app.status_view()["speech"],
        json!({"source":"tico","stream":false,"transcribe":false,"tico":"tico.team"})
    );
    app.save_settings(SettingsForm {
        microphone: "Studio mic".into(),
        meeting_audio: false,
        transcription_url: "https://openrouter.ai/api/v1".into(),
        transcription_model: "elevenlabs/scribe-v2".into(),
        transcription_language: String::new(),
        calendar: true,
        key: String::new(),
        streaming: Some(true),
        streaming_model: String::new(),
        xai_key: String::new(),
        speech_source: Some("own".into()),
    });
    settle(&mut app, |app| !app.busy);
    let written = settings::read(directory.path()).unwrap();
    assert_eq!(written.speech_source, "own");
    // Own keys without a saved key can't transcribe.
    assert_eq!(app.status_view()["speech"]["source"], "none");
    assert_eq!(
        full_view(&mut app)["settings"]["data"]["speech_source"],
        "own"
    );
    assert_eq!(written.microphone, "Studio mic");
    assert!(!written.meeting_audio);
    assert_eq!(written.transcription_language, "");
    assert_eq!(written.calendar_connection, "second");
    assert_eq!(written.destinations.len(), 2);
    assert_eq!(written.streaming, Some(true));
    assert_eq!(
        written.streaming_model,
        tico_recorder_native::stream::DEFAULT_MODEL
    );
    let file = std::fs::read_to_string(directory.path().join("settings.json")).unwrap();
    assert!(!file.contains("key"), "{file}");
    let view = full_view(&mut app);
    assert_eq!(
        view["settings"]["data"]["connections"][0]["name"],
        "tico.team"
    );
    assert_eq!(view["settings"]["data"]["connections"][0]["default"], true);
    // Turning the calendar on fetches it; this fixture has no sign-in token, so that fetch fails
    // visibly. Let it finish before the next check.
    settle(&mut app, |app| !app.error.is_empty());
    assert_eq!(app.error, "Sign in to Tico in Settings");
    app.dismiss_error();
    // A bad endpoint is refused and the saved settings stay as they were.
    app.save_settings(SettingsForm {
        transcription_url: "http://evil.example".into(),
        ..Default::default()
    });
    settle(&mut app, |app| !app.busy);
    assert_eq!(app.error, "Use HTTPS, or HTTP on localhost");
    assert_eq!(
        settings::read(directory.path()).unwrap().microphone,
        "Studio mic"
    );
}

#[test]
fn an_idle_window_is_told_nothing_and_reads_nothing_until_the_library_changes() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    store.create("Quiet").unwrap();
    let mut app = app(store.clone(), directory.path());
    app.tick();
    full_view(&mut app);
    assert!(!app.stale());
    let version = app.ui_version();
    for _ in 0..5 {
        app.tick();
    }
    assert_eq!(app.ui_version(), version, "an idle tick changes nothing");
    // A write elsewhere shows up on a later tick, and the window is told to redraw.
    store.create("Arrived").unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while !app.stale() && Instant::now() < deadline {
        app.tick();
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(app.stale());
    assert!(row(&full_view(&mut app), "Arrived").is_object());
    assert!(!app.stale());
}

#[test]
fn meeting_chat_shows_people_bots_and_private_questions_in_time_order() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    app.config.destinations = vec![connection("fixture", "Tico Team")];
    app.destination = "fixture".into();
    app.roster = vec![("bot:librarian".into(), "Librarian".into())];
    let id = app
        .records
        .iter()
        .find(|row| row.title.starts_with("Product"))
        .unwrap()
        .id
        .clone();
    app.open(&id).unwrap();
    // Not in Tico yet: the dock messages the meeting, and the first message would connect it.
    let chat = full_view(&mut app)["meeting"]["data"]["chat"].clone();
    assert_eq!(
        (
            chat["connected"].as_bool(),
            chat["can_message"].as_bool(),
            chat["private"].as_bool()
        ),
        (Some(false), Some(true), Some(false))
    );
    assert_eq!(chat["messages"], json!([]));

    let turn = |role: &str, text: &str| Turn {
        role: role.into(),
        text: text.into(),
        via: "text".into(),
        at: "2026-10-29T15:00:30+00:00".into(),
    };
    app.context.chat = vec![
        turn("you", "Who owns it?"),
        turn("assistant", "Arthur does."),
    ];
    app.context.live_id = "live-1".into();
    app.context.live_state = "live".into();
    app.live = json!({
        "humans": [{"actor": "human:2", "name": "Arthur Silva"}],
        "bots": [{"bot": "librarian"}, {"bot": "head-of-engineering"}],
        "chat": [
            {"id": "c1", "actor": "human:2", "role": "human", "text": "Hi all", "created": "2026-10-29T15:00:00.000000Z"},
            {"id": "c2", "actor": "bot:librarian", "role": "bot", "text": "Notes are linked.", "created": "2026-10-29T15:01:00.000000Z"},
            {"id": "c3", "actor": "human:1", "role": "human", "text": "Thanks", "created": "2026-10-29T15:00:10.000000Z"},
        ],
        "router": {"turns": [
            {"bot": "head-of-engineering", "status": "claimed"},
            {"bot": "librarian", "status": "replied"},
        ]},
    });
    let chat = full_view(&mut app)["meeting"]["data"]["chat"].clone();
    let rows: Vec<(String, bool, bool, bool)> = chat["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                row["name"].as_str().unwrap().to_owned(),
                row["bot"] == true,
                row["me"] == true,
                row["private"] == true,
            )
        })
        .collect();
    assert_eq!(
        rows,
        vec![
            ("Arthur Silva".into(), false, false, false),
            ("You".into(), false, true, false),
            ("You".into(), false, true, true),
            ("Tico".into(), true, false, true),
            ("Librarian".into(), true, false, false),
        ]
    );
    // A bot that claimed its turn is shown replying; one that already replied is not.
    assert_eq!(chat["replying"], json!(["Head of engineering"]));
    assert_eq!(chat["connected"], true);
}

#[test]
fn an_update_waits_for_every_send_from_its_first_step() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut app = app(store, directory.path());
    // An action item on its way to Tico, before any delivery row says "sending".
    app.mark_sending(Some("item-1"), None);
    assert_eq!(app.update_hold(), Some("While a meeting is being sent"));
    // A meeting send still waiting for its last words to be transcribed.
    app.mark_sending(None, Some("Transcribing…"));
    assert_eq!(app.update_hold(), Some("While a meeting is being sent"));
    app.mark_sending(None, None);
    assert_eq!(app.update_hold(), None);
}

#[test]
fn one_install_owns_the_reservation_and_nothing_starts_until_it_is_done() {
    use crate::update::{Build, Intent, Step, admit};
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut app = app(store.clone(), directory.path());
    let idle = Some(std::time::Duration::from_secs(60 * 60));
    let mut installing = false;
    // The background thread admits an idle install...
    let hold = app.update_hold();
    let background = admit(
        &mut app.updating,
        &mut installing,
        false,
        Intent::Background,
        Build::Downloaded,
        hold,
        idle,
    );
    assert_eq!(background, Step::Install);
    assert!(installing);
    // ...and a Restart click in the meantime neither installs too nor takes over the reservation.
    let hold = app.update_hold();
    let restart = admit(
        &mut app.updating,
        &mut installing,
        false,
        Intent::Restart,
        Build::Downloaded,
        hold,
        idle,
    );
    assert_eq!(restart, Step::Wait);
    assert!(app.updating);
    // Until the owner is done, no recording starts.
    assert_eq!(app.record(None), None);
    assert!(app.error.contains("updating"));
    assert!(store.list().unwrap().is_empty());
    // The owner finishes; the next Restart may install and restart.
    installing = false;
    app.updating = false;
    let hold = app.update_hold();
    let restart = admit(
        &mut app.updating,
        &mut installing,
        false,
        Intent::Restart,
        Build::Installed,
        hold,
        idle,
    );
    assert_eq!(restart, Step::InstallAndRestart);
    assert!(app.updating);
}

#[test]
fn quitting_joins_an_admitted_install_and_admits_no_new_one() {
    use crate::update::{Build, Intent, Step, admit, begin_quit};
    let idle = Some(std::time::Duration::from_secs(60 * 60));
    let (mut updating, mut installing, mut quitting) = (false, false, false);
    // The background thread is admitted but has not started writing when the app quits: the quit waits.
    assert_eq!(
        admit(
            &mut updating,
            &mut installing,
            quitting,
            Intent::Background,
            Build::Downloaded,
            None,
            idle
        ),
        Step::Install
    );
    assert!(begin_quit(&mut quitting, installing));
    // Once quitting, a background install that had not been admitted yet is not.
    let (mut other_updating, mut other_installing) = (false, false);
    assert_eq!(
        admit(
            &mut other_updating,
            &mut other_installing,
            quitting,
            Intent::Background,
            Build::Downloaded,
            None,
            idle
        ),
        Step::Wait
    );
    assert!(!other_updating && !other_installing);
    // The quit's own install is still admitted when nothing else holds the app.
    assert_eq!(
        admit(
            &mut other_updating,
            &mut other_installing,
            quitting,
            Intent::Quit,
            Build::Downloaded,
            None,
            None
        ),
        Step::Install
    );
    // A restart keeps `updating` but is no install in progress, so its own exit does not wait.
    let (mut restart_updating, mut restart_installing, mut restart_quitting) =
        (false, false, false);
    assert_eq!(
        admit(
            &mut restart_updating,
            &mut restart_installing,
            false,
            Intent::Restart,
            Build::Installed,
            None,
            None
        ),
        Step::InstallAndRestart
    );
    restart_installing = false; // apply clears it before restarting
    assert!(!begin_quit(&mut restart_quitting, restart_installing));
    assert!(restart_updating);
}

#[test]
fn an_update_waits_while_a_live_meeting_is_connected_and_is_free_when_idle() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut app = app(store, directory.path());
    assert_eq!(app.update_hold(), None);
    app.records.push(store::Summary {
        live_state: "live".into(),
        ..Default::default()
    });
    assert_eq!(app.update_hold(), Some("After the live meeting ends"));
    app.records[0].live_state = "ended".into();
    assert_eq!(app.update_hold(), None);
}

#[test]
fn the_open_meetings_bot_notices_show_with_the_bots_name() {
    use tico_recorder_native::notices::{self, Notice};
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut app = app(store, directory.path());
    assert_eq!(full_view(&mut app)["live"], json!({"notices": []}));
    notices::remember(
        "live-notices",
        12,
        vec![Notice {
            id: "waiting:t1".into(),
            kind: "waiting".into(),
            bot: "falcon".into(),
            turn_id: "t1".into(),
            reason: String::new(),
            detail: String::new(),
            at: 1_700_000_000_000,
        }],
    );
    app.context.live_id = "live-notices".into();
    app.live = json!({"id": "live-notices"});
    app.roster = vec![("bot:falcon".into(), "Falcon".into())];
    assert_eq!(
        full_view(&mut app)["live"]["notices"],
        json!([{"id":"waiting:t1","kind":"waiting","bot":"Falcon",
            "text":"Falcon is waiting for its computer","at":1_700_000_000_000_i64}])
    );
    // Another meeting open: not its notices.
    app.context.live_id = "live-other".into();
    assert_eq!(full_view(&mut app)["live"]["notices"], json!([]));
}

#[test]
fn open_in_tico_stays_on_the_shared_meetings_instance() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    app.config.destinations = vec![
        connection("first", "acme.example"),
        connection("second", "other.example"),
    ];
    let id = app.records[0].id.clone();
    app.open(&id).unwrap();
    app.busy = true; // No roster/network work: this replay is entirely synthetic.
    assert!(full_view(&mut app)["meeting"]["data"]["live_url"].is_null());
    app.context.live_destination = "first".into();
    app.context.live_id = "room &/?#".into();
    app.destination = "second".into();
    for state in ["live", "paused", "ended"] {
        app.context.live_state = state.into();
        let view = full_view(&mut app)["meeting"]["data"].clone();
        let link = reqwest::Url::parse(view["live_url"].as_str().unwrap()).unwrap();
        assert_eq!(link.host_str(), Some("runner.acme.example"));
        let route = link.fragment().unwrap();
        assert!(route.starts_with("/meetings?"));
        let query = reqwest::Url::parse(&format!("https://acme.example/{route}")).unwrap();
        assert_eq!(
            query.query_pairs().collect::<Vec<_>>(),
            vec![("live".into(), "room &/?#".into())]
        );
    }
    // Losing the connection must not send this meeting to the new default instance.
    app.config.destinations.remove(0);
    assert!(full_view(&mut app)["meeting"]["data"]["live_url"].is_null());
}
