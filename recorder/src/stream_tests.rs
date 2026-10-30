use super::*;
use crate::{
    audio::Channel,
    capture::{AudioSink, CaptureStream, Source},
    engine::RecordingHandle,
};
use serde_json::json;
use std::net::TcpListener;

type Server = WebSocket<TcpStream>;
type Session = Box<dyn FnOnce(&mut Server) + Send>;
/// The URI and Authorization header of each connection the mock accepted.
type Seen = Arc<Mutex<Vec<(String, String)>>>;

/// A local server speaking the streaming protocol. Each accepted connection runs the next
/// session script.
fn mock(sessions: Vec<Session>) -> (String, Seen, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("ws://{}/v1/stt", listener.local_addr().unwrap());
    let seen: Seen = Arc::default();
    let requests = seen.clone();
    let worker = thread::spawn(move || {
        for session in sessions {
            let (tcp, _) = listener.accept().unwrap();
            // Generous: under a loaded parallel test run the client can take seconds to
            // reconnect and send; this only bounds how long a broken run hangs.
            tcp.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
            let requests = requests.clone();
            let mut socket = tungstenite::accept_hdr(
                tcp,
                move |request: &tungstenite::handshake::server::Request,
                      response: tungstenite::handshake::server::Response| {
                    let auth = request
                        .headers()
                        .get("authorization")
                        .and_then(|value| value.to_str().ok())
                        .unwrap_or("")
                        .to_owned();
                    requests
                        .lock()
                        .unwrap()
                        .push((request.uri().to_string(), auth));
                    Ok(response)
                },
            )
            .unwrap();
            session(&mut socket);
        }
    });
    (url, seen, worker)
}

