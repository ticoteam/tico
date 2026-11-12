//! Tico Recorder's own updates. The app reads the signed release feed on launch and every six hours and
//! downloads a newer build in the background. Installing waits for `Recorder::update_hold` to clear, and it
//! never restarts the app by itself:
//! - the person clicks Restart: install and restart now (when nothing holds the app);
//! - no window has had focus for ten minutes: install, the new build starts at the next launch;
//! - the app quits with a downloaded build: install before exit, so the next launch is the new build.
//!
//! While anything holds the app (a recording, a live or paused meeting, a send) the page shows no update banner
//! and no launch prompt; `held` says so. The banner shows the first line of the release's notes when it is a
//! person's note (see `human_notes`), never the "Built from <sha>" placeholder.
//!
//! The page reads `status.update` from `view` and calls `update_check`, `update_install` and
//! `move_to_applications`. A copy that cannot replace itself (translocated, on a disk image, in a folder this
//! user cannot write; see `app_location`) does not check: the Updates row offers Move to Applications instead.
//! The app menu's "Check for Updates…" opens Settings at the Updates row and checks.
use super::{Shared, lock_shared};
use serde_json::{Value, json};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};
#[cfg(target_os = "macos")]
use tauri::menu::MenuItem;
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent, menu::Menu};
use tauri_plugin_updater::{Update, UpdaterExt};
use tico_recorder_native::app_location::{self, Existing, Location};

/// The app menu item's id.
pub const MENU_CHECK: &str = "check-updates";

/// The first check waits this long, so launch is not slowed down.
const FIRST_CHECK: Duration = Duration::from_secs(20);
const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
/// How often the background thread looks at a downloaded build.
const LOOK_EVERY: Duration = Duration::from_secs(30);
/// Without any focused window for this long, a downloaded build installs in the background.
pub const IDLE_BEFORE_INSTALL: Duration = Duration::from_secs(10 * 60);
/// How long quitting waits for a background install that is already running.
const QUIT_WAIT: Duration = Duration::from_secs(60);

/// Why an install is being considered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Intent {
    /// The background thread: install only after a long idle stretch, and never restart.
    Background,
    /// The person clicked Restart: install and restart.
    Restart,
    /// The app is quitting: install before exit, so the next launch is the new build.
    Quit,
}

/// What is waiting to be installed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Build {
    None,
    /// Downloaded and verified, not installed yet.
    Downloaded,
    /// Installed on disk; the next launch runs it.
    Installed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Wait,
    Install,
    InstallAndRestart,
}

/// The install policy as a pure decision. `hold` is `Recorder::update_hold`; `idle` is how long no window
/// has had focus (None while one has focus).
pub fn decide(intent: Intent, build: Build, hold: Option<&str>, idle: Option<Duration>) -> Step {
    if build == Build::None || hold.is_some() {
        return Step::Wait;
    }
    match intent {
        Intent::Restart => Step::InstallAndRestart,
        Intent::Background
            if build == Build::Downloaded && idle.is_some_and(|d| d >= IDLE_BEFORE_INSTALL) =>
        {
            Step::Install
        }
        Intent::Quit if build == Build::Downloaded => Step::Install,
        _ => Step::Wait,
    }
}

/// `decide` plus the install admission, taken under the recorder's lock and then the updates lock:
/// - a step other than Wait takes `updating` (no recording, live meeting or send starts) and `installing`
///   (an install is accepted, even before it writes anything);
/// - while another call owns `updating` (the background thread and Restart can overlap) this one waits,
///   and only the owner clears it;
/// - once the app is quitting, only the quit itself may install.
pub fn admit(
    updating: &mut bool,
    installing: &mut bool,
    quitting: bool,
    intent: Intent,
    build: Build,
    hold: Option<&str>,
    idle: Option<Duration>,
) -> Step {
    if *updating || (quitting && intent != Intent::Quit) {
        return Step::Wait;
    }
    let step = decide(intent, build, hold, idle);
    if step != Step::Wait {
        *updating = true;
        *installing = true;
    }
    step
}

