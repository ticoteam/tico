use super::*;
use crate::store::Store;
use serde_json::json;

fn interval(start_ms: u64, end_ms: u64, name: &str) -> Interval {
    Interval {
        start_ms,
        end_ms,
        name: name.into(),
    }
}
fn word(start_ms: u64, end_ms: u64, speaker: &str) -> Word {
    Word {
        start_ms,
        end_ms,
        text: "speech".into(),
        speaker: speaker.into(),
        ..Default::default()
    }
}
fn record() -> Recording {
    serde_json::from_value(
        json!({"id":"r","title":"Meeting","started_at":"","state":"stopped",
        "microphone":"","meeting_audio":true,"warning":"","chunks":[],"deliveries":[]}),
    )
    .unwrap()
}
fn chunk(channel: &str, words: Vec<Word>) -> crate::store::Chunk {
    serde_json::from_value(json!({"id":channel,"recording_id":"r","channel":channel,
        "speaker":if channel=="me" {"Me"} else {"Speaker 1"},"start_ms":0,"end_ms":10000,
        "file":"","signal":{"samples":1,"nonzero":1,"peak":0.5,"rms":0.5,"duration_ms":10000,"sample_rate":16000},
        "status":"done","text":"speech","error":"","attempts":1,"provider":{},"words":words})).unwrap()
}

