//! The turn's stream loop (crate::execute `stream_loop`) and `Runner::flush` against a synthetic hub that keeps
//! events the way backend/execution.py `events` does (a resent seq with the same content is skipped, a gap or a
//! changed one refused), behind one write lock. Holds the runtime tests for AC-3 to AC-7 of the I07 spec and the
//! bench behind bench/perf-event-uploads.py (ignored in the normal run).

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::Notify;

use super::{Bounds, ADAPTIVE, FIXED};
use crate::execute::Flags;
use crate::runner::Runner;

#[derive(Default)]
struct Kept {
    /// Per attempt: (seq, kind, payload) in the order stored.
    stored: HashMap<String, Vec<(i64, String, Value)>>,
    /// When each (attempt, seq) first reached the hub.
    arrived: HashMap<(String, i64), Instant>,
    /// When the upload that first stored each (attempt, seq) reached the hub, before waiting for the write lock.
    sent: HashMap<(String, i64), Instant>,
    /// Per events upload: attempt, handler time, the ack it answered (None: refused or answer dropped).
    uploads: Vec<(String, Duration, Option<i64>)>,
    /// Input poll handler times.
    polls: Vec<Duration>,
    refused: Vec<String>,
}

struct Hub {
    url: String,
    kept: Arc<Mutex<Kept>>,
}

#[derive(Clone, Default)]
struct Behaviour {
    /// Time an events upload holds the write lock: a fixed part and a part per event.
    write: Duration,
    per_event: Duration,
    /// Time an input poll holds it.
    poll: Duration,
    /// Events uploads (0-based, in arrival order) answered 503 without storing.
    fail: HashSet<usize>,
    /// Events uploads stored whose answer is lost (the connection closes).
    drop_answer: HashSet<usize>,
}

/// The hub runs on its own runtime and threads, as a server does: the runner's work never delays its answers.
async fn hub(behaviour: Behaviour) -> Hub {
    let kept = Arc::new(Mutex::new(Kept::default()));
    let (k, b) = (kept.clone(), Arc::new(behaviour));
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .unwrap();
        rt.block_on(serve(k, b, tx));
    });
    let url = tokio::task::spawn_blocking(move || rx.recv().unwrap())
        .await
        .unwrap();
    Hub { url, kept }
}