/// Quitting: no install is admitted from here on but the quit's own, and the answer is whether one
/// already admitted is still going (the quit waits for it).
pub fn begin_quit(quitting: &mut bool, installing: bool) -> bool {
    *quitting = true;
    installing
}

/// The release notes line the banner shows: the first non-empty line, or "" when there is none or it is the
/// release build's placeholder ("Built from <sha>").
pub fn human_notes(raw: Option<&str>) -> String {
    let line = raw
        .unwrap_or("")
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("");
    let placeholder = line.strip_prefix("Built from ").is_some_and(|sha| {
        !sha.is_empty()
            && sha
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    });
    if placeholder {
        String::new()
    } else {
        line.to_string()
    }
}

/// What `status.update` shows. Shared by the view, the check thread, window events and the commands.
#[derive(Clone)]
pub struct Updates(Arc<Mutex<Pending>>);

struct Pending {
    /// none (not checked yet), checking, current (up to date), downloading, ready, installed, error.
    state: &'static str,
    version: String,
    notes: String,
    /// Why the last check, download or install failed (state "error").
    error: &'static str,
    /// Download progress, 0-100, while downloading with a known size.
    progress: Option<u8>,
    /// This build's version.
    current: String,
    /// The running .app bundle, when there is one (not under `cargo run`).
    bundle: Option<PathBuf>,
    location: Location,
    /// Show the launch prompt to move: this copy cannot update and is not in an Applications folder.
    prompt: bool,
    /// A downloaded build waiting to be installed.
    ready: Option<(Update, Vec<u8>)>,
    checking: bool,
    /// An install was admitted and has not finished (quitting waits for it).
    installing: bool,
    /// The app is quitting: no new install starts but the quit's own.
    quitting: bool,
    /// When no window had focus last; None while a window has focus.
    unfocused_since: Option<Instant>,
}

impl Default for Updates {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Pending {
            state: "none",
            version: String::new(),
            notes: String::new(),
            error: "",
            progress: None,
            current: String::new(),
            bundle: None,
            location: Location::Ok,
            prompt: false,
            ready: None,
            checking: false,
            installing: false,
            quitting: false,
            // The app starts unfocused; the first focus event clears this.
            unfocused_since: Some(Instant::now()),
        })))
    }
}

impl Pending {
    fn build(&self) -> Build {
        if self.state == "installed" {
            Build::Installed
        } else if self.ready.is_some() {
            Build::Downloaded
        } else {
            Build::None
        }
    }

    fn idle_for(&self) -> Option<Duration> {
        self.unfocused_since.map(|at| at.elapsed())
    }
}

impl Updates {
    fn lock(&self) -> MutexGuard<'_, Pending> {
        self.0.lock().unwrap_or_else(|error| error.into_inner())
    }

    /// `status.update`. `blocked` is why an install waits right now (from `Recorder::update_hold`); `held`
    /// says whether anything holds the app, whatever the update's state.
    pub fn status(&self, blocked: Option<&str>) -> Value {
        let pending = self.lock();
        let held = blocked.is_some();
        let movable = !pending.location.can_update() && pending.bundle.is_some();
        let blocked = match pending.state {
            "ready" | "installed" => blocked.unwrap_or(""),
            _ => "",
        };
        let progress = match pending.state {
            "downloading" => pending.progress.map_or(Value::Null, |p| json!(p)),
            _ => Value::Null,
        };
        json!({"state": pending.state, "version": pending.version,
            "notes": pending.notes, "blocked": blocked, "error": pending.error,
            "progress": progress, "current": pending.current,
            "location": pending.location.line(), "movable": movable,
            "prompt": movable && pending.prompt, "held": held})
    }

    /// Records this build's version and where it runs from. Called once at launch.
    pub fn locate(&self, current: String) {
        let bundle = std::env::current_exe()
            .ok()
            .and_then(|exe| app_location::bundle_of(&exe));
        let location = bundle.as_deref().map_or(Location::Ok, |b| {
            app_location::check(b, app_location::writable)
        });
        let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
        let prompt = bundle
            .as_deref()
            .is_some_and(|b| !app_location::in_applications(b, &home));
        let mut pending = self.lock();
        pending.current = current;
        pending.bundle = bundle;
        pending.location = location;
        pending.prompt = prompt;
    }

    fn set(&self, app: &AppHandle, state: &'static str, version: &str, error: &'static str) {
        {
            let mut pending = self.lock();
            pending.state = state;
            pending.version = version.into();
            pending.error = error;
            pending.progress = None;
        }
        // The window redraws from `view` after a change; this event is what asks it to.
        let _ = app.emit("changed", 0);
    }

    #[cfg(test)]
    fn idle_for(&self) -> Option<Duration> {
        self.lock().idle_for()
    }

    /// Tracks focus for the idle rule. A closed or hidden window counts as unfocused.
    pub fn window_event(&self, event: &WindowEvent) {
        let mut pending = self.lock();
        match event {
            WindowEvent::Focused(true) => pending.unfocused_since = None,
            WindowEvent::Focused(false)
            | WindowEvent::CloseRequested { .. }
            | WindowEvent::Destroyed => {
                pending.unfocused_since.get_or_insert_with(Instant::now);
            }
            _ => {}
        }
    }
}

