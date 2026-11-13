//! Behaviour of the recorder's actions and what the window is told to draw. These replace the
//! egui harness tests: the same scenarios, checked on `Recorder` and its `view`.
use super::recorder::*;
use serde_json::{Value, json};
use std::time::{Duration, Instant};
use tico_recorder_native::{
    calls::Claim,
    settings::{self, AppLock, Destination},
    store::{self, Store},
    workspace::{Item, Turn},
};

#[test]
fn picker_search_runs_blocking_http_outside_the_tauri_runtime() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
    };
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let server = std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(8);
        let mut stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(Instant::now() < deadline, "Picker did not reach HTTP");
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(e) => panic!("{e}"),
            }
        };
        // An accepted stream keeps the listener's non-blocking mode on macOS: reads would not wait.
        stream.set_nonblocking(false).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let mut headers = vec![];
        while !headers.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            stream.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
        }
        let headers = String::from_utf8(headers).unwrap();
        assert!(headers.starts_with("GET /api/v2/tasks?q=%2342&limit=30&brief=true&offset=30 "));
        let body = r##"{"tasks":[{"id":"task","number":42,"title":"Review pricing","status":"open"}],"next_offset":60}"##;
        write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
    });
    let reply = tauri::async_runtime::block_on(super::picker_search(
        move || {
            tico_recorder_native::network::Tico::new(
                Destination {
                    id: "synthetic".into(),
                    name: "Acme".into(),
                    base_url: url,
                    actor: "human:test".into(),
                },
                "synthetic-token".into(),
            )
        },
        |tico| tico.search_tasks("42", "bug", 30),
    ))
    .unwrap();
    server.join().unwrap();
    assert_eq!(reply["tasks"][0]["number"], 42);
    assert_eq!(reply["next_offset"], 60);
}

fn app(store: Store, directory: &std::path::Path) -> Recorder {
    // No-sign-in fixtures need empty credentials without depending on a desktop secret service.
    static KEYRING: std::sync::Once = std::sync::Once::new();
    KEYRING.call_once(|| {
        keyring::set_default_credential_builder(keyring::mock::default_credential_builder());
    });
    let lock = AppLock::acquire(directory).unwrap();
    let mut app = Recorder::new(store, Some(lock), true).unwrap();
    // No call app on the mic unless a test says so (the real read is this Mac's).
    app.apps_on_mic = || Ok(vec![]);
    app
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
fn an_open_link_chooses_its_tico_and_starts_nothing() {
    assert_eq!(
        link_address(
            "tico-recorder://open?server=https://tico.example.com/",
            "open"
        )
        .as_deref(),
        Some("https://tico.example.com")
    );
    assert_eq!(
        link_address(
            "tico-recorder://open?server=https://tico.example.com",
            "connect"
        ),
        None
    );
    let directory = tempfile::tempdir().unwrap();
    let mut app = app(Store::open(directory.path()).unwrap(), directory.path());
    app.config.destinations = vec![
        connection("first", "first.example.com"),
        connection("second", "tico.example.com"),
    ];
    app.destination = "first".into();
    app.link("tico-recorder://open?server=https://tico.example.com/");
    assert_eq!(
        app.destination, "second",
        "the runner sign-in is that Tico's"
    );
    assert!(app.connect.is_none() && app.error.is_empty());
    assert_eq!(full_view(&mut app)["status"]["recording"], Value::Null);
    // A Tico the Recorder is not signed in to asks first, as a connect link does.
    app.link("tico-recorder://open?server=https://new.example.com");
    assert_eq!(app.connect.as_deref(), Some("https://new.example.com"));
    assert_eq!(app.destination, "second");
    app.dismiss_connect();
    app.link("tico-recorder://open?server=http://evil.example");
    assert!(app.connect.is_none());
    assert_eq!(app.error, "That link is not a Tico Recorder connect link");
}

#[test]
fn the_view_lists_the_desktop_apps_orgs_and_which_are_signed_in() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = app(Store::open(directory.path()).unwrap(), directory.path());
    assert_eq!(
        full_view(&mut app)["desktop_orgs"],
        json!([]),
        "the demo library reads no orgs.json"
    );
    let file = directory.path().join("orgs.json");
    std::fs::write(
        &file,
        r#"{"version":1,"active":"o_bbbbbbbb","orgs":[
            {"id":"o_aaaaaaaa","url":"https://tico.example.com/","name":"Acme","email":"ana@acme.example"},
            {"id":"o_bbbbbbbb","url":"https://tidy.example.com/","name":"TIDY","email":"ana@tidy.example"}]}"#,
    )
    .unwrap();
    app.desktop = tico_recorder_native::desktop::Watch::new(Some(file));
    app.config.destinations = vec![connection("acme", "tico.example.com")];
    assert_eq!(
        full_view(&mut app)["desktop_orgs"],
        json!([
            {"name":"Acme","url":"https://tico.example.com","email":"ana@acme.example","active":false,"connected":true},
            {"name":"TIDY","url":"https://tidy.example.com","email":"ana@tidy.example","active":true,"connected":false}
        ])
    );
    // Allow on a row signs in to that org only: one already signed in, or not in the desktop app, starts nothing.
    app.sign_in_desktop(Some("https://tico.example.com"));
    app.sign_in_desktop(Some("https://other.example.com"));
    assert_eq!(full_view(&mut app)["status"]["signing_in"], json!(false));
    let orgs = tico_recorder_native::desktop::parse(
        r#"{"version":1,"active":"o_bbbbbbbb","orgs":[{"id":"o_aaaaaaaa","url":"https://a.example.com/"},
            {"id":"o_bbbbbbbb","url":"https://b.example.com/"}]}"#,
    );
    assert_eq!(
        suggested_address(Some(&orgs), || unreachable!()),
        "b.example.com"
    );
    assert_eq!(
        suggested_address(Some(&[]), || unreachable!()),
        "",
        "orgs.json wins over server.txt"
    );
    assert_eq!(
        suggested_address(None, || "https://old.example.com/\n".into()),
        "old.example.com"
    );
    assert_eq!(signed_in_message(&["A".into()]), "Signed in to A");
    assert_eq!(
        signed_in_message(&["A".into(), "B".into()]),
        "Signed in to A and B"
    );
    assert_eq!(
        signed_in_message(&["A".into(), "B".into(), "C".into()]),
        "Signed in to A, B and C"
    );
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
    assert_eq!(meeting["sync_error"], error);
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
            live_input: None,
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
fn delete_while_recording_here_stops_capture_then_deletes() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let recording = store.create("Not wanted").unwrap();
    let mut app = app(store.clone(), directory.path());
    let handle = tico_recorder_native::engine::RecordingHandle::with_sources(
        store.clone(),
        recording.id.clone(),
        || Ok((vec![], String::new())),
    )
    .unwrap();
    app.set_active(recording.id.clone(), handle);
    app.open(&recording.id).unwrap();
    // Finish → Delete on a recording that was never shared.
    app.delete(&recording.id);
    settle(&mut app, |app| !app.busy);
    assert!(!app.busy, "delete did not finish");
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(app.store.get(&recording.id).is_err());
    assert!(full_view(&mut app)["status"]["recording"].is_null());
}

