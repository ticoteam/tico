//! Recorder-local links become server links only after the meeting is approved.
use crate::{
    network::{Tico, hash},
    store::{Delivery, Store},
    workspace::{Item, MAX_ITEMS},
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Task {
    pub id: String,
    pub number: u64,
    pub title: String,
    pub status: String,
}

impl Tico {
    pub fn search_tasks(&self, query: &str, offset: usize) -> Result<Value> {
        let mut url = reqwest::Url::parse("https://localhost/")?;
        url.query_pairs_mut()
            .append_pair("q", query)
            .append_pair("limit", "30")
            .append_pair("brief", "true")
            .append_pair("offset", &offset.to_string());
        let reply = self.call(
            &format!("/api/v2/tasks?{}", url.query().unwrap_or("")),
            None,
            "",
        )?;
        let tasks = tasks(&reply)?;
        Ok(json!({"tasks":tasks,"next_offset":reply["next_offset"]}))
    }

    /// Recheck all ids in one bounded read. A missing or no longer readable task disappears.
    pub fn readable_tasks(&self, ids: &[String]) -> Result<Vec<Task>> {
        let mut out = vec![];
        for batch in ids.chunks(30) {
            let mut url = reqwest::Url::parse("https://localhost/")?;
            url.query_pairs_mut()
                .append_pair("ids", &batch.join(","))
                .append_pair("limit", "30")
                .append_pair("brief", "true");
            out.extend(tasks(&self.call(
                &format!("/api/v2/tasks?{}", url.query().unwrap_or("")),
                None,
                "",
            )?)?);
        }
        Ok(out)
    }

    /// The selection is cached per meeting and org, but its titles and permissions are never cached in the prompt.
    pub fn draft_tasks(&self, store: &Store, id: &str) -> Result<Vec<Task>> {
        let context = store.context(id)?;
        if context.kept_on_mac {
            return Ok(vec![]);
        }
        let now = chrono::Utc::now().timestamp();
        let ids = if context.task_candidates_destination != self.destination.id
            || now - context.task_candidates_at >= 300
            || context.task_candidates_at == 0
        {
            let mut url = reqwest::Url::parse("https://localhost/")?;
            url.query_pairs_mut()
                .append_pair("owner", &self.destination.actor)
                .append_pair("status", "open,doing,waiting,review,ready")
                .append_pair("limit", "30")
                .append_pair("brief", "true");
            let found = tasks(&self.call(
                &format!("/api/v2/tasks?{}", url.query().unwrap_or("")),
                None,
                "",
            )?)?;
            let ids: Vec<String> = found.iter().map(|task| task.id.clone()).collect();
            store.update_context(id, |c| {
                c.task_candidates = ids.clone();
                c.task_candidates_at = now;
                c.task_candidates_destination = self.destination.id.clone();
                Ok(())
            })?;
            return Ok(found);
        } else {
            context.task_candidates
        };
        Ok(self
            .readable_tasks(&ids)?
            .into_iter()
            .filter(|task| {
                ["open", "doing", "waiting", "review", "ready"].contains(&task.status.as_str())
            })
            .collect())
    }

    pub fn queue_links(&self, store: &Store, id: &str, ids: &[String]) -> Result<()> {
        ensure!(!ids.is_empty() && ids.len() <= 30, "Choose up to 30 tasks");
        let found = self.readable_tasks(ids)?;
        ensure!(
            ids.iter().all(|id| found.iter().any(|task| &task.id == id)),
            "A task is no longer available"
        );
        let context = store.context(id)?;
        ensure!(!context.kept_on_mac, "This meeting stays on this Mac");
        ensure!(
            context.live_destination.is_empty() || context.live_destination == self.destination.id,
            "This meeting belongs to another Tico"
        );
        if let Some(delivery) = store
            .get(id)?
            .deliveries
            .iter()
            .find(|d| d.destination_id == self.destination.id && crate::live::saved_in_tico(d))
        {
            for batch in ids.chunks(20) {
                self.call(
                    &format!("/api/v2/meetings/{}/tasks", delivery.meeting_id),
                    Some(&json!({"task_ids":batch,"kind":"linked"})),
                    &format!("recorder:{id}:links:{}", hash(&json!(batch))),
                )?;
            }
        }
        store.update_context(id, |c| {
            ensure!(!c.kept_on_mac, "This meeting stays on this Mac");
            c.live_destination = self.destination.id.clone();
            for task in found {
                if !c.items.iter().any(|item| item.task_id == task.id) {
                    ensure!(c.items.len() < MAX_ITEMS, "Too many tasks");
                    c.items.push(Item {
                        id: uuid::Uuid::new_v4().to_string(),
                        kind: "link".into(),
                        task_id: task.id,
                        title: String::new(),
                        ..Default::default()
                    });
                }
            }
            Ok(())
        })
    }

    pub fn accept_task_link(&self, store: &Store, id: &str, item_id: &str) -> Result<()> {
        let context = store.context(id)?;
        ensure!(!context.kept_on_mac, "This meeting stays on this Mac");
        ensure!(
            context.live_destination.is_empty() || context.live_destination == self.destination.id,
            "This meeting belongs to another Tico"
        );
        let item = context
            .items
            .iter()
            .find(|i| i.id == item_id)
            .context("This proposal was removed")?;
        let tasks = self.readable_tasks(&[item.proposed_task_id.clone()])?;
        ensure!(
            !item.proposed_task_id.is_empty() && !tasks.is_empty(),
            "This task is no longer available"
        );
        if let Some(delivery) = store
            .get(id)?
            .deliveries
            .iter()
            .find(|d| d.destination_id == self.destination.id && crate::live::saved_in_tico(d))
        {
            let server_id = delivery.receipt["items"][item_id]["id"]
                .as_str()
                .context("This proposal is not saved")?;
            self.call(
                &format!(
                    "/api/v2/meetings/{}/items/{server_id}/link",
                    delivery.meeting_id
                ),
                Some(&json!({})),
                &format!("recorder:{id}:accept-link:{server_id}"),
            )?;
        }
        store.update_context(id, |c| {
            c.live_destination = self.destination.id.clone();
            if let Some(item) = c.items.iter_mut().find(|i| i.id == item_id) {
                item.kind = "link".into();
                item.task_id = item.proposed_task_id.clone();
                item.proposed_task_id.clear();
                item.title.clear();
            }
            Ok(())
        })
    }

    pub(crate) fn save_links(
        &self,
        store: &Store,
        id: &str,
        context: &crate::workspace::Context,
        delivery: &mut Delivery,
    ) -> Result<()> {
        for kind in ["linked", "sent"] {
            let mut ids: Vec<String> = context
                .items
                .iter()
                .filter(|item| {
                    !item.task_id.is_empty()
                        && (item.kind == "link") == (kind == "linked")
                        && delivery.receipt["links"][&item.task_id] != kind
                })
                .map(|item| item.task_id.clone())
                .collect();
            ids.sort();
            ids.dedup();
            for batch in ids.chunks(20) {
                self.call(
                    &format!("/api/v2/meetings/{}/tasks", delivery.meeting_id),
                    Some(&json!({"task_ids":batch,"kind":kind})),
                    &format!("recorder:{id}:links:{kind}:{}", hash(&json!(batch))),
                )?;
                for tid in batch {
                    delivery.receipt["links"][tid] = json!(kind);
                }
                store.delivery(id, delivery)?;
            }
        }
        Ok(())
    }
}

fn tasks(reply: &Value) -> Result<Vec<Task>> {
    let mut tasks: Vec<Task> = serde_json::from_value(reply["tasks"].clone())?;
    for task in &mut tasks {
        task.title = task.title.chars().take(200).collect();
    }
    Ok(tasks)
}
