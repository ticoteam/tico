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
            .filter(|item| {
                item.task_destination.is_empty() || item.task_destination == self.router()
            })
            .flat_map(|item| [&item.task_id, &item.proposed_task_id])
            .filter(|id| !id.is_empty())
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let scope = self.link_scope();
        let config = self.config.clone();
        let destination = self.router();
        let remote = self
            .current
            .as_ref()
            .and_then(|r| {
                r.deliveries
                    .iter()
                    .find(|d| d.destination_id == destination && live::saved_in_tico(d))
            })
            .map(|d| d.meeting_id.clone());
        if ids.is_empty() && remote.is_none() {
            return;
        }
        self.link_tasks = Value::Null;
        self.link_tasks_at = Instant::now();
        self.job_for("task-links", Some(id), move || {
            let tico = Tico::saved(&config, &destination)?;
            let tasks = tico.readable_tasks(&ids)?;
            let browser = tico.browser_url()?;
            let linked = remote.as_ref().map(|rid| tico.linked_tasks(rid, None)).transpose()?;
            Ok(json!({"tasks":tasks,"scope":scope,"browser":browser,"remote":remote,"linked":linked}))
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
            if let Some(task) = item_id.strip_prefix("server:") {
                let record = store.get(&id)?;
                let delivery = record
                    .deliveries
                    .iter()
                    .find(|d| d.destination_id == tico.destination.id && live::saved_in_tico(d))
                    .ok_or_else(|| anyhow::anyhow!("This meeting is not saved"))?;
                tico.unlink_meeting_task(&delivery.meeting_id, task)?;
                store.update_context(&id, |c| {
                    c.items.retain(|i| i.kind != "link" || i.task_id != task);
                    Ok(())
                })?;
                return Ok(json!({"message":"Unlinked"}));
            }
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
        self.link_tasks_at = Instant::now() - Duration::from_secs(60);
    }
    pub fn more_task_links(&mut self) {
        let Some(cursor) = self.link_tasks["linked"]["next_cursor"]
            .as_str()
            .map(str::to_owned)
        else {
            return;
        };
        let Some(remote) = self.link_tasks["remote"].as_str().map(str::to_owned) else {
            return;
        };
        let scope = self.link_scope();
        self.network_job("more-links", move |tico, _, _| {
            Ok(json!({"scope":scope,"linked":tico.linked_tasks(&remote, Some(&cursor))?}))
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_link_titles_and_proposal_numbers_do_not_cross_orgs_or_this_mac() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Local meeting").unwrap();
        let mut app = Recorder::new(store, None, true).unwrap();
        app.config.destinations = ["one", "two"]
            .iter()
            .map(|id| settings::Destination {
                id: id.to_string(),
                name: id.to_string(),
                base_url: format!("https://{id}.example"),
                actor: "human:ana".into(),
            })
            .collect();
        app.destination = "one".into();
        app.open(&record.id).unwrap();
        app.context.items = vec![
            Item {
                id: "link".into(),
                kind: "link".into(),
                task_id: "task".into(),
                task_destination: "one".into(),
                ..Default::default()
            },
            Item {
                id: "proposal".into(),
                title: "Follow up".into(),
                proposed_task_id: "task".into(),
                task_destination: "one".into(),
                ..Default::default()
            },
        ];
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[{"id":"task","title":"Private title","number":42,"status":"open"}]});
        app.link_tasks_at = Instant::now();
        let view = app.actions_view("one");
        assert_eq!(
            view["items"]
                .as_array()
                .unwrap()
                .iter()
                .find(|row| row["id"] == "link")
                .unwrap()["number"],
            42
        );
        app.destination = "two".into();
        let rows = app.actions_view("two")["items"].as_array().unwrap().clone();
        assert_eq!(rows.len(), 1);
        assert!(rows[0]["proposed_task_id"].is_null());
        assert!(rows[0]["proposed_number"].is_null());
        app.context.kept_on_mac = true;
        assert_eq!(app.actions_view("two")["can_link"], false);
    }
}