fn delete_during_capture_stop(fail_stop: bool, shutdown: bool) {
    use std::sync::mpsc;
    use tico_recorder_native::capture::{AudioSink, CaptureStream, Source};

    struct GatedStop {
        entered: mpsc::Sender<()>,
        release: Option<mpsc::Receiver<()>>,
        fail: bool,
    }
    impl CaptureStream for GatedStop {
        fn stop(&mut self) -> anyhow::Result<()> {
            if let Some(release) = self.release.take() {
                self.entered.send(())?;
                release.recv_timeout(Duration::from_secs(2))?;
            }
            anyhow::ensure!(!self.fail, "Synthetic capture stop failure");
            Ok(())
        }
    }

    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let recording = store.create("Delete buffered audio").unwrap();
    let (entered, stopping) = mpsc::channel();
    let (release, gate) = mpsc::channel();
    let handle = tico_recorder_native::engine::RecordingHandle::with_sources(
        store.clone(),
        recording.id.clone(),
        move || {
            let (mut sink, consumer) = AudioSink::channel();
            let health = sink.health.clone();
            sink.push_interleaved(&vec![0.2; 16000], 1);
            Ok((
                vec![Source {
                    channel: tico_recorder_native::audio::Channel::Microphone,
                    name: "synthetic".into(),
                    sample_rate: 16000,
                    started: Instant::now(),
                    health,
                    consumer,
                    live_input: None,
                    stream: Box::new(GatedStop {
                        entered,
                        release: Some(gate),
                        fail: fail_stop,
                    }),
                }],
                String::new(),
            ))
        },
    )
    .unwrap();
    let mut app = app(store, directory.path());
    app.set_active(recording.id.clone(), handle);
    app.open(&recording.id).unwrap();
    settle(&mut app, |app| {
        app.status_view()["recording"]["sources"]
            .as_array()
            .is_some_and(|sources| sources.len() == 1)
    });
    app.delete(&recording.id);
    stopping.recv_timeout(Duration::from_secs(2)).unwrap();
    assert!(app.busy);
    assert!(app.update_hold().is_some());
    assert!(app.store.get(&recording.id).is_ok());
    release.send(()).unwrap();
    if shutdown {
        app.shutdown();
        assert!(app.store.get(&recording.id).is_err());
    }
    settle(&mut app, |app| !app.busy);
    assert!(!app.busy, "delete did not finish");
    assert!(app.update_hold().is_none());
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(app.store.get(&recording.id).is_err());
    assert!(!directory.path().join("audio").join(&recording.id).exists());
    assert!(
        full_view(&mut app)["library"]["data"]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["id"] != recording.id)
    );
}

#[test]
fn delete_waits_for_the_last_audio_write_and_removes_its_files() {
    delete_during_capture_stop(false, false);
}

#[test]
fn delete_still_removes_audio_after_capture_stop_fails() {
    delete_during_capture_stop(true, false);
}

#[test]
fn shutdown_waits_for_delete_to_remove_the_last_audio() {
    delete_during_capture_stop(false, true);
}

#[test]
fn delete_does_not_take_capture_when_another_job_holds_the_controls() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let recording = store.create("Busy recording").unwrap();
    let handle = tico_recorder_native::engine::RecordingHandle::with_sources(
        store.clone(),
        recording.id.clone(),
        || Ok((vec![], String::new())),
    )
    .unwrap();
    let mut app = app(store, directory.path());
    app.set_active(recording.id.clone(), handle);
    app.open(&recording.id).unwrap();
    app.busy = true;
    app.delete(&recording.id);
    assert_eq!(app.status_view()["recording"]["id"], recording.id);
    assert_eq!(app.meeting.as_deref(), Some(recording.id.as_str()));
    assert!(app.store.get(&recording.id).is_ok());
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
fn settings_reload_controller_observes_writes_without_a_settings_job_reply() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut app = app(store, directory.path());
    // Exercise the normal controller refresh with the fixture's background workers disabled.
    app.demo = false;
    let mut config = settings::read(directory.path()).unwrap();
    config.microphone = "Studio mic".into();
    settings::write(directory.path(), &config).unwrap();
    app.tick();
    assert_eq!(app.config.microphone, "Studio mic");
    config.microphone.clear();
    settings::write(directory.path(), &config).unwrap();
    app.tick();
    assert!(app.config.microphone.is_empty());
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
            {"id": "c1", "actor": "human:2", "role": "human", "text": "Hi all", "created_at": "2026-10-29T15:00:00.000000Z"},
            {"id": "c2", "actor": "bot:librarian", "role": "bot", "text": "Notes are linked.", "created_at": "2026-10-29T15:01:00.000000Z"},
            {"id": "c3", "actor": "human:1", "role": "human", "text": "Thanks", "created_at": "2026-10-29T15:00:10.000000Z"},
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
    // A paused live meeting is still shared: no install, no banner.
    app.records[0].live_state = "paused".into();
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
fn send_requires_review_of_paused_quotes_and_discard_keeps_the_local_recording() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    seed_demo(&store).unwrap();
    let mut app = app(store, directory.path());
    let id = app
        .records
        .iter()
        .find(|r| r.chunks > r.silent && r.deliveries.is_empty())
        .unwrap()
        .id
        .clone();
    app.config.destinations = vec![connection("first", "acme.example")];
    app.store
        .update_context(&id, |c| {
            c.paused_windows = vec![(5000, Some(10000))];
            c.items = vec![Item {
                quote: "Paused quote".into(),
                at_ms: 6000,
                ..Default::default()
            }];
            Ok(())
        })
        .unwrap();
    app.send_meeting(&id, "first");
    assert!(app.error.contains("may quote paused speech"));
    assert!(!app.busy);
    assert!(app.store.get(&id).unwrap().deliveries.is_empty());
    // Confirmation reaches the bypass before the busy guard, without starting any network job.
    app.busy = true;
    app.error.clear();
    app.send_meeting_confirmed(&id, "first", true);
    assert!(app.error.is_empty());
    app.busy = false;
    app.store
        .update_context(&id, |c| {
            c.items[0].quote = "Normalized or reworded quote".into();
            c.items[0].at_ms = 1000;
            Ok(())
        })
        .unwrap();
    app.send_meeting(&id, "first");
    assert!(app.error.contains("may quote paused speech"));
    app.store
        .update_context(&id, |c| {
            c.live_state = "paused".into();
            Ok(())
        })
        .unwrap();
    app.send_meeting_confirmed(&id, "first", true);
    assert!(app.error.contains("Resume sharing"));
    assert!(!app.busy);
    app.store
        .update_context(&id, |c| {
            c.live_discarded = true;
            Ok(())
        })
        .unwrap();
    app.send_meeting_confirmed(&id, "first", true);
    assert!(app.error.contains("discarded"));
    assert!(!app.busy);
    assert!(!app.store.get(&id).unwrap().chunks.is_empty());
    assert!(
        full_view(&mut app)["library"]["data"]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["id"] == id && r["status"]["label"] == "Ending in Tico")
    );
}

#[test]
fn individual_item_send_uses_durable_pause_discard_and_quote_guards() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Synthetic item guards").unwrap();
    store
        .update_context(&record.id, |c| {
            c.paused_windows = vec![(5000, Some(10000))];
            c.items = vec![Item {
                id: "item".into(),
                title: "Follow up".into(),
                quote: "Reworded paused quote".into(),
                owner: "bot:fixture".into(),
                ..Default::default()
            }];
            Ok(())
        })
        .unwrap();
    let mut app = app(store.clone(), dir.path());
    app.open(&record.id).unwrap();
    app.send_item("item", "bot:fixture");
    assert!(
        app.item_error
            .as_ref()
            .unwrap()
            .1
            .contains("may quote paused speech")
    );
    assert!(app.sending.is_none());
    // The displayed context stays stale: the durable guard must still refuse a direct command.
    for discarded in [false, true] {
        store
            .update_context(&record.id, |c| {
                c.live_state = "paused".into();
                c.live_discarded = discarded;
                Ok(())
            })
            .unwrap();
        app.send_item_confirmed("item", "bot:fixture", true);
        assert!(app.item_error.as_ref().unwrap().1.contains(if discarded {
            "discarded"
        } else {
            "Resume"
        }));
        assert!(app.sending.is_none());
        app.context = store.context(&record.id).unwrap();
        let view = full_view(&mut app)["meeting"]["data"]["actions"].clone();
        assert_eq!(view["items"][0]["can_send"], false);
        assert!(view["items"][0]["send_blocked"].as_str().is_some());
    }
}

