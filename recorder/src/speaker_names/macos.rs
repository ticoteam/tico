//! Read-only AX transport, scoped to a Meet document in an allowlisted browser. Never uses
//! AX actions, screenshots, keyboard shortcuts or browser automation. The one write is
//! Chromium's `AXManualAccessibility` switch on the browser app, which makes it build the
//! page's AX tree (it stays empty for assistive clients otherwise). AXValue is read only for
//! static text inside a participant row or a video tile's name container.
use super::{
    Batch, MeetingAdapter, Observation, POLL_MS, Samples,
    meet::{self, GoogleMeet, Node},
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
/// Until a Meet document is found, look this rarely; a found meeting is read every [`POLL_MS`].
const FIND_MS: u64 = 2000;
/// Evidence is written in batches this far apart, and once more when the watch ends.
const FLUSH_MS: u64 = 1000;
const PROMPT_FILE: &str = "speaker-accessibility-dismissed";
const ASKED_FILE: &str = "speaker-accessibility-prompted";
const MANUAL: &str = "AXManualAccessibility";
/// Set while this recording's watch saw a supported browser without Accessibility access.
static PERMISSION: AtomicBool = AtomicBool::new(false);
pub fn permission_pending() -> bool {
    PERMISSION.load(Ordering::Acquire)
}
/// Whether the recorder may read Meet's accessibility tree.
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
        if !seen.contains_key(&pid) {
            let id = bundles.remove(&pid).unwrap_or_else(|| bundle(pid));
            seen.insert(pid, id);
        }
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
/// tree. Discovery only reads roles/URLs; name/state labels are read inside the Meet document.
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
fn document(element: &CFType, depth: usize, budget: &mut Budget) -> Option<CFType> {
    if depth > 12 || !budget.take() {
        return None;
    }
    let role = text(element, "AXRole");
    if role == "AXWebArea" {
        return GoogleMeet
            .detect_window("", &url(element))
            .then(|| element.clone());
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
fn project(
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
        .filter(|s| meet::kept_class(s))
        .collect();
    let name_container = name_container
        || meet::name_container(&Node {
            classes: classes.clone(),
            ..Default::default()
        });
    let mut label = string(1);
    if meet::private_branch(&role, &label) {
        return None;
    }
    let description = string(2);
    if meet::private_branch(&role, &description) {
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
            .filter_map(|child| project(child, depth + 1, name_container, budget))
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
}
#[derive(Default)]
struct Poll {
    /// A supported browser is running but Accessibility is not allowed.
    denied: bool,
    /// A Meet document was found, so the next poll comes at the full rate.
    found: bool,
    observed: Observation,
    info: PollInfo,
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
        // Only a window titled like Meet is walked; other tabs and windows are never read.
        let windows: Vec<_> = elements(&app, "AXWindows")
            .into_iter()
            .filter(|w| meet::window_title(&text(w, "AXTitle")))
            .collect();
        // Chromium page accessibility slows the browser, so only for a window showing Meet.
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
            let Some(document) = document(window, 0, &mut budget) else {
                continue;
            };
            let nodes = children(&document)
                .iter()
                .filter_map(|e| project(e, 0, false, &mut budget))
                .collect();
            // An incomplete tree cannot safely say that a speaker stopped or a second speaker
            // was absent. Close the previous evidence rather than applying a partial result.
            info.browser.clone_from(&browser);
            if budget.left == 0 || Instant::now() >= budget.until {
                return Poll {
                    found: true,
                    info: PollInfo {
                        found: true,
                        nodes: 1800 - budget.left,
                        truncated: true,
                        ..info
                    },
                    ..Default::default()
                };
            }
            found.push(GoogleMeet.observe(&Node {
                role: "AXGroup".into(),
                children: nodes,
                ..Default::default()
            }));
        }
    }
    info.found = !found.is_empty();
    info.nodes = 1800 - budget.left;
    Poll {
        denied: false,
        found: !found.is_empty(),
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
                    let at = offset + started.elapsed().as_millis() as u64;
                    let intervals = samples.push(at, polled.observed.clone());
                    for line in log.poll(&polled.info, &polled.observed, &intervals, at) {
                        crate::diag!("{line}");
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
                    let every = if polled.found { POLL_MS } else { FIND_MS };
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
