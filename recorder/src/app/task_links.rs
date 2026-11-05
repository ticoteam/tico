use super::*;

impl Recorder {
    pub(super) fn pending_task_link(&self, task: &str) -> bool {
        if task.is_empty() {
            return false;
        }
        if self
            .context
            .task_link_operations
            .iter()
            .any(|op| op.task_ids.iter().any(|id| id == task))
        {
            return true;
        }
        // An accepted proposal can be a local link before its first Save creates an outbox action.
        self.context
            .items
            .iter()
            .any(|item| item.kind == "link" && item.task_id == task)
            && !self.current.as_ref().is_some_and(|record| {
                record.deliveries.iter().any(|delivery| {
                    delivery.destination_id == self.router()
                        && delivery.receipt["links"][task].is_string()
                })
            })
            && !self.link_tasks["linked"]["tasks"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|row| row["task_id"] == task)
    }

    pub(super) fn linked_tasks_view(&self) -> Value {
        if self.context.kept_on_mac || self.link_tasks["scope"] != self.link_scope() {
            return Value::Null;
        }
        let mut linked = self.link_tasks["linked"].clone();
        if let Some(tasks) = linked["tasks"].as_array_mut() {
            for task in tasks {
                task["pending"] =
                    json!(self.pending_task_link(task["task_id"].as_str().unwrap_or("")));
            }
        }
        linked
    }

    pub(super) fn may_open_linked_task(&self, url: &str) -> bool {
        if self.link_tasks["scope"] != self.link_scope()
            || self.link_tasks_at.elapsed() > Duration::from_secs(60)
        {
            return false;
        }
        let Some(base) = self.link_tasks["browser"]
            .as_str()
            .and_then(|base| settings::safe_url(base).ok())
        else {
            return false;
        };
        let Some(id) = url.strip_prefix(&format!("{base}/#/task/")) else {
            return false;
        };
        self.link_tasks["tasks"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|task| task["id"] == id)
            || self.link_tasks["linked"]["tasks"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|task| task["task_id"] == id)
    }
    pub(super) fn draft_destination(&self, id: &str) -> String {
        let context = self.store.context(id).unwrap_or_default();
        if !context.live_destination.is_empty() {
            return context.live_destination;
        }
        if self.meeting.as_deref() == Some(id) {
            return self.router();
        }
        if !context.task_candidates_destination.is_empty() {
            return context.task_candidates_destination;
        }
        self.config
            .destinations
            .first()
            .map(|d| d.id.clone())
            .unwrap_or_default()
    }
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
            || self.context.kept_on_mac
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
            let task = item_id
                .strip_prefix("server:")
                .map(str::to_owned)
                .or_else(|| {
                    context
                        .items
                        .iter()
                        .find(|i| i.id == item_id && i.kind == "link")
                        .map(|i| i.task_id.clone())
                });
            if let Some(task) = task {
                tico.queue_unlink(&store, &id, &task)?;
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
        app.link_tasks = json!({"scope":app.link_scope(),"browser":"https://browser.acme.example","tasks":[{"id":"task","title":"Private title","number":42,"status":"open"}]});
        app.link_tasks_at = Instant::now();
        assert!(app.may_open_linked_task("https://browser.acme.example/#/task/task"));
        assert!(!app.may_open_linked_task("https://browser.acme.example/#/task/other"));
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
        assert!(!app.may_open_linked_task("https://browser.acme.example/#/task/task"));
        let rows = app.actions_view("two")["items"].as_array().unwrap().clone();
        assert_eq!(rows.len(), 1);
        assert!(rows[0]["proposed_task_id"].is_null());
        assert!(rows[0]["proposed_number"].is_null());
        app.context.kept_on_mac = true;
        assert_eq!(app.actions_view("two")["can_link"], false);
        let background = app.store.create("Background recording").unwrap();
        app.store
            .update_context(&background.id, |c| {
                c.live_destination = "one".into();
                Ok(())
            })
            .unwrap();
        assert_eq!(app.draft_destination(&background.id), "one");
    }

    fn local_app() -> (tempfile::TempDir, Recorder, String) {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::open(dir.path()).unwrap();
        let record = store.create("Local meeting").unwrap();
        let mut app = Recorder::new(store, None, true).unwrap();
        app.config.destinations = vec![settings::Destination {
            id: "one".into(),
            name: "Acme".into(),
            base_url: "https://acme.example".into(),
            actor: "human:ana".into(),
        }];
        app.destination = "one".into();
        app.open(&record.id).unwrap();
        (dir, app, record.id)
    }