/// The background thread: a check on launch and every six hours, and an install check every 30 seconds.
/// Runs whether or not the window is visible.
pub fn start(app: AppHandle, recorder: Shared, updates: Updates) {
    std::thread::Builder::new()
        .name("tico-recorder-update".into())
        .spawn(move || {
            std::thread::sleep(FIRST_CHECK);
            let mut checked: Option<Instant> = None;
            loop {
                if checked.is_none_or(|at| at.elapsed() >= CHECK_EVERY) {
                    checked = Some(Instant::now());
                    check(&app, &updates);
                }
                apply(&app, &recorder, &updates, Intent::Background);
                std::thread::sleep(LOOK_EVERY);
            }
        })
        .expect("recorder update thread");
}

/// Reads the feed and downloads a newer build. One check at a time, and a build already downloaded is kept.
/// A copy that cannot replace itself does not check.
fn check(app: &AppHandle, updates: &Updates) {
    {
        let mut pending = updates.lock();
        if pending.checking
            || pending.ready.is_some()
            || pending.state == "installed"
            || !pending.location.can_update()
        {
            return;
        }
        pending.checking = true;
    }
    updates.set(app, "checking", "", "");
    match tauri::async_runtime::block_on(async {
        let updater = app.updater().map_err(|error| error.to_string())?;
        updater.check().await.map_err(|error| error.to_string())
    }) {
        Ok(Some(update)) => {
            let version = update.version.clone();
            updates.lock().notes = human_notes(update.body.as_deref());
            updates.set(app, "downloading", &version, "");
            let mut got = 0u64;
            let on_chunk = |chunk: usize, total: Option<u64>| {
                got += chunk as u64;
                let Some(total) = total.filter(|t| *t > 0) else {
                    return;
                };
                let percent = (got.saturating_mul(100) / total).min(100) as u8;
                let mut pending = updates.lock();
                if pending.progress != Some(percent) {
                    pending.progress = Some(percent);
                    drop(pending);
                    let _ = app.emit("changed", 0);
                }
            };
            match tauri::async_runtime::block_on(update.download(on_chunk, || {})) {
                Ok(bytes) => {
                    updates.lock().ready = Some((update, bytes));
                    updates.set(app, "ready", &version, "");
                }
                Err(error) => {
                    eprintln!("update download failed: {error}");
                    updates.set(app, "error", &version, "Download failed");
                }
            }
        }
        Ok(None) => updates.set(app, "current", "", ""),
        Err(error) => {
            eprintln!("update check failed: {error}");
            updates.set(app, "error", "", "Couldn't check for updates");
        }
    }
    updates.lock().checking = false;
}

