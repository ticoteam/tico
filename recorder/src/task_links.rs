//! Recorder-local links become server links only after the meeting is approved.
use crate::{
    network::{Tico, hash},
    store::{Delivery, Store},
    workspace::{Item, MAX_ITEMS, TaskLinkOperation},
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
    #[serde(default)]
    pub owner_id: String,
}

impl Tico {
    pub fn linked_tasks(&self, meeting: &str, cursor: Option<&str>) -> Result<Value> {
        let mut url = reqwest::Url::parse("https://localhost/")?;
        url.query_pairs_mut().append_pair("limit", "50");
        if let Some(cursor) = cursor {
            url.query_pairs_mut().append_pair("cursor", cursor);
        }
        self.call(
            &format!(
                "/api/v2/meetings/{meeting}/tasks?{}",
                url.query().unwrap_or("")
            ),
            None,
            "",
        )
    }
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
            return Ok(found
                .into_iter()
                .filter(|task| {
                    task.owner_id == self.destination.actor
                        && ["open", "doing", "waiting", "review", "ready"]
                            .contains(&task.status.as_str())
                })
                .collect());
        } else {
            context.task_candidates
        };
        Ok(self
            .readable_tasks(&ids)?
            .into_iter()
            .filter(|task| {
                task.owner_id == self.destination.actor
                    && ["open", "doing", "waiting", "review", "ready"]
                        .contains(&task.status.as_str())
            })
            .collect())
    }

    pub fn queue_links(&self, store: &Store, id: &str, ids: &[String]) -> Result<()> {
        ensure!(
            !ids.is_empty() && ids.len() <= MAX_ITEMS,
            "Choose up to {MAX_ITEMS} tasks"
        );
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
        store.update_context(id, |c| {
            ensure!(!c.kept_on_mac, "This meeting stays on this Mac");
            ensure!(
                c.task_link_operations.len() < MAX_ITEMS,
                "Too many pending link actions"
            );
            c.live_destination = self.destination.id.clone();
            for task in found {
                if !c.items.iter().any(|item| item.task_id == task.id) {
                    ensure!(c.items.len() < MAX_ITEMS, "Too many tasks");
                    c.items.push(Item {
                        id: uuid::Uuid::new_v4().to_string(),
                        kind: "link".into(),
                        task_id: task.id,
                        task_destination: self.destination.id.clone(),
                        title: String::new(),
                        ..Default::default()
                    });
                }
            }
            c.task_link_operations.push(TaskLinkOperation {
                id: uuid::Uuid::new_v4().to_string(),
                kind: "linked".into(),
                task_ids: ids.to_vec(),
            });
            Ok(())
        })?;
        self.flush_saved_links(store, id)
    }

    pub fn queue_unlink(&self, store: &Store, id: &str, task: &str) -> Result<()> {
        store.update_context(id, |c| {
            ensure!(!c.kept_on_mac, "This meeting stays on this Mac");
            ensure!(
                c.live_destination.is_empty() || c.live_destination == self.destination.id,
                "This meeting belongs to another Tico"
            );
            c.live_destination = self.destination.id.clone();
            ensure!(
                c.task_link_operations.len() < MAX_ITEMS,
                "Too many pending link actions"
            );
            c.task_link_operations.push(TaskLinkOperation {
                id: uuid::Uuid::new_v4().to_string(),
                kind: "unlink".into(),
                task_ids: vec![task.to_owned()],
            });
            Ok(())
        })?;
        if !store
            .get(id)?
            .deliveries
            .iter()
            .any(|d| d.destination_id == self.destination.id && crate::live::saved_in_tico(d))
        {
            store.update_context(id, |c| {
                c.items
                    .retain(|item| item.kind != "link" || item.task_id != task);
                for op in &mut c.task_link_operations {
                    op.task_ids.retain(|tid| tid != task);
                }
                c.task_link_operations.retain(|op| !op.task_ids.is_empty());
                Ok(())
            })?;
        }
        self.flush_saved_links(store, id)
    }

    fn flush_saved_links(&self, store: &Store, id: &str) -> Result<()> {
        if let Some(mut delivery) = store
            .get(id)?
            .deliveries
            .into_iter()
            .find(|d| d.destination_id == self.destination.id && crate::live::saved_in_tico(d))
        {
            self.flush_link_operations(store, id, &mut delivery)?;
        }
        Ok(())
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
        ensure!(
            item.task_destination.is_empty() || item.task_destination == self.destination.id,
            "This proposal belongs to another Tico"
        );
        ensure!(
            !item.proposed_task_id.is_empty(),
            "This item has no task to link"
        );
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
        // Older local rows and tasks created before Save join the same durable outbox.
        store.update_context(id, |c| {
            for kind in ["linked", "sent"] {
                let mut ids: Vec<String> = context
                    .items
                    .iter()
                    .filter(|item| {
                        !item.task_id.is_empty()
                            && (item.kind == "link") == (kind == "linked")
                            && (item.task_destination.is_empty()
                                || item.task_destination == self.destination.id)
                            && delivery.receipt["links"][&item.task_id] != kind
                            && !c
                                .task_link_operations
                                .iter()
                                .any(|op| op.task_ids.contains(&item.task_id))
                    })
                    .map(|item| item.task_id.clone())
                    .collect();
                ids.sort();
                ids.dedup();
                if !ids.is_empty() {
                    c.task_link_operations.push(TaskLinkOperation {
                        id: uuid::Uuid::new_v4().to_string(),
                        kind: kind.into(),
                        task_ids: ids,
                    });
                }
            }
            Ok(())
        })?;
        self.flush_link_operations(store, id, delivery)
    }

    fn flush_link_operations(
        &self,
        store: &Store,
        id: &str,
        delivery: &mut Delivery,
    ) -> Result<()> {
        for op in store.context(id)?.task_link_operations {
            for batch in op.task_ids.chunks(20) {
                let key = format!("recorder:{id}:links:{}:{}", op.id, hash(&json!(batch)));
                let reply = if op.kind == "unlink" {
                    self.unlink_meeting_task(&delivery.meeting_id, &batch[0], &key)?
                } else {
                    self.call(
                        &format!("/api/v2/meetings/{}/tasks", delivery.meeting_id),
                        Some(&json!({"task_ids":batch,"kind":op.kind})),
                        &key,
                    )?
                };
                let skipped: Vec<_> = batch
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| reply["results"][*index]["status"] == 404)
                    .map(|(_, tid)| tid.clone())
                    .collect();
                for tid in batch {
                    if op.kind == "unlink" || skipped.contains(tid) {
                        if let Some(links) = delivery.receipt["links"].as_object_mut() {
                            links.remove(tid);
                        }
                    } else {
                        delivery.receipt["links"][tid] = json!(op.kind);
                    }
                }
                store.delivery(id, delivery)?;
                store.update_context(id, |c| {
                    c.skipped_task_links += skipped.len();
                    c.items.retain(|item| {
                        item.kind != "link"
                            || !(skipped.contains(&item.task_id)
                                || op.kind == "unlink" && batch.contains(&item.task_id))
                    });
                    if let Some(pending) = c
                        .task_link_operations
                        .iter_mut()
                        .find(|pending| pending.id == op.id)
                    {
                        pending.task_ids.retain(|tid| !batch.contains(tid));
                    }
                    c.task_link_operations
                        .retain(|pending| !pending.task_ids.is_empty());
                    Ok(())
                })?;
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
