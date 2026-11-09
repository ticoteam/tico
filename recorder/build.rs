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
/// build` and `cargo tauri dev` run it first (beforeBuildCommand, beforeDevCommand) and this only checks that every
/// file of ui/app has an output no older than itself: a plain `cargo build` then fails here, saying what to run,
/// rather than embedding a stale window or failing inside Tauri's macro.
#[cfg(feature = "desktop")]
fn check_window_build() {
    use std::path::{Path, PathBuf};

    fn files(dir: &Path, found: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                files(&path, found);
            } else {
                found.push(path);
            }
        }
    }
    let modified = |path: &Path| std::fs::metadata(path).and_then(|m| m.modified()).ok();

    println!("cargo:rerun-if-changed=ui/app");
    println!("cargo:rerun-if-changed=dist/app");
    let source = Path::new("ui/app");
    let mut sources = Vec::new();
    files(source, &mut sources);
    sources.sort();
    let mut missing = Vec::new();
    let mut stale = Vec::new();
    for file in &sources {
        let name = file
            .strip_prefix(source)
            .unwrap_or(file)
            .to_string_lossy()
            .into_owned();
        if name.ends_with(".d.ts") {
            continue;
        }
        let output = Path::new("dist/app").join(match name.strip_suffix(".ts") {
            Some(stem) => format!("{stem}.js"),
            None => name.clone(),
        });
        match (modified(file), modified(&output)) {
            (_, None) => missing.push(output.display().to_string()),
            (Some(source_time), Some(output_time)) if output_time < source_time => {
                stale.push(output.display().to_string())
            }
            _ => {}
        }
    }
    if missing.is_empty() && stale.is_empty() {
        return;
    }
    let mut message = String::from(
        "the Recorder window is TypeScript compiled into recorder/dist/app, and that output is not current.\n\
         Run `node recorder/build-ui.cjs` from the repository root (after `npm ci` there, for esbuild);\n\
         `cargo tauri build` and `cargo tauri dev` run it for you.",
    );
    if !missing.is_empty() {
        message += &format!("\nMissing: {}", missing.join(", "));
    }
    if !stale.is_empty() {
        message += &format!("\nOlder than its source: {}", stale.join(", "));
    }
    panic!("{message}");
}