/// Applies the install policy for `intent`. Returns true when it installed or restarted.
fn apply(app: &AppHandle, recorder: &Shared, updates: &Updates, intent: Intent) -> bool {
    // Admission happens under the recorder lock and then the updates lock, so no recording, live meeting or
    // send can start in between, and a quit sees an admitted install even before it writes anything. Neither
    // lock is kept: quitting (the restart below, too) takes the recorder lock on the main thread.
    let step = {
        let mut shared = lock_shared(recorder);
        let hold = shared.update_hold();
        let mut pending = updates.lock();
        let (build, idle, quitting) = (pending.build(), pending.idle_for(), pending.quitting);
        admit(
            &mut shared.updating,
            &mut pending.installing,
            quitting,
            intent,
            build,
            hold,
            idle,
        )
    };
    if step == Step::Wait {
        return false;
    }
    // Taken in its own statement: the Pending lock must be free again before `updates.set` below.
    let ready = updates.lock().ready.take();
    let installed = match ready {
        Some((update, bytes)) => {
            let version = update.version.clone();
            let result = update.install(&bytes);
            updates.lock().installing = false;
            match result {
                Ok(()) => {
                    updates.set(app, "installed", &version, "");
                    true
                }
                Err(error) => {
                    eprintln!("update install failed: {error}");
                    updates.set(app, "error", &version, "Install failed");
                    false
                }
            }
        }
        None => {
            updates.lock().installing = false;
            true
        }
    };
    if installed && step == Step::InstallAndRestart {
        // Restart is the last step: the process exits and the installed build starts. `updating` stays set.
        app.restart();
    }
    lock_shared(recorder).updating = false;
    installed
}

/// Called when the app exits: installs a downloaded build if nothing holds the app. No restart.
pub fn on_quit(app: &AppHandle, recorder: &Shared, updates: &Updates) {
    // No background install starts from here on, and one already admitted finishes first (even one that has
    // not started writing), so the app never exits halfway through an install. Restart's own reservation is
    // `updating`, not `installing`, so a restart does not wait here.
    let busy = {
        let mut pending = updates.lock();
        let installing = pending.installing;
        begin_quit(&mut pending.quitting, installing)
    };
    if busy {
        let deadline = Instant::now() + QUIT_WAIT;
        while updates.lock().installing && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    apply(app, recorder, updates, Intent::Quit);
}

/// Checks now, from the page. A check already running is not doubled.
#[tauri::command(async)]
pub fn update_check(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
) -> Value {
    check(&app, &updates);
    updates.status(lock_shared(&recorder).update_hold())
}

/// Restart clicked: installs the downloaded build and restarts now, or says why it waits.
#[tauri::command(async)]
pub fn update_install(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
) -> Value {
    apply(&app, &recorder, &updates, Intent::Restart);
    updates.status(lock_shared(&recorder).update_hold())
}

/// The default macOS menus with "Check for Updates…" under About in the app menu. No menu bar item.
pub fn app_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::default(app)?;
    #[cfg(target_os = "macos")]
    if let Some(first) = menu.items()?.into_iter().next()
        && let Some(app_menu) = first.as_submenu()
    {
        let item = MenuItem::with_id(app, MENU_CHECK, "Check for Updates…", true, None::<&str>)?;
        app_menu.insert(&item, 1)?;
    }
    Ok(menu)
}

/// "Check for Updates…": shows the window on Settings at the Updates row and checks (off the main thread).
pub fn from_menu(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
    let _ = app.emit("open-updates", 0);
    let app = app.clone();
    std::thread::spawn(move || {
        let updates = app.state::<Updates>();
        check(&app, &updates);
    });
}