fn send(socket: &mut Server, event: Value) {
    socket.send(Message::text(event.to_string())).unwrap();
}
fn created(socket: &mut Server) {
    send(socket, json!({"type":"transcript.created","id":"fixture"}));
}
/// Reads audio frames until at least `bytes` arrived; returns the frame sizes.
fn audio(socket: &mut Server, bytes: usize) -> Vec<usize> {
    let mut frames = vec![];
    while frames.iter().sum::<usize>() < bytes {
        match socket.read().unwrap() {
            Message::Binary(data) => frames.push(data.len()),
            Message::Text(text) => panic!("Unexpected text before audio: {text}"),
            _ => {}
        }
    }
    frames
}
/// Reads whatever the client already sent, until it pauses.
fn drain(socket: &mut Server) {
    socket
        .get_mut()
        .set_read_timeout(Some(Duration::from_millis(300)))
        .unwrap();
    while socket.read().is_ok() {}
}
/// Reads until the client says the audio is done.
fn until_done(socket: &mut Server) {
    loop {
        if let Message::Text(text) = socket.read().unwrap() {
            assert_eq!(text.as_str(), r#"{"type":"audio.done"}"#);
            return;
        }
    }
}
fn result(
    final_: bool,
    speech_final: bool,
    start: f64,
    duration: f64,
    words: &[(&str, f64, f64)],
) -> Value {
    json!({
        "type": "transcript.partial",
        "text": words.iter().map(|w| w.0).collect::<Vec<_>>().join(" "),
        "words": words.iter().map(|(text, start, end)| json!({"text":text,"start":start,"end":end,"speaker":1})).collect::<Vec<_>>(),
        "is_final": final_, "speech_final": speech_final, "start": start, "duration": duration,
    })
}

fn plan(url: &str) -> Plan {
    Plan {
        url: url.into(),
        model: DEFAULT_MODEL.into(),
        language: "en".into(),
        key: Some("fixture-key".into()),
        tico: None,
        required: true,
        retry: Duration::from_millis(50),
    }
}
fn wait_for(what: &str, mut ready: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(45);
    while !ready() {
        assert!(Instant::now() < deadline, "Timed out waiting for {what}");
        thread::sleep(Duration::from_millis(10));
    }
}
fn state(id: &str, channel: &str) -> StreamStatus {
    status(id)
        .into_iter()
        .find(|row| row.channel == channel)
        .unwrap_or_default()
}
fn texts(store: &Store, id: &str, channel: &str) -> Vec<(String, u64)> {
    store
        .live_words(id)
        .unwrap()
        .get(channel)
        .into_iter()
        .flatten()
        .map(|word| (word.text.clone(), word.start_ms))
        .collect()
}
const SECOND: usize = 16_000;

#[test]
fn service_times_map_to_the_recording_across_gaps() {
    // 0.5 s streamed from 10 s into the recording, then audio resumed at 20 s.
    let anchors = [(0, 10_000.), (8_000, 20_000.)];
    assert_eq!(position(&anchors, 0.25), 10_250);
    assert_eq!(position(&anchors, 0.7), 20_200);
    assert_eq!(speaker(&json!(0)), "S1");
    assert_eq!(speaker(&json!(null)), "");
}

#[test]
fn partials_show_live_finals_replace_what_they_cover_and_the_key_stays_in_the_header() {
    let (gate, opened) = mpsc::channel::<()>();
    let (url, seen, server) = mock(vec![Box::new(move |socket: &mut Server| {
        created(socket);
        let frames = audio(socket, 2 * SECOND * 2);
        // 100 ms frames of PCM16.
        assert!(frames.iter().all(|len| *len == FRAME_BYTES), "{frames:?}");
        send(
            socket,
            result(
                false,
                false,
                0.0,
                1.0,
                &[("We", 0.1, 0.3), ("chip", 0.4, 0.7)],
            ),
        );
        opened.recv_timeout(Duration::from_secs(5)).unwrap();
        // Within an utterance a final carries only its new words while `start` stays at the
        // utterance start; it must not erase the earlier final.
        send(
            socket,
            result(
                true,
                false,
                0.0,
                0.8,
                &[("We", 0.1, 0.3), ("chip", 0.4, 0.7)],
            ),
        );
        send(
            socket,
            result(
                true,
                false,
                0.0,
                1.6,
                &[("on", 1.0, 1.1), ("Friday", 1.2, 1.5)],
            ),
        );
        // A revision of the last words, then the end of the utterance restating all of it.
        send(
            socket,
            result(true, false, 0.0, 1.9, &[("Friday.", 1.2, 1.6)]),
        );
        send(
            socket,
            result(
                true,
                true,
                0.0,
                2.0,
                &[
                    ("We", 0.1, 0.3),
                    ("ship", 0.4, 0.7),
                    ("on", 1.0, 1.1),
                    ("Friday.", 1.2, 1.6),
                ],
            ),
        );
        until_done(socket);
        send(
            socket,
            json!({"type":"transcript.done","text":"","words":[],"duration":2.0}),
        );
    })]);
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Streamed fixture").unwrap();
    let mut streams = Streams::new(store.clone(), record.id.clone(), plan(&url));
    let feed = streams.feed("them").unwrap();
    assert!(streams.feed("them").is_none(), "one stream per channel");
    // Two seconds starting 60 s into the recording (a resumed recording, say).
    feed.push(60_000., &vec![0.1; SECOND]);
    feed.push(61_000., &vec![0.1; SECOND]);
    wait_for("partial text", || !partials(&record.id).is_empty());
    let turns = live_turns(&store, &record.id).unwrap();
    assert_eq!(turns.len(), 1);
    assert!(turns[0].partial && turns[0].streamed);
    assert_eq!(
        (turns[0].text.as_str(), turns[0].start_ms),
        ("We chip", 60_100)
    );
    assert_eq!(turns[0].speaker, "S2");
    gate.send(()).unwrap();
    wait_for("the restated final", || {
        texts(&store, &record.id, "them")
            .iter()
            .any(|(text, _)| text == "ship")
    });
    drop(feed);
    wait_for("the stream to stop", || {
        state(&record.id, "them").state == "stopped"
    });
    server.join().unwrap();
    assert_eq!(
        texts(&store, &record.id, "them"),
        [
            ("We", 60_100),
            ("ship", 60_400),
            ("on", 61_000),
            ("Friday.", 61_200)
        ]
        .map(|(text, at)| (text.to_owned(), at))
    );
    assert!(partials(&record.id).is_empty());
    let turns = live_turns(&store, &record.id).unwrap();
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].text, "We ship on Friday.");
    assert!(turns[0].streamed && !turns[0].partial);
    let requests = seen.lock().unwrap();
    let (uri, auth) = &requests[0];
    assert_eq!(auth, "Bearer fixture-key");
    assert!(!uri.contains("fixture-key"));
    for part in [
        "model=grok-voice-transcribe-2.0",
        "encoding=pcm",
        "sample_rate=16000",
        "interim_results=true",
        "language=en",
        "diarize=true",
    ] {
        assert!(uri.contains(part), "{uri} lacks {part}");
    }
    let status = state(&record.id, "them");
    assert_eq!((status.reconnects, status.dropped_ms), (0, 0));
}