#[test]
fn a_meeting_shared_live_shows_the_lines_tico_holds_with_controls_only_where_an_edit_can_reach_it()
{
    use tico_recorder_native::live;
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Shared edit view").unwrap();
    let mut context = store.context(&record.id).unwrap();
    context.live_id = "live-fixture".into();
    context.live_destination = "synthetic".into();
    context.live_state = "live".into();
    store.save_context(&record.id, &context).unwrap();
    // Three lines in Tico's order: two accepted, one still on its way.
    {
        let db = store.db().unwrap();
        for (seq, speaker, start, end, text, acknowledged) in [
            (1, "Me", 1000, 1550, "Hello there", 1),
            (2, "S1", 1200, 1750, "hi all", 1),
            (3, "S1", 2000, 2500, "pending words", 0),
        ] {
            db.execute(
                "INSERT INTO live_outbox(recording_id,destination_id,seq,chunk_id,payload_json,acknowledged) VALUES(?,?,?,?,?,?)",
                rusqlite::params![
                    record.id,
                    "synthetic",
                    seq,
                    format!("stream:{seq}"),
                    json!({"seq":seq,"speaker":speaker,"start_ms":start,"end_ms":end,"text":text}).to_string(),
                    acknowledged
                ],
            )
            .unwrap();
        }
    }
    live::edit_shared(&store, &record.id, 1, Some("Hi there")).unwrap();
    live::edit_shared(&store, &record.id, 2, None).unwrap();
    let mut app = app(store, directory.path());
    app.open(&record.id).unwrap();
    let blocks = full_view(&mut app)["transcript"]["data"]["blocks"].clone();
    assert_eq!(
        blocks,
        json!([
            {"speaker":"Me","me":true,"start_ms":1000,"paragraphs":["Hi there"],"partial":false,
             "streamed":false,"seq":1,"edited":true,"removed":false,"editable":true},
            {"speaker":"S1","me":false,"start_ms":1200,"paragraphs":["[removed]"],"partial":false,
             "streamed":false,"seq":2,"edited":true,"removed":true,"editable":true},
            {"speaker":"S1","me":false,"start_ms":2000,"paragraphs":["pending words"],"partial":false,
             "streamed":false,"seq":3,"edited":false,"removed":false,"editable":false},
        ]),
        "{blocks}"
    );
}

#[test]
fn an_edit_or_removal_names_the_open_meeting_and_leaves_another_alone() {
    use tico_recorder_native::live;
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let mut ids = vec![];
    for title in ["First call", "Second call"] {
        let record = store.create(title).unwrap();
        let mut context = store.context(&record.id).unwrap();
        context.live_id = format!("live-{}", record.id);
        context.live_destination = "synthetic".into();
        context.live_state = "live".into();
        store.save_context(&record.id, &context).unwrap();
        store
            .db()
            .unwrap()
            .execute(
                "INSERT INTO live_outbox(recording_id,destination_id,seq,chunk_id,payload_json,acknowledged) VALUES(?,?,1,'stream:1',?,1)",
                rusqlite::params![
                    record.id,
                    "synthetic",
                    json!({"seq":1,"speaker":"Me","start_ms":1000,"end_ms":1550,"text":"Hello there"}).to_string()
                ],
            )
            .unwrap();
        ids.push(record.id);
    }
    let (first, second) = (ids[0].clone(), ids[1].clone());
    let mut app = app(store, directory.path());
    app.open(&first).unwrap();
    app.edit_shared_line(&second, 1, Some("Changed elsewhere"));
    assert_eq!(app.error, "That meeting is no longer open");
    app.dismiss_error();
    app.edit_shared_line(&second, 1, None);
    assert_eq!(app.error, "That meeting is no longer open");
    app.dismiss_error();
    let other = live::shared_lines(&app.store, &second, "synthetic").unwrap();
    assert_eq!(
        (other[0].text.as_str(), other[0].edited),
        ("Hello there", false)
    );
    // The open meeting is the one that changes.
    app.edit_shared_line(&first, 1, Some("Changed here"));
    assert!(app.error.is_empty(), "{}", app.error);
    let open = live::shared_lines(&app.store, &first, "synthetic").unwrap();
    assert_eq!(open[0].text, "Changed here");
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

#[test]
fn a_title_without_a_quote_requires_fresh_review_after_each_edit() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Synthetic title review").unwrap();
    store
        .update_context(&record.id, |c| {
            c.paused_windows = vec![(5000, Some(10000))];
            c.items = vec![Item {
                id: "title-only".into(),
                title: "Secret launch date".into(),
                owner: "bot:fixture".into(),
                ..Default::default()
            }];
            Ok(())
        })
        .unwrap();
    let mut app = app(store.clone(), dir.path());
    app.open(&record.id).unwrap();
    for title in ["Secret launch date", "Changed secret launch date"] {
        store
            .update_context(&record.id, |c| {
                c.items[0].title = title.into();
                Ok(())
            })
            .unwrap();
        app.send_item("title-only", "bot:fixture");
        assert!(
            app.item_error
                .as_ref()
                .unwrap()
                .1
                .contains("Review its title and quote")
        );
        assert!(app.sending.is_none());
        assert!(tico_recorder_native::live::paused_review_required(
            &store.context(&record.id).unwrap()
        ));
    }
    app.context = store.context(&record.id).unwrap();
    assert_eq!(
        full_view(&mut app)["meeting"]["data"]["actions"]["items"][0]["paused_warning"],
        true
    );
}

#[test]
fn screen_controls_honor_local_pause_and_pending_discard() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Synthetic screen controls").unwrap();
    let mut app = app(store.clone(), directory.path());
    app.open(&record.id).unwrap();
    assert_eq!(
        full_view(&mut app)["meeting"]["data"]["can_share_screen"],
        true
    );
    for discarded in [false, true] {
        store
            .update_context(&record.id, |context| {
                context.live_id = "synthetic-live".into();
                context.live_state = "live".into();
                context.locally_paused = !discarded;
                context.live_discarded = discarded;
                Ok(())
            })
            .unwrap();
        app.context = store.context(&record.id).unwrap();
        app.bump();
        assert_eq!(
            full_view(&mut app)["meeting"]["data"]["can_share_screen"],
            false
        );
        assert!(super::screen::require_live(&app.context).is_err());
    }
}

// ---- Calls: `tico-recorder://record` from a Google Meet started in a Tico thread ----

const CALL: &str = "6f1c1a52-6a4e-4c39-9d43-6a1f0e3c2b10";

