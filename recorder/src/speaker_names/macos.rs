//! Read-only AX transport, scoped to a meeting document (Google Meet, or Microsoft Teams on the
//! web) in an allowlisted browser, read by the adapter whose address it has. Never uses AX
//! actions, screenshots, keyboard shortcuts or browser automation. The one write is Chromium's
//! `AXManualAccessibility` switch on the browser app, which makes it build the page's AX tree
//! (it stays empty for assistive clients otherwise; Chromium can answer the switch with an
//! error yet build the tree, and the result is never relied on). AXValue is read only for
//! static text inside a participant row or a video tile's name container. While no meeting
//! document is found, other browser windows' tab strips are read for tab titles only (never
//! pages), so a call in a background tab can be named in the log and the window.
use super::{
    ADAPTERS, Batch, MeetingAdapter, Observation, POLL_MS, Samples,
    meet::Node,
    watch_log::{PollInfo, WatchLog},
};
use crate::store::Store;
use core_foundation::{
    array::CFArray,
    base::{CFType, CFTypeRef, TCFType},
    boolean::CFBoolean,
    dictionary::CFDictionary,
    number::CFNumber,
    string::CFString,
    url::CFURL,
};
use objc2_app_kit::NSRunningApplication;
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::c_void,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    fn AXIsProcessTrusted() -> bool;
    fn AXUIElementCreateApplication(pid: i32) -> CFTypeRef;
    fn AXUIElementCopyAttributeValue(
        element: CFTypeRef,
        attribute: *const c_void,
        value: *mut CFTypeRef,
    ) -> i32;
    fn AXUIElementSetMessagingTimeout(element: CFTypeRef, seconds: f32) -> i32;
    fn AXUIElementSetAttributeValue(
        element: CFTypeRef,
        attribute: *const c_void,
        value: CFTypeRef,
    ) -> i32;
    fn AXIsProcessTrustedWithOptions(options: *const c_void) -> bool;
    fn AXUIElementCopyMultipleAttributeValues(
        element: CFTypeRef,
        attributes: core_foundation::array::CFArrayRef,
        options: u32,
        values: *mut core_foundation::array::CFArrayRef,
    ) -> i32;
}
#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    fn CGWindowListCopyWindowInfo(
        options: u32,
        relative: u32,
    ) -> core_foundation::array::CFArrayRef;
}
const BROWSERS: &[&str] = &[
    "com.google.Chrome",
    "company.thebrowser.Browser",
    "com.microsoft.edgemac",
    "com.brave.Browser",
    "com.apple.Safari",
];
const CHROMIUM: &[&str] = &[
    "com.google.Chrome",
    "company.thebrowser.Browser",
    "com.microsoft.edgemac",
    "com.brave.Browser",
];
/// Until a meeting document shows a call, look this rarely; a call is read every [`POLL_MS`].
const FIND_MS: u64 = 2000;
/// Evidence is written in batches this far apart, and once more when the watch ends.
const FLUSH_MS: u64 = 1000;
const PROMPT_FILE: &str = "speaker-accessibility-dismissed";
const ASKED_FILE: &str = "speaker-accessibility-prompted";
const MANUAL: &str = "AXManualAccessibility";
/// While no meeting document is found, tab strips are read this rarely, and this many nodes.
const TAB_SCAN_MS: u64 = 10_000;
const TAB_NODES: usize = 300;
/// Set while this recording's watch saw a supported browser without Accessibility access.
static PERMISSION: AtomicBool = AtomicBool::new(false);
/// The meeting app whose call this recording's watch saw in a background tab, until its
/// document is found or the tab is gone.
static HIDDEN: std::sync::Mutex<Option<&'static str>> = std::sync::Mutex::new(None);
pub fn hidden_call() -> Option<&'static str> {
    *HIDDEN.lock().unwrap_or_else(|error| error.into_inner())
}
fn set_hidden(app: Option<&'static str>) {
    *HIDDEN.lock().unwrap_or_else(|error| error.into_inner()) = app;
}
pub fn permission_pending() -> bool {
    PERMISSION.load(Ordering::Acquire)
}
/// Whether the recorder may read meeting pages' accessibility tree.
pub fn access() -> Option<bool> {
    Some(unsafe { AXIsProcessTrusted() })
}
pub fn dismiss_permission(root: &Path) {
    let _ = std::fs::write(root.join(PROMPT_FILE), b"1");
    PERMISSION.store(false, Ordering::Release);
}
/// The first Allow shows macOS's own prompt, which also lists the app in the Accessibility
/// pane; later ones open the pane. Never both at once, and neither counts as a dismissal.
pub fn open_settings(root: &Path) {
    if unsafe { AXIsProcessTrusted() } {
        return;
    }
    let asked = root.join(ASKED_FILE);
    if !asked.exists() {
        let _ = std::fs::write(&asked, b"1");
        let options = CFDictionary::from_CFType_pairs(&[(
            CFString::new("AXTrustedCheckOptionPrompt"),
            CFBoolean::true_value(),
        )]);
        unsafe {
            AXIsProcessTrustedWithOptions(options.as_concrete_TypeRef().cast());
        }
        return;
    }
    let _ = std::process::Command::new("/usr/bin/open")
        .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
}
fn attr(element: &CFType, key: &str) -> Option<CFType> {
    unsafe {
        AXUIElementSetMessagingTimeout(element.as_CFTypeRef(), 0.025);
    }
    let mut value = std::ptr::null();
    let key = CFString::new(key);
    (unsafe {
        AXUIElementCopyAttributeValue(element.as_CFTypeRef(), key.as_CFTypeRef(), &mut value)
    } == 0
        && !value.is_null())
    .then(|| unsafe { CFType::wrap_under_create_rule(value) })
}
fn text(element: &CFType, key: &str) -> String {
    attr(element, key)
        .and_then(|v| v.downcast::<CFString>())
        .map(|s| s.to_string())
        .unwrap_or_default()
}
fn url(element: &CFType) -> String {
    for key in ["AXURL", "AXDocument"] {
        if let Some(value) = attr(element, key) {
            if let Some(url) = value.downcast::<CFURL>() {
                return url.get_string().to_string();
            }
            if let Some(text) = value.downcast::<CFString>() {
                return text.to_string();
            }
        }
    }
    String::new()
}
fn children(element: &CFType) -> Vec<CFType> {
    elements(element, "AXChildren")
}
fn elements(element: &CFType, key: &str) -> Vec<CFType> {
    attr(element, key)
        .and_then(|v| v.downcast::<CFArray>())
        .map(|array| {
            array
                .iter()
                .take(200)
                .map(|e| unsafe { CFType::wrap_under_get_rule(*e) })
                .collect()
        })
        .unwrap_or_default()
}
fn array(value: &CFType) -> Vec<CFType> {
    value
        .downcast::<CFArray>()
        .map(|a| {
            a.iter()
                .take(200)
                .map(|p| unsafe { CFType::wrap_under_get_rule(*p) })
                .collect()
        })
        .unwrap_or_default()
}
fn metadata(element: &CFType) -> Vec<CFType> {
    let keys: Vec<_> = [
        "AXRole",
        "AXTitle",
        "AXDescription",
        "AXDOMClassList",
        "AXChildren",
    ]
    .iter()
    .map(|key| CFString::new(key).as_CFType())
    .collect();
    let keys = CFArray::from_CFTypes(&keys);
    let mut values = std::ptr::null();
    unsafe {
        AXUIElementSetMessagingTimeout(element.as_CFTypeRef(), 0.025);
    }
    if unsafe {
        AXUIElementCopyMultipleAttributeValues(
            element.as_CFTypeRef(),
            keys.as_concrete_TypeRef(),
            0,
            &mut values,
        )
    } != 0
        || values.is_null()
    {
        return vec![];
    }
    let values = unsafe { CFType::wrap_under_create_rule(values.cast()) };
    array(&values)
}
fn bundle(pid: i32) -> Option<String> {
    NSRunningApplication::runningApplicationWithProcessIdentifier(pid)?
        .bundleIdentifier()
        .map(|id| id.to_string())
}
/// (pid, Chromium) for each supported browser with a window. Window titles here would need
/// Screen Recording, so they are not read; `bundles` caches each window owner's bundle id
/// while that process keeps a window.
fn browser_windows(bundles: &mut BTreeMap<i32, Option<String>>) -> Vec<(i32, bool)> {
    let raw = unsafe { CGWindowListCopyWindowInfo(16, 0) }; // Include minimized windows; exclude desktop.
    if raw.is_null() {
        return vec![];
    }
    let windows = unsafe { CFArray::<CFDictionary<CFString, CFType>>::wrap_under_create_rule(raw) };
    let mut seen = BTreeMap::new();
    for window in windows.iter().take(200) {
        let Some(pid) = window
            .find(CFString::new("kCGWindowOwnerPID"))
            .and_then(|v| v.downcast::<CFNumber>())
            .and_then(|n| n.to_i32())
        else {
            continue;
        };
        seen.entry(pid)
            .or_insert_with(|| bundles.remove(&pid).unwrap_or_else(|| bundle(pid)));
    }
    *bundles = seen;
    bundles
        .iter()
        .filter_map(|(pid, id)| {
            let id = id.as_deref()?;
            BROWSERS
                .contains(&id)
                .then(|| (*pid, CHROMIUM.contains(&id)))
        })
        .collect()
}

