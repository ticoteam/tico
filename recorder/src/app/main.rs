//! Tico Recorder's window: Tauri 2 on the system WebView. The UI in `ui/app` is plain
//! HTML/CSS/JS; it polls `view` and calls the commands below, each a thin wrapper over
//! `Recorder`. Recording, transcription, live sync and drafts run on their own threads.
mod recorder;
mod screen;
#[cfg(test)]
mod tests;
mod update;

use recorder::{Have, Recorder, SettingsForm};
use serde_json::Value;
use std::{
    sync::{Arc, Mutex, MutexGuard},
    time::Duration,
};
use tauri::{Manager, RunEvent, State};
use tico_recorder_native::{
    settings::{self, AppLock},
    store::Store,
};

type Shared = Arc<Mutex<Recorder>>;
fn lock<'a>(state: &'a State<'_, Shared>) -> MutexGuard<'a, Recorder> {
    lock_shared(state)
}
fn lock_shared(shared: &Shared) -> MutexGuard<'_, Recorder> {
    shared.lock().unwrap_or_else(|error| error.into_inner())
}

/// Defines each command as an async (off the main thread) wrapper that locks the recorder.
macro_rules! commands {
    ($($name:ident($($arg:ident: $ty:ty),*) => |$app:ident| $body:expr;)*) => {
        $(
            #[tauri::command(async)]
            fn $name(state: State<'_, Shared>, $($arg: $ty),*) -> Result<Value, String> {
                #[allow(unused_mut)]
                let mut $app = lock(&state);
                // Whatever a command changes, the window redraws from `view` right after; `view`
                // itself then counts as having drawn it.
                $app.bump();
                serde_json::to_value($body).map_err(|error| error.to_string())
            }
        )*
    };
}
/// The page's view, with `status.update` added: the update state lives outside the recorder.
#[tauri::command(async)]
fn view(
    state: State<'_, Shared>,
    updates: State<'_, update::Updates>,
    have: Have,
) -> Result<Value, String> {
    let mut app = lock(&state);
    app.bump();
    let mut view = app.view(&have);
    let blocked = app.update_hold();
    drop(app);
    view["status"]["update"] = updates.status(blocked);
    Ok(view)
}

// Network writes run outside the Recorder mutex, so typing and redraws keep working while a save waits.
#[tauri::command(async)]
fn shared_item(
    state: State<'_, Shared>,
    id: Option<String>,
    action: Option<String>,
    body: Option<Value>,
) -> Result<Value, String> {
    let (config, destination, meeting, epoch) = {
        let app = lock(&state);
        let (destination, meeting) = app.saved_target().ok_or("Save the meeting first")?;
        (app.config.clone(), destination, meeting, app.saved_scope())
    };
    let tico = tico_recorder_native::network::Tico::saved(&config, &destination)
        .map_err(|e| format!("{e:#}"))?;
    let mut path = format!("/api/v2/meetings/{meeting}/items");
    if let Some(id) = &id {
        path.push_str(&format!("/{id}"));
    }
    if action.as_deref() == Some("send") {
        path.push_str("/push");
    }
    let reply = tico
        .call(&path, body.as_ref(), &uuid::Uuid::new_v4().to_string())
        .map_err(|e| format!("{e:#}"));
    let data = tico
        .call(&format!("/api/v2/meetings/{meeting}/items"), None, "")
        .map_err(|e| format!("{e:#}"))?;
    let mut app = lock(&state);
    app.accept_saved_reply((destination, meeting), epoch, data.clone())?;
    match reply {
        Ok(value) => Ok(value),
        Err(error) if error.contains("(version_conflict)") => {
            let item = data["sections"]
                .as_object()
                .into_iter()
                .flat_map(|s| s.values())
                .filter_map(|v| v.as_array())
                .flatten()
                .find(|item| item["id"].as_str() == id.as_deref())
                .cloned();
            Ok(serde_json::json!({"conflict":item,"error":error}))
        }
        Err(error) => Err(error),
    }
}

