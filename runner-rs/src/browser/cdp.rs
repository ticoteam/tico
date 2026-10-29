//! One Chrome shared by many DevTools clients. Chrome speaks CDP over `--remote-debugging-pipe` (fd 3 in, fd 4
//! out, NUL-separated JSON), so it has no debugging port any process could reach. Each client (a websocket on the
//! endpoint, or the runner itself) gets its own browser session (`Target.attachToBrowserTarget`): its commands go
//! to that session, ids are renumbered so replies find their way back, and a client sees only events of sessions it
//! opened. A client cannot close the shared browser (`Browser.close` is answered here) or use another's sessions.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::{mpsc, oneshot, watch};

/// The runner's own calls (fill, status).
const RUNNER: u64 = 0;
/// Methods on a client's browser session that would end the browser for every client.
const KEPT: &[&str] = &["Browser.close", "Browser.crash", "Browser.crashGpuProcess"];
const ATTACHES: &[&str] = &["Target.attachToTarget", "Target.attachToBrowserTarget"];
const CALL_TIMEOUT: Duration = Duration::from_secs(30);
/// A single CDP message larger than this (a full-page screenshot is a few MB) ends the connection.
const MESSAGE_LIMIT: usize = 256 * 1024 * 1024;

enum Waiter {
    Client { client: u64, id: Value, method: String },
    Call(oneshot::Sender<Value>),
}

struct Peer {
    tx: mpsc::UnboundedSender<String>,
    root: String,
}

#[derive(Default)]
pub struct Mux {
    pending: HashMap<u64, Waiter>,
    peers: HashMap<u64, Peer>,
    owners: HashMap<String, u64>,
    next_peer: u64,
}

/// Where a message from Chrome goes.
pub enum Route {
    Client(mpsc::UnboundedSender<String>, String),
    Call(oneshot::Sender<Value>, Value),
    Drop,
}

fn error_reply(id: &Value, message: &str) -> String {
    json!({"id": id, "error": {"code": -32001, "message": message}}).to_string()
}

impl Mux {
    /// A client's message, renumbered for Chrome as `wire_id`; Err is the reply the client gets instead (None: none).
    pub fn client_message(&mut self, client: u64, text: &str, wire_id: u64) -> Result<String, Option<String>> {
        let Ok(Value::Object(mut msg)) = serde_json::from_str::<Value>(text) else { return Err(None) };
        let id = msg.get("id").cloned().unwrap_or(Value::Null);
        if !id.is_u64() && !id.is_i64() {
            return Err(Some(error_reply(&id, "Message must have integer 'id' property")));
        }
        let Some(peer) = self.peers.get(&client) else { return Err(None) };
        let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or_default().to_string();
        let session = match msg.get("sessionId").and_then(|s| s.as_str()) {
            None => peer.root.clone(),
            Some(s) if self.owners.get(s) == Some(&client) => s.to_string(),
            Some(_) => return Err(Some(error_reply(&id, "Session with given id not found."))),
        };
        if session == peer.root && KEPT.contains(&method.as_str()) {
            return Err(Some(json!({"id": id, "result": {}}).to_string()));
        }
        msg.insert("id".into(), json!(wire_id));
        msg.insert("sessionId".into(), json!(session));
        self.pending.insert(wire_id, Waiter::Client { client, id, method });
        Ok(Value::Object(msg).to_string())
    }

    /// The runner's own command.
    pub fn call(&mut self, wire_id: u64, method: &str, params: Value, session: Option<&str>, tx: oneshot::Sender<Value>) -> String {
        self.pending.insert(wire_id, Waiter::Call(tx));
        let mut msg = json!({"id": wire_id, "method": method, "params": params});
        if let Some(s) = session {
            msg["sessionId"] = json!(s);
        }
        msg.to_string()
    }

    /// A command whose reply nobody waits for.
    pub fn fire(&mut self, wire_id: u64, method: &str, params: Value) -> String {
        json!({"id": wire_id, "method": method, "params": params}).to_string()
    }