/// The budget bounds work even when a browser hangs or returns a very large accessibility
/// tree. Discovery only reads roles/URLs; name/state labels are read inside the meeting document.
struct Budget {
    until: Instant,
    left: usize,
    canceled: Arc<AtomicBool>,
}
impl Budget {
    fn take(&mut self) -> bool {
        if self.left == 0 || Instant::now() >= self.until || self.canceled.load(Ordering::Acquire) {
            return false;
        }
        self.left -= 1;
        true
    }
}
/// The window's page and the meeting app it shows.
fn document(
    element: &CFType,
    depth: usize,
    budget: &mut Budget,
) -> Option<(CFType, &'static dyn MeetingAdapter)> {
    if depth > 12 || !budget.take() {
        return None;
    }
    let role = text(element, "AXRole");
    if role == "AXWebArea" {
        let url = url(element);
        return ADAPTERS
            .iter()
            .find(|meeting| meeting.detect_window("", &url))
            .map(|meeting| (element.clone(), *meeting));
    }
    if matches!(role.as_str(), "AXTextArea" | "AXTextField") {
        return None;
    }
    for child in children(element) {
        if let Some(document) = document(&child, depth + 1, budget) {
            return Some(document);
        }
    }
    None
}
fn selected(tab: &CFType) -> bool {
    attr(tab, "AXValue").is_some_and(|value| {
        value.downcast::<CFBoolean>().is_some_and(bool::from)
            || value
                .downcast::<CFNumber>()
                .and_then(|n| n.to_i64())
                .is_some_and(|n| n != 0)
    })
}
/// The meeting app with a call in a tab behind this window's front tab, read from its tab strip:
/// roles and tab titles only. The walk never enters a page, a text field or a scroll area.
fn hidden_tab(window: &CFType, budget: &mut Budget) -> Option<&'static str> {
    let mut level = vec![(window.clone(), false)];
    for _ in 0..8 {
        let mut next = vec![];
        for (element, in_strip) in &level {
            if !budget.take() {
                return None;
            }
            let role = text(element, "AXRole");
            if *in_strip {
                let title = text(element, "AXTitle");
                if let Some(meeting) = ADAPTERS.iter().find(|meeting| meeting.call_tab(&title)) {
                    if !selected(element) {
                        return Some(meeting.app());
                    }
                    continue;
                }
            }
            if matches!(
                role.as_str(),
                "AXWebArea" | "AXScrollArea" | "AXTextArea" | "AXTextField"
            ) {
                continue;
            }
            let strip = *in_strip || role == "AXTabGroup";
            next.extend(children(element).into_iter().map(|child| (child, strip)));
        }
        if next.is_empty() {
            break;
        }
        level = next;
    }
    None
}
fn project(
    app: &dyn MeetingAdapter,
    element: &CFType,
    depth: usize,
    name_container: bool,
    budget: &mut Budget,
) -> Option<Node> {
    if depth > 24 || !budget.take() {
        return None;
    }
    // One IPC per structural node bounds the cost of a full poll.
    let values = metadata(element);
    if values.len() != 5 {
        return None;
    }
    let string = |i: usize| {
        values[i]
            .downcast::<CFString>()
            .map(|s| s.to_string())
            .unwrap_or_default()
    };
    let role = string(0);
    if role == "AXStaticText" {
        return name_container.then(|| Node {
            role,
            label: text(element, "AXValue"),
            ..Default::default()
        });
    }
    if matches!(
        role.as_str(),
        "AXTextArea" | "AXTextField" | "AXWebArea" | "AXDocument"
    ) {
        return None;
    }
    let classes: Vec<String> = array(&values[3])
        .iter()
        .filter_map(|p| p.downcast::<CFString>())
        .map(|s| s.to_string())
        .filter(|s| app.kept_class(s))
        .collect();
    let name_container = name_container
        || app.name_container(&Node {
            role: role.clone(),
            classes: classes.clone(),
            ..Default::default()
        });
    let mut label = string(1);
    if app.private_branch(&role, &label) {
        return None;
    }
    let description = string(2);
    if app.private_branch(&role, &description) {
        return None;
    }
    if label.is_empty() {
        label = description.clone();
    }
    // Do not descend into images/canvases (including shared screen contents).
    let descendants = if matches!(role.as_str(), "AXImage" | "AXCanvas") {
        vec![]
    } else {
        array(&values[4])
            .iter()
            .filter_map(|child| project(app, child, depth + 1, name_container, budget))
            .collect()
    };
    Some(Node {
        role,
        label: label.chars().take(200).collect(),
        description: description.chars().take(200).collect(),
        classes,
        children: descendants,
    })
}
#[derive(Default)]
struct Discovery {
    bundles: BTreeMap<i32, Option<String>>,
    /// Browsers whose page accessibility was checked: true when this watch switched it on.
    woken: BTreeMap<i32, bool>,
    /// Accessibility access as last logged.
    trusted: Option<bool>,
    rec: String,
    /// When tab strips were last read for a call in a background tab.
    scanned: Option<Instant>,
}
#[derive(Default)]
struct Poll {
    /// A supported browser is running but Accessibility is not allowed.
    denied: bool,
    /// A meeting document was found.
    found: bool,
    /// It shows a call, so the next poll comes at the full rate.
    fast: bool,
    observed: Observation,
    info: PollInfo,
    /// Tab strips were read this poll: the app with a call in a background tab, if any.
    hidden: Option<Option<&'static str>>,
}
fn poll(canceled: Arc<AtomicBool>, discovery: &mut Discovery) -> Poll {
    objc2::rc::autoreleasepool(|_| poll_inner(canceled, discovery))
}
fn set_manual(pid: i32, on: bool) {
    let raw = unsafe { AXUIElementCreateApplication(pid) };
    if raw.is_null() {
        return;
    }
    let app = unsafe { CFType::wrap_under_create_rule(raw) };
    let key = CFString::new(MANUAL);
    let value = if on {
        CFBoolean::true_value()
    } else {
        CFBoolean::false_value()
    };
    unsafe {
        AXUIElementSetMessagingTimeout(app.as_CFTypeRef(), 0.025);
        AXUIElementSetAttributeValue(app.as_CFTypeRef(), key.as_CFTypeRef(), value.as_CFTypeRef());
    }
}
/// Chromium returns kAXErrorAttributeUnsupported here yet still enables its tree, which fills
/// in over the next polls. Asked once per browser process. A browser that already had it on
/// (another assistive app) is left as it was, including when the watch ends.
fn wake(app: &CFType, pid: i32, browser: &str, rec: &str, woken: &mut BTreeMap<i32, bool>) {
    if woken.contains_key(&pid) {
        return;
    }
    let already = attr(app, MANUAL)
        .and_then(|value| value.downcast::<CFBoolean>())
        .is_some_and(bool::from);
    woken.insert(pid, !already);
    if already {
        crate::diag!(
            "meet: rec={rec} page accessibility already on in {browser} (pid {pid}); left as is"
        );
    } else {
        set_manual(pid, true);
        crate::diag!("meet: rec={rec} page accessibility switched on in {browser} (pid {pid})");
    }
}
/// Switch page accessibility back off in the browsers this watch switched on.
fn unwake(woken: &BTreeMap<i32, bool>, rec: &str) {
    objc2::rc::autoreleasepool(|_| {
        for (pid, _) in woken.iter().filter(|(_, ours)| **ours) {
            match bundle(*pid).filter(|id| CHROMIUM.contains(&id.as_str())) {
                Some(id) => {
                    set_manual(*pid, false);
                    crate::diag!(
                        "meet: rec={rec} page accessibility switched back off in {id} (pid {pid})"
                    );
                }
                None => {
                    crate::diag!("meet: rec={rec} browser pid {pid} gone; nothing to switch off")
                }
            }
        }
    })
}
fn poll_inner(canceled: Arc<AtomicBool>, discovery: &mut Discovery) -> Poll {
    let browsers = browser_windows(&mut discovery.bundles);
    let trusted = unsafe { AXIsProcessTrusted() };
    if discovery.trusted != Some(trusted) {
        discovery.trusted = Some(trusted);
        crate::diag!(
            "meet: rec={} accessibility {}",
            discovery.rec,
            if trusted { "allowed" } else { "not allowed" }
        );
    }
    if !trusted {
        return Poll {
            denied: !browsers.is_empty(),
            ..Default::default()
        };
    }
    PERMISSION.store(false, Ordering::Release);
    let mut budget = Budget {
        until: Instant::now() + Duration::from_millis(150),
        left: 1800,
        canceled,
    };
    let mut found = vec![];
    let mut behind = vec![];
    let mut seen = false;
    let mut call = None;
    let mut info = PollInfo::default();
    for (pid, chromium) in browsers {
        let raw = unsafe { AXUIElementCreateApplication(pid) };
        if raw.is_null() {
            continue;
        }
        let app = unsafe { CFType::wrap_under_create_rule(raw) };
        unsafe {
            AXUIElementSetMessagingTimeout(app.as_CFTypeRef(), 0.025);
        }
        // Only a window titled like a meeting app is walked; other windows' pages are never read.
        let mut windows = vec![];
        for window in elements(&app, "AXWindows") {
            let title = text(&window, "AXTitle");
            // A window's title is its front tab's: one showing a call has no call behind it.
            if !ADAPTERS.iter().any(|meeting| meeting.call_tab(&title)) {
                behind.push(window.clone());
            }
            if ADAPTERS.iter().any(|meeting| meeting.window_title(&title)) {
                windows.push(window);
            }
        }
        // Chromium page accessibility slows the browser, so only for a meeting app's window.
        let browser = discovery
            .bundles
            .get(&pid)
            .cloned()
            .flatten()
            .unwrap_or_default();
        if chromium && !windows.is_empty() {
            wake(&app, pid, &browser, &discovery.rec, &mut discovery.woken);
        }
        info.windows += windows.len();
        for window in &windows {
            let Some((document, meeting)) = document(window, 0, &mut budget) else {
                continue;
            };
            let nodes = children(&document)
                .iter()
                .filter_map(|e| project(meeting, e, 0, false, &mut budget))
                .collect();
            // An incomplete tree cannot safely say that a speaker stopped or a second speaker
            // was absent. Close the previous evidence rather than applying a partial result.
            info.browser.clone_from(&browser);
            info.app = meeting.app();
            seen = true;
            if budget.left == 0 || Instant::now() >= budget.until {
                return Poll {
                    found: true,
                    // Full rate only where the document itself is the call (Meet's address).
                    fast: meeting.in_call(&Observation::default()),
                    info: PollInfo {
                        found: true,
                        nodes: 1800 - budget.left,
                        truncated: true,
                        ..info
                    },
                    ..Default::default()
                };
            }
            let observed = meeting.observe(&Node {
                role: "AXGroup".into(),
                children: nodes,
                ..Default::default()
            });
            // A Teams page without a call (chat, calendar) is no meeting to name.
            if meeting.in_call(&observed) {
                found.push(observed);
                call = Some(meeting.app());
            }
        }
    }
    // No call document: a call may sit in a tab behind another one, which the browser does
    // not expose. Tab strips say so; read them rarely, within what is left of this poll's budget.
    let hidden = (found.is_empty()
        && discovery
            .scanned
            .is_none_or(|at| at.elapsed() >= Duration::from_millis(TAB_SCAN_MS)))
    .then(|| {
        discovery.scanned = Some(Instant::now());
        let mut scan = Budget {
            until: budget.until,
            left: budget.left.min(TAB_NODES),
            canceled: budget.canceled.clone(),
        };
        let app = behind
            .iter()
            .find_map(|window| hidden_tab(window, &mut scan));
        // A scan cut short says nothing about the tabs it did not reach.
        let complete = scan.left > 0 && Instant::now() < scan.until;
        (app.is_some() || complete).then_some(app)
    })
    .flatten();
    info.found = seen;
    info.app = call.unwrap_or(info.app);
    info.nodes = 1800 - budget.left;
    Poll {
        denied: false,
        hidden,
        found: seen,
        fast: !found.is_empty(),
        info,
        // Two meetings open at once have no safe audio-to-window association.
        observed: if found.len() == 1 {
            found.pop().unwrap()
        } else {
            Observation::default()
        },
    }
}
fn flush(store: &Store, id: &str, batch: &mut Batch) {
    if batch.is_empty() {
        return;
    }
    let batch = std::mem::take(batch);
    // A failed write loses this batch only; observation goes on.
    if let Err(error) = store.observe_speakers(id, &batch.people, &batch.intervals, &batch.readable)
    {
        crate::diag!(
            "meet: rec={} speaker evidence not saved: {error:#}",
            &id[..id.len().min(8)]
        );
    }
}

