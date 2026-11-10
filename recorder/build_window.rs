//! The check build.rs runs on the window's build (recorder/build-ui.cjs) before Tauri embeds dist/app. Standard
//! library only: build.rs includes this file as a module, and src/lib.rs includes it for its tests.
//!
//! dist/build-manifest.txt lists each source with its output and the FNV-1a 64 hash of both, and the hash of each
//! recipe input (build-ui.cjs, package.json, package-lock.json). The window is current when the manifest matches the
//! files by content: every ui/app source built from its present bytes, no source removed, each output as written,
//! nothing else in dist/app, and the recipe unchanged. Times are not read, so equal or future mtimes do not matter.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

/// What build-ui.cjs hashes as its recipe, relative to recorder/ (the same list as its INPUTS).
pub const INPUTS: [&str; 3] = ["build-ui.cjs", "../package.json", "../package-lock.json"];
pub const MANIFEST: &str = "dist/build-manifest.txt";

/// FNV-1a 64 as 16 hex digits, as build-ui.cjs writes it.
pub fn fnv(bytes: &[u8]) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash = (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{hash:016x}")
}

fn hash_of(path: &Path) -> String {
    fs::read(path).map_or_else(|_| "missing".to_owned(), |bytes| fnv(&bytes))
}

/// Every file under dir, relative to `root` with / separators.
fn files(root: &Path, dir: &str, found: &mut Vec<String>) {
    let Ok(entries) = fs::read_dir(root.join(dir)) else {
        return;
    };
    for entry in entries.flatten() {
        let name = format!("{dir}/{}", entry.file_name().to_string_lossy());
        if entry.path().is_dir() {
            files(root, &name, found);
        } else {
            found.push(name);
        }
    }
}

/// The files build-ui.cjs builds from ui/app: not declarations, not Node scripts.
fn is_built(name: &str) -> bool {
    // .d.ts, .d.mts and .d.cts are declarations; .mts and .cts are Node scripts.
    !name.ends_with(".d.ts") && !name.ends_with(".mts") && !name.ends_with(".cts")
}

/// Why dist/app does not match ui/app and the recipe, one line each; empty when it does. `root` is recorder/.
pub fn problems(root: &Path) -> Vec<String> {
    let Ok(manifest) = fs::read_to_string(root.join(MANIFEST)) else {
        return vec![format!(
            "{MANIFEST} is missing: the window was never built, or its last build failed"
        )];
    };
    let mut problems = Vec::new();
    let mut inputs = BTreeMap::new();
    // source -> (its hash, output, the output's hash)
    let mut built = BTreeMap::new();
    for line in manifest.lines() {
        let fields: Vec<&str> = line.split('\t').collect();
        match fields.as_slice() {
            ["input", name, hash] => {
                inputs.insert(*name, *hash);
            }
            ["file", source, hash, output, output_hash] if source.starts_with("ui/app/") => {
                built.insert(*source, (*hash, *output, *output_hash));
            }
            _ => {}
        }
    }
    for input in INPUTS {
        if inputs.get(input).copied() != Some(hash_of(&root.join(input)).as_str()) {
            problems.push(format!("{input} changed since the build"));
        }
    }
    let mut sources = Vec::new();
    files(root, "ui/app", &mut sources);
    sources.retain(|name| is_built(name));
    sources.sort();
    for source in &sources {
        match built.get(source.as_str()) {
            None => problems.push(format!("{source} was never built")),
            Some((hash, _, _)) if *hash != hash_of(&root.join(source)) => {
                problems.push(format!("{source} changed since the build"));
            }
            _ => {}
        }
    }
    let mut outputs = BTreeSet::new();
    for (source, (_, output, output_hash)) in &built {
        if !sources.iter().any(|name| name == source) {
            problems.push(format!("{source} is gone but its output is still listed"));
        }
        if *output_hash != hash_of(&root.join(output)) {
            problems.push(format!("{output} is missing or not what the build wrote"));
        }
        outputs.insert(output.to_string());
    }
    let mut present = Vec::new();
    files(root, "dist/app", &mut present);
    present.sort();
    for file in present {
        if !outputs.contains(&file) {
            problems.push(format!("{file} is not an output of the build"));
        }
    }
    problems
}