/// The record as the store reads it back: names settled from the evidence (see `Store::get`).
fn named(record: &Recording, streamed: &BTreeMap<String, Vec<Word>>) -> Recording {
    let mut record = record.clone();
    record.speaker_names = vote(&record, streamed);
    record
}
/// What the saved transcript shows for each meeting-audio word.
fn shown(record: &Recording) -> Vec<String> {
    crate::transcript::chunk_words(&named(record, &BTreeMap::new()))["them"]
        .iter()
        .map(|w| w.speaker.clone())
        .collect()
}
fn voice(start_ms: u64, end_ms: u64, intervals: Vec<Interval>) -> Recording {
    let mut record = record();
    record.meeting_speakers.intervals = intervals;
    record
        .chunks
        .push(chunk("them", vec![word(start_ms, end_ms, "Speaker 2")]));
    record
}
#[test]
fn lag_allowance_and_gaps_use_capture_time_not_transcription_arrival() {
    let ana = || vec![interval(1300, 2300, "Ana Example")];
    // Meet shows speaking about a poll after the audio: 1000-2000 meets 1300-2300.
    assert_eq!(shown(&voice(1000, 2000, ana())), ["Ana Example"]);
    assert_eq!(shown(&voice(2100, 3100, ana())), ["Speaker 2"]);
    assert_eq!(shown(&voice(0, 1000, ana())), ["Speaker 2"]);
}
#[test]
fn overlap_needs_a_clear_duration_majority_and_local_names_are_excluded() {
    let mut record = voice(
        1000,
        2500,
        vec![
            interval(1300, 2800, "Ana Example"),
            interval(1300, 2800, "Ben Example"),
        ],
    );
    assert_eq!(shown(&record), ["Speaker 2"]);
    record
        .meeting_speakers
        .self_names
        .push("ana example".into());
    assert_eq!(shown(&record), ["Ben Example"]);
}
#[test]
fn missed_samples_and_no_speaker_close_the_evidence_instead_of_bridging_gaps() {
    let mut samples = Samples::default();
    let observed = speaking(&["Ana Example"]);
    assert!(samples.push(1000, observed.clone()).is_empty());
    assert_eq!(
        samples.push(1250, observed.clone()),
        [interval(1000, 1250, "Ana Example")]
    );
    assert!(samples.push(2500, observed).is_empty());
    assert_eq!(
        samples.push(2750, speaking(&[])),
        [interval(2500, 2750, "Ana Example")]
    );
    assert!(samples.push(3000, speaking(&[])).is_empty());
}
fn speaking(names: &[&str]) -> Observation {
    Observation {
        active: names.iter().map(|n| n.to_string()).collect(),
        indicators: true,
        ..Default::default()
    }
}
#[test]
fn short_silences_bridge_but_long_silence_unreadable_state_and_missed_polls_do_not() {
    let mut samples = Samples::default();
    let mut out = vec![];
    // Speaking, two silent polls (500 ms), speaking: one turn.
    for (at, observed) in [
        (0, speaking(&["Ana Example"])),
        (250, speaking(&[])),
        (500, speaking(&[])),
        (750, speaking(&["Ana Example"])),
        (1000, speaking(&[])),
        // 1 s of silence ends the turn.
        (1250, speaking(&[])),
        (1500, speaking(&[])),
        (1750, speaking(&[])),
        (2000, speaking(&["Ana Example"])),
        // Unreadable indicator: silence is unknown, nothing is bridged through it.
        (2250, Observation::default()),
        (2500, speaking(&["Ana Example"])),
        (2750, speaking(&[])),
        // A missed poll closes evidence too.
        (3000, speaking(&["Ana Example"])),
        (4000, speaking(&["Ana Example"])),
        (4250, speaking(&[])),
    ] {
        out.extend(samples.push(at, observed));
    }
    assert_eq!(
        coalesce(out),
        [
            interval(0, 1000, "Ana Example"),
            interval(2000, 2250, "Ana Example"),
            interval(2500, 2750, "Ana Example"),
            interval(4000, 4250, "Ana Example"),
        ]
    );
}
/// The store joins an interval that starts where the same name's last one ended.
fn coalesce(intervals: Vec<Interval>) -> Vec<Interval> {
    let mut out: Vec<Interval> = vec![];
    for next in intervals {
        match out.iter_mut().rev().find(|i| i.name == next.name) {
            Some(last) if last.end_ms == next.start_ms => last.end_ms = next.end_ms,
            _ => out.push(next),
        }
    }
    out
}
#[test]
fn self_identification_in_the_next_poll_prevents_mic_leakage() {
    let mut samples = Samples::default();
    samples.push(1000, speaking(&["Casey Example"]));
    assert!(
        samples
            .push(
                1250,
                Observation {
                    self_names: vec!["Casey Example".into()],
                    indicators: true,
                    ..Default::default()
                }
            )
            .is_empty()
    );
}
#[test]
fn voice_cluster_uses_duration_votes_for_short_turns_overlap_and_gaps() {
    let mut record = record();
    record.meeting_speakers.intervals = vec![
        interval(300, 2300, "Ana Example"),
        interval(3300, 3500, "Ben Example"),
    ];
    record.chunks.push(chunk(
        "them",
        vec![
            word(0, 2000, "Speaker 2"),
            word(3000, 3200, "Speaker 2"),
            word(6000, 6200, "Speaker 2"),
        ],
    ));
    let mut local_word = word(0, 2000, "");
    local_word.text = "local".into();
    record.chunks.push(chunk("me", vec![local_word]));
    assert!(shown(&record).iter().all(|s| s == "Ana Example"));
    assert_eq!(
        crate::transcript::lines(&record)
            .iter()
            .filter(|line| line.me)
            .map(|line| line.speaker.as_str())
            .collect::<Vec<_>>(),
        ["Me"]
    );
    assert_eq!(
        record.chunks[0].words[0].speaker, "Speaker 2",
        "voice evidence stays intact"
    );
}
#[test]
fn stream_and_offline_voices_keep_their_own_labels_and_names() {
    let mut record = record();
    record.state = "recording".into();
    record.meeting_speakers.intervals = vec![
        interval(300, 1300, "Ana Example"),
        interval(2300, 3300, "Ben Example"),
    ];
    let mut offline = chunk("them", vec![word(0, 1000, "Speaker 1")]);
    offline.end_ms = 1000;
    record.chunks.push(offline);
    // The streaming service numbers its voices separately from the offline diarizer.
    let streamed = BTreeMap::from([(
        "them".into(),
        vec![word(2000, 3000, "stream:S1"), word(3200, 3300, "stream:S1")],
    )]);
    let record = named(&record, &streamed);
    assert_eq!(name_of(&record, "Speaker 1"), "Ana Example");
    assert_eq!(name_of(&record, "Speaker 2"), "Ben Example");
    let live: Vec<_> = crate::transcript::live_turns(&record, &streamed, &[])
        .into_iter()
        .map(|turn| turn.speaker)
        .collect();
    assert_eq!(live, ["Ana Example", "Ben Example"]);
}
#[test]
fn one_stable_label_pools_its_stream_and_offline_evidence_into_one_name() {
    let mut record = record();
    record.state = "recording".into();
    record.meeting_speakers.intervals = vec![
        interval(300, 900, "Ana Example"),
        interval(5300, 5900, "Ana Example"),
    ];
    // Recorder #2681: the stream voice and the offline voice were linked to one label.
    record.speaker_labels = BTreeMap::from([
        ("stream:S4".into(), "Speaker 2".into()),
        ("Speaker 7".into(), "Speaker 2".into()),
    ]);
    let mut offline = chunk("them", vec![word(0, 600, "Speaker 7")]);
    offline.end_ms = 1000;
    record.chunks.push(offline);
    let streamed = BTreeMap::from([("them".into(), vec![word(5000, 5600, "stream:S4")])]);
    // 0.6 s each: neither is enough alone, together they name the label.
    assert_eq!(shown(&record), ["Speaker 2"]);
    let record = named(&record, &streamed);
    assert_eq!(name_of(&record, "Speaker 2"), "Ana Example");
    let live: Vec<_> = crate::transcript::live_turns(&record, &streamed, &[])
        .into_iter()
        .map(|turn| turn.speaker)
        .collect();
    assert_eq!(live, ["Ana Example", "Ana Example"]);
}
#[test]
fn each_voice_gets_one_name_only_after_enough_clear_evidence() {
    let mut record = record();
    record.meeting_speakers.intervals = vec![
        // Speaker 1: 2.4 s of Ana, 0.4 s of Ben (noise or overlap).
        interval(300, 2700, "Ana Example"),
        interval(5300, 5700, "Ben Example"),
        // Speaker 2: 1.6 s of Ben.
        interval(3300, 4900, "Ben Example"),
        // Speaker 3: only 0.6 s of anything.
        interval(8300, 8900, "Ana Example"),
        // Speaker 4: 1.2 s Ana vs 1 s Ben is not a two-thirds majority.
        interval(10_300, 11_500, "Ana Example"),
        interval(11_500, 12_500, "Ben Example"),
    ];
    record.chunks.push(chunk(
        "them",
        vec![
            word(0, 2400, "Speaker 1"),
            word(5000, 5400, "Speaker 1"),
            word(7000, 7200, "Speaker 1"),
            word(3000, 4600, "Speaker 2"),
            word(8000, 8600, "Speaker 3"),
            word(10_000, 12_200, "Speaker 4"),
        ],
    ));
    assert_eq!(
        shown(&record),
        [
            "Ana Example",
            "Ana Example",
            "Ana Example",
            "Ben Example",
            "Speaker 3",
            "Speaker 4"
        ]
    );
}
#[test]
fn a_tied_voice_cluster_stays_anonymous_instead_of_switching_identity_per_word() {
    let mut record = record();
    record.meeting_speakers.intervals = vec![
        interval(300, 1300, "Ana Example"),
        interval(2300, 3300, "Ben Example"),
    ];
    record.chunks.push(chunk(
        "them",
        vec![word(0, 1000, "Speaker 2"), word(2000, 3000, "Speaker 2")],
    ));
    assert!(shown(&record).iter().all(|s| s == "Speaker 2"));
}
#[test]
fn untimed_utterances_get_names_and_manual_edits_keep_the_chosen_speaker() {
    let mut record = record();
    record.meeting_speakers.intervals = vec![interval(300, 10_300, "Ana Example")];
    record.chunks.push(chunk("them", vec![]));
    assert_eq!(shown(&record), ["Ana Example"]);
    // An edit is no evidence: its chunk alone names nobody.
    record.chunks[0].provider = json!({"source":"edit"});
    assert_eq!(shown(&record), ["Speaker 1"]);
    // A speaker a person typed is never renamed.
    record.chunks[0].provider = json!({});
    record.chunks[0].speaker = "Chosen speaker".into();
    assert_eq!(shown(&record), ["Chosen speaker"]);
}
fn row(name: &str, extra: Vec<serde_json::Value>, indicator: &[&str]) -> serde_json::Value {
    let mut children = vec![json!({"role":"AXStaticText","label":name})];
    children.extend(extra);
    children.push(json!({"role":"AXGroup","classes":["I118Fc"],"children":[
        {"role":"AXGroup","classes":["VeFZv"],"children":[
            {"role":"AXGroup","classes":indicator}]}]}));
    json!({"role":"AXGroup","classes":["KV1GEc","cxdMu","other"],"description":name,"children":children})
}
fn panel(rows: Vec<serde_json::Value>) -> meet::Node {
    serde_json::from_value(
        json!({"role":"AXGroup","description":"Side panel","children":[
        {"role":"AXList","description":"Participants","children":rows},
        {"role":"AXGroup","label":"In-call messages","children":[
            row("Chat Person", vec![], &["IisKdb", "OgVli"])]}]}),
    )
    .unwrap()
}
#[test]
fn meet_rows_name_remote_speakers_and_never_the_local_person_or_an_unknown_state() {
    let root = panel(vec![
        row(
            "Casey Example",
            vec![json!({"role":"AXStaticText","label":"(You)"})],
            &["IisKdb", "wEsLMd"],
        ),
        row("Ana Example", vec![], &["IisKdb", "HX2H7", "x9nQ6"]),
        row("Ben Example", vec![], &["IisKdb", "gjg47c"]),
        // A new Meet state class is not proof of speech.
        row("Dee Example", vec![], &["IisKdb", "Zz9new"]),
        row("3 others", vec![], &["IisKdb", "OgVli"]),
    ]);
    let rows = meet::rows(&root);
    assert_eq!(
        rows.iter()
            .map(|r| (r.name.as_str(), r.local, r.speaking))
            .collect::<Vec<_>>(),
        [
            ("Casey Example", true, Some(true)),
            ("Ana Example", false, Some(true)),
            ("Ben Example", false, Some(false)),
            ("Dee Example", false, None),
        ]
    );
    let observed = meet::GoogleMeet.observe(&root);
    assert_eq!(observed.active, ["Ana Example"]);
    assert_eq!(observed.self_names, ["Casey Example"]);
    assert!(observed.indicators);
    assert!(!observed.participants.iter().any(|p| p.contains("Chat")));
}
#[test]
fn without_the_row_structure_nothing_is_observed() {
    for fixture in [
        include_str!("../../tests/fixtures/meet-ax/chrome-ended.json"),
        r#"{"role":"AXGroup","children":[{"role":"AXGroup","description":"Ana Example","classes":["IisKdb","OgVli"]}]}"#,
    ] {
        let root: meet::Node = serde_json::from_str(fixture).unwrap();
        let observed = meet::GoogleMeet.observe(&root);
        assert!(observed.active.is_empty());
        assert!(!observed.indicators);
    }
}
/// Captured live in Chrome: 591 polls 250 ms apart, reduced to People-panel rows (deduplicated
/// subtrees) and the poll times. One account was signed in on two devices, so both rows share
/// one name; only the first row ever showed a level indicator.
#[derive(serde::Deserialize)]
struct Capture {
    polls: Vec<u64>,
    trees: Vec<serde_json::Value>,
    frames: Vec<Frame>,
}
#[derive(serde::Deserialize)]
struct Frame {
    t: u64,
    rows: Vec<usize>,
}
fn replay(edit: impl Fn(&mut serde_json::Value)) -> (Vec<Observation>, Vec<Interval>) {
    let capture: Capture = serde_json::from_str(include_str!(
        "../../tests/fixtures/meet-ax/chrome-live-rows.json"
    ))
    .unwrap();
    let mut trees = capture.trees.clone();
    trees.iter_mut().for_each(edit);
    let (mut samples, mut observed, mut intervals, mut frame) =
        (Samples::default(), vec![], vec![], 0);
    for at in capture.polls {
        while capture.frames.get(frame + 1).is_some_and(|f| f.t <= at) {
            frame += 1;
        }
        let rows: Vec<_> = capture.frames[frame]
            .rows
            .iter()
            .map(|i| trees[*i].clone())
            .collect();
        let root: meet::Node =
            serde_json::from_value(json!({"role":"AXList","children":rows})).unwrap();
        let observation = meet::GoogleMeet.observe(&root);
        intervals.extend(samples.push(at, observation.clone()));
        observed.push(observation);
    }
    (observed, coalesce(intervals))
}
/// The captured rows with a mute control (the other device's) become a second, remote-looking
/// person marked "(You)", so the indicator row is the remote speaker.
fn as_remote(tree: &mut serde_json::Value) {
    fn button(node: &serde_json::Value) -> bool {
        node["role"] == "AXButton"
            || node["children"]
                .as_array()
                .is_some_and(|c| c.iter().any(button))
    }
    if !button(tree) {
        return;
    }
    tree["description"] = json!("Participant 2");
    let children = tree["children"].as_array_mut().unwrap();
    for child in children.iter_mut() {
        if child["label"] == "Participant 1" {
            child["label"] = json!("Participant 2");
        }
    }
    children.push(json!({"role":"AXStaticText","label":"(You)"}));
}
#[test]
fn captured_chrome_rows_yield_the_live_speech_bursts() {
    let (observed, intervals) = replay(as_remote);
    assert!(observed.iter().all(|o| {
        o.participants
            .iter()
            .all(|p| p == "Participant 1" || p == "Participant 2")
    }));
    // Rows are missing until the panel opens and during three relayouts, and a lone row
    // cannot say whose it is.
    assert_eq!(observed.iter().filter(|o| !o.indicators).count(), 157);
    let spans: Vec<_> = intervals
        .iter()
        .map(|i| {
            assert_eq!(i.name, "Participant 1");
            (i.start_ms, i.end_ms)
        })
        .collect();
    assert_eq!(
        spans,
        [
            (51456, 51710),
            (61334, 66148),
            (67915, 68169),
            (69191, 70710),
            (93783, 99605),
            // The empty tree at 100031 ms is not bridged.
            (100122, 102143),
            (115826, 116080),
            (137862, 139449),
            (143114, 143609),
            (146149, 147405),
        ]
    );
}
#[test]
fn captured_rows_where_the_speaking_row_is_the_local_person_yield_nothing() {
    // As captured, the indicator row is the one without a mute control: this Mac's own row,
    // in whatever language Meet shows. Its bursts are the mic's.
    let (observed, intervals) = replay(|_| {});
    assert!(intervals.is_empty());
    assert!(observed.iter().any(|o| o.self_names == ["Participant 1"]));
    assert!(observed.iter().all(|o| o.active.is_empty()));
}
#[test]
fn meet_detection_rejects_landing_pages_other_sites_and_unrelated_browser_documents() {
    for url in [
        "https://meet.google.com/abc-defg-hij",
        "https://meet.google.com/meet/abc-defg-hij?authuser=0",
    ] {
        assert!(meet::GoogleMeet.detect_window("", url));
    }
    for url in [
        "https://meet.google.com/",
        "https://meet.google.com/landing",
        "https://meet.google.com.evil.example/abc-defg-hij",
        "https://acme.example/abc-defg-hij",
        "http://meet.google.com/abc-defg-hij",
    ] {
        assert!(!meet::GoogleMeet.detect_window("Google Meet", url));
    }
}
#[test]
fn captured_chrome_tree_exposes_names_but_visual_classes_do_not_invent_speech() {
    let root: meet::Node = serde_json::from_str(include_str!(
        "../../tests/fixtures/meet-ax/chrome-observed.json"
    ))
    .unwrap();
    let observed = meet::GoogleMeet.observe(&root);
    assert!(!observed.participants.is_empty());
    assert!(
        observed
            .participants
            .iter()
            .all(|name| name.starts_with("Participant "))
    );
    assert!(observed.active.is_empty());
}
#[test]
fn old_library_migration_is_repeatable_keeps_audio_and_cascades_local_identity_data() {
    let dir = tempfile::tempdir().unwrap();
    let old = rusqlite::Connection::open(dir.path().join("recorder.sqlite")).unwrap();
    old.execute_batch("CREATE TABLE recordings(id TEXT PRIMARY KEY,document TEXT NOT NULL);")
        .unwrap();
    let mut record = record();
    record.id = uuid::Uuid::new_v4().to_string();
    old.execute(
        "INSERT INTO recordings VALUES(?,?)",
        rusqlite::params![record.id, serde_json::to_string(&record).unwrap()],
    )
    .unwrap();
    drop(old);
    let store = Store::open(dir.path()).unwrap();
    assert!(
        store
            .get(&record.id)
            .unwrap()
            .meeting_speakers
            .intervals
            .is_empty()
    );
    let people = [
        ("Ana Example".to_string(), false),
        ("Casey Example".to_string(), true),
    ];
    store
        .observe_speakers(
            &record.id,
            &people,
            &[interval(1000, 1250, "Ana Example")],
            &[(1000, 1250)],
        )
        .unwrap();
    store
        .observe_speakers(
            &record.id,
            &[],
            &[interval(1250, 1500, "Ana Example")],
            &[(1250, 1500)],
        )
        .unwrap();
    store
        .mutate(&record.id, |r| {
            r.title = "Renamed".into();
            Ok(())
        })
        .unwrap();
    let reopened = Store::open(dir.path()).unwrap();
    let timeline = reopened.get(&record.id).unwrap().meeting_speakers;
    assert_eq!(timeline.intervals, [interval(1000, 1500, "Ana Example")]);
    assert_eq!(timeline.readable, [(1000, 1500)]);
    assert_eq!(timeline.self_names, ["Casey Example"]);
    assert_eq!(
        reopened.context(&record.id).unwrap().participants,
        ["Ana Example", "Casey Example"]
    );
    assert_eq!(reopened.get(&record.id).unwrap().title, "Renamed");
    reopened
        .db()
        .unwrap()
        .execute("DELETE FROM recordings WHERE id=?", [&record.id])
        .unwrap();
    assert!(
        reopened
            .meeting_speakers(&record.id)
            .unwrap()
            .intervals
            .is_empty()
    );
    assert!(
        reopened
            .meeting_speakers(&record.id)
            .unwrap()
            .participants
            .is_empty()
    );
}
/// One second of speech per word, for `seconds` from `start_ms`.
fn talk(start_ms: u64, seconds: u64, speaker: &str) -> Vec<Word> {
    (0..seconds)
        .map(|i| word(start_ms + i * 1000, start_ms + (i + 1) * 1000, speaker))
        .collect()
}
#[test]
fn a_long_talker_is_not_named_after_someone_elses_short_replies() {
    let mut record = record();
    // Speaker 2 talks for 20 s; Ana's indicator shows two 0.75 s "mm-hmm"s meanwhile. The
    // talker's own row never shows a reading (a phone, or a row out of view).
    record.meeting_speakers.intervals = vec![
        interval(5300, 6050, "Ana Example"),
        interval(12_300, 13_050, "Ana Example"),
    ];
    record.meeting_speakers.readable = vec![(0, 25_000)];
    record.chunks.push(chunk("them", talk(0, 20, "Speaker 2")));
    assert!(shown(&record).iter().all(|s| s == "Speaker 2"));
    // Without readable spans (a library from before they were kept) the old rule applies.
    record.meeting_speakers.readable.clear();
    assert!(shown(&record).iter().all(|s| s == "Ana Example"));
}
#[test]
fn a_trailing_indicator_at_turn_handoffs_does_not_name_the_next_voice() {
    let mut record = record();
    let mut words = vec![];
    let mut intervals = vec![];
    // Ana and Speaker 2 alternate 3 s turns. Ana's indicator trails 0.45 s into each of
    // Speaker 2's turns; Speaker 2's own row shows nothing.
    for turn in 0..4u64 {
        let ana = turn * 6000;
        words.extend(talk(ana, 3, "Speaker 1"));
        intervals.push(interval(ana + 300, ana + 3300 + 450, "Ana Example"));
        words.extend(talk(ana + 3000, 3, "Speaker 2"));
    }
    record.meeting_speakers.intervals = intervals;
    record.meeting_speakers.readable = vec![(0, 30_000)];
    record.chunks.push(chunk("them", words));
    let shown = shown(&record);
    let named: BTreeSet<_> = shown.iter().map(String::as_str).collect();
    assert_eq!(named, BTreeSet::from(["Ana Example", "Speaker 2"]));
    assert_eq!(shown.iter().filter(|s| *s == "Ana Example").count(), 12);
}
#[test]
fn names_stick_until_another_name_clearly_wins_and_never_go_back_to_a_number() {
    let mut record = voice(0, 3000, vec![]);
    record.speaker_names = BTreeMap::from([("Speaker 2".into(), "Ana Example".into())]);
    // No evidence at all, then a tie: the name stays.
    assert_eq!(vote(&record, &BTreeMap::new())["Speaker 2"], "Ana Example");
    record.meeting_speakers.intervals = vec![
        interval(300, 1800, "Ana Example"),
        interval(300, 1800, "Ben Example"),
    ];
    assert_eq!(vote(&record, &BTreeMap::new())["Speaker 2"], "Ana Example");
    // Another name wins outright: the voice moves to it.
    record.meeting_speakers.intervals = vec![interval(300, 3300, "Ben Example")];
    assert_eq!(vote(&record, &BTreeMap::new())["Speaker 2"], "Ben Example");
    // A name later known to be this Mac's person is dropped.
    record.meeting_speakers.self_names = vec!["Ana Example".into()];
    record.meeting_speakers.intervals.clear();
    assert!(vote(&record, &BTreeMap::new()).is_empty());
}
#[test]
fn the_store_keeps_a_settled_name_when_later_evidence_is_gone() {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path()).unwrap();
    let id = store.create("Meeting").unwrap().id;
    let mut said = chunk("them", vec![word(0, 2000, "Speaker 2")]);
    said.recording_id = id.clone();
    store
        .db()
        .unwrap()
        .execute(
            "INSERT INTO chunks(id,recording_id,status,start_ms,document) VALUES(?,?,?,?,?)",
            rusqlite::params![
                said.id,
                id,
                said.status,
                0,
                serde_json::to_string(&said).unwrap()
            ],
        )
        .unwrap();
    store
        .observe_speakers(
            &id,
            &[("Ana Example".into(), false)],
            &[interval(300, 2300, "Ana Example")],
            &[(0, 3000)],
        )
        .unwrap();
    // The store numbers voices itself (#2681); the name sits on whatever label it chose.
    let first = store.get(&id).unwrap();
    assert_eq!(
        first.speaker_names.values().collect::<Vec<_>>(),
        ["Ana Example"]
    );
    store
        .db()
        .unwrap()
        .execute("DELETE FROM meeting_speaker_intervals", [])
        .unwrap();
    let record = store.get(&id).unwrap();
    assert_eq!(record.speaker_names, first.speaker_names);
    assert_eq!(
        crate::transcript::chunk_words(&record)["them"][0].speaker,
        "Ana Example"
    );
}
fn button() -> serde_json::Value {
    json!({"role":"AXGroup","children":[{"role":"AXButton","description":"Mute"}]})
}
#[test]
fn without_a_known_local_row_no_one_is_speaking() {
    // Meet in another language: no "(You)" text, and every row has a control.
    let root = panel(vec![
        row("Casey Example", vec![button()], &["IisKdb", "wEsLMd"]),
        row("Ana Example", vec![button()], &["IisKdb", "HX2H7"]),
    ]);
    let observed = meet::GoogleMeet.observe(&root);
    assert!(observed.active.is_empty());
    assert!(!observed.indicators);
    assert_eq!(observed.participants, ["Ana Example", "Casey Example"]);
    // A lone row cannot say whose it is either.
    let observed = meet::GoogleMeet.observe(&panel(vec![row(
        "Ana Example",
        vec![],
        &["IisKdb", "HX2H7"],
    )]));
    assert!(observed.active.is_empty());
    assert!(!observed.indicators);
}
#[test]
fn the_one_row_without_a_mute_control_is_the_local_person_in_any_language() {
    let root = panel(vec![
        row("Casey Example", vec![], &["IisKdb", "wEsLMd"]),
        row("Ana Example", vec![button()], &["IisKdb", "HX2H7"]),
        row("Ben Example", vec![button()], &["IisKdb", "gjg47c"]),
    ]);
    let observed = meet::GoogleMeet.observe(&root);
    assert_eq!(observed.active, ["Ana Example"]);
    assert_eq!(observed.self_names, ["Casey Example"]);
    assert!(observed.indicators);
}
#[test]
fn readable_polls_are_recorded_and_unreadable_or_missed_ones_are_not() {
    let mut samples = Samples::default();
    samples.push(1000, speaking(&[]));
    assert_eq!(samples.readable, None);
    samples.push(1250, Observation::default());
    assert_eq!(samples.readable, Some((1000, 1250)));
    samples.push(1500, speaking(&[]));
    assert_eq!(samples.readable, None, "the previous poll was unreadable");
    samples.push(3000, speaking(&[]));
    assert_eq!(samples.readable, None, "a missed poll");
    samples.push(3250, speaking(&[]));
    assert_eq!(samples.readable, Some((3000, 3250)));
}
#[test]
fn a_batch_joins_contiguous_evidence_before_writing() {
    let mut batch = Batch::default();
    assert!(batch.is_empty());
    batch.add(vec![interval(0, 250, "Ana Example")], Some((0, 250)));
    batch.add(
        vec![
            interval(250, 500, "Ana Example"),
            interval(250, 500, "Ben Example"),
        ],
        Some((250, 500)),
    );
    batch.add(
        vec![interval(1500, 1750, "Ana Example")],
        Some((1500, 1750)),
    );
    assert_eq!(
        batch.intervals,
        [
            interval(0, 500, "Ana Example"),
            interval(250, 500, "Ben Example"),
            interval(1500, 1750, "Ana Example"),
        ]
    );
    assert_eq!(batch.readable, [(0, 500), (1500, 1750)]);
}
