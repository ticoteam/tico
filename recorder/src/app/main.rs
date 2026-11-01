//! Tico Recorder's window: Tauri 2 on the system WebView. The UI in `ui/app` is plain
//! HTML/CSS/JS; it polls `view` and calls the commands below, each a thin wrapper over
//! `Recorder`. Recording, transcription, live sync and drafts run on their own threads.
mod recorder;
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

commands! {
    record(event: Option<Value>) => |app| app.record(event);
    open_meeting(id: String) => |app| { let opened = app.open(&id); app.report(opened) };
    close_meeting() => |app| app.close();
    stop_recording() => |app| app.stop();
    rename(title: String) => |app| app.rename(&title);
    finish_live() => |app| app.finish_live();
    edit_shared_line(id: String, seq: i64, text: String) => |app| app.edit_shared_line(&id, seq, Some(&text));
    remove_shared_line(id: String, seq: i64) => |app| app.edit_shared_line(&id, seq, None);
    retry(id: String) => |app| app.retry(&id);
    set_notes(notes: String) => |app| app.set_notes(&notes);
    add_item(title: String) => |app| app.add_item(&title);
    edit_item(id: String, title: String) => |app| app.edit_item(&id, &title);
    set_owner(id: String, owner: String) => |app| app.set_owner(&id, &owner);
    remove_item(id: String) => |app| app.remove_item(&id);
    send_item(id: String, owner: String) => |app| app.send_item(&id, &owner);
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
    send_meeting(id: String, destination: String) => |app| app.send_meeting(&id, &destination);
    delete_recording(id: String) => |app| app.delete(&id);
    save_settings(form: SettingsForm) => |app| app.save_settings(form);
    set_audio(microphone: String, meeting: bool) => |app| app.set_audio(&microphone, meeting);
    sign_in(address: String) => |app| app.sign_in(&address);
    sign_out(id: String) => |app| app.sign_out(&id);
    make_default(id: String) => |app| app.make_default(&id);
    import_legacy(directory: String) => |app| app.import_legacy(&directory);
    confirm_connect() => |app| app.confirm_connect();
    dismiss_connect() => |app| app.dismiss_connect();
    dismiss_error() => |app| app.dismiss_error();
    open_url(url: String) => |app| app.open_url(&url);
    dismiss_notice() => |app| app.dismiss_notice();
    set_visible(visible: bool) => |app| app.visible = visible;
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
            record,
            open_meeting,
            close_meeting,
            stop_recording,
            rename,
            finish_live,
            edit_shared_line,
            remove_shared_line,
            retry,
            set_notes,
            add_item,
            edit_item,
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
            save_settings,
            set_audio,
            sign_in,
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
    app.run(|handle, event| match event {
        // `tico-recorder://connect?server=…` from Tico's Connect recorder button, also on a cold start.
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