/// The process id of a running `build-ui.cjs --watch` (dist/.watching), when there is one.
pub fn watcher(root: &Path) -> Option<u32> {
    let pid: u32 = fs::read_to_string(root.join("dist/.watching"))
        .ok()?
        .trim()
        .parse()
        .ok()?;
    let alive = std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    alive.then_some(pid)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "tico-recorder-build-window-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = fs::remove_dir_all(&root);
        let recorder = root.join("recorder");
        for (path, text) in [
            ("package.json", "{}"),
            ("package-lock.json", "{}"),
            ("recorder/build-ui.cjs", "// recipe"),
            ("recorder/ui/app/app.ts", "let a: number = 1;"),
            ("recorder/ui/app/app.css", "body {}"),
            ("recorder/ui/app/bridge.d.ts", "declare const x: number;"),
            ("recorder/dist/app/app.js", "let a = 1;"),
            ("recorder/dist/app/app.css", "body {}"),
        ] {
            let path = root.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
        let mut manifest = String::from("# test\nesbuild\t0\n");
        for input in INPUTS {
            manifest += &format!("input\t{input}\t{}\n", hash_of(&recorder.join(input)));
        }
        for (source, output) in [
            ("ui/app/app.css", "dist/app/app.css"),
            ("ui/app/app.ts", "dist/app/app.js"),
        ] {
            let (hash, output_hash) = (
                hash_of(&recorder.join(source)),
                hash_of(&recorder.join(output)),
            );
            manifest += &format!("file\t{source}\t{hash}\t{output}\t{output_hash}\n");
        }
        fs::write(recorder.join(MANIFEST), manifest).unwrap();
        recorder
    }

    #[test]
    fn fnv_matches_the_published_vectors_build_ui_uses_too() {
        assert_eq!(fnv(b""), "cbf29ce484222325");
        assert_eq!(fnv(b"a"), "af63dc4c8601ec8c");
        assert_eq!(fnv(b"foobar"), "85944171f73967e8");
    }

    #[test]
    fn a_current_build_passes_and_each_kind_of_drift_is_named() {
        let root = tree();
        assert_eq!(problems(&root), Vec::<String>::new());

        // Same length, any mtime: only the bytes count.
        fs::write(root.join("ui/app/app.ts"), "let a: number = 2;").unwrap();
        assert_eq!(problems(&root), ["ui/app/app.ts changed since the build"]);
        fs::write(root.join("ui/app/app.ts"), "let a: number = 1;").unwrap();

        fs::write(root.join("dist/app/app.js"), "damaged").unwrap();
        assert_eq!(
            problems(&root),
            ["dist/app/app.js is missing or not what the build wrote"]
        );
        fs::write(root.join("dist/app/app.js"), "let a = 1;").unwrap();

        fs::write(root.join("ui/app/new.ts"), "").unwrap();
        fs::write(root.join("dist/app/stray.js"), "").unwrap();
        assert_eq!(
            problems(&root),
            [
                "ui/app/new.ts was never built",
                "dist/app/stray.js is not an output of the build"
            ]
        );
        fs::remove_file(root.join("ui/app/new.ts")).unwrap();
        fs::remove_file(root.join("dist/app/stray.js")).unwrap();

        fs::remove_file(root.join("ui/app/app.css")).unwrap();
        assert_eq!(
            problems(&root),
            ["ui/app/app.css is gone but its output is still listed"]
        );
        fs::write(root.join("ui/app/app.css"), "body {}").unwrap();

        fs::write(root.join("../package-lock.json"), "{\"esbuild\": 2}").unwrap();
        fs::write(root.join("build-ui.cjs"), "// new recipe").unwrap();
        assert_eq!(
            problems(&root),
            [
                "build-ui.cjs changed since the build",
                "../package-lock.json changed since the build"
            ]
        );

        fs::remove_file(root.join(MANIFEST)).unwrap();
        assert!(problems(&root)[0].starts_with("dist/build-manifest.txt is missing"));
        assert_eq!(watcher(&root), None);
        let _ = fs::remove_dir_all(root.parent().unwrap());
    }
}