commands! {
    stop_screen() => |app| app.screen.stop("");
    record(event: Option<Value>) => |app| app.record(event);
    open_meeting(id: String) => |app| { let opened = app.open(&id); app.report(opened) };
    close_meeting() => |app| app.close();
    stop_recording() => |app| app.stop();
    rename(title: String) => |app| app.rename(&title);
    finish_live() => |app| app.finish_live();
    control_live(action: String) => |app| app.control_live(&action);
    edit_shared_line(id: String, seq: i64, text: String) => |app| app.edit_shared_line(&id, seq, Some(&text));
    remove_shared_line(id: String, seq: i64) => |app| app.edit_shared_line(&id, seq, None);
    retry(id: String) => |app| app.retry(&id);
    set_notes(notes: String) => |app| app.set_notes(&notes);
    add_item(title: String) => |app| app.add_item(&title);
    edit_item(id: String, title: String) => |app| app.edit_item(&id, &title);
    set_owner(id: String, owner: String) => |app| app.set_owner(&id, &owner);
    remove_item(id: String) => |app| app.remove_item(&id);
    send_item(id: String, owner: String, paused_confirmed: Option<bool>) => |app| if paused_confirmed.unwrap_or(false) { app.send_item_confirmed(&id, &owner, true) } else { app.send_item(&id, &owner) };
    choose_org(id: String) => |app| app.choose_org(&id);
    change_org() => |app| app.change_org();
    load_roster() => |app| app.load_roster();
    start_adding() => |app| app.start_adding();
    choose_people_org(id: String) => |app| app.choose_people_org(&id);
    pick(actor: String) => |app| app.pick(&actor);
    add_participant(name: String) => |app| app.add_participant(&name);
    remove_access(actor: String) => |app| app.remove_access(&actor);
    set_team(team: bool) => |app| app.set_team(team);
    ask(question: String) => |app| app.ask(&question);
    chat_send(text: String) => |app| app.chat_send(&text);
    set_chat_private(private: bool) => |app| app.set_chat_private(private);
    send_meeting(id: String, destination: String, paused_confirmed: Option<bool>) => |app| app.send_meeting_confirmed(&id, &destination, paused_confirmed.unwrap_or(false));
    delete_recording(id: String) => |app| app.delete(&id);
    share_live(destination: String, team: bool, actors: Vec<String>) => |app| app.share_live(&destination, team, actors);
    save_meeting(id: String, destination: String, audience: String, title: String, paused_confirmed: Option<bool>) => |app| app.save(&id, &destination, &audience, &title, paused_confirmed.unwrap_or(false));
    keep_local(id: String) => |app| app.keep_local(&id);
    save_settings(form: SettingsForm) => |app| app.save_settings(form);
    set_audio(microphone: String, meeting: bool) => |app| app.set_audio(&microphone, meeting);
    sign_in(address: String) => |app| app.sign_in(&address);
    sign_in_desktop(url: Option<String>) => |app| app.sign_in_desktop(url.as_deref());
    make_default(id: String) => |app| app.make_default(&id);
    import_legacy(directory: String) => |app| app.import_legacy(&directory);
    confirm_connect() => |app| app.confirm_connect();
    dismiss_connect() => |app| app.dismiss_connect();
    dismiss_error() => |app| app.dismiss_error();
    open_url(url: String) => |app| app.open_url(&url);
    dismiss_notice() => |app| app.dismiss_notice();
    set_visible(visible: bool) => |app| app.set_visible(visible);
}

#[tauri::command(async)]
fn sign_out(state: State<'_, Shared>, id: String) -> Result<(), String> {
    let screen = lock(&state).screen.clone();
    screen.stop_destination_and_wait(&id);
    let mut app = lock(&state);
    app.sign_out(&id);
    app.bump();
    Ok(())
}

/// The native pasteboard works after an asynchronous server call in WKWebView.
fn copy_meeting_url(handle: &tauri::AppHandle, url: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let text = url.to_owned();
        if handle
            .run_on_main_thread(move || {
                use objc2_app_kit::{NSPasteboard, NSPasteboardTypeString};
                let board = NSPasteboard::generalPasteboard();
                board.clearContents();
                let copied = board
                    .setString_forType(&objc2_foundation::NSString::from_str(&text), unsafe {
                        NSPasteboardTypeString
                    });
                let _ = tx.send(copied);
            })
            .is_err()
        {
            return false;
        }
        rx.recv_timeout(Duration::from_secs(2)).unwrap_or(false)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (handle, url);
        false
    }
}

