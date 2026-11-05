//! Synthetic Recorder rows through the real transport and an isolated loopback Tico.
use anyhow::{Context, Result, ensure};
use serde_json::json;
use tico_recorder_native::{
    audio::Channel, network::Tico, settings::Destination, store::Store, workspace::Item,
};

fn recording(store: &Store, title: &str) -> Result<String> {
    let record = store.create(title)?;
    store.save_audio(
        &record.id,
        Channel::Microphone,
        0,
        1000,
        &vec![0.2; 16000],
        16000,
    )?;
    let chunk = store.claim_transcription()?.unwrap();
    store.transcription_result(
        &chunk,
        "I will send the proposal",
        "",
        json!({"synthetic":true}),
    )?;
    store.state(&record.id, "stopped")?;
    Ok(record.id)
}

pub fn run(store: &Store, base: &str) -> Result<()> {
    ensure!(
        std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Use an isolated library"
    );
    ensure!(
        reqwest::Url::parse(base)?.host_str() == Some("127.0.0.1"),
        "Replay only supports loopback"
    );
    let tico = Tico::new(
        Destination {
            id: "rows-fixture".into(),
            name: "Acme".into(),
            base_url: base.into(),
            actor: "human:ana".into(),
        },
        std::env::var("TICO_REPLAY_TEST_TOKEN")?,
    )?;
    let tasks: Vec<String> = serde_json::from_str(&std::env::var("TICO_LINK_TEST_TASKS")?)?;
    ensure!(tasks.len() == 45, "Use 45 synthetic tasks");
    let id = recording(store, "Recorder rows synthetic")?;
    tico.queue_links(store, &id, &tasks)
        .context("Queue existing tasks")?;
    ensure!(
        tico.send_to(store, &id, false, "team").is_err(),
        "Fixture interrupts batch two"
    );
    let reopened = Store::open(store.root())?;
    let meeting = tico
        .send_to(&reopened, &id, false, "team")
        .context("Resume queued link batches")?;
    reopened.update_context(&id, |c| {
        c.items.push(Item {
            id: "proposal".into(),
            kind: "task".into(),
            title: "Follow up on pricing".into(),
            proposed_task_id: tasks[0].clone(),
            ..Default::default()
        });
        Ok(())
    })?;
    tico.upload_pending_item(&reopened, &id, "proposal")?;
    tico.accept_task_link(&reopened, &id, "proposal")?;
    reopened.update_context(&id, |c| {
        c.items.push(Item {
            id: "added".into(),
            kind: "task".into(),
            title: "Send the revised plan".into(),
            ..Default::default()
        });
        Ok(())
    })?;
    tico.upload_pending_item(&reopened, &id, "added")?;
    tico.upload_pending_item(&reopened, &id, "added")?;
    tico.send_task(&reopened, &id, "added", "human:ana", "Ana", false)?;
    let created = reopened
        .context(&id)?
        .items
        .into_iter()
        .find(|i| i.id == "added")
        .unwrap()
        .task_id;

    let local = recording(store, "This Mac synthetic")?;
    tico.ensure_live(store, &local, false)
        .context("Share This Mac fixture")?;
    let live = store.context(&local)?.live_id;
    tico.call(
        &format!("/api/v2/live-meetings/{live}/chat"),
        Some(&json!({"text":"Keep this chat","at_ms":0})),
        "rows:chat",
    )?;
    tico.finish_live(store, &local)
        .context("Finalize This Mac fixture")?;
    let pending = store.get(&local)?.deliveries[0].meeting_id.clone();
    tico.call(
        &format!("/api/v2/meetings/{pending}/items"),
        Some(
            &json!({"section":"task","text":"Already sent","status":"pushed","result_ref":created}),
        ),
        "rows:sent-link",
    )?;
    store.update_context(&local, |c| {
        c.items.push(Item {
            id: "sent".into(),
            kind: "task".into(),
            title: "Already sent".into(),
            task_id: created.clone(),
            ..Default::default()
        });
        Ok(())
    })?;
    tico.queue_links(store, &local, &tasks[..1])?;
    tico.keep_local(store, &local)
        .context("Keep finalized fixture on This Mac")?;
    ensure!(
        store.context(&local)?.items.len() == 1
            && store.context(&local)?.items[0].task_id == created,
        "This Mac drops queued links"
    );
    ensure!(
        store.get(&local)?.deliveries[0].receipt["dismissed"] == true,
        "This Mac retains its dismissed receipt"
    );

    ensure!(
        store
            .get(&local)?
            .chunks
            .iter()
            .any(|chunk| chunk.text == "I will send the proposal"),
        "This Mac retains the local transcript"
    );
    ensure!(
        !store.get(&local)?.chunks.is_empty(),
        "This Mac retains the local recording"
    );
    let retry = recording(store, "Item replay synthetic")?;
    store.update_context(&retry, |c| {
        c.items.push(Item {
            id: "retry".into(),
            kind: "task".into(),
            title: "Original retry title".into(),
            owner: "human:ana".into(),
            ..Default::default()
        });
        Ok(())
    })?;
    ensure!(
        tico.send_to(store, &retry, false, "team").is_err(),
        "Fixture loses the committed item reply"
    );
    store.update_context(&retry, |c| {
        let i = &mut c.items[0];
        i.title = "Current retry title".into();
        i.owner = "human:ben".into();
        i.body = "Current retry details".into();
        i.due = "2026-12-01T12:00:00Z".into();
        Ok(())
    })?;
    let retry_meeting = tico.send_to(&Store::open(store.root())?, &retry, false, "team")?;
    println!(
        "{}",
        json!({"synthetic":true,"meeting":meeting,"created":created,"retry_meeting":retry_meeting,"local":store.context(&local)?,"local_id":local,"pending":pending,"live":live})
    );
    Ok(())
}
