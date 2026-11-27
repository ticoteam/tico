//! ACP (Agent Client Protocol): JSON-RPC 2.0 over an agent's stdin and stdout, one message per line. The hosts that
//! speak it (grok.rs, antigravity.rs) share what is the protocol's own here; what they do with a turn stays theirs.
//!
//! - Framing: `write` puts one message on the agent's input as one line; `Reader` reads the agent's output a line at a
//!   time, a JSON message or the text of a line that is not one (a banner, a sign-in link), and `classify` says
//!   whether a message answers a request, asks the client something, or tells it something.
//! - The messages: `request`, `answer` (to what the agent asks: a tool's permission given or refused, anything else
//!   unsupported), `cancel`, and the lifecycle's parameters (`new_session`, `open_session`, `prompt`) with
//!   `resume_method` (how an agent resumes a saved conversation, by what `initialize` said it can do).
//! - `Client`: requests in flight by id, each answered to its own waiter (`request`) or handed back to the host
//!   (`Slot::Hold`, as a `session/prompt` whose answer ends a turn), for a host whose one reader runs in the
//!   background. A host that reads in the turn's own task (antigravity.rs) uses the framing alone.
//! - `cancel_by`: `session/cancel` given up at a deadline, as an agent may have stopped reading its input.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader, Lines};
use tokio::sync::oneshot;

use crate::util::{text_of, truthy};

pub const PROTOCOL_VERSION: i64 = 1;

/// A request's answer: its `result`, or its `error`.
pub type Reply = Result<Value, Value>;

/// An agent's input: None once closed. Shared by a turn's reader and a stop of the turn.
pub type Input<W> = tokio::sync::Mutex<Option<W>>;

/// One message on the agent's input, as one line. Err(None): the input is closed; Err(Some): the write failed.
pub async fn write<W: AsyncWrite + Unpin>(
    input: &Input<W>,
    msg: &Value,
) -> Result<(), Option<std::io::Error>> {
    let mut input = input.lock().await;
    let Some(w) = input.as_mut() else {
        return Err(None);
    };
    write_line(w, msg).await.map_err(Some)
}

async fn write_line<W: AsyncWrite + Unpin>(w: &mut W, msg: &Value) -> std::io::Result<()> {
    let line = msg.to_string() + "\n";
    w.write_all(line.as_bytes()).await?;
    w.flush().await
}

/// `session/cancel` for `session`, given up at `by`: true once written, false when the input is closed, broke, or was
/// not read in time.
pub async fn cancel_by<W: AsyncWrite + Unpin>(
    input: &Input<W>,
    session: &str,
    by: tokio::time::Instant,
) -> bool {
    matches!(
        tokio::time::timeout_at(by, write(input, &cancel(session))).await,
        Ok(Ok(()))
    )
}

/// A line of the agent's output.
#[derive(Debug, PartialEq)]
pub enum Line {
    Message(Value),
    /// A line that is not JSON, as it came.
    Text(String),
}

/// The agent's output, a line at a time.
pub struct Reader<R> {
    lines: Lines<BufReader<R>>,
}

impl<R: AsyncRead + Unpin> Reader<R> {
    pub fn new(output: R) -> Reader<R> {
        Reader {
            lines: BufReader::new(output).lines(),
        }
    }

    /// The next line that is not blank; Ok(None) once the output ended. Cancel-safe: a line is never half read.
    pub async fn next(&mut self) -> std::io::Result<Option<Line>> {
        loop {
            let Some(line) = self.lines.next_line().await? else {
                return Ok(None);
            };
            if line.trim().is_empty() {
                continue;
            }
            return Ok(Some(match serde_json::from_str(line.trim()) {
                Ok(msg) => Line::Message(msg),
                Err(_) => Line::Text(line),
            }));
        }
    }
}