    pub fn chrome_message(&mut self, mut msg: Value) -> Route {
        if let Some(wire) = msg.get("id").and_then(|i| i.as_u64()) {
            return match self.pending.remove(&wire) {
                Some(Waiter::Call(tx)) => Route::Call(tx, msg),
                Some(Waiter::Client { client, id, method }) => {
                    let Some(peer) = self.peers.get(&client) else { return Route::Drop };
                    if ATTACHES.contains(&method.as_str()) {
                        if let Some(s) = msg["result"]["sessionId"].as_str() {
                            self.owners.insert(s.to_string(), client);
                        }
                    }
                    msg["id"] = id;
                    if msg.get("sessionId").and_then(|s| s.as_str()) == Some(peer.root.as_str()) {
                        msg.as_object_mut().unwrap().remove("sessionId");
                    }
                    Route::Client(peer.tx.clone(), msg.to_string())
                }
                None => Route::Drop,
            };
        }
        let Some(session) = msg.get("sessionId").and_then(|s| s.as_str()).map(String::from) else { return Route::Drop };
        let Some(&client) = self.owners.get(&session) else { return Route::Drop };
        let child = msg["params"]["sessionId"].as_str().map(String::from);
        match (msg["method"].as_str().unwrap_or_default(), child) {
            ("Target.attachedToTarget", Some(c)) => {
                self.owners.insert(c, client);
            }
            ("Target.detachedFromTarget", Some(c)) => {
                self.owners.remove(&c);
            }
            _ => {}
        }
        if client == RUNNER {
            return Route::Drop;
        }
        let Some(peer) = self.peers.get(&client) else { return Route::Drop };
        if session == peer.root {
            msg.as_object_mut().unwrap().remove("sessionId");
        }
        Route::Client(peer.tx.clone(), msg.to_string())
    }

    pub fn add_peer(&mut self, root: &str, tx: mpsc::UnboundedSender<String>) -> u64 {
        self.next_peer += 1;
        let client = self.next_peer;
        self.peers.insert(client, Peer { tx, root: root.to_string() });
        self.owners.insert(root.to_string(), client);
        client
    }

    /// Forget a client; its browser session, to detach, is returned.
    pub fn remove_peer(&mut self, client: u64) -> Option<String> {
        let peer = self.peers.remove(&client)?;
        self.owners.retain(|_, c| *c != client);
        self.pending.retain(|_, w| !matches!(w, Waiter::Client { client: c, .. } if *c == client));
        Some(peer.root)
    }

    pub fn own(&mut self, session: &str) {
        self.owners.insert(session.to_string(), RUNNER);
    }

    pub fn disown(&mut self, session: &str) {
        self.owners.remove(session);
    }
}

pub struct Browser {
    out: mpsc::UnboundedSender<String>,
    mux: Mutex<Mux>,
    next: AtomicU64,
    gone: watch::Receiver<bool>,
}