#[test]
fn a_reconnected_stream_places_its_words_at_the_right_recording_time() {
    let (url, seen, server) = mock(vec![
        Box::new(|socket: &mut Server| {
            created(socket);
            audio(socket, FRAME_BYTES);
            send(socket, result(true, true, 0.0, 1.0, &[("first", 0.5, 0.8)]));
            // The connection then drops without a close, once the audio already sent is read:
            // closing with unread data resets the connection, which can discard the final
            // before the client reads it.
            drain(socket);
        }),
        Box::new(|socket: &mut Server| {
            created(socket);
            audio(socket, FRAME_BYTES);
            send(
                socket,
                result(true, true, 0.0, 0.5, &[("second", 0.2, 0.4)]),
            );
            until_done(socket);
            send(socket, json!({"type":"transcript.done"}));
        }),
    ]);
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Reconnect fixture").unwrap();
    let mut streams = Streams::new(store.clone(), record.id.clone(), plan(&url));
    let feed = streams.feed("me").unwrap();
    feed.push(10_000., &vec![0.1; SECOND]);
    wait_for("the first final", || {
        !texts(&store, &record.id, "me").is_empty()
    });
    wait_for("the reconnect", || {
        let status = state(&record.id, "me");
        status.reconnects == 1 && status.state == "live"
    });
    // Speech resumes 30 s into the recording on the new connection, whose clock starts at 0.
    feed.push(30_000., &vec![0.1; SECOND]);
    wait_for("the second final", || {
        texts(&store, &record.id, "me").len() == 2
    });
    drop(feed);
    wait_for("the stream to stop", || {
        state(&record.id, "me").state == "stopped"
    });
    server.join().unwrap();
    assert_eq!(
        texts(&store, &record.id, "me"),
        [("first".to_owned(), 10_500), ("second".to_owned(), 30_200)]
    );
    // The microphone side asks for no speaker labels.
    assert!(
        seen.lock()
            .unwrap()
            .iter()
            .all(|(uri, _)| !uri.contains("diarize"))
    );
}

#[test]
fn a_service_error_is_shown_and_retried() {
    let (url, _, server) = mock(vec![
        Box::new(|socket: &mut Server| {
            created(socket);
            send(socket, json!({"type":"error","message":"rate limited"}));
            let _ = socket.read();
        }),
        Box::new(|socket: &mut Server| {
            created(socket);
            until_done(socket);
            send(socket, json!({"type":"transcript.done"}));
        }),
    ]);
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Error fixture").unwrap();
    let mut slow = plan(&url);
    slow.retry = Duration::from_millis(600);
    let mut streams = Streams::new(store.clone(), record.id.clone(), slow);
    let feed = streams.feed("me").unwrap();
    wait_for("the retry", || state(&record.id, "me").reconnects == 1);
    let shown = state(&record.id, "me");
    assert_eq!(shown.state, "retrying");
    assert!(shown.error.contains("rate limited"), "{shown:?}");
    wait_for("the second session", || {
        state(&record.id, "me").state == "live"
    });
    drop(feed);
    wait_for("the stream to stop", || {
        state(&record.id, "me").state == "stopped"
    });
    server.join().unwrap();
}

#[test]
fn without_a_key_streaming_is_off_and_recording_is_unaffected() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("No key fixture").unwrap();
    let mut quiet = plan("ws://127.0.0.1:9/v1/stt");
    quiet.key = Some(String::new());
    quiet.required = false;
    let mut streams = Streams::new(store.clone(), record.id.clone(), quiet);
    let feed = streams.feed("me").unwrap();
    wait_for("off", || state(&record.id, "me").state == "off");
    assert!(state(&record.id, "me").error.is_empty());
    // Pushing to a finished stream is harmless.
    feed.push(0., &[0.1; 160]);
    let mut config = Settings::default();
    assert!(
        Plan::from_settings(&config).is_some_and(|plan| !plan.required && plan.url == DEFAULT_URL)
    );
    config.streaming = Some(false);
    assert!(Plan::from_settings(&config).is_none());
    assert!(!enabled(&config));
}