async fn serve(k: Arc<Mutex<Kept>>, b: Arc<Behaviour>, ready: std::sync::mpsc::Sender<String>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    ready
        .send(format!("http://{}", listener.local_addr().unwrap()))
        .unwrap();
    let writer = Arc::new(tokio::sync::Mutex::new(0usize)); // the write lock; counts events uploads
    {
        while let Ok((mut socket, _)) = listener.accept().await {
            let (kept, writer, b) = (k.clone(), writer.clone(), b.clone());
            tokio::spawn(async move {
                let mut request = Vec::new();
                let mut buffer = [0u8; 65536];
                loop {
                    // One request at a time on a kept-alive connection.
                    let head = loop {
                        if let Some(at) = request.windows(4).position(|p| p == b"\r\n\r\n") {
                            break at;
                        }
                        match socket.read(&mut buffer).await {
                            Ok(0) | Err(_) => return,
                            Ok(n) => request.extend_from_slice(&buffer[..n]),
                        }
                    };
                    let text = String::from_utf8_lossy(&request[..head]).to_string();
                    let length = text
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    while request.len() < head + 4 + length {
                        match socket.read(&mut buffer).await {
                            Ok(0) | Err(_) => return,
                            Ok(n) => request.extend_from_slice(&buffer[..n]),
                        }
                    }
                    let body: Value = serde_json::from_slice(&request[head + 4..head + 4 + length])
                        .unwrap_or(Value::Null);
                    request.drain(..head + 4 + length);
                    let path = text
                        .lines()
                        .next()
                        .and_then(|l| l.split(' ').nth(1))
                        .unwrap_or_default()
                        .to_string();
                    let began = Instant::now();
                    let parts: Vec<&str> = path.trim_start_matches('/').split('/').collect();
                    let (status, answer, drop) = match parts.as_slice() {
                        [.., "attempts", aid, "events"] => {
                            let mut lock = writer.lock().await;
                            let n = *lock;
                            *lock += 1;
                            let events = body["events"].as_array().cloned().unwrap_or_default();
                            tokio::time::sleep(b.write + b.per_event * events.len() as u32).await;
                            if b.fail.contains(&n) {
                                kept.lock().unwrap().uploads.push((
                                    aid.to_string(),
                                    began.elapsed(),
                                    None,
                                ));
                                (503, json!({"detail": "busy"}), false)
                            } else {
                                let mut k = kept.lock().unwrap();
                                let k = &mut *k;
                                let rows = k.stored.entry(aid.to_string()).or_default();
                                let mut refused = None;
                                for e in &events {
                                    let seq = e["seq"].as_i64().unwrap();
                                    let row = (
                                        seq,
                                        e["kind"].as_str().unwrap().to_string(),
                                        e["payload"].clone(),
                                    );
                                    match rows.iter().find(|r| r.0 == seq) {
                                        Some(had) if *had == row => continue,
                                        Some(_) => {
                                            refused = Some(format!("{aid}: seq {seq} changed"))
                                        }
                                        None if seq != rows.last().map_or(0, |r| r.0) + 1 => {
                                            refused = Some(format!("{aid}: gap before {seq}"))
                                        }
                                        None => {
                                            k.arrived
                                                .insert((aid.to_string(), seq), Instant::now());
                                            k.sent.insert((aid.to_string(), seq), began);
                                            rows.push(row);
                                        }
                                    }
                                    if refused.is_some() {
                                        break;
                                    }
                                }
                                let ack = rows.last().map_or(0, |r| r.0);
                                let drop = b.drop_answer.contains(&n);
                                let answered = (refused.is_none() && !drop).then_some(ack);
                                k.uploads.push((aid.to_string(), began.elapsed(), answered));
                                match refused {
                                    Some(why) => {
                                        k.refused.push(why);
                                        (409, json!({"detail": "event_gap"}), false)
                                    }
                                    None => {
                                        (200, json!({"ack_seq": ack, "historical": false}), drop)
                                    }
                                }
                            }
                        }
                        [.., "attempts", _, "inputs"] => {
                            let _lock = writer.lock().await;
                            tokio::time::sleep(b.poll).await;
                            kept.lock().unwrap().polls.push(began.elapsed());
                            (200, json!({"messages": []}), false)
                        }
                        _ => (404, json!({"detail": "not here"}), false),
                    };
                    if drop {
                        let _ = socket.shutdown().await;
                        return;
                    }
                    let body = answer.to_string();
                    let reply = format!(
                        "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                        body.len()
                    );
                    if socket.write_all(reply.as_bytes()).await.is_err() {
                        return;
                    }
                }
            });
        }
    }
}

