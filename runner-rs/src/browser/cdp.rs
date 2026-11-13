//! The runner's DevTools connection to one Chrome. Chrome speaks CDP over `--remote-debugging-pipe` (fd 3 in, fd 4
//! out, NUL-separated JSON), so it has no debugging port any process could reach, and only the runner holds the
//! pipe. Calls are numbered here and each reply finds its waiter by its id; events are dropped (no domain is
//! enabled, `Runtime` least of all: a page can tell when it is).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::{mpsc, oneshot, watch};

const CALL_TIMEOUT: Duration = Duration::from_secs(30);
/// A single CDP message larger than this (a full-page screenshot is a few MB) ends the connection.
const MESSAGE_LIMIT: usize = 256 * 1024 * 1024;

/// The calls waiting for Chrome's reply, by wire id.
#[derive(Default)]
pub struct Mux {
    pending: HashMap<u64, oneshot::Sender<Value>>,
}

impl Mux {
    /// The text of call `wire_id` for Chrome; its reply goes to `tx`.
    pub fn call(
        &mut self,
        wire_id: u64,
        method: &str,
        params: Value,
        session: Option<&str>,
        tx: oneshot::Sender<Value>,
    ) -> String {
        self.pending.insert(wire_id, tx);
        let mut msg = json!({"id": wire_id, "method": method, "params": params});
        if let Some(s) = session {
            msg["sessionId"] = json!(s);
        }
        msg.to_string()
    }

    /// Where a message from Chrome goes: the waiter of its id, or nowhere (an event, or a call that gave up).
    pub fn chrome_message(&mut self, msg: &Value) -> Option<oneshot::Sender<Value>> {
        let wire = msg.get("id").and_then(|i| i.as_u64())?;
        self.pending.remove(&wire)
    }

    fn forget(&mut self, wire_id: u64) {
        self.pending.remove(&wire_id);
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
    pub fn start(
        mut to_chrome: tokio::net::unix::pipe::Sender,
        mut from_chrome: tokio::net::unix::pipe::Receiver,
    ) -> Arc<Browser> {
        let (out, mut queue) = mpsc::unbounded_channel::<String>();
        let (gone_tx, gone) = watch::channel(false);
        let browser = Arc::new(Browser {
            out,
            mux: Mutex::new(Mux::default()),
            next: AtomicU64::new(1),
            gone,
        });
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
                    let Some(b) = weak.upgrade() else {
                        break 'read;
                    };
                    b.incoming(&message[..message.len() - 1]);
                }
                if buf.len() > MESSAGE_LIMIT {
                    break;
                }
            }
            let _ = gone_tx.send(true);
            if let Some(b) = weak.upgrade() {
                // Every waiting call fails at once.
                *b.mux.lock().unwrap() = Mux::default();
            }
        });
        browser
    }

    fn incoming(&self, raw: &[u8]) {
        let Ok(msg) = serde_json::from_slice::<Value>(raw) else {
            return;
        };
        let waiter = self.mux.lock().unwrap().chrome_message(&msg);
        if let Some(tx) = waiter {
            let _ = tx.send(msg);
        }
    }

    pub fn alive(&self) -> bool {
        !*self.gone.borrow()
    }

    /// A CDP call; Err carries Chrome's error message (never a parameter).
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        session: Option<&str>,
    ) -> Result<Value, String> {
        self.call_within(method, params, session, CALL_TIMEOUT)
            .await
    }

    pub async fn call_within(
        &self,
        method: &str,
        params: Value,
        session: Option<&str>,
        wait: Duration,
    ) -> Result<Value, String> {
        if !self.alive() {
            return Err("the browser is not running".into());
        }
        let (tx, rx) = oneshot::channel();
        let id = self.next.fetch_add(1, Ordering::SeqCst);
        let text = self
            .mux
            .lock()
            .unwrap()
            .call(id, method, params, session, tx);
        if self.out.send(text).is_err() {
            return Err("the browser is not running".into());
        }
        let reply = match tokio::time::timeout(wait, rx).await {
            Ok(Ok(r)) => r,
            Ok(Err(_)) => return Err("the browser stopped".into()),
            Err(_) => {
                self.mux.lock().unwrap().forget(id);
                return Err(format!("{method} did not answer"));
            }
        };
        if let Some(e) = reply.get("error") {
            return Err(e["message"]
                .as_str()
                .unwrap_or("CDP error")
                .chars()
                .take(200)
                .collect());
        }
        Ok(reply["result"].clone())
    }

    /// Attach to a target (flattened): the session id its calls carry.
    pub async fn attach(&self, target: &str) -> Result<String, String> {
        let r = self
            .call(
                "Target.attachToTarget",
                json!({"targetId": target, "flatten": true}),
                None,
            )
            .await?;
        Ok(r["sessionId"].as_str().ok_or("no session")?.to_string())
    }

    pub async fn detach(&self, session: &str) {
        let _ = self
            .call_within(
                "Target.detachFromTarget",
                json!({"sessionId": session}),
                None,
                Duration::from_secs(5),
            )
            .await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_reply_finds_its_waiter_and_an_event_or_unknown_id_goes_nowhere() {
        let mut mux = Mux::default();
        let (tx, mut rx) = oneshot::channel();
        let text = mux.call(
            7,
            "Page.navigate",
            json!({"url": "https://acme.example/"}),
            Some("S1"),
            tx,
        );
        let sent: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            (sent["id"].as_u64(), sent["sessionId"].as_str()),
            (Some(7), Some("S1"))
        );
        assert!(mux
            .chrome_message(&json!({"method": "Page.loadEventFired", "sessionId": "S1"}))
            .is_none());
        assert!(mux
            .chrome_message(&json!({"id": 99, "result": {}}))
            .is_none());
        let reply = json!({"id": 7, "result": {"frameId": "F"}});
        mux.chrome_message(&reply)
            .unwrap()
            .send(reply.clone())
            .unwrap();
        assert_eq!(rx.try_recv().unwrap(), reply);
        assert!(mux.pending.is_empty());
    }
}
