use super::*;
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::{Arc, Mutex},
};

fn server(
    steps: Vec<Option<Value>>,
) -> (
    Tico,
    Arc<Mutex<Vec<(String, Value, String)>>>,
    std::thread::JoinHandle<()>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(vec![]));
    let requests = seen.clone();
    listener.set_nonblocking(true).unwrap();
    let handle = std::thread::spawn(move || {
        for reply in steps {
            let deadline = std::time::Instant::now() + Duration::from_secs(8);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(std::time::Instant::now() < deadline, "Missing HTTP request");
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("{error}"),
                }
            };
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut bytes = vec![];
            let end = loop {
                let mut one = [0; 1];
                stream.read_exact(&mut one).unwrap();
                bytes.push(one[0]);
                if bytes.ends_with(b"\r\n\r\n") {
                    break bytes.len();
                }
            };
            let headers = String::from_utf8(bytes[..end].to_vec()).unwrap();
            let mut length = 0;
            let mut key = String::new();
            for line in headers.lines() {
                if let Some((name, value)) = line.split_once(':') {
                    if name.eq_ignore_ascii_case("content-length") {
                        length = value.trim().parse().unwrap();
                    }
                    if name.eq_ignore_ascii_case("idempotency-key") {
                        key = value.trim().to_owned();
                    }
                }
            }
            let mut body = vec![0; length];
            stream.read_exact(&mut body).unwrap();
            requests.lock().unwrap().push((
                headers.lines().next().unwrap().to_string(),
                if body.is_empty() {
                    json!({})
                } else {
                    serde_json::from_slice(&body).unwrap()
                },
                key,
            ));
            if let Some(reply) = reply {
                // `{"status": 404}` alone stands for a route this Tico does not have.
                let missing = reply.as_object().is_some_and(|row| row.len() == 1)
                    && reply["status"].as_u64().is_some_and(|code| code >= 400);
                let status = if missing { format!("{} Not Found", reply["status"]) } else { "200 OK".into() };
                let body = if missing { b"{}".to_vec() } else { serde_json::to_vec(&reply).unwrap() };
                write!(stream,"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len()).unwrap();
                stream.write_all(&body).unwrap();
            }
        }
    });
    let tico = Tico::new(
        Destination {
            id: "synthetic".into(),
            name: "Fixture".into(),
            base_url: url,
            actor: "human:test".into(),
        },
        "synthetic-token".into(),
    )
    .unwrap();
    (tico, seen, handle)
}
fn transcript(store: &Store, title: &str) -> Recording {
    let record = store.create(title).unwrap();
    store
        .save_audio(
            &record.id,
            crate::audio::Channel::Microphone,
            0,
            1000,
            &vec![0.2; 16000],
            16000,
        )
        .unwrap();
    let claimed = store.claim_transcription().unwrap().unwrap();
    store
        .transcription_result(
            &claimed,
            "I will send the proposal",
            "",
            json!({"fixture":true}),
        )
        .unwrap();
    store.get(&record.id).unwrap()
}
#[test]
fn lost_import_response_retries_the_same_payload_and_key_after_restart() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = transcript(&store, "Lost import acknowledgement");
    let who = json!({"actor":"human:test"});
    let (tico, seen, worker) = server(vec![
        Some(who.clone()),
        None,
        Some(who),
        Some(json!({"id":"meeting-fixture"})),
    ]);
    assert!(tico.send(&store, &record.id, false).is_err());
    assert!(
        crate::engine::RecordingHandle::with_sources(
            store.clone(),
            record.id.clone(),
            || anyhow::bail!("A frozen recording must not open inputs")
        )
        .is_err()
    );
    let reopened = Store::open(dir.path()).unwrap();
    assert!(
        reopened
            .edit_transcript(&record.id, &record.chunks[0].id, "Me", "Changed words")
            .is_err()
    );
    assert_eq!(
        tico.send(&reopened, &record.id, false).unwrap(),
        "meeting-fixture"
    );
    worker.join().unwrap();
    let calls = seen.lock().unwrap();
    assert_eq!(calls[1].1, calls[3].1);
    assert_eq!(calls[1].2, calls[3].2);
    assert!(calls[1].0.contains("meetings/import"));
}
#[test]
fn actions_are_only_pushed_when_explicitly_requested() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = transcript(&store, "Reviewed proposal");
    let mut context = store.context(&record.id).unwrap();
    context.items.push(crate::workspace::Item {
        id: "item-fixture".into(),
        title: "Send the proposal".into(),
        quote: "I will send the proposal".into(),
        kind: "task".into(),
        selected: true,
        ..Default::default()
    });
    store.save_context(&record.id, &context).unwrap();
    let (tico, seen, worker) = server(vec![
        Some(json!({"actor":"human:test"})),
        Some(json!({"id":"meeting-fixture"})),
        Some(json!({"sections":{"task":[]}})),
        Some(json!({"item":{"id":"proposal-fixture"}})),
    ]);
    tico.send(&store, &record.id, false).unwrap();
    worker.join().unwrap();
    assert!(
        seen.lock()
            .unwrap()
            .iter()
            .all(|entry| !entry.0.contains("/push"))
    );
}
/// A meeting with one proposed action item, sent without pushing it: import, list, file.
fn send_one_item(steps: Vec<Option<Value>>) -> Vec<String> {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = transcript(&store, "Routes");
    let mut context = store.context(&record.id).unwrap();
    context.items.push(crate::workspace::Item {
        id: "item-fixture".into(),
        title: "Send the proposal".into(),
        quote: "I will send the proposal".into(),
        kind: "task".into(),
        ..Default::default()
    });
    store.save_context(&record.id, &context).unwrap();
    let (tico, seen, worker) = server(steps);
    tico.send(&store, &record.id, false).unwrap();
    worker.join().unwrap();
    let lines = seen.lock().unwrap().iter().map(|row| row.0.split(' ').take(2).collect::<Vec<_>>().join(" ")).collect();
    let saved = store.get(&record.id).unwrap().deliveries[0].receipt["items"]["item-fixture"]["id"].clone();
    assert_eq!(saved, "proposal-fixture");
    lines
}
#[test]
fn meeting_routes_go_to_api_v2_which_runner_hostnames_route() {
    let lines = send_one_item(vec![
        Some(json!({"actor":"human:test"})),
        Some(json!({"id":"meeting-fixture"})),
        Some(json!({"sections":{"task":[]}})),
        Some(json!({"item":{"id":"proposal-fixture"}})),
    ]);
    assert_eq!(&lines[2..], [
        "GET /api/v2/meetings/meeting-fixture/items",
        "POST /api/v2/meetings/meeting-fixture/items",
    ]);
}
#[test]
fn an_older_tico_without_v2_meeting_routes_gets_the_old_path() {
    let missing = || Some(json!({"status":404}));
    let lines = send_one_item(vec![
        Some(json!({"actor":"human:test"})),
        Some(json!({"id":"meeting-fixture"})),
        missing(),
        Some(json!({"sections":{"task":[]}})),
        missing(),
        Some(json!({"item":{"id":"proposal-fixture"}})),
    ]);
    assert_eq!(&lines[2..], [
        "GET /api/v2/meetings/meeting-fixture/items",
        "GET /api/meetings/meeting-fixture/items",
        "POST /api/v2/meetings/meeting-fixture/items",
        "POST /api/meetings/meeting-fixture/items",
    ]);
}
#[test]
fn a_pending_earlier_turn_holds_back_later_source_turns_and_silence_does_not_block() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Ordered live fixture").unwrap();
    store
        .mutate(&record.id, |r| {
            r.meeting_audio = true;
            Ok(())
        })
        .unwrap();
    let earlier = store
        .save_audio(
            &record.id,
            crate::audio::Channel::Microphone,
            0,
            1000,
            &vec![0.2; 16000],
            16000,
        )
        .unwrap();
    store
        .save_audio(
            &record.id,
            crate::audio::Channel::Meeting,
            1000,
            2000,
            &vec![0.; 16000],
            16000,
        )
        .unwrap();
    let mut context = store.context(&record.id).unwrap();
    context.live_id = "live-fixture".into();
    context.live_destination = "synthetic".into();
    context.live_state = "live".into();
    store.save_context(&record.id, &context).unwrap();
    let (tico, seen, worker) = server(vec![
        Some(json!({"id":"live-fixture"})),
        Some(json!({"accepted":[1]})),
        Some(json!({"id":"live-fixture"})),
    ]);
    tico.sync_live(&store, &record.id).unwrap();
    let claimed = store.claim_transcription().unwrap().unwrap();
    assert_eq!(claimed.id, earlier.id);
    store
        .transcription_result(&claimed, "First turn", "", json!({}))
        .unwrap();
    tico.sync_live(&store, &record.id).unwrap();
    worker.join().unwrap();
    let calls = seen.lock().unwrap();
    assert!(calls[0].0.starts_with("GET"));
    assert_eq!(calls[1].1["chunks"][0]["seq"], 1);
    assert_eq!(calls[1].1["chunks"][0]["text"], "First turn");
}

