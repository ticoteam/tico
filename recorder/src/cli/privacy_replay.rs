//! Synthetic acceptance driver: production Store and Tico transport, no capture or providers.
use anyhow::{Result, ensure};
use serde_json::json;
use tico_recorder_native::{
    audio::Channel,
    network::Tico,
    settings::Destination,
    store::{Store, Word},
    workspace::Item,
};

fn word(at: u64, text: &str) -> Word {
    Word {
        start_ms: at,
        end_ms: at + 500,
        text: text.into(),
        speaker: String::new(),
        turn: String::new(),
    }
}

pub fn run(store: &Store, base: &str, scenario: &str) -> Result<()> {
    ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Use an isolated library"
    );
    let url = reqwest::Url::parse(base)?;
    ensure!(
        url.host_str() == Some("127.0.0.1"),
        "Replay only supports loopback"
    );
    ensure!(
        [
            "local",
            "remote",
            "discard",
            "lost-ack",
            "sequence-conflict",
            "pause-ack",
            "discard-ack"
        ]
        .contains(&scenario),
        "Unknown replay"
    );
    let tico = Tico::new(
        Destination {
            id: "privacy-fixture".into(),
            name: "Acme fixture".into(),
            base_url: base.into(),
            actor: String::new(),
        },
        std::env::var("TICO_REPLAY_TEST_TOKEN")?,
    )?;
    let record = store.create("Privacy replay synthetic")?;
    let id = &record.id;
    store.state(id, "recording")?;
    let live_id = tico.ensure_live(store, id, false)?;
    store.replace_live_words(id, "me", 0, 1000, &[word(0, "Public opening")])?;
    if scenario == "lost-ack" {
        ensure!(
            tico.push_live(store, id).is_err(),
            "Fixture must lose a committed acknowledgement"
        );
    }
    tico.push_live(store, id)?;
    if scenario == "sequence-conflict" {
        // A restored local row disagrees with an already committed server sequence.
        // Reject it without acknowledging/filing; restoring the original row permits safe retry.
        let db = store.db()?;
        let payload: String = db.query_row(
            "SELECT payload_json FROM live_outbox WHERE recording_id=? AND seq=1",
            [id],
            |r| r.get(0),
        )?;
        let mut changed: serde_json::Value = serde_json::from_str(&payload)?;
        changed["text"] = json!("Conflicting synthetic retry");
        db.execute(
            "UPDATE live_outbox SET acknowledged=0,payload_json=? WHERE recording_id=? AND seq=1",
            [&serde_json::to_string(&changed)?, id],
        )?;
        drop(db);
        let error = tico
            .push_live(store, id)
            .expect_err("Real server must reject sequence mismatch");
        ensure!(
            format!("{error:#}").contains("sequence_conflict"),
            "Expected actual sequence conflict: {error:#}"
        );
        ensure!(
            store.get(id)?.deliveries.is_empty(),
            "Conflicting retry cannot file"
        );
        store.db()?.execute(
            "UPDATE live_outbox SET payload_json=? WHERE recording_id=? AND seq=1",
            [&payload, id],
        )?;
        tico.push_live(store, id)?;
    }
    if scenario == "remote" {
        // A later acknowledged turn bounds the uncertainty without sacrificing earlier public speech.
        store.replace_live_words(
            id,
            "me",
            2000,
            3000,
            &[word(2000, "Uncertain pre-pause turn")],
        )?;
        tico.push_live(store, id)?;
        tico.call(
            &format!("/api/v2/live-meetings/{live_id}/control"),
            Some(&json!({"action":"pause"})),
            "fixture:remote-pause",
        )?;
        store.replace_live_words(id, "me", 4000, 5000, &[word(4000, "PRIVATE_ALPHA")])?;
        // The real paused backend refuses the queued text; readback must quarantine it.
        let _ = tico.push_live(store, id);
        tico.sync_live(store, id)?;
    } else {
        if scenario == "pause-ack" {
            ensure!(
                tico.control_live(store, id, "pause", 3000).is_err(),
                "Fixture must lose pause acknowledgement"
            );
            ensure!(
                store.context(id)?.locally_paused,
                "Uncertain pause remains locally paused"
            );
        } else {
            tico.control_live(store, id, "pause", 3000)?;
        }
        store.replace_live_words(id, "me", 4000, 5000, &[word(4000, "PRIVATE_ALPHA")])?;
    }
    // Finished chunks span both boundaries and correct earlier streamed turns/nearest turns.
    let samples: Vec<f32> = (0..16000).map(|i| (i as f32 * 0.1).sin() * 0.2).collect();
    store.save_audio(id, Channel::Microphone, 0, 6000, &samples, 16000)?;
    store.save_audio(id, Channel::Microphone, 6000, 12000, &samples, 16000)?;
    for words in [
        vec![word(0, "Public opening"), word(4000, "PRIVATE_ALPHA")],
        vec![word(6500, "PRIVATE_BETA"), word(8000, "Public closing")],
    ] {
        let chunk = store.claim_transcription()?.expect("fixture chunk");
        let text = words
            .iter()
            .map(|w| w.text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        store.transcription_words(&chunk, &text, words, "", json!({"fixture":true}))?;
    }
    store.state(id, "stopped")?;
    store.update_context(id, |c| {
        c.items.push(Item {
            id: "quote".into(),
            kind: "task".into(),
            title: "Review selected quote".into(),
            quote: "PRIVATE_ALPHA".into(),
            at_ms: 4000,
            ..Default::default()
        });
        c.items.push(Item {
            id: "title".into(),
            kind: "task".into(),
            title: "PRIVATE_BETA".into(),
            ..Default::default()
        });
        Ok(())
    })?;
    ensure!(
        tico.finish_live(store, id).is_err(),
        "Paused Finish must refuse"
    );
    ensure!(
        tico.send(store, id, false).is_err(),
        "Paused Send must refuse"
    );
    ensure!(
        tico.send_task_confirmed(store, id, "quote", "human:ben", "Ben", true, true)
            .is_err(),
        "Paused item Send must refuse even after review"
    );
    // Reopen the same synthetic library: the pause/discard policy cannot rely on UI memory.
    let reopened = Store::open(store.root())?;
    if ["discard", "discard-ack"].contains(&scenario) {
        if scenario == "discard-ack" {
            ensure!(
                tico.control_live(&reopened, id, "discard", 12000).is_err(),
                "Fixture must lose end acknowledgement"
            );
            ensure!(
                Store::open(store.root())?.context(id)?.live_discarded,
                "Discard intent survives reopen after lost acknowledgement"
            );
        }
        tico.control_live(&reopened, id, "discard", 12000)?;
        tico.control_live(&reopened, id, "discard", 12000)?;
        ensure!(
            tico.send(&reopened, id, false).is_err(),
            "Discard cannot file"
        );
        ensure!(
            tico.finish_live(&reopened, id).is_err(),
            "Discard cannot finalize"
        );
        ensure!(
            tico.send_task_confirmed(&reopened, id, "quote", "human:ben", "Ben", true, true)
                .is_err(),
            "Discard cannot export items"
        );
    } else {
        tico.control_live(&reopened, id, "resume", 7000)?;
        store.replace_live_words(id, "me", 8000, 9000, &[word(8000, "Public closing")])?;
        tico.push_live(&reopened, id)?;
        tico.sync_live(&reopened, id)?;
        for item in ["quote", "title"] {
            ensure!(
                tico.send_task(&reopened, id, item, "human:ben", "Ben", true)
                    .is_err(),
                "Title and quote require review"
            );
        }
        reopened.update_context(id, |c| {
            c.items[1].title = "PRIVATE_BETA edited".into();
            Ok(())
        })?;
        ensure!(
            tico.send_task(&reopened, id, "title", "human:ben", "Ben", true)
                .is_err(),
            "Edited title requires fresh review"
        );
        for item in ["quote", "title"] {
            tico.send_task_confirmed(&reopened, id, item, "human:ben", "Ben", true, true)?;
        }
        tico.finish_live(&reopened, id)?;
        tico.finish_live(&reopened, id)?;
    }
    let local = reopened.get(id)?;
    for secret in ["PRIVATE_ALPHA", "PRIVATE_BETA"] {
        ensure!(
            local.chunks.iter().any(|c| c.text.contains(secret)),
            "Local text must retain paused speech"
        );
    }
    for chunk in &local.chunks {
        ensure!(
            reopened.audio_path(chunk)?.is_file(),
            "Local audio must remain"
        );
    }
    println!(
        "{}",
        json!({"synthetic":true,"scenario":scenario,"live_id":live_id,"local":local,"context":reopened.context(id)?})
    );
    Ok(())
}