pub struct Watch {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl Watch {
    pub fn start(
        store: Store,
        id: String,
        started: Instant,
        offset: u64,
        stop: Arc<AtomicBool>,
    ) -> Self {
        let canceled = stop.clone();
        let worker = thread::Builder::new()
            .name("tico-meet-speakers".into())
            .spawn(move || {
                crate::qos::background();
                // Each recording asks afresh.
                PERMISSION.store(false, Ordering::Release);
                let mut samples = Samples::default();
                let mut participants = BTreeSet::new();
                let mut locals = BTreeSet::new();
                let mut log = WatchLog::new(&id);
                let mut discovery = Discovery {
                    rec: log.rec().to_owned(),
                    ..Default::default()
                };
                crate::diag!("meet: rec={} watch started", log.rec());
                let mut asked = false;
                // The app with a call in a background tab, and whether the log said so already.
                let mut hidden: Option<&'static str> = None;
                let mut hidden_said = false;
                set_hidden(None);
                let mut batch = Batch::default();
                let mut flushed = Instant::now();
                while !canceled.load(Ordering::Acquire) {
                    let tick = Instant::now();
                    let mut polled = poll(canceled.clone(), &mut discovery);
                    polled.info.ms = tick.elapsed().as_millis() as u64;
                    if polled.denied && !asked {
                        asked = true;
                        let dismissed = store.root().join(PROMPT_FILE).exists();
                        crate::diag!(
                            "meet: rec={} supported browser open without accessibility; notice {}",
                            log.rec(),
                            if dismissed {
                                "dismissed earlier, not shown"
                            } else {
                                "shown"
                            }
                        );
                    }
                    if polled.denied && !store.root().join(PROMPT_FILE).exists() {
                        PERMISSION.store(true, Ordering::Release);
                    }
                    // A call's document read this poll: its names are being read.
                    if polled.fast {
                        hidden = None;
                    } else if let Some(scan) = polled.hidden {
                        hidden = scan;
                    }
                    if let Some(app) = hidden.filter(|_| !hidden_said) {
                        hidden_said = true;
                        crate::diag!(
                            "meet: rec={} {app} call is in a background tab; the browser shows only the front tab's page, so no names until it is in front",
                            log.rec()
                        );
                    }
                    set_hidden(hidden);
                    let at = offset + started.elapsed().as_millis() as u64;
                    let intervals = samples.push(at, polled.observed.clone());
                    for line in log.poll(&polled.info, &polled.observed, &intervals, at) {
                        crate::diag!("{line}");
                    }
                    // A truncated read has no names; the last full one stands until it is stale.
                    if polled.found
                        && !(polled.observed.participants.is_empty()
                            && polled.observed.self_names.is_empty())
                    {
                        super::note_present(&id, &polled.observed);
                    }
                    for name in &polled.observed.participants {
                        if participants.insert(name.clone()) {
                            batch.people.push((name.clone(), false));
                        }
                    }
                    for name in &polled.observed.self_names {
                        if locals.insert(name.clone()) {
                            batch.people.push((name.clone(), true));
                        }
                    }
                    batch.add(intervals, samples.readable);
                    if flushed.elapsed() >= Duration::from_millis(FLUSH_MS) {
                        flush(&store, &id, &mut batch);
                        flushed = Instant::now();
                    }
                    let every = if polled.fast { POLL_MS } else { FIND_MS };
                    thread::park_timeout(
                        Duration::from_millis(every).saturating_sub(tick.elapsed()),
                    );
                }
                flush(&store, &id, &mut batch);
                unwake(&discovery.woken, log.rec());
                for line in log.end() {
                    crate::diag!("{line}");
                }
                PERMISSION.store(false, Ordering::Release);
                set_hidden(None);
            })
            .ok();
        Self { stop, worker }
    }
}
impl Drop for Watch {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            worker.thread().unpark();
            let _ = worker.join();
        }
    }
}