/// A local Tico for the record-link tests: answers each request with `answer(method, path)` and keeps what was asked.
struct FakeTico {
    url: String,
    seen: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    /// Each request with its body: "METHOD path body".
    bodies: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
}
impl FakeTico {
    fn start(answer: impl Fn(&str, &str) -> (u16, Value) + Send + 'static) -> Self {
        use std::io::{Read, Write};
        use std::sync::{Arc, Mutex, atomic::AtomicBool, atomic::Ordering};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let seen = Arc::new(Mutex::new(vec![]));
        let bodies = Arc::new(Mutex::new(vec![]));
        let stop = Arc::new(AtomicBool::new(false));
        let (log, stopped, sent) = (seen.clone(), stop.clone(), bodies.clone());
        std::thread::spawn(move || {
            while !stopped.load(Ordering::Relaxed) {
                let mut stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(_) => {
                        std::thread::sleep(Duration::from_millis(5));
                        continue;
                    }
                };
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut head = vec![];
                while !head.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    if stream.read_exact(&mut byte).is_err() {
                        break;
                    }
                    head.push(byte[0]);
                }
                let head = String::from_utf8_lossy(&head).to_string();
                let mut words = head.split_whitespace();
                let (method, path) = (
                    words.next().unwrap_or_default().to_owned(),
                    words.next().unwrap_or_default().to_owned(),
                );
                let length = head
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(|n| n.trim().parse::<usize>().unwrap_or(0))
                    })
                    .unwrap_or(0);
                let mut body = vec![0; length];
                let _ = stream.read_exact(&mut body);
                sent.lock().unwrap().push(format!(
                    "{method} {path} {}",
                    String::from_utf8_lossy(&body)
                ));
                log.lock().unwrap().push(format!("{method} {path}"));
                let (status, reply) = answer(&method, &path);
                let reply = reply.to_string();
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                    reply.len()
                );
            }
        });
        Self {
            url,
            seen,
            bodies,
            stop,
        }
    }
    fn seen(&self) -> Vec<String> {
        self.seen.lock().unwrap().clone()
    }
    fn asked(&self, request: &str) -> usize {
        self.seen().iter().filter(|seen| *seen == request).count()
    }
}
impl Drop for FakeTico {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}
fn local_tico(
    config: &settings::Settings,
    id: &str,
) -> anyhow::Result<tico_recorder_native::network::Tico> {
    let destination = config
        .destinations
        .iter()
        .find(|row| row.id == id)
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("Choose a Tico connection"))?;
    tico_recorder_native::network::Tico::new(destination, "synthetic-token".into())
}
fn silent_capture(
    store: &Store,
    id: &str,
    _: Option<String>,
    _: bool,
) -> anyhow::Result<tico_recorder_native::engine::RecordingHandle> {
    use tico_recorder_native::capture::{AudioSink, CaptureStream, Source};
    struct Quiet;
    impl CaptureStream for Quiet {
        fn stop(&mut self) -> anyhow::Result<()> {
            Ok(())
        }
    }
    tico_recorder_native::engine::RecordingHandle::with_sources(store.clone(), id.into(), || {
        let (sink, consumer) = AudioSink::channel();
        Ok((
            vec![Source {
                channel: tico_recorder_native::audio::Channel::Microphone,
                name: "silent".into(),
                sample_rate: 16000,
                started: Instant::now(),
                health: sink.health.clone(),
                consumer,
                stream: Box::new(Quiet),
                live_input: None,
            }],
            String::new(),
        ))
    })
}
/// A Recorder signed in to `tico` as `acme`, recording silently.
fn call_app(directory: &std::path::Path, tico: &FakeTico) -> Recorder {
    let mut app = app(Store::open(directory).unwrap(), directory);
    app.tico_for = local_tico;
    app.start_capture = silent_capture;
    app.config.destinations = vec![Destination {
        id: "acme".into(),
        name: "Acme".into(),
        base_url: tico.url.clone(),
        actor: "human:1".into(),
    }];
    app
}
fn call_link(tico: &FakeTico) -> String {
    format!("tico-recorder://record?server={}&call={CALL}", tico.url)
}
fn claim_path() -> String {
    format!("/api/v2/calls/{CALL}/recorder")
}
/// The source's claim answer, the call's live meeting `live-1` as `state`, and `{}` for the rest.
fn source_answer(method: &str, path: &str, state: &str) -> (u16, Value) {
    if path == claim_path() && method == "POST" {
        (
            200,
            json!({"source":true,"meeting_id":"live-1","title":"Pricing call","source_actor":"human:1"}),
        )
    } else if path == "/api/v2/live-meetings/live-1" {
        (
            200,
            json!({"id":"live-1","state":state,"visibility":"private","imported_meeting_id":null}),
        )
    } else {
        (200, json!({}))
    }
}

#[test]
fn a_call_link_signs_in_to_its_org_first_and_records_nothing_when_another_recorder_is_the_source() {
    let tico = FakeTico::start(|method, path| {
        if path == claim_path() && method == "POST" {
            (
                200,
                json!({"source":false,"meeting_id":"live-1","title":"Pricing call","source_actor":"human:2"}),
            )
        } else {
            (200, json!({}))
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    let signed_in = app.config.destinations.clone();
    app.config.destinations.clear();
    app.link(&call_link(&tico));
    // Not signed in to that org: its sign-in is asked for first, and nothing is claimed yet.
    assert_eq!(app.connect.as_deref(), Some(tico.url.as_str()));
    assert!(tico.seen().is_empty());
    // Once signed in, the call goes on.
    app.config.destinations = signed_in;
    app.connect = None;
    app.continue_record();
    settle(&mut app, |_| {
        tico.asked(&format!("POST {}", claim_path())) == 1
    });
    settle(&mut app, |app| app.destination == "acme");
    assert_eq!(tico.asked(&format!("POST {}", claim_path())), 1);
    assert_eq!(app.destination, "acme");
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(full_view(&mut app)["status"]["recording"].is_null());
    assert!(app.store.list().unwrap().is_empty(), "nothing recorded");
    // A dismissed sign-in drops the waiting call.
    app.config.destinations.clear();
    app.link(&call_link(&tico));
    app.dismiss_connect();
    app.config.destinations = vec![connection("acme", "x")];
    app.continue_record();
    assert_eq!(tico.asked(&format!("POST {}", claim_path())), 1);
}

#[test]
fn an_older_tico_without_calls_only_shows_its_org() {
    // FastAPI's own 404, with no Tico error code.
    let tico = FakeTico::start(|_, _| (404, json!({"detail":"Not Found"})));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.config
        .destinations
        .push(connection("other", "other.example.com"));
    app.destination = "other".into();
    app.link(&call_link(&tico));
    settle(&mut app, |app| app.destination == "acme");
    assert_eq!(app.destination, "acme");
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(app.store.list().unwrap().is_empty());
}

#[test]
fn the_source_records_into_the_calls_live_meeting_and_stops_by_itself_when_the_call_ends() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "ended"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    // The window stays open on the call's recording: the Recorder still reads the call's meeting itself.
    app.accept_claim(
        "acme",
        CALL,
        Claim::Source {
            meeting_id: "live-1".into(),
            title: "Pricing call".into(),
        },
    );
    let id = app.meeting.clone().expect("the call's recording is open");
    let context = app.store.context(&id).unwrap();
    assert_eq!(
        (
            context.live_destination.as_str(),
            context.live_id.as_str(),
            context.call_id.as_str()
        ),
        ("acme", "live-1", CALL)
    );
    assert_eq!(app.store.get(&id).unwrap().title, "Pricing call");
    assert_eq!(full_view(&mut app)["meeting"]["data"]["call"], true);
    // Tico says the call's meeting ended: the recording stops by itself and is kept as in Tico.
    settle(&mut app, |app| app.status_view()["recording"].is_null());
    assert!(
        app.status_view()["recording"].is_null(),
        "stopped by itself"
    );
    assert!(tico.asked("GET /api/v2/live-meetings/live-1") >= 1);
    let record = app.store.get(&id).unwrap();
    assert!(
        sent_to(&record.deliveries, "acme"),
        "{:?}",
        record.deliveries
    );
    let view = full_view(&mut app);
    let meeting = &view["meeting"]["data"];
    assert_eq!(meeting["sent"], true);
    assert_eq!(meeting["can_finish"], false);
    assert_eq!(row(&view, "Pricing call")["can_send"], false);
    // It is never filed as a meeting of its own, and no live meeting was made for it.
    app.save(&id, "acme", "team", "Pricing call", false);
    app.send_meeting(&id, "acme");
    settle(&mut app, |app| !app.busy);
    let seen = tico.seen();
    assert!(
        !seen
            .iter()
            .any(|row| row.starts_with("POST /api/v2/live-meetings ")
                || row == "POST /api/v2/live-meetings"
                || row.contains("/api/v2/meetings/import")
                || row.contains("/finalize")
                || row.contains("/control")),
        "{seen:?}"
    );
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 0);
}

#[test]
fn stopping_a_call_by_hand_gives_the_source_back_and_skips_finish() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.link(&call_link(&tico));
    settle(&mut app, |app| !app.status_view()["recording"].is_null());
    let id = app.status_view()["recording"]["id"]
        .as_str()
        .expect("the source records")
        .to_owned();
    // A second link for the call being recorded changes nothing.
    app.link(&call_link(&tico));
    settle(&mut app, |_| false);
    assert_eq!(tico.asked(&format!("POST {}", claim_path())), 1);
    assert_eq!(app.store.list().unwrap().len(), 1);
    assert_eq!(app.status_view()["recording"]["id"], id.as_str());
    app.stop_by_hand();
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 1);
    settle(&mut app, |app| app.status_view()["recording"].is_null());
    assert!(app.status_view()["recording"].is_null());
    let record = app.store.get(&id).unwrap();
    assert!(sent_to(&record.deliveries, "acme"));
    // Nothing more goes to the call's meeting from here.
    assert_eq!(app.store.context(&id).unwrap().live_state, "ended");
    let view = full_view(&mut app);
    assert_eq!(view["meeting"]["data"]["call"], true);
    assert_eq!(view["meeting"]["data"]["can_finish"], false);
    assert_eq!(row(&view, "Pricing call")["can_send"], false);
    assert!(
        !tico
            .seen()
            .iter()
            .any(|row| row == "POST /api/v2/live-meetings"),
        "no live meeting is made for a call"
    );
}

