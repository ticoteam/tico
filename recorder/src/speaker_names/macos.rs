//! Read-only AX transport, scoped to a Meet document in an allowlisted browser. Never uses
//! AX actions, screenshots, keyboard shortcuts or browser automation. The one write is
//! Chromium's `AXManualAccessibility` switch on the browser app, which makes it build the
//! page's AX tree (it stays empty for assistive clients otherwise). AXValue is read only for
//! static text inside a participant row or a video tile's name container.
use super::{
    MeetingAdapter, Observation, POLL_MS, Samples,
    meet::{self, GoogleMeet, Node},
};
use crate::store::Store;
use core_foundation::{
    array::CFArray,
    base::{CFType, CFTypeRef, TCFType},
    dictionary::CFDictionary,
    number::CFNumber,
    string::CFString,
    url::CFURL,
};
use objc2_app_kit::NSRunningApplication;
use std::{
    collections::BTreeSet,
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
const PROMPT_FILE: &str = "speaker-accessibility-dismissed";
static PERMISSION: AtomicBool = AtomicBool::new(false);
pub fn permission_pending() -> bool {
    PERMISSION.load(Ordering::Acquire)
}
pub fn dismiss_permission(root: &Path) {
    let _ = std::fs::write(root.join(PROMPT_FILE), b"1");
    PERMISSION.store(false, Ordering::Release);
}
pub fn open_settings(root: &Path) {
    // Lists the app in the Accessibility pane; macOS shows its own prompt the first time.
    let options = CFDictionary::from_CFType_pairs(&[(
        CFString::new("AXTrustedCheckOptionPrompt"),
        core_foundation::boolean::CFBoolean::true_value(),
    )]);
    unsafe {
        AXIsProcessTrustedWithOptions(options.as_concrete_TypeRef().cast());
    }
    let opened = std::process::Command::new("/usr/bin/open")
        .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    if opened {
        dismiss_permission(root);
    }
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
/// (pid, a window title looks like Meet, Chromium).
fn browser_windows() -> Vec<(i32, bool, bool)> {
    let raw = unsafe { CGWindowListCopyWindowInfo(16, 0) }; // Include minimized windows; exclude desktop.
    if raw.is_null() {
        return vec![];
    }
    let windows = unsafe { CFArray::<CFDictionary<CFString, CFType>>::wrap_under_create_rule(raw) };
    let mut pids = std::collections::BTreeMap::new();
    for window in windows.iter().take(200) {
        let Some(pid) = window
            .find(CFString::new("kCGWindowOwnerPID"))
            .and_then(|v| v.downcast::<CFNumber>())
            .and_then(|n| n.to_i32())
        else {
            continue;
        };
        let Some(app) = NSRunningApplication::runningApplicationWithProcessIdentifier(pid) else {
            continue;
        };
        let Some(bundle) = app.bundleIdentifier().map(|id| id.to_string()) else {
            continue;
        };
        if !BROWSERS.contains(&bundle.as_str()) {
            continue;
        }
        // Window titles are used only to detect Meet when AX is denied; they are never stored.
        let is_meet = window
            .find(CFString::new("kCGWindowName"))
            .and_then(|v| v.downcast::<CFString>())
            .is_some_and(|s| meet::window_title(&s.to_string()));
        let entry = pids
            .entry(pid)
            .or_insert((false, CHROMIUM.contains(&bundle.as_str())));
        entry.0 |= is_meet;
    }
    pids.into_iter()
        .map(|(pid, (meet, chromium))| (pid, meet, chromium))
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
fn poll(canceled: Arc<AtomicBool>, woken: &mut BTreeSet<i32>) -> (bool, Observation) {
    objc2::rc::autoreleasepool(|_| poll_inner(canceled, woken))
}
/// Chromium returns kAXErrorAttributeUnsupported here yet still enables its tree, which fills
/// in over the next polls. Asked once per browser process; later polls retry the walk.
fn wake(app: &CFType) {
    let key = CFString::new("AXManualAccessibility");
    unsafe {
        AXUIElementSetAttributeValue(
            app.as_CFTypeRef(),
            key.as_CFTypeRef(),
            core_foundation::boolean::CFBoolean::true_value().as_CFTypeRef(),
        );
    }
}
fn poll_inner(canceled: Arc<AtomicBool>, woken: &mut BTreeSet<i32>) -> (bool, Observation) {
    let windows = browser_windows();
    if !unsafe { AXIsProcessTrusted() } {
        return (
            windows.iter().any(|(_, meet, _)| *meet),
            Observation::default(),
        );
    }
    woken.retain(|pid| windows.iter().any(|(p, _, _)| p == pid));
    PERMISSION.store(false, Ordering::Release);
    let mut budget = Budget {
        until: Instant::now() + Duration::from_millis(150),
        left: 1800,
        canceled,
    };
    let mut found = vec![];
    for (pid, _, chromium) in windows {
        let raw = unsafe { AXUIElementCreateApplication(pid) };
        if raw.is_null() {
            continue;
        }
        let app = unsafe { CFType::wrap_under_create_rule(raw) };
        unsafe {
            AXUIElementSetMessagingTimeout(app.as_CFTypeRef(), 0.025);
        }
        let windows = elements(&app, "AXWindows");
        // Chromium page accessibility slows the browser, so only for a window showing Meet.
        if chromium
            && !woken.contains(&pid)
            && windows
                .iter()
                .any(|w| meet::window_title(&text(w, "AXTitle")))
        {
            woken.insert(pid);
            wake(&app);
        }
        for window in &windows {
            let Some(document) = document(&window, 0, &mut budget) else {
                continue;
            };
            let nodes = children(&document)
                .iter()
                .filter_map(|e| project(e, 0, false, &mut budget))
                .collect();
            // An incomplete tree cannot safely say that a speaker stopped or a second speaker
            // was absent. Close the previous evidence rather than applying a partial result.
            if budget.left == 0 || Instant::now() >= budget.until {
                return (false, Observation::default());
            }
            found.push(GoogleMeet.observe(&Node {
                role: "AXGroup".into(),
                children: nodes,
                ..Default::default()
            }));
        }
    }
    // Two meetings open at once have no safe audio-to-window association.
    if found.len() == 1 {
        (false, found.pop().unwrap())
    } else {
        (false, Observation::default())
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
                let mut samples = Samples::default();
                let mut participants = BTreeSet::new();
                let mut locals = BTreeSet::new();
                let mut woken = BTreeSet::new();
                while !canceled.load(Ordering::Acquire) {
                    let tick = Instant::now();
                    let (denied, observed) = poll(canceled.clone(), &mut woken);
                    if denied && !store.root().join(PROMPT_FILE).exists() {
                        PERMISSION.store(true, Ordering::Release);
                    }
                    let at = offset + started.elapsed().as_millis() as u64;
                    let intervals = samples.push(at, observed.clone());
                    let mut new_names = false;
                    for name in &observed.participants {
                        new_names |= participants.insert(name.clone());
                    }
                    for name in &observed.self_names {
                        new_names |= locals.insert(name.clone());
                    }
                    if (!intervals.is_empty() || new_names) && !canceled.load(Ordering::Acquire) {
                        if store.observe_speakers(&id, &observed, &intervals).is_err() {
                            break;
                        }
                    }
                    thread::park_timeout(
                        Duration::from_millis(POLL_MS).saturating_sub(tick.elapsed()),
                    );
                }
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