#[test]
fn lost_finalization_response_preserves_notes_and_reuses_the_canonical_meeting() {
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path()).unwrap();
    let record = transcript(&store, "Lost finalization acknowledgement");
    let mut context = store.context(&record.id).unwrap();
    context.live_id = "live-fixture".into();
    context.live_destination = "synthetic".into();
    context.live_state = "ended".into();
    context.notes = "Reviewed original notes".into();
    store.save_context(&record.id, &context).unwrap();
    let (tico, seen, worker) = server(vec![
        Some(json!({"id":"live-fixture","state":"ended"})),
        None,
        Some(json!({"id":"live-fixture","state":"ended"})),
        Some(json!({"meeting_id":"canonical-fixture"})),
    ]);
    assert!(tico.finish_live(&store, &record.id).is_err());
    store
        .update_context(&record.id, |current| {
            current.notes = "Local later notes".into();
            Ok(())
        })
        .unwrap();
    tico.finish_live(&store, &record.id).unwrap();
    tico.finish_live(&store, &record.id).unwrap();
    worker.join().unwrap();
    let requests = seen.lock().unwrap();
    assert_eq!(requests[1].1, requests[3].1);
    assert_eq!(requests[1].2, requests[3].2);
    assert_eq!(requests[3].1, json!({}));
    // The notes reviewed at the first attempt are what `send` files, not later edits.
    let frozen = store.context(&record.id).unwrap().finalize_payload.unwrap();
    assert_eq!(frozen["notes"], "Reviewed original notes");
    assert_eq!(
        store.get(&record.id).unwrap().deliveries[0].meeting_id,
        "canonical-fixture"
    );
}