fn runner_at(url: &str, bounds: Bounds) -> (Arc<Runner>, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!("tico-pace-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir_all(dir.join("projects")).unwrap();
    let path = dir.join("runner.json");
    std::fs::write(
        &path,
        json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")})
            .to_string(),
    )
    .unwrap();
    let config = crate::config::Config::load(&path).unwrap();
    let client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
    let mut runner = Runner::new(
        config,
        client,
        crate::state::State::open(&dir.join("state")).unwrap(),
    );
    runner.upload_bounds = bounds;
    (Arc::new(runner), dir)
}

fn flags() -> Arc<Flags> {
    Arc::new(Flags {
        lost: AtomicBool::new(false),
        stopped: AtomicBool::new(false),
        stop_reason: Mutex::new(None),
        done: AtomicBool::new(false),
        harness_allocated: Arc::new(AtomicBool::new(false)),
        start_acknowledged: AtomicBool::new(false),
        done_notify: Notify::new(),
        deadline: Mutex::new(Instant::now() + Duration::from_secs(3600)),
    })
}

/// One step of a synthetic turn: an event to record, then a pause.
type Step = (&'static str, Value, Duration);

/// The bench reply: a status line, `deltas` text deltas `gap` apart with a tool start and end every `tool_every`, the
/// final message, its usage and a closing status. About 40 bytes a delta, like a model's streamed tokens.
fn reply(deltas: usize, gap: Duration, tool_every: usize) -> Vec<Step> {
    let mut steps: Vec<Step> = vec![("status", json!({"waiting": ""}), gap)];
    for n in 0..deltas {
        if n > 0 && n % tool_every == 0 {
            steps.push((
                "tool",
                json!({"item_id": format!("call_{n}"), "name": "shell", "phase": "started"}),
                gap,
            ));
            steps.push((
                "tool",
                json!({"item_id": format!("call_{n}"), "name": "shell", "phase": "completed"}),
                gap,
            ));
        }
        steps.push((
            "delta",
            json!({"text": format!("Acme renewal line {n:05} is on track. ")}),
            gap,
        ));
    }
    steps.push((
        "message",
        json!({"text": "Acme's renewal summary is ready.", "final": true}),
        Duration::ZERO,
    ));
    steps.push((
        "tokens",
        json!({"input": 1200, "output": 3400}),
        Duration::ZERO,
    ));
    steps.push(("status", json!({"done": true}), Duration::ZERO));
    steps
}

/// Per recorded event: its seq, kind, when the turn emitted it and when its row was committed to the outbox.
type Recorded = Vec<(i64, &'static str, Instant, Instant)>;

/// Runs one turn's steps through the real stream loop and drive-loop pass, as crate::execute and crate::drive do:
/// each event is appended to the outbox and wakes the flusher; once a second the drive loop flushes and polls for
/// inputs. Returns once the outbox is empty after the last step.
async fn turn(runner: Arc<Runner>, aid: String, steps: Vec<Step>) -> Recorded {
    turn_with(runner, aid, steps, true).await
}

/// `turn`, with or without the drive loop's 1 s pass (without it, only the stream loop uploads).
async fn turn_with(
    runner: Arc<Runner>,
    aid: String,
    steps: Vec<Step>,
    with_pass: bool,
) -> Recorded {
    runner.state.record(&json!({"id": aid})).unwrap();
    let (wake, flags) = (Arc::new(Notify::new()), flags());
    let streamer = tokio::spawn(runner.clone().stream_loop(
        aid.clone(),
        wake.clone(),
        flags.clone(),
    ));
    let pass = {
        let (runner, aid) = (runner.clone(), aid.clone());
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(1)).await;
                if !with_pass {
                    continue;
                }
                let _ = runner.flush(&aid).await;
                let _ = runner
                    .client
                    .post(&format!("attempts/{aid}/inputs"), &json!({}), None)
                    .await;
            }
        })
    };
    // The harness: each step at its own time from the start, so a turn lasts as long whatever its writes cost.
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<(&'static str, Value, Instant)>();
    let harness = tokio::spawn(async move {
        let mut at = tokio::time::Instant::now();
        for (kind, payload, pause) in steps {
            tx.send((kind, payload, Instant::now())).unwrap();
            at += pause;
            tokio::time::sleep_until(at).await;
        }
    });
    // The drive loop: whatever the harness sent since its last write goes in one transaction, off the runtime's
    // workers, then wakes the flusher (Runner::write_rows).
    let mut recorded: Recorded = vec![];
    while let Some(first) = rx.recv().await {
        let mut queued = vec![first];
        while let Ok(more) = rx.try_recv() {
            queued.push(more);
        }
        let rows: Vec<(String, String)> = queued
            .iter()
            .map(|(k, p, _)| (k.to_string(), p.to_string()))
            .collect();
        let id = aid.clone();
        let last = runner
            .state
            .off(move |s| s.append_batch(&id, &rows).and_then(|_| s.last_seq(&id)))
            .await
            .unwrap();
        let written = Instant::now();
        let first_seq = last - queued.len() as i64 + 1;
        for (n, (kind, _, at)) in queued.into_iter().enumerate() {
            recorded.push((first_seq + n as i64, kind, at, written));
        }
        wake.notify_one();
    }
    harness.await.unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    // On the blocking pool, as the runner reads the store: a read on an async worker would stall it behind the
    // store's fsyncs and starve the stream loops while the turns' last control events go up.
    loop {
        let id = aid.clone();
        if runner
            .state
            .off(move |s| s.pending(&id))
            .await
            .unwrap()
            .is_empty()
        {
            break;
        }
        assert!(Instant::now() < deadline, "{aid}: the outbox never emptied");
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    flags.done.store(true, Ordering::SeqCst);
    streamer.abort();
    pass.abort();
    runner.forget_flush(&aid);
    recorded
}

/// What the runner kept for `aid`, as (seq, kind, payload) rows.
fn local(runner: &Runner, aid: &str) -> Vec<(i64, String, Value)> {
    runner
        .state
        .with(|c| {
            let mut stmt =
                c.prepare("SELECT seq, kind, payload FROM events WHERE attempt_id=? ORDER BY seq")?;
            let rows = stmt.query_map([aid], |r| {
                let payload: String = r.get(2)?;
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    serde_json::from_str(&payload).unwrap(),
                ))
            })?;
            rows.collect()
        })
        .unwrap()
}