impl Browser {
    /// Talk to Chrome over its pipe ends: `to_chrome` (its fd 3) and `from_chrome` (its fd 4).
    pub fn start(mut to_chrome: tokio::net::unix::pipe::Sender, mut from_chrome: tokio::net::unix::pipe::Receiver) -> Arc<Browser> {
        let (out, mut queue) = mpsc::unbounded_channel::<String>();
        let (gone_tx, gone) = watch::channel(false);
        let browser = Arc::new(Browser { out, mux: Mutex::new(Mux::default()), next: AtomicU64::new(1), gone });
        tokio::spawn(async move {
            while let Some(text) = queue.recv().await {
                let mut bytes = text.into_bytes();
                bytes.push(0);
                if to_chrome.write_all(&bytes).await.is_err() {
                    break;
                }
            }
        });
        let weak = Arc::downgrade(&browser);
        tokio::spawn(async move {
            let mut buf: Vec<u8> = Vec::with_capacity(1 << 16);
            let mut chunk = vec![0u8; 1 << 16];
            'read: loop {
                let n = match from_chrome.read(&mut chunk).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => n,
                };
                buf.extend_from_slice(&chunk[..n]);
                while let Some(end) = buf.iter().position(|b| *b == 0) {
                    let message: Vec<u8> = buf.drain(..=end).collect();
                    let Some(b) = weak.upgrade() else { break 'read };
                    b.incoming(&message[..message.len() - 1]);
                }
                if buf.len() > MESSAGE_LIMIT {
                    break;
                }
            }
            let _ = gone_tx.send(true);
            if let Some(b) = weak.upgrade() {
                // Every waiting call fails and every client's connection ends.
                *b.mux.lock().unwrap() = Mux::default();
            }
        });
        browser
    }

    fn incoming(&self, raw: &[u8]) {
        let Ok(msg) = serde_json::from_slice::<Value>(raw) else { return };
        let route = self.mux.lock().unwrap().chrome_message(msg);
        match route {
            Route::Client(tx, text) => {
                let _ = tx.send(text);
            }
            Route::Call(tx, msg) => {
                let _ = tx.send(msg);
            }
            Route::Drop => {}
        }
    }

    pub fn alive(&self) -> bool {
        !*self.gone.borrow()
    }

    pub async fn closed(&self) {
        let mut gone = self.gone.clone();
        let _ = gone.wait_for(|g| *g).await;
    }

    fn wire_id(&self) -> u64 {
        self.next.fetch_add(1, Ordering::SeqCst)
    }

    /// The runner's own CDP call; Err carries Chrome's error message (never a parameter).
    pub async fn call(&self, method: &str, params: Value, session: Option<&str>) -> Result<Value, String> {
        self.call_within(method, params, session, CALL_TIMEOUT).await
    }

    pub async fn call_within(&self, method: &str, params: Value, session: Option<&str>, wait: Duration) -> Result<Value, String> {
        if !self.alive() {
            return Err("the browser is not running".into());
        }
        let (tx, rx) = oneshot::channel();
        let id = self.wire_id();
        let text = self.mux.lock().unwrap().call(id, method, params, session, tx);
        if self.out.send(text).is_err() {
            return Err("the browser is not running".into());
        }
        let reply = match tokio::time::timeout(wait, rx).await {
            Ok(Ok(r)) => r,
            Ok(Err(_)) => return Err("the browser stopped".into()),
            Err(_) => {
                self.mux.lock().unwrap().pending.remove(&id);
                return Err(format!("{method} did not answer"));
            }
        };
        if let Some(e) = reply.get("error") {
            return Err(e["message"].as_str().unwrap_or("CDP error").chars().take(200).collect());
        }
        Ok(reply["result"].clone())
    }

    /// Attach the runner to a target (flattened); events on that session are dropped.
    pub async fn attach(&self, target: &str) -> Result<String, String> {
        let r = self.call("Target.attachToTarget", json!({"targetId": target, "flatten": true}), None).await?;
        let session = r["sessionId"].as_str().ok_or("no session")?.to_string();
        self.mux.lock().unwrap().own(&session);
        Ok(session)
    }

    pub async fn detach(&self, session: &str) {
        let _ = self.call_within("Target.detachFromTarget", json!({"sessionId": session}), None, Duration::from_secs(5)).await;
        self.mux.lock().unwrap().disown(session);
    }

    /// A new client: its browser session and the channel of what Chrome sends it.
    pub async fn connect(&self) -> Result<(u64, mpsc::UnboundedReceiver<String>), String> {
        let r = self.call("Target.attachToBrowserTarget", json!({}), None).await?;
        let root = r["sessionId"].as_str().ok_or("no browser session")?.to_string();
        let (tx, rx) = mpsc::unbounded_channel();
        let client = self.mux.lock().unwrap().add_peer(&root, tx);
        Ok((client, rx))
    }

    /// A client's message; the Err text, when there is one, goes straight back to it.
    pub fn send(&self, client: u64, text: &str) -> Result<(), Option<String>> {
        let id = self.wire_id();
        let wire = self.mux.lock().unwrap().client_message(client, text, id)?;
        self.out.send(wire).map_err(|_| None)
    }

    pub fn disconnect(&self, client: u64) {
        let root = self.mux.lock().unwrap().remove_peer(client);
        if let Some(root) = root {
            let id = self.wire_id();
            let text = self.mux.lock().unwrap().fire(id, "Target.detachFromTarget", json!({"sessionId": root}));
            let _ = self.out.send(text);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn peer(mux: &mut Mux, root: &str) -> (u64, mpsc::UnboundedReceiver<String>) {
        let (tx, rx) = mpsc::unbounded_channel();
        (mux.add_peer(root, tx), rx)
    }

    fn delivered(route: Route) -> Option<Value> {
        match route {
            Route::Client(tx, text) => {
                drop(tx);
                Some(serde_json::from_str(&text).unwrap())
            }
            _ => None,
        }
    }

    #[test]
    fn replies_return_to_their_client_with_its_own_id_and_no_session_of_its_root() {
        let mut mux = Mux::default();
        let (a, _ra) = peer(&mut mux, "ROOT-A");
        let (b, _rb) = peer(&mut mux, "ROOT-B");
        let wire = mux.client_message(a, r#"{"id":7,"method":"Target.getTargets"}"#, 100).unwrap();
        let sent: Value = serde_json::from_str(&wire).unwrap();
        assert_eq!((sent["id"].as_u64(), sent["sessionId"].as_str()), (Some(100), Some("ROOT-A")));
        mux.client_message(b, r#"{"id":7,"method":"Target.getTargets"}"#, 101).unwrap();
        let back = delivered(mux.chrome_message(json!({"id": 100, "sessionId": "ROOT-A", "result": {"targetInfos": []}}))).unwrap();
        assert_eq!(back, json!({"id": 7, "result": {"targetInfos": []}}));
        // An unknown wire id goes nowhere.
        assert!(delivered(mux.chrome_message(json!({"id": 999, "result": {}}))).is_none());
    }

    #[test]
    fn a_client_sees_only_its_own_sessions_and_cannot_use_anothers() {
        let mut mux = Mux::default();
        let (a, _ra) = peer(&mut mux, "ROOT-A");
        let (b, _rb) = peer(&mut mux, "ROOT-B");
        mux.client_message(a, r#"{"id":1,"method":"Target.attachToTarget","params":{"targetId":"T","flatten":true}}"#, 10).unwrap();
        delivered(mux.chrome_message(json!({"id": 10, "sessionId": "ROOT-A", "result": {"sessionId": "PAGE-A"}}))).unwrap();
        let refused = mux.client_message(b, r#"{"id":2,"method":"Runtime.evaluate","sessionId":"PAGE-A"}"#, 11).unwrap_err().unwrap();
        assert!(refused.contains("Session with given id not found"));
        assert!(mux.client_message(a, r#"{"id":3,"method":"Runtime.evaluate","sessionId":"PAGE-A"}"#, 12).is_ok());
        // Events on A's page reach A only; auto-attached children become A's.
        let event = delivered(mux.chrome_message(json!({"method": "Target.attachedToTarget", "sessionId": "ROOT-A", "params": {"sessionId": "WORKER-A"}}))).unwrap();
        assert!(event.get("sessionId").is_none());
        assert!(mux.client_message(a, r#"{"id":4,"method":"Runtime.enable","sessionId":"WORKER-A"}"#, 13).is_ok());
        assert!(mux.client_message(b, r#"{"id":4,"method":"Runtime.enable","sessionId":"WORKER-A"}"#, 14).is_err());
        // Browser-level events with no session, and events of the runner's own sessions, go to no client.
        assert!(delivered(mux.chrome_message(json!({"method": "Target.targetCreated", "params": {}}))).is_none());
        mux.own("RUNNER-PAGE");
        assert!(delivered(mux.chrome_message(json!({"method": "Page.loadEventFired", "sessionId": "RUNNER-PAGE", "params": {}}))).is_none());
        // A gone client's sessions are forgotten.
        assert_eq!(mux.remove_peer(a).as_deref(), Some("ROOT-A"));
        assert!(delivered(mux.chrome_message(json!({"method": "Page.loadEventFired", "sessionId": "PAGE-A", "params": {}}))).is_none());
    }

    #[test]
    fn a_client_cannot_close_the_shared_browser() {
        let mut mux = Mux::default();
        let (a, _ra) = peer(&mut mux, "ROOT-A");
        let reply = mux.client_message(a, r#"{"id":5,"method":"Browser.close"}"#, 20).unwrap_err().unwrap();
        assert_eq!(serde_json::from_str::<Value>(&reply).unwrap(), json!({"id": 5, "result": {}}));
        assert!(mux.pending.is_empty());
        assert!(mux.client_message(a, "not json", 21).unwrap_err().is_none());
    }
}
