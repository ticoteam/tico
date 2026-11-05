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

#[test]
fn lag_allowance_and_gaps_use_capture_time_not_transcription_arrival() {
    let timeline = Timeline {
        intervals: vec![interval(1300, 2300, "Ana Example")],
        ..Default::default()
    };
    assert_eq!(timeline.at(1000, 1200).as_deref(), Some("Ana Example"));
    assert_eq!(timeline.at(2100, 2200), None);
    assert_eq!(timeline.at(3000, 3500), None);
    assert_eq!(timeline.at(0, 500), None);
}
#[test]
fn overlap_needs_a_clear_duration_majority_and_local_names_are_excluded() {
    let mut timeline = Timeline {
        intervals: vec![
            interval(1300, 2300, "Ana Example"),
            interval(1800, 2800, "Ben Example"),
        ],
        ..Default::default()
    };
    assert_eq!(timeline.at(1500, 2000), None);
    assert_eq!(timeline.at(1000, 1600).as_deref(), Some("Ana Example"));
    timeline.self_names.push("ana example".into());
    assert_eq!(timeline.at(1000, 1400), None);
    assert_eq!(timeline.at(1500, 2000).as_deref(), Some("Ben Example"));
}
#[test]
fn missed_samples_and_no_speaker_close_the_evidence_instead_of_bridging_gaps() {
    let mut samples = Samples::default();
    let observed = Observation {
        active: vec!["Ana Example".into()],
        ..Default::default()
    };
    assert!(samples.push(1000, observed.clone()).is_empty());
    assert_eq!(
        samples.push(1250, observed.clone()),
        [interval(1000, 1250, "Ana Example")]
    );
    assert!(samples.push(2500, observed).is_empty());
    assert_eq!(
        samples.push(2750, Observation::default()),
        [interval(2500, 2750, "Ana Example")]
    );
    assert!(samples.push(3000, Observation::default()).is_empty());
}
#[test]
fn self_identification_in_the_next_poll_prevents_mic_leakage() {
    let mut samples = Samples::default();
    samples.push(
        1000,
        Observation {
            active: vec!["Casey Example".into()],
            ..Default::default()
        },
    );
    assert!(
        samples
            .push(
                1250,
                Observation {
                    self_names: vec!["Casey Example".into()],
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
    let named = named(&record);
    assert!(
        named.chunks[0]
            .words
            .iter()
            .all(|word| word.speaker == "Ana Example")
    );
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
fn provisional_stream_voices_are_scoped_away_from_offline_voice_numbers() {
    let mut record = record();
    record.state = "recording".into();
    record.meeting_speakers.intervals = vec![
        interval(300, 1300, "Ana Example"),
        interval(2300, 3300, "Ben Example"),
    ];
    let mut offline = chunk("them", vec![word(0, 1000, "Speaker 1")]);
    offline.end_ms = 1000;
    record.chunks.push(offline);
    let mut streamed = BTreeMap::from([(
        "them".into(),
        vec![word(2000, 3000, "Speaker 1"), word(5000, 5100, "Speaker 1")],
    )]);
    let names = Names::new(&record, &streamed);
    names.streamed("them", streamed.get_mut("them").unwrap());
    assert!(
        streamed["them"]
            .iter()
            .all(|word| word.speaker == "Ben Example")
    );
    assert_eq!(named(&record).chunks[0].words[0].speaker, "Ana Example");
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
    assert!(
        named(&record).chunks[0]
            .words
            .iter()
            .all(|word| word.speaker == "Speaker 2")
    );
}
#[test]
fn untimed_utterances_get_names_and_manual_edits_keep_the_chosen_speaker() {
    let mut record = record();
    record.meeting_speakers.intervals = vec![interval(300, 10_300, "Ana Example")];
    record.chunks.push(chunk("them", vec![]));
    assert_eq!(named(&record).chunks[0].speaker, "Ana Example");
    assert_eq!(
        crate::transcript::chunk_words(&record)["them"][0].speaker,
        "Ana Example"
    );
    record.chunks[0].provider = json!({"source":"edit"});
    record.chunks[0].speaker = "Chosen speaker".into();
    assert_eq!(named(&record).chunks[0].speaker, "Chosen speaker");
}
#[test]
fn meet_layout_contracts_preserve_names_and_never_name_the_others_tile_or_pinned_tile_as_speaker() {
    let fixtures = [
        (
            include_str!("../../tests/fixtures/meet-ax/gallery.json"),
            vec!["Ana Example"],
            vec!["Casey Example"],
        ),
        (
            include_str!("../../tests/fixtures/meet-ax/presenting.json"),
            vec!["Ben Example"],
            vec![],
        ),
        (
            include_str!("../../tests/fixtures/meet-ax/others.json"),
            vec!["Ben Example"],
            vec!["Casey Example"],
        ),
    ];
    for (fixture, active, locals) in fixtures {
        let root: meet::Node = serde_json::from_str(fixture).unwrap();
        let observed = meet::GoogleMeet.observe(&root);
        assert_eq!(observed.active, active);
        assert_eq!(observed.self_names, locals);
        assert!(
            observed
                .participants
                .iter()
                .all(|p| !p.contains("others") && !p.contains("Private"))
        );
    }
    let silent:meet::Node=serde_json::from_value(json!({"role":"AXGroup","label":"Pinned","children":[{"role":"AXButton","label":"More options for Ana Example"}]})).unwrap();
    assert!(meet::GoogleMeet.read_active_speakers(&silent).is_empty());
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
