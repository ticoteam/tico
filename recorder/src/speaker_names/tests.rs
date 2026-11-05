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

/// What the saved transcript shows for each meeting-audio word.
fn shown(record: &Recording) -> Vec<String> {
    crate::transcript::chunk_words(record)["them"]
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
    let names = Names::new(&record, &streamed);
    assert_eq!(names.get("Speaker 1"), "Ana Example");
    assert_eq!(names.get("Speaker 2"), "Ben Example");
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
    let names = Names::new(&record, &streamed);
    assert_eq!(names.get("Speaker 2"), "Ana Example");
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
#[test]
fn captured_chrome_rows_yield_the_live_speech_bursts() {
    let (observed, intervals) = replay(|_| {});
    assert!(
        observed
            .iter()
            .all(|o| o.participants.iter().all(|p| p == "Participant 1"))
    );
    // Rows are missing until the panel opens and during three relayouts.
    assert_eq!(observed.iter().filter(|o| !o.indicators).count(), 149);
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
fn captured_rows_marked_as_the_local_person_yield_nothing() {
    // The captured row with the indicator carries an anonymized second text ("P38") where Meet
    // shows "(You)" on the local person's row. With that marker, the bursts are the mic's.
    fn mark(node: &mut serde_json::Value) {
        if node["label"] == "P38" {
            node["label"] = json!("(You)");
        }
        if let Some(children) = node.get_mut("children").and_then(|c| c.as_array_mut()) {
            children.iter_mut().for_each(mark);
        }
    }
    let (observed, intervals) = replay(mark);
    assert!(intervals.is_empty());
    assert!(observed.iter().any(|o| o.self_names == ["Participant 1"]));
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
    let observed = Observation {
        participants: vec!["Ana Example".into(), "Casey Example".into()],
        self_names: vec!["Casey Example".into()],
        active: vec!["Ana Example".into()],
        indicators: true,
    };
    store
        .observe_speakers(
            &record.id,
            &observed,
            &[interval(1000, 1250, "Ana Example")],
        )
        .unwrap();
    store
        .observe_speakers(
            &record.id,
            &observed,
            &[interval(1250, 1500, "Ana Example")],
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