/// Creating a link is explicit: a local recording is opened privately in the chosen Tico.
#[tauri::command(async)]
fn meeting_link(
    state: State<'_, Shared>,
    handle: tauri::AppHandle,
    id: String,
    destination: String,
    confirmed: bool,
    copy: bool,
) -> Result<Value, String> {
    let (store, config) = {
        let app = lock(&state);
        (app.store.clone(), app.config.clone())
    };
    (|| -> anyhow::Result<Value> {
        let record = store.get(&id)?;
        let context = store.context(&id)?;
        anyhow::ensure!(!context.live_discarded || context.live_removed, "This meeting was discarded; its recording stays local");
        anyhow::ensure!(context.live_destination.is_empty() || context.live_destination == destination, "Choose this meeting's Tico");
        let tico = tico_recorder_native::network::Tico::saved(&config, &destination)?;
        let browser = tico.browser_url()?;
        let filed = record.deliveries.iter().find(|d| d.destination_id == destination && !d.meeting_id.is_empty());
        if let Some(delivery) = filed {
            return Ok(serde_json::json!({"url":format!("{browser}/#/meetings?meeting={}", delivery.meeting_id)}));
        }
        anyhow::ensure!(!context.live_id.is_empty() || confirmed, "Confirm uploading this transcript privately to Tico first");
        let live_id = if context.live_id.is_empty() { tico.ensure_live(&store, &id, false)? } else { context.live_id };
        let detail = tico.call(&format!("/api/v2/live-meetings/{live_id}"), None, "")?;
        Ok(serde_json::json!({"url":format!("{browser}/#/meetings/live/{live_id}"),"private":detail["visibility"] != "team", "only_you": detail["visibility"] != "team" && detail["access"].as_array().is_none_or(|a| a.is_empty()) && detail["humans"].as_array().is_none_or(|a| a.len() <= 1)}))
    })().map(|mut value| {
        value["copied"] = serde_json::json!(copy && value["url"].as_str().is_some_and(|url| copy_meeting_url(&handle, url)));
        value
    }).map_err(|e| format!("{e:#}"))
}
#[tauri::command(async)]
fn share_screen(
    state: State<'_, Shared>,
    handle: tauri::AppHandle,
    id: String,
    expected_team: bool,
) -> Result<(), String> {
    let (screen, store, config) = {
        let app = lock(&state);
        (app.screen.clone(), app.store.clone(), app.config.clone())
    };
    (|| -> anyhow::Result<()> {
        let context = store.context(&id)?;
        screen::require_live(&context)?;
        anyhow::ensure!(
            store.get(&id)?.deliveries.is_empty(),
            "This recording was already sent"
        );
        let tico = tico_recorder_native::network::Tico::saved(&config, &context.live_destination)?;
        screen.begin(
            handle,
            tico,
            id.clone(),
            context.live_id,
            store.get(&id)?.title,
            store,
            expected_team,
        )
    })()
    .map_err(|e| format!("{e:#}"))
}

/// Background work, and telling the window when to redraw. Runs whether or not the window is
/// visible. It sleeps until the library changes or a job finishes (both wake the store's change
/// signal), at most a second while idle; while recording it also sends levels ten times a second.
fn ticker(handle: tauri::AppHandle, recorder: &Shared) {
    use tauri::Emitter;
    let ticking = Arc::downgrade(recorder);
    let store = lock_shared(recorder).store.clone();
    std::thread::Builder::new()
        .name("tico-recorder-tick".into())
        .spawn(move || {
            let mut told = 0;
            let mut last_told = std::time::Instant::now();
            loop {
                let started = std::time::Instant::now();
                let mark = store.changes();
                let Some(recorder) = ticking.upgrade() else {
                    break;
                };
                let (stale, version, levels, recording) = {
                    let mut app = lock_shared(&recorder);
                    app.tick();
                    let levels = if app.visible { app.levels() } else { None };
                    (app.stale(), app.ui_version(), levels, app.recording())
                };
                drop(recorder);
                if let Some(levels) = levels {
                    let _ = handle.emit("level", levels);
                }
                // While recording, redraws are spaced out; live text changes many times a second.
                let spacing = if recording {
                    Duration::from_millis(300)
                } else {
                    Duration::ZERO
                };
                if stale && version != told && last_told.elapsed() >= spacing {
                    told = version;
                    last_told = std::time::Instant::now();
                    let _ = handle.emit("changed", version);
                }
                let wait = if recording {
                    Duration::from_millis(100)
                } else {
                    Duration::from_secs(1)
                };
                store.wait_changes(mark, wait);
                // Never more than twenty ticks a second, however often the library changes.
                std::thread::sleep(Duration::from_millis(50).saturating_sub(started.elapsed()));
            }
        })
        .expect("recorder ticker");
}

