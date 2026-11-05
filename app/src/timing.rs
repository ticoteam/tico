//! How long an org switch takes, phase by phase: one info line in the app's log per switch. A switch to an org the
//! pointer rested on is timed from the hover, so its line shows the prefetch and the click. Addresses are logged by
//! host only, never a path or query.
use std::time::{Duration, Instant};

use tauri::Manager;

use crate::{App, AppState};

/// One org's switch (or prefetch) being timed.
pub(crate) struct Trace {
    org: String,
    serial: u64,
    start: Instant,
    marks: Vec<(String, u128)>,
    /// Started at a hover: the page is being prefetched.
    prefetched: bool,
    /// The click (or other switch) came; until then a prefetch writes nothing.
    clicked: bool,
    /// The page has called the bridge: the app on it is up.
    ready: bool,
}

/// At most this many marks per switch: a page's frames navigate too, and a line should stay readable.
const MARKS: usize = 40;
/// At most this many orgs timed at once.
const TRACES: usize = 4;
/// A page that never calls the bridge (signed out, or a hub too old) still gets its line this long after it loads.
const AFTER_LOAD: Duration = Duration::from_secs(5);
/// And one that never finishes loading, this long after it started.
const AT_MOST: Duration = Duration::from_secs(30);

impl Trace {
    fn new(org: &str, serial: u64, prefetched: bool) -> Trace {
        Trace { org: org.to_string(), serial, start: Instant::now(), marks: Vec::new(), prefetched, clicked: !prefetched, ready: false }
    }

    fn mark(&mut self, what: &str) {
        if self.marks.len() < MARKS {
            self.marks.push((what.to_string(), self.start.elapsed().as_millis()));
        }
    }

    fn line(&self) -> String {
        line(&self.org, self.prefetched, &self.marks)
    }
}

/// The log line for a switch's marks.
pub(crate) fn line(org: &str, prefetched: bool, marks: &[(String, u128)]) -> String {
    let phases: Vec<String> = marks.iter().map(|(what, ms)| format!("{what} {ms}")).collect();
    let (prefetched, since) = if prefetched { ("yes", "hover") } else { ("no", "click") };
    format!("org switch {org}, prefetched: {prefetched} (ms since {since}): {}", phases.join(", "))
}

fn next_serial() -> u64 {
    static SERIAL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    SERIAL.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
}

fn with<R>(app: &App, f: impl FnOnce(&mut Vec<Trace>) -> R) -> R {
    let state = app.state::<AppState>();
    let mut traces = state.traces.lock().unwrap();
    f(&mut traces)
}

/// Writes a clicked trace with its last mark; a prefetch that was never clicked goes without a line.
fn write(mut trace: Trace, last: &str) {
    if trace.clicked {
        trace.mark(last);
        log::info!("{}", trace.line());
    }
}

/// Starts a new trace for `org`, writing out any it replaces.
fn start(app: &App, org: &str, prefetched: bool, what: &str) {
    let serial = next_serial();
    with(app, |traces| {
        if let Some(index) = traces.iter().position(|trace| trace.org == org) {
            write(traces.remove(index), "superseded");
        }
        if traces.len() >= TRACES {
            write(traces.remove(0), "superseded");
        }
        let mut trace = Trace::new(org, serial, prefetched);
        trace.mark(what);
        traces.push(trace);
    });
    finish_later(app, serial, AT_MOST, "gave up");
}

/// The pointer rested on `org` and its page is being built hidden.
pub(crate) fn hover(app: &App, org: &str) {
    start(app, org, true, "hover");
}

/// The prefetched page of `org` closed unused: its trace goes without a line.
pub(crate) fn drop_prefetch(app: &App, org: &str) {
    with(app, |traces| traces.retain(|trace| trace.org != org || trace.clicked));
}

/// The click on `org` reached the app (or another switch began). A prefetch's trace goes on from its hover.
pub(crate) fn click(app: &App, org: &str, what: &str) {
    let continued = with(app, |traces| match traces.iter_mut().find(|trace| trace.org == org && !trace.clicked) {
        Some(trace) => {
            trace.clicked = true;
            trace.mark(what);
            true
        }
        None => false,
    });
    if !continued {
        start(app, org, false, what);
    }
}

/// Starts timing unless `org` is already timed: a switch that did not come from a click still gets a line.
pub(crate) fn begin(app: &App, org: &str) {
    if !with(app, |traces| traces.iter().any(|trace| trace.org == org)) {
        click(app, org, "switch");
    }
}

/// Marks a phase for `org`, if it is being timed.
pub(crate) fn mark(app: &App, org: &str, what: &str) {
    with(app, |traces| {
        if let Some(trace) = traces.iter_mut().find(|trace| trace.org == org) {
            trace.mark(what);
        }
    });
}

/// Marks the last phase for `org` and writes its line.
pub(crate) fn finish(app: &App, org: &str, what: &str) {
    with(app, |traces| {
        if let Some(index) = traces.iter().position(|trace| trace.org == org) {
            write(traces.remove(index), what);
        }
    });
}

/// The page is on screen. A prefetched page whose app is already up has nothing left to time.
pub(crate) fn shown(app: &App, org: &str) {
    with(app, |traces| {
        let Some(index) = traces.iter().position(|trace| trace.org == org) else { return };
        if traces[index].ready {
            write(traces.remove(index), "shown");
        } else {
            traces[index].mark("shown");
        }
    });
}

/// The page finished loading: a clicked switch's line is written at the page's first bridge call, or a few seconds
/// from now.
pub(crate) fn loaded(app: &App, org: &str, what: &str) {
    let serial = with(app, |traces| {
        let trace = traces.iter_mut().find(|trace| trace.org == org)?;
        trace.mark(what);
        trace.clicked.then_some(trace.serial)
    });
    if let Some(serial) = serial {
        finish_later(app, serial, AFTER_LOAD, "no bridge call yet");
    }
}

/// The page called the bridge for the first time: the app on it is up.
pub(crate) fn ready(app: &App, org: &str, name: &str) {
    with(app, |traces| {
        let Some(index) = traces.iter().position(|trace| trace.org == org && !trace.ready) else { return };
        traces[index].ready = true;
        let what = format!("app ready ({name})");
        if traces[index].clicked {
            write(traces.remove(index), &what);
        } else {
            traces[index].mark(&what);
        }
    });
}

fn finish_later(app: &App, serial: u64, after: Duration, what: &'static str) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(after).await;
        with(&app, |traces| {
            if let Some(index) = traces.iter().position(|trace| trace.serial == serial) {
                if traces[index].clicked || what == "gave up" {
                    write(traces.remove(index), what);
                }
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_switch_line_lists_each_phase_in_order_and_says_whether_it_was_prefetched() {
        let marks = vec![("click".to_string(), 0), ("lock".to_string(), 1), ("nav team.example.com".to_string(), 40)];
        assert_eq!(line("o_aaaaaaaa", false, &marks),
                   "org switch o_aaaaaaaa, prefetched: no (ms since click): click 0, lock 1, nav team.example.com 40");
        assert!(line("o_aaaaaaaa", true, &marks).contains("prefetched: yes (ms since hover)"));
    }

    #[test]
    fn a_trace_keeps_at_most_its_cap_of_marks() {
        let mut trace = Trace::new("o", 1, false);
        for _ in 0..100 { trace.mark("nav"); }
        assert_eq!(trace.marks.len(), MARKS);
        assert!(trace.clicked && !Trace::new("o", 2, true).clicked, "a hover is not a click");
    }
}