/// What a message from the agent is.
#[derive(Debug, PartialEq)]
pub enum Message {
    /// The answer to the client's request `id`.
    Response { id: Value, reply: Reply },
    /// The agent asks the client something; it waits for an `answer`.
    Request {
        id: Value,
        method: String,
        params: Value,
    },
    /// The agent tells the client something (`session/update`); `method` is empty when it names none.
    Notification { method: String, params: Value },
}

pub fn classify(msg: &Value) -> Message {
    let id = msg.get("id").filter(|i| !i.is_null()).cloned();
    let method = msg.get("method").and_then(Value::as_str);
    let params = msg.get("params").cloned().unwrap_or(Value::Null);
    match (id, method) {
        (Some(id), None) => Message::Response {
            id,
            reply: match msg.get("error") {
                Some(e) => Err(e.clone()),
                None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
            },
        },
        (Some(id), Some(method)) => Message::Request {
            id,
            method: method.into(),
            params,
        },
        (None, method) => Message::Notification {
            method: method.unwrap_or_default().into(),
            params,
        },
    }
}

pub fn request(id: i64, method: &str, params: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
}

/// `session/cancel`: the agent ends the session's prompt as cancelled.
pub fn cancel(session: &str) -> Value {
    json!({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session}})
}

/// Whether a tool's permission (`session/request_permission`) is given while the agent waits for an answer.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Permission {
    /// An allow option is chosen (`permission`).
    Give,
    /// Cancelled, as when no turn runs.
    Refuse,
}

/// The client's answer to the agent's request `id`: a tool's permission, else unsupported.
pub fn answer(id: Value, method: &str, params: &Value, give: Permission) -> Value {
    if method != "session/request_permission" {
        return json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "unsupported client request"}});
    }
    let outcome = match give {
        Permission::Give => permission(params),
        Permission::Refuse => json!({"outcome": {"outcome": "cancelled"}}),
    };
    json!({"jsonrpc": "2.0", "id": id, "result": outcome})
}

/// The answer to `session/request_permission` when it is given: an allow option, always before once; an unknown
/// option shape is cancelled rather than guessed.
pub fn permission(params: &Value) -> Value {
    let options = params["options"].as_array().cloned().unwrap_or_default();
    let chosen = ["allow_always", "allow_once"]
        .iter()
        .find_map(|kind| options.iter().find(|o| o["kind"] == *kind));
    match chosen.filter(|o| truthy(o.get("optionId"))) {
        Some(o) => json!({"outcome": {"outcome": "selected", "optionId": o["optionId"]}}),
        None => json!({"outcome": {"outcome": "cancelled"}}),
    }
}

/// `session/new`'s parameters.
pub fn new_session(cwd: &str, servers: Value) -> Value {
    json!({"cwd": cwd, "mcpServers": servers})
}

/// `session/load` or `session/resume`'s parameters, for the saved conversation `session`.
pub fn open_session(session: &str, cwd: &str, servers: Value) -> Value {
    json!({"sessionId": session, "cwd": cwd, "mcpServers": servers})
}

/// `session/prompt`'s parameters: the turn's text.
pub fn prompt(session: &str, text: &str) -> Value {
    json!({"sessionId": session, "prompt": [{"type": "text", "text": text}]})
}

/// How an agent resumes a saved conversation, by its `initialize` answer: `session/resume` (it does not replay the
/// conversation's history as updates), else `session/load`; None when it can do neither.
pub fn resume_method(init: &Value) -> Option<&'static str> {
    let caps = &init["agentCapabilities"];
    if caps["sessionCapabilities"]["resume"].is_object() {
        Some("session/resume")
    } else if caps["loadSession"] == true {
        Some("session/load")
    } else {
        None
    }
}

/// What a refused request's error says: its message, else the error as it is.
pub fn error_text(err: &Value) -> String {
    err.get("message")
        .filter(|m| truthy(Some(m)))
        .map(text_of)
        .unwrap_or_else(|| text_of(err))
}

