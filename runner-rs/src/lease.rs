//! Lease renewal on a thread of its own. A turn's lease is what tells the hub its run is alive, so its renewal must
//! not wait for a free async worker: the runner's runtime has two, and synchronous work on them (a checkout's secret
//! scrub, git, the state database) could hold both. On Oct 17-18 eight finished turns stopped renewing right after
//! the model's last word, their leases lapsed and the hub let the jobs go before the results came in (Dev 242).
//! Renewals run on one current-thread runtime on a dedicated OS thread, with an HTTP client of their own, so a
//! connection is never shared between the two runtimes.

use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use serde_json::json;
use tokio::runtime::Handle;

use crate::client::Client;
use crate::execute::Flags;
use crate::wire::{decode, RenewAnswer};

/// The lease thread's runtime, started on first use and kept for the life of the process.
pub(crate) fn handle() -> &'static Handle {
    static HANDLE: OnceLock<Handle> = OnceLock::new();
    HANDLE.get_or_init(|| {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::Builder::new()
            .name("tico-lease".into())
            .spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("lease runtime");
                tx.send(rt.handle().clone()).expect("lease handle");
                rt.block_on(std::future::pending::<()>());
            })
            .expect("lease thread");
        rx.recv().expect("lease runtime started")
    })
}

/// Renew `aid` every `every` on the lease thread until the turn is done; abort the handle to stop it sooner.
pub(crate) fn spawn(client: &Client, aid: String, flags: Arc<Flags>, every: Duration) -> tokio::task::JoinHandle<()> {
    handle().spawn(renew_loop(client.detached(), aid, flags, every))
}

/// A definite answer about the attempt ends the turn; an unreachable hub does not.
async fn renew_loop(client: Client, aid: String, flags: Arc<Flags>, every: Duration) {
    loop {
        tokio::select! {
            _ = tokio::time::sleep(every) => {}
            _ = flags.done_notify.notified() => return,
        }
        if flags.done.load(Ordering::SeqCst) {
            return;
        }
        let before = Instant::now();
        let renewed = client.post(&format!("attempts/{aid}/renew"), &json!({}), None).await;
        match renewed.and_then(decode::<RenewAnswer>) {
            Ok(reply) => {
                let lease = reply.lease_seconds.unwrap_or(60.0);
                *flags.deadline.lock().unwrap() = before + Duration::from_secs_f64((lease - 10.0).max(0.0));
                if reply.stop == Some(true) {
                    // A person pressed Stop, or the bot was quarantined (its `stop_reason`): the turn is interrupted
                    // and reported as interrupted, not lost.
                    if let Some(why) = reply.stop_reason.filter(|why| !why.is_empty()) {
                        *flags.stop_reason.lock().unwrap() = Some(why.to_string());
                    }
                    flags.stopped.store(true, Ordering::SeqCst);
                }
            }
            Err(e) if !e.retryable => {
                flags.lost.store(true, Ordering::SeqCst);
                return;
            }
            Err(_) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;
    use tokio::sync::Notify;

    fn flags() -> Arc<Flags> {
        Arc::new(Flags {
            lost: AtomicBool::new(false),
            stopped: AtomicBool::new(false),
            stop_reason: Mutex::new(None),
            done: AtomicBool::new(false),
            done_notify: Notify::new(),
            deadline: Mutex::new(Instant::now()),
        })
    }

    /// The hub, served from a thread of its own so blocking the runner's workers cannot stall it.
    fn hub(replies: Vec<(u16, &'static str)>) -> (String, Arc<Mutex<Vec<String>>>) {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
            rt.block_on(async move {
                tx.send(crate::client::tests::serve(replies).await).unwrap();
                std::future::pending::<()>().await
            });
        });
        rx.recv().unwrap()
    }

    #[test]
    fn renewals_go_on_while_every_async_worker_is_blocked() {
        let ok = r#"{"lease_until":"x","lease_seconds":90}"#;
        let (url, seen) = hub(vec![(200, ok); 8]);
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
        let flags = flags();
        rt.block_on(async {
            let renewer = spawn(&client, "a1".into(), flags.clone(), Duration::from_millis(50));
            // Both workers busy with synchronous work, the way a long secret scrub or a held state lock would be.
            let block = || tokio::spawn(async { std::thread::sleep(Duration::from_millis(600)) });
            let (a, b) = (block(), block());
            std::thread::sleep(Duration::from_millis(600));
            let renewed = seen.lock().unwrap().len();
            assert!(renewed >= 3, "only {renewed} renewals while the workers were blocked");
            assert!(seen.lock().unwrap().iter().all(|r| r.starts_with("POST /api/v2/attempts/a1/renew ")));
            let _ = (a.await, b.await);
            renewer.abort();
        });
        assert!(!flags.lost.load(Ordering::SeqCst));
    }

    #[test]
    fn a_stop_with_a_reason_stops_the_turn_with_that_reason_and_does_not_lose_the_lease() {
        // Dev 573: a quarantined bot's renewal is a Stop with the quarantine's reason, not a stale lease.
        let stop = r#"{"lease_until":"x","lease_seconds":90,"stop":true,"stop_reason":"Quarantined: 10 refusals today, resumes about 10:05 UTC"}"#;
        let (url, _) = hub(vec![(200, stop); 4]);
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        let flags = flags();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            let renewer = spawn(&client, "a3".into(), flags.clone(), Duration::from_millis(20));
            tokio::time::timeout(Duration::from_secs(2), async {
                while !flags.stopped.load(Ordering::SeqCst) {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            }).await.expect("renewal did not stop the turn");
            renewer.abort();
        });
        assert!(!flags.lost.load(Ordering::SeqCst));
        assert_eq!(flags.stop_reason.lock().unwrap().as_deref(), Some("Quarantined: 10 refusals today, resumes about 10:05 UTC"));
    }

    #[test]
    fn a_definite_refusal_ends_the_renewals_and_marks_the_lease_lost() {
        let (url, seen) = hub(vec![(409, r#"{"error":{"code":"stale_lease","detail":"Lease expired","retryable":false}}"#)]);
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        let flags = flags();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            let renewer = spawn(&client, "a2".into(), flags.clone(), Duration::from_millis(20));
            tokio::time::timeout(Duration::from_secs(5), renewer).await.expect("renewer ended").unwrap();
        });
        assert!(flags.lost.load(Ordering::SeqCst));
        assert_eq!(seen.lock().unwrap().len(), 1);
    }

    #[test]
    fn the_turn_ending_stops_the_renewals() {
        let (url, _) = hub(vec![]);
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        let flags = flags();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            let renewer = spawn(&client, "a3".into(), flags.clone(), Duration::from_secs(3600));
            // `notify_waiters` wakes only a renewer already waiting, as at the end of a turn.
            tokio::time::sleep(Duration::from_millis(100)).await;
            flags.done.store(true, Ordering::SeqCst);
            flags.done_notify.notify_waiters();
            tokio::time::timeout(Duration::from_secs(5), renewer).await.expect("renewer ended").unwrap();
        });
    }
}
