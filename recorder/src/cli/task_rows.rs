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

fn receipt_race(store: &Store, tico: &Tico, task: &str, order: &str) -> Result<serde_json::Value> {
    let id = recording(store, &format!("Receipt race {order}"))?;
    let meeting = tico.send_to(store, &id, false, "team")?;
    let gate = format!("/recorder-test/races/{meeting}");
    tico.call(&gate, Some(&json!({"order":order})), "")?;
    ensure!(
        tico.queue_links(store, &id, &[task.into()]).is_err(),
        "Fixture fails the first link request"
    );
    store.update_context(&id, |c| {
        c.items.push(Item {
            id: "added".into(),
            kind: "task".into(),
            title: format!("Receipt race {order}"),
            ..Default::default()
        });
        Ok(())
    })?;
    std::thread::scope(|scope| -> Result<()> {
        let held = scope.spawn(|| {
            if order == "link-last" {
                tico.flush_saved_links(store, &id)
            } else {
                tico.upload_pending_item(store, &id, "added")
            }
        });
        let completed = (|| -> Result<()> {
            tico.call(&format!("{gate}/wait"), Some(&json!({})), "")?;
            if order == "link-last" {
                tico.upload_pending_item(store, &id, "added")?;
            } else {
                tico.flush_saved_links(store, &id)?;
            }
            Ok(())
        })();
        let released = tico.call(&format!("{gate}/release"), Some(&json!({})), "");
        let finished = held.join().expect("receipt race worker");
        completed?;
        released?;
        finished
    })?;
    let receipt = store.get(&id)?.deliveries[0].receipt.clone();
    let original = receipt["items"]["added"].clone();
    ensure!(
        original["id"].as_str().is_some(),
        "Link retry lost the Add receipt"
    );
    ensure!(
        receipt["links"][task] == "linked",
        "Add lost the link receipt"
    );
    ensure!(
        receipt["uploads"]["added"].is_null(),
        "Upload intent was restored"
    );
    ensure!(
        store.context(&id)?.task_link_operations.is_empty(),
        "Link remains queued"
    );
    store.update_context(&id, |c| {
        c.items
            .iter_mut()
            .find(|item| item.id == "added")
            .unwrap()
            .owner = "human:ben".into();
        Ok(())
    })?;
    tico.send_task(store, &id, "added", "human:ben", "Ben", false)?;
    let created = store
        .context(&id)?
        .items
        .into_iter()
        .find(|item| item.id == "added")
        .unwrap()
        .task_id;
    Ok(json!({"meeting":meeting,"original":original,"created":created,"order":order}))
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
        store.get(&local)?.deliveries.is_empty()
            && store.context(&local)?.live_discarded
            && store.context(&local)?.live_removed
            && store.context(&local)?.task_link_operations.is_empty(),
        "This Mac removes the delivery and pending links after discard"
    );

    let reopened_local = Store::open(store.root())?;
    tico.flush_saved_links(&reopened_local, &local)?;
    ensure!(
        tico.finish_live(&reopened_local, &local).is_err(),
        "Recovery must not finalize a discarded live share"
    );
    ensure!(
        tico.queue_links(&reopened_local, &local, &tasks[..1])
            .is_err(),
        "A discarded live share cannot queue new links"
    );
    ensure!(
        store
            .get(&local)?
            .chunks
            .iter()
            .any(|chunk| chunk.text == "I will send the proposal"),
        "This Mac retains the local transcript"
    );
    for chunk in &store.get(&local)?.chunks {
        ensure!(
            store.audio_path(chunk)?.is_file(),
            "This Mac retains saved audio"
        );
    }

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
    let mut races = vec![];
    for order in ["link-last", "upload-last"] {
        races.push(receipt_race(store, &tico, &tasks[0], order)?);
    }
    println!(
        "{}",
        json!({"synthetic":true,"meeting":meeting,"created":created,"retry_meeting":retry_meeting,"races":races,"local":store.context(&local)?,"local_id":local,"pending":pending,"live":live})
    );
    Ok(())
}
