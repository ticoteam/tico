#[cfg(feature = "desktop")]
mod build_window;

fn main() {
    // The release writes its version into tauri.conf.json before building (recorder.yml); the Recorder tells Tico
    // that version on its calls, the CLI included, so it is read here rather than from Cargo.toml.
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=build.rs");
    let config = std::fs::read_to_string("tauri.conf.json").unwrap_or_default();
    let version = config
        .split_once("\"version\"")
        .and_then(|(_, rest)| rest.split('"').nth(1))
        .filter(|value| !value.is_empty() && value.len() <= 40)
        .unwrap_or(env!("CARGO_PKG_VERSION"))
        .to_owned();
    println!("cargo:rustc-env=TICO_RECORDER_VERSION={version}");
    println!("cargo:rerun-if-changed=src/capture/voice_processing.m");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .file("src/capture/voice_processing.m")
            .flag("-fobjc-arc")
            .flag("-fblocks")
            .flag("-mmacosx-version-min=14.2")
            .compile("tico_voice_processing");
        println!("cargo:rustc-link-lib=framework=AVFAudio");
        println!("cargo:rustc-link-lib=framework=AudioToolbox");
    }

    // Only the desktop app embeds the UI and needs Tauri's generated context.
    #[cfg(feature = "desktop")]
    {
        check_window_build();
        tauri_build::build();
    }
}

/// The window's TypeScript (ui/app) runs as the JavaScript recorder/build-ui.cjs writes into dist/app, the folder
/// tauri.conf.json embeds. Compiling it needs Node and esbuild, which a build script should not, so `cargo tauri
/// build` and `cargo tauri dev` run it first (beforeBuildCommand, and beforeDevCommand with --watch) and this only
/// checks dist/app against the manifest that build wrote (build_window.rs): a plain `cargo build` then fails here,
/// saying what to run, rather than embedding a stale window or failing inside Tauri's macro. While a watcher runs
/// (cargo tauri dev), a change it has not finished building is waited for, up to ten seconds.
#[cfg(feature = "desktop")]
fn check_window_build() {
    use std::path::Path;

    for path in [
        "ui/app",
        "dist/app",
        build_window::MANIFEST,
        "build-ui.cjs",
        "../package.json",
        "../package-lock.json",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
    let root = Path::new(".");
    let mut problems = build_window::problems(root);
    let mut waited = 0;
    while !problems.is_empty() && waited < 100 && build_window::watcher(root).is_some() {
        std::thread::sleep(std::time::Duration::from_millis(100));
        waited += 1;
        problems = build_window::problems(root);
    }
    if problems.is_empty() {
        return;
    }
    let shown = problems.len().min(12);
    panic!(
        "the Recorder window is TypeScript compiled into recorder/dist/app, and that output is not current.\n\
         Run `node recorder/build-ui.cjs` from the repository root (after `npm ci` there, for esbuild);\n\
         `cargo tauri build` and `cargo tauri dev` run it for you.\n{}{}",
        problems[..shown].join("\n"),
        if problems.len() > shown {
            format!("\n(and {} more)", problems.len() - shown)
        } else {
            String::new()
        }
    );
}
