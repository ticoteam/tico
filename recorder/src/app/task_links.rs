use super::*;

impl Recorder {
    pub(super) fn context_links(context: &Context) -> Value {
        json!([
            context.task_link_operations,
            context
                .items
                .iter()
                .map(|i| (
                    &i.id,
                    &i.kind,
                    &i.task_id,
                    &i.proposed_task_id,
                    &i.task_destination
                ))
                .collect::<Vec<_>>(),
            context.kept_on_mac,
            context.live_discarded,
            context.live_destination,
            context.skipped_task_links,
        ])
    }

    pub(super) fn task_links_snapshot(&self) -> Value {
        json!([
            Self::context_links(&self.context),
            self.current.as_ref().map(|r| {
                r.deliveries
                    .iter()
                    .map(|d| {
                        (
                            &d.destination_id,
                            &d.meeting_id,
                            live::saved_in_tico(d),
                            &d.receipt["links"],
                        )
                    })
                    .collect::<Vec<_>>()
            })
        ])
    }

    pub(super) fn invalidate_task_links(&mut self) {
        self.link_tasks_generation += 1;
        self.link_tasks_dirty = true;
    }

    pub(super) fn saved_items_with_links(&self) -> Value {
        let mut data = self.saved_items.clone();
        if let Some(sections) = data["sections"].as_object_mut() {
            for rows in sections.values_mut().filter_map(Value::as_array_mut) {
                for row in rows {
                    if let Some(task) = row["proposed_task_id"]
                        .as_str()
                        .and_then(|id| self.linked_task(id))
                    {
                        row["proposed_number"] = task["number"].clone();
                    }
                }
            }
        }
        data
    }

    pub(super) fn pending_task_link(&self, task: &str) -> bool {
        !task.is_empty()
            && self.link_activity.destination == self.router()
            && self.link_activity.sending
            && self.link_activity.task_ids.iter().any(|id| id == task)
    }

