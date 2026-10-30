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