#[test]
fn a_full_stream_queue_drops_streamed_audio_and_never_recorded_audio() {
    struct Synthetic;
    impl CaptureStream for Synthetic {
        fn stop(&mut self) -> Result<()> {
            Ok(())
        }
    }
    // A service that accepts the connection and never answers: the queue fills during the
    // handshake while capture keeps going.
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("ws://{}/v1/stt", listener.local_addr().unwrap());
    let stalled = thread::spawn(move || {
        let held = listener.accept().unwrap();
        thread::sleep(Duration::from_secs(3));
        drop(held);
    });
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Overflow fixture").unwrap();
    let total = SECOND * 70;
    let done = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let fed = done.clone();
    let handle = RecordingHandle::with_sources_streaming(
        store.clone(),
        record.id.clone(),
        Some(plan(&url)),
        move || {
            let (mut sink, consumer) = AudioSink::channel();
            let health = sink.health.clone();
            thread::spawn(move || {
                // Speech-like bursts with pauses, so chunks are cut as in a real recording.
                let samples: Vec<f32> = (0..total)
                    .map(|n| {
                        if n % (SECOND * 16 / 10) < SECOND * 13 / 10 {
                            (n as f32 * 300. * std::f32::consts::TAU / 16_000.).sin() * 0.3
                        } else {
                            0.0005
                        }
                    })
                    .collect();
                for part in samples.chunks(1024 * 32) {
                    sink.push_interleaved(part, 1);
                    thread::sleep(Duration::from_millis(20));
                }
                fed.store(true, Ordering::Release);
                thread::sleep(Duration::from_secs(5));
            });
            Ok((
                vec![Source {
                    channel: Channel::Microphone,
                    name: "synthetic".into(),
                    sample_rate: 16_000,
                    started: Instant::now(),
                    health,
                    consumer,
                    stream: Box::new(Synthetic),
                }],
                String::new(),
            ))
        },
    )
    .unwrap();
    wait_for("the audio", || done.load(Ordering::Acquire));
    thread::sleep(Duration::from_millis(200));
    handle.finish().unwrap();
    let chunks = store.get(&record.id).unwrap().chunks;
    let saved: u64 = chunks.iter().map(|chunk| chunk.signal.duration_ms).sum();
    assert!(
        saved.abs_diff(70_000) <= 2,
        "recorded audio was lost: {saved} ms"
    );
    let status = state(&record.id, "me");
    // At most ten seconds wait in the queue; the rest was skipped for live text only.
    assert!(status.dropped_ms >= 59_000, "{status:?}");
    assert!(texts(&store, &record.id, "me").is_empty());
    stalled.join().unwrap();
}