/// The apps on the mic, as `call_apps_read` takes them.
fn on_mic(apps: &[&str]) -> Result<Vec<String>, String> {
    Ok(apps.iter().map(|app| app.to_string()).collect())
}

#[test]
fn a_call_recording_stops_by_itself_once_call_apps_leave_the_mic_and_gives_the_source_back() {
    static READS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.apps_on_mic = || {
        READS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        Ok(vec!["com.google.Chrome.helper".into()])
    };
    app.link(&call_link(&tico));
    settle(&mut app, |app| !app.status_view()["recording"].is_null());
    let id = recording(&app).expect("the source records");
    // While recording, the apps on the mic are read in the background (macOS).
    settle(&mut app, |_| {
        READS.load(std::sync::atomic::Ordering::Relaxed) > 0
    });
    if cfg!(target_os = "macos") {
        assert!(READS.load(std::sync::atomic::Ordering::Relaxed) > 0);
    }
    let start = Instant::now();
    let at = |seconds| start + Duration::from_secs(seconds);
    app.call_apps_read(&id, on_mic(&["com.google.Chrome.helper"]), at(0));
    // Chrome leaves the mic; Willow (dictation) stays on it and does not count.
    app.call_apps_read(&id, on_mic(&["com.seewillow.WillowMac"]), at(2));
    app.call_apps_read(&id, on_mic(&["com.seewillow.WillowMac"]), at(16));
    assert_eq!(
        recording(&app).as_deref(),
        Some(id.as_str()),
        "14 s is not yet"
    );
    app.call_apps_read(&id, on_mic(&["com.seewillow.WillowMac"]), at(17));
    assert_eq!(app.status_view()["message"], "Call ended");
    // Stopped as by Stop: the call's source goes back and the recording is kept as in Tico.
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 1);
    settle(&mut app, |app| app.status_view()["recording"].is_null());
    assert!(app.status_view()["recording"].is_null());
    assert!(sent_to(&app.store.get(&id).unwrap().deliveries, "acme"));
}

#[test]
fn a_recording_without_a_call_app_on_the_mic_never_stops_by_itself() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = join_app(directory.path());
    let id = app.record(None).expect("recording");
    let start = Instant::now();
    let at = |seconds| start + Duration::from_secs(seconds);
    // In person, or a voice memo: only dictation on the mic, or nothing.
    for second in (0..120).step_by(2) {
        let apps: &[&str] = if second % 4 == 0 {
            &["com.seewillow.WillowMac"]
        } else {
            &[]
        };
        app.call_apps_read(&id, on_mic(apps), at(second));
    }
    assert_eq!(recording(&app).as_deref(), Some(id.as_str()));
    // A read that fails turns it off for this recording: it keeps recording whatever comes after.
    app.call_apps_read(&id, Err("Core Audio -1".into()), at(120));
    app.call_apps_read(&id, on_mic(&["us.zoom.xos"]), at(122));
    app.call_apps_read(&id, on_mic(&[]), at(124));
    app.call_apps_read(&id, on_mic(&[]), at(160));
    assert_eq!(recording(&app).as_deref(), Some(id.as_str()));
    app.stop_by_hand();
    settle(&mut app, |app| app.status_view()["recording"].is_null());
}