/// Where a request's answer goes.
pub enum Slot<T> {
    /// To the task waiting in `Client::request`.
    Wait(oneshot::Sender<Reply>),
    /// Back to the host, with `T`, from `Client::resolve` or `Client::close`.
    Hold(T),
}

/// Why `Client::request` or `Client::send` has no result.
#[derive(Debug)]
pub enum RequestError {
    /// It could not be written (`write`'s error; TimedOut when the agent did not read it in time).
    Write(Option<std::io::Error>),
    /// No answer in the time it had.
    Late,
    /// The agent's output ended, before the request was sent or while it waited.
    Ended,
    /// The agent answered with an error.
    Refused(Value),
}

/// The requests in flight, and whether the agent's output has ended: one lock, so a request is never registered
/// after `close` drained them.
struct Pending<T> {
    slots: HashMap<i64, Slot<T>>,
    closed: bool,
}

/// The client side of one agent process whose one reader runs in the background: requests in flight by id. A process
/// gets a client of its own, so the reader of one that went cannot end or answer the next one's requests.
pub struct Client<W, T> {
    input: Input<W>,
    pending: Mutex<Pending<T>>,
    next_id: AtomicI64,
    /// How long one write may take: an agent that stops reading its input does not hold it, or a stop, longer.
    write_wait: Duration,
}

/// The input while a line is written to it: unless finished, the line may be half written (timed out, failed, or its
/// future dropped), so the input is closed before anything queued behind it can append to that line.
struct Unfinished<'a, W> {
    input: tokio::sync::MutexGuard<'a, Option<W>>,
    whole: bool,
}

impl<W> Drop for Unfinished<'_, W> {
    fn drop(&mut self) {
        if !self.whole {
            drop(self.input.take());
        }
    }
}

/// Removes a request's waiter when its `Client::request` ends however it ends, a dropped future included.
struct Forget<'a, T> {
    pending: &'a Mutex<Pending<T>>,
    id: i64,
}

impl<T> Drop for Forget<'_, T> {
    fn drop(&mut self) {
        let mut pending = self.pending.lock().unwrap_or_else(|p| p.into_inner());
        pending.slots.remove(&self.id);
    }
}

impl<W: AsyncWrite + Unpin, T> Client<W, T> {
    pub fn new(input: Option<W>, write_wait: Duration) -> Self {
        Client {
            input: tokio::sync::Mutex::new(input),
            pending: Mutex::new(Pending {
                slots: HashMap::new(),
                closed: false,
            }),
            next_id: AtomicI64::new(0),
            write_wait,
        }
    }

    fn pending(&self) -> std::sync::MutexGuard<'_, Pending<T>> {
        self.pending.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Close the input: the agent reads its end.
    pub async fn disconnect(&self) {
        drop(self.input.lock().await.take());
    }

