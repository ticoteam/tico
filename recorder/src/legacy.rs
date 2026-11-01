//! Read the previous recorder library without changing any source files.
use crate::{
    audio::Channel,
    store::{Delivery, Recording, Store},
    workspace::Item,
};
use anyhow::{Context, Result, ensure};
use rusqlite::params;
use serde_json::{Value, json};
use std::{fs, path::Path};
impl Store {
    pub fn import_legacy(&self, root: &Path) -> Result<usize> {
        if !root.exists() {
            return Ok(0);
        }
        let root = root.canonicalize()?;
        let mut imported = 0;
        for entry in fs::read_dir(&root)? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let id = entry.file_name().to_string_lossy().to_string();
            if uuid::Uuid::parse_str(&id).is_err() {
                continue;
            }
            let directory = entry.path();
            let metadata = directory.join("recording.json");
            if !metadata.exists() {
                continue;
            }
            let old: Value = serde_json::from_slice(&fs::read(&metadata)?)?;
            ensure!(
                old["id"].as_str() == Some(&id),
                "Invalid legacy recording identifier"
            );
            let exists = self.get(&id).is_ok();
            if !exists {
                let record = Recording {
                    id: id.clone(),
                    title: old["title"].as_str().unwrap_or("Imported recording").into(),
                    started_at: old["started_at"].as_str().unwrap_or("").into(),
                    state: if old["status"] == "recording" {
                        "interrupted"
                    } else {
                        "stopped"
                    }
                    .into(),
                    microphone: String::new(),
                    meeting_audio: old["chunks"]
                        .as_array()
                        .is_some_and(|rows| rows.iter().any(|chunk| chunk["channel"] == "them")),
                    warning: String::new(),
                    chunks: vec![],
                    deliveries: vec![],
                    speakers: String::new(),
                };
                self.db()?.execute(
                    "INSERT INTO recordings VALUES(?,?)",
                    params![id, serde_json::to_string(&record)?],
                )?;
                imported += 1;
            }
            for chunk in old["chunks"].as_array().into_iter().flatten() {
                let legacy_id = chunk["id"]
                    .as_str()
                    .context("Legacy audio has no identifier")?;
                if self
                    .get(&id)?
                    .chunks
                    .iter()
                    .any(|row| row.provider["legacy_id"] == legacy_id)
                {
                    continue;
                }
                let name = chunk["file"]
                    .as_str()
                    .context("Legacy audio has no filename")?;
                let path = directory.join("audio").join(name).canonicalize()?;
                ensure!(
                    path.starts_with(&directory),
                    "Legacy audio escaped its recording directory"
                );
                let mut saved = self.save_wav_with_id(
                    &id,
                    Channel::parse(chunk["channel"].as_str().unwrap_or(""))?,
                    chunk["start_ms"].as_u64().unwrap_or(0),
                    chunk["end_ms"]
                        .as_u64()
                        .context("Invalid legacy audio time")?,
                    &fs::read(path)?,
                    legacy_id,
                )?;
                saved.provider = json!({"legacy_id":legacy_id});
                if let Some(segment) = old["segments"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .find(|row| row["chunk_id"] == legacy_id)
                {
                    saved.text = segment["text"].as_str().unwrap_or("").into();
                    saved.speaker = segment["speaker"].as_str().unwrap_or(&saved.speaker).into();
                    saved.status = "done".into();
                }
                if saved.signal.silent() {
                    saved.text.clear();
                    saved.status = "silence".into();
                }
                self.db()?.execute(
                    "UPDATE chunks SET status=?,document=? WHERE id=?",
                    params![saved.status, serde_json::to_string(&saved)?, saved.id],
                )?;
            }
            if !self.context(&id)?.legacy_import_complete {
                let mut context = self.context(&id)?;
                if let Some(live) = old.get("live").filter(|value| value.is_object()) {
                    context.live_id = live["id"].as_str().unwrap_or("").into();
                    context.live_destination = live["destination_id"].as_str().unwrap_or("").into();
                    context.live_state = live["state"].as_str().unwrap_or("live").into();
                    if let Some(meeting) = live["meeting_id"]
                        .as_str()
                        .filter(|value| !value.is_empty())
                    {
                        self.delivery(
                            &id,
                            &Delivery {
                                destination_id: context.live_destination.clone(),
                                meeting_id: meeting.into(),
                                status: "partial".into(),
                                error: String::new(),
                                receipt: json!({"source":"legacy-live"}),
                            },
                        )?;
                    }
                    if !context.live_id.is_empty() {
                        self.mutate(&id,|record|{record.warning="This recording is linked to an existing Tico live meeting. Reconnect its original Tico destination to resume or finish delivery.".into();Ok(())})?;
                    }
                }
                context.items.clear();
                for item in old["actions"].as_array().into_iter().flatten() {
                    context.items.push(Item {
                        id: item["id"].as_str().unwrap_or("").into(),
                        kind: item["kind"].as_str().unwrap_or("task").into(),
                        title: item["title"].as_str().unwrap_or("").into(),
                        quote: item["quote"].as_str().unwrap_or("").into(),
                        at_ms: item["at_ms"].as_u64().unwrap_or(0),
                        selected: false,
                        ..Default::default()
                    });
                }
                for (destination, value) in old["deliveries"].as_object().into_iter().flatten() {
                    self.delivery(
                        &id,
                        &Delivery {
                            destination_id: destination.clone(),
                            meeting_id: value["meeting_id"].as_str().unwrap_or("").into(),
                            status: value["status"].as_str().unwrap_or("partial").into(),
                            error: value["error"].as_str().unwrap_or("").into(),
                            receipt: value.clone(),
                        },
                    )?;
                }
                if self
                    .get(&id)?
                    .chunks
                    .iter()
                    .all(|chunk| chunk.signal.silent())
                {
                    self.mutate(&id,|record|{record.warning="This recording contains only digital silence. There is no recorded speech to transcribe.".into();Ok(())})?;
                }
                context.legacy_import_complete = true;
                self.save_context(&id, &context)?;
            }
        }
        if let Some(parent) = root.parent() {
            let file = parent.join("settings.json");
            if file.is_file() {
                let previous: crate::settings::Settings = serde_json::from_slice(&fs::read(file)?)?;
                let mut current = crate::settings::read(self.root())?;
                for destination in previous.destinations {
                    if !current
                        .destinations
                        .iter()
                        .any(|entry| entry.id == destination.id)
                    {
                        current.destinations.push(destination);
                    }
                }
                crate::settings::write(self.root(), &current)?;
            }
        }
        Ok(imported)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_import_preserves_originals_transcripts_and_delivery_receipts_and_is_repeatable() {
        let source = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        let chunk = uuid::Uuid::new_v4().to_string();
        let directory = source.path().join(&id);
        fs::create_dir_all(directory.join("audio")).unwrap();
        let wav = crate::audio::encode_wav(&vec![0.2; 16000], 16000).unwrap();
        fs::write(directory.join("audio").join(format!("{chunk}.wav")), &wav).unwrap();
        let value = json!({"id":id,"title":"Previous recorder","started_at":"2026-10-20T20:00:00Z","status":"stopped","chunks":[{"id":chunk,"channel":"me","start_ms":0,"end_ms":1000,"file":format!("{chunk}.wav")}],"segments":[{"chunk_id":chunk,"speaker":"Chris","text":"Existing words"}],"deliveries":{"previous-tico":{"meeting_id":"existing-meeting","status":"sent"}}});
        let bytes = serde_json::to_vec(&value).unwrap();
        fs::write(directory.join("recording.json"), &bytes).unwrap();
        let store = Store::open(target.path()).unwrap();
        assert_eq!(store.import_legacy(source.path()).unwrap(), 1);
        assert_eq!(store.import_legacy(source.path()).unwrap(), 0);
        let imported = store.get(&id).unwrap();
        assert_eq!(imported.chunks.len(), 1);
        assert_eq!(imported.chunks[0].text, "Existing words");
        assert_eq!(imported.deliveries[0].meeting_id, "existing-meeting");
        assert_eq!(fs::read(directory.join("recording.json")).unwrap(), bytes);
        assert_eq!(
            fs::read(directory.join("audio").join(format!("{chunk}.wav"))).unwrap(),
            wav
        );
    }
}