    #[test]
    fn this_mac_reloads_open_context_and_closing_cannot_restore_dropped_links() {
        let (_dir, mut app, id) = local_app();
        let stale_link = Item {
            id: "link".into(),
            kind: "link".into(),
            task_id: "target".into(),
            ..Default::default()
        };
        app.context.items = vec![
            stale_link.clone(),
            Item {
                id: "proposal".into(),
                title: "Send the plan".into(),
                proposed_task_id: "target".into(),
                ..Default::default()
            },
        ];
        app.context.notes = "Current notes".into();
        app.store.save_context(&id, &app.context).unwrap();
        app.save_context();
        app.keep_local(&id);
        assert!(app.context.kept_on_mac);
        assert_eq!(app.context.items.len(), 1);
        assert!(app.context.items[0].proposed_task_id.is_empty());
        assert_eq!(app.actions_view("one")["can_link"], false);
        // A stale editor or a late draft must also respect the durable local-only decision.
        app.context.items.push(stale_link);
        app.context.items[0].proposed_task_id = "target".into();
        app.close();
        let stored = app.store.context(&id).unwrap();
        assert!(stored.kept_on_mac && stored.items.len() == 1);
        assert!(stored.items[0].proposed_task_id.is_empty());
        assert_eq!(stored.notes, "Current notes");
        app.open(&id).unwrap();
        assert_eq!(app.actions_view("one")["can_link"], false);
    }

    #[test]
    fn post_save_sent_rows_remain_until_the_remote_list_contains_each_task() {
        let (_dir, mut app, _) = local_app();
        app.context.items = ["first", "second"]
            .iter()
            .map(|id| Item {
                id: id.to_string(),
                kind: "task".into(),
                title: format!("Send {id}"),
                task_id: id.to_string(),
                task_destination: "one".into(),
                ..Default::default()
            })
            .collect();
        app.link_tasks =
            json!({"scope":app.link_scope(),"remote":"saved","tasks":[],"linked":{"tasks":[]}});
        app.link_tasks_at = Instant::now();
        assert_eq!(
            app.actions_view("one")["items"].as_array().unwrap().len(),
            2
        );
        app.link_tasks["linked"]["tasks"] =
            json!([{"task_id":"first","title":"Send first","number":42}]);
        let view = app.actions_view("one");
        let local = view["items"].as_array().unwrap();
        assert_eq!(local.len(), 1);
        assert_eq!(local[0]["id"], "second");
        assert_eq!(local[0]["sent"], true);
        assert_eq!(view["linked"]["tasks"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn a_stale_window_cannot_restore_a_link_skipped_by_save() {
        let (_dir, mut app, id) = local_app();
        app.context.items.push(Item {
            id: "link".into(),
            kind: "link".into(),
            task_id: "gone".into(),
            ..Default::default()
        });
        app.store.save_context(&id, &app.context).unwrap();
        app.store
            .update_context(&id, |c| {
                c.items.clear();
                c.skipped_task_links = 1;
                Ok(())
            })
            .unwrap();
        app.context.notes = "Notes after the skip".into();
        app.close();
        let stored = app.store.context(&id).unwrap();
        assert!(stored.items.is_empty());
        assert_eq!(stored.skipped_task_links, 1);
        assert_eq!(stored.notes, "Notes after the skip");
    }

    #[test]
    fn an_accepted_local_link_stays_pending_until_saved() {
        let (_dir, mut app, id) = local_app();
        app.context.items.push(Item {
            id: "link".into(),
            kind: "link".into(),
            task_id: "task".into(),
            ..Default::default()
        });
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[{"id":"task","title":"Review pricing","number":42}]});
        app.link_tasks_at = Instant::now();
        assert_eq!(app.actions_view("one")["items"][0]["pending"], true);
        app.store
            .delivery(
                &id,
                &store::Delivery {
                    destination_id: "one".into(),
                    meeting_id: "saved".into(),
                    status: "sent".into(),
                    error: String::new(),
                    receipt: json!({"links":{"task":"linked"}}),
                },
            )
            .unwrap();
        app.reload_open();
        assert_eq!(app.actions_view("one")["items"][0]["pending"], false);
    }

    #[test]
    fn pending_link_rows_follow_durable_results_without_losing_window_edits() {
        let (_dir, mut app, id) = local_app();
        app.context.items.push(Item {
            id: "link".into(),
            kind: "link".into(),
            task_id: "task".into(),
            ..Default::default()
        });
        app.context
            .task_link_operations
            .push(tico_recorder_native::workspace::TaskLinkOperation {
                id: "action".into(),
                kind: "linked".into(),
                task_ids: vec!["task".into()],
            });
        app.store.save_context(&id, &app.context).unwrap();
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[{"id":"task","title":"Review pricing","number":42}],"linked":{"tasks":[]}});
        app.link_tasks_at = Instant::now();
        assert_eq!(app.actions_view("one")["items"][0]["pending"], true);
        // Even a stale remote row must show pending while a later action is uncommitted.
        app.link_tasks["linked"]["tasks"] = json!([{"task_id":"task","title":"Review pricing"}]);
        assert_eq!(
            app.actions_view("one")["linked"]["tasks"][0]["pending"],
            true
        );
        app.context.notes = "Unsaved window notes".into();
        app.store
            .update_context(&id, |c| {
                c.task_link_operations.clear();
                Ok(())
            })
            .unwrap();
        app.reload_open();
        assert!(app.context.task_link_operations.is_empty());
        assert_eq!(app.context.notes, "Unsaved window notes");
        assert_eq!(
            app.actions_view("one")["linked"]["tasks"][0]["pending"],
            false
        );
    }
}
