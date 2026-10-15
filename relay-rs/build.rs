use sha2::{Digest, Sha256};
use std::{fs, path::Path};

fn main() {
    let mut paths = vec!["Cargo.toml".to_owned(), "Cargo.lock".to_owned(), "build.rs".to_owned()];
    paths.extend(fs::read_dir("src").unwrap().map(|e| e.unwrap().path().to_string_lossy().into_owned()));
    paths.sort();
    let mut hash = Sha256::new();
    for path in paths {
        if !Path::new(&path).is_file() { continue; }
        println!("cargo:rerun-if-changed={path}");
        let data = fs::read(&path).unwrap();
        hash.update(format!("{path}\0{}\0", data.len()).as_bytes());
        hash.update(data);
    }
    println!("cargo:rustc-env=TICO_SCREEN_BUILD_ID={}", &format!("{:x}", hash.finalize())[..16]);
}
