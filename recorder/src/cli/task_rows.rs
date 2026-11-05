//! Synthetic Recorder rows through the real transport and an isolated loopback Tico.
use anyhow::{Result, ensure};
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
    tico.queue_links(store, &id, &tasks)?;
    ensure!(
        tico.send_to(store, &id, false, "team").is_err(),
        "Fixture interrupts batch two"
    );
    let reopened = Store::open(store.root())?;
    let meeting = tico.send_to(&reopened, &id, false, "team")?;
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
    tico.ensure_live(store, &local, false)?;
    tico.finish_live(store, &local)?;
    tico.queue_links(store, &local, &tasks[..1])?;
    tico.keep_local(store, &local)?;
    ensure!(
        store.context(&local)?.items.is_empty(),
        "This Mac drops queued links"
    );
    ensure!(
        store.get(&local)?.deliveries.is_empty(),
        "This Mac never files"
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
        json!({"synthetic":true,"meeting":meeting,"created":created,"retry_meeting":retry_meeting,"local":store.context(&local)?})
    );
    Ok(())
}
