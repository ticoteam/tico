//! Screen capture is ephemeral and independent of Recorder's lock and audio threads.
use anyhow::{Context, Result, ensure};
use reqwest::blocking::Client;
use serde_json::{Value, json};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};
use tauri::Emitter;
use tico_recorder_native::{network::Tico, settings, signin};
#[cfg(target_os = "macos")]
#[path = "screen_macos.rs"]
mod native;

#[derive(Clone, Default)]
pub struct ScreenShare(Arc<Mutex<Option<Arc<Session>>>>);
pub struct Session {
    pub stopped: AtomicBool,
    pub meeting: String,
    pub destination: String,
    pub title: String,
    pub source: Mutex<String>,
    pub phase: Mutex<String>,
    pub error: Mutex<String>,
    pub latest: Mutex<Option<Vec<u8>>>,
    pub thumbnail: Mutex<Option<Vec<u8>>>,
    generation: Mutex<String>,
    access_token: Mutex<String>,
    pub app: Option<tauri::AppHandle>,
    pub capture_id: String,
    pub selected: AtomicBool,
    auth: Arc<dyn Fn(Option<&str>) -> Result<String> + Send + Sync>,
    audience: Mutex<String>,
    prefix: String,
    client: Client,
}
impl ScreenShare {
    pub fn view(&self) -> Value {
        self.0
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| {
                json!({
                    "capture_id":s.capture_id,"meeting":s.meeting,"title":s.title,"destination":s.destination,
                    "active":!s.stopped.load(Ordering::Acquire),"source":*s.source.lock().unwrap(),
                    "phase":*s.phase.lock().unwrap(),"error":*s.error.lock().unwrap()
                })
            })
            .unwrap_or(Value::Null)
    }
    pub fn dismiss(&self) {
        let mut current = self.0.lock().unwrap();
        if current
            .as_ref()
            .is_some_and(|s| s.stopped.load(Ordering::Acquire))
        {
            *current = None;
        }
    }
    pub fn stop(&self, reason: &str) {
        if let Some(s) = self.0.lock().unwrap().as_ref() {
            s.stop(reason);
        }
    }
    pub fn stop_meeting(&self, id: &str) {
        if let Some(s) = self.0.lock().unwrap().as_ref().filter(|s| s.meeting == id) {
            s.stop("");
        }
    }
    pub fn stop_and_wait(&self) {
        let session = self.0.lock().unwrap().clone();
        if let Some(session) = session {
            session.stop("");
            session.stop_remote(Duration::from_secs(1));
        }
    }
    pub fn stop_destination_and_wait(&self, id: &str) {
        let session = self
            .0
            .lock()
            .unwrap()
            .clone()
            .filter(|s| s.destination == id);
        if let Some(session) = session {
            session.stop("");
            session.stop_remote(Duration::from_secs(1));
        }
    }
    pub fn stop_destination(&self, id: &str) {
        if let Some(s) = self
            .0
            .lock()
            .unwrap()
            .as_ref()
            .filter(|s| s.destination == id)
        {
            s.stop("");
        }
    }
    pub fn active_for(&self, id: &str) -> bool {
        self.0
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|s| s.meeting == id && !s.stopped.load(Ordering::Acquire))
    }
    pub fn begin(
        &self,
        app: tauri::AppHandle,
        tico: Tico,
        meeting: String,
        live_id: String,
        title: String,
        expected_team: bool,
    ) -> Result<()> {
        ensure!(
            cfg!(target_os = "macos"),
            "Screen sharing needs macOS for now"
        );
        let mut current = self.0.lock().unwrap();
        ensure!(
            !current
                .as_ref()
                .is_some_and(|s| !s.stopped.load(Ordering::Acquire)),
            "Stop the current screen share first"
        );
        let session = Arc::new(Session {
            stopped: AtomicBool::new(false),
            meeting,
            destination: tico.destination.id.clone(),
            title,
            source: Mutex::new(String::new()),
            phase: Mutex::new("Checking Tico…".into()),
            error: Mutex::new(String::new()),
            latest: Mutex::new(None),
            thumbnail: Mutex::new(None),
            generation: Mutex::new(String::new()),
            access_token: Mutex::new(String::new()),
            app: Some(app.clone()),
            capture_id: uuid::Uuid::new_v4().to_string(),
            selected: AtomicBool::new(false),
            auth: {
                let id = tico.destination.id.clone();
                Arc::new(move |rejected| signin::token_after_rejection(&id, rejected))
            },
            audience: Mutex::new(String::new()),
            prefix: format!(
                "{}/api/v2/live-meetings/{live_id}/screen",
                settings::safe_url(&tico.destination.base_url)?
            ),
            client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .connect_timeout(Duration::from_secs(3))
                .timeout(Duration::from_secs(4))
                .build()?,
        });
        *current = Some(session.clone());
        std::thread::spawn(move || {
            let result = (|| -> Result<()> {
                let capability = session.request("", None, None)?;
                ensure!(
                    capability.status().is_success(),
                    "{}",
                    response_error(capability.status())
                );
                let limits: Value = capability.json()?;
                ensure!(
                    limits["visibility"] == if expected_team { "team" } else { "private" },
                    "The meeting audience changed; confirm screen sharing again"
                );
                ensure!(
                    limits["supported"] == true
                        && limits["limits"]["max_bytes"]
                            .as_u64()
                            .is_some_and(|n| n >= 1_000_000)
                        && limits["limits"]["max_edge"]
                            .as_u64()
                            .is_some_and(|n| n >= 1920),
                    "Update Tico to use this screen sharing format"
                );
                ensure!(
                    !session.stopped.load(Ordering::Acquire),
                    "Screen sharing cancelled"
                );
                let audience = limits["audience"]
                    .as_str()
                    .context("Update Tico to confirm screen sharing audiences")?;
                ensure!(
                    audience.len() == 64 && audience.bytes().all(|c| c.is_ascii_hexdigit()),
                    "Invalid screen audience receipt"
                );
                *session.audience.lock().unwrap() = audience.into();
                *session.phase.lock().unwrap() = "Choose a screen or window…".into();
                session.changed();
                #[cfg(target_os = "macos")]
                {
                    let capture = session.clone();
                    app.run_on_main_thread(move || native::pick(capture))?;
                }
                Ok(())
            })();
            if let Err(e) = result {
                session.stop(&e.to_string());
            }
        });
        Ok(())
    }
}
fn response_error(status: reqwest::StatusCode) -> &'static str {
    match status.as_u16() {
        401 => "Sign in to Tico again to share your screen",
        403 | 404 => "You no longer have access to this meeting, or this Tico needs an update",
        409 => "The screen share or its audience changed; confirm sharing again",
        410 => "Screen sharing ended because the meeting was paused, ended or its audience changed",
        413 => "The screen image is too large",
        429 => "Too many screen shares; stop another share and try again",
        _ => "Tico could not continue screen sharing",
    }
}
fn frame_timeout(bytes: usize) -> Duration {
    Duration::from_secs_f64((4. + bytes as f64 / 65_536.).min(15.))
}
impl Session {
    fn changed(&self) {
        if let Some(app) = &self.app {
            let _ = app.emit("changed", 0);
        }
    }
    pub fn stop(self: &Arc<Self>, error: &str) {
        if !self.stopped.swap(true, Ordering::AcqRel) {
            *self.latest.lock().unwrap() = None;
            *self.thumbnail.lock().unwrap() = None;
            *self.phase.lock().unwrap() = "Stopped".into();
            *self.error.lock().unwrap() = error.into();
            #[cfg(target_os = "macos")]
            if let Some(app) = &self.app {
                let id = self.capture_id.clone();
                let _ = app.run_on_main_thread(move || native::clear_if(&id));
            }
            self.changed();
            if let Some(app) = &self.app {
                let _ = app.emit(
                    "screen-preview",
                    json!({"capture_id":self.capture_id,"url":Value::Null}),
                );
            }
            // Independent of an in-flight frame or token renewal: stop the viewer immediately.
            let session = self.clone();
            std::thread::spawn(move || session.stop_remote(Duration::from_secs(1)));
        }
    }
    fn stop_remote(&self, timeout: Duration) {
        let generation = self.generation.lock().unwrap().clone();
        let token = self.access_token.lock().unwrap().clone();
        if generation.is_empty() || token.is_empty() {
            return;
        }
        // Use the current token directly, before sign-out deletes it. Never renew while quitting.
        let _ = self
            .client
            .post(format!("{}/stop?generation={generation}", self.prefix))
            .bearer_auth(token)
            .timeout(timeout)
            .send();
    }
    fn request(
        &self,
        path: &str,
        body: Option<Vec<u8>>,
        key: Option<&str>,
    ) -> Result<reqwest::blocking::Response> {
        let token = self.access_token.lock().unwrap().clone();
        let token = if token.is_empty() {
            let fresh = (self.auth)(None)?;
            *self.access_token.lock().unwrap() = fresh.clone();
            fresh
        } else {
            token
        };
        let url = format!("{}{path}", self.prefix);
        let timeout = body
            .as_ref()
            .map_or(Duration::from_secs(4), |bytes| frame_timeout(bytes.len()));
        let build = || {
            let mut request = if let Some(bytes) = &body {
                self.client
                    .post(&url)
                    .header("Content-Type", "image/jpeg")
                    .body(bytes.clone())
            } else if key.is_some() || path.contains("/alive") {
                self.client.post(&url)
            } else {
                self.client.get(&url)
            };
            if let Some(key) = key {
                request = request.header("Idempotency-Key", key);
            }
            request.timeout(timeout)
        };
        let response = build().bearer_auth(&token).send()?;
        if response.status() == reqwest::StatusCode::UNAUTHORIZED
            && !self.stopped.load(Ordering::Acquire)
        {
            let fresh = (self.auth)(Some(&token))?;
            *self.access_token.lock().unwrap() = fresh.clone();
            ensure!(
                !self.stopped.load(Ordering::Acquire),
                "Screen sharing cancelled"
            );
            return Ok(build().bearer_auth(fresh).send()?);
        }
        Ok(response)
    }
    fn heartbeat(self: &Arc<Self>, generation: String) {
        let session = self.clone();
        std::thread::spawn(move || {
            let mut good = Instant::now();
            while !session.stopped.load(Ordering::Acquire) {
                match session.request(&format!("/alive?generation={generation}"), None, None) {
                    Ok(response) if response.status().is_success() => good = Instant::now(),
                    Ok(response) if response.status().is_client_error() => {
                        session.stop(response_error(response.status()));
                        return;
                    }
                    _ if good.elapsed() >= Duration::from_secs(6) => {
                        session.stop("Screen sharing lost its connection to Tico");
                        return;
                    }
                    _ => {}
                }
                for _ in 0..20 {
                    if session.stopped.load(Ordering::Acquire) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        });
    }
    pub fn publish(self: &Arc<Self>) {
        let session = self.clone();
        std::thread::spawn(move || {
            let result = (|| -> Result<()> {
                ensure!(
                    !session.stopped.load(Ordering::Acquire),
                    "Screen sharing cancelled"
                );
                let audience = session.audience.lock().unwrap().clone();
                let response = session.request(
                    &format!("/start?expected_audience={audience}"),
                    None,
                    Some(&uuid::Uuid::new_v4().to_string()),
                )?;
                ensure!(
                    response.status().is_success(),
                    "{}",
                    response_error(response.status())
                );
                let value: Value = response.json()?;
                let generation = value["generation"]
                    .as_str()
                    .context("Tico did not return a screen share")?
                    .to_owned();
                *session.generation.lock().unwrap() = generation.clone();
                ensure!(
                    !session.stopped.load(Ordering::Acquire),
                    "Screen sharing cancelled"
                );
                *session.phase.lock().unwrap() = "Sharing".into();
                session.changed();
                session.heartbeat(generation.clone());
                let mut seq = 0_u64;
                let mut previous = Vec::new();
                let mut failed_since: Option<Instant> = None;
                let started = Instant::now();
                while !session.stopped.load(Ordering::Acquire) {
                    ensure!(
                        !previous.is_empty() || started.elapsed() < Duration::from_secs(10),
                        "No screen image arrived from macOS; try selecting the source again"
                    );
                    let frame = session.latest.lock().unwrap().take();
                    let Some(bytes) = frame.filter(|bytes| *bytes != previous) else {
                        std::thread::sleep(Duration::from_millis(100));
                        continue;
                    };
                    seq += 1;
                    let response = session.request(
                        &format!("/frame?generation={generation}&seq={seq}"),
                        Some(bytes.clone()),
                        None,
                    );
                    match response {
                        Ok(response) if response.status().is_success() => {
                            failed_since = None;
                            previous = bytes;
                            let thumbnail = session.thumbnail.lock().unwrap().take();
                            if !session.stopped.load(Ordering::Acquire)
                                && let (Some(app), Some(thumbnail)) = (&session.app, thumbnail)
                            {
                                use base64::Engine;
                                let _ = app.emit("screen-preview", json!({"capture_id":session.capture_id,"url":format!("data:image/jpeg;base64,{}", base64::engine::general_purpose::STANDARD.encode(thumbnail))}));
                            }
                        }
                        Ok(response) if response.status().is_client_error() => {
                            anyhow::bail!("{}", response_error(response.status()))
                        }
                        _ => {
                            let mut latest = session.latest.lock().unwrap();
                            if !session.stopped.load(Ordering::Acquire) {
                                latest.get_or_insert(bytes);
                            }
                            ensure!(
                                failed_since.get_or_insert_with(Instant::now).elapsed()
                                    < Duration::from_secs(15),
                                "Screen sharing could not upload images; check your connection"
                            );
                        }
                    }
                    std::thread::sleep(Duration::from_millis(200));
                }
                Ok(())
            })();
            session.stop(result.err().map(|e| e.to_string()).as_deref().unwrap_or(""));
            // Covers Stop while the start response was pending and no generation was known yet.
            session.stop_remote(Duration::from_secs(1));
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    fn session(prefix: String) -> Arc<Session> {
        Arc::new(Session {
            stopped: AtomicBool::new(false),
            meeting: "one".into(),
            destination: "fixture".into(),
            title: "Fixture".into(),
            source: Mutex::new("Synthetic window".into()),
            phase: Mutex::new("Starting".into()),
            error: Mutex::new(String::new()),
            latest: Mutex::new(None),
            thumbnail: Mutex::new(None),
            generation: Mutex::new(String::new()),
            access_token: Mutex::new("synthetic-test-token".into()),
            app: None,
            capture_id: "capture-one".into(),
            selected: AtomicBool::new(false),
            auth: Arc::new(|_| Ok("synthetic-test-token".into())),
            audience: Mutex::new(String::new()),
            prefix,
            client: Client::builder()
                .timeout(Duration::from_millis(500))
                .build()
                .unwrap(),
        })
    }
    #[test]
    fn stopping_is_immediate_even_with_a_blocked_sender_and_never_retargets() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let s = session(format!("http://{}", listener.local_addr().unwrap()));
        let share = ScreenShare(Arc::new(Mutex::new(Some(s.clone()))));
        let (accepted, blocked) = std::sync::mpsc::channel();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut head = vec![];
            loop {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                head.push(byte[0]);
                if head.ends_with(b"\r\n\r\n") {
                    break;
                }
            }
            accepted.send(()).unwrap();
            // The sender is in a real blocked read. UI and Stop must still be usable.
            std::thread::sleep(Duration::from_millis(300));
            let _ = socket.write_all(
                b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            );
        });
        s.publish();
        blocked.recv_timeout(Duration::from_secs(2)).unwrap();
        let began = Instant::now();
        assert_eq!(share.view()["active"], true);
        share.stop_meeting("other-meeting");
        assert!(share.active_for("one"));
        share.stop_meeting("one");
        assert_eq!(share.view()["active"], false);
        assert!(began.elapsed() < Duration::from_millis(100));
        assert!(s.latest.lock().unwrap().is_none());
        share.stop_destination("fixture");
        assert!(!share.active_for("one"));
        server.join().unwrap();
    }
    #[test]
    fn a_new_handle_never_resumes_capture_and_signout_stops_only_its_connection() {
        let s = session("http://127.0.0.1:1".into());
        let share = ScreenShare(Arc::new(Mutex::new(Some(s.clone()))));
        share.stop_destination("other-connection");
        assert!(share.active_for("one"));
        share.stop_destination("fixture");
        assert_eq!(share.view()["active"], false);
        share.dismiss();
        assert!(share.view().is_null());
        assert!(ScreenShare::default().view().is_null());
    }
    #[test]
    fn slow_frames_keep_the_lease_alive_and_stop_revokes_before_upload_finishes() {
        use std::sync::atomic::AtomicUsize;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let s = session(format!("http://{}", listener.local_addr().unwrap()));
        let beats = Arc::new(AtomicUsize::new(0));
        let frames = Arc::new(AtomicUsize::new(0));
        let complete = Arc::new(AtomicBool::new(false));
        let done = Arc::new(AtomicBool::new(false));
        let (stopped_tx, stopped_rx) = std::sync::mpsc::channel();
        let server = {
            let (beats, frames, complete, done) = (
                beats.clone(),
                frames.clone(),
                complete.clone(),
                done.clone(),
            );
            std::thread::spawn(move || {
                let mut workers = vec![];
                while !done.load(Ordering::Acquire) {
                    let (mut socket, _) = match listener.accept() {
                        Ok(pair) => pair,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(5));
                            continue;
                        }
                        Err(error) => panic!("{error}"),
                    };
                    let (beats, frames, complete, done, stopped_tx) = (
                        beats.clone(),
                        frames.clone(),
                        complete.clone(),
                        done.clone(),
                        stopped_tx.clone(),
                    );
                    workers.push(std::thread::spawn(move || {
                        socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                        let mut bytes = vec![];
                        while !bytes.ends_with(b"\r\n\r\n") { let mut one = [0]; if socket.read_exact(&mut one).is_err() { return; } bytes.push(one[0]); }
                        let head = String::from_utf8(bytes).unwrap();
                        let line = head.lines().next().unwrap();
                        if line.contains("/frame?") {
                            frames.fetch_add(1, Ordering::AcqRel);
                            let length: usize = head.lines().find_map(|l| l.split_once(':').filter(|(k,_)| k.eq_ignore_ascii_case("Content-Length")).map(|(_,v)| v.trim().parse().unwrap())).unwrap();
                            let mut remaining = length;
                            while remaining > 0 {
                                if done.load(Ordering::Acquire) { return; }
                                let count = remaining.min(8192);
                                let mut chunk = vec![0; count];
                                if socket.read_exact(&mut chunk).is_err() { return; }
                                remaining -= count;
                                std::thread::sleep(Duration::from_millis(100)); // about 80 KB/s
                            }
                            complete.store(true, Ordering::Release);
                        } else if line.contains("/alive?") { beats.fetch_add(1, Ordering::AcqRel); }
                        else if line.contains("/stop?") { let _ = stopped_tx.send(()); }
                        if line.contains("/start?") {
                            let body = br#"{"generation":"synthetic-lease"}"#;
                            let _ = write!(socket,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len());
                            let _ = socket.write_all(body);
                        } else { let _ = socket.write_all(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"); }
                    }));
                }
                for worker in workers {
                    worker.join().unwrap();
                }
            })
        };
        *s.latest.lock().unwrap() = Some(vec![0; 400_000]);
        s.publish();
        let deadline = Instant::now() + Duration::from_secs(12);
        while !complete.load(Ordering::Acquire)
            && Instant::now() < deadline
            && !s.stopped.load(Ordering::Acquire)
        {
            std::thread::sleep(Duration::from_millis(10));
        }
        let succeeded = complete.load(Ordering::Acquire)
            && !s.stopped.load(Ordering::Acquire)
            && beats.load(Ordering::Acquire) >= 2;
        // A second upload is still pending when Stop has to reach the server.
        *s.latest.lock().unwrap() = Some(vec![1; 400_000]);
        while frames.load(Ordering::Acquire) < 2 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let began = Instant::now();
        s.stop("");
        let promptly_stopped = stopped_rx.recv_timeout(Duration::from_secs(1)).is_ok()
            && began.elapsed() < Duration::from_secs(1);
        done.store(true, Ordering::Release);
        server.join().unwrap();
        assert!(
            succeeded,
            "A five-second frame upload must not expire its independent heartbeat"
        );
        assert!(
            promptly_stopped,
            "Stop must reach the server while a frame upload is blocked"
        );
        assert!(s.latest.lock().unwrap().is_none());
    }
}