    /// One message, written by `by`, waiting for the input within it too. One not written whole (in time, without
    /// error, or before its future was dropped) may be half a line, so the input is closed then, before anything
    /// queued behind it can write; one still waiting for another write at `by` gives up without writing, and that
    /// write closes the input if it never ends.
    async fn write_by(
        &self,
        msg: &Value,
        by: tokio::time::Instant,
    ) -> Result<(), Option<std::io::Error>> {
        let late = || {
            Err(Some(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "the agent did not read its input in time",
            )))
        };
        let Ok(input) = tokio::time::timeout_at(by, self.input.lock()).await else {
            return late();
        };
        let mut line = Unfinished {
            input,
            whole: false,
        };
        let Some(w) = line.input.as_mut() else {
            return Err(None);
        };
        match tokio::time::timeout_at(by, write_line(w, msg)).await {
            Ok(Ok(())) => {
                line.whole = true;
                Ok(())
            }
            Ok(Err(e)) => Err(Some(e)),
            Err(_) => late(),
        }
    }

    /// One message, within the client's write wait.
    pub async fn write(&self, msg: &Value) -> Result<(), Option<std::io::Error>> {
        self.write_by(msg, tokio::time::Instant::now() + self.write_wait)
            .await
    }

    /// `session/cancel` for `session`, given up at `by`: true once written.
    pub async fn cancel_by(&self, session: &str, by: tokio::time::Instant) -> bool {
        self.write_by(&cancel(session), by).await.is_ok()
    }

    /// The next id (1 first) for `slot`; Ended once the output has.
    fn register(&self, slot: Slot<T>) -> Result<i64, RequestError> {
        let mut pending = self.pending();
        if pending.closed {
            return Err(RequestError::Ended);
        }
        let id = self.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        pending.slots.insert(id, slot);
        Ok(id)
    }

    /// Send `method`, its answer going to `slot`; nothing is left waiting when it cannot be sent, or when the send is
    /// dropped before it was.
    pub async fn send(
        &self,
        method: &str,
        params: Value,
        slot: Slot<T>,
    ) -> Result<i64, RequestError> {
        let id = self.register(slot)?;
        let forget = Forget {
            pending: &self.pending,
            id,
        };
        self.write(&request(id, method, params))
            .await
            .map_err(RequestError::Write)?;
        // Written: the slot waits for its answer.
        std::mem::forget(forget);
        Ok(id)
    }

    /// Send `method` and wait up to `wait` for its answer once written; one that comes later is dropped, and so is
    /// the waiter of a request whose future is dropped.
    pub async fn request(
        &self,
        method: &str,
        params: Value,
        wait: Duration,
    ) -> Result<Value, RequestError> {
        let (tx, rx) = oneshot::channel();
        let id = self.register(Slot::Wait(tx))?;
        let _forget = Forget {
            pending: &self.pending,
            id,
        };
        self.write(&request(id, method, params))
            .await
            .map_err(RequestError::Write)?;
        match tokio::time::timeout(wait, rx).await {
            Err(_) => Err(RequestError::Late),
            Ok(Err(_)) => Err(RequestError::Ended),
            Ok(Ok(Ok(result))) => Ok(result),
            Ok(Ok(Err(err))) => Err(RequestError::Refused(err)),
        }
    }

    /// Wait for request `id`'s answer in `slot`, as if it had been sent so (a test's agent side).
    #[cfg(test)]
    pub fn expect(&self, id: i64, slot: Slot<T>) {
        self.pending().slots.insert(id, slot);
    }

    /// The answer to request `id`: given to its waiter, or handed back with what it holds. None for an id not in
    /// flight (answered, given up, or never sent).
    pub fn resolve(&self, id: &Value, reply: Reply) -> Option<(T, Reply)> {
        match id.as_i64().and_then(|i| self.pending().slots.remove(&i))? {
            Slot::Wait(tx) => {
                let _ = tx.send(reply);
                None
            }
            Slot::Hold(held) => Some((held, reply)),
        }
    }

    /// The agent's output ended: every waiter gets `RequestError::Ended`, what the rest held is handed back, and any
    /// request from now on is refused at once.
    pub fn close(&self) -> Vec<T> {
        let slots: Vec<Slot<T>> = {
            let mut pending = self.pending();
            pending.closed = true;
            pending.slots.drain().map(|(_, s)| s).collect()
        };
        slots
            .into_iter()
            .filter_map(|slot| match slot {
                Slot::Wait(_) => None,
                Slot::Hold(held) => Some(held),
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use tokio::io::{duplex, DuplexStream};

    /// A client and its agent's side: what the agent reads is what the client wrote.
    async fn pair() -> (
        Arc<Client<DuplexStream, &'static str>>,
        Reader<DuplexStream>,
    ) {
        let (ours, theirs) = duplex(64 * 1024);
        let client = Arc::new(Client::new(Some(ours), Duration::from_secs(5)));
        (client, Reader::new(theirs))
    }

    fn in_flight(client: &Client<DuplexStream, &'static str>) -> usize {
        client.pending().slots.len()
    }

    async fn next_message(agent: &mut Reader<DuplexStream>) -> Value {
        match agent.next().await.unwrap() {
            Some(Line::Message(msg)) => msg,
            other => panic!("expected a message, got {other:?}"),
        }
    }

    /// The background reader a host runs: each answer resolved, what a request held handed back.
    async fn deliver(
        client: &Client<DuplexStream, &'static str>,
        msg: Value,
    ) -> Option<(&'static str, Reply)> {
        match classify(&msg) {
            Message::Response { id, reply } => client.resolve(&id, reply),
            other => panic!("expected a response, got {other:?}"),
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn each_answer_goes_to_the_request_with_its_id() {
        let (client, mut agent) = pair().await;
        let first = tokio::spawn({
            let c = client.clone();
            async move {
                c.request(
                    "initialize",
                    json!({"protocolVersion": 1}),
                    Duration::from_secs(5),
                )
                .await
            }
        });
        let sent = next_message(&mut agent).await;
        assert_eq!(
            sent,
            json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
        );
        let second = tokio::spawn({
            let c = client.clone();
            async move {
                c.request("session/new", json!({"cwd": "/w"}), Duration::from_secs(5))
                    .await
            }
        });
        assert_eq!(next_message(&mut agent).await["id"], 2);
        // Answered out of order, the second with an error: each waiter gets its own.
        assert!(deliver(
            &client,
            json!({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "no"}})
        )
        .await
        .is_none());
        assert!(deliver(
            &client,
            json!({"jsonrpc": "2.0", "id": 1, "result": {"agentCapabilities": {}}})
        )
        .await
        .is_none());
        assert_eq!(
            first.await.unwrap().unwrap(),
            json!({"agentCapabilities": {}})
        );
        match second.await.unwrap() {
            Err(RequestError::Refused(err)) => assert_eq!(error_text(&err), "no"),
            other => panic!("expected a refusal, got {other:?}"),
        }
        // An answer to nothing in flight is dropped.
        assert!(
            deliver(&client, json!({"jsonrpc": "2.0", "id": 1, "result": {}}))
                .await
                .is_none()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_held_request_comes_back_with_its_answer() {
        let (client, mut agent) = pair().await;
        let id = client
            .send("session/prompt", prompt("s-1", "hi"), Slot::Hold("turn-1"))
            .await
            .unwrap();
        assert_eq!(
            next_message(&mut agent).await,
            json!({"jsonrpc": "2.0", "id": id, "method": "session/prompt", "params": {"sessionId": "s-1", "prompt": [{"type": "text", "text": "hi"}]}})
        );
        let (held, reply) = deliver(
            &client,
            json!({"jsonrpc": "2.0", "id": id, "result": {"stopReason": "end_turn"}}),
        )
        .await
        .unwrap();
        assert_eq!(
            (held, reply.unwrap()),
            ("turn-1", json!({"stopReason": "end_turn"}))
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn the_output_ending_ends_every_request_in_flight() {
        let (client, mut agent) = pair().await;
        client
            .send("session/prompt", prompt("s-1", "hi"), Slot::Hold("turn-1"))
            .await
            .unwrap();
        let waiting = tokio::spawn({
            let c = client.clone();
            async move {
                c.request("session/load", json!({}), Duration::from_secs(5))
                    .await
            }
        });
        // A request is waiting before it is written, so once the agent has read both, both are in flight.
        next_message(&mut agent).await;
        assert_eq!(next_message(&mut agent).await["method"], "session/load");
        assert_eq!(client.close(), vec!["turn-1"]);
        assert!(matches!(waiting.await.unwrap(), Err(RequestError::Ended)));
        assert_eq!(in_flight(&client), 0);
    }

    #[tokio::test]
    async fn nothing_is_sent_or_left_waiting_once_the_output_ended() {
        let (client, mut agent) = pair().await;
        assert!(client.close().is_empty());
        assert!(matches!(
            client
                .send("session/prompt", prompt("s-1", "hi"), Slot::Hold("turn-1"))
                .await,
            Err(RequestError::Ended)
        ));
        assert!(matches!(
            client
                .request("session/new", json!({}), Duration::from_secs(5))
                .await,
            Err(RequestError::Ended)
        ));
        assert_eq!(
            in_flight(&client),
            0,
            "no prompt stranded after the reader ended"
        );
        client.disconnect().await;
        assert_eq!(agent.next().await.unwrap(), None, "nothing was written");
    }

    #[tokio::test]
    async fn a_dropped_request_leaves_nothing_waiting() {
        let (client, mut agent) = pair().await;
        let waiting = tokio::spawn({
            let c = client.clone();
            async move {
                c.request("session/load", json!({}), Duration::from_secs(60))
                    .await
            }
        });
        let id = next_message(&mut agent).await["id"].clone();
        assert_eq!(in_flight(&client), 1);
        waiting.abort();
        assert!(waiting.await.unwrap_err().is_cancelled());
        assert_eq!(
            in_flight(&client),
            0,
            "the dropped request's waiter is gone"
        );
        assert!(
            deliver(&client, json!({"jsonrpc": "2.0", "id": id, "result": {}}))
                .await
                .is_none()
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_write_the_agent_does_not_read_is_given_up_and_closes_the_input() {
        // A pipe too small for the request: the agent never reads it.
        let (ours, _theirs) = duplex(8);
        let client: Client<DuplexStream, &'static str> =
            Client::new(Some(ours), Duration::from_millis(100));
        match client
            .request("initialize", json!({}), Duration::from_secs(60))
            .await
        {
            Err(RequestError::Write(Some(e))) => {
                assert_eq!(e.kind(), std::io::ErrorKind::TimedOut)
            }
            other => panic!("expected a timed-out write, got {other:?}"),
        }
        assert_eq!(in_flight(&client), 0);
        // Half a line went: the input is not written to again, so a stop never waits behind it.
        assert!(matches!(client.write(&cancel("s-1")).await, Err(None)));
        assert!(
            !client
                .cancel_by("s-1", tokio::time::Instant::now() + Duration::from_secs(5))
                .await
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_cancel_behind_a_blocked_write_gives_up_by_its_own_deadline() {
        // The agent stops reading while a prompt is half written: that write may hold the input for two minutes.
        let (ours, _theirs) = duplex(8);
        let client: Arc<Client<DuplexStream, &'static str>> =
            Arc::new(Client::new(Some(ours), Duration::from_secs(120)));
        let prompt = tokio::spawn({
            let c = client.clone();
            async move { c.write(&request(1, "session/prompt", json!({}))).await }
        });
        tokio::task::yield_now().await;
        let started = tokio::time::Instant::now();
        let by = started + Duration::from_secs(15);
        assert!(!client.cancel_by("s-1", by).await);
        assert!(
            tokio::time::Instant::now() <= by,
            "the cancel waited {:?}, past its 15 s",
            started.elapsed()
        );
        // The prompt's own write still ends at its own deadline, and closes the input it left half a line on.
        assert!(matches!(prompt.await.unwrap(), Err(Some(_))));
        assert!(matches!(client.write(&cancel("s-1")).await, Err(None)));
    }

    #[tokio::test(start_paused = true)]
    async fn nothing_queued_behind_a_half_written_line_reaches_the_agent() {
        let (ours, _theirs) = duplex(8);
        let client: Arc<Client<DuplexStream, &'static str>> =
            Arc::new(Client::new(Some(ours), Duration::from_millis(100)));
        let first = tokio::spawn({
            let c = client.clone();
            async move { c.write(&request(1, "session/prompt", json!({}))).await }
        });
        tokio::task::yield_now().await;
        let queued = tokio::spawn({
            let c = client.clone();
            async move { c.write(&cancel("s-1")).await }
        });
        assert!(matches!(first.await.unwrap(), Err(Some(_))));
        // The timed-out write closed the input before letting go of it: the queued one finds it closed.
        assert!(matches!(queued.await.unwrap(), Err(None)));
    }

    #[tokio::test(start_paused = true)]
    async fn a_dropped_send_leaves_nothing_waiting() {
        let (ours, _theirs) = duplex(8);
        let client: Arc<Client<DuplexStream, &'static str>> =
            Arc::new(Client::new(Some(ours), Duration::from_secs(120)));
        let sending = tokio::spawn({
            let c = client.clone();
            async move {
                c.send(
                    "session/prompt",
                    json!({"text": "long"}),
                    Slot::Hold("turn-1"),
                )
                .await
            }
        });
        tokio::task::yield_now().await;
        assert_eq!(in_flight(&client), 1);
        sending.abort();
        assert!(sending.await.unwrap_err().is_cancelled());
        assert_eq!(in_flight(&client), 0, "the dropped send's slot is gone");
        // It may have left half a line: the input is closed, so nothing is appended to it.
        assert!(matches!(client.write(&cancel("s-1")).await, Err(None)));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_request_not_answered_in_time_is_given_up() {
        let (client, mut agent) = pair().await;
        let late = client
            .request("initialize", json!({}), Duration::from_millis(50))
            .await;
        assert!(matches!(late, Err(RequestError::Late)));
        assert_eq!(in_flight(&client), 0, "nothing left waiting");
        let id = next_message(&mut agent).await["id"].clone();
        assert!(
            deliver(&client, json!({"jsonrpc": "2.0", "id": id, "result": {}}))
                .await
                .is_none()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_closed_input_sends_nothing_and_leaves_nothing_waiting() {
        let (client, _agent) = pair().await;
        client.disconnect().await;
        assert!(matches!(
            client
                .request("initialize", json!({}), Duration::from_secs(5))
                .await,
            Err(RequestError::Write(None))
        ));
        assert_eq!(in_flight(&client), 0);
        // An agent that went away: its end of the pipe is gone.
        let (client, agent) = pair().await;
        drop(agent);
        assert!(matches!(
            client.send("initialize", json!({}), Slot::Hold("x")).await,
            Err(RequestError::Write(Some(_)))
        ));
        assert_eq!(in_flight(&client), 0);
    }

    #[tokio::test]
    async fn the_reader_skips_blank_lines_keeps_text_and_says_when_the_output_ended() {
        let (mut ours, theirs) = duplex(1024);
        let mut reader = Reader::new(theirs);
        ours.write_all(
            b"\n  \n{\"jsonrpc\":\"2.0\",\"method\":\"x\"}\nOpen the following link\n{\"half\":",
        )
        .await
        .unwrap();
        drop(ours);
        assert_eq!(
            reader.next().await.unwrap(),
            Some(Line::Message(json!({"jsonrpc": "2.0", "method": "x"})))
        );
        assert_eq!(
            reader.next().await.unwrap(),
            Some(Line::Text("Open the following link".into()))
        );
        assert_eq!(
            reader.next().await.unwrap(),
            Some(Line::Text("{\"half\":".into()))
        );
        assert_eq!(reader.next().await.unwrap(), None);
    }

    #[test]
    fn responses_requests_and_notifications_are_told_apart() {
        assert_eq!(
            classify(&json!({"id": 3, "result": {"a": 1}})),
            Message::Response {
                id: json!(3),
                reply: Ok(json!({"a": 1}))
            }
        );
        assert_eq!(
            classify(&json!({"id": 3})),
            Message::Response {
                id: json!(3),
                reply: Ok(Value::Null)
            }
        );
        assert_eq!(
            classify(&json!({"id": 3, "error": {"code": 1}, "result": {}})),
            Message::Response {
                id: json!(3),
                reply: Err(json!({"code": 1}))
            }
        );
        assert_eq!(
            classify(
                &json!({"id": "p", "method": "session/request_permission", "params": {"options": []}})
            ),
            Message::Request {
                id: json!("p"),
                method: "session/request_permission".into(),
                params: json!({"options": []})
            }
        );
        // A null id is a notification's.
        assert_eq!(
            classify(
                &json!({"id": null, "method": "session/update", "params": {"sessionId": "s"}})
            ),
            Message::Notification {
                method: "session/update".into(),
                params: json!({"sessionId": "s"})
            }
        );
        assert_eq!(
            classify(&json!({"jsonrpc": "2.0"})),
            Message::Notification {
                method: String::new(),
                params: Value::Null
            }
        );
    }

    #[test]
    fn what_the_agent_asks_is_answered_by_whether_permission_is_given() {
        let options = json!({"options": [{"kind": "allow_once", "optionId": "once"}, {"kind": "allow_always", "optionId": "always"}]});
        assert_eq!(
            answer(
                json!(7),
                "session/request_permission",
                &options,
                Permission::Give
            ),
            json!({"jsonrpc": "2.0", "id": 7, "result": {"outcome": {"outcome": "selected", "optionId": "always"}}})
        );
        assert_eq!(
            answer(
                json!(7),
                "session/request_permission",
                &options,
                Permission::Refuse
            ),
            json!({"jsonrpc": "2.0", "id": 7, "result": {"outcome": {"outcome": "cancelled"}}})
        );
        assert_eq!(
            permission(&json!({"options": [{"kind": "reject_once", "optionId": "no"}]})),
            json!({"outcome": {"outcome": "cancelled"}})
        );
        assert_eq!(
            permission(&json!({"options": [{"kind": "allow_once", "optionId": ""}]})),
            json!({"outcome": {"outcome": "cancelled"}}),
            "an allow option with no id is not guessed"
        );
        assert_eq!(
            answer(
                json!("x"),
                "fs/read_text_file",
                &json!({}),
                Permission::Give
            ),
            json!({"jsonrpc": "2.0", "id": "x", "error": {"code": -32601, "message": "unsupported client request"}})
        );
    }

    #[test]
    fn a_saved_conversation_is_resumed_by_what_the_agent_can_do() {
        let resume = json!({"agentCapabilities": {"sessionCapabilities": {"resume": {}}, "loadSession": true}});
        assert_eq!(resume_method(&resume), Some("session/resume"));
        assert_eq!(
            resume_method(&json!({"agentCapabilities": {"loadSession": true}})),
            Some("session/load")
        );
        assert_eq!(
            resume_method(&json!({"agentCapabilities": {"loadSession": "yes"}})),
            None
        );
        assert_eq!(resume_method(&json!({})), None);
    }

    #[tokio::test(start_paused = true)]
    async fn a_cancel_is_written_once_and_given_up_at_its_deadline() {
        let (ours, theirs) = duplex(1024);
        let input: Input<DuplexStream> = tokio::sync::Mutex::new(Some(ours));
        let mut agent = Reader::new(theirs);
        let by = tokio::time::Instant::now() + Duration::from_secs(5);
        assert!(cancel_by(&input, "s-1", by).await);
        assert_eq!(
            agent.next().await.unwrap(),
            Some(Line::Message(
                json!({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s-1"}})
            ))
        );
        // An agent that stopped reading: its pipe full, the cancel is given up at the deadline, not waited out.
        let (ours, _theirs) = duplex(8);
        let input: Input<DuplexStream> = tokio::sync::Mutex::new(Some(ours));
        assert!(
            !cancel_by(
                &input,
                "s-1",
                tokio::time::Instant::now() + Duration::from_millis(100)
            )
            .await
        );
        // Nothing to cancel on a closed input.
        let closed: Input<DuplexStream> = tokio::sync::Mutex::new(None);
        assert!(
            !cancel_by(
                &closed,
                "s-1",
                tokio::time::Instant::now() + Duration::from_secs(5)
            )
            .await
        );
    }
}
