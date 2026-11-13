//! A durable, non-secret device identity survives re-enrollment. Only the app-salted hash leaves the computer.

use std::sync::OnceLock;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const SALT: &[u8] = b"tico:device-id:v1\0";

fn hash(raw: &str) -> Option<String> {
    let id = uuid::Uuid::parse_str(raw.trim()).ok()?;
    // Uninitialized or placeholder machine ids cannot prove two enrollments share a device.
    if id.is_nil() || id.as_bytes().iter().all(|b| *b == 0xff) {
        return None;
    }
    let mut digest = Sha256::new();
    digest.update(SALT);
    digest.update(id.as_bytes());
    Some(hex::encode(digest.finalize()))
}

#[cfg(any(target_os = "macos", test))]
fn from_ioreg(output: &str) -> Option<String> {
    output.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        if key.trim() != "\"IOPlatformUUID\"" {
            return None;
        }
        hash(value.trim().strip_prefix('"')?.strip_suffix('"')?)
    })
}

fn detect() -> Option<String> {
    #[cfg(target_os = "linux")]
    {
        hash(&std::fs::read_to_string("/etc/machine-id").ok()?)
    }
    #[cfg(target_os = "macos")]
    {
        let output = std::process::Command::new("/usr/sbin/ioreg")
            .args(["-rd1", "-c", "IOPlatformExpertDevice"])
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        from_ioreg(&String::from_utf8(output.stdout).ok()?)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        None
    }
}

pub fn get() -> &'static Option<String> {
    static ID: OnceLock<Option<String>> = OnceLock::new();
    // A container's machine id comes from its image, shared by every container made from it: it proves nothing.
    ID.get_or_init(|| {
        if crate::follow::kind() == "docker" {
            None
        } else {
            detect()
        }
    })
}

pub fn report(body: &mut Value) {
    if let Some(id) = get() {
        body["device_id"] = json!(id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn device_id_is_stable_salted_and_distinguishes_devices() {
        let raw = "0123456789abcdef0123456789abcdef";
        let first = hash(raw).unwrap();
        assert_eq!(
            hash(&format!("{}\n", raw.to_uppercase())),
            Some(first.clone())
        );
        assert_eq!(
            hash("01234567-89AB-CDEF-0123-456789ABCDEF"),
            Some(first.clone())
        );
        assert_eq!(first.len(), 64);
        assert!(!first.contains(raw));
        assert_ne!(first, hex::encode(Sha256::digest(raw.as_bytes())));
        assert_ne!(Some(first), hash("1123456789abcdef0123456789abcdef"));
    }

    #[test]
    fn device_id_is_unknown_for_missing_uninitialized_or_placeholder_ids() {
        for raw in [
            "",
            "uninitialized",
            "00000000000000000000000000000000",
            "ffffffffffffffffffffffffffffffff",
        ] {
            assert_eq!(hash(raw), None);
        }
    }

    #[test]
    fn device_id_uses_only_the_platform_uuid_from_ioreg() {
        let output = r#"+-o IOPlatformExpertDevice <class IOPlatformExpertDevice>
    {
      "IOPlatformSerialNumber" = "not-the-device-id"
      "IOPlatformUUID" = "01234567-89AB-CDEF-0123-456789ABCDEF"
    }"#;
        assert_eq!(from_ioreg(output), hash("0123456789abcdef0123456789abcdef"));
        assert_eq!(
            from_ioreg(r#""IOPlatformSerialNumber" = "0123456789abcdef0123456789abcdef""#),
            None
        );
        assert_eq!(from_ioreg(r#""IOPlatformUUID" = """#), None);
    }
}