#[test]
fn a_call_whose_meeting_already_has_transcript_is_given_back_and_not_recorded() {
    // An earlier source recorded and gave the call back: its lines hold sequences a new recording would reuse.
    let tico = FakeTico::start(|method, path| {
        if path == "/api/v2/live-meetings/live-1" {
            (200, json!({"id":"live-1","state":"live","seq":3}))
        } else {
            source_answer(method, path, "live")
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.link(&call_link(&tico));
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    settle(&mut app, |app| app.destination == "acme");
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 1);
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(app.status_view()["recording"].is_null());
    assert!(app.store.list().unwrap().is_empty(), "nothing recorded");
}

#[test]
fn every_release_reaches_tico_and_a_refused_one_is_tried_again() {
    static REFUSED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    let tico = FakeTico::start(|method, path| {
        // The first release of the second call fails once.
        if method == "DELETE"
            && path.contains("aaaaaaaa")
            && !REFUSED.swap(true, std::sync::atomic::Ordering::SeqCst)
        {
            return (503, json!({"error":{"code":"unavailable"}}));
        }
        (200, json!({}))
    });
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    let second = "aaaaaaaa-6a4e-4c39-9d43-6a1f0e3c2b10";
    // Two given back at once: neither is dropped while the other runs.
    app.release_call("acme", CALL, None);
    app.release_call("acme", second, None);
    let deadline = Instant::now() + Duration::from_secs(6);
    let both = |tico: &FakeTico| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
            && tico.asked(&format!("DELETE /api/v2/calls/{second}/recorder")) == 2
    };
    while !both(&tico) && Instant::now() < deadline {
        app.tick();
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(both(&tico), "{:?}", tico.seen());
}

#[test]
fn a_release_sends_an_empty_json_body() {
    let tico = FakeTico::start(|_, _| (200, json!({})));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.release_call("acme", CALL, None);
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    assert_eq!(
        tico.bodies.lock().unwrap().as_slice(),
        [format!("DELETE {} {{}}", claim_path())]
    );
}

#[test]
fn tico_saying_the_call_has_transcript_wins_and_a_refused_release_is_queued() {
    static RELEASES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let tico = FakeTico::start(|method, path| {
        if path == claim_path() && method == "POST" {
            return (
                200,
                json!({"source":true,"meeting_id":"live-1","title":"Pricing call","has_transcript":true}),
            );
        }
        // Tico takes the source back only on the fourth try: after the claim's own three.
        if method == "DELETE" && RELEASES.fetch_add(1, std::sync::atomic::Ordering::SeqCst) < 3 {
            return (503, json!({"error":{"code":"unavailable"}}));
        }
        // The meeting itself reads as empty: Tico's word is taken over it.
        (200, json!({"id":"live-1","state":"live","seq":0}))
    });
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.link(&call_link(&tico));
    let deadline = Instant::now() + Duration::from_secs(6);
    while tico.asked(&format!("DELETE {}", claim_path())) < 4 && Instant::now() < deadline {
        app.tick();
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 4);
    assert_eq!(tico.asked("GET /api/v2/live-meetings/live-1"), 0);
    assert!(app.error.is_empty(), "{}", app.error);
    assert!(app.store.list().unwrap().is_empty(), "nothing recorded");
}

#[test]
fn a_refused_line_on_a_call_reads_the_meeting_instead_of_retrying() {
    let tico = FakeTico::start(|method, path| {
        if method == "POST" && path == "/api/v2/live-meetings/live-1/chunks" {
            // The call ended and its source went: Tico refuses this Recorder's lines.
            return (
                403,
                json!({"error":{"code":"forbidden","detail":"Only the person who connected this meeting can control its transcript"}}),
            );
        }
        source_answer(method, path, "ended")
    });
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Pricing call").unwrap();
    store
        .update_context(&record.id, |context| {
            context.live_destination = "acme".into();
            context.live_id = "live-1".into();
            context.live_state = "live".into();
            context.call_id = CALL.into();
            Ok(())
        })
        .unwrap();
    store
        .save_audio(
            &record.id,
            tico_recorder_native::audio::Channel::Microphone,
            0,
            1000,
            &vec![0.2; 16000],
            16000,
        )
        .unwrap();
    let claimed = store.claim_transcription().unwrap().unwrap();
    store
        .transcription_result(&claimed, "Ship it on Friday", "", json!({"fixture":true}))
        .unwrap();
    let config = settings::Settings {
        destinations: vec![Destination {
            id: "acme".into(),
            name: "Acme".into(),
            base_url: tico.url.clone(),
            actor: "human:1".into(),
        }],
        ..Default::default()
    };
    let remote = local_tico(&config, "acme").unwrap();
    remote.push_live(&store, &record.id).unwrap();
    assert_eq!(tico.asked("POST /api/v2/live-meetings/live-1/chunks"), 1);
    assert_eq!(store.context(&record.id).unwrap().live_state, "ended");
}

#[test]
fn a_call_waiting_for_sign_in_goes_when_its_card_is_replaced_another_org_signs_in_or_it_is_old() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    let signed_in = app.config.destinations.clone();
    let claims = || tico.asked(&format!("POST {}", claim_path()));
    let wait = |app: &mut Recorder, setup: &dyn Fn(&mut Recorder)| {
        app.config.destinations.clear();
        app.link(&call_link(&tico));
        assert_eq!(app.connect.as_deref(), Some(tico.url.as_str()));
        setup(app);
        app.config.destinations = signed_in.clone();
        app.continue_record();
        settle(app, |_| false);
    };
    // Another connect link replaces the card.
    wait(&mut app, &|app| {
        app.link("tico-recorder://connect?server=https://other.example.com")
    });
    // Another org's sign-in finishes first.
    wait(&mut app, &|app| {
        app.config.destinations = vec![connection("other", "other.example.com")];
        app.continue_record();
    });
    // Five minutes pass.
    wait(&mut app, &|app| app.expire_pending_record());
    assert_eq!(claims(), 0, "{:?}", tico.seen());
}

#[test]
fn a_call_from_an_org_just_signed_in_to_records_only_on_record() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    let signed_in = app.config.destinations.clone();
    let after_sign_in = |app: &mut Recorder| {
        app.config.destinations.clear();
        app.link(&call_link(&tico));
        app.config.destinations = signed_in.clone();
        app.connect = None;
        app.continue_record();
        settle(app, |app| !app.status_view()["record_prompt"].is_null());
    };
    after_sign_in(&mut app);
    // The link came for an org this Recorder was not signed in to: it asks, and records nothing yet.
    assert_eq!(app.status_view()["record_prompt"]["title"], "Pricing call");
    assert!(app.status_view()["recording"].is_null());
    assert!(app.store.list().unwrap().is_empty());
    // Cancel gives the source back.
    app.dismiss_record();
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 1);
    assert!(app.status_view()["record_prompt"].is_null());
    assert!(app.store.list().unwrap().is_empty());
    // Record records into the call's meeting.
    after_sign_in(&mut app);
    app.confirm_record();
    assert!(app.status_view()["record_prompt"].is_null());
    let id = app.status_view()["recording"]["id"]
        .as_str()
        .expect("recording after Record")
        .to_owned();
    assert_eq!(app.store.context(&id).unwrap().call_id, CALL);
}

#[test]
fn capture_that_ends_by_itself_keeps_the_call_in_tico_and_gives_the_source_back() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    // No inputs: capture ends at once, as a lost device would end it.
    app.start_capture = |store, id, _, _| {
        tico_recorder_native::engine::RecordingHandle::with_sources(
            store.clone(),
            id.into(),
            || Ok((vec![], String::new())),
        )
    };
    app.accept_claim(
        "acme",
        CALL,
        Claim::Source {
            meeting_id: "live-1".into(),
            title: "Pricing call".into(),
        },
    );
    let id = app.meeting.clone().unwrap();
    settle(&mut app, |_| {
        tico.asked(&format!("DELETE {}", claim_path())) == 1
    });
    assert_eq!(tico.asked(&format!("DELETE {}", claim_path())), 1);
    assert!(sent_to(&app.store.get(&id).unwrap().deliveries, "acme"));
}

#[test]
fn quitting_mid_call_gives_the_source_back_and_keeps_the_call_in_tico() {
    let tico = FakeTico::start(|method, path| source_answer(method, path, "live"));
    let directory = tempfile::tempdir().unwrap();
    let mut app = call_app(directory.path(), &tico);
    app.accept_claim(
        "acme",
        CALL,
        Claim::Source {
            meeting_id: "live-1".into(),
            title: "Pricing call".into(),
        },
    );
    let id = app.meeting.clone().unwrap();
    app.shutdown();
    // Given back before quit returns (the background release may also get there: Tico takes it as one).
    assert!(tico.asked(&format!("DELETE {}", claim_path())) >= 1);
    assert!(sent_to(&app.store.get(&id).unwrap().deliveries, "acme"));
}