/// Streams a few seconds of a benchmark recording to the real service. Opt-in and paid
/// (fractions of a cent): `TICO_STREAM_SMOKE=1`, with `XAI_API_KEY` or the keychain key.
#[test]
fn live_smoke_against_the_real_service() {
    if std::env::var("TICO_STREAM_SMOKE").as_deref() != Ok("1") {
        return;
    }
    let key = std::env::var("XAI_API_KEY")
        .ok()
        .filter(|key| !key.is_empty())
        .or_else(|| {
            settings::secret(KEY_NAME)
                .ok()
                .filter(|key| !key.is_empty())
        })
        .expect("No xAI key in XAI_API_KEY or the keychain");
    let home = std::env::var("HOME").unwrap();
    let path = std::env::var("TICO_STREAM_SMOKE_WAV")
        .unwrap_or_else(|_| format!("{home}/.cache/tico-recorder-bench/ES2004a.Headset-1.wav"));
    let mut reader = hound::WavReader::open(&path).unwrap();
    let spec = reader.spec();
    assert_eq!((spec.sample_rate, spec.channels), (16_000, 1), "{path}");
    let samples: Vec<f32> = match spec.sample_format {
        hound::SampleFormat::Int => reader
            .samples::<i32>()
            .map(|s| s.unwrap() as f32 / (1u32 << (spec.bits_per_sample - 1)) as f32)
            .collect(),
        hound::SampleFormat::Float => reader.samples::<f32>().map(Result::unwrap).collect(),
    };
    // The loudest eight seconds, where someone is talking.
    let window = 8 * SECOND;
    let energy = |at: usize| samples[at..at + window].iter().map(|x| x * x).sum::<f32>();
    let start = (0..samples.len().saturating_sub(window))
        .step_by(SECOND)
        .max_by(|a, b| energy(*a).total_cmp(&energy(*b)))
        .unwrap();
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Live smoke").unwrap();
    let mut live = plan(DEFAULT_URL);
    live.key = Some(key);
    let mut streams = Streams::new(store.clone(), record.id.clone(), live);
    let feed = streams.feed("them").unwrap();
    let began = Instant::now();
    let mut first_partial = None;
    for (index, frame) in samples[start..start + window]
        .chunks(SECOND / 10)
        .enumerate()
    {
        feed.push(index as f64 * 100., frame);
        if first_partial.is_none() && !partials(&record.id).is_empty() {
            first_partial = Some(began.elapsed());
        }
        let due = began + Duration::from_millis(100 * (index as u64 + 1));
        thread::sleep(due.saturating_duration_since(Instant::now()));
    }
    drop(feed);
    wait_for("the stream to stop", || {
        state(&record.id, "them").state == "stopped"
    });
    let status = state(&record.id, "them");
    let words = texts(&store, &record.id, "them");
    let turns = live_turns(&store, &record.id).unwrap();
    println!(
        "smoke: {} s of {path} from {} s",
        window / SECOND,
        start / SECOND
    );
    println!("smoke: first partial after {first_partial:?}; status {status:?}");
    for turn in &turns {
        println!(
            "smoke: [{}-{} ms] {}: {}",
            turn.start_ms, turn.end_ms, turn.speaker, turn.text
        );
    }
    assert!(status.error.is_empty(), "{status:?}");
    assert!(words.len() >= 5, "too few words came back: {words:?}");
    assert!(words.iter().all(|(_, at)| *at <= 8_500));
}

#[test]
fn through_your_tico_live_text_follows_what_it_can_do() {
    let destination = |id: &str, url: &str| Destination {
        id: id.into(),
        name: "Acme".into(),
        base_url: url.replace("/v1/stt", ""),
        actor: "human:test".into(),
    };
    // A Tico that can't stream: live text is off, quietly; chunks still go.
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Tico off fixture").unwrap();
    let quiet = destination("stream-off", "http://127.0.0.1:9");
    speech::remember(
        &quiet,
        Ok(speech::Services {
            transcribe: true,
            assist: true,
            stream: false,
        }),
    );
    let mut off = plan("ws://127.0.0.1:9/api/v2/recorder/stream");
    off.tico = Some(quiet);
    off.key = None;
    off.required = false;
    let mut streams = Streams::new(store.clone(), record.id.clone(), off);
    let _feed = streams.feed("me").unwrap();
    wait_for("off", || state(&record.id, "me").state == "off");
    assert!(state(&record.id, "me").error.is_empty());
    // A Tico that can: the person's token goes in the header, never the address.
    let (url, seen, server) = mock(vec![Box::new(|socket: &mut Server| {
        created(socket);
        until_done(socket);
        send(socket, json!({"type":"transcript.done"}));
    })]);
    let relay = destination("stream-on", &url);
    speech::remember(
        &relay,
        Ok(speech::Services {
            transcribe: true,
            assist: true,
            stream: true,
        }),
    );
    let record = store.create("Tico on fixture").unwrap();
    let mut on = plan(&url.replace("/v1/stt", "/api/v2/recorder/stream"));
    on.tico = Some(relay);
    on.key = Some("person-token".into());
    on.required = false;
    let mut streams = Streams::new(store.clone(), record.id.clone(), on);
    let feed = streams.feed("me").unwrap();
    wait_for("live", || state(&record.id, "me").state == "live");
    drop(feed);
    wait_for("the stream to stop", || {
        state(&record.id, "me").state == "stopped"
    });
    server.join().unwrap();
    let requests = seen.lock().unwrap();
    let (uri, auth) = &requests[0];
    assert!(uri.starts_with("/api/v2/recorder/stream?"), "{uri}");
    assert!(!uri.contains("person-token"));
    assert_eq!(auth, "Bearer person-token");
}