/// `--import-transcription-key-stdin`: save one credential from standard input in this app's
/// keychain identity and exit, without opening a window, capture or the library.
fn import_key() -> anyhow::Result<()> {
    use std::io::Read;
    anyhow::ensure!(
        std::env::args().count() == 2,
        "Pass the credential through standard input only"
    );
    let mut key = String::new();
    std::io::stdin().take(8193).read_to_string(&mut key)?;
    let key = key.trim();
    anyhow::ensure!(
        (20..=8192).contains(&key.len()) && !key.chars().any(char::is_whitespace),
        "Expected one transcription credential on standard input"
    );
    if !settings::secret("transcription")?.is_empty() {
        println!("Native transcription credential is already configured; existing value retained");
        return Ok(());
    }
    settings::save_secret("transcription", key)?;
    anyhow::ensure!(
        settings::secret("transcription")? == key,
        "The native keychain did not retain the transcription credential"
    );
    println!("Transcription credential saved and verified in the native keychain");
    Ok(())
}

fn main() {
    if std::env::args().nth(1).as_deref() == Some("--import-transcription-key-stdin") {
        if let Err(error) = import_key() {
            eprintln!("Credential import failed: {error:#}");
            std::process::exit(1);
        }
        return;
    }
    let demo = std::env::args().any(|arg| arg == "--demo");
    assert!(
        !demo || std::env::var_os("TICO_RECORDER_DATA_DIR").is_some(),
        "Demo mode requires an isolated TICO_RECORDER_DATA_DIR"
    );
    let root = settings::root();
    let library_lock = AppLock::acquire(&root).expect("Recorder library is in use");
    let store = Store::open(&root).expect("Open recorder library");
    store.recover().expect("Recover saved audio");
    if demo && store.list().unwrap_or_default().is_empty() {
        recorder::seed_demo(&store).expect("Create demo library");
    }
    let recorder: Shared = Arc::new(Mutex::new(
        Recorder::new(store, Some(library_lock), demo).expect("Start the recorder"),
    ));
    // Profiling: record a test tone at launch for this many seconds, then stop.
    if let Some(seconds) = std::env::var("TICO_RECORDER_PROFILE_RECORD_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|_| recorder::synthetic_input())
    {
        lock_shared(&recorder).record(None);
        let stopping = Arc::downgrade(&recorder);
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(seconds));
            if let Some(recorder) = stopping.upgrade() {
                lock_shared(&recorder).stop();
            }
        });
    }
    let updates = update::Updates::default();
    let window_focus = updates.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Focus decides when a downloaded update may install unattended (see update.rs).
        .on_window_event(move |_window, event| window_focus.window_event(event))
        .manage(recorder.clone())
        .manage(updates.clone())
        .invoke_handler(tauri::generate_handler![
            view,
            meeting_link,
            share_screen,
            stop_screen,
            record,
            open_meeting,
            close_meeting,
            stop_recording,
            rename,
            finish_live,
            control_live,
            edit_shared_line,
            remove_shared_line,
            retry,
            set_notes,
            add_item,
            edit_item,
            shared_item,
            set_owner,
            remove_item,
            send_item,
            choose_org,
            change_org,
            load_roster,
            start_adding,
            choose_people_org,
            pick,
            add_participant,
            remove_access,
            set_team,
            ask,
            chat_send,
            set_chat_private,
            send_meeting,
            delete_recording,
            share_live,
            save_meeting,
            keep_local,
            save_settings,
            set_audio,
            sign_in,
            sign_in_desktop,
            sign_out,
            make_default,
            import_legacy,
            confirm_connect,
            dismiss_connect,
            dismiss_error,
            open_url,
            dismiss_notice,
            set_visible,
            update::update_check,
            update::update_install,
        ])
        .build(tauri::generate_context!())
        .expect("Start Tico Recorder");
    ticker(app.handle().clone(), &recorder);
    update::start(app.handle().clone(), recorder.clone(), updates);
    app.run(|handle: &tauri::AppHandle, event: RunEvent| match event {
        // `tico-recorder://connect?server=…` from Tico's Connect recorder button, and `tico-recorder://open?server=…`
        // from Record in the Tico desktop app, also on a cold start.
        #[cfg(any(target_os = "macos", target_os = "ios"))]
        RunEvent::Opened { urls } => {
            let state = handle.state::<Shared>();
            for url in urls {
                lock(&state).link(url.as_str());
            }
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        // Stop flushes the last audio buffer before the process ends.
        RunEvent::Exit => {
            let recorder = handle.state::<Shared>();
            lock(&recorder).shutdown();
            // A downloaded update installs now, so the next launch is the new build. No restart.
            update::on_quit(handle, &recorder, &handle.state::<update::Updates>());
        }
        _ => {}
    });
}