#[test]
fn empty_or_silent_live_recordings_can_end_without_creating_a_fake_import() {
    for silent in [false, true] {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let record = store.create("Empty connected recording").unwrap();
        if silent {
            store
                .save_audio(
                    &record.id,
                    crate::audio::Channel::Microphone,
                    0,
                    1000,
                    &vec![0.; 16000],
                    16000,
                )
                .unwrap();
        }
        store
            .update_context(&record.id, |context| {
                context.live_id = "empty-fixture".into();
                context.live_destination = "synthetic".into();
                context.live_state = "live".into();
                Ok(())
            })
            .unwrap();
        let (tico, seen, worker) = server(vec![
            Some(json!({"id":"empty-fixture","state":"live"})),
            Some(json!({"state":"ended"})),
        ]);
        tico.finish_live(&store, &record.id).unwrap();
        tico.finish_live(&store, &record.id).unwrap();
        worker.join().unwrap();
        let requests = seen.lock().unwrap();
        assert_eq!(requests.len(), 2);
        assert!(requests[1].0.contains("/control"));
        assert_eq!(requests[1].1["action"], "end");
        assert_eq!(store.context(&record.id).unwrap().live_state, "ended");
        assert!(store.get(&record.id).unwrap().deliveries.is_empty());
        assert!(
            crate::engine::RecordingHandle::with_sources(
                store.clone(),
                record.id.clone(),
                || anyhow::bail!("A closed meeting must not reopen inputs")
            )
            .is_err()
        );
    }
}
#[test]
fn drafts_and_private_answers_come_from_the_model_and_nothing_reaches_tico() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = transcript(&store, "Solo session");
    let chat = |content: &str| Some(json!({"choices":[{"message":{"content":content}}]}));
    let (model, seen, worker) = server(vec![
        chat(r#"{"items":[{"title":"Send the proposal","quote":"I will send the proposal"},
            {"title":"Ship on Friday","quote":"We ship on Friday"}]}"#),
        chat("You said you will send the proposal (00:00)."),
    ]);
    let config = Settings {
        transcription_url: model.destination.base_url.clone(),
        ..Default::default()
    };
    assert_eq!(crate::assist::draft(&store, &config, "key", &record.id).unwrap(), 1);
    // Nothing new to review: no request is made.
    assert_eq!(crate::assist::draft(&store, &config, "key", &record.id).unwrap(), 0);
    let answer = crate::assist::ask(&store, &config, "key", &record.id, "What did I commit to?").unwrap();
    assert!(answer.contains("send the proposal"));
    worker.join().unwrap();
    let requests = seen.lock().unwrap();
    assert_eq!(requests.len(), 2);
    assert!(requests.iter().all(|row| row.0.starts_with("POST /chat/completions ")));
    assert_eq!(requests[0].1["model"], crate::settings::ASSIST_MODEL);
    assert!(requests[0].1["messages"][1]["content"].as_str().unwrap().contains("I will send the proposal"));
    let context = store.context(&record.id).unwrap();
    assert!(context.live_id.is_empty() && context.live_destination.is_empty());
    assert_eq!(context.chat.len(), 2);
    // The invented quote was dropped; the real one is kept with its time.
    assert_eq!(context.items.len(), 1);
    assert_eq!(context.items[0].quote, "I will send the proposal");
    assert!(context.items[0].task_id.is_empty());
    assert_eq!(context.drafted_through_ms, 1000);
    assert!(store.get(&record.id).unwrap().deliveries.is_empty());
}
#[test]
fn an_action_item_becomes_one_private_task_carrying_the_transcript() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = transcript(&store, "Pricing call");
    let mut context = store.context(&record.id).unwrap();
    context.items.push(crate::workspace::Item {
        id: "item-fixture".into(),
        kind: "task".into(),
        title: "Send the proposal".into(),
        quote: "I will send the proposal".into(),
        ..Default::default()
    });
    store.save_context(&record.id, &context).unwrap();
    let who = json!({"actor":"human:test","name":"Chris"});
    let (tico, seen, worker) = server(vec![
        Some(who.clone()),
        Some(json!({"task":{"id":"task-fixture"}})),
        Some(who),
    ]);
    for _ in 0..2 {
        tico.send_task(&store, &record.id, "item-fixture", "bot:sales", "Sales Bot", true)
            .unwrap();
    }
    worker.join().unwrap();
    let requests = seen.lock().unwrap();
    let tasks: Vec<_> = requests.iter().filter(|row| row.0.starts_with("POST /api/v2/tasks ")).collect();
    assert_eq!(tasks.len(), 1);
    let body = &tasks[0].1;
    assert_eq!((body["owner"].as_str(), body["private"].as_bool()), (Some("bot:sales"), Some(true)));
    let text = body["body"].as_str().unwrap();
    assert!(text.contains("“I will send the proposal” — Me, 00:00"), "{text}");
    assert!(text.contains("Transcript around it:\n[00:00] Me: I will send the proposal"), "{text}");
    assert!(text.contains("sent by Chris from Tico Recorder"), "{text}");
    let context = store.context(&record.id).unwrap();
    let item = &context.items[0];
    assert_eq!((item.task_id.as_str(), item.sent_to.as_str()), ("task-fixture", "Sales Bot"));
    assert!(item.task_url.ends_with("/#/task/task-fixture"));
    // The meeting now belongs to this Tico: Send offers only it.
    assert_eq!(context.live_destination, tico.destination.id);
}
fn streamed(store: &Store, id: &str, channel: &str, start_ms: u64, text: &str, speaker: &str) {
    let words: Vec<Word> = text
        .split(' ')
        .enumerate()
        .map(|(index, word)| Word {
            start_ms: start_ms + index as u64 * 300,
            end_ms: start_ms + index as u64 * 300 + 250,
            text: word.into(),
            speaker: speaker.into(),
            turn: String::new(),
        })
        .collect();
    let end = words.last().map_or(start_ms, |word| word.end_ms);
    store.replace_live_words(id, channel, start_ms, end, &words).unwrap();
}
#[test]
fn items_drafted_from_streamed_text_survive_the_chunked_rewrite() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let record = store.create("Streamed drafting").unwrap();
    store.state(&record.id, "recording").unwrap();
    // Streamed text a few seconds old; no chunk is transcribed yet.
    streamed(&store, &record.id, "me", 1_000, "Okay I will send Dana the pricing deck tomorrow", "");
    assert!(!crate::assist::draft_due(&store, &record.id).unwrap(), "under 400 new characters");
    let long = "so the plan is that everyone reviews the numbers before the board meeting next week ".repeat(8);
    streamed(&store, &record.id, "them", 5_000, long.trim(), "S1");
    assert!(crate::assist::draft_due(&store, &record.id).unwrap());
    let chat = |content: &str| Some(json!({"choices":[{"message":{"content":content}}]}));
    let (model, seen, worker) = server(vec![chat(
        r#"{"items":[{"title":"Send Dana the pricing deck","quote":"I will send Dana the pricing deck"},
            {"title":"Ship on Friday","quote":"We ship on Friday"}]}"#,
    )]);
    let config = Settings {
        transcription_url: model.destination.base_url.clone(),
        ..Default::default()
    };
    assert_eq!(crate::assist::draft(&store, &config, "key", &record.id).unwrap(), 1);
    worker.join().unwrap();
    let sent = seen.lock().unwrap()[0].1["messages"][1]["content"].as_str().unwrap().to_owned();
    assert!(sent.contains("I will send Dana the pricing deck"));
    // The newest five seconds wait for the next review while recording.
    let through = store.context(&record.id).unwrap().drafted_through_ms;
    // 120 words from 5 s, the last ending at 40.95 s.
    assert!(through > 4_000 && through <= 40_950 - 5_000, "{through}");
    // The chunk for that time lands and words it differently.
    store
        .save_audio(&record.id, crate::audio::Channel::Microphone, 0, 4_500, &vec![0.2; 72_000], 16000)
        .unwrap();
    let claimed = store.claim_transcription().unwrap().unwrap();
    let words = ["Okay,", "I'll", "send", "Dana", "the", "pricing", "deck", "tomorrow."]
        .iter()
        .enumerate()
        .map(|(index, text)| Word {
            start_ms: 1_000 + index as u64 * 300,
            end_ms: 1_250 + index as u64 * 300,
            text: (*text).into(),
            ..Default::default()
        })
        .collect();
    store
        .transcription_words(&claimed, "Okay, I'll send Dana the pricing deck tomorrow.", words, "", json!({}))
        .unwrap();
    let record = store.get(&record.id).unwrap();
    let shown = crate::transcript::live_lines(&record, &store.live_words(&record.id).unwrap());
    assert_eq!(shown[0].text, "Okay, I'll send Dana the pricing deck tomorrow.");
    assert!(!shown.iter().any(|line| line.text.contains("I will send")));
    // The item stays, backed by the text it was drafted from; an invented quote is not.
    let context = store.context(&record.id).unwrap();
    assert_eq!(context.items.len(), 1);
    let item = &context.items[0];
    assert_eq!(item.quote, "I will send Dana the pricing deck");
    assert!(item.source.contains(&item.quote));
    assert!(crate::assist::quote_supported(item, &record));
    let invented = crate::workspace::Item {
        quote: "We ship on Friday".into(),
        ..item.clone()
    };
    assert!(!crate::assist::quote_supported(&invented, &record));
    let typed = crate::workspace::Item {
        quote: "We ship on Friday".into(),
        source: String::new(),
        ..item.clone()
    };
    assert!(!crate::assist::quote_supported(&typed, &record));
}
#[test]
fn streamed_text_never_reaches_tico() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let shared = transcript(&store, "Shared live");
    let mut context = store.context(&shared.id).unwrap();
    context.live_id = "live-fixture".into();
    context.live_destination = "synthetic".into();
    context.live_state = "live".into();
    store.save_context(&shared.id, &context).unwrap();
    let sent = transcript(&store, "Sent after the meeting");
    for id in [&shared.id, &sent.id] {
        // Streamed words inside the finished chunk and after it, where no chunk is done yet.
        streamed(&store, id, "me", 200, "streamedcovered words", "");
        streamed(&store, id, "me", 5_000, "streamedonly words", "");
        streamed(&store, id, "them", 6_000, "streamedfarend words", "S1");
    }
    let who = json!({"actor":"human:test"});
    let (tico, seen, worker) = server(vec![
        Some(json!({"accepted":[1]})),
        Some(json!({"id":"live-fixture"})),
        Some(who),
        Some(json!({"id":"meeting-fixture"})),
    ]);
    tico.sync_live(&store, &shared.id).unwrap();
    tico.send(&store, &sent.id, false).unwrap();
    worker.join().unwrap();
    let requests = seen.lock().unwrap();
    assert_eq!(requests.len(), 4);
    assert_eq!(requests[0].1["chunks"][0]["text"], "I will send the proposal");
    assert!(requests[3].1["transcript"].to_string().contains("I will send the proposal"));
    for (_, body, _) in requests.iter() {
        let body = body.to_string();
        assert!(!body.contains("streamed"), "streamed text reached Tico: {body}");
    }
}

