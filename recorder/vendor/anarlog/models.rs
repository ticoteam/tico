//! Build script shared by the vendored crates: fetches the ONNX model each one embeds with
//! `include_bytes!`, pinned to the anarlog commit the code came from and checked by SHA-256.
use std::{env, path::Path, process::Command};

const COMMIT: &str = "98d6219da3ad1f06c8175fb0f5e4e1867a6d6286";

fn main() {
    let (path, sha) = match env::var("CARGO_PKG_NAME").unwrap().as_str() {
        "anlg-embedding" => (
            "crates/embedding/src/onnx/embedding.onnx",
            "841645a9b5109369858ca89695e8b23ecfd4486b30bdd5cb3f99fef8d1c29205",
        ),
        "pyannote-local" => (
            "crates/pyannote-local/src/data/segmentation.onnx",
            "057ee564753071c0b09b5b611648b50ac188d50846bff5f01e9f7bbf1591ea25",
        ),
        other => panic!("no model for {other}"),
    };
    let local = Path::new(path.split_once("/src/").map(|(_, rest)| rest).unwrap());
    let local = Path::new("src").join(local);
    println!("cargo:rerun-if-changed={}", local.display());
    if digest(&local).as_deref() == Some(sha) {
        return;
    }
    let url = format!("https://raw.githubusercontent.com/fastrepl/anarlog/{COMMIT}/{path}");
    let partial = local.with_extension("download");
    // A fresh checkout has no model folder: the models are gitignored.
    std::fs::create_dir_all(local.parent().unwrap()).unwrap();
    let status = Command::new("curl")
        .args(["-fsSL", "--retry", "3", "-o"])
        .arg(&partial)
        .arg(&url)
        .status()
        .expect("curl is needed to fetch the diarization models");
    assert!(status.success(), "could not download {url}");
    assert_eq!(digest(&partial).as_deref(), Some(sha), "{url} has an unexpected checksum");
    std::fs::rename(partial, local).unwrap();
}

fn digest(path: &Path) -> Option<String> {
    if !path.is_file() {
        return None;
    }
    let output = Command::new("shasum").args(["-a", "256"]).arg(path).output().ok()?;
    String::from_utf8(output.stdout).ok()?.split_whitespace().next().map(str::to_owned)
}