#[test]
fn a_call_still_live_at_launch_is_watched_until_its_call_ends() {
    static ENDED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    let tico = FakeTico::start(|method, path| {
        let ended = ENDED.load(std::sync::atomic::Ordering::SeqCst);
        source_answer(method, path, if ended { "ended" } else { "live" })
    });
    let directory = tempfile::tempdir().unwrap();
    // Left by a Recorder that crashed mid-call.
    let store = Store::open(directory.path()).unwrap();
    let record = store.create("Pricing call").unwrap();
    store
        .update_context(&record.id, |context| {
            context.live_destination = "acme".into();
            context.live_id = "live-1".into();
            context.live_state = "live".into();
            context.call_id = CALL.into();
            Ok(())
        })
        .unwrap();
    drop(store);
    let mut app = call_app(directory.path(), &tico);
    let read = || tico.asked("GET /api/v2/live-meetings/live-1");
    settle(&mut app, |_| read() >= 1);
    assert!(read() >= 1);
    assert!(!sent_to(
        &app.store.get(&record.id).unwrap().deliveries,
        "acme"
    ));
    assert_eq!(full_view(&mut app)["meeting"]["data"], Value::Null);
    ENDED.store(true, std::sync::atomic::Ordering::SeqCst);
    let deadline = Instant::now() + Duration::from_secs(8);
    while !sent_to(&app.store.get(&record.id).unwrap().deliveries, "acme")
        && Instant::now() < deadline
    {
        app.tick();
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(sent_to(
        &app.store.get(&record.id).unwrap().deliveries,
        "acme"
    ));
    assert_eq!(app.store.context(&record.id).unwrap().live_state, "ended");
}

// ---- `tico-recorder://record` from Join on a Tico calendar event ----

const EVENT: &str = "google:team@example.com:abc123_20261110T150000Z";

#[test]
fn record_links_carry_a_call_or_an_event() {
    let link = |query: &str| RecordLink::parse(&format!("tico-recorder://record?{query}"));
    let server = "server=https://tico.example.com/";
    // A Tico call: the call wins over anything else the link carries, its id in the canonical form a path takes.
    let call = link(&format!(
        "{server}&call=6F1C1A52-6A4E-4C39-9D43-6A1F0E3C2B10&title=Ignored&event=x"
    ))
    .unwrap();
    assert_eq!(call.server, "https://tico.example.com");
    assert_eq!(call.call.as_deref(), Some(CALL));
    assert_eq!((call.title, call.event), (None, None));
    // A calendar event: the occurrence joined, and its title for showing the request.
    let event = link(&format!(
        "{server}&title=%20Weekly%20sync%0A&event={}",
        EVENT.replace('@', "%40")
    ))
    .unwrap();
    assert_eq!(event.call, None);
    assert_eq!(event.title.as_deref(), Some("Weekly sync"));
    assert_eq!(event.event.as_deref(), Some(EVENT));
    assert_eq!(link(&format!("{server}&event=x")).unwrap().title, None);
    let long = link(&format!("{server}&event=x&title={}", "é".repeat(500))).unwrap();
    assert_eq!(long.title.unwrap().chars().count(), 200);
    // Refused: a title alone (any page could start a recording with one), an event or call id that is not one, no
    // safe server, another action or scheme.
    for refused in [
        format!("tico-recorder://record?{server}&title=Plan"),
        format!("tico-recorder://record?{server}&title=Plan&event="),
        format!("tico-recorder://record?{server}&title=Plan&event=has%20space"),
        format!("tico-recorder://record?{server}&title=Plan&event=tab%09in"),
        format!(
            "tico-recorder://record?{server}&title=Plan&event={}",
            "x".repeat(301)
        ),
        format!("tico-recorder://record?{server}&call=not-a-uuid&event=x"),
        format!("tico-recorder://record?{server}&call="),
        format!("tico-recorder://record?{server}&call=../../api/v2/logout"),
        format!("tico-recorder://record?{server}&call={CALL}%2F..%2Fx"),
        "tico-recorder://record?event=x".to_owned(),
        format!("tico-recorder://record?call={CALL}"),
        "tico-recorder://record?server=&event=x".to_owned(),
        "tico-recorder://record?server=http://evil.example&event=x".to_owned(),
        format!("tico-recorder://record?server=https://user:pw@tico.example.com&call={CALL}"),
        "tico-recorder://record?server=https://tico.example.com/?x=1&event=x".to_owned(),
        format!("tico-recorder://record?{server}"),
        format!("tico-recorder://open?{server}&event=x"),
        format!("tico://record?{server}&call={CALL}"),
        format!("https://record?{server}&event=x"),
    ] {
        assert_eq!(RecordLink::parse(&refused), None, "{refused}");
    }
    assert_eq!(
        occurrence_path("google:en.usa#holiday@group.v.calendar.google.com:e1"),
        "/api/v2/calendar/events/google:en.usa%23holiday@group.v.calendar.google.com:e1"
    );
}

/// An occurrence as Tico's calendar answers one, from `start` to `end` minutes from now.
fn occurrence(title: &str, start: i64, end: i64, join: bool) -> Value {
    let at = |minutes| {
        (chrono::Utc::now() + chrono::Duration::minutes(minutes))
            .format("%Y-%m-%dT%H:%M:%SZ")
            .to_string()
    };
    json!({"occurrence_id": EVENT, "title": title, "start": at(start), "end": at(end), "can": {"join": join}})
}

#[test]
fn only_an_event_the_person_can_join_and_that_is_on_now_is_recorded() {
    let now = chrono::Utc::now();
    assert_eq!(
        joinable(&occurrence("Sync", 10, 40, true), now).as_deref(),
        Ok("Sync")
    );
    assert_eq!(
        joinable(&occurrence("Sync", -30, 10, true), now).as_deref(),
        Ok("Sync")
    );
    assert!(
        joinable(&occurrence("Sync", 20, 50, true), now).is_err(),
        "too early"
    );
    assert!(
        joinable(&occurrence("Sync", -60, -1, true), now).is_err(),
        "over"
    );
    assert!(
        joinable(&occurrence("Busy", -5, 30, false), now).is_err(),
        "not joinable"
    );
    assert!(
        joinable(
            &json!({"title":"Day off","start":"2026-11-10","end":"2026-11-11","can":{"join":true}}),
            now
        )
        .is_err()
    );
    let long = joinable(&occurrence(&"é".repeat(300), -5, 30, true), now).unwrap();
    assert_eq!(long.chars().count(), 200);
}

/// A local Tico for the event-link tests: answers the occurrence check with `occurrence` (404 when None), and what
/// opening a live meeting asks.
fn join_tico(occurrence: Option<Value>) -> FakeTico {
    FakeTico::start(move |method, path| match (method, path) {
        ("GET", "/api/v2/me") => (200, json!({"actor":"human:1","name":"Ada"})),
        ("GET", path) if path.starts_with("/api/v2/calendar/events/") => match &occurrence {
            Some(found) => (200, found.clone()),
            None => (
                404,
                json!({"error":{"code":"not_found","detail":"There is no such event"}}),
            ),
        },
        ("POST", "/api/v2/live-meetings") => (200, json!({"id":"live-1","state":"live"})),
        ("GET", path) if path.starts_with("/api/v2/live-meetings/live-1") => {
            (200, json!({"id":"live-1","state":"live","chat":[]}))
        }
        _ => (200, json!({})),
    })
}
impl FakeTico {
    /// Whether the occurrence was asked for.
    fn checked(&self) -> bool {
        self.seen()
            .iter()
            .any(|seen| seen.starts_with("GET /api/v2/calendar/events/"))
    }
}
fn acme(url: &str) -> Destination {
    Destination {
        id: "acme".into(),
        name: "Acme".into(),
        base_url: url.into(),
        actor: "human:1".into(),
    }
}
/// A Recorder signed in to nothing, recording silently against local Ticos.
fn join_app(directory: &std::path::Path) -> Recorder {
    let mut app = app(Store::open(directory).unwrap(), directory);
    app.tico_for = local_tico;
    app.start_capture = silent_capture;
    app
}
fn join_link(server: &str, title: &str) -> String {
    format!(
        "tico-recorder://record?server={server}&title={}&event={}",
        title.replace(' ', "%20"),
        EVENT.replace('@', "%40")
    )
}
fn recording(app: &Recorder) -> Option<String> {
    app.status_view()["recording"]["id"]
        .as_str()
        .map(str::to_owned)
}
/// Runs background work until the occurrence check has been answered and handled.
fn settle_check(app: &mut Recorder, tico: &FakeTico) {
    settle(app, |_| tico.checked());
    settle(app, |app| !app.checking_record());
}
/// The live meetings Tico was asked to open, as their bodies.
fn live_meetings_made(tico: &FakeTico) -> Vec<Value> {
    tico.bodies
        .lock()
        .unwrap()
        .iter()
        .filter_map(|seen| seen.strip_prefix("POST /api/v2/live-meetings "))
        .map(|body| serde_json::from_str(body).unwrap())
        .collect()
}

#[test]
fn a_record_link_records_the_event_live_under_its_orgs_title_and_never_a_second_recording() {
    let tico = join_tico(Some(occurrence("Weekly sync", 5, 35, true)));
    let directory = tempfile::tempdir().unwrap();
    let mut app = join_app(directory.path());
    app.config.destinations = vec![connection("other", "other.example.com"), acme(&tico.url)];
    app.destination = "other".into();
    app.link(&join_link(&tico.url, "Whatever the page says"));
    assert_eq!(
        recording(&app),
        None,
        "nothing records before Tico shows the event"
    );
    settle_check(&mut app, &tico);
    // Signed in already: it records at once, without asking.
    assert!(app.status_view()["record_prompt"].is_null());
    let id = recording(&app).expect("recording started");
    assert_eq!(app.meeting.as_deref(), Some(id.as_str()));
    assert_eq!(app.store.get(&id).unwrap().title, "Weekly sync");
    assert_eq!(app.store.context(&id).unwrap().calendar_event, EVENT);
    assert_eq!(app.destination, "acme");
    assert!(app.connect.is_none());
    // Join on another event while recording: the recording in progress stays the only one, and comes to the front.
    let _ = app.open(&app.store.create("Older").unwrap().id);
    app.link(&join_link(&tico.url, "Another meeting"));
    assert_eq!(app.meeting.as_deref(), Some(id.as_str()));
    assert_eq!(recording(&app).as_deref(), Some(id.as_str()));
    assert_eq!(app.store.summaries().unwrap().len(), 2);
    settle(&mut app, |app| {
        !app.store.context(&id).unwrap().live_id.is_empty()
    });
    let context = app.store.context(&id).unwrap();
    assert_eq!(
        (context.live_id.as_str(), context.live_destination.as_str()),
        ("live-1", "acme")
    );
    let created = live_meetings_made(&tico);
    assert_eq!(created.len(), 1);
    assert_eq!(created[0]["title"], "Weekly sync");
    assert_eq!(created[0]["calendar_event"], EVENT);
    assert_eq!(created[0]["visibility"], "private");
    app.shutdown();
}

#[test]
fn a_record_link_records_nothing_for_an_event_tico_does_not_show_or_that_is_not_on_now() {
    for (occurrence, why) in [
        (None, "Tico answers 404"),
        (Some(occurrence("Sync", 60, 90, true)), "an hour away"),
        (Some(occurrence("Sync", -90, -30, true)), "over"),
        (Some(occurrence("Busy", -5, 25, false)), "can't join"),
    ] {
        let tico = join_tico(occurrence);
        let directory = tempfile::tempdir().unwrap();
        let mut app = join_app(directory.path());
        app.config.destinations = vec![acme(&tico.url)];
        app.link(&join_link(&tico.url, "Sync"));
        settle_check(&mut app, &tico);
        assert_eq!(recording(&app), None, "{why}");
        assert!(app.status_view()["record_prompt"].is_null(), "{why}");
        assert!(app.store.summaries().unwrap().is_empty(), "{why}");
        assert!(
            !tico.seen().iter().any(|seen| seen.starts_with("POST ")),
            "{why}"
        );
    }
    // A title-only link is refused before anything is asked.
    let tico = join_tico(Some(occurrence("Sync", 0, 30, true)));
    let directory = tempfile::tempdir().unwrap();
    let mut app = join_app(directory.path());
    app.config.destinations = vec![acme(&tico.url)];
    app.link(&format!(
        "tico-recorder://record?server={}&title=Sync",
        tico.url
    ));
    settle(&mut app, |_| false);
    assert_eq!(recording(&app), None);
    assert!(tico.seen().is_empty());
}

#[test]
fn a_record_link_for_an_org_not_signed_in_signs_in_first_then_asks_before_recording() {
    let tico = join_tico(Some(occurrence("Weekly sync", 0, 30, true)));
    let directory = tempfile::tempdir().unwrap();
    let mut app = join_app(directory.path());
    app.link(&join_link(&tico.url, "Weekly sync"));
    assert_eq!(app.connect.as_deref(), Some(tico.url.as_str()));
    assert_eq!(recording(&app), None);
    // Dismissing the sign-in drops the request: a later sign-in records nothing.
    app.dismiss_connect();
    app.config.destinations = vec![acme(&tico.url)];
    app.continue_record();
    settle(&mut app, |_| false);
    assert!(!tico.checked());
    // A request older than five minutes is dropped too.
    app.config.destinations.clear();
    app.link(&join_link(&tico.url, "Weekly sync"));
    app.expire_pending_record();
    app.config.destinations = vec![acme(&tico.url)];
    app.continue_record();
    settle(&mut app, |_| false);
    assert!(!tico.checked());
    // Signing in to that org goes on with the check, then asks once, with the org's title.
    let after_sign_in = |app: &mut Recorder| {
        app.config.destinations.clear();
        app.link(&join_link(&tico.url, "What the page says"));
        app.config.destinations = vec![acme(&tico.url)];
        app.connect = None;
        app.continue_record();
        settle(app, |app| !app.status_view()["record_prompt"].is_null());
    };
    after_sign_in(&mut app);
    assert_eq!(app.status_view()["record_prompt"]["title"], "Weekly sync");
    assert_eq!(recording(&app), None);
    assert!(app.store.summaries().unwrap().is_empty());
    // Cancel records nothing.
    app.dismiss_record();
    settle(&mut app, |_| false);
    assert!(app.status_view()["record_prompt"].is_null());
    assert_eq!(recording(&app), None);
    assert!(app.store.summaries().unwrap().is_empty());
    // Record records the event live.
    after_sign_in(&mut app);
    app.confirm_record();
    assert!(app.status_view()["record_prompt"].is_null());
    let id = recording(&app).expect("recording after Record");
    assert_eq!(app.store.get(&id).unwrap().title, "Weekly sync");
    assert_eq!(app.store.context(&id).unwrap().calendar_event, EVENT);
    settle(&mut app, |app| {
        !app.store.context(&id).unwrap().live_id.is_empty()
    });
    assert_eq!(app.store.context(&id).unwrap().live_id, "live-1");
    assert_eq!(live_meetings_made(&tico).len(), 1);
    app.shutdown();
}

#[test]
fn record_on_the_question_starts_nothing_while_another_recording_runs() {
    let tico = join_tico(Some(occurrence("Weekly sync", 0, 30, true)));
    let directory = tempfile::tempdir().unwrap();
    let mut app = join_app(directory.path());
    app.link(&join_link(&tico.url, "Weekly sync"));
    app.config.destinations = vec![acme(&tico.url)];
    app.connect = None;
    app.continue_record();
    settle(&mut app, |app| {
        !app.status_view()["record_prompt"].is_null()
    });
    let running = app.record(None).expect("a recording by hand");
    app.confirm_record();
    assert_eq!(recording(&app).as_deref(), Some(running.as_str()));
    assert_eq!(app.store.summaries().unwrap().len(), 1);
    assert!(live_meetings_made(&tico).is_empty());
    app.shutdown();
}