/// Move to Applications. `how`: "copy" (asks first when a newer copy is already there, by returning
/// `{"ask": version}`), "replace" (replace it anyway) or "open" (open the copy already there). On success
/// this copy quits and the moved one opens; otherwise `{"error": …}`.
#[tauri::command(async)]
pub fn move_to_applications(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
    how: String,
) -> Value {
    if let Some(hold) = lock_shared(&recorder).update_hold() {
        return json!({"error": hold});
    }
    let (bundle, current) = {
        let pending = updates.lock();
        (pending.bundle.clone(), pending.current.clone())
    };
    let Some(bundle) = bundle else {
        return json!({"error": "Not running as an app"});
    };
    let Some(name) = bundle.file_name() else {
        return json!({"error": "Not running as an app"});
    };
    let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
    let target = app_location::target_folder(&home, app_location::writable).join(name);
    let found = target
        .exists()
        .then(|| app_location::bundle_version(&target));
    let moved = match (
        how.as_str(),
        app_location::existing(found.as_deref(), &current),
    ) {
        ("open", _) if target.exists() => Ok(target),
        ("copy", Existing::Newer(version)) => return json!({"ask": version}),
        ("copy" | "replace", _) => {
            app_location::copy_to(&bundle, target.parent().unwrap_or(&target))
        }
        _ => return json!({"error": "Nothing to open"}),
    };
    match moved.and_then(|path| app_location::open_after_exit(&path, std::process::id())) {
        Ok(()) => {
            app.exit(0);
            json!({})
        }
        Err(error) => {
            eprintln!("move to Applications failed: {error:#}");
            json!({"error": "Couldn't move the app"})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LONG: Option<Duration> = Some(Duration::from_secs(10 * 60));
    const SHORT: Option<Duration> = Some(Duration::from_secs(60));
    const HOLD: Option<&str> = Some("After this recording");

    #[test]
    fn nothing_downloaded_means_nothing_to_do() {
        for intent in [Intent::Background, Intent::Restart, Intent::Quit] {
            assert_eq!(decide(intent, Build::None, None, LONG), Step::Wait);
        }
    }

    #[test]
    fn a_hold_waits_for_every_intent() {
        for intent in [Intent::Background, Intent::Restart, Intent::Quit] {
            for build in [Build::Downloaded, Build::Installed] {
                assert_eq!(decide(intent, build, HOLD, LONG), Step::Wait);
            }
        }
    }

    #[test]
    fn restart_click_installs_and_restarts_when_free() {
        assert_eq!(
            decide(Intent::Restart, Build::Downloaded, None, SHORT),
            Step::InstallAndRestart
        );
        assert_eq!(
            decide(Intent::Restart, Build::Installed, None, None),
            Step::InstallAndRestart
        );
    }

    #[test]
    fn background_waits_until_ten_minutes_without_focus() {
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, None),
            Step::Wait
        );
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, SHORT),
            Step::Wait
        );
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, LONG),
            Step::Install
        );
    }

    #[test]
    fn background_never_restarts_and_does_not_reinstall() {
        assert_eq!(
            decide(Intent::Background, Build::Installed, None, LONG),
            Step::Wait
        );
    }

    #[test]
    fn quit_installs_without_restart() {
        assert_eq!(
            decide(Intent::Quit, Build::Downloaded, None, None),
            Step::Install
        );
        assert_eq!(
            decide(Intent::Quit, Build::Installed, None, None),
            Step::Wait
        );
    }

    #[test]
    fn status_says_why_a_ready_build_waits_and_nothing_else() {
        let updates = Updates::default();
        assert_eq!(
            updates.status(Some("After this recording")),
            json!({
                "state": "none", "version": "", "notes": "", "blocked": "", "error": "",
                "progress": null, "current": "", "location": "", "movable": false,
                "prompt": false, "held": true
            })
        );
        updates.lock().state = "ready";
        assert_eq!(
            updates.status(Some("After this recording"))["blocked"],
            "After this recording"
        );
        assert_eq!(updates.status(None)["blocked"], "");
        updates.lock().state = "installed";
        assert_eq!(
            updates.status(Some("After the live meeting ends"))["blocked"],
            "After the live meeting ends"
        );
        updates.lock().state = "downloading";
        assert_eq!(updates.status(Some("After this recording"))["blocked"], "");
    }

    #[test]
    fn focus_resets_the_idle_clock() {
        let updates = Updates::default();
        updates.window_event(&WindowEvent::Focused(true));
        assert_eq!(updates.idle_for(), None);
        updates.window_event(&WindowEvent::Focused(false));
        assert!(updates.idle_for().is_some());
        updates.window_event(&WindowEvent::Focused(true));
        assert_eq!(updates.idle_for(), None);
    }

    #[test]
    fn status_shows_each_check_state() {
        let updates = Updates::default();
        updates.lock().current = "0.5.143".into();
        assert_eq!(updates.status(None)["current"], "0.5.143");
        for state in ["checking", "current", "ready", "installed"] {
            updates.lock().state = state;
            let status = updates.status(None);
            assert_eq!(status["state"], state);
            assert_eq!(status["progress"], Value::Null);
            assert_eq!(status["error"], "");
        }
        // Progress shows only while downloading.
        {
            let mut pending = updates.lock();
            pending.state = "downloading";
            pending.progress = Some(42);
        }
        assert_eq!(updates.status(None)["progress"], 42);
        updates.lock().state = "ready";
        assert_eq!(updates.status(None)["progress"], Value::Null);
        {
            let mut pending = updates.lock();
            pending.state = "error";
            pending.error = "Couldn't check for updates";
        }
        assert_eq!(updates.status(None)["error"], "Couldn't check for updates");
    }

    #[test]
    fn a_copy_that_cannot_update_offers_the_move() {
        let updates = Updates::default();
        {
            let mut pending = updates.lock();
            pending.location = Location::Translocated;
            pending.bundle =
                Some("/private/var/folders/x/AppTranslocation/1/d/Tico Recorder.app".into());
        }
        let status = updates.status(None);
        assert_eq!(status["location"], "Can't update from here");
        assert_eq!(status["movable"], true);
        // The launch prompt shows only away from the Applications folders (`locate` decides).
        assert_eq!(status["prompt"], false);
        updates.lock().prompt = true;
        assert_eq!(updates.status(None)["prompt"], true);
        // Without a bundle (cargo run) nothing can be moved.
        updates.lock().bundle = None;
        assert_eq!(updates.status(None)["movable"], false);
        assert_eq!(updates.status(None)["prompt"], false);
        updates.lock().bundle = Some("/Volumes/Tico Recorder/Tico Recorder.app".into());
        updates.lock().location = Location::Ok;
        assert_eq!(updates.status(None)["location"], "");
        // A copy that updates fine is never prompted, wherever it is.
        assert_eq!(updates.status(None)["prompt"], false);
    }

    #[test]
    fn a_held_app_admits_no_install_from_any_path() {
        for intent in [Intent::Background, Intent::Restart, Intent::Quit] {
            let (mut updating, mut installing) = (false, false);
            let step = admit(
                &mut updating,
                &mut installing,
                intent == Intent::Quit,
                intent,
                Build::Downloaded,
                Some("After the live meeting ends"),
                LONG,
            );
            assert_eq!(step, Step::Wait, "{intent:?}");
            assert!(!updating && !installing, "{intent:?}");
        }
        // Once free, the idle and quit paths install.
        let (mut updating, mut installing) = (false, false);
        let idle = admit(
            &mut updating,
            &mut installing,
            false,
            Intent::Background,
            Build::Downloaded,
            None,
            LONG,
        );
        assert_eq!(idle, Step::Install);
        let (mut updating, mut installing) = (false, false);
        let quit = admit(
            &mut updating,
            &mut installing,
            true,
            Intent::Quit,
            Build::Downloaded,
            None,
            None,
        );
        assert_eq!(quit, Step::Install);
    }

    #[test]
    fn notes_show_a_persons_line_and_never_the_build_placeholder() {
        assert_eq!(human_notes(Some("Built from 2d1fcb1")), "");
        assert_eq!(human_notes(Some("  Built from 2d1fcb1d5e  \n")), "");
        assert_eq!(human_notes(None), "");
        assert_eq!(human_notes(Some("\n \n")), "");
        assert_eq!(
            human_notes(Some("\nCopy transcript and @me.\nMore detail.")),
            "Copy transcript and @me."
        );
        // Only the exact placeholder is hidden.
        assert_eq!(
            human_notes(Some("Built from scratch")),
            "Built from scratch"
        );
        assert_eq!(human_notes(Some("Built from 2D1F")), "Built from 2D1F");
    }
}
