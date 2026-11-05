use super::*;

impl Recorder {
    pub(super) fn link_scope(&self) -> Value {
        let destination = self.router();
        let connected = self
            .config
            .destinations
            .iter()
            .find(|d| d.id == destination);
        json!([
            self.meeting,
            connected.map(|d| (&d.id, &d.actor, &d.base_url))
        ])
    }
    pub(super) fn linked_task(&self, id: &str) -> Option<&Value> {
        if id.is_empty()
            || self.link_tasks["scope"] != self.link_scope()
            || self.link_tasks_at.elapsed() > Duration::from_secs(60)
        {
            return None;
        }
        self.link_tasks["tasks"]
            .as_array()?
            .iter()
            .find(|task| task["id"] == id)
    }
    pub(super) fn refresh_task_links(&mut self) {
        if self.demo
            || self.background.contains("task-links")
            || self.link_tasks_at.elapsed() < Duration::from_secs(60)
        {
            return;
        }
        let Some(id) = self.meeting.clone() else {
            return;
        };
        let ids: Vec<String> = self
            .context
            .items
            .iter()
            .flat_map(|item| [&item.task_id, &item.proposed_task_id])
            .filter(|id| !id.is_empty())
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        if ids.is_empty() {
            return;
        }
        let scope = self.link_scope();
        let config = self.config.clone();
        let destination = self.router();
        self.link_tasks_at = Instant::now();
        self.job_for("task-links", Some(id), move || {
            let tico = Tico::saved(&config, &destination)?;
            let tasks = tico.readable_tasks(&ids)?;
            let browser = tico.browser_url()?;
            Ok(json!({"tasks":tasks,"scope":scope,"browser":browser}))
        });
    }
    pub fn task_picker_context(&self) -> (Settings, String) {
        (self.config.clone(), self.router())
    }
    pub fn link_selected_tasks(&mut self, ids: Vec<String>) {
        self.save_context();
        self.network_job("link", move |tico, store, id| {
            tico.queue_links(&store, &id, &ids)?;
            Ok(json!({"message":"Linked"}))
        });
        self.link_tasks_at = Instant::now() - Duration::from_secs(60);
    }
    pub fn accept_link_item(&mut self, item_id: &str) {
        self.save_context();
        let item_id = item_id.to_owned();
        self.network_job("link", move |tico, store, id| {
            tico.accept_task_link(&store, &id, &item_id)?;
            Ok(json!({"message":"Linked"}))
        });
        self.link_tasks_at = Instant::now() - Duration::from_secs(60);
    }
    pub fn unlink_item(&mut self, item_id: &str) {
        let item_id = item_id.to_owned();
        self.network_job("link", move |tico, store, id| {
            let context = store.context(&id)?;
            if let Some(item) = context
                .items
                .iter()
                .find(|i| i.id == item_id && i.kind == "link")
            {
                if let Some(delivery) = store
                    .get(&id)?
                    .deliveries
                    .iter()
                    .find(|d| d.destination_id == tico.destination.id && live::saved_in_tico(d))
                {
                    tico.unlink_meeting_task(&delivery.meeting_id, &item.task_id)?;
                }
                store.update_context(&id, |c| {
                    c.items.retain(|i| i.id != item_id);
                    Ok(())
                })?;
            }
            Ok(json!({"message":"Unlinked"}))
        });
    }
    pub fn edit_task_detail(&mut self, item_id: &str, due: &str, body: &str) {
        if let Some(item) = self
            .context
            .items
            .iter_mut()
            .find(|i| i.id == item_id && i.task_id.is_empty())
        {
            item.due = due.chars().take(100).collect();
            item.body = body.chars().take(4000).collect();
            self.save_context();
        }
    }
}