fn tico_settings(id: &str, base_url: &str) -> Settings {
    Settings {
        destinations: vec![Destination {
            id: id.into(),
            name: "Acme".into(),
            base_url: base_url.into(),
            actor: "human:test".into(),
        }],
        ..Default::default()
    }
}
#[test]
fn speech_goes_through_the_tico_once_connected_unless_own_keys_were_already_set_up() {
    use crate::speech::{self, OWN, TICO};
    let mut config = Settings::default();
    assert_eq!(speech::source(&config), OWN, "no Tico yet");
    config = tico_settings("speech-source", "https://runner.acme.example");
    assert_eq!(speech::source(&config), TICO);
    config.speech_source = OWN.into();
    assert_eq!(speech::source(&config), OWN);
    config.speech_source = TICO.into();
    assert_eq!(
        speech::source(&tico_settings("x", "https://a.example")),
        TICO
    );
    // An install with keys of its own keeps them; one without follows the connection.
    let unset = tico_settings("speech-source", "https://runner.acme.example");
    assert_eq!(speech::settled(&unset, true), Some(OWN));
    assert_eq!(speech::settled(&unset, false), None);
    assert_eq!(speech::settled(&config, true), None, "a choice is kept");
    let dir = tempfile::tempdir().unwrap();
    // No settings file: a new install, nothing to keep and the keychain is not asked.
    assert!(!speech::settle(dir.path(), || panic!("keychain read")).unwrap());
    settings::write(dir.path(), &unset).unwrap();
    assert!(speech::settle(dir.path(), || true).unwrap());
    assert_eq!(settings::read(dir.path()).unwrap().speech_source, OWN);
    // Settled once: later launches never ask again.
    assert!(!speech::settle(dir.path(), || panic!("keychain read")).unwrap());
    // Older settings files have no field; anything else is refused.
    std::fs::write(
        dir.path().join("settings.json"),
        br#"{"transcription_model":"x"}"#,
    )
    .unwrap();
    assert_eq!(settings::read(dir.path()).unwrap().speech_source, "");
    let mut bad = unset.clone();
    bad.speech_source = "xai".into();
    assert!(settings::write(dir.path(), &bad).is_err());
}
#[test]
fn your_tico_transcribes_through_its_relay_with_the_person_token() {
    use crate::speech::{self, Services};
    let (tico, seen, worker) = server(vec![
        Some(json!({"transcribe":true,"assist":true,"stream":false})),
        Some(
            json!({"text":"Hello there","words":[{"word":"Hello","start":0.1,"end":0.4,"speaker":"speaker_0"}]}),
        ),
    ]);
    let config = Settings {
        transcription_url: "https://openrouter.ai/api/v1".into(),
        transcription_model: "my/custom-model".into(),
        destinations: vec![tico.destination.clone()],
        ..Default::default()
    };
    let services = speech::fetch(&tico.destination, "person-token").unwrap();
    assert_eq!(
        services,
        Services {
            transcribe: true,
            assist: true,
            stream: false
        }
    );
    speech::remember(&tico.destination, Ok(services));
    let (relay, token) = speech::transcription_with(&config, |id| {
        assert_eq!(id, "synthetic");
        Ok("person-token".into())
    })
    .unwrap();
    assert_eq!(token, "person-token");
    assert_eq!(
        relay.transcription_url,
        format!("{}/api/v2/recorder", tico.destination.base_url)
    );
    let (text, words) = transcribe_words(&relay, &token, b"RIFF", true).unwrap();
    worker.join().unwrap();
    assert_eq!(
        (text.as_str(), words[0].turn.as_str()),
        ("Hello there", "speaker_0")
    );
    let requests = seen.lock().unwrap();
    assert!(
        requests[0].0.starts_with("GET /api/v2/recorder/services "),
        "{}",
        requests[0].0
    );
    assert!(
        requests[1]
            .0
            .starts_with("POST /api/v2/recorder/audio/transcriptions ")
    );
    // Scribe v2 by name, so the Tico asks for word timings and speaker labels.
    assert_eq!(requests[1].1["model"], "elevenlabs/scribe-v2");
    assert_eq!(
        requests[1].1["provider"]["options"]["elevenlabs"]["diarize"],
        true
    );
    // A Tico that can't transcribe now says so; nothing is sent.
    speech::remember(&tico.destination, Ok(Services::default()));
    let refused = speech::transcription_with(&config, |_| panic!("no token needed")).unwrap_err();
    assert_eq!(
        refused.to_string(),
        "Your Tico can't transcribe right now. Audio is saved locally."
    );
    speech::remember(&tico.destination, Err("Tico request failed: Server returned HTTP 503 (unavailable). Check the connection or retry.".into()));
    let down = speech::transcription_with(&config, |_| panic!("no token needed")).unwrap_err();
    assert_eq!(
        down.to_string(),
        "Your Tico can't transcribe right now — HTTP 503"
    );
    let old = speech::plain(anyhow::anyhow!(OLD_TICO), "transcribe");
    assert_eq!(
        old.to_string(),
        "Your Tico can't transcribe yet. Update Tico, or use your own keys."
    );
}
#[test]
fn your_tico_streams_over_its_own_address() {
    let mut config = tico_settings("speech-stream", "https://runner.acme.example");
    let plan = crate::stream::Plan::from_settings(&config).unwrap();
    assert_eq!(plan.url, "wss://runner.acme.example/api/v2/recorder/stream");
    assert!(plan.tico.is_some() && plan.key.is_none() && !plan.required);
    assert!(
        crate::stream::enabled(&config),
        "on unless switched off; the Tico says if it can"
    );
    config.destinations[0].base_url = "http://127.0.0.1:8000".into();
    assert_eq!(
        crate::stream::Plan::from_settings(&config).unwrap().url,
        "ws://127.0.0.1:8000/api/v2/recorder/stream"
    );
    config.streaming = Some(false);
    assert!(crate::stream::Plan::from_settings(&config).is_none());
    // Own keys keep the xAI address.
    config.streaming = None;
    config.speech_source = crate::speech::OWN.into();
    let own = crate::stream::Plan::from_settings(&config).unwrap();
    assert_eq!(own.url, crate::stream::DEFAULT_URL);
    assert!(own.tico.is_none());
}