fn arrival(kept: &Kept, aid: &str, seq: i64) -> Instant {
    kept.arrived[&(aid.to_string(), seq)]
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ac3_ac4_ac5_under_load_text_waits_but_first_text_and_control_events_go_at_once() {
    let hub = hub(Behaviour::default()).await;
    let (runner, dir) = runner_at(&hub.url, ADAPTIVE);
    // 29 other turns streaming on this runner: the window is at its 1 s ceiling.
    let others: Vec<_> = (0..29).map(|_| runner.upload_load.streaming()).collect();
    let steps = reply(240, Duration::from_millis(10), 120);
    let recorded = turn(runner.clone(), "a-load".into(), steps).await;
    drop(others);
    let kept = hub.kept.lock().unwrap();
    let late = |seq: i64, at: Instant| arrival(&kept, "a-load", seq).saturating_duration_since(at);
    let first_text = recorded.iter().find(|r| r.1 == "delta").unwrap();
    // AC-3: the first text goes at once despite the 1 s window: a few ms from its row's commit to the hub (timed
    // from the commit, so a slow local disk does not count; held for the window, it would take about 1 s).
    assert!(
        late(first_text.0, first_text.3) < Duration::from_millis(100),
        "{:?}",
        late(first_text.0, first_text.3)
    );
    // AC-4: each tool event and the final message go at once, after every delta before them.
    for (seq, kind, _, written) in recorded
        .iter()
        .filter(|r| r.1 == "tool" || r.1 == "message")
    {
        assert!(
            late(*seq, *written) < Duration::from_millis(100),
            "{kind} {seq}: {:?}",
            late(*seq, *written)
        );
        for (before, ..) in recorded.iter().filter(|r| r.0 < *seq) {
            assert!(arrival(&kept, "a-load", *before) <= arrival(&kept, "a-load", *seq));
        }
    }
    // AC-5: no delta waited much past the maximum age (1 s from its commit, plus a post).
    for (seq, _, _, written) in recorded.iter().filter(|r| r.1 == "delta") {
        assert!(
            late(*seq, *written) < Duration::from_millis(1150),
            "delta {seq}: {:?}",
            late(*seq, *written)
        );
    }
    // 2.4 s of deltas at a 1 s window: a handful of uploads, not one per 50 ms (about 50).
    let uploads = kept.uploads.len();
    assert!(uploads <= 15, "{uploads} uploads");
    assert!(kept.refused.is_empty(), "{:?}", kept.refused);
    assert_eq!(kept.stored["a-load"], local(&runner, "a-load"));
    std::fs::remove_dir_all(dir).ok();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ac6_ac7_failed_and_lost_answer_uploads_store_every_event_once_in_order_as_the_old_runner_does(
) {
    let steps = || reply(150, Duration::from_millis(3), 50);
    // Today's pacing, with no failures: the reference record.
    let reference = hub(Behaviour::default()).await;
    let (old, old_dir) = runner_at(&reference.url, FIXED);
    turn(old.clone(), "a-1".into(), steps()).await;
    // Adaptive pacing under load: the first upload refused, the second stored but its answer lost, so the third
    // resends what the hub already has.
    let failing = hub(Behaviour {
        fail: [0].into(),
        drop_answer: [1].into(),
        ..Default::default()
    })
    .await;
    let (new, new_dir) = runner_at(&failing.url, ADAPTIVE);
    let others: Vec<_> = (0..9).map(|_| new.upload_load.streaming()).collect();
    turn(new.clone(), "a-1".into(), steps()).await;
    drop(others);
    let (was, now) = (reference.kept.lock().unwrap(), failing.kept.lock().unwrap());
    // Lossless: every event the runner recorded is stored once, in seq order, with its kind and data.
    let stored = &now.stored["a-1"];
    let seqs: Vec<i64> = stored.iter().map(|r| r.0).collect();
    assert_eq!(seqs, (1..=stored.len() as i64).collect::<Vec<_>>());
    assert_eq!(*stored, local(&new, "a-1"));
    assert!(now.refused.is_empty(), "{:?}", now.refused);
    // AC-7: identical to what today's runner stores for the same turn.
    assert_eq!(*stored, was.stored["a-1"]);
    // The failures happened, and every ack answered moved forward.
    assert_eq!(now.uploads.iter().filter(|u| u.2.is_none()).count(), 2);
    assert!(now.uploads.len() >= 3, "{}", now.uploads.len());
    let acks: Vec<i64> = now.uploads.iter().filter_map(|u| u.2).collect();
    assert!(acks.windows(2).all(|w| w[0] <= w[1]), "{acks:?}");
    // The runner's own ack ends at the last event.
    let last = new
        .state
        .with(|c| {
            c.query_row("SELECT ack FROM attempts WHERE id='a-1'", [], |r| {
                r.get::<_, i64>(0)
            })
        })
        .unwrap();
    assert_eq!(last, stored.len() as i64);
    std::fs::remove_dir_all(old_dir).ok();
    std::fs::remove_dir_all(new_dir).ok();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ac5_a_delta_written_during_a_slow_upload_waits_at_most_the_maximum_age_from_its_write() {
    // A hub write of 300 ms: rows written while an upload is in flight are seen by the stream loop only after it.
    // Their age counts from their write, so they are sent within 1 s of it, not 1 s after the upload ended.
    let hub = hub(Behaviour {
        write: Duration::from_millis(300),
        ..Default::default()
    })
    .await;
    let (runner, dir) = runner_at(&hub.url, ADAPTIVE);
    let others: Vec<_> = (0..29).map(|_| runner.upload_load.streaming()).collect();
    // No tool events (they go at once and would hide the age bound), and 50 deltas a second so a full upload (100
    // rows) never comes first; the stream loop is the only uploader.
    let steps = reply(150, Duration::from_millis(20), 1000);
    let recorded = turn_with(runner.clone(), "a-slow".into(), steps, false).await;
    drop(others);
    let kept = hub.kept.lock().unwrap();
    let mut worst = Duration::ZERO;
    for (seq, _, _, written) in recorded.iter().filter(|r| r.1 == "delta") {
        let sent = kept.sent[&("a-slow".to_string(), *seq)];
        worst = worst.max(sent.saturating_duration_since(*written));
    }
    // From its write, a delta is sent within the 1 s maximum age (plus scheduling); counted from when the stream
    // loop first saw it, the rows written as an upload began would go about 1.3 s after their write.
    assert!(worst < Duration::from_millis(1150), "{worst:?}");
    assert!(kept.refused.is_empty(), "{:?}", kept.refused);
    assert_eq!(kept.stored["a-slow"], local(&runner, "a-slow"));
    std::fs::remove_dir_all(dir).ok();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn idle_turns_do_not_widen_the_window_and_a_turn_counts_out_a_second_after_its_last_upload() {
    let hub = hub(Behaviour::default()).await;
    let (runner, dir) = runner_at(&hub.url, ADAPTIVE);
    // Twenty turns whose stream loops run but record nothing (waiting on a tool or a person).
    let idle: Vec<_> = (0..20)
        .map(|n| {
            let aid = format!("a-idle-{n:02}");
            runner.state.record(&json!({"id": aid})).unwrap();
            tokio::spawn(
                runner
                    .clone()
                    .stream_loop(aid, Arc::new(Notify::new()), flags()),
            )
        })
        .collect();
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(runner.upload_load.turns(), 0);
    // One streaming turn: about 1.2 s of deltas paces at the 50 ms floor, not at the 1 s that 21 turns would set.
    let steps = reply(120, Duration::from_millis(10), 1000);
    turn_with(runner.clone(), "a-busy".into(), steps, false).await;
    let uploads = hub.kept.lock().unwrap().uploads.len();
    assert!(uploads >= 10, "{uploads} uploads");
    // A live stream loop: counted while it has rows and for a second after its last upload, then not.
    let aid = "a-once".to_string();
    runner.state.record(&json!({"id": aid})).unwrap();
    let wake = Arc::new(Notify::new());
    let once = tokio::spawn(
        runner
            .clone()
            .stream_loop(aid.clone(), wake.clone(), flags()),
    );
    runner
        .state
        .append(&aid, "status", &json!({"waiting": ""}))
        .unwrap();
    wake.notify_one();
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(runner.state.pending(&aid).unwrap().is_empty());
    assert_eq!(runner.upload_load.turns(), 1);
    tokio::time::sleep(Duration::from_millis(1000)).await;
    assert_eq!(runner.upload_load.turns(), 0);
    once.abort();
    for h in idle {
        h.abort();
    }
    std::fs::remove_dir_all(dir).ok();
}

/// How long a control event recorded during an upload takes to reach the hub when the store's write connection
/// stalls for 1 s (other turns' appends and fsyncs) just as that upload's answer arrives.
async fn control_behind_a_stalled_ack(bounds: Bounds) -> Duration {
    let hub = hub(Behaviour {
        write: Duration::from_millis(200),
        ..Default::default()
    })
    .await;
    let (runner, dir) = runner_at(&hub.url, bounds);
    let aid = "a-stall".to_string();
    runner.state.record(&json!({"id": aid})).unwrap();
    for n in 0..5 {
        runner
            .state
            .append(&aid, "delta", &json!({"text": format!("Acme line {n}. ")}))
            .unwrap();
    }
    let first = tokio::spawn({
        let (runner, aid) = (runner.clone(), aid.clone());
        async move { runner.flush(&aid).await }
    });
    tokio::time::sleep(Duration::from_millis(50)).await;
    let seq = runner
        .state
        .append(
            &aid,
            "tool",
            &json!({"item_id": "call_1", "name": "shell", "phase": "started"}),
        )
        .unwrap();
    let written = Instant::now();
    // The stream loop's flush for the tool event, waiting on the one in flight.
    let second = tokio::spawn({
        let (runner, aid) = (runner.clone(), aid.clone());
        async move { runner.flush(&aid).await }
    });
    tokio::time::sleep(Duration::from_millis(50)).await;
    let state = runner.state.clone();
    let stall = std::thread::spawn(move || {
        state.with(|_| {
            std::thread::sleep(Duration::from_secs(1));
            Ok(())
        })
    });
    first.await.unwrap().unwrap();
    second.await.unwrap().unwrap();
    stall.join().unwrap().unwrap();
    let kept = hub.kept.lock().unwrap();
    assert!(kept.refused.is_empty(), "{:?}", kept.refused);
    assert_eq!(kept.stored[&aid], local(&runner, &aid));
    let late = arrival(&kept, &aid, seq).saturating_duration_since(written);
    std::fs::remove_dir_all(dir).ok();
    late
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ac4_a_control_event_does_not_wait_for_the_store_to_write_the_previous_ack() {
    // Lock released when the server answers: the tool event waits only for the upload in flight (about 150 ms
    // left) and its own (200 ms), not for the stalled ack write.
    let now = control_behind_a_stalled_ack(ADAPTIVE).await;
    assert!(now < Duration::from_millis(800), "{now:?}");
    // The old order, ack written under the lock: it waits for the stall too.
    let before = control_behind_a_stalled_ack(Bounds {
        ack_unlocked: false,
        ..ADAPTIVE
    })
    .await;
    assert!(before > Duration::from_millis(1000), "{before:?}");
}

fn percentile(values: &mut [Duration], p: f64) -> Duration {
    if values.is_empty() {
        return Duration::ZERO;
    }
    values.sort();
    let at = ((values.len() as f64 * p).ceil() as usize).clamp(1, values.len()) - 1;
    values[at]
}

fn ms(d: Duration) -> f64 {
    d.as_secs_f64() * 1000.0
}

/// One bench row: uploads per turn, then upload p95, input p95, first p50, first p95, control p95, first p95 w and
/// control p95 w, in ms.
type Row = [f64; 8];

fn row_text(name: &str, turns: usize, r: &Row) -> String {
    format!(
        "{name:<9} {turns:>5} {:>13.1} {:>12.1} {:>12.1} {:>11.1} {:>11.1} {:>14.1} {:>13.1} {:>15.1}",
        r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
    )
}

/// Measure 1 and 2 of the I07 spec: `turns` synthetic turns at once on one runner against the synthetic hub, each
/// streaming the same reply. Returns the row and checks the stored runs are lossless.
async fn bench_once(bounds: Bounds, turns: usize, deltas: usize) -> Row {
    let hub = hub(Behaviour {
        write: Duration::from_millis(3),
        per_event: Duration::from_micros(15),
        poll: Duration::from_micros(1500),
        ..Default::default()
    })
    .await;
    let (runner, dir) = runner_at(&hub.url, bounds);
    let handles: Vec<_> = (0..turns)
        .map(|n| {
            let runner = runner.clone();
            tokio::spawn(async move {
                // Staggered starts, as turns claimed one after another.
                tokio::time::sleep(Duration::from_millis(7 * n as u64)).await;
                let aid = format!("a-bench-{n:02}");
                let recorded = turn(
                    runner,
                    aid.clone(),
                    reply(deltas, Duration::from_millis(10), 400),
                )
                .await;
                (aid, recorded)
            })
        })
        .collect();
    let mut runs = vec![];
    for h in handles {
        runs.push(h.await.unwrap());
    }
    let kept = hub.kept.lock().unwrap();
    let (mut first, mut control) = (vec![], vec![]);
    // The same from the row's commit: pacing and the post alone, without the local disk write.
    let (mut first_w, mut control_w) = (vec![], vec![]);
    for (aid, recorded) in &runs {
        assert_eq!(
            kept.stored[aid],
            local(&runner, aid),
            "{aid} lost or changed events"
        );
        let text = recorded.iter().find(|r| r.1 == "delta").unwrap();
        first.push(arrival(&kept, aid, text.0).saturating_duration_since(text.2));
        first_w.push(arrival(&kept, aid, text.0).saturating_duration_since(text.3));
        for (seq, _, at, written) in recorded
            .iter()
            .filter(|r| !super::MAY_WAIT.contains(&r.1) && r.0 > 1)
        {
            control.push(arrival(&kept, aid, *seq).saturating_duration_since(*at));
            control_w.push(arrival(&kept, aid, *seq).saturating_duration_since(*written));
        }
    }
    assert!(kept.refused.is_empty(), "{:?}", kept.refused);
    let mut uploads: Vec<Duration> = kept.uploads.iter().map(|u| u.1).collect();
    let mut polls = kept.polls.clone();
    let row = [
        kept.uploads.len() as f64 / turns as f64,
        ms(percentile(&mut uploads, 0.95)),
        ms(percentile(&mut polls, 0.95)),
        ms(percentile(&mut first, 0.50)),
        ms(percentile(&mut first, 0.95)),
        ms(percentile(&mut control, 0.95)),
        ms(percentile(&mut first_w, 0.95)),
        ms(percentile(&mut control_w, 0.95)),
    ];
    std::fs::remove_dir_all(dir).ok();
    row
}

/// bench/perf-event-uploads.py runs this; TICO_BENCH_DELTAS sets the reply length (default 2,000 deltas, 20 s),
/// TICO_BENCH_TURNS the sizes (default 1,10,30) and TICO_BENCH_RUNS the rounds (default 3). Each round runs both
/// runners at every size, the first runner alternating by round and size, so a machine that slows during a round
/// does not always count against the same one; the median of each column over the rounds closes the output.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore]
async fn upload_bench() {
    let number = |name: &str, default: usize| {
        std::env::var(name)
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(default)
    };
    let deltas = number("TICO_BENCH_DELTAS", 2000);
    let rounds = number("TICO_BENCH_RUNS", 3).max(1);
    let sizes: Vec<usize> = std::env::var("TICO_BENCH_TURNS")
        .ok()
        .map(|v| v.split(',').filter_map(|n| n.trim().parse().ok()).collect())
        .unwrap_or_else(|| vec![1, 10, 30]);
    println!(
        "\n{deltas} deltas a turn, 10 ms apart; times in ms; control = status, tool, final message; \
         w = from the row's commit to the hub, not from the event"
    );
    let header = format!(
        "{:<9} {:>5} {:>13} {:>12} {:>12} {:>11} {:>11} {:>14} {:>13} {:>15}",
        "runner",
        "turns",
        "uploads/turn",
        "upload p95",
        "input p95",
        "first p50",
        "first p95",
        "control p95",
        "first p95 w",
        "control p95 w"
    );
    let runners = [("fixed", FIXED), ("adaptive", ADAPTIVE)];
    let mut rows: HashMap<(&str, usize), Vec<Row>> = HashMap::new();
    for round in 0..rounds {
        println!("\nround {} of {rounds}\n{header}", round + 1);
        for (n, &turns) in sizes.iter().enumerate() {
            let mut order = runners;
            if (round + n) % 2 == 1 {
                order.reverse();
            }
            for (name, bounds) in order {
                let row = bench_once(bounds, turns, deltas).await;
                println!("{}", row_text(name, turns, &row));
                rows.entry((name, turns)).or_default().push(row);
            }
        }
    }
    println!("\nmedian of {rounds} rounds\n{header}");
    for &turns in &sizes {
        for (name, _) in runners {
            let runs = &rows[&(name, turns)];
            let mut median = [0.0; 8];
            for (column, value) in median.iter_mut().enumerate() {
                let mut values: Vec<f64> = runs.iter().map(|r| r[column]).collect();
                values.sort_by(f64::total_cmp);
                *value = values[values.len() / 2];
            }
            println!("{}", row_text(name, turns, &median));
        }
    }
}