    pub(super) fn task_link_error(&self, task: &str) -> &str {
        if self.link_activity.destination == self.router()
            && self.link_activity.task_ids.iter().any(|id| id == task)
        {
            &self.link_activity.error
        } else {
            ""
        }
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
                task["error"] = json!(self.task_link_error(task["task_id"].as_str().unwrap_or("")));
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
            self.items_epoch,
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
            || (!self.link_tasks_dirty && self.link_tasks_at.elapsed() < Duration::from_secs(60))
        {
            return;
        }
        let Some(id) = self.meeting.clone() else {
            return;
        };
        let mut ids: Vec<String> = self
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
        ids.extend(
            self.saved_items["sections"]
                .as_object()
                .into_iter()
                .flat_map(|s| s.values())
                .filter_map(Value::as_array)
                .flatten()
                .filter_map(|row| row["proposed_task_id"].as_str().map(str::to_owned)),
        );
        ids.sort();
        ids.dedup();
        let scope = self.link_scope();
        let generation = self.link_tasks_generation;
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
        self.link_tasks_at = Instant::now();
        self.link_tasks_dirty = false;
        self.job_for("task-links", Some(id), move || {
            let data = (|| -> anyhow::Result<Value> {
                let tico = Tico::saved(&config, &destination)?;
                let tasks = tico.readable_tasks(&ids)?;
                let browser = tico.browser_url()?;
                let linked = remote.as_ref().map(|rid| tico.linked_tasks(rid, None)).transpose()?;
                Ok(json!({"tasks":tasks,"scope":scope,"browser":browser,"remote":remote,"linked":linked}))
            })();
            // Even a failed fetch carries its generation, so it cannot clear a newer view.
            Ok(json!({"scope":scope,"generation":generation,"data":data.ok()}))
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
        self.invalidate_task_links();
    }
    pub fn accept_link_item(&mut self, item_id: &str) {
        self.save_context();
        let item_id = item_id.to_owned();
        self.network_job("link", move |tico, store, id| {
            tico.accept_task_link(&store, &id, &item_id)?;
            Ok(json!({"message":"Linked"}))
        });
        self.invalidate_task_links();
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
        self.invalidate_task_links();
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
        let generation = self.link_tasks_generation;
        self.network_job("more-links", move |tico, _, _| {
            Ok(json!({"scope":scope,"generation":generation,"linked":tico.linked_tasks(&remote, Some(&cursor))?}))
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
    fn an_accepted_local_link_waiting_for_save_has_no_spinner() {
        let (_dir, mut app, id) = local_app();
        app.context.items.push(Item {
            id: "link".into(),
            kind: "link".into(),
            task_id: "task".into(),
            ..Default::default()
        });
        app.store.save_context(&id, &app.context).unwrap();
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[{"id":"task","title":"Review pricing","number":42}]});
        app.link_tasks_at = Instant::now();
        assert_eq!(app.actions_view("one")["items"][0]["pending"], false);
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
        assert_eq!(app.actions_view("one")["items"][0]["pending"], false);
        // Queued actions show the task status while no request is in flight.
        app.link_tasks["linked"]["tasks"] = json!([{"task_id":"task","title":"Review pricing"}]);
        assert_eq!(
            app.actions_view("one")["linked"]["tasks"][0]["pending"],
            false
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
    fn tick_until(app: &mut Recorder, done: impl Fn(&Recorder) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            let mark = app.store.changes();
            app.refresh = Instant::now() - Duration::from_secs(1);
            app.tick();
            if done(app) {
                return;
            }
            assert!(Instant::now() < deadline, "Recorder did not refresh");
            app.store.wait_changes(mark, Duration::from_millis(20));
        }
    }

    #[test]
    fn receipt_only_changes_refresh_links_and_reject_an_older_fetch() {
        let (_dir, mut app, id) = local_app();
        app.context.notes = "Window draft".into();
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[],"linked":{"tasks":[]}});
        app.link_tasks_at = Instant::now();
        let generation = app.link_tasks_generation;
        let expected = app.link_tasks.clone();
        let mut stale = expected.clone();
        stale["tasks"] = json!([{"id":"stale","title":"Before the link"}]);
        // No outbox change: a Save may acknowledge a batch before it removes the operation.
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
        tick_until(&mut app, |a| {
            a.current.as_ref().unwrap().deliveries.len() == 1
        });
        assert!(app.link_tasks_generation > generation);
        assert!(app.link_tasks_dirty);
        assert_eq!(app.context.notes, "Window draft");
        app.tx
            .send(Reply {
                kind: "task-links".into(),
                generation: app.items_generation,
                connection: app.saved_connection(),
                meeting: Some(id),
                result: Ok(json!({"scope":app.link_scope(),"generation":generation,"data":stale})),
            })
            .unwrap();
        app.tick();
        assert!(app.link_tasks_dirty);
        assert_eq!(app.link_tasks, expected);
    }

    #[test]
    fn live_link_saved_by_a_job_clears_the_spinner_and_shows_send_errors_without_reopening() {
        use std::{
            io::{Read, Write},
            net::TcpListener,
        };
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let (arrived, requests) = mpsc::channel();
        let (release, held) = mpsc::channel();
        let server = std::thread::spawn(move || {
            let steps = [
                (
                    "GET /api/v2/tasks?",
                    200,
                    json!({"tasks":[{"id":"task","title":"Review pricing","number":42,"status":"open"}]}),
                    false,
                ),
                ("GET /api/v2/me ", 200, json!({"actor":"human:ana"}), false),
                ("POST /api/v2/meetings/saved/review ", 200, json!({}), false),
                (
                    "POST /api/v2/meetings/saved/tasks ",
                    503,
                    json!({"error":{"code":"unavailable"}}),
                    true,
                ),
                ("GET /api/v2/me ", 200, json!({"actor":"human:ana"}), false),
                (
                    "POST /api/v2/meetings/saved/tasks ",
                    200,
                    json!({"results":[{"status":200}]}),
                    true,
                ),
            ];
            for (prefix, status, body, gated) in steps {
                let deadline = Instant::now() + Duration::from_secs(8);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(Instant::now() < deadline, "Missing {prefix}");
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(e) => panic!("{e}"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut headers = vec![];
                while !headers.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    stream.read_exact(&mut byte).unwrap();
                    headers.push(byte[0]);
                }
                let headers = String::from_utf8(headers).unwrap();
                assert!(headers.starts_with(prefix), "{headers}");
                let size = headers
                    .lines()
                    .filter_map(|line| line.split_once(':'))
                    .find(|(key, _)| key.eq_ignore_ascii_case("content-length"))
                    .map(|(_, value)| value.trim().parse::<usize>().unwrap())
                    .unwrap_or(0);
                stream.read_exact(&mut vec![0; size]).unwrap();
                if gated {
                    arrived.send(()).unwrap();
                    held.recv_timeout(Duration::from_secs(8)).unwrap();
                }
                let body = body.to_string();
                write!(stream, "HTTP/1.1 {status} Reply\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            }
        });
        let (_dir, mut app, id) = local_app();
        let mut destination = app.config.destinations[0].clone();
        destination.base_url = url;
        let tico = Arc::new(Tico::new(destination, "synthetic-token".into()).unwrap());
        app.store
            .save_audio(&id, Channel::Microphone, 0, 1000, &vec![0.2; 16000], 16000)
            .unwrap();
        let chunk = app.store.claim_transcription().unwrap().unwrap();
        app.store
            .transcription_result(&chunk, "Review pricing", "", json!({}))
            .unwrap();
        app.store
            .update_context(&id, |c| {
                c.live_id = "live".into();
                c.live_state = "live".into();
                c.live_destination = "one".into();
                Ok(())
            })
            .unwrap();
        let queued = tico.clone();
        let store = app.store.clone();
        let recording = id.clone();
        assert!(app.job("link", move || {
            queued.queue_links(&store, &recording, &["task".into()])?;
            Ok(json!({}))
        }));
        tick_until(&mut app, |a| !a.busy);
        app.link_tasks = json!({"scope":app.link_scope(),"tasks":[{"id":"task","title":"Review pricing","number":42,"status":"open"}],"linked":{"tasks":[]}});
        app.link_tasks_at = Instant::now();
        assert_eq!(app.actions_view("one")["items"][0]["pending"], false);
        assert_eq!(app.actions_view("one")["items"][0]["status"], "open");
        assert_eq!(app.context.task_link_operations.len(), 1);
        // Finish files a pending meeting. Save below uses the production send job and receipt path.
        app.store
            .update_context(&id, |c| {
                c.live_state = "ended".into();
                Ok(())
            })
            .unwrap();
        app.store
            .delivery(
                &id,
                &store::Delivery {
                    destination_id: "one".into(),
                    meeting_id: "saved".into(),
                    status: "pending".into(),
                    error: String::new(),
                    receipt: json!({"source":"live"}),
                },
            )
            .unwrap();
        for succeeds in [false, true] {
            let sending = tico.clone();
            let store = app.store.clone();
            let recording = id.clone();
            assert!(app.job("send", move || {
                sending.send(&store, &recording, false)?;
                Ok(json!({}))
            }));
            requests.recv_timeout(Duration::from_secs(8)).unwrap();
            app.tick();
            assert_eq!(app.actions_view("one")["items"][0]["pending"], true);
            release.send(()).unwrap();
            tick_until(&mut app, |a| !a.busy);
            let view = app.actions_view("one");
            assert_eq!(view["items"][0]["pending"], false);
            assert_eq!(
                view["items"][0]["error"].as_str().unwrap().is_empty(),
                succeeds
            );
        }
        server.join().unwrap();
        assert_eq!(app.meeting.as_deref(), Some(id.as_str()));
        assert!(app.context.task_link_operations.is_empty());
        assert_eq!(
            app.current.as_ref().unwrap().deliveries[0].receipt["links"]["task"],
            "linked"
        );
    }

    #[test]
    fn save_recovery_drafts_survive_a_stale_window_and_connection_epoch_changes() {
        let (_dir, mut app, id) = local_app();
        app.context.items.push(Item {
            id: "a".into(),
            kind: "task".into(),
            title: "My current fields".into(),
            ..Default::default()
        });
        app.store.save_context(&id, &app.context).unwrap();
        let closed = json!({"item":{"id":"server-a","status":"proposed","version":2,"updated_by":"human:ben"},"text":"My current fields","detail":{"owner":"human:ana"}});
        app.store
            .update_context(&id, |c| {
                c.items[0].closed_draft = Some(closed.clone());
                Ok(())
            })
            .unwrap();
        app.save_context();
        assert_eq!(app.actions_view("Acme")["items"][0]["closed_draft"], closed);
        let scope = app.link_scope();
        app.reset_saved();
        assert_ne!(scope, app.link_scope());
        app.close();
        app.open(&id).unwrap();
        assert_eq!(app.context.items[0].closed_draft, Some(closed));
    }
}
